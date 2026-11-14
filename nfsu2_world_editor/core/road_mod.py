"""Add a drivable road to a region: mesh, scenery placement, collision,
streaming visibility. Writes modified copies, never the originals.

The road is hosted by one existing streaming section (``host``): its models go
into the host's first solid list and are placed in the host's own scenery pack;
its collision polygons are appended to the host's collision pack (the grid
grows to cover them). Every drivable section whose boundary the road crosses
gets the host in its visible list, and the drivable boundaries are extended
over the road where it leaves them (sea crossings), so the game always knows
which drivable section the car is in.

Junctions: shipped barrier walls and kerb faces across the mouth of each
junction are taken out of the collision grids (the polygons stay in the file,
unreferenced, so no index moves).

``plan`` (dict, e.g. loaded from JSON)::

    center: [[x, y, z], ...]   evenly spaced centreline (see road_builder)
    stations: [s, ...]
    half: half width (m)
    island: [[x, y] * 3] optional flat triangular island
    island_z: island top height
    island_range: [s0, s1] stations on the island (no road walls there)
    junctions: [[x, y, heading_x, heading_y], ...] mouths to open
"""
import math
import struct

from . import bchunk, collision as C, road_builder as RB, visibility as V, world_edit as WE
from .meshcodec import MeshData, encode, decode
from .scenery import SceneryPack, PACK as SCENERY_PACK
from .solidlist import add_object
from .stream import Region

TEX_ASPHALT = 'RDP_LANEB_WD_AA_KT'
TEX_CONCRETE = 'ARC_CONCFREEWAY'
TEX_GRASS = 'TRN_GRASSA'
NIGHT_COLOUR = (130 << 24) | (100 << 16) | (110 << 8) | 110   # baked ARGB like nearby roads


def _texture_hashes(region, names):
    idx = region.texture_index()
    by_name = {}
    for h, ts in idx.items():
        by_name.setdefault(ts[0].name, h)
    out = []
    for n in names:
        if n not in by_name:
            raise KeyError('texture %s not found in the region' % n)
        out.append(by_name[n])
    return out


def _part_mesh(parts, tex, template_mat):
    m = MeshData()
    m.textures = list(tex)
    for p in parts:
        if not p.faces:
            continue
        off = len(m.verts)
        first = len(m.indices)
        m.verts.extend(p.verts)
        m.uvs.extend(p.uvs)
        m.colors.extend([NIGHT_COLOUR] * len(p.verts))
        for f in p.faces:
            m.indices.extend(i + off for i in f)
        m.materials.append((first, len(m.indices) - first, p.slot, template_mat))
    return m


