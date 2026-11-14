"""Plan for the west highway v2 (NW hills -> west coast -> island -> SW downtown lot).
Writes a plan JSON for tools/build_highway_mod.py:

    python tools/west_highway_plan.py plan2.json
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from shapely.geometry import LineString, Polygon, Point
from nfsu2_world_editor.core import road_builder as RB, highway as HW

STEP = 8.0
J1 = (-2000.0, 1145.0, 58.5)   # NW hills road (B26), new road leaves to the south
J2 = (-1290.0, -665.0, 18.4)   # west leg of the SW-downtown lot road (E23/E21), joined from the west
CTRL = [J1[:2], (-2000, 1095), (-2012, 1010), (-2040, 890), (-2052, 740), (-2030, 560),
        (-1990, 360), (-1950, 160), (-1905, -20), (-1855, -150), (-1790, -280), (-1720, -400),
        (-1640, -505), (-1550, -590), (-1440, -648), (-1350, -666), J2[:2]]
ISLAND_Z = 8.5
ISLAND_BASE = [(-1890, -120), (-1770, -100), (-1650, -230), (-1590, -420), (-1540, -540), (-1550, -670), (-1660, -695), (-1790, -560), (-1880, -330)]

dense = RB.catmull_rom([tuple(map(float, p)) for p in CTRL], samples=24)
rs = RB.resample(dense, STEP)
xy = [(x, y) for x, y, s in rs]
st = [s for x, y, s in rs]
total = st[-1]
line = LineString(xy)
# island: organic coast around the base triangle, keeping the road corridor on it
seg = [p for p in xy if Polygon(ISLAND_BASE).buffer(-20).contains(Point(p))]
keep = LineString(seg).buffer(HW.HALF + HW.SIDEWALK + 30, cap_style=2)
coast = HW.organic_island(ISLAND_BASE, keep)
on = Polygon(coast).buffer(-25)
land = [on.contains(Point(p)) for p in xy]
i0 = land.index(True); i1 = len(land) - 1 - land[::-1].index(True)
for i in range(i0, i1 + 1):
    land[i] = True
s_on, s_off = st[i0], st[i1]
keys = [(0.0, J1[2]), (24.0, J1[2] - 0.2), (s_on - 30, ISLAND_Z), (s_off + 30, ISLAND_Z),
        (total - 24, J2[2] + 0.1), (total, J2[2])]
z = RB.profile(st, keys, max_grade=0.06)
center = [(x, y, zz) for (x, y), zz in zip(xy, z)]
plan = dict(center=center, stations=st, land=land, island_coast=[list(p) for p in list(coast.exterior.coords)[:-1]],
            island_z=ISLAND_Z, junctions=[[J1[0], J1[1], 0.0, -1.0], [J2[0], J2[1], 1.0, 0.0]],
            trim=[11.0, 9.0], median_end=[45.0, 45.0], open_ends=[30.0, 30.0])
out = sys.argv[1] if len(sys.argv) > 1 else 'plan2.json'
json.dump(plan, open(out, 'w'))
grades = [abs(z[i] - z[i - 1]) / (st[i] - st[i - 1]) for i in range(1, len(z))]
print('points %d, length %.0f m, island %.0f..%.0f m (%d pts), coast pts %d, max grade %.3f'
      % (len(center), total, s_on, s_off, i1 - i0 + 1, len(plan['island_coast']), max(grades)))
