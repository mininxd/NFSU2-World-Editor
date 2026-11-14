"""Top-down map of every positioned section's TRN_ (road/terrain) triangles,
coloured by height and labelled with the section tag. Needs matplotlib.

    python -m nfsu2_world_editor.cli.sectionmap --tracks DIR [--region L4RA] --out map.png
"""
import argparse
import sys

from ..core.files import region_paths, read_bytes
from ..core.world import section_table, section_objects


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m nfsu2_world_editor.cli.sectionmap',
                                 description=__doc__.splitlines()[0])
    ap.add_argument('--tracks', required=True, help='game TRACKS folder (or the game folder)')
    ap.add_argument('--region', default='L4RA')
    ap.add_argument('--out', required=True, help='output image path (.png)')
    ap.add_argument('--prefix', default='TRN_', help='only draw objects whose name starts with this')
    a = ap.parse_args(argv)
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.collections import PolyCollection
    except ImportError:
        ap.error('matplotlib is required: pip install matplotlib')
    try:
        _, l4, stream = region_paths(a.tracks, a.region)
    except FileNotFoundError as ex:
        ap.error(str(ex))
    table = section_table(read_bytes(l4))
    buf = read_bytes(stream)
    polys, heights, labels = [], [], []
    for e in table:
        if tuple(e['center']) == (0.0, 0.0, 0.0):
            continue
        for o in section_objects(buf, e['offset'], e['size']):
            if not o['name'].startswith(a.prefix):
                continue
            v = o['verts']
            for i, j, k in o['faces']:
                polys.append([v[i][:2], v[j][:2], v[k][:2]])
                heights.append((v[i][2] + v[j][2] + v[k][2]) / 3)
        labels.append((e['tag'], e['center']))
    fig, ax = plt.subplots(figsize=(22, 22), dpi=90)
    pc = PolyCollection(polys, cmap='terrain', linewidths=0)
    pc.set_array(heights)
    ax.add_collection(pc)
    for t, (x, y, _) in labels:
        ax.text(x, y, t, fontsize=6, ha='center', color='red')
    ax.autoscale()
    ax.set_aspect('equal')
    ax.set_title('%s %s geometry, colour = height; red = section tag at table centre' % (a.region, a.prefix))
    plt.colorbar(pc, ax=ax, shrink=0.5, label='z')
    plt.savefig(a.out, bbox_inches='tight')
    print('wrote', a.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
