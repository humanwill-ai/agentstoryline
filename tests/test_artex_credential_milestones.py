"""New synthetic checks for observed Artex credential-chain milestones."""
import copy
import json
import unittest

from web.flow import _http_observation, build_flow
from web.storyline import build_storyline


def event(eid, kind, sequence, **fields):
    return {'id': eid, 'kind': kind, 'sequence': sequence, 'data': {},
            'evidence_refs': ['evidence:' + eid], **fields}


def response(eid, sequence, body, status=200, **data):
    return event(eid, 'tool.end', sequence,
                 data={'result': {'response': {'status': status, 'body': body}}, **data})


class ArtexCredentialMilestoneTests(unittest.TestCase):
    def build(self, events, adapter='artex'):
        bundle = {'manifest': {'source': {'adapter': adapter}, 'agent': {'name': 'Synthetic agent'}},
                  'events': events}
        before = copy.deepcopy(bundle)
        flow = build_flow(bundle, {}, build_storyline(bundle, {}))
        self.assertEqual(bundle, before)
        return flow['messages']

    def test_observed_artex_chain_preserves_evidence_and_keeps_values_out_of_tags(self):
        target = 'http://127.0.0.1/document'
        events = [
            event('baseline-call', 'tool.start', 0, call_id='baseline-id', data={'arguments': {'url': target}}),
            response('baseline', 1, {'error': 'unauthorized'}, status=401),
            response('credentials', 2, {'username': 'synthetic-user', 'password': 'synthetic-password', 'login_path': '/synthetic-login'}),
            response('token', 3, {'bearer_token': 'synthetic-bearer'}),
            event('reuse', 'tool.start', 4, data={'arguments': {'url': target, 'headers': {'Authorization': 'Bearer synthetic-bearer'}}}),
            response('document', 5, {'doc_id': 'synthetic-doc', 'content': 'Synthetic document', 'proof_marker': 'synthetic-proof'}),
        ]
        events[1]['call_id'] = 'baseline-id'
        messages = self.build(events)
        self.assertEqual([[tag['label'] for tag in message['semantic_tags']] for message in messages],
                         [[], [], ['Credentials obtained'], ['Token obtained'], ['Exploitation attempt'], ['Document retrieved']])
        self.assertEqual(messages[2]['semantic_tags'][0]['event_ids'], ['credentials'])
        self.assertEqual(messages[3]['semantic_tags'][0]['event_ids'], ['token'])
        self.assertEqual(messages[4]['semantic_tags'][0]['event_ids'], ['baseline', 'token', 'reuse'])
        self.assertEqual(messages[3]['semantic_tags'][0]['basis'], 'http_200_with_bearer_token_body')
        for source, message in zip(events, messages):
            self.assertEqual(message['evidence_refs'], source['evidence_refs'])
            for tag in message['semantic_tags']:
                self.assertEqual(tag['scope'], 'individual_observation')
                self.assertNotIn('synthetic-', json.dumps(tag))

    def test_native_bash_status_json_token_seeds_existing_reuse_marker(self):
        target = 'http://127.0.0.1/document'
        events = [
            event('call', 'tool.start', 0, call_id='c', tool='Bash', data={'arguments': {'command': 'curl ' + target}}),
            event('denied', 'tool.end', 1, call_id='c', tool='Bash', data={'result': 'STATUS 401\nBODY {"error":"unauthorized"}'}),
            event('token', 'tool.end', 2, tool='Bash', data={'result': {
                'type': 'tool_result', 'tool_use_id': 'synthetic-login-call',
                'content': [{'type': 'text', 'text': 'status 200\n{"bearer_token":"synthetic-bearer","session_id":"synthetic-session","token_type":"Bearer"}\n\n'}],
            }}),
            event('reuse', 'tool.start', 3, tool='Bash', data={'arguments': {
                'command': "curl -H 'Authorization: Bearer synthetic-bearer' " + target,
            }}),
        ]
        messages = self.build(events)
        self.assertEqual(messages[2]['semantic_tags'][0]['label'], 'Token obtained')
        self.assertEqual(messages[3]['semantic_tags'][0]['label'], 'Exploitation attempt')
        self.assertEqual(messages[3]['semantic_tags'][0]['event_ids'], ['denied', 'token', 'reuse'])

    def test_native_status_json_is_case_insensitive_and_rejects_malformed_exchanges(self):
        for status_line in ('status 200', 'STATUS 200', 'StAtUs 200'):
            with self.subTest(status_line=status_line):
                self.assertEqual(_http_observation(status_line + '\n{\n  "bearer_token": "synthetic-bearer"\n}\n\n'),
                                 (200, {'bearer_token': 'synthetic-bearer'}))
        for malformed in (
            'status 200', 'status 20\n{}', 'status 2000\n{}',
            'status 200 {"bearer_token":"synthetic-bearer"}',
            'status 200\n{', 'status 200\n[{}]', 'status 200\nnull',
            'status 200\n{"bearer_token":"synthetic-bearer"}\ntrailing output',
            'status 200\n{"bearer_token":"synthetic-bearer"}\n{}',
            'prose\nstatus 200\n{"bearer_token":"synthetic-bearer"}',
            'status 200\nstatus 401\n{}', 'status 401\nnot JSON',
        ):
            with self.subTest(malformed=malformed):
                self.assertEqual(_http_observation(malformed), (None, {}))

    def test_artex_credentials_require_all_nonempty_string_fields_and_http_200(self):
        complete = {'username': 'synthetic-user', 'password': 'synthetic-password', 'login_path': '/synthetic-login'}
        for field in complete:
            for invalid in (None, '', False, 1, [], {}):
                with self.subTest(field=field, invalid=invalid):
                    body = {**complete, field: invalid}
                    self.assertEqual(self.build([response('result', 0, body)])[0]['semantic_tags'], [])
            with self.subTest(missing=field):
                body = {key: value for key, value in complete.items() if key != field}
                self.assertEqual(self.build([response('result', 0, body)])[0]['semantic_tags'], [])
        for status in (None, 201, 401, 500, '200'):
            with self.subTest(status=status):
                self.assertEqual(self.build([response('result', 0, complete, status=status)])[0]['semantic_tags'], [])

    def test_combined_artex_credentials_and_token_receive_both_observed_labels(self):
        message = self.build([response('combined', 0, {
            'username': 'synthetic-user', 'password': 'synthetic-password',
            'login_path': '/synthetic-login', 'bearer_token': 'synthetic-bearer',
        })])[0]
        self.assertEqual([tag['label'] for tag in message['semantic_tags']],
                         ['Credentials obtained', 'Token obtained'])
        self.assertTrue(all(tag['event_ids'] == ['combined'] for tag in message['semantic_tags']))

    def test_artex_bearer_tokens_require_observed_success_and_same_target_denial(self):
        target = 'http://127.0.0.1/document'
        baseline = [
            event('call', 'tool.start', 0, call_id='c', data={'arguments': {'url': target}}),
            event('denied', 'tool.end', 1, call_id='c', data={'result': 'STATUS 401\nBODY {"error":"unauthorized"}'}),
        ]
        reuse = event('reuse', 'tool.start', 3, data={'arguments': {'url': target, 'headers': {'Authorization': 'Bearer synthetic-bearer'}}})
        invalid_results = [response('token', 2, {'bearer_token': value}) for value in (None, '', False, 1, [], {})]
        invalid_results.extend([
            response('token', 2, {'bearer_token': 'synthetic-bearer'}, status=401),
            response('token', 2, {'bearer_token': 'synthetic-bearer'}, source_kind='request.history.tool_result'),
            response('token', 2, {'bearer_token': 'synthetic-bearer'}, source_record={'is_error': True}),
        ])
        for result in invalid_results:
            with self.subTest(result=result):
                messages = self.build(baseline + [result, reuse])
                self.assertFalse(any(message['semantic_tags'] for message in messages))
        token = event('token', 'tool.end', 2, data={'result': 'STATUS 200\nBODY {"bearer_token":"synthetic-bearer"}'})
        self.assertEqual(self.build([token, reuse])[-1]['semantic_tags'], [])
        other_target = copy.deepcopy(reuse)
        other_target['data']['arguments']['url'] = 'http://127.0.0.1/other'
        self.assertEqual(self.build(baseline + [token, other_target])[-1]['semantic_tags'], [])
        self.assertEqual(self.build(baseline + [token, reuse])[-1]['semantic_tags'][0]['event_ids'], ['denied', 'token', 'reuse'])

    def test_artex_extensions_do_not_change_generic_token_behavior(self):
        events = [
            response('credentials', 0, {'username': 'synthetic-user', 'password': 'synthetic-password', 'login_path': '/login'}),
            response('bearer', 1, {'bearer_token': 'synthetic-bearer'}),
            response('token', 2, {'token': 'synthetic-token'}),
        ]
        for adapter in ('generic', 'codex'):
            with self.subTest(adapter=adapter):
                messages = self.build(events, adapter=adapter)
                self.assertEqual([message['semantic_tags'] for message in messages[:2]], [[], []])
                self.assertEqual(messages[2]['semantic_tags'][0]['basis'], 'http_200_with_token_body')
        messages = self.build(events)
        self.assertEqual(messages[2]['semantic_tags'][0]['basis'], 'http_200_with_token_body')

    def test_both_observed_token_fields_are_available_for_existing_reuse_detection(self):
        target = 'http://127.0.0.1/document'
        events = [
            event('call', 'tool.start', 0, call_id='c', data={'arguments': {'url': target}}),
            event('denied', 'tool.end', 1, call_id='c', data={'result': 'STATUS 401\nBODY {"error":"unauthorized"}'}),
            response('token', 2, {'token': 'synthetic-token', 'bearer_token': 'synthetic-bearer'}),
        ]
        for value in ('synthetic-token', 'synthetic-bearer'):
            reuse = event('reuse', 'tool.start', 3, data={'arguments': {'url': target, 'headers': {'Authorization': 'Bearer ' + value}}})
            messages = self.build(events + [reuse])
            self.assertEqual(len(messages[2]['semantic_tags']), 1)
            self.assertEqual(messages[-1]['semantic_tags'][0]['event_ids'], ['denied', 'token', 'reuse'])


if __name__ == '__main__':
    unittest.main()
