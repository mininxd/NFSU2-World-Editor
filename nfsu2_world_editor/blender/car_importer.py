"""Import an NFSU2 car (GEOMETRY.BIN + TEXTURES.BIN) into the current scene."""
import math
import os

import bpy
from mathutils import Euler, Matrix, Vector

from ..core import cars, meshcodec
from ..core.files import read_bytes
from .importer import game_matrix
from .textures import TextureLibrary


# Paint colours used when the part's material class is CARSKIN (no stored albedo).
DEFAULT_PAINT = (0.75, 0.06, 0.07, 1.0)  # classic red

CLASS_SHADERS = {
    'CARSKIN': dict(metallic=0.5, roughness=0.16, coat=0.4),
    'WINDSHIELD': dict(metallic=0.0, roughness=0.04, transmission=0.95, alpha=0.42,
                       colour=(0.03, 0.035, 0.04, 1), ior=1.45),
    'HEADLIGHTGLASS': dict(metallic=0.0, roughness=0.05, transmission=0.35, alpha=0.7,
                           emit=10.0, colour=(1.0, 0.98, 0.92, 1), ior=1.45),
    'BRAKELIGHTGLASS': dict(metallic=0.0, roughness=0.06, transmission=0.65, alpha=0.55,
                            emit=5.0, colour=(0.7, 0.02, 0.02, 1)),
    'BRAKELIGHT': dict(metallic=0.0, roughness=0.3, emit=8.0, colour=(0.8, 0.02, 0.02, 1)),
    'HEADLIGHT': dict(metallic=0.85, roughness=0.18, colour=(0.9, 0.9, 0.85, 1), emit=0.4),
    'RUBBER': dict(metallic=0.0, roughness=0.75, colour=(0.03, 0.03, 0.03, 1)),
    'MAGSILVER': dict(metallic=1.0, roughness=0.22, colour=(0.72, 0.72, 0.75, 1)),
    'CHROME': dict(metallic=1.0, roughness=0.06, colour=(0.9, 0.9, 0.92, 1)),
    'ALUMINUM': dict(metallic=1.0, roughness=0.35, colour=(0.65, 0.65, 0.67, 1)),
    'CARBOTTOM': dict(metallic=0.1, roughness=0.85, colour=(0.04, 0.04, 0.04, 1)),
    'BOTTOM': dict(metallic=0.1, roughness=0.85, colour=(0.04, 0.04, 0.04, 1)),
    'BLACK': dict(metallic=0.0, roughness=0.6, colour=(0.02, 0.02, 0.02, 1)),
    'DULLPLASTIC': dict(metallic=0.0, roughness=0.65, colour=(0.045, 0.04, 0.04, 1)),
    'TRIM': dict(metallic=0.25, roughness=0.28, colour=(0.02, 0.02, 0.02, 1)),
    'TEXTURED': dict(metallic=0.12, roughness=0.45, colour=(0.06, 0.06, 0.06, 1)),
    'CARBONFIBRE': dict(metallic=0.7, roughness=0.35, colour=(0.03, 0.03, 0.03, 1)),
    'DECAL': dict(metallic=0.1, roughness=0.35, alpha_from_tex=True, clip=True),
    'DEFROSTER': dict(metallic=0.0, roughness=0.04, transmission=0.95, alpha=0.4,
                      colour=(0.03, 0.035, 0.04, 1), ior=1.45, detail=True),
}


class _HashLib:
    """TextureLibrary-compatible wrapper around a hash->Texture map."""

    def __init__(self, by_hash):
        self.by_hash = by_hash
        self.images = {}

    def lookup(self, h, section=None):
        return self.by_hash.get(h)

    def image(self, tex):
        return TextureLibrary.image(self, tex)


def _set_transmission(bsdf, value):
    if 'Transmission Weight' in bsdf.inputs:
        bsdf.inputs['Transmission Weight'].default_value = value
    elif 'Transmission' in bsdf.inputs:
        bsdf.inputs['Transmission'].default_value = value


