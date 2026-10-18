"""New synthetic persistence, revision and provenance checks."""
import copy
import tempfile
import unittest
from unittest.mock import patch
from analysis.explanation_profiles import ExplanationProfile
from analysis.explanations import select_message_ids
from analysis.explanations import recording_fingerprint, validate_explanations
from analysis.explanation_store import AnnotationStore


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.bundle = {'manifest': {'run_id': 'test'}, 'events': [{'id': 'a', 'sequence': 1, 'data': {}}, {'id': 'b', 'sequence': 2, 'data': {}}]}
        self.flow = {'messages': [{'id': e['id'], 'event_ids':[e['id']]} for e in self.bundle['events']]}
        self.document = {'version': 1, 'run_id': 'test', 'bundle_sha256': recording_fingerprint(self.bundle), 'provenance': {'method': 'offline-development-worker','model':'synthetic','provider':'offline'}, 'annotations': [{'message_id':'a','text':'Observed item.','basis':'observed_action','event_ids':['a']}]}

    def test_resume_merge_and_restore_preserve_item_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AnnotationStore(directory)
            self.assertIsNone(store.load(self.bundle,self.flow))
            first = store.merge(self.bundle,self.flow,self.document)
            revision = store.history(self.bundle)[0]
            second = copy.deepcopy(self.document)
            second['provenance']['model'] = 'second'
            second['annotations'][0].update(message_id='b',event_ids=['b'])
            merged = store.merge(self.bundle,self.flow,second)
            self.assertEqual(len(merged['annotations']),2)
            self.assertEqual(merged['annotations'][0]['provenance'],first['annotations'][0]['provenance'])
            self.assertEqual(AnnotationStore(directory).load(self.bundle,self.flow),merged)
            self.assertEqual(store.restore(self.bundle,self.flow,revision),first)
            self.assertEqual(len(store.history(self.bundle)),3)
            changed = copy.deepcopy(self.bundle)
            changed['events'][0]['data']['changed'] = True
            self.assertIsNone(store.load(changed,self.flow))

    def test_profile_mismatch_and_credentials_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AnnotationStore(directory)
            result = store.merge(self.bundle,self.flow,self.document)
            result['profile']['version'] = 99
            with self.assertRaises(ValueError): validate_explanations(self.bundle,self.flow,result)
            self.document['annotations'][0]['text'] = 'Bearer supersecretcredential'
            with self.assertRaises(ValueError): store.merge(self.bundle,self.flow,self.document)

    def test_legacy_provenance_unknown_and_profile_evolution(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AnnotationStore(directory)
            legacy = store.merge(self.bundle, self.flow, self.document)
            self.assertIsNone(legacy['annotations'][0]['provenance']['created_at'])
            self.assertEqual(legacy['annotations'][0]['provenance']['profile_version'], 0)
            self.assertIn('a', select_message_ids(self.bundle, self.flow, legacy))
            old = copy.deepcopy(legacy)
            old['annotations'][0]['provenance'].update(profile_version=1, created_at='2026-01-01T00:00:00Z')
            with patch('analysis.explanations.profile_for', return_value=ExplanationProfile('generic', 2)):
                self.assertEqual(validate_explanations(self.bundle, self.flow, old), old)
                self.assertIn('a', select_message_ids(self.bundle, self.flow, old))

    def test_seed_full_cumulative_snapshot_over_batch_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AnnotationStore(directory)
            seed = store.merge(self.bundle, self.flow, self.document)
            bundle = copy.deepcopy(self.bundle)
            bundle['events'] = [{'id': str(i), 'sequence':i, 'data':{}} for i in range(45)]
            flow = {'messages': [{'id':e['id'],'event_ids':[e['id']]} for e in bundle['events']]}
            seed['bundle_sha256'] = recording_fingerprint(bundle)
            template = seed['annotations'][0]
            seed['annotations'] = [dict(copy.deepcopy(template), message_id=e['id'],event_ids=[e['id']]) for e in bundle['events']]
            self.assertEqual(len(store.seed(bundle,flow,seed)['annotations']),45)
            with self.assertRaises(ValueError): store.merge(bundle,flow,seed)

    def test_seed_upgrades_legacy_current_before_combining(self):
        import json
        from pathlib import Path
        from analysis.explanation_store import merge_documents
        with tempfile.TemporaryDirectory() as directory:
            store = AnnotationStore(directory)
            folder = Path(directory) / 'annotations' / recording_fingerprint(self.bundle)
            folder.mkdir(parents=True)
            (folder / 'current.json').write_text(json.dumps(self.document))
            other = copy.deepcopy(self.document)
            other['annotations'][0].update(message_id='b', event_ids=['b'])
            newer = merge_documents(self.bundle, self.flow, None, other)
            merged = store.seed(self.bundle, self.flow, newer)
            self.assertEqual([item['message_id'] for item in merged['annotations']], ['a','b'])
            self.assertIsNone(merged['annotations'][0]['provenance']['created_at'])

    def test_authentication_prose_persists_but_literal_credentials_do_not(self):
        from analysis.explanation_profiles import redact
        with tempfile.TemporaryDirectory() as directory:
            store = AnnotationStore(directory)
            self.document['annotations'][0]['text'] = 'Requests a document with a bearer credential.'
            saved = store.merge(self.bundle, self.flow, self.document)
            self.assertEqual(saved['annotations'][0]['text'], self.document['annotations'][0]['text'])
            self.document['annotations'][0]['text'] = 'Bearer actual-secret-key-123'
            with self.assertRaises(ValueError): store.merge(self.bundle, self.flow, self.document)
            self.assertEqual(redact({'Authorization':'Bearer credential'}), {'Authorization':'[REDACTED]'})
            self.assertEqual(redact('Basic authentication'), 'Basic authentication')
