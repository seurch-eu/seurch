"""The knowledge panel: which cards a query gets, and in what order."""

from cards.relevance import _title_match
from cards.stackexchange import fetch_stackexchange
from cards.thetvdb import fetch_thetvdb
from cards.tripadvisor import fetch_tripadvisor
from instant.places import map_answer
from maps.services import fetch_geocode, looks_like_place


def _wikipedia_ranks_first(query, wiki_card, other_card):
    """True when the Wikipedia card's title matches the query more closely than
    the other card's, so it should be shown first."""
    if not wiki_card or not other_card:
        return False
    return _title_match(wiki_card.get('title', ''), query) > _title_match(other_card.get('title', ''), query)


def knowledge_cards(query, lang, web_results, wikipedia_card, disabled, *,
                    maps_enabled, instant_present):
    """Knowledge cards + map quick-answer derived from the web results."""
    thetvdb = tripadvisor = stackexchange = None
    if 'thetvdb' not in disabled:
        thetvdb = fetch_thetvdb(
            query, wikipedia_card=wikipedia_card, web_results=web_results, lang=lang,
        )
    if not thetvdb and 'tripadvisor' not in disabled:
        tripadvisor = fetch_tripadvisor(
            query, wikipedia_card=wikipedia_card, web_results=web_results, lang=lang,
        )
    if not (thetvdb or tripadvisor) and 'stackexchange' not in disabled:
        stackexchange = fetch_stackexchange(query, web_results=web_results, lang=lang)

    answer = None
    if maps_enabled and not instant_present and not (thetvdb or tripadvisor or stackexchange) \
            and looks_like_place(query, wikipedia_card, web_results):
        geo = fetch_geocode(query, limit=1, lang=lang)
        answer = map_answer(geo[0] if geo else None, query)

    return {
        'thetvdb_card': thetvdb,
        'tripadvisor_card': tripadvisor,
        'stackexchange_card': stackexchange,
        'wikipedia_first': _wikipedia_ranks_first(
            query, wikipedia_card, thetvdb or tripadvisor or stackexchange,
        ),
        'map_answer': answer,
    }
