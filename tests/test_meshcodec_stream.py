import struct

import pytest

from nfsu2_world_editor.core import bchunk, meshcodec, solidlist
from nfsu2_world_editor.core.stream import Region
from nfsu2_world_editor.core.world import section_table, section_objects
from nfsu2_world_editor.core.writer import ChunkWriter, write_solid_object, write_section_table

VERTS = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.5), (10.0, 20.0, 1.0), (0.0, 20.0, 0.25)]
FACES = [(0, 1, 2), (0, 2, 3)]


def _section(base, name, n_obj=2):
    """A solid list with header (count + hash pairs) like the shipped sections."""
    w = ChunkWriter(base=base)
    w.leaf(0, bytes(12))
    w.begin(solidlist.SOLID_LIST)
    w.begin(solidlist.SOLID_LIST_HEADER)
    info = bytearray(0x30)
    struct.pack_into('<I', info, 0x0C, n_obj)
    w.leaf(solidlist.SOLID_LIST_INFO, bytes(info))
    from nfsu2_world_editor.core.tpk import bin_hash
    hs = sorted(bin_hash('%s_%d' % (name, i)) for i in range(n_obj))
    w.leaf(solidlist.SOLID_LIST_PAIRS, b''.join(struct.pack('<II', h, 0) for h in hs))
    w.end()
    for i in range(n_obj):
        write_solid_object(w, '%s_%d' % (name, i), VERTS, FACES, tex_hashes=(0x1234, 0x5678),
                           materials=[(0, 3, 0), (3, 3, 1)], model_hash=bin_hash('%s_%d' % (name, i)))
    w.end()
    return w.getvalue()


def _region():
    """Three sections on 2 KiB boundaries separated by zero padding chunks."""
    stream = bytearray()
    entries = []
    for i, tag in enumerate(('A1', 'B2', 'C3')):
        off = len(stream)
        sec = _section(off, 'OBJ_%s' % tag)
        stream += sec
        entries.append(dict(tag=tag, num=i + 1, offset=off, size=len(sec), geom_size=len(sec)))
        pad = (-(len(stream) + 8)) % 2048
        stream += struct.pack('<II', 0, pad) + bytes(pad)
    w = ChunkWriter()
    w.leaf(0x00034112, bytes(16))
    write_section_table(w, entries)
    return bytes(stream), w.getvalue()


def test_region_no_edit_round_trip():
    s, l4 = _region()
    r = Region(s, l4, 'L9RA')
    out_s, out_l4 = r.build()
    assert bytes(out_s) == s and bytes(out_l4) == l4


def _objects(r, tag):
    return [o for c in r.section_chunks(tag) if c.children for o in c.find_all(meshcodec.SOLID_OBJECT)]


def test_mesh_decode_encode_idempotent_and_edit():
    s, l4 = _region()
    r = Region(s, l4)
    o = _objects(r, 'B2')[0]
    m = meshcodec.decode(o)
    assert m.verts == VERTS and m.triangles() == FACES and m.textures == [0x1234, 0x5678]
    meshcodec.encode(o, m)
    once = bchunk.chunk_bytes(o, o.offset)
    meshcodec.encode(o, meshcodec.decode(o))
    assert bchunk.chunk_bytes(o, o.offset) == once           # stable after the first encode
    # edit: lift by 3 m, add a vertex + triangle in a new material with a new texture
    m = meshcodec.decode(o)
    m.verts = [(x, y, z + 3.0) for x, y, z in m.verts] + [(5.0, 30.0, 3.0)]
    m.colors.append(0xFF808080)
    m.uvs.append((0.5, 1.5))
    m.indices += [2, 4, 3]
    m.textures.append(0x9ABC)
    rec = m.materials[0][3]
    m.materials.append((6, 3, 2, rec))
    meshcodec.encode(o, m)
    out_s, out_l4 = r.build()
    table = {e['tag']: e for e in section_table(bytes(out_l4))}
    objs = section_objects(bytes(out_s), table['B2']['offset'], table['B2']['size'])
    ob = [x for x in objs if x['name'] == 'OBJ_B2_0'][0]
    assert len(ob['verts']) == 5 and len(ob['faces']) == 3
    assert ob['verts'][0][2] == pytest.approx(3.0)
    assert ob['tex'] == [0x1234, 0x5678, 0x9ABC]
    assert [mm[:2] for mm in ob['mats']] == [(0, 3), (3, 3), (6, 3)]
    # header triangle count / texture count / bounds follow the mesh
    hd = bytes(o.find(meshcodec.SOLID_HEADER).data)
    assert struct.unpack_from('<I', hd, 0x14)[0] == 3 and hd[0x19] == 3
    lo = struct.unpack_from('<3f', hd, 0x20)
    hi = struct.unpack_from('<3f', hd, 0x30)
    assert lo[2] == pytest.approx(3.0 - 0.01) and hi[1] == pytest.approx(30.0 + 0.01)


