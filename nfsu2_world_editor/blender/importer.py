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
from ..core.stream import Region
from .textures import TextureLibrary, build_material

STORAGE_DISTANCE = 1500.0   # models this far from the section centre sit on the 'storage grid'


ALL_SECTIONS = ('ALL', '*')
PANORAMA_PREFIXES = ('PAN_', 'SKYDOME')


def parse_sections(text):
    """Split a tag list. ``'all'`` / ``'*'`` (or an empty string) means every section."""
    tags = [s.strip().upper() for s in text.replace(';', ',').replace(' ', ',').split(',') if s.strip()]
    return [] if any(t in ALL_SECTIONS for t in tags) else tags


def _material(texhash, cache, lib=None, section=None):
    m = cache.get(texhash)
    if m is None:
        m = cache[texhash] = build_material(texhash, lib, section)
    return m


def build_mesh(o, name, cache, lib=None, section=None):
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
    # store the game's bytes as-is (sRGB byte colour) so they export back exactly
    me.color_attributes['prelight'].data.foreach_set('color_srgb', cl)
    tex = o['tex']
    slots = {}
    for _, _, t in o['mats']:
        th = tex[t] if t < len(tex) else 0
        if th not in slots:
            slots[th] = len(me.materials)
            me.materials.append(_material(th, cache, lib, section))
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


def global_texture_files(tracks_dir, global_dir=None):
    """BUN/BIN files of the game's GLOBAL folder (sibling of TRACKS unless given)."""
    from pathlib import Path
    d = Path(global_dir) if global_dir else Path(tracks_dir).parent / 'GLOBAL'
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in ('.bun', '.bin'))


def import_world(tracks, region='L4RA', sections=('C21',), instances=True, context=None,
                 log=print, textures=True, skip_panoramas=False, collision=False,
                 global_dir=None):
    """Import ``sections`` of ``region`` from the TRACKS folder into the current scene.

    An empty ``sections`` list (or ``'all'``) imports every section of the region.
    ``skip_panoramas`` leaves out the distant backdrop models (``PAN_*``,
    ``SKYDOME*``), which otherwise surround the map with huge far geometry.
    ``collision`` adds a read-only ``COLLISION_<tag>`` mesh per section (ground
    faces, barrier walls drawn 3 m tall) for checking where cars can drive.
    ``global_dir`` (default: a ``GLOBAL`` folder next to TRACKS, if any) adds the
    game's shared texture packs (GLOBALB.BUN, InGameCommon.bun, ...) for
    textures the region itself does not carry.
    Returns ``{tag: (solids, triangles, scenery_instances)}``. Raises
    FileNotFoundError / KeyError with a readable message on bad input.
    """
    context = context or bpy.context
    if isinstance(sections, str):
        sections = parse_sections(sections)
    tracks_dir, l4_path, stream_path = region_paths(tracks, region)
    table = {e['tag'].upper(): e for e in section_table(read_bytes(l4_path))}
    if not sections:
        sections = sorted(table)
    unknown = [t for t in sections if t not in table]
    if unknown:
        raise KeyError('Unknown section(s) %s in %s. Examples: %s' % (
            ', '.join(unknown), l4_path.name, ', '.join(sorted(table)[:12])))
    reg = Region(read_bytes(stream_path), read_bytes(l4_path), region.upper())
    sbuf = reg.src
    lib = None
    if textures:
        lib = TextureLibrary(reg)
        for f in global_texture_files(tracks_dir, global_dir):
            lib.add_file(f, log)
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
        coll['nfs_tracks_dir'] = str(tracks_dir)
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
            if skip_panoramas and o['name'].upper().startswith(PANORAMA_PREFIXES):
                continue
            ob = bpy.data.objects.new(o['name'], build_mesh(o, o['name'], cache, lib, tag))
            ob.matrix_world = game_matrix(o['matrix'])
            ob['nfs_hash'] = '%08X' % o['hash']
            ob['nfs_name'] = o['name']
            ob['nfs_section'] = tag
            ob['nfs_chunk_offset'] = d - 8
            ob['nfs_chunk_size'] = s + 8
            ob['nfs_vertex_stride'] = o['stride']
            ob['nfs_kind'] = 'solid'
            ob['nfs_region'] = region.upper()
            # models parked on the far 'storage grid' (XB_* etc.) are drawn only via instances
            n = len(o['verts'])
            wc = ob.matrix_world @ Vector([sum(v[k] for v in o['verts']) / n for k in range(3)])
            if (wc.xy - cen.xy).length > STORAGE_DISTANCE and not o['name'].startswith(('PAN_', 'SKYDOME', 'TRN_')):
                ob['nfs_storage'] = True
                scoll.objects.link(ob)
                ob['nfs_sig'] = mesh_signature(ob)
                n_store += 1
                continue
            coll.objects.link(ob)
            ob['nfs_sig'] = mesh_signature(ob)
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
                    meshes[h] = (build_mesh(o, o['name'], cache, lib, tag), o) if ok else None
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
                ob['nfs_region'] = region.upper()
                ob['nfs_game_matrix'] = [float(x) for x in gm]
                ob['nfs_basis'] = [float(x) for row in ob.matrix_basis for x in row]
                icoll.objects.link(ob)
                n_inst += 1
        if collision:
            build_collision_object(sbuf, e, coll, tag)
        stats[tag] = (n_obj, n_tri, n_inst)
        log('[nfsu2] section %s: %d solids (+%d storage-grid models hidden), %d tris, %d scenery instances'
            % (tag, n_obj, n_store, n_tri, n_inst))
    return stats


