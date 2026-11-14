"""Synthetic car geometry + compressed TPK: no game files required."""
import struct

import pytest

from nfsu2_world_editor.core import bchunk, cars, dxt, meshcodec, solidlist, tpk
from nfsu2_world_editor.core.compress import compress_jdlz, decompress_jdlz
from nfsu2_world_editor.core.writer import ChunkWriter, write_solid_object
from nfsu2_world_editor.core.tpk import bin_hash, CarTexturePack, load_packs, TPK, TPK_INFO, TPK_NAME, TPK_SLOTS, TPK_DATA, TPK_PIXELS


VERTS = [
    (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.2), (0.0, 1.0, 0.2),
]
FACES = [(0, 1, 2), (0, 2, 3)]


def _geometry_file(*names):
    """A solid list (like a tiny GEOMETRY.BIN) with optional trailing chunk."""
    w = ChunkWriter(base=0)
    w.begin(solidlist.SOLID_LIST)
    w.begin(solidlist.SOLID_LIST_HEADER)
    info = bytearray(0x30)
    struct.pack_into('<I', info, 0x0C, len(names))
    w.leaf(solidlist.SOLID_LIST_INFO, bytes(info))
    hs = sorted(bin_hash(n) for n in names)
    w.leaf(solidlist.SOLID_LIST_PAIRS, b''.join(struct.pack('<II', h, 0) for h in hs))
    w.end()
    for n in names:
        write_solid_object(w, n, VERTS, FACES, tex_hashes=(bin_hash('CARSKIN'),),
                           materials=[(0, 6, 0)], model_hash=bin_hash(n))
    w.end()
    # trailing 0x80034020-style empty container + padding, like a real car file
    w.leaf(0, bytes(16))
    w.begin(0x80034020)
    w.leaf(0x00034021, bytes(24))
    w.end()
    return w.getvalue()


def test_car_geometry_no_edit_roundtrip():
    data = _geometry_file('TEST_BASE_A', 'TEST_KIT00_BODY_A', 'TEST_KIT00_HOOD_A',
                          'TEST_KIT00_ENGINE_A', 'TEST_STYLE01_HOOD_A',
                          'TEST_KIT00_FRONT_WHEEL_A')
    car = cars.CarGeometry(data)
    assert car.name == 'TEST'
    assert len(car.parts) == 6
    assert car.rebuild() == data          # no-edit serialize is byte-identical
    car.reencode_meshes()
    once = car.rebuild()
    for p in car.parts:
        p.mesh = meshcodec.decode(p.node)
    car.reencode_meshes()
    assert car.rebuild() == once          # a second encode is stable


def test_stock_kit_hides_engine_and_style():
    data = _geometry_file('TEST_BASE_A', 'TEST_KIT00_BODY_A', 'TEST_KIT00_BODY_B',
                          'TEST_KIT00_ENGINE_A', 'TEST_STYLE01_HOOD_A',
                          'TEST_KIT00_HOOD_A', 'TEST_KIT00_FRONT_WHEEL_A')
    car = cars.CarGeometry(data)
    stock = {p.family: p for p in car.stock_parts('KIT00', 'A')}
    names = sorted(p.name for p in stock.values())
    assert 'TEST_KIT00_ENGINE_A' not in names
    assert 'TEST_STYLE01_HOOD_A' not in names
    assert stock['TEST_BODY'].name == 'TEST_KIT00_BODY_A'  # lod A over B
    assert 'TEST_BASE' in stock and 'TEST_HOOD' in stock
    assert car.wheel_part().name == 'TEST_KIT00_FRONT_WHEEL_A'


def test_part_name_parsing():
    assert cars._kit_family('MIATA_KIT00_FRONT_BUMPER_A') == ('KIT00', 'MIATA_FRONT_BUMPER')
    assert cars._lod_of('MIATA_KIT00_BODY_B') == 'B'
    assert cars._kit_family('MIATA_BASE_A') == (None, 'MIATA_BASE')


def _car_header(key, width, height, comp, rest=120):
    h = bytearray(rest)
    struct.pack_into('<I', h, 0, key)
    struct.pack_into('<HH', h, 0x20, width, height)
    h[0x26] = comp
    return bytes(h)


