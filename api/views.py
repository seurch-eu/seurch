"""Public API endpoints.

Every search feature of the site is exposed here as a stateless JSON endpoint:
the search options that the web UI keeps in a preferences cookie (engine,
safe-search, language, time range, pagination) are plain query parameters
instead, so a request is fully described by its URL. Authentication is by API
key (see ``api.authentication``); all endpoints require one.

The handlers are thin wrappers over the same ``fetch_*`` service functions the
HTML views call, so the API and the website always return the same results.
"""

import json
import re

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView

from cards.wikipedia import fetch_wikipedia
from images.services import fetch_images, fetch_similar_images
from instant.detect import detect as detect_instant_answer
from maps.services import fetch_geocode, place_from_coords
from news.services import fetch_news
from search import health, usage
from search.clients import REAL_ENGINES, selected_engines
from search.panel import knowledge_cards
from translate.services import fetch_languages, fetch_translation
from videos.services import fetch_videos
from web.services import fetch_suggestions, fetch_web

from .serializers import (
    ApiKeySerializer,
    ImageResultSerializer,
    LanguageSerializer,
    NewsResultSerializer,
    PlaceSerializer,
    ProviderStatusSerializer,
    TranslationSerializer,
    VideoResultSerializer,
    WebResultSerializer,
)

# Highest page number the API will fetch - keeps a client from walking a
# provider into the ground (Brave's own offset caps out well before this).
MAX_PAGE = 50
# Geocoding results cap for the maps endpoint.
MAX_PLACES = 50
_SPLIT_RE = re.compile(r'[,\s]+')


class _SafeJSONEncoder(DjangoJSONEncoder):
    """DjangoJSONEncoder (handles lazy strings, datetimes, Decimal…) that falls
    back to ``str`` for anything still unknown, so an exotic value in an instant
    answer can never 500 the endpoint."""

    def default(self, obj):
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)


def _jsonable(obj):
    """Coerce a template-shaped dict (cards, instant answers) to plain JSON.

    These structures are built for the templates and may carry lazy translation
    proxies or other non-JSON types; round-tripping through the safe encoder
    yields pure data without the views needing a field-by-field serializer for
    every provider/answer shape."""
    if obj is None:
        return None
    return json.loads(json.dumps(obj, cls=_SafeJSONEncoder))


class BaseAPIView(APIView):
    """Shared query-parameter parsing for the search endpoints.

    Authentication, permission and throttling come from the project-wide DRF
    defaults (API key required, per-key rate limits)."""

    def _query(self, request, *, required=True):
        q = (request.query_params.get('q') or '').strip()
        if required and not q:
            raise ValidationError({'q': 'This query parameter is required.'})
        return q

    def _engine(self, request):
        """Engines to query: comma/space separated names, ``all``, or default all."""
        raw = (request.query_params.get('engine') or '').strip().lower()
        if not raw or raw == 'all':
            return list(REAL_ENGINES)
        names = [p for p in _SPLIT_RE.split(raw) if p]
        unknown = [n for n in names if n not in REAL_ENGINES]
        if unknown:
            raise ValidationError({'engine': (
                f'Unknown engine(s): {", ".join(unknown)}. '
                f'Valid: {", ".join(REAL_ENGINES)} (or "all").'
            )})
        return list(selected_engines(names))

    def _safe(self, request):
        val = (request.query_params.get('safe') or 'on').strip().lower()
        return 'off' if val in ('off', 'false', '0', 'no') else 'on'

    def _lang(self, request):
        return (request.query_params.get('lang') or '').strip().lower()

    def _page(self, request):
        raw = request.query_params.get('page', 1)
        try:
            page = int(raw)
        except (TypeError, ValueError):
            raise ValidationError({'page': 'Must be an integer.'})
        return min(max(page, 1), MAX_PAGE)

    def _date(self, request):
        val = (request.query_params.get('date') or '').strip().lower()
        return val if val in ('d', 'w', 'm', 'y') else ''


