"""NFSU2 world reader (pure python, usable inside Blender).
Format per FRSWorlsEditor docs/NFSU2-FORMATS.md and OpenUG2 docs/FORMATS.md."""
import struct

ALIGN = {0x00134B01: 128, 0x33320002: 128, 0x00134012: 8}

def chunks(buf, start, end):
    off = start
    while off + 8 <= end:
        cid, size = struct.unpack_from('<II', buf, off)
        if off + 8 + size > end: break
        yield cid, off + 8, size
        off += 8 + size

def payload_start(buf, cid, data, size):
    """Skip 0x11 alignment filler only if it is really there (absolute-offset alignment)."""
    a = ALIGN.get(cid, 16)
    pad = (-data) % a
    if pad and pad <= size and buf[data:data+pad] == b'\x11' * pad:
        return data + pad
    return data

def find(buf, start, end, cid_want):
    for cid, d, s in chunks(buf, start, end):
        if cid == cid_want: yield d, s
        elif cid & 0x80000000: yield from find(buf, d, d + s, cid_want)

def section_table(l4_buf):
    out = []
    for d, s in find(l4_buf, 0, len(l4_buf), 0x00034110):
        for i in range(s // 80):
            e = d + i * 80
            tag = l4_buf[e:e+4].split(b'\0')[0].decode('ascii', 'replace')
            num, = struct.unpack_from('<I', l4_buf, e + 8)
            off, tot, nopack, order = struct.unpack_from('<IIII', l4_buf, e + 0x14)
            cx, cy, cz = struct.unpack_from('<3f', l4_buf, e + 0x24)
            out.append(dict(tag=tag, num=num, offset=off, size=tot, geom_size=nopack,
                            order=order, center=(cx, cy, cz)))
    return out

def parse_object(buf, d, s):
    """Parse one 0x80134010 SolidObject -> dict or None."""
    obj = {'name': '?', 'matrix': None, 'tex': [], 'verts': [], 'uvs': [], 'cols': [],
           'faces': [], 'face_mat': [], 'normals': None, 'stride': 0}
    mesh = None
    for cid, cd, cs in chunks(buf, d, d + s):
        if cid == 0x00134011:
            p = payload_start(buf, cid, cd, cs)
            obj['hash'], = struct.unpack_from('<I', buf, p + 0x10)
            obj['matrix'] = struct.unpack_from('<16f', buf, p + 0x40)
            nm = buf[p + 0xA4: cd + cs].split(b'\0')[0]
            obj['name'] = nm.decode('ascii', 'replace')
        elif cid == 0x00134012:
            p = payload_start(buf, cid, cd, cs)
            obj['tex'] = [struct.unpack_from('<I', buf, q)[0] for q in range(p, cd + cs - 7, 8)]
        elif cid == 0x80134100:
            mesh = (cd, cs)
    if not mesh: return None
    sub = {c: (dd, ss) for c, dd, ss in chunks(buf, *(mesh[0], mesh[0] + mesh[1])) if c in (0x134900, 0x134B01, 0x134B02, 0x134B03)}
    if not all(k in sub for k in (0x134900, 0x134B01, 0x134B02, 0x134B03)): return None
    hd = payload_start(buf, 0x134900, *sub[0x134900])
    nmat, = struct.unpack_from('<I', buf, hd + 0x10)
    ntri, = struct.unpack_from('<I', buf, hd + 0x24)
    nvert, = struct.unpack_from('<I', buf, hd + 0x34)
    vd, vs = sub[0x134B01]; vp = payload_start(buf, 0x134B01, vd, vs)
    if nvert == 0: return None
    stride = (vd + vs - vp) // nvert
    if stride not in (24, 36):
        # fall back: whole payload / count
        stride = vs // nvert
        if stride not in (24, 36): return None
    obj['stride'] = stride
    norms = [] if stride == 36 else None
    for i in range(nvert):
        q = vp + i * stride
        x, y, z = struct.unpack_from('<3f', buf, q)
        obj['verts'].append((x, y, z))
        if stride == 24:
            col, = struct.unpack_from('<I', buf, q + 12); u, v = struct.unpack_from('<2f', buf, q + 16)
        else:
            norms.append(struct.unpack_from('<3f', buf, q + 12))
            col, = struct.unpack_from('<I', buf, q + 24); u, v = struct.unpack_from('<2f', buf, q + 28)
        obj['cols'].append(((col >> 16) & 255, (col >> 8) & 255, col & 255, col >> 24))  # ARGB -> RGBA
        obj['uvs'].append((u, v))
    obj['normals'] = norms
    md, ms = sub[0x134B02]; mp = payload_start(buf, 0x134B02, md, ms)
    mats = []
    for i in range((md + ms - mp) // 60):
        q = mp + i * 60
        cnt, = struct.unpack_from('<I', buf, q + 0x0C)
        tex, = struct.unpack_from('<I', buf, q + 0x1C)
        first, = struct.unpack_from('<I', buf, q + 0x34)
        mats.append((first, cnt, tex))
    idd, ids = sub[0x134B03]; ip = payload_start(buf, 0x134B03, idd, ids)
    nidx = min(ntri * 3, (idd + ids - ip) // 2)
    idx = struct.unpack_from('<%dH' % nidx, buf, ip)
    tri_mat = {}
    for mi, (first, cnt, tex) in enumerate(mats):
        for t in range(first // 3, (first + cnt) // 3): tri_mat[t] = mi
    obj['mats'] = mats
    for t in range(nidx // 3):
        a, b, c = idx[3*t:3*t+3]
        if a == b or b == c or a == c: continue
        if max(a, b, c) >= nvert: continue
        obj['faces'].append((a, b, c)); obj['face_mat'].append(tri_mat.get(t, 0))
    return obj

def section_objects(buf, off, size):
    """All SolidObjects in a streaming section [off, off+size)."""
    objs = []
    for d, s in find(buf, off, off + size, 0x80134010):
        o = parse_object(buf, d, s)
        if o: objs.append(o)
    return objs

def apply_matrix(m, v):
    x, y, z = v
    return (x*m[0] + y*m[4] + z*m[8] + m[12], x*m[1] + y*m[5] + z*m[9] + m[13], x*m[2] + y*m[6] + z*m[10] + m[14])
