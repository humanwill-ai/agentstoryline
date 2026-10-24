"""Synthetic optional telemetry service checks; no external calls."""
import base64
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from analysis.explanation_service import ExplanationService
from analysis.explanation_workflow import load_portable, refresh_flow
from tests.test_explanation_workflow import synthetic_bundle
from tests.test_orcareplay_adapter import fixture_files
from web.recording import write_recording
from web.render import build_payload


class TelemetryServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.json'
        write_recording(build_payload(synthetic_bundle(), {}), self.source)
        self.original = self.source.read_bytes()
        self.service = ExplanationService(self.source, self.root / 'state')
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            for name, raw in fixture_files().items():
                archive.writestr(name, raw)
        self.upload = {'recording_id': self.service.recording_id, 'files': [
            {'filename': 'synthetic.orca', 'source_base64': base64.b64encode(stream.getvalue()).decode()}]}

    def test_prepare_apply_roundtrip_and_originals(self):
        original = copy.deepcopy(self.service.package)
        preview = self.service.prepare_telemetry(self.upload)
        self.assertEqual(self.service.package, original)
        self.assertFalse(preview['replacing'])
        self.service.apply_telemetry(dict(preview))
        self.assertEqual(self.service.bundle, original['payload']['bundle'])
        self.assertIn('telemetry', self.service.package['payload'])
        stored = self.root / 'state' / self.service.recording_id / 'recording.json'
        reopened = load_portable(stored)
        self.assertEqual(reopened['payload']['telemetry'], self.service.package['payload']['telemetry'])
        self.assertEqual(refresh_flow(reopened['payload'])['telemetry'], reopened['payload']['telemetry'])
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertTrue(any(row['available'] for row in self.service.artifact_listing()['artifacts']))
        replacement = self.service.prepare_telemetry(self.upload)
        self.assertTrue(replacement['replacing'])

    def test_stale_cancel_and_invalid_do_not_mutate(self):
        original = copy.deepcopy(self.service.package)
        preview = self.service.prepare_telemetry(self.upload)
        with self.assertRaises(ValueError):
            self.service.apply_telemetry(dict(preview, recording_id='stale'))
        with self.assertRaises(ValueError):
            self.service.apply_telemetry(dict(preview, preview_id='wrong'))
        self.service.cancel_telemetry(preview)
        with self.assertRaises(ValueError):
            self.service.apply_telemetry(preview)
        invalid = copy.deepcopy(self.upload)
        invalid['files'][0]['source_base64'] = 'invalid!'
        with self.assertRaises(ValueError):
            self.service.prepare_telemetry(invalid)
        with patch('analysis.explanation_service.MAX_BYTES', 1):
            with self.assertRaises(ValueError):
                self.service.prepare_telemetry(self.upload)
        self.assertEqual(original, self.service.package)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_unsupported_primary_and_extension_validation(self):
        self.service.bundle['manifest']['source'] = {'adapter': 'codex'}
        with self.assertRaises(ValueError):
            self.service.prepare_telemetry(self.upload)
        self.service.bundle['manifest'].pop('source')
        preview = self.service.prepare_telemetry(self.upload)
        self.service.apply_telemetry(preview)
        package = copy.deepcopy(self.service.package)
        package['payload']['telemetry']['primary_fingerprint'] = 'wrong'
        bad = self.root / 'bad.json'
        bad.write_text(json.dumps(package))
        with self.assertRaises(ValueError):
            load_portable(bad)

    def test_mutated_base_and_superseded_previews_rejected(self):
        first = self.service.prepare_telemetry(self.upload)
        second = self.service.prepare_telemetry(self.upload)
        with self.assertRaises(ValueError):
            self.service.apply_telemetry(first)
        self.service.attachments([{'name': 'inert.py', 'language': 'python', 'text': '# synthetic'}])
        with self.assertRaises(ValueError):
            self.service.apply_telemetry(second)
        self.assertNotIn('telemetry', self.service.package['payload'])
        self.assertEqual(self.service.package['attachments'][0]['name'], 'inert.py')

    def test_expired_and_forged_map_rejected(self):
        preview = self.service.prepare_telemetry(self.upload)
        self.service.telemetry_preview['expires'] = 0
        with self.assertRaises(ValueError):
            self.service.apply_telemetry(preview)
        preview = self.service.prepare_telemetry(self.upload)
        self.service.apply_telemetry(preview)
        package = copy.deepcopy(self.service.package)
        package['payload']['telemetry']['matches'] = [{'orca_request_id': 'invented'}]
        path = self.root / 'forged.json'
        path.write_text(json.dumps(package))
        with self.assertRaises(ValueError):
            load_portable(path)
        reopened = ExplanationService(self.root / 'state' / self.service.recording_id / 'recording.json', self.root / 'state')
        self.assertEqual(reopened.package['payload']['telemetry'], self.service.package['payload']['telemetry'])
        self.assertTrue(any(row['available'] for row in reopened.artifact_listing()['artifacts']))

    def test_portable_rejects_invalid_supplemental_core_and_wrong_source(self):
        preview = self.service.prepare_telemetry(self.upload)
        self.service.apply_telemetry(preview)
        for change in ('source', 'core', 'event-bound'):
            package = copy.deepcopy(self.service.package)
            bundle = package['payload']['telemetry']['bundle']
            if change == 'source':
                bundle['manifest']['source']['adapter'] = 'codex'
            elif change == 'core':
                bundle['events'][0].pop('id')
            else:
                bundle['events'] *= 501
            path = self.root / (change + '.json')
            path.write_text(json.dumps(package))
            with self.assertRaises(ValueError):
                load_portable(path)
