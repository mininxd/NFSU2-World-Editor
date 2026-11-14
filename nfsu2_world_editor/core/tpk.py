"""Texture packs (``0xB3300000``) in NFSU2 track streams and TPK files.

::

    0xB3300000  TexturePack
    +-- 0xB3310000  info
    |   +-- 0x33310001  pack name / path
    |   +-- 0x33310002  hash table, 8 bytes per texture (sorted)
    |   +-- 0x33310004  texture headers, 124 bytes each
    |   +-- 0x33310005  format table, 32 bytes each (D3DFORMAT @ +0x14)
    +-- 0xB3320000  data
        +-- 0x33320001  data info
        +-- 0x33320002  pixel block (128-byte aligned), mips largest first

Offsets in the header are relative to the pixel block payload (after filler).
P8 textures keep a 256-entry D3DCOLOR (B,G,R,A) palette in the same block.
Works on :mod:`bchunk` trees so a pack can be edited and written back.
"""
import struct

from . import bchunk, dxt

TPK = 0xB3300000
TPK_INFO = 0xB3310000
TPK_NAME = 0x33310001
TPK_HASHES = 0x33310002
TPK_HEADERS = 0x33310004
TPK_FORMATS = 0x33310005
TPK_DATA = 0xB3320000
TPK_PIXELS = 0x33320002

HEADER_SIZE = 124
FORMAT_SIZE = 32

USAGE_NAMES = {0: 'opaque', 1: 'cutout', 2: 'modulated'}
BLEND_NAMES = {0: 'copy', 1: 'blend', 2: 'additive'}


def bin_hash(name):
    """EA string hash used for texture / object names: h = h*33 + c, seed -1."""
    h = 0xFFFFFFFF
    for c in name.encode('ascii'):
        h = (h * 33 + c) & 0xFFFFFFFF
    return h


class Texture:
    __slots__ = ('pack', 'index', 'name', 'hash', 'offset', 'pal_offset', 'size', 'pal_size',
                 'width', 'height', 'mips', 'format', 'usage', 'blend', 'header')

    @property
    def format_name(self):
        return dxt.format_name(self.format)

    def levels(self):
        """[(w, h, offset, size)] for each mip level stored."""
        out = []
        off = self.offset
        for w, h in dxt.mip_dims(self.width, self.height, max(1, self.mips)):
            n = dxt.level_size(self.format, w, h)
            if off + n > self.offset + self.size:
                break
            out.append((w, h, off, n))
            off += n
        return out

    def raw(self):
        """All stored bytes of this texture (every mip level)."""
        return bytes(self.pack.pixels[self.offset:self.offset + self.size])

    def palette(self):
        if self.pal_size:
            return bytes(self.pack.pixels[self.pal_offset:self.pal_offset + self.pal_size])
        return None

    def decode(self, level=0, use_numpy=True):
        """-> (rgba bytes, w, h) of one mip level."""
        w, h, off, n = self.levels()[level]
        return dxt.decode(self.pack.pixels[off:off + n], self.format, w, h, self.palette(),
                          use_numpy=use_numpy), w, h

    def alpha_is_opacity(self):
        """True when the game uses this texture's alpha as transparency
        (+0x55 usage: 1 alpha test, 2 with blend 1/2 = blended/additive)."""
        return self.usage == 1 or (self.usage == 2 and self.blend in (1, 2))

    def __repr__(self):
        return '<Texture %08X %s %dx%d %s mips=%d>' % (self.hash, self.name, self.width, self.height,
                                                       self.format_name, self.mips)


