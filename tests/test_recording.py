"""New synthetic portable recording tests; no private captured evidence."""
import json
from pathlib import Path
import tempfile
import unittest
from web.render import build_payload, render_timeline
from web.recording import write_recording


class RecordingTests(unittest.TestCase):
    def payload(self):
        bundle = {'manifest': {'agent': {'name': 'Synthetic'}, 'run_id': 'synthetic'}, 'events': [
            {'id': 'e', 'kind': 'observation', 'sequence': 0, 'timestamp': None,
             'data': {'content': '</script><script>throw Error("executed")</script>', 'number': 9007199254740993}}]}
        return build_payload(bundle, {})

    def test_preserves_evidence_and_attaches_script_without_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / 'replay.py'
            script.write_text('raise RuntimeError("must never execute")\n')
            write_recording(self.payload(), root / 'run.traceforge.json', replay_scripts=[script])
            data = json.loads((root / 'run.traceforge.json').read_text())
            self.assertEqual(data['format'], 'traceforge-recording')
            self.assertIn('9007199254740993', data['payload']['evidence_text']['e']['data'])
            self.assertEqual(data['attachments'][0]['text'], script.read_text())
            with self.assertRaises(FileExistsError):
                write_recording(self.payload(), root / 'run.traceforge.json')

    def test_limits_and_extension(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bad = root / 'bad.html'; bad.write_text('<script>bad</script>')
            with self.assertRaisesRegex(ValueError, '.py'):
                write_recording(self.payload(), root / 'out.json', replay_scripts=[bad])
            large = root / 'large.py'; large.write_bytes(b'x' * (1024 * 1024 + 1))
            with self.assertRaisesRegex(ValueError, '1 MiB'):
                write_recording(self.payload(), root / 'out.json', replay_scripts=[large])
            payload = self.payload(); payload['flow']['messages'] *= 5001
            with self.assertRaisesRegex(ValueError, '5,000'):
                write_recording(payload, root / 'out.json')

    def test_timeline_packages_identical_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); payload = self.payload()
            render_timeline(payload['bundle'], {}, root / 'view.html', recording_output=root / 'run.json')
            package = json.loads((root / 'run.json').read_text())
            self.assertEqual(package['payload'], payload)
            html = (root / 'view.html').read_text()
            self.assertIn('open-recording', html)
            self.assertNotIn('</script><script>throw', html)

    def test_existing_html_does_not_leave_partial_recording(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); html = root / 'existing.html'; html.write_text('keep')
            payload = self.payload()
            with self.assertRaises(FileExistsError):
                render_timeline(payload['bundle'], {}, html, recording_output=root / 'out.json')
            self.assertFalse((root / 'out.json').exists())
            self.assertEqual(html.read_text(), 'keep')
            with self.assertRaisesRegex(ValueError, 'different'):
                render_timeline(payload['bundle'], {}, root / 'same', recording_output=root / 'same')
            self.assertFalse((root / 'same').exists())
