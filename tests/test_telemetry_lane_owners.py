"""Synthetic recorded ownership for disposable Both correlation endpoints."""
from copy import deepcopy
import unittest

from tests import test_telemetry_correlations
from web.telemetry import _project_correlations


class TelemetryLaneOwnerTests(unittest.TestCase):
    def fixture(self, workers=2, providers=1):
        story, model, attachment = test_telemetry_correlations.TelemetryCorrelationTests().fixture()
        flow = {'entities': [], 'messages': [], 'logical_storyline': {'lanes': [], 'activity_entities': {}}}
        for i in range(workers):
            flow['entities'].append({'id': 'w'+str(i), 'kind': 'worker', 'display_name': 'Worker '+str(i), 'basis': 'recorded_identity'})
            flow['logical_storyline']['lanes'].append({'entity_id': 'w'+str(i)})
        for i in range(providers):
            flow['entities'].append({'id': 'model'+str(i), 'kind': 'model', 'basis': 'recorded_identity'})
        for aid, eid, owner in [('a', 'ar', 'w0'), ('b', 'supp', 'w'+str(workers-1))]:
            flow['messages'].append({'id': 'm'+aid, 'activity_id': aid, 'event_ids': [eid],
                'owner_entity_id': owner, 'entity_basis': 'presentation_grouping_of_recorded_identity'})
        return story, model, attachment, flow

    def test_unique_owners_do_not_depend_on_worker_count_or_model_provider(self):
        for workers, providers in [(1, 1), (2, 1), (5, 3)]:
            story, model, attachment, flow = self.fixture(workers, providers)
            before = deepcopy((model, attachment, flow))
            _project_correlations(story, model, attachment, flow=flow)
            edge = story['edges'][0]
            self.assertEqual(edge['display_source_owner_entity_id'], 'w0')
            self.assertEqual(edge['display_target_owner_entity_id'], 'w'+str(workers-1))
            self.assertEqual(edge['source_activity_id'], 'a')
            self.assertEqual(edge['display_mapping']['source']['lane_owner']['label'], 'Worker 0')
            self.assertEqual(edge['source_ms'], 1)
            self.assertEqual(edge['target_ms'], 2)
            self.assertEqual((model, attachment, flow), before)

    def test_unknown_or_conflicting_ownership_falls_back(self):
        for mode in ('unknown', 'conflict', 'unrecorded'):
            story, model, attachment, flow = self.fixture()
            if mode == 'unknown':
                flow['messages'][0]['owner_entity_id'] = None
            elif mode == 'unrecorded':
                flow['messages'][0]['entity_basis'] = 'inferred'
            else:
                flow['logical_storyline']['activity_entities']['a'] = {
                    'owner_entity_id': 'w1', 'basis': 'recorded_activity_with_presentation_identity'}
            _project_correlations(story, model, attachment, flow=flow)
            edge = story['edges'][0]
            self.assertNotIn('display_source_owner_entity_id', edge)
            self.assertEqual(edge['display_mapping']['source']['lane_owner']['basis'], 'model_lane_fallback')
            self.assertEqual(edge['display_target_owner_entity_id'], 'w1')

    def test_logical_assignment_can_supply_recorded_owner_and_exact_timing_survives(self):
        story, model, attachment, flow = self.fixture()
        flow['messages'][0]['owner_entity_id'] = None
        flow['logical_storyline']['activity_entities']['a'] = {'owner_entity_id': 'w0', 'basis': 'recorded_activity_with_presentation_identity'}
        story['origin_timestamp'] = '2026-01-01T00:00:00Z'
        primary = {'events': [{'id': 'ar', 'timestamp': '2026-01-01T00:00:10.123456Z'}]}
        attachment['bundle']['events'][-1]['timestamp'] = '2026-01-01T00:00:20.654321Z'
        _project_correlations(story, model, attachment, primary, flow)
        self.assertEqual(story['edges'][0]['display_source_owner_entity_id'], 'w0')
        self.assertAlmostEqual(story['edges'][0]['source_ms'], 10123.456)
        self.assertAlmostEqual(story['edges'][0]['target_ms'], 20654.321)

    def test_owner_metadata_survives_absent_visible_lane(self):
        story, model, attachment, flow = self.fixture()
        flow['logical_storyline']['lanes'] = []
        _project_correlations(story, model, attachment, flow=flow)
        owner = story['edges'][0]['display_mapping']['source']['lane_owner']
        self.assertEqual(owner['entity_id'], 'w0')
        self.assertEqual(owner['owner_identity_basis'], 'recorded_identity')

    def test_duplicate_entity_ids_cannot_supply_unique_owner(self):
        story, model, attachment, flow = self.fixture()
        flow['entities'].append({**flow['entities'][0], 'display_name': 'Conflicting identity'})
        _project_correlations(story, model, attachment, flow=flow)
        self.assertNotIn('display_source_owner_entity_id', story['edges'][0])
        self.assertEqual(story['edges'][0]['display_mapping']['source']['lane_owner']['basis'], 'model_lane_fallback')
