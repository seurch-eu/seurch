"""User search preferences, stored client-side in a single JSON cookie."""

import json
from urllib.parse import quote, unquote

from django.utils.translation import gettext_lazy as _

from .clients import LANG_COUNTRY, REAL_ENGINES

COOKIE_NAME = 'seurch_prefs'
COOKIE_MAX_AGE = 60 * 60 * 24 * 400  # ~13 months

# Country flag shown next to each engine in Settings → Engines, reflecting
# where the engine/index is operated from.
ENGINE_FLAGS = {
    'brave': '🇺🇸',
    'mojeek': '🇬🇧',
    'marginalia': '🇸🇪',
    'staan': '🇪🇺',
}
# Brand names (never translated) and the one-line description shown beside each
# engine toggle.
ENGINE_LABELS = {
    'brave': 'Brave', 'mojeek': 'Mojeek', 'marginalia': 'Marginalia', 'staan': 'Staan',
}
ENGINE_DESCRIPTIONS = {
    'brave': _('Privacy-focused engine with its own index. Also powers search-bar autocomplete.'),
    'mojeek': _('Independent engine with its own index. Web search only.'),
    'marginalia': _('Non-commercial, small-web focused index. Web search only.'),
    'staan': _('European index built by Qwant and Ecosia. Web search only.'),
}
# Safe search is a simple on/off toggle (on by default); coerce() sends any
# other value to the default.
SAFE_CHOICES = ('off', 'on')
LANG_CHOICES = ('auto', 'en', 'fr', 'de', 'es', 'it', 'pt', 'nl')
UI_LANG_CHOICES = ('auto', 'en', 'fr', 'de', 'es', 'it', 'pt', 'nl')
THEME_CHOICES = ('system', 'light', 'dark')

# Toggleable supplementary data sources: (key, label, description).
SOURCES = (
    ('wikipedia', _('Wikipedia'), _('Encyclopedia summary panel beside web results.')),
    ('thetvdb', _('TheTVDB'), _('Film and TV-show panel.')),
    ('tripadvisor', _('TripAdvisor'), _('Restaurant, hotel and attraction panel.')),
    ('stackexchange', _('Stack Exchange'), _('Top question-and-answer panel.')),
    ('weather', _('Open-Meteo'), _('Weather instant answer.')),
    ('pixabay', _('Pixabay'), _('Royalty-free photos blended into the Images tab.')),
    ('translate', _('Translate'), _('Text translation tab powered by LibreTranslate.')),
    ('sepia', _('Sepia'), _('PeerTube-based video search blended into the Videos tab.')),
    ('worldnews', _('World News API'), _('News articles blended into the News tab.')),
    ('openstreetmap', _('OpenStreetMap'), _('Maps and place search in the Maps tab.')),
)
SOURCE_KEYS = tuple(key for key, _label, _desc in SOURCES)
SOURCE_LABELS = {key: label for key, label, _desc in SOURCES}
SOURCE_DESCRIPTIONS = {key: desc for key, _label, desc in SOURCES}

# Country flag shown next to each data source in Settings → Engines.
SOURCE_FLAGS = {
    'wikipedia': '🇺🇸',
    'thetvdb': '🇺🇸',
    'tripadvisor': '🇺🇸',
    'stackexchange': '🇺🇸',
    'weather': '🇨🇭',
    'pixabay': '🇩🇪',
    'translate': '🇫🇷',
    'sepia': '🇪🇺',
    'worldnews': '🇩🇪',
    'openstreetmap': '🌍',
}

# Engines and data sources whose underlying project is open source.
OPEN_SOURCE = frozenset({
    'marginalia',
    'sepia',
    'wikipedia',
    'weather',
    'translate',
    'openstreetmap',
})

# Providers whose API is metered and billed by query volume, shown as a "Paid"
# badge in Settings → Engines. 
DEFAULT_PAID_PROVIDERS = frozenset({
    'brave',
    'mojeek',
    'staan',
    'worldnews',
})

# The search types a user configures providers for, in the order Settings →
# Engines.
SEARCH_TYPES = (
    ('web', _('Web'), 'fa-magnifying-glass'),
    ('images', _('Images'), 'fa-image'),
    ('news', _('News'), 'fa-newspaper'),
    ('videos', _('Videos'), 'fa-video'),
    ('maps', _('Maps'), 'fa-map-location-dot'),
    ('translate', _('Translate'), 'fa-language'),
)
SEARCH_TYPE_KEYS = tuple(key for key, _label, _icon in SEARCH_TYPES)

# Every provider each search type can involve, in display order: first the ones
# that produce that type's results, then the supplementary sources that enrich
# them.
TYPE_PROVIDERS = {
    'web': (*REAL_ENGINES, 'wikipedia', 'thetvdb', 'tripadvisor', 'stackexchange', 'weather'),
    'images': ('brave', 'pixabay'),
    'news': ('brave', 'worldnews'),
    'videos': ('brave', 'sepia'),
    'maps': ('openstreetmap',),
    'translate': ('translate',),
}

