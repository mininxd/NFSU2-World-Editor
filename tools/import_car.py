"""Headless Blender import of an NFSU2 car (no add-on install needed).

    blender -b --factory-startup --python tools/import_car.py -- \
        --car "C:/Games/NFSU2/CARS/MIATA" --blend out.blend --png preview.png
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nfsu2_world_editor.blender import car_importer, importer  # noqa: E402


def parse(argv):
    ap = argparse.ArgumentParser(prog='blender -b --python tools/import_car.py --',
                                 description='Import an NFSU2 car into Blender')
    ap.add_argument('--car', required=True, help='CARS/<NAME> folder or GEOMETRY.BIN')
    ap.add_argument('--kit', default='KIT00')
    ap.add_argument('--lod', default='A')
    ap.add_argument('--paint', default='0.75,0.06,0.07',
                    help='CARSKIN RGBA or RGB, comma-separated 0-1 floats')
    ap.add_argument('--global-dir', help='game GLOBAL folder (default: guessed from --car)')
    ap.add_argument('--no-textures', action='store_true')
    ap.add_argument('--keep', action='store_true')
    ap.add_argument('--blend', help='save a .blend here')
    ap.add_argument('--png', help='render a preview here')
    return ap.parse_args(argv)


def _paint(s):
    v = [float(x) for x in s.split(',')]
    if len(v) == 3:
        v.append(1.0)
    if len(v) != 4:
        raise SystemExit('--paint needs 3 or 4 comma-separated numbers')
    return tuple(v)


def main():
    a = parse(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    import bpy
    if not a.keep:
        importer.clear_scene()
    t0 = time.time()
    try:
        root = car_importer.import_car(a.car, kit=a.kit, lod=a.lod, paint=_paint(a.paint),
                                       global_dir=a.global_dir, textures=not a.no_textures)
    except (FileNotFoundError, ValueError) as ex:
        print('[nfsu2] error:', ex, file=sys.stderr)
        sys.exit(1)
    print('[nfsu2] imported %s in %.1fs' % (root.name, time.time() - t0))
    if a.blend:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.blend))
    if a.png:
        importer.render_preview(a.png, color_type='MATERIAL')


main()
