"""Build a new road: centreline, elevation profile, render mesh and collision.

Pure Python, no Blender. Coordinates are game world units (metres), Z up.

* :func:`catmull_rom` / :func:`resample` turn control points into an evenly
  spaced centreline.
* :func:`profile` assigns heights from fixed (station, z) keys, limiting the
  grade and smoothing the curve.
* :func:`road_mesh` makes asphalt, kerb walls and (optionally) bridge-deck
  sides as triangle lists with UVs; :func:`road_collision` the matching
  collision polygons (ground quads + both-sided barrier walls).
"""
import math

from . import collision as C


def catmull_rom(pts, samples=16):
    """Centripetal-ish uniform Catmull-Rom through 2D/3D control points."""
    if len(pts) < 2:
        return list(pts)
    p = [pts[0]] + list(pts) + [pts[-1]]
    out = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        for k in range(samples):
            t = k / samples
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * ((2 * b) + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2
                                    + (-a + 3 * b - 3 * c + d) * t3)
                             for a, b, c, d in zip(p0, p1, p2, p3)))
    out.append(tuple(pts[-1]))
    return out


def polyline_length(pts):
    return sum(math.dist(a[:2], b[:2]) for a, b in zip(pts, pts[1:]))


def resample(pts, step):
    """Even spacing ``step`` along a dense 2D polyline -> [(x, y, station)]."""
    out = [(pts[0][0], pts[0][1], 0.0)]
    acc = 0.0
    need = step
    for a, b in zip(pts, pts[1:]):
        seg = math.dist(a[:2], b[:2])
        while seg > 0 and acc + seg >= need:
            t = (need - acc) / seg
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, need))
            need += step
        acc += seg
    if acc - out[-1][2] > step * 0.25:
        out.append((pts[-1][0], pts[-1][1], acc))
    else:
        out[-1] = (pts[-1][0], pts[-1][1], acc)
    return out


def profile(stations, keys, max_grade=0.06, smooth_passes=40):
    """Heights for each station from ``keys`` [(station, z)] (linear between keys),
    grade-limited both ways and smoothed, keys held fixed."""
    keys = sorted(keys)

    def lin(s):
        if s <= keys[0][0]:
            return keys[0][1]
        for (s0, z0), (s1, z1) in zip(keys, keys[1:]):
            if s <= s1:
                return z0 + (z1 - z0) * (s - s0) / (s1 - s0)
        return keys[-1][1]
    z = [lin(s) for s in stations]
    fixed = set()
    for ks, _ in keys:
        fixed.add(min(range(len(stations)), key=lambda i: abs(stations[i] - ks)))
    for _ in range(smooth_passes):
        nz = list(z)
        for i in range(1, len(z) - 1):
            if i not in fixed:
                nz[i] = (z[i - 1] + 2 * z[i] + z[i + 1]) / 4
        z = nz
    for i in range(1, len(z)):
        d = stations[i] - stations[i - 1]
        if abs(z[i] - z[i - 1]) > max_grade * d * 1.5 and i not in fixed:
            z[i] = z[i - 1] + math.copysign(max_grade * d, z[i] - z[i - 1])
    return z


def frames(center):
    """Unit tangent and left normal at each centreline point [(x, y, z)]."""
    out = []
    n = len(center)
    for i in range(n):
        a = center[max(0, i - 1)]
        b = center[min(n - 1, i + 1)]
        tx, ty = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(tx, ty) or 1.0
        tx, ty = tx / ln, ty / ln
        out.append(((tx, ty), (-ty, tx)))
    return out


class MeshPart:
    """Triangle soup with per-vertex uv and colour, one material slot."""

    def __init__(self, name, tex_slot=0, double_sided=False):
        self.name = name
        self.double = double_sided
        self.slot = tex_slot
        self.verts, self.uvs, self.faces = [], [], []

    def v(self, p, uv):
        self.verts.append(tuple(p))
        self.uvs.append(tuple(uv))
        return len(self.verts) - 1

    def quad(self, a, b, c, d):
        """a-b-c-d in perimeter order (counter-clockwise seen from the visible
        side). The shipped road triangles are stored counter-clockwise seen from
        above, so that order is kept; ``double_sided`` parts get both windings."""
        self.faces.append((a, b, c))
        self.faces.append((a, c, d))
        if self.double:
            self.faces.append((a, c, b))
            self.faces.append((a, d, c))


