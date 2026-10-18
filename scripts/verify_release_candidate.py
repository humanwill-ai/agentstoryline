#!/usr/bin/env python3
"""Verify a clean staged public snapshot locally without executing its contents."""

from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from urllib.parse import unquote, urlsplit

# A verifier invoked from inside the candidate must not create runtime caches.
if __name__ == "__main__":
    sys.dont_write_bytecode = True

try:
    from . import build_public_source as builder
except ImportError:  # Direct invocation: python3 scripts/verify_release_candidate.py ...
    import build_public_source as builder


REQUIRED_RELEASE_FILES = frozenset({
    "LICENSE", "THIRD_PARTY_NOTICES.md", "README.md", "CONTRIBUTING.md",
    "SECURITY.md", "CHANGELOG.md", "docs/RELEASE_SCOPE_V0_1.md",
    "docs/PUBLIC_SOURCE_AUDIT.md", "docs/RELEASE_VALIDATION.md",
    "docs/RELEASE_COMMANDS.md", "docs/RELEASE_NOTES_V0_1.md",
    "docs/PUBLICATION_CHECKLIST.md", "scripts/verify_release_candidate.py",
    "agentstoryline_version.py", "docs/ORCAREPLAY_ADAPTER.md",
    "adapters/orcareplay/__init__.py", "adapters/orcareplay/importer.py",
    "adapters/orcareplay/bounded_zip.py",
    "examples/experimental-orcareplay.tar", "examples/experimental-orcareplay.orca.zip", "tests/test_orcareplay_examples.py",
})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MANIFEST_KEYS = {
    "format", "version", "files", "file_count", "total_bytes",
    "content_sha256", "omitted_optional_files", "scope", "release_version",
}


def _safe_path(name):
    if not isinstance(name, str) or not name:
        raise ValueError("manifest paths must be nonempty strings")
    path = PurePosixPath(name)
    if (path.is_absolute() or path.as_posix() != name or ".." in path.parts
            or "\\" in name or "\x00" in name
            or any(part in builder.FORBIDDEN_PARTS for part in path.parts)
            or path.name in builder.FORBIDDEN_NAMES or path.name.startswith(".env")
            or "mixkit" in name.lower()
            or path.suffix.lower() in {".key", ".pem", ".pyc", ".pyo", ".wav"}):
        raise ValueError(f"prohibited snapshot path: {name}")
    return name


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate manifest JSON key: {key}")
        result[key] = value
    return result


def _regular_bytes(path):
    # lstat first: never open a symlink or a FIFO supplied as snapshot content.
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"snapshot entry must be a regular file: {path.name}")
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f"snapshot entry must be a regular file: {path.name}")
        return stream.read()


def _inventory(root):
    files, directories = set(), set()

    def visit(directory):
        with os.scandir(directory) as entries:
            for entry in entries:
                name = Path(entry.path).relative_to(root).as_posix()
                _safe_path(name)
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISDIR(mode):
                    directories.add(name)
                    visit(Path(entry.path))
                elif stat.S_ISREG(mode):
                    files.add(name)
                else:
                    raise ValueError(f"symlink or special snapshot entry: {name}")

    visit(root)
    return files, directories


