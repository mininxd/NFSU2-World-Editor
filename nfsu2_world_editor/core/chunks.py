"""Generic chunk walking for NFSU2 .BUN/.BIN files.

A chunk is ``uint32 id, uint32 size, payload[size]`` (little-endian). When
``id & 0x80000000`` the payload is itself a list of child chunks.
"""
import struct

_HDR = struct.Struct('<II')
CONTAINER_FLAG = 0x80000000

# Payload alignment (absolute file offset) for chunks that start with 0x11 filler.
ALIGN = {0x00134B01: 128, 0x33320002: 128, 0x00134012: 8}
DEFAULT_ALIGN = 16
FILLER = 0x11


def is_container(cid):
    return bool(cid & CONTAINER_FLAG)


def iter_chunks(buf, start=0, end=None):
    """Yield ``(id, data_offset, size)`` for each chunk directly in ``buf[start:end]``.

    Stops silently at the first chunk whose size runs past ``end``.
    """
    if end is None:
        end = len(buf)
    off = start
    while off + 8 <= end:
        cid, size = _HDR.unpack_from(buf, off)
        if off + 8 + size > end:
            break
        yield cid, off + 8, size
        off += 8 + size


def walk(buf, start=0, end=None, depth=0, path=()):
    """Recursive walk. Yields ``(kind, offset, id, size, depth, path)`` where kind is
    ``'CHUNK'`` or ``'BAD'`` (a chunk overrunning its parent; walking that level stops)."""
    if end is None:
        end = len(buf)
    off = start
    while off + 8 <= end:
        cid, size = _HDR.unpack_from(buf, off)
        data = off + 8
        if data + size > end:
            yield ('BAD', off, cid, size, depth, path)
            return
        yield ('CHUNK', off, cid, size, depth, path)
        if is_container(cid) and size >= 8:
            yield from walk(buf, data, data + size, depth + 1, path + (cid,))
        off = data + size


def payload_start(buf, cid, data, size):
    """Return where the real payload begins, skipping 0x11 alignment filler only if
    it is actually present (alignment is relative to the absolute file offset)."""
    a = ALIGN.get(cid, DEFAULT_ALIGN)
    pad = (-data) % a
    if pad and pad <= size and buf[data:data + pad] == bytes([FILLER]) * pad:
        return data + pad
    return data


def find(buf, start, end, cid_want):
    """Yield ``(data_offset, size)`` of every chunk with id ``cid_want``, descending
    into containers (but not into a matching container itself)."""
    for cid, d, s in iter_chunks(buf, start, end):
        if cid == cid_want:
            yield d, s
        elif is_container(cid):
            yield from find(buf, d, d + s, cid_want)
