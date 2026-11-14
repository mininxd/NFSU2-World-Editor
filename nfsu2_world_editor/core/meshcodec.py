"""Lossless decode/encode of SolidObject meshes (``0x80134010``) for writing
edited geometry back into a streaming file.

:func:`decode` returns a :class:`MeshData` holding *everything* needed to rebuild
the mesh chunks (raw triangle list including degenerate joints, ARGB colours,
the 60-byte material records...). :func:`encode` writes a MeshData back into an
object node, regenerating header counts, bounds, vertex/material/index buffers
and the texture list, while keeping every unknown field from the original.
Decoding then encoding an untouched object reproduces its bytes exactly
(checked on all 10 735 objects of L4RA).

Measured rules the encoder follows:

* object header (0x134011): +0x14 triangle count, +0x19 texture-list length,
  +0x20/+0x30 bounds = float32(min - 0.01) / float32(max + 0.01) of all vertices;
* descriptor (0x134900): +0x10 material count, +0x24 triangles, +0x34 vertices;
* material record (0x134B02, 60 bytes): +0x00/+0x10 bounds of the vertices the
  range uses (same +-0.01), +0x0C index count, +0x1C texture slot, +0x34 first
  index; the ranges tile the index buffer;
* index buffer (0x134B03): u16 triangle list, zero-padded to 4 bytes;
* vertex buffer (0x134B01): 24-byte (pos, ARGB, uv) or 36-byte (pos, normal,
  ARGB, uv) records, 128-byte aligned (handled by :mod:`bchunk`).
"""
import struct

from . import bchunk

SOLID_OBJECT = 0x80134010
SOLID_HEADER = 0x00134011
SOLID_TEXLIST = 0x00134012
SOLID_MESH = 0x80134100
MESH_HEADER = 0x00134900
MESH_VERTS = 0x00134B01
MESH_MATS = 0x00134B02
MESH_INDICES = 0x00134B03
MATERIAL_SIZE = 60
BOUNDS_PAD = 0.01
MAX_VERTS = 65535
NAME_OFFSET = 0xA4
NAME_LEN = 28              # header name field (0xA4..0xC0); header payload is 192 bytes


def _f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]


class MeshData:
    """Plain-Python mesh in game layout.

    verts: [(x,y,z)] local space; normals: [(nx,ny,nz)] or None (24-byte stride);
    colors: [argb u32]; uvs: [(u,v)] (game convention, v down);
    indices: flat u16 triangle list; materials: [(first, count, slot, record60)];
    textures: [texture hash] (the object's 0x134012 list).
    """
    __slots__ = ('name', 'hash', 'matrix', 'verts', 'normals', 'colors', 'uvs', 'indices',
                 'materials', 'textures', 'stride')

    def __init__(self):
        self.name = ''
        self.hash = 0
        self.matrix = None
        self.verts, self.colors, self.uvs, self.indices = [], [], [], []
        self.normals = None
        self.materials, self.textures = [], []
        self.stride = 24

    def triangles(self):
        ix = self.indices
        return [tuple(ix[i:i + 3]) for i in range(0, len(ix) - 2, 3)]


def mesh_parts(obj):
    """-> dict of the object's relevant chunks (missing ones are absent)."""
    parts = {}
    for c in obj.children:
        if c.id in (SOLID_HEADER, SOLID_TEXLIST, SOLID_MESH):
            parts[c.id] = c
    m = parts.get(SOLID_MESH)
    if m is not None:
        for c in m.children:
            if c.id in (MESH_HEADER, MESH_VERTS, MESH_MATS, MESH_INDICES):
                parts.setdefault(c.id, c)
    return parts


