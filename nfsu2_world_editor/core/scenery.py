"""Scenery packs (``0x80034100``): the placements the game actually draws.

A solid object in a section's solid list is only a model; the game draws what
the section's scenery pack places. Terrain (``TRN_``) models are placed with
an identity transform because their vertices are already in world space.

::

    0x80034100  scenery pack
    +-- 0x00034101  header, 60 bytes (section number at 0x0C)
    +-- 0x00034103  instances, 64 bytes (aligned)
    +-- 0x00034102  model infos, 68 bytes
    +-- 0x00034104  bounding-volume tree, 36-byte nodes
    +-- 0x00034105  (optional)
    +-- 0x00034106  per-instance 128-byte blocks (block 0 = none)

Instance: f32 min[3], max[3], u16 info, u16 flags, u32 block (1-based),
f32 translation[3], i16 rotation[9] (1.0 = 0x2000), u16 0.
Info: char name[32], u32 hash, u32 hash, u32 0, u16 tris, u16 tris, 12 zero
bytes, f32 radius, u32 (copied from a template).
Tree node: f32 min[3], max[3], u16 count, u16 child[5]; child < 0x8000 is an
instance, otherwise node ``0x10000 - child``; node 0 is the root.

Works on :mod:`bchunk` nodes so a pack can be edited and the region written
back with all alignment handled by :mod:`bchunk`.
"""
import struct

PACK = 0x80034100
HEADER = 0x00034101
INFOS = 0x00034102
INSTANCES = 0x00034103
TREE = 0x00034104
BLOCKS = 0x00034106

INFO_SIZE = 68
INST_SIZE = 64
NODE_SIZE = 36
BLOCK_SIZE = 128


class SceneryPack:
    def __init__(self, node):
        if node.id != PACK or node.children is None:
            raise ValueError('not a scenery pack node')
        self.node = node
        self.section, = struct.unpack_from('<I', bytes(node.find(HEADER).data), 0x0C)
        self.infos = self._split(INFOS, INFO_SIZE)
        self.inst = self._split(INSTANCES, INST_SIZE)
        self.blocks = self._split(BLOCKS, BLOCK_SIZE)
        self.tree = self._split(TREE, NODE_SIZE)
        self._shipped = len(self.inst)
        self.force_tree = False

    def _split(self, cid, n):
        c = self.node.find(cid)
        d = bytes(c.data) if c is not None else b''
        return [d[i:i + n] for i in range(0, len(d) - n + 1, n)]

    def instance(self, i):
        r = self.inst[i]
        return dict(bmin=struct.unpack_from('<3f', r, 0), bmax=struct.unpack_from('<3f', r, 12),
                    info=struct.unpack_from('<H', r, 24)[0], flags=struct.unpack_from('<H', r, 26)[0],
                    block=struct.unpack_from('<I', r, 28)[0], t=struct.unpack_from('<3f', r, 32))

    def info_hash(self, i):
        return struct.unpack_from('<I', self.infos[i], 0x20)[0]

    def add_model(self, template, name, model_hash, ntri, radius):
        e = bytearray(template)
        e[0:32] = name.encode('ascii')[:31].ljust(32, b'\0')
        struct.pack_into('<IIIHH', e, 0x20, model_hash, model_hash, 0, min(ntri, 0xFFFF), min(ntri, 0xFFFF))
        struct.pack_into('<f', e, 0x3C, radius)
        self.infos.append(bytes(e))
        return len(self.infos) - 1

    def add_instance(self, info, bmin, bmax, flags, block, t=(0.0, 0.0, 0.0), rot=None):
        rot = rot or (8192, 0, 0, 0, 8192, 0, 0, 0, 8192)
        if len(self.blocks) == 0:
            self.blocks.append(bytes(BLOCK_SIZE))
        r = struct.pack('<3f3fHHI3f9hH', *bmin, *bmax, info, flags, len(self.blocks), *t, *rot, 0)
        self.blocks.append(bytes(block))
        self.inst.append(r)
        return len(self.inst) - 1

    def build_tree(self):
        """Rebuild a 5-ary bounding-volume tree over every instance."""
        boxes = [(struct.unpack_from('<3f', r, 0), struct.unpack_from('<3f', r, 12)) for r in self.inst]

        def union(bs):
            return (tuple(min(b[0][k] for b in bs) for k in range(3)),
                    tuple(max(b[1][k] for b in bs) for k in range(3)))
        nodes = []
        level = [('r', i, boxes[i]) for i in range(len(boxes))]
        while len(level) > 5:
            lo, hi = union([it[2] for it in level])
            ax = max(range(3), key=lambda k: hi[k] - lo[k])
            level.sort(key=lambda it: it[2][0][ax] + it[2][1][ax])
            # as few groups as possible, sizes balanced
            ng = -(-len(level) // 5)
            per = -(-len(level) // ng)
            nxt = []
            for g in range(0, len(level), per):
                grp = level[g:g + per]
                nodes.append(grp)
                nxt.append(('n', len(nodes) - 1, union([it[2] for it in grp])))
            level = nxt
        nodes.append(level)
        order = list(range(len(nodes)))[::-1]          # root first
        newid = {old: new for new, old in enumerate(order)}
        out = []
        for old in order:
            grp = nodes[old]
            lo, hi = union([it[2] for it in grp])
            kids = [i if kind == 'r' else (0x10000 - newid[i]) & 0xFFFF for kind, i, _ in grp]
            kids += [0] * (5 - len(kids))
            out.append(struct.pack('<3f3fH5H', *lo, *hi, len(grp), *kids))
        return out

    def validate(self):
        """Every instance once, every non-root node once, boxes enclose children."""
        seen_r, seen_n = [], []
        for ni, nd in enumerate(self.tree):
            lo = struct.unpack_from('<3f', nd, 0)
            hi = struct.unpack_from('<3f', nd, 12)
            cnt, = struct.unpack_from('<H', nd, 24)
            for c in struct.unpack_from('<5H', nd, 26)[:cnt]:
                if c < 0x8000:
                    seen_r.append(c)
                    b = (struct.unpack_from('<3f', self.inst[c], 0), struct.unpack_from('<3f', self.inst[c], 12))
                else:
                    k = 0x10000 - c
                    seen_n.append(k)
                    b = (struct.unpack_from('<3f', self.tree[k], 0), struct.unpack_from('<3f', self.tree[k], 12))
                if any(b[0][j] < lo[j] - 1e-3 or b[1][j] > hi[j] + 1e-3 for j in range(3)):
                    return False
        return sorted(seen_r) == list(range(len(self.inst))) and sorted(seen_n) == list(range(1, len(self.tree)))

    def commit(self):
        """Write the lists back into the bchunk nodes (tree rebuilt if anything was added)."""
        if len(self.inst) != self._shipped or self.force_tree:
            self.tree = self.build_tree()
        for cid, lst in ((INFOS, self.infos), (INSTANCES, self.inst), (BLOCKS, self.blocks), (TREE, self.tree)):
            c = self.node.find(cid)
            new = b''.join(lst)
            if bytes(c.data) != new:
                c.data = new
                c.dirty = True
        self.node.dirty = True
