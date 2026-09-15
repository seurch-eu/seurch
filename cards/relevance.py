"""Shared relevance helpers and keyword sets for knowledge-card detection."""

import difflib
import re
import unicodedata

# ---------------------------------------------------------------------------
# Movie / TV keyword sets
# ---------------------------------------------------------------------------

_MOVIE_TV_WIKI_KEYWORDS = frozenset({
    # en
    'film', 'movie', 'television series', 'tv series', 'tv show', 'web series',
    'animated series', 'sitcom', 'drama series', 'miniseries', 'mini-series',
    'documentary', 'streaming series', 'anime', 'telenovela', 'soap opera',
    'reality show', 'talk show', 'limited series', 'short film',
    # fr
    'série télévisée', 'téléfilm', 'série animée', 'dessin animé',
    'documentaire', 'mini-série', 'websérie', 'court métrage', 'long métrage',
    # de
    'fernsehserie', 'zeichentrickserie', 'dokumentarfilm', 'miniserie',
    'webserie', 'seifenoper', 'kurzfilm', 'spielfilm', 'fernsehfilm',
    # es
    'película', 'serie de televisión', 'serie animada', 'telefilme',
    'documental', 'cortometraje', 'largometraje',
    # it
    'serie televisiva', 'serie animata', 'documentario', 'cortometraggio',
    # pt
    'filme', 'série de televisão', 'série animada', 'documentário',
    'minissérie', 'curta-metragem', 'longa-metragem',
    # nl
    'televisieserie', 'tekenfilmserie', 'animatieserie', 'soapserie', 'dramaserie',
})

# If Wikipedia's short description identifies the subject as a person (actor,
# singer, …) it's not a film/show, skip TheTVDB even when IMDB etc. 
_PERSON_WIKI_KEYWORDS = frozenset({
    # en
    'actor', 'actress', 'singer', 'rapper', 'musician', 'composer', 'comedian',
    'television presenter', 'film director', 'filmmaker', 'screenwriter',
    'film producer', 'model', 'athlete', 'politician', 'novelist', 'journalist',
    'footballer', 'painter',
    # fr
    'acteur', 'actrice', 'chanteur', 'chanteuse', 'rappeur', 'musicien',
    'musicienne', 'compositeur', 'humoriste', 'présentateur', 'réalisateur',
    'réalisatrice', 'cinéaste', 'scénariste', 'mannequin', 'athlète',
    'écrivain', 'romancier', 'journaliste', 'footballeur', 'peintre',
    'homme politique', 'femme politique',
    # de
    'schauspieler', 'schauspielerin', 'sänger', 'sängerin', 'musiker',
    'komponist', 'komiker', 'moderator', 'regisseur', 'drehbuchautor',
    'sportler', 'politiker', 'schriftsteller', 'fußballspieler',
    'fußballer', 'maler',
    # es
    'actriz', 'cantante', 'rapero', 'músico', 'compositor', 'comediante',
    'humorista', 'presentador', 'director de cine', 'cineasta', 'guionista',
    'modelo', 'atleta', 'escritor', 'novelista', 'periodista', 'futbolista',
    'pintor', 'político',
    # it
    'attore', 'attrice', 'musicista', 'compositore', 'comico', 'conduttore',
    'regista', 'sceneggiatore', 'modello', 'scrittore', 'romanziere',
    'giornalista', 'calciatore', 'pittore', 'politico',
    # pt
    'ator', 'atriz', 'cantor', 'cantora', 'apresentador', 'diretor de cinema',
    'roteirista', 'romancista', 'jornalista', 'futebolista',
    # nl
    'zanger', 'zangeres', 'muzikant', 'componist', 'komiek', 'presentator',
    'scenarioschrijver', 'atleet', 'schrijver', 'voetballer', 'schilder',
    'politicus',
})

_MOVIE_TV_DOMAINS = frozenset({
    'imdb.com', 'rottentomatoes.com', 'metacritic.com', 'themoviedb.org',
    'letterboxd.com', 'allocine.fr', 'senscritique.com', 'filmaffinity.com',
    'justwatch.com',
})

# ---------------------------------------------------------------------------
# Travel keyword sets
# ---------------------------------------------------------------------------

