#!/usr/bin/env python3
"""Stage a reviewed public source snapshot locally; never publish or copy Git history."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agentstoryline_version import __version__
MANIFEST_NAME = "PUBLIC_SOURCE_MANIFEST.json"

# Exact paths only. Adding a source file requires explicit review here.
PUBLIC_FILES = tuple("""
.gitignore
CHANGELOG.md
CONTRIBUTING.md
LICENSE
README.md
SECURITY.md
THIRD_PARTY_NOTICES.md
adapters/__init__.py
adapters/artex/__init__.py
adapters/artex/coverage.py
adapters/artex/importer.py
adapters/codex/__init__.py
adapters/codex/importer.py
adapters/orcareplay/__init__.py
adapters/orcareplay/bounded_zip.py
adapters/orcareplay/collection.py
adapters/orcareplay/importer.py
adapters/otlp/__init__.py
adapters/otlp/importer.py
adapters/registry.py
agentstoryline.py
agentstoryline_version.py
analysis/__init__.py
analysis/artex_relationships.py
analysis/correlations.py
analysis/explanation_handoff.py
analysis/explanation_profiles.py
analysis/explanation_provider.py
analysis/explanation_service.py
analysis/explanation_store.py
analysis/explanation_workflow.py
analysis/explanations.py
analysis/milestones.py
analysis/narrative.py
analysis/telemetry.py
analysis/workflow.py
contracts/ADAPTER_CAPABILITIES.md
contracts/README.md
contracts/analysis-response.schema.json
contracts/bundle.schema.json
core/__init__.py
core/bundle.py
docs/ARTEX_ORCA_WALKTHROUGH.md
docs/ARTEX_RELATIONSHIPS.md
docs/BRANDING.md
docs/GENERATED_WORKFLOW.md
docs/MILESTONES.md
docs/ORCAREPLAY_ADAPTER.md
docs/ORCA_COLLECTIONS.md
docs/OTLP_ADAPTER.md
docs/PUBLICATION_CHECKLIST.md
docs/PUBLIC_SOURCE_AUDIT.md
docs/RELEASE_COMMANDS.md
docs/RELEASE_NOTES_V0_1.md
docs/RELEASE_NOTES_V0_2.md
docs/RELEASE_SCOPE_V0_1.md
docs/RELEASE_VALIDATION.md
docs/REPLAY.md
docs/STORYLINE.md
docs/TELEMETRY_ATTACHMENTS.md
docs/assets/artex-orca-attach.png
docs/assets/artex-orca-both.png
docs/assets/artex-orca-import.png
docs/assets/artex-orca-model.png
docs/assets/artex-orca-story.png
examples/README.md
examples/artex-local-inspection.tar
examples/artex-local-inspection/database/llm_records.ndjson
examples/artex-local-inspection/files/fixture.json
examples/artex-local-inspection/files/transcripts/exp42-synthetic.jsonl
examples/artex-local-inspection/manifest.json
examples/experimental-codex-rollout.jsonl
examples/experimental-orcareplay.orca.zip
examples/experimental-orcareplay.tar
examples/experimental-orcareplay/blobs/9d/9daa319f27e2e6dbc246390e5f8bef363494624c4dd5a6707f16cd378df76061
examples/experimental-orcareplay/blobs/c3/c34363c33b63eda8daa1d4e491d1e4120317baa0432a9b6fdb77354b9c1d1417
examples/experimental-orcareplay/events.jsonl
examples/experimental-orcareplay/manifest.json
examples/experimental-otlp.json
examples/generate_demos.py
examples/orcatest-1/README.md
examples/orcatest-1/SANITIZATION.json
examples/orcatest-1/artex/OrcaTest-task-1-native.tar.zst
examples/orcatest-1/orca/run_36524fd8f6747fc2712506d0.orca.zip
examples/orcatest-1/orca/run_5ec32a87c10ed8175272bfe3.orca.zip
examples/orcatest-1/orca/run_78e32c79f75c04aba773d673.orca.zip
examples/orcatest-1/orca/run_91e75b3261e20f42ca1f2f44.orca.zip
replay/__init__.py
replay/__main__.py
replay/runner.py
scripts/build_public_source.py
scripts/demo.py
scripts/local_token_target.py
scripts/validate_artex_capture.py
scripts/verify_release_candidate.py
scripts/verify_token_replays.py
scripts/viewer_demo.py
tests/fixtures/analysis/README.md
tests/fixtures/analysis/artex-token-narrative.json
tests/fixtures/artex/token_session/database/llm_records.ndjson
tests/fixtures/artex/token_session/manifest.json
tests/fixtures/replay/session_plan.json
tests/fixtures/storyline/concurrent/events.jsonl
tests/fixtures/storyline/concurrent/manifest.json
tests/test_analysis.py
tests/test_artex_adapter.py
tests/test_artex_archive_content.py
tests/test_artex_coverage.py
tests/test_artex_credential_milestones.py
tests/test_artex_multilingual_archives.py
tests/test_artex_native_traffic.py
tests/test_artex_relationship_projection.py
tests/test_artex_relationship_ui.py
tests/test_artex_relationships.py
tests/test_artifact_frontend.py
tests/test_both_links_ui.py
tests/test_brand_compatibility.py
tests/test_bundle.py
tests/test_cli.py
tests/test_codex_adapter.py
tests/test_compact_chat.py
tests/test_explanation_controls.py
tests/test_explanation_frontend.py
tests/test_explanation_handoff.py
tests/test_explanation_profiles.py
tests/test_explanation_provider.py
tests/test_explanation_service.py
tests/test_explanation_session.py
tests/test_explanation_store.py
tests/test_explanation_workflow.py
tests/test_explanations.py
tests/test_flow.py
tests/test_generic_milestone_ui.py
tests/test_import_control_unlock.py
tests/test_integration.py
tests/test_link_lane_ui.py
tests/test_logical_storyline.py
tests/test_milestone_enrichment.py
tests/test_milestone_rail.py
tests/test_milestone_sharing.py
tests/test_milestones.py
tests/test_offline_explanation_controls.py
tests/test_orca_collection.py
tests/test_orca_collection_flow.py
tests/test_orca_collection_ui.py
tests/test_orca_correlations.py
tests/test_orca_flow_presentation.py
tests/test_orcareplay_adapter.py
tests/test_orcareplay_examples.py
tests/test_orcareplay_integration.py
tests/test_original_artifacts.py
tests/test_original_artifacts_http.py
tests/test_otlp_adapter.py
tests/test_pacing.py
tests/test_public_source.py
tests/test_recording.py
tests/test_release_candidate.py
tests/test_release_examples.py
tests/test_replay.py
tests/test_replay_outcomes.py
tests/test_replay_view.py
tests/test_sharing.py
tests/test_sharing_service.py
tests/test_story_axis_frontend.py
tests/test_storyline.py
tests/test_storyline_cursor_motion.py
tests/test_storyline_markers.py
tests/test_storyline_scrub.py
tests/test_telemetry_correlations.py
tests/test_telemetry_lane_owners.py
tests/test_telemetry_matching.py
tests/test_telemetry_projection.py
tests/test_telemetry_service.py
tests/test_telemetry_ui.py
tests/test_time_axis.py
tests/test_viewer.py
tests/test_viewer_identity.py
tests/test_viewer_import.py
tests/test_viewer_import_http.py
tests/test_viewer_import_temp_paths.py
tests/test_viewer_v1_controls.py
tests/test_workflow.py
traceforge.py
web/__init__.py
web/artifacts.py
web/assets/sleepy-replicant-128.png
web/assets/sleepy-replicant-32.png
web/assets/sleepy-replicant-512.png
web/assets/sleepy-replicant-master.png
web/branding.py
web/flow.css
web/flow.html
web/flow.js
web/flow.py
web/pacing.py
web/recording.py
web/render.py
web/replay_view.py
web/sharing.py
web/storyline.css
web/storyline.js
web/storyline.py
web/telemetry.py
web/theme.py
web/time_axis.py
web/timeline.html
web/viewer_import.py
""".split())

# Release media is independently reviewable and may be prepared after the source.
# Missing media is reported, never replaced by a wildcard/directory copy.
OPTIONAL_FILES = (
    "docs/assets/agentstoryline-overview.png",
    "docs/assets/agentstoryline-chat.png",
    "docs/assets/AgentStorylineMusic.mov",
)
FORBIDDEN_PARTS = frozenset({
    ".git", ".aws", ".ssh", ".codex", ".agents", ".venv", "node_modules",
    "__pycache__", "work", "recordings", "integrations",
})
FORBIDDEN_NAMES = frozenset({
    "AGENTS.md", "HANDOFF.md", "IMPLEMENTATION_BRIEF.md",
    "PYTHON_FIRST_VERIFICATION_PLAN.md", "RELEASE_COMPATIBILITY_CHECKPOINT.md",
    "artex-baseline.json", "artex.lock.json",
})


def _json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _no_symlinks(path):
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError(f"symlink paths are not allowed: {path}")


def _checked_paths():
    paths = PUBLIC_FILES + OPTIONAL_FILES
    if len(set(paths)) != len(paths):
        raise ValueError("duplicate allowlist path")
    if not {"LICENSE", "THIRD_PARTY_NOTICES.md"}.issubset(PUBLIC_FILES):
        raise ValueError("license and notices are required")
    for name in paths:
        path = PurePosixPath(name)
        if (path.is_absolute() or path.as_posix() != name or ".." in path.parts
                or "\\" in name or name == MANIFEST_NAME
                or any(part in FORBIDDEN_PARTS for part in path.parts)
                or path.name in FORBIDDEN_NAMES
                or path.name.startswith(".env") or "mixkit" in name.lower()
                or path.suffix.lower() in {".key", ".pem", ".pyc", ".pyo", ".wav"}):
            raise ValueError(f"unsafe allowlist path: {name}")
    return sorted(paths)


def build_public_source(destination, *, source_root=ROOT):
    """Copy exact reviewed paths to a new directory and return its content manifest.

    Input/output paths must have no symlink components; use /private/tmp on macOS.
    Existing destinations, including empty directories, are always refused.
    This is a local snapshot tool, not a defense against hostile concurrent writers.
    """
    source = Path(source_root).absolute()
    target = Path(destination).absolute()
    _no_symlinks(source)
    _no_symlinks(target)
    if not source.is_dir():
        raise ValueError("source root must be a directory")
    source, target = source.resolve(), target.resolve()
    if target == source or source in target.parents or target in source.parents:
        raise ValueError("destination must be outside and must not contain the source tree")
    if target.exists():
        raise ValueError("destination must not exist; existing contents are never overwritten")
    if not target.parent.is_dir():
        raise ValueError("destination parent must already exist")

    payloads, entries, omitted = {}, [], []
    for name in _checked_paths():
        path = source / name
        _no_symlinks(path)
        if name in OPTIONAL_FILES and not path.exists():
            omitted.append(name)
            continue
        # Refuse special files before opening: opening a FIFO could block.
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError(f"allowlisted source must be a regular file: {name}")
        with path.open("rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError(f"allowlisted source must be a regular file: {name}")
            raw = stream.read()
        payloads[name] = raw
        entries.append({"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    manifest = {
        "format": "agentstoryline-public-source", "version": 1,
        "release_version": __version__,
        "files": entries, "file_count": len(entries),
        "total_bytes": sum(item["bytes"] for item in entries),
        "content_sha256": hashlib.sha256(_json_bytes(entries)).hexdigest(),
        "omitted_optional_files": omitted,
        "scope": "Reviewed current working files only; no Git history or publication.",
    }
    # Reserve output only after every source file has passed validation and read.
    target.mkdir(mode=0o755, exist_ok=False)
    for name, raw in {**payloads, MANIFEST_NAME: _json_bytes(manifest)}.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
        path.chmod(0o644)
        os.utime(path, (0, 0))
    for directory in sorted((p for p in target.rglob("*") if p.is_dir()), reverse=True):
        directory.chmod(0o755)
        os.utime(directory, (0, 0))
    target.chmod(0o755)
    os.utime(target, (0, 0))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="New directory outside this checkout")
    args = parser.parse_args()
    try:
        manifest = build_public_source(args.destination)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Public source staging failed: {exc}\n")
    print(json.dumps({
        "destination": str(args.destination.absolute()),
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
        "content_sha256": manifest["content_sha256"],
        "omitted_optional_files": manifest["omitted_optional_files"],
    }, indent=2))


if __name__ == "__main__":
    main()
