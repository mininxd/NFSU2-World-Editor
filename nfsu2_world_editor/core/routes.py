"""Free-roam / event road graph files (``TRACKS/ROUTES<region>/Routes*.bin``).

Leaves (all little-endian, no containers):

* ``0x34121`` route records, back to back.  Each record is
  ``links[8] | header[128] | point[56] * n | tail[4]`` (140 + 56n bytes); the
  header holds ``u16 type, u16 route_id`` at +8, a 32-byte name at +16 and the
  point count twice at +52/+54.  Point k is the 56-byte slot at
  136 + 56k; it stores world ``f32 X, Y, Z`` at +4/+8/+12 and two ``u16``
  widths (1/256 m) at +24/+26.  The remaining point fields are packed and kept opaque.
* ``0x34122`` connections: ``u32 0`` then 32-byte records (primary point/route
  pairs at +4..+10, secondary pairs at +24..+30).
* ``0x34123``: 276-byte records, kept raw.

Everything is preserved byte-for-byte; only coordinates are interpreted.
Writing new routes is intentionally not offered: the packed point fields are
not decoded, so a synthesised record could break traffic/AI in game.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

ROUTES = 0x34121
CONNECTIONS = 0x34122
EXTRA = 0x34123
_HDR = 136
_PT = 56


@dataclass
class Route:
    raw_header: bytes          # 136 bytes (links + header)
    points: list               # list of 56-byte slots (raw)
    tail: bytes                # 4 bytes

    @property
    def type(self):
        return struct.unpack_from('<H', self.raw_header, 8)[0]

    @property
    def route_id(self):
        return struct.unpack_from('<H', self.raw_header, 10)[0]

    @property
    def name(self):
        return self.raw_header[16:48].split(b'\0')[0].decode('latin-1')

    def xyz(self, k):
        return struct.unpack_from('<fff', self.points[k], 4)

    def widths(self, k):
        a, b = struct.unpack_from('<HH', self.points[k], 24)
        return a / 256.0, b / 256.0

    def to_bytes(self):
        return self.raw_header + b''.join(self.points) + self.tail


def parse_routes(body: bytes):
    out, o = [], 0
    while o < len(body):
        if o + 140 > len(body):
            raise ValueError('truncated route record at %d' % o)
        n, n2 = struct.unpack_from('<HH', body, o + 52)
        if n != n2:
            raise ValueError('route %d point counts differ (%d/%d)' % (o, n, n2))
        end = o + 140 + _PT * n
        if end > len(body):
            raise ValueError('route record overruns leaf at %d' % o)
        hdr = body[o:o + _HDR]
        pts = [body[o + _HDR + _PT * k:o + _HDR + _PT * (k + 1)] for k in range(n)]
        out.append(Route(hdr, pts, body[end - 4:end]))
        o = end
    return out


@dataclass
class Connection:
    raw: bytes

    @property
    def primary(self):
        pa, pb, ra, rb = struct.unpack_from('<4H', self.raw, 4)
        return (ra, pa), (rb, pb)

    @property
    def secondary(self):
        ra, rb, pa, pb = struct.unpack_from('<4H', self.raw, 24)
        if ra == 0xFFFF:
            return None
        return (ra, pa), (rb, pb)


@dataclass
class RoutesFile:
    leaves: list = field(default_factory=list)   # [(id, payload-object-or-bytes)]

    @classmethod
    def parse(cls, data: bytes):
        f, o = cls(), 0
        while o < len(data):
            cid, size = struct.unpack_from('<II', data, o)
            body = data[o + 8:o + 8 + size]
            if len(body) != size:
                raise ValueError('truncated chunk 0x%x' % cid)
            if cid == ROUTES:
                f.leaves.append((cid, parse_routes(body)))
            elif cid == CONNECTIONS and size % 32 == 4:
                f.leaves.append((cid, (body[:4], [Connection(body[4 + 32 * i:36 + 32 * i])
                                                  for i in range((size - 4) // 32)])))
            else:
                f.leaves.append((cid, body))
            o += 8 + size
        return f

    def to_bytes(self):
        out = bytearray()
        for cid, v in self.leaves:
            if cid == ROUTES and isinstance(v, list):
                body = b''.join(r.to_bytes() for r in v)
            elif cid == CONNECTIONS and isinstance(v, tuple):
                body = v[0] + b''.join(c.raw for c in v[1])
            else:
                body = v
            out += struct.pack('<II', cid, len(body)) + body
        return bytes(out)

    @property
    def routes(self):
        for cid, v in self.leaves:
            if cid == ROUTES and isinstance(v, list):
                return v
        return []

    @property
    def connections(self):
        for cid, v in self.leaves:
            if cid == CONNECTIONS and isinstance(v, tuple):
                return v[1]
        return []

    def nearest_point(self, x, y):
        best = None
        for r in self.routes:
            for k in range(len(r.points)):
                px, py, _ = r.xyz(k)
                d = (px - x) ** 2 + (py - y) ** 2
                if best is None or d < best[0]:
                    best = (d, r.route_id, k)
        return None if best is None else (best[0] ** 0.5, best[1], best[2])