def _set_ior(bsdf, value):
    for key in ('IOR', 'Specular IOR Level'):
        if key in bsdf.inputs and key == 'IOR':
            bsdf.inputs[key].default_value = value
            return


def _paint_material(name, colour, settings, tex=None, lib=None, multiply_paint=False):
    m = bpy.data.materials.get(name)
    if m is not None:
        return m
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    out.location = (560, 0)
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.location = (220, 0)
    bsdf.inputs['Metallic'].default_value = settings.get('metallic', 0.0)
    bsdf.inputs['Roughness'].default_value = settings.get('roughness', 0.5)
    if settings.get('coat') and 'Coat Weight' in bsdf.inputs:
        bsdf.inputs['Coat Weight'].default_value = settings['coat']
        if 'Coat Roughness' in bsdf.inputs:
            bsdf.inputs['Coat Roughness'].default_value = 0.08
    col = settings.get('colour', colour)
    img_node = None
    if tex is not None and lib is not None:
        img_node = nt.nodes.new('ShaderNodeTexImage')
        img_node.location = (-360, 0)
        img_node.image = lib.image(tex)
        if multiply_paint:
            mix = nt.nodes.new('ShaderNodeMix')
            mix.data_type = 'RGBA'
            mix.blend_type = 'MULTIPLY'
            mix.location = (0, 0)
            mix.inputs['Factor'].default_value = 1.0
            mix.inputs[7].default_value = col
            nt.links.new(img_node.outputs['Color'], mix.inputs[6])
            nt.links.new(mix.outputs[2], bsdf.inputs['Base Color'])
        else:
            nt.links.new(img_node.outputs['Color'], bsdf.inputs['Base Color'])
    else:
        bsdf.inputs['Base Color'].default_value = col
    if settings.get('transmission'):
        _set_transmission(bsdf, settings['transmission'])
        if 'ior' in settings:
            _set_ior(bsdf, settings['ior'])
        bsdf.inputs['Alpha'].default_value = settings.get('alpha', 0.4)
        m.blend_method = 'BLEND'
        m.use_screen_refraction = True
        try:
            m.use_backface_culling = False
            m.surface_render_method = 'BLENDED'
        except Exception:
            pass
        # optional defroster / detail: mix texture onto the glass colour
        if settings.get('detail') and img_node is not None:
            mix = nt.nodes.new('ShaderNodeMix')
            mix.data_type = 'RGBA'
            mix.blend_type = 'MIX'
            mix.location = (0, 80)
            nt.links.new(img_node.outputs['Color'], mix.inputs[7])
            mix.inputs[6].default_value = col
            if 'Alpha' in img_node.outputs:
                nt.links.new(img_node.outputs['Alpha'], mix.inputs['Factor'])
            nt.links.new(mix.outputs[2], bsdf.inputs['Base Color'])
    if settings.get('alpha_from_tex') and img_node is not None and 'Alpha' in img_node.outputs:
        nt.links.new(img_node.outputs['Alpha'], bsdf.inputs['Alpha'])
        try:
            m.blend_method = 'CLIP' if settings.get('clip') else 'BLEND'
            m.alpha_threshold = 0.4
        except Exception:
            m.blend_method = 'BLEND'
    if settings.get('emit'):
        emit_col = settings.get('colour', col) or (1, 1, 1, 1)
        if 'Emission Color' in bsdf.inputs:
            bsdf.inputs['Emission Color'].default_value = emit_col
        if 'Emission Strength' in bsdf.inputs:
            bsdf.inputs['Emission Strength'].default_value = settings['emit']
    nt.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    m['nfs_kind'] = 'car_material'
    m['nfs_car_class'] = settings.get('_cls', '')
    return m


