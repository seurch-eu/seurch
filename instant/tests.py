"""Tests for the instant-answer tools and their dispatch.

These tests were generated with an LLM and then reviewed by hand. Treat a
failure as a real signal, but read the assertion before trusting it: a test
here can encode an assumption the code never promised. Fix or delete such a
test rather than bending the code to satisfy it.
"""

import json
import math
import time
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from . import currency, places, tools, weather
from .detect import detect
from .models import CurrencyRate


class MathTests(TestCase):
    def test_basic(self):
        self.assertEqual(tools.math_answer('2+2')['result'], '4')
        self.assertEqual(tools.math_answer('(3*4)/2')['result'], '6')
        self.assertEqual(tools.math_answer('2^10')['result'], '1,024')

    def test_functions_and_constants(self):
        self.assertEqual(tools.math_answer('sqrt(16)')['result'], '4')
        self.assertEqual(tools.math_answer('factorial(5)')['result'], '120')
        self.assertTrue(tools.math_answer('pi*2')['result'].startswith('6.28'))

    def test_percent_of(self):
        self.assertEqual(tools.math_answer('15% of 200')['result'], '30')

    def test_not_math(self):
        self.assertIsNone(tools.math_answer('hello world'))
        self.assertIsNone(tools.math_answer('2024'))  # no operator

    def test_injection_blocked(self):
        self.assertIsNone(tools.math_answer('__import__("os")'))
        self.assertIsNone(tools.math_answer('1/0'))

    def test_huge_computations_refused(self):
        # Unbounded ** / factorial would pin a worker's CPU and memory (DoS);
        # over-cap operands must be refused quickly, not computed.
        start = time.monotonic()
        self.assertIsNone(tools.math_answer('9^9^9'))
        self.assertIsNone(tools.math_answer('factorial(1000000)'))
        self.assertIsNone(tools.math_answer('(2^1000)^1000'))
        self.assertIsNone(tools.math_answer('factorial(factorial(1000))'))
        self.assertLess(time.monotonic() - start, 1.0)

    def test_reasonable_powers_and_factorials_still_work(self):
        self.assertEqual(tools.math_answer('2^100')['raw'], 2 ** 100)
        self.assertEqual(tools.math_answer('factorial(20)')['raw'], math.factorial(20))
        self.assertEqual(tools.math_answer('2^-2')['result'], '0.25')


class BaseConversionTests(TestCase):
    def test_prefixed(self):
        r = tools.base_answer('0xff')
        self.assertEqual(r['dec'], '255')
        self.assertEqual(r['hex'], 'FF')
        self.assertEqual(r['bin'], '11111111')

    def test_to_base(self):
        self.assertEqual(tools.base_answer('255 in hex')['hex'], 'FF')
        self.assertEqual(tools.base_answer('binary 1010 to decimal')['dec'], '10')

    def test_not_base(self):
        self.assertIsNone(tools.base_answer('hello'))


class ColorTests(TestCase):
    def test_hex(self):
        r = tools.color_answer('#ff0000')
        self.assertEqual(r['rgb'], 'rgb(255, 0, 0)')
        self.assertEqual(r['hsl'], 'hsl(0, 100%, 50%)')

    def test_rgb_and_hsl(self):
        self.assertEqual(tools.color_answer('rgb(255, 128, 0)')['hex'], '#FF8000')
        self.assertEqual(tools.color_answer('hsl(0, 100%, 50%)')['hex'], '#FF0000')

    def test_keyword_default(self):
        self.assertIsNotNone(tools.color_answer('color picker'))

    def test_three_letter_words_not_colors(self):
        # "bed"/"dad" are valid 3-hex but must not hijack a search.
        self.assertIsNone(tools.color_answer('bed'))
        self.assertIsNone(tools.color_answer('dad'))


