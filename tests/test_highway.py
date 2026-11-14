"""Highway / island geometry, budgeted boundaries, padding that keeps offsets.

Synthetic tests always run; the build check runs only with ``NFSU2_TRACKS``.
"""
import json
import math
import os
import struct
from pathlib import Path

import pytest

from nfsu2_world_editor.core import bchunk, highway as HW, road_builder as RB, road_mod as RM

TRACKS = os.environ.get('NFSU2_TRACKS')
needs_tracks = pytest.mark.skipif(not TRACKS or not Path(TRACKS).is_dir(), reason='NFSU2_TRACKS not set')


def _line(n=30, step=8.0, z=5.0):
    return [(0.0, i * step, z) for i in range(n)]


def test_highway_mesh_width_and_winding():
    c = _line()
    parts = HW.highway_mesh(c, [False] * len(c))
    xs = [v[0] for p in parts for v in p.verts if abs(v[2] - 5.0) < 1e-6]
    assert math.isclose(min(xs), -HW.HALF) and math.isclose(max(xs), HW.HALF)
    slots = {p.slot for p in parts}
    assert {HW.T_YS, HW.T_WD, HW.T_WS, HW.T_NL, HW.T_CONC} <= slots
    for p in parts:
        if p.double:
            continue
        for a, b, cc in p.faces:            # single-sided road faces: CCW from above
            va, vb, vc = p.verts[a], p.verts[b], p.verts[cc]
            assert (vb[0] - va[0]) * (vc[1] - va[1]) - (vb[1] - va[1]) * (vc[0] - va[0]) > 0


def test_highway_land_rows_get_sidewalks_and_no_edge_walls():
    pytest.importorskip('shapely')
    c = _line()
    land = [True] * len(c)
    parts = HW.highway_mesh(c, land)
    walk = [p for p in parts if p.slot == HW.T_WALK]
    assert walk and max(abs(v[0]) for v in walk[0].verts) == pytest.approx(HW.HALF + HW.SIDEWALK)
    polys = HW.highway_collision(c, land, median_end=(1e9, 1e9))
    assert polys and all(not q.is_barrier for q in polys)
    bridge = HW.highway_collision(c, [False] * len(c), open_ends=(0, 0), median_end=(1e9, 1e9))
    assert any(q.is_barrier for q in bridge)


def test_organic_island_contains_keep_and_terrain():
    pytest.importorskip('shapely')
    from shapely.geometry import LineString
    tri = [(0, 0), (400, 0), (200, 500)]
    road = [(200.0, y, 8.5) for y in range(60, 420, 8)]
    corridor = LineString([p[:2] for p in road]).buffer(HW.HALF + HW.SIDEWALK, cap_style=2)
    keep = corridor.buffer(30)
    coast = HW.organic_island(tri, keep)
    assert coast.is_valid and coast.contains(keep)
    parts, ground, walls = HW.island_terrain(coast, road, 8.45, corridor)
    assert parts and ground and walls
    zs = [v[2] for p in parts for v in p.verts]
    assert min(zs) == pytest.approx(-3.0) and max(zs) < 8.45 + 6.5


def test_fit_boundaries_keeps_point_budget():
    pytest.importorskip('shapely')
    from nfsu2_world_editor.core.visibility import Boundary

    class VL:
        def __init__(self, s):
            self.section, self.sections = s, [s]

    class VM:
        def __init__(self):
            self.boundaries = [Boundary(1, [(0, 0), (100, 0), (100, 100), (0, 100)]),
                               Boundary(2, [(100, 0), (200, 0), (200, 100), (100, 100)])]
            self.visible = [VL(1), VL(2)]

        def boundary(self, s):
            return next(b for b in self.boundaries if b.section == s)

        def drivable_at(self, x, y):
            return [b.section for b in self.boundaries if b.contains(x, y)]

    vm = VM()
    # a road leaving square 1 to the west and curving south
    road = [(-10 - 4 * i, 50 - 3 * i) for i in range(40)]
    users, changed = RM.fit_boundaries(vm, road, 8.0, {1}, owner=[1] * len(road), log=lambda *a: None)
    assert changed == {1} and users == {1}
    assert len(vm.boundary(1).points) <= RM.MAX_BOUNDARY_POINTS
    assert vm.boundary(2).points == [(100, 0), (200, 0), (200, 100), (100, 100)]


def test_padding_keeps_next_chunk_offset():
    # leaf, 64-byte pad, leaf: growing the first leaf by 16 bytes shrinks the pad
    a = struct.pack('<II', 0x1234, 8) + bytes(8)
    pad = struct.pack('<II', 0, 72) + bytes(72)
    b = struct.pack('<II', 0x5678, 4) + b'abcd'
    tree = bchunk.parse(a + pad + b)
    tree[0].data = bytes(24)
    tree[0].dirty = True
    out = bytes(bchunk.serialize(tree))
    assert len(out) == len(a + pad + b)
    assert out.endswith(b)


@needs_tracks
def test_tracks_highway_build_stays_in_budget(tmp_path):
    pytest.importorskip('shapely')
    from nfsu2_world_editor.core import highway_mod as HM
    plan_path = os.environ.get('NFSU2_HIGHWAY_PLAN')
    if not plan_path or not Path(plan_path).is_file():
        pytest.skip('NFSU2_HIGHWAY_PLAN not set')
    plan = json.load(open(plan_path))
    r = HM.build_highway_mod(TRACKS, plan, str(tmp_path), write=False, log=lambda *a: None)
    assert r['budget']['max_points'] <= 17
    assert all(t1 <= HM.LIST_BUDGET for _, _, t1 in r['budget']['lists'])
