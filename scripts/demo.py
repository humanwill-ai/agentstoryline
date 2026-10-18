#!/usr/bin/env python3
"""Newly authored synthetic demo; creates only its own loopback HTTP target."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.artex.importer import import_artex
from analysis.narrative import build_observed_timeline, import_analysis_response
from core.bundle import load_bundle
from replay.runner import run_plan, export_plan
from web.render import render_timeline


def run_demo(destination):
    output = Path(destination).absolute()
    output.mkdir(parents=True, exist_ok=False)
    import_artex(ROOT / "tests/fixtures/artex/token_session", output / "bundle")
    bundle = load_bundle(output / "bundle")
    narrative_file = ROOT / "tests/fixtures/analysis/artex-token-narrative.json"
    if narrative_file.exists():
        narrative = import_analysis_response(bundle, json.loads(narrative_file.read_text()), model="gpt-6.1-sol", provider="development-worker; offline authored analysis, no provider API integration")
    else:
        narrative = build_observed_timeline(bundle)
    (output / "analysis.json").write_text(json.dumps(narrative, indent=2) + "\n")
    render_timeline(bundle, narrative, output / "timeline.html")
    issued, received = [], []

    class Target(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == "/session":
                token = secrets.token_hex(16)
                issued.append(token)
                value, status = {"token": token}, 200
            elif self.path == "/protected":
                authorization = self.headers.get("Authorization", "")
                received.append(authorization)
                valid = bool(issued) and authorization == "Bearer " + issued[-1]
                value, status = {"ok": valid, "synthetic": True}, 200 if valid else 403
            else:
                value, status = {"error": "outside demo paths"}, 404
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    plan = {
        "schema_version": "0.1", "run_id": bundle["manifest"]["run_id"],
        "allowed_origins": [origin],
        "source": {"kind": "authored-synthetic-demonstration", "note": "Manual mapping from synthetic Artex Bash observations to supported HTTP operations; no automatic shell conversion."},
        "steps": [
            {"id": "session", "operation": "http.request", "method": "GET", "url": origin + "/session", "expect": {"status": 200}, "extract": {"token": {"source": "json", "path": ["token"]}}, "evidence_refs": ["artex.activity.11", "artex.activity.12"], "provenance": "authored"},
            {"id": "protected", "operation": "http.request", "method": "GET", "url": origin + "/protected", "headers": {"Authorization": {"template": "Bearer {token}", "bindings": {"token": {"$ref": "session.token"}}}}, "expect": {"status": 200}, "extract": {"ok": {"source": "json", "path": ["ok"]}}, "evidence_refs": ["artex.activity.13", "artex.activity.14"], "dependencies": [{"event_id": "artex.activity.12", "provenance": "inferred"}], "provenance": "authored"},
        ],
    }
    plan_path = output / "plan.json"
    plan_path.write_text(json.dumps(plan, indent=2) + "\n")
    try:
        first = run_plan(plan_path, output / "replay-1", allow_execute=True)
        second = run_plan(plan_path, output / "replay-2", allow_execute=True)
        passed = first["status"] == second["status"] == "passed" and len(issued) == len(received) == 2 and issued[0] != issued[1] and received == ["Bearer " + token for token in issued]
        for folder in ("replay-1", "replay-2"):
            passed = passed and json.loads((output / folder / "artifacts/protected.response.bin").read_text())["ok"] is True
        exported = export_plan(plan_path, output / "replay-package")
        result = {"status": "passed" if passed else "failed", "synthetic": True, "fixture_origin": "newly authored development data, not a real Artex run", "replay_plan_origin": "manually authored supported HTTP mapping", "fresh_token_dependency_verified": passed, "replays": 2, "events": len(bundle["events"]), "timeline": str(output / "timeline.html"), "exported_package": exported["output_dir"], "note": "Demo target stops on completion; exported plan must be configured for another explicitly prepared local target before execution."}
        (output / "demo-result.json").write_text(json.dumps(result, indent=2) + "\n")
        if not passed:
            raise RuntimeError("fresh-token replay assertion failed; inspect retained artifacts")
        return result
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", help="New directory for demo artifacts")
    args = parser.parse_args()
    print(json.dumps(run_demo(args.output), indent=2))
