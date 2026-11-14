"""Streaming visibility (``0x80034150``) in ``L4Rx.BUN``.

::

    0x80034150  visible-section manager
    +-- 0x00034151  header: u32, u32 count, u16 list (kept raw)
    +-- 0x00034152  drivable boundaries (variable records)
    +-- 0x00034153  drivable section visible lists (164-byte records)
    +-- 0x00034154  (32-byte records, kept raw)
    +-- 0x00034155  loading sections (76-byte records)

Every record starts with 8 bytes the game overwrites at load time (list links;
``0b 00 00 00 0b 00 00 00`` in the files) which are kept as they are.

Boundary record: links[8], u16 section, u16 n, f32 bbox[4] (min x, min y,
max x, max y), then ``n + 1`` points (f32 x, y). The bbox is exactly the
bounds of all ``n + 1`` points in every shipped record, so the stored count is
one less than the number of points.

Visible-list record (164 bytes): links[8], u16 section, u16, u32, u32 count,
u16 sections[72].

The game finds the drivable section the car is in from the boundaries and
streams that section's visible list.
"""
import struct

MANAGER = 0x80034150
HEADER = 0x00034151
BOUNDARIES = 0x00034152
VISIBLE = 0x00034153
UNK54 = 0x00034154
LOADING = 0x00034155
VIS_RECORD = 164
VIS_MAX = 72
DEFAULT_LINKS = b'\x0b\x00\x00\x00\x0b\x00\x00\x00'


class Boundary:
    __slots__ = ('links', 'section', 'points')

    def __init__(self, section, points, links=DEFAULT_LINKS):
        self.section, self.points, self.links = section, [tuple(p) for p in points], links

    def bbox(self):
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return (min(xs), min(ys), max(xs), max(ys))

    def pack(self):
        return (self.links + struct.pack('<HH4f', self.section, len(self.points) - 1, *self.bbox())
                + b''.join(struct.pack('<2f', *p) for p in self.points))

    def contains(self, x, y):
        inside = False
        pts = self.points
        j = len(pts) - 1
        for i in range(len(pts)):
            xi, yi = pts[i]
            xj, yj = pts[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        return inside


class VisibleList:
    __slots__ = ('raw', 'section', 'sections')

    def __init__(self, raw):
        self.raw = bytearray(raw)
        self.section, = struct.unpack_from('<H', raw, 8)
        n, = struct.unpack_from('<I', raw, 16)
        self.sections = list(struct.unpack_from('<%dH' % n, raw, 20))

    def pack(self):
        if len(self.sections) > VIS_MAX:
            raise ValueError('visible list of %d is full' % self.section)
        r = bytearray(self.raw)
        struct.pack_into('<I', r, 16, len(self.sections))
        r[20:VIS_RECORD] = struct.pack('<%dH' % len(self.sections), *self.sections).ljust(VIS_RECORD - 20, b'\0')
        return bytes(r)


class VisibilityManager:
    def __init__(self, payload):
        self.order = []
        self.raw = {}
        self.boundaries = []
        self.visible = []
        off = 0
        while off + 8 <= len(payload):
            cid, size = struct.unpack_from('<II', payload, off)
            d = bytes(payload[off + 8:off + 8 + size])
            self.order.append(cid)
            if cid == BOUNDARIES:
                q = 0
                while q < size:
                    sec, n = struct.unpack_from('<HH', d, q + 8)
                    pts = [struct.unpack_from('<2f', d, q + 28 + 8 * i) for i in range(n + 1)]
                    b = Boundary(sec, pts, d[q:q + 8])
                    self.boundaries.append(b)
                    q += 28 + 8 * (n + 1)
            elif cid == VISIBLE:
                self.visible = [VisibleList(d[q:q + VIS_RECORD]) for q in range(0, size, VIS_RECORD)]
                if size % VIS_RECORD:
                    raise ValueError('visible list chunk not a multiple of 164')
            self.raw[cid] = d
            off += 8 + size
        if off != len(payload):
            raise ValueError('trailing bytes in visibility manager')

    def payload(self):
        out = bytearray()
        for cid in self.order:
            if cid == BOUNDARIES:
                d = b''.join(b.pack() for b in self.boundaries)
            elif cid == VISIBLE:
                d = b''.join(v.pack() for v in self.visible)
            else:
                d = self.raw[cid]
            out += struct.pack('<II', cid, len(d)) + d
        return bytes(out)

    def to_bytes(self):
        p = self.payload()
        return struct.pack('<II', MANAGER, len(p)) + p

    # -- queries
    def drivable_at(self, x, y):
        """Section numbers of all drivable boundaries containing (x, y) (file order)."""
        live = {v.section for v in self.visible}
        return [b.section for b in self.boundaries if b.section in live and b.contains(x, y)]

    def boundary(self, section):
        for b in self.boundaries:
            if b.section == section:
                return b
        return None

    def visible_list(self, section):
        for v in self.visible:
            if v.section == section:
                return v
        return None

    def loading_sections(self):
        """[(name, drivable list, extra list)] from 0x34155 (read only)."""
        d = self.raw.get(LOADING, b'')
        out = []
        for q in range(0, len(d) - 75, 76):
            name = d[q + 8:q + 24].split(b'\0')[0].decode('ascii', 'replace')
            n1, = struct.unpack_from('<H', d, q + 24)
            l1 = struct.unpack_from('<%dH' % n1, d, q + 26)
            n2, = struct.unpack_from('<H', d, q + 58)
            l2 = struct.unpack_from('<%dH' % n2, d, q + 60)
            out.append((name, list(l1), list(l2)))
        return out


def find_manager(l4):
    """(chunk offset, total size) of the visibility manager in an L4 buffer."""
    from .chunks import iter_chunks
    for cid, d, s in iter_chunks(l4, 0, len(l4)):
        if cid == MANAGER:
            return d - 8, s + 8
    return None