def road_mesh(center, half_width, uv_len=40.0, kerb_h=0.9, deck_depth=1.5, deck=None, strips=4,
              s0=0.0, fr=None):
    """Render parts for a road along ``center`` [(x, y, z)].

    Returns {'asphalt': MeshPart, 'kerb': MeshPart, 'deck': MeshPart}. ``deck`` is a
    per-point bool list: True where the road is a bridge (draw the deck sides and
    underside)."""
    fr = fr or frames(center)
    asphalt = MeshPart('asphalt', 0)
    kerb = MeshPart('kerb', 1, True)
    side = MeshPart('deck', 1, True)
    s = s0
    rows = []
    for i, (p, (t, nrm)) in enumerate(zip(center, fr)):
        if i:
            s += math.dist(center[i - 1][:2], p[:2])
        L = (p[0] + nrm[0] * half_width, p[1] + nrm[1] * half_width, p[2])
        R = (p[0] - nrm[0] * half_width, p[1] - nrm[1] * half_width, p[2])
        rows.append((L, R, s))
    # asphalt: ``strips`` lanes across, each mapping the lane texture u 0..1
    # (mirrored every other strip so neighbouring edges match)
    k = strips
    for i, (L, R, s) in enumerate(rows):
        base = len(asphalt.verts)
        for j in range(k + 1):
            t = j / k
            P = (L[0] + (R[0] - L[0]) * t, L[1] + (R[1] - L[1]) * t, L[2] + (R[2] - L[2]) * t)
            asphalt.v(P, (float(j % 2), s / uv_len))
        if i:
            b = base - (k + 1)
            for j in range(k):
                asphalt.quad(b + j, b + j + 1, base + j + 1, base + j)
    # kerb walls (inner face + top) on both edges
    for edge, sign in ((0, 1), (1, -1)):
        base = len(kerb.verts)
        for i, (L, R, s) in enumerate(rows):
            P = (L, R)[edge]
            nx, ny = fr[i][1]
            o = (P[0] + sign * nx * 0.5, P[1] + sign * ny * 0.5)
            kerb.v((P[0], P[1], P[2] - 0.05), (0.0, s / 4.0))
            kerb.v((P[0], P[1], P[2] + kerb_h), (0.25, s / 4.0))
            kerb.v((o[0], o[1], P[2] + kerb_h), (0.35, s / 4.0))
            kerb.v((o[0], o[1], P[2] - deck_depth), (0.75, s / 4.0))
            if i:
                a = base + 4 * i
                b = a - 4
                for k in range(3):
                    if edge == 0:
                        kerb.quad(b + k, b + k + 1, a + k + 1, a + k)
                    else:
                        kerb.quad(b + k, a + k, a + k + 1, b + k + 1)
    # deck underside where bridged
    if deck:
        for i in range(1, len(rows)):
            if not (deck[i] or deck[i - 1]):
                continue
            (L0, R0, s0), (L1, R1, s1) = rows[i - 1], rows[i]
            nx0, ny0 = fr[i - 1][1]
            nx1, ny1 = fr[i][1]
            q = [(L0[0] + nx0 * .5, L0[1] + ny0 * .5, L0[2] - deck_depth),
                 (R0[0] - nx0 * .5, R0[1] - ny0 * .5, R0[2] - deck_depth),
                 (R1[0] - nx1 * .5, R1[1] - ny1 * .5, R1[2] - deck_depth),
                 (L1[0] + nx1 * .5, L1[1] + ny1 * .5, L1[2] - deck_depth)]
            ids = [side.v(v, (k & 1, s0 / 8 if k < 2 else s1 / 8)) for k, v in enumerate(q)]
            side.quad(ids[0], ids[3], ids[2], ids[1])
    return {'asphalt': asphalt, 'kerb': kerb, 'deck': side}


def pillars(center, every=60.0, size=2.0, floor=-2.0, deck_depth=1.5, skip=None):
    """Simple square bridge piers every ``every`` units under the deck."""
    part = MeshPart('piers', 1, True)
    s = 0.0
    nxt = every / 2
    for i in range(1, len(center)):
        s += math.dist(center[i - 1][:2], center[i][:2])
        if s < nxt:
            continue
        nxt += every
        if skip and skip(i):
            continue
        x, y, z = center[i]
        top = z - deck_depth
        if top - floor < 2:
            continue
        h = size / 2
        c = [(x - h, y - h), (x + h, y - h), (x + h, y + h), (x - h, y + h)]
        for k in range(4):
            a, b = c[k], c[(k + 1) % 4]
            i0 = part.v((a[0], a[1], floor), (0, 0))
            part.v((b[0], b[1], floor), (1, 0))
            part.v((b[0], b[1], top), (1, (top - floor) / 4))
            part.v((a[0], a[1], top), (0, (top - floor) / 4))
            part.quad(i0, i0 + 1, i0 + 2, i0 + 3)
    return part