def build_geometry(plan, piece_rows=40):
    """-> list of (name, [MeshPart]) models and the collision polygon list."""
    center = [tuple(p) for p in plan['center']]
    st = plan['stations']
    half = plan['half']
    isl = plan.get('island_range')
    deck = [not (isl and isl[0] <= s <= isl[1]) for s in st]
    fr = RB.frames(center)
    models = []
    n = len(center)
    for k0 in range(0, n - 1, piece_rows):
        k1 = min(n - 1, k0 + piece_rows)
        sub = center[k0:k1 + 1]
        parts = RB.road_mesh(sub, half, deck=deck[k0:k1 + 1], s0=st[k0], fr=fr[k0:k1 + 1])
        piers = RB.pillars(sub, skip=lambda i, k0=k0: not deck[k0 + i] or center[k0 + i][2] < 6)
        sel = [parts['asphalt'], parts['kerb'], parts['deck'], piers]
        models.append(('TRN_WESTRD_%02d' % (k0 // piece_rows), [p for p in sel if p.faces]))
    polys = []
    gaps = [tuple(isl)] if isl else []
    polys += RB.road_collision(center, half, open_ends=(plan.get('open_start', 18.0), plan.get('open_end', 18.0)),
                               wall_gaps=gaps)
    if plan.get('island'):
        top, skirt, tris = RB.triangle_island(plan['island'], plan['island_z'])
        models.append(('TRN_WESTRD_ISLAND', [top, skirt]))
        for t in tris:
            polys.append(C.make_ground(list(t), C.SURFACE['GRASS']))
        tri = plan['island']
        for k in range(3):
            a, b = tri[k], tri[(k + 1) % 3]
            L = math.dist(a, b)
            m = max(1, int(L // 8))
            for i in range(m):
                p = (a[0] + (b[0] - a[0]) * i / m, a[1] + (b[1] - a[1]) * i / m)
                q = (a[0] + (b[0] - a[0]) * (i + 1) / m, a[1] + (b[1] - a[1]) * (i + 1) / m)
                mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
                if min(math.dist(mid, c[:2]) for c in center) < half + 10:
                    continue                      # where the road crosses the shore
                polys.append(C.make_barrier(p, q))
                polys.append(C.make_barrier(q, p))
    return models, polys


# ------------------------------------------------------------------ collision

def _segment_near(a, b, p, r):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L))
    return math.hypot(ax + dx * t - p[0], ay + dy * t - p[1]) <= r


def open_junction(pack, mouth_pts, radius):
    """Drop barriers / vertical kerb faces near the mouth points from the grid.
    Returns the number of polygons unreferenced."""
    drop = set()
    for i, q in enumerate(pack.polys):
        if not (q.is_barrier or q.is_vertical):
            continue
        c = q.corners()
        a, b = c[0][:2], c[2][:2]
        if any(_segment_near(a, b, m, radius) for m in mouth_pts):
            drop.add(i)
    if not drop:
        return 0
    lists = [[r for r in lst if r[0] not in drop] for lst in pack.cell_lists()]
    pack.refs, pack.cells = [], []
    for lst in lists:
        pack.cells.append((len(pack.refs), len(lst)))
        pack.refs.extend(lst)
    return len(drop)


_collision_node = WE.collision_node
_set_collision = WE.set_collision


# ------------------------------------------------------------------ visibility

def extend_boundaries(vm, corridor_pts, half, host_num, margin=25.0, assign=None, areas=()):
    """Make every point of the road lie in a drivable boundary; returns the set of
    drivable sections that now cover the road. Needs shapely."""
    from shapely.geometry import LineString, Polygon, Point
    from shapely.ops import unary_union, nearest_points
    live = {v.section for v in vm.visible}
    polys = {}
    for b in vm.boundaries:
        if b.section in live:
            polys[b.section] = Polygon(b.points).buffer(0)
    covered = []
    for p in corridor_pts:
        covered.append(next((s for s, P in polys.items() if P.contains(Point(p))), None))
    # nearest boundary per uncovered point (optionally forced by ``assign``)
    owner = []
    for i, p in enumerate(corridor_pts):
        if covered[i] is not None:
            owner.append(None)
            continue
        if assign:
            s = assign(i, p)
            if s is not None:
                owner.append(s)
                continue
        owner.append(min(polys, key=lambda s: polys[s].distance(Point(p))))
    runs = []
    for i, o in enumerate(owner):
        if o is None:
            continue
        if runs and runs[-1][0] == o and runs[-1][2] == i - 1:
            runs[-1][2] = i
        else:
            runs.append([o, i, i])
    changed = {}
    for sec, i0, i1 in runs:
        pts = corridor_pts[max(0, i0 - 2):i1 + 3]
        band = LineString(pts).buffer(half + margin, cap_style=2) if len(pts) > 1 else Point(pts[0]).buffer(half + margin)
        own = changed.get(sec, polys[sec])
        if not band.intersects(own):
            a, b = nearest_points(band, own)
            band = band.union(LineString([a, b]).buffer(half + margin))
        others = unary_union([P for s, P in polys.items() if s != sec])
        ext = band.difference(others.buffer(0.5))
        new = own.union(ext).buffer(0.01).buffer(-0.01)
        if new.geom_type != 'Polygon':
            new = max(new.geoms, key=lambda g: g.area)
        new = Polygon(new.exterior).simplify(1.0, preserve_topology=True)
        changed[sec] = new
        polys[sec] = new
    # extra areas (an island): the uncovered part goes to the nearest section
    for area in areas:
        A = Polygon(area).buffer(margin)
        rest = A.difference(unary_union(list(polys.values())))
        for g in getattr(rest, 'geoms', [rest]):
            if g.is_empty or g.area < 1:
                continue
            sec = min(polys, key=lambda s: polys[s].distance(g))
            new = polys[sec].union(g).buffer(0.01).buffer(-0.01)
            if new.geom_type != 'Polygon':
                new = max(new.geoms, key=lambda q: q.area)
            polys[sec] = changed[sec] = Polygon(new.exterior).simplify(1.0, preserve_topology=True)
    # repair: points left on a seam or trimmed by the simplification
    for _ in range(4):
        miss = [i for i, p in enumerate(corridor_pts)
                if not any(P.contains(Point(p)) for P in polys.values())]
        if not miss:
            break
        for i in miss:
            p = corridor_pts[i]
            sec = min(polys, key=lambda s: polys[s].distance(Point(p)))
            others = unary_union([P for s, P in polys.items() if s != sec])
            add = Point(p).buffer(half).difference(others)
            new = polys[sec].union(add).buffer(0.01).buffer(-0.01)
            if new.geom_type != 'Polygon':
                new = max(new.geoms, key=lambda g: g.area)
            polys[sec] = changed[sec] = Polygon(new.exterior)
    for sec, P in changed.items():
        b = vm.boundary(sec)
        orig_ccw = _signed_area(b.points) > 0
        pts = list(P.exterior.coords)[:-1]
        if (_signed_area(pts) > 0) != orig_ccw:
            pts = pts[::-1]
        b.points = [(float(x), float(y)) for x, y in pts]
    users = set()
    for p in corridor_pts:
        for s in vm.drivable_at(*p):
            users.add(s)
    return users, set(changed)


def _signed_area(pts):
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
               for i in range(len(pts))) / 2


# ------------------------------------------------------------------ main

def build_road_mod(tracks, plan, out_dir, host='E21', region_name='L4RA', log=print, assign=None):
    region = Region.open(tracks, region_name)
    host_e = region.entry(host)
    host_num = host_e['num']
    models, polys = build_geometry(plan)
    tex = _texture_hashes(region, [TEX_ASPHALT, TEX_CONCRETE, TEX_GRASS])
    # scenery info / flags / block copied from the host's own TRN road placement
    own = WE.own_scenery_pack(region, host)
    if own is None:
        raise ValueError('%s has no scenery pack of its own' % host)
    tmpl = WE.trn_template(own)
    written = []
    for name, parts in models:
        obj = add_object(region, host, name)
        tm = decode(obj)
        mat_tmpl = tm.materials[0][3] if tm and tm.materials else None
        m = _part_mesh(parts, tex, mat_tmpl)
        m.name = name
        m.matrix = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
        if len(m.verts) > 0xFFFF:
            raise ValueError('%s too big' % name)
        encode(obj, m)
        from .tpk import bin_hash
        WE.place_world_model(own, tmpl, name, bin_hash(name), m.verts, len(m.indices) // 3)
        written.append((name, len(m.verts), len(m.indices) // 3))
    own.commit()
    if not own.validate():
        raise RuntimeError('scenery tree check failed')
    log('models: %d (%d verts, %d tris)' % (len(written), sum(w[1] for w in written), sum(w[2] for w in written)))
    # collision: host gets the road; junction mouths opened everywhere
    node = _collision_node(region, host)
    pack = C.CollisionPack.parse(bchunk.chunk_bytes(node, 0)[8:])
    n_before = len(pack.polys)
    mouths = plan.get('junctions', [])
    opened = {}
    for e in region.entries:
        if not e['size'] or e['tag'][:1] in 'XYZ':
            continue
        nd = _collision_node(region, e['tag'])
        if nd is None:
            continue
        p = pack if e['tag'] == host else C.CollisionPack.parse(bchunk.chunk_bytes(nd, 0)[8:])
        if not p.polys:
            continue
        if any(p.bmin[0] - 30 <= m[0] <= p.bmax[0] + 30 and p.bmin[1] - 30 <= m[1] <= p.bmax[1] + 30 for m in mouths):
            k = open_junction(p, [m[:2] for m in mouths], plan['half'] + 2.0)
            if k:
                opened[e['tag']] = k
                if p is not pack:
                    _set_collision(nd, p)
    pack.add_polygons(polys)
    _set_collision(node, pack)
    log('collision: %s +%d polygons (grid %dx%d), junction walls removed %s'
        % (host, len(pack.polys) - n_before, pack.width, pack.height, opened))
    stream, l4 = region.build()
    # visibility
    tree = bchunk.parse(l4)
    mgr = next(c for c in tree if c.id == V.MANAGER)
    vm = V.VisibilityManager(bytes(bchunk.chunk_bytes(mgr, 0))[8:])
    corridor = [tuple(p[:2]) for p in plan['center']]
    if plan.get('island'):
        corridor_extra = [tuple(p) for p in plan['island']]
    else:
        corridor_extra = []
    users, changed = extend_boundaries(vm, corridor, plan['half'], host_num, assign=assign,
                                       areas=[plan['island']] if plan.get('island') else ())
    if plan.get('island'):
        (ax, ay), (bx, by), (cx, cy) = plan['island']
        for i in range(11):
            for j in range(11 - i):
                u, v = i / 10, j / 10
                users.update(vm.drivable_at(ax + (bx - ax) * u + (cx - ax) * v, ay + (by - ay) * u + (cy - ay) * v))
    added = []
    for s in sorted(users):
        vl = vm.visible_list(s)
        if host_num not in vl.sections:
            vl.sections.append(host_num)
            vl.sections.sort()
            added.append(s)
    missing = [i for i, p in enumerate(corridor) if not vm.drivable_at(*p)]
    if missing:
        raise RuntimeError('road points outside every drivable boundary: %s' % missing[:10])
    kids = bchunk.parse(vm.payload())
    for old, new in zip(mgr.children, kids):
        if bytes(old.data) != bytes(new.data):
            old.data = bytes(new.data)
            old.dirty = True
    mgr.dirty = True
    l4 = bytes(bchunk.serialize(tree))
    log('visibility: boundaries extended %s; host %s added to visible lists of %s; covering %s'
        % (sorted(changed), host, added, sorted(users)))
    import os
    os.makedirs(out_dir, exist_ok=True)
    sp = os.path.join(out_dir, 'STREAM%s.BUN' % region_name)
    lp = os.path.join(out_dir, '%s.BUN' % region_name)
    for p, d in ((sp, stream), (lp, l4)):
        if os.path.abspath(p) in (os.path.abspath(os.path.join(tracks, os.path.basename(p))),):
            raise RuntimeError('refusing to overwrite the original %s' % p)
        with open(p, 'wb') as f:
            f.write(d)
    return dict(stream=sp, l4=lp, models=written, users=sorted(users), changed=sorted(changed),
                lists_added=added, opened=opened, polys=len(polys))
