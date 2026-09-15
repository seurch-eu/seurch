"""Provider health tracking, powers the ``/status`` page."""

import logging
import time

from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .db import bound_statement_timeout

logger = logging.getLogger(__name__)

# How a provider's status is determined, shown on the page and stored as the
# row's ``source``.
MONITOR_PROBE = 'probe'   # active call to a free health endpoint
MONITOR_QUERY = 'query'   # observed from the outcome of real searches


def _key(name):
    """A `configured` check that's true when the named setting is non-empty."""
    return lambda: bool(getattr(settings, name, ''))


def _always():
    return True


PROVIDERS = {
    # Web search engines.
    'brave':         {'name': 'Brave',          'flag': '🇺🇸',  'group': 'engine',    'monitor': MONITOR_QUERY, 'configured': _key('BRAVE_API_KEY')},
    'mojeek':        {'name': 'Mojeek',         'flag': '🇬🇧',  'group': 'engine',    'monitor': MONITOR_QUERY, 'configured': _key('MOJEEK_API_KEY')},
    'marginalia':    {'name': 'Marginalia',     'flag': '🇸🇪',  'group': 'engine',    'monitor': MONITOR_QUERY, 'configured': _key('MARGINALIA_API_KEY')},
    'staan':         {'name': 'Staan',          'flag': '🇪🇺',  'group': 'engine',    'monitor': MONITOR_QUERY, 'configured': _key('STAAN_API_KEY')},
    # Media & news providers blended into the result tabs.
    'pixabay':       {'name': 'Pixabay',        'flag': '🇩🇪',  'group': 'media',     'monitor': MONITOR_QUERY, 'configured': _key('PIXABAY_API_KEY')},
    'sepia':         {'name': 'Sepia',          'flag': '🇪🇺',  'group': 'media',     'monitor': MONITOR_QUERY, 'configured': _always},
    'worldnews':     {'name': 'World News API', 'flag': '🇩🇪',  'group': 'media',     'monitor': MONITOR_QUERY, 'configured': _key('WORLDNEWS_API_KEY')},
    # Knowledge panels beside web results.
    'wikipedia':     {'name': 'Wikipedia',      'flag': '🌍',  'group': 'cards',     'monitor': MONITOR_QUERY, 'configured': _always},
    'wikidata':      {'name': 'Wikidata',       'flag': '🌍',  'group': 'cards',     'monitor': MONITOR_QUERY, 'configured': _always},
    'thetvdb':       {'name': 'TheTVDB',        'flag': '🇺🇸',  'group': 'cards',     'monitor': MONITOR_QUERY, 'configured': _key('THETVDB_API_KEY')},
    'tripadvisor':   {'name': 'TripAdvisor',    'flag': '🇺🇸',  'group': 'cards',     'monitor': MONITOR_QUERY, 'configured': _key('TRIPADVISOR_API_KEY')},
    'stackexchange': {'name': 'Stack Exchange', 'flag': '🇺🇸',  'group': 'cards',     'monitor': MONITOR_QUERY, 'configured': _always},
    # Instant answers. Both are free/keyless and the `instant` app may not import
    # `search` (it sits below it), so rather than instrument them from inside
    # `instant` we check them with a free active probe from `run_probes` below.
    'weather':       {'name': 'Open-Meteo',     'flag': '🇨🇭',  'group': 'instant',   'monitor': MONITOR_PROBE, 'configured': _always},
    'currency':      {'name': 'Frankfurter',    'flag': '🇪🇺',  'group': 'instant',   'monitor': MONITOR_PROBE, 'configured': _always},
    # Maps & translation, both expose a free health endpoint we probe directly.
    'openstreetmap': {'name': 'OpenStreetMap',  'flag': '🌍',  'group': 'maps',      'monitor': MONITOR_PROBE, 'configured': _always},
    'translate':     {'name': 'LibreTranslate', 'flag': '🇫🇷',  'group': 'translate', 'monitor': MONITOR_PROBE, 'configured': _key('LIBRETRANSLATE_URL')},
}

GROUPS = (
    ('engine',    _('Search engines')),
    ('media',     _('Media & news')),
    ('cards',     _('Knowledge panels')),
    ('instant',   _('Instant answers')),
    ('maps',      _('Maps')),
    ('translate', _('Translation')),
)


