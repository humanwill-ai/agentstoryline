"""New synthetic presentation checks; no private run evidence."""
import copy
import unittest
import json
import subprocess
from pathlib import Path

from web.flow import build_flow
from web.storyline import build_storyline


def event(eid, kind, sequence, timestamp=None, **fields):
    return {'id': eid, 'kind': kind, 'sequence': sequence, 'timestamp': timestamp, 'data': {}, **fields}


class FlowTests(unittest.TestCase):
    def build(self, events):
        bundle = {'manifest': {'agent': {'name': 'Synthetic agent'}}, 'events': events}
        before = copy.deepcopy(bundle)
        result = build_flow(bundle, {}, build_storyline(bundle, {}))
        self.assertEqual(before, bundle)
        return result

    def test_attempt_markers_require_response_evidence_and_observed_token_reuse(self):
        events = [
            event('baseline-call', 'tool.start', -1, call_id='baseline-id', data={'arguments': {'url': 'http://127.0.0.1/document'}}),
            event('baseline', 'tool.end', 0, call_id='baseline-id', data={'result': 'STATUS 401\nBODY {"error":"unauthorized"}'}),
            event('token', 'tool.end', 1, data={'result': 'STATUS 200\nBODY {"token":"synthetic-fresh"}'}),
            event('attempt', 'tool.start', 2, data={'arguments': {'url': 'http://127.0.0.1/document', 'headers': {'Authorization': 'Bearer synthetic-fresh'}}}),
            event('document', 'tool.end', 3, data={'result': 'STATUS 200\nBODY {"doc_id":"synthetic-doc","content":"Synthetic document","proof_marker":"synthetic-proof"}'}),
        ]
        messages = self.build(events)['messages'][1:]
        self.assertEqual([[(t['kind'], t['label']) for t in m['semantic_tags']] for m in messages],
                         [[], [('success', 'Token obtained')], [('danger', 'Exploitation attempt')], [('success', 'Document retrieved')]])
        self.assertEqual(messages[2]['semantic_tags'][0]['event_ids'], ['baseline', 'token', 'attempt'])
        self.assertTrue(all(t['scope'] == 'individual_observation' for m in messages for t in m['semantic_tags']))

    def test_markers_do_not_read_history_prompts_provider_status_or_generic_completion(self):
        events = [
            event('request', 'model.request', 0, data={'request': {'messages': [{'content': 'Exploitation succeeded STATUS 200'}]}}),
            event('response', 'model.response', 1, data={'response': {'text': 'Vulnerability identified', 'traceforge': {'attempts': [{'status': 200}]}}, 'source_record': {'status': 'ok'}}),
            event('complete', 'tool.end', 2, data={'result': 'completed successfully', 'recording_metadata': {'completion': 'complete'}}),
            event('guessed', 'tool.start', 3, data={'arguments': {'headers': {'Authorization': 'Bearer unobserved'}}}),
            event('error', 'tool.end', 4, data={'outcome': 'success', 'result': {'is_error': True, 'content': [{'text': 'STATUS 200\nBODY {"token":"synthetic-error"}'}]}}),
            event('fact', 'tool.start', 5, data={'arguments': {'summary': 'Authorization: Bearer unobserved'}}),
            event('public', 'tool.end', 6, data={'result': 'STATUS 200\nBODY {"ok":true}'}),
        ]
        self.assertTrue(all(not m['semantic_tags'] for m in self.build(events)['messages']))

    def test_credential_reuse_needs_a_denied_baseline_at_the_same_target(self):
        token = event('token', 'tool.end', 1, data={'result': 'STATUS 200\nBODY {"token":"synthetic-fresh"}'})
        attempt = event('attempt', 'tool.start', 2, data={'arguments': {'url': 'http://127.0.0.1/document', 'headers': {'Authorization': 'Bearer synthetic-fresh'}}})
        self.assertFalse(self.build([token, attempt])['messages'][-1]['semantic_tags'])
        unrelated = [
            event('other-call', 'tool.start', -1, call_id='other', data={'arguments': {'url': 'http://127.0.0.1/other'}}),
            event('other-result', 'tool.end', 0, call_id='other', data={'result': 'STATUS 401\nBODY {"error":"unauthorized"}'}),
        ]
        self.assertFalse(self.build(unrelated + [token, attempt])['messages'][-1]['semantic_tags'])
        fact = event('fact', 'tool.start', 3, data={'arguments': {'summary': 'Authorization: Bearer synthetic-fresh'}})
        self.assertFalse(self.build(unrelated + [token, fact])['messages'][-1]['semantic_tags'])

    def test_explicit_attempt_phase_and_outcome_are_preserved_as_marker_basis(self):
        messages = self.build([
            event('phase', 'tool.start', 0, data={'phase': 'exploitation'}),
            event('outcome', 'tool.end', 1, data={'outcome': 'succeeded', 'result': 'recorded attempt outcome'}),
        ])['messages']
        self.assertEqual(messages[0]['semantic_tags'][0]['basis'], 'recorded_phase')
        self.assertEqual(messages[1]['semantic_tags'][0]['basis'], 'recorded_attempt_outcome')

    def test_storyline_markers_group_only_existing_tags_on_the_same_activity(self):
        source = (Path(__file__).resolve().parents[1] / 'web' / 'flow.js').read_text()
        helper = next(line for line in source.splitlines() if line.startswith('function storyMarkerEntries('))
        cases = [
            {'id': 'call', 'activity_id': 'a', 'semantic_tags': [{'kind': 'danger', 'label': 'Exploitation attempt', 'event_ids': ['baseline', 'call']}]},
            {'id': 'result', 'activity_id': 'a', 'semantic_tags': [{'kind': 'success', 'label': 'Document retrieved', 'event_ids': ['result']}]},
            {'id': 'other', 'activity_id': 'b', 'semantic_tags': [{'kind': 'success', 'label': 'Token obtained'}]},
            {'id': 'plain', 'activity_id': 'a', 'text': 'successful exploitation'},
        ]
        script = helper + '\nconst messages=' + json.dumps(cases) + ';process.stdout.write(JSON.stringify([storyMarkerEntries(messages,"a"),storyMarkerEntries(messages,"missing")]));'
        grouped, missing = json.loads(subprocess.check_output(['node', '-e', script], text=True))
        self.assertEqual([entry['message']['id'] for entry in grouped], ['call', 'result'])
        self.assertEqual(grouped[0]['tag']['event_ids'], ['baseline', 'call'])
        self.assertEqual(missing, [])

    def test_explicit_correlation_keeps_call_and_result_messages(self):
        start = event('call', 'tool.start', 0, '2026-01-01T00:00:00Z', call_id='c', tool='Bash', data={'arguments': {'command': 'synthetic'}})
        end = event('result', 'tool.end', 1, '2026-01-01T00:00:02Z', call_id='c', tool='Bash', parent_id='call', data={'result': 'exact result'})
        view = self.build([start, end])
        a, b = view['messages']
        self.assertEqual(a['activity_id'], b['activity_id'])
        self.assertEqual([a['kind'], b['kind']], ['tool.start', 'tool.end'])
        self.assertEqual(b['text'], 'exact result')
        self.assertEqual(view['duration_ms'], 2000)
        self.assertEqual(view['time_mode'], 'recorded')
        end.pop('parent_id')
        view = self.build([start, end])
        self.assertNotEqual(*[m['activity_id'] for m in view['messages']])

    def test_invoking_agent_is_shared_and_tool_use_response_is_not_blank(self):
        request = event('request', 'model.request', 0, data={'source_record': {'model': 'synthetic-model', 'worker': 'w'}, 'request': {}})
        tool = event('call', 'tool.start', 1, tool='Bash', data={'source_record': {'worker': 'w'}, 'arguments': {'command': 'synthetic'}})
        response = event('response', 'model.response', 2, data={'source_record': {'model': 'synthetic-model', 'worker': 'w'}, 'response': {'text': '', 'content': [{'type': 'tool_use', 'name': 'Bash', 'id': 'synthetic-id', 'input': {'command': 'synthetic'}}]}})
        view = self.build([request, tool, response])
        self.assertEqual(view['messages'][0]['actor_id'], view['messages'][1]['actor_id'])
        self.assertIn('Proposed tool: Bash', view['messages'][2]['text'])
        self.assertIsNone(view['messages'][2]['parameters'])
        self.assertEqual(next(a for a in view['actors'] if a['role'] == 'source')['name'], 'w')
        self.assertEqual(next(a for a in view['actors'] if a['role'] == 'model')['name'], 'Recorded model')

    def test_previews_keep_values_without_turning_status_into_outcome(self):
        observation = event('observation', 'observation', 0, data={'source_table': 'explorations', 'source_record': {'status': 'pending', 'description': 'synthetic details', 'raw_response': 'private-sized blob'}})
        tool = event('tool', 'tool.start', 1, tool='Synthetic', data={'arguments': {'command': '</script>', 'limit': 0, 'exact_integer': 9007199254740993}})
        view = self.build([observation, tool])
        self.assertEqual(view['messages'][0]['text'], 'description: synthetic details\nstatus: pending')
        self.assertNotIn('raw_response', view['messages'][0]['text'])
        self.assertEqual(view['messages'][1]['parameters'], {'command': '</script>', 'limit': 0, 'exact_integer': 9007199254740993})
        self.assertEqual(view['messages'][1]['text'], 'Invocation requested: Synthetic')
        self.assertIn('9007199254740993', view['messages'][1]['parameters_text'])
        self.assertEqual(view['messages'][0]['text_basis'], 'presentation_summary')

    def test_response_strings_lists_and_empty_observation_content(self):
        for response, expected in [('exact response', 'exact response'), ([{'type': 'text', 'text': 'first'}, 'second'], 'first\nsecond')]:
            view = self.build([event('response', 'model.response', 0, data={'response': response})])
            self.assertEqual(view['messages'][0]['text'], expected)
        view = self.build([event('observation', 'observation', 0, data={'content': ''})])
        self.assertEqual(view['messages'][0]['text'], 'Recorded source record; inspect original evidence.')

    def test_missing_and_reversed_time_have_explicit_monotonic_review_schedule(self):
        for stamps in [(None, None), ('2026-01-01T00:00:02Z', '2026-01-01T00:00:00Z')]:
            view = self.build([event(str(i), 'observation', i, stamp) for i, stamp in enumerate(stamps)])
            self.assertEqual(view['time_mode'], 'review_sequence')
            self.assertEqual([m['offset_ms'] for m in view['messages']], [0, 1000])
            self.assertEqual([m['timestamp'] for m in view['messages']], list(stamps))
            self.assertTrue(all(m['playback_timing_basis'] == 'review_sequence_not_elapsed_time' for m in view['messages']))

    def test_actor_identity_is_stable_and_request_sender_is_source(self):
        request = event('request', 'model.request', 0, data={'source_record': {'model': 'synthetic-model', 'worker': 'recorded-worker'}, 'request': {}})
        response = event('response', 'model.response', 1, data={'source_record': {'model': 'synthetic-model', 'worker': 'recorded-worker'}, 'response': {'text': 'exact'}})
        view = self.build([request, response])
        roles = {a['id']: a['role'] for a in view['actors']}
        self.assertEqual([roles[m['actor_id']] for m in view['messages']], ['source', 'model'])
        extra = event('extra', 'observation', -1, data={'agent_id': 'different'})
        second = self.build([extra, request, response])
        self.assertEqual(view['messages'][0]['actor_id'], second['messages'][1]['actor_id'])
        self.assertTrue(all(a['provenance'] == 'observed' for a in view['actors']))

    def test_hostile_strings_and_request_history_are_exact_context(self):
        hostile = '</script><img src=x onerror=alert(1)>'
        request = event('request', 'model.request', 0, data={'request': {'system': hostile, 'messages': [{'role': 'user', 'content': hostile}]}})
        history = event('history', 'observation', 1, parent_id='request', data={'source_kind': 'request.history.tool_result', 'block': {'content': hostile}})
        view = self.build([request, history])
        self.assertEqual(len(view['messages']), 1)
        message = view['messages'][0]
        self.assertEqual(message['text'], 'Recorded request · 1 prior messages · tool count unavailable')
        self.assertIsNone(message['parameters'])
        self.assertEqual(message['context'][0]['text'], hostile)
        self.assertEqual(message['context'][-1]['text'], hostile)
        self.assertEqual(message['context'][-1]['event_ids'], ['history'])
        self.assertEqual(message['provenance'], 'observed')

    def test_participant_roles_and_models_are_evidence_bounded(self):
        def record(eid, seq, session=None, worker='worker', kind='model.request', **extra):
            fields = {'worker': worker, **extra}
            if session:
                fields['session_id'] = session
            return event(eid, kind, seq, data={'source_record': fields, 'request': {}, 'response': 'synthetic'})
        view = self.build([
            record('one', 0, 'session-one'), record('two', 1, 'session-two'),
            record('unknown', 2), record('reporter', 3, 'report-session', 'reporter'),
            record('coordinator', 4, 'planner-session', 'planner'),
            record('model', 5, 'session-one', kind='model.response', model='offline-file-bridge', profile_name='gpt-6.1-sol-caller-declared'),
            record('unknown-model', 6, 'session-two', kind='model.response', model='synthetic-local-model'),
        ])
        self.assertEqual(view['participants']['worker_count'], 2)
        self.assertEqual(view['participants']['coordinator_count'], 1)
        self.assertEqual([w['display_name'] for w in view['participants']['workers']], ['Worker 1', 'Worker 2'])
        self.assertNotEqual(*[w['accent'] for w in view['participants']['workers']])
        model, unknown = [a for a in view['actors'] if a['role'] == 'model']
        self.assertEqual(model['display_name'], 'GPT-6.1 Sol')
        self.assertEqual(model['model'], 'offline-file-bridge')
        self.assertEqual(model['provider'], 'OpenAI')
        self.assertEqual(model['display_model_basis'], 'caller_declared_profile')
        self.assertEqual(model['associated_worker_name'], 'Worker 1')
        self.assertEqual(unknown['display_name'], 'synthetic-local-model')
        self.assertIsNone(unknown['provider'])
        self.assertIn('Worker record (identity unavailable)', [a['display_name'] for a in view['actors']])

    def test_generic_agent_requests_have_agent_identity_without_worker_claim(self):
        bundle = {'manifest': {'agent': {'name': 'Codex'}}, 'events': [event('request', 'model.request', 0, data={'request': {}}), event('call', 'tool.start', 1, tool='Synthetic', data={'arguments': {}})]}
        view = build_flow(bundle, {}, build_storyline(bundle, {}))
        self.assertEqual(view['participants']['worker_count'], 0)
        self.assertEqual(len(view['actors']), 1)
        actor = view['actors'][0]
        self.assertEqual(actor['display_name'], 'Codex agent')
        self.assertEqual(actor['display_role'], 'agent')
        self.assertEqual(actor['avatar_kind'], 'agent')
        self.assertNotIn('Artex', actor['display_name'])

    def test_coordinator_labels_follow_distinct_recorded_identities(self):
        rows = [
            event('planner', 'model.request', 0, data={'source_record': {'worker': 'planner', 'session_id': 'planner-one'}, 'request': {}}),
            event('model', 'model.response', 1, data={'source_record': {'session_id': 'planner-one', 'model': 'synthetic-model'}, 'response': 'synthetic'}),
            event('unknown', 'observation', 2, data={'source_record': {'worker': 'coordinator'}}),
        ]
        view = self.build(rows)
        self.assertEqual(view['participants']['coordinator_count'], 1)
        self.assertEqual([c['display_name'] for c in view['participants']['coordinators']], ['Coordinator'])
        self.assertEqual([a['display_name'] for a in view['actors']], ['Coordinator', 'synthetic-model', 'Coordinator record (identity unavailable)'])
        self.assertEqual([a.get('associated_coordinator_name') for a in view['actors']], ['Coordinator', 'Coordinator', None])
        self.assertEqual(view['actors'][-1]['display_role'], 'record')
        self.assertNotIn('participant_identity', view['actors'][-1])

        rows.append(event('second', 'model.request', 3, data={'source_record': {'worker': 'coordinator', 'session_id': 'planner-two'}, 'request': {}}))
        multiple = self.build(rows)
        self.assertEqual(multiple['participants']['coordinator_count'], 2)
        self.assertEqual([c['display_name'] for c in multiple['participants']['coordinators']], ['Coordinator 1', 'Coordinator 2'])
        self.assertEqual([a.get('associated_coordinator_name') for a in multiple['actors']], ['Coordinator 1', 'Coordinator 1', None, 'Coordinator 2'])
        self.assertEqual(multiple['participants']['coordinators'][0]['id'], view['participants']['coordinators'][0]['id'])

    def test_shared_session_preserves_named_workers_and_role_only_records(self):
        records = [
            {'session_id': 'shared', 'worker': 'worker-alpha'},
            {'session_id': 'shared', 'worker': 'worker-beta'},
            {'worker': 'worker'}, {'worker': 'coordinator'}, {},
        ]
        view = self.build([event(str(i), 'observation', i, data={'source_record': record}) for i, record in enumerate(records)])
        self.assertEqual(view['participants']['worker_count'], 2)
        self.assertEqual(view['participants']['coordinator_count'], 0)
        self.assertEqual([a['display_name'] for a in view['actors']], ['Worker 1', 'Worker 2', 'Worker record (identity unavailable)', 'Coordinator record (identity unavailable)', 'Synthetic agent records'])
        self.assertTrue(all(a['avatar_kind'] == 'record' and a['display_role'] == 'record' for a in view['actors'][2:]))
        self.assertEqual(len({w['identity'] for w in view['participants']['workers']}), 2)

    def test_attached_history_does_not_add_participants(self):
        request = event('request', 'model.request', 0, data={'source_record': {'worker': 'worker', 'session_id': 'visible'}, 'request': {}})
        history = event('history', 'observation', 1, parent_id='request', data={'source_kind': 'request.history.tool_result', 'source_record': {'worker': 'worker', 'session_id': 'context-only'}, 'block': {'content': 'synthetic'}})
        view = self.build([request, history])
        self.assertEqual(len(view['actors']), 1)
        self.assertEqual(view['participants']['worker_count'], 1)
        self.assertEqual(view['participants']['coordinator_count'], 0)
        self.assertIn('does not establish', view['participants']['notes'][-1])

    def test_artex_scheduler_slots_are_not_counted_again_for_each_intent(self):
        rows = []
        for index, (session, worker) in enumerate([('exp9-worker-i2', 'work#1'), ('exp9-worker-i3', 'work#2'), ('exp9-worker-i4', 'work#1')]):
            rows.extend([
                event('request' + str(index), 'model.request', index * 2, data={'source_table': 'llm_records', 'source_record': {'worker': 'worker', 'session_id': session}, 'recording_metadata': {'session_id': session}, 'request': {}}),
                event('tool' + str(index), 'tool.start', index * 2 + 1, tool='Synthetic', data={'source_table': 'activity', 'source_record': {'worker': worker, 'exploration_id': 9}, 'recording_metadata': {'session_id': session}, 'arguments': {}}),
            ])
        bundle = {'manifest': {'agent': {'name': 'Artex'}}, 'events': rows}
        before = copy.deepcopy(bundle)
        view = build_flow(bundle, {}, build_storyline(bundle, {}))
        self.assertEqual(bundle, before)
        self.assertEqual(view['participants']['worker_count'], 2)
        actors = {a['id']: a for a in view['actors']}
        names = [actors[m['actor_id']]['display_name'] for m in view['messages']]
        self.assertEqual(names, ['Worker 1', 'Worker 1', 'Worker 2', 'Worker 2', 'Worker 1', 'Worker 1'])
        # Conflicting recorded slot ownership must not silently pick a slot.
        rows.append(event('conflict', 'tool.start', 7, tool='Synthetic', data={'source_table': 'activity', 'source_record': {'worker': 'work#3', 'exploration_id': 9}, 'recording_metadata': {'session_id': 'exp9-worker-i2'}, 'arguments': {}}))
        view = build_flow(bundle, {}, build_storyline(bundle, {}))
        actor = next(a for a in view['actors'] if a['id'] == view['messages'][0]['actor_id'])
        self.assertNotEqual(actor['participant_identity'], 'artex:exp9:work#1')

    def test_namespaced_openai_model_preserves_original_identity(self):
        view = self.build([event('model', 'model.response', 0, data={'source_record': {'model': 'openai/gpt-6-luna'}, 'response': 'synthetic'})])
        actor = view['actors'][0]
        self.assertEqual(actor['model'], 'openai/gpt-6-luna')
        self.assertEqual(actor['display_name'], 'GPT-6 Luna')
        self.assertEqual(actor['avatar_kind'], 'openai')

    def test_artex_goal_initializer_is_not_a_second_coordinator(self):
        rows = [
            event('goal', 'model.request', 0, data={'source_record': {'worker': 'goals', 'session_id': 'exp9-goals'}, 'request': {}}),
            event('goal-tool', 'tool.start', 1, tool='Synthetic', data={'source_record': {'worker': 'planner', 'exploration_id': 9}, 'recording_metadata': {'session_id': 'exp9-goals'}, 'arguments': {}}),
            event('round', 'observation', 2, data={'source_record': {'worker': 'planner', 'exploration_id': 9}}),
            event('planner', 'model.request', 3, data={'source_record': {'worker': 'planner', 'session_id': 'exp9-planner'}, 'request': {}}),
        ]
        bundle = {'manifest': {'agent': {'name': 'Artex'}}, 'events': rows}
        view = build_flow(bundle, {}, build_storyline(bundle, {}))
        self.assertEqual(view['participants']['coordinator_count'], 1)
        actors = {a['id']: a for a in view['actors']}
        self.assertEqual([actors[m['actor_id']]['display_name'] for m in view['messages']], ['Goal initializer', 'Goal initializer', 'Coordinator', 'Coordinator'])

    def test_task_input_requires_recorded_goal_and_analysis_keeps_its_basis(self):
        task = event('task', 'observation', 0, data={'source_table': 'tasks', 'source_record': {'goal': 'synthetic recorded goal'}})
        view = self.build([task])
        self.assertEqual(view['messages'][0]['kind'], 'task.input')
        self.assertEqual(view['messages'][0]['title'], 'Original task request')
        self.assertEqual(view['messages'][0]['text'], 'synthetic recorded goal')
        task['data']['source_record'].pop('goal')
        self.assertEqual(self.build([task])['messages'][0]['kind'], 'observation')

    def test_artex_exploration_description_is_original_task_request(self):
        exploration = event('exploration', 'observation', 0, data={
            'source_table': 'explorations',
            'source_record': {
                'description': 'synthetic original request',
                'goal': 'synthetic derived goal',
                'status': 'open',
                'worker': 'planner',
                'session_id': 'exp9-planner',
            },
        })
        planner = event('planner', 'model.request', 1, data={
            'source_record': {'worker': 'planner', 'session_id': 'exp9-planner'},
            'request': {},
        })
        bundle = {'manifest': {'agent': {'name': 'Artex'}}, 'events': [exploration, planner]}
        before = copy.deepcopy(bundle)
        view = build_flow(bundle, {}, build_storyline(bundle, {}))
        self.assertEqual(bundle, before)
        message = view['messages'][0]
        actor = next(actor for actor in view['actors'] if actor['role'] == 'task')
        self.assertEqual((message['kind'], message['title'], message['text']),
                         ('task.input', 'Original task request', 'synthetic original request'))
        self.assertNotIn('status', message['text'])
        self.assertEqual((actor['display_name'], actor['display_role'], actor['avatar_kind']),
                         ('Original task request', 'task input', 'task'))
        self.assertNotIn('associated_coordinator_name', actor)
        self.assertEqual(view['participants']['coordinator_count'], 1)

    def test_artex_exploration_goal_is_fallback_and_missing_input_stays_observation(self):
        goal = event('goal', 'observation', 0, data={'source_table': 'exploration', 'source_record': {'goal': 'synthetic goal', 'status': 'open'}})
        missing = event('missing', 'observation', 1, data={'source_table': 'explorations', 'source_record': {'description': '', 'status': 'open'}})
        bundle = {'manifest': {'agent': {'name': 'Artex'}}, 'events': [goal, missing]}
        view = build_flow(bundle, {}, build_storyline(bundle, {}))
        self.assertEqual([(message['kind'], message['text']) for message in view['messages']], [
            ('task.input', 'synthetic goal'),
            ('observation', 'status: open'),
        ])

    def test_non_artex_exploration_is_not_assumed_to_be_human_authored(self):
        exploration = event('exploration', 'observation', 0, data={'source_table': 'explorations', 'source_record': {'description': 'synthetic details', 'goal': 'synthetic goal'}})
        self.assertEqual(self.build([exploration])['messages'][0]['kind'], 'observation')

    def test_logical_usage_context_preserves_tool_timing_and_raw_evidence(self):
        record = {'worker': 'work#1', 'exploration_id': 7}
        rows = [
            event('call', 'tool.start', 0, '2026-01-01T00:00:00Z', call_id='search', tool='traffic_search', data={'source_table': 'activity', 'source_record': {**record, 'kind': 'tool_use'}, 'arguments': {'query': 'synthetic'}}),
            event('result', 'tool.end', 1, '2026-01-01T00:00:01Z', call_id='search', tool='traffic_search', parent_id='call', data={'source_table': 'activity', 'source_record': {**record, 'kind': 'tool_result'}, 'result': 'Synthetic evidence'}),
            event('usage', 'observation', 2, '2026-01-01T00:00:02Z', data={'source_table': 'activity', 'source_record': {**record, 'kind': 'usage', 'summary': 'Synthetic accounting'}}),
        ]
        bundle = {'manifest': {'agent': {'name': 'Artex'}}, 'events': rows}
        story = build_storyline(bundle, {})
        before = copy.deepcopy((bundle, story))
        view = build_flow(bundle, {}, story)
        groups = view['logical_storyline']['activity_groups']
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group['label'], 'traffic_search')
        self.assertEqual(group['source_activity_ids'], ['activity:call', 'activity:usage'])
        self.assertEqual(group['event_ids'], ['call', 'result', 'usage'])
        self.assertEqual((group['start_ms'], group['end_ms'], group['timing'], group['count'], group['source_count']), (0, 1000, 'interval', 1, 2))
        self.assertEqual([message['event_ids'] for message in view['messages']], [['call'], ['result'], ['usage']])
        self.assertEqual([message['activity_id'] for message in view['messages']], ['activity:call', 'activity:call', 'activity:usage'])
        self.assertEqual((bundle, story), before)
        self.assertIn('not_causality', group['grouping_basis'][-1])

    def test_logical_usage_needs_preceding_same_entity_within_two_seconds(self):
        for usage_time, worker, folded in [('2026-01-01T00:00:03Z', 'work#1', True), ('2026-01-01T00:00:03.001Z', 'work#1', False), ('2026-01-01T00:00:00Z', 'work#1', False), ('2026-01-01T00:00:02Z', 'work#2', False), (None, 'work#1', False)]:
            with self.subTest(usage_time=usage_time, worker=worker):
                rows = [
                    event('call', 'tool.end', 0, '2026-01-01T00:00:01Z', tool='traffic_search', data={'source_table': 'activity', 'source_record': {'worker': 'work#1', 'exploration_id': 7, 'kind': 'tool_result'}, 'result': 'Synthetic evidence'}),
                    event('usage', 'observation', 1, usage_time, data={'source_table': 'activity', 'source_record': {'worker': worker, 'exploration_id': 7, 'kind': 'usage'}}),
                ]
                bundle = {'manifest': {'agent': {'name': 'Artex'}}, 'events': rows}
                story = build_storyline(bundle, {})
                logical = build_flow(bundle, {}, story)['logical_storyline']
                group = logical['activity_groups'][0]
                self.assertEqual(group['source_activity_ids'], ['activity:call', 'activity:usage'] if folded else ['activity:call'])
                self.assertEqual(logical['omitted_activity_ids'], [] if folded else ['activity:usage'])
                self.assertEqual(len(story['activities']), 2)

    def test_logical_unattributed_usage_is_omitted_without_erasing_source(self):
        rows = [event('usage', 'observation', 0, '2026-01-01T00:00:00Z', data={'source_table': 'activity', 'source_record': {'kind': 'usage'}})]
        bundle = {'manifest': {'agent': {'name': 'Artex'}}, 'events': rows}
        story = build_storyline(bundle, {})
        view = build_flow(bundle, {}, story)
        self.assertEqual(view['logical_storyline']['activity_groups'], [])
        self.assertEqual(view['logical_storyline']['omitted_activity_ids'], ['activity:usage'])
        self.assertEqual(len(view['messages']), 1)
        self.assertEqual(story['activities'][0]['event_ids'], ['usage'])

    def test_logical_usage_attaches_to_nearest_preceding_activity_only_for_artex(self):
        rows = [event(name, 'observation', index, stamp, data={'source_table': 'activity', 'source_record': {'worker': 'work#1', 'exploration_id': 7, 'kind': kind, 'summary': name}}) for index, (name, stamp, kind) in enumerate([
            ('earlier', '2026-01-01T00:00:00Z', 'text'),
            ('nearest', '2026-01-01T00:00:01Z', 'text'),
            ('usage', '2026-01-01T00:00:02Z', 'usage'),
        ])]
        bundle = {'manifest': {'agent': {'name': 'Artex'}}, 'events': rows}
        groups = build_flow(bundle, {}, build_storyline(bundle, {}))['logical_storyline']['activity_groups']
        self.assertEqual([group['source_activity_ids'] for group in groups], [['activity:earlier'], ['activity:nearest', 'activity:usage']])
        bundle['manifest']['agent']['name'] = 'Synthetic agent'
        groups = build_flow(bundle, {}, build_storyline(bundle, {}))['logical_storyline']['activity_groups']
        self.assertEqual([group['source_activity_ids'] for group in groups], [['activity:earlier'], ['activity:nearest'], ['activity:usage']])

    def test_logical_source_activity_labels_use_recorded_kind_and_summary(self):
        cases = [('planner', 'round', 'Inspecting evidence', 'Inspecting evidence'),
                 ('planner', 'round', '', 'Planning'),
                 ('planner', 'result', 'Synthetic result', 'Reviewing evidence'),
                 ('planner', 'text', 'Synthetic instructions', 'Reviewing task requirements'),
                 ('work#1', 'result', 'Synthetic result', 'Reporting results'),
                 ('work#1', 'other', '', 'Recorded activity')]
        rows = [event('record-' + str(index), 'observation', index, '2026-01-01T00:00:0' + str(index) + 'Z', data={'source_table': 'activity', 'source_record': {'worker': worker, 'exploration_id': 7, 'kind': kind, 'summary': summary}}) for index, (worker, kind, summary, expected) in enumerate(cases)]
        bundle = {'manifest': {'agent': {'name': 'Artex'}}, 'events': rows}
        story = build_storyline(bundle, {})
        view = build_flow(bundle, {}, story)
        labels = {group['source_activity_ids'][0]: group['label'] for group in view['logical_storyline']['activity_groups']}
        self.assertEqual([labels['activity:record-' + str(index)] for index in range(len(cases))], [case[3] for case in cases])
        self.assertTrue(all(activity['label'] == 'activity record' for activity in story['activities']))
        self.assertEqual(view['messages'][0]['text'], 'summary: Inspecting evidence')

    def test_logical_overlaps_are_display_envelopes_without_new_causality(self):
        rows = []
        for index, (start, end) in enumerate([(0, 4), (2, 6), (6, 8)]):
            row = {'worker': 'worker', 'session_id': 'worker-session'}
            rows.extend([
                event('call-' + str(index), 'tool.start', index * 2, '2026-01-01T00:00:0' + str(start) + 'Z', tool='Synthetic ' + str(index), call_id=str(index), data={'source_record': row, 'arguments': {}}),
                event('result-' + str(index), 'tool.end', index * 2 + 1, '2026-01-01T00:00:0' + str(end) + 'Z', tool='Synthetic ' + str(index), call_id=str(index), parent_id='call-' + str(index), data={'source_record': row, 'result': 'Synthetic result'}),
            ])
        bundle = {'manifest': {'agent': {'name': 'Synthetic agent'}}, 'events': rows}
        story = build_storyline(bundle, {})
        before = copy.deepcopy((bundle, story))
        logical = build_flow(bundle, {}, story)['logical_storyline']
        groups = logical['activity_groups']
        self.assertEqual([group['source_activity_ids'] for group in groups], [['activity:call-0', 'activity:call-1'], ['activity:call-2']])
        self.assertEqual((groups[0]['start_ms'], groups[0]['end_ms'], groups[0]['timing'], groups[0]['count']), (0, 6000, 'presentation_span', 2))
        self.assertIn('2 activities', groups[0]['label'])
        self.assertIn('not_execution_or_causality', groups[0]['grouping_basis'][-1])
        self.assertEqual(groups[1]['timing'], 'interval')
        self.assertEqual((bundle, story), before)

    def test_logical_groups_keep_dynamic_entity_identity_and_unknown_times(self):
        for count in (0, 1, 3, 7):
            rows = [event('record-' + str(index), 'observation', index, data={'source_record': {'worker': 'worker', 'session_id': 'worker-' + str(index)}}) for index in range(count)]
            view = self.build(rows)
            logical = view['logical_storyline']
            self.assertEqual(len(logical['activity_groups']), count)
            self.assertEqual(len({group['entity_id'] for group in logical['activity_groups']}), count)
            self.assertEqual({group['entity_id'] for group in logical['activity_groups']}, {lane['id'] for lane in logical['lanes']})
            self.assertTrue(all(group['timing'] == 'unknown' and group['start_ms'] is None and group['end_ms'] is None for group in logical['activity_groups']))

    def test_logical_lanes_group_three_workers_and_exact_model_provider_identity(self):
        rows = []
        for i, provider in enumerate(['provider-a', 'provider-a', 'provider-b']):
            record = {'worker': 'worker', 'session_id': 'session-' + str(i), 'model': 'shared-model', 'provider': provider}
            rows.extend([
                event('request-' + str(i), 'model.request', i * 2, call_id='exchange-' + str(i), data={'source_record': record, 'request': {}}),
                event('response-' + str(i), 'model.response', i * 2 + 1, call_id='exchange-' + str(i), parent_id='request-' + str(i), data={'source_record': record, 'response': 'Synthetic reply'}),
            ])
        bundle = {'manifest': {'agent': {'name': 'Synthetic agent'}}, 'events': rows}
        story = build_storyline(bundle, {})
        before = copy.deepcopy((bundle, story))
        view = build_flow(bundle, {}, story)
        self.assertEqual((bundle, story), before)
        workers = [item for item in view['entities'] if item['kind'] == 'worker']
        models = [item for item in view['entities'] if item['kind'] == 'model']
        self.assertEqual(len(workers), 3)
        self.assertEqual([item['recorded_provider'] for item in models], ['provider-a', 'provider-b'])
        self.assertEqual(len([actor for actor in view['actors'] if actor['role'] == 'model']), 3)
        self.assertEqual([lane['kind'] for lane in view['logical_storyline']['lanes']], ['worker'] * 3 + ['model'] * 2)
        request, response = view['messages'][:2]
        self.assertEqual(request['entity_id'], response['owner_entity_id'])
        self.assertEqual(request['target_entity_id'], response['entity_id'])
        self.assertEqual(request['model_entity_id'], view['messages'][2]['model_entity_id'])
        assignment = view['logical_storyline']['activity_entities'][request['activity_id']]
        self.assertEqual(assignment['entity_id'], response['entity_id'])
        self.assertEqual(assignment['owner_entity_id'], request['entity_id'])
        self.assertEqual(assignment['source_lane_id'], request['lane_id'])

    def test_provider_switch_in_one_session_retains_separate_source_actors(self):
        rows = [event(str(i), 'model.response', i, data={
            'model_identity': {'model': 'shared-model', 'model_provider': provider, 'basis': 'recorded_config'},
            'source_record': {'worker': 'worker', 'session_id': 'same-session'}, 'response': 'Synthetic reply',
        }) for i, provider in enumerate(['provider-a', 'provider-b'])]
        view = self.build(rows)
        self.assertEqual(len(view['actors']), 2)
        self.assertEqual([actor['provider'] for actor in view['actors']], ['provider-a', 'provider-b'])
        self.assertEqual(len({message['entity_id'] for message in view['messages']}), 2)
        self.assertEqual(len({message['owner_entity_id'] for message in view['messages']}), 1)

    def test_artex_logical_workers_follow_recorded_slots_across_intents(self):
        rows = []
        for i, slot in enumerate(['work#1', 'work#2', 'work#3', 'work#1']):
            session = 'exp7-worker-intent-' + str(i)
            rows.extend([
                event('request-' + str(i), 'model.request', i * 2, data={'source_record': {'worker': 'worker', 'session_id': session, 'model': 'synthetic-model'}, 'request': {}}),
                event('call-' + str(i), 'tool.start', i * 2 + 1, tool='Synthetic', data={'source_table': 'activity', 'recording_metadata': {'session_id': session}, 'source_record': {'worker': slot, 'exploration_id': 7}, 'arguments': {}}),
            ])
        bundle = {'manifest': {'agent': {'name': 'Artex'}}, 'events': rows}
        view = build_flow(bundle, {}, build_storyline(bundle, {}))
        workers = [entity for entity in view['entities'] if entity['kind'] == 'worker']
        self.assertEqual([worker['identity'] for worker in workers], ['artex:exp7:work#1', 'artex:exp7:work#2', 'artex:exp7:work#3'])
        self.assertEqual(len([entity for entity in view['entities'] if entity['kind'] == 'model']), 1)
        self.assertEqual(view['messages'][0]['entity_id'], view['messages'][6]['entity_id'])
        self.assertEqual(view['messages'][0]['entity_id'], view['messages'][1]['entity_id'])

    def test_task_and_goal_setup_are_annotations_without_extra_worker_lanes(self):
        rows = [
            event('task', 'observation', 0, data={'source_table': 'explorations', 'source_record': {'description': 'Synthetic task'}}),
            event('goals', 'model.request', 1, data={'source_record': {'worker': 'goals', 'session_id': 'exp7-goals', 'model': 'synthetic-model'}, 'request': {}}),
            event('goal-record', 'observation', 2, data={'source_record': {'worker': 'goals', 'session_id': 'exp7-goals', 'status': 'open'}}),
            event('planner', 'observation', 3, data={'source_record': {'worker': 'planner', 'session_id': 'exp7-planner'}}),
        ]
        bundle = {'manifest': {'agent': {'name': 'Artex'}}, 'events': rows}
        view = build_flow(bundle, {}, build_storyline(bundle, {}))
        self.assertEqual([lane['kind'] for lane in view['logical_storyline']['lanes']], ['coordinator', 'model'])
        self.assertEqual([annotation['kind'] for annotation in view['logical_storyline']['annotations']], ['task', 'setup'])
        self.assertEqual(view['participants']['coordinator_count'], 1)
        self.assertNotEqual(view['messages'][1]['entity_id'], view['messages'][3]['entity_id'])

    def test_target_origin_is_evidenced_and_tools_stay_worker_owned(self):
        record = {'worker': 'worker', 'session_id': 'synthetic-worker'}
        start = event('call', 'tool.start', 0, '2026-01-01T00:00:00Z', call_id='synthetic-call', tool='Bash', data={'source_record': record, 'arguments': {'command': "curl 'http://LOCALHOST:80/example?synthetic=yes'"}})
        end = event('result', 'tool.end', 1, '2026-01-01T00:00:02Z', call_id='synthetic-call', parent_id='call', tool='Bash', data={'source_record': record, 'result': 'STATUS 401\nBODY {"error":"synthetic"}'})
        view = self.build([start, end])
        target = next(entity for entity in view['entities'] if entity['kind'] == 'target')
        self.assertEqual(target['display_name'], 'Target · localhost')
        self.assertEqual(target['origin'], 'http://localhost')
        self.assertEqual(target['identity'], 'http://localhost')
        self.assertEqual(view['messages'][0]['entity_id'], view['messages'][1]['entity_id'])
        self.assertNotEqual(target['id'], view['messages'][1]['entity_id'])
        self.assertEqual([message['target_entity_id'] for message in view['messages']], [target['id']] * 2)
        assignment = view['logical_storyline']['activity_entities'][view['messages'][0]['activity_id']]
        self.assertEqual(assignment['target_ms'], 2000)
        self.assertEqual(assignment['entity_id'], view['messages'][0]['owner_entity_id'])
        self.assertEqual(assignment['target_basis'], 'recorded_command_url_with_paired_http_observation')
        self.assertFalse(any(entity['kind'] == 'target' for entity in self.build([start])['entities']))
        end.pop('parent_id')
        self.assertFalse(any(entity['kind'] == 'target' for entity in self.build([start, end])['entities']))

    def test_target_origin_rejects_ambiguous_unsafe_urls_and_prompt_text(self):
        for url in ['https://name:password@localhost/path', 'file:///synthetic', 'http://localhost:bad/path', 'http://localhost/\npath']:
            view = self.build([event('call', 'tool.start', 0, data={'arguments': {'url': url}})])
            self.assertFalse(any(entity['kind'] == 'target' for entity in view['entities']))
        view = self.build([
            event('request', 'model.request', 0, data={'request': {'messages': [{'content': 'http://localhost/prose'}]}}),
            event('response', 'model.response', 1, data={'response': 'STATUS 200\nBODY {"url":"http://localhost/prose"}'}),
        ])
        self.assertFalse(any(entity['kind'] == 'target' for entity in view['entities']))
        start = event('call', 'tool.start', 0, call_id='c', data={'arguments': {'command': 'curl http://localhost/one http://other.local/two'}})
        end = event('result', 'tool.end', 1, call_id='c', parent_id='call', data={'result': 'STATUS 200\nBODY {"ok":true}'})
        self.assertFalse(any(entity['kind'] == 'target' for entity in self.build([start, end])['entities']))

    def test_structured_target_exchange_and_compact_text_retain_original_evidence(self):
        result = {'request': {'url': 'http://127.0.0.1:8123/path'}, 'response': {'status': 200, 'body': {'synthetic': True}}}
        response = 'Synthetic reply ' + 'with exact detail ' * 30
        view = self.build([
            event('result', 'tool.end', 0, tool='SyntheticHTTP', data={'result': result}),
            event('response', 'model.response', 1, data={'response': response}),
        ])
        target = next(entity for entity in view['entities'] if entity['kind'] == 'target')
        self.assertEqual(target['identity'], 'http://127.0.0.1:8123')
        self.assertEqual(target['display_name'], 'Target · 127.0.0.1:8123')
        self.assertEqual(target['origin'], 'http://127.0.0.1:8123')
        self.assertEqual(view['messages'][0]['text'], json.dumps(result, ensure_ascii=False, indent=2))
        self.assertEqual(view['messages'][0]['compact_text'], 'SyntheticHTTP · HTTP 200')
        self.assertEqual(view['messages'][1]['text'], response)
        self.assertLessEqual(len(view['messages'][1]['compact_text']), 180)
        self.assertTrue(view['messages'][1]['compact_text'].endswith('…'))

    def test_logical_agent_and_setup_avatars_ignore_tool_response_actor_category(self):
        bundle = {'manifest': {'agent': {'name': 'Codex'}}, 'events': [
            event('call', 'tool.start', 0, call_id='c', data={'arguments': {}}),
            event('result', 'tool.end', 1, call_id='c', parent_id='call', data={'result': 'Synthetic output'}),
        ]}
        view = build_flow(bundle, {}, build_storyline(bundle, {}))
        agent = next(entity for entity in view['entities'] if entity['kind'] == 'agent')
        self.assertEqual(agent['avatar_kind'], 'agent')
        self.assertEqual(len(agent['actor_ids']), 2)
        bundle = {'manifest': {'agent': {'name': 'Artex'}}, 'events': [
            event('request', 'model.request', 0, data={'source_record': {'worker': 'goals', 'session_id': 'exp7-goals'}, 'request': {}}),
            event('tool', 'tool.start', 1, data={'recording_metadata': {'session_id': 'exp7-goals'}, 'source_record': {'worker': 'planner', 'exploration_id': 7}, 'arguments': {}}),
        ]}
        view = build_flow(bundle, {}, build_storyline(bundle, {}))
        setups = [entity for entity in view['entities'] if entity['kind'] == 'setup']
        self.assertEqual(len(setups), 1)
        self.assertEqual(setups[0]['avatar_kind'], 'agent')
        self.assertEqual(view['messages'][0]['entity_id'], view['messages'][1]['entity_id'])


if __name__ == '__main__':
    unittest.main()
