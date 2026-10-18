"""Newly authored synthetic native schema cases; no saved real model output."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from adapters.codex.importer import CodexImportError, import_codex
from core.bundle import BundleValidationError, load_bundle


def row(kind, payload, seconds=0):
    return {"timestamp": f"2026-10-10T12:00:{seconds:02d}Z", "type": kind, "payload": payload}


def records():
    return [
        row("session_meta", {"id": "synthetic-session", "originator": "codex_cli_rs", "cli_version": "synthetic-version", "model_provider": "synthetic-provider"}),
        row("turn_context", {"model": "synthetic-model", "turn_id": "synthetic-turn"}),
        row("response_item", {"type": "message", "role": "assistant", "id": "message-1", "phase": "commentary", "content": [{"type": "output_text", "text": "Synthetic local fixture inspection."}]}, 1),
        row("response_item", {"type": "function_call", "id": "call-item", "call_id": "call-1", "namespace": "synthetic", "name": "inspect", "arguments": '{"path":"fixture.json"}'}, 2),
        row("response_item", {"type": "function_call_output", "id": "result-item", "call_id": "call-1", "output": '{"ok":true,"value":42}'}, 3),
        row("response_item", {"type": "custom_tool_call", "id": "custom-item", "call_id": "call-2", "name": "synthetic_eval", "input": "inspect_fixture()"}, 4),
        row("response_item", {"type": "custom_tool_call_output", "id": "custom-result", "call_id": "call-2", "output": [{"type": "text", "text": "synthetic result"}]}, 5),
        row("response_item", {"type": "message", "role": "assistant", "phase": "final_answer", "content": [{"type": "output_text", "text": "Synthetic verification passed."}]}, 6),
    ]


class CodexAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source, self.destination = self.root / "native.jsonl", self.root / "bundle"

    def write(self, rows=None):
        raw = b"".join((json.dumps(r, ensure_ascii=False) + "\r\n").encode() for r in (records() if rows is None else rows))
        self.source.write_bytes(raw)
        return raw

    def run_import(self, rows=None):
        self.write(rows)
        return import_codex(self.source, self.destination)

    def test_maps_native_function_custom_and_visible_response_with_exact_artifact(self):
        raw = self.write()
        bundle = import_codex(self.source, self.destination)
        self.assertEqual(load_bundle(self.destination), bundle)
        self.assertEqual((self.destination / "artifacts/codex/rollout.jsonl").read_bytes(), raw)
        self.assertEqual(bundle["manifest"]["artifact_refs"][0]["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual([e["kind"] for e in bundle["events"]], ["model.response", "tool.start", "tool.end", "tool.start", "tool.end", "model.response"])
        self.assertEqual(bundle["events"][2]["parent_id"], bundle["events"][1]["id"])
        self.assertEqual(bundle["events"][4]["dependencies"], [{"event_id": bundle["events"][3]["id"], "provenance": "recorded"}])
        self.assertEqual(bundle["events"][1]["tool"], "synthetic.inspect")
        self.assertEqual(bundle["events"][1]["data"]["arguments"], records()[3]["payload"]["arguments"])
        self.assertEqual(bundle["events"][4]["data"]["result"], records()[6]["payload"]["output"])
        self.assertEqual(bundle["events"][0]["data"]["model_identity"]["model"], "synthetic-model")
        self.assertEqual(bundle["manifest"]["completeness"]["status"], "partial")
        self.assertNotIn("model.request", {e["kind"] for e in bundle["events"]})

    def test_context_reasoning_and_duplicate_event_copies_not_mapped(self):
        rows = records()
        rows += [row("response_item", {"type": "reasoning", "summary": [{"text": "synthetic excluded summary"}], "encrypted_content": "synthetic-not-real"}, 7),
                 row("response_item", {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "synthetic context"}]}, 7),
                 row("event_msg", {"type": "agent_message", "message": "copy of assistant response"}, 8)]
        bundle = self.run_import(rows)
        self.assertEqual(len(bundle["events"]), 6)
        counts = bundle["manifest"]["source"]["excluded_record_counts"]
        self.assertEqual(counts["response_item.reasoning"], 1)
        self.assertEqual(counts["event_msg"], 1)

    def test_nonvisible_assistant_channels_phases_and_blocks_excluded(self):
        rows = records()
        for field, value in (("channel", "analysis"), ("channel", "summary"), ("phase", "analysis"), ("phase", "summary"), ("internal_chat_message_metadata_passthrough", {"channel": "analysis"}), ("content", [{"type": "reasoning", "text": "synthetic excluded"}])):
            payload = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "synthetic hidden"}], field: value}
            rows.append(row("response_item", payload, 7))
        rows.append({**row("response_item", {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "synthetic hidden"}]}, 8), "channel": "analysis"})
        bundle = self.run_import(rows)
        self.assertEqual(len(bundle["events"]), 6)

    def test_same_second_fractional_timestamps_use_datetime_order(self):
        rows = records()
        rows[3]["timestamp"] = "2026-10-10T12:00:02Z"
        rows[4]["timestamp"] = "2026-10-10T12:00:02.123456789Z"
        bundle = self.run_import(rows)
        self.assertIn("dependencies", bundle["events"][2])
        self.assertFalse(any("out of order" in g for g in bundle["manifest"]["completeness"]["gaps"]))
        self.assertEqual(bundle["events"][2]["timestamp"], "2026-10-10T12:00:02.123456Z")
        self.assertEqual(bundle["events"][2]["data"]["source_record"]["timestamp"], "2026-10-10T12:00:02.123456789Z")

    def test_wrong_agent_missing_duplicate_metadata_rejected(self):
        for mode in ("wrong", "missing", "duplicate"):
            with self.subTest(mode=mode):
                rows = records()
                if mode == "wrong": rows[0]["payload"]["originator"] = "artex"
                elif mode == "missing": rows = rows[1:]
                else: rows.append(rows[0])
                self.write(rows)
                with self.assertRaises(CodexImportError): import_codex(self.source, self.destination)
                self.assertFalse(self.destination.exists())

    def test_repeated_call_ids_and_outputs_do_not_guess_pair(self):
        for index in (3, 4):
            with self.subTest(index=index):
                rows = records()
                rows.insert(5, rows[index])
                self.write(rows)
                out = self.root / f"bundle-{index}"
                bundle = import_codex(self.source, out)
                relevant = [e for e in bundle["events"] if e.get("call_id") == "call-1"]
                self.assertTrue(all("parent_id" not in e and "dependencies" not in e for e in relevant))
                self.assertTrue(any("repeated" in gap for gap in bundle["manifest"]["completeness"]["gaps"]))

    def test_repeated_item_id_disables_even_unique_call_pair(self):
        rows = records()
        rows[3]["payload"]["id"] = rows[2]["payload"]["id"]
        bundle = self.run_import(rows)
        self.assertNotIn("dependencies", bundle["events"][2])
        self.assertTrue(bundle["events"][1]["data"]["repeated_native_item_id"])

    def test_unmatched_and_incompatible_tool_kinds_remain_unlinked(self):
        for mode in ("no-call", "no-result", "incompatible"):
            with self.subTest(mode=mode):
                rows = records()
                if mode == "no-call": del rows[3]
                elif mode == "no-result": del rows[4]
                else: rows[4]["payload"]["type"] = "custom_tool_call_output"
                self.write(rows)
                bundle = import_codex(self.source, self.root / mode)
                for item in bundle["events"]:
                    if item.get("call_id") == "call-1": self.assertNotIn("dependencies", item)

    def test_out_of_order_native_record_or_timestamp_does_not_assign_dependency(self):
        for mode in ("record", "timestamp"):
            with self.subTest(mode=mode):
                rows = records()
                if mode == "record": rows[3], rows[4] = rows[4], rows[3]
                else: rows[4]["timestamp"] = "2026-10-10T12:00:01Z"
                self.write(rows)
                bundle = import_codex(self.source, self.root / mode)
                self.assertEqual([e["data"]["source_line"] for e in bundle["events"]], [3, 4, 5, 6, 7, 8])
                self.assertTrue(any("out of order" in g for g in bundle["manifest"]["completeness"]["gaps"]))
                for e in bundle["events"]:
                    if e.get("call_id") == "call-1": self.assertNotIn("dependencies", e)

    def test_foreign_source_fields_are_data_not_filesystem_or_contract_instructions(self):
        rows = records()
        foreign = {"path": "../../private-file", "sha256": "not-a-hash", "dependencies": [{"event_id": "invented"}], "agent": "OtherAgent"}
        rows[3]["payload"]["artifact_refs"] = [foreign]
        rows[3]["payload"]["source"] = foreign
        rows[3]["payload"]["arguments"] = "touch /tmp/this-must-never-run"
        with patch("subprocess.run", side_effect=AssertionError("evidence executed")), patch("os.system", side_effect=AssertionError("evidence executed")):
            bundle = self.run_import(rows)
        self.assertEqual(bundle["events"][1]["data"]["source_record"]["payload"]["source"], foreign)
        self.assertNotIn("artifact_refs", bundle["events"][1])

    def test_unknown_model_metadata_remains_explicit(self):
        rows = records()
        del rows[1]
        bundle = self.run_import(rows)
        self.assertNotIn("model_identity", bundle["events"][0]["data"])
        self.assertTrue(any("model identity is unknown" in g for g in bundle["manifest"]["completeness"]["gaps"]))

    def test_model_switch_is_tied_to_recorded_context_not_timestamp_guess(self):
        rows = records()
        rows.insert(5, row("turn_context", {"model": "synthetic-second-model", "turn_id": "second-turn"}, 3))
        bundle = self.run_import(rows)
        self.assertEqual(bundle["events"][2]["data"]["model_identity"]["model"], "synthetic-model")
        self.assertEqual(bundle["events"][3]["data"]["model_identity"]["model"], "synthetic-second-model")

    def test_metadata_only_recording_is_partial_and_empty(self):
        bundle = self.run_import(records()[:1])
        self.assertEqual(bundle["events"], [])
        self.assertEqual(bundle["manifest"]["completeness"]["status"], "partial")

    def test_bad_json_constants_duplicate_keys_and_blank_lines_rejected(self):
        for raw in (b'{"payload":{},"payload":{},"timestamp":"2026-10-10T12:00:00Z","type":"session_meta"}\n', b'{"payload":{"value":NaN}}\n', b'{"payload":{"value":1e9999}}\n', b'\n', b'\xff\n'):
            with self.subTest(raw=raw):
                self.source.write_bytes(raw)
                with self.assertRaises(CodexImportError): import_codex(self.source, self.destination)
                self.assertFalse(self.destination.exists())

    def test_missing_required_supported_item_fields_and_invalid_time_rejected(self):
        for key, index in (("call_id", 3), ("name", 3), ("arguments", 3), ("output", 4), ("timestamp", 0)):
            with self.subTest(key=key):
                rows = records()
                if key == "timestamp": rows[index][key] = "2026-10-10 12:00:00"
                else: del rows[index]["payload"][key]
                self.write(rows)
                with self.assertRaises(CodexImportError): import_codex(self.source, self.destination)

    def test_byte_and_record_limits_reject_before_publication(self):
        self.write()
        for setting, value in (("MAX_FILE_BYTES", 10), ("MAX_RECORDS", 2)):
            with self.subTest(setting=setting), patch(f"adapters.codex.importer.{setting}", value):
                with self.assertRaises(CodexImportError): import_codex(self.source, self.destination)
                self.assertFalse(self.destination.exists())

    def test_source_and_destination_symlinks_rejected(self):
        self.write()
        alias = self.root / "alias.jsonl"
        alias.symlink_to(self.source)
        with self.assertRaises(CodexImportError): import_codex(alias, self.destination)
        self.destination.symlink_to(self.root / "missing")
        with self.assertRaises(CodexImportError): import_codex(self.source, self.destination)
        parent_alias = self.root / "parent-alias"
        parent_alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(CodexImportError): import_codex(parent_alias / self.source.name, self.root / "new")
        with self.assertRaises(CodexImportError): import_codex(self.source, parent_alias / "new")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "named pipes unavailable")
    def test_special_source_file_rejected_without_execution(self):
        os.mkfifo(self.source)
        with self.assertRaisesRegex(CodexImportError, "regular file"): import_codex(self.source, self.destination)

    def test_existing_destination_is_preserved(self):
        self.write()
        self.destination.mkdir()
        sentinel = self.destination / "unrelated.txt"
        sentinel.write_text("keep")
        with self.assertRaises(CodexImportError): import_codex(self.source, self.destination)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_source_read_once_even_if_file_changes_after_parse(self):
        raw = self.write()
        from adapters.codex.importer import _convert
        def mutate(captured):
            converted = _convert(captured)
            self.source.write_bytes(b"changed")
            return converted
        with patch("adapters.codex.importer._convert", side_effect=mutate):
            import_codex(self.source, self.destination)
        self.assertEqual((self.destination / "artifacts/codex/rollout.jsonl").read_bytes(), raw)

    def test_concurrent_destination_is_not_overwritten(self):
        self.write()
        real_load = load_bundle
        def concurrent(staging):
            bundle = real_load(staging)
            self.destination.mkdir()
            (self.destination / "keep").write_text("concurrent data")
            return bundle
        with patch("adapters.codex.importer.load_bundle", side_effect=concurrent):
            with self.assertRaisesRegex(CodexImportError, "cannot publish"): import_codex(self.source, self.destination)
        self.assertEqual((self.destination / "keep").read_text(), "concurrent data")

    def test_failed_publication_cleans_owned_files_only(self):
        self.write()
        def failed_copy(_source, _output):
            (self.destination / "concurrent.txt").write_text("keep")
            raise OSError("synthetic failure")
        with patch("adapters.codex.importer.shutil.copyfileobj", side_effect=failed_copy):
            with self.assertRaises(CodexImportError): import_codex(self.source, self.destination)
        self.assertEqual([p.name for p in self.destination.iterdir()], ["concurrent.txt"])

    def test_checksum_and_artifact_paths_validated_by_common_contract(self):
        bundle = self.run_import()
        artifact = self.destination / "artifacts/codex/rollout.jsonl"
        artifact.write_bytes(b"corrupt")
        with self.assertRaisesRegex(BundleValidationError, "SHA-256 mismatch"): load_bundle(self.destination)
        manifest = bundle["manifest"]
        manifest["artifact_refs"][0]["path"] = "artifacts/../../native.jsonl"
        (self.destination / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(BundleValidationError, "without traversal"): load_bundle(self.destination)


if __name__ == "__main__":
    unittest.main()
