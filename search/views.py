import concurrent.futures
import json
import logging
import re
import secrets
import time
import uuid
from urllib.parse import urlencode, urlparse

import httpx
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import BaseUserManager
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_safe
from lingua import Language, LanguageDetectorBuilder

from cards.wikipedia import fetch_wikipedia
from images.services import fetch_images, fetch_similar_images
from instant.detect import detect as detect_instant_answer
from maps.services import (
    WORLD_EMBED_URL,
    WORLD_OSM_URL,
    fetch_geocode,
    place_from_coords,
)
from news.services import fetch_news
from translate.services import fetch_languages, fetch_translation
from videos.services import fetch_videos
from web.services import fetch_suggestions, fetch_web

from . import backup, health, preferences, usage
from . import bangs as bang_service
from .clients import USER_AGENT
from .imageproxy import must_proxy, proxify, resolve_public_address, safe_content_type
from .models import BlockedSite, CustomBang
from .panel import knowledge_cards
from .services import blocked_domains_for, filter_blocked, normalize_domain

_CUSTOM_BANG_TRIGGER_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]*$')
_SETTINGS_PANES = {'general', 'appearance', 'language', 'bangs', 'blocked', 'account', 'engine', 'browser', 'backup', 'apikeys'}

API_KEY_LIMIT = 20

_PANE_SLUGS = {
    'general': 'general',
    'appearance': 'appearance',
    'language': 'language',
    'engine': 'engines',
    'bangs': 'shortcuts',
    'blocked': 'blocked',
    'browser': 'browser',
    'account': 'account',
    'apikeys': 'api-keys',
    'backup': 'backup',
}
_PANE_BY_SLUG = {slug: pane for pane, slug in _PANE_SLUGS.items()}

_PREF_SETTINGS = {'providers', 'safe_search', 'open_links_new_tab', 'search_lang', 'ui_lang', 'proxy_images', 'similar_images'}

_ACCOUNT_SETTINGS = {
    'email', 'delete_email', 'create_api_key', 'delete_api_key',
    'generate_session_link', 'delete_session_link',
}

logger = logging.getLogger(__name__)

_LINGUA_DETECTOR = (
    LanguageDetectorBuilder
    .from_languages(
        Language.ENGLISH, Language.FRENCH, Language.GERMAN,
        Language.SPANISH, Language.ITALIAN, Language.PORTUGUESE,
        Language.DUTCH,
    )
    .build()
)
_LINGUA_MIN_CONFIDENCE = 0.5
# lingua splits its confidence across all seven candidate languages and the
# values sum to 1.0, so a short query rarely lets any single language clear the
# absolute 0.5 bar, even unmistakably English phrases ("harry potter", "what
# time is it") top out around 0.45. Judging on the absolute score alone made
# those queries fall through to the Accept-Language header (e.g. French), so we
# also accept the top language when it leads the runner-up by a clear margin.
_LINGUA_MIN_MARGIN = 0.10


def _detect_language_from_query(query: str) -> str:
    """Use lingua to identify the query language; returns '' when confidence is too low."""
    values = _LINGUA_DETECTOR.compute_language_confidence_values(query)
    if not values:
        return ''
    top = values[0]
    runner_up = values[1].value if len(values) > 1 else 0.0
    if top.value >= _LINGUA_MIN_CONFIDENCE or top.value - runner_up >= _LINGUA_MIN_MARGIN:
        return top.language.iso_code_639_1.name.lower()
    return ''


def _detect_language(request, query: str = '') -> str:
    """Detect language from query text, then Accept-Language header, then give up."""
    if query:
        lang = _detect_language_from_query(query)
        if lang:
            return lang
    return preferences.detect_browser_lang(request)


def _provider_key_missing():
    """Map of provider key -> True when this deployment can't query it."""
    return {
        'brave': not settings.BRAVE_API_KEY,
        'mojeek': not settings.MOJEEK_API_KEY,
        'marginalia': not settings.MARGINALIA_API_KEY,
        'staan': not settings.STAAN_API_KEY,
        'thetvdb': not settings.THETVDB_API_KEY,
        'tripadvisor': not settings.TRIPADVISOR_API_KEY,
        'pixabay': not settings.PIXABAY_API_KEY,
        'worldnews': not settings.WORLDNEWS_API_KEY,
        'translate': not settings.LIBRETRANSLATE_URL,
    }


_TAB_ICONS = {
    'web': 'fa-globe', 'images': 'fa-image', 'news': 'fa-newspaper',
    'videos': 'fa-video', 'maps': 'fa-map', 'translate': 'fa-language',
}


def _tab_nav(available, tab_urls):
    """The tab nav in display order: one entry per tab this user can use."""
    return [
        {'key': key, 'label': label, 'icon': _TAB_ICONS[key], 'url': tab_urls[key]}
        for key, label, _icon in preferences.SEARCH_TYPES if key in available
    ]


def _available_tabs(prefs):
    """The set of result tabs the user's current choices can actually serve."""
    tabs = {'web'}
    for tab in ('images', 'news', 'videos'):
        if preferences.enabled_providers(prefs, tab):
            tabs.add(tab)
    if preferences.is_enabled(prefs, 'maps', 'openstreetmap'):
        tabs.add('maps')
    if bool(settings.LIBRETRANSLATE_URL) and preferences.is_enabled(prefs, 'translate', 'translate'):
        tabs.add('translate')
    return tabs


def _suggest_enabled(request):
    """Whether search-bar autocomplete is active for this request."""
    return preferences.is_enabled(preferences.load(request), 'web', 'brave')


def _with_db_cleanup(fn, *args, **kwargs):
    """Run *fn* (in a worker thread), then release that thread's DB connection."""
    from django.db import connections
    try:
        return fn(*args, **kwargs)
    finally:
        connections.close_all()


