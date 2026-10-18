import copy
import unittest

from web.storyline import build_storyline


def event(eid, kind='observation', second=0, **fields):
    return {'id': eid, 'kind': kind, 'timestamp': f'2026-01-01T00:00:{second:02d}Z' if second is not None else None, **fields}


class StorylineTests(unittest.TestCase):
    def test_pair_overlap_edges_chapters_and_no_mutation(self):
        events = [event('a', 'tool.start', 0, call_id='a', tool='shell'), event('b', 'tool.start', 1, call_id='b', tool='shell'), event('c', 'tool.end', 3, call_id='a', parent_id='a'), event('d', 'tool.end', 4, call_id='b', dependencies=[{'event_id': 'b', 'provenance': 'recorded'}])]
        bundle = {'events': events}
        narrative = {'stages': [{'id': 's', 'event_ids': ['a', 'c'], 'basis': 'interpretation'}]}
        original = copy.deepcopy((bundle, narrative))
        result = build_storyline(bundle, narrative)
        self.assertEqual((bundle, narrative), original)
        self.assertEqual(len(result['activities']), 2)
        self.assertEqual(len(result['lanes']), 1)
        self.assertEqual([a['timing'] for a in result['activities']], ['interval', 'interval'])
        self.assertEqual(result['chapters'][0]['activity_ids'], ['activity:a'])
        self.assertEqual(result['edges'][0]['source_event_id'], 'a')
        self.assertEqual(result['duration_ms'], 4000)

    def test_duplicate_ids_missing_endpoints_and_clock_reversal(self):
        events = [event('a', 'tool.start', 4, call_id='x'), event('b', 'tool.end', 2, call_id='x', parent_id='a'), event('c', 'model.request', None, call_id='m'), event('d', 'model.response', 5, call_id='m', parent_id='c'), event('e', 'tool.start', 0, call_id='duplicate'), event('f', 'tool.start', 1, call_id='duplicate'), event('g', 'tool.end', 2, call_id='duplicate', parent_id='e')]
        result = build_storyline({'events': events}, {})
        self.assertEqual(result['activities'][0]['timing'], 'clock_conflict')
        self.assertEqual(result['activities'][1]['timing'], 'incomplete')
        self.assertIsNone(result['activities'][1]['start_ms'])
        self.assertEqual(len(result['activities']), 5)
        self.assertEqual({eid for a in result['activities'] for eid in a['event_ids']}, {e['id'] for e in events})

    def test_history_attached_only_with_recorded_correlation(self):
        events = [event('a', 'tool.start', 0, call_id='x'), event('b', 'tool.end', 1, call_id='x', parent_id='a'), event('h', second=2, call_id='x', evidence_refs=['a'], data={'source_kind': 'request.history.tool_use', 'correlation_basis': 'unique_recorded_tool_use_id_within_export'}), event('u', second=3, call_id='x', data={'source_kind': 'request.history.tool_use', 'correlation_basis': 'transcript_only'})]
        result = build_storyline({'events': events}, {})
        self.assertEqual(len(result['activities']), 2)
        self.assertEqual(result['activities'][0]['event_ids'], ['a', 'b', 'h'])
        self.assertEqual(result['activities'][0]['end_ms'], 1000)
        self.assertEqual(result['activities'][1]['category'], 'observation')

    def test_identity_and_uncorrelated_flags_prevent_pairing(self):
        events = [event('a', 'tool.start', 0, call_id='x', data={'session_id': 'one'}), event('b', 'tool.end', 1, call_id='x', parent_id='a', data={'session_id': 'two'}), event('c', 'tool.start', 0, call_id='y', data={'repeated_native_item_id': True}), event('d', 'tool.end', 1, call_id='y', parent_id='c')]
        result = build_storyline({'events': events}, {})
        self.assertEqual(len(result['activities']), 4)
        self.assertEqual(len(result['lanes']), 3)

    def test_unknown_sparse_input(self):
        self.assertEqual(build_storyline({}, {})['duration_ms'], 0)
        result = build_storyline({'events': [{'id': 'a'}]}, {'stages': []})
        self.assertIsNone(result['origin_timestamp'])
        self.assertEqual(result['activities'][0]['timing'], 'unknown')

    def test_persistence_timestamps_are_observations_not_duration(self):
        for end, expected in ((0, 'instant'), (3, 'incomplete')):
            events = [event('a', 'model.request', 0, call_id='m', data={'timestamp_basis': 'row_persistence'}), event('b', 'model.response', end, call_id='m', parent_id='a', data={'timestamp_basis': 'row_persistence'})]
            a = build_storyline({'events': events}, {})['activities'][0]
            self.assertEqual(a['timing'], expected)
            self.assertEqual(a['start_ms'], 0)
            self.assertEqual(a['end_ms'], end * 1000)
            self.assertTrue(any('persistence' in note for note in a['notes']))

    def test_source_context_disambiguates_calls_without_session(self):
        events = []
        for node in ('one', 'two'):
            data = {'source_record': {'worker': 'w', 'node_id': node, 'exploration_id': 'e'}}
            events.extend([event(node + '.start', 'tool.start', 0, call_id='x', data=data), event(node + '.end', 'tool.end', 1, call_id='x', parent_id=node + '.start', data=data)])
        result = build_storyline({'events': events}, {})
        self.assertEqual(len(result['activities']), 2)
        self.assertEqual(len(result['lanes']), 2)
        self.assertTrue(all(a['timing'] == 'interval' for a in result['activities']))

    def test_inferred_dependency_does_not_pair_endpoints(self):
        events = [event('a', 'tool.start', 0, call_id='x'), event('b', 'tool.end', 1, call_id='x', dependencies=[{'event_id': 'a', 'provenance': 'inferred'}])]
        result = build_storyline({'events': events}, {})
        self.assertEqual(len(result['activities']), 2)
        self.assertEqual(result['edges'][0]['provenance'], 'inferred')

    def test_labels_are_mechanical_and_history_notes_deduplicated(self):
        events = [event('a', 'tool.start', 0, call_id='x', tool='HTTP', data={'arguments': {'method': 'get', 'url': 'https://user:secret@example.test/session?token=secret#fragment'}}), event('b', 'tool.end', 1, call_id='x', parent_id='a')]
        for i in range(3):
            events.append(event('h' + str(i), evidence_refs=['a'], data={'source_kind': 'request.history.tool_use', 'correlation_basis': 'unique_recorded_tool_use_id_within_export'}))
        result = build_storyline({'events': events}, {})
        a = result['activities'][0]
        self.assertEqual(a['label'], 'GET /session')
        self.assertEqual(len(a['notes']), len(set(a['notes'])))
        self.assertEqual(len(a['event_ids']), 5)
        self.assertEqual(build_storyline({'events': [event('t', data={'source_table': 'tasks'})]}, {})['activities'][0]['label'], 'Task record')
        model = build_storyline({'events': [event('r', 'model.request', call_id='m'), event('s', 'model.response', call_id='m', parent_id='r')]}, {})
        self.assertEqual(model['activities'][0]['label'], 'Model exchange')

    def test_edge_anchors_use_exact_event_not_grouped_activity_end(self):
        events = [event('request', 'model.request', 0, call_id='m'), event('response', 'model.response', 4, call_id='m', parent_id='request'), event('tool', 'tool.start', 5, dependencies=[{'event_id': 'request', 'provenance': 'recorded'}])]
        result = build_storyline({'events': events}, {})
        dependency = next(e for e in result['edges'] if e['kind'] == 'dependency')
        self.assertEqual(result['activities'][0]['end_ms'], 4000)
        self.assertEqual(dependency['source_ms'], 0)
        self.assertEqual(dependency['target_ms'], 5000)
