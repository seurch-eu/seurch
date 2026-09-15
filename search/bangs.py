import json
import logging
import os
import re
from urllib.parse import quote_plus, urlencode, urlsplit

from django.urls import reverse

logger = logging.getLogger(__name__)

_bangs: dict | None = None
_BANG_RE = re.compile(r'(?:^|\s)!([a-zA-Z0-9][a-zA-Z0-9._-]*)(?:\s|$)')
_LUCKY_RE = re.compile(r'(?:^|\s)!(?:\s|$)')
_TAB_BANGS = {
    'web': 'web',
    'images': 'images',
    'i': 'images',
    'news': 'news',
    'n': 'news',
    'videos': 'videos',
    'v': 'videos',
    'maps': 'maps',
    'm': 'maps',
    'translate': 'translate',
}

# Priority order used to pick a replacement when the requested tab isn't
# available to this user (see fallback_tab).
_TAB_ORDER = ('web', 'images', 'news', 'videos', 'maps')


def fallback_tab(available_tabs) -> str:
    """The tab to land on when the requested one isn't available to the user.

    Returns the first available tab in display order; defaults to ``'web'``
    (always available, every engine does web)."""
    for tab in _TAB_ORDER:
        if tab in available_tabs:
            return tab
    return 'web'


def _load() -> dict:
    global _bangs
    if _bangs is not None:
        return _bangs
    path = os.path.join(os.path.dirname(__file__), 'data', 'bangs.json')
    try:
        with open(path, encoding='utf-8') as f:
            _bangs = json.load(f)
        logger.debug('Loaded %d bang triggers', len(_bangs))
    except OSError:
        logger.info('bangs.json not found, run: python manage.py fetch_bangs')
        _bangs = {}
    except Exception as exc:
        logger.warning('Failed to load bangs.json: %s', exc)
        _bangs = {}
    return _bangs


def _user_bang(user, trigger: str) -> str | None:
    """Return the URL template for *trigger* if the authenticated *user* has one defined."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    from .models import CustomBang
    try:
        return CustomBang.objects.values_list('url_template', flat=True).get(user=user, trigger=trigger)
    except CustomBang.DoesNotExist:
        return None


def resolve(query: str, user=None, available_tabs=None) -> str | None:
    """Return a redirect URL if *query* contains a valid bang, else None.

    Bangs matching a local result tab (e.g. ``!news``, ``!n``) redirect to
    that tab; user-defined custom bangs are checked next and override the
    generic external bangs map. Standalone ``!`` (lucky bang) is handled
    separately by ``lucky_terms``.

    When *available_tabs* is given (a set of tab names this user can use), a
    tab bang for a tab they don't have, e.g. ``!news`` while on Mojeek, or
    ``!maps`` with OpenStreetMap switched off, is deactivated: it degrades to
    a normal search on the fallback tab instead of routing to a hidden tab.
    """
    if '!' not in query:
        return None
    m = _BANG_RE.search(query)
    if not m:
        return None
    trigger = m.group(1).lower()
    terms = _BANG_RE.sub(' ', query).strip()
    tab = _TAB_BANGS.get(trigger)
    if tab:
        if available_tabs is not None and tab not in available_tabs:
            tab = fallback_tab(available_tabs)
        return reverse('search:results') + '?' + urlencode({'q': terms, 'tab': tab})
    url_tpl = _user_bang(user, trigger) or _load().get(trigger)
    if not url_tpl:
        return None
    return url_tpl.replace('{{{s}}}', quote_plus(terms))


def count() -> int:
    """Number of generic (external) bang triggers shipped in ``bangs.json``.

    Used by the About page to tell users how many shortcuts are searchable.
    Returns 0 when the data file hasn't been fetched yet (``fetch_bangs``).
    """
    return len(_load())


# Most rows the About-page search ever returns (shared by the server-side
# fallback and the client-side about.js, which keeps the same cap).
SEARCH_LIMIT = 30


def _hostname(url_tpl: str) -> str:
    """Best-effort display host for a bang URL template (scheme + ``www.`` dropped)."""
    host = (urlsplit(url_tpl.replace('{{{s}}}', '')).hostname or '').lower()
    return host.removeprefix('www.')


def search(query: str, user=None, limit: int = SEARCH_LIMIT) -> tuple[list[dict], int]:
    """Filter the bang map for *query*, the server-side twin of ``about.js``.

    Powers the About page's bang search when JavaScript is unavailable. Matches
    on the trigger (exact > prefix > substring) and then the target hostname,
    using the same ranking as the client-side box so both paths agree. The
    signed-in *user*'s own custom bangs are folded in and flagged (and override
    a generic trigger of the same name). Returns ``(rows, total)`` where *rows*
    holds at most *limit* result dicts and *total* is the full match count.
    """
    q = query.strip().lower().lstrip('!')
    if not q:
        return [], 0

    entries = dict(_load())
    custom = set()
    if user is not None and getattr(user, 'is_authenticated', False):
        from .models import CustomBang
        for trigger, url_tpl in CustomBang.objects.filter(user=user).values_list('trigger', 'url_template'):
            trigger = trigger.lower()
            entries[trigger] = url_tpl  # a custom bang shadows the generic one
            custom.add(trigger)

    scored = []
    for trigger, url_tpl in entries.items():
        pos = trigger.find(q)
        if trigger == q:
            score = 0
        elif pos == 0:
            score = 1
        elif pos > 0:
            score = 2
        elif q in _hostname(url_tpl):
            score = 3
        else:
            continue
        scored.append((score, len(trigger), trigger, url_tpl))

    scored.sort(key=lambda row: (row[0], row[1], row[2]))
    rows = [
        {
            'trigger': trigger,
            'url': url_tpl,
            'hostname': _hostname(url_tpl),
            'custom': trigger in custom,
        }
        for _, _, trigger, url_tpl in scored[:limit]
    ]
    return rows, len(scored)


def lucky_terms(query: str) -> str | None:
    """If *query* contains a standalone ``!`` bang, return the remaining terms.

    Returns None when there is no lucky bang or no terms to search for.
    """
    if '!' not in query or not _LUCKY_RE.search(query):
        return None
    return _LUCKY_RE.sub(' ', query).strip() or None
