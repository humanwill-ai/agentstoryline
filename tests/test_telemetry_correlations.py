"""Synthetic display mapping tests; no source run evidence is retained."""
from copy import deepcopy
import unittest

from web.telemetry import _project_correlations


class TelemetryCorrelationTests(unittest.TestCase):
    def fixture(self):
        def event(eid, kind, seq, causes=()):
            return {'id': eid, 'kind': kind, 'data': {'source_session': {'id': 's'},
                    'source_record': {'seq': seq, 'causes': list(causes)}}}
        attachment = {'matches': [{'orca_request_id': 'oq', 'orca_response_id': 'or',
            'artex_request_id': 'aq', 'artex_response_id': 'ar', 'basis': 'exact_recorded_content',
            'evidence': {'equal_fields': ['body']}}], 'bundle': {'events': [
                event('oq', 'model.request', 1), event('or', 'model.response', 2, [1]),
                event('ot', 'tool.start', 3, [2]), event('oe', 'tool.end', 4, [3]),
                event('supp', 'model.request', 5)]}}
        item = {'id': 'corr', 'source_event_id': 'oe', 'target_event_id': 'supp',
                'label': 'Possible context reuse', 'provenance': 'content_inferred',
                'matching_method': 'exact_content', 'limitations': ['No execution causality'],
                'anchors': [{'sha256': 'synthetic'}]}
        model = {'storyline': {'correlations': {'items': [item], 'sessions': [{'id': 's'}]}}}
        story = {'activities': [{'id': 'a', 'event_ids': ['aq', 'ar'], 'start_ms': 1},
                                {'id': 'b', 'event_ids': ['supp'], 'start_ms': 2}], 'edges': []}
        return story, model, attachment

    def test_lifecycle_anchor_preserves_evidence_and_inputs(self):
        story, model, attachment = self.fixture()
        originals = deepcopy((model, attachment))
        _project_correlations(story, model, attachment)
        item = story['telemetry_correlations']['items'][0]
        self.assertEqual(item['display_status'], 'mapped')
        self.assertEqual(item['display_source_event_id'], 'ar')
        self.assertEqual(item['source_event_id'], 'oe')
        self.assertEqual(item['display_mapping']['source']['lifecycle_event_ids'], ['oe', 'ot', 'or'])
        self.assertEqual(item['display_mapping']['target']['basis'], 'retained_supplemental')
        self.assertEqual(story['edges'][0]['source_activity_id'], 'a')
        self.assertEqual((model, attachment), originals)
        previous = deepcopy(story)
        _project_correlations(story, model, attachment)
        self.assertEqual(story, previous)

    def test_attached_payload_roundtrip_preserves_story_model_and_sources(self):
        import json
        from analysis.telemetry import build_telemetry_attachment
        from tests.test_explanation_workflow import synthetic_bundle
        from web.render import build_payload
        from web.telemetry import build_telemetry_views
        primary = synthetic_bundle()
        orca = deepcopy(primary)
        orca['manifest']['run_id'] = 'synthetic-orca'
        orca['events'][0].update(id='orca-observation', run_id='synthetic-orca')
        payload = build_payload(primary, {})
        payload['telemetry'] = build_telemetry_attachment(primary, orca)
        original = deepcopy(payload)
        combined = build_telemetry_views(payload)
        self.assertEqual(payload, original)
        self.assertEqual(combined['storyline'], original['storyline'])
        self.assertEqual(combined['bundle'], original['bundle'])
        self.assertEqual(combined['telemetry'], original['telemetry'])
        rebuilt = build_telemetry_views(json.loads(json.dumps(combined)))
        self.assertEqual(rebuilt, combined)
        self.assertEqual(rebuilt['telemetry_views']['model'], combined['telemetry_views']['model'])

    def test_response_anchor_uses_response_timestamp(self):
        story, model, attachment = self.fixture()
        story['origin_timestamp'] = '2026-01-01T00:00:00Z'
        primary = {'events': [
            {'id': 'aq', 'timestamp': '2026-01-01T00:00:00Z'},
            {'id': 'ar', 'timestamp': '2026-01-01T00:00:10Z'}]}
        attachment['bundle']['events'][-1]['timestamp'] = '2026-01-01T00:00:20Z'
        _project_correlations(story, model, attachment, primary)
        self.assertEqual(story['edges'][0]['source_ms'], 10000)
        self.assertEqual(story['edges'][0]['target_ms'], 20000)

    def test_ambiguous_explicit_parents_remain_unresolved(self):
        story, model, attachment = self.fixture()
        attachment['bundle']['events'][2]['data']['source_record']['causes'] = [1, 2]
        _project_correlations(story, model, attachment)
        self.assertEqual(story['telemetry_correlations']['summary'], {'mapped': 0, 'unresolved': 1})
        self.assertIn('uniquely matched', story['telemetry_correlations']['items'][0]['display_mapping']['reason'])
        self.assertEqual(story['edges'], [])

    def test_missing_or_ambiguous_exchange_does_not_guess(self):
        for ambiguous in (False, True):
            story, model, attachment = self.fixture()
            attachment['matches'] = attachment['matches'] * 2 if ambiguous else []
            _project_correlations(story, model, attachment)
            self.assertEqual(story['telemetry_correlations']['items'][0]['display_status'], 'unresolved')
            self.assertEqual(story['edges'], [])

    def test_inferred_dependency_cannot_supply_explicit_display_anchor(self):
        story, model, attachment = self.fixture()
        tool = attachment['bundle']['events'][2]
        tool['data']['source_record']['causes'] = []
        tool['dependencies'] = [{'event_id': 'or', 'provenance': 'inferred'}]
        _project_correlations(story, model, attachment)
        self.assertEqual(story['telemetry_correlations']['items'][0]['display_status'], 'unresolved')
        self.assertEqual(story['edges'], [])

    def test_retained_correlation_edge_is_replaced_once(self):
        story, model, attachment = self.fixture()
        model['storyline']['correlations']['items'][0]['source_event_id'] = 'supp'
        story['edges'] = [{'correlation_id': 'corr', 'id': 'old'}]
        _project_correlations(story, model, attachment)
        self.assertEqual(len(story['edges']), 1)
        self.assertEqual(story['edges'][0]['correlation_id'], 'corr')


if __name__ == '__main__':
    unittest.main()
