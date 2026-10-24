"""Explicitly selected Orca sessions, ordered by time without causal inference."""
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from core.bundle import load_bundle
from .importer import import_orcareplay, OrcaReplayImportError

MAX_COLLECTION_BYTES = 32 * 1024 * 1024
MAX_COLLECTION_EVENTS = 5000


def import_orca_collection(sources, destination):
    sources = list(sources)
    if not 2 <= len(sources) <= 16:
        raise ValueError('select 2–16 Orca recording files')
    destination = Path(destination)
    destination.mkdir(mode=0o700)
    sessions, events, inventory, refs, gaps, seen = [], [], [], [], set(), set()
    total = 0
    for position, source in enumerate(sources):
        source = Path(source)
        # A .orca file is a native ZIP container, not a general archive input.
        if not source.name.lower().endswith(('.orca', '.orca.zip')):
            raise ValueError('collections accept only native .orca or .orca.zip files')
        from web.viewer_import import _read_for_detection
        raw = _read_for_detection(source, MAX_COLLECTION_BYTES)
        total += len(raw)
        if total > MAX_COLLECTION_BYTES:
            raise ValueError('Orca collection exceeds 32 MiB aggregate limit')
        if not raw.startswith(b'PK'):
            raise ValueError('collections require native Orca ZIP containers')
        member_root = destination / ('member-%d' % position)
        with tempfile.TemporaryDirectory(prefix='traceforge-orca-source-') as temporary:
            selected = Path(temporary).resolve() / 'selected.orca'
            selected.write_bytes(raw)
            member = import_orcareplay(selected, member_root)
        native = member['manifest']['source']['native_manifest']
        run_id = native['run_id']
        if run_id in seen:
            raise ValueError('duplicate Orca run_id in collection')
        seen.add(run_id)
        session_id = 'orca-session-' + hashlib.sha256(run_id.encode()).hexdigest()[:24]
        boundary = next((event['data']['source_record'].get('attrs', {}) for event in member['events'] if event['kind'] == 'run.start'), {})
        run_key = native.get('run_key', boundary.get('run_key', run_id))
        if not isinstance(run_key, str) or not run_key:
            raise ValueError('Orca run_key must be a nonempty string')
        prefix = session_id + ':'
        ids = {event['id']: prefix + event['id'] for event in member['events']}
        session = {'id': session_id, 'run_id': run_id, 'run_key': run_key, 'label': run_key,
                   'event_count': len(member['events']), 'selection_index': position,
                   'source_sha256': hashlib.sha256(raw).hexdigest(),
                   'native_manifest': native, 'event_id_map': ids}
        sessions.append(session)
        gaps.update(member['manifest']['completeness']['gaps'])
        artifact_prefix = 'artifacts/orcareplay/files/' + session_id + '/'
        def remap(event):
            mapped = copy.deepcopy(event)
            for key in ('parent_id', 'call_id'):
                if isinstance(mapped.get(key), str):
                    mapped[key] = prefix + mapped[key]
            for key in ('depends_on', 'evidence_refs'):
                if isinstance(mapped.get(key), list):
                    mapped[key] = [prefix + value if isinstance(value, str) else value for value in mapped[key]]
            for ref in mapped.get('artifact_refs', []):
                if isinstance(ref, dict) and isinstance(ref.get('path'), str):
                    ref['path'] = artifact_prefix + ref['path'][len('artifacts/orcareplay/files/'):]
            # Only importer-derived blob pointers are rewritten; source payloads
            # and model/tool protocol identifiers remain exact evidence.
            for key in ('content', 'result'):
                value = mapped['data'].get(key)
                original = event['data']['source_record'].get('payload')
                if (isinstance(value, dict) and isinstance(original, dict) and '$blob' in original
                        and isinstance(value.get('artifact_path'), str)):
                    value['artifact_path'] = artifact_prefix + value['artifact_path'][len('artifacts/orcareplay/files/'):]
            return mapped
        for event in member['events']:
            mapped = remap(event)
            mapped['id'] = ids[event['id']]
            mapped['data']['source_session'] = {k: session[k] for k in ('id', 'run_id', 'run_key')}
            mapped['data']['source_session'].update(event_id=event['id'], sequence=event['sequence'])
            events.append(mapped)
        if len(events) > MAX_COLLECTION_EVENTS:
            raise ValueError('Orca collection exceeds 5,000 portable events')
        for item in member['manifest']['source']['archive_inventory']:
            row = dict(item, path='files/' + session_id + '/' + item['path'][len('files/'):])
            inventory.append(row)
            refs.append({'path': 'artifacts/orcareplay/' + row['path'], 'sha256': row['sha256']})
        target = destination / 'artifacts/orcareplay/files' / session_id
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(member_root / 'artifacts/orcareplay/files'), target)
        # Reserved child directory prevents collisions with source members.
        original = destination / 'artifacts/orcareplay/files' / (session_id + '-container') / 'original.orca'
        original.parent.mkdir()
        original.write_bytes(raw)
        row = {'path': 'files/' + session_id + '-container/original.orca', 'bytes': len(raw), 'sha256': session['source_sha256']}
        inventory.append(row)
        refs.append({'path': 'artifacts/orcareplay/' + row['path'], 'sha256': row['sha256']})
        shutil.rmtree(member_root)
    events.sort(key=lambda event: datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00')))
    collection_hash = hashlib.sha256('\0'.join(sorted(seen)).encode()).hexdigest()[:24]
    run = 'orcareplay-collection-' + collection_hash
    for sequence, event in enumerate(events):
        event['sequence'], event['run_id'] = sequence, run
    gaps.add('User selected these files together; common task membership and cross-session causality are not established. Display order is canonical absolute time with selection/source order for ties.')
    manifest = {'schema_version': '0.1', 'run_id': run, 'agent': {'name': 'Orca session collection', 'version': '1'},
                'started_at': events[0]['timestamp'] if events else sessions[0]['native_manifest']['created_at'],
                'completeness': {'status': 'partial', 'gaps': sorted(gaps)}, 'artifact_refs': refs,
                'source': {'adapter': 'orcareplay', 'experimental': True, 'format': 'orca-session-collection',
                           'archive_inventory': inventory, 'event_count': len(events),
                           'collection': {'version': 1, 'association_basis': 'user_selected_files_not_proven_same_task', 'sessions': sessions}}}
    (destination / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    (destination / 'events.jsonl').write_text(''.join(json.dumps(event) + '\n' for event in events), encoding='utf-8')
    return load_bundle(destination)