SURFACE_COLOURS = {1: (0.25, 0.25, 0.28, 1), 15: (0.2, 0.6, 0.2, 1), 20: (0.9, 0.2, 0.1, 1)}


def build_collision_object(sbuf, entry, coll, tag):
    """Mesh of the section's collision pack (``0x80034130``), coloured by surface."""
    from ..core import collision as C
    obs = []
    for off, total in C.find_packs(sbuf, entry['offset'], entry['offset'] + entry['size']):
        pack = C.CollisionPack.parse(bytes(sbuf[off + 8:off + total]))
        live = {r[0] for r in pack.refs}
        hz = {}
        for q in pack.polys:
            if not q.is_barrier:
                for x, y, z in q.corners():
                    k = (int(x // 8), int(y // 8))
                    hz[k] = max(hz.get(k, z), z)
        verts, faces, surf = [], [], []
        for i, q in enumerate(pack.polys):
            if i not in live:
                continue
            cs = q.corners()
            if q.is_barrier:
                (x0, y0, _), (x1, y1, _) = cs[1], cs[2]
                z = _ground_hint(hz, (x0 + x1) / 2, (y0 + y1) / 2)
                cs = [(x0, y0, z), (x1, y1, z), (x1, y1, z + 3.0), (x0, y0, z + 3.0)]
            elif not (q.flags & C.FLAG_QUAD):
                cs = cs[:3]
            b = len(verts)
            verts.extend(cs)
            faces.append(tuple(range(b, b + len(cs))))
            surf.append(q.surface)
        me = bpy.data.meshes.new('COLLISION_' + tag)
        me.from_pydata(verts, [], faces)
        mats = {}
        for p, s in zip(me.polygons, surf):
            if s not in mats:
                m = bpy.data.materials.get('nfs_coll_%d' % s) or bpy.data.materials.new('nfs_coll_%d' % s)
                m.diffuse_color = SURFACE_COLOURS.get(s, (0.2, 0.4, 0.9, 1))
                mats[s] = len(me.materials)
                me.materials.append(m)
            p.material_index = mats[s]
        me.validate()
        ob = bpy.data.objects.new('COLLISION_' + tag, me)
        ob['nfs_kind'] = 'collision'
        ob['nfs_section'] = tag
        ob.display_type = 'WIRE'
        ob.hide_render = True
        coll.objects.link(ob)
        obs.append(ob)
    return obs


def _ground_hint(hz, x, y):
    """Barriers store no height: draw them on the nearest ground found nearby."""
    cx, cy = int(x // 8), int(y // 8)
    for r in range(4):
        vals = [hz[(cx + i, cy + j)] for i in range(-r, r + 1) for j in range(-r, r + 1) if (cx + i, cy + j) in hz]
        if vals:
            return max(vals)
    return 0.0


def instance_moved(ob, rot_tol=1e-3, pos_tol=1e-3):
    """True when a scenery instance was moved, rotated or scaled since import
    (compares loc/rot/scale, so shipped matrices with shear read as unchanged)."""
    orig = ob.get('nfs_basis')
    if orig is None:
        return False
    cur = [x for row in ob.matrix_basis for x in row]
    for i, (a, b) in enumerate(zip(cur, orig)):
        if abs(a - b) > (pos_tol if i % 4 == 3 else rot_tol):
            return True
    return False


def mesh_signature(ob):
    """Checksum of everything the exporter writes for a solid object. The
    exporter skips objects whose signature still matches the one stored at
    import, so untouched objects keep their original bytes."""
    import hashlib
    from array import array
    me = ob.data
    hsh = hashlib.sha1()
    for coll, attr, n, typ in ((me.vertices, 'co', 3, 'f'), (me.loops, 'vertex_index', 1, 'i'),
                               (me.polygons, 'material_index', 1, 'i')):
        a = array(typ, [0]) * (len(coll) * n)
        coll.foreach_get(attr, a)
        hsh.update(a.tobytes())
    uv = me.uv_layers.active
    if uv is not None:
        a = array('f', [0.0]) * (len(me.loops) * 2)
        uv.data.foreach_get('uv', a)
        hsh.update(a.tobytes())
    ca = me.color_attributes.get('prelight')
    if ca is not None:
        a = array('f', [0.0]) * (len(ca.data) * 4)
        ca.data.foreach_get('color_srgb', a)
        hsh.update(a.tobytes())
    hsh.update(','.join(m.get('nfs_texture_hash', m.name) if m else '-' for m in me.materials).encode())
    # matrix_basis is rebuilt from loc/rot/scale, so it reads the same at import
    # and at export even for shipped matrices with shear (matrix_world does not)
    hsh.update(array('f', [round(x, 4) for row in ob.matrix_basis for x in row]).tobytes())
    return hsh.hexdigest()


def clear_scene():
    """Delete all objects, the importer's collections and now-unused meshes."""
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    for c in list(bpy.data.collections):
        if c.get('nfs_section') is not None or c.name.startswith(('STORAGE_', 'SCENERY_')):
            bpy.data.collections.remove(c)
    for me in list(bpy.data.meshes):
        if me.users == 0:
            bpy.data.meshes.remove(me)


def render_preview(path, resolution=(1600, 1000), color_type='MATERIAL'):
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
    sh.color_type = color_type
    sh.show_cavity = True
    sh.show_shadows = False
    sh.background_type = 'VIEWPORT'
    scene.render.resolution_x, scene.render.resolution_y = resolution
    scene.render.filepath = os.path.abspath(str(path))
    bpy.ops.render.render(write_still=True)
