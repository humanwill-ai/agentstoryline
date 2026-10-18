"""Original bytes require local bindings beneath the configured imports anchor.

The trusted absolute anchor is opened directly for scoped filesystem access.
Every imported component beneath it is traversed without following symlinks.
Portable metadata never selects an anchor or grants filesystem access.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

MAX_FILE_BYTES = 32 * 1024 * 1024
PREVIEW_BYTES = 64 * 1024


def inventory(bundle):
    source = bundle.get('manifest', {}).get('source', {})
    if not isinstance(source, dict):
        return []
    sanitization = source.get('sanitization')
    if isinstance(sanitization, dict) and sanitization.get('kind') == 'sanitized-derivative':
        return []
    rows = source.get('archive_inventory', [])
    result = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name = row.get('path')
        if (not isinstance(name, str) or not name.startswith('files/') or '\\' in name
                or name != PurePosixPath(name).as_posix() or '..' in PurePosixPath(name).parts
                or not re.fullmatch(r'[0-9a-f]{64}', str(row.get('sha256', '')))
                or type(row.get('bytes')) is not int or not 0 <= row['bytes'] <= MAX_FILE_BYTES):
            continue
        result.append({key: row[key] for key in ('path', 'bytes', 'sha256')})
    return result


def write_binding(directory, bundle, fingerprint):
    """Called only by native normalization after the importer validates all bytes."""
    value = {'recording_id': fingerprint, 'files': inventory(bundle)}
    (Path(directory) / 'artifact-binding.json').write_text(json.dumps(value), encoding='utf-8')


class ArtifactStore:
    def __init__(self, state_dir, bundle, fingerprint):
        self.fingerprint = fingerprint
        self.rows = inventory(bundle)
        # Directory names are fixed adapter constants, never portable paths.
        source_adapter = bundle.get('manifest', {}).get('source', {}).get('adapter')
        artifact_adapter = 'orcareplay' if source_adapter == 'orcareplay' else 'artex'
        self.root = None
        self.allowed = {}
        imports = Path(state_dir).resolve() / 'imports'
        self.imports = imports
        # No path supplied by the portable package is ever used as a root.
        if imports.is_dir() and not imports.is_symlink():
            for directory in sorted(imports.iterdir()):
                if not re.fullmatch(r'[0-9a-f]{32}', directory.name) or directory.is_symlink():
                    continue
                binding = directory / 'artifact-binding.json'
                try:
                    if binding.is_symlink() or binding.stat().st_size > 2 * 1024 * 1024:
                        continue
                    value = json.loads(binding.read_text(encoding='utf-8'))
                    if value != {'recording_id': fingerprint, 'files': self.rows}:
                        continue
                    self.root = directory / 'bundle' / 'artifacts' / artifact_adapter
                    break
                except (OSError, ValueError):
                    continue
        for row in self.rows:
            key = hashlib.sha256((fingerprint + '\0' + row['path'] + '\0' + row['sha256']).encode()).hexdigest()
            self.allowed[key] = row

    def _read(self, row):
        if self.root is None:
            raise ValueError('Original bytes unavailable; import the native archive locally')
        # Open the explicitly configured trusted anchor directly: macOS scoped
        # access may allow this directory but deny walking its system ancestors.
        # All imported components below the anchor still require O_NOFOLLOW.
        if (not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY')
                or os.open not in getattr(os, 'supports_dir_fd', set())):
            raise ValueError('Original bytes unavailable on this platform')
        descriptors = []
        try:
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            current = os.open(self.imports, flags | os.O_DIRECTORY)
            descriptors.append(current)
            for part in self.root.relative_to(self.imports).parts + PurePosixPath(row['path']).parts[:-1]:
                current = os.open(part, flags | os.O_DIRECTORY, dir_fd=current)
                descriptors.append(current)
            fd = os.open(PurePosixPath(row['path']).name, flags, dir_fd=current)
            descriptors.append(fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size != row['bytes'] or info.st_size > MAX_FILE_BYTES:
                raise ValueError('Original bytes unavailable or changed')
            with os.fdopen(os.dup(fd), 'rb') as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) != row['bytes'] or hashlib.sha256(raw).hexdigest() != row['sha256']:
                raise ValueError('Original bytes unavailable or changed')
            return raw
        except (OSError, NotImplementedError) as exc:
            raise ValueError('Original bytes unavailable or changed') from exc
        finally:
            for fd in reversed(descriptors):
                os.close(fd)

    def listing(self):
        result = []
        for key, row in self.allowed.items():
            available = True
            reason = None
            try:
                self._read(row)
            except ValueError as exc:
                available, reason = False, str(exc)
            result.append(dict(row, artifact_id=key, name=PurePosixPath(row['path']).name,
                               available=available, reason=reason))
        return {'recording_id': self.fingerprint, 'artifacts': result}

    def read(self, key):
        if not isinstance(key, str) or key not in self.allowed:
            raise ValueError('Unknown artifact')
        row = self.allowed[key]
        return row, self._read(row)

    def preview(self, key):
        row, raw = self.read(key)
        binary = b'\0' in raw
        try:
            raw.decode('utf-8')
        except UnicodeError:
            binary = True
        text = '' if binary else raw[:PREVIEW_BYTES].decode('utf-8', errors='ignore')
        return {'recording_id': self.fingerprint, 'name': PurePosixPath(row['path']).name,
                'bytes': len(raw), 'binary': binary, 'text': text,
                'truncated': not binary and len(raw) > PREVIEW_BYTES}
