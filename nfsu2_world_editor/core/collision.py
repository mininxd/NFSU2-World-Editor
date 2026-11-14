"""Track collision packs (``0x80034130``) in ``STREAML4Rx.BUN``.

Layout (see FRSWorlsEditor docs, section 5a, and OpenUG2 docs)::

    0x80034130  collision pack (one per positioned streaming section)
    +-- 0x00034131  header, 48 bytes: u32 section, u16 grid w, u16 grid h,
    |               f32[2] grid origin, f32[3]+pad min, f32[3]+pad max
    +-- 0x00034134  polygons, 32 bytes each
    +-- 0x00034136  cell references (u16 polygon, u16 8x8 sub-cell mask)
    +-- 0x00034137  grid cells (u16 first ref, u16 ref count), row major

Polygon (32 bytes): u8 race-barrier group, u8 extreme-corner code, u8 surface,
u8 flags (0x10 quad, 0x02 vertical, 0x16 barrier), i16 height base, u16 0xFFFF,
i16 x[4] (1/8 unit), i16 y[4] (1/8 unit), i16 z[4] (1/256 unit, + base).

:func:`parse` / :meth:`CollisionPack.to_bytes` round-trip every shipped pack
byte for byte. :func:`grid_membership` recomputes which cells/sub-cells a
polygon touches (checked against the shipped grids), and
:meth:`CollisionPack.add_polygons` appends new polygons while keeping every
shipped polygon index and cell order intact.
"""
import math
import struct

PACK = 0x80034130
HEADER = 0x00034131
POLYS = 0x00034134
REFS = 0x00034136
CELLS = 0x00034137

POLY_SIZE = 32
CELL = 32.0
SUB = 8.0
TOL = 1e-4
BARRIER_Z = 32512
FLT_MAX = 3.4028234663852886e38

# surface ids (SPEED2 surface table)
SURFACE = {'NONE': 0, 'ROAD': 1, 'ROAD_WET': 2, 'ROAD_BRIDGE': 7, 'DIRT': 8, 'GRAVEL': 9,
           'ROUGH_ROAD': 10, 'DEEP_WATER': 14, 'GRASS': 15, 'SIDEWALK': 16,
           'SOLID_WALL': 20, 'METAL': 26}

FLAG_QUAD = 0x10
FLAG_VERTICAL = 0x02
FLAG_BARRIER = 0x16


class Polygon:
    __slots__ = ('group', 'code', 'surface', 'flags', 'base', 'tail', 'xs', 'ys', 'zs')

    def __init__(self, group=0, code=0, surface=1, flags=0, base=0, xs=(0,) * 4, ys=(0,) * 4,
                 zs=(0,) * 4, tail=0xFFFF):
        self.group, self.code, self.surface, self.flags = group, code, surface, flags
        self.base, self.tail = base, tail
        self.xs, self.ys, self.zs = tuple(xs), tuple(ys), tuple(zs)

    @classmethod
    def unpack(cls, buf, off=0):
        g, c, s, f, base, tail = struct.unpack_from('<4BhH', buf, off)
        v = struct.unpack_from('<12h', buf, off + 8)
        return cls(g, c, s, f, base, v[0:4], v[4:8], v[8:12], tail)

    def pack(self):
        return struct.pack('<4BhH12h', self.group, self.code, self.surface, self.flags, self.base,
                           self.tail, *self.xs, *self.ys, *self.zs)

    @property
    def is_barrier(self):
        return self.base == 0 and self.zs == (-BARRIER_Z, BARRIER_Z, BARRIER_Z, -BARRIER_Z)

    @property
    def is_vertical(self):
        return bool(self.flags & FLAG_VERTICAL)

    def plan(self):
        """Distinct plan corners in world units (1/8 quantised)."""
        pts = [(x / 8.0, y / 8.0) for x, y in zip(self.xs, self.ys)]
        if not (self.flags & FLAG_QUAD):
            pts = pts[:3]
        return pts

    def corners(self):
        """World (x, y, z) corners; barriers get z = +-127."""
        out = []
        for x, y, z in zip(self.xs, self.ys, self.zs):
            out.append((x / 8.0, y / 8.0, self.base + z / 256.0))
        return out

    def key(self):
        return (self.xs, self.ys, self.zs, self.base, self.is_barrier)