# --------------------------------------------------------------------------- #
# Recording (write side), called from the per-provider request helpers.
# --------------------------------------------------------------------------- #

# In-process write throttle.
_THROTTLE_SECONDS = 60
_last_write = {}  # provider -> (ok: bool, monotonic_ts: float)


def _should_write(provider, ok):
    prev = _last_write.get(provider)
    now = time.monotonic()
    if prev is None or prev[0] != ok or (now - prev[1]) >= _THROTTLE_SECONDS:
        _last_write[provider] = (ok, now)
        return True
    return False


def _record(provider, ok, source, detail=''):
    """Persist the latest outcome for *provider*. Best-effort; never raises."""
    if provider not in PROVIDERS or not _should_write(provider, ok):
        return
    try:
        from .models import ProviderStatus
        defaults = {'ok': ok, 'source': source, 'last_error': '' if ok else (detail or '')[:300]}
        if ok:
            defaults['last_ok_at'] = timezone.now()
        with transaction.atomic():
            bound_statement_timeout()
            ProviderStatus.objects.update_or_create(provider=provider, defaults=defaults)
    except DatabaseError:
        logger.debug('provider status write skipped for %s (database error)', provider, exc_info=True)
    except Exception:
        logger.debug('provider status write failed for %s', provider, exc_info=True)


def record_ok(provider, source=MONITOR_QUERY):
    """Note that *provider* just answered successfully."""
    _record(provider, True, source)


def record_down(provider, detail='', source=MONITOR_QUERY):
    """Note that *provider* just failed (with an optional short *detail*)."""
    _record(provider, False, source, detail)


# --------------------------------------------------------------------------- #
# Active probes, only for providers with a free, public health endpoint.
# --------------------------------------------------------------------------- #

def _probe_openstreetmap():
    import httpx

    from maps.services import NOMINATIM_API_BASE

    from .clients import USER_AGENT
    with httpx.Client(timeout=8) as client:
        r = client.get(
            f'{NOMINATIM_API_BASE}/status.php',
            params={'format': 'json'},
            headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'},
        )
        r.raise_for_status()
        data = r.json()
    # status.php returns {"status": 0, "message": "OK"} when healthy; any other
    # status code means the geocoding database is degraded.
    if (data or {}).get('status') != 0:
        raise RuntimeError((data or {}).get('message') or 'status not OK')


def _probe_translate():
    import httpx

    from translate.services import _base_url
    base = _base_url()
    with httpx.Client(timeout=8) as client:
        r = client.get(f'{base}/languages', headers={'Accept': 'application/json'})
        r.raise_for_status()
        if not r.json():
            raise RuntimeError('empty language list')


def _probe_weather():
    # Open-Meteo has no dedicated health endpoint, but its geocoding API is free
    # and keyless, so a single lookup is a fine (free) liveness check. Importing
    # from `instant` here is allowed, `search` already sits above `instant`.
    from instant.weather import _geocode
    if _geocode('Paris') is None:
        raise RuntimeError('geocoding request failed')


def _probe_currency():
    # Frankfurter (ECB data) is free and keyless; fetch the live rate table.
    from instant.currency import _fetch_rates
    if _fetch_rates() is None:
        raise RuntimeError('rate request failed')


_PROBES = {
    'openstreetmap': _probe_openstreetmap,
    'translate': _probe_translate,
    'weather': _probe_weather,
    'currency': _probe_currency,
}


def run_probes():
    """Actively check every provider that exposes a free health endpoint."""
    results = {}
    for slug, probe in _PROBES.items():
        if not PROVIDERS[slug]['configured']():
            continue
        t0 = time.monotonic()
        try:
            probe()
        except Exception as exc:
            logger.warning('provider probe %s down (%.2fs): %s', slug, time.monotonic() - t0, exc)
            record_down(slug, str(exc), source=MONITOR_PROBE)
            results[slug] = False
        else:
            logger.debug('provider probe %s ok (%.2fs)', slug, time.monotonic() - t0)
            record_ok(slug, source=MONITOR_PROBE)
            results[slug] = True
    return results


# --------------------------------------------------------------------------- #
# Read side, rendering the status page.
# --------------------------------------------------------------------------- #

def _flag(slug):
    """Provider flag, LibreTranslate's reflects where the instance is hosted."""
    if slug == 'translate':
        from . import preferences
        return preferences.translate_flag()
    return PROVIDERS[slug]['flag']


