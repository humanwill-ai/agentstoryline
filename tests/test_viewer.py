import tempfile
import unittest
import json
import io
import wave
import base64
from pathlib import Path

from web.render import render_timeline


class ViewerTests(unittest.TestCase):
    def test_flow_first_frame_is_spotlight(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "flow.html"
            render_timeline({"manifest": {}, "events": []}, {"stages": []}, output)
            html = output.read_text()
            self.assertIn('<main id="tf-flow" class="f-single f-spotlight">', html)
            self.assertIn('<option value="spotlight" selected>Spotlight</option>', html)

    def test_flow_default_and_technical_view_retain_exact_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = {"manifest": {}, "events": [{"id": "e1", "sequence": 0,
                      "kind": "observation", "data": {"text": "<untrusted>"}}]}
            payloads = []
            for view in ("flow", "evidence"):
                output = Path(temp) / (view + ".html")
                render_timeline(bundle, {"stages": []}, output, view=view)
                payloads.append(json.loads(output.read_text().split('<script id="recording" type="application/json">')[1].split('</script>')[0]))
            self.assertIn("flow", payloads[0])
            self.assertNotIn("flow", payloads[1])
            self.assertEqual(payloads[0]["evidence_text"], payloads[1]["evidence_text"])

    def test_sound_is_embedded_exactly_and_invalid_sound_creates_no_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(8000)
                wav.writeframes(b"\x00\x00" * 800)
            sound = root / "pop.wav"
            sound.write_bytes(buf.getvalue())
            output = root / "flow.html"
            render_timeline({}, {}, output, notification_sound=sound)
            payload = json.loads(output.read_text().split('<script id="recording" type="application/json">')[1].split('</script>')[0])
            self.assertEqual(base64.b64decode(payload["notification_sound"].split(",", 1)[1]), sound.read_bytes())
            sound.write_text("not audio")
            with self.assertRaises(ValueError):
                render_timeline({}, {}, root / "bad.html", notification_sound=sound)
            self.assertFalse((root / "bad.html").exists())

    def test_large_numbers_preserved_in_evidence_display_strings(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "report.html"
            huge = 900719925474099312345
            render_timeline({"manifest": {"source_id": huge}, "events": [{"id": "e1", "sequence": huge, "data": {"source_id": huge}}]}, {"stages": []}, target)
            html = target.read_text()
            payload = json.loads(html.split('<script id="recording" type="application/json">')[1].split('</script>')[0])
            self.assertIn(str(huge), payload["evidence_text"]["e1"]["data"])
            self.assertEqual(payload["evidence_text"]["e1"]["sequence"], str(huge))
            self.assertIn(str(huge), payload["manifest_text"])

    def test_recorded_html_cannot_escape_embedded_data(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "report.html"
            render_timeline({"raw": "</script><script>alert(1)</script>"}, {"stages": []}, target)
            html = target.read_text()
            self.assertNotIn("</script><script>alert(1)</script>", html)
            self.assertIn("\\u003c/script\\u003e", html)
            self.assertIn("connect-src 'none'", html)

    def test_existing_report_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "report.html"
            target.write_text("original")
            with self.assertRaises(FileExistsError):
                render_timeline({}, {}, target)
            self.assertEqual(target.read_text(), "original")

    def test_standalone_assets_and_untrusted_placeholder_text(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "report.html"
            value = "__TRACE_JS__ __TRACE_CSS__ __TRACE_DATA__ </script>"
            render_timeline({"manifest": {}, "events": [
                {"id": "e1", "kind": "observation", "sequence": 0,
                 "timestamp": "2026-10-11T10:00:00Z", "data": {"value": value}},
            ]}, {"stages": []}, target)
            html = target.read_text()
            raw = html.split('<script id="recording" type="application/json">')[1].split('</script>')[0]
            payload = json.loads(raw)
            self.assertEqual(payload["bundle"]["events"][0]["data"]["value"], value)
            self.assertIn("storyline", payload)
            self.assertEqual(payload["storyline"]["activities"][0]["event_ids"], ["e1"])
            outside_data = html.replace(raw, "")
            self.assertNotIn("__TRACE_JS__", outside_data)
            self.assertNotIn("__TRACE_CSS__", outside_data)
            self.assertNotIn("__TRACE_DATA__", outside_data)
            self.assertNotIn('<script src=', html)
            self.assertNotIn('<link rel="stylesheet"', html)

    def test_rejected_plan_does_not_create_report(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "report.html"
            with self.assertRaises(ValueError):
                render_timeline({"manifest": {"run_id": "source"}, "events": []},
                                {"stages": []}, target, replay_plan={})
            self.assertFalse(target.exists())

    def test_replay_assertion_integers_preserved_for_inspection(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "report.html"
            huge = 900719925474099312345
            plan = {"schema_version": "0.1", "run_id": "source",
                    "allowed_origins": ["http://127.0.0.1:8765"], "steps": [
                        {"id": "read", "operation": "http.request", "method": "GET",
                         "url": "http://127.0.0.1:8765/value", "provenance": "authored",
                         "evidence_refs": ["e1"],
                         "expect": {"json": [{"path": ["value"], "equals": huge}]}}
                    ]}
            render_timeline({"manifest": {"run_id": "source"}, "events": [
                {"id": "e1", "sequence": 0, "data": {}}
            ]}, {"stages": []}, target, replay_plan=plan)
            raw = target.read_text().split('<script id="recording" type="application/json">')[1].split('</script>')[0]
            self.assertIn(str(huge), json.loads(raw)["replay_steps_text"]["read"])
