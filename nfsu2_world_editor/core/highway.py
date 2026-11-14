"""Wide road ("highway") and natural island builders for the road mod.

Pure geometry: everything returns :class:`road_builder.MeshPart` lists and
collision polygons in world space. Placement into game sections is done by
:mod:`road_mod`.

Cross-section of the highway (per side, metres from the centre line)::

    0.0 .. 1.0   median (concrete, Jersey barrier on the centre line)
    1.0 .. 4.75  lane 1   (yellow solid line on the median side)
    4.75.. 8.5   lane 2   (white dashed line between the lanes)
    8.5 .. 11.5  shoulder (white solid edge line)
    11.5.. 13.0  outer strip (no line)

On bridges the edges get concrete kerb walls and a deck underside; on land
(the island) they get flush sidewalks instead.
"""
import math

from . import collision as C
from .road_builder import MeshPart, frames

# texture slots (indices into the texture list built by road_mod)
T_YS, T_WD, T_WS, T_NL, T_CONC, T_WALK, T_GRASS, T_BEACH, T_ROCK = range(9)
TEXTURES = ['RDP_LANEA_YS_AA_KT', 'RDP_LANEB_WD_AA_KT', 'RDP_LANEB_WS_AA_KT', 'RDP_LANEC_NL_AA_KT',
            'ARC_CONCFREEWAY', 'ARC_SIDEWALKPAVE1', 'TRN_GRASSA', 'TRN_DIRTAGRASSA_KT', 'ORG_ROCK_A_LL']

STRIPS = [(0.0, 1.0, T_CONC), (1.0, 4.75, T_YS), (4.75, 8.5, T_WD), (8.5, 11.5, T_WS), (11.5, 13.0, T_NL)]
HALF = 13.0
SIDEWALK = 6.0


def _up(part, a, b, c, d):
    """Quad a-b-c-d (perimeter order), stored counter-clockwise from above."""
    va, vb, vc = part.verts[a], part.verts[b], part.verts[c]
    cr = (vb[0] - va[0]) * (vc[1] - va[1]) - (vb[1] - va[1]) * (vc[0] - va[0])
    if cr < 0:
        a, b, c, d = a, d, c, b
    part.faces.append((a, b, c))
    part.faces.append((a, c, d))


def _rows(center, fr, s0=0.0):
    rows, s = [], s0
    for i, p in enumerate(center):
        if i:
            s += math.dist(center[i - 1][:2], p[:2])
        rows.append(s)
    return rows


