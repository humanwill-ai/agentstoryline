import copy
import json
import unittest
from analysis.explanations import recording_fingerprint, validate_explanations
from analysis.explanation_profiles import profile_for
from core.bundle import validate_bundle
from web.render import build_payload

from web.sharing import prepare_share


def package():
    evidence = {'api_key': 'canarycredential987', 'text': 'canarycredential987 Bearer bearerCanary987 https://private.example/a 192.0.2.42 user@private.example /Users/alice/private/file.txt CUSTOM-CANARY',
                'nested': json.dumps({'password': 'nestedCanary765', 'message': 'nestedCanary765'})}
    event = {'schema_version': '0.1', 'id': 'e1', 'run_id': 'run1', 'kind': 'observation',
             'timestamp': '2026-01-01T00:00:00Z', 'sequence': 0, 'data': evidence,
             'artifact_refs': [{'path': 'artifacts/private.txt', 'sha256': 'a' * 64}]}
    bundle = {'manifest': {'schema_version': '0.1', 'run_id': 'run1', 'agent': {'name': 'Synthetic', 'version': '1'},
              'started_at': '2026-01-01T00:00:00Z', 'completeness': {'status': 'complete', 'gaps': []}}, 'events': [event]}
    annotations = {'version': 1, 'run_id': 'run1', 'bundle_sha256': recording_fingerprint(bundle),
        'provenance': {'method': 'offline-development-worker', 'model': 'synthetic', 'provider': 'local'},
        'annotations': [{'message_id': 'm1', 'text': 'Observed CUSTOM-CANARY user@private.example',
                         'basis': 'observed_action', 'event_ids': ['e1']}]}
    payload = {'bundle': bundle, 'narrative': {'raw': copy.deepcopy(evidence)},
        'storyline': {'activities': [], 'lanes': [], 'raw': json.dumps(evidence)},
        'flow': {'actors': [], 'messages': [{'id': 'm1', 'kind': 'observation', 'actor_id': 'a1', 'offset_ms': 0,
                 'event_ids': ['e1'], 'description': json.dumps(evidence), 'source_record': copy.deepcopy(evidence)}]},
        'manifest_text': json.dumps(bundle['manifest']), 'source_eventtext': json.dumps(event),
        'explanations': annotations, 'notification_sound': None,
        'evidence_text': {'e1': {'event': json.dumps(event)}}}
    return {'format': 'traceforge-recording', 'version': 1, 'payload': payload,
            'attachments': [{'name': 'private.py', 'language': 'python', 'text': 'attachedCanary999'}]}


