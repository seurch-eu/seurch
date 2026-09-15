import logging
import time

import httpx
from django.conf import settings

from cards import wikidata
from cards.cache import cached_card
from cards.relevance import (
    _contains_word,
    _is_travel_query,
    _name_is_relevant,
    _shares_significant_token,
    _significant_tokens,
    _title_match,
)
from search import health

logger = logging.getLogger(__name__)

TA_API_BASE = 'https://terra.tripadvisor.com/api'

_QUERY_MAX_LENGTH = 500

_TA_LOCALES = frozenset({
    'ar', 'ar-EG', 'da-DK', 'de-AT', 'de-CH', 'de-DE', 'el-GR', 'en-AU',
    'en-CA', 'en-HK', 'en-IE', 'en-IN', 'en-MY', 'en-NZ', 'en-PH', 'en-SG',
    'en-UK', 'en-US', 'en-ZA', 'es-AR', 'es-CL', 'es-CO', 'es-ES', 'es-MX',
    'es-PE', 'es-VE', 'fi', 'fr-BE', 'fr-CA', 'fr-CH', 'fr-FR', 'he-IL', 'hu',
    'id-ID', 'it-CH', 'it-IT', 'ja-JP', 'ko-KR', 'nl-BE', 'nl-NL', 'no-NO',
    'pl', 'pt-BR', 'pt-PT', 'ru-RU', 'sv-SE', 'th-TH', 'tr-TR', 'vi-VN', 'zh',
    'zh-CN', 'zh-HK', 'zh-TW',
})

_TA_MARKET = {
    'en': ('en-US', 'US'),
    'fr': ('fr-FR', 'FR'),
    'de': ('de-DE', 'DE'),
    'es': ('es-ES', 'ES'),
    'it': ('it-IT', 'IT'),
    'pt': ('pt-PT', 'PT'),
    'nl': ('nl-NL', 'NL'),
}
_TA_DEFAULT_LANG = 'en'

# Travel keywords mapped to TripAdvisor's search categories
_TA_CATEGORY_KEYWORDS = (
    ('RESTAURANT', frozenset({
        'restaurant', 'restaurants', 'restaurante', 'restaurantes',
        'ristorante', 'ristoranti', 'café', 'cafés', 'cafe', 'cafes', 'caffè',
        'bar', 'bars', 'pub', 'pubs', 'kneipe', 'bistro', 'brasserie',
        'trattoria', 'osteria', 'pizzeria', 'tavern', 'taberna', 'locanda',
        'biergarten', 'where to eat', 'où manger', 'dónde comer',
        'dove mangiare', 'onde comer', 'waar eten', 'wo essen',
    })),
    ('HOTEL', frozenset({
        'hotel', 'hotels', 'hôtel', 'hôtels', 'hostel', 'hostels', 'hostal',
        'ostello', 'auberge', 'auberge de jeunesse', 'inn', 'b&b',
        'bed and breakfast', 'unterkunft', 'pension', 'gasthaus',
        'hébergement', "chambre d'hôtes", 'gîte', 'alojamiento', 'pousada',
        'acomodação', 'herberg', 'verblijf', 'where to stay', 'où dormir',
        'dónde dormir', 'dove dormire', 'onde dormir', 'waar slapen',
        'wo schlafen',
    })),
    ('ATTRACTION', frozenset({
        'museum', 'museums', 'musée', 'musées', 'museo', 'museos', 'musei',
        'museu', 'museus', 'attraction', 'attractions', 'things to do',
        'sightseeing', 'que faire', 'qué hacer', 'cosa fare', 'o que fazer',
        'was tun', 'wat te doen', 'sehenswürdigkeit', 'sehenswürdigkeiten',
        'bezienswaardigheid', 'bezienswaardigheden', 'zoo', 'beach', 'plage',
        'playa', 'spiaggia', 'praia', 'strand', 'spa', 'spas', 'casino',
    })),
)


def _query_category(query):
    """The TripAdvisor search category the query names, or '' when none or
    several do (an ambiguous query is left unfiltered)."""
    q_lower = query.lower()
    matched = [cat for cat, keywords in _TA_CATEGORY_KEYWORDS
               if _contains_word(q_lower, keywords)]
    return matched[0] if len(matched) == 1 else ''


