"""Region-level read/write: ``STREAML4Rx.BUN`` plus its ``L4Rx.BUN`` section table.

:class:`Region` parses the streaming file into a :mod:`bchunk` tree, lets callers
edit chunks of any section, and writes a new pair of files. Sections stay on
their 2 KiB boundaries: the zero-padding chunk before each section is resized.
Every section keeps its original file offset whenever the padding in front of
it can absorb the size change of earlier sections (the shipped files leave
large gaps); otherwise it moves by a multiple of 2048 bytes (its bytes are
unchanged). The L4 table offsets, sizes and "size without texture pixels"
fields are updated. Unmodified input gives byte-identical output.
"""
import os
import struct

from . import bchunk
from .files import region_paths, read_bytes
from .world import SECTION_TABLE, SECTION_ENTRY_SIZE

SECTION_ALIGN = 2048


def _table_location(l4):
    """Absolute payload offset and size of the first section table chunk."""
    def rec(start, end):
        off = start
        while off + 8 <= end:
            cid, size = struct.unpack_from('<II', l4, off)
            if off + 8 + size > end:
                return None
            if cid == SECTION_TABLE:
                return off + 8, size
            if cid & 0x80000000:
                r = rec(off + 8, off + 8 + size)
                if r:
                    return r
            off += 8 + size
        return None
    return rec(0, len(l4))


class Region:
    def __init__(self, stream_buf, l4_buf, name='L4RA'):
        self.name = name
        self.source_paths = ()
        self.src = stream_buf
        self.l4 = l4_buf
        self.chunks = bchunk.parse(stream_buf)
        loc = _table_location(l4_buf)
        if loc is None:
            raise ValueError('no section table (0x00034110) in the L4 file')
        self.table_offset, tsize = loc
        self.entries = []
        for i in range(tsize // SECTION_ENTRY_SIZE):
            q = self.table_offset + i * SECTION_ENTRY_SIZE
            tag = bytes(l4_buf[q:q + 8]).split(b'\0')[0].decode('ascii', 'replace')
            num, = struct.unpack_from('<I', l4_buf, q + 8)
            off, size, geom = struct.unpack_from('<3I', l4_buf, q + 0x14)
            self.entries.append(dict(tag=tag, num=num, offset=off, size=size, geom_size=geom, index=i))
        starts = {e['offset'] for e in self.entries if e['size']}
        # zero chunks right before a section start keep 2 KiB alignment
        for prev, nxt in zip(self.chunks, self.chunks[1:]):
            if prev.id == 0 and nxt.offset in starts:
                prev.pad_mod = SECTION_ALIGN
                prev.pad_target = nxt.offset % SECTION_ALIGN

    @classmethod
    def open(cls, tracks, region='L4RA'):
        _, l4p, sp = region_paths(tracks, region)
        r = cls(read_bytes(sp), read_bytes(l4p), region.upper())
        r.source_paths = (os.path.abspath(str(sp)), os.path.abspath(str(l4p)))
        return r

    def entry(self, tag):
        for e in self.entries:
            if e['tag'].upper() == tag.upper():
                return e
        raise KeyError('unknown section %s' % tag)

    def section_chunks(self, tag):
        """Top-level chunks belonging to a section (in file order)."""
        e = self.entry(tag)
        lo, hi = e['offset'], e['offset'] + e['size']
        return [c for c in self.chunks if c.offset is not None and lo <= c.offset < hi]

    def build(self):
        """-> (stream bytes, l4 bytes) with every edit applied."""
        out = bytearray()
        newpos = {}
        chunks = self.chunks
        for i, c in enumerate(chunks):
            if c.offset is not None:
                newpos[c.offset] = len(out)
            nxt = chunks[i + 1] if i + 1 < len(chunks) else None
            if (c.id == 0 and c.pad_mod == SECTION_ALIGN and c.pad_target is not None
                    and nxt is not None and nxt.offset is not None):
                self._write_section_pad(c, nxt.offset, out)
            else:
                bchunk._write(c, len(out), out, 0)
        newpos[len(self.src)] = len(out)

        def remap(o):
            if o in newpos:
                return newpos[o]
            # inside a chunk: shift like the chunk that contains it
            best = max((k for k in newpos if k <= o), default=0)
            return newpos[best] + (o - best)

        l4 = bytearray(self.l4)
        for e in self.entries:
            if not e['size']:
                continue
            no = remap(e['offset'])
            ns = remap(e['offset'] + e['size']) - no
            ng = e['geom_size'] + (ns - e['size'])
            q = self.table_offset + e['index'] * SECTION_ENTRY_SIZE
            struct.pack_into('<3I', l4, q + 0x14, no, ns, ng)
        return out, l4

    @staticmethod
    def _write_section_pad(c, target, out):
        """Zero chunk in front of a section: land the section on its original
        offset ``target`` if possible, else on the next 2 KiB boundary."""
        pos = len(out)
        minimal = (c.pad_target - (pos + 8)) % SECTION_ALIGN
        keep = target - (pos + 8)
        n = keep if keep >= minimal else minimal
        out += struct.pack('<II', 0, n)
        out += c.data if n == len(c.data) else bytes(n)

    def write(self, out_dir, stream_name=None, l4_name=None):
        """Write ``STREAM<region>.BUN`` and ``<region>.BUN`` into ``out_dir``.
        Refuses to overwrite the input files."""
        sp = os.path.join(out_dir, stream_name or 'STREAM%s.BUN' % self.name)
        lp = os.path.join(out_dir, l4_name or '%s.BUN' % self.name)
        src_dirs = {os.path.normcase(os.path.dirname(p)) for p in self.source_paths}
        if os.path.normcase(os.path.abspath(out_dir)) in src_dirs:
            raise ValueError('refusing to overwrite the original game files in %s; '
                             'choose another output folder' % out_dir)
        stream, l4 = self.build()
        os.makedirs(out_dir, exist_ok=True)
        for p, data in ((sp, stream), (lp, l4)):
            with open(p, 'wb') as f:
                f.write(data)
        return sp, lp

    def section_of(self, offset):
        """Tag of the positioned-or-library section containing ``offset`` (the
        smallest matching entry), or None."""
        best = None
        for e in self.entries:
            if e['size'] and e['offset'] <= offset < e['offset'] + e['size']:
                if best is None or e['size'] < best['size']:
                    best = e
        return best['tag'] if best else None

    def texture_packs(self, sections=None):
        """[(TexturePack, section_tag)] for every texture pack (optionally only in
        the given section tags)."""
        from .tpk import iter_packs
        want = {s.upper() for s in sections} if sections else None
        out = []
        for c in self.chunks:
            if c.id == 0xB3300000:
                tag = self.section_of(c.offset)
                if want is not None and (tag or '').upper() not in want:
                    continue
                for p in iter_packs([c]):
                    out.append((p, tag))
        return out

    def texture_index(self, sections=None):
        """hash -> [Texture] across packs (each texture may live in several packs)."""
        idx = {}
        for p, _tag in self.texture_packs(sections):
            for t in p.textures:
                idx.setdefault(t.hash, []).append(t)
        return idx
