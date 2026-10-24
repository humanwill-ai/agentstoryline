"""Best-effort, inert sanitized derivatives of portable recordings.

This does not establish that a recording is safe to share. Review the resulting
file: identifiers and evidence references are deliberately preserved.
"""
import copy
import json
import re
from collections import Counter
from functools import lru_cache

from analysis.explanations import recording_fingerprint, validate_explanations
from core.bundle import validate_bundle
from web.recording import MAX_BYTES

_MARK = '[REDACTED]'
_PROTECTED = frozenset(('id', 'run_id', 'parent_id', 'call_id', 'event_id', 'message_id',
    'actor_id', 'entity_id', 'activity_id', 'lane_id', 'source_event_id', 'target_event_id',
    'source_activity_id', 'target_activity_id', 'event_ids', 'evidence_refs', 'dependencies',
    'schema_version', 'version', 'kind', 'provenance', 'basis', 'method', 'adapter_id',
    'profile_version', 'outcome', 'timestamp', 'started_at', 'created_at', 'sequence', 'status'))
_CREDENTIAL = re.compile(r'(?i)(?:^|[_\- ])(?:password|passwd|secret|token|api[_\- ]?key|authorization|cookie|credential|access[_\- ]?key|private[_\- ]?key)(?:$|[_\- ])')
def _credential_key(key):
    normalized = re.sub(r'[^a-z0-9]', '', key.lower())
    return bool(_CREDENTIAL.search(key) or normalized.endswith(('password', 'passwd', 'secret', 'token', 'apikey', 'authorization', 'cookie', 'credential', 'accesskey', 'privatekey')))


