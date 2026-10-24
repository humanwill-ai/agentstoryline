"""New synthetic regression checks for private detection copies under temp aliases."""
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from adapters.artex.importer import _archive
from web.viewer_import import detect_source


@unittest.skipUnless(shutil.which('zstd'), 'requires installed zstd')
class DetectionTempPathTests(unittest.TestCase):
    def test_zstd_detection_canonicalizes_only_private_temp_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            parent = root / 'temp-parent'
            parent.mkdir()
            alias = root / 'temp-alias'
            alias.symlink_to(parent, target_is_directory=True)
            output = io.BytesIO()
            manifest = json.dumps({'format_version': 1, 'tables': {},
                                   'task_id': 'synthetic', 'created_at': 'synthetic'}).encode()
            with tarfile.open(fileobj=output, mode='w') as archive:
                info = tarfile.TarInfo('manifest.json')
                info.size = len(manifest)
                archive.addfile(info, io.BytesIO(manifest))
            compressed = subprocess.run([shutil.which('zstd'), '-q', '-c'],
                                        input=output.getvalue(), capture_output=True,
                                        check=True).stdout
            source = root / 'synthetic.tar.zst'
            source.write_bytes(compressed)
            real_temporary_directory = tempfile.TemporaryDirectory
            def detection_directory(**kwargs):
                return real_temporary_directory(dir=alias, **kwargs)
            with patch('web.viewer_import.tempfile.TemporaryDirectory', side_effect=detection_directory), \
                    patch('web.viewer_import._archive', wraps=_archive) as reader:
                self.assertEqual(detect_source(source), 'artex')
                private_copy = reader.call_args.args[0]
                self.assertEqual(private_copy.parent.parent, parent)
                self.assertEqual(private_copy, private_copy.resolve())
            selected_alias = root / 'selected.tar.zst'
            selected_alias.symlink_to(source)
            with self.assertRaisesRegex(ValueError, 'symlinks'):
                detect_source(selected_alias)
            source_alias = root / 'selected-parent'
            source_alias.symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'symlinks'):
                detect_source(source_alias / source.name)


if __name__ == '__main__':
    unittest.main()
