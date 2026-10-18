"""Newly authored synthetic native traffic-sidecar regression tests."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from adapters.artex.coverage import source_coverage
from adapters.artex.importer import ArtexImportError, import_artex


class NativeTrafficTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "source"
        (self.source / "traffic").mkdir(parents=True)
        self.snapshot = {
            "format_version": 3, "task_id": 1, "created_at": "2026-10-17T12:00:00Z",
            "tables": {"tasks": [{"id": 1}]}, "data_counts": {"tasks": 1, "traffic": 2}}
        self.traffic = {"version": 1, "exchanges": [
            {"id": "synthetic-request-1", "ts": 1792238400000, "host": "127.0.0.1"},
            {"id": "synthetic-request-2", "ts": 1792238401000, "host": "127.0.0.1"}], "blobs": None}
        self.save()

    def tearDown(self):
        self.temporary.cleanup()

    def save(self):
        (self.source / "manifest.json").write_text(json.dumps(self.snapshot))
        (self.source / "traffic/traffic.json").write_text(json.dumps(self.traffic))

    def test_native_sidecar_count_and_original_artifacts_for_each_package(self):
        archive = self.root / "native.tar"
        with tarfile.open(archive, "w") as output:
            for path in sorted(self.source.rglob("*")):
                if path.is_file():
                    output.add(path, arcname=path.relative_to(self.source).as_posix())
        sources = [self.source, archive]
        if shutil.which("zstd"):
            compressed = self.root / "native.tar.zst"
            with compressed.open("wb") as stream:
                subprocess.run([shutil.which("zstd"), "-q", "-c", str(archive)], stdout=stream, check=True)
            sources.append(compressed)
        original = (self.source / "traffic/traffic.json").read_bytes()
        for index, source in enumerate(sources):
            with self.subTest(source=source.name):
                destination = self.root / ("bundle-" + str(index))
                bundle = import_artex(source, destination)
                self.assertEqual((destination / "artifacts/artex/traffic/traffic.json").read_bytes(), original)
                self.assertEqual(json.loads((destination / "artifacts/artex/manifest.json").read_bytes()), self.snapshot)
                self.assertTrue(any("not mapped to timeline events" in gap for gap in bundle["manifest"]["completeness"]["gaps"]))
                coverage = source_coverage(source, bundle)
                self.assertEqual(coverage["tables"]["traffic"], 2)
                self.assertEqual(coverage["categories"]["findings_traffic"]["source_count"], 2)
                self.assertEqual(coverage["categories"]["findings_traffic"]["imported_count"], 0)
                item = next(item for item in coverage["files"] if item["path"] == "traffic/traffic.json")
                self.assertEqual(item["sha256"], hashlib.sha256(original).hexdigest())
                self.assertEqual(item["imported_status"], "preserved")

    def test_native_empty_exchanges_null_or_array(self):
        for index, exchanges in enumerate((None, [])):
            self.traffic["exchanges"] = exchanges
            self.snapshot["data_counts"]["traffic"] = 0
            self.save()
            bundle = import_artex(self.source, self.root / ("empty-" + str(index)))
            self.assertEqual(source_coverage(self.source, bundle)["tables"]["traffic"], 0)

    def test_missing_malformed_ambiguous_or_mismatched_traffic_is_rejected(self):
        original_snapshot = json.loads(json.dumps(self.snapshot))
        original_traffic = json.loads(json.dumps(self.traffic))
        for mutation in ("missing", "json", "version", "boolean_version", "shape", "missing_exchanges", "row", "count", "ambiguous"):
            with self.subTest(mutation=mutation):
                self.snapshot = json.loads(json.dumps(original_snapshot))
                self.traffic = json.loads(json.dumps(original_traffic))
                if mutation == "version":
                    self.traffic["version"] = 2
                elif mutation == "boolean_version":
                    self.traffic["version"] = True
                elif mutation == "shape":
                    self.traffic["exchanges"] = {}
                elif mutation == "missing_exchanges":
                    del self.traffic["exchanges"]
                elif mutation == "row":
                    self.traffic["exchanges"] = ["invalid-row", {}]
                elif mutation == "count":
                    self.snapshot["data_counts"]["traffic"] = 3
                elif mutation == "ambiguous":
                    self.snapshot["tables"]["traffic"] = [{"id": 7}]
                self.save()
                if mutation == "missing":
                    (self.source / "traffic/traffic.json").unlink()
                elif mutation == "json":
                    (self.source / "traffic/traffic.json").write_text("{")
                destination = self.root / "rejected"
                with self.assertRaises(ArtexImportError):
                    import_artex(self.source, destination)
                self.assertFalse(destination.exists())
                with self.assertRaises(ArtexImportError):
                    source_coverage(self.source)

    def test_sidecar_exchanges_obey_total_row_limit(self):
        with patch("adapters.artex.importer.MAX_ROWS", 2):
            with self.assertRaisesRegex(ArtexImportError, "row count limit"):
                import_artex(self.source, self.root / "over-budget")
            with self.assertRaisesRegex(ArtexImportError, "row count limit"):
                source_coverage(self.source)


if __name__ == "__main__":
    unittest.main()
