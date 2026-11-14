"""Collision / visibility / scenery / routes / road builder.

Synthetic tests always run; the ``TRACKS`` round trips run only when
``NFSU2_TRACKS`` points at a game TRACKS folder (never shipped with the repo).
"""
import os
import struct
from pathlib import Path

import pytest

from nfsu2_world_editor.core import bchunk, collision as C, road_builder as RB, visibility as V
from nfsu2_world_editor.core.routes import RoutesFile
from nfsu2_world_editor.core.scenery import SceneryPack, PACK as SCENERY_PACK, INSTANCES, NODE_SIZE

TRACKS = os.environ.get('NFSU2_TRACKS')
needs_tracks = pytest.mark.skipif(not TRACKS or not Path(TRACKS).is_dir(), reason='NFSU2_TRACKS not set')


# ------------------------------------------------------------------ collision

def test_extreme_code_and_ground_poly():
    q = C.make_ground([(0, 0, 10), (8, 0, 10), (8, 8, 12), (0, 8, 12)])
    assert q.flags & 0x10 and q.surface == 1
    assert q.base == 11
    assert [round(c[2], 2) for c in q.corners()] == [10, 10, 12, 12]
    # -x: corner 0 (tie with 3 -> later = 3), -y: 1 (tie 0/1 -> 1), +x: 2, +y: 3
    assert C.extreme_code([0, 8, 8, 0], [0, 0, 8, 8]) == (3 | 1 << 2 | 2 << 4 | 3 << 6)


def test_barrier_and_pack_roundtrip():
    p = C.CollisionPack()
    p.section = 521
    p.header_raw = bytes(48)
    polys = [C.make_ground([(0, 0, 1), (40, 0, 1), (40, 40, 1), (0, 40, 1)])]
    polys += C.make_wall((0, 0), (40, 0))
    p.add_polygons(polys)
    assert p.width >= 2 and p.height >= 2
    raw = p.to_bytes()
    q = C.CollisionPack.parse(raw[8:])
    assert q.to_bytes() == raw
    assert all(b.is_barrier for b in q.polys[1:])
    # every poly is referenced and every cell list is contiguous
    assert {r[0] for r in q.refs} == set(range(len(q.polys)))
    off = 0
    for first, n in q.cells:
        assert first == off
        off += n


