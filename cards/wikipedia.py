import logging
import re
import time

import httpx

from cards import wikidata
from cards.cache import cached_card
from cards.relevance import _fold_accents, _name_is_relevant, _title_match
from search import health
from search.clients import USER_AGENT

logger = logging.getLogger(__name__)


# How many search hits to consider when picking the article closest to the query.
_SEARCH_LIMIT = 5

# Explicit sexual terms used to hide a Wikipedia card under safe search when the
# *article title itself* names one, a cheap, network-free signal that catches
# the common case (a search resolving to an explicitly-named page such as
# "Fellatio") without a Wikidata round-trip, and the floor that still fires when
# the Wikidata adult-check is unreachable. Stored accent-folded and lowercase
# (matched against `_fold_accents(title)`); the Latinate forms cover several of
# the seven UI languages at once. Scoped to sexual acts, pornography and sex
# toys, not bare anatomy, and matched whole-word, so precision stays high
# ("Sussex", "Essex", "Scunthorpe" never match).
_EXPLICIT_TERMS = frozenset({
    # Sexual acts
    'fellatio', 'fellatie', 'fellation', 'felacion', 'cunnilingus', 'anilingus',
    'masturbation', 'masturbatie', 'masturbacion', 'masturbazione', 'masturbacao',
    'orgasm', 'orgasme', 'orgasmus', 'orgasmo',
    'ejaculation', 'ejaculatie', 'ejakulation', 'eyaculacion', 'eiaculazione',
    'ejaculacao', 'coitus', 'coito', 'sodomy', 'sodomie', 'sodomia',
    'blowjob', 'handjob', 'deepthroat', 'creampie', 'gangbang', 'bukkake',
    'fisting', 'pompino',
    'oral sex', 'anal sex', 'group sex', 'orale seks', 'anale seks', 'groepsseks',
    'sexe oral', 'sexe anal', 'sexo oral', 'sexo anal', 'sesso orale', 'sesso anale',
    'oralverkehr', 'analverkehr', 'geschlechtsverkehr',
    # Pornography / erotica
    'pornography', 'pornographic', 'pornographie', 'pornografie', 'pornografia',
    'porno', 'porn', 'erotica', 'erotik', 'erotisme', 'erotismo',
    # Sex toys / kink (unambiguous)
    'dildo', 'bdsm', 'sadomasochism', 'sadomasochisme',
})

_EXPLICIT_RE = re.compile(
    r'(?<!\w)(?:' + '|'.join(re.escape(t) for t in sorted(_EXPLICIT_TERMS, key=len, reverse=True)) + r')(?!\w)'
)


def _looks_explicit(text):
    """True when *text* (an article title) names an explicit sexual term."""
    return bool(_EXPLICIT_RE.search(_fold_accents(text or '')))


def _is_explicit(card):
    """True when a Wikipedia *card* is sexually explicit and should be hidden
    under safe search.
    """
    return _looks_explicit(card.get('title', '')) or wikidata.is_adult_subject(card.get('wikibase_item'))


def fetch_wikipedia(query, lang='', safe_search='on'):
    """Return a Wikipedia summary card dict if the query matches a standard article, else None."""
    card = cached_card('wikipedia', query, lang, lambda: _fetch_wikipedia(query, lang))
    if card and safe_search != 'off' and _is_explicit(card):
        logger.debug('wikipedia drop: explicit subject %r hidden by safe search=%s',
                     card.get('title'), safe_search)
        return None
    return card


def _closest_hit(search_hits, query):
    """Pick the search hit closest to the query, or None when even the closest
    doesn't match."""
    scored = []
    for i, hit in enumerate(search_hits):
        names = [n for n in (hit.get('title'), hit.get('redirecttitle')) if n]
        score = max((_title_match(name, query) for name in names), default=0.0)
        scored.append((score, -i, hit, names))

    _, _, best, names = max(scored)
    if not any(_name_is_relevant(name, query) for name in names):
        logger.debug('wikipedia drop: %r not pertinent to query=%r',
                     best.get('title'), query)
        return None
    return best