def _locales(lang):
    """Locales to ask for, in priority order, for search language *lang*."""

    locale = _TA_MARKET.get(lang, _TA_MARKET[_TA_DEFAULT_LANG])[0]
    default = _TA_MARKET[_TA_DEFAULT_LANG][0]
    return [locale] if locale == default else [locale, default]


def _country_code(lang):
    """Alpha-2 country to bias the search to, from the search language, or ''
    for *Any language*, where there is nothing to infer and no filter is sent."""
    return _TA_MARKET.get(lang, ('', ''))[1]


def _problem_detail(exc):
    """The `application/problem+json` body behind a failed request, condensed
    for the log, or '' when the failure carried no problem document.
    """
    response = getattr(exc, 'response', None)
    if response is None:
        return ''
    try:
        problem = response.json()
    except Exception:
        return ''
    if not isinstance(problem, dict):
        return ''
    parts = [str(problem[key]) for key in ('title', 'detail') if problem.get(key)]
    parts += [
        f'{field.get("field")}={field.get("rejected_value")!r} ({field.get("message")})'
        for field in problem.get('field_errors') or [] if isinstance(field, dict)
    ]
    if problem.get('trace_id'):
        parts.append(f'trace_id={problem["trace_id"]}')
    return ' | '.join(parts)


def _rating_bubbles(rating):
    """Return a 5-element list of 'full', 'half', or 'empty' for TripAdvisor bubbles."""
    bubbles = []
    r = float(rating)
    for i in range(5):
        if r >= i + 0.75:
            bubbles.append('full')
        elif r >= i + 0.25:
            bubbles.append('half')
        else:
            bubbles.append('empty')
    return bubbles


def _entry_language(entry):
    return (entry.get('language') or '').strip().lower()


def _preferred(entries, locales):
    """The entry of a per-language list that best fits *locales*, or None."""
    entries = [e for e in (entries or []) if isinstance(e, dict)]
    if not entries:
        return None
    wanted = []
    for locale in locales:
        low = locale.lower()
        for candidate in (low, low.split('-')[0]):
            if candidate not in wanted:
                wanted.append(candidate)
    for want in wanted:
        matches = [e for e in entries if _entry_language(e) == want]
        if not matches and '-' not in want:
            # A bare language also accepts the API's regional variants of it.
            matches = [e for e in entries if _entry_language(e).split('-')[0] == want]
        if matches:
            return max(matches, key=lambda e: bool(e.get('primary')))
    return max(entries, key=lambda e: bool(e.get('primary')))


def _text(entries, locales, key='value'):
    """The preferred translation's text out of a per-language list, or ''."""
    return ((_preferred(entries, locales) or {}).get(key) or '').strip()


def _fields(location, locales):
    """A catalog Location's name, formatted address and Geo name, localised."""
    return (
        _text(location.get('names'), locales),
        _text(location.get('addresses'), locales, 'formatted'),
        (location.get('geo') or '').strip(),
    )


def fetch_tripadvisor(query, wikipedia_card=None, web_results=None, lang=''):
    """Return a TripAdvisor card dict for a travel/hospitality query, or None."""
    return cached_card('tripadvisor', query, lang, lambda: _fetch_tripadvisor(
        query, wikipedia_card=wikipedia_card, web_results=web_results, lang=lang,
    ))


def _closest_location(locations, query, references, locales):
    """The hit closest to what was searched, as ``(location, coverage)``.

    Every location is scored on how much of the query its name *and place*
    (address + Geo) cover, then on how closely its name matches the query /
    Wikipedia title, the closest wins, not the first one.
    """
    query_tokens = _significant_tokens(query)
    scored = []
    for i, loc in enumerate(locations):
        name, address, geo = _fields(loc, locales)
        place = ' '.join(p for p in (address, geo) if p)
        name_matches = _name_is_relevant(name, *references)
        place_matches = _shares_significant_token(query, place)
        if not (name_matches or place_matches):
            continue
        covered = query_tokens & (_significant_tokens(name) | _significant_tokens(place))
        # A query of nothing but stop/category words ("best hotel") has nothing
        # to cover, so it counts as covered rather than as the worst possible
        # match; every hit scores alike either way, the ranking is unchanged.
        coverage = len(covered) / len(query_tokens) if query_tokens else 1.0
        closeness = max((_title_match(name, ref) for ref in references), default=0.0)
        scored.append(((coverage, closeness, place_matches, -i), loc))
    if not scored:
        return None, 0.0
    (coverage, *_), chosen = max(scored)
    return chosen, coverage


