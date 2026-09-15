"""Language-agnostic entity classification via Wikidata."""

import logging
import time

import httpx

from cards.relevance import _contains_word
from search import health
from search.cache import _cache_get, _cache_set, _make_cache_key
from search.clients import USER_AGENT

logger = logging.getLogger(__name__)

WIKIDATA_API_BASE = 'https://www.wikidata.org/w/api.php'


# Curated P31 class ids

_MOVIE_TV_CLASS_IDS = frozenset({
    'Q11424',     # film
    'Q24862',     # short film
    'Q93204',     # documentary film
    'Q202866',    # animated film
    'Q506240',    # television film
    'Q5398426',   # television series
    'Q581714',    # animated series
    'Q1259759',   # miniseries
    'Q526877',    # web series
    'Q15416',     # television program
})

_PERSON_CLASS_IDS = frozenset({
    'Q5',         # human
})

_TRAVEL_PLACE_CLASS_IDS = frozenset({
    'Q570116',    # tourist attraction
    'Q33506',     # museum
    'Q27686',     # hotel
    'Q11707',     # restaurant
    'Q194195',    # amusement park
    'Q46169',     # national park
    'Q23413',     # castle
    'Q16560',     # palace
    'Q483110',    # stadium
})

_PLACE_CLASS_IDS = frozenset({
    'Q515',       # city
    'Q1549591',   # big city
    'Q5119',      # capital city
    'Q3957',      # town
    'Q532',       # village
    'Q486972',    # human settlement
    'Q6256',      # country
    'Q8502',      # mountain
    'Q23397',     # lake
    'Q4022',      # river
    'Q23442',     # island
    'Q1248784',   # airport
    'Q12280',     # bridge
    'Q4989906',   # monument
})

# Wikidata class labels are fetched in English regardless of the article's
# language, so one vocabulary covers every query language. Matched whole-word.

_MOVIE_TV_LABEL_KEYWORDS = frozenset({
    'film', 'movie', 'television series', 'tv series', 'television program',
    'television show', 'miniseries', 'web series', 'anime', 'sitcom',
    'telenovela', 'soap opera', 'documentary',
})

# A class label naming the *industry* around films must not pass for a work:
# "film studio", "film festival", "film award", "film genre", …
_MOVIE_TV_LABEL_STOPWORDS = frozenset({
    'studio', 'company', 'organization', 'organisation', 'enterprise',
    'award', 'festival', 'genre', 'occupation', 'profession', 'magazine',
    'journal', 'website', 'database', 'character',
})

_TRAVEL_PLACE_LABEL_KEYWORDS = frozenset({
    'hotel', 'hostel', 'resort', 'restaurant', 'café', 'cafe', 'bar', 'pub',
    'museum', 'art gallery', 'tourist attraction', 'landmark', 'amusement park',
    'theme park', 'water park', 'national park', 'zoo', 'aquarium',
    'botanical garden', 'castle', 'palace', 'cathedral', 'basilica', 'church',
    'mosque', 'synagogue', 'temple', 'monastery', 'abbey', 'monument',
    'memorial', 'lighthouse', 'observation tower', 'skyscraper', 'stadium',
    'arena', 'opera house', 'theatre', 'theater', 'beach', 'spa', 'casino',
    'marina', 'pier', 'heritage site', 'archaeological site',
})

_PLACE_LABEL_KEYWORDS = frozenset({
    'city', 'town', 'village', 'settlement', 'commune', 'municipality',
    'locality', 'country', 'state', 'province', 'region', 'county',
    'district', 'prefecture', 'department', 'canton', 'borough', 'suburb',
    'neighborhood', 'neighbourhood', 'capital', 'metropolis', 'river', 'lake',
    'sea', 'ocean', 'bay', 'strait', 'mountain', 'volcano', 'hill', 'island',
    'peninsula', 'archipelago', 'desert', 'valley', 'glacier', 'forest',
    'park', 'garden', 'square', 'street', 'avenue', 'boulevard', 'bridge',
    'tower', 'airport', 'station', 'port', 'harbor', 'harbour', 'canal',
    'dam', 'road',
})

# Adult / sexually-explicit subject detection
# Drives safe-search hiding of the Wikipedia card (see cards.wikipedia)
_ADULT_LABEL_KEYWORDS = frozenset({
    'sexual activity', 'human sexual activity', 'sexual practice',
    'sexual intercourse', 'sexual penetration', 'sexual behavior',
    'sexual behaviour', 'sex position', 'sex act', 'sexual act',
    'oral sex', 'anal sex', 'group sex', 'sexual fetishism', 'paraphilia',
    'pornography', 'pornographic film', 'pornographic magazine',
    'pornographic genre', 'pornographic website', 'sex toy', 'erotica',
})


