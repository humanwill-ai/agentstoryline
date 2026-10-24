"""Synthetic offline attachment evidence checks."""
import copy
import json
import unittest

from analysis.telemetry import build_telemetry_attachment, validate_telemetry_attachment


def bundle(prefix, text='Synthetic prompt', reply='Synthetic answer', request_id=None):
    request = {'id': prefix + '-q', 'kind': 'model.request', 'data': {
        'request': {'messages': [{'role': 'user', 'content': text}]},
        'source_session': {'id': 'session-' + prefix, 'sequence': 1}}}
    response = {'id': prefix + '-a', 'kind': 'model.response', 'parent_id': request['id'],
                'data': {'response': {'text': reply}, 'source_record': {'attrs': {}}}}
    if request_id:
        response['data']['source_record']['attrs']['request_id'] = request_id
    return {'manifest': {'run_id': prefix}, 'events': [request, response]}


class TelemetryMatchingTests(unittest.TestCase):
    def test_exact_content_preserves_sources_and_binding(self):
        a, o = bundle('a'), bundle('o')
        originals = copy.deepcopy((a, o))
        result = build_telemetry_attachment(a, o)
        self.assertEqual(result['summary']['matched_exchanges'], 1)
        self.assertEqual(result['matches'][0]['artex_response_id'], 'a-a')
        self.assertEqual((a, o), originals)
        self.assertEqual(validate_telemetry_attachment(a, result), result)
        result['bundle']['manifest']['run_id'] = 'changed'
        self.assertEqual(o, originals[1])
        with self.assertRaises(ValueError):
            validate_telemetry_attachment(a, result)

    def test_forgery_and_stale_primary_rejected(self):
        a, o = bundle('a'), bundle('o')
        result = build_telemetry_attachment(a, o)
        forged = copy.deepcopy(result)
        forged['matches'][0]['artex_request_id'] = 'invented'
        with self.assertRaises(ValueError):
            validate_telemetry_attachment(a, forged)
        a['events'][0]['data']['request']['messages'][0]['content'] = 'new'
        with self.assertRaises(ValueError):
            validate_telemetry_attachment(a, result)

    def test_session_model_time_are_not_identity(self):
        a, o = bundle('a', 'one', 'first'), bundle('o', 'two', 'second')
        for b in (a, o):
            b['events'][0].update(timestamp='2026-01-01T00:00:00Z')
            b['events'][0]['data'].update(source_session={'id': 'same'}, model='same')
        result = build_telemetry_attachment(a, o)
        self.assertEqual(result['summary']['matched_exchanges'], 0)
        self.assertEqual(result['unmatched_orca_request_ids'], ['o-q'])

    def test_repeated_content_is_ambiguous_in_both_directions(self):
        a, o = bundle('a'), bundle('o')
        a['events'].extend(bundle('a2')['events'])
        result = build_telemetry_attachment(a, o)
        self.assertEqual(result['ambiguous_orca_request_ids'], ['o-q'])
        a = bundle('a')
        o['events'].extend(bundle('o2')['events'])
        result = build_telemetry_attachment(a, o)
        self.assertEqual(result['summary']['ambiguous_exchanges'], 2)
        self.assertEqual(result['matches'], [])

    def test_shared_id_never_overrides_conflicting_content(self):
        a = bundle('a', reply='one', request_id='shared')
        o = bundle('o', reply='two', request_id='shared')
        result = build_telemetry_attachment(a, o)
        self.assertEqual(result['matches'], [])
        self.assertEqual(result['summary']['conflicting_exchanges'], 1)

    def test_sse_normalization_ignores_envelope_clock_only(self):
        a, o = bundle('a'), bundle('o')
        def stream(created):
            return 'data: ' + json.dumps({'id': 'chatcmpl-shared', 'created': created,
                'choices': [{'index': 0, 'delta': {'content': 'Hello'}}]}) + '\n\ndata: [DONE]\n'
        a['events'][1]['data']['source_record']['raw_response'] = stream(1)
        o['events'][1]['data']['resolved_payload'] = stream(2)
        o['events'][0]['data']['request']['messages'][0]['content'] = [{'type': 'text', 'text': 'Synthetic prompt'}]
        result = build_telemetry_attachment(a, o)
        self.assertEqual(result['summary']['matched_exchanges'], 1)
        self.assertIn('body', result['matches'][0]['evidence']['equal_fields'])

    def test_shared_id_with_missing_content_and_orca_only_are_explicit(self):
        a, o = bundle('a', request_id='same'), bundle('o', request_id='same')
        for b in (a, o):
            b['events'][0]['data'].pop('request')
            b['events'][1]['data'].pop('response')
        o['events'].extend(bundle('extra', 'different', 'different')['events'])
        result = build_telemetry_attachment(a, o)
        self.assertEqual(result['matches'][0]['basis'], 'shared_request_id')
        self.assertEqual(result['summary']['orca_only_exchanges'], 1)


if __name__ == '__main__':
    unittest.main()
