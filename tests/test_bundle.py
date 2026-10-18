import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from core.bundle import BundleValidationError, load_bundle, validate_bundle


def recording():
    return {
        "manifest": {"schema_version": "0.1", "run_id": "synthetic-run", "agent": {"name": "Synthetic", "version": "1"}, "started_at": "2026-10-10T10:00:00Z", "completeness": {"status": "complete", "gaps": []}},
        "events": [
            {"schema_version": "0.1", "id": "event-1", "run_id": "synthetic-run", "kind": "run.start", "timestamp": "2026-10-10T10:00:00Z", "sequence": 0},
            {"schema_version": "0.1", "id": "event-2", "run_id": "synthetic-run", "kind": "tool.end", "timestamp": "2026-10-10T10:00:01Z", "sequence": 4, "data": {"result": {"status": 200, "body": "synthetic"}}, "evidence_refs": ["event-1"], "dependencies": [{"event_id": "event-1", "provenance": "recorded"}]},
        ],
    }


class BundleTests(unittest.TestCase):
    def write_bundle(self, root, bundle):
        (root / "manifest.json").write_text(json.dumps(bundle["manifest"]), encoding="utf-8")
        (root / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in bundle["events"]), encoding="utf-8")

    def test_load_preserves_structured_data_and_order(self):
        bundle = recording()
        with tempfile.TemporaryDirectory() as directory:
            self.write_bundle(Path(directory), bundle)
            self.assertEqual(load_bundle(directory), bundle)

    def test_reject_invalid_fields(self):
        mutations = [
            lambda b: b["manifest"].update(schema_version="9"),
            lambda b: b["manifest"].update(run_id=""),
            lambda b: b["manifest"].update(started_at="2026-10-10 10:00:00"),
            lambda b: b["manifest"]["agent"].update(secret="outside-extensions"),
            lambda b: b["manifest"]["agent"].pop("version"),
            lambda b: b["events"][1].update(id="event-1"),
            lambda b: b["events"][1].update(run_id="other"),
            lambda b: b["events"][1].update(sequence=0),
            lambda b: b["events"][1].update(sequence=True),
            lambda b: b["events"][1].update(sequence=-1),
            lambda b: b["events"][1].update(kind=["tool.end"]),
            lambda b: b["events"][1].update(data=[]),
            lambda b: b["events"][1].update(timestamp="2026-02-30T00:00:00Z"),
            lambda b: b["events"][1].update(timestamp="2026-10-10T12:00:00+02:60"),
            lambda b: b["events"][1].update(evidence_refs=["missing"]),
            lambda b: b["events"][1].update(evidence_refs=["event-2"]),
            lambda b: b["events"][0].update(parent_id="event-2"),
            lambda b: b["events"][1].update(evidence_refs=["event-1", "event-1"]),
            lambda b: b["events"][1].update(evidence_refs=[{}]),
            lambda b: b["events"][1].update(dependencies=[{"event_id": "missing", "provenance": "recorded"}]),
            lambda b: b["events"][1].update(dependencies=[{"event_id": "event-1", "provenance": "guess"}]),
            lambda b: b["events"][1].update(dependencies=[{"event_id": "event-1", "provenance": ["inferred"]}]),
            lambda b: b["events"][1].pop("id"),
            lambda b: b["manifest"]["completeness"].update(status=["partial"]),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index):
                bundle = recording()
                mutate(bundle)
                with self.assertRaises(BundleValidationError):
                    validate_bundle(bundle)

    def test_partial_and_empty_completeness(self):
        for status, gaps, valid in [("complete", [], False), ("complete", ["missing output"], False), ("partial", [], False), ("partial", ["missing output"], True)]:
            bundle = recording()
            bundle["events"] = []
            bundle["manifest"]["completeness"] = {"status": status, "gaps": gaps}
            with self.subTest(status=status, gaps=gaps):
                if valid:
                    self.assertEqual(validate_bundle(bundle), bundle)
                else:
                    with self.assertRaises(BundleValidationError):
                        validate_bundle(bundle)

    def test_clock_disagreement_is_preserved(self):
        bundle = recording()
        bundle["events"][1]["timestamp"] = "2026-10-09T01:00:00+02:00"
        self.assertEqual(validate_bundle(bundle), bundle)

    def test_artifact_hash_and_path_safety(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifacts").mkdir()
            (root / "artifacts" / "output.txt").write_bytes(b"synthetic output")
            digest = hashlib.sha256(b"synthetic output").hexdigest()
            bundle = recording()
            bundle["events"][1]["artifact_refs"] = [{"path": "artifacts/output.txt", "sha256": digest}]
            self.write_bundle(root, bundle)
            self.assertEqual(load_bundle(root), bundle)
            with self.assertRaises(BundleValidationError):
                validate_bundle(bundle)
            for path in ["../output.txt", "/etc/passwd", "artifacts/../manifest.json", "artifacts/./output.txt", "artifacts//output.txt", "artifacts\\output.txt", "artifacts/missing", "artifacts/output.txt/"]:
                with self.subTest(path=path):
                    bad = copy.deepcopy(bundle)
                    bad["events"][1]["artifact_refs"][0]["path"] = path
                    self.write_bundle(root, bad)
                    with self.assertRaises(BundleValidationError):
                        load_bundle(root)
            bundle["events"][1]["artifact_refs"][0]["sha256"] = "0" * 64
            self.write_bundle(root, bundle)
            with self.assertRaises(BundleValidationError):
                load_bundle(root)

    def test_symlink_artifacts_and_envelopes_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = recording()
            self.write_bundle(root, bundle)
            (root / "target").write_bytes(b"data")
            (root / "artifacts").symlink_to(root, target_is_directory=True)
            bundle["manifest"]["artifact_refs"] = [{"path": "artifacts/target", "sha256": hashlib.sha256(b"data").hexdigest()}]
            self.write_bundle(root, bundle)
            with self.assertRaises(BundleValidationError):
                load_bundle(root)
            (root / "manifest.json").unlink()
            (root / "manifest.json").symlink_to(root / "target")
            with self.assertRaises(BundleValidationError):
                load_bundle(root)

    def test_raw_payload_is_not_artifact_contract(self):
        bundle = recording()
        bundle["events"][1]["data"] = {"path": "../../external", "sha256": "not-an-artifact", "artifact_refs": ["raw external string"]}
        self.assertEqual(validate_bundle(bundle), bundle)

    def test_duplicate_json_keys_non_json_and_blank_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_bundle(root, recording())
            for contents in ['{"id":"a","id":"b"}\n', '{"value":NaN}\n', '{"value":1e999}\n', '\n', '{broken\n']:
                with self.subTest(contents=contents):
                    (root / "events.jsonl").write_text(contents)
                    with self.assertRaises(BundleValidationError):
                        load_bundle(root)


if __name__ == "__main__":
    unittest.main()
