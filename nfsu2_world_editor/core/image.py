"""Minimal stdlib-only PNG and TGA read/write for RGBA8 images.

PNG: writes 8-bit RGBA; reads 8-bit greyscale/grey+alpha/RGB/RGBA/palette,
non-interlaced (what every paint program saves by default).
TGA: writes uncompressed 32-bit top-left; reads uncompressed or RLE 24/32-bit.
"""
import struct
import zlib

_PNG_SIG = b'\x89PNG\r\n\x1a\n'


def _png_chunk(tag, data):
    return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)


def write_png(path, rgba, w, h, level=6):
    raw = bytearray()
    stride = w * 4
    rgba = bytes(rgba)
    for y in range(h):
        raw.append(0)
        raw += rgba[y * stride:(y + 1) * stride]
    data = (_PNG_SIG + _png_chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) +
            _png_chunk(b'IDAT', zlib.compress(bytes(raw), level)) + _png_chunk(b'IEND', b''))
    with open(path, 'wb') as f:
        f.write(data)


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def read_png(path):
    """-> (rgba bytes, w, h)"""
    with open(path, 'rb') as f:
        data = f.read()
    if data[:8] != _PNG_SIG:
        raise ValueError('%s is not a PNG' % path)
    off = 8
    idat = bytearray()
    plte = trns = None
    while off < len(data):
        n, = struct.unpack_from('>I', data, off)
        tag = data[off + 4:off + 8]
        body = data[off + 8:off + 8 + n]
        off += 12 + n
        if tag == b'IHDR':
            w, h, depth, ctype, _, _, inter = struct.unpack('>IIBBBBB', body)
        elif tag == b'PLTE':
            plte = body
        elif tag == b'tRNS':
            trns = body
        elif tag == b'IDAT':
            idat += body
        elif tag == b'IEND':
            break
    if depth != 8 or inter:
        raise ValueError('%s: only 8-bit non-interlaced PNGs are supported' % path)
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(bytes(idat))
    stride = w * ch
    out = bytearray(h * stride)
    prev = bytearray(stride)
    q = 0
    for y in range(h):
        ft = raw[q]
        line = bytearray(raw[q + 1:q + 1 + stride])
        q += 1 + stride
        if ft == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 255
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ft == 3:
            for i in range(stride):
                left = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                left = line[i - ch] if i >= ch else 0
                ul = prev[i - ch] if i >= ch else 0
                line[i] = (line[i] + _paeth(left, prev[i], ul)) & 255
        out[y * stride:(y + 1) * stride] = line
        prev = line
    if ch == 4:
        return bytes(out), w, h
    px = bytearray(w * h * 4)
    if ch == 3:
        px[0::4], px[1::4], px[2::4] = out[0::3], out[1::3], out[2::3]
        px[3::4] = b'\xff' * (w * h)
    elif ch == 2:
        px[0::4] = px[1::4] = px[2::4] = out[0::2]
        px[3::4] = out[1::2]
    elif ctype == 0:
        px[0::4] = px[1::4] = px[2::4] = out
        px[3::4] = b'\xff' * (w * h)
    else:
        pal = [bytes(plte[3 * i:3 * i + 3]) + bytes([trns[i] if trns and i < len(trns) else 255])
               for i in range(len(plte) // 3)]
        px = bytearray(b''.join(pal[i] for i in out))
    return bytes(px), w, h


def write_tga(path, rgba, w, h):
    hdr = struct.pack('<BBBHHBHHHHBB', 0, 0, 2, 0, 0, 0, 0, 0, w, h, 32, 0x28)
    rgba = bytes(rgba)
    bgra = bytearray(len(rgba))
    bgra[0::4], bgra[1::4], bgra[2::4], bgra[3::4] = rgba[2::4], rgba[1::4], rgba[0::4], rgba[3::4]
    with open(path, 'wb') as f:
        f.write(hdr + bytes(bgra))


def read_tga(path):
    with open(path, 'rb') as f:
        data = f.read()
    idlen, cmtype, itype, _, cmlen, cmdepth, _, _, w, h, bpp, desc = struct.unpack_from('<BBBHHBHHHHBB', data, 0)
    if itype not in (2, 10) or bpp not in (24, 32):
        raise ValueError('%s: only true-colour 24/32-bit TGA supported' % path)
    off = 18 + idlen + cmlen * ((cmdepth + 7) // 8)
    bp = bpp // 8
    n = w * h
    if itype == 2:
        pix = data[off:off + n * bp]
    else:
        pix = bytearray()
        while len(pix) < n * bp:
            c = data[off]
            off += 1
            k = (c & 127) + 1
            if c & 128:
                pix += data[off:off + bp] * k
                off += bp
            else:
                pix += data[off:off + k * bp]
                off += k * bp
        pix = bytes(pix[:n * bp])
    out = bytearray(n * 4)
    out[0::4], out[1::4], out[2::4] = pix[2::bp], pix[1::bp], pix[0::bp]
    out[3::4] = pix[3::bp] if bp == 4 else b'\xff' * n
    if not desc & 0x20:  # bottom-left origin -> flip
        s = w * 4
        out = b''.join(bytes(out[(h - 1 - y) * s:(h - y) * s]) for y in range(h))
    return bytes(out), w, h


def read_image(path):
    p = str(path).lower()
    if p.endswith('.tga'):
        return read_tga(path)
    return read_png(path)


def write_image(path, rgba, w, h):
    if str(path).lower().endswith('.tga'):
        write_tga(path, rgba, w, h)
    else:
        write_png(path, rgba, w, h)