def test_add_polygons_keeps_shipped_lists():
    p = C.CollisionPack()
    p.header_raw = bytes(48)
    p.add_polygons([C.make_ground([(0, 0, 0), (30, 0, 0), (30, 30, 0), (0, 30, 0)])])
    before = p.cell_lists()
    w0, o0 = p.width, p.origin
    p.add_polygons([C.make_ground([(-70, -70, 0), (-40, -70, 0), (-40, -40, 0), (-70, -40, 0)])])
    dx = int((o0[0] - p.origin[0]) / C.CELL)
    dy = int((o0[1] - p.origin[1]) / C.CELL)
    after = p.cell_lists()
    for i, lst in enumerate(before):
        assert after[(i // w0 + dy) * p.width + i % w0 + dx][:len(lst)] == lst


# ----------------------------------------------------------------- visibility

def _manager_payload():
    b1 = V.Boundary(101, [(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)])
    b2 = V.Boundary(102, [(100, 0), (200, 0), (200, 100), (100, 0)])
    vis = bytearray(164)
    vis[:8] = V.DEFAULT_LINKS
    struct.pack_into('<HHII3H', vis, 8, 101, 0, 0, 3, 101, 102, 521)
    parts = [(V.HEADER, struct.pack('<IIH', 20, 259, 0)),
             (V.BOUNDARIES, b1.pack() + b2.pack()),
             (V.VISIBLE, bytes(vis))]
    return b''.join(struct.pack('<II', c, len(d)) + d for c, d in parts)


def test_visibility_roundtrip_and_queries():
    pl = _manager_payload()
    vm = V.VisibilityManager(pl)
    assert vm.payload() == pl
    assert vm.drivable_at(50, 50) == [101]
    assert vm.drivable_at(150, 20) == []          # 102 has no visible list
    vl = vm.visible_list(101)
    assert vl.sections == [101, 102, 521]
    vl.sections.append(522)
    vm2 = V.VisibilityManager(vm.payload())
    assert vm2.visible_list(101).sections[-1] == 522


# --------------------------------------------------------------- road builder

def test_profile_grade_and_mesh_winding():
    center2 = RB.resample([(0, 0), (0, 400)], 4.0)
    st = [p[2] for p in center2]
    zs = RB.profile(st, [(0, 50.0), (st[-1], 35.0)], max_grade=0.06)
    assert abs(zs[0] - 50) < 1e-6 and abs(zs[-1] - 35) < 1e-6
    assert max(abs(zs[i + 1] - zs[i]) / 4.0 for i in range(len(zs) - 1)) <= 0.06 + 1e-6
    center = [(x, y, z) for (x, y, _), z in zip(center2, zs)]
    parts = RB.road_mesh(center, 7.0)
    road = parts['asphalt']
    # road top faces are counter-clockwise seen from above (normal +z)
    assert road.faces
    for a, b, c in road.faces:
        pa, pb, pc = (road.verts[i] for i in (a, b, c))
        nz = (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (pc[0] - pa[0])
        assert nz > 0
    polys = RB.road_collision(center, 7.0)
    assert any(q.is_barrier for q in polys) and any(not q.is_barrier for q in polys)


# ------------------------------------------------------------------- routes

def _route(rid, pts):
    hdr = bytearray(136)
    hdr[:8] = V.DEFAULT_LINKS
    struct.pack_into('<HH', hdr, 8, 1, rid)
    hdr[16:30] = b'TrackRoutesA10'
    struct.pack_into('<HH', hdr, 52, len(pts), len(pts))
    body = b''
    for x, y in pts:
        s = bytearray(56)
        struct.pack_into('<3f', s, 4, x, y, 0.0)
        struct.pack_into('<HH', s, 24, 2560, 2048)
        body += bytes(s)
    return bytes(hdr) + body + b'\x06\x06\0\0'


def test_routes_roundtrip():
    routes = _route(101, [(0, 0), (10, 0)]) + _route(102, [(10, 0), (10, 20), (10, 40)])
    conn = b'\0' * 4 + struct.pack('<I4H3I4H', 0, 1, 0, 101, 102, 0, 0, 0, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF)
    data = struct.pack('<II', 0x34121, len(routes)) + routes + struct.pack('<II', 0x34122, len(conn)) + conn
    f = RoutesFile.parse(data)
    assert f.to_bytes() == data
    assert [r.route_id for r in f.routes] == [101, 102]
    assert f.routes[1].xyz(2) == (10.0, 40.0, 0.0)
    assert f.routes[0].widths(0) == (10.0, 8.0)
    assert f.connections[0].primary == ((101, 1), (102, 0))
    assert f.nearest_point(9, 1)[1:] == (101, 1)


# ------------------------------------------------------- game data round trips

def _region_file(name):
    for p in Path(TRACKS).iterdir():
        if p.name.upper() == name:
            return p
    pytest.skip('%s not found' % name)


@needs_tracks
def test_tracks_collision_roundtrip():
    sm = _region_file('STREAML4RA.BUN').read_bytes()
    n = 0
    for off, total in C.find_packs(sm):
        pack = C.CollisionPack.parse(sm[off + 8:off + total])
        assert pack.to_bytes() == sm[off:off + total]
        n += 1
    assert n > 100


@needs_tracks
def test_tracks_visibility_roundtrip():
    l4 = _region_file('L4RA.BUN').read_bytes()
    off, total = V.find_manager(l4)
    vm = V.VisibilityManager(l4[off + 8:off + total])
    assert vm.to_bytes() == l4[off:off + total]
    assert vm.boundaries and vm.visible


@needs_tracks
def test_tracks_scenery_trees():
    sm = _region_file('STREAML4RA.BUN').read_bytes()
    nodes = [n for _, n in bchunk.walk_nodes(bchunk.parse(sm)) if n.id == SCENERY_PACK]
    assert nodes
    for n in nodes[:80]:
        sp = SceneryPack(n)
        assert sp.validate()


@needs_tracks
def test_tracks_routes_roundtrip():
    files = sorted(Path(TRACKS).glob('ROUTES*/Routes*.bin'))
    if not files:
        pytest.skip('no Routes*.bin')
    for p in files:
        d = p.read_bytes()
        assert RoutesFile.parse(d).to_bytes() == d, p.name


# ---------------------------------------------------------------- world edit

def test_ground_from_triangles_orientation_and_walls():
    from nfsu2_world_editor.core import world_edit as WE
    flat_cw = ((0, 0, 5), (0, 10, 5), (10, 0, 5))          # clockwise in plan
    wall = ((0, 0, 0), (10, 0, 0), (10, 0, 10))
    polys = WE.ground_from_triangles([flat_cw, wall])
    assert len(polys) == 1 and polys[0].surface == 1
    xs, ys = polys[0].xs[:3], polys[0].ys[:3]
    area2 = (xs[1] - xs[0]) * (ys[2] - ys[0]) - (ys[1] - ys[0]) * (xs[2] - xs[0])
    assert area2 > 0                                       # stored counter-clockwise
    with_walls = WE.ground_from_triangles([flat_cw, wall], walls=True)
    assert any(q.is_barrier for q in with_walls)


@needs_tracks
def test_tracks_instance_writeback_and_placement():
    from nfsu2_world_editor.core import world_edit as WE
    from nfsu2_world_editor.core.stream import Region
    reg = Region.open(TRACKS, 'L4RA')
    sp = WE.own_scenery_pack(reg, 'C22')
    assert sp is not None and sp.validate()
    i = 0
    rec = sp.instance(i)
    st = WE._payload_start(sp.node.find(INSTANCES))
    sp2, k = WE.find_instance(reg, 'C22', st + 64 * i)
    assert sp2.section == sp.section and k == i
    m = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0) + tuple(x + 5.0 for x in rec['t']) + (1.0,)
    WE.set_instance_transform(sp, i, m, ((-1, -1, -1), (1, 1, 1)))
    WE.place_world_model(sp, WE.trn_template(sp), 'TRN_TEST', 0x1234, [(0, 0, 0), (4, 4, 1)], 2)
    sp.commit()
    assert sp.validate()
    assert sp.instance(i)['t'] == tuple(x + 5.0 for x in rec['t'])
    stream, _ = reg.build()
    assert bchunk.serialize(bchunk.parse(stream)) == stream
