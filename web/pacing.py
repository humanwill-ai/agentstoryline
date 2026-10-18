"""Deterministic reading schedule over the existing flow axis, not execution timing."""
import math


def build_smart_pacing(flow):
    """Add reading holds and compress empty waits without changing source evidence.

    ``recorded_*`` fields refer to flow.offset_ms, which is explicitly a review
    sequence when timestamps are unavailable. Equal offsets retain source order.
    The schedule asserts no dependencies, concurrency, or execution durations.
    """
    messages = flow.get('messages', [])
    result = {'version': 1, 'duration_ms': 0, 'entries': [], 'segments': []}
    if not messages:
        return result
    cursor = 0

    def segment(duration, start, end, kind, label):
        nonlocal cursor
        result['segments'].append({'start_ms': cursor, 'end_ms': cursor + duration,
                                   'recorded_start_ms': start, 'recorded_end_ms': end,
                                   'kind': kind, 'label': label})
        cursor += duration

    segment(400, 0, 0, 'lead', 'Preparing review')
    previous = 0
    for message in messages:
        offset = message.get('offset_ms')
        # Normalized flows already have a monotonic axis. Fail explicitly if a
        # caller bypasses that normalization rather than silently invent timing.
        if (not isinstance(offset, (int, float)) or isinstance(offset, bool)
                or not math.isfinite(offset) or offset < previous):
            raise ValueError('Smart pacing requires finite monotonic flow offsets')
        delta = offset - previous
        if delta > 0:
            wait = min(2500, max(80, round(math.sqrt(delta) * 16)))
            segment(wait, previous, offset, 'gap', 'Recorded interval' if flow.get('time_mode') == 'recorded' else 'Review sequence interval')
        # Match the collapsed card previews. Attached historical request context
        # and full evidence do not make a short visible card wait indefinitely.
        preview = message.get('text') or message.get('title') or message.get('kind') or ''
        preview_length = min(1200, len(str(preview)))
        if message.get('parameters') is not None:
            preview_length += min(420, len(str(message.get('parameters_text') or '')))
        dwell = min(4000, 1000 + preview_length * 2)
        result['entries'].append({'message_id': message['id'], 'at_ms': cursor,
                                  'recorded_ms': offset, 'dwell_ms': dwell})
        segment(dwell, offset, offset, 'hold', 'Reading message')
        previous = offset
    result['duration_ms'] = cursor
    return result
