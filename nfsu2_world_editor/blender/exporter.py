"""Write edited Blender objects back into a copy of the game files.

For every ``SECTION_<tag>`` collection created by the importer:

* solid objects (``nfs_kind == 'solid'``) whose mesh or transform changed since
  import are re-encoded in place (vertex positions/UVs/prelight colours,
  any vertex/triangle count up to 65535 vertices, materials by texture hash);
* new mesh objects linked directly in the collection are added to the
  section's first solid list as new SolidObjects (world-space vertices,
  identity matrix);
* everything else keeps its original bytes.

The source files are read from the TRACKS folder the section was imported
from; the result goes to a different folder (``STREAM<region>.BUN`` +
``<region>.BUN``). Collision (``0x80034130``) is NOT rebuilt yet: edited
geometry looks right in game but cars still drive on the old surface.
"""
import os

import bpy
from mathutils import Matrix

from ..core import meshcodec
from ..core.meshcodec import MeshData, SOLID_OBJECT
from ..core.solidlist import add_object
from ..core.stream import Region
from ..core.tpk import bin_hash
from .importer import game_matrix, mesh_signature

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


def export_world(out_dir, context=None, tracks=None, region=None, log=print):
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
    stats = dict(edited=0, added=0, unchanged=0, sections=set())
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
    sp, lp = reg.write(out_dir)
    stats['stream'], stats['l4'] = sp, lp
    stats['sections'] = sorted(stats['sections'])
    log('[nfsu2] export: %d edited, %d added, %d unchanged -> %s' % (stats['edited'], stats['added'],
                                                                    stats['unchanged'], sp))
    return stats


def reg_path(tracks, region):
    from ..core.files import region_paths
    return str(region_paths(tracks, region)[2])
