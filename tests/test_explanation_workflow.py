"""New synthetic offline exchange integration tests; attached code stays inert."""
import copy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from analysis.explanation_workflow import export_explanation_request, import_explanation_response
from analysis.explanations import recording_fingerprint
from web.recording import write_recording
from web.render import build_payload


def synthetic_bundle():
    return {'manifest': {'schema_version': '0.1', 'run_id': 'synthetic', 'agent': {'name': 'Artex', 'version': '1'},
                         'started_at': '2026-10-10T10:00:00Z', 'completeness': {'status': 'complete', 'gaps': []},
                         'artifact_refs': [{'path': 'unavailable/source.txt', 'sha256': 'a' * 64}]},
            'events': [{'schema_version': '0.1', 'id': 'e', 'run_id': 'synthetic', 'sequence': 0,
                        'kind': 'observation', 'timestamp': '2026-10-10T10:00:00Z',
                        'data': {'source_record': {'summary': 'Inspect the synthetic recording'},
                                 'raw': '</script><script>throw Error("inert")</script>', 'precise': 9007199254740993},
                        'artifact_refs': [{'path': 'unavailable/event.txt', 'sha256': 'b' * 64}]}]}


class ExplanationWorkflowTests(unittest.TestCase):
    def make_source(self, root):
        payload = build_payload(synthetic_bundle(), {})
        payload['custom_preserved'] = {'evidence': 'original'}
        payload['flow']['messages'][0].pop('description', None)  # stale display gets refreshed
        source = root / 'source.json'
        attachment = {'name': 'inert.py', 'language': 'python', 'text': 'raise RuntimeError("never execute")\n'}
        write_recording(payload, source, attachments=[attachment])
        response = root / 'response.json'
        response.write_text(json.dumps({'version': 1, 'run_id': 'synthetic', 'bundle_sha256': recording_fingerprint(payload['bundle']),
            'provenance': {'method': 'offline-development-worker', 'model': 'offline', 'provider': 'offline'},
            'annotations': [{'message_id': 'message:e', 'text': 'Reviews the recording.', 'basis': 'recorded_purpose', 'event_ids': ['e']}]}))
        return source, response, payload, attachment

    def test_export_import_preserves_evidence_attachment_and_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, response, original, attachment = self.make_source(root)
            before = source.read_bytes()
            request_path = root / 'request.json'
            export_explanation_request(source, request_path, message_ids=['message:e'], model='offline')
            request = json.loads(request_path.read_text())
            self.assertEqual(request['targets'][0]['description']['text'], 'Inspect the synthetic recording')
            html, output = root / 'view.html', root / 'enhanced.json'
            result = import_explanation_response(source, response, html, recording_output=output)
            self.assertEqual(result['execution'], 'not requested')
            package = json.loads(output.read_text())
            self.assertEqual(package['attachments'], [attachment])
            for key in original:
                if key != 'flow':
                    self.assertEqual(package['payload'][key], original[key])
            self.assertEqual(package['payload']['explanations']['annotations'][0]['text'], 'Reviews the recording.')
            self.assertNotIn('explanation', package['payload']['flow']['messages'][0])
            self.assertEqual(source.read_bytes(), before)
            self.assertIn('9007199254740993', package['payload']['evidence_text']['e']['data'])
            self.assertNotIn('</script><script>throw Error', html.read_text())

    def test_stale_annotations_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, response, _, _ = self.make_source(root)
            doc = json.loads(response.read_text()); doc['bundle_sha256'] = 'stale'
            response.write_text(json.dumps(doc))
            with self.assertRaisesRegex(ValueError, 'fingerprint'):
                import_explanation_response(source, response, root / 'out.html', recording_output=root / 'out.json')
            self.assertFalse((root / 'out.json').exists())
            self.assertFalse((root / 'out.html').exists())

    def test_collisions_and_existing_destinations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, response, _, _ = self.make_source(root)
            before = source.read_bytes()
            for html, output in [(source, root / 'new.json'), (root / 'same', root / 'same'), (root / 'new.html', response)]:
                with self.assertRaises((ValueError, FileExistsError)):
                    import_explanation_response(source, response, html, recording_output=output)
            with self.assertRaises(ValueError):
                export_explanation_request(source, source, message_ids=['message:e'], model='offline')
            existing = root / 'existing.html'; existing.write_text('keep')
            with self.assertRaises(FileExistsError):
                import_explanation_response(source, response, existing, recording_output=root / 'new.json')
            self.assertFalse((root / 'new.json').exists())
            self.assertEqual(existing.read_text(), 'keep')
            self.assertEqual(source.read_bytes(), before)

    def test_cli_request_import_and_stale_error(self):
        from traceforge import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, response, _, _ = self.make_source(root)
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = main(['explanation-request', str(source), str(root / 'request.json'), '--message-id', 'message:e', '--model', 'offline'])
                self.assertEqual(code, 0, output.getvalue())
                code = main(['explanation-import', str(source), str(response), str(root / 'view.html'), '--recording-output', str(root / 'out.json')])
                self.assertEqual(code, 0, output.getvalue())
                code = main(['explanation-request', str(source), str(source), '--message-id', 'message:e', '--model', 'offline'])
                self.assertEqual(code, 2, output.getvalue())

    def test_core_structure_invalid_but_artifacts_never_opened(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, _, _ = self.make_source(root)
            package = json.loads(source.read_text())
            package['payload']['bundle']['events'][0]['sequence'] = -1
            source.write_text(json.dumps(package))
            with self.assertRaises(ValueError):
                export_explanation_request(source, root / 'out.json', message_ids=['message:e'], model='offline')


if __name__ == '__main__':
    unittest.main()