_TRAVEL_QUERY_KEYWORDS = frozenset({
    # en
    'restaurant', 'restaurants', 'hotel', 'hotels', 'hostel', 'hostels',
    'cafe', 'cafes', 'café', 'cafés', 'bar', 'bars', 'pub', 'pubs',
    'bistro', 'brasserie', 'trattoria', 'pizzeria', 'tavern',
    'resort', 'resorts', 'spa', 'spas', 'inn', 'b&b', 'bed and breakfast',
    'attraction', 'attractions', 'things to do', 'sightseeing', 'tourist',
    'museum', 'museums', 'gallery', 'galleries', 'beach',
    'tour', 'tours', 'excursion', 'excursions',
    'where to eat', 'where to stay', 'best places',
    'vacation', 'holiday', 'travel guide',
    # fr
    'hôtel', 'hôtels', 'auberge', 'auberge de jeunesse',
    'musée', 'musées', 'plage', 'voyage', 'vacances',
    'que faire', 'où manger', 'où dormir',
    'hébergement', "chambre d'hôtes", 'gîte', 'tourisme',
    # de
    'kneipe', 'biergarten', 'strand', 'reise', 'urlaub',
    'sehenswürdigkeit', 'sehenswürdigkeiten', 'ausflug',
    'wo essen', 'wo schlafen', 'was tun',
    'reiseführer', 'unterkunft', 'pension', 'gasthaus',
    # es
    'restaurante', 'restaurantes', 'hostal', 'taberna',
    'museo', 'museos', 'playa', 'viaje', 'vacaciones',
    'qué hacer', 'dónde comer', 'dónde dormir',
    'excursión', 'alojamiento', 'pensión', 'turismo',
    # it
    'ristorante', 'ristoranti', 'ostello', 'caffè', 'osteria', 'locanda',
    'musei', 'spiaggia', 'viaggio', 'vacanza',
    'cosa fare', 'dove mangiare', 'dove dormire',
    'escursione', 'alloggio', 'agriturismo',
    # pt
    'museu', 'museus',
    'praia', 'viagem', 'férias',
    'o que fazer', 'onde comer', 'onde dormir',
    'excursão', 'acomodação', 'pousada',
    # nl
    'kroeg', 'vakantie', 'reizen',
    'bezienswaardigheid', 'bezienswaardigheden',
    'wat te doen', 'waar eten', 'waar slapen',
    'uitstap', 'reisgids', 'verblijf', 'herberg', 'toerisme',
})

_TRAVEL_WIKI_KEYWORDS = frozenset({
    # en
    'restaurant', 'hotel', 'tourist attraction', 'landmark',
    'beach resort', 'museum', 'amusement park', 'theme park', 'national park',
    'marina', 'resort', 'spa',
    # fr
    'hôtel', 'attraction touristique', 'lieu touristique',
    'musée', "parc d'attractions",
    # de
    'sehenswürdigkeit', 'vergnügungspark', 'freizeitpark',
    # es
    'restaurante', 'atracción turística', 'lugar turístico',
    'museo', 'parque de atracciones',
    # it
    'ristorante', 'attrazione turistica', 'luogo turistico',
    'parco divertimenti',
    # pt
    'atração turística', 'museu',
    'parque de diversões',
    # nl
    'toeristische attractie', 'pretpark',
})

_TRAVEL_DOMAINS = frozenset({
    'tripadvisor.com', 'yelp.com', 'booking.com', 'hotels.com',
    'airbnb.com', 'lonelyplanet.com', 'timeout.com', 'fodors.com',
    'opentable.com', 'zomato.com', 'thefork.com', 'expedia.com',
})

# ---------------------------------------------------------------------------
# Place / map keyword sets
# ---------------------------------------------------------------------------

_PLACE_QUERY_PHRASES = (
    # en
    'map of', 'directions to', 'where is', 'how to get to', 'address of',
    'near me', 'nearby',
    # fr
    'plan de', 'carte de', 'itinéraire', 'où se trouve', 'comment aller', 'près de moi',
    # de
    'karte von', 'wegbeschreibung', 'wo ist', 'anfahrt', 'in der nähe',
    # es
    'mapa de', 'cómo llegar', 'dónde está', 'cerca de mí',
    # it
    'mappa di', 'come arrivare', 'dove si trova', 'vicino a me',
    # nl
    'kaart van', 'route naar', 'waar is', 'in de buurt',
)

