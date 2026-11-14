"""List, round-trip and export textures of an NFSU2 car.

    python -m nfsu2_world_editor.cli.cars list PATH
    python -m nfsu2_world_editor.cli.cars roundtrip PATH [--meshes] [--out DIR]
    python -m nfsu2_world_editor.cli.cars export-textures PATH --out DIR [--format png|tga]

PATH is a ``CARS/<NAME>`` folder or a ``GEOMETRY.BIN`` file. The originals
are never modified; ``--out`` writes a copy.
"""
import argparse
import os
import sys
import time

from ..core import cars
from ..core.image import write_image
from ..core.tpk import USAGE_NAMES, BLEND_NAMES


def _open(path):
    geo, tex, folder = cars.car_files(path)
    return cars.CarGeometry.open(geo), tex, folder


def cmd_list(a):
    car, tex, folder = _open(a.path)
    print('%s: %d parts in %s' % (car.name, len(car.parts), folder))
    if tex:
        packs = cars.load_car_textures(tex)
        n = sum(len(p.textures) for p in packs)
        print('textures: %s (%d textures in %d pack(s))' % (tex, n, len(packs)))
    stock = car.stock_parts(a.kit, a.lod)
    print('stock %s lod-%s: %d parts' % (a.kit, a.lod, len(stock)))
    print('%-36s %4s %-8s %6s %6s  classes' % ('name', 'LOD', 'kit', 'verts', 'faces'))
    for p in (stock if a.stock else car.parts):
        cls = ','.join(cars.CLASS_NAMES.get(c, '%08X' % c) for c in p.classes)
        print('%-36s %4s %-8s %6d %6d  %s' % (p.name, p.lod or '-', p.kit or '-', p.nverts, p.nfaces, cls))
    return 0


def cmd_roundtrip(a):
    t0 = time.time()
    car, tex, folder = _open(a.path)
    src = car.src
    if a.meshes:
        n = car.reencode_meshes()
        print('re-encoded %d meshes' % n)
    out = car.rebuild()
    same = out == src
    print('GEOMETRY.BIN: %d bytes, %s' % (len(src), 'byte-identical' if same else 'DIFFERENT'))
    ok = same
    if tex:
        from ..core import bchunk
        from ..core.files import read_bytes
        buf = read_bytes(tex)
        rebuilt = bytes(bchunk.serialize(bchunk.parse(buf)))
        t_ok = rebuilt == buf
        print('TEXTURES.BIN: %d bytes, %s' % (len(buf), 'byte-identical' if t_ok else 'DIFFERENT'))
        ok &= t_ok
        if a.meshes:
            packs = cars.load_car_textures(tex)
            n = 0
            for p in packs:
                for t in p.textures:
                    t.decode()
                    n += 1
            print('textures: %d decoded' % n)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        geo_out = os.path.join(a.out, 'GEOMETRY.BIN')
        open(geo_out, 'wb').write(out)
        print('wrote %s' % geo_out)
        if tex:
            tex_out = os.path.join(a.out, 'TEXTURES.BIN')
            open(tex_out, 'wb').write(read_bytes(tex) if same else rebuilt)
            print('wrote %s (unmodified copy)' % tex_out)
    print('%s in %.2fs' % ('OK' if ok else 'MISMATCH', time.time() - t0))
    return 0 if ok else 1


def cmd_export_textures(a):
    car, tex, folder = _open(a.path)
    if not tex:
        print('error: no TEXTURES.BIN next to the geometry', file=sys.stderr)
        return 1
    packs = cars.load_car_textures(tex)
    os.makedirs(a.out, exist_ok=True)
    n = 0
    for p in packs:
        for t in p.textures:
            rgba, w, h = t.decode()
            name = '%08X_%s.%s' % (t.hash, t.name, a.format)
            write_image(os.path.join(a.out, name), rgba, w, h)
            n += 1
            if a.list:
                print('%08X %s %dx%d %s mips=%d usage=%s' % (
                    t.hash, t.name, w, h, t.format_name, t.mips,
                    USAGE_NAMES.get(t.usage, t.usage)))
    print('wrote %d textures to %s' % (n, a.out))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m nfsu2_world_editor.cli.cars',
                                 description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest='cmd', required=True)

    p_list = sub.add_parser('list', help='list parts (and textures) of a car')
    p_list.add_argument('path', help='CARS/<NAME> folder or GEOMETRY.BIN')
    p_list.add_argument('--kit', default='KIT00')
    p_list.add_argument('--lod', default='A')
    p_list.add_argument('--stock', action='store_true', help='only print the assembled stock kit')
    p_list.set_defaults(func=cmd_list)

    p_rt = sub.add_parser('roundtrip', help='parse and rebuild; must be byte-identical')
    p_rt.add_argument('path')
    p_rt.add_argument('--meshes', action='store_true', help='also decode + re-encode every mesh')
    p_rt.add_argument('--out', help='write the rebuilt GEOMETRY.BIN (and a TEXTURES.BIN copy) here')
    p_rt.set_defaults(func=cmd_roundtrip)

    p_ex = sub.add_parser('export-textures', help='decode car textures to PNG/TGA')
    p_ex.add_argument('path')
    p_ex.add_argument('--out', required=True)
    p_ex.add_argument('--format', default='png', choices=('png', 'tga'))
    p_ex.add_argument('--list', action='store_true')
    p_ex.set_defaults(func=cmd_export_textures)

    a = ap.parse_args(argv)
    try:
        return a.func(a)
    except (FileNotFoundError, ValueError) as ex:
        print('error: %s' % ex, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
