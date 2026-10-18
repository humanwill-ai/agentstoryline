"""Validate AgentStoryline v0.1 recording directories without executing evidence."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

VERSION = "0.1"
EVENT_KINDS = frozenset({"model.request", "model.response", "tool.start", "tool.end", "run.start", "run.end", "observation"})
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


class BundleValidationError(ValueError):
    """An input recording violates the bundle contract."""


def _fail(where: str, message: str) -> None:
    raise BundleValidationError(f"{where}: {message}")


def _object(value: Any, where: str) -> dict:
    if not isinstance(value, dict):
        _fail(where, "must be an object")
    return value


def _identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(c.isspace() or not c.isprintable() for c in value):
        _fail(where, "must be a nonempty identifier without whitespace or control characters")
    return value


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(where, "must be a nonempty string")
    return value


def _timestamp(value: Any, where: str) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        _fail(where, "must be an RFC3339 timestamp with timezone")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(where, "invalid RFC3339 timestamp")


def _keys(value: dict, allowed: set[str], where: str) -> None:
    extras = set(value) - allowed
    if extras:
        _fail(where, f"unknown fields {sorted(extras)}; put additional fields in extensions")
    if "extensions" in value:
        _object(value["extensions"], where + ".extensions")


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise BundleValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(text: str, where: str = "JSON") -> Any:
    """Strict JSON reader shared with analysis imports."""
    def invalid_constant(value: str) -> None:
        _fail(where, f"non-JSON numeric constant {value}")
    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            _fail(where, "numeric value exceeds finite floating-point representation")
        return number
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=invalid_constant, parse_float=finite_float)
    except (ValueError, RecursionError) as exc:
        raise BundleValidationError(f"{where}: {exc}") from exc


def _regular(path: Path, where: str) -> None:
    if path.is_symlink() or not path.is_file():
        _fail(where, "must be an existing regular file, not a symlink")


def _open_regular_in_root(root: Path, parts: list[str], where: str):
    """Open under a pinned directory without following path-component symlinks."""
    directory_fd = None
    file_fd = None
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow
    try:
        directory_fd = os.open(root, directory_flags)
        for part in parts[:-1]:
            child_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child_fd
        file_fd = os.open(parts[-1], os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0), dir_fd=directory_fd)
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            _fail(where, "must be a regular file")
        stream = os.fdopen(file_fd, "rb")
        file_fd = None  # the returned stream owns this descriptor
        return stream
    except OSError as exc:
        raise BundleValidationError(f"{where}: cannot open regular file without following symlinks") from exc
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        if file_fd is not None:
            os.close(file_fd)


def _artifact(ref: Any, root: Path, where: str) -> None:
    ref = _object(ref, where)
    path = _string(ref.get("path"), where + ".path")
    digest = ref.get("sha256")
    if not isinstance(digest, str) or not _HASH.fullmatch(digest):
        _fail(where + ".sha256", "must be a lowercase 64-character SHA-256")
    parts = path.split("/")
    if "\\" in path or ":" in path or PurePosixPath(path).is_absolute() or len(parts) < 2 or parts[0] != "artifacts" or any(p in {"", ".", ".."} for p in parts):
        _fail(where + ".path", "must be a relative path within artifacts/ without traversal")
    target = root
    for part in parts:
        target = target / part
        if target.is_symlink():
            _fail(where + ".path", "symlinks are forbidden")
    _regular(target, where + ".path")
    hasher = hashlib.sha256()
    with _open_regular_in_root(root, parts, where + ".path") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    if hasher.hexdigest() != digest:
        _fail(where, "artifact SHA-256 mismatch")


def _artifact_refs(envelope: dict, root: Path | None, where: str) -> None:
    # Only reserved envelope fields denote artifacts. Raw tool values and
    # extensions are never recursively interpreted as filesystem instructions.
    if "artifact_refs" not in envelope:
        return
    refs = envelope["artifact_refs"]
    if not isinstance(refs, list):
        _fail(where + ".artifact_refs", "must be a list")
    if refs and root is None:
        _fail(where, "artifact references require artifact_root for checksum validation")
    for index, ref in enumerate(refs):
        _artifact(ref, root, f"{where}.artifact_refs[{index}]")


def validate_bundle(bundle: Any, *, artifact_root: str | Path | None = None) -> dict:
    """Validate structured records; artifact references require a directory root."""
    bundle = _object(bundle, "bundle")
    manifest = _object(bundle.get("manifest"), "manifest")
    _keys(manifest, {"schema_version", "run_id", "agent", "started_at", "completeness", "source", "extensions", "artifact_refs"}, "manifest")
    if manifest.get("schema_version") != VERSION:
        _fail("manifest.schema_version", "unsupported version")
    run_id = _identifier(manifest.get("run_id"), "manifest.run_id")
    _timestamp(manifest.get("started_at"), "manifest.started_at")
    agent = _object(manifest.get("agent"), "manifest.agent")
    _keys(agent, {"name", "version", "extensions"}, "manifest.agent")
    for field in ("name", "version"):
        _string(agent.get(field), f"manifest.agent.{field}")
    completeness = _object(manifest.get("completeness"), "manifest.completeness")
    _keys(completeness, {"status", "gaps", "extensions"}, "manifest.completeness")
    status, gaps = completeness.get("status"), completeness.get("gaps")
    if not isinstance(status, str) or status not in {"complete", "partial"} or not isinstance(gaps, list):
        _fail("manifest.completeness", "requires status complete|partial and a list of gaps")
    for index, gap in enumerate(gaps):
        _string(gap, f"manifest.completeness.gaps[{index}]")
    if (status == "partial" and not gaps) or (status == "complete" and gaps):
        _fail("manifest.completeness", "partial recordings require gaps; complete recordings cannot declare gaps")
    events = bundle.get("events")
    if not isinstance(events, list):
        _fail("events", "must be a list")
    if status == "complete" and not events:
        _fail("manifest.completeness", "an empty recording must be partial with explicit gaps")
    seen: set[str] = set()
    previous_sequence = -1
    for index, event in enumerate(events):
        where = f"events[{index}]"
        event = _object(event, where)
        _keys(event, {"schema_version", "id", "run_id", "kind", "timestamp", "sequence", "parent_id", "call_id", "tool", "data", "evidence_refs", "dependencies", "extensions", "artifact_refs"}, where)
        if event.get("schema_version") != VERSION:
            _fail(where + ".schema_version", "unsupported version")
        event_id = _identifier(event.get("id"), where + ".id")
        if event_id in seen:
            _fail(where + ".id", "duplicate event ID")
        if event.get("run_id") != run_id:
            _fail(where + ".run_id", "must match manifest run_id")
        if not isinstance(event.get("kind"), str) or event.get("kind") not in EVENT_KINDS:
            _fail(where + ".kind", "unsupported event kind")
        _timestamp(event.get("timestamp"), where + ".timestamp")
        sequence = event.get("sequence")
        if type(sequence) is not int or sequence < 0 or sequence <= previous_sequence:
            _fail(where + ".sequence", "must be a strictly increasing nonnegative integer")
        if "data" in event:
            _object(event["data"], where + ".data")
        for field in ("call_id", "tool"):
            if field in event:
                _string(event[field], where + "." + field)
        refs = event.get("evidence_refs", [])
        if not isinstance(refs, list) or len(refs) != len(set(r for r in refs if isinstance(r, str))):
            _fail(where + ".evidence_refs", "must be a list of unique event IDs")
        links = list(refs)
        if "parent_id" in event:
            links.append(event["parent_id"])
        dependencies = event.get("dependencies", [])
        if not isinstance(dependencies, list):
            _fail(where + ".dependencies", "must be a list")
        dependency_ids = set()
        for dep_index, dependency in enumerate(dependencies):
            dep_where = f"{where}.dependencies[{dep_index}]"
            dependency = _object(dependency, dep_where)
            _keys(dependency, {"event_id", "provenance"}, dep_where)
            dep_id = _identifier(dependency.get("event_id"), dep_where + ".event_id")
            if dep_id in dependency_ids:
                _fail(dep_where, "duplicate dependency")
            dependency_ids.add(dep_id)
            if not isinstance(dependency.get("provenance"), str) or dependency.get("provenance") not in {"recorded", "inferred"}:
                _fail(dep_where + ".provenance", "must be recorded or inferred")
            links.append(dep_id)
        for link in links:
            _identifier(link, where + ".reference")
            if link not in seen:
                _fail(where + ".reference", f"unknown or nonpreceding event ID {link!r}")
        seen.add(event_id)
        previous_sequence = sequence
    root = None if artifact_root is None else Path(artifact_root)
    _artifact_refs(manifest, root, "manifest")
    for index, event in enumerate(events):
        _artifact_refs(event, root, f"events[{index}]")
    return {"manifest": manifest, "events": events}


def load_bundle(path: str | Path) -> dict:
    """Load and validate manifest.json/events.jsonl and referenced artifacts.

    Does not execute tools, replay requests, or contact a model/provider.
    """
    root = Path(path).absolute()
    if root.is_symlink() or not root.is_dir():
        _fail("bundle", "must be an existing directory, not a symlink")
    manifest_path, events_path = root / "manifest.json", root / "events.jsonl"
    _regular(manifest_path, "manifest.json")
    _regular(events_path, "events.jsonl")
    try:
        with _open_regular_in_root(root, ["manifest.json"], "manifest.json") as stream:
            manifest = read_json(stream.read().decode("utf-8"), "manifest.json")
        events = []
        with _open_regular_in_root(root, ["events.jsonl"], "events.jsonl") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    _fail(f"events.jsonl:{line_number}", "blank records are forbidden")
                events.append(read_json(line.decode("utf-8"), f"events.jsonl:{line_number}"))
        return validate_bundle({"manifest": manifest, "events": events}, artifact_root=root)
    except (OSError, UnicodeError, RecursionError) as exc:
        raise BundleValidationError(f"bundle read failed: {exc}") from exc
