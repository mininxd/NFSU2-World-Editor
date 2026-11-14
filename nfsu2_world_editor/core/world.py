"""NFSU2 world structures: section table, SolidObject meshes, scenery instances.

Format notes follow FRSWorlsEditor docs/NFSU2-FORMATS.md and OpenUG2 docs/FORMATS.md.
All binary values are little-endian.
"""
import struct

from .chunks import iter_chunks, find, payload_start

SECTION_TABLE = 0x00034110      # in L4Rx.BUN, 80-byte entries
SOLID_OBJECT = 0x80134010       # container: one model
SOLID_HEADER = 0x00134011
SOLID_TEXLIST = 0x00134012
SOLID_MESH = 0x80134100
MESH_HEADER = 0x00134900
MESH_VERTS = 0x00134B01
MESH_MATS = 0x00134B02
MESH_INDICES = 0x00134B03
SCENERY_PACK = 0x80034100
SCENERY_INFOS = 0x00034102      # 68-byte entries, model hash @0x20
SCENERY_INSTANCES = 0x00034103  # 64-byte entries

SECTION_ENTRY_SIZE = 80
MATERIAL_SIZE = 60
SCENERY_INFO_SIZE = 68
SCENERY_INSTANCE_SIZE = 64
VERTEX_STRIDES = (24, 36)       # 24: pos,colour,uv   36: pos,normal,colour,uv


