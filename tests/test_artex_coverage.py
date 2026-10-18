"""Newly authored synthetic coverage checks; no real run evidence."""
import importlib.util
import json
from pathlib import Path
import tempfile
import tarfile
import unittest

from adapters.artex.coverage import comparison_report, source_coverage, write_report
from adapters.artex.importer import ArtexImportError, import_artex


class CoverageTests(unittest.TestCase):
    def source(self, root):
        root.mkdir()
        (root / 'manifest.json').write_text(json.dumps({'format_version': 3,
            'created_at': '2026-10-10T12:00:00Z', 'task_id': 1,
            'tables': {'tasks': [{'id': 1}], 'llm_records': []}, 'data_counts': {'tasks': 1}}))
        (root / 'files' / 'transcripts').mkdir(parents=True)
        (root / 'files' / 'transcripts' / 'test.jsonl').write_text('{"uuid":"secret-evidence-text","type":"boundary","timestamp":"2026-10-10T12:00:00Z"}\n')
        return root

    def test_metadata_and_unknown_display(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temp:
            source = self.source(Path(temp) / 'source')
            report = source_coverage(source)
            self.assertEqual(report['categories']['transcripts']['source_count'], 1)
            self.assertIsNone(report['categories']['activity']['displayed_count'])
            self.assertNotIn('secret-evidence-text', json.dumps(report))
            self.assertTrue(all(len(f['sha256']) == 64 for f in report['files']))

    def test_actual_preservation_and_separate_execution(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temp:
            source = self.source(Path(temp) / 'source')
            bundle = import_artex(source, Path(temp) / 'bundle')
            scenarios = [{'label': label, 'execution_id': execution, 'source': source, 'bundle': bundle}
                         for label, execution in [('A', 'seeded'), ('B', 'native'), ('C', 'native')]]
            report = comparison_report(scenarios)
            self.assertEqual([p['same_source_execution'] for p in report['execution_comparisons']], [False, False, True])
            files = report['scenarios'][1]['coverage']['files']
            self.assertTrue(all(f['imported_status'] == 'preserved' for f in files))
            output = Path(temp) / 'report'
            write_report(report, output)
            self.assertTrue((output / 'coverage.md').is_file())
            with self.assertRaises(FileExistsError):
                write_report(report, output)

    def test_archive_inventory_and_unknown_relationship(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temp:
            source = self.source(Path(temp) / 'source')
            archive = Path(temp) / 'native.tar'
            with tarfile.open(archive, 'w') as output:
                for path in sorted(source.rglob('*')):
                    if path.is_file():
                        output.add(path, arcname=path.relative_to(source).as_posix())
            report = comparison_report([{'source': archive}, {'source': source}])
            self.assertIsNone(report['execution_comparisons'][0]['same_source_execution'])
            left = report['scenarios'][0]['coverage']['files']
            right = report['scenarios'][1]['coverage']['files']
            self.assertEqual(sorted((x['path'], x['sha256']) for x in left),
                             sorted((x['path'], x['sha256']) for x in right))

    def test_snapshot_json_excludes_private_siblings(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temp:
            source = self.source(Path(temp) / 'source')
            (source / 'private-sibling-name.txt').write_text('private unrelated text')
            report = source_coverage(source / 'manifest.json')
            self.assertEqual([f['path'] for f in report['files']], ['manifest.json'])
            self.assertNotIn('private-sibling-name', json.dumps(report))
            self.assertIsNone(report['categories']['transcripts']['source_count'])
            self.assertEqual(report['categories']['workspace']['source_status'], 'unknown_uninspected_companions')

    def test_symlink_and_bad_counts_rejected(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temp:
            source = self.source(Path(temp) / 'source')
            (source / 'link').symlink_to(source / 'manifest.json')
            with self.assertRaises(ArtexImportError):
                source_coverage(source)
            (source / 'link').unlink()
            manifest = json.loads((source / 'manifest.json').read_text())
            manifest['data_counts']['tasks'] = 2
            (source / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ArtexImportError):
                source_coverage(source)


if __name__ == '__main__':
    unittest.main()
