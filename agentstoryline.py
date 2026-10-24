#!/usr/bin/env python3
"""Local entry point for AgentStoryline. Provider calls require explicit generation in the optional local explanation service."""
import argparse
import json
import sys
from pathlib import Path

from agentstoryline_version import __version__


def write_json_new(path, value):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"AgentStoryline {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Validate a recording bundle")
    validate.add_argument("bundle")
    importer = commands.add_parser("import-artex", help="Import a supported Artex export")
    importer.add_argument("source")
    importer.add_argument("destination")
    coverage = commands.add_parser("artex-coverage", help="Inventory Artex source/import/display coverage without executing evidence")
    coverage.add_argument("sources", nargs="*")
    coverage.add_argument("--scenarios", help="JSON array declaring labels, source, optional bundle, execution_id, source_pin and context")
    coverage.add_argument("--output", required=True)
    codex_importer = commands.add_parser("import-codex", help="Import a supported native Codex rollout or declared subset")
    codex_importer.add_argument("source")
    codex_importer.add_argument("destination")
    otlp_importer = commands.add_parser("import-otlp", help="Import a supported offline OTLP protobuf-JSON trace/log export")
    otlp_importer.add_argument("source")
    otlp_importer.add_argument("destination")
    orca_importer = commands.add_parser("import-orcareplay", help="Import an experimental OrcaReplay run directory, .orca ZIP, or plain tar")
    orca_importer.add_argument("source", help="Run directory or native ZIP (.orca, .orca.zip), or plain tar; content selects the format")
    orca_importer.add_argument("destination")
    timeline = commands.add_parser("timeline", help="Render a self-contained evidence viewer")
    timeline.add_argument("bundle")
    timeline.add_argument("output")
    timeline.add_argument("--analysis", help="Validated analysis response JSON")
    timeline.add_argument("--plan", help="Evidence-linked replay plan JSON; display only, never execute")
    timeline.add_argument("--view", choices=("flow", "evidence"), default="flow", help="Chat/storyline (default) or technical evidence viewer")
    timeline.add_argument("--sound", help="Optional local notification PCM WAV, embedded in the offline flow report")
    timeline.add_argument("--recording-output", help="Export portable .traceforge.json for UI Open recording")
    timeline.add_argument("--replay-script", action="append", default=[], help="Attach local .py for inspection/download only; requires --recording-output")
    request = commands.add_parser("analysis-request", help="Export an LLM analysis request; never sends it")
    request.add_argument("bundle")
    request.add_argument("output")
    request.add_argument("--model", required=True, help="Model identifier to include in the exported request")
    response = commands.add_parser("analysis-import", help="Validate an externally obtained analysis response")
    response.add_argument("bundle")
    response.add_argument("response")
    response.add_argument("output")
    response.add_argument("--model", required=True, help="Caller-declared model used to produce this response")
    response.add_argument("--provider", help="Caller-declared analysis provider")
    replay = commands.add_parser("replay", help="Validate or explicitly run supported local HTTP replay")
    replay.add_argument("plan")
    replay.add_argument("output")
    replay.add_argument("--execute", action="store_true", help="Explicitly allow local HTTP execution")
    export = commands.add_parser("export-replay", help="Export a validated standalone Python replay package")
    export.add_argument("plan")
    export.add_argument("destination")
    proposal = commands.add_parser("replay-request", help="Export an offline request for an evidence-linked replay proposal")
    proposal.add_argument("bundle")
    proposal.add_argument("output")
    proposal.add_argument("--allowed-origin", action="append", required=True, help="Exact prepared loopback origin, including port")
    accept = commands.add_parser("replay-import", help="Validate an LLM replay proposal against its source recording")
    accept.add_argument("bundle")
    accept.add_argument("response")
    accept.add_argument("output")
    accept.add_argument("--allowed-origin", action="append", required=True, help="Operator-approved origin; must match the proposal scope")
    workflow_request = commands.add_parser("workflow-request", help="Export paired explanation/replay requests; never sends them")
    workflow_request.add_argument("bundle")
    workflow_request.add_argument("destination")
    workflow_request.add_argument("--model", required=True)
    workflow_request.add_argument("--allowed-origin", action="append", required=True)
    workflow_import = commands.add_parser("workflow-import", help="Validate generated explanation/replay and export a report/package; never executes")
    workflow_import.add_argument("bundle")
    workflow_import.add_argument("request_dir")
    workflow_import.add_argument("analysis_response")
    workflow_import.add_argument("replay_response")
    workflow_import.add_argument("destination")
    workflow_import.add_argument("--provider", required=True, help="Caller-declared model provider")
    workflow_import.add_argument("--generation-method", required=True, choices=["offline-development-worker", "external-provider-response"])
    explain_request = commands.add_parser("explanation-request", help="Export bounded read-only descriptions request from a portable recording")
    explain_request.add_argument("recording")
    explain_request.add_argument("output")
    explain_request.add_argument("--message-id", action="append", required=True, help="Selected flow message ID; repeat up to 40 times")
    explain_request.add_argument("--model", required=True, help="Caller-declared offline model label; no provider call")
    explain_request.add_argument("--include-milestones", action="store_true", help="Request optional evidence-linked AI-inferred milestones alongside descriptions")
    explain_import = commands.add_parser("explanation-import", help="Validate offline descriptions and render a preserved portable recording")
    explain_import.add_argument("recording")
    explain_import.add_argument("response")
    explain_import.add_argument("output")
    explain_import.add_argument("--recording-output", required=True)
    explain_import.add_argument("--state-dir", help="Optional local annotation history directory")
    explain_serve = commands.add_parser("explanation-serve", help="Serve a local viewer with explicitly requested provider descriptions")
    explain_serve.add_argument("recording")
    explain_serve.add_argument("--state-dir", default="work/explanations")
    explain_serve.add_argument("--port", type=int, default=0)
    viewer = commands.add_parser("viewer", help="Open a supported native agent export or portable recording in the local viewer")
    viewer.add_argument("source", nargs="+", help="One recording, or multiple OrcaRouter .orca/.orca.zip files for a combined view. Portable JSON, Artex export, OrcaReplay directory/.orca/.orca.zip/plain tar, Codex rollout JSONL, or OTLP JSON; content selects the format")
    viewer.add_argument("--state-dir", default="work/explanations")
    viewer.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        if args.command == "viewer":
            from web.viewer_import import normalize_source, normalize_orca_collection
            from analysis.explanation_service import serve
            _package, recording = (normalize_source(args.source[0], args.state_dir) if len(args.source) == 1
                                   else normalize_orca_collection(args.source, args.state_dir))
            serve(recording, args.state_dir, args.port)
        elif args.command == "explanation-serve":
            from analysis.explanation_service import serve
            serve(args.recording, args.state_dir, args.port)
        elif args.command == "explanation-request":
            from analysis.explanation_workflow import export_explanation_request
            print(json.dumps(export_explanation_request(args.recording, args.output, message_ids=args.message_id, model=args.model, include_milestones=args.include_milestones), indent=2))
        elif args.command == "explanation-import":
            from analysis.explanation_workflow import import_explanation_response
            print(json.dumps(import_explanation_response(args.recording, args.response, args.output, recording_output=args.recording_output, state_dir=args.state_dir), indent=2))
        elif args.command == "artex-coverage":
            from adapters.artex.coverage import comparison_report, write_report
            if args.scenarios:
                from adapters.artex.importer import _json, _read
                scenarios = _json(_read(Path(args.scenarios)))
                if not isinstance(scenarios, list):
                    raise ValueError("scenario configuration must be an array")
            else:
                scenarios = [{"label": Path(source).name, "source": source} for source in args.sources]
            if not scenarios:
                raise ValueError("provide sources or --scenarios")
            write_report(comparison_report(scenarios), args.output)
            print(f"Coverage report: {args.output}")
        elif args.command == "import-artex":
            from adapters.artex.importer import import_artex
            import_artex(args.source, args.destination)
            print(f"Imported recording: {args.destination}")
        elif args.command == "import-codex":
            from adapters.codex.importer import import_codex
            import_codex(args.source, args.destination)
            print(f"Imported recording: {args.destination}")
        elif args.command == "import-orcareplay":
            from adapters.orcareplay import import_orcareplay
            import_orcareplay(args.source, args.destination)
            print(f"Imported recording: {args.destination}")
        elif args.command == "import-otlp":
            from adapters.otlp import import_otlp
            import_otlp(args.source, args.destination)
            print(f"Imported recording: {args.destination}")
        elif args.command == "workflow-request":
            from analysis.workflow import export_generation_requests
            result = export_generation_requests(args.bundle, args.destination, model=args.model, allowed_origins=args.allowed_origin)
            print(json.dumps(result, indent=2))
        elif args.command == "workflow-import":
            from analysis.workflow import import_generation_responses
            result = import_generation_responses(args.bundle, args.request_dir, args.analysis_response, args.replay_response, args.destination,
                provider=args.provider, generation_method=args.generation_method)
            print(json.dumps(result, indent=2))
        elif args.command == "replay":
            from replay.runner import run_plan, validate_plan
            from core.bundle import read_json
            if not args.execute:
                plan = validate_plan(read_json(Path(args.plan).read_text()))
                print(json.dumps({"status": "validated", "run_id": plan["run_id"], "steps": len(plan["steps"]), "execution": "not requested"}, indent=2))
                return 0
            result = run_plan(args.plan, args.output, allow_execute=args.execute)
            print(json.dumps(result, indent=2))
            return 0 if result.get("status") == "passed" else 1
        elif args.command == "export-replay":
            from replay.runner import export_plan
            print(json.dumps(export_plan(args.plan, args.destination), indent=2))
        else:
            from core.bundle import load_bundle
            from analysis.narrative import build_observed_timeline, build_analysis_request, import_analysis_response
            from core.bundle import read_json
            bundle = load_bundle(args.bundle)
            if args.command == "validate":
                print(json.dumps({"run_id": bundle["manifest"]["run_id"], "events": len(bundle["events"]), "completeness": bundle["manifest"]["completeness"]}, indent=2))
            elif args.command == "analysis-request":
                write_json_new(args.output, build_analysis_request(bundle, model=args.model))
                print(f"Analysis request exported; no model call made: {args.output}")
            elif args.command == "analysis-import":
                value = read_json(Path(args.response).read_text())
                write_json_new(args.output, import_analysis_response(bundle, value, model=args.model, provider=args.provider))
                print(f"Analysis validated: {args.output}")
            elif args.command == "timeline":
                from web.render import render_timeline
                from analysis.narrative import validate_narrative
                narrative = validate_narrative(bundle, read_json(Path(args.analysis).read_text())) if args.analysis else build_observed_timeline(bundle)
                plan = read_json(Path(args.plan).read_text()) if args.plan else None
                render_timeline(bundle, narrative, args.output, replay_plan=plan, view=args.view, notification_sound=args.sound, recording_output=args.recording_output, replay_scripts=args.replay_script)
                print(f"Evidence viewer: {Path(args.output).resolve()}")
            elif args.command == "replay-request":
                from replay.runner import export_proposal_request
                export_proposal_request(bundle, args.output, allowed_origins=args.allowed_origin)
                print(f"Replay proposal request exported; no model call made: {args.output}")
            elif args.command == "replay-import":
                from replay.runner import validate_proposal
                plan = validate_proposal(bundle, read_json(Path(args.response).read_text()), allowed_origins=args.allowed_origin)
                write_json_new(args.output, plan)
                print(f"Replay proposal validated, not executed: {args.output}")
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"AgentStoryline: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
