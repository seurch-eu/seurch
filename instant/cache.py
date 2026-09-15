"""Tiny TTL cache backed by the ``InstantCache`` table.

Mirrors the pattern in ``search.cache`` but lives in this app so the two
stay decoupled. Used for network-backed answers (weather) to keep upstream
request rates polite.
"""

import hashlib
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


def make_key(*parts):
    raw = ':'.join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()


def get(key):
    from .models import InstantCache
    try:
        entry = InstantCache.objects.get(cache_key=key, expires_at__gt=timezone.now())
        return entry.data
    except InstantCache.DoesNotExist:
        return None
    except Exception as exc:
        logger.warning('instant cache get error: %s', exc)
        return None


def set(key, data, ttl):  # shadows the builtin deliberately: cache-style name
    from .models import InstantCache
    try:
        InstantCache.objects.update_or_create(
            cache_key=key,
            defaults={'data': data, 'expires_at': timezone.now() + ttl},
        )
    except Exception as exc:
        logger.warning('instant cache set error: %s', exc)
