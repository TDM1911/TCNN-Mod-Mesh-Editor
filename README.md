# Outfit Mesh Editor
<img width="1920" height="919" alt="image" src="https://github.com/user-attachments/assets/dfe3efe9-1078-4df6-bcc2-0a3280ca7af3" />
<img width="1920" height="919" alt="image" src="https://github.com/user-attachments/assets/3a693df7-8a34-47d2-913e-9c13f5387042" />

A local web tool for building custom Spine outfit meshes for **Third Crisis: Neon Nights**,
without touching code. Wraps the proven pipeline (auto-pick donor mesh, coverage check,
dilate/expand, per-vertex edit, single-page pack + export) behind a canvas UI.

## Run
```
start.bat        (Windows: installs deps + launches)
```
or manually:
```
pip install -r requirements.txt
python server.py
```
Then open http://localhost:5000

## How to edit the mesh
1. Load skeleton (e.g. `chibi` for the overworld model). Slots list appears.
2. Draw each outfit part on the matching full PNG canvas (full-canvas PNG per part) or import existed data by choosing single.json + atlas page.
3. Pick a slot → drop its part PNG. Or the tool will auto-picks the best donor mesh and shows on screen.
4. Auto-expand (sliders) and/or **drag vertices** on the canvas until it cover the entire artwork. Be clever about it though, too much alter might make things look weird.
5. Run diff animation to confirm the movement is correct.
6. Export to mod → writes `single.json` + `racer_page.png` into the mod's `assets/racer` folder. Restart the game and run `outfit.racer`.

## Skeletons
Each `skeletons/<name>/` holds `positions.json`, `meta.json`, `weights.json`, `atlas.atlas`,
`anim_idle.json`, and a `pages/` folder of atlas PNGs. **This data is NOT included in the repo**
— it's extracted from your own copy of the game (proprietary). See [skeletons/README.md](skeletons/README.md)
for how to generate it with `posdump`. Drop a skeleton folder in and it appears in the dropdown.

## Notes
- Bone-local coords are exported **raw**; the mod applies the x0.01 import-scale at load, so
  the same export works for both portrait and overworld skeletons.
- Part PNGs must be full-canvas (same size as the skeleton's DRAW_TEMPLATE).
