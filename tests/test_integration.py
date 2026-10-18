import tempfile
import unittest
from pathlib import Path

from scripts.demo import run_demo


class IntegrationTests(unittest.TestCase):
    def test_synthetic_artex_import_timeline_and_fresh_token_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_demo(Path(temp).resolve() / "demo")
            self.assertEqual(result["status"], "passed")
            self.assertTrue(result["fresh_token_dependency_verified"])
            self.assertGreater(result["events"], 4)
            self.assertTrue(Path(result["timeline"]).is_file())