def _part_material(part, texhash, cls_hash, lib, paint, cache):
    cls = cars.CLASS_NAMES.get(cls_hash) if cls_hash else None
    settings = dict(CLASS_SHADERS.get(cls, dict(metallic=0.1, roughness=0.5,
                                                colour=(0.08, 0.08, 0.09, 1))))
    settings['_cls'] = cls or 'part'
    tex = lib.lookup(texhash) if (lib is not None and texhash) else None
    key = (texhash if tex is not None else 0, cls, tuple(paint[:3]))
    if key in cache:
        return cache[key]
    name = 'car_%s_%08X_%02x%02x%02x' % (cls or 'part', texhash if tex is not None else 0,
                                         int(paint[0] * 255), int(paint[1] * 255), int(paint[2] * 255))
    # Glass / lights must not fall back to an opaque world texture (that is
    # what made windows look like scratchy defroster / damage maps).
    use_tex = tex is not None and cls not in cars.GLASS_CLASSES
    if cls == 'DEFROSTER':
        use_tex = tex is not None  # glass with optional detail
    if cls == 'CARSKIN':
        m = _paint_material(name, paint, settings, tex if tex is not None else None,
                            lib, multiply_paint=bool(tex))
    else:
        m = _paint_material(name, settings.get('colour', paint), settings,
                            tex if use_tex else None, lib, multiply_paint=False)
    cache[key] = m
    return m


def _build_car_mesh(part, lib, paint, cache):
    """Mesh with one material per shader class (not grouped only by texture).

    ``importer.build_mesh`` merges faces that share a texture hash, which
    turns wheel rims into rubber when they reuse the tyre atlas.
    """
    o = _as_obj_dict(part)
    me = bpy.data.meshes.new(part.name)
    me.from_pydata(o['verts'], [], o['faces'])
    me.uv_layers.new(name='UVMap')
    me.color_attributes.new('prelight', 'BYTE_COLOR', 'CORNER')
    loop_v = [0] * len(me.loops)
    me.loops.foreach_get('vertex_index', loop_v)
    uv, cl = [], []
    for v in loop_v:
        u, vv = o['uvs'][v] if v < len(o['uvs']) else (0.0, 0.0)
        uv += (u, 1.0 - vv)
        r, g, b, a = o['cols'][v] if v < len(o['cols']) else (255, 255, 255, 255)
        cl += (r / 255, g / 255, b / 255, a / 255)
    me.uv_layers['UVMap'].data.foreach_set('uv', uv)
    me.color_attributes['prelight'].data.foreach_set('color_srgb', cl)

    mats, face_mat = [], [0] * len(o['faces'])
    for i, rec in enumerate(part.mesh.materials):
        first, count, slot = rec[0], rec[1], rec[2]
        th = part.textures[slot] if slot < len(part.textures) else 0
        cls_hash = cars.part_shader(part, i, slot)
        mat = _part_material(part, th, cls_hash, lib, paint, cache)
        if mat not in mats:
            mats.append(mat)
        mid = mats.index(mat)
        start, n = first // 3, count // 3
        for f in range(start, min(start + n, len(face_mat))):
            face_mat[f] = mid
    for mat in mats:
        me.materials.append(mat)
    for poly, fi in zip(me.polygons, face_mat):
        poly.material_index = fi
    me.validate()
    me.update()
    # game meshes are low-poly; smooth shading matches the in-game look
    for poly in me.polygons:
        poly.use_smooth = True
    try:
        me.use_auto_smooth = True
        me.auto_smooth_angle = 0.698  # 40 degrees
    except Exception:
        pass
    return me


def _part_basis(part):
    """Local transform for a part. Skip leftover L/R mirror flags."""
    if cars.uses_baked_model_space(part.matrix, part.mesh.verts):
        return Matrix.Identity(4)
    return game_matrix(part.matrix) if part.matrix else Matrix.Identity(4)


