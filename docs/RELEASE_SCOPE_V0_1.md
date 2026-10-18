# AgentStoryline v0.1 release scope

Scope frozen for the v0.1.0 source preview on 2026-10-18. Source publication is
authorized separately from a version tag, GitHub Release, deployment or release
workflow. No tagged release is claimed by this document.

## Product boundary

AgentStoryline v0.1 is a **local-first agent-trace viewer**. Its primary workflow
is to open a recording, follow recorded activity through a storyline and chat or
terminal view, inspect the evidence, and optionally add descriptions. Recorded
playback reveals existing messages; it never executes recorded tools, commands,
scripts, or requests. Source agents remain independent projects.

The release covers local import, evidence review, playback, optional
descriptions, and portable exports. Procedural execution, general replay,
execution portability, live telemetry collection, and hosted collaboration are
outside v0.1. Existing replay/generated-workflow modules remain experimental
development references with their authorization, approval, dependency and local
scope controls intact.

## Installation and first recording

Python 3.10+ and the standard library are sufficient. No model account or source
agent installation is needed to review a recording. Artex `.tar.zst` import
additionally requires an installed `zstd` executable.

From the repository root:

```bash
python3 scripts/viewer_demo.py work/viewer-demo
python3 agentstoryline.py viewer work/viewer-demo/recording.traceforge.json --state-dir work/viewer-state
```

The first command creates a new directory containing a normalized bundle,
standalone `timeline.html`, and portable recording from newly authored synthetic
Artex-shaped fixtures. It makes no provider calls and executes no recorded
actions. Choose a fresh output directory if `work/viewer-demo` already exists.
The second command starts the loopback viewer; open the local URL it prints.
The generated HTML can also be opened directly for offline review. Outputs stay
in ignored `work/`.

Replace the demo source with your selected local recording:

```bash
python3 agentstoryline.py viewer /path/to/recording.tar --state-dir work/viewer-state
python3 agentstoryline.py viewer /path/to/codex-rollout.jsonl --state-dir work/viewer-state
python3 agentstoryline.py viewer /path/to/otlp-export.json --state-dir work/viewer-state
python3 agentstoryline.py viewer /path/to/orcareplay-trajectory.orca.zip --state-dir work/viewer-state
```

Import does not overwrite source files. Keep the state directory to retain
annotations, revisions and access to locally retained original artifacts.

## Source support

| Input | v0.1 status | Boundary |
| --- | --- | --- |
| Artex TaskArchiveSnapshot formats 1–3: `.tar`, `.tar.zst`, extracted export directory or supported snapshot JSON | Supported | Dedicated Artex interpretation profile; historical/partial capture remains explicit |
| Portable `.traceforge.json` recording, envelope version `1` | Supported | Display package; not an execution package or complete source archive |
| Codex native rollout JSONL | Experimental | One explicitly selected supported native session; partial transcript mapping with a generic description profile |
| Offline OTLP protobuf-JSON traces/logs | Experimental | Bounded pinned profile for one JSON object containing `resourceSpans`, `resourceLogs`, or both; conservative GenAI mapping |
| OrcaReplay native `.orca`/`.orca.zip`, trajectory directory or plain `.tar` | Experimental | ZIP is the native export container; tar is an additional convenience. Read-only `manifest.json`, `events.jsonl` and referenced blobs; recorded snapshot metadata/diffs, without filesystem or Git restoration |
| `codex exec --json`, OTLP file-exporter JSONL, live OTLP collection | Outside v0.1 | Distinct formats/services; no compatibility inferred from similar names |
| LiteLLM or JEV-specific recordings/profiles | Future | No dedicated adapter or source-specific interpretation claimed |

The served viewer's **Import recording** menu presents the supported source
families before opening its local file picker; content detection remains
authoritative. Extracted Artex and OrcaReplay trajectory directories are CLI inputs. Standalone HTML opens portable recordings;
native/telemetry normalization requires the local service. Unknown JSON is
rejected rather than guessed to be Artex. Adapter bounds still apply, and native
import may exceed the smaller portable viewer limits below.

Source badges and metadata report declared provenance, not authenticated origin.
Explicit `manifest.source.adapter` controls adapter selection. Only an absent
adapter field permits the legacy known-name fallback; unknown explicit adapters
retain generic interpretation. Common bundle schema `0.1`, portable envelope
version `1`, `.traceforge.json`, and the `traceforge.py` compatibility command
retain their identifiers without renaming original evidence.

