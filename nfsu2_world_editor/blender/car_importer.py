"""Import an NFSU2 car (GEOMETRY.BIN + TEXTURES.BIN) into the current scene."""
import math
import os

import bpy
from mathutils import Euler, Matrix, Vector

from ..core import cars, meshcodec
from ..core.files import read_bytes
from .importer import build_mesh, game_matrix
from .textures import TextureLibrary, build_material


# Paint colours used when the part's material class is CARSKIN (no stored albedo).
DEFAULT_PAINT = (0.75, 0.06, 0.07, 1.0)  # classic red

CLASS_SHADERS = {
    'CARSKIN': dict(metallic=0.35, roughness=0.28),
    'WINDSHIELD': dict(metallic=0.0, roughness=0.05, transmission=1.0, alpha=0.25),
    'HEADLIGHTGLASS': dict(metallic=0.0, roughness=0.08, transmission=0.7, alpha=0.4, emit=2.0),
    'RUBBER': dict(metallic=0.0, roughness=0.7, colour=(0.03, 0.03, 0.03, 1)),
    'MAGSILVER': dict(metallic=1.0, roughness=0.25, colour=(0.7, 0.7, 0.72, 1)),
    'CHROME': dict(metallic=1.0, roughness=0.08, colour=(0.85, 0.85, 0.87, 1)),
    'CARBOTTOM': dict(metallic=0.1, roughness=0.8, colour=(0.05, 0.05, 0.05, 1)),
    'BLACK': dict(metallic=0.0, roughness=0.6, colour=(0.02, 0.02, 0.02, 1)),
}


class _HashLib:
    """TextureLibrary-compatible wrapper around a hash->Texture map."""

    def __init__(self, by_hash):
        self.by_hash = by_hash
        self.images = {}

    def lookup(self, h, section=None):
        return self.by_hash.get(h)

    def image(self, tex):
        # reuse TextureLibrary.image by borrowing a dummy
        return TextureLibrary.image(self, tex)


def _class_of(part, slot):
    if slot < len(part.classes):
        return cars.CLASS_NAMES.get(part.classes[slot])
    if part.classes:
        return cars.CLASS_NAMES.get(part.classes[0])
    return None


def _paint_material(name, colour, settings, tex=None, lib=None):
    m = bpy.data.materials.get(name)
    if m is not None:
        return m
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    out.location = (500, 0)
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.location = (200, 0)
    bsdf.inputs['Metallic'].default_value = settings.get('metallic', 0.0)
    bsdf.inputs['Roughness'].default_value = settings.get('roughness', 0.5)
    col = settings.get('colour', colour)
    if tex is not None and lib is not None:
        img = nt.nodes.new('ShaderNodeTexImage')
        img.location = (-300, 0)
        img.image = lib.image(tex)
        # multiply texture by paint colour (CARSKIN)
        mix = nt.nodes.new('ShaderNodeMix')
        mix.data_type = 'RGBA'
        mix.blend_type = 'MULTIPLY'
        mix.location = (0, 0)
        mix.inputs['Factor'].default_value = 1.0
        mix.inputs[7].default_value = col
        nt.links.new(img.outputs['Color'], mix.inputs[6])
        nt.links.new(mix.outputs[2], bsdf.inputs['Base Color'])
    else:
        bsdf.inputs['Base Color'].default_value = col
    if settings.get('transmission'):
        # Blender 4.x: Transmission input
        if 'Transmission Weight' in bsdf.inputs:
            bsdf.inputs['Transmission Weight'].default_value = settings['transmission']
        elif 'Transmission' in bsdf.inputs:
            bsdf.inputs['Transmission'].default_value = settings['transmission']
        alpha = settings.get('alpha', 0.3)
        bsdf.inputs['Alpha'].default_value = alpha
        m.blend_method = 'BLEND'
        m.use_screen_refraction = True
        try:
            m.surface_render_method = 'BLENDED'
        except Exception:
            pass
    if settings.get('emit'):
        if 'Emission Color' in bsdf.inputs:
            bsdf.inputs['Emission Color'].default_value = col if col else (1, 1, 1, 1)
        if 'Emission Strength' in bsdf.inputs:
            bsdf.inputs['Emission Strength'].default_value = settings['emit']
    nt.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    m['nfs_kind'] = 'car_material'
    return m