_STREET_SUFFIXES = frozenset({
    'street', 'st', 'avenue', 'ave', 'road', 'rd', 'boulevard', 'blvd',
    'lane', 'ln', 'drive', 'dr', 'court', 'ct', 'square', 'sq', 'place', 'pl',
    'way', 'highway', 'route', 'terrace', 'crescent', 'close', 'parkway',
    'rue', 'impasse', 'allée', 'chemin', 'quai', 'cours',
    'straße', 'strasse', 'str', 'platz', 'weg', 'gasse', 'allee', 'ring',
    'via', 'viale', 'piazza', 'corso', 'strada', 'vicolo',
    'calle', 'avenida', 'plaza', 'paseo', 'carrer', 'rambla',
    'straat', 'laan', 'plein', 'gracht', 'kade',
})

_PLACE_WIKI_KEYWORDS = frozenset({
    # en
    'city', 'town', 'village', 'commune', 'municipality', 'capital', 'country',
    'county', 'region', 'province', 'state', 'district', 'borough', 'prefecture',
    'neighborhood', 'neighbourhood', 'suburb', 'metropolis',
    'river', 'lake', 'sea', 'ocean', 'mountain', 'mount', 'hill', 'island',
    'peninsula', 'desert', 'valley', 'volcano', 'archipelago', 'glacier', 'bay',
    'national park', 'park', 'square', 'street', 'avenue', 'bridge', 'tower',
    'landmark', 'monument', 'castle', 'palace', 'cathedral', 'stadium', 'airport',
    'station', 'harbour', 'harbor', 'port',
    # fr
    'ville', 'pays', 'région', 'fleuve', 'rivière', 'montagne', 'île',
    'quartier', 'château', 'pont', 'gare',
    # de
    'stadt', 'dorf', 'land', 'fluss', 'berg', 'insel', 'gebirge', 'schloss',
    # es
    'ciudad', 'pueblo', 'país', 'río', 'montaña', 'isla', 'barrio', 'castillo',
    # it
    'città', 'paese', 'fiume', 'montagna', 'isola', 'quartiere', 'castello',
    # nl
    'stad', 'dorp', 'rivier', 'eiland', 'wijk', 'kasteel',
})

_MAP_DOMAINS = frozenset({
    'openstreetmap.org', 'google.com/maps', 'maps.google', 'goo.gl/maps',
    'maps.apple.com', 'bing.com/maps', 'mapquest.com', 'here.com', 'waze.com',
})

# ---------------------------------------------------------------------------
# Programming / tech-question keyword sets
#
# Stack Overflow's content is overwhelmingly in English, so, unlike the
# multilingual sets above, these stay English-only; the card is offered
# regardless of the query's detected language.
# ---------------------------------------------------------------------------

_PROGRAMMING_QUERY_KEYWORDS = frozenset({
    # languages, runtimes & frameworks
    'python', 'javascript', 'typescript', 'java', 'c++', 'c#', 'php', 'ruby',
    'golang', 'rust', 'swift', 'kotlin', 'scala', 'perl', 'bash', 'powershell',
    'sql', 'html', 'css', 'react', 'angular', 'vue', 'django', 'flask',
    'node', 'node.js', 'express', 'spring', 'laravel', 'rails', 'dotnet',
    '.net', 'jquery', 'numpy', 'pandas', 'tensorflow', 'pytorch',
    # error / debugging vocabulary
    'error', 'exception', 'traceback', 'stack trace', 'stacktrace',
    'syntax error', 'segmentation fault', 'segfault', 'null pointer',
    'nullpointerexception', 'nullreferenceexception', 'typeerror', 'valueerror',
    'keyerror', 'indexerror', 'attributeerror', 'referenceerror',
    'undefined is not a function', 'undefined reference', 'cannot find module',
    'compile error', 'compiler error', 'runtime error',
    # programming concepts & actions
    'function', 'method', 'algorithm', 'regex', 'regexp', 'recursion',
    'compiler', 'debug', 'debugging', 'array', 'dictionary', 'linked list',
    'npm install', 'pip install', 'git commit', 'git merge', 'merge conflict',
    'docker', 'kubernetes', 'json parse', 'api request', 'unit test',
    'how to fix', 'how do i', 'what does this error mean',
})