@login_required
def index(request):
    available = _available_tabs(preferences.load(request))
    results_url = reverse('search:results')
    return render(request, 'search/index.html', {
        'tabs': _tab_nav(available, {t: f'{results_url}?tab={t}' for t in available}),
    })


@login_required
def provider_status(request):
    """Status page: which external search providers are currently up or down."""
    if not settings.STATUS_PAGE_ENABLED:
        raise Http404('The provider status page is disabled on this instance.')
    return render(request, 'search/status.html', health.status_page_context())


def _monitor_token_ok(request):
    """Whether the request carries the monitor token, if one is configured."""
    expected = settings.STATUS_MONITOR_TOKEN
    if not expected:
        return True
    supplied = (
        request.GET.get('token')
        or request.headers.get('X-Monitor-Token')
        or request.headers.get('Authorization', '').removeprefix('Bearer ').strip()
    )
    # Compare bytes: compare_digest rejects non-ASCII str outright, and a
    # hostile token must fail the check, not raise a 500.
    return secrets.compare_digest((supplied or '').encode(), expected.encode())


def _monitor_response(payload, status):
    """JSON monitor reply, never cached, no outage should be served from a proxy."""
    response = JsonResponse(payload, status=status)
    response['Cache-Control'] = 'no-store'
    return response


@require_safe
def status_monitor(request):
    """Provider health as an HTTP status code, for an external uptime monitor."""
    if not settings.STATUS_MONITOR_ENABLED:
        raise Http404('The status monitor endpoint is disabled on this instance.')
    if not _monitor_token_ok(request):
        return _monitor_response({'status': 'error', 'detail': 'invalid token'}, 403)
    ok, payload = health.monitor_report()
    return _monitor_response(payload, 200 if ok else 500)


@require_safe
def provider_status_monitor(request, provider):
    """One provider's health as an HTTP status code: ``/status/health/<slug>``."""
    if not settings.STATUS_MONITOR_ENABLED:
        raise Http404('The status monitor endpoint is disabled on this instance.')
    if not _monitor_token_ok(request):
        return _monitor_response({'status': 'error', 'detail': 'invalid token'}, 403)
    report = health.provider_monitor_report(provider)
    if report is None:
        detail = (
            'provider not configured on this instance'
            if provider in health.PROVIDERS else 'unknown provider'
        )
        return _monitor_response({'status': 'error', 'provider': provider, 'detail': detail}, 404)
    ok, payload = report
    return _monitor_response(payload, 200 if ok else 500)


@login_required
def about(request):
    count = bang_service.count()
    # No-JS fallback for the bang search: when JavaScript can't run the live
    # box (about.js), the form submits ?bang=… and we filter server-side here.
    bang_query = request.GET.get('bang', '').strip()
    bang_matches, bang_total = (
        bang_service.search(bang_query, user=request.user) if bang_query else ([], 0)
    )
    return render(request, 'search/about.html', {
        'bang_count': count,
        'bang_count_display': f'{count:,}',
        'bang_query': bang_query,
        'bang_matches': bang_matches,
        'bang_total': bang_total,
    })


@login_required
def api(request):
    """Developer page - links to the API documentation site and documents the
    OpenSearch integration."""
    return render(request, 'search/api.html', {
        'opensearch_url': request.build_absolute_uri(reverse('search:opensearch_xml')),
        'suggest_url': request.build_absolute_uri(reverse('search:opensearch_suggest')),
        'create_key_url': _settings_pane_url('apikeys'),
        'public_api_enabled': settings.PUBLIC_API_ENABLED,
    })


