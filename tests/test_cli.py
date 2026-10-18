"""Exercise the public offline workflow, including untrusted proposal import."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from traceforge import main
from tests.test_otlp_adapter import fixture as otlp_fixture

ROOT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def invoke(self, *args):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            code = main(list(map(str, args)))
        return code, stream.getvalue()

    def test_import_analysis_view_and_proposal_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            bundle = root / "bundle"
            self.assertEqual(self.invoke("import-artex", ROOT / "tests/fixtures/artex/token_session", bundle)[0], 0)
            self.assertEqual(self.invoke("validate", bundle)[0], 0)
            analysis = root / "analysis.json"
            self.assertEqual(self.invoke("analysis-import", bundle, ROOT / "tests/fixtures/analysis/artex-token-narrative.json", analysis, "--model", "gpt-6.1-sol", "--provider", "offline-worker-fixture")[0], 0)
            self.assertEqual(self.invoke("timeline", bundle, root / "timeline.html", "--analysis", analysis)[0], 0)
            self.assertEqual(self.invoke("analysis-request", bundle, root / "request.json", "--model", "offline-test")[0], 0)
            plan = {"schema_version": "0.1", "run_id": "artex-task-23", "allowed_origins": ["http://127.0.0.1:9000"], "steps": [{"id": "session", "operation": "http.request", "method": "GET", "url": "http://127.0.0.1:9000/session", "evidence_refs": ["artex.activity.11"], "provenance": "inferred"}]}
            response = root / "proposal.json"
            response.write_text(json.dumps(plan))
            validated = root / "validated.json"
            self.assertEqual(self.invoke("replay-import", bundle, response, validated, "--allowed-origin", "http://127.0.0.1:9000")[0], 0)
            self.assertEqual(self.invoke("replay", validated, root / "never-created")[0], 0)
            self.assertFalse((root / "never-created").exists())
            self.assertEqual(self.invoke("export-replay", validated, root / "package")[0], 0)
            self.assertTrue((root / "package/replay/runner.py").exists())
            (root / "narrative-response.json").write_bytes((ROOT / "tests/fixtures/analysis/artex-token-narrative.json").read_bytes())
            self.assertEqual(self.invoke("workflow-request", bundle, root / "generation-requests", "--model", "gpt-6.1-sol", "--allowed-origin", "http://127.0.0.1:9000")[0], 0)
            code, message = self.invoke("workflow-import", bundle, root / "generation-requests",
                root / "narrative-response.json", response, root / "workflow",
                "--provider", "offline-worker-fixture", "--generation-method", "offline-development-worker")
            self.assertEqual(code, 0, message)
            self.assertTrue((root / "workflow/timeline.html").exists())
            self.assertEqual(json.loads((root / "workflow/generation.json").read_text())["execution"], "not_requested")
            altered = copy.deepcopy(plan)
            altered["allowed_origins"] = ["http://127.0.0.1:9001"]
            altered["steps"][0]["url"] = "http://127.0.0.1:9001/session"
            response.write_text(json.dumps(altered))
            code, message = self.invoke("replay-import", bundle, response, root / "rejected.json", "--allowed-origin", "http://127.0.0.1:9000")
            self.assertEqual(code, 2, message)
            self.assertFalse((root / "rejected.json").exists())

    def test_unknown_evidence_and_invalid_json_report_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            self.assertEqual(self.invoke("import-artex", ROOT / "tests/fixtures/artex/token_session", root / "bundle")[0], 0)
            response = root / "response.json"
            response.write_text(json.dumps({"run_id": "artex-task-23", "stages": [{"id": "fake", "title": "Unsupported claim", "summary": "No evidence", "event_ids": ["missing"], "basis": "interpretation"}]}))
            code, message = self.invoke("analysis-import", root / "bundle", response, root / "analysis.json", "--model", "offline-test")
            self.assertEqual(code, 2, message)
            self.assertFalse((root / "analysis.json").exists())
            response.write_text('{"duplicate":1,"duplicate":2}')
            self.assertEqual(self.invoke("replay", response, root / "result")[0], 2)

    def test_otlp_import_command_creates_valid_bundle_without_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / 'telemetry.json'
            source.write_text(json.dumps(otlp_fixture()))
            bundle = root / 'bundle'
            code, output = self.invoke('import-otlp', source, bundle)
            self.assertEqual(code, 0, output)
            code, output = self.invoke('validate', bundle)
            self.assertEqual(code, 0, output)
            self.assertEqual(json.loads(output)['completeness']['status'], 'partial')
