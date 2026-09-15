import logging
import time
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from django.conf import settings

from . import health

logger = logging.getLogger(__name__)

# Sent on every outbound request the app makes on a user's behalf. Wikimedia
# (and similar CDNs) reject a request without a descriptive User-Agent, and
# Nominatim's usage policy asks for one, so one identity covers them all.
USER_AGENT = 'Seurch/1.0 (privacy-first search; https://github.com/seurch-eu/seurch)'

BRAVE_API_BASE = 'https://api.search.brave.com/res/v1'
MOJEEK_API_BASE = 'https://api.mojeek.com/search'
MARGINALIA_API_BASE = 'https://api2.marginalia-search.com'
STAAN_API_BASE = 'https://api.staan.ai/v2'
SEPIA_API_BASE = 'https://sepiasearch.org/api/v1'
PIXABAY_API_BASE = 'https://pixabay.com/api/'
WORLDNEWS_API_BASE = 'https://api.worldnewsapi.com'

RESULTS_PER_PAGE = 10

REAL_ENGINES = ('brave', 'mojeek', 'marginalia', 'staan')


def selected_engines(engine):
    """Normalise an engine selection to a canonical tuple of real engines."""
    if isinstance(engine, str):
        names = REAL_ENGINES if engine == 'all' else (engine,)
    else:
        names = tuple(engine or ())
    chosen = set(names)
    return tuple(e for e in REAL_ENGINES if e in chosen)


_FRESHNESS = {'d': 'pd', 'w': 'pw', 'm': 'pm', 'y': 'py'}

# The same time windows expressed as deltas, for providers that filter by an
# absolute "published after" timestamp rather than Brave's freshness codes, the
# World News API (news) and SepiaSearch/PeerTube (videos).
_FRESHNESS_DELTA = {
    'd': timedelta(days=1),
    'w': timedelta(weeks=1),
    'm': timedelta(days=30),
    'y': timedelta(days=365),
}

# Brave requires both search_lang + country for effective language filtering
LANG_COUNTRY = {
    'en': 'us', 'fr': 'fr', 'de': 'de', 'es': 'es',
    'it': 'it', 'pt': 'pt', 'nl': 'nl',
}

# Staan takes a single ``market`` (language + region). It serves fr-FR, de-DE and
# a family of English markets (en-US/GB/FR/CA/AU/IN/IE/NZ/ZA/SG) only, so the
# app's other languages have no market to map onto, ``staan_market`` leaves the
# parameter off for those and lets the API apply its own default.
STAAN_MARKETS = {'en': 'en-US', 'fr': 'fr-FR', 'de': 'de-DE'}

# Staan pages ten results at a time and rejects an offset past 30, so it can
# only serve the first four pages of a search.
STAAN_MAX_OFFSET = 30

# Staan's ``q`` is capped at 400 characters; a longer query is rejected upstream,
# so the fetcher skips the call rather than spending a request (and a spurious
# "down" health record) on a request that cannot succeed.
STAAN_MAX_QUERY_LEN = 400


def query_label(query):
    """How a query may appear in the app log."""
    return repr(query) if settings.LOG_SEARCH_QUERIES else f'len={len(query)}'


def interleave(lists):
    """Round-robin merge: one item from each list in turn, each list keeping its
    own order, so a blended tab alternates between its providers."""
    merged = []
    for i in range(max((len(items) for items in lists), default=0)):
        for items in lists:
            if i < len(items):
                merged.append(items[i])
    return merged


def staan_market(lang):
    """Staan ``market`` for a search language, or '' when it serves none."""
    return STAAN_MARKETS.get(lang, '')


def brave_safesearch(safe_search):
    """Map the on/off safe-search preference to Brave's ``safesearch`` value."""
    return 'off' if safe_search == 'off' else 'strict'


def _provider_request(provider, url, params=None, headers=None, timeout=10, json=None):
    """Call *url* for *provider* and return the parsed JSON body, or ``None``."""
    path = urlsplit(url).path
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=timeout) as client:
            headers = {'Accept': 'application/json', **(headers or {})}
            if json is None:
                r = client.get(url, params=params, headers=headers)
            else:
                r = client.post(url, params=params, json=json, headers=headers)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        logger.warning('%s %s error (%.2fs): %s', provider, path, time.monotonic() - t0, exc)
        health.record_down(provider, str(exc))
        return None
    logger.debug('%s %s ok (%.2fs)', provider, path, time.monotonic() - t0)
    health.record_ok(provider)
    return data


def _brave_request(endpoint, params, api_key=None):
    """Brave Search API. *api_key* overrides the default key (the suggest
    endpoint bills against its own subscription)."""
    if api_key is None:
        api_key = settings.BRAVE_API_KEY
    if not api_key:
        return None
    return _provider_request('brave', f'{BRAVE_API_BASE}{endpoint}', params, {
        'Accept-Encoding': 'gzip',
        'X-Subscription-Token': api_key,
    })


def _mojeek_request(params):
    if not settings.MOJEEK_API_KEY:
        return None
    return _provider_request(
        'mojeek', MOJEEK_API_BASE,
        {**params, 'api_key': settings.MOJEEK_API_KEY, 'fmt': 'json'},
    )


def _marginalia_request(params):
    if not settings.MARGINALIA_API_KEY:
        return None
    return _provider_request(
        'marginalia', f'{MARGINALIA_API_BASE}/search', params,
        {'API-Key': settings.MARGINALIA_API_KEY},
    )


def _staan_request(params):
    """Staan web search API (https://staan.ai/)."""
    if not settings.STAAN_API_KEY:
        return None
    return _provider_request(
        'staan', f'{STAAN_API_BASE}/search/web', params,
        {'Authorization': f'Bearer {settings.STAAN_API_KEY}'},
    )


def _sepia_request(params):
    """SepiaSearch video API, a free, privacy-focused PeerTube search (no key)."""
    return _provider_request('sepia', f'{SEPIA_API_BASE}/search/videos', params)


def _pixabay_request(params):
    """Pixabay is a royalty free image search engine."""
    if not settings.PIXABAY_API_KEY:
        return None
    return _provider_request(
        'pixabay', PIXABAY_API_BASE, {**params, 'key': settings.PIXABAY_API_KEY},
    )


def _worldnews_request(params):
    """World News API search endpoint (https://worldnewsapi.com/)."""
    if not settings.WORLDNEWS_API_KEY:
        return None
    return _provider_request(
        'worldnews', f'{WORLDNEWS_API_BASE}/search-news', params,
        {'x-api-key': settings.WORLDNEWS_API_KEY},
    )
