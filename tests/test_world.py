import pytest

from nfsu2_world_editor.core.chunks import iter_chunks, find
from nfsu2_world_editor.core.world import (section_table, parse_object, section_objects,
                                           build_model_index, iter_scenery_instances,
                                           apply_matrix, is_preplaced, SOLID_OBJECT)
from nfsu2_world_editor.core.writer import (ChunkWriter, write_solid_object, write_section_table,
                                            write_scenery_pack)

# float32-exact values so round-trips compare equal
VERTS = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.5), (10.0, 20.0, 1.0), (0.0, 20.0, 0.25)]
FACES = [(0, 1, 2), (0, 2, 3)]
UVS = [(0.0, 0.0), (1.0, 0.0), (1.0, 2.0), (0.0, 2.0)]
COLS = [(255, 0, 0, 255), (0, 255, 0, 128), (0, 0, 255, 64), (12, 34, 56, 78)]
MATRIX = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 100.0, -50.0, 2.5, 1.0)


def test_section_table_parse():
    entries = [
        dict(tag='C21', num=321, offset=0x1000, size=0x2000, geom_size=0x1800, order=7,
             center=(1500.0, -250.5, 12.0)),
        dict(tag='B26', num=226, offset=0x3000, size=0x80, center=(0.0, 0.0, 0.0)),
    ]
    w = ChunkWriter()
    w.begin(0x80034100 | 0x10)        # any container, the table must be found inside it
    write_section_table(w, entries)
    w.end()
    t = section_table(w.getvalue())
    assert [e['tag'] for e in t] == ['C21', 'B26']
    assert t[0]['num'] == 321 and t[0]['offset'] == 0x1000 and t[0]['size'] == 0x2000
    assert t[0]['geom_size'] == 0x1800 and t[0]['order'] == 7
    assert t[0]['center'] == (1500.0, -250.5, 12.0)
    assert t[1]['geom_size'] == 0x80


@pytest.mark.parametrize('base', [0, 5, 77])
@pytest.mark.parametrize('with_normals', [False, True])
def test_solid_object_round_trip(base, with_normals):
    normals = [(0.0, 0.0, 1.0)] * 4 if with_normals else None
    w = ChunkWriter(base=base)
    write_solid_object(w, 'TRN_TEST', VERTS, FACES, uvs=UVS, cols=COLS, normals=normals,
                       tex_hashes=(0xDEADBEEF, 0x12345678), materials=[(0, 3, 1), (3, 3, 0)],
                       model_hash=0xCAFEF00D, matrix=MATRIX)
    buf = bytes(base) + w.getvalue()
    cid, d, s = next(iter_chunks(buf, base))
    assert cid == SOLID_OBJECT
    o = parse_object(buf, d, s)
    assert o['name'] == 'TRN_TEST'
    assert o['hash'] == 0xCAFEF00D
    assert o['matrix'] == MATRIX
    assert o['tex'] == [0xDEADBEEF, 0x12345678]
    assert o['stride'] == (36 if with_normals else 24)
    assert [tuple(v) for v in o['verts']] == VERTS
    assert o['uvs'] == UVS
    assert o['cols'] == COLS
    assert o['faces'] == FACES
    assert o['mats'] == [(0, 3, 1), (3, 3, 0)]
    assert o['face_mat'] == [0, 1]
    assert (o['normals'] is None) == (not with_normals)
    if with_normals:
        assert o['normals'] == normals


def test_degenerate_and_out_of_range_triangles_dropped():
    w = ChunkWriter()
    write_solid_object(w, 'X', VERTS, [(0, 1, 2), (1, 1, 2), (0, 2, 9), (0, 2, 3)])
    buf = w.getvalue()
    _, d, s = next(iter_chunks(buf))
    assert parse_object(buf, d, s)['faces'] == [(0, 1, 2), (0, 2, 3)]


def test_object_without_mesh_is_none():
    w = ChunkWriter()
    w.begin(SOLID_OBJECT)
    w.leaf(0x00134011, bytes(0xA4) + b'EMPTY\0')
    w.end()
    buf = w.getvalue()
    _, d, s = next(iter_chunks(buf))
    assert parse_object(buf, d, s) is None


def _section_with_scenery():
    w = ChunkWriter()
    w.begin(0x80034000 | 0x10)
    write_solid_object(w, 'XB_TREE', VERTS, FACES, model_hash=0x1111)
    write_solid_object(w, 'TRN_ROAD', VERTS, FACES, model_hash=0x2222, matrix=MATRIX)
    rot = (0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
    write_scenery_pack(w, [0x1111, 0x9999], [(0, (5.0, 6.0, 7.0), rot), (1, (0.0, 0.0, 0.0), rot),
                                             (5, (0.0, 0.0, 0.0), rot)])
    w.end()
    return w.getvalue(), rot


def test_section_objects_and_model_index():
    buf, _ = _section_with_scenery()
    objs = section_objects(buf, 0, len(buf))
    assert [o['name'] for o in objs] == ['XB_TREE', 'TRN_ROAD']
    idx = build_model_index(buf, [dict(offset=0, size=len(buf))])
    assert set(idx) == {0x1111, 0x2222}
    assert parse_object(buf, *idx[0x2222])['name'] == 'TRN_ROAD'


def test_scenery_instances():
    buf, rot = _section_with_scenery()
    inst = list(iter_scenery_instances(buf, 0, len(buf)))
    assert [h for h, _, _ in inst] == [0x1111, 0x9999]   # out-of-range info index skipped
    h, m, rec = inst[0]
    assert m[12:15] == (5.0, 6.0, 7.0)
    assert m[0:3] == rot[0:3] and m[4:7] == rot[3:6] and m[8:11] == rot[6:9]
    assert apply_matrix(m, (1.0, 0.0, 0.0)) == (5.0, 7.0, 7.0)


def test_is_preplaced():
    assert not is_preplaced({'verts': VERTS})
    far = [(x + 5000.0, y, z) for x, y, z in VERTS]
    assert is_preplaced({'verts': far})
