# Changelog

## v0.2.0 — 2026-10-24

Application/source version update; a GitHub release is a separate publication.
See the [v0.2.0 notes](docs/RELEASE_NOTES_V0_2.md) for scope and limits.

- Combine native OrcaRouter sessions while retaining source attribution and
  evidence-linked, content-inferred cross-session context.
- Attach Orca telemetry to Artex recordings with a reviewable match preview and
  Story, Model telemetry and Both views. Preserve attachment evidence in portable
  exports; sanitized sharing omits telemetry.
- Show recorded Artex task/evidence relationships, explicit outcome milestones,
  and optional, labeled AI milestone suggestions.
- Add a sanitized Artex + four-session Orca demo and illustrated local import and
  attachment walkthrough. Original private recordings remain excluded.

## v0.1.0 — 2026-10-18

AgentStoryline's first public release is a local-first agent-trace viewer.
[Download v0.1.0](https://github.com/humanwill-ai/agentstoryline/releases/tag/v0.1.0).
This is a local application; no hosted deployment is included.

- Import supported Artex TaskArchiveSnapshot formats 1–3 and portable
  `.traceforge.json` recordings. Add experimental native Codex rollout JSONL
  and bounded offline OTLP protobuf-JSON import. Include experimental read-only
  OrcaReplay native ZIP-based `.orca`/`.orca.zip` files, trajectory directories
  and additional plain tar import, preserving snapshot
  metadata/diffs and referenced evidence without restoring filesystem or Git state.
- Review recorded activity in storyline, chat and terminal views, with evidence
  inspection, seeking, Smart/Recorded pacing and three display themes.
- Optionally add evidence-linked descriptions through a bound offline session
  job, manual exchange or explicitly configured API generation. Preserve local
  annotation revisions separately from recorded evidence.
- Export portable JSON and standalone HTML. Inspect retained original files
  through the matching local import; original file bytes are not embedded in
  portable exports.
- Provide newly authored synthetic examples, reviewed synthetic-target demo media,
  Apache-2.0 licensing, notices and a deterministic source-snapshot builder.
- Preserve `traceforge.py`, bundle schema `0.1`, portable envelope version `1`,
  `.traceforge.json` and existing local description-state compatibility.

Playback never executes recorded actions. Procedural replay and generated
workflows remain experimental development modules outside v0.1. Normal exports
retain recorded content and are not redacted. Codex/OTLP/OrcaReplay mapping, coverage,
performance and browser-validation limits are described in the
[release notes](docs/RELEASE_NOTES_V0_1.md). The owner waived the current second-browser visual and
keyboard pass for v0.1.0 on 2026-10-18; this remains an untested compatibility
limit. See the
[publication checklist](docs/PUBLICATION_CHECKLIST.md).
