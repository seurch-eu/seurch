"""Text translation via a self-hosted LibreTranslate instance."""

from django.conf import settings

from search.cache import _make_cache_key, cache_or_fetch
from search.clients import _provider_request

# Used when LibreTranslate is unreachable, so the language selectors still
# render something sensible.
FALLBACK_LANGUAGES = [
    ('en', 'English'), ('fr', 'French'), ('de', 'German'), ('es', 'Spanish'),
    ('it', 'Italian'), ('pt', 'Portuguese'), ('nl', 'Dutch'), ('ru', 'Russian'),
    ('ar', 'Arabic'), ('zh', 'Chinese'), ('ja', 'Japanese'), ('ko', 'Korean'),
    ('pl', 'Polish'), ('tr', 'Turkish'), ('uk', 'Ukrainian'),
]


def _base_url() -> str:
    return settings.LIBRETRANSLATE_URL.rstrip('/')


def _auth_params() -> dict:
    api_key = settings.LIBRETRANSLATE_API_KEY
    return {'api_key': api_key} if api_key else {}


def fetch_languages() -> list[tuple[str, str]]:
    """Return ``(code, name)`` pairs supported by LibreTranslate.

    Falls back to a static list if the service is unreachable.
    """
    base = _base_url()
    if not base:
        return FALLBACK_LANGUAGES

    def fetch():
        data = _provider_request('translate', f'{base}/languages')
        if data is None:
            return None
        return [{'code': lang['code'], 'name': lang['name']} for lang in data]

    key = _make_cache_key('translate-languages', '', '', 1, '', '', '')
    languages = cache_or_fetch(key, fetch)
    if languages is None:
        return FALLBACK_LANGUAGES
    return [(lang['code'], lang['name']) for lang in languages]


def fetch_translation(text: str, target_lang: str, source_lang: str = 'auto') -> dict | None:
    """Translate *text* to *target_lang* via LibreTranslate.

    Returns a dict with ``translated_text`` and ``detected_lang`` (empty
    unless *source_lang* is ``"auto"``), or ``None`` if the service is
    unavailable or misconfigured.
    """
    base = _base_url()
    if not base or not text or not target_lang:
        return None

    def fetch():
        data = _provider_request('translate', f'{base}/translate', json={
            'q': text,
            'source': source_lang,
            'target': target_lang,
            'format': 'text',
            **_auth_params(),
        })
        if data is None:
            return None
        return {
            'translated_text': data.get('translatedText', ''),
            'detected_lang': (data.get('detectedLanguage') or {}).get('language', ''),
        }

    key = _make_cache_key('translate', text, source_lang, 1, '', target_lang, '')
    return cache_or_fetch(key, fetch)
