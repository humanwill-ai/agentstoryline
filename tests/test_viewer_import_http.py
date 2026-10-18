"""New synthetic HTTP native import tests; providers and recorded commands remain inert."""
import base64
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from analysis.explanation_service import ExplanationService, make_server
from analysis.explanations import recording_fingerprint
from tests.test_explanation_workflow import synthetic_bundle
from tests.test_viewer_import import archive_bytes
from web.recording import write_recording
from web.render import build_payload
from web.viewer_import import MAX_UPLOAD_BODY
from tests.test_codex_adapter import records as codex_records
from tests.test_otlp_adapter import fixture as otlp_fixture


class ViewerImportHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source.traceforge.json'
        payload = build_payload(synthetic_bundle(), {})
        payload['notification_sound'] = 'data:audio/wav;base64,UklGRg=='
        payload['explanations'] = {'version': 1, 'run_id': 'synthetic',
            'bundle_sha256': recording_fingerprint(payload['bundle']),
            'provenance': {'method': 'offline-development-worker', 'model': 'synthetic', 'provider': 'synthetic'},
            'annotations': [{'message_id': 'message:e', 'text': 'Reviews synthetic evidence.',
                             'basis': 'recorded_purpose', 'event_ids': ['e']}]}
        write_recording(payload, self.source)
        self.original_source = self.source.read_bytes()
        self.provider = Mock(side_effect=AssertionError('no provider during native import'))
        self.service = ExplanationService(self.source, self.root / 'state', provider=self.provider)
        self.server = make_server(self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, value=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        actual = {'Content-Type': 'application/json', 'Origin': self.origin,
                  'X-TraceForge-Token': self.service.token}
        actual.update(headers or {})
        try:
            connection.request('POST' if value is not None else 'GET', path,
                body=None if value is None else json.dumps(value).encode(), headers=actual)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def upload(self, raw=None, recording_id=None):
        return {'recording_id': self.service.recording_id if recording_id is None else recording_id,
                'filename': 'synthetic.tar',
                'archive_base64': base64.b64encode(archive_bytes() if raw is None else raw).decode()}

    def test_success_rebinds_and_portable_audio_explanations_remain_preserved(self):
        # Portable activation retains audio and annotation content; legacy documents
        # are upgraded into the existing versioned annotation store, not discarded.
        self.assertEqual(self.service.package['payload']['notification_sound'], 'data:audio/wav;base64,UklGRg==')
        self.assertEqual(self.service.package['payload']['explanations']['annotations'][0]['text'], 'Reviews synthetic evidence.')
        portable = copy.deepcopy(self.service.package)
        status, _ = self.request('/api/recording', {'package': portable})
        self.assertEqual(status, 200)
        self.assertEqual(self.service.package['payload']['notification_sound'], portable['payload']['notification_sound'])
        self.assertEqual(self.service.package['payload']['explanations'], portable['payload']['explanations'])
        previous = self.service.recording_id
        status, response = self.request('/api/import-artex', self.upload())
        self.assertEqual(status, 200)
        self.assertNotEqual(response['recording_id'], previous)
        self.assertEqual(response['recording_id'], self.service.recording_id)
        self.assertEqual(response['source']['adapter_id'], 'artex')
        self.assertEqual(self.request('/api/session')[1]['recording_id'], response['recording_id'])
        self.assertEqual(self.request('/api/source')[1]['native_version'], 3)
        self.assertEqual(self.source.read_bytes(), self.original_source)
        self.provider.assert_not_called()

    def test_codex_native_rollout_imports_through_generic_endpoint(self):
        raw = ''.join(json.dumps(row) + '\n' for row in codex_records()).encode()
        value = {'recording_id': self.service.recording_id, 'filename': 'session.jsonl',
                 'source_base64': base64.b64encode(raw).decode()}
        status, response = self.request('/api/import-native', value)
        self.assertEqual(status, 200)
        self.assertEqual(response['source']['adapter_id'], 'codex')
        self.assertEqual(response['source']['native_format'], 'native-rollout-jsonl')
        self.assertEqual(response['source']['native_version'], 'synthetic-version')
        self.assertEqual(response['source']['capture_surface'], 'native-rollout-transcript')
        self.assertEqual(response['source']['coverage_status'], 'partial')
        package = self.request('/api/recording')[1]
        self.assertEqual(package['payload']['bundle']['manifest']['source']['adapter'], 'codex')
        self.provider.assert_not_called()

    def test_otlp_json_imports_through_generic_endpoint(self):
        raw = (json.dumps(otlp_fixture()) + '\n').encode()
        value = {'recording_id': self.service.recording_id, 'filename': 'telemetry.json',
                 'source_base64': base64.b64encode(raw).decode()}
        status, response = self.request('/api/import-native', value)
        self.assertEqual(status, 200)
        self.assertEqual(response['source']['adapter_id'], 'otlp')
        self.assertEqual(response['source']['support_tier'], 'experimental')
        self.assertEqual(response['source']['native_format'], 'otlp-protobuf-json')
        self.assertEqual(response['source']['capture_surface'], 'offline-export-request-json')
        self.assertEqual(response['source']['coverage_status'], 'partial')
        self.assertEqual(self.request('/api/recording')[1]['payload']['bundle']['manifest']['source']['adapter'], 'otlp')
        self.provider.assert_not_called()

    def test_failed_stale_running_and_origin_token_rejections_preserve_source(self):
        original = copy.deepcopy(self.service.package)
        original_id = self.service.recording_id
        cases = [self.upload(b'corrupt archive'), self.upload(archive_bytes(999)), self.upload(recording_id='stale')]
        for value in cases:
            status, response = self.request('/api/import-artex', value)
            self.assertEqual(status, 400)
            self.assertNotIn('archive_base64', response['error'])
            self.assertEqual(self.service.package, original)
            self.assertEqual(self.service.recording_id, original_id)
        self.service.status['state'] = 'running'
        self.assertEqual(self.request('/api/import-artex', self.upload())[0], 400)
        self.service.status['state'] = 'idle'
        for headers in [{'Origin': 'https://evil.invalid'}, {'X-TraceForge-Token': 'wrong'}]:
            self.assertEqual(self.request('/api/import-artex', self.upload(), headers)[0], 403)
        self.assertEqual(self.service.package, original)
        self.assertEqual(self.source.read_bytes(), self.original_source)
        self.provider.assert_not_called()

    def test_body_limit_rejected_before_body_read_or_import(self):
        # Declared oversize length with no payload must receive a response at once.
        # Waiting to read this body would time out and fail the test.
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            with patch('analysis.explanation_service.import_upload', side_effect=AssertionError('oversize body must not reach importer')):
                connection.request('POST', '/api/import-artex', body=b'', headers={
                    'Origin': self.origin, 'X-TraceForge-Token': self.service.token,
                    'Content-Type': 'application/json', 'Content-Length': str(MAX_UPLOAD_BODY + 1)})
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                self.assertIn('error', json.loads(response.read()))
        finally:
            connection.close()
        self.assertEqual(self.source.read_bytes(), self.original_source)
        self.provider.assert_not_called()
