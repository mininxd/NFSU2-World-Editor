"""Write edited Blender objects back into a copy of the game files.

For every ``SECTION_<tag>`` collection created by the importer:

* solid objects (``nfs_kind == 'solid'``) whose mesh or transform changed since
  import are re-encoded in place (vertex positions/UVs/prelight colours,
  any vertex/triangle count up to 65535 vertices, materials by texture hash);
* new mesh objects linked directly in the collection are added to the
  section's first solid list as new SolidObjects (world-space vertices,
  identity matrix);
* new objects also get a placement in the section's own scenery pack (the game
  only draws what scenery places) and, unless ``nfs_collision`` is 0, ground
  collision from their upward faces (surface id from ``nfs_surface``, default
  1 = road; optionally barrier walls from steep faces);
* scenery instances (``nfs_kind == 'scenery_instance'``) that were moved,
  rotated or uniformly scaled are written back (translation, rotation, bounds;
  the bounding-volume tree is rebuilt);
* everything else keeps its original bytes.

The source files are read from the TRACKS folder the section was imported
from; the result goes to a different folder (``STREAM<region>.BUN`` +
``<region>.BUN``). Collision of *edited* shipped objects is not rebuilt: the
old surface stays where it was.
"""
import os

import bpy
from mathutils import Matrix

from ..core import meshcodec, world_edit as WE
from ..core.meshcodec import MeshData, SOLID_OBJECT
from ..core.solidlist import add_object
from ..core.stream import Region
from ..core.tpk import bin_hash
from .importer import game_matrix, mesh_signature, instance_moved

DEFAULT_ARGB = 0xFF808080   # neutral prelight (x2 modulation = unlit texture)


def _argb(c):
    r, g, b, a = (max(0, min(255, int(round(x * 255)))) for x in c)
    return (a << 24) | (r << 16) | (g << 8) | b


def mesh_from_blender(ob, template, to_local, depsgraph=None):
    """Build a MeshData from Blender object ``ob``. ``template`` (MeshData) gives
    the stride, texture list and material records to reuse; ``to_local`` maps
    Blender world space to the game object's local space."""
    ev = ob.evaluated_get(depsgraph) if depsgraph is not None else ob
    me = ev.to_mesh()
    try:
        me.calc_loop_triangles()
        xf = to_local @ ob.matrix_world
        nxf = xf.to_3x3().inverted_safe().transposed()
        uv = me.uv_layers.active
        uvd = uv.data if uv is not None else None
        ca = me.color_attributes.get('prelight') or (me.color_attributes.active_color
                                                     if me.color_attributes else None)
        corner = ca is not None and ca.domain == 'CORNER'
        cdata = ca.data if ca is not None else None
        use_srgb = ca is not None and hasattr(cdata[0] if len(cdata) else None, 'color_srgb')
        want_normals = template.normals is not None
        cnorm = me.corner_normals if want_normals and hasattr(me, 'corner_normals') else None

        textures = list(template.textures)
        slot_of = {}
        for i, h in enumerate(textures):
            slot_of.setdefault(h, i)
        default_slot = template.materials[0][2] if (template.materials and textures) else 0
        mat_slot = []
        for m in me.materials:
            hx = m.get('nfs_texture_hash') if m is not None else None
            if hx is None:
                mat_slot.append(default_slot if textures else None)
                continue
            h = int(str(hx), 16)
            if h not in slot_of:
                slot_of[h] = len(textures)
                textures.append(h)
            mat_slot.append(slot_of[h])
        if not textures:
            textures.append(0)
        mat_slot = [default_slot if s is None else s for s in mat_slot] or [default_slot]

        groups = {}
        order = [s for _f, _c, s, _r in template.materials]
        verts, cols, uvs, norms = [], [], [], ([] if want_normals else None)
        vmap = {}
        lco = [xf @ v.co for v in me.vertices]
        for tri in me.loop_triangles:
            mi = tri.material_index
            slot = mat_slot[mi] if mi < len(mat_slot) else mat_slot[0]
            if slot not in order:
                order.append(slot)
            out = groups.setdefault(slot, [])
            for vi, li in zip(tri.vertices, tri.loops):
                u, v = uvd[li].uv if uvd is not None else (0.0, 0.0)
                if cdata is not None:
                    c = cdata[li if corner else vi]
                    col = _argb(c.color_srgb if use_srgb else c.color)
                else:
                    col = DEFAULT_ARGB
                if want_normals:
                    n = (nxf @ (cnorm[li].vector if cnorm is not None else me.vertices[vi].normal)).normalized()
                    key = (vi, u, v, col, round(n.x, 5), round(n.y, 5), round(n.z, 5))
                else:
                    key = (vi, u, v, col)
                idx = vmap.get(key)
                if idx is None:
                    idx = vmap[key] = len(verts)
                    p = lco[vi]
                    verts.append((p.x, p.y, p.z))
                    cols.append(col)
                    uvs.append((u, 1.0 - v))
                    if want_normals:
                        norms.append((n.x, n.y, n.z))
                out.append(idx)
    finally:
        ev.to_mesh_clear()
    m = MeshData()
    m.name, m.hash, m.matrix, m.stride = template.name, template.hash, template.matrix, template.stride
    m.verts, m.colors, m.uvs, m.normals = verts, cols, uvs, norms
    m.textures = textures
    recs = {}
    for _f, _c, s, r in template.materials:
        recs.setdefault(s, r)
    fallback = template.materials[0][3] if template.materials else None
    first = 0
    for s in order:
        idx = groups.get(s)
        if not idx:
            continue
        m.indices.extend(idx)
        m.materials.append((first, len(idx), s, recs.get(s, fallback)))
        first += len(idx)
    return m


