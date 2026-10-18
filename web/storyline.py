"""Evidence-derived presentation model. No execution or behavioral classification."""
from datetime import datetime, timezone
from urllib.parse import urlsplit


def _dict(value):
    return value if isinstance(value, dict) else {}


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, OverflowError):
        return None



def _label(event, paired):
    kind = event.get('kind', 'observation')
    data = _dict(event.get('data'))
    if paired and kind == 'model.request':
        return 'Model exchange'
    if paired and kind == 'run.start':
        return str(data.get('operation') or 'Agent operation')
    if kind == 'tool.start' and data.get('execution_status') == 'proposal_only_not_execution':
        return 'Tool proposal: ' + str(event.get('tool') or 'unnamed tool')
    if kind.startswith('tool.'):
        arguments = _dict(data.get('arguments'))
        method, url = arguments.get('method'), arguments.get('url')
        if isinstance(method, str) and method.upper() in ('GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'CONNECT', 'TRACE') and isinstance(url, str):
            try:
                parsed = urlsplit(url)
                if parsed.scheme in ('http', 'https') and parsed.hostname:
                    path = parsed.path or '/'
                    if not any(ord(c) < 32 or ord(c) == 127 for c in path):
                        return method.upper() + ' ' + path
            except ValueError:
                pass
        return str(event.get('tool') or kind)
    if kind == 'observation' and isinstance(data.get('source_table'), str):
        table = data['source_table']
        return {'tasks': 'Task record', 'task': 'Task record', 'nodes': 'Node record', 'node': 'Node record'}.get(table, table + ' record')
    return str(event.get('tool') or kind)


def build_storyline(bundle, narrative):
    """Build a deterministic, non-mutating view of even sparse renderer inputs.

    Pairing requires unique explicit IDs and a recorded endpoint relationship.
    Sequence proximity and shared tool names never establish a duration.
    """
    bundle, narrative = _dict(bundle), _dict(narrative)
    manifest = _dict(bundle.get('manifest'))
    events = [e for e in bundle.get('events', []) if isinstance(e, dict) and isinstance(e.get('id'), str)]
    stamps = {e['id']: _time(e.get('timestamp')) for e in events}
    known = [t for t in stamps.values() if t is not None]
    initial = _time(manifest.get('started_at'))
    origin = min(known + ([initial] if initial else []), default=None)
    offset = lambda eid: (stamps[eid] - origin).total_seconds() * 1000 if stamps.get(eid) and origin else None
    notes = list(_dict(manifest.get('completeness')).get('gaps', []))
    lanes, lane_keys = [], {}

    def identity(event):
        data = _dict(event.get('data'))
        meta = _dict(data.get('recording_metadata'))
        source = _dict(manifest.get('source'))
        session = meta.get('session_id') or data.get('session_id') or source.get('session_id')
        record = _dict(data.get('source_record'))
        agent = data.get('agent_id') or meta.get('agent_id') or record.get('agent_id') or record.get('agent')
        worker = record.get('worker')
        node = record.get('node_id') if not session else None
        exploration = record.get('exploration_id') if not session else None
        # These are source identities, never workers inferred from overlap.
        return tuple(str(v) if v is not None else '' for v in (session, agent, worker, node, exploration))

    def lane(event):
        key = identity(event)
        if key not in lane_keys:
            lid = 'lane:' + str(len(lanes) + 1)
            lane_keys[key] = lid
            name = str(_dict(manifest.get('agent')).get('name') or 'Source agent')
            detail = ', '.join(f'{label}: {value}' for label, value in zip(('session', 'agent', 'worker', 'node', 'exploration'), key) if value)
            lanes.append({'id': lid, 'label': name, 'detail': detail or 'No separate agent identity recorded'})
        return lane_keys[key]

    groups = {}
    for e in events:
        if e.get('kind') in ('tool.start', 'tool.end', 'model.request', 'model.response', 'run.start', 'run.end') and e.get('call_id'):
            category = e['kind'].split('.')[0]
            groups.setdefault((category, identity(e), e['call_id']), []).append(e)
    partners = {}
    for (category, _, _), members in groups.items():
        start_kind, end_kind = (('tool.start', 'tool.end') if category == 'tool'
                                else ('model.request', 'model.response') if category == 'model'
                                else ('run.start', 'run.end'))
        starts = [e for e in members if e.get('kind') == start_kind]
        ends = [e for e in members if e.get('kind') == end_kind]
        if len(starts) != 1 or len(ends) != 1:
            continue
        start, end = starts[0], ends[0]
        blocked = any(_dict(e.get('data')).get('repeated_native_item_id') or _dict(e.get('data')).get('correlation_basis') == 'uncorrelated_transcript_item' for e in members)
        start_lifecycle, end_lifecycle = _dict(_dict(start.get('data')).get('span_lifecycle')), _dict(_dict(end.get('data')).get('span_lifecycle'))
        same_lifecycle = (start_lifecycle.get('relationship') == 'recorded_same_span_boundary_not_data_dependency'
                          and end_lifecycle.get('relationship') == 'recorded_same_span_boundary_not_data_dependency'
                          and start_lifecycle.get('trace_id') == end_lifecycle.get('trace_id')
                          and start_lifecycle.get('span_id') == end_lifecycle.get('span_id'))
        linked = (same_lifecycle or end.get('parent_id') == start['id']
                  or any(_dict(d).get('event_id') == start['id'] and _dict(d).get('provenance') == 'recorded'
                         for d in end.get('dependencies', [])))
        if linked and not blocked and (not start.get('tool') or not end.get('tool') or start['tool'] == end['tool']):
            partners[start['id']] = end
            partners[end['id']] = start

    activities, event_activity = [], {}
    histories = []
    for e in events:
        if e['id'] in event_activity:
            continue
        data = _dict(e.get('data'))
        if str(data.get('source_kind', '')).startswith('request.history.'):
            histories.append(e)
            continue
        pair = partners.get(e['id'])
        members = [e]
        if pair:
            members = [e, pair] if e.get('kind') in ('tool.start', 'model.request', 'run.start') else [pair, e]
        first, last = members[0], members[-1]
        start, end = offset(first['id']), offset(last['id'])
        kind = first.get('kind', 'observation')
        category = kind.split('.')[0] if kind.split('.')[0] in ('model', 'tool', 'run') else 'observation'
        activity_notes = []
        if data.get('execution_status') == 'proposal_only_not_execution':
            activity_notes.append('Source records a tool proposal; execution is not established.')
        if category == 'tool':
            activity_notes.append('Invocation/result records do not by themselves prove approval, execution, or success.')
        observation_only = any(_dict(m.get('data')).get('timestamp_basis') in ('row_persistence', 'first_observed_in_this_request_not_execution') for m in members)
        if observation_only:
            activity_notes.append('Recorded persistence timestamps do not establish actual execution boundaries.')
        if pair:
            timing = 'unknown' if start is None and end is None else 'incomplete' if start is None or end is None else 'clock_conflict' if end < start else 'interval' if end > start else 'instant'
            if observation_only and timing == 'interval':
                timing = 'incomplete'
                activity_notes.append('Positions show recorded observations only; the elapsed gap is not an execution duration.')
            if timing == 'clock_conflict':
                activity_notes.append('Recorded endpoint precedes its start; duration is not trustworthy.')
        elif kind in ('tool.start', 'tool.end', 'model.request', 'model.response', 'run.start', 'run.end'):
            timing = 'incomplete' if start is not None else 'unknown'
            start, end = (None, end) if kind in ('tool.end', 'model.response', 'run.end') else (start, None)
            activity_notes.append('No unambiguous recorded counterpart; no duration inferred.')
        else:
            timing = 'instant' if start is not None else 'unknown'
        a = {'id': 'activity:' + first['id'], 'label': _label(first, bool(pair)), 'kind': kind, 'category': category, 'lane_id': lane(first), 'start_ms': start, 'end_ms': end, 'event_ids': [m['id'] for m in members], 'timing': timing, 'notes': activity_notes}
        activities.append(a)
        for m in members:
            event_activity[m['id']] = a
    for e in histories:
        refs = [ref for ref in e.get('evidence_refs', []) if ref in event_activity and event_activity[ref]['category'] == 'tool']
        if _dict(e.get('data')).get('correlation_basis') == 'unique_recorded_tool_use_id_within_export' and len(refs) == 1:
            a = event_activity[refs[0]]
            a['event_ids'].append(e['id'])
            a['notes'].append('Linked request-history block is evidence of prior content, not another execution.')
        else:
            t = offset(e['id'])
            a = {'id': 'activity:' + e['id'], 'label': 'Recorded request history', 'kind': 'observation', 'category': 'observation', 'lane_id': lane(e), 'start_ms': t, 'end_ms': t, 'event_ids': [e['id']], 'timing': 'instant' if t is not None else 'unknown', 'notes': ['Timestamp is first observation in a request, not execution time.']}
            activities.append(a)
        event_activity[e['id']] = a
    for activity in activities:
        activity['notes'] = list(dict.fromkeys(activity['notes']))
    edges = []
    for e in events:
        links = [('dependency', d.get('event_id'), d.get('provenance', 'recorded')) for d in e.get('dependencies', []) if isinstance(d, dict)]
        links += [('evidence', ref, 'recorded') for ref in e.get('evidence_refs', [])]
        if e.get('parent_id'):
            links.append(('parent', e['parent_id'], 'recorded'))
        for kind, ref, provenance in links:
            if ref not in event_activity:
                continue
            edges.append({'id': 'edge:' + str(len(edges) + 1), 'source_activity_id': event_activity[ref]['id'], 'target_activity_id': event_activity[e['id']]['id'], 'source_event_id': ref, 'target_event_id': e['id'], 'source_ms': offset(ref), 'target_ms': offset(e['id']), 'kind': kind, 'provenance': provenance, 'label': f'{provenance} {kind}'})
    chapters = []
    for i, stage in enumerate(narrative.get('stages', [])):
        if not isinstance(stage, dict):
            continue
        refs = [ref for ref in stage.get('event_ids', []) if ref in event_activity]
        times = [offset(ref) for ref in refs if offset(ref) is not None]
        chapters.append({'id': stage.get('id', 'chapter:' + str(i + 1)), 'title': stage.get('title', 'Recorded stage'), 'summary': stage.get('summary', ''), 'basis': stage.get('basis', 'observed'), 'event_ids': refs, 'activity_ids': list(dict.fromkeys(event_activity[ref]['id'] for ref in refs)), 'start_ms': min(times, default=None), 'end_ms': max(times, default=None)})
    ordered_times = [stamps[e['id']] for e in events if stamps[e['id']] is not None]
    if any(later < earlier for earlier, later in zip(ordered_times, ordered_times[1:])):
        notes.append('Timestamps reverse in recorded event order; chronological position does not establish dependency.')
    if any(t is None for t in stamps.values()):
        notes.append('Some event timestamps are unknown; sequence order is not elapsed time.')
    return {'version': '1', 'origin_timestamp': origin.isoformat().replace('+00:00', 'Z') if origin else None, 'duration_ms': max((offset(e['id']) for e in events if offset(e['id']) is not None), default=0), 'lanes': lanes, 'activities': activities, 'edges': edges, 'chapters': chapters, 'notes': notes}