def _part_material(part, texhash, slot, lib, paint, cache):
    cls = _class_of(part, slot)
    settings = CLASS_SHADERS.get(cls, dict(metallic=0.1, roughness=0.5))
    tex = lib.lookup(texhash) if lib is not None else None
    key = (texhash, cls, tuple(paint))
    if key in cache:
        return cache[key]
    if tex is not None and cls not in ('CARSKIN',):
        m = build_material(texhash, lib)
        cache[key] = m
        return m
    # CARSKIN (optionally multiplied by a bound texture) or a class-only shader
    name = 'car_%s_%08X_%02x%02x%02x' % (cls or 'part', texhash,
                                         int(paint[0] * 255), int(paint[1] * 255), int(paint[2] * 255))
    m = _paint_material(name, paint, settings, tex if cls == 'CARSKIN' else None, lib)
    cache[key] = m
    return m


def _assign_class_materials(ob, part, lib, paint, cache):
    """Rebuild the object's materials using car texture / class binding."""
    me = ob.data
    # rebuild from part.mesh material slots so we know the texture hash + class
    slots_map = {}
    new_mats = []
    for i, (first, count, slot, rec) in enumerate(part.mesh.materials):
        th = part.textures[slot] if slot < len(part.textures) else 0
        if th not in slots_map:
            slots_map[th] = len(new_mats)
            new_mats.append(_part_material(part, th, slot, lib, paint, cache))
    me.materials.clear()
    for m in new_mats:
        me.materials.append(m)
    # face material indices already match tex-slot grouping from build_mesh;
    # remap if the hash order differed. build_mesh used part['tex'] order.
    # Safer to leave indices: build_mesh grouped by tex hash in encounter order
    # which is the same as we just did.


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
    for p in parts:
        if p in skip:
            continue
        me = build_mesh(_as_obj_dict(p), p.name, {}, lib, None)
        ob = bpy.data.objects.new(p.name, me)
        ob.matrix_world = game_matrix(p.matrix) if p.matrix else Matrix.Identity(4)
        ob['nfs_kind'] = 'car_part'
        ob['nfs_car'] = car.name
        ob['nfs_hash'] = '%08X' % p.hash
        ob['nfs_part'] = p.family
        collection.objects.link(ob)
        ob.parent = root
        _assign_class_materials(ob, p, lib, paint, cache)

    if wheel is not None:
        layout = cars.wheel_layout(global_dir or cars.default_global_dir(folder), car.name)
        if not layout:
            body = next((p for p in parts if 'BODY' in p.family.upper()), parts[0])
            layout = cars.estimate_wheel_layout(body, wheel)
            log('[nfsu2] wheel axles estimated from geometry')
        xforms = cars.wheel_transforms(wheel, layout)
        me = build_mesh(_as_obj_dict(wheel), wheel.name, {}, lib, None)
        for i, (loc, rot) in enumerate(xforms):
            ob = bpy.data.objects.new('%s.W%d' % (wheel.name, i), me)
            ob.location = loc
            ob.rotation_euler = rot
            ob['nfs_kind'] = 'car_wheel'
            ob['nfs_car'] = car.name
            ob['nfs_hash'] = '%08X' % wheel.hash
            ob['nfs_wheel'] = i
            collection.objects.link(ob)
            ob.parent = root
            _assign_class_materials(ob, wheel, lib, paint, cache)
        def _place_brakes(part, indices):
            if part is None:
                return
            me = build_mesh(_as_obj_dict(part), part.name, {}, lib, None)
            for i in indices:
                loc, rot = xforms[i]
                ob = bpy.data.objects.new('%s.W%d' % (part.name, i), me)
                ob.location = loc
                ob.rotation_euler = rot
                ob['nfs_kind'] = 'car_part'
                ob['nfs_car'] = car.name
                ob['nfs_hash'] = '%08X' % part.hash
                ob['nfs_part'] = part.family
                collection.objects.link(ob)
                ob.parent = root
                _assign_class_materials(ob, part, lib, paint, cache)
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
