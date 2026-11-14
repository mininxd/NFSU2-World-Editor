"""NFSU2 World Modeller - prototype Blender importer.

Run headless:
  blender -b --python import_world.py -- --tracks ./TRACKS --region L4RA \
          --sections C21,C22,B26 [--instances] --blend out.blend --obj out.obj --png preview.png

Imports the SolidObjects (0x80134010) stored in the chosen streaming sections of
STREAML4Rx.BUN (located through the 0x00034110 section table in L4Rx.BUN), and
optionally the section's scenery instances (0x00034103) that place library models.
Each Blender object keeps custom properties (section, hash, name, file offset) so a
future exporter can write edits back.
Coordinates: game is Z-up and is kept 1:1 (no axis swap). Triangles are D3D
wound; kept unchanged, which yields outward/upward normals in Blender (checked).
"""
import sys, os, struct, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy, bmesh
from mathutils import Matrix, Vector
from nfsu2lib import section_table, find, chunks, payload_start, parse_object

def args():
    a = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    o = dict(tracks='./TRACKS', region='L4RA', sections='C21', instances=False,
             blend=None, obj=None, png=None)
    i = 0
    while i < len(a):
        k = a[i].lstrip('-')
        if k == 'instances': o[k] = True; i += 1
        else: o[k] = a[i + 1]; i += 2
    return o

_mats = {}
def material(texhash):
    name = 'tex_%08X' % texhash
    if name in _mats: return _mats[name]
    m = bpy.data.materials.new(name); m['nfs_texture_hash'] = '%08X' % texhash
    # deterministic pastel colour per texture so materials are distinguishable without textures
    h = texhash * 2654435761 & 0xFFFFFFFF
    m.diffuse_color = (0.35 + (h & 255) / 512, 0.35 + (h >> 8 & 255) / 512, 0.35 + (h >> 16 & 255) / 512, 1)
    _mats[name] = m
    return m

def build_mesh(o, name):
    me = bpy.data.meshes.new(name)
    # stored winding kept as-is: verified on C21/C22/B26 that it gives upward (+Z)
    # normals for all TRN_ ground in Blender (reversing gave 0 up / 6942 down)
    faces = o['faces']
    me.from_pydata(o['verts'], [], faces)
    me.uv_layers.new(name='UVMap')
    me.color_attributes.new('prelight', 'BYTE_COLOR', 'CORNER')
    # re-fetch after both layers exist (adding a layer reallocates customdata)
    loop_v = [0] * len(me.loops); me.loops.foreach_get('vertex_index', loop_v)
    uv = []; cl = []
    for v in loop_v:
        u, vv = o['uvs'][v]; uv += (u, 1.0 - vv)
        r, g, b, a = o['cols'][v]; cl += (r / 255, g / 255, b / 255, a / 255)
    me.uv_layers['UVMap'].data.foreach_set('uv', uv)
    me.color_attributes['prelight'].data.foreach_set('color', cl)
    slots = {}
    for mi, (first, cnt, tex) in enumerate(o['mats']):
        th = o['tex'][tex] if tex < len(o['tex']) else 0
        if th not in slots:
            slots[th] = len(me.materials); me.materials.append(material(th))
    remap = [slots[o['tex'][t] if t < len(o['tex']) else 0] for _, _, t in o['mats']]
    for p, fm in zip(me.polygons, o['face_mat']):
        p.material_index = remap[fm] if fm < len(remap) else 0
    me.validate(); me.update()
    return me

def game_matrix(m):
    # stored row-major, translation in row 3 (row-vector convention) -> Blender column-vector
    return Matrix([m[0:4], m[4:8], m[8:12], m[12:16]]).transposed()

def is_preplaced(o):
    V = o['verts']; lo = [min(v[k] for v in V) for k in range(3)]; hi = [max(v[k] for v in V) for k in range(3)]
    c = Vector([(lo[k] + hi[k]) / 2 for k in range(3)]); diag = (Vector(hi) - Vector(lo)).length
    return c.length > diag

