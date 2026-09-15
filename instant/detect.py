"""Instant-answer dispatcher.

``detect(query, request)`` runs the query through an ordered list of handlers
and returns the first match (a context dict tagged with ``type``) or ``None``.

Ordering matters: specific keyword tools run first, then the
amount-plus-keyword converters (currency → units → base), and finally the math
fallback. Every handler is cheap and offline except weather and currency,
which only reach the network once their trigger words appear.
"""

import logging

from . import currency, tools, weather

logger = logging.getLogger(__name__)

MAX_QUERY_LEN = 280


def _currency(query, request):
    return currency.answer(query)


def _weather(query, request):
    lang = ''
    if request is not None:
        lang = request.GET.get('lang', '')
    return weather.answer(query, lang or 'en')


# Ordered: first match wins.
HANDLERS = [
    tools.ip_answer,
    tools.qr_answer,
    tools.uuid_answer,
    tools.password_answer,
    tools.hash_answer,
    tools.encode_answer,
    tools.json_answer,
    tools.regex_answer,
    tools.color_answer,
    tools.worldclock_answer,
    tools.timestamp_answer,
    _weather,
    tools.timer_answer,
    tools.coin_answer,
    tools.dice_answer,
    tools.random_answer,
    tools.port_answer,
    tools.http_answer,
    _currency,
    tools.unit_answer,
    tools.base_answer,
    tools.math_answer,
]


def detect(query, request=None, weather_enabled=True):
    """Return an instant-answer context dict for *query*, or None.

    When *weather_enabled* is False the Open-Meteo weather handler is skipped
    entirely (no detection, no network call), letting the next handler, or no
    answer, take its place.
    """
    query = (query or '').strip()
    if not query or len(query) > MAX_QUERY_LEN:
        return None
    for handler in HANDLERS:
        if handler is _weather and not weather_enabled:
            continue
        try:
            result = handler(query, request)
        except Exception as exc:  # one bad handler must never break the page
            logger.warning('instant handler %s failed: %s', getattr(handler, '__name__', handler), exc)
            continue
        if result:
            result.setdefault('query', query)
            return result
    return None