def _object_nodes(region):
    nodes = {}
    for c in region.chunks:
        if c.children is not None:
            for o in c.find_all(SOLID_OBJECT):
                nodes[o.offset] = o
    return nodes


def section_collections(scene):
    return [c for c in bpy.data.collections if c.get('nfs_section') is not None
            and c.name.startswith('SECTION_') and scene.user_of_id(c)]


def _to_game(mat):
    """Blender column-vector Matrix -> row-major game matrix (16 floats)."""
    return tuple(x for row in mat.transposed() for x in row)


def export_world(out_dir, context=None, tracks=None, region=None, log=print, place_scenery=True,
                 rebuild_collision=True, collision_walls=False, move_instances=True):
    """Export every edited section in the scene. Returns a stats dict."""
    context = context or bpy.context
    scene = context.scene
    colls = section_collections(scene)
    if not colls:
        raise RuntimeError('no imported NFSU2 sections (SECTION_* collections) in the scene')
    tracks = tracks or colls[0].get('nfs_tracks_dir')
    region = (region or colls[0].get('nfs_region') or 'L4RA').upper()
    if not tracks:
        raise RuntimeError('unknown source TRACKS folder; pass tracks=')
    reg = Region.open(tracks, region)
    src_dir = os.path.dirname(os.path.abspath(reg_path(tracks, region)))
    if os.path.abspath(out_dir) == src_dir:
        raise RuntimeError('refusing to overwrite the original game files; pick another folder')
    nodes = _object_nodes(reg)
    dg = context.evaluated_depsgraph_get()
    stats = dict(edited=0, added=0, unchanged=0, moved=0, collision_polys=0, sections=set())
    packs = {}      # id(node) -> SceneryPack touched
    new_coll = {}   # tag -> [polygons]
    for coll in colls:
        tag = coll['nfs_section']
        for ob in coll.all_objects:
            if ob.type != 'MESH' or ob.get('nfs_kind') != 'solid':
                continue
            if ob.get('nfs_sig') == mesh_signature(ob):
                stats['unchanged'] += 1
                continue
            node = nodes.get(int(ob['nfs_chunk_offset']))
            if node is None:
                log('[nfsu2] %s: original object not found at offset %s, skipped' % (ob.name, ob['nfs_chunk_offset']))
                continue
            tmpl = meshcodec.decode(node)
            to_local = game_matrix(tmpl.matrix).inverted_safe()
            m = mesh_from_blender(ob, tmpl, to_local, dg)
            meshcodec.encode(node, m)
            stats['edited'] += 1
            stats['sections'].add(tag)
            log('[nfsu2] %s/%s: re-encoded (%d verts, %d tris)' % (tag, ob.name, len(m.verts), len(m.indices) // 3))
        for ob in coll.objects:
            if ob.type != 'MESH' or ob.get('nfs_kind'):
                continue
            name = ob.get('nfs_name') or ob.name.upper().replace(' ', '_').replace('.', '_')
            node = add_object(reg, tag, name)
            tmpl = meshcodec.decode(node)
            tmpl.textures = []
            tmpl.matrix = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
            m = mesh_from_blender(ob, tmpl, Matrix.Identity(4), dg)
            m.name, m.hash = name, bin_hash(name)
            meshcodec.encode(node, m)
            stats['added'] += 1
            stats['sections'].add(tag)
            log('[nfsu2] %s: added new object %s (%d verts, %d tris)' % (tag, name, len(m.verts), len(m.indices) // 3))
            if place_scenery and m.verts:
                sp = WE.own_scenery_pack(reg, tag)
                if sp is None:
                    log('[nfsu2] %s: no scenery pack of its own; %s will not be drawn' % (tag, name))
                else:
                    sp = packs.setdefault(id(sp.node), sp)
                    WE.place_world_model(sp, WE.trn_template(sp), name, m.hash, m.verts, len(m.indices) // 3)
            if rebuild_collision and int(ob.get('nfs_collision', 1)):
                tris = [tuple(m.verts[m.indices[i + k]] for k in range(3)) for i in range(0, len(m.indices) - 2, 3)]
                polys = WE.ground_from_triangles(tris, surface=int(ob.get('nfs_surface', 1)), walls=collision_walls)
                new_coll.setdefault(tag, []).extend(polys)
        if move_instances:
            for ob in coll.all_objects:
                if ob.get('nfs_kind') != 'scenery_instance' or not instance_moved(ob):
                    continue
                sp, i = WE.find_instance(reg, tag, int(ob['nfs_record_offset']))
                if sp is None:
                    log('[nfsu2] %s: scenery record of %s not found, skipped' % (tag, ob.name))
                    continue
                sp = packs.setdefault(id(sp.node), sp)
                lo = tuple(min(c[k] for c in ob.bound_box) for k in range(3))
                hi = tuple(max(c[k] for c in ob.bound_box) for k in range(3))
                try:
                    WE.set_instance_transform(sp, i, _to_game(ob.matrix_world), (lo, hi))
                except ValueError as ex:
                    log('[nfsu2] %s: %s (%s), skipped' % (tag, ob.name, ex))
                    continue
                stats['moved'] += 1
                stats['sections'].add(tag)
    for sp in packs.values():
        sp.commit()
        if not sp.validate():
            raise RuntimeError('scenery tree of section %d failed its check' % sp.section)
    for tag, polys in new_coll.items():
        if not polys:
            continue
        try:
            WE.add_collision(reg, tag, polys)
        except ValueError as ex:
            log('[nfsu2] %s: collision not added (%s)' % (tag, ex))
            continue
        stats['collision_polys'] += len(polys)
        log('[nfsu2] %s: +%d collision polygons' % (tag, len(polys)))
    sp, lp = reg.write(out_dir)
    stats['stream'], stats['l4'] = sp, lp
    stats['sections'] = sorted(stats['sections'])
    log('[nfsu2] export: %d edited, %d added, %d moved props, %d unchanged -> %s'
        % (stats['edited'], stats['added'], stats['moved'], stats['unchanged'], sp))
    return stats


def reg_path(tracks, region):
    from ..core.files import region_paths
    return str(region_paths(tracks, region)[2])
