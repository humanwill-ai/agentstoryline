# Bounded procedural replay

Replay is a separate execution adapter. Importing evidence, opening the timeline,
building a narrative, exporting a proposal request, and exporting a runnable plan
do not execute recorded tools. The supported initial operation is `http.request`
against an explicitly configured synthetic HTTP service on literal loopback IPs.
Recorded Bash commands remain evidence; this runner never executes them.

## APIs and opt-in

```python
from replay.runner import (
    run_plan, validate_plan, validate_proposal,
    export_plan, export_proposal_request,
)

checked = validate_plan(plan_dict)  # detached copy, no network/filesystem writes
result = run_plan("plan.json", "new-run-dir", allow_execute=True)
exported = export_plan("plan.json", "new-export-dir")  # no execution
request = export_proposal_request(
    validated_bundle, "proposal-request.json",
    allowed_origins=["http://127.0.0.1:8765"],
)
checked_proposal = validate_proposal(
    validated_bundle, proposed_plan_dict,
    allowed_origins=["http://127.0.0.1:8765"],
)
```

`run_plan` requires the literal Boolean `True` for `allow_execute`. Omitted opt-in
raises `ExecutionNotAllowed`. Invalid plans raise `ReplayValidationError` before
any request or output creation. A runtime failure returns `status: "failed"`,
identifies `failed_step`, preserves completed/failed step evidence, and lists
`unexecuted_steps`. Successful runs return `status: "passed"`. Filesystem errors
such as an existing destination raise; the caller chooses a fresh directory.

Standalone commands:

```sh
python3 -m replay plan.json new-run-dir --execute
python3 -m replay plan.json new-export-dir --export
```

Exports contain `plan.json`, a self-contained Python `replay` package, and a README.
They use only the standard library and require Python 3.10+. From the export
directory, run `python3 -m replay plan.json new-result --execute`. The synthetic
service is configured separately. A valid export does not prove successful replay.
CLI exit statuses are 0 for successful run/export, 1 for runtime failure, and 2
for refusal or invalid input.

## Plan v0.1

The fixture at `tests/fixtures/replay/session_plan.json` is **manually authored
synthetic mapping**, not automatically inferred or model-generated replay. Its
port 8765 is a placeholder replaced with an ephemeral loopback port by tests.
Its source evidence IDs match the Artex synthetic fixture's session and protected
tool results (`artex.activity.12`, `artex.activity.14`). The variable dependency
is explicitly marked inferred, not claimed as a recorded dependency.

```json
{
  "schema_version": "0.1",
  "run_id": "synthetic-session-replay",
  "allowed_origins": ["http://127.0.0.1:8765"],
  "steps": [
    {
      "id": "session",
      "operation": "http.request",
      "method": "GET",
      "url": "http://127.0.0.1:8765/session",
      "expect": {"status": 200},
      "extract": {"token": {"source": "json", "path": ["token"]}},
      "evidence_refs": ["artex.activity.12"],
      "provenance": "authored"
    },
    {
      "id": "protected",
      "operation": "http.request",
      "method": "GET",
      "url": "http://127.0.0.1:8765/protected",
      "headers": {
        "Authorization": {
          "template": "Bearer {token}",
          "bindings": {"token": {"$ref": "session.token"}}
        }
      },
      "expect": {"status": 200},
      "evidence_refs": ["artex.activity.14"],
      "dependencies": [{"event_id": "artex.activity.12", "provenance": "inferred"}],
      "provenance": "authored"
    }
  ]
}
```

