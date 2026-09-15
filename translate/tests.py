"""Tests for the translation tab and its LibreTranslate client.

These tests were generated with an LLM and then reviewed by hand. Treat a
failure as a real signal, but read the assertion before trusting it: a test
here can encode an assumption the code never promised. Fix or delete such a
test rather than bending the code to satisfy it.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings

from . import services


@override_settings(LIBRETRANSLATE_URL='http://libretranslate:5000')
class FetchTranslationTests(TestCase):
    @patch('translate.services._provider_request')
    def test_translate_success(self, mock_request):
        mock_request.return_value = {
            'translatedText': 'Bonjour',
            'detectedLanguage': {'language': 'en', 'confidence': 0.9},
        }

        result = services.fetch_translation('Hello', 'fr', 'auto')

        self.assertEqual(result['translated_text'], 'Bonjour')
        self.assertEqual(result['detected_lang'], 'en')
        mock_request.assert_called_once()

    @patch('translate.services._provider_request')
    def test_translate_caches_result(self, mock_request):
        mock_request.return_value = {'translatedText': 'Bonjour', 'detectedLanguage': {'language': 'en'}}

        services.fetch_translation('Hello', 'fr', 'auto')
        services.fetch_translation('Hello', 'fr', 'auto')

        mock_request.assert_called_once()

    @patch('translate.services._provider_request', return_value=None)
    def test_translate_error_returns_none(self, _mock):
        self.assertIsNone(services.fetch_translation('Hello', 'fr', 'auto'))

    @override_settings(LIBRETRANSLATE_URL='')
    def test_translate_unconfigured_returns_none(self):
        self.assertIsNone(services.fetch_translation('Hello', 'fr', 'auto'))

    def test_translate_empty_text_returns_none(self):
        self.assertIsNone(services.fetch_translation('', 'fr', 'auto'))


class FetchLanguagesTests(TestCase):
    @override_settings(LIBRETRANSLATE_URL='')
    def test_unconfigured_returns_fallback(self):
        self.assertEqual(services.fetch_languages(), services.FALLBACK_LANGUAGES)

    @override_settings(LIBRETRANSLATE_URL='http://libretranslate:5000')
    @patch('translate.services._provider_request')
    def test_fetches_and_caches_languages(self, mock_request):
        mock_request.return_value = [
            {'code': 'en', 'name': 'English', 'targets': ['fr']},
            {'code': 'fr', 'name': 'French', 'targets': ['en']},
        ]

        result = services.fetch_languages()
        self.assertEqual(result, [('en', 'English'), ('fr', 'French')])

        services.fetch_languages()
        mock_request.assert_called_once()

    @override_settings(LIBRETRANSLATE_URL='http://libretranslate:5000')
    @patch('translate.services._provider_request', return_value=None)
    def test_error_returns_fallback(self, _mock):
        self.assertEqual(services.fetch_languages(), services.FALLBACK_LANGUAGES)