def road_collision(center, half_width, open_ends=(0.0, 0.0), wall_gaps=(), surface=1,
                   wall_step=8.0):
    """Collision polygons for the road: one ground quad per half-width per step and
    both-sided barrier walls along both edges. ``open_ends`` (start, end) metres
    without walls; ``wall_gaps`` extra [(s0, s1)] station ranges without walls."""
    fr = frames(center)
    rows = []
    s = 0.0
    for i, (p, (t, n)) in enumerate(zip(center, fr)):
        if i:
            s += math.dist(center[i - 1][:2], p[:2])
        rows.append(((p[0] + n[0] * half_width, p[1] + n[1] * half_width, p[2]), p,
                     (p[0] - n[0] * half_width, p[1] - n[1] * half_width, p[2]), s))
    total = rows[-1][3]
    polys = []
    for (L0, C0, R0, s0), (L1, C1, R1, s1) in zip(rows, rows[1:]):
        polys.append(C.make_ground([L0, L1, C1, C0][::-1], surface))
        polys.append(C.make_ground([C0, C1, R1, R0][::-1], surface))

    def walled(sa, sb):
        if sa < open_ends[0] or sb > total - open_ends[1]:
            return False
        return not any(sa < g1 and sb > g0 for g0, g1 in wall_gaps)
    for (L0, C0, R0, s0), (L1, C1, R1, s1) in zip(rows, rows[1:]):
        if walled(s0, s1):
            polys.extend(C.make_wall(L0, L1, wall_step))
            polys.extend(C.make_wall(R0, R1, wall_step))
    return polys


def triangle_island(tri, z, skirt_floor=-3.0, uv_scale=16.0, n=10):
    """Flat triangular island: top (subdivided ``n`` times per edge) plus sloped
    skirts down to ``skirt_floor``. Returns (top MeshPart, skirt MeshPart, top
    triangles as world point triples for collision)."""
    (ax, ay), (bx, by), (cx, cy) = tri
    if (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) < 0:      # make it CCW
        (bx, by), (cx, cy) = (cx, cy), (bx, by)
    top = MeshPart('island', 2)
    skirt = MeshPart('island_skirt', 1, True)
    grid = {}
    for i in range(n + 1):
        for j in range(n + 1 - i):
            u, v = i / n, j / n
            x = ax + (bx - ax) * u + (cx - ax) * v
            y = ay + (by - ay) * u + (cy - ay) * v
            grid[i, j] = top.v((x, y, z), (x / uv_scale, y / uv_scale))
    tris = []
    for i in range(n):
        for j in range(n - i):
            for t in ((grid[i, j], grid[i + 1, j], grid[i, j + 1]),
                      ((grid[i + 1, j], grid[i + 1, j + 1], grid[i, j + 1]) if i + j + 1 < n else None)):
                if t is None:
                    continue
                top.faces.append(t)      # counter-clockwise from above
                tris.append(tuple(top.verts[k] for k in t))
    pts = [(ax, ay), (bx, by), (cx, cy)]
    gx = sum(p[0] for p in pts) / 3
    gy = sum(p[1] for p in pts) / 3
    for k in range(3):
        a, b = pts[k], pts[(k + 1) % 3]
        def out(p, d=12.0):
            dx, dy = p[0] - gx, p[1] - gy
            ln = math.hypot(dx, dy)
            return (p[0] + dx / ln * d, p[1] + dy / ln * d)
        ea, eb = out(a), out(b)
        L = math.dist(a, b)
        i0 = skirt.v((a[0], a[1], z), (0, 0))
        skirt.v((b[0], b[1], z), (L / 8, 0))
        skirt.v((eb[0], eb[1], skirt_floor), (L / 8, 2))
        skirt.v((ea[0], ea[1], skirt_floor), (0, 2))
        skirt.quad(i0, i0 + 3, i0 + 2, i0 + 1)
    return top, skirt, tris
