"""Portable display packages. Attached source is data, never execution input."""
import json
import copy
from pathlib import Path

MAX_BYTES = 128 * 1024 * 1024


def write_recording(payload, destination, *, replay_scripts=(), attachments=()):
    if not isinstance(attachments, (list, tuple)):
        raise ValueError("attachments must be a list")
    if len(replay_scripts) + len(attachments) > 20:
        raise ValueError('at most 20 replay scripts may be attached')
    attachments = copy.deepcopy(list(attachments))
    for item in attachments:
        if (not isinstance(item, dict) or set(item) != {'name', 'language', 'text'}
                or not isinstance(item['name'], str) or not 1 <= len(item['name']) <= 180
                or item['language'] != 'python' or not isinstance(item['text'], str)
                or len(item['text'].encode('utf-8')) > 1024 * 1024):
            raise ValueError('invalid preserved Python attachment')
    for source in replay_scripts:
        path = Path(source)
        if path.suffix.lower() != '.py':
            raise ValueError('replay script attachments must be .py files')
        with path.open('rb') as handle:
            raw = handle.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('replay script attachment exceeds 1 MiB')
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('replay script attachments must be UTF-8') from exc
        attachments.append({'name': path.name[:180], 'language': 'python', 'text': text})
    for key, owner in [('messages', payload.get('flow', {})), ('actors', payload.get('flow', {})),
                       ('events', payload.get('bundle', {})), ('activities', payload.get('storyline', {})),
                       ('lanes', payload.get('storyline', {}))]:
        rows = owner.get(key)
        if not isinstance(rows, list) or len(rows) > 5000:
            raise ValueError('portable recordings require flow data with at most 5,000 rows per collection')
    package = {'format': 'traceforge-recording', 'version': 1, 'payload': payload, 'attachments': attachments}
    count = 0
    def check(value, depth=0):
        nonlocal count
        count += 1
        if count > 1000000 or depth > 48:
            raise ValueError('recording structure exceeds import limits')
        if isinstance(value, str) and len(value) > 32 * 1024 * 1024:
            raise ValueError('recording text exceeds import limits')
        if isinstance(value, dict):
            for child in value.values():
                check(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                check(child, depth + 1)
    check(package)
    data = json.dumps(package, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(data) > MAX_BYTES:
        raise ValueError('portable recording exceeds 128 MiB')
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as handle:
        handle.write(data)
    return path