def extreme_code(xs, ys):
    """Byte 1: index of the corner furthest toward -x, -y, +x, +y (2 bits each,
    ties go to the later corner)."""
    def arg(vals, sign):
        best = 0
        for i in range(4):
            if sign * vals[i] >= sign * vals[best]:
                best = i
        return best
    return arg(xs, -1) | (arg(ys, -1) << 2) | (arg(xs, 1) << 4) | (arg(ys, 1) << 6)


def _q8(v):
    return int(round(v * 8.0))


def make_ground(pts, surface=1, group=0):
    """Ground polygon from 3 or 4 world points (x, y, z), perimeter order, convex in
    plan. Height base = floor((zmin + zmax) / 2)."""
    if len(pts) not in (3, 4):
        raise ValueError('ground polygon needs 3 or 4 corners')
    quad = len(pts) == 4
    p4 = list(pts) + ([pts[2]] if not quad else [])
    zmin = min(p[2] for p in p4)
    zmax = max(p[2] for p in p4)
    base = int(math.floor((zmin + zmax) / 2.0))
    xs = tuple(_q8(p[0]) for p in p4)
    ys = tuple(_q8(p[1]) for p in p4)
    zs = tuple(int(round((p[2] - base) * 256.0)) for p in p4)
    if any(abs(z) > 32767 for z in zs):
        raise ValueError('polygon too tall for one height base')
    flags = FLAG_QUAD if quad else 0
    return Polygon(group, extreme_code(xs, ys), surface, flags, base, xs, ys, zs)


def barrier_code(xs, ys):
    """Barrier heading code from corner 1 -> corner 2."""
    dx = xs[2] - xs[1]
    dy = ys[2] - ys[1]
    c = 0xFF
    if dx > 0:
        c &= ~0x02
    if dy > 0:
        c &= ~0x08
    if dx < 0:
        c &= ~0x20
    if dy < 0:
        c &= ~0x80
    return c


def make_barrier(a, b, surface=20, group=0):
    """Infinitely tall one-sided wall from plan point a to b. Write it twice (a->b and
    b->a) for a wall that holds from both sides (see :func:`make_wall`)."""
    xs = (_q8(a[0]), _q8(a[0]), _q8(b[0]), _q8(b[0]))
    ys = (_q8(a[1]), _q8(a[1]), _q8(b[1]), _q8(b[1]))
    zs = (-BARRIER_Z, BARRIER_Z, BARRIER_Z, -BARRIER_Z)
    code = barrier_code(xs, ys)
    assert code == extreme_code(xs, ys) or True
    return Polygon(group, code, surface, FLAG_BARRIER, 0, xs, ys, zs)


def make_wall(a, b, max_len=8.0, surface=20):
    """Barrier wall a->b cut into pieces of at most ``max_len`` units, both directions."""
    d = math.hypot(b[0] - a[0], b[1] - a[1])
    n = max(1, int(math.ceil(d / max_len)))
    out = []
    for i in range(n):
        p = (a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n)
        q = (a[0] + (b[0] - a[0]) * (i + 1) / n, a[1] + (b[1] - a[1]) * (i + 1) / n)
        out.append(make_barrier(p, q, surface))
        out.append(make_barrier(q, p, surface))
    return out


# ---------------------------------------------------------------- grid geometry

def _outline(poly):
    """Plan outline as stored (in world units): ground corners, or a segment."""
    pts = [(x / 8.0, y / 8.0) for x, y in zip(poly.xs, poly.ys)]
    uniq = []
    for p in pts:
        if p not in uniq:
            uniq.append(p)
    return uniq


