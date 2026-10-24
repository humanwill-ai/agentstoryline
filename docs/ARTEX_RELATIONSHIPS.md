# Recorded Artex relationships

Artex Story now projects explicit native archive relationships into the storyline:

- Task structure from `exploration_edges`, resolved against unique node IDs in the same run and exploration.
- Evidence bindings from `finding_traffic_bindings`, resolved against unique finding and traffic snapshot IDs in the same run.

The compact relationship strip lists endpoints and recorded relation labels. Task arrows are solid teal; evidence arrows are dotted lavender. Each category can be hidden. Selecting an arrow or chip highlights its endpoints and opens the source rows. Collapsed activities do not draw self-arrows; their relationships remain inspectable in the strip.

These relationships describe recorded task structure and evidence binding. They do not establish causal delegation, execution order, or runtime input/output dependencies. Generic parent/history references are not drawn as task links. Missing or ambiguous endpoints are omitted with notes in the projection.

For `proves` only, an arrow may use a uniquely scoped `report_finding` result and/or `prove_goal` invocation as display anchors. This requires explicit matching node/finding IDs and preserves the native graph endpoints, source evidence, and anchor basis in details. Temporal proximity and similar text never create these anchors.

The derivation applies only to the Artex adapter. It is rebuilt when importing or refreshing saved recordings. Story and Both retain the native relationships; Model telemetry keeps its independent Orca projection. This does not implement remapping of Orca inferred cross-session links into Both.

Canonical source events, dependencies, evidence and timestamps are unchanged. No model calls or execution are required. Exports carry the original evidence and can rebuild this display after reopening.
