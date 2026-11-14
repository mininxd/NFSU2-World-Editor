"""Highway + island mod builder (version 2 of the west-road mod).

Differences from :mod:`road_mod` (version 1), driven by an in-game report of an
empty world in the west after version 1:

* **Streaming budget.** Version 1 put the whole road (~600 KB) into section E21
  and added E21 to the load lists of drivable areas 118, 206 and 301-303.
  Area 303 then loaded 7.45 MB of sections; the shipped maximum is 6.85 MB,
  and the game's fixed track-streaming pool can't hold it. Version 2 splits the
  road over several host sections that are *already* in the load lists of the
  areas the road passes through (B25 north, C25 along Coal Harbor, E21 island
  and south). It never edits a load list, checks every list's total against a
  budget, and keeps every host under the largest shipped section.
* **Boundaries.** Grown drivable boundaries keep the shipped maximum of 17
  points (version 1 produced up to 211).
* Wider road (26 m, 4 lanes, median barrier), a natural island with terrain,
  and copies of shipped Bayview buildings, trees and street lights on it.
"""
import math
import struct

from . import bchunk, collision as C, highway as HW, visibility as V, world_edit as WE
from .meshcodec import MeshData, encode, decode, SOLID_OBJECT
from .road_builder import frames
from .road_mod import (fit_boundaries, list_bytes, open_junction, NIGHT_COLOUR, _texture_hashes,
                       MAX_BOUNDARY_POINTS)
from .scenery import SceneryPack, PACK as SCENERY_PACK
from .solidlist import add_object
from .stream import Region
from .tpk import bin_hash

HOSTS = ('B25', 'C25', 'E21')
LIST_BUDGET = 6_500_000          # bytes; shipped max list total is 6,846,492 (L4RA area 303)
PIECE_ROWS = 25

BUILDINGS = ['XB_HTECHTOWERQ_1A_00', 'XB_BUILDCORNEROLDTWN2_1A_00', 'XB_GENBLDINGBF_1A_00',
             'XB_BGBUILDM_1A_00', 'XB_UC_CONDOFAT_1A_RB_00', 'XB_UC_ELLIPSEBLDING_1A_LB_0',
             'XB_HOTELCLASSICC_1A_00', 'XB_UC_RODEOCAFE_1A_00', 'XB_CORNERGYM_1A_AL_00']
COMPANIONS = {'XB_HOTELCLASSICC_1A_00': ['XB_HOTELCLASSICCWIN_1A_00']}
TREE = 'XT_STREET_TREEA_1B_00'
LIGHT = 'XO_STREETLIGHTJ_1A_00'


# ------------------------------------------------------------------ helpers

def _model_index(region):
    idx = {}
    for c in region.chunks:
        if c.children is None:
            continue
        for o in c.find_all(SOLID_OBJECT):
            m = decode(o)
            if m is not None and m.name not in idx:
                idx[m.name] = o
    return idx


def _instance_templates(region):
    """(info, flags, block) per model-name prefix from shipped placements."""
    out = {}
    for c in region.chunks:
        if c.id != SCENERY_PACK or c.children is None:
            continue
        sp = SceneryPack(c)
        for i in range(len(sp.inst)):
            ins = sp.instance(i)
            nm = sp.infos[ins['info']][:3].upper()
            if nm in (b'XB_', b'XT_', b'XO_') and nm not in out:
                blk = sp.blocks[ins['block']] if ins['block'] < len(sp.blocks) else bytes(128)
                out[nm] = (sp.infos[ins['info']], ins['flags'], blk)
        if len(out) == 3:
            break
    return out


def _mesh_from_parts(parts, tex):
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
        m.materials.append((first, len(m.indices) - first, p.slot, None))
    return m


def _encode_new(region, host, name, m, mat_tmpl=None):
    node = add_object(region, host, name)
    t = decode(node)
    rec = mat_tmpl if mat_tmpl is not None else (t.materials[0][3] if t and t.materials else None)
    m.materials = [(f, n, s, rec if r is None else r) for f, n, s, r in m.materials]
    m.name, m.hash = name, bin_hash(name)
    m.matrix = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
    if m.normals is None:
        m.stride = 24
    if len(m.verts) > 0xFFFF:
        raise ValueError('%s: too many vertices' % name)
    encode(node, m)
    return node


