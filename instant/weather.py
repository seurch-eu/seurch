"""Weather instant answer via Open-Meteo (free, open-source, no API key).

Two calls: the geocoding API resolves a place name to coordinates, then the
forecast API returns current conditions plus a 7-day outlook. Both are free
and keyless. Results are cached for one hour per place.
"""

import logging
import re
import time
from datetime import timedelta

import httpx

from . import cache
from . import keywords as kw
from .data import weather_label
from .keywords import alt

logger = logging.getLogger(__name__)

GEOCODE_URL = 'https://geocoding-api.open-meteo.com/v1/search'
FORECAST_URL = 'https://api.open-meteo.com/v1/forecast'
# One hour, matching the shared search cache (search.cache.CACHE_TTL).
WEATHER_TTL = timedelta(hours=1)

_COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']


def _compass(degrees):
    try:
        return _COMPASS[round(degrees / 45) % 8]
    except (TypeError, ValueError):
        return ''


def _c_to_f(c):
    return round(c * 9 / 5 + 32)


def _geocode(name, lang='en'):
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=8) as client:
            r = client.get(GEOCODE_URL, params={
                'name': name, 'count': 1, 'language': lang or 'en', 'format': 'json',
            }, headers={'Accept': 'application/json'})
            r.raise_for_status()
            results = r.json().get('results') or []
        if not results:
            return None
        g = results[0]
        logger.debug('open-meteo geocode ok %r (%.2fs)', name, time.monotonic() - t0)
        return {
            'name': g.get('name', name),
            'admin1': g.get('admin1', ''),
            'country': g.get('country', ''),
            'country_code': g.get('country_code', ''),
            'lat': g.get('latitude'),
            'lon': g.get('longitude'),
            'timezone': g.get('timezone', 'auto'),
        }
    except Exception as exc:
        logger.warning('open-meteo geocode error (%.2fs): %s', time.monotonic() - t0, exc)
        return None


def _forecast(lat, lon):
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=8) as client:
            r = client.get(FORECAST_URL, params={
                'latitude': lat, 'longitude': lon,
                'current': 'temperature_2m,relative_humidity_2m,apparent_temperature,'
                           'weather_code,wind_speed_10m,wind_direction_10m,is_day',
                'daily': 'weather_code,temperature_2m_max,temperature_2m_min',
                'timezone': 'auto', 'forecast_days': 7,
            }, headers={'Accept': 'application/json'})
            r.raise_for_status()
            logger.debug('open-meteo forecast ok (%.2fs)', time.monotonic() - t0)
            return r.json()
    except Exception as exc:
        logger.warning('open-meteo forecast error (%.2fs): %s', time.monotonic() - t0, exc)
        return None


def _build_daily(daily):
    import datetime as dt
    out = []
    times = daily.get('time') or []
    codes = daily.get('weather_code') or []
    highs = daily.get('temperature_2m_max') or []
    lows = daily.get('temperature_2m_min') or []
    for i, day in enumerate(times):
        if i >= len(codes):
            break
        _label, emoji = weather_label(codes[i])
        try:
            weekday = dt.date.fromisoformat(day).weekday()
        except ValueError:
            weekday = None
        hi = round(highs[i]) if i < len(highs) and highs[i] is not None else None
        lo = round(lows[i]) if i < len(lows) and lows[i] is not None else None
        out.append({
            # Weekday name and condition text are localised at render time
            # (see instant/templatetags/instant_extras.py); the cache only holds
            # JSON-safe primitives, the WMO code and the weekday index.
            'weekday': weekday,
            'code': codes[i],
            'emoji': emoji,
            'high_c': hi, 'low_c': lo,
            'high_f': _c_to_f(hi) if hi is not None else None,
            'low_f': _c_to_f(lo) if lo is not None else None,
            'is_today': i == 0,
        })
    return out


def get_weather(place, lang='en'):
    """Return a weather card dict for a place name, or None."""
    place = (place or '').strip()
    if not place:
        return None

    key = cache.make_key('weather', place.lower(), lang or 'en')
    cached = cache.get(key)
    if cached is not None:
        return cached

    geo = _geocode(place, lang)
    if not geo or geo['lat'] is None:
        return None

    data = _forecast(geo['lat'], geo['lon'])
    if not data or 'current' not in data:
        return None

    cur = data['current']
    code = cur.get('weather_code', 0)
    _label, emoji = weather_label(code)
    temp_c = round(cur.get('temperature_2m', 0))
    feels_c = round(cur.get('apparent_temperature', 0))

    location = geo['name']
    if geo['admin1'] and geo['admin1'].lower() != geo['name'].lower():
        location = f"{geo['name']}, {geo['admin1']}"

    card = {
        'type': 'weather',
        'location': location,
        'country': geo['country'],
        'emoji': emoji,
        'code': code,
        'temp_c': temp_c,
        'temp_f': _c_to_f(temp_c),
        'feels_c': feels_c,
        'feels_f': _c_to_f(feels_c),
        'humidity': cur.get('relative_humidity_2m'),
        'wind_kmh': round(cur.get('wind_speed_10m', 0)),
        'wind_dir': _compass(cur.get('wind_direction_10m', 0)),
        'is_day': bool(cur.get('is_day', 1)),
        'daily': _build_daily(data.get('daily') or {}),
    }
    cache.set(key, card, WEATHER_TTL)
    return card


# ---------------------------------------------------------------------------
# Query parsing, "weather in Paris", "London weather", "forecast Tokyo"
# ---------------------------------------------------------------------------

_WEATHER_WORD_ALT = alt(kw.WEATHER)
# "[article] <weather word> [prep] <place>"  e.g. "el tiempo en madrid", "météo à paris".
_WEATHER_LEAD_RE = re.compile(
    r'^(?:(?:' + alt(kw.ARTICLES) + r')\s+)?(?:' + _WEATHER_WORD_ALT + r')\s+'
    r'(?:(?:' + alt(kw.PREP) + r')\s+)?(.+)$', re.IGNORECASE)
# "<place> <weather word>"  e.g. "london weather", "roma meteo".
_WEATHER_TRAIL_RE = re.compile(r'^(.+?)\s+(?:' + _WEATHER_WORD_ALT + r')$', re.IGNORECASE)
_WEATHER_FILLER_RE = re.compile(
    r"\b(today|now|right now|currently|forecast|aujourd'hui|heute|hoy|oggi|hoje|vandaag)\b", re.IGNORECASE)


def answer(query, lang='en'):
    """Build a weather instant-answer dict, or None."""
    q = query.strip().rstrip('?').strip()
    m = _WEATHER_LEAD_RE.match(q) or _WEATHER_TRAIL_RE.match(q)
    if not m:
        return None
    place = _WEATHER_FILLER_RE.sub('', m.group(1)).strip()
    if not place or len(place) < 2:
        return None
    return get_weather(place, lang)
