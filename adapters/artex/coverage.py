"""Bounded, read-only source inventory and conservative Artex coverage reports.

Reports contain metadata and counts only; evidence text is never embedded.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from adapters.artex import importer


def _role(name):
    if name == 'manifest.json':
        return 'database_snapshot'
    if name.startswith('database/'):
        return 'database_stream'
    if name.startswith('files/transcripts/'):
        return 'transcript'
    if name.startswith('files/'):
        return 'workspace'
    return 'companion'


def source_coverage(source, bundle=None, display=None):
    """Inventory a source; compare actual bundle hashes and caller display evidence.

    display is optional caller-provided {category: count}; absence means unknown.
    Counts describe observed representations, never completeness of execution.
    """
    source = Path(source).absolute()
    inventory = []
    snapshot_only = not source.is_dir() and not source.name.endswith((".tar", ".tar.zst"))
    if source.is_dir():
        files = importer._directory(source)
    elif source.name.endswith(('.tar', '.tar.zst')):
        files = importer._archive(source, inventory=inventory)
    else:
        # Deliberately do not enumerate unrelated siblings of an explicit JSON file.
        files = {'manifest.json': importer._read(source)}
    snapshot = importer._json(files['manifest.json'])
    if not isinstance(snapshot, dict) or type(snapshot.get('format_version')) is not int or snapshot['format_version'] not in (1, 2, 3):
        raise importer.ArtexImportError('expected an Artex TaskArchiveSnapshot format 1, 2 or 3')
    tables = snapshot.get('tables', {})
    if not isinstance(tables, dict):
        raise importer.ArtexImportError('snapshot tables must be an object')
    counts = {}
    for name, rows in tables.items():
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise importer.ArtexImportError('snapshot table must be an array')
        counts[name] = len(rows)
    streams = snapshot.get('streamed_tables') or {}
    if not isinstance(streams, dict) or set(streams) - {'llm_records'}:
        raise importer.ArtexImportError('unsupported streamed table')
    if streams and (streams.get('llm_records') != importer.STREAM_PATH or tables.get('llm_records')):
        raise importer.ArtexImportError('invalid or ambiguous LLM stream declaration')
    for name, path in streams.items():
        if path not in files:
            if snapshot_only:
                files[path] = importer._read(source.parent / path)
            else:
                raise importer.ArtexImportError('declared database stream is missing')
        count = 0
        for line in io.BytesIO(files[path]):
            if not line.strip():
                continue
            count += 1
            if count + sum(counts.values()) > importer.MAX_ROWS:
                raise importer.ArtexImportError('export exceeds row count limit')
            if not isinstance(importer._json(line), dict):
                raise importer.ArtexImportError('database stream must contain row objects')
        counts[name] = count
    if importer.TRAFFIC_PATH in files:
        counts['traffic'] = importer._traffic_count(files, tables)
    if sum(counts.values()) > importer.MAX_ROWS:
        raise importer.ArtexImportError('export exceeds row count limit')
    declared_counts = snapshot.get('data_counts', {})
    if not isinstance(declared_counts, dict):
        raise importer.ArtexImportError('invalid data_counts')
    for name, expected in declared_counts.items():
        if type(expected) is not int or expected != counts.get(name, 0):
            raise importer.ArtexImportError('source row count mismatch: ' + name)
    if not inventory:
        inventory = [{'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                     for name, raw in sorted(files.items())]
    if bundle is not None and not isinstance(bundle, dict):
        from core.bundle import load_bundle
        bundle = load_bundle(bundle)
    artifact_hashes = {} if bundle is None else {
        item['path']: item['sha256'] for item in bundle['manifest'].get('artifact_refs', [])}
    # artifact_refs currently live under manifest.source; tolerate either contract.
    if bundle is not None:
        for item in bundle['manifest'].get('source', {}).get('artifact_refs', []):
            artifact_hashes[item['path']] = item['sha256']
    imported = {}
    if bundle is not None:
        for event in bundle['events']:
            table = event.get('data', {}).get('source_table')
            if table:
                imported.setdefault(table, set()).add(str(event['data'].get('source_id')))
    for item in inventory:
        item['role'] = _role(item['path'])
        actual = artifact_hashes.get('artifacts/artex/' + item['path'])
        item['imported_status'] = ('unknown' if bundle is None else
                                   'preserved' if actual == item['sha256'] else
                                   'hash_mismatch' if actual else 'missing')
    categories = {}
    for name, table_names, file_role in [
        ('database_tables', list(counts), None), ('llm_exchanges', ['llm_records'], None),
        ('activity', ['activity'], None),
        ('findings_traffic', [n for n in counts if 'finding' in n or 'traffic' in n], None),
        ('transcripts', [], 'transcript'), ('workspace', [], 'workspace')]:
        available = sum(item['role'] == file_role for item in inventory) if file_role else sum(counts.get(t, 0) for t in table_names)
        represented = (sum(item['role'] == file_role and item['imported_status'] == 'preserved' for item in inventory)
                       if file_role else sum(len(imported.get(t, ())) for t in table_names)) if bundle is not None else None
        if file_role and snapshot_only:
            available = None
            represented = None
        categories[name] = {'source_count': available, 'unit': 'files' if file_role else 'rows',
                            'source_status': 'unknown_uninspected_companions' if available is None else 'available' if available else 'missing_or_not_recorded',
                            'imported_count': represented,
                            'imported_basis': 'checksum_verified_artifacts' if file_role else 'distinct_source_rows_in_events',
                            'displayed_count': (display or {}).get(name),
                            'display_status': 'caller_observed' if name in (display or {}) else 'unknown'}
    return {'schema_version': 1, 'source_name': source.name, 'files': inventory,
            'tables': counts, 'categories': categories, 'completeness': 'unknown',
            'inventory_scope': 'snapshot_and_declared_llm_stream_only' if snapshot_only else 'explicit_package',
            'limitations': (['Companion availability is unknown for snapshot JSON; select an explicit extracted directory or full archive to inventory companions.'] if snapshot_only else []) + ['A source export cannot prove complete runtime capture.',
                            'Artifact preservation does not establish parsing or viewer display.',
                            'Display coverage is unknown unless explicitly observed by the caller.',
                            'Absent rows/files may be omitted by export or never recorded.']}


def comparison_report(scenarios):
    """Caller supplies labels, execution IDs, source pins/context; none are inferred."""
    result = []
    for scenario in scenarios:
        coverage = source_coverage(scenario['source'], scenario.get('bundle'), scenario.get('display'))
        result.append({'label': scenario.get('label', 'unlabeled'),
                       'execution_id': scenario.get('execution_id'),
                       'source_pin': scenario.get('source_pin'),
                       'context': scenario.get('context'), 'coverage': coverage})
    pairs = []
    for index, left in enumerate(result):
        for right in result[index + 1:]:
            same = None if not left['execution_id'] or not right['execution_id'] else left['execution_id'] == right['execution_id']
            pairs.append({'left': left['label'], 'right': right['label'],
                          'same_source_execution': same,
                          'basis': 'caller_declared_execution_ids' if same is not None else 'unknown'})
    return {'schema_version': 1, 'scenarios': result, 'execution_comparisons': pairs,
            'limitations': ['Separate seeded scenarios are not paired executions.',
                            'Execution IDs, pins and context are caller declarations, not independently verified.']}


def write_report(report, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    (destination / 'coverage.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    lines = ['# Artex source/import/display coverage', '', 'Runtime completeness: unknown.', '']
    for scenario in report['scenarios']:
        lines.extend([f"## {scenario['label']}", '', '| Category | Source | Imported | Displayed |',
                      '| --- | ---: | ---: | ---: |'])
        for name, value in scenario['coverage']['categories'].items():
            values = [str(value[k]) if value[k] is not None else 'unknown' for k in ('source_count', 'imported_count', 'displayed_count')]
            lines.append('| ' + ' | '.join([name] + values) + ' |')
        lines.extend(['', 'File metadata and SHA-256 checksums are in coverage.json; contents are excluded.', ''])
    lines.extend(report['limitations'])
    (destination / 'coverage.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return report