def import_car(path, kit='KIT00', lod='A', paint=DEFAULT_PAINT, location=(0.0, 0.0, 0.0),
               rotation=(0.0, 0.0, 0.0), name=None, global_dir=None, textures=True,
               collection=None, context=None, log=print):
    """Import a stock kit of a car as a hierarchy under an empty.

    ``path`` is a ``CARS/<NAME>`` folder or a ``GEOMETRY.BIN``. ``paint`` is
    an RGBA tuple applied to CARSKIN materials. Returns the root empty.
    """
    context = context or bpy.context
    geo, tex, folder = cars.car_files(path)
    car = cars.CarGeometry.open(geo)
    parts = car.stock_parts(kit, lod)
    if not parts:
        raise ValueError('no stock parts for kit %s' % kit)
    lib = None
    if textures and tex is not None:
        extras = []
        gdir = global_dir or cars.default_global_dir(folder)
        if gdir and os.path.isdir(str(gdir)):
            extras = [str(p) for p in __import__('pathlib').Path(gdir).iterdir()
                      if p.is_file() and p.suffix.lower() in ('.bun', '.bin')]
        packs = cars.load_car_textures(tex, extras)
        lib = _HashLib(cars.texture_lookup(packs))
        log('[nfsu2] car textures: %d unique' % len(lib.by_hash))
    root_name = name or ('CAR_' + car.name)
    root = bpy.data.objects.new(root_name, None)
    root.empty_display_type = 'PLAIN_AXES'
    root.empty_display_size = 1.5
    root['nfs_kind'] = 'car'
    root['nfs_car'] = car.name
    root['nfs_kit'] = kit
    root['nfs_source'] = str(geo)
    scene = context.scene
    if collection is None:
        collection = bpy.data.collections.new(root_name)
        scene.collection.children.link(collection)
    collection.objects.link(root)

    cache = {}
    wheel = car.wheel_part(kit, lod)
    front_brake = next((p for p in parts if 'FRONT_BRAKE' in p.family.upper()), None)
    rear_brake = next((p for p in parts if 'REAR_BRAKE' in p.family.upper()), None)
    skip = {p for p in (wheel, front_brake, rear_brake) if p is not None}
    # exhaust meshes are stored at the origin like wheels; hide them on a parked car
    skip.update(p for p in parts if 'EXHAUST' in p.family.upper())

    def _link_part(p, ob_name, loc=None, rot=None, kind='car_part'):
        me = _build_car_mesh(p, lib, paint, cache)
        ob = bpy.data.objects.new(ob_name, me)
        collection.objects.link(ob)
        ob.parent = root
        ob.matrix_parent_inverse = Matrix.Identity(4)
        if loc is not None:
            ob.location = loc
            ob.rotation_euler = rot or (0.0, 0.0, 0.0)
        else:
            ob.matrix_basis = _part_basis(p)
        ob['nfs_kind'] = kind
        ob['nfs_car'] = car.name
        ob['nfs_hash'] = '%08X' % p.hash
        ob['nfs_part'] = p.family
        return ob

    for p in parts:
        if p in skip:
            continue
        _link_part(p, p.name)

    if wheel is not None:
        layout = cars.wheel_layout(global_dir or cars.default_global_dir(folder), car.name)
        if not layout:
            body = next((p for p in parts if 'BODY' in p.family.upper()), parts[0])
            layout = cars.estimate_wheel_layout(body, wheel)
            log('[nfsu2] wheel axles estimated from geometry')
        xforms = cars.wheel_transforms(wheel, layout)
        for i, (loc, rot) in enumerate(xforms):
            ob = _link_part(wheel, '%s.W%d' % (wheel.name, i), loc, rot, kind='car_wheel')
            ob['nfs_wheel'] = i
        def _place_brakes(part, indices):
            if part is None:
                return
            for i in indices:
                loc, rot = xforms[i]
                _link_part(part, '%s.W%d' % (part.name, i), loc, rot)
        _place_brakes(front_brake, (0, 1))
        _place_brakes(rear_brake, (2, 3))

    root.location = location
    root.rotation_euler = rotation
    log('[nfsu2] imported car %s (%d parts, kit %s) at (%.2f, %.2f, %.2f)'
        % (car.name, len(parts), kit, location[0], location[1], location[2]))
    return root


