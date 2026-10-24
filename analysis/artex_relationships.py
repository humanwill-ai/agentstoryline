"""Native Artex graph rows, projected without causal or execution inference.

Only explicit database edges and finding/snapshot bindings are considered. Source
rows, event dependencies, and timestamps remain untouched.
"""
from collections import Counter, defaultdict
import hashlib
import json
import re
from urllib.parse import urlsplit


def _identity(value):
    # Database JSON identifiers may be strings or integers, never booleans.
    if isinstance(value, bool) or not isinstance(value, (str, int)) or value == "":
        return None
    return str(value)


def build_artex_relationships(bundle):
    """Return versioned presentation relationships with exact source witnesses."""
    result = {"version": 1, "items": [], "notes": [], "node_labels": {}}
    if bundle.get("manifest", {}).get("source", {}).get("adapter") != "artex":
        return result
    events = [e for e in bundle.get("events", []) if isinstance(e, dict)]
    counts = Counter(e.get("id") for e in events if isinstance(e.get("id"), str))
    tables = defaultdict(list)
    for event in events:
        data = event.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("source_record"), dict):
            continue
        if counts.get(event.get("id")) != 1 or _identity(event.get("run_id")) is None:
            continue
        tables[data.get("source_table")].append((event, data["source_record"]))

    def index(table, scoped=False):
        lookup = defaultdict(list)
        for event, row in tables[table]:
            key = (_identity(event["run_id"]), _identity(row.get("id")))
            if scoped:
                key += (_identity(row.get("exploration_id")),)
            if None not in key:
                lookup[key].append((event, row))
        return lookup

    nodes = index("exploration_nodes", True)
    findings = index("findings")
    snapshots = index("traffic_evidence_snapshots")

    def label(event, row):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if isinstance(row.get("url"), str):
            try:
                path = urlsplit(row["url"]).path or "/"
            except ValueError:
                path = "[invalid URL]"
            return " ".join(str(value) for value in (row.get("method"), path, row.get("status")) if value is not None)[:180]
        text = next((value for value in (row.get("name"), payload.get("name"), payload.get("summary"), payload.get("goal"),
                                        row.get("url"), row.get("kind")) if isinstance(value, str) and value.strip()), event["id"])
        return text[:180]

    def add(kind, witness, row, source, target, relation, basis):
        source_event, source_row = source
        target_event, target_row = target
        digest = hashlib.sha256(json.dumps([kind, witness["id"], source_event["id"], target_event["id"]],
                                           separators=(",", ":")).encode()).hexdigest()[:24]
        item = {"id": "artex.relationship." + digest, "kind": kind,
                "source_event_id": source_event["id"], "target_event_id": target_event["id"],
                "label": relation, "provenance": "source_recorded",
                "evidence_event_ids": [witness["id"], source_event["id"], target_event["id"]],
                "basis": basis,
                "limitations": ["Recorded database relationship; does not establish causal delegation, timing, or runtime dataflow."],
                "source_label": label(source_event, source_row), "target_label": label(target_event, target_row)}
        result["items"].append(item)
        for event, node in (source, target):
            result["node_labels"][event["id"]] = label(event, node)

    def resolve(lookup, key):
        candidates = lookup.get(key, []) if None not in key else []
        return candidates[0] if len(candidates) == 1 else None

    skipped = 0
    for event, row in tables["exploration_edges"]:
        scope = _identity(row.get("exploration_id"))
        run = _identity(event["run_id"])
        source = resolve(nodes, (run, _identity(row.get("src_id")), scope))
        target = resolve(nodes, (run, _identity(row.get("dst_id")), scope))
        relation = row.get("rel")
        if not source or not target or not isinstance(relation, str) or not relation.strip():
            skipped += 1
            continue
        add("task_structure", event, row, source, target, relation,
            {"source_table": "exploration_edges", "exploration_id": row["exploration_id"],
             "src_id": row["src_id"], "dst_id": row["dst_id"], "rel": relation,
             "endpoint_basis": "unique_run_and_exploration_scoped_node_ids"})
    for event, row in tables["finding_traffic_bindings"]:
        run = _identity(event["run_id"])
        source = resolve(findings, (run, _identity(row.get("finding_id"))))
        target = resolve(snapshots, (run, _identity(row.get("snapshot_id"))))
        if not source or not target:
            skipped += 1
            continue
        role = row.get("role")
        add("evidence_binding", event, row, source, target,
            "evidence: " + role if isinstance(role, str) and role.strip() else "evidence binding",
            {"source_table": "finding_traffic_bindings", "finding_id": row["finding_id"],
             "snapshot_id": row["snapshot_id"], "role": role,
             "endpoint_basis": "unique_run_scoped_finding_and_snapshot_ids"})
    # Optional display anchors preserve the native node endpoints above. They
    # identify explicit tool records, never an inferred execution dependency.
    activities = tables["activity"]

    def usable(event, row, kind, tool):
        data = event["data"]
        value = data.get("result")
        return (event.get("kind") == kind and event.get("tool", row.get("tool")) == tool
                and not any(value for value in (event.get("is_error"), row.get("is_error"), data.get("is_error")))
                and not (isinstance(value, dict) and value.get("is_error")))

    def report_result(value, node_id):
        if isinstance(value, dict):
            return value
        if not isinstance(value, str):
            return None
        match = re.fullmatch(r"finding recorded: ([0-9]+)\n(.+)", value, re.DOTALL)
        if not match or _identity(match[1]) != node_id:
            return None
        try:
            parsed = json.loads(match[2])
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    for item in result["items"]:
        basis = item["basis"]
        if item["kind"] != "task_structure" or basis["rel"] != "proves":
            continue
        edge = next(e for e in events if e.get("id") == item["evidence_event_ids"][0])
        run = _identity(edge["run_id"])
        scope = _identity(basis["exploration_id"])
        src, dst = _identity(basis["src_id"]), _identity(basis["dst_id"])
        targets = []
        sources = []
        for event, row in activities:
            if _identity(event["run_id"]) != run:
                continue
            data = event["data"]
            if usable(event, row, "tool.start", "prove_goal") and _identity(row.get("exploration_id")) == scope:
                args = data.get("arguments")
                if isinstance(args, dict) and (_identity(args.get("evidence_id")), _identity(args.get("goal_id"))) == (src, dst):
                    targets.append(event)
            if not usable(event, row, "tool.end", "report_finding"):
                continue
            parsed = report_result(data.get("result"), src)
            if not parsed or _identity(parsed.get("finding_node_id")) != src or parsed.get("is_error"):
                continue
            finding = resolve(findings, (run, _identity(parsed.get("finding_id"))))
            if not finding or _identity(finding[1].get("node_id")) != src:
                continue
            witnesses = [finding[0]["id"]]
            recorded_scope = _identity(row.get("exploration_id"))
            if recorded_scope is not None:
                if recorded_scope != scope:
                    continue
            else:
                call_id = _identity(event.get("call_id"))
                paired = [(start, start_row) for start, start_row in activities
                          if call_id is not None and _identity(start.get("call_id")) == call_id
                          and _identity(start["run_id"]) == run
                          and usable(start, start_row, "tool.start", "report_finding")]
                if len(paired) != 1 or _identity(paired[0][1].get("exploration_id")) != scope:
                    continue
                witnesses.append(paired[0][0]["id"])
            sources.append((event, witnesses))
        anchor_basis = {}
        if len(targets) == 1:
            item["display_target_event_id"] = targets[0]["id"]
            item["evidence_event_ids"].append(targets[0]["id"])
            anchor_basis["target"] = "unique_scoped_prove_goal_arguments_evidence_id_and_goal_id"
        if len(sources) == 1:
            source, witnesses = sources[0]
            item["display_source_event_id"] = source["id"]
            item["evidence_event_ids"].extend([source["id"], *witnesses])
            anchor_basis["source"] = "unique_scoped_report_finding_result_and_matching_finding_node_id"
        if anchor_basis:
            item["display_anchor_basis"] = anchor_basis
            item["evidence_event_ids"] = list(dict.fromkeys(item["evidence_event_ids"]))

    result["notes"] = ["Artex native relationships preserve recorded direction and relation labels; endpoints are source row observations.",
                       "History text, temporal proximity, shared actors, and request/response contents are not relationship evidence."]
    if skipped:
        result["notes"].append(f"Omitted {skipped} native relationship rows with missing or ambiguous endpoints or relation labels.")
    return result
