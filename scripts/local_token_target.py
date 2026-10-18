#!/usr/bin/env python3
"""Disposable loopback-only target for AgentStoryline recording/replay checks.

This deliberately small synthetic service is not a security benchmark. Every
session request invalidates the previous token; reset invalidates all state.
Runtime files, including synthetic tokens, must be written outside the repo.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import threading
import time


def serve(destination):
    destination = Path(destination).resolve()
    root = Path(__file__).resolve().parents[1]
    if destination == root or root in destination.parents:
        raise ValueError("target runtime files must remain outside the repository")
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    lock = threading.Lock()
    state = {"token": None, "generation": 0, "phase": "initial"}
    log = (destination / "requests.jsonl").open("x", encoding="utf-8")
    os.chmod(destination / "requests.jsonl", 0o600)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_request(self):
            with lock:
                authorization = self.headers.get("Authorization", "")
                if self.command == "GET" and self.path == "/session":
                    state["generation"] += 1
                    state["token"] = secrets.token_urlsafe(24)
                    status, body = 200, {"token": state["token"], "generation": state["generation"]}
                elif self.command == "GET" and self.path == "/protected":
                    allowed = state["token"] is not None and secrets.compare_digest(authorization, "Bearer " + state["token"])
                    status = 200 if allowed else 401
                    body = {"ok": allowed, "marker": "traceforge-protected-v1" if allowed else "denied", "generation": state["generation"]}
                elif self.command == "POST" and self.path == "/reset":
                    state["token"] = None
                    state["generation"] += 1
                    state["phase"] = "reset-" + str(state["generation"])
                    status, body = 200, {"reset": True, "generation": state["generation"]}
                else:
                    status, body = 404, {"error": "synthetic route not found"}
                log.write(json.dumps({"observed_at_ns": time.time_ns(), "method": self.command, "path": self.path,
                                      "authorization": authorization, "status": status, "response": body,
                                      "phase": state["phase"]}) + "\n")
                log.flush()
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True

        do_GET = handle_request
        do_POST = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    meta = {"pid": os.getpid(), "origin": f"http://127.0.0.1:{server.server_port}",
            "purpose": "newly authored synthetic token dependency target", "marker": "traceforge-protected-v1"}
    (destination / "target.json").write_text(json.dumps(meta, indent=2) + "\n")
    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
        log.close()
        (destination / "stopped.json").write_text(json.dumps({"pid": os.getpid(), "stopped_at_ns": time.time_ns()}) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    serve(parser.parse_args().destination)
