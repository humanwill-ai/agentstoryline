"""Authored synthetic release-verifier checks; no candidate code is executed."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import build_public_source as builder
from scripts import verify_release_candidate as verifier


class ReleaseCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        self.paths = ("LICENSE", "THIRD_PARTY_NOTICES.md", "README.md", "docs/guide.md", "scripts/inert.py")
        for name in self.paths:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Synthetic release material\n")
        (self.source / "README.md").write_text(
            '# Synthetic release\n[Guide](docs/guide.md#use-the-flag)\n'
            '[Reference][guide]\n[guide]: docs/guide.md\n'
            '<a href="docs/guide.md#explicit">Details</a>\n'
            '[External](https://invalid.invalid/never-fetch)\n'
            '`[Code](missing-inline.md)`\n`<img src="missing-code.png">`\n'
            '```md\n[Code](missing-fenced.md)\n```\n')
        (self.source / "docs/guide.md").write_text(
            '# Guide\n## Use the `flag`\n<a id="explicit"></a>\n[Back](../README.md#synthetic-release)\n')
        (self.source / "scripts/inert.py").write_text('raise RuntimeError("candidate must never run")\n')
        for obj, name, value in (
            (builder, "PUBLIC_FILES", self.paths),
            (builder, "OPTIONAL_FILES", ("docs/optional.png",)),
            (verifier, "REQUIRED_RELEASE_FILES", frozenset({"LICENSE", "THIRD_PARTY_NOTICES.md", "README.md"})),
        ):
            active = patch.object(obj, name, value)
            active.start()
            self.addCleanup(active.stop)
        self.snapshot = self.root / "snapshot"
        self.manifest = builder.build_public_source(self.snapshot, source_root=self.source)

    def write_manifest(self, manifest=None, *, recalculate=False):
        manifest = self.manifest if manifest is None else manifest
        if recalculate:
            manifest["file_count"] = len(manifest["files"])
            manifest["total_bytes"] = sum(item["bytes"] for item in manifest["files"])
            manifest["content_sha256"] = hashlib.sha256(builder._json_bytes(manifest["files"])).hexdigest()
        (self.snapshot / builder.MANIFEST_NAME).write_bytes(builder._json_bytes(manifest))

    def replace_readme(self, text):
        raw = text.encode()
        (self.snapshot / "README.md").write_bytes(raw)
        item = next(item for item in self.manifest["files"] if item["path"] == "README.md")
        item.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        self.write_manifest(recalculate=True)

    def test_valid_snapshot_is_unchanged_and_candidate_code_stays_inert(self):
        before = {p.relative_to(self.snapshot).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.snapshot.rglob("*") if p.is_file()}
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")), \
                patch("subprocess.run", side_effect=AssertionError("execution forbidden")):
            result = verifier.verify_release_candidate(self.snapshot)
        self.assertEqual(result["content_sha256"], self.manifest["content_sha256"])
        self.assertEqual(result["file_count"], len(self.paths))
        self.assertEqual(result["local_markdown_links_checked"], 4)
        after = {p.relative_to(self.snapshot).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in self.snapshot.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_content_tampering_is_detected(self):
        path = self.snapshot / "LICENSE"
        original = path.read_bytes()
        for raw, error in ((original + b"x", "byte count"), (b"X" + original[1:], "SHA-256")):
            with self.subTest(error=error):
                path.write_bytes(raw)
                with self.assertRaisesRegex(ValueError, error):
                    verifier.verify_release_candidate(self.snapshot)

    def test_manifest_schema_counts_and_aggregate_are_strict(self):
        for key, value in (("version", True), ("file_count", True), ("file_count", 999),
                           ("release_version", "0.0.0"),
                           ("total_bytes", -1), ("total_bytes", 999),
                           ("content_sha256", "0" * 64), ("scope", None),
                           ("omitted_optional_files", []), ("unexpected", "value")):
            with self.subTest(key=key, value=value):
                manifest = json.loads(json.dumps(self.manifest))
                manifest[key] = value
                self.write_manifest(manifest)
                with self.assertRaises(ValueError):
                    verifier.verify_release_candidate(self.snapshot)

    def test_duplicate_json_keys_and_file_paths_are_refused(self):
        raw = builder._json_bytes(self.manifest).decode().replace('"version": 1', '"version": 1, "version": 1')
        (self.snapshot / builder.MANIFEST_NAME).write_text(raw)
        with self.assertRaisesRegex(ValueError, "duplicate manifest JSON key"):
            verifier.verify_release_candidate(self.snapshot)
        self.manifest["files"].insert(0, dict(self.manifest["files"][0]))
        self.write_manifest(recalculate=True)
        with self.assertRaisesRegex(ValueError, "unique and sorted"):
            verifier.verify_release_candidate(self.snapshot)

    def test_rehashed_manifest_cannot_hide_missing_required_material(self):
        self.manifest["files"] = [item for item in self.manifest["files"] if item["path"] != "LICENSE"]
        (self.snapshot / "LICENSE").unlink()
        self.write_manifest(recalculate=True)
        with self.assertRaisesRegex(ValueError, "missing required release files: LICENSE"):
            verifier.verify_release_candidate(self.snapshot)

    def test_extra_files_empty_directories_and_caches_are_refused(self):
        for name, directory in (("extra.txt", False), ("empty", True), ("__pycache__", True),
                                (".git", True), (".env", False)):
            path = self.snapshot / name
            with self.subTest(name=name):
                path.mkdir() if directory else path.write_text("synthetic extra")
                with self.assertRaises(ValueError):
                    verifier.verify_release_candidate(self.snapshot)
                path.rmdir() if directory else path.unlink()

    def test_manifest_cannot_admit_unreviewed_or_unsafe_paths(self):
        for name in ("unreviewed.txt", "../outside", "/absolute", "docs/.env", "docs/secret.pem"):
            with self.subTest(name=name):
                manifest = json.loads(json.dumps(self.manifest))
                manifest["files"].append({"path": name, "bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()})
                manifest["files"].sort(key=lambda entry: entry["path"])
                self.write_manifest(manifest, recalculate=True)
                with self.assertRaises(ValueError):
                    verifier.verify_release_candidate(self.snapshot)

    def test_symlink_roots_files_directories_and_fifos_are_refused(self):
        alias = self.root / "alias"
        alias.symlink_to(self.snapshot, target_is_directory=True)
        with self.assertRaises(ValueError):
            verifier.verify_release_candidate(alias)
        path = self.snapshot / "extra"
        for kind in ("file", "directory", "fifo"):
            with self.subTest(kind=kind):
                if kind == "fifo":
                    os.mkfifo(path)
                else:
                    path.symlink_to(self.source if kind == "directory" else self.source / "LICENSE")
                with self.assertRaises(ValueError):
                    verifier.verify_release_candidate(self.snapshot)
                path.unlink()

    def test_broken_markdown_targets_anchors_and_escaping_links_are_refused(self):
        for link in ("missing.md", "docs/guide.md#missing", "../outside", "%2e%2e/outside",
                     "/private/file", "file:///private/file", "docs%5cguide.md"):
            with self.subTest(link=link):
                self.replace_readme(f"[Bad]({link})\n")
                with self.assertRaises(ValueError):
                    verifier.verify_release_candidate(self.snapshot)
        self.replace_readme("[Missing][undefined]\n")
        with self.assertRaisesRegex(ValueError, "undefined Markdown reference"):
            verifier.verify_release_candidate(self.snapshot)


if __name__ == "__main__":
    unittest.main()
