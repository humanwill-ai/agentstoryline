# AgentStoryline v0.2.0 — 2026-10-24

Download the [v0.2.0 GitHub release](https://github.com/humanwill-ai/agentstoryline/releases/tag/v0.2.0),
including the reviewed source and sanitized Artex/Orca demo recordings.
AgentStoryline remains a local-first viewer: playback never
executes recorded commands or tools, and procedural replay stays outside this
version's release scope.

## Changes

- Combine native OrcaRouter sessions with retained source attribution and
  evidence-linked, explicitly content-inferred cross-session context.
- Attach Orca telemetry to Artex recordings after reviewing a match preview.
  Compare Story, Model telemetry and Both, inspect match evidence and unresolved
  links, and filter Artex versus telemetry links in Both.
- Show recorded Artex task/evidence relationships, supported explicit outcome
  milestones and optional labeled AI milestone suggestions.
- Preserve telemetry attachments in portable exports. Add an explicitly approved
  sanitized Artex + four-session Orca demo and an
  [illustrated local walkthrough](ARTEX_ORCA_WALKTHROUGH.md).

## Boundaries and known limits

With telemetry attached, **Improve descriptions** currently operates on the
Artex Story; those explanations also appear in Both. It does not enrich the
attached Model telemetry view. Import, attachment and playback make no model
calls. Generation uses the explicitly selected API provider or bound offline
session workflow.

Source milestones require supported structured outcome evidence; ordinary tool
completion and model prose do not automatically become milestones. AI milestone
suggestions are off by default, require generation and appear in Explained mode;
generation may return none. Suggestions remain interpretations, and validation
does not establish factual correctness. This update retains that behavior. See
[milestones](MILESTONES.md).

Telemetry matches are conservative correlations, not provider attestation or
independent corroboration. Ambiguous, conflicting and unresolved relationships
remain inspectable; clock skew and differently segmented SSE captures can limit
matching. See [attachment limits](TELEMETRY_ATTACHMENTS.md) and
[Orca collection semantics](ORCA_COLLECTIONS.md).

Native and telemetry imports require the local service. Portable JSON and HTML
do not embed original archive/workspace bytes. Ordinary exports retain recorded
content and attached telemetry without automatic redaction; the experimental
sanitized-sharing derivative omits telemetry. Original private recordings remain
excluded from the public source.

The common bundle remains schema `0.1`, the portable envelope remains version
`1`, and `.traceforge.json`, `traceforge.py` and existing description state remain
compatible. Codex, OTLP and OrcaReplay imports retain their experimental status.
See [release validation](RELEASE_VALIDATION.md) for recorded checks and limitations,
the [changelog](../CHANGELOG.md) for history, and the
[v0.1 notes](RELEASE_NOTES_V0_1.md) for inherited viewer boundaries.
