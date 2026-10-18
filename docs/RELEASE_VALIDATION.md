# Synthetic release example validation

Validated locally on 2026-10-18 with Python 3.14.0 on Darwin x86_64. This report
covers redistributable synthetic examples and their offline recording pipeline.
It is not evidence of a public release, deployment, model-quality evaluation,
complete runtime capture, security-task success or a procedural replay test.

## Inputs and provenance

All input data is newly authored in
[`examples/generate_demos.py`](../examples/generate_demos.py). The generator
uses fixed values and Python's standard library; it does not read real sessions,
research, credentials, local account metadata or a runtime clock. The seven
generated input files, including a deterministic uncompressed Artex tar, are
checked in for direct use. Their bytes are verified against regenerated content.
No paid calls, model providers, internet targets or source operations were used.
The examples inherit the repository Apache-2.0 license.

| Input | Tier and profile | Observed importer result |
| --- | --- | --- |
| `examples/artex-local-inspection/` | Supported Artex native archive format 3; streamed `llm_records` and explicit v1 capture metadata | 9 events: 1 model request, 1 model response, 1 tool start, 1 tool end, 5 observations |
| `examples/artex-local-inspection.tar` | Same supported profile; native plain tar | Identical 9 events to the directory input |
| `examples/experimental-codex-rollout.jsonl` | Experimental Codex native rollout subset | 6 events: 2 visible model responses, 2 tool starts, 2 tool ends; no synthesized model requests |
| `examples/experimental-otlp.json` | Experimental OTLP protobuf-JSON offline profile, combined traces/logs container | 5 events: 1 model request, 2 model responses (one from a log), 1 tool start, 1 tool end |

Artex's five observations preserve task, exploration node, finding, snapshot and
transcript history evidence. Its invocation points to the explicitly identified
model request, and its result points to the matching invocation. The source
workspace JSON and transcript JSONL remain byte-exact inert artifacts. The
snapshot observation is an importer representation, not an extra source action.

Codex retains both function and custom tool call/output forms; each result has a
single recorded dependency on its unique explicit-ID invocation. The example's
synthetic CLI version and model/provider labels make no runtime compatibility or
provider-authenticity claim. It is not the distinct `codex exec --json` stream.

OTLP retains model and tool content, source nanoseconds, parent-span hierarchy
and the completion log's span correlation. No `parent_id` or `dependencies` are
created from those structural relationships. Its combined object is an offline
convenience envelope, not one OTLP wire service request or Collector file-exporter
JSONL. The numeric enums and hex IDs follow the supported protobuf-JSON profile.

Every imported bundle is intentionally **partial**, with explicit gaps. Artex
does not establish recorder loss counts or full wire capture. Codex does not
capture provider requests, retries or full tool schemas. OTLP cannot establish
sampling/export-window completeness or whether content capture was disabled.
An invocation request is not proof of approval or execution. Public compatibility
boundaries are documented in [Release scope v0.1](RELEASE_SCOPE_V0_1.md); the
[OTLP profile](OTLP_ADAPTER.md) describes its experimental mapping in detail.

## Automated checks and exact results

New focused test command:

```sh
python3 -m unittest discover -s tests -p test_release_examples.py -v
```

Result: **5 tests passed**, 0 failures, 0 errors, 0 skips; 0.225 seconds.

The five checks verify deterministic source bytes; both Artex source containers;
native Codex function/custom pair semantics; OTLP structural relationships and
nanosecond evidence; and detection/normalization through the viewer import path.
Each native import passes `load_bundle`, verifies artifact SHA-256 references,
builds the observed timeline/viewer payload, exports a portable recording with
`write_recording`, and reloads it with `load_portable`. All payloads and event
data survive the portable round trip unchanged, and no replay attachments are
added. Native source artifact bodies are checked against the original bytes.

Relevant existing suites plus the new examples:

```sh
python3 -m unittest tests.test_artex_adapter tests.test_codex_adapter tests.test_otlp_adapter tests.test_recording tests.test_release_examples -v
```

Result: **78 tests passed**, 0 failures, 0 errors, 0 skips; 0.930 seconds.
This includes strict input parsing, unsafe-source rejection, bounded imports,
destination preservation, artifact checksums, correlation ambiguity and portable
recording behavior. It exercises only the adapters' existing supported profiles.

