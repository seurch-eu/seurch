import logging
from datetime import UTC, datetime
from functools import partial
from urllib.parse import urlparse

from django.conf import settings

from search.cache import BRAVE_CACHE_TTL, _make_cache_key, cache_or_fetch_many
from search.clients import (
    _FRESHNESS,
    _FRESHNESS_DELTA,
    LANG_COUNTRY,
    RESULTS_PER_PAGE,
    _brave_request,
    _worldnews_request,
    brave_safesearch,
    interleave,
    query_label,
    selected_engines,
)

logger = logging.getLogger(__name__)


def fetch_news(query, engine, page=1, safe_search='on', lang='', date='', worldnews_enabled=True):
    # News sources are blended. Brave backs the brave/all engines; World News
    # API is mixed in whenever its key is set and the user hasn't disabled it,
    # and is the sole news source for the web-only engines (Mojeek, Marginalia,
    # Staan), which have no news search of their own.
    #
    # Brave's slice is cached, but only transiently (search.cache.
    # cache_or_fetch_many, BRAVE_CACHE_TTL): its terms permit only the storage
    # required for operation. World News API's terms grant no caching
    # permission at all, so its slice always carries no cache key and is
    # fetched live on every call.
    use_brave = 'brave' in selected_engines(engine) and bool(settings.BRAVE_API_KEY)
    use_worldnews = worldnews_enabled and bool(settings.WORLDNEWS_API_KEY)

    if not (use_brave or use_worldnews):
        return []

    jobs = []
    if use_brave:
        jobs.append((
            _make_cache_key('news_brave', query, '', page, safe_search, lang, date),
            partial(_brave_news, query, page, safe_search, lang, date),
            BRAVE_CACHE_TTL,
        ))
    if use_worldnews:
        jobs.append((None, partial(_worldnews_news, query, page, lang, date), None))

    return interleave([r for r in cache_or_fetch_many(jobs) if r])


def _brave_news(query, page, safe_search, lang, date):
    # Brave's `offset` is a 0-based page index (max 9), like fetch_web, not a
    # result count. Using (page-1)*count overshoots the cap and breaks page 2+.
    params = {'q': query, 'count': RESULTS_PER_PAGE, 'offset': page - 1, 'safesearch': brave_safesearch(safe_search)}
    if lang:
        params['search_lang'] = lang
        if lang in LANG_COUNTRY:
            params['country'] = LANG_COUNTRY[lang]
    if date in _FRESHNESS:
        params['freshness'] = _FRESHNESS[date]
    data = _brave_request('/news/search', params)
    if data is None:
        return None
    return data.get('results', [])


def _worldnews_news(query, page, lang, date):
    # World News paginates by an absolute result offset + count (not a page
    # index). It has no safe-search control of its own, so adult filtering is
    # left to Brave on the blended tab.
    logger.info('news querying worldnews q=%s page=%d',
                query_label(query), page)
    params = {
        'text': query,
        'number': RESULTS_PER_PAGE,
        'offset': (page - 1) * RESULTS_PER_PAGE,
        'sort': 'publish-time',
        'sort-direction': 'DESC',
    }
    # World News accepts the same ISO-639-1 codes the app uses for languages.
    if lang in LANG_COUNTRY:
        params['language'] = lang
    if date in _FRESHNESS_DELTA:
        earliest = datetime.now(UTC) - _FRESHNESS_DELTA[date]
        params['earliest-publish-date'] = earliest.strftime('%Y-%m-%d %H:%M:%S')
    data = _worldnews_request(params)
    if data is None:
        return None
    return [_normalize_worldnews(item) for item in data.get('news', [])]


def _normalize_worldnews(item):
    """Map a World News API article onto the shape the news template expects
    (`url`, `title`, `description`, `meta_url.hostname`, `thumbnail.src`, `age`,
    `source`) so the template stays provider-agnostic, exactly as Pixabay hits
    are normalised onto the Brave image shape. `source` is the provider badge
    shown on each result."""
    url = item.get('url', '')
    return {
        'url': url,
        'title': item.get('title', ''),
        'description': item.get('summary') or item.get('text', ''),
        'meta_url': {'hostname': _hostname(url)},
        'thumbnail': {'src': item.get('image', '')},
        'age': _relative_age(item.get('publish_date', '')),
        'source': 'World News API',
    }


def _hostname(url):
    """Bare host for a URL (leading 'www.' dropped), or '' if unparseable."""
    host = (urlparse(url).hostname or '') if url else ''
    return host.removeprefix('www.')


def _relative_age(publish_date):
    """Human 'N days ago' string from a World News publish date.

    World News stamps articles as 'YYYY-MM-DD HH:MM:SS' in UTC; an ISO 'T'
    separator and trailing 'Z' are tolerated too. Returns '' when absent or
    unparseable, so the template simply omits the age."""
    if not publish_date:
        return ''
    try:
        cleaned = publish_date.strip().replace('Z', '').replace('T', ' ')
        published = datetime.strptime(cleaned[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return ''
    delta = datetime.now(UTC) - published
    if delta.days > 365:
        return f'{delta.days // 365} years ago'
    if delta.days > 30:
        return f'{delta.days // 30} months ago'
    if delta.days > 0:
        return f'{delta.days} days ago'
    if delta.seconds > 3600:
        return f'{delta.seconds // 3600} hours ago'
    if delta.seconds > 60:
        return f'{delta.seconds // 60} minutes ago'
    return 'just now'