# The providers above that *decorate* a search type's results (the web tab's
# knowledge cards and its weather instant answer) rather than produce them.
ENRICHMENT_PROVIDERS = frozenset({
    'wikipedia', 'thetvdb', 'tripadvisor', 'stackexchange', 'weather',
})

# What a provider does *for one search type*, when that differs from its generic
# description above (Brave is a web index here, an image API there).
TYPE_PROVIDER_DESCRIPTIONS = {
    ('images', 'brave'): _("Brave's own image search."),
    ('news', 'brave'): _("Brave's own news search."),
    ('videos', 'brave'): _("Brave's own video search."),
}

def defaults():
    """A fresh preferences dict with every key at its default value."""
    return {
        'disabled_providers': {},
        'safe_search': 'on',
        'search_lang': 'auto',
        'ui_lang': 'auto',
        'theme': 'system',
        'open_links_new_tab': False,
        'proxy_images': False,
        'similar_images': True,
    }


def coerce(data):
    """Return a clean, fully-populated preferences dict from arbitrary input."""
    prefs = defaults()
    if not isinstance(data, dict):
        return prefs
    prefs['disabled_providers'] = _coerce_disabled_providers(data)
    if data.get('safe_search') in SAFE_CHOICES:
        prefs['safe_search'] = data['safe_search']
    if data.get('search_lang') in LANG_CHOICES:
        prefs['search_lang'] = data['search_lang']
    if data.get('ui_lang') in UI_LANG_CHOICES:
        prefs['ui_lang'] = data['ui_lang']
    if data.get('theme') in THEME_CHOICES:
        prefs['theme'] = data['theme']
    prefs['open_links_new_tab'] = bool(data.get('open_links_new_tab'))
    prefs['proxy_images'] = bool(data.get('proxy_images'))
    prefs['similar_images'] = bool(data.get('similar_images', True))
    return prefs


def _coerce_disabled_providers(data):
    """Validate the ``{search type: [provider keys]}`` mapping from *data*.

    Unknown search types and providers a type can't use are dropped, and each
    type's list is reordered to `TYPE_PROVIDERS` so the value is canonical
    whatever order it arrived in.
    """
    raw = data.get('disabled_providers')
    if not isinstance(raw, dict):
        return {}
    clean = {}
    for search_type in SEARCH_TYPE_KEYS:
        keys = raw.get(search_type)
        if not isinstance(keys, (list, tuple, set, frozenset)):
            continue
        chosen = set(keys)
        disabled = [p for p in TYPE_PROVIDERS[search_type] if p in chosen]
        if disabled:
            clean[search_type] = disabled
    return clean


def type_providers(search_type):
    """Every provider *search_type* can use, in display order."""
    return TYPE_PROVIDERS.get(search_type, ())


def disabled_for(prefs, search_type):
    """The set of providers switched off for *search_type*."""
    return set((prefs.get('disabled_providers') or {}).get(search_type, ()))


def is_enabled(prefs, search_type, provider):
    """True when *provider* is switched on for *search_type*."""
    return provider in type_providers(search_type) and provider not in disabled_for(prefs, search_type)


def enabled_engines(prefs, search_type='web'):
    """Search engines enabled for *search_type*, in canonical order."""
    usable = set(type_providers(search_type))
    disabled = disabled_for(prefs, search_type)
    return [e for e in REAL_ENGINES if e in usable and e not in disabled]


def tab_providers(tab):
    """Providers *tab* can query, in display order (empty for single-provider tabs)."""
    providers = tuple(p for p in type_providers(tab) if p not in ENRICHMENT_PROVIDERS)
    return providers if len(providers) > 1 else ()


def provider_label(key):
    """Brand name for a provider, whether it's an engine or a data source."""
    return ENGINE_LABELS.get(key) or SOURCE_LABELS.get(key, key)


def provider_flag(key):
    """Country flag for a provider, whether it's an engine or a data source."""
    if key == 'translate':
        return translate_flag()
    return ENGINE_FLAGS.get(key) or SOURCE_FLAGS.get(key, '')


def provider_description(search_type, key):
    """What *key* does for *search_type*, falling back to its generic description."""
    return TYPE_PROVIDER_DESCRIPTIONS.get((search_type, key)) or (
        ENGINE_DESCRIPTIONS.get(key) or SOURCE_DESCRIPTIONS.get(key, '')
    )


def enabled_providers(prefs, tab):
    """Result providers of *tab* the user hasn't switched off for that tab."""
    disabled = disabled_for(prefs, tab)
    return [p for p in tab_providers(tab) if p not in disabled]


def requested_scope(tab, requested):
    """The providers of *tab* a request explicitly scoped to, canonically ordered."""
    return [p for p in tab_providers(tab) if p in set(requested or ())]


