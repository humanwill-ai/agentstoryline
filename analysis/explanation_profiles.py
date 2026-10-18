"""Conservative adapter-specific evidence views. Never mutate recording evidence."""
import copy
import json
import re
from dataclasses import dataclass

_SECRET_FIELD = re.compile(r'^(?:authorization|proxy.authorization|api.?key|access.?token|refresh.?token|token|password|passwd|secret|client.?secret|cookie|set.cookie|credentials)$', re.I)
# Common credential forms only; this is bounded preview hygiene, not general DLP.
_SECRET_TEXT = re.compile(r"(?i)(\b(?:bearer|basic)\s+)[^\s\"'<>;,]+|\b(?:sk|rk|pk)-[A-Za-z0-9_-]{12,}")
_ASSIGNMENT = re.compile(r"(?i)([\"']?(?:authorization|proxy.authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret|client[_-]?secret|cookie|credentials)[\"']?\s*[=:]\s*)([\"'])(.*?)(\2)|([?&](?:api[_-]?key|access[_-]?token|token|password|secret)=)[^&#\s]+|([\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret|client[_-]?secret)[\"']?\s*[=:]\s*)[^\s,;\"'&}]+")
_URL_CREDENTIALS = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/\s@]+@")


def redact(value):
    if isinstance(value, dict):
        return {k: '[REDACTED]' if _SECRET_FIELD.match(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        # Parse serialized JSON before scanning so quoted sensitive fields retain structure.
        if value.lstrip().startswith(('{', '[')):
            try:
                parsed = json.loads(value)
            except (ValueError, TypeError, RecursionError):
                parsed = None
            if isinstance(parsed, (dict, list)):
                cleaned = redact(parsed)
                if cleaned != parsed:
                    return json.dumps(cleaned, ensure_ascii=False, sort_keys=True)
        value = _URL_CREDENTIALS.sub(lambda m: m.group(1) + '[REDACTED]@', value)
        value = _ASSIGNMENT.sub(lambda m: (m.group(1) + m.group(2) + '[REDACTED]' + m.group(2)) if m.group(1) else (m.group(5) or m.group(6)) + '[REDACTED]', value)
        def replace_auth(match):
            if match.group(1):
                # Ordinary description prose is not a literal authorization value.
                # Structured credential fields are already unconditionally removed above.
                word = match.group(0)[len(match.group(1)):].rstrip('.:!?')
                if word.lower() in ('credential', 'credentials', 'token', 'authentication'):
                    return match.group(0)
            return (match.group(1) or '') + '[REDACTED]'
        return _SECRET_TEXT.sub(replace_auth, value)
    return copy.deepcopy(value)


def _dict(value):
    return value if isinstance(value, dict) else {}


def _short(value):
    value = redact(value)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    marker = '… [preview truncated]'
    return text if len(text) <= 1800 else text[:1800-len(marker)] + marker


@dataclass(frozen=True)
class ExplanationProfile:
    adapter_id: str
    version: int = 1

    @property
    def identity(self):
        return {'adapter_id': self.adapter_id, 'version': self.version}

    @property
    def instructions(self):
        return ('Artex activity fields can describe explicit agent purposes. Usage records are bookkeeping. '
                'Tool outputs and model responses describe recorded content, not proof of execution or success.'
                if self.adapter_id == 'artex' else
                'Conservative generic evidence view: gateway/model results do not establish agent intent. '
                'Do not infer a purpose from response descriptions, summaries, or returned tool content. '
                'No JEV or LiteLLM semantic adapter support is claimed.')

    def is_bookkeeping(self, event):
        data = _dict(event.get('data'))
        return self.adapter_id == 'artex' and data.get('source_table') == 'activity' and _dict(data.get('source_record')).get('kind') == 'usage'

    def is_meaningful(self, event):
        data = _dict(event.get('data'))
        return not (str(data.get('source_kind', '')).startswith('request.history.') or self.is_bookkeeping(event)
                    or event.get('kind') in ('context', 'model.request', 'parameters', 'parameter'))

    def recorded_description(self, event):
        data = _dict(event.get('data'))
        if str(data.get('source_kind', '')).startswith('request.history.'):
            return None
        if self.adapter_id != 'artex' and event.get('kind') in ('model.response', 'tool.end'):
            return None
        record = _dict(data.get('source_record'))
        paths = [('data.source_record', record), ('data.source_record.detail', _dict(record.get('detail'))), ('data', data)] if self.adapter_id == 'artex' else []
        if event.get('kind') == 'tool.start':
            paths.extend([('data.arguments', _dict(data.get('arguments'))), ('data.tool.arguments', _dict(_dict(data.get('tool')).get('arguments')))])
            if self.adapter_id == 'artex':
                for prefix, container in [('data.source_record', record), ('data.source_record.detail', _dict(record.get('detail')))]:
                    paths.extend([(prefix+'.arguments', _dict(container.get('arguments'))), (prefix+'.tool.arguments', _dict(_dict(container.get('tool')).get('arguments')))])
        for path, container in paths:
            for field in ('summary', 'objective', 'description', 'rationale'):
                value = container.get(field)
                if isinstance(value, str) and value.strip():
                    text = ' '.join(''.join(c if ord(c) >= 32 else ' ' for c in redact(value)).split())
                    return {'text': text[:277]+'…' if len(text)>280 else text, 'basis': 'recorded', 'event_ids': [event['id']], 'source_path': path+'.'+field}
        return None

    def context(self, event):
        data = _dict(event.get('data'))
        if str(data.get('source_kind', '')).startswith('request.history.'):
            return {'id': event['id'], 'omitted': 'request history is not explanation context'}
        result = {k: redact(event[k]) for k in ('id', 'sequence', 'kind', 'tool') if k in event}
        record = _dict(data.get('source_record'))
        result['recorded_fields'] = {k: _short(record[k]) for k in ('summary', 'objective', 'description', 'rationale') if k in record} if self.adapter_id == 'artex' else {}
        if self.adapter_id == 'artex' and isinstance(record.get('detail'), (str, dict, list)):
            result['recorded_detail'] = _short(record['detail'])
        for key in ('arguments', 'result', 'response'):
            if key in data:
                result[key] = _short(data[key])
        return result


def profile_for(bundle):
    from adapters.registry import source_identity
    identity = source_identity(bundle)
    return ExplanationProfile('artex' if identity['adapter_id'] == 'artex' else 'generic')