_PROGRAMMING_DOMAINS = frozenset({
    'stackoverflow.com', 'stackexchange.com', 'superuser.com', 'serverfault.com',
    'askubuntu.com', 'github.com', 'gitlab.com', 'developer.mozilla.org',
    'docs.python.org', 'pypi.org', 'npmjs.com', 'w3schools.com',
})

# ---------------------------------------------------------------------------
# Token normalisation
# ---------------------------------------------------------------------------


def _fold_accents(token):
    """Casefold a token and strip diacritics, so "Misérables" ≈ "miserables"
    and "Straße" ≈ "strasse", accent habits differ per keyboard/language."""
    token = unicodedata.normalize('NFKD', token.casefold())
    return ''.join(ch for ch in token if not unicodedata.combining(ch))


def _fold_token(token):
    """Matching form of a token: accent-folded, with a trailing plural "s"
    dropped ("restaurants" ≈ "restaurant", "eggs" ≈ "egg"). Applied to query,
    candidate and stop/filler vocabularies alike, so both sides stay aligned."""
    token = _fold_accents(token)
    if len(token) > 3 and token.endswith('s') and not token.endswith('ss'):
        token = token[:-1]
    return token


# ---------------------------------------------------------------------------
# Relevance stop/filler words (shared across card types)
# ---------------------------------------------------------------------------

# Tokens that never identify a specific entity: articles, prepositions and vague
# qualifiers ("best", "near"…) across the seven languages, plus the category
# nouns that drive card detection ("restaurant", "film", "actor"…). Stripped
# before relevance scoring so "best restaurants in lyon" is matched on "lyon",
# and "the matrix film" on "matrix".
_RELEVANCE_STOPWORDS = frozenset({
    # en
    'the', 'a', 'an', 'of', 'in', 'on', 'at', 'to', 'for', 'and', 'or', 'with',
    'near', 'by', 'best', 'top', 'good', 'great', 'cheap', 'nearby', 'me', 'my',
    # fr
    'le', 'la', 'les', 'un', 'une', 'des', 'du', 'et', 'ou', 'dans', 'sur',
    'pour', 'près', 'meilleur', 'meilleurs', 'meilleure', 'bon', 'proche',
    # de
    'der', 'die', 'das', 'ein', 'eine', 'den', 'dem', 'und', 'oder', 'für',
    'bei', 'zum', 'zur', 'beste', 'besten', 'gut', 'gute', 'nah',
    # es
    'el', 'los', 'las', 'una', 'unos', 'unas', 'para', 'cerca', 'mejor',
    'mejores', 'buen', 'bueno',
    # it
    'lo', 'gli', 'nel', 'nella', 'per', 'vicino', 'migliore', 'migliori',
    'buono',
    # pt
    'os', 'um', 'uma', 'em', 'no', 'na', 'perto', 'melhor', 'melhores', 'bom',
    # nl
    'het', 'een', 'van', 'bij', 'nabij', 'goede',
})

# Folded like the tokens it is matched against (see `_fold_token`).
_RELEVANCE_FILLER = frozenset(
    _fold_token(tok)
    for tok in _RELEVANCE_STOPWORDS | {
        tok
        for kw in (_TRAVEL_QUERY_KEYWORDS | _MOVIE_TV_WIKI_KEYWORDS | _PERSON_WIKI_KEYWORDS)
        for tok in kw.split()
        if len(tok) > 1
    }
)

# Query words that mark an informational search rather than a title, they
# suppress the TheTVDB probe so the API isn't called for "python tutorial", etc.
# Accent-folded only (no plural folding: "news" must not swallow "new").
_NON_TITLE_MARKERS = frozenset(map(_fold_accents, {
    'tutorial', 'tutorials', 'guide', 'how', 'what', 'why', 'when', 'where',
    'recipe', 'recipes', 'review', 'reviews', 'vs', 'versus', 'meaning',
    'definition', 'lyrics', 'news', 'download', 'price', 'wiki',
    # a few common non-English equivalents
    'tutoriel', 'guía', 'guida', 'rezept', 'ricetta', 'receta', 'recensione',
    'reseña', 'bedeutung', 'significato', 'significado', 'paroles', 'letra',
}))