class UnitTests(TestCase):
    def test_length(self):
        r = tools.unit_answer('5 km to miles')
        self.assertEqual(r['category'], 'length')
        self.assertTrue(r['result'].startswith('3.10'))

    def test_temperature(self):
        r = tools.unit_answer('100 f to c')
        self.assertTrue(r['result'].startswith('37.7'))

    def test_mismatched_categories(self):
        self.assertIsNone(tools.unit_answer('5 km to kg'))


class EncodeTests(TestCase):
    def test_base64_roundtrip(self):
        self.assertEqual(tools.encode_answer('base64 encode hello')['output'], 'aGVsbG8=')
        self.assertEqual(tools.encode_answer('base64 decode aGVsbG8=')['output'], 'hello')

    def test_url(self):
        self.assertEqual(tools.encode_answer('url encode a b&c')['output'], 'a+b%26c')

    def test_requires_action_word(self):
        self.assertIsNone(tools.encode_answer('url shortener'))


class HashTests(TestCase):
    def test_md5(self):
        r = tools.hash_answer('md5 hello')
        self.assertEqual(r['hashes']['md5'], '5d41402abc4b2a76b9719d911017c592')
        self.assertEqual(r['highlight'], 'md5')

    def test_generic_requires_of(self):
        self.assertIsNone(tools.hash_answer('hash browns recipe'))
        self.assertIsNotNone(tools.hash_answer('hash of test'))


class HttpStatusTests(TestCase):
    def test_known_error(self):
        r = tools.http_answer('404')
        self.assertEqual(r['name'], 'Not Found')
        self.assertTrue(r['is_error'])

    def test_with_keyword(self):
        self.assertEqual(tools.http_answer('http status 200')['name'], 'OK')

    def test_bare_success_needs_keyword(self):
        self.assertIsNone(tools.http_answer('200'))

    def test_unknown_code(self):
        self.assertIsNone(tools.http_answer('999'))


class PortTests(TestCase):
    def test_known(self):
        r = tools.port_answer('port 443')
        self.assertEqual(r['service'], 'HTTPS')
        self.assertTrue(r['known'])

    def test_out_of_range(self):
        self.assertIsNone(tools.port_answer('port 99999'))

    def test_unknown_but_valid(self):
        r = tools.port_answer('port 12345')
        self.assertFalse(r['known'])


class RandomiserTests(TestCase):
    def test_random_range(self):
        r = tools.random_answer('random number 1-10')
        self.assertTrue(1 <= r['value'] <= 10)

    def test_dice(self):
        r = tools.dice_answer('roll 2d6')
        self.assertEqual(r['count'], 2)
        self.assertEqual(len(r['rolls']), 2)
        self.assertTrue(2 <= r['total'] <= 12)

    def test_coin(self):
        self.assertIn(tools.coin_answer('flip a coin')['result'], ('Heads', 'Tails'))


class TimerTests(TestCase):
    def test_duration(self):
        self.assertEqual(tools.timer_answer('timer 5 minutes')['seconds'], 300)
        self.assertEqual(tools.timer_answer('10 min timer')['seconds'], 600)

    def test_stopwatch(self):
        self.assertEqual(tools.timer_answer('stopwatch')['mode'], 'stopwatch')


class WorldClockTests(TestCase):
    def test_city(self):
        r = tools.worldclock_answer('time in tokyo')
        self.assertEqual(r['tz'], 'Asia/Tokyo')
        self.assertRegex(r['time'], r'^\d{2}:\d{2}$')
        self.assertTrue(r['offset'].startswith('UTC'))

    def test_phrasing(self):
        self.assertIsNotNone(tools.worldclock_answer('what time is it in new york'))

    def test_unknown_city(self):
        self.assertIsNone(tools.worldclock_answer('time in zzzznowhere'))


