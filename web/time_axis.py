"""Evidence-preserving display geometry for recorded storyline time."""

import math


MIN_IDLE_MS = 30_000
COMPACT_IDLE_MS = 5_000


def _number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _unique(values):
    if isinstance(values, (str, bytes, dict)):
        return []
    try:
        return list(dict.fromkeys(value for value in values if isinstance(value, str)))
    except TypeError:
        return []


def _activity_coverage(storyline, flow):
    """Return merged occupied spans with exact endpoint evidence references."""
    units = []
    events_by_activity = {}

    def unit(start, end, activity_ids=(), event_ids=()):
        activity_ids, event_ids = _unique(activity_ids), _unique(event_ids)
        units.append({'start': max(0, start), 'end': max(0, end),
                      'start_ids': activity_ids.copy(), 'end_ids': activity_ids.copy(),
                      'start_event_ids': event_ids.copy(), 'end_event_ids': event_ids.copy()})

    def point(value, activity_ids=(), event_ids=()):
        if _number(value):
            unit(value, value, activity_ids, event_ids)

    activities = storyline.get('activities', [])
    for activity in activities if isinstance(activities, list) else []:
        if not isinstance(activity, dict) or not isinstance(activity.get('id'), str):
            continue
        activity_id = activity['id']
        events_by_activity[activity_id] = _unique(activity.get('event_ids', []))
        start, end = activity.get('start_ms'), activity.get('end_ms')
        if (activity.get('timing') == 'interval' and _number(start) and _number(end)
                and end >= start):
            unit(start, end, [activity_id], events_by_activity[activity_id])
            continue
        # Incomplete and conflicting endpoints are separate observations. They
        # must not create an apparently occupied interval between the two.
        for position in _unique_numbers(start, end):
            unit(position, position, [activity_id], events_by_activity[activity_id])

    messages = flow.get('messages', [])
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        activity_id = message.get('activity_id')
        activity_ids = [activity_id] if isinstance(activity_id, str) else []
        event_ids = _unique(message.get('event_ids', []))
        event_ids = _unique(event_ids + [event_id for aid in activity_ids
                                         for event_id in events_by_activity.get(aid, [])])
        point(message.get('offset_ms'), activity_ids, event_ids)

    edges = storyline.get('edges', [])
    for edge in edges if isinstance(edges, list) else []:
        if not isinstance(edge, dict):
            continue
        for prefix in ('source', 'target'):
            activity_id, event_id = edge.get(prefix + '_activity_id'), edge.get(prefix + '_event_id')
            activity_ids = [activity_id] if isinstance(activity_id, str) else []
            event_ids = ([event_id] if isinstance(event_id, str) else [])
            event_ids += [item for aid in activity_ids for item in events_by_activity.get(aid, [])]
            point(edge.get(prefix + '_ms'), activity_ids, event_ids)

    logical = flow.get('logical_storyline')
    associations = logical.get('activity_entities', {}) if isinstance(logical, dict) else {}
    for activity_id, association in associations.items() if isinstance(associations, dict) else []:
        if not isinstance(activity_id, str) or not isinstance(association, dict):
            continue
        point(association.get('target_ms'), [activity_id],
              events_by_activity.get(activity_id, []))

    units.sort(key=lambda unit: (unit['start'], unit['end']))
    merged = []
    for unit in units:
        if not merged or unit['start'] > merged[-1]['end']:
            merged.append(unit.copy())
            continue
        current = merged[-1]
        if unit['start'] == current['start']:
            current['start_ids'] = _unique(current['start_ids'] + unit['start_ids'])
            current['start_event_ids'] = _unique(
                current['start_event_ids'] + unit['start_event_ids'])
        if unit['end'] > current['end']:
            current['end'] = unit['end']
            current['end_ids'] = unit['end_ids'].copy()
            current['end_event_ids'] = unit['end_event_ids'].copy()
        elif unit['end'] == current['end']:
            current['end_ids'] = _unique(current['end_ids'] + unit['end_ids'])
            current['end_event_ids'] = _unique(
                current['end_event_ids'] + unit['end_event_ids'])
    return merged


