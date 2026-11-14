"""Top-down map of all positioned sections' TRN_ (road/terrain) triangles, labelled by section tag."""
import sys, matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from nfsu2lib import *
l4=open('../TRACKS/L4RA.BUN','rb').read(); st=section_table(l4)
buf=open('../TRACKS/STREAML4RA.BUN','rb').read()
polys=[]; cols=[]; labels=[]
for e in st:
    if e['center']==(0.0,0.0,0.0): continue
    objs=section_objects(buf,e['offset'],e['size'])
    for o in objs:
        if not o['name'].startswith('TRN_'): continue
        V=o['verts']
        for a,b,c in o['faces']:
            polys.append([V[a][:2],V[b][:2],V[c][:2]])
            cols.append((V[a][2]+V[b][2]+V[c][2])/3)
    labels.append((e['tag'],e['center']))
fig,ax=plt.subplots(figsize=(22,22),dpi=90)
pc=PolyCollection(polys,array=__import__('numpy').array(cols),cmap='terrain',linewidths=0)
ax.add_collection(pc)
for t,(x,y,z) in labels: ax.text(x,y,t,fontsize=6,ha='center',color='red')
ax.autoscale(); ax.set_aspect('equal'); ax.set_title('L4RA (Bayview) TRN_ geometry, colour = height; red = section tag at table centre')
plt.colorbar(pc,ax=ax,shrink=0.5,label='z')
plt.savefig('../out/section_map_L4RA.png',bbox_inches='tight')
