"""Cross-platform, case-insensitive location of game files.

Windows installs, Linux copies from a case-insensitive drive, Proton prefixes etc.
may spell names differently (``STREAML4RA.BUN`` vs ``streamL4RA.bun``), so every
lookup here tries the exact name first and then a case-insensitive directory scan.
"""
import os
import re
from pathlib import Path

_REGION_RE = re.compile(r'^(?:STREAM)?(L\d+R[A-Z0-9])\.BUN$', re.IGNORECASE)


def find_file(folder, name):
    """Return the Path of ``name`` inside ``folder`` ignoring case, or None."""
    folder = Path(folder)
    exact = folder / name
    if exact.is_file():
        return exact
    if not folder.is_dir():
        return None
    want = name.casefold()
    for entry in os.scandir(folder):
        if entry.name.casefold() == want and entry.is_file():
            return Path(entry.path)
    return None


def _find_dir(folder, name):
    folder = Path(folder)
    if not folder.is_dir():
        return None
    want = name.casefold()
    for entry in os.scandir(folder):
        if entry.name.casefold() == want and entry.is_dir():
            return Path(entry.path)
    return None


def resolve_tracks_dir(path):
    """Accept the TRACKS folder itself, the game root that contains it, or any file
    inside TRACKS. Returns a Path to the folder holding the region .BUN files."""
    p = Path(os.path.expanduser(str(path)))
    if p.is_file():
        p = p.parent
    if p.is_dir() and not list_regions(p):
        sub = _find_dir(p, 'TRACKS')
        if sub is not None:
            return sub
    return p


def list_regions(tracks_dir):
    """Region names (e.g. ``['L4RA', ...]``) that have both L4Rx.BUN and STREAML4Rx.BUN."""
    tracks_dir = Path(tracks_dir)
    if not tracks_dir.is_dir():
        return []
    plain, stream = set(), set()
    for entry in os.scandir(tracks_dir):
        m = _REGION_RE.match(entry.name)
        if m:
            region = m.group(1).upper()
            (stream if entry.name.upper().startswith('STREAM') else plain).add(region)
    return sorted(plain & stream)


def region_from_filename(name):
    """``'STREAML4RA.BUN'`` / ``'l4ra.bun'`` -> ``'L4RA'``; anything else -> None."""
    m = _REGION_RE.match(Path(name).name)
    return m.group(1).upper() if m else None


def region_paths(tracks, region='L4RA'):
    """Return ``(tracks_dir, l4_path, stream_path)`` or raise FileNotFoundError with a
    helpful message."""
    tracks_dir = resolve_tracks_dir(tracks)
    region = region.upper()
    l4 = find_file(tracks_dir, region + '.BUN')
    stream = find_file(tracks_dir, 'STREAM' + region + '.BUN')
    missing = [n for n, p in ((region + '.BUN', l4), ('STREAM' + region + '.BUN', stream)) if p is None]
    if missing:
        have = list_regions(tracks_dir)
        raise FileNotFoundError(
            'Missing %s in %s%s' % (', '.join(missing), tracks_dir,
                                    ' (regions found: %s)' % ', '.join(have) if have else
                                    ' (no NFSU2 region files found; point to your game\'s TRACKS folder)'))
    return tracks_dir, l4, stream


def read_bytes(path):
    with open(path, 'rb') as f:
        return f.read()
