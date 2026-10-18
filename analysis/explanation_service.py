"""Optional loopback-only description service. Recorded material stays inert."""
import copy
import datetime
import hmac
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from analysis.explanation_workflow import load_portable, refresh_flow
from analysis.explanations import build_explanation_request, validate_explanations, recording_fingerprint, select_message_ids
from analysis.explanation_provider import generate, validate_endpoint, ProviderError
from analysis.explanation_profiles import profile_for, redact
from analysis.explanation_store import AnnotationStore
from analysis.explanation_handoff import HandoffError, export_request_set, import_response_set, offline_start, offline_status, offline_pending, offline_cancel
from web.recording import MAX_BYTES
from web.branding import embed_brand_assets
from web.theme import apply_default_flow_theme
from core.bundle import read_json
from adapters.registry import source_identity
from web.viewer_import import import_upload, MAX_UPLOAD_BODY
from web.sharing import prepare_share
from web.artifacts import ArtifactStore


class ExplanationService:
    def __init__(self, recording, state_dir, provider=generate):
        self.state_dir = Path(state_dir)
        self.store = AnnotationStore(state_dir)
        self.provider = provider
        self.lock = threading.RLock()
        self.token = secrets.token_urlsafe(32)
        self.cancelled = threading.Event()
        self.config = None
        self.pending = []
        self.status = {'state': 'idle', 'completed': 0, 'total': 0, 'failed': 0, 'error': None,
                       'provider': None, 'model': None}
        self._activate(load_portable(recording))

    def _activate(self, package, imported=False):
        candidate = copy.deepcopy(package)
        candidate['payload'] = refresh_flow(package['payload'])
        bundle, flow = candidate['payload']['bundle'], candidate['payload']['flow']
        fingerprint = recording_fingerprint(bundle)
        original = candidate['payload'].get('explanations')
        if original is not None:
            original = validate_explanations(bundle, flow, original)
        document = self.store.load(bundle, flow)
        if document is None and original is not None:
            document = self.store.seed(bundle, flow, original)
        if document is not None:
            candidate['payload']['explanations'] = document
        if imported:
            destination = self.state_dir / fingerprint / 'recording.json'
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp = destination.with_suffix('.tmp')
            temp.write_text(json.dumps(package, ensure_ascii=False, allow_nan=False), encoding='utf-8')
            temp.replace(destination)
        # Active source changes only after every validation and save succeeds.
        self.package, self.bundle, self.flow = candidate, bundle, flow
        self.recording_id = fingerprint
        self.artifacts = ArtifactStore(self.state_dir, bundle, fingerprint)
        self.share_preview = None
        self.status.update(state='idle', completed=0, total=0, failed=0, error=None,
                           provider=None, model=None)

    @staticmethod
    def _model(value):
        if (not isinstance(value, str) or not value.strip() or len(value) > 200
                or any(ord(char) < 32 for char in value) or redact(value) != value):
            raise ValueError('invalid model label')
        return value

    def _next_request(self, ids, model):
        size = min(20, len(ids))
        while size:
            try:
                chosen = ids[:size]
                return chosen, build_explanation_request(self.bundle, self.flow, chosen, model=model)
            except ValueError as exc:
                if 'exceeds 512000' not in str(exc) or size == 1:
                    raise
                size = max(1, size // 2)
        raise ValueError('no targets')

    def planned_requests(self, ids, model):
        offset = 0
        while offset < len(ids):
            chosen, request = self._next_request(ids[offset:], model)
            yield chosen, request
            offset += len(chosen)

    def preview(self, config):
        with self.lock:
            model = self._model(config.get('model') or 'preview')
            ids = select_message_ids(self.bundle, self.flow, self.package['payload'].get('explanations'),
                scope=config.get('scope', 'missing'), start_ms=config.get('start_ms'), end_ms=config.get('end_ms'))
            sizes = [len(json.dumps(request, ensure_ascii=False).encode('utf-8'))
                     for _, request in self.planned_requests(ids, model)]
            return {'targets': len(ids), 'batches': len(sizes), 'request_bytes': sum(sizes),
                    'profile': profile_for(self.bundle).identity, 'recording_id': self.recording_id}

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.status)
            result['recording_id'] = self.recording_id
            result['profile'] = profile_for(self.bundle).identity
            result['source'] = source_identity(self.bundle)
            annotated = self.package['payload'].get('explanations', {}).get('annotations', [])
            result['coverage'] = {'annotated': len(annotated), 'total': len(self.flow['messages'])}
            return result

    def start(self, config, retry=False):
        with self.lock:
            if self.status['state'] == 'running':
                raise ValueError('generation already running')
            if offline_status(self)['state'] == 'waiting':
                raise ValueError('cancel the waiting offline job before API generation')
            if retry:
                if not self.config or not self.pending:
                    raise ValueError('no failed or cancelled targets to retry')
                config = self.config
                ids = list(self.pending)
            else:
                if config.get('provider') != 'openai-compatible':
                    raise ValueError('unsupported provider')
                validate_endpoint(config.get('endpoint'))
                model = self._model(config.get('model'))
                label = config['endpoint'].rstrip('/')
                if len(label) > 200 or redact(label) != label or any(ord(char) < 32 for char in label):
                    raise ValueError('invalid gateway provenance label')
                key = config.get('api_key') or os.environ.get('TRACEFORGE_EXPLANATION_API_KEY', '')
                if not isinstance(key, str) or len(key) > 8192 or '\r' in key or '\n' in key:
                    raise ValueError('invalid API key')
                config = dict(config, api_key=key, provider_label=label)
                ids = select_message_ids(self.bundle, self.flow, self.package['payload'].get('explanations'),
                    scope=config.get('scope', 'missing'), start_ms=config.get('start_ms'), end_ms=config.get('end_ms'))
            # Validate every planned batch before the first potentially paid call,
            # retaining only one bounded request at a time.
            for _chosen, _request in self.planned_requests(ids, config['model']):
                pass
            self.config = config
            self.pending = list(ids)
            self.cancelled.clear()
            self.status.update(state='running', completed=0, total=len(ids), failed=0, error=None,
                               provider=config['provider_label'], model=config['model'])
            threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        try:
            while self.pending and not self.cancelled.is_set():
                ids, request = self._next_request(self.pending, self.config['model'])
                document = self.provider(self.config['endpoint'], self.config['model'], self.config['api_key'], request)
                if not isinstance(document, dict) or not isinstance(document.get('annotations'), list):
                    raise ValueError('invalid provider document')
                received = [item.get('message_id') for item in document['annotations'] if isinstance(item, dict)]
                if len(received) != len(ids) or set(received) != set(ids):
                    raise ValueError('provider must return exactly the selected targets')
                profile = profile_for(self.bundle)
                provenance = {'method': 'provider-api', 'model': self.config['model'], 'provider': self.config['provider_label']}
                document = copy.deepcopy(document)
                document.update(version=2, profile=profile.identity, provenance=provenance)
                for item in document['annotations']:
                    item['provenance'] = dict(provenance, created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                              adapter_id=profile.adapter_id, profile_version=profile.version)
                document = validate_explanations(self.bundle, self.flow, document)
                with self.lock:
                    merged = self.store.merge(self.bundle, self.flow, document)
                    self.package['payload']['explanations'] = merged
                    del self.pending[:len(ids)]
                    self.status['completed'] += len(ids)
            with self.lock:
                self.status['state'] = 'cancelled' if self.cancelled.is_set() else 'complete'
        except Exception as exc:
            with self.lock:
                error = str(exc) if isinstance(exc, ProviderError) else 'Provider descriptions failed structural validation or checkpoint persistence; retry explicitly.'
                self.status.update(state='failed', failed=len(self.pending), error=error)

    def register(self, package):
        with self.lock:
            if self.status['state'] == 'running':
                raise ValueError('cancel and wait before opening a recording')
            # Reuse the bounded portable validator, without trusting imported paths.
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / 'recording.json'
                source.write_text(json.dumps(package, allow_nan=False), encoding='utf-8')
                validated = load_portable(source)
            self._activate(validated, imported=True)
            self.pending = []
            self.config = None

    def preview_share(self, value):
        with self.lock:
            if value.get('recording_id') != self.recording_id:
                raise ValueError('recording changed; reload before continuing')
            if self.status['state'] == 'running':
                raise ValueError('generation running')
            # Retain at most one frozen derivative; a failed new preview expires it.
            self.share_preview = None
            result = prepare_share(self.package, {'custom_terms': value.get('custom_terms', [])})
            raw = json.dumps(result['package'], ensure_ascii=False, allow_nan=False).encode('utf-8')
            if len(raw) > MAX_BYTES:
                raise ValueError('sharing derivative exceeds size limit')
            preview_id = secrets.token_urlsafe(32)
            self.share_preview = {'id': preview_id, 'recording_id': self.recording_id,
                                  'expires': time.monotonic() + 15 * 60, 'raw': raw}
            text = raw.decode('utf-8')
            return {'preview_id': preview_id, 'recording_id': self.recording_id,
                    'report': result['report'], 'preview_text': text[:200000],
                    'truncated': len(text) > 200000}

    def export_share(self, value):
        with self.lock:
            preview = self.share_preview
            supplied = value.get('preview_id')
            if (value.get('recording_id') != self.recording_id or preview is None
                    or preview['recording_id'] != self.recording_id
                    or not isinstance(supplied, str) or not hmac.compare_digest(supplied, preview['id'])
                    or time.monotonic() >= preview['expires']):
                if preview is not None and time.monotonic() >= preview['expires']:
                    self.share_preview = None
                raise ValueError('sharing preview unavailable; preview and review again')
            return preview['raw']

    def import_native(self, value):
        with self.lock:
            if value.get('recording_id') != self.recording_id:
                raise ValueError('recording changed; reload before continuing')
            if self.status['state'] == 'running':
                raise ValueError('cancel and wait before opening a recording')
            package, _path = import_upload(value, self.state_dir)
            self._activate(package, imported=True)
            self.pending = []
            self.config = None

    # Compatibility for callers using the pre-multi-adapter service method.
    def import_artex(self, value):
        return self.import_native(value)

    def attachments(self, attachments):
        with self.lock:
            if self.status['state'] == 'running':
                raise ValueError('generation running')
            if not isinstance(attachments, list) or len(attachments) > 20:
                raise ValueError('expected at most 20 inert attachments')
            for item in attachments:
                if (not isinstance(item, dict) or set(item) != {'name', 'language', 'text'}
                        or not isinstance(item['name'], str) or not 1 <= len(item['name']) <= 180
                        or item['language'] != 'python' or not isinstance(item['text'], str)
                        or len(item['text'].encode('utf-8')) > 1024 * 1024):
                    raise ValueError('invalid inert Python attachment')
            candidate = copy.deepcopy(self.package)
            candidate['attachments'] = copy.deepcopy(attachments)
            if len(json.dumps(candidate, ensure_ascii=False, allow_nan=False).encode('utf-8')) > MAX_BYTES:
                raise ValueError('portable recording exceeds size limit')
            destination = self.state_dir / self.recording_id / 'recording.json'
            if destination.exists():
                temp = destination.with_suffix('.tmp')
                temp.write_text(json.dumps(candidate, ensure_ascii=False, allow_nan=False), encoding='utf-8')
                temp.replace(destination)
            self.package = candidate

    def html(self):
        assets = Path(__file__).resolve().parents[1] / 'web'
        with self.lock:
            data = json.dumps(self.package, ensure_ascii=False)
        for char, replacement in [('<', '\\u003c'), ('>', '\\u003e'), ('&', '\\u0026'), ('\u2028', '\\u2028'), ('\u2029', '\\u2029')]:
            data = data.replace(char, replacement)
        template = (assets / 'flow.html').read_text().replace("connect-src 'none'", "connect-src 'self'")
        template = apply_default_flow_theme(template)
        template = embed_brand_assets(template)
        template = template.replace('__TRACE_CSS__', (assets / 'flow.css').read_text())
        template = template.replace('__TRACE_JS__', (assets / 'flow.js').read_text())
        return template.replace('__TRACE_DATA__', data).encode('utf-8')


