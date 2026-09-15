"""Shared caching for the knowledge cards."""

from search.cache import _cache_get, _cache_set, _make_cache_key


def cached_card(kind, query, lang, fetch):
    """Return the *kind* card for *query*, fetching it at most once an hour.

    Unlike a result cache this stores the *absence* of a card too.
    """
    key = _make_cache_key(kind, query, '', 1, '', lang, '')
    cached = _cache_get(key)
    if cached is not None:
        return cached.get('card')
    card = fetch()
    _cache_set(key, {'card': card})
    return card
