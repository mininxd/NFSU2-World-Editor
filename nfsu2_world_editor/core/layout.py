"""Layout rules every shipped streaming file follows, as a checker for edited files.

Measured on all 107 046 chunks of STREAML4RA.BUN (and holding for the other
regions):

* chunk ids with a fixed header position modulo 16 (``HDR16``) - e.g. a solid
  object's texture list ``0x134012`` always starts on 16 bytes because the
  192-byte object header before it is padded with 0x11 filler;
* top-level packs that always start on 128 bytes (``stream.TOP_ALIGN``);
* solid object headers (``0x134011``) are 192 bytes after their filler;
* the objects of a solid list are sorted by hash, like its (hash, pointer) index;
* chunks whose payload SPEED2.EXE locates with ``(header + 0x17) & ~0xF``
  (``GAME_REALIGN``: solid object header, vertex/index buffers, the 0x80135000
  pack's children, the collision header) carry exactly the 0x11 filler the game
  skips. Found in the 1.2 executable's loaders (0x493BEC, 0x5CC2E6, 0x48E1DF,
  0x5D8169): if the filler is shorter, or a whole 16 bytes longer, the game
  reads the structure from the wrong address.

:func:`violations` returns readable problems (empty list = layout OK).
"""
import struct

from .stream import TOP_ALIGN

HDR16 = {
    0x80134001: 0, 0x134002: 8, 0x134003: 0, 0x134012: 0, 0x134B01: 4,
    0x80034100: 0, 0x34101: 8, 0x34103: 12, 0x34102: 0,
    0xB3300000: 0, 0xB3310000: 0, 0x33310001: 8, 0x33310002: 12, 0xB3320000: 0, 0x33320001: 8,
    0x33320002: 0, 0xB0300100: 0, 0x30300101: 8, 0x30300102: 0,
    0x80034130: 0, 0x34131: 8, 0x34134: 0, 0x34136: 8,
    0x80135000: 0, 0x135001: 8, 0x135002: 0, 0x135003: 0, 0x80135100: 0, 0x135101: 8, 0x135102: 0,
    0x80036000: 0, 0x36001: 8, 0x36002: 0, 0x36003: 0, 0x34027: 0, 0x35020: 0,
}
FILLER = 0x11
HEADER_SIZE = 192
GAME_REALIGN = {0x134011, 0x134B02, 0x134B03, 0x135001, 0x135002, 0x135003, 0x34131}


def game_skip(p):
    """Bytes of filler the game skips after a chunk header at ``p``."""
    return ((p + 0x17) & ~0xF) - (p + 8)


def violations(buf, start=0, end=None, limit=50):
    end = len(buf) if end is None else end
    out = []

    def add(msg):
        if len(out) < limit:
            out.append(msg)

    def walk(s, e, depth, parent):
        p = s
        prev_hash = None
        while p < e:
            if p + 8 > e:
                add('truncated chunk header at 0x%X' % p)
                return
            cid, size = struct.unpack_from('<II', buf, p)
            if p + 8 + size > e:
                add('chunk 0x%08X at 0x%X overruns its parent' % (cid, p))
                return
            r = HDR16.get(cid)
            if r is not None and p % 16 != r:
                add('chunk 0x%08X at 0x%X: header offset mod 16 = %d, shipped files always %d' % (cid, p, p % 16, r))
            if depth == 0 and cid in TOP_ALIGN:
                m, rr = TOP_ALIGN[cid]
                if p % m != rr:
                    add('pack 0x%08X at 0x%X not on its %d-byte boundary' % (cid, p, m))
            if cid in GAME_REALIGN:
                skip = game_skip(p)
                n = 0
                while n < size and n < 64 and buf[p + 8 + n] == FILLER:
                    n += 1
                if n < skip or n >= skip + 16:
                    add('chunk 0x%08X at 0x%X: %d filler bytes but the game skips %d' % (cid, p, n, skip))
            if cid == 0x134011:
                n = 0
                while n < size and buf[p + 8 + n] == FILLER and n < 16:
                    n += 1
                if size - n != HEADER_SIZE:
                    add('solid object header at 0x%X is %d bytes (shipped: %d)' % (p, size - n, HEADER_SIZE))
            if parent == 0x80134000 and cid == 0x80134010:
                q = p + 8
                if struct.unpack_from('<I', buf, q)[0] == 0x134011:
                    hs, = struct.unpack_from('<I', buf, q + 4)
                    n = 0
                    while n < hs and buf[q + 8 + n] == FILLER and n < 16:
                        n += 1
                    h, = struct.unpack_from('<I', buf, q + 8 + n + 0x10)
                    if prev_hash is not None and h < prev_hash:
                        add('solid list at 0x%X: objects not sorted by hash (0x%08X after 0x%08X)' % (s, h, prev_hash))
                    prev_hash = h
            if cid & 0x80000000 and size:
                walk(p + 8, p + 8 + size, depth + 1, cid)
            p += 8 + size

    walk(start, end, 0, None)
    return out


def section_violations(stream, entries, tags=None, limit=50):
    """``{tag: [problems]}`` for the positioned sections of a region (``entries``
    as in :class:`stream.Region`), optionally only ``tags``."""
    out = {}
    for e in entries:
        if not e['size'] or e['tag'] == '--' or (tags is not None and e['tag'] not in tags):
            continue
        v = violations(stream, e['offset'], e['offset'] + e['size'], limit)
        if v:
            out[e['tag']] = v
    return out
