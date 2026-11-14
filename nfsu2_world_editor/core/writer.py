"""Minimal chunk writer.

Used by the unit tests to synthesise game-like data, and the starting point for
writing edited meshes back into STREAML4Rx.BUN.
"""
import struct

from .chunks import ALIGN, DEFAULT_ALIGN, FILLER
from .world import (SOLID_OBJECT, SOLID_HEADER, SOLID_TEXLIST, SOLID_MESH, MESH_HEADER,
                    MESH_VERTS, MESH_MATS, MESH_INDICES, MATERIAL_SIZE, SECTION_TABLE,
                    SECTION_ENTRY_SIZE, SCENERY_PACK, SCENERY_INFOS, SCENERY_INSTANCES,
                    SCENERY_INFO_SIZE, SCENERY_INSTANCE_SIZE)

IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


class ChunkWriter:
    """Appends chunks to a bytearray. Containers are opened with ``begin`` and closed
    with ``end``; sizes are patched in afterwards. ``base`` is the absolute file
    offset the buffer will start at (alignment filler depends on it)."""

    def __init__(self, base=0):
        self.buf = bytearray()
        self.base = base
        self._stack = []

    def tell(self):
        return self.base + len(self.buf)

    def raw(self, data):
        self.buf += data

    def begin(self, cid):
        self.buf += struct.pack('<II', cid, 0)
        self._stack.append(len(self.buf))

    def end(self):
        start = self._stack.pop()
        struct.pack_into('<I', self.buf, start - 4, len(self.buf) - start)

    def leaf(self, cid, payload, align=False):
        """Write a data chunk. With ``align`` the payload is preceded by 0x11 filler so
        it starts at the alignment the reader expects for ``cid``."""
        self.buf += struct.pack('<II', cid, 0)
        start = len(self.buf)
        if align:
            a = ALIGN.get(cid, DEFAULT_ALIGN)
            self.buf += bytes([FILLER]) * ((-(self.base + start)) % a)
        self.buf += payload
        struct.pack_into('<I', self.buf, start - 4, len(self.buf) - start)

    def getvalue(self):
        assert not self._stack, 'unclosed container'
        return bytes(self.buf)


def write_solid_object(w, name, verts, faces, uvs=None, cols=None, normals=None,
                       tex_hashes=(0,), materials=None, model_hash=0, matrix=IDENTITY):
    """Write a SolidObject container. ``materials`` is a list of
    ``(first_index, index_count, tex_slot)``; default: one material over all faces."""
    n = len(verts)
    uvs = uvs or [(0.0, 0.0)] * n
    cols = cols or [(255, 255, 255, 255)] * n
    idx = [i for f in faces for i in f]
    if materials is None:
        materials = [(0, len(idx), 0)]
    w.begin(SOLID_OBJECT)
    hdr = bytearray(0xA4)
    struct.pack_into('<I', hdr, 0x10, model_hash)
    struct.pack_into('<I', hdr, 0x14, len(faces))
    hdr[0x19] = len(tex_hashes) & 0xFF
    struct.pack_into('<16f', hdr, 0x40, *matrix)
    w.leaf(SOLID_HEADER, bytes(hdr) + name.encode('ascii') + b'\0', align=True)
    w.leaf(SOLID_TEXLIST, b''.join(struct.pack('<II', h, 0) for h in tex_hashes), align=True)
    w.begin(SOLID_MESH)
    mh = bytearray(0x38)
    struct.pack_into('<I', mh, 0x10, len(materials))
    struct.pack_into('<I', mh, 0x24, len(faces))
    struct.pack_into('<I', mh, 0x34, n)
    w.leaf(MESH_HEADER, bytes(mh), align=True)
    vb = bytearray()
    for i, (x, y, z) in enumerate(verts):
        r, g, b, a = cols[i]
        argb = (a << 24) | (r << 16) | (g << 8) | b
        if normals is None:
            vb += struct.pack('<3fI2f', x, y, z, argb, *uvs[i])
        else:
            vb += struct.pack('<6fI2f', x, y, z, *normals[i], argb, *uvs[i])
    w.leaf(MESH_VERTS, bytes(vb), align=True)
    mb = bytearray()
    for first, cnt, slot in materials:
        m = bytearray(MATERIAL_SIZE)
        struct.pack_into('<I', m, 0x0C, cnt)
        struct.pack_into('<I', m, 0x1C, slot)
        struct.pack_into('<I', m, 0x34, first)
        mb += m
    w.leaf(MESH_MATS, bytes(mb), align=True)
    w.leaf(MESH_INDICES, struct.pack('<%dH' % len(idx), *idx), align=True)
    w.end()
    w.end()


