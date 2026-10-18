# OpenTelemetry OTLP/JSON recording adapter

`adapters.otlp.importer.import_otlp(source, destination)` imports one explicitly
selected, bounded OTLP protobuf-JSON export into a new AgentStoryline v0.1
recording bundle. It is an offline reader: it does not open a network listener,
connect to a Collector, execute telemetry content, call a model, or replay tools.

```python
from adapters.otlp import import_otlp

bundle = import_otlp("/outside-git/export.json",
                     "/outside-git/new-otlp-bundle")
```

`looks_like_otlp_export(decoded_json)` provides envelope detection for a shared
dispatcher. It returns true for an object with a list-valued `resourceSpans` or
`resourceLogs` field. It does not establish validity or provenance; the importer
performs strict validation.

## Supported profile

The first profile accepts OTLP's protobuf-JSON envelope for traces
(`resourceSpans`), logs (`resourceLogs`), or a combined offline object containing
both signals. The combined object is a convenience container rather than a claim
that OTLP defines one request carrying both services. Hexadecimal trace/span IDs
are accepted case-insensitively and normalized to lowercase for correlation.
OTLP/JSON's canonical integer enum representation is supported; protobuf enum
names are accepted only as a declared compatibility extension and create a
coverage warning. Nanosecond timestamp source values remain
as decimal strings in normalized event data even though the common RFC3339 field
has microsecond precision.

The OpenTelemetry file exporter's JSONL stream is outside this first profile: it
contains one `TracesData` or `LogsData` object per line rather than one OTLP
service-request JSON document. A JSONL file must not be relabeled as supported
OTLP/JSON input; it needs a separate framing adapter and round-trip tests.

A span is mapped when its name or any attribute uses the `gen_ai.*` namespace:

- allowlisted inference operations (`chat`, `text_completion`, `embeddings`,
  `generate_content`) become `model.request` and `model.response` boundaries;
- tool execution spans become `tool.start` and `tool.end` boundaries;
- create/invoke-agent operations become `run.start` and `run.end` boundaries;
- workflow, retrieval and unknown GenAI operations remain neutral observations
  rather than being mislabeled as model exchanges;
- GenAI log records become request, response, or observation events based on
  their recorded `event.name`; unknown GenAI names remain observations.

All semantic attributes, decoded embedded span events, resource identity,
resource/scope `schemaUrl`, dropped counts, instrumentation scope, status, span
links, source location, trace ID, span ID and exact nanosecond time are retained.
The original source bytes are stored unchanged as
`artifacts/otlp/export.json` with a SHA-256 manifest reference. Non-GenAI spans
and logs remain in that artifact and are counted in coverage rather than being
misrepresented as agent messages.

OTLP parent-span relationships, span links and log `traceId`/`spanId` correlations are kept
inside event data with their recorded structural meaning. They are deliberately
not emitted as AgentStoryline `dependencies`: span ancestry does not prove that
one tool output became a later prompt or input. The adapter never infers missing
content, execution approval, retries, stream boundaries, or cross-trace flow.
The request/start and response/end boundaries generated from one span share an
explicit lifecycle ID used only to form one visible interval. It is not emitted
as `parent_id`, an evidence edge, or a data dependency.

Every imported OTLP bundle is currently marked partial. Sampling, Collector
filtering, export-window selection, dropped records, redaction and disabled
prompt/completion capture cannot be disproved from one offline payload. Explicit
coverage gaps and counts make these limitations visible.

## Safety and validation status

Input is limited to a 32 MiB regular file, 50,000 span/log records, bounded
attribute collections and bounded nested AnyValues. Duplicate JSON keys,
duplicate semantic attributes, non-finite numbers, ambiguous AnyValues,
duplicate span identities, invalid IDs/times, symlinks and existing destination
directories are rejected. Publication uses a new directory and the resulting
bundle passes the common artifact and reference validator.

`tests/test_otlp_adapter.py` uses newly authored canonical protobuf-JSON fixtures
covering inference, tools, logs, hierarchy, missing content, multiple traces,
unknown signals, nanoseconds, dropped counts, schema URLs, malformed values,
limits and filesystem safety.

A real local exporter checkpoint also passed. OpenTelemetry Python SDK and OTLP
HTTP exporters 1.45.1 emitted trace and log binary protobuf to a loopback
receiver. The captured `ExportTraceServiceRequest` and
`ExportLogsServiceRequest` were converted using the official OTLP/JSON
deviations (hex IDs and numeric enums), combined, imported, and rendered. Two
spans and two correlated logs produced six messages/events: two model requests,
two model responses, and tool start/end. Recorded log bodies, span content, model
identity, and tool arguments/results remained visible, with no data dependencies
invented. This validates the SDK -> OTLP/HTTP protobuf -> spec-correct JSON ->
AgentStoryline path for both signals. It does not validate an OpenTelemetry
Collector or the file exporter JSONL format.

Reference specifications:

- https://github.com/open-telemetry/opentelemetry-proto/blob/main/docs/specification.md
- https://opentelemetry.io/docs/specs/semconv/gen-ai/