class SharingTests(unittest.TestCase):
    def test_every_duplicate_and_encoded_evidence_redacted_without_mutation(self):
        original = package()
        before = copy.deepcopy(original)
        result = prepare_share(original, {'custom_terms': ['CUSTOM-CANARY']})
        self.assertEqual(original, before)
        derived = result['package']
        serialized = json.dumps(derived)
        for secret in ('canarycredential987', 'bearerCanary987', 'private.example', '192.0.2.42',
                       '/Users/alice', 'nestedCanary765', 'CUSTOM-CANARY', 'attachedCanary999',
                       'artifacts/private.txt'):
            self.assertNotIn(secret, serialized)
            self.assertNotIn(secret, json.dumps(result['report']))
        self.assertEqual(derived['attachments'], [])
        bundle = derived['payload']['bundle']
        validate_bundle(bundle)
        self.assertEqual(bundle['events'][0]['id'], 'e1')
        self.assertEqual(bundle['manifest']['completeness']['status'], 'partial')
        self.assertNotEqual(recording_fingerprint(bundle), recording_fingerprint(original['payload']['bundle']))
        self.assertEqual(derived['payload']['sharing']['derived_bundle_sha256'], recording_fingerprint(bundle))
        validate_explanations(bundle, derived['payload']['flow'], derived['payload']['explanations'])
        self.assertIn('[REDACTED]', derived['payload']['evidence_text']['e1']['event'])
        self.assertEqual(result['report']['omitted_attachments'], 1)
        self.assertGreater(result['report']['omitted_original_artifacts'], 0)

    def test_structural_custom_terms_do_not_destroy_ids_enums_or_references(self):
        result = prepare_share(package(), {'custom_terms': ['e1', 'observation', 'observed_action', 'run1']})['package']
        event = result['payload']['bundle']['events'][0]
        self.assertEqual(event['id'], 'e1')
        self.assertEqual(event['kind'], 'observation')
        self.assertEqual(result['payload']['explanations']['annotations'][0]['event_ids'], ['e1'])
        self.assertTrue(result['payload']['sharing']['identifiers_preserved'])

    def test_raw_schema_like_keys_never_bypass_redaction(self):
        value = package()
        raw = {'result': {'status': {'id': 'CUSTOM-CANARY', 'kind': 'CUSTOM-CANARY'},
                          'kind': 'CUSTOM-CANARY', 'dependencies': ['CUSTOM-CANARY'],
                          'run_id': 'CUSTOM-CANARY', 'event_ids': ['CUSTOM-CANARY'],
                          'accessToken': 'camelCaseCanary999'},
               'copy': 'camelCaseCanary999'}
        p = value['payload']
        p['bundle']['events'][0]['data']['raw'] = raw
        p['flow']['messages'][0]['source_record'] = copy.deepcopy(raw)
        p['source_eventtext'] = json.dumps(raw)
        p['explanations']['bundle_sha256'] = recording_fingerprint(p['bundle'])
        result = prepare_share(value, {'custom_terms': ['CUSTOM-CANARY']})
        self.assertNotIn('CUSTOM-CANARY', json.dumps(result['package']))
        self.assertNotIn('camelCaseCanary999', json.dumps(result['package']))

    def test_explicit_adapter_identity_and_source_metadata_remain_at_original_path(self):
        for name, adapter in (('Renamed recording', 'artex'), ('Artex', 'explicit-unknown')):
            value = package()
            bundle = value['payload']['bundle']
            bundle['manifest']['agent']['name'] = name
            bundle['manifest']['source'] = {'adapter': adapter, 'label': 'CUSTOM-CANARY'}
            value['payload']['explanations']['bundle_sha256'] = recording_fingerprint(bundle)
            result = prepare_share(value, {'custom_terms': ['CUSTOM-CANARY', adapter, 'adapter']})['package']
            source = result['payload']['bundle']['manifest']['source']
            self.assertEqual(source['adapter'], adapter)
            self.assertEqual(source['label'], '[REDACTED]')
            self.assertIn('sanitization', source)
            self.assertEqual(profile_for(result['payload']['bundle']).identity, profile_for(bundle).identity)

    def test_presentation_dependency_metadata_and_schema_like_fields_are_redacted(self):
        value = package()
        message = value['payload']['flow']['messages'][0]
        message['dependencies'] = [{'event_id': 'e1', 'notes': 'CUSTOM-CANARY'}]
        message['status'] = {'id': 'CUSTOM-CANARY', 'note': 'CUSTOM-CANARY'}
        message['version'] = 'CUSTOM-CANARY'
        message['kind'] = 'Bearer authCanary999'
        result = prepare_share(value, {'custom_terms': ['CUSTOM-CANARY']})
        self.assertNotIn('CUSTOM-CANARY', json.dumps(result['package']))
        self.assertNotIn('authCanary999', json.dumps(result['package']))

    def test_recorded_dependency_ids_and_provenance_are_preserved(self):
        value = package()
        event = copy.deepcopy(value['payload']['bundle']['events'][0])
        event.update(id='e2', sequence=1, dependencies=[{'event_id': 'e1', 'provenance': 'recorded'}])
        value['payload']['bundle']['events'].append(event)
        value['payload']['explanations']['bundle_sha256'] = recording_fingerprint(value['payload']['bundle'])
        result = prepare_share(value, {'custom_terms': ['e1', 'recorded']})['package']
        self.assertEqual(result['payload']['bundle']['events'][1]['dependencies'], [{'event_id': 'e1', 'provenance': 'recorded'}])

    def test_validation_errors_do_not_echo_private_field_names(self):
        value = package()
        value['payload']['bundle']['manifest']['private-field-canary999'] = 'private-value-canary999'
        with self.assertRaises(ValueError) as caught:
            prepare_share(value)
        self.assertNotIn('private-field-canary999', str(caught.exception))
        self.assertNotIn('private-value-canary999', str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_nested_presentation_description_and_context_references_remain_valid(self):
        value = package()
        p = value['payload']
        p['bundle']['events'][0]['id'] = 'artex.tasks.23'
        m = p['flow']['messages'][0]
        m['event_ids'] = ['artex.tasks.23']
        m['description'] = {'text': 'Observed CUSTOM-CANARY', 'basis': 'recorded', 'event_ids': ['artex.tasks.23'], 'source_path': 'data.summary'}
        m['context'] = [{'text': 'CUSTOM-CANARY', 'event_ids': ['artex.tasks.23']}]
        m['semantic_tags'] = [{'label': 'CUSTOM-CANARY', 'event_ids': ['artex.tasks.23'], 'basis': 'recorded_phase'}]
        p['explanations']['annotations'][0]['event_ids'] = ['artex.tasks.23']
        p['explanations']['bundle_sha256'] = recording_fingerprint(p['bundle'])
        result = prepare_share(value, {'custom_terms': ['CUSTOM-CANARY', 'recorded']})['package']
        m = result['payload']['flow']['messages'][0]
        self.assertEqual(m['description']['event_ids'], m['event_ids'])
        self.assertEqual(m['description']['basis'], 'recorded')
        self.assertEqual(m['context'][0]['event_ids'], m['event_ids'])
        self.assertEqual(m['semantic_tags'][0]['event_ids'], m['event_ids'])
        self.assertNotIn('CUSTOM-CANARY', json.dumps(result))

    def test_complete_built_presentation_retains_all_nested_references_and_timestamps(self):
        bundle = package()['payload']['bundle']
        bundle['manifest']['agent']['name'] = 'Artex'
        bundle['manifest']['source'] = {'adapter': 'artex'}
        event = bundle['events'][0]
        event.pop('artifact_refs')
        event.update(id='artex.tasks.23', timestamp='2026-01-01T16:32:22Z')
        event['data'] = {'source_record': {'summary': 'Synthetic recorded task'}}
        second = copy.deepcopy(event)
        second.update(id='artex.tasks.24', sequence=1, timestamp='2026-01-01T17:32:22Z')
        bundle['events'].append(second)
        payload = build_payload(bundle, {'stages': []})
        original = {'format': 'traceforge-recording', 'version': 1, 'payload': payload, 'attachments': []}
        result = prepare_share(original)['package']
        checked = 0
        def compare(a, b):
            nonlocal checked
            if isinstance(a, dict) and isinstance(b, dict):
                for key, value in a.items():
                    if key not in b:
                        continue
                    if key == 'id' or key.endswith(('_id', '_ids')) or key in ('evidence_refs', 'timestamp'):
                        self.assertEqual(value, b[key], key)
                        checked += 1
                    compare(value, b[key])
            elif isinstance(a, list) and isinstance(b, list):
                for x, y in zip(a, b):
                    compare(x, y)
        for key in ('flow', 'storyline'):
            compare(payload[key], result['payload'][key])
        self.assertGreater(checked, 20)
        self.assertEqual(result['payload']['flow']['messages'][0]['description']['event_ids'], ['artex.tasks.23'])

    def test_expanding_redactions_reject_description_over_viewer_limit(self):
        value = package()
        value['payload']['flow']['messages'][0]['description'] = {
            'text': 'x' * 280, 'basis': 'recorded', 'event_ids': ['e1'], 'source_path': 'data.summary'}
        with self.assertRaisesRegex(ValueError, 'recorded description exceeds viewer limits'):
            prepare_share(value, {'custom_terms': ['x']})
        self.assertEqual(value['payload']['flow']['messages'][0]['description']['text'], 'x' * 280)

    def test_options_reject_disabled_defaults_and_invalid_literals(self):
        for options in ({'redact_credentials': False}, {'custom_terms': ['']}, {'preserve_audio': 'yes'}):
            with self.assertRaises(ValueError):
                prepare_share(package(), options)

    def test_audio_is_omitted_by_default_or_explicitly_preserved(self):
        value = package()
        value['payload']['notification_sound'] = 'data:audio/wav;base64,UklGRg=='
        self.assertIsNone(prepare_share(value)['package']['payload']['notification_sound'])
        self.assertEqual(prepare_share(value, {'preserve_audio': True})['package']['payload']['notification_sound'], value['payload']['notification_sound'])

    def test_invalid_source_annotations_rejected(self):
        value = package()
        value['payload']['explanations']['bundle_sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            prepare_share(value)

    def test_known_source_kind_enums_preserved_only_at_event_data_path(self):
        known = ('archive.transcript', 'archive.snapshot',
                 'request.history.tool_use', 'request.history.tool_result')
        for source_kind in known:
            value = package()
            event = value['payload']['bundle']['events'][0]
            event['data']['source_kind'] = source_kind
            event['data']['nested_source'] = {'source_kind': source_kind}
            value['payload']['explanations']['bundle_sha256'] = recording_fingerprint(value['payload']['bundle'])
            shared = prepare_share(value, {'custom_terms': [source_kind]})['package']
            data = shared['payload']['bundle']['events'][0]['data']
            self.assertEqual(data['source_kind'], source_kind)
            self.assertEqual(data['nested_source']['source_kind'], '[REDACTED]')
        value = package()
        value['payload']['bundle']['events'][0]['data']['source_kind'] = 'CUSTOM-CANARY'
        value['payload']['explanations']['bundle_sha256'] = recording_fingerprint(value['payload']['bundle'])
        shared = prepare_share(value, {'custom_terms': ['CUSTOM-CANARY']})['package']
        self.assertEqual(shared['payload']['bundle']['events'][0]['data']['source_kind'], '[REDACTED]')
