import re
from functools import partial
from urllib.parse import urlparse

from django.conf import settings

from search.cache import (
    BRAVE_CACHE_TTL,
    CACHE_TTL,
    _make_cache_key,
    cache_or_fetch,
    cache_or_fetch_many,
)
from search.clients import (
    _FRESHNESS,
    LANG_COUNTRY,
    RESULTS_PER_PAGE,
    STAAN_MAX_OFFSET,
    STAAN_MAX_QUERY_LEN,
    _brave_request,
    _marginalia_request,
    _mojeek_request,
    _staan_request,
    brave_safesearch,
    selected_engines,
    staan_market,
)


def _norm_brave(result):
    meta = result.get('meta_url') or {}
    netloc = meta.get('netloc', '')
    path = meta.get('path', '')
    display_url = (netloc + ' ' + path).strip() if path else netloc
    return {
        'title': result.get('title', ''),
        'url': result.get('url', ''),
        'description': result.get('description', ''),
        'display_url': display_url,
        'favicon_url': (result.get('profile') or {}).get('img', ''),
        'sitelinks': (result.get('deep_results') or {}).get('buttons', []),
        'age': result.get('age', ''),
        'source': 'brave',
    }


def _norm_mojeek(result):
    url = result.get('url', '')
    try:
        display_url = urlparse(url).netloc
    except Exception:
        display_url = url
    return {
        'title': result.get('title', ''),
        'url': url,
        'description': result.get('desc', ''),
        'display_url': display_url,
        'favicon_url': '',
        'sitelinks': [],
        'age': '',
        'source': 'mojeek',
    }


def _norm_marginalia(result):
    url = result.get('url', '')
    try:
        display_url = urlparse(url).netloc
    except Exception:
        display_url = url
    return {
        'title': result.get('title', ''),
        'url': url,
        'description': result.get('description', ''),
        'display_url': display_url,
        'favicon_url': '',
        'sitelinks': [],
        'age': '',
        'source': 'marginalia',
    }


def _norm_staan(result):
    """Normalise one Staan document onto the shared web-result shape."""
    return {
        'title': result.get('title', ''),
        'url': result.get('url', ''),
        'description': result.get('snippet', ''),
        'display_url': result.get('display_url') or result.get('hostname', ''),
        'favicon_url': result.get('favicon_url', ''),
        'sitelinks': [],
        'age': '',
        'source': 'staan',
    }


# A query word must be at least this long to be highlighted, so single letters
# and stray punctuation aren't wrapped throughout a snippet.
_MIN_TERM_LEN = 2

# Markup that must survive highlighting intact, an HTML entity (&amp;, &#x27;)
# or a tag. Term matching only runs on the plain-text spans between these, so a
# query term can never be wrapped inside an entity (which would corrupt it).
_MARKUP_RE = re.compile(r'&#?\w+;|<[^>]*>')

# An opening <strong> tag, its presence means the engine already highlighted
# the matched terms, so we leave that snippet alone instead of wrapping twice.
_HAS_STRONG_RE = re.compile(r'<\s*strong', re.IGNORECASE)


def _query_terms(query):
    """Distinct query words (≥ ``_MIN_TERM_LEN`` chars) worth highlighting."""
    seen = set()
    terms = []
    for word in re.findall(r'\w+', query or ''):
        low = word.lower()
        if len(word) >= _MIN_TERM_LEN and low not in seen:
            seen.add(low)
            terms.append(word)
    return terms


def _highlight(text, query):
    """Wrap whole-word occurrences of the query's terms in ``<strong>``."""
    if not text or _HAS_STRONG_RE.search(text):
        return text
    terms = _query_terms(query)
    if not terms:
        return text
    alternatives = '|'.join(re.escape(t) for t in terms)
    pattern = re.compile(rf'\b(?:{alternatives})\b', re.IGNORECASE)

    def _wrap(span):
        return pattern.sub(lambda m: f'<strong>{m.group(0)}</strong>', span)

    out = []
    pos = 0
    for mk in _MARKUP_RE.finditer(text):
        out.append(_wrap(text[pos:mk.start()]))
        out.append(mk.group(0))
        pos = mk.end()
    out.append(_wrap(text[pos:]))
    return ''.join(out)