def assign_owners(vm, pts, allowed, min_run=12):
    """Drivable section per road point: the boundary containing it, else the
    nearest allowed one; runs shorter than ``min_run`` merge into a neighbour."""
    from shapely.geometry import Point, Polygon
    live = {v.section for v in vm.visible}
    orig = {b.section: Polygon(b.points).buffer(0) for b in vm.boundaries if b.section in live}
    owner = []
    for p in pts:
        P = Point(p[:2])
        inside = [s for s, Q in orig.items() if Q.contains(P)]
        bad = [s for s in inside if s not in allowed]
        if bad:
            raise ValueError('road point %s lies in drivable section(s) %s outside the budget' % (p[:2], bad))
        owner.append(inside[0] if inside else min(allowed, key=lambda s: orig[s].distance(P)))
    changed = True
    while changed:
        changed = False
        runs = []
        for i, o in enumerate(owner):
            if runs and runs[-1][0] == o:
                runs[-1][2] = i
            else:
                runs.append([o, i, i])
        for k, (o, a, b) in enumerate(runs):
            if b - a + 1 < min_run and len(runs) > 1:
                nb = runs[k - 1][0] if k else runs[k + 1][0]
                if any(orig[o].contains(Point(pts[i][:2])) for i in range(a, b + 1)):
                    continue
                for i in range(a, b + 1):
                    owner[i] = nb
                changed = True
                break
    return owner


# ------------------------------------------------------------------ main

