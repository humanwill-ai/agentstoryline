# Storyline viewer

The first delivery presents recorded activities in timing lanes, evidence-linked details, optional replay-plan binding arrows, and historical playback. Playback advances the recorded view; it does not run tools, make HTTP requests, or execute a replay plan.

The default `timeline` output is now the chat/storyline flow viewer described in the [release scope](RELEASE_SCOPE_V0_1.md). Render the technical storyline described here with `--view evidence`:

```sh
python3 agentstoryline.py timeline /path/to/bundle /path/to/timeline.html --view evidence
python3 agentstoryline.py timeline /path/to/bundle /path/to/timeline.html --view evidence --analysis /path/to/analysis.json --plan /path/to/plan.json
```

Open the resulting self-contained HTML file in a browser. Analysis and plan inputs are optional. The offline workflow report includes its validated replay plan automatically.

Timing lanes use available recorded timestamps. Partial recordings, missing timestamps, and inconsistent clocks remain explicit limitations; an inferred layout or missing endpoint is not proof of actual duration, overlap, ordering, or successful completion. Inspect cited evidence when interpreting an activity or timing relationship.

Rows group explicit source identities and activity mechanisms. Additional rows accommodate overlap and readable marker labels; they do not invent additional workers. Very short calls use labeled markers, and persistence-only or conflicting boundaries use observation endpoints instead of a filled execution interval. Chapter spans cover cited event timestamps, not necessarily continuous phases. Colors describe model, tool, observation, and run records; they do not automatically classify an action as an attack.

Select an activity to inspect its original events and relevant dependency arrows. The diagram anchors recorded dependencies at the cited event timestamps. Parent/evidence associations and relationships within a paired activity remain available in the inspector and relationship list. Use the playback slider, speed control, zoom menu, and timeline scrollbar to navigate the recording. The event list retains search and kind filtering.

Replay binding arrows describe explicit `$ref` inputs and template bindings in the attached plan. They are labeled as inferred or authored plan intent, separately from recorded causality. Inspect a step to see its exact template, extraction contract, input path, and evidence citations. Historical token values are never substituted into those bindings. Plans with ambiguous activity citations retain the event IDs and an explanatory note instead of choosing an arbitrary activity endpoint.

The separate replay-plan flow remains inspectable when a binding cannot be placed uniquely on the historical timeline. For example, a target step citing both its prerequisite response and its own request/result has multiple supporting activities. The viewer shows the procedural connection and all citations without assigning an unsupported historical endpoint.

Plan display validates the existing replay schema, exact loopback scope rules, matching run ID, and real event citations for every step and dependency. A dependency labeled recorded must be recorded by a cited event. This establishes structural and citation consistency; it does not prove that an authored or inferred plan faithfully reproduces the recording. Viewing grants no execution authorization and widens no operator scope.

## Developer contract

`web.replay_view.build_replay_view(bundle, plan, storyline)` accepts a validated recording and the storyline's `activities` entries containing `id` and `event_ids`. It validates the plan and returns detached display `steps`, `edges`, `notes`, `allowed_origins`, `run_id`, and `execution: not_requested`. The renderer adds overlay edges to the storyline's edges while preserving their `kind: replay_binding` distinction.

Each binding edge includes source/target step IDs, activity IDs and event IDs (nullable for ambiguous mappings), all source/target evidence references, exact `reference`, JSON Pointer `input_path`, label, and provenance (`inferred` or `authored`). Template inputs retain their template and bindings; extraction metadata remains in the source display step. A recorded step declaration cannot promote a replay variable binding into recorded causality.

New synthetic regressions in `tests/test_replay_view.py` cover scope and run-ID rejection, unknown/forward references, citation requirements, exact recorded dependencies, provenance, preserved templates, and ambiguous endpoints. These tests are authored for this delivery and require no server, model call, or real evidence. Actual execution controls, execution progress, and server integration belong to a later milestone.
