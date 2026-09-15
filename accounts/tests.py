"""Tests for authentication: session links, brute-force protection, account fields.

These tests were generated with an LLM and then reviewed by hand. Treat a
failure as a real signal, but read the assertion before trusting it: a test
here can encode an assumption the code never promised. Fix or delete such a
test rather than bending the code to satisfy it.
"""

import hashlib
import re

from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from .models import SessionLink

# The clear-text token only ever exists in the page that generated it, so the
# UI tests read it back out of the rendered link rather than from the database.
_PRIVATE_URL_RE = re.compile(r'/private/([^/"\s]+)/')


def private_link_token(response):
    """The one-time session-link token rendered on *response*, or ``None``."""
    match = _PRIVATE_URL_RE.search(response.content.decode())
    return match.group(1) if match else None

# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #

class SessionLinkModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw')

    def test_generate_creates_token(self):
        link, token = SessionLink.generate(self.user)
        self.assertEqual(link.user, self.user)
        self.assertTrue(token)
        self.assertIsNone(link.last_used_at)

    def test_token_is_stored_only_as_a_hash(self):
        # The point of the whole scheme: a dump of the table hands an attacker
        # nothing they can sign in with.
        link, token = SessionLink.generate(self.user)
        self.assertEqual(link.token_hash, hashlib.sha256(token.encode('utf-8')).hexdigest())
        row = SessionLink.objects.filter(pk=link.pk).values().get()
        self.assertNotIn(token, [str(value) for value in row.values()])

    def test_generate_replaces_existing_link(self):
        _, old_token = SessionLink.generate(self.user)
        second, new_token = SessionLink.generate(self.user)
        self.assertEqual(SessionLink.objects.filter(user=self.user).count(), 1)
        self.assertNotEqual(new_token, old_token)
        self.assertIsNone(SessionLink.authenticate(old_token))
        self.assertEqual(SessionLink.authenticate(new_token), second)

    def test_authenticate_valid_invalid_empty(self):
        _, token = SessionLink.generate(self.user)
        self.assertEqual(SessionLink.authenticate(token).user, self.user)
        self.assertIsNone(SessionLink.authenticate(token + 'tampered'))
        self.assertIsNone(SessionLink.authenticate(''))
        self.assertIsNone(SessionLink.authenticate(None))

    def test_authenticate_rejects_the_stored_hash_itself(self):
        # Presenting the digest read out of the database must not work, or
        # hashing would have bought nothing.
        link, _ = SessionLink.generate(self.user)
        self.assertIsNone(SessionLink.authenticate(link.token_hash))

    def test_authenticate_inactive_user_rejected(self):
        _, token = SessionLink.generate(self.user)
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertIsNone(SessionLink.authenticate(token))

    def test_touch_last_used(self):
        link, _ = SessionLink.generate(self.user)
        self.assertIsNone(link.last_used_at)
        link.touch_last_used()
        link.refresh_from_db()
        self.assertIsNotNone(link.last_used_at)


# --------------------------------------------------------------------------- #
# Access-log redaction of Session-Link tokens (config/gunicorn.py)
# --------------------------------------------------------------------------- #