def section_table_payload(entries):
    """``entries``: dicts with tag, num, offset, size, geom_size, order, center."""
    out = bytearray()
    for e in entries:
        b = bytearray(SECTION_ENTRY_SIZE)
        tag = e['tag'].encode('ascii')[:7]
        b[0:len(tag)] = tag
        struct.pack_into('<I', b, 8, e.get('num', 0))
        struct.pack_into('<4I', b, 0x14, e['offset'], e['size'], e.get('geom_size', e['size']),
                         e.get('order', 0))
        struct.pack_into('<3f', b, 0x24, *e.get('center', (0.0, 0.0, 0.0)))
        out += b
    return bytes(out)


def write_section_table(w, entries):
    w.leaf(SECTION_TABLE, section_table_payload(entries))


def write_scenery_pack(w, model_hashes, instances):
    """``instances``: list of ``(info_index, (tx,ty,tz), rot9)`` where rot9 are floats
    (stored as int16 * 1/8192)."""
    w.begin(SCENERY_PACK)
    infos = bytearray()
    for h in model_hashes:
        e = bytearray(SCENERY_INFO_SIZE)
        struct.pack_into('<I', e, 0x20, h)
        infos += e
    w.leaf(SCENERY_INFOS, bytes(infos))
    recs = bytearray()
    for ii, t, rot in instances:
        r = bytearray(SCENERY_INSTANCE_SIZE)
        struct.pack_into('<H', r, 0x18, ii)
        struct.pack_into('<3f', r, 0x20, *t)
        struct.pack_into('<9h', r, 0x2C, *[int(round(x * 8192)) for x in rot])
        recs += r
    w.leaf(SCENERY_INSTANCES, bytes(recs), align=True)
    w.end()


def write_texture_pack(w, textures, name='TRACK', path='Location4\\Test.tpk'):
    """Write a ``0xB3300000`` texture pack. ``textures``: dicts with name, hash,
    format (D3DFORMAT / FourCC), width, height, mips, data (all mip levels) and
    optional palette (1024 bytes, P8), usage, blend. Textures are sorted by hash
    like the shipped packs; palettes are stored first in the pixel block."""
    from .tpk import (TPK, TPK_INFO, TPK_NAME, TPK_HASHES, TPK_HEADERS, TPK_FORMATS, TPK_DATA,
                      TPK_PIXELS, HEADER_SIZE, FORMAT_SIZE)
    texs = sorted(textures, key=lambda t: t['hash'])
    pixels = bytearray()
    pal_off = {}
    for i, t in enumerate(texs):
        if t.get('palette'):
            pal_off[i] = len(pixels)
            pixels += t['palette']
    hashes, headers, formats = bytearray(), bytearray(), bytearray()
    for i, t in enumerate(texs):
        off = len(pixels)
        pixels += t['data']
        h = bytearray(HEADER_SIZE)
        nm = t['name'].encode('ascii')[:23]
        h[0x0C:0x0C + len(nm)] = nm
        struct.pack_into('<IIIIIIII', h, 0x24, t['hash'], 0x1A93CF, 0, off, pal_off.get(i, 0),
                         len(t['data']), 1024 if i in pal_off else 0, 0)
        struct.pack_into('<HH', h, 0x44, t['width'], t['height'])
        h[0x4E] = t.get('mips', 1)
        h[0x55] = t.get('usage', 0)
        h[0x56] = t.get('blend', 0)
        headers += h
        f = bytearray(FORMAT_SIZE)
        struct.pack_into('<I', f, 0x14, t['format'])
        formats += f
        hashes += struct.pack('<II', t['hash'], 0)
    w.begin(TPK)
    w.begin(TPK_INFO)
    nmb = bytearray(0x7C)
    nmb[4:4 + len(name)] = name.encode('ascii')
    nmb[0x20:0x20 + len(path)] = path.encode('ascii')
    w.leaf(TPK_NAME, bytes(nmb))
    w.leaf(TPK_HASHES, bytes(hashes))
    w.leaf(TPK_HEADERS, bytes(headers))
    w.leaf(TPK_FORMATS, bytes(formats))
    w.end()
    w.begin(TPK_DATA)
    w.leaf(0x33320001, bytes(24))
    w.leaf(TPK_PIXELS, bytes(pixels), align=True)
    w.end()
    w.end()
