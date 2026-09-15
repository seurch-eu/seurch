import hashlib
import secrets

from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import models
from django.utils import timezone

# Session key set on any Django session created via private_session_login,
# so generate()/turning the link off can find and kill those sessions
# without also catching the user's ordinary password-login sessions (which
# share the same _auth_user_id but never set this key).
SESSION_LINK_SESSION_KEY = 'via_session_link'

# Bytes of randomness behind a session-link token. 32 bytes is ~256 bits,
# far past anything guessable, and mirrors the public-API key secret.
TOKEN_BYTES = 32


def hash_token(token):
    """SHA-256 hex digest of a session-link token - what the database stores.

    A fast hash is the right choice here, for the same reasons it is for the
    public-API keys (see ``api.keys``): the token carries ~256 bits of
    entropy, so there is no dictionary to run against it and a slow KDF would
    only add latency to a request that has to verify on every use. The digest
    is 64 hex chars, which is exactly the column width.

    There is no constant-time comparison to do: the lookup is an indexed
    equality match on the digest, and an attacker who can only vary the token
    cannot walk the digest towards a stored value without inverting SHA-256.
    """
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


class SessionLink(models.Model):
    """A private-browsing login link for a user.

    Visiting ``/private/<token>/`` logs the holder in as *user*
    without a password, so a signed-in user can open a private/incognito
    window - which carries no cookies from the normal session - and still
    reach their account.

    The token is password-equivalent, so - like ``ApiKey``, and like a
    password itself - only a hash of it is stored. The clear-text token
    exists once, in the response that created it; Settings shows it that one
    time and can never show it again, so a user who loses the link generates
    a replacement (which invalidates the old one) rather than re-reading the
    stored copy. A dump of this table therefore hands an attacker nothing
    they can sign in with. One row per user.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='session_link',
    )
    # SHA-256 hex of the token; the token itself is never stored. Unique so a
    # presented token resolves to its row with one indexed lookup.
    token_hash = models.CharField(max_length=64, unique=True, db_index=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'accounts_session_link'

    @classmethod
    def generate(cls, user):
        """Create or replace *user*'s session link.

        Returns ``(instance, token)``. *token* is the only time the caller -
        and the user - ever sees the clear-text value: show it once, then
        drop it. Any previous link for the user is overwritten, and so
        stops working.
        """
        token = secrets.token_urlsafe(TOKEN_BYTES)
        instance, _ = cls.objects.update_or_create(
            user=user,
            defaults={
                'token_hash': hash_token(token),
                'created_at': timezone.now(),
                'last_used_at': None,
            },
        )
        return instance, token

    @classmethod
    def authenticate(cls, token):
        """Return the active link for a presented token, or ``None``."""
        if not token:
            return None
        try:
            instance = cls.objects.select_related('user').get(token_hash=hash_token(token))
        except cls.DoesNotExist:
            return None
        if not instance.user.is_active:
            return None
        return instance

    def touch_last_used(self):
        """Record that the link was just used (best-effort, single column write)."""
        self.last_used_at = timezone.now()
        self.save(update_fields=['last_used_at'])

    @classmethod
    def terminate_sessions(cls, request):
        """Kill every active Django session that was opened via a session
        link for ``request.user`` (e.g. because the link is being
        regenerated or turned off).

        If the request's own session is one of them - the user is managing
        Settings from inside a private-link session - flush it instead of
        deleting its row outright, so ``SessionMiddleware`` writes out a
        fresh anonymous session at the end of the request rather than
        reviving the deleted one (which happens if anything, e.g.
        ``django.contrib.messages``, still marks the session modified).
        """
        user_id = str(request.user.pk)
        current_key = request.session.session_key
        current_is_linked = False
        stale_keys = []
        for session in Session.objects.filter(expire_date__gt=timezone.now()):
            data = session.get_decoded()
            if data.get('_auth_user_id') != user_id or not data.get(SESSION_LINK_SESSION_KEY):
                continue
            if session.session_key == current_key:
                current_is_linked = True
            else:
                stale_keys.append(session.session_key)
        if stale_keys:
            Session.objects.filter(session_key__in=stale_keys).delete()
        if current_is_linked:
            request.session.flush()
