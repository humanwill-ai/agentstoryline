"""New synthetic frontend entity and compact-card tests; no recorded run fixtures."""
import copy
import json
from pathlib import Path
import subprocess
import unittest

from web.flow import build_flow
from web.pacing import build_smart_pacing
from web.storyline import build_storyline


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'web' / 'flow.js').read_text()
HELPERS = SOURCE[SOURCE.index('function deriveChatEntities('):SOURCE.index('const chatIdentity=')]


def evaluate(expression, **variables):
    script = HELPERS + '\n' + '\n'.join('const ' + key + '=' + json.dumps(value) + ';' for key, value in variables.items())
    script += '\nprocess.stdout.write(JSON.stringify(' + expression + '));'
    return json.loads(subprocess.check_output(['node', '-e', script], text=True))


def message(identifier, kind, owner='worker:one', **fields):
    return {'id': identifier, 'kind': kind, 'actor_id': owner, 'entity_id': owner, 'owner_entity_id': owner, **fields}


class CompactChatTests(unittest.TestCase):
    def compact(self, shown, all_messages=None):
        return evaluate('compactChatEntries(shown,allMessages,m=>m.entity_id,m=>m.owner_entity_id)', shown=shown, allMessages=all_messages if all_messages is not None else shown)

    def test_compact_omits_only_explicit_metadata_flags_and_retains_actions_and_markers(self):
        rows = [message('metadata', 'observation', compact_hidden=True),
                message('context', 'context', compact_hidden=True),
                message('unknown', 'observation'),
                message('string', 'observation', compact_hidden='true'),
                message('call', 'tool.start', compact_hidden=True),
                message('model', 'model.response', compact_hidden=True),
                message('marker', 'observation', compact_hidden=True, semantic_tags=[{'kind': 'success'}])]
        before = copy.deepcopy(rows)
        self.assertEqual([entry['id'] for entry in self.compact(rows)],
                         ['unknown', 'string', 'call', 'model', 'marker'])
        self.assertEqual(rows, before)

    def test_backend_compact_classification_preserves_source_records_and_meaningful_messages(self):
        rows = []
        def add(identifier, kind='observation', table=None, record=None, **data):
            rows.append({'id': identifier, 'kind': kind, 'sequence': len(rows),
                         'data': {'source_table': table, 'source_record': record or {}, **data}})
        for table in ('assets', 'task_asset_links', 'llm_usage', 'tool_usage', 'exploration_edges',
                      'traffic_evidence_snapshots', 'finding_traffic_bindings', 'side_question_sessions'):
            add(table, table=table, record={'status': 'recorded', 'description': 'Synthetic metadata'})
        add('usage', table='activity', record={'kind': 'usage'})
        add('snapshot', source_kind='archive.snapshot')
        add('transcript', source_kind='archive.transcript', record={'type': 'message', 'message': {'role': 'assistant', 'content': 'Historical context'}})
        add('opaque', table='future_metadata', record={'foreign_id': 'synthetic'})
        hidden = {row['id'] for row in rows}
        add('task', table='tasks', record={'goal': 'Synthetic request'})
        add('finding', table='findings', record={'summary': 'Synthetic finding'})
        add('round', table='activity', record={'kind': 'round', 'summary': 'Synthetic planning'})
        add('result', table='activity', record={'kind': 'result', 'detail': 'Synthetic worker result'})
        add('constraint', table='task_constraints', record={'text': 'Synthetic task restriction'})
        add('unknown_prose', table='future_record', record={'summary': 'Useful unfamiliar evidence'})
        add('request', kind='model.request', table='llm_usage', request={})
        add('response', kind='model.response', table='llm_usage', response='Synthetic response')
        add('call', kind='tool.start', table='tool_usage', arguments={})
        add('tool_result', kind='tool.end', table='tool_usage', result='Synthetic output')
        bundle = {'manifest': {'agent': {'name': 'Artex'}}, 'events': rows}
        before = copy.deepcopy(bundle)
        storyline = build_storyline(bundle, {})
        original_storyline = copy.deepcopy(storyline)
        flow = build_flow(bundle, {}, storyline)
        self.assertEqual({m['event_ids'][0] for m in flow['messages'] if m.get('compact_hidden')}, hidden)
        self.assertEqual(len(flow['messages']), len(rows))
        self.assertEqual(next(m['text'] for m in flow['messages'] if m['event_ids'] == ['constraint']),
                         'text: Synthetic task restriction')
        self.assertTrue(all(m.get('compact_hidden_basis') for m in flow['messages'] if m.get('compact_hidden')))
        self.assertEqual(bundle, before)
        self.assertEqual(storyline, original_storyline)
        self.assertEqual({m['event_ids'][0] for e in self.compact(flow['messages']) for m in e['messages']},
                         {row['id'] for row in rows} - hidden)

    def test_generic_unknown_prose_is_visible_but_context_and_content_free_records_are_not(self):
        rows = [{'id': str(index), 'kind': 'observation', 'sequence': index, 'data': data}
                for index, data in enumerate([{'source_record': {'foreign_id': 'synthetic'}},
                                             {'source_record': {'summary': 'Useful generic observation'}},
                                             {'content': 'Generic observation content'},
                                             {'source_table': 'llm_usage', 'source_record': {'summary': 'Unknown adapter prose'}},
                                             {'source_kind': 'archive.transcript', 'source_record': {'type': 'boundary'}}])]
        bundle = {'manifest': {'agent': {'name': 'Generic source'}}, 'events': rows}
        flow = build_flow(bundle, {}, build_storyline(bundle, {}))
        self.assertEqual([m['event_ids'][0] for m in flow['messages'] if m.get('compact_hidden')], ['0', '4'])
        self.assertEqual([e['messages'][0]['event_ids'][0] for e in self.compact(flow['messages'])], ['1', '2', '3'])

    def test_filtered_smart_schedule_has_no_hidden_holds_or_per_record_waits(self):
        rows = [message('first', 'model.response', offset_ms=0, text='Visible response')]
        rows.extend(message('hidden:' + str(i), 'observation', offset_ms=i * 1000,
                            compact_hidden=True, text='Source accounting') for i in range(1, 101))
        rows.append(message('last', 'tool.end', offset_ms=200000, text='Visible output'))
        rows.append(message('tail', 'context', offset_ms=201000, compact_hidden=True, text='Transcript context'))
        pacing = build_smart_pacing({'messages': rows, 'time_mode': 'recorded'})
        before = copy.deepcopy(pacing)
        filtered = evaluate("filteredChatPacing(pacing,rows,rows.filter(compactChatVisible),'recorded')", pacing=pacing, rows=rows)
        self.assertEqual([e['message_id'] for e in filtered['entries']], ['first', 'last'])
        self.assertEqual([e['dwell_ms'] for e in filtered['entries']],
                         [pacing['entries'][0]['dwell_ms'], pacing['entries'][-2]['dwell_ms']])
        self.assertEqual(len([s for s in filtered['segments'] if s['kind'] == 'hold']), 2)
        self.assertLess(filtered['duration_ms'], 8000)
        self.assertEqual(filtered['segments'][-1]['recorded_end_ms'], 201000)
        for a, b in zip(filtered['segments'], filtered['segments'][1:]):
            self.assertEqual(a['end_ms'], b['start_ms'])
            self.assertEqual(a['recorded_end_ms'], b['recorded_start_ms'])
        self.assertEqual(evaluate("filteredChatPacing(pacing,rows,rows,'recorded')", pacing=pacing, rows=rows), before)
        self.assertEqual(pacing, before)

    def test_playback_queue_and_mode_switches_use_visible_messages_at_the_same_recorded_time(self):
        rows = [message('first', 'model.response', offset_ms=0, text='Visible response'),
                message('metadata', 'observation', offset_ms=1000, compact_hidden=True),
                message('usage', 'observation', offset_ms=2000, bookkeeping=True, compact_hidden=True),
                message('last', 'tool.end', offset_ms=3000, text='Visible output')]
        pacing = build_smart_pacing({'messages': rows, 'time_mode': 'recorded'})
        initialization = SOURCE[SOURCE.index('const chatMessageVisible='):SOURCE.index('const zoomLevels=')]
        controls = SOURCE[SOURCE.index('const arrivalSpacing='):SOURCE.index('const seconds=')]
        seek_recorded = SOURCE[SOURCE.index('function storySeekRecorded('):SOURCE.index('function storySeekPointer(')]
        seek = next(line for line in SOURCE.splitlines() if line.startswith('function seek('))
        reveal = next(line for line in SOURCE.splitlines() if line.startswith('function revealOne('))
        script = HELPERS + '\nconst messages=' + json.dumps(rows) + ';const sourcePacing=' + json.dumps(pacing) + ';'
        script += """
const f={time_mode:'recorded'},duration=3000,visible=new Set(),panels=new Map();
let pacingMode='smart',reviewPosition=-1,chatMode='compact',textMode='original',position=-1,startedAt=0,previous=0,lastArrival=0,sounds=0;
function render(){} function stopAudio(){} function pop(){sounds++;}
""" + initialization + controls + seek_recorded + seek + '\n' + reveal
        script += """
const initial=pendingMessage().id;
seek(entryTimes.get('first'));const afterFirst=pendingMessage().id;
reviewPosition=entryTimes.get('last');position=recordedAt(reviewPosition);revealOne(performance.now()+1000);
const compact={position,shown:[...visible],pending:pendingMessage()?.id??null,sounds};
chatMode='full';refreshChatSchedule();const full={position,shown:[...visible],entries:entries.map(e=>e.message_id),sounds};
textMode='explained';refreshChatSchedule();const explained={position,shown:[...visible],entries:entries.map(e=>e.message_id),sounds};
chatMode='compact';refreshChatSchedule();const restored={position,shown:[...visible],sounds};
process.stdout.write(JSON.stringify({initial,afterFirst,compact,full,explained,restored}));
"""
        result = json.loads(subprocess.check_output(['node', '-e', script], text=True))
        self.assertEqual((result['initial'], result['afterFirst']), ('first', 'last'))
        self.assertEqual(result['compact'], {'position': 3000, 'shown': ['first', 'last'], 'pending': None, 'sounds': 1})
        self.assertEqual(result['full'], {'position': 3000, 'shown': ['first', 'metadata', 'usage', 'last'],
                                          'entries': ['first', 'metadata', 'usage', 'last'], 'sounds': 1})
        self.assertEqual(result['explained'], {'position': 3000, 'shown': ['first', 'metadata', 'last'],
                                               'entries': ['first', 'metadata', 'last'], 'sounds': 1})
        self.assertEqual(result['restored'], {'position': 3000, 'shown': ['first', 'last'], 'sounds': 1})

    def test_structural_pair_retains_exact_call_and_result_as_one_owner_action(self):
        data = [message('call', 'tool.start', activity_id='action', parameters_text='{"integer":9007199254740993}', text='Invocation recorded'),
                message('reply', 'tool.end', activity_id='action', text='</script>\nexact synthetic output', event_ids=['evidence'])]
        before = copy.deepcopy(data)
        entries = self.compact(data)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['entity_id'], 'worker:one')
        self.assertTrue(entries[0]['action'])
        self.assertEqual(entries[0]['messages'], before)
        self.assertEqual(data, before)

    def test_visible_prefix_does_not_reveal_unseen_result(self):
        call = message('call', 'tool.start', call_id='unique', text='call')
        reply = message('reply', 'tool.end', call_id='unique', text='unseen evidence')
        entries = self.compact([call], [call, reply])
        self.assertEqual(entries[0]['messages'], [call])
        self.assertNotIn('unseen evidence', json.dumps(entries))
        self.assertEqual(self.compact([call, reply])[0]['id'], entries[0]['id'])

    def test_result_updates_original_card_position_without_reordering_interleaving(self):
        rows = [message('call', 'tool.start', activity_id='a'), message('model', 'model.response', owner='model:one'), message('reply', 'tool.end', activity_id='a')]
        entries = self.compact(rows)
        self.assertEqual([entry['id'] for entry in entries], ['call', 'model'])
        self.assertEqual([m['id'] for m in entries[0]['messages']], ['call', 'reply'])
        self.assertFalse(entries[1]['action'])

    def test_unique_call_id_pairs_across_activity_ids(self):
        rows = [message('call', 'tool.start', call_id='c', activity_id='a'), message('reply', 'tool.end', call_id='c', activity_id='b')]
        self.assertEqual(len(self.compact(rows)), 1)

    def test_shared_call_id_between_owners_creates_no_cross_owner_pair(self):
        rows = [message('call', 'tool.start', owner='worker:one', call_id='c'), message('reply', 'tool.end', owner='worker:two', call_id='c')]
        self.assertEqual(len(self.compact(rows)), 2)

    def test_repeated_or_reversed_correlations_remain_separate(self):
        for rows in ([message('a', 'tool.start', call_id='c'), message('b', 'tool.start', call_id='c'), message('r', 'tool.end', call_id='c')],
                     [message('r', 'tool.end', call_id='c'), message('a', 'tool.start', call_id='c')]):
            self.assertEqual(len(self.compact(rows)), len(rows))

    def test_conflicting_call_ids_are_not_hidden_by_shared_activity(self):
        rows = [message('call', 'tool.start', activity_id='shared', call_id='a'), message('reply', 'tool.end', activity_id='shared', call_id='b')]
        self.assertEqual(len(self.compact(rows)), 2)

    def test_adjacent_tools_without_structural_ids_are_not_paired(self):
        rows = [message('call', 'tool.start', title='Same tool'), message('reply', 'tool.end', title='Same tool')]
        self.assertEqual(len(self.compact(rows)), 2)

    def test_cache_has_identical_visible_result_and_retains_unpaired_messages(self):
        rows = [message('call', 'tool.start', call_id='c'), message('model', 'model.response', owner='model'), message('reply', 'tool.end', call_id='c')]
        cached = evaluate('(()=>{const cache=new Map(compactChatEntries(rows,rows,m=>m.entity_id,m=>m.owner_entity_id).flatMap(e=>e.messages.map(m=>[m.id,e.id])));return compactChatEntries(rows,rows,m=>m.entity_id,m=>m.owner_entity_id,cache);})()', rows=rows)
        self.assertEqual(cached, self.compact(rows))

    def test_explicit_entities_keep_model_speaker_separate_from_owner(self):
        flow = {'entities': [{'id': 'agent', 'display_name': 'Recorded agent', 'actor_ids': ['source']}, {'id': 'model', 'kind': 'model', 'display_name': 'Synthetic model', 'actor_ids': ['model-source']}],
                'actors': [{'id': 'source', 'role': 'source'}, {'id': 'model-source', 'role': 'model'}],
                'messages': [{'id': 'request', 'kind': 'model.request', 'actor_id': 'source', 'entity_id': 'agent', 'owner_entity_id': 'agent'}, {'id': 'response', 'kind': 'model.response', 'actor_id': 'model-source', 'entity_id': 'model', 'owner_entity_id': 'agent'}]}
        result = evaluate('(()=>{const identity=deriveChatEntities(flow);return {ids:flow.messages.map(identity.messageEntity),owners:flow.messages.map(identity.messageOwner),entities:[...identity.entities.keys()]};})()', flow=flow)
        self.assertEqual(result, {'ids': ['agent', 'model'], 'owners': ['agent', 'agent'], 'entities': ['agent', 'model']})

    def test_participant_strip_uses_logical_entities_without_discarding_source_identities(self):
        for worker_count, model_count in ((0, 0), (1, 1), (2, 3), (7, 2)):
            with self.subTest(workers=worker_count, models=model_count):
                entities = [
                    {'id': 'coordinator', 'kind': 'coordinator', 'display_name': 'Coordinator', 'primary_lane': True},
                    *[{'id': 'worker:' + str(i), 'kind': 'worker', 'display_name': 'Worker ' + str(i + 1), 'primary_lane': True}
                      for i in range(worker_count)],
                    # Identical display names from different providers are distinct recorded models.
                    *[{'id': 'model:' + str(i), 'kind': 'model', 'display_name': 'Any model', 'provider': 'Provider ' + str(i), 'primary_lane': True}
                      for i in range(model_count)],
                    {'id': 'target', 'kind': 'target', 'display_name': 'Target', 'primary_lane': True},
                    *[{'id': 'record:' + str(i), 'kind': 'records', 'display_name': 'Artex records', 'primary_lane': False}
                      for i in range(3)],
                    {'id': 'unknown-worker', 'kind': 'records', 'display_name': 'Worker record (identity unavailable)', 'primary_lane': False},
                    {'id': 'setup', 'kind': 'setup', 'display_name': 'Goal initializer', 'primary_lane': False},
                    {'id': 'task', 'kind': 'task', 'display_name': 'Original task request', 'primary_lane': False},
                    {'id': 'unused', 'kind': 'worker', 'display_name': 'Unused entity', 'primary_lane': True},
                ]
                for entity in entities:
                    entity['actor_ids'] = ['source:' + entity['id']]
                flow = {'entities': entities,
                        'actors': [{'id': e['actor_ids'][0], 'role': 'source'} for e in entities],
                        'messages': [{'id': e['id'], 'entity_id': e['id'], 'actor_id': e['actor_ids'][0],
                                      'owner_entity_id': 'coordinator', 'target_entity_id': 'target'}
                                     for e in entities if e['id'] not in ('unused', 'target')]}
                before = copy.deepcopy(flow)
                result = evaluate("""(()=>{const identity=deriveChatEntities(flow);return {
                    strip:participantStripEntities(flow,identity.entities,identity.messageEntity,identity.messageOwner),
                    entities:[...identity.entities.values()],actors:flow.actors};})()""", flow=flow)
                self.assertEqual([e['id'] for e in result['strip']],
                                 [e['id'] for e in entities if e['primary_lane'] and e['id'] != 'unused'])
                self.assertEqual(result['entities'], before['entities'])
                self.assertEqual(result['actors'], before['actors'])
                self.assertEqual(flow, before)

    def test_participant_strip_preserves_legacy_entities_without_primary_lane_metadata(self):
        flow = {'actors': [{'id': 'source', 'role': 'source', 'name': 'Unknown record'}],
                'messages': [{'id': 'one', 'actor_id': 'source'}]}
        result = evaluate('(()=>{const identity=deriveChatEntities(flow);return participantStripEntities(flow,identity.entities,identity.messageEntity,identity.messageOwner);})()', flow=flow)
        self.assertEqual([e['name'] for e in result], ['Unknown record'])

    def test_target_label_does_not_duplicate_existing_role_prefix(self):
        result = evaluate("[targetDisplayName({display_name:'Target · synthetic.test'}),targetDisplayName({display_name:'synthetic.test'})]")
        self.assertEqual(result, ['Target · synthetic.test', 'Target · synthetic.test'])

    def test_legacy_fallback_groups_explicit_participant_and_model_records(self):
        actors = [{'id': 'caller', 'role': 'source', 'participant_identity': 'session', 'associated_worker_name': 'Worker 1', 'display_name': 'Worker 1', 'avatar_kind': 'worker'},
                  {'id': 'tool', 'role': 'tool', 'participant_identity': 'session', 'associated_worker_name': 'Worker 1', 'display_name': 'Synthetic tool'},
                  {'id': 'model-a', 'role': 'model', 'display_name': 'Recorded model', 'model': 'synthetic'}, {'id': 'model-b', 'role': 'model', 'display_name': 'Recorded model', 'model': 'synthetic'}]
        flow = {'actors': actors, 'messages': [{'id': a['id'], 'actor_id': a['id']} for a in actors]}
        result = evaluate('(()=>{const identity=deriveChatEntities(flow);return {ids:flow.messages.map(identity.messageEntity),entities:[...identity.entities.values()]};})()', flow=flow)
        self.assertEqual(result['ids'][0], result['ids'][1])
        self.assertEqual(result['ids'][2], result['ids'][3])
        self.assertNotEqual(result['ids'][0], result['ids'][2])
        self.assertEqual(result['entities'][0]['display_name'], 'Worker 1')

    def test_legacy_fallback_never_infers_target_or_worker_from_unknown_source(self):
        flow = {'actors': [{'id': 'source', 'role': 'source', 'name': 'Unknown record'}, {'id': 'tool', 'role': 'tool', 'name': 'HTTP synthetic'}], 'messages': [{'id': 'one', 'actor_id': 'source'}, {'id': 'two', 'actor_id': 'tool'}]}
        result = evaluate('(()=>{const identity=deriveChatEntities(flow);return [...identity.entities.values()];})()', flow=flow)
        self.assertEqual(len(result), 2)
        self.assertFalse(any(e.get('kind') in ('worker', 'target') for e in result))

    def test_backend_contract_drives_worker_model_and_explicit_target_entities(self):
        def event(identifier, kind, sequence, **extra):
            return {'id': identifier, 'kind': kind, 'sequence': sequence,
                    'timestamp': '2026-01-01T00:00:0' + str(sequence) + 'Z',
                    'data': {'source_record': {'worker': 'synthetic-worker', 'session_id': 'synthetic-session', 'model': 'synthetic-model'}, **extra.pop('data', {})}, **extra}
        rows = [event('request', 'model.request', 0, data={'request': {}}),
                event('response', 'model.response', 1, parent_id='request', data={'response': 'Exact synthetic response'}),
                event('call', 'tool.start', 2, tool='SyntheticHTTP', call_id='synthetic-call', data={'arguments': {'url': 'http://127.0.0.1:8123/synthetic'}}),
                event('result', 'tool.end', 3, tool='SyntheticHTTP', call_id='synthetic-call', parent_id='call', data={'result': 'STATUS 200\nBODY {"synthetic":true}'})]
        bundle = {'manifest': {'agent': {'name': 'SyntheticAgent'}}, 'events': rows}
        flow = build_flow(bundle, {}, build_storyline(bundle, {}))
        result = evaluate('(()=>{const identity=deriveChatEntities(flow);const entries=compactChatEntries(flow.messages,flow.messages,identity.messageEntity,identity.messageOwner);return {entries,entities:[...identity.entities.values()]};})()', flow=flow)
        entries = result['entries']
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]['entity_id'], entries[2]['entity_id'])
        self.assertNotEqual(entries[1]['entity_id'], entries[2]['entity_id'])
        self.assertEqual([m['kind'] for m in entries[2]['messages']], ['tool.start', 'tool.end'])
        self.assertEqual(entries[2]['messages'][-1]['text'], 'STATUS 200\nBODY {"synthetic":true}')
        self.assertEqual(entries[2]['messages'][0]['target_entity_id'], entries[2]['messages'][-1]['target_entity_id'])
        self.assertEqual({e['kind'] for e in result['entities']}, {'worker', 'model', 'target'})

    def test_default_and_full_toggle_are_accessible_and_keep_parameters_in_inspector(self):
        html = (ROOT / 'web' / 'flow.html').read_text()
        self.assertIn('data-chat-mode="compact" aria-pressed="true"', html)
        self.assertIn('data-chat-mode="full" aria-pressed="false"', html)
        self.assertIn('aria-label="Message detail"', html)
        self.assertIn("chatMode=preferences?.chatMode==='full'?'full':'compact'", SOURCE)
        self.assertIn("section('Exact parameters',message.parameters_text??message.parameters)", SOURCE)
        self.assertIn("if(chatMode==='full'&&m.parameters", SOURCE)


if __name__ == '__main__':
    unittest.main()
