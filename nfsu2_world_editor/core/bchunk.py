"""Byte-preserving chunk tree: parse any .BUN/.BIN region into nodes and write it
back.

Writing an unmodified tree reproduces the input byte for byte. When something
inside changes size, everything after it is laid out again:

* leaves of the chunk types below get their ``0x11`` alignment filler recomputed
  from the new **absolute** file offset (the game aligns these payloads);
* zero-id padding chunks are resized so the chunk after them keeps its original
  position modulo 128 (the convention the shipped files follow);
* container sizes are recomputed.

The alignment table was measured on every leaf of the eight shipped TRACKS
regions: each listed id has its payload on that boundary (with 0x11 filler when
needed) in 100% of instances. Ids not listed are stored verbatim.
"""
import struct

FILLER = 0x11
PAD_MOD = 128

# chunk id -> payload alignment (absolute file offset)
FILLER_ALIGN = {
    # geometry (0x80134000 solid lists)
    0x00134011: 16, 0x00134012: 8, 0x0013401A: 16, 0x00134900: 16,
    0x00134B01: 128, 0x00134B02: 16, 0x00134B03: 16,
    # texture pixel block
    0x33320002: 128,
    # scenery / lights / misc. stream chunks
    0x00034027: 16, 0x00034103: 32, 0x00035020: 16, 0x00036002: 16, 0x00036003: 16,
    0x00135002: 64, 0x00135003: 16, 0x00135102: 16,
    # L4Rx.BUN
    0x00034026: 16, 0x00034250: 16, 0x00035021: 16, 0x00037080: 16, 0x00037090: 16,
    0x00037110: 16, 0x00037150: 16, 0x00039200: 16, 0x00039202: 16, 0x00E34010: 16,
    0x0003B210: 8,
}

_HDR = struct.Struct('<II')


class Chunk:
    """One chunk. Containers have ``children`` (list), leaves have ``data``.

    ``aligned``: payload is preceded by filler up to ``FILLER_ALIGN[id]`` and
    ``data`` excludes that filler. Otherwise ``data`` is the raw payload.
    For padding chunks (``id == 0``) ``pad_target`` is the original position
    modulo 128 of the byte after the chunk.
    """
    __slots__ = ('id', 'data', 'children', 'aligned', 'pad_target', 'pad_mod', 'offset', 'size', 'dirty')

    def __init__(self, cid, data=b'', children=None, aligned=False):
        self.id = cid
        self.data = data
        self.children = children
        self.aligned = aligned
        self.pad_target = None
        self.pad_mod = PAD_MOD
        self.offset = None   # original absolute offset of the chunk header (None = new)
        self.size = None     # original payload size
        self.dirty = True

    @property
    def is_container(self):
        return self.children is not None

    def find(self, cid):
        """First direct child with id ``cid`` or None."""
        for c in self.children or ():
            if c.id == cid:
                return c
        return None

    def find_all(self, cid, recursive=True):
        out = []
        for c in self.children or ():
            if c.id == cid:
                out.append(c)
            elif recursive and c.children is not None:
                out.extend(c.find_all(cid, True))
        return out

    def __repr__(self):
        if self.children is not None:
            return '<Chunk %08X [%d children]>' % (self.id, len(self.children))
        return '<Chunk %08X %d bytes%s>' % (self.id, len(self.data), ' aligned' if self.aligned else '')


def parse(buf, start=0, end=None):
    """Parse ``buf[start:end]`` (offsets are absolute file offsets into ``buf``)
    into a list of Chunk. Trailing bytes that do not form a chunk are returned as
    a raw pseudo-chunk with id ``None``."""
    if end is None:
        end = len(buf)
    out = []
    off = start
    mv = memoryview(buf)
    while off + 8 <= end:
        cid, size = _HDR.unpack_from(buf, off)
        data = off + 8
        if data + size > end:
            break
        ch = _parse_one(buf, mv, cid, data, size)
        ch.offset = off
        out.append(ch)
        off = data + size
    if off < end:
        tail = Chunk(None, mv[off:end])
        tail.offset = off
        tail.dirty = False
        out.append(tail)
    return out


def _parse_one(buf, mv, cid, data, size):
    if cid & 0x80000000:
        ch = Chunk(cid, children=parse(buf, data, data + size))
        # a container whose children do not tile it exactly is kept raw
        if sum(_orig_len(c) for c in ch.children) != size:
            ch = Chunk(cid, mv[data:data + size])
            ch.children = None
    elif cid == 0:
        ch = Chunk(0, mv[data:data + size])
        ch.pad_target = (data + size) % PAD_MOD
    else:
        a = FILLER_ALIGN.get(cid)
        pad = (-data) % a if a else 0
        if a and pad <= size and buf[data:data + pad] == bytes([FILLER]) * pad:
            ch = Chunk(cid, mv[data + pad:data + size], aligned=True)
        else:
            ch = Chunk(cid, mv[data:data + size])
    ch.size = size
    ch.dirty = False
    return ch


def _orig_len(c):
    return (len(c.data) if c.id is None else c.size + 8)


def serialize(chunks, base=0, out=None):
    """Write chunks to a bytearray as if it starts at absolute offset ``base``."""
    if out is None:
        out = bytearray()
    start = len(out)
    for c in chunks:
        _write(c, base + (len(out) - start), out, base - start)
    return out


def _write(c, pos, out, rel):
    # rel: absolute = rel + len(out)
    if c.id is None:
        out += c.data
        return
    hdr = len(out)
    out += b'\0\0\0\0\0\0\0\0'
    if c.children is not None:
        for k in c.children:
            _write(k, rel + len(out), out, rel)
    elif c.id == 0:
        n = len(c.data)
        if c.pad_target is not None and (c.offset != pos or c.dirty):
            n = (c.pad_target - (pos + 8)) % c.pad_mod
            if c.offset is not None and c.size is not None and rel == 0:
                # keep the next chunk at its original offset when the padding can absorb the shift
                m = c.offset + 8 + c.size - (pos + 8)
                if m >= 0 and (m - n) % c.pad_mod == 0:
                    n = m
            out += bytes(n) if n != len(c.data) or any(c.data) else c.data
        else:
            out += c.data
    else:
        if c.aligned:
            a = FILLER_ALIGN[c.id]
            out += bytes([FILLER]) * ((-(rel + len(out))) % a)
        out += c.data
    struct.pack_into('<II', out, hdr, c.id, len(out) - hdr - 8)


def chunk_bytes(c, pos):
    """Serialise a single chunk placed at absolute offset ``pos``."""
    return bytes(serialize([c], pos))


def walk_nodes(chunks, depth=0):
    for c in chunks:
        yield depth, c
        if c.children is not None:
            yield from walk_nodes(c.children, depth + 1)