def entity_tags(wikibase_item):
    """Classify a Wikidata item into card-detection tags, or None."""
    if not wikibase_item:
        return None
    key = _make_cache_key('wikidata', wikibase_item, '', 1, '', '', '')
    cached = _cache_get(key)
    if cached is not None:
        return frozenset(cached.get('tags') or [])

    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=5) as client:
            class_ids = _instance_of(client, wikibase_item)
            labels = _class_labels(client, class_ids) if class_ids else []
        health.record_ok('wikidata')
    except Exception as exc:
        logger.warning('wikidata error item=%s (%.2fs): %s',
                       wikibase_item, time.monotonic() - t0, exc)
        health.record_down('wikidata', str(exc))
        return None

    tags = _classify(class_ids, labels)
    _cache_set(key, {'tags': sorted(tags)})
    logger.debug('wikidata ok item=%s classes=%d tags=%s (%.2fs)',
                 wikibase_item, len(class_ids), sorted(tags), time.monotonic() - t0)
    return frozenset(tags)


def is_adult_subject(wikibase_item):
    """True when a Wikidata subject is sexually explicit, else False."""
    if not wikibase_item:
        return False
    key = _make_cache_key('wikidata_adult', wikibase_item, '', 1, '', '', '')
    cached = _cache_get(key)
    if cached is not None:
        return bool(cached.get('adult'))

    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=5) as client:
            class_ids = _instance_of(client, wikibase_item) + _subclass_of(client, wikibase_item)
            labels = _class_labels(client, class_ids) if class_ids else []
        health.record_ok('wikidata')
    except Exception as exc:
        logger.warning('wikidata adult-check error item=%s (%.2fs): %s',
                       wikibase_item, time.monotonic() - t0, exc)
        health.record_down('wikidata', str(exc))
        return False

    adult = any(_contains_word(label, _ADULT_LABEL_KEYWORDS) for label in labels)
    _cache_set(key, {'adult': adult})
    logger.debug('wikidata adult-check item=%s classes=%d adult=%s (%.2fs)',
                 wikibase_item, len(class_ids), adult, time.monotonic() - t0)
    return adult


def _claim_ids(client, wikibase_item, prop):
    """Return the entity ids referenced by a Wikidata statement (P31/P279)."""
    r = client.get(
        WIKIDATA_API_BASE,
        params={
            'action': 'wbgetclaims',
            'entity': wikibase_item,
            'property': prop,
            'format': 'json',
        },
        headers={'User-Agent': USER_AGENT},
    )
    r.raise_for_status()
    ids = []
    for claim in r.json().get('claims', {}).get(prop, []):
        class_id = ((((claim.get('mainsnak') or {}).get('datavalue') or {})
                     .get('value') or {}).get('id'))
        if class_id:
            ids.append(class_id)
    return ids[:25]


def _instance_of(client, wikibase_item):
    """Return the item's P31 ("instance of") class ids."""
    return _claim_ids(client, wikibase_item, 'P31')


def _subclass_of(client, wikibase_item):
    """Return the item's P279 ("subclass of") class ids."""
    return _claim_ids(client, wikibase_item, 'P279')


def _class_labels(client, class_ids):
    """Return the English labels of the given class ids (lowercased)."""
    r = client.get(
        WIKIDATA_API_BASE,
        params={
            'action': 'wbgetentities',
            'ids': '|'.join(class_ids),
            'props': 'labels',
            'languages': 'en',
            'format': 'json',
        },
        headers={'User-Agent': USER_AGENT},
    )
    r.raise_for_status()
    labels = []
    for entity in (r.json().get('entities') or {}).values():
        label = ((entity.get('labels') or {}).get('en') or {}).get('value', '')
        if label:
            labels.append(label.lower())
    return labels


def _classify(class_ids, labels):
    """Map P31 class ids + their English labels onto card-detection tags."""
    ids = set(class_ids)
    tags = set()
    if ids & _PERSON_CLASS_IDS:
        tags.add('person')
    if ids & _MOVIE_TV_CLASS_IDS or any(
        _contains_word(label, _MOVIE_TV_LABEL_KEYWORDS)
        and not _contains_word(label, _MOVIE_TV_LABEL_STOPWORDS)
        for label in labels
    ):
        tags.add('movie_tv')
    if ids & _TRAVEL_PLACE_CLASS_IDS or any(
        _contains_word(label, _TRAVEL_PLACE_LABEL_KEYWORDS) for label in labels
    ):
        tags.update(('travel_place', 'place'))
    if ids & _PLACE_CLASS_IDS or any(
        _contains_word(label, _PLACE_LABEL_KEYWORDS) for label in labels
    ):
        tags.add('place')
    return tags