def make_server(service, port=0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, value, content_type='application/json', download=False):
            raw = value if isinstance(value, bytes) else json.dumps(value, allow_nan=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            if download:
                self.send_header('Content-Disposition', 'attachment; filename="artifact.bin"')
                self.send_header('X-TraceForge-Recording-Id', service.recording_id)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(raw)

        def allowed(self, write=False):
            hosts = {'127.0.0.1:' + str(self.server.server_port), 'localhost:' + str(self.server.server_port)}
            host = self.headers.get('Host', '')
            if host not in hosts:
                return False
            origin = self.headers.get('Origin')
            if origin is not None and origin != 'http://' + host:
                return False
            if self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none'):
                return False
            if write:
                supplied = self.headers.get('X-TraceForge-Token', '')
                return origin == 'http://' + host and hmac.compare_digest(supplied, service.token)
            return True

        def do_GET(self):
            if not self.allowed():
                return self.reply(403, {'error': 'same-origin access required'})
            if self.path == '/':
                return self.reply(200, service.html(), 'text/html; charset=utf-8')
            with service.lock:
                if self.path == '/api/session':
                    value = {'service': 'traceforge-explanations', 'token': service.token,
                             'recording_id': service.recording_id, 'source': source_identity(service.bundle), 'busy': service.status['state'] == 'running'}
                elif self.path == '/api/source':
                    value = source_identity(service.bundle)
                elif self.path == '/api/offline-status':
                    value = offline_status(service)
                elif self.path == '/api/offline-pending':
                    value = offline_pending(service)
                    if value is None:
                        return self.reply(404, {'error': 'No waiting offline job'})
                elif self.path == '/api/status':
                    value = service.snapshot()
                elif self.path in ('/api/export', '/api/recording'):
                    value = copy.deepcopy(service.package)
                elif self.path == '/api/annotations':
                    value = {'explanations': copy.deepcopy(service.package['payload'].get('explanations')), 'recording_id': service.recording_id}
                elif self.path == '/api/history':
                    try:
                        value = service.store.history(service.bundle, service.flow)
                    except Exception:
                        return self.reply(400, {'error': 'Annotation history unavailable'})
                else:
                    return self.reply(404, {'error': 'not found'})
            return self.reply(200, value)

        def do_POST(self):
            self.connection.settimeout(30)
            if not self.allowed(write=True):
                return self.reply(403, {'error': 'same-origin token required'})
            try:
                length = int(self.headers.get('Content-Length', '-1'))
                limits = {'/api/import-native': MAX_UPLOAD_BODY, '/api/import-artex': MAX_UPLOAD_BODY,
                          '/api/share-preview': 128 * 1024, '/api/share-export': 4096}
                limit = limits.get(self.path, MAX_BYTES * 2 + 1024)
                if not 0 <= length <= limit or self.headers.get('Transfer-Encoding'):
                    raise ValueError('invalid request size')
                if self.headers.get_content_type() != 'application/json':
                    raise ValueError('JSON required')
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict):
                    raise ValueError('object required')
                with service.lock:
                    if self.path != '/api/recording' and value.get('recording_id') != service.recording_id:
                        if self.path in ('/api/offline-request', '/api/offline-import', '/api/offline-start', '/api/offline-cancel'):
                            raise HandoffError('Offline handoff recording changed; reload before continuing.')
                        raise ValueError('recording changed; reload before continuing')
                    if self.path == '/api/recording':
                        service.register(read_json(value['recording_json']) if 'recording_json' in value else value['package'])
                    elif self.path == '/api/artifacts':
                        return self.reply(200, service.artifacts.listing())
                    elif self.path == '/api/artifact-preview':
                        return self.reply(200, service.artifacts.preview(value.get('artifact_id')))
                    elif self.path == '/api/artifact-download':
                        _row, raw = service.artifacts.read(value.get('artifact_id'))
                        return self.reply(200, raw, 'application/octet-stream', download=True)
                    elif self.path == '/api/share-preview':
                        return self.reply(200, service.preview_share(value))
                    elif self.path == '/api/share-export':
                        return self.reply(200, service.export_share(value))
                    elif self.path in ('/api/import-native', '/api/import-artex'):
                        service.import_native(value)
                    elif self.path == '/api/attachments':
                        service.attachments(value['attachments'])
                    elif self.path == '/api/offline-start':
                        return self.reply(200, offline_start(service, value))
                    elif self.path == '/api/offline-cancel':
                        return self.reply(200, offline_cancel(service, value))
                    elif self.path == '/api/offline-request':
                        return self.reply(200, export_request_set(service, value))
                    elif self.path == '/api/offline-import':
                        return self.reply(200, import_response_set(service, value))
                    elif self.path == '/api/preview':
                        return self.reply(200, service.preview(value))
                    elif self.path == '/api/generate':
                        service.start(value)
                    elif self.path == '/api/retry':
                        service.start({}, retry=True)
                    elif self.path == '/api/cancel':
                        service.cancelled.set()
                    elif self.path == '/api/restore':
                        with service.lock:
                            if service.status['state'] == 'running' or offline_status(service)['state'] == 'waiting':
                                raise ValueError('generation running')
                            service.package['payload']['explanations'] = service.store.restore(service.bundle, service.flow, value['revision'])
                    else:
                        return self.reply(404, {'error': 'not found'})
                    return self.reply(200, {'ok': True, 'recording_id': service.recording_id, 'source': source_identity(service.bundle)})
            except HandoffError as exc:
                return self.reply(400, {'error': str(exc)})
            except Exception:
                return self.reply(400, {'error': 'Invalid request or unavailable operation'})
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def serve(recording, state_dir, port=0):
    service = ExplanationService(recording, state_dir)
    server = make_server(service, port)
    print(f'AgentStoryline viewer: http://127.0.0.1:{server.server_port}/', flush=True)
    print('Press Ctrl-C to stop. Generation requires an explicit browser action.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        service.cancelled.set()
    finally:
        server.server_close()