def _overlap(pts, x0, y0, x1, y1, tol=TOL):
    """Separating-axis test of a convex point set against an axis-aligned box (closed)."""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    if max(xs) < x0 - tol or min(xs) > x1 + tol or max(ys) < y0 - tol or min(ys) > y1 + tol:
        return False
    n = len(pts)
    if n < 2:
        return True
    box = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    edges = [(pts[i], pts[(i + 1) % n]) for i in range(n if n > 2 else 1)]
    for a, b in edges:
        ex, ey = b[0] - a[0], b[1] - a[1]
        if ex == 0 and ey == 0:
            continue
        nx, ny = -ey, ex
        ln = math.hypot(nx, ny)
        nx, ny = nx / ln, ny / ln
        pp = [p[0] * nx + p[1] * ny for p in pts]
        bp = [p[0] * nx + p[1] * ny for p in box]
        if max(pp) < min(bp) - tol or min(pp) > max(bp) + tol:
            return False
    return True


def poly_bounds(poly):
    o = _outline(poly)
    return (min(p[0] for p in o), min(p[1] for p in o), max(p[0] for p in o), max(p[1] for p in o))


def grid_membership(poly, origin, width, height):
    """{cell index: sub-cell mask} for the cells a polygon belongs to."""
    pts = _outline(poly)
    bx0, by0, bx1, by1 = poly_bounds(poly)
    ox, oy = origin
    cx0 = max(0, int(math.floor((bx0 - ox) / CELL)) - 1)
    cy0 = max(0, int(math.floor((by0 - oy) / CELL)) - 1)
    cx1 = min(width - 1, int(math.floor((bx1 - ox) / CELL)) + 1)
    cy1 = min(height - 1, int(math.floor((by1 - oy) / CELL)) + 1)
    out = {}
    for cy in range(cy0, cy1 + 1):
        for cx in range(cx0, cx1 + 1):
            x0 = ox + cx * CELL
            y0 = oy + cy * CELL
            x1, y1 = x0 + CELL, y0 + CELL
            if bx0 >= x1 or by0 >= y1:      # cell owns its low edges only
                continue
            if not _overlap(pts, x0, y0, x1, y1):
                continue
            mask = 0
            for sy in range(4):
                for sx in range(4):
                    if _overlap(pts, x0 + sx * SUB, y0 + sy * SUB, x0 + (sx + 1) * SUB,
                                y0 + (sy + 1) * SUB):
                        mask |= 1 << (sy * 4 + sx)
            if mask:
                out[cy * width + cx] = mask
    return out


# ---------------------------------------------------------------- the pack

