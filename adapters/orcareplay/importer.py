"""Bounded Orca Trace import; no source operations are executed or restored.

Observed Orca schemas 0.1.0 and 0.3.0 were inspected at upstream
a9178ca26af95cfd3231b8eb2da4ae11dbbd0a84. This is deliberately a partial
observation profile, not an Orca replay engine or full schema implementation.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tarfile
import tempfile

from core.bundle import BundleValidationError, load_bundle, read_json
from .bounded_zip import BoundedZipError, entries as zip_entries

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_FILES = 10000
MAX_RECORDS = 50000
MAX_LINE_BYTES = 4 * 1024 * 1024
MAX_PATH_DEPTH = 32
MAX_ZIP_DIRECTORY_BYTES = 16 * 1024 * 1024
SCHEMA_VERSIONS = frozenset({"0.1.0", "0.3.0"})
SOURCE_REVISION = "a9178ca26af95cfd3231b8eb2da4ae11dbbd0a84"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_RUN = re.compile(r"^run_[0-9a-f]{6,32}$")
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$")


class OrcaReplayImportError(ValueError):
    """Malformed, corrupt, unsafe, oversized, or unsupported source recording."""


class _BoundedArchiveStream:
    """Bound reads before tarfile can process hidden PAX/GNU metadata."""

    def __init__(self, stream):
        self.stream = stream
        self.remaining = MAX_ARCHIVE_BYTES

    def read(self, size=-1):
        amount = self.remaining + 1 if size < 0 else min(size, self.remaining + 1)
        raw = self.stream.read(amount)
        if len(raw) > self.remaining:
            raise OrcaReplayImportError("archive exceeds read byte budget")
        self.remaining -= len(raw)
        return raw


def _bounded_tarinfo():
    # Each import owns its counters, including concurrent GUI imports. Hidden
    # extension records are parsed before iteration yields ordinary members.
    class BoundedTarInfo(tarfile.TarInfo):
        headers = 0

        @classmethod
        def frombuf(cls, buf, encoding, errors):
            item = super().frombuf(buf, encoding, errors)
            cls.headers += 1
            if cls.headers > MAX_FILES:
                raise OrcaReplayImportError("archive header count limit exceeded")
            if item.size < 0 or item.size > MAX_FILE_BYTES:
                raise OrcaReplayImportError("archive header exceeds member byte limit")
            if item.type in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK) and item.size > MAX_MANIFEST_BYTES:
                raise OrcaReplayImportError("archive metadata exceeds byte limit")
            return item

    return BoundedTarInfo


def looks_like_orcareplay_manifest(value):
    """Conservative discriminator, not validation or authenticated provenance."""
    return (isinstance(value, dict)
            and isinstance(value.get("schema_version"), str)
            and value["schema_version"] in SCHEMA_VERSIONS
            and isinstance(value.get("run_id"), str)
            and _RUN.fullmatch(value["run_id"]) is not None
            and isinstance(value.get("orca_version"), str) and bool(value["orca_version"].strip())
            and isinstance(value.get("adapter"), dict)
            and isinstance(value["adapter"].get("id"), str) and bool(value["adapter"]["id"].strip()))


def _safe_ancestors(path):
    if any(current.is_symlink() for current in (path, *path.parents)):
        raise OrcaReplayImportError("symlinks in source/destination paths are forbidden")


def _open_path(path, *, directory=False):
    """Pin every ancestor instead of trusting a prior symlink check."""
    descriptor = os.open(path.anchor, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for index, part in enumerate(path.parts[1:]):
            is_directory = directory or index < len(path.parts[1:]) - 1
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            if is_directory:
                flags |= getattr(os, "O_DIRECTORY", 0)
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _parts(name):
    if not isinstance(name, str) or len(name) > 1024 or "\\" in name or ":" in name or name.startswith("/"):
        raise OrcaReplayImportError("unsafe source member path")
    # Common tar writers emit a harmless leading './' and directory trailing '/'.
    if name.startswith("./"):
        name = name[2:]
    name = name.rstrip("/")
    if not name or any(part in ("", ".", "..") for part in name.split("/")) or len(name.split("/")) > MAX_PATH_DEPTH:
        raise OrcaReplayImportError("unsafe source member path or path depth limit")
    return name


def _source_files(source):
    """Return retained bytes plus inventories; never extract an archive to disk."""
    source = Path(source).absolute()
    _safe_ancestors(source)
    files, omitted, seen = {}, [], set()
    total, count = 0, 0
    directory_count = 0

    def member(name, size, get_bytes, *, snapshot=False, verify_snapshot=False):
        nonlocal total, count
        name = _parts(name)
        if name in seen:
            raise OrcaReplayImportError("duplicate source member path")
        seen.add(name)
        count += 1
        total += size
        if count + directory_count > MAX_FILES or total > MAX_TOTAL_BYTES or size > MAX_FILE_BYTES:
            raise OrcaReplayImportError("source exceeds file/count/total byte limits")
        if snapshot:
            if verify_snapshot and len(get_bytes()) != size:
                raise OrcaReplayImportError("snapshot source member is truncated")
            omitted.append({"path": name, "bytes": size, "status": "snapshot_body_not_imported"})
            return
        raw = get_bytes()
        if len(raw) != size:
            raise OrcaReplayImportError("source member changed size or is truncated")
        files[name] = raw

    try:
        if source.is_dir():
            root_fd = _open_path(source, directory=True)
            def walk(fd, prefix=""):
                nonlocal directory_count
                for name in sorted(os.listdir(fd)):
                    relative = _parts(prefix + name)
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        directory_count += 1
                        if directory_count + count > MAX_FILES:
                            raise OrcaReplayImportError("source exceeds member count limit")
                        child = os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=fd)
                        try:
                            walk(child, relative + "/")
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(info.st_mode):
                        def read():
                            child = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), dir_fd=fd)
                            with os.fdopen(child, "rb") as stream:
                                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                                    raise OrcaReplayImportError("source member is not regular")
                                return stream.read(MAX_FILE_BYTES + 1)
                        member(relative, info.st_size, read, snapshot=relative.startswith("fs/"))
                    else:
                        raise OrcaReplayImportError("source links and special files are forbidden")
            try:
                walk(root_fd)
            finally:
                os.close(root_fd)
        else:
            descriptor = _open_path(source)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARCHIVE_BYTES:
                    raise OrcaReplayImportError("archive must be a bounded regular ZIP or plain tar file")
                signature = stream.read(4)
                stream.seek(0)
                if signature.startswith(b"PK"):
                    for name, size, directory, read in zip_entries(
                            stream, size=info.st_size, max_files=MAX_FILES,
                            max_file_bytes=MAX_FILE_BYTES, max_total_bytes=MAX_TOTAL_BYTES,
                            max_directory_bytes=MAX_ZIP_DIRECTORY_BYTES, normalize=_parts):
                        if directory:
                            read()  # Empty directory streams still need CRC/size validation.
                            directory_count += 1
                            seen.add(name)
                            continue
                        snapshot = name.startswith("fs/") or (name.count("/") >= 2 and name.split("/")[1] == "fs")
                        member(name, size, read, snapshot=snapshot, verify_snapshot=True)
                else:
                    _read_tar(stream, member)
            manifests = [name for name in files if name == "manifest.json" or (name.endswith("/manifest.json") and name.count("/") == 1)]
            if len(manifests) != 1:
                raise OrcaReplayImportError("archive needs exactly one root or single-prefix manifest.json")
            prefix = manifests[0][:-len("manifest.json")]
            if prefix and any(not name.startswith(prefix) and name != prefix[:-1] for name in seen):
                raise OrcaReplayImportError("archive members must share one run directory")
            files = {name[len(prefix):]: raw for name, raw in files.items()}
            omitted = [{**item, "path": item["path"][len(prefix):]} for item in omitted]
        return files, sorted(omitted, key=lambda item: item["path"])
    except (OSError, tarfile.TarError, BoundedZipError, EOFError, RecursionError) as exc:
        raise OrcaReplayImportError(f"cannot read safe bounded source: {exc}") from exc


def _read_tar(stream, member):
    """Preserve the existing bounded streaming plain-tar reader."""
    with tarfile.open(fileobj=_BoundedArchiveStream(stream), mode="r|", tarinfo=_bounded_tarinfo()) as archive:
        names = set()
        entries = 0
        # Streaming iteration bounds even directory-only archives.
        for entry in archive:
            entries += 1
            if entries > MAX_FILES:
                raise OrcaReplayImportError("archive exceeds member count limit")
            if entry.name in (".", "./") and entry.isdir():
                continue
            name = _parts(entry.name)
            if name in names:
                raise OrcaReplayImportError("duplicate archive member path")
            names.add(name)
            if entry.isdir():
                continue
            if not entry.isfile() or entry.issparse() or entry.size < 0:
                raise OrcaReplayImportError("archive links, sparse and special members are forbidden")
            def read():
                extracted = archive.extractfile(entry)
                if extracted is None:
                    raise OrcaReplayImportError("unreadable archive member")
                with extracted:
                    return extracted.read(MAX_FILE_BYTES + 1)
            # Prefix is not known until manifest discovery; exclude any
            # path segment named fs here, then normalize below.
            snapshot = name.startswith("fs/") or (name.count("/") >= 2 and name.split("/")[1] == "fs")
            member(name, entry.size, read, snapshot=snapshot)


def read_source_files(source):
    """Read bounded directory/ZIP/plain tar members for conservative UI detection.

    Returned paths are normalized to the single manifest root. ``fs/`` snapshot
    bodies are deliberately omitted. No content is executed, unpacked or restored.
    Import performs the full Orca validation separately.
    """
    return _source_files(source)[0]


def _json(raw, where):
    try:
        return read_json(raw.decode("utf-8"), where)
    except (BundleValidationError, UnicodeError) as exc:
        raise OrcaReplayImportError(str(exc)) from exc


def _time(value):
    if not isinstance(value, str) or not _TIME.fullmatch(value):
        raise OrcaReplayImportError("source timestamp must be RFC3339 with timezone")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError as exc:
        raise OrcaReplayImportError("invalid source timestamp") from exc


def _nonnegative(value, field):
    if type(value) is not int or value < 0:
        raise OrcaReplayImportError(f"{field} must be a nonnegative integer")
    return value


def _payload(event, files, gaps):
    value = event.get("payload")
    media = None
    if isinstance(value, dict) and "$blob" in value:
        ref = value["$blob"]
        if not isinstance(ref, str) or not ref.startswith("sha256:") or not _HASH.fullmatch(ref[7:]):
            raise OrcaReplayImportError("invalid payload $blob digest")
        size = _nonnegative(value.get("bytes"), "blob bytes")
        media = value.get("media_type")
        if set(value) - {"$blob", "bytes", "media_type"}:
            raise OrcaReplayImportError("malformed blob reference fields")
        if media is not None and not isinstance(media, str):
            raise OrcaReplayImportError("blob media_type must be a string")
        name = f"blobs/{ref[7:9]}/{ref[7:]}"
        if name not in files:
            raise OrcaReplayImportError("missing referenced blob: " + name)
        raw = files[name]
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != ref[7:]:
            raise OrcaReplayImportError("referenced blob size/SHA-256 mismatch")
        try:
            value = raw.decode("utf-8")
        except UnicodeError:
            gaps.add("Binary blob content is retained as an artifact, without a text preview.")
            return {"artifact_path": "artifacts/orcareplay/files/" + name,
                    "bytes": size, "media_type": media, "content_status": "binary_artifact_only"}
    if isinstance(value, str):
        if media == "application/json" or media and media.endswith("+json"):
            return _json(value.encode("utf-8"), "source JSON payload")
        # Wire model bodies are often JSON encoded inside an inline JSON string.
        # Arbitrary text/code beginning with a brace is still legitimate text.
        if media is None and value.lstrip().startswith(("{", "[")):
            try:
                return _json(value.encode("utf-8"), "source JSON payload")
            except OrcaReplayImportError:
                gaps.add("An undeclared opaque payload resembles JSON but could not be decoded strictly; original text is retained without structured interpretation.")
    return value


def _response(value):
    if not isinstance(value, dict) or not isinstance(value.get("choices"), list):
        return value
    blocks = []
    for choice in value["choices"]:
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            continue
        message = choice["message"]
        content = message.get("content")
        if isinstance(content, str):
            blocks.append({"type": "text", "text": content})
        elif isinstance(content, list):
            blocks.extend(content)
        for call in message.get("tool_calls", []) if isinstance(message.get("tool_calls"), list) else []:
            if isinstance(call, dict) and isinstance(call.get("function"), dict):
                function = call["function"]
                blocks.append({"type": "tool_use", "id": call.get("id"), "name": function.get("name"),
                               "input": function.get("arguments"), "execution_status": "proposal_only_not_execution"})
    return {"content": blocks} if blocks else value


def _convert(files, omitted):
    for required in ("manifest.json", "events.jsonl"):
        if required not in files:
            raise OrcaReplayImportError("missing required " + required)
    if len(files["manifest.json"]) > MAX_MANIFEST_BYTES:
        raise OrcaReplayImportError("manifest exceeds byte limit")
    native = _json(files["manifest.json"], "Orca manifest")
    if not looks_like_orcareplay_manifest(native):
        raise OrcaReplayImportError("unsupported Orca manifest/version")
    created = _time(native.get("created_at"))
    integrity = native.get("integrity")
    digest = integrity.get("events_sha256") if isinstance(integrity, dict) else None
    if not isinstance(digest, str) or not _HASH.fullmatch(digest):
        raise OrcaReplayImportError("manifest requires events_sha256 integrity digest")
    raw = files["events.jsonl"]
    if hashlib.sha256(raw).hexdigest() != digest:
        raise OrcaReplayImportError("events.jsonl SHA-256 mismatch")
    for name, content in files.items():
        if name.startswith("blobs/"):
            parts = name.split("/")
            if len(parts) != 3 or not _HASH.fullmatch(parts[-1]) or parts[1] != parts[-1][:2]:
                raise OrcaReplayImportError("invalid blob store path")
            if hashlib.sha256(content).hexdigest() != parts[-1]:
                raise OrcaReplayImportError("blob store SHA-256 mismatch")
        elif name.endswith(".json") and name != "manifest.json":
            _json(content, name)
    gaps = {
        "Experimental read-only Orca import; supplied source provenance, capture completeness and redaction effectiveness are not independently attested.",
        "Filesystem snapshot bodies under fs/ are excluded; snapshot metadata and referenced diff blobs are retained. No filesystem restore or Git operation is performed.",
        "Source causes are recorder-declared relationships, not proven dataflow; no canonical dependencies are inferred from them.",
        "tool.call records can be model protocol proposals, not execution. tool.result is source-reported content, not independent proof of execution or success.",
        "Canonical timestamps use created_at plus authoritative mono_us; original informational wall-clock timestamps are retained in source_record.",
        "Model request history remains attached context, not additional invented exchanges; opaque or missing payload content cannot be reconstructed.",
    }
    rows = []
    previous_mono = -1
    tail_truncated = False
    lines = raw.splitlines(keepends=True)
    if len(lines) > MAX_RECORDS:
        raise OrcaReplayImportError("event record count limit exceeded")
    for index, line in enumerate(lines):
        if len(line) > MAX_LINE_BYTES:
            raise OrcaReplayImportError("event line byte limit exceeded")
        if not line.strip():
            raise OrcaReplayImportError("blank event records are forbidden")
        try:
            record = _json(line, f"events.jsonl:{index + 1}")
        except OrcaReplayImportError as exc:
            if index == len(lines) - 1 and not line.endswith(b"\n"):
                # Only a genuine incomplete JSON/UTF8 final line is tolerated;
                # duplicate keys/nonfinite numbers still indicate unsafe JSON.
                text = str(exc)
                if "duplicate JSON key" in text or "numeric" in text:
                    raise
                tail_truncated = True
                gaps.add("A truncated unterminated final JSONL line was not mapped; its exact bytes remain in the event-log artifact.")
                break
            raise
        if not isinstance(record, dict):
            raise OrcaReplayImportError("event must be an object")
        if _nonnegative(record.get("seq"), "seq") != index:
            raise OrcaReplayImportError("event sequence must be dense, starting at zero")
        mono = _nonnegative(record.get("mono_us"), "mono_us")
        if mono < previous_mono:
            raise OrcaReplayImportError("monotonic event timing regresses")
        previous_mono = mono
        _nonnegative(record.get("turn"), "turn")
        _time(record.get("ts"))
        for key in ("type", "actor"):
            if not isinstance(record.get(key), str) or not record[key].strip():
                raise OrcaReplayImportError("event requires nonempty " + key)
        if "attrs" in record and not isinstance(record["attrs"], dict):
            raise OrcaReplayImportError("event attrs must be an object")
        causes = record.get("causes", [])
        if (not isinstance(causes, list) or any(type(cause) is not int or cause < 0 or cause >= index for cause in causes)
                or len(causes) != len(set(causes))):
            raise OrcaReplayImportError("causes must contain unique preceding sequence numbers")
        if "redacted" in record and (not isinstance(record["redacted"], list) or any(not isinstance(item, str) for item in record["redacted"])):
            raise OrcaReplayImportError("event redacted identifiers must be strings")
        rows.append(record)
    if raw and not raw.endswith(b"\n") and not tail_truncated:
        gaps.add("Final valid JSONL record lacks a newline; the original event log is retained unchanged.")
    counts = native.get("counts", {})
    if not isinstance(counts, dict):
        raise OrcaReplayImportError("manifest counts must be an object")
    if "events" in counts and _nonnegative(counts["events"], "counts.events") != len(rows):
        gaps.add("Manifest event count differs from imported complete JSONL records; no missing records are invented.")
    blob_count = sum(name.startswith("blobs/") for name in files)
    for label, container, field in (("counts.blobs", counts, "blobs"),
                                    ("integrity.blob_count", integrity, "blob_count")):
        if field in container and _nonnegative(container[field], label) != blob_count:
            gaps.add("Manifest " + label + " differs from retained blob-store file count; no missing content is invented.")
    if Counter(row["type"] for row in rows)["run.start"] != 1 or Counter(row["type"] for row in rows)["run.end"] != 1:
        gaps.add("Exactly one run.start and run.end boundary was not supplied; run completeness cannot be established.")
    run_id = "orcareplay-" + native["run_id"]
    events = []
    for record in rows:
        kind, attrs = record["type"], record.get("attrs", {})
        value = _payload(record, files, gaps)
        data = {"source_kind": kind, "source_record": record, "mono_us": record["mono_us"],
                "source_line": record["seq"] + 1, "source_actor": record["actor"], "turn": record["turn"],
                "timestamp_basis": "run_created_at_plus_monotonic_microseconds",
                "source_causes": {"sequences": record.get("causes", []), "relationship": "source_declared_not_proven_dataflow"}}
        canonical = {"run.start": "run.start", "run.end": "run.end", "model.request": "model.request",
                     "model.response": "model.response", "tool.call": "tool.start", "tool.result": "tool.end",
                     "shell.exec": "tool.start", "shell.result": "tool.end"}.get(kind, "observation")
        if kind == "model.request":
            data["request"] = value if isinstance(value, dict) else {"content": value}
            if not isinstance(value, dict):
                gaps.add("One or more model request bodies are absent or opaque; message history is unavailable for those requests.")
        elif kind == "model.response":
            data["response"] = _response(value)
            data["resolved_payload"] = value
        elif kind in ("tool.call", "shell.exec"):
            data["arguments"] = attrs.get("input", attrs.get("arguments", value if value is not None else attrs))
            data["execution_status"] = "proposal_only_not_execution" if kind == "tool.call" else "source_observed_shell_invocation"
        elif kind in ("tool.result", "shell.result"):
            data["result"] = value if value is not None else attrs
            data["execution_status"] = "source_reported_result_not_independent_execution_attestation"
        else:
            data["content"] = value if value is not None else attrs
            data["classification"] = "source_observation_not_execution"
            if canonical == "observation":
                gaps.add("Source event type " + kind + " is retained as a neutral observation without execution or specialized semantic mapping.")
        if isinstance(attrs.get("model"), str) and attrs["model"]:
            data["model_identity"] = {"model": attrs["model"], "basis": "source_recorded_not_provider_attestation"}
        try:
            timestamp = (created + timedelta(microseconds=record["mono_us"])).isoformat().replace("+00:00", "Z")
        except OverflowError as exc:
            raise OrcaReplayImportError("monotonic timestamp exceeds supported range") from exc
        event = {"schema_version": "0.1", "id": f"orcareplay.event.{record['seq']}", "run_id": run_id,
                 "kind": canonical, "timestamp": timestamp, "sequence": record["seq"], "data": data}
        if canonical.startswith("tool."):
            event["tool"] = "shell" if kind.startswith("shell.") else str(attrs.get("name") or "unknown-tool")
        # Do not pair using broad causes, names, turn proximity, or proposals.
        # Explicit protocol IDs remain metadata until an execution-aware profile
        # can distinguish source reconstruction from observed invocation.
        events.append(event)
    inventory = [{"path": "files/" + name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
                 for name, content in sorted(files.items())]
    refs = [{"path": "artifacts/orcareplay/" + item["path"], "sha256": item["sha256"]} for item in inventory]
    manifest = {"schema_version": "0.1", "run_id": run_id,
                "agent": {"name": native["adapter"]["id"], "version": str(native["adapter"].get("version") or "unknown")},
                "started_at": created.isoformat().replace("+00:00", "Z"),
                "completeness": {"status": "partial", "gaps": sorted(gaps)},
                "source": {"adapter": "orcareplay", "experimental": True, "format": "orca-trace",
                           "format_version": native["schema_version"], "capture_surface": "offline-run-directory-or-zip-or-tar",
                           "source_revision_reviewed": SOURCE_REVISION, "native_manifest": native,
                           "sequence_basis": "source_seq", "timing_basis": "created_at_plus_mono_us",
                           "relationship_policy": "source causes and wire proposals are not proven dataflow or execution",
                           "event_count": len(events), "truncated_final_line": tail_truncated,
                           "archive_inventory": inventory, "omitted_snapshot_files": omitted},
                "artifact_refs": refs}
    return {"manifest": manifest, "events": events}


def _publish_new(staging, destination):
    owned = []
    try:
        destination.mkdir(mode=0o700, exist_ok=False)
        owned.append((destination, destination.lstat()))
        for source in sorted(staging.rglob("*")):
            target = destination / source.relative_to(staging)
            if source.is_dir():
                target.mkdir(mode=0o700, exist_ok=False)
                owned.append((target, target.lstat()))
            else:
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    owned.append((target, os.fstat(output.fileno())))
                    with source.open("rb") as stream:
                        shutil.copyfileobj(stream, output)
    except BaseException:
        for path, original in reversed(owned):
            try:
                current = path.lstat()
                if (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino):
                    path.rmdir() if stat.S_ISDIR(current.st_mode) else path.unlink()
            except OSError:
                pass
        raise


def import_orcareplay(source, destination):
    """Convert a run directory, native ZIP or plain tar to a validated bundle.

    All retained source files are copied verbatim as artifacts. No commands,
    models, network calls, source Git operations, replay or restore are invoked.
    A missing/corrupt referenced blob or missing/mismatched event hash fails the
    import; a genuinely truncated last line is retained with a partial-coverage gap.
    """
    destination = Path(destination).absolute()
    _safe_ancestors(destination)
    if destination.exists():
        raise OrcaReplayImportError("destination already exists; choose a new directory")
    files, omitted = _source_files(source)
    bundle = _convert(files, omitted)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _safe_ancestors(destination)
    try:
        with tempfile.TemporaryDirectory(prefix=".orcareplay-import-", dir=destination.parent) as temporary:
            staging = Path(temporary) / "bundle"
            for name, raw in files.items():
                artifact = staging / "artifacts/orcareplay/files" / name
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_bytes(raw)
            (staging / "manifest.json").write_text(json.dumps(bundle["manifest"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (staging / "events.jsonl").write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in bundle["events"]), encoding="utf-8")
            verified = load_bundle(staging)
            _publish_new(staging, destination)
    except (OSError, RecursionError, BundleValidationError) as exc:
        raise OrcaReplayImportError(f"cannot publish validated Orca bundle: {exc}") from exc
    return verified
