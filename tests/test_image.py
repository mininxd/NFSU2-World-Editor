from nfsu2_world_editor.core import image


def test_png_and_tga_round_trip(tmp_path):
    w, h = 7, 5
    px = bytes((x * 30 % 256, y * 50 % 256, (x ^ y) * 9 % 256, (x * y * 7) % 256)
               for y in range(h) for x in range(w) for _ in [0]) if False else b''.join(
        bytes((x * 30 % 256, y * 50 % 256, (x ^ y) * 9 % 256, (x * y * 7) % 256))
        for y in range(h) for x in range(w))
    for ext in ('png', 'tga'):
        p = tmp_path / ('img.' + ext)
        image.write_image(p, px, w, h)
        assert image.read_image(p) == (px, w, h)


def test_png_filters_from_zlib_stream(tmp_path):
    # a PNG using filter types 1..4 (hand built) decodes to the same pixels
    import struct
    import zlib
    w, h = 3, 4
    rows = [bytes(((x + y) * 20, x * 40, y * 60)) for y in range(h) for x in range(w)]
    rgb = [b''.join(rows[y * w:(y + 1) * w]) for y in range(h)]
    raw = bytearray()
    prev = bytes(w * 3)
    for y, line in enumerate(rgb):
        ft = y % 4 + 1
        out = bytearray()
        for i, v in enumerate(line):
            left = line[i - 3] if i >= 3 else 0
            up = prev[i]
            ul = prev[i - 3] if i >= 3 else 0
            if ft == 1:
                pred = left
            elif ft == 2:
                pred = up
            elif ft == 3:
                pred = (left + up) >> 1
            else:
                pred = image._paeth(left, up, ul)
            out.append((v - pred) & 255)
        raw += bytes([ft]) + out
        prev = line

    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xFFFFFFFF)
    data = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(bytes(raw))) + chunk(b'IEND', b''))
    p = tmp_path / 'f.png'
    p.write_bytes(data)
    px, ww, hh = image.read_png(p)
    assert (ww, hh) == (w, h)
    assert px == b''.join(r + b'\xff' for r in rows)
