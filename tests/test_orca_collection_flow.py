"""Synthetic collection identity tests; no captured evidence fixtures."""
import copy
import unittest
from tests.test_orca_flow_presentation import row
from web.flow import build_flow
from web.storyline import build_storyline


class OrcaCollectionFlowTests(unittest.TestCase):
    def build(self):
        events = []
        sessions = []
        for sid, label in [('a', 'exp1-planner'), ('b', 'exp1-worker-i2')]:
            sessions.append(dict(id=sid, run_id='run-' + sid, run_key=label, label=label))
            for seq, kind, model, causes in [(0, 'model.request', 'openai/gpt-6-luna', []),
                                              (1, 'route.decision', None, [0]),
                                              (2, 'model.response', 'gpt-6-luna', [0])]:
                e = row(seq, kind, model, causes)
                e['id'] = sid + str(seq)
                e['sequence'] = len(events)
                e.pop('parent_id', None)
                e['data']['source_record']['attrs']['request_id'] = 'same-request-id'
                e['data']['source_record']['role'] = 'coordinator'
                e['data']['source_session'] = dict(id=sid, run_id='run-' + sid, run_key=label,
                                                   sequence=seq, event_id='e' + str(seq))
                events.append(e)
        bundle = dict(manifest=dict(source=dict(adapter='orcareplay', collection=dict(sessions=sessions))), events=events)
        before = copy.deepcopy(bundle)
        flow = build_flow(bundle, {}, build_storyline(bundle, {}))
        self.assertEqual(bundle, before)
        return flow

    def test_session_participants_and_ownership_survive_shared_model_lane(self):
        flow = self.build()
        self.assertEqual(flow['participants']['agent_count'], 2)
        self.assertEqual(flow['participants']['worker_count'], 0)
        self.assertEqual(flow['participants']['coordinator_count'], 0)
        agents = [e for e in flow['entities'] if e['kind'] == 'agent']
        self.assertEqual({e['display_name'] for e in agents}, {'exp1-planner', 'exp1-worker-i2'})
        self.assertEqual(len([e for e in flow['entities'] if e['kind'] == 'model']), 1)
        owners = {e['source_session_id']: e['id'] for e in agents}
        for m in flow['messages']:
            self.assertEqual(m['owner_entity_id'], owners[m['source_session_id']])
        for m in flow['messages']:
            if m['kind'] == 'model.request':
                self.assertEqual(m['context'][-1]['source_session_id'], m['source_session_id'])
                self.assertEqual(m['context'][-1]['event_ids'], [m['source_session_id'] + '1'])
        self.assertEqual(len(flow['collection']['sessions']), 2)
