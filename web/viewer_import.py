"""Bounded, read-only native recording normalization for the local viewer."""
import base64
import binascii
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import uuid

from adapters.artex.importer import import_artex, MAX_FILE_BYTES, _archive, _json
from adapters.codex.importer import import_codex
from adapters.orcareplay import import_orcareplay, looks_like_orcareplay_manifest, read_source_files
from adapters.otlp import import_otlp, looks_like_otlp_export
from analysis.explanation_workflow import load_portable
from analysis.narrative import build_observed_timeline
from web.recording import write_recording
from web.artifacts import write_binding
from analysis.explanations import recording_fingerprint
from web.render import build_payload

MAX_UPLOAD_BYTES = MAX_FILE_BYTES
MAX_UPLOAD_BODY = ((MAX_UPLOAD_BYTES + 2) // 3) * 4 + 4096


def _private_root(state_dir):
    root = Path(state_dir).resolve() / 'imports'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def _read_for_detection(source, limit=128 * 1024 * 1024):
    """Read a bounded regular file while pinning and not following its path."""
    path = Path(source).absolute()
    # macOS exposes /var and /tmp as fixed system aliases into /private. Treat
    # only those known aliases canonically; selected or user-directory symlinks
    # remain rejected below.
    parts = path.parts
    if len(parts) > 1 and parts[0] == '/' and parts[1] in ('var', 'tmp'):
        alias = Path('/') / parts[1]
        expected = Path('/private') / parts[1]
        try:
            if alias.is_symlink() and alias.resolve() == expected:
                path = expected.joinpath(*parts[2:])
        except OSError:
            pass
    for current in (path, *path.parents):
        if current.is_symlink():
            raise ValueError('recording detection does not follow symlinks')
    directory = None
    descriptor = None
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    try:
        directory = os.open(path.anchor, flags | getattr(os, 'O_DIRECTORY', 0))
        for part in path.parts[1:-1]:
            child = os.open(part, flags | getattr(os, 'O_DIRECTORY', 0), dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(path.name, flags | getattr(os, 'O_NONBLOCK', 0), dir_fd=directory)
        with os.fdopen(descriptor, 'rb') as handle:
            descriptor = None
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError('recording detection requires a bounded regular file')
            raw = handle.read(limit + 1)
            if len(raw) > limit:
                raise ValueError('recording exceeds detection byte limit')
            return raw
    except OSError as exc:
        raise ValueError('cannot inspect recording without following symlinks') from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def _jsonl_kind(raw):
    """Identify the two documented Codex JSONL surfaces without guessing.

    Native rollout records contain ``session_meta``. ``codex exec --json`` is a
    distinct event stream whose dotted event names must not be passed to the
    rollout importer and silently presented as an empty recording.
    """
    seen = set()
    for line in raw.splitlines()[:64]:
        if len(line) > 256 * 1024:
            return 'unknown-jsonl'
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, ValueError):
            return 'unknown-jsonl'
        if not isinstance(value, dict) or not isinstance(value.get('type'), str):
            return 'unknown-jsonl'
        seen.add(value['type'])
        if value['type'] == 'session_meta' and isinstance(value.get('payload'), dict):
            return 'codex-rollout'
    if any(kind in {'thread.started', 'turn.started', 'turn.completed', 'turn.failed', 'error'}
           or kind.startswith('item.') for kind in seen):
        return 'codex-exec-events'
    return 'unknown-jsonl'


def _manifest_source_kind(value):
    """Recognize recorded schema fields, never the selected file's name."""
    artex = (isinstance(value, dict) and type(value.get('format_version')) is int
             and value.get('format_version') in (1, 2, 3)
             and isinstance(value.get('tables'), dict)
             and 'task_id' in value and 'created_at' in value)
    orca = looks_like_orcareplay_manifest(value)
    if artex and orca:
        raise ValueError('ambiguous recording manifest declares multiple source formats')
    return 'artex' if artex else 'orcareplay' if orca else 'unknown-json'


def _looks_like_tar(raw):
    if len(raw) < tarfile.BLOCKSIZE:
        return False
    try:
        return tarfile.TarInfo.frombuf(raw[:tarfile.BLOCKSIZE], 'utf-8', 'surrogateescape') is not None
    except (tarfile.TarError, ValueError):
        return False


def detect_source(source):
    """Return a conservative source kind based on container and recorded shape."""
    path = Path(source).absolute()
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError('recording source is unavailable') from exc
    if stat.S_ISLNK(info.st_mode):
        raise ValueError('recording detection does not follow symlinks')
    if stat.S_ISDIR(info.st_mode):
        value = _json(_read_for_detection(path / 'manifest.json', MAX_FILE_BYTES))
        return _manifest_source_kind(value)
    raw = _read_for_detection(path)
    if raw.startswith(b'\x28\xb5\x2f\xfd'):
        # Artex's bounded decoder is selected by actual zstd magic. Its native
        # parser expects the documented suffix, so normalize a private copy.
        with tempfile.TemporaryDirectory(prefix='agentstoryline-detect-') as temporary:
            # Canonicalize only our private directory: macOS temp roots may
            # contain the system /var alias, rejected by the strict reader.
            archive_path = Path(temporary).resolve() / 'source.tar.zst'
            archive_path.write_bytes(raw)
            files = _archive(archive_path)
        kind = _manifest_source_kind(_json(files['manifest.json']))
        if kind != 'artex':
            raise ValueError('zstd archives support only the Artex snapshot profile')
        return kind
    if raw.startswith(b'PK'):
        # Orca's native .orca container is ZIP. A filename or ZIP signature alone
        # cannot establish its schema; inspect through the same bounded reader.
        files = read_source_files(path)
        kind = _manifest_source_kind(_json(files['manifest.json']))
        return 'orcareplay' if kind == 'orcareplay' else 'unknown-zip'
    if _looks_like_tar(raw):
        files = read_source_files(path)
        return _manifest_source_kind(_json(files['manifest.json']))
    if path.name.lower().endswith('.jsonl'):
        return _jsonl_kind(raw)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        return _jsonl_kind(raw)
    if isinstance(value, dict) and value.get('format') == 'traceforge-recording':
        return 'portable'
    if looks_like_otlp_export(value):
        return 'otlp'
    if isinstance(value, dict) and isinstance(value.get('type'), str):
        kind = value['type']
        if kind in {'thread.started', 'turn.started', 'turn.completed', 'turn.failed', 'error'} or kind.startswith('item.'):
            return 'codex-exec-events'
    kind = _manifest_source_kind(value)
    # An OrcaReplay manifest alone is not a recording; events must be selected
    # together in an extracted run directory, native ZIP, or plain tar archive.
    return kind if kind != 'orcareplay' else 'unknown-json'


def normalize_native(source, state_dir, source_kind=None):
    """Reserve unique private state, retaining source artifacts after validation."""
    root = _private_root(state_dir)
    with tempfile.TemporaryDirectory(prefix='.normalize-', dir=root) as temporary:
        staging = Path(temporary)
        source_kind = source_kind or detect_source(source)
        if source_kind == 'codex-exec-events':
            raise ValueError('Codex exec --json event streams are distinct from native rollout transcripts and are not supported yet')
        if source_kind == 'codex-rollout':
            bundle = import_codex(source, staging / 'bundle')
        elif source_kind == 'otlp':
            bundle = import_otlp(source, staging / 'bundle')
        elif source_kind == 'orcareplay':
            bundle = import_orcareplay(source, staging / 'bundle')
        elif source_kind == 'artex':
            native = Path(source)
            if native.is_dir():
                bundle = import_artex(native, staging / 'bundle')
            else:
                raw = _read_for_detection(native)
                # A detected archive may have a misleading filename. Normalize
                # only its private working copy for Artex's suffix-based parser.
                suffix = '.tar.zst' if raw.startswith(b'\x28\xb5\x2f\xfd') else '.tar' if _looks_like_tar(raw) else None
                if suffix and not native.name.endswith(suffix):
                    selected = staging / ('selected' + suffix)
                    selected.write_bytes(raw)
                    bundle = import_artex(selected, staging / 'bundle')
                    selected.unlink()
                else:
                    bundle = import_artex(native, staging / 'bundle')

        else:
            raise ValueError('unrecognized recording; choose portable AgentStoryline JSON, an Artex archive/snapshot, an OrcaReplay run directory/.orca ZIP/plain tar, Codex native rollout JSONL, or canonical OTLP JSON')
        package_path = staging / 'recording.traceforge.json'
        write_recording(build_payload(bundle, build_observed_timeline(bundle)), package_path)
        package = load_portable(package_path)
        write_binding(staging, bundle, recording_fingerprint(bundle))
        # Atomic publication to an unpredictable new path. No source lifecycle writes.
        destination = root / uuid.uuid4().hex
        staging.rename(destination)
        return package, destination / package_path.name


def normalize_source(source, state_dir):
    path = Path(source)
    kind = detect_source(path)
    if kind == 'portable':
        return load_portable(path), path
    return normalize_native(path, state_dir, kind)


def normalize_orca_collection(sources, state_dir):
    """Atomically publish a validated, explicitly selected Orca collection."""
    from adapters.orcareplay.collection import import_orca_collection
    root = _private_root(state_dir)
    with tempfile.TemporaryDirectory(prefix='.collection-', dir=root) as temporary:
        staging = Path(temporary)
        bundle = import_orca_collection(sources, staging / 'bundle')
        package_path = staging / 'recording.traceforge.json'
        write_recording(build_payload(bundle, build_observed_timeline(bundle)), package_path)
        package = load_portable(package_path)
        write_binding(staging, bundle, recording_fingerprint(bundle))
        destination = root / uuid.uuid4().hex
        staging.rename(destination)
        return package, destination / package_path.name


def import_upload(value, state_dir):
    if not isinstance(value, dict):
        raise ValueError('upload must be an object')
    if 'files' in value:
        rows = value['files']
        if not isinstance(rows, list) or not 2 <= len(rows) <= 16:
            raise ValueError('select 2–16 Orca recording files')
        if any(key in value for key in ('filename', 'source_base64', 'archive_base64')):
            raise ValueError('choose either a single upload or a collection')
        total_encoded = sum(len(row.get('source_base64', '')) for row in rows if isinstance(row, dict) and isinstance(row.get('source_base64'), str))
        if total_encoded > ((MAX_UPLOAD_BYTES + 2) // 3) * 4 + 64:
            raise ValueError('Orca collection exceeds aggregate upload limit')
        root = _private_root(state_dir)
        with tempfile.TemporaryDirectory(prefix='traceforge-upload-') as temporary:
            sources, total = [], 0
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise ValueError('invalid Orca upload')
                name, encoded = row.get('filename'), row.get('source_base64')
                if (not isinstance(name, str) or len(name) > 255 or '/' in name or '\\' in name
                        or not name.lower().endswith(('.orca', '.orca.zip')) or not isinstance(encoded, str)):
                    raise ValueError('collections accept only native .orca or .orca.zip files')
                try:
                    raw = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise ValueError('invalid native recording encoding') from exc
                total += len(raw)
                if total > MAX_UPLOAD_BYTES:
                    raise ValueError('Orca collection exceeds aggregate upload limit')
                source = Path(temporary).resolve() / ('source-%d.orca' % index)
                source.write_bytes(raw)
                sources.append(source)
            return normalize_orca_collection(sources, state_dir)
    filename = value.get('filename')
    encoded = value.get('source_base64', value.get('archive_base64'))
    if (not isinstance(filename, str) or len(filename) > 255 or '/' in filename or '\\' in filename
            or not filename.lower().endswith(('.tar', '.tar.zst', '.orca', '.orca.zip', '.zip', '.jsonl', '.json'))):
        raise ValueError('expected an Artex archive, OrcaReplay .orca ZIP/plain tar, Codex native rollout JSONL, or canonical OTLP JSON')
    if not isinstance(encoded, str) or len(encoded) > ((MAX_UPLOAD_BYTES + 2) // 3) * 4:
        raise ValueError('native recording exceeds upload limit')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError('invalid native recording encoding') from exc
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError('native recording exceeds upload limit')
    root = _private_root(state_dir)
    with tempfile.TemporaryDirectory(prefix='traceforge-upload-') as temporary:
        suffix = '.tar.zst' if filename.lower().endswith('.tar.zst') else Path(filename).suffix.lower()
        source = Path(temporary).resolve() / ('source' + suffix)
        source.write_bytes(raw)
        return normalize_native(source, state_dir)


def import_telemetry_upload(value, state_dir):
    """Import 1-16 native Orca files without changing the active recording."""
    if not isinstance(value, dict):
        raise ValueError('telemetry upload must be an object')
    rows = value.get('files')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
        raise ValueError('select 1-16 native Orca recording files')
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('filename'), str)
                or not row['filename'].lower().endswith(('.orca', '.orca.zip'))):
            raise ValueError('telemetry accepts only native Orca recording files')
    upload = {'files': rows} if len(rows) > 1 else dict(rows[0])
    package, path = import_upload(upload, state_dir)
    from adapters.registry import source_identity
    if source_identity(package['payload']['bundle'])['adapter_id'] != 'orcareplay':
        raise ValueError('telemetry attachment requires OrcaReplay evidence')
    return package, path