def _as_obj_dict(part):
    """Shape expected by importer.build_mesh."""
    m = part.mesh
    cols = []
    for c in m.colors:
        a = (c >> 24) & 255
        r = (c >> 16) & 255
        g = (c >> 8) & 255
        b = c & 255
        cols.append((r, g, b, a))
    faces = m.triangles()
    # materials: list of (first, count, slot); face_mat index per triangle
    face_mat = []
    mats = []
    for first, count, slot, rec in m.materials:
        mats.append((first, count, slot))
        for _ in range(count // 3):
            face_mat.append(len(mats) - 1)
    if len(face_mat) < len(faces):
        face_mat.extend([0] * (len(faces) - len(face_mat)))
    return dict(verts=list(m.verts), faces=faces, uvs=list(m.uvs), cols=cols,
                tex=list(m.textures), mats=mats, face_mat=face_mat, name=m.name,
                hash=m.hash, matrix=m.matrix, stride=m.stride)


def export_car(out_path, context=None, source=None, log=print):
    """Write a GEOMETRY.BIN: re-encode edited parts, keep the rest identical.

    ``out_path`` is the output file (the source is never overwritten). ``source``
    is the original GEOMETRY.BIN (default: ``nfs_source`` on a ``car`` object).
    """
    from .exporter import mesh_from_blender, mesh_signature
    context = context or bpy.context
    scene = context.scene
    roots = [o for o in scene.objects if o.get('nfs_kind') == 'car']
    if not roots:
        raise RuntimeError('no imported NFSU2 car in the scene')
    root = roots[0]
    src = source or root.get('nfs_source')
    if not src:
        raise RuntimeError('car has no nfs_source; pass the original GEOMETRY.BIN')
    src = os.path.abspath(src)
    out_path = os.path.abspath(out_path)
    if os.path.isdir(out_path):
        out_path = os.path.join(out_path, 'GEOMETRY.BIN')
    if os.path.isfile(out_path) and os.path.samefile(out_path, src):
        raise RuntimeError('refusing to overwrite the source GEOMETRY.BIN; pick another folder')
    car = cars.CarGeometry.open(src)
    by_hash = {'%08X' % p.hash: p for p in car.parts}
    to_local = root.matrix_world.inverted()
    deps = context.evaluated_depsgraph_get()
    edited = 0
    seen = set()
    for ob in scene.objects:
        if ob.get('nfs_kind') not in ('car_part', 'car_wheel'):
            continue
        hx = ob.get('nfs_hash') or (by_hash and None)
        # wheels share the source part; nfs_hash may be missing on copies
        if not hx:
            # match by part family / name prefix
            continue
        if hx in seen:
            continue
        part = by_hash.get(hx)
        if part is None:
            continue
        seen.add(hx)
        # skip re-encode when the mesh hasn't changed (same vertices in local space)
        m = mesh_from_blender(ob, part.mesh, to_local, deps)
        meshcodec.encode(part.node, m)
        edited += 1
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    data = car.rebuild()
    open(out_path, 'wb').write(data)
    # copy TEXTURES.BIN alongside if present, unmodified
    tex = cars.car_files(src)[1]
    if tex:
        dest = os.path.join(os.path.dirname(out_path), 'TEXTURES.BIN')
        if not os.path.exists(dest):
            import shutil
            shutil.copy2(str(tex), dest)
    log('[nfsu2] exported car %s (%d re-encoded part(s)) -> %s' % (car.name, edited, out_path))
    return dict(path=out_path, edited=edited, name=car.name)
