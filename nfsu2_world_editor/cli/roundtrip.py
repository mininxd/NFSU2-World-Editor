"""Verify the converter on your own files: parse and rebuild a region and check
the output is byte-identical (nothing is written unless --out is given).

    python -m nfsu2_world_editor.cli.roundtrip --tracks DIR [--region L4RA]
        [--meshes] [--textures] [--out DIR]

--meshes   also decode + re-encode every mesh object (in memory) and count
           the byte-identical ones
--textures also decode every texture (format census)
"""
import argparse
import sys
import time

from ..core import bchunk, meshcodec
from ..core.stream import Region


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m nfsu2_world_editor.cli.roundtrip',
                                 description=__doc__.splitlines()[0])
    ap.add_argument('--tracks', required=True)
    ap.add_argument('--region', default='L4RA')
    ap.add_argument('--meshes', action='store_true')
    ap.add_argument('--textures', action='store_true')
    ap.add_argument('--out', help='also write the rebuilt files here')
    a = ap.parse_args(argv)
    t0 = time.time()
    try:
        reg = Region.open(a.tracks, a.region)
    except (FileNotFoundError, ValueError) as ex:
        print('error: %s' % ex, file=sys.stderr)
        return 1
    ok = True
    stream, l4 = reg.build()
    s_ok = bytes(stream) == bytes(reg.src)
    l_ok = bytes(l4) == bytes(reg.l4)
    print('STREAM%s.BUN: %d bytes, rebuilt %s' % (reg.name, len(stream), 'byte-identical' if s_ok else 'DIFFERENT'))
    print('%s.BUN: %d bytes, rebuilt %s' % (reg.name, len(l4), 'byte-identical' if l_ok else 'DIFFERENT'))
    ok &= s_ok and l_ok
    if a.out:
        print('wrote %s, %s' % reg.write(a.out))
    if a.meshes:
        same = diff = skip = 0
        for top in reg.chunks:
            if top.children is None:
                continue
            for o in top.find_all(meshcodec.SOLID_OBJECT):
                before = bchunk.chunk_bytes(o, o.offset)
                m = meshcodec.decode(o)
                if m is None:
                    skip += 1
                    continue
                meshcodec.encode(o, m)
                if bchunk.chunk_bytes(o, o.offset) == before:
                    same += 1
                else:
                    diff += 1
                    print('  re-encode differs: %s @%d' % (m.name, o.offset))
        print('meshes: %d re-encoded byte-identical, %d differ, %d without mesh' % (same, diff, skip))
    if a.textures:
        fm = {}
        n = 0
        for p, _tag in reg.texture_packs():
            for t in p.textures:
                t.decode()
                fm[t.format_name] = fm.get(t.format_name, 0) + 1
                n += 1
        print('textures: %d records decoded (%s)' % (n, ', '.join('%s %d' % kv for kv in sorted(fm.items()))))
    print('%s in %.1fs' % ('OK' if ok else 'MISMATCH', time.time() - t0))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
