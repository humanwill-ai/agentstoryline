"""Bounded, evidence-linked correlations for combined Orca recordings.

Correlations are presentation analysis. They never become canonical parents,
dependencies, participant identities, or replay prerequisites.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import re


MIN_ANCHOR_CHARS = 160
MAX_ANCHORS = 512
MAX_REFERENCES = 512
MAX_DEPTH = 10
REFERENCE_KEYS = frozenset({
    "task_id", "intent_id", "finding_id", "finding_node_id", "goal_id",
    "evidence_id", "traffic_id", "source_traffic_id", "step_id", "fact_id",
})


def _dict(value):
    return value if isinstance(value, dict) else {}


def _session(event):
    return _dict(_dict(event.get("data")).get("source_session")).get("id")


def _walk(value, path, *, depth=0):
    """Yield bounded scalar leaves without interpreting opaque source files."""
    if depth > MAX_DEPTH:
        return
    if isinstance(value, dict):
        for key in sorted(value):
            yield from _walk(value[key], path + [str(key)], depth=depth + 1)
    elif isinstance(value, list):
        for index, child in enumerate(value[:1000]):
            yield from _walk(child, path + [str(index)], depth=depth + 1)
    elif isinstance(value, (str, int)) and not isinstance(value, bool):
        yield path, value


def _response_texts(data):
    for index, block in enumerate(_dict(data.get("response")).get("content", [])):
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            yield ["response", "content", str(index), "text"], block["text"]


def _request_texts(data):
    for index, message in enumerate(_dict(data.get("request")).get("messages", [])):
        if not isinstance(message, dict) or message.get("role") == "system":
            continue
        content = message.get("content")
        if isinstance(content, str):
            yield ["request", "messages", str(index), "content"], content
        elif isinstance(content, list):
            for block_index, block in enumerate(content):
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    yield ["request", "messages", str(index), "content", str(block_index), "text"], block["text"]


def _anchor_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_anchors(events):
    anchors = []
    for event in events:
        data = _dict(event.get("data"))
        if event.get("kind") == "tool.start":
            leaves = _walk(data.get("arguments"), ["arguments"])
            origin = "tool_argument"
        elif event.get("kind") == "model.response":
            leaves = _response_texts(data)
            origin = "model_output"
        else:
            continue
        for path, value in leaves:
            if isinstance(value, str):
                text = value.strip()
                if len(text) >= MIN_ANCHOR_CHARS and len(set(text)) >= 12:
                    anchors.append({"event_id": event["id"], "session_id": _session(event),
                                    "path": "/" + "/".join(path), "text": text,
                                    "hash": _anchor_hash(text), "length": len(text), "origin": origin})
                    if len(anchors) >= MAX_ANCHORS:
                        return anchors
    return anchors


def _target_texts(events):
    targets = []
    for event in events:
        data = _dict(event.get("data"))
        if event.get("kind") == "model.request":
            leaves = _request_texts(data)
            target_kind = "request_context"
        elif event.get("kind") == "tool.end":
            leaves = _walk(data.get("result"), ["result"])
            target_kind = "tool_result"
        else:
            continue
        for path, value in leaves:
            if isinstance(value, str) and len(value) >= MIN_ANCHOR_CHARS:
                targets.append({"event_id": event["id"], "session_id": _session(event),
                                "path": "/" + "/".join(path), "text": value,
                                "target_kind": target_kind})
    return targets


def _decoded_json_values(text):
    if not isinstance(text, str) or len(text) > 4 * 1024 * 1024:
        return []
    values = []
    decoder = json.JSONDecoder()
    starts = [0]
    starts.extend(match.start() for match in re.finditer(r"(?m)(?=^[\[{])", text))
    for start in starts[:32]:
        try:
            value, _ = decoder.raw_decode(text[start:].lstrip())
        except (ValueError, RecursionError):
            continue
        if isinstance(value, (dict, list)):
            values.append(value)
    return values


def _typed_references(events):
    grouped = defaultdict(lambda: {"event_ids": [], "session_ids": [], "paths": []})
    for event in events:
        data = _dict(event.get("data"))
        roots = [("arguments", data.get("arguments")), ("result", data.get("result")),
                 ("request", data.get("request")), ("response", data.get("response"))]
        expanded = list(roots)
        for root, value in roots:
            for _, scalar in _walk(value, [root]):
                if isinstance(scalar, str):
                    for index, parsed in enumerate(_decoded_json_values(scalar)):
                        expanded.append((root + ".decoded." + str(index), parsed))
        for root, value in expanded:
            for path, scalar in _walk(value, [root]):
                key = path[-1] if path else ""
                if key not in REFERENCE_KEYS or not isinstance(scalar, (str, int)) or isinstance(scalar, bool):
                    continue
                rendered = str(scalar)
                if not rendered or len(rendered) > 160:
                    continue
                identity = (key, rendered)
                row = grouped[identity]
                if event["id"] not in row["event_ids"]:
                    row["event_ids"].append(event["id"])
                session = _session(event)
                if session and session not in row["session_ids"]:
                    row["session_ids"].append(session)
                location = {"event_id": event["id"], "path": "/" + "/".join(path)}
                if location not in row["paths"] and len(row["paths"]) < 20:
                    row["paths"].append(location)
    result = []
    for (namespace, value), row in sorted(grouped.items()):
        if len(row["session_ids"]) < 2:
            continue
        result.append({"namespace": namespace, "value": value, **row,
                       "provenance": "recorded_payload_reference",
                       "meaning": "Shared typed reference; task membership or causality is not established by the identifier alone."})
        if len(result) >= MAX_REFERENCES:
            break
    return result


def build_orca_correlations(bundle):
    """Return conservative cross-session content matches for an Orca collection."""
    bundle = _dict(bundle)
    source = _dict(_dict(bundle.get("manifest")).get("source"))
    collection = _dict(source.get("collection"))
    empty = {"version": 1, "provenance": "content_inferred", "items": [], "shared_references": [], "notes": []}
    if source.get("adapter") != "orcareplay" or not isinstance(collection.get("sessions"), list):
        return empty
    events = [event for event in bundle.get("events", []) if isinstance(event, dict) and isinstance(event.get("id"), str)]
    anchors, targets = _source_anchors(events), _target_texts(events)
    producers = defaultdict(set)
    for anchor in anchors:
        producers[anchor["hash"]].add(anchor["session_id"])
    matches = []
    for anchor in anchors:
        if len(producers[anchor["hash"]]) != 1:
            continue
        for target in targets:
            if not anchor["session_id"] or not target["session_id"] or anchor["session_id"] == target["session_id"]:
                continue
            if anchor["origin"] == "tool_argument" and target["target_kind"] != "request_context":
                continue
            if anchor["text"] not in target["text"]:
                continue
            relation = "tool_context_reuse" if anchor["origin"] == "tool_argument" else "model_output_reuse"
            matches.append({**anchor, "target_event_id": target["event_id"],
                            "target_session_id": target["session_id"], "target_path": target["path"],
                            "target_kind": target["target_kind"], "relation": relation})
    groups = defaultdict(list)
    for match in matches:
        groups[(match["event_id"], match["target_session_id"], match["relation"])].append(match)
    items = []
    for key, members in sorted(groups.items()):
        source_event_id, target_session_id, relation = key
        members.sort(key=lambda row: (row["target_kind"] != "tool_result", -row["length"], row["target_event_id"], row["target_path"]))
        primary = members[0]
        support = []
        for row in members:
            entry = {"event_id": row["target_event_id"], "path": row["target_path"]}
            if entry not in support:
                support.append(entry)
        digest = hashlib.sha256((source_event_id + "\0" + target_session_id + "\0" + relation).encode()).hexdigest()[:20]
        label = "Possible tool-context transfer" if relation == "tool_context_reuse" else "Possible model-output transfer"
        unique_anchors = {row["hash"]: row for row in members}
        items.append({
            "id": "orca-correlation-" + digest, "kind": relation, "label": label,
            "provenance": "content_inferred", "direction": "candidate_source_to_context",
            "source_event_id": source_event_id, "target_event_id": primary["target_event_id"],
            "source_session_id": primary["session_id"], "target_session_id": target_session_id,
            "matching_method": "exact_substring_of_substantial_recorded_content",
            "anchors": [{"sha256": row["hash"], "length": row["length"], "source_path": row["path"]}
                        for row in unique_anchors.values()],
            "target_evidence": support,
            "limitations": ["No exported parent/source-run identifier links these sessions.",
                            "Matching content supports related context, not delegation or execution causality."],
        })
    return {
        "version": 1, "provenance": "content_inferred", "items": items,
        "shared_references": _typed_references(events),
        "notes": ["Orca native causes remain within each source session.",
                  "Cross-session arrows are derived from substantial exact content reuse and are not recorded dependencies."],
    }