def decode(obj):
    """Decode a ``0x80134010`` bchunk node -> MeshData, or None if it has no
    usable mesh (no mesh chunk, zero vertices or an unknown stride)."""
    p = mesh_parts(obj)
    if not all(k in p for k in (SOLID_HEADER, MESH_HEADER, MESH_VERTS, MESH_MATS, MESH_INDICES)):
        return None
    hd = bytes(p[SOLID_HEADER].data)
    dd = bytes(p[MESH_HEADER].data)
    ntri, = struct.unpack_from('<I', dd, 0x24)
    nv, = struct.unpack_from('<I', dd, 0x34)
    vb = bytes(p[MESH_VERTS].data)
    if nv == 0 or len(vb) != nv * (len(vb) // nv) or len(vb) // nv not in (24, 36):
        return None
    m = MeshData()
    m.hash, = struct.unpack_from('<I', hd, 0x10)
    m.matrix = struct.unpack_from('<16f', hd, 0x40)
    m.name = hd[0xA4:].split(b'\0')[0].decode('ascii', 'replace')
    s = m.stride = len(vb) // nv
    if s == 24:
        for i in range(nv):
            x, y, z, c, u, v = struct.unpack_from('<3fI2f', vb, i * 24)
            m.verts.append((x, y, z))
            m.colors.append(c)
            m.uvs.append((u, v))
    else:
        m.normals = []
        for i in range(nv):
            x, y, z, nx, ny, nz, c, u, v = struct.unpack_from('<6fI2f', vb, i * 36)
            m.verts.append((x, y, z))
            m.normals.append((nx, ny, nz))
            m.colors.append(c)
            m.uvs.append((u, v))
    ib = bytes(p[MESH_INDICES].data)
    n = min(ntri * 3, len(ib) // 2)
    m.indices = list(struct.unpack_from('<%dH' % n, ib, 0))
    md = bytes(p[MESH_MATS].data)
    for i in range(len(md) // MATERIAL_SIZE):
        rec = md[i * MATERIAL_SIZE:(i + 1) * MATERIAL_SIZE]
        cnt, = struct.unpack_from('<I', rec, 0x0C)
        slot, = struct.unpack_from('<I', rec, 0x1C)
        first, = struct.unpack_from('<I', rec, 0x34)
        m.materials.append((first, cnt, slot, rec))
    if SOLID_TEXLIST in p:
        tl = bytes(p[SOLID_TEXLIST].data)
        m.textures = [struct.unpack_from('<I', tl, q)[0] for q in range(0, len(tl) - 7, 8)]
    return m


def _bounds(points):
    lo = [min(v[k] for v in points) for k in range(3)]
    hi = [max(v[k] for v in points) for k in range(3)]
    # float32 arithmetic like the original tools: f32(f32(x) - f32(0.01))
    pad = _f32(BOUNDS_PAD)
    return ([_f32(_f32(x) - pad) for x in lo], [_f32(_f32(x) + pad) for x in hi])


def _set(chunk, data):
    chunk.data = bytes(data)
    chunk.dirty = True


def encode(obj, m):
    """Write MeshData ``m`` into the object node ``obj`` (in place)."""
    p = mesh_parts(obj)
    nv = len(m.verts)
    if nv == 0:
        raise ValueError('%s: mesh has no vertices' % m.name)
    if nv > MAX_VERTS:
        raise ValueError('%s: %d vertices; the game format allows at most %d per object '
                         '(split the mesh)' % (m.name, nv, MAX_VERTS))
    if len(m.indices) % 3:
        raise ValueError('%s: index count is not a multiple of 3' % m.name)
    if m.indices and max(m.indices) >= nv:
        raise ValueError('%s: index out of range' % m.name)
    ntri = len(m.indices) // 3
    normals = m.normals
    stride = 36 if normals is not None else 24
    # vertex buffer
    vb = bytearray()
    if stride == 24:
        for (x, y, z), c, (u, v) in zip(m.verts, m.colors, m.uvs):
            vb += struct.pack('<3fI2f', x, y, z, c & 0xFFFFFFFF, u, v)
    else:
        for (x, y, z), n, c, (u, v) in zip(m.verts, normals, m.colors, m.uvs):
            vb += struct.pack('<6fI2f', x, y, z, n[0], n[1], n[2], c & 0xFFFFFFFF, u, v)
    _set(p[MESH_VERTS], vb)
    # materials
    md = bytearray()
    for first, cnt, slot, rec in m.materials:
        r = bytearray(rec if rec is not None else bytes(MATERIAL_SIZE))
        used = [m.verts[i] for i in m.indices[first:first + cnt]]
        if used:
            lo, hi = _bounds(used)
            struct.pack_into('<3f', r, 0x00, *lo)
            struct.pack_into('<3f', r, 0x10, *hi)
        struct.pack_into('<I', r, 0x0C, cnt)
        struct.pack_into('<I', r, 0x1C, slot)
        struct.pack_into('<I', r, 0x34, first)
        md += r
    _set(p[MESH_MATS], md)
    # indices (u16, zero padded to 4 bytes)
    ib = struct.pack('<%dH' % len(m.indices), *m.indices)
    if len(ib) % 4:
        ib += b'\0\0'
    _set(p[MESH_INDICES], ib)
    # descriptor
    dd = bytearray(p[MESH_HEADER].data)
    old_dtri, = struct.unpack_from('<I', dd, 0x24)
    struct.pack_into('<I', dd, 0x10, len(m.materials))
    struct.pack_into('<I', dd, 0x24, ntri)
    struct.pack_into('<I', dd, 0x34, nv)
    _set(p[MESH_HEADER], dd)
    # texture list
    old_ntex = len(m.textures)
    if SOLID_TEXLIST in p:
        old = bytes(p[SOLID_TEXLIST].data)
        oldh = [struct.unpack_from('<I', old, q)[0] for q in range(0, len(old) - 7, 8)]
        old_ntex = len(oldh)
        if oldh != list(m.textures):
            tl = bytearray()
            for i, h in enumerate(m.textures):
                rest = old[i * 8 + 4:i * 8 + 8] if i * 8 + 8 <= len(old) else b'\0\0\0\0'
                tl += struct.pack('<I', h) + rest
            _set(p[SOLID_TEXLIST], tl)
    # object header
    hd = bytearray(p[SOLID_HEADER].data)
    # keep any (rare) offset the shipped header has against the real counts
    old_htri, = struct.unpack_from('<I', hd, 0x14)
    struct.pack_into('<I', hd, 0x14, max(0, old_htri + ntri - old_dtri))
    hd[0x19] = max(0, min(255, hd[0x19] + len(m.textures) - old_ntex))
    lo, hi = _bounds(m.verts)
    struct.pack_into('<3f', hd, 0x20, *lo)
    struct.pack_into('<3f', hd, 0x30, *hi)
    if m.matrix is not None:
        struct.pack_into('<16f', hd, 0x40, *m.matrix)
    _set(p[SOLID_HEADER], hd)
    p[SOLID_MESH].dirty = True
    obj.dirty = True


def set_name(obj, name, model_hash=None):
    """Rename an object (header name + hash; default hash = bin_hash(name))."""
    from .tpk import bin_hash
    h = obj.find(SOLID_HEADER)
    if len(name) > NAME_LEN - 1:
        raise ValueError('object name %r is longer than %d characters' % (name, NAME_LEN - 1))
    hd = bytearray(h.data[:NAME_OFFSET])
    struct.pack_into('<I', hd, 0x10, bin_hash(name) if model_hash is None else model_hash)
    # the shipped header is always 192 bytes: the name sits in a fixed 28-byte field
    raw = name.encode('ascii').ljust(NAME_LEN, b'\0')
    _set(h, bytes(hd) + raw + bytes(h.data[NAME_OFFSET + NAME_LEN:]))
    obj.dirty = True


def clone_object(obj):
    """Deep copy of an object node (data copied, marked new)."""
    def cp(c):
        n = bchunk.Chunk(c.id, bytes(c.data) if c.children is None else b'',
                         [cp(k) for k in c.children] if c.children is not None else None, c.aligned)
        n.pad_target, n.pad_mod, n.size = c.pad_target, c.pad_mod, c.size
        return n
    return cp(obj)
