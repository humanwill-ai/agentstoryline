"""Synthetic offline handoffs; no provider invocation or evidence execution."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from analysis.explanation_handoff import HandoffError, export_request_set, import_response_set
from analysis.explanation_service import ExplanationService, make_server
from tests.test_explanation_workflow import synthetic_bundle
from web.recording import write_recording
from web.render import build_payload


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        bundle = synthetic_bundle()
        base = bundle['events'][0]
        bundle['events'] = []
        for i in range(45):
            event = copy.deepcopy(base)
            event.update(id='e' + str(i), sequence=i)
            event['data']['raw'] = 'api_key=sk-abcdefghijklmnopqrstuvwxyz0123456789'
            bundle['events'].append(event)
        self.source = self.root / 'source.json'
        write_recording(build_payload(bundle, {}), self.source,
            attachments=[{'name': 'inert.py', 'language': 'python', 'text': 'raise RuntimeError("never")'}])
        self.original = self.source.read_bytes()
        self.service = self.reopen()

    def reopen(self):
        def forbidden(*args, **kwargs):
            raise AssertionError('provider must never be invoked')
        return ExplanationService(self.source, self.root / 'state', provider=forbidden)

    def export(self, **kwargs):
        return export_request_set(self.service, dict(recording_id=self.service.recording_id, scope='all', **kwargs))

    def response(self, request, batches=None, legacy=False):
        documents = []
        for batch in request['requests'] if batches is None else batches:
            document = copy.deepcopy(batch['response_format'])
            document['provenance']['provider'] = 'declared-local-worker'
            document['annotations'] = []
            for target in batch['targets']:
                item = {'message_id': target['message_id'], 'text': 'Reviews synthetic evidence.',
                        'basis': 'observed_action', 'event_ids': [target['own_event_id']]}
                if not legacy:
                    item['provenance'] = dict(document['provenance'], created_at='2026-10-10T10:00:00Z',
                        adapter_id=document['profile']['adapter_id'], profile_version=document['profile']['version'])
                document['annotations'].append(item)
            if legacy:
                document['version'] = 1
                document.pop('profile')
            documents.append(document)
        return {'format': 'traceforge-explanation-response-set', 'version': 1, 'request_id': request['request_id'],
                'bundle_sha256': request['bundle_sha256'], 'responses': documents}

    def apply(self, envelope):
        return import_response_set(self.service, {'recording_id': self.service.recording_id,
                                                 'response_json': json.dumps(envelope)})

    def test_all_batches_partial_resume_restart_idempotent_source_preserved(self):
        request = self.export(api_key='do-not-export', endpoint='https://unused.example')
        self.assertEqual([len(r['targets']) for r in request['requests']], [20, 20, 5])
        self.assertEqual(sum(len(r['targets']) for r in request['requests']), 45)
        raw = json.dumps(request)
        self.assertNotIn('do-not-export', raw)
        self.assertNotIn('unused.example', raw)
        self.assertNotIn('sk-abcdefghijklmnopqrstuvwxyz', raw)
        self.assertEqual(request['requests'][0]['model'], 'gpt-6.1-sol')
        self.assertEqual(self.apply(self.response(request, request['requests'][:1]))['total'], 20)
        self.service = self.reopen()
        result = self.apply(self.response(request, request['requests'][1:]))
        self.assertEqual((result['imported'], result['total']), (25, 45))
        history = self.service.store.history(self.service.bundle)
        self.apply(self.response(request))
        self.assertEqual(history, self.service.store.history(self.service.bundle))
        doc = self.service.package['payload']['explanations']
        self.assertEqual(doc['annotations'][0]['provenance']['created_at'], '2026-10-10T10:00:00Z')
        self.assertEqual(self.source.read_bytes(), self.original)
        pending = self.root / 'state' / self.service.recording_id / 'offline-requests' / (request['request_id'] + '.json')
        self.assertEqual(json.loads(pending.read_text()), request)
        with patch('analysis.explanation_handoff.uuid.uuid4') as uuid:
            uuid.return_value.hex = request['request_id']
            with self.assertRaises(HandoffError):
                self.export()
        self.assertEqual(json.loads(pending.read_text()), request)

    def test_invalid_last_batch_is_atomic_and_fixed_errors(self):
        request = self.export()
        for mutation in ('target', 'ref', 'fingerprint', 'method', 'model', 'profile', 'duplicate', 'timestamp'):
            with self.subTest(mutation=mutation):
                envelope = self.response(request)
                document = envelope['responses'][-1]
                if mutation == 'target': document['annotations'][0]['message_id'] = 'unrequested-secret'
                if mutation == 'ref': document['annotations'][0]['event_ids'].append('e44')
                if mutation == 'fingerprint': document['bundle_sha256'] = '0' * 64
                if mutation == 'method': document['annotations'][0]['provenance']['method'] = 'provider-api'
                if mutation == 'model': document['provenance']['model'] = 'different'
                if mutation == 'profile': document['profile']['version'] = 0
                if mutation == 'duplicate': envelope['responses'][-1] = envelope['responses'][0]
                if mutation == 'timestamp': document['annotations'][0]['provenance']['created_at'] = 'bad'
                with self.assertRaises(HandoffError) as error:
                    self.apply(envelope)
                self.assertNotIn('unrequested-secret', str(error.exception))
                self.assertNotIn('explanations', self.service.package['payload'])
                self.assertEqual(self.service.store.history(self.service.bundle), [])

    def test_legacy_timestamp_unknown_and_stale_busy_empty_guards(self):
        request = self.export()
        self.apply(self.response(request, legacy=True))
        self.assertTrue(all(a['provenance']['created_at'] is None for a in self.service.package['payload']['explanations']['annotations']))
        for config in ({'recording_id': 'stale'}, {'recording_id': self.service.recording_id}):
            if config['recording_id'] != 'stale': self.service.status['state'] = 'running'
            with self.assertRaises(HandoffError): export_request_set(self.service, config)
            with self.assertRaises(HandoffError): import_response_set(self.service, dict(config, response_json='{}'))
        self.service.status['state'] = 'idle'
        with self.assertRaises(HandoffError):
            export_request_set(self.service, {'recording_id': self.service.recording_id, 'scope': 'interval', 'start_ms': 1e10, 'end_ms': 1e10})

    def test_adaptive_export_preserves_all_targets(self):
        from analysis.explanations import build_explanation_request
        def bounded(bundle, flow, ids, *, model):
            if len(ids) > 3:
                raise ValueError('explanation request exceeds 512000 UTF-8 bytes; select fewer messages')
            return build_explanation_request(bundle, flow, ids, model=model)
        with patch('analysis.explanation_service.build_explanation_request', side_effect=bounded):
            request = self.export()
        targets = [t['message_id'] for r in request['requests'] for t in r['targets']]
        self.assertEqual(len(targets), 45)
        self.assertEqual(len(set(targets)), 45)
        self.assertTrue(all(len(r['targets']) <= 3 for r in request['requests']))
        self.assertEqual(self.apply(self.response(request))['total'], 45)

    def test_export_limit_before_write(self):
        with patch('analysis.explanation_handoff.MAX_HANDOFF_BYTES', 100):
            with self.assertRaises(HandoffError): self.export()
        self.assertFalse((self.root / 'state' / self.service.recording_id / 'offline-requests').exists())

    def test_malformed_unknown_and_standalone_rejected(self):
        request = self.export()
        envelope = self.response(request)
        for raw in ('{"format":1,"format":2}', '{"x":NaN}', json.dumps(envelope['responses'][0])):
            with self.assertRaises(HandoffError):
                import_response_set(self.service, {'recording_id': self.service.recording_id, 'response_json': raw})
        for request_id in ('../escape', 'a' * 32):
            invalid = copy.deepcopy(envelope)
            invalid['request_id'] = request_id
            with self.assertRaises(HandoffError): self.apply(invalid)
        invalid = copy.deepcopy(envelope)
        invalid['bundle_sha256'] = 'a' * 64
        with self.assertRaises(HandoffError): self.apply(invalid)

    def test_http_routes_origin_token_stale_and_success(self):
        server = make_server(self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = 'http://127.0.0.1:' + str(server.server_port)
        def post(path, value, token=None):
            headers = {'Content-Type': 'application/json', 'Origin': url,
                       'X-TraceForge-Token': token or self.service.token}
            req = urllib.request.Request(url + path, json.dumps(value).encode(), headers)
            with urllib.request.urlopen(req, timeout=3) as response: return json.load(response)
        value = {'scope': 'all', 'recording_id': self.service.recording_id}
        with self.assertRaises(urllib.error.HTTPError) as error: post('/api/offline-request', value, 'wrong')
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as error: post('/api/offline-request', dict(value, recording_id='stale'))
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
        request = post('/api/offline-request', value)
        result = post('/api/offline-import', {'recording_id': self.service.recording_id, 'response_json': json.dumps(self.response(request))})
        self.assertEqual(result['total'], 45)
