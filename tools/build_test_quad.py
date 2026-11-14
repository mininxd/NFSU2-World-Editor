"""Variant F: original game + one 4x4 m concrete quad in section E21 (diagnostic:
does adding any object break the world?). Only E21's size in the L4RA table
may change.

    python tools/build_test_quad.py PATH/TRACKS OUTDIR
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nfsu2_world_editor.core import highway as HW, highway_mod as HM, world_edit as WE
from nfsu2_world_editor.core.road_builder import MeshPart
from nfsu2_world_editor.core.road_mod import _texture_hashes
from nfsu2_world_editor.core.stream import Region
from nfsu2_world_editor.core.tpk import bin_hash

tracks, out = sys.argv[1], sys.argv[2]
X, Y, Z = -1312.0, -665.0, 19.8      # just west of the lot road's west kerb (south junction)
region = Region.open(tracks, 'L4RA')
tex = _texture_hashes(region, HW.TEXTURES)
p = MeshPart('quad', HW.T_CONC, True)
i = p.v((X - 2, Y - 2, Z), (0, 0)); p.v((X + 2, Y - 2, Z), (1, 0)); p.v((X + 2, Y + 2, Z), (1, 1)); p.v((X - 2, Y + 2, Z), (0, 1))
p.quad(i, i + 1, i + 2, i + 3)
m = HM._mesh_from_parts([p], tex)
name = 'TRN_HW_TESTQUAD'
HM._encode_new(region, 'E21', name, m)
sp = WE.own_scenery_pack(region, 'E21')
WE.place_world_model(sp, WE.trn_template(sp), name, bin_hash(name), m.verts, len(m.indices) // 3)
sp.commit()
assert sp.validate()
stream, l4 = region.build()
HM._write(out, 'L4RA', region, stream, l4, True)
print('F written to', out)
