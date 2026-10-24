"""Disposable display projections for an Artex recording with Orca telemetry.

The primary and attached bundles remain exact source evidence. These projections
combine display objects only, never canonical dependencies or task membership.
"""
from copy import deepcopy
from datetime import datetime

from analysis.narrative import build_observed_timeline
from analysis.telemetry import validate_telemetry_attachment
from web.pacing import build_smart_pacing
from web.time_axis import build_story_axis


def _time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def _supplement_ids(attachment):
    """Only unmatched exchanges and their explicitly linked tool lifecycle.

    A matched/ambiguous model request is a boundary: it cannot acquire another
    request's tools merely through ordering. Routing remains available in the
    telemetry view and details, rather than becoming another story action.
    """
    events = attachment['bundle']['events']
    selected = set(attachment['unmatched_orca_request_ids'])
    sequence_ids = {(e.get('data', {}).get('source_session', {}).get('id'), e.get('data', {}).get('source_record', {}).get('seq')): e['id'] for e in events}
    for event in events:
        if event.get('kind') not in ('model.response', 'tool.start', 'tool.end'):
            continue
        refs = {event.get('parent_id')}
        data = event.get('data', {})
        session = data.get('source_session', {}).get('id')
        refs.update(sequence_ids.get((session, seq)) for seq in data.get('source_record', {}).get('causes', []) if session)
        refs.update(d.get('event_id') for d in event.get('dependencies', []))
        if refs & selected:
            selected.add(event['id'])
    return selected