def highway_mesh(center, land, s0=0.0, fr=None, uv_len=40.0, kerb_h=0.9, deck_depth=1.6,
                 median_end=(0.0, 0.0), total=None):
    """Mesh parts for the highway along ``center`` [(x, y, z)].

    ``land``: per point bool, True where the road runs on the ground (sidewalks
    instead of kerbs and deck). ``median_end``: (from, to) stations of the
    median barrier (open near the junctions). Returns {slot: MeshPart}."""
    fr = fr or frames(center)
    st = _rows(center, fr, s0)
    parts = {k: MeshPart('hw%d' % k, k, False) for k in range(9)}
    conc2 = MeshPart('hw_conc2', T_CONC, True)      # double-sided concrete
    n = len(center)
    # lane strips, both sides
    for sign in (1, -1):
        for d0, d1, slot in STRIPS:
            p = parts[slot]
            prev = None
            for i in range(n):
                x, y, z = center[i]
                nx, ny = fr[i][1]
                a = p.v((x + sign * nx * d0, y + sign * ny * d0, z), (0.0, st[i] / uv_len))
                b = p.v((x + sign * nx * d1, y + sign * ny * d1, z), (1.0, st[i] / uv_len))
                if prev:
                    _up(p, prev[0], prev[1], b, a)
                prev = (a, b)
    # sidewalks on land / kerbs + deck on bridges
    for sign in (1, -1):
        for i in range(1, n):
            x0, y0, z0 = center[i - 1]
            x1, y1, z1 = center[i]
            (nx0, ny0), (nx1, ny1) = fr[i - 1][1], fr[i][1]
            if land[i] and land[i - 1]:
                p = parts[T_WALK]
                q = [(x0 + sign * nx0 * HALF, y0 + sign * ny0 * HALF, z0 + 0.02),
                     (x0 + sign * nx0 * (HALF + SIDEWALK), y0 + sign * ny0 * (HALF + SIDEWALK), z0 + 0.02),
                     (x1 + sign * nx1 * (HALF + SIDEWALK), y1 + sign * ny1 * (HALF + SIDEWALK), z1 + 0.02),
                     (x1 + sign * nx1 * HALF, y1 + sign * ny1 * HALF, z1 + 0.02)]
                ids = [p.v(v, ((k in (1, 2)) * 1.5, st[i - 1 + (k >= 2)] / 4.0)) for k, v in enumerate(q)]
                _up(p, *ids)
            else:
                # kerb: inner face, top, outer face down to the deck bottom
                e0 = (x0 + sign * nx0 * HALF, y0 + sign * ny0 * HALF)
                e1 = (x1 + sign * nx1 * HALF, y1 + sign * ny1 * HALF)
                o0 = (e0[0] + sign * nx0 * 0.5, e0[1] + sign * ny0 * 0.5)
                o1 = (e1[0] + sign * nx1 * 0.5, e1[1] + sign * ny1 * 0.5)
                ring0 = [(e0[0], e0[1], z0 - 0.05), (e0[0], e0[1], z0 + kerb_h), (o0[0], o0[1], z0 + kerb_h),
                         (o0[0], o0[1], z0 - deck_depth)]
                ring1 = [(e1[0], e1[1], z1 - 0.05), (e1[0], e1[1], z1 + kerb_h), (o1[0], o1[1], z1 + kerb_h),
                         (o1[0], o1[1], z1 - deck_depth)]
                for k in range(3):
                    a = conc2.v(ring0[k], (k * 0.3, st[i - 1] / 4))
                    b = conc2.v(ring0[k + 1], ((k + 1) * 0.3, st[i - 1] / 4))
                    c = conc2.v(ring1[k + 1], ((k + 1) * 0.3, st[i] / 4))
                    d = conc2.v(ring1[k], (k * 0.3, st[i] / 4))
                    conc2.quad(a, b, c, d)
                if sign == 1:      # deck underside once per row
                    q = [(x0 + nx0 * (HALF + .5), y0 + ny0 * (HALF + .5), z0 - deck_depth),
                         (x0 - nx0 * (HALF + .5), y0 - ny0 * (HALF + .5), z0 - deck_depth),
                         (x1 - nx1 * (HALF + .5), y1 - ny1 * (HALF + .5), z1 - deck_depth),
                         (x1 + nx1 * (HALF + .5), y1 + ny1 * (HALF + .5), z1 - deck_depth)]
                    ids = [conc2.v(v, (k & 1, st[i - 1 + (k >= 2)] / 8)) for k, v in enumerate(q)]
                    conc2.quad(*ids)
    # median Jersey barrier on the centre line
    total = st[-1] if total is None else total
    for i in range(1, n):
        if st[i - 1] < median_end[0] or st[i] > total - median_end[1]:
            continue
        x0, y0, z0 = center[i - 1]
        x1, y1, z1 = center[i]
        (nx0, ny0), (nx1, ny1) = fr[i - 1][1], fr[i][1]
        prof = [(-0.4, 0.0), (-0.15, 0.85), (0.15, 0.85), (0.4, 0.0)]
        for k in range(3):
            (a0, h0), (a1, h1) = prof[k], prof[k + 1]
            q = [(x0 + nx0 * a0, y0 + ny0 * a0, z0 + h0), (x0 + nx0 * a1, y0 + ny0 * a1, z0 + h1),
                 (x1 + nx1 * a1, y1 + ny1 * a1, z1 + h1), (x1 + nx1 * a0, y1 + ny1 * a0, z1 + h0)]
            ids = [conc2.v(v, (k * 0.33 + (j & 1) * 0.33, st[i - 1 + (j >= 2)] / 4)) for j, v in enumerate(q)]
            conc2.quad(*ids)
    out = [p for p in parts.values() if p.faces]
    if conc2.faces:
        out.append(conc2)
    return out


def piers(center, land, every=64.0, size=2.2, floor=-3.0, deck_depth=1.6, offsets=(-7.5, 7.5)):
    part = MeshPart('hw_piers', T_CONC, True)
    fr = frames(center)
    s, nxt = 0.0, every / 2
    for i in range(1, len(center)):
        s += math.dist(center[i - 1][:2], center[i][:2])
        if s < nxt:
            continue
        nxt += every
        if land[i]:
            continue
        x, y, z = center[i]
        top = z - deck_depth
        if top - floor < 2:
            continue
        (tx, ty), (nx, ny) = fr[i]
        for off in offsets:
            cx, cy = x + nx * off, y + ny * off
            h = size / 2
            c = [(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h)]
            for k in range(4):
                a, b = c[k], c[(k + 1) % 4]
                i0 = part.v((a[0], a[1], floor), (0, 0))
                part.v((b[0], b[1], floor), (1, 0))
                part.v((b[0], b[1], top), (1, (top - floor) / 4))
                part.v((a[0], a[1], top), (0, (top - floor) / 4))
                part.quad(i0, i0 + 1, i0 + 2, i0 + 3)
    return part


