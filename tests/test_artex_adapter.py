"""Newly authored synthetic adapter tests; no imported production evidence."""

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

from adapters.artex.importer import ArtexImportError, import_artex
from core.bundle import BundleValidationError, load_bundle


FIXTURE = Path(__file__).parent / "fixtures" / "artex" / "token_session"


class ArtexAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        # macOS /var is a platform symlink; source itself and task subpaths are real.
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "source"
        shutil.copytree(FIXTURE, self.source)
        self.destination = self.root / "bundle"

    def tearDown(self):
        self.temporary.cleanup()

    def snapshot(self):
        return json.loads((self.source / "manifest.json").read_text())

    def save(self, snapshot):
        (self.source / "manifest.json").write_text(json.dumps(snapshot))

    def captured_source(self):
        """Author v1 capture metadata locally; this is not a production trace."""
        snapshot = json.loads((FIXTURE / "manifest.json").read_text())
        rows = [json.loads(line) for line in (FIXTURE / "database/llm_records.ndjson").read_text().splitlines()]
        capture = {"version": 1, "model_call_id": "synthetic-logical-model-call", "session_id": "synthetic-captured-session",
                   "recording_enabled": True, "started_at": "2026-10-10T12:00:00.5Z"}
        request = json.loads(rows[0]["request_body"])
        request["traceforge"] = capture
        response = json.loads(rows[0]["response_body"])
        response["traceforge"] = {**capture, "ended_at": "2026-10-10T12:00:03.5Z", "attempts": [{"number": 1, "status": "ok"}]}
        response["content"] = [{"type": "tool_use", "id": "synthetic-session-call", "name": "Bash",
                                "input": {"command": "synthetic-full-command"}}]
        rows[0]["request_body"] = json.dumps(request)
        rows[0]["response_body"] = json.dumps(response)
        for index, row in enumerate(snapshot["tables"]["activity"][:2]):
            content = {"tool_input": {"command": "synthetic-full-command"}} if index == 0 else {
                "tool_result": {"type": "tool_result", "tool_use_id": "synthetic-session-call",
                                "content": [{"type": "text", "text": "synthetic full result"}], "is_error": False}}
            row["metadata"] = {"traceforge": {**capture, **content, "observed_at": row["created_at"]}}
            row["detail"] = "truncated historical display text"
        snapshot["recording_export"] = {"schema_version": 1, "read_only": True, "consistency": "repeatable_read",
                                         "completeness": {"status": "partial", "gaps": ["transcript files omitted"]}}
        return snapshot, rows

    def save_capture(self, snapshot, rows):
        self.save(snapshot)
        (self.source / "database/llm_records.ndjson").write_text("".join(json.dumps(row) + "\n" for row in rows))

    def test_explicit_streaming_capture_preserves_content_timestamps_and_recorded_links(self):
        snapshot, rows = self.captured_source()
        self.save_capture(snapshot, rows)
        raw = (self.source / "database/llm_records.ndjson").read_bytes()
        bundle = import_artex(self.archive(), self.destination)
        events = {event["id"]: event for event in bundle["events"]}
        request, response = events["artex.llm_records.101.request"], events["artex.llm_records.101.response"]
        start, end = events["artex.activity.11"], events["artex.activity.12"]
        self.assertEqual(request["timestamp"], "2026-10-10T12:00:00.500000Z")
        self.assertEqual(response["timestamp"], "2026-10-10T12:00:03.500000Z")
        self.assertEqual(request["call_id"], "synthetic-logical-model-call")
        self.assertEqual(start["dependencies"], [{"event_id": request["id"], "provenance": "recorded"}])
        self.assertLess(start["sequence"], response["sequence"])  # Tools can run while the stream is live.
        self.assertEqual(start["data"]["model_response_event_id"], response["id"])
        self.assertEqual(start["data"]["arguments"], {"command": "synthetic-full-command"})
        self.assertEqual(end["data"]["result"]["content"][0]["text"], "synthetic full result")
        self.assertEqual(end["data"]["source_record"]["detail"], "truncated historical display text")
        self.assertEqual(response["data"]["response"]["content"][0]["input"], start["data"]["arguments"])
        self.assertEqual(end["dependencies"], [{"event_id": start["id"], "provenance": "recorded"}])
        self.assertEqual(bundle["manifest"]["source"]["recording_export"], snapshot["recording_export"])
        self.assertIn("Artex export: transcript files omitted", bundle["manifest"]["completeness"]["gaps"])
        self.assertEqual((self.destination / "artifacts/artex/database/llm_records.ndjson").read_bytes(), raw)

    def test_full_tool_definitions_supersede_legacy_names_only_gap(self):
        for shape in ("complete", "missing", "partial", "malformed"):
            with self.subTest(shape=shape):
                snapshot, rows = self.captured_source()
                for row in rows:
                    request = json.loads(row["request_body"])
                    request["tools"] = ["A", "B"]
                    request["tool_definitions"] = [{"name": "A", "input_schema": {}}, {"name": "B", "input_schema": {"type": "object"}}]
                    if shape == "missing":
                        request.pop("tool_definitions")
                    elif shape == "partial":
                        request["tool_definitions"].pop()
                    elif shape == "malformed":
                        request["tool_definitions"][1]["input_schema"] = "unavailable"
                    row["request_body"] = json.dumps(request)
                self.save_capture(snapshot, rows)
                bundle = import_artex(self.source, self.root / ("schema-" + shape))
                has_gap = any("without corresponding full schemas" in gap for gap in bundle["manifest"]["completeness"]["gaps"])
                self.assertEqual(has_gap, shape != "complete")

    def test_missing_disabled_unknown_and_ambiguous_capture_ids_do_not_create_model_links(self):
        for mutation in ("missing", "disabled", "unknown-version", "wrong-session", "mismatched-response", "duplicate", "malformed-id", "missing-tool-id"):
            with self.subTest(mutation=mutation):
                snapshot, rows = self.captured_source()
                capture = snapshot["tables"]["activity"][0]["metadata"]["traceforge"]
                if mutation == "missing":
                    capture.pop("model_call_id")
                elif mutation == "disabled":
                    capture["recording_enabled"] = False
                elif mutation == "unknown-version":
                    capture["version"] = 2
                elif mutation == "wrong-session":
                    capture["session_id"] = "different-session"
                elif mutation == "mismatched-response":
                    response = json.loads(rows[0]["response_body"])
                    response["traceforge"]["model_call_id"] = "different-call"
                    rows[0]["response_body"] = json.dumps(response)
                elif mutation == "duplicate":
                    rows.append({**rows[0], "id": 104})
                    snapshot["data_counts"]["llm_records"] += 1
                elif mutation == "malformed-id":
                    capture["model_call_id"] = ["not-a-string-id"]
                else:
                    snapshot["tables"]["activity"][0].pop("tool_use_id")
                self.save_capture(snapshot, rows)
                bundle = import_artex(self.source, self.root / ("bundle-" + mutation))
                start = next(e for e in bundle["events"] if e["id"] == "artex.activity.11")
                self.assertNotIn("dependencies", start)
                self.assertNotIn("model_response_event_id", start["data"])

    def test_opt_in_capture_validator_checks_nontext_and_writes_observed_report(self):
        from scripts.validate_artex_capture import validate_capture
        snapshot, rows = self.captured_source()
        snapshot["tables"]["activity"][1]["metadata"]["traceforge"]["tool_result"]["content"].append(
            {"type": "thinking", "thinking": "synthetic non-text block", "signature": "synthetic-signature"})
        self.save_capture(snapshot, rows)
        report = validate_capture(self.archive(), self.root / "validation")
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["nontext_result_blocks"], [{"event_id": "artex.activity.12", "type": "thinking"}])
        self.assertEqual(report["checks"][0]["tool_use_id"], "synthetic-session-call")
        self.assertTrue((self.root / "validation/timeline.html").is_file())
        self.assertEqual(json.loads((self.root / "validation/observed.json").read_text())["method"], "observed")
        with self.assertRaisesRegex(ValueError, "outside"):
            validate_capture(FIXTURE, self.root / "rejected")
        self.assertFalse((self.root / "rejected").exists())

    def test_malformed_body_keeps_raw_evidence_and_mixed_timestamp_uncertainty(self):
        snapshot, rows = self.captured_source()
        original = '{"messages":[],"ambiguous":{"confidence":1,"confidence":2}}'
        rows[0]['request_body'] = original
        rows[0]['ts'] = '2026-10-10T12:00:04Z'
        self.save_capture(snapshot, rows)
        bundle = import_artex(self.source, self.destination)
        request = next(e for e in bundle['events'] if e['id'] == 'artex.llm_records.' + str(rows[0]['id']) + '.request')
        self.assertEqual(request['data']['request'], original)
        self.assertEqual(request['data']['timestamp_basis'], 'row_persistence')
        self.assertTrue(any('mixed persistence/capture' in gap for gap in bundle['manifest']['completeness']['gaps']))

    def test_explicit_reversed_model_capture_still_rejected(self):
        snapshot, rows = self.captured_source()
        response = json.loads(rows[0]['response_body'])
        response['traceforge']['ended_at'] = '2026-10-10T12:00:00Z'
        rows[0]['response_body'] = json.dumps(response)
        self.save_capture(snapshot, rows)
        with self.assertRaisesRegex(ArtexImportError, 'precedes request'):
            import_artex(self.source, self.destination)

    def test_native_streamed_export_preserves_structure_and_pairing(self):
        original = (self.source / "manifest.json").read_bytes()
        bundle = import_artex(self.source, self.destination)
        self.assertEqual(bundle, load_bundle(self.destination))
        self.assertEqual(bundle["manifest"]["run_id"], "artex-task-23")
        self.assertEqual(bundle["manifest"]["completeness"]["status"], "partial")
        events = {event["id"]: event for event in bundle["events"]}
        start = events["artex.activity.11"]
        end = events["artex.activity.12"]
        self.assertEqual(start["data"]["arguments"], {"command": "curl -sS http://127.0.0.1:8765/session"})
        self.assertEqual(end["data"]["result"]["token"], "recorded-demo-token")
        self.assertEqual(end["dependencies"], [{"event_id": start["id"], "provenance": "recorded"}])
        self.assertEqual(end["data"]["source_record"], self.snapshot()["tables"]["activity"][1])
        request = events["artex.llm_records.102.request"]
        response = events["artex.llm_records.102.response"]
        self.assertEqual(response["parent_id"], request["id"])
        self.assertIsInstance(request["data"]["request"]["messages"], list)
        self.assertEqual(request["data"]["source_id"], 102)
        history = events["artex.llm_records.102.history.2.0"]
        self.assertEqual(history["kind"], "observation")
        self.assertIn(end["id"], history["evidence_refs"])
        self.assertNotIn("parent_id", start)  # No inferred model parent.
        self.assertEqual((self.destination / "artifacts/artex/manifest.json").read_bytes(), original)
        self.assertEqual((self.source / "manifest.json").read_bytes(), original)
        self.assertEqual([e["sequence"] for e in bundle["events"]], list(range(len(bundle["events"]))))

    def test_legacy_inline_export_and_unknown_source_fields(self):
        snapshot = self.snapshot()
        snapshot["format_version"] = 1
        snapshot.pop("streamed_tables")
        snapshot["tables"]["llm_records"] = [json.loads(line) for line in (self.source / "database/llm_records.ndjson").read_text().splitlines()]
        snapshot["tables"]["activity"][0]["unknown_field"] = {"path": "../../raw-string", "sha256": "raw-value"}
        self.save(snapshot)
        bundle = import_artex(self.source / "manifest.json", self.destination)
        start = next(e for e in bundle["events"] if e["id"] == "artex.activity.11")
        self.assertEqual(start["data"]["source_record"]["unknown_field"], snapshot["tables"]["activity"][0]["unknown_field"])
        self.assertEqual(len(bundle["manifest"]["artifact_refs"]), 1)

    def test_missing_recording_and_missing_tool_result_are_explicit_gaps(self):
        snapshot = self.snapshot()
        snapshot.pop("streamed_tables")
        snapshot["data_counts"]["llm_records"] = 0
        snapshot["tables"]["activity"] = snapshot["tables"]["activity"][:1]
        snapshot["data_counts"]["activity"] = 1
        self.save(snapshot)
        bundle = import_artex(self.source, self.destination)
        gaps = " ".join(bundle["manifest"]["completeness"]["gaps"])
        self.assertIn("No LLM body records", gaps)
        self.assertIn("no activity result", gaps)
        self.assertNotIn("tool.end", [e["kind"] for e in bundle["events"]])

    def test_unmatched_and_ambiguous_tool_ids_do_not_invent_dependencies(self):
        snapshot = self.snapshot()
        duplicate = dict(snapshot["tables"]["activity"][0], id=15)
        snapshot["tables"]["activity"].append(duplicate)
        snapshot["tables"]["activity"][3]["tool_use_id"] = "orphan-result"
        snapshot["data_counts"]["activity"] += 1
        self.save(snapshot)
        bundle = import_artex(self.source, self.destination)
        ends = [e for e in bundle["events"] if e["kind"] == "tool.end"]
        self.assertTrue(all("dependencies" not in e for e in ends))
        ambiguous = next(e for e in bundle["events"] if e["id"] == "artex.llm_records.102.history.1.0")
        self.assertEqual(ambiguous["data"]["correlation_basis"], "transcript_only")

    def test_bad_json_body_is_retained_as_text(self):
        snapshot = self.snapshot()
        snapshot["tables"]["activity"][0]["detail"] = "invalid-json-but-preserved"
        self.save(snapshot)
        bundle = import_artex(self.source, self.destination)
        start = next(e for e in bundle["events"] if e["id"] == "artex.activity.11")
        self.assertEqual(start["data"]["arguments"], "invalid-json-but-preserved")
        self.assertIn("not valid JSON", " ".join(bundle["manifest"]["completeness"]["gaps"]))

    def test_declared_missing_stream_wrong_counts_and_task_are_rejected(self):
        for mutation in ("missing", "counts", "task", "version", "traversal"):
            with self.subTest(mutation=mutation):
                snapshot = self.snapshot()
                if mutation == "missing":
                    path = self.source / "database/llm_records.ndjson"
                    original = path.read_bytes()
                    path.unlink()
                elif mutation == "counts":
                    snapshot["data_counts"]["activity"] += 1
                elif mutation == "task":
                    snapshot["task_id"] = 99
                elif mutation == "version":
                    snapshot["format_version"] = 9
                elif mutation == "traversal":
                    snapshot["streamed_tables"]["llm_records"] = "../../outside.ndjson"
                self.save(snapshot)
                with self.assertRaises(ArtexImportError):
                    import_artex(self.source, self.destination)
                self.assertFalse(self.destination.exists())
                if mutation == "missing":
                    path.write_bytes(original)
                shutil.copyfile(FIXTURE / "manifest.json", self.source / "manifest.json")

    def test_symlink_file_or_parent_is_rejected_without_reading_target(self):
        manifest = self.source / "manifest.json"
        original = manifest.read_bytes()
        outside = self.root / "outside.json"
        outside.write_bytes(original)
        manifest.unlink()
        manifest.symlink_to(outside)
        with self.assertRaises(ArtexImportError):
            import_artex(self.source, self.destination)
        manifest.unlink()
        manifest.write_bytes(original)
        database = self.source / "database"
        shutil.move(database, self.root / "outside-database")
        database.symlink_to(self.root / "outside-database", target_is_directory=True)
        with self.assertRaises(ArtexImportError):
            import_artex(self.source, self.destination)
        self.assertFalse(self.destination.exists())

    def archive(self, bad=None):
        path = self.root / "export.tar"
        with tarfile.open(path, "w") as output:
            for name in ("manifest.json", "database/llm_records.ndjson"):
                data = (self.source / name).read_bytes()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                output.addfile(info, io.BytesIO(data))
            if bad:
                info = tarfile.TarInfo(bad)
                if bad == "link":
                    info.type = tarfile.SYMTYPE
                    info.linkname = "../outside"
                output.addfile(info)
        return path

    def test_v2_export_metadata_is_preserved_without_inventing_semantics(self):
        snapshot = self.snapshot()
        metadata = {"schema_version": 2, "read_only": True, "consistency": "repeatable_read",
                    "completeness": {"status": "partial", "gaps": ["synthetic v2 missing file"]},
                    "files": [{"path": "files/workspaces/report.txt", "status": "unavailable", "reason": "synthetic"}],
                    "future_capture_field": {"opaque": [1, False, "unchanged"]}}
        snapshot["recording_export"] = metadata
        snapshot["future_archive_metadata"] = {"opaque": "retained in original artifact"}
        self.save(snapshot)
        bundle = import_artex(self.source, self.destination)
        self.assertEqual(bundle["manifest"]["source"]["recording_export"], metadata)
        original = json.loads((self.destination / "artifacts/artex/manifest.json").read_text())
        self.assertEqual(original, snapshot)
        gaps = "\n".join(bundle["manifest"]["completeness"]["gaps"])
        self.assertIn("Artex export: synthetic v2 missing file", gaps)
        self.assertIn("future_archive_metadata", gaps)
        self.assertNotIn("files/workspaces/report.txt", {row["path"] for row in bundle["manifest"]["source"]["archive_inventory"]})

    def test_composite_rows_keep_stable_evidence_ids_across_imports(self):
        snapshot = self.snapshot()
        binding = {"finding_id": 5, "traffic_id": 9, "task_id": snapshot["task_id"],
                   "exploration_id": 42, "scope": "synthetic-local"}
        snapshot["tables"]["finding_traffic_bindings"] = [binding, dict(binding)]
        snapshot["data_counts"]["finding_traffic_bindings"] = 2
        self.save(snapshot)
        first = import_artex(self.source, self.destination)
        second = import_artex(self.source, self.root / "second-bundle")
        def bindings(bundle):
            return [event for event in bundle["events"] if event["data"].get("source_table") == "finding_traffic_bindings"]
        observed = bindings(first)
        self.assertEqual(observed, bindings(second))
        self.assertEqual(len(observed), 2)
        self.assertNotEqual(observed[0]["id"], observed[1]["id"])
        for event in observed:
            self.assertEqual(event["kind"], "observation")
            self.assertEqual(event["data"]["source_record"], binding)
            self.assertEqual(event["data"]["identity_basis"], "canonical_row_sha256_and_duplicate_ordinal")
            self.assertNotIn("dependencies", event)
        from web.render import build_payload
        payload = build_payload(first, {})
        referenced = {event_id for message in payload["flow"]["messages"] for event_id in message["event_ids"]}
        self.assertTrue({event["id"] for event in observed}.issubset(referenced))

    def test_native_tar_and_optional_zstd_import(self):
        archive = self.archive()
        bundle = import_artex(archive, self.destination)
        self.assertEqual(len(bundle["events"]), 20)
        node = next(event for event in bundle["events"] if event["id"] == "artex.exploration_nodes.7")
        self.assertEqual(node["kind"], "observation")
        self.assertEqual(node["data"]["source_record"], self.snapshot()["tables"]["exploration_nodes"][0])
        self.assertNotIn("dependencies", node)
        self.assertNotIn("parent_id", node)
        if shutil.which("zstd"):
            compressed = self.root / "export.tar.zst"
            with compressed.open("wb") as stream:
                subprocess.run([shutil.which("zstd"), "-q", "-c", str(archive)], stdout=stream, check=True)
            second = import_artex(compressed, self.root / "second-bundle")
            self.assertEqual(bundle, second)

    def test_unsafe_tar_members_including_ignored_files_are_rejected(self):
        for bad in ("../escape", "/absolute", "link", "manifest.json", "files/../../escape"):
            with self.subTest(bad=bad), self.assertRaises(ArtexImportError):
                import_artex(self.archive(bad), self.destination)
            self.assertFalse(self.destination.exists())

    def test_missing_zstd_has_actionable_failure(self):
        source = self.root / "export.tar.zst"
        source.write_bytes(b"not used without decoder")
        with patch("adapters.artex.importer.shutil.which", return_value=None):
            with self.assertRaisesRegex(ArtexImportError, "extracted export"):
                import_artex(source, self.destination)

    def test_hidden_pax_and_gnu_headers_are_bounded_before_payload_reads(self):
        # These are processed by tarfile before a member reaches iteration.
        for metadata in (tarfile.XHDTYPE, tarfile.GNUTYPE_LONGNAME):
            with self.subTest(metadata=metadata):
                path = self.root / "metadata.tar"
                header = tarfile.TarInfo("hidden-metadata")
                header.type = metadata
                header.size = 4096
                path.write_bytes(header.tobuf())
                with patch("adapters.artex.importer.MAX_FILE_BYTES", 1024):
                    with self.assertRaisesRegex(ArtexImportError, "header exceeds"):
                        import_artex(path, self.destination)
                self.assertFalse(self.destination.exists())

    def test_archive_total_budget_covers_metadata_headers_and_padding(self):
        archive = self.archive()
        with patch("adapters.artex.importer.MAX_ARCHIVE_BYTES", 1024):
            with self.assertRaisesRegex(ArtexImportError, "expanded archive"):
                import_artex(archive, self.destination)
        self.assertFalse(self.destination.exists())

    def test_stalled_decoder_is_killed_by_wall_clock_deadline(self):
        decoder = self.root / "synthetic-stalled-decoder"
        decoder.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n")
        decoder.chmod(0o700)
        source = self.root / "stalled.tar.zst"
        source.write_bytes(b"synthetic input")
        started = time.monotonic()
        with patch("adapters.artex.importer.shutil.which", return_value=str(decoder)):
            with patch("adapters.artex.importer.DECODER_TIMEOUT_SECONDS", 0.1):
                with self.assertRaises(ArtexImportError):
                    import_artex(source, self.destination)
        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse(self.destination.exists())

    def test_existing_destination_and_size_limits_are_rejected(self):
        self.destination.mkdir()
        sentinel = self.destination / "keep"
        sentinel.write_text("unrelated")
        with self.assertRaises(ArtexImportError):
            import_artex(self.source, self.destination)
        self.assertEqual(sentinel.read_text(), "unrelated")
        with patch("adapters.artex.importer.MAX_FILE_BYTES", 10):
            with self.assertRaises(ArtexImportError):
                import_artex(self.source, self.root / "other")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "named pipes unavailable on this platform")
    def test_fifo_source_is_rejected_without_blocking(self):
        fifo = self.root / "source.json"
        os.mkfifo(fifo)
        started = time.monotonic()
        with self.assertRaisesRegex(ArtexImportError, "regular file"):
            import_artex(fifo, self.destination)
        self.assertLess(time.monotonic() - started, 1)
        self.assertFalse(self.destination.exists())

    def test_concurrent_empty_destination_is_never_replaced(self):
        from core.bundle import load_bundle as actual_load
        identity = []

        def concurrent_writer(staging):
            verified = actual_load(staging)
            self.destination.mkdir(mode=0o711)
            identity.append(self.destination.stat().st_ino)
            return verified

        with patch("core.bundle.load_bundle", side_effect=concurrent_writer):
            with self.assertRaisesRegex(ArtexImportError, "cannot publish"):
                import_artex(self.source, self.destination)
        self.assertEqual(self.destination.stat().st_ino, identity[0])
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_failed_publication_cleans_only_owned_entries(self):
        def failed_copy(_source, _output):
            (self.destination / "concurrent-writer.txt").write_text("keep unrelated data")
            raise OSError("synthetic disk failure")

        with patch("adapters.artex.importer.shutil.copyfileobj", side_effect=failed_copy):
            with self.assertRaisesRegex(ArtexImportError, "publication failed"):
                import_artex(self.source, self.destination)
        self.assertEqual([p.name for p in self.destination.iterdir()], ["concurrent-writer.txt"])
        self.assertEqual((self.destination / "concurrent-writer.txt").read_text(), "keep unrelated data")

    def test_original_artifact_checksum_is_checked(self):
        import_artex(self.source, self.destination)
        (self.destination / "artifacts/artex/manifest.json").write_text("{}")
        with self.assertRaises(BundleValidationError):
            load_bundle(self.destination)


if __name__ == "__main__":
    unittest.main()
