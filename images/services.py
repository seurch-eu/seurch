import logging
import re
from functools import partial

from django.conf import settings

from search.cache import (
    BRAVE_CACHE_TTL,
    PIXABAY_CACHE_TTL,
    _make_cache_key,
    cache_or_fetch_many,
)
from search.clients import (
    LANG_COUNTRY,
    RESULTS_PER_PAGE,
    _brave_request,
    _pixabay_request,
    brave_safesearch,
    interleave,
    query_label,
    selected_engines,
)

logger = logging.getLogger(__name__)


def fetch_images(query, engine, page=1, safe_search='on', lang='', pixabay_enabled=True):
    # Image sources are blended. Brave backs the brave/all engines; Pixabay is
    # mixed in whenever its key is set and the user hasn't disabled it, and is
    # the sole image source for the web-only engines (Mojeek, Marginalia,
    # Staan), which have no image search of their own.
    #
    # There is no time/date filter here: neither provider supports one, the
    # Brave image endpoint has no `freshness` parameter and Pixabay has no date
    # filter, so the time-range control is hidden on the images tab (see the
    # `results` view).
    #
    # Each provider's slice is cached independently (search.cache.
    # cache_or_fetch_many), Brave under the short-lived `BRAVE_CACHE_TTL` (its
    # terms permit only the transient storage required for operation) and
    # Pixabay under `PIXABAY_CACHE_TTL` (its terms require results be kept at
    # least 24 hours).
    use_brave = 'brave' in selected_engines(engine) and bool(settings.BRAVE_API_KEY)
    use_pixabay = pixabay_enabled and bool(settings.PIXABAY_API_KEY)

    if not (use_brave or use_pixabay):
        return []

    jobs = []
    if use_brave:
        jobs.append((
            _make_cache_key('images_brave', query, '', page, safe_search, lang, ''),
            partial(_brave_images, query, page, safe_search, lang),
            BRAVE_CACHE_TTL,
        ))
    if use_pixabay:
        jobs.append((
            _make_cache_key('images_pixabay', query, '', page, safe_search, lang, ''),
            partial(_pixabay_images, query, page, safe_search, lang),
            PIXABAY_CACHE_TTL,
        ))

    return interleave([r for r in cache_or_fetch_many(jobs) if r])


def _brave_images(query, page, safe_search, lang):
    # The image endpoint has no `offset` parameter, so paginate by over-fetching
    # and slicing the requested page out locally. `brave_safesearch` maps our
    # on/off preference to `off`/`strict` (the endpoint rejects `moderate`).
    count = min(page * RESULTS_PER_PAGE, 200)
    params = {
        'q': query,
        'count': count,
        'safesearch': brave_safesearch(safe_search),
    }
    if lang:
        params['search_lang'] = lang
        if lang in LANG_COUNTRY:
            params['country'] = LANG_COUNTRY[lang]
    data = _brave_request('/images/search', params)
    if data is None:
        return None
    return data.get('results', [])[(page - 1) * RESULTS_PER_PAGE:page * RESULTS_PER_PAGE]


def _pixabay_images(query, page, safe_search, lang):
    logger.info('images querying pixabay q=%s page=%d',
                query_label(query), page)
    params = {
        'q': query,
        'page': page,
        'per_page': RESULTS_PER_PAGE,
        'image_type': 'all',
        # Pixabay's safesearch is a boolean string; mirror Brave's off/strict split.
        'safesearch': 'false' if safe_search == 'off' else 'true',
    }
    # Pixabay accepts the same ISO-639-1 codes the app uses for languages.
    if lang in LANG_COUNTRY:
        params['lang'] = lang
    data = _pixabay_request(params)
    if data is None:
        return None
    return [_normalize_pixabay(hit) for hit in data.get('hits', [])]


def _normalize_pixabay(hit):
    """Map a Pixabay `hit` onto the shape the image template expects
    (`url`, `title`, `source`, `thumbnail.src`). `source` is shown as the
    on-image badge and the figure links back to `url` (the image's Pixabay
    page), satisfying Pixabay's API attribution terms."""
    return {
        'title': hit.get('tags', ''),
        'url': hit.get('pageURL', ''),
        'source': 'Pixabay',
        'thumbnail': {'src': hit.get('webformatURL') or hit.get('previewURL', '')},
    }


# Image captions usually end with the site/source they came from
# ("Golden Retriever - American Kennel Club", "Eiffel Tower | Britannica").
# Drop that trailing segment so the search seed is the subject itself, not
# the website. Only the final separator-delimited chunk is removed, and only a
# short one, so multi-word subjects ("New York - Brooklyn Bridge") survive.
_SOURCE_SUFFIX_RE = re.compile(r'\s+[|·–—-]\s+[^|·–—-]{1,40}$')


def _clean_caption(title):
    """Reduce an image caption to a search-friendly subject phrase."""
    collapsed = ' '.join((title or '').split())
    trimmed = _SOURCE_SUFFIX_RE.sub('', collapsed).strip()
    return trimmed if len(trimmed) >= 2 else collapsed


def fetch_similar_images(title, query='', *, engine, safe_search='on', lang='',
                         pixabay_enabled=True, exclude_url=''):
    """Images similar to the one just opened, an image search seeded with its caption."""
    seed = _clean_caption(title) or ' '.join(query.split())
    if not seed:
        return '', []
    results = fetch_images(
        seed, engine, page=1, safe_search=safe_search, lang=lang,
        pixabay_enabled=pixabay_enabled,
    )
    if exclude_url:
        results = [r for r in results if r.get('url') != exclude_url]
    return seed, results
