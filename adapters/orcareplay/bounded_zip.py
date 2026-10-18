"""Read the bounded ZIP32 store/deflate subset used by Orca exports.

No extraction and no zipfile object graph: the trailer bounds the central
directory before it is read or any member descriptors are allocated. Both CLI
headers and Go-style data descriptors are checked against that directory.
"""

from __future__ import annotations

import stat
import struct
import zlib


class BoundedZipError(ValueError):
    """Unsupported, corrupt or oversized ZIP input."""


def _fail(message):
    raise BoundedZipError(message)


def _read(stream, at, size):
    stream.seek(at)
    raw = stream.read(size)
    if len(raw) != size:
        _fail("truncated ZIP structure or member")
    return raw


def _extra(raw):
    at = 0
    while at < len(raw):
        if at + 4 > len(raw):
            _fail("malformed ZIP extra field")
        kind, size = struct.unpack_from("<HH", raw, at)
        at += 4
        if kind == 1:
            _fail("ZIP64 archives are unsupported")
        if at + size > len(raw):
            _fail("truncated ZIP extra field")
        at += size


def entries(stream, *, size, max_files, max_file_bytes, max_total_bytes,
            max_directory_bytes, normalize):
    """Yield (normalized path, size, directory flag, bounded read callable).

    Inventory/headers are validated before decompressing any payload. The caller
    must consume each regular member, including intentionally omitted snapshots,
    to validate its actual size, compressed stream and CRC.
    """
    tail_at = max(0, size - 22 - 65535)
    tail = _read(stream, tail_at, size - tail_at)
    at = len(tail)
    while True:
        at = tail.rfind(b"PK\x05\x06", 0, at)
        if at < 0:
            _fail("ZIP end-of-central-directory record is missing or malformed")
        if at + 22 <= len(tail) and at + 22 + struct.unpack_from("<H", tail, at + 20)[0] == len(tail):
            break
    _, disk, central_disk, disk_count, count, central_size, central_at, _ = struct.unpack_from("<4s4H2IH", tail, at)
    eocd_at = tail_at + at
    if disk or central_disk or disk_count != count:
        _fail("multi-disk ZIP archives are unsupported")
    if count == 65535 or central_size == 0xffffffff or central_at == 0xffffffff:
        _fail("ZIP64 archives are unsupported")
    if count > max_files or central_size > max_directory_bytes:
        _fail("ZIP central directory exceeds member/count byte limits")
    if central_at + central_size != eocd_at or central_size < 46 * count:
        _fail("ZIP central directory bounds are invalid")
    central = _read(stream, central_at, central_size)
    members, paths, file_paths = [], set(), set()
    total, cursor = 0, 0
    for _ in range(count):
        if cursor + 46 > len(central):
            _fail("truncated ZIP central directory")
        fields = struct.unpack_from("<4s6H3I5H2I", central, cursor)
        (signature, _made, version, flags, method, _time, _date, crc, compressed,
         uncompressed, name_size, extra_size, comment_size, member_disk,
         _internal, external, local_at) = fields
        if signature != b"PK\x01\x02":
            _fail("invalid ZIP central directory signature")
        end = cursor + 46 + name_size + extra_size + comment_size
        if end > len(central) or not 0 < name_size <= 1024:
            _fail("invalid ZIP member name or metadata bounds")
        if member_disk:
            _fail("multi-disk ZIP members are unsupported")
        if 0xffffffff in (compressed, uncompressed, local_at) or version > 20:
            _fail("ZIP64 or newer ZIP features are unsupported")
        if flags & ~0x080e or method not in (0, 8) or method == 0 and flags & 6:
            _fail("encrypted or unsupported ZIP flags/compression method")
        name_raw = central[cursor + 46:cursor + 46 + name_size]
        try:
            # Orca's CLI emits UTF-8 even without setting the UTF-8 flag.
            name = name_raw.decode("utf-8")
        except UnicodeError as exc:
            raise BoundedZipError("ZIP member names must be UTF-8") from exc
        if "\x00" in name:
            _fail("NUL in ZIP member path")
        path = normalize(name)
        if name != path + ("/" if name.endswith("/") else ""):
            _fail("noncanonical or ambiguous ZIP member path")
        if path in paths:
            _fail("duplicate or ambiguous ZIP member path")
        paths.add(path)
        mode = stat.S_IFMT(external >> 16)
        directory = name.endswith("/")
        if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or mode == stat.S_IFDIR and not directory:
            _fail("ZIP links and special members are forbidden")
        if external & 0x10 and not directory or directory and mode == stat.S_IFREG:
            _fail("ambiguous ZIP directory attributes")
        if directory and (uncompressed or crc):
            _fail("ZIP directory must have an empty body")
        if not directory:
            file_paths.add(path)
        total += uncompressed
        if uncompressed > max_file_bytes or total > max_total_bytes:
            _fail("ZIP exceeds member/total decompressed byte limits")
        _extra(central[cursor + 46 + name_size:cursor + 46 + name_size + extra_size])
        if local_at + 30 > central_at:
            _fail("ZIP local header is outside the member region")
        local = _read(stream, local_at, 30)
        (local_signature, local_version, local_flags, local_method, _lt, _ld,
         local_crc, local_compressed, local_uncompressed, local_name_size,
         local_extra_size) = struct.unpack("<4s5H3I2H", local)
        if (local_signature != b"PK\x03\x04" or local_version != version
                or local_flags != flags or local_method != method or local_name_size != name_size):
            _fail("ZIP local and central headers disagree")
        data_at = local_at + 30 + local_name_size + local_extra_size
        data_end = data_at + compressed
        if data_end > central_at:
            _fail("ZIP member exceeds its archive bounds")
        if _read(stream, local_at + 30, name_size) != name_raw:
            _fail("ZIP local and central member names disagree")
        _extra(_read(stream, local_at + 30 + name_size, local_extra_size))
        expected = (crc, compressed, uncompressed)
        actual = (local_crc, local_compressed, local_uncompressed)
        if flags & 8:
            if any(value not in (0, expected[index]) for index, value in enumerate(actual)):
                _fail("ZIP descriptor header disagrees with central directory")
            descriptor = _read(stream, data_end, min(16, central_at - data_end))
            if len(descriptor) >= 16 and descriptor[:4] == b"PK\x07\x08" and struct.unpack("<3I", descriptor[4:16]) == expected:
                data_end += 16
            elif len(descriptor) >= 12 and struct.unpack("<3I", descriptor[:12]) == expected:
                data_end += 12
            else:
                _fail("ZIP data descriptor is corrupt or unsupported")
        elif actual != expected:
            _fail("ZIP local and central sizes/checksum disagree")
        members.append((path, uncompressed, directory, local_at, data_end,
                        data_at, compressed, method, crc))
        cursor = end
    if cursor != len(central):
        _fail("ZIP central directory has uncounted or trailing records")
    for path in paths:
        if any("/".join(path.split("/")[:index]) in file_paths for index in range(1, len(path.split("/")))):
            _fail("ZIP file and directory paths conflict")
    # Reject overlapping, unreferenced and concatenated member regions. Offsets
    # remain absolute ZIP32 offsets; executable stubs are outside this profile.
    end = 0
    for member in sorted(members, key=lambda item: item[3]):
        if member[3] != end:
            _fail("ZIP member regions overlap or contain unreferenced data")
        end = member[4]
    if end != central_at:
        _fail("ZIP member region does not end at the central directory")
    for path, uncompressed, directory, _, _, data_at, compressed, method, crc in members:
        def read(data_at=data_at, compressed=compressed, uncompressed=uncompressed, method=method, crc=crc):
            stream.seek(data_at)
            inflater = zlib.decompressobj(-15) if method == 8 else None
            remaining, length, checksum, chunks = compressed, 0, 0, []
            while remaining:
                raw = stream.read(min(65536, remaining))
                if not raw:
                    _fail("truncated ZIP member payload")
                remaining -= len(raw)
                try:
                    content = inflater.decompress(raw, uncompressed - length + 1) if inflater else raw
                except zlib.error as exc:
                    raise BoundedZipError("corrupt ZIP deflate stream") from exc
                length += len(content)
                if length > uncompressed or inflater and inflater.unconsumed_tail:
                    _fail("ZIP member exceeds its declared decompressed size")
                checksum = zlib.crc32(content, checksum)
                chunks.append(content)
                if inflater and inflater.unused_data:
                    _fail("ZIP deflate member contains trailing compressed data")
            if inflater and not inflater.eof:
                _fail("ZIP deflate stream is truncated")
            if length != uncompressed or checksum != crc:
                _fail("ZIP member size or CRC checksum mismatch")
            return b"".join(chunks)
        yield path, uncompressed, directory, read
