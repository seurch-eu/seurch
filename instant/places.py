"""Map instant answer.

The web-tab *map quick-answer*, a minimap card shown when a query looks like a
place, is surfaced through the same single ``instant_answer`` slot as every
other instant answer. That way it never stacks on top of (say) a weather or
currency answer: whichever matches first wins, and the map only fills the slot
when nothing else did.

Geocoding itself lives in ``maps.services`` because the Maps tab shares it
(and, by app layering, ``instant`` must not import it). This module owns only
the instant-answer *shape*; the caller passes in an already-built place dict
from ``maps.services.fetch_geocode``.
"""


def map_answer(place, query=''):
    """Wrap a geocoded *place* as a ``type='map'`` instant answer.

    Returns ``None`` when there is no place, so the caller can assign the result
    straight to ``instant_answer`` without an extra guard.
    """
    if not place:
        return None
    return {'type': 'map', 'place': place, 'query': query}
