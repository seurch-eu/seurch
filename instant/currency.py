"""Currency exchange rates via Frankfurter (ECB data, free, no API key).

Rates are fetched once and stored in the ``CurrencyRate`` table; subsequent
lookups reuse the row until it is older than 24 h. All rates are stored
relative to a single base (USD) so any pair can be derived locally:

    amount_b = amount_a * rate[b] / rate[a]

If the upstream feed is unreachable a stale row is still served (better a
day-old rate than none); only a completely cold cache yields ``None``.
"""

import logging
import re
import time
from datetime import timedelta

import httpx
from django.utils import timezone

from . import keywords as kw
from .data import CURRENCIES, CURRENCY_SYMBOLS, CURRENCY_WORDS
from .keywords import alt

logger = logging.getLogger(__name__)

FRANKFURTER_BASE = 'https://api.frankfurter.dev/v1'
BASE_CURRENCY = 'USD'
RATES_TTL = timedelta(hours=24)
# Shown as quick chips under the converter.
POPULAR = ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD', 'CNY', 'INR']


def _fetch_rates():
    """Fetch the latest rate table from Frankfurter, or None on failure."""
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=10) as client:
            r = client.get(
                f'{FRANKFURTER_BASE}/latest',
                params={'base': BASE_CURRENCY},
                headers={'Accept': 'application/json'},
            )
            r.raise_for_status()
            data = r.json()
        rates = {k: float(v) for k, v in (data.get('rates') or {}).items()}
        rates[BASE_CURRENCY] = 1.0
        logger.debug('frankfurter ok %d rates (%.2fs)', len(rates), time.monotonic() - t0)
        return {'rates': rates, 'date': data.get('date', '')}
    except Exception as exc:
        logger.warning('frankfurter error (%.2fs): %s', time.monotonic() - t0, exc)
        return None


def get_rate_table(force=False):
    """Return {'rates', 'date', 'fetched_at', 'stale'} or None.

    Refreshes from the network when the cached row is missing or older than
    24 h; falls back to a stale row when the network call fails.
    """
    from .models import CurrencyRate

    row = None
    try:
        row = CurrencyRate.objects.filter(base=BASE_CURRENCY).first()
    except Exception as exc:
        logger.warning('currency cache read error: %s', exc)

    fresh = row is not None and (timezone.now() - row.fetched_at) < RATES_TTL
    if row is not None and fresh and not force:
        return {'rates': row.rates, 'date': row.rate_date,
                'fetched_at': row.fetched_at, 'stale': False}

    fetched = _fetch_rates()
    if fetched is None:
        if row is not None:  # serve stale data rather than nothing
            return {'rates': row.rates, 'date': row.rate_date,
                    'fetched_at': row.fetched_at, 'stale': True}
        return None

    try:
        row, _ = CurrencyRate.objects.update_or_create(
            base=BASE_CURRENCY,
            defaults={'rates': fetched['rates'], 'rate_date': fetched['date'],
                      'provider': 'frankfurter'},
        )
        return {'rates': row.rates, 'date': row.rate_date,
                'fetched_at': row.fetched_at, 'stale': False}
    except Exception as exc:
        logger.warning('currency cache write error: %s', exc)
        return {'rates': fetched['rates'], 'date': fetched['date'],
                'fetched_at': timezone.now(), 'stale': False}


def convert(amount, from_code, to_code, rates):
    """Cross-convert via the shared base. Returns None if a code is unknown."""
    if from_code not in rates or to_code not in rates:
        return None
    return amount * rates[to_code] / rates[from_code]


def is_supported(code):
    return code in CURRENCIES


def currency_payload(rates):
    """Metadata + rates for the in-browser converter (names, symbols, flags)."""
    meta = {}
    for code in rates:
        name, sym, flag = CURRENCIES.get(code, (code, '', ''))
        meta[code] = {'name': name, 'symbol': sym, 'flag': flag}
    return {'rates': rates, 'meta': meta}


# ---------------------------------------------------------------------------
# Query parsing, "100 usd to eur", "convert 50 gbp to usd", "€100 in yen"
# ---------------------------------------------------------------------------

_CONNECTOR = re.compile(r'\s+(?:' + alt(kw.CONNECTORS + ['=', '->', '→']) + r')\s+', re.IGNORECASE)
_CONVERT_PREFIX = re.compile(r'^(?:' + alt(kw.CONVERT) + r')\s+', re.IGNORECASE)


def _to_float(s):
    try:
        return float(s.replace(',', '').replace(' ', ''))
    except (ValueError, AttributeError):
        return None


def _resolve_code(token):
    token = (token or '').strip().lower()
    if not token:
        return None
    if token.upper() in CURRENCIES:
        return token.upper()
    if token in CURRENCY_WORDS:
        return CURRENCY_WORDS[token]
    return None


def _resolve_symbol(token):
    token = (token or '').strip()
    if token in CURRENCY_SYMBOLS:
        return CURRENCY_SYMBOLS[token]
    return CURRENCY_SYMBOLS.get(token.upper())


def _parse_money(text):
    """Return (amount|None, code) from '$100', '100 usd', 'dollars', etc."""
    text = (text or '').strip()
    if not text:
        return None

    # symbol-prefixed amount: $100, €100, R$ 100
    m = re.match(r'^([^\d\s.,]{1,3})\s*([\d.,]+)$', text)
    if m:
        code = _resolve_symbol(m.group(1))
        amt = _to_float(m.group(2))
        if code and amt is not None:
            return (amt, code)

    # amount + currency: 100 usd, 100 dollars, 100usd
    m = re.match(r'^([\d.,]+)\s*(.+)$', text)
    if m:
        amt = _to_float(m.group(1))
        code = _resolve_code(m.group(2)) or _resolve_symbol(m.group(2))
        if amt is not None and code:
            return (amt, code)

    # bare currency (amount defaults to 1 later): usd, euros, €
    code = _resolve_code(text) or _resolve_symbol(text)
    if code:
        return (None, code)
    return None


def answer(query):
    """Build a currency instant-answer dict, or None."""
    q = _CONVERT_PREFIX.sub('', query.strip(), count=1)
    parts = _CONNECTOR.split(q, maxsplit=1)
    if len(parts) != 2:
        return None

    left = _parse_money(parts[0])
    right = _parse_money(parts[1])
    if not left or not right:
        return None

    amount = left[0] if left[0] is not None else 1.0
    from_code, to_code = left[1], right[1]
    if from_code == to_code:
        return None

    table = get_rate_table()
    if table is None:
        return None
    rates = table['rates']
    converted = convert(amount, from_code, to_code, rates)
    if converted is None:
        return None

    from . import tools
    fn, fs, ff = CURRENCIES.get(from_code, (from_code, '', ''))
    tn, ts, tf = CURRENCIES.get(to_code, (to_code, '', ''))
    unit_rate = convert(1, from_code, to_code, rates)
    return {
        'type': 'currency',
        'amount': tools.fmt_number(amount),
        'amount_raw': amount,
        'from_code': from_code,
        'to_code': to_code,
        'from_name': fn, 'from_symbol': fs, 'from_flag': ff,
        'to_name': tn, 'to_symbol': ts, 'to_flag': tf,
        'result': tools.fmt_number(converted, 2),
        'result_raw': round(converted, 4),
        'unit_rate': tools.fmt_number(unit_rate, 4),
        'date': table.get('date', ''),
        'stale': table.get('stale', False),
        'popular': [c for c in POPULAR if c in rates],
        'data': currency_payload(rates),
    }
