import math
import random

import pytest

from nfsu2_world_editor.core import dxt


def gradient(w, h, alpha=False):
    px = bytearray()
    for y in range(h):
        for x in range(w):
            px += bytes((x * 255 // max(1, w - 1), y * 255 // max(1, h - 1), (x + y) * 4 & 255,
                         (x * 6 + y * 3) % 256 if alpha else 255))
    return bytes(px)


def psnr(a, b, channels=(0, 1, 2)):
    se = n = 0
    for i in range(0, len(a), 4):
        for k in channels:
            se += (a[i + k] - b[i + k]) ** 2
            n += 1
    return 99.0 if se == 0 else 10 * math.log10(255 ** 2 * n / se)


@pytest.mark.parametrize('fmt', [dxt.FOURCC_DXT1, dxt.FOURCC_DXT3, dxt.FOURCC_DXT5])
def test_dxt_round_trip_quality(fmt):
    w, h = 32, 16
    img = gradient(w, h, alpha=fmt != dxt.FOURCC_DXT1)
    enc = dxt.encode(img, fmt, w, h)
    assert len(enc) == dxt.level_size(fmt, w, h)
    dec = dxt.decode(enc, fmt, w, h, use_numpy=False)
    assert len(dec) == w * h * 4
    assert psnr(img, dec) > 30
    if fmt == dxt.FOURCC_DXT3:
        assert psnr(img, dec, (3,)) > 28     # 4-bit alpha
    if fmt == dxt.FOURCC_DXT5:
        assert psnr(img, dec, (3,)) > 30


@pytest.mark.parametrize('fmt', [dxt.FOURCC_DXT1, dxt.FOURCC_DXT3, dxt.FOURCC_DXT5])
def test_solid_colour_blocks_are_exact(fmt):
    # colours exactly representable in RGB565
    img = bytes((247, 251, 0, 255)) * 64
    dec = dxt.decode(dxt.encode(img, fmt, 8, 8), fmt, 8, 8, use_numpy=False)
    assert dec == img


def test_dxt1_punch_through_alpha():
    w = h = 8
    img = bytearray(gradient(w, h))
    for i in range(0, w * h * 4, 8):
        img[i + 3] = 0
    dec = dxt.decode(dxt.encode(bytes(img), dxt.FOURCC_DXT1, w, h, dxt1_alpha=True),
                     dxt.FOURCC_DXT1, w, h, use_numpy=False)
    assert [dec[i + 3] for i in range(0, len(dec), 4)] == [img[i + 3] for i in range(0, len(img), 4)]


def test_known_dxt1_block():
    # c0 = pure red (0xF800), c1 = pure blue (0x001F), selectors 0,1,2,3 in the first row
    blk = bytes((0x00, 0xF8, 0x1F, 0x00, 0b11100100, 0, 0, 0))
    dec = dxt.decode(blk, dxt.FOURCC_DXT1, 4, 4, use_numpy=False)
    px = [tuple(dec[i:i + 4]) for i in range(0, 16, 4)]
    assert px == [(255, 0, 0, 255), (0, 0, 255, 255), (170, 0, 85, 255), (85, 0, 170, 255)]


def test_non_multiple_of_four_and_tiny_mips():
    for w, h in ((1, 1), (2, 2), (6, 3), (4, 1)):
        img = gradient(w, h)
        for fmt in (dxt.FOURCC_DXT1, dxt.FOURCC_DXT3):
            enc = dxt.encode(img, fmt, w, h)
            assert len(enc) == dxt.level_size(fmt, w, h)
            assert len(dxt.decode(enc, fmt, w, h, use_numpy=False)) == w * h * 4


@pytest.mark.skipif(not dxt.HAVE_NUMPY, reason='numpy not installed')
@pytest.mark.parametrize('fmt', [dxt.FOURCC_DXT1, dxt.FOURCC_DXT3, dxt.FOURCC_DXT5])
def test_numpy_path_matches_pure_python(fmt):
    rnd = random.Random(1)
    w, h = 12, 8
    data = bytes(rnd.randrange(256) for _ in range(dxt.level_size(fmt, w, h)))
    assert dxt.decode(data, fmt, w, h, use_numpy=True) == dxt.decode(data, fmt, w, h, use_numpy=False)


def test_p8_and_argb():
    rnd = random.Random(2)
    pal = bytes(rnd.randrange(256) for _ in range(1024))
    idx = bytes(rnd.randrange(256) for _ in range(64))
    for np_ in (False, True):
        dec = dxt.decode(idx, dxt.D3DFMT_P8, 8, 8, pal, use_numpy=np_ and dxt.HAVE_NUMPY)
        # palette is D3DCOLOR (B,G,R,A)
        assert dec[0:4] == bytes((pal[4 * idx[0] + 2], pal[4 * idx[0] + 1], pal[4 * idx[0]], pal[4 * idx[0] + 3]))
    # P8 encode with the same palette maps every pixel back to an identical colour
    re = dxt.encode(dec, dxt.D3DFMT_P8, 8, 8, palette=pal)
    assert dxt.decode(re, dxt.D3DFMT_P8, 8, 8, pal, use_numpy=False) == dec
    img = gradient(5, 3, alpha=True)
    assert dxt.decode(dxt.encode(img, dxt.D3DFMT_A8R8G8B8, 5, 3), dxt.D3DFMT_A8R8G8B8, 5, 3,
                      use_numpy=False) == img


def test_build_palette_and_downsample():
    img = gradient(16, 16)
    pal = dxt.build_palette(img)
    assert len(pal) == 1024
    assert dxt.palette_error(img, pal) < 20
    small, w2, h2 = dxt.downsample(bytes((10, 20, 30, 40)) * 16, 4, 4)
    assert (w2, h2) == (2, 2) and small == bytes((10, 20, 30, 40)) * 4