@login_required
def results(request):
    # The translate tab submits its form via POST so the text to translate stays
    # out of the URL, history and access logs (and isn't length-capped); every
    # other tab and the quick filters are plain GET links. Read the shared query
    # and the translate params from whichever method the request used.
    data = request.POST if request.method == 'POST' else request.GET
    query = data.get('q', '').strip()
    prefs = preferences.load(request)
    # The knowledge cards and the weather answer decorate the *web* results, so
    # they're configured under the Web search type.
    disabled = preferences.disabled_for(prefs, 'web')

    tab = data.get('tab', 'web')
    if tab not in ('web', 'images', 'news', 'videos', 'maps', 'translate'):
        tab = 'web'

    try:
        page = max(1, int(request.GET.get('page', 1)))
    except (ValueError, TypeError):
        page = 1

    if 'lang' in request.GET:
        lang = request.GET['lang']
    else:
        lang_pref = prefs['search_lang']
        lang = lang_pref if lang_pref != 'auto' else _detect_language(request, query)
    safe_search = request.GET.get('safe', prefs['safe_search'])
    date = request.GET.get('date', '')

    translate_source = data.get('source', 'auto')
    translate_target = data.get('target') or (
        prefs['ui_lang'] if prefs['ui_lang'] != 'auto' else 'en'
    )
    # No-JS swap: the swap button submits ``swap`` so the server exchanges the
    # two languages (with JS, results.js swaps them client-side first). Skipped
    # when the source is auto-detect, which can't become a target.
    if data.get('swap') and translate_source != 'auto':
        translate_source, translate_target = translate_target, translate_source

    open_links_new_tab = prefs['open_links_new_tab']
    weather_enabled = 'weather' not in disabled
    maps_enabled = preferences.is_enabled(prefs, 'maps', 'openstreetmap')

    available = _available_tabs(prefs)
    translate_enabled = 'translate' in available
    if tab not in available:
        tab = bang_service.fallback_tab(available)

    # Per-request search scope: the scope quick-setting (a set of ``scope=``
    # params) picks which providers this one search hits, overriding the saved
    # engine/source preferences without changing them.
    requested_scope = preferences.requested_scope(tab, data.getlist('scope'))
    scope_active = bool(requested_scope)
    scope = requested_scope or preferences.enabled_providers(prefs, tab)
    engine = list(scope) if tab == 'web' else preferences.enabled_engines(prefs)
    media_engine = ['brave'] if 'brave' in scope else []
    pixabay_enabled = 'pixabay' in scope
    worldnews_enabled = 'worldnews' in scope
    sepia_enabled = 'sepia' in scope

    blocked = blocked_domains_for(request.user)
    key_missing = _provider_key_missing()
    # Notice flags for the web panel when it can't return anything.
    no_engines_enabled = not engine
    engines_unconfigured = bool(engine) and all(key_missing[e] for e in engine)

    # Bangs are a search-query feature; on the translate tab the query is plain
    # text to translate (which may legitimately start with "!"), so skip them.
    if query and tab != 'translate':
        lucky = bang_service.lucky_terms(query)
        if lucky:
            results, _ = fetch_web(lucky, engine, 1, safe_search, lang, date)
            results = filter_blocked(results, blocked)
            if results:
                return redirect(results[0]['url'])
            query = lucky
        else:
            bang_url = bang_service.resolve(query, user=request.user, available_tabs=available)
            if bang_url:
                return redirect(bang_url)

    params = request.GET.copy()
    if page > 1:
        params['page'] = page - 1
        prev_page_url = '?' + params.urlencode()
    else:
        prev_page_url = ''
    params['page'] = page + 1
    next_page_url = '?' + params.urlencode()

    # Per-tab URLs for the tab nav: real links (not CSS-radio labels) so tab
    # switching works without JavaScript. Each keeps the current query + filters,
    # swaps ``tab`` and resets paging.
    tab_params = request.GET.copy()
    tab_params.pop('page', None)
    tab_params.pop('tab', None)
    tab_urls = {}
    for available_tab in available:
        per_tab = tab_params.copy()
        per_tab['tab'] = available_tab
        tab_urls[available_tab] = '?' + per_tab.urlencode()

    context = {
        'query': query,
        'active_tab': tab,
        'available_tabs': available,
        'tabs': _tab_nav(available, tab_urls),
        'web_results': [],
        'image_results': [],
        'news_results': [],
        'video_results': [],
        'map_places': [],
        'map_primary': None,
        'map_embed_url': WORLD_EMBED_URL,
        'map_osm_url': WORLD_OSM_URL,
        'translation': None,
        'translate_unavailable': False,
        'translate_enabled': translate_enabled,
        'translate_source': translate_source,
        'translate_target': translate_target,
        'translate_languages': fetch_languages() if tab == 'translate' else [],
        'instant_answer': None,
        'defer_cards': False,
        'no_engines_enabled': no_engines_enabled,
        'engines_unconfigured': engines_unconfigured,
        'brave_key_missing': key_missing['brave'],
        'pixabay_key_missing': key_missing['pixabay'],
        'worldnews_key_missing': key_missing['worldnews'],
        'open_links_new_tab': open_links_new_tab,
        'proxy_images': prefs['proxy_images'],
        'similar_images': prefs['similar_images'],
        'scope_options': preferences.scope_view(tab, scope),
        'scope_count': len(scope),
        'scope_active': scope_active,
        'scope_keys': scope if scope_active else [],
        'page': page,
        'prev_page_url': prev_page_url,
        'next_page_url': next_page_url,
        'qs_lang': lang,
        'qs_safe': safe_search,
        'qs_date': date,
        'show_date_filter': tab in ('web', 'news', 'videos'),
    }

    if not query:
        return render(request, 'search/results.html', context)

    usage.record_search(request.user, len(engine) if tab == 'web' else 1)

    t0 = time.monotonic()
    # Queries are personal data: the raw text is only logged when the operator
    # opted in (LOG_SEARCH_QUERIES, on by default only in DEBUG); otherwise the
    # line carries just the length, keeping it useful for volume/latency work.
    q_label = repr(query) if settings.LOG_SEARCH_QUERIES else f'len={len(query)}'
    logger.info('search q=%s tab=%s providers=%s page=%d safe=%s lang=%s date=%s',
                q_label, tab, scope, page, safe_search, lang, date)

    if tab == 'web':
        # Lazy cards: render the web results + instant answer now and pull the
        # knowledge panel (Wikipedia/TheTVDB/TripAdvisor/Stack Exchange + the map
        # quick-answer) from /search/cards/ via JS, so a slow card never holds up
        # the answer. This is automatic, not a user setting: it's gated purely on
        # the ``js`` cookie (set by base.html) so we only defer when JavaScript is
        # confirmed.
        defer = (
            page == 1 and bool(query)
            and request.COOKIES.get('js') == '1'
            and request.GET.get('cards') != 'eager'
        )
        if page == 1:
            # The instant answer depends only on the query, so fetch it alongside
            # the web search instead of in series ahead of it.
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                f_instant = pool.submit(
                    _with_db_cleanup, detect_instant_answer, query, request,
                    weather_enabled=weather_enabled,
                )
                f_wikipedia = (
                    pool.submit(_with_db_cleanup, fetch_wikipedia, query, lang, safe_search)
                    if not defer and 'wikipedia' not in disabled else None
                )
                web_results, correction = fetch_web(query, engine, page, safe_search, lang, date)
                context['instant_answer'] = f_instant.result()
                context['wikipedia_card'] = f_wikipedia.result() if f_wikipedia else None
        else:
            web_results, correction = fetch_web(query, engine, page, safe_search, lang, date)
        web_results = filter_blocked(web_results, blocked)
        context['web_results'] = web_results
        if correction:
            corr_params = request.GET.copy()
            corr_params['q'] = correction
            corr_params.pop('page', None)
            context['correction'] = correction
            context['correction_url'] = '?' + corr_params.urlencode()
        if page == 1 and defer:
            context['defer_cards'] = True
            cards_params = {
                'q': query, 'lang': lang, 'safe': safe_search, 'date': date,
                'instant': '1' if context['instant_answer'] else '0',
                'scope': engine,
            }
            context['cards_url'] = reverse('search:cards') + '?' + urlencode(cards_params, doseq=True)
            # <noscript> fallback target: reload eagerly so a browser with the
            # ``js`` cookie set but JavaScript now off still gets the cards.
            full_path = request.get_full_path()
            context['cards_eager_url'] = full_path + ('&' if '?' in full_path else '?') + 'cards=eager'
        elif page == 1:
            cards = knowledge_cards(
                query, lang, context['web_results'], context['wikipedia_card'],
                disabled, maps_enabled=maps_enabled,
                instant_present=bool(context['instant_answer']),
            )
            context['thetvdb_card'] = cards['thetvdb_card']
            context['tripadvisor_card'] = cards['tripadvisor_card']
            context['stackexchange_card'] = cards['stackexchange_card']
            context['wikipedia_first'] = cards['wikipedia_first']
            # The map quick-answer shares the single instant-answer slot.
            if not context['instant_answer'] and cards['map_answer']:
                context['instant_answer'] = cards['map_answer']
    elif tab == 'maps':
        lat = request.GET.get('lat', '').strip()
        lon = request.GET.get('lon', '').strip()
        place = None
        if lat and lon:
            try:
                place = place_from_coords(
                    float(lat), float(lon),
                    name=request.GET.get('label', '') or query,
                )
            except (ValueError, TypeError):
                place = None
        if place:
            context['map_places'] = [place]
            context['map_primary'] = place
        elif query:
            places = fetch_geocode(query, limit=10, lang=lang)
            context['map_places'] = places
            context['map_primary'] = places[0] if places else None
        primary = context['map_primary']
        if primary:
            context['map_embed_url'] = primary.get('embed_url') or WORLD_EMBED_URL
            context['map_osm_url'] = primary.get('osm_url') or WORLD_OSM_URL
    elif tab == 'images':
        context['image_results'] = filter_blocked(
            fetch_images(query, media_engine, page, safe_search, lang, pixabay_enabled=pixabay_enabled), blocked,
        )
    elif tab == 'news':
        context['news_results'] = filter_blocked(
            fetch_news(query, media_engine, page, safe_search, lang, date, worldnews_enabled=worldnews_enabled), blocked,
        )
    elif tab == 'videos':
        context['video_results'] = filter_blocked(
            fetch_videos(query, media_engine, page, safe_search, lang, date, sepia_enabled=sepia_enabled), blocked,
        )
    elif tab == 'translate':
        translation = fetch_translation(query, translate_target, translate_source)
        context['translation'] = translation
        context['translate_unavailable'] = translation is None
        if translation and translation.get('detected_lang'):
            lang_names = dict(context['translate_languages'])
            context['translate_detected_name'] = lang_names.get(
                translation['detected_lang'], translation['detected_lang'],
            )

    n = len(
        context['web_results'] or context['image_results'] or
        context['news_results'] or context['video_results'] or
        context['map_places']
    )
    logger.info('search done q=%s tab=%s results=%d elapsed=%.2fs',
                q_label, tab, n, time.monotonic() - t0)

    # TripAdvisor's Master Terms (3.4.3) require pages displaying its content
    # to be excluded from search engine indexes.
    context['noindex'] = bool(context.get('tripadvisor_card'))
    response = render(request, 'search/results.html', context)
    if context['noindex']:
        response['X-Robots-Tag'] = 'noindex'
    return response