def section_table(l4_buf):
    """Parse every 0x00034110 table in an L4Rx.BUN buffer -> list of dicts."""
    out = []
    for d, s in find(l4_buf, 0, len(l4_buf), SECTION_TABLE):
        for i in range(s // SECTION_ENTRY_SIZE):
            e = d + i * SECTION_ENTRY_SIZE
            tag = bytes(l4_buf[e:e + 8]).split(b'\0')[0].decode('ascii', 'replace')
            num, = struct.unpack_from('<I', l4_buf, e + 8)
            off, tot, nopack, order = struct.unpack_from('<4I', l4_buf, e + 0x14)
            cx, cy, cz = struct.unpack_from('<3f', l4_buf, e + 0x24)
            out.append(dict(tag=tag, num=num, offset=off, size=tot, geom_size=nopack,
                            order=order, center=(cx, cy, cz)))
    return out


def object_hash(buf, d, s):
    """Model hash of the SolidObject at ``(d, s)`` without parsing the mesh, or None."""
    for cid, cd, cs in iter_chunks(buf, d, d + s):
        if cid == SOLID_HEADER:
            p = payload_start(buf, cid, cd, cs)
            if p + 0x14 <= cd + cs:
                return struct.unpack_from('<I', buf, p + 0x10)[0]
    return None


def parse_object(buf, d, s):
    """Parse one SolidObject container payload ``buf[d:d+s]`` -> dict, or None if it
    has no usable mesh."""
    obj = {'name': '?', 'hash': 0, 'matrix': None, 'tex': [], 'verts': [], 'uvs': [],
           'cols': [], 'faces': [], 'face_mat': [], 'normals': None, 'stride': 0, 'mats': []}
    mesh = None
    for cid, cd, cs in iter_chunks(buf, d, d + s):
        if cid == SOLID_HEADER:
            p = payload_start(buf, cid, cd, cs)
            obj['hash'], = struct.unpack_from('<I', buf, p + 0x10)
            obj['matrix'] = struct.unpack_from('<16f', buf, p + 0x40)
            nm = bytes(buf[p + 0xA4: cd + cs]).split(b'\0')[0]
            obj['name'] = nm.decode('ascii', 'replace')
        elif cid == SOLID_TEXLIST:
            p = payload_start(buf, cid, cd, cs)
            obj['tex'] = [struct.unpack_from('<I', buf, q)[0] for q in range(p, cd + cs - 7, 8)]
        elif cid == SOLID_MESH:
            mesh = (cd, cs)
    if not mesh:
        return None
    need = (MESH_HEADER, MESH_VERTS, MESH_MATS, MESH_INDICES)
    sub = {c: (dd, ss) for c, dd, ss in iter_chunks(buf, mesh[0], mesh[0] + mesh[1]) if c in need}
    if not all(k in sub for k in need):
        return None
    hd = payload_start(buf, MESH_HEADER, *sub[MESH_HEADER])
    ntri, = struct.unpack_from('<I', buf, hd + 0x24)
    nvert, = struct.unpack_from('<I', buf, hd + 0x34)
    if nvert == 0:
        return None
    vd, vs = sub[MESH_VERTS]
    vp = payload_start(buf, MESH_VERTS, vd, vs)
    stride = (vd + vs - vp) // nvert
    if stride not in VERTEX_STRIDES:
        stride = vs // nvert
        if stride not in VERTEX_STRIDES:
            return None
    obj['stride'] = stride
    norms = [] if stride == 36 else None
    for i in range(nvert):
        q = vp + i * stride
        obj['verts'].append(struct.unpack_from('<3f', buf, q))
        if stride == 24:
            col, = struct.unpack_from('<I', buf, q + 12)
            u, v = struct.unpack_from('<2f', buf, q + 16)
        else:
            norms.append(struct.unpack_from('<3f', buf, q + 12))
            col, = struct.unpack_from('<I', buf, q + 24)
            u, v = struct.unpack_from('<2f', buf, q + 28)
        obj['cols'].append(((col >> 16) & 255, (col >> 8) & 255, col & 255, col >> 24))  # ARGB -> RGBA
        obj['uvs'].append((u, v))
    obj['normals'] = norms
    md, ms = sub[MESH_MATS]
    mp = payload_start(buf, MESH_MATS, md, ms)
    mats = []
    for i in range((md + ms - mp) // MATERIAL_SIZE):
        q = mp + i * MATERIAL_SIZE
        cnt, = struct.unpack_from('<I', buf, q + 0x0C)
        tex, = struct.unpack_from('<I', buf, q + 0x1C)
        first, = struct.unpack_from('<I', buf, q + 0x34)
        mats.append((first, cnt, tex))
    idd, ids = sub[MESH_INDICES]
    ip = payload_start(buf, MESH_INDICES, idd, ids)
    nidx = min(ntri * 3, (idd + ids - ip) // 2)
    idx = struct.unpack_from('<%dH' % nidx, buf, ip)
    tri_mat = {}
    for mi, (first, cnt, tex) in enumerate(mats):
        for t in range(first // 3, (first + cnt) // 3):
            tri_mat[t] = mi
    obj['mats'] = mats
    for t in range(nidx // 3):
        a, b, c = idx[3 * t:3 * t + 3]
        if a == b or b == c or a == c or max(a, b, c) >= nvert:
            continue  # degenerate strip joints / out of range
        obj['faces'].append((a, b, c))
        obj['face_mat'].append(tri_mat.get(t, 0))
    return obj


def section_objects(buf, off, size):
    """All parseable SolidObjects in a streaming section ``buf[off:off+size]``."""
    objs = []
    for d, s in find(buf, off, off + size, SOLID_OBJECT):
        o = parse_object(buf, d, s)
        if o:
            o['chunk_offset'] = d - 8
            o['chunk_size'] = s + 8
            objs.append(o)
    return objs


def build_model_index(buf, sections):
    """hash -> (data_offset, size) of the first SolidObject with that hash across the
    given section-table entries."""
    index = {}
    for e in sections:
        for d, s in find(buf, e['offset'], e['offset'] + e['size'], SOLID_OBJECT):
            h = object_hash(buf, d, s)
            if h is not None:
                index.setdefault(h, (d, s))
    return index


def iter_scenery_instances(buf, off, size):
    """Yield ``(model_hash, matrix16, record_offset)`` for every scenery instance in a
    section. ``matrix16`` is row-major with translation in the last row (game
    row-vector convention), like SolidObject matrices."""
    for d, s in find(buf, off, off + size, SCENERY_PACK):
        ch = {c: (dd, ss) for c, dd, ss in iter_chunks(buf, d, d + s)}
        if SCENERY_INFOS not in ch or SCENERY_INSTANCES not in ch:
            continue
        id_, is_ = ch[SCENERY_INFOS]
        infos = [struct.unpack_from('<I', buf, q + 0x20)[0]
                 for q in range(id_, id_ + is_ - (SCENERY_INFO_SIZE - 1), SCENERY_INFO_SIZE)]
        rd, rs = ch[SCENERY_INSTANCES]
        rp = payload_start(buf, SCENERY_INSTANCES, rd, rs)
        for q in range(rp, rd + rs - (SCENERY_INSTANCE_SIZE - 1), SCENERY_INSTANCE_SIZE):
            ii, = struct.unpack_from('<H', buf, q + 0x18)
            if ii >= len(infos):
                continue
            t = struct.unpack_from('<3f', buf, q + 0x20)
            r = [x / 8192.0 for x in struct.unpack_from('<9h', buf, q + 0x2C)]
            m = (r[0], r[1], r[2], 0.0, r[3], r[4], r[5], 0.0, r[6], r[7], r[8], 0.0,
                 t[0], t[1], t[2], 1.0)
            yield infos[ii], m, q


def apply_matrix(m, v):
    """Transform point ``v`` by a row-major 4x4 game matrix (row-vector convention)."""
    x, y, z = v
    return (x * m[0] + y * m[4] + z * m[8] + m[12],
            x * m[1] + y * m[5] + z * m[9] + m[13],
            x * m[2] + y * m[6] + z * m[10] + m[14])


def is_preplaced(obj):
    """True if the model's vertices are already in world space far from its own
    origin (i.e. it is not a reusable library model meant for instancing)."""
    v = obj['verts']
    lo = [min(p[k] for p in v) for k in range(3)]
    hi = [max(p[k] for p in v) for k in range(3)]
    c = [(lo[k] + hi[k]) / 2 for k in range(3)]
    diag = sum((hi[k] - lo[k]) ** 2 for k in range(3)) ** 0.5
    return sum(x * x for x in c) ** 0.5 > diag
