"""Tests for the public JSON API: keys, authentication, throttling, serializers.

These tests were generated with an LLM and then reviewed by hand. Treat a
failure as a real signal, but read the assertion before trusting it: a test
here can encode an assumption the code never promised. Fix or delete such a
test rather than bending the code to satisfy it.
"""

import importlib
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import clear_url_caches, reverse
from rest_framework.test import APIClient

from api import keys
from api.models import ApiKey
from search import health
from search.clients import REAL_ENGINES
from search.models import ProviderStatus

# Drop throttling for the test run so the many requests across test methods
# don't trip the per-key rate limit; keep JSON-only rendering deterministic.
API_TEST_SETTINGS = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['api.authentication.ApiKeyAuthentication'],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
}


# --------------------------------------------------------------------------- #
# Key generation / model
# --------------------------------------------------------------------------- #

class KeyHelpersTests(TestCase):
    def test_generate_and_parse_roundtrip(self):
        prefix, secret, full_key = keys.generate_key()
        self.assertTrue(full_key.startswith(keys.KEY_BRAND))
        parsed_prefix, parsed_secret = keys.parse_key(full_key)
        self.assertEqual(parsed_prefix, prefix)
        self.assertEqual(parsed_secret, secret)

    def test_verify_secret(self):
        _, secret, _ = keys.generate_key()
        hashed = keys.hash_secret(secret)
        self.assertTrue(keys.verify_secret(secret, hashed))
        self.assertFalse(keys.verify_secret(secret + 'x', hashed))

    def test_parse_malformed(self):
        self.assertEqual(keys.parse_key(''), (None, None))
        self.assertEqual(keys.parse_key('garbage'), (None, None))
        self.assertEqual(keys.parse_key('seurch_sk_onlyprefix'), (None, None))


class ApiKeyModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw')

    def test_create_returns_full_key_and_stores_no_secret(self):
        instance, full_key = ApiKey.create(self.user, name='laptop')
        self.assertEqual(instance.name, 'laptop')
        # The raw secret is never stored anywhere on the row.
        _, secret = keys.parse_key(full_key)
        self.assertNotIn(secret, instance.hashed_key)
        self.assertEqual(instance.hashed_key, keys.hash_secret(secret))

    def test_authenticate_valid_invalid_revoked(self):
        instance, full_key = ApiKey.create(self.user)
        self.assertEqual(ApiKey.authenticate(full_key), instance)
        self.assertIsNone(ApiKey.authenticate(full_key + 'tampered'))
        self.assertIsNone(ApiKey.authenticate('seurch_sk_deadbeef.nope'))
        instance.revoked = True
        instance.save(update_fields=['revoked'])
        self.assertIsNone(ApiKey.authenticate(full_key))

    def test_name_defaults_when_blank(self):
        instance, _ = ApiKey.create(self.user, name='   ')
        self.assertEqual(instance.name, 'API key')


# --------------------------------------------------------------------------- #
# Authentication / permission
# --------------------------------------------------------------------------- #