# ---------------------------------------------------------------------------
# Relevance scoring helpers
# ---------------------------------------------------------------------------


def _first_keyword_pos(text, keywords):
    """Index of the earliest whole-word keyword match in `text`, or None.

    Whole-word (not substring) matching avoids false hits like 'film' inside
    'filmmaker' or the Portuguese 'ator' inside 'senator'. Returning the
    position lets callers disambiguate descriptions that mention several
    categories ("2018 film about a singer") by which one comes first.
    """
    earliest = None
    for kw in keywords:
        m = re.search(r'(?<!\w)' + re.escape(kw) + r'(?!\w)', text)
        if m and (earliest is None or m.start() < earliest):
            earliest = m.start()
    return earliest


def _contains_word(text, keywords):
    """True if `text` contains any of `keywords` as a whole word/phrase."""
    return _first_keyword_pos(text, keywords) is not None


def _significant_tokens(text):
    """Identifying tokens: folded words with stop/category words and single
    characters removed, so relevance is judged on the proper-noun parts."""
    return {
        t for t in map(_fold_token, re.findall(r'\w+', text or ''))
        if len(t) > 1 and t not in _RELEVANCE_FILLER
    }


def _normalized_text(text):
    """Folded characters of *text* with spacing/punctuation removed, the form
    compared by `_fuzzy_ratio`, so "Spider-Man" and "spiderman" coincide."""
    return ''.join(map(_fold_token, re.findall(r'\w+', text or '')))


# Minimum character-level similarity for a fuzzy match. Only consulted when
# token matching found nothing at all, and kept strict so near-identical
# spellings ("incepton", "spiderman") pass but loose overlaps don't.
_FUZZY_MATCH_MIN = 0.8

# Minimum `_match_score` for a returned entity to count as matching the search.
_NAME_MATCH_MIN = 0.5


