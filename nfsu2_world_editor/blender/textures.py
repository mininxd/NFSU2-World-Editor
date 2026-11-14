"""Turn NFSU2 texture-pack entries into Blender images and materials."""
import struct

import bpy

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None


class TextureLibrary:
    """Texture lookup for a :class:`core.stream.Region`. A section's own packs
    win over the region-wide library packs."""

    def __init__(self, region, extra_files=(), log=None):
        self.region = region
        self.by_hash = {}
        self.by_section = {}
        for pack, tag in region.texture_packs():
            for t in pack.textures:
                self.by_hash.setdefault(t.hash, t)
                self.by_section.setdefault(tag, {}).setdefault(t.hash, t)
        self.images = {}
        self.extra_count = 0
        for path in extra_files:
            self.add_file(path, log)

    def add_file(self, path, log=None):
        """Add the texture packs of another BUN/BIN (e.g. the game's GLOBAL
        packs) as a fallback: textures already known keep their region copy.
        Files without (or with unreadable) packs are skipped."""
        from ..core import bchunk, tpk
        from ..core.files import read_bytes
        try:
            packs = list(tpk.iter_packs(bchunk.parse(read_bytes(path))))
        except Exception as e:  # compressed or non-chunk file
            if log:
                log('[textures] skipped %s (%s)' % (path, type(e).__name__))
            return 0
        n = 0
        for p in packs:
            for t in p.textures:
                if t.hash not in self.by_hash:
                    self.by_hash[t.hash] = t
                    n += 1
        self.extra_count += n
        if log and n:
            log('[textures] %s: %d extra textures' % (path, n))
        return n

    def lookup(self, h, section=None):
        if section is not None:
            t = self.by_section.get(section, {}).get(h)
            if t is not None:
                return t
        return self.by_hash.get(h)

    def image(self, tex):
        img = self.images.get(tex.hash)
        if img is not None:
            return img
        name = 'TEX_%08X_%s' % (tex.hash, tex.name)
        img = bpy.data.images.get(name)
        if img is None:
            rgba, w, h = tex.decode()
            img = bpy.data.images.new(name, w, h, alpha=True)
            if np is not None:
                a = np.frombuffer(rgba, np.uint8).reshape(h, w, 4)[::-1].astype(np.float32) / 255.0
                img.pixels.foreach_set(a.ravel())
            else:
                rows = [rgba[y * w * 4:(y + 1) * w * 4] for y in range(h)]
                img.pixels.foreach_set([c / 255.0 for r in reversed(rows) for c in r])
            img.pack()
            img['nfs_texture_hash'] = '%08X' % tex.hash
            img['nfs_texture_name'] = tex.name
            img['nfs_format'] = tex.format_name
        # the game uses alpha as opacity only for cut-out/blended textures; for
        # the rest (roads: wet-gloss mask) it must be ignored
        img.alpha_mode = 'STRAIGHT' if tex.alpha_is_opacity() else 'NONE'
        self.images[tex.hash] = img
        return img


def _pastel(texhash):
    h = texhash * 2654435761 & 0xFFFFFFFF
    return (0.35 + (h & 255) / 512, 0.35 + (h >> 8 & 255) / 512, 0.35 + (h >> 16 & 255) / 512, 1.0)


def build_material(texhash, lib=None, section=None):
    """Material ``tex_<HASH>`` (one per texture hash), shared by every mesh."""
    name = 'tex_%08X' % texhash
    m = bpy.data.materials.get(name)
    if m is not None and (m.get('nfs_has_texture') or lib is None):
        return m
    tex = lib.lookup(texhash, section) if lib is not None else None
    if m is None:
        m = bpy.data.materials.new(name)
    m['nfs_texture_hash'] = '%08X' % texhash
    m.diffuse_color = _pastel(texhash)
    if tex is None:
        return m
    m['nfs_texture_name'] = tex.name
    m['nfs_has_texture'] = True
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    out.location = (700, 0)
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.location = (400, 0)
    bsdf.inputs['Roughness'].default_value = 0.8
    img = nt.nodes.new('ShaderNodeTexImage')
    img.location = (-400, 100)
    img.image = lib.image(tex)
    img.interpolation = 'Linear'
    nt.nodes.active = img          # Workbench "Texture" colour mode shows this one
    vc = nt.nodes.new('ShaderNodeVertexColor')
    vc.location = (-400, -200)
    vc.layer_name = 'prelight'
    mix = nt.nodes.new('ShaderNodeMix')
    mix.data_type = 'RGBA'
    mix.blend_type = 'MULTIPLY'
    mix.location = (-100, 0)
    mix.inputs['Factor'].default_value = 1.0
    nt.links.new(img.outputs['Color'], mix.inputs[6])
    nt.links.new(vc.outputs['Color'], mix.inputs[7])
    # the game modulates texture x baked light x 2
    sc = nt.nodes.new('ShaderNodeVectorMath')
    sc.operation = 'SCALE'
    sc.location = (150, 0)
    sc.inputs['Scale'].default_value = 2.0
    nt.links.new(mix.outputs[2], sc.inputs[0])
    nt.links.new(sc.outputs['Vector'], bsdf.inputs['Base Color'])
    nt.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    if tex.alpha_is_opacity():
        nt.links.new(img.outputs['Alpha'], bsdf.inputs['Alpha'])
        if tex.usage == 1:
            m.blend_method = 'CLIP'
            m.alpha_threshold = 0.5
        else:
            m.blend_method = 'BLEND'
            m.surface_render_method = 'BLENDED'
        if tex.blend == 2:  # additive glow
            nt.links.new(sc.outputs['Vector'], bsdf.inputs['Emission Color'])
            bsdf.inputs['Emission Strength'].default_value = 1.0
    m['nfs_usage'] = tex.usage
    m['nfs_blend'] = tex.blend
    return m
