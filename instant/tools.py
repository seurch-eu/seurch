"""Local (offline) instant-answer handlers.

Each ``*_answer`` function takes the raw query (and, where relevant, the
request) and returns a context dict tagged with ``type``, or ``None`` when the
query is not a match. None of these touch the network; everything is computed
from the standard library plus the static tables in ``data``/``units``.
"""

import ast
import base64
import binascii
import hashlib
import ipaddress
import json as _json
import math
import operator
import random
import re
import urllib.parse
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import keywords as kw
from . import qr, units
from .data import CITY_TZ, HTTP_STATUS, PORTS, http_category
from .keywords import alt

_rng = random.SystemRandom()


# ---------------------------------------------------------------------------
# Number formatting
# ---------------------------------------------------------------------------

def fmt_number(x, max_dp=6):
    """Human-friendly number: thousands separators, trimmed decimals."""
    try:
        if x == int(x) and abs(x) < 1e15:
            return f'{int(x):,}'
    except (ValueError, OverflowError):
        pass
    s = f'{x:,.{max_dp}f}'
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    return s


# ---------------------------------------------------------------------------
# Math expressions
# ---------------------------------------------------------------------------

# The AST allowlist stops code execution, but ``**`` and ``factorial`` also
# have to be bounded in *size*: unchecked, a short query like ``9^9^9`` or
# ``factorial(999999)`` computes an astronomically large int that pins a
# worker's CPU and memory (a cheap denial of service, reachable from the
# search page and the public API). Anything over these caps raises
# ``ValueError``, which callers already treat as "not a math answer".
_MAX_POW_EXPONENT = 1000
_MAX_POW_RESULT_BITS = 100_000
_MAX_FACTORIAL_ARG = 10_000


def _bounded_pow(base, exp):
    if abs(exp) > _MAX_POW_EXPONENT:
        raise ValueError('exponent too large')
    if isinstance(base, int) and isinstance(exp, int) and exp > 0 \
            and base.bit_length() * exp > _MAX_POW_RESULT_BITS:
        raise ValueError('result too large')
    return operator.pow(base, exp)


def _bounded_factorial(n):
    if isinstance(n, (int, float)) and n > _MAX_FACTORIAL_ARG:
        raise ValueError('factorial argument too large')
    return math.factorial(n)


_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: _bounded_pow, ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    'sqrt': math.sqrt, 'cbrt': lambda x: math.copysign(abs(x) ** (1 / 3), x),
    'sin': math.sin, 'cos': math.cos, 'tan': math.tan,
    'asin': math.asin, 'acos': math.acos, 'atan': math.atan,
    'log': math.log10, 'log2': math.log2, 'ln': math.log,
    'exp': math.exp, 'abs': abs, 'round': round,
    'floor': math.floor, 'ceil': math.ceil, 'factorial': _bounded_factorial,
    'radians': math.radians, 'degrees': math.degrees, 'gcd': math.gcd,
}
_CONSTS = {'pi': math.pi, 'e': math.e, 'tau': math.tau}

_MATH_CHARS = re.compile(r'^[\d\s+\-*/^%.,()a-z]+$')
_HAS_OP = re.compile(r'[+\-*/^%]|\b(?:sqrt|cbrt|sin|cos|tan|log|ln|exp|abs|'
                     r'round|floor|ceil|factorial|pi|tau|gcd)\b')
_PERCENT_OF = re.compile(r'^\s*([\d.]+)\s*%\s+(?:' + alt(kw.OF) + r')\s+([\d.]+)\s*$', re.IGNORECASE)
_PERCENT_INLINE = re.compile(r'(\d+(?:\.\d+)?)\s*%')


