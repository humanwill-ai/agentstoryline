"""New synthetic Orca display tests; no private capture fixtures."""
import copy
import unittest
from web.flow import build_flow, _response_preview
from web.storyline import build_storyline


def row(seq, kind, model=None, causes=None, provider=None, call_id=None):
    attrs = {'model': model} if model else {}
    if kind == 'run.start':
        attrs['run_key'] = 'exp9-worker-i7'
    data = {'source_kind': kind, 'source_record': {'seq': seq, 'type': kind, 'attrs': attrs, 'causes': causes or []}}
    if model:
        data['model_identity'] = {'model': model, 'basis': 'source_recorded_not_provider_attestation'}
        if provider:
            data['model_identity']['provider'] = provider
    if kind == 'model.request':
        data['request'] = {'model': model, 'messages': []}
    elif kind == 'model.response':
        data['response'] = {'text': 'Synthetic response'}
    else:
        data['content'] = attrs
    return {'id': 'e' + str(seq), 'kind': 'observation' if kind == 'route.decision' else kind,
            'sequence': seq, 'timestamp': '2026-01-01T00:00:0' + str(seq) + 'Z',
            'data': data, **({'parent_id': 'e' + str(causes[0])} if kind == 'model.response' and causes else {}), **({'call_id': call_id} if call_id else {})}


class OrcaFlowPresentationTests(unittest.TestCase):
    def build(self, events):
        bundle = {'manifest': {'source': {'adapter': 'orcareplay'}, 'agent': {'name': 'gateway'}}, 'events': events}
        before = copy.deepcopy(bundle)
        flow = build_flow(bundle, {}, build_storyline(bundle, {}))
        self.assertEqual(bundle, before)
        return flow

    def test_routes_are_context_only_when_source_links_one_request(self):
        flow = self.build([row(0, 'run.start'), row(1, 'model.request', 'openai/gpt-6-luna', call_id='call'),
                           row(2, 'route.decision', 'openai/gpt-6-luna', [1]),
                           row(3, 'route.decision', 'other', [0]),
                           row(4, 'model.response', 'gpt-6-luna', [1], call_id='call')])
        request, linked, unlinked = flow['messages'][1:4]
        self.assertTrue(linked['compact_hidden'])
        self.assertFalse(unlinked.get('compact_hidden', False))
        self.assertEqual(request['context'][-1]['event_ids'], ['e2'])
        self.assertIn('model', request['context'][-1]['parameters'])
        self.assertEqual(flow['participants']['worker_count'], 0)
        self.assertTrue(any('exp9-worker-i7' in e['display_name'] for e in flow['entities']))
        models = [e for e in flow['entities'] if e['kind'] == 'model']
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]['recorded_model_aliases'], ['openai/gpt-6-luna', 'gpt-6-luna'])

    def test_native_unique_request_id_and_source_cause_alias_without_activity_pair(self):
        request = row(0, 'model.request', 'openai/gpt-6-luna')
        response = row(1, 'model.response', 'gpt-6-luna', [0])
        response.pop('parent_id')
        for event in (request, response):
            event['data']['source_record']['attrs']['request_id'] = 'synthetic-request'
        flow = self.build([request, response])
        self.assertNotEqual(flow['messages'][0]['activity_id'], flow['messages'][1]['activity_id'])
        self.assertEqual(len([e for e in flow['entities'] if e['kind'] == 'model']), 1)
        response['data']['source_record']['causes'] = []
        flow = self.build([request, response])
        self.assertEqual(len([e for e in flow['entities'] if e['kind'] == 'model']), 2)

    def test_unpaired_or_different_provider_models_stay_separate(self):
        for paired, provider, returned in [(False, None, 'gpt-6-luna'), (True, 'other-provider', 'gpt-6-luna'), (True, None, 'gpt-6-astra')]:
            with self.subTest(paired=paired, provider=provider, returned=returned):
                flow = self.build([row(0, 'model.request', 'openai/gpt-6-luna', call_id='a'),
                                   row(1, 'model.response', returned, causes=[0], provider=provider, call_id='a' if paired else 'b')])
                self.assertEqual(len([e for e in flow['entities'] if e['kind'] == 'model']), 2)


class StreamPreviewTests(unittest.TestCase):
    def test_partial_stream_notices_are_display_only(self):
        for blocks, expected in [([], 'could not be decoded'), ([{'type': 'text', 'text': 'Synthetic fragment'}], 'partially decoded')]:
            response = {'content': blocks, 'stream_decode': {'status': 'partial', 'issues': ['synthetic unsupported event']}}
            before = copy.deepcopy(response)
            preview = _response_preview(response)
            self.assertIn(expected, preview)
            if blocks:
                self.assertIn('Synthetic fragment', preview)
            self.assertEqual(response, before)
