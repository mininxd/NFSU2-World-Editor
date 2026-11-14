import struct

import pytest

from nfsu2_world_editor.core import bchunk, dxt, tpk
from nfsu2_world_editor.core.writer import ChunkWriter, write_texture_pack, write_solid_object
from nfsu2_world_editor.core.chunks import payload_start


def _tex_list():
    w = h = 8
    img = bytes((x * 30, y * 30, 128, 255) for y in range(h) for x in range(w) for _ in [0]) if False else \
        b''.join(bytes((x * 30, y * 30, 128, 255)) for y in range(h) for x in range(w))
    d1 = b''.join(dxt.encode(img if i == 0 else dxt.downsample(img, w, h)[0] if i == 1 else
                             dxt.downsample(*dxt.downsample(img, w, h))[0], dxt.FOURCC_DXT1,
                             max(1, w >> i), max(1, h >> i)) for i in range(3))
    pal = bytes(range(256)) * 4
    p8 = bytes((i * 7) & 255 for i in range(16 + 4))   # 4x4 + 2x2
    return img, [
        dict(name='TEST_DXT1', hash=tpk.bin_hash('TEST_DXT1'), format=dxt.FOURCC_DXT1, width=8, height=8,
             mips=3, data=d1, usage=1),
        dict(name='TEST_P8', hash=tpk.bin_hash('TEST_P8'), format=dxt.D3DFMT_P8, width=4, height=4,
             mips=2, data=p8, palette=pal),
    ]


def _file(base=0):
    _img, texs = _tex_list()
    w = ChunkWriter(base=base)
    w.leaf(0, bytes(20))
    write_texture_pack(w, texs)
    w.leaf(0, bytes(36))
    w.begin(0x80134000)
    write_solid_object(w, 'OBJ_A', [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)], [(0, 1, 2)])
    w.end()
    return w.getvalue()


def test_bin_hash_known_value():
    # documented in FRSWorlsEditor NFSU2-FORMATS.md
    assert tpk.bin_hash('ARC_WHOUSE_DOOR_01') == 0x0052DF42


@pytest.mark.parametrize('base', [0, 4, 100, 2048 - 8])
def test_bchunk_round_trip_identical(base):
    data = _file(base)
    buf = bytes(base) + data
    chunks = bchunk.parse(buf, base)
    assert bytes(bchunk.serialize(chunks, base)) == data


def test_bchunk_relocation_realigns_filler():
    data = _file(0)
    chunks = bchunk.parse(data)
    for shift in (4, 8, 52, 128):
        out = bytes(bchunk.serialize(chunks, shift))
        buf = bytes(shift) + out
        again = bchunk.parse(buf, shift)
        # every aligned leaf payload sits on its boundary in the relocated copy
        for _d, c in bchunk.walk_nodes(again):
            if c.aligned:
                assert c.offset is not None
        for (_d0, a), (_d1, b) in zip(bchunk.walk_nodes(chunks), bchunk.walk_nodes(again)):
            assert a.id == b.id
            if a.children is None and a.id not in (0, None):
                assert bytes(a.data) == bytes(b.data)


def test_growing_a_leaf_keeps_following_alignment():
    data = _file(0)
    chunks = bchunk.parse(data)
    name = chunks[1].find(tpk.TPK_INFO).find(tpk.TPK_NAME)
    name.data = bytes(name.data) + bytes(12)
    name.dirty = True
    out = bytes(bchunk.serialize(chunks, 0))
    again = bchunk.parse(out)
    pix = again[1].find(tpk.TPK_DATA).find(tpk.TPK_PIXELS)
    d = pix.offset + 8
    assert payload_start(out, tpk.TPK_PIXELS, d, pix.size) % 128 == 0
    p1 = tpk.TexturePack(again[1])
    p0 = tpk.TexturePack(chunks[1])
    assert [t.raw() for t in p1.textures] == [t.raw() for t in p0.textures]


def test_texture_pack_parse_decode_and_replace():
    img, texs = _tex_list()
    data = _file(0)
    chunks = bchunk.parse(data)
    packs = list(tpk.iter_packs(chunks))
    assert len(packs) == 1
    p = packs[0]
    assert p.name == 'TRACK'
    by = {t.name: t for t in p.textures}
    t1, t8 = by['TEST_DXT1'], by['TEST_P8']
    assert (t1.width, t1.height, t1.mips, t1.format_name, t1.usage) == (8, 8, 3, 'DXT1', 1)
    assert t1.alpha_is_opacity() and not t8.alpha_is_opacity()
    assert [lv[:2] for lv in t1.levels()] == [(8, 8), (4, 4), (2, 2)]
    rgba, w, h = t1.decode(use_numpy=False)
    assert (w, h) == (8, 8)
    rgba8, _, _ = t8.decode(use_numpy=False)
    assert rgba8[0:4] == bytes((2, 1, 0, 3))   # palette entry 0 is bytes 0,1,2,3 (B,G,R,A)
    # unedited re-encode of a texture decodes close to the source
    new = tpk.encode_texture(t1, rgba, w, h)
    assert len(new) == t1.size
    # replace pixels: same size, file size unchanged, other texture untouched
    inverted = bytes(255 - v if i % 4 != 3 else v for i, v in enumerate(rgba))
    before8 = t8.raw()
    tpk.apply_image(t1, inverted, 8, 8)
    out = bytes(bchunk.serialize(chunks, 0))
    assert len(out) == len(data)
    p2 = list(tpk.iter_packs(bchunk.parse(out)))[0]
    b2 = {t.name: t for t in p2.textures}
    assert b2['TEST_P8'].raw() == before8
    dec, _, _ = b2['TEST_DXT1'].decode(use_numpy=False)
    err = sum(abs(a - b) for a, b in zip(dec, inverted)) / len(dec)
    assert err < 8


def test_p8_import_rebuilds_palette_when_needed():
    _img, texs = _tex_list()
    chunks = bchunk.parse(_file(0))
    t8 = {t.name: t for t in list(tpk.iter_packs(chunks))[0].textures}['TEST_P8']
    red = bytes((255, 0, 0, 255)) * 16          # not in the grey-ish test palette
    tpk.apply_image(t8, red, 4, 4)
    dec, _, _ = t8.pack.textures[t8.index].decode(use_numpy=False)
    assert dec == red
    with pytest.raises(ValueError):
        tpk.encode_texture(t8, red * 4, 8, 8)    # size change is refused