class MiscToolTests(TestCase):
    def test_uuid(self):
        r = tools.uuid_answer('uuid')
        self.assertRegex(r['value'], r'^[0-9a-f-]{36}$')
        self.assertEqual(len(r['values']), 5)

    def test_qr(self):
        r = tools.qr_answer('qr code hello')
        self.assertEqual(r['text'], 'hello')
        self.assertIn('<svg', r['svg'])

    def test_json_valid(self):
        r = tools.json_answer('json {"a": 1}')
        self.assertTrue(r['valid'])
        self.assertIn('"a": 1', r['formatted'])

    def test_json_invalid(self):
        r = tools.json_answer('json {bad}')
        self.assertFalse(r['valid'])

    def test_regex_trigger(self):
        self.assertEqual(tools.regex_answer('regex tester')['type'], 'regex')

    def test_ip(self):
        # Left-most X-Forwarded-For entry (the real client) wins over the
        # trailing proxy hop.
        rf = RequestFactory()
        req = rf.get('/', HTTP_X_FORWARDED_FOR='8.8.8.8, 10.0.0.1')
        r = tools.ip_answer("what's my ip", req)
        self.assertEqual(r['ip'], '8.8.8.8')
        self.assertFalse(r['is_local'])

    def test_ip_public_v6(self):
        rf = RequestFactory()
        req = rf.get('/', HTTP_X_FORWARDED_FOR='2606:4700:4700::1111, fd00::1')
        r = tools.ip_answer("what's my ip", req)
        self.assertEqual(r['ip'], '2606:4700:4700::1111')
        self.assertFalse(r['is_local'])

    def test_ip_private_v6_is_flagged(self):
        # IPv6 ULA (fc00::/7) and link-local (fe80::/10) must be detected as
        # local so the "behind a proxy your public IP may differ" hint shows.
        rf = RequestFactory()
        for addr in ('fd12:3456:789a::1', 'fe80::1', '::1'):
            r = tools.ip_answer('my ip', rf.get('/', HTTP_X_FORWARDED_FOR=addr))
            self.assertEqual(r['ip'], addr)
            self.assertTrue(r['is_local'], addr)

    def test_ip_docker_proxy_address_is_flagged(self):
        # The production symptom: the proxy forwards a private Docker-network
        # address instead of the real client IP.
        rf = RequestFactory()
        req = rf.get('/', HTTP_X_FORWARDED_FOR='172.18.0.1')
        r = tools.ip_answer('my ip', req)
        self.assertTrue(r['is_local'])

    def test_ip_public_172_not_flagged(self):
        # 172.32.x is public; only 172.16.0.0/12 is private. The old
        # startswith('172.') check wrongly flagged the whole 172/8 block.
        rf = RequestFactory()
        req = rf.get('/', HTTP_X_FORWARDED_FOR='172.32.0.5')
        r = tools.ip_answer('my ip', req)
        self.assertFalse(r['is_local'])

    def test_ip_strips_port_and_brackets(self):
        rf = RequestFactory()
        cases = {
            '203.0.113.7:54321': '203.0.113.7',
            '[2606:4700:4700::1111]:443': '2606:4700:4700::1111',
            'fe80::1%eth0': 'fe80::1',
        }
        for raw, expected in cases.items():
            r = tools.ip_answer('my ip', rf.get('/', HTTP_X_FORWARDED_FOR=raw))
            self.assertEqual(r['ip'], expected, raw)

    def test_ip_falls_back_to_remote_addr(self):
        rf = RequestFactory()
        req = rf.get('/', REMOTE_ADDR='2606:4700:4700::1111')
        r = tools.ip_answer('my ip', req)
        self.assertEqual(r['ip'], '2606:4700:4700::1111')
        self.assertFalse(r['is_local'])


