"""Synthetic native Artex v3 archive compatibility across localized text.

The fixtures are authored in memory and contain no captured model output,
credentials, remote targets, or executable behavior.  Every locale uses the
same source schema and evidence chain so a translated display string cannot
become a dependency or milestone signal by accident.
"""

import hashlib
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

from adapters.artex.importer import import_artex
from web.render import build_payload


LOCALIZED = {
    "en": {
        "goal": "Use exposed credentials to retrieve the protected document.",
        "system": "Inspect only the authorized local synthetic target.",
        "prompt": "Find credentials, sign in, and read the protected document.",
        "model": "I will inspect the recorded structured responses in order.",
        "transcript": "The credentials were observed in the authorized fixture.",
        "credential_message": "Synthetic credentials disclosed.",
        "token_message": "Synthetic session created.",
        "document": "Synthetic protected document.",
        "transcript_file": "task-history.jsonl",
        "workspace_dir": "evidence",
        "workspace_file": "evidence-note.txt",
        "workspace": "Protected-document retrieval evidence.\n",
    },
    "ko": {
        "goal": "노출된 자격 증명을 사용해 보호된 문서를 가져오세요.",
        "system": "승인된 로컬 합성 대상만 검사하세요.",
        "prompt": "자격 증명을 찾고 로그인한 뒤 보호된 문서를 읽으세요.",
        "model": "기록된 구조화 응답을 순서대로 확인하겠습니다.",
        "transcript": "승인된 테스트 환경에서 자격 증명이 확인되었습니다.",
        "credential_message": "합성 자격 증명이 노출되었습니다.",
        "token_message": "합성 세션이 생성되었습니다.",
        "document": "합성 보호 문서입니다.",
        "transcript_file": "작업-기록.jsonl",
        "workspace_dir": "증거",
        "workspace_file": "증거-메모.txt",
        "workspace": "보호 문서 검색 증거입니다.\n",
    },
    "zh": {
        "goal": "使用暴露的凭据获取受保护文档。",
        "system": "仅检查已授权的本地合成目标。",
        "prompt": "查找凭据、登录，然后读取受保护文档。",
        "model": "我会按顺序检查记录的结构化响应。",
        "transcript": "已在授权测试环境中发现凭据。",
        "credential_message": "合成凭据已暴露。",
        "token_message": "合成会话已创建。",
        "document": "这是合成的受保护文档。",
        "transcript_file": "任务-记录.jsonl",
        "workspace_dir": "证据",
        "workspace_file": "证据-说明.txt",
        "workspace": "受保护文档检索证据。\n",
    },
}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def activity(row_id, second, kind, call_id, detail, summary, *, phase=None):
    row = {
        "id": row_id,
        "exploration_id": 42,
        "node_id": 7,
        "worker": "work#1",
        "kind": kind,
        "tool": "Bash",
        "tool_use_id": call_id,
        "is_error": False,
        "summary": summary,
        "detail": detail,
        "metadata": {},
        "created_at": f"2026-10-17T12:00:{second:02d}Z",
        "main_seg": None,
    }
    if phase is not None:
        row["phase"] = phase
    return row


