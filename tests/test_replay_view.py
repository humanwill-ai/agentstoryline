"""New offline replay-overlay tests using synthetic evidence only."""
import copy
import contextlib
import io
import json
import re
import tempfile
from pathlib import Path
from unittest.mock import patch
from tests.test_bundle import recording
from traceforge import main
import unittest
from replay.runner import ReplayValidationError
from web.replay_view import build_replay_view


class ReplayViewTests(unittest.TestCase):
    def setUp(self):
        self.bundle = {"manifest": {"run_id": "synthetic"}, "events": [{"id": "e1"}, {"id": "e2"}]}
        self.storyline = {"activities": [{"id": "a1", "event_ids": ["e1"]}, {"id": "a2", "event_ids": ["e2"]}]}
        self.plan = {"schema_version": "0.1", "run_id": "synthetic", "allowed_origins": ["http://127.0.0.1:8765"], "steps": [
            {"id": "session", "operation": "http.request", "method": "GET", "url": "http://127.0.0.1:8765/session", "evidence_refs": ["e1"], "provenance": "authored", "extract": {"token": {"source": "json", "path": ["token"]}}},
            {"id": "protected", "operation": "http.request", "method": "GET", "url": "http://127.0.0.1:8765/protected", "evidence_refs": ["e2"], "provenance": "authored", "headers": {"Authorization": {"template": "Bearer {token}", "bindings": {"token": {"$ref": "session.token"}}}}}]}

    def view(self):
        return build_replay_view(self.bundle, self.plan, self.storyline)

    def test_exact_binding_preserved_without_resolving_or_execution(self):
        original = copy.deepcopy(self.plan)
        view = self.view()
        edge = view["edges"][0]
        self.assertEqual((edge["source_activity_id"], edge["target_activity_id"]), ("a1", "a2"))
        self.assertEqual(edge["input_path"], "/headers/Authorization/bindings/token")
        self.assertEqual(edge["reference"], "session.token")
        self.assertEqual(edge["kind"], "replay_binding")
        self.assertEqual(edge["provenance"], "authored")
        self.assertEqual(view["steps"][1]["headers"], original["steps"][1]["headers"])
        self.assertEqual(self.plan, original)
        self.assertEqual(view["execution"], "not_requested")

    def test_inferred_provenance_and_ambiguous_activity(self):
        self.plan["steps"][0]["provenance"] = "inferred"
        self.storyline["activities"].append({"id": "another", "event_ids": ["e1"]})
        view = self.view()
        self.assertIsNone(view["edges"][0]["source_activity_id"])
        self.assertEqual(view["edges"][0]["source_event_id"], "e1")
        self.assertEqual(view["edges"][0]["provenance"], "inferred")
        self.assertTrue(any("ambiguous" in note for note in view["notes"]))

    def test_multi_event_citations_never_choose_arbitrary_event(self):
        self.plan["steps"][0]["evidence_refs"] = ["e1", "e2"]
        edge = self.view()["edges"][0]
        self.assertIsNone(edge["source_activity_id"])
        self.assertIsNone(edge["source_event_id"])
        self.assertEqual(edge["source_evidence_refs"], ["e1", "e2"])

    def test_invalid_scope_run_reference_and_citations_rejected(self):
        changes = [lambda p: p.update(run_id="other"),
                   lambda p: p["steps"][0].update(url="http://example.com/"),
                   lambda p: p["steps"][0].update(evidence_refs=[]),
                   lambda p: p["steps"][0].update(evidence_refs=["missing"]),
                   lambda p: p["steps"][1]["headers"].update(Authorization={"$ref": "missing.token"}),
                   lambda p: p["steps"][1].update(dependencies=[{"event_id": "missing", "provenance": "inferred"}])]
        for change in changes:
            plan = copy.deepcopy(self.plan)
            change(plan)
            with self.subTest(plan=plan), self.assertRaises(ReplayValidationError):
                build_replay_view(self.bundle, plan, self.storyline)

    def test_recorded_dependency_requires_exact_recorded_evidence(self):
        self.plan["steps"][1]["dependencies"] = [{"event_id": "e1", "provenance": "recorded"}]
        with self.assertRaises(ReplayValidationError):
            self.view()
        self.bundle["events"][1]["dependencies"] = [{"event_id": "e1", "provenance": "recorded"}]
        self.assertEqual(len(self.view()["edges"]), 1)
        self.plan["steps"][1]["dependencies"] *= 2
        with self.assertRaises(ReplayValidationError):
            self.view()

    def test_timeline_cli_plan_validates_before_output_and_never_executes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle_dir = root / "bundle"
            bundle_dir.mkdir()
            bundle = recording()
            (bundle_dir / "manifest.json").write_text(json.dumps(bundle["manifest"]))
            (bundle_dir / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in bundle["events"]))
            plan = copy.deepcopy(self.plan)
            plan["run_id"] = bundle["manifest"]["run_id"]
            for step, event in zip(plan["steps"], bundle["events"]):
                step["evidence_refs"] = [event["id"]]
            plan_path = root / "plan.json"
            output = root / "timeline.html"
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream), patch("replay.runner._LiteralHTTPConnection", side_effect=AssertionError("viewer opened HTTP connection")), patch("replay.runner.run_plan", side_effect=AssertionError("viewer executed plan")):
                invalid = copy.deepcopy(plan)
                invalid["steps"][1]["evidence_refs"] = ["unknown-event"]
                plan_path.write_text(json.dumps(invalid))
                self.assertEqual(main(["timeline", str(bundle_dir), str(output), "--plan", str(plan_path)]), 2)
                self.assertFalse(output.exists())
                # Duplicate object keys must be rejected by the CLI's safe reader.
                plan_path.write_text('{"run_id":"one","run_id":"two"}')
                self.assertEqual(main(["timeline", str(bundle_dir), str(output), "--plan", str(plan_path)]), 2)
                self.assertFalse(output.exists())
                plan_path.write_text(json.dumps(plan))
                self.assertEqual(main(["timeline", str(bundle_dir), str(output), "--plan", str(plan_path)]), 0, stream.getvalue())
            html = output.read_text()
            embedded = re.search(r'<script[^>]*id="recording"[^>]*>(.*?)</script>', html, re.S)
            self.assertIsNotNone(embedded)
            payload = json.loads(embedded.group(1))
            self.assertEqual(payload["replay"]["execution"], "not_requested")
            bindings = [edge for edge in payload["storyline"]["edges"] if edge["kind"] == "replay_binding"]
            self.assertEqual(len(bindings), 1)
            self.assertEqual(bindings[0]["reference"], "session.token")
            self.assertEqual(bindings[0]["source_event_id"], "event-1")
            self.assertEqual(bindings[0]["target_event_id"], "event-2")