class TimestampTests(TestCase):
    def test_keyword_current(self):
        r = tools.timestamp_answer('unix timestamp')
        self.assertEqual(r['type'], 'timestamp')
        self.assertFalse(r['given'])
        self.assertGreater(r['epoch'], 1_600_000_000)

    def test_explicit_seconds(self):
        r = tools.timestamp_answer('timestamp 1700000000')
        self.assertTrue(r['given'])
        self.assertEqual(r['utc'], '2023-11-14 22:13:20 UTC')

    def test_to_date_form(self):
        self.assertEqual(tools.timestamp_answer('1700000000 to date')['epoch'], 1700000000)

    def test_milliseconds(self):
        # 13-digit values are treated as milliseconds.
        self.assertEqual(tools.timestamp_answer('epoch 1700000000000')['utc'], '2023-11-14 22:13:20 UTC')

    def test_not_a_timestamp(self):
        self.assertIsNone(tools.timestamp_answer('1700000000'))  # bare number, no keyword
        self.assertIsNone(tools.timestamp_answer('hello'))


class PasswordTests(TestCase):
    def test_generates(self):
        r = tools.password_answer('password generator')
        self.assertEqual(r['type'], 'password')
        self.assertEqual(len(r['value']), 16)

    def test_charset_excludes_ambiguous(self):
        pw = tools.generate_password(2000)
        for ch in 'lIO01':
            self.assertNotIn(ch, pw)

    def test_options(self):
        digits_only = tools.generate_password(50, lower=False, upper=False, symbols=False)
        self.assertTrue(all(c in '23456789' for c in digits_only))

    def test_requires_qualifier(self):
        self.assertIsNone(tools.password_answer('password'))  # bare word, no qualifier
        self.assertIsNotNone(tools.password_answer('générer un mot de passe'))


class CoinFalsePositiveTests(TestCase):
    def test_common_words_not_coin(self):
        for q in ('piece', 'moneda', 'munt', 'a piece of cake'):
            self.assertIsNone(tools.coin_answer(q), f'{q!r} should not flip a coin')

    def test_clear_phrases_still_work(self):
        for q in ('flip a coin', 'pile ou face', 'kopf oder zahl', 'cara o cruz'):
            self.assertIsNotNone(tools.coin_answer(q))


class DetectOrderingTests(TestCase):
    def _type(self, q):
        r = detect(q, None)
        return r['type'] if r else None

    def test_positives(self):
        cases = {
            '2+2': 'math', '0xff in decimal': 'base', '#00ff00': 'color',
            'uuid': 'uuid', 'md5 test': 'hash', 'base64 encode hi': 'encode',
            'json formatter': 'json', 'regex tester': 'regex', 'http 404': 'http',
            'port 22': 'port', 'roll 2d6': 'dice', 'flip a coin': 'coin',
            'random number': 'random', 'timer': 'timer', 'time in london': 'worldclock',
            'qr code hi': 'qr', '5 km to miles': 'unit',
        }
        for q, t in cases.items():
            self.assertEqual(self._type(q), t, f'{q!r} should be {t}')

    def test_negatives(self):
        for q in ('python tutorial', 'how to bake bread', 'wall street journal',
                  'url shortener', 'hash browns', 'the office', 'covid-19', 'bed'):
            self.assertIsNone(self._type(q), f'{q!r} should not be an instant answer')

    def test_long_query_ignored(self):
        self.assertIsNone(detect('2+2 ' * 100, None))