def _prefix_display(payload):
    """Namespace presentation IDs, preserving event IDs and raw evidence."""
    flow, story = deepcopy(payload['flow']), deepcopy(payload['storyline'])
    ids = set()
    for rows in (flow.get('messages', []), flow.get('actors', []), flow.get('entities', []),
                 story.get('activities', []), story.get('lanes', []), story.get('edges', []),
                 story.get('chapters', []), flow.get('logical_storyline', {}).get('activity_groups', [])):
        ids.update(row['id'] for row in rows if isinstance(row.get('id'), str))
    mapping = {key: 'telemetry:' + key for key in ids}
    # Only rewrite declared display reference fields, not parameters/context/text.
    def rewrite(value, key=''):
        if isinstance(value, dict):
            return {mapping.get(k, k): rewrite(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [rewrite(v, key) for v in value]
        if isinstance(value, str) and (key in ('id', 'actor_ids', 'source_lane_ids', 'source_activity_ids', 'activity_ids', 'omitted_activity_ids') or (key.endswith('_id') and key not in ('parent_id','event_id','call_id','source_event_id','target_event_id','source_session_id','actor_session_id')) or key in mapping):
            return mapping.get(value, value)
        return value
    # Raw model/tool content is intentionally excluded from the rewrite.
    for message in flow['messages']:
        saved = {key: message.pop(key) for key in ('parameters', 'context', 'dependencies', 'evidence_refs', 'parent_id', 'call_id') if key in message}
        updated = rewrite(message)
        message.clear(); message.update(updated); message.update(saved)
    for key in ('actors', 'entities', 'logical_storyline', 'participants', 'chapters'):
        flow[key] = rewrite(flow.get(key, [] if key in ('actors','entities','chapters') else {}))
    story = rewrite(story)
    flow.pop('pacing', None); flow.pop('story_axis', None)
    return {'flow': flow, 'storyline': story}


def _shift(projection, origin):
    story, flow = projection['storyline'], projection['flow']
    delta = (_time(story['origin_timestamp']) - origin).total_seconds() * 1000
    for message in flow['messages']:
        for key in ('offset_ms', 'recorded_offset_ms'):
            if isinstance(message.get(key), (int, float)): message[key] += delta
    for rows, keys in ((story['activities'], ('start_ms', 'end_ms')), (story['edges'], ('source_ms','target_ms')),
                       (story['chapters'], ('start_ms','end_ms')),
                       (flow.get('logical_storyline', {}).get('activity_groups', []), ('start_ms','end_ms'))):
        for row in rows:
            for key in keys:
                if isinstance(row.get(key), (int,float)): row[key] += delta
    for assignment in flow.get('logical_storyline', {}).get('activity_entities', {}).values():
        if isinstance(assignment.get('target_ms'), (int,float)): assignment['target_ms'] += delta
    flow['chapters'] = deepcopy(story['chapters'])
    for owner in (flow, story):
        owner['origin_timestamp'] = origin.isoformat().replace('+00:00','Z')
        owner['duration_ms'] += delta


def _schedule(projection):
    flow, story = projection['flow'], projection['storyline']
    flow['duration_ms'] = max([0] + [m['offset_ms'] for m in flow['messages']])
    story['duration_ms'] = max(story['duration_ms'], flow['duration_ms'])
    # Both axes must describe the same clock, including source snapshot tails.
    flow['duration_ms'] = story['duration_ms']
    flow['story_axis'] = build_story_axis(story, flow)
    flow['pacing'] = build_smart_pacing(flow)
    return projection


def _correlation_lane_owner(flow, eid, activity):
    """Use existing recorded attribution only; a shared model is never an owner."""
    entity_rows = {}
    for entity in flow.get('entities', []):
        entity_rows.setdefault(entity['id'], []).append(entity)
    # Duplicate IDs cannot provide a unique identity, even if labels agree.
    entities = {eid: rows[0] for eid, rows in entity_rows.items() if len(rows) == 1}
    candidates = {}

    def add(owner_id, basis, evidence):
        owner = entities.get(owner_id, {})
        if (owner.get('kind') not in ('worker', 'coordinator', 'agent', 'setup')
                or owner.get('basis') not in ('recorded_identity', 'recorded_source_session_not_attested_role',
                                              'recorded_source_identity_without_worker_role')):
            return
        candidates.setdefault(owner_id, []).append({'basis': basis, **evidence})

    # Inspect the whole already-paired activity as well as the exact endpoint;
    # conflicting explicit ownership must fall back rather than choose a row.
    for message in flow.get('messages', []):
        if eid in message.get('event_ids', []) or message.get('activity_id') == activity['id']:
            if message.get('entity_basis') == 'presentation_grouping_of_recorded_identity':
                add(message.get('owner_entity_id'), 'recorded_message_owner',
                    {'message_id': message['id'], 'event_ids': deepcopy(message.get('event_ids', []))})
    assignment = flow.get('logical_storyline', {}).get('activity_entities', {}).get(activity['id'], {})
    if assignment.get('basis') == 'recorded_activity_with_presentation_identity':
        add(assignment.get('owner_entity_id'), 'recorded_activity_owner', {'activity_id': activity['id']})
    if len(candidates) != 1:
        return {'basis': 'model_lane_fallback', 'reason': 'Conflicting recorded owners' if candidates else 'No unique recorded owner'}
    owner_id, evidence = next(iter(candidates.items()))
    owner = entities[owner_id]
    return {'entity_id': owner_id, 'label': owner.get('display_name') or owner.get('label') or owner_id,
            'basis': 'unique_recorded_owner', 'owner_identity_basis': owner['basis'], 'evidence': evidence}


def _project_correlations(story, model, attachment, primary_bundle=None, flow=None):
    """Map evidence to visible anchors without asserting tool equivalence."""
    events = {e['id']: e for e in attachment['bundle']['events']}
    timed_events = dict(events)
    timed_events.update({e['id']: e for e in (primary_bundle or {}).get('events', [])})

    def event_offset(eid, activity):
        stamp = timed_events.get(eid, {}).get('timestamp')
        origin = story.get('origin_timestamp')
        if stamp and origin:
            try:
                return (_time(stamp) - _time(origin)).total_seconds() * 1000
            except (ValueError, TypeError):
                pass
        return activity['start_ms']

    activities = {}
    for activity in story['activities']:
        for eid in activity['event_ids']:
            activities.setdefault(eid, []).append(activity)
    matches = {}
    for match in attachment['matches']:
        for side in ('request', 'response'):
            source, target = match.get('orca_' + side + '_id'), match.get('artex_' + side + '_id')
            if source and target:
                matches.setdefault(source, []).append((target, match))
    sequences = {}
    for event in events.values():
        data = event.get('data', {})
        key = (data.get('source_session', {}).get('id'), data.get('source_record', {}).get('seq'))
        sequences.setdefault(key, []).append(event['id'])

    def resolve(eid, path=()):
        if eid in path:
            return None, {'reason': 'Cyclic source lifecycle references'}
        if len(activities.get(eid, [])) == 1:
            return eid, {'basis': 'retained_supplemental', 'source_event_id': eid}
        choices = matches.get(eid, [])
        if len(choices) == 1 and len(activities.get(choices[0][0], [])) == 1:
            target, match = choices[0]
            return target, {'basis': 'validated_exchange_match', 'source_event_id': eid,
                            'match': deepcopy(match)}
        event = events.get(eid, {})
        if event.get('kind') not in ('tool.start', 'tool.end'):
            return None, {'reason': 'No unique visible endpoint or validated exchange match'}
        data = event.get('data', {})
        session = data.get('source_session', {}).get('id')
        refs = {event.get('parent_id')} | {d.get('event_id') for d in event.get('dependencies', []) if d.get('provenance') == 'recorded'}
        for seq in data.get('source_record', {}).get('causes', []):
            if session:
                refs.update(sequences.get((session, seq), []))
        refs.discard(None)
        allowed = {'model.response'} if event.get('kind') == 'tool.start' else {'tool.start'}
        # Multiple source parents are ambiguous even if they converge visually.
        if len(refs) != 1 or events.get(next(iter(refs)), {}).get('kind') not in allowed:
            return None, {'reason': 'No unique explicit tool lifecycle source'}
        parent = next(iter(refs))
        target, mapping = resolve(parent, path + (eid,))
        if target is None or mapping.get('basis') == 'retained_supplemental':
            return None, {'reason': 'Tool lifecycle has no uniquely matched model exchange', 'source_mapping': mapping}
        return target, {'basis': 'explicit_tool_lifecycle_display_anchor', 'source_event_id': eid,
                        'lifecycle_event_ids': [eid] + mapping.get('lifecycle_event_ids', [parent]),
                        'exchange_mapping': mapping,
                        'limitation': 'Display anchor only; no Artex tool equivalence is asserted.'}

    original = model['storyline'].get('correlations', {})
    projection = {'version': 1, 'items': [], 'summary': {'mapped': 0, 'unresolved': 0}}
    if 'sessions' in original:
        projection['sessions'] = deepcopy(original['sessions'])
    ids = {item['id'] for item in original.get('items', [])}
    story['edges'] = [e for e in story['edges'] if e.get('correlation_id') not in ids]
    for original_item in original.get('items', []):
        item = deepcopy(original_item)
        source, sm = resolve(item['source_event_id'])
        target, tm = resolve(item['target_event_id'])
        item['display_mapping'] = {'source': sm, 'target': tm}
        if flow is not None:
            for value, mapping in ((source, sm), (target, tm)):
                if value is not None:
                    mapping['lane_owner'] = _correlation_lane_owner(flow, value, activities[value][0])
        for side, value in (('source', source), ('target', target)):
            if value is not None:
                item['display_' + side + '_event_id'] = value
        status = 'mapped' if source is not None and target is not None else 'unresolved'
        item['display_status'] = status
        if status == 'unresolved':
            item['display_mapping']['reason'] = '; '.join(side + ': ' + mapping['reason'] for side, mapping in (('source', sm), ('target', tm)) if 'reason' in mapping)
        else:
            sa, ta = activities[source][0], activities[target][0]
            story['edges'].append({'id': 'edge:telemetry-correlation:' + item['id'],
                'source_activity_id': sa['id'], 'target_activity_id': ta['id'],
                'source_event_id': item['source_event_id'], 'target_event_id': item['target_event_id'],
                'display_source_event_id': source, 'display_target_event_id': target,
                'source_ms': event_offset(source, sa), 'target_ms': event_offset(target, ta),
                'kind': 'content_correlation', 'provenance': item['provenance'],
                'label': item['label'], 'correlation_id': item['id'],
                'basis': item['matching_method'], 'limitations': deepcopy(item['limitations']),
                'display_mapping': deepcopy(item['display_mapping'])})
            for side, mapping in (('source', sm), ('target', tm)):
                owner_id = mapping.get('lane_owner', {}).get('entity_id')
                if owner_id:
                    story['edges'][-1]['display_' + side + '_owner_entity_id'] = owner_id
        projection['items'].append(item)
        projection['summary'][status] += 1
    story['telemetry_correlations'] = projection


def build_telemetry_views(payload):
    """Rebuild views from original evidence; never trust saved projections."""
    if 'telemetry' not in payload:
        return payload
    attachment = validate_telemetry_attachment(payload['bundle'], payload['telemetry'])
    from web.render import build_payload
    source = attachment['bundle']
    primary_ids = {e['id'] for e in payload['bundle']['events']}
    if primary_ids.intersection(e['id'] for e in source['events']):
        raise ValueError('primary and telemetry event IDs collide')
    model = _prefix_display(build_payload(source, build_observed_timeline(source)))
    model['telemetry_view_mode'] = 'model'
    model['flow']['title'] = 'Attached Orca model telemetry'
    _schedule(model)

    # Filter disposable display collections, leaving source events untouched.
    included = _supplement_ids(attachment)
    extra = deepcopy(model)
    flow, story = extra['flow'], extra['storyline']
    flow['messages'] = [m for m in flow['messages'] if set(m['event_ids']) & included]
    story['activities'] = [a for a in story['activities'] if set(a['event_ids']) & included]
    activity_ids = {a['id'] for a in story['activities']}
    story['edges'] = [e for e in story['edges'] if e['source_activity_id'] in activity_ids and e['target_activity_id'] in activity_ids]
    story['chapters'] = [c for c in story['chapters'] if set(c.get('event_ids', [])) & included]
    actor_ids = {m['actor_id'] for m in flow['messages']}
    flow['actors'] = [a for a in flow['actors'] if a['id'] in actor_ids]
    entity_ids = {m.get(key) for m in flow['messages'] for key in ('entity_id','owner_entity_id','model_entity_id','target_entity_id')}
    flow['entities'] = [e for e in flow['entities'] if e['id'] in entity_ids]
    logical = flow['logical_storyline']
    logical['lanes'] = [l for l in logical['lanes'] if l['entity_id'] in entity_ids]
    for key in ('activity_lanes','activity_entities'):
        logical[key] = {k:v for k,v in logical[key].items() if k in activity_ids}
    logical['annotations'] = [a for a in logical['annotations'] if a['activity_id'] in activity_ids]
    logical['activity_groups'] = [g for g in logical['activity_groups'] if set(g.get('source_activity_ids',[])) <= activity_ids]
    logical['omitted_activity_ids'] = [i for i in logical['omitted_activity_ids'] if i in activity_ids]
    used_lanes = {a['lane_id'] for a in story['activities']}
    story['lanes'] = [l for l in story['lanes'] if l['id'] in used_lanes]
    story.pop('correlations', None)
    flow['correlations'] = {}; flow['collection'] = {}
    for m in flow['messages']:
        m['telemetry_source'] = 'Orca only'
        m['telemetry_association'] = 'Supplemental recording; common task membership is not established by attachment.'
    for e in flow['entities']:
        if e.get('kind') != 'model':
            e['display_name'] += ' · Orca only'; e['label'] = e['display_name']
    for lane in logical['lanes']:
        if lane.get('kind') != 'model': lane['label'] += ' · Orca only'

    both = {'flow': deepcopy(payload['flow']), 'storyline': deepcopy(payload['storyline']), 'telemetry_view_mode':'both'}
    origin = min(_time(both['storyline']['origin_timestamp']), _time(extra['storyline']['origin_timestamp']))
    _shift(both, origin); _shift(extra, origin)
    # Stable time ordering is a review convention, never a causal edge.
    bf, bs = both['flow'], both['storyline']
    # A shared model lane is display grouping, anchored by matched exchanges.
    primary_messages = {eid:m for m in bf['messages'] for eid in m['event_ids']}
    model_messages = {eid:m for m in model['flow']['messages'] for eid in m['event_ids']}
    model_aliases = {}
    for match in attachment['matches']:
        left = primary_messages.get(match['artex_request_id'], {}).get('model_entity_id')
        right = model_messages.get(match['orca_request_id'], {}).get('model_entity_id')
        if left and right: model_aliases.setdefault(right,set()).add(left)
    aliases = {k:next(iter(v)) for k,v in model_aliases.items() if len(v)==1}
    for m in flow['messages']:
        for key in ('entity_id','model_entity_id','target_entity_id'):
            if m.get(key) in aliases: m[key] = aliases[m[key]]
    for assignment in logical['activity_entities'].values():
        for key in ('entity_id','target_entity_id'):
            if assignment.get(key) in aliases: assignment[key] = aliases[assignment[key]]
    for key,value in list(logical['activity_lanes'].items()):
        logical['activity_lanes'][key] = aliases.get(value,value)
    for group in logical['activity_groups']:
        for key in ('entity_id','lane_id'):
            if group.get(key) in aliases: group[key] = aliases[group[key]]
    logical['lanes'] = [l for l in logical['lanes'] if l['entity_id'] not in aliases]
    for entity in flow['entities']:
        if entity['id'] in aliases:
            target = next(e for e in bf['entities'] if e['id']==aliases[entity['id']])
            for key in ('actor_ids','source_lane_ids','event_ids'):
                target[key] = list(dict.fromkeys(target[key] + entity[key]))
    flow['entities'] = [e for e in flow['entities'] if e['id'] not in aliases]
    for key in ('messages','actors','entities'):
        bf[key].extend(flow[key])
    bf['messages'].sort(key=lambda m:m['offset_ms'])
    for key in ('lanes','activities','edges','chapters'):
        bs[key].extend(story[key])
    for key in ('lanes','annotations','activity_groups','omitted_activity_ids'):
        bf['logical_storyline'][key].extend(logical[key])
    for key in ('activity_lanes','activity_entities'):
        bf['logical_storyline'][key].update(logical[key])
    bs['duration_ms'] = max(bs['duration_ms'], story['duration_ms'])
    bf['chapters'] = deepcopy(bs['chapters'])
    bf['title'] = 'Artex story with Orca telemetry'
    bf['time_mode'] = 'recorded' if bf['time_mode'] == flow['time_mode'] == 'recorded' else 'review_sequence'
    if bf['time_mode'] != 'recorded':
        for index,message in enumerate(bf['messages']):
            message['offset_ms'] = index*1000
            message['playback_timing_basis'] = 'review_sequence_not_elapsed_time'
    bs['notes'].append('Combined presentation: Artex story plus unmatched Orca exchanges and explicitly linked tools. Attachment does not establish common task membership or execution.')
    matched = {m[k] for m in attachment['matches'] for k in ('artex_request_id','artex_response_id') if m.get(k)}
    for message in bf['messages']:
        if set(message['event_ids']) & matched: message['telemetry_source'] = 'Artex + matched Orca'
    _project_correlations(bs, model, attachment, payload['bundle'], bf)
    _schedule(both)
    result = dict(payload)
    result['telemetry_views'] = {'model':model, 'both':both}
    return result
