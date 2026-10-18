# Offline generated explanation and replay

`analysis.workflow` combines two existing contracts into one bounded offline
workflow. It validates the source bundle, exports normalized chat messages for
an explanation and replay proposal, then accepts independently generated JSON,
validates evidence links and scope, renders an explained timeline, and exports
a standalone replay package. No workflow function calls a provider or executes
replay. Execution remains a separate explicit `--execute` operation.

```python
from analysis.workflow import export_generation_requests, import_generation_responses

export_generation_requests(
    "/canonical/external/recording", "/canonical/external/requests",
    model="gpt-6.1-sol", allowed_origins=["http://127.0.0.1:8765"],
)
# A selected model/development worker consumes the actual exported requests.
# Save its JSON object responses separately, outside Git.
import_generation_responses(
    "/canonical/external/recording", "/canonical/external/requests",
    "/canonical/external/analysis-response.json",
    "/canonical/external/replay-response.json", "/canonical/external/generated",
    provider="development-worker", generation_method="offline-development-worker",
)
```

Equivalent CLI commands are:

```sh
python3 agentstoryline.py workflow-request /canonical/external/recording /canonical/external/requests --model gpt-6.1-sol --allowed-origin http://127.0.0.1:8765
python3 agentstoryline.py workflow-import /canonical/external/recording /canonical/external/requests /canonical/external/analysis-response.json /canonical/external/replay-response.json /canonical/external/generated --provider development-worker --generation-method offline-development-worker
python3 -m replay /canonical/external/generated/plan.json /canonical/external/replay-result --execute
```

The last command separately opts in to execution. The operator must prepare the
configured local target and verify the proposed mapping before executing it.

Request export creates `analysis-request.json`, `replay-request.json`, and
`request-context.json` in a new directory. Both requests contain the same full
validated recording. The replay request lists only operator-supplied loopback
origins and supported HTTP semantics. System messages explicitly treat source
content as untrusted evidence. SHA-256 fingerprints bind the context to the
recording, request content, requested model, and approved origins. These hashes
provide content consistency, not signed model identity or remote attestation.

Import rechecks current recording and request content before accepting either
response. Explanation stages must have existing evidence IDs and interpretation
basis; the workflow requires a nonempty explained timeline. Replay steps must
have valid citations, inferred provenance, matching run ID and exact approved
origins. Recorded dependencies require explicit matching source metadata. The
existing replay validator rejects unsupported tools, forward references,
malformed templates, scope expansion, and unrecognized fields. Any failed
schema/evidence check rejects both responses before output creation. Refusal
objects are deliberately rejected without execution.

The output contains unchanged response objects, validated `analysis.json`,
`plan.json`, `timeline.html`, and `generation.json`, plus `replay-package/` with
the plan, standard-library runner, and generation provenance. The narrative's
existing provenance remains compatible with normal timeline validation.
`generation.json` additionally identifies either `offline-development-worker`
or `external-provider-response`, always with caller-declared model/provider
identity. A development worker's answer is model-generated interpretation but
is not evidence that an independently configured production provider transport
was used. Exporting a request alone is not generation. The workflow neither
copies authored fixture responses nor supplies fallback proposals.

Paths for source, request/response files, and outputs must be canonical,
non-symlink paths outside the AgentStoryline repository. Input JSON must be regular
files; request files are limited to 64 MiB and response objects to 2 MiB.
Directories must be new with existing parents, and files use exclusive creation.
Existing output and source bytes are preserved. A filesystem failure during
artifact writes can leave a partial newly created output directory; it never
reuses an existing output. This is a local developer tool, not protection
against another process maliciously racing all filesystem operations.

## Content assertions and fresh state

Replay `expect` now supports response JSON equality comparisons:

```json
{"status": 200, "json": [
  {"path": ["ok"], "equals": true},
  {"path": ["marker"], "equals": "traceforge-protected-v1"}
]}
```

A response must pass both its HTTP status and every comparison. Paths select
object keys or nonnegative array indexes, with an empty path selecting the whole
JSON value. Comparisons require exact JSON/Python types recursively; booleans
do not equal integers, including inside containers. Expected values are literal
JSON, not variable references. A failed/missing path, malformed response JSON,
or mismatch retains response evidence, fails the step, and stops subsequent
requests. There are 1–64 comparisons per step. JSON-only expectations still
require a 2xx status; redirects remain refused.

Session values should be extracted from the new `/session` response and
referenced by later operations, never embedded from the historical recording.
Structural validation does not itself prove freshness or target authorization.
For the runtime checkpoint, the target observer must compare the session token
issued for each replay to that replay's actual downstream Authorization header,
verify tokens differ across capture/replays, and verify the protected response's
expected content. The generic runner preserves enough request/response artifacts
for this separate outcome check but does not claim target-specific freshness
from HTTP status alone.

## Validation

New tests in `tests/test_workflow.py` cover rejected malformed/unlinked outputs,
changed recording/request content, nonempty interpretations, explicit offline
provenance, no target connections, output/source preservation, FIFO/symlink
refusal, and size bounds. `tests/test_replay_outcomes.py` covers protected content,
false HTTP-200 success, nested type mismatch, malformed comparisons, duplicate
JSON keys, and stopping with retained evidence. All use newly authored synthetic
local evidence; these tests are not model-generation or actual Artex-run claims.
