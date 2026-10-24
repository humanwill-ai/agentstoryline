"""New synthetic source-outcome tests; no real recordings or model calls."""
import copy
import unittest

from analysis.milestones import source_reported_milestones
from web.flow import build_flow
from web.storyline import build_storyline


class SourceMilestoneTests(unittest.TestCase):
    def event(self, entries):
        return {'id': 'e1', 'kind': 'observation', 'sequence': 0,
                'data': {'milestones': entries}}

    def test_explicit_outcomes_have_own_source_evidence_and_no_verification_claim(self):
        event = self.event([{'label': 'Tests passed', 'outcome': 'success'},
                            {'label': 'Report generated', 'outcome': 'success'},
                            {'label': 'Report generation failed', 'outcome': 'failure'}])
        before = copy.deepcopy(event)
        actual = source_reported_milestones(event)
        self.assertEqual([m['outcome'] for m in actual], ['success', 'success', 'failure'])
        for milestone in actual:
            self.assertEqual(set(milestone), {'label', 'outcome', 'event_ids', 'provenance', 'basis'})
            self.assertEqual(milestone['event_ids'], ['e1'])
            self.assertEqual(milestone['provenance'], 'source_reported')
            self.assertEqual(milestone['basis'], 'explicit_source_outcome')
        self.assertEqual(event, before)

    def test_strict_types_bounds_and_foreign_references(self):
        entries = [None, 'Tests passed', {'label': 'a' * 121, 'outcome': 'success'},
                   {'label': '  ', 'outcome': 'success'}, {'label': 'a\nb', 'outcome': 'success'},
                   *[{'label': 'a' + char + 'b', 'outcome': 'success'} for char in '\u0085\u2028\u2029'],
                   {'label': 'Tests passed', 'outcome': ['success']},
                   {'label': 'Tests passed', 'outcome': 'succeeded'},
                   {'label': 'Tests passed', 'outcome': 'success', 'event_ids': ['unrelated']}]
        self.assertEqual(source_reported_milestones(self.event(entries)), [])
        valid = {'label': 'a' * 120, 'outcome': 'attempt', 'event_ids': ['e1']}
        self.assertEqual(len(source_reported_milestones(self.event([valid] * 20))), 16)
        self.assertEqual(source_reported_milestones(self.event({'label': 'bad'})), [])

    def test_never_infers_from_tool_status_http_or_model_prose(self):
        for kind, data in [('tool.end', {'outcome': 'success', 'result': {'status': 200}}),
                           ('model.response', {'response': {'text': 'Tests passed. Report generated successfully.'}})]:
            self.assertEqual(source_reported_milestones({'id': 'e1', 'kind': kind, 'data': data}), [])

    def test_orca_opt_in_extension_requires_exact_source_kind_and_explicit_attrs(self):
        event = {'id': 'e1', 'data': {'source_kind': 'milestone', 'source_record': {
            'attrs': {'label': 'Report generated', 'outcome': 'unconfirmed'}}}}
        self.assertEqual(source_reported_milestones(event), [])
        self.assertEqual(source_reported_milestones(event, 'orcareplay')[0]['outcome'], 'unconfirmed')
        event['data']['source_kind'] = 'tool.result'
        self.assertEqual(source_reported_milestones(event, 'orcareplay'), [])

    def test_flow_preserves_bundle_and_keeps_explicit_milestone_visible(self):
        event = self.event([{'label': 'Report generated', 'outcome': 'success'}])
        event['data']['source_kind'] = 'archive.snapshot'
        bundle = {'manifest': {'source': {'adapter': 'orcareplay'}}, 'events': [event]}
        before = copy.deepcopy(bundle)
        flow = build_flow(bundle, {}, build_storyline(bundle, {}))
        message = flow['messages'][0]
        self.assertEqual(message['milestones'][0]['label'], 'Report generated')
        self.assertFalse(message.get('compact_hidden', False))
        self.assertEqual(bundle, before)
