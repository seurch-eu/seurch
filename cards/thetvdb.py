import logging
import re
import time
from datetime import timedelta

import httpx
from django.conf import settings

from cards import wikidata
from cards.cache import cached_card
from cards.relevance import (
    _is_movie_or_tv,
    _is_travel_query,
    _looks_like_title,
    _name_is_relevant,
    _title_match,
    _wiki_is_person,
    _wiki_is_place,
)
from search import health
from search.cache import _cache_get, _cache_set, _make_cache_key

logger = logging.getLogger(__name__)

THETVDB_API_BASE = 'https://api4.thetvdb.com/v4'
THETVDB_SITE = 'https://thetvdb.com'

_THETVDB_LANG = {
    'en': 'eng', 'fr': 'fra', 'de': 'deu', 'es': 'spa',
    'it': 'ita', 'pt': 'pt', 'nl': 'nld',
}

# A probe carries no explicit movie signal, so its best hit must match the
# query strictly before a card may surface.
_PROBE_MATCH_MIN = 0.6

# TheTVDB bearer tokens are valid for one month; refresh well before expiry.
# A token invalidated early (e.g. the key was reissued) is handled by the
# 401-retry in _api_get, so the long TTL can't lock the card out.
_TOKEN_TTL = timedelta(days=25)

# TheTVDB artwork type ids for full-width background art ("fanart"): 3 is the
# series background, 15 the movie background.
_BACKGROUND_ARTWORK_TYPES = {3, 15}

# Crew roles shown on the card, in display priority. TheTVDB models crew as
# `characters` entries whose peopleType names the role.
_CREW_TYPES = ('Creator', 'Showrunner', 'Director', 'Writer')


def fetch_thetvdb(query, wikipedia_card=None, web_results=None, lang=''):
    """Return a TheTVDB card dict for a movie/TV show query, or None."""
    return cached_card('thetvdb', query, lang, lambda: _fetch_thetvdb(
        query, wikipedia_card=wikipedia_card, web_results=web_results, lang=lang,
    ))


def _login(client, api_key):
    """POST /login for a fresh bearer token."""
    payload = {'apikey': api_key}
    pin = settings.THETVDB_PIN
    if pin:
        payload['pin'] = pin
    r = client.post(f'{THETVDB_API_BASE}/login', json=payload)
    r.raise_for_status()
    return r.json()['data']['token']


def _get_token(client, api_key, force_refresh=False):
    """Bearer token for the API, cached so we don't log in on every search."""
    key = _make_cache_key('thetvdb-token', api_key, '', 1, '', '', '')
    if not force_refresh:
        cached = _cache_get(key)
        if cached and cached.get('token'):
            return cached['token']
    token = _login(client, api_key)
    _cache_set(key, {'token': token}, ttl=_TOKEN_TTL)
    return token


def _api_get(client, api_key, path, params=None):
    """Authenticated GET returning the response's ``data`` payload.

    A 401 means the cached token was invalidated before its TTL (key reissued,
    subscription lapsed), so log in again once and retry.
    """
    token = _get_token(client, api_key)
    url = f'{THETVDB_API_BASE}{path}'
    r = client.get(url, params=params, headers={'Authorization': f'Bearer {token}'})
    if r.status_code == 401:
        token = _get_token(client, api_key, force_refresh=True)
        r = client.get(url, params=params, headers={'Authorization': f'Bearer {token}'})
    r.raise_for_status()
    return r.json().get('data')


def _pick_translation(records, lang3):
    """The translation record for *lang3*, falling back to English."""
    records = records or []
    for language in (lang3, 'eng'):
        for t in records:
            if t.get('language') == language:
                return t
    return {}


