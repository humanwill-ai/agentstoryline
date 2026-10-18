# AgentStoryline v0.1.0 release notes

**Source preview; tagged release pending.** v0.1 is a local-first agent-trace
viewer for importing recordings, following recorded activity, inspecting evidence
and optionally adding descriptions. These notes describe the public source scope;
no version tag or GitHub Release is claimed.

The proposed semantic release label/tag is `v0.1.0`; the product scope is v0.1.

## What is included

- Storyline, chat and terminal views; evidence inspection, playhead seeking,
  Smart/Recorded pacing, optional notification audio, and Green, Spotlight
  (default) and Dark themes.
- Local recording import and portable JSON/standalone HTML export. The served
  viewer provides source-family choices before the local file picker.
- Optional evidence-linked descriptions with revision history and separate
  Original/Explained display. The default offline session workflow queues work
  for an authorized worker; it cannot awaken Codex or run a local model.
- Synthetic examples and demo media, Apache-2.0 source licensing and notices,
  and an explicitly allowlisted source-package builder.

Python 3.10+ and the standard library are sufficient for recording review.
Artex `.tar.zst` import additionally needs an installed `zstd` executable.
No source-agent installation or model account is required. Start with the
[synthetic demo](../README.md#try-the-synthetic-viewer) or the
[example inputs](../examples/README.md).

## Compatibility and import matrix

| Input | v0.1 status | Import boundary |
| --- | --- | --- |
| Artex TaskArchiveSnapshot formats 1–3 | Supported | `.tar`, `.tar.zst`, extracted export directory or supported snapshot JSON; historical/partial capture remains explicit. Extracted directories are CLI inputs. |
| Portable `.traceforge.json`, envelope version `1` | Supported | Opens in the local service or standalone HTML; display package, not a source archive or execution package. |
| Codex native rollout JSONL | Experimental | One explicitly selected supported native session; visible message and tool-call/result subset, without reconstructed provider requests or hidden reasoning. |
| Offline OTLP protobuf-JSON | Experimental | One JSON object containing `resourceSpans`, `resourceLogs` or both; bounded pinned profile, conservative GenAI mapping, always marked partial. |
| OrcaReplay native `.orca`/`.orca.zip`, trajectory directory or plain `.tar` | Experimental | Native ZIP export container, with tar as an additional convenience. Read-only manifest/events/referenced blobs; snapshot metadata and diffs retained, without filesystem/Git restoration or execution. Directories are CLI inputs. |
| `codex exec --json`, OTLP file-exporter JSONL, live OTLP collection | Outside v0.1 | Distinct formats/services; similar names do not establish compatibility. |
| LiteLLM/JEV-specific recordings or profiles | Future | No dedicated adapter or source-specific interpretation is claimed. |

Native/telemetry normalization requires the local service. Unknown JSON is
rejected. An explicit `manifest.source.adapter` selects interpretation; only an
absent field permits the legacy known-name fallback. Unknown explicit adapters
use generic interpretation. Badges identify declared provenance, not
authenticated origin. See [release scope](RELEASE_SCOPE_V0_1.md#source-support), the [OTLP profile](OTLP_ADAPTER.md) and
[OrcaReplay profile](ORCAREPLAY_ADAPTER.md).

## Upgrade and saved-data compatibility

`agentstoryline.py` is the preferred command; `traceforge.py` and the existing
`explanation-serve` compatibility command remain available. Common bundle schema
`0.1`, portable envelope version `1` and the `.traceforge.json` extension retain
their identifiers. Renaming the product does not rename original evidence.

Keep the existing local state directory when reopening a recording to retain
saved descriptions, revision history and retained original files. Saved local
annotations for the same source fingerprint take precedence over embedded
annotations. Import does not overwrite source files. No migration to a new
recording extension or bundle version is required for this release scope; this
does not promise compatibility with unrecognized formats or future versions.

## Known limits

- Portable packages are limited to 128 MiB and 5,000 rows per event, message,
  actor, activity or lane collection, with additional structure, text,
  attachment and adapter-specific limits. Maximum sizes do not guarantee
  comfortable performance; larger native imports may not fit portable export.
- Missing capture data stays missing. Codex does not reconstruct requests,
  schemas, retries or streaming boundaries. OTLP ancestry and links do not prove
  tool-to-prompt data dependencies or recording completeness. OrcaReplay snapshot
  metadata/diffs are inspection evidence and do not restore filesystem or Git
  state; unsupported or missing capture data remains explicit.
- Descriptions are interpretations. Validation checks their structure, source
  binding and references; it does not establish factual correctness. Compressed
  storyline intervals and pacing do not change source timestamps or explain
  why an interval has no recorded activity.
- Portable JSON and HTML include source-file inventory and parsed transcript
  history, but not original workspace/transcript bytes. Exact original-file
  access requires the intact matching local import and state directory.
  Standalone HTML cannot generate or persist new descriptions by itself.
- A current second-browser visual and keyboard interaction pass was waived by
  the owner for v0.1.0 on 2026-10-18. Safari/Firefox compatibility has not been
  established by the Chrome inspection or automated browser-contract tests.
  Exact checks and results are in
  [release validation](RELEASE_VALIDATION.md).

## Security, privacy and execution boundaries

Recorded playback reveals existing content and never executes recorded tools,
commands, scripts or requests. Imported scripts and HTML remain inert inspection
data. Procedural execution, general replay, execution portability, live telemetry
collection and hosted collaboration are outside v0.1; experimental replay and
generated-workflow modules retain their authorization and local scope controls.

Import and playback do not call a model provider. Explicit API description
generation sends selected excerpts to the configured endpoint and may incur
charges. Original-file controls validate retained checksums; paths in portable
packages do not grant arbitrary local file access.

Normal JSON/HTML exports retain recorded content and are **not redaction**.
The experimental sharing and Python-script controls are hidden. Development
sharing prepares a separate derivative locally, makes no upload, and requires
review; best-effort redaction does not guarantee removal of sensitive data.
Review all exported material before distributing it.

The source snapshot excludes private state, real recordings, credentials,
research and private integration metadata. Its allowlist review does not audit
Git history or certify imported material's rights. Source agents remain
independent projects; imported recordings retain their own rights. See
[LICENSE](../LICENSE), [third-party notices](../THIRD_PARTY_NOTICES.md) and the
[public source audit](PUBLIC_SOURCE_AUDIT.md).

Release preparation and the [publication checklist](PUBLICATION_CHECKLIST.md)
remain separate from authorization to commit, push, tag or publish.
