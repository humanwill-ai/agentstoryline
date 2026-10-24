"""Synthetic Orca Trace input only: bounded evidence import never executes it."""

import hashlib
import io
import json
from pathlib import Path
import stat
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

from adapters.orcareplay import (
    OrcaReplayImportError, import_orcareplay, looks_like_orcareplay_manifest,
    read_source_files,
)
from core.bundle import load_bundle
from web.flow import build_flow
from web.storyline import build_storyline


MODULE = "adapters.orcareplay.importer"


def event(seq, kind, **fields):
    return {"seq": seq, "ts": "2026-10-18T10:00:00Z", "mono_us": seq * 1000000,
            "turn": 1, "type": kind, "actor": "model" if kind == "tool.call" else "harness", **fields}


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode()


def fixture_files(events=None, *, tail=b""):
    blob = encoded({"ok": True, "text": "Synthetic tool output"})
    digest = hashlib.sha256(blob).hexdigest()
    request = {"model": "fixture-model", "messages": [{"role": "user", "content": "Read the local fixture."}]}
    if events is None:
        events = [
            event(0, "run.start"),
            event(1, "model.request", attrs={"model": "fixture-model"}, payload=json.dumps(request)),
            event(2, "model.response", causes=[1], payload={"choices": [{"message": {"content": "Inspecting fixture", "tool_calls": [{"id": "call-1", "function": {"name": "read_fixture", "arguments": '{"path":"fixture.json"}'}}]}}]}),
            event(3, "tool.call", attrs={"tool_use_id": "call-1", "name": "read_fixture", "input": {"path": "fixture.json"}}, causes=[2]),
            event(4, "tool.result", attrs={"tool_use_id": "call-1", "name": "read_fixture"}, causes=[3], payload={"$blob": "sha256:" + digest, "bytes": len(blob), "media_type": "application/json"}),
            event(5, "shell.exec", attrs={"argv": ["echo", "fixture"], "cwd": "/synthetic"}, causes=[3]),
            event(6, "shell.result", attrs={"exit_code": 0}, causes=[5], payload="fixture"),
            event(7, "model.request", payload={**request, "messages": request["messages"] + [{"role": "tool", "content": "Synthetic tool output", "tool_call_id": "call-1"}]}),
            event(8, "fs.snapshot", attrs={"tree": "synthetic-metadata-only"}),
            event(9, "future.event", payload={"new": "Unknown retained observation"}),
            event(10, "model.response", payload={"content": [{"type": "text", "text": "Done."}]}),
            event(11, "run.end"),
        ]
    raw = b"".join(encoded(item) + b"\n" for item in events) + tail
    manifest = {"schema_version": "0.3.0", "run_id": "run_abc123", "created_at": "2026-10-18T10:00:00Z",
                "orca_version": "synthetic", "adapter": {"id": "synthetic", "version": "1"},
                "argv": ["synthetic-agent"], "cwd": "/synthetic",
                "integrity": {"events_sha256": hashlib.sha256(raw).hexdigest(), "blob_count": 1},
                "counts": {"events": len(events), "blobs": 1}}
    return {"manifest.json": encoded(manifest), "events.jsonl": raw,
            f"blobs/{digest[:2]}/{digest}": blob, "redactions.json": encoded({"rules": []}),
            "fs/objects/synthetic": b"Excluded snapshot body. Never restore or run this."}


class OrcaReplayAdapterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "run"
        self.destination = self.root / "bundle"

    def write(self, files=None):
        files = fixture_files() if files is None else files
        for name, raw in files.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        return files

    def archive(self, files=None, *, prefix="", extras=()):
        path = self.root / "source.without-tar-suffix"
        with tarfile.open(path, "w") as archive:
            for name, raw in (fixture_files() if files is None else files).items():
                member = tarfile.TarInfo(prefix + name)
                member.size = len(raw)
                archive.addfile(member, io.BytesIO(raw))
            for member, raw in extras:
                archive.addfile(member, io.BytesIO(raw))
        return path

    def zip_archive(self, files=None, *, prefix="", compression=zipfile.ZIP_DEFLATED,
                    extras=(), descriptor=False, comment=b""):
        class NonSeekable(io.BytesIO):
            def seek(self, *args):
                raise OSError("synthetic streaming ZIP writer")
        output = NonSeekable() if descriptor else io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(output, "w", compression=compression) as archive:
                archive.comment = comment
                for name, raw in (fixture_files() if files is None else files).items():
                    archive.writestr(prefix + name, raw)
                for name, raw in extras:
                    archive.writestr(name, raw)
        path = self.root / "source.orca.zip"
        path.write_bytes(output.getvalue())
        return path

    def zip_headers(self, raw):
        trailer = raw.rfind(b"PK\x05\x06")
        cursor = struct.unpack_from("<I", raw, trailer + 16)[0]
        headers = []
        for _ in range(struct.unpack_from("<H", raw, trailer + 10)[0]):
            local = struct.unpack_from("<I", raw, cursor + 42)[0]
            name_len, extra_len, comment_len = struct.unpack_from("<3H", raw, cursor + 28)
            name = bytes(raw[cursor + 46:cursor + 46 + name_len]).decode()
            headers.append((name, local, cursor))
            cursor += 46 + name_len + extra_len + comment_len
        return trailer, headers

    def assert_rejected(self, source=None):
        with self.assertRaises(OrcaReplayImportError):
            import_orcareplay(self.source if source is None else source, self.destination)
        self.assertFalse(self.destination.exists())

    def rewrite_manifest(self, files, **updates):
        manifest = json.loads(files["manifest.json"])
        manifest.update(updates)
        return {**files, "manifest.json": encoded(manifest)}

    def test_maps_model_tool_and_shell_preserving_sources_and_bytes(self):
        files = self.write()
        bundle = import_orcareplay(self.source, self.destination)
        self.assertEqual(load_bundle(self.destination), bundle)
        self.assertEqual(len(bundle["events"]), 12)
        source = bundle["manifest"]["source"]
        self.assertEqual(source["adapter"], "orcareplay")
        self.assertTrue(source["experimental"])
        self.assertEqual(source["format_version"], "0.3.0")
        self.assertEqual(bundle["manifest"]["completeness"]["status"], "partial")
        gaps = bundle["manifest"]["completeness"]["gaps"]
        self.assertFalse(any("Source event type run." in gap for gap in gaps))
        events = bundle["events"]
        self.assertEqual(events[1]["data"]["request"]["messages"][0]["content"], "Read the local fixture.")
        self.assertEqual(events[2]["data"]["response"]["content"][1]["type"], "tool_use")
        self.assertEqual(events[3]["kind"], "tool.start")
        self.assertEqual(events[3]["data"]["execution_status"], "proposal_only_not_execution")
        self.assertEqual(events[3]["data"]["arguments"], {"path": "fixture.json"})
        self.assertTrue(events[4]["data"]["result"]["ok"])
        self.assertEqual(events[5]["tool"], "shell")
        self.assertEqual(events[5]["data"]["arguments"]["argv"], ["echo", "fixture"])
        self.assertEqual(events[6]["data"]["result"], "fixture")
        for name, raw in files.items():
            artifact = self.destination / "artifacts/orcareplay/files" / name
            if name.startswith("fs/"):
                self.assertFalse(artifact.exists())
            else:
                self.assertEqual(artifact.read_bytes(), raw)
        self.assertEqual(source["omitted_snapshot_files"][0]["bytes"], len(files["fs/objects/synthetic"]))
        self.assertTrue(all("dependencies" not in item and "parent_id" not in item for item in events))

    def test_directory_and_root_or_prefixed_tar_are_equivalent(self):
        self.write()
        directory = import_orcareplay(self.source, self.destination)
        for prefix in ("", "run_abc123/"):
            with self.subTest(prefix=prefix):
                archive = self.archive(prefix=prefix)
                imported = import_orcareplay(archive, self.root / ("prefixed" if prefix else "root-tar"))
                self.assertEqual(imported, directory)
                self.assertEqual(read_source_files(archive), read_source_files(self.source))

    def test_zip_store_deflate_root_prefix_descriptors_and_comments_equivalent(self):
        self.write()
        directory = import_orcareplay(self.source, self.destination)
        for prefix in ("", "run_abc123/"):
            for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                for descriptor in (False, True):
                    with self.subTest(prefix=prefix, compression=compression, descriptor=descriptor):
                        archive = self.zip_archive(prefix=prefix, compression=compression,
                                                   descriptor=descriptor, comment=b"ordinary ZIP comment")
                        destination = self.root / f"zip-{bool(prefix)}-{compression}-{descriptor}"
                        self.assertEqual(import_orcareplay(archive, destination), directory)
                        self.assertEqual(read_source_files(archive), read_source_files(self.source))

    def test_zip_upstream_unflagged_utf8_and_empty_files_preserve_bytes(self):
        files = {**fixture_files(), "unicode-é.txt": "Synthetic Unicode π".encode(), "empty.txt": b""}
        archive = self.zip_archive(files)
        raw = bytearray(archive.read_bytes())
        _, headers = self.zip_headers(raw)
        for _, local, central in headers:
            for offset in (local + 6, central + 8):
                struct.pack_into("<H", raw, offset, struct.unpack_from("<H", raw, offset)[0] & ~0x800)
        archive.write_bytes(raw)
        bundle = import_orcareplay(archive, self.destination)
        self.assertEqual(len(bundle["events"]), 12)
        self.assertEqual(read_source_files(archive), {name: content for name, content in files.items() if not name.startswith("fs/")})

    def test_zip_unsigned_data_descriptors_supported(self):
        path = self.zip_archive(descriptor=True)
        raw = bytearray(path.read_bytes())
        trailer, headers = self.zip_headers(raw)
        cuts = []
        for _, local, central in headers:
            compressed = struct.unpack_from("<I", raw, central + 20)[0]
            name_size, extra_size = struct.unpack_from("<2H", raw, local + 26)
            descriptor = local + 30 + name_size + extra_size + compressed
            self.assertEqual(raw[descriptor:descriptor + 4], b"PK\x07\x08")
            cuts.append(descriptor)
        # Remove each optional descriptor signature and adjust absolute offsets.
        for _, local, central in headers:
            struct.pack_into("<I", raw, central + 42, local - 4 * sum(cut < local for cut in cuts))
        central_at = struct.unpack_from("<I", raw, trailer + 16)[0]
        struct.pack_into("<I", raw, trailer + 16, central_at - 4 * len(cuts))
        for cut in reversed(cuts):
            del raw[cut:cut + 4]
        path.write_bytes(raw)
        import_orcareplay(path, self.destination)

    def test_zip_snapshot_crc_validated_even_though_body_omitted(self):
        path = self.zip_archive(compression=zipfile.ZIP_STORED)
        raw = bytearray(path.read_bytes())
        _, headers = self.zip_headers(raw)
        _, local, _ = next(item for item in headers if item[0].startswith("fs/"))
        name_size, extra_size = struct.unpack_from("<2H", raw, local + 26)
        raw[local + 30 + name_size + extra_size] ^= 1
        path.write_bytes(raw)
        self.assert_rejected(path)

    def test_zip_integrity_and_payload_corruption_rejected(self):
        for corruption in ("stored", "deflate", "event-hash", "blob-hash"):
            with self.subTest(corruption=corruption):
                files = fixture_files()
                if corruption == "event-hash":
                    files["events.jsonl"] += b" "
                elif corruption == "blob-hash":
                    files[next(name for name in files if name.startswith("blobs/"))] = b"corrupt blob"
                path = self.zip_archive(files, compression=zipfile.ZIP_STORED if corruption == "stored" else zipfile.ZIP_DEFLATED)
                if corruption in ("stored", "deflate"):
                    raw = bytearray(path.read_bytes())
                    _, headers = self.zip_headers(raw)
                    _, local, _ = headers[0]
                    name_size, extra_size = struct.unpack_from("<2H", raw, local + 26)
                    raw[local + 30 + name_size + extra_size] ^= 0x80
                    path.write_bytes(raw)
                self.assert_rejected(path)

    def test_zip_unsafe_duplicate_conflicting_and_multiple_roots_rejected(self):
        for name in ("../escape", "/absolute", "C:/drive", "run/../escape", "bad\\path",
                     "./alias", "bad//path", "manifest.json", "other/manifest.json",
                     "events.jsonl/child"):
            with self.subTest(name=name):
                self.assert_rejected(self.zip_archive(extras=[(name, b"{}")]))
        for prefix, extras in (("run_abc123/", [("outside.txt", b"outside")]),
                               ("run_abc123/", [("other/", b"")]),
                               ("", [("conflict", b"file"), ("conflict/", b"")])):
            self.assert_rejected(self.zip_archive(prefix=prefix, extras=extras))

    def test_zip_links_special_members_and_ambiguous_attributes_rejected(self):
        for mode in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFSOCK, stat.S_IFDIR):
            with self.subTest(mode=mode):
                info = zipfile.ZipInfo("unsafe")
                info.create_system = 3
                info.external_attr = (mode | 0o600) << 16
                self.assert_rejected(self.zip_archive(extras=[(info, b"")]))
        info = zipfile.ZipInfo("unsafe")
        info.external_attr = 0x10
        self.assert_rejected(self.zip_archive(extras=[(info, b"")]))

    def test_zip_explicit_directory_entries_supported_and_counted(self):
        path = self.zip_archive(prefix="run_abc123/", extras=[("run_abc123/", b""), ("run_abc123/extra/", b"")])
        import_orcareplay(path, self.destination)
        with patch(MODULE + ".MAX_FILES", len(fixture_files()) + 1):
            with self.assertRaises(OrcaReplayImportError):
                read_source_files(path)

    def test_zip_structure_flags_methods_zip64_and_multidisk_rejected(self):
        for mutation in ("encrypted", "unsupported-flag", "method", "local-name", "local-method", "local-size",
                         "local-offset", "overlap", "disk", "member-disk", "zip64-count", "zip64-size",
                         "count-mismatch", "central-size", "trailing-bytes", "truncated", "descriptor"):
            with self.subTest(mutation=mutation):
                path = self.zip_archive(descriptor=mutation == "descriptor")
                raw = bytearray(path.read_bytes())
                trailer, headers = self.zip_headers(raw)
                _, local, central = headers[0]
                if mutation in ("encrypted", "unsupported-flag"):
                    flag = 1 if mutation == "encrypted" else 0x20
                    struct.pack_into("<H", raw, local + 6, flag)
                    struct.pack_into("<H", raw, central + 8, flag)
                elif mutation == "method":
                    struct.pack_into("<H", raw, local + 8, 12)
                    struct.pack_into("<H", raw, central + 10, 12)
                elif mutation == "local-name": raw[local + 30] ^= 1
                elif mutation == "local-method": struct.pack_into("<H", raw, local + 8, 0)
                elif mutation == "local-size": struct.pack_into("<I", raw, local + 22, 1)
                elif mutation == "local-offset": struct.pack_into("<I", raw, central + 42, 0xfffffffe)
                elif mutation == "overlap": struct.pack_into("<I", raw, headers[1][2] + 42, local)
                elif mutation == "disk": struct.pack_into("<H", raw, trailer + 4, 1)
                elif mutation == "member-disk": struct.pack_into("<H", raw, central + 34, 1)
                elif mutation == "zip64-count": struct.pack_into("<2H", raw, trailer + 8, 65535, 65535)
                elif mutation == "zip64-size": struct.pack_into("<I", raw, central + 24, 0xffffffff)
                elif mutation == "count-mismatch": struct.pack_into("<2H", raw, trailer + 8, 1, 1)
                elif mutation == "central-size": struct.pack_into("<I", raw, trailer + 12, 1)
                elif mutation == "trailing-bytes": raw += b"trailing"
                elif mutation == "truncated": del raw[-5:]
                elif mutation == "descriptor":
                    compressed = struct.unpack_from("<I", raw, central + 20)[0]
                    name_size, extra_size = struct.unpack_from("<2H", raw, local + 26)
                    raw[local + 30 + name_size + extra_size + compressed + 4] ^= 1
                path.write_bytes(raw)
                self.assert_rejected(path)

    def test_zip_extra_fields_and_zip64_local_headers_rejected(self):
        for extra in (b"\x01\x00\x00\x00", b"\x02\x00\x05\x00x", b"x"):
            info = zipfile.ZipInfo("extra.txt")
            info.extra = extra
            self.assert_rejected(self.zip_archive(extras=[(info, b"x")]))
        path = self.root / "force-zip64.orca"
        with zipfile.ZipFile(path, "w") as archive:
            for name, raw in fixture_files().items():
                with archive.open(name, "w", force_zip64=True) as member:
                    member.write(raw)
        self.assert_rejected(path)

    def test_zip_limits_checked_before_decompression_and_including_snapshots(self):
        path = self.zip_archive()
        for constant, value in (("MAX_FILE_BYTES", 20), ("MAX_TOTAL_BYTES", 100),
                                ("MAX_FILES", 2), ("MAX_ARCHIVE_BYTES", 20),
                                ("MAX_ZIP_DIRECTORY_BYTES", 20)):
            with self.subTest(limit=constant):
                with patch(MODULE + "." + constant, value), patch("adapters.orcareplay.bounded_zip.zlib.decompressobj", side_effect=AssertionError("must reject before decompression")):
                    self.assert_rejected(path)
        files = {**fixture_files(), "fs/oversized": b"x" * 10000}
        path = self.zip_archive(files)
        with patch(MODULE + ".MAX_FILE_BYTES", 9000): self.assert_rejected(path)
        with patch(MODULE + ".MAX_TOTAL_BYTES", sum(map(len, files.values())) - 1): self.assert_rejected(path)

    def test_zip_forged_small_expanded_size_stops_decompression(self):
        path = self.zip_archive({**fixture_files(), "fs/bomb": b"x" * (1024 * 1024)})
        raw = bytearray(path.read_bytes())
        _, headers = self.zip_headers(raw)
        _, local, central = next(item for item in headers if item[0] == "fs/bomb")
        struct.pack_into("<I", raw, local + 22, 1)
        struct.pack_into("<I", raw, central + 24, 1)
        path.write_bytes(raw)
        self.assert_rejected(path)

    def test_sse_stream_assembles_text_and_interleaved_tool_proposals(self):
        chunks = [
            {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "Read "}}]},
            {"choices": [{"index": 0, "delta": {"content": "fixture.", "tool_calls": [
                {"index": 0, "id": "call-a", "type": "function", "function": {"name": "read_", "arguments": '{"path":'}},
                {"index": 1, "id": "call-b", "function": {"name": "check", "arguments": '{}'}}]}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "function": {"name": "fixture", "arguments": '"synthetic"}'}}]}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"total_tokens": 12}},
        ]
        wire = ": keepalive\r\nevent: message\r\n\r\n" + "".join("data: " + json.dumps(c) + "\r\n\r\n" for c in chunks) + "data: [DONE]\r\n\r\n"
        raw = wire.encode()
        digest = hashlib.sha256(raw).hexdigest()
        ref = {"$blob": "sha256:" + digest, "bytes": len(raw), "media_type": "text/event-stream"}
        files = fixture_files([event(0, "model.response", payload=ref)])
        files[f"blobs/{digest[:2]}/{digest}"] = raw
        self.write(files)
        bundle = import_orcareplay(self.source, self.destination)
        data = bundle["events"][0]["data"]
        response = data["response"]
        self.assertEqual(response["stream_decode"]["status"], "complete")
        self.assertEqual(response["content"][0]["text"], "Read fixture.")
        self.assertEqual([(b["name"], b["input"]) for b in response["content"][1:]],
                         [("read_fixture", '{"path":"synthetic"}'), ("check", '{}')])
        self.assertTrue(all(b["execution_status"] == "proposal_only_not_execution" for b in response["content"][1:]))
        self.assertEqual(data["source_record"]["payload"], ref)
        self.assertEqual(data["resolved_payload"], wire)
        self.assertEqual((self.destination / f"artifacts/orcareplay/files/blobs/{digest[:2]}/{digest}").read_bytes(), raw)

    def test_sse_partial_and_unsupported_frames_are_honest(self):
        from adapters.orcareplay.importer import _response
        valid = 'data: {"choices":[{"index":0,"delta":{"content":"Partial"}}]}\n\n'
        cases = [
            (valid, "missing_done_marker"),
            (valid + 'data: {bad}\n\ndata: [DONE]\n\n', "malformed_json_frame"),
            (valid + 'data: {"error":{"message":"synthetic"}}\n\ndata: [DONE]\n\n', "unsupported_stream_frame"),
            (valid + 'data: [DONE]', "unterminated_sse_frame"),
            (valid + 'data: [DONE]\n\ndata: {}\n\n', "data_after_done"),
            ('data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"name":"read","arguments":"{"}}]},"finish_reason":"tool_calls"}]}\n\ndata: [DONE]\n\n', "invalid_or_incomplete_tool_arguments"),
        ]
        for wire, issue in cases:
            with self.subTest(issue=issue):
                response = _response(wire)
                self.assertEqual(response["stream_decode"]["status"], "partial")
                self.assertIn(issue, response["stream_decode"]["issues"])
        self.assertEqual(_response("ordinary response text"), "ordinary response text")

    def test_sse_multiline_data_and_choices_remain_distinct(self):
        from adapters.orcareplay.importer import _response
        wire = ('data: {"choices": [\n'
                'data: {"index":1,"delta":{"content":"Second"},"finish_reason":"stop"},\n'
                'data: {"index":0,"delta":{"content":"First"},"finish_reason":"stop"}]}\n\n'
                'data: [DONE]\n\n')
        response = _response(wire)
        self.assertEqual(response["stream_decode"]["status"], "complete")
        self.assertEqual([(b["choice_index"], b["text"]) for b in response["content"]], [(0, "First"), (1, "Second")])

    def test_monotonic_time_authoritative_wall_clock_retained(self):
        rows = [event(0, "run.start"), event(1, "run.end")]
        rows[1]["ts"] = "2020-01-01T00:00:00Z"
        self.write(fixture_files(rows))
        bundle = import_orcareplay(self.source, self.destination)
        self.assertEqual(bundle["events"][1]["timestamp"], "2026-10-18T10:00:01Z")
        self.assertEqual(bundle["events"][1]["data"]["source_record"]["ts"], rows[1]["ts"])
        self.assertEqual(bundle["events"][1]["data"]["mono_us"], 1000000)

    def test_repeated_request_history_is_context_without_invented_messages(self):
        self.write()
        bundle = import_orcareplay(self.source, self.destination)
        requests = [event for event in bundle["events"] if event["kind"] == "model.request"]
        self.assertEqual(len(requests), 2)
        self.assertEqual([len(event["data"]["request"]["messages"]) for event in requests], [1, 2])
        storyline = build_storyline(bundle, {})
        flow = build_flow(bundle, {}, storyline)
        self.assertEqual(len(flow["messages"]), len(bundle["events"]))
        request_messages = [message for message in flow["messages"] if message["kind"] == "model.request"]
        self.assertEqual([len(message["context"][0]["parameters"]) for message in request_messages], [1, 2])
        self.assertFalse(any(event["data"]["source_kind"].startswith("request.history") for event in bundle["events"]))

    def test_unknown_types_and_ambiguous_causes_are_neutral(self):
        self.write(fixture_files([event(0, "tool.call", attrs={"name": "x"}), event(1, "tool.call", attrs={"name": "x"}),
                                  event(2, "tool.result", causes=[0, 1], attrs={"name": "x"}),
                                  event(3, "future.event", causes=[0, 2], payload={"custom": True})]))
        bundle = import_orcareplay(self.source, self.destination)
        self.assertEqual(bundle["events"][-1]["kind"], "observation")
        self.assertEqual(bundle["events"][-1]["data"]["content"], {"custom": True})
        self.assertEqual(bundle["events"][2]["data"]["source_causes"]["sequences"], [0, 1])
        self.assertTrue(all("call_id" not in item and "dependencies" not in item and "parent_id" not in item for item in bundle["events"]))
        self.assertTrue(any("future.event" in gap for gap in bundle["manifest"]["completeness"]["gaps"]))

    def test_truncated_final_line_retained_with_gap(self):
        files = self.write(fixture_files([event(0, "run.start")], tail=b'{"seq":1,"payload":"\xf0\x9f'))
        bundle = import_orcareplay(self.source, self.destination)
        self.assertEqual(len(bundle["events"]), 1)
        self.assertTrue(bundle["manifest"]["source"]["truncated_final_line"])
        self.assertEqual((self.destination / "artifacts/orcareplay/files/events.jsonl").read_bytes(), files["events.jsonl"])

    def test_invalid_interior_or_blank_lines_fail(self):
        for tail in (b"{bad}\n{}\n", b"\n"):
            with self.subTest(tail=tail):
                self.write(fixture_files([], tail=tail))
                self.assert_rejected()

    def test_valid_final_line_without_newline_is_retained(self):
        self.write(fixture_files([], tail=encoded(event(0, "run.start"))))
        bundle = import_orcareplay(self.source, self.destination)
        self.assertEqual(len(bundle["events"]), 1)
        self.assertFalse(bundle["manifest"]["source"]["truncated_final_line"])

    def test_missing_or_corrupt_event_hash_rejected(self):
        for mode in ("missing", "mismatch"):
            with self.subTest(mode=mode):
                files = fixture_files()
                files = self.rewrite_manifest(files, integrity={} if mode == "missing" else {"events_sha256": "0" * 64})
                self.write(files)
                self.assert_rejected()

    def test_missing_tampered_size_or_malformed_blob_rejected(self):
        for mode in ("missing", "tampered", "size", "malformed"):
            with self.subTest(mode=mode):
                files = fixture_files()
                name = next(name for name in files if name.startswith("blobs/"))
                if mode == "missing":
                    del files[name]
                elif mode == "tampered":
                    files[name] = b"tampered"
                else:
                    rows = [json.loads(line) for line in files["events.jsonl"].splitlines()]
                    if mode == "size": rows[4]["payload"]["bytes"] += 1
                    else: rows[4]["payload"]["$blob"] = "sha256:../../escape"
                    files = fixture_files(rows)
                # Fresh subdirectories prevent a prior file masking missing data.
                self.source = self.root / mode
                self.write(files)
                self.assert_rejected()

    def test_duplicate_keys_and_nonfinite_numbers_in_json_rejected(self):
        for raw in (b'{"seq":0,"seq":1}', b'{"seq":NaN}', b'{"seq":1e999}'):
            with self.subTest(raw=raw):
                self.write(fixture_files([], tail=raw))
                self.assert_rejected()
        files = fixture_files()
        files["manifest.json"] = b'{"schema_version":"0.3.0","schema_version":"0.1.0"}'
        self.write(files)
        self.assert_rejected()

    def test_declared_json_blob_rejects_duplicate_keys_but_plain_text_code_is_text(self):
        raw = b'{"duplicate":1,"duplicate":2}'
        digest = hashlib.sha256(raw).hexdigest()
        ref = {"$blob": "sha256:" + digest, "bytes": len(raw), "media_type": "application/json"}
        files = fixture_files([event(0, "tool.result", payload=ref)])
        files[f"blobs/{digest[:2]}/{digest}"] = raw
        self.write(files)
        self.assert_rejected()
        self.source = self.root / "plain"
        files = fixture_files([event(0, "tool.result", payload="{ invalid code;")])
        self.write(files)
        bundle = import_orcareplay(self.source, self.destination)
        self.assertEqual(bundle["events"][0]["data"]["result"], "{ invalid code;")

    def test_binary_blob_preserved_without_lossy_text_preview(self):
        raw = b"\x00\xff\xfe"
        digest = hashlib.sha256(raw).hexdigest()
        ref = {"$blob": "sha256:" + digest, "bytes": len(raw), "media_type": "application/octet-stream"}
        files = fixture_files([event(0, "tool.result", payload=ref)])
        files[f"blobs/{digest[:2]}/{digest}"] = raw
        self.write(files)
        bundle = import_orcareplay(self.source, self.destination)
        result = bundle["events"][0]["data"]["result"]
        self.assertEqual(result["content_status"], "binary_artifact_only")
        self.assertEqual((self.destination / result["artifact_path"]).read_bytes(), raw)

    def test_bad_sequence_causes_or_timing_rejected(self):
        for updates in ({"seq": 2}, {"mono_us": -1}, {"mono_us": False}, {"ts": "bad"},
                        {"causes": [1]}, {"causes": [0, 0]}, {"turn": -1}, {"attrs": []}):
            with self.subTest(updates=updates):
                rows = [event(0, "run.start"), event(1, "run.end", **{})]
                rows[1].update(updates)
                self.write(fixture_files(rows))
                self.assert_rejected()
        self.write(fixture_files([event(0, "run.start", mono_us=10), event(1, "run.end", mono_us=9)]))
        self.assert_rejected()

    def test_schema_discriminator_and_future_versions(self):
        files = fixture_files()
        manifest = json.loads(files["manifest.json"])
        self.assertTrue(looks_like_orcareplay_manifest(manifest))
        for version in ("0.2.0", "9.0.0", [], None):
            with self.subTest(version=version):
                self.assertFalse(looks_like_orcareplay_manifest({**manifest, "schema_version": version}))
                self.write(self.rewrite_manifest(files, schema_version=version))
                self.assert_rejected()
        self.assertFalse(looks_like_orcareplay_manifest({"schema_version": "0.1", "agent": "OrcaReplay"}))

    def test_count_mismatches_become_explicit_gaps(self):
        files = self.rewrite_manifest(fixture_files(), counts={"events": 100, "blobs": 9},
                                      integrity={"events_sha256": json.loads(fixture_files()["manifest.json"])["integrity"]["events_sha256"], "blob_count": 8})
        self.write(files)
        bundle = import_orcareplay(self.source, self.destination)
        gaps = bundle["manifest"]["completeness"]["gaps"]
        self.assertTrue(any("event count" in gap for gap in gaps))
        self.assertTrue(any("counts.blobs" in gap for gap in gaps))
        self.assertTrue(any("integrity.blob_count" in gap for gap in gaps))

    def test_directory_symlinks_and_ancestor_symlinks_rejected(self):
        self.write()
        (self.source / "link").symlink_to(self.root / "nonexistent")
        self.assert_rejected()
        (self.source / "link").unlink()
        linked = self.root / "linked"
        linked.symlink_to(self.source, target_is_directory=True)
        self.assert_rejected(linked)
        linked_destination = self.root / "linked-output"
        linked_destination.symlink_to(self.root / "other", target_is_directory=True)
        with self.assertRaises(OrcaReplayImportError):
            import_orcareplay(self.source, linked_destination / "output")

    def test_directory_fifo_rejected_without_opening(self):
        import os
        self.write()
        os.mkfifo(self.source / "named-pipe")
        self.assert_rejected()

    def test_archive_links_special_traversal_duplicate_and_multiple_roots_rejected(self):
        members = []
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE):
            member = tarfile.TarInfo("unsafe")
            member.type, member.linkname = kind, "manifest.json"
            members.append(member)
        for name in ("../escape", "/absolute", "C:/drive", "run/../escape", "bad\\path", "manifest.json", "other/manifest.json"):
            member = tarfile.TarInfo(name)
            member.size = 2
            members.append(member)
        for member in members:
            with self.subTest(name=member.name, kind=member.type):
                path = self.archive(extras=[(member, b"{}")])
                self.assert_rejected(path)

    def test_hidden_tar_metadata_is_bounded_before_processing(self):
        path = self.root / "hidden-metadata.tar"
        for kind in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME):
            with self.subTest(kind=kind):
                member = tarfile.TarInfo("hidden-metadata")
                member.type = kind
                member.size = 2 * 1024 * 1024
                path.write_bytes(member.tobuf(format=tarfile.USTAR_FORMAT))
                self.assert_rejected(path)
        # Each hidden record counts, even if it consumes no visible member.
        member = tarfile.TarInfo("hidden-metadata")
        member.type = tarfile.XGLTYPE
        path.write_bytes(member.tobuf(format=tarfile.USTAR_FORMAT) * 4)
        with patch(MODULE + ".MAX_FILES", 2): self.assert_rejected(path)

    def test_compressed_tar_rejected(self):
        path = self.root / "compressed.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for name, raw in fixture_files().items():
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                archive.addfile(member, io.BytesIO(raw))
        self.assert_rejected(path)

    def test_bounded_files_total_records_lines_and_directories(self):
        self.write()
        for constant, value in (("MAX_FILE_BYTES", 20), ("MAX_TOTAL_BYTES", 100),
                                ("MAX_FILES", 2), ("MAX_RECORDS", 2),
                                ("MAX_LINE_BYTES", 20), ("MAX_MANIFEST_BYTES", 20)):
            with self.subTest(limit=constant):
                with patch(MODULE + "." + constant, value):
                    self.assert_rejected()
        empty = self.root / "empty"
        for index in range(5): (empty / str(index)).mkdir(parents=True)
        with patch(MODULE + ".MAX_FILES", 2):
            with self.assertRaises(OrcaReplayImportError): read_source_files(empty)
        path = self.archive()
        with patch(MODULE + ".MAX_ARCHIVE_BYTES", 20): self.assert_rejected(path)
        with patch(MODULE + ".MAX_FILES", 2): self.assert_rejected(path)

    def test_destination_existing_never_modified(self):
        self.write()
        self.destination.mkdir()
        marker = self.destination / "keep"
        marker.write_text("Original destination")
        with self.assertRaises(OrcaReplayImportError): import_orcareplay(self.source, self.destination)
        self.assertEqual(marker.read_text(), "Original destination")

    def test_source_only_fields_never_drive_filesystem_or_execution(self):
        rows = [event(0, "future.event", payload={"path": "../../outside", "command": "do not execute", "artifact_refs": [{"path": "../../outside"}]},
                      extensions={"custom": True})]
        self.write(fixture_files(rows))
        with patch("subprocess.run", side_effect=AssertionError("must never run commands")), patch("socket.create_connection", side_effect=AssertionError("must never connect")):
            bundle = import_orcareplay(self.source, self.destination)
        self.assertEqual(bundle["events"][0]["data"]["source_record"], rows[0])


if __name__ == "__main__":
    unittest.main()