class AccessLogRedactionTests(TestCase):
    """The /private/<token>/ path is password-equivalent; the gunicorn access
    logger must never write the token to stdout."""

    def test_redact_scrubs_token_everywhere_a_path_appears(self):
        from config.gunicorn import redact
        self.assertEqual(
            redact('GET /private/Zx9_secret-token/?q=cats HTTP/1.1'),
            'GET /private/[redacted]/?q=cats HTTP/1.1',
        )
        self.assertEqual(redact('/private/Zx9_secret-token/'), '/private/[redacted]/')
        self.assertEqual(
            redact('https://seurch.example/private/Zx9_secret-token/'),
            'https://seurch.example/private/[redacted]/',
        )

    def test_redact_leaves_ordinary_values_alone(self):
        from config.gunicorn import redact
        self.assertEqual(redact('GET /search/?q=privacy HTTP/1.1'), 'GET /search/?q=privacy HTTP/1.1')
        self.assertEqual(redact(200), 200)
        self.assertEqual(redact(None), None)

    def test_logger_atoms_are_redacted(self):
        import datetime
        from types import SimpleNamespace

        from config.gunicorn import RedactingLogger

        # Skip Logger.__init__ (it wants a full gunicorn config and opens log
        # files); atoms() itself needs no logger state beyond the class.
        logger = RedactingLogger.__new__(RedactingLogger)
        atoms = logger.atoms(
            resp=SimpleNamespace(status='302 Found', sent=0, headers=[]),
            req=SimpleNamespace(headers=[]),
            environ={
                'REQUEST_METHOD': 'GET',
                'RAW_URI': '/private/Zx9_secret-token/?q=cats',
                'PATH_INFO': '/private/Zx9_secret-token/',
                'QUERY_STRING': 'q=cats',
                'SERVER_PROTOCOL': 'HTTP/1.1',
                'HTTP_REFERER': 'https://seurch.example/private/Zx9_secret-token/',
            },
            request_time=datetime.timedelta(seconds=0),
        )
        flattened = ' '.join(str(v) for v in atoms.values())
        self.assertNotIn('Zx9_secret-token', flattened)
        self.assertIn('/private/[redacted]/', atoms['r'])
        self.assertIn('/private/[redacted]/', atoms['U'])
        self.assertIn('/private/[redacted]/', atoms['f'])


# --------------------------------------------------------------------------- #
# Brute-force protection (login lockout, password-reset throttling)
# --------------------------------------------------------------------------- #

class LoginLockoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('kate', password='right-horse-battery')
        cache.clear()  # rate-limit counters must not leak between tests

    def _fail(self, n=1, username='kate'):
        for _ in range(n):
            self.client.post(reverse('login'), {'username': username, 'password': 'wrong'})

    def test_lockout_after_repeated_failures(self):
        self._fail(settings.LOGIN_THROTTLE_LIMIT)
        # Even the correct password is refused once locked, and the error
        # doesn't reveal that it was correct.
        resp = self.client.post(reverse('login'), {'username': 'kate', 'password': 'right-horse-battery'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Too many failed sign-in attempts')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_under_the_limit_can_still_sign_in(self):
        self._fail(settings.LOGIN_THROTTLE_LIMIT - 1)
        resp = self.client.post(reverse('login'), {'username': 'kate', 'password': 'right-horse-battery'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)

    def test_successful_login_forgives_earlier_failures(self):
        self._fail(settings.LOGIN_THROTTLE_LIMIT - 1)
        self.client.post(reverse('login'), {'username': 'kate', 'password': 'right-horse-battery'})
        self.client.get(reverse('logout'))
        self.client.post(reverse('logout'))
        # The counter restarted: one more failure is far from the limit again.
        self._fail(1)
        resp = self.client.post(reverse('login'), {'username': 'kate', 'password': 'right-horse-battery'})
        self.assertEqual(resp.status_code, 302)

    def test_lockout_message_is_translated(self):
        # Regression: the lockout error is a gettext string, so it must
        # actually be present in the catalogs, not just wrapped in _().
        for _ in range(settings.LOGIN_THROTTLE_LIMIT):
            self.client.post(reverse('login'), {'username': 'kate', 'password': 'wrong'},
                              HTTP_ACCEPT_LANGUAGE='fr-FR,fr;q=0.9')
        resp = self.client.post(reverse('login'), {'username': 'kate', 'password': 'wrong'},
                                 HTTP_ACCEPT_LANGUAGE='fr-FR,fr;q=0.9')
        self.assertContains(resp, 'Trop de tentatives de connexion échouées')

    def test_lockout_is_per_username(self):
        other = User.objects.create_user('leo', password='right-horse-battery')
        self._fail(settings.LOGIN_THROTTLE_LIMIT)
        # A different account signs in fine (the IP budget is much larger).
        resp = self.client.post(reverse('login'), {'username': 'leo', 'password': 'right-horse-battery'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), other.pk)


class PasswordResetThrottleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('mia', password='pw', email='mia@example.com')
        cache.clear()

    def test_reset_emails_capped_per_ip(self):
        limit = settings.PASSWORD_RESET_THROTTLE_LIMIT
        for _ in range(limit + 3):
            resp = self.client.post(reverse('password_reset'), {'email': 'mia@example.com'})
            # Over-budget requests are indistinguishable from sent ones.
            self.assertRedirects(resp, '/password-reset/done/')
        self.assertEqual(len(mail.outbox), limit)


# --------------------------------------------------------------------------- #
# /private/<token>/ view
# --------------------------------------------------------------------------- #

class PrivateSessionLoginViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('bob', password='pw')

    def test_valid_token_logs_in_and_redirects(self):
        _, token = SessionLink.generate(self.user)
        resp = self.client.get(reverse('private_session_login', kwargs={'token': token}))
        self.assertRedirects(resp, reverse('search:index'))
        # The session now belongs to the linked user.
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)

    def test_valid_token_touches_last_used(self):
        link, token = SessionLink.generate(self.user)
        self.client.get(reverse('private_session_login', kwargs={'token': token}))
        link.refresh_from_db()
        self.assertIsNotNone(link.last_used_at)

    def test_invalid_token_redirects_to_login(self):
        resp = self.client.get(reverse('private_session_login', kwargs={'token': 'does-not-exist'}))
        self.assertRedirects(resp, reverse('login'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_regenerated_old_token_no_longer_works(self):
        _, old_token = SessionLink.generate(self.user)
        SessionLink.generate(self.user)
        resp = self.client.get(reverse('private_session_login', kwargs={'token': old_token}))
        self.assertRedirects(resp, reverse('login'))

    def test_inactive_user_token_rejected(self):
        _, token = SessionLink.generate(self.user)
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        resp = self.client.get(reverse('private_session_login', kwargs={'token': token}))
        self.assertRedirects(resp, reverse('login'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_query_param_logs_in_and_forwards_to_results(self):
        _, token = SessionLink.generate(self.user)
        url = reverse('private_session_login', kwargs={'token': token})
        resp = self.client.get(url, {'q': 'foo'})
        self.assertRedirects(resp, reverse('search:results') + '?q=foo')
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)

    def test_blank_query_param_redirects_to_index(self):
        _, token = SessionLink.generate(self.user)
        url = reverse('private_session_login', kwargs={'token': token})
        resp = self.client.get(url, {'q': '   '})
        self.assertRedirects(resp, reverse('search:index'))


# --------------------------------------------------------------------------- #
# Settings → Account UI
# --------------------------------------------------------------------------- #

class SettingsSessionLinkUITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('erin', password='pw')
        self.client.login(username='erin', password='pw')

    def test_empty_state(self):
        resp = self.client.get(reverse('search:settings') + '?pane=account')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Generate link')

    def _generate(self):
        """Click "Generate link"; return the settings page that follows."""
        return self.client.post(reverse('search:settings'), {
            'setting': 'generate_session_link', 'pane': 'account',
        }, follow=True)

    def test_generate_shows_link_once(self):
        resp = self._generate()
        self.assertEqual(resp.status_code, 200)
        token = private_link_token(resp)
        self.assertIsNotNone(token)
        # The link on the page is the real one, and it is the only place it
        # ever appears: the row behind it holds a hash.
        self.assertEqual(SessionLink.authenticate(token).user, self.user)
        url = reverse('private_session_login', kwargs={'token': token})
        self.assertContains(resp, url + '?q=%s')
        self.assertContains(resp, 'will not be shown again')
        self.assertContains(resp, 'Generate new link')
        self.assertContains(resp, 'Turn off')

    def test_link_is_not_shown_again_on_a_later_visit(self):
        self._generate()
        resp = self.client.get(reverse('search:settings') + '?pane=account')
        self.assertIsNone(private_link_token(resp))
        # The link is still active, the page just can't show it.
        self.assertTrue(SessionLink.objects.filter(user=self.user).exists())
        self.assertContains(resp, 'A private session link is active.')
        self.assertContains(resp, 'cannot be shown again')

    def test_regenerate_invalidates_old_token(self):
        old_token = private_link_token(self._generate())
        new_token = private_link_token(self._generate())

        self.assertNotEqual(old_token, new_token)
        self.assertEqual(SessionLink.objects.filter(user=self.user).count(), 1)
        self.assertIsNone(SessionLink.authenticate(old_token))
        self.assertEqual(SessionLink.authenticate(new_token).user, self.user)

    def test_turn_off_removes_link(self):
        self.client.post(reverse('search:settings'), {
            'setting': 'generate_session_link', 'pane': 'account',
        })
        self.assertTrue(SessionLink.objects.filter(user=self.user).exists())

        resp = self.client.post(reverse('search:settings'), {
            'setting': 'delete_session_link', 'pane': 'account',
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(SessionLink.objects.filter(user=self.user).exists())
        self.assertContains(resp, 'Generate link')

    def test_turning_off_does_not_affect_another_users_link(self):
        other = User.objects.create_user('mallory', password='pw')
        other_link, _ = SessionLink.generate(other)

        self.client.post(reverse('search:settings'), {
            'setting': 'delete_session_link', 'pane': 'account',
        })

        self.assertTrue(SessionLink.objects.filter(pk=other_link.pk).exists())


# --------------------------------------------------------------------------- #
# Terminating sessions opened via the link, on regenerate/turn off
# --------------------------------------------------------------------------- #

class SessionLinkTerminationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('carol', password='pw')

    def _link_session_client(self):
        """A fresh Client signed in only via a (now-current) session link."""
        _, token = SessionLink.generate(self.user)
        client = Client()
        client.get(reverse('private_session_login', kwargs={'token': token}))
        self.assertEqual(int(client.session['_auth_user_id']), self.user.pk)
        return client

    def test_regenerate_terminates_link_sessions(self):
        link_client = self._link_session_client()

        self.client.login(username='carol', password='pw')
        self.client.post(reverse('search:settings'), {
            'setting': 'generate_session_link', 'pane': 'account',
        })

        resp = link_client.get(reverse('search:settings'))
        self.assertRedirects(resp, f"/login/?next={reverse('search:settings')}")

    def test_turn_off_terminates_link_sessions(self):
        link_client = self._link_session_client()

        self.client.login(username='carol', password='pw')
        self.client.post(reverse('search:settings'), {
            'setting': 'delete_session_link', 'pane': 'account',
        })

        resp = link_client.get(reverse('search:settings'))
        self.assertRedirects(resp, f"/login/?next={reverse('search:settings')}")

    def test_password_login_session_not_terminated(self):
        # carol has both a private link and an ordinary password session;
        # regenerating the link must not log out the password session.
        SessionLink.generate(self.user)
        self.client.login(username='carol', password='pw')

        self.client.post(reverse('search:settings'), {
            'setting': 'generate_session_link', 'pane': 'account',
        })

        resp = self.client.get(reverse('search:settings'))
        self.assertEqual(resp.status_code, 200)

    def test_other_users_link_sessions_unaffected(self):
        other = User.objects.create_user('dave', password='pw')
        other_link_client = Client()
        _, other_token = SessionLink.generate(other)
        other_link_client.get(reverse('private_session_login', kwargs={'token': other_token}))

        self.client.login(username='carol', password='pw')
        self.client.post(reverse('search:settings'), {
            'setting': 'generate_session_link', 'pane': 'account',
        })

        resp = other_link_client.get(reverse('search:settings'))
        self.assertEqual(resp.status_code, 200)

    def test_acting_via_link_session_logs_self_out(self):
        link_client = self._link_session_client()

        resp = link_client.post(reverse('search:settings'), {
            'setting': 'generate_session_link', 'pane': 'account',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn('_auth_user_id', link_client.session)

        resp = link_client.get(reverse('search:settings'))
        self.assertRedirects(resp, f"/login/?next={reverse('search:settings')}")

    def test_regenerating_from_inside_a_link_session_still_reveals_the_link(self):
        # Regenerating from within a private-link session logs that session out
        # (above). The clear text exists only in this one response cycle, so
        # check it isn't lost with the flushed session: it survives into the
        # session the user signs back in with, and is shown there.
        link_client = self._link_session_client()
        link_client.post(reverse('search:settings'), {
            'setting': 'generate_session_link', 'pane': 'account',
        })
        self.assertNotIn('_auth_user_id', link_client.session)

        link_client.post(reverse('login'), {'username': 'carol', 'password': 'pw'})
        resp = link_client.get(reverse('search:settings') + '?pane=account')
        token = private_link_token(resp)
        self.assertIsNotNone(token, 'the newly generated link was never shown')
        self.assertEqual(SessionLink.authenticate(token).user, self.user)
