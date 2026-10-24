"""Offline explanation exchange for portable recordings; source files stay inert."""
import copy
import json
from pathlib import Path

from analysis.explanations import build_explanation_request, validate_explanations
from core.bundle import read_json, validate_bundle
from web.recording import MAX_BYTES


def _bounded_json(source, *, limit=MAX_BYTES):
    with Path(source).open('rb') as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('input JSON exceeds size limit')
    value = read_json(raw.decode('utf-8'))
    count = 0
    def check(item, depth=0):
        nonlocal count
        count += 1
        if count > 1000000 or depth > 48:
            raise ValueError('recording structure exceeds import limits')
        if isinstance(item, str) and len(item) > 32 * 1024 * 1024:
            raise ValueError('recording text exceeds import limits')
        if isinstance(item, dict):
            for child in item.values():
                check(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                check(child, depth + 1)
    check(value)
    return value


def _destinations(inputs, outputs):
    sources = {Path(p).resolve() for p in inputs}
    destinations = [Path(p) for p in outputs]
    if len({p.resolve() for p in destinations}) != len(destinations):
        raise ValueError('output destinations must be different')
    for path in destinations:
        if path.resolve() in sources:
            raise ValueError('source and destination must be different')
        if path.exists() or path.is_symlink():
            raise FileExistsError('output already exists: ' + str(path))


def load_portable(source):
    """Validate portable envelopes and core structure without opening artifacts.

    Artifact references are preserved but not resolved or checksum-verified.
    Only their reserved manifest/event envelopes are removed in a validation
    copy, never nested evidence or the original package.
    """
    package = _bounded_json(source)
    if (not isinstance(package, dict) or set(package) != {'format', 'version', 'payload', 'attachments'}
            or package['format'] != 'traceforge-recording' or type(package['version']) is not int
            or package['version'] != 1):
        raise ValueError('unsupported portable recording envelope')
    payload = package['payload']
    if not isinstance(payload, dict) or not all(isinstance(payload.get(k), dict) for k in ('bundle', 'narrative', 'storyline', 'flow')):
        raise ValueError('portable recording requires bundle, narrative, storyline and flow objects')
    for key, owner in [('messages', payload['flow']), ('actors', payload['flow']),
                       ('events', payload['bundle']), ('activities', payload['storyline']), ('lanes', payload['storyline'])]:
        if not isinstance(owner.get(key), list) or len(owner[key]) > 5000:
            raise ValueError('portable recording collections require at most 5000 rows')
        if any(not isinstance(row, dict) for row in owner[key]):
            raise ValueError('portable recording collection rows must be objects')
    core = copy.deepcopy(payload['bundle'])
    if isinstance(core.get('manifest'), dict):
        core['manifest'].pop('artifact_refs', None)
    for event in core['events']:
        event.pop('artifact_refs', None)
    validate_bundle(core)
    if 'telemetry' in payload:
        from analysis.telemetry import validate_telemetry_attachment
        from adapters.registry import source_identity
        attachment = payload['telemetry']
        telemetry_bundle = attachment.get('bundle') if isinstance(attachment, dict) else None
        if (not isinstance(telemetry_bundle, dict)
                or source_identity(payload['bundle'])['adapter_id'] != 'artex'
                or source_identity(telemetry_bundle)['adapter_id'] != 'orcareplay'):
            raise ValueError('telemetry requires Artex primary and OrcaReplay supplemental evidence')
        events = telemetry_bundle.get('events')
        if (not isinstance(events, list) or len(events) > 5000
                or any(not isinstance(event, dict) for event in events)):
            raise ValueError('telemetry requires at most 5000 event objects')
        telemetry_core = copy.deepcopy(telemetry_bundle)
        if isinstance(telemetry_core.get('manifest'), dict):
            telemetry_core['manifest'].pop('artifact_refs', None)
        for event in telemetry_core['events']:
            event.pop('artifact_refs', None)
        validate_bundle(telemetry_core)
        validate_telemetry_attachment(payload['bundle'], attachment)
    attachments = package['attachments']
    if not isinstance(attachments, list) or len(attachments) > 20:
        raise ValueError('expected at most 20 inert attachments')
    for item in attachments:
        if (not isinstance(item, dict) or set(item) != {'name', 'language', 'text'}
                or not isinstance(item['name'], str) or not 1 <= len(item['name']) <= 180
                or item['language'] != 'python' or not isinstance(item['text'], str)
                or len(item['text'].encode('utf-8')) > 1024 * 1024):
            raise ValueError('invalid preserved Python attachment')
    return package


def refresh_flow(payload):
    """Refresh display derivations while preserving original evidence fields."""
    from web.flow import build_flow
    from web.pacing import build_smart_pacing
    from web.time_axis import build_story_axis
    from web.storyline import add_artex_relationships
    result = copy.deepcopy(payload)
    result['storyline'] = add_artex_relationships(result['bundle'], result['storyline'])
    flow = build_flow(result['bundle'], result['narrative'], result['storyline'])
    flow['story_axis'] = build_story_axis(result['storyline'], flow)
    flow['pacing'] = build_smart_pacing(flow)
    result['flow'] = flow
    if 'telemetry' in result:
        from web.telemetry import build_telemetry_views
        result = build_telemetry_views(result)
    return result


def export_explanation_request(source, destination, *, message_ids, model, include_milestones=False):
    _destinations([source], [destination])
    package = load_portable(source)
    payload = refresh_flow(package['payload'])
    request = build_explanation_request(payload['bundle'], payload['flow'], message_ids, model=model, include_milestones=include_milestones)
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(request, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return {'request': str(output.resolve()), 'targets': len(message_ids), 'execution': 'not requested'}


def import_explanation_response(source, response, destination, *, recording_output, state_dir=None):
    _destinations([source, response], [destination, recording_output])
    package = load_portable(source)
    payload = refresh_flow(package['payload'])
    document = _bounded_json(response, limit=1024 * 1024)
    from analysis.explanation_store import AnnotationStore, merge_documents
    bundle, flow = payload['bundle'], payload['flow']
    document = validate_explanations(bundle, flow, document)
    if len(document['annotations']) > 40:
        raise ValueError('import accepts at most 40 annotations per response batch')
    existing = payload.get('explanations')
    if state_dir is not None:
        store = AnnotationStore(state_dir)
        # Imported explanations seed persistence without dropping earlier batches.
        if existing:
            store.seed(bundle, flow, existing)
        payload['explanations'] = store.merge(bundle, flow, document)
    else:
        payload['explanations'] = merge_documents(bundle, flow, existing, document)
    from web.render import render_payload
    render_payload(payload, destination, recording_output=recording_output, attachments=package['attachments'])
    return {'html': str(Path(destination).resolve()), 'recording': str(Path(recording_output).resolve()),
            'annotations': len(payload['explanations']['annotations']), 'imported': len(document['annotations']), 'execution': 'not requested',
            'verification': 'structural only; annotation meaning and artifact checksums not verified'}
