"""Fixed-window rate limiting on the shared Django cache.

Backs the brute-force protection on the auth endpoints (login lockout,
password-reset throttling, see ``accounts.views``) and the per-IP budget on
the public OpenSearch endpoints (``search.views``). Counters live in
``CACHES['default']`` (a database cache by default, see settings), so a limit
holds across gunicorn workers and survives restarts, where a per-process
counter would let an attacker multiply every limit by the worker count or
reset it with a redeploy.

A counter is identified by a ``scope`` (what is being limited, e.g.
``'login-user'``) and an ``ident`` (who is being limited, a username or IP).
The window is fixed, not sliding: the first hit starts it, and the count
expires with it.
"""

from django.core.cache import cache


def _key(scope, ident):
    return f'ratelimit:{scope}:{ident}'


def hit(scope, ident, window):
    """Record one hit against ``scope:ident``; return the count in the current
    *window* (seconds), including this hit."""
    key = _key(scope, ident)
    cache.add(key, 0, window)
    try:
        return cache.incr(key)
    except ValueError:
        # The counter expired between add() and incr(); start a fresh window.
        cache.set(key, 1, window)
        return 1


def is_limited(scope, ident, limit):
    """True when ``scope:ident`` has already used up *limit* hits in its
    current window (a pure check, records nothing)."""
    return (cache.get(_key(scope, ident)) or 0) >= limit


def clear(scope, ident):
    """Forget ``scope:ident``'s counter (e.g. after a successful login)."""
    cache.delete(_key(scope, ident))


def client_ip(request):
    """Best-effort client IP for rate-limit keys.

    Prefers the left-most ``X-Forwarded-For`` entry, falling
    back to ``REMOTE_ADDR``. Returns ``''`` when neither is usable, callers
    should still count those requests (under the empty key) rather than
    exempt them.
    """
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')