def _unique_numbers(*values):
    result = []
    for value in values:
        if _number(value) and value not in result:
            result.append(value)
    return result


def build_story_axis(storyline, flow, *, min_idle_ms=MIN_IDLE_MS,
                     compact_idle_ms=COMPACT_IDLE_MS):
    """Build an additive display-time map without rewriting recorded time.

    Long spaces are eligible only when no source activity occupies them. The
    returned map is presentation metadata: all recorded timestamps, offsets,
    activity bounds, dependencies, and playback pacing remain authoritative.
    """
    storyline = storyline if isinstance(storyline, dict) else {}
    flow = flow if isinstance(flow, dict) else {}
    if not _number(min_idle_ms) or min_idle_ms < 0:
        raise ValueError('story axis idle threshold must be a finite nonnegative number')
    if not _number(compact_idle_ms) or compact_idle_ms < 0:
        raise ValueError('story axis compact width must be a finite nonnegative number')
    durations = [value for value in (storyline.get('duration_ms'), flow.get('duration_ms'))
                 if _number(value) and value >= 0]
    duration = max(durations, default=0)
    base = {'version': 1, 'mode': 'recorded',
            'recorded_duration_ms': duration, 'display_duration_ms': duration,
            'threshold_ms': min_idle_ms, 'compact_idle_ms': compact_idle_ms,
            'segments': []}
    if duration <= 0:
        return base
    if flow.get('time_mode') != 'recorded':
        base['mode'] = 'review_sequence'
        base['segments'].append(_segment('recorded', 0, duration, 0, duration))
        return base

    coverage = _activity_coverage(storyline, flow)
    coverage = [unit for unit in coverage if unit['start'] <= duration and unit['end'] >= 0]
    gaps = []
    for left, right in zip(coverage, coverage[1:]):
        start, end = min(duration, left['end']), min(duration, right['start'])
        if end - start < min_idle_ms or end - start <= compact_idle_ms:
            continue
        boundary_activities = _unique(left['end_ids'] + right['start_ids'])
        boundary_events = _unique(left['end_event_ids'] + right['start_event_ids'])
        gaps.append({'start': start, 'end': end,
                     'boundary_activity_ids': boundary_activities,
                     'boundary_event_ids': boundary_events})

    if not gaps:
        base['segments'].append(_segment('recorded', 0, duration, 0, duration))
        return base

    base['mode'] = 'condensed_idle'
    recorded_cursor = display_cursor = 0
    for gap in gaps:
        if gap['start'] > recorded_cursor:
            length = gap['start'] - recorded_cursor
            base['segments'].append(_segment(
                'recorded', recorded_cursor, gap['start'], display_cursor,
                display_cursor + length))
            display_cursor += length
        recorded_span = gap['end'] - gap['start']
        break_end = display_cursor + compact_idle_ms
        item = _segment('idle_break', gap['start'], gap['end'],
                        display_cursor, break_end)
        item.update({'omitted_ms': recorded_span - compact_idle_ms,
                     'label': _duration_label(recorded_span) + ' with no recorded activity · condensed',
                     'boundary_activity_ids': gap['boundary_activity_ids'],
                     'boundary_event_ids': gap['boundary_event_ids']})
        base['segments'].append(item)
        recorded_cursor, display_cursor = gap['end'], break_end
    if recorded_cursor < duration:
        length = duration - recorded_cursor
        base['segments'].append(_segment(
            'recorded', recorded_cursor, duration, display_cursor,
            display_cursor + length))
        display_cursor += length
    base['display_duration_ms'] = display_cursor
    return base


def _segment(kind, recorded_start, recorded_end, display_start, display_end):
    return {'kind': kind, 'recorded_start_ms': recorded_start,
            'recorded_end_ms': recorded_end, 'display_start_ms': display_start,
            'display_end_ms': display_end}


def _duration_label(milliseconds):
    seconds = int(round(milliseconds / 1000))
    minutes, seconds = divmod(seconds, 60)
    if minutes:
        return f'{minutes:02d}:{seconds:02d}'
    return f'{seconds}s'