class MultilingualTests(TestCase):
    """Triggers must fire in every language the app supports (en/fr/de/es/it/pt/nl)."""

    def _type(self, q):
        r = detect(q, None)
        return r['type'] if r else None

    def test_worldclock_languages(self):
        cases = {
            'heure à paris': 'Europe/Paris',
            'uhrzeit in berlin': 'Europe/Berlin',
            'hora en madrid': 'Europe/Madrid',
            'che ore sono a roma': 'Europe/Rome',
            'que horas são em lisboa': 'Europe/Lisbon',
            'hoe laat is het in amsterdam': 'Europe/Amsterdam',
            'wie spät ist es in berlin': 'Europe/Berlin',
            'quelle heure est-il à londres': 'Europe/London',
        }
        for q, tz in cases.items():
            r = detect(q, None)
            self.assertIsNotNone(r, f'{q!r} should match')
            self.assertEqual(r['type'], 'worldclock')
            self.assertEqual(r['tz'], tz, f'{q!r}')

    def test_keyword_tools_languages(self):
        cases = {
            'mon adresse ip': 'ip', 'meine ip': 'ip', 'mi ip': 'ip', 'il mio ip': 'ip',
            'meu ip': 'ip', 'mijn ip': 'ip',
            'couleur': 'color', 'farbe': 'color', 'sélecteur de couleur': 'color',
            'base64 encoder bonjour': 'encode', 'url décoder a%20b': 'encode',
            'md5 de bonjour': 'hash', 'sha256 von test': 'hash',
            'nombre aléatoire': 'random', 'zufallszahl': 'random',
            'lancer un dé': 'dice', 'würfeln': 'dice', 'tirar dado': 'dice',
            'pile ou face': 'coin', 'kopf oder zahl': 'coin', 'cara o cruz': 'coin',
            'minuteur': 'timer', 'chronomètre': 'timer', 'stoppuhr': 'timer',
            'puerto 443': 'port', 'porta 22': 'port',
            '255 en binaire': 'base', 'convertir 255 en hexadécimal': 'base',
            'expression régulière': 'regex', 'json valideren': 'json',
        }
        for q, t in cases.items():
            self.assertEqual(self._type(q), t, f'{q!r} should be {t}')

    def test_unit_conversion_languages(self):
        self.assertEqual(self._type('100 km en miles'), 'unit')      # fr connector
        self.assertEqual(self._type('10 kg in lb'), 'unit')
        self.assertEqual(self._type('5 kilomètres en miles'), 'unit')  # localized word
        r = detect('2 livres en kg', None)                            # localized pound → mass
        self.assertEqual(r['category'], 'mass')

    def test_timer_duration_languages(self):
        self.assertEqual(detect('temporizador 5 minutos', None)['seconds'], 300)
        self.assertEqual(detect('minuteur 10 minutes', None)['seconds'], 600)

    def test_qr_languages(self):
        self.assertEqual(detect('générer qr code bonjour', None)['text'], 'bonjour')
        self.assertEqual(detect('código qr hola', None)['text'], 'hola')

    def test_currency_connectors_languages(self):
        rates = {'rates': {'USD': 1.0, 'EUR': 0.9, 'GBP': 0.8}, 'date': 'x', 'stale': False}
        with patch.object(currency, 'get_rate_table', return_value=rates):
            self.assertEqual(currency.answer('100 usd en eur')['to_code'], 'EUR')         # fr
            self.assertEqual(currency.answer('convertir 50 gbp en usd')['from_code'], 'GBP')
            self.assertEqual(currency.answer('100 dólares a euros')['from_code'], 'USD')   # es

    @patch('instant.weather._forecast')
    @patch('instant.weather._geocode')
    def test_weather_languages(self, mock_geo, mock_fc):
        mock_geo.return_value = {'name': 'X', 'admin1': '', 'country': '', 'country_code': '',
                                 'lat': 1.0, 'lon': 1.0, 'timezone': 'UTC'}
        mock_fc.return_value = {
            'current': {'temperature_2m': 1, 'apparent_temperature': 1, 'weather_code': 0,
                        'relative_humidity_2m': 1, 'wind_speed_10m': 1, 'wind_direction_10m': 1, 'is_day': 1},
            'daily': {'time': ['2026-06-04'], 'weather_code': [0], 'temperature_2m_max': [1], 'temperature_2m_min': [1]},
        }
        for q in ('météo à paris', 'wetter berlin', 'el tiempo en madrid', 'meteo roma',
                  'tempo em lisboa', 'weer in amsterdam'):
            self.assertIsNotNone(weather.answer(q), f'{q!r} should match')

    def test_negatives_still_hold(self):
        for q in ('tutoriel python', 'comment faire du pain', 'the office', 'bed'):
            self.assertIsNone(self._type(q), f'{q!r} should not be an instant answer')


