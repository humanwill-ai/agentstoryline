# Recording contract v0.1

`bundle.schema.json` describes the combined in-memory `{manifest, events}`
shape. On disk, `manifest.json` contains the manifest and `events.jsonl` has one
event object per line. `core.bundle.load_bundle(path)` enforces semantic checks
that JSON Schema alone cannot: unique IDs, matching run IDs, strictly increasing
capture sequences, preceding parent/evidence/dependency references, and artifact
hashes and path safety. It returns those full structured records unchanged and
raises `BundleValidationError` on invalid input. No network or execution occurs.

Timestamps require RFC3339 with a timezone. They are preserved even when source
clocks disagree or go backward; sequence determines recording order. Sequence
gaps are permitted. References may only point to earlier recorded events, so
dependencies cannot cycle. `call_id` is correlation text, not an event reference.
Additional envelope or agent fields belong in `extensions`; source metadata and
event `data` preserve arbitrary JSON evidence without interpreting its contents.

Artifacts are declared only in `manifest.artifact_refs` or an event's
`artifact_refs`, each containing `path: "artifacts/..."` and a lowercase `sha256`.
Paths must be relative POSIX paths, without traversal, backslashes or symlink
components. Referenced files must exist and match the checksum. A `path` or
`sha256` inside tool output, model messages, metadata, or extensions is raw
evidence, not an artifact declaration. Unreferenced artifacts are not verified.

Partial recordings require one or more nonblank gaps. Complete recordings must
declare no gaps and contain at least one event. Complete means the producer
asserts the exported recording has no known gaps; the validator cannot prove
that an external agent exported every action. It does not require synthetic run
start/end events, tool pairings, or model responses absent from source records.
In-memory `validate_bundle(bundle, artifact_root=...)` offers the same checks;
an artifact root is required when references are present.

`analysis.narrative.build_observed_timeline(bundle)` creates one observed stage
per recorded event. It reports that a request/result was recorded, without
asserting execution, success, vulnerability, or inferred causality. An inferred
dependency remains visibly labeled as a source-declared inference.

Optional model analysis is offline and requires an explicitly chosen model.
`build_analysis_request(bundle, model=...)` exports an OpenAI-compatible chat
request with a strict response schema and all recording data treated as untrusted
evidence. The caller decides whether to submit it; AgentStoryline does not connect
to providers, read keys, or make paid calls. Provider support for structured
outputs varies. An external response must follow `analysis-response.schema.json`.
`import_analysis_response(bundle, response, model=..., provider=...)` accepts
the JSON content object/string, validates every stage's event references, marks
all model stages `interpretation`, and retains separately generated observations.
Model/provider identity is caller-declared provenance. No unlinked global model
summary is accepted. Empty interpretation lists are valid and make no claims.
Request export requires at least one evidence event; an empty partial recording
can still produce an observed timeline or import an empty interpretation.

`validate_narrative(bundle, narrative)` validates a stored observed or imported
model narrative, including exact observation and provenance consistency. Linked
IDs establish traceability, not factual truth or semantic support of a model's
claim. Model content grants no execution authority. Artifact contents are not
read into model requests. Analysis expects a bundle previously loaded through
`load_bundle`; it rechecks record structure but does not repeat filesystem hashes.

Standard-library validators are authoritative. The schemas are portable shape
descriptions and do not replace reference, datetime, sequence, or filesystem
validation. Recordings are local evidence snapshots, not cryptographically
authenticated attestations; do not modify them concurrently while validating.
