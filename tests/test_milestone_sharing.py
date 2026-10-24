"""Synthetic sharing/reload checks for source and optional AI milestones."""
import copy
import json
import unittest

from analysis.explanation_store import _upgrade
from analysis.explanations import validate_explanations
from tests.test_sharing import package
from web.sharing import _validate, prepare_share


class MilestoneSharingTests(unittest.TestCase):
    def fixture(self):
        value = package()
        payload = value['payload']
        payload['flow']['messages'][0]['milestones'] = [{
            'label': 'Report generated for CUSTOM-CANARY', 'outcome': 'success', 'event_ids': ['e1'],
            'provenance': 'source_reported', 'basis': 'explicit_source_outcome'}]
        payload['explanations'] = _upgrade(payload['bundle'], payload['explanations'])
        payload['explanations']['annotations'][0]['provenance'].update(
            profile_version=2, created_at='2026-01-01T00:00:00Z')
        payload['explanations']['annotations'][0]['milestones'] = [{
            'label': 'Tests passed for CUSTOM-CANARY', 'outcome': 'success', 'event_ids': ['e1']}]
        return value

    def test_share_json_reload_preserves_nested_structure_and_separates_ai(self):
        value = self.fixture()
        before = copy.deepcopy(value)
        result = prepare_share(value, {'custom_terms': ['success', 'source_reported',
            'explicit_source_outcome', 'e1', 'CUSTOM-CANARY']})['package']
        reloaded = json.loads(json.dumps(result))
        _validate(reloaded)
        payload = reloaded['payload']
        source = payload['flow']['messages'][0]['milestones'][0]
        inferred = payload['explanations']['annotations'][0]['milestones'][0]
        self.assertEqual(source['outcome'], 'success')
        self.assertEqual(source['provenance'], 'source_reported')
        self.assertEqual(source['basis'], 'explicit_source_outcome')
        self.assertEqual(source['event_ids'], ['e1'])
        self.assertEqual(inferred['outcome'], 'success')
        self.assertEqual(inferred['event_ids'], ['e1'])
        self.assertNotIn('provenance', inferred)
        self.assertNotIn('CUSTOM-CANARY', json.dumps(reloaded))
        self.assertEqual(value, before)
        validate_explanations(payload['bundle'], payload['flow'], payload['explanations'])

    def test_rejects_forged_source_or_ai_milestone_refs_and_verified_claim(self):
        for owner in ('source', 'ai'):
            for mutation in ('unknown_reference', 'source_verified'):
                value = self.fixture()
                payload = value['payload']
                item = (payload['flow']['messages'][0] if owner == 'source'
                        else payload['explanations']['annotations'][0])['milestones'][0]
                if mutation == 'unknown_reference':
                    item['event_ids'] = ['missing']
                else:
                    item['provenance'] = 'source_verified'
                with self.assertRaises(ValueError):
                    prepare_share(value)

    def test_absent_and_empty_opt_in_stay_compatible(self):
        for milestones in (None, []):
            value = package()
            if milestones is not None:
                value['payload']['flow']['messages'][0]['milestones'] = milestones
            reloaded = json.loads(json.dumps(prepare_share(value)['package']))
            _validate(reloaded)
            self.assertEqual(reloaded['payload']['flow']['messages'][0].get('milestones'), milestones)