def highway_collision(center, land, s0=0.0, total=None, open_ends=(30.0, 30.0), median_end=(40.0, 40.0)):
    """Ground (road, plus sidewalks on land), edge walls on bridges, median wall."""
    fr = frames(center)
    st = _rows(center, fr, s0)
    total = st[-1] if total is None else total
    polys = []
    for i in range(1, len(center)):
        (x0, y0, z0), (x1, y1, z1) = center[i - 1], center[i]
        (nx0, ny0), (nx1, ny1) = fr[i - 1][1], fr[i][1]
        w = HALF + (SIDEWALK if (land[i] and land[i - 1]) else 0.0)
        for a, b in ((0.0, w), (-w, 0.0)):
            q = [(x0 + nx0 * a, y0 + ny0 * a, z0), (x0 + nx0 * b, y0 + ny0 * b, z0),
                 (x1 + nx1 * b, y1 + ny1 * b, z1), (x1 + nx1 * a, y1 + ny1 * a, z1)]
            if (q[1][0] - q[0][0]) * (q[2][1] - q[0][1]) - (q[1][1] - q[0][1]) * (q[2][0] - q[0][0]) < 0:
                q = q[::-1]
            polys.append(C.make_ground(q, 1))
        if not (land[i] and land[i - 1]) and open_ends[0] <= st[i - 1] and st[i] <= total - open_ends[1]:
            for sgn in (1, -1):
                polys.extend(C.make_wall((x0 + sgn * nx0 * HALF, y0 + sgn * ny0 * HALF),
                                         (x1 + sgn * nx1 * HALF, y1 + sgn * ny1 * HALF)))
        if median_end[0] <= st[i - 1] and st[i] <= total - median_end[1]:
            polys.extend(C.make_wall((x0, y0), (x1, y1)))
    return polys


# ------------------------------------------------------------------ island

def _noise(t, seed):
    return (math.sin(t * 3 + seed) * 0.5 + math.sin(t * 7 + seed * 1.7) * 0.3
            + math.sin(t * 13 + seed * 2.3) * 0.15)


def organic_island(base, keep, seed=1.3, amp=28.0, step=14.0):
    """Natural coastline around ``base`` (polygon points): low-frequency radial
    noise, always containing the ``keep`` shapely geometry. Returns a shapely
    Polygon."""
    from shapely.geometry import Polygon
    P = Polygon(base).buffer(25.0, join_style=1).simplify(4.0)
    ring = P.exterior
    L = ring.length
    m = max(24, int(L / step))
    cx, cy = P.centroid.x, P.centroid.y
    pts = []
    for k in range(m):
        q = ring.interpolate(L * k / m)
        dx, dy = q.x - cx, q.y - cy
        ln = math.hypot(dx, dy) or 1.0
        t = 2 * math.pi * k / m
        o = amp * _noise(t, seed)
        pts.append((q.x + dx / ln * o, q.y + dy / ln * o))
    I = Polygon(pts).buffer(0).union(keep).buffer(20, join_style=1).buffer(-20, join_style=1)
    if I.geom_type != 'Polygon':
        I = max(I.geoms, key=lambda g: g.area)
    return Polygon(I.exterior).simplify(1.5)


