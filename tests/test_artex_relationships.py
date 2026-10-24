"""New synthetic tests for source-recorded Artex relationships."""
import copy
import unittest

from analysis.artex_relationships import build_artex_relationships


def event(event_id, table, **row):
    return {"id": event_id, "run_id": "synthetic-run", "data": {"source_table": table, "source_record": row}}


def fixture():
    return {"manifest": {"source": {"adapter": "artex"}}, "events": [
        event("goal", "exploration_nodes", id=1, exploration_id=7, kind="fact", payload={"summary": "Synthetic goal"}),
        event("intent", "exploration_nodes", id=2, exploration_id=7, kind="intent"),
        event("edge", "exploration_edges", exploration_id=7, src_id=1, dst_id=2, rel="derived_from"),
        event("finding", "findings", id=3, name="Synthetic finding"),
        event("snapshot", "traffic_evidence_snapshots", id="snap", method="GET", url="http://localhost/example", status=200),
        event("binding", "finding_traffic_bindings", finding_id=3, snapshot_id="snap", role="baseline"),
    ]}


class ArtexRelationshipTests(unittest.TestCase):
    def test_native_direction_witnesses_and_source_preservation(self):
        bundle = fixture()
        before = copy.deepcopy(bundle)
        result = build_artex_relationships(bundle)
        self.assertEqual(bundle, before)
        self.assertEqual(result, build_artex_relationships(bundle))
        self.assertEqual(len(result["items"]), 2)
        edge, binding = result["items"]
        self.assertEqual((edge["source_event_id"], edge["target_event_id"], edge["label"]), ("goal", "intent", "derived_from"))
        self.assertEqual(edge["evidence_event_ids"], ["edge", "goal", "intent"])
        self.assertEqual(edge["provenance"], "source_recorded")
        self.assertEqual(binding["kind"], "evidence_binding")
        self.assertEqual(binding["target_label"], "GET /example 200")
        self.assertEqual(result["node_labels"]["goal"], "Synthetic goal")
        self.assertEqual((binding["source_event_id"], binding["target_event_id"]), ("finding", "snapshot"))

    def test_other_adapters_are_inert(self):
        for adapter in ("orca", "codex", None):
            bundle = fixture()
            bundle["manifest"]["source"]["adapter"] = adapter
            self.assertEqual(build_artex_relationships(bundle)["items"], [])

    def test_scope_prevents_cross_exploration_and_run_matches(self):
        bundle = fixture()
        bundle["events"].append(event("other-goal", "exploration_nodes", id=1, exploration_id=8))
        self.assertEqual(len(build_artex_relationships(bundle)["items"]), 2)
        bundle["events"][1]["run_id"] = "other-run"
        self.assertEqual(len(build_artex_relationships(bundle)["items"]), 1)

    def test_conflicting_scoped_ids_are_omitted(self):
        for table, row in (("exploration_nodes", {"id": 1, "exploration_id": 7}),
                           ("findings", {"id": 3}), ("traffic_evidence_snapshots", {"id": "snap"})):
            bundle = fixture()
            bundle["events"].append(event("conflict", table, **row))
            result = build_artex_relationships(bundle)
            self.assertEqual(len(result["items"]), 1)
            self.assertIn("ambiguous", result["notes"][-1])

    def test_missing_refs_and_boolean_ids_do_not_match(self):
        bundle = fixture()
        bundle["events"][2]["data"]["source_record"]["src_id"] = True
        bundle["events"] = [e for e in bundle["events"] if e["id"] != "snapshot"]
        self.assertEqual(build_artex_relationships(bundle)["items"], [])

    def test_duplicate_event_identity_and_history_do_not_create_edges(self):
        bundle = fixture()
        bundle["events"].append(copy.deepcopy(bundle["events"][0]))
        bundle["events"].append(event("history", "history", id=99, finding_id=3, src_id=1, dst_id=2))
        self.assertEqual(len(build_artex_relationships(bundle)["items"]), 1)


