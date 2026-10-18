"""Synthetic tests for storyline display compaction; no captured evidence."""

import copy
import unittest

from web.time_axis import build_story_axis
from web.render import build_payload


def activity(aid, start, end=None, timing='instant', events=()):
    return {'id': aid, 'start_ms': start, 'end_ms': start if end is None else end,
            'timing': timing, 'event_ids': list(events)}


class StoryAxisTests(unittest.TestCase):
    def test_flow_payload_carries_additive_axis(self):
        bundle = {'manifest': {'run_id': 'synthetic-axis',
                               'started_at': '2026-01-01T00:00:00Z',
                               'agent': {'name': 'Synthetic'},
                               'completeness': {'status': 'complete', 'gaps': []}},
                  'events': [
                      {'id': 'early', 'kind': 'observation', 'sequence': 0,
                       'timestamp': '2026-01-01T00:00:00Z', 'data': {'content': 'early'}},
                      {'id': 'late', 'kind': 'observation', 'sequence': 1,
                       'timestamp': '2026-01-01T00:00:40Z', 'data': {'content': 'late'}},
                  ]}
        payload = build_payload(bundle, {})
        axis = payload['flow']['story_axis']
        self.assertEqual(axis['mode'], 'condensed_idle')
        self.assertEqual(axis['recorded_duration_ms'], 40000)
        self.assertEqual(axis['display_duration_ms'], 5000)
        self.assertEqual([segment['kind'] for segment in axis['segments']], ['idle_break'])
        self.assertEqual(payload['bundle'], bundle)

    def test_long_idle_gap_is_additive_and_preserves_source(self):
        storyline = {'duration_ms': 390051.329, 'activities': [
            activity('before', 156627.787, events=('before-event',)),
            activity('after', 367653.297, events=('after-event',)),
            activity('finish', 390051.329, events=('finish-event',)),
        ]}
        flow = {'duration_ms': 390051.329, 'time_mode': 'recorded',
                'messages': [{'recorded_offset_ms': 156627.787}]}
        before = copy.deepcopy((storyline, flow))

        axis = build_story_axis(storyline, flow)

        self.assertEqual((storyline, flow), before)
        self.assertEqual(axis['mode'], 'condensed_idle')
        self.assertEqual(axis['recorded_duration_ms'], 390051.329)
        self.assertAlmostEqual(axis['display_duration_ms'], 184025.819)
        gap = next(segment for segment in axis['segments']
                   if segment['kind'] == 'idle_break')
        self.assertEqual((gap['recorded_start_ms'], gap['recorded_end_ms']),
                         (156627.787, 367653.297))
        self.assertEqual(gap['display_end_ms'] - gap['display_start_ms'], 5000)
        self.assertAlmostEqual(gap['omitted_ms'], 206025.51)
        self.assertEqual(gap['boundary_activity_ids'], ['before', 'after'])
        self.assertEqual(gap['boundary_event_ids'], ['before-event', 'after-event'])
        self.assertEqual(gap['label'], '03:31 with no recorded activity · condensed')
        for left, right in zip(axis['segments'], axis['segments'][1:]):
            self.assertEqual(left['recorded_end_ms'], right['recorded_start_ms'])
            self.assertEqual(left['display_end_ms'], right['display_start_ms'])

    def test_threshold_and_multiple_gaps_are_deterministic(self):
        storyline = {'duration_ms': 130000, 'activities': [
            activity('a', 0), activity('b', 30000),
            activity('c', 70001), activity('d', 130000),
        ]}
        axis = build_story_axis(storyline, {'duration_ms': 130000, 'time_mode': 'recorded'})
        breaks = [segment for segment in axis['segments'] if segment['kind'] == 'idle_break']
        self.assertEqual([(item['recorded_start_ms'], item['recorded_end_ms']) for item in breaks],
                         [(0, 30000), (30000, 70001), (70001, 130000)])
        self.assertEqual(axis['display_duration_ms'], 15000)
        self.assertEqual(axis, build_story_axis(storyline, {'duration_ms': 130000, 'time_mode': 'recorded'}))

    def test_reliable_interval_prevents_false_idle_break(self):
        storyline = {'duration_ms': 100000, 'activities': [
            activity('start', 0),
            activity('busy', 10000, 90000, 'interval'),
            activity('end', 100000),
        ]}
        axis = build_story_axis(storyline, {'duration_ms': 100000, 'time_mode': 'recorded'})
        self.assertEqual(axis['mode'], 'recorded')
        self.assertEqual(axis['segments'], [{
            'kind': 'recorded', 'recorded_start_ms': 0,
            'recorded_end_ms': 100000, 'display_start_ms': 0,
            'display_end_ms': 100000,
        }])

    def test_incomplete_endpoints_do_not_invent_occupied_interval(self):
        storyline = {'duration_ms': 100000, 'activities': [
            activity('uncertain', 10000, 90000, 'incomplete', ('u',)),
        ]}
        axis = build_story_axis(storyline, {'duration_ms': 100000, 'time_mode': 'recorded'})
        gap = next(segment for segment in axis['segments'] if segment['kind'] == 'idle_break')
        self.assertEqual((gap['recorded_start_ms'], gap['recorded_end_ms']), (10000, 90000))
        self.assertEqual(gap['boundary_activity_ids'], ['uncertain'])
        self.assertEqual(gap['boundary_event_ids'], ['u'])

    def test_message_edge_and_target_contact_offsets_are_gap_anchors(self):
        storyline = {'duration_ms': 100000, 'activities': [
            activity('start', 0, events=('start-event',)),
            activity('end', 100000, events=('end-event',)),
        ]}
        cases = [
            ({'messages': [{'id': 'message', 'offset_ms': 50000,
                            'activity_id': 'message-activity',
                            'event_ids': ['message-event']}]},
             'message-activity', 'message-event'),
            ({'messages': [], 'edges': 'storyline'}, 'edge-activity', 'edge-event'),
            ({'messages': [], 'logical_storyline': {'activity_entities': {
                'target-activity': {'target_ms': 50000, 'target_entity_id': 'target'}}}},
             'target-activity', 'target-event'),
        ]
        for extra, boundary_activity, boundary_event in cases:
            with self.subTest(boundary_activity=boundary_activity):
                story = copy.deepcopy(storyline)
                if extra.pop('edges', None):
                    story['edges'] = [{'source_ms': 50000,
                                       'source_activity_id': 'edge-activity',
                                       'source_event_id': 'edge-event'}]
                if boundary_activity == 'target-activity':
                    story['activities'].append(
                        activity('target-activity', 100000, events=('target-event',)))
                flow = {'duration_ms': 100000, 'time_mode': 'recorded', **extra}
                before = copy.deepcopy((story, flow))
                axis = build_story_axis(story, flow)
                self.assertEqual((story, flow), before)
                breaks = [segment for segment in axis['segments']
                          if segment['kind'] == 'idle_break']
                self.assertEqual([(item['recorded_start_ms'], item['recorded_end_ms'])
                                  for item in breaks], [(0, 50000), (50000, 100000)])
                self.assertTrue(all(boundary_activity in item['boundary_activity_ids']
                                    for item in breaks))
                self.assertTrue(all(boundary_event in item['boundary_event_ids']
                                    for item in breaks))

    def test_mixed_interior_anchors_prevent_false_idle_claim(self):
        storyline = {'duration_ms': 100000, 'activities': [
            activity('start', 0), activity('target', 100000, events=('target-event',)),
            activity('end', 100000),
        ], 'edges': [{'source_ms': 50000, 'source_activity_id': 'edge',
                      'source_event_id': 'edge-event'}]}
        flow = {'duration_ms': 100000, 'time_mode': 'recorded',
                'messages': [{'id': 'message', 'offset_ms': 25000,
                              'event_ids': ['message-event']}],
                'logical_storyline': {'activity_entities': {
                    'target': {'target_ms': 75000, 'target_entity_id': 'target-entity'}}}}
        axis = build_story_axis(storyline, flow)
        self.assertEqual(axis['mode'], 'recorded')
        self.assertFalse(any(segment['kind'] == 'idle_break'
                             for segment in axis['segments']))

    def test_short_unknown_and_empty_axes_stay_linear(self):
        short = {'duration_ms': 29999, 'activities': [activity('a', 0), activity('b', 29999)]}
        self.assertEqual(build_story_axis(short, {'time_mode': 'recorded'})['mode'], 'recorded')
        review = build_story_axis(
            {'duration_ms': 100000, 'activities': [activity('a', 0), activity('b', 100000)]},
            {'duration_ms': 100000, 'time_mode': 'review_sequence'})
        self.assertEqual(review['mode'], 'review_sequence')
        self.assertEqual(len(review['segments']), 1)
        empty = build_story_axis({}, {'time_mode': 'recorded'})
        self.assertEqual(empty['display_duration_ms'], 0)
        self.assertEqual(empty['segments'], [])

    def test_invalid_configuration_fails_explicitly(self):
        for threshold, width in [(-1, 5000), (30000, -1), (float('nan'), 5000),
                                 (30000, True)]:
            with self.assertRaises(ValueError):
                build_story_axis({}, {}, min_idle_ms=threshold, compact_idle_ms=width)


if __name__ == '__main__':
    unittest.main()
