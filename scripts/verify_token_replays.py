#!/usr/bin/env python3
"""Verify this synthetic token demonstration from recorded/replay artifacts.

This is an offline acceptance check for local_token_target.py, not a general
claim that a security task has been reproduced. It does not execute requests.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.bundle import load_bundle, read_json
from analysis.narrative import import_analysis_response
from analysis.workflow import _hash, _requests
from replay.runner import validate_proposal


def _artifact(directory, reference):
    directory = directory.resolve()
    path = directory / reference["path"]
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.resolve().is_relative_to(directory):
        raise ValueError("unsafe replay artifact path")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference["sha256"] or len(raw) != reference["bytes"]:
        raise ValueError("replay artifact checksum/size mismatch")
    return read_json(raw)


def verify(bundle_path, workflow, replays):
    bundle = load_bundle(bundle_path)
    workflow = Path(workflow)
    generation = read_json((workflow / "generation.json").read_text())
    if generation["method"] != "offline-development-worker":
        raise ValueError("this checkpoint requires declared offline development-worker generation")
    context = generation["request_context"]
    _, _, expected_context = _requests(bundle, model=generation["model"], allowed_origins=context["allowed_origins"])
    if context != expected_context:
        raise ValueError("generation context does not match source bundle and request contract")
    analysis_response = read_json((workflow / "analysis-response.json").read_text())
    replay_response = read_json((workflow / "replay-response.json").read_text())
    if _hash(analysis_response) != generation["analysis_response_sha256"] or _hash(replay_response) != generation["replay_response_sha256"]:
        raise ValueError("generation response fingerprint mismatch")
    expected_analysis = import_analysis_response(bundle, analysis_response, model=generation["model"], provider=generation["provider"])
    if read_json((workflow / "analysis.json").read_text()) != expected_analysis:
        raise ValueError("stored narrative does not match validated generated response")
    plan = validate_proposal(bundle, read_json((workflow / "plan.json").read_text()),
                             allowed_origins=generation["request_context"]["allowed_origins"])
    if plan != validate_proposal(bundle, replay_response, allowed_origins=context["allowed_origins"]):
        raise ValueError("stored replay plan does not match generated response")
    if len(plan["allowed_origins"]) != 1:
        raise ValueError("this demonstration requires exactly one target origin")
    origin = plan["allowed_origins"][0]
    original_tokens = []
    original_authorizations = []
    original_generations = []
    original_success = False
    for event in bundle["events"]:
        if event["kind"] != "tool.end" or event.get("tool") != "AllowlistedLocalHTTP":
            continue
        for block in event["data"].get("result", {}).get("content", []):
            if block.get("type") != "text":
                continue
            try:
                item = read_json(block["text"])
            except (KeyError, ValueError):
                continue
            request, response = item.get("request", {}), item.get("response", {})
            request = {key.lower(): value for key, value in request.items()}
            body = response.get("body", {})
            if isinstance(body, str):
                body = read_json(body)
            if request.get("method") != "GET":
                raise ValueError("original capture contains an unexpected HTTP method")
            if request.get("url") == origin + "/session" and response.get("status") == 200:
                original_tokens.append(body["token"])
                original_generations.append(body["generation"])
            if request.get("url") == origin + "/protected":
                original_authorizations.append(request.get("headers", {}).get("Authorization"))
                original_generations.append(body["generation"])
                original_success |= response.get("status") == 200 and body.get("ok") is True and body.get("marker") == "traceforge-protected-v1"
    if len(original_tokens) != 1 or original_authorizations != ["Bearer " + original_tokens[0]] or not original_success:
        raise ValueError("original capture does not establish the expected token-dependent HTTP outcome")
    if len(original_generations) != 2 or any(type(g) is not int or g <= 0 for g in original_generations) or original_generations[0] != original_generations[1]:
        raise ValueError("original token and protected response belong to different target generations")
    if original_tokens[0] in json.dumps(plan):
        raise ValueError("generated plan retained the historical token")
    if len(replays) < 2:
        raise ValueError("at least two replay outcomes are required")
    tokens, outcomes = [], []
    for location in replays:
        directory = Path(location)
        result = read_json((directory / "result.json").read_text())
        executed_plan = read_json((directory / "plan.json").read_text())
        if executed_plan != plan or result["status"] != "passed" or len(result["steps"]) != 2:
            raise ValueError("replay did not execute the validated two-step plan successfully")
        first, second = result["steps"]
        session_request, session_response = _artifact(directory, first["request"]), _artifact(directory, first["response"])
        protected_request, protected_response = _artifact(directory, second["request"]), _artifact(directory, second["response"])
        token = session_response.get("token")
        if not isinstance(token, str) or not token or token in original_tokens + tokens:
            raise ValueError("replay did not obtain a distinct fresh token")
        if session_request["url"] != origin + "/session" or protected_request["url"] != origin + "/protected" or session_request["method"] != "GET" or protected_request["method"] != "GET":
            raise ValueError("unexpected replay operations")
        if protected_request["headers"].get("Authorization") != "Bearer " + token:
            raise ValueError("replay did not use its own freshly returned token")
        if protected_response.get("ok") is not True or protected_response.get("marker") != "traceforge-protected-v1" or second.get("json_assertions_passed", 0) < 2:
            raise ValueError("protected response outcome assertions were not demonstrated")
        if first["response_status"] != 200 or second["response_status"] != 200:
            raise ValueError("unexpected replay response status")
        if any(type(g) is not int or g <= 0 for g in (session_response.get("generation"), protected_response.get("generation"))) or session_response["generation"] != protected_response["generation"]:
            raise ValueError("replay token and protected response belong to different target generations")
        tokens.append(token)
        outcomes.append({"directory": str(directory.resolve()), "fresh_token_sha256": hashlib.sha256(token.encode()).hexdigest(), "protected_outcome_verified": True})
    return {"status": "passed", "run_id": bundle["manifest"]["run_id"], "generation_method": generation["method"],
            "original_outcome_verified": True, "historical_token_excluded_from_plan": True,
            "fresh_token_dependency_verified": True, "replays": outcomes,
            "scope": "Controlled local token demonstration; no general security-performance claim."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle")
    parser.add_argument("workflow")
    parser.add_argument("replays", nargs="+")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = verify(args.bundle, args.workflow, args.replays)
    with Path(args.output).open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps(report, indent=2))