@login_required
def web_cards(request):
    """Deferred knowledge panel for the web tab, fetched by JS after the results."""
    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse({'panel': '', 'instant': ''})

    prefs = preferences.load(request)
    disabled = preferences.disabled_for(prefs, 'web')
    lang = request.GET.get('lang', '')
    safe_search = request.GET.get('safe', prefs['safe_search'])
    date = request.GET.get('date', '')
    instant_present = request.GET.get('instant') == '1'
    maps_enabled = preferences.is_enabled(prefs, 'maps', 'openstreetmap')
    # Honour the same per-request scope the originating search used (passed
    # through as ``scope=``), so this hits the cache the search just populated.
    engine = preferences.scope_engines(prefs, request.GET.getlist('scope'))
    blocked = blocked_domains_for(request.user)

    web_results, _ = fetch_web(query, engine, 1, safe_search, lang, date)
    web_results = filter_blocked(web_results, blocked)
    wikipedia_card = (
        fetch_wikipedia(query, lang, safe_search) if 'wikipedia' not in disabled else None
    )
    cards = knowledge_cards(
        query, lang, web_results, wikipedia_card, disabled,
        maps_enabled=maps_enabled, instant_present=instant_present,
    )

    has_card = bool(
        wikipedia_card or cards['thetvdb_card']
        or cards['tripadvisor_card'] or cards['stackexchange_card']
    )
    panel_html = ''
    if has_card:
        panel_html = render_to_string('cards/_panel.html', {
            'wikipedia_card': wikipedia_card,
            'wikipedia_first': cards['wikipedia_first'],
            'thetvdb_card': cards['thetvdb_card'],
            'tripadvisor_card': cards['tripadvisor_card'],
            'stackexchange_card': cards['stackexchange_card'],
            'open_links_new_tab': prefs['open_links_new_tab'],
            'proxy_images': prefs['proxy_images'],
        }, request=request)
    instant_html = ''
    if cards['map_answer']:
        instant_html = render_to_string(
            'instant/answer.html', {'answer': cards['map_answer']}, request=request,
        )
    response = JsonResponse({'panel': panel_html, 'instant': instant_html})
    if cards['tripadvisor_card']:
        # Same TripAdvisor non-indexability requirement as the eager render in
        # `results`, this endpoint is how the card reaches the page when cards
        # are deferred.
        response['X-Robots-Tag'] = 'noindex'
    return response


