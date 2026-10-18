"""Synthetic checks for locally bound originals, never portable path authority."""
import hashlib
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from web.artifacts import ArtifactStore, write_binding, MAX_FILE_BYTES
from adapters.artex.importer import _convert


class OriginalArtifactsTest(unittest.TestCase):
    def fixture(self, directory, raw=b'<script>inert</script>'):
        state = Path(directory)
        local = state / 'imports' / ('a' * 32)
        root = local / 'bundle' / 'artifacts' / 'artex'
        (root / 'files').mkdir(parents=True)
        path = root / 'files' / 'report.html'
        path.write_bytes(raw)
        row = {'path': 'files/report.html', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        bundle = {'manifest': {'source': {'archive_inventory': [row]}}}
        write_binding(local, bundle, 'record-one')
        return state, local, path, bundle

    def test_preview_hash_missing_changed_and_cross_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            state, local, path, bundle = self.fixture(temporary)
            store = ArtifactStore(state, bundle, 'record-one')
            row = store.listing()['artifacts'][0]
            self.assertTrue(row['available'])
            self.assertEqual(store.preview(row['artifact_id'])['text'], '<script>inert</script>')
            other = ArtifactStore(state, bundle, 'record-two')
            self.assertFalse(other.listing()['artifacts'][0]['available'])
            with self.assertRaises(ValueError):
                other.read(row['artifact_id'])
            path.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                store.read(row['artifact_id'])
            path.unlink()
            self.assertFalse(store.listing()['artifacts'][0]['available'])

    def test_symlink_and_no_portable_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            state, local, path, bundle = self.fixture(temporary)
            store = ArtifactStore(state, bundle, 'record-one')
            key = store.listing()['artifacts'][0]['artifact_id']
            outside = state / 'private'
            outside.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(outside)
            with self.assertRaises(ValueError):
                store.read(key)
            (local / 'artifact-binding.json').unlink()
            bundle['manifest']['source']['path'] = str(local)
            self.assertFalse(ArtifactStore(state, bundle, 'record-one').listing()['artifacts'][0]['available'])

    def test_unsupported_secure_reader_capabilities_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            state, local, path, bundle = self.fixture(temporary)
            store = ArtifactStore(state, bundle, 'record-one')
            key = store.listing()['artifacts'][0]['artifact_id']
            with patch('web.artifacts.os.supports_dir_fd', set()):
                with self.assertRaisesRegex(ValueError, 'unavailable on this platform'):
                    store.read(key)
                self.assertFalse(store.listing()['artifacts'][0]['available'])
            nofollow = os.O_NOFOLLOW
            try:
                del os.O_NOFOLLOW
                with self.assertRaisesRegex(ValueError, 'unavailable on this platform'):
                    store.read(key)
            finally:
                os.O_NOFOLLOW = nofollow
            # Some implementations advertise dir_fd yet reject it at runtime.
            with patch('web.artifacts.os.open', side_effect=NotImplementedError), patch(
                    'web.artifacts.os.supports_dir_fd', {os.open}):
                with self.assertRaises(ValueError):
                    store.read(key)

    def test_trusted_anchor_is_opened_directly_without_system_ancestor_walk(self):
        with tempfile.TemporaryDirectory() as temporary:
            state, local, path, bundle = self.fixture(temporary)
            store = ArtifactStore(state, bundle, 'record-one')
            key = store.listing()['artifacts'][0]['artifact_id']
            opened = []
            real_open = os.open
            def scoped_open(name, flags, *args, **kwargs):
                opened.append((name, flags, kwargs.get('dir_fd')))
                if kwargs.get('dir_fd') is None and Path(name) != store.imports:
                    raise PermissionError('synthetic scoped ancestor denial')
                return real_open(name, flags, *args, **kwargs)
            with patch('web.artifacts.os.open', side_effect=scoped_open), patch(
                    'web.artifacts.os.supports_dir_fd', {os.open}):
                self.assertEqual(store.read(key)[1], b'<script>inert</script>')
            self.assertEqual(Path(opened[0][0]), state.resolve() / 'imports')
            self.assertIsNone(opened[0][2])
            self.assertTrue(all(flags & os.O_NOFOLLOW for _, flags, _ in opened))
            self.assertTrue(all(directory is not None for _, _, directory in opened[1:]))

    def test_intermediate_imported_directory_symlink_fails_closed(self):
        for relative in ('imports', 'uuid', 'bundle', 'bundle/artifacts', 'bundle/artifacts/artex/files'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                state, local, path, bundle = self.fixture(temporary)
                store = ArtifactStore(state, bundle, 'record-one')
                key = store.listing()['artifacts'][0]['artifact_id']
                replaced = state / 'imports' if relative == 'imports' else local if relative == 'uuid' else local / relative
                outside = state / 'outside-import'
                shutil.move(replaced, outside)
                replaced.symlink_to(outside, target_is_directory=True)
                with self.assertRaises(ValueError):
                    store.read(key)
                self.assertFalse(store.listing()['artifacts'][0]['available'])
                # Reopening must not bless an intermediate link either.
                reopened = ArtifactStore(state, bundle, 'record-one')
                self.assertFalse(reopened.listing()['artifacts'][0]['available'])

    def test_preview_bound_binary_and_size(self):
        with tempfile.TemporaryDirectory() as temporary:
            state, local, path, bundle = self.fixture(temporary, b'a' * 70000)
            store = ArtifactStore(state, bundle, 'record-one')
            key = store.listing()['artifacts'][0]['artifact_id']
            preview = store.preview(key)
            self.assertEqual(len(preview['text']), 65536)
            self.assertTrue(preview['truncated'])
            with path.open('wb') as output:
                output.truncate(MAX_FILE_BYTES + 1)
            with self.assertRaises(ValueError):
                store.read(key)
        with tempfile.TemporaryDirectory() as temporary:
            state, local, path, bundle = self.fixture(temporary, b'\x00binary')
            store = ArtifactStore(state, bundle, 'record-one')
            preview = store.preview(store.listing()['artifacts'][0]['artifact_id'])
            self.assertTrue(preview['binary'])
            self.assertEqual(preview['text'], '')

    def test_generic_composite_binding_rows_preserve_scoped_fields(self):
        row = {'finding_id': 17, 'traffic_id': 31, 'task_id': 'task', 'scope': 'local', 'request': 'synthetic'}
        snapshot = {'task_id': 'task', 'created_at': '2026-10-17T10:00:00Z', 'format_version': 3,
                    'tables': {'finding_traffic_bindings': [row], 'new_native_table': [dict(row)]}}
        converted = _convert(snapshot, [], [{'path': 'artifacts/artex/manifest.json', 'sha256': 'a'*64}], {})
        events = [event for event in converted['events'] if event['data'].get('source_table')]
        self.assertEqual(len(events), 2)
        for event in events:
            self.assertIn('composite-', event['id'])
            self.assertEqual(event['kind'], 'observation')
            self.assertEqual(event['data']['source_record'], row)
            self.assertNotIn('dependencies', event)
        repeated = _convert(snapshot, [], [{'path': 'artifacts/artex/manifest.json', 'sha256': 'a'*64}], {})
        self.assertEqual(converted['events'], repeated['events'])


if __name__ == '__main__':
    unittest.main()
