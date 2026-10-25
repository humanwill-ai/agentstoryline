# Evidence-linked milestones

Milestones describe outcomes in the recording's own domain: tests passed,
report generated, attempted action, failed deployment, or an unconfirmed claim.
The viewer does not define a fixed list of security achievements.

## Explicit recorded outcomes

Canonical events can opt into `data.milestones`, a bounded list of objects with
`label` and `outcome`. The supported outcomes are `attempt`, `success`, `failure`
and `unconfirmed`. Labels are one line of at most 120 characters, with at most
16 entries per event. If supplied, `event_ids` must contain only the enclosing
event ID. Invalid entries stay in raw evidence but are omitted from presentation.

The derived message carries its actor and timestamp, own-event evidence links,
`source_reported` provenance and `explicit_source_outcome` basis. Source-reported
is not independently verified. Existing Artex evidence tags remain supported.
Ordinary tool completion, HTTP 200 responses and model prose do not automatically
become milestones.

An Orca producer may opt into the AgentStoryline extension by emitting a
`milestone` event with `attrs.label` and `attrs.outcome`. This is an explicit
extension contract, not a claim that current OrcaRouter exports emit it or that
it is part of the upstream standard. Other adapters must deliberately map their
own structured outcome fields to the canonical contract.

## Optional AI suggestions

In **Improve descriptions**, enable **Suggest milestones (AI-inferred)** and
choose the existing provider/API or offline-session workflow. The checkbox is
checked by default in the UI and can be unchecked. Generation can add up to three milestone suggestions per message
or explicitly return none. Suggestions cite only supplied evidence and never
choose an independent actor or timestamp. A source's success claim without
corroborating results should be `unconfirmed`, not upgraded into proof.

The UI labels suggestions **AI-inferred** and displays them in Explained mode.
Original mode excludes AI suggestions. Click a card to inspect its source
message and cited evidence. Playback and seeking reveal or remove cards with
their messages.

Suggestions are stored with descriptions in fingerprint-bound annotation
revisions, and included in portable recording export. The original recording
is unchanged. Existing description-only files remain readable. Schema validation
checks references, bounds and provenance, not factual truth; generated outcomes
remain interpretations for the reviewer to assess.

The CLI equivalent adds `--include-milestones` to `explanation-request`.
No model call occurs when exporting a request. Generation through an API still
requires the user's selected provider and credentials; the offline-session path
uses the active session without separate provider calls.
