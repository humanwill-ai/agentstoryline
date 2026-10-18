"""Response-content assertions must detect false success and stop dependencies."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from replay.runner import ReplayValidationError, export_plan, run_plan, validate_plan


class OutcomeHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.server.paths.append(self.path)
        data = b'{"ok":true,"payload":{"entries":[{"marker":"synthetic-protected-v1"}]}}'
        if self.path == "/malformed":
            data = b'{"ok":true,"ok":false}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class ReplayOutcomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), OutcomeHandler)
        cls.server.paths = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.server.paths.clear()
        self.plan = {"schema_version": "0.1", "run_id": "synthetic-outcome", "allowed_origins": [self.origin], "steps": [
            {"id": "protected", "operation": "http.request", "method": "GET", "url": self.origin + "/protected",
             "expect": {"status": 200, "json": [{"path": ["ok"], "equals": True}, {"path": ["payload", "entries", 0, "marker"], "equals": "synthetic-protected-v1"}]}},
            {"id": "next", "operation": "http.request", "method": "GET", "url": self.origin + "/next"}]}

    def tearDown(self):
        self.temp.cleanup()

    def execute(self):
        path = self.root / "plan.json"
        path.write_text(json.dumps(self.plan))
        return run_plan(path, self.root / "result", allow_execute=True)

    def test_expected_payload_verified_and_exported(self):
        result = self.execute()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["steps"][0]["json_assertions_passed"], 2)
        self.assertEqual(self.server.paths, ["/protected", "/next"])
        export_plan(self.root / "plan.json", self.root / "package")
        self.assertEqual(json.loads((self.root / "package" / "plan.json").read_text())["steps"][0]["expect"], self.plan["steps"][0]["expect"])

    def test_http_200_wrong_payload_stops_and_keeps_evidence(self):
        self.plan["steps"][0]["expect"]["json"][1]["equals"] = "wrong"
        result = self.execute()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["unexecuted_steps"], ["next"])
        self.assertEqual(self.server.paths, ["/protected"])
        self.assertEqual(result["steps"][0]["response_status"], 200)
        self.assertTrue((self.root / "result" / result["steps"][0]["response"]["path"]).is_file())

    def test_nested_boolean_does_not_equal_integer(self):
        self.plan["steps"][0]["expect"] = {"json": [{"path": [], "equals": {"ok": 1, "payload": {"entries": [{"marker": "synthetic-protected-v1"}]}}}]}
        self.assertEqual(self.execute()["status"], "failed")
        self.assertEqual(self.server.paths, ["/protected"])

    def test_duplicate_json_fails(self):
        self.plan["steps"][0]["url"] = self.origin + "/malformed"
        result = self.execute()
        self.assertEqual(result["status"], "failed")
        self.assertIn("duplicate JSON", result["steps"][0]["error"])

    def test_missing_assertion_path_stops(self):
        self.plan["steps"][0]["expect"] = {"json": [{"path": ["missing"], "equals": True}]}
        result = self.execute()
        self.assertEqual(result["status"], "failed")
        self.assertIn("assertion path", result["steps"][0]["error"])
        self.assertEqual(self.server.paths, ["/protected"])

    def test_malformed_comparison_rejected_without_network(self):
        for comparison in [{"path": [True], "equals": True}, {"path": [-1], "equals": True}, {"path": ["ok"]}, {"path": "ok", "equals": True}, {"path": [], "equals": float("nan")}]:
            with self.subTest(comparison=comparison):
                self.plan["steps"][0]["expect"] = {"json": [comparison]}
                with self.assertRaises(ReplayValidationError):
                    validate_plan(self.plan)
        self.assertEqual(self.server.paths, [])


if __name__ == "__main__":
    unittest.main()
