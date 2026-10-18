"""Local-only synthetic original artifact API guards and native reopen checks."""
import http.client
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from analysis.explanation_service import ExplanationService, make_server
from web.viewer_import import normalize_source
from tests.test_viewer_import import FIXTURE


class OriginalArtifactsHTTPTests(unittest.TestCase):
    def test_guards_native_startup_reopen_and_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            state = root / 'state'
            native = root / 'native'
            shutil.copytree(FIXTURE, native)
            (native / 'files/workspace').mkdir(parents=True)
            (native / 'files/workspace/report.txt').write_text('synthetic original report')
            _package, path = normalize_source(native, state)
            service = ExplanationService(path, state, provider=Mock(side_effect=AssertionError('no model calls')))
            listing = service.artifacts.listing()
            self.assertTrue(listing['artifacts'])
            self.assertTrue(all(row['available'] for row in listing['artifacts']))
            key = listing['artifacts'][0]['artifact_id']
            package = service.package
            # Reopening the same persisted import recovers only the trusted binding.
            service.register(package)
            self.assertTrue(service.artifacts.listing()['artifacts'][0]['available'])
            # A different portable recording clears the original byte allowlist.
            from tests.test_explanation_workflow import synthetic_bundle
            from web.recording import write_recording
            from web.render import build_payload
            from analysis.explanation_workflow import load_portable
            other = root / 'other.json'
            write_recording(build_payload(synthetic_bundle(), {}), other)
            service.register(load_portable(other))
            self.assertEqual(service.artifacts.listing()['artifacts'], [])
            with self.assertRaises(ValueError):
                service.artifacts.read(key)
            service.register(package)
            self.assertTrue(service.artifacts.listing()['artifacts'][0]['available'])
            # Sanitized derivatives cannot recover originals from the same state.
            from web.sharing import prepare_share
            shared = prepare_share(package, {'custom_terms': []})['package']
            service.register(shared)
            self.assertEqual(service.artifacts.listing()['artifacts'], [])
            with self.assertRaises(ValueError):
                service.artifacts.read(key)
            service.register(package)
            self.assertTrue(service.artifacts.listing()['artifacts'][0]['available'])
            server = make_server(service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            origin = 'http://127.0.0.1:' + str(server.server_port)
            def request(endpoint, recording_id=None, token=None, actual_origin=None):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                try:
                    connection.request('POST', endpoint, json.dumps({'recording_id': recording_id or service.recording_id,
                        'artifact_id': key}), headers={'Origin': actual_origin or origin, 'Content-Type': 'application/json',
                        'X-TraceForge-Token': token or service.token})
                    response = connection.getresponse()
                    return response.status, dict(response.getheaders()), response.read()
                finally:
                    connection.close()
            try:
                for endpoint in ('/api/artifacts', '/api/artifact-preview', '/api/artifact-download'):
                    self.assertEqual(request(endpoint, token='wrong')[0], 403)
                    self.assertEqual(request(endpoint, actual_origin='https://invalid.example')[0], 403)
                    self.assertEqual(request(endpoint, recording_id='stale')[0], 400)
                status, headers, raw = request('/api/artifact-download')
                self.assertEqual(status, 200)
                self.assertEqual(headers['Content-Type'], 'application/octet-stream')
                self.assertEqual(headers['X-TraceForge-Recording-Id'], service.recording_id)
                self.assertIn('attachment;', headers['Content-Disposition'])
                self.assertTrue(raw)
                service.provider.assert_not_called()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
