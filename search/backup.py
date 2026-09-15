"""Export / import / cloud-sync of a user's full settings document."""

import re
from urllib.parse import urlparse

from django.db import transaction

from . import preferences
from .models import BlockedSite, CustomBang
from .services import normalize_domain

DOCUMENT_VERSION = 1

REV_COOKIE_NAME = 'seurch_prefs_rev'

_TRIGGER_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]*$')


def valid_bang_url(url):
    """True when *url* is acceptable as a custom-bang template."""
    if '<' in url or '>' in url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ('http', 'https') and bool(parsed.netloc)


def revision(saved):
    """Cookie-safe stamp identifying a snapshot version (owner + last write time)."""
    return f'{saved.user_id}.{saved.updated_at:%Y%m%d%H%M%S%f}'


def stamp_revision(response, saved):
    """Mark *response*'s device as having seen snapshot *saved*."""
    response.set_cookie(
        REV_COOKIE_NAME, revision(saved),
        max_age=preferences.COOKIE_MAX_AGE, samesite='Lax',
    )


def build_document(request, prefs=None):
    """Snapshot the signed-in user's full settings as a serialisable dict."""
    if prefs is None:
        prefs = preferences.load(request)
    return {
        'version': DOCUMENT_VERSION,
        'preferences': prefs,
        'custom_bangs': [
            {'trigger': b.trigger, 'url_template': b.url_template}
            for b in CustomBang.objects.filter(user=request.user)
        ],
        'blocked_sites': [s.domain for s in BlockedSite.objects.filter(user=request.user)],
    }


def sync_to_db(request, response=None, prefs=None):
    """Write the current settings document to UserSettings for cross-device sync.

    Called automatically after every settings change so every other device
    picks the new state up on its next request. Pass *response* to also stamp
    the writing device with the new revision, otherwise its next request
    would needlessly re-fetch the snapshot it just wrote. Returns the saved
    ``UserSettings`` row.
    """
    from .models import UserSettings
    doc = build_document(request, prefs=prefs)
    saved, _ = UserSettings.objects.update_or_create(user=request.user, defaults={'document': doc})
    if response is not None:
        stamp_revision(response, saved)
    return saved


def _clean_bangs(raw):
    """Validate raw bang entries → ``{trigger: url_template}`` (last wins per trigger)."""
    out = {}
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        trigger = str(entry.get('trigger', '')).strip().lstrip('!').lower()[:32]
        url = str(entry.get('url_template', '')).strip()
        if _TRIGGER_RE.match(trigger) and '{{{s}}}' in url and valid_bang_url(url):
            out[trigger] = url[:500]
    return out


def _clean_sites(raw):
    """Validate raw blocked-site entries → ordered, de-duplicated domain list."""
    out = []
    if not isinstance(raw, list):
        return out
    seen = set()
    for entry in raw:
        domain = normalize_domain(str(entry))[:255]
        if domain and '.' in domain and domain not in seen:
            seen.add(domain)
            out.append(domain)
    return out


def apply_document(request, response, doc):
    """Validate *doc* and apply it for the signed-in user."""
    if not isinstance(doc, dict):
        raise ValueError('Settings file is not a valid settings document.')  # noqa: TRY004

    prefs = preferences.coerce(doc.get('preferences'))
    bangs = _clean_bangs(doc.get('custom_bangs'))
    sites = _clean_sites(doc.get('blocked_sites'))

    with transaction.atomic():
        CustomBang.objects.filter(user=request.user).delete()
        CustomBang.objects.bulk_create([
            CustomBang(user=request.user, trigger=trigger, url_template=url)
            for trigger, url in bangs.items()
        ])
        BlockedSite.objects.filter(user=request.user).delete()
        BlockedSite.objects.bulk_create([
            BlockedSite(user=request.user, domain=domain) for domain in sites
        ])

    # Only persist the cookie once the database writes have committed.
    preferences.apply_to_response(response, prefs)
    sync_to_db(request, response, prefs=prefs)
    return prefs
