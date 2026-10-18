"""Import bounded OTLP protobuf-JSON traces and GenAI logs without executing them."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from core.bundle import BundleValidationError, load_bundle


MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 50_000
MAX_ATTRIBUTES = 4_096
MAX_VALUE_DEPTH = 24
_TRACE_ID = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
_SPAN_ID = re.compile(r"^[0-9a-f]{16}$", re.IGNORECASE)
_ANY_VALUE_FIELDS = frozenset({
    "stringValue", "boolValue", "intValue", "doubleValue", "arrayValue",
    "kvlistValue", "bytesValue",
})
_INFERENCE_OPERATIONS = frozenset({"chat", "text_completion", "embeddings", "generate_content"})


class OTLPImportError(ValueError):
    """Malformed, unsafe, unsupported, or non-OTLP source recording."""


def looks_like_otlp_export(value):
    """Return whether a decoded object has an OTLP trace/log export envelope.

    This only detects the envelope. ``import_otlp`` performs strict validation.
    A combined object containing both resourceSpans and resourceLogs is accepted
    so a caller can preserve correlated offline signals in one artifact.
    """
    return (
        isinstance(value, dict)
        and any(key in value for key in ("resourceSpans", "resourceLogs"))
        and all(
            key not in value or isinstance(value[key], list)
            for key in ("resourceSpans", "resourceLogs")
        )
    )


def _safe_ancestors(path):
    for current in (path, *path.parents):
        if current.is_symlink():
            raise OTLPImportError("symlinks in source/destination paths are unsupported")


def _read(path):
    _safe_ancestors(path)
    directory = None
    descriptor = None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory = os.open(path.anchor, flags | getattr(os, "O_DIRECTORY", 0))
        for part in path.parts[1:-1]:
            child = os.open(part, flags | getattr(os, "O_DIRECTORY", 0), dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(path.name, flags | getattr(os, "O_NONBLOCK", 0), dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                raise OTLPImportError("source must be a bounded regular file")
            raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise OTLPImportError("source exceeds byte limit")
            return raw
    except OSError as exc:
        raise OTLPImportError("cannot read regular source without following symlinks") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def _parse(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise OTLPImportError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    def invalid_constant(value):
        raise OTLPImportError(f"invalid JSON constant: {value}")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise OTLPImportError("numeric value exceeds finite floating-point representation")
        return number

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=invalid_constant, parse_float=finite_float)
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, OTLPImportError):
            raise
        raise OTLPImportError(f"invalid OTLP JSON: {exc}") from exc
    if not looks_like_otlp_export(value):
        raise OTLPImportError("source is not an OTLP trace/log export request JSON envelope")
    return value


def _object(value, where):
    if not isinstance(value, dict):
        raise OTLPImportError(f"{where} must be an object")
    return value


def _list(value, where):
    if not isinstance(value, list):
        raise OTLPImportError(f"{where} must be a list")
    return value


def _identifier(value, pattern, where, *, optional=False):
    if optional and value in (None, ""):
        return None
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise OTLPImportError(f"{where} is not a valid OTLP hexadecimal identifier")
    return value.lower()


def _uint(value, where):
    # Protobuf JSON encodes 64-bit integers as decimal strings. Integer input is
    # accepted for producers that lose that distinction but booleans are not.
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        number = int(value)
    elif type(value) is int and value >= 0:
        number = value
    else:
        raise OTLPImportError(f"{where} must be an unsigned decimal integer")
    if number > 18_446_744_073_709_551_615:
        raise OTLPImportError(f"{where} exceeds uint64")
    return number


def _optional_count(record, key, where):
    if key not in record:
        return 0
    number = _uint(record[key], where + "." + key)
    if number > 4_294_967_295:
        raise OTLPImportError(f"{where}.{key} exceeds uint32")
    return number


def _timestamp(value, where):
    nanos = _uint(value, where)
    try:
        seconds, remainder = divmod(nanos, 1_000_000_000)
        instant = datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=remainder // 1000)
    except (OverflowError, OSError, ValueError) as exc:
        raise OTLPImportError(f"{where} is outside the supported datetime range") from exc
    return instant.isoformat().replace("+00:00", "Z"), nanos


def _any_value(value, where, depth=0):
    if depth > MAX_VALUE_DEPTH:
        raise OTLPImportError(f"{where} exceeds nested AnyValue depth")
    value = _object(value, where)
    present = [key for key in _ANY_VALUE_FIELDS if key in value]
    unknown = set(value) - _ANY_VALUE_FIELDS
    if unknown or len(present) != 1:
        raise OTLPImportError(f"{where} must contain exactly one supported AnyValue field")
    key = present[0]
    item = value[key]
    if key == "stringValue":
        if not isinstance(item, str):
            raise OTLPImportError(f"{where}.{key} must be a string")
        return item
    if key == "boolValue":
        if type(item) is not bool:
            raise OTLPImportError(f"{where}.{key} must be a boolean")
        return item
    if key == "intValue":
        if isinstance(item, str) and re.fullmatch(r"-?(?:0|[1-9][0-9]*)", item):
            return int(item)
        if type(item) is int:
            return item
        raise OTLPImportError(f"{where}.{key} must be a decimal integer")
    if key == "doubleValue":
        if type(item) not in (int, float) or isinstance(item, bool) or not math.isfinite(item):
            raise OTLPImportError(f"{where}.{key} must be finite")
        return item
    if key == "bytesValue":
        if not isinstance(item, str):
            raise OTLPImportError(f"{where}.{key} must be a base64 string")
        return {"otlp_bytes_base64": item}
    if key == "arrayValue":
        values = _list(_object(item, where + ".arrayValue").get("values", []), where + ".arrayValue.values")
        if len(values) > MAX_ATTRIBUTES:
            raise OTLPImportError(f"{where} array exceeds item limit")
        return [_any_value(child, f"{where}.arrayValue.values[{index}]", depth + 1)
                for index, child in enumerate(values)]
    values = _list(_object(item, where + ".kvlistValue").get("values", []), where + ".kvlistValue.values")
    return _attributes(values, where + ".kvlistValue.values", depth + 1)


def _attributes(values, where, depth=0):
    values = _list(values, where)
    if len(values) > MAX_ATTRIBUTES:
        raise OTLPImportError(f"{where} exceeds attribute limit")
    result = {}
    for index, pair in enumerate(values):
        pair = _object(pair, f"{where}[{index}]")
        key = pair.get("key")
        if not isinstance(key, str) or not key:
            raise OTLPImportError(f"{where}[{index}].key must be a nonempty string")
        if key in result:
            raise OTLPImportError(f"{where} contains duplicate attribute {key!r}")
        result[key] = _any_value(pair.get("value"), f"{where}[{index}].value", depth)
    return result


def _resource(resource, where):
    resource = _object(resource or {}, where)
    return (_attributes(resource.get("attributes", []), where + ".attributes"),
            {"dropped_attributes_count": _optional_count(resource, "droppedAttributesCount", where)})


def _scope(scope, where, schema_url=None):
    scope = _object(scope or {}, where)
    attributes = _attributes(scope.get("attributes", []), where + ".attributes")
    return {"name": scope.get("name") if isinstance(scope.get("name"), str) else "",
            "version": scope.get("version") if isinstance(scope.get("version"), str) else "",
            "attributes": attributes,
            "dropped_attributes_count": _optional_count(scope, "droppedAttributesCount", where),
            "schema_url": schema_url if isinstance(schema_url, str) else None}


def _genai(attributes, name=""):
    return any(key.startswith("gen_ai.") for key in attributes) or (
        isinstance(name, str) and name.startswith("gen_ai.")
    )


def _operation(attributes, name):
    value = attributes.get("gen_ai.operation.name")
    return value if isinstance(value, str) and value else name


def _span_family(operation, attributes):
    lowered = operation.lower()
    if lowered == "execute_tool" or isinstance(attributes.get("gen_ai.tool.name"), str):
        return "tool"
    if lowered in {"create_agent", "invoke_agent"}:
        return "agent"
    if lowered in _INFERENCE_OPERATIONS:
        return "model"
    return "observation"


def _partition_attributes(attributes):
    request, response, common = {}, {}, {}
    for key, value in attributes.items():
        if key.startswith(("gen_ai.request.", "gen_ai.input.")):
            request[key] = value
        elif key.startswith(("gen_ai.response.", "gen_ai.output.", "gen_ai.usage.")):
            response[key] = value
        else:
            common[key] = value
    return request, response, common


def _model_identity(attributes, phase):
    provider = attributes.get("gen_ai.provider.name", attributes.get("gen_ai.system"))
    if phase == "response":
        model = attributes.get("gen_ai.response.model", attributes.get("gen_ai.request.model"))
    else:
        model = attributes.get("gen_ai.request.model", attributes.get("gen_ai.response.model"))
    identity = {"basis": "recorded_otlp_semantic_attribute_not_provider_attestation"}
    if isinstance(model, str) and model:
        identity["model"] = model
    if isinstance(provider, str) and provider:
        identity["provider"] = provider
    return identity if len(identity) > 1 else None


def _agent_identity(attributes):
    agent_id, name = attributes.get("gen_ai.agent.id"), attributes.get("gen_ai.agent.name")
    identity = {}
    if isinstance(agent_id, str) and agent_id:
        identity["id"] = agent_id
    if isinstance(name, str) and name:
        identity["name"] = name
    if identity:
        identity["basis"] = "recorded_otlp_gen_ai_agent_attribute"
    return identity or None


def _request_display(attributes):
    request = {}
    model = attributes.get("gen_ai.request.model")
    messages = attributes.get("gen_ai.input.messages", attributes.get("gen_ai.prompt"))
    tools = attributes.get("gen_ai.tool.definitions")
    instructions = attributes.get("gen_ai.system_instructions")
    if isinstance(model, str) and model:
        request["model"] = model
    if messages is not None:
        request["messages"] = messages
    if tools is not None:
        request["tools"] = tools
    if instructions is not None:
        request["system_instructions"] = instructions
    return request


def _response_display(attributes):
    response = {}
    model = attributes.get("gen_ai.response.model")
    content = attributes.get("gen_ai.output.messages", attributes.get("gen_ai.completion"))
    if isinstance(model, str) and model:
        response["model"] = model
    if content is not None:
        response["content"] = content
    return response


def _explicit_agent_fields(attributes):
    identity = _agent_identity(attributes)
    fields = {}
    if identity:
        fields.update(agent_id=identity.get("id") or identity.get("name"),
                      recorded_agent_identity=identity)
    conversation = attributes.get("gen_ai.conversation.id")
    if isinstance(conversation, str) and conversation:
        fields["session_id"] = conversation
        fields["session_identity_basis"] = "recorded_otlp_gen_ai_conversation_id"
    return fields


def _span_status(span, where):
    status = _object(span.get("status", {}), where + ".status")
    code = status.get("code", 0)
    if not isinstance(code, (str, int)) or isinstance(code, bool):
        raise OTLPImportError(f"{where}.status.code must be a protobuf enum string or integer")
    message = status.get("message")
    if message is not None and not isinstance(message, str):
        raise OTLPImportError(f"{where}.status.message must be a string")
    return {"code": code, "message": message}


def _source_location(signal, resource_index, scope_index, record_index):
    return {"artifact": "artifacts/otlp/export.json", "signal": signal,
            "resource_index": resource_index, "scope_index": scope_index,
            "record_index": record_index}


def _collect(value):
    spans, logs, all_timestamps = [], [], []
    record_count = 0
    resources = []
    span_ids = set()

    for ri, resource_spans in enumerate(value.get("resourceSpans", [])):
        resource_spans = _object(resource_spans, f"resourceSpans[{ri}]")
        resource, resource_metadata = _resource(resource_spans.get("resource"), f"resourceSpans[{ri}].resource")
        resource_metadata["schema_url"] = resource_spans.get("schemaUrl") if isinstance(resource_spans.get("schemaUrl"), str) else None
        resources.append(resource)
        scope_spans = _list(resource_spans.get("scopeSpans", []), f"resourceSpans[{ri}].scopeSpans")
        for si, scope_item in enumerate(scope_spans):
            scope_item = _object(scope_item, f"resourceSpans[{ri}].scopeSpans[{si}]")
            scope = _scope(scope_item.get("scope"), f"resourceSpans[{ri}].scopeSpans[{si}].scope",
                           scope_item.get("schemaUrl"))
            records = _list(scope_item.get("spans", []), f"resourceSpans[{ri}].scopeSpans[{si}].spans")
            for xi, span in enumerate(records):
                record_count += 1
                if record_count > MAX_RECORDS:
                    raise OTLPImportError("source exceeds record limit")
                where = f"resourceSpans[{ri}].scopeSpans[{si}].spans[{xi}]"
                span = _object(span, where)
                trace_id = _identifier(span.get("traceId"), _TRACE_ID, where + ".traceId")
                span_id = _identifier(span.get("spanId"), _SPAN_ID, where + ".spanId")
                parent_span_id = _identifier(span.get("parentSpanId"), _SPAN_ID, where + ".parentSpanId", optional=True)
                identity = (trace_id, span_id)
                if identity in span_ids:
                    raise OTLPImportError(f"duplicate span identity {trace_id}/{span_id}")
                span_ids.add(identity)
                start, start_nanos = _timestamp(span.get("startTimeUnixNano"), where + ".startTimeUnixNano")
                end, end_nanos = _timestamp(span.get("endTimeUnixNano"), where + ".endTimeUnixNano")
                if end_nanos < start_nanos:
                    raise OTLPImportError(f"{where} ends before it starts")
                all_timestamps.extend((start_nanos, end_nanos))
                attributes = _attributes(span.get("attributes", []), where + ".attributes")
                name = span.get("name")
                if not isinstance(name, str) or not name:
                    raise OTLPImportError(f"{where}.name must be a nonempty string")
                events = _list(span.get("events", []), where + ".events")
                decoded_events = []
                for ei, event in enumerate(events):
                    event = _object(event, f"{where}.events[{ei}]")
                    event_time, event_nanos = _timestamp(event.get("timeUnixNano"), f"{where}.events[{ei}].timeUnixNano")
                    all_timestamps.append(event_nanos)
                    decoded_events.append({"name": event.get("name"), "timestamp": event_time,
                                           "time_unix_nano": str(event_nanos),
                                           "attributes": _attributes(event.get("attributes", []), f"{where}.events[{ei}].attributes"),
                                           "dropped_attributes_count": _optional_count(event, "droppedAttributesCount", f"{where}.events[{ei}]")})
                links = []
                for li, link in enumerate(_list(span.get("links", []), where + ".links")):
                    link_where = f"{where}.links[{li}]"
                    link = _object(link, link_where)
                    links.append({
                        "trace_id": _identifier(link.get("traceId"), _TRACE_ID, link_where + ".traceId"),
                        "span_id": _identifier(link.get("spanId"), _SPAN_ID, link_where + ".spanId"),
                        "trace_state": link.get("traceState") if isinstance(link.get("traceState"), str) else None,
                        "attributes": _attributes(link.get("attributes", []), link_where + ".attributes"),
                        "dropped_attributes_count": _optional_count(link, "droppedAttributesCount", link_where),
                        "relationship": "recorded_otlp_span_link_not_data_dependency",
                    })
                spans.append({"trace_id": trace_id, "span_id": span_id,
                              "parent_span_id": parent_span_id, "name": name,
                              "start": start, "start_nanos": start_nanos,
                              "end": end, "end_nanos": end_nanos,
                              "attributes": attributes, "events": decoded_events,
                              "links": links,
                              "dropped_counts": {
                                  "attributes": _optional_count(span, "droppedAttributesCount", where),
                                  "events": _optional_count(span, "droppedEventsCount", where),
                                  "links": _optional_count(span, "droppedLinksCount", where),
                              },
                              "status": _span_status(span, where), "resource": resource,
                              "resource_metadata": resource_metadata,
                              "scope": scope, "location": _source_location("traces", ri, si, xi),
                              "flags": span.get("flags"), "trace_state": span.get("traceState"),
                              "kind": span.get("kind")})

    for ri, resource_logs in enumerate(value.get("resourceLogs", [])):
        resource_logs = _object(resource_logs, f"resourceLogs[{ri}]")
        resource, resource_metadata = _resource(resource_logs.get("resource"), f"resourceLogs[{ri}].resource")
        resource_metadata["schema_url"] = resource_logs.get("schemaUrl") if isinstance(resource_logs.get("schemaUrl"), str) else None
        resources.append(resource)
        scope_logs = _list(resource_logs.get("scopeLogs", []), f"resourceLogs[{ri}].scopeLogs")
        for si, scope_item in enumerate(scope_logs):
            scope_item = _object(scope_item, f"resourceLogs[{ri}].scopeLogs[{si}]")
            scope = _scope(scope_item.get("scope"), f"resourceLogs[{ri}].scopeLogs[{si}].scope",
                           scope_item.get("schemaUrl"))
            records = _list(scope_item.get("logRecords", []), f"resourceLogs[{ri}].scopeLogs[{si}].logRecords")
            for li, record in enumerate(records):
                record_count += 1
                if record_count > MAX_RECORDS:
                    raise OTLPImportError("source exceeds record limit")
                where = f"resourceLogs[{ri}].scopeLogs[{si}].logRecords[{li}]"
                record = _object(record, where)
                attributes = _attributes(record.get("attributes", []), where + ".attributes")
                body = _any_value(record["body"], where + ".body") if "body" in record else None
                trace_id = _identifier(record.get("traceId"), _TRACE_ID, where + ".traceId", optional=True)
                span_id = _identifier(record.get("spanId"), _SPAN_ID, where + ".spanId", optional=True)
                stamp_value = record.get("timeUnixNano", record.get("observedTimeUnixNano"))
                timestamp = nanos = None
                if stamp_value is not None:
                    timestamp, nanos = _timestamp(stamp_value, where + ".timeUnixNano")
                    all_timestamps.append(nanos)
                logs.append({"trace_id": trace_id, "span_id": span_id, "timestamp": timestamp,
                             "time_nanos": nanos, "attributes": attributes, "body": body,
                             "resource": resource, "scope": scope,
                             "resource_metadata": resource_metadata,
                             "location": _source_location("logs", ri, si, li),
                             "severity_number": record.get("severityNumber"),
                             "severity_text": record.get("severityText"),
                             "flags": record.get("flags"),
                             "dropped_attributes_count": _optional_count(record, "droppedAttributesCount", where)})
    return spans, logs, all_timestamps, resources, record_count


def _log_name(log):
    name = log["attributes"].get("event.name")
    if isinstance(name, str):
        return name
    body = log["body"]
    if isinstance(body, dict):
        for key in ("event.name", "name"):
            if isinstance(body.get(key), str):
                return body[key]
    return ""


def _log_kind(name):
    lowered = name.lower()
    if any(part in lowered for part in ("prompt", "request", "input")):
        return "model.request"
    if any(part in lowered for part in ("completion", "response", "output", "choice")):
        return "model.response"
    return "observation"


def _event(event_id, run_id, kind, timestamp, nanos, order, data, **fields):
    return {"schema_version": "0.1", "id": event_id, "run_id": run_id,
            "kind": kind, "timestamp": timestamp, "sequence": 0,
            "data": {**data, "otlp_time_unix_nano": str(nanos)},
            "extensions": {"sort_order": order}, **fields}


def _convert(value, raw):
    spans, logs, timestamps, resources, record_count = _collect(value)
    if not timestamps:
        raise OTLPImportError("OTLP export has no usable trace/log timestamp")
    traces = sorted({span["trace_id"] for span in spans} |
                    {log["trace_id"] for log in logs if log["trace_id"]})
    run_seed = traces[0] if len(traces) == 1 else hashlib.sha256(raw).hexdigest()[:24]
    run_id = "otlp-" + run_seed
    mapped_spans = {(span["trace_id"], span["span_id"]): span for span in spans
                    if _genai(span["attributes"], span["name"])}
    all_span_ids = {(span["trace_id"], span["span_id"]) for span in spans}
    events = []
    skipped_logs = Counter()
    gaps = {
        "OTLP export is a supplied telemetry snapshot; collector sampling, filtering, dropped records and export-window completeness cannot be reconstructed.",
        "Prompt, response, tool input and tool output content may be disabled, redacted or absent in the supplied telemetry.",
        "Recorded parentSpanId values are retained as structural hierarchy metadata; they are not converted into AgentStoryline data dependencies.",
        "No provider request retries, stream boundaries or transport bodies are inferred beyond fields present in the supplied export.",
        "OpenTelemetry GenAI semantic conventions evolve independently; unrecognized semantic attributes remain preserved in the source artifact and mapped event data.",
    }
    order = 0
    for span in spans:
        if (span["trace_id"], span["span_id"]) not in mapped_spans:
            continue
        operation = _operation(span["attributes"], span["name"])
        family = _span_family(operation, span["attributes"])
        request, response, common = _partition_attributes(span["attributes"])
        hierarchy = {"trace_id": span["trace_id"], "span_id": span["span_id"],
                     "parent_span_id": span["parent_span_id"],
                     "parent_present_in_export": ((span["trace_id"], span["parent_span_id"]) in all_span_ids
                                                  if span["parent_span_id"] else None),
                     "relationship": "recorded_structural_parent_not_data_dependency"}
        shared = {"source_location": span["location"], "capture_surface": "otlp_span",
                  "operation": operation, "span_name": span["name"], "span_kind": span["kind"],
                  "resource_attributes": span["resource"], "scope": span["scope"],
                  "resource_metadata": span["resource_metadata"],
                  "common_attributes": common, "span_hierarchy": hierarchy,
                  "embedded_span_events": span["events"], "span_links": span["links"],
                  "dropped_counts": span["dropped_counts"], "trace_state": span["trace_state"],
                  "span_lifecycle": {"trace_id": span["trace_id"], "span_id": span["span_id"],
                                     "relationship": "recorded_same_span_boundary_not_data_dependency"},
                  **_explicit_agent_fields(span["attributes"])}
        base = f"otlp.span.{span['trace_id']}.{span['span_id']}"
        if family == "tool":
            call_id = f"otlp:{span['trace_id']}:{span['span_id']}"
            tool = span["attributes"].get("gen_ai.tool.name") or span["name"]
            arguments = ({"arguments": span["attributes"]["gen_ai.tool.call.arguments"]}
                         if "gen_ai.tool.call.arguments" in span["attributes"] else {})
            result = ({"result": span["attributes"]["gen_ai.tool.call.result"]}
                      if "gen_ai.tool.call.result" in span["attributes"] else {})
            events.append(_event(base + ".start", run_id, "tool.start", span["start"], span["start_nanos"], order,
                                 {**shared, "phase": "start", "attributes": request,
                                  **arguments,
                                  "execution_status": "span_started"}, call_id=call_id, tool=str(tool)))
            order += 1
            events.append(_event(base + ".end", run_id, "tool.end", span["end"], span["end_nanos"], order,
                                 {**shared, "phase": "end", "attributes": response,
                                  **result,
                                  "status": span["status"], "execution_status": "span_ended"},
                                 call_id=call_id, tool=str(tool)))
        elif family == "agent":
            call_id = f"otlp:{span['trace_id']}:{span['span_id']}"
            events.append(_event(base + ".start", run_id, "run.start", span["start"], span["start_nanos"], order,
                                 {**shared, "phase": "start", "attributes": request}, call_id=call_id))
            order += 1
            events.append(_event(base + ".end", run_id, "run.end", span["end"], span["end_nanos"], order,
                                 {**shared, "phase": "end", "attributes": response, "status": span["status"]},
                                 call_id=call_id))
        elif family == "model":
            call_id = f"otlp:{span['trace_id']}:{span['span_id']}"
            events.append(_event(base + ".request", run_id, "model.request", span["start"], span["start_nanos"], order,
                                 {**shared, "phase": "request", "attributes": request,
                                  "request": _request_display(span["attributes"]),
                                  **({"model_identity": _model_identity(span["attributes"], "request")}
                                     if _model_identity(span["attributes"], "request") else {})},
                                 call_id=call_id))
            order += 1
            events.append(_event(base + ".response", run_id, "model.response", span["end"], span["end_nanos"], order,
                                 {**shared, "phase": "response", "attributes": response,
                                  "response": _response_display(span["attributes"]),
                                  **({"model_identity": _model_identity(span["attributes"], "response")}
                                     if _model_identity(span["attributes"], "response") else {}),
                                  "status": span["status"]}, call_id=call_id,
                                 ))
        else:
            events.append(_event(base + ".observation", run_id, "observation", span["start"], span["start_nanos"], order,
                                 {**shared, "phase": "observed_span", "attributes": span["attributes"],
                                  "span_end_timestamp": span["end"],
                                  "span_end_time_unix_nano": str(span["end_nanos"]),
                                  "status": span["status"],
                                  "classification": "unsupported_genai_operation_preserved_as_observation"}))
        order += 1

    for index, log in enumerate(logs):
        name = _log_name(log)
        if not _genai(log["attributes"], name):
            skipped_logs["non_genai"] += 1
            continue
        if log["timestamp"] is None:
            skipped_logs["missing_timestamp"] += 1
            gaps.add("One or more GenAI log records lack timeUnixNano/observedTimeUnixNano and were not mapped to timeline events.")
            continue
        identity = (log["trace_id"], log["span_id"])
        matched = identity in mapped_spans
        if log["span_id"] and not matched:
            gaps.add("One or more GenAI log records reference a span absent from the mapped GenAI trace set; they remain unparented observations.")
        kind = _log_kind(name)
        matched_span = mapped_spans.get(identity)
        identity_attributes = ({**matched_span["attributes"], **log["attributes"]}
                               if matched_span else log["attributes"])
        log_model_identity = _model_identity(identity_attributes, "response" if kind == "model.response" else "request")
        if log_model_identity and matched_span and not _model_identity(log["attributes"], "response" if kind == "model.response" else "request"):
            log_model_identity["basis"] = "recorded_correlated_otlp_span_semantic_attribute_not_provider_attestation"
        display = ({"request": log["body"] if isinstance(log["body"], dict) else {"content": log["body"]}}
                   if kind == "model.request" else
                   {"response": log["body"]} if kind == "model.response" else {})
        events.append(_event(f"otlp.log.{index}", run_id, kind, log["timestamp"], log["time_nanos"], order,
                             {"source_location": log["location"], "capture_surface": "otlp_log_record",
                              "event_name": name or "unknown", "body": log["body"],
                              "attributes": log["attributes"], "resource_attributes": log["resource"],
                              "resource_metadata": log["resource_metadata"],
                              "scope": log["scope"], "severity_number": log["severity_number"],
                              "severity_text": log["severity_text"],
                              "dropped_attributes_count": log["dropped_attributes_count"],
                              **display, **_explicit_agent_fields(identity_attributes),
                              **({"model_identity": log_model_identity} if log_model_identity else {}),
                              "span_correlation": {"trace_id": log["trace_id"], "span_id": log["span_id"],
                                                   "mapped_span_present": matched,
                                                   "relationship": "recorded_identifier_correlation_not_data_dependency"}}))
        order += 1

    if not mapped_spans:
        gaps.add("No spans carrying recognized gen_ai.* semantic attributes or names were mapped.")
    if not events:
        gaps.add("No supported GenAI span or timestamped GenAI log events were supplied.")
    if len(traces) != 1:
        gaps.add(f"Supplied export contains {len(traces)} trace IDs; the bundle groups them as one import artifact without inventing cross-trace relationships.")
    unmapped_spans = len(spans) - len(mapped_spans)
    if unmapped_spans:
        gaps.add(f"{unmapped_spans} non-GenAI spans remain only in the preserved source artifact.")
    if any(isinstance(span["kind"], str) or isinstance(span["status"]["code"], str) for span in spans):
        gaps.add("One or more enum fields use protobuf enum names rather than OTLP/JSON's canonical integer encoding; they were accepted as a compatibility extension.")
    if (any(any(value for value in span["dropped_counts"].values()) for span in spans)
            or any(log["dropped_attributes_count"] for log in logs)
            or any(span["resource_metadata"]["dropped_attributes_count"] or span["scope"]["dropped_attributes_count"] for span in spans)
            or any(log["resource_metadata"]["dropped_attributes_count"] or log["scope"]["dropped_attributes_count"] for log in logs)
            or any(event["dropped_attributes_count"] for span in spans for event in span["events"])
            or any(link["dropped_attributes_count"] for span in spans for link in span["links"])):
        gaps.add("The supplied OTLP records report dropped attributes, events or links; their missing content cannot be reconstructed.")

    events.sort(key=lambda event: (_uint(event["data"]["otlp_time_unix_nano"], "event timestamp"),
                                   event["extensions"]["sort_order"], event["id"]))
    for sequence, event in enumerate(events):
        event["sequence"] = sequence
        del event["extensions"]

    services = sorted({str(resource.get("service.name")) for resource in resources
                       if isinstance(resource.get("service.name"), str) and resource.get("service.name")})
    sdk_versions = sorted({str(resource.get("telemetry.sdk.version")) for resource in resources
                           if isinstance(resource.get("telemetry.sdk.version"), str) and resource.get("telemetry.sdk.version")})
    started_at, _ = _timestamp(min(timestamps), "earliest timestamp")
    manifest = {
        "schema_version": "0.1", "run_id": run_id,
        "agent": {"name": "OpenTelemetry", "version": sdk_versions[0] if len(sdk_versions) == 1 else "unknown",
                  "extensions": {"services": services, "telemetry_sdk_versions": sdk_versions}},
        "started_at": started_at,
        "completeness": {"status": "partial", "gaps": sorted(gaps),
                         "extensions": {"mapped_genai_spans": len(mapped_spans),
                                        "unmapped_spans": unmapped_spans,
                                        "mapped_events": len(events),
                                        "skipped_log_records": dict(sorted(skipped_logs.items()))}},
        "source": {"adapter": "otlp", "format": "otlp-protobuf-json",
                   "format_version": "OTLP JSON encoding; GenAI semantic-convention version not declared",
                   "capture_surface": "offline-export-request-json",
                   "signals": sorted(key for key in ("resourceSpans", "resourceLogs") if key in value),
                   "record_count": record_count, "trace_ids": traces,
                   "services": services, "sequence_basis": "timestamp_then_source_order",
                   "relationship_policy": "span hierarchy and identifier correlation are not data dependencies"},
        "artifact_refs": [{"path": "artifacts/otlp/export.json", "sha256": hashlib.sha256(raw).hexdigest()}],
    }
    return {"manifest": manifest, "events": events}


def _publish_new(staging, destination):
    owned = []
    try:
        destination.mkdir(mode=0o700, exist_ok=False)
        owned.append((destination, destination.lstat()))
        for source in sorted(staging.rglob("*")):
            target = destination / source.relative_to(staging)
            if source.is_dir():
                target.mkdir(mode=0o700, exist_ok=False)
                owned.append((target, target.lstat()))
            else:
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    owned.append((target, os.fstat(output.fileno())))
                    with source.open("rb") as stream:
                        shutil.copyfileobj(stream, output)
    except BaseException:
        for path, original in reversed(owned):
            try:
                current = path.lstat()
                if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
                    continue
                path.rmdir() if stat.S_ISDIR(current.st_mode) else path.unlink()
            except OSError:
                pass
        raise


def import_otlp(source, destination):
    """Import one supplied OTLP protobuf-JSON export into a new recording bundle.

    Supported input is an ExportTraceServiceRequest-like ``resourceSpans``
    envelope, an ExportLogsServiceRequest-like ``resourceLogs`` envelope, or a
    combined offline object containing both. The function does not connect to a
    collector, execute telemetry content, contact a provider, or replay tools.
    """
    source, destination = Path(source).absolute(), Path(destination).absolute()
    _safe_ancestors(destination)
    if destination.exists():
        raise OTLPImportError("destination already exists; choose a new bundle directory")
    raw = _read(source)
    bundle = _convert(_parse(raw), raw)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _safe_ancestors(destination)
    with tempfile.TemporaryDirectory(prefix=".otlp-import-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "bundle"
        artifact = staging / "artifacts/otlp/export.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(raw)
        (staging / "manifest.json").write_text(
            json.dumps(bundle["manifest"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (staging / "events.jsonl").write_text(
            "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in bundle["events"]), encoding="utf-8")
        try:
            verified = load_bundle(staging)
        except BundleValidationError as exc:
            raise OTLPImportError(f"converted source violates bundle contract: {exc}") from exc
        try:
            _publish_new(staging, destination)
        except OSError as exc:
            raise OTLPImportError("cannot publish new bundle; destination may exist or publication failed") from exc
    return verified
