"""New synthetic profile, selection and redaction checks."""
import copy
import json
import unittest
from analysis.explanation_profiles import profile_for, redact
from analysis.explanations import build_explanation_request, select_message_ids


class ProfileTests(unittest.TestCase):
    def test_redaction_precedes_context_serialization_without_mutation(self):
        bundle = {'manifest': {'agent': {'name': 'Artex'}}, 'events': [{'id': 'e', 'kind': 'tool.start', 'data': {'arguments': {'api_key': 'private', 'headers': {'Authorization': 'Bearer hidden'}, 'command': 'api_key=hidden sk-abcdefghijklmnop'}}}]}
        original = copy.deepcopy(bundle)
        flow = {'messages': [{'id': 'm', 'event_ids': ['e']}]}
        raw = json.dumps(build_explanation_request(bundle, flow, ['m'], model='test')['targets'])
        self.assertNotIn('hidden', raw)
        self.assertNotIn('private', raw)
        self.assertEqual(bundle, original)

    def test_generic_gateway_does_not_supply_intent(self):
        event = {'id': 'e', 'kind': 'model.response', 'data': {'source_record': {'description': 'A gateway result, not intent'}, 'response': 'result'}}
        profile = profile_for({'manifest': {'agent': {'name': 'LiteLLM'}}})
        self.assertEqual(profile.adapter_id, 'generic')
        self.assertIsNone(profile.recorded_description(event))
        self.assertEqual(profile.context(event)['recorded_fields'], {})

    def test_resume_skips_history_usage_parameters_and_recorded_descriptions(self):
        bundle = {'manifest': {'agent': {'name': 'Artex'}}, 'events': [
            {'id': 'a', 'kind': 'observation', 'data': {}},
            {'id': 'b', 'kind': 'observation', 'data': {'source_record': {'summary': 'Useful recorded text'}}},
            {'id': 'c', 'kind': 'observation', 'data': {'source_table': 'activity', 'source_record': {'kind': 'usage'}}},
            {'id': 'd', 'kind': 'model.request', 'data': {}},
            {'id': 'e', 'kind': 'observation', 'data': {'source_kind': 'request.history.text'}}]}
        flow = {'messages': [{'id': e['id'], 'event_ids': [e['id']], 'offset_ms': i*100} for i,e in enumerate(bundle['events'])]}
        self.assertEqual(select_message_ids(bundle, flow), ['a'])
        self.assertEqual(select_message_ids(bundle, flow, scope='all'), ['a','b'])
        self.assertEqual(select_message_ids(bundle, flow, scope='interval', start_ms=50,end_ms=150), ['b'])

    def test_common_serialized_auth_and_url_credentials(self):
        inputs = ['{"token":"secret-value"}', "api_key='secret-value'", 'Authorization: Basic c2VjcmV0', 'https://user:password@example.invalid/path?api_key=secret-value&ok=yes', '{"nested":{"access_token":"secret-value"}}']
        for raw in inputs:
            clean = redact(raw)
            self.assertNotIn('secret-value', clean)
            self.assertNotIn('c2VjcmV0', clean)
            self.assertNotIn('user:password', clean)
            self.assertEqual(redact(clean), clean)
        self.assertEqual(redact('{"safe": 1}'), '{"safe": 1}')

    def test_invalid_interval_bounds(self):
        for start, end in [(True, 4), (0, False), (float('nan'), 2), (0, float('inf')), (-1, 4), (5,4)]:
            with self.assertRaises(ValueError):
                select_message_ids({'events': []}, {'messages': []}, scope='interval', start_ms=start, end_ms=end)
