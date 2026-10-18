"""Synthetic sharing service tests. No model or recorded-command execution."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from analysis.explanation_service import ExplanationService, make_server
from analysis.explanation_workflow import load_portable
from tests.test_explanation_workflow import synthetic_bundle
from web.render import build_payload
from web.recording import write_recording


class SharingServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source.json'
        bundle = synthetic_bundle()
        bundle['events'][0].setdefault('data', {})['secret'] = 'synthetic-secret-value'
        write_recording(build_payload(bundle, {}), self.source,
            attachments=[{'name': 'inert.py', 'language': 'python', 'text': 'raise RuntimeError("never")'}])
        self.provider = Mock(side_effect=AssertionError('no provider on sharing'))
        self.service = ExplanationService(self.source, self.root / 'state', provider=self.provider)
        self.original = copy.deepcopy(self.service.package)
        self.source_bytes = self.source.read_bytes()

    def tearDown(self):
        self.temp.cleanup()

    def preview(self):
        return self.service.preview_share({'recording_id': self.service.recording_id, 'custom_terms': []})

    def export(self, preview):
        return self.service.export_share({'recording_id': preview['recording_id'], 'preview_id': preview['preview_id']})

    def test_frozen_exact_bytes_roundtrip_and_source_preserved(self):
        preview = self.preview()
        raw = self.export(preview)
        self.assertFalse(preview['truncated'])
        self.assertEqual(raw, preview['preview_text'].encode('utf-8'))
        self.assertNotIn(b'synthetic-secret-value', raw)
        self.assertEqual(json.loads(raw)['attachments'], [])
        target = self.root / 'shared.json'
        target.write_bytes(raw)
        self.assertEqual(load_portable(target)['format'], 'traceforge-recording')
        self.service.package['payload']['notification_sound'] = 'changed after preview'
        self.assertEqual(self.export(preview), raw)
        self.service.package = copy.deepcopy(self.original)
        self.assertEqual(self.service.package, self.original)
        self.assertEqual(self.source.read_bytes(), self.source_bytes)
        self.provider.assert_not_called()

    def test_stale_wrong_expired_and_activation_clear(self):
        preview = self.preview()
        with self.assertRaises(ValueError):
            self.service.export_share({'recording_id': 'stale', 'preview_id': preview['preview_id']})
        with self.assertRaises(ValueError):
            self.service.export_share({'recording_id': self.service.recording_id, 'preview_id': 'wrong'})
        with patch('analysis.explanation_service.time.monotonic', return_value=self.service.share_preview['expires']):
            with self.assertRaises(ValueError):
                self.export(preview)
        self.assertIsNone(self.service.share_preview)
        preview = self.preview()
        self.service.register(self.original)
        self.assertIsNone(self.service.share_preview)
        with self.assertRaises(ValueError):
            self.export(preview)

    def test_single_preview_running_guard_and_validation_failure(self):
        first = self.preview()
        second = self.preview()
        self.assertNotEqual(first['preview_id'], second['preview_id'])
        with self.assertRaises(ValueError):
            self.export(first)
        self.service.status['state'] = 'running'
        with self.assertRaises(ValueError):
            self.preview()
        self.service.status['state'] = 'idle'
        with self.assertRaises(ValueError):
            self.service.preview_share({'recording_id': self.service.recording_id, 'custom_terms': ['']})
        self.assertIsNone(self.service.share_preview)
        self.assertEqual(self.service.package, self.original)

    def test_preview_cap_and_export_bound(self):
        altered = copy.deepcopy(self.original)
        altered['payload']['test_large'] = 'x' * 200100
        with patch('analysis.explanation_service.prepare_share', return_value={'package': altered, 'report': {}}):
            preview = self.preview()
        self.assertTrue(preview['truncated'])
        self.assertEqual(len(preview['preview_text']), 200000)
        self.assertGreater(len(self.export(preview)), 200000)
        with patch('analysis.explanation_service.MAX_BYTES', 10):
            with self.assertRaises(ValueError):
                self.preview()
        self.assertIsNone(self.service.share_preview)


class SharingHttpTests(unittest.TestCase):
    setUp = SharingServiceTests.setUp
    tearDown = SharingServiceTests.tearDown
    export = SharingServiceTests.export
    def test_http_origin_token_stale_and_size_guards(self):
        import threading
        import urllib.request
        import urllib.error
        server = make_server(self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = 'http://127.0.0.1:' + str(server.server_port)
        def post(path, value, extra=None):
            headers = {'Content-Type': 'application/json', 'Origin': url, 'X-TraceForge-Token': self.service.token}
            headers.update(extra or {})
            request = urllib.request.Request(url + path, json.dumps(value).encode(), headers)
            return urllib.request.urlopen(request, timeout=3)
        try:
            value = {'recording_id': self.service.recording_id, 'custom_terms': []}
            with post('/api/share-preview', value) as response:
                preview = json.load(response)
            with post('/api/share-export', {'preview_id': preview['preview_id'], 'recording_id': preview['recording_id']}) as response:
                self.assertEqual(response.read(), self.export(preview))
            for path, body, headers, status in [
                ('/api/share-preview', value, {'X-TraceForge-Token': 'wrong'}, 403),
                ('/api/share-preview', value, {'Origin': 'https://evil.invalid'}, 403),
                ('/api/share-preview', dict(value, recording_id='stale'), {}, 400),
                ('/api/share-preview', dict(value, padding='x' * (128 * 1024)), {}, 400),
                ('/api/share-export', dict(preview, padding='x' * 4096), {}, 400)]:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    post(path, body, headers)
                self.assertEqual(error.exception.code, status)
            self.assertEqual(self.service.package, self.original)
            self.provider.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
