import unittest
from unittest.mock import patch
from analysis.explanation_provider import generate, validate_endpoint, ProviderError


class ProviderTests(unittest.TestCase):
    def test_endpoint_policy(self):
        self.assertEqual(validate_endpoint('https://gateway.example/v1'), 'https://gateway.example/v1/chat/completions')
        self.assertTrue(validate_endpoint('http://127.0.0.1:9999/v1'))
        for endpoint in ['http://example.com/v1', 'https://key@example.com/v1', 'https://example.com/?key=secret', 'file:///tmp/a']:
            with self.assertRaises(ValueError):
                validate_endpoint(endpoint)

    def test_errors_redacted(self):
        with patch('urllib.request.OpenerDirector.open', side_effect=RuntimeError('secret-key')):
            with self.assertRaises(ProviderError) as error:
                generate('https://gateway.example/v1', 'model', 'secret-key', {'instructions': 'Return JSON'})
        self.assertNotIn('secret-key', str(error.exception))

    def test_redirects_disabled(self):
        from analysis.explanation_provider import _NoRedirect
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example'))

    def test_http_status_and_timeout_are_actionable_and_redacted(self):
        import urllib.error
        import io
        cases = [(urllib.error.HTTPError('https://secret.example/key', 401, 'secret-message', {}, io.BytesIO(b'secret-body')), 'HTTP 401'),
                 (TimeoutError('secret-timeout'), 'timed out'),
                 (urllib.error.URLError(TimeoutError('secret-timeout')), 'timed out')]
        for failure, expected in cases:
            with self.subTest(expected=expected):
                with patch('urllib.request.OpenerDirector.open', side_effect=failure):
                    with self.assertRaises(ProviderError) as error:
                        generate('https://gateway.example/v1', 'model', 'secret-key', {'instructions': 'Return JSON'})
                self.assertIn(expected, str(error.exception))
                self.assertNotIn('secret', str(error.exception))
