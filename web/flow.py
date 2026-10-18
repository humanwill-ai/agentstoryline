"""Observed chat presentation; playback schedules never assert execution times."""
import copy
import hashlib
import json
import re
from urllib.parse import urlsplit

from web.storyline import _dict, _time
from analysis.explanation_profiles import profile_for
from adapters.registry import source_identity


def _text(value):
    if isinstance(value, str):
        return value
    if value is None:
        return ''
    if isinstance(value, dict):
        if isinstance(value.get('text'), str) and (value['text'] or 'content' not in value):
            return value['text']
        if 'content' in value:
            return _text(value['content'])
    if isinstance(value, list):
        return '\n'.join(_text(item) for item in value)
    return json.dumps(value, ensure_ascii=False, indent=2)



_OBSERVATION_PREVIEW_FIELDS = ('name', 'description', 'status', 'content', 'summary', 'detail', 'text')


def _observation_preview(data):
    record = _dict(data.get('source_record'))
    if data.get('source_kind') == 'archive.transcript':
        label = 'Transcript boundary' if record.get('type') == 'boundary' else 'Transcript history'
        message = _dict(record.get('message'))
        role = message.get('role')
        content = _text(message.get('content'))
        return label + (' · ' + str(role) if role else '') + (': ' + content[:400] + ('… [preview truncated]' if len(content) > 400 else '') if content else '; inspect the original transcript row.')
    fields = []
    for field in _OBSERVATION_PREVIEW_FIELDS:
        value = record.get(field)
        if value is None or value == '':
            continue
        text = _text(value)
        fields.append(field + ': ' + (text[:400] + '… [preview truncated]' if len(text) > 400 else text))
    if fields:
        return '\n'.join(fields)
    value = data.get('content')
    if value is not None and value != '':
        text = _text(value)
        return text[:400] + '… [preview truncated]' if len(text) > 400 else text
    return 'Recorded ' + str(data.get('source_table') or data.get('source_kind') or 'source') + ' record; inspect original evidence.'


def _compact_hidden_reason(event, message, adapter_id):
    """Classify display-only clutter; Full chat and source evidence retain it.

    Explicit exchanges and milestones take priority over a table's metadata role.
    Unknown observations with readable previews remain visible rather than
    assuming that an unfamiliar source schema is bookkeeping.
    """
    kind = message['kind']
    if message.get('semantic_tags') or kind not in ('observation', 'context'):
        return None
    if kind == 'context':
        return 'recorded_context_not_an_additional_exchange'
    if message.get('bookkeeping'):
        return 'usage_bookkeeping'
    data = _dict(event.get('data'))
    table = data.get('source_table')
    if adapter_id == 'artex':
        if table in ('activity', 'findings', 'task_constraints'):
            return None
        if table in ('assets', 'task_asset_links', 'task_llm_profiles', 'task_scope',
                     'llm_usage', 'tool_usage', 'exploration_edges', 'exploration_anchors',
                     'finding_traffic_bindings', 'traffic_evidence_snapshots', 'side_question_sessions'):
            return 'source_metadata_not_an_additional_exchange'
    if data.get('source_kind') == 'archive.snapshot':
        return 'source_inventory'
    record = _dict(data.get('source_record'))
    if not message.get('description') and not any(
            value is not None and value != ''
            for value in [*(record.get(field) for field in _OBSERVATION_PREVIEW_FIELDS), data.get('content')]):
        return 'source_record_without_readable_preview'
    return None


def _response_preview(response):
    if not isinstance(response, dict):
        return _text(response)
    content = response.get('content')
    if not isinstance(content, list):
        return _text(response)
    blocks = []
    for block in content:
        if isinstance(block, dict) and block.get('type') == 'tool_use':
            blocks.append('Proposed tool: ' + str(block.get('name') or '(name not recorded)') + '\n' + _text(block.get('input')))
        else:
            blocks.append(_text(block))
    return '\n'.join(blocks) or _text(response)


def _http_observation(result):
    """Read structured, STATUS/BODY, or native status-plus-JSON exchanges."""
    text = _text(result)
    try:
        exchange = json.loads(text)
    except (ValueError, TypeError):
        exchange = None
    if isinstance(exchange, dict) and isinstance(exchange.get('response'), dict):
        response = exchange['response']
        status, body = response.get('status'), response.get('body')
    else:
        status_match = re.search(r'^STATUS (\d{3})\s*$', text, re.M)
        body_match = re.search(r'^BODY (.+)$', text, re.M)
        if not status_match or not body_match:
            native = re.fullmatch(r'status[ \t]+(\d{3})[ \t]*\r?\n(.+)', text, re.I | re.S)
            if not native:
                return None, {}
            try:
                body = json.loads(native[2])
            except ValueError:
                return None, {}
            return (int(native[1]), body) if isinstance(body, dict) else (None, {})
        status, body = int(status_match[1]), body_match[1]
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            body = None
    return status, _dict(body)


def _request_target(arguments):
    arguments = _dict(arguments)
    if isinstance(arguments.get('url'), str):
        return arguments['url']
    match = re.search(r"https?://[^\s'\"<>]+", str(arguments.get('command') or ''))
    return match[0] if match else None


