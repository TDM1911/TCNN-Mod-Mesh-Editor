"""
Core outfit-mesh pipeline, generalized from the proven build_single_page.py.

A Skeleton loads the dumped spine data (positions/meta/weights + atlas + pages) and
provides the exact same canvas transform the DRAW_TEMPLATE.psd uses, so a part PNG drawn
on the template lines up 1:1 here. Everything downstream — donor auto-pick, coverage,
dilation, weighted-vertex baking, single-page packing/export — mirrors the pipeline that
now renders correctly in-game (including the x0.01 import-scale correction).
"""
import json, os, numpy as np, cv2
from collections import defaultdict

CANVAS_MARGIN = 30
# The live skeleton is import-scale 0.01 vs these dumped raw coords. We export RAW bone-local
# coords (like build_single_page.py); the mod's RacerOutfit.Load() applies the x0.01 at load time.
# Keep it in ONE place (the mod) so exports stay consistent — do NOT also scale here.


class Skeleton:
    def __init__(self, folder):
        self.folder = folder
        self.data = json.load(open(os.path.join(folder, "positions.json")))
        self.meta = json.load(open(os.path.join(folder, "meta.json")))
        self.weights = json.load(open(os.path.join(folder, "weights.json")))
        self.bones = self.meta["bones"]                       # index-aligned
        self.slot_by_index = {s["index"]: s for s in self.meta["slots"]}
        self.slot_by_name = {s["name"]: s for s in self.meta["slots"]}
        self._wl = {(m["skin"], m["slot"], m["name"]): m["vw"] for m in self.weights["meshes"]}
        self._parse_atlas(os.path.join(folder, "atlas.atlas"))
        self._load_pages(os.path.join(folder, "pages"))
        self._build_canvas()
        self._index_meshes()
        self.anim = None
        ap = os.path.join(folder, "anim_idle.json")
        if os.path.exists(ap): self.anim = json.load(open(ap))

    def deform_frames(self, canvas_verts, vw):
        """Re-pose a weighted mesh through the idle animation. Returns per-frame canvas verts, so the
        editor can play the idle and reveal deformation problems (the same LBS the game runs)."""
        if not self.anim or vw is None: return None
        # bone-local coords from the SETUP transforms (meta.json), per influence
        infl = []
        for i in range(len(canvas_verts)):
            wx, wy = self.from_canvas(*canvas_verts[i])
            lst = []
            for b, w in vw[i]:
                b = int(b); bm = self.bones[b]
                det = bm["a"] * bm["d"] - bm["b"] * bm["c"]
                if abs(det) < 1e-9: continue
                ax = wx - bm["x"]; ay = wy - bm["y"]
                lx = (bm["d"] * ax - bm["b"] * ay) / det; ly = (bm["a"] * ay - bm["c"] * ax) / det
                lst.append((b, float(w), lx, ly))
            infl.append(lst)
        frames = []
        for fr in self.anim["frames"]:
            fb = fr["bones"]
            pts = []
            for lst in infl:
                X = Y = 0.0
                for (b, w, lx, ly) in lst:
                    bb = fb[b] if b < len(fb) else None
                    if bb is None: continue
                    X += (bb["a"] * lx + bb["b"] * ly + bb["x"]) * w
                    Y += (bb["c"] * lx + bb["d"] * ly + bb["y"]) * w
                pts.append(list(self.to_canvas(X, Y)))
            frames.append(pts)
        return frames

    # ---- atlas + pages ----
    def _parse_atlas(self, path):
        lines = [l.rstrip("\n") for l in open(path, encoding="utf-8")]
        self.region_page, self.declared = {}, {}
        cur, i, n = None, 0, len(lines)
        while i < n:
            l = lines[i]
            if l.endswith(".png"):
                cur = l[:-4]; i += 1
                while i < n and (":" in lines[i]) and not lines[i].endswith(".png") and lines[i].strip():
                    if lines[i].startswith("size:"):
                        w, h = lines[i][5:].split(","); self.declared[cur] = (int(w), int(h))
                    i += 1
                continue
            if l.strip() and ":" not in l:
                self.region_page[l.strip()] = cur; i += 1
                while i < n and (":" in lines[i]) and lines[i].strip():
                    i += 1
                continue
            i += 1

    def _load_pages(self, pdir):
        self.pages = {}
        for pname in self.declared:
            fp = os.path.join(pdir, pname + ".png")
            if os.path.exists(fp):
                from PIL import Image
                self.pages[pname] = np.array(Image.open(fp).convert("RGBA"))
        self.pscale = {p: (self.pages[p].shape[1] / self.declared[p][0],
                           self.pages[p].shape[0] / self.declared[p][1]) for p in self.pages}

    # ---- canvas transform (identical to the DRAW_TEMPLATE builder) ----
    def _build_canvas(self):
        xs, ys = [], []
        for a in self.data:
            if a["skin"] == "naked" or a["skin"].startswith("outfits/"):
                v = a["verts"]; xs += v[0::2]; ys += v[1::2]
        self.minx, self.maxx = min(xs), max(xs)
        self.miny, self.maxy = min(ys), max(ys)
        self.W = int(self.maxx - self.minx + 2 * CANVAS_MARGIN)
        self.H = int(self.maxy - self.miny + 2 * CANVAS_MARGIN)

    def to_canvas(self, x, y):
        return (x - self.minx + CANVAS_MARGIN, (self.maxy - y) + CANVAS_MARGIN)

    def from_canvas(self, cx, cy):
        return (cx - CANVAS_MARGIN + self.minx, self.maxy + CANVAS_MARGIN - cy)

    # ---- mesh catalog ----
    def _index_meshes(self):
        self.by_slot = defaultdict(list)
        for a in self.data:
            if a["type"] == "mesh" and (a["skin"], a["slot"], a["name"]) in self._wl:
                self.by_slot[a["slot"]].append(a)

    def slots_with_meshes(self):
        out = []
        for idx in sorted(self.by_slot):
            s = self.slot_by_index.get(idx)
            if s: out.append({"index": idx, "name": s["name"], "bone": s.get("bone"),
                              "candidates": len(self.by_slot[idx])})
        return out

    def candidates(self, slot_index):
        """Distinct donor meshes for a slot (dedup by name+vertcount)."""
        seen, out = set(), []
        for a in self.by_slot.get(slot_index, []):
            sig = (a["name"], len(a["verts"]))
            if sig in seen: continue
            seen.add(sig); out.append(a)
        return out

    def mesh_canvas_verts(self, a):
        v = a["verts"]; n = len(v) // 2
        return np.array([self.to_canvas(v[2 * i], v[2 * i + 1]) for i in range(n)], float)

    # ---- reference art warped into canvas (for display) ----
    def warp_ref(self, a):
        buf = np.zeros((self.H, self.W, 4), np.uint8)
        page = self.pages.get(self.region_page.get(a["region"]))
        if page is None: return buf
        sx, sy = self.pscale.get(self.region_page.get(a["region"]), (1.0, 1.0))
        v, uvs, tris = a["verts"], a["uvs"], a["tris"]
        for t in range(0, len(tris), 3):
            ids = (tris[t], tris[t + 1], tris[t + 2])
            src = np.float32([[uvs[2 * i] * sx, uvs[2 * i + 1] * sy] for i in ids])
            dst = np.float32([self.to_canvas(v[2 * i], v[2 * i + 1]) for i in ids])
            x0 = max(int(np.floor(dst[:, 0].min())), 0); y0 = max(int(np.floor(dst[:, 1].min())), 0)
            x1 = min(int(np.ceil(dst[:, 0].max())), self.W); y1 = min(int(np.ceil(dst[:, 1].max())), self.H)
            if x1 <= x0 or y1 <= y0: continue
            dl = dst - [x0, y0]
            M = cv2.getAffineTransform(src, dl.astype(np.float32))
            wrp = cv2.warpAffine(page, M, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
            mask = np.zeros((y1 - y0, x1 - x0), np.uint8); cv2.fillConvexPoly(mask, dl.astype(np.int32), 255)
            m = (mask > 0) & (wrp[:, :, 3] > 0); buf[y0:y1, x0:x1][m] = wrp[m]
        return buf

    def weight_of(self, a):
        return self._wl.get((a["skin"], a["slot"], a["name"]))

    # ---- import an exported outfit back into editable form ----
    def reconstruct_canvas_verts(self, mesh):
        """single.json mesh (weighted bone-local verts) -> setup-pose canvas verts."""
        bones = mesh["bones"]; verts = mesh["vertices"]; n = mesh["worldVerticesLength"] // 2
        bi = vi = 0; out = []
        for _ in range(n):
            cnt = bones[bi]; bi += 1; X = Y = 0.0
            for _k in range(cnt):
                b = bones[bi]; bi += 1
                lx = verts[vi]; ly = verts[vi + 1]; w = verts[vi + 2]; vi += 3
                bm = self.bones[b]
                X += (bm["a"] * lx + bm["b"] * ly + bm["x"]) * w
                Y += (bm["c"] * lx + bm["d"] * ly + bm["y"]) * w
            out.append(list(self.to_canvas(X, Y)))
        return out

    def warp_page_to_canvas(self, page, region_uvs, cverts, tris, pageW, pageH):
        """Rebuild the full-canvas part art from a packed page + regionUVs (reverse of export)."""
        buf = np.zeros((self.H, self.W, 4), np.uint8)
        for t in range(0, len(tris), 3):
            ids = (tris[t], tris[t + 1], tris[t + 2])
            src = np.float32([[region_uvs[2 * i] * pageW, (1.0 - region_uvs[2 * i + 1]) * pageH] for i in ids])
            dst = np.float32([cverts[i] for i in ids])
            x0 = max(int(np.floor(dst[:, 0].min())), 0); y0 = max(int(np.floor(dst[:, 1].min())), 0)
            x1 = min(int(np.ceil(dst[:, 0].max())), self.W); y1 = min(int(np.ceil(dst[:, 1].max())), self.H)
            if x1 <= x0 or y1 <= y0: continue
            dl = dst - [x0, y0]
            M = cv2.getAffineTransform(src, dl.astype(np.float32))
            wrp = cv2.warpAffine(page, M, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
            mask = np.zeros((y1 - y0, x1 - x0), np.uint8); cv2.fillConvexPoly(mask, dl.astype(np.int32), 255)
            mm = (mask > 0) & (wrp[:, :, 3] > 0); buf[y0:y1, x0:x1][mm] = wrp[mm]
        # page is premultiplied — recover straight alpha for display/editing
        a = buf[:, :, 3:4].astype(np.float32) / 255.0; a[a == 0] = 1
        buf[:, :, :3] = np.clip(buf[:, :, :3] / a, 0, 255).astype(np.uint8)
        return buf

    def base_backdrop(self):
        """Skeleton-only orientation backdrop (no body composite). Draws real limb/deform bones as
        shafts and only short parent links, so control bones don't spray lines across the canvas."""
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0)); dr = ImageDraw.Draw(img)
        maxlen = (self.W + self.H) * 0.12          # skip anything longer than a plausible limb
        w = max(1, self.W // 320); rr = max(2, self.W // 340)
        for b in self.bones:                        # short parent links only
            p = b.get("parent", -1)
            if p is None or p < 0: continue
            par = self.bones[p]
            x0, y0 = self.to_canvas(b["x"], b["y"]); x1, y1 = self.to_canvas(par["x"], par["y"])
            if abs(x1 - x0) + abs(y1 - y0) > maxlen: continue
            dr.line([x0, y0, x1, y1], fill=(0, 200, 255, 110), width=w)
        for b in self.bones:                        # real deform bones as shafts
            L = b.get("len", 0)
            if L <= 2: continue
            ox, oy = self.to_canvas(b["x"], b["y"]); tx, ty = self.to_canvas(b["tipx"], b["tipy"])
            if abs(tx - ox) + abs(ty - oy) > maxlen: continue
            dr.line([ox, oy, tx, ty], fill=(120, 255, 255, 190), width=w)
            dr.ellipse([ox - rr, oy - rr, ox + rr, oy + rr], fill=(255, 90, 90, 210))
        return np.array(img)


# ---------- part image (drawn on the template canvas) ----------
def part_mask(part_rgba):
    m = (part_rgba[:, :, 3] > 0).astype(np.uint8)
    if m.sum() == 0: return m
    ncc, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    for c in range(1, ncc):
        if stats[c, cv2.CC_STAT_AREA] < 400:
            m[lab == c] = 0
    return m


# ---------- rasterize a mesh (canvas verts + tris) to a filled mask ----------
def rasterize(verts_canvas, tris, W, H):
    mm = np.zeros((H, W), np.uint8)
    for t in range(0, len(tris), 3):
        poly = np.array([[int(verts_canvas[tris[t + k]][0]), int(verts_canvas[tris[t + k]][1])]
                         for k in range(3)], np.int32)
        cv2.fillConvexPoly(mm, poly, 1)
    return mm


# ---------- auto-pick best donor mesh by IoU with the part mask ----------
def auto_pick(skel, slot_index, pmask):
    best, mtot = None, int(pmask.sum())
    for a in skel.candidates(slot_index):
        pts = skel.mesh_canvas_verts(a)
        mm = rasterize(pts, a["tris"], skel.W, skel.H)
        inter = int(((mm > 0) & (pmask > 0)).sum())
        if inter == 0: continue
        ma = int((mm > 0).sum())
        iou = inter / (ma + mtot - inter + 1e-6)
        if best is None or iou > best[0]: best = (iou, a)
    return best[1] if best else None


# ---------- coverage metrics: how much art is covered, how much mesh spills ----------
def coverage(skel, verts_canvas, tris, pmask):
    mm = rasterize(verts_canvas, tris, skel.W, skel.H)
    art = int(pmask.sum()); mesh = int((mm > 0).sum())
    inter = int(((mm > 0) & (pmask > 0)).sum())
    covered = inter / art if art else 0.0            # fraction of art the mesh covers
    spill = (mesh - inter) / mesh if mesh else 0.0   # fraction of mesh outside the art
    return {"covered": covered, "spill": spill, "artpx": art, "meshpx": mesh}


# ---------- boundary + dilation (overhang push + directional extend + gapclose) ----------
def _boundary(tris, npts):
    ec = defaultdict(int)
    for t in range(0, len(tris), 3):
        for x, y in ((tris[t], tris[t + 1]), (tris[t + 1], tris[t + 2]), (tris[t + 2], tris[t])):
            ec[(min(x, y), max(x, y))] += 1
    ba = defaultdict(list)
    for (x, y), c in ec.items():
        if c == 1: ba[x].append(y); ba[y].append(x)
    return ba

def _samp(mask, p, W, H):
    x = int(round(p[0])); y = int(round(p[1]))
    return mask[y, x] if 0 <= y < H and 0 <= x < W else 0

def dilate(skel, pts0, tris, pmask, margin=15, base=11, extend_up=False, extend_down=False,
           gap_r=45, gap_iters=6):
    W, H = skel.W, skel.H
    pts0 = np.array(pts0, float)
    ba = _boundary(tris, len(pts0)); bl = list(ba)
    mm = rasterize(pts0, tris, W, H)
    nrm, disp = {}, {}
    for i in bl:
        nb = ba[i]
        if len(nb) < 2: nrm[i] = np.zeros(2); disp[i] = 0.0; continue
        tg = pts0[nb[1]] - pts0[nb[0]]; L = np.hypot(*tg) or 1; tg /= L
        nr = np.array([-tg[1], tg[0]])
        if _samp(mm, pts0[i] + nr * 3, W, H) > _samp(mm, pts0[i] - nr * 3, W, H): nr = -nr
        nrm[i] = nr
        if _samp(pmask, pts0[i] + nr * 3, W, H) == 0: disp[i] = base; continue
        last = 0
        for t in range(1, 70):
            if _samp(pmask, pts0[i] + nr * t, W, H) > 0: last = t
            elif last > 0 and t - last > 4: break
        disp[i] = last + margin
    for _ in range(3):
        nd = dict(disp)
        for i in bl:
            nb = [j for j in ba[i] if j in disp]
            if nb: nd[i] = 0.5 * disp[i] + 0.5 * np.mean([disp[j] for j in nb])
        disp = nd
    pts1 = pts0.copy()
    for i in bl: pts1[i] = pts0[i] + nrm[i] * disp[i]
    if extend_up:
        for i in bl:
            if nrm[i][1] < -0.25:
                last = 0
                for t in range(1, 180):
                    if _samp(pmask, pts0[i] + np.array([0., -t]), W, H) > 0: last = t
                    elif last > 0 and t - last > 6: break
                if last > 0: pts1[i][1] = min(pts1[i][1], pts0[i][1] - (last + 8))
    if extend_down:
        for i in bl:
            if nrm[i][1] > 0.25:
                last = 0
                for t in range(1, 220):
                    if _samp(pmask, pts0[i] + np.array([0., t]), W, H) > 0: last = t
                    elif last > 0 and t - last > 6: break
                if last > 0: pts1[i][1] = max(pts1[i][1], pts0[i][1] + (last + 8))
    # gapclose: pull boundary into remaining uncovered art
    for _ in range(gap_iters):
        dm = rasterize(pts1, tris, W, H); unc = pmask & (1 - (dm > 0))
        if int(unc.sum()) < 15: break
        uy, ux = np.where(unc > 0); uc = np.stack([ux, uy], 1).astype(float)
        for i in bl:
            d = uc - pts1[i]; dist = np.hypot(d[:, 0], d[:, 1]); near = dist < gap_r
            if near.sum() == 0: continue
            far = uc[near][np.argmax(dist[near])]; pts1[i] = pts1[i] + (far - pts1[i]) * 0.7
    return pts1


# ---------- bake weighted geometry (canvas verts -> Bones[]/Vertices[] in bone-local, x IMPORT_SCALE) ----------
def bake_weighted(skel, a, verts_canvas):
    vw = skel.weight_of(a)
    n = len(verts_canvas)
    mbones, mverts = [], []
    for i in range(n):
        wx, wy = skel.from_canvas(*verts_canvas[i])
        mbones.append(len(vw[i]))
        for b, w in vw[i]:
            b = int(b); bm = skel.bones[b]
            det = bm["a"] * bm["d"] - bm["b"] * bm["c"]
            ax = wx - bm["x"]; ay = wy - bm["y"]
            lx = (bm["d"] * ax - bm["b"] * ay) / det
            ly = (bm["a"] * ay - bm["c"] * ax) / det
            mbones.append(b)
            # raw bone-local coords; the mod applies x0.01 at load.
            mverts += [lx, ly, float(w)]
    return mbones, mverts


# ---------- crop premultiplied patch for a mesh footprint ----------
def _premult(part_rgba):
    pm = part_rgba.astype(np.float32); pm[:, :, :3] *= (pm[:, :, 3:4] / 255.0)
    return pm.astype(np.uint8)


def pack_and_export(skel, items, part_images, out_dir, page_w=2048, pad=6):
    """items: [{slot, slotIndex, name, skin, verts_canvas(list), tris}]
       part_images: {slot: rgba array (canvas-sized)}  -> premultiplied patches
       Writes racer_page.png + single.json (+ returns summary)."""
    os.makedirs(out_dir, exist_ok=True)
    meshes, patches = [], []
    for it in items:
        pts1 = np.array(it["verts_canvas"], float)
        a = {"skin": it["skin"], "slot": it["slotIndex"], "name": it["name"]}
        vw = skel.weight_of(a)
        if vw is None:  # fallback: match by slot+name
            vw = next((m["vw"] for m in skel.weights["meshes"]
                       if m["slot"] == it["slotIndex"] and m["name"] == it["name"]), None)
        # bones/verts
        mbones, mverts = [], []
        n = len(pts1)
        for i in range(n):
            wx, wy = skel.from_canvas(*pts1[i])
            mbones.append(len(vw[i]))
            for b, w in vw[i]:
                b = int(b); bm = skel.bones[b]
                det = bm["a"] * bm["d"] - bm["b"] * bm["c"]
                ax = wx - bm["x"]; ay = wy - bm["y"]
                lx = (bm["d"] * ax - bm["b"] * ay) / det; ly = (bm["a"] * ay - bm["c"] * ax) / det
                mbones.append(b); mverts += [lx, ly, float(w)]
        # premultiplied patch crop over the dilated footprint
        pm = _premult(part_images[it["slot"]])
        xs_ = pts1[:, 0]; ys_ = pts1[:, 1]
        bx0 = max(int(np.floor(xs_.min())) - 1, 0); by0 = max(int(np.floor(ys_.min())) - 1, 0)
        bx1 = min(int(np.ceil(xs_.max())) + 1, skel.W); by1 = min(int(np.ceil(ys_.max())) + 1, skel.H)
        patch = pm[by0:by1, bx0:bx1].copy()
        localuv = [(pts1[i][0] - bx0, pts1[i][1] - by0) for i in range(n)]
        meshes.append({"slot": it["slot"], "slotIndex": it["slotIndex"], "name": it["name"],
                       "skin": it["skin"], "bones": mbones, "vertices": mverts,
                       "worldVerticesLength": n * 2, "triangles": [int(x) for x in it["tris"]]})
        patches.append((patch, localuv))

    # shelf-pack patches onto one page
    order = sorted(range(len(patches)), key=lambda k: -patches[k][0].shape[0])
    place = {}; x = pad; y = pad; rowh = 0
    for k in order:
        ph, pw = patches[k][0].shape[:2]
        if x + pw + pad > page_w: x = pad; y += rowh + pad; rowh = 0
        place[k] = (x, y); x += pw + pad; rowh = max(rowh, ph)
    page_h = ((y + rowh + pad + 3) // 4) * 4
    page = np.zeros((page_h, page_w, 4), np.uint8)
    for k in range(len(patches)):
        patch, localuv = patches[k]; px, py = place[k]; ph, pw = patch.shape[:2]
        page[py:py + ph, px:px + pw] = patch
        ruv = []
        for (ux, uy) in localuv:
            ruv += [(px + ux) / page_w, 1.0 - (py + uy) / page_h]   # V flipped for Unity bottom-up
        meshes[k]["regionUVs"] = ruv

    from PIL import Image
    Image.fromarray(page).save(os.path.join(out_dir, "racer_page.png"))
    single = {"page": "racer_page.png", "pageW": page_w, "pageH": page_h, "meshes": meshes}
    json.dump(single, open(os.path.join(out_dir, "single.json"), "w"))
    return {"page": [page_w, page_h], "meshes": len(meshes), "out": out_dir}
