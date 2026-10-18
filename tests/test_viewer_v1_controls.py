"""New synthetic frontend checks for declared sources and reviewed share exports."""
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / 'web/flow.js').read_text()
HTML = (ROOT / 'web/flow.html').read_text()

class ViewerV1ControlsTests(unittest.TestCase):
    def test_explicit_adapter_wins_over_legacy_agent_name(self):
        helper = JS[JS.index('function declaredAdapter('):JS.index('function sourceIdentity(')]
        cases = [
            {'bundle': {'manifest': {'source': {'adapter': 'unknown'}, 'agent': {'name': 'artex'}}}},
            {'bundle': {'manifest': {'source': {'adapter': 'artex'}, 'agent': {'name': 'renamed'}}}},
            {'bundle': {'manifest': {'agent': {'name': 'artex'}}}},
            {'bundle': {'manifest': {'source': {'adapter': 'codex'}}}},
            {'bundle': {'manifest': {'source': {'adapter': 'otlp'}}}},
        ]
        result = subprocess.check_output(['node', '-e', helper + '\nconsole.log(JSON.stringify(' + json.dumps(cases) + '.map(declaredAdapter)));'], text=True)
        self.assertEqual(json.loads(result), ['unknown', 'artex', 'artex', 'codex', 'otlp'])

    def test_sharing_requires_preview_and_explicit_review(self):
        self.assertIn('id="share-reviewed" type="checkbox"', HTML)
        self.assertIn('id="share-download" class="f-primary" type="button" disabled', HTML)
        self.assertIn("if(!preview||!$('share-reviewed').checked)return", JS)
        self.assertIn("selected.recording_id!==explanationService?.recording_id", JS)
        self.assertIn("ticket!==serial||!panel.isConnected", JS)
        self.assertIn('cannot guarantee every secret is found', HTML)
        self.assertIn('Attachments are omitted', HTML)

    def test_native_import_is_bounded_and_service_only(self):
        self.assertIn('/api/import-native', JS)
        self.assertIn('file.size>32*1024*1024', JS)
        self.assertIn('source_base64:btoa(binary)', JS)
        self.assertIn('python3 agentstoryline.py viewer /path/to/recording', JS)
        self.assertIn('\\.jsonl', JS)
        self.assertIn('.jsonl', HTML)
        self.assertIn('OTLP protobuf-JSON', JS)
        self.assertIn("id==='otlp'?'OpenTelemetry · experimental'", JS)
        self.assertIn("decoded?.format!=='traceforge-recording'", JS)
        self.assertIn('including OTLP JSON', JS)
        self.assertIn('service.recording_id=registered.recording_id', JS)
        self.assertIn('Recorded playback', HTML)
        self.assertNotIn('Play flow', HTML + JS)

if __name__ == '__main__':
    unittest.main()
