"""Build a new-road mod from a plan JSON (see nfsu2_world_editor/core/road_mod.py).

    python tools/build_road_mod.py --tracks PATH/TRACKS --plan plan.json --out OUTDIR [--host E21]

Writes OUTDIR/STREAML4RA.BUN and OUTDIR/L4RA.BUN (copies; originals untouched).
Needs ``shapely`` for the drivable-boundary extension (pip install shapely).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nfsu2_world_editor.core.road_mod import build_road_mod  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tracks', required=True)
    ap.add_argument('--plan', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--host', default='E21')
    ap.add_argument('--region', default='L4RA')
    a = ap.parse_args()
    if os.path.abspath(a.out) == os.path.abspath(a.tracks):
        sys.exit('refusing to write into the source TRACKS folder')
    with open(a.plan) as f:
        plan = json.load(f)
    r = build_road_mod(a.tracks, plan, a.out, host=a.host, region_name=a.region)
    print(json.dumps({k: v for k, v in r.items() if k != 'models'}, indent=1))


if __name__ == '__main__':
    main()
