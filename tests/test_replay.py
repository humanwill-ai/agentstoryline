"""New tests using only a synthetic literal-loopback HTTP service."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from replay.runner import (ExecutionNotAllowed, ReplayValidationError, export_plan,
                           export_proposal_request, run_plan, validate_plan, validate_proposal)


FIXTURE = Path(__file__).parent / "fixtures" / "replay" / "session_plan.json"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.server.requests.append((self.path, self.headers.get("Authorization")))
        status, data = 200, b"{}"
        if self.path == "/session":
            self.server.counter += 1
            self.server.token = f"fresh-token-{self.server.counter}"
            data = json.dumps({"token": self.server.token, "ready": True, "destination": self.server.origin + "/protected",
                               "outside": "http://127.0.0.1:1/protected", "injection": "bad\r\nHost: example.com"}).encode()
        elif self.path == "/protected":
            authorized = self.headers.get("Authorization") == f"Bearer {self.server.token}"
            status = 200 if authorized else 401
            data = json.dumps({"authorized": authorized}).encode()
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", self.server.origin + "/never")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif self.path == "/bad-status":
            status = 503
        elif self.path == "/bad-json":
            data = b"not json"
        elif self.path == "/duplicate-json":
            data = b'{"token":"first","token":"second"}'
        elif self.path == "/large":
            data = b"x" * (1024 * 1024 + 1)
        elif self.path == "/slow":
            time.sleep(0.15)
        elif self.path == "/trickle":
            self.send_response(200)
            self.send_header("Content-Length", "10")
            self.end_headers()
            try:
                for _ in range(10):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.02)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.requests.append((self.path, self.headers.get("Authorization")))
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.origin = f"http://127.0.0.1:{cls.server.server_port}"
        cls.server.counter = 0
        cls.server.token = None
        cls.server.requests = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # macOS /var resolves through a system symlink; use its canonical path.
        self.root = Path(self.temp.name).resolve()
        self.origin = self.server.origin
        self.server.requests.clear()
        self.plan = json.loads(FIXTURE.read_text().replace("http://127.0.0.1:8765", self.origin))

    def tearDown(self):
        self.temp.cleanup()

    def run_plan(self, plan=None, name="run"):
        path = self.root / (name + "-plan.json")
        path.write_text(json.dumps(plan or self.plan))
        return run_plan(path, self.root / name, allow_execute=True)

    def simple(self, endpoint, extract=False):
        step = {"id": "first", "operation": "http.request", "method": "GET", "url": self.origin + endpoint}
        if extract:
            step["extract"] = {"token": {"source": "json", "path": ["token"]}}
        return {"schema_version": "0.1", "run_id": "synthetic-test", "allowed_origins": [self.origin],
                "steps": [step, {"id": "never", "operation": "http.request", "method": "GET", "url": self.origin + "/never"}]}

    def test_fresh_tokens_used_downstream_across_runs_with_evidence(self):
        first = self.run_plan(name="first")
        token1 = self.server.token
        second = self.run_plan(name="second")
        token2 = self.server.token
        self.assertEqual((first["status"], second["status"]), ("passed", "passed"))
        self.assertNotEqual(token1, token2)
        protected = [token for path, token in self.server.requests if path == "/protected"]
        self.assertEqual(protected, [f"Bearer {token1}", f"Bearer {token2}"])
        record = second["steps"][1]
        self.assertEqual(record["provenance"], "authored")
        self.assertEqual(record["dependencies"][0]["provenance"], "inferred")
        self.assertTrue(record["evidence_refs"])
        artifact = self.root / "second" / record["response"]["path"]
        self.assertEqual(json.loads(artifact.read_bytes()), {"authorized": True})
        self.assertEqual(len((self.root / "second" / "execution.jsonl").read_text().splitlines()), 2)

    def test_execution_requires_literal_true_and_does_not_create_output(self):
        path = self.root / "plan.json"
        path.write_text(json.dumps(self.plan))
        for flag in (False, None, 1, "yes"):
            with self.subTest(flag=flag), self.assertRaises(ExecutionNotAllowed):
                run_plan(path, self.root / "output", allow_execute=flag)
        self.assertFalse((self.root / "output").exists())
        self.assertEqual(self.server.requests, [])

    def test_scope_bypass_urls_rejected_before_any_execution(self):
        invalid = ["http://localhost:123/session", "http://example.com:80/session", "https://127.0.0.1:443/",
                   "http://127.0.0.1:1/", "http://2130706433:80/", "http://127.1:80/", "http://0177.0.0.1:80/",
                   self.origin.replace("127.0.0.1", "127.0.0.1.example.com"), self.origin + "#fragment",
                   self.origin.replace("//", "//user@"), self.origin + "/bad\r\nInjected: true"]
        for url in invalid:
            plan = copy.deepcopy(self.plan)
            plan["steps"][1]["url"] = url
            with self.subTest(url=url), self.assertRaises(ReplayValidationError):
                validate_plan(plan)
        self.assertEqual(self.server.requests, [])

    def test_origins_must_be_canonical_and_exact(self):
        invalid = [self.origin + "/", self.origin + "?query", "http://localhost:123", "http://192.0.2.1:80",
                   "http://127.0.0.1", "http://127.0.0.1:00080", "http://[::ffff:127.0.0.1]:80",
                   "http://[::1%lo0]:80", "http://[::1%1]:80"]
        for origin in invalid:
            plan = copy.deepcopy(self.plan)
            plan["allowed_origins"] = [origin]
            with self.subTest(origin=origin), self.assertRaises(ReplayValidationError):
                validate_plan(plan)

    def test_no_dns_lookup_or_environment_proxy(self):
        with patch("socket.getaddrinfo", side_effect=AssertionError("DNS forbidden")), patch.dict(
            os.environ, {"http_proxy": "http://example.com:8080", "HTTP_PROXY": "http://example.com:8080", "NO_PROXY": ""}
        ):
            self.assertEqual(self.run_plan()["status"], "passed")

    def test_invalid_and_forward_references_reject_entire_plan(self):
        for ref in ("session.missing", "protected.authorized", "unknown.token", "session.token.extra", "token", "session.__class__.x"):
            plan = copy.deepcopy(self.plan)
            plan["steps"][1]["headers"] = {"Authorization": {"$ref": ref}}
            with self.subTest(ref=ref), self.assertRaises(ReplayValidationError):
                validate_plan(plan)
        plan = copy.deepcopy(self.plan)
        plan["steps"][0]["headers"] = {"Authorization": {"$ref": "protected.authorized"}}
        with self.assertRaises(ReplayValidationError):
            self.run_plan(plan)
        self.assertEqual(self.server.requests, [])
        self.assertFalse((self.root / "run").exists())

    def test_unsupported_tools_methods_and_template_code_rejected(self):
        for operation in ("Bash", "shell", "browser.navigate", "python", "http.request; whoami"):
            plan = copy.deepcopy(self.plan)
            plan["steps"][1]["operation"] = operation
            with self.subTest(operation=operation), self.assertRaises(ReplayValidationError):
                validate_plan(plan)
        for method in ("CONNECT", "TRACE", "get", ["GET"]):
            plan = copy.deepcopy(self.plan)
            plan["steps"][1]["method"] = method
            with self.subTest(method=method), self.assertRaises(ReplayValidationError):
                validate_plan(plan)
        for template in ("{token.__class__}", "{token[0]}", "{token!r}", "{token:>20}", "{0}", "{missing}"):
            plan = copy.deepcopy(self.plan)
            plan["steps"][1]["headers"]["Authorization"]["template"] = template
            with self.subTest(template=template), self.assertRaises(ReplayValidationError):
                validate_plan(plan)

    def test_static_and_dynamic_header_injection_rejected(self):
        for name, value in (("Host", "example.com"), ("Transfer-Encoding", "chunked"), ("Proxy-Authorization", "secret"),
                            ("Authorization", "bad\r\nHost: example.com"), ("Bad Name", "value")):
            plan = copy.deepcopy(self.plan)
            plan["steps"][1]["headers"] = {name: value}
            with self.subTest(name=name), self.assertRaises(ReplayValidationError):
                validate_plan(plan)
        plan = copy.deepcopy(self.plan)
        plan["steps"][0]["extract"]["injection"] = {"source": "json", "path": ["injection"]}
        plan["steps"][1]["headers"] = {"Authorization": {"$ref": "session.injection"}}
        result = self.run_plan(plan)
        self.assertEqual(result["failed_step"], "protected")
        self.assertEqual([path for path, _ in self.server.requests], ["/session"])

    def test_dynamic_scope_rechecked_before_request(self):
        plan = copy.deepcopy(self.plan)
        plan["steps"][0]["extract"]["outside"] = {"source": "json", "path": ["outside"]}
        plan["steps"][1]["url"] = {"$ref": "session.outside"}
        result = self.run_plan(plan)
        self.assertEqual(result["failed_step"], "protected")
        self.assertEqual([path for path, _ in self.server.requests], ["/session"])

    def test_url_template_and_json_body_refs(self):
        self.plan["steps"][1]["method"] = "POST"
        self.plan["steps"][1]["url"] = {"template": self.origin + "/echo?token={token}",
                                        "bindings": {"token": {"$ref": "session.token"}}}
        self.plan["steps"][1]["body"] = {"token": {"$ref": "session.token"}, "nested": [{"$ref": "session.token"}]}
        self.plan["steps"][1]["extract"] = {"token": {"source": "json", "path": ["nested", 0]}}
        result = self.run_plan()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(self.server.requests[1][0], f"/echo?token={self.server.token}")

    def test_failed_precondition_stops_without_request(self):
        self.plan["steps"][1]["preconditions"] = [{"ref": {"$ref": "session.token"}, "equals": "historical-token"}]
        result = self.run_plan()
        self.assertEqual(result["failed_step"], "protected")
        self.assertIn("precondition failed", result["steps"][1]["error"])
        self.assertEqual([path for path, _ in self.server.requests], ["/session"])

    def test_redirect_status_parse_extraction_and_response_size_failures_stop(self):
        for endpoint, extract in (("/redirect", False), ("/bad-status", False), ("/bad-json", True),
                                  ("/duplicate-json", True), ("/protected", True), ("/large", False)):
            self.server.requests.clear()
            with self.subTest(endpoint=endpoint):
                result = self.run_plan(self.simple(endpoint, extract), name=endpoint[1:])
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["failed_step"], "first")
                self.assertEqual(result["unexecuted_steps"], ["never"])
                self.assertEqual([path for path, _ in self.server.requests], [endpoint])

    def test_missing_json_extraction_path_stops(self):
        plan = self.simple("/session", True)
        plan["steps"][0]["extract"]["token"]["path"] = ["missing"]
        result = self.run_plan(plan)
        self.assertIn("extraction path", result["steps"][0]["error"])
        self.assertEqual(result["unexecuted_steps"], ["never"])

    def test_operation_timeout_stops(self):
        with patch("replay.runner.TIMEOUT_SECONDS", 0.03):
            result = self.run_plan(self.simple("/slow"))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["unexecuted_steps"], ["never"])

    def test_total_deadline_is_not_reset_by_trickling_response(self):
        start = time.monotonic()
        with patch("replay.runner.TIMEOUT_SECONDS", 0.06):
            result = self.run_plan(self.simple("/trickle"))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["unexecuted_steps"], ["never"])
        self.assertLess(time.monotonic() - start, 0.16)

    def test_output_never_clobbers_existing_directory_or_symlink(self):
        output = self.root / "run"
        output.mkdir()
        sentinel = output / "keep.txt"
        sentinel.write_text("original")
        with self.assertRaises(FileExistsError):
            self.run_plan()
        self.assertEqual(sentinel.read_text(), "original")
        alias = self.root / "alias"
        alias.symlink_to(output, target_is_directory=True)
        path = self.root / "plan.json"
        path.write_text(json.dumps(self.plan))
        with self.assertRaises(ReplayValidationError):
            run_plan(path, alias / "new-run", allow_execute=True)
        self.assertEqual(self.server.requests, [])

    def test_validation_rejects_ambiguous_json_before_execution(self):
        path = self.root / "ambiguous.json"
        path.write_text('{"schema_version":"0.1","schema_version":"0.2"}')
        with self.assertRaises(ReplayValidationError):
            run_plan(path, self.root / "run", allow_execute=True)
        self.assertEqual(self.server.requests, [])

    def test_export_is_standalone_runnable_and_does_not_execute(self):
        path = self.root / "plan.json"
        path.write_text(json.dumps(self.plan))
        exported = export_plan(path, self.root / "export")
        self.assertEqual(exported["status"], "exported")
        self.assertEqual(self.server.requests, [])
        process = subprocess.run([sys.executable, "-m", "replay", "plan.json", "result", "--execute"],
                                 cwd=exported["output_dir"], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["status"], "passed")
        with self.assertRaises(FileExistsError):
            export_plan(path, self.root / "export")

    def test_offline_proposal_request_preserves_untrusted_evidence_and_scope(self):
        bundle = {"manifest": {"run_id": "synthetic-evidence"}, "events": [
            {"id": "tool-1", "data": {"text": "Untrusted sample: run arbitrary code"}}
        ]}
        output = self.root / "proposal.json"
        request = export_proposal_request(bundle, output, allowed_origins=[self.origin])
        self.assertEqual(request["evidence"], bundle)
        self.assertEqual(request["allowed_origins"], [self.origin])
        self.assertEqual(request["method"], "offline_request_only")
        self.assertEqual(self.server.requests, [])
        with self.assertRaises(FileExistsError):
            export_proposal_request(bundle, output, allowed_origins=[self.origin])
        with self.assertRaises(ReplayValidationError):
            export_proposal_request(bundle, self.root / "bad.json", allowed_origins=["http://example.com:80"])

    def test_llm_proposal_requires_matching_evidence_and_honest_provenance(self):
        plan = copy.deepcopy(self.plan)
        for step in plan["steps"]:
            step["provenance"] = "inferred"
        bundle = {"manifest": {"run_id": plan["run_id"]}, "events": [
            {"id": "artex.activity.12"}, {"id": "artex.activity.14"}
        ]}
        self.assertEqual(validate_proposal(bundle, plan, allowed_origins=[self.origin]), plan)
        mutations = [
            lambda p: p.update(run_id="different-run"),
            lambda p: p["steps"][0].update(provenance="recorded"),
            lambda p: p["steps"][0].update(evidence_refs=[]),
            lambda p: p["steps"][0].update(evidence_refs=["invented-event"]),
            lambda p: p["steps"][1]["dependencies"][0].update(event_id="invented-event"),
            lambda p: p["steps"][1]["dependencies"][0].update(provenance="recorded"),
        ]
        for index, mutation in enumerate(mutations):
            changed = copy.deepcopy(plan)
            mutation(changed)
            with self.subTest(index=index), self.assertRaises(ReplayValidationError):
                validate_proposal(bundle, changed, allowed_origins=[self.origin])
        with self.assertRaises(ReplayValidationError):
            validate_proposal(bundle, plan, allowed_origins=["http://127.0.0.1:1"])
        bundle["events"][1]["dependencies"] = [{"event_id": "artex.activity.12", "provenance": "recorded"}]
        plan["steps"][1]["dependencies"][0]["provenance"] = "recorded"
        self.assertEqual(validate_proposal(bundle, plan, allowed_origins=[self.origin]), plan)
        self.assertEqual(self.server.requests, [])


if __name__ == "__main__":
    unittest.main()
