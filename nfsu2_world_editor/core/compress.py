"""EA JDLZ and HUFF decompressors (and a JDLZ compressor) used by car texture
packs (``CARS/*/TEXTURES.BIN``) and some GLOBAL ``.lzc`` bundles.

The formats are documented in community NFSU2 notes (OpenNFSTools for JDLZ;
the HUFF container is a 16-byte ``HUFF\\x01\\x10`` wrapper around an EA
canonical-Huffman stream). Implementations here are independent and stay in
pure Python 3.8+.
"""
import struct


class CompressError(ValueError):
    """Malformed or truncated compressed block."""


# ---------------------------------------------------------------------------
# JDLZ
# ---------------------------------------------------------------------------

JDLZ_MAGIC = b'JDLZ'
JDLZ_VERSION = 0x00001002


def is_jdlz(data):
    return len(data) >= 16 and data[:4] == JDLZ_MAGIC


def decompress_jdlz(data):
    """Decompress a JDLZ block -> uncompressed bytes.

    Header (16 bytes): ``'JDLZ'``, u32 version, u32 uncompressed, u32 compressed.
    Two 8-bit flag streams run in lockstep: a 1-bit in the first stream starts a
    back-reference (length/distance encoded in two bytes, short or long form
    chosen by the second stream); a 0-bit copies a literal.
    """
    if not is_jdlz(data):
        raise CompressError('not a JDLZ block')
    _ver, raw_size, comp_size = struct.unpack_from('<III', data, 4)
    if raw_size == 0 or comp_size > len(data):
        raise CompressError('JDLZ: bad sizes')
    src = memoryview(data)
    out = bytearray(raw_size)
    i, o, f1, f2 = 16, 0, 1, 1
    end = len(data)
    try:
        while i < end and o < raw_size:
            if f1 == 1:
                f1 = src[i] | 0x100
                i += 1
            if f2 == 1:
                f2 = src[i] | 0x100
                i += 1
            if f1 & 1:
                if f2 & 1:
                    length = ((src[i] & 0xF0) << 4 | src[i + 1]) + 3
                    dist = (src[i] & 0x0F) + 1
                else:
                    dist = ((src[i] & 0xE0) << 3 | src[i + 1]) + 17
                    length = (src[i] & 0x1F) + 3
                i += 2
                if dist > o or o + length > raw_size:
                    raise CompressError('JDLZ: bad back-reference')
                # support overlapping copies
                for k in range(length):
                    out[o + k] = out[o + k - dist]
                o += length
                f2 >>= 1
            else:
                out[o] = src[i]
                o += 1
                i += 1
            f1 >>= 1
    except IndexError:
        raise CompressError('JDLZ: truncated')
    if o != raw_size:
        raise CompressError('JDLZ: decoded %d of %d bytes' % (o, raw_size))
    return bytes(out)


def compress_jdlz(data, version=JDLZ_VERSION):
    """Compress ``data`` to a JDLZ block (valid, not necessarily unique)."""
    raw = bytes(data)
    n = len(raw)
    body = bytearray()
    f1_bits, f2_bits = [], []
    i = 0
    while i < n:
        # search a back-reference: dist 1..4096+16, length 3..
        best = None  # (length, dist, long_form)
        window = 4096 + 16
        start = max(0, i - window)
        # cap search for speed
        lo = start
        max_keep = min(18 + 31, n - i)  # short form max length is 3+31=34; long is bigger
        # try to find a decent match
        for dpos in range(i - 1, lo - 1, -1):
            if raw[dpos] != raw[i]:
                continue
            k = 0
            limit = min(n - i, i - dpos, 3 + 0xFFF)  # long form length = (hi nibble<<8 | b1)+3
            while k < limit and raw[dpos + k] == raw[i + k]:
                k += 1
            if k >= 3 and (best is None or k > best[0]):
                dist = i - dpos
                # prefer a form that can encode this dist/length
                if dist <= 16 and k <= 3 + 0xFFF:
                    best = (k, dist, True)   # long form: dist = (low nibble)+1
                if 17 <= dist <= 17 + 0x7FF and k <= 3 + 0x1F:
                    cand = (k, dist, False)  # short form
                    if best is None or cand[0] >= best[0]:
                        best = cand
                if best and best[0] >= 34:
                    break
        if best and best[0] >= 3:
            length, dist, long_form = best
            f1_bits.append(1)
            f2_bits.append(1 if long_form else 0)
            if long_form:
                # ln, t = (b1 | (b0 & 0xF0)<<4)+3, (b0 & 0x0F)+1
                enc_len = length - 3
                enc_dist = dist - 1
                b0 = ((enc_len >> 4) & 0xF0) | (enc_dist & 0x0F)
                b1 = enc_len & 0xFF
            else:
                # t, ln = (b1 | (b0 & 0xE0)<<3)+17, (b0 & 0x1F)+3
                enc_dist = dist - 17
                enc_len = length - 3
                b0 = ((enc_dist >> 3) & 0xE0) | (enc_len & 0x1F)
                b1 = enc_dist & 0xFF
            body.append(b0)
            body.append(b1)
            i += length
        else:
            f1_bits.append(0)
            body.append(raw[i])
            i += 1

    # pack flag bits into bytes (LSB first, matching the decoder's shift-right)
    def pack_flags(bits):
        out = bytearray()
        for q in range(0, len(bits), 8):
            byte = 0
            chunk = bits[q:q + 8]
            for k, bit in enumerate(chunk):
                if bit:
                    byte |= (1 << k)
            out.append(byte)
        return out

    f1 = pack_flags(f1_bits)
    # f2 only has bits for back-references; the decoder refills f2 on the same
    # cadence as f1 (every 8 f1 bits, and also whenever f2 is spent). We must
    # emit f2 bytes the way the decoder consumes them: a new f2 byte is read
    # whenever f2==1 at the start of a step, including steps that are literals.
    # Interleave while rewriting is easier: emit in decoder order.
    return _jdlz_emit(raw, version)