def _fetch_wikipedia(query, lang=''):
    """Fetch a Wikipedia summary card from the MediaWiki API (uncached)."""
    wiki_lang = lang if lang else 'en'
    base = f'https://{wiki_lang}.wikipedia.org'
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=5, follow_redirects=True) as client:
            headers = {'User-Agent': USER_AGENT}

            # Candidate article titles via the MediaWiki search API.
            # `redirecttitle` reports the redirect a hit matched through, so a
            # query in another language still scores against what it typed.
            search_r = client.get(
                f'{base}/w/api.php',
                params={
                    'action': 'query',
                    'list': 'search',
                    'srsearch': query,
                    'format': 'json',
                    'srlimit': _SEARCH_LIMIT,
                    'srinfo': '',
                    'srprop': 'redirecttitle',
                },
                headers=headers,
            )
            search_r.raise_for_status()
            health.record_ok('wikipedia')
            search_hits = search_r.json().get('query', {}).get('search', [])
            if not search_hits:
                return None

            best = _closest_hit(search_hits, query)
            if best is None:
                return None
            title = best['title']

            summary_r = client.get(
                f'{base}/api/rest_v1/page/summary/{title.replace(" ", "_")}',
                headers=headers,
            )
            summary_r.raise_for_status()
            data = summary_r.json()

            if data.get('type') != 'standard':
                return None

            logger.debug('wikipedia ok lang=%s title=%r (%.2fs)', wiki_lang, title, time.monotonic() - t0)
            image = _pick_image(data.get('originalimage'), data.get('thumbnail'))
            # Add non-free images
            if not image:
                image = (
                    _fetch_page_image(client, base, title, headers)
                    or _fetch_lead_media_image(client, base, title, headers)
                )
            return {
                'title': data.get('title', ''),
                'description': data.get('description', ''),
                'extract': data.get('extract', ''),
                'url': data.get('content_urls', {}).get('desktop', {}).get('page', ''),
                'thumbnail': image,
                'wikibase_item': data.get('wikibase_item', ''),
            }
    except Exception as exc:
        logger.warning('wikipedia error (%.2fs): %s', time.monotonic() - t0, exc)
        health.record_down('wikipedia', str(exc))
        return None


def _pick_image(original, thumb):
    """Choose a card image URL from PageImages-style ``original``/``thumbnail``
    objects, preferring the original unless it is unreasonably large."""
    original = original or {}
    thumb = thumb or {}
    if original.get('source') and original.get('width', 9999) <= 2000:
        return original['source']
    return thumb.get('source', '')


def _fetch_page_image(client, base, title, headers):
    """Return the URL of an article's lead image via the Action API, or ''."""
    try:
        r = client.get(
            f'{base}/w/api.php',
            params={
                'action': 'query',
                'prop': 'pageimages',
                'piprop': 'original|thumbnail',
                'pithumbsize': 640,
                'pilicense': 'any',
                'titles': title,
                'redirects': 1,
                'format': 'json',
            },
            headers=headers,
        )
        r.raise_for_status()
        pages = r.json().get('query', {}).get('pages', {})
        for page in pages.values():
            image = _pick_image(page.get('original'), page.get('thumbnail'))
            if image:
                return image
    except Exception as exc:
        logger.warning('wikipedia pageimage error: %s', exc)
    return ''


def _fetch_lead_media_image(client, base, title, headers):
    """Return the URL of the page's first rendered image via the REST
    media-list endpoint, or ''."""
    try:
        r = client.get(
            f'{base}/api/rest_v1/page/media-list/{title.replace(" ", "_")}',
            headers=headers,
        )
        r.raise_for_status()
        for item in r.json().get('items', []):
            if item.get('type') != 'image':
                continue
            srcset = item.get('srcset') or []
            if not srcset:
                continue
            # srcset is ordered by ascending scale; take the sharpest variant.
            src = srcset[-1].get('src', '')
            if src:
                return f'https:{src}' if src.startswith('//') else src
    except Exception as exc:
        logger.warning('wikipedia media-list error: %s', exc)
    return ''
