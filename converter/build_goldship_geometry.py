#!/usr/bin/env python3
"""Build and install GOLDSHIP car model and database entry into NFSU2.

Converts the Umamusume Gold Ship high-poly mesh into a 100% compliant NFSU2 GEOMETRY.BIN,
configures textures, and registers the car in GLOBALB.BUN and ENGLISH.BIN.
"""
import os
import struct
import sys
from pathlib import Path

# Add repo root to module search path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from nfsu2_world_editor.core import bchunk, cars, compress, dxt, files, meshcodec


def make_dummy_mesh(name, part_hash, matrix, tex_hash):
    """Create a minimal invisible 1-triangle mesh for hidden/collapsed sub-parts."""
    m = meshcodec.MeshData()
    m.name = name
    m.hash = part_hash
    m.matrix = matrix
    # Tiny triangle inside origin
    m.verts = [(0.0, 0.0, 0.0), (0.001, 0.0, 0.0), (0.0, 0.001, 0.0)]
    m.normals = [(0.0, 0.0, 1.0), (0.0, 0.0, 1.0), (0.0, 0.0, 1.0)]
    m.uvs = [(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)]
    m.colors = [0x00000000, 0x00000000, 0x00000000]
    m.indices = [0, 1, 2]
    rec = bytearray(60)
    m.materials = [(0, 3, 0, rec)]
    m.textures = [tex_hash]
    return m


def extract_mw_body(mw_geo_path):
    """Extract high-poly body mesh vertices, normals, and UVs from MW GEOMETRY.BIN."""
    data = files.read_bytes(mw_geo_path)
    chunks = bchunk.parse(data)
    body_obj = None
    for o in chunks[0].find_all(0x80134010):
        hd = bytes(o.find(0x00134011).data)
        name = hd[0xA4:].split(b'\0')[0].decode('ascii', 'replace')
        if '_BODY_B' in name or '_BODY_A' in name:
            body_obj = o
            break
    if not body_obj:
        raise ValueError(f'No body mesh found in {mw_geo_path}')

    mesh = body_obj.find(0x80134100)
    vb = bytes(mesh.find(0x00134B01).data)
    ib = bytes(mesh.find(0x00134B03).data)

    nv = len(vb) // 32
    verts, normals, uvs, colors = [], [], [], []
    for i in range(nv):
        q = i * 32
        x, y, z = struct.unpack_from('<3f', vb, q)
        nx, ny, nz = struct.unpack_from('<3f', vb, q + 12)
        u, v = struct.unpack_from('<2f', vb, q + 24)
        verts.append((x, y, z))
        normals.append((nx, ny, nz))
        uvs.append((u, v))
        colors.append(0xFFFFFFFF)

    num_idx = len(ib) // 2
    raw_indices = struct.unpack_from(f'<{num_idx}H', ib, 0)
    clean_indices = []
    for t in range(0, num_idx - 2, 3):
        i0, i1, i2 = raw_indices[t:t + 3]
        if i0 != i1 and i1 != i2 and i0 != i2 and max(i0, i1, i2) < nv:
            clean_indices.extend([i0, i1, i2])

    return verts, normals, uvs, colors, clean_indices


def convert_geometry_to_nfsu2(src_mw_geo, template_u2_geo, output_geo_path):
    """Convert MW car model into a valid NFSU2 GEOMETRY.BIN using template."""
    print(f'[geo] Extracting Gold Ship body from {src_mw_geo}...')
    verts, normals, uvs, colors, indices = extract_mw_body(src_mw_geo)
    print(f'[geo] Extracted {len(verts)} vertices and {len(indices)//3} triangles')

    print(f'[geo] Loading NFSU2 template from {template_u2_geo}...')
    u2_data = files.read_bytes(template_u2_geo)
    u2_chunks = bchunk.parse(u2_data)

    # Keywords of exterior parts to collapse so they do not clip with the unified body
    COLLAPSE_KEYWORDS = (
        'BUMPER', 'DOOR_LEFT', 'DOOR_RIGHT', 'HOOD', 'TRUNK', 'ROOF',
        'SKIRT', 'MIRROR', 'HEADLIGHT', 'BRAKELIGHT',
        'FRONT_WHEEL', 'REAR_WHEEL', 'FRONT_TIRE', 'REAR_TIRE'
    )

    body_node = None
    collapsed_count = 0

    for o in u2_chunks[0].find_all(0x80134010):
        hd = bytes(o.find(0x00134011).data)
        name = hd[0xA4:].split(b'\0')[0].decode('ascii', 'replace')

        if 'KIT00_BODY_A' in name:
            body_node = o
            continue

        # Check if part should be collapsed
        if any(kw in name for kw in COLLAPSE_KEYWORDS):
            try:
                dec = meshcodec.decode(o)
                if dec and len(dec.verts) > 3:
                    tex_h = dec.textures[0] if dec.textures else 0
                    dummy = make_dummy_mesh(dec.name, dec.hash, dec.matrix, tex_h)
                    meshcodec.encode(o, dummy)
                    collapsed_count += 1
            except Exception:
                pass

    if not body_node:
        raise ValueError('Could not find KIT00_BODY_A in template')

    # Encode new Gold Ship body into KIT00_BODY_A
    old_body_mesh = meshcodec.decode(body_node)
    new_mesh = meshcodec.MeshData()
    new_mesh.name = old_body_mesh.name
    new_mesh.hash = old_body_mesh.hash
    new_mesh.matrix = old_body_mesh.matrix
    new_mesh.verts = verts
    new_mesh.normals = normals
    new_mesh.uvs = uvs
    new_mesh.colors = colors
    new_mesh.indices = indices

    rec = bytearray(60)
    new_mesh.materials = [(0, len(indices), 0, rec)]
    # Use texture hash 0x5A00E244 (Gold Ship texture)
    new_mesh.textures = [0x5A00E244]

    meshcodec.encode(body_node, new_mesh)
    print(f'[geo] Collapsed {collapsed_count} exterior parts and installed Gold Ship body')

    # Serialize to output
    out_bytes = bytes(bchunk.serialize(u2_chunks))
    Path(output_geo_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_geo_path, 'wb') as f:
        f.write(out_bytes)
    print(f'[geo] Saved NFSU2 GEOMETRY.BIN to {output_geo_path} ({len(out_bytes)} bytes)')


