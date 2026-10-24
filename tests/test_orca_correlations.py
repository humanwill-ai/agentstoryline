"""Synthetic Orca content-correlation tests; no captured evidence fixtures."""
import copy
import unittest

from analysis.correlations import build_orca_correlations
from web.storyline import build_storyline


LONG_CONTEXT = "The recorded design result contains a stable, detailed explanation " * 5
TOOL_CONTEXT = "The tool received a separate, detailed set of recorded instructions " * 5


def event(identifier, session, kind, data, sequence):
    return {"id": identifier, "run_id": "collection", "sequence": sequence,
            "timestamp": "2026-10-24T10:00:%02dZ" % sequence, "kind": kind,
            "data": {**data, "source_session": {"id": session, "run_id": "run_" + session,
                                                  "run_key": session, "sequence": sequence,
                                                  "event_id": "native.%d" % sequence}}}


class OrcaCorrelationTests(unittest.TestCase):
    def bundle(self):
        events = [
            event("a1", "a", "model.response", {"response": {"content": [{"type": "text", "text": LONG_CONTEXT}]}}, 1),
            event("b1", "b", "tool.end", {"result": {"job_id": 17, "final_text": LONG_CONTEXT}}, 2),
            event("b2", "b", "model.request", {"request": {"messages": [{"role": "system", "content": LONG_CONTEXT},
                {"role": "user", "content": "Prior output:\n" + LONG_CONTEXT}]}}, 3),
            event("a2", "a", "tool.start", {"arguments": {"task_id": 9, "instructions": TOOL_CONTEXT}}, 4),
            event("c1", "c", "model.request", {"request": {"messages": [{"role": "user", "content": "Context " + TOOL_CONTEXT}]}}, 5),
            event("c2", "c", "tool.end", {"result": {"task_id": 9, "message": "done"}}, 6),
        ]
        return {"manifest": {"source": {"adapter": "orcareplay", "collection": {"sessions": [
                    {"id": "a"}, {"id": "b"}, {"id": "c"}]}}}, "events": events}

    def test_exact_content_links_are_inferred_and_collapsed(self):
        bundle = self.bundle()
        before = copy.deepcopy(bundle)
        result = build_orca_correlations(bundle)
        self.assertEqual(bundle, before)
        output = [item for item in result["items"] if item["kind"] == "model_output_reuse"]
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]["target_event_id"], "b1")
        self.assertEqual({item["event_id"] for item in output[0]["target_evidence"]}, {"b1", "b2"})
        context = [item for item in result["items"] if item["kind"] == "tool_context_reuse"]
        self.assertEqual([(item["source_event_id"], item["target_event_id"]) for item in context], [("a2", "c1")])
        self.assertTrue(all(item["provenance"] == "content_inferred" for item in result["items"]))

    def test_shared_ids_are_references_not_arrows(self):
        result = build_orca_correlations(self.bundle())
        task = next(ref for ref in result["shared_references"] if ref["namespace"] == "task_id")
        self.assertEqual(task["value"], "9")
        self.assertEqual(set(task["session_ids"]), {"a", "c"})
        self.assertFalse(any(item["matching_method"] == "matching_identifier" for item in result["items"]))

    def test_static_system_prompt_does_not_become_target(self):
        bundle = self.bundle()
        bundle["events"][2]["data"]["request"]["messages"] = [{"role": "system", "content": LONG_CONTEXT}]
        result = build_orca_correlations(bundle)
        output = next(item for item in result["items"] if item["kind"] == "model_output_reuse")
        self.assertEqual(output["target_evidence"], [{"event_id": "b1", "path": "/result/final_text"}])

    def test_storyline_keeps_correlations_separate_from_dependencies(self):
        bundle = self.bundle()
        story = build_storyline(bundle, {})
        inferred = [edge for edge in story["edges"] if edge["kind"] == "content_correlation"]
        self.assertEqual(len(inferred), 2)
        self.assertTrue(all(edge["provenance"] == "content_inferred" for edge in inferred))
        self.assertFalse(any(edge["kind"] == "dependency" for edge in inferred))

    def test_non_collection_sources_are_unchanged(self):
        bundle = self.bundle()
        bundle["manifest"]["source"] = {"adapter": "orcareplay"}
        self.assertEqual(build_orca_correlations(bundle)["items"], [])


if __name__ == "__main__":
    unittest.main()