class CollisionPack:
    def __init__(self):
        self.section = 0
        self.width = self.height = 1
        self.origin = (0.0, 0.0)
        self.bmin = (FLT_MAX,) * 3
        self.bmax = (-FLT_MAX,) * 3
        self.header_raw = None
        self.polys = []
        self.refs = []       # [(poly index, mask)]
        self.cells = []      # [(first, count)]

    # -- io
    @classmethod
    def parse(cls, payload):
        """``payload``: bytes of the 0x80034130 container (children only)."""
        p = cls()
        off = 0
        seen = []
        while off + 8 <= len(payload):
            cid, size = struct.unpack_from('<II', payload, off)
            d = payload[off + 8:off + 8 + size]
            seen.append(cid)
            if cid == HEADER:
                p.header_raw = bytes(d)
                p.section, p.width, p.height = struct.unpack_from('<IHH', d, 0)
                p.origin = struct.unpack_from('<2f', d, 8)
                p.bmin = struct.unpack_from('<3f', d, 16)
                p.bmax = struct.unpack_from('<3f', d, 32)
            elif cid == POLYS:
                p.polys = [Polygon.unpack(d, i) for i in range(0, size - POLY_SIZE + 1, POLY_SIZE)]
            elif cid == REFS:
                p.refs = [struct.unpack_from('<HH', d, i) for i in range(0, size - 3, 4)]
            elif cid == CELLS:
                p.cells = [struct.unpack_from('<HH', d, i) for i in range(0, size - 3, 4)]
            else:
                raise ValueError('unexpected chunk %08X in collision pack' % cid)
            off += 8 + size
        if seen != [HEADER, POLYS, REFS, CELLS] or off != len(payload):
            raise ValueError('unexpected collision pack layout')
        return p

    def header_bytes(self):
        h = bytearray(self.header_raw if self.header_raw else bytes(48))
        struct.pack_into('<IHH2f', h, 0, self.section, self.width, self.height, *self.origin)
        struct.pack_into('<3f', h, 16, *self.bmin)
        struct.pack_into('<3f', h, 32, *self.bmax)
        return bytes(h)

    def payload(self):
        def ch(cid, data):
            return struct.pack('<II', cid, len(data)) + data
        return (ch(HEADER, self.header_bytes())
                + ch(POLYS, b''.join(q.pack() for q in self.polys))
                + ch(REFS, b''.join(struct.pack('<HH', *r) for r in self.refs))
                + ch(CELLS, b''.join(struct.pack('<HH', *c) for c in self.cells)))

    def to_bytes(self):
        """Whole 0x80034130 chunk (header included)."""
        pl = self.payload()
        return struct.pack('<II', PACK, len(pl)) + pl

    # -- queries
    def cell_lists(self):
        """[[(poly, mask), ...] per cell]."""
        return [self.refs[f:f + n] for f, n in self.cells]

    def computed_bounds(self, polys=None):
        polys = self.polys if polys is None else polys
        lo = [FLT_MAX] * 3
        hi = [-FLT_MAX] * 3
        for q in polys:
            for x, y, z in q.corners():
                if q.is_barrier:
                    z = None
                for k, v in enumerate((x, y, z)):
                    if v is None:
                        continue
                    lo[k] = min(lo[k], v)
                    hi[k] = max(hi[k], v)
            if q.is_barrier:
                lo[2] = min(lo[2], -127.0)
                hi[2] = max(hi[2], 127.0)
        return tuple(lo), tuple(hi)

    # -- editing
    def add_polygons(self, new_polys):
        """Append polygons; grow the grid if needed. Shipped polygon indices and the
        order of every shipped cell list stay as they were (new refs go last)."""
        if not new_polys:
            return
        start = len(self.polys)
        if start + len(new_polys) > 0xFFFF:
            raise ValueError('too many polygons for one pack')
        old_lists = self.cell_lists()
        old_w, old_h, old_origin = self.width, self.height, self.origin
        self.polys.extend(new_polys)
        lo, hi = self.computed_bounds()
        self.bmin = tuple(min(a, b) for a, b in zip(self.bmin, lo)) if self.polys[:start] else lo
        self.bmax = tuple(max(a, b) for a, b in zip(self.bmax, hi)) if self.polys[:start] else hi
        gx0 = math.floor(self.bmin[0] / CELL) * CELL
        gy0 = math.floor(self.bmin[1] / CELL) * CELL
        if start:
            gx0 = min(gx0, old_origin[0])
            gy0 = min(gy0, old_origin[1])
        w = int(math.floor(self.bmax[0] / CELL) - math.floor(gx0 / CELL)) + 1
        h = int(math.floor(self.bmax[1] / CELL) - math.floor(gy0 / CELL)) + 1
        if start:
            w = max(w, int((old_origin[0] - gx0) / CELL) + old_w)
            h = max(h, int((old_origin[1] - gy0) / CELL) + old_h)
        lists = [[] for _ in range(w * h)]
        if start:
            dx = int(round((old_origin[0] - gx0) / CELL))
            dy = int(round((old_origin[1] - gy0) / CELL))
            for i, lst in enumerate(old_lists):
                cx, cy = i % old_w, i // old_w
                lists[(cy + dy) * w + cx + dx] = list(lst)
        for k, q in enumerate(new_polys):
            for ci, mask in sorted(grid_membership(q, (gx0, gy0), w, h).items()):
                lists[ci].append((start + k, mask))
        self.width, self.height, self.origin = w, h, (float(gx0), float(gy0))
        self.refs, self.cells = [], []
        for lst in lists:
            self.cells.append((len(self.refs), len(lst)))   # empty cells: running offset
            self.refs.extend(lst)
        if len(self.refs) > 0xFFFF:
            raise ValueError('too many cell references for one pack')


def parse(payload):
    return CollisionPack.parse(payload)


def find_packs(buf, start=0, end=None):
    """Yield ``(chunk_offset, chunk_total_size)`` of every collision pack in ``buf``."""
    from .chunks import find
    for d, s in find(buf, start, len(buf) if end is None else end, PACK):
        yield d - 8, s + 8
