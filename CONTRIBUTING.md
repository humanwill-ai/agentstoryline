# Contributing to AgentStoryline

AgentStoryline is a source-neutral, local-first agent-trace viewer. Contributions
should help people import recordings, inspect evidence, follow recorded activity
and understand its limits. Read the [v0.1 release scope](docs/RELEASE_SCOPE_V0_1.md)
before proposing a change. Artex import is supported; Codex native rollout and
offline OTLP protobuf-JSON import are experimental. Procedural execution, live
telemetry collection and hosted collaboration are outside v0.1.

For a suspected vulnerability, follow [SECURITY.md](SECURITY.md). Do not disclose
sensitive security reports in public issues or pull requests.

## Propose and develop a change

Use an issue or pull request to describe the problem, expected behavior and a
small reproducible example. State the input format and relevant version when
reporting an import problem. Use a synthetic reproduction rather than attaching
a real session. Keep changes focused, preserve unrelated work and explain any
compatibility impact. Repository publication and release operations are separate
maintainer decisions.

Python 3.10+ and the standard library are sufficient for development and tests.
Native Artex `.tar.zst` import additionally needs an installed `zstd` executable.
For a local viewer example, use the [synthetic demo instructions](examples/README.md).
No model account or source-agent installation is needed for that workflow.

## Fixtures and privacy

Author fixtures from scratch with fixed, clearly synthetic values. Do not copy
real recordings, private conversations, model outputs, research, credentials,
account identifiers or machine paths into tests, screenshots, logs or examples.
Credential-like test canaries must be inert and clearly identified as synthetic.
Keep runtime recordings and local state outside Git. Explain fixture provenance,
the behavior it exercises and known coverage gaps; distinguish authored
interpretations from recorded evidence or provider output.

Use the [example generator](examples/generate_demos.py) as a reference for
deterministic source-shaped inputs. Public media must use synthetic data and
assets whose redistribution rights have been reviewed. Ordinary recording
exports retain source content and are not redaction.

## Source adapters and evidence

Follow the [recording contract](contracts/README.md) and
[bundle schema](contracts/bundle.schema.json). Keep source-specific parsing in
`adapters/`, shared validation in `core/`, interpretations in `analysis/` and
presentation in `web/`. Recording adapters must not execute recorded content,
discover unrelated sessions or contact a source service or model provider.

An adapter contribution should document and test its exact supported profile,
version/framing limits, rejected inputs and omissions. Preserve original source
bytes and provenance where the adapter contract requires them; validate artifact
hashes and safe paths. Use bounded parsing, reject unsafe files and refuse to
overwrite existing destinations. Validate normalized bundles through the shared
validator, then check portable export and reopening. Similar event names do not
establish equivalent capture coverage or execution portability.

Keep observed evidence, source-declared relationships and inferred descriptions
distinguishable. Do not turn span ancestry, timestamps or a tool request into
proof of data dependency, authorization, execution or success. Retain explicit
partial-coverage gaps. Source labels are declared provenance, not authenticated
origin; unknown adapters must not acquire source-specific interpretation. See
the [source support matrix](docs/RELEASE_SCOPE_V0_1.md#source-support) and
[experimental OTLP profile](docs/OTLP_ADAPTER.md).

Preserve schema identifiers, serialized values, evidence IDs and compatibility
entry points unless a reviewed migration explicitly changes them. Playback must
remain inert, and existing scope, approval and authorization controls must stay
intact. Optional descriptions must remain a separate, explicit workflow.

## Validate and describe the result

Run relevant existing tests while developing, then the complete suite before
submitting:

```sh
python3 -m unittest discover -s tests -v
```

Add focused regression tests when needed to demonstrate a behavior change.
Adapter changes should cover malformed/unsupported input, limits, safe artifact
handling, deterministic normalization, coverage gaps and round trips. Viewer
changes need a browser check with synthetic data, including affected controls
and keyboard interactions. Tests and demos must stay local and must not make
paid model calls or operate on internet targets.

In the pull request, explain what changes for the user, list commands and actual
results, and disclose skipped checks or known limitations. Separate automated
failures, qualitative observations and unconfirmed causes. Do not claim broader
source compatibility or security validation than the checks establish. Consult
[release validation](docs/RELEASE_VALIDATION.md) for the current validation scope.

## License and attribution

By submitting an intentional contribution for inclusion, you agree to license it
under the project's [Apache License, Version 2.0](LICENSE), subject to that
license's contribution terms. Submit only material you have the right to
contribute. Preserve copyright, attribution and license notices; identify any
third-party material and its applicable terms. Imported recordings and external
assets do not acquire the project's license. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
