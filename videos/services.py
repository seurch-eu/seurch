import logging
from datetime import UTC, datetime
from functools import partial

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from search.cache import (
    BRAVE_CACHE_TTL,
    CACHE_TTL,
    _make_cache_key,
    cache_or_fetch_many,
)
from search.clients import (
    _FRESHNESS,
    _FRESHNESS_DELTA,
    LANG_COUNTRY,
    RESULTS_PER_PAGE,
    _brave_request,
    _sepia_request,
    brave_safesearch,
    interleave,
    selected_engines,
)

logger = logging.getLogger(__name__)

# Sepia only filters by these; anything else is left unfiltered.
_SEPIA_LANGUAGES = frozenset(LANG_COUNTRY)


def fetch_videos(query, engine, page=1, safe_search='on', lang='', date='', sepia_enabled=True):
    # Video sources are blended. Brave backs any selection that includes the
    # Brave engine; Sepia (PeerTube-based) is mixed in whenever the user hasn't
    # disabled it (no API key required), and is the sole video source for the
    # web-only engines (Mojeek, Marginalia, Staan), which have no video search
    # of their own.
    use_brave = 'brave' in selected_engines(engine) and bool(settings.BRAVE_API_KEY)
    use_sepia = sepia_enabled  # Sepia is free, no API key needed

    jobs = []
    if use_sepia:
        jobs.append((
            _make_cache_key('videos_sepia', query, '', page, safe_search, lang, date),
            partial(_sepia_videos, query, page, safe_search, lang, date),
            CACHE_TTL,
        ))
    if use_brave:
        jobs.append((
            _make_cache_key('videos_brave', query, '', page, safe_search, lang, date),
            partial(_brave_videos, query, page, safe_search, lang, date),
            BRAVE_CACHE_TTL,
        ))

    return interleave([r for r in cache_or_fetch_many(jobs) if r])


def _sepia_videos(query, page, safe_search, lang, date):
    params = {
        'search': query,
        'start': (page - 1) * RESULTS_PER_PAGE,
        'count': RESULTS_PER_PAGE,
        'sort': '-match',
        # PeerTube's nsfw parameter: 'both' allows all, 'false' filters.
        'nsfw': 'both' if safe_search == 'off' else 'false',
    }
    if lang in _SEPIA_LANGUAGES:
        params['languageOneOf[]'] = lang
    # PeerTube has no freshness codes; it filters by an absolute publish date,
    # so translate the chosen window into a "published after" startDate.
    if date in _FRESHNESS_DELTA:
        params['startDate'] = (datetime.now(UTC) - _FRESHNESS_DELTA[date]).isoformat()
    data = _sepia_request(params)
    if data is None:
        return None
    return [_normalize_sepia_video(video) for video in data.get('data', [])]


def _brave_videos(query, page, safe_search, lang, date):
    params = {'q': query, 'count': RESULTS_PER_PAGE, 'offset': page - 1, 'safesearch': brave_safesearch(safe_search)}
    if lang:
        params['search_lang'] = lang
        if lang in LANG_COUNTRY:
            params['country'] = LANG_COUNTRY[lang]
    if date in _FRESHNESS:
        params['freshness'] = _FRESHNESS[date]
    data = _brave_request('/videos/search', params)
    if data is None:
        return None
    return [_normalize_brave_video(video) for video in data.get('results', [])]


def _normalize_sepia_video(video):
    """Map a raw PeerTube video onto the shared video-result shape."""
    url = video.get('url', '')
    return {
        'url': url,
        'title': video.get('name', ''),
        'meta_url': {'hostname': url.split('/')[2] if url else ''},
        'thumbnail': {'src': video.get('thumbnailUrl', '')},
        'video': {'duration': _format_duration(video.get('duration', 0))},
        'age': _format_age(video.get('publishedAt', '')),
        'source': _('PeerTube'),
    }


def _normalize_brave_video(video):
    """Map a raw Brave video result onto the same shape Sepia's resultsi."""
    meta = video.get('meta_url') or {}
    thumbnail = video.get('thumbnail') or {}
    return {
        'url': video.get('url', ''),
        'title': video.get('title', ''),
        'meta_url': {'hostname': meta.get('hostname', '')},
        'thumbnail': {'src': thumbnail.get('src', '')},
        'video': {'duration': (video.get('video') or {}).get('duration', '')},
        'age': video.get('age', ''),
    }


def _format_duration(seconds):
    """Convert seconds to MM:SS or HH:MM:SS format."""
    if not seconds:
        return ''
    minutes, secs = divmod(int(seconds), 60)
    hours, mins = divmod(minutes, 60)
    if hours:
        return f'{hours}:{mins:02d}:{secs:02d}'
    return f'{minutes}:{secs:02d}'


def _format_age(iso_date):
    """Convert ISO date to a full date string (e.g. 'April 22, 2024'),"""
    if not iso_date:
        return ''
    try:
        return datetime.fromisoformat(iso_date).strftime('%B %d, %Y')
    except (TypeError, ValueError):
        return ''