@login_required
def suggest(request):
    q = request.GET.get('q', '').strip()
    if not q or not _suggest_enabled(request):
        return JsonResponse({'suggestions': []})
    return JsonResponse({'suggestions': fetch_suggestions(q)})


def _thumb_src(result, proxy):
    """Thumbnail URL for an image result, proxied when the viewer opted in (or
    unconditionally for a host like Pixabay that prohibits hotlinking)."""
    src = (result.get('thumbnail') or {}).get('src', '')
    return proxify(src) if src and (proxy or must_proxy(src)) else src


def _similar_images_for(request, title, query, *, limit=12):
    """``(prefs, seed, results)`` for the similar-images grid, shared by the
    lightbox endpoint and the no-JS detail page."""
    prefs = preferences.load(request)
    if not prefs['similar_images']:
        return prefs, '', []
    scope = preferences.scope_providers(prefs, 'images', request.GET.getlist('scope'))
    seed, results = fetch_similar_images(
        title, query,
        engine=['brave'] if 'brave' in scope else [],
        safe_search=request.GET.get('safe', prefs['safe_search']),
        lang=request.GET.get('lang', ''),
        pixabay_enabled='pixabay' in scope,
        exclude_url=request.GET.get('src', '').strip(),
    )
    if seed:
        usage.record_search(request.user)
    results = filter_blocked(results, blocked_domains_for(request.user))[:limit]
    return prefs, seed, results


@login_required
def image_similar(request):
    """Real similar images for the Images-tab lightbox (lazy, per opened image)."""
    title = request.GET.get('q', '').strip()
    query = request.GET.get('query', '').strip()
    prefs, seed, results = _similar_images_for(request, title, query)
    proxy = prefs['proxy_images']
    images = [{
        'title': r.get('title', ''),
        'url': r.get('url', ''),
        'source': r.get('source', ''),
        'thumb': _thumb_src(r, proxy),
    } for r in results]
    return JsonResponse({'query': seed, 'images': images})


@login_required
def image_detail(request):
    """No-JS fallback for the Images-tab lightbox."""
    def _http(url):
        # Params are caller-supplied; only ever surface http(s) URLs, never a
        # javascript:/data: scheme an href or img src could act on.
        try:
            return url if urlparse(url).scheme in ('http', 'https') else ''
        except ValueError:
            return ''

    title = request.GET.get('q', '').strip()
    query = request.GET.get('query', '').strip()
    prefs, seed, similar = _similar_images_for(request, title, query)
    return render(request, 'images/detail.html', {
        'title': title,
        'query': query,
        'image_url': _http(request.GET.get('img', '').strip()),
        'source_url': _http(request.GET.get('src', '').strip()),
        'source': request.GET.get('source', '').strip(),
        'similar_images_results': similar,
        'seed': seed,
        'qs_lang': request.GET.get('lang', ''),
        'qs_safe': request.GET.get('safe', prefs['safe_search']),
        'scope_keys': preferences.requested_scope('images', request.GET.getlist('scope')),
        'proxy_images': prefs['proxy_images'],
        'similar_images': prefs['similar_images'],
    })


def _opensearch_limited(request, scope):
    """Per-IP budget for the public OpenSearch endpoints.

    They carry no login, and suggest keystrokes hit Brave's *paid* suggest
    API, so an anonymous caller could otherwise drain the quota freely. The
    limit (``OPENSEARCH_THROTTLE_LIMIT`` per ``.._WINDOW`` seconds) leaves a
    browser firing a request per keystroke plenty of room; counters live in
    the shared cache (one budget across workers, see accounts.ratelimit).
    """
    from accounts import ratelimit
    count = ratelimit.hit(scope, ratelimit.client_ip(request), settings.OPENSEARCH_THROTTLE_WINDOW)
    return count > settings.OPENSEARCH_THROTTLE_LIMIT


def opensearch_suggest(request):
    """OpenSearch-format autocomplete, public (no login) so browsers can use it."""
    q = request.GET.get('q', '').strip()
    if _opensearch_limited(request, 'opensearch-suggest'):
        # Valid-but-empty body so an address bar quietly shows no suggestions.
        return JsonResponse([q, []], safe=False, status=429)
    suggestions = fetch_suggestions(q) if q and _suggest_enabled(request) else []
    # OpenSearch Suggestions 1.1 format: ["query", ["s1", "s2", ...]]
    return JsonResponse([q, suggestions], safe=False)


def opensearch_xml(request):
    """OpenSearch description document, no login required so browsers can fetch it."""
    if _opensearch_limited(request, 'opensearch-xml'):
        return HttpResponse(status=429)
    base = request.build_absolute_uri('/').rstrip('/')
    logo = request.build_absolute_uri(static('search/logo.svg'))

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/"\n'
        '    xmlns:moz="http://www.mozilla.org/2006/browser/search/">\n'
        '  <ShortName>Seurch</ShortName>\n'
        '  <Description>Privacy-first European search</Description>\n'
        '  <InputEncoding>UTF-8</InputEncoding>\n'
        f'  <Image height="16" width="16" type="image/svg+xml">{logo}</Image>\n'
        f'  <Url type="text/html" method="get" template="{base}/search/?q={{searchTerms}}"/>\n'
        f'  <Url type="application/x-suggestions+json" method="get" template="{base}/opensearch-suggest/?q={{searchTerms}}"/>\n'
        f'  <moz:SearchForm>{base}/</moz:SearchForm>\n'
        '</OpenSearchDescription>\n'
    )
    resp = HttpResponse(xml, content_type='application/opensearchdescription+xml')
    resp['Access-Control-Allow-Origin'] = '*'
    resp['Cache-Control'] = 'public, max-age=86400'
    return resp


