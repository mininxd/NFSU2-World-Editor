"""Texture pixel codecs: DXT1 / DXT3 / DXT5, 8-bit palettised (P8) and raw
A8R8G8B8, decode and encode.

Everything works in pure Python (Blender's bundled Python or any CPython 3.8+).
When numpy is importable a vectorised path is used for decoding (results are
identical); pass ``use_numpy=False`` to force the pure path.

Images are ``bytes``/``bytearray`` of RGBA8, row-major, top row first.
"""
import struct

try:  # optional fast path
    import numpy as _np
except Exception:  # pragma: no cover - numpy missing
    _np = None

HAVE_NUMPY = _np is not None

# format codes used by the TPK format table (D3DFORMAT)
FOURCC_DXT1 = 0x31545844
FOURCC_DXT3 = 0x33545844
FOURCC_DXT5 = 0x35545844
D3DFMT_A8R8G8B8 = 21
D3DFMT_P8 = 41

FORMAT_NAMES = {FOURCC_DXT1: 'DXT1', FOURCC_DXT3: 'DXT3', FOURCC_DXT5: 'DXT5',
                D3DFMT_A8R8G8B8: 'ARGB8', D3DFMT_P8: 'P8'}


def format_name(fmt):
    return FORMAT_NAMES.get(fmt, 'FMT_%X' % fmt)


def block_bytes(fmt):
    return 8 if fmt == FOURCC_DXT1 else 16


def level_size(fmt, w, h):
    """Byte size of one mip level."""
    if fmt in (FOURCC_DXT1, FOURCC_DXT3, FOURCC_DXT5):
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * block_bytes(fmt)
    if fmt == D3DFMT_P8:
        return w * h
    if fmt == D3DFMT_A8R8G8B8:
        return w * h * 4
    raise ValueError('unsupported texture format %s' % format_name(fmt))


def mip_dims(w, h, levels):
    out = []
    for i in range(levels):
        out.append((max(1, w >> i), max(1, h >> i)))
    return out


# ---------------------------------------------------------------- helpers

