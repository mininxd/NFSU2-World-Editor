"""Export the textures of a region to PNG/TGA and import edited images back.

    python -m nfsu2_world_editor.cli.textures export --tracks DIR --out TEXDIR
            [--region L4RA] [--sections C21,B26] [--format png|tga] [--list]
    python -m nfsu2_world_editor.cli.textures import --tracks DIR --dir TEXDIR
            --out MODDIR [--region L4RA]

``export`` writes one image per unique texture, named ``<HASH>_<NAME>.png``,
plus ``textures.json`` (format, size, mips, alpha mode and a checksum of the
exported pixels). Edit any image in a paint program, keeping its size.

``import`` re-encodes every image whose pixels changed into the texture's
original format (DXT1/DXT3/DXT5/P8/ARGB8, same size, mips regenerated) inside
every pack that holds it, and writes ``STREAM<region>.BUN`` + ``<region>.BUN``
to MODDIR. Unchanged images keep their original bytes, so importing an
unedited export reproduces the game file byte for byte. The originals are
never modified.
"""
import argparse
import hashlib
import json
import os
import sys
import time

from ..core import dxt
from ..core.image import read_image, write_image
from ..core.stream import Region
from ..core.tpk import apply_image, USAGE_NAMES, BLEND_NAMES

MANIFEST = 'textures.json'


def _safe(name):
    return ''.join(ch if ch.isalnum() or ch in '_-.' else '_' for ch in name) or 'unnamed'


def _sha(rgba):
    return hashlib.sha1(bytes(rgba)).hexdigest()


def cmd_export(a):
    reg = Region.open(a.tracks, a.region)
    sections = [s.strip() for s in a.sections.split(',') if s.strip()] if a.sections else None
    packs = reg.texture_packs(sections)
    seen = {}
    for p, tag in packs:
        for t in p.textures:
            seen.setdefault(t.hash, (t, set()))[1].add(tag or '?')
    if a.list:
        for h, (t, tags) in sorted(seen.items()):
            print('%08X %-24s %4dx%-4d %-5s mips=%d %-9s %-8s %s' % (
                h, t.name, t.width, t.height, t.format_name, t.mips, USAGE_NAMES.get(t.usage, t.usage),
                BLEND_NAMES.get(t.blend, t.blend), ','.join(sorted(tags))))
        print('# %d unique textures in %d packs' % (len(seen), len(packs)))
        return 0
    os.makedirs(a.out, exist_ok=True)
    ext = '.' + a.format.lower()
    entries = []
    stats = {}
    t0 = time.time()
    for i, (h, (t, tags)) in enumerate(sorted(seen.items())):
        try:
            rgba, w, hh = t.decode()
        except ValueError as ex:
            print('skip %08X %s: %s' % (h, t.name, ex), file=sys.stderr)
            continue
        fn = '%08X_%s%s' % (h, _safe(t.name), ext)
        write_image(os.path.join(a.out, fn), rgba, w, hh)
        stats[t.format_name] = stats.get(t.format_name, 0) + 1
        entries.append(dict(file=fn, hash='%08X' % h, name=t.name, format=t.format_name,
                            width=w, height=hh, mips=t.mips, usage=t.usage, blend=t.blend,
                            alpha_is_opacity=t.alpha_is_opacity(), sections=sorted(tags),
                            sha1=_sha(rgba)))
        if (i + 1) % 200 == 0:
            print('  %d / %d textures...' % (i + 1, len(seen)))
    with open(os.path.join(a.out, MANIFEST), 'w') as f:
        json.dump(dict(region=reg.name, textures=entries), f, indent=1)
    print('exported %d textures (%s) to %s in %.1fs' % (
        len(entries), ', '.join('%s %d' % kv for kv in sorted(stats.items())), a.out, time.time() - t0))
    return 0


def import_textures(reg, tex_dir, log=print):
    """Apply edited images from ``tex_dir`` (with its textures.json) to ``reg``.
    Returns the number of texture records re-encoded."""
    with open(os.path.join(tex_dir, MANIFEST)) as f:
        man = json.load(f)
    index = reg.texture_index()
    changed = 0
    for e in man['textures']:
        path = os.path.join(tex_dir, e['file'])
        if not os.path.exists(path):
            continue
        rgba, w, h = read_image(path)
        if _sha(rgba) == e['sha1']:
            continue
        recs = index.get(int(e['hash'], 16), [])
        for t in recs:
            apply_image(t, rgba, w, h)
            changed += 1
        log('  %s -> %d record(s) re-encoded as %s' % (e['file'], len(recs), e['format']))
    return changed


def cmd_import(a):
    reg = Region.open(a.tracks, a.region)
    t0 = time.time()
    n = import_textures(reg, a.dir)
    sp, lp = reg.write(a.out)
    print('%d texture record(s) updated; wrote %s and %s in %.1fs' % (n, sp, lp, time.time() - t0))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m nfsu2_world_editor.cli.textures',
                                 description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    e = sub.add_parser('export', help='decode textures to PNG/TGA')
    e.add_argument('--tracks', required=True, help='game TRACKS folder (or the game folder)')
    e.add_argument('--region', default='L4RA')
    e.add_argument('--out', help='output folder')
    e.add_argument('--sections', help='only packs inside these section tags, e.g. C22,B26')
    e.add_argument('--format', default='png', choices=('png', 'tga'))
    e.add_argument('--list', action='store_true', help='only list textures')
    i = sub.add_parser('import', help='re-encode edited images into a new BUN')
    i.add_argument('--tracks', required=True)
    i.add_argument('--region', default='L4RA')
    i.add_argument('--dir', required=True, help='folder written by "export" (with textures.json)')
    i.add_argument('--out', required=True, help='output folder for the modified BUN files')
    a = ap.parse_args(argv)
    if a.cmd == 'export' and not a.list and not a.out:
        ap.error('--out is required (or use --list)')
    try:
        return cmd_export(a) if a.cmd == 'export' else cmd_import(a)
    except (FileNotFoundError, ValueError, KeyError) as ex:
        print('error: %s' % ex, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
