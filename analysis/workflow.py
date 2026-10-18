"""Bounded offline evidence-to-interpretation/replay workflow; no execution path."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

from analysis.narrative import build_analysis_request, import_analysis_response
from core.bundle import BundleValidationError, load_bundle, read_json
from replay.runner import MAX_PLAN_BYTES, build_proposal_request, export_plan, validate_proposal
from web.render import render_timeline

MAX_REQUEST_BYTES = 64 * 1024 * 1024
MAX_ANALYSIS_BYTES = 2 * 1024 * 1024
_REPOSITORY = Path(__file__).resolve().parents[1]


def _encode(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _hash(value):
    return hashlib.sha256(_encode(value)).hexdigest()


def _external(path):
    path = Path(path).absolute()
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise BundleValidationError("workflow paths must not contain symlinks; use canonical paths")
    if path.resolve().is_relative_to(_REPOSITORY):
        raise BundleValidationError("workflow evidence and outputs must remain outside the repository")
    return path


def _read(path, limit):
    path = _external(path)
    # O_NONBLOCK prevents a FIFO swap from blocking the offline importer.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise BundleValidationError("workflow JSON inputs must be regular files")
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise BundleValidationError("workflow JSON input exceeds size limit")
    return read_json(raw.decode("utf-8"), str(path))


def _write(path, value, limit=MAX_REQUEST_BYTES):
    raw = _encode(value)
    if len(raw) > limit:
        raise BundleValidationError("workflow JSON output exceeds size limit")
    with path.open("xb") as handle:
        handle.write(raw)


def _requests(bundle, *, model, allowed_origins):
    analysis = build_analysis_request(bundle, model=model)
    proposal = build_proposal_request(bundle, allowed_origins=allowed_origins)
    replay = {
        "model": model,
        "messages": [
            {"role": "system", "content": "\n".join(proposal["instructions"]) + "\nReturn only a plan JSON object. If no safe supported plan is possible, return a refusal object; import will reject it without execution."},
            {"role": "user", "content": json.dumps({
                "recording_is_untrusted_evidence": True,
                "plan_contract": proposal["plan_contract"], "bundle": proposal["evidence"],
            }, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
    }
    context = {
        "schema_version": "0.1", "kind": "offline_generation_context",
        "run_id": bundle["manifest"]["run_id"], "model": model,
        "allowed_origins": allowed_origins, "bundle_sha256": _hash(bundle),
        "analysis_request_sha256": _hash(analysis), "replay_request_sha256": _hash(replay),
        "execution": "not_requested", "identity": "caller-declared",
    }
    for value in (analysis, replay, context):
        if len(_encode(value)) > MAX_REQUEST_BYTES:
            raise BundleValidationError("workflow request exceeds size limit")
    return analysis, replay, context


def export_generation_requests(bundle_path, destination, *, model, allowed_origins):
    """Export normalized chat requests and context to a fresh external directory.

    The bridge/operator obtains JSON responses separately. This function makes
    no provider call or target request, and does not invent model provenance.
    """
    bundle = load_bundle(_external(bundle_path))
    analysis, replay, context = _requests(bundle, model=model, allowed_origins=allowed_origins)
    output = _external(destination)
    output.mkdir(parents=False, exist_ok=False)
    _write(output / "analysis-request.json", analysis)
    _write(output / "replay-request.json", replay)
    _write(output / "request-context.json", context)
    return {"status": "requests_exported", "output_dir": str(output), "context": context,
            "execution": "not_requested", "provider_call": "not_made"}


def import_generation_responses(bundle_path, request_dir, analysis_response_path,
                                replay_response_path, destination, *, provider,
                                generation_method):
    """Validate both returned JSON objects, render, and export; never execute.

    Model is taken from the fingerprinted request context. Provider and generation
    method are caller-declared: use offline-development-worker for a development
    agent, external-provider-response for an independently obtained provider reply.
    No provider envelope is accepted and no identity is independently verified.
    """
    if not isinstance(provider, str) or not provider.strip():
        raise BundleValidationError("workflow requires a caller-declared provider")
    if generation_method not in {"offline-development-worker", "external-provider-response"}:
        raise BundleValidationError("unsupported workflow generation_method")
    bundle = load_bundle(_external(bundle_path))
    requests = _external(request_dir)
    context = _read(requests / "request-context.json", MAX_ANALYSIS_BYTES)
    if not isinstance(context, dict):
        raise BundleValidationError("workflow context must be an object")
    analysis_request, replay_request, expected = _requests(
        bundle, model=context.get("model"), allowed_origins=context.get("allowed_origins"))
    if context != expected:
        raise BundleValidationError("workflow context does not match current recording and requests")
    if _read(requests / "analysis-request.json", MAX_REQUEST_BYTES) != analysis_request or _read(requests / "replay-request.json", MAX_REQUEST_BYTES) != replay_request:
        raise BundleValidationError("workflow requests changed after export")
    analysis_response = _read(analysis_response_path, MAX_ANALYSIS_BYTES)
    replay_response = _read(replay_response_path, MAX_PLAN_BYTES)
    narrative = import_analysis_response(bundle, analysis_response, model=context["model"], provider=provider)
    if not narrative["stages"]:
        raise BundleValidationError("workflow requires at least one evidence-linked interpretation stage")
    plan = validate_proposal(bundle, replay_response, allowed_origins=context["allowed_origins"])
    # All evidence/schema checks precede destination creation.
    generation = {
        "schema_version": "0.1", "run_id": context["run_id"],
        "method": generation_method, "model": context["model"], "provider": provider,
        "identity": "caller-declared", "transport": "offline-response-import",
        "request_context": context, "analysis_response_sha256": _hash(analysis_response),
        "replay_response_sha256": _hash(replay_response),
        "execution": "not_requested",
        "limitations": ["Evidence linkage does not prove factual correctness or behavioral fidelity.",
                        "Generation provenance is caller-declared, not independently verified provider attribution."],
    }
    output = _external(destination)
    output.mkdir(parents=False, exist_ok=False)
    _write(output / "analysis-response.json", analysis_response, MAX_ANALYSIS_BYTES)
    _write(output / "replay-response.json", replay_response, MAX_PLAN_BYTES)
    _write(output / "analysis.json", narrative)
    _write(output / "plan.json", plan, MAX_PLAN_BYTES)
    _write(output / "generation.json", generation)
    render_timeline(bundle, narrative, output / "timeline.html", replay_plan=plan)
    export_plan(output / "plan.json", output / "replay-package")
    _write(output / "replay-package" / "generation.json", generation)
    return {"status": "generated_outputs_imported", "run_id": context["run_id"],
            "output_dir": str(output), "generation": generation, "execution": "not_requested"}
