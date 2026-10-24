"""Conservative, reproducible attachment of supplemental model telemetry.

Neither source is mutated. Session labels, clocks and model names are never
identity evidence; matching is one-to-one and requires recorded content or IDs.
"""
from copy import deepcopy
import hashlib
import json

from .explanations import recording_fingerprint


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            pass
    return value


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'))


def _response(event):
    d = event.get('data', {}) if event else {}
    row = d.get('source_record', {})
    raw = row.get('raw_response') or d.get('resolved_payload')
    if isinstance(raw, str) and raw.startswith('data:'):
        chunks = []
        ids = set()
        for line in raw.splitlines():
            if not line.startswith('data:'):
                continue
            item = _json(line[5:].strip())
            if not isinstance(item, dict):
                continue
            if item.get('id'):
                ids.add(item['id'])
            # Preserve every semantic chunk and its ordering, ignoring envelope clocks.
            choices = item.get('choices')
            if choices:
                chunks.append(choices)
        return (_canonical(chunks) if chunks else None), ids
    value = _json(row.get('response_body')) or d.get('response')
    if isinstance(value, dict):
        value = {k: v for k, v in value.items() if k not in ('usage', 'model', 'id', 'created')}
    return (_canonical(value) if value else None), set()


def _request(event):
    d = event.get('data', {})
    row = d.get('source_record', {})
    value = _json(row.get('raw_request'))
    if not isinstance(value, dict):
        value = d.get('request')
    if not isinstance(value, dict):
        value = _json(row.get('request_body'))
    if not isinstance(value, dict) or not value.get('messages'):
        return None
    messages = deepcopy(value['messages'])
    # String and singleton text-block content have identical semantics.
    for message in messages:
        content = message.get('content')
        if isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict) and content[0].get('type') == 'text':
            message['content'] = content[0].get('text', '')
    system = value.get('system')
    if system:
        if isinstance(system, list) and all(isinstance(s, str) for s in system):
            system = '\n\n'.join(system)
        messages.insert(0, {'role': 'system', 'content': system})
    return _canonical(messages)


def _exchanges(bundle):
    events = bundle.get('events', [])
    requests = [e for e in events if e.get('kind') == 'model.request']
    responses = [e for e in events if e.get('kind') == 'model.response']
    result = []
    for req in requests:
        d = req.get('data', {})
        session = d.get('source_session', {}).get('id')
        seq = d.get('source_session', {}).get('sequence')
        linked = [r for r in responses if r.get('parent_id') == req['id'] or
                  (req.get('call_id') and r.get('call_id') == req['call_id']) or
                  (session and r.get('data', {}).get('source_session', {}).get('id') == session and
                   seq in r.get('data', {}).get('source_causes', {}).get('sequences', []))]
        resp = linked[0] if len(linked) == 1 else None
        body, ids = _response(resp)
        for e in [req, resp]:
            if e:
                rid = e.get('data', {}).get('source_record', {}).get('attrs', {}).get('request_id')
                if rid:
                    ids.add(rid.removeprefix('chatcmpl-'))
        ids = {i.removeprefix('chatcmpl-') for i in ids}
        model = (d.get('model_identity', {}).get('model') or d.get('source_record', {}).get('model') or
                 (d.get('request') or {}).get('model'))
        if isinstance(model, str):
            model = model.rsplit('/', 1)[-1]
        result.append({'request': req, 'response': resp, 'session': session, 'model': model,
                       'body': body, 'messages': _request(req), 'ids': ids})
    return result


def build_telemetry_attachment(artex_bundle, orca_bundle):
    """Build an evidence map. Ambiguity is retained instead of tie-broken."""
    primary, telemetry = _exchanges(artex_bundle), _exchanges(orca_bundle)
    candidates, conflicts = {}, []
    for oi, o in enumerate(telemetry):
        candidates[oi] = []
        for ai, a in enumerate(primary):
            equal = [field for field in ('body', 'messages') if a[field] and a[field] == o[field]]
            shared = sorted(a['ids'] & o['ids'])
            differing = [field for field in ('body', 'messages') if a[field] and o[field] and a[field] != o[field]]
            if not equal and not shared:
                continue
            if a['model'] and o['model'] and a['model'] != o['model']:
                differing.append('model')
            if differing:
                conflicts.append({'orca_request_id': o['request']['id'], 'artex_request_id': a['request']['id'],
                                  'fields': differing, 'reason': 'Recorded content disagrees'})
                continue
            candidates[oi].append((ai, equal, shared))
    reverse = {}
    for oi, choices in candidates.items():
        for ai, _, _ in choices:
            reverse.setdefault(ai, []).append(oi)
    matches, unmatched, ambiguous = [], [], []
    conflict_ids = {c['orca_request_id'] for c in conflicts}
    for oi, o in enumerate(telemetry):
        choices = candidates[oi]
        if len(choices) != 1 or len(reverse[choices[0][0]]) != 1:
            (ambiguous if choices else unmatched).append(o['request']['id'])
            continue
        ai, equal, shared = choices[0]
        a = primary[ai]
        matches.append({'artex_request_id': a['request']['id'],
                        'artex_response_id': a['response']['id'] if a['response'] else None,
                        'orca_request_id': o['request']['id'],
                        'orca_response_id': o['response']['id'] if o['response'] else None,
                        'session_id': o['session'], 'basis': 'exact_recorded_content' if equal else 'shared_request_id',
                        'evidence': {'equal_fields': equal, 'shared_request_ids': shared,
                                     'content_sha256': {f: hashlib.sha256(a[f].encode()).hexdigest() for f in equal}}})
    matched_ids = {m['orca_request_id'] for m in matches}
    unresolved = {o['session'] for o in telemetry if o['session'] and o['request']['id'] not in matched_ids}
    return {'version': 1, 'primary_fingerprint': recording_fingerprint(artex_bundle),
            'telemetry_fingerprint': recording_fingerprint(orca_bundle), 'bundle': deepcopy(orca_bundle),
            'matches': matches, 'unmatched_orca_request_ids': unmatched,
            'ambiguous_orca_request_ids': ambiguous, 'conflicts': conflicts,
            'summary': {'matched_exchanges': len(matches), 'orca_only_exchanges': len(unmatched),
                        'ambiguous_exchanges': len(ambiguous), 'conflicting_exchanges': len(conflict_ids),
                        'unresolved_sessions': len(unresolved)}}


def validate_telemetry_attachment(artex_bundle, attachment):
    """Reject stale bindings or forged maps; return the independently rebuilt map."""
    if not isinstance(attachment, dict) or not isinstance(attachment.get('bundle'), dict):
        raise ValueError('Invalid telemetry attachment')
    expected = build_telemetry_attachment(artex_bundle, attachment['bundle'])
    if attachment != expected:
        raise ValueError('Telemetry attachment does not match its recorded sources')
    return expected
