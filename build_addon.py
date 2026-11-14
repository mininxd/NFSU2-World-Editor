#!/usr/bin/env python3
"""Zip the Blender add-on for installation (pure Python, Windows/Linux/macOS).

    python build_addon.py [--out dist]

Produces ``dist/nfsu2_world_editor-<version>.zip`` with a single top-level
``nfsu2_world_editor/`` folder, installable either as a Blender 4.2+ extension
(Edit > Preferences > Get Extensions > Install from Disk) or as a legacy add-on
(Preferences > Add-ons > Install from Disk).
"""
import argparse
import ast
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent
PKG = 'nfsu2_world_editor'
EXCLUDE_DIRS = {'__pycache__', 'tests', '.pytest_cache'}
EXCLUDE_SUFFIX = {'.pyc', '.pyo', '.blend', '.blend1', '.bun', '.bin', '.obj', '.png'}


def versions(pkg_dir):
    manifest = (pkg_dir / 'blender_manifest.toml').read_text(encoding='utf-8')
    m = re.search(r'^version\s*=\s*"([^"]+)"', manifest, re.M)
    tree = ast.parse((pkg_dir / '__init__.py').read_text(encoding='utf-8'))
    bl = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
              and any(getattr(t, 'id', None) == 'bl_info' for t in n.targets))
    return m.group(1), '.'.join(map(str, bl['version']))


def build(out_dir):
    pkg_dir = ROOT / PKG
    man_v, bl_v = versions(pkg_dir)
    if man_v != bl_v:
        raise SystemExit('version mismatch: blender_manifest.toml %s vs bl_info %s' % (man_v, bl_v))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / ('%s-%s.zip' % (PKG, man_v))
    files = sorted(p for p in pkg_dir.rglob('*') if p.is_file()
                   and not EXCLUDE_DIRS.intersection(p.relative_to(pkg_dir).parts)
                   and p.suffix.lower() not in EXCLUDE_SUFFIX)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in files:
            arc = PurePosixPath(PKG, *p.relative_to(pkg_dir).parts)  # forward slashes on every OS
            z.write(p, str(arc))
    return target, [f.relative_to(pkg_dir).as_posix() for f in files]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--out', default=str(ROOT / 'dist'), help='output folder (default: dist)')
    a = ap.parse_args(argv)
    target, files = build(a.out)
    print('built %s (%d files)' % (target, len(files)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
