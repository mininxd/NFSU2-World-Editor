import os
import struct

import pytest

from nfsu2_world_editor.core.compress import (
    compress_jdlz, decompress_jdlz, decompress, CompressError, is_jdlz,
)


@pytest.mark.parametrize('src', [
    b'hello world ' * 20,
    bytes(range(256)) * 4,
    b'A' * 200 + b'B' * 80 + bytes(range(40)),
    b'\x00' * 64,
    b'x',
    os.urandom(512),
])
def test_jdlz_roundtrip(src):
    blob = compress_jdlz(src)
    assert is_jdlz(blob)
    assert decompress_jdlz(blob) == src
    _ver, raw, comp = struct.unpack_from('<III', blob, 4)
    assert raw == len(src)
    assert comp == len(blob)


def test_jdlz_rejects_garbage():
    with pytest.raises(CompressError):
        decompress_jdlz(b'not-jdlz-at-all!!!!')
    with pytest.raises(CompressError):
        decompress_jdlz(b'JDLZ' + b'\x00' * 12)


def test_decompress_passthrough():
    assert decompress(b'plain') == b'plain'