def main():
    A = args()
    l4 = open(os.path.join(A['tracks'], A['region'] + '.BUN'), 'rb').read()
    sbuf = open(os.path.join(A['tracks'], 'STREAM' + A['region'] + '.BUN'), 'rb').read()
    table = {e['tag']: e for e in section_table(l4)}
    for ob in list(bpy.data.objects): bpy.data.objects.remove(ob)
    scene = bpy.context.scene
    want = [s.strip() for s in A['sections'].split(',') if s.strip()]
    stats = {}
    model_index = None
    for tag in want:
        e = table[tag]
        coll = bpy.data.collections.new('SECTION_' + tag); scene.collection.children.link(coll)
        coll['nfs_section'] = tag; coll['nfs_section_number'] = e['num']
        coll['nfs_stream_offset'] = e['offset']; coll['nfs_stream_size'] = e['size']
        scoll = bpy.data.collections.new('STORAGE_' + tag); coll.children.link(scoll)
        scoll.hide_render = True; scoll.hide_viewport = True
        n_obj = n_tri = n_store = 0; placed = set(); cen = Vector(e['center'])
        for d, s in find(sbuf, e['offset'], e['offset'] + e['size'], 0x80134010):
            o = parse_object(sbuf, d, s)
            if not o or not o['faces']: continue
            me = build_mesh(o, o['name'])
            ob = bpy.data.objects.new(o['name'], me)
            ob.matrix_world = game_matrix(o['matrix'])
            ob['nfs_hash'] = '%08X' % o['hash']; ob['nfs_name'] = o['name']; ob['nfs_section'] = tag
            ob['nfs_chunk_offset'] = d - 8; ob['nfs_chunk_size'] = s + 8; ob['nfs_vertex_stride'] = o['stride']
            ob['nfs_kind'] = 'solid'
            # models parked on the far 'storage grid' (XB_* etc) are only drawn via scenery instances
            wc = ob.matrix_world @ Vector([sum(v[k] for v in o['verts']) / len(o['verts']) for k in range(3)])
            if (wc.xy - cen.xy).length > 1500 and not o['name'].startswith(('PAN_', 'SKYDOME')):
                ob['nfs_storage'] = True; scoll.objects.link(ob); n_store += 1; continue
            coll.objects.link(ob); n_obj += 1; n_tri += len(o['faces'])
            placed.add((o['hash'], tuple(round(x, 2) for x in o['matrix'])))
        n_inst = 0
        if A['instances']:
            if model_index is None:  # hash -> (offset,size) of every SolidObject in the region
                model_index = {}
                for ee in table.values():
                    for d, s in find(sbuf, ee['offset'], ee['offset'] + ee['size'], 0x80134010):
                        cd = next((cd for c, cd, cs in chunks(sbuf, d, d + s) if c == 0x134011), None)
                        if cd is None: continue
                        h, = struct.unpack_from('<I', sbuf, payload_start(sbuf, 0x134011, cd, 0) + 0x10)
                        model_index.setdefault(h, (d, s))
            meshes = {}
            icoll = bpy.data.collections.new('SCENERY_' + tag); coll.children.link(icoll)
            for d, s in find(sbuf, e['offset'], e['offset'] + e['size'], 0x80034100):
                ch = {c: (dd, ss) for c, dd, ss in chunks(sbuf, d, d + s)}
                if not all(k in ch for k in (0x34102, 0x34103)): continue
                id_, is_ = ch[0x34102]
                infos = [struct.unpack_from('<I', sbuf, q + 0x20)[0] for q in range(id_, id_ + is_ - 67, 68)]
                rd, rs = ch[0x34103]; rp = payload_start(sbuf, 0x34103, rd, rs)
                for q in range(rp, rd + rs - 63, 64):
                    ii, = struct.unpack_from('<H', sbuf, q + 0x18)
                    if ii >= len(infos) or infos[ii] not in model_index: continue
                    h = infos[ii]
                    if h not in meshes:
                        o = parse_object(sbuf, *model_index[h])
                        meshes[h] = (build_mesh(o, o['name']), o) if o and o['faces'] and not is_preplaced(o) else None
                    if meshes[h] is None: continue
                    t = struct.unpack_from('<3f', sbuf, q + 0x20)
                    R = [x / 8192 for x in struct.unpack_from('<9h', sbuf, q + 0x2C)]
                    gm = [R[0], R[1], R[2], 0, R[3], R[4], R[5], 0, R[6], R[7], R[8], 0, t[0], t[1], t[2], 1]
                    key = (h, tuple(round(x, 2) for x in gm))
                    if key in placed: continue
                    placed.add(key)
                    me, o = meshes[h]
                    ob = bpy.data.objects.new(o['name'] + '.inst', me)  # linked duplicate: shares mesh
                    ob.matrix_world = game_matrix(gm)
                    ob['nfs_kind'] = 'scenery_instance'; ob['nfs_hash'] = '%08X' % h; ob['nfs_section'] = tag
                    ob['nfs_record_offset'] = q
                    icoll.objects.link(ob); n_inst += 1
        stats[tag] = (n_obj, n_tri, n_inst)
        print(f'[nfsu2] section {tag}: {n_obj} solids (+{n_store} storage-grid models hidden), {n_tri} tris, {n_inst} scenery instances')

    if A['blend']:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(A['blend']))
    if A['obj']:
        bpy.ops.wm.obj_export(filepath=os.path.abspath(A['obj']), export_materials=True,
                              export_colors=True, forward_axis='Y', up_axis='Z')
    if A['png']:
        render_preview(A['png'])

def render_preview(path):
    scene = bpy.context.scene
    obs = [o for o in scene.objects if o.type == 'MESH' and not o.get('nfs_storage')]
    pts = [o.matrix_world @ Vector(c) for o in obs for c in o.bound_box]
    lo = Vector([min(p[k] for p in pts) for k in range(3)]); hi = Vector([max(p[k] for p in pts) for k in range(3)])
    c = (lo + hi) / 2; r = (hi - lo).length / 2
    cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam')); scene.collection.objects.link(cam)
    # orthographic, looking straight down (north = +Y up in the image)
    cam.data.type = 'ORTHO'; cam.data.clip_end = (hi.z - lo.z) + 2000
    ext = hi - lo; aspect = 1600 / 1000
    cam.data.ortho_scale = max(ext.x, ext.y * aspect) * 1.05
    cam.location = (c.x, c.y, hi.z + 500); cam.rotation_euler = (0, 0, 0)
    scene.camera = cam
    scene.render.engine = 'BLENDER_WORKBENCH'
    sh = scene.display.shading
    sh.light = 'STUDIO'; sh.color_type = 'MATERIAL'; sh.show_cavity = True; sh.show_shadows = False
    sh.background_type = 'VIEWPORT'
    scene.render.resolution_x, scene.render.resolution_y = 1600, 1000
    scene.render.filepath = os.path.abspath(path)
    bpy.ops.render.render(write_still=True)

main()