def _semantic_tags(event, disclosed_tokens, denied_baselines, calls, adapter_id):
    """Conservative presentation classifications, scoped to individual events."""
    data, tags = _dict(event.get('data')), []
    kind = event.get('kind')
    if kind not in ('tool.start', 'tool.end') or str(data.get('source_kind', '')).startswith('request.history.'):
        return tags
    def tag(code, label, basis, refs=None):
        tags.append({'kind': code, 'label': label, 'basis': basis,
                     'event_ids': refs or [event['id']], 'scope': 'individual_observation'})
    phase = data.get('phase') or event.get('phase')
    if kind == 'tool.start' and phase in ('exploitation', 'exploit'):
        tag('danger', 'Exploitation attempt', 'recorded_phase')
    if kind == 'tool.start':
        raw_arguments = _dict(data.get('arguments'))
        arguments = _text(raw_arguments.get('command')) if 'command' in raw_arguments else _text(raw_arguments.get('headers'))
        for token, source_id in disclosed_tokens.items():
            target = _request_target(raw_arguments)
            if target in denied_baselines and re.search(r'Authorization', arguments, re.I) and 'Bearer ' + token in arguments:
                if not tags:
                    tag('danger', 'Exploitation attempt', 'captured_token_reused_after_denied_baseline', [denied_baselines[target], source_id, event['id']])
                break
    if kind == 'tool.end':
        result = data.get('result')
        if _dict(result).get('is_error') or _dict(data.get('source_record')).get('is_error'):
            return tags
        status, body = _http_observation(result)
        if status == 401:
            call = calls.get(event.get('call_id'))
            target = _request_target(_dict(call.get('data')).get('arguments')) if call else None
            if target:
                denied_baselines[target] = event['id']
        if status == 200 and adapter_id == 'artex' and all(isinstance(body.get(key), str) and body[key] for key in ('username', 'password', 'login_path')):
            tag('success', 'Credentials obtained', 'http_200_with_credentials_and_login_path')
        token_fields = ('token', 'bearer_token') if adapter_id == 'artex' else ('token',)
        tokens = [key for key in token_fields if isinstance(body.get(key), str) and body[key]]
        if status == 200 and tokens:
            for key in tokens:
                disclosed_tokens[body[key]] = event['id']
            tag('success', 'Token obtained', 'http_200_with_' + tokens[0] + '_body')
        elif status == 200 and all(isinstance(body.get(key), str) and body[key] for key in ('doc_id', 'content', 'proof_marker')):
            tag('success', 'Document retrieved', 'http_200_with_document_and_proof_marker')
        elif status is None and data.get('outcome') in ('success', 'succeeded'):
            tag('success', 'Successful attempt', 'recorded_attempt_outcome')
    return tags


