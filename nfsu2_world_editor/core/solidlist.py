"""Adding objects to a section's solid list (``0x80134000``)."""
import struct

from .meshcodec import SOLID_OBJECT, SOLID_HEADER, clone_object, set_name, decode
from .tpk import bin_hash

SOLID_LIST = 0x80134000
SOLID_LIST_HEADER = 0x80134001
SOLID_LIST_INFO = 0x00134002
SOLID_LIST_PAIRS = 0x00134003


def object_hash(obj):
    return struct.unpack_from('<I', obj.find(SOLID_HEADER).data, 0x10)[0]


def region_hashes(region):
    hs = set()
    for c in region.chunks:
        if c.id == SOLID_LIST:
            for o in c.find_all(SOLID_OBJECT, recursive=False):
                h = o.find(SOLID_HEADER)
                if h is not None:
                    hs.add(object_hash(o))
    return hs


def add_object(region, tag, name, template=None):
    """Clone a template object into the first solid list of section ``tag``,
    rename it (hash = bin_hash(name)) and register it in the list header.
    Returns the new node (fill it with :func:`meshcodec.encode`)."""
    lists = [c for c in region.section_chunks(tag) if c.id == SOLID_LIST]
    if not lists:
        raise ValueError('section %s has no solid list to add objects to' % tag)
    sl = lists[0]
    h = bin_hash(name)
    if h in region_hashes(region):
        raise ValueError('an object named/hashed like %r (%08X) already exists in the region' % (name, h))
    if template is None:
        for o in sl.children:
            if o.id == SOLID_OBJECT and decode(o) is not None and decode(o).stride == 24:
                template = o
                break
    if template is None:
        raise ValueError('section %s has no 24-byte-stride object to use as a template' % tag)
    new = clone_object(template)
    set_name(new, name, h)
    sl.children.append(new)
    sl.dirty = True
    hdr = sl.find(SOLID_LIST_HEADER)
    if hdr is not None:
        info = hdr.find(SOLID_LIST_INFO)
        if info is not None:
            d = bytearray(info.data)
            n, = struct.unpack_from('<I', d, 0x0C)
            struct.pack_into('<I', d, 0x0C, n + 1)
            info.data = bytes(d)
            info.dirty = True
        pairs = hdr.find(SOLID_LIST_PAIRS)
        if pairs is not None:
            raw = bytes(pairs.data)
            ps = [struct.unpack_from('<II', raw, q) for q in range(0, len(raw) - 7, 8)]
            ps.append((h, 0))
            ps.sort(key=lambda p: p[0])
            pairs.data = b''.join(struct.pack('<II', *p) for p in ps)
            pairs.dirty = True
        hdr.dirty = True
    return new
