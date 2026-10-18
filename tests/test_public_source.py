"""Synthetic checks for the explicit public source snapshot boundary."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import build_public_source as builder


class PublicSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        self.paths = ("LICENSE", "THIRD_PARTY_NOTICES.md", "README.md", "agentstoryline_version.py")
        for name in self.paths:
            (self.source / name).write_text("Synthetic " + name + "\n")
        (self.source / "agentstoryline_version.py").write_text(f'__version__ = "{builder.__version__}"\n')
        self.patch_required = patch.object(builder, "PUBLIC_FILES", self.paths)
        self.patch_optional = patch.object(builder, "OPTIONAL_FILES", ("docs/assets/demo.png",))
        self.patch_required.start()
        self.patch_optional.start()
        self.addCleanup(self.patch_required.stop)
        self.addCleanup(self.patch_optional.stop)
        self.addCleanup(self.temporary.cleanup)

    def build(self, name="output"):
        destination = self.root / name
        return destination, builder.build_public_source(destination, source_root=self.source)

    def test_exact_allowlist_determinism_hashes_and_metadata(self):
        for name in ("work/private.json", ".env", "docs/HANDOFF.md",
                     "mixkit-long-pop-2358.wav", "recordings/private.mp4",
                     "unreviewed.py", ".git/config",
                     "upstream-orcareplay/examples/traces/run_synthetic/events.jsonl"):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("SYNTHETIC-EXCLUSION-CANARY")
        first, manifest = self.build("first")
        second, other = self.build("second")
        self.assertEqual(manifest, other)
        self.assertEqual(manifest["release_version"], builder.__version__)
        self.assertEqual(manifest["omitted_optional_files"], ["docs/assets/demo.png"])
        self.assertEqual(manifest["file_count"], len(self.paths))
        files = sorted(p.relative_to(first).as_posix() for p in first.rglob("*") if p.is_file())
        self.assertEqual(files, sorted((*self.paths, builder.MANIFEST_NAME)))
        for entry in manifest["files"]:
            raw = (first / entry["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), entry["sha256"])
            self.assertEqual(len(raw), entry["bytes"])
        for name in files:
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
            self.assertEqual((first / name).stat().st_mtime, 0)
            self.assertEqual((first / name).stat().st_mode & 0o777, 0o644)
        self.assertEqual(json.loads((first / builder.MANIFEST_NAME).read_bytes()), manifest)

    def test_release_material_and_version_bytes_are_preserved_without_extra_drafts(self):
        release_paths = (
            "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md",
            "docs/RELEASE_NOTES_V0_1.md", "docs/PUBLICATION_CHECKLIST.md",
            "docs/RELEASE_COMMANDS.md", "scripts/verify_release_candidate.py",
            "tests/test_release_candidate.py", "docs/ORCAREPLAY_ADAPTER.md",
            "adapters/orcareplay/__init__.py", "adapters/orcareplay/importer.py",
            "adapters/orcareplay/bounded_zip.py",
            "examples/experimental-orcareplay/manifest.json",
            "examples/experimental-orcareplay/events.jsonl",
            "examples/experimental-orcareplay.tar", "examples/experimental-orcareplay.orca.zip", "tests/test_orcareplay_examples.py",
        )
        for name in release_paths:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Newly authored synthetic release material: " + name + "\n")
        (self.source / "docs/unreviewed-release-draft.md").write_text("SYNTHETIC-EXCLUSION-CANARY")
        version = "99.0.0-synthetic"
        version_raw = f'__version__ = "{version}"\n'.encode()
        (self.source / "agentstoryline_version.py").write_bytes(version_raw)
        with patch.object(builder, "PUBLIC_FILES", self.paths + release_paths), \
                patch.object(builder, "__version__", version):
            destination, manifest = self.build()
        self.assertEqual(manifest["release_version"], version)
        self.assertEqual(manifest["file_count"], len(self.paths) + len(release_paths))
        self.assertEqual((destination / "agentstoryline_version.py").read_bytes(), version_raw)
        for name in release_paths:
            self.assertEqual((destination / name).read_bytes(), (self.source / name).read_bytes())
        self.assertFalse((destination / "docs/unreviewed-release-draft.md").exists())

    def test_optional_media_is_exactly_named_and_hashed(self):
        media = self.source / "docs/assets"
        media.mkdir(parents=True)
        (media / "demo.png").write_bytes(b"synthetic-reviewed-image")
        (media / "private.png").write_bytes(b"synthetic-private-image")
        destination, manifest = self.build()
        self.assertEqual(manifest["omitted_optional_files"], [])
        self.assertEqual((destination / "docs/assets/demo.png").read_bytes(), b"synthetic-reviewed-image")
        self.assertFalse((destination / "docs/assets/private.png").exists())

    def test_existing_outputs_are_preserved_even_when_empty(self):
        for kind in ("empty", "nonempty", "file"):
            destination = self.root / kind
            if kind == "file":
                destination.write_text("retain")
            else:
                destination.mkdir()
                if kind == "nonempty":
                    (destination / "canary").write_text("retain")
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                builder.build_public_source(destination, source_root=self.source)
        self.assertEqual((self.root / "nonempty/canary").read_text(), "retain")
        self.assertEqual((self.root / "file").read_text(), "retain")

    def test_source_and_related_destinations_are_refused(self):
        for destination in (self.source, self.source / "output", self.root):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                builder.build_public_source(destination, source_root=self.source)

    def test_symlink_sources_destinations_and_parent_components_are_refused(self):
        original = self.source / "README.md"
        original.unlink()
        original.symlink_to(self.source / "LICENSE")
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse((self.root / "output").exists())
        original.unlink()
        original.write_text("Synthetic README")
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            builder.build_public_source(alias / "output", source_root=self.source)

    def test_missing_or_special_source_fails_before_output_creation(self):
        readme = self.source / "README.md"
        readme.unlink()
        with self.assertRaises(OSError):
            self.build()
        self.assertFalse((self.root / "output").exists())
        os.mkfifo(readme)
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse((self.root / "output").exists())

    def test_allowlist_cannot_omit_notices_or_admit_prohibited_paths(self):
        for name in ("../private", "/private/file", "work/private.json",
                     ".env", "docs/HANDOFF.md", "integrations/artex.lock.json",
                     "assets/mixkit-pop.wav", ".git/config", "secret.pem"):
            with self.subTest(name=name), patch.object(builder, "PUBLIC_FILES", self.paths + (name,)):
                with self.assertRaises(ValueError):
                    self.build()
        with patch.object(builder, "PUBLIC_FILES", ("README.md",)):
            with self.assertRaisesRegex(ValueError, "license and notices"):
                self.build()


if __name__ == "__main__":
    unittest.main()
