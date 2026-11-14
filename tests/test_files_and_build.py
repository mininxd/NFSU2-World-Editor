import zipfile

from nfsu2_world_editor.core.files import (find_file, resolve_tracks_dir, list_regions,
                                           region_paths, region_from_filename)
import build_addon


def _fake_install(root):
    tracks = root / 'Need for Speed Underground 2' / 'Tracks'
    tracks.mkdir(parents=True)
    (tracks / 'l4ra.bun').write_bytes(b'')
    (tracks / 'StreamL4RA.BUN').write_bytes(b'')
    (tracks / 'L4RB.BUN').write_bytes(b'')   # no stream file -> not a full region
    return tracks


def test_case_insensitive_lookup(tmp_path):
    tracks = _fake_install(tmp_path)
    assert find_file(tracks, 'L4RA.BUN').name.lower() == 'l4ra.bun'
    assert find_file(tracks, 'STREAML4RA.BUN') is not None
    assert find_file(tracks, 'MISSING.BUN') is None
    assert list_regions(tracks) == ['L4RA']


def test_resolve_from_game_root_and_file(tmp_path):
    tracks = _fake_install(tmp_path)
    game = tracks.parent
    assert resolve_tracks_dir(game).resolve() == tracks.resolve()
    assert resolve_tracks_dir(tracks / 'l4ra.bun').resolve() == tracks.resolve()
    d, l4, stream = region_paths(game, 'l4ra')
    assert l4.name == 'l4ra.bun' and stream.name == 'StreamL4RA.BUN'


def test_region_paths_error_lists_regions(tmp_path):
    tracks = _fake_install(tmp_path)
    try:
        region_paths(tracks, 'L4RC')
    except FileNotFoundError as ex:
        assert 'L4RA' in str(ex)
    else:
        raise AssertionError('expected FileNotFoundError')


def test_region_from_filename():
    assert region_from_filename('STREAML4RA.BUN') == 'L4RA'
    assert region_from_filename('/x/y/l4rb.bun') == 'L4RB'
    assert region_from_filename('GLOBALB.BUN') is None


def test_build_zip(tmp_path):
    target, files = build_addon.build(tmp_path)
    with zipfile.ZipFile(target) as z:
        names = z.namelist()
    assert 'nfsu2_world_editor/__init__.py' in names
    assert 'nfsu2_world_editor/blender_manifest.toml' in names
    assert 'nfsu2_world_editor/core/world.py' in names
    assert all(n.startswith('nfsu2_world_editor/') and '\\' not in n for n in names)
    assert not any('__pycache__' in n or n.endswith('.pyc') for n in names)


def test_core_has_no_bpy_or_numpy_dependency():
    import pathlib
    import nfsu2_world_editor
    core = pathlib.Path(nfsu2_world_editor.__file__).parent / 'core'
    for p in core.glob('*.py'):
        src = p.read_text(encoding='utf-8')
        assert 'import bpy' not in src and 'numpy' not in src, p.name
