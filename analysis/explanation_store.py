"""Fingerprint-isolated annotation revisions; source recordings remain untouched."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
import uuid

from analysis.explanation_profiles import profile_for, redact
from analysis.explanations import recording_fingerprint, validate_explanations

_LOCK = threading.RLock()


def _upgrade(bundle, document):
    result = copy.deepcopy(document)
    if result['version'] == 1:
        result.update(version=2, profile=profile_for(bundle).identity)
        for item in result['annotations']:
            item['provenance'] = dict(result['provenance'], created_at=None,
                                      adapter_id=result['profile']['adapter_id'], profile_version=0)
    return result



def merge_documents(bundle, flow, existing, incoming):
    """Validate and cumulatively merge a bounded batch without storage side effects."""
    incoming = _upgrade(bundle, validate_explanations(bundle, flow, incoming))
    if len(incoming['annotations']) > 40:
        raise ValueError('merge accepts at most 40 annotations per batch')
    prior = _upgrade(bundle, validate_explanations(bundle, flow, existing)) if existing else None
    combined = {item['message_id']: item for item in (prior['annotations'] if prior else [])}
    combined.update({item['message_id']: item for item in incoming['annotations']})
    incoming['annotations'] = list(combined.values())
    return validate_explanations(bundle, flow, incoming)


class AnnotationStore:
    def __init__(self, state_dir):
        self.state_dir = Path(state_dir)

    def _directory(self, bundle):
        return self.state_dir / 'annotations' / recording_fingerprint(bundle)

    def load(self, bundle, flow):
        with _LOCK:
            path = self._directory(bundle) / 'current.json'
            if not path.exists():
                return None
            return validate_explanations(bundle, flow, json.loads(path.read_text(encoding='utf-8')))

    def _save(self, bundle, flow, document):
        document = validate_explanations(bundle, flow, document)
        if redact(document) != document:
            raise ValueError('annotation document contains credential-like values')
        directory = self._directory(bundle)
        revisions = directory / 'revisions'
        revisions.mkdir(parents=True, exist_ok=True)
        revision = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex
        raw = json.dumps(document, ensure_ascii=False, indent=2) + '\n'
        revision_path = revisions / (revision + '.json')
        with revision_path.open('x', encoding='utf-8') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        temporary = directory / ('.current-' + uuid.uuid4().hex)
        try:
            with temporary.open('x', encoding='utf-8') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, directory / 'current.json')
        finally:
            temporary.unlink(missing_ok=True)
        return copy.deepcopy(document)

    def merge(self, bundle, flow, document):
        """Apply supplied IDs only, preserving other items and their provenance."""
        with _LOCK:
            merged = merge_documents(bundle, flow, self.load(bundle, flow), document)
            return self._save(bundle, flow, merged)

    def seed(self, bundle, flow, document):
        """Import a validated cumulative portable snapshot, retaining other saved IDs."""
        incoming = _upgrade(bundle, validate_explanations(bundle, flow, document))
        with _LOCK:
            prior = self.load(bundle, flow)
            prior = _upgrade(bundle, prior) if prior else None
            combined = {item['message_id']: item for item in (prior['annotations'] if prior else [])}
            combined.update({item['message_id']: item for item in incoming['annotations']})
            incoming['annotations'] = list(combined.values())
            return self._save(bundle, flow, incoming)

    def history(self, bundle, flow=None):
        """Return ordered revision IDs, oldest first."""
        with _LOCK:
            return sorted(path.stem for path in (self._directory(bundle) / 'revisions').glob('*.json'))

    def restore(self, bundle, flow, revision):
        """Restore an exact historical snapshot as a new revision."""
        if not isinstance(revision, str) or not re.fullmatch(r'[0-9]{8}T[0-9]{12}Z-[0-9a-f]{32}', revision):
            raise ValueError('invalid revision ID')
        with _LOCK:
            path = self._directory(bundle) / 'revisions' / (revision + '.json')
            document = validate_explanations(bundle, flow, json.loads(path.read_text(encoding='utf-8')))
            return self._save(bundle, flow, _upgrade(bundle, document))