def _jdlz_emit(raw, version):
    """Second-pass emitter that interleaves flags the way the decoder reads them."""
    n = len(raw)
    # first, decide operations
    ops = []  # ('L', byte) or ('R', length, dist, long)
    i = 0
    while i < n:
        best = None
        lo = max(0, i - (17 + 0x7FF))
        for dpos in range(i - 1, lo - 1, -1):
            if raw[dpos] != raw[i]:
                continue
            k = 0
            limit = min(n - i, i - dpos, 3 + 0xFFF)
            while k < limit and raw[dpos + k] == raw[i + k]:
                k += 1
            if k < 3:
                continue
            dist = i - dpos
            long_ok = dist <= 16 and k <= 3 + 0xFFF
            short_ok = 17 <= dist <= 17 + 0x7FF and k <= 3 + 0x1F
            if long_ok and (best is None or k > best[0] or (k == best[0] and dist < best[1])):
                best = (k, dist, True)
            if short_ok and (best is None or k > best[0]):
                best = (k, dist, False)
            if best and best[0] >= 64:
                break
        if best:
            ops.append(('R', best[0], best[1], best[2]))
            i += best[0]
        else:
            ops.append(('L', raw[i]))
            i += 1

    payload = bytearray()
    f1 = 0
    f1_mask = 1
    f1_pos = None
    f2 = 0
    f2_mask = 1
    f2_pos = None

    def refill_f1():
        nonlocal f1, f1_mask, f1_pos
        f1_pos = len(payload)
        payload.append(0)
        f1, f1_mask = 0, 1

    def refill_f2():
        nonlocal f2, f2_mask, f2_pos
        f2_pos = len(payload)
        payload.append(0)
        f2, f2_mask = 0, 1

    # Decoder: if f1==1: read f1 byte; if f2==1: read f2 byte; then use bits.
    # Start with f1=f2=1 so the first step refills both.
    need_f1 = True
    need_f2 = True
    for op in ops:
        if need_f1:
            refill_f1()
            need_f1 = False
        if need_f2:
            refill_f2()
            need_f2 = False
        if op[0] == 'R':
            payload[f1_pos] |= f1_mask
            _length, dist, long_form = op[1], op[2], op[3]
            if long_form:
                payload[f2_pos] |= f2_mask
                enc_len = _length - 3
                enc_dist = dist - 1
                payload.append(((enc_len >> 4) & 0xF0) | (enc_dist & 0x0F))
                payload.append(enc_len & 0xFF)
            else:
                enc_dist = dist - 17
                enc_len = _length - 3
                payload.append(((enc_dist >> 3) & 0xE0) | (enc_len & 0x1F))
                payload.append(enc_dist & 0xFF)
            f2_mask <<= 1
            if f2_mask == 0x100:
                need_f2 = True
                f2_mask = 1
        else:
            payload.append(op[1])
        f1_mask <<= 1
        if f1_mask == 0x100:
            need_f1 = True
            f1_mask = 1

    header = JDLZ_MAGIC + struct.pack('<III', version, n, 16 + len(payload))
    # compressed size in the header is the full block size (header + payload),
    # matching shipped blocks (enc from the slot table includes the 16-byte header).
    return header + payload


# ---------------------------------------------------------------------------
# HUFF (EA Huffman, 'HUFF' 01 10 wrapper)
# ---------------------------------------------------------------------------

