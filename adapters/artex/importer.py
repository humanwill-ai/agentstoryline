"""Read-only import of Artex TaskArchiveSnapshot exports (formats 1–3)."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading


MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 4096
MAX_ROWS = 50000
DECODER_TIMEOUT_SECONDS = 15
STREAM_PATH = "database/llm_records.ndjson"
TRAFFIC_PATH = "traffic/traffic.json"


class ArtexImportError(ValueError):
    """Unsupported, inconsistent, or unsafe source export."""


class _BoundedArchiveStream:
    """Count expanded bytes before tarfile processes hidden metadata records."""

    def __init__(self, stream):
        self.stream = stream
        self.remaining = MAX_ARCHIVE_BYTES

    def read(self, size=-1):
        # Even a metadata-declared huge read cannot allocate beyond our budget.
        amount = self.remaining + 1 if size < 0 else min(size, self.remaining + 1)
        raw = self.stream.read(amount)
        if len(raw) > self.remaining:
            raise ArtexImportError("expanded archive exceeds byte limit")
        self.remaining -= len(raw)
        return raw


class _BoundedTarInfo(tarfile.TarInfo):
    @classmethod
    def frombuf(cls, buf, encoding, errors):
        item = super().frombuf(buf, encoding, errors)
        # PAX/GNU metadata is handled inside tarfile, before archive iteration.
        if item.size < 0 or item.size > MAX_FILE_BYTES:
            raise ArtexImportError("archive header exceeds member byte limit")
        return item


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ArtexImportError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ArtexImportError(f"invalid JSON constant: {value}")

    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (ValueError, UnicodeError) as exc:
        raise ArtexImportError(f"invalid source JSON: {exc}") from exc


def _read(path):
    # Child directories are checked too: O_NOFOLLOW alone only protects the leaf.
    current = path
    while current != current.parent:
        if current.is_symlink():
            raise ArtexImportError(f"source symlinks are unsupported: {current.name}")
        current = current.parent
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) |
                             getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                raise ArtexImportError("source must be a bounded regular file")
            raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise ArtexImportError("source file exceeds size limit")
            return raw
    except OSError as exc:
        raise ArtexImportError(f"cannot read source file: {path.name}") from exc


def _archive(path, inventory=None):
    """Inspect without extracting; optional installed zstd only decompresses stdin."""
    # File is opened without following symlinks. The compressed input is bounded.
    raw = _read(path)
    process = None
    timer = None
    files = {}
    try:
        with tempfile.TemporaryFile() as compressed:
            compressed.write(raw)
            compressed.seek(0)
            if path.name.endswith(".tar.zst"):
                executable = shutil.which("zstd")
                if not executable:
                    raise ArtexImportError(".tar.zst needs an installed zstd; supply an extracted export instead")
                process = subprocess.Popen([executable, "-q", "-d", "-c"],
                                           stdin=compressed, stdout=subprocess.PIPE,
                                           stderr=subprocess.DEVNULL)
                def stop_decoder():
                    try:
                        process.kill()
                    except OSError:
                        pass  # It completed between the timer and kill syscall.
                timer = threading.Timer(DECODER_TIMEOUT_SECONDS, stop_decoder)
                timer.daemon = True
                timer.start()
                stream = process.stdout
            else:
                stream = compressed
            size = 0
            names = set()
            bounded = _BoundedArchiveStream(stream)
            with tarfile.open(fileobj=bounded, mode="r|", tarinfo=_BoundedTarInfo) as archive:
                for index, member in enumerate(archive):
                    name = member.name.rstrip("/")
                    parts = PurePosixPath(name).parts
                    if (index >= MAX_ARCHIVE_MEMBERS or not parts or
                            name.startswith("/") or ".." in parts or "\\" in name or
                            name in names or not (member.isfile() or member.isdir())):
                        raise ArtexImportError("unsafe, duplicate, or unsupported archive member")
                    name = PurePosixPath(name).as_posix()
                    if name in names:
                        raise ArtexImportError("duplicate normalized archive member")
                    names.add(name)
                    size += member.size
                    if member.size < 0 or member.size > MAX_FILE_BYTES or size > MAX_ARCHIVE_BYTES:
                        raise ArtexImportError("archive exceeds import size limits")
                    if member.isfile():
                        with archive.extractfile(member) as content:
                            files[name] = content.read(MAX_FILE_BYTES + 1)
                        if inventory is not None:
                            inventory.append(_inventory_entry(name, files[name]))
            if process:
                # The same budget also covers trailing decompressed data.
                while bounded.read(64 * 1024):
                    pass
                if process.wait(timeout=10) != 0:
                    raise ArtexImportError("zstd could not decode source archive")
    except (tarfile.TarError, OSError, subprocess.TimeoutExpired) as exc:
        raise ArtexImportError("cannot read source archive") from exc
    finally:
        if timer:
            timer.cancel()
        if process:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stdout.close()
    if "manifest.json" not in files:
        raise ArtexImportError("archive has no manifest.json snapshot")
    return files


def _inventory_entry(name, raw):
    return {"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _directory(root):
    """Read a bounded extracted package without following links or special files."""
    files = {}
    total = 0
    members = 0
    # scandir never follows a link; _read additionally checks every parent path.
    pending = [root]
    while pending:
        directory = pending.pop()
        current = directory
        while current != current.parent:
            if current.is_symlink():
                raise ArtexImportError("source symlinks are unsupported")
            current = current.parent
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    members += 1
                    if members > MAX_ARCHIVE_MEMBERS:
                        raise ArtexImportError("extracted archive exceeds member limit")
                    info = entry.stat(follow_symlinks=False)
                    path = Path(entry.path)
                    name = path.relative_to(root).as_posix()
                    if "\\" in name:
                        raise ArtexImportError("unsupported extracted archive path")
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(path)
                    elif stat.S_ISREG(info.st_mode):
                        raw = _read(path)
                        total += len(raw)
                        if total > MAX_ARCHIVE_BYTES:
                            raise ArtexImportError("extracted archive exceeds byte limit")
                        files[name] = raw
                    else:
                        raise ArtexImportError("links and special extracted archive entries are unsupported")
        except OSError as exc:
            raise ArtexImportError("cannot inspect extracted archive") from exc
    if "manifest.json" not in files:
        raise ArtexImportError("archive has no manifest.json snapshot")
    return files


def _traffic_count(files, tables):
    """Native traffic is a companion snapshot, not a database table."""
    if TRAFFIC_PATH not in files:
        return len(tables.get("traffic", []))
    if tables.get("traffic"):
        raise ArtexImportError("ambiguous traffic table and companion snapshot")
    traffic = _json(files[TRAFFIC_PATH])
    if not isinstance(traffic, dict) or type(traffic.get("version")) is not int or traffic["version"] != 1:
        raise ArtexImportError("expected an Artex traffic archive version 1")
    # Go's native exporter serializes an empty nil slice as null.
    rows = traffic.get("exchanges")
    if rows is None and "exchanges" in traffic:
        rows = []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ArtexImportError("traffic exchanges must contain an array of row objects")
    return len(rows)


def _time(value):
    if not isinstance(value, str) or "T" not in value:
        raise ArtexImportError("source timestamps must be RFC3339 strings")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArtexImportError("invalid source timestamp") from exc
    if instant.tzinfo is None:
        raise ArtexImportError("source timestamp needs a timezone")
    return instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _identity(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value):
        raise ArtexImportError("source row IDs must be strings or integers")
    return str(value)


def _body(value, gaps, label):
    if value in (None, ""):
        gaps.add(f"{label}: body is absent")
        return None
    if isinstance(value, str):
        try:
            return _json(value)
        except ArtexImportError:
            gaps.add(f"{label}: body is not valid JSON; original text retained")
            return value
    # Snapshot SQL fields are strings; retain structured values for explicit exports.
    return value


def _recording_metadata(value, gaps, label):
    """Interpret only the explicitly versioned capture contract; keep raw data."""
    if not isinstance(value, dict) or "traceforge" not in value:
        return None
    metadata = value["traceforge"]
    if not isinstance(metadata, dict) or type(metadata.get("version")) is not int or metadata["version"] != 1:
        gaps.add(f"{label}: unsupported recording metadata; preserved without interpretation")
        return None
    return metadata


def _model_identity(metadata):
    if not isinstance(metadata, dict) or metadata.get("recording_enabled") is False:
        return None
    call, session = metadata.get("model_call_id"), metadata.get("session_id")
    if isinstance(call, str) and call and isinstance(session, str) and session:
        return call, session
    return None


def _convert(snapshot, llm_rows, artifacts, files=None):
    task_id = _identity(snapshot.get("task_id"))
    run_id = "artex-task-" + task_id
    exported_at = _time(snapshot.get("created_at"))
    tables = snapshot["tables"]
    gaps = {
        "Artex snapshots are partial recordings: recording can be disabled and persistence failures are not enumerated.",
        "Provider stream timing and per-attempt request/response bodies are not captured by the normalized recording; raw wire text is preserved when present, not reconstructed.",
        "Activity tool_use records an invocation request; a start does not prove execution or approval. Result errors may include blocked operations.",
        "Workspace files are preserved as inert artifacts, not interpreted or executed.",
    }
    events = []
    ids = set()

    def event(identifier, kind, timestamp, data, **fields):
        if len(events) >= MAX_ROWS:
            raise ArtexImportError("export exceeds timeline event count limit")
        if identifier in ids:
            raise ArtexImportError(f"duplicate source event ID: {identifier}")
        ids.add(identifier)
        item = {"schema_version": "0.1", "id": identifier, "run_id": run_id,
                "kind": kind, "timestamp": timestamp, "sequence": len(events),
                "data": data, **fields}
        events.append(item)
        return item

    def timestamp(row, table):
        value = row.get("ts") or row.get("created_at")
        if value is None:
            gaps.add(f"{table}: rows without timestamps are observed at snapshot export time")
            return exported_at
        return _time(value)

    event("artex.snapshot", "observation", exported_at,
          {"source_kind": "archive.snapshot", "task_id": snapshot["task_id"],
           "snapshot": artifacts[0], "source_format_version": snapshot["format_version"]})
    # Unknown native tables remain inert observations with their complete row.
    # No execution, achievement or causal meaning is inferred from table names.
    primary_tables = ("tasks", "explorations", "activity", "tool_usage", "llm_usage", "findings",
                      "finding_traffic_bindings", "traffic_evidence_snapshots", "intercept_pending")
    mapped_tables = primary_tables + tuple(sorted(set(tables) - set(primary_tables) - {"llm_records"}))
    activity_calls = {}
    activity_results = {}
    model_exchanges = {}
    export_metadata = snapshot.get("recording_export")
    if isinstance(export_metadata, dict):
        declared = export_metadata.get("completeness", {}).get("gaps", []) if isinstance(export_metadata.get("completeness"), dict) else []
        if isinstance(declared, list):
            gaps.update("Artex export: " + gap for gap in declared if isinstance(gap, str) and gap.strip())
    # Each table is an exported database table, not the reduced ActivityDTO.
    for table in mapped_tables:
        occurrences = {}
        for row in tables.get(table, []):
            if row.get("id") is not None:
                source_id = _identity(row["id"])
                identity_basis = "source_primary_id"
            else:
                canonical = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                ordinal = occurrences.get(digest, 0)
                occurrences[digest] = ordinal + 1
                source_id = "composite-" + digest + "-" + str(ordinal)
                identity_basis = "canonical_row_sha256_and_duplicate_ordinal"
            data = {"source_table": table, "source_id": row["id"] if row.get("id") is not None else source_id, "source_record": row,
                    "identity_basis": identity_basis}
            kind = "observation"
            fields = {}
            if table == "activity" and row.get("kind") in ("tool_use", "tool_result"):
                kind = "tool.start" if row["kind"] == "tool_use" else "tool.end"
                data["execution_status"] = "requested" if kind == "tool.start" else "result_recorded"
                metadata = _body(row.get("metadata"), set(), f"activity {source_id} metadata")
                capture = _recording_metadata(metadata, gaps, f"activity {source_id}")
                content_key = "tool_input" if kind == "tool.start" else "tool_result"
                if capture is None or content_key not in capture:
                    data["arguments" if kind == "tool.start" else "result"] = _body(row.get("detail"), gaps, f"activity {source_id} legacy detail")
                if capture is not None:
                    data["recording_metadata"] = capture
                    if content_key in capture:
                        data["arguments" if kind == "tool.start" else "result"] = capture[content_key]
                        data["content_basis"] = "recorded_structured_content"
                    if capture.get("recording_enabled") is False:
                        gaps.add(f"activity {source_id}: model recording was disabled; no model correlation is available")
                    if capture.get("gap"):
                        gaps.add(f"activity {source_id}: {capture['gap']}")
                if row.get("tool_use_id"):
                    fields["call_id"] = row["tool_use_id"]
                else:
                    gaps.add(f"activity {source_id}: tool invocation/result has no call ID")
                if row.get("tool"):
                    fields["tool"] = row["tool"]
            if table == "tool_usage":
                gaps.add("tool_usage stores attribution only, without invocation IDs, arguments, results or reliable activity/model links")
                if row.get("tool_key"):
                    fields["tool"] = row["tool_key"]
            item = event(f"artex.{table}.{source_id}", kind, timestamp(row, table), data, **fields)
            capture = data.get("recording_metadata", {})
            if capture.get("observed_at"):
                item["timestamp"] = _time(capture["observed_at"])
                data["timestamp_basis"] = "recorded_activity_observed_at"
            if kind in ("tool.start", "tool.end") and item.get("call_id"):
                target = activity_calls if kind == "tool.start" else activity_results
                target.setdefault(item["call_id"], []).append(item)

    histories = []
    for row in llm_rows:
        if row.get("task_id") not in (None, "", task_id, snapshot["task_id"]):
            raise ArtexImportError("LLM row belongs to a different task")
        source_id = _identity(row.get("id"))
        stamp = timestamp(row, "llm_records")
        request = _body(row.get("request_body"), gaps, f"llm_records {source_id} request")
        response = _body(row.get("response_body"), gaps, f"llm_records {source_id} response")
        req_capture = _recording_metadata(request, gaps, f"llm_records {source_id} request")
        res_capture = _recording_metadata(response, gaps, f"llm_records {source_id} response")
        base = f"artex.llm_records.{source_id}"
        shared = {"source_table": "llm_records", "source_id": row["id"],
                  "source_record": row, "timestamp_basis": "row_persistence"}
        req_stamp = _time(req_capture["started_at"]) if req_capture and req_capture.get("started_at") else stamp
        res_stamp = _time(res_capture["ended_at"]) if res_capture and res_capture.get("ended_at") else stamp
        mixed_reverse = datetime.fromisoformat(res_stamp.replace("Z", "+00:00")) < datetime.fromisoformat(req_stamp.replace("Z", "+00:00"))
        if mixed_reverse:
            if req_capture and req_capture.get("started_at") and res_capture and res_capture.get("ended_at"):
                raise ArtexImportError("recorded model response completion precedes request start")
            gaps.add(f"llm_records {source_id}: mixed persistence/capture timestamps reverse request/response order; original times retained, not an execution interval")
        req_data = {**shared, "request": request}
        res_data = {**shared, "response": response}
        if req_capture:
            req_data["recording_metadata"] = req_capture
        if res_capture:
            res_data["recording_metadata"] = res_capture
            if res_capture.get("completion") not in (None, "complete"):
                gaps.add(f"llm_records {source_id}: recorded model completion is {res_capture['completion']}; normalized response may be partial")
        req_data["timestamp_basis"] = "recorded_request_started_at" if req_capture and req_capture.get("started_at") else "row_persistence"
        res_data["timestamp_basis"] = "recorded_response_ended_at" if res_capture and res_capture.get("ended_at") else "row_persistence"
        if req_data["timestamp_basis"] == "row_persistence" or res_data["timestamp_basis"] == "row_persistence":
            gaps.add("Some LLM events use row persistence time; explicit request start/response completion timestamps are unavailable")
        req = event(base + ".request", "model.request", req_stamp, req_data, call_id=base)
        if mixed_reverse:
            res_data["unresolved_request_event_id"] = req["id"]
            res_data["correlation_basis"] = "same_source_row_with_conflicting_mixed_timestamp_bases"
        res = event(base + ".response", "model.response", res_stamp, res_data,
                    call_id=base, **({} if mixed_reverse else {"parent_id": req["id"], "evidence_refs": [req["id"]]}))
        model_id = req_capture.get("model_call_id") if req_capture else None
        if (isinstance(model_id, str) and model_id and res_capture and
                model_id == res_capture.get("model_call_id") and
                req_capture.get("session_id") == res_capture.get("session_id") and
                isinstance(req_capture.get("session_id"), str) and req_capture["session_id"] and
                req_capture.get("recording_enabled") is True and res_capture.get("recording_enabled") is True):
            req["call_id"] = res["call_id"] = model_id
            model_exchanges.setdefault((model_id, req_capture["session_id"]), []).append((req, res))
        else:
            gaps.add(f"llm_records {source_id}: no consistent enabled model-call/session identity is recorded")
        if not isinstance(response, dict) or not isinstance(response.get("content"), list):
            gaps.add("Some normalized LLM responses lack full content blocks; tool-use response content is unavailable")
        if not row.get("raw_request") or not row.get("raw_response"):
            gaps.add("Some LLM rows lack raw wire request/response capture")
        if isinstance(request, dict) and isinstance(request.get("tools"), list):
            definitions = request.get("tool_definitions", [])
            schemas = {item.get("name") for item in definitions
                       if isinstance(item, dict) and isinstance(item.get("name"), str)
                       and isinstance(item.get("input_schema"), (dict, bool))} if isinstance(definitions, list) else set()
            if any(isinstance(tool, str) and tool not in schemas for tool in request["tools"]):
                gaps.add("Some normalized LLM requests list tools without corresponding full schemas")
        messages = request.get("messages", []) if isinstance(request, dict) else []
        if not isinstance(messages, list):
            gaps.add(f"llm_records {source_id}: messages is not an array; preserved without interpretation")
            continue
        for mi, message in enumerate(messages):
            blocks = message.get("content", []) if isinstance(message, dict) else []
            if not isinstance(blocks, list):
                continue
            for bi, block in enumerate(blocks):
                if not isinstance(block, dict) or block.get("type") not in ("tool_use", "tool_result"):
                    continue
                call = block.get("id") if block["type"] == "tool_use" else block.get("tool_use_id")
                fields = {"parent_id": req["id"], "evidence_refs": [req["id"]]}
                if isinstance(call, str) and call:
                    fields["call_id"] = call
                if block.get("name"):
                    fields["tool"] = block["name"]
                item = event(f"{base}.history.{mi}.{bi}", "observation", req_stamp,
                             {"source_kind": "request.history." + block["type"],
                              "source_id": row["id"], "session_id": row.get("session_id"),
                              "message_index": mi, "block_index": bi, "block": block,
                              "timestamp_basis": "first_observed_in_this_request_not_execution"}, **fields)
                histories.append(item)

    # Transcript UUID parent chains describe recorded history, not execution
    # dependencies. Compaction can repeat messages; every row stays an observation.
    transcript_files = [name for name in (files or {})
                        if name.startswith("files/transcripts/") and name.endswith(".jsonl")]
    transcript_identities = {}
    transcript_lines = 0
    for name in sorted(transcript_files):
        path_id = hashlib.sha256(name.encode("utf-8")).hexdigest()[:24]
        for line_number, line in enumerate(io.BytesIO(files[name]), 1):
            transcript_lines += 1
            if transcript_lines > MAX_ROWS:
                raise ArtexImportError("transcripts exceed total processed line count limit")
            if not line.strip():
                continue
            try:
                row = _json(line)
            except ArtexImportError:
                gaps.add(f"{name}:{line_number}: malformed transcript JSON; original artifact retained")
                continue
            if not isinstance(row, dict):
                gaps.add(f"{name}:{line_number}: transcript row is not an object; original artifact retained")
                continue
            data = {"source_kind": "archive.transcript", "source_path": name,
                    "source_line": line_number, "source_record": row,
                    "observation_semantics": "recorded_history_not_execution",
                    "timestamp_basis": "transcript_timestamp"}
            for key in ("uuid", "parent_uuid", "session_id", "agent_id", "is_sidechain", "type"):
                if key in row:
                    data[key] = row[key]
            identity = row.get("uuid")
            if isinstance(identity, str) and identity:
                identity_key = (str(row.get("session_id")), identity)
                canonical = json.dumps(row, sort_keys=True, ensure_ascii=False)
                prior = transcript_identities.setdefault(identity_key, set())
                if canonical in prior:
                    continue  # Original line remains in its checksum-verified artifact.
                if prior:
                    gaps.add(f"{name}:{line_number}: conflicting transcript UUID content; both original rows retained")
                    data["conflicting_uuid_content"] = True
                prior.add(canonical)
            if row.get("type") not in ("message", "boundary"):
                gaps.add(f"{name}:{line_number}: unknown transcript row type; preserved without interpretation")
            try:
                stamp = _time(row.get("timestamp"))
            except ArtexImportError:
                stamp = exported_at
                data["timestamp_basis"] = "snapshot_export_fallback"
                gaps.add(f"{name}: missing or invalid transcript timestamps use snapshot export time")
            event(f"artex.transcript.{path_id}.{line_number}", "observation", stamp, data)
    if not transcript_files:
        gaps.add("No archived transcript JSONL files are present; transcript history is unavailable")
    # Stable UTC chronology; equal timestamps preserve source-table/row order.
    events.sort(key=lambda item: (datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")), item["sequence"]))
    positions = {item["id"]: index for index, item in enumerate(events)}
    for item in events:
        item["sequence"] = positions[item["id"]]
        if item["kind"] == "tool.end":
            candidates = activity_calls.get(item.get("call_id"), [])
            row = item["data"]["source_record"]
            candidates = [candidate for candidate in candidates if all(
                candidate["data"]["source_record"].get(key) == row.get(key)
                for key in ("exploration_id", "node_id", "worker", "main_seg"))]
            session = item["data"].get("recording_metadata", {}).get("session_id")
            candidates = [candidate for candidate in candidates if session is None or
                          candidate["data"].get("recording_metadata", {}).get("session_id") == session]
            if len(candidates) == 1 and positions[candidates[0]["id"]] < item["sequence"]:
                item["dependencies"] = [{"event_id": candidates[0]["id"], "provenance": "recorded"}]
            else:
                gaps.add(f"activity {item['data']['source_id']}: tool result has no unique earlier matching invocation in the same activity context")
        if item["kind"] == "tool.start":
            capture = item["data"].get("recording_metadata", {})
            candidates = model_exchanges.get(_model_identity(capture), []) if item.get("call_id") else []
            if len(candidates) == 1 and positions[candidates[0][0]["id"]] < item["sequence"]:
                request, response = candidates[0]
                item["dependencies"] = [{"event_id": request["id"], "provenance": "recorded"}]
                item["data"]["correlation_basis"] = "explicit_model_call_id_and_session_id"
                item["data"]["model_response_event_id"] = response["id"]
            else:
                gaps.add(f"activity {item['data']['source_id']}: no unique preceding request matches an explicit model-call/session identity; timing is not a dependency")
    for item in histories:
        candidates = (activity_calls if item["data"]["block"]["type"] == "tool_use" else activity_results).get(item.get("call_id"), [])
        if len(candidates) == 1 and positions[candidates[0]["id"]] < item["sequence"]:
            item["evidence_refs"].append(candidates[0]["id"])
            item["data"]["correlation_basis"] = "unique_recorded_tool_use_id_within_export"
        else:
            item["data"]["correlation_basis"] = "transcript_only"
    for call, starts in activity_calls.items():
        if call not in activity_results:
            gaps.add(f"tool call {call}: no activity result is present")
    unknown = sorted(set(tables) - set(mapped_tables) - {"llm_records"})
    if unknown:
        gaps.add("Tables preserved in source snapshot without timeline mapping: " + ", ".join(unknown))
    if not llm_rows:
        gaps.add("No LLM body records are present; recording may have been disabled")
    manifest = {"schema_version": "0.1", "run_id": run_id,
                "agent": {"name": "Artex", "version": "unknown", "extensions": {"archive_format_version": snapshot["format_version"]}},
                "started_at": events[0]["timestamp"],
                "completeness": {"status": "partial", "gaps": sorted(gaps)},
                "artifact_refs": artifacts,
                "source": {"adapter": "artex", "task_id": snapshot["task_id"],
                           "exported_at": exported_at,
                           "archive_inventory": [_inventory_entry(name, raw) for name, raw in sorted((files or {}).items())],
                           "transcript_import": {"semantics": "observations_only", "files": len(transcript_files)}}}
    extra_metadata = sorted(set(snapshot) - {"task_id", "created_at", "tables", "format_version", "data_counts", "streamed_tables", "recording_export"})
    if extra_metadata:
        manifest["completeness"]["gaps"].append("Additional snapshot metadata retained in original manifest artifact without interpretation: " + ", ".join(extra_metadata))
    if export_metadata is not None:
        manifest["source"]["recording_export"] = export_metadata
    return {"manifest": manifest, "events": events}


def _publish_new(staging, destination):
    """Reserve a new directory exclusively; never rename over a concurrent path."""
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
                    with source.open("rb") as input_stream:
                        shutil.copyfileobj(input_stream, output)
    except BaseException:
        # Remove only entries whose inode is still ours. Do not recursively remove
        # a directory: another writer's files must survive a failed publication.
        for path, original in reversed(owned):
            try:
                current = path.lstat()
                if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
                    continue
                if stat.S_ISDIR(current.st_mode):
                    path.rmdir()
                else:
                    path.unlink()
            except OSError:
                pass
        raise


def import_artex(source, destination):
    """Create a new bundle directory; return its validated manifest/events mapping.

    source: extracted snapshot directory, snapshot JSON, or native .tar.zst/.tar.
    destination must not already exist. No source/database/archive lifecycle writes.
    """
    source = Path(source).absolute()
    destination = Path(destination).absolute()
    if source.is_symlink():
        raise ArtexImportError("source symlinks are unsupported")
    if source.is_dir():
        files = _directory(source)
        root = source
    elif source.name.endswith((".tar.zst", ".tar")):
        files = _archive(source)
        root = None
    else:
        files = {"manifest.json": _read(source)}
        root = source.parent
    snapshot = _json(files["manifest.json"])
    if not isinstance(snapshot, dict) or type(snapshot.get("format_version")) is not int or snapshot["format_version"] not in (1, 2, 3):
        raise ArtexImportError("expected an Artex TaskArchiveSnapshot format 1, 2 or 3")
    tables = snapshot.get("tables")
    if not isinstance(tables, dict) or any(not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows) for rows in tables.values()):
        raise ArtexImportError("snapshot tables must contain arrays of row objects")
    streamed = snapshot.get("streamed_tables", {})
    if streamed is None:
        streamed = {}
    if not isinstance(streamed, dict) or set(streamed) - {"llm_records"}:
        raise ArtexImportError("unsupported streamed table")
    if streamed:
        if streamed["llm_records"] != STREAM_PATH or tables.get("llm_records"):
            raise ArtexImportError("invalid or ambiguous LLM stream declaration")
        if STREAM_PATH not in files:
            if root is None:
                raise ArtexImportError("declared LLM stream missing from archive")
            files[STREAM_PATH] = _read(root / STREAM_PATH)
        llm_rows = []
        for line_number, line in enumerate(io.BytesIO(files[STREAM_PATH]), 1):
            if line_number > MAX_ROWS:
                raise ArtexImportError("LLM stream exceeds processed line count limit")
            if line.strip():
                llm_rows.append(_json(line))
        if any(not isinstance(row, dict) for row in llm_rows):
            raise ArtexImportError("LLM stream must contain row objects")
    else:
        llm_rows = tables.get("llm_records", [])
    traffic_count = _traffic_count(files, tables)
    row_count = (sum(len(rows) for rows in tables.values()) + (len(llm_rows) if streamed else 0)
                 + traffic_count - len(tables.get("traffic", [])))
    if row_count > MAX_ROWS:
        raise ArtexImportError("export exceeds row count limit")
    counts = snapshot.get("data_counts", {})
    if not isinstance(counts, dict):
        raise ArtexImportError("invalid data_counts")
    for table, expected in counts.items():
        actual = (len(llm_rows) if table == "llm_records" else
                  traffic_count if table == "traffic" else len(tables.get(table, [])))
        if type(expected) is not int or expected < 0 or actual != expected:
            raise ArtexImportError(f"source row count mismatch: {table}")
    if destination.exists() or destination.is_symlink():
        raise ArtexImportError("destination already exists; choose a new bundle directory")
    artifacts = [{"path": "artifacts/artex/" + name, "sha256": hashlib.sha256(raw).hexdigest()}
                 for name, raw in sorted(files.items(), key=lambda item: (item[0] != "manifest.json", item[0]))]
    bundle = _convert(snapshot, llm_rows, artifacts, files)
    if TRAFFIC_PATH in files:
        bundle["manifest"]["completeness"]["gaps"].append("Archived traffic exchanges are checksum-preserved in traffic/traffic.json; they are not mapped to timeline events.")
    if root is not None and not source.is_dir():
        bundle["manifest"]["completeness"]["gaps"].append("Snapshot JSON import reads only the snapshot and declared LLM stream; companion transcript/workspace files are not inventoried. Supply the extracted archive directory for full bounded file preservation.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".artex-import-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "bundle"
        staging.mkdir()
        for name, raw in files.items():
            target = staging / "artifacts" / "artex" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        (staging / "manifest.json").write_text(json.dumps(bundle["manifest"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        with (staging / "events.jsonl").open("w", encoding="utf-8") as output:
            for item in bundle["events"]:
                output.write(json.dumps(item, ensure_ascii=False) + "\n")
        from core.bundle import BundleValidationError, load_bundle
        try:
            verified = load_bundle(staging)
        except BundleValidationError as exc:
            raise ArtexImportError(f"converted source violates bundle contract: {exc}") from exc
        try:
            _publish_new(staging, destination)
        except OSError as exc:
            raise ArtexImportError("cannot publish new bundle; destination may exist or publication failed") from exc
    return verified
