"""Synthetic loopback service tests; fake providers never make network calls."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from analysis.explanation_service import ExplanationService, make_server
from tests.test_explanation_workflow import synthetic_bundle
from web.render import build_payload
from web.recording import write_recording


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.json'
        write_recording(build_payload(synthetic_bundle(), {}), self.source,
                        attachments=[{'name': 'inert.py', 'language': 'python', 'text': 'raise Exception("never")'}])
        self.original = self.source.read_bytes()
        self.calls = []
        def provider(endpoint, model, key, request):
            self.calls.append((endpoint, model, key))
            doc = copy.deepcopy(request['response_format'])
            doc['annotations'] = [{'message_id': target['message_id'], 'text': 'Inspects synthetic evidence.',
                                    'basis': 'observed_action', 'event_ids': [target['own_event_id']]}
                                   for target in request['targets']]
            return doc
        self.service = ExplanationService(self.source, self.root / 'state', provider=provider)
        self.server = make_server(self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, value=None, headers=None):
        if value is not None:
            value = dict(value)
            value.setdefault('recording_id', self.service.recording_id)
        extra = {'Content-Type': 'application/json'}
        if value is not None:
            extra.update({'Origin': self.url, 'X-TraceForge-Token': self.service.token})
        extra.update(headers or {})
        req = urllib.request.Request(self.url + path, None if value is None else json.dumps(value).encode(), extra)
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.loads(response.read())

    def test_http_viewer_embeds_brand_without_mutating_recording_content(self):
        import base64
        from html.parser import HTMLParser

        class Images(HTMLParser):
            def __init__(self):
                super().__init__()
                self.logo = None
                self.icon = None

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'img' and attrs.get('class') == 'f-logo':
                    self.logo = attrs.get('src')
                if tag == 'link' and attrs.get('rel') == 'icon':
                    self.icon = attrs.get('href')

        marker = '__AGENTSTORYLINE_LOGO__ __AGENTSTORYLINE_FAVICON__'
        self.service.package['attachments'][0]['text'] = marker
        original = copy.deepcopy(self.service.package)
        with urllib.request.urlopen(self.url + '/', timeout=3) as response:
            html = response.read().decode('utf-8')
        parser = Images()
        parser.feed(html)
        assets = Path(__file__).resolve().parents[1] / 'web/assets'
        for uri, filename in ((parser.logo, 'sleepy-replicant-128.png'),
                              (parser.icon, 'sleepy-replicant-32.png')):
            self.assertTrue(uri.startswith('data:image/png;base64,'))
            self.assertEqual(base64.b64decode(uri.split(',', 1)[1]), (assets / filename).read_bytes())
        self.assertIn(marker, html)
        self.assertIn("img-src data:", html)
        self.assertIn("connect-src 'self'", html)
        self.assertIn('<main id="tf-flow" class="f-single f-spotlight">', html)
        self.assertIn('<option value="spotlight" selected>Spotlight</option>', html)
        self.assertEqual(self.service.package, original)
        self.assertEqual(self.calls, [])

    def test_csrf_host_and_origin(self):
        session = self.request('/api/session')
        self.assertEqual(session['service'], 'traceforge-explanations')
        for headers in [{'Host': 'evil.example'}, {'Origin': 'https://evil.example'}]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/session', headers=headers)
            self.assertEqual(error.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/cancel', {}, {'X-TraceForge-Token': 'wrong'})
        self.assertEqual(error.exception.code, 403)
        self.assertEqual(self.calls, [])

    def test_generation_preserves_raw_and_export(self):
        self.request('/api/generate', {'provider': 'openai-compatible', 'endpoint': 'https://gateway.example/v1',
                                     'model': 'synthetic-model', 'api_key': 'test-session-key', 'scope': 'all'})
        for _ in range(100):
            status = self.request('/api/status')
            if status['state'] != 'running':
                break
            time.sleep(.01)
        self.assertEqual(status['state'], 'complete', status)
        self.assertEqual(status['completed'], 1)
        exported = self.request('/api/export')
        original = json.loads(self.original)
        self.assertEqual(exported['payload']['bundle'], original['payload']['bundle'])
        self.assertEqual(exported['attachments'], original['attachments'])
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertNotIn('test-session-key', json.dumps(status))
        self.assertTrue(self.request('/api/history'))
        self.assertEqual(exported['payload']['explanations']['annotations'][0]['provenance']['method'], 'provider-api')

    def test_import_raw_precision_and_persistence(self):
        package = json.loads(self.original)
        self.request('/api/recording', {'recording_json': self.original.decode()})
        exported = self.request('/api/recording')
        self.assertEqual(exported['payload']['bundle'], package['payload']['bundle'])
        self.assertEqual(exported['payload']['bundle']['events'][0]['data']['precise'], 9007199254740993)
        self.assertTrue((self.root / 'state' / self.service.recording_id / 'recording.json').exists())

    def test_failed_batch_retry_is_explicit_and_no_unsolicited_ids(self):
        real = self.service.provider
        def malicious(*args):
            document = real(*args)
            document['annotations'][0]['message_id'] = 'unsolicited'
            return document
        self.service.provider = malicious
        config = {'provider': 'openai-compatible', 'endpoint': 'https://gateway.example/v1',
                  'model': 'synthetic-model', 'scope': 'all'}
        self.service.start(config)
        for _ in range(100):
            if self.service.snapshot()['state'] != 'running':
                break
            time.sleep(.01)
        self.assertEqual(self.service.snapshot()['state'], 'failed')
        self.assertNotIn('explanations', self.service.package['payload'])
        self.assertEqual(len(self.calls), 1)
        self.service.provider = real
        self.service.start({}, retry=True)
        for _ in range(100):
            if self.service.snapshot()['state'] != 'running':
                break
            time.sleep(.01)
        self.assertEqual(self.service.snapshot()['state'], 'complete')
        self.assertEqual(len(self.calls), 2)

    def test_cancel_checkpoints_inflight_batch_then_stops(self):
        entered, release = threading.Event(), threading.Event()
        real = self.service.provider
        def delayed(*args):
            entered.set()
            release.wait(2)
            return real(*args)
        self.service.provider = delayed
        self.service.start({'provider': 'openai-compatible', 'endpoint': 'https://gateway.example/v1',
                            'model': 'synthetic-model', 'scope': 'all'})
        self.assertTrue(entered.wait(2))
        self.service.cancelled.set()
        release.set()
        for _ in range(100):
            if self.service.snapshot()['state'] != 'running':
                break
            time.sleep(.01)
        self.assertEqual(self.service.snapshot()['state'], 'cancelled')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.service.snapshot()['completed'], 1)

    def test_preview_has_exact_counts_and_no_calls(self):
        result = self.request('/api/preview', {'scope': 'all', 'model': 'synthetic-model'})
        self.assertEqual(result['targets'], 1)
        self.assertEqual(result['batches'], 1)
        self.assertGreater(result['request_bytes'], 0)
        self.assertEqual(result['recording_id'], self.service.recording_id)
        self.assertEqual(self.calls, [])

    def test_invalid_import_is_transactional(self):
        original = copy.deepcopy(self.service.package)
        package = json.loads(self.original)
        package['payload']['bundle']['manifest']['run_id'] = 'different'
        package['payload']['bundle']['events'][0]['run_id'] = 'different'
        package['payload']['explanations'] = {'invalid': 'document'}
        with self.assertRaises(ValueError):
            self.service.register(package)
        self.assertEqual(self.service.package, original)
        self.assertEqual(self.service.bundle, original['payload']['bundle'])
        self.assertFalse((self.root / 'state' / self.service.recording_id / 'recording.json').exists())
        self.assertIn(b'"attachments": [', self.service.html())

    def test_adaptive_batch_plan_matches_worker(self):
        from unittest.mock import patch
        sizes = []
        def bounded(bundle, flow, ids, model):
            sizes.append(len(ids))
            if len(ids) > 3:
                raise ValueError('explanation request exceeds 512000 UTF-8 bytes; select fewer messages')
            return {'ids': list(ids)}
        with patch('analysis.explanation_service.build_explanation_request', side_effect=bounded):
            planned = list(self.service.planned_requests(list(range(21)), 'fake'))
            self.assertEqual(sum(len(ids) for ids, request in planned), 21)
            self.assertTrue(all(len(ids) <= 3 for ids, _ in planned))
            first, _ = self.service._next_request(list(range(21)), 'fake')
            self.assertEqual(first, planned[0][0])
        self.assertIn(20, sizes)
        self.assertEqual(self.calls, [])

    def test_attachments_export_persistence_and_busy_rejection(self):
        self.request('/api/recording', {'recording_json': self.original.decode()})
        attachments = [{'name': 'download.py', 'language': 'python', 'text': 'raise Exception("inert")'}]
        self.request('/api/attachments', {'attachments': attachments})
        exported = self.request('/api/export')
        self.assertEqual(exported['attachments'], attachments)
        stored = json.loads((self.root / 'state' / self.service.recording_id / 'recording.json').read_text())
        self.assertEqual(stored['attachments'], attachments)
        self.assertEqual(exported['payload']['bundle'], json.loads(self.original)['payload']['bundle'])
        self.service.status['state'] = 'running'
        with self.assertRaises(ValueError):
            self.service.attachments([])
        self.service.status['state'] = 'idle'
        with self.assertRaises(ValueError):
            self.service.attachments([dict(attachments[0], language='shell')])
        self.assertEqual(self.service.package['attachments'], attachments)

    def test_restore_current_authoritative_on_reopen(self):
        from analysis.explanations import recording_fingerprint
        bundle = synthetic_bundle()
        second = copy.deepcopy(bundle['events'][0])
        second.update(id='b', sequence=1)
        bundle['events'].append(second)
        payload = build_payload(bundle, {})
        doc = {'version': 1, 'run_id': 'synthetic', 'bundle_sha256': recording_fingerprint(bundle),
               'provenance': {'method': 'offline-development-worker', 'model': 'offline', 'provider': 'offline'},
               'annotations': [{'message_id': 'message:e', 'text': 'First annotation.', 'basis': 'observed_action', 'event_ids': ['e']}]}
        self.service.store.merge(bundle, payload['flow'], doc)
        revision = self.service.store.history(bundle, payload['flow'])[0]
        doc['annotations'].append({'message_id': 'message:b', 'text': 'Second annotation.', 'basis': 'observed_action', 'event_ids': ['b']})
        self.service.store.merge(bundle, payload['flow'], doc)
        self.service.store.restore(bundle, payload['flow'], revision)
        before = self.service.store.history(bundle, payload['flow'])
        payload['explanations'] = doc
        source = self.root / 'annotated.json'
        write_recording(payload, source)
        reopened = ExplanationService(source, self.root / 'state', provider=self.service.provider)
        self.assertEqual([item['message_id'] for item in reopened.package['payload']['explanations']['annotations']], ['message:e'])
        self.assertEqual(reopened.store.history(bundle, payload['flow']), before)

    def test_stale_tab_mutations_rejected_before_provider(self):
        for path, value in [('/api/preview', {'scope': 'all'}),
                            ('/api/generate', {'provider': 'openai-compatible', 'endpoint': 'https://gateway.example/v1', 'model': 'fake', 'scope': 'all'}),
                            ('/api/cancel', {}), ('/api/retry', {}), ('/api/restore', {'revision': 'x'}),
                            ('/api/attachments', {'attachments': []})]:
            with self.subTest(path=path):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.request(path, dict(value, recording_id='stale'))
                self.assertEqual(error.exception.code, 400)
                error.exception.close()
        self.assertEqual(self.calls, [])
