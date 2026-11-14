"""Headless Blender import of NFSU2 world sections (no add-on install needed).

    blender -b --factory-startup --python tools/import_world.py -- \
        --tracks "C:/Games/NFSU2/TRACKS" --region L4RA --sections C21,C22,B26 \
        --instances --blend out.blend --obj out.obj --png preview.png
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nfsu2_world_editor.blender import importer  # noqa: E402


def parse(argv):
    ap = argparse.ArgumentParser(prog='blender -b --python tools/import_world.py --',
                                 description='Import NFSU2 world sections into Blender')
    ap.add_argument('--tracks', required=True, help='game TRACKS folder (or the game folder)')
    ap.add_argument('--region', default='L4RA')
    ap.add_argument('--sections', default='C21', help='comma-separated tags, e.g. C21,C22,B26')
    ap.add_argument('--instances', action='store_true', help='also place scenery instances')
    ap.add_argument('--keep', action='store_true', help='keep the default scene objects')
    ap.add_argument('--blend', help='save a .blend here')
    ap.add_argument('--obj', help='export a Wavefront .obj here')
    ap.add_argument('--png', help='render a top-down preview here')
    return ap.parse_args(argv)


def main():
    a = parse(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    import bpy
    if not a.keep:
        importer.clear_scene()
    try:
        importer.import_world(a.tracks, a.region, a.sections, a.instances)
    except (FileNotFoundError, KeyError) as ex:
        print('[nfsu2] error:', ex, file=sys.stderr)
        sys.exit(1)
    if a.blend:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.blend))
    if a.obj:
        bpy.ops.wm.obj_export(filepath=os.path.abspath(a.obj), export_materials=True,
                              export_colors=True, forward_axis='Y', up_axis='Z')
    if a.png:
        importer.render_preview(a.png)


main()