The tests copy the checked-in example bytes to resolved temporary paths before
native imports. This permits directory-descriptor traversal inside the macOS app
sandbox; it preserves the importers' symlink and regular-file safety checks.
All generated bundles and portable outputs from these checks are temporary.

## Remaining validation limits

This focused checkpoint has no unresolved automated failures. It validates the
recording/import/export path, not external exporter versions, platform
portability, Artex execution or procedural replay. Raw real-run evidence and
generated private recordings remain outside the repository and examples.

## Broader release-candidate checks

The complete private working tree ran **441 tests** in 32.939 seconds with no
failures, errors or skips. This includes startup and loopback-service behavior,
every supported import route, playback and pacing, optional-description state,
normal portable export/reimport, original-artifact controls, source detection,
sharing guards and experimental replay isolation. Three existing Python
`ResourceWarning` messages about implicitly cleaned-up expected HTTP errors were
printed; they did not fail a test.

The exact reviewed public snapshot ran **440 tests** in 32.206 seconds with no
failures, errors or skips. It intentionally omits one private Artex integration
pin test with the excluded integration metadata. A second deterministic stage
matched the snapshot byte-for-byte. The authoritative file inventory and hash
are in its generated `PUBLIC_SOURCE_MANIFEST.json`; see the
[public source audit](PUBLIC_SOURCE_AUDIT.md).

Chrome 155.0.8059.80 loaded the viewer at 1920×1080 in Spotlight and rendered
source material for the demo. The current owner-supplied `AgentStorylineMusic.mov`
is 34.700 seconds, 1920×1080 H.264 with stereo 48 kHz AAC audio. It replaces the
previous remuxed MP4 and is copied byte-for-byte at the owner's explicit request.
Music, notification audio, title cards, QuickTime timecode, attached cover image,
and original metadata are preserved. The embedded author name is retained as
an explicit exception to the earlier metadata-stripping policy. Full decoding
and representative-frame inspection passed; no credential or token value was
observed in the sampled frames. Raw source-recording bytes remain excluded.

During Smart reading holds, the storyline cursor glides toward the next visibly
separated recorded position using viewing pace; near-coincident source positions
occupying less than one storyline pixel are treated as a presentation cluster.
The displayed evidence clock remains exact and held, while message arrivals and
source timestamps are unchanged. Instrumented cursor-motion checks passed. Both
checked-in PNGs remain 1920×1080 synthetic fixtures.

The owner waived a current second-browser interaction pass for v0.1.0 on
2026-10-18; it has not passed. The
installed Firefox is version 11 and cannot provide a meaningful current-browser
check; Safari 26.2 launched but was not automation-controlled in this session.
Automated DOM/contract tests cover browser-side interaction logic, but they do
not replace a current Safari or Firefox visual and keyboard pass.

## v0.1.0 step 5 release-material checkpoint

The prepared release uses application version `0.1.0`, exposed by both
`agentstoryline.py --version` and the compatibility `traceforge.py --version`
entry point. The public-source manifest records the same release version while
keeping manifest format version `1`, bundle schema `0.1` and portable envelope
version `1` as separate compatibility identifiers.

Two fresh source snapshots were built independently. Both contained the same
**150 source files**, included all three optional media files and had
byte-identical manifests. The strict release verifier accepted both, including
all per-file hashes, exact inventory and **66 local Markdown targets/anchors**.
Re-verification after the complete suite found no cache or other candidate-tree
mutation. The final aggregate package hash is reported outside the package so
this validation record does not create a self-referential hash.

A staged candidate ran **450 tests** on Python 3.14.0 / macOS 15.7.9 with no
failures, errors or skips. Three existing non-failing
`ResourceWarning` messages appeared while expected HTTP 403/400 errors were
cleaned up. JavaScript syntax validation passed. The documented synthetic
quickstart generated a 20-event portable recording and standalone HTML without
executing a recording, target or model operation; both CLI entry points printed
`AgentStoryline 0.1.0`.

This checkpoint prepares release materials only. The current second-browser
visual/keyboard pass, final owner review, Git-history/rights review if repository
history is proposed, private vulnerability-reporting setup, and explicit
authorization for any commit, push, tag, visibility change or publication
remain open gates in the [publication checklist](PUBLICATION_CHECKLIST.md).


