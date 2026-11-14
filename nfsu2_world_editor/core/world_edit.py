"""Section-level edit helpers shared by the road builder and the Blender exporter.

* place new world-space (``TRN_``-style, identity matrix) models in a section's
  own scenery pack so the game actually draws them;
* write back moved scenery instances (translation, rotation, bounds);
* add collision polygons to a section (ground from upward triangles, barrier
  walls) through :mod:`collision`.

All functions work on a :class:`stream.Region` and its :mod:`bchunk` nodes, so
the region writer handles every size change and alignment.
"""
import math
import struct

from . import bchunk, collision as C
from .bchunk import FILLER_ALIGN
from .scenery import SceneryPack, PACK as SCENERY_PACK, INSTANCES, INST_SIZE

IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


# ---------------------------------------------------------------- scenery

def own_scenery_pack(region, tag):
    """The scenery pack whose header names ``tag``'s own section number."""
    num = region.entry(tag)['num']
    for c in region.section_chunks(tag):
        if c.id == SCENERY_PACK and c.children is not None:
            sp = SceneryPack(c)
            if sp.section == num:
                return sp
    return None


def trn_template(sp):
    """(info record, instance flags, 128-byte block) copied from the pack's first
    ``TRN_*ROAD*`` placement (else its first ``TRN_`` one, else instance 0)."""
    best = None
    for i in range(len(sp.inst)):
        nm = sp.infos[sp.instance(i)['info']][:32].split(b'\0')[0].upper()
        if nm.startswith(b'TRN_') and b'ROAD' in nm:
            best = i
            break
        if best is None and nm.startswith(b'TRN_'):
            best = i
    if best is None:
        if not sp.inst:
            raise ValueError('scenery pack %d has no placement to copy' % sp.section)
        best = 0
    ti = sp.instance(best)
    block = sp.blocks[ti['block']] if ti['block'] < len(sp.blocks) else bytes(128)
    return sp.infos[ti['info']], ti['flags'], block


def place_world_model(sp, template, name, model_hash, verts, ntri):
    """Add a model info + identity placement for a world-space model."""
    info, flags, block = template
    lo = tuple(min(v[k] for v in verts) for k in range(3))
    hi = tuple(max(v[k] for v in verts) for k in range(3))
    radius = max(math.sqrt(sum(c * c for c in v)) for v in verts)
    ii = sp.add_model(info, name, model_hash, ntri, radius)
    return sp.add_instance(ii, lo, hi, flags, block)


def _payload_start(node):
    a = FILLER_ALIGN.get(node.id) if node.aligned else None
    start = node.offset + 8
    return start + ((-start) % a if a else 0)


def find_instance(region, tag, record_offset):
    """(SceneryPack, index) of the shipped instance whose record was at absolute
    STREAM offset ``record_offset`` (as stored by the importer)."""
    for c in region.section_chunks(tag):
        if c.id != SCENERY_PACK or c.children is None:
            continue
        n = c.find(INSTANCES)
        if n is None or n.offset is None:
            continue
        st = _payload_start(n)
        if st <= record_offset < st + n.size:
            k, r = divmod(record_offset - st, INST_SIZE)
            if r == 0:
                return SceneryPack(c), k
    return None, None


def set_instance_transform(sp, i, matrix16, local_bounds):
    """Write a new row-major game matrix (rotation part may include uniform
    scale up to ~4) into instance ``i`` and recompute its world bounds from the
    model's local bounds ``(lo, hi)``."""
    r = bytearray(sp.inst[i])
    rot = [matrix16[k] for k in (0, 1, 2, 4, 5, 6, 8, 9, 10)]
    q = [int(round(x * 8192.0)) for x in rot]
    if any(abs(x) > 32767 for x in q):
        raise ValueError('instance scale too large to store')
    t = matrix16[12:15]
    lo, hi = local_bounds
    pts = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    w = [(p[0] * rot[0] + p[1] * rot[3] + p[2] * rot[6] + t[0],
          p[0] * rot[1] + p[1] * rot[4] + p[2] * rot[7] + t[1],
          p[0] * rot[2] + p[1] * rot[5] + p[2] * rot[8] + t[2]) for p in pts]
    bmin = tuple(min(p[k] for p in w) for k in range(3))
    bmax = tuple(max(p[k] for p in w) for k in range(3))
    struct.pack_into('<3f3f', r, 0, *bmin, *bmax)
    struct.pack_into('<3f9h', r, 32, *t, *q)
    sp.inst[i] = bytes(r)
    sp.force_tree = True


# --------------------------------------------------------------- collision

def collision_node(region, tag):
    for c in region.section_chunks(tag):
        if c.id == C.PACK:
            return c
    return None


def read_collision(node):
    return C.CollisionPack.parse(bytes(bchunk.chunk_bytes(node, 0))[8:])


def set_collision(node, pack):
    kids = bchunk.parse(pack.payload())
    if len(kids) != len(node.children):
        node.children = kids
    else:
        for old, new in zip(node.children, kids):
            if bytes(old.data) != bytes(new.data):
                old.data = bytes(new.data)
                old.dirty = True
    node.dirty = True


def ground_from_triangles(tris, surface=1, min_up=0.5, walls=False, wall_surface=20):
    """Collision polygons for world-space triangles ``[(p0, p1, p2)]``.

    Faces whose normal has ``|z| >= min_up`` become ground triangles (stored
    counter-clockwise in plan like the shipped data). Steeper faces are skipped,
    or, with ``walls``, become barrier walls along their lowest edge."""
    out = []
    for a, b, c in tris:
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        ln = math.sqrt(nx * nx + ny * ny + nz * nz)
        if ln < 1e-9:
            continue
        if abs(nz) / ln >= min_up:
            pts = [a, b, c] if nz > 0 else [a, c, b]
            try:
                out.append(C.make_ground(pts, surface))
            except ValueError:
                pass
        elif walls:
            e = sorted(((a, b), (b, c), (c, a)), key=lambda e: e[0][2] + e[1][2])[0]
            if math.dist(e[0][:2], e[1][:2]) > 0.25:
                out.extend(C.make_wall(e[0][:2], e[1][:2], surface=wall_surface))
    return out


def add_collision(region, tag, polys):
    """Append ``polys`` to ``tag``'s collision pack. Returns the pack."""
    node = collision_node(region, tag)
    if node is None:
        raise ValueError('section %s has no collision pack' % tag)
    pack = read_collision(node)
    pack.add_polygons(polys)
    set_collision(node, pack)
    return pack