def _image_response(content, content_type):
    """Proxied image bytes → response that can't turn into markup on this origin."""
    resp = HttpResponse(content, content_type=safe_content_type(content_type))
    resp['X-Content-Type-Options'] = 'nosniff'
    resp['Content-Security-Policy'] = "default-src 'none'"
    return resp


@login_required
def image_proxy(request):
    """Fetch and return an image previously registered via ``imageproxy.proxify``."""
    from .models import ProxiedImage

    try:
        token = uuid.UUID(request.GET.get('id', '').strip())
    except (ValueError, TypeError, AttributeError):
        return HttpResponse(status=400)

    try:
        entry = ProxiedImage.objects.get(id=token, expires_at__gt=timezone.now())
    except ProxiedImage.DoesNotExist:
        return HttpResponse(status=404)

    # Pixabay's API terms prohibit hotlinking: rather than relaying live from
    # its CDN on every hit, the image is downloaded once and stored on the row
    # (`persist`), so every later request (any user, until the row expires) is
    # served straight from here without contacting Pixabay again.
    if entry.persist and entry.content:
        return _image_response(bytes(entry.content), entry.content_type or 'image/jpeg')

    url = entry.url
    parsed = urlparse(url)
    host = parsed.hostname or ''
    headers = {
        # Wikimedia (and some other CDNs) reject requests without a descriptive
        # User-Agent per their UA policy, httpx's default UA gets a 403.
        'User-Agent': USER_AGENT,
    }
    # TripAdvisor's media CDN only serves images with a matching Referer.
    if host.lower().endswith('tripadvisor.com'):
        headers['Referer'] = 'https://www.tripadvisor.com/'

    # The host was only vetted when the URL was registered; its DNS record may
    # say something else by now (rebinding). Re-resolve at fetch time, refuse
    # anything that maps to a private/reserved address, and connect to the
    # exact IP just validated (Host header + TLS SNI keep the original name).
    # Redirects are not followed: a 30x from the pinned host could point
    # anywhere, including back inside the network this check just excluded.
    ip = resolve_public_address(url)
    if ip is None:
        return HttpResponse(status=204)
    netloc = f'[{ip}]' if ':' in ip else ip
    if parsed.port:
        netloc = f'{netloc}:{parsed.port}'
    pinned_url = parsed._replace(netloc=netloc).geturl()
    headers['Host'] = parsed.netloc

    try:
        with httpx.Client(timeout=8, follow_redirects=False) as client:
            r = client.get(pinned_url, headers=headers, extensions={'sni_hostname': host})
            r.raise_for_status()
            content_type = safe_content_type(r.headers.get('content-type', 'image/jpeg'))
            if entry.persist:
                entry.content = r.content
                entry.content_type = content_type
                entry.save(update_fields=['content', 'content_type'])
            resp = _image_response(r.content, content_type)
            resp['Cache-Control'] = 'public, max-age=86400'
            return resp
    except Exception:
        return HttpResponse(status=204)


def _settings_pane_url(pane):
    """Canonical URL for a settings pane. ``general`` lives at the section root
    (``/settings/``); every other pane is ``/settings/<slug>/``."""
    if pane == 'general':
        return reverse('search:settings')
    return reverse('search:settings_pane', kwargs={'pane': _PANE_SLUGS[pane]})


def _user_api_keys(user):
    """The user's active (non-revoked) public-API keys, newest first."""
    from api.models import ApiKey
    return list(ApiKey.objects.filter(user=user, revoked=False))


def _user_session_link(user):
    """The user's private-browsing session link, or ``None`` if never generated."""
    from accounts.models import SessionLink
    try:
        return user.session_link
    except SessionLink.DoesNotExist:
        return None


_BOOL_PREF_SETTINGS = ('safe_search', 'open_links_new_tab', 'proxy_images', 'similar_images')

_CHOICE_PREF_SETTINGS = {
    'search_lang': (preferences.LANG_CHOICES, 'auto'),
    'ui_lang': (preferences.UI_LANG_CHOICES, 'auto'),
}


def _apply_provider_setting(request, prefs):
    """One search type's provider toggles (Settings → Engines posts a form per
    type). A provider is enabled when its box is ticked, so everything unticked
    lands in that type's disabled set; the other search types keep whatever the
    user chose for them."""
    search_type = request.POST.get('search_type', '')
    if search_type not in preferences.SEARCH_TYPE_KEYS:
        return
    off = [
        p for p in preferences.type_providers(search_type)
        if request.POST.get(f'provider_{p}') != 'on'
    ]
    disabled = dict(prefs['disabled_providers'])
    if off:
        disabled[search_type] = off
    else:
        disabled.pop(search_type, None)
    prefs['disabled_providers'] = disabled
    logger.info('settings user=%s %s disabled_providers=%s',
                request.user.username, search_type, off)


def _apply_bang_setting(request, setting):
    """Add or remove one of the user's custom bangs."""
    if setting == 'delete_custom_bang':
        _delete_own(CustomBang, request.user, request.POST.get('bang_id', ''))
        messages.success(request, 'Custom bang removed.')
        return

    trigger = request.POST.get('trigger', '').strip().lstrip('!').lower()
    url_template = request.POST.get('url_template', '').strip()
    if not trigger or not _CUSTOM_BANG_TRIGGER_RE.match(trigger):
        messages.error(request, 'Trigger must be alphanumeric (dots, dashes, underscores allowed).')
    elif '{{{s}}}' not in url_template:
        messages.error(request, 'URL template must contain the placeholder {{{s}}} where the query goes.')
    elif not url_template.lower().startswith(('http://', 'https://')):
        messages.error(request, 'URL template must start with http:// or https://.')
    elif not backup.valid_bang_url(url_template):
        messages.error(request, 'URL template must be a valid web address (no < or > characters).')
    else:
        CustomBang.objects.update_or_create(
            user=request.user, trigger=trigger, defaults={'url_template': url_template},
        )
        logger.info('settings user=%s add_custom_bang=%s', request.user.username, trigger)
        messages.success(request, f'Custom bang !{trigger} saved.')


