"""Synthetic milestone inference contracts; no provider calls."""
import copy
import tempfile
import unittest
from analysis.explanations import build_explanation_request, validate_explanations, select_message_ids, validate_request_references
from analysis.explanation_store import AnnotationStore, _upgrade
from tests import test_explanations as fixtures

class MilestoneEnrichmentTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.ExplanationTests()
        fixture.setUp()
        self.bundle, self.flow = fixture.bundle, fixture.flow
        self.document = _upgrade(self.bundle, fixture.document)
        self.item = self.document['annotations'][0]
        self.item['provenance'].update(profile_version=2, created_at='2026-10-24T00:00:00Z')
        self.item['milestones'] = [{'label': 'Inspection attempted', 'outcome': 'attempt', 'event_ids': ['call']}]

    def test_optional_contract_and_strict_invalid_nested_data(self):
        validate_explanations(self.bundle, self.flow, self.document)
        for milestones in (None, [{}], self.item['milestones'] * 4,
                [{'label': 'x', 'outcome': 'verified', 'event_ids': ['call']}],
                [{'label': 'x', 'outcome': 'success', 'event_ids': ['prior']}],
                [{'label': 'x', 'outcome': 'success', 'event_ids': ['call', 'unrelated']}],
                [{'label': 'x'*121, 'outcome': 'success', 'event_ids': ['call']}],
                [{'label': 'x', 'outcome': 'success', 'event_ids': ['call'], 'actor': 'invented'}]):
            candidate = copy.deepcopy(self.document)
            candidate['annotations'][0]['milestones'] = milestones
            with self.assertRaises(ValueError):
                validate_explanations(self.bundle, self.flow, candidate)
        self.item['milestones'] = []
        validate_explanations(self.bundle, self.flow, self.document)
        del self.item['milestones']
        validate_explanations(self.bundle, self.flow, self.document)
        self.document['profile']['version'] = 1
        validate_explanations(self.bundle, self.flow, self.document)

    def test_opt_in_prompt_and_context_restrictions(self):
        request = build_explanation_request(self.bundle, self.flow, ['message:call'], model='test')
        self.assertNotIn('milestones', request['response_format']['annotations'][0])
        with self.assertRaises(ValueError):
            validate_request_references(self.document, request)
        request = build_explanation_request(self.bundle, self.flow, ['message:call'], model='test', include_milestones=True)
        self.assertIn('never verified', request['instructions'])
        self.assertIn('source claim alone', request['instructions'])
        validate_request_references(self.document, request)
        omitted = copy.deepcopy(self.document)
        del omitted['annotations'][0]['milestones']
        with self.assertRaises(ValueError):
            validate_request_references(omitted, request)
        request['targets'][0]['evidence'] = [{'id': 'prior'}]
        with self.assertRaises(ValueError):
            validate_request_references(self.document, request)
        for value in ('true', 1, None):
            with self.assertRaises(ValueError):
                build_explanation_request(self.bundle, self.flow, ['message:call'], model='test', include_milestones=value)

    def test_missing_selection_and_persistent_source_isolation(self):
        original = copy.deepcopy(self.bundle)
        self.item.pop('milestones')
        self.assertIn('message:call', select_message_ids(self.bundle, self.flow, self.document, include_milestones=True))
        self.item['milestones'] = []
        self.assertNotIn('message:call', select_message_ids(self.bundle, self.flow, self.document, include_milestones=True))
        self.item['milestones'] = [{'label': 'Inspection attempted', 'outcome': 'attempt', 'event_ids': ['call']}]
        with tempfile.TemporaryDirectory() as directory:
            store = AnnotationStore(directory)
            store.merge(self.bundle, self.flow, self.document)
            self.assertEqual(store.load(self.bundle, self.flow)['annotations'][0]['milestones'], self.item['milestones'])
            other = copy.deepcopy(self.bundle)
            other['manifest']['run_id'] = 'other'
            self.assertIsNone(store.load(other, self.flow))
        self.assertEqual(self.bundle, original)

    def test_offline_enrichment_export_import_reload_and_unrequested_rejection(self):
        from tests import test_explanation_handoff as handoffs
        from analysis.explanation_handoff import HandoffError
        fixture = handoffs.HandoffTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        request = fixture.export(include_milestones=True)
        self.assertTrue(all(batch['include_milestones'] for batch in request['requests']))
        response = fixture.response(request)
        for document in response['responses']:
            for item in document['annotations']:
                item['milestones'] = [{'label': 'Recorded step attempted', 'outcome': 'attempt', 'event_ids': item['event_ids'][:]}]
        self.assertEqual(fixture.apply(response)['imported'], 45)
        fixture.service = fixture.reopen()
        annotations = fixture.service.package['payload']['explanations']['annotations']
        self.assertEqual(len(annotations), 45)
        self.assertTrue(all(a['milestones'][0]['outcome'] == 'attempt' for a in annotations))
        self.assertEqual(fixture.source.read_bytes(), fixture.original)
        no_opt = fixture.export()
        rejected = fixture.response(no_opt)
        for document in rejected['responses']:
            for item in document['annotations']:
                item['milestones'] = [{'label': 'Unexpected suggestion', 'outcome': 'unconfirmed', 'event_ids': item['event_ids'][:]}]
        with self.assertRaises(HandoffError):
            fixture.apply(rejected)
        self.assertEqual(fixture.service.package['payload']['explanations']['annotations'], annotations)

    def test_api_worker_uses_opt_in_and_rejects_unrequested_suggestions(self):
        from tests import test_explanation_handoff as handoffs
        fixture = handoffs.HandoffTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        service = fixture.service
        seen = []
        def provider(endpoint, model, key, request):
            seen.append(request['include_milestones'])
            doc = copy.deepcopy(request['response_format'])
            doc['annotations'] = [{'message_id': t['message_id'], 'text': 'Synthetic step.',
                'basis': 'observed_action', 'event_ids': [t['own_event_id']],
                'milestones': [{'label': 'Synthetic step attempted', 'outcome': 'attempt', 'event_ids': [t['own_event_id']]}]}
                for t in request['targets']]
            return doc
        service.provider = provider
        service.config = {'model': 'fake', 'endpoint': 'http://unused', 'api_key': '', 'provider_label': 'fake', 'include_milestones': True}
        service.pending = [service.flow['messages'][0]['id']]
        service._work()
        self.assertEqual(service.status['state'], 'complete')
        self.assertEqual(seen, [True])
        saved = copy.deepcopy(service.package['payload']['explanations'])
        service.config['include_milestones'] = False
        service.pending = [service.flow['messages'][1]['id']]
        service._work()
        self.assertEqual(service.status['state'], 'failed')
        self.assertEqual(service.package['payload']['explanations'], saved)