_SOURCE_LABELS = {
    'brave': 'Brave',
    'mojeek': 'Mojeek',
    'marginalia': 'Marginalia',
    'staan': 'Staan',
}


# Reciprocal Rank Fusion damping constant.
_RRF_K = 60


def _merge_web(per_engine_results):
    """Fuse ranked result lists from several engines into one ranking.

    `per_engine_results` is a list of `(source_name, [normalised_results])` tuples
    in priority order. Duplicate URLs are collapsed into a single entry, and the
    combined list is ordered by **Reciprocal Rank Fusion**: each entry scores
    ``Σ 1/(_RRF_K + rank)`` over the engines that returned it. Because the terms
    add up, a result several engines agree on rises above one that any single
    engine ranked highly, so cross-engine consensus earns a better place.
    Entries with equal scores keep their first-seen order (the sort is stable).
    """
    seen = {}
    order = []  # unique entries in first-seen order, the stable-sort tiebreaker
    for source, items in per_engine_results:
        for rank, r in enumerate(items):
            entry = seen.get(r['url'])
            contribution = 1.0 / (_RRF_K + rank)
            if entry is None:
                r['_sources'] = [source]
                r['_score'] = contribution
                seen[r['url']] = r
                order.append(r)
            else:
                if source not in entry['_sources']:
                    entry['_sources'].append(source)
                entry['_score'] += contribution

    order.sort(key=lambda r: r['_score'], reverse=True)

    merged = []
    for r in order:
        sources = r.pop('_sources', [r['source']])
        r.pop('_score', None)
        if len(sources) == 1:
            r['source'] = sources[0]
        elif len(sources) == 2:
            r['source'] = 'both'
        else:
            r['source'] = 'all'
        r['source_label'] = ' · '.join(
            _SOURCE_LABELS.get(s, s) for s in sources if s != 'brave'
        )
        merged.append(r)
    return merged


def fetch_suggestions(query):
    """Return up to 7 query suggestions via Brave's autocomplete (suggest) endpoint."""
    api_key = settings.BRAVE_SUGGEST_API_KEY
    if not api_key:
        return []

    def fetch():
        data = _brave_request('/suggest/search', {'q': query}, api_key=api_key)
        if not isinstance(data, dict):
            # None is an upstream failure (retried next time); an unexpected
            # shape is a successful but useless answer, cached as "nothing".
            return None if data is None else []
        results = data.get('results')
        if not isinstance(results, list):
            return []
        return [r['query'] for r in results if isinstance(r, dict) and r.get('query')][:7]

    key = _make_cache_key('suggest', query, '', 1, '', '', '')
    return cache_or_fetch(key, fetch, ttl=BRAVE_CACHE_TTL) or []


def _fetch_brave_web(query, page, safe_search, lang, date):
    """Brave's slice of the web results (normalised, highlighted) + any
    spelling correction it suggested, or ``None`` on an upstream error."""
    params = {'q': query, 'count': RESULTS_PER_PAGE, 'offset': page - 1, 'safesearch': brave_safesearch(safe_search)}
    if lang:
        params['search_lang'] = lang
        if lang in LANG_COUNTRY:
            params['country'] = LANG_COUNTRY[lang]
    if date in _FRESHNESS:
        params['freshness'] = _FRESHNESS[date]
    data = _brave_request('/web/search', params)
    if data is None:
        return None

    correction = ''
    altered = (data.get('query') or {}).get('altered', '')
    if altered and altered.lower() != query.lower():
        correction = altered
    norm = [_norm_brave(r) for r in data.get('web', {}).get('results', [])]
    for r in norm:
        r['description'] = _highlight(r['description'], query)
    return {'results': norm, 'correction': correction}