def _fuzzy_ratio(a, b):
    """Character-level similarity (0..1) between two normalised strings."""
    na, nb = _normalized_text(a), _normalized_text(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _match_score(candidate, reference):
    """How strongly `candidate` matches `reference` (0..1)."""
    c_tokens = _significant_tokens(candidate)
    r_tokens = _significant_tokens(reference)
    if c_tokens and r_tokens:
        overlap = len(c_tokens & r_tokens) / min(len(c_tokens), len(r_tokens))
        if overlap:
            return overlap
    fuzzy = _fuzzy_ratio(candidate, reference)
    return fuzzy if fuzzy >= _FUZZY_MATCH_MIN else 0.0


def _name_is_relevant(name, *references):
    """True if `name` plausibly refers to the same subject as any reference
    string (typically the query and/or the Wikipedia title)."""
    return any(_match_score(name, ref) >= _NAME_MATCH_MIN for ref in references if ref)


def _shares_significant_token(a, b):
    """True if two strings share at least one identifying token, used to tie a
    place's address back to a location named in the query ("…in lyon")."""
    return bool(_significant_tokens(a) & _significant_tokens(b))


def _ranking_tokens(text):
    """Tokens for ranking how well a title matches the query: drops stop/category
    words but *keeps* standalone numbers (so a sequel's trailing "2" counts)."""
    return {
        t for t in map(_fold_token, re.findall(r'\w+', text or ''))
        if t not in _RELEVANCE_FILLER and (len(t) > 1 or t.isdigit())
    }


def _title_match(candidate, query):
    """Jaccard overlap between a candidate title and the user's query."""
    c = _ranking_tokens(candidate)
    q = _ranking_tokens(query)
    if not c or not q:
        return 0.0
    jaccard = len(c & q) / len(c | q)
    if jaccard:
        return jaccard
    fuzzy = _fuzzy_ratio(candidate, query)
    return fuzzy if fuzzy >= _FUZZY_MATCH_MIN else 0.0


# ---------------------------------------------------------------------------
# Card-type detection
# ---------------------------------------------------------------------------


def _is_movie_or_tv(query, web_results, wikipedia_card, tags=None):
    """Return True if the query likely refers to a movie or TV show."""
    if tags:
        if 'movie_tv' in tags:
            return True
        if tags & {'person', 'place'}:
            return False
    if wikipedia_card:
        desc = (wikipedia_card.get('description') or '').lower()
        work_pos = _first_keyword_pos(desc, _MOVIE_TV_WIKI_KEYWORDS)
        person_pos = _first_keyword_pos(desc, _PERSON_WIKI_KEYWORDS)
        # A film/show description leads with the work ("2018 film"); a person's
        # leads with the role ("American actor"). When both appear, biopics
        # ("film about a singer"), actor-directors, the one mentioned first
        # wins, so a movie's card still shows and an actor's still doesn't.
        if work_pos is not None and (person_pos is None or work_pos < person_pos):
            return True
        if person_pos is not None:
            return False
    # No Wikipedia signal: fall back to known movie/TV domains in the results.
    for result in (web_results or [])[:8]:
        url = result.get('url', '')
        if any(domain in url for domain in _MOVIE_TV_DOMAINS):
            return True
    return False


def _wiki_is_person(wikipedia_card, tags=None):
    """True when the Wikipedia card's subject is a person."""
    if tags:
        if 'person' in tags:
            return True
        if 'movie_tv' in tags:
            return False
    if not wikipedia_card:
        return False
    desc = (wikipedia_card.get('description') or '').lower()
    work = _first_keyword_pos(desc, _MOVIE_TV_WIKI_KEYWORDS)
    person = _first_keyword_pos(desc, _PERSON_WIKI_KEYWORDS)
    return person is not None and (work is None or person < work)


def _wiki_is_place(wikipedia_card, tags=None):
    """True when the Wikipedia card's subject is a place (city, landmark, …)."""
    if tags:
        if tags & {'place', 'travel_place'}:
            return True
        if tags & {'movie_tv', 'person'}:
            return False
    if not wikipedia_card:
        return False
    desc = (wikipedia_card.get('description') or '').lower()
    return _contains_word(desc, _PLACE_WIKI_KEYWORDS | _TRAVEL_WIKI_KEYWORDS)


def _looks_like_title(query, wikipedia_card):
    """True when the query is a plausible movie/TV title."""
    q_tokens = _significant_tokens(query)
    if not 2 <= len(q_tokens) <= 6:
        return False
    # Markers are checked on accent-folded-only words: plural folding would
    # turn "news" into "new" and wrongly suppress titles like "brave new world".
    words = {_fold_accents(t) for t in re.findall(r'\w+', query)}
    if words & _NON_TITLE_MARKERS:
        return False
    wiki_title = (wikipedia_card or {}).get('title') or ''
    if wiki_title:
        return _name_is_relevant(wiki_title, query)
    return True


def _is_programming_question(query, web_results):
    """Return True if the query likely seeks help with code (an SO-style question)."""
    if _contains_word(query.lower(), _PROGRAMMING_QUERY_KEYWORDS):
        return True
    for result in (web_results or [])[:8]:
        url = result.get('url', '')
        if any(domain in url for domain in _PROGRAMMING_DOMAINS):
            return True
    return False


def _query_travel_intent(query, wikipedia_card):
    """True when the *query itself* asks for hospitality, beyond the entity name."""
    if not _contains_word(query.lower(), _TRAVEL_QUERY_KEYWORDS):
        return False
    wiki_title = ((wikipedia_card or {}).get('title') or '').lower()
    return not (wiki_title and _contains_word(wiki_title, _TRAVEL_QUERY_KEYWORDS))


def _is_travel_query(query, web_results, wikipedia_card, tags=None):
    """Return True if the query likely refers to a restaurant, hotel, or attraction."""
    if tags and 'travel_place' in tags:
        return True
    if _query_travel_intent(query, wikipedia_card):
        return True
    if tags and tags & {'person', 'movie_tv'}:
        return False
    if _contains_word(query.lower(), _TRAVEL_QUERY_KEYWORDS):
        return True
    if tags and 'place' in tags:
        return False
    if wikipedia_card:
        desc = (wikipedia_card.get('description') or '').lower()
        if _contains_word(desc, _TRAVEL_WIKI_KEYWORDS):
            return True
    for result in (web_results or [])[:8]:
        url = result.get('url', '')
        if any(domain in url for domain in _TRAVEL_DOMAINS):
            return True
    return False
