"""Build Blender objects from NFSU2 streaming sections.

Coordinates: the game is Z-up and is kept 1:1 (no axis swap). Triangles keep
their stored D3D winding, which gives upward normals for ground in Blender.
Every object carries ``nfs_*`` custom properties (section, hash, file offset...)
so a future exporter can write edits back.
"""
import os

import bpy
from mathutils import Matrix, Vector

from ..core import (find, parse_object, section_table, build_model_index,
                    iter_scenery_instances, is_preplaced, region_paths, read_bytes,
                    SOLID_OBJECT)

STORAGE_DISTANCE = 1500.0   # models this far from the section centre sit on the 'storage grid'


def parse_sections(text):
    return [s.strip().upper() for s in text.replace(';', ',').replace(' ', ',').split(',') if s.strip()]


def _material(texhash, cache):
    name = 'tex_%08X' % texhash
    m = cache.get(name) or bpy.data.materials.get(name)
    if m is None:
        m = bpy.data.materials.new(name)
        m['nfs_texture_hash'] = '%08X' % texhash
        # deterministic pastel colour per texture so materials differ without textures
        h = texhash * 2654435761 & 0xFFFFFFFF
        m.diffuse_color = (0.35 + (h & 255) / 512, 0.35 + (h >> 8 & 255) / 512,
                           0.35 + (h >> 16 & 255) / 512, 1.0)
    cache[name] = m
    return m


def build_mesh(o, name, cache):
    me = bpy.data.meshes.new(name)
    me.from_pydata(o['verts'], [], o['faces'])
    me.uv_layers.new(name='UVMap')
    me.color_attributes.new('prelight', 'BYTE_COLOR', 'CORNER')
    # fetch loops only after both layers exist (adding a layer reallocates customdata)
    loop_v = [0] * len(me.loops)
    me.loops.foreach_get('vertex_index', loop_v)
    uv, cl = [], []
    for v in loop_v:
        u, vv = o['uvs'][v]
        uv += (u, 1.0 - vv)
        r, g, b, a = o['cols'][v]
        cl += (r / 255, g / 255, b / 255, a / 255)
    me.uv_layers['UVMap'].data.foreach_set('uv', uv)
    me.color_attributes['prelight'].data.foreach_set('color', cl)
    tex = o['tex']
    slots = {}
    for _, _, t in o['mats']:
        th = tex[t] if t < len(tex) else 0
        if th not in slots:
            slots[th] = len(me.materials)
            me.materials.append(_material(th, cache))
    remap = [slots[tex[t] if t < len(tex) else 0] for _, _, t in o['mats']]
    for p, fm in zip(me.polygons, o['face_mat']):
        p.material_index = remap[fm] if fm < len(remap) else 0
    me.validate()
    me.update()
    return me


def game_matrix(m):
    """Row-major, translation in row 3 (row-vector) -> Blender column-vector Matrix."""
    return Matrix([m[0:4], m[4:8], m[8:12], m[12:16]]).transposed()


def _key(h, m):
    return (h, tuple(round(x, 2) for x in m))