def _status_rows():
    """``{provider: ProviderStatus}`` for every recorded provider."""
    from .models import ProviderStatus
    return {r.provider: r for r in ProviderStatus.objects.all()}


def configured_providers():
    """Slugs of the providers set up on this deployment, in registry order."""
    return [slug for slug, meta in PROVIDERS.items() if meta['configured']()]


def provider_statuses():
    """Status of every configured provider, in registry order."""
    try:
        rows = _status_rows()
    except DatabaseError:
        rows = {}
    out = []
    for slug in configured_providers():
        meta = PROVIDERS[slug]
        row = rows.get(slug)
        out.append({
            'slug': slug,
            'name': meta['name'],
            'flag': _flag(slug),
            'group': meta['group'],
            'monitor': meta['monitor'],
            'state': ('up' if row.ok else 'down') if row else 'unknown',
            'checked_at': getattr(row, 'checked_at', None),
            'last_ok_at': getattr(row, 'last_ok_at', None),
            'last_error': getattr(row, 'last_error', '') or '',
            'source': getattr(row, 'source', '') or '',
        })
    return out


def status_page_context():
    """Template context for the status page: ordered groups + an overall summary."""
    statuses = provider_statuses()
    by_group = {}
    for s in statuses:
        by_group.setdefault(s['group'], []).append(s)
    groups = [
        {'key': key, 'label': label, 'items': by_group[key]}
        for key, label in GROUPS
        if key in by_group
    ]
    down = sum(1 for s in statuses if s['state'] == 'down')
    operational = sum(1 for s in statuses if s['state'] == 'up')
    total = len(statuses)
    if down:
        overall = 'issues'
    elif operational:
        overall = 'operational'
    else:
        overall = 'pending'
    return {
        'groups': groups,
        'summary': {
            'total': total,
            'down': down,
            'operational': operational,
            'unknown': total - down - operational,
            'all_ok': down == 0,
            'state': overall,
        },
    }


# --------------------------------------------------------------------------- #
# Monitor endpoint, the machine-readable sibling of the status page.
# --------------------------------------------------------------------------- #

def monitor_scope():
    """Provider slugs the ``/status/health`` endpoint watches."""
    configured = configured_providers()
    wanted = {w.strip() for w in getattr(settings, 'STATUS_MONITOR_PROVIDERS', []) or [] if w.strip()}
    if not wanted:
        return configured
    return [
        slug for slug in configured
        if slug in wanted or PROVIDERS[slug]['group'] in wanted
    ]


def provider_monitor_report(slug):
    """``(ok, payload)`` for one provider's own monitor endpoint."""
    if slug not in PROVIDERS or not PROVIDERS[slug]['configured']():
        return None
    try:
        row = _status_rows().get(slug)
    except DatabaseError:
        logger.warning('monitor: database unavailable', exc_info=True)
        return False, {'status': 'error', 'provider': slug, 'detail': 'database unavailable'}
    state = ('up' if row.ok else 'down') if row else 'unknown'
    return state != 'down', {
        'status': 'down' if state == 'down' else 'ok',
        'provider': slug,
        'state': state,
        'checked_at': getattr(row, 'checked_at', None),
        'last_ok_at': getattr(row, 'last_ok_at', None),
    }


def monitor_report():
    """Health verdict for an external uptime monitor: ``(ok, payload)``."""
    watched = monitor_scope()
    if not watched:
        logger.warning(
            'monitor: STATUS_MONITOR_PROVIDERS=%r matches no configured provider',
            list(getattr(settings, 'STATUS_MONITOR_PROVIDERS', []) or []),
        )
        return False, {'status': 'error', 'detail': 'no providers watched'}
    try:
        rows = _status_rows()
    except DatabaseError:
        logger.warning('monitor: database unavailable', exc_info=True)
        return False, {'status': 'error', 'detail': 'database unavailable'}
    down = [slug for slug in watched if slug in rows and not rows[slug].ok]
    operational = sum(1 for slug in watched if slug in rows and rows[slug].ok)
    return not down, {
        'status': 'down' if down else 'ok',
        'watched': len(watched),
        'operational': operational,
        'down': down,
        'unknown': len(watched) - len(down) - operational,
    }