def _search_catalog(client, query, locales, country_code=''):
    """Catalog Locations matching *query*, in the API's relevance order."""
    params = {'query': query[:_QUERY_MAX_LENGTH], 'locale': locales}
    category = _query_category(query)
    if category:
        params['category'] = category
    if country_code:
        params['country_code'] = country_code
    r = client.get(f'{TA_API_BASE}/catalog/locations/search', params=params)
    r.raise_for_status()
    return [hit['location'] for hit in r.json().get('data', []) if hit.get('location')]


def _fetch_tripadvisor(query, wikipedia_card=None, web_results=None, lang=''):
    """Fetch a TripAdvisor card from the API (uncached)."""
    api_key = settings.TRIPADVISOR_API_KEY
    if not api_key:
        return None

    tags = wikidata.entity_tags((wikipedia_card or {}).get('wikibase_item'))
    if not _is_travel_query(query, web_results, wikipedia_card, tags):
        return None

    locales = _locales(lang)
    references = [r for r in (query, (wikipedia_card or {}).get('title')) if r]
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=8, headers={'X-API-Key': api_key}) as client:
            # The country behind the search language narrows the search to
            # where the searcher most likely means, but it is a hint, not a
            # fact: "central park new york" searched in French must not settle
            # for a Paris café of that name. So the narrowed search only stands
            # when its winner covers the whole query, otherwise the search runs
            # again unfiltered and the wider-covering of the two wins. Neither
            # page is a superset of the other (each is capped at one page of
            # hits), which is why the loser is kept rather than overwritten.
            country_code = _country_code(lang)
            chosen, best_coverage = None, -1.0
            for country in ([country_code, ''] if country_code else ['']):
                locations = _search_catalog(client, query, locales, country_code=country)
                hit, coverage = _closest_location(locations, query, references, locales)
                if hit is not None and coverage > best_coverage:
                    chosen, best_coverage = hit, coverage
                if best_coverage >= 1.0:
                    break
            health.record_ok('tripadvisor')
            if chosen is None:
                logger.debug('tripadvisor drop: no location matches query=%r', query)
                return None

            detail_r = client.get(
                f'{TA_API_BASE}/catalog/locations/{chosen["id"]}',
                params={'locale': locales},
            )
            detail_r.raise_for_status()
            d = detail_r.json()

        name, address, geo = _fields(d, locales)
        place = ' '.join(p for p in (address, geo) if p)

        # Pertinence gate: the place must connect to the query, by name, or (for
        # category queries) by sitting in a place the query names.
        if not (_name_is_relevant(name, *references) or _shares_significant_token(query, place)):
            logger.debug('tripadvisor drop: %r not pertinent to query=%r', name, query)
            return None

        overall = d.get('overall_rating') or {}
        rating = overall.get('rating')
        num_reviews = overall.get('count') or 0
        description = _text(d.get('descriptions'), locales)
        web_url = (((d.get('urls') or {}).get('tripadvisor') or {}).get('main') or '').strip()

        coordinates = d.get('coordinates') or {}
        try:
            latitude = round(float(coordinates['latitude']), 6)
            longitude = round(float(coordinates['longitude']), 6)
        except (KeyError, TypeError, ValueError):
            latitude = longitude = None

        logger.debug('tripadvisor ok name=%r rating=%s reviews=%s (%.2fs)',
                     name, rating, num_reviews, time.monotonic() - t0)
        return {
            'name': name,
            'geo': geo,
            'address': address,
            'rating': float(rating) if rating else None,
            'rating_bubbles': _rating_bubbles(rating) if rating else [],
            'num_reviews': int(num_reviews) if num_reviews else 0,
            'description': description,
            'url': web_url,
            'latitude': latitude,
            'longitude': longitude,
        }
    except Exception as exc:
        problem = _problem_detail(exc)
        error = f'{exc} [{problem}]' if problem else str(exc)
        logger.warning('tripadvisor error (%.2fs): %s', time.monotonic() - t0, error)
        health.record_down('tripadvisor', error)
        return None