class ApiRootView(BaseAPIView):
    """Index of the available endpoints."""

    def get(self, request):
        def link(name):
            return reverse(f'api:{name}', request=request)

        endpoints = {
            'web_search': link('web'),
            'image_search': link('images'),
            'similar_image_search': link('images-similar'),
            'news_search': link('news'),
            'video_search': link('videos'),
            'maps': link('maps'),
            'translate': link('translate'),
            'translate_languages': link('translate-languages'),
            'instant': link('instant'),
            'cards': link('cards'),
            'suggest': link('suggest'),
            'status': link('status'),
            'key': link('key'),
        }
        if not settings.STATUS_PAGE_ENABLED:
            # Provider status isn't published on this instance (see StatusView),
            # so don't advertise the endpoint that would 404.
            endpoints.pop('status')
        return Response(endpoints)


class WebSearchView(BaseAPIView):
    def get(self, request):
        query = self._query(request)
        engine = self._engine(request)
        page = self._page(request)
        results, correction = fetch_web(
            query, engine, page, self._safe(request), self._lang(request), self._date(request),
        )
        # Web search fans out to every engine in scope; each provider it queries
        # counts as one search, matching the website (search.views.results).
        usage.record_search(request.user, len(engine))
        return Response({
            'query': query,
            'tab': 'web',
            'page': page,
            'engine': engine,
            'correction': correction or '',
            'results': WebResultSerializer(results, many=True).data,
        })


class ImageSearchView(BaseAPIView):
    def get(self, request):
        query = self._query(request)
        engine = self._engine(request)
        page = self._page(request)
        results = fetch_images(
            query, engine, page, self._safe(request), self._lang(request),
        )
        usage.record_search(request.user)
        return Response({
            'query': query,
            'tab': 'images',
            'page': page,
            'engine': engine,
            'results': ImageResultSerializer(results, many=True).data,
        })


class SimilarImageSearchView(BaseAPIView):
    """Images similar to a given one - an image search seeded from its caption.

    Pass the opened image's caption as ``q`` (and/or the page's ``query`` as a
    fallback seed); ``exclude_url`` drops the opened image from the results.
    Mirrors the website's lightbox "similar images" grid.
    """

    def get(self, request):
        title = (request.query_params.get('q') or '').strip()
        query = (request.query_params.get('query') or '').strip()
        if not title and not query:
            raise ValidationError({'q': 'Provide q (the image caption) and/or query (a fallback seed).'})
        engine = self._engine(request)
        seed, results = fetch_similar_images(
            title, query, engine=engine,
            safe_search=self._safe(request), lang=self._lang(request),
            exclude_url=(request.query_params.get('exclude_url') or '').strip(),
        )
        # A real image search ran only when a usable seed was found; count that.
        if seed:
            usage.record_search(request.user)
        return Response({
            'query': seed,
            'engine': engine,
            'results': ImageResultSerializer(results, many=True).data,
        })


class NewsSearchView(BaseAPIView):
    def get(self, request):
        query = self._query(request)
        engine = self._engine(request)
        page = self._page(request)
        results = fetch_news(
            query, engine, page, self._safe(request), self._lang(request), self._date(request),
        )
        usage.record_search(request.user)
        return Response({
            'query': query,
            'tab': 'news',
            'page': page,
            'engine': engine,
            'results': NewsResultSerializer(results, many=True).data,
        })


class VideoSearchView(BaseAPIView):
    def get(self, request):
        query = self._query(request)
        engine = self._engine(request)
        page = self._page(request)
        results = fetch_videos(
            query, engine, page, self._safe(request), self._lang(request), self._date(request),
        )
        usage.record_search(request.user)
        return Response({
            'query': query,
            'tab': 'videos',
            'page': page,
            'engine': engine,
            'results': VideoResultSerializer(results, many=True).data,
        })


class MapsView(BaseAPIView):
    """Geocode a place name (``q``) or reverse a coordinate pair (``lat``/``lon``)."""

    def get(self, request):
        lat = (request.query_params.get('lat') or '').strip()
        lon = (request.query_params.get('lon') or '').strip()
        lang = self._lang(request)

        if lat and lon:
            try:
                place = place_from_coords(
                    float(lat), float(lon),
                    name=(request.query_params.get('label') or request.query_params.get('q') or '').strip(),
                )
            except (TypeError, ValueError):
                raise ValidationError({'lat': 'lat and lon must be valid coordinates.'})
            places = [place] if place else []
            query = ''
        else:
            query = self._query(request)
            try:
                limit = int(request.query_params.get('limit', 10))
            except (TypeError, ValueError):
                raise ValidationError({'limit': 'Must be an integer.'})
            limit = min(max(limit, 1), MAX_PLACES)
            places = fetch_geocode(query, limit=limit, lang=lang)

        usage.record_search(request.user)
        return Response({
            'query': query,
            'places': PlaceSerializer(places, many=True).data,
        })