Supported methods are `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, and `OPTIONS`.
Unknown fields, tool operations, methods, schema versions, and extraction sources
are rejected. Step IDs are unique ASCII identifiers with optional hyphens;
variable and template names are ASCII identifiers without hyphens. Empty JSON
paths select the entire response. String path components select object keys;
nonnegative integer components select array entries. Type mismatches or missing
components fail the step. JSON parsing rejects invalid UTF-8, duplicate keys,
nonfinite numbers, and malformed JSON when extraction is requested.

Dynamic values are explicit `{"$ref":"earlier_step.variable"}` objects. References
must point to variables extracted by earlier steps. They can occur in URLs,
header values, or nested JSON bodies. Templates contain only simple named
placeholders with exactly matching reference bindings; attribute access, array
access, conversions, format specifications, code evaluation, and forward references
are rejected. Templates insert values literally; percent encoding of URL path and
query values is the plan author's responsibility. Resolved URL scope and header
values are checked immediately before each request. Missing extraction fails before
later references can be used, and variable state is fresh for every invocation.

An optional string body is sent as UTF-8; any other JSON body is serialized as JSON
and receives `Content-Type: application/json` unless explicitly supplied. Header
values must resolve to strings. Controls, duplicate names with different case,
and runner-controlled or proxy headers are rejected. The runner sets connection
close and lets the HTTP library construct host/content length.

`expect: {"status": 200}` requires an exact status. Optional `expect.json` holds
1–64 literal response comparisons, for example `{"path": ["ok"], "equals": true}`.
Missing paths, invalid JSON and type-sensitive mismatches fail the step before
subsequent operations. Without an expected status, only 2xx statuses pass. Redirect statuses always fail, even if explicitly expected.
`preconditions` are explicit equality checks on earlier extracted values:

```json
{"preconditions": [{"ref": {"$ref": "session.ready"}, "equals": true}]}
```

Equality requires matching JSON/Python value types as well as matching values.
Failed preconditions stop before sending that step's request. There are no implicit
cookie jars, retries, variables sourced from historical tokens, browser state,
shell steps, arbitrary scripts, or hidden model calls.

## Scope and limits

Allowed origins must be canonical `http://LITERAL_LOOPBACK_IP:PORT` strings with
explicit ports and no credentials, path, query, or fragment. IPv4 loopback and
IPv6 `::1` literals are supported. Hostnames (including `localhost`), encoded or
noncanonical host spellings, IPv4-mapped IPv6, remote addresses, HTTPS, and
out-of-scope ports are refused. Connections use numeric socket addresses directly:
no DNS resolution, environment proxy use, or redirect following. Every dynamic
URL is validated after substitution.

Limits are 64 steps, 16 allowed origins, a 2 MiB plan, 1 MiB request and response
bodies, 64 headers/extracted variables/preconditions, and five seconds total network
time per operation. The total socket deadline also applies to a slowly streaming
response; it is not reset by each incoming byte. Network, status, parsing, scope,
precondition, and extraction errors stop execution without retrying.

Output/export destinations must be new directories with existing parents. Symlink
destinations and symlink ancestors are refused. On systems where `/tmp` or `/var`
are symlinks, pass the resolved canonical path. Each file is created exclusively;
existing outputs are never reused or overwritten. This is a local developer
tool, not a sandbox against another process maliciously racing directory changes.

## Evidence and local artifacts

Each execution directory contains the validated `plan.json`, `execution.jsonl`,
`result.json`, and request/response artifacts. Logs preserve source `evidence_refs`,
event dependency provenance, and step provenance. Artifact references include a
relative path, byte count, and SHA-256. Source evidence is never rewritten to look
like replay output. Requests and responses can include fresh session values; keep
outputs local and outside Git. Refused or unsent steps have no request artifacts.

A standalone hand-authored plan can use `provenance: "authored"`. General plan
validation checks provenance syntax but cannot confirm event links without a bundle.
For an LLM proposal, `validate_proposal` additionally requires matching bundle
`run_id`, exact operator-approved origins, nonempty real event citations on every
step, and `provenance: "inferred"`. Its dependencies must cite real events, and a
dependency can claim `recorded` only when a cited source event explicitly contains
that same recorded dependency. Inferred dependencies remain interpretations.
These checks do not establish that a proposed HTTP mapping faithfully reproduces
the historical tool or that success proves a security finding.

`export_proposal_request` accepts a bundle already validated by
`core.bundle.load_bundle`, writes an exclusive JSON file with scope, evidence,
instructions, and the plan contract, and makes no model request. Its instructions
explicitly treat evidence text as untrusted data. An actual offline GPT-6.1 Sol generated proposal is validated in the [paired
generation workflow](GENERATED_WORKFLOW.md). It is distinct from this manually
authored fixture and from an automatic hosted-provider client. Importing a model
proposal uses structural runner validation plus bundle/scope checks before
explicit execution.

## Validation

`python3 -m unittest tests.test_replay -v` runs newly authored local synthetic
tests. They demonstrate that two runs receive different session tokens and use
each fresh token downstream, plus explicit opt-in, scope/hostname/redirect/proxy
refusal, no DNS lookup, dynamic scope/header revalidation, invalid and forward
references, unsupported operations/templates, failure stopping, time/byte bounds,
no clobber, standalone export execution, and proposal evidence/provenance checks.
No internet target, security scan, model call, or paid service is used.
