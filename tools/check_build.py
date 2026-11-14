"""Checks for the built variants: layout rules (core/layout.py), an independent
chunk walk (no repo parser), which sections / table entries changed.

    NFSU2_TRACKS=PATH/TRACKS python tools/check_build.py OUTDIR [OUTDIR ...]
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import struct
from nfsu2_world_editor.core.stream import Region
from nfsu2_world_editor.core.layout import section_violations

TR = os.environ.get('NFSU2_TRACKS') or sys.exit('set NFSU2_TRACKS to the original TRACKS folder')
ORIG_ST = open(TR + '/STREAML4RA.BUN', 'rb').read()
orig = Region(ORIG_ST, open(TR + '/L4RA.BUN', 'rb').read(), 'L4RA')
o_ent = {e['tag']: e for e in orig.entries}

def walk(buf, s, e, errs, depth=0):
    p = s
    while p < e:
        if p + 8 > e:
            errs.append('truncated header at 0x%X' % p); return
        cid, size = struct.unpack_from('<II', buf, p)
        if p + 8 + size > e:
            errs.append('0x%08X at 0x%X overruns parent end 0x%X' % (cid, p, e)); return
        if cid & 0x80000000 and size and depth < 12:
            walk(buf, p + 8, p + 8 + size, errs, depth + 1)
        p += 8 + size

for v in sys.argv[1:]:
    st = open(os.path.join(v, 'STREAML4RA.BUN'), 'rb').read()
    l4 = open(os.path.join(v, 'L4RA.BUN'), 'rb').read()
    r = Region(st, l4, 'L4RA')
    bad = section_violations(st, r.entries)
    nprob = sum(len(x) for x in bad.values())
    errs = []
    walk(st, 0, len(st), errs); walk(l4, 0, len(l4), errs)
    # every positioned section must start on a chunk boundary of the top-level walk
    tops = set(); p = 0
    while p < len(st):
        tops.add(p); cid, size = struct.unpack_from('<II', st, p); p += 8 + size
    for e in r.entries:
        if e['size'] and e['tag'] != '--' and e['offset'] not in tops:
            errs.append('section %s offset 0x%X not on a top-level chunk' % (e['tag'], e['offset']))
        if e['offset'] % 2048 and e['size']:
            errs.append('section %s offset not 2 KiB aligned' % e['tag'])
    changed = [e['tag'] for e in r.entries if (e['offset'], e['size']) != (o_ent[e['tag']]['offset'], o_ent[e['tag']]['size'])]
    moved = [e['tag'] for e in r.entries if e['offset'] != o_ent[e['tag']]['offset']]
    content = [e['tag'] for e in r.entries if e['size'] and e['tag'] != '--' and
               st[e['offset']:e['offset'] + e['size']] != ORIG_ST[o_ent[e['tag']]['offset']:o_ent[e['tag']]['offset'] + o_ent[e['tag']]['size']]]
    print('%s: layout problems %d %s | chunk-walk errors %d %s | table entries changed %s | moved %s | sections with new content %s | sizes stream %d l4 %d'
          % (os.path.basename(v), nprob, list(bad)[:5], len(errs), errs[:3], changed, moved, content, len(st), len(l4)))