class TranslateView(BaseAPIView):
    """Translate text. Accepts GET (params) or POST (body); POST keeps long
    text out of the URL/logs, mirroring the website's translate form."""

    def get(self, request):
        return self._translate(request, request.query_params)

    def post(self, request):
        return self._translate(request, request.data)

    def _translate(self, request, data):
        text = (data.get('q') or data.get('text') or '').strip()
        if not text:
            raise ValidationError({'q': 'This parameter is required (the text to translate).'})
        target = (data.get('target') or '').strip()
        if not target:
            raise ValidationError({'target': 'This parameter is required (target language code).'})
        source = (data.get('source') or 'auto').strip() or 'auto'

        usage.record_search(request.user)
        result = fetch_translation(text, target, source)
        if result is None:
            return Response(
                {'detail': 'Translation is unavailable or not configured on this deployment.'},
                status=503,
            )
        return Response({
            'source': source,
            'target': target,
            **TranslationSerializer(result).data,
        })


class LanguagesView(BaseAPIView):
    def get(self, request):
        usage.record_search(request.user)
        languages = [{'code': code, 'name': name} for code, name in fetch_languages()]
        return Response({'languages': LanguageSerializer(languages, many=True).data})


class InstantView(BaseAPIView):
    """Instant answer for a query (math, units, weather, currency, IP, …), or null."""

    def get(self, request):
        query = self._query(request)
        usage.record_search(request.user)
        answer = detect_instant_answer(query, getattr(request, '_request', request))
        return Response({'query': query, 'answer': _jsonable(answer)})


class CardsView(BaseAPIView):
    """Knowledge cards for a query: Wikipedia, TheTVDB, TripAdvisor, Stack Exchange,
    plus a map quick-answer. Mirrors the web tab's knowledge panel, so the cards
    are derived from the same web + Wikipedia context."""

    def get(self, request):
        query = self._query(request)
        lang = self._lang(request)
        safe = self._safe(request)
        engine = self._engine(request)

        usage.record_search(request.user)
        web_results, _ = fetch_web(query, engine, 1, safe, lang, '')
        wikipedia_card = fetch_wikipedia(query, lang, safe)
        cards = knowledge_cards(
            query, lang, web_results, wikipedia_card, disabled=set(),
            maps_enabled=True, instant_present=False,
        )
        return Response({
            'query': query,
            'wikipedia': _jsonable(wikipedia_card),
            'thetvdb': _jsonable(cards['thetvdb_card']),
            'tripadvisor': _jsonable(cards['tripadvisor_card']),
            'stackexchange': _jsonable(cards['stackexchange_card']),
            'map': _jsonable(cards['map_answer']),
        })


class SuggestView(BaseAPIView):
    """Search-bar autocomplete suggestions for a query."""

    def get(self, request):
        query = self._query(request)
        usage.record_search(request.user)
        return Response({'query': query, 'suggestions': fetch_suggestions(query)})


class StatusView(BaseAPIView):
    """Current up/down status of each configured upstream provider.

    This is the same data the /status page renders, so ``STATUS_PAGE_ENABLED``
    turns both off together: an instance that doesn't publish which providers
    it uses (and when they fail) shouldn't publish it here either. Uptime
    monitoring doesn't go through here, it has its own keyless endpoint at
    /status/health.
    """

    def get(self, request):
        if not settings.STATUS_PAGE_ENABLED:
            raise NotFound('Provider status is not published on this instance.')
        statuses = health.provider_statuses()
        return Response({'providers': ProviderStatusSerializer(statuses, many=True).data})


class KeyInfoView(BaseAPIView):
    """Details of the API key making the request - handy to verify a key works."""

    def get(self, request):
        return Response(ApiKeySerializer(request.auth).data)