def _apply_blocked_site_setting(request, setting):
    """Add or remove one of the user's blocked sites."""
    if setting == 'delete_blocked_site':
        _delete_own(BlockedSite, request.user, request.POST.get('site_id', ''))
        messages.success(request, 'Site removed from blocked list.')
        return

    domain = normalize_domain(request.POST.get('domain', ''))
    if not domain or '.' not in domain:
        messages.error(request, 'Enter a valid domain (e.g. example.com).')
        return
    _, created = BlockedSite.objects.get_or_create(user=request.user, domain=domain)
    # Like search queries, a blocked domain reveals what the user browses; only
    # pair it with their name when the operator opted into verbose logging.
    logger.info('settings user=%s add_blocked_site=%s created=%s', request.user.username,
                domain if settings.LOG_SEARCH_QUERIES else '[redacted]', created)
    messages.success(request, f'{domain} added to your blocked sites.' if created
                     else f'{domain} is already blocked.')


def _apply_email_setting(request, setting):
    """Set or clear the account's email address."""
    if not request.user.check_password(request.POST.get('current_password', '')):
        messages.error(request, 'Your password was incorrect, email unchanged.')
        return

    if setting == 'delete_email':
        if request.user.email:
            request.user.email = ''
            request.user.save(update_fields=['email'])
            logger.info('settings user=%s email_removed', request.user.username)
        messages.success(request, 'Email address removed. Without one, your password can no longer be reset.')
        return

    email = request.POST.get('email', '').strip()
    if not email:
        messages.error(request, 'Enter an email address.')
        return
    try:
        if len(email) > 254:  # column limit of User.email
            raise ValidationError('email too long')
        validate_email(email)
    except ValidationError:
        messages.error(request, 'Enter a valid email address.')
        return
    request.user.email = BaseUserManager.normalize_email(email)
    request.user.save(update_fields=['email'])
    logger.info('settings user=%s email_updated', request.user.username)
    messages.success(request, 'Email address saved.')


def _apply_api_key_setting(request, setting):
    """Create or revoke one of the user's public-API keys."""
    from api.models import ApiKey

    if setting == 'delete_api_key':
        key_id = request.POST.get('key_id', '')
        try:
            ApiKey.objects.filter(user=request.user, pk=int(key_id)).update(revoked=True)
        except (TypeError, ValueError):
            pass
        logger.info('settings user=%s delete_api_key=%s', request.user.username, key_id)
        messages.success(request, 'API key revoked.')
        return

    if not settings.PUBLIC_API_ENABLED:
        messages.error(request, 'The public API is disabled on this instance.')
    elif ApiKey.objects.filter(user=request.user, revoked=False).count() >= API_KEY_LIMIT:
        messages.error(request, f'You already have the maximum of {API_KEY_LIMIT} API keys. Revoke one first.')
    else:
        _, full_key = ApiKey.create(request.user, name=request.POST.get('key_name', '').strip() or 'API key')
        # Shown once on the next page render, then dropped; the secret is
        # unrecoverable afterwards.
        request.session['new_api_key'] = full_key
        logger.info('settings user=%s create_api_key', request.user.username)
        messages.success(request, 'API key created. Copy it now; it will not be shown again.')


def _apply_session_link_setting(request, setting):
    """Generate or turn off the user's private-browsing session link. Either way
    the sessions opened through the old link are terminated."""
    from accounts.models import SessionLink

    token = None
    if setting == 'generate_session_link':
        _, token = SessionLink.generate(request.user)
        note = 'Private session link generated. Copy it now; it will not be shown again.'
    else:
        SessionLink.objects.filter(user=request.user).delete()
        note = 'Private session link turned off.'
    # Ends the sessions opened through the old link, and flushes this request's
    # own session when Settings is being used from inside one of them - so the
    # link has to be stashed after this call, not before, or it goes with it.
    SessionLink.terminate_sessions(request)
    if token is not None:
        request.session['new_session_link_url'] = request.build_absolute_uri(
            reverse('private_session_login', kwargs={'token': token}),
        )
    logger.info('settings user=%s %s', request.user.username, setting)
    messages.success(request, note)


def _delete_own(model, user, raw_pk):
    """Delete *user*'s own row of *model*, ignoring a non-numeric id."""
    try:
        model.objects.filter(user=user, pk=int(raw_pk)).delete()
    except (TypeError, ValueError):
        pass


def _apply_setting(request, setting, prefs):
    """Apply one settings form submission."""
    if setting == 'providers':
        _apply_provider_setting(request, prefs)
    elif setting in _BOOL_PREF_SETTINGS:
        value = request.POST.get(setting) == 'on'
        # Safe search is stored as on/off rather than a bool, for the URL
        # parameter of the same name.
        prefs[setting] = ('on' if value else 'off') if setting == 'safe_search' else value
        logger.info('settings user=%s %s=%s', request.user.username, setting, prefs[setting])
    elif setting in _CHOICE_PREF_SETTINGS:
        choices, fallback = _CHOICE_PREF_SETTINGS[setting]
        chosen = request.POST.get(setting, fallback)
        prefs[setting] = chosen if chosen in choices else fallback
        logger.info('settings user=%s %s=%s', request.user.username, setting, prefs[setting])
    elif setting in ('add_custom_bang', 'delete_custom_bang'):
        _apply_bang_setting(request, setting)
    elif setting in ('add_blocked_site', 'delete_blocked_site'):
        _apply_blocked_site_setting(request, setting)
    elif setting in ('email', 'delete_email'):
        _apply_email_setting(request, setting)
    elif setting in ('create_api_key', 'delete_api_key'):
        _apply_api_key_setting(request, setting)
    elif setting in ('generate_session_link', 'delete_session_link'):
        _apply_session_link_setting(request, setting)


