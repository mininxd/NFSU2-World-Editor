"""Headless Blender import of NFSU2 world sections (no add-on install needed).

    blender -b --factory-startup --python tools/import_world.py -- \
        --tracks "C:/Games/NFSU2/TRACKS" --region L4RA --sections C21,C22,B26 \
        --instances --blend out.blend --obj out.obj --png preview.png
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nfsu2_world_editor.blender import importer  # noqa: E402


def parse(argv):
    ap = argparse.ArgumentParser(prog='blender -b --python tools/import_world.py --',
                                 description='Import NFSU2 world sections into Blender')
    ap.add_argument('--tracks', required=True, help='game TRACKS folder (or the game folder)')
    ap.add_argument('--region', default='L4RA')
    ap.add_argument('--sections', default='C21', help='comma-separated tags, e.g. C21,C22,B26, or "all" for the whole region')
    ap.add_argument('--instances', action='store_true', help='also place scenery instances')
    ap.add_argument('--no-textures', action='store_true', help='skip texture decoding (grey materials)')
    ap.add_argument('--global-dir', help='game GLOBAL folder for shared textures (default: next to TRACKS)')
    ap.add_argument('--collision', action='store_true', help='add COLLISION_<tag> wireframe meshes')
    ap.add_argument('--skip-pan', action='store_true', help='leave out PAN_*/SKYDOME backdrop models')
    ap.add_argument('--keep', action='store_true', help='keep the default scene objects')
    ap.add_argument('--blend', help='save a .blend here')
    ap.add_argument('--obj', help='export a Wavefront .obj here')
    ap.add_argument('--png', help='render a top-down preview here')
    ap.add_argument('--png-mode', default='TEXTURE', choices=('TEXTURE', 'MATERIAL'),
                    help='Workbench colour mode for --png (default TEXTURE)')
    return ap.parse_args(argv)


def main():
    a = parse(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    import bpy
    if not a.keep:
        importer.clear_scene()
    t0 = time.time()
    try:
        stats = importer.import_world(a.tracks, a.region, a.sections, a.instances,
                                      textures=not a.no_textures, skip_panoramas=a.skip_pan, collision=a.collision,
                                      global_dir=a.global_dir)
    except (FileNotFoundError, KeyError) as ex:
        print('[nfsu2] error:', ex, file=sys.stderr)
        sys.exit(1)
    print('[nfsu2] imported %d section(s): %d solids, %d solid tris, %d scenery instances in %.1fs'
          % (len(stats), sum(v[0] for v in stats.values()), sum(v[1] for v in stats.values()),
             sum(v[2] for v in stats.values()), time.time() - t0))
    if a.blend:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.blend))
    if a.obj:
        bpy.ops.wm.obj_export(filepath=os.path.abspath(a.obj), export_materials=True,
                              export_colors=True, forward_axis='Y', up_axis='Z')
    if a.png:
        importer.render_preview(a.png, color_type=a.png_mode)


main()
