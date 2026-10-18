"""Import bounded native Codex JSONL transcripts without executing their text."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from core.bundle import BundleValidationError, load_bundle, read_json

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 50000
ORIGINATORS = frozenset({"codex", "codex_cli_rs", "codex_vscode", "codex_app", "codex_exec"})
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$")


class CodexImportError(ValueError):
    """Malformed, unsafe, unsupported, or non-Codex source recording."""


def _safe_ancestors(path):
    for current in (path, *path.parents):
        if current.is_symlink():
            raise CodexImportError("symlinks in source/destination paths are unsupported")


def _read(path):
    _safe_ancestors(path)
    # Pin every directory component; do not follow an ancestor substituted with
    # a symlink between the preliminary check and the source open.
    directory = None
    descriptor = None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory = os.open(path.anchor, flags | getattr(os, "O_DIRECTORY", 0))
        for part in path.parts[1:-1]:
            child = os.open(part, flags | getattr(os, "O_DIRECTORY", 0), dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(path.name, flags | getattr(os, "O_NONBLOCK", 0), dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                raise CodexImportError("source must be a bounded regular file")
            raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise CodexImportError("source exceeds byte limit")
            return raw
    except OSError as exc:
        raise CodexImportError("cannot read regular source without following symlinks") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def _time(value):
    if not isinstance(value, str) or not _TIME.fullmatch(value):
        raise CodexImportError("native record timestamp must be RFC3339 with timezone")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError as exc:
        raise CodexImportError("invalid native record timestamp") from exc


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise CodexImportError(f"{label} must be a nonempty string")
    return value


def _instant(stamp):
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _visible_assistant(record):
    payload = record["payload"]
    if payload.get("type") != "message" or payload.get("role") != "assistant":
        return False
    if payload.get("phase") not in (None, "commentary", "final", "final_answer"):
        return False
    metadata = payload.get("internal_chat_message_metadata_passthrough")
    containers = [record, payload] + ([metadata] if isinstance(metadata, dict) else [])
    if any(container.get("channel") not in (None, "commentary", "final", "final_answer") for container in containers):
        return False
    content = payload.get("content")
    return isinstance(content, list) and all(isinstance(block, dict) and block.get("type") in ("output_text", "refusal") for block in content)


def _convert(raw):
    rows = []
    for number, line in enumerate(raw.splitlines(), 1):
        if number > MAX_RECORDS:
            raise CodexImportError("source exceeds record limit")
        if not line.strip():
            raise CodexImportError(f"native JSONL line {number} is blank")
        try:
            record = read_json(line.decode("utf-8"), f"native JSONL line {number}")
        except (BundleValidationError, UnicodeError) as exc:
            raise CodexImportError(str(exc)) from exc
        if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
            raise CodexImportError("native records require object payloads")
        _text(record.get("type"), "native record type")
        stamp = _time(record.get("timestamp"))
        rows.append((number, record, stamp))
    metadata = [(n, r, t) for n, r, t in rows if r["type"] == "session_meta"]
    if len(metadata) != 1:
        raise CodexImportError("expected exactly one Codex session_meta record")
    meta_line, meta_record, started_at = metadata[0]
    meta = meta_record["payload"]
    if not isinstance(meta.get("originator"), str) or meta["originator"] not in ORIGINATORS:
        raise CodexImportError("session_meta originator is not a supported Codex agent")
    identity = _text(meta.get("id"), "Codex session id")
    if any(c.isspace() or not c.isprintable() for c in identity):
        raise CodexImportError("Codex session id has whitespace/control characters")
    run_id = "codex-session-" + identity
    gaps = {
        "Native rollout is a transcript or selected transcript subset, not complete provider request/response capture.",
        "Provider model requests, full tool schemas, retries and stream boundaries are unavailable; no model.request is reconstructed.",
        "Reasoning records are excluded from the timeline; hidden reasoning is unavailable and never reconstructed.",
        "User/developer context, runtime event copies and unsupported records are not mapped; supplied original bytes remain in the artifact.",
        "tool.start records an emitted invocation request, not proof of approval or execution; outputs are transcript evidence only.",
    }
    events = []
    starts, ends = defaultdict(list), defaultdict(list)
    native_ids = Counter(r["payload"].get("id") for _, r, _ in rows
                         if r["type"] == "response_item" and isinstance(r["payload"].get("id"), str))
    model = None
    previous_stamp = None
    excluded = Counter()
    model_records = []
    for number, record, stamp in rows:
        kind, payload = record["type"], record["payload"]
        if previous_stamp is not None and _instant(stamp) < _instant(previous_stamp):
            gaps.add("Source timestamps are out of order; timeline sequence preserves native file order and does not assert global chronology.")
        previous_stamp = stamp
        if kind == "turn_context":
            model = {"model": payload.get("model", "unknown"),
                     "model_provider": payload.get("model_provider", meta.get("model_provider", "unknown")),
                     "turn_id": payload.get("turn_id"), "source_line": number,
                     "basis": "recorded_turn_context_not_provider_attestation"}
            model_records.append(model)
            continue
        if kind != "response_item":
            excluded[kind] += 1
            continue
        subtype = payload.get("type")
        fields = {}
        data = {"source_line": number, "source_record": record,
                "timestamp_basis": "native_rollout_record_timestamp"}
        if model is not None:
            data["model_identity"] = dict(model)
        if _visible_assistant(record):
            event_kind = "model.response"
            data["response"] = payload
            data["content_basis"] = "visible_assistant_transcript_item_not_full_provider_response"
        elif subtype in ("function_call", "custom_tool_call"):
            event_kind = "tool.start"
            fields["call_id"] = _text(payload.get("call_id"), "tool call_id")
            name = _text(payload.get("name"), "tool name")
            namespace = payload.get("namespace")
            fields["tool"] = f"{namespace}.{name}" if isinstance(namespace, str) and namespace.strip() else name
            argument_key = "arguments" if subtype == "function_call" else "input"
            if argument_key not in payload:
                raise CodexImportError(f"{subtype} lacks {argument_key}")
            data["arguments"] = payload[argument_key]
            data["execution_status"] = "requested"
            data["content_basis"] = "native_" + subtype
        elif subtype in ("function_call_output", "custom_tool_call_output"):
            event_kind = "tool.end"
            fields["call_id"] = _text(payload.get("call_id"), "tool output call_id")
            if "output" not in payload:
                raise CodexImportError(f"{subtype} lacks output")
            data["result"] = payload["output"]
            data["execution_status"] = "result_recorded"
            data["content_basis"] = "native_" + subtype
        else:
            excluded["response_item." + str(subtype)] += 1
            continue
        native_id = payload.get("id")
        if isinstance(native_id, str) and native_ids[native_id] > 1:
            gaps.add("Repeated native response-item IDs are retained as distinct transcript items; associated calls are not correlated.")
            data["repeated_native_item_id"] = True
        item = {"schema_version": "0.1", "id": f"codex.line.{number}", "run_id": run_id,
                "kind": event_kind, "timestamp": stamp, "sequence": len(events), "data": data, **fields}
        events.append(item)
        if event_kind in ("tool.start", "tool.end"):
            (starts if event_kind == "tool.start" else ends)[item["call_id"]].append(item)
    for call in starts.keys() | ends.keys():
        calls, outputs = starts.get(call, []), ends.get(call, [])
        matched = False
        if len(calls) == len(outputs) == 1:
            request, result = calls[0], outputs[0]
            compatible = request["data"]["content_basis"] + "_output" == result["data"]["content_basis"]
            repeated = request["data"].get("repeated_native_item_id") or result["data"].get("repeated_native_item_id")
            if compatible and not repeated and request["sequence"] < result["sequence"] and _instant(request["timestamp"]) <= _instant(result["timestamp"]):
                result.update(tool=request["tool"], parent_id=request["id"], evidence_refs=[request["id"]],
                              dependencies=[{"event_id": request["id"], "provenance": "recorded"}])
                result["data"]["correlation_basis"] = "unique_explicit_call_id_and_matching_native_tool_kind"
                matched = True
        if not matched:
            gaps.add(f"Tool call {call}: missing, repeated, incompatible or nonpreceding call/output; no dependency assigned.")
            for item in calls + outputs:
                item["data"]["correlation_basis"] = "uncorrelated_transcript_item"
    if not events:
        gaps.add("No supported visible assistant/tool transcript items were supplied.")
    if not model_records:
        gaps.add("No turn_context model identity is recorded; model identity is unknown.")
    manifest = {"schema_version": "0.1", "run_id": run_id,
                "agent": {"name": "Codex", "version": str(meta.get("cli_version") or "unknown"),
                          "extensions": {"originator": meta["originator"]}},
                "started_at": started_at, "completeness": {"status": "partial", "gaps": sorted(gaps)},
                "source": {"adapter": "codex", "format": "native-rollout-jsonl",
                           "format_version": str(meta.get("cli_version") or "unknown"),
                           "capture_surface": "native-rollout-transcript",
                           "distinct_from": ["codex-exec-json-event-stream"], "session_id": identity,
                           "session_meta_source_line": meta_line, "session_meta": meta,
                           "model_identity_records": model_records, "excluded_record_counts": dict(sorted(excluded.items())),
                           "sequence_basis": "native_file_order", "selection": "supplied_file_only_coverage_unverified"},
                "artifact_refs": [{"path": "artifacts/codex/rollout.jsonl", "sha256": hashlib.sha256(raw).hexdigest()}]}
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
                if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
                    continue
                path.rmdir() if stat.S_ISDIR(current.st_mode) else path.unlink()
            except OSError:
                pass
        raise


def import_codex(source, destination):
    """Import only the supplied native JSONL into a new validated recording bundle.

    Neither source discovery, execution, provider calls nor replay is performed.
    Source may be an explicitly selected byte-preserving transcript subset.
    """
    source, destination = Path(source).absolute(), Path(destination).absolute()
    _safe_ancestors(destination)
    if destination.exists():
        raise CodexImportError("destination already exists; choose a new bundle directory")
    raw = _read(source)
    bundle = _convert(raw)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _safe_ancestors(destination)
    with tempfile.TemporaryDirectory(prefix=".codex-import-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "bundle"
        artifact = staging / "artifacts/codex/rollout.jsonl"
        artifact.parent.mkdir(parents=True)
        # Use bytes already captured, never reread a potentially changing rollout.
        artifact.write_bytes(raw)
        (staging / "manifest.json").write_text(json.dumps(bundle["manifest"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (staging / "events.jsonl").write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in bundle["events"]), encoding="utf-8")
        try:
            verified = load_bundle(staging)
        except BundleValidationError as exc:
            raise CodexImportError(f"converted source violates bundle contract: {exc}") from exc
        try:
            _publish_new(staging, destination)
        except OSError as exc:
            raise CodexImportError("cannot publish new bundle; destination may exist or publication failed") from exc
    return verified