def _logical_activity_groups(storyline, assignments, entities, events, adapter_id):
    """Cluster display entries only; source activities remain authoritative.

    Temporal proximity attaches Artex usage bookkeeping as presentation context,
    never as evidence of causality or another execution. An overlap span is a
    display envelope, not a newly established execution duration.
    """
    by_event = {event['id']: event for event in events}
    primary = {item['id']: item for item in entities if item['primary_lane']}
    by_entity, usage, omitted = {}, [], []

    def known(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def record(activity):
        event = next((by_event[eid] for eid in activity.get('event_ids', []) if eid in by_event), {})
        data = _dict(event.get('data'))
        return data, _dict(data.get('source_record'))

    def label(activity, owner, data, row):
        if activity.get('category') != 'observation' or data.get('source_table') != 'activity':
            return activity.get('label') or activity.get('kind') or 'Recorded activity'
        kind = str(row.get('kind') or '').lower()
        summary = row.get('summary')
        if owner['kind'] == 'coordinator':
            if kind == 'round':
                return summary.strip().split('\n', 1)[0][:180] if isinstance(summary, str) and summary.strip() else 'Planning'
            if kind == 'result':
                return 'Reviewing evidence'
            if kind == 'text':
                return 'Reviewing task requirements'
        if owner['kind'] == 'worker' and kind == 'result':
            return 'Reporting results'
        return summary.strip().split('\n', 1)[0][:180] if isinstance(summary, str) and summary.strip() else 'Recorded activity'

    for order, activity in enumerate(storyline.get('activities', [])):
        eid = _dict(assignments.get(activity['id'])).get('entity_id')
        data, row = record(activity)
        if (adapter_id == 'artex' and activity.get('category') == 'observation'
                and data.get('source_table') == 'activity' and row.get('kind') == 'usage'):
            if eid in primary:
                usage.append((activity, eid))
            else:
                omitted.append(activity['id'])
            continue
        if eid not in primary:
            continue
        start, end = activity.get('start_ms'), activity.get('end_ms')
        anchor = start if known(start) else end if known(end) else None
        interval = activity.get('timing') == 'interval' and known(start) and known(end) and end > start
        by_entity.setdefault(eid, []).append({
            'activity': activity, 'label': label(activity, primary[eid], data, row),
            'anchor': anchor, 'extent': end if interval else anchor,
            'order': order, 'context': [],
        })

    for activity, eid in usage:
        observed = activity.get('start_ms') if known(activity.get('start_ms')) else activity.get('end_ms')
        candidates = []
        if known(observed):
            for item in by_entity.get(eid, []):
                source = item['activity']
                stamps = [value for value in (source.get('start_ms'), source.get('end_ms')) if known(value)]
                latest = max(stamps) if stamps else None
                if latest is not None and 0 <= observed - latest <= 2000:
                    candidates.append((latest, item['order'], item))
        if candidates:
            max(candidates, key=lambda item: item[:2])[2]['context'].append(activity)
        else:
            omitted.append(activity['id'])

    groups = []
    for eid, entries in by_entity.items():
        entries.sort(key=lambda item: (item['anchor'] is None, item['anchor'] or 0, item['order']))
        clusters = []
        for item in entries:
            if (clusters and item['anchor'] is not None and clusters[-1]['end'] is not None
                    and clusters[-1]['start'] <= item['anchor'] < clusters[-1]['end']):
                clusters[-1]['items'].append(item)
                clusters[-1]['end'] = max(clusters[-1]['end'], item['extent'])
            else:
                clusters.append({'items': [item], 'start': item['anchor'], 'end': item['extent']})
        for cluster in clusters:
            items = cluster['items']
            meaningful = [item['activity'] for item in items]
            members = [activity for item in items for activity in [item['activity'], *item['context']]]
            first = meaningful[0]
            labels = list(dict.fromkeys(item['label'] for item in items))
            count = len(meaningful)
            overlap = count > 1
            categories = set(activity.get('category', 'observation') for activity in meaningful)
            bases = ['presentation_only_source_activity']
            if overlap:
                bases.append('recorded_timestamp_overlap_display_envelope_not_execution_or_causality')
            if len(members) > count:
                bases.append('artex_usage_within_2000ms_of_preceding_same_entity_observation_not_causality')
            groups.append({
                'id': 'presentation:' + first['id'], 'entity_id': eid, 'lane_id': eid,
                'source_activity_ids': [activity['id'] for activity in members],
                'meaningful_activity_ids': [activity['id'] for activity in meaningful],
                'event_ids': list(dict.fromkeys(event_id for activity in members for event_id in activity.get('event_ids', []))),
                'count': count, 'source_count': len(members),
                'label': ' + '.join(labels[:2]) + (' · ' + str(count) + ' activities' if overlap else ''),
                'kind': 'presentation.group' if overlap else first.get('kind'),
                'category': next(iter(categories)) if len(categories) == 1 else 'mixed',
                'start_ms': cluster['start'] if overlap else first.get('start_ms'),
                'end_ms': cluster['end'] if overlap else first.get('end_ms'),
                'timing': 'presentation_span' if overlap else first.get('timing'),
                'provenance': 'presentation', 'grouping_basis': bases,
                'notes': ['Display grouping retains original activity timing and evidence; it establishes no causal relationship.'],
            })
    return groups, omitted


def _logical_presentation(messages, actors, events, storyline, agent_name, adapter_id):
    """Add display aliases without replacing source actors, lanes or messages."""
    by_actor = {actor['id']: actor for actor in actors}
    by_event = {event['id']: event for event in events}
    entities, indexed = [], {}

    def entity(kind, identity, label, *, actor=None, primary=True, basis='recorded_identity', **details):
        key = json.dumps([kind, identity], ensure_ascii=False, sort_keys=True)
        if key not in indexed:
            item = {'id': 'entity:' + hashlib.sha256(key.encode()).hexdigest()[:16],
                    'kind': kind, 'identity': copy.deepcopy(identity), 'display_name': label,
                    'label': label, 'display_role': kind, 'avatar_kind': {'setup': 'agent', 'records': 'record'}.get(kind, kind),
                    'accent': '#94a3b8', 'basis': basis, 'provenance': 'presentation', 'primary_lane': primary,
                    'actor_ids': [], 'source_lane_ids': [], 'event_ids': [], **details}
            indexed[key] = item
            entities.append(item)
        item = indexed[key]
        if actor:
            if actor['id'] not in item['actor_ids']:
                item['actor_ids'].append(actor['id'])
            if kind not in ('agent', 'setup') and (kind not in ('worker', 'coordinator') or actor['role'] == 'source'):
                item['avatar_kind'] = actor.get('avatar_kind', kind)
                item['accent'] = actor.get('accent', item['accent'])
        return item

    def participant(actor, message):
        identity = actor.get('participant_identity')
        if identity:
            kind = 'worker' if actor.get('associated_worker_name') else 'coordinator'
            label = actor.get('associated_worker_name') or actor.get('associated_coordinator_name')
            return entity(kind, identity, label, actor=actor)
        if actor.get('associated_agent_name') == 'Goal initializer':
            return entity('setup', actor['identity'][0] or actor['identity'], 'Goal initializer', actor=actor, primary=False)
        if message['kind'] == 'task.input':
            return entity('task', actor['identity'], 'Original task request', actor=actor, primary=False,
                          basis='recorded_task_input')
        if actor.get('display_role') == 'agent' or message['kind'].startswith('tool.'):
            return entity('agent', actor['identity'], agent_name + ' agent', actor=actor,
                          basis='recorded_source_identity_without_worker_role')
        return entity('records', actor['identity'], actor.get('display_name') or 'Recorded source',
                      actor=actor, primary=False, basis='source_records_without_participant_identity')

    def model(event):
        data = _dict(event.get('data'))
        record, declared_identity = _dict(data.get('source_record')), _dict(data.get('model_identity'))
        recorded_model = declared_identity.get('model') or record.get('model') or _dict(data.get('request')).get('model') or _dict(data.get('response')).get('model')
        recorded_provider = record.get('provider') or declared_identity.get('provider') or declared_identity.get('model_provider')
        profile = record.get('profile_name')
        declared = isinstance(profile, str) and profile.endswith('-caller-declared')
        shown = profile[:-len('-caller-declared')] if declared else str(recorded_model or 'Recorded model')
        family = shown.removeprefix('openai/')
        match = re.fullmatch(r'gpt-(\d+(?:\.\d+)?)-(sol|astra|luna)', family, re.I)
        if match:
            shown = 'GPT-' + match.group(1) + ' ' + match.group(2).title()
        provider = recorded_provider or ('OpenAI' if re.match(r'^gpt-\d', family, re.I) else None)
        model_basis = declared_identity.get('basis') or ('caller_declared_profile' if declared else 'recorded_model_not_provider_attestation' if recorded_model else 'model_identity_unavailable')
        # Repeated names identify a model grouping, never a worker or causal link.
        # Preserve recorded provider and transport separately from family display.
        identity = {'model': recorded_model, 'provider': recorded_provider,
                    'profile': profile if declared else None, 'basis': model_basis}
        if not recorded_model and not declared:
            identity['unknown_source_actor'] = next((m['actor_id'] for m in messages if event['id'] in m['event_ids']), None)
        result = entity('model', identity, shown, basis=model_basis, model=recorded_model,
                        provider=provider, recorded_provider=recorded_provider,
                        provider_basis='recorded_provider' if recorded_provider else 'model_family_display' if provider else 'unavailable',
                        display_model_basis='caller_declared_profile' if declared else 'recorded_model',
                        model_identity=copy.deepcopy(declared_identity))
        result.update(avatar_kind='openai' if str(provider).lower() == 'openai' else 'model', accent='#34d399')
        return result

    def attach(item, message):
        for field, values in [('event_ids', message['event_ids']), ('source_lane_ids', [message.get('lane_id')])]:
            for value in values:
                if value and value not in item[field]:
                    item[field].append(value)

    owners = {}
    for message in messages:
        actor = by_actor[message['actor_id']]
        event = by_event[message['event_ids'][0]]
        owner = participant(actor, message) if message['kind'] != 'model.response' or actor.get('participant_identity') or actor.get('associated_agent_name') else None
        if owner:
            attach(owner, message)
        model_entity = model(event) if message['kind'] in ('model.request', 'model.response') else None
        if model_entity:
            attach(model_entity, message)
            if actor['role'] == 'model' and actor['id'] not in model_entity['actor_ids']:
                model_entity['actor_ids'].append(actor['id'])
        shown = model_entity if message['kind'] == 'model.response' else owner
        message.update(entity_id=shown['id'] if shown else None,
                       owner_entity_id=owner['id'] if owner and owner['kind'] in ('worker', 'coordinator', 'agent', 'setup') else None,
                       model_entity_id=model_entity['id'] if model_entity else None,
                       target_entity_id=model_entity['id'] if message['kind'] == 'model.request' else None,
                       entity_basis='presentation_grouping_of_recorded_identity')
        if message['kind'] == 'model.request' and message.get('activity_id'):
            owners[message['activity_id']] = message['owner_entity_id']
    # Only an already-paired source activity can supply a missing response owner.
    for message in messages:
        if message['kind'] == 'model.response' and not message['owner_entity_id']:
            message['owner_entity_id'] = owners.get(message.get('activity_id'))

    activity_messages = {}
    for message in messages:
        if message.get('activity_id'):
            activity_messages.setdefault(message['activity_id'], []).append(message)

    def target_origin(url):
        if not isinstance(url, str) or any(ord(c) < 33 or ord(c) == 127 for c in url):
            return None
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None:
                return None
            host = parsed.hostname.lower()
            host = '[' + host + ']' if ':' in host else host
            port = parsed.port
            return parsed.scheme + '://' + host + (':' + str(port) if port and port != {'http': 80, 'https': 443}[parsed.scheme] else '')
        except ValueError:
            return None

    assignments, annotations = {}, []
    for activity in storyline.get('activities', []):
        group = activity_messages.get(activity['id'], [])
        if not group:
            continue
        first = group[0]
        model_id = next((m['model_entity_id'] for m in group if m['model_entity_id']), None)
        owner_id = next((m['owner_entity_id'] for m in group if m['owner_entity_id']), None)
        shown_id = model_id if activity.get('category') == 'model' else owner_id or first['entity_id']
        assignment = {'entity_id': shown_id, 'owner_entity_id': owner_id,
                      'target_entity_id': None, 'source_lane_id': activity.get('lane_id'),
                      'basis': 'recorded_activity_with_presentation_identity'}
        proposal_only = any(_dict(by_event[m['event_ids'][0]].get('data')).get('execution_status') == 'proposal_only_not_execution' for m in group)
        if activity.get('category') == 'tool' and not proposal_only:
            start = next((m for m in group if m['kind'] == 'tool.start'), None)
            end = next((m for m in group if m['kind'] == 'tool.end'), None)
            arguments = _dict(start.get('parameters')) if start else {}
            url, target_basis = arguments.get('url'), 'structured_recorded_request_url'
            status, _ = _http_observation(_dict(by_event[end['event_ids'][0]].get('data')).get('result')) if end else (None, {})
            http_status = isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599
            if not isinstance(url, str) and http_status:
                command_urls = set(re.findall(r"https?://[^\s'\"<>]+", str(arguments.get('command') or '')))
                if len(command_urls) == 1:
                    url = next(iter(command_urls))
                    target_basis = 'recorded_command_url_with_paired_http_observation'
                elif not command_urls and end:
                    result = _dict(by_event[end['event_ids'][0]].get('data')).get('result')
                    try:
                        exchange = json.loads(_text(result))
                    except (TypeError, ValueError):
                        exchange = {}
                    url = _dict(_dict(exchange).get('request')).get('url')
                    target_basis = 'structured_recorded_http_exchange'
            origin = target_origin(url)
            if origin:
                target_label = 'Target · ' + urlsplit(origin).netloc
                target = entity('target', origin, target_label, basis=target_basis,
                                avatar_kind='target', accent='#fb7185', origin=origin)
                for message in group:
                    message['target_entity_id'] = target['id']
                    message['target_basis'] = target_basis
                    attach(target, message)
                assignment.update(target_entity_id=target['id'], target_basis=target_basis,
                                  target_ms=end.get('recorded_offset_ms') if end and http_status else start.get('recorded_offset_ms') if start else None)
        assignments[activity['id']] = assignment
        shown = next((e for e in entities if e['id'] == shown_id), None)
        if shown and not shown['primary_lane']:
            annotations.append({'activity_id': activity['id'], 'entity_id': shown_id,
                                'label': first['title'], 'kind': shown['kind'],
                                'event_ids': copy.deepcopy(activity.get('event_ids', []))})

    for message in messages:
        kind = message['kind']
        tool = by_event[message['event_ids'][0]].get('tool') or 'recorded tool'
        if kind == 'model.request':
            model_item = next(e for e in entities if e['id'] == message['model_entity_id'])
            label = 'Request ' + model_item['display_name']
        elif kind == 'model.response':
            label = message['text'].split('\n', 1)[0] or 'Recorded model response'
        elif kind == 'tool.start':
            proposal = _dict(by_event[message['event_ids'][0]].get('data')).get('execution_status') == 'proposal_only_not_execution'
            label = ('Proposed tool: ' if proposal else 'Use ') + str(tool)
        elif kind == 'tool.end':
            status, _ = _http_observation(_dict(by_event[message['event_ids'][0]].get('data')).get('result'))
            label = str(tool) + (' · HTTP ' + str(status) if status is not None else ' result')
        else:
            label = message['text'].split('\n', 1)[0] or message['title']
        message['compact_text'] = label[:177] + '…' if len(label) > 180 else label
        message['action_label'] = message['compact_text']
        message['compact_text_basis'] = 'presentation_summary_original_message_retained'
    rank = {'coordinator': 0, 'worker': 1, 'agent': 2, 'model': 3, 'target': 4}
    lanes = [{'id': item['id'], 'entity_id': item['id'], 'label': item['display_name'],
              'detail': item['basis'], 'kind': item['kind'], 'accent': item['accent'],
              'avatar_kind': item['avatar_kind']} for item in sorted(entities, key=lambda e: rank.get(e['kind'], 5)) if item['primary_lane']]
    activity_groups, omitted = _logical_activity_groups(storyline, assignments, entities, events, adapter_id)
    return entities, {'lanes': lanes, 'activity_lanes': {aid: value['entity_id'] for aid, value in assignments.items()},
                      'activity_entities': assignments, 'annotations': annotations,
                      'activity_groups': activity_groups, 'omitted_activity_ids': omitted,
                      'basis': 'presentation_grouping_source_storyline_retained'}


def build_flow(bundle, narrative, storyline):
    """Return explicit event messages, with request history attached as context.

    Event sequence is authoritative display order. Only storyline's evidence-
    backed correlations are reused; matching names or proximity create no links.
    Strings remain evidence text and must be rendered using textContent by clients.
    """
    bundle, narrative, storyline = _dict(bundle), _dict(narrative), _dict(storyline)
    explanation_profile = profile_for(bundle)
    adapter_id = source_identity(bundle)['adapter_id']
    manifest = _dict(bundle.get('manifest'))
    events = [e for e in bundle.get('events', []) if isinstance(e, dict) and isinstance(e.get('id'), str)]
    events = sorted(enumerate(events), key=lambda pair: (pair[1].get('sequence') if isinstance(pair[1].get('sequence'), (int, float)) else pair[0], pair[0]))
    events = [e for _, e in events]
    activities = {eid: activity for activity in storyline.get('activities', []) for eid in activity.get('event_ids', [])}
    actors, actor_ids = [], {}
    actor_records = {}
    messages, histories = [], []
    disclosed_tokens = {}
    denied_baselines = {}
    calls = {}
    source = _dict(manifest.get('source'))
    agent_name = str(_dict(manifest.get('agent')).get('name') or 'Recorded source')

    def task_input(data):
        """Return only source fields whose recorded schema identifies task input."""
        table = data.get('source_table')
        record = _dict(data.get('source_record'))
        fields = ('goal',) if table in ('tasks', 'task') else (
            ('description', 'goal') if adapter_id == 'artex' and table in ('explorations', 'exploration') else ()
        )
        for field in fields:
            value = record.get(field)
            if isinstance(value, str) and value.strip():
                return value
        return None
    # Artex sessions identify intents, whereas work#N identifies a scheduler
    # slot. Only explicit activity/session records may connect the two.
    artex_sessions = {}
    if adapter_id == 'artex':
        for event in events:
            data = _dict(event.get('data'))
            record, meta = _dict(data.get('source_record')), _dict(data.get('recording_metadata'))
            worker, session = record.get('worker'), meta.get('session_id')
            if data.get('source_table') == 'activity' and isinstance(worker, str) and re.fullmatch(r'work#\d+', worker) and session:
                scope = record.get('exploration_id')
                if scope is not None:
                    artex_sessions.setdefault(str(session), set()).add('artex:exp' + str(scope) + ':' + worker)

    def actor(event, role):
        data = _dict(event.get('data'))
        record, meta = _dict(data.get('source_record')), _dict(data.get('recording_metadata'))
        identity = [meta.get('session_id') or data.get('session_id') or record.get('session_id') or source.get('session_id'),
                    data.get('agent_id') or meta.get('agent_id') or record.get('agent_id') or record.get('agent'), record.get('worker')]
        model = _dict(data.get('model_identity')).get('model') or record.get('model') or _dict(data.get('request')).get('model') or _dict(data.get('response')).get('model')
        model = model if role == 'model' else None
        tool = event.get('tool') if role == 'tool' else None
        provider = record.get('provider') or _dict(data.get('model_identity')).get('provider') or _dict(data.get('model_identity')).get('model_provider')
        key_parts = [identity, role, model, tool]
        if role == 'model' and provider:
            key_parts.append(provider)
        key = json.dumps(key_parts, ensure_ascii=False, sort_keys=True)
        if key not in actor_ids:
            aid = 'actor:' + hashlib.sha256(key.encode()).hexdigest()[:16]
            actor_ids[key] = aid
            source_label = record.get('agent') or record.get('worker') or agent_name
            label = 'Recorded model' if role == 'model' else 'Recorded task' if role == 'task' else source_label
            actors.append({'id': aid, 'name': str(tool or label), 'role': role,
                           'model': model, 'identity': copy.deepcopy(identity), 'provenance': 'observed',
                           'model_identity_basis': (copy.deepcopy(data.get('model_identity')) or {'basis': 'recorded_profile_not_provider_attestation'}) if role == 'model' else None})
        actor_records.setdefault(actor_ids[key], []).append((record, meta, data))
        return actor_ids[key]

    for event in events:
        data = _dict(event.get('data'))
        if event.get('kind') == 'tool.start' and event.get('call_id'):
            calls[event['call_id']] = event
        kind = event.get('kind', 'observation')
        activity = activities.get(event['id'], {})
        history = str(data.get('source_kind', '')).startswith('request.history.')
        role = 'model' if kind == 'model.response' else 'tool' if kind == 'tool.end' else 'source'
        parameters = None
        title = {'model.request': 'Model request', 'model.response': 'Model response', 'tool.start': 'Tool call', 'tool.end': 'Tool result'}.get(kind, 'Recorded observation')
        if kind == 'model.request':
            request = _dict(data.get('request'))
            prior = request.get('messages')
            tools = request.get('tools') if isinstance(request.get('tools'), list) else request.get('tool_definitions')
            prior_label = str(len(prior)) + ' prior messages' if isinstance(prior, list) else 'message count unavailable'
            tool_label = str(len(tools)) + ' tools' if isinstance(tools, list) else 'tool count unavailable'
            text = 'Recorded request · ' + prior_label + ' · ' + tool_label
        elif kind == 'model.response':
            text = _response_preview(data.get('response'))
        elif kind == 'tool.start':
            parameters = copy.deepcopy(data.get('arguments'))
            proposal = data.get('execution_status') == 'proposal_only_not_execution'
            title = 'Tool proposal' if proposal else title
            text = ('Proposed tool (execution not established): ' if proposal else 'Invocation requested: ') + str(event.get('tool') or '(tool name not recorded)')
        elif kind == 'tool.end':
            text = _text(data.get('result'))
        elif adapter_id == 'orcareplay':
            source_kind = str(data.get('source_kind') or kind)
            title = {'run.start': 'Run start', 'run.end': 'Run end',
                     'fs.snapshot': 'Filesystem snapshot metadata', 'fs.change': 'Filesystem change'}.get(source_kind, 'Source event: ' + source_kind)
            content = data.get('content')
            if isinstance(content, dict):
                preview = next((content[field] for field in ('label', 'name', 'description', 'summary', 'text', 'status')
                                if isinstance(content.get(field), str) and content[field].strip()), '')
            else:
                preview = _text(content)
            text = title + (': ' + preview if preview else ' · inspect original evidence.')
        elif task_input(data) is not None:
            kind, title, role = 'task.input', 'Original task request', 'task'
            text = task_input(data)
        else:
            title = str(data.get('source_table') or 'Source') + ' record'
            text = _observation_preview(data)
        if data.get('source_kind') == 'archive.transcript':
            kind = 'context'
            title = 'Transcript boundary (context)' if _dict(data.get('source_record')).get('type') == 'boundary' else 'Transcript history (context)'
        if history:
            kind, title = 'context', 'Recorded request history (context)'
            text = _text(data.get('block', data))
            parameters = copy.deepcopy(data.get('block'))
        if event.get('tool'):
            title += ': ' + str(event['tool'])
        message = {'id': 'message:' + event['id'], 'event_ids': [event['id']],
                   'activity_id': activity.get('id'), 'lane_id': activity.get('lane_id'),
                   'actor_id': actor(event, role), 'kind': kind, 'title': title, 'text': text,
                   'parameters': parameters, 'parameters_text': json.dumps(parameters, ensure_ascii=False, indent=2) if parameters is not None else '', 'timestamp': event.get('timestamp'), 'offset_ms': None,
                   'timing_basis': data.get('timestamp_basis') or ('recorded_timestamp' if _time(event.get('timestamp')) else 'unknown'),
                   'provenance': 'observed', 'text_basis': 'presentation_summary' if kind in ('model.request', 'model.response', 'observation', 'tool.start') else 'recorded_content', 'context': [], 'call_id': event.get('call_id'),
                   'dependencies': copy.deepcopy(event.get('dependencies', [])),
                   'parent_id': event.get('parent_id'), 'evidence_refs': copy.deepcopy(event.get('evidence_refs', [])),
                   'semantic_tags': _semantic_tags(event, disclosed_tokens, denied_baselines, calls, adapter_id)}
        if isinstance(data.get('execution_status'), str):
            message['execution_status'] = data['execution_status']
        description = explanation_profile.recorded_description(event)
        if description:
            message['description'] = description
        if (not history and explanation_profile.is_bookkeeping(event)):
            message['bookkeeping'] = True
        hidden_reason = _compact_hidden_reason(event, message, adapter_id)
        if hidden_reason:
            message.update(compact_hidden=True, compact_hidden_basis=hidden_reason)
        if kind == 'model.request':
            request = _dict(data.get('request'))
            for field in ('system', 'messages'):
                if field in request:
                    message['context'].append({'kind': 'request.' + field, 'title': 'Recorded request ' + field + ' (context)',
                                               'text': _text(request[field]), 'parameters': copy.deepcopy(request[field]),
                                               'parameters_text': json.dumps(request[field], ensure_ascii=False, indent=2),
                                               'event_ids': [event['id']], 'provenance': 'observed'})
        (histories if history else messages).append(message)

    for history in histories:
        # A history block can cite both endpoints; choose only the canonical first
        # message in its already-correlated activity, never a name-based match.
        targets = [m for m in messages if m['activity_id'] and m['activity_id'] == history['activity_id']]
        if not targets:
            targets = [m for m in messages if m['event_ids'][0] == history['parent_id'] and m['kind'] == 'model.request']
        if targets:
            targets[0]['context'].append(history)
        else:
            messages.append(history)
    # Attached request history is context, not another visible participant.
    displayed = {message['actor_id'] for message in messages}
    actors = [item for item in actors if item['id'] in displayed]
    palette = ['#60a5fa', '#a78bfa', '#fbbf24', '#34d399', '#fb7185', '#22d3ee']
    worker_groups, coordinator_groups = {}, {}

    role_labels = {'worker', 'planner', 'coordinator', 'model', 'system', 'tool', 'reporter', 'goals', 'main'}

    def participant_identity(item):
        session, agent, worker = item['identity']
        if adapter_id == 'artex':
            bindings = set()
            for record, meta, data in actor_records[item['id']]:
                slot = record.get('worker')
                scope = record.get('exploration_id')
                session_scope = re.fullmatch(r'exp(\d+)-(?:planner|goals)', str(session or ''))
                if slot == 'planner' and (scope is not None or session_scope):
                    return 'artex:exp' + str(scope if scope is not None else session_scope[1]) + ':planner'
                if isinstance(slot, str) and re.fullmatch(r'work#\d+', slot) and scope is not None:
                    bindings.add('artex:exp' + str(scope) + ':' + slot)
                elif session:
                    bindings.update(artex_sessions.get(str(session), set()))
            if len(bindings) == 1:
                return next(iter(bindings))
        parts = [value for value in (session, agent, worker) if value and str(value).lower() not in role_labels]
        return str(parts[0]) if len(parts) == 1 else json.dumps(parts, ensure_ascii=False) if parts else None

    def participant(item):
        if item['role'] == 'task':
            return None, None
        session, agent, worker = item['identity']
        records = actor_records[item['id']]
        if adapter_id == 'artex' and re.fullmatch(r'exp\d+-goals', str(session or '')):
            return 'goals', str(session)
        roles = {str(r.get('role') or r.get('worker') or d.get('role') or m.get('role') or '').lower()
                 for r, m, d in records}
        explicit_worker = any(r.get('worker') and str(r['worker']).lower() not in role_labels for r, _, _ in records)
        identity = participant_identity(item)
        if roles & {'planner', 'coordinator'}:
            return 'coordinator', identity
        if explicit_worker or 'worker' in roles:
            return 'worker', identity
        return None, None

    for item in actors:
        category, identity = participant(item)
        if identity and category in ('worker', 'coordinator'):
            groups = worker_groups if category == 'worker' else coordinator_groups
            groups.setdefault(str(identity), len(groups) + 1)
    workers, coordinators = {}, {}
    for item in actors:
        category, identity = participant(item)
        # A model/tool record may omit the role while retaining an explicitly
        # recorded session identity already anchored by a worker record.
        if category is None and item['role'] != 'task':
            candidate = participant_identity(item)
            if candidate and str(candidate) in worker_groups:
                category, identity = 'worker', candidate
            elif candidate and str(candidate) in coordinator_groups:
                category, identity = 'coordinator', candidate
        number = (worker_groups if category == 'worker' else coordinator_groups).get(str(identity)) if category else None
        name = ('Worker ' if category == 'worker' else 'Coordinator ') + str(number) if number else None
        if number and category == 'coordinator' and len(coordinator_groups) == 1:
            name = 'Coordinator'
        item.update(display_name=item['name'], display_role=item['role'], avatar_kind=item['role'], accent='#94a3b8')
        if item['role'] == 'source':
            item.update(display_name=name or (category.title() + ' record (identity unavailable)' if category else agent_name + ' records'),
                        display_role=category if name else 'record', avatar_kind=category if name else 'record',
                        accent=palette[(number - 1) % len(palette)] if number else '#94a3b8')
            if category is None and any(message['actor_id'] == item['id'] and message['kind'] in ('model.request', 'tool.start') for message in messages):
                item.update(display_name=agent_name + ' agent', display_role='agent', avatar_kind='agent')
        elif item['role'] == 'task':
            item.update(display_name='Original task request', display_role='task input', avatar_kind='task')
        if name:
            item['associated_worker_name' if category == 'worker' else 'associated_coordinator_name'] = name
            item['participant_identity'] = str(identity)
            collection = workers if category == 'worker' else coordinators
            # Count identities represented by visible messages, irrespective of
            # source/model/tool presentation categories.
            collection.setdefault(str(identity), {'id': 'participant:' + hashlib.sha256((category + ':' + str(identity)).encode()).hexdigest()[:16],
                'display_name': name, 'display_role': category, 'avatar_kind': category,
                'accent': palette[(number - 1) % len(palette)], 'identity': str(identity)})
        if category == 'goals':
            item['associated_agent_name'] = 'Goal initializer'
            if item['role'] == 'source':
                item.update(display_name='Goal initializer', display_role='goals', avatar_kind='agent', accent='#fbbf24')
        if item['role'] == 'model':
            records = actor_records[item['id']]
            profile = next((r.get('profile_name') for r, _, _ in records if r.get('profile_name')), None)
            declared = isinstance(profile, str) and profile.endswith('-caller-declared')
            display_model = profile[:-len('-caller-declared')] if declared else item.get('model')
            display_model = str(display_model or 'Recorded model')
            model_family = display_model.removeprefix('openai/')
            match = re.fullmatch(r'gpt-(\d+(?:\.\d+)?)-(sol|astra|luna)', model_family, re.I)
            if match:
                display_model = 'GPT-' + match.group(1) + ' ' + match.group(2).title()
            provider = next((r.get('provider') or _dict(d.get('model_identity')).get('provider') or _dict(d.get('model_identity')).get('model_provider') for r, _, d in records
                             if r.get('provider') or _dict(d.get('model_identity')).get('provider') or _dict(d.get('model_identity')).get('model_provider')), None)
            if not provider and re.match(r'^gpt-\d', model_family, re.I):
                provider = 'OpenAI'
            item.update(display_name=display_model, display_model=display_model,
                        display_model_basis='caller_declared_profile' if declared else 'recorded_model',
                        provider=provider, avatar_kind='openai' if str(provider).lower() == 'openai' else 'model', accent='#34d399')
    participants = {'workers': list(workers.values()), 'coordinators': list(coordinators.values()),
                    'worker_count': len(workers), 'coordinator_count': len(coordinators),
                    'notes': ['Counts describe distinct identities in displayed records; this capture may be partial.']}
    if not coordinators:
        participants['notes'].append('No distinct coordinator or planner identity was recorded in displayed messages; this does not establish that none existed.')
    order = {e['id']: i for i, e in enumerate(events)}
    messages.sort(key=lambda m: order[m['event_ids'][0]])
    times = [_time(m['timestamp']) for m in messages]
    known = [t for t in times if t is not None]
    origin = _time(storyline.get('origin_timestamp')) or min(known, default=None)
    recorded = bool(messages) and all(t is not None for t in times) and all(b >= a for a, b in zip(times, times[1:]))
    notes = list(storyline.get('notes', []))
    if any(message.get('compact_hidden') for message in messages):
        notes.append('Compact chat omits recorded context and source bookkeeping. Full / Original chat, source storyline and original evidence retain those records; Smart pacing reads only the displayed messages.')
    notes.append('Tool call/result records alone do not prove approval, execution, or success. Recorded times may describe observation or persistence; inspect each timing basis.')
    if not recorded:
        notes.append('Playback uses an evenly spaced review sequence, not recorded elapsed time; original timestamps and offsets remain available.')
    for index, (message, timestamp) in enumerate(zip(messages, times)):
        observed = (timestamp - origin).total_seconds() * 1000 if timestamp and origin else None
        message['recorded_offset_ms'] = observed
        message['offset_ms'] = observed if recorded else index * 1000
        if not recorded:
            message['playback_timing_basis'] = 'review_sequence_not_elapsed_time'
        else:
            message['playback_timing_basis'] = 'recorded_timestamp'
    entities, logical_storyline = _logical_presentation(messages, actors, events, storyline, agent_name, adapter_id)
    return {'version': '1', 'title': 'Artex recorded playback' if adapter_id == 'artex' else agent_name + ' recorded flow', 'messages': messages, 'actors': actors, 'participants': participants,
            'entities': entities, 'logical_storyline': logical_storyline,
            'duration_ms': max((m['offset_ms'] for m in messages), default=0),
            'time_mode': 'recorded' if recorded else 'review_sequence', 'notes': list(dict.fromkeys(notes)),
            'origin_timestamp': storyline.get('origin_timestamp'),
            'chapters': copy.deepcopy(storyline.get('chapters', []))}
