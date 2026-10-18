"""New synthetic native viewer import checks; recorded commands stay inert."""
import base64
import copy
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

from adapters.registry import source_identity
from analysis.explanation_profiles import profile_for
from analysis.explanation_service import ExplanationService
from web.viewer_import import detect_source, normalize_source, import_upload
from tests.test_codex_adapter import records as codex_records
from tests.test_otlp_adapter import fixture as otlp_fixture
from tests.test_explanation_workflow import synthetic_bundle
from web.recording import write_recording
from web.render import build_payload

FIXTURE = Path(__file__).parent / 'fixtures/artex/token_session'


def archive_bytes(version=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        for path in sorted(FIXTURE.rglob('*')):
            if not path.is_file():
                continue
            raw = path.read_bytes()
            if version is not None and path.name == 'manifest.json':
                snapshot = json.loads(raw)
                snapshot['format_version'] = version
                raw = json.dumps(snapshot).encode()
            info = tarfile.TarInfo(path.relative_to(FIXTURE).as_posix())
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    return output.getvalue()


class SourceIdentityTests(unittest.TestCase):
    def test_explicit_adapter_precedes_display_name(self):
        for adapter, name, expected in [('unknown', 'Artex', 'generic'), ('codex', 'Artex', 'codex'),
                                        ('artex', 'Other', 'artex'), ('otlp', 'Other', 'otlp')]:
            bundle = {'manifest': {'source': {'adapter': adapter}, 'agent': {'name': name}}}
            self.assertEqual(source_identity(bundle)['adapter_id'], expected)
            self.assertEqual(profile_for(bundle).adapter_id, 'artex' if expected == 'artex' else 'generic')
        self.assertEqual(source_identity({'manifest': {'agent': {'name': 'Artex'}}})['adapter_id'], 'artex')
        self.assertEqual(source_identity({'manifest': {'agent': {'name': 'Codex'}}})['support_tier'], 'experimental')
        self.assertEqual(source_identity({'manifest': {'agent': {'name': 'OpenTelemetry'}}})['adapter_id'], 'otlp')
        self.assertEqual(source_identity({'manifest': {'source': {'adapter': 'otlp'}}})['support_tier'], 'experimental')
        self.assertEqual(source_identity({'manifest': {'agent': {'name': 'LiteLLM'}}})['native_format'], 'unknown')


class ViewerImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source.traceforge.json'
        write_recording(build_payload(synthetic_bundle(), {}), self.source)
        self.provider = Mock(side_effect=AssertionError('provider must not run on import'))
        self.service = ExplanationService(self.source, self.root / 'state', provider=self.provider)

    def tearDown(self):
        self.temp.cleanup()

    def value(self, raw=None):
        return {'filename': 'synthetic.tar', 'archive_base64': base64.b64encode(archive_bytes() if raw is None else raw).decode(), 'recording_id': self.service.recording_id}

    def test_native_import_preserves_artifacts_and_does_not_call_provider(self):
        original = self.source.read_bytes()
        previous = self.service.recording_id
        self.service.import_artex(self.value())
        self.assertNotEqual(self.service.recording_id, previous)
        self.assertEqual(self.service.snapshot()['source']['adapter_id'], 'artex')
        self.assertTrue(list((self.root / 'state/imports').glob('*/bundle/artifacts/artex/manifest.json')))
        self.assertEqual(original, self.source.read_bytes())
        self.provider.assert_not_called()

    def test_corrupt_unsupported_stale_running_preserve_active_source(self):
        before = copy.deepcopy(self.service.package)
        for value in [self.value(b'not a tar'), self.value(archive_bytes(999)), dict(self.value(), recording_id='stale')]:
            with self.assertRaises(ValueError):
                self.service.import_artex(value)
            self.assertEqual(self.service.package, before)
        self.service.status['state'] = 'running'
        with self.assertRaises(ValueError):
            self.service.import_artex(self.value())
        self.assertEqual(self.service.package, before)
        self.provider.assert_not_called()

    def test_upload_limit_before_decode_and_unsupported_name(self):
        with patch('web.viewer_import.MAX_UPLOAD_BYTES', 3), patch('web.viewer_import.base64.b64decode', side_effect=AssertionError('must check encoded length first')):
            with self.assertRaises(ValueError):
                import_upload(self.value(b'1234567'), self.root / 'state')
        with self.assertRaises(ValueError):
            import_upload(dict(self.value(), filename='../unsafe.tar'), self.root / 'state')

    def test_cli_normalization_portable_extracted_and_unique_native_outputs(self):
        package, source = normalize_source(self.source, self.root / 'state')
        self.assertEqual(source, self.source)
        first, one = normalize_source(FIXTURE, self.root / 'state')
        second, two = normalize_source(FIXTURE, self.root / 'state')
        self.assertNotEqual(one, two)
        self.assertEqual(first, second)
        self.assertEqual(package, self.service.package)

    def test_codex_rollout_is_detected_and_normalized_directly(self):
        source = self.root / 'codex-session.jsonl'
        source.write_text(''.join(json.dumps(row) + '\n' for row in codex_records()))
        self.assertEqual(detect_source(source), 'codex-rollout')
        package, recording = normalize_source(source, self.root / 'state')
        self.assertTrue(recording.is_file())
        manifest = package['payload']['bundle']['manifest']
        self.assertEqual(manifest['source']['adapter'], 'codex')
        self.assertEqual(manifest['source']['capture_surface'], 'native-rollout-transcript')
        self.assertEqual(manifest['completeness']['status'], 'partial')
        self.assertEqual(len(package['payload']['bundle']['events']), 6)
        self.provider.assert_not_called()

    def test_codex_exec_json_is_identified_as_a_distinct_unsupported_surface(self):
        source = self.root / 'codex-exec.jsonl'
        source.write_text(json.dumps({'type': 'thread.started', 'thread_id': 'synthetic'}) + '\n')
        self.assertEqual(detect_source(source), 'codex-exec-events')
        with self.assertRaisesRegex(ValueError, 'exec --json.*distinct'):
            normalize_source(source, self.root / 'state')

    def test_dispatch_detection_rejects_symlinked_jsonl_and_symlinked_parent(self):
        source = self.root / 'real.jsonl'
        source.write_text(''.join(json.dumps(row) + '\n' for row in codex_records()))
        alias = self.root / 'alias.jsonl'
        alias.symlink_to(source)
        with self.assertRaisesRegex(ValueError, 'does not follow symlinks'):
            detect_source(alias)
        parent_alias = self.root / 'parent-alias'
        parent_alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            detect_source(parent_alias / source.name)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'named pipes unavailable')
    def test_dispatch_detection_rejects_jsonl_fifo_without_blocking(self):
        source = self.root / 'stream.jsonl'
        os.mkfifo(source)
        with self.assertRaisesRegex(ValueError, 'bounded regular file'):
            detect_source(source)

    def test_codex_jsonl_upload_uses_same_native_pipeline(self):
        raw = ''.join(json.dumps(row) + '\n' for row in codex_records()).encode()
        value = {'filename': 'selected.jsonl', 'source_base64': base64.b64encode(raw).decode()}
        package, _recording = import_upload(value, self.root / 'state')
        self.assertEqual(package['payload']['bundle']['manifest']['source']['adapter'], 'codex')

    def test_otlp_json_detects_normalizes_and_uploads_through_shared_pipeline(self):
        source = self.root / 'telemetry.json'
        raw = (json.dumps(otlp_fixture()) + '\n').encode()
        source.write_bytes(raw)
        self.assertEqual(detect_source(source), 'otlp')
        package, recording = normalize_source(source, self.root / 'state')
        self.assertTrue(recording.is_file())
        manifest = package['payload']['bundle']['manifest']
        self.assertEqual(manifest['source']['adapter'], 'otlp')
        self.assertEqual(manifest['source']['format'], 'otlp-protobuf-json')
        self.assertEqual(manifest['completeness']['status'], 'partial')
        uploaded, _recording = import_upload({
            'filename': 'telemetry.json', 'source_base64': base64.b64encode(raw).decode()}, self.root / 'state')
        self.assertEqual(uploaded['payload']['bundle']['manifest']['source']['adapter'], 'otlp')
        self.provider.assert_not_called()

    def test_unknown_json_is_not_guessed_as_artex_but_snapshot_remains_detectable(self):
        unknown = self.root / 'unknown.json'
        unknown.write_text(json.dumps({'events': [], 'vendor': 'synthetic'}))
        self.assertEqual(detect_source(unknown), 'unknown-json')
        with self.assertRaisesRegex(ValueError, 'unrecognized recording'):
            normalize_source(unknown, self.root / 'state')
        snapshot = self.root / 'snapshot.json'
        snapshot.write_bytes((FIXTURE / 'manifest.json').read_bytes())
        self.assertEqual(detect_source(snapshot), 'artex')
