# Combined OrcaRouter sessions

Select **Import recording → OrcaReplay / OrcaRouter**, then choose multiple
native `.orca` or `.orca.zip` files together. The local viewer combines 2–16
files, up to 32 MiB in aggregate and 5,000 normalized events. A single file
still opens normally. Mixed source formats, duplicate run IDs, and non-Orca
containers are rejected. Native imports require the local viewer service.

The CLI accepts the same collection:

```sh
python3 agentstoryline.py viewer session-a.orca.zip session-b.orca.zip --state-dir work/viewer-state
```

All sessions appear together by default in the storyline and chat. The Session
selector isolates one session's storyline, messages and milestones. Filtering
does not change the recording clock or underlying evidence. Export recording
saves the combined portable recording, including optional AI annotations.

## What the combination establishes

Each session retains its source run ID and run key. Canonical event, call and
artifact identifiers are namespaced to prevent collisions; raw source records,
model/tool payloads and original ZIP bytes are retained. Messages link to their
source session and original sequence. One model can share a lane while its
messages remain attributed to the session making the call.

Events are ordered by normalized absolute timestamps. Equal timestamps retain
selection order and each file's original order. These gateway timestamps may be
reconstructed, so apparent overlap is not proof of precise execution timing.

Selecting files together does not establish a shared task, delegation or
communication. The combined manifest explicitly records the user-selected
association. No coordinator/worker roles are borrowed from Artex exports, parsed
as facts from suggestive run names, or invented from timestamp proximity.
Within-session source causes and request IDs remain scoped to their session.

For combined Orca recordings, AgentStoryline also checks bounded decoded request
content, tool arguments/results and model response text for substantial exact
reuse across sessions. These links are labeled **content-inferred**, drawn as
dashed blue correlations, and kept separate from recorded dependencies. Each
link retains endpoint events, evidence paths, content hashes and limitations.
Repeated request history is collapsed. Static system prompts, short identifiers,
timing proximity and run-key names never create links. Shared typed identifiers
such as task or intent IDs are inspectable but do not establish direction or
causality on their own.

When such links exist, the combined Orca view adds an evidence strip above the
storyline, dashed-link badges on involved activities, and presentation-only
handoff cards in the single combined chat. Cards appear only after both endpoint
events have been revealed. Selecting a chip, arrow or card highlights the two
storyline endpoints and opens the match method, limitations and original source
and target evidence. These additions do not enter the message stream, message
counts, timestamps, milestones or replay plan. Other import adapters and
single-session Orca recordings retain their existing presentation.

## OrcaTest 1 checkpoint

The four supplied OrcaRouter ZIPs contain 226 events and 33 model exchanges.
Their run keys are `exp1-goals`, `exp1-worker-i2`, `exp1-planner` and `conv-1`.
These labels are used as recorded. All source causes resolve within their own
files; no explicit cross-session parent-run links or shared request/tool IDs were
found. Payload analysis does find exact goal-context reuse, the worker's final
answer inside a later worker-output result, and a report request explicitly
marked as tool-triggered with matching finding context. These support useful
content-inferred relationships and shared typed task references, but not a
proven coordinator-to-worker delegation graph.

No native Artex archive, database or custom recording export was used for this
checkpoint. Optional AI milestone annotations use only the supplied Orca tool
results and remain marked AI-inferred. Original unsanitized exports and private generated artifacts remain outside Git.
The owner-approved sanitized derivatives are provided separately in
[`examples/orcatest-1`](../examples/orcatest-1/README.md); see their sanitization
manifest and the [import walkthrough](ARTEX_ORCA_WALKTHROUGH.md).

Native causal correlation still requires the recorder to provide an explicit
shared task/trace ID, stable participant IDs and parent/delegation references.
Until then, the viewer preserves the distinction between useful content-derived
review links and source-established causal relationships.
