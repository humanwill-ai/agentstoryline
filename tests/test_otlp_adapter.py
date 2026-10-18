"""Synthetic OTLP protobuf-JSON adapter tests; no real model or user trace data."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from adapters.otlp import OTLPImportError, import_otlp, looks_like_otlp_export
from core.bundle import BundleValidationError, load_bundle
from web.flow import build_flow
from web.storyline import build_storyline


TRACE = "00112233445566778899aabbccddeeff"
ROOT_SPAN = "0011223344556677"
TOOL_SPAN = "8899aabbccddeeff"


def av(value):
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    raise TypeError(value)


def attrs(**values):
    return [{"key": key.replace("__", "."), "value": av(value)} for key, value in values.items()]


def fixture():
    resource = {"attributes": attrs(service__name="synthetic-agent", telemetry__sdk__version="1.2.3"),
                "droppedAttributesCount": 1}
    inference = {
        "traceId": TRACE, "spanId": ROOT_SPAN, "name": "chat synthetic-model",
        "kind": 3, "startTimeUnixNano": "1791633600000000001",
        "endTimeUnixNano": "1791633601000000999",
        "attributes": attrs(gen_ai__operation__name="chat", gen_ai__provider__name="synthetic",
                            gen_ai__request__model="synthetic-model", gen_ai__response__model="synthetic-model-v2",
                            gen_ai__agent__id="agent-synthetic", gen_ai__agent__name="Synthetic Agent",
                            gen_ai__input__messages="synthetic input messages",
                            gen_ai__output__messages="synthetic output messages",
                            gen_ai__usage__input_tokens=12, gen_ai__usage__output_tokens=4),
        "events": [{"timeUnixNano": "1791633600500000000", "name": "synthetic checkpoint",
                    "attributes": attrs(synthetic__value=7)}],
        "links": [{"traceId": "ffeeddccbbaa99887766554433221100", "spanId": "1234567890abcdef",
                   "attributes": attrs(synthetic__link="context")}],
        "droppedAttributesCount": 2,
        "status": {"code": 1},
    }
    tool = {
        "traceId": TRACE, "spanId": TOOL_SPAN, "parentSpanId": ROOT_SPAN,
        "name": "execute_tool inspect", "kind": 1,
        "startTimeUnixNano": "1791633600200000000", "endTimeUnixNano": "1791633600400000000",
        "attributes": attrs(gen_ai__operation__name="execute_tool", gen_ai__tool__name="inspect",
                            gen_ai__tool__call__id="call-synthetic",
                            gen_ai__tool__call__arguments='{"target":"fixture"}',
                            gen_ai__tool__call__result='{"ok":true}'),
        "status": {"code": 1},
    }
    ordinary = {
        "traceId": TRACE, "spanId": "aaaaaaaaaaaaaaaa", "name": "ordinary database",
        "startTimeUnixNano": "1791633600100000000", "endTimeUnixNano": "1791633600150000000",
        "attributes": attrs(db__system="synthetic"),
    }
    logs = [
        {"timeUnixNano": "1791633600000000010", "traceId": TRACE, "spanId": ROOT_SPAN,
         "body": {"stringValue": "synthetic prompt"},
         "attributes": attrs(event__name="gen_ai.content.prompt", gen_ai__input__messages="synthetic prompt")},
        {"timeUnixNano": "1791633601000000010", "traceId": TRACE, "spanId": ROOT_SPAN,
         "body": {"kvlistValue": {"values": [{"key": "text", "value": {"stringValue": "synthetic response"}}]}},
         "attributes": attrs(event__name="gen_ai.content.completion", gen_ai__output__messages="synthetic response")},
        {"timeUnixNano": "1791633601000000020", "body": {"stringValue": "ordinary log"},
         "attributes": attrs(event__name="ordinary.event")},
    ]
    return {
        "resourceSpans": [{"resource": resource, "schemaUrl": "https://example.invalid/resource/1",
                            "scopeSpans": [{
            "scope": {"name": "synthetic.instrumentation", "version": "0.1", "droppedAttributesCount": 3},
            "schemaUrl": "https://example.invalid/genai/1",
            "spans": [inference, tool, ordinary],
        }]}],
        "resourceLogs": [{"resource": resource, "schemaUrl": "https://example.invalid/resource/1",
                           "scopeLogs": [{
            "scope": {"name": "synthetic.instrumentation", "version": "0.1"},
            "schemaUrl": "https://example.invalid/genai/1",
            "logRecords": logs,
        }]}],
    }


class OTLPAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "export.json"
        self.destination = self.root / "bundle"

    def write(self, value=None, raw=None):
        data = raw if raw is not None else (json.dumps(fixture() if value is None else value, ensure_ascii=False) + "\n").encode()
        self.source.write_bytes(data)
        return data

    def run_import(self, value=None):
        self.write(value)
        return import_otlp(self.source, self.destination)

    def test_imports_genai_spans_and_correlated_logs_with_exact_artifact(self):
        raw = self.write()
        bundle = import_otlp(self.source, self.destination)
        self.assertEqual(bundle, load_bundle(self.destination))
        self.assertEqual((self.destination / "artifacts/otlp/export.json").read_bytes(), raw)
        self.assertEqual(bundle["manifest"]["artifact_refs"][0]["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(bundle["manifest"]["source"]["adapter"], "otlp")
        self.assertEqual(bundle["manifest"]["agent"]["extensions"]["services"], ["synthetic-agent"])
        self.assertEqual(len(bundle["events"]), 6)
        self.assertEqual([event["kind"] for event in bundle["events"]],
                         ["model.request", "model.request", "tool.start", "tool.end", "model.response", "model.response"])
        self.assertTrue(all("dependencies" not in event for event in bundle["events"]))
        self.assertTrue(all("parent_id" not in event for event in bundle["events"]))

    def test_structural_parent_and_log_correlation_are_not_data_dependencies(self):
        bundle = self.run_import()
        tool = next(event for event in bundle["events"] if event["kind"] == "tool.start")
        hierarchy = tool["data"]["span_hierarchy"]
        self.assertEqual(hierarchy["parent_span_id"], ROOT_SPAN)
        self.assertTrue(hierarchy["parent_present_in_export"])
        self.assertIn("not_data_dependency", hierarchy["relationship"])
        tool_end = next(event for event in bundle["events"] if event["kind"] == "tool.end")
        self.assertIn("not_data_dependency", tool_end["data"]["span_lifecycle"]["relationship"])
        log = next(event for event in bundle["events"] if event["id"].startswith("otlp.log"))
        self.assertTrue(log["data"]["span_correlation"]["mapped_span_present"])
        self.assertTrue(any("parentSpanId" in gap for gap in bundle["manifest"]["completeness"]["gaps"]))

    def test_preserves_nanoseconds_and_partitions_request_response_attributes(self):
        bundle = self.run_import()
        request = next(event for event in bundle["events"] if event["id"].endswith(".request"))
        response = next(event for event in bundle["events"] if event["id"].endswith(".response"))
        self.assertEqual(request["data"]["otlp_time_unix_nano"], "1791633600000000001")
        self.assertEqual(response["data"]["otlp_time_unix_nano"], "1791633601000000999")
        self.assertEqual(request["data"]["attributes"]["gen_ai.request.model"], "synthetic-model")
        self.assertEqual(response["data"]["attributes"]["gen_ai.usage.output_tokens"], 4)
        self.assertEqual(request["data"]["embedded_span_events"][0]["attributes"]["synthetic.value"], 7)
        self.assertEqual(request["data"]["request"]["messages"], "synthetic input messages")
        self.assertEqual(response["data"]["response"]["content"], "synthetic output messages")
        self.assertEqual(request["data"]["model_identity"], {
            "basis": "recorded_otlp_semantic_attribute_not_provider_attestation",
            "model": "synthetic-model", "provider": "synthetic",
        })
        self.assertEqual(response["data"]["model_identity"]["model"], "synthetic-model-v2")
        self.assertEqual(request["data"]["agent_id"], "agent-synthetic")
        self.assertEqual(request["data"]["recorded_agent_identity"]["name"], "Synthetic Agent")
        self.assertEqual(request["data"]["dropped_counts"]["attributes"], 2)
        self.assertEqual(request["data"]["resource_metadata"], {
            "dropped_attributes_count": 1, "schema_url": "https://example.invalid/resource/1",
        })
        self.assertEqual(request["data"]["scope"]["schema_url"], "https://example.invalid/genai/1")
        self.assertEqual(request["data"]["scope"]["dropped_attributes_count"], 3)
        self.assertEqual(request["data"]["span_links"][0]["relationship"],
                         "recorded_otlp_span_link_not_data_dependency")
        self.assertTrue(any("dropped attributes" in gap for gap in bundle["manifest"]["completeness"]["gaps"]))

        tool_start = next(event for event in bundle["events"] if event["kind"] == "tool.start")
        tool_end = next(event for event in bundle["events"] if event["kind"] == "tool.end")
        self.assertEqual(tool_start["data"]["arguments"], '{"target":"fixture"}')
        self.assertEqual(tool_end["data"]["result"], '{"ok":true}')

    def test_genai_log_body_is_exposed_to_request_and_response_display_fields(self):
        bundle = self.run_import()
        prompt = next(event for event in bundle["events"] if event["id"] == "otlp.log.0")
        completion = next(event for event in bundle["events"] if event["id"] == "otlp.log.1")
        self.assertEqual(prompt["data"]["request"], {"content": "synthetic prompt"})
        self.assertEqual(completion["data"]["response"], {"text": "synthetic response"})

    def test_imported_fixture_builds_generic_storyline_and_chat(self):
        bundle = self.run_import()
        storyline = build_storyline(bundle, {})
        flow = build_flow(bundle, {}, storyline)
        self.assertEqual(len(storyline["activities"]), 4)
        intervals = [activity for activity in storyline["activities"] if activity["timing"] == "interval"]
        self.assertEqual(len(intervals), 2)
        self.assertEqual({activity["category"] for activity in intervals}, {"model", "tool"})
        self.assertEqual(storyline["edges"], [])
        self.assertEqual(len(flow["messages"]), len(bundle["events"]))
        models = [entity for entity in flow["entities"] if entity["kind"] == "model"]
        self.assertEqual({entity.get("model") for entity in models}, {"synthetic-model", "synthetic-model-v2"})
        agents = [entity for entity in flow["entities"] if entity["kind"] in ("agent", "worker", "coordinator")]
        self.assertTrue(any("agent-synthetic" in json.dumps(entity.get("identity")) for entity in agents))

    def test_operation_allowlist_and_agent_interval_pairing(self):
        value = fixture()
        container = value["resourceSpans"][0]["scopeSpans"][0]
        template = container["spans"][0]
        container["spans"] = []
        operations = [
            ("chat", "1000000000000001"),
            ("create_agent", "1000000000000002"),
            ("invoke_agent", "1000000000000003"),
            ("workflow", "1000000000000004"),
            ("retrieval", "1000000000000005"),
            ("future_unknown", "1000000000000006"),
        ]
        for operation, span_id in operations:
            span = json.loads(json.dumps(template))
            span["spanId"] = span_id
            span["name"] = operation + " synthetic"
            span["attributes"] = attrs(gen_ai__operation__name=operation,
                                       gen_ai__agent__id="agent-synthetic")
            container["spans"].append(span)
        value.pop("resourceLogs")
        bundle = self.run_import(value)
        kinds = [event["kind"] for event in bundle["events"]]
        self.assertEqual(kinds.count("model.request"), 1)
        self.assertEqual(kinds.count("model.response"), 1)
        self.assertEqual(kinds.count("run.start"), 2)
        self.assertEqual(kinds.count("run.end"), 2)
        self.assertEqual(kinds.count("observation"), 3)
        observations = [event for event in bundle["events"] if event["kind"] == "observation"]
        self.assertEqual({event["data"]["operation"] for event in observations},
                         {"workflow", "retrieval", "future_unknown"})
        self.assertTrue(all(event["data"]["classification"] ==
                            "unsupported_genai_operation_preserved_as_observation"
                            for event in observations))

        storyline = build_storyline(bundle, {})
        intervals = [activity for activity in storyline["activities"] if activity["timing"] == "interval"]
        self.assertEqual(len(intervals), 3)
        self.assertEqual([activity["category"] for activity in intervals].count("run"), 2)
        self.assertEqual(storyline["edges"], [])

    def test_non_genai_records_are_counted_but_not_mapped(self):
        bundle = self.run_import()
        coverage = bundle["manifest"]["completeness"]["extensions"]
        self.assertEqual(coverage["mapped_genai_spans"], 2)
        self.assertEqual(coverage["unmapped_spans"], 1)
        self.assertEqual(coverage["skipped_log_records"], {"non_genai": 1})
        self.assertTrue(any("non-GenAI spans" in gap for gap in bundle["manifest"]["completeness"]["gaps"]))

    def test_orphan_and_untimestamped_genai_logs_have_explicit_coverage(self):
        value = fixture()
        records = value["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        records[0]["spanId"] = "bbbbbbbbbbbbbbbb"
        records.append({"traceId": TRACE, "spanId": ROOT_SPAN,
                        "body": {"stringValue": "missing time"},
                        "attributes": attrs(event__name="gen_ai.content.prompt")})
        bundle = self.run_import(value)
        gaps = bundle["manifest"]["completeness"]["gaps"]
        self.assertTrue(any("absent from the mapped" in gap for gap in gaps))
        self.assertTrue(any("lack timeUnixNano" in gap for gap in gaps))
        self.assertEqual(bundle["manifest"]["completeness"]["extensions"]["skipped_log_records"]["missing_timestamp"], 1)

    def test_multiple_traces_are_grouped_without_cross_trace_links(self):
        value = fixture()
        second = json.loads(json.dumps(value["resourceSpans"][0]["scopeSpans"][0]["spans"][0]))
        second["traceId"] = "ffeeddccbbaa99887766554433221100"
        second["spanId"] = "1234567890abcdef"
        value["resourceSpans"][0]["scopeSpans"][0]["spans"].append(second)
        bundle = self.run_import(value)
        self.assertEqual(len(bundle["manifest"]["source"]["trace_ids"]), 2)
        self.assertTrue(any("2 trace IDs" in gap for gap in bundle["manifest"]["completeness"]["gaps"]))

    def test_non_genai_only_export_is_valid_empty_partial_bundle(self):
        value = fixture()
        value.pop("resourceLogs")
        value["resourceSpans"][0]["scopeSpans"][0]["spans"] = [
            value["resourceSpans"][0]["scopeSpans"][0]["spans"][2]
        ]
        bundle = self.run_import(value)
        self.assertEqual(bundle["events"], [])
        self.assertEqual(bundle["manifest"]["completeness"]["status"], "partial")
        self.assertTrue(any("No spans carrying" in gap for gap in bundle["manifest"]["completeness"]["gaps"]))

    def test_detection_is_envelope_only(self):
        self.assertTrue(looks_like_otlp_export({"resourceSpans": []}))
        self.assertTrue(looks_like_otlp_export({"resourceLogs": []}))
        self.assertFalse(looks_like_otlp_export({"events": []}))
        self.assertFalse(looks_like_otlp_export({"resourceSpans": {}}))

    def test_uppercase_identifiers_are_accepted_and_normalized_for_correlation(self):
        value = fixture()
        for span in value["resourceSpans"][0]["scopeSpans"][0]["spans"]:
            span["traceId"] = span["traceId"].upper()
            span["spanId"] = span["spanId"].upper()
            if span.get("parentSpanId"):
                span["parentSpanId"] = span["parentSpanId"].upper()
        for record in value["resourceLogs"][0]["scopeLogs"][0]["logRecords"]:
            if record.get("traceId"):
                record["traceId"] = record["traceId"].upper()
            if record.get("spanId"):
                record["spanId"] = record["spanId"].upper()
        bundle = self.run_import(value)
        self.assertEqual(bundle["manifest"]["source"]["trace_ids"], [TRACE])
        correlated = [event for event in bundle["events"] if event["id"].startswith("otlp.log")]
        self.assertTrue(all(event["data"]["span_correlation"]["mapped_span_present"] for event in correlated))

    def test_named_enum_compatibility_extension_is_reported(self):
        value = fixture()
        span = value["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        span["kind"] = "SPAN_KIND_CLIENT"
        span["status"]["code"] = "STATUS_CODE_OK"
        bundle = self.run_import(value)
        self.assertTrue(any("compatibility extension" in gap for gap in bundle["manifest"]["completeness"]["gaps"]))

    def test_rejects_invalid_envelope_json_ids_times_and_duplicate_spans(self):
        cases = []
        invalid_id = fixture(); invalid_id["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["traceId"] = "ABC"
        cases.append(invalid_id)
        reversed_time = fixture(); reversed_time["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["endTimeUnixNano"] = "1"
        cases.append(reversed_time)
        duplicate = fixture(); duplicate["resourceSpans"][0]["scopeSpans"][0]["spans"].append(
            json.loads(json.dumps(duplicate["resourceSpans"][0]["scopeSpans"][0]["spans"][0])))
        cases.append(duplicate)
        for index, value in enumerate(cases):
            with self.subTest(index=index):
                self.write(value)
                with self.assertRaises(OTLPImportError):
                    import_otlp(self.source, self.root / f"bundle-{index}")

        for index, raw in enumerate((b'{"events":[]}', b'{"resourceSpans":[],"resourceSpans":[]}',
                                     b'{"resourceSpans":[],"x":NaN}', b'\xff')):
            with self.subTest(raw=index):
                self.write(raw=raw)
                with self.assertRaises(OTLPImportError):
                    import_otlp(self.source, self.root / f"raw-{index}")

    def test_rejects_ambiguous_or_invalid_attributes_and_anyvalues(self):
        duplicate = fixture()
        span = duplicate["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        span["attributes"].append(json.loads(json.dumps(span["attributes"][0])))
        multiple = fixture()
        multiple["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["body"] = {
            "stringValue": "one", "boolValue": True,
        }
        unknown = fixture()
        unknown["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["body"] = {"unknownValue": "x"}
        for index, value in enumerate((duplicate, multiple, unknown)):
            with self.subTest(index=index):
                self.write(value)
                with self.assertRaises(OTLPImportError):
                    import_otlp(self.source, self.root / f"invalid-value-{index}")

    def test_byte_record_and_attribute_limits_reject_before_publication(self):
        for setting, value in (("MAX_FILE_BYTES", 20), ("MAX_RECORDS", 1), ("MAX_ATTRIBUTES", 1)):
            with self.subTest(setting=setting):
                self.write()
                with patch(f"adapters.otlp.importer.{setting}", value):
                    with self.assertRaises(OTLPImportError):
                        import_otlp(self.source, self.destination)
                self.assertFalse(self.destination.exists())

    def test_source_and_destination_symlinks_are_rejected(self):
        self.write()
        alias = self.root / "alias.json"
        alias.symlink_to(self.source)
        with self.assertRaises(OTLPImportError):
            import_otlp(alias, self.destination)
        self.destination.symlink_to(self.root / "missing")
        with self.assertRaises(OTLPImportError):
            import_otlp(self.source, self.destination)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "named pipes unavailable")
    def test_special_source_file_is_rejected_without_blocking(self):
        os.mkfifo(self.source)
        with self.assertRaisesRegex(OTLPImportError, "regular file"):
            import_otlp(self.source, self.destination)

    def test_existing_destination_is_preserved(self):
        self.write()
        self.destination.mkdir()
        sentinel = self.destination / "keep"
        sentinel.write_text("unrelated")
        with self.assertRaises(OTLPImportError):
            import_otlp(self.source, self.destination)
        self.assertEqual(sentinel.read_text(), "unrelated")

    def test_checksum_is_enforced_by_common_bundle_contract(self):
        self.run_import()
        artifact = self.destination / "artifacts/otlp/export.json"
        artifact.write_bytes(b"changed")
        with self.assertRaisesRegex(BundleValidationError, "SHA-256 mismatch"):
            load_bundle(self.destination)


if __name__ == "__main__":
    unittest.main()