def import_world(tracks, region='L4RA', sections=('C21',), instances=True, context=None,
                 log=print):
    """Import ``sections`` of ``region`` from the TRACKS folder into the current scene.

    Returns ``{tag: (solids, triangles, scenery_instances)}``. Raises
    FileNotFoundError / KeyError with a readable message on bad input.
    """
    context = context or bpy.context
    if isinstance(sections, str):
        sections = parse_sections(sections)
    tracks_dir, l4_path, stream_path = region_paths(tracks, region)
    table = {e['tag'].upper(): e for e in section_table(read_bytes(l4_path))}
    unknown = [t for t in sections if t not in table]
    if unknown:
        raise KeyError('Unknown section(s) %s in %s. Examples: %s' % (
            ', '.join(unknown), l4_path.name, ', '.join(sorted(table)[:12])))
    sbuf = read_bytes(stream_path)
    scene = context.scene
    cache = {}
    stats = {}
    model_index = None
    for tag in sections:
        e = table[tag]
        coll = bpy.data.collections.new('SECTION_' + tag)
        scene.collection.children.link(coll)
        coll['nfs_region'] = region.upper()
        coll['nfs_section'] = tag
        coll['nfs_section_number'] = e['num']
        coll['nfs_stream_offset'] = e['offset']
        coll['nfs_stream_size'] = e['size']
        scoll = bpy.data.collections.new('STORAGE_' + tag)
        coll.children.link(scoll)
        scoll.hide_render = True
        scoll.hide_viewport = True
        n_obj = n_tri = n_store = 0
        placed = set()
        cen = Vector(e['center'])
        for d, s in find(sbuf, e['offset'], e['offset'] + e['size'], SOLID_OBJECT):
            o = parse_object(sbuf, d, s)
            if not o or not o['faces']:
                continue
            ob = bpy.data.objects.new(o['name'], build_mesh(o, o['name'], cache))
            ob.matrix_world = game_matrix(o['matrix'])
            ob['nfs_hash'] = '%08X' % o['hash']
            ob['nfs_name'] = o['name']
            ob['nfs_section'] = tag
            ob['nfs_chunk_offset'] = d - 8
            ob['nfs_chunk_size'] = s + 8
            ob['nfs_vertex_stride'] = o['stride']
            ob['nfs_kind'] = 'solid'
            # models parked on the far 'storage grid' (XB_* etc.) are drawn only via instances
            n = len(o['verts'])
            wc = ob.matrix_world @ Vector([sum(v[k] for v in o['verts']) / n for k in range(3)])
            if (wc.xy - cen.xy).length > STORAGE_DISTANCE and not o['name'].startswith(('PAN_', 'SKYDOME')):
                ob['nfs_storage'] = True
                scoll.objects.link(ob)
                n_store += 1
                continue
            coll.objects.link(ob)
            n_obj += 1
            n_tri += len(o['faces'])
            placed.add(_key(o['hash'], o['matrix']))
        n_inst = 0
        if instances:
            if model_index is None:
                model_index = build_model_index(sbuf, table.values())
            meshes = {}
            icoll = bpy.data.collections.new('SCENERY_' + tag)
            coll.children.link(icoll)
            for h, gm, rec in iter_scenery_instances(sbuf, e['offset'], e['size']):
                if h not in model_index:
                    continue
                if h not in meshes:
                    o = parse_object(sbuf, *model_index[h])
                    ok = o and o['faces'] and not is_preplaced(o)
                    meshes[h] = (build_mesh(o, o['name'], cache), o) if ok else None
                if meshes[h] is None:
                    continue
                key = _key(h, gm)
                if key in placed:
                    continue
                placed.add(key)
                me, o = meshes[h]
                ob = bpy.data.objects.new(o['name'] + '.inst', me)  # linked duplicate
                ob.matrix_world = game_matrix(gm)
                ob['nfs_kind'] = 'scenery_instance'
                ob['nfs_hash'] = '%08X' % h
                ob['nfs_section'] = tag
                ob['nfs_record_offset'] = rec
                icoll.objects.link(ob)
                n_inst += 1
        stats[tag] = (n_obj, n_tri, n_inst)
        log('[nfsu2] section %s: %d solids (+%d storage-grid models hidden), %d tris, %d scenery instances'
            % (tag, n_obj, n_store, n_tri, n_inst))
    return stats


def clear_scene():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)


def render_preview(path, resolution=(1600, 1000)):
    """Top-down orthographic Workbench render of all visible meshes to ``path``."""
    scene = bpy.context.scene
    obs = [o for o in scene.objects if o.type == 'MESH' and not o.get('nfs_storage')]
    if not obs:
        raise RuntimeError('nothing to render')
    pts = [o.matrix_world @ Vector(c) for o in obs for c in o.bound_box]
    lo = Vector([min(p[k] for p in pts) for k in range(3)])
    hi = Vector([max(p[k] for p in pts) for k in range(3)])
    c = (lo + hi) / 2
    cam = bpy.data.objects.new('nfs_preview_cam', bpy.data.cameras.new('nfs_preview_cam'))
    scene.collection.objects.link(cam)
    cam.data.type = 'ORTHO'
    cam.data.clip_end = (hi.z - lo.z) + 2000
    ext = hi - lo
    aspect = resolution[0] / resolution[1]
    cam.data.ortho_scale = max(ext.x, ext.y * aspect) * 1.05
    cam.location = (c.x, c.y, hi.z + 500)
    cam.rotation_euler = (0, 0, 0)
    scene.camera = cam
    scene.render.engine = 'BLENDER_WORKBENCH'
    sh = scene.display.shading
    sh.light = 'STUDIO'
    sh.color_type = 'MATERIAL'
    sh.show_cavity = True
    sh.show_shadows = False
    sh.background_type = 'VIEWPORT'
    scene.render.resolution_x, scene.render.resolution_y = resolution
    scene.render.filepath = os.path.abspath(str(path))
    bpy.ops.render.render(write_still=True)
