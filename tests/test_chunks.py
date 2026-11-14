import struct

from nfsu2_world_editor.core.chunks import iter_chunks, walk, find, payload_start
from nfsu2_world_editor.core.writer import ChunkWriter


def make_tree():
    w = ChunkWriter()
    w.leaf(0x00000001, b'abcd')
    w.begin(0x80000010)
    w.leaf(0x00000002, b'xy')
    w.begin(0x80000020)
    w.leaf(0x00000002, b'z' * 5)
    w.end()
    w.end()
    w.leaf(0x00000003, b'')
    return w.getvalue()


def test_header_is_little_endian():
    buf = make_tree()
    assert buf[:8] == b'\x01\x00\x00\x00\x04\x00\x00\x00'


def test_iter_chunks_top_level():
    buf = make_tree()
    ids = [(cid, s) for cid, _, s in iter_chunks(buf)]
    assert ids == [(0x1, 4), (0x80000010, 8 + 2 + 8 + 8 + 5), (0x3, 0)]
    cid, d, s = list(iter_chunks(buf))[0]
    assert buf[d:d + s] == b'abcd'


def test_walk_depths_and_paths():
    rows = [(k, cid, depth, path) for k, _, cid, _, depth, path in walk(make_tree())]
    assert rows == [
        ('CHUNK', 0x1, 0, ()),
        ('CHUNK', 0x80000010, 0, ()),
        ('CHUNK', 0x2, 1, (0x80000010,)),
        ('CHUNK', 0x80000020, 1, (0x80000010,)),
        ('CHUNK', 0x2, 2, (0x80000010, 0x80000020)),
        ('CHUNK', 0x3, 0, ()),
    ]


def test_walk_reports_overrun():
    buf = make_tree() + struct.pack('<II', 0x7, 1000) + b'short'
    bad = [r for r in walk(buf) if r[0] == 'BAD']
    assert len(bad) == 1 and bad[0][2] == 0x7
    # iter_chunks stops quietly at the same place
    assert [c for c, _, _ in iter_chunks(buf)][-1] == 0x3


def test_find_descends_containers():
    buf = make_tree()
    found = [bytes(buf[d:d + s]) for d, s in find(buf, 0, len(buf), 0x2)]
    assert found == [b'xy', b'zzzzz']


def test_payload_start_skips_only_real_filler():
    for base in (0, 3, 100):
        w = ChunkWriter(base=base)
        w.leaf(0x00134B01, b'\xAA' * 16, align=True)  # 128-byte alignment
        buf = bytes(base) + w.getvalue()
        cid, d, s = next(iter_chunks(buf, base))
        p = payload_start(buf, cid, d, s)
        assert p % 128 == 0 and buf[p:p + 16] == b'\xAA' * 16
    # misaligned data without 0x11 filler is returned unchanged
    buf = struct.pack('<II', 0x00134B01, 4) + b'\x00\x01\x02\x03'
    assert payload_start(buf, 0x00134B01, 8, 4) == 8
