"""Evidence-linked replay overlays. Rendering never resolves or executes inputs."""
import copy

from replay.runner import ReplayValidationError, validate_plan


def _bindings(value, path):
    if isinstance(value, dict):
        if "$ref" in value:
            yield path, value["$ref"]
        else:
            for key, item in value.items():
                yield from _bindings(item, path + "/" + key.replace("~", "~0").replace("/", "~1"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _bindings(item, path + "/" + str(index))


def build_replay_view(bundle, plan, storyline):
    """Validate a plan's citations and map explicit variable bindings to activities.

    A binding is plan intent, never evidence of historical causality. Ambiguous
    citations stay unmapped, with all cited IDs retained for inspection.
    """
    plan = validate_plan(plan)
    if plan["run_id"] != bundle["manifest"].get("run_id"):
        raise ReplayValidationError("replay run_id does not match the evidence bundle")
    events = {event["id"]: event for event in bundle["events"]}
    steps = plan["steps"]
    for step in steps:
        refs = step.get("evidence_refs", [])
        if not refs or any(ref not in events for ref in refs):
            raise ReplayValidationError("replay steps must cite real evidence_refs")
        recorded = {dependency["event_id"] for ref in refs
                    for dependency in events[ref].get("dependencies", [])
                    if isinstance(dependency, dict) and dependency.get("provenance") == "recorded" and "event_id" in dependency}
        seen = set()
        for dependency in step.get("dependencies", []):
            event_id = dependency["event_id"]
            if event_id not in events or event_id in seen:
                raise ReplayValidationError("replay dependency must cite a unique real event ID")
            seen.add(event_id)
            if dependency["provenance"] == "recorded" and event_id not in recorded:
                raise ReplayValidationError("replay dependency claims recorded provenance without recorded evidence")
    notes = ["Replay bindings describe planned input flow, not recorded causality. Viewing never executes requests or substitutes historical values."]
    mapping = {}
    for step in steps:
        refs = step["evidence_refs"]
        candidates = {activity["id"] for activity in storyline.get("activities", [])
                      if set(refs).intersection(activity.get("event_ids", []))}
        activity_id = next(iter(candidates)) if len(candidates) == 1 else None
        mapping[step["id"]] = activity_id
        if activity_id is None:
            notes.append(f"Step {step['id']} has {'ambiguous' if candidates else 'unmapped'} activity citations; inspect evidence_refs.")
    by_id = {step["id"]: step for step in steps}
    edges = []
    display_steps = []
    for step in steps:
        display = copy.deepcopy(step)
        display["activity_id"] = mapping[step["id"]]
        display["bindings"] = []
        for field in ("url", "headers", "body", "preconditions"):
            for path, reference in _bindings(step.get(field), "/" + field):
                source_id = reference.split(".", 1)[0]
                source = by_id[source_id]
                provenance = "inferred" if step.get("provenance") == "inferred" or source.get("provenance") == "inferred" else "authored"
                edge = {"id": f"replay-binding-{len(edges) + 1}",
                        "source_activity_id": mapping[source_id], "target_activity_id": mapping[step["id"]],
                        "source_event_id": source["evidence_refs"][0] if len(source["evidence_refs"]) == 1 else None,
                        "target_event_id": step["evidence_refs"][0] if len(step["evidence_refs"]) == 1 else None,
                        "source_evidence_refs": list(source["evidence_refs"]), "target_evidence_refs": list(step["evidence_refs"]),
                        "kind": "replay_binding", "provenance": provenance,
                        "label": f"{reference} → {path}", "reference": reference, "input_path": path,
                        "source_step": source_id, "target_step": step["id"]}
                edges.append(edge)
                display["bindings"].append({"reference": reference, "input_path": path})
        display_steps.append(display)
    return {"run_id": plan["run_id"], "steps": display_steps, "edges": edges, "notes": notes,
            "allowed_origins": list(plan["allowed_origins"]), "execution": "not_requested"}