## OrcaReplay first-release importer checkpoint — 2026-10-18

The experimental read-only OrcaReplay importer is included in the prepared
v0.1.0 source package, CLI and local viewer's Import recording menu. This
checkpoint covers offline inspection, not OrcaReplay execution, filesystem
restoration, capture completeness or universal recorder compatibility.

Upstream was inspected at commit
`a9178ca26af95cfd3231b8eb2da4ae11dbbd0a84`. The two public input directories were
fetched from that revision and tested outside the source package:

| External input | Source schema | Imported events | Retained exact source files |
| --- | --- | ---: | ---: |
| `examples/traces/run_9f2c14a03b71` | `0.3.0` | 31 | 8: manifest, event log, redactions and five blobs |
| `packages/cli/quickstart/trace` | `0.1.0` | 19 | 4: manifest, event log, redactions and one blob |

The first sample is explicitly described by upstream as handwritten, not a
real captured run. The second is an upstream quickstart fixture; no agent was
executed to produce or replay it during this validation. Both directories and
locally constructed plain tar equivalents imported with identical canonical
events. Every retained artifact checksum passed. Native viewer normalization,
portable export and reload preserved all mapped events and evidence. The
quickstart's 15 `fs/` files were listed as excluded snapshot bodies, never
restored or executed. All imports declare partial coverage.

The source event-log hashes were, respectively,
`07aa293d6af485d1637a64e0004453282bcca43f2b4cb32970eda4cabbdbd0de` and
`7062881f14cd88e4364bc34aa193ff1e4ecda5aa0ae02de29423cf7c1d429461`.
External fixture bytes are not vendored. The included eight-event, two-blob
OrcaReplay example is newly authored from deterministic constants, with a
reproducible plain tar and no private run content.

New adapter tests cover source/tar equivalence, original bytes, monotonic timing,
proposal/result semantics, repeated request history, neutral unknown events,
truncated tails, corruption, missing blobs, duplicate keys/nonfinite JSON,
symlinks, special files, archive traversal, resource bounds including hidden tar
metadata, and preservation of existing destinations. Integration checks cover
CLI routing, conservative source detection, actual loopback HTTP uploads,
source identity, bound original-file access, offline description handling, and
portable export/reopen. None calls a provider or executes imported operations.

The final public candidate passed **483 tests** in 34.514 seconds on Python
3.14.0 / macOS, with
no failures, errors or skips. Three existing non-failing expected-HTTP-error
`ResourceWarning` messages remain. JavaScript syntax and strict candidate
inventory, hashes and local documentation links passed. Browser
review clarified the tool-proposal badge and neutral-event labels; those fixes
and the new card-state regression are included in the final 483-test run.
The verified snapshot contains **161 source files** and checks **75 local
Markdown links/anchors**. A post-test verification found no candidate mutation.

Chrome's local viewer imported the synthetic `.tar` through the OrcaReplay
source choice and completed all eight recorded messages. The source badge says
experimental, original evidence remains inspectable, and proposals are labeled
without claiming execution. This does not close the previously open current
second-browser interaction gate or establish Windows/Linux runtime support.

The final source snapshot preserves the separately refreshed demo video and
contains no private recordings, external upstream fixtures or local viewer
state. Two independent final builds and their exact hashes are retained outside
the package to avoid a self-referential manifest. No commit, push, provider call,
CI dispatch, tag or publication occurred in this implementation task.


## Native OrcaReplay ZIP qualification — 2026-10-18

This checkpoint supersedes the earlier directory/plain-tar-only archive scope.
The CLI and served viewer now accept native ZIP-based `.orca` and `.orca.zip`
recordings (and recognized Orca ZIP content with a `.zip` name). `.ora` was an
owner typo and is not a supported viewer extension. Tar remains an additional
convenience, not the native exchange container. Canonical event and portable
recording schemas are unchanged.