def _fetch_mojeek_web(query, page, lang):
    """Mojeek's slice of the web results (normalised, highlighted), or ``None``
    on an upstream error."""
    params = {'q': query, 'results': RESULTS_PER_PAGE, 's': (page - 1) * RESULTS_PER_PAGE + 1}
    if lang:
        params['language'] = lang.upper()
    data = _mojeek_request(params)
    if data is None:
        return None
    norm = [_norm_mojeek(r) for r in (data.get('response') or {}).get('results', [])]
    for r in norm:
        r['description'] = _highlight(r['description'], query)
    return norm


def _fetch_marginalia_web(query, page, safe_search, lang):
    """Marginalia's slice of the web results (normalised, highlighted), or
    ``None`` on an upstream error."""
    params = {
        'query': query,
        'count': RESULTS_PER_PAGE,
        'page': page,
        'nsfw': 0 if safe_search == 'off' else 1,
    }
    data = _marginalia_request(params)
    if data is None:
        return None
    norm = [_norm_marginalia(r) for r in data.get('results', [])]
    for r in norm:
        r['description'] = _highlight(r['description'], query)
    return norm


def _fetch_staan_web(query, page, lang):
    """Staan's slice of the web results (normalised, highlighted), or ``None``
    on an upstream error."""
    params = {'q': query, 'offset': (page - 1) * RESULTS_PER_PAGE}
    market = staan_market(lang)
    if market:
        params['market'] = market
    data = _staan_request(params)
    if data is None:
        return None
    norm = [_norm_staan(r) for r in (data.get('web') or {}).get('results', [])]
    for r in norm:
        r['description'] = _highlight(r['description'], query)
    return norm


def fetch_web(query, engine, page=1, safe_search='on', lang='', date=''):
    """Return (results, correction) where correction is a suggested query or ''."""
    selected = selected_engines(engine)
    use_brave = 'brave' in selected
    use_mojeek = 'mojeek' in selected
    use_marginalia = 'marginalia' in selected
    use_staan = 'staan' in selected

    brave_available = use_brave and bool(settings.BRAVE_API_KEY)
    mojeek_available = use_mojeek and bool(settings.MOJEEK_API_KEY)
    marginalia_available = use_marginalia and bool(settings.MARGINALIA_API_KEY)
    staan_available = (
        use_staan
        and bool(settings.STAAN_API_KEY)
        and (page - 1) * RESULTS_PER_PAGE <= STAAN_MAX_OFFSET
        and len(query) <= STAAN_MAX_QUERY_LEN
    )

    if not (brave_available or mojeek_available or marginalia_available or staan_available):
        return [], ''

    names = []
    jobs = []
    if brave_available:
        names.append('brave')
        jobs.append((
            _make_cache_key('web_brave', query, '', page, safe_search, lang, date),
            partial(_fetch_brave_web, query, page, safe_search, lang, date),
            BRAVE_CACHE_TTL,
        ))
    if mojeek_available:
        names.append('mojeek')
        jobs.append((
            _make_cache_key('web_mojeek', query, '', page, '', lang, ''),
            partial(_fetch_mojeek_web, query, page, lang),
            CACHE_TTL,
        ))
    if marginalia_available:
        names.append('marginalia')
        jobs.append((
            _make_cache_key('web_marginalia', query, '', page, safe_search, lang, ''),
            partial(_fetch_marginalia_web, query, page, safe_search, lang),
            CACHE_TTL,
        ))
    if staan_available:
        names.append('staan')
        jobs.append((
            _make_cache_key('web_staan', query, '', page, '', lang, ''),
            partial(_fetch_staan_web, query, page, lang),
            CACHE_TTL,
        ))

    correction = ''
    per_engine = []
    for name, raw in zip(names, cache_or_fetch_many(jobs)):
        if raw is None:
            continue
        if name == 'brave':
            correction = raw['correction']
            items = raw['results']
        else:
            items = raw
        if items:
            per_engine.append((name, items))

    results = _merge_web(per_engine)
    return results, correction
