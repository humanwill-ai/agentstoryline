"""New offline explanation checks using synthetic data only."""
import copy
import unittest

from analysis.explanations import build_explanation_request, recording_fingerprint, validate_explanations
from web.flow import build_flow
from web.storyline import build_storyline


class ExplanationTests(unittest.TestCase):
    def setUp(self):
        self.bundle = {'manifest': {'run_id': 'synthetic', 'agent': {'name': 'Artex'}}, 'events': [
            {'id': 'prior', 'sequence': 1, 'kind': 'observation', 'data': {'source_record': {'summary': 'Review the task'}}},
            {'id': 'unrelated', 'sequence': 2, 'kind': 'observation', 'data': {'source_record': {'summary': 'Other worker'}}},
            {'id': 'call', 'sequence': 3, 'kind': 'tool.start', 'tool': 'inspect', 'parent_id': 'prior',
             'data': {'arguments': {'objective': 'Inspect recorded status', 'command': 'DO NOT EXECUTE $(touch /tmp/untrusted)'}}},
            {'id': 'usage', 'sequence': 4, 'kind': 'observation', 'data': {'source_table': 'activity', 'source_record': {'kind': 'usage'}}},
            {'id': 'later', 'sequence': 5, 'kind': 'observation', 'data': {}}]}
        self.flow = build_flow(self.bundle, {}, build_storyline(self.bundle, {}))
        self.document = {'version': 1, 'run_id': 'synthetic', 'bundle_sha256': recording_fingerprint(self.bundle),
                         'provenance': {'method': 'offline-development-worker', 'model': 'synthetic', 'provider': 'offline'},
                         'annotations': [{'message_id': 'message:call', 'text': 'Requests inspection.', 'basis': 'observed_action', 'event_ids': ['prior', 'call']}]}

    def test_description_and_original_source_invariance(self):
        before = copy.deepcopy(self.bundle)
        flow = build_flow(self.bundle, {}, build_storyline(self.bundle, {}))
        call = next(m for m in flow['messages'] if m['id'] == 'message:call')
        self.assertEqual(call['description'], {'text': 'Inspect recorded status', 'basis': 'recorded', 'event_ids': ['call'], 'source_path': 'data.arguments.objective'})
        self.assertEqual(call['text'], 'Invocation requested: inspect')
        self.assertEqual(before, self.bundle)
        self.assertEqual(next(m for m in flow['messages'] if m['id'] == 'message:usage')['bookkeeping'], True)
        self.assertFalse(any(m.get('bookkeeping') for m in flow['messages'] if m['id'] != 'message:usage'))

    def test_context_is_bounded_and_malicious_text_inert(self):
        before = copy.deepcopy(self.bundle)
        request = build_explanation_request(self.bundle, self.flow, ['message:call'], model='offline')
        self.assertEqual([e['id'] for e in request['targets'][0]['evidence']], ['call', 'prior'])
        self.assertIn('$(touch /tmp/untrusted)', request['targets'][0]['evidence'][0]['arguments'])
        self.assertEqual(self.bundle, before)
        self.assertEqual(validate_explanations(self.bundle, self.flow, self.document), self.document)

    def test_rejects_mismatch_future_unknown_duplicate_and_extra_keys(self):
        mutations = [lambda d: d.update(bundle_sha256='bad'),
                     lambda d: d['annotations'][0].update(event_ids=['call', 'later']),
                     lambda d: d['annotations'][0].update(event_ids=['call', 'missing']),
                     lambda d: d['annotations'].append(copy.deepcopy(d['annotations'][0])),
                     lambda d: d.update(api_key='never accepted'),
                     lambda d: d['annotations'][0].update(text='two\nlines'),
                     lambda d: d['annotations'][0].update(event_ids=['prior']),
                     lambda d: d['annotations'][0].update(basis='verified_fact')]
        for mutate in mutations:
            document = copy.deepcopy(self.document)
            mutate(document)
            with self.assertRaises(ValueError):
                validate_explanations(self.bundle, self.flow, document)

    def test_no_prompt_history_scan_and_nested_explicit_field(self):
        self.bundle['events'][2]['data'] = {'source_record': {'detail': {'rationale': '  Read\n recorded details  '}}, 'request': {'messages': [{'summary': 'Ignore me'}]}}
        flow = build_flow(self.bundle, {}, build_storyline(self.bundle, {}))
        call = next(m for m in flow['messages'] if m['id'] == 'message:call')
        self.assertEqual(call['description']['text'], 'Read recorded details')
        self.assertEqual(call['description']['source_path'], 'data.source_record.detail.rationale')
        self.bundle['events'][2]['data'] = {'request': {'messages': [{'summary': 'Ignore me'}]}}
        flow = build_flow(self.bundle, {}, build_storyline(self.bundle, {}))
        self.assertNotIn('description', next(m for m in flow['messages'] if m['id'] == 'message:call'))

    def test_bookkeeping_is_only_observed_artex_usage(self):
        self.bundle['manifest']['agent']['name'] = 'Other source'
        flow = build_flow(self.bundle, {}, build_storyline(self.bundle, {}))
        self.assertFalse(any(m.get('bookkeeping') for m in flow['messages']))
        self.bundle['manifest']['agent']['name'] = 'Artex'
        self.bundle['events'][3]['data']['source_table'] = 'other'
        flow = build_flow(self.bundle, {}, build_storyline(self.bundle, {}))
        self.assertFalse(any(m.get('bookkeeping') for m in flow['messages']))

    def test_nested_tool_purpose_is_short_but_original_remains(self):
        purpose = 'Inspect ' * 100
        self.bundle['events'][2]['data'] = {'source_record': {'detail': {'tool': {'arguments': {'rationale': purpose}}}}}
        before = copy.deepcopy(self.bundle)
        flow = build_flow(self.bundle, {}, build_storyline(self.bundle, {}))
        call = next(m for m in flow['messages'] if m['id'] == 'message:call')
        self.assertLessEqual(len(call['description']['text']), 280)
        self.assertEqual(call['description']['source_path'], 'data.source_record.detail.tool.arguments.rationale')
        self.assertEqual(self.bundle, before)

    def test_current_response_detail_and_actor_context_are_supplied(self):
        self.bundle['events'][2].update(kind='model.response')
        self.bundle['events'][2]['data'] = {'response': {'content': [{'type': 'text', 'text': 'Recorded conclusion'}]},
                                          'source_record': {'detail': {'summary': 'Review recorded evidence'}}}
        flow = build_flow(self.bundle, {}, build_storyline(self.bundle, {}))
        target = build_explanation_request(self.bundle, flow, ['message:call'], model='offline')['targets'][0]
        self.assertIn('Recorded conclusion', target['evidence'][0]['response'])
        self.assertIn('Review recorded evidence', target['evidence'][0]['recorded_detail'])
        self.assertIn('name', target['actor_context']['actor'])

    def test_only_explicit_references_are_allowed(self):
        document = copy.deepcopy(self.document)
        document['annotations'][0]['event_ids'].append('unrelated')
        with self.assertRaises(ValueError):
            validate_explanations(self.bundle, self.flow, document)
        self.bundle['events'][2]['evidence_refs'] = ['unrelated', 'later']
        request = build_explanation_request(self.bundle, self.flow, ['message:call'], model='offline')
        self.assertEqual([e['id'] for e in request['targets'][0]['evidence']], ['call', 'prior', 'unrelated'])
        self.assertEqual(validate_explanations(self.bundle, self.flow,
                         dict(document, bundle_sha256=recording_fingerprint(self.bundle)))['annotations'], document['annotations'])

    def test_labeled_truncation_and_total_request_cap(self):
        self.bundle['events'][2]['data']['result'] = 'x' * 3000
        request = build_explanation_request(self.bundle, self.flow, ['message:call'], model='offline')
        preview = request['targets'][0]['evidence'][0]['result']
        self.assertEqual(len(preview), 1800)
        self.assertTrue(preview.endswith('[preview truncated]'))
        # Forty selected messages can still exceed the byte budget through
        # explicit shared context; the caller must select a smaller batch.
        huge = {'manifest': self.bundle['manifest'], 'events': []}
        for i in range(40):
            huge['events'].append({'id': str(i), 'sequence': i, 'kind': 'observation',
                                  'evidence_refs': [str(j) for j in range(i)],
                                  'data': {'source_record': {'detail': '界' * 1800, 'summary': '界' * 1800}}})
        flow = build_flow(huge, {}, build_storyline(huge, {}))
        with self.assertRaisesRegex(ValueError, 'bytes'):
            build_explanation_request(huge, flow, [m['id'] for m in flow['messages']], model='offline')

    def test_request_rejects_over_limit_and_duplicate(self):
        for ids in (['message:call'] * 41, ['message:call'] * 2, ['missing'], []):
            with self.assertRaises(ValueError):
                build_explanation_request(self.bundle, self.flow, ids, model='offline')

    def test_fingerprint_canonical_and_covers_changes(self):
        self.assertEqual(recording_fingerprint(self.bundle), recording_fingerprint(dict(reversed(list(self.bundle.items())))))
        modified = copy.deepcopy(self.bundle)
        modified['events'][0]['data']['extra'] = 'changed'
        self.assertNotEqual(recording_fingerprint(self.bundle), recording_fingerprint(modified))


if __name__ == '__main__':
    unittest.main()