_PATTERNS = (
    ('credentials', re.compile(r'(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9+/_=.~\-]+')),
    ('credentials', re.compile(r'''(?i)\b(?:password|passwd|secret|token|api[_-]?key|authorization|cookie)\b\s*[:=]\s*(?:"[^"\n]*"|'[^'\n]*'|[^\s,;}]+)''')),
    ('credentials', re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')),
    ('credentials', re.compile(r'\b(?:sk-[A-Za-z0-9_-]{12,}|AKIA[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{15,})\b')),
    ('emails', re.compile(r'\b[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b')),
    ('targets', re.compile(r'(?i)\blocalhost(?::\d+)?\b')),
    ('targets', re.compile(r'(?i)\b(?:https?|wss?|ftp|ssh)://[^\s<>"\'\\]+')),
    ('targets', re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?(?![\w.])')),
    ('targets', re.compile(r'(?i)(?<![\w:])(?:[0-9a-f]{1,4}:){2,}[0-9a-f:]{0,39}(?![\w:])')),
    ('targets', re.compile(r'(?i)\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}\b')),
    ('local_paths', re.compile(r'(?i)(?:[A-Z]:\\|\\\\)[^\s"\'<>]+')),
    ('local_paths', re.compile(r'(?<![\w:])(?:~?/)(?:[^\s/"\'<>]+/)+[^\s"\'<>]*')),
)


def _validate(package):
    if (not isinstance(package, dict) or set(package) != {'format', 'version', 'payload', 'attachments'}
            or package['format'] != 'traceforge-recording' or type(package['version']) is not int
            or package['version'] != 1):
        raise ValueError('unsupported portable recording envelope')
    count = 0
    def bounds(value, depth=0):
        nonlocal count
        count += 1
        if count > 1000000 or depth > 48:
            raise ValueError('recording structure exceeds import limits')
        if isinstance(value, str) and len(value) > 32 * 1024 * 1024:
            raise ValueError('recording text exceeds import limits')
        if isinstance(value, dict):
            if any(not isinstance(k, str) for k in value):
                raise ValueError('recording keys must be strings')
            for child in value.values():
                bounds(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                bounds(child, depth + 1)
    bounds(package)
    if len(json.dumps(package, ensure_ascii=False, allow_nan=False).encode()) > MAX_BYTES:
        raise ValueError('portable recording exceeds 128 MiB')
    p = package['payload']
    if not isinstance(p, dict) or any(not isinstance(p.get(k), dict) for k in ('bundle', 'flow', 'narrative', 'storyline')):
        raise ValueError('missing portable display objects')
    for owner, key in ((p['bundle'], 'events'), (p['flow'], 'messages'), (p['flow'], 'actors'),
                       (p['storyline'], 'activities'), (p['storyline'], 'lanes')):
        rows = owner.get(key)
        if not isinstance(rows, list) or len(rows) > 5000:
            raise ValueError('invalid portable display collection')
        ids = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] in ids:
                raise ValueError('invalid or duplicate collection identifier')
            ids.add(row['id'])
    core = copy.deepcopy(p['bundle'])
    core['manifest'].pop('artifact_refs', None)
    for event in core['events']:
        event.pop('artifact_refs', None)
    validate_bundle(core)
    event_ids = {event['id'] for event in p['bundle']['events']}
    for message in p['flow']['messages']:
        if 'milestones' not in message:
            continue
        milestones = message['milestones']
        if not isinstance(milestones, list) or len(milestones) > 16:
            raise ValueError('invalid source milestones')
        own = message.get('event_ids')
        for item in milestones:
            if (not isinstance(item, dict) or set(item) != {'label', 'outcome', 'event_ids', 'provenance', 'basis'}
                    or not isinstance(item['label'], str) or not item['label'].strip() or len(item['label']) > 120
                    or any(ord(c) < 32 or ord(c) == 127 or c in '\u0085\u2028\u2029' for c in item['label'])
                    or not isinstance(item['outcome'], str) or item['outcome'] not in ('attempt', 'success', 'failure', 'unconfirmed')
                    or item['provenance'] != 'source_reported' or item['basis'] != 'explicit_source_outcome'
                    or not isinstance(own, list) or len(own) != 1 or own[0] not in event_ids
                    or item['event_ids'] != own):
                raise ValueError('invalid source milestone')
    if p.get('explanations') is not None:
        validate_explanations(p['bundle'], p['flow'], p['explanations'])
    files = package['attachments']
    if not isinstance(files, list) or len(files) > 20:
        raise ValueError('invalid portable attachments')
    for f in files:
        if (not isinstance(f, dict) or set(f) != {'name', 'language', 'text'} or f['language'] != 'python'
                or not isinstance(f['name'], str) or not 1 <= len(f['name']) <= 180
                or not isinstance(f['text'], str) or len(f['text'].encode()) > 1024 * 1024):
            raise ValueError('invalid inert Python attachment')


def prepare_share(package, options=None):
    """Return {package, report}; never mutate the source, read files or call models.

    Options are custom_terms (literal, case-sensitive) and preserve_audio (false).
    Built-in protections cannot be disabled. Counts refer to matches across all
    duplicated fields, not unique secrets. No secret values occur in the report.
    """
    try:
        _validate(package)
    except (ValueError, TypeError, KeyError, OverflowError):
        raise ValueError('source recording failed validation; inspect the source locally') from None
    options = {} if options is None else options
    if not isinstance(options, dict) or set(options) - {'custom_terms', 'preserve_audio'}:
        raise ValueError('unsupported sharing options')
    terms = options.get('custom_terms', [])
    if (not isinstance(terms, list) or len(terms) > 100
            or any(not isinstance(t, str) or not t or len(t) > 1000 for t in terms)):
        raise ValueError('custom_terms requires at most 100 nonempty literals of at most 1000 characters')
    if type(options.get('preserve_audio', False)) is not bool:
        raise ValueError('preserve_audio must be boolean')
    counts, omitted = Counter(), Counter()
    credential_key = lru_cache(maxsize=4096)(_credential_key)
    known = set()
    def decoded(text):
        if text.lstrip().startswith(('{', '[', '"')):
            try:
                return json.loads(text)
            except (ValueError, RecursionError):
                pass
        return None
    def collect(value, depth=0):
        if depth > 48:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if credential_key(key):
                    def leaves(x):
                        if isinstance(x, str) and x:
                            known.add(x)
                        elif isinstance(x, (dict, list)):
                            for v in (x.values() if isinstance(x, dict) else x):
                                leaves(v)
                    leaves(child)
                collect(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                collect(child, depth + 1)
        elif isinstance(value, str):
            for match in re.finditer(r'(?i)\b(?:Bearer|Basic)\s+([A-Za-z0-9+/_=.~\-]+)', value):
                known.add(match.group(1))
            parsed = decoded(value)
            if parsed is not None and parsed != value:
                collect(parsed, depth + 1)
    collect(package)
    literals = sorted(set(terms), key=len, reverse=True)
    credentials = sorted(known, key=len, reverse=True)
    # Propagate learned secrets without scanning every string once per secret.
    # Refuse unusually large rule sets rather than silently omitting protections.
    if len(credentials) > 4096 or sum(len(v) for v in credentials) > 1024 * 1024:
        raise ValueError('too many credential literals for bounded sanitization; source requires manual review')
    literal_patterns = [(category, re.compile('|'.join(re.escape(v) for v in values)))
                        for category, values in (('credentials', credentials), ('custom_terms', literals)) if values]
    def redact_text(value):
        matches = Counter()
        for category, pattern in literal_patterns:
            value, n = pattern.subn(lambda match: _MARK, value)
            if n:
                matches[category] += n
        for category, pattern in _PATTERNS:
            value, n = pattern.subn(_MARK, value)
            if n:
                matches[category] += n
        return value, tuple(matches.items())
    # Repeated raw dictionary keys and repeated short evidence dominate large
    # packages. The per-call bounded cache retains no source data after return.
    cached_text = lru_cache(maxsize=8192)(redact_text)
    def text(value):
        cleaned, matches = cached_text(value) if len(value) <= 4096 else redact_text(value)
        if matches:
            counts.update(dict(matches))
        return cleaned
    raw_fields = frozenset(('data', 'extensions', 'source_record', 'narrative', 'recorded_source', 'source', 'parameters', 'arguments', 'result'))
    actual_ids = set()
    source_times = {package['payload']['bundle']['manifest']['started_at']}
    for event in package['payload']['bundle']['events']:
        actual_ids.update(event[k] for k in ('id', 'run_id', 'call_id') if isinstance(event.get(k), str))
        source_times.add(event['timestamp'])
    identity_rows = frozenset(('messages', 'actors', 'entities', 'chapters', 'lanes', 'activities', 'edges', 'activity_groups', 'workers', 'coordinators', 'context', 'entries', 'segments', 'annotations'))
    def identities(value, path=()):
        if isinstance(value, dict):
            if len(path) >= 2 and path[-1] == '*' and path[-2] in identity_rows and isinstance(value.get('id'), str):
                actual_ids.add(value['id'])
            for k, child in value.items():
                if k not in raw_fields:
                    identities(child, path + (k,))
        elif isinstance(value, list):
            for child in value:
                identities(child, path + ('*',))
    for collection in ('flow', 'storyline'):
        identities(package['payload'][collection])

    def protected(path, key, child):
        # Preserve only actual schema/identity fields, never arbitrary data with
        # an identifier-like key. Even dependency lists are traversed.
        if isinstance(child, dict):
            return False
        if isinstance(child, list) and not all(isinstance(v, str) for v in child):
            return False
        if path[:2] in (('payload', 'flow'), ('payload', 'storyline')):
            if key == 'id' or key.endswith(('_id', '_ids')) or key == 'evidence_refs':
                refs = child if isinstance(child, list) else [child]
                if refs and all(isinstance(v, str) and v in actual_ids for v in refs):
                    return True
            if (key == 'timestamp' or key.endswith('_timestamp') or key in ('started_at', 'ended_at')) and isinstance(child, str) and child in source_times:
                return True
        if path == ('payload', 'bundle', 'manifest'):
            return key in ('schema_version', 'run_id', 'started_at')
        if path == ('payload', 'bundle', 'manifest', 'agent'):
            return key == 'version'
        if path == ('payload', 'bundle', 'manifest', 'completeness'):
            return key == 'status'
        if path[:3] == ('payload', 'bundle', 'events'):
            if len(path) == 4:
                return key in ('schema_version', 'id', 'run_id', 'kind', 'timestamp', 'sequence',
                               'parent_id', 'call_id', 'evidence_refs')
            if len(path) == 6 and path[4] == 'dependencies':
                return key in ('event_id', 'provenance')
        if len(path) == 4 and path[:2] in (('payload', 'flow'), ('payload', 'storyline')):
            if key == 'kind':
                return child in ('model.request', 'model.response', 'tool.start', 'tool.end', 'run.start', 'run.end', 'observation', 'model', 'tool', 'run', 'dependency', 'parent', 'evidence', 'agent', 'target', 'worker', 'coordinator', 'setup')
            return key == 'id' or key.endswith(('_id', '_ids')) or key == 'evidence_refs'
        if path[:3] == ('payload', 'flow', 'messages'):
            if (len(path) == 5 and path[4] == 'description') or (len(path) == 6 and path[4] in ('context', 'semantic_tags', 'dependencies', 'milestones')):
                return key in ('event_id', 'event_ids', 'evidence_refs', 'basis', 'provenance', 'scope', 'outcome')
        if path[:2] == ('payload', 'explanations'):
            return key in _PROTECTED
        if path[:3] == ('payload', 'replay', 'steps') and len(path) == 4:
            return key == 'id' or key.endswith(('_id', '_ids')) or key in ('evidence_refs', 'kind')
        return False

    def walk(value, key='', depth=0, free=False, path=()):
        if depth > 48:
            raise ValueError('encoded evidence exceeds sharing depth limit')
        if isinstance(value, dict):
            result = {}
            for k, child in value.items():
                result_key = text(k) if free and not (path == ('payload', 'bundle', 'manifest', 'source') and k == 'adapter') else k
                if result_key in result:
                    raise ValueError('redaction would merge evidence field names; source requires manual review')
                if k in ('artifact_refs', 'artifact_content', 'artifact_contents', 'artifacts'):
                    omitted['original_artifacts'] += len(child) if isinstance(child, (list, dict)) else 1
                    continue
                if (path == ('payload', 'bundle', 'events', '*', 'data') and k == 'source_kind'
                        and child in ('archive.transcript', 'archive.snapshot',
                                      'request.history.tool_use', 'request.history.tool_result')):
                    result[result_key] = child
                elif credential_key(k):
                    counts['credentials'] += 1
                    result[result_key] = _MARK
                elif (not free and protected(path, k, child)) or (path == ('payload', 'bundle', 'manifest', 'source') and k == 'adapter' and isinstance(child, str)):
                    result[result_key] = copy.deepcopy(child)
                else:
                    result[result_key] = walk(child, k, depth + 1,
                        free or k in raw_fields, path + (k,))
            return result
        if isinstance(value, list):
            return [walk(v, key, depth + 1, free, path + ('*',)) for v in value]
        if isinstance(value, str):
            if not free and path[:2] in (('payload', 'flow'), ('payload', 'storyline')) and value in actual_ids:
                return value
            parsed = decoded(value)
            if parsed is not None and parsed != value:
                return json.dumps(walk(parsed, depth=depth + 1, free=free, path=path), ensure_ascii=False)
            return text(value)
        return value
    # Cross-source content hashes become invalid after redaction. Keep the
    # sanitized derivative primary-only; ordinary Export retains the attachment.
    share_source = dict(package, payload=dict(package['payload']))
    had_telemetry = 'telemetry' in share_source['payload']
    share_source['payload'].pop('telemetry', None)
    share_source['payload'].pop('telemetry_views', None)
    result = walk(share_source)
    omitted['attachments'] = len(package['attachments'])
    result['attachments'] = []
    p = result['payload']
    if not options.get('preserve_audio', False):
        if p.get('notification_sound'):
            omitted['audio'] = 1
        p['notification_sound'] = None
    else:
        # Restore only the bounded WAV already validated by the viewer contract.
        sound = package['payload'].get('notification_sound')
        if sound and (not isinstance(sound, str) or not re.fullmatch(r'data:audio/wav;base64,[A-Za-z0-9+/=]+', sound) or len(sound) > 3 * 1024 * 1024):
            raise ValueError('invalid embedded notification audio')
        p['notification_sound'] = sound
    source_hash = recording_fingerprint(package['payload']['bundle'])
    declaration = {'kind': 'sanitized-derivative', 'version': 1, 'source_bundle_sha256': source_hash,
                   'original_artifacts': 'omitted', 'attachments': 'omitted',
                   'evidence': 'redacted; source original remains separate',
                   'annotations': 'redacted-existing-not-regenerated'}
    manifest = p['bundle']['manifest']
    original_source = manifest.get('source')
    if isinstance(original_source, dict):
        manifest['source'] = {**original_source, 'sanitization': declaration}
    else:
        manifest['source'] = {'sanitization': declaration}
        if original_source is not None:
            manifest['source']['recorded_source'] = original_source
    completeness = manifest['completeness']
    completeness['status'] = 'partial'
    completeness['gaps'].append('Sanitized sharing derivative: text redacted; original artifact references and Python attachments omitted. Review required; this is not the unchanged source recording.')
    fingerprint = recording_fingerprint(p['bundle'])
    if p.get('explanations') is not None:
        p['explanations']['bundle_sha256'] = fingerprint
    p['sharing'] = {**declaration, 'derived_bundle_sha256': fingerprint,
                    'review_required': True, 'identifiers_preserved': True}
    p['manifest_text'] = json.dumps(manifest, ensure_ascii=False, indent=2)
    # Keep exact redacted source dumps available, consistent with the derivative.
    p['evidence_text'] = {e['id']: {'event': json.dumps(e, ensure_ascii=False, indent=2),
                                  'data': json.dumps(e.get('data', {}), ensure_ascii=False, indent=2),
                                  'sequence': str(e['sequence'])} for e in p['bundle']['events']}
    for message in p['flow']['messages']:
        description = message.get('description')
        if isinstance(description, dict):
            description_text, source_path = description.get('text'), description.get('source_path')
            if (not isinstance(description_text, str) or not description_text.strip()
                    or len(description_text) > 280
                    or any(ord(c) < 32 or c in '\u0085\u2028\u2029' for c in description_text)
                    or not isinstance(source_path, str) or not source_path.strip() or len(source_path) > 512):
                raise ValueError('sanitized recorded description exceeds viewer limits; adjust custom terms or source metadata')
    try:
        _validate(result)
    except (ValueError, TypeError, KeyError, OverflowError):
        raise ValueError('sanitized derivative failed validation; adjust custom terms or source metadata') from None
    report = {'rule_counts': {k: v for k, v in sorted(counts.items()) if v},
              'omitted_attachments': omitted['attachments'],
              'omitted_original_artifacts': omitted['original_artifacts'],
              'omitted_audio': omitted['audio'], 'omitted_telemetry': had_telemetry, 'review_required': True,
              'warnings': ['Best-effort sanitization cannot guarantee removal of all sensitive information. Review the exported derivative before sharing.',
                           'Identifiers and evidence references are preserved and may themselves contain sensitive information.',
                           'Existing descriptions were redacted and rebound to this derivative; they were not regenerated or fact-checked.']}
    if had_telemetry:
        report['warnings'].append('Attached Orca telemetry omitted: cross-source content matches cannot be retained after redaction. Ordinary Export preserves the original attachment.')
    return {'package': result, 'report': report}
