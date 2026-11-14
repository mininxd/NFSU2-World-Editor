#!/usr/bin/env python3
"""NFSU2 BUN/BIN chunk walker.
Chunk = uint32 id, uint32 size, payload[size]. If id & 0x80000000 the payload is
a list of child chunks. Usage: chunkdump.py FILE [--tree [maxdepth]] [--hist]
"""
import struct, sys, collections

def walk(buf, start, end, depth=0, path=()):
    off = start
    while off + 8 <= end:
        cid, size = struct.unpack_from('<II', buf, off)
        data = off + 8
        if data + size > end:
            yield ('BAD', off, cid, size, depth, path); return
        yield ('CHUNK', off, cid, size, depth, path)
        if cid & 0x80000000 and size >= 8:
            yield from walk(buf, data, data + size, depth + 1, path + (cid,))
        off = data + size

def main():
    fn = sys.argv[1]
    buf = open(fn, 'rb').read()
    tree = '--tree' in sys.argv
    maxd = 99
    if tree:
        i = sys.argv.index('--tree')
        if i + 1 < len(sys.argv) and sys.argv[i+1].isdigit(): maxd = int(sys.argv[i+1])
    hist = collections.Counter(); bytes_ = collections.Counter(); bad = 0
    for kind, off, cid, size, depth, path in walk(buf, 0, len(buf)):
        if kind == 'BAD':
            bad += 1; print(f'!! bad chunk @0x{off:X} id=0x{cid:08X} size=0x{size:X}'); continue
        hist[cid] += 1; bytes_[cid] += size
        if tree and depth <= maxd:
            print(f'{"  "*depth}0x{cid:08X} @0x{off:08X} size=0x{size:X}')
    print(f'# {fn}: {len(buf)} bytes, {sum(hist.values())} chunks, {bad} bad')
    print('# id          count       bytes')
    for cid, n in sorted(hist.items(), key=lambda x: -x[1]):
        print(f'0x{cid:08X} {n:8d} {bytes_[cid]:12d}')

if __name__ == '__main__':
    main()
