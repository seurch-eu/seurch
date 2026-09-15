"""API-key authentication for the public API.

A client presents its key in one of two headers::

    Authorization: Api-Key seurch_sk_<prefix>.<secret>
    X-Api-Key: seurch_sk_<prefix>.<secret>

``Authorization`` is the documented form (``Bearer`` is accepted as a synonym
for convenience). A request with **no** key header is left unauthenticated so
the permission layer answers a clean ``401``; a request with a *bad* key is
rejected outright.
"""

import logging
import time

from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from .models import ApiKey

logger = logging.getLogger(__name__)

# Keywords accepted in the Authorization header before the key.
_AUTH_KEYWORDS = (b'api-key', b'bearer')

# In-process throttle for the per-request ``last_used_at`` write: a single hot
# row per key, so we update it at most once a minute rather than on every call
# (mirrors search.health / search.usage - bookkeeping must not tax the hot path).
_TOUCH_INTERVAL_SECONDS = 60
_last_touch: dict[str, float] = {}


class ApiKeyAuthentication(BaseAuthentication):
    keyword = 'Api-Key'

    def authenticate(self, request):
        presented = self._extract_key(request)
        if not presented:
            return None  # no credentials -> permission layer returns 401

        instance = ApiKey.authenticate(presented)
        if instance is None:
            raise AuthenticationFailed('Invalid or revoked API key.')
        if not instance.user.is_active:
            raise AuthenticationFailed('User account is disabled.')

        self._touch(instance)
        return (instance.user, instance)

    def authenticate_header(self, request):
        # Drives the WWW-Authenticate header so unauthenticated requests get a
        # 401 (not a 403).
        return self.keyword

    @staticmethod
    def _extract_key(request) -> str:
        """Pull the raw key from the Authorization or X-Api-Key header."""
        auth = get_authorization_header(request).split()
        # A lone token with no recognised keyword isn't ours - ignore it so
        # other schemes (none, here) aren't shadowed.
        if auth and auth[0].lower() in _AUTH_KEYWORDS and len(auth) == 2:
            return auth[1].decode('latin-1')
        x_api_key = request.META.get('HTTP_X_API_KEY', '')
        return x_api_key.strip()

    @staticmethod
    def _touch(instance: ApiKey) -> None:
        now = time.monotonic()
        last = _last_touch.get(instance.prefix)
        if last is not None and (now - last) < _TOUCH_INTERVAL_SECONDS:
            return
        _last_touch[instance.prefix] = now
        try:
            instance.touch_last_used()
        except Exception:  # bookkeeping must never break a request
            logger.debug('api key last_used update failed', exc_info=True)
