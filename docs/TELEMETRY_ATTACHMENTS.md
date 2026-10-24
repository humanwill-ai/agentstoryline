# Optional Artex + Orca telemetry

Import an Artex archive or recording in the local viewer, then choose **Attach
OrcaRouter telemetry** and select 1–16 native `.orca` / `.orca.zip` files. Review the
match counts and choose **Apply**. Cancel leaves the current recording intact.
The operation reads local files only; it makes no model calls or target requests.

After attachment, the viewer offers:

- **Story**: the original Artex storyline with matched telemetry in details.
- **Model telemetry**: the complete attached Orca recording/collection.
- **Both**: Artex plus unmatched Orca model exchanges and explicitly linked tool
  proposals/results. Matched exchanges appear once. Routing remains available
  in details and in the telemetry view.

Selecting a matched message or activity exposes the source evidence and match
basis. Orca-only participants are labeled; attachment alone does not establish
that an unmatched session belongs to the task. Improve descriptions operates on
the Artex Story; existing Artex explanations also remain visible in Both.

## Evidence and matching

The original normalized Artex bundle and original normalized Orca bundle remain
separate and unchanged. A fingerprint-bound, reproducible correlation map links
unique exchanges using matching recorded request/response content or shared
request IDs with compatible content/model fields. Session names, models, and
nearby timestamps alone never establish a match. Ambiguity and conflicts remain
visible in the preview; neither creates a canonical dependency or execution
claim. Matching SSE captures is deliberately conservative: different chunk
segmentation may prevent a match even when the final text is equivalent.

Both is a disposable display projection. Clocks are aligned by recorded absolute
timestamps; clock skew is not corrected. Shared model lanes are presentation
aliases supported by matched exchanges. No provider attestation is implied.
Unmatched exchange counts and conflicting exchange counts may overlap: a
conflicting call is still unmatched, so the summary columns are not additive.

## Persistence and limits

Apply persists the package in local viewer state. Browser refresh keeps the
attachment. **Export recording** includes both normalized evidence bundles and
the correlation map; reopen that AgentStoryline JSON to restore the attachment.
Reopening the original Artex archive alone does not auto-attach prior telemetry.
Native archive bytes and workspace artifacts remain in the local import store;
they are not embedded in portable JSON. The source files remain untouched.

The attachment shares existing import/package limits (32 MiB native upload,
5,000 events per source bundle, 128 MiB portable package). Local service actions
require the current recording binding, request token and accepted origin.
Prepare previews expire after fifteen minutes; a new import or a change to the
recording invalidates the preview. Reattaching replaces the supplemental
collection only after review and Apply.

The sanitized-sharing derivative intentionally omits telemetry and states this
in its report: source-content hashes cannot survive redaction unchanged.
Ordinary Export preserves the attachment.

## OrcaTest 1 validation

The private comparison found 29 unique matches, four Orca-only reporter exchanges,
zero ambiguous exchanges, zero conflicting exchanges and one unmatched session.
The combined display retains the reporter's model/tool evidence without adding
another copy of matched model exchanges. Original private test recordings are not fixtures. The separately approved
[sanitized demo](../examples/orcatest-1/README.md) provides reviewed derivatives
for the [import walkthrough](ARTEX_ORCA_WALKTHROUGH.md), with its transformations
recorded in `SANITIZATION.json`.

## Relationships in Both

Both preserves the Orca collection's inferred cross-session links as well as the
native Artex relationships. It maps a link onto a visible activity only through a
unique validated exchange match or a retained supplemental event. A hidden Orca
tool can use its uniquely recorded tool lifecycle leading to a matched model
exchange as a display anchor. This does not assert equivalence to an Artex tool.
Arrow timing uses the mapped event timestamp, including response timestamps
within paired model activities, relative to the combined clock.

Dashed amber links and chat context cards remain **content-inferred**. The compact
Cross-session context control shows mapped and unresolved counts. Unresolved
links stay inspectable with reasons and do not receive guessed arrows. Selecting
a link opens its original Orca evidence and the separate display-mapping basis.
Show all dashed links can be disabled to retain only links for the selected
activity. Native Artex task/evidence toggles remain independent.

Matched activities and messages in Both carry an **Artex + Orca** badge. This
indicates a validated match between captures, not independent corroboration.
Story and Model telemetry keep their previous projections. Original bundles and
correlation evidence remain unchanged; export/reopen rebuilds Both from them.

### Reading links across lanes

In Both's logical-entity view, telemetry arrows use a recorded owner's lane when
that ownership is unique and the lane is visible. This changes only the vertical
placement: mapped evidence timestamps still determine the horizontal position.
The inspector retains original Orca evidence, exchange mapping and recorded owner
attribution. A lane endpoint is a visual anchor, not proof of delegation.

If ownership is absent, conflicting, or its lane is not visible, the endpoint stays
on the original activity. Source identities mode always uses source activity lanes.
No participant lane or role is invented. In the OrcaTest demo, goal initialization
has no separate visible lane, so those source endpoints remain on the model lane.

Both provides **Links: All / Artex / Telemetry** in the storyline toolbar. The
filter takes precedence over selection and survives timeline zoom and resize.
Amber telemetry routes use stable offsets and endpoint dots to distinguish
connections. Existing Story and Model telemetry controls remain unchanged.
