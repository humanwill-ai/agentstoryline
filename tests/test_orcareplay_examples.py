"""Newly authored OrcaReplay examples; no external dataset or operation is run."""

import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
import zipfile

from adapters.orcareplay import import_orcareplay
from analysis.explanation_workflow import load_portable
from analysis.narrative import build_observed_timeline
from core.bundle import load_bundle
from examples.generate_demos import demo_files, orcareplay_files
from web.recording import write_recording
from web.render import build_payload
from web.viewer_import import detect_source, normalize_source


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class OrcaReplayExampleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "experimental-orcareplay"
        shutil.copytree(EXAMPLES / "experimental-orcareplay", self.source)
        self.archive = self.root / "experimental-orcareplay.tar"
        shutil.copyfile(EXAMPLES / "experimental-orcareplay.tar", self.archive)
        self.zip_archive = self.root / "experimental-orcareplay.orca.zip"
        shutil.copyfile(EXAMPLES / "experimental-orcareplay.orca.zip", self.zip_archive)
        self.orca_archive = self.root / "experimental-orcareplay.orca"
        shutil.copyfile(self.zip_archive, self.orca_archive)

    def test_checked_in_directory_tar_and_zip_bytes_are_deterministic(self):
        files = demo_files()
        for relative, raw in files.items():
            if relative.startswith("experimental-orcareplay"):
                with self.subTest(path=relative):
                    self.assertEqual((EXAMPLES / relative).read_bytes(), raw)
        with tarfile.open(fileobj=io.BytesIO(files["experimental-orcareplay.tar"]), mode="r:") as archive:
            self.assertEqual(archive.getnames(), sorted(orcareplay_files()))
            for member in archive:
                self.assertTrue(member.isfile())
                self.assertEqual((member.mtime, member.uid, member.gid, member.uname, member.gname), (0, 0, 0, "", ""))
                self.assertEqual(archive.extractfile(member).read(), orcareplay_files()[member.name])

        with zipfile.ZipFile(io.BytesIO(files["experimental-orcareplay.orca.zip"])) as archive:
            self.assertEqual(archive.namelist(), sorted(orcareplay_files()))
            for member in archive.infolist():
                self.assertEqual(member.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(member.compress_type, zipfile.ZIP_STORED)
                self.assertEqual(member.external_attr >> 16, 0o100644)
                self.assertEqual(archive.read(member), orcareplay_files()[member.filename])

    def test_sample_integrity_is_bound_to_authored_event_and_blob_bytes(self):
        files = orcareplay_files()
        manifest = json.loads(files["manifest.json"])
        events = [json.loads(row) for row in files["events.jsonl"].splitlines()]
        self.assertEqual(manifest["integrity"]["events_sha256"], hashlib.sha256(files["events.jsonl"]).hexdigest())
        self.assertTrue(manifest["provenance"]["synthetic"])
        self.assertEqual(manifest["counts"]["events"], len(events))
        self.assertEqual([event["type"] for event in events], ["run.start", "model.request", "tool.call", "tool.result", "fs.snapshot", "fs.change", "model.response", "run.end"])
        for seq in (3, 5):
            ref = events[seq]["payload"]
            digest = ref["$blob"].removeprefix("sha256:")
            raw = files["blobs/" + digest[:2] + "/" + digest]
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            self.assertEqual(len(raw), ref["bytes"])

    def test_directory_tar_zip_evidence_and_portable_roundtrip(self):
        directory_bundle = None
        for source, name in ((self.source, "directory"), (self.archive, "tar"),
                             (self.zip_archive, "zip"), (self.orca_archive, "orca")):
            with self.subTest(source=name):
                destination = self.root / (name + "-bundle")
                bundle = import_orcareplay(source, destination)
                self.assertEqual(load_bundle(destination), bundle)
                self.assertEqual(bundle["manifest"]["source"]["adapter"], "orcareplay")
                self.assertEqual(bundle["manifest"]["completeness"]["status"], "partial")
                self.assertEqual(len(bundle["events"]), 8)
                self.assertEqual([event["kind"] for event in bundle["events"]][2:4], ["tool.start", "tool.end"])
                for relative, raw in orcareplay_files().items():
                    self.assertEqual((destination / "artifacts/orcareplay/files" / relative).read_bytes(), raw)
                for artifact in bundle["manifest"]["artifact_refs"]:
                    raw = (destination / artifact["path"]).read_bytes()
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), artifact["sha256"])
                payload = build_payload(bundle, build_observed_timeline(bundle))
                portable = self.root / (name + ".traceforge.json")
                write_recording(payload, portable)
                self.assertEqual(load_portable(portable)["payload"], payload)
                if directory_bundle is None:
                    directory_bundle = bundle
                else:
                    self.assertEqual(bundle["events"], directory_bundle["events"])

    def test_viewer_detects_and_normalizes_native_evidence(self):
        for source in (self.source, self.archive, self.zip_archive, self.orca_archive):
            with self.subTest(source=source.name):
                self.assertEqual(detect_source(source), "orcareplay")
                package, portable = normalize_source(source, self.root / "viewer-state")
                self.assertEqual(package, load_portable(portable))
                self.assertEqual(package["payload"]["bundle"]["manifest"]["source"]["adapter"], "orcareplay")
                self.assertEqual(len(package["payload"]["bundle"]["events"]), 8)


if __name__ == "__main__":
    unittest.main()
