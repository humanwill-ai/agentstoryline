# Experimental read-only OrcaReplay adapter

The first AgentStoryline release includes an experimental, offline reader for
OrcaReplay `orca-trace` recordings. It accepts native ZIP-based `.orca` and
`.orca.zip` exports, a selected trajectory directory, or a plain `.tar` containing
a trajectory at its root or inside one run folder. ZIP is the native export
container; plain tar is an additional import convenience.
This is an evidence-import profile: playback does not execute recorded actions,
restore files, reset Git, or invoke OrcaReplay's replay runtime.

```sh
python3 agentstoryline.py import-orcareplay examples/experimental-orcareplay.orca.zip /outside-git/new-orcareplay-bundle
python3 agentstoryline.py viewer examples/experimental-orcareplay.orca.zip --state-dir work/viewer-state
python3 agentstoryline.py viewer examples/experimental-orcareplay --state-dir work/viewer-state
```

Directories are CLI inputs. The served viewer also offers **OrcaReplay** in its
source-family menu for selecting a native `.orca`/`.orca.zip` or plain tar file.
Content detection selects the reader; a filename or source-menu choice does not authenticate provenance.
Unknown or malformed input is rejected instead of falling back to Artex.

The Python entry point is `adapters.orcareplay.import_orcareplay(source,
destination)`. It creates a new bundle, leaves source bytes unchanged, declares
`manifest.source.adapter` as `orcareplay`, and uses the conservative generic
description profile. No OrcaReplay installation, package manager, model account,
network connection or optional archive decompressor is required.

## Recording and mapping boundary

The profile reads native `manifest.json`, `events.jsonl` and referenced
content-addressed blobs. Recorded event order, source identities, wall timestamps, attributes and payloads
remain evidence. Viewer timestamps derive from `created_at` plus `mono_us` to
retain the recorded monotonic order; original wall times remain in event data. Original manifest/event/blob bytes are
retained under `artifacts/orcareplay/files/` with SHA-256 references. Native event and
blob hashes are checked; a missing or mismatched referenced blob is an import
error. A matching hash verifies consistency of supplied bytes, not authorship,
truth, approval or completeness.

Model request/response records and tool calls/results become corresponding
viewer events. Repeated messages inside model requests remain request context;
they do not become duplicate observed conversation messages. Shell operations
remain distinguishable from ordinary tool records. Unknown record types remain
observations, with their original type and payload available for inspection.
Explicit recorded `causes` links refer only to prior source records and stay in event data; no canonical
dependencies or tool-result pairings are emitted from those links, chronology,
matching names or protocol proposals. A recorded tool proposal
does not prove execution, and a reported result is not an independent execution
attestation.

Snapshot metadata, recorded file diffs, provenance, redaction declarations and
available referenced blob evidence are retained. Snapshot references do not
establish that a workspace can be reconstructed. Filesystem snapshot bodies,
Git objects and restoration are outside this profile; excluded capture material
and unavailable content remain explicit in coverage. The importer does not run
shell text, restore a repository, fetch missing blobs or reconstruct hidden
reasoning, missing provider calls or omitted history.

`.tar.zst`, compressed tar variants, live recording, execution replay,
and universal compatibility with future OrcaReplay schema versions are outside
this first profile. Portable viewer size limits still apply to imported bundles;
a valid native recording may be too large for portable export. Portable JSON and
HTML retain normalized evidence and source-file inventory, but do not embed
original source file bytes; exact retained-file access needs the matching local
import. See [release scope](RELEASE_SCOPE_V0_1.md).

## Input safety limits

The bounded reader limits manifests to 1 MiB, individual input files to 32 MiB,
cumulative uncompressed input to 128 MiB, ZIP/plain tar archives to 256 MiB,
inventory entries to 10,000, event records to 50,000 and JSONL lines to 4 MiB. It rejects unsafe paths,
symlinks, archive links/special members, duplicate file paths or JSON keys,
non-finite JSON numbers, regressing monotonic time, invalid event sequences and
existing destinations. ZIP32 stored and deflate members are supported. Encrypted,
ZIP64 and multi-disk archives and other compression methods are rejected. ZIP central
directories are capped at 16 MiB and member paths at 32 components. Local/central
headers, bounded decompression, declared sizes and CRCs are checked before native
event/blob digests. Native digests and referenced blob sizes are checked.
A genuinely truncated final unterminated JSONL record can be retained unchanged
without mapping it; coverage reports that gap. These bounds do not guarantee
that the resulting bundle fits the smaller portable viewer limits. The served
viewer upload limit is 32 MiB for native input, smaller than the CLI archive
limit; use the CLI for larger bounded inputs.

## Review and validation

Source review date: 2026-10-18. The reviewed upstream is
[Continuum-AI-Corp/OrcaReplay at a9178ca](https://github.com/Continuum-AI-Corp/OrcaReplay/tree/a9178ca26af95cfd3231b8eb2da4ae11dbbd0a84),
including its ZIP archive writer/reader and `orca-trace` manifest, event and
blob layout. The machine schema leaves `schema_version` as an unrestricted
string; reviewed sample manifests contain both `0.1.0` and `0.3.0`, while the
prose specification labels itself v0 / `0.1.0`. These observations do not imply
universal compatibility with every version or establish a fixed machine-schema
version requirement upstream. This bounded importer accepts only `0.1.0` and
`0.3.0` for its common supported profile, validates their structure and evidence,
and rejects other version labels.
This revision identifies the interoperability reference; it is not a vendored
runtime dependency or a claim about every upstream recorder/adapter.

The checked-in [synthetic example](../examples/README.md) is newly authored under
Apache-2.0 from deterministic constants. It contains no upstream sample bytes,
private run evidence, real model output or credential. Tests cover deterministic
sample generation, equivalent directory/plain-tar/native-ZIP import, evidence
checksums and portable viewer reload. Importer safety/malformed-input checks
are separate from those example checks. Actual executed results and any upstream compatibility
checkpoints are recorded in [release validation](RELEASE_VALIDATION.md); this
guide does not claim unrun checks passed or that a hosted-console export was
tested. External upstream recordings used for validation are excluded from the
source package.

Release preparation does not authorize publishing the repository, the source
candidate or any imported recording. Imported recordings keep their own rights;
Apache-2.0 applies to the newly authored implementation and synthetic examples.
