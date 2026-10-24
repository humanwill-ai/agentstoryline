"""Synthetic integration coverage for native relationship display projections."""
import copy
import unittest
from tests.test_artex_relationships import anchored_fixture
from web.render import build_payload
from web.storyline import add_artex_relationships
from analysis.explanation_workflow import refresh_flow


def bundle():
    result = anchored_fixture()
    result['manifest'].update(schema_version='0.1', run_id='synthetic-run', agent={'name':'Artex'}, started_at='2026-10-01T00:00:00Z', completeness={'status':'complete','gaps':[]})
    for i, event in enumerate(result['events']):
        event.update(schema_version='0.1', sequence=i, timestamp=f'2026-10-01T00:00:{i:02d}Z')
        event.setdefault('kind', 'observation')
    return result


class ProjectionTests(unittest.TestCase):
    def test_native_edges_anchored_without_mutating_source(self):
        source = bundle()
        before = copy.deepcopy(source)
        payload = build_payload(source, {})
        edges = [e for e in payload['storyline']['edges'] if e['kind']=='artex_relationship']
        self.assertEqual(len(edges), 2)
        proof = next(e for e in edges if e['label']=='proves')
        self.assertEqual((proof['source_event_id'],proof['target_event_id']), ('report-end','prove-start'))
        self.assertEqual((proof['native_source_event_id'],proof['native_target_event_id']), ('goal','intent'))
        self.assertEqual(source, before)
        self.assertEqual(add_artex_relationships(source,payload['storyline']),payload['storyline'])

    def test_saved_old_view_refreshes_and_is_idempotent(self):
        source = bundle()
        payload = build_payload(source,{})
        payload['storyline'].pop('artex_relationships')
        payload['storyline']['edges'] = [e for e in payload['storyline']['edges'] if e['kind']!='artex_relationship']
        fresh = refresh_flow(payload)
        self.assertEqual(len(fresh['storyline']['artex_relationships']['items']),2)
        self.assertEqual(fresh,refresh_flow(fresh))
        self.assertEqual(fresh['bundle'],source)
        self.assertNotIn('artex_relationships',payload['storyline'])

    def test_other_adapters_remain_unchanged(self):
        for adapter in ['codex','orcareplay','otlp']:
            source=bundle();source['manifest']['source']['adapter']=adapter
            payload=build_payload(source,{})
            self.assertNotIn('artex_relationships',payload['storyline'])
            self.assertEqual(add_artex_relationships(source,payload['storyline']),payload['storyline'])