def _validate_manifest(manifest):
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
        raise ValueError("unexpected manifest schema")
    if (manifest["format"] != "agentstoryline-public-source"
            or type(manifest["version"]) is not int or manifest["version"] != 1):
        raise ValueError("unsupported manifest format/version")
    if manifest["release_version"] != builder.__version__:
        raise ValueError("manifest release_version differs from the application version")
    if not isinstance(manifest["scope"], str) or not manifest["scope"].strip():
        raise ValueError("manifest scope must be nonempty text")
    for key in ("file_count", "total_bytes"):
        if type(manifest[key]) is not int or manifest[key] < 0:
            raise ValueError(f"manifest {key} must be a nonnegative integer")
    if not isinstance(manifest["files"], list):
        raise ValueError("manifest files must be a list")
    paths = []
    for item in manifest["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "bytes", "sha256"}:
            raise ValueError("unexpected manifest file-entry schema")
        paths.append(_safe_path(item["path"]))
        if type(item["bytes"]) is not int or item["bytes"] < 0:
            raise ValueError("file byte counts must be nonnegative integers")
        if not isinstance(item["sha256"], str) or not _SHA256.fullmatch(item["sha256"]):
            raise ValueError("file SHA-256 must be lowercase hexadecimal")
    if paths != sorted(set(paths)):
        raise ValueError("manifest file paths must be unique and sorted")
    if manifest["file_count"] != len(paths):
        raise ValueError("manifest file_count mismatch")
    if manifest["total_bytes"] != sum(item["bytes"] for item in manifest["files"]):
        raise ValueError("manifest total_bytes mismatch")
    digest = hashlib.sha256(builder._json_bytes(manifest["files"])).hexdigest()
    if manifest["content_sha256"] != digest:
        raise ValueError("manifest aggregate content_sha256 mismatch")
    omitted = manifest["omitted_optional_files"]
    if (not isinstance(omitted, list) or any(not isinstance(name, str) for name in omitted)
            or omitted != sorted(set(omitted))):
        raise ValueError("omitted optional paths must be unique and sorted")
    allowed = set(builder._checked_paths())
    if not set(paths).issubset(allowed):
        raise ValueError("manifest includes paths outside the reviewed source allowlist")
    missing = (set(builder.PUBLIC_FILES) | REQUIRED_RELEASE_FILES) - set(paths)
    if missing:
        raise ValueError("missing required release files: " + ", ".join(sorted(missing)))
    if set(omitted) != set(builder.OPTIONAL_FILES) - set(paths):
        raise ValueError("omitted optional paths do not match absent optional files")
    return set(paths)


class _HTMLLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.anchors = [], set()

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if value is not None:
                if key in {"href", "src"}:
                    self.links.append(value)
                if key == "id" or (tag == "a" and key == "name"):
                    self.anchors.add(value)


def _markdown_text(raw):
    text = raw.decode("utf-8")
    # Fenced and inline code are examples, not navigable document links.
    lines, fence = [], None
    for line in text.splitlines():
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            lines.append("")
        else:
            lines.append(line if fence is None else "")
    return "\n".join(lines)


def _markdown_data(raw):
    text = _markdown_text(raw)
    links_text = re.sub(r"(`+).*?\1", "", text)
    html = _HTMLLinks()
    html.feed(links_text)
    anchors, duplicates = html.anchors, {}
    for line in text.splitlines():
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            title = re.sub(r"<[^>]+>", "", heading.group(1)).lower()
            title = re.sub(r"[^\w\-\s]", "", title).replace(" ", "-")
            count = duplicates.get(title, 0)
            duplicates[title] = count + 1
            anchors.add(title + (f"-{count}" if count else ""))
    text = links_text
    refs = {}
    for match in re.finditer(r"(?m)^\s{0,3}\[([^]\n]+)\]:\s*(<[^>]+>|\S+)", text):
        refs[" ".join(match.group(1).lower().split())] = match.group(2).strip("<>")
    # Balanced single-level parentheses cover ordinary file names and link titles.
    for match in re.finditer(r"!?\[[^]\n]*\]\(\s*(<[^>]+>|(?:[^()\n]|\([^()\n]*\))*)\)", text):
        value = match.group(1).strip()
        html.links.append(value[1:value.index(">")] if value.startswith("<") else value.split()[0] if value else "")
    for match in re.finditer(r"!?\[([^]\n]+)\]\[([^]\n]*)\]", text):
        ref = " ".join((match.group(2) or match.group(1)).lower().split())
        if ref not in refs:
            raise ValueError(f"undefined Markdown reference: {ref}")
        html.links.append(refs[ref])
    for match in re.finditer(r"(?<![!\]])\[([^]\n]+)\](?![\[( :])", text):
        ref = " ".join(match.group(1).lower().split())
        if ref in refs:
            html.links.append(refs[ref])
    return html.links, anchors


def _check_markdown(payloads, directories):
    documents = {name: _markdown_data(raw) for name, raw in payloads.items() if name.endswith(".md")}
    checked = 0
    for name, (links, _) in documents.items():
        for link in links:
            url = urlsplit(link)
            if url.scheme.lower() == "file":
                raise ValueError(f"unsafe local Markdown link in {name}: {link}")
            if url.scheme or url.netloc:
                continue  # Never resolve, fetch or validate external destinations.
            target = unquote(url.path)
            if "\\" in target or "\x00" in target or target.startswith("/"):
                raise ValueError(f"unsafe local Markdown link in {name}: {link}")
            parts = list(PurePosixPath(name).parent.parts)
            for part in PurePosixPath(target).parts:
                if part == "..":
                    if not parts:
                        raise ValueError(f"Markdown link escapes snapshot in {name}: {link}")
                    parts.pop()
                elif part != ".":
                    parts.append(part)
            destination = "/".join(parts) if target else name
            if destination not in payloads and destination not in directories and destination != "":
                raise ValueError(f"broken local Markdown link in {name}: {link}")
            if url.fragment and destination in documents:
                if unquote(url.fragment) not in documents[destination][1]:
                    raise ValueError(f"broken Markdown anchor in {name}: {link}")
            checked += 1
    return checked


def verify_release_candidate(snapshot):
    """Read and validate only; reject caches, extra directories and all symlinks.

    This checks current local bytes, not authenticity or hostile concurrent writes.
    Only this verifier's builder/version dependencies are imported. The viewer,
    examples, recorded actions, and tests are never run.
    """
    root = Path(snapshot).absolute()
    builder._no_symlinks(root)
    if not root.is_dir():
        raise ValueError("snapshot must be a directory")
    root = root.resolve()
    files, directories = _inventory(root)
    manifest_raw = _regular_bytes(root / builder.MANIFEST_NAME)
    if len(manifest_raw) > 16 * 1024 * 1024:
        raise ValueError("manifest exceeds 16 MiB")
    manifest = json.loads(manifest_raw, object_pairs_hook=_no_duplicate_keys)
    expected = _validate_manifest(manifest)
    if files != expected | {builder.MANIFEST_NAME}:
        raise ValueError("snapshot inventory differs from manifest (extra or missing files)")
    expected_directories = {
        parent.as_posix() for name in expected for parent in PurePosixPath(name).parents
        if parent != PurePosixPath(".")
    }
    if directories != expected_directories:
        raise ValueError("snapshot contains extra or missing directories; runtime caches are refused")
    payloads = {}
    for item in manifest["files"]:
        raw = _regular_bytes(root / item["path"])
        if len(raw) != item["bytes"]:
            raise ValueError(f"file byte count mismatch: {item['path']}")
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError(f"file SHA-256 mismatch: {item['path']}")
        payloads[item["path"]] = raw
    links = _check_markdown(payloads, directories)
    return {
        "file_count": manifest["file_count"], "total_bytes": manifest["total_bytes"],
        "release_version": manifest["release_version"],
        "content_sha256": manifest["content_sha256"], "local_markdown_links_checked": links,
        "omitted_optional_files": manifest["omitted_optional_files"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="Clean staged directory; no caches or extra files")
    args = parser.parse_args()
    try:
        result = verify_release_candidate(args.snapshot)
    except (OSError, ValueError, UnicodeError) as exc:
        parser.exit(1, f"Release candidate verification failed: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