HUFF_MAGIC = b'HUFF\x01\x10'
_HUFF_KINDS = {0x30FB, 0x32FB, 0x34FB, 0xB0FB, 0xB2FB, 0xB4FB}


def is_huff(data):
    return len(data) >= 16 and data[:6] == HUFF_MAGIC


class _BitReader:
    """Big-endian bit reader (MSB of each byte first)."""

    def __init__(self, data):
        self.data = data
        self.at = 0
        self.value = 0
        self.count = 0

    def peek(self, size):
        while self.count < size and self.at < len(self.data):
            self.value = (self.value << 8) | self.data[self.at]
            self.count += 8
            self.at += 1
        if self.count < size:
            return self.value << (size - self.count)
        return self.value >> (self.count - size)

    def get(self, size):
        r = self.peek(size)
        if self.count < size:
            raise CompressError('HUFF: truncated bitstream')
        self.count -= size
        self.value &= (1 << self.count) - 1
        return r

    def number(self):
        """Variable-length non-negative integer used in the table."""
        width = 2
        while not self.get(1):
            width += 1
            if width > 30:
                raise CompressError('HUFF: unbounded number')
        return self.get(width) + (1 << width) - 4


def decompress_huff(data, max_output=1 << 22):
    """Decompress a ``HUFF\\x01\\x10`` block -> uncompressed bytes."""
    if not is_huff(data):
        raise CompressError('not a HUFF block')
    expected, packed = struct.unpack_from('<II', data, 8)
    if not 0 < expected <= max_output or not 0 < packed <= len(data) - 16:
        raise CompressError('HUFF: bad sizes')
    bits = _BitReader(data[16:16 + packed])
    kind = bits.get(16)
    if kind & 0x100:
        bits.get(32 if kind & 0x8000 else 24)
    kind &= ~0x100
    if kind not in _HUFF_KINDS:
        raise CompressError('HUFF: unknown coding 0x%04X' % kind)
    inner = bits.get(32 if kind & 0x8000 else 24)
    if inner != expected:
        raise CompressError('HUFF: inner size mismatch')
    clue = bits.get(8)
    entries, base, total = [], 0, 0
    for width in range(1, 17):
        base <<= 1
        n = bits.number()
        if total + n > 256 or base + n > (1 << width):
            raise CompressError('HUFF: bad code table')
        entries.append((width, base, n))
        base += n
        total += n
        if base == (1 << width):
            break
    else:
        raise CompressError('HUFF: incomplete code table')
    used, symbols, cur = set(), [], 255
    for _ in range(total):
        skip = bits.number() + 1
        if skip > 256 - len(used):
            raise CompressError('HUFF: bad symbol order')
        while skip:
            cur = (cur + 1) & 255
            if cur not in used:
                skip -= 1
        used.add(cur)
        symbols.append(cur)
    quick = [None] * 256
    full = {}
    k = 0
    for width, start, n in entries:
        for code in range(start, start + n):
            sym = symbols[k]
            k += 1
            full[width, code] = sym
            if width <= 8:
                step = 1 << (8 - width)
                for q in range(code << (8 - width), (code + 1) << (8 - width)):
                    quick[q] = (width, sym)
    out = bytearray()
    while True:
        e = quick[bits.peek(8)]
        if e is None:
            for width in range(9, 17):
                sym = full.get((width, bits.peek(width)))
                if sym is not None:
                    bits.get(width)
                    break
            else:
                raise CompressError('HUFF: unknown code')
        else:
            width, sym = e
            bits.get(width)
        if sym != clue:
            out.append(sym)
        else:
            run = bits.number()
            if run:
                if not out or len(out) + run > expected:
                    raise CompressError('HUFF: bad run')
                out.extend([out[-1]] * run)
            elif bits.get(1):
                break
            else:
                out.append(bits.get(8))
        if len(out) > expected:
            raise CompressError('HUFF: output overrun')
    if len(out) != expected:
        raise CompressError('HUFF: decoded %d of %d bytes' % (len(out), expected))
    if kind in (0x32FB, 0xB2FB, 0x34FB, 0xB4FB):
        v = v2 = 0
        double = kind in (0x34FB, 0xB4FB)
        for i, d in enumerate(out):
            v = (v + d) & 255
            v2 = (v2 + v) & 255
            out[i] = v2 if double else v
    return bytes(out)


def decompress(data):
    """Decompress a JDLZ or HUFF block; other input is returned unchanged."""
    if is_jdlz(data):
        return decompress_jdlz(data)
    if is_huff(data):
        return decompress_huff(data)
    return bytes(data)
