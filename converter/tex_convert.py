#!/usr/bin/env python3
"""Convert Need for Speed texture packs (NFSU2 / NFSMW TEXTURES.BIN, TRACKS/*.BUN) to PNG.

Usage:
    python3 tex_convert.py <texture_file> [output_directory]

Examples:
    # Convert car textures to PNG:
    python3 tex_convert.py /sdcard/nfsu2/CARS/MIATA/TEXTURES.BIN /sdcard/nfsu2/mod_test/world_editor/miata_textures

    # Convert BMW / NFSMW template texture:
    python3 tex_convert.py /sdcard/nfsu2/mod_test/world_editor/BMWM3/TEXTURES.BIN /sdcard/nfsu2/mod_test/world_editor/bmw_textures

    # Convert without output directory (saves in <name>_textures/):
    python3 tex_convert.py /sdcard/nfsu2/CARS/SKYLINE/TEXTURES.BIN
"""
import argparse
import struct
import sys
from pathlib import Path

# Add repo root to module search path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from nfsu2_world_editor.core import bchunk, cars, compress, dxt, files, image, tpk


def decode_jdlz_texture_pack(data):
    """Decode NFS Most Wanted / JDLZ-compressed car template textures from binary data."""
    chunks = bchunk.parse(data)
    results = []

    for top in chunks:
        if top.id != 0xB3300000 or not top.children:
            continue
        info = top.find(0xB3310000)
        data_block = top.find(0xB3320000)
        if not info or not data_block:
            continue

        c1 = info.find(0x33310001)
        c3 = info.find(0x33310003)
        d2 = data_block.find(0x33320002)
        if not c3 or not d2 or not d2.data:
            continue

        # Extract texture name from c1 if available
        tex_name = "CarTemplate"
        if c1 and c1.data:
            raw_c1 = bytes(c1.data)
            # Find string after CARTEXTURES
            parts = [s.decode('ascii', 'replace') for s in raw_c1.split(b'\0') if s]
            if len(parts) >= 2:
                # E.g. Global\Pipeline\CarTemplateTextures_BMWM3.tpk
                tex_name = Path(parts[1]).stem

        thash, w, comp_size, uncomp_size, h, _ = struct.unpack_from('<6I', c3.data, 0)
        raw_pixels = bytes(d2.data)

        # Decompress if JDLZ
        if raw_pixels.startswith(b'JDLZ'):
            decomp = compress.decompress(raw_pixels)
        else:
            decomp = raw_pixels

        # Decode DXT1
        try:
            rgba = dxt.decode(decomp[:w * h // 2], dxt.FOURCC_DXT1, w, h)
            results.append((tex_name, w, h, rgba))
        except Exception:
            # Fallback to DXT5
            try:
                rgba = dxt.decode(decomp[:w * h], dxt.FOURCC_DXT5, w, h)
                results.append((tex_name, w, h, rgba))
            except Exception:
                pass

    return results


def convert_textures(input_file, out_dir):
    input_path = Path(input_file)
    if input_path.is_dir():
        input_file = input_path / 'TEXTURES.BIN'
        default_dir = Path(f"{input_path.name}_textures")
    else:
        default_dir = Path(f"{input_path.parent.name if input_path.stem == 'TEXTURES' else input_path.stem}_textures")

    out_dir = Path(out_dir) if out_dir else default_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f'[tex] Loading {input_file}...')
    data = files.read_bytes(input_file)

    count = 0

    # 1. Try standard NFSU2 car textures
    try:
        packs = cars.load_car_textures(input_file)
        for pack in packs:
            for tex in pack.textures:
                try:
                    rgba, w, h = tex.decode()
                    name = tex.name or f'{tex.hash:08X}'
                    clean_name = "".join(c for c in name if c.isalnum() or c in ('_', '-')).strip()
                    if not clean_name:
                        clean_name = f'{tex.hash:08X}'
                    png_path = out_dir / f"{clean_name}.png"
                    image.write_png(png_path, rgba, w, h)
                    count += 1
                except Exception:
                    pass
    except Exception:
        pass

    # 2. Try standard NFSU2 world TPK
    if count == 0:
        try:
            chunks = bchunk.parse(data)
            packs = list(tpk.iter_packs(chunks, data))
            for pack in packs:
                for tex in pack.textures:
                    try:
                        rgba, w, h = tex.decode()
                        name = tex.name or f'{tex.hash:08X}'
                        clean_name = "".join(c for c in name if c.isalnum() or c in ('_', '-')).strip()
                        if not clean_name:
                            clean_name = f'{tex.hash:08X}'
                        png_path = out_dir / f"{clean_name}.png"
                        image.write_png(png_path, rgba, w, h)
                        count += 1
                    except Exception:
                        pass
        except Exception:
            pass

    # 3. Try JDLZ-compressed car template textures
    if count == 0:
        jdlz_textures = decode_jdlz_texture_pack(data)
        for name, w, h, rgba in jdlz_textures:
            clean_name = "".join(c for c in name if c.isalnum() or c in ('_', '-')).strip()
            png_path = out_dir / f"{clean_name}.png"
            image.write_png(png_path, rgba, w, h)
            count += 1

    if count == 0:
        sys.exit(f'Error: no decodable textures found in {input_file}')

    print(f'[export] Saved {count} textures to {out_dir}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('input', help='Path to TEXTURES.BIN, car directory, or .BUN file')
    parser.add_argument('output', nargs='?', default=None, help='Output directory (optional)')

    args = parser.parse_args()
    convert_textures(args.input, args.output)


if __name__ == '__main__':
    main()