class TexturePack:
    """A parsed ``0xB3300000`` node. ``textures`` are in header order."""

    def __init__(self, node):
        if node.id != TPK or node.children is None:
            raise ValueError('not a texture pack chunk')
        self.node = node
        info = node.find(TPK_INFO)
        data = node.find(TPK_DATA)
        if info is None or data is None:
            raise ValueError('texture pack without info/data')
        self.name_chunk = info.find(TPK_NAME)
        self.headers_chunk = info.find(TPK_HEADERS)
        self.formats_chunk = info.find(TPK_FORMATS)
        self.pixel_chunk = data.find(TPK_PIXELS)
        if self.headers_chunk is None or self.pixel_chunk is None:
            raise ValueError('texture pack without headers/pixels (maybe a compressed car pack)')
        nm = bytes(self.name_chunk.data) if self.name_chunk is not None else b''
        self.name = nm[4:0x20].split(b'\0')[0].decode('ascii', 'replace')
        self.path = nm[0x20:0x60].split(b'\0')[0].decode('ascii', 'replace')
        self.pixels = self.pixel_chunk.data
        self.textures = []
        hd = bytes(self.headers_chunk.data)
        fm = bytes(self.formats_chunk.data) if self.formats_chunk is not None else b''
        for i in range(len(hd) // HEADER_SIZE):
            q = i * HEADER_SIZE
            t = Texture()
            t.pack = self
            t.index = i
            t.header = hd[q:q + HEADER_SIZE]
            t.name = hd[q + 0x0C:q + 0x24].split(b'\0')[0].decode('ascii', 'replace')
            (t.hash, _cls, _r, t.offset, t.pal_offset, t.size, t.pal_size, _m0,
             t.width, t.height) = struct.unpack_from('<IIIIIIIIHH', hd, q + 0x24)
            t.mips = hd[q + 0x4E]
            t.usage = hd[q + 0x55]
            t.blend = hd[q + 0x56]
            fq = i * FORMAT_SIZE + 0x14
            t.format = struct.unpack_from('<I', fm, fq)[0] if fq + 4 <= len(fm) else dxt.FOURCC_DXT1
            self.textures.append(t)
        self.by_hash = {t.hash: t for t in self.textures}

    def replace_palette(self, tex, palette):
        if not tex.pal_size or len(palette) != tex.pal_size:
            raise ValueError('%s has no palette slot of %d bytes' % (tex.name, len(palette)))
        buf = bytearray(self.pixels)
        buf[tex.pal_offset:tex.pal_offset + tex.pal_size] = palette
        self.pixels = self.pixel_chunk.data = buf
        self.pixel_chunk.dirty = True

    def replace_pixels(self, tex, new_bytes):
        """Overwrite ``tex``'s stored bytes (same size: format, size and mips are kept)."""
        if len(new_bytes) != tex.size:
            raise ValueError('%s: new data is %d bytes, slot holds %d' % (tex.name, len(new_bytes), tex.size))
        buf = bytearray(self.pixels)
        buf[tex.offset:tex.offset + tex.size] = new_bytes
        self.pixels = self.pixel_chunk.data = buf
        self.pixel_chunk.dirty = True



PALETTE_MSE_LIMIT = 20.0   # ~35 dB: above this a P8 texture gets a new palette


def encode_texture(tex, rgba, w, h, palette_out=None):
    """Encode an RGBA image into exactly the layout of ``tex`` (format, size, mip
    count). The image must have the texture's size; mips are regenerated with a
    box filter. Returns bytes of length ``tex.size``.

    P8: the texture's palette is reused when it still fits the image; otherwise a
    new median-cut palette is built and appended to ``palette_out`` (a list) so
    the caller can store it with :meth:`TexturePack.replace_palette`."""
    if (w, h) != (tex.width, tex.height):
        raise ValueError('%s is %dx%d, image is %dx%d (resizing a texture is not supported yet)'
                         % (tex.name, tex.width, tex.height, w, h))
    pal = tex.palette()
    if pal is not None and tex.format == dxt.D3DFMT_P8 and dxt.palette_error(rgba, pal) > PALETTE_MSE_LIMIT:
        pal = dxt.build_palette(rgba)
        if palette_out is not None:
            palette_out.append(pal)
    dxt1_alpha = tex.format == dxt.FOURCC_DXT1 and tex.alpha_is_opacity()
    out = bytearray()
    img, cw, ch = rgba, w, h
    for i, (lw, lh, _off, n) in enumerate(tex.levels()):
        if i:
            img, cw, ch = dxt.downsample(img, cw, ch)
        out += dxt.encode(img, tex.format, lw, lh, palette=pal, dxt1_alpha=dxt1_alpha)
    old = tex.raw()
    out += old[len(out):]  # keep any unused tail bytes of the slot
    return bytes(out[:tex.size])


def apply_image(tex, rgba, w, h):
    """Encode ``rgba`` into ``tex`` and store it (pixels, and palette for P8)."""
    pals = []
    data = encode_texture(tex, rgba, w, h, pals)
    if pals:
        tex.pack.replace_palette(tex, pals[0])
    tex.pack.replace_pixels(tex, data)


# ---------------------------------------------------------------------------
# Compressed car / vinyl TPK (offset-slot table 0x33310003)
# ---------------------------------------------------------------------------

TPK_SLOTS = 0x33310003   # 24-byte records: key, file offset, enc, dec, header-from-end, unk
SLOT_SIZE = 24
CAR_HEADER_FROM_END_ADD = 0x88   # P = decoded_size - HeaderFromEnd + 0x88
CAR_COMP = {0x20: dxt.D3DFMT_A8R8G8B8, 0x22: dxt.FOURCC_DXT1,
            0x24: dxt.FOURCC_DXT3, 0x26: dxt.FOURCC_DXT5}
COMP_TO_CAR = {v: k for k, v in CAR_COMP.items()}


def _car_header_at(decoded, header_from_end):
    """Start of the embedded header inside a decompressed car-texture block."""
    return len(decoded) - header_from_end + CAR_HEADER_FROM_END_ADD


class CarTexturePack:
    """A ``0xB3300000`` pack whose headers are a 24-byte offset-slot table
    (``CARS/*/TEXTURES.BIN``, some GLOBAL/LOC packs). Pixel blocks at the
    stored file offsets are JDLZ or HUFF compressed; after decode, a 120-byte
    header at ``P = decoded_size - HeaderFromEnd + 0x88`` gives size and
    format. The :class:`Texture` objects are the same as in a track pack, so
    Blender materials and ``decode()`` work unchanged.
    """

    def __init__(self, node, src):
        if node.id != TPK or node.children is None:
            raise ValueError('not a texture pack chunk')
        info = node.find(TPK_INFO)
        if info is None or info.find(TPK_SLOTS) is None:
            raise ValueError('not a compressed (offset-slot) texture pack')
        self.node = node
        self.src = bytes(src)
        self.name_chunk = info.find(TPK_NAME)
        self.slots_chunk = info.find(TPK_SLOTS)
        nm = bytes(self.name_chunk.data) if self.name_chunk is not None else b''
        self.name = nm[4:0x20].split(b'\0')[0].decode('ascii', 'replace')
        self.path = nm[0x20:0x60].split(b'\0')[0].decode('ascii', 'replace')
        self.pixels = b''   # unused: each Texture keeps its own decoded block
        self.textures = []
        blobs = []
        slots = bytes(self.slots_chunk.data)
        from .compress import decompress
        for i in range(len(slots) // SLOT_SIZE):
            key, off, enc, dec, hfe, _unk = struct.unpack_from('<6I', slots, i * SLOT_SIZE)
            if off + enc > len(self.src):
                raise ValueError('car texture 0x%08X: block out of range' % key)
            raw = decompress(self.src[off:off + enc])
            if dec and len(raw) != dec:
                raise ValueError('car texture 0x%08X: decoded %d, expected %d' % (key, len(raw), dec))
            P = _car_header_at(raw, hfe)
            if not (0 <= P + 0x28 <= len(raw)):
                raise ValueError('car texture 0x%08X: bad embedded header' % key)
            hid, = struct.unpack_from('<I', raw, P)
            if hid != key:
                raise ValueError('car texture 0x%08X: header key 0x%08X' % (key, hid))
            width, height = struct.unpack_from('<HH', raw, P + 0x20)
            comp = raw[P + 0x26]
            fmt = CAR_COMP.get(comp)
            if fmt is None:
                raise ValueError('car texture 0x%08X: unknown compression 0x%02X' % (key, comp))
            # mip chain packed from the start of the decoded block up to P
            total = 0
            mips = 0
            for w, h in dxt.mip_dims(width, height, 16):
                n = dxt.level_size(fmt, w, h)
                if total + n > P:
                    break
                total += n
                mips += 1
            if mips == 0:
                raise ValueError('car texture 0x%08X: no mip levels fit' % key)
            t = Texture()
            t.pack = self
            t.index = i
            t.header = raw[P:]
            t.name = '%08X' % key
            t.hash = key
            t.offset = 0
            t.pal_offset = 0
            t.size = total
            t.pal_size = 0
            t.width = width
            t.height = height
            t.mips = mips
            t.format = fmt
            t.usage = 1 if fmt in (dxt.FOURCC_DXT3, dxt.FOURCC_DXT5) else 0
            t.blend = 1 if t.usage else 0
            self.textures.append(t)
            blobs.append(raw[:total])
        blob = bytearray()
        for tex, pix in zip(self.textures, blobs):
            tex.offset = len(blob)
            blob += pix
        self.pixels = bytes(blob)
        self.by_hash = {t.hash: t for t in self.textures}

    def __repr__(self):
        return '<CarTexturePack %s %d textures>' % (self.name, len(self.textures))



def _iter_packs_walk(chunks, src):
    for c in chunks:
        if c.id == TPK and c.children is not None:
            info = c.find(TPK_INFO)
            if info is not None and info.find(TPK_HEADERS) is not None:
                try:
                    yield TexturePack(c)
                    continue
                except ValueError:
                    pass
            if info is not None and info.find(TPK_SLOTS) is not None and src is not None:
                try:
                    yield CarTexturePack(c, src)
                    continue
                except ValueError:
                    pass
        if c.children is not None:
            yield from _iter_packs_walk(c.children, src)


def iter_packs(chunks, src=None):
    """Yield TexturePack or CarTexturePack for every pack in a chunk list.

    ``src`` is the original file bytes; it is required for compressed
    (offset-slot) packs, whose pixel blocks are addressed at file offsets.
    """
    yield from _iter_packs_walk(chunks, src)


def load_packs(path):
    """Parse every texture pack in a BUN/BIN file (track or car)."""
    from .files import read_bytes
    buf = read_bytes(path)
    return list(iter_packs(bchunk.parse(buf), buf)), buf
