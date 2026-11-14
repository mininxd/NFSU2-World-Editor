"""Layout rules the game depends on (header alignment, filler the loaders skip,
solid header size, top-level pack alignment). Synthetic tests always run; the
check on the shipped files runs only with ``NFSU2_TRACKS``."""
import os
import struct
from pathlib import Path

import pytest

from nfsu2_world_editor.core import layout, stream
from nfsu2_world_editor.core.meshcodec import NAME_OFFSET, NAME_LEN

TRACKS = os.environ.get('NFSU2_TRACKS')
needs_tracks = pytest.mark.skipif(not TRACKS or not Path(TRACKS).is_dir(), reason='NFSU2_TRACKS not set')


def _chunk(cid, payload):
    return struct.pack('<II', cid, len(payload)) + payload


def test_game_skip_matches_loader_rounding():
    # SPEED2.EXE loaders take (header + 0x17) & ~0xF as the payload address
    assert layout.game_skip(8) == 0
    assert layout.game_skip(0) == 8
    assert layout.game_skip(4) == 4
    assert layout.game_skip(12) == 12


def test_misplaced_0x135001_is_reported():
    good = bytes(8) + _chunk(0x135001, bytes(32))           # header at 8: payload on 16, no filler
    assert not [v for v in layout.violations(good, 8) if 'filler' in v]
    bad = bytes(4) + _chunk(0x135001, bytes(32))            # header at 4: the game reads 4 bytes late
    assert any('game skips 4' in m for m in layout.violations(bad, 4))


def test_filler_a_block_too_long_is_reported():
    # header at 0 mod 16: the game skips 8 bytes; 24 bytes of filler means it reads filler
    bad = bytes(16) + _chunk(0x135002, b'\x11' * 24 + bytes(16))
    assert any('24 filler bytes but the game skips 8' in m for m in layout.violations(bad, 16))
    ok = bytes(16) + _chunk(0x135002, b'\x11' * 8 + bytes(16))
    assert not [m for m in layout.violations(ok, 16) if 'filler' in m]


def test_short_solid_header_is_reported():
    buf = bytes(8) + _chunk(0x134011, bytes(180))
    assert any('180 bytes' in m for m in layout.violations(buf, 8))
    buf = bytes(8) + _chunk(0x134011, bytes(192))
    assert not [m for m in layout.violations(buf, 8) if 'bytes (shipped' in m]


def test_misaligned_top_level_pack_is_reported():
    buf = _chunk(0x1111, bytes(84)) + _chunk(0x80135000, bytes(8) + _chunk(0x135001, bytes(32)))
    assert any('128-byte boundary' in m for m in layout.violations(buf))


def test_region_build_realigns_top_level_pack():
    pack = _chunk(0x80135000, _chunk(0x135001, bytes(32)))
    src = _chunk(0x1111, bytes(120)) + pack
    r = stream.Region.__new__(stream.Region)
    from nfsu2_world_editor.core import bchunk
    r.src, r.l4, r.entries, r.table_offset = src, b'', [], 0
    r.chunks = bchunk.parse(src)
    r.chunks[0].data = bytes(84)                              # shrink the chunk in front by 36
    r.chunks[0].dirty = True
    out, _ = r.build()
    p = bytes(out).find(struct.pack('<I', 0x80135000))
    assert p % 128 == 0
    assert layout.violations(bytes(out)) == []


def test_name_field_ends_at_header_end():
    assert NAME_OFFSET + NAME_LEN == layout.HEADER_SIZE


@needs_tracks
def test_shipped_region_has_no_violations():
    r = stream.Region.open(TRACKS)
    assert layout.section_violations(r.src, r.entries) == {}