def _eval_node(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError('bad constant')
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
        if node.keywords:
            raise ValueError('no kwargs')
        return _FUNCS[node.func.id](*[_eval_node(a) for a in node.args])
    raise ValueError(f'unsupported: {type(node).__name__}')


def safe_eval(expr):
    """Evaluate a restricted arithmetic expression. Raises on anything unsafe."""
    expr = expr.replace('^', '**').replace('×', '*').replace('÷', '/')
    tree = ast.parse(expr, mode='eval')
    return _eval_node(tree.body)


def math_answer(query, request=None):
    q = query.strip()
    low = q.lower().rstrip('=?').strip()
    if not low:
        return None

    # "15% of 200" → 30
    m = _PERCENT_OF.match(low)
    if m:
        try:
            result = float(m.group(1)) / 100 * float(m.group(2))
            return {'type': 'math', 'expr': f'{m.group(1)}% of {m.group(2)}',
                    'result': fmt_number(result), 'raw': result}
        except ValueError:
            return None

    if not _MATH_CHARS.match(low) or not _HAS_OP.search(low):
        return None
    # Inline percents: "200 + 10%" → "200 + 10/100*200" is ambiguous; treat a
    # standalone "N%" as N/100 so "50% * 80" works intuitively.
    expr = _PERCENT_INLINE.sub(r'(\1/100)', low)
    try:
        result = safe_eval(expr)
    except (ValueError, SyntaxError, TypeError, ZeroDivisionError, OverflowError,
            RecursionError):
        return None
    if not isinstance(result, (int, float)) or isinstance(result, bool):
        return None
    if isinstance(result, float) and (math.isnan(result) or math.isinf(result)):
        return None
    return {'type': 'math', 'expr': q.rstrip('=?').strip(),
            'result': fmt_number(result), 'raw': result}


# ---------------------------------------------------------------------------
# Base / radix conversion
# ---------------------------------------------------------------------------

_BASE_NAMES = {name: base for base, names in kw.BASE_NAMES.items() for name in names}
_BASE_NAME_ALT = alt(_BASE_NAMES.keys())
_BASE_PREFIXED = re.compile(r'^(0x[0-9a-f]+|0b[01]+|0o[0-7]+)$', re.IGNORECASE)
_BASE_TO = re.compile(
    r'^(?:(?:' + alt(kw.CONVERT) + r')\s+)?(.+?)\s+(?:' + alt(kw.CONNECTORS) + r')\s+('
    + _BASE_NAME_ALT + r')$', re.IGNORECASE)
_BASE_FROM = re.compile(r'^(' + _BASE_NAME_ALT + r')\s+([0-9a-fx]+)$', re.IGNORECASE)
_BASE_FROM_INNER = re.compile(r'^(' + _BASE_NAME_ALT + r')\s+(.+)$', re.IGNORECASE)


def _parse_int_any(token):
    """Parse an integer written in dec/hex/bin/oct (prefixed or bare)."""
    token = token.strip().lower().replace(' ', '')
    try:
        if token.startswith(('0x', '0b', '0o')):
            return int(token, 0)
        if re.fullmatch(r'[0-9a-f]+', token) and re.search(r'[a-f]', token):
            return int(token, 16)
        return int(token, 10)
    except ValueError:
        return None


def _base_card(value):
    return {
        'type': 'base',
        'dec': f'{value:,}',
        'hex': format(value, 'X'),
        'bin': format(value, 'b'),
        'oct': format(value, 'o'),
        'value': value,
    }


def base_answer(query, request=None):
    q = query.strip().lower()

    if _BASE_PREFIXED.match(q):
        value = _parse_int_any(q)
        return _base_card(value) if value is not None else None

    m = _BASE_TO.match(q)
    if m:
        src = m.group(1).strip()
        # Allow "binary 1010 to hex" too.
        fm = _BASE_FROM_INNER.match(src)
        if fm:
            base = _BASE_NAMES[fm.group(1)]
            try:
                value = int(fm.group(2).strip().replace(' ', ''), base)
            except ValueError:
                return None
        else:
            value = _parse_int_any(src)
        return _base_card(value) if value is not None else None

    m = _BASE_FROM.match(q)
    if m:
        base = _BASE_NAMES[m.group(1)]
        try:
            value = int(m.group(2).strip(), base)
        except ValueError:
            return None
        return _base_card(value)
    return None


# ---------------------------------------------------------------------------
# Colour conversion (hex ↔ rgb ↔ hsl)
# ---------------------------------------------------------------------------

_NAMED_COLORS = {
    'black': '000000', 'white': 'ffffff', 'red': 'ff0000', 'lime': '00ff00',
    'green': '008000', 'blue': '0000ff', 'yellow': 'ffff00', 'cyan': '00ffff',
    'magenta': 'ff00ff', 'silver': 'c0c0c0', 'gray': '808080', 'grey': '808080',
    'maroon': '800000', 'olive': '808000', 'purple': '800080', 'teal': '008080',
    'navy': '000080', 'orange': 'ffa500', 'pink': 'ffc0cb', 'brown': 'a52a2a',
    'gold': 'ffd700', 'indigo': '4b0082', 'violet': 'ee82ee', 'coral': 'ff7f50',
    'salmon': 'fa8072', 'turquoise': '40e0d0', 'crimson': 'dc143c',
}
_HEX_RE = re.compile(r'#([0-9a-f]{6}|[0-9a-f]{3})\b', re.IGNORECASE)
_HEX_ONLY_RE = re.compile(r'^#?([0-9a-f]{6}|[0-9a-f]{3})$', re.IGNORECASE)
_RGB_RE = re.compile(r'rgba?\(\s*(\d+)\D+(\d+)\D+(\d+)', re.IGNORECASE)
_HSL_RE = re.compile(r'hsla?\(\s*(\d+)\D+(\d+)%?\D+(\d+)%?', re.IGNORECASE)
_COLOR_KEYWORDS = frozenset(kw.COLOR_KEYWORDS)
_COLOR_CONTEXT_RE = re.compile(r'\b(?:' + alt(kw.COLOR_CONTEXT) + r')\b', re.IGNORECASE)


def _rgb_to_hsl(r, g, b):
    r, g, b = r / 255, g / 255, b / 255
    mx, mn = max(r, g, b), min(r, g, b)
    lum = (mx + mn) / 2
    if mx == mn:
        h = s = 0.0
    else:
        d = mx - mn
        s = d / (2 - mx - mn) if lum > 0.5 else d / (mx + mn)
        if mx == r:
            h = (g - b) / d + (6 if g < b else 0)
        elif mx == g:
            h = (b - r) / d + 2
        else:
            h = (r - g) / d + 4
        h /= 6
    return round(h * 360), round(s * 100), round(lum * 100)


def _color_card(r, g, b):
    hexv = f'{r:02X}{g:02X}{b:02X}'
    h, s, lum = _rgb_to_hsl(r, g, b)
    return {
        'type': 'color',
        'hex': f'#{hexv}',
        'rgb': f'rgb({r}, {g}, {b})',
        'hsl': f'hsl({h}, {s}%, {lum}%)',
        'r': r, 'g': g, 'b': b, 'h': h, 's': s, 'l': lum,
    }


def _hex_to_card(h):
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return _color_card(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def color_answer(query, request=None):
    q = query.strip().lower()
    if q in _COLOR_KEYWORDS:
        return _color_card(79, 70, 229)  # indigo default

    rm = _RGB_RE.search(q)
    if rm:
        r, g, b = (min(255, int(x)) for x in rm.groups())
        return _color_card(r, g, b)
    sm = _HSL_RE.search(q)
    if sm:
        return _hsl_card(int(sm.group(1)), min(100, int(sm.group(2))), min(100, int(sm.group(3))))
    hm = _HEX_RE.search(q)
    if hm:
        return _hex_to_card(hm.group(1))

    # Bare hex ("ff0000") or a named colour ("red") only count when the query
    # makes the colour intent explicit, otherwise words like "bed" or "dad"
    # (valid 3-digit hex) would hijack ordinary searches.
    if not _COLOR_CONTEXT_RE.search(q):
        return None
    for token in re.split(r'\s+', q):
        if token in _NAMED_COLORS:
            return _hex_to_card(_NAMED_COLORS[token])
        # Only full 6-digit hex when bare: a 3-digit match would treat "255"
        # (in "255 in hex") or words like "abc" as colours.
        if re.fullmatch(r'#?[0-9a-f]{6}', token):
            return _hex_to_card(token.lstrip('#'))
    return None


def _hsl_to_rgb(h, s, lum):
    h, s, lum = h / 360, s / 100, lum / 100

    def hue(p, q, t):
        t %= 1
        if t < 1 / 6:
            return p + (q - p) * 6 * t
        if t < 1 / 2:
            return q
        if t < 2 / 3:
            return p + (q - p) * (2 / 3 - t) * 6
        return p

    if s == 0:
        r = g = b = lum
    else:
        q = lum * (1 + s) if lum < 0.5 else lum + s - lum * s
        p = 2 * lum - q
        r, g, b = hue(p, q, h + 1 / 3), hue(p, q, h), hue(p, q, h - 1 / 3)
    return round(r * 255), round(g * 255), round(b * 255)


def _hsl_card(h, s, lum):
    return _color_card(*_hsl_to_rgb(h, s, lum))


# ---------------------------------------------------------------------------
# What's my IP
# ---------------------------------------------------------------------------

_IP_RE = re.compile(r'\b(?:' + alt(kw.MY_IP) + r')\b', re.IGNORECASE)


def _normalize_ip(raw):
    """Pull a bare IP out of an X-Forwarded-For / REMOTE_ADDR token.

    Handles the shapes proxies actually emit: a plain address, a bracketed
    IPv6 (``[2001:db8::1]``), an address with a port (``1.2.3.4:443`` or
    ``[2001:db8::1]:443``) and an IPv6 zone id (``fe80::1%eth0``). A bare IPv6
    is full of colons, so we only strip a ``:port`` when brackets were present
    or there is exactly one colon. Returns ``''`` for empty input and the
    original token (trimmed) when it does not parse as an IP.
    """
    raw = (raw or '').strip()
    if not raw:
        return ''
    candidate = raw
    if candidate.startswith('['):
        candidate = candidate[1:].partition(']')[0]
    elif candidate.count(':') == 1:  # host:port (a bare IPv6 has >= 2 colons)
        candidate = candidate.split(':', 1)[0]
    candidate = candidate.split('%', 1)[0]  # drop IPv6 zone id
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return raw


def _is_private_ip(ip):
    """True when *ip* is not a routable public address (v4 or v6).

    Covers loopback, private (RFC 1918 / IPv6 ULA fc00::/7), link-local
    (169.254/16 and fe80::/10), CGNAT and other reserved ranges via the
    stdlib, so the "behind a proxy your public IP may differ" hint fires for
    IPv6 too, not just the handful of IPv4 prefixes the old check knew about.
    Unparseable / empty values count as non-public.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return (addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_unspecified or addr.is_reserved)


def _client_ip(request):
    """Best-effort client IP from proxy headers.

    Prefers the left-most ``X-Forwarded-For`` entry (the original client as
    seen by the first proxy), falling back to ``REMOTE_ADDR``. The selected
    entry is normalized but *not* second-guessed: if the proxy only ever saw a
    private address (e.g. it never received the real client IP) that private
    address is what we report, the ``is_local`` hint then explains it.
    """
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        raw = xff.split(',')[0]
    else:
        raw = request.META.get('REMOTE_ADDR', '')
    return _normalize_ip(raw)


def ip_answer(query, request=None):
    q = query.strip().lower()
    if q != 'ip' and not _IP_RE.search(q):
        return None
    ip = ''
    ua = ''
    if request is not None:
        ip = _client_ip(request)
        ua = request.META.get('HTTP_USER_AGENT', '')
    return {
        'type': 'ip',
        'ip': ip or 'Unavailable',
        'user_agent': ua,
        'is_local': _is_private_ip(ip),
    }


# ---------------------------------------------------------------------------
# UUID generator
# ---------------------------------------------------------------------------

def uuid_answer(query, request=None):
    q = query.strip().lower()
    if not re.fullmatch(r'(generate\s+|new\s+|random\s+)?(uuid|guid)(\s*v?4)?(\s+generator)?', q):
        return None
    return {
        'type': 'uuid',
        'value': str(uuid.uuid4()),
        'values': [str(uuid.uuid4()) for _ in range(5)],
    }


# ---------------------------------------------------------------------------
# Hash generator (md5, sha1, sha256, sha512)
# ---------------------------------------------------------------------------

# Explicit algorithm ("md5 hello") triggers directly; the generic word "hash"
# must be followed by "of"/"for" so food like "hash browns" stays a web search.
_HASH_ALGO_RE = re.compile(
    r'^(md5|sha-?1|sha-?224|sha-?256|sha-?384|sha-?512)\s+(?:hash\s+)?(?:(?:'
    + alt(kw.OF) + r')\s+)?(.+)$', re.IGNORECASE)
_HASH_GENERIC_RE = re.compile(r'^hash\s+(?:' + alt(kw.OF) + r')\s+(.+)$', re.IGNORECASE)


def compute_hashes(text):
    raw = text.encode('utf-8')
    return {
        'md5': hashlib.md5(raw).hexdigest(),
        'sha1': hashlib.sha1(raw).hexdigest(),
        'sha256': hashlib.sha256(raw).hexdigest(),
        'sha512': hashlib.sha512(raw).hexdigest(),
    }


def hash_answer(query, request=None):
    q = query.strip()
    m = _HASH_ALGO_RE.match(q)
    if m:
        algo = m.group(1).lower().replace('-', '')
        text = m.group(2).strip()
    else:
        m = _HASH_GENERIC_RE.match(q)
        if not m:
            return None
        algo, text = '', m.group(1).strip()
    if not text:
        return None
    return {
        'type': 'hash',
        'text': text,
        'hashes': compute_hashes(text),
        'highlight': algo,
    }


# ---------------------------------------------------------------------------
# Encode / decode (URL, Base64)
# ---------------------------------------------------------------------------

# Require an explicit encode/decode verb so "url shortener" stays a web search.
_DECODE_SET = {w.lower() for w in kw.DECODE_VERBS}
_ENCODE_RE = re.compile(
    r'^(url|base64|b64)\s*(' + alt(kw.ENCODE_VERBS + kw.DECODE_VERBS) + r')\s*:?\s+(.+)$', re.IGNORECASE)


def _b64_decode(text):
    try:
        pad = '=' * (-len(text) % 4)
        return base64.b64decode(text + pad, validate=False).decode('utf-8', 'replace')
    except (binascii.Error, ValueError):
        return None


def encode_answer(query, request=None):
    m = _ENCODE_RE.match(query.strip())
    if not m:
        return None
    kind = 'url' if m.group(1).lower() == 'url' else 'base64'
    action = 'decode' if m.group(2).lower() in _DECODE_SET else 'encode'
    text = m.group(3).strip()

    if kind == 'url':
        output = urllib.parse.unquote_plus(text) if action == 'decode' else urllib.parse.quote_plus(text)
    else:
        if action == 'decode':
            output = _b64_decode(text)
            if output is None:
                output = '(invalid Base64 input)'
        else:
            output = base64.b64encode(text.encode('utf-8')).decode('ascii')
    return {
        'type': 'encode',
        'kind': kind,
        'kind_label': 'URL' if kind == 'url' else 'Base64',
        'action': action,
        'input': text,
        'output': output,
    }


# ---------------------------------------------------------------------------
# JSON formatter / validator
# ---------------------------------------------------------------------------

_JSON_KEYWORDS = frozenset(kw.JSON_KEYWORDS)
_JSON_PAYLOAD_RE = re.compile(
    r'^(?:(?:' + alt(kw.JSON_VERBS) + r')\s+)?json\s*:?\s*(.+)$', re.IGNORECASE | re.DOTALL)


def json_answer(query, request=None):
    q = query.strip()
    low = q.lower()
    payload = ''
    if low in _JSON_KEYWORDS:
        payload = ''
    else:
        m = _JSON_PAYLOAD_RE.match(q)
        if m and m.group(1).strip().startswith(('{', '[')):
            payload = m.group(1).strip()
        elif q.startswith(('{', '[')) and q.endswith(('}', ']')) and len(q) > 1:
            payload = q
        else:
            return None

    card = {'type': 'json', 'input': payload, 'formatted': '', 'valid': None, 'error': ''}
    if payload:
        try:
            parsed = _json.loads(payload)
            card['formatted'] = _json.dumps(parsed, indent=2, ensure_ascii=False, sort_keys=False)
            card['valid'] = True
        except ValueError as exc:
            card['valid'] = False
            card['error'] = str(exc)
    return card


# ---------------------------------------------------------------------------
# Regex tester (client-side widget; server just recognises the trigger)
# ---------------------------------------------------------------------------

_REGEX_KEYWORDS = frozenset(kw.REGEX_KEYWORDS)


def regex_answer(query, request=None):
    if query.strip().lower() in _REGEX_KEYWORDS:
        return {'type': 'regex'}
    return None


# ---------------------------------------------------------------------------
# HTTP status code lookup
# ---------------------------------------------------------------------------

_HTTP_WORD_ALT = alt(kw.HTTP_WORDS)
_HTTP_RE = re.compile(
    r'^(?:(?:' + _HTTP_WORD_ALT + r')\s+)*(\d{3})(?:\s+(?:' + _HTTP_WORD_ALT + r'))?$', re.IGNORECASE)
_HTTP_KW_RE = re.compile(r'\b(?:' + _HTTP_WORD_ALT + r')\b', re.IGNORECASE)


def http_answer(query, request=None):
    q = query.strip()
    m = _HTTP_RE.match(q)
    if not m:
        return None
    code = int(m.group(1))
    if code not in HTTP_STATUS:
        return None
    # A bare 3-digit number only counts as an HTTP code when it is a client or
    # server error (the codes people actually look up), otherwise require a
    # keyword so plain numbers don't get hijacked.
    bare = q.isdigit()
    if bare and not (400 <= code <= 599) and not _HTTP_KW_RE.search(q):
        return None
    name, desc = HTTP_STATUS[code]
    cat, cat_label = http_category(code)
    return {
        'type': 'http',
        'code': code,
        'name': name,
        'description': desc,
        'category': cat,
        'category_label': cat_label,
        'is_error': code >= 400,
    }


# ---------------------------------------------------------------------------
# Port number lookup
# ---------------------------------------------------------------------------

_PORT_WORD_ALT = alt(kw.PORT_WORDS)
_PORT_RE = re.compile(
    r'^(?:tcp\s+|udp\s+)?(?:' + _PORT_WORD_ALT + r')'
    r'(?:\s+(?:number|número|numero|nummer|numéro|numero))?\s+(\d{1,5})$'
    r'|^(\d{1,5})\s+(?:' + _PORT_WORD_ALT + r')$', re.IGNORECASE)


def port_answer(query, request=None):
    m = _PORT_RE.match(query.strip())
    if not m:
        return None
    port = int(m.group(1) or m.group(2))
    if not 1 <= port <= 65535:
        return None
    if port not in PORTS:
        return {'type': 'port', 'port': port, 'known': False}
    service, proto, desc = PORTS[port]
    return {
        'type': 'port', 'port': port, 'known': True,
        'service': service, 'protocol': proto, 'description': desc,
    }


# ---------------------------------------------------------------------------
# Random number
# ---------------------------------------------------------------------------

_RANDOM_RE = re.compile(
    r'^(?:(?:' + alt(kw.GENERATE) + r')\s+)?(?:' + alt(kw.RANDOM_WORDS) + r')'
    r'(?:\s+(?:number\s+)?(?:(?:' + alt(kw.RANGE_BETWEEN) + r')\s+)?'
    r'(-?\d+)\s*(?:' + alt(kw.RANGE_SEP) + r')\s*(-?\d+))?$', re.IGNORECASE)


def random_answer(query, request=None):
    q = query.strip().lower()
    m = _RANDOM_RE.match(q)
    if not m:
        return None
    lo, hi = 1, 100
    if m.group(1) is not None and m.group(2) is not None:
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
    return {'type': 'random', 'min': lo, 'max': hi, 'value': _rng.randint(lo, hi)}


# ---------------------------------------------------------------------------
# Dice roll
# ---------------------------------------------------------------------------

_DICE_RE = re.compile(
    r'^(?:(?:' + alt(kw.DICE_VERBS) + r')\s+)?'
    r'(?:(\d{1,3})?\s*d\s*(\d{1,3})'
    r'|(?:(?:a|an|un|une|einen|eine|een|uno|um|uma)\s+)?(?:' + alt(kw.DICE_NOUNS) + r'))$',
    re.IGNORECASE)


def dice_answer(query, request=None):
    m = _DICE_RE.match(query.strip())
    if not m:
        return None
    count = int(m.group(1)) if m.group(1) else 1
    sides = int(m.group(2)) if m.group(2) else 6
    count = max(1, min(count, 20))
    sides = max(2, min(sides, 1000))
    rolls = [_rng.randint(1, sides) for _ in range(count)]
    return {
        'type': 'dice', 'count': count, 'sides': sides,
        'rolls': rolls, 'total': sum(rolls),
        'notation': f'{count}d{sides}',
    }


# ---------------------------------------------------------------------------
# Coin flip
# ---------------------------------------------------------------------------

_COIN_KEYWORDS = frozenset(kw.COIN_KEYWORDS)


def coin_answer(query, request=None):
    if query.strip().lower() not in _COIN_KEYWORDS:
        return None
    return {'type': 'coin', 'result': _rng.choice(['Heads', 'Tails'])}


# ---------------------------------------------------------------------------
# Stopwatch / timer (client-side widget; server parses an optional duration)
# ---------------------------------------------------------------------------

_STOPWATCH_KEYWORDS = frozenset(kw.STOPWATCH_WORDS)
_TIMER_KEYWORDS = frozenset(kw.TIMER_WORDS) | {'set timer', 'set a timer', 'egg timer'}
_TIMER_TRIGGER_ALT = alt(kw.TIMER_WORDS)
_DUR_HOURS_RE = re.compile(r'(\d+)\s*(?:' + alt(kw.DUR_HOURS) + r')\b', re.IGNORECASE)
_DUR_MINS_RE = re.compile(r'(\d+)\s*(?:' + alt(kw.DUR_MINS) + r')\b', re.IGNORECASE)
_DUR_SECS_RE = re.compile(r'(\d+)\s*(?:' + alt(kw.DUR_SECS) + r')\b', re.IGNORECASE)


def parse_duration(text):
    """Sum a hh/mm/ss duration written in any supported language."""
    total = 0
    for n in _DUR_HOURS_RE.findall(text):
        total += int(n) * 3600
    for n in _DUR_MINS_RE.findall(text):
        total += int(n) * 60
    for n in _DUR_SECS_RE.findall(text):
        total += int(n)
    return total


def timer_answer(query, request=None):
    q = query.strip().lower()
    if q in _STOPWATCH_KEYWORDS:
        return {'type': 'timer', 'mode': 'stopwatch', 'seconds': 0}
    if q in _TIMER_KEYWORDS:
        return {'type': 'timer', 'mode': 'timer', 'seconds': 0}

    m = re.match(r'^(?:set\s+)?(?:a\s+)?(?:' + _TIMER_TRIGGER_ALT + r')\s+'
                 r'(?:(?:for|pour|für|fuer|de|para|voor|van)\s+)?(.+)$', q)
    if m:
        dur = m.group(1)
    else:
        m2 = re.match(r'^(.+?)\s+(?:' + _TIMER_TRIGGER_ALT + r')$', q)
        if not m2:
            return None
        dur = m2.group(1)

    seconds = parse_duration(dur)
    if seconds == 0:
        bm = re.fullmatch(r'(\d+)', dur.strip())
        if bm:
            seconds = int(bm.group(1)) * 60  # bare number → minutes
        # else: keep 0 and still open the timer for the user to set
    return {'type': 'timer', 'mode': 'timer', 'seconds': seconds}


# ---------------------------------------------------------------------------
# World clock / time in a city
# ---------------------------------------------------------------------------

_TIME_WORD_ALT = alt(kw.TIME)
_TIME_PREP_ALT = alt(kw.PREP)
# A localized "what time is it" question, with an optional preposition + place.
_TIME_QUESTION_RE = re.compile(
    r'^(?:' + alt(kw.TIME_QUESTIONS) + r')(?:\s+(?:' + _TIME_PREP_ALT + r'))?\s+(.+)$', re.IGNORECASE)
# "<time word> [filler] <prep> <place>", e.g. "time in tokyo", "heure à paris".
_TIME_PREP_RE = re.compile(
    r'\b(?:' + _TIME_WORD_ALT + r')\b.*?\b(?:' + _TIME_PREP_ALT + r')\s+(.+)$', re.IGNORECASE)
# "<time word> <place>" and "<place> <time word>".
_TIME_LEAD_RE = re.compile(r'^(?:' + _TIME_WORD_ALT + r')\s+(.+)$', re.IGNORECASE)
_TIME_TRAIL_RE = re.compile(r'^(.+?)\s+(?:' + _TIME_WORD_ALT + r')(?:\s+now)?$', re.IGNORECASE)


def worldclock_answer(query, request=None):
    q = query.strip().lower().rstrip('?').strip()
    place = None
    mq = _TIME_QUESTION_RE.match(q)
    if mq:
        place = mq.group(1)
    elif re.search(r'\b(?:' + _TIME_WORD_ALT + r')\b', q):
        m = _TIME_PREP_RE.search(q) or _TIME_LEAD_RE.match(q) or _TIME_TRAIL_RE.match(q)
        if m:
            place = m.group(1)
    if not place:
        return None
    place = re.sub(r'\b(city|now|right now|currently)\b', '', place).strip().strip('?').strip()
    if not place:
        return None
    tz_name = CITY_TZ.get(place)
    if not tz_name:
        return None
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    now = datetime.now(tz)
    offset = now.utcoffset()
    total_min = int(offset.total_seconds() // 60) if offset else 0
    sign = '+' if total_min >= 0 else '-'
    off_label = f'UTC{sign}{abs(total_min) // 60:02d}:{abs(total_min) % 60:02d}'
    return {
        'type': 'worldclock',
        'place': place.title(),
        'tz': tz_name,
        'tz_abbr': now.tzname() or '',
        'time': now.strftime('%H:%M'),
        'time_ampm': now.strftime('%I:%M %p').lstrip('0'),
        'date': now.strftime('%A, %d %B %Y'),
        'offset': off_label,
    }


# ---------------------------------------------------------------------------
# QR code
# ---------------------------------------------------------------------------

_QR_GEN_ALT = alt(kw.GENERATE)
_QR_RE = re.compile(
    r'^(?:(?:' + _QR_GEN_ALT + r')\s+)?(?:' + alt(kw.QR_NOUNS) + r')\s+'
    r'(?:(?:for|pour|für|fuer|para|voor|de)\s+)?(.+)$', re.IGNORECASE)
_QR_GEN_PREFIX_RE = re.compile(r'^(?:' + _QR_GEN_ALT + r')\s+', re.IGNORECASE)
_QR_DEFAULT = frozenset(kw.QR_NOUNS) | {'qr generator', 'qr code generator', 'qr-code generator'}


def qr_answer(query, request=None):
    q = query.strip()
    low = _QR_GEN_PREFIX_RE.sub('', q.lower(), count=1)
    if low in _QR_DEFAULT:
        text = 'https://example.com'
    else:
        m = _QR_RE.match(q)
        if not m:
            return None
        text = m.group(1).strip()
    if not text:
        return None
    svg = qr.make_svg(text)
    if svg is None:
        return None
    return {'type': 'qr', 'text': text, 'svg': svg}


# ---------------------------------------------------------------------------
# Unit conversion
# ---------------------------------------------------------------------------

_UNIT_TOKEN = r'[a-z0-9à-ÿ°"\'µ²³/]+'
_UNIT_RE = re.compile(
    r'^(-?\d+(?:\.\d+)?)\s*(' + _UNIT_TOKEN + r')\s+(?:' + alt(kw.CONNECTORS) + r')\s+('
    + _UNIT_TOKEN + r')\s*$', re.IGNORECASE)


def unit_answer(query, request=None):
    m = _UNIT_RE.match(query.strip())
    if not m:
        return None
    amount = float(m.group(1))
    from_u = units.lookup_unit(m.group(2))
    to_u = units.lookup_unit(m.group(3))
    if not from_u or not to_u or from_u[0] != to_u[0]:
        return None
    category = from_u[0]
    result = units.convert(category, amount, from_u[1], to_u[1])
    return {
        'type': 'unit',
        'category': category,
        'category_label': units.CATEGORY_LABELS.get(category, units.UNIT_DEFS[category]['label']),
        'amount': fmt_number(amount),
        'amount_raw': amount,
        'result': fmt_number(result),
        'result_raw': round(result, 6),
        'from_key': from_u[1],
        'to_key': to_u[1],
        'from_symbol': units.symbol(category, from_u[1]),
        'to_symbol': units.symbol(category, to_u[1]),
        'payload': units.category_payload(category),
    }


# ---------------------------------------------------------------------------
# Unix timestamp / epoch converter
# ---------------------------------------------------------------------------

_TIMESTAMP_KEYWORDS = frozenset(kw.TIMESTAMP_KEYWORDS)
_TS_KW_NUM_RE = re.compile(
    r'^(?:timestamp|epoch|unix(?:\s*time)?|unixtime|horodatage|zeitstempel)\s+(\d{9,16})$', re.IGNORECASE)
_TS_TO_DATE_RE = re.compile(
    r'^(?:(?:' + alt(kw.CONVERT) + r')\s+)?(\d{9,16})\s+(?:' + alt(kw.CONNECTORS) + r')\s+(?:'
    + alt(kw.DATE_WORDS) + r')$', re.IGNORECASE)
_TS_NUM_KW_RE = re.compile(r'^(\d{9,16})\s+(?:timestamp|epoch|unix)$', re.IGNORECASE)


def _timestamp_card(value):
    if value is None:
        epoch = int(datetime.now(UTC).timestamp())
        secs, given = epoch, False
    else:
        secs = value / 1000 if value >= 10 ** 12 else value
        epoch, given = value, True
    try:
        dt = datetime.fromtimestamp(secs, UTC)
    except (OSError, OverflowError, ValueError):
        return None
    return {
        'type': 'timestamp',
        'epoch': epoch,
        'given': given,
        'utc': dt.strftime('%Y-%m-%d %H:%M:%S UTC'),
        'utc_full': dt.strftime('%A, %d %B %Y · %H:%M:%S UTC'),
        'iso': dt.strftime('%Y-%m-%dT%H:%M:%SZ'),
    }


def timestamp_answer(query, request=None):
    q = query.strip().lower()
    if q in _TIMESTAMP_KEYWORDS:
        return _timestamp_card(None)
    m = _TS_KW_NUM_RE.match(q) or _TS_TO_DATE_RE.match(q) or _TS_NUM_KW_RE.match(q)
    if m:
        try:
            return _timestamp_card(int(m.group(1)))
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Password generator (secure RNG; live regeneration client-side)
# ---------------------------------------------------------------------------

_PASSWORD_KEYWORDS = frozenset(kw.PASSWORD_KEYWORDS)
_PW_LOWER = 'abcdefghijkmnopqrstuvwxyz'      # no l (looks like 1/I)
_PW_UPPER = 'ABCDEFGHJKLMNPQRSTUVWXYZ'       # no I, O
_PW_DIGITS = '23456789'                       # no 0, 1
_PW_SYMBOLS = '!@#$%^&*()-_=+[]{};:,.?'


def generate_password(length=16, lower=True, upper=True, digits=True, symbols=True):
    pool = (_PW_LOWER if lower else '') + (_PW_UPPER if upper else '') + \
           (_PW_DIGITS if digits else '') + (_PW_SYMBOLS if symbols else '')
    if not pool:
        pool = _PW_LOWER + _PW_UPPER + _PW_DIGITS
    length = max(4, min(int(length), 128))
    return ''.join(_rng.choice(pool) for _ in range(length))


def password_answer(query, request=None):
    if query.strip().lower() not in _PASSWORD_KEYWORDS:
        return None
    return {'type': 'password', 'value': generate_password(16), 'length': 16}
