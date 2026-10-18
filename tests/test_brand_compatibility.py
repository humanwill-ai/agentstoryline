"""New synthetic checks for AgentStoryline branding and legacy recording compatibility."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from analysis.explanation_workflow import load_portable
from web.viewer_import import normalize_source

ROOT = Path(__file__).resolve().parents[1]


class BrandCompatibilityTests(unittest.TestCase):
    def invoke(self, entrypoint, *args):
        return subprocess.run([sys.executable, str(ROOT / entrypoint), *map(str, args)],
                              capture_output=True, text=True, cwd=ROOT)

    def test_new_and_legacy_cli_share_offline_commands_and_errors(self):
        import agentstoryline
        import traceforge
        self.assertIs(traceforge.main, agentstoryline.main)
        for entrypoint in ('agentstoryline.py', 'traceforge.py'):
            with self.subTest(entrypoint=entrypoint):
                version_result = self.invoke(entrypoint, '--version')
                self.assertEqual(version_result.returncode, 0, version_result.stderr)
                self.assertEqual(version_result.stdout.strip(), 'AgentStoryline 0.1.0')
                help_result = self.invoke(entrypoint, '--help')
                self.assertEqual(help_result.returncode, 0, help_result.stderr)
                self.assertIn('AgentStoryline', help_result.stdout)
                with tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    imported = self.invoke(entrypoint, 'import-artex',
                                           ROOT / 'tests/fixtures/artex/token_session', root / 'bundle')
                    self.assertEqual(imported.returncode, 0, imported.stderr)
                    rendered = self.invoke(entrypoint, 'timeline', root / 'bundle', root / 'view.html',
                                           '--recording-output', root / 'legacy.traceforge.json')
                    self.assertEqual(rendered.returncode, 0, rendered.stderr)
                    recording = root / 'legacy.traceforge.json'
                    original = recording.read_bytes()
                    package = load_portable(recording)
                    self.assertEqual(package['format'], 'traceforge-recording')
                    normalized, source = normalize_source(recording, root / 'state')
                    self.assertEqual(source, recording)
                    self.assertEqual(normalized, package)
                    self.assertEqual(recording.read_bytes(), original)
                    html = (root / 'view.html').read_text()
                    self.assertIn('<title>AgentStoryline', html)
                    self.assertIn('python3 agentstoryline.py viewer', html)
                    self.assertIn('X-TraceForge-Token', html)
                    self.assertIn("'traceforge-recording'", html)
                    failed = self.invoke(entrypoint, 'validate', root / 'missing')
                    self.assertEqual(failed.returncode, 2)
                    self.assertIn('AgentStoryline:', failed.stderr)

    def test_provider_badge_uses_text_without_distributed_logo(self):
        source = (ROOT / 'web/flow.js').read_text()
        self.assertIn("el.textContent='OA'", source)
        self.assertNotIn('const modelLogo=', source)
        self.assertNotIn('M9.205 8.658', source)
        self.assertIn('el.title=displayName(a)', source)
