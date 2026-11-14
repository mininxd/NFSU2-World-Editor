"""List the streaming sections of a region (tag, number, stream offset/size, centre).

    python -m nfsu2_world_editor.cli.sections --tracks DIR [--region L4RA] [--json OUT]
"""
import argparse
import json
import sys

from ..core.files import region_paths, read_bytes
from ..core.world import section_table


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m nfsu2_world_editor.cli.sections',
                                 description=__doc__.splitlines()[0])
    ap.add_argument('--tracks', required=True, help='game TRACKS folder (or the game folder)')
    ap.add_argument('--region', default='L4RA', help='region, default L4RA (Bayview)')
    ap.add_argument('--json', help='also write the table as JSON to this path')
    a = ap.parse_args(argv)
    try:
        _, l4, _ = region_paths(a.tracks, a.region)
    except FileNotFoundError as ex:
        ap.error(str(ex))
    table = section_table(read_bytes(l4))
    print('%-6s %5s %10s %10s %10s %10s %10s' % ('tag', 'num', 'offset', 'size', 'cx', 'cy', 'cz'))
    for e in table:
        print('%-6s %5d %10d %10d %10.1f %10.1f %10.1f' % ((e['tag'], e['num'], e['offset'], e['size'])
                                                         + tuple(e['center'])))
    print('# %d sections' % len(table))
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as f:
            json.dump(table, f, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