class CurrencyTests(TestCase):
    RATES = {'rates': {'USD': 1.0, 'EUR': 0.9, 'GBP': 0.8, 'JPY': 150.0},
             'date': '2026-06-04', 'fetched_at': timezone.now(), 'stale': False}

    def test_parse_conversion(self):
        with patch.object(currency, 'get_rate_table', return_value=self.RATES):
            r = currency.answer('100 usd to eur')
        self.assertEqual(r['from_code'], 'USD')
        self.assertEqual(r['to_code'], 'EUR')
        self.assertEqual(r['result'], '90')

    def test_symbol_and_words(self):
        with patch.object(currency, 'get_rate_table', return_value=self.RATES):
            self.assertEqual(currency.answer('£10 to usd')['from_code'], 'GBP')
            self.assertEqual(currency.answer('5 dollars in euros')['from_code'], 'USD')

    def test_same_currency_skipped(self):
        with patch.object(currency, 'get_rate_table', return_value=self.RATES):
            self.assertIsNone(currency.answer('100 usd to usd'))

    @patch('instant.currency._fetch_rates')
    def test_rate_table_caches(self, mock_fetch):
        mock_fetch.return_value = {'rates': {'USD': 1.0, 'EUR': 0.9}, 'date': '2026-06-04'}
        first = currency.get_rate_table()
        self.assertEqual(first['rates']['EUR'], 0.9)
        currency.get_rate_table()
        self.assertEqual(mock_fetch.call_count, 1)  # second call served from DB

    @patch('instant.currency._fetch_rates', return_value=None)
    def test_cold_failure_returns_none(self, _mock):
        self.assertIsNone(currency.get_rate_table())

    def test_stale_served_on_failure(self):
        row = CurrencyRate.objects.create(base='USD', rates={'USD': 1.0, 'EUR': 0.7},
                                          rate_date='old', provider='x')
        CurrencyRate.objects.filter(pk=row.pk).update(
            fetched_at=timezone.now() - timedelta(hours=48))
        with patch('instant.currency._fetch_rates', return_value=None):
            table = currency.get_rate_table()
        self.assertTrue(table['stale'])
        self.assertEqual(table['rates']['EUR'], 0.7)


class WeatherTests(TestCase):
    @patch('instant.weather._forecast')
    @patch('instant.weather._geocode')
    def test_answer(self, mock_geo, mock_fc):
        mock_geo.return_value = {'name': 'Paris', 'admin1': 'Île-de-France', 'country': 'France',
                                 'country_code': 'FR', 'lat': 48.85, 'lon': 2.35, 'timezone': 'Europe/Paris'}
        mock_fc.return_value = {
            'current': {'temperature_2m': 18.2, 'apparent_temperature': 16.5, 'weather_code': 3,
                        'relative_humidity_2m': 72, 'wind_speed_10m': 14, 'wind_direction_10m': 225, 'is_day': 1},
            'daily': {'time': ['2026-06-04', '2026-06-05'], 'weather_code': [3, 1],
                      'temperature_2m_max': [18, 20], 'temperature_2m_min': [10, 12]},
        }
        card = weather.answer('weather in paris')
        self.assertEqual(card['temp_c'], 18)
        self.assertEqual(card['temp_f'], 64)
        self.assertEqual(card['location'], 'Paris, Île-de-France')
        self.assertEqual(len(card['daily']), 2)
        self.assertTrue(card['daily'][0]['is_today'])

    @patch('instant.weather._geocode', return_value=None)
    def test_unknown_place(self, _mock):
        self.assertIsNone(weather.answer('weather in zzzznowhere'))

    @patch('instant.weather._forecast')
    @patch('instant.weather._geocode')
    def test_result_cached_for_one_hour(self, mock_geo, mock_fc):
        mock_geo.return_value = {'name': 'Paris', 'admin1': '', 'country': 'France',
                                 'country_code': 'FR', 'lat': 48.85, 'lon': 2.35, 'timezone': 'auto'}
        mock_fc.return_value = {
            'current': {'temperature_2m': 18, 'apparent_temperature': 17, 'weather_code': 0,
                        'relative_humidity_2m': 50, 'wind_speed_10m': 5,
                        'wind_direction_10m': 90, 'is_day': 1},
            'daily': {'time': ['2026-06-04'], 'weather_code': [0],
                      'temperature_2m_max': [20], 'temperature_2m_min': [10]},
        }
        first = weather.get_weather('paris')
        second = weather.get_weather('paris')
        self.assertEqual(second, first)
        # The second lookup is served from the cache, upstream hit only once.
        self.assertEqual(mock_geo.call_count, 1)
        self.assertEqual(mock_fc.call_count, 1)
        # And the cache window is one hour.
        self.assertEqual(weather.WEATHER_TTL, timedelta(hours=1))


class InstantViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('zoe', password='pass')
        self.client.login(username='zoe', password='pass')

    @patch('search.views.fetch_wikipedia', return_value=None)
    def test_math_card_renders(self, _w):
        resp = self.client.get(reverse('search:results') + '?q=2%2B2')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.context['instant_answer'])
        self.assertEqual(resp.context['instant_answer']['type'], 'math')
        self.assertContains(resp, 'Calculator')
        self.assertContains(resp, 'instant/instant.js')

    @patch('search.views.fetch_wikipedia', return_value=None)
    def test_uuid_card_renders(self, _w):
        resp = self.client.get(reverse('search:results') + '?q=uuid')
        self.assertEqual(resp.context['instant_answer']['type'], 'uuid')
        self.assertContains(resp, 'UUID generator')

    @patch('search.views.fetch_wikipedia', return_value=None)
    def test_plain_query_has_no_instant_answer(self, _w):
        resp = self.client.get(reverse('search:results') + '?q=python+tutorial')
        self.assertIsNone(resp.context['instant_answer'])

    @patch('search.views.fetch_wikipedia', return_value=None)
    def test_instant_only_on_page_one(self, _w):
        resp = self.client.get(reverse('search:results') + '?q=uuid&page=2')
        self.assertIsNone(resp.context['instant_answer'])


class EndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ned', password='pass')
        self.client.login(username='ned', password='pass')

    def test_qr_svg(self):
        resp = self.client.get(reverse('instant:qr_svg') + '?text=hello')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'image/svg+xml')
        self.assertIn(b'<svg', resp.content)

    def test_qr_svg_empty(self):
        resp = self.client.get(reverse('instant:qr_svg') + '?text=')
        self.assertEqual(resp.status_code, 400)

    def test_hash_endpoint(self):
        resp = self.client.get(reverse('instant:hashes') + '?text=hello')
        data = json.loads(resp.content)
        self.assertEqual(data['md5'], '5d41402abc4b2a76b9719d911017c592')

    def test_login_required(self):
        self.client.logout()
        resp = self.client.get(reverse('instant:qr_svg') + '?text=hi')
        self.assertEqual(resp.status_code, 302)


class MapAnswerTests(TestCase):
    def test_wraps_place(self):
        place = {'name': 'Paris', 'display_name': 'Paris, France', 'type': 'city',
                 'mini_embed_url': 'https://osm/mini', 'osm_url': 'https://osm/paris'}
        ans = places.map_answer(place, 'paris')
        self.assertEqual(ans['type'], 'map')
        self.assertEqual(ans['place'], place)
        self.assertEqual(ans['query'], 'paris')

    def test_no_place_is_none(self):
        self.assertIsNone(places.map_answer(None, 'paris'))
        self.assertIsNone(places.map_answer([], 'paris'))
