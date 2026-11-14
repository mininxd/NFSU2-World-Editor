"""Walk the chunks of an NFSU2 .BUN/.BIN file and print an ID histogram.

    python -m nfsu2_world_editor.cli.chunkdump FILE [--tree [MAXDEPTH]]
    python -m nfsu2_world_editor.cli.chunkdump --tracks DIR --file STREAML4RA.BUN
"""
import argparse
import collections
import sys

from ..core.chunks import walk
from ..core.files import find_file, resolve_tracks_dir, read_bytes


def dump(buf, name='', tree_depth=None, out=sys.stdout):
    hist, nbytes, bad = collections.Counter(), collections.Counter(), 0
    for kind, off, cid, size, depth, _ in walk(buf, 0, len(buf)):
        if kind == 'BAD':
            bad += 1
            print('!! bad chunk @0x%X id=0x%08X size=0x%X' % (off, cid, size), file=out)
            continue
        hist[cid] += 1
        nbytes[cid] += size
        if tree_depth is not None and depth <= tree_depth:
            print('%s0x%08X @0x%08X size=0x%X' % ('  ' * depth, cid, off, size), file=out)
    print('# %s: %d bytes, %d chunks, %d bad' % (name, len(buf), sum(hist.values()), bad), file=out)
    print('# id          count       bytes', file=out)
    for cid, n in sorted(hist.items(), key=lambda x: -x[1]):
        print('0x%08X %8d %12d' % (cid, n, nbytes[cid]), file=out)
    return hist, bad


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m nfsu2_world_editor.cli.chunkdump',
                                 description=__doc__.splitlines()[0])
    ap.add_argument('file', nargs='?', help='path to a .BUN/.BIN file')
    ap.add_argument('--tracks', help='TRACKS folder; FILE/--file is then looked up in it (case-insensitive)')
    ap.add_argument('--file', dest='name', help='file name inside --tracks, e.g. STREAML4RA.BUN')
    ap.add_argument('--tree', nargs='?', type=int, const=99, default=None, metavar='MAXDEPTH',
                    help='also print the chunk tree down to MAXDEPTH')
    a = ap.parse_args(argv)
    name = a.name or a.file
    if not name:
        ap.error('give FILE or --tracks DIR --file NAME')
    if a.tracks:
        path = find_file(resolve_tracks_dir(a.tracks), name)
        if path is None:
            ap.error('%s not found in %s' % (name, a.tracks))
    else:
        path = name
    dump(read_bytes(path), str(path), a.tree)
    return 0


if __name__ == '__main__':
    sys.exit(main())
