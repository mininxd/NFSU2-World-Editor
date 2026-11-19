#!/usr/bin/env python3
"""Export UV wireframe layout (UV Map / Template) from 3D models (GEOMETRY.BIN, .obj, etc.) to PNG.

Usage:
    python3 uv_export.py <3d_file_or_obj> [output_uv.png] [options]

Examples:
    # Export UV map for a car directly from GEOMETRY.BIN:
    python3 uv_export.py /sdcard/nfsu2/mod_test/world_editor/BMWM3 bmw_m3_uv.png

    # Export UV map from an existing .obj:
    python3 uv_export.py /sdcard/nfsu2/mod_test/world_editor/bmw_m3.obj

    # Export with 4096x4096 resolution and transparent background:
    python3 uv_export.py /sdcard/nfsu2/mod_test/world_editor/BMWM3 --size 4096 --transparent

    # Overlay wireframe on top of an existing texture:
    python3 uv_export.py /sdcard/nfsu2/mod_test/world_editor/BMWM3 --overlay /sdcard/nfsu2/mod_test/world_editor/bmwm3_texture.png
"""
import argparse
import os
import struct
import sys
from pathlib import Path

# Add repo root to module search path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PIL import Image, ImageDraw
from nfsu2_world_editor.core import bchunk, cars, files


def parse_uvs_from_obj(obj_path):
    """Extract UV coordinates and triangle UV indices from a .obj file."""
    uvs = []
    triangles = []
    with open(obj_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if line.startswith('vt '):
                parts = line.split()
                u, v = float(parts[1]), float(parts[2])
                # Convert back to D3D convention (0 top, 1 bottom)
                uvs.append((u, 1.0 - v))
            elif line.startswith('f '):
                parts = line.split()[1:]
                vt_indices = []
                for p in parts:
                    sub = p.split('/')
                    if len(sub) > 1 and sub[1]:
                        vt_indices.append(int(sub[1]) - 1)
                if len(vt_indices) >= 3:
                    for i in range(1, len(vt_indices) - 1):
                        triangles.append((vt_indices[0], vt_indices[i], vt_indices[i + 1]))
    return uvs, triangles


def parse_uvs_from_geometry(geo_path):
    """Extract UV coordinates and triangle indices from GEOMETRY.BIN."""
    geo_path = Path(geo_path)
    if geo_path.is_dir():
        geo_file = geo_path / 'GEOMETRY.BIN'
    else:
        geo_file = geo_path

    data = files.read_bytes(geo_file)
    chunks = bchunk.parse(data)
    objs = chunks[0].find_all(0x80134010)

    # Pick candidate objects with mesh
    all_uvs = []
    all_triangles = []
    v_base = 0

    for o in objs:
        mesh = o.find(0x80134100)
        if not mesh:
            continue
        b01 = mesh.find(0x00134B01)
        b03 = mesh.find(0x00134B03)
        if not b01 or not b03 or len(b01.data) <= 64 or len(b03.data) < 6:
            continue

        vb = bytes(b01.data)
        ib = bytes(b03.data)

        stride = None
        for s in (32, 24, 36, 40):
            if len(vb) % s == 0:
                stride = s
                break
        if not stride:
            continue

        num_verts = len(vb) // stride
        sub_uvs = []
        for i in range(num_verts):
            q = i * stride
            if stride == 32:
                u, v = struct.unpack_from('<2f', vb, q + 24)
            elif stride == 24:
                u, v = struct.unpack_from('<2f', vb, q + 16)
            elif stride == 36:
                u, v = struct.unpack_from('<2f', vb, q + 28)
            else:
                u, v = struct.unpack_from('<2f', vb, q + 16)
            sub_uvs.append((u, v))

        num_idx = len(ib) // 2
        indices = struct.unpack_from(f'<{num_idx}H', ib, 0)
        for t in range(0, num_idx - 2, 3):
            i0, i1, i2 = indices[t:t + 3]
            if i0 != i1 and i1 != i2 and i0 != i2 and max(i0, i1, i2) < num_verts:
                all_triangles.append((i0 + v_base, i1 + v_base, i2 + v_base))

        all_uvs.extend(sub_uvs)
        v_base += num_verts

    return all_uvs, all_triangles


def render_uv_map(uvs, triangles, output_png, size=2048, transparent=False, line_color=(0, 210, 255, 200),
                  bg_color=(20, 20, 24, 255), overlay_path=None):
    """Draw wireframe UV map."""
    output_png = Path(output_png)
    output_png.parent.mkdir(parents=True, exist_ok=True)

    if overlay_path and Path(overlay_path).exists():
        base_img = Image.open(overlay_path).convert('RGBA')
        resample_filter = getattr(getattr(Image, 'Resampling', Image), 'LANCZOS', Image.ANTIALIAS)
        img = base_img.resize((size, size), resample_filter)
    elif transparent:
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    else:
        img = Image.new('RGBA', (size, size), bg_color)

    draw = ImageDraw.Draw(img)

    def to_px(u, v):
        px = int(round(u * (size - 1))) % size
        py = int(round(v * (size - 1))) % size
        return (px, py)

    lines_drawn = 0
    total_uvs = len(uvs)

    for i0, i1, i2 in triangles:
        if max(i0, i1, i2) >= total_uvs:
            continue
        u0, v0 = uvs[i0]
        u1, v1 = uvs[i1]
        u2, v2 = uvs[i2]

        # Ignore invalid/dummy UVs
        if any(abs(c) > 10.0 for c in (u0, v0, u1, v1, u2, v2)):
            continue

        p0 = to_px(u0, v0)
        p1 = to_px(u1, v1)
        p2 = to_px(u2, v2)

        # Draw wireframe triangle
        draw.line([p0, p1, p2, p0], fill=line_color, width=1)
        lines_drawn += 3

    img.save(output_png, 'PNG')
    print(f'[uv] Exported UV map: {output_png} ({size}x{size}, {lines_drawn} edges drawn)')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('input', help='Path to 3D game file (GEOMETRY.BIN, car directory, or .obj file)')
    parser.add_argument('output', nargs='?', default=None, help='Output .png filename (optional)')
    parser.add_argument('--size', type=int, default=2048, help='Output image resolution: 1024, 2048, 4096 (default: 2048)')
    parser.add_argument('--transparent', action='store_true', help='Use transparent background instead of dark')
    parser.add_argument('--overlay', default=None, help='Path to texture image to overlay wireframe onto')

    args = parser.parse_args()
    in_path = Path(args.input)
    if not in_path.exists():
        sys.exit(f'Error: input path not found: {in_path}')

    if args.output:
        out_png = Path(args.output)
    else:
        stem = in_path.name if in_path.is_dir() else in_path.stem
        out_png = Path(f'{stem}_uv_map.png')

    if in_path.suffix.lower() == '.obj':
        print(f'[uv] Reading UV coordinates from OBJ: {in_path}...')
        uvs, triangles = parse_uvs_from_obj(in_path)
    else:
        print(f'[uv] Reading UV coordinates from binary: {in_path}...')
        uvs, triangles = parse_uvs_from_geometry(in_path)

    if not uvs or not triangles:
        sys.exit(f'Error: no UV coordinates found in {in_path}')

    print(f'[uv] Found {len(uvs)} UV vertices and {len(triangles)} UV triangles')
    render_uv_map(uvs, triangles, out_png, size=args.size, transparent=args.transparent, overlay_path=args.overlay)


if __name__ == '__main__':
    main()
