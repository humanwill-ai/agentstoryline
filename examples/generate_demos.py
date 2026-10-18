#!/usr/bin/env python3
"""Generate redistributable synthetic recordings; never read runtime evidence.

Uses only the Python standard library. All content and identifiers below were
authored for this demo. Stored tool text is inert and is never executed here.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile
import zipfile


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def json_lines(rows):
    return b"".join((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8") for row in rows)


def timestamp(second):
    return f"2026-10-18T12:00:{second:02d}Z"


def artex_files():
    capture = {"version": 1, "model_call_id": "synthetic-model-call-1",
               "session_id": "synthetic-artex-session", "recording_enabled": True}
    arguments = {"command": "cat synthetic-workspace/fixture.json"}
    result = {"type": "tool_result", "tool_use_id": "synthetic-inspect-1",
              "content": [{"type": "text", "text": '{"ok":true,"value":42}'}], "is_error": False}
    activity = []
    for identifier, second, kind, body in (
        (11, 2, "tool_use", {"tool_input": arguments}),
        (12, 3, "tool_result", {"tool_result": result}),
    ):
        activity.append({"id": identifier, "exploration_id": 42, "node_id": 7,
                         "worker": "worker-1", "main_seg": None, "kind": kind,
                         "tool": "Bash", "tool_use_id": "synthetic-inspect-1",
                         "is_error": False, "summary": "Synthetic local fixture inspection",
                         "detail": "Synthetic display summary; structured evidence is in metadata.",
                         "metadata": {"traceforge": {**capture, **body, "observed_at": timestamp(second)}},
                         "created_at": timestamp(second)})
    request = {"system": [{"type": "text", "text": "Synthetic local demonstration. Read only the provided fixture."}],
               "messages": [{"role": "user", "content": [{"type": "text", "text": "Read fixture.json and report its value."}]}],
               "tools": [{"name": "Bash", "description": "Synthetic local read operation",
                          "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}}}],
               "traceforge": {**capture, "started_at": timestamp(1)}}
    response = {"text": "", "content": [{"type": "tool_use", "id": "synthetic-inspect-1",
                                               "name": "Bash", "input": arguments}],
                "stop_reason": "tool_use", "usage": {"input_tokens": 0, "output_tokens": 0},
                "traceforge": {**capture, "ended_at": timestamp(4), "attempts": [{"number": 1, "status": "ok"}]}}
    row = {"id": 101, "ts": timestamp(4), "task_id": "23", "model": "synthetic-model",
           "session_id": capture["session_id"], "worker": "worker-1", "profile_name": "synthetic",
           "status": "ok", "error": None, "latency_ms": 3000,
           "input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_write": 0,
           "request_body": json.dumps(request), "response_body": json.dumps(response),
           "raw_request": None, "raw_response": None}
    tables = {
        "tasks": [{"id": 23, "exploration_id": 42, "name": "Synthetic fixture inspection",
                   "description": "Newly authored demo; no real runtime or model output.",
                   "goal": "Inspect the synthetic local fixture", "status": "completed", "created_at": timestamp(0)}],
        "exploration_nodes": [{"id": 7, "exploration_id": 42, "kind": "intent", "title": "Read synthetic fixture"}],
        "activity": activity,
        "findings": [{"id": 31, "task_id": 23, "exploration_id": 42, "title": "Synthetic fixture value is 42",
                      "description": "Demonstration observation only; not a security finding.", "created_at": timestamp(5)}],
        "llm_records": [],
    }
    manifest = {"format_version": 3, "created_at": timestamp(6), "task_id": 23, "exploration_id": 42,
                "source_task_ids": [], "hosts": ["127.0.0.1"], "exclusive_hosts": [], "exclusive_asset_ids": [],
                "tables": tables, "data_counts": {key: len(rows) for key, rows in tables.items()},
                "streamed_tables": {"llm_records": "database/llm_records.ndjson"}, "aggregate_stats": {}}
    manifest["data_counts"]["llm_records"] = 1
    transcript = {"uuid": "synthetic-transcript-1", "session_id": capture["session_id"], "is_sidechain": False,
                  "timestamp": timestamp(5), "type": "message", "message": {"role": "assistant",
                  "content": [{"type": "text", "text": "Synthetic fixture value: 42."}]}}
    return {"manifest.json": encoded(manifest), "database/llm_records.ndjson": json_lines([row]),
            "files/fixture.json": encoded({"synthetic": True, "ok": True, "value": 42}),
            "files/transcripts/exp42-synthetic.jsonl": json_lines([transcript])}


def codex_rows():
    def row(kind, payload, second):
        return {"timestamp": timestamp(second), "type": kind, "payload": payload}
    return [
        row("session_meta", {"id": "synthetic-codex-session", "originator": "codex_cli_rs",
                             "cli_version": "synthetic-schema-demo", "model_provider": "synthetic-provider"}, 0),
        row("turn_context", {"model": "synthetic-model", "turn_id": "synthetic-turn"}, 0),
        row("response_item", {"type": "message", "role": "assistant", "phase": "commentary",
                              "content": [{"type": "output_text", "text": "I will inspect the synthetic local fixture."}]}, 1),
        row("response_item", {"type": "function_call", "id": "synthetic-call-item", "call_id": "synthetic-call-1",
                              "name": "inspect_fixture", "arguments": '{"path":"fixture.json"}'}, 2),
        row("response_item", {"type": "function_call_output", "id": "synthetic-result-item",
                              "call_id": "synthetic-call-1", "output": '{"ok":true,"value":42}'}, 3),
        row("response_item", {"type": "custom_tool_call", "id": "synthetic-custom-item", "call_id": "synthetic-call-2",
                              "name": "check_fixture", "input": "assert fixture.value == 42"}, 4),
        row("response_item", {"type": "custom_tool_call_output", "id": "synthetic-custom-result",
                              "call_id": "synthetic-call-2", "output": "Synthetic check passed."}, 5),
        row("response_item", {"type": "message", "role": "assistant", "phase": "final_answer",
                              "content": [{"type": "output_text", "text": "The synthetic fixture contains value 42."}]}, 6),
    ]


def attributes(**values):
    return [{"key": key.replace("__", "."), "value": {"stringValue": value}} for key, value in values.items()]


def otlp_document():
    trace = "00000000000000000000000000000042"
    model_span, tool_span = "0000000000000001", "0000000000000002"
    # Fixed synthetic epoch in decimal nanoseconds, not a runtime clock read.
    base = 1792324800000000000
    def nano(second):
        return str(base + second * 1_000_000_000 + 42)
    spans = [
        {"traceId": trace, "spanId": model_span, "name": "chat synthetic-model", "kind": 3,
         "startTimeUnixNano": nano(1), "endTimeUnixNano": nano(4), "status": {"code": 1},
         "attributes": attributes(gen_ai__operation__name="chat", gen_ai__provider__name="synthetic-provider",
                                  gen_ai__request__model="synthetic-model", gen_ai__input__messages="Inspect synthetic fixture.",
                                  gen_ai__output__messages="Synthetic fixture contains value 42.")},
        {"traceId": trace, "spanId": tool_span, "parentSpanId": model_span, "name": "execute_tool inspect_fixture",
         "kind": 1, "startTimeUnixNano": nano(2), "endTimeUnixNano": nano(3), "status": {"code": 1},
         "attributes": attributes(gen_ai__operation__name="execute_tool", gen_ai__tool__name="inspect_fixture",
                                  gen_ai__tool__call__id="synthetic-call-1", gen_ai__tool__call__arguments='{"path":"fixture.json"}',
                                  gen_ai__tool__call__result='{"ok":true,"value":42}')},
    ]
    resource = {"attributes": attributes(service__name="synthetic-demo-agent")}
    logs = [{"timeUnixNano": nano(5), "traceId": trace, "spanId": model_span,
             "body": {"stringValue": "Synthetic completion log: value 42."},
             "attributes": attributes(event__name="gen_ai.content.completion")}]
    return {"resourceSpans": [{"resource": resource, "scopeSpans": [{"scope": {"name": "synthetic.demo", "version": "1"}, "spans": spans}]}],
            "resourceLogs": [{"resource": resource, "scopeLogs": [{"scope": {"name": "synthetic.demo", "version": "1"}, "logRecords": logs}]}]}


def orcareplay_files():
    """Newly authored orca-trace-shaped evidence; no upstream data is read."""
    result = b'{"synthetic":true,"value":42}\n'
    diff = b"--- a/fixture.json\n+++ b/fixture.json\n@@ -1 +1 @@\n-{\"value\":0}\n+{\"value\":42}\n"
    blobs = {}
    def blob(raw, media_type):
        digest = hashlib.sha256(raw).hexdigest()
        blobs["blobs/" + digest[:2] + "/" + digest] = raw
        return {"$blob": "sha256:" + digest, "bytes": len(raw), "media_type": media_type}
    result_ref = blob(result, "application/json")
    diff_ref = blob(diff, "text/x-diff")
    rows = []
    def event(kind, payload, *, attrs=None, causes=None):
        seq = len(rows)
        row = {"seq": seq, "ts": timestamp(seq), "mono_us": seq * 1_000_000,
               "turn": 0, "type": kind, "actor": {"model.request": "agent", "model.response": "model",
                   "tool.call": "agent", "tool.result": "tool"}.get(kind, "orca"), "payload": payload}
        if attrs is not None:
            row["attrs"] = attrs
        if causes is not None:
            row["causes"] = causes
        rows.append(row)
    event("run.start", {"label": "Synthetic OrcaReplay evidence inspection", "synthetic": True})
    event("model.request", {"model": "synthetic-model", "messages": [
        {"role": "user", "content": "Inspect the synthetic fixture and summarize the recorded diff."}]})
    event("tool.call", {"name": "inspect_fixture", "arguments": {"path": "fixture.json"}},
          attrs={"call_id": "synthetic-call-1", "name": "inspect_fixture", "input": {"path": "fixture.json"}}, causes=[1])
    event("tool.result", result_ref, attrs={"call_id": "synthetic-call-1", "name": "inspect_fixture"}, causes=[2])
    event("fs.snapshot", {"label": "Synthetic snapshot metadata only"},
          attrs={"tree": "synthetic-tree-not-a-restorable-git-object", "files": [{"path": "fixture.json"}]})
    event("fs.change", diff_ref, attrs={"path": "fixture.json", "change": "modified"})
    event("model.response", {"role": "assistant", "model": "synthetic-model", "content": "The synthetic report contains value 42; the diff records its change from 0."}, causes=[3, 5])
    event("run.end", {"status": "completed", "synthetic": True})
    events = json_lines(rows)
    manifest = {"schema_version": "0.3.0", "run_id": "run_abcd12", "created_at": timestamp(0),
                "orca_version": "synthetic-schema-demo", "argv": ["synthetic-agent"], "cwd": "/synthetic/workspace", "adapter": {"id": "synthetic", "version": "0.1"},
                "counts": {"events": len(rows), "blobs": len(blobs)}, "integrity": {"events_sha256": hashlib.sha256(events).hexdigest(), "blob_count": len(blobs)},
                "provenance": {"synthetic": True, "license": "Apache-2.0", "description": "Newly authored constants; no upstream sample or runtime capture."}}
    return {"manifest.json": encoded(manifest), "events.jsonl": events, **blobs}


def plain_tar(files):
    # Fixed metadata keeps account names, local paths and runtime clocks out.
    archive_bytes = io.BytesIO()
    with tarfile.open(fileobj=archive_bytes, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, raw in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(raw), 0o644, 0
            archive.addfile(info, io.BytesIO(raw))
    return archive_bytes.getvalue()


def native_zip(files):
    """Deterministic native ZIP packaging of the authored trajectory only."""
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, raw in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, raw)
    return archive_bytes.getvalue()


def demo_files():
    artex = artex_files()
    files = {"artex-local-inspection/" + name: raw for name, raw in artex.items()}
    # Deterministic plain tar works without installing zstd.
    files["artex-local-inspection.tar"] = plain_tar(artex)
    orcareplay = orcareplay_files()
    files.update({"experimental-orcareplay/" + name: raw for name, raw in orcareplay.items()})
    files["experimental-orcareplay.tar"] = plain_tar(orcareplay)
    files["experimental-orcareplay.orca.zip"] = native_zip(orcareplay)
    files["experimental-codex-rollout.jsonl"] = json_lines(codex_rows())
    files["experimental-otlp.json"] = encoded(otlp_document())
    return files


def generate(destination):
    root = Path(destination)
    for name, raw in demo_files().items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", nargs="?", type=Path, default=Path(__file__).resolve().parent)
    generate(parser.parse_args().destination)
