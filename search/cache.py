import concurrent.futures
import hashlib
import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)

CACHE_TTL = timedelta(hours=1)

# Brave's API terms permit only the "transient storage required for the
# operation" of the service.
BRAVE_CACHE_TTL = timedelta(seconds=60)

# Pixabay's API terms require cached results/images to be kept for at least
# 24 hours (no more-frequent re-fetching of the same content).
PIXABAY_CACHE_TTL = timedelta(hours=24)


def _make_cache_key(tab, query, engine, page, safe_search, lang, date):
    raw = f'{tab}:{query}:{engine}:{page}:{safe_search}:{lang}:{date}'
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(key):
    from search.models import SearchCache
    try:
        entry = SearchCache.objects.get(cache_key=key, expires_at__gt=timezone.now())
        logger.debug('cache hit key=%s', key[:8])
        return entry.results
    except SearchCache.DoesNotExist:
        return None
    except Exception as exc:
        logger.warning('cache get error: %s', exc)
        return None


def _cache_set(key, results, ttl=CACHE_TTL):
    from search.models import SearchCache
    try:
        SearchCache.objects.update_or_create(
            cache_key=key,
            defaults={
                'results': results,
                'expires_at': timezone.now() + ttl,
            },
        )
    except Exception as exc:
        logger.warning('cache set error: %s', exc)


def cache_or_fetch(key, fetch, ttl=CACHE_TTL):
    """Return the cached value for *key*, else ``fetch()`` (and cache it)."""
    cached = _cache_get(key) if key else None
    if cached is not None:
        return cached
    value = fetch()
    if value is not None and key:
        _cache_set(key, value, ttl=ttl)
    return value


def cache_or_fetch_many(jobs):
    """Resolve several independent cache-or-fetch lookups, running cache misses
    concurrently.
    """
    results = [None] * len(jobs)
    misses = []
    for i, (key, _fn, _ttl) in enumerate(jobs):
        cached = _cache_get(key) if key else None
        if cached is not None:
            results[i] = cached
        else:
            misses.append(i)

    if misses:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(misses)) as ex:
            futures = {i: ex.submit(jobs[i][1]) for i in misses}
            for i, future in futures.items():
                results[i] = future.result()
        for i in misses:
            key, _fn, ttl = jobs[i]
            if results[i] is not None and key:
                _cache_set(key, results[i], ttl=ttl)

    return results