def anchored_fixture():
    bundle = fixture()
    bundle["events"][2]["data"]["source_record"]["rel"] = "proves"
    bundle["events"][3]["data"]["source_record"]["node_id"] = 1
    for event_id, kind, tool, scope, content in (
        ("report-start", "tool.start", "report_finding", 7, {"arguments": {"intent_id": 9}}),
        ("report-end", "tool.end", "report_finding", 7, {"result": 'finding recorded: 1\n{"finding_id":"3","finding_node_id":"1"}'}),
        ("prove-start", "tool.start", "prove_goal", 7, {"arguments": {"evidence_id": 1, "goal_id": 2}}),
    ):
        item = event(event_id, "activity", exploration_id=scope, tool=tool, is_error=False)
        item.update(kind=kind, tool=tool, call_id="report-call" if tool == "report_finding" else "prove-call")
        item["data"].update(content)
        bundle["events"].append(item)
    return bundle


class ArtexDisplayAnchorTests(unittest.TestCase):
    def relationship(self, bundle):
        return build_artex_relationships(bundle)["items"][0]

    def test_explicit_anchors_preserve_native_endpoints_and_counts(self):
        bundle = anchored_fixture()
        before = copy.deepcopy(bundle)
        result = build_artex_relationships(bundle)
        item = result["items"][0]
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual((item["source_event_id"], item["target_event_id"]), ("goal", "intent"))
        self.assertEqual((item["display_source_event_id"], item["display_target_event_id"]), ("report-end", "prove-start"))
        self.assertTrue({"report-end", "finding", "prove-start"}.issubset(item["evidence_event_ids"]))
        self.assertEqual(bundle, before)

    def test_only_proves_is_anchored(self):
        bundle = anchored_fixture()
        bundle["events"][2]["data"]["source_record"]["rel"] = "yields"
        self.assertNotIn("display_anchor_basis", self.relationship(bundle))

    def test_duplicate_candidates_fall_back_independently(self):
        for position, field in ((7, "display_source_event_id"), (8, "display_target_event_id")):
            bundle = anchored_fixture()
            duplicate = copy.deepcopy(bundle["events"][position])
            duplicate["id"] += "-duplicate"
            bundle["events"].append(duplicate)
            self.assertNotIn(field, self.relationship(bundle))

    def test_wrong_scope_and_errors_are_not_anchors(self):
        for position, field in ((7, "display_source_event_id"), (8, "display_target_event_id")):
            for mutation in ("scope", "error", "run"):
                bundle = anchored_fixture()
                candidate = bundle["events"][position]
                if mutation == "scope":
                    candidate["data"]["source_record"]["exploration_id"] = 8
                elif mutation == "error":
                    candidate["data"]["source_record"]["is_error"] = True
                else:
                    candidate["run_id"] = "other-run"
                self.assertNotIn(field, self.relationship(bundle))

    def test_dict_result_and_uniquely_paired_start_scope(self):
        bundle = anchored_fixture()
        report = bundle["events"][7]
        report["data"]["result"] = {"finding_id": 3, "finding_node_id": 1}
        del report["data"]["source_record"]["exploration_id"]
        self.assertEqual(self.relationship(bundle)["display_source_event_id"], "report-end")
        self.assertIn("report-start", self.relationship(bundle)["evidence_event_ids"])
        duplicate = copy.deepcopy(bundle["events"][6])
        duplicate["id"] = "ambiguous-start"
        bundle["events"].append(duplicate)
        self.assertNotIn("display_source_event_id", self.relationship(bundle))

    def test_result_prefix_mapping_and_arguments_must_agree(self):
        bad_results = ["finding recorded: 9\n{\"finding_id\":3,\"finding_node_id\":1}",
                       '{"finding_id":3,"finding_node_id":1}',
                       {"finding_id": 99, "finding_node_id": 1},
                       {"finding_id": 3, "finding_node_id": 2},
                       {"finding_id": 3, "finding_node_id": 1, "is_error": True}]
        for value in bad_results:
            bundle = anchored_fixture()
            bundle["events"][7]["data"]["result"] = value
            self.assertNotIn("display_source_event_id", self.relationship(bundle))
        bundle = anchored_fixture()
        bundle["events"][3]["data"]["source_record"]["node_id"] = 99
        self.assertNotIn("display_source_event_id", self.relationship(bundle))
        bundle = anchored_fixture()
        bundle["events"][8]["data"]["arguments"]["goal_id"] = 99
        self.assertNotIn("display_target_event_id", self.relationship(bundle))
