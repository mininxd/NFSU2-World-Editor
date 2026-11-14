"""End-to-end round-trip check on your own game files (headless Blender).

    blender -b --factory-startup --python tools/selftest_roundtrip.py -- \
        --tracks "C:/Games/NFSU2/TRACKS" --out OUTDIR [--section C21] [--lift 3]

1. imports the section, exports without edits -> must equal the originals byte for byte;
2. lifts the first TRN_ road mesh by ``--lift`` metres, subdivides a second mesh
   (more vertices/triangles) and adds a new object -> exports to OUTDIR/edited;
3. re-imports the edited files and checks the change, and that every other
   section's bytes are unchanged (only moved by multiples of 2 KiB).
Exit code 0 = all checks passed.
"""
import argparse
import filecmp
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bpy  # noqa: E402
import bmesh  # noqa: E402

from nfsu2_world_editor.blender import importer, exporter  # noqa: E402
from nfsu2_world_editor.core.stream import Region  # noqa: E402
from nfsu2_world_editor.core.files import region_paths  # noqa: E402


def check(cond, msg):
    print('[selftest] %s: %s' % ('PASS' if cond else 'FAIL', msg))
    return bool(cond)


def objects_by_name(tag):
    coll = bpy.data.collections['SECTION_' + tag]
    return {o.get('nfs_name', o.name): o for o in coll.objects if o.type == 'MESH'}


def main():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument('--tracks', required=True)
    ap.add_argument('--region', default='L4RA')
    ap.add_argument('--section', default='C21')
    ap.add_argument('--lift', type=float, default=3.0)
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    ok = True
    tag = a.section.upper()
    _, l4p, sp = region_paths(a.tracks, a.region)

    # 1. no-edit round trip
    importer.clear_scene()
    importer.import_world(a.tracks, a.region, [tag], instances=False)
    d0 = os.path.join(a.out, 'noedit')
    st = exporter.export_world(d0, tracks=a.tracks, region=a.region)
    ok &= check(st['edited'] == 0 and st['added'] == 0, 'no object flagged as edited after import')
    ok &= check(filecmp.cmp(st['stream'], sp, shallow=False), 'no-edit STREAM file is byte-identical')
    ok &= check(filecmp.cmp(st['l4'], l4p, shallow=False), 'no-edit L4 file is byte-identical')

    # 2. edits
    obs = objects_by_name(tag)
    roads = sorted(n for n, o in obs.items() if n.startswith('TRN_') and len(o.data.polygons) > 20)
    lifted, subdiv = roads[0], roads[1]
    o = obs[lifted]
    before = [tuple(o.matrix_world @ v.co) for v in o.data.vertices]
    o.location.z += a.lift
    bpy.context.view_layer.update()
    s = obs[subdiv]
    bm = bmesh.new()
    bm.from_mesh(s.data)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=1, use_grid_fill=True)
    bm.to_mesh(s.data)
    bm.free()
    n_tri_sub = sum(len(p.vertices) - 2 for p in s.data.polygons)
    # new object: a 40 x 12 m slab west of the lifted road, textured like it
    c = sum((o.matrix_world @ v.co for v in o.data.vertices), o.matrix_world.translation * 0) / len(o.data.vertices)
    me = bpy.data.meshes.new('NFS_NEWROAD_TEST')
    x0, y0, z0 = c.x - 60, c.y, c.z + 5
    me.from_pydata([(x0 - 20, y0 - 6, z0), (x0 + 20, y0 - 6, z0), (x0 + 20, y0 + 6, z0), (x0 - 20, y0 + 6, z0)],
                   [], [(0, 1, 2, 3)])
    me.uv_layers.new(name='UVMap')
    me.materials.append(o.data.materials[0])
    new = bpy.data.objects.new('NFS_NEWROAD_TEST', me)
    bpy.data.collections['SECTION_' + tag].objects.link(new)
    d1 = os.path.join(a.out, 'edited')
    st = exporter.export_world(d1, tracks=a.tracks, region=a.region)
    ok &= check(st['edited'] == 2 and st['added'] == 1, 'exporter wrote 2 edited + 1 new object (%s)' % st)

    # 3. re-import edited files
    importer.clear_scene()
    importer.import_world(d1, a.region, [tag], instances=False)
    obs2 = objects_by_name(tag)
    o2 = obs2[lifted]
    after = [tuple(o2.matrix_world @ v.co) for v in o2.data.vertices]
    dz = [b[2] - a_[2] for a_, b in zip(before, after)]
    ok &= check(len(after) == len(before) and all(abs(z - a.lift) < 1e-3 for z in dz),
                '%s re-imports %.2f m higher (%d vertices)' % (lifted, a.lift, len(after)))
    ok &= check(len(obs2[subdiv].data.polygons) == n_tri_sub,
                '%s has %d triangles after subdivision' % (subdiv, n_tri_sub))
    ok &= check('NFS_NEWROAD_TEST' in obs2 and len(obs2['NFS_NEWROAD_TEST'].data.polygons) == 2,
                'new object NFS_NEWROAD_TEST present with 2 triangles')
    r0 = Region.open(a.tracks, a.region)
    r1 = Region.open(d1, a.region)
    same = moved = 0
    bad = []
    for e0, e1 in zip(r0.entries, r1.entries):
        if e0['tag'].upper() == tag or not e0['size']:
            continue
        b0 = r0.src[e0['offset']:e0['offset'] + e0['size']]
        b1 = r1.src[e1['offset']:e1['offset'] + e1['size']]
        if b0 == b1 and (e1['offset'] - e0['offset']) % 2048 == 0:
            same += 1
            moved += e1['offset'] != e0['offset']
        else:
            bad.append(e0['tag'])
    ok &= check(not bad, 'all %d other sections byte-identical (%d moved by 2 KiB multiples)%s'
                % (same, moved, (' BAD: %s' % bad[:10]) if bad else ''))
    e0, e1 = r0.entry(tag), r1.entry(tag)
    print('[selftest] %s: size %d -> %d, geom %d -> %d' % (tag, e0['size'], e1['size'], e0['geom_size'], e1['geom_size']))
    print('[selftest] RESULT: %s' % ('ALL PASSED' if ok else 'FAILURES'))
    sys.exit(0 if ok else 1)


main()
