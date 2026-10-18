"""New synthetic native archive evidence tests; no runtime or model execution."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from adapters.artex import importer


class ArchiveContentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        self.source.mkdir()
        self.snapshot = {'format_version': 3, 'task_id': 7, 'created_at': '2026-10-01T00:00:00Z',
                         'tables': {}, 'data_counts': {}}
        self.write('manifest.json', json.dumps(self.snapshot).encode())

    def write(self, name, raw):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def run_import(self, source=None):
        return importer.import_artex(source or self.source, self.root / 'bundle')

    def test_all_artifacts_preserved_and_history_not_execution(self):
        row = {'uuid': 'u1', 'session_id': 's1', 'type': 'message', 'timestamp': '2026-10-01T00:00:00Z',
               'message': {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 't1', 'name': 'Bash', 'input': {'command': 'NEVER EXECUTE'}}]}}
        boundary = {**row, 'uuid': 'u2', 'parent_uuid': 'u1', 'type': 'boundary', 'message': None}
        raw = b'\n'.join(json.dumps(r).encode() for r in (row, row, boundary))
        self.write('files/transcripts/exp1-test.jsonl', raw)
        workspace = b'\x00inert workspace\xff'
        self.write('files/workspaces/example.bin', workspace)
        result = self.run_import()
        snapshot = next(e for e in result['events'] if e['id'] == 'artex.snapshot')
        self.assertEqual(snapshot['data']['snapshot']['path'], 'artifacts/artex/manifest.json')
        events = [e for e in result['events'] if e['data'].get('source_kind') == 'archive.transcript']
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e['kind'] == 'observation' for e in events))
        self.assertTrue(all('dependencies' not in e and 'parent_id' not in e for e in events))
        self.assertEqual(events[1]['data']['parent_uuid'], 'u1')
        from web.render import build_payload
        payload = build_payload(result, {})
        history_ids = {e['id'] for e in events}
        cards = [m for m in payload['flow']['messages'] if m['event_ids'][0] in history_ids]
        self.assertEqual(len(cards), 2)
        self.assertTrue(all(m['kind'] == 'context' for m in cards))
        self.assertTrue(all(m['title'].startswith('Transcript ') for m in cards))
        for name, expected in [('files/transcripts/exp1-test.jsonl', raw), ('files/workspaces/example.bin', workspace)]:
            self.assertEqual((self.root / 'bundle/artifacts/artex' / name).read_bytes(), expected)
            item = next(i for i in result['manifest']['source']['archive_inventory'] if i['path'] == name)
            self.assertEqual(item['sha256'], hashlib.sha256(expected).hexdigest())

    def test_malformed_unknown_conflicting_and_missing_transcripts(self):
        row = {'uuid': 'u', 'session_id': 's', 'type': 'future', 'timestamp': None}
        self.write('files/transcripts/exp1.jsonl', b'bad json\n[]\n' + json.dumps(row).encode() + b'\n' + json.dumps({**row, 'extra': True}).encode())
        result = self.run_import()
        gaps = '\n'.join(result['manifest']['completeness']['gaps'])
        for phrase in ('malformed transcript', 'not an object', 'unknown transcript', 'conflicting transcript', 'invalid transcript timestamps'):
            self.assertIn(phrase, gaps)
        self.assertEqual(sum(e['data'].get('conflicting_uuid_content', False) for e in result['events']), 1)

    def test_tar_preserves_unknown_members(self):
        archive = self.root / 'native.tar'
        with tarfile.open(archive, 'w') as tar:
            for name, raw in [('manifest.json', json.dumps(self.snapshot).encode()), ('files/unknown.dat', b'evidence')]:
                entry = tarfile.TarInfo(name)
                entry.size = len(raw)
                tar.addfile(entry, io.BytesIO(raw))
        inventory = []
        self.assertEqual(importer._archive(archive, inventory)['files/unknown.dat'], b'evidence')
        self.assertEqual(len(inventory), 2)
        result = self.run_import(archive)
        self.assertIn('No archived transcript', '\n'.join(result['manifest']['completeness']['gaps']))

    def test_extracted_link_and_total_limits(self):
        outside = self.root / 'outside'
        outside.write_bytes(b'secret')
        self.write('files/a', b'a').unlink()
        (self.source / 'files/a').symlink_to(outside)
        with self.assertRaises(importer.ArtexImportError):
            self.run_import()
        (self.source / 'files/a').unlink()
        self.write('files/a', b'1234')
        with patch.object(importer, 'MAX_ARCHIVE_BYTES', 3):
            with self.assertRaises(importer.ArtexImportError):
                self.run_import()
        with patch.object(importer, 'MAX_ARCHIVE_MEMBERS', 1):
            with self.assertRaises(importer.ArtexImportError):
                self.run_import()

    def test_transcript_total_line_limit_counts_blank_and_malformed(self):
        self.write('files/transcripts/a.jsonl', b'bad\n\n')
        self.write('files/transcripts/b.jsonl', b'bad\n')
        with patch.object(importer, 'MAX_ROWS', 2):
            with self.assertRaisesRegex(importer.ArtexImportError, 'total processed line'):
                self.run_import()

    def test_directory_iterator_stops_at_member_limit(self):
        class Entry:
            name = 'manifest.json'
            path = str(self.source / 'manifest.json')
            def stat(inner, follow_symlinks=False):
                return (self.source / 'manifest.json').stat()
        class Entries:
            def __enter__(inner):
                return inner
            def __exit__(inner, *args):
                pass
            def __iter__(inner):
                yield Entry()
                yield Entry()
                raise AssertionError('directory iterator consumed beyond limit')
        with patch.object(importer.os, 'scandir', return_value=Entries()), patch.object(importer, 'MAX_ARCHIVE_MEMBERS', 1):
            with self.assertRaisesRegex(importer.ArtexImportError, 'member limit'):
                importer._directory(self.source)

    def test_llm_processed_line_limit_counts_blank_lines(self):
        self.snapshot['streamed_tables'] = {'llm_records': importer.STREAM_PATH}
        self.write('manifest.json', json.dumps(self.snapshot).encode())
        self.write(importer.STREAM_PATH, b'\n\n\n')
        with patch.object(importer, 'MAX_ROWS', 2):
            with self.assertRaisesRegex(importer.ArtexImportError, 'LLM stream exceeds processed line'):
                self.run_import()

    def test_json_source_never_walks_parent(self):
        (self.source / 'unrelated-link').symlink_to('/does/not/exist')
        result = self.run_import(self.source / 'manifest.json')
        self.assertEqual(len(result['manifest']['source']['archive_inventory']), 1)


if __name__ == '__main__':
    unittest.main()