@override_settings(REST_FRAMEWORK=API_TEST_SETTINGS, STATUS_PAGE_ENABLED=True)
class AuthenticationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('bob', password='pw')
        _, self.key = ApiKey.create(self.user)
        self.client = APIClient()

    def test_missing_key_is_401(self):
        resp = self.client.get(reverse('api:status'))
        self.assertEqual(resp.status_code, 401)

    def test_bad_key_is_401(self):
        self.client.credentials(HTTP_AUTHORIZATION='Api-Key seurch_sk_dead.beef')
        resp = self.client.get(reverse('api:status'))
        self.assertEqual(resp.status_code, 401)

    def test_revoked_key_is_401(self):
        key = ApiKey.objects.get(user=self.user)
        key.revoked = True
        key.save(update_fields=['revoked'])
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {self.key}')
        resp = self.client.get(reverse('api:status'))
        self.assertEqual(resp.status_code, 401)

    def test_authorization_header_works(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {self.key}')
        resp = self.client.get(reverse('api:status'))
        self.assertEqual(resp.status_code, 200)

    def test_x_api_key_header_works(self):
        self.client.credentials(HTTP_X_API_KEY=self.key)
        resp = self.client.get(reverse('api:status'))
        self.assertEqual(resp.status_code, 200)

    def test_bearer_keyword_accepted(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.key}')
        resp = self.client.get(reverse('api:status'))
        self.assertEqual(resp.status_code, 200)

    def test_inactive_user_rejected(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {self.key}')
        resp = self.client.get(reverse('api:status'))
        self.assertEqual(resp.status_code, 401)


# --------------------------------------------------------------------------- #
# Endpoints (services mocked - no network)
# --------------------------------------------------------------------------- #

@override_settings(REST_FRAMEWORK=API_TEST_SETTINGS, STATUS_PAGE_ENABLED=True)
class EndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('carol', password='pw')
        _, self.key = ApiKey.create(self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {self.key}')

    def test_root_lists_endpoints(self):
        resp = self.client.get(reverse('api:root'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('web_search', resp.data)
        self.assertIn('translate_languages', resp.data)

    def test_web_search_shapes_results(self):
        sample = [{
            'title': 'Example', 'url': 'https://example.com', 'description': 'desc',
            'display_url': 'example.com', 'favicon_url': '', 'sitelinks': [],
            'age': '', 'source': 'brave', 'source_label': 'Brave',
        }]
        with patch('api.views.fetch_web', return_value=(sample, 'correction')) as m:
            resp = self.client.get(reverse('api:web'), {'q': 'hello', 'engine': 'brave', 'page': '2'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['query'], 'hello')
        self.assertEqual(resp.data['page'], 2)
        self.assertEqual(resp.data['correction'], 'correction')
        self.assertEqual(resp.data['results'][0]['url'], 'https://example.com')
        # engine parsed to a list
        self.assertEqual(m.call_args.args[1], ['brave'])

    def test_web_requires_query(self):
        resp = self.client.get(reverse('api:web'))
        self.assertEqual(resp.status_code, 400)
        self.assertIn('q', resp.data)

    def test_web_accepts_staan_engine(self):
        with patch('api.views.fetch_web', return_value=([], '')) as m:
            resp = self.client.get(reverse('api:web'), {'q': 'hello', 'engine': 'staan'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['engine'], ['staan'])
        self.assertEqual(m.call_args.args[1], ['staan'])

    def test_web_default_engine_covers_every_engine(self):
        # No ``engine`` parameter → every real engine, Staan included.
        with patch('api.views.fetch_web', return_value=([], '')) as m:
            resp = self.client.get(reverse('api:web'), {'q': 'hello'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(m.call_args.args[1], list(REAL_ENGINES))
        self.assertIn('staan', m.call_args.args[1])

    def test_web_rejects_unknown_engine(self):
        resp = self.client.get(reverse('api:web'), {'q': 'x', 'engine': 'bing'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('engine', resp.data)

    def test_image_search_flattens_thumbnail(self):
        sample = [{'title': 'cat', 'url': 'https://pix/1', 'source': 'Pixabay',
                   'thumbnail': {'src': 'https://img/cat.jpg'}}]
        with patch('api.views.fetch_images', return_value=sample):
            resp = self.client.get(reverse('api:images'), {'q': 'cat'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['results'][0]['thumbnail'], 'https://img/cat.jpg')

    def test_similar_image_search(self):
        sample = [{'title': 'cat', 'url': 'https://pix/2', 'source': 'Pixabay',
                   'thumbnail': {'src': 'https://img/2.jpg'}}]
        with patch('api.views.fetch_similar_images', return_value=('cats', sample)) as m:
            resp = self.client.get(reverse('api:images-similar'),
                                   {'q': 'a cute cat', 'exclude_url': 'https://pix/1'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['query'], 'cats')
        self.assertEqual(resp.data['results'][0]['thumbnail'], 'https://img/2.jpg')
        # The caption is passed through as the seed; exclude_url is honoured.
        self.assertEqual(m.call_args.args[0], 'a cute cat')
        self.assertEqual(m.call_args.kwargs['exclude_url'], 'https://pix/1')

    def test_similar_image_requires_seed(self):
        resp = self.client.get(reverse('api:images-similar'))
        self.assertEqual(resp.status_code, 400)

    def test_news_search(self):
        sample = [{'title': 'n', 'url': 'https://n/1', 'description': 'd',
                   'meta_url': {'hostname': 'n.com'}, 'thumbnail': {'src': 's'},
                   'age': '1 day ago', 'source': 'Brave'}]
        with patch('api.views.fetch_news', return_value=sample):
            resp = self.client.get(reverse('api:news'), {'q': 'world'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['results'][0]['hostname'], 'n.com')

    def test_video_search(self):
        sample = [{'title': 'v', 'url': 'https://v/1', 'meta_url': {'hostname': 'v.com'},
                   'thumbnail': {'src': 's'}, 'video': {'duration': '1:23'}, 'age': '2024'}]
        with patch('api.views.fetch_videos', return_value=sample):
            resp = self.client.get(reverse('api:videos'), {'q': 'clip'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['results'][0]['duration'], '1:23')

    def test_maps_geocode(self):
        place = {'name': 'Paris', 'display_name': 'Paris, France', 'lat': 48.85, 'lon': 2.35}
        with patch('api.views.fetch_geocode', return_value=[place]) as m:
            resp = self.client.get(reverse('api:maps'), {'q': 'paris', 'limit': '5'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['places'][0]['name'], 'Paris')
        self.assertEqual(m.call_args.kwargs['limit'], 5)

    def test_maps_reverse_coords(self):
        place = {'name': 'Pin', 'lat': 1.0, 'lon': 2.0}
        with patch('api.views.place_from_coords', return_value=place) as m:
            resp = self.client.get(reverse('api:maps'), {'lat': '1.0', 'lon': '2.0'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['places'][0]['lat'], 1.0)
        m.assert_called_once()

    def test_maps_bad_coords(self):
        resp = self.client.get(reverse('api:maps'), {'lat': 'x', 'lon': 'y'})
        self.assertEqual(resp.status_code, 400)

    def test_translate_get(self):
        with patch('api.views.fetch_translation',
                   return_value={'translated_text': 'hola', 'detected_lang': 'en'}):
            resp = self.client.get(reverse('api:translate'), {'q': 'hello', 'target': 'es'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['translated_text'], 'hola')
        self.assertEqual(resp.data['target'], 'es')

    def test_translate_post(self):
        with patch('api.views.fetch_translation',
                   return_value={'translated_text': 'bonjour', 'detected_lang': ''}):
            resp = self.client.post(reverse('api:translate'),
                                    {'q': 'hi', 'target': 'fr', 'source': 'en'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['translated_text'], 'bonjour')

    def test_translate_unavailable_is_503(self):
        with patch('api.views.fetch_translation', return_value=None):
            resp = self.client.get(reverse('api:translate'), {'q': 'hi', 'target': 'fr'})
        self.assertEqual(resp.status_code, 503)

    def test_translate_requires_target(self):
        resp = self.client.get(reverse('api:translate'), {'q': 'hi'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('target', resp.data)

    def test_languages(self):
        with patch('api.views.fetch_languages', return_value=[('en', 'English'), ('es', 'Spanish')]):
            resp = self.client.get(reverse('api:translate-languages'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['languages'][0], {'code': 'en', 'name': 'English'})

    def test_instant_math_answer(self):
        # Math is a local handler - no network - so this exercises the real chain.
        resp = self.client.get(reverse('api:instant'), {'q': '2+2'})
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.data['answer'])
        self.assertEqual(resp.data['answer']['type'], 'math')

    def test_instant_no_answer(self):
        resp = self.client.get(reverse('api:instant'), {'q': 'some ordinary search words'})
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.data['answer'])

    def test_cards(self):
        fake_cards = {
            'thetvdb_card': {'title': 'Movie'}, 'tripadvisor_card': None,
            'stackexchange_card': None, 'map_answer': None, 'wikipedia_first': False,
        }
        with patch('api.views.fetch_web', return_value=([], '')), \
                patch('api.views.fetch_wikipedia', return_value={'title': 'Wiki'}), \
                patch('api.views.knowledge_cards', return_value=fake_cards):
            resp = self.client.get(reverse('api:cards'), {'q': 'inception'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['wikipedia']['title'], 'Wiki')
        self.assertEqual(resp.data['thetvdb']['title'], 'Movie')
        self.assertIsNone(resp.data['tripadvisor'])

    def test_suggest(self):
        with patch('api.views.fetch_suggestions', return_value=['a', 'ab', 'abc']):
            resp = self.client.get(reverse('api:suggest'), {'q': 'a'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['suggestions'], ['a', 'ab', 'abc'])

    def test_status(self):
        # Provider health is written from request worker threads whose DB
        # connection lives outside this test's transaction, so earlier view
        # tests can leak committed rows (harmless on SQLite, where the threaded
        # writes fail under lock, but real on PostgreSQL/CI). Baseline the table
        # and the in-process throttle first, mirroring search.tests.
        health._last_write.clear()
        ProviderStatus.objects.all().delete()
        ProviderStatus.objects.create(provider='wikipedia', ok=True, source='query')
        resp = self.client.get(reverse('api:status'))
        self.assertEqual(resp.status_code, 200)
        slugs = {p['slug']: p for p in resp.data['providers']}
        self.assertIn('wikipedia', slugs)
        self.assertEqual(slugs['wikipedia']['state'], 'up')

    @override_settings(STATUS_PAGE_ENABLED=False)
    def test_status_gone_when_the_status_page_is_disabled(self):
        # Same data as the /status page, so the same toggle hides both; uptime
        # monitoring uses the keyless /status/health endpoint instead.
        self.assertEqual(self.client.get(reverse('api:status')).status_code, 404)
        self.assertNotIn('status', self.client.get(reverse('api:root')).data)

    def test_status_listed_in_the_root_index_when_enabled(self):
        self.assertIn('status', self.client.get(reverse('api:root')).data)

    def test_key_info(self):
        resp = self.client.get(reverse('api:key'))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['prefix'].startswith(keys.KEY_BRAND))
        self.assertFalse(resp.data['revoked'])


# --------------------------------------------------------------------------- #
# Search counting (per-query, except the meta endpoints)
# --------------------------------------------------------------------------- #

@override_settings(REST_FRAMEWORK=API_TEST_SETTINGS, STATUS_PAGE_ENABLED=True)
class SearchCountingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('counter', password='pw')
        _, self.key = ApiKey.create(self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {self.key}')

    def test_non_meta_endpoints_count(self):
        empty_cards = {
            'thetvdb_card': None, 'tripadvisor_card': None, 'stackexchange_card': None,
            'map_answer': None, 'wikipedia_first': False,
        }
        with patch('api.views.usage.record_search') as rec, \
                patch('api.views.fetch_web', return_value=([], '')), \
                patch('api.views.fetch_images', return_value=[]), \
                patch('api.views.fetch_similar_images', return_value=('seed', [])), \
                patch('api.views.fetch_news', return_value=[]), \
                patch('api.views.fetch_videos', return_value=[]), \
                patch('api.views.fetch_geocode', return_value=[]), \
                patch('api.views.fetch_translation', return_value={'translated_text': 'x', 'detected_lang': ''}), \
                patch('api.views.fetch_languages', return_value=[]), \
                patch('api.views.fetch_wikipedia', return_value=None), \
                patch('api.views.knowledge_cards', return_value=empty_cards), \
                patch('api.views.fetch_suggestions', return_value=[]):
            self.client.get(reverse('api:web'), {'q': 'x'})
            self.client.get(reverse('api:images'), {'q': 'x'})
            self.client.get(reverse('api:images-similar'), {'q': 'x'})
            self.client.get(reverse('api:news'), {'q': 'x'})
            self.client.get(reverse('api:videos'), {'q': 'x'})
            self.client.get(reverse('api:maps'), {'q': 'x'})
            self.client.get(reverse('api:translate'), {'q': 'x', 'target': 'es'})
            self.client.get(reverse('api:translate-languages'))
            self.client.get(reverse('api:instant'), {'q': '2+2'})
            self.client.get(reverse('api:cards'), {'q': 'x'})
            self.client.get(reverse('api:suggest'), {'q': 'x'})
        # One increment per non-meta endpoint (11 of them).
        self.assertEqual(rec.call_count, 11)

    def test_meta_endpoints_do_not_count(self):
        with patch('api.views.usage.record_search') as rec:
            self.assertEqual(self.client.get(reverse('api:root')).status_code, 200)
            self.assertEqual(self.client.get(reverse('api:status')).status_code, 200)
            self.assertEqual(self.client.get(reverse('api:key')).status_code, 200)
        rec.assert_not_called()

    def test_bad_request_does_not_count(self):
        # A malformed query (missing q) is rejected before any search runs.
        with patch('api.views.usage.record_search') as rec:
            self.assertEqual(self.client.get(reverse('api:web')).status_code, 400)
        rec.assert_not_called()


# --------------------------------------------------------------------------- #
# Management commands
# --------------------------------------------------------------------------- #

class ManagementCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('dave', password='pw')

    def test_create_api_key_command(self):
        out = StringIO()
        call_command('create_api_key', 'dave', '--name', 'cron', stdout=out)
        output = out.getvalue()
        self.assertIn(keys.KEY_BRAND, output)
        self.assertEqual(ApiKey.objects.filter(user=self.user, name='cron').count(), 1)
        # The printed key must actually authenticate.
        printed = next(line.strip() for line in output.splitlines()
                       if line.strip().startswith(keys.KEY_BRAND))
        self.assertIsNotNone(ApiKey.authenticate(printed))

    def test_create_api_key_unknown_user(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command('create_api_key', 'nobody')

    def test_revoke_api_key_command(self):
        instance, _ = ApiKey.create(self.user)
        call_command('revoke_api_key', instance.display_prefix, stdout=StringIO())
        instance.refresh_from_db()
        self.assertTrue(instance.revoked)

    def test_list_api_keys_command(self):
        ApiKey.create(self.user, name='visible-key')
        out = StringIO()
        call_command('list_api_keys', '--user', 'dave', stdout=out)
        self.assertIn('visible-key', out.getvalue())


# --------------------------------------------------------------------------- #
# Self-service Settings UI
# --------------------------------------------------------------------------- #

class SettingsApiKeyUITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('erin', password='pw')
        self.client.login(username='erin', password='pw')

    def test_empty_state(self):
        resp = self.client.get(reverse('search:settings') + '?pane=apikeys')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "You haven't created any API keys yet.")

    def test_create_key_shows_secret_once(self):
        resp = self.client.post(reverse('search:settings'), {
            'setting': 'create_api_key', 'pane': 'apikeys', 'key_name': 'My script',
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ApiKey.objects.filter(user=self.user, name='My script').count(), 1)
        # The full secret is revealed exactly once, in the one-time banner...
        self.assertContains(resp, 'Your new API key')
        # ...and is gone on the next load (popped from the session).
        again = self.client.get(reverse('search:settings') + '?pane=apikeys')
        self.assertNotContains(again, 'Your new API key')

    def test_key_listed_then_revoked(self):
        instance, _ = ApiKey.create(self.user, name='listme')
        resp = self.client.get(reverse('search:settings') + '?pane=apikeys')
        self.assertContains(resp, 'listme')
        self.assertContains(resp, instance.display_prefix)

        self.client.post(reverse('search:settings'), {
            'setting': 'delete_api_key', 'pane': 'apikeys', 'key_id': instance.id,
        })
        instance.refresh_from_db()
        self.assertTrue(instance.revoked)
        # Revoked keys drop out of the list.
        after = self.client.get(reverse('search:settings') + '?pane=apikeys')
        self.assertNotContains(after, 'listme')

    def test_cannot_revoke_another_users_key(self):
        other = User.objects.create_user('mallory', password='pw')
        instance, _ = ApiKey.create(other, name='not-yours')
        self.client.post(reverse('search:settings'), {
            'setting': 'delete_api_key', 'pane': 'apikeys', 'key_id': instance.id,
        })
        instance.refresh_from_db()
        self.assertFalse(instance.revoked)

    def test_key_limit_enforced(self):
        from search.views import API_KEY_LIMIT
        for i in range(API_KEY_LIMIT):
            ApiKey.create(self.user, name=f'key-{i}')
        resp = self.client.post(reverse('search:settings'), {
            'setting': 'create_api_key', 'pane': 'apikeys', 'key_name': 'one too many',
        }, follow=True)
        self.assertEqual(ApiKey.objects.filter(user=self.user, revoked=False).count(), API_KEY_LIMIT)
        self.assertContains(resp, 'maximum')

    @override_settings(PUBLIC_API_ENABLED=False)
    def test_create_key_blocked_when_public_api_disabled(self):
        # Belt-and-suspenders server-side check behind the hidden form: even a
        # hand-crafted POST must not mint a key nobody can use.
        resp = self.client.post(reverse('search:settings'), {
            'setting': 'create_api_key', 'pane': 'apikeys', 'key_name': 'should not exist',
        }, follow=True)
        self.assertEqual(ApiKey.objects.filter(user=self.user).count(), 0)
        self.assertContains(resp, 'disabled')

    @override_settings(PUBLIC_API_ENABLED=False)
    def test_create_key_form_hidden_when_public_api_disabled(self):
        resp = self.client.get(reverse('search:settings') + '?pane=apikeys')
        self.assertNotContains(resp, 'name="key_name"')
        self.assertContains(resp, 'The public API is disabled on this instance')

    def test_revoke_still_works_when_public_api_disabled(self):
        instance, _ = ApiKey.create(self.user, name='old-key')
        with override_settings(PUBLIC_API_ENABLED=False):
            self.client.post(reverse('search:settings'), {
                'setting': 'delete_api_key', 'pane': 'apikeys', 'key_id': instance.id,
            })
        instance.refresh_from_db()
        self.assertTrue(instance.revoked)


# --------------------------------------------------------------------------- #
# PUBLIC_API_ENABLED gating the /api/v1/ mount itself
# --------------------------------------------------------------------------- #

def _reload_root_urlconf():
    """Rebuild config.urls' urlpatterns from the current settings.

    The /api/v1/ mount is decided once, at module import time, from
    PUBLIC_API_ENABLED (exactly like a real deployment toggling the env var
    and restarting). A test that overrides the setting must force that
    rebuild and clear Django's resolver cache, or it keeps resolving against
    whichever urlpatterns happened to be built first.
    """
    import config.urls
    clear_url_caches()
    importlib.reload(config.urls)


class PublicApiUrlMountTests(TestCase):
    def tearDown(self):
        # Settings are already restored by the time tearDown runs, so this
        # puts urlpatterns back in sync for every other test in the suite.
        _reload_root_urlconf()

    @override_settings(PUBLIC_API_ENABLED=False)
    def test_api_v1_not_found_when_disabled(self):
        _reload_root_urlconf()
        resp = self.client.get('/api/v1/')
        self.assertEqual(resp.status_code, 404)

    def test_api_v1_reachable_when_enabled(self):
        _reload_root_urlconf()
        resp = self.client.get('/api/v1/')
        # No key supplied: authentication, not routing, should reject it.
        self.assertEqual(resp.status_code, 401)
