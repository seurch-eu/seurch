"""Seurch settings middleware."""

import logging

from . import backup, preferences
from .models import UserSettings

logger = logging.getLogger(__name__)


class AutoSyncSettingsMiddleware:
    """Refresh the preferences cookie whenever it lags the account snapshot."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        saved = None
        try:
            saved = self._unseen_snapshot(request)
            if saved is not None:
                # Make the refreshed preferences effective for *this* request,
                # not just the next one: views read them via request.COOKIES.
                prefs = preferences.coerce(saved.document.get('preferences', {}))
                request.COOKIES[preferences.COOKIE_NAME] = preferences.encode(prefs)
        except Exception:
            saved = None
            logger.exception('error during auto-sync prefs refresh')

        response = self.get_response(request)

        # Persist the refresh, unless the view itself wrote the preferences
        # cookie (a settings save already stamps the response with the state
        # it just synced, which is newer than the snapshot read above).
        if saved is not None and preferences.COOKIE_NAME not in response.cookies:
            try:
                prefs = preferences.coerce(saved.document.get('preferences', {}))
                preferences.apply_to_response(response, prefs)
                backup.stamp_revision(response, saved)
                logger.debug('refreshed prefs from snapshot user=%s', request.user.username)
            except Exception:
                logger.exception('error persisting auto-sync prefs refresh')

        return response

    @staticmethod
    def _unseen_snapshot(request):
        """The user's UserSettings row when this device hasn't seen it, else None."""
        if not (hasattr(request, 'user') and request.user.is_authenticated):
            return None
        saved = UserSettings.objects.filter(user=request.user).first()
        if saved is None or not saved.document:
            return None
        if (preferences.COOKIE_NAME in request.COOKIES
                and request.COOKIES.get(backup.REV_COOKIE_NAME) == backup.revision(saved)):
            return None
        return saved
