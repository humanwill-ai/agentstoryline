# Public source package audit

Review date: 2026-10-18. Scope: the current working-tree source package for the
AgentStoryline v0.1.0 viewer candidate, including step 5 release materials and the experimental read-only OrcaReplay adapter.
This is preparation for owner review, not publication authorization or a
Git-history audit.

## Staging boundary

Run from the checkout with Python 3.10+:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/build_public_source.py /private/tmp/agentstoryline-public-source
PYTHONDONTWRITEBYTECODE=1 python3 scripts/verify_release_candidate.py /private/tmp/agentstoryline-public-source
```

The destination must be new, outside this checkout, with an existing parent.
On macOS use the real `/private/tmp` path rather than its symlink alias.
The script reads only the exact paths in `PUBLIC_FILES` and the three exact
`OPTIONAL_FILES` media paths. Missing required files fail before any destination
is created. Missing optional media is reported in the manifest. It never copies
a directory recursively from the source, reads Git objects, commits, pushes,
uploads, changes repository visibility or publishes a release.

Every copied file retains its original bytes. Output paths are sorted; files
have mode 0644, directories 0755, and modification times are normalized to the
Unix epoch. `PUBLIC_SOURCE_MANIFEST.json` records paths, byte counts, SHA-256
hashes, omitted optional files and an aggregate hash of the canonical file list.
The manifest's release_version comes from agentstoryline_version.py; application
release 0.1.0 is separate from manifest format version 1, bundle schema 0.1 and
portable envelope version 1. The version module is itself copied and hashed.
The file count excludes the manifest itself. Absolute checkout paths,
timestamps, Git author/account metadata and private source pins are not added
to it.

The release verifier checks required release files, strict manifest structure,
the exact inventory, hashes and local documentation targets/anchors without
running candidate actions, tests or provider calls. It refuses extra files,
directories and caches, and does not fetch external links. Use the trusted
checkout's verifier on the candidate; see [release commands](RELEASE_COMMANDS.md)
for the supported invocation and limitations. Suppress bytecode while checking
or running tests in a candidate; generated caches make its inventory unclean.

Existing destinations, including empty directories, are refused. No overwrite or
cleanup option is provided. Symlink source files and symlink path components,
special files, traversal and prohibited allowlist paths are rejected. Sources are
fully read and checked before creating the output directory. An output I/O failure
can leave a partial new directory; use another new destination after inspection.
This is a local snapshot tool and does not claim protection against hostile
concurrent filesystem mutation.

## Inventory decisions

| Material | Package decision and evidence |
| --- | --- |
| Application modules and browser templates | Include exact Python, JavaScript, CSS and HTML paths under adapters, analysis, core, replay and web, plus both CLI entry points and agentstoryline_version.py. The replay code remains experimental and retains explicit execution/scope checks. |
| Contracts | Include the two schemas and two contract guides; no vendored adapter runtime is included. |
| License and notices | Require the full Apache-2.0 LICENSE and THIRD_PARTY_NOTICES.md; copy unchanged. The original-code grant does not relicense imported recordings or external assets. |
| Current user documentation | Include README, RELEASE_SCOPE_V0_1, RELEASE_VALIDATION, this audit, BRANDING, OTLP_ADAPTER, ORCAREPLAY_ADAPTER, STORYLINE, REPLAY and GENERATED_WORKFLOW. The release scope is the primary current guide. |
| Release and contribution materials | Include CHANGELOG, CONTRIBUTING, SECURITY, RELEASE_NOTES_V0_1, PUBLICATION_CHECKLIST and RELEASE_COMMANDS. These describe the prepared/unreleased candidate, synthetic contribution policy, pending private-reporting setup, remaining browser check and separate owner-controlled publication gates. They contain no credentials, raw recordings or private integration pins. |
| Mascot PNGs | Include the four exact Sleepy Replicant master/export paths. BRANDING documents owner selection and OpenAI image-generation provenance; THIRD_PARTY_NOTICES inventories them. |
| Release screenshots/video | Only docs/assets/agentstoryline-overview.png, agentstoryline-chat.png and agentstoryline-demo.mp4 may be copied if present. The PNGs come from the authored synthetic examples. The reviewed video is a bounded highlight montage from the authorized isolated synthetic-target Artex run; it excludes raw credentials, token values and source-recording bytes. Review every replacement independently before distribution. |
| Synthetic fixture files | Include the exact seven files under tests/fixtures. The Artex manifest says “Synthetic local data; no source runs or model outputs”; the interpretation README explicitly distinguishes authored interpretation from provider output; the concurrent storyline manifest labels itself an authored UI fixture. The recorded-demo-token is an inert synthetic value. |
| Public examples | Include the exact self-contained generator, example README, the generated Artex/Codex/OTLP inputs, a newly authored OrcaReplay trajectory directory/native `.orca.zip`/plain tar, and their regression tests. These are newly authored synthetic data; no raw trace is used as an input. |
| Test modules and local utilities | Include explicitly named tests and synthetic/local-only scripts, including verify_release_candidate.py and test_release_candidate.py. Tests with credential-like canaries intentionally exercise redaction; these strings are authored fixtures, not credentials. No test is run by staging or release verification. |
| Private Artex integration inventory | Exclude integrations/artex-baseline.json and artex.lock.json. They contain private baseline paths, hashes and integration history. Exclude verify_artex_pin.py and its pin-specific test from this package. Keep the private originals intact. |
| Internal instructions/history/plans | Exclude AGENTS, HANDOFF, IMPLEMENTATION_BRIEF, PYTHON_FIRST_VERIFICATION_PLAN, RELEASE_COMPATIBILITY_CHECKPOINT and RECORDING_INTEROPERABILITY_PLAN. They record internal work/authorization history or links into that history. |
| Adapter/history guides needing separate public editing | Exclude ARCHITECTURE, ARTEX_ADAPTER, ARTEX_ARCHIVE_COMPATIBILITY, CODEX_ADAPTER, CHAT_EXPLANATIONS, FLOW_VIEWER and ARTEX_VIEWER_V1. These contain private integration pins, local evidence locations, retained-run details, or links to internal planning. Their useful feature descriptions are covered by the current release scope. |
| Runtime data and Git history | Exclude all work/, recordings/, Git internals, caches, virtual environments, local state, generated private HTML/portable recordings, screenshots, videos, transcripts, attachments, provider output and execution logs except individually reviewed synthetic example/media paths. Tracked status alone never grants inclusion. |
| Credentials and service material | No environment files, keys, credential stores, private research, paid-provider run evidence, external datasets or provider-generated transcripts are admitted. The optional API client code and documentation are original implementation, not a bundled paid service or captured provider result. |
| Audio | No WAV or Mixkit asset is allowlisted. Public demos use the built-in generated notification tone; user-supplied sound and old exports need their own rights review. |

OrcaReplay implementation, including its bounded ZIP reader, adapter tests,
guide and deterministic directory/native-ZIP/plain-tar samples are individually
allowlisted. No upstream dataset, capture, package source or
OrcaReplay runtime is bundled. The new example bytes are generated from authored
constants under Apache-2.0, including hashes of authored blob contents. External
compatibility captures remain validation-only and excluded.

The exact allowlist in the builder is authoritative. A newly created file is
excluded by default even if Git tracks it. Changes to an allowlisted file are
copied as current bytes and require renewed content review; allowlisting is not
automatic credential detection or a substitute for review. Internal-file
exclusions are packaging decisions, not removal or rewriting of source history.

## Review performed and limits

The review compared tracked files and untracked nonignored additions, inspected
fixture provenance, asset references and source/document references to private
paths, integration pins, recordings, credential fields and audio. The
nonignored inventory contains original implementation, authored fixtures and
documented mascot assets; real evidence remains in private runtime locations.
Runtime folders and Git history were deliberately not opened or packaged.

PNG chunk inspection found OpenAI/C2PA generation provenance in the mascot
master and only color-space/dimension EXIF metadata in the smaller exports.
The master provenance is preserved unchanged. A bounded printable-marker check
found no checkout-owner name, local home path, API-key field, bearer credential,
research or instruction marker in that provenance; this is not comprehensive
metadata or rights certification.

OTLP_ADAPTER is suitable for inclusion: it documents the bounded JSON profile,
synthetic tests and a clearly limited local SDK exporter checkpoint without
embedding its raw capture or machine paths. RECORDING_INTEROPERABILITY_PLAN
contains no raw trace but remains excluded because it is internal authorization
and planning history linked to excluded documents.

New synthetic tests in test_public_source.py verify exact inclusion and
exclusion, identical manifests/content across two builds, per-file hashes and
normalized metadata, exact optional-media inclusion, unchanged existing outputs,
unsafe destination refusal, symlink/special-file rejection, missing required
files, required notices and prohibited allowlist additions. Release validation
records build/test results. The final manifest records the aggregate package
hash; report that hash outside the package to avoid a self-referential document.

The step 5 release-material review checked the nine newly allowlisted release,
version and verifier files for local links, private checkout/evidence locations,
private repository identifiers and credential-shaped values. Only documented
temporary output examples and authored unsafe-path test cases were found;
no private evidence or credential value was found. Prepared/unreleased wording
and the uncompleted second-browser/private-reporting gates remain explicit.
The versioned builder tests also check that release-material and version-file
bytes survive staging and that an unreviewed release draft stays excluded.
The verifier's synthetic tests cover manifest/version tampering, inventory
changes, unsafe paths, local targets/anchors, and inert candidate code.

A staged snapshot contains no private Git history. Publishing this repository
or its history would require a separate history/secret/rights audit. Existing
teaser images and earlier recordings are outside this package review; their
prior publication does not authorize copying them into the source package.
No distribution, paid call, remote target operation or publication is performed
by this workflow.


## Final prepublication privacy review — 2026-10-18

An independent review inspected every candidate file, archive member and metadata,
the distributed screenshots and PNG metadata, and the video container metadata.
No actual credentials, personal email addresses, owner home paths or private
research references were found. Synthetic redaction canaries intentionally
contain example paths, addresses and tokens; they are authored test inputs.
Archive ownership is blank/zero and ZIP comments/extra fields are empty. The
mascot's C2PA provenance retains OpenAI/ChatGPT attribution and generated content
IDs without user identity. The demo MP4 contains standard encoder/container
metadata after removal of the editor's personal author fields.

The intended release commit uses a GitHub noreply address and a clean root created
from this exact allowlisted snapshot. No development Git history is imported.
Existing public teaser history is a separate review scope; replacing or deleting
it is not part of source staging. The owner waived the second-browser gate for
v0.1.0, without claiming compatibility validation.
