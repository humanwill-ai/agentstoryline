"""Public synthetic examples: native import, exact artifacts and portable reload."""

import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest

from adapters.artex.importer import import_artex
from adapters.codex import import_codex
from adapters.otlp import import_otlp
from analysis.explanation_workflow import load_portable
from analysis.narrative import build_observed_timeline
from core.bundle import load_bundle
from examples.generate_demos import demo_files
from web.recording import write_recording
from web.render import build_payload
from web.viewer_import import detect_source, normalize_source


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class ReleaseExampleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        # Import from a real temporary path, avoiding OS-protected ancestor
        # directory traversal in macOS app sandboxes without weakening readers.
        self.sources = self.root / "examples"
        shutil.copytree(EXAMPLES, self.sources)

    def roundtrip(self, importer, source, name):
        destination = self.root / name
        bundle = importer(source, destination)
        self.assertEqual(load_bundle(destination), bundle)
        self.assertEqual(bundle["manifest"]["completeness"]["status"], "partial")
        self.assertTrue(bundle["manifest"]["completeness"]["gaps"])
        for artifact in bundle["manifest"]["artifact_refs"]:
            raw = (destination / artifact["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), artifact["sha256"])
        payload = build_payload(bundle, build_observed_timeline(bundle))
        exported = self.root / (name + ".agentstoryline.json")
        write_recording(payload, exported)
        reloaded = load_portable(exported)
        self.assertEqual(reloaded["payload"], payload)
        self.assertEqual(reloaded["payload"]["bundle"]["events"], bundle["events"])
        self.assertEqual(reloaded["attachments"], [])
        return bundle, destination

    def test_checked_in_inputs_match_deterministic_generator(self):
        for relative, expected in demo_files().items():
            with self.subTest(file=relative):
                self.assertEqual((EXAMPLES / relative).read_bytes(), expected)

    def test_artex_directory_and_native_archive_roundtrip(self):
        directory, destination = self.roundtrip(import_artex, self.sources / "artex-local-inspection", "artex-directory")
        archive, _ = self.roundtrip(import_artex, self.sources / "artex-local-inspection.tar", "artex-archive")
        self.assertEqual(directory["events"], archive["events"])
        events = {event["id"]: event for event in directory["events"]}
        self.assertEqual(len(events), 9)
        request = events["artex.llm_records.101.request"]
        start, end = events["artex.activity.11"], events["artex.activity.12"]
        self.assertEqual(start["dependencies"], [{"event_id": request["id"], "provenance": "recorded"}])
        self.assertEqual(end["dependencies"], [{"event_id": start["id"], "provenance": "recorded"}])
        self.assertEqual(end["data"]["result"]["content"][0]["text"], '{"ok":true,"value":42}')
        for relative in ("files/fixture.json", "files/transcripts/exp42-synthetic.jsonl", "database/llm_records.ndjson"):
            self.assertEqual((destination / "artifacts/artex" / relative).read_bytes(),
                             (EXAMPLES / "artex-local-inspection" / relative).read_bytes())

    def test_experimental_codex_roundtrip_and_explicit_native_pairs(self):
        bundle, destination = self.roundtrip(import_codex, self.sources / "experimental-codex-rollout.jsonl", "codex")
        events = bundle["events"]
        self.assertEqual([event["kind"] for event in events],
                         ["model.response", "tool.start", "tool.end", "tool.start", "tool.end", "model.response"])
        for start, end in ((events[1], events[2]), (events[3], events[4])):
            self.assertEqual(end["dependencies"], [{"event_id": start["id"], "provenance": "recorded"}])
            self.assertNotIn("dependencies", start)
        self.assertEqual((destination / "artifacts/codex/rollout.jsonl").read_bytes(),
                         (EXAMPLES / "experimental-codex-rollout.jsonl").read_bytes())

    def test_experimental_otlp_roundtrip_preserves_structural_relationships(self):
        bundle, destination = self.roundtrip(import_otlp, self.sources / "experimental-otlp.json", "otlp")
        events = bundle["events"]
        self.assertEqual([event["kind"] for event in events],
                         ["model.request", "tool.start", "tool.end", "model.response", "model.response"])
        self.assertTrue(all("dependencies" not in event and "parent_id" not in event for event in events))
        self.assertEqual(events[1]["data"]["span_hierarchy"]["parent_span_id"], "0000000000000001")
        self.assertTrue(events[4]["data"]["span_correlation"]["mapped_span_present"])
        self.assertEqual(events[0]["data"]["otlp_time_unix_nano"], "1792324801000000042")
        self.assertEqual((destination / "artifacts/otlp/export.json").read_bytes(),
                         (EXAMPLES / "experimental-otlp.json").read_bytes())

    def test_examples_normalize_through_viewer_and_portable_reload(self):
        for filename, expected_kind, adapter, count in (
            ("artex-local-inspection.tar", "artex", "artex", 9),
            ("experimental-codex-rollout.jsonl", "codex-rollout", "codex", 6),
            ("experimental-otlp.json", "otlp", "otlp", 5),
        ):
            with self.subTest(source=filename):
                source = self.sources / filename
                self.assertEqual(detect_source(source), expected_kind)
                package, portable = normalize_source(source, self.root / "viewer-state")
                self.assertEqual(package, load_portable(portable))
                self.assertEqual(len(package["payload"]["bundle"]["events"]), count)
                self.assertEqual(package["payload"]["bundle"]["manifest"]["source"]["adapter"], adapter)


if __name__ == "__main__":
    unittest.main()
