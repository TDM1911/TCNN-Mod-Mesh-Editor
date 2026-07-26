"""
Outfit Mesh Editor — local web app backend.

Run:  python server.py   (then open http://localhost:5000)

Wraps the proven build pipeline (pipeline.py): load a skeleton template, drop drawn part PNGs
onto slots, auto-pick the best donor mesh, see coverage, dilate + drag vertices, and export
single.json + packed page straight into the mod for in-game testing.
"""
import os, io, base64, json, numpy as np
from flask import Flask, request, jsonify, send_from_directory, Response
from PIL import Image
import pipeline as P

HERE = os.path.dirname(os.path.abspath(__file__))
SKEL_DIR = os.path.join(HERE, "skeletons")

app = Flask(__name__, static_folder=os.path.join(HERE, "static"))

# ---- in-memory session (single local user) ----
S = {"skel": None, "name": None, "parts": {}, "meshes": {}}
# meshes[slot] = {"slotIndex", "name", "skin", "tris":[...], "verts":[[x,y],...], "base":[[x,y],...]}


def png_response(arr):
    im = Image.fromarray(arr)
    b = io.BytesIO(); im.save(b, "PNG"); b.seek(0)
    return Response(b.read(), mimetype="image/png")


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/skeletons")
def skeletons():
    out = []
    for d in sorted(os.listdir(SKEL_DIR)):
        if os.path.isfile(os.path.join(SKEL_DIR, d, "positions.json")):
            out.append(d)
    return jsonify(out)


@app.route("/api/init")
def init():
    name = request.args["skeleton"]
    skel = P.Skeleton(os.path.join(SKEL_DIR, name))
    S.update(skel=skel, name=name, parts={}, meshes={})
    return jsonify({"name": name, "W": skel.W, "H": skel.H,
                    "slots": skel.slots_with_meshes()})


@app.route("/api/base_layers")
def base_layers():
    """Orientation backdrop: faint body (whatever exists) + skeleton armature."""
    return png_response(S["skel"].base_backdrop())


def _decode_part(file_storage, skel):
    im = Image.open(file_storage).convert("RGBA")
    arr = np.array(im)
    if arr.shape[0] != skel.H or arr.shape[1] != skel.W:
        im = im.resize((skel.W, skel.H)); arr = np.array(im)   # expect full-canvas parts
    return arr


def _store_mesh(slot, a, hasPart):
    skel = S["skel"]
    verts = skel.mesh_canvas_verts(a).tolist()
    S["meshes"][slot] = {"a": a, "slotIndex": slot, "name": a["name"], "skin": a["skin"],
                         "tris": [int(t) for t in a["tris"]], "verts": verts,
                         "base": [list(v) for v in verts], "hasPart": hasPart}
    return S["meshes"][slot]


def _default_donor(slot):
    """Pick a sensible default mesh to show the moment a slot is clicked (no part needed)."""
    cands = S["skel"].candidates(slot)
    if not cands: return None
    outf = [c for c in cands if c["skin"].startswith("outfits/")] or cands
    return max(outf, key=lambda c: len(c["verts"]))


def _public(m):
    return {"slotIndex": m["slotIndex"], "name": m["name"], "skin": m["skin"],
            "tris": m["tris"], "verts": m["verts"], "hasPart": m["hasPart"]}


def _cov(slot):
    """Coverage against the drawn part if present, else against the donor's own art."""
    skel = S["skel"]; m = S["meshes"][slot]
    if m["hasPart"] and slot in S["parts"]:
        pmask = P.part_mask(S["parts"][slot])
    else:
        ref = skel.warp_ref(m["a"]); pmask = (ref[:, :, 3] > 0).astype(np.uint8)
    return P.coverage(skel, np.array(m["verts"]), m["tris"], pmask)


