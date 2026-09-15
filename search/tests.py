"""Tests for the core search app: results orchestration, settings, status, bangs.

These tests were generated with an LLM and then reviewed by hand. Treat a
failure as a real signal, but read the assertion before trusting it: a test
here can encode an assumption the code never promised. Fix or delete such a
test rather than bending the code to satisfy it.
"""

import json
import re
import uuid
from datetime import UTC, timedelta
from unittest.mock import MagicMock, patch

import httpx
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from cards.relevance import (
    _contains_word,
    _is_movie_or_tv,
    _is_travel_query,
    _name_is_relevant,
    _shares_significant_token,
    _significant_tokens,
    _title_match,
)
from cards.stackexchange import fetch_stackexchange
from cards.thetvdb import fetch_thetvdb
from cards.tripadvisor import _TA_LOCALES, _locales, _query_category, fetch_tripadvisor
from cards.wikidata import entity_tags, is_adult_subject
from cards.wikipedia import _fetch_wikipedia, _looks_explicit, fetch_wikipedia
from images.services import _clean_caption, fetch_images, fetch_similar_images
from maps.services import (
    WORLD_EMBED_URL,
    WORLD_OSM_URL,
    _build_place,
    fetch_geocode,
    looks_like_place,
    place_from_coords,
)
from news.services import fetch_news
from search import health
from search.clients import _brave_request, _staan_request, brave_safesearch
from search.models import ProviderStatus
from videos.services import fetch_videos
from web.services import (
    _highlight,
    _merge_web,
    _norm_brave,
    _norm_marginalia,
    _norm_mojeek,
    _norm_staan,
    _query_terms,
    fetch_suggestions,
    fetch_web,
)

from . import bangs, preferences
from .views import _available_tabs, _detect_language, _detect_language_from_query


def _fake_httpx_client(payloads):
    """Build a stand-in for ``httpx.Client`` whose ``get`` returns responses
    yielding ``payloads`` (parsed JSON) in order. Returns ``(context_manager,
    client)`` so tests can assert on the recorded calls."""
    client = MagicMock()
    responses = []
    for payload in payloads:
        resp = MagicMock()
        resp.json.return_value = payload
        resp.raise_for_status.return_value = None
        responses.append(resp)
    client.get.side_effect = responses
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = False
    return cm, client


def _disabled_providers(only_engine=None, providers_off=()):
    """A ``disabled_providers`` map switching providers off for every search type.

    Preferences are per-search-type, which is verbose to spell out when a test
    only cares that a provider is off everywhere. ``only_engine`` leaves that
    search engine as the sole one enabled; ``providers_off`` names providers to
    switch off outright. `coerce` drops the entries a given type can't use.
    """
    from search import preferences
    off = set(providers_off)
    if only_engine:
        off |= set(preferences.REAL_ENGINES) - {only_engine}
    if not off:
        return {}
    return {t: sorted(off) for t in preferences.SEARCH_TYPE_KEYS}


def _prefs(*, only_engine=None, providers_off=(), **prefs):
    """A coerced preferences dict, as a view would read it back from the cookie."""
    from search import preferences
    disabled = _disabled_providers(only_engine, providers_off)
    if disabled:
        prefs.setdefault('disabled_providers', disabled)
    return preferences.coerce(prefs)


def _set_prefs(client, *, only_engine=None, providers_off=(), **prefs):
    """Seed the preferences cookie on the test *client* (settings live in a cookie)."""
    from search import preferences
    disabled = _disabled_providers(only_engine, providers_off)
    if disabled:
        prefs.setdefault('disabled_providers', disabled)
    client.cookies[preferences.COOKIE_NAME] = preferences.encode(prefs)


def _get_prefs(client):
    """Read the preferences cookie the *client* currently holds as a clean dict."""
    from search import preferences
    morsel = client.cookies.get(preferences.COOKIE_NAME)
    return preferences.decode(morsel.value if morsel else None)


def _save_providers(search_type, **on):
    """POST body saving one search type's provider toggles (unlisted ones = off)."""
    return {'setting': 'providers', 'search_type': search_type, 'pane': 'engine', **on}


def _save_web(**on):
    """POST body for the Web search type's provider toggles."""
    return _save_providers('web', **on)


def _web_engines(client):
    """Search engines the *client*'s cookie leaves enabled for the Web type."""
    return preferences.enabled_engines(_get_prefs(client))


def _type_group(resp, search_type):
    """The Settings → Engines group for *search_type* out of a rendered response."""
    return next(g for g in resp.context['search_types'] if g['key'] == search_type)


def _type_form(content, search_type):
    """The Settings → Engines form markup for one search type."""
    return re.search(
        rf'name="search_type" value="{search_type}">(.*?)</form>', content, re.DOTALL,
    ).group(1)


def _provider_row(content, search_type, provider):
    """A provider's whole row (brand, badges, description, toggle) in its own form."""
    rows = _type_form(content, search_type).split('<div class="flex items-center justify-between')
    return next(row for row in rows if f'name="provider_{provider}"' in row)


def _provider_input(content, search_type, provider):
    """One provider's toggle ``<input>`` inside its own search-type form."""
    return re.search(
        rf'<input[^>]*name="provider_{provider}"[^>]*>', _type_form(content, search_type),
    ).group(0)


class NormBraveTests(TestCase):
    def test_full_result(self):
        raw = {
            'title': 'Example',
            'url': 'https://example.com/page',
            'description': 'A page',
            'meta_url': {'netloc': 'example.com', 'path': '/page'},
            'profile': {'img': 'https://example.com/favicon.png'},
            'deep_results': {'buttons': [{'title': 'Sub', 'url': 'https://example.com/sub'}]},
        }
        r = _norm_brave(raw)
        self.assertEqual(r['title'], 'Example')
        self.assertEqual(r['url'], 'https://example.com/page')
        self.assertEqual(r['description'], 'A page')
        self.assertEqual(r['display_url'], 'example.com /page')
        self.assertEqual(r['favicon_url'], 'https://example.com/favicon.png')
        self.assertEqual(r['source'], 'brave')
        self.assertEqual(len(r['sitelinks']), 1)

    def test_minimal_result(self):
        r = _norm_brave({'title': 'T', 'url': 'https://t.com'})
        self.assertEqual(r['source'], 'brave')
        self.assertEqual(r['favicon_url'], '')
        self.assertEqual(r['sitelinks'], [])

    def test_display_url_no_path(self):
        raw = {'meta_url': {'netloc': 'example.com', 'path': ''}}
        r = _norm_brave(raw)
        self.assertEqual(r['display_url'], 'example.com')


class NormMojeekTests(TestCase):
    def test_full_result(self):
        raw = {'title': 'Mojeek', 'url': 'https://mojeek.com/about', 'desc': 'About'}
        r = _norm_mojeek(raw)
        self.assertEqual(r['title'], 'Mojeek')
        self.assertEqual(r['url'], 'https://mojeek.com/about')
        self.assertEqual(r['description'], 'About')
        self.assertEqual(r['display_url'], 'mojeek.com')
        self.assertEqual(r['favicon_url'], '')
        self.assertEqual(r['sitelinks'], [])
        self.assertEqual(r['source'], 'mojeek')

    def test_invalid_url_returns_empty_display(self):
        r = _norm_mojeek({'url': 'not-a-url'})
        self.assertEqual(r['display_url'], '')

    def test_missing_desc(self):
        r = _norm_mojeek({'url': 'https://example.com'})
        self.assertEqual(r['description'], '')


class NormStaanTests(TestCase):
    def test_full_result(self):
        raw = {
            'title': '15 vegetarian recipes', 'url': 'https://example.com/veg',
            'snippet': 'Looking for vegetarian recipes?',
            'display_url': 'www.example.com > food > veg',
            'hostname': 'www.example.com',
            'favicon_url': 'https://s.qwant.com/v1/fav/example',
        }
        r = _norm_staan(raw)
        self.assertEqual(r['title'], '15 vegetarian recipes')
        self.assertEqual(r['url'], 'https://example.com/veg')
        self.assertEqual(r['description'], 'Looking for vegetarian recipes?')
        self.assertEqual(r['display_url'], 'www.example.com > food > veg')
        self.assertEqual(r['favicon_url'], 'https://s.qwant.com/v1/fav/example')
        self.assertEqual(r['sitelinks'], [])
        self.assertEqual(r['source'], 'staan')

    def test_hostname_backs_a_missing_display_url(self):
        r = _norm_staan({'url': 'https://example.com/veg', 'hostname': 'example.com'})
        self.assertEqual(r['display_url'], 'example.com')

    def test_minimal_result(self):
        r = _norm_staan({'url': 'https://example.com'})
        self.assertEqual(r['description'], '')
        self.assertEqual(r['display_url'], '')
        self.assertEqual(r['favicon_url'], '')
        # published_date only comes back with the enrichment modes the plain
        # search doesn't request, so a result never carries an age.
        self.assertEqual(r['age'], '')


class MergeWebTests(TestCase):
    def _brave(self, url, title='B'):
        return _norm_brave({
            'title': title, 'url': url, 'description': '',
            'meta_url': {'netloc': url.split('/')[2], 'path': ''},
            'profile': {},
        })

    def _mojeek(self, url, title='M'):
        return _norm_mojeek({'title': title, 'url': url, 'desc': ''})

    def _marginalia(self, url, title='X'):
        return _norm_marginalia({'title': title, 'url': url, 'description': ''})

    def _staan(self, url, title='S'):
        return _norm_staan({'title': title, 'url': url, 'snippet': ''})

    def test_deduplication_marks_both(self):
        merged = _merge_web([
            ('brave', [self._brave('https://shared.com')]),
            ('mojeek', [self._mojeek('https://shared.com')]),
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['source'], 'both')
        # Brave is never named in the label (see _merge_web); 'source' itself
        # still tracks that Brave contributed, just not the displayed text.
        self.assertEqual(merged[0]['source_label'], 'Mojeek')

    def test_triple_deduplication(self):
        merged = _merge_web([
            ('brave', [self._brave('https://shared.com')]),
            ('mojeek', [self._mojeek('https://shared.com')]),
            ('marginalia', [self._marginalia('https://shared.com')]),
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['source'], 'all')
        self.assertEqual(merged[0]['source_label'], 'Mojeek · Marginalia')

    def test_staan_joins_the_fusion(self):
        # A URL Staan agrees on gains its RRF contribution like any other engine.
        merged = _merge_web([
            ('mojeek', [self._mojeek('https://shared.com')]),
            ('staan', [self._staan('https://shared.com')]),
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['source'], 'both')
        self.assertEqual(merged[0]['source_label'], 'Mojeek · Staan')

    def test_staan_only(self):
        merged = _merge_web([('staan', [self._staan('https://s.com')])])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['source'], 'staan')
        self.assertEqual(merged[0]['source_label'], 'Staan')

    def test_unique_results_interleaved(self):
        merged = _merge_web([
            ('brave', [self._brave('https://b1.com'), self._brave('https://b2.com')]),
            ('mojeek', [self._mojeek('https://m1.com'), self._mojeek('https://m2.com')]),
        ])
        urls = [r['url'] for r in merged]
        self.assertIn('https://b1.com', urls)
        self.assertIn('https://m1.com', urls)
        self.assertEqual(len(merged), 4)

    def test_shared_url_not_duplicated(self):
        merged = _merge_web([
            ('brave', [self._brave('https://shared.com'), self._brave('https://brave-only.com')]),
            ('mojeek', [self._mojeek('https://shared.com'), self._mojeek('https://mojeek-only.com')]),
        ])
        urls = [r['url'] for r in merged]
        self.assertEqual(urls.count('https://shared.com'), 1)
        self.assertIn('https://brave-only.com', urls)
        self.assertIn('https://mojeek-only.com', urls)

    def test_shared_result_ranked_above_single_engine_tops(self):
        # A URL only Mojeek returns sits at its #1 slot; a URL both engines
        # return sits lower in each list. Reciprocal Rank Fusion sums the two
        # engines' contributions, so the agreed-on result outranks the one a
        # single engine put first.
        merged = _merge_web([
            ('brave', [
                self._brave('https://brave-top.com'),
                self._brave('https://brave-top2.com'),
                self._brave('https://shared.com'),
            ]),
            ('mojeek', [
                self._mojeek('https://mojeek-top.com'),
                self._mojeek('https://shared.com'),
            ]),
        ])
        urls = [r['url'] for r in merged]
        shared_i = urls.index('https://shared.com')
        self.assertLess(shared_i, urls.index('https://mojeek-top.com'))
        self.assertLess(shared_i, urls.index('https://brave-top2.com'))
        self.assertEqual(merged[shared_i]['source'], 'both')

    def test_single_engine_order_preserved(self):
        # With one engine, the engine's own ranking is kept intact.
        merged = _merge_web([('brave', [
            self._brave('https://1.com'),
            self._brave('https://2.com'),
            self._brave('https://3.com'),
        ])])
        self.assertEqual([r['url'] for r in merged],
                         ['https://1.com', 'https://2.com', 'https://3.com'])

    def test_empty_inputs(self):
        self.assertEqual(_merge_web([]), [])
        self.assertEqual(_merge_web([('brave', []), ('mojeek', [])]), [])

    def test_brave_only(self):
        merged = _merge_web([('brave', [self._brave('https://b.com')])])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['source'], 'brave')
        # No badge text at all for a Brave-only result (see _merge_web).
        self.assertEqual(merged[0]['source_label'], '')

    def test_mojeek_only(self):
        merged = _merge_web([('mojeek', [self._mojeek('https://m.com')])])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['source'], 'mojeek')

    def test_marginalia_only(self):
        merged = _merge_web([('marginalia', [self._marginalia('https://x.com')])])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['source'], 'marginalia')


class QueryTermsTests(TestCase):
    def test_splits_dedupes_and_drops_short_words(self):
        # "a" is too short; "Piano"/"piano" collapse to one term, original case kept.
        self.assertEqual(_query_terms('a Piano piano lesson'), ['Piano', 'lesson'])

    def test_strips_punctuation(self):
        self.assertEqual(_query_terms('"node.js" guide!'), ['node', 'js', 'guide'])

    def test_blank_query(self):
        self.assertEqual(_query_terms(''), [])
        self.assertEqual(_query_terms(None), [])


class HighlightTests(TestCase):
    """`_highlight` gives Mojeek/Marginalia snippets the <strong> markup Brave
    adds itself."""

    def test_wraps_each_term_preserving_case(self):
        out = _highlight('Learn Piano with online lessons', 'piano lessons')
        self.assertEqual(out, 'Learn <strong>Piano</strong> with online <strong>lessons</strong>')

    def test_matches_whole_words_only(self):
        # "art" must not light up inside "cartoon" or "smart".
        out = _highlight('A smart cartoon about art', 'art')
        self.assertEqual(out, 'A smart cartoon about <strong>art</strong>')

    def test_all_occurrences_highlighted(self):
        out = _highlight('piano, piano, piano', 'piano')
        self.assertEqual(out, '<strong>piano</strong>, <strong>piano</strong>, <strong>piano</strong>')

    def test_entities_are_not_matched_into(self):
        # The apostrophe entity must survive intact; only the real word wraps.
        out = _highlight('l&#x27;art moderne', 'art')
        self.assertEqual(out, 'l&#x27;<strong>art</strong> moderne')

    def test_existing_tags_pass_through(self):
        out = _highlight('see <a href="x">piano</a> here', 'piano here')
        self.assertEqual(out, 'see <a href="x"><strong>piano</strong></a> <strong>here</strong>')

    def test_skips_snippets_already_highlighted(self):
        # When the engine already wrapped a term, leave the snippet alone rather
        # than wrapping a second time.
        text = 'Learn <strong>piano</strong> today'
        self.assertEqual(_highlight(text, 'piano today'), text)

    def test_short_terms_are_skipped(self):
        self.assertEqual(_highlight('I a m here', 'I a m'), 'I a m here')

    def test_blank_inputs_unchanged(self):
        self.assertEqual(_highlight('', 'piano'), '')
        self.assertEqual(_highlight('text', ''), 'text')

    def test_renders_through_snippet_filter(self):
        # End to end: highlighting + the template filter yield bold terms and a
        # decoded apostrophe, never literal markup.
        from django.template import Context, Template
        desc = _highlight('Le piano n&#x27;est pas dur', 'piano')
        tpl = Template('{% load result_extras %}{{ value|snippet }}')
        out = tpl.render(Context({'value': desc}))
        self.assertEqual(out, 'Le <strong>piano</strong> n&#x27;est pas dur')
        self.assertNotIn('&amp;#x27;', out)


class FetchWebHighlightTests(TestCase):
    @override_settings(BRAVE_API_KEY='', MOJEEK_API_KEY='mk', MARGINALIA_API_KEY='public')
    @patch('web.services._marginalia_request')
    @patch('web.services._mojeek_request')
    def test_mojeek_and_marginalia_get_highlighted(self, mock_mojeek, mock_marginalia):
        mock_mojeek.return_value = {'response': {'results': [
            {'url': 'https://m.example/1', 'title': 'M', 'desc': 'Learn piano fast'},
        ]}}
        mock_marginalia.return_value = {'results': [
            {'url': 'https://x.example/1', 'title': 'X', 'description': 'A piano guide'},
        ]}
        results, _ = fetch_web('piano', 'all', page=1)
        by_url = {r['url']: r['description'] for r in results}
        self.assertEqual(by_url['https://m.example/1'], 'Learn <strong>piano</strong> fast')
        self.assertEqual(by_url['https://x.example/1'], 'A <strong>piano</strong> guide')

    @override_settings(BRAVE_API_KEY='bk', MOJEEK_API_KEY='', MARGINALIA_API_KEY='')
    @patch('web.services._brave_request')
    def test_brave_snippet_left_untouched(self, mock_brave):
        # Brave already wraps the term; we must not add a second layer.
        mock_brave.return_value = {'web': {'results': [
            {'url': 'https://b.example/1', 'title': 'B',
             'description': 'Learn <strong>piano</strong> now',
             'meta_url': {'netloc': 'b.example', 'path': ''}, 'profile': {}},
        ]}}
        results, _ = fetch_web('piano', 'brave', page=1)
        self.assertEqual(results[0]['description'], 'Learn <strong>piano</strong> now')

    @override_settings(BRAVE_API_KEY='bk', MOJEEK_API_KEY='', MARGINALIA_API_KEY='')
    @patch('web.services._brave_request')
    def test_brave_snippet_without_markup_gets_highlighted(self, mock_brave):
        # Brave omits <strong> for some queries (e.g. navigational "facebook"),
        # so we fill it in, preserving the snippet's original casing.
        mock_brave.return_value = {'web': {'results': [
            {'url': 'https://facebook.com/', 'title': 'Facebook',
             'description': 'Log in to Facebook to connect with friends',
             'meta_url': {'netloc': 'facebook.com', 'path': ''}, 'profile': {}},
        ]}}
        results, _ = fetch_web('facebook', 'brave', page=1)
        self.assertEqual(
            results[0]['description'],
            'Log in to <strong>Facebook</strong> to connect with friends',
        )


class StaanRequestTests(TestCase):
    """The Staan HTTP client: endpoint, bearer auth, the key gate and failures."""

    @override_settings(STAAN_API_KEY='sk')
    def test_key_sent_as_bearer_header(self):
        cm, client = _fake_httpx_client([{'web': {'results': []}}])
        with patch('httpx.Client', return_value=cm):
            data = _staan_request({'q': 'piano'})
        self.assertEqual(data, {'web': {'results': []}})
        self.assertEqual(client.get.call_args[0][0], 'https://api.staan.ai/v2/search/web')
        headers = client.get.call_args.kwargs['headers']
        self.assertEqual(headers['Authorization'], 'Bearer sk')
        # The key must never travel in the query string (it would be logged).
        self.assertNotIn('key', client.get.call_args.kwargs['params'])

    @override_settings(STAAN_API_KEY='')
    def test_no_key_returns_none_without_request(self):
        with patch('httpx.Client') as mock_client:
            self.assertIsNone(_staan_request({'q': 'piano'}))
        mock_client.assert_not_called()

    @override_settings(STAAN_API_KEY='sk')
    def test_upstream_error_returns_none_and_records_down(self):
        with patch('httpx.Client', side_effect=RuntimeError('boom')), \
                patch('search.clients.health.record_down') as mock_down:
            self.assertIsNone(_staan_request({'q': 'piano'}))
        self.assertEqual(mock_down.call_args[0][0], 'staan')


class FetchStaanWebTests(TestCase):
    """Staan's slice of the web results: request shape, normalisation, and the
    key gate that keeps it out of a search entirely when unconfigured."""

    @override_settings(BRAVE_API_KEY='', MOJEEK_API_KEY='', MARGINALIA_API_KEY='',
                       STAAN_API_KEY='sk')
    @patch('web.services._staan_request')
    def test_results_normalised_and_highlighted(self, mock_staan):
        mock_staan.return_value = {'web': {'results': [
            {'url': 'https://s.example/1', 'title': 'S', 'snippet': 'Learn piano fast'},
        ]}}
        results, correction = fetch_web('piano', 'staan', page=1)
        self.assertEqual(correction, '')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['source'], 'staan')
        self.assertEqual(results[0]['description'], 'Learn <strong>piano</strong> fast')

    @override_settings(BRAVE_API_KEY='', MOJEEK_API_KEY='', MARGINALIA_API_KEY='',
                       STAAN_API_KEY='sk')
    @patch('web.services._staan_request')
    def test_pages_by_offset_and_maps_the_language_to_a_market(self, mock_staan):
        mock_staan.return_value = {'web': {'results': []}}
        fetch_web('piano', 'staan', page=3, safe_search='off', lang='fr')
        params = mock_staan.call_args[0][0]
        self.assertEqual(params['q'], 'piano')
        self.assertEqual(params['offset'], 20)  # ten results a page
        self.assertEqual(params['market'], 'fr-FR')

    @override_settings(BRAVE_API_KEY='', MOJEEK_API_KEY='', MARGINALIA_API_KEY='',
                       STAAN_API_KEY='sk')
    @patch('web.services._staan_request')
    def test_market_omitted_for_a_language_staan_does_not_serve(self, mock_staan):
        mock_staan.return_value = {'web': {'results': []}}
        fetch_web('piano', 'staan', page=1, lang='nl')
        self.assertNotIn('market', mock_staan.call_args[0][0])

    @override_settings(BRAVE_API_KEY='', MOJEEK_API_KEY='', MARGINALIA_API_KEY='',
                       STAAN_API_KEY='sk')
    @patch('web.services._staan_request')
    def test_sits_out_pages_past_the_offset_cap(self, mock_staan):
        # Staan rejects an offset past 30, so it can only serve pages 1-4.
        mock_staan.return_value = {'web': {'results': []}}
        fetch_web('piano', 'staan', page=4)
        self.assertEqual(mock_staan.call_args[0][0]['offset'], 30)
        mock_staan.reset_mock()
        results, _ = fetch_web('piano', 'staan', page=5)
        self.assertEqual(results, [])
        mock_staan.assert_not_called()

    @override_settings(BRAVE_API_KEY='', MOJEEK_API_KEY='', MARGINALIA_API_KEY='',
                       STAAN_API_KEY='sk')
    @patch('web.services._staan_request')
    def test_sits_out_an_over_long_query(self, mock_staan):
        # ``q`` is capped at 400 characters; a longer one is never sent.
        results, _ = fetch_web('x' * 401, 'staan', page=1)
        self.assertEqual(results, [])
        mock_staan.assert_not_called()

    @override_settings(BRAVE_API_KEY='', MOJEEK_API_KEY='', MARGINALIA_API_KEY='',
                       STAAN_API_KEY='')
    @patch('web.services._staan_request')
    def test_no_key_skips_the_engine(self, mock_staan):
        results, _ = fetch_web('piano', 'staan', page=1)
        self.assertEqual(results, [])
        mock_staan.assert_not_called()

    @override_settings(BRAVE_API_KEY='', MOJEEK_API_KEY='mk', MARGINALIA_API_KEY='',
                       STAAN_API_KEY='sk')
    @patch('web.services._staan_request')
    @patch('web.services._mojeek_request')
    def test_blended_with_another_engine(self, mock_mojeek, mock_staan):
        mock_mojeek.return_value = {'response': {'results': [
            {'url': 'https://shared.example/', 'title': 'M', 'desc': ''},
        ]}}
        mock_staan.return_value = {'web': {'results': [
            {'url': 'https://shared.example/', 'title': 'S', 'snippet': ''},
            {'url': 'https://s.example/2', 'title': 'S2', 'snippet': ''},
        ]}}
        results, _ = fetch_web('piano', 'all', page=1)
        by_url = {r['url']: r for r in results}
        self.assertEqual(by_url['https://shared.example/']['source'], 'both')
        self.assertEqual(by_url['https://shared.example/']['source_label'], 'Mojeek · Staan')
        self.assertEqual(by_url['https://s.example/2']['source'], 'staan')


class FetchSuggestionsTests(TestCase):
    @override_settings(BRAVE_SUGGEST_API_KEY='')
    @patch('web.services._brave_request')
    def test_no_key_returns_empty_without_request(self, mock_brave):
        self.assertEqual(fetch_suggestions('q'), [])
        mock_brave.assert_not_called()

    @override_settings(BRAVE_SUGGEST_API_KEY='suggest-key')
    @patch('web.services._brave_request')
    def test_returns_queries_from_results(self, mock_brave):
        mock_brave.return_value = {'results': [
            {'query': 'python tutorial', 'is_entity': False},
            {'query': 'python download', 'is_entity': False},
        ]}
        self.assertEqual(fetch_suggestions('python'), ['python tutorial', 'python download'])
        mock_brave.assert_called_once_with('/suggest/search', {'q': 'python'}, api_key='suggest-key')

    @override_settings(BRAVE_SUGGEST_API_KEY='suggest-key')
    @patch('web.services._brave_request')
    def test_caps_at_seven(self, mock_brave):
        mock_brave.return_value = {'results': [{'query': f'q{i}'} for i in range(10)]}
        self.assertEqual(len(fetch_suggestions('q')), 7)

    @override_settings(BRAVE_SUGGEST_API_KEY='suggest-key')
    @patch('web.services._brave_request')
    def test_skips_entries_without_query(self, mock_brave):
        mock_brave.return_value = {'results': [{'query': 'a'}, {'is_entity': True}, {'query': ''}]}
        self.assertEqual(fetch_suggestions('q'), ['a'])

    @override_settings(BRAVE_SUGGEST_API_KEY='suggest-key')
    @patch('web.services._brave_request')
    def test_request_error_returns_empty(self, mock_brave):
        mock_brave.return_value = None
        self.assertEqual(fetch_suggestions('q'), [])

    @override_settings(BRAVE_SUGGEST_API_KEY='suggest-key')
    @patch('web.services._brave_request')
    def test_unexpected_shape_returns_empty(self, mock_brave):
        mock_brave.return_value = {'results': 'not-a-list'}
        self.assertEqual(fetch_suggestions('q'), [])

    @override_settings(BRAVE_SUGGEST_API_KEY='suggest-key')
    @patch('web.services._brave_request')
    def test_repeat_query_uses_cache_not_api(self, mock_brave):
        mock_brave.return_value = {'results': [{'query': 'cached suggestion'}]}
        first = fetch_suggestions('repeatme')
        second = fetch_suggestions('repeatme')
        self.assertEqual(first, ['cached suggestion'])
        self.assertEqual(second, ['cached suggestion'])
        mock_brave.assert_called_once()


class FetchImagesTests(TestCase):
    @override_settings(BRAVE_API_KEY='', PIXABAY_API_KEY='')
    def test_no_key_returns_empty(self):
        self.assertEqual(fetch_images('q', 'all'), [])

    @override_settings(BRAVE_API_KEY='test-key', PIXABAY_API_KEY='')
    def test_non_brave_engine_returns_empty(self):
        self.assertEqual(fetch_images('q', 'mojeek'), [])

    @override_settings(BRAVE_API_KEY='test-key')
    @patch('images.services._brave_request')
    def test_pagination_slices_locally(self, mock_brave):
        # The image endpoint has no `offset`, so each page is sliced locally.
        mock_brave.return_value = {'results': [
            {'title': f't{i}', 'url': f'u{i}', 'source': 's', 'thumbnail': {'src': f'x{i}'}}
            for i in range(25)
        ]}
        page1 = fetch_images('q', 'all', page=1, safe_search='off')
        page2 = fetch_images('q', 'all', page=2, safe_search='off')
        self.assertEqual(len(page1), 10)
        self.assertEqual(len(page2), 10)
        self.assertEqual(page1[0]['title'], 't0')
        self.assertEqual(page2[0]['title'], 't10')

    @override_settings(BRAVE_API_KEY='', PIXABAY_API_KEY='pix-key')
    @patch('images.services._pixabay_request')
    def test_pixabay_fallback_when_no_brave_key(self, mock_pixabay):
        mock_pixabay.return_value = {'hits': [
            {'tags': 'cat, kitten', 'pageURL': 'https://pixabay.com/p/1/',
             'webformatURL': 'https://cdn.pixabay.com/cat_640.jpg',
             'previewURL': 'https://cdn.pixabay.com/cat_150.jpg'},
        ]}
        results = fetch_images('cats', 'all')
        self.assertEqual(len(results), 1)
        img = results[0]
        self.assertEqual(img['url'], 'https://pixabay.com/p/1/')
        self.assertEqual(img['title'], 'cat, kitten')
        self.assertEqual(img['source'], 'Pixabay')
        self.assertEqual(img['thumbnail']['src'], 'https://cdn.pixabay.com/cat_640.jpg')

    @override_settings(BRAVE_API_KEY='test-key', PIXABAY_API_KEY='pix-key')
    @patch('images.services._pixabay_request')
    def test_pixabay_backs_mojeek_engine(self, mock_pixabay):
        # Mojeek/Marginalia have no image search, so Pixabay serves the tab.
        mock_pixabay.return_value = {'hits': [
            {'tags': 'dog', 'pageURL': 'https://pixabay.com/p/2/',
             'webformatURL': 'https://cdn.pixabay.com/dog_640.jpg'},
        ]}
        results = fetch_images('dogs', 'mojeek')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['source'], 'Pixabay')

    @override_settings(BRAVE_API_KEY='test-key', PIXABAY_API_KEY='pix-key')
    @patch('images.services._pixabay_request')
    @patch('images.services._brave_request')
    def test_brave_and_pixabay_blended(self, mock_brave, mock_pixabay):
        # With both keys set, the two providers are interleaved round-robin.
        mock_brave.return_value = {'results': [
            {'title': f'b{i}', 'url': f'https://ex.com/{i}', 'source': 'example.com',
             'thumbnail': {'src': f'https://imgs.brave.com/{i}.jpg'}}
            for i in range(2)
        ]}
        mock_pixabay.return_value = {'hits': [
            {'tags': f'p{i}', 'pageURL': f'https://pixabay.com/p/{i}/',
             'webformatURL': f'https://cdn.pixabay.com/{i}.jpg'}
            for i in range(2)
        ]}
        results = fetch_images('cats', 'all')
        mock_pixabay.assert_called_once()  # Pixabay is queried, not skipped
        self.assertEqual(len(results), 4)
        self.assertEqual([r['source'] for r in results],
                         ['example.com', 'Pixabay', 'example.com', 'Pixabay'])

    @override_settings(BRAVE_API_KEY='test-key', PIXABAY_API_KEY='pix-key')
    @patch('images.services._pixabay_request')
    @patch('images.services._brave_request')
    def test_pixabay_fallback_when_brave_empty(self, mock_brave, mock_pixabay):
        mock_brave.return_value = {'results': []}
        mock_pixabay.return_value = {'hits': [
            {'tags': 'sea', 'pageURL': 'https://pixabay.com/p/3/',
             'webformatURL': 'https://cdn.pixabay.com/sea_640.jpg'},
        ]}
        results = fetch_images('sea', 'all')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['source'], 'Pixabay')


class CleanCaptionTests(TestCase):
    """Reducing an image caption to a search-friendly subject phrase."""

    def test_strips_trailing_source_after_dash(self):
        self.assertEqual(
            _clean_caption('Golden Retriever - American Kennel Club'),
            'Golden Retriever',
        )

    def test_strips_trailing_site_after_pipe(self):
        self.assertEqual(
            _clean_caption('Eiffel Tower at night | Britannica'),
            'Eiffel Tower at night',
        )

    def test_keeps_hyphenated_word(self):
        # No spaces around the hyphen, so it isn't a source separator.
        self.assertEqual(_clean_caption('Spider-Man poster'), 'Spider-Man poster')

    def test_only_strips_final_segment(self):
        self.assertEqual(
            _clean_caption('New York - Brooklyn - Bridge'),
            'New York - Brooklyn',
        )

    def test_keeps_long_tail(self):
        # A long trailing segment is content, not a site name, so it stays.
        title = 'Cats - ' + 'a' * 41
        self.assertEqual(_clean_caption(title), title)

    def test_collapses_whitespace(self):
        self.assertEqual(_clean_caption('  cats   and   dogs  '), 'cats and dogs')

    def test_empty(self):
        self.assertEqual(_clean_caption(''), '')
        self.assertEqual(_clean_caption(None), '')


class FetchSimilarImagesTests(TestCase):
    """The lightbox / detail 'similar images' search, seeded by the image caption."""

    @patch('images.services.fetch_images')
    def test_seeds_search_with_cleaned_caption(self, mock_fetch):
        mock_fetch.return_value = [
            {'title': 'a', 'url': 'https://a', 'source': 's', 'thumbnail': {'src': 'ta'}},
        ]
        seed, results = fetch_similar_images('Golden Retriever - AKC', engine='all')
        self.assertEqual(seed, 'Golden Retriever')
        self.assertEqual(len(results), 1)
        # Searched for the cleaned caption (not the raw title), page 1.
        self.assertEqual(mock_fetch.call_args.args[0], 'Golden Retriever')

    @patch('images.services.fetch_images')
    def test_excludes_the_opened_image(self, mock_fetch):
        mock_fetch.return_value = [
            {'title': 'same', 'url': 'https://same', 'source': 's', 'thumbnail': {'src': 't1'}},
            {'title': 'other', 'url': 'https://other', 'source': 's', 'thumbnail': {'src': 't2'}},
        ]
        _, results = fetch_similar_images('cats', engine='all', exclude_url='https://same')
        self.assertEqual([r['url'] for r in results], ['https://other'])

    @patch('images.services.fetch_images')
    def test_falls_back_to_query_when_no_caption(self, mock_fetch):
        mock_fetch.return_value = []
        seed, _ = fetch_similar_images('', query='dogs', engine='all')
        self.assertEqual(seed, 'dogs')
        self.assertEqual(mock_fetch.call_args.args[0], 'dogs')

    @patch('images.services.fetch_images')
    def test_no_seed_skips_search(self, mock_fetch):
        self.assertEqual(fetch_similar_images('', query='', engine='all'), ('', []))
        mock_fetch.assert_not_called()  # nothing to search for, no upstream call


class ImageSimilarEndpointTests(TestCase):
    """The Images-tab lightbox pulls real similar images (JSON) from here."""

    def setUp(self):
        self.user = User.objects.create_user('isa', password='pass')
        self.client.login(username='isa', password='pass')

    @patch('search.views.fetch_similar_images')
    def test_returns_images_and_seed(self, mock_sim):
        mock_sim.return_value = ('Golden Retriever', [
            {'title': 'pup', 'url': 'https://ex.com/p', 'source': 'ex.com',
             'thumbnail': {'src': 'https://ex.com/p.jpg'}},
        ])
        resp = self.client.get(reverse('search:image_similar') + '?q=Golden Retriever - AKC')
        data = resp.json()
        self.assertEqual(data['query'], 'Golden Retriever')
        self.assertEqual(len(data['images']), 1)
        self.assertEqual(data['images'][0]['thumb'], 'https://ex.com/p.jpg')
        self.assertEqual(data['images'][0]['url'], 'https://ex.com/p')

    @patch('search.views.fetch_similar_images', return_value=('', []))
    def test_empty_when_no_seed(self, mock_sim):
        resp = self.client.get(reverse('search:image_similar'))
        self.assertEqual(resp.json(), {'query': '', 'images': []})

    def test_login_required(self):
        self.client.logout()
        resp = self.client.get(reverse('search:image_similar') + '?q=cats')
        self.assertEqual(resp.status_code, 302)


class ImageDetailPageTests(TestCase):
    """No-JS fallback page for the image lightbox (the 'similar images' feature)."""

    def setUp(self):
        self.user = User.objects.create_user('ida', password='pass')
        self.client.login(username='ida', password='pass')

    @patch('search.views.fetch_similar_images')
    def test_renders_image_and_similar_grid(self, mock_sim):
        mock_sim.return_value = ('dogs', [
            {'title': 'pup', 'url': 'https://ex.com/p', 'source': 'ex.com',
             'thumbnail': {'src': 'https://ex.com/p.jpg'}},
        ])
        resp = self.client.get(reverse('search:image_detail'), {
            'q': 'Golden Retriever - AKC', 'query': 'dogs',
            'img': 'https://ex.com/dog.jpg', 'src': 'https://ex.com/dog',
            'source': 'ex.com',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'https://ex.com/dog.jpg')   # opened image + "Open image"
        self.assertContains(resp, 'https://ex.com/dog"')      # "Visit page" link
        self.assertContains(resp, 'https://ex.com/p.jpg')     # a real similar-image thumbnail
        self.assertContains(resp, 'See all results')          # link to the full results
        self.assertContains(resp, 'tab=images')

    @patch('search.views.fetch_similar_images', return_value=('', []))
    def test_rejects_non_http_urls(self, mock_sim):
        # Params are caller-supplied; a javascript: scheme must never reach an href.
        resp = self.client.get(reverse('search:image_detail'), {
            'q': 'x', 'img': 'javascript:alert(1)', 'src': 'javascript:alert(2)',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'javascript:alert')

    @patch('search.views.fetch_similar_images', return_value=('', []))
    def test_empty_params_still_render(self, mock_sim):
        resp = self.client.get(reverse('search:image_detail'))
        self.assertEqual(resp.status_code, 200)

    def test_login_required(self):
        self.client.logout()
        resp = self.client.get(reverse('search:image_detail'))
        self.assertEqual(resp.status_code, 302)


class SimilarImagesOptOutTests(TestCase):
    """The 'similar images' feature is opt-out per user (Settings -> General);
    when on, every lookup counts as one search; the gallery also exposes direct
    open-page / open-image links so the lightbox can be bypassed entirely."""

    def setUp(self):
        self.user = User.objects.create_user('sim', password='pass')
        self.client.login(username='sim', password='pass')

    def _count(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'account'}))
        return resp.context['search_count']

    # --- preference plumbing ------------------------------------------------
    def test_enabled_by_default(self):
        self.assertTrue(preferences.defaults()['similar_images'])
        self.assertTrue(preferences.coerce({})['similar_images'])

    def test_coerce_roundtrip(self):
        self.assertFalse(preferences.coerce({'similar_images': False})['similar_images'])
        self.assertTrue(preferences.coerce({'similar_images': True})['similar_images'])

    def test_settings_toggle_off_then_on(self):
        # Unchecked checkbox → field absent → off; checked → 'on' → on.
        self.client.post(reverse('search:settings'),
                         {'setting': 'similar_images', 'pane': 'general'})
        self.assertFalse(_get_prefs(self.client)['similar_images'])
        self.client.post(reverse('search:settings'),
                         {'setting': 'similar_images', 'pane': 'general', 'similar_images': 'on'})
        self.assertTrue(_get_prefs(self.client)['similar_images'])

    def test_settings_general_pane_has_toggle(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'general'}))
        self.assertTrue(resp.context['similar_images'])
        self.assertContains(resp, 'name="similar_images"')

    # --- counting -----------------------------------------------------------
    @patch('search.views.fetch_similar_images')
    def test_lookup_counts_one_search(self, mock_sim):
        mock_sim.return_value = ('dogs', [
            {'title': 'pup', 'url': 'https://ex.com/p', 'source': 'ex.com',
             'thumbnail': {'src': 'https://ex.com/p.jpg'}},
        ])
        self.client.get(reverse('search:image_similar') + '?q=dogs')
        self.assertEqual(self._count(), 1)

    @patch('search.views.fetch_similar_images', return_value=('', []))
    def test_no_count_without_usable_seed(self, mock_sim):
        # No seed → no upstream search ran, so nothing is counted.
        self.client.get(reverse('search:image_similar'))
        self.assertEqual(self._count(), 0)

    # --- opt-out behaviour --------------------------------------------------
    @patch('search.views.fetch_similar_images')
    def test_endpoint_empty_and_no_search_when_opted_out(self, mock_sim):
        _set_prefs(self.client, similar_images=False)
        resp = self.client.get(reverse('search:image_similar') + '?q=dogs')
        self.assertEqual(resp.json(), {'query': '', 'images': []})
        mock_sim.assert_not_called()  # no upstream call
        self.assertEqual(self._count(), 0)  # and nothing counted

    @patch('search.views.fetch_similar_images')
    def test_detail_page_omits_grid_when_opted_out(self, mock_sim):
        _set_prefs(self.client, similar_images=False)
        resp = self.client.get(reverse('search:image_detail'), {
            'q': 'dogs', 'img': 'https://ex.com/dog.jpg', 'src': 'https://ex.com/dog',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'https://ex.com/dog.jpg')  # opened image still shown
        self.assertNotContains(resp, 'No similar images found.')  # grid section gone
        mock_sim.assert_not_called()

    @patch('search.views.fetch_images')
    def test_results_grid_hidden_when_opted_out(self, mock_images):
        mock_images.return_value = [
            {'title': 'A cat', 'url': 'https://ex.com/cat', 'source': 'ex.com',
             'thumbnail': {'src': 'https://ex.com/cat.jpg'}},
        ]
        _set_prefs(self.client, similar_images=False)
        resp = self.client.get(reverse('search:results') + '?q=cats&tab=images')
        # Lightbox still ships (image + visit/open links) but flagged disabled,
        self.assertContains(resp, 'id="img-lightbox"')
        self.assertContains(resp, 'data-similar-enabled="0"')
        # and the similar grid + its skeleton/templates are dropped.
        self.assertNotContains(resp, 'id="img-lightbox-similar"')
        self.assertNotContains(resp, 'id="img-skeleton-tpl"')

    # --- direct gallery actions (task 3) ------------------------------------
    @patch('search.views.fetch_images')
    def test_gallery_exposes_direct_open_links(self, mock_images):
        mock_images.return_value = [
            {'title': 'A cat', 'url': 'https://ex.com/cat', 'source': 'ex.com',
             'thumbnail': {'src': 'https://ex.com/cat.jpg'}},
        ]
        resp = self.client.get(reverse('search:results') + '?q=cats&tab=images')
        # Open the image itself and the source page straight from the grid,
        # regardless of the similar-images setting.
        self.assertContains(resp, 'aria-label="Open image"')
        self.assertContains(resp, 'aria-label="Visit page"')
        self.assertContains(resp, 'href="https://ex.com/cat.jpg"')  # open-image target


class PaginationOffsetTests(TestCase):
    """Brave `offset` is a 0-based page index (max 9), not a result count."""

    @override_settings(BRAVE_API_KEY='test-key')
    @patch('news.services._brave_request')
    def test_news_offset_is_page_index(self, mock_brave):
        mock_brave.return_value = {'results': []}
        fetch_news('q', 'all', page=3, safe_search='off')
        self.assertEqual(mock_brave.call_args[0][1]['offset'], 2)

    @override_settings(BRAVE_API_KEY='', SEPIA_API_KEY='')
    @patch('videos.services._sepia_request')
    def test_no_video_provider_returns_empty(self, mock_sepia):
        mock_sepia.return_value = None
        self.assertEqual(fetch_videos('q', 'all'), [])

    @override_settings(BRAVE_API_KEY='test-key', SEPIA_API_KEY='')
    @patch('videos.services._sepia_request')
    @patch('videos.services._brave_request')
    def test_sepia_disabled_returns_brave_only(self, mock_brave, mock_sepia):
        # When Sepia is disabled via sepia_enabled=False, only Brave is queried.
        mock_brave.return_value = {'results': [{'title': 'Brave video'}]}
        results = fetch_videos('q', 'all', sepia_enabled=False)
        mock_sepia.assert_not_called()
        mock_brave.assert_called_once()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], 'Brave video')

    @override_settings(SEPIA_API_KEY='')
    def test_sepia_enabled_but_no_key_still_works(self):
        # Sepia doesn't require an API key, so sepia_enabled=True should still query it.
        # This test verifies the parameter is accepted even without a key.
        with patch('videos.services._sepia_request') as mock_sepia:
            mock_sepia.return_value = {'data': []}
            fetch_videos('q', 'all', sepia_enabled=True)
            mock_sepia.assert_called_once()

    @override_settings(SEPIA_API_KEY='')
    @patch('videos.services._sepia_request')
    def test_sepia_fallback_for_mojeek(self, mock_sepia):
        # Mojeek has no video search, so Sepia serves as the sole provider.
        mock_sepia.return_value = {'data': [
            {'name': 'Test Video', 'url': 'https://sepiasearch.org/videos/test',
             'thumbnailUrl': 'https://sepiasearch.org/static/thumbnails/test.jpg',
             'duration': 125, 'publishedAt': '2024-01-15T10:00:00Z'}
        ]}
        results = fetch_videos('q', 'mojeek', sepia_enabled=True)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], 'Test Video')
        self.assertEqual(results[0]['source'], 'PeerTube')
        self.assertEqual(results[0]['video']['duration'], '2:05')

    @override_settings(BRAVE_API_KEY='test-key', SEPIA_API_KEY='')
    @patch('videos.services._sepia_request')
    @patch('videos.services._brave_request')
    def test_brave_and_sepia_blended(self, mock_brave, mock_sepia):
        # With both providers enabled, results are concatenated (Sepia first, then Brave).
        mock_sepia.return_value = {'data': [
            {'name': 'Sepia video 1', 'url': 'https://sepiasearch.org/videos/1',
             'thumbnailUrl': 'https://sepiasearch.org/static/1.jpg',
             'duration': 60, 'publishedAt': '2024-01-01T00:00:00Z'}
        ]}
        mock_brave.return_value = {'results': [
            {'title': 'Brave video 1', 'url': 'https://example.com/1',
             'thumbnail': {'src': 'https://imgs.brave.com/1.jpg'}}
        ]}
        results = fetch_videos('q', 'all', sepia_enabled=True)
        mock_sepia.assert_called_once()
        mock_brave.assert_called_once()
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['source'], 'PeerTube')
        self.assertEqual(results[0]['title'], 'Sepia video 1')
        self.assertNotIn('source', results[1])  # Brave results don't have source badge

    @override_settings(BRAVE_API_KEY='test-key', SEPIA_API_KEY='')
    @patch('videos.services._sepia_request')
    @patch('videos.services._brave_request')
    def test_brave_video_normalized_like_sepia(self, mock_brave, mock_sepia):
        # Brave's raw video result is normalized onto the same shape as Sepia's,
        # so both render identically (hostname, duration, thumbnail, age).
        mock_sepia.return_value = {'data': []}
        mock_brave.return_value = {'results': [
            {'title': 'Brave video 1', 'url': 'https://youtube.com/watch?v=1',
             'age': 'April 22, 2024',
             'thumbnail': {'src': 'https://imgs.brave.com/1.jpg'},
             'meta_url': {'hostname': 'youtube.com'},
             'video': {'duration': '4:32'}}
        ]}
        results = fetch_videos('q', 'all', sepia_enabled=True)
        self.assertEqual(len(results), 1)
        video = results[0]
        self.assertEqual(video['title'], 'Brave video 1')
        self.assertEqual(video['meta_url']['hostname'], 'youtube.com')
        self.assertEqual(video['thumbnail']['src'], 'https://imgs.brave.com/1.jpg')
        self.assertEqual(video['video']['duration'], '4:32')
        self.assertEqual(video['age'], 'April 22, 2024')

    @override_settings(SEPIA_API_KEY='')
    @patch('videos.services._sepia_request')
    def test_sepia_duration_formatting(self, mock_sepia):
        mock_sepia.return_value = {'data': [
            {'name': 'Short', 'url': 'https://sepiasearch.org/videos/s',
             'thumbnailUrl': 'https://sepiasearch.org/static/s.jpg',
             'duration': 45, 'publishedAt': '2024-01-01T00:00:00Z'},
            {'name': 'Medium', 'url': 'https://sepiasearch.org/videos/m',
             'thumbnailUrl': 'https://sepiasearch.org/static/m.jpg',
             'duration': 3661, 'publishedAt': '2024-01-01T00:00:00Z'},
        ]}
        results = fetch_videos('q', 'all', sepia_enabled=True)
        self.assertEqual(results[0]['video']['duration'], '0:45')
        self.assertEqual(results[1]['video']['duration'], '1:01:01')

    @override_settings(SEPIA_API_KEY='')
    @patch('videos.services._sepia_request')
    def test_sepia_age_formatting(self, mock_sepia):
        # Sepia's publishedAt is formatted as a full date ("April 22, 2024"),
        # matching the format Brave's video age field uses.
        from datetime import datetime, timedelta
        published = datetime.now(UTC) - timedelta(days=45)
        mock_sepia.return_value = {'data': [
            {'name': 'Old video', 'url': 'https://sepiasearch.org/videos/o',
             'thumbnailUrl': 'https://sepiasearch.org/static/o.jpg',
             'duration': 100, 'publishedAt': published.isoformat()}
        ]}
        results = fetch_videos('q', 'all', sepia_enabled=True)
        self.assertEqual(results[0]['age'], published.strftime('%B %d, %Y'))

    @override_settings(SEPIA_API_KEY='')
    @patch('videos.services._sepia_request')
    def test_sepia_safesearch_mapping(self, mock_sepia):
        mock_sepia.return_value = {'data': []}
        fetch_videos('q', 'all', safe_search='on', sepia_enabled=True)
        self.assertEqual(mock_sepia.call_args[0][0]['nsfw'], 'false')
        
        fetch_videos('q', 'all', safe_search='off', sepia_enabled=True)
        self.assertEqual(mock_sepia.call_args[0][0]['nsfw'], 'both')

    @override_settings(SEPIA_API_KEY='')
    @patch('videos.services._sepia_request')
    def test_sepia_language_filtering(self, mock_sepia):
        mock_sepia.return_value = {'data': []}
        fetch_videos('q', 'all', lang='fr', sepia_enabled=True)
        self.assertEqual(mock_sepia.call_args[0][0]['languageOneOf[]'], 'fr')

    @override_settings(BRAVE_API_KEY='test-key')
    @patch('videos.services._brave_request')
    def test_brave_video_date_filter_sets_freshness(self, mock_brave):
        # The time-range filter maps onto Brave's `freshness` code (w → pw).
        mock_brave.return_value = {'results': []}
        fetch_videos('q', 'brave', date='w', sepia_enabled=False)
        self.assertEqual(mock_brave.call_args[0][1]['freshness'], 'pw')

    @override_settings(BRAVE_API_KEY='test-key')
    @patch('videos.services._brave_request')
    def test_brave_video_no_date_omits_freshness(self, mock_brave):
        mock_brave.return_value = {'results': []}
        fetch_videos('q', 'brave', sepia_enabled=False)
        self.assertNotIn('freshness', mock_brave.call_args[0][1])

    @override_settings(SEPIA_API_KEY='')
    @patch('videos.services._sepia_request')
    def test_sepia_date_filter_sets_start_date(self, mock_sepia):
        # PeerTube has no freshness codes, so the chosen window becomes a
        # "published after" startDate instead.
        mock_sepia.return_value = {'data': []}
        fetch_videos('q', 'mojeek', date='m', sepia_enabled=True)
        self.assertIn('startDate', mock_sepia.call_args[0][0])

    @override_settings(SEPIA_API_KEY='')
    @patch('videos.services._sepia_request')
    def test_sepia_no_date_omits_start_date(self, mock_sepia):
        mock_sepia.return_value = {'data': []}
        fetch_videos('q', 'mojeek', sepia_enabled=True)
        self.assertNotIn('startDate', mock_sepia.call_args[0][0])


class WorldNewsBlendTests(TestCase):
    """News blends Brave with the World News API, mirroring how Images blend
    Brave with Pixabay and Videos with Sepia."""

    @override_settings(BRAVE_API_KEY='', WORLDNEWS_API_KEY='')
    @patch('news.services._worldnews_request')
    def test_no_news_provider_returns_empty(self, mock_wn):
        mock_wn.return_value = None
        self.assertEqual(fetch_news('q', 'all'), [])
        mock_wn.assert_not_called()  # no key → never queried

    @override_settings(BRAVE_API_KEY='test-key', WORLDNEWS_API_KEY='wn-key')
    @patch('news.services._worldnews_request')
    @patch('news.services._brave_request')
    def test_worldnews_disabled_returns_brave_only(self, mock_brave, mock_wn):
        # worldnews_enabled=False → only Brave is queried.
        mock_brave.return_value = {'results': [{'title': 'Brave news'}]}
        results = fetch_news('ukraine', 'all', worldnews_enabled=False)
        mock_wn.assert_not_called()
        mock_brave.assert_called_once()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], 'Brave news')

    @override_settings(BRAVE_API_KEY='', WORLDNEWS_API_KEY='wn-key')
    @patch('news.services._worldnews_request')
    def test_worldnews_fallback_for_mojeek(self, mock_wn):
        # Mojeek has no news search, so World News serves as the sole provider,
        # normalised onto the Brave news shape the template expects.
        mock_wn.return_value = {'news': [
            {'title': 'WN article', 'url': 'https://www.news.example.com/a',
             'summary': 'A summary.', 'image': 'https://img.example.com/a.jpg',
             'publish_date': '2024-01-15 10:00:00'},
        ]}
        results = fetch_news('kyiv', 'mojeek')
        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertEqual(item['title'], 'WN article')
        self.assertEqual(item['url'], 'https://www.news.example.com/a')
        self.assertEqual(item['description'], 'A summary.')
        self.assertEqual(item['meta_url']['hostname'], 'news.example.com')  # 'www.' dropped
        self.assertEqual(item['thumbnail']['src'], 'https://img.example.com/a.jpg')

    @override_settings(BRAVE_API_KEY='', WORLDNEWS_API_KEY='wn-key')
    @patch('news.services._worldnews_request')
    def test_worldnews_description_falls_back_to_text(self, mock_wn):
        # When an article has no summary, the body text is used for the snippet.
        mock_wn.return_value = {'news': [
            {'title': 'No summary', 'url': 'https://e.example/x', 'text': 'Body text.'},
        ]}
        results = fetch_news('storm', 'mojeek')
        self.assertEqual(results[0]['description'], 'Body text.')

    @override_settings(BRAVE_API_KEY='test-key', WORLDNEWS_API_KEY='wn-key')
    @patch('news.services._worldnews_request')
    @patch('news.services._brave_request')
    def test_brave_and_worldnews_blended(self, mock_brave, mock_wn):
        # Both providers enabled → results interleave round-robin (Brave first).
        mock_brave.return_value = {'results': [
            {'title': 'Brave 1', 'url': 'https://brave.example/1'},
            {'title': 'Brave 2', 'url': 'https://brave.example/2'},
        ]}
        mock_wn.return_value = {'news': [
            {'title': 'WN 1', 'url': 'https://wn.example/1', 'publish_date': '2024-01-01 00:00:00'},
            {'title': 'WN 2', 'url': 'https://wn.example/2', 'publish_date': '2024-01-02 00:00:00'},
        ]}
        results = fetch_news('war', 'all')
        mock_brave.assert_called_once()
        mock_wn.assert_called_once()
        self.assertEqual([r['title'] for r in results], ['Brave 1', 'WN 1', 'Brave 2', 'WN 2'])

    @override_settings(BRAVE_API_KEY='test-key', WORLDNEWS_API_KEY='wn-key')
    @patch('news.services._worldnews_request')
    @patch('news.services._brave_request')
    def test_worldnews_error_does_not_break_brave(self, mock_brave, mock_wn):
        # A World News failure (None) still returns Brave's results.
        mock_brave.return_value = {'results': [{'title': 'Brave only'}]}
        mock_wn.return_value = None
        results = fetch_news('flood', 'all')
        self.assertEqual([r['title'] for r in results], ['Brave only'])

    @override_settings(WORLDNEWS_API_KEY='wn-key')
    @patch('news.services._worldnews_request')
    def test_worldnews_pagination_is_absolute_offset(self, mock_wn):
        # World News paginates by an absolute result offset, not a page index.
        mock_wn.return_value = {'news': []}
        fetch_news('q', 'mojeek', page=3)
        self.assertEqual(mock_wn.call_args[0][0]['offset'], 20)
        self.assertEqual(mock_wn.call_args[0][0]['number'], 10)

    @override_settings(WORLDNEWS_API_KEY='wn-key')
    @patch('news.services._worldnews_request')
    def test_worldnews_language_filtering(self, mock_wn):
        mock_wn.return_value = {'news': []}
        fetch_news('q', 'mojeek', lang='fr')
        self.assertEqual(mock_wn.call_args[0][0]['language'], 'fr')

    @override_settings(WORLDNEWS_API_KEY='wn-key')
    @patch('news.services._worldnews_request')
    def test_worldnews_date_filter_sets_earliest_publish_date(self, mock_wn):
        mock_wn.return_value = {'news': []}
        fetch_news('q', 'mojeek', date='w')
        self.assertIn('earliest-publish-date', mock_wn.call_args[0][0])


class LoginRequiredTests(TestCase):
    def test_index_redirects_anonymous(self):
        resp = self.client.get(reverse('search:index'))
        self.assertRedirects(resp, '/login/?next=/', fetch_redirect_response=False)

    def test_results_redirects_anonymous(self):
        resp = self.client.get(reverse('search:results'))
        self.assertRedirects(resp, '/login/?next=/search/', fetch_redirect_response=False)

    def test_settings_redirects_anonymous(self):
        resp = self.client.get(reverse('search:settings'))
        self.assertRedirects(resp, '/login/?next=/settings/', fetch_redirect_response=False)


class IndexViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pass')
        self.client.login(username='alice', password='pass')

    def test_index_ok(self):
        resp = self.client.get(reverse('search:index'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Seurch')


class FooterPagesTests(TestCase):
    """The informational pages linked from the site footer."""

    PAGES = ('about', 'api')

    def setUp(self):
        self.user = User.objects.create_user('carol', password='pass')
        self.client.login(username='carol', password='pass')

    # A distinctive snippet of each page's body, to prove the content block (not
    # just the shared header/footer) actually renders.
    BODY_SNIPPETS = {
        'about': 'metasearch engine',
        'api': 'OpenSearch',
    }

    def test_pages_render(self):
        for name in self.PAGES:
            with self.subTest(page=name):
                resp = self.client.get(reverse('search:' + name))
                self.assertEqual(resp.status_code, 200)
                self.assertContains(resp, 'Seurch')
                self.assertContains(resp, self.BODY_SNIPPETS[name])

    def test_pages_require_login(self):
        self.client.logout()
        for name in self.PAGES:
            with self.subTest(page=name):
                url = reverse('search:' + name)
                self.assertRedirects(
                    self.client.get(url), f'/login/?next={url}',
                    fetch_redirect_response=False,
                )

    def test_footer_links_are_wired(self):
        # The footer must not ship dead '#' placeholder links: every page it
        # points at resolves, and no nav href is a bare '#'.
        resp = self.client.get(reverse('search:index'))
        for name in self.PAGES:
            self.assertContains(resp, f'href="{reverse("search:" + name)}"')
        self.assertNotContains(resp, 'href="#"')

    def test_no_legal_links_by_default(self):
        # Privacy/terms/legal-notice content is jurisdiction-specific and not
        # bundled by default; self-hosters opt in via FOOTER_LINKS.
        resp = self.client.get(reverse('search:index'))
        self.assertNotContains(resp, 'status.seurch.eu')
        self.assertEqual(dict(resp.context['footer_links']), {})

    def test_api_page_documents_opensearch_endpoint(self):
        resp = self.client.get(reverse('search:api'))
        self.assertContains(resp, reverse('search:opensearch_xml'))

    @override_settings(FOOTER_LINKS={'Legal notice': 'https://example.com/legal'})
    def test_custom_footer_links_from_env(self):
        # Self-hosted operators add their own footer links (privacy policy,
        # terms, legal notice, …) via the FOOTER_LINKS env var.
        resp = self.client.get(reverse('search:index'))
        self.assertContains(resp, 'href="https://example.com/legal"')
        self.assertContains(resp, 'Legal notice')

    def test_source_link_is_wired(self):
        # The footer always links out to the source repository (SOURCE_URL).
        resp = self.client.get(reverse('search:index'))
        self.assertContains(resp, f'href="{settings.SOURCE_URL}"')
        self.assertContains(resp, 'Source')

    @override_settings(GIT_REF='main', GIT_SHA='abc1234')
    def test_version_shown_next_to_copyright(self):
        # The footer shows the build's git ref and short commit hash next to
        # the copyright, so a deployment can be traced back to its source.
        resp = self.client.get(reverse('search:index'))
        self.assertContains(resp, 'main@abc1234')

    @override_settings(GIT_REF='', GIT_SHA='')
    def test_no_version_shown_when_unresolved(self):
        # If neither could be resolved (e.g. no .git and no build args), the
        # footer degrades gracefully instead of showing an empty "()".
        resp = self.client.get(reverse('search:index'))
        self.assertNotContains(resp, 'text-slate-400 dark:text-slate-600')


class AboutPageContentTests(TestCase):
    """The About page documents instant answers and bangs (with a bang search)."""

    def setUp(self):
        self.user = User.objects.create_user('dave', password='pass')
        self.client.login(username='dave', password='pass')

    def test_lists_instant_answers_with_examples(self):
        resp = self.client.get(reverse('search:about'))
        self.assertContains(resp, 'Instant answers')
        # A spread of example queries from across the instant-answer handlers,
        # proving the showcase list actually rendered (not just the heading).
        for example in ('2+2', '5 km to miles', '0xff in decimal', 'md5 hello',
                        'json formatter', 'roll 2d6', 'qr code https://example.com'):
            with self.subTest(example=example):
                self.assertContains(resp, example)

    def test_lists_builtin_tab_bangs(self):
        resp = self.client.get(reverse('search:about'))
        self.assertContains(resp, 'Bangs')
        for bang in ('!web', '!images', '!news', '!videos', '!maps', '!translate'):
            with self.subTest(bang=bang):
                self.assertContains(resp, bang)

    def test_has_bang_search_widget_and_script(self):
        # The big external bang list isn't dumped on the page, it's searched
        # client-side, so the page ships the search input and its script, not
        # thousands of rows.
        resp = self.client.get(reverse('search:about'))
        self.assertContains(resp, 'id="bang-search"')
        self.assertContains(resp, 'id="bang-results"')
        self.assertContains(resp, 'about.js')

    def test_no_template_comment_leaks_into_html(self):
        # Regression: a multi-line {# #} isn't a real Django comment (those are
        # single-line only), so it leaked into the page as text, and its stray
        # "<noscript>" word opened a real element that hid the search form when
        # JS was on. Guard against template comments reaching the browser.
        resp = self.client.get(reverse('search:about'))
        self.assertNotContains(resp, '{#')
        self.assertNotContains(resp, 'filters live via')
        # No-JS fallbacks use the `nojs:` CSS variant now (not <noscript>, which a
        # CSP blocker like NoScript leaves unrendered), so the page should carry no
        # <noscript> at all, and certainly no stray one from a leaked comment.
        self.assertEqual(resp.content.decode().count('<noscript>'), 0)

    def test_shows_external_bang_count(self):
        resp = self.client.get(reverse('search:about'))
        count = bangs.count()
        self.assertGreater(count, 0)
        self.assertEqual(resp.context['bang_count'], count)
        # Rendered with thousands separators (e.g. "13,558").
        self.assertContains(resp, f'{count:,}')


class BangCountTests(TestCase):
    def test_count_matches_loaded_map(self):
        self.assertEqual(bangs.count(), len(bangs._load()))


class BangSearchTests(TestCase):
    """Server-side bang search, the no-JS fallback for the About page box."""

    SAMPLE = {
        'g': 'https://www.google.com/search?q={{{s}}}',
        'gh': 'https://github.com/search?q={{{s}}}',
        'github': 'https://github.com/search?q={{{s}}}',
        'w': 'https://en.wikipedia.org/w/index.php?search={{{s}}}',
    }

    def test_empty_query_returns_nothing(self):
        self.assertEqual(bangs.search('   '), ([], 0))

    def test_ranks_exact_then_prefix_by_length(self):
        with patch('search.bangs._load', return_value=self.SAMPLE):
            rows, _ = bangs.search('g')
        triggers = [r['trigger'] for r in rows]
        self.assertEqual(triggers[0], 'g')                          # exact wins
        self.assertLess(triggers.index('gh'), triggers.index('github'))  # shorter prefix first

    def test_matches_on_hostname_when_trigger_misses(self):
        with patch('search.bangs._load', return_value=self.SAMPLE):
            rows, total = bangs.search('wikipedia')
        self.assertEqual([r['trigger'] for r in rows], ['w'])
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]['hostname'], 'en.wikipedia.org')

    def test_strips_leading_bang_and_lowercases(self):
        with patch('search.bangs._load', return_value=self.SAMPLE):
            rows, _ = bangs.search('!GH')
        self.assertEqual(rows[0]['trigger'], 'gh')

    def test_limit_caps_rows_but_total_is_full_count(self):
        data = {f't{i:03d}': f'https://e{i:03d}.example.com/?q={{{{{{s}}}}}}' for i in range(50)}
        with patch('search.bangs._load', return_value=data):
            rows, total = bangs.search('t', limit=10)
        self.assertEqual(len(rows), 10)
        self.assertEqual(total, 50)

    def test_includes_and_flags_user_custom_bangs(self):
        from search.models import CustomBang
        user = User.objects.create_user('erin', password='pass')
        CustomBang.objects.create(user=user, trigger='mine', url_template='https://example.org/s?q={{{s}}}')
        with patch('search.bangs._load', return_value={}):
            rows, total = bangs.search('mine', user=user)
        self.assertEqual(total, 1)
        self.assertTrue(rows[0]['custom'])
        self.assertEqual(rows[0]['hostname'], 'example.org')

    def test_custom_bang_overrides_generic(self):
        from search.models import CustomBang
        user = User.objects.create_user('frank', password='pass')
        CustomBang.objects.create(user=user, trigger='gh', url_template='https://my.example/{{{s}}}')
        with patch('search.bangs._load', return_value=self.SAMPLE):
            rows, _ = bangs.search('gh', user=user)
        gh = next(r for r in rows if r['trigger'] == 'gh')
        self.assertTrue(gh['custom'])
        self.assertEqual(gh['url'], 'https://my.example/{{{s}}}')


class AboutBangSearchViewTests(TestCase):
    """The About page answers ?bang=… server-side, so the box works without JS."""

    SAMPLE = {
        'gh': 'https://github.com/search?q={{{s}}}',
        'github': 'https://github.com/search?q={{{s}}}',
    }

    def setUp(self):
        self.user = User.objects.create_user('grace', password='pass')
        self.client.login(username='grace', password='pass')

    def test_no_query_renders_no_results(self):
        resp = self.client.get(reverse('search:about'))
        self.assertEqual(resp.context['bang_query'], '')
        self.assertEqual(list(resp.context['bang_matches']), [])

    def test_query_renders_matches_server_side(self):
        with patch('search.bangs._load', return_value=self.SAMPLE):
            resp = self.client.get(reverse('search:about'), {'bang': 'github'})
        self.assertEqual(resp.context['bang_query'], 'github')
        self.assertContains(resp, '!github')
        self.assertContains(resp, 'github.com')
        self.assertContains(resp, 'value="github"')  # echoed back into the input

    def test_no_match_shows_message(self):
        with patch('search.bangs._load', return_value=self.SAMPLE):
            resp = self.client.get(reverse('search:about'), {'bang': 'zzzznope'})
        self.assertEqual(list(resp.context['bang_matches']), [])
        self.assertContains(resp, 'No bangs match')

    def test_form_has_nojs_submit_fallback(self):
        # No-JS (incl. NoScript): a real submit button, revealed by the `nojs:`
        # variant, replaces the live JS filter.
        resp = self.client.get(reverse('search:about'))
        self.assertContains(resp, 'name="bang"')
        self.assertContains(resp, 'nojs:inline-flex')
        self.assertContains(resp, 'type="submit"')


class ResultsViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('bob', password='pass')
        self.client.login(username='bob', password='pass')

    def test_no_query_returns_200(self):
        resp = self.client.get(reverse('search:results'))
        self.assertEqual(resp.status_code, 200)

    def test_query_in_context(self):
        resp = self.client.get(reverse('search:results') + '?q=python')
        self.assertEqual(resp.context['query'], 'python')

    def test_invalid_tab_defaults_to_web(self):
        resp = self.client.get(reverse('search:results') + '?q=test&tab=invalid')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'web')

    def test_valid_tab_preserved(self):
        resp = self.client.get(reverse('search:results') + '?q=test&tab=images')
        self.assertEqual(resp.context['active_tab'], 'images')

    @patch('search.views.fetch_videos', return_value=[])
    def test_date_filter_shown_on_videos(self, mock_videos):
        # Videos support time filtering (Brave freshness / Sepia startDate).
        resp = self.client.get(reverse('search:results') + '?q=test&tab=videos')
        self.assertTrue(resp.context['show_date_filter'])

    @patch('search.views.fetch_images', return_value=[])
    def test_date_filter_hidden_on_images(self, mock_images):
        # Neither image provider supports time filtering, so the control is hidden.
        resp = self.client.get(reverse('search:results') + '?q=test&tab=images')
        self.assertFalse(resp.context['show_date_filter'])

    @patch('search.views.fetch_images')
    def test_images_tab_renders_similar_image_lightbox(self, mock_images):
        # Opening an image is a client-side lightbox carrying the "similar
        # images" endpoint; it (and its script) ship with results.
        mock_images.return_value = [
            {'title': 'A cat', 'url': 'https://ex.com/cat',
             'source': 'ex.com', 'thumbnail': {'src': 'https://ex.com/cat.jpg'}},
        ]
        resp = self.client.get(reverse('search:results') + '?q=cats&tab=images')
        self.assertContains(resp, 'id="img-lightbox"')
        self.assertContains(resp, 'data-img-card')
        self.assertContains(resp, 'search/image-similar/')   # real-images endpoint
        self.assertContains(resp, 'search/images.js')
        # Skeleton loader markup ships for the JS loading state.
        self.assertContains(resp, 'id="img-skeleton-tpl"')
        self.assertContains(resp, 'animate-pulse')
        # The result link falls back to the server-rendered detail page (no JS).
        self.assertContains(resp, '/search/image/?q=')
        # The source badge still links straight to the source (Pixabay attribution).
        self.assertContains(resp, 'href="https://ex.com/cat"')
        # The non-working image-type filter toolbar was removed.
        self.assertNotContains(resp, 'Wallpapers')
        # Template comments must not leak as text (Django {# #} is single-line only).
        self.assertNotContains(resp, 'images.js intercepts')

    @patch('search.views.fetch_images', return_value=[])
    def test_no_image_results_omits_lightbox_and_script(self, mock_images):
        resp = self.client.get(reverse('search:results') + '?q=cats&tab=images')
        self.assertNotContains(resp, 'id="img-lightbox"')
        self.assertNotContains(resp, 'search/images.js')

    def test_open_links_new_tab_false_by_default(self):
        resp = self.client.get(reverse('search:results') + '?q=test')
        self.assertFalse(resp.context['open_links_new_tab'])

    def test_open_links_new_tab_from_prefs(self):
        _set_prefs(self.client, open_links_new_tab=True)
        resp = self.client.get(reverse('search:results') + '?q=test')
        self.assertTrue(resp.context['open_links_new_tab'])

    @override_settings(BRAVE_API_KEY='test-key')
    @patch('web.services._brave_request')
    def test_brave_results_displayed(self, mock_brave):
        mock_brave.return_value = {
            'web': {'results': [
                {
                    'title': 'Test Result',
                    'url': 'https://test.com',
                    'description': 'A test page',
                    'meta_url': {'netloc': 'test.com', 'path': ''},
                    'profile': {},
                }
            ]}
        }
        _set_prefs(self.client, only_engine='brave')
        resp = self.client.get(reverse('search:results') + '?q=hello')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Test Result')

    @override_settings(MOJEEK_API_KEY='test-key')
    @patch('web.services._mojeek_request')
    def test_mojeek_results_displayed(self, mock_mojeek):
        mock_mojeek.return_value = {
            'response': {'results': [
                {'title': 'Mojeek Result', 'url': 'https://mojeek-result.com', 'desc': 'Found it'}
            ]}
        }
        _set_prefs(self.client, only_engine='mojeek')
        resp = self.client.get(reverse('search:results') + '?q=hello')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Mojeek Result')

    @override_settings(BRAVE_API_KEY='test-key', MOJEEK_API_KEY='test-key', MARGINALIA_API_KEY='')
    @patch('web.services._brave_request')
    @patch('web.services._mojeek_request')
    def test_both_engines_merged(self, mock_mojeek, mock_brave):
        mock_brave.return_value = {
            'web': {'results': [
                {'title': 'Brave Result', 'url': 'https://brave.com', 'description': '',
                 'meta_url': {'netloc': 'brave.com', 'path': ''}, 'profile': {}}
            ]}
        }
        mock_mojeek.return_value = {
            'response': {'results': [
                {'title': 'Mojeek Result', 'url': 'https://mojeek.com', 'desc': ''}
            ]}
        }
        _set_prefs(self.client)
        resp = self.client.get(reverse('search:results') + '?q=hello')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Brave Result')
        self.assertContains(resp, 'Mojeek Result')

    @override_settings(BRAVE_API_KEY='test-key')
    @patch('web.services._brave_request')
    def test_safe_search_on_maps_to_brave_strict(self, mock_brave):
        # "On" is exposed to users as a single toggle; Brave gets its strongest level.
        mock_brave.return_value = {'web': {'results': []}}
        _set_prefs(self.client, only_engine='brave', safe_search='on')
        self.client.get(reverse('search:results') + '?q=test')
        self.assertEqual(mock_brave.call_args[0][1]['safesearch'], 'strict')

    @override_settings(BRAVE_API_KEY='test-key')
    @patch('web.services._brave_request')
    def test_safe_search_off_maps_to_brave_off(self, mock_brave):
        mock_brave.return_value = {'web': {'results': []}}
        _set_prefs(self.client, only_engine='brave', safe_search='off')
        self.client.get(reverse('search:results') + '?q=test')
        self.assertEqual(mock_brave.call_args[0][1]['safesearch'], 'off')

    @override_settings(BRAVE_API_KEY='test-key', PIXABAY_API_KEY='')
    @patch('images.services._brave_request')
    def test_image_safesearch_strict_and_no_offset(self, mock_brave):
        # The image endpoint has no offset and only accepts off/strict.
        mock_brave.return_value = {'results': []}
        _set_prefs(self.client, safe_search='on')
        self.client.get(reverse('search:results') + '?q=cats&tab=images')
        endpoint, params = mock_brave.call_args[0][0], mock_brave.call_args[0][1]
        self.assertEqual(endpoint, '/images/search')
        self.assertEqual(params['safesearch'], 'strict')
        self.assertNotIn('offset', params)

    @override_settings(BRAVE_API_KEY='test-key')
    @patch('images.services._brave_request')
    def test_image_results_and_source_displayed(self, mock_brave):
        mock_brave.return_value = {'results': [
            {'title': 'A Cat', 'url': 'https://ex.com/p', 'source': 'example.com',
             'thumbnail': {'src': 'https://imgs.brave.com/cat.jpg'}}
        ]}
        resp = self.client.get(reverse('search:results') + '?q=cats&tab=images')
        self.assertEqual(resp.status_code, 200)
        # Proxying is off by default, so the thumbnail is hot-linked as-is.
        self.assertContains(resp, 'https://imgs.brave.com/cat.jpg')
        self.assertContains(resp, '>example.com<')  # source shown as an on-image badge

    @override_settings(BRAVE_API_KEY='test-key')
    @patch('images.services._brave_request')
    def test_images_proxied_when_enabled(self, mock_brave):
        from search.models import ProxiedImage
        mock_brave.return_value = {'results': [
            {'title': 'A Cat', 'url': 'https://ex.com/p', 'source': 'example.com',
             'thumbnail': {'src': 'https://imgs.brave.com/cat.jpg'}}
        ]}
        _set_prefs(self.client, proxy_images=True)
        resp = self.client.get(reverse('search:results') + '?q=cats&tab=images')
        self.assertEqual(resp.status_code, 200)
        # With proxying on, the raw CDN URL is replaced by the token path.
        self.assertNotContains(resp, 'https://imgs.brave.com/cat.jpg')
        self.assertContains(resp, reverse('search:image_proxy') + '?id=')
        self.assertTrue(ProxiedImage.objects.filter(url='https://imgs.brave.com/cat.jpg').exists())

    @override_settings(BRAVE_API_KEY='', PIXABAY_API_KEY='test-key')
    @patch('images.services._pixabay_request')
    def test_pixabay_always_proxied_even_with_proxying_off(self, mock_pixabay):
        from search.models import ProxiedImage
        mock_pixabay.return_value = {'hits': [
            {'tags': 'cat', 'pageURL': 'https://pixabay.com/photos/cat-1/',
             'webformatURL': 'https://cdn.pixabay.com/photo/cat.jpg'}
        ]}
        # proxy_images is left at its off-by-default setting.
        resp = self.client.get(reverse('search:results') + '?q=cats&tab=images')
        self.assertEqual(resp.status_code, 200)
        # Pixabay's terms forbid hotlinking, so its CDN URL must never reach the page.
        self.assertNotContains(resp, 'https://cdn.pixabay.com/photo/cat.jpg')
        self.assertContains(resp, reverse('search:image_proxy') + '?id=')
        entry = ProxiedImage.objects.get(url='https://cdn.pixabay.com/photo/cat.jpg')
        self.assertTrue(entry.persist)

    @override_settings(BRAVE_API_KEY='test-key')
    @patch('images.services._brave_request')
    def test_image_tab_has_pagination(self, mock_brave):
        mock_brave.return_value = {'results': [
            {'title': f'Img {i}', 'url': f'https://ex.com/{i}', 'source': 'example.com',
             'thumbnail': {'src': f'https://imgs.brave.com/{i}.jpg'}}
            for i in range(10)
        ]}
        resp = self.client.get(reverse('search:results') + '?q=cats&tab=images')
        self.assertContains(resp, 'tab=images&amp;page=2')  # Next link present

    @override_settings(BRAVE_API_KEY='', PIXABAY_API_KEY='pix-key')
    @patch('images.services._pixabay_request')
    def test_pixabay_image_shows_attribution(self, mock_pixabay):
        # Pixabay's terms require crediting/linking back: the figure links to the
        # image's Pixabay page and carries a visible "Pixabay" badge.
        mock_pixabay.return_value = {'hits': [
            {'tags': 'cat', 'pageURL': 'https://pixabay.com/p/1/',
             'webformatURL': 'https://cdn.pixabay.com/cat_640.jpg'},
        ]}
        resp = self.client.get(reverse('search:results') + '?q=cats&tab=images')
        self.assertEqual(resp.status_code, 200)
        # The thumbnail is force-proxied (Pixabay forbids hotlinking), but the
        # attribution link still points straight at the Pixabay page.
        self.assertNotContains(resp, 'https://cdn.pixabay.com/cat_640.jpg')
        self.assertContains(resp, reverse('search:image_proxy') + '?id=')
        self.assertContains(resp, 'https://pixabay.com/p/1/')  # links back to the source page
        self.assertContains(resp, '>Pixabay<')  # attribution badge rendered

    @override_settings(BRAVE_API_KEY='', WORLDNEWS_API_KEY='wn-key')
    @patch('news.services._worldnews_request')
    def test_news_result_shows_provider_badge(self, mock_wn):
        # Each news result shows which provider it came from (Brave / World News).
        mock_wn.return_value = {'news': [
            {'title': 'WN headline', 'url': 'https://news.example.com/x',
             'summary': 'Body.', 'publish_date': '2024-01-15 10:00:00'},
        ]}
        _set_prefs(self.client, only_engine='mojeek')  # no Brave → World News only
        resp = self.client.get(reverse('search:results') + '?q=kyiv&tab=news')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'WN headline')
        self.assertContains(resp, '>World News API<')  # provider badge rendered

    @override_settings(BRAVE_API_KEY='', WORLDNEWS_API_KEY='wn-key')
    @patch('news.services._worldnews_request')
    def test_news_thumbnail_placeholder(self, mock_wn):
        # Every result gets the thumbnail slot, with a placeholder icon behind
        # the image: it shows for an article with no thumbnail at all, and
        # news.js uncovers it when a thumbnail fails to load.
        mock_wn.return_value = {'news': [
            {'title': 'With image', 'url': 'https://news.example.com/a',
             'summary': 'Body.', 'image': 'https://news.example.com/a.jpg'},
            {'title': 'No image', 'url': 'https://news.example.com/b', 'summary': 'Body.'},
        ]}
        _set_prefs(self.client, only_engine='mojeek')  # no Brave → World News only
        resp = self.client.get(reverse('search:results') + '?q=kyiv&tab=news')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content.decode().count('data-news-thumb'), 2)
        self.assertEqual(resp.content.decode().count('fa-newspaper text-[20px]'), 2)
        self.assertContains(resp, 'https://news.example.com/a.jpg')  # the one real thumbnail
        self.assertContains(resp, 'news/news.js')  # fallback script wired up


@override_settings(BRAVE_API_KEY='k', MOJEEK_API_KEY='k', MARGINALIA_API_KEY='k',
                   STAAN_API_KEY='k', PIXABAY_API_KEY='k', WORLDNEWS_API_KEY='k')
class EmptyStateTests(TestCase):
    """The panels with nothing to show: an invitation before the first search,
    "nothing matched" only once a query actually came back empty (the keys are
    configured here, an unconfigured provider has its own notice)."""

    def setUp(self):
        self.user = User.objects.create_user('emma', password='pass')
        self.client.login(username='emma', password='pass')

    def test_no_query_invites_a_search_on_every_tab(self):
        # Switching tabs carries no query, so the tab must not claim it searched
        # for the empty string ("No image results found for ...").
        for tab, invitation in (
            ('web', 'Search the web'),
            ('images', 'Search for images'),
            ('news', 'Search the news'),
            ('videos', 'Search for videos'),
            ('maps', 'Explore the map'),
        ):
            with self.subTest(tab=tab):
                resp = self.client.get(reverse('search:results') + f'?tab={tab}')
                self.assertEqual(resp.status_code, 200)
                self.assertContains(resp, invitation)
                self.assertNotContains(resp, 'Nothing matched')

    @patch('search.views.fetch_images', return_value=[])
    def test_empty_image_search_reports_the_query(self, mock_images):
        resp = self.client.get(reverse('search:results') + '?q=zzznotathing&tab=images')
        self.assertContains(resp, 'No images found')
        self.assertContains(resp, 'zzznotathing')
        self.assertNotContains(resp, 'Search for images')

    @patch('search.views.fetch_videos', return_value=[])
    def test_empty_video_search_reports_the_query(self, mock_videos):
        resp = self.client.get(reverse('search:results') + '?q=zzznotathing&tab=videos')
        self.assertContains(resp, 'No videos found')
        self.assertNotContains(resp, 'Search for videos')

    @patch('search.views.fetch_news', return_value=[])
    def test_empty_news_search_reports_the_query(self, mock_news):
        resp = self.client.get(reverse('search:results') + '?q=zzznotathing&tab=news')
        self.assertContains(resp, 'No news found')
        self.assertNotContains(resp, 'Search the news')

    @patch('search.views.fetch_web', return_value=([], None))
    def test_empty_web_search_reports_the_query(self, mock_web):
        resp = self.client.get(reverse('search:results') + '?q=zzznotathing&tab=web')
        self.assertContains(resp, 'No results found')
        self.assertContains(resp, 'zzznotathing')
        self.assertNotContains(resp, 'Search the web')

    def test_empty_web_panel_has_nothing_beside_the_message(self):
        # The alignment rule in assets/app.css keys off this shape: no
        # knowledge panel, and the message alone in the results column
        # (`:only-child`), which is what lets an empty Web tab centre its
        # message on the whole panel like every other tab instead of inside
        # the 700px results column.
        resp = self.client.get(reverse('search:results') + '?tab=web')
        self.assertContains(resp, 'results-empty')
        self.assertNotContains(resp, 'knowledge-panel')  # no cards without a query
        self.assertNotContains(resp, 'ia-wrap')          # nor an instant answer
        self.assertNotContains(resp, 'Did you mean')     # nor a spelling correction

    def test_panel_comments_do_not_leak_as_text(self):
        # Django's {# #} comment is single-line only; a multi-line one renders
        # as visible text on exactly these (otherwise empty) panels.
        for tab in ('web', 'images', 'news', 'videos', 'maps'):
            with self.subTest(tab=tab):
                resp = self.client.get(reverse('search:results') + f'?tab={tab}')
                self.assertNotContains(resp, '{#')
                self.assertNotContains(resp, '#}')

    @patch('search.views.fetch_images', return_value=[])
    def test_query_is_escaped_once_in_the_empty_state(self, mock_images):
        # The message is built with {% blocktrans asvar %} and rendered |safe,
        # so the query it interpolates must still be escaped exactly once.
        resp = self.client.get(reverse('search:results') + '?q=%3Cb%3Ex%26y&tab=images')
        body = resp.content.decode()
        self.assertIn('&lt;b&gt;x&amp;y', body)
        self.assertNotIn('<b>x', body)
        self.assertNotIn('&amp;lt;', body)


class SettingsViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('carol', password='pass')
        self.client.login(username='carol', password='pass')

    def test_get_renders(self):
        resp = self.client.get(reverse('search:settings'))
        self.assertEqual(resp.status_code, 200)

    def test_get_context_defaults(self):
        resp = self.client.get(reverse('search:settings'))
        # Every provider of every search type enabled by default.
        web = _type_group(resp, 'web')
        self.assertEqual([p['key'] for p in web['engines'] if p['enabled']],
                         ['brave', 'mojeek', 'marginalia', 'staan'])
        self.assertTrue(all(p['enabled'] for p in web['extras']))
        self.assertEqual(resp.context['safe_search'], 'on')
        self.assertFalse(resp.context['open_links_new_tab'])

    def test_save_engines_subset(self):
        # The user can enable any combination, e.g. just Mojeek + Marginalia.
        resp = self.client.post(reverse('search:settings'), _save_web(
            provider_mojeek='on', provider_marginalia='on',
        ))
        self.assertRedirects(resp, reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        self.assertEqual(_get_prefs(self.client)['disabled_providers']['web'],
                         ['brave', 'staan', 'wikipedia', 'thetvdb', 'tripadvisor',
                          'stackexchange', 'weather'])

    def test_save_single_engine(self):
        self.client.post(reverse('search:settings'), _save_web(provider_mojeek='on'))
        self.assertEqual(_web_engines(self.client), ['mojeek'])

    def test_save_all_engines_off(self):
        # Turning everything off is allowed (the web panel then shows a notice).
        self.client.post(reverse('search:settings'), _save_web())
        self.assertEqual(_web_engines(self.client), [])

    def test_save_is_scoped_to_one_search_type(self):
        # Switching Brave off for the Web leaves the Images tab on Brave: each
        # search type carries its own selection.
        self.client.post(reverse('search:settings'), _save_web(
            provider_mojeek='on', provider_staan='on',
        ))
        prefs = _get_prefs(self.client)
        self.assertNotIn('brave', preferences.enabled_engines(prefs, 'web'))
        self.assertEqual(preferences.enabled_providers(prefs, 'images'), ['brave', 'pixabay'])

    def test_save_per_type_selection(self):
        # "For images I want just Brave" — Pixabay off, Web untouched.
        self.client.post(reverse('search:settings'), {
            'setting': 'providers', 'search_type': 'images', 'pane': 'engine',
            'provider_brave': 'on',
        })
        prefs = _get_prefs(self.client)
        self.assertEqual(preferences.enabled_providers(prefs, 'images'), ['brave'])
        self.assertEqual(preferences.enabled_engines(prefs, 'web'),
                         ['brave', 'mojeek', 'marginalia', 'staan'])

    def test_save_unknown_search_type_changes_nothing(self):
        self.client.post(reverse('search:settings'), {
            'setting': 'providers', 'search_type': 'bogus', 'pane': 'engine',
        })
        self.assertEqual(_get_prefs(self.client)['disabled_providers'], {})

    def test_save_safe_search_off(self):
        # The toggle is on by default; unchecking omits the field → off.
        self.client.post(
            reverse('search:settings'),
            {'setting': 'safe_search', 'pane': 'general'},
        )
        self.assertEqual(_get_prefs(self.client)['safe_search'], 'off')

    def test_save_safe_search_on(self):
        # Start from off, then tick the toggle (submits safe_search=on).
        _set_prefs(self.client, safe_search='off')
        self.client.post(
            reverse('search:settings'),
            {'setting': 'safe_search', 'safe_search': 'on'},
        )
        self.assertEqual(_get_prefs(self.client)['safe_search'], 'on')

    def test_save_open_links_new_tab_on(self):
        self.client.post(
            reverse('search:settings'),
            {'setting': 'open_links_new_tab', 'open_links_new_tab': 'on'},
        )
        self.assertTrue(_get_prefs(self.client)['open_links_new_tab'])

    def test_save_open_links_new_tab_off(self):
        self.client.post(
            reverse('search:settings'),
            {'setting': 'open_links_new_tab'},
        )
        self.assertFalse(_get_prefs(self.client)['open_links_new_tab'])


class SearchCountTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('eve', password='pass')
        self.client.login(username='eve', password='pass')

    def test_zero_by_default(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'account'}))
        self.assertEqual(resp.context['search_count'], 0)
        self.assertContains(resp, 'Searches this month')

    def test_increments_per_provider(self):
        # Each provider a web search hits counts as one search. With the default
        # four engines enabled, two web searches add four each → eight.
        self.client.get(reverse('search:results') + '?q=python')
        self.client.get(reverse('search:results') + '?q=django')
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'account'}))
        self.assertEqual(resp.context['search_count'], 8)

    def test_scope_narrows_provider_count(self):
        # A per-request scope of two providers counts as two.
        self.client.get(reverse('search:results') + '?q=python&scope=brave&scope=mojeek')
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'account'}))
        self.assertEqual(resp.context['search_count'], 2)

    def test_single_scope_counts_one(self):
        self.client.get(reverse('search:results') + '?q=python&scope=marginalia')
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'account'}))
        self.assertEqual(resp.context['search_count'], 1)

    def test_non_web_tab_counts_one(self):
        # Maps hits a single provider, so it counts as one regardless of scope.
        self.client.get(reverse('search:results') + '?q=paris&tab=maps')
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'account'}))
        self.assertEqual(resp.context['search_count'], 1)

    def test_no_query_does_not_increment(self):
        self.client.get(reverse('search:results'))
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'account'}))
        self.assertEqual(resp.context['search_count'], 0)

    def test_count_is_per_user(self):
        User.objects.create_user('frank', password='pass')
        self.client.get(reverse('search:results') + '?q=python')

        other_client = self.client_class()
        other_client.login(username='frank', password='pass')
        resp = other_client.get(reverse('search:settings_pane', kwargs={'pane': 'account'}))
        self.assertEqual(resp.context['search_count'], 0)

    def test_record_search_adds_count(self):
        from search import usage
        usage.record_search(self.user, 3)
        usage.record_search(self.user, 2)
        self.assertEqual(usage.searches_this_month(self.user), 5)

    def test_record_search_ignores_non_positive(self):
        from search import usage
        usage.record_search(self.user, 0)
        usage.record_search(self.user, -4)
        self.assertEqual(usage.searches_this_month(self.user), 0)


class SearchScopeTests(TestCase):
    """Search scope on the web tab: a per-request provider picker (Brave / Mojeek
    / Marginalia) that overrides the saved engine preferences for one search,
    like the engine settings but as a quick setting on each search. The other
    tabs' provider pickers are covered by ``TabScopeTests`` below."""

    def setUp(self):
        self.user = User.objects.create_user('scout', password='pass')
        self.client.login(username='scout', password='pass')

    def test_scope_overrides_engines_queried(self):
        # The scope, not the saved engine prefs, decides which engines run.
        with patch('search.views.fetch_web', return_value=([], '')) as mock_web:
            self.client.get(reverse('search:results') + '?q=hi&scope=brave&scope=mojeek')
        self.assertEqual(mock_web.call_args[0][1], ['brave', 'mojeek'])

    def test_scope_order_is_canonical(self):
        # Whatever order the scope params arrive in, engines come back canonical.
        with patch('search.views.fetch_web', return_value=([], '')) as mock_web:
            self.client.get(reverse('search:results') + '?q=hi&scope=mojeek&scope=brave')
        self.assertEqual(mock_web.call_args[0][1], ['brave', 'mojeek'])

    def test_scope_can_select_engine_disabled_in_settings(self):
        # Scope is a true override: an engine switched off in Settings can still
        # be picked for a single search.
        _set_prefs(self.client, providers_off=['brave', 'mojeek', 'marginalia', 'staan'])
        with patch('search.views.fetch_web', return_value=([], '')) as mock_web:
            self.client.get(reverse('search:results') + '?q=hi&scope=brave')
        self.assertEqual(mock_web.call_args[0][1], ['brave'])

    def test_invalid_scope_value_ignored(self):
        with patch('search.views.fetch_web', return_value=([], '')) as mock_web:
            self.client.get(reverse('search:results') + '?q=hi&scope=brave&scope=bogus')
        self.assertEqual(mock_web.call_args[0][1], ['brave'])

    def test_empty_scope_falls_back_to_enabled_engines(self):
        # No (or all-invalid) scope → the user's saved engine preferences apply.
        _set_prefs(self.client, providers_off=['marginalia', 'staan'])
        with patch('search.views.fetch_web', return_value=([], '')) as mock_web:
            self.client.get(reverse('search:results') + '?q=hi&scope=bogus')
        self.assertEqual(mock_web.call_args[0][1], ['brave', 'mojeek'])

    def test_picker_rendered_with_provider_checkboxes(self):
        resp = self.client.get(reverse('search:results') + '?q=hi')
        self.assertContains(resp, 'name="scope" value="brave"')
        self.assertContains(resp, 'name="scope" value="mojeek"')
        self.assertContains(resp, 'name="scope" value="marginalia"')
        self.assertContains(resp, 'name="scope" value="staan"')

    def test_picker_badges_the_paid_providers(self):
        # The scope menu is the other place a search's providers are chosen, so
        # it carries the same "Paid" badge Settings → Engines shows.
        resp = self.client.get(reverse('search:results'))  # no query → no network
        menu = re.search(r'<details id="scope-menu".*?</details>',
                         resp.content.decode(), re.DOTALL).group(0)
        rows = {re.search(r'value="(\w+)"', row).group(1): '>Paid<' in row
                for row in menu.split('<label')[1:]}
        self.assertEqual(rows, {'brave': True, 'mojeek': True,
                                'marginalia': False, 'staan': True})

    def test_scope_comments_do_not_leak_into_page(self):
        # Django ``{# #}`` comments must be single-line; a multi-line one renders
        # as literal text. Guard against that regression for the scope markup.
        resp = self.client.get(reverse('search:results') + '?q=hi&scope=brave')
        self.assertNotContains(resp, '{#')
        self.assertNotContains(resp, 'per-search provider picker')

    def test_picker_reflects_active_scope(self):
        resp = self.client.get(reverse('search:results') + '?q=hi&scope=brave')
        opts = {o['key']: o['checked'] for o in resp.context['scope_options']}
        self.assertEqual(
            opts,
            {'brave': True, 'mojeek': False, 'marginalia': False, 'staan': False},
        )
        self.assertEqual(resp.context['scope_count'], 1)
        self.assertTrue(resp.context['scope_active'])

    def test_picker_default_reflects_enabled_engines(self):
        _set_prefs(self.client, providers_off=['mojeek'])
        resp = self.client.get(reverse('search:results') + '?q=hi')
        opts = {o['key']: o['checked'] for o in resp.context['scope_options']}
        self.assertEqual(
            opts,
            {'brave': True, 'mojeek': False, 'marginalia': True, 'staan': True},
        )
        self.assertFalse(resp.context['scope_active'])

    def test_active_scope_carried_on_new_query_form(self):
        # The header search form keeps the chosen scope as hidden inputs so a new
        # query doesn't silently reset to the default engines.
        resp = self.client.get(reverse('search:results') + '?q=hi&scope=brave')
        self.assertContains(resp, '<input type="hidden" name="scope" value="brave">')

    @patch('search.views.fetch_wikipedia', return_value=None)
    @patch('search.views.fetch_web', return_value=([], ''))
    def test_deferred_cards_endpoint_honours_scope(self, mock_web, mock_wiki):
        # The cards endpoint re-reads the web results under the same scope, so it
        # hits the cache the search populated rather than re-querying all engines.
        self.client.get(reverse('search:cards') + '?q=hi&scope=brave&instant=0')
        self.assertEqual(mock_web.call_args[0][1], ['brave'])


class TabScopeTests(TestCase):
    """The provider picker works on every blended tab, not just Web: each tab
    offers its own providers (Images → Brave + Pixabay, News → Brave + World
    News, Videos → Brave + Sepia) and the scope decides which of them run."""

    def setUp(self):
        self.user = User.objects.create_user('vera', password='pass')
        self.client.login(username='vera', password='pass')

    def _results(self, params):
        return self.client.get(reverse('search:results') + params)

    # --- Images -------------------------------------------------------------
    @patch('search.views.fetch_images', return_value=[])
    def test_images_scoped_to_pixabay_drops_brave(self, mock_images):
        self._results('?q=cats&tab=images&scope=pixabay')
        self.assertEqual(mock_images.call_args[0][1], [])  # no engine → no Brave
        self.assertTrue(mock_images.call_args[1]['pixabay_enabled'])

    @patch('search.views.fetch_images', return_value=[])
    def test_images_scoped_to_brave_drops_pixabay(self, mock_images):
        self._results('?q=cats&tab=images&scope=brave')
        self.assertEqual(mock_images.call_args[0][1], ['brave'])
        self.assertFalse(mock_images.call_args[1]['pixabay_enabled'])

    @patch('search.views.fetch_images', return_value=[])
    def test_images_default_uses_both_providers(self, mock_images):
        self._results('?q=cats&tab=images')
        self.assertEqual(mock_images.call_args[0][1], ['brave'])
        self.assertTrue(mock_images.call_args[1]['pixabay_enabled'])

    @patch('search.views.fetch_images', return_value=[])
    def test_images_engine_excludes_web_only_engines(self, mock_images):
        # Mojeek/Marginalia can't serve images, so they never reach the fetcher
        # (which keeps the per-provider cache keys from varying pointlessly).
        self._results('?q=cats&tab=images&scope=brave&scope=mojeek')
        self.assertEqual(mock_images.call_args[0][1], ['brave'])

    @patch('search.views.fetch_images', return_value=[])
    def test_images_default_follows_disabled_source(self, mock_images):
        # No scope → the saved Settings choices, source toggles included.
        _set_prefs(self.client, providers_off=['pixabay'])
        self._results('?q=cats&tab=images')
        self.assertFalse(mock_images.call_args[1]['pixabay_enabled'])

    @patch('search.views.fetch_images', return_value=[])
    def test_images_scope_overrides_disabled_source(self, mock_images):
        # Like engines, the scope is a true override: a source switched off in
        # Settings can still be picked for a single search.
        _set_prefs(self.client, providers_off=['pixabay'])
        self._results('?q=cats&tab=images&scope=pixabay')
        self.assertTrue(mock_images.call_args[1]['pixabay_enabled'])
        self.assertEqual(mock_images.call_args[0][1], [])

    @patch('search.views.fetch_images', return_value=[])
    def test_images_default_follows_disabled_providers(self, mock_images):
        _set_prefs(self.client, providers_off=['brave', 'mojeek', 'marginalia', 'staan'])
        self._results('?q=cats&tab=images')
        self.assertEqual(mock_images.call_args[0][1], [])
        self.assertTrue(mock_images.call_args[1]['pixabay_enabled'])

    # --- News / Videos ------------------------------------------------------
    @patch('search.views.fetch_news', return_value=[])
    def test_news_scoped_to_worldnews(self, mock_news):
        self._results('?q=ukraine&tab=news&scope=worldnews')
        self.assertEqual(mock_news.call_args[0][1], [])
        self.assertTrue(mock_news.call_args[1]['worldnews_enabled'])

    @patch('search.views.fetch_news', return_value=[])
    def test_news_scoped_to_brave(self, mock_news):
        self._results('?q=ukraine&tab=news&scope=brave')
        self.assertEqual(mock_news.call_args[0][1], ['brave'])
        self.assertFalse(mock_news.call_args[1]['worldnews_enabled'])

    @patch('search.views.fetch_videos', return_value=[])
    def test_videos_scoped_to_sepia(self, mock_videos):
        self._results('?q=django&tab=videos&scope=sepia')
        self.assertEqual(mock_videos.call_args[0][1], [])
        self.assertTrue(mock_videos.call_args[1]['sepia_enabled'])

    @patch('search.views.fetch_videos', return_value=[])
    def test_videos_scoped_to_brave(self, mock_videos):
        self._results('?q=django&tab=videos&scope=brave')
        self.assertEqual(mock_videos.call_args[0][1], ['brave'])
        self.assertFalse(mock_videos.call_args[1]['sepia_enabled'])

    # --- Cross-tab behaviour ------------------------------------------------
    @patch('search.views.fetch_news', return_value=[])
    def test_scope_from_another_tab_is_ignored(self, mock_news):
        # Switching tabs keeps the scope in the URL; providers the new tab can't
        # serve are dropped, and an empty intersection means "tab default".
        self._results('?q=ukraine&tab=news&scope=mojeek')
        self.assertEqual(mock_news.call_args[0][1], ['brave'])
        self.assertTrue(mock_news.call_args[1]['worldnews_enabled'])
        self.assertFalse(self._results('?q=ukraine&tab=news&scope=mojeek').context['scope_active'])

    @patch('search.views.fetch_news', return_value=[])
    def test_shared_provider_carries_across_tabs(self, mock_news):
        # "Brave only" is meaningful on both tabs, so it does carry over.
        resp = self._results('?q=ukraine&tab=news&scope=brave&scope=mojeek')
        self.assertTrue(resp.context['scope_active'])
        self.assertFalse(mock_news.call_args[1]['worldnews_enabled'])

    @patch('search.views.fetch_news', return_value=[])
    def test_non_web_tab_still_counts_one_search(self, mock_news):
        # Counting is unchanged: only the web tab counts per provider.
        self._results('?q=ukraine&tab=news&scope=brave&scope=worldnews')
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'account'}))
        self.assertEqual(resp.context['search_count'], 1)

    # --- Picker rendering ---------------------------------------------------
    def test_picker_offers_the_tabs_own_providers(self):
        resp = self._results('?q=cats&tab=images')
        self.assertEqual([o['key'] for o in resp.context['scope_options']],
                         ['brave', 'pixabay'])
        self.assertContains(resp, 'name="scope" value="pixabay"')
        self.assertNotContains(resp, 'name="scope" value="mojeek"')

    def test_picker_labels_supplementary_provider(self):
        resp = self._results('?q=ukraine&tab=news')
        opt = resp.context['scope_options'][1]
        self.assertEqual(opt['key'], 'worldnews')
        self.assertEqual(str(opt['label']), str(preferences.SOURCE_LABELS['worldnews']))
        self.assertEqual(opt['flag'], preferences.SOURCE_FLAGS['worldnews'])

    def test_picker_reflects_active_scope(self):
        resp = self._results('?q=django&tab=videos&scope=sepia')
        self.assertEqual({o['key']: o['checked'] for o in resp.context['scope_options']},
                         {'brave': False, 'sepia': True})
        self.assertEqual(resp.context['scope_count'], 1)

    @override_settings(LIBRETRANSLATE_URL='http://localhost:5000')
    @patch('search.views.fetch_translation', return_value=None)
    @patch('search.views.fetch_languages', return_value=[])
    @patch('search.views.fetch_geocode', return_value=[])
    def test_single_provider_tabs_have_no_picker(self, *mocks):
        # Maps (OpenStreetMap) and Translate (LibreTranslate) have nothing to pick.
        for tab in ('maps', 'translate'):
            resp = self._results(f'?q=paris&tab={tab}')
            self.assertEqual(resp.context['active_tab'], tab)
            self.assertEqual(resp.context['scope_options'], [], tab)
            self.assertNotContains(resp, 'name="scope"')

    def test_active_scope_carried_on_new_query_form(self):
        resp = self._results('?q=cats&tab=images&scope=pixabay')
        self.assertContains(resp, '<input type="hidden" name="scope" value="pixabay">')

    def test_default_scope_is_not_pinned_to_links(self):
        # Nothing narrowed → no scope on the links, so the saved preferences keep
        # applying (rather than being frozen into every URL).
        resp = self._results('?q=cats&tab=images')
        self.assertEqual(resp.context['scope_keys'], [])
        self.assertNotContains(resp, 'type="hidden" name="scope"')


class ImagesScopeCarryTests(TestCase):
    """The Images tab's scope follows the image through the lightbox and the
    no-JS detail page, so similar images come from the same providers."""

    def setUp(self):
        self.user = User.objects.create_user('iris', password='pass')
        self.client.login(username='iris', password='pass')

    @patch('search.views.fetch_similar_images', return_value=('dogs', []))
    def test_similar_endpoint_honours_scope(self, mock_sim):
        self.client.get(reverse('search:image_similar') + '?q=dogs&scope=pixabay')
        self.assertEqual(mock_sim.call_args[1]['engine'], [])
        self.assertTrue(mock_sim.call_args[1]['pixabay_enabled'])

    @patch('search.views.fetch_similar_images', return_value=('dogs', []))
    def test_similar_endpoint_scoped_to_brave(self, mock_sim):
        self.client.get(reverse('search:image_similar') + '?q=dogs&scope=brave')
        self.assertEqual(mock_sim.call_args[1]['engine'], ['brave'])
        self.assertFalse(mock_sim.call_args[1]['pixabay_enabled'])

    @patch('search.views.fetch_similar_images', return_value=('dogs', []))
    def test_similar_endpoint_defaults_to_saved_providers(self, mock_sim):
        _set_prefs(self.client, providers_off=['pixabay'])
        self.client.get(reverse('search:image_similar') + '?q=dogs')
        self.assertEqual(mock_sim.call_args[1]['engine'], ['brave'])
        self.assertFalse(mock_sim.call_args[1]['pixabay_enabled'])

    @patch('search.views.fetch_images')
    def test_grid_carries_scope_to_lightbox_and_detail_link(self, mock_images):
        mock_images.return_value = [{
            'title': 'A cat', 'url': 'https://ex.com/cat', 'source': 'Pixabay',
            'thumbnail': {'src': 'https://ex.com/cat.jpg'},
        }]
        resp = self.client.get(reverse('search:results') + '?q=cats&tab=images&scope=pixabay')
        self.assertContains(resp, 'data-scope="pixabay"')  # lightbox lookup
        self.assertContains(resp, '&amp;scope=pixabay')     # no-JS detail link

    @patch('search.views.fetch_similar_images', return_value=('dogs', []))
    def test_detail_page_keeps_scope_on_its_links(self, mock_sim):
        resp = self.client.get(reverse('search:image_detail'), {
            'q': 'dogs', 'query': 'dogs', 'img': 'https://ex.com/dog.jpg',
            'src': 'https://ex.com/dog', 'scope': 'pixabay',
        })
        self.assertEqual(resp.context['scope_keys'], ['pixabay'])
        self.assertContains(resp, 'tab=images&amp;q=dogs&amp;scope=pixabay')


class EngineSettingsDisplayTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('grace', password='pass')
        self.client.login(username='grace', password='pass')

    def test_engine_flags_shown(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        self.assertContains(resp, preferences.ENGINE_FLAGS['brave'])
        self.assertContains(resp, preferences.ENGINE_FLAGS['mojeek'])
        self.assertContains(resp, preferences.ENGINE_FLAGS['marginalia'])
        self.assertContains(resp, preferences.ENGINE_FLAGS['staan'])

    def test_brave_row_notes_autocomplete(self):
        # The Brave row tells users it powers search-bar autocomplete, so the
        # effect of turning Brave off is discoverable.
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        self.assertContains(resp, 'autocomplete')

    @override_settings(BRAVE_API_KEY='', MOJEEK_API_KEY='test-key', MARGINALIA_API_KEY='test-key')
    def test_engine_without_api_key_is_disabled(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        content = resp.content.decode()
        self.assertIn('disabled', _provider_input(content, 'web', 'brave'))
        self.assertNotIn('disabled', _provider_input(content, 'web', 'mojeek'))
        # ...and in every other search type Brave appears under, too.
        self.assertIn('disabled', _provider_input(content, 'images', 'brave'))

    @override_settings(BRAVE_API_KEY='test-key', MOJEEK_API_KEY='test-key',
                       MARGINALIA_API_KEY='test-key', STAAN_API_KEY='test-key')
    def test_engine_with_api_key_is_not_disabled(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        content = resp.content.decode()
        for key in ('brave', 'mojeek', 'marginalia', 'staan'):
            self.assertNotIn('disabled', _provider_input(content, 'web', key))

    def test_each_search_type_has_its_own_form(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        content = resp.content.decode()
        for search_type, providers in preferences.TYPE_PROVIDERS.items():
            form = _type_form(content, search_type)
            for provider in providers:
                self.assertIn(f'name="provider_{provider}"', form)

    def test_engines_are_offered_per_search_type(self):
        # Brave backs four search types, so it carries four independent toggles;
        # the web-only engines appear under Web alone.
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        content = resp.content.decode()
        self.assertEqual(len(re.findall('name="provider_brave"', content)), 4)
        self.assertEqual(len(re.findall('name="provider_mojeek"', content)), 1)
        self.assertNotIn('provider_mojeek', _type_form(content, 'images'))

    def test_web_group_lists_knowledge_sources(self):
        # "For web I want Wikipedia, Brave and Staan" — the knowledge cards and
        # the weather answer are configured with the web engines, not apart.
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        web = _type_group(resp, 'web')
        self.assertEqual([p['key'] for p in web['engines']],
                         ['brave', 'mojeek', 'marginalia', 'staan'])
        self.assertEqual([p['key'] for p in web['extras']],
                         ['wikipedia', 'thetvdb', 'tripadvisor', 'stackexchange', 'weather'])

    def test_media_provider_description_is_per_search_type(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        images = _type_group(resp, 'images')
        brave = next(p for p in images['engines'] if p['key'] == 'brave')
        self.assertEqual(str(brave['desc']), "Brave's own image search.")

    def test_empty_search_type_is_flagged(self):
        _set_prefs(self.client, disabled_providers={'images': ['brave', 'pixabay']})
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        self.assertEqual(_type_group(resp, 'images')['enabled_count'], 0)
        self.assertContains(resp, 'Nothing is enabled for Images')

    def test_data_source_flags_shown(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        for key in preferences.SOURCE_KEYS:
            self.assertContains(resp, preferences.SOURCE_FLAGS[key])

    def test_open_source_badge_shown_for_open_source_projects(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        # Each badge mentions "Open source" twice: the visible label and the title attribute.
        self.assertContains(resp, 'Open source', count=len(preferences.OPEN_SOURCE) * 2)

    def test_paid_badge_shown_for_commercial_apis(self):
        # Brave carries a toggle under four search types, so its badge shows on
        # each of them; the keyless/free providers never carry one.
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        content = resp.content.decode()
        for search_type, provider in (('web', 'brave'), ('web', 'mojeek'), ('web', 'staan'),
                                      ('images', 'brave'), ('news', 'worldnews')):
            self.assertIn('Paid', _provider_row(content, search_type, provider),
                          f'{search_type}/{provider}')

    def test_paid_badge_not_shown_for_free_providers(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        content = resp.content.decode()
        for search_type, provider in (('web', 'marginalia'), ('web', 'wikipedia'),
                                      ('web', 'thetvdb'), ('web', 'tripadvisor'),
                                      ('web', 'stackexchange'), ('web', 'weather'),
                                      ('images', 'pixabay'), ('videos', 'sepia'),
                                      ('maps', 'openstreetmap'), ('translate', 'translate')):
            self.assertNotIn('Paid', _provider_row(content, search_type, provider),
                             f'{search_type}/{provider}')

    def test_paid_badge_explains_itself(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        self.assertContains(resp, 'Commercial API, billed beyond any free allowance')

    @override_settings(PAID_PROVIDERS=['staan', 'pixabay'])
    def test_paid_set_follows_the_env_var(self):
        # A deployment on different plans relabels the badge without a code change.
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        content = resp.content.decode()
        self.assertIn('Paid', _provider_row(content, 'web', 'staan'))
        self.assertIn('Paid', _provider_row(content, 'images', 'pixabay'))
        self.assertNotIn('Paid', _provider_row(content, 'web', 'brave'))
        self.assertNotIn('Paid', _provider_row(content, 'news', 'worldnews'))

    @override_settings(PAID_PROVIDERS=['none'])
    def test_paid_badge_can_be_switched_off_entirely(self):
        # No configured name is a provider key, so nothing is badged.
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        self.assertNotContains(resp, 'Paid')

    def test_open_source_badge_not_shown_for_closed_source_projects(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        content = resp.content.decode()
        for key, label, _desc in preferences.SOURCES:
            if key in preferences.OPEN_SOURCE:
                continue
            row = re.search(rf'{re.escape(escape(str(label)))}.*?</strong>', content, re.DOTALL).group(0)
            self.assertNotIn('Open source', row)

    def test_data_source_labels_use_provider_names(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        self.assertContains(resp, 'TheTVDB')
        self.assertContains(resp, 'TripAdvisor')
        self.assertContains(resp, 'Open-Meteo')
        self.assertContains(resp, 'Pixabay')

    @override_settings(THETVDB_API_KEY='', TRIPADVISOR_API_KEY='test-key', PIXABAY_API_KEY='test-key')
    def test_data_source_without_api_key_is_disabled(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        content = resp.content.decode()
        self.assertIn('disabled', _provider_input(content, 'web', 'thetvdb'))
        self.assertNotIn('disabled', _provider_input(content, 'web', 'tripadvisor'))
        self.assertContains(resp, 'API key not configured')

    @override_settings(
        THETVDB_API_KEY='test-key',
        TRIPADVISOR_API_KEY='test-key', PIXABAY_API_KEY='test-key',
    )
    def test_data_source_with_api_key_is_not_disabled(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        content = resp.content.decode()
        self.assertNotIn('disabled', _provider_input(content, 'web', 'thetvdb'))
        self.assertNotIn('disabled', _provider_input(content, 'web', 'tripadvisor'))
        self.assertNotIn('disabled', _provider_input(content, 'images', 'pixabay'))

    @override_settings(LIBRETRANSLATE_URL='')
    def test_translate_source_disabled_without_libretranslate_url(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        content = resp.content.decode()
        self.assertIn('disabled', _provider_input(content, 'translate', 'translate'))
        self.assertContains(resp, 'Translation server not configured')

    @override_settings(LIBRETRANSLATE_URL='http://localhost:5000')
    def test_translate_source_enabled_with_libretranslate_url(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        content = resp.content.decode()
        self.assertNotIn('disabled', _provider_input(content, 'translate', 'translate'))

    def test_translate_origin_flag_defaults_to_french(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        self.assertContains(resp, '🇫🇷')

    @override_settings(LIBRETRANSLATE_ORIGIN_COUNTRY='de')
    def test_translate_origin_flag_follows_env_var(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        self.assertContains(resp, '🇩🇪')

    @override_settings(LIBRETRANSLATE_URL='http://localhost:5000')
    def test_disable_translate_source(self):
        self.client.post(reverse('search:settings'), _save_providers('translate'))
        self.assertFalse(preferences.is_enabled(_get_prefs(self.client), 'translate', 'translate'))

    @override_settings(THETVDB_API_KEY='', TRIPADVISOR_API_KEY='', PIXABAY_API_KEY='')
    def test_keyless_sources_never_disabled(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        content = resp.content.decode()
        for key in ('wikipedia', 'stackexchange', 'weather'):
            self.assertNotIn('disabled', _provider_input(content, 'web', key))


class BuildPlaceTests(TestCase):
    def test_full_result(self):
        raw = {
            'lat': '48.8534951', 'lon': '2.3483915',
            'display_name': 'Paris, Île-de-France, France',
            'name': 'Paris', 'class': 'place', 'type': 'city',
            'addresstype': 'city', 'importance': 0.95,
            'boundingbox': ['48.81', '48.90', '2.22', '2.46'],
        }
        p = _build_place(raw)
        self.assertEqual(p['name'], 'Paris')
        self.assertAlmostEqual(p['lat'], 48.8534951, places=5)
        self.assertAlmostEqual(p['lon'], 2.3483915, places=5)
        self.assertEqual(p['type'], 'city')
        self.assertIn('export/embed.html', p['embed_url'])
        self.assertIn('marker=', p['embed_url'])
        self.assertIn('mlat=48.853495', p['osm_url'])
        self.assertIn('openstreetmap.org/directions?to=', p['directions_url'])
        self.assertIn('48.853495', p['directions_url'])
        self.assertTrue(p['geo_uri'].startswith('geo:'))
        self.assertIn('48.853495', p['geo_uri'])

    def test_invalid_coords_returns_none(self):
        self.assertIsNone(_build_place({'display_name': 'nowhere'}))

    def test_name_falls_back_to_display_name(self):
        p = _build_place({'lat': '1.0', 'lon': '2.0', 'display_name': 'Foo, Bar'})
        self.assertEqual(p['name'], 'Foo')

    def test_degenerate_bbox_is_clamped(self):
        # No bounding box → a small box is synthesised around the point.
        p = _build_place({'lat': '10.0', 'lon': '20.0', 'display_name': 'P'})
        self.assertIn('bbox=', p['embed_url'])
        self.assertIn('export/embed.html', p['embed_url'])

    def test_place_from_coords(self):
        p = place_from_coords(48.8584, 2.2945, name='Eiffel Tower')
        self.assertEqual(p['name'], 'Eiffel Tower')
        self.assertEqual(p['lat'], 48.8584)
        self.assertEqual(p['lon'], 2.2945)
        self.assertIn('export/embed.html', p['embed_url'])
        self.assertIn('openstreetmap.org/directions?to=48.8584%2C2.2945', p['directions_url'])
        self.assertEqual(p['geo_uri'], 'geo:48.8584,2.2945?q=48.8584,2.2945(Eiffel%20Tower)')


class LooksLikePlaceTests(TestCase):
    def test_street_address(self):
        self.assertTrue(looks_like_place('10 Downing Street'))
        self.assertTrue(looks_like_place('221B Baker St'))

    def test_map_phrases(self):
        self.assertTrue(looks_like_place('map of berlin'))
        self.assertTrue(looks_like_place('directions to the airport'))
        self.assertTrue(looks_like_place('where is the colosseum'))

    def test_plain_query_is_not_a_place(self):
        self.assertFalse(looks_like_place('python tutorial'))
        self.assertFalse(looks_like_place('how to bake bread'))

    def test_street_word_without_number_is_not_an_address(self):
        # "street" appears but there's no house number → not an address.
        self.assertFalse(looks_like_place('wall street journal'))

    def test_wiki_place_description(self):
        self.assertTrue(looks_like_place('lyon', {'description': 'City in France'}, []))

    def test_map_domain_in_results(self):
        self.assertTrue(looks_like_place(
            'somewhere', None,
            [{'url': 'https://www.openstreetmap.org/relation/7444'}],
        ))

    def test_wikidata_place_tag_fires_in_any_language(self):
        # Polish description, no keyword list covers it, but the subject's
        # Wikidata classes say it's a place.
        with patch('cards.wikidata.entity_tags', return_value=frozenset({'place'})):
            self.assertTrue(looks_like_place(
                'paryż', {'wikibase_item': 'Q90',
                          'description': 'stolica i największe miasto francji'}, [],
            ))

    def test_wikidata_movie_tag_blocks_place_description(self):
        # A film about a city is a film: the tag beats the description keyword.
        with patch('cards.wikidata.entity_tags', return_value=frozenset({'movie_tv'})):
            self.assertFalse(looks_like_place(
                'paris texas', {'wikibase_item': 'Q1187053',
                                'description': '1984 film set around a city in Texas'}, [],
            ))


class FetchGeocodeTests(TestCase):
    @patch('maps.services._nominatim_request')
    def test_parses_results(self, mock_req):
        mock_req.return_value = [{
            'lat': '51.5034', 'lon': '-0.1276',
            'display_name': '10 Downing Street, London, UK',
            'name': 'Downing Street', 'type': 'house', 'class': 'place',
            'boundingbox': ['51.50', '51.51', '-0.13', '-0.12'],
        }]
        places = fetch_geocode('10 downing street', limit=1)
        self.assertEqual(len(places), 1)
        self.assertEqual(places[0]['lat'], 51.5034)
        self.assertEqual(places[0]['name'], 'Downing Street')
        mock_req.assert_called_once()

    @patch('maps.services._nominatim_request', return_value=None)
    def test_request_failure_returns_empty(self, mock_req):
        self.assertEqual(fetch_geocode('anywhere'), [])

    def test_empty_query_skips_request(self):
        with patch('maps.services._nominatim_request') as mock_req:
            self.assertEqual(fetch_geocode('   '), [])
            mock_req.assert_not_called()


class BangResolveTests(TestCase):
    def test_tab_bang_redirects_to_internal_tab(self):
        url = bangs.resolve('!news ukraine')
        self.assertEqual(url, '/search/?q=ukraine&tab=news')

    def test_short_tab_bangs(self):
        self.assertEqual(bangs.resolve('!i cats'), '/search/?q=cats&tab=images')
        self.assertEqual(bangs.resolve('!n kyiv'), '/search/?q=kyiv&tab=news')
        self.assertEqual(bangs.resolve('!v lectures'), '/search/?q=lectures&tab=videos')
        self.assertEqual(bangs.resolve('!m berlin'), '/search/?q=berlin&tab=maps')

    def test_tab_bang_trailing(self):
        url = bangs.resolve('cat pictures !images')
        self.assertEqual(url, '/search/?q=cat+pictures&tab=images')

    def test_tab_bang_case_insensitive(self):
        url = bangs.resolve('!News kyiv')
        self.assertEqual(url, '/search/?q=kyiv&tab=news')

    def test_tab_bang_takes_precedence_over_external(self):
        # 'web' exists in bangs.json but should resolve to the local tab.
        url = bangs.resolve('!web hello')
        self.assertEqual(url, '/search/?q=hello&tab=web')

    def test_tab_bang_alone(self):
        url = bangs.resolve('!maps')
        self.assertEqual(url, '/search/?q=&tab=maps')

    def test_translate_bang_redirects_to_internal_tab(self):
        url = bangs.resolve('!translate bonjour')
        self.assertEqual(url, '/search/?q=bonjour&tab=translate')

    def test_tab_bang_active_when_tab_available(self):
        # The tab is in the user's available set → routes there as usual.
        url = bangs.resolve('!news kyiv', available_tabs={'web', 'news'})
        self.assertEqual(url, '/search/?q=kyiv&tab=news')

    def test_tab_bang_deactivated_when_tab_unavailable(self):
        # News isn't available (e.g. a Mojeek user) → the bang degrades to a
        # plain search on the fallback tab instead of routing to a hidden tab.
        url = bangs.resolve('!news kyiv', available_tabs={'web', 'maps'})
        self.assertEqual(url, '/search/?q=kyiv&tab=web')

    def test_maps_bang_deactivated_when_maps_disabled(self):
        url = bangs.resolve('!m berlin', available_tabs={'web', 'images'})
        self.assertEqual(url, '/search/?q=berlin&tab=web')

    def test_fallback_tab_prefers_web_then_display_order(self):
        self.assertEqual(bangs.fallback_tab({'web', 'images', 'maps'}), 'web')
        self.assertEqual(bangs.fallback_tab({'images', 'maps'}), 'images')
        self.assertEqual(bangs.fallback_tab({'maps'}), 'maps')
        self.assertEqual(bangs.fallback_tab(set()), 'web')

    def test_external_bang_unchanged(self):
        with patch.object(bangs, '_bangs', {'w': 'https://en.wikipedia.org/wiki/Special:Search?search={{{s}}}'}):
            url = bangs.resolve('!w einstein')
            self.assertEqual(url, 'https://en.wikipedia.org/wiki/Special:Search?search=einstein')

    def test_no_bang_returns_none(self):
        self.assertIsNone(bangs.resolve('plain query'))

    def test_unknown_bang_returns_none(self):
        with patch.object(bangs, '_bangs', {}):
            self.assertIsNone(bangs.resolve('!doesnotexist foo'))

    def test_lucky_terms_leading(self):
        self.assertEqual(bangs.lucky_terms('! python tutorial'), 'python tutorial')

    def test_lucky_terms_trailing(self):
        self.assertEqual(bangs.lucky_terms('python tutorial !'), 'python tutorial')

    def test_lucky_terms_empty_when_alone(self):
        self.assertIsNone(bangs.lucky_terms('!'))

    def test_lucky_terms_ignores_attached_bang(self):
        # !news is a tab bang, not a lucky bang.
        self.assertIsNone(bangs.lucky_terms('!news kyiv'))

    def test_lucky_terms_no_bang(self):
        self.assertIsNone(bangs.lucky_terms('plain query'))


class BangViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('eve', password='pass')
        self.client.login(username='eve', password='pass')

    def test_tab_bang_redirects_to_local_tab(self):
        resp = self.client.get(reverse('search:results') + '?q=!news+ukraine')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/search/?q=ukraine&tab=news')

    def test_short_tab_bang_redirects(self):
        resp = self.client.get(reverse('search:results') + '?q=!i+cats')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/search/?q=cats&tab=images')

    @patch('search.views.fetch_web')
    def test_lucky_bang_redirects_to_first_result(self, mock_web):
        mock_web.return_value = (
            [{'url': 'https://example.com/top', 'title': 'Top'}],
            None,
        )
        resp = self.client.get(reverse('search:results') + '?q=!+python+docs')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], 'https://example.com/top')
        # Search was run with the bang stripped out.
        self.assertEqual(mock_web.call_args[0][0], 'python docs')

    @patch('search.views.fetch_web')
    def test_lucky_bang_no_results_falls_through(self, mock_web):
        mock_web.return_value = ([], None)
        resp = self.client.get(reverse('search:results') + '?q=!+zzznoresults')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['query'], 'zzznoresults')


class CustomBangTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('fred', password='pass')

    def test_user_bang_overrides_generic(self):
        from search.models import CustomBang
        CustomBang.objects.create(
            user=self.user, trigger='w',
            url_template='https://my-wiki.example.com/q?s={{{s}}}',
        )
        with patch.object(bangs, '_bangs', {'w': 'https://en.wikipedia.org/wiki/Special:Search?search={{{s}}}'}):
            url = bangs.resolve('!w einstein', user=self.user)
        self.assertEqual(url, 'https://my-wiki.example.com/q?s=einstein')

    def test_user_bang_resolves_when_no_generic_match(self):
        from search.models import CustomBang
        CustomBang.objects.create(
            user=self.user, trigger='myb',
            url_template='https://example.com/?q={{{s}}}',
        )
        with patch.object(bangs, '_bangs', {}):
            url = bangs.resolve('!myb hello', user=self.user)
        self.assertEqual(url, 'https://example.com/?q=hello')

    def test_anonymous_user_falls_back_to_generic(self):
        with patch.object(bangs, '_bangs', {'w': 'https://en.wikipedia.org/wiki/Special:Search?search={{{s}}}'}):
            url = bangs.resolve('!w einstein', user=None)
        self.assertEqual(url, 'https://en.wikipedia.org/wiki/Special:Search?search=einstein')

    def test_tab_bang_not_overridable(self):
        from search.models import CustomBang
        CustomBang.objects.create(
            user=self.user, trigger='news',
            url_template='https://example.com/?q={{{s}}}',
        )
        # !news is a tab bang and should always go to the local news tab.
        url = bangs.resolve('!news ukraine', user=self.user)
        self.assertEqual(url, '/search/?q=ukraine&tab=news')


class CustomBangSettingsViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('gail', password='pass')
        self.client.login(username='gail', password='pass')

    def test_add_custom_bang(self):
        from search.models import CustomBang
        resp = self.client.post(reverse('search:settings'), {
            'setting': 'add_custom_bang',
            'trigger': 'gh',
            'url_template': 'https://github.com/search?q={{{s}}}',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(CustomBang.objects.filter(user=self.user, trigger='gh').exists())

    def test_trigger_normalized_lowercase_and_stripped(self):
        from search.models import CustomBang
        self.client.post(reverse('search:settings'), {
            'setting': 'add_custom_bang',
            'trigger': '!GH',
            'url_template': 'https://github.com/search?q={{{s}}}',
        })
        self.assertTrue(CustomBang.objects.filter(user=self.user, trigger='gh').exists())

    def test_invalid_trigger_rejected(self):
        from search.models import CustomBang
        resp = self.client.post(reverse('search:settings'), {
            'setting': 'add_custom_bang',
            'trigger': 'has space',
            'url_template': 'https://github.com/search?q={{{s}}}',
        }, follow=True)
        self.assertFalse(CustomBang.objects.filter(user=self.user).exists())
        self.assertContains(resp, 'Trigger must be')

    def test_url_template_must_contain_placeholder(self):
        from search.models import CustomBang
        resp = self.client.post(reverse('search:settings'), {
            'setting': 'add_custom_bang',
            'trigger': 'foo',
            'url_template': 'https://example.com/',
        }, follow=True)
        self.assertFalse(CustomBang.objects.filter(user=self.user).exists())
        self.assertContains(resp, 'placeholder')

    def test_url_template_must_be_http(self):
        from search.models import CustomBang
        resp = self.client.post(reverse('search:settings'), {
            'setting': 'add_custom_bang',
            'trigger': 'foo',
            'url_template': 'javascript:alert(1){{{s}}}',
        }, follow=True)
        self.assertFalse(CustomBang.objects.filter(user=self.user).exists())
        self.assertContains(resp, 'http')

    def test_update_existing_bang(self):
        from search.models import CustomBang
        CustomBang.objects.create(user=self.user, trigger='gh', url_template='https://old.example.com/{{{s}}}')
        self.client.post(reverse('search:settings'), {
            'setting': 'add_custom_bang',
            'trigger': 'gh',
            'url_template': 'https://github.com/search?q={{{s}}}',
        })
        bang = CustomBang.objects.get(user=self.user, trigger='gh')
        self.assertEqual(bang.url_template, 'https://github.com/search?q={{{s}}}')

    def test_delete_custom_bang(self):
        from search.models import CustomBang
        bang = CustomBang.objects.create(user=self.user, trigger='gh', url_template='https://github.com/search?q={{{s}}}')
        self.client.post(reverse('search:settings'), {
            'setting': 'delete_custom_bang',
            'bang_id': bang.id,
        })
        self.assertFalse(CustomBang.objects.filter(pk=bang.id).exists())

    def test_cannot_delete_other_users_bang(self):
        from search.models import CustomBang
        other = User.objects.create_user('hank', password='pass')
        bang = CustomBang.objects.create(user=other, trigger='gh', url_template='https://github.com/search?q={{{s}}}')
        self.client.post(reverse('search:settings'), {
            'setting': 'delete_custom_bang',
            'bang_id': bang.id,
        })
        self.assertTrue(CustomBang.objects.filter(pk=bang.id).exists())

    def test_settings_lists_user_bangs(self):
        from search.models import CustomBang
        CustomBang.objects.create(user=self.user, trigger='gh', url_template='https://github.com/search?q={{{s}}}')
        resp = self.client.get(reverse('search:settings'))
        self.assertContains(resp, '!gh')
        self.assertContains(resp, 'github.com')

    def test_custom_bangs_json_endpoint(self):
        from search.models import CustomBang
        CustomBang.objects.create(user=self.user, trigger='gh', url_template='https://github.com/search?q={{{s}}}')
        resp = self.client.get(reverse('search:custom_bangs_json'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'gh': 'https://github.com/search?q={{{s}}}'})

    def test_user_bangs_injected_into_results_page(self):
        from search.models import CustomBang
        CustomBang.objects.create(user=self.user, trigger='gh', url_template='https://github.com/search?q={{{s}}}')
        resp = self.client.get(reverse('search:results'))
        # Rendered as an inert JSON data block ({% json_script %}), not an
        # executable inline script.
        self.assertContains(resp, '<script id="user-bangs" type="application/json">')
        self.assertContains(resp, '"gh": "https://github.com/search?q={{{s}}}"')

    def test_script_breakout_in_bang_url_rejected_on_create(self):
        from search.models import CustomBang
        resp = self.client.post(reverse('search:settings'), {
            'setting': 'add_custom_bang',
            'trigger': 'evil',
            'url_template': 'https://example.com/</script><script>alert(1)</script>?q={{{s}}}',
        }, follow=True)
        self.assertFalse(CustomBang.objects.filter(user=self.user).exists())
        self.assertContains(resp, 'valid web address')

    def test_stored_breakout_never_reaches_the_page_raw(self):
        # Defence-in-depth: even a payload already in the database (predating
        # the validators, or written around them) is neutralised by
        # {% json_script %}'s escaping when the page renders.
        from search.models import CustomBang
        CustomBang.objects.create(
            user=self.user, trigger='evil',
            url_template='https://example.com/</script><script>alert(1)</script>?q={{{s}}}',
        )
        resp = self.client.get(reverse('search:results'))
        self.assertNotContains(resp, '</script><script>alert(1)')

    @patch('search.views.fetch_web')
    def test_custom_bang_redirects_from_results_view(self, mock_web):
        from search.models import CustomBang
        CustomBang.objects.create(user=self.user, trigger='myb', url_template='https://my.example.com/?q={{{s}}}')
        resp = self.client.get(reverse('search:results') + '?q=!myb+hello')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], 'https://my.example.com/?q=hello')
        mock_web.assert_not_called()


class BlockedSiteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('iris', password='pass')

    def test_normalize_domain(self):
        from search.services import normalize_domain
        self.assertEqual(normalize_domain('example.com'), 'example.com')
        self.assertEqual(normalize_domain('Example.COM'), 'example.com')
        self.assertEqual(normalize_domain('https://www.example.com/foo/bar'), 'example.com')
        self.assertEqual(normalize_domain('http://blog.example.com:8080/x'), 'blog.example.com')
        self.assertEqual(normalize_domain(''), '')
        self.assertEqual(normalize_domain('   '), '')

    def test_filter_blocked_matches_exact(self):
        from search.services import filter_blocked
        results = [
            {'url': 'https://example.com/x', 'title': 'A'},
            {'url': 'https://other.com/x', 'title': 'B'},
        ]
        out = filter_blocked(results, {'example.com'})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['url'], 'https://other.com/x')

    def test_filter_blocked_matches_subdomain(self):
        from search.services import filter_blocked
        results = [
            {'url': 'https://www.example.com/x'},
            {'url': 'https://blog.example.com/y'},
            {'url': 'https://other.com/z'},
        ]
        out = filter_blocked(results, {'example.com'})
        self.assertEqual([r['url'] for r in out], ['https://other.com/z'])

    def test_filter_blocked_empty_set_returns_all(self):
        from search.services import filter_blocked
        results = [{'url': 'https://example.com/x'}]
        self.assertEqual(filter_blocked(results, set()), results)

    def test_blocked_domains_for_anonymous(self):
        from django.contrib.auth.models import AnonymousUser

        from search.services import blocked_domains_for
        self.assertEqual(blocked_domains_for(AnonymousUser()), set())

    def test_blocked_domains_for_user(self):
        from search.models import BlockedSite
        from search.services import blocked_domains_for
        BlockedSite.objects.create(user=self.user, domain='foo.com')
        BlockedSite.objects.create(user=self.user, domain='bar.com')
        self.assertEqual(blocked_domains_for(self.user), {'foo.com', 'bar.com'})


class BlockedSiteSettingsViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('jay', password='pass')
        self.client.login(username='jay', password='pass')

    def test_add_blocked_site_via_settings(self):
        from search.models import BlockedSite
        resp = self.client.post(reverse('search:settings'), {
            'setting': 'add_blocked_site',
            'domain': 'pinterest.com',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(BlockedSite.objects.filter(user=self.user, domain='pinterest.com').exists())

    def test_add_normalizes_url_to_domain(self):
        from search.models import BlockedSite
        self.client.post(reverse('search:settings'), {
            'setting': 'add_blocked_site',
            'domain': 'https://www.Pinterest.com/foo/bar',
        })
        self.assertTrue(BlockedSite.objects.filter(user=self.user, domain='pinterest.com').exists())

    def test_invalid_domain_rejected(self):
        from search.models import BlockedSite
        resp = self.client.post(reverse('search:settings'), {
            'setting': 'add_blocked_site',
            'domain': 'not-a-domain',
        }, follow=True)
        self.assertFalse(BlockedSite.objects.filter(user=self.user).exists())
        self.assertContains(resp, 'valid domain')

    def test_delete_blocked_site(self):
        from search.models import BlockedSite
        site = BlockedSite.objects.create(user=self.user, domain='pinterest.com')
        self.client.post(reverse('search:settings'), {
            'setting': 'delete_blocked_site',
            'site_id': site.id,
        })
        self.assertFalse(BlockedSite.objects.filter(pk=site.id).exists())

    def test_block_site_quick_action(self):
        from search.models import BlockedSite
        resp = self.client.post(reverse('search:block_site'), {
            'domain': 'https://www.spam.example.com/abc',
            'next': '/search/?q=foo',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/search/?q=foo')
        self.assertTrue(BlockedSite.objects.filter(user=self.user, domain='spam.example.com').exists())

    def test_block_site_idempotent(self):
        from search.models import BlockedSite
        BlockedSite.objects.create(user=self.user, domain='spam.com')
        resp = self.client.post(reverse('search:block_site'), {'domain': 'spam.com'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(BlockedSite.objects.filter(user=self.user, domain='spam.com').count(), 1)

    def test_block_site_rejects_external_next_url(self):
        resp = self.client.post(reverse('search:block_site'), {
            'domain': 'spam.com',
            'next': 'https://evil.example.com/',
        })
        self.assertEqual(resp['Location'], '/')

    def test_block_site_rejects_invalid(self):
        from search.models import BlockedSite
        self.client.post(reverse('search:block_site'), {'domain': 'not a domain'})
        self.assertFalse(BlockedSite.objects.filter(user=self.user).exists())

    def test_block_site_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse('search:block_site'), {'domain': 'spam.com'})
        self.assertEqual(resp.status_code, 302)
        self.assertIn('login', resp['Location'])

    def test_block_site_requires_post(self):
        resp = self.client.get(reverse('search:block_site') + '?domain=spam.com')
        self.assertEqual(resp.status_code, 405)

    @patch('search.views.fetch_web')
    def test_blocked_sites_filtered_from_web_results(self, mock_web):
        from search.models import BlockedSite
        BlockedSite.objects.create(user=self.user, domain='blocked.example.com')
        mock_web.return_value = ([
            {'url': 'https://blocked.example.com/a', 'title': 'Blocked', 'description': '', 'display_url': 'blocked.example.com', 'source': 'brave'},
            {'url': 'https://kept.example.com/b', 'title': 'Kept', 'description': '', 'display_url': 'kept.example.com', 'source': 'brave'},
        ], '')
        resp = self.client.get(reverse('search:results') + '?q=foo')
        urls = [r['url'] for r in resp.context['web_results']]
        self.assertEqual(urls, ['https://kept.example.com/b'])

    def test_settings_lists_blocked_sites(self):
        from search.models import BlockedSite
        BlockedSite.objects.create(user=self.user, domain='pinterest.com')
        resp = self.client.get(reverse('search:settings'))
        self.assertContains(resp, 'pinterest.com')


class ResultActionsMenuTests(TestCase):
    """The per-result kebab menu groups a Wayback-Machine "Cached" link with the
    block-site action."""

    def setUp(self):
        self.user = User.objects.create_user('mara', password='pass')
        self.client.login(username='mara', password='pass')

    @patch('search.views.fetch_web')
    def test_menu_has_cached_link_and_block_action(self, mock_web):
        mock_web.return_value = ([
            {'url': 'https://example.com/page', 'title': 'Example', 'description': '',
             'display_url': 'example.com', 'source': 'brave'},
        ], '')
        resp = self.client.get(reverse('search:results') + '?q=foo')
        # Both actions live inside one <details> disclosure menu (no JS).
        self.assertContains(resp, '<details')
        # "Cached" links to the Wayback Machine snapshot of the result URL.
        self.assertContains(resp, 'https://web.archive.org/web/https://example.com/page')
        # The block-site action is still offered (now inside the menu).
        self.assertContains(resp, reverse('search:block_site'))
        self.assertContains(resp, 'Block site')


class SettingsActivePaneTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('kira', password='pass')
        self.client.login(username='kira', password='pass')

    def test_default_pane_is_general(self):
        resp = self.client.get(reverse('search:settings'))
        self.assertEqual(resp.context['active_pane'], 'general')
        self.assertContains(resp, 'id="settings-general"    name="settings-tab" checked')

    def test_pane_query_param_is_ignored(self):
        # Panes are addressed by path (/settings/<slug>/); a ?pane= key is not a
        # way in, whatever it names.
        for value in ('bangs', 'evil'):
            resp = self.client.get(reverse('search:settings') + f'?pane={value}')
            self.assertEqual(resp.status_code, 200, value)
            self.assertEqual(resp.context['active_pane'], 'general', value)

    def test_post_preserves_active_pane(self):
        resp = self.client.post(reverse('search:settings'), {
            'setting': 'add_blocked_site',
            'pane': 'blocked',
            'domain': 'pinterest.com',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('search:settings_pane', kwargs={'pane': 'blocked'}))

    def test_post_with_unknown_pane_drops_it(self):
        resp = self.client.post(reverse('search:settings'), {
            'setting': 'safe_search',
            'safe_search': 'on',
            'pane': 'evil',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('search:settings'))

    def test_pane_path_url_selects_pane(self):
        # /settings/<slug>/ renders the matching pane. The slug differs from the
        # internal key for a couple of panes (shortcuts->bangs, engines->engine).
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'shortcuts'}))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_pane'], 'bangs')
        self.assertContains(resp, 'id="settings-bangs"      name="settings-tab" checked')

    def test_pane_path_urls_resolve_for_every_pane(self):
        from search.views import _PANE_SLUGS
        for pane, slug in _PANE_SLUGS.items():
            resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': slug}))
            self.assertEqual(resp.status_code, 200, slug)
            self.assertEqual(resp.context['active_pane'], pane)

    def test_unknown_pane_slug_404s(self):
        # The slug set is constrained in the URLconf, so a bad slug never reaches
        # the view (and never shadows the sibling settings/ endpoints).
        self.assertEqual(self.client.get('/settings/nope/').status_code, 404)


class SearchLanguageSettingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('lang', password='pass')
        self.client.login(username='lang', password='pass')

    def test_default_is_auto(self):
        resp = self.client.get(reverse('search:settings'))
        self.assertEqual(resp.context['search_lang'], 'auto')

    def test_default_uses_browser_lang_when_supported(self):
        # No preferences cookie yet, French browser -> default is 'fr', not 'auto'.
        resp = self.client.get(reverse('search:settings'), HTTP_ACCEPT_LANGUAGE='fr-FR,fr;q=0.9')
        self.assertEqual(resp.context['search_lang'], 'fr')

    def test_default_stays_auto_for_unsupported_browser_lang(self):
        resp = self.client.get(reverse('search:settings'), HTTP_ACCEPT_LANGUAGE='ja-JP,ja;q=0.9')
        self.assertEqual(resp.context['search_lang'], 'auto')

    def test_saved_pref_overrides_browser_lang(self):
        _set_prefs(self.client, search_lang='de')
        resp = self.client.get(reverse('search:settings'), HTTP_ACCEPT_LANGUAGE='fr-FR,fr;q=0.9')
        self.assertEqual(resp.context['search_lang'], 'de')

    @patch('search.views._detect_language')
    def test_results_use_browser_lang_default_without_cookie(self, mock_detect):
        resp = self.client.get(
            reverse('search:results') + '?q=test', HTTP_ACCEPT_LANGUAGE='fr-FR,fr;q=0.9',
        )
        mock_detect.assert_not_called()
        self.assertEqual(resp.context['qs_lang'], 'fr')

    def test_save_search_lang(self):
        self.client.post(reverse('search:settings'), {
            'setting': 'search_lang', 'search_lang': 'fr', 'pane': 'general',
        })
        self.assertEqual(_get_prefs(self.client)['search_lang'], 'fr')

    def test_invalid_search_lang_defaults_to_auto(self):
        self.client.post(reverse('search:settings'), {
            'setting': 'search_lang', 'search_lang': 'klingon',
        })
        self.assertEqual(_get_prefs(self.client)['search_lang'], 'auto')

    @patch('search.views._detect_language')
    def test_enforced_lang_skips_detection(self, mock_detect):
        _set_prefs(self.client, search_lang='es')
        resp = self.client.get(reverse('search:results') + '?q=test')
        mock_detect.assert_not_called()
        self.assertEqual(resp.context['qs_lang'], 'es')

    @patch('search.views._detect_language', return_value='nl')
    def test_auto_pref_consults_detection(self, mock_detect):
        _set_prefs(self.client, search_lang='auto')
        resp = self.client.get(reverse('search:results') + '?q=test')
        self.assertTrue(mock_detect.called)
        self.assertEqual(resp.context['qs_lang'], 'nl')

    @patch('search.views._detect_language', return_value='nl')
    def test_query_param_overrides_enforced_lang(self, mock_detect):
        _set_prefs(self.client, search_lang='de')
        resp = self.client.get(reverse('search:results') + '?q=test&lang=fr')
        self.assertEqual(resp.context['qs_lang'], 'fr')


class LanguageDetectionTests(TestCase):
    """Regression tests for query-based language detection.

    lingua normalises its confidence across all seven candidate languages, so a
    short but unmistakable query scores well under the absolute 0.5 threshold and
    used to fall through to the Accept-Language header, surfacing as French
    results for plainly English queries. Detection now also trusts a clear lead
    over the runner-up. See ``_detect_language_from_query``.
    """

    def test_clear_english_queries_detected(self):
        # Each of these tops out around 0.45 confidence (below 0.5) yet leads the
        # runner-up by a wide margin; before the fix they fell back to the header.
        for query in ('harry potter', 'what time is it', 'how to make pasta',
                      'python list comprehension'):
            self.assertEqual(_detect_language_from_query(query), 'en', query)

    def test_other_languages_not_regressed(self):
        cases = {
            'comment faire des pates': 'fr',
            'wie ist das wetter heute': 'de',
            'cual es el clima hoy': 'es',
            'qual e il meteo oggi': 'it',
            'beste restaurants in de buurt': 'nl',
        }
        for query, expected in cases.items():
            self.assertEqual(_detect_language_from_query(query), expected, query)

    def test_genuinely_ambiguous_query_defers(self):
        # A near-tie (e.g. "restaurants", spelled alike in several languages)
        # carries no clear signal, so detection abstains and lets the caller fall
        # back to the Accept-Language header.
        self.assertEqual(_detect_language_from_query('restaurants'), '')

    def test_english_query_beats_french_accept_language_header(self):
        # The reported bug: an English query from a French browser must search in
        # English rather than fall back to the French header.
        request = RequestFactory().get('/', HTTP_ACCEPT_LANGUAGE='fr-FR,fr;q=0.9')
        self.assertEqual(_detect_language(request, 'harry potter'), 'en')

    def test_falls_back_to_header_when_query_undetectable(self):
        # The header fallback still applies when the query itself is ambiguous.
        request = RequestFactory().get('/', HTTP_ACCEPT_LANGUAGE='fr-FR,fr;q=0.9')
        self.assertEqual(_detect_language(request, 'restaurants'), 'fr')


class InterfaceLanguageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('uilang', password='pass')
        self.client.login(username='uilang', password='pass')

    def test_default_is_auto(self):
        resp = self.client.get(reverse('search:settings'))
        self.assertEqual(resp.context['ui_lang'], 'auto')

    def test_language_pane_selectable(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'language'}))
        self.assertEqual(resp.context['active_pane'], 'language')
        self.assertContains(resp, 'id="settings-language"')

    def test_save_ui_lang_sets_language_cookie(self):
        from django.conf import settings as dj_settings
        self.client.post(reverse('search:settings'), {
            'setting': 'ui_lang', 'ui_lang': 'fr', 'pane': 'language',
        })
        self.assertEqual(self.client.cookies[dj_settings.LANGUAGE_COOKIE_NAME].value, 'fr')
        self.assertEqual(_get_prefs(self.client)['ui_lang'], 'fr')

    def test_ui_lang_actually_translates_the_page(self):
        # Regression: selecting an interface language must change the rendered
        # text, not merely set a cookie. Depends on compiled .mo catalogs.
        self.client.post(reverse('search:settings'), {
            'setting': 'ui_lang', 'ui_lang': 'fr', 'pane': 'language',
        })
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'language'}))
        self.assertContains(resp, 'lang="fr"')
        self.assertContains(resp, 'Paramètres')  # "Settings" in French

    def test_invalid_ui_lang_defaults_to_auto(self):
        self.client.post(reverse('search:settings'), {
            'setting': 'ui_lang', 'ui_lang': 'klingon', 'pane': 'language',
        })
        self.assertEqual(_get_prefs(self.client)['ui_lang'], 'auto')

    def test_auto_ui_lang_clears_language_cookie(self):
        from django.conf import settings as dj_settings
        cookie = dj_settings.LANGUAGE_COOKIE_NAME
        self.client.post(reverse('search:settings'), {
            'setting': 'ui_lang', 'ui_lang': 'fr', 'pane': 'language',
        })
        self.assertEqual(self.client.cookies[cookie].value, 'fr')
        self.client.post(reverse('search:settings'), {
            'setting': 'ui_lang', 'ui_lang': 'auto', 'pane': 'language',
        })
        # Switching back to auto clears the language cookie (falls back to the
        # browser's Accept-Language).
        self.assertEqual(self.client.cookies[cookie].value, '')


class DataSourceSettingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('cards', password='pass')
        self.client.login(username='cards', password='pass')

    def test_all_enabled_by_default(self):
        resp = self.client.get(reverse('search:settings'))
        enabled = {
            group['key']: [p['key'] for p in group['engines'] + group['extras'] if p['enabled']]
            for group in resp.context['search_types']
        }
        self.assertEqual(enabled, {
            key: list(providers) for key, providers in preferences.TYPE_PROVIDERS.items()
        })

    def test_save_disables_unchecked_sources(self):
        # Only Brave + Wikipedia + weather are checked on the Web type →
        # everything else it offers is switched off, other types untouched.
        self.client.post(reverse('search:settings'), _save_web(
            provider_brave='on', provider_wikipedia='on', provider_weather='on',
        ))
        prefs = _get_prefs(self.client)
        self.assertEqual(set(prefs['disabled_providers']['web']),
                         {'mojeek', 'marginalia', 'staan', 'thetvdb', 'tripadvisor', 'stackexchange'})
        self.assertNotIn('news', prefs['disabled_providers'])

    def test_disabled_reflected_in_context(self):
        _set_prefs(self.client, disabled_providers={
            'web': ['tripadvisor'], 'images': ['pixabay'],
        })
        resp = self.client.get(reverse('search:settings'))
        web = {p['key']: p['enabled'] for p in _type_group(resp, 'web')['extras']}
        images = {p['key']: p['enabled'] for p in _type_group(resp, 'images')['engines']}
        self.assertFalse(web['tripadvisor'])
        self.assertTrue(web['wikipedia'])
        self.assertFalse(images['pixabay'])
        self.assertTrue(images['brave'])

    @patch('search.panel.fetch_stackexchange', return_value=None)
    @patch('search.panel.fetch_tripadvisor', return_value=None)
    @patch('search.panel.fetch_thetvdb', return_value=None)
    @patch('search.views.fetch_wikipedia')
    def test_disabled_card_not_fetched(self, mock_wiki, mock_thetvdb, mock_ta, mock_se):
        _set_prefs(self.client, providers_off=['wikipedia'])
        self.client.get(reverse('search:results') + '?q=python')
        mock_wiki.assert_not_called()

    @patch('search.panel.fetch_stackexchange', return_value=None)
    @patch('search.panel.fetch_tripadvisor', return_value=None)
    @patch('search.panel.fetch_thetvdb', return_value=None)
    @patch('search.views.fetch_wikipedia', return_value=None)
    def test_enabled_card_is_fetched_by_default(self, mock_wiki, mock_thetvdb, mock_ta, mock_se):
        self.client.get(reverse('search:results') + '?q=python')
        mock_wiki.assert_called()

    @patch('search.panel.fetch_stackexchange', return_value=None)
    @patch('search.panel.fetch_tripadvisor', return_value=None)
    @patch('search.panel.fetch_thetvdb', return_value=None)
    @patch('search.views.fetch_wikipedia', return_value=None)
    def test_safe_search_passed_to_wikipedia_card(self, mock_wiki, mock_thetvdb, mock_ta, mock_se):
        # The card fetcher needs the safe-search setting so it can hide explicit
        # subjects (the search-engine fetchers already receive it).
        _set_prefs(self.client, safe_search='on')
        self.client.get(reverse('search:results') + '?q=python')
        self.assertEqual(mock_wiki.call_args.args[2], 'on')

    @patch('search.views.detect_instant_answer', return_value=None)
    def test_weather_disable_flag_passed_to_detect(self, mock_detect):
        _set_prefs(self.client, providers_off=['weather'])
        self.client.get(reverse('search:results') + '?q=hello')
        self.assertFalse(mock_detect.call_args.kwargs['weather_enabled'])

    @patch('search.views.detect_instant_answer', return_value=None)
    def test_weather_enabled_flag_by_default(self, mock_detect):
        self.client.get(reverse('search:results') + '?q=hello')
        self.assertTrue(mock_detect.call_args.kwargs['weather_enabled'])

    @patch('search.views.fetch_images', return_value=[])
    def test_pixabay_disable_flag_passed_to_fetch_images(self, mock_images):
        _set_prefs(self.client, providers_off=['pixabay'])
        self.client.get(reverse('search:results') + '?q=cats&tab=images')
        self.assertFalse(mock_images.call_args.kwargs['pixabay_enabled'])

    @patch('search.views.fetch_images', return_value=[])
    def test_pixabay_enabled_flag_by_default(self, mock_images):
        self.client.get(reverse('search:results') + '?q=cats&tab=images')
        self.assertTrue(mock_images.call_args.kwargs['pixabay_enabled'])

    @patch('search.views.fetch_news', return_value=[])
    def test_worldnews_disable_flag_passed_to_fetch_news(self, mock_news):
        _set_prefs(self.client, providers_off=['worldnews'])
        self.client.get(reverse('search:results') + '?q=kyiv&tab=news')
        self.assertFalse(mock_news.call_args.kwargs['worldnews_enabled'])

    @patch('search.views.fetch_news', return_value=[])
    def test_worldnews_enabled_flag_by_default(self, mock_news):
        self.client.get(reverse('search:results') + '?q=kyiv&tab=news')
        self.assertTrue(mock_news.call_args.kwargs['worldnews_enabled'])


class WeatherDisableHandlerTests(TestCase):
    @patch('instant.detect.weather.answer')
    def test_disabled_skips_weather_handler(self, mock_weather):
        from instant.detect import detect
        detect('weather in paris', None, weather_enabled=False)
        mock_weather.assert_not_called()

    @patch('instant.detect.weather.answer', return_value={'type': 'weather'})
    def test_enabled_consults_weather_handler(self, mock_weather):
        from instant.detect import detect
        result = detect('weather in paris', None, weather_enabled=True)
        self.assertTrue(mock_weather.called)
        self.assertEqual(result['type'], 'weather')


class PreferencesModuleTests(TestCase):
    def test_defaults(self):
        from search import preferences
        d = preferences.defaults()
        self.assertEqual(d['disabled_providers'], {})  # every provider, everywhere
        self.assertEqual(d['safe_search'], 'on')
        self.assertEqual(d['search_lang'], 'auto')
        self.assertEqual(d['theme'], 'system')
        self.assertFalse(d['open_links_new_tab'])

    def test_coerce_drops_invalid_values(self):
        from search import preferences
        p = preferences.coerce({
            'disabled_providers': {
                'web': ['mojeek', 'bogus', 'pixabay'],  # pixabay is not a web provider
                'bogus_type': ['brave'],
                'images': [],
            },
            'safe_search': 'x', 'search_lang': 'zz', 'theme': 'neon',
            'open_links_new_tab': 1,
        })
        self.assertEqual(p['disabled_providers'], {'web': ['mojeek']})
        self.assertEqual(p['safe_search'], 'on')
        self.assertEqual(p['search_lang'], 'auto')
        self.assertEqual(p['theme'], 'system')
        self.assertTrue(p['open_links_new_tab'])

    def test_coerce_orders_each_types_disabled_list(self):
        from search import preferences
        p = preferences.coerce({'disabled_providers': {'web': ['weather', 'staan', 'brave']}})
        self.assertEqual(p['disabled_providers']['web'], ['brave', 'staan', 'weather'])

    def test_coerce_ignores_unknown_keys_and_shapes(self):
        # Only a per-search-type mapping counts: a scalar, a list or a stray
        # top-level key leaves every provider enabled.
        from search import preferences
        for data in ({'disabled_providers': 'brave'},
                     {'disabled_providers': ['brave']},
                     {'search_engine': 'mojeek'},
                     {'disabled_engines': ['brave'], 'disabled_sources': ['pixabay']}):
            self.assertEqual(preferences.coerce(data)['disabled_providers'], {}, data)

    def test_coerce_drops_providers_a_type_cannot_use(self):
        from search import preferences
        p = preferences.coerce({'disabled_providers': {
            'web': ['brave', 'bogus', 'pixabay'],  # pixabay is an images source
            'maps': ['brave'],                     # maps only offers OpenStreetMap
        }})
        self.assertEqual(p['disabled_providers']['web'], ['brave'])
        self.assertNotIn('maps', p['disabled_providers'])

    def test_is_enabled_is_per_search_type(self):
        from search import preferences
        prefs = preferences.coerce({'disabled_providers': {'web': ['brave']}})
        self.assertFalse(preferences.is_enabled(prefs, 'web', 'brave'))
        self.assertTrue(preferences.is_enabled(prefs, 'images', 'brave'))
        # A provider a search type can't use is never "enabled" for it.
        self.assertFalse(preferences.is_enabled(prefs, 'images', 'mojeek'))

    def test_enabled_engines_defaults_to_the_web_type(self):
        from search import preferences
        prefs = preferences.coerce({'disabled_providers': {'web': ['brave'], 'images': ['brave']}})
        self.assertEqual(preferences.enabled_engines(prefs), ['mojeek', 'marginalia', 'staan'])
        self.assertEqual(preferences.enabled_engines(prefs, 'news'), ['brave'])
        self.assertEqual(preferences.enabled_engines(prefs, 'images'), [])

    def test_coerce_keeps_valid_theme(self):
        from search import preferences
        self.assertEqual(preferences.coerce({'theme': 'dark'})['theme'], 'dark')
        self.assertEqual(preferences.coerce({'theme': 'light'})['theme'], 'light')

    def test_scope_engines_resolves_request_scope(self):
        from search import preferences
        prefs = preferences.defaults()
        # A valid subset wins, normalised to canonical order; unknowns dropped.
        self.assertEqual(preferences.scope_engines(prefs, ['mojeek', 'brave']),
                         ['brave', 'mojeek'])
        self.assertEqual(preferences.scope_engines(prefs, ['brave', 'bogus']), ['brave'])

    def test_scope_engines_falls_back_to_enabled(self):
        from search import preferences
        prefs = preferences.coerce({'disabled_providers': {'web': ['brave']}})
        # Empty / all-invalid scope → the saved enabled engines.
        self.assertEqual(preferences.scope_engines(prefs, []), ['mojeek', 'marginalia', 'staan'])
        self.assertEqual(preferences.scope_engines(prefs, ['bogus']),
                         ['mojeek', 'marginalia', 'staan'])
        self.assertEqual(preferences.scope_engines(prefs, None),
                         ['mojeek', 'marginalia', 'staan'])

    def test_scope_view_flags_paid_providers(self):
        from search import preferences
        view = {o['key']: o['paid'] for o in preferences.scope_view('web', [])}
        self.assertEqual(view, {'brave': True, 'mojeek': True,
                                'marginalia': False, 'staan': True})
        self.assertEqual({o['key']: o['paid'] for o in preferences.scope_view('images', [])},
                         {'brave': True, 'pixabay': False})

    @override_settings(PAID_PROVIDERS=['pixabay'])
    def test_scope_view_paid_follows_the_setting(self):
        from search import preferences
        self.assertEqual({o['key']: o['paid'] for o in preferences.scope_view('images', [])},
                         {'brave': False, 'pixabay': True})

    def test_scope_view_marks_checked(self):
        from search import preferences
        view = preferences.scope_view('web', ['brave', 'marginalia'])
        self.assertEqual([(o['key'], o['checked']) for o in view],
                         [('brave', True), ('mojeek', False), ('marginalia', True),
                          ('staan', False)])
        # Carries the brand label and flag for the picker UI.
        self.assertEqual(view[0]['label'], 'Brave')
        self.assertEqual(view[0]['flag'], preferences.ENGINE_FLAGS['brave'])

    def test_scope_view_covers_each_tabs_providers(self):
        from search import preferences
        self.assertEqual([o['key'] for o in preferences.scope_view('images', [])],
                         ['brave', 'pixabay'])
        self.assertEqual([o['key'] for o in preferences.scope_view('videos', ['sepia'])],
                         ['brave', 'sepia'])
        # Single-provider tabs have nothing to pick.
        self.assertEqual(preferences.scope_view('maps', []), [])
        self.assertEqual(preferences.scope_view('translate', []), [])

    def test_tab_providers_and_labels(self):
        from search import preferences
        self.assertEqual(preferences.tab_providers('news'), ('brave', 'worldnews'))
        self.assertEqual(preferences.tab_providers('maps'), ())
        # Labels/flags resolve across both namespaces (engines and data sources).
        self.assertEqual(preferences.provider_label('brave'), 'Brave')
        self.assertEqual(str(preferences.provider_label('worldnews')),
                         str(preferences.SOURCE_LABELS['worldnews']))
        self.assertEqual(preferences.provider_flag('sepia'), preferences.SOURCE_FLAGS['sepia'])

    def test_enabled_providers_covers_engines_and_sources(self):
        from search import preferences
        prefs = preferences.coerce({'disabled_providers': {
            'web': ['brave'], 'images': ['brave', 'pixabay'], 'news': ['brave'],
        }})
        self.assertEqual(preferences.enabled_providers(prefs, 'web'),
                         ['mojeek', 'marginalia', 'staan'])
        self.assertEqual(preferences.enabled_providers(prefs, 'images'), [])
        self.assertEqual(preferences.enabled_providers(prefs, 'news'), ['worldnews'])

    def test_enabled_providers_is_read_per_tab(self):
        from search import preferences
        # Brave off for the Web only: the media tabs still default to it.
        prefs = preferences.coerce({'disabled_providers': {'web': ['brave']}})
        self.assertEqual(preferences.enabled_providers(prefs, 'web'),
                         ['mojeek', 'marginalia', 'staan'])
        self.assertEqual(preferences.enabled_providers(prefs, 'images'), ['brave', 'pixabay'])
        self.assertEqual(preferences.enabled_providers(prefs, 'videos'), ['brave', 'sepia'])

    def test_paid_providers_defaults_to_the_metered_apis(self):
        from search import preferences
        self.assertEqual(preferences.paid_providers(), preferences.DEFAULT_PAID_PROVIDERS)
        self.assertEqual(preferences.paid_providers(),
                         {'brave', 'mojeek', 'staan', 'worldnews'})

    @override_settings(PAID_PROVIDERS=[' Brave ', 'BOGUS', 'thetvdb'])
    def test_paid_providers_cleans_the_configured_list(self):
        # Whitespace and case are tolerated; anything that isn't a provider key
        # is dropped, the same clamping user input gets.
        from search import preferences
        self.assertEqual(preferences.paid_providers(), {'brave', 'thetvdb'})

    def test_every_scope_provider_is_configurable(self):
        from search import preferences
        # The scope picker can only offer providers Settings can switch on/off.
        for tab in preferences.SEARCH_TYPE_KEYS:
            self.assertLessEqual(
                set(preferences.tab_providers(tab)), set(preferences.TYPE_PROVIDERS[tab]), tab,
            )

    def test_scope_providers_is_a_per_tab_override(self):
        from search import preferences
        prefs = preferences.coerce({'disabled_providers': {'videos': ['sepia']}})
        # A provider disabled in Settings can still be scoped in for one search.
        self.assertEqual(preferences.scope_providers(prefs, 'videos', ['sepia']), ['sepia'])
        # Values the tab can't serve are dropped, so an empty result means default.
        self.assertEqual(preferences.scope_providers(prefs, 'videos', ['mojeek']), ['brave'])
        self.assertEqual(preferences.scope_providers(prefs, 'videos', []), ['brave'])
        # Canonical order, whatever order the request listed them in.
        self.assertEqual(preferences.scope_providers(prefs, 'images', ['pixabay', 'brave']),
                         ['brave', 'pixabay'])

    def test_requested_scope_is_empty_without_a_usable_scope(self):
        from search import preferences
        self.assertEqual(preferences.requested_scope('news', ['worldnews']), ['worldnews'])
        self.assertEqual(preferences.requested_scope('news', ['bogus', 'mojeek']), [])
        self.assertEqual(preferences.requested_scope('news', None), [])
        self.assertEqual(preferences.requested_scope('maps', ['openstreetmap']), [])

    def test_coerce_rejects_unknown_safe_values(self):
        # Safe search is on/off; anything else lands on the default 'on'.
        from search import preferences
        self.assertEqual(preferences.coerce({'safe_search': 'moderate'})['safe_search'], 'on')
        self.assertEqual(preferences.coerce({'safe_search': 'bogus'})['safe_search'], 'on')
        self.assertEqual(preferences.coerce({'safe_search': 'off'})['safe_search'], 'off')
        self.assertEqual(preferences.coerce({'safe_search': 'on'})['safe_search'], 'on')

    def test_brave_safesearch_maps_on_off(self):
        # Only the literal 'off' disables filtering; anything else is "on" →
        # Brave's strict.
        self.assertEqual(brave_safesearch('off'), 'off')
        self.assertEqual(brave_safesearch('on'), 'strict')
        self.assertEqual(brave_safesearch('strict'), 'strict')
        self.assertEqual(brave_safesearch('moderate'), 'strict')

    def test_coerce_non_dict_returns_defaults(self):
        from search import preferences
        self.assertEqual(preferences.coerce(None), preferences.defaults())
        self.assertEqual(preferences.coerce('nope'), preferences.defaults())

    def test_encode_decode_round_trip(self):
        from search import preferences
        original = _prefs(only_engine='mojeek', providers_off=['pixabay', 'weather'])
        self.assertEqual(preferences.decode(preferences.encode(original)), original)

    def test_decode_garbage_returns_defaults(self):
        from search import preferences
        self.assertEqual(preferences.decode('not-json'), preferences.defaults())
        self.assertEqual(preferences.decode(''), preferences.defaults())


class SettingsBackupTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('bk', password='pass')
        self.client.login(username='bk', password='pass')

    def _upload(self, doc):
        return SimpleUploadedFile(
            'settings.json', json.dumps(doc).encode('utf-8'), content_type='application/json',
        )

    def test_export_returns_full_document(self):
        from search.models import BlockedSite, CustomBang
        _set_prefs(self.client, disabled_providers={'web': ['brave', 'marginalia', 'staan']},
                   theme='dark')
        CustomBang.objects.create(user=self.user, trigger='gh', url_template='https://gh/{{{s}}}')
        BlockedSite.objects.create(user=self.user, domain='spam.com')
        resp = self.client.get(reverse('search:settings_export'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/json')
        self.assertIn('attachment', resp['Content-Disposition'])
        doc = json.loads(resp.content)
        self.assertEqual(doc['preferences']['disabled_providers'],
                         {'web': ['brave', 'marginalia', 'staan']})
        self.assertEqual(doc['preferences']['theme'], 'dark')
        self.assertEqual(doc['custom_bangs'], [{'trigger': 'gh', 'url_template': 'https://gh/{{{s}}}'}])
        self.assertEqual(doc['blocked_sites'], ['spam.com'])

    def test_import_applies_document(self):
        from search.models import BlockedSite, CustomBang
        resp = self.client.post(reverse('search:settings_import'), {'settings_file': self._upload({
            'version': 1,
            'preferences': {
                'disabled_providers': {'web': ['mojeek', 'marginalia', 'staan', 'weather']},
                'theme': 'light',
            },
            'custom_bangs': [{'trigger': 'yt', 'url_template': 'https://yt/{{{s}}}'}],
            'blocked_sites': ['ads.example'],
        })})
        self.assertEqual(resp.status_code, 302)
        prefs = _get_prefs(self.client)
        self.assertEqual(preferences.enabled_engines(prefs), ['brave'])
        self.assertEqual(prefs['theme'], 'light')
        self.assertFalse(preferences.is_enabled(prefs, 'web', 'weather'))
        self.assertTrue(CustomBang.objects.filter(user=self.user, trigger='yt').exists())
        self.assertTrue(BlockedSite.objects.filter(user=self.user, domain='ads.example').exists())

    def test_import_replaces_existing_bangs(self):
        from search.models import CustomBang
        CustomBang.objects.create(user=self.user, trigger='old', url_template='https://o/{{{s}}}')
        self.client.post(reverse('search:settings_import'), {'settings_file': self._upload({
            'preferences': {}, 'custom_bangs': [], 'blocked_sites': [],
        })})
        self.assertFalse(CustomBang.objects.filter(user=self.user, trigger='old').exists())

    def test_import_skips_invalid_entries(self):
        from search.models import BlockedSite, CustomBang
        self.client.post(reverse('search:settings_import'), {'settings_file': self._upload({
            'preferences': {},
            'custom_bangs': [{'trigger': 'bad trigger!', 'url_template': 'no-placeholder'}],
            'blocked_sites': ['notadomain'],
        })})
        self.assertEqual(CustomBang.objects.filter(user=self.user).count(), 0)
        self.assertEqual(BlockedSite.objects.filter(user=self.user).count(), 0)

    def test_import_rejects_script_breakout_bang_url(self):
        # A bang URL carrying a </script> breakout must not enter the database
        # through the import path either (it would then render for this user
        # on every page).
        from search.models import CustomBang
        self.client.post(reverse('search:settings_import'), {'settings_file': self._upload({
            'preferences': {},
            'custom_bangs': [{
                'trigger': 'evil',
                'url_template': 'https://example.com/</script><script>alert(1)</script>?q={{{s}}}',
            }],
            'blocked_sites': [],
        })})
        self.assertEqual(CustomBang.objects.filter(user=self.user).count(), 0)

    def test_import_rejects_bad_json(self):
        upload = SimpleUploadedFile('settings.json', b'<<not json>>', content_type='application/json')
        resp = self.client.post(reverse('search:settings_import'), {'settings_file': upload}, follow=True)
        self.assertContains(resp, 'not valid JSON')

    def test_import_requires_a_file(self):
        resp = self.client.post(reverse('search:settings_import'), follow=True)
        self.assertContains(resp, 'Choose a settings file')

    def test_pref_change_auto_syncs_to_account(self):
        # Changing any preference snapshots the full settings document to the
        # account automatically, there is no manual "save to account" step.
        from search.models import UserSettings
        self.client.post(reverse('search:settings'), _save_web(provider_mojeek='on'))
        saved = UserSettings.objects.filter(user=self.user).first()
        self.assertIsNotNone(saved)
        self.assertEqual(saved.document['preferences']['disabled_providers']['web'],
                         ['brave', 'marginalia', 'staan', 'wikipedia', 'thetvdb',
                          'tripadvisor', 'stackexchange', 'weather'])

    def test_bang_change_auto_syncs_to_account(self):
        from search.models import CustomBang, UserSettings
        self.client.post(reverse('search:settings'), {
            'setting': 'add_custom_bang', 'trigger': 'gh',
            'url_template': 'https://gh/{{{s}}}', 'pane': 'bangs',
        })
        self.assertTrue(CustomBang.objects.filter(user=self.user, trigger='gh').exists())
        saved = UserSettings.objects.filter(user=self.user).first()
        self.assertIsNotNone(saved)
        self.assertIn('gh', [b['trigger'] for b in saved.document['custom_bangs']])

    def test_settings_restore_on_new_device(self):
        # A device with no preferences cookie but an existing account snapshot
        # has its preferences restored by AutoSyncSettingsMiddleware.
        from search import preferences
        from search.models import UserSettings
        self.client.post(reverse('search:settings'), _save_web(provider_brave='on'))
        self.assertTrue(UserSettings.objects.filter(user=self.user).exists())
        # Simulate a fresh device: drop the preferences cookie.
        del self.client.cookies[preferences.COOKIE_NAME]
        self.client.get(reverse('search:index'))
        self.assertEqual(_web_engines(self.client), ['brave'])

    def test_stale_device_picks_up_change_from_other_device(self):
        # A device that *has* a preferences cookie, but one predating the
        # account snapshot, is refreshed too, so a change made on one device
        # (e.g. enabling image proxying) reaches every other device.
        from search import backup
        self.client.post(reverse('search:settings'), {
            'setting': 'proxy_images', 'proxy_images': 'on', 'pane': 'general',
        })
        # Simulate another signed-in device: an old cookie, never saw the snapshot.
        _set_prefs(self.client, proxy_images=False)
        del self.client.cookies[backup.REV_COOKIE_NAME]
        resp = self.client.get(reverse('search:settings'))
        # Effective immediately for this very request, and persisted on the device.
        self.assertTrue(resp.context['proxy_images'])
        self.assertTrue(_get_prefs(self.client)['proxy_images'])

    def test_device_in_sync_is_not_rewritten(self):
        # A device whose revision matches the snapshot keeps its cookie as-is,
        # no Set-Cookie churn on every request.
        from search import preferences
        self.client.post(reverse('search:settings'), _save_web(provider_mojeek='on'))
        resp = self.client.get(reverse('search:settings'))
        self.assertNotIn(preferences.COOKIE_NAME, resp.cookies)
        self.assertEqual([p['key'] for p in _type_group(resp, 'web')['engines'] if p['enabled']],
                         ['mojeek'])

    def test_import_reaches_other_devices(self):
        # Importing a settings file updates the account snapshot, so the
        # imported state syncs to other devices instead of staying local.
        from search import preferences
        from search.models import UserSettings
        self.client.post(reverse('search:settings_import'), {'settings_file': self._upload({
            'version': 1,
            'preferences': {
                'disabled_providers': {'web': ['brave', 'mojeek', 'staan']},
                'proxy_images': True,
            },
            'custom_bangs': [],
            'blocked_sites': ['ads.example'],
        })})
        saved = UserSettings.objects.filter(user=self.user).first()
        self.assertIsNotNone(saved)
        self.assertEqual(saved.document['preferences']['disabled_providers']['web'],
                         ['brave', 'mojeek', 'staan'])
        self.assertTrue(saved.document['preferences']['proxy_images'])
        self.assertEqual(saved.document['blocked_sites'], ['ads.example'])
        # And a fresh device restores the imported preferences.
        del self.client.cookies[preferences.COOKIE_NAME]
        self.client.get(reverse('search:index'))
        self.assertEqual(_web_engines(self.client), ['marginalia'])

    def test_quick_block_action_updates_snapshot(self):
        # The "Block" quick-action on results syncs the document like the
        # settings page does.
        from search.models import UserSettings
        self.client.post(reverse('search:block_site'), {'domain': 'spam.example', 'next': '/'})
        saved = UserSettings.objects.filter(user=self.user).first()
        self.assertIsNotNone(saved)
        self.assertIn('spam.example', saved.document['blocked_sites'])

    def test_settings_page_shows_backup_pane(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'backup'}))
        self.assertEqual(resp.context['active_pane'], 'backup')
        self.assertContains(resp, 'Backup')


class SettingsStructureTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('struct', password='pass')
        self.client.login(username='struct', password='pass')

    def test_engine_pane_selectable(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        self.assertEqual(resp.context['active_pane'], 'engine')
        self.assertContains(resp, 'id="settings-engine"')

    def test_removed_panes_have_no_url(self):
        # Panes that were dropped from Settings are not addressable: the URLconf
        # constrains the slug set, so their old paths 404 rather than quietly
        # rendering the first pane.
        for pane in ('advanced', 'privacy'):
            self.assertEqual(self.client.get(f'/settings/{pane}/').status_code, 404, pane)

    def test_privacy_content_removed(self):
        resp = self.client.get(reverse('search:settings'))
        self.assertNotContains(resp, 'No tracking or profiling')

    def test_browser_pane_keeps_instructions_without_add_button(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'browser'}))
        self.assertNotContains(resp, 'id="add-engine-btn"')
        self.assertContains(resp, 'about:preferences#search')  # manual instructions remain


class SettingsAutoSaveTests(TestCase):
    """Preference toggles/selects save the moment they change (JS), and still
    submit via their Save button without JavaScript (no-JS fallback)."""

    def setUp(self):
        self.user = User.objects.create_user('auto', password='pass')
        self.client.login(username='auto', password='pass')

    # --- markup: live with JS, working buttons without it --------------------
    def test_pref_forms_marked_for_autosave(self):
        resp = self.client.get(reverse('search:settings'))
        # Every preference form opts in (six scalar settings plus one provider
        # form per search type); the account email form must not.
        self.assertEqual(resp.content.count(b'data-autosave'), 6 + len(preferences.SEARCH_TYPES))

    def test_save_buttons_hidden_only_when_js_present(self):
        resp = self.client.get(reverse('search:settings'))
        # The redundant Save buttons stay in the HTML (no-JS fallback) but the
        # `js:` variant hides them once scripting flips `.js` on <html>.
        self.assertContains(resp, 'js:hidden')
        self.assertContains(resp, 'Save changes')  # still there for no-JS users
        self.assertContains(resp, "classList.add('js')")  # the flip itself

    def test_autosave_script_included(self):
        resp = self.client.get(reverse('search:settings'))
        self.assertContains(resp, 'search/settings.js')

    def test_saved_toast_label_is_translated(self):
        # The toast text is handed to JS via {% trans %}, so the string must
        # live in the catalogs (regression: "Settings saved." was a bare Python
        # message string before, untranslated). Depends on compiled .mo files.
        self.client.post(reverse('search:settings'), {
            'setting': 'ui_lang', 'ui_lang': 'fr', 'pane': 'language',
        })
        resp = self.client.get(reverse('search:settings'))
        self.assertContains(resp, 'Paramètres enregistrés.')  # FR toast label

    # --- the JS path: a background fetch that just gets an ack ---------------
    def test_ajax_toggle_saves_and_returns_json(self):
        resp = self.client.post(
            reverse('search:settings'),
            {'setting': 'safe_search', 'pane': 'general'},  # unticked → off
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/json')
        self.assertEqual(resp.json(), {'ok': True})
        self.assertEqual(_get_prefs(self.client)['safe_search'], 'off')

    def test_ajax_toggle_on_persists(self):
        _set_prefs(self.client, safe_search='off')
        self.client.post(
            reverse('search:settings'),
            {'setting': 'safe_search', 'safe_search': 'on'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(_get_prefs(self.client)['safe_search'], 'on')

    def test_ajax_multi_toggle_engines(self):
        resp = self.client.post(
            reverse('search:settings'), _save_web(provider_mojeek='on'),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.json(), {'ok': True})
        self.assertEqual(_web_engines(self.client), ['mojeek'])

    def test_ajax_save_skips_flash_message(self):
        # No page reload happens on the JS path, so we don't queue a banner the
        # user would only see on some unrelated later navigation. (Checked via
        # the messages framework, since the template always carries the literal
        # "Settings saved." as the JS toast label.)
        from django.contrib.messages import get_messages
        resp = self.client.post(
            reverse('search:settings'),
            {'setting': 'proxy_images', 'pane': 'general'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual([str(m) for m in get_messages(resp.wsgi_request)], [])

    # --- the no-JS path is untouched: plain POST → redirect + banner ---------
    def test_plain_post_still_redirects_and_flashes(self):
        from django.contrib.messages import get_messages
        resp = self.client.post(
            reverse('search:settings'),
            {'setting': 'proxy_images', 'pane': 'general'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn('Settings saved.', [str(m) for m in get_messages(resp.wsgi_request)])
        self.assertFalse(_get_prefs(self.client)['proxy_images'])


class MapsTabViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('dave', password='pass')
        self.client.login(username='dave', password='pass')

    @patch('search.views.fetch_geocode')
    def test_maps_tab_lists_places(self, mock_geo):
        mock_geo.return_value = [{
            'name': 'Paris', 'display_name': 'Paris, France',
            'lat': 48.8566, 'lon': 2.3522, 'type': 'city', 'category': 'place',
            'embed_url': 'https://www.openstreetmap.org/export/embed.html?bbox=a&marker=b',
            'mini_embed_url': 'https://www.openstreetmap.org/export/embed.html?bbox=mini',
            'osm_url': 'https://www.openstreetmap.org/?mlat=48.8566&mlon=2.3522#map=16/48.8566/2.3522',
        }]
        resp = self.client.get(reverse('search:results') + '?q=paris&tab=maps')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'maps')
        self.assertEqual(len(resp.context['map_places']), 1)
        self.assertEqual(resp.context['map_primary']['name'], 'Paris')
        self.assertContains(resp, 'id="map-frame"')
        self.assertContains(resp, 'id="map-directions"')
        self.assertContains(resp, 'Directions')
        # The frame follows the primary place, not the world view.
        self.assertEqual(resp.context['map_embed_url'],
                         'https://www.openstreetmap.org/export/embed.html?bbox=a&marker=b')
        mock_geo.assert_called_once()

    @patch('search.views.fetch_geocode', return_value=[])
    def test_maps_tab_no_results(self, mock_geo):
        # Nothing geocoded: the map itself stays on screen (world view) and the
        # sidebar, where the places would be, explains why it's empty.
        resp = self.client.get(reverse('search:results') + '?q=zzznowhere&tab=maps')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['map_places'], [])
        self.assertIsNone(resp.context['map_primary'])
        self.assertContains(resp, 'No places found')
        self.assertContains(resp, 'id="map-frame"')
        self.assertEqual(resp.context['map_embed_url'], WORLD_EMBED_URL)
        self.assertContains(resp, 'maps-sidebar-empty')
        self.assertNotContains(resp, 'id="map-directions"')  # no destination

    def test_maps_tab_without_query_shows_the_world_map(self):
        # Switching to Maps from another tab carries no query; the tab shows the
        # map anyway and puts the invitation in the sidebar.
        with patch('search.views.fetch_geocode') as mock_geo:
            resp = self.client.get(reverse('search:results') + '?tab=maps')
            mock_geo.assert_not_called()
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context['map_primary'])
        self.assertEqual(resp.context['map_embed_url'], WORLD_EMBED_URL)
        self.assertEqual(resp.context['map_osm_url'], WORLD_OSM_URL)
        self.assertContains(resp, 'id="map-frame"')
        self.assertContains(resp, WORLD_EMBED_URL.replace('&', '&amp;'))
        self.assertContains(resp, 'Explore the map')
        self.assertContains(resp, 'Larger map')
        self.assertNotContains(resp, 'id="map-directions"')

    def test_maps_tab_with_explicit_coords(self):
        # When lat/lon are supplied (e.g. from a TripAdvisor card) no geocoding
        # happens, the coordinates are used directly.
        with patch('search.views.fetch_geocode') as mock_geo:
            resp = self.client.get(
                reverse('search:results')
                + '?q=Eiffel+Tower&tab=maps&lat=48.8584&lon=2.2945&label=Eiffel+Tower'
            )
            mock_geo.assert_not_called()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context['map_places']), 1)
        primary = resp.context['map_primary']
        self.assertEqual(primary['lat'], 48.8584)
        self.assertEqual(primary['lon'], 2.2945)
        self.assertEqual(primary['name'], 'Eiffel Tower')
        # Directions button targets the point: OSM routing (desktop default)
        # plus the geo: URI the client uses on mobile.
        self.assertContains(resp, 'openstreetmap.org/directions?to=48.8584%2C2.2945')
        self.assertContains(resp, 'geo:48.8584,2.2945')

    @patch('search.views.detect_instant_answer', return_value=None)
    @patch('search.views.fetch_wikipedia', return_value=None)
    @patch('search.panel.fetch_geocode')
    def test_web_tab_shows_map_answer_for_address(self, mock_geo, mock_wiki, mock_detect):
        # The map quick-answer is now a `type='map'` instant answer that lives in
        # the single `instant_answer` slot (rendered by instant/_map.html).
        mock_geo.return_value = [{
            'name': '10 Downing Street', 'display_name': '10 Downing Street, London',
            'lat': 51.5034, 'lon': -0.1276, 'type': 'house', 'category': 'place',
            'embed_url': 'https://www.openstreetmap.org/export/embed.html?bbox=a&marker=b',
            'mini_embed_url': 'https://www.openstreetmap.org/export/embed.html?bbox=mini',
            'osm_url': 'https://www.openstreetmap.org/?mlat=51.5034&mlon=-0.1276#map=16/51.5/-0.13',
        }]
        resp = self.client.get(reverse('search:results') + '?q=10+Downing+Street')
        self.assertEqual(resp.status_code, 200)
        answer = resp.context['instant_answer']
        self.assertIsNotNone(answer)
        self.assertEqual(answer['type'], 'map')
        self.assertEqual(answer['place']['name'], '10 Downing Street')
        self.assertContains(resp, 'Open in Maps')
        mock_geo.assert_called_once()

    @patch('search.views.detect_instant_answer', return_value=None)
    @patch('search.views.fetch_wikipedia', return_value=None)
    @patch('search.panel.fetch_geocode')
    def test_web_tab_no_map_answer_for_plain_query(self, mock_geo, mock_wiki, mock_detect):
        resp = self.client.get(reverse('search:results') + '?q=python+tutorial')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context['instant_answer'])
        mock_geo.assert_not_called()

    @patch('search.views.detect_instant_answer')
    @patch('search.views.fetch_wikipedia', return_value=None)
    @patch('search.panel.fetch_geocode')
    def test_web_tab_map_hidden_when_other_instant_answer(self, mock_geo, mock_wiki, mock_detect):
        # "map of berlin" is a place phrase, but another instant answer already
        # matched, the map must not replace it and must not even geocode.
        mock_detect.return_value = {'type': 'math', 'query': 'map of berlin', 'result': '42'}
        resp = self.client.get(reverse('search:results') + '?q=map+of+berlin')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['instant_answer']['type'], 'math')
        mock_geo.assert_not_called()

    @patch('search.views.detect_instant_answer', return_value=None)
    @patch('search.views.fetch_wikipedia', return_value=None)
    @patch('search.panel.fetch_geocode')
    def test_web_map_answer_skipped_when_openstreetmap_disabled(self, mock_geo, mock_wiki, mock_detect):
        # Turning OpenStreetMap off stops the web-tab map quick-answer from
        # geocoding at all.
        _set_prefs(self.client, providers_off=['openstreetmap'])
        resp = self.client.get(reverse('search:results') + '?q=10+Downing+Street')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context['instant_answer'])
        mock_geo.assert_not_called()

    def test_maps_tab_unavailable_when_openstreetmap_disabled(self):
        # With OSM off the Maps tab is gone: the request falls back to web and
        # the Maps label is dropped from the nav. (No query → no geocoding.)
        _set_prefs(self.client, providers_off=['openstreetmap'])
        resp = self.client.get(reverse('search:results') + '?tab=maps')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'web')
        self.assertNotIn('maps', resp.context['available_tabs'])
        self.assertNotContains(resp, 'data-tab="maps"')


class TabAvailabilityTests(TestCase):
    """Tabs are hidden (and their bangs deactivated) when the user's engine or
    source choices can't serve them (task: hide image/web/map tabs with no
    engine)."""

    def setUp(self):
        self.user = User.objects.create_user('tina', password='pass')
        self.client.login(username='tina', password='pass')

    # --- _available_tabs: the capability matrix ---
    def test_all_engine_serves_every_tab(self):
        tabs = _available_tabs(_prefs())
        self.assertEqual(tabs, {'web', 'images', 'news', 'videos', 'maps'})

    def test_mojeek_rides_on_fallback_providers(self):
        # Mojeek has no news/image/video search of its own, but News rides on
        # World News, Images on Pixabay and Videos on Sepia (all on by default),
        # so every tab is still available.
        tabs = _available_tabs(_prefs(only_engine='mojeek'))
        self.assertEqual(tabs, {'web', 'images', 'news', 'videos', 'maps'})

    def test_marginalia_without_pixabay_has_no_images(self):
        # Pixabay off + no Brave → no Images; News (World News) and Videos
        # (Sepia) still come from their fallback providers.
        tabs = _available_tabs(_prefs(only_engine='marginalia', providers_off=['pixabay']))
        self.assertEqual(tabs, {'web', 'news', 'videos', 'maps'})

    def test_news_hidden_without_brave_and_worldnews(self):
        # No Brave engine and World News disabled → the News tab has no source.
        tabs = _available_tabs(_prefs(only_engine='mojeek', providers_off=['worldnews']))
        self.assertNotIn('news', tabs)

    def test_videos_hidden_without_brave_and_sepia(self):
        # No Brave engine and Sepia disabled → the Videos tab has no source.
        tabs = _available_tabs(_prefs(only_engine='mojeek', providers_off=['sepia']))
        self.assertNotIn('videos', tabs)

    def test_openstreetmap_off_removes_maps(self):
        tabs = _available_tabs(_prefs(providers_off=['openstreetmap']))
        self.assertNotIn('maps', tabs)

    def test_web_always_available(self):
        # Web has no "no source" state: it is there with every engine enabled,
        # and with any single one of them as the only engine left.
        self.assertIn('web', _available_tabs(_prefs()))
        for engine in preferences.REAL_ENGINES:
            self.assertIn('web', _available_tabs(_prefs(only_engine=engine)))

    # --- nav rendering (results + index) ---
    def test_results_nav_shows_news_for_mojeek(self):
        # News rides on World News for Mojeek (it has no news of its own), just
        # as Videos ride on Sepia and Images on Pixabay, so the tab is shown.
        _set_prefs(self.client, only_engine='mojeek')
        resp = self.client.get(reverse('search:results'))  # no query → no network
        self.assertContains(resp, 'data-tab="web"')
        self.assertContains(resp, 'data-tab="maps"')
        self.assertContains(resp, 'data-tab="videos"')
        self.assertContains(resp, 'data-tab="news"')

    def test_results_nav_hides_news_without_provider(self):
        # Mojeek + World News off → no news source → the News tab is hidden.
        _set_prefs(self.client, only_engine='mojeek', providers_off=['worldnews'])
        resp = self.client.get(reverse('search:results'))  # no query → no network
        self.assertNotContains(resp, 'data-tab="news"')

    def test_results_nav_shows_all_tabs_for_all_engine(self):
        resp = self.client.get(reverse('search:results'))
        for tab in ('web', 'images', 'news', 'videos', 'maps'):
            self.assertContains(resp, f'data-tab="{tab}"')

    def test_index_shows_news_for_mojeek(self):
        _set_prefs(self.client, only_engine='mojeek')
        resp = self.client.get(reverse('search:index'))
        self.assertContains(resp, 'tab=web')
        self.assertContains(resp, 'tab=videos')  # Sepia-powered
        self.assertContains(resp, 'tab=news')  # World News-powered

    # --- bang deactivation through the results view ---
    def test_news_bang_active_for_all_engine(self):
        resp = self.client.get(reverse('search:results') + '?q=!news+kyiv')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/search/?q=kyiv&tab=news')

    def test_news_bang_active_for_mojeek_via_worldnews(self):
        # World News gives Mojeek a News tab, so the !news bang stays active.
        _set_prefs(self.client, only_engine='mojeek')
        resp = self.client.get(reverse('search:results') + '?q=!news+kyiv')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/search/?q=kyiv&tab=news')

    def test_news_bang_deactivated_without_provider(self):
        # Mojeek + World News off → no news source → !news degrades to web.
        _set_prefs(self.client, only_engine='mojeek', providers_off=['worldnews'])
        resp = self.client.get(reverse('search:results') + '?q=!news+kyiv')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/search/?q=kyiv&tab=web')

    def test_maps_bang_deactivated_when_openstreetmap_disabled(self):
        _set_prefs(self.client, providers_off=['openstreetmap'])
        resp = self.client.get(reverse('search:results') + '?q=!m+berlin')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/search/?q=berlin&tab=web')


class SuggestEngineGateTests(TestCase):
    """Search-bar autocomplete (Brave suggest API) is off when Brave is disabled."""

    def setUp(self):
        self.user = User.objects.create_user('sug', password='pass')
        self.client.login(username='sug', password='pass')

    @patch('search.views.fetch_suggestions', return_value=['python tutorial'])
    def test_suggest_works_when_brave_enabled(self, mock_fetch):
        resp = self.client.get(reverse('search:suggest') + '?q=python')
        self.assertEqual(resp.json()['suggestions'], ['python tutorial'])
        mock_fetch.assert_called_once()

    @patch('search.views.fetch_suggestions', return_value=['python tutorial'])
    def test_suggest_empty_when_brave_disabled(self, mock_fetch):
        _set_prefs(self.client, providers_off=['brave'])
        resp = self.client.get(reverse('search:suggest') + '?q=python')
        self.assertEqual(resp.json()['suggestions'], [])
        mock_fetch.assert_not_called()  # no keystrokes sent to Brave

    @patch('search.views.fetch_suggestions', return_value=['python tutorial'])
    def test_opensearch_suggest_empty_when_brave_disabled(self, mock_fetch):
        _set_prefs(self.client, providers_off=['brave'])
        resp = self.client.get(reverse('search:opensearch_suggest') + '?q=py')
        self.assertEqual(resp.json(), ['py', []])
        mock_fetch.assert_not_called()

    @patch('search.views.fetch_suggestions', return_value=['python tutorial'])
    def test_suggest_still_works_with_other_engine_disabled(self, mock_fetch):
        # Disabling Mojeek (but keeping Brave) leaves autocomplete on.
        _set_prefs(self.client, providers_off=['mojeek'])
        resp = self.client.get(reverse('search:suggest') + '?q=python')
        self.assertEqual(resp.json()['suggestions'], ['python tutorial'])
        mock_fetch.assert_called_once()


class SecurityHeadersTests(TestCase):
    """Hardening headers from settings: nosniff, Referrer-Policy and the
    (report-only, for now) Content Security Policy with per-request nonces."""

    def setUp(self):
        self.user = User.objects.create_user('sec', password='pass')
        self.client.login(username='sec', password='pass')

    def test_hardening_headers_present(self):
        resp = self.client.get(reverse('search:index'))
        self.assertEqual(resp['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(resp['Referrer-Policy'], 'same-origin')
        policy = resp['Content-Security-Policy-Report-Only']
        self.assertIn("default-src 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertIn('nonce-', policy)

    def test_inline_scripts_carry_the_nonce(self):
        resp = self.client.get(reverse('search:index'))
        policy = resp['Content-Security-Policy-Report-Only']
        nonce = re.search(r"nonce-([A-Za-z0-9_-]+)", policy).group(1)
        self.assertContains(resp, f'nonce="{nonce}"')


class SearchQueryLoggingTests(TestCase):
    """Raw query text in the app log is opt-in (LOG_SEARCH_QUERIES): a
    privacy-first product must not retain every user's searches in container
    logs by default."""

    def setUp(self):
        self.user = User.objects.create_user('quinn', password='pass')
        self.client.login(username='quinn', password='pass')

    def _search(self):
        with (
            patch('search.views.fetch_images', return_value=[]),
            self.assertLogs('search.views', 'INFO') as logs,
        ):
            self.client.get(reverse('search:results') + '?q=secret+medical+condition&tab=images')
        return '\n'.join(logs.output)

    @override_settings(LOG_SEARCH_QUERIES=False)
    def test_query_text_absent_when_disabled(self):
        output = self._search()
        self.assertNotIn('secret medical condition', output)
        # The lines still carry the useful non-identifying bits.
        self.assertIn('len=24', output)
        self.assertIn('tab=images', output)
        self.assertIn('search done', output)

    @override_settings(LOG_SEARCH_QUERIES=True)
    def test_query_text_present_when_enabled(self):
        output = self._search()
        self.assertIn('secret medical condition', output)

    @override_settings(LOG_SEARCH_QUERIES=False)
    def test_blocked_domain_not_paired_with_user_when_disabled(self):
        with self.assertLogs('search.views', 'INFO') as logs:
            self.client.post(reverse('search:block_site'), {'domain': 'embarrassing.example', 'next': '/'})
        output = '\n'.join(logs.output)
        self.assertNotIn('embarrassing.example', output)
        self.assertIn('[redacted]', output)


@override_settings(OPENSEARCH_THROTTLE_LIMIT=5)
class OpenSearchThrottleTests(TestCase):
    """The public (unauthenticated) OpenSearch endpoints are capped per IP,
    suggest hits Brave's paid API, so anonymous callers can't drain quota."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()  # rate-limit counters must not leak between tests

    @patch('search.views.fetch_suggestions', return_value=['python tutorial'])
    def test_suggest_rate_limited_per_ip(self, mock_fetch):
        url = reverse('search:opensearch_suggest') + '?q=py'
        for _ in range(5):
            self.assertEqual(self.client.get(url).status_code, 200)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json(), ['py', []])
        # The over-budget request never reached the paid upstream.
        self.assertEqual(mock_fetch.call_count, 5)

    def test_opensearch_xml_rate_limited_per_ip(self):
        url = reverse('search:opensearch_xml')
        for _ in range(5):
            self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(url).status_code, 429)

    @patch('search.views.fetch_suggestions', return_value=['python tutorial'])
    def test_endpoints_budget_independently(self, mock_fetch):
        # Hammering suggest must not break a browser fetching the descriptor.
        suggest = reverse('search:opensearch_suggest') + '?q=py'
        for _ in range(6):
            self.client.get(suggest)
        self.assertEqual(self.client.get(reverse('search:opensearch_xml')).status_code, 200)


class OpenStreetMapSourceTests(TestCase):
    """OpenStreetMap is a user-toggleable source (task: allow disabling OSM)."""

    def setUp(self):
        self.user = User.objects.create_user('omar', password='pass')
        self.client.login(username='omar', password='pass')

    def test_openstreetmap_listed_as_toggle(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        self.assertContains(resp, 'name="provider_openstreetmap"')
        self.assertContains(resp, 'OpenStreetMap')

    def test_openstreetmap_keyless_toggle_never_disabled(self):
        # No API key → never rendered as a disabled (locked) toggle.
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        tag = _provider_input(resp.content.decode(), 'maps', 'openstreetmap')
        self.assertNotIn('disabled', tag)

    def test_disabling_openstreetmap_persists(self):
        # Submit the Maps form with its only box unticked.
        self.client.post(reverse('search:settings'), _save_providers('maps'))
        self.assertFalse(preferences.is_enabled(_get_prefs(self.client), 'maps', 'openstreetmap'))

    def test_engine_pane_renamed_to_engines(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'engines'}))
        self.assertContains(resp, 'Engines')


@override_settings(LIBRETRANSLATE_URL='http://localhost:5000')
class TranslateTabViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('frank', password='pass')
        self.client.login(username='frank', password='pass')

    @patch('search.views.fetch_languages', return_value=[('en', 'English'), ('fr', 'French')])
    @patch('search.views.fetch_translation')
    def test_translate_tab_shows_translation(self, mock_translate, mock_languages):
        mock_translate.return_value = {'translated_text': 'Bonjour', 'detected_lang': 'en'}
        resp = self.client.get(reverse('search:results') + '?q=hello&tab=translate&target=fr')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'translate')
        self.assertEqual(resp.context['translation']['translated_text'], 'Bonjour')
        self.assertEqual(resp.context['translate_detected_name'], 'English')
        self.assertContains(resp, 'Bonjour')
        mock_translate.assert_called_once_with('hello', 'fr', 'auto')

    @patch('search.views.fetch_languages', return_value=[('en', 'English'), ('fr', 'French')])
    @patch('search.views.fetch_translation', return_value=None)
    def test_translate_tab_unavailable(self, mock_translate, mock_languages):
        resp = self.client.get(reverse('search:results') + '?q=hello&tab=translate')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context['translation'])
        self.assertTrue(resp.context['translate_unavailable'])
        self.assertContains(resp, 'Translation service is unavailable')

    @patch('search.views.fetch_languages', return_value=[('en', 'English'), ('fr', 'French')])
    def test_translate_tab_empty_query_shows_languages(self, mock_languages):
        resp = self.client.get(reverse('search:results') + '?tab=translate')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'translate')
        self.assertContains(resp, 'Translation will appear here')
        self.assertContains(resp, 'English')
        self.assertContains(resp, 'French')

    @patch('search.views.fetch_languages', return_value=[('en', 'English'), ('fr', 'French')])
    def test_translate_tab_has_textarea_for_direct_input(self, mock_languages):
        resp = self.client.get(reverse('search:results') + '?q=hello&tab=translate')
        self.assertContains(resp, '<textarea')
        self.assertContains(resp, 'name="q"')

    @patch('search.views.fetch_languages', return_value=[('en', 'English'), ('fr', 'French')])
    def test_translate_panel_is_a_post_form(self, mock_languages):
        # The text to translate must stay out of the URL/history, so the panel
        # submits via POST (with a CSRF token) and the language selects carry
        # their own names so changing one re-submits the entered text.
        resp = self.client.get(reverse('search:results') + '?tab=translate')
        self.assertContains(resp, 'method="post"')
        self.assertContains(resp, 'csrfmiddlewaretoken')
        self.assertContains(resp, 'name="source"')
        self.assertContains(resp, 'name="target"')

    @patch('search.views.fetch_languages', return_value=[('en', 'English'), ('fr', 'French')])
    @patch('search.views.fetch_translation')
    def test_translate_tab_accepts_post(self, mock_translate, mock_languages):
        mock_translate.return_value = {'translated_text': 'Bonjour', 'detected_lang': ''}
        resp = self.client.post(reverse('search:results'), {
            'tab': 'translate', 'q': 'Hello', 'source': 'en', 'target': 'fr',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'translate')
        self.assertContains(resp, 'Bonjour')
        mock_translate.assert_called_once_with('Hello', 'fr', 'en')

    @patch('search.views.fetch_languages', return_value=[('en', 'English'), ('ja', 'Japanese')])
    @patch('search.views.fetch_translation')
    def test_translate_post_from_explicit_source(self, mock_translate, mock_languages):
        # Translating *from* a manually chosen source (e.g. Japanese) passes that
        # source through: the selector value is submitted with the text now,
        # rather than a page reload dropping what the user typed.
        mock_translate.return_value = {'translated_text': 'Hello', 'detected_lang': ''}
        resp = self.client.post(reverse('search:results'), {
            'tab': 'translate', 'q': 'こんにちは', 'source': 'ja', 'target': 'en',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Hello')
        mock_translate.assert_called_once_with('こんにちは', 'en', 'ja')

    @patch('search.views.fetch_languages', return_value=[('en', 'English'), ('fr', 'French')])
    @patch('search.views.fetch_translation')
    def test_translate_text_is_not_bang_resolved(self, mock_translate, mock_languages):
        # Text to translate may legitimately start with "!"; it must be translated,
        # not hijacked into a bang redirect the way a search query would be.
        mock_translate.return_value = {'translated_text': '!Bonjour', 'detected_lang': ''}
        resp = self.client.post(reverse('search:results'), {
            'tab': 'translate', 'q': '!g hello', 'source': 'en', 'target': 'fr',
        })
        self.assertEqual(resp.status_code, 200)
        mock_translate.assert_called_once_with('!g hello', 'fr', 'en')

    @patch('search.views.fetch_languages', return_value=[('en', 'English'), ('fr', 'French')])
    def test_translate_tab_shown_in_nav_when_enabled(self, mock_languages):
        resp = self.client.get(reverse('search:results') + '?q=hello&tab=web')
        self.assertContains(resp, 'id="tab-translate"')

    @patch('search.views.fetch_languages', return_value=[('en', 'English'), ('fr', 'French')])
    def test_translate_link_shown_on_index_when_enabled(self, mock_languages):
        resp = self.client.get(reverse('search:index'))
        self.assertContains(resp, 'tab=translate')


class TranslateDisabledTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('frank', password='pass')
        self.client.login(username='frank', password='pass')

    @override_settings(LIBRETRANSLATE_URL='')
    def test_translate_tab_unset_url_falls_back_to_web(self):
        resp = self.client.get(reverse('search:results') + '?q=hello&tab=translate')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'web')
        self.assertFalse(resp.context['translate_enabled'])

    @override_settings(LIBRETRANSLATE_URL='')
    def test_translate_tab_hidden_from_nav_when_url_unset(self):
        resp = self.client.get(reverse('search:results') + '?q=hello&tab=web')
        self.assertNotContains(resp, 'id="tab-translate"')

    @override_settings(LIBRETRANSLATE_URL='')
    def test_translate_link_hidden_from_index_when_url_unset(self):
        resp = self.client.get(reverse('search:index'))
        self.assertNotContains(resp, 'tab=translate')

    @override_settings(LIBRETRANSLATE_URL='http://localhost:5000')
    def test_translate_tab_falls_back_to_web_when_user_disables_it(self):
        _set_prefs(self.client, providers_off=['translate'])
        resp = self.client.get(reverse('search:results') + '?q=hello&tab=translate')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'web')
        self.assertFalse(resp.context['translate_enabled'])

    @override_settings(LIBRETRANSLATE_URL='http://localhost:5000')
    def test_translate_tab_hidden_from_nav_when_user_disables_it(self):
        _set_prefs(self.client, providers_off=['translate'])
        resp = self.client.get(reverse('search:results') + '?q=hello&tab=web')
        self.assertNotContains(resp, 'id="tab-translate"')


class WholeWordMatchTests(TestCase):
    """Keyword detection matches whole words, not substrings."""

    def test_no_substring_false_positives(self):
        self.assertFalse(_contains_word('spain', {'spa'}))
        self.assertFalse(_contains_word('barcelona', {'bar'}))
        self.assertFalse(_contains_word('filmmaker', {'film'}))

    def test_matches_whole_words(self):
        self.assertTrue(_contains_word('day spa downtown', {'spa'}))
        self.assertTrue(_contains_word('a quiet bar', {'bar'}))

    def test_matches_accented_and_phrases(self):
        self.assertTrue(_contains_word('hôtel à paris', {'hôtel'}))
        self.assertTrue(_contains_word('best things to do in rome', {'things to do'}))


class WikidataEntityTagsTests(TestCase):
    """`entity_tags` classifies a Wikipedia subject language-independently."""

    @staticmethod
    def _claims(*ids):
        return {'claims': {'P31': [
            {'mainsnak': {'datavalue': {'value': {'id': i}}}} for i in ids
        ]}}

    @staticmethod
    def _labels(**by_id):
        return {'entities': {
            qid: {'labels': {'en': {'value': label}}} for qid, label in by_id.items()
        }}

    def test_film_recognised_by_class_id(self):
        cm, _ = _fake_httpx_client([self._claims('Q11424'), self._labels(Q11424='film')])
        with patch('cards.wikidata.httpx.Client', return_value=cm):
            tags = entity_tags('Q25188')
        self.assertEqual(tags, frozenset({'movie_tv'}))

    def test_film_subclass_recognised_by_english_label(self):
        # The long tail: an uncurated subclass id ("romantic comedy film")
        # still classifies via its English label, whatever language the
        # article was in (the German wiki says "Filmkomödie", no keyword hit).
        cm, _ = _fake_httpx_client([self._claims('Q860626'), self._labels(Q860626='romantic comedy film')])
        with patch('cards.wikidata.httpx.Client', return_value=cm):
            tags = entity_tags('Q83495')
        self.assertEqual(tags, frozenset({'movie_tv'}))

    def test_film_industry_class_is_not_a_work(self):
        # "film studio" mentions film but is no movie, the card must not fire.
        cm, _ = _fake_httpx_client([self._claims('Q375336'), self._labels(Q375336='film studio')])
        with patch('cards.wikidata.httpx.Client', return_value=cm):
            tags = entity_tags('Q126399')
        self.assertEqual(tags, frozenset())

    def test_human_is_a_person(self):
        cm, _ = _fake_httpx_client([self._claims('Q5'), self._labels(Q5='human')])
        with patch('cards.wikidata.httpx.Client', return_value=cm):
            tags = entity_tags('Q2263')
        self.assertEqual(tags, frozenset({'person'}))

    def test_commune_label_is_a_plain_place(self):
        # Country-specific locality classes resolve via their label.
        cm, _ = _fake_httpx_client([self._claims('Q484170'), self._labels(Q484170='commune of France')])
        with patch('cards.wikidata.httpx.Client', return_value=cm):
            tags = entity_tags('Q456')
        self.assertEqual(tags, frozenset({'place'}))

    def test_cathedral_label_is_a_travel_place(self):
        cm, _ = _fake_httpx_client([self._claims('Q2977'), self._labels(Q2977='cathedral')])
        with patch('cards.wikidata.httpx.Client', return_value=cm):
            tags = entity_tags('Q2981')
        self.assertEqual(tags, frozenset({'travel_place', 'place'}))

    def test_no_item_id_skips_the_request(self):
        with patch('cards.wikidata.httpx.Client') as mock_client:
            self.assertIsNone(entity_tags(''))
        mock_client.assert_not_called()

    def test_api_error_returns_none_and_is_not_cached(self):
        failing = MagicMock()
        failing.__enter__.return_value.get.side_effect = RuntimeError('boom')
        failing.__exit__.return_value = False
        ok, _ = _fake_httpx_client([self._claims('Q11424'), self._labels(Q11424='film')])
        with patch('cards.wikidata.httpx.Client', side_effect=[failing, ok]):
            self.assertIsNone(entity_tags('Q25188'))
            # The failure wasn't cached, so the retry reaches the API and works.
            self.assertEqual(entity_tags('Q25188'), frozenset({'movie_tv'}))

    def test_classification_cached_across_calls(self):
        cm, client = _fake_httpx_client([self._claims('Q5'), self._labels(Q5='human')])
        with patch('cards.wikidata.httpx.Client', return_value=cm):
            first = entity_tags('Q2263')
            second = entity_tags('Q2263')
        self.assertEqual(first, second)
        # Only the first call hit the network (claims + labels = 2 gets).
        self.assertEqual(client.get.call_count, 2)


class WikidataAdultSubjectTests(TestCase):
    """`is_adult_subject` flags sexually-explicit subjects language-independently."""

    @staticmethod
    def _claims(prop, *ids):
        return {'claims': {prop: [
            {'mainsnak': {'datavalue': {'value': {'id': i}}}} for i in ids
        ]}}

    @staticmethod
    def _labels(**by_id):
        return {'entities': {
            qid: {'labels': {'en': {'value': label}}} for qid, label in by_id.items()
        }}

    def test_concept_flagged_via_subclass_label(self):
        # A sexual act is a *subclass of* (P279) "oral sex", not an instance of
        # anything, so P279 must be consulted. The label carries it regardless
        # of the article's language (this is the Dutch "Fellatio" case).
        payloads = [
            self._claims('P31'),                      # no P31
            self._claims('P279', 'Q315985'),          # subclass of oral sex
            self._labels(Q315985='oral sex'),
        ]
        cm, _ = _fake_httpx_client(payloads)
        with patch('cards.wikidata.httpx.Client', return_value=cm):
            self.assertTrue(is_adult_subject('Q170518'))

    def test_work_flagged_via_instance_label(self):
        # A specific work is an *instance of* "pornographic film".
        payloads = [
            self._claims('P31', 'Q18351550'),
            self._claims('P279'),
            self._labels(Q18351550='pornographic film'),
        ]
        cm, _ = _fake_httpx_client(payloads)
        with patch('cards.wikidata.httpx.Client', return_value=cm):
            self.assertTrue(is_adult_subject('Q12345'))

    def test_innocent_subject_not_flagged(self):
        payloads = [
            self._claims('P31', 'Q22698'),
            self._claims('P279'),
            self._labels(Q22698='park'),
        ]
        cm, _ = _fake_httpx_client(payloads)
        with patch('cards.wikidata.httpx.Client', return_value=cm):
            self.assertFalse(is_adult_subject('Q160409'))

    def test_no_item_id_skips_the_request(self):
        with patch('cards.wikidata.httpx.Client') as mock_client:
            self.assertFalse(is_adult_subject(''))
        mock_client.assert_not_called()

    def test_api_error_fails_open_and_is_not_cached(self):
        # A Wikidata outage must not block cards wholesale, it fails open
        # (False), leaving the cheap title check as the floor, and isn't cached.
        failing = MagicMock()
        failing.__enter__.return_value.get.side_effect = RuntimeError('boom')
        failing.__exit__.return_value = False
        ok, _ = _fake_httpx_client([
            self._claims('P31'), self._claims('P279', 'Q315985'),
            self._labels(Q315985='oral sex'),
        ])
        with patch('cards.wikidata.httpx.Client', side_effect=[failing, ok]):
            self.assertFalse(is_adult_subject('Q170518'))
            self.assertTrue(is_adult_subject('Q170518'))

    def test_result_cached_across_calls(self):
        payloads = [
            self._claims('P31'), self._claims('P279', 'Q315985'),
            self._labels(Q315985='oral sex'),
        ]
        cm, client = _fake_httpx_client(payloads)
        with patch('cards.wikidata.httpx.Client', return_value=cm):
            first = is_adult_subject('Q170518')
            second = is_adult_subject('Q170518')
        self.assertTrue(first)
        self.assertEqual(first, second)
        # Only the first call hit the network (P31 + P279 + labels = 3 gets).
        self.assertEqual(client.get.call_count, 3)


class MovieDetectionTests(TestCase):
    """`_is_movie_or_tv` triggers across languages and rejects people."""

    def test_english_film(self):
        self.assertTrue(_is_movie_or_tv('inception', [], {'description': '2010 science fiction film'}))

    def test_french_series(self):
        self.assertTrue(_is_movie_or_tv('le bureau', [], {'description': 'série télévisée française'}))

    def test_german_series(self):
        self.assertTrue(_is_movie_or_tv('tatort', [], {'description': 'deutsche Fernsehserie'}))

    def test_spanish_series(self):
        self.assertTrue(_is_movie_or_tv('la casa de papel', [], {'description': 'serie de televisión española'}))

    def test_portuguese_film(self):
        self.assertTrue(_is_movie_or_tv('cidade de deus', [], {'description': 'filme brasileiro de 2002'}))

    def test_person_is_not_a_movie_even_with_imdb_link(self):
        # An actor's name must not surface a movie card, even though IMDb links
        # show up in the results.
        self.assertFalse(_is_movie_or_tv(
            'tom hanks',
            [{'url': 'https://www.imdb.com/name/nm0000158/'}],
            {'description': 'American actor'},
        ))

    def test_person_non_english(self):
        self.assertFalse(_is_movie_or_tv('jean dujardin', [], {'description': 'acteur français'}))

    def test_biopic_about_a_person_is_a_movie(self):
        # "film" precedes "singer": the subject is the film, so the card shows.
        self.assertTrue(_is_movie_or_tv(
            'bohemian rhapsody', [],
            {'description': '2018 biographical film about singer Freddie Mercury'},
        ))

    def test_actor_who_also_produces_is_a_person(self):
        # "actor" precedes "film producer": the subject is the person.
        self.assertFalse(_is_movie_or_tv(
            'brad pitt', [],
            {'description': 'American actor and film producer'},
        ))

    def test_domain_fallback_without_wikipedia(self):
        self.assertTrue(_is_movie_or_tv(
            'some obscure title', [{'url': 'https://letterboxd.com/film/x/'}], None,
        ))

    def test_plain_query_is_not_a_movie(self):
        self.assertFalse(_is_movie_or_tv(
            'python tutorial', [{'url': 'https://docs.python.org/3/tutorial/'}], None,
        ))

    def test_wikidata_tag_fires_in_any_language(self):
        # Danish description: "spillefilm" defeats whole-word keyword matching
        # and Danish isn't in the keyword lists, the Wikidata tag still fires.
        card = {'description': 'dansk spillefilm fra 2010'}
        self.assertFalse(_is_movie_or_tv('inception', [], card))
        self.assertTrue(_is_movie_or_tv('inception', [], card, frozenset({'movie_tv'})))

    def test_wikidata_person_tag_blocks_movie(self):
        self.assertFalse(_is_movie_or_tv(
            'jean dujardin', [{'url': 'https://www.imdb.com/name/x/'}],
            {'description': 'fransk skuespiller'}, frozenset({'person'}),
        ))

    def test_wikidata_place_tag_blocks_movie(self):
        self.assertFalse(_is_movie_or_tv(
            'notre dame de paris', [], {'description': 'katedral i paris'},
            frozenset({'place', 'travel_place'}),
        ))


class TravelDetectionTests(TestCase):
    """`_is_travel_query` triggers across languages without substring misfires."""

    def test_cities_do_not_trigger_on_substrings(self):
        self.assertFalse(_is_travel_query('barcelona', [], None))  # not 'bar'
        self.assertFalse(_is_travel_query('spain', [], None))      # not 'spa'

    def test_english_keyword(self):
        self.assertTrue(_is_travel_query('tapas bar in madrid', [], None))
        self.assertTrue(_is_travel_query('where to eat in rome', [], None))

    def test_french_keyword(self):
        self.assertTrue(_is_travel_query('meilleurs restaurants à lyon', [], None))

    def test_german_keyword(self):
        self.assertTrue(_is_travel_query('günstige unterkunft in berlin', [], None))

    def test_wiki_description(self):
        self.assertTrue(_is_travel_query('louvre', [], {'description': 'art museum in Paris'}))

    def test_travel_domain(self):
        self.assertTrue(_is_travel_query('somewhere', [{'url': 'https://www.tripadvisor.com/x'}], None))

    def test_plain_query(self):
        self.assertFalse(_is_travel_query('python tutorial', [], None))

    def test_wikidata_travel_tag_fires_in_any_language(self):
        # Polish description, not in the keyword lists, the tag still fires.
        self.assertTrue(_is_travel_query(
            'hundertwasserhaus', [], {'description': 'budynek w wiedniu'},
            frozenset({'travel_place', 'place'}),
        ))

    def test_wikidata_movie_tag_beats_travel_word_in_a_title(self):
        # A film whose *title* carries a travel word must not call TripAdvisor:
        # the keyword is accounted for by the article's own name.
        self.assertFalse(_is_travel_query(
            'the grand budapest hotel', [],
            {'title': 'The Grand Budapest Hotel', 'description': '2014 film'},
            frozenset({'movie_tv'}),
        ))

    def test_query_travel_intent_beats_a_person_anchor(self):
        # "aki restaurant paris": Wikipedia has no article for the restaurant,
        # so the anchor resolves to a same-named person, the query's explicit
        # "restaurant" still wins.
        self.assertTrue(_is_travel_query(
            'aki restaurant paris', [],
            {'title': 'Aki Kaurismäki', 'description': 'Finnish film director'},
            frozenset({'person'}),
        ))

    def test_query_travel_intent_beats_a_movie_anchor(self):
        # "envie restaurant paris": the anchor resolves to the film "Envie";
        # the travel keyword is not part of that title, so the intent wins.
        self.assertTrue(_is_travel_query(
            'envie restaurant paris', [],
            {'title': 'Envie', 'description': 'film français de 1969'},
            frozenset({'movie_tv'}),
        ))

    def test_wikidata_plain_place_is_for_the_map_not_tripadvisor(self):
        # A bare city must not become a TripAdvisor card, even when booking
        # sites rank in the results.
        self.assertFalse(_is_travel_query(
            'barcelona', [{'url': 'https://www.booking.com/x'}],
            {'description': 'city in Spain'}, frozenset({'place'}),
        ))

    def test_query_keywords_beat_a_place_tag(self):
        # "restaurants in lyon" resolves the Wikipedia card to the *city*, but
        # the query intent is hospitality.
        self.assertTrue(_is_travel_query(
            'restaurants in lyon', [],
            {'title': 'Lyon', 'description': 'city in France'}, frozenset({'place'}),
        ))


class RelevanceTests(TestCase):
    """Pertinence helpers used to drop unrelated cards."""

    def test_significant_tokens_drop_filler(self):
        self.assertEqual(_significant_tokens('best restaurants in lyon'), {'lyon'})
        self.assertEqual(_significant_tokens('The Matrix'), {'matrix'})

    def test_name_relevant(self):
        self.assertTrue(_name_is_relevant('Inception', 'inception'))
        self.assertTrue(_name_is_relevant('The Matrix', 'matrix movie'))
        self.assertTrue(_name_is_relevant('The Lord of the Rings: The Fellowship of the Ring',
                                          'lord of the rings'))

    def test_name_not_relevant(self):
        self.assertFalse(_name_is_relevant('Avatar', 'python tutorial'))
        self.assertFalse(_name_is_relevant('Le Jules Verne', 'best restaurants in paris'))

    def test_shares_significant_token(self):
        self.assertTrue(_shares_significant_token('restaurants in lyon',
                                                  'Rue de la Paix, 69000 Lyon, France'))
        self.assertFalse(_shares_significant_token('best hotel', 'Dubai, UAE'))

    def test_title_match_prefers_exact_over_sequel(self):
        # The query lacks the sequel's "2", so the exact title scores higher.
        q = "le diable s'habille en prada"
        self.assertEqual(_title_match("Le Diable s'habille en Prada", q), 1.0)
        self.assertGreater(
            _title_match("Le Diable s'habille en Prada", q),
            _title_match("Le Diable s'habille en Prada 2", q),
        )

    def test_title_match_ignores_articles(self):
        # "The" must not penalise the match, so "godfather" still maps to it.
        self.assertEqual(_title_match('The Godfather', 'godfather'), 1.0)

    def test_accents_fold_across_languages(self):
        # Keyboards differ: an unaccented query must still match the entity.
        self.assertEqual(_title_match('Les Misérables', 'les miserables'), 1.0)
        self.assertTrue(_name_is_relevant('Crème brûlée', 'creme brulee'))

    def test_plural_and_singular_align(self):
        self.assertTrue(_name_is_relevant('Boiled egg', 'how to boil eggs'))

    def test_fuzzy_fallback_matches_spacing_variants(self):
        # Tokenisations are disjoint ("spiderman" vs "spider"+"man"); the
        # character-level fallback still ties them together.
        self.assertTrue(_name_is_relevant('Spider-Man', 'spiderman'))
        self.assertGreaterEqual(_title_match('Spider-Man', 'spiderman'), 0.8)

    def test_fuzzy_fallback_rejects_unrelated_strings(self):
        self.assertFalse(_name_is_relevant('Avatar', 'quarterly report'))
        self.assertEqual(_title_match('Avatar', 'quarterly report'), 0.0)


def _fake_thetvdb_client(payloads, token='test-token'):
    """Stand-in for ``httpx.Client`` against TheTVDB v4: ``post`` answers the
    /login call with a bearer *token* and ``get`` returns ``{'data': payload}``
    responses yielding *payloads* in order. Returns ``(context_manager,
    client)`` so tests can assert on the recorded calls."""
    client = MagicMock()
    login = MagicMock(status_code=200)
    login.json.return_value = {'data': {'token': token}}
    login.raise_for_status.return_value = None
    client.post.return_value = login
    responses = []
    for payload in payloads:
        resp = MagicMock(status_code=200)
        resp.json.return_value = {'data': payload}
        resp.raise_for_status.return_value = None
        responses.append(resp)
    client.get.side_effect = responses
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = False
    return cm, client


@override_settings(THETVDB_API_KEY='test-key')
class FetchThetvdbTests(TestCase):
    def test_no_api_call_when_not_detected(self):
        with patch('cards.thetvdb.httpx.Client') as mock_client:
            result = fetch_thetvdb(
                'python tutorial', wikipedia_card=None,
                web_results=[{'url': 'https://docs.python.org/'}], lang='',
            )
        self.assertIsNone(result)
        mock_client.assert_not_called()

    @override_settings(THETVDB_API_KEY='')
    def test_no_api_call_without_key(self):
        with patch('cards.thetvdb.httpx.Client') as mock_client:
            result = fetch_thetvdb(
                'inception',
                wikipedia_card={'title': 'Inception', 'description': '2010 film'},
                web_results=[], lang='',
            )
        self.assertIsNone(result)
        mock_client.assert_not_called()

    def test_picks_pertinent_hit_over_top_ranked_unrelated(self):
        search_payload = [
            {'type': 'movie', 'tvdb_id': '1', 'name': 'Unrelated Blockbuster', 'slug': 'unrelated'},
            {'type': 'movie', 'tvdb_id': '2', 'name': 'Inception', 'slug': 'inception'},
        ]
        detail_payload = {
            'name': 'Inception', 'year': '2010', 'slug': 'inception',
            'genres': [{'name': 'Science Fiction'}], 'overview': 'A thief...',
            'status': {'name': 'Released'}, 'characters': [],
        }
        cm, client = _fake_thetvdb_client([search_payload, detail_payload])
        with patch('cards.thetvdb.httpx.Client', return_value=cm):
            card = fetch_thetvdb(
                'inception',
                wikipedia_card={'title': 'Inception', 'description': '2010 film'},
                web_results=[], lang='',
            )
        self.assertIsNotNone(card)
        self.assertEqual(card['title'], 'Inception')
        self.assertEqual(card['url'], 'https://thetvdb.com/movies/inception')
        # The detail call must target the pertinent id (2), not the top hit.
        self.assertIn('/movies/2/extended', client.get.call_args_list[1].args[0])

    def test_drops_card_when_no_hit_is_pertinent(self):
        search_payload = [
            {'type': 'movie', 'tvdb_id': '1', 'name': 'Totally Different', 'slug': 'totally-different'},
        ]
        cm, client = _fake_thetvdb_client([search_payload])
        with patch('cards.thetvdb.httpx.Client', return_value=cm):
            card = fetch_thetvdb(
                'the office', wikipedia_card=None,
                web_results=[{'url': 'https://www.imdb.com/title/x/'}], lang='',
            )
        self.assertIsNone(card)
        # Only the search ran; the detail endpoint was never hit.
        self.assertEqual(client.get.call_count, 1)

    def test_non_film_search_hits_are_ignored(self):
        # The v4 search index also returns people and companies; neither may
        # back a movie/TV card even when the name matches the query exactly.
        search_payload = [
            {'type': 'person', 'tvdb_id': '7', 'name': 'The Office', 'slug': 'the-office-person'},
            {'type': 'company', 'tvdb_id': '8', 'name': 'The Office', 'slug': 'the-office-company'},
        ]
        cm, client = _fake_thetvdb_client([search_payload])
        with patch('cards.thetvdb.httpx.Client', return_value=cm):
            card = fetch_thetvdb(
                'the office', wikipedia_card=None,
                web_results=[{'url': 'https://www.imdb.com/title/x/'}], lang='',
            )
        self.assertIsNone(card)
        self.assertEqual(client.get.call_count, 1)

    def test_exact_title_beats_higher_ranked_sequel(self):
        # Regression: "le diable s'habille en prada" must not surface the buzzier
        # upcoming sequel "… 2" TheTVDB ranked first; the exact title wins.
        search_payload = [
            {'type': 'movie', 'tvdb_id': '2', 'name': "Le Diable s'habille en Prada 2", 'slug': 'prada-2'},
            {'type': 'movie', 'tvdb_id': '1', 'name': "Le Diable s'habille en Prada", 'slug': 'prada'},
        ]
        detail_payload = {
            'name': "Le Diable s'habille en Prada", 'year': '2006', 'slug': 'prada',
            'genres': [{'name': 'Comédie'}], 'status': {'name': 'Released'}, 'characters': [],
        }
        cm, client = _fake_thetvdb_client([search_payload, detail_payload])
        with patch('cards.thetvdb.httpx.Client', return_value=cm):
            card = fetch_thetvdb(
                "le diable s'habille en prada",
                wikipedia_card={'title': "Le Diable s'habille en Prada",
                                'description': 'film américain de 2006'},
                web_results=[], lang='fr',
            )
        self.assertIsNotNone(card)
        self.assertEqual(card['title'], "Le Diable s'habille en Prada")
        self.assertIn('/movies/1/extended', client.get.call_args_list[1].args[0])

    def test_exact_title_wins_even_when_wikipedia_resolved_to_sequel(self):
        # Regression for the French case: Wikipedia's top hit is the buzzy sequel
        # article, so its title carries a "2". Ranking against the *query* (which
        # has no "2") must still pick the original the user asked for.
        search_payload = [
            {'type': 'movie', 'tvdb_id': '2', 'name': "Le Diable s'habille en Prada 2", 'slug': 'prada-2'},
            {'type': 'movie', 'tvdb_id': '1', 'name': "Le Diable s'habille en Prada", 'slug': 'prada'},
        ]
        detail_payload = {
            'name': "Le Diable s'habille en Prada", 'year': '2006', 'slug': 'prada',
            'status': {'name': 'Released'}, 'characters': [],
        }
        cm, client = _fake_thetvdb_client([search_payload, detail_payload])
        with patch('cards.thetvdb.httpx.Client', return_value=cm):
            card = fetch_thetvdb(
                "le diable s'habille en prada",
                wikipedia_card={'title': "Le Diable s'habille en Prada 2",
                                'description': 'film américain de 2026'},
                web_results=[], lang='fr',
            )
        self.assertIsNotNone(card)
        self.assertEqual(card['title'], "Le Diable s'habille en Prada")
        self.assertIn('/movies/1/extended', client.get.call_args_list[1].args[0])

    def test_translation_used_for_title_and_overview(self):
        # The extended record's own name/overview are the record defaults; the
        # search language's translation (meta=translations) wins when present.
        search_payload = [
            {'type': 'series', 'tvdb_id': '81189', 'name': 'Breaking Bad', 'slug': 'breaking-bad',
             'translations': {'fra': 'Breaking Bad'}},
        ]
        detail_payload = {
            'name': 'Breaking Bad', 'year': '2008', 'slug': 'breaking-bad',
            'status': {'name': 'Ended'}, 'characters': [],
            'overview': 'English overview.',
            'translations': {
                'nameTranslations': [
                    {'language': 'eng', 'name': 'Breaking Bad'},
                    {'language': 'fra', 'name': 'Breaking Bad (FR)'},
                ],
                'overviewTranslations': [
                    {'language': 'eng', 'overview': 'English overview.'},
                    {'language': 'fra', 'overview': 'Résumé français.'},
                ],
            },
        }
        cm, _ = _fake_thetvdb_client([search_payload, detail_payload])
        with patch('cards.thetvdb.httpx.Client', return_value=cm):
            card = fetch_thetvdb(
                'breaking bad',
                wikipedia_card={'title': 'Breaking Bad', 'description': 'série télévisée'},
                web_results=[], lang='fr',
            )
        self.assertIsNotNone(card)
        self.assertEqual(card['title'], 'Breaking Bad (FR)')
        self.assertEqual(card['overview'], 'Résumé français.')
        self.assertEqual(card['media_type'], 'series')
        self.assertEqual(card['url'], 'https://thetvdb.com/series/breaking-bad')

    def test_alias_connects_native_language_query(self):
        # A native-language query reaches a differently-named record through its
        # aliases ("la casa de papel" → "Money Heist"), so the hit still counts.
        search_payload = [
            {'type': 'series', 'tvdb_id': '327417', 'name': 'Money Heist',
             'slug': 'money-heist', 'aliases': ['La casa de papel']},
        ]
        detail_payload = {
            'name': 'Money Heist', 'year': '2017', 'slug': 'money-heist',
            'status': {'name': 'Ended'}, 'characters': [],
        }
        cm, _ = _fake_thetvdb_client([search_payload, detail_payload])
        with patch('cards.thetvdb.httpx.Client', return_value=cm):
            card = fetch_thetvdb(
                'la casa de papel',
                wikipedia_card={'title': 'La casa de papel', 'description': 'serie de televisión'},
                web_results=[], lang='es',
            )
        self.assertIsNotNone(card)
        self.assertEqual(card['title'], 'Money Heist')

    def test_cast_and_crew_from_characters(self):
        search_payload = [
            {'type': 'series', 'tvdb_id': '81189', 'name': 'Breaking Bad', 'slug': 'breaking-bad'},
        ]
        detail_payload = {
            'name': 'Breaking Bad', 'year': '2008', 'slug': 'breaking-bad',
            'status': {'name': 'Ended'},
            'characters': [
                {'peopleType': 'Actor', 'personName': 'Aaron Paul', 'name': 'Jesse Pinkman', 'sort': 2},
                {'peopleType': 'Actor', 'personName': 'Bryan Cranston', 'name': 'Walter White',
                 'sort': 1, 'personImgURL': 'https://artworks.thetvdb.com/banners/person/bc.jpg'},
                {'peopleType': 'Writer', 'personName': 'Vince Gilligan'},
                {'peopleType': 'Creator', 'personName': 'Vince Gilligan'},
            ],
        }
        cm, _ = _fake_thetvdb_client([search_payload, detail_payload])
        with patch('cards.thetvdb.httpx.Client', return_value=cm):
            card = fetch_thetvdb(
                'breaking bad',
                wikipedia_card={'title': 'Breaking Bad', 'description': 'TV series'},
                web_results=[], lang='',
            )
        self.assertIsNotNone(card)
        # Billing order follows `sort`, and the actor's photo comes through.
        self.assertEqual([c['name'] for c in card['cast']], ['Bryan Cranston', 'Aaron Paul'])
        self.assertEqual(card['cast'][0]['character'], 'Walter White')
        self.assertEqual(card['cast'][0]['profile'],
                         'https://artworks.thetvdb.com/banners/person/bc.jpg')
        # One person, one crew entry, under the highest-priority role.
        self.assertEqual(card['crew'], [{'name': 'Vince Gilligan', 'job': 'Creator', 'profile': ''}])

    def test_probe_finds_film_when_wikipedia_is_disambiguation(self):
        # "the devil wears prada" → Wikipedia returns a disambiguation page (None
        # card) and no movie domain ranks; the probe still finds the film.
        search_payload = [
            {'type': 'movie', 'tvdb_id': '10', 'name': 'The Devil Wears Prada', 'slug': 'the-devil-wears-prada'},
        ]
        detail_payload = {
            'name': 'The Devil Wears Prada', 'year': '2006', 'slug': 'the-devil-wears-prada',
            'genres': [{'name': 'Comedy'}], 'status': {'name': 'Released'}, 'characters': [],
        }
        cm, _ = _fake_thetvdb_client([search_payload, detail_payload])
        with patch('cards.thetvdb.httpx.Client', return_value=cm):
            card = fetch_thetvdb('the devil wears prada', wikipedia_card=None, web_results=[], lang='')
        self.assertIsNotNone(card)
        self.assertEqual(card['title'], 'The Devil Wears Prada')

    def test_probe_finds_film_when_wikipedia_is_source_novel(self):
        # Wikipedia points at the source novel (no "film" keyword), probe anyway
        # because the query is the article's name, and drop the "(novel)" cruft.
        search_payload = [
            {'type': 'movie', 'tvdb_id': '10', 'name': 'The Devil Wears Prada', 'slug': 'the-devil-wears-prada'},
        ]
        detail_payload = {
            'name': 'The Devil Wears Prada', 'year': '2006', 'slug': 'the-devil-wears-prada',
            'status': {'name': 'Released'}, 'characters': [],
        }
        cm, client = _fake_thetvdb_client([search_payload, detail_payload])
        with patch('cards.thetvdb.httpx.Client', return_value=cm):
            card = fetch_thetvdb(
                'the devil wears prada',
                wikipedia_card={'title': 'The Devil Wears Prada (novel)',
                                'description': '2003 novel by Lauren Weisberger'},
                web_results=[], lang='',
            )
        self.assertIsNotNone(card)
        self.assertEqual(card['title'], 'The Devil Wears Prada')
        # The "(novel)" disambiguator is stripped from the TheTVDB search query.
        self.assertEqual(client.get.call_args_list[0].kwargs['params']['query'],
                         'The Devil Wears Prada')

    def test_probe_not_run_for_person(self):
        with patch('cards.thetvdb.httpx.Client') as mock_client:
            card = fetch_thetvdb(
                'tom hanks',
                wikipedia_card={'title': 'Tom Hanks', 'description': 'American actor'},
                web_results=[{'url': 'https://www.imdb.com/name/nm0000158/'}], lang='',
            )
        self.assertIsNone(card)
        mock_client.assert_not_called()

    def test_probe_not_run_for_informational_query(self):
        with patch('cards.thetvdb.httpx.Client') as mock_client:
            card = fetch_thetvdb('python tutorial', wikipedia_card=None, web_results=[], lang='')
        self.assertIsNone(card)
        mock_client.assert_not_called()

    def test_probe_not_run_for_travel_query(self):
        # A hotel/restaurant search must not waste a TheTVDB probe, TripAdvisor
        # handles it instead.
        with patch('cards.thetvdb.httpx.Client') as mock_client:
            card = fetch_thetvdb('ritz paris hotel', wikipedia_card=None, web_results=[], lang='')
        self.assertIsNone(card)
        mock_client.assert_not_called()

    def test_probe_dropped_when_title_match_is_weak(self):
        # A probe with no movie signal must match the title strictly: a biopic
        # loosely titled "Einstein" is not shown for "albert einstein".
        search_payload = [
            {'type': 'movie', 'tvdb_id': '5', 'name': 'Einstein', 'slug': 'einstein'},
        ]
        cm, client = _fake_thetvdb_client([search_payload])
        with patch('cards.thetvdb.httpx.Client', return_value=cm):
            card = fetch_thetvdb(
                'albert einstein',
                wikipedia_card={'title': 'Albert Einstein',
                                'description': 'German-born theoretical physicist'},
                web_results=[], lang='',
            )
        self.assertIsNone(card)
        # Dropped before the detail endpoint was hit.
        self.assertEqual(client.get.call_count, 1)

    def test_detected_via_wikidata_tags_in_any_language(self):
        # Danish Wikipedia description, no keyword fires, but the subject's
        # Wikidata classes say "film", so the card still triggers.
        search_payload = [
            {'type': 'movie', 'tvdb_id': '2', 'name': 'Inception', 'slug': 'inception'},
        ]
        detail_payload = {
            'name': 'Inception', 'year': '2010', 'slug': 'inception',
            'genres': [{'name': 'Science Fiction'}], 'status': {'name': 'Released'}, 'characters': [],
        }
        cm, _ = _fake_thetvdb_client([search_payload, detail_payload])
        with patch('cards.wikidata.entity_tags', return_value=frozenset({'movie_tv'})) as mock_tags, \
                patch('cards.thetvdb.httpx.Client', return_value=cm):
            card = fetch_thetvdb(
                'inception',
                wikipedia_card={'title': 'Inception', 'wikibase_item': 'Q25188',
                                'description': 'dansk spillefilm fra 2010'},
                web_results=[], lang='',
            )
        self.assertIsNotNone(card)
        self.assertEqual(card['title'], 'Inception')
        mock_tags.assert_called_once_with('Q25188')

    def test_probe_not_run_for_wikidata_person(self):
        # The description gives no role keyword, but Wikidata knows it's a human.
        with patch('cards.wikidata.entity_tags', return_value=frozenset({'person'})), \
                patch('cards.thetvdb.httpx.Client') as mock_client:
            card = fetch_thetvdb(
                'magnus carlsen',
                wikipedia_card={'title': 'Magnus Carlsen', 'wikibase_item': 'Q106807',
                                'description': 'norsk sjakkspiller'},
                web_results=[], lang='',
            )
        self.assertIsNone(card)
        mock_client.assert_not_called()

    def test_travel_intent_query_never_calls_thetvdb(self):
        # The anchor resolved "envie restaurant paris" to the *film* "Envie";
        # a movie card here would be wrong and would keep TripAdvisor (which
        # only runs when TheTVDB found nothing) from ever showing the restaurant.
        with patch('cards.thetvdb.httpx.Client') as mock_client:
            card = fetch_thetvdb(
                'envie restaurant paris',
                wikipedia_card={'title': 'Envie', 'description': 'film français de 1969'},
                web_results=[], lang='fr',
            )
        self.assertIsNone(card)
        mock_client.assert_not_called()

    def test_probe_not_run_for_wikidata_place(self):
        # A cathedral shares its name with several films; the place tag keeps
        # the movie card away without any language-specific keyword.
        with patch('cards.wikidata.entity_tags', return_value=frozenset({'place', 'travel_place'})), \
                patch('cards.thetvdb.httpx.Client') as mock_client:
            card = fetch_thetvdb(
                'notre dame de paris',
                wikipedia_card={'title': 'Notre-Dame de Paris', 'wikibase_item': 'Q2981',
                                'description': 'cathédrale parisienne'},
                web_results=[], lang='fr',
            )
        self.assertIsNone(card)
        mock_client.assert_not_called()

    def test_result_cached_across_calls(self):
        # A repeated search reuses the cached card (1-hour SearchCache) instead
        # of re-hitting the API.
        search_payload = [
            {'type': 'movie', 'tvdb_id': '2', 'name': 'Inception', 'slug': 'inception'},
        ]
        detail_payload = {
            'name': 'Inception', 'year': '2010', 'slug': 'inception',
            'genres': [{'name': 'Science Fiction'}], 'overview': 'A thief...',
            'status': {'name': 'Released'}, 'characters': [],
        }
        wiki = {'title': 'Inception', 'description': '2010 film'}
        cm, client = _fake_thetvdb_client([search_payload, detail_payload])
        with patch('cards.thetvdb.httpx.Client', return_value=cm):
            first = fetch_thetvdb('inception', wikipedia_card=wiki, web_results=[], lang='')
            second = fetch_thetvdb('inception', wikipedia_card=wiki, web_results=[], lang='')
        self.assertEqual(first['title'], 'Inception')
        self.assertEqual(second, first)
        # Only the first call hit the network (one search + one detail = 2 gets).
        self.assertEqual(client.get.call_count, 2)

    def test_negative_result_cached_across_calls(self):
        # A query that yields no card caches the miss, so it isn't re-probed.
        search_payload = [
            {'type': 'movie', 'tvdb_id': '1', 'name': 'Totally Different', 'slug': 'totally-different'},
        ]
        cm, client = _fake_thetvdb_client([search_payload])
        with patch('cards.thetvdb.httpx.Client', return_value=cm):
            first = fetch_thetvdb('the office', wikipedia_card=None,
                                  web_results=[{'url': 'https://www.imdb.com/title/x/'}], lang='')
            second = fetch_thetvdb('the office', wikipedia_card=None,
                                   web_results=[{'url': 'https://www.imdb.com/title/x/'}], lang='')
        self.assertIsNone(first)
        self.assertIsNone(second)
        # The cached miss means the API was only probed once.
        self.assertEqual(client.get.call_count, 1)

    def test_bearer_token_reused_across_lookups(self):
        # The one-month bearer token is cached, so a second (different) query
        # reuses it instead of logging in again on every search.
        payloads = [
            [{'type': 'movie', 'tvdb_id': '2', 'name': 'Inception', 'slug': 'inception'}],
            {'name': 'Inception', 'year': '2010', 'slug': 'inception',
             'status': {'name': 'Released'}, 'characters': []},
            [{'type': 'movie', 'tvdb_id': '3', 'name': 'Interstellar', 'slug': 'interstellar'}],
            {'name': 'Interstellar', 'year': '2014', 'slug': 'interstellar',
             'status': {'name': 'Released'}, 'characters': []},
        ]
        cm, client = _fake_thetvdb_client(payloads)
        with patch('cards.thetvdb.httpx.Client', return_value=cm):
            first = fetch_thetvdb('inception', wikipedia_card={'title': 'Inception', 'description': '2010 film'},
                                  web_results=[], lang='')
            second = fetch_thetvdb('interstellar', wikipedia_card={'title': 'Interstellar', 'description': '2014 film'},
                                   web_results=[], lang='')
        self.assertEqual(first['title'], 'Inception')
        self.assertEqual(second['title'], 'Interstellar')
        self.assertEqual(client.post.call_count, 1)

    def test_stale_token_refreshed_and_retried_once(self):
        # A cached token TheTVDB no longer accepts (key reissued) yields a 401;
        # the fetcher logs in again and retries instead of dropping the card
        # until the token cache expires.
        from search.cache import _cache_set as cache_set
        from search.cache import _make_cache_key as make_key
        cache_set(make_key('thetvdb-token', 'test-key', '', 1, '', '', ''), {'token': 'stale'})

        denied = MagicMock(status_code=401)
        search_ok = MagicMock(status_code=200)
        search_ok.json.return_value = {'data': [
            {'type': 'movie', 'tvdb_id': '2', 'name': 'Inception', 'slug': 'inception'},
        ]}
        search_ok.raise_for_status.return_value = None
        detail_ok = MagicMock(status_code=200)
        detail_ok.json.return_value = {'data': {
            'name': 'Inception', 'year': '2010', 'slug': 'inception',
            'status': {'name': 'Released'}, 'characters': [],
        }}
        detail_ok.raise_for_status.return_value = None

        client = MagicMock()
        login = MagicMock(status_code=200)
        login.json.return_value = {'data': {'token': 'fresh-token'}}
        login.raise_for_status.return_value = None
        client.post.return_value = login
        client.get.side_effect = [denied, search_ok, detail_ok]
        cm = MagicMock()
        cm.__enter__.return_value = client
        cm.__exit__.return_value = False

        with patch('cards.thetvdb.httpx.Client', return_value=cm):
            card = fetch_thetvdb(
                'inception',
                wikipedia_card={'title': 'Inception', 'description': '2010 film'},
                web_results=[], lang='',
            )
        self.assertIsNotNone(card)
        self.assertEqual(card['title'], 'Inception')
        # Exactly one re-login, and the retried search used the fresh token.
        self.assertEqual(client.post.call_count, 1)
        retry_headers = client.get.call_args_list[1].kwargs['headers']
        self.assertEqual(retry_headers['Authorization'], 'Bearer fresh-token')


@override_settings(TRIPADVISOR_API_KEY='test-key')
class FetchTripadvisorTests(TestCase):
    """The card runs on Tripadvisor's Terra Partner API catalog endpoints:
    `GET /catalog/locations/search` for the query, `GET /catalog/locations/{id}`
    for the Location it picks. Both authenticate with an `X-API-Key` header and
    return the catalog projection, whose localised fields are one entry per
    language."""

    @staticmethod
    def _location(loc_id, name, *, address='', geo='', rating=None, reviews=0,
                  description='', language='en'):
        """A catalog Location, in the shape the two catalog endpoints return."""
        loc = {
            'id': loc_id,
            'names': [{'language': language, 'value': name, 'primary': True}],
            'descriptions': [{'language': language, 'value': description}] if description else [],
            'geo': geo,
            'geo_id': 1,
            'urls': {'tripadvisor': {'main': f'https://www.tripadvisor.com/-d{loc_id}'}},
        }
        if address:
            loc['addresses'] = [{'language': language, 'formatted': address}]
        if rating is not None:
            loc['overall_rating'] = {'rating': rating, 'count': reviews}
        return loc

    @classmethod
    def _hits(cls, *locations):
        """A `PageSearchCatalogLocation` page wrapping *locations*."""
        return {
            'data': [{'location': loc,
                      'matched_value': {'language': 'en', 'value': loc['names'][0]['value']}}
                     for loc in locations],
            'pagination': {'page': 1, 'size': 20,
                           'total_elements': len(locations), 'total_pages': 1},
        }

    def test_rejected_request_logs_the_api_s_problem_detail(self):
        # Every error the API returns is an application/problem+json document
        # naming the parameter it rejected. Without it a 400 reads only as
        # "Client error '400 Bad Request'", which says nothing about which
        # parameter to fix.
        problem = {
            'status': 400, 'title': 'Parameter is not valid',
            'detail': 'The field locale is invalid', 'trace_id': 'f128ba49e',
            'field_errors': [{'field': 'locale', 'rejected_value': 'fr',
                              'message': "Unsupported factual locale 'fr'"}],
        }
        response = MagicMock()
        response.json.return_value = problem
        cm, client = _fake_httpx_client([])
        client.get.side_effect = httpx.HTTPStatusError(
            "Client error '400 Bad Request'", request=MagicMock(), response=response,
        )
        with patch('cards.tripadvisor.httpx.Client', return_value=cm), \
                self.assertLogs('cards.tripadvisor', level='WARNING') as logs:
            card = fetch_tripadvisor('restaurants in paris', wikipedia_card=None,
                                     web_results=[], lang='fr')
        self.assertIsNone(card)
        logged = '\n'.join(logs.output)
        self.assertIn('Parameter is not valid', logged)
        self.assertIn("locale='fr'", logged)
        self.assertIn("Unsupported factual locale 'fr'", logged)
        self.assertIn('trace_id=f128ba49e', logged)

    def test_no_api_call_when_not_travel(self):
        with patch('cards.tripadvisor.httpx.Client') as mock_client:
            result = fetch_tripadvisor('python tutorial', wikipedia_card=None, web_results=[], lang='')
        self.assertIsNone(result)
        mock_client.assert_not_called()

    def test_sends_key_as_header_and_locales_in_priority_order(self):
        # The Terra API authenticates with a header, the key must never travel
        # in the query string (it would be logged), and `locale` is a priority
        # list: the search language, then English behind it. Both are
        # Tripadvisor's own locale codes -- a bare "fr" or "en" is not in its
        # vocabulary and gets the whole search rejected with a 400.
        location = self._location(42, 'Le Jules Verne', address='Avenue Gustave Eiffel, Paris',
                                  geo='Paris', rating=4.5, reviews=1200, language='fr')
        cm, client = _fake_httpx_client([self._hits(location), location])
        with patch('cards.tripadvisor.httpx.Client', return_value=cm) as mock_client:
            card = fetch_tripadvisor('restaurants in paris', wikipedia_card=None,
                                     web_results=[], lang='fr')
        self.assertIsNotNone(card)
        self.assertEqual(mock_client.call_args.kwargs['headers'], {'X-API-Key': 'test-key'})
        search_params = client.get.call_args_list[0].kwargs['params']
        self.assertEqual(search_params['locale'], ['fr-FR', 'en-US'])
        self.assertNotIn('key', search_params)
        self.assertEqual(client.get.call_args_list[1].kwargs['params']['locale'],
                         ['fr-FR', 'en-US'])

    def test_every_locale_the_card_can_send_is_supported(self):
        # An unsupported `locale` doesn't cost one field or one card, it is a
        # 400 on every travel search at once, so nothing the card can put on
        # the wire is left to chance: each search language the app offers, plus
        # the arbitrary `?lang=` codes that reach the card unvalidated, must
        # resolve to locales Tripadvisor publishes, English last as the
        # catch-all that carries a language the card doesn't localise to.
        for lang in (*preferences.LANG_CHOICES, 'pl', 'xx', ''):
            with self.subTest(lang=lang):
                locales = _locales(lang)
                self.assertEqual(set(locales) - _TA_LOCALES, set())
                self.assertEqual(locales[-1], 'en-US')

    def test_country_code_comes_from_the_search_language(self):
        location = self._location(42, 'Le Jules Verne', address='Avenue Gustave Eiffel, Paris',
                                  geo='Paris', rating=4.5, reviews=1200, language='fr')
        cm, client = _fake_httpx_client([self._hits(location), location])
        with patch('cards.tripadvisor.httpx.Client', return_value=cm):
            fetch_tripadvisor('restaurants in paris', wikipedia_card=None,
                              web_results=[], lang='fr')
        self.assertEqual(client.get.call_args_list[0].kwargs['params']['country_code'], 'FR')

    def test_no_country_filter_without_a_search_language(self):
        # *Any language* says nothing about where the searcher means, so the
        # search goes out unfiltered rather than guessing a country.
        location = self._location(2, 'Eiffel Tower', address='Champ de Mars, Paris', geo='Paris',
                                  rating=4.5, reviews=140000)
        cm, client = _fake_httpx_client([self._hits(location), location])
        with patch('cards.tripadvisor.httpx.Client', return_value=cm):
            fetch_tripadvisor('eiffel tower', wikipedia_card=None,
                              web_results=[{'url': 'https://www.tripadvisor.com/x'}], lang='')
        search_params = client.get.call_args_list[0].kwargs['params']
        self.assertNotIn('country_code', search_params)
        self.assertEqual(search_params['locale'], ['en-US'])

    def test_country_filtered_search_retries_unfiltered(self):
        # The country is a hint, not a fact: a French-language search for a New
        # York landmark finds nothing in France, and must not come back empty.
        location = self._location(3, 'Central Park', address='59th St, New York',
                                  geo='New York City', rating=4.8, reviews=1000, language='fr')
        cm, client = _fake_httpx_client([self._hits(), self._hits(location), location])
        with patch('cards.tripadvisor.httpx.Client', return_value=cm):
            card = fetch_tripadvisor(
                'central park new york', wikipedia_card=None,
                web_results=[{'url': 'https://www.tripadvisor.com/x'}], lang='fr',
            )
        self.assertIsNotNone(card)
        self.assertEqual(card['name'], 'Central Park')
        self.assertEqual(client.get.call_args_list[0].kwargs['params']['country_code'], 'FR')
        self.assertNotIn('country_code', client.get.call_args_list[1].kwargs['params'])

    def test_country_filtered_hit_that_only_half_matches_loses_to_the_wider_search(self):
        # The nastier half of the same problem: filtering to France does not
        # come back empty, it comes back with a Paris café called "Central
        # Park", which clears the pertinence gate on its name alone. It covers
        # only half the query, so the unfiltered search runs too and the New
        # York landmark, which covers all of it, takes the card.
        paris = self._location(8, 'Central Park', address='11 Rue de Paris', geo='Paris',
                               rating=4.0, reviews=90, language='fr')
        new_york = self._location(3, 'Central Park', address='59th St, New York',
                                  geo='New York City', rating=4.8, reviews=1000, language='fr')
        cm, client = _fake_httpx_client(
            [self._hits(paris), self._hits(new_york, paris), new_york],
        )
        with patch('cards.tripadvisor.httpx.Client', return_value=cm):
            card = fetch_tripadvisor(
                'central park new york', wikipedia_card=None,
                web_results=[{'url': 'https://www.tripadvisor.com/x'}], lang='fr',
            )
        self.assertIsNotNone(card)
        self.assertEqual(card['geo'], 'New York City')
        self.assertIn('/catalog/locations/3', client.get.call_args_list[2].args[0])

    def test_country_filtered_hit_covering_the_query_skips_the_retry(self):
        # The win the filter is there for: one search, no widening, because the
        # narrowed hit already accounts for the whole query.
        aki = self._location(11, 'Aki', address='11bis Rue Sainte-Anne, 75001 Paris France',
                             geo='Paris', rating=4.5, reviews=1500, language='fr')
        cm, client = _fake_httpx_client([self._hits(aki), aki])
        with patch('cards.tripadvisor.httpx.Client', return_value=cm):
            card = fetch_tripadvisor('aki restaurant paris', wikipedia_card=None,
                                     web_results=[], lang='fr')
        self.assertIsNotNone(card)
        self.assertEqual(client.get.call_count, 2)  # search + Location, no retry

    def test_category_query_matched_by_address(self):
        location = self._location(42, 'Le Jules Verne',
                                  address='Avenue Gustave Eiffel, 75007 Paris', geo='Paris',
                                  rating=4.5, reviews=1200)
        cm, _ = _fake_httpx_client([self._hits(location), location])
        with patch('cards.tripadvisor.httpx.Client', return_value=cm):
            card = fetch_tripadvisor('restaurants in paris', wikipedia_card=None,
                                     web_results=[], lang='')
        self.assertIsNotNone(card)
        self.assertEqual(card['name'], 'Le Jules Verne')

    def test_maps_the_catalog_projection_onto_the_card(self):
        location = self._location(
            2, 'Eiffel Tower', address='Champ de Mars, 75007 Paris, France', geo='Paris',
            rating=4.5, reviews=140000, description='A wrought-iron lattice tower.',
        )
        location['coordinates'] = {'latitude': 48.85837, 'longitude': 2.294481}
        cm, _ = _fake_httpx_client([self._hits(location), location])
        with patch('cards.tripadvisor.httpx.Client', return_value=cm):
            card = fetch_tripadvisor(
                'eiffel tower', wikipedia_card=None,
                web_results=[{'url': 'https://www.tripadvisor.com/x'}], lang='',
            )
        self.assertEqual(card, {
            'name': 'Eiffel Tower',
            'geo': 'Paris',
            'address': 'Champ de Mars, 75007 Paris, France',
            'rating': 4.5,
            'rating_bubbles': ['full', 'full', 'full', 'full', 'half'],
            'num_reviews': 140000,
            'description': 'A wrought-iron lattice tower.',
            'url': 'https://www.tripadvisor.com/-d2',
            'latitude': 48.85837,
            'longitude': 2.294481,
        })

    def test_picks_the_translation_for_the_search_language(self):
        # One entry per language: the search language wins over the primary
        # English name, and the description follows the same order.
        location = self._location(5, 'Cologne Cathedral', address='Domkloster 4, Cologne',
                                  geo='Cologne', rating=4.8, reviews=30000,
                                  description='A Gothic cathedral.')
        location['names'].append({'language': 'de', 'value': 'Kölner Dom', 'primary': True})
        location['descriptions'].append({'language': 'de', 'value': 'Eine gotische Kathedrale.'})
        location['addresses'].append({'language': 'de', 'formatted': 'Domkloster 4, 50667 Köln'})
        cm, _ = _fake_httpx_client([self._hits(location), location])
        with patch('cards.tripadvisor.httpx.Client', return_value=cm):
            card = fetch_tripadvisor(
                'kölner dom', wikipedia_card=None,
                web_results=[{'url': 'https://www.tripadvisor.com/x'}], lang='de',
            )
        self.assertIsNotNone(card)
        self.assertEqual(card['name'], 'Kölner Dom')
        self.assertEqual(card['description'], 'Eine gotische Kathedrale.')
        self.assertEqual(card['address'], 'Domkloster 4, 50667 Köln')

    def test_falls_back_to_another_language_when_untranslated(self):
        # Nothing in Dutch, so the primary entry carries the card rather than
        # leaving it blank.
        location = self._location(6, 'Wawel Castle', address='Wawel 5, Kraków', geo='Krakow',
                                  rating=4.7, reviews=21000, language='en')
        cm, _ = _fake_httpx_client([self._hits(location), location])
        with patch('cards.tripadvisor.httpx.Client', return_value=cm):
            card = fetch_tripadvisor(
                'wawel castle', wikipedia_card=None,
                web_results=[{'url': 'https://www.tripadvisor.com/x'}], lang='nl',
            )
        self.assertIsNotNone(card)
        self.assertEqual(card['name'], 'Wawel Castle')

    def test_prefers_name_matching_location(self):
        other = self._location(1, 'Some Other Place', address='Nowhere', geo='Nowhere')
        tower = self._location(2, 'Eiffel Tower', address='Champ de Mars, Paris', geo='Paris',
                               rating=4.5, reviews=140000)
        cm, client = _fake_httpx_client([self._hits(other, tower), tower])
        with patch('cards.tripadvisor.httpx.Client', return_value=cm):
            card = fetch_tripadvisor(
                'eiffel tower', wikipedia_card=None,
                web_results=[{'url': 'https://www.tripadvisor.com/x'}], lang='',
            )
        self.assertIsNotNone(card)
        self.assertEqual(card['name'], 'Eiffel Tower')
        # Chose the name-matching location (id 2), not the first result.
        self.assertIn('/catalog/locations/2', client.get.call_args_list[1].args[0])

    def test_drops_generic_query_with_no_location(self):
        hotel = self._location(7, 'Grand Plaza Hotel', address='Dubai', geo='Dubai', rating=4.0)
        cm, client = _fake_httpx_client([self._hits(hotel)])
        with patch('cards.tripadvisor.httpx.Client', return_value=cm):
            card = fetch_tripadvisor('best hotel', wikipedia_card=None, web_results=[], lang='')
        self.assertIsNone(card)
        # Nothing matched, so the Location endpoint was never hit.
        self.assertEqual(client.get.call_count, 1)

    def test_picks_closest_name_not_first_qualifying(self):
        # Both names relate to the search, but the second is the *closest*
        # match, first-past-the-gate is not good enough.
        deck = self._location(1, 'Eiffel Tower Viewing Deck',
                              address='Champ de Mars, Paris', geo='Paris')
        tower = self._location(2, 'Eiffel Tower', address='Champ de Mars, Paris', geo='Paris',
                               rating=4.5, reviews=140000)
        cm, client = _fake_httpx_client([self._hits(deck, tower), tower])
        with patch('cards.tripadvisor.httpx.Client', return_value=cm):
            card = fetch_tripadvisor(
                'eiffel tower', wikipedia_card=None,
                web_results=[{'url': 'https://www.tripadvisor.com/x'}], lang='',
            )
        self.assertIsNotNone(card)
        self.assertEqual(card['name'], 'Eiffel Tower')
        self.assertIn('/catalog/locations/2', client.get.call_args_list[1].args[0])

    def test_detected_via_wikidata_tags_in_any_language(self):
        # No travel keyword in any list ("zamek królewski"), but Wikidata says
        # the subject is a castle, the card triggers and the place matches.
        castle = self._location(9, 'Wawel Castle', address='Wawel 5, 31-001 Kraków, Poland',
                                geo='Krakow', rating=4.7, reviews=21000)
        cm, _ = _fake_httpx_client([self._hits(castle), castle])
        with patch('cards.wikidata.entity_tags', return_value=frozenset({'travel_place', 'place'})) as mock_tags, \
                patch('cards.tripadvisor.httpx.Client', return_value=cm):
            card = fetch_tripadvisor(
                'wawel castle',
                wikipedia_card={'title': 'Wawel Castle', 'wikibase_item': 'Q1058371',
                                'description': 'zamek królewski w krakowie'},
                web_results=[], lang='',
            )
        self.assertIsNotNone(card)
        self.assertEqual(card['name'], 'Wawel Castle')
        mock_tags.assert_called_once_with('Q1058371')

    def test_named_restaurant_beats_city_geo_hit(self):
        # Regression: "aki restaurant paris", TripAdvisor lists the city's
        # own geo entry first, and it ties the one-word venue name on
        # closeness (each matches one query token). The venue's address
        # covers the city token too, so by coverage the venue wins; the
        # search is also filtered to category=RESTAURANT so geo entries
        # drop out of real responses entirely.
        city = self._location(187147, 'Paris', address='Ile-de-France, France', geo='France')
        aki = self._location(11, 'Aki', address='11bis Rue Sainte-Anne, 75001 Paris France',
                             geo='Paris', rating=4.5, reviews=1500)
        cm, client = _fake_httpx_client([self._hits(city, aki), aki])
        with patch('cards.tripadvisor.httpx.Client', return_value=cm):
            card = fetch_tripadvisor('aki restaurant paris', wikipedia_card=None,
                                     web_results=[], lang='fr')
        self.assertIsNotNone(card)
        self.assertEqual(card['name'], 'Aki')
        self.assertIn('/catalog/locations/11', client.get.call_args_list[1].args[0])
        self.assertEqual(client.get.call_args_list[0].kwargs['params']['category'],
                         'RESTAURANT')

    def test_no_category_filter_for_ambiguous_or_plain_queries(self):
        # "spa hotel" names two categories and "eiffel tower" names none,
        # both searches stay unfiltered.
        tower = self._location(2, 'Eiffel Tower', address='Champ de Mars, Paris', geo='Paris',
                               rating=4.5, reviews=140000)
        cm, client = _fake_httpx_client([self._hits(tower), tower])
        with patch('cards.tripadvisor.httpx.Client', return_value=cm):
            fetch_tripadvisor('eiffel tower', wikipedia_card=None,
                              web_results=[{'url': 'https://www.tripadvisor.com/x'}], lang='')
        self.assertNotIn('category', client.get.call_args_list[0].kwargs['params'])
        self.assertEqual(_query_category('spa hotel in baden'), '')
        self.assertEqual(_query_category('aki restaurant paris'), 'RESTAURANT')
        self.assertEqual(_query_category('où dormir à lyon'), 'HOTEL')
        self.assertEqual(_query_category('musées florence'), 'ATTRACTION')

    def test_restaurant_query_survives_same_named_person_anchor(self):
        # Regression: "aki restaurant paris", Wikipedia resolves to a
        # same-named person (tagged via Wikidata), but the query's explicit
        # travel intent must still produce the restaurant's card.
        aki = self._location(11, 'Aki', address='11bis Rue Sainte-Anne, 75001 Paris France',
                             geo='Paris', rating=4.5, reviews=1500)
        cm, _ = _fake_httpx_client([self._hits(aki), aki])
        with patch('cards.wikidata.entity_tags', return_value=frozenset({'person'})), \
                patch('cards.tripadvisor.httpx.Client', return_value=cm):
            card = fetch_tripadvisor(
                'aki restaurant paris',
                wikipedia_card={'title': 'Aki Kaurismäki', 'wikibase_item': 'Q53026',
                                'description': 'Finnish film director'},
                web_results=[], lang='fr',
            )
        self.assertIsNotNone(card)
        self.assertEqual(card['name'], 'Aki')


class FetchStackExchangeTests(TestCase):
    def test_no_api_call_when_not_a_programming_question(self):
        with patch('cards.stackexchange.httpx.Client') as mock_client:
            result = fetch_stackexchange('best restaurants in lyon', web_results=[], lang='')
        self.assertIsNone(result)
        mock_client.assert_not_called()

    def test_picks_pertinent_question_and_its_top_answer(self):
        search_payload = {'items': [
            {'question_id': 1, 'title': 'Unrelated question about Java generics',
             'link': 'https://stackoverflow.com/q/1', 'score': 900, 'answer_count': 3,
             'view_count': 50000, 'is_answered': True, 'tags': ['java']},
            {'question_id': 2, 'title': 'How do I reverse a string in Python?',
             'link': 'https://stackoverflow.com/q/2', 'score': 2847, 'answer_count': 47,
             'view_count': 1200000, 'is_answered': True, 'tags': ['python', 'string']},
        ]}
        answer_payload = {'items': [
            {'answer_id': 10, 'score': 5841, 'is_accepted': True,
             'body': '<p>Use slice notation: <code>my_string[::-1]</code></p>'},
        ]}
        cm, client = _fake_httpx_client([search_payload, answer_payload])
        with patch('cards.stackexchange.httpx.Client', return_value=cm):
            card = fetch_stackexchange('how do I reverse a string in python', web_results=[], lang='')
        self.assertIsNotNone(card)
        self.assertEqual(card['title'], 'How do I reverse a string in Python?')
        self.assertEqual(card['answer']['is_accepted'], True)
        self.assertIn('slice notation', card['answer']['body'])
        # The answers call must target the pertinent question (id 2), not the first hit.
        self.assertIn('/questions/2/answers', client.get.call_args_list[1].args[0])

    def test_ranks_best_title_match_over_loosely_related_first_hit(self):
        # Regression: the API's relevance sort can return a loosely-related
        # question ("how to install ... python", sharing only how/python) above
        # the obvious match; the card must surface the best title match, not the
        # first hit that clears the pertinence gate.
        search_payload = {'items': [
            {'question_id': 1, 'title': 'How do I install the yaml package for Python?',
             'link': 'https://stackoverflow.com/q/1', 'score': 800, 'answer_count': 5,
             'view_count': 300000, 'is_answered': True, 'tags': ['python', 'yaml']},
            {'question_id': 2, 'title': 'How to run a server in python',
             'link': 'https://stackoverflow.com/q/2', 'score': 120, 'answer_count': 4,
             'view_count': 90000, 'is_answered': True, 'tags': ['python', 'server']},
        ]}
        answer_payload = {'items': [
            {'answer_id': 20, 'score': 64, 'is_accepted': True,
             'body': '<p>Use <code>http.server</code>.</p>'},
        ]}
        cm, client = _fake_httpx_client([search_payload, answer_payload])
        with patch('cards.stackexchange.httpx.Client', return_value=cm):
            card = fetch_stackexchange('how to run a python server', web_results=[], lang='')
        self.assertIsNotNone(card)
        self.assertEqual(card['title'], 'How to run a server in python')
        # The answers call must target the best-matching question (id 2).
        self.assertIn('/questions/2/answers', client.get.call_args_list[1].args[0])

    def test_drops_card_when_no_hit_is_pertinent(self):
        search_payload = {'items': [
            {'question_id': 5, 'title': 'Totally unrelated question about COBOL mainframes',
             'link': 'https://stackoverflow.com/q/5', 'score': 10, 'answer_count': 1,
             'view_count': 100, 'is_answered': True, 'tags': ['cobol']},
        ]}
        cm, client = _fake_httpx_client([search_payload])
        with patch('cards.stackexchange.httpx.Client', return_value=cm):
            card = fetch_stackexchange('python typeerror nonetype object is not subscriptable',
                                       web_results=[], lang='')
        self.assertIsNone(card)
        # Only the search ran; the answers endpoint was never hit.
        self.assertEqual(client.get.call_count, 1)

    def test_no_answer_call_when_question_has_no_answers(self):
        search_payload = {'items': [
            {'question_id': 7, 'title': 'How do I reverse a string in Python?',
             'link': 'https://stackoverflow.com/q/7', 'score': 3, 'answer_count': 0,
             'view_count': 20, 'is_answered': False, 'tags': ['python']},
        ]}
        cm, client = _fake_httpx_client([search_payload])
        with patch('cards.stackexchange.httpx.Client', return_value=cm):
            card = fetch_stackexchange('how do I reverse a string in python', web_results=[], lang='')
        self.assertIsNotNone(card)
        self.assertIsNone(card['answer'])
        self.assertEqual(client.get.call_count, 1)

    def test_uses_stack_exchange_site_from_web_results(self):
        # A Super User result in the web list routes both API calls to that site
        # and labels the card accordingly, instead of defaulting to SO.
        search_payload = {'items': [
            {'question_id': 9, 'title': 'How to fix a stuck Windows Update?',
             'link': 'https://superuser.com/q/9', 'score': 50, 'answer_count': 2,
             'view_count': 4000, 'is_answered': True, 'tags': ['windows']},
        ]}
        answer_payload = {'items': [
            {'answer_id': 90, 'score': 12, 'is_accepted': True,
             'link': 'https://superuser.com/a/90', 'body': '<p>Run <code>wuauclt</code>.</p>'},
        ]}
        cm, client = _fake_httpx_client([search_payload, answer_payload])
        with patch('cards.stackexchange.httpx.Client', return_value=cm):
            card = fetch_stackexchange(
                'how to fix a stuck windows update',
                web_results=[{'url': 'https://superuser.com/questions/9/stuck-update'}], lang='',
            )
        self.assertIsNotNone(card)
        self.assertEqual(card['site_name'], 'Super User')
        self.assertEqual(client.get.call_args_list[0].kwargs['params']['site'], 'superuser')
        self.assertEqual(client.get.call_args_list[1].kwargs['params']['site'], 'superuser')

    def test_sanitizes_answer_html_keeping_code_dropping_scripts(self):
        search_payload = {'items': [
            {'question_id': 3, 'title': 'How do I reverse a string in Python?',
             'link': 'https://stackoverflow.com/q/3', 'score': 10, 'answer_count': 1,
             'view_count': 100, 'is_answered': True, 'tags': ['python']},
        ]}
        answer_payload = {'items': [
            {'answer_id': 30, 'score': 5, 'is_accepted': True,
             'body': '<p>Use <code>s[::-1]</code></p>'
                     '<pre><code>print(s[::-1])</code></pre>'
                     '<script>alert(1)</script>'
                     '<a href="javascript:alert(2)">x</a>'
                     '<a href="https://docs.python.org">docs</a>'},
        ]}
        cm, _ = _fake_httpx_client([search_payload, answer_payload])
        with patch('cards.stackexchange.httpx.Client', return_value=cm):
            card = fetch_stackexchange('how do I reverse a string in python', web_results=[], lang='')
        body = card['answer']['body']
        self.assertIn('<pre><code>print(s[::-1])</code></pre>', body)  # code block kept
        self.assertIn('<code>s[::-1]</code>', body)                    # inline code kept
        self.assertNotIn('<script', body)                             # script tag dropped
        self.assertNotIn('alert(1)', body)                            # script body dropped
        self.assertNotIn('javascript:', body)                         # unsafe href neutralised
        self.assertIn('https://docs.python.org', body)                # safe link kept

    def test_long_answer_is_flagged_for_read_more(self):
        search_payload = {'items': [
            {'question_id': 4, 'title': 'How do I reverse a string in Python?',
             'link': 'https://stackoverflow.com/q/4', 'score': 10, 'answer_count': 1,
             'view_count': 100, 'is_answered': True, 'tags': ['python']},
        ]}
        answer_payload = {'items': [
            {'answer_id': 40, 'score': 5, 'is_accepted': True,
             'link': 'https://stackoverflow.com/a/40', 'body': '<p>' + 'word ' * 120 + '</p>'},
        ]}
        cm, _ = _fake_httpx_client([search_payload, answer_payload])
        with patch('cards.stackexchange.httpx.Client', return_value=cm):
            card = fetch_stackexchange('how do I reverse a string in python', web_results=[], lang='')
        self.assertTrue(card['answer']['is_long'])
        self.assertEqual(card['answer']['url'], 'https://stackoverflow.com/a/40')


class FetchWikipediaTests(TestCase):
    SEARCH = {'query': {'search': [{'title': 'Disneyland'}]}}

    @staticmethod
    def _summary(**extra):
        data = {
            'type': 'standard',
            'title': 'Disneyland',
            'description': 'theme park in California',
            'extract': 'Disneyland is a theme park...',
            'content_urls': {'desktop': {'page': 'https://en.wikipedia.org/wiki/Disneyland'}},
        }
        data.update(extra)
        return data

    def test_uses_summary_image_and_skips_fallback(self):
        summary = self._summary(thumbnail={'source': 'https://upload.wikimedia.org/thumb.jpg', 'width': 320})
        cm, client = _fake_httpx_client([self.SEARCH, summary])
        with patch('cards.wikipedia.httpx.Client', return_value=cm):
            card = _fetch_wikipedia('disneyland')
        self.assertEqual(card['thumbnail'], 'https://upload.wikimedia.org/thumb.jpg')
        # No image gap, so the Action API fallback must not be called.
        self.assertEqual(client.get.call_count, 2)

    def test_falls_back_to_pageimages_when_summary_has_no_image(self):
        # Summary is a standard article but exposes no (free) image -- the
        # Disneyland case, whose lead image is a non-free logo.
        pageimages = {'query': {'pages': {'1234': {
            'original': {'source': 'https://upload.wikimedia.org/logo.png', 'width': 400, 'height': 300},
        }}}}
        cm, client = _fake_httpx_client([self.SEARCH, self._summary(), pageimages])
        with patch('cards.wikipedia.httpx.Client', return_value=cm):
            card = _fetch_wikipedia('disneyland')
        self.assertEqual(card['thumbnail'], 'https://upload.wikimedia.org/logo.png')
        # The fallback must ask the Action API for non-free images.
        self.assertEqual(client.get.call_count, 3)
        params = client.get.call_args_list[2].kwargs['params']
        self.assertEqual(params['pilicense'], 'any')
        self.assertEqual(params['prop'], 'pageimages')

    def test_fallback_prefers_thumbnail_when_original_too_large(self):
        pageimages = {'query': {'pages': {'1234': {
            'original': {'source': 'https://upload.wikimedia.org/huge.png', 'width': 5000, 'height': 4000},
            'thumbnail': {'source': 'https://upload.wikimedia.org/sized.png', 'width': 640},
        }}}}
        cm, _ = _fake_httpx_client([self.SEARCH, self._summary(), pageimages])
        with patch('cards.wikipedia.httpx.Client', return_value=cm):
            card = _fetch_wikipedia('disneyland')
        self.assertEqual(card['thumbnail'], 'https://upload.wikimedia.org/sized.png')

    def test_falls_back_to_media_list_for_svg_logo(self):
        # Disneyland: summary empty and PageImages skips the SVG logo, so the
        # rasterised logo must be recovered from the REST media-list.
        pageimages = {'query': {'pages': {'1234': {}}}}
        medialist = {'items': [
            {'type': 'image', 'section_id': 0, 'title': 'File:Disneyland_Park_Logo.svg',
             'srcset': [
                 {'src': '//upload.wikimedia.org/w/120px-Disneyland_Park_Logo.svg.png', 'scale': '1x'},
                 {'src': '//upload.wikimedia.org/w/240px-Disneyland_Park_Logo.svg.png', 'scale': '2x'},
             ]},
        ]}
        cm, client = _fake_httpx_client([self.SEARCH, self._summary(), pageimages, medialist])
        with patch('cards.wikipedia.httpx.Client', return_value=cm):
            card = _fetch_wikipedia('disneyland')
        # The sharpest srcset variant, with the protocol filled in.
        self.assertEqual(card['thumbnail'],
                         'https://upload.wikimedia.org/w/240px-Disneyland_Park_Logo.svg.png')
        self.assertEqual(client.get.call_count, 4)
        self.assertIn('media-list', client.get.call_args_list[3].args[0])

    def test_media_list_skips_non_image_items(self):
        # Audio pronunciations and other non-image media must be ignored.
        pageimages = {'query': {'pages': {'1234': {}}}}
        medialist = {'items': [
            {'type': 'audio', 'title': 'File:En-Disneyland.ogg',
             'srcset': [{'src': '//upload.wikimedia.org/a.ogg', 'scale': '1x'}]},
            {'type': 'image', 'title': 'File:Logo.svg',
             'srcset': [{'src': '//upload.wikimedia.org/logo.png', 'scale': '1x'}]},
        ]}
        cm, _ = _fake_httpx_client([self.SEARCH, self._summary(), pageimages, medialist])
        with patch('cards.wikipedia.httpx.Client', return_value=cm):
            card = _fetch_wikipedia('disneyland')
        self.assertEqual(card['thumbnail'], 'https://upload.wikimedia.org/logo.png')

    def test_thumbnail_empty_when_all_fallbacks_have_no_image(self):
        pageimages = {'query': {'pages': {'1234': {}}}}
        medialist = {'items': []}
        cm, client = _fake_httpx_client([self.SEARCH, self._summary(), pageimages, medialist])
        with patch('cards.wikipedia.httpx.Client', return_value=cm):
            card = _fetch_wikipedia('disneyland')
        self.assertEqual(card['thumbnail'], '')
        # The rest of the card still survives fruitless fallbacks.
        self.assertEqual(card['title'], 'Disneyland')
        self.assertEqual(client.get.call_count, 4)

    def test_fallback_failure_degrades_to_empty_thumbnail(self):
        # pageimages raises and media-list yields nothing: the card should
        # still come back, just without a thumbnail.
        medialist = {'items': []}
        cm, client = _fake_httpx_client([self.SEARCH, self._summary(), medialist])
        responses = list(client.get.side_effect)  # search, summary, media-list
        boom = MagicMock()
        boom.raise_for_status.side_effect = RuntimeError('boom')
        # Splice the raising pageimages response in before media-list.
        client.get.side_effect = responses[:2] + [boom, responses[2]]
        with patch('cards.wikipedia.httpx.Client', return_value=cm):
            card = _fetch_wikipedia('disneyland')
        self.assertEqual(card['thumbnail'], '')
        self.assertEqual(card['title'], 'Disneyland')

    def test_non_standard_summary_returns_none(self):
        disambig = self._summary(type='disambiguation')
        cm, client = _fake_httpx_client([self.SEARCH, disambig])
        with patch('cards.wikipedia.httpx.Client', return_value=cm):
            card = _fetch_wikipedia('disneyland')
        self.assertIsNone(card)
        # No fallback for a non-article.
        self.assertEqual(client.get.call_count, 2)

    def test_no_search_hits_returns_none(self):
        cm, _ = _fake_httpx_client([{'query': {'search': []}}])
        with patch('cards.wikipedia.httpx.Client', return_value=cm):
            card = _fetch_wikipedia('asdfqwerzxcv')
        self.assertIsNone(card)

    def test_picks_closest_hit_not_first(self):
        # Full-text ranking can put a tangential page first; the title closest
        # to the query wins.
        search = {'query': {'search': [{'title': 'Disneyland Paris'}, {'title': 'Disneyland'}]}}
        summary = self._summary(thumbnail={'source': 'https://upload.wikimedia.org/t.jpg', 'width': 320})
        cm, client = _fake_httpx_client([search, summary])
        with patch('cards.wikipedia.httpx.Client', return_value=cm):
            card = _fetch_wikipedia('disneyland')
        self.assertEqual(card['title'], 'Disneyland')
        self.assertTrue(client.get.call_args_list[1].args[0].endswith('/page/summary/Disneyland'))

    def test_redirect_title_rescues_native_language_query(self):
        # The query matched through a redirect ("la casa de papel" → "Money
        # Heist"): closeness is judged against the redirect title too, so the
        # card isn't filtered just because the canonical title differs.
        search = {'query': {'search': [
            {'title': 'Money Heist', 'redirecttitle': 'La casa de papel'},
        ]}}
        summary = {
            'type': 'standard',
            'title': 'Money Heist',
            'description': 'Spanish heist crime drama television series',
            'extract': 'Money Heist is a Spanish series...',
            'content_urls': {'desktop': {'page': 'https://en.wikipedia.org/wiki/Money_Heist'}},
            'thumbnail': {'source': 'https://upload.wikimedia.org/mh.jpg', 'width': 320},
        }
        cm, _ = _fake_httpx_client([search, summary])
        with patch('cards.wikipedia.httpx.Client', return_value=cm):
            card = _fetch_wikipedia('la casa de papel')
        self.assertIsNotNone(card)
        self.assertEqual(card['title'], 'Money Heist')

    def test_drops_card_when_no_hit_matches_query(self):
        # Even the closest hit shares nothing with the query → no card, and
        # the summary endpoint is never called.
        search = {'query': {'search': [{'title': 'Iceland'}, {'title': 'Ice cream'}]}}
        cm, client = _fake_httpx_client([search])
        with patch('cards.wikipedia.httpx.Client', return_value=cm):
            card = _fetch_wikipedia('quarterly report template')
        self.assertIsNone(card)
        self.assertEqual(client.get.call_count, 1)

    def test_card_exposes_wikibase_item(self):
        # The Wikidata item id drives language-independent card detection.
        summary = self._summary(
            wikibase_item='Q217054',
            thumbnail={'source': 'https://upload.wikimedia.org/t.jpg', 'width': 320},
        )
        cm, _ = _fake_httpx_client([self.SEARCH, summary])
        with patch('cards.wikipedia.httpx.Client', return_value=cm):
            card = _fetch_wikipedia('disneyland')
        self.assertEqual(card['wikibase_item'], 'Q217054')


class LooksExplicitTests(TestCase):
    """The cheap, network-free title check behind safe-search card hiding."""

    def test_flags_explicit_titles_across_languages(self):
        for title in ['Fellatio', 'Pornografie', 'Masturbation', 'Sexe oral',
                      'Orale seks', 'Pornografía', 'Geschlechtsverkehr', 'BDSM', 'Dildo']:
            self.assertTrue(_looks_explicit(title), title)

    def test_does_not_flag_innocent_lookalikes(self):
        # Whole-word + scoped vocabulary: place names that merely contain "sex",
        # bands/shows with a sexual word, and the ambiguous "Vibrator" are safe.
        for title in ['Disneyland', 'Sussex', 'Essex', 'Middlesex', 'Scunthorpe',
                      'Sex and the City', 'Sex Pistols', 'Hardcore punk',
                      'Vibrator', 'Python (programming language)']:
            self.assertFalse(_looks_explicit(title), title)


class WikipediaSafeSearchTests(TestCase):
    """`fetch_wikipedia` hides explicit cards when safe search is on."""

    @staticmethod
    def _payloads(title='Fellatio', wikibase_item='Q170518'):
        # A query that legitimately resolves to an explicit page (the relevance
        # gate passes), whose summary carries an explicit lead image, what the
        # safe-search filter has to catch before the card is shown.
        search = {'query': {'search': [{'title': title}]}}
        summary = {
            'type': 'standard', 'title': title,
            'description': 'sexual practice', 'extract': f'{title} is ...',
            'content_urls': {'desktop': {'page': f'https://nl.wikipedia.org/wiki/{title}'}},
            'thumbnail': {'source': 'https://upload.wikimedia.org/explicit.jpg', 'width': 320},
            'wikibase_item': wikibase_item,
        }
        return [search, summary]

    def test_explicit_card_shown_when_safe_off(self):
        cm, _ = _fake_httpx_client(self._payloads())
        with patch('cards.wikipedia.httpx.Client', return_value=cm):
            card = fetch_wikipedia('fellatio', safe_search='off')
        self.assertIsNotNone(card)
        self.assertEqual(card['title'], 'Fellatio')

    def test_explicit_title_hidden_when_safe_on(self):
        # The title alone is explicit, so no Wikidata round-trip is needed.
        cm, _ = _fake_httpx_client(self._payloads())
        with patch('cards.wikipedia.httpx.Client', return_value=cm), \
                patch('cards.wikidata.is_adult_subject') as mock_adult:
            card = fetch_wikipedia('fellatio', safe_search='on')
        self.assertIsNone(card)
        mock_adult.assert_not_called()

    def test_explicit_title_hidden_for_any_non_off_value(self):
        # Only the literal 'off' turns filtering off, so anything else hides it.
        cm, _ = _fake_httpx_client(self._payloads())
        with patch('cards.wikipedia.httpx.Client', return_value=cm):
            card = fetch_wikipedia('fellatio', safe_search='strict')
        self.assertIsNone(card)

    def test_innocent_title_but_adult_wikidata_class_hidden(self):
        # Title looks harmless; the subject's Wikidata class marks it adult.
        cm, _ = _fake_httpx_client(self._payloads(title='Debbie Does Dallas', wikibase_item='Q555'))
        with patch('cards.wikipedia.httpx.Client', return_value=cm), \
                patch('cards.wikidata.is_adult_subject', return_value=True) as mock_adult:
            card = fetch_wikipedia('debbie does dallas', safe_search='on')
        self.assertIsNone(card)
        mock_adult.assert_called_once_with('Q555')

    def test_safe_card_shown_when_subject_is_clean(self):
        search = {'query': {'search': [{'title': 'Disneyland'}]}}
        summary = {
            'type': 'standard', 'title': 'Disneyland', 'description': 'theme park',
            'extract': 'Disneyland is ...', 'wikibase_item': 'Q1234',
            'content_urls': {'desktop': {'page': 'https://en.wikipedia.org/wiki/Disneyland'}},
        }
        cm, _ = _fake_httpx_client([search, summary])
        with patch('cards.wikipedia.httpx.Client', return_value=cm), \
                patch('cards.wikidata.is_adult_subject', return_value=False):
            card = fetch_wikipedia('disneyland', safe_search='on')
        self.assertIsNotNone(card)
        self.assertEqual(card['title'], 'Disneyland')


def _img_response(content=b'\x89PNG', content_type='image/png'):
    """A stand-in httpx response for a successfully fetched image."""
    resp = MagicMock()
    resp.content = content
    resp.headers = {'content-type': content_type}
    resp.raise_for_status = MagicMock()
    return resp


class ProxifyHelperTests(TestCase):
    """``imageproxy.proxify`` mints DB-backed tokens, never raw-URL proxies."""

    def test_registers_url_and_returns_token_path(self):
        from search.imageproxy import proxify
        from search.models import ProxiedImage
        path = proxify('https://cdn.example.com/cat.jpg')
        self.assertTrue(path.startswith(reverse('search:image_proxy') + '?id='))
        token = path.split('id=')[1]
        self.assertEqual(ProxiedImage.objects.get(id=token).url, 'https://cdn.example.com/cat.jpg')

    def test_same_url_dedupes_to_one_row(self):
        from search.imageproxy import proxify
        from search.models import ProxiedImage
        self.assertEqual(proxify('https://cdn.example.com/a.jpg'),
                         proxify('https://cdn.example.com/a.jpg'))
        self.assertEqual(ProxiedImage.objects.count(), 1)

    def test_reuse_refreshes_expiry(self):
        from search.imageproxy import proxify
        from search.models import ProxiedImage
        proxify('https://cdn.example.com/a.jpg')
        ProxiedImage.objects.update(expires_at=timezone.now())  # simulate near-expiry
        proxify('https://cdn.example.com/a.jpg')
        self.assertGreater(ProxiedImage.objects.get().expires_at,
                           timezone.now() + timedelta(hours=1))

    def test_empty_or_unproxyable_returns_blank(self):
        from search.imageproxy import proxify
        from search.models import ProxiedImage
        for bad in ('', None, '   ', 'ftp://x/y.png', 'javascript:alert(1)',
                    'not a url', '/relative/path.png'):
            self.assertEqual(proxify(bad), '', bad)
        self.assertEqual(ProxiedImage.objects.count(), 0)

    def test_blocks_loopback_and_private_hosts(self):
        from search.imageproxy import proxify
        from search.models import ProxiedImage
        for bad in ('http://localhost/x.png', 'http://127.0.0.1/x.png',
                    'http://10.0.0.5/x.png', 'http://169.254.169.254/latest/meta',
                    'http://[::1]/x.png'):
            self.assertEqual(proxify(bad), '', bad)
        self.assertEqual(ProxiedImage.objects.count(), 0)

    def test_pixabay_hosts_marked_persist(self):
        from search.imageproxy import proxify
        from search.models import ProxiedImage
        for url in ('https://pixabay.com/photos/cat-1/',
                    'https://cdn.pixabay.com/photo/cat.jpg'):
            ProxiedImage.objects.all().delete()
            proxify(url)
            self.assertTrue(ProxiedImage.objects.get().persist, url)

    def test_non_pixabay_host_not_marked_persist(self):
        from search.imageproxy import proxify
        from search.models import ProxiedImage
        proxify('https://cdn.example.com/cat.jpg')
        self.assertFalse(ProxiedImage.objects.get().persist)

    def test_must_proxy_true_only_for_persist_hosts(self):
        from search.imageproxy import must_proxy
        self.assertTrue(must_proxy('https://cdn.pixabay.com/photo/cat.jpg'))
        self.assertTrue(must_proxy('https://pixabay.com/photos/cat-1/'))
        self.assertFalse(must_proxy('https://cdn.example.com/cat.jpg'))
        self.assertFalse(must_proxy('not a url'))
        self.assertFalse(must_proxy(''))


class ResolvePublicAddressTests(TestCase):
    """Fetch-time DNS validation behind the image proxy (SSRF guard)."""

    def _resolve(self, url, addrinfo):
        from search.imageproxy import resolve_public_address
        with patch('search.imageproxy.socket.getaddrinfo', return_value=addrinfo):
            return resolve_public_address(url)

    @staticmethod
    def _info(*ips):
        return [(None, None, None, '', (ip, 0)) for ip in ips]

    def test_public_host_returns_first_address(self):
        self.assertEqual(
            self._resolve('https://cdn.example.com/x.png', self._info('93.184.216.34', '1.1.1.1')),
            '93.184.216.34',
        )

    def test_any_private_answer_rejects_the_host(self):
        # A record set mixing public and private addresses is DNS rebinding,
        # not something to route around.
        self.assertIsNone(
            self._resolve('https://cdn.example.com/x.png', self._info('93.184.216.34', '10.0.0.5')),
        )
        self.assertIsNone(
            self._resolve('https://cdn.example.com/x.png', self._info('169.254.169.254')),
        )

    def test_literal_private_host_rejected_without_dns(self):
        from search.imageproxy import resolve_public_address
        with patch('search.imageproxy.socket.getaddrinfo') as mock_dns:
            self.assertIsNone(resolve_public_address('http://127.0.0.1/x.png'))
            self.assertIsNone(resolve_public_address('http://169.254.169.254/latest/meta'))
            self.assertIsNone(resolve_public_address('http://localhost/x.png'))
        mock_dns.assert_not_called()

    def test_unresolvable_host_rejected(self):
        from search.imageproxy import resolve_public_address
        with patch('search.imageproxy.socket.getaddrinfo', side_effect=OSError('nxdomain')):
            self.assertIsNone(resolve_public_address('https://cdn.example.com/x.png'))


class ImageProxyViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('img', password='pass')
        self.client.login(username='img', password='pass')
        # The proxy re-resolves the host at fetch time (SSRF/DNS-rebinding
        # guard); pin it to a public documentation IP so no test touches DNS.
        patcher = patch('search.views.resolve_public_address', return_value='203.0.113.7')
        self.mock_resolve = patcher.start()
        self.addCleanup(patcher.stop)

    def _token(self, url='https://cdn.example.com/cat.jpg', expires_at=None, persist=False):
        from search.imageproxy import _NAMESPACE, PROXY_TTL
        from search.models import ProxiedImage
        token = uuid.uuid5(_NAMESPACE, url)
        ProxiedImage.objects.create(
            id=token, url=url, persist=persist,
            expires_at=expires_at or timezone.now() + PROXY_TTL,
        )
        return token

    def test_login_required(self):
        self.client.logout()
        resp = self.client.get(reverse('search:image_proxy') + '?id=' + str(uuid.uuid4()))
        self.assertEqual(resp.status_code, 302)

    def test_missing_id_returns_400(self):
        self.assertEqual(self.client.get(reverse('search:image_proxy')).status_code, 400)

    def test_malformed_id_returns_400(self):
        resp = self.client.get(reverse('search:image_proxy') + '?id=not-a-uuid')
        self.assertEqual(resp.status_code, 400)

    def test_unknown_token_returns_404(self):
        resp = self.client.get(reverse('search:image_proxy') + '?id=' + str(uuid.uuid4()))
        self.assertEqual(resp.status_code, 404)

    def test_expired_token_returns_404(self):
        token = self._token(expires_at=timezone.now() - timedelta(seconds=1))
        resp = self.client.get(reverse('search:image_proxy') + '?id=' + str(token))
        self.assertEqual(resp.status_code, 404)

    @patch('search.views.httpx.Client')
    def test_valid_token_fetches_stored_url(self, mock_cls):
        client = mock_cls.return_value.__enter__.return_value
        client.get.return_value = _img_response(b'IMGDATA', 'image/png')
        token = self._token()
        resp = self.client.get(reverse('search:image_proxy') + '?id=' + str(token))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b'IMGDATA')
        self.assertEqual(resp['Content-Type'], 'image/png')
        self.assertEqual(resp['Cache-Control'], 'public, max-age=86400')
        self.assertEqual(resp['X-Content-Type-Options'], 'nosniff')
        # The *stored* URL is fetched, nothing from the request can redirect
        # it, and the connection is pinned to the IP the host just resolved
        # to (Host header + SNI carry the original name).
        self.mock_resolve.assert_called_once_with('https://cdn.example.com/cat.jpg')
        self.assertEqual(client.get.call_args.args[0], 'https://203.0.113.7/cat.jpg')
        self.assertEqual(client.get.call_args.kwargs['headers']['Host'], 'cdn.example.com')
        self.assertEqual(client.get.call_args.kwargs['extensions'],
                         {'sni_hostname': 'cdn.example.com'})
        # Redirects must not be followed (a 30x could point at a private host).
        self.assertFalse(mock_cls.call_args.kwargs['follow_redirects'])

    @patch('search.views.httpx.Client')
    def test_host_resolving_to_private_address_refused(self, mock_cls):
        # DNS rebinding: the host passed the register-time check but now
        # resolves to something private, no request may leave at all.
        self.mock_resolve.return_value = None
        token = self._token()
        resp = self.client.get(reverse('search:image_proxy') + '?id=' + str(token))
        self.assertEqual(resp.status_code, 204)
        mock_cls.return_value.__enter__.return_value.get.assert_not_called()

    @patch('search.views.httpx.Client')
    def test_redirect_answer_returns_204(self, mock_cls):
        # follow_redirects is off, so a 302 upstream surfaces as an HTTP error
        # (raise_for_status) and the proxy returns nothing instead of chasing
        # the Location header wherever it points.
        client = mock_cls.return_value.__enter__.return_value
        resp_302 = MagicMock()
        resp_302.raise_for_status.side_effect = Exception('302 Found')
        client.get.return_value = resp_302
        token = self._token()
        resp = self.client.get(reverse('search:image_proxy') + '?id=' + str(token))
        self.assertEqual(resp.status_code, 204)

    @patch('search.views.httpx.Client')
    def test_non_image_content_type_neutralized(self, mock_cls):
        # An upstream answering text/html must never be served as HTML from
        # this origin; the bytes come back as an opaque download instead.
        client = mock_cls.return_value.__enter__.return_value
        client.get.return_value = _img_response(b'<html>boo</html>', 'text/html; charset=utf-8')
        token = self._token()
        resp = self.client.get(reverse('search:image_proxy') + '?id=' + str(token))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/octet-stream')
        self.assertEqual(resp['X-Content-Type-Options'], 'nosniff')

    @patch('search.views.httpx.Client')
    def test_upstream_failure_returns_204(self, mock_cls):
        client = mock_cls.return_value.__enter__.return_value
        client.get.side_effect = Exception('boom')
        token = self._token()
        resp = self.client.get(reverse('search:image_proxy') + '?id=' + str(token))
        self.assertEqual(resp.status_code, 204)

    @patch('search.views.httpx.Client')
    def test_tripadvisor_host_gets_referer(self, mock_cls):
        client = mock_cls.return_value.__enter__.return_value
        client.get.return_value = _img_response()
        token = self._token('https://media-cdn.tripadvisor.com/photo.jpg')
        self.client.get(reverse('search:image_proxy') + '?id=' + str(token))
        self.assertEqual(client.get.call_args.kwargs['headers'].get('Referer'),
                         'https://www.tripadvisor.com/')

    @patch('search.views.httpx.Client')
    def test_other_host_gets_no_referer(self, mock_cls):
        client = mock_cls.return_value.__enter__.return_value
        client.get.return_value = _img_response()
        token = self._token('https://cdn.example.com/cat.jpg')
        self.client.get(reverse('search:image_proxy') + '?id=' + str(token))
        self.assertNotIn('Referer', client.get.call_args.kwargs['headers'])

    @patch('search.views.httpx.Client')
    def test_sends_descriptive_user_agent(self, mock_cls):
        # Wikimedia (and similar) 403 a default library UA; we send our own.
        client = mock_cls.return_value.__enter__.return_value
        client.get.return_value = _img_response()
        token = self._token('https://upload.wikimedia.org/x.png')
        self.client.get(reverse('search:image_proxy') + '?id=' + str(token))
        self.assertIn('Seurch', client.get.call_args.kwargs['headers'].get('User-Agent', ''))

    @patch('search.views.httpx.Client')
    def test_persist_host_stores_bytes_on_first_fetch(self, mock_cls):
        from search.models import ProxiedImage
        client = mock_cls.return_value.__enter__.return_value
        client.get.return_value = _img_response(b'PIXDATA', 'image/jpeg')
        token = self._token('https://cdn.pixabay.com/photo/cat.jpg', persist=True)
        resp = self.client.get(reverse('search:image_proxy') + '?id=' + str(token))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b'PIXDATA')
        entry = ProxiedImage.objects.get(id=token)
        self.assertEqual(bytes(entry.content), b'PIXDATA')
        self.assertEqual(entry.content_type, 'image/jpeg')

    @patch('search.views.httpx.Client')
    def test_persist_host_second_hit_skips_upstream(self, mock_cls):
        from search.models import ProxiedImage
        client = mock_cls.return_value.__enter__.return_value
        client.get.return_value = _img_response(b'PIXDATA', 'image/jpeg')
        token = self._token('https://cdn.pixabay.com/photo/cat.jpg', persist=True)
        ProxiedImage.objects.filter(id=token).update(content=b'STORED', content_type='image/png')
        resp = self.client.get(reverse('search:image_proxy') + '?id=' + str(token))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b'STORED')
        self.assertEqual(resp['Content-Type'], 'image/png')
        client.get.assert_not_called()

    @patch('search.views.httpx.Client')
    def test_non_persist_host_never_stores_bytes(self, mock_cls):
        from search.models import ProxiedImage
        client = mock_cls.return_value.__enter__.return_value
        client.get.return_value = _img_response(b'IMGDATA', 'image/png')
        token = self._token('https://cdn.example.com/cat.jpg', persist=False)
        self.client.get(reverse('search:image_proxy') + '?id=' + str(token))
        entry = ProxiedImage.objects.get(id=token)
        self.assertIsNone(entry.content)


class ImgSrcTagTests(TestCase):
    """`{% img_src %}` proxies only when `proxy_images` is enabled in context."""

    def _render(self, url, **ctx):
        from django.template import Context, Template
        tpl = Template('{% load image_extras %}{% img_src url %}')
        return tpl.render(Context({'url': url, **ctx}))

    def test_passthrough_when_proxy_disabled(self):
        from search.models import ProxiedImage
        # proxy_images absent from context → off (the default)
        out = self._render('https://cdn.example.com/i.png')
        self.assertEqual(out, 'https://cdn.example.com/i.png')
        self.assertEqual(ProxiedImage.objects.count(), 0)

    def test_proxies_when_enabled(self):
        from search.models import ProxiedImage
        out = self._render('https://cdn.example.com/i.png', proxy_images=True)
        self.assertIn(reverse('search:image_proxy') + '?id=', out)
        self.assertNotIn('https://cdn.example.com/i.png', out)
        self.assertEqual(ProxiedImage.objects.count(), 1)

    def test_blank_for_empty(self):
        self.assertEqual(self._render('', proxy_images=True), '')
        self.assertEqual(self._render(''), '')

    def test_pixabay_proxied_even_when_disabled(self):
        from search.models import ProxiedImage
        # proxy_images is off, but Pixabay's no-hotlinking terms force it anyway.
        out = self._render('https://cdn.pixabay.com/photo/cat.jpg')
        self.assertIn(reverse('search:image_proxy') + '?id=', out)
        self.assertNotIn('https://cdn.pixabay.com/photo/cat.jpg', out)
        self.assertTrue(ProxiedImage.objects.get().persist)

    def test_non_pixabay_host_not_force_proxied(self):
        out = self._render('https://cdn.example.com/cat.jpg')
        self.assertEqual(out, 'https://cdn.example.com/cat.jpg')


class SnippetFilterTests(TestCase):
    """`{{ value|snippet }}` keeps engine `<strong>` highlights as real tags,
    decodes HTML entities, and escapes everything else for safety."""

    def _render(self, value):
        from django.template import Context, Template
        tpl = Template('{% load result_extras %}{{ value|snippet }}')
        return tpl.render(Context({'value': value}))

    def test_strong_tags_become_real_highlight(self):
        # The matched query term arrives wrapped in <strong> and must render as
        # a real tag, not the visible literal text "<strong>".
        out = self._render('Learn the <strong>piano</strong> online')
        self.assertEqual(out, 'Learn the <strong>piano</strong> online')

    def test_entities_are_decoded_not_double_escaped(self):
        # French snippet: `d&#x27;une` must render as `d'une`, i.e. the browser
        # receives `d&#x27;une` (an apostrophe entity), never `d&amp;#x27;une`.
        out = self._render('Apprendre l&#x27;art d&#x27;une langue')
        self.assertEqual(out, 'Apprendre l&#x27;art d&#x27;une langue')
        self.assertNotIn('&amp;#x27;', out)

    def test_combined_strong_and_entities(self):
        out = self._render("Le <strong>piano</strong> n&#x27;est pas dur")
        self.assertEqual(out, "Le <strong>piano</strong> n&#x27;est pas dur")

    def test_strong_tag_is_case_insensitive_and_drops_attributes(self):
        out = self._render('a <STRONG class="hl">b</Strong> c')
        self.assertEqual(out, 'a <strong>b</strong> c')

    def test_other_markup_is_escaped(self):
        # Only <strong> is promoted; any other markup is neutralised so a
        # malicious snippet cannot inject scripts or links.
        out = self._render('<script>alert(1)</script> <a href="#">x</a>')
        self.assertNotIn('<script>', out)
        self.assertNotIn('<a ', out)
        self.assertIn('&lt;script&gt;', out)

    def test_entity_encoded_strong_stays_literal(self):
        # An entity-encoded tag is content, not a real highlight, so it must not
        # be promoted into a live <strong>.
        out = self._render('text &lt;strong&gt;not a tag&lt;/strong&gt;')
        self.assertNotIn('<strong>', out)
        self.assertIn('&lt;strong&gt;', out)

    def test_blank_for_empty(self):
        self.assertEqual(self._render(''), '')
        self.assertEqual(self._render(None), '')


class ProxyImageSettingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('px', password='pass')
        self.client.login(username='px', password='pass')

    def test_defaults_off(self):
        from search import preferences
        self.assertFalse(preferences.defaults()['proxy_images'])
        self.assertFalse(preferences.coerce({})['proxy_images'])

    def test_coerce_reads_bool(self):
        from search import preferences
        self.assertTrue(preferences.coerce({'proxy_images': True})['proxy_images'])
        self.assertFalse(preferences.coerce({'proxy_images': ''})['proxy_images'])

    def test_toggle_on_persists_to_cookie(self):
        resp = self.client.post(reverse('search:settings'), {
            'setting': 'proxy_images', 'proxy_images': 'on', 'pane': 'general',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(_get_prefs(self.client)['proxy_images'])

    def test_toggle_off_persists_to_cookie(self):
        _set_prefs(self.client, proxy_images=True)
        resp = self.client.post(reverse('search:settings'), {
            'setting': 'proxy_images', 'pane': 'general',  # unchecked → field absent
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(_get_prefs(self.client)['proxy_images'])

    def test_settings_page_renders_toggle(self):
        resp = self.client.get(reverse('search:settings'))
        self.assertContains(resp, 'name="proxy_images"')


class EmailSettingTests(TestCase):
    """Settings → Account lets the user add, change and remove their email."""

    def setUp(self):
        self.user = User.objects.create_user('em', password='pass')
        self.client.login(username='em', password='pass')

    def _post_email(self, **data):
        # The account password confirms email changes; tests override it to
        # exercise the rejection path.
        return self.client.post(
            reverse('search:settings'),
            {'pane': 'account', 'current_password': 'pass', **data},
        )

    def test_add_email(self):
        resp = self._post_email(setting='email', email='me@example.com')
        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'me@example.com')

    def test_change_email(self):
        self.user.email = 'old@example.com'
        self.user.save()
        self._post_email(setting='email', email='new@example.com')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'new@example.com')

    def test_email_domain_is_normalized(self):
        self._post_email(setting='email', email='Me@EXAMPLE.COM')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'Me@example.com')

    def test_invalid_email_rejected(self):
        resp = self._post_email(setting='email', email='not-an-email')
        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, '')
        resp = self.client.get(resp['Location'])
        self.assertContains(resp, 'valid email')

    def test_overlong_email_rejected(self):
        self._post_email(setting='email', email='a' * 250 + '@example.com')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, '')

    def test_empty_email_rejected(self):
        resp = self._post_email(setting='email', email='   ')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, '')
        resp = self.client.get(resp['Location'])
        self.assertContains(resp, 'Enter an email address')

    def test_remove_email(self):
        self.user.email = 'me@example.com'
        self.user.save()
        resp = self._post_email(setting='delete_email')
        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, '')

    def test_change_email_requires_correct_password(self):
        self.user.email = 'old@example.com'
        self.user.save()
        resp = self._post_email(setting='email', email='new@example.com', current_password='wrong')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'old@example.com')
        resp = self.client.get(resp['Location'])
        self.assertContains(resp, 'password was incorrect')

    def test_change_email_requires_a_password_at_all(self):
        resp = self._post_email(setting='email', email='new@example.com', current_password='')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, '')
        resp = self.client.get(resp['Location'])
        self.assertContains(resp, 'password was incorrect')

    def test_remove_email_requires_correct_password(self):
        self.user.email = 'me@example.com'
        self.user.save()
        self._post_email(setting='delete_email', current_password='wrong')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'me@example.com')

    def test_account_pane_renders_password_confirmation(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'account'}))
        self.assertContains(resp, 'name="current_password"')

    def test_account_pane_shows_email_form_and_disclaimer(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'account'}))
        self.assertContains(resp, 'name="email"')
        self.assertContains(resp, 'no way to reset your password')
        # No email yet → the warning callout is shown.
        self.assertContains(resp, 'No email address is set.')

    def test_account_pane_offers_removal_only_when_email_set(self):
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'account'}))
        self.assertNotContains(resp, 'value="delete_email"')
        self.user.email = 'me@example.com'
        self.user.save()
        resp = self.client.get(reverse('search:settings_pane', kwargs={'pane': 'account'}))
        self.assertContains(resp, 'value="delete_email"')
        self.assertContains(resp, 'me@example.com')

    def test_email_change_does_not_touch_settings_document(self):
        # Account data lives on the User row, not in the synced settings
        # document, saving an email must not create or bump a snapshot.
        from search.models import UserSettings
        self._post_email(setting='email', email='me@example.com')
        self.assertFalse(UserSettings.objects.filter(user=self.user).exists())


def _reset_provider_health():
    """Baseline the process-global provider-health state for a health test.

    Clears the in-process write throttle *and* the ProviderStatus table. The
    results view records provider health (e.g. wikipedia/wikidata) from request
    worker threads, whose DB connection lives outside this test's transaction,
    so a prior view test can commit rows that would otherwise leak into these
    exact-contents assertions. (Harmless on PostgreSQL, the rows just commit;
    on SQLite the threaded writes fail under lock, which is why it only bites CI.)
    """
    health._last_write.clear()
    ProviderStatus.objects.all().delete()


class ProviderHealthRecordingTests(TestCase):
    """The write side: record_ok / record_down and the in-process throttle."""

    def setUp(self):
        _reset_provider_health()

    def test_record_down_then_recovery(self):
        health.record_down('brave', 'kaboom')
        row = ProviderStatus.objects.get(provider='brave')
        self.assertFalse(row.ok)
        self.assertEqual(row.last_error, 'kaboom')
        self.assertEqual(row.source, 'query')
        self.assertIsNone(row.last_ok_at)

        health.record_ok('brave')
        row.refresh_from_db()
        self.assertTrue(row.ok)
        self.assertEqual(row.last_error, '')
        self.assertIsNotNone(row.last_ok_at)

    def test_unknown_provider_is_ignored(self):
        health.record_down('not-a-real-provider', 'x')
        self.assertEqual(ProviderStatus.objects.count(), 0)

    def test_long_error_is_truncated(self):
        health.record_down('sepia', 'e' * 500)
        self.assertEqual(len(ProviderStatus.objects.get(provider='sepia').last_error), 300)

    def test_throttle_skips_repeat_but_always_writes_transitions(self):
        health.record_ok('sepia')
        first_checked = ProviderStatus.objects.get(provider='sepia').checked_at
        # A second identical outcome inside the throttle window is skipped...
        health.record_ok('sepia')
        self.assertEqual(ProviderStatus.objects.get(provider='sepia').checked_at, first_checked)
        # ...but a state transition is always persisted.
        health.record_down('sepia', 'oops')
        self.assertFalse(ProviderStatus.objects.get(provider='sepia').ok)


class ProviderStatusesTests(TestCase):
    """The read side: which providers show, and their derived state."""

    def setUp(self):
        _reset_provider_health()

    @override_settings(BRAVE_API_KEY='', PIXABAY_API_KEY='')
    def test_unconfigured_providers_are_hidden(self):
        slugs = {s['slug'] for s in health.provider_statuses()}
        self.assertNotIn('brave', slugs)      # no API key → not shown (not "down")
        self.assertNotIn('pixabay', slugs)
        self.assertIn('sepia', slugs)         # keyless → always shown
        self.assertIn('wikipedia', slugs)

    @override_settings(BRAVE_API_KEY='secret')
    def test_configured_provider_is_shown(self):
        self.assertIn('brave', {s['slug'] for s in health.provider_statuses()})

    @override_settings(STAAN_API_KEY='sk')
    def test_staan_listed_with_the_search_engines(self):
        by_slug = {s['slug']: s for s in health.provider_statuses()}
        self.assertEqual(by_slug['staan']['group'], 'engine')
        self.assertEqual(by_slug['staan']['name'], 'Staan')
        # Observed from real searches, never probed (it's a paid API).
        self.assertEqual(by_slug['staan']['monitor'], health.MONITOR_QUERY)

    @override_settings(STAAN_API_KEY='')
    def test_staan_hidden_without_a_key(self):
        self.assertNotIn('staan', {s['slug'] for s in health.provider_statuses()})

    def test_state_reflects_recorded_rows(self):
        health.record_down('sepia', 'down!')
        by_slug = {s['slug']: s for s in health.provider_statuses()}
        self.assertEqual(by_slug['sepia']['state'], 'down')
        self.assertEqual(by_slug['sepia']['last_error'], 'down!')
        self.assertEqual(by_slug['wikipedia']['state'], 'unknown')  # never observed

    def test_status_page_context_groups_and_summary(self):
        health.record_down('sepia', 'x')
        ctx = health.status_page_context()
        self.assertTrue(ctx['groups'])
        self.assertEqual(ctx['summary']['down'], 1)
        self.assertFalse(ctx['summary']['all_ok'])
        # Groups only appear when they have at least one configured provider.
        self.assertTrue(all(g['items'] for g in ctx['groups']))


class RunProbesTests(TestCase):
    """The active probes for providers with a free health endpoint."""

    def setUp(self):
        _reset_provider_health()

    def test_successful_probe_records_ok_from_probe(self):
        with patch.dict(health._PROBES, {'openstreetmap': lambda: None}, clear=True):
            results = health.run_probes()
        self.assertTrue(results['openstreetmap'])
        row = ProviderStatus.objects.get(provider='openstreetmap')
        self.assertTrue(row.ok)
        self.assertEqual(row.source, 'probe')

    def test_failing_probe_records_down_from_probe(self):
        def boom():
            raise RuntimeError('nope')

        with patch.dict(health._PROBES, {'openstreetmap': boom}, clear=True):
            results = health.run_probes()
        self.assertFalse(results['openstreetmap'])
        row = ProviderStatus.objects.get(provider='openstreetmap')
        self.assertFalse(row.ok)
        self.assertEqual(row.source, 'probe')
        self.assertIn('nope', row.last_error)

    @override_settings(LIBRETRANSLATE_URL='')
    def test_unconfigured_provider_is_not_probed(self):
        with patch.dict(health._PROBES, {'translate': lambda: None}, clear=True):
            results = health.run_probes()
        self.assertNotIn('translate', results)
        self.assertFalse(ProviderStatus.objects.filter(provider='translate').exists())


class ProviderInstrumentationTests(TestCase):
    """Request helpers record health for free, off the back of real queries."""

    def setUp(self):
        _reset_provider_health()

    @override_settings(BRAVE_API_KEY='testkey')
    def test_successful_request_records_ok(self):
        cm, _ = _fake_httpx_client([{'web': {'results': []}}])
        with patch('search.clients.httpx.Client', return_value=cm):
            self.assertIsNotNone(_brave_request('/web/search', {'q': 'x'}))
        self.assertTrue(ProviderStatus.objects.get(provider='brave').ok)

    @override_settings(BRAVE_API_KEY='testkey')
    def test_failed_request_records_down(self):
        import httpx
        cm, client = _fake_httpx_client([{}])
        client.get.side_effect = httpx.RequestError('boom')
        with patch('search.clients.httpx.Client', return_value=cm):
            self.assertIsNone(_brave_request('/web/search', {'q': 'x'}))
        row = ProviderStatus.objects.get(provider='brave')
        self.assertFalse(row.ok)
        self.assertIn('boom', row.last_error)

    @override_settings(BRAVE_API_KEY='')
    def test_missing_key_records_nothing(self):
        # A missing key is "not configured", not "down", no row is written.
        self.assertIsNone(_brave_request('/web/search', {'q': 'x'}))
        self.assertFalse(ProviderStatus.objects.filter(provider='brave').exists())


@override_settings(STATUS_PAGE_ENABLED=True)
class ProviderStatusPageTests(TestCase):
    def setUp(self):
        _reset_provider_health()
        User.objects.create_user(username='carol', password='pass')

    def test_requires_login(self):
        resp = self.client.get(reverse('search:provider_status'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp['Location'])

    def test_renders_for_logged_in_user(self):
        self.client.login(username='carol', password='pass')
        resp = self.client.get(reverse('search:provider_status'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Sepia')  # a keyless provider, always listed

    def test_down_provider_is_flagged(self):
        health.record_down('sepia', 'kaboom')
        self.client.login(username='carol', password='pass')
        resp = self.client.get(reverse('search:provider_status'))
        self.assertContains(resp, 'Down')


class ProviderStatusPageToggleTests(TestCase):
    """STATUS_PAGE_ENABLED takes the page, and every link to it, off a deploy."""

    def setUp(self):
        _reset_provider_health()
        User.objects.create_user(username='dave', password='pass')
        self.client.login(username='dave', password='pass')

    @override_settings(STATUS_PAGE_ENABLED=False)
    def test_page_is_gone_when_disabled(self):
        resp = self.client.get(reverse('search:provider_status'))
        self.assertEqual(resp.status_code, 404)

    @override_settings(STATUS_PAGE_ENABLED=False)
    def test_links_hidden_when_disabled(self):
        # The footer ships on every page and Settings links it from the sidebar;
        # neither should offer a link that 404s.
        link = f'href="{reverse("search:provider_status")}"'
        for url in (reverse('search:about'), reverse('search:settings')):
            self.assertNotContains(self.client.get(url), link)

    @override_settings(STATUS_PAGE_ENABLED=True)
    def test_links_present_when_enabled(self):
        link = f'href="{reverse("search:provider_status")}"'
        for url in (reverse('search:about'), reverse('search:settings')):
            self.assertContains(self.client.get(url), link)


class MonitorScopeTests(TestCase):
    """Which providers /status/health watches (STATUS_MONITOR_PROVIDERS)."""

    @override_settings(STATUS_MONITOR_PROVIDERS=[])
    def test_empty_watches_every_configured_provider(self):
        self.assertEqual(health.monitor_scope(), health.configured_providers())

    @override_settings(STATUS_MONITOR_PROVIDERS=['sepia'])
    def test_slug_narrows_the_scope(self):
        self.assertEqual(health.monitor_scope(), ['sepia'])

    @override_settings(
        STATUS_MONITOR_PROVIDERS=['cards'], THETVDB_API_KEY='', TRIPADVISOR_API_KEY='',
    )
    def test_group_key_expands_to_its_configured_members(self):
        # Group keys are accepted alongside slugs; a member this deployment has
        # no key for stays out, exactly as it stays off the page.
        self.assertEqual(health.monitor_scope(), ['wikipedia', 'wikidata', 'stackexchange'])

    @override_settings(STATUS_MONITOR_PROVIDERS=['nope', 'sepia'])
    def test_unknown_entries_are_ignored(self):
        self.assertEqual(health.monitor_scope(), ['sepia'])


@override_settings(
    STATUS_MONITOR_ENABLED=True, STATUS_MONITOR_TOKEN='', STATUS_MONITOR_PROVIDERS=[],
)
class StatusMonitorEndpointTests(TestCase):
    """/status/health, the 200-or-500 endpoint an uptime monitor polls.

    The class pins the monitor settings so a developer's own .env can't decide
    the outcome; each test overrides the one it is about.
    """

    def setUp(self):
        _reset_provider_health()
        self.url = reverse('search:status_monitor')

    def test_answers_without_a_login(self):
        # A monitoring service can't sign in; every other page would redirect.
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_all_up_is_200(self):
        health.record_ok('sepia')
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'ok')
        self.assertEqual(resp.json()['down'], [])
        self.assertEqual(resp.json()['operational'], 1)
        # A cached verdict is a lie the next time round.
        self.assertEqual(resp['Cache-Control'], 'no-store')

    def test_down_provider_is_500(self):
        health.record_down('sepia', 'kaboom')
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()['status'], 'down')
        self.assertEqual(resp.json()['down'], ['sepia'])
        # It names what failed, never why: last_error can quote an upstream
        # response, and this endpoint answers without authentication.
        self.assertNotIn('kaboom', resp.content.decode())

    def test_never_observed_provider_is_not_an_outage(self):
        # Most providers are only observed when someone searches, so a quiet one
        # must not hold the monitor red.
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['unknown'], resp.json()['watched'])

    @override_settings(BRAVE_API_KEY='')
    def test_unconfigured_provider_is_not_watched(self):
        # A leftover row from when the key was set isn't this deployment's outage.
        health.record_down('brave', 'stale')
        self.assertEqual(self.client.get(self.url).status_code, 200)

    @override_settings(STATUS_MONITOR_PROVIDERS=['engine'], BRAVE_API_KEY='k')
    def test_scope_decides_what_pages(self):
        health.record_down('sepia', 'x')   # media: watched by nobody here
        self.assertEqual(self.client.get(self.url).status_code, 200)
        health.record_down('brave', 'x')   # a watched search engine
        self.assertEqual(self.client.get(self.url).status_code, 500)

    @override_settings(STATUS_MONITOR_PROVIDERS=['nothing-configured'])
    def test_empty_scope_is_a_500_not_a_clean_bill_of_health(self):
        # Can only be a typo in STATUS_MONITOR_PROVIDERS (the default watches
        # every configured provider), and a monitor stuck green on a typo is
        # worse than no monitor.
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()['status'], 'error')

    def test_database_failure_is_500(self):
        # The rows are unreadable, so searches are broken too: page for it
        # rather than reporting "nothing observed, all good".
        with patch('search.health._status_rows', side_effect=DatabaseError('gone')):
            resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()['status'], 'error')

    def test_both_spellings_answer_directly(self):
        # Monitors get configured with either; neither should hit a redirect.
        self.assertEqual(self.client.get('/status/health').status_code, 200)
        self.assertEqual(self.client.get('/status/health/').status_code, 200)

    @override_settings(STATUS_MONITOR_ENABLED=False)
    def test_disabled_endpoint_is_404(self):
        self.assertEqual(self.client.get(self.url).status_code, 404)

    @override_settings(STATUS_PAGE_ENABLED=False)
    def test_still_answers_with_the_status_page_off(self):
        # The whole point: an instance that doesn't publish the page is still
        # monitorable.
        self.assertEqual(self.client.get(self.url).status_code, 200)

    @override_settings(STATUS_MONITOR_TOKEN='s3cret')
    def test_token_required_when_configured(self):
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.url + '?token=nope').status_code, 403)

    @override_settings(STATUS_MONITOR_TOKEN='s3cret')
    def test_token_accepted_from_query_or_header(self):
        self.assertEqual(self.client.get(self.url + '?token=s3cret').status_code, 200)
        self.assertEqual(
            self.client.get(self.url, headers={'x-monitor-token': 's3cret'}).status_code, 200,
        )
        self.assertEqual(
            self.client.get(self.url, headers={'authorization': 'Bearer s3cret'}).status_code, 200,
        )

    @override_settings(STATUS_MONITOR_TOKEN='s3cret')
    def test_non_ascii_token_is_rejected_not_crashed(self):
        # compare_digest refuses non-ASCII str, hence the bytes comparison.
        self.assertEqual(self.client.get(self.url + '?token=sécret').status_code, 403)


@override_settings(
    STATUS_MONITOR_ENABLED=True, STATUS_MONITOR_TOKEN='', STATUS_MONITOR_PROVIDERS=[],
)
class ProviderStatusMonitorEndpointTests(TestCase):
    """/status/health/<slug>, one 200-or-500 check per upstream provider."""

    def setUp(self):
        _reset_provider_health()

    def _url(self, slug):
        return reverse('search:provider_status_monitor', kwargs={'provider': slug})

    def test_answers_without_a_login(self):
        # Public, like the aggregate endpoint: a monitor can't sign in.
        self.assertEqual(self.client.get(self._url('sepia')).status_code, 200)

    def test_up_provider_is_200(self):
        health.record_ok('sepia')
        resp = self.client.get(self._url('sepia'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'ok')
        self.assertEqual(resp.json()['provider'], 'sepia')
        self.assertEqual(resp.json()['state'], 'up')
        self.assertIsNotNone(resp.json()['checked_at'])
        self.assertEqual(resp['Cache-Control'], 'no-store')

    def test_down_provider_is_500(self):
        health.record_down('sepia', 'kaboom')
        resp = self.client.get(self._url('sepia'))
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()['status'], 'down')
        self.assertEqual(resp.json()['state'], 'down')
        # As with the aggregate endpoint: what failed, never the upstream's words.
        self.assertNotIn('kaboom', resp.content.decode())

    def test_never_observed_provider_is_200_but_says_unknown(self):
        resp = self.client.get(self._url('sepia'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'ok')
        self.assertEqual(resp.json()['state'], 'unknown')

    def test_each_provider_answers_for_itself(self):
        # The point of per-provider checks: one outage reddens one monitor.
        health.record_down('sepia', 'x')
        self.assertEqual(self.client.get(self._url('sepia')).status_code, 500)
        self.assertEqual(self.client.get(self._url('wikipedia')).status_code, 200)

    @override_settings(LIBRETRANSLATE_URL='http://lt.example')
    def test_translate_endpoint_covers_libretranslate(self):
        health.record_down('translate', 'x', source=health.MONITOR_PROBE)
        self.assertEqual(self.client.get(self._url('translate')).status_code, 500)

    def test_unknown_slug_is_404_with_a_json_body(self):
        # JSON like every other reply here, so a typo in a monitor's URL shows
        # up in the response the monitoring service already records.
        resp = self.client.get(self._url('nosuchprovider'))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()['detail'], 'unknown provider')

    @override_settings(BRAVE_API_KEY='')
    def test_unconfigured_provider_is_404_not_200(self):
        # Nothing to report, and a green monitor for a provider that isn't even
        # wired up would be worse than an obviously broken check.
        resp = self.client.get(self._url('brave'))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()['detail'], 'provider not configured on this instance')

    @override_settings(STATUS_MONITOR_PROVIDERS=['engine'])
    def test_aggregate_scope_does_not_narrow_the_per_provider_checks(self):
        # STATUS_MONITOR_PROVIDERS scopes the roll-up; a URL that names its
        # provider always answers for it.
        health.record_down('sepia', 'x')
        self.assertEqual(self.client.get(self._url('sepia')).status_code, 500)

    def test_database_failure_is_500(self):
        with patch('search.health._status_rows', side_effect=DatabaseError('gone')):
            resp = self.client.get(self._url('sepia'))
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()['status'], 'error')

    def test_both_spellings_answer_directly(self):
        self.assertEqual(self.client.get('/status/health/sepia').status_code, 200)
        self.assertEqual(self.client.get('/status/health/sepia/').status_code, 200)

    def test_aggregate_endpoint_still_answers(self):
        # The roll-up and the per-provider checks coexist.
        self.assertEqual(self.client.get('/status/health').status_code, 200)

    @override_settings(STATUS_MONITOR_ENABLED=False)
    def test_disabled_endpoint_is_404(self):
        self.assertEqual(self.client.get(self._url('sepia')).status_code, 404)

    @override_settings(STATUS_PAGE_ENABLED=False)
    def test_still_answers_with_the_status_page_off(self):
        self.assertEqual(self.client.get(self._url('sepia')).status_code, 200)

    @override_settings(STATUS_MONITOR_TOKEN='s3cret')
    def test_token_applies_here_too(self):
        self.assertEqual(self.client.get(self._url('sepia')).status_code, 403)
        self.assertEqual(self.client.get(self._url('sepia') + '?token=s3cret').status_code, 200)

    def test_every_configured_provider_has_a_working_endpoint(self):
        # Whatever this deployment runs, each one is monitorable on its own URL.
        for slug in health.configured_providers():
            self.assertEqual(self.client.get(self._url(slug)).status_code, 200, slug)


class LazyCardsTests(TestCase):
    """Lazy-loading of the web-tab knowledge panel (automatic, JS-gated).

    The results page renders web results + the instant answer immediately and,
    when JS is confirmed (the ``js`` cookie), defers the knowledge cards to
    ``/search/cards/``; with no JS it renders them inline.
    """

    def setUp(self):
        self.user = User.objects.create_user('lazy', password='pass')
        self.client.login(username='lazy', password='pass')

    def _js(self):
        """Simulate a browser that has run base.html's JS-detection snippet."""
        self.client.cookies['js'] = '1'

    # --- gating in the results view ---

    @patch('search.panel.fetch_stackexchange', return_value=None)
    @patch('search.panel.fetch_tripadvisor', return_value=None)
    @patch('search.panel.fetch_thetvdb', return_value=None)
    @patch('search.views.fetch_wikipedia', return_value=None)
    def test_cards_deferred_by_default_with_js(self, mock_wiki, mock_thetvdb, mock_ta, mock_se):
        self._js()
        resp = self.client.get(reverse('search:results') + '?q=python')
        self.assertTrue(resp.context['defer_cards'])
        # No card fetcher runs in the results request, they move to the endpoint.
        mock_wiki.assert_not_called()
        mock_thetvdb.assert_not_called()
        mock_ta.assert_not_called()
        mock_se.assert_not_called()
        self.assertContains(resp, 'data-cards-url')
        self.assertContains(resp, 'cards.js')

    @patch('search.panel.fetch_stackexchange', return_value=None)
    @patch('search.panel.fetch_tripadvisor', return_value=None)
    @patch('search.panel.fetch_thetvdb', return_value=None)
    @patch('search.views.fetch_wikipedia', return_value=None)
    def test_eager_without_js_cookie(self, mock_wiki, mock_thetvdb, mock_ta, mock_se):
        # No `js` cookie → JS unconfirmed (or first visit) → render cards inline.
        resp = self.client.get(reverse('search:results') + '?q=python')
        self.assertFalse(resp.context['defer_cards'])
        mock_wiki.assert_called()

    @patch('search.panel.fetch_stackexchange', return_value=None)
    @patch('search.panel.fetch_tripadvisor')
    @patch('search.panel.fetch_thetvdb', return_value=None)
    @patch('search.views.fetch_wikipedia', return_value=None)
    def test_noindex_when_tripadvisor_card_renders_eagerly(self, mock_wiki, mock_thetvdb, mock_ta, mock_se):
        # TripAdvisor's Master Terms (3.4.3) require pages showing its content
        # to be excluded from search engine indexes.
        mock_ta.return_value = {
            'name': 'Le Restaurant', 'geo': 'Lyon', 'address': '',
            'rating': None, 'rating_bubbles': [], 'num_reviews': 0,
            'description': '', 'url': 'https://www.tripadvisor.com/x',
            'latitude': None, 'longitude': None,
        }
        resp = self.client.get(reverse('search:results') + '?q=le+restaurant')
        self.assertEqual(resp['X-Robots-Tag'], 'noindex')
        self.assertContains(resp, '<meta name="robots" content="noindex, nofollow">')

    @patch('search.panel.fetch_stackexchange', return_value=None)
    @patch('search.panel.fetch_tripadvisor', return_value=None)
    @patch('search.panel.fetch_thetvdb', return_value=None)
    @patch('search.views.fetch_wikipedia', return_value=None)
    def test_no_noindex_without_tripadvisor_card(self, mock_wiki, mock_thetvdb, mock_ta, mock_se):
        resp = self.client.get(reverse('search:results') + '?q=python')
        self.assertNotIn('X-Robots-Tag', resp)
        self.assertNotContains(resp, 'name="robots"')

    @patch('search.panel.fetch_stackexchange', return_value=None)
    @patch('search.panel.fetch_tripadvisor', return_value=None)
    @patch('search.panel.fetch_thetvdb', return_value=None)
    @patch('search.views.fetch_wikipedia', return_value=None)
    def test_eager_param_forces_inline(self, mock_wiki, mock_thetvdb, mock_ta, mock_se):
        # The <noscript> fallback reloads with cards=eager; that forces an inline
        # render even when the js cookie is present (JS turned off after the fact).
        self._js()
        resp = self.client.get(reverse('search:results') + '?q=python&cards=eager')
        self.assertFalse(resp.context['defer_cards'])
        mock_wiki.assert_called()

    def test_page_two_not_deferred(self):
        self._js()
        resp = self.client.get(reverse('search:results') + '?q=python&page=2')
        self.assertFalse(resp.context['defer_cards'])

    def test_noscript_fallback_present_in_skeleton(self):
        self._js()
        resp = self.client.get(reverse('search:results') + '?q=python')
        self.assertContains(resp, 'http-equiv="refresh"')
        self.assertContains(resp, 'cards=eager')
        self.assertContains(resp, 'aria-busy="true"')
        # Template comments must not leak into the HTML (Django {# #} is single-line).
        self.assertNotContains(resp, 'cards.js fetches')
        self.assertNotContains(resp, 'lets the server confirm')

    @override_settings(BRAVE_API_KEY='test-key')
    @patch('web.services._brave_request')
    def test_results_render_immediately_when_deferred(self, mock_brave):
        # The whole point: the answer is on screen even though the cards aren't.
        mock_brave.return_value = {'web': {'results': [
            {'title': 'Test Result', 'url': 'https://test.com', 'description': 'x',
             'meta_url': {'netloc': 'test.com', 'path': ''}, 'profile': {}}]}}
        _set_prefs(self.client, only_engine='brave')
        self._js()
        resp = self.client.get(reverse('search:results') + '?q=hello')
        self.assertTrue(resp.context['defer_cards'])
        self.assertContains(resp, 'Test Result')

    # --- the /search/cards/ endpoint ---

    def test_endpoint_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse('search:cards') + '?q=python')
        self.assertEqual(resp.status_code, 302)

    def test_endpoint_empty_query(self):
        resp = self.client.get(reverse('search:cards'))
        self.assertEqual(resp.json(), {'panel': '', 'instant': ''})

    @patch('search.views.fetch_web', return_value=([], ''))
    @patch('search.panel.fetch_stackexchange', return_value=None)
    @patch('search.panel.fetch_tripadvisor', return_value=None)
    @patch('search.panel.fetch_thetvdb', return_value=None)
    @patch('search.views.fetch_wikipedia')
    def test_endpoint_returns_panel_html(self, mock_wiki, mock_thetvdb, mock_ta, mock_se, mock_web):
        mock_wiki.return_value = {
            'title': 'Python', 'description': 'programming language',
            'extract': 'Python is a language.', 'thumbnail': '',
            'url': 'https://en.wikipedia.org/wiki/Python',
        }
        resp = self.client.get(reverse('search:cards') + '?q=python&instant=0')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('Read on Wikipedia', data['panel'])
        self.assertEqual(data['instant'], '')

    @patch('search.views.fetch_web', return_value=([], ''))
    @patch('search.panel.fetch_stackexchange', return_value=None)
    @patch('search.panel.fetch_tripadvisor', return_value=None)
    @patch('search.panel.fetch_thetvdb', return_value=None)
    @patch('search.views.fetch_wikipedia', return_value=None)
    def test_endpoint_empty_panel_when_no_cards(self, mock_wiki, mock_thetvdb, mock_ta, mock_se, mock_web):
        resp = self.client.get(reverse('search:cards') + '?q=python&instant=0')
        self.assertEqual(resp.json()['panel'], '')

    @patch('search.views.fetch_web', return_value=([], ''))
    @patch('search.panel.fetch_stackexchange', return_value=None)
    @patch('search.panel.fetch_tripadvisor')
    @patch('search.panel.fetch_thetvdb', return_value=None)
    @patch('search.views.fetch_wikipedia', return_value=None)
    def test_endpoint_sets_noindex_header_for_tripadvisor_card(self, mock_wiki, mock_thetvdb, mock_ta, mock_se, mock_web):
        # The deferred path is how the card reaches a JS-enabled page, so it
        # needs the same TripAdvisor non-indexability signal as the eager render.
        mock_ta.return_value = {
            'name': 'Le Restaurant', 'geo': 'Lyon', 'address': '',
            'rating': None, 'rating_bubbles': [], 'num_reviews': 0,
            'description': '', 'url': 'https://www.tripadvisor.com/x',
            'latitude': None, 'longitude': None,
        }
        resp = self.client.get(reverse('search:cards') + '?q=le+restaurant&instant=0')
        self.assertEqual(resp['X-Robots-Tag'], 'noindex')

    @patch('search.views.fetch_web', return_value=([], ''))
    @patch('search.panel.looks_like_place', return_value=True)
    @patch('search.panel.fetch_geocode')
    @patch('search.panel.fetch_stackexchange', return_value=None)
    @patch('search.panel.fetch_tripadvisor', return_value=None)
    @patch('search.panel.fetch_thetvdb', return_value=None)
    @patch('search.views.fetch_wikipedia', return_value=None)
    def test_endpoint_returns_map_answer(self, mock_wiki, mock_thetvdb, mock_ta, mock_se, mock_geo, mock_place, mock_web):
        mock_geo.return_value = [{
            'name': '10 Downing Street', 'display_name': '10 Downing Street, London',
            'lat': 51.5, 'lon': -0.12, 'type': 'house', 'category': 'place',
            'embed_url': 'https://osm/embed', 'mini_embed_url': 'https://osm/embed?mini',
            'osm_url': 'https://osm/x',
        }]
        resp = self.client.get(reverse('search:cards') + '?q=10+Downing+Street&instant=0')
        data = resp.json()
        self.assertIn('Open in Maps', data['instant'])
        mock_geo.assert_called_once()

    @patch('search.views.fetch_web', return_value=([], ''))
    @patch('search.panel.looks_like_place', return_value=True)
    @patch('search.panel.fetch_geocode')
    @patch('search.panel.fetch_stackexchange', return_value=None)
    @patch('search.panel.fetch_tripadvisor', return_value=None)
    @patch('search.panel.fetch_thetvdb', return_value=None)
    @patch('search.views.fetch_wikipedia', return_value=None)
    def test_endpoint_skips_map_when_instant_present(self, mock_wiki, mock_thetvdb, mock_ta, mock_se, mock_geo, mock_place, mock_web):
        # instant=1 means the page already shows an instant answer, the map
        # shares that slot, so it must not geocode or render.
        resp = self.client.get(reverse('search:cards') + '?q=berlin&instant=1')
        self.assertEqual(resp.json()['instant'], '')
        mock_geo.assert_not_called()

    @patch('search.views.fetch_web', return_value=([], ''))
    @patch('search.panel.fetch_stackexchange', return_value=None)
    @patch('search.panel.fetch_tripadvisor', return_value=None)
    @patch('search.panel.fetch_thetvdb', return_value=None)
    @patch('search.views.fetch_wikipedia')
    def test_endpoint_respects_disabled_source(self, mock_wiki, mock_thetvdb, mock_ta, mock_se, mock_web):
        _set_prefs(self.client, providers_off=['wikipedia'])
        self.client.get(reverse('search:cards') + '?q=python&instant=0')
        mock_wiki.assert_not_called()


class MobileCardExpandTests(TestCase):
    """The mobile "Show more" toggle on the knowledge cards.

    The knowledge panel stacks above the web results on mobile, so the cards
    render trimmed there (hero image, cast/crew/tagline dropped, a tighter clamp
    on the summary). With JavaScript a footer button puts ``.card-open`` on the
    card, and the ``card-open:`` Tailwind variant restores every trimmed part;
    without it the button isn't shown at all and the card stays as it was.
    """

    WIKI_CARD = {
        'title': 'Python', 'description': 'programming language',
        'extract': 'Python is a language.', 'thumbnail': '',
        'url': 'https://en.wikipedia.org/wiki/Python',
    }

    def setUp(self):
        self.user = User.objects.create_user('expand', password='pass')
        self.client.login(username='expand', password='pass')

    def _results(self, query='python'):
        """Eager (no ``js`` cookie) results page carrying a Wikipedia card."""
        with patch('search.views.fetch_wikipedia', return_value=self.WIKI_CARD), \
             patch('search.panel.fetch_thetvdb', return_value=None), \
             patch('search.panel.fetch_tripadvisor', return_value=None), \
             patch('search.panel.fetch_stackexchange', return_value=None):
            return self.client.get(reverse('search:results') + '?q=' + query)

    def test_card_ships_the_toggle(self):
        resp = self._results()
        self.assertContains(resp, 'data-card-toggle')
        self.assertContains(resp, 'Show more')
        self.assertContains(resp, 'Show less')
        # The button targets the card root it sits in.
        self.assertContains(resp, 'data-card')

    def test_toggle_is_js_and_mobile_only(self):
        # `js:` keeps it off the page when scripting never ran (the button would
        # do nothing), `max-sm:` because from `sm` up the card is already whole.
        self.assertContains(self._results(), 'js:max-sm:inline-flex')

    def test_trimmed_parts_carry_the_expanded_display(self):
        # Every part hidden on mobile names what it becomes once expanded, right
        # next to the `sm:` class that shows it on a wider screen.
        resp = self._results()
        self.assertContains(resp, 'hidden card-open:flex sm:flex')
        self.assertContains(resp, 'line-clamp-3 card-open:line-clamp-none sm:line-clamp-5')

    def test_expand_script_loaded_on_the_eager_path(self):
        self.assertContains(self._results(), 'cards/expand.js')

    def test_expand_script_loaded_on_the_deferred_path(self):
        # Deferred cards arrive by innerHTML, which never runs the scripts it
        # inserts, so the page itself must carry the (delegating) listener.
        self.client.cookies['js'] = '1'
        with patch('search.views.fetch_wikipedia', return_value=None):
            resp = self.client.get(reverse('search:results') + '?q=python')
        self.assertTrue(resp.context['defer_cards'])
        self.assertContains(resp, 'cards/expand.js')

    def test_no_script_when_the_page_has_no_cards(self):
        with patch('search.views.fetch_wikipedia', return_value=None), \
             patch('search.panel.fetch_thetvdb', return_value=None), \
             patch('search.panel.fetch_tripadvisor', return_value=None), \
             patch('search.panel.fetch_stackexchange', return_value=None):
            resp = self.client.get(reverse('search:results') + '?q=python')
        self.assertNotContains(resp, 'cards/expand.js')

    @patch('search.views.fetch_web', return_value=([], ''))
    @patch('search.panel.fetch_stackexchange', return_value=None)
    @patch('search.panel.fetch_tripadvisor', return_value=None)
    @patch('search.panel.fetch_thetvdb', return_value=None)
    @patch('search.views.fetch_wikipedia')
    def test_deferred_panel_html_carries_the_toggle(self, mock_wiki, mock_tvdb, mock_ta, mock_se, mock_web):
        mock_wiki.return_value = self.WIKI_CARD
        resp = self.client.get(reverse('search:cards') + '?q=python&instant=0')
        self.assertIn('data-card-toggle', resp.json()['panel'])

    @patch('search.panel.fetch_stackexchange', return_value=None)
    @patch('search.panel.fetch_tripadvisor')
    @patch('search.panel.fetch_thetvdb', return_value=None)
    @patch('search.views.fetch_wikipedia', return_value=None)
    def test_tripadvisor_card_expands_too(self, mock_wiki, mock_tvdb, mock_ta, mock_se):
        mock_ta.return_value = {
            'name': 'Le Restaurant', 'geo': 'Lyon', 'address': '',
            'rating': None, 'rating_bubbles': [], 'num_reviews': 0,
            'description': 'A restaurant.',
            'url': 'https://www.tripadvisor.com/x', 'latitude': None, 'longitude': None,
        }
        resp = self.client.get(reverse('search:results') + '?q=le+restaurant')
        self.assertContains(resp, 'data-card-toggle')
        # The header is the card's collapsed-on-mobile block, and the
        # description un-clamps with it.
        self.assertContains(resp, 'hidden card-open:flex sm:flex')
        self.assertContains(resp, 'card-open:line-clamp-none')

    SE_CARD = {
        'site_name': 'Stack Overflow', 'title': 'How do I x?',
        'url': 'https://stackoverflow.com/q/1', 'score': 5, 'answer_count': 2,
        'is_answered': True, 'view_count': 100, 'tags': ['python'],
        'answer': {
            'body': '<p>Do y.</p>', 'score': 9, 'is_accepted': True,
            'is_long': True, 'url': 'https://stackoverflow.com/a/2',
        },
    }

    @patch('search.panel.fetch_stackexchange')
    @patch('search.panel.fetch_tripadvisor', return_value=None)
    @patch('search.panel.fetch_thetvdb', return_value=None)
    @patch('search.views.fetch_wikipedia', return_value=None)
    def test_stack_exchange_read_more_ships_both_labels(self, mock_wiki, mock_tvdb, mock_ta, mock_se):
        # The answer toggle used to be wired by a <script> inside the card, which
        # the deferred path silently dropped. Both labels are markup now, so
        # expand.js can swap them without carrying translated strings.
        mock_se.return_value = self.SE_CARD
        resp = self.client.get(reverse('search:results') + '?q=how+do+i+x')
        self.assertContains(resp, 'data-se-readmore')
        self.assertContains(resp, 'Read more')
        self.assertContains(resp, 'Show less')

    @patch('search.views.fetch_web', return_value=([], ''))
    @patch('search.panel.fetch_stackexchange')
    @patch('search.panel.fetch_tripadvisor', return_value=None)
    @patch('search.panel.fetch_thetvdb', return_value=None)
    @patch('search.views.fetch_wikipedia', return_value=None)
    def test_deferred_panel_carries_no_inline_script(self, mock_wiki, mock_tvdb, mock_ta, mock_se, mock_web):
        # cards.js injects this HTML with innerHTML, which never executes the
        # scripts it inserts, so a card that needs JS must be served by a real
        # <script src> on the page, never by an inline one of its own.
        mock_se.return_value = self.SE_CARD
        resp = self.client.get(reverse('search:cards') + '?q=how+do+i+x&instant=0')
        self.assertNotIn('<script', resp.json()['panel'])


class SearchBarClearButtonTests(TestCase):
    """The clear ("×") button in the search bar.

    The native ``::-webkit-search-cancel-button`` is suppressed project-wide
    (WebKit-only, unstyleable), so both search bars — the homepage box and the
    compact one in the results header — carry their own button instead. It
    empties the field and hands the caret back; it never submits or navigates.
    Visibility is entirely search/static/search/clear.js's job, so the markup
    ships ``hidden`` and a visitor without scripting is offered no dead control.
    """

    def setUp(self):
        self.user = User.objects.create_user('clara', password='pass')
        self.client.login(username='clara', password='pass')

    def _pages(self):
        """Both places the main search bar renders (offline: nothing upstream
        matters here, only the shell around the field)."""
        with patch('search.views.fetch_web', return_value=([], '')), \
             patch('search.views.fetch_wikipedia', return_value=None), \
             patch('search.panel.fetch_thetvdb', return_value=None), \
             patch('search.panel.fetch_tripadvisor', return_value=None), \
             patch('search.panel.fetch_stackexchange', return_value=None):
            results = self.client.get(reverse('search:results') + '?q=python')
        return {
            'index': self.client.get(reverse('search:index')),
            'results': results,
        }

    def test_both_search_bars_ship_the_button(self):
        for page, resp in self._pages().items():
            with self.subTest(page=page):
                self.assertContains(resp, 'data-search-clear')
                self.assertContains(resp, 'Clear search')

    def test_button_never_submits_the_form(self):
        # type="button": clearing is a local edit of the field, not a search.
        # A default-type button inside the form would run the query instead.
        for page, resp in self._pages().items():
            with self.subTest(page=page):
                html = resp.content.decode()
                button = html[html.index('<button type="button" data-search-clear'):]
                self.assertTrue(button.startswith('<button type="button"'))

    def test_button_ships_hidden(self):
        # Nothing but clear.js ever unhides it, so with scripting off (or before
        # the script runs) there is no button to click and nothing to explain.
        for page, resp in self._pages().items():
            with self.subTest(page=page):
                html = resp.content.decode()
                button = html[html.index('data-search-clear'):]
                self.assertIn('class="hidden ', button[:button.index('>')])

    def test_button_sits_inside_the_search_form_before_submit(self):
        # It has to be a child of the search form (that's how clear.js finds the
        # input), and rendered before the submit pill so it reads inside the bar.
        for page, resp in self._pages().items():
            with self.subTest(page=page):
                html = resp.content.decode()
                clear = html.index('data-search-clear')
                self.assertLess(html.index('type="search"'), clear)
                self.assertLess(clear, html.index('type="submit"', clear))
                self.assertLess(clear, html.index('</form>', clear))

    def test_clear_script_loaded_on_both_pages(self):
        for page, resp in self._pages().items():
            with self.subTest(page=page):
                self.assertContains(resp, 'search/clear.js')

    def test_no_template_comment_leaks_into_html(self):
        # The partial's rationale is a {% comment %} block, not {# #} (which is
        # single-line only, so a multi-line one leaks into the page as text).
        for page, resp in self._pages().items():
            with self.subTest(page=page):
                self.assertNotContains(resp, 'Progressive enhancement')
                self.assertNotContains(resp, 'endcomment')
