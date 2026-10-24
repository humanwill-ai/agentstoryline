"""Synthetic, local-only Orca collection checks."""
import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from tests.test_orcareplay_adapter import fixture_files
from web.viewer_import import normalize_orca_collection, import_upload
from analysis.explanation_workflow import load_portable
from web.artifacts import ArtifactStore
from analysis.explanations import recording_fingerprint
from unittest.mock import patch

class OrcaCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def source(self, number, *, run=None, created='2026-10-18T10:00:00Z'):
        files = fixture_files()
        manifest = json.loads(files['manifest.json'])
        manifest.update(run_id=run or ('run_abc12%d' % number), run_key='exact session %d' % number, created_at=created)
        files['manifest.json'] = json.dumps(manifest).encode()
        path = self.root / ('source-%d.orca.zip' % number)
        with zipfile.ZipFile(path, 'w') as archive:
            for name, raw in files.items():
                archive.writestr(name, raw)
        return path

    def test_collision_isolation_timing_originals_and_roundtrip(self):
        first = self.source(1, created='2026-10-18T11:00:00+01:00')
        second = self.source(2, created='2026-10-18T09:59:59Z')
        package, path = normalize_orca_collection([first, second], self.root / 'state')
        self.assertEqual(package, load_portable(path))
        bundle = package['payload']['bundle']
        events = bundle['events']
        self.assertEqual(len({e['id'] for e in events}), len(events))
        self.assertEqual(events[0]['data']['source_session']['run_id'], 'run_abc122')
        self.assertEqual([s['label'] for s in bundle['manifest']['source']['collection']['sessions']], ['exact session 1', 'exact session 2'])
        for event in events:
            self.assertEqual(event['data']['source_record']['seq'], event['data']['source_session']['sequence'])
        store = ArtifactStore(self.root / 'state', bundle, recording_fingerprint(bundle))
        self.assertIsNotNone(store.root)
        originals = [row for row in store.rows if row['path'].endswith('/original.orca')]
        self.assertEqual(len(originals), 2)
        self.assertEqual(store._read(originals[0]), first.read_bytes())
        requests = [event for event in events if event['kind'] == 'model.request' and event['data']['source_session']['sequence'] == 7]
        ids = [event['data']['request']['messages'][-1]['tool_call_id'] for event in requests]
        self.assertEqual(ids, ['call-1', 'call-1'])
        self.assertNotIn('call_id', requests[0])

    def test_duplicate_and_mixed_rejected_without_publication(self):
        first = self.source(1)
        duplicate = self.source(2, run='run_abc121')
        for paths in ([first, duplicate], [first, self.root / 'other.json']):
            with self.assertRaises(ValueError):
                normalize_orca_collection(paths, self.root / 'state')
        self.assertEqual(list((self.root / 'state/imports').iterdir()), [])

    def test_collection_upload(self):
        paths = [self.source(1), self.source(2)]
        package, path = import_upload({'files': [{'filename': p.name, 'source_base64': base64.b64encode(p.read_bytes()).decode()} for p in paths]}, self.root / 'state')
        self.assertTrue(path.exists())
        self.assertEqual(package['payload']['bundle']['manifest']['source']['adapter'], 'orcareplay')

    def test_bounds_and_stable_tie_order(self):
        paths = [self.source(1), self.source(2)]
        package, _ = normalize_orca_collection(paths, self.root / 'state')
        events = package['payload']['bundle']['events']
        self.assertEqual([event['data']['source_session']['run_id'] for event in events[:2]], ['run_abc121', 'run_abc122'])
        self.assertEqual([event['data']['source_session']['sequence'] for event in events[:2]], [0, 0])
        with patch('adapters.orcareplay.collection.MAX_COLLECTION_EVENTS', 1):
            with self.assertRaisesRegex(ValueError, '5,000'):
                normalize_orca_collection(paths, self.root / 'bounded')
        self.assertEqual(list((self.root / 'bounded/imports').iterdir()), [])
        with self.assertRaises(ValueError):
            normalize_orca_collection(paths * 9, self.root / 'bounded')