def build_highway_mod(tracks, plan, out_dir, region_name='L4RA', hosts=HOSTS, budget=LIST_BUDGET,
                      log=print, write=True, island=True, boundaries=True, test_marker=False):
    """Build the mod; returns a summary dict (and writes the two files to
    ``out_dir`` when ``write``). ``island=False`` leaves out the island,
    buildings and props; ``boundaries=False`` leaves the visibility data
    untouched (diagnostic build); ``test_marker`` adds a small concrete pillar
    beside the north junction (diagnostic: is new geometry drawn at all?)."""
    from shapely.geometry import Polygon, LineString, Point
    region = Region.open(tracks, region_name)
    l4tree = bchunk.parse(region.l4)
    mgr = next(c for c in l4tree if c.id == V.MANAGER)
    vm0 = V.VisibilityManager(bytes(bchunk.chunk_bytes(mgr, 0))[8:])
    sizes0 = {e['num']: e['size'] for e in region.entries}
    shipped_max_section = max(e['size'] for e in region.entries if e['tag'][:1] not in 'XYZ')
    host_num = {h: region.entry(h)['num'] for h in hosts}
    areas_of = {h: {v.section for v in vm0.visible if host_num[h] in v.sections} for h in hosts}

    center = [tuple(p) for p in plan['center']]
    st = plan['stations']
    total = st[-1]
    land = plan['land'] if island else [False] * len(center)
    n = len(center)
    # ---- owners and hosts (no load list is edited: hosts must already be listed)
    allowed = set()
    for h in hosts:
        for a in areas_of[h]:
            if list_bytes(vm0, sizes0, a) + 400_000 <= budget:
                allowed.add(a)
    owner = assign_owners(vm0, center, allowed)
    host_of = []
    k = 0
    for i, o in enumerate(owner):
        while k < len(hosts) and o not in areas_of[hosts[k]]:
            k += 1
        if k == len(hosts):
            raise ValueError('no host section is loaded in drivable area %d (road point %d)' % (o, i))
        host_of.append(hosts[k])
    ranges = {}
    for i, h in enumerate(host_of):
        a, b = ranges.get(h, (i, i))
        ranges[h] = (min(a, i), max(b, i))
    log('owners: %s' % _runs(owner))
    log('hosts: %s' % {h: (a, b, round(st[a]), round(st[b])) for h, (a, b) in ranges.items()})

    tex_names = HW.TEXTURES
    tex = _texture_hashes(region, tex_names)
    fr = frames(center)
    coll = {h: [] for h in hosts}
    packs = {h: WE.own_scenery_pack(region, h) for h in hosts}
    for h in hosts:
        if packs[h] is None:
            raise ValueError('%s has no scenery pack of its own' % h)
    trn = {h: WE.trn_template(packs[h]) for h in hosts}
    made = []

    def add_model(h, name, parts):
        m = _mesh_from_parts(parts, tex)
        if not m.verts:
            return
        node = _encode_new(region, h, name, m)
        WE.place_world_model(packs[h], trn[h], name, bin_hash(name), m.verts, len(m.indices) // 3)
        made.append((h, name, len(m.verts), len(m.indices) // 3))

    trim0, trim1 = plan.get('trim', (0.0, 0.0))
    med = plan.get('median_end', (40.0, 40.0))
    opn = plan.get('open_ends', (30.0, 30.0))
    for h in hosts:
        if h not in ranges:
            continue
        a, b = ranges[h]
        a0 = max(0, a - 1)
        # mesh pieces (trimmed where the road overlaps the existing junction roads)
        for k0 in range(a0, b, PIECE_ROWS):
            k1 = min(b, k0 + PIECE_ROWS)
            if k1 <= k0:
                continue
            idx = [i for i in range(k0, k1 + 1) if trim0 <= st[i] <= total - trim1]
            if len(idx) < 2:
                continue
            sub = [center[i] for i in idx]
            parts = HW.highway_mesh(sub, [land[i] for i in idx], s0=st[idx[0]], fr=[fr[i] for i in idx],
                                    median_end=(med[0] - st[idx[0]], med[1] - (total - st[idx[-1]])),
                                    total=st[idx[-1]] - st[idx[0]])
            parts.append(HW.piers(sub, [land[i] for i in idx]))
            add_model(h, 'TRN_HW_%s_%02d' % (h, (k0 - a0) // PIECE_ROWS), [p for p in parts if p.faces])
        sub = center[a0:b + 1]
        coll[h] += HW.highway_collision(sub, land[a0:b + 1], s0=st[a0], total=total,
                                        open_ends=opn, median_end=med)
    # ---- island (terrain, buildings, props) in the host that owns the island road
    isl_host = None
    if island and plan.get('island_coast'):
        coast = Polygon(plan['island_coast'])
        on = [i for i in range(n) if land[i]]
        isl_host = host_of[on[len(on) // 2]]
        road_pts = [center[i] for i in on]
        line = LineString([p[:2] for p in road_pts])
        corridor = line.buffer(HW.HALF + HW.SIDEWALK, cap_style=2)
        parts, ground, walls = HW.island_terrain(coast, road_pts, plan['island_z'] - 0.05, corridor)
        add_model(isl_host, 'TRN_HW_ISLAND', parts)
        coll[isl_host] += [C.make_ground(list(t), 15) for t in ground]
        for a, b in walls:
            coll[isl_host] += C.make_wall(a, b)
        coll[isl_host] += _place_buildings(region, packs[isl_host], isl_host, plan, center, st, land, coast,
                                           corridor, made, log)
    if test_marker:
        (tx, ty), (nx, ny) = fr[0]
        x0, y0, z0 = center[0]
        mx, my = x0 + nx * (HW.HALF + 6) + tx * 10, y0 + ny * (HW.HALF + 6) + ty * 10
        add_model(host_of[0], 'TRN_HW_TESTMARKER', [_box(mx, my, z0 - 2, 2.0, 14.0)])
    # ---- junction mouths in every pack, then the new collision
    mouths = plan.get('junctions', [])
    opened = {}
    for e in region.entries:
        if not e['size'] or e['tag'][:1] in 'XYZ':
            continue
        nd = WE.collision_node(region, e['tag'])
        if nd is None:
            continue
        p = WE.read_collision(nd)
        if not p.polys:
            continue
        if any(p.bmin[0] - 40 <= m[0] <= p.bmax[0] + 40 and p.bmin[1] - 40 <= m[1] <= p.bmax[1] + 40 for m in mouths):
            k = open_junction(p, [m[:2] for m in mouths], HW.HALF + 4.0)
            if k:
                opened[e['tag']] = k
                WE.set_collision(nd, p)
    for h in hosts:
        if coll[h]:
            WE.add_collision(region, h, coll[h])
    for sp in packs.values():
        sp.commit()
        if not sp.validate():
            raise RuntimeError('scenery tree check failed for %d' % sp.section)
    log('models: %d, collision: %s, junction walls removed: %s'
        % (len(made), {h: len(v) for h, v in coll.items()}, opened))
    stream, l4 = region.build()
    if not boundaries:
        r2 = Region(stream, l4, region_name)
        report = check_budget(vm0, sizes0, {e['num']: e['size'] for e in r2.entries}, budget,
                              shipped_max_section, r2, log)
        _write(out_dir, region_name, region, stream, l4, write)
        return dict(stream=stream, l4=l4, models=made, owners=owner, hosts=host_of, changed=[], users=[],
                    budget=report, opened=opened)
    # ---- boundaries (points only; load lists untouched)
    tree = bchunk.parse(l4)
    mgr = next(c for c in tree if c.id == V.MANAGER)
    vm = V.VisibilityManager(bytes(bchunk.chunk_bytes(mgr, 0))[8:])
    isl_area = ()
    if island and plan.get('island_coast'):
        isl_area = [list(Polygon(plan['island_coast']).buffer(9.0).simplify(3.0).exterior.coords)[:-1]]
    users, changed = fit_boundaries(vm, [p[:2] for p in center], HW.HALF + 1.0, allowed, areas=isl_area,
                                    owner=owner, log=log)
    if isl_host and plan.get('island_coast'):
        changed = set(changed) | _cover_area(vm, Polygon(plan['island_coast']), owner, host_of, isl_host,
                                             areas_of, allowed, log)
    kids = bchunk.parse(vm.payload())
    for old, new in zip(mgr.children, kids):
        if bytes(old.data) != bytes(new.data):
            old.data = bytes(new.data)
            old.dirty = True
    mgr.dirty = True
    l4 = bytes(bchunk.serialize(tree))
    missing = [i for i, p in enumerate(center) if not vm.drivable_at(*p[:2])]
    if missing:
        raise RuntimeError('road points outside every drivable boundary: %s' % missing[:10])
    # ---- budget checks on the result
    r2 = Region(stream, l4, region_name)
    sizes = {e['num']: e['size'] for e in r2.entries}
    report = check_budget(vm, sizes0, sizes, budget, shipped_max_section, r2, log)
    _write(out_dir, region_name, region, stream, l4, write)
    return dict(stream=stream, l4=l4, models=made, owners=owner, hosts=host_of, changed=sorted(changed),
                users=sorted(users), budget=report, opened=opened)


def check_layout(stream, l4, region_name='L4RA'):
    """Raise if any section breaks a layout rule of the shipped files that the
    game depends on (see :mod:`layout`)."""
    from .layout import section_violations
    bad = section_violations(stream, Region(stream, l4, region_name).entries)
    if bad:
        raise RuntimeError('layout check failed: %s' % {k: v[:3] for k, v in bad.items()})


def _write(out_dir, region_name, region, stream, l4, write):
    check_layout(stream, l4, region_name)
    if not write:
        return
    import os
    os.makedirs(out_dir, exist_ok=True)
    src = {os.path.abspath(p) for p in region.source_paths}
    for name, d in (('STREAM%s.BUN' % region_name, stream), ('%s.BUN' % region_name, l4)):
        p = os.path.join(out_dir, name)
        if os.path.abspath(p) in src or os.path.realpath(p) in {os.path.realpath(q) for q in src}:
            raise RuntimeError('refusing to overwrite the original %s' % p)
        with open(p, 'wb') as f:
            f.write(d)


def _box(x, y, z, w, h):
    from .road_builder import MeshPart
    p = MeshPart('marker', HW.T_CONC, True)
    c = [(x - w / 2, y - w / 2), (x + w / 2, y - w / 2), (x + w / 2, y + w / 2), (x - w / 2, y + w / 2)]
    for k in range(4):
        a, b = c[k], c[(k + 1) % 4]
        i = p.v((a[0], a[1], z), (0, 0))
        p.v((b[0], b[1], z), (1, 0))
        p.v((b[0], b[1], z + h), (1, h / 4))
        p.v((a[0], a[1], z + h), (0, h / 4))
        p.quad(i, i + 1, i + 2, i + 3)
    i = p.v((c[0][0], c[0][1], z + h), (0, 0))
    for q in c[1:]:
        p.v((q[0], q[1], z + h), (1, 1))
    p.quad(i, i + 1, i + 2, i + 3)
    return p


def _runs(xs):
    out = []
    for i, x in enumerate(xs):
        if out and out[-1][0] == x:
            out[-1][2] = i
        else:
            out.append([x, i, i])
    return [tuple(r) for r in out]


def _cover_area(vm, A, owner, host_of, isl_host, areas_of, allowed, log):
    """Every point of the island must be in an area that loads the island host."""
    from shapely.geometry import Point
    bad = []
    minx, miny, maxx, maxy = A.bounds
    y = miny
    while y <= maxy:
        x = minx
        while x <= maxx:
            if A.contains(Point(x, y)):
                hit = vm.drivable_at(x, y)
                if not hit or not any(s in areas_of[isl_host] for s in hit[:1]):
                    bad.append((round(x), round(y), hit))
            x += 20
        y += 20
    if bad:
        log('island points without the island host loaded: %d (e.g. %s)' % (len(bad), bad[:3]))
        # grow the area owning the island road over the island
        from collections import Counter
        sec = Counter(o for o, h in zip(owner, host_of) if h == isl_host).most_common(1)[0][0]
        _, ch = fit_boundaries(vm, [b[:2] for b in bad], 10.0, allowed & areas_of[isl_host], areas=(),
                               owner=[sec] * len(bad), log=log)
        return set(ch)
    return set()


def check_budget(vm, sizes0, sizes, budget, max_section, region, log):
    worst = []
    for v in vm.visible:
        t0 = sum(sizes0.get(s, 0) for s in v.sections)
        t1 = sum(sizes.get(s, 0) for s in v.sections)
        if t1 != t0:
            worst.append((t1, v.section, t0))
    worst.sort(reverse=True)
    over = [w for w in worst if w[0] > budget]
    big = [(e['tag'], e['size']) for e in region.entries if e['size'] > max_section and e['tag'][:1] not in 'XYZ']
    log('load lists changed: %d, largest now %s (budget %d)' % (len(worst), worst[:5], budget))
    if over:
        raise RuntimeError('load lists over budget: %s' % over)
    if big:
        raise RuntimeError('sections larger than the largest shipped one (%d): %s' % (max_section, big))
    pts = max(len(b.points) for b in vm.boundaries)
    if pts > MAX_BOUNDARY_POINTS:
        raise RuntimeError('a boundary has %d points (max %d)' % (pts, MAX_BOUNDARY_POINTS))
    return dict(lists=[(s, t0, t1) for t1, s, t0 in worst], max_points=pts)


# ------------------------------------------------------------------ buildings

def _place_buildings(region, sp, host, plan, center, st, land, coast, corridor, made, log):
    """Copy shipped building/tree/light models into ``host`` and place them along
    the island road like a city block. Returns collision polygons."""
    from shapely.geometry import Polygon, Point
    from shapely import affinity
    idx = _model_index(region)
    tmpl = _instance_templates(region)
    copies = {}

    def copy(src):
        if src in copies:
            return copies[src]
        node = idx.get(src)
        if node is None:
            raise KeyError('model %s not found' % src)
        m = decode(node)
        name = ('XB_WR_' if src.startswith('XB_') else src[:3] + 'WR_') + src[3:][:18]
        name = name[:27]
        mm = MeshData()
        mm.verts, mm.uvs, mm.colors = list(m.verts), list(m.uvs), list(m.colors)
        mm.indices = list(m.indices)
        mm.textures = list(m.textures)
        mm.materials = list(m.materials)
        mm.normals, mm.stride = m.normals, m.stride
        _encode_new(region, host, name, mm)
        lo = tuple(min(v[k] for v in m.verts) for k in range(3))
        hi = tuple(max(v[k] for v in m.verts) for k in range(3))
        radius = max(math.sqrt(sum(c * c for c in v)) for v in m.verts)
        info = tmpl[src[:3].upper().encode()]
        ii = sp.add_model(info[0], name, bin_hash(name), len(m.indices) // 3, radius)
        copies[src] = (name, ii, lo, hi, info)
        made.append((host, name, len(m.verts), len(m.indices) // 3))
        return copies[src]

    def place(src, x, y, z, heading):
        name, ii, lo, hi, info = copy(src)
        c, s = math.cos(heading), math.sin(heading)
        rot = (c, s, 0.0, -s, c, 0.0, 0.0, 0.0, 1.0)
        corners = [(px, py, pz) for px in (lo[0], hi[0]) for py in (lo[1], hi[1]) for pz in (lo[2], hi[2])]
        w = [(p[0] * c - p[1] * s + x, p[0] * s + p[1] * c + y, p[2] + z) for p in corners]
        bmin = tuple(min(q[k] for q in w) for k in range(3))
        bmax = tuple(max(q[k] for q in w) for k in range(3))
        q = [int(round(v * 8192)) for v in rot]
        sp.add_instance(ii, bmin, bmax, info[1], info[2], t=(x, y, z), rot=tuple(q))
        return lo, hi

    fr = frames(center)
    on = [i for i in range(len(center)) if land[i]]
    zc = plan['island_z']
    inner = coast.buffer(-10)
    taken = []
    polys = []
    nb = 0
    for side in (1, -1):
        k = 0
        s_next = st[on[0]] + 40
        for i in on:
            if st[i] < s_next or st[i] > st[on[-1]] - 40:
                continue
            placed = False
            for tries in range(len(BUILDINGS)):
                src = BUILDINGS[(k + tries + (side < 0) * 4) % len(BUILDINGS)]
                m = decode(idx[src])
                lo = [min(v[j] for v in m.verts) for j in range(3)]
                hi = [max(v[j] for v in m.verts) for j in range(3)]
                if lo[2] > 1.0 or hi[2] - lo[2] > 140:
                    continue
                length, depth = hi[0] - lo[0], hi[1] - lo[1]
                (tx, ty), (nx, ny) = fr[i]
                heading = math.atan2(ty, tx) + (math.pi if side < 0 else 0.0)
                d = HW.HALF + HW.SIDEWALK + 5 + depth / 2
                cxl, cyl = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
                x = center[i][0] + side * nx * d
                y = center[i][1] + side * ny * d
                # local centre -> world offset
                c, s = math.cos(heading), math.sin(heading)
                px, py = x - (cxl * c - cyl * s), y - (cxl * s + cyl * c)
                fp = Polygon([(lo[0], lo[1]), (hi[0], lo[1]), (hi[0], hi[1]), (lo[0], hi[1])])
                fp = affinity.rotate(fp, heading, origin=(0, 0), use_radians=True)
                fp = affinity.translate(fp, px, py)
                if not inner.contains(fp) or fp.intersects(corridor.buffer(3)) or any(fp.buffer(6).intersects(t) for t in taken):
                    continue
                place(src, px, py, zc, heading)
                for comp in COMPANIONS.get(src, []):
                    if comp in idx:
                        place(comp, px, py, zc, heading)
                taken.append(fp)
                pts = list(fp.exterior.coords)[:4]
                for e in range(4):
                    polys += C.make_wall(pts[e], pts[(e + 1) % 4])
                nb += 1
                s_next = st[i] + length + 12
                k += 1 + tries
                placed = True
                break
            if not placed:
                s_next = st[i] + 16
    # street lights every 40 m, trees in between, on both sidewalks
    nl = nt = 0
    s_l = st[on[0]] + 20
    for i in on:
        if st[i] < s_l or st[i] > st[on[-1]] - 20:
            continue
        s_l = st[i] + 40
        (tx, ty), (nx, ny) = fr[i]
        for side in (1, -1):
            d = HW.HALF + 1.2
            hd = math.atan2(ty, tx) + (math.pi / 2 if side > 0 else -math.pi / 2)
            place(LIGHT, center[i][0] + side * nx * d, center[i][1] + side * ny * d, zc, hd)
            nl += 1
            dt = HW.HALF + HW.SIDEWALK - 1.5
            j = min(len(center) - 1, i + 2)
            tp = Point(center[j][0] + side * fr[j][1][0] * dt, center[j][1] + side * fr[j][1][1] * dt)
            if all(tp.distance(t) > 3 for t in taken):
                place(TREE, tp.x, tp.y, zc, st[j] * 0.37)
                nt += 1
    log('island: %d buildings, %d street lights, %d trees' % (nb, nl, nt))
    return polys