def _rgb565(c):
    r = (c >> 11) & 31
    g = (c >> 5) & 63
    b = c & 31
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def _palette(c0, c1, dxt1):
    p0 = _rgb565(c0)
    p1 = _rgb565(c1)
    if c0 > c1 or not dxt1:
        p2 = tuple((2 * a + b) // 3 for a, b in zip(p0, p1))
        p3 = tuple((a + 2 * b) // 3 for a, b in zip(p0, p1))
        return (p0 + (255,), p1 + (255,), p2 + (255,), p3 + (255,))
    p2 = tuple((a + b) // 2 for a, b in zip(p0, p1))
    return (p0 + (255,), p1 + (255,), p2 + (255,), (0, 0, 0, 0))


def _dxt5_alpha_table(a0, a1):
    if a0 > a1:
        return [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    return [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]


# ---------------------------------------------------------------- decode

def decode(data, fmt, w, h, palette=None, use_numpy=True):
    """Decode one mip level to RGBA8 bytes (len ``w*h*4``)."""
    if use_numpy and _np is not None:
        return _decode_np(data, fmt, w, h, palette)
    if fmt in (FOURCC_DXT1, FOURCC_DXT3, FOURCC_DXT5):
        return _decode_dxt_py(data, fmt, w, h)
    if fmt == D3DFMT_P8:
        if palette is None:
            raise ValueError('P8 texture needs a palette')
        pal = [bytes((palette[4 * i + 2], palette[4 * i + 1], palette[4 * i], palette[4 * i + 3]))
               for i in range(256)]
        return b''.join(pal[i] for i in bytes(data[:w * h]))
    if fmt == D3DFMT_A8R8G8B8:
        out = bytearray(w * h * 4)
        src = bytes(data[:w * h * 4])
        out[0::4] = src[2::4]
        out[1::4] = src[1::4]
        out[2::4] = src[0::4]
        out[3::4] = src[3::4]
        return bytes(out)
    raise ValueError('unsupported texture format %s' % format_name(fmt))


def _decode_dxt_py(data, fmt, w, h):
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    bb = block_bytes(fmt)
    out = bytearray(w * h * 4)
    data = bytes(data)
    dxt1 = fmt == FOURCC_DXT1
    q = 0
    for by in range(bh):
        for bx in range(bw):
            blk = data[q:q + bb]
            q += bb
            if dxt1:
                cb = blk
                alpha = None
            else:
                cb = blk[8:]
                if fmt == FOURCC_DXT3:
                    av = int.from_bytes(blk[:8], 'little')
                    alpha = [((av >> (4 * i)) & 15) * 17 for i in range(16)]
                else:
                    tab = _dxt5_alpha_table(blk[0], blk[1])
                    bits = int.from_bytes(blk[2:8], 'little')
                    alpha = [tab[(bits >> (3 * i)) & 7] for i in range(16)]
            c0, c1, sel = struct.unpack_from('<HHI', cb, 0)
            pal = _palette(c0, c1, dxt1)
            for py in range(4):
                y = by * 4 + py
                if y >= h:
                    break
                row = y * w
                for px in range(4):
                    x = bx * 4 + px
                    if x >= w:
                        break
                    i = py * 4 + px
                    c = pal[(sel >> (2 * i)) & 3]
                    o = (row + x) * 4
                    out[o] = c[0]
                    out[o + 1] = c[1]
                    out[o + 2] = c[2]
                    out[o + 3] = c[3] if alpha is None else alpha[i]
    return bytes(out)


def _decode_np(data, fmt, w, h, palette):
    np = _np
    if fmt == D3DFMT_P8:
        pal = np.frombuffer(bytes(palette[:1024]), np.uint8).reshape(256, 4)[:, [2, 1, 0, 3]]
        idx = np.frombuffer(bytes(data[:w * h]), np.uint8)
        return pal[idx].tobytes()
    if fmt == D3DFMT_A8R8G8B8:
        a = np.frombuffer(bytes(data[:w * h * 4]), np.uint8).reshape(-1, 4)
        return a[:, [2, 1, 0, 3]].tobytes()
    if fmt not in (FOURCC_DXT1, FOURCC_DXT3, FOURCC_DXT5):
        raise ValueError('unsupported texture format %s' % format_name(fmt))
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    bb = block_bytes(fmt)
    n = bw * bh
    blk = np.frombuffer(bytes(data[:n * bb]), np.uint8).reshape(n, bb)
    cb = blk[:, bb - 8:]
    c0 = cb[:, 0].astype(np.uint32) | (cb[:, 1].astype(np.uint32) << 8)
    c1 = cb[:, 2].astype(np.uint32) | (cb[:, 3].astype(np.uint32) << 8)
    sel = (cb[:, 4].astype(np.uint32) | (cb[:, 5].astype(np.uint32) << 8) |
           (cb[:, 6].astype(np.uint32) << 16) | (cb[:, 7].astype(np.uint32) << 24))

    def unpack(c):
        r = (c >> 11) & 31
        g = (c >> 5) & 63
        b = c & 31
        return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], 1).astype(np.int32)

    p0, p1 = unpack(c0), unpack(c1)
    four = (c0 > c1) | (fmt != FOURCC_DXT1)
    p2 = np.where(four[:, None], (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four[:, None], (p0 + 2 * p1) // 3, 0)
    pal = np.zeros((n, 4, 4), np.int32)
    pal[:, 0, :3] = p0
    pal[:, 1, :3] = p1
    pal[:, 2, :3] = p2
    pal[:, 3, :3] = p3
    pal[:, :3, 3] = 255
    pal[:, 3, 3] = np.where(four, 255, 0)
    shifts = np.arange(16, dtype=np.uint32) * 2
    idx = (sel[:, None] >> shifts) & 3                       # n x 16
    px = pal[np.arange(n)[:, None], idx]                     # n x 16 x 4
    if fmt == FOURCC_DXT3:
        a = blk[:, :8]
        lo = a & 15
        hi = a >> 4
        al = np.stack([lo, hi], 2).reshape(n, 16).astype(np.int32) * 17
        px[:, :, 3] = al
    elif fmt == FOURCC_DXT5:
        a0 = blk[:, 0].astype(np.int32)
        a1 = blk[:, 1].astype(np.int32)
        bits = np.zeros(n, np.uint64)
        for k in range(6):
            bits |= blk[:, 2 + k].astype(np.uint64) << np.uint64(8 * k)
        ai = ((bits[:, None] >> (np.arange(16, dtype=np.uint64) * np.uint64(3))) & np.uint64(7)).astype(np.int32)
        tab = np.zeros((n, 8), np.int32)
        tab[:, 0], tab[:, 1] = a0, a1
        big = a0 > a1
        for i in range(1, 7):
            tab[:, 1 + i] = np.where(big, ((7 - i) * a0 + i * a1) // 7, 0)
        for i in range(1, 5):
            tab[:, 1 + i] = np.where(big, tab[:, 1 + i], ((5 - i) * a0 + i * a1) // 5)
        tab[:, 6] = np.where(big, tab[:, 6], 0)
        tab[:, 7] = np.where(big, tab[:, 7], 255)
        px[:, :, 3] = tab[np.arange(n)[:, None], ai]
    img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return img[:h, :w].astype(np.uint8).tobytes()


# ---------------------------------------------------------------- encode

def _to565(r, g, b):
    return ((r * 31 + 127) // 255) << 11 | ((g * 63 + 127) // 255) << 5 | ((b * 31 + 127) // 255)


def _encode_color_block(px, dxt1_alpha):
    """``px``: 16 RGBA tuples. Returns 8 bytes (DXT1-style colour block).
    ``dxt1_alpha``: use the 3-colour mode with transparent texels where a < 128."""
    trans = [p[3] < 128 for p in px] if dxt1_alpha else [False] * 16
    cols = [p for p, t in zip(px, trans) if not t]
    if not cols:
        return struct.pack('<HHI', 0, 0xFFFF, 0xFFFFFFFF)
    # endpoints: extremes along the axis of greatest spread (bounding-box diagonal)
    lo = [min(c[k] for c in cols) for k in range(3)]
    hi = [max(c[k] for c in cols) for k in range(3)]
    axis = [hi[k] - lo[k] for k in range(3)]
    # orient the diagonal by the sign of the covariance with the dominant channel
    m = [sum(c[k] for c in cols) / len(cols) for k in range(3)]
    d = max(range(3), key=lambda k: axis[k])
    for k in range(3):
        if k != d:
            cov = sum((c[d] - m[d]) * (c[k] - m[k]) for c in cols)
            if cov < 0:
                lo[k], hi[k] = hi[k], lo[k]
    # inset a little to reduce error
    e0 = [hi[k] - (hi[k] - lo[k]) / 16.0 for k in range(3)]
    e1 = [lo[k] + (hi[k] - lo[k]) / 16.0 for k in range(3)]
    c0 = _to565(*[int(round(x)) for x in e0])
    c1 = _to565(*[int(round(x)) for x in e1])
    use3 = any(trans)
    if use3:
        if c0 > c1:
            c0, c1 = c1, c0
    else:
        if c0 < c1:
            c0, c1 = c1, c0
    c0, c1, sel = _select(px, trans, c0, c1)
    if not use3 and c0 != c1:
        # one least-squares refinement of the endpoints for the chosen selectors
        r = _refine(px, sel)
        if r is not None:
            n0, n1 = r
            if n0 < n1:
                n0, n1 = n1, n0
            if n0 != n1:
                cand = _select(px, trans, n0, n1)
                if _block_err(px, trans, cand) < _block_err(px, trans, (c0, c1, sel)):
                    c0, c1, sel = cand
    return struct.pack('<HHI', c0, c1, sel)


def _select(px, trans, c0, c1):
    pal = _palette(c0, c1, True)
    n = 3 if (c0 <= c1) else 4
    sel = 0
    for i, p in enumerate(px):
        if trans[i]:
            s = 3
        else:
            best = None
            s = 0
            for j in range(n):
                q = pal[j]
                dd = (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2
                if best is None or dd < best:
                    best, s = dd, j
        sel |= s << (2 * i)
    return c0, c1, sel


def _block_err(px, trans, cand):
    c0, c1, sel = cand
    pal = _palette(c0, c1, True)
    e = 0
    for i, p in enumerate(px):
        if not trans[i]:
            q = pal[(sel >> (2 * i)) & 3]
            e += (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2
    return e


_W = ((1.0, 0.0), (0.0, 1.0), (2 / 3.0, 1 / 3.0), (1 / 3.0, 2 / 3.0))


def _refine(px, sel):
    aa = ab = bb = 0.0
    ax = [0.0, 0.0, 0.0]
    bx = [0.0, 0.0, 0.0]
    for i, p in enumerate(px):
        a, b = _W[(sel >> (2 * i)) & 3]
        aa += a * a
        ab += a * b
        bb += b * b
        for k in range(3):
            ax[k] += a * p[k]
            bx[k] += b * p[k]
    det = aa * bb - ab * ab
    if abs(det) < 1e-9:
        return None
    e0 = [max(0, min(255, int(round((ax[k] * bb - bx[k] * ab) / det)))) for k in range(3)]
    e1 = [max(0, min(255, int(round((bx[k] * aa - ax[k] * ab) / det)))) for k in range(3)]
    return _to565(*e0), _to565(*e1)


def _encode_dxt5_alpha(al):
    a0, a1 = max(al), min(al)
    if a0 == a1:
        return bytes((a0, a1)) + bytes(6)
    tab = _dxt5_alpha_table(a0, a1)
    bits = 0
    for i, a in enumerate(al):
        j = min(range(8), key=lambda k: abs(tab[k] - a))
        bits |= j << (3 * i)
    return bytes((a0, a1)) + bits.to_bytes(6, 'little')


def encode(rgba, fmt, w, h, palette=None, dxt1_alpha=False):
    """Encode RGBA8 ``w*h`` pixels into one mip level of ``fmt``.

    For P8 the given 1024-byte (B,G,R,A) ``palette`` is reused and each pixel is
    mapped to its nearest entry (use :func:`build_palette` to make a new one)."""
    rgba = bytes(rgba)
    if fmt == D3DFMT_A8R8G8B8:
        out = bytearray(w * h * 4)
        out[0::4] = rgba[2::4]
        out[1::4] = rgba[1::4]
        out[2::4] = rgba[0::4]
        out[3::4] = rgba[3::4]
        return bytes(out)
    if fmt == D3DFMT_P8:
        return _encode_p8(rgba, w, h, palette)
    if fmt not in (FOURCC_DXT1, FOURCC_DXT3, FOURCC_DXT5):
        raise ValueError('unsupported texture format %s' % format_name(fmt))
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    out = bytearray()
    for by in range(bh):
        for bx in range(bw):
            px = []
            for py in range(4):
                y = min(by * 4 + py, h - 1)
                for pxx in range(4):
                    x = min(bx * 4 + pxx, w - 1)
                    o = (y * w + x) * 4
                    px.append(tuple(rgba[o:o + 4]))
            if fmt == FOURCC_DXT3:
                av = 0
                for i, p in enumerate(px):
                    av |= ((p[3] * 15 + 127) // 255) << (4 * i)
                out += av.to_bytes(8, 'little')
                out += _encode_color_block(px, False)
            elif fmt == FOURCC_DXT5:
                out += _encode_dxt5_alpha([p[3] for p in px])
                out += _encode_color_block(px, False)
            else:
                out += _encode_color_block(px, dxt1_alpha)
    return bytes(out)


def _encode_p8(rgba, w, h, palette):
    if palette is None:
        palette = build_palette(rgba)
    if _np is not None:
        return _p8_indices_np(rgba, palette)[0].tobytes()
    pal = [(palette[4 * i + 2], palette[4 * i + 1], palette[4 * i], palette[4 * i + 3]) for i in range(256)]
    cache = {}
    out = bytearray(w * h)
    for i in range(w * h):
        p = rgba[4 * i:4 * i + 4]
        j = cache.get(p)
        if j is None:
            r, g, b, a = p
            j = min(range(256), key=lambda k: (pal[k][0] - r) ** 2 + (pal[k][1] - g) ** 2 +
                    (pal[k][2] - b) ** 2 + (pal[k][3] - a) ** 2)
            cache[p] = j
        out[i] = j
    return bytes(out)


def build_palette(rgba, size=256):
    """Median-cut a 256-entry palette from RGBA8 pixels -> 1024 bytes (B,G,R,A)."""
    px = [tuple(rgba[i:i + 4]) for i in range(0, len(rgba), 4)]
    boxes = [px]
    while len(boxes) < size:
        boxes.sort(key=len, reverse=True)
        box = boxes[0]
        if len(box) < 2:
            break
        rng = [max(p[k] for p in box) - min(p[k] for p in box) for k in range(4)]
        k = rng.index(max(rng))
        if rng[k] == 0:
            break
        box = sorted(box, key=lambda p: p[k])
        mid = len(box) // 2
        boxes[0:1] = [box[:mid], box[mid:]]
    out = bytearray(size * 4)
    for i, box in enumerate(boxes[:size]):
        n = len(box)
        r, g, b, a = [sum(p[k] for p in box) // n for k in range(4)]
        out[4 * i:4 * i + 4] = bytes((b, g, r, a))
    return bytes(out)


def downsample(rgba, w, h):
    """2x2 box filter -> (bytes, w2, h2)."""
    w2, h2 = max(1, w // 2), max(1, h // 2)
    if _np is not None:
        a = _np.frombuffer(bytes(rgba), _np.uint8).reshape(h, w, 4).astype(_np.uint32)
        if w2 * 2 != w or h2 * 2 != h:
            ys = _np.minimum(_np.arange(h2 * 2), h - 1)
            xs = _np.minimum(_np.arange(w2 * 2), w - 1)
            a = a[ys][:, xs]
        s = a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2]
        return ((s + 2) // 4).astype(_np.uint8).tobytes(), w2, h2
    out = bytearray(w2 * h2 * 4)
    for y in range(h2):
        for x in range(w2):
            for k in range(4):
                s = 0
                for dy in (0, 1):
                    for dx in (0, 1):
                        yy, xx = min(2 * y + dy, h - 1), min(2 * x + dx, w - 1)
                        s += rgba[(yy * w + xx) * 4 + k]
                out[(y * w2 + x) * 4 + k] = (s + 2) // 4
    return bytes(out), w2, h2


def _p8_indices_np(rgba, palette):
    np = _np
    px = np.frombuffer(bytes(rgba), np.uint8).reshape(-1, 4).astype(np.int32)
    pal = np.frombuffer(bytes(palette[:1024]), np.uint8).reshape(256, 4)[:, [2, 1, 0, 3]].astype(np.int32)
    uniq, inv = np.unique(px, axis=0, return_inverse=True)
    best = np.zeros(len(uniq), np.int64)
    err = np.zeros(len(uniq), np.int64)
    for s in range(0, len(uniq), 4096):
        d = ((uniq[s:s + 4096, None, :] - pal[None, :, :]) ** 2).sum(2)
        best[s:s + 4096] = d.argmin(1)
        err[s:s + 4096] = d.min(1)
    inv = inv.reshape(-1)
    return best[inv].astype(np.uint8), err[inv]


def palette_error(rgba, palette):
    """Mean squared RGB error of mapping ``rgba`` onto ``palette``."""
    if _np is not None:
        return float(_p8_indices_np(rgba, palette)[1].mean()) / 4.0
    idx = _encode_p8(rgba, len(rgba) // 4, 1, palette)
    tot = 0
    for i, j in enumerate(idx):
        for k, kk in ((0, 2), (1, 1), (2, 0), (3, 3)):
            tot += (rgba[4 * i + k] - palette[4 * j + kk]) ** 2
    return tot / max(1, len(idx)) / 4.0
