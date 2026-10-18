#!/usr/bin/env python3
"""Opt-in offline validation of an actual Artex capture export, outside Git.

This imports existing evidence and renders observed stages; it makes no provider
calls and performs no replay. The export must come from an independently run
Artex harness/endpoint. A passing report establishes recording/import behavior,
not security-task success or model performance.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.artex.importer import import_artex
from analysis.narrative import build_observed_timeline
from core.bundle import load_bundle
from web.render import render_timeline


def _outside_repository(path):
    resolved = Path(path).resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise ValueError("capture evidence and validation output must remain outside the AgentStoryline repository")
    # Keep the supplied source path for the importer's no-symlink checks.
    return Path(path).absolute()


def validate_capture(source, destination, *, require_nontext=True):
    source = _outside_repository(source)
    destination = _outside_repository(destination)
    destination.mkdir(parents=True, exist_ok=False)
    bundle = import_artex(source, destination / "bundle")
    # Re-open validates the published source artifacts and every checksum.
    bundle = load_bundle(destination / "bundle")
    export = bundle["manifest"]["source"].get("recording_export", {})
    if (not isinstance(export, dict) or export.get("schema_version") != 1 or export.get("read_only") is not True or
            export.get("consistency") != "repeatable_read" or
            export.get("completeness", {}).get("status") != "partial"):
        raise ValueError("expected a v1 read-only, repeatable-read, explicitly partial export")
    events = {event["id"]: event for event in bundle["events"]}
    starts = [event for event in events.values() if event["kind"] == "tool.start" and
              event.get("data", {}).get("correlation_basis") == "explicit_model_call_id_and_session_id"]
    if not starts:
        raise ValueError("capture contains no explicitly correlated model/tool invocation")
    checks = []
    nontext = []
    for start in starts:
        capture = start["data"]["recording_metadata"]
        dependencies = start.get("dependencies", [])
        if len(dependencies) != 1 or dependencies[0]["provenance"] != "recorded":
            raise ValueError("tool invocation does not have one recorded model-request dependency")
        request = events[dependencies[0]["event_id"]]
        response = events[start["data"]["model_response_event_id"]]
        if request["kind"] != "model.request" or response["kind"] != "model.response":
            raise ValueError("model correlation resolves to incorrect event kinds")
        for model_event in (request, response):
            metadata = model_event["data"].get("recording_metadata", {})
            if (metadata.get("model_call_id") != capture.get("model_call_id") or
                    metadata.get("session_id") != capture.get("session_id") or
                    metadata.get("recording_enabled") is not True):
                raise ValueError("model/tool capture identities do not agree")
        if start["data"].get("arguments") != capture.get("tool_input"):
            raise ValueError("structured tool input was not retained")
        response_body = response["data"].get("response", {})
        response_blocks = response_body.get("content", []) if isinstance(response_body, dict) else []
        if not isinstance(response_blocks, list):
            raise ValueError("normalized model response content is not an array")
        matching_blocks = [block for block in response_blocks if isinstance(block, dict) and
                           block.get("type") == "tool_use" and block.get("id") == start["call_id"]]
        if len(matching_blocks) != 1 or matching_blocks[0].get("input") != capture.get("tool_input"):
            raise ValueError("normalized model response does not retain the matching full tool-use input")
        results = [event for event in events.values() if event["kind"] == "tool.end" and
                   {"event_id": start["id"], "provenance": "recorded"} in event.get("dependencies", [])]
        if len(results) != 1:
            raise ValueError("expected exactly one explicitly correlated tool result")
        result = results[0]
        result_capture = result["data"].get("recording_metadata", {})
        block = result_capture.get("tool_result")
        if not isinstance(block, dict) or result["data"].get("result") != block:
            raise ValueError("full structured tool-result content block was not retained")
        result_blocks = block.get("content", [])
        if not isinstance(result_blocks, list):
            raise ValueError("tool-result content is not an array")
        for item in result_blocks:
            if isinstance(item, dict) and item.get("type") not in (None, "text"):
                nontext.append({"event_id": result["id"], "type": item["type"]})
        if request["data"].get("timestamp_basis") != "recorded_request_started_at" or response["data"].get("timestamp_basis") != "recorded_response_ended_at":
            raise ValueError("explicit capture timestamps were not mapped")
        checks.append({"model_request_id": request["id"], "model_response_id": response["id"],
                       "tool_start_id": start["id"], "tool_end_id": result["id"],
                       "model_call_id": capture["model_call_id"], "tool_use_id": start["call_id"],
                       "tool_started_before_response_completed": start["sequence"] < response["sequence"]})
    if require_nontext and not nontext:
        raise ValueError("expected at least one synthetic non-text tool-result block in this integration capture")
    observed = build_observed_timeline(bundle)
    render_timeline(bundle, observed, destination / "timeline.html")
    report = {"status": "passed", "method": "offline recording/import validation",
              "source": str(source), "run_id": bundle["manifest"]["run_id"], "event_count": len(events),
              "checks": checks, "nontext_result_blocks": nontext,
              "source_manifest_artifact": next(ref for ref in bundle["manifest"]["artifact_refs"] if ref["path"].endswith("/manifest.json")),
              "source_artifacts": bundle["manifest"]["artifact_refs"], "recording_export": export,
              "completeness": bundle["manifest"]["completeness"],
              "scope": "Observed capture behavior only; no provider call, security-task evaluation or replay was performed."}
    for name, value in (("observed.json", observed), ("validation-report.json", report)):
        with (destination / name).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--allow-text-only", action="store_true", help="Relax the integration fixture's non-text block requirement")
    args = parser.parse_args()
    try:
        report = validate_capture(args.source, args.destination, require_nontext=not args.allow_text_only)
        print(json.dumps({"status": report["status"], "run_id": report["run_id"], "event_count": report["event_count"],
                          "explicit_model_tool_pairs": len(report["checks"]), "destination": str(args.destination.resolve())}, indent=2))
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"Capture validation failed: {exc}\n")


if __name__ == "__main__":
    main()