def scope_providers(prefs, tab, requested):
    """Providers to query for one request on *tab*, honouring the search scope."""
    return requested_scope(tab, requested) or enabled_providers(prefs, tab)


def scope_engines(prefs, requested):
    """Engines to query for a web search, honouring the per-request search scope."""
    return scope_providers(prefs, 'web', requested)


def scope_view(tab, active):
    """Render-friendly provider list for *tab*'s search-scope picker."""
    chosen = set(active)
    paid = paid_providers()
    return [
        {
            'key': p, 'label': provider_label(p), 'flag': provider_flag(p),
            'checked': p in chosen, 'paid': p in paid,
        }
        for p in tab_providers(tab)
    ]


def encode(prefs):
    """Serialise *prefs* to the cookie string: URL-encoded JSON."""
    return quote(json.dumps(coerce(prefs), separators=(',', ':')))


def decode(raw):
    """Parse a cookie string back into a clean preferences dict (defaults if absent/garbage)."""
    if not raw:
        return defaults()
    try:
        return coerce(json.loads(unquote(raw)))
    except (ValueError, TypeError):
        return defaults()


def detect_browser_lang(request):
    """Primary language from the browser's Accept-Language header, or '' if unsupported/absent."""
    header = request.META.get('HTTP_ACCEPT_LANGUAGE', '')
    for tag in header.split(','):
        primary = tag.split(';')[0].strip().lower().split('-')[0]
        if primary in LANG_COUNTRY:
            return primary
    return ''


def load(request):
    """Current preferences for *request*, read from the cookie."""
    raw = request.COOKIES.get(COOKIE_NAME)
    if raw:
        return decode(raw)
    prefs = defaults()
    browser_lang = detect_browser_lang(request)
    if browser_lang:
        prefs['search_lang'] = browser_lang
    return prefs


def apply_to_response(response, prefs):
    """Persist *prefs* onto *response* as the preferences cookie; returns the cleaned prefs."""
    clean = coerce(prefs)
    response.set_cookie(COOKIE_NAME, encode(clean), max_age=COOKIE_MAX_AGE, samesite='Lax')
    _sync_language_cookie(response, clean.get('ui_lang', 'auto'))
    return clean


def _sync_language_cookie(response, ui_lang):
    """Set or clear Django's language cookie to match *ui_lang*."""
    from django.conf import settings as django_settings
    lang_cookie = django_settings.LANGUAGE_COOKIE_NAME
    if ui_lang and ui_lang != 'auto':
        response.set_cookie(lang_cookie, ui_lang, max_age=COOKIE_MAX_AGE, samesite='Lax')
    else:
        response.delete_cookie(lang_cookie)


def _flag_from_country_code(country_code):
    """Convert a two-letter ISO 3166-1 country code to its flag emoji, or '' if invalid."""
    code = (country_code or '').strip().lower()
    if len(code) != 2 or not code.isalpha():
        return ''
    return ''.join(chr(0x1F1E6 + ord(c) - ord('a')) for c in code)


def translate_flag():
    """Flag for LibreTranslate, from the deployment's declared origin country."""
    from django.conf import settings as django_settings
    return (
        _flag_from_country_code(django_settings.LIBRETRANSLATE_ORIGIN_COUNTRY)
        or SOURCE_FLAGS['translate']
    )


def paid_providers():
    """The providers this deployment marks as paid, as a set of provider keys."""
    from django.conf import settings as django_settings
    configured = django_settings.PAID_PROVIDERS
    if configured is None:
        return DEFAULT_PAID_PROVIDERS
    known = set(ENGINE_LABELS) | set(SOURCE_KEYS)
    return frozenset(name.strip().lower() for name in configured) & known


def _provider_view(prefs, search_type, key, key_missing, paid):
    """One provider toggle in Settings → Engines, for one search type."""
    return {
        'key': key,
        'label': provider_label(key),
        'desc': provider_description(search_type, key),
        'flag': provider_flag(key),
        'enabled': key not in disabled_for(prefs, search_type),
        'open_source': key in OPEN_SOURCE,
        'paid': key in paid,
        'key_missing': bool(key_missing.get(key)),
        'unavailable_label': (
            _('Translation server not configured') if key == 'translate'
            else _('API key not configured')
        ),
    }


def search_types_view(prefs, key_missing=None):
    """Render-friendly model of Settings → Engines: one group per search type."""
    key_missing = key_missing or {}
    paid = paid_providers()
    groups = []
    for search_type, label, icon in SEARCH_TYPES:
        providers = [
            _provider_view(prefs, search_type, key, key_missing, paid)
            for key in type_providers(search_type)
        ]
        groups.append({
            'key': search_type, 'label': label, 'icon': icon,
            'engines': [p for p in providers if p['key'] not in ENRICHMENT_PROVIDERS],
            'extras': [p for p in providers if p['key'] in ENRICHMENT_PROVIDERS],
            'enabled_count': sum(1 for p in providers if p['enabled']),
        })
    return groups