def native_members(locale):
    """Return a small, realistic native TaskArchiveSnapshot v3 member set."""
    text = LOCALIZED[locale]
    target = "http://127.0.0.1:8765/private/document"
    activities = [
        activity(101, 1, "tool_use", "baseline", encoded({"command": "curl -sS " + target}).decode(), text["prompt"]),
        activity(102, 2, "tool_result", "baseline", 'status 401\n{"error":"unauthorized"}', text["prompt"]),
        activity(103, 3, "tool_use", "credentials", encoded({"command": "curl -sS http://127.0.0.1:8765/public/debug/credentials"}).decode(), text["prompt"]),
        activity(104, 4, "tool_result", "credentials", "status 200\n" + json.dumps({
            "username": "synthetic-user", "password": "synthetic-password",
            "login_path": "/session", "message": text["credential_message"],
        }, ensure_ascii=False), text["credential_message"]),
        activity(105, 5, "tool_use", "login", encoded({
            "command": "curl -sS -X POST http://127.0.0.1:8765/session "
                       "-d 'username=synthetic-user&password=synthetic-password'",
        }).decode(), text["prompt"]),
        activity(106, 6, "tool_result", "login", "status 200\n" + json.dumps({
            "bearer_token": "synthetic-bearer", "session_id": "synthetic-session",
            "token_type": "Bearer", "message": text["token_message"],
        }, ensure_ascii=False), text["token_message"]),
        activity(107, 7, "tool_use", "document", encoded({
            "command": "curl -sS -H 'Authorization: Bearer synthetic-bearer' " + target,
        }).decode(), text["prompt"], phase="exploitation"),
        activity(108, 8, "tool_result", "document", "status 200\n" + json.dumps({
            "doc_id": "synthetic-doc", "content": text["document"],
            "proof_marker": "synthetic-proof",
        }, ensure_ascii=False), text["document"]),
    ]
    task = {
        "id": 23, "exploration_id": 42, "name": "Synthetic localized archive",
        "description": text["prompt"], "goal": text["goal"], "status": "completed",
        "created_at": "2026-10-17T12:00:00Z",
    }
    request = {
        "system": [{"type": "text", "text": text["system"]}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": text["prompt"]}]}],
        "tools": ["Bash"],
        "tool_definitions": [{"name": "Bash", "input_schema": {"type": "object"}}],
    }
    response = {
        "content": [{"type": "text", "text": text["model"]}],
        "stop_reason": "end_turn",
    }
    llm_row = {
        "id": 201, "ts": "2026-10-17T12:00:00.500Z", "model": "synthetic-model",
        "profile_name": "synthetic", "session_id": "exp42-worker-i7", "task_id": "23",
        "worker": "worker", "status": "ok", "request_body": json.dumps(request, ensure_ascii=False),
        "response_body": json.dumps(response, ensure_ascii=False),
        "raw_request": json.dumps(request, ensure_ascii=False),
        "raw_response": json.dumps(response, ensure_ascii=False),
    }
    manifest = {
        "format_version": 3,
        "created_at": "2026-10-17T12:00:12Z",
        "task_id": 23,
        "exploration_id": 42,
        "source_task_ids": [],
        "hosts": ["127.0.0.1"],
        "exclusive_hosts": [],
        "exclusive_asset_ids": [],
        "tables": {
            "tasks": [task],
            "activity": activities,
            "llm_records": [],
            "exploration_nodes": [{
                "id": 7, "exploration_id": 42, "kind": "intent", "title": text["goal"],
            }],
        },
        "streamed_tables": {"llm_records": "database/llm_records.ndjson"},
        "data_counts": {"tasks": 1, "activity": 8, "llm_records": 1, "exploration_nodes": 1},
        "aggregate_stats": {},
    }
    transcript_row = {
        "uuid": "localized-message", "session_id": "exp42-worker-i7", "agent_id": "work#1",
        "type": "message", "timestamp": "2026-10-17T12:00:09Z",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text["transcript"]}]},
    }
    transcript_name = "files/transcripts/" + text["transcript_file"]
    # This is the exact workspace layout staged by Artex's native producer.
    workspace_name = "files/tasks/23/" + text["workspace_dir"] + "/" + text["workspace_file"]
    return {
        "manifest.json": encoded(manifest),
        "database/llm_records.ndjson": encoded(llm_row) + b"\n",
        transcript_name: encoded(transcript_row) + b"\n",
        workspace_name: text["workspace"].encode("utf-8"),
    }


def write_tar(path, members):
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as archive:
        for name, raw in members.items():
            item = tarfile.TarInfo(name)
            item.size = len(raw)
            item.mtime = 0
            archive.addfile(item, io.BytesIO(raw))


class ArtexMultilingualArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def import_locale(self, locale, *, zstd=False):
        members = native_members(locale)
        archive = self.root / (locale + ".native.tar")
        write_tar(archive, members)
        source = archive
        if zstd:
            source = archive.with_suffix(".tar.zst")
            with source.open("wb") as output:
                subprocess.run(["zstd", "-q", "-c", str(archive)], stdout=output, check=True)
        destination = self.root / (locale + ("-zstd" if zstd else "-tar") + "-bundle")
        actual_popen = subprocess.Popen

        def decoder_only(args, *popen_args, **popen_kwargs):
            if (zstd and isinstance(args, list) and len(args) == 4
                    and Path(args[0]).name == "zstd" and args[1:] == ["-q", "-d", "-c"]):
                return actual_popen(args, *popen_args, **popen_kwargs)
            raise AssertionError("recorded command execution")

        # Import and presentation must remain offline and must never execute a
        # command found in an archive.  zstd setup, when requested, happens
        # before these guards and is only test-fixture compression.
        with patch.object(socket, "create_connection", side_effect=AssertionError("network access")), \
                patch.object(urllib.request, "urlopen", side_effect=AssertionError("provider access")), \
                patch.object(subprocess, "Popen", side_effect=decoder_only):
            bundle = import_artex(source, destination)
            payload = build_payload(bundle, {})
        return members, destination, bundle, payload

    def test_english_korean_and_chinese_native_archives_have_identical_structural_semantics(self):
        structural_results = {}
        for locale, localized in LOCALIZED.items():
            with self.subTest(locale=locale):
                members, destination, bundle, payload = self.import_locale(locale)
                flow = payload["flow"]
                events = bundle["events"]

                # The complete native bytes, including Unicode paths and
                # workspace content, remain checksum-verifiable artifacts.
                inventory = {item["path"]: item for item in bundle["manifest"]["source"]["archive_inventory"]}
                for name, raw in members.items():
                    preserved = destination / "artifacts" / "artex" / name
                    self.assertEqual(preserved.read_bytes(), raw)
                    self.assertEqual(inventory[name]["sha256"], hashlib.sha256(raw).hexdigest())

                task_message = next(message for message in flow["messages"] if message["kind"] == "task.input")
                transcript = next(message for message in flow["messages"]
                                  if bundle_event(bundle, message)["data"].get("source_kind") == "archive.transcript")
                model = next(message for message in flow["messages"] if message["kind"] == "model.response")
                self.assertEqual(task_message["text"], localized["goal"])
                self.assertIn(localized["transcript"], transcript["text"])
                self.assertIn(localized["model"], model["text"])
                self.assertTrue(transcript["compact_hidden"])
                self.assertEqual(transcript["compact_hidden_basis"], "recorded_context_not_an_additional_exchange")
                self.assertFalse(task_message.get("compact_hidden", False))

                semantic_tags = [tag for message in flow["messages"] for tag in message["semantic_tags"]]
                tags = [(message["event_ids"][0], tag["label"], tuple(tag["event_ids"]))
                        for message in flow["messages"] for tag in message["semantic_tags"]]
                self.assertEqual([label for _, label, _ in tags], [
                    "Credentials obtained", "Token obtained", "Exploitation attempt", "Document retrieved",
                ])
                self.assertEqual(tags[2][2], (
                    "artex.activity.102", "artex.activity.106", "artex.activity.107",
                ))
                self.assertTrue(all("synthetic-bearer" not in json.dumps(tag) for tag in semantic_tags))

                events_by_id = {event["id"]: event for event in events}
                tool_dependencies = {
                    event["id"]: event.get("dependencies") for event in events
                    if event["kind"] == "tool.end"
                }
                self.assertEqual(tool_dependencies, {
                    "artex.activity.102": [{"event_id": "artex.activity.101", "provenance": "recorded"}],
                    "artex.activity.104": [{"event_id": "artex.activity.103", "provenance": "recorded"}],
                    "artex.activity.106": [{"event_id": "artex.activity.105", "provenance": "recorded"}],
                    "artex.activity.108": [{"event_id": "artex.activity.107", "provenance": "recorded"}],
                })
                self.assertEqual(events_by_id["artex.llm_records.201.response"]["parent_id"],
                                 "artex.llm_records.201.request")
                transcript_event = events_by_id[transcript["event_ids"][0]]
                self.assertNotIn("dependencies", transcript_event)
                self.assertNotIn("parent_id", transcript_event)
                self.assertTrue(any(edge["kind"] == "dependency" for edge in payload["storyline"]["edges"]))

                structural_results[locale] = {
                    "event_kinds": [event["kind"] for event in events],
                    "tag_labels": [label for _, label, _ in tags],
                    "dependency_count": sum("dependencies" in event for event in events),
                    "full_messages": len(flow["messages"]),
                    "compact_visible": sum(not message.get("compact_hidden", False) for message in flow["messages"]),
                }

        self.assertEqual(structural_results["en"], structural_results["ko"])
        self.assertEqual(structural_results["en"], structural_results["zh"])

    @unittest.skipUnless(shutil.which("zstd"), "zstd is required for native .tar.zst coverage")
    def test_korean_native_tar_zst_matches_the_same_localized_scenario(self):
        members, destination, bundle, payload = self.import_locale("ko", zstd=True)
        self.assertEqual(bundle["manifest"]["agent"]["extensions"]["archive_format_version"], 3)
        self.assertEqual({item["path"] for item in bundle["manifest"]["source"]["archive_inventory"]}, set(members))
        task_message = next(message for message in payload["flow"]["messages"]
                            if message["kind"] == "task.input")
        self.assertEqual(task_message["text"], LOCALIZED["ko"]["goal"])
        relative = ("files/tasks/23/" + LOCALIZED["ko"]["workspace_dir"] + "/" +
                    LOCALIZED["ko"]["workspace_file"])
        self.assertEqual((destination / "artifacts/artex" / relative).read_bytes(), members[relative])


def bundle_event(bundle, message):
    by_id = {event["id"]: event for event in bundle["events"]}
    return by_id[message["event_ids"][0]]


if __name__ == "__main__":
    unittest.main()
