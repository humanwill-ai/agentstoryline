# Synthetic AgentStoryline examples

These newly authored recordings are redistributable under the repository's Apache-2.0
license. Every message, identifier, timestamp, model label, command and result is
synthetic. No real sessions, private research, model outputs, credentials or
runtime metadata were copied. Importing them is offline and does not execute
their stored tool text. They demonstrate recording portability, not successful
agent execution, model quality or a security evaluation.

| Example | Support tier | Expected import |
| --- | --- | --- |
| `artex-local-inspection/` or `artex-local-inspection.tar` | Supported Artex archive format 3 with streamed LLM rows | Nine events: one model exchange, one explicitly correlated tool call/result, task/node/finding/snapshot observations and one transcript history observation; original workspace and transcript bytes retained |
| `experimental-codex-rollout.jsonl` | Experimental native Codex rollout subset | Six events: two visible assistant messages, two tool starts and their two matching results; no invented model requests |
| `experimental-orcareplay.orca.zip`, `experimental-orcareplay/` or `experimental-orcareplay.tar` | Experimental read-only OrcaReplay trajectory | Eight events: run start/end, model request/response, tool call/reported result, snapshot metadata and file diff; two referenced blobs, no filesystem/Git restoration |
| `experimental-otlp.json` | Experimental OTLP protobuf-JSON offline profile | Five events: model request/response, tool start/end and one completion log; structural span/log correlations remain data, with no data dependencies |

All four source families report partial completeness. The Artex tool is
linked using explicit capture identities. Codex tool results link only to their
explicit native call IDs. OTLP span ancestry is not an input/output dependency.
The OTLP example combines trace and log service envelopes for offline import;
it is not one wire service request or Collector file-exporter JSONL. The Codex
example is not `codex exec --json`, and its synthetic version label is not a
claim about a tested real CLI version. Source-specific evidence limits are in
[the adapter documentation](../docs/RELEASE_VALIDATION.md).

From the repository root, open any example with the local viewer:

```sh
python3 agentstoryline.py viewer examples/artex-local-inspection.tar
python3 agentstoryline.py viewer examples/experimental-codex-rollout.jsonl
python3 agentstoryline.py viewer examples/experimental-otlp.json
python3 agentstoryline.py viewer examples/experimental-orcareplay.orca.zip
```

Use a new destination outside the repository when importing bundles:

```sh
python3 agentstoryline.py import-artex examples/artex-local-inspection.tar /outside-git/new-artex-bundle
python3 agentstoryline.py import-codex examples/experimental-codex-rollout.jsonl /outside-git/new-codex-bundle
python3 agentstoryline.py import-otlp examples/experimental-otlp.json /outside-git/new-otlp-bundle
python3 agentstoryline.py import-orcareplay examples/experimental-orcareplay.orca.zip /outside-git/new-orcareplay-bundle
```

Regenerate the checked-in inputs deterministically with the standard library:

```sh
python3 examples/generate_demos.py
python3 -m unittest tests.test_release_examples tests.test_orcareplay_examples -v
```

The generator reads no files, environment variables, sessions or runtime clock.
It overwrites only its thirteen documented output files in the chosen destination.
Its ZIP and plain tar archives use fixed metadata and need no compression dependency.
The OrcaReplay ZIP uses stored members so regeneration does not depend on a
particular compressor version. Native `.orca` and `.orca.zip` are ZIP containers;
plain tar is an additional import convenience, not the upstream native export. The tests
verify checked-in bytes, artifact checksums, dependency semantics and portable
recording export/reload for every format.

The [OrcaReplay profile](../docs/ORCAREPLAY_ADAPTER.md) preserves the native
manifest/event/blob bytes. Its recorded snapshot metadata and diff are inert
inspection evidence, without restorable filesystem or Git content. This sample
is not copied from upstream quickstart data or an actual agent execution.