def test_section_growth_moves_later_sections_by_2k():
    s, l4 = _region()
    r = Region(s, l4)
    before = {e['tag']: e for e in section_table(l4)}
    o = _objects(r, 'A1')[1]
    m = meshcodec.decode(o)
    # ~3 KB more vertices -> section A1 crosses a 2 KiB boundary
    for i in range(150):
        m.verts.append((float(i), 1.0, 2.0))
        m.colors.append(0xFFFFFFFF)
        m.uvs.append((0.0, 0.0))
    meshcodec.encode(o, m)
    out_s, out_l4 = r.build()
    after = {e['tag']: e for e in section_table(bytes(out_l4))}
    assert after['A1']['offset'] == before['A1']['offset']
    assert after['A1']['size'] > before['A1']['size']
    for tag in ('B2', 'C3'):
        e0, e1 = before[tag], after[tag]
        shift = e1['offset'] - e0['offset']
        assert shift > 0 and shift % 2048 == 0
        assert bytes(out_s[e1['offset']:e1['offset'] + e1['size']]) == s[e0['offset']:e0['offset'] + e0['size']]
    # vertex buffers stay 128-byte aligned after the move
    objs = section_objects(bytes(out_s), after['A1']['offset'], after['A1']['size'])
    assert len(objs[1]['verts']) == 154


def test_add_object_registers_in_solid_list():
    s, l4 = _region()
    r = Region(s, l4)
    node = solidlist.add_object(r, 'C3', 'NFS_NEW_ROAD')
    m = meshcodec.decode(node)
    m.verts = [(0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 4.0, 0.0)]
    m.colors, m.uvs, m.indices = [0xFF808080] * 3, [(0.0, 0.0)] * 3, [0, 1, 2]
    m.materials = [(0, 3, 0, m.materials[0][3])]
    m.textures = [0x1234]
    m.matrix = None
    meshcodec.encode(node, m)
    out_s, out_l4 = r.build()
    e = {x['tag']: x for x in section_table(bytes(out_l4))}['C3']
    names = [o['name'] for o in section_objects(bytes(out_s), e['offset'], e['size'])]
    assert 'NFS_NEW_ROAD' in names and len(names) == 3
    # objects stay sorted by hash like the shipped lists
    from nfsu2_world_editor.core.tpk import bin_hash
    assert [bin_hash(n) for n in names] == sorted(bin_hash(n) for n in names)
    r2 = Region(bytes(out_s), bytes(out_l4))
    sl = [c for c in r2.section_chunks('C3') if c.id == solidlist.SOLID_LIST][0]
    hdr = sl.find(solidlist.SOLID_LIST_HEADER)
    assert struct.unpack_from('<I', hdr.find(solidlist.SOLID_LIST_INFO).data, 0x0C)[0] == 3
    pairs = bytes(hdr.find(solidlist.SOLID_LIST_PAIRS).data)
    hs = [struct.unpack_from('<I', pairs, q)[0] for q in range(0, len(pairs), 8)]
    assert hs == sorted(hs) and len(hs) == 3
    with pytest.raises(ValueError):
        solidlist.add_object(r2, 'C3', 'NFS_NEW_ROAD')     # duplicate name/hash


def test_encode_rejects_bad_meshes():
    s, l4 = _region()
    o = _objects(Region(s, l4), 'A1')[0]
    m = meshcodec.decode(o)
    m.indices = [0, 1]
    with pytest.raises(ValueError):
        meshcodec.encode(o, m)
    m = meshcodec.decode(o)
    m.indices = [0, 1, 99]
    with pytest.raises(ValueError):
        meshcodec.encode(o, m)