@login_required
def settings_view(request, pane=None):
    if request.method == 'POST':
        setting = request.POST.get('setting', 'providers')
        prefs = preferences.load(request)
        _apply_setting(request, setting, prefs)

        pane = request.POST.get('pane', '')
        if pane not in _SETTINGS_PANES:
            pane = 'general'
        # The toggles/selects auto-save in the background via fetch(): that path
        # stays on the page and just wants an ack, so reply with JSON instead of
        # a redirect (and skip the flash message, there's no reload to show it).
        # Without JS the same form submits normally and we redirect as before.
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        response = JsonResponse({'ok': True}) if is_ajax else redirect(_settings_pane_url(pane))

        # Preference settings live in the cookie; persist it on the response.
        if setting in _PREF_SETTINGS:
            preferences.apply_to_response(response, prefs)
            backup.sync_to_db(request, response, prefs=prefs)
            if not is_ajax:
                messages.success(request, 'Settings saved.')
        elif setting not in _ACCOUNT_SETTINGS:
            # DB-backed setting (bang/blocked site), sync the full document to DB.
            backup.sync_to_db(request, response)
        return response

    # /settings/<slug>/ — map the URL slug to its internal pane key; bare
    # /settings/ opens on the first pane.
    active_pane = _PANE_BY_SLUG.get(pane, 'general') if pane is not None else 'general'
    prefs = preferences.load(request)
    session_link = _user_session_link(request.user)
    new_session_link_url = request.session.pop('new_session_link_url', None)
    return render(request, 'search/settings.html', {
        'active_pane': active_pane,
        'search_types': preferences.search_types_view(prefs, _provider_key_missing()),
        'safe_search': prefs['safe_search'],
        'search_lang': prefs['search_lang'],
        'ui_lang': prefs['ui_lang'],
        'theme': prefs['theme'],
        'open_links_new_tab': prefs['open_links_new_tab'],
        'proxy_images': prefs['proxy_images'],
        'similar_images': prefs['similar_images'],
        'custom_bangs': list(CustomBang.objects.filter(user=request.user)),
        'blocked_sites': list(BlockedSite.objects.filter(user=request.user)),
        'ui_lang_choices': preferences.UI_LANG_CHOICES,
        'search_count': usage.searches_this_month(request.user),
        'manual_search_url': request.build_absolute_uri(reverse('search:results')) + '?q=%s',
        'api_keys': _user_api_keys(request.user),
        'new_api_key': request.session.pop('new_api_key', None),
        'api_docs_url': reverse('search:api'),
        'public_api_enabled': settings.PUBLIC_API_ENABLED,
        'session_link': session_link,
        'new_session_link_url': new_session_link_url,
        'new_session_link_search_url': f'{new_session_link_url}?q=%s' if new_session_link_url else None,
    })


@require_POST
def settings_theme(request):
    """Persist the colour theme, for both theme.js (AJAX) and the no-JS forms."""
    from django.utils.http import url_has_allowed_host_and_scheme

    theme = request.POST.get('theme', 'system')
    prefs = preferences.load(request)
    prefs['theme'] = theme if theme in preferences.THEME_CHOICES else 'system'

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        response = JsonResponse({'ok': True})
    else:
        next_url = request.POST.get('next') or ''
        if not url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
        ):
            next_url = _settings_pane_url('appearance')
        response = redirect(next_url)

    preferences.apply_to_response(response, prefs)
    if request.user.is_authenticated:
        backup.sync_to_db(request, response, prefs=prefs)
    return response


@login_required
def settings_export(request):
    """Download the user's full settings (preferences + bangs + blocked sites) as JSON."""
    doc = backup.build_document(request)
    response = HttpResponse(json.dumps(doc, indent=2), content_type='application/json')
    response['Content-Disposition'] = 'attachment; filename="seurch-settings.json"'
    logger.info('settings user=%s export', request.user.username)
    return response


@login_required
@require_POST
def settings_import(request):
    """Apply an uploaded settings JSON file, replacing preferences, bangs and blocked sites."""
    target = _settings_pane_url('backup')
    upload = request.FILES.get('settings_file')
    if upload is None:
        messages.error(request, 'Choose a settings file to import.')
        return redirect(target)
    if upload.size > 256 * 1024:
        messages.error(request, 'That settings file is too large.')
        return redirect(target)
    try:
        doc = json.loads(upload.read().decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        messages.error(request, 'That file is not valid JSON.')
        return redirect(target)

    response = redirect(target)
    try:
        backup.apply_document(request, response, doc)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(target)
    logger.info('settings user=%s import ok', request.user.username)
    messages.success(request, 'Settings imported.')
    return response


@login_required
@require_POST
def block_site(request):
    """Quick-action: add a domain to the user's blocked list, then return where they came from."""
    from django.utils.http import url_has_allowed_host_and_scheme
    domain = normalize_domain(request.POST.get('domain', ''))
    next_url = request.POST.get('next') or ''
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = '/'
    response = redirect(next_url)
    if domain and '.' in domain:
        BlockedSite.objects.get_or_create(user=request.user, domain=domain)
        backup.sync_to_db(request, response)
        messages.success(request, f'{domain} added to your blocked sites.')
        logger.info('block_site user=%s domain=%s', request.user.username,
                    domain if settings.LOG_SEARCH_QUERIES else '[redacted]')
    else:
        messages.error(request, 'Invalid domain.')
    return response


@login_required
def custom_bangs_json(request):
    """Expose the signed-in user's custom bangs as ``{trigger: url_template}``."""
    data = dict(CustomBang.objects.filter(user=request.user).values_list('trigger', 'url_template'))
    resp = JsonResponse(data)
    resp['Cache-Control'] = 'private, no-store'
    return resp