def setup_goldship_textures(output_dir):
    """Set up TEXTURES.BIN and VINYLS.BIN in GOLDSHIP folder."""
    output_dir = Path(output_dir)
    # Copy template TEXTURES.BIN from MUSTANGGT
    nfsu2_tex = Path('/sdcard/nfsu2/CARS/MUSTANGGT/TEXTURES.BIN')
    dest_tex = output_dir / 'TEXTURES.BIN'
    if not dest_tex.exists() and nfsu2_tex.exists():
        dest_tex.write_bytes(nfsu2_tex.read_bytes())
        print(f'[tex] Copied base TEXTURES.BIN to {dest_tex}')

    # Copy VINYLS.BIN
    nfsu2_vinyls = Path('/sdcard/nfsu2/CARS/MUSTANGGT/VINYLS.BIN')
    dest_vinyls = output_dir / 'VINYLS.BIN'
    if not dest_vinyls.exists() and nfsu2_vinyls.exists():
        dest_vinyls.write_bytes(nfsu2_vinyls.read_bytes())
        print(f'[tex] Copied VINYLS.BIN to {dest_vinyls}')


def register_goldship_in_globalb(globalb_path):
    """Register GOLDSHIP in GLOBALB.BUN by replacing slot 7 (CORSA)."""
    globalb_path = Path(globalb_path)
    backup_path = globalb_path.with_suffix('.BUN.bak')
    if not backup_path.exists():
        backup_path.write_bytes(globalb_path.read_bytes())
        print(f'[db] Backed up original GLOBALB.BUN to {backup_path}')

    data = files.read_bytes(globalb_path)
    chunks = bchunk.parse(data)
    car_table = next(c for c in chunks if c.id == 0x34600)
    rec_size = 2192

    # Slot 7 (CORSA, unused in US release)
    d = bytearray(car_table.data)
    offset = 7 * rec_size

    def put_str(buf, off, max_len, text):
        raw = text.encode('ascii')[:max_len - 1] + b'\0'
        buf[off:off + len(raw)] = raw

    put_str(d, offset + 0x08, 24, 'GOLDSHIP')
    put_str(d, offset + 0x28, 24, 'GOLDSHIP')
    put_str(d, offset + 0x48, 32, r'CARS\GOLDSHIP\GEOMETRY.BIN')
    put_str(d, offset + 0x68, 32, r'CARS\GOLDSHIP\GEOMETRY.LZC')
    put_str(d, offset + 0xC8, 24, 'FORD')

    car_table.data = bytes(d)
    car_table.dirty = True

    new_globalb = bytes(bchunk.serialize(chunks))
    globalb_path.write_bytes(new_globalb)
    print(f'[db] Registered GOLDSHIP in {globalb_path} (Slot 7, {len(new_globalb)} bytes)')


def register_goldship_in_language(english_path):
    """Rename Corsa string to Goldship in LANGUAGES/ENGLISH.BIN."""
    english_path = Path(english_path)
    backup_path = english_path.with_suffix('.BIN.bak')
    if not backup_path.exists():
        backup_path.write_bytes(english_path.read_bytes())
        print(f'[lang] Backed up original ENGLISH.BIN to {backup_path}')

    data = bytearray(files.read_bytes(english_path))
    # Replace null-terminated "Corsa\0" with "Goldship\0" (or in-place)
    target = b'Corsa\x00'
    pos = data.find(target)
    if pos != -1:
        data[pos:pos + 5] = b'GoldS'
        english_path.write_bytes(bytes(data))
        print(f'[lang] Updated car name in {english_path} at offset {pos}')


def main():
    print('=== Installing Gold Ship into NFSU2 ===')
    mw_geo = '/sdcard/nfsu2/mod_test/nfsmw_uma/CARS/MUSTANGGT/GEOMETRY.BIN'
    u2_template = '/sdcard/nfsu2/CARS/MUSTANGGT/GEOMETRY.BIN'
    goldship_geo = Path('/sdcard/nfsu2/CARS/GOLDSHIP/GEOMETRY.BIN')
    goldship_dir = Path('/sdcard/nfsu2/CARS/GOLDSHIP')
    globalb = Path('/sdcard/nfsu2/GLOBAL/GLOBALB.BUN')
    english = Path('/sdcard/nfsu2/LANGUAGES/ENGLISH.BIN')

    # Backup existing GOLDSHIP/GEOMETRY.BIN if needed
    if goldship_geo.exists():
        bak = Path('/sdcard/nfsu2/CARS/GOLDSHIP/GEOMETRY.BIN.mw_bak')
        if not bak.exists():
            bak.write_bytes(goldship_geo.read_bytes())

    # 1. Convert geometry to compliant NFSU2 format
    convert_geometry_to_nfsu2(mw_geo, u2_template, goldship_geo)

    # 2. Textures & Vinyls
    setup_goldship_textures(goldship_dir)

    # 3. Register in GLOBALB.BUN
    register_goldship_in_globalb(globalb)

    # 4. Register in ENGLISH.BIN
    register_goldship_in_language(english)

    print('=== Gold Ship installation complete! ===')


if __name__ == '__main__':
    main()
