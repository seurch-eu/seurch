"""Geocoding and map-related services using OpenStreetMap Nominatim.

Nominatim is the free, keyless geocoder behind OpenStreetMap. Its usage
policy asks for a descriptive User-Agent and modest request rates, so every
lookup is cached (CACHE_TTL) and gated behind `looks_like_place` on the web
tab. Maps are rendered with OSM's official `/export/embed.html` iframe, which
needs no API key and keeps map tiles as the only third-party request.
"""

from urllib.parse import quote

from cards import wikidata
from cards.relevance import (
    _MAP_DOMAINS,
    _PLACE_QUERY_PHRASES,
    _PLACE_WIKI_KEYWORDS,
    _STREET_SUFFIXES,
    _contains_word,
    _is_travel_query,
)
from search.cache import _make_cache_key, cache_or_fetch
from search.clients import USER_AGENT, _provider_request

NOMINATIM_API_BASE = 'https://nominatim.openstreetmap.org'

# World view for the Maps tab when there is no place to centre on: no query yet
# (the tab was switched to from another one) or a query nothing geocoded.
WORLD_EMBED_URL = (
    'https://www.openstreetmap.org/export/embed.html'
    '?bbox=-180%2C-60%2C180%2C75'
    '&layer=mapnik'
)
WORLD_OSM_URL = 'https://www.openstreetmap.org/#map=2/25/0'


def _embed_url(minlon, minlat, maxlon, maxlat, mlat, mlon):
    """Build an OpenStreetMap `/export/embed.html` URL with a centred marker."""
    return (
        'https://www.openstreetmap.org/export/embed.html'
        f'?bbox={minlon}%2C{minlat}%2C{maxlon}%2C{maxlat}'
        '&layer=mapnik'
        f'&marker={mlat}%2C{mlon}'
    )


def _mini_bbox(lat, lon, half=0.008):
    """A small square bounding box (~street level) centred on a point."""
    return (
        round(lon - half, 6), round(lat - half, 6),
        round(lon + half, 6), round(lat + half, 6),
    )


def _geo_uri(lat, lon, label=''):
    """Build a `geo:` URI for a point, with an optional labelled marker.

    `geo:` hands the coordinates off to the user's default map app (RFC 5870),
    so the choice of app stays with the user rather than being hardcoded.
    """
    uri = f'geo:{lat},{lon}?q={lat},{lon}'
    if label:
        uri += f'({quote(label)})'
    return uri


def _osm_directions_url(lat, lon):
    """OpenStreetMap web routing URL with the destination pre-filled."""
    return f'https://www.openstreetmap.org/directions?to={lat}%2C{lon}'


def _build_place(raw):
    """Normalise one Nominatim result into a template-ready place dict, or None."""
    try:
        lat = round(float(raw['lat']), 6)
        lon = round(float(raw['lon']), 6)
    except (KeyError, TypeError, ValueError):
        return None

    bb = raw.get('boundingbox') or []
    minlon = minlat = maxlon = maxlat = None
    if len(bb) == 4:
        try:
            # Nominatim order: [south_lat, north_lat, west_lon, east_lon]
            minlat, maxlat, minlon, maxlon = (round(float(x), 6) for x in bb)
        except (TypeError, ValueError):
            minlon = None
    # Fall back to / clamp degenerate boxes so the map isn't absurdly zoomed.
    if minlon is None or maxlon - minlon < 1e-4 or maxlat - minlat < 1e-4:
        minlon, minlat, maxlon, maxlat = _mini_bbox(lat, lon, 0.003)

    display_name = raw.get('display_name', '')
    name = raw.get('name') or (display_name.split(',')[0] if display_name else '')

    return {
        'name': name,
        'display_name': display_name,
        'lat': lat,
        'lon': lon,
        'category': raw.get('class', '') or raw.get('category', ''),
        'type': raw.get('type', ''),
        'addresstype': raw.get('addresstype', ''),
        'importance': raw.get('importance', 0),
        'embed_url': _embed_url(minlon, minlat, maxlon, maxlat, lat, lon),
        'mini_embed_url': _embed_url(*_mini_bbox(lat, lon), lat, lon),
        'osm_url': f'https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=16/{lat}/{lon}',
        # Directions: OpenStreetMap web routing is the desktop / no-JS default;
        # on mobile the client swaps in `geo_uri` to open the user's default
        # map app (see results.html).
        'directions_url': _osm_directions_url(lat, lon),
        'geo_uri': _geo_uri(lat, lon, name),
    }


def place_from_coords(lat, lon, name='', display_name=''):
    """Build a place dict directly from coordinates (e.g. from a TripAdvisor card)."""
    return _build_place({
        'lat': lat,
        'lon': lon,
        'name': name,
        'display_name': display_name or name,
    })


def looks_like_place(query, wikipedia_card=None, web_results=None):
    """Heuristic: does this query look like an address or geographic place?

    Keeps geocoding targeted so ordinary informational searches don't hit
    Nominatim.
    """
    q_lower = query.lower()

    if _contains_word(q_lower, _PLACE_QUERY_PHRASES):
        return True

    # Street address: a street-type token alongside a house number.
    tokens = q_lower.replace(',', ' ').split()
    if any(any(ch.isdigit() for ch in t) for t in tokens) and \
            any(t.strip('.') in _STREET_SUFFIXES for t in tokens):
        return True

    tags = wikidata.entity_tags((wikipedia_card or {}).get('wikibase_item'))
    if tags:
        if tags & {'place', 'travel_place'}:
            return True
        if tags & {'person', 'movie_tv'}:
            return False

    if wikipedia_card:
        desc = (wikipedia_card.get('description') or '').lower()
        if _contains_word(desc, _PLACE_WIKI_KEYWORDS):
            return True

    for result in (web_results or [])[:8]:
        url = result.get('url', '')
        if any(domain in url for domain in _MAP_DOMAINS):
            return True

    # Restaurants / hotels / attractions are places too.
    return _is_travel_query(query, web_results, wikipedia_card, tags)


def _nominatim_request(params):
    """Call the Nominatim search endpoint; return the parsed JSON list or None."""
    return _provider_request(
        'openstreetmap', f'{NOMINATIM_API_BASE}/search', params,
        {'User-Agent': USER_AGENT}, timeout=6,
    )


def _geocode(query, limit, lang):
    """Places for a query, or ``None`` when the lookup failed, so a failure is
    passed back to the caller rather than cached as "no places"."""
    params = {'q': query, 'format': 'jsonv2', 'limit': limit}
    if lang:
        params['accept-language'] = lang
    data = _nominatim_request(params)
    if data is None:
        return None
    return [place for place in map(_build_place, data[:limit]) if place]


def fetch_geocode(query, limit=1, lang=''):
    """Geocode a query via OpenStreetMap Nominatim. Returns a list of place dicts."""
    query = (query or '').strip()
    if not query:
        return []
    key = _make_cache_key('geocode', query, '', limit, '', lang, '')
    return cache_or_fetch(key, lambda: _geocode(query, limit, lang)) or []