def _fetch_thetvdb(query, wikipedia_card=None, web_results=None, lang=''):
    """Fetch a TheTVDB card from the API (uncached)."""
    api_key = settings.THETVDB_API_KEY
    if not api_key:
        return None

    tags = wikidata.entity_tags((wikipedia_card or {}).get('wikibase_item'))
    # Check if this card is pertienent here
    if _is_travel_query(query, web_results, wikipedia_card, tags):
        return None
    detected = _is_movie_or_tv(query, web_results, wikipedia_card, tags)
    probe = (
        not detected
        and not _wiki_is_person(wikipedia_card, tags)
        and not _wiki_is_place(wikipedia_card, tags)
        and _looks_like_title(query, wikipedia_card)
    )
    if not detected and not probe:
        return None

    lang3 = _THETVDB_LANG.get(lang, 'eng')
    wiki_title = (wikipedia_card or {}).get('title') or ''
    # Search by the canonical Wikipedia title when we have one, dropping any
    # "(disambiguator)" so we don't query TheTVDB for "… (band)"; else the raw query.
    search_query = re.sub(r'\s*\([^)]*\)', '', wiki_title).strip() or query
    references = [r for r in (wiki_title, query) if r]

    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=8) as client:
            found = _api_get(client, api_key, '/search', params={
                'query': search_query,
                'limit': 20,
            }) or []
            health.record_ok('thetvdb')
            # The search index also covers people and companies; only film/TV
            # records can back this card.
            hits = [h for h in found if h.get('type') in ('movie', 'series')]
            if not hits:
                return None

            # Keep hits that are actually about the searched subject, then rank
            # by how closely a title matches the user's *query* (so an exact
            # title beats a sequel like "… 2" even when Wikipedia resolved to the
            # sequel)
            scored = []
            for pos, h in enumerate(hits):
                names = [h.get('name') or '', (h.get('translations') or {}).get(lang3) or '']
                names += [a for a in (h.get('aliases') or [])[:8] if a]
                names = [n for n in names if n]
                if not names or not any(_name_is_relevant(n, *references) for n in names):
                    continue
                match = max(_title_match(n, query) for n in names)
                scored.append((match, -pos, h))
            if not scored:
                logger.debug('thetvdb drop: no pertinent hit for query=%r', query)
                return None

            best_match, _, best = max(scored, key=lambda s: (s[0], s[1]))
            # A probe had no explicit movie signal, so demand a strict title
            # match, otherwise a non-film named entity could surface a card.
            if probe and best_match < _PROBE_MATCH_MIN:
                logger.debug('thetvdb probe drop: %r weakly matches query=%r',
                             best.get('name'), query)
                return None

            media_type = best['type']
            collection = 'movies' if media_type == 'movie' else 'series'
            d = _api_get(
                client, api_key, f'/{collection}/{best["tvdb_id"]}/extended',
                params={'meta': 'translations'},
            ) or {}

        translations = d.get('translations') or {}
        name_tr = _pick_translation(translations.get('nameTranslations'), lang3)
        overview_tr = _pick_translation(translations.get('overviewTranslations'), lang3)
        title = name_tr.get('name') or d.get('name', '')
        overview = overview_tr.get('overview') or d.get('overview', '')

        year = d.get('year') or ''
        if not year:
            date = d.get('firstAired') or (d.get('first_release') or {}).get('date') or ''
            year = date[:4]
        genres = [g['name'] for g in (d.get('genres') or [])[:3]]

        poster = d.get('image') or ''
        backdrop = next(
            (a['image'] for a in (d.get('artworks') or [])
             if a.get('type') in _BACKGROUND_ARTWORK_TYPES and a.get('image')),
            '',
        )

        characters = d.get('characters') or []

        # Cast: top 8 billed actors
        actors = sorted(
            (c for c in characters if c.get('peopleType') == 'Actor'),
            key=lambda c: c.get('sort') or 999,
        )
        cast = [
            {
                'name': c.get('personName', ''),
                'character': c.get('name') or '',
                'profile': c.get('personImgURL') or c.get('image') or '',
            }
            for c in actors[:8] if c.get('personName')
        ]

        # Crew: creators/showrunners (TV) + directors + writers, capped at 4
        # unique people, in _CREW_TYPES priority order.
        crew = []
        seen = set()
        for people_type in _CREW_TYPES:
            for c in characters:
                if len(crew) >= 4:
                    break
                name = c.get('personName', '')
                if c.get('peopleType') == people_type and name and name not in seen:
                    crew.append({'name': name, 'job': people_type,
                                 'profile': c.get('personImgURL') or c.get('image') or ''})
                    seen.add(name)

        logger.debug('thetvdb ok media_type=%s title=%r cast=%d crew=%d (%.2fs)',
                     media_type, title, len(cast), len(crew), time.monotonic() - t0)
        return {
            'title': title,
            'year': year,
            'media_type': media_type,
            'overview': overview,
            'poster': poster,
            'backdrop': backdrop,
            'genres': genres,
            # Taglines only exist on movie translations (TheTVDB disallows one
            # without a title); series simply render without.
            'tagline': overview_tr.get('tagline', ''),
            'status': (d.get('status') or {}).get('name') or '',
            'cast': cast,
            'crew': crew,
            # Direct link to the record's own TheTVDB page; the card footer
            # (cards/_thetvdb.html) renders it plus the attribution TheTVDB's
            # terms require.
            'url': f'{THETVDB_SITE}/{collection}/{d.get("slug") or best.get("slug", "")}',
        }
    except Exception as exc:
        logger.warning('thetvdb error (%.2fs): %s', time.monotonic() - t0, exc)
        health.record_down('thetvdb', str(exc))
        return None
