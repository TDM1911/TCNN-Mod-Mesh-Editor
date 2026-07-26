# Skeletons

Each `skeletons/<name>/` holds the spine data the editor works against — all **extracted
from your own copy of the game**, none of it is committed here (proprietary game data):

```
skeletons/<name>/
  positions.json     # attachments: verts, uvs, tris, slot   (posdump)
  meta.json          # bones (setup a,b,c,d,x,y,len,parent) + slots   (posdump)
  weights.json       # per-vertex bone weights   (posdump)
  atlas.atlas        # the libgdx atlas
  anim_idle.json     # {dur, frames:[{bones:[{a,b,c,d,x,y}]}]}  (posdump <atlas> <skel> out <animName> <N>, keep only "bones")
  pages/             # atlas page PNGs
```

Generate `positions/meta/weights` with the `posdump` tool:
`posdump <atlas>.atlas <skel>.skel positions.json`
List animation names with: `posdump <atlas> <skel> out list 1`