@app.route("/api/select_slot")
def select_slot():
    skel = S["skel"]; slot = int(request.args["slot"])
    if slot not in S["meshes"]:
        a = _default_donor(slot)
        if a is None: return jsonify({"ok": False, "msg": "no editable mesh in this slot"})
        _store_mesh(slot, a, hasPart=False)
    m = S["meshes"][slot]
    return jsonify({"ok": True, "mesh": _public(m), "coverage": _cov(slot),
                    "candidates": [{"name": c["name"], "verts": len(c["verts"]) // 2}
                                   for c in skel.candidates(slot)]})


@app.route("/api/ref_png/<int:slot>")
def ref_png(slot):
    m = S["meshes"].get(slot)
    if m is None: return ("", 404)
    if m["hasPart"] and slot in S["parts"]:
        return png_response(S["parts"][slot])
    return png_response(S["skel"].warp_ref(m["a"]))     # the game's own art for this donor mesh


def _tokens(s):
    import re
    return set(t for t in re.split(r"[^a-z0-9]+", s.lower()) if len(t) > 1)


def _name_bonus(fname, slotname):
    """Reward slots whose name shares words with the file name (e.g. 'torso_top' -> 'top_torso')."""
    if not fname: return 0.0
    ft = _tokens(os.path.splitext(fname)[0]); st = _tokens(slotname)
    if not ft or not st: return 0.0
    return 0.30 * len(ft & st) / len(ft)     # up to +0.30 when all filename words match the slot


def _all_overlaps(pmask, fname=None):
    """Donor meshes overlapping the drawing, ranked by IoU (+ filename hint). Bbox pre-filter keeps
    it fast. Each tuple is (score, iou, slot, a)."""
    skel = S["skel"]
    ys, xs = np.where(pmask > 0)
    if len(xs) == 0: return []
    pb = (xs.min(), ys.min(), xs.max(), ys.max()); artpx = int(pmask.sum())
    best_per_slot = {}
    for sidx in skel.by_slot:
        bonus = _name_bonus(fname, skel.slot_by_index[sidx]["name"])
        for a in skel.candidates(sidx):
            pts = skel.mesh_canvas_verts(a)
            if (pts[:, 0].max() < pb[0] or pts[:, 0].min() > pb[2] or
                    pts[:, 1].max() < pb[1] or pts[:, 1].min() > pb[3]):
                continue
            mm = P.rasterize(pts, a["tris"], skel.W, skel.H)
            inter = int(((mm > 0) & (pmask > 0)).sum())
            if inter == 0: continue
            iou = inter / (int((mm > 0).sum()) + artpx - inter + 1e-6)
            score = iou + bonus
            if sidx not in best_per_slot or score > best_per_slot[sidx][0]:
                best_per_slot[sidx] = (score, iou, sidx, a)
    return sorted(best_per_slot.values(), key=lambda e: -e[0])


def _global_pick(pmask, fname=None):
    ov = _all_overlaps(pmask, fname)
    if not ov: return None
    _, iou, slot, a = ov[0]
    return (iou, slot, a)


def _apply_pick(pick, drawing, from_slot=None):
    _, sidx, a = pick
    if from_slot is not None and from_slot != sidx:
        S["meshes"].pop(from_slot, None); S["parts"].pop(from_slot, None)
    S["parts"][sidx] = drawing
    _store_mesh(sidx, a, hasPart=True)
    skel = S["skel"]
    return jsonify({"ok": True, "slot": sidx, "movedFrom": from_slot if from_slot != sidx else None,
                    "mesh": _public(S["meshes"][sidx]), "coverage": _cov(sidx),
                    "candidates": [{"name": c["name"], "verts": len(c["verts"]) // 2}
                                   for c in skel.candidates(sidx)]})


@app.route("/api/upload_part", methods=["POST"])
def upload_part():
    skel = S["skel"]; slot = int(request.form["slot"])
    f = request.files["file"]; arr = _decode_part(f, skel)
    pin = request.form.get("pin") in ("1", "true", "True")
    if pin and slot >= 0:                     # force into the selected slot's best mesh
        a = P.auto_pick(skel, slot, P.part_mask(arr))
        if a is None:
            return jsonify({"ok": False, "msg": "no mesh in this slot overlaps the drawing"})
        return _apply_pick((0.0, slot, a), arr, from_slot=slot)
    pick = _global_pick(P.part_mask(arr), f.filename)   # best-fit mesh ANYWHERE (+ filename hint)
    if pick is None:
        return jsonify({"ok": False, "msg": "this drawing doesn't overlap any mesh on the body"})
    return _apply_pick(pick, arr, from_slot=slot)


@app.route("/api/upload_batch", methods=["POST"])
def upload_batch():
    """Import many part PNGs at once. Each file's best-fit mesh is found across the whole body,
    then a greedy pass assigns files to slots so two pieces never claim the same slot."""
    skel = S["skel"]
    files = request.files.getlist("files")
    parsed = []
    for f in files:
        arr = _decode_part(f, skel)
        parsed.append({"file": f.filename, "arr": arr, "ov": _all_overlaps(P.part_mask(arr), f.filename)})
    # greedy: highest (file, slot) score first (IoU + filename hint), each file and slot used once
    edges = []
    for fi, p in enumerate(parsed):
        for (score, iou, slot, a) in p["ov"]:
            edges.append((score, fi, slot, a))
    edges.sort(key=lambda e: -e[0])
    used_f, used_s, assign = set(), set(), {}
    for score, fi, slot, a in edges:
        if fi in used_f or slot in used_s: continue
        used_f.add(fi); used_s.add(slot); assign[fi] = (slot, a, score)
    results = []
    for fi, p in enumerate(parsed):
        if fi in assign:
            slot, a, iou = assign[fi]
            S["parts"][slot] = p["arr"]; _store_mesh(slot, a, hasPart=True)
            results.append({"file": p["file"], "ok": True, "slot": slot,
                            "slotName": skel.slot_by_index[slot]["name"], "mesh": a["name"],
                            "covered": _cov(slot)["covered"]})
        else:
            results.append({"file": p["file"], "ok": False,
                            "msg": "no free mesh overlaps this drawing"})
    return jsonify({"ok": True, "results": results,
                    "assigned": sum(1 for r in results if r["ok"]), "total": len(parsed)})


@app.route("/api/autopick", methods=["POST"])
def autopick():
    """Re-run best-fit for the drawing currently on a slot (e.g. after manually trying donors)."""
    slot = int(request.json["slot"])
    if slot not in S["parts"]:
        return jsonify({"ok": False, "msg": "no drawing on this slot yet"})
    pick = _global_pick(P.part_mask(S["parts"][slot]))
    if pick is None: return jsonify({"ok": False, "msg": "no mesh overlaps this drawing"})
    return _apply_pick(pick, S["parts"][slot], from_slot=slot)


@app.route("/api/pick_candidate", methods=["POST"])
def pick_candidate():
    skel = S["skel"]; slot = int(request.json["slot"]); name = request.json["name"]
    a = next((c for c in skel.candidates(slot) if c["name"] == name), None)
    if a is None: return jsonify({"ok": False})
    _store_mesh(slot, a, hasPart=S["meshes"].get(slot, {}).get("hasPart", False))
    return jsonify({"ok": True, "mesh": _public(S["meshes"][slot]), "coverage": _cov(slot)})


@app.route("/api/dilate", methods=["POST"])
def dilate():
    skel = S["skel"]; j = request.json; slot = int(j["slot"])
    m = S["meshes"][slot]; pmask = P.part_mask(S["parts"][slot])
    pts = P.dilate(skel, np.array(m["base"]), m["tris"], pmask,
                   margin=float(j.get("margin", 15)), base=float(j.get("baseRim", 11)),
                   extend_up=bool(j.get("extendUp")), extend_down=bool(j.get("extendDown")),
                   gap_r=float(j.get("gapR", 45)), gap_iters=int(j.get("gapIters", 6)))
    m["verts"] = pts.tolist()
    return jsonify({"ok": True, "verts": m["verts"], "coverage": _cov(slot)})


@app.route("/api/set_verts", methods=["POST"])
def set_verts():
    slot = int(request.json["slot"]); S["meshes"][slot]["verts"] = request.json["verts"]
    return jsonify({"ok": True, "coverage": _cov(slot)})


@app.route("/api/reset_verts", methods=["POST"])
def reset_verts():
    slot = int(request.json["slot"]); m = S["meshes"][slot]
    m["verts"] = [list(v) for v in m["base"]]
    return jsonify({"ok": True, "verts": m["verts"], "coverage": _cov(slot)})


@app.route("/api/part_png/<int:slot>")
def part_png(slot):
    return png_response(S["parts"][slot])


@app.route("/api/state")
def state():
    return jsonify({"meshes": {str(k): {"name": v["name"], "slotIndex": v["slotIndex"]}
                              for k, v in S["meshes"].items()}})


@app.route("/api/anim_frames")
def anim_frames():
    """Per-frame canvas verts for the current slot's mesh through the idle animation."""
    skel = S["skel"]; slot = int(request.args["slot"])
    m = S["meshes"].get(slot)
    if m is None: return jsonify({"ok": False, "msg": "select a slot first"})
    if skel.anim is None: return jsonify({"ok": False, "msg": "no idle animation for this skeleton"})
    vw = skel.weight_of(m["a"])
    frames = skel.deform_frames(m["verts"], vw)
    if frames is None: return jsonify({"ok": False, "msg": "this mesh has no weights to animate"})
    return jsonify({"ok": True, "frames": frames, "dur": skel.anim.get("dur", 3.0),
                    "tris": m["tris"]})


@app.route("/api/export", methods=["POST"])
def export():
    skel = S["skel"]; j = request.json or {}
    out_dir = j.get("out") or os.path.join(HERE, "projects", (S["name"] or "outfit") + "_out")
    exportable = [(slot, m) for slot, m in S["meshes"].items() if m["hasPart"] and slot in S["parts"]]
    if not exportable:
        return jsonify({"ok": False, "msg": "drop a drawing on at least one slot before exporting"})
    items = [{"slot": str(slot), "slotIndex": m["slotIndex"], "name": m["name"],
              "skin": m["skin"], "verts_canvas": m["verts"], "tris": m["tris"]}
             for slot, m in exportable]
    part_images = {str(slot): S["parts"][slot] for slot, m in exportable}
    summary = P.pack_and_export(skel, items, part_images, out_dir,
                                page_w=int(j.get("pageW", 2048)))

    # --- configs: hide.json (nude slots this outfit covers) + outfit.json ---
    NUDE = ["pussy", "pubes", "pubic_hair", "panties", "panties_front", "cb_string",
            "nipples", "nipples_hard", "nipple_swollen_back", "nipple_swollen_front",
            "boobs_shadow", "boob_front", "boob_back", "sweat_boobB", "sweat_boobF"]
    hide = j.get("hide")
    if hide is None:
        hide = [n for n in NUDE if skel.slot_by_name.get(n)]   # only ones that exist here
    json.dump(hide, open(os.path.join(out_dir, "hide.json"), "w"))
    oid = j.get("id", "racer")
    json.dump({"id": oid, "name": j.get("title", "Custom Outfit"),
               "skeleton": S["name"], "base": "naked"},
              open(os.path.join(out_dir, "outfit.json"), "w"))
    return jsonify({"ok": True, **summary, "configs": ["single.json", "racer_page.png", "hide.json", "outfit.json"],
                    "hidden": len(hide)})


if __name__ == "__main__":
    print("Outfit Mesh Editor -> http://localhost:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
