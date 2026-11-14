"""Headless Blender export: write the edited NFSU2 sections of a .blend back
into new game files (the originals are never touched).

    blender -b edited.blend --python tools/export_world.py -- --out OUTDIR \
        [--tracks "C:/Games/NFSU2/TRACKS"]

Only objects whose mesh/transform changed since import (and new mesh objects
placed in a SECTION_<tag> collection) are re-encoded; everything else is
copied byte for byte. Writes OUTDIR/STREAM<region>.BUN and OUTDIR/<region>.BUN.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nfsu2_world_editor.blender import exporter  # noqa: E402


def main():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    ap = argparse.ArgumentParser(prog='blender -b FILE.blend --python tools/export_world.py --')
    ap.add_argument('--out', required=True, help='output folder for the modified BUN files')
    ap.add_argument('--tracks', help='source TRACKS folder (default: the one used at import)')
    ap.add_argument('--region', help='region (default: the one used at import)')
    a = ap.parse_args(argv)
    try:
        exporter.export_world(os.path.abspath(a.out), tracks=a.tracks, region=a.region)
    except (RuntimeError, ValueError, FileNotFoundError, KeyError) as ex:
        print('[nfsu2] error:', ex, file=sys.stderr)
        sys.exit(1)


main()