The public upstream tree and releases at
`a9178ca26af95cfd3231b8eb2da4ae11dbbd0a84` contained no downloadable native archive.
For direct producer compatibility, the unchanged upstream
[`packages/cli/src/archive.ts`](https://github.com/Continuum-AI-Corp/OrcaReplay/blob/a9178ca26af95cfd3231b8eb2da4ae11dbbd0a84/packages/cli/src/archive.ts)
`writeArchive()` function was run locally under Node 26 against the two public
fixtures described above. Its source SHA-256 was
`5cdf4424cef69814effb73a3e29421dbe43de036a48c1103d138bc7a756389c3`.
The writer ran twice with identical output, and upstream `readArchive()` recovered
each entry byte-for-byte. No CLI capture, hosted-console export, model call,
agent execution or replay was performed.

| Upstream-writer output | ZIP members | Imported events | Retained source files | Excluded snapshot files |
| --- | ---: | ---: | ---: | ---: |
| Handwritten fixture as `.orca` | 8 deflate | 31 | 8 | 0 |
| Quickstart fixture as `.orca.zip` | 4 deflate | 19 | 4 | 0 |
| Quickstart with filesystem bodies as `.orca` | 8 deflate + 11 stored | 19 | 4 | 15 |

Archive SHA-256 values in table order:

- `cc3a3c1cb4c37d3045aa01d1085bd939026805bd708e18f30c49bdc65f523e02`
- `ba0b6e9069f4ef04d4f4c90c76a305362a32dc1f10925a08962e990a5fdcb868`
- `29ec7962a70543e72851feb2ede093f8ed76c865ca260437622f6bf6b28cfe3b`

All three imported with canonical events identical to their source directories.
Every retained artifact hash, native viewer normalization and portable export/
reload comparison passed against the packaged implementation. Excluded ZIP
snapshot bodies were decompressed within bounds and CRC checked, then discarded;
no snapshot was restored. External sample and producer bytes remain outside the
public source package. The bundled native ZIP example is separately authored
synthetic data, deterministically generated alongside its directory/tar versions.

The exact public source candidate passed **498 tests in 36.876 seconds** on
Python 3.14.0 / macOS, with no failures, errors or skips. Three existing non-failing
expected-HTTP-error ResourceWarnings remain. ZIP tests cover stored/deflate,
root/single-prefix layouts, data descriptors, empty directory streams, UTF-8
names, CRC/SHA corruption, conflicting paths, traversal, symlinks, bounds and
forged decompressed sizes. Unsupported encryption, ZIP64, multidisk archives and
compression methods are rejected. HTTP tests import `.orca`, `.orca.zip`, `.zip`
and tar, verify original-file access and portable roundtrips, and retain the
current recording after invalid uploads. Existing format regressions pass.

JavaScript syntax, exact package inventory/hashes and **75 local Markdown
links/anchors** passed. The source inventory now contains **163 files**. Chrome
rendered both upstream-writer archives with their correct 31/19-message counts
and experimental source badge. The browser import handler received File objects
through its real change event and submitted them to the loopback HTTP service;
automation's OS file-picker upload was unavailable due its separate path sandbox.
This is browser-handler/HTTP evidence, not an OS file-picker or second-browser
qualification. No existing private viewer session was changed.

Concurrent README, audit and demo-media updates were retained. No private
recording, external fixture, local viewer state or upstream implementation is
added to the public package. The existing publication gates remain open; this
change does not publish the first release or establish universal schema or hosted
OrcaRouter-export compatibility. No commit, push, CI or provider spending occurred.


Post-application verification preserved a concurrent storyline presentation-cursor
change. A second full packaged run completed 498 tests in 36.927 seconds: 497
passed and one existing frontend assertion still expected the prior cursor
expression. The concurrent work then updated that assertion; its entire
10-test frontend module passed in 1.049 seconds. The only package change between
those checks was that assertion. Strict 163-file/75-link verification passed;
the actual-checkout CLI also imported the upstream quickstart `.orca.zip`.
The earlier all-green 498-test run qualifies the Orca change before this unrelated
cursor merge; the combined source is covered by the second run plus its targeted
follow-up, not claimed as a third all-green full-suite run.


## Previous owner-edited media checkpoint — 2026-10-18 (superseded)

The owner-supplied 34.7-second launch edit replaces the earlier continuous
editing capture as the checked-in demo. A complete decode and representative
two-second-interval contact-sheet review confirmed the 1920×1080 app view remains
legible, the full chat is in frame, and no recorded credential or token value is
visible. All 11 audible notification bursts match the project-generated tone source capture
with normalized waveform correlation 0.99661–1.00000; retained capture and
synthesis instrumentation confirms the generated 720→350 Hz chirp.

The public MP4 contains only one 30 fps H.264 video stream and one stereo 48 kHz
AAC audio stream. The original QuickTime timecode, attached cover image, author
fields and other source metadata were removed by lossless remuxing. The retained
video and audio streams were not re-encoded. Deterministic public-source staging,
strict verification and the complete offline/synthetic suite are rerun against
the final packaged bytes; exact candidate hashes are recorded outside the
self-identifying package.


## Owner review preparation — 2026-10-18

The exact owner-edited-media candidate passed all **505 tests in 38.378 seconds**
on Python 3.14.0/macOS, plus both JavaScript syntax checks. Three existing
non-failing ResourceWarnings for expected HTTP 403/400 errors remain. Its
164 payload files (165 with the manifest) and 75 local documentation links
passed strict verification after testing.

The packaged synthetic quickstart generated 20 events, standalone HTML and
portable JSON. Both standalone and served Chrome views loaded, and event stepping
revealed all nine compact conversation entries. Export, authenticated reimport
and a second export returned identical portable JSON. Browser console checks
reported no warnings/errors or horizontal overflow. No recorded command or
provider operation was executed.

Independent source/privacy review covered all candidate files, bundled tar/ZIP
members and metadata, PNG metadata/screenshots and the reviewed video metadata.
No actual credential, personal email, owner home path or private research data
was found. Credential/path matches are authored synthetic test canaries. This
review does not guarantee exhaustive detection and does not cover private Git
history. The distribution uses a clean source-only history and noreply commit
metadata.

The owner authorized local commits and preparation through step 6 while retaining
a separate publication confirmation. The current second-browser check is waived
for this release, not passed. Private vulnerability reporting is still disabled
on the intended destination. The owner subsequently chose to keep it disabled;
it is no longer a publication prerequisite.
The final documentation revision is staged twice and checked again before the
local release commit; exact hashes and outcomes stay in the private handoff.


## Current unchanged music edit — 2026-10-18

The owner explicitly requested replacing the prior video with
`docs/assets/AgentStorylineMusic.mov` as supplied. The current file is 26,625,005
bytes, SHA-256 `fe17cb6716cffdeac64d4a8226d1139d30a2a25df1fe11777c7bff859a3e5cf4`.
The file was not remuxed, transcoded, normalized or stripped of metadata.
The original author/artist metadata remains; the earlier clean-metadata and
generated-tone-only findings apply to the superseded MP4, not this music edit.
The package allowlist and README now select the MOV and exclude the old MP4.
Source behavior is unchanged. Focused packaging checks and deterministic staging
validate the new media references; the preceding 505-test application result
remains the code-validation baseline. Publication is still on hold.


## Source-publication authorization — 2026-10-18

The owner authorized replacing the public teaser history with the reviewed clean
source history. Private vulnerability reporting remains disabled by the owner's
explicit decision, and the second-browser check remains waived. A local-only
backup of the old teaser history is retained before a guarded branch replacement.
Source publication does not create a version tag or GitHub Release. This update
changes status/policy documentation only; application behavior and the exact
owner-supplied MOV bytes remain unchanged. Final package and remote verification
are recorded outside the self-identifying manifest.


## v0.1.0 publication — 2026-10-18

After explicit owner confirmation, the annotated `v0.1.0` tag and public GitHub
Release were created at reviewed commit
`a52cc57022f06812709488d88d8f242051f11415`.
[Published release](https://github.com/humanwill-ai/agentstoryline/releases/tag/v0.1.0).
The tag remains immutable; this later main-branch documentation update records
the completed action and changes no application behavior.

GitHub reports the release as published, not draft or prerelease. Uploaded video
and manifest SHA-256 digests match their local reviewed bytes. The owner-supplied
music MOV is unchanged. Private vulnerability reporting remains disabled. No
hosted deployment or workflow enablement occurred. Prior 505-test application
and packaging results apply to the released code; the post-release documentation
package is separately checked for inventory, hashes and local links.
