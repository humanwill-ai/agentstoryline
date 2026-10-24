"""Bounded display of explicit source-reported outcomes, never verification.

``event.data.milestones`` is an opt-in canonical extension: a list of objects
with label and outcome. Orca sources may opt in with an event whose type is
``milestone`` and attrs containing label/outcome; this is our extension, not
an assertion about the upstream Orca format. No prose, HTTP status, or tool
completion is interpreted as evidence of goal achievement.
"""

MAX_MILESTONES = 16
MAX_LABEL_LENGTH = 120
OUTCOMES = frozenset(('attempt', 'success', 'failure', 'unconfirmed'))


def source_reported_milestones(event, adapter_id=None):
    """Return fresh presentation entries citing only the containing event.

    Malformed entries (including foreign evidence references) are discarded.
    Extra source metadata never enters the presentation contract.
    """
    if not isinstance(event, dict) or not isinstance(event.get('id'), str) or not event['id']:
        return []
    data = event.get('data')
    if not isinstance(data, dict):
        return []
    entries = data.get('milestones')
    if entries is None and adapter_id == 'orcareplay' and data.get('source_kind') == 'milestone':
        record = data.get('source_record')
        attrs = record.get('attrs') if isinstance(record, dict) else None
        entries = [attrs] if isinstance(attrs, dict) else []
    if not isinstance(entries, list):
        return []
    result = []
    for entry in entries[:MAX_MILESTONES]:
        if not isinstance(entry, dict):
            continue
        label, outcome = entry.get('label'), entry.get('outcome')
        if (not isinstance(label, str) or not label.strip()
                or len(label) > MAX_LABEL_LENGTH
                or any(ord(char) < 32 or ord(char) == 127 or char in '\u0085\u2028\u2029' for char in label)
                or not isinstance(outcome, str) or outcome not in OUTCOMES):
            continue
        if 'event_ids' in entry and entry['event_ids'] != [event['id']]:
            continue
        result.append({'label': label.strip(), 'outcome': outcome,
                       'event_ids': [event['id']], 'provenance': 'source_reported',
                       'basis': 'explicit_source_outcome'})
    return result