def _car_tpk_file(key, width, height, fmt, pixels, name=b'CARTEXTURES'):
    """Minimal compressed car TPK with one JDLZ texture."""
    comp_id = tpk.COMP_TO_CAR[fmt]
    header = _car_header(key, width, height, comp_id)
    decoded = pixels + header   # header at the end; P = dec - 120
    # HeaderFromEnd such that P = dec - hfe + 0x88 equals len(pixels)
    # len(decoded) - hfe + 0x88 = len(pixels)  =>  hfe = len(header) + 0x88 - 0? 
    # dec - hfe + 0x88 = len(pixels) = dec - 120  => hfe = 0x88 + 120 - 0 wait
    # dec - hfe + 136 = dec - 120 => -hfe + 136 = -120 => hfe = 256. Yes if header is 120.
    hfe = 256
    blob = compress_jdlz(decoded)
    # Build a pack: info + data, then patch slot offsets after serialize.
    w = ChunkWriter(base=0)
    w.begin(TPK)
    w.begin(TPK_INFO)
    name_payload = bytearray(124)
    name_payload[0:4] = b'\x05\x00\x00\x00'
    name_payload[4:4 + len(name)] = name
    path = b'Global\\Pipeline\\CarTemplateTextures_TEST.tpk'
    name_payload[0x20:0x20 + len(path)] = path
    w.leaf(TPK_NAME, bytes(name_payload))
    w.leaf(tpk.TPK_HASHES, struct.pack('<II', key, 0))
    # placeholder slot; offset patched later
    slot = struct.pack('<6I', key, 0, len(blob), len(decoded), hfe, 0)
    w.leaf(TPK_SLOTS, slot)
    w.end()
    w.begin(TPK_DATA)
    w.leaf(0x33320001, bytes(24))
    w.leaf(TPK_PIXELS, blob)
    w.end()
    w.end()
    data = w.getvalue()
    # find the pixel payload (after alignment filler) and patch AbsoluteOffset
    chunks = bchunk.parse(data)
    pix = chunks[0].find(TPK_DATA).find(TPK_PIXELS)
    # chunk_bytes isn't needed: offset of the JDLZ magic inside the file
    abs_off = data.index(b'JDLZ')
    slots = bytearray(chunks[0].find(TPK_INFO).find(TPK_SLOTS).data)
    struct.pack_into('<I', slots, 4, abs_off)
    # rewrite the slot bytes inside `data`
    # find the slot key and patch the following u32
    # safer: re-serialize after editing the tree
    chunks[0].find(TPK_INFO).find(TPK_SLOTS).data = bytes(slots)
    chunks[0].find(TPK_INFO).find(TPK_SLOTS).dirty = True
    out = bytes(bchunk.serialize(chunks))
    # JDLZ may have moved if dirty rewrite shifted things; patch again if needed
    if out[struct.unpack_from('<I', bytes(slots), 4)[0]:][:4] != b'JDLZ':
        abs_off = out.index(b'JDLZ')
        slots = bytearray(4)  # unused
        # edit tree on reparse
        chunks = bchunk.parse(out)
        raw_slots = bytearray(chunks[0].find(TPK_INFO).find(TPK_SLOTS).data)
        struct.pack_into('<I', raw_slots, 4, abs_off)
        chunks[0].find(TPK_INFO).find(TPK_SLOTS).data = bytes(raw_slots)
        chunks[0].find(TPK_INFO).find(TPK_SLOTS).dirty = True
        out = bytes(bchunk.serialize(chunks))
    return out


def test_car_texture_pack_decode_and_unmodified_serialize():
    w = h = 8
    rgba = b''.join(bytes((x * 30, y * 30, 80, 255)) for y in range(h) for x in range(w))
    pixels = dxt.encode(rgba, dxt.FOURCC_DXT1, w, h)
    key = bin_hash('TEST_SKIN')
    data = _car_tpk_file(key, w, h, dxt.FOURCC_DXT1, pixels)
    packs, _ = load_packs(_AsPath := _write_tmp(data))
    assert len(packs) == 1 and isinstance(packs[0], CarTexturePack)
    t = packs[0].by_hash[key]
    assert t.width == 8 and t.height == 8 and t.format == dxt.FOURCC_DXT1
    out, ww, hh = t.decode()
    assert (ww, hh) == (8, 8) and len(out) == 8 * 8 * 4
    # unmodified serialize of the original file is byte-identical
    assert bytes(bchunk.serialize(bchunk.parse(data))) == data


def _write_tmp(data):
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix='.BIN')
    os.write(fd, data)
    os.close(fd)
    _write_tmp.paths = getattr(_write_tmp, 'paths', [])
    _write_tmp.paths.append(path)
    return path


def test_cli_help():
    from nfsu2_world_editor.cli import cars as cli
    with pytest.raises(SystemExit) as e:
        cli.main(['--help'])
    assert e.value.code == 0


@pytest.mark.skipif(not __import__('os').environ.get('NFSU2_CARS'),
                    reason='set NFSU2_CARS to a CARS/<NAME> folder to run the live round-trip')
def test_live_car_roundtrip():
    import os
    from nfsu2_world_editor.cli import cars as cli
    assert cli.main(['roundtrip', os.environ['NFSU2_CARS'], '--meshes']) == 0