Codex import maps supported visible assistant messages and tool calls/results;
it does not reconstruct hidden reasoning or missing provider requests, schemas,
retries or stream boundaries. Original selected artifacts remain unchanged, so
select the intended input records before import if raw-file exclusions are
needed. Every OTLP import is marked partial; sampling, filtering and missing
content cannot be disproved from one payload. OTLP span ancestry and links do
not establish tool-to-prompt data dependencies. OrcaReplay maps recorded requests/responses and tool calls/results, retaining
unknown records as observations. Snapshot records and their diffs are inspection
evidence; importing does not restore checkpoints, files or Git state. No
OrcaReplay runtime is needed and its execution/replay behavior is outside this
adapter. See the [OTLP profile](OTLP_ADAPTER.md) and
[OrcaReplay profile](ORCAREPLAY_ADAPTER.md).

## Review and presentation

The viewer provides chat/terminal and single/multiple-chat views, recorded
milestones, an evidence inspector, optional notification audio, and storyline
zoom, scrolling and playhead seeking. **Theme** offers Green, Spotlight (the
default), and Dark. **Text → Original / Explained** is separate from theme and
switches between recorded text and available descriptions.

Smart pacing adjusts reading time; Recorded pacing follows source elapsed time.
The condensed storyline axis can compress intervals with no recorded activity.
Neither presentation changes source timestamps or proves why an interval was
empty. Missing data, uncertain associations, inferred interpretations and
partial coverage remain distinguishable from observed evidence.

## Optional descriptions

**Improve descriptions** defaults to missing descriptions and **Offline / Codex
session**. This queues a bound local job for an authorized worker in the existing
session; it cannot awaken Codex automatically and does not run a local model.
Manual request export/response import remains available. **API** requires
explicit generation with a configured endpoint, model and key; selected excerpts
then leave the local viewer for that provider and may incur charges. Import and
playback never start generation.

Annotations are evidence-linked interpretations stored separately with revision
history. Validation checks structure, source binding and evidence references; it
does not establish factual correctness or authenticate the source. Artex has a
dedicated profile, while experimental and unknown sources use a conservative
generic profile. Existing local annotations take precedence when reopening the
same source fingerprint; embedded annotations do not silently replace saved
local revisions.

## Exports and original evidence

The top-level **Export recording** action saves the currently loaded portable
JSON and stays disabled until a recording is loaded. It includes current
descriptions and attached scripts, but not original archive/workspace bytes.
**Improve descriptions → Optional details → Save enriched recording** also saves
the current annotations. **Download HTML** saves a standalone viewer. HTML
supports offline review, while new description generation and persistent saves
require the local service.

Portable packages are bounded to **128 MiB**, with at most **5,000 rows per
collection** for events, messages, actors, activities and lanes. Structure, text,
attachment and adapter-specific limits also apply; these ceilings do not promise
comfortable performance at every maximum. Larger native importer limits do not
guarantee successful viewer export.

Exports retain recorded content; normal export is not redaction. Source-file
inventory and parsed transcript history travel with the display package, but
original workspace/transcript file bytes do not. The local service can preview
up to 64 KiB of retained UTF-8 text and download exact original bytes after
checksum validation. Those controls require a matching local import and intact
retained files in the same state directory. Portable paths never grant access to
arbitrary local files. Standalone HTML or a portable copy on another machine
cannot recover files that were not embedded. Scripts and HTML in source files
remain inert inspection data.

The experimental **Share recording** and **Add Python script** controls are
currently hidden. Sharing, when enabled for development, prepares a separate
redacted derivative with preview, report and review acknowledgment. It omits
attachments, original artifact references and audio by default, preserves
evidence identifiers and performs no upload or publication. Redaction is best
effort; review the complete derivative before distributing it. Hidden sharing
does not make normal exports safe to publish.

## Readiness and validation

The scope above defines v0.1 behavior; it is not a claim that release checks or
publication are complete. Repository tests use synthetic fixtures. Run:

```bash
python3 -m unittest discover -s tests -v
```

Build/runtime checks, browser inspection and unresolved release work are recorded
in [release validation](RELEASE_VALIDATION.md). Package boundaries and source
review are recorded in the [public source audit](PUBLIC_SOURCE_AUDIT.md). Real
recordings, credentials, private run data and private research stay outside
release artifacts and Git.