def _smooth(e0, e1, x):
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def island_terrain(coast, road_pts, plateau_z, corridor, grid=16.0, beach_z=0.8, floor=-3.0,
                   skirt=14.0, plateau_w=60.0, seed=2.1):
    """Terrain for the island: plateau at ``plateau_z`` along the road, gentle
    hills, beach to the coastline and an underwater skirt.

    ``coast``: shapely Polygon; ``road_pts``: centreline points on the island;
    ``corridor``: shapely Polygon of road + sidewalks (left out of the terrain).
    Returns (parts, ground triangles [(p0, p1, p2)], wall segments [(a, b)])."""
    from shapely.geometry import Point, Polygon, MultiPoint, LineString
    from shapely.ops import triangulate
    road_line = LineString([p[:2] for p in road_pts])
    outer = coast.buffer(skirt, join_style=1)

    def height(x, y):
        P = Point(x, y)
        dc = coast.exterior.distance(P) if coast.contains(P) else -outer.exterior.distance(P)
        if dc < 0:
            return floor
        dr = road_line.distance(P)
        base = beach_z + (plateau_z - 0.4 - beach_z) * _smooth(0.0, 55.0, dc)
        hills = 3.5 * _smooth(plateau_w, plateau_w + 80, dr) * _smooth(20, 90, dc) * \
            (0.6 + 0.4 * math.sin(x / 47.0 + seed) * math.cos(y / 61.0 - seed))
        z = base + hills
        w = 1.0 - _smooth(HALF + SIDEWALK + 4, plateau_w, dr)
        z = w * plateau_z + (1 - w) * z
        return min(z, plateau_z + 6) if dc > 0 else beach_z

    pts = []
    L = coast.exterior.length
    for k in range(int(L / 12)):
        q = coast.exterior.interpolate(L * k / int(L / 12))
        pts.append((q.x, q.y, beach_z))
    Lo = outer.exterior.length
    for k in range(int(Lo / 16)):
        q = outer.exterior.interpolate(Lo * k / int(Lo / 16))
        pts.append((q.x, q.y, floor))
    # corridor edges: one point per road row on both sides
    edge = corridor.exterior
    Le = edge.length
    for k in range(int(Le / 8)):
        q = edge.interpolate(Le * k / int(Le / 8))
        if coast.buffer(-2).contains(q):
            pts.append((q.x, q.y, plateau_z))
    minx, miny, maxx, maxy = coast.bounds
    inner = coast.buffer(-6)
    keep_out = corridor.buffer(4)
    y = miny
    j = 0
    while y <= maxy:
        x = minx + (grid / 2 if j % 2 else 0)
        while x <= maxx:
            P = Point(x, y)
            if inner.contains(P) and not keep_out.contains(P):
                pts.append((x, y, height(x, y)))
            x += grid
        y += grid * 0.87
        j += 1
    zof = {(round(p[0], 3), round(p[1], 3)): p[2] for p in pts}
    tris = triangulate(MultiPoint([p[:2] for p in pts]))
    parts = {T_GRASS: MeshPart('isl_grass', T_GRASS), T_BEACH: MeshPart('isl_beach', T_BEACH),
             T_ROCK: MeshPart('isl_rock', T_ROCK)}
    ground = []
    cor_in = corridor.buffer(-0.5)
    for t in tris:
        c = t.centroid
        if not outer.contains(c) or cor_in.contains(c):
            continue
        vs = [(x, y, zof.get((round(x, 3), round(y, 3)), height(x, y))) for x, y in list(t.exterior.coords)[:3]]
        a, b, cc = vs
        cr = (b[0] - a[0]) * (cc[1] - a[1]) - (b[1] - a[1]) * (cc[0] - a[0])
        if abs(cr) < 1e-6:
            continue
        if cr < 0:
            vs = [a, cc, b]
        zm = sum(v[2] for v in vs) / 3
        zr = max(v[2] for v in vs) - min(v[2] for v in vs)
        span = max(math.dist(vs[i][:2], vs[(i + 1) % 3][:2]) for i in range(3))
        slot = T_ROCK if (zm < beach_z + 0.5 and zr > 2.5) or zr / max(span, 1) > 0.45 else \
            (T_BEACH if zm < 3.0 else T_GRASS)
        p = parts[slot]
        ids = [p.v(v, (v[0] / 12.0, v[1] / 12.0)) for v in vs]
        p.faces.append(tuple(ids))
        if zm > floor + 0.5:
            ground.append(tuple(vs))
    # invisible walls a little offshore so cars stay on the island
    shore = coast.buffer(8, join_style=1).exterior
    Ls = shore.length
    m = int(Ls / 8)
    walls = []
    for k in range(m):
        a = shore.interpolate(Ls * k / m)
        b = shore.interpolate(Ls * (k + 1) / m)
        mid = Point((a.x + b.x) / 2, (a.y + b.y) / 2)
        if corridor.buffer(HALF).distance(mid) < 1.0 or road_line.distance(mid) < HALF + 30:
            continue
        walls.append(((a.x, a.y), (b.x, b.y)))
    return [p for p in parts.values() if p.faces], ground, walls
