"""Build the highway + island mod (version 2) from a plan JSON.

    python tools/build_highway_mod.py --tracks PATH/TRACKS --plan plan.json --out OUTDIR [--minimal]

Writes OUTDIR/STREAML4RA.BUN and OUTDIR/L4RA.BUN (copies; originals untouched).
Needs ``shapely``. See nfsu2_world_editor/core/highway_mod.py.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nfsu2_world_editor.core.highway_mod import build_highway_mod, HOSTS, LIST_BUDGET  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tracks', required=True)
    ap.add_argument('--plan', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--region', default='L4RA')
    ap.add_argument('--hosts', default=','.join(HOSTS), help='host sections in road order')
    ap.add_argument('--budget', type=int, default=LIST_BUDGET, help='max bytes per load list')
    ap.add_argument('--minimal', action='store_true',
                    help='diagnostic: road only, visibility untouched, marker pillar at the north junction')
    a = ap.parse_args()
    if os.path.realpath(a.out) == os.path.realpath(a.tracks):
        sys.exit('refusing to write into the source TRACKS folder')
    with open(a.plan) as f:
        plan = json.load(f)
    r = build_highway_mod(a.tracks, plan, a.out, region_name=a.region, hosts=tuple(a.hosts.split(',')),
                          budget=a.budget, island=not a.minimal, boundaries=not a.minimal, test_marker=a.minimal)
    print(json.dumps({k: v for k, v in r.items() if k not in ('stream', 'l4', 'models', 'owners', 'hosts')}, indent=1))


if __name__ == '__main__':
    main()
