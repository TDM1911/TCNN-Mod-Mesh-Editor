const $ = s => document.querySelector(s);
const cv = $("#cv"), ctx = cv.getContext("2d");
let skel = null, sel = null, skelName = null;
const meshes = {};            // slot -> {verts, base, tris, name, skin, slotIndex}
const partImg = {};           // slot -> HTMLImageElement
let baseImg = null;
const view = { s: 1, tx: 0, ty: 0 };   // canvas(model) -> screen
let drag = null;              // {type:'pan'|'vert', ...}
const DEFAULT_OUT = "H:/projects/TCNN Modding/0.5.0-a5/TCNNOutfits/assets/racer";

function toast(m, ms = 2200) { const t = $("#toast"); t.textContent = m; t.classList.add("show");
  clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove("show"), ms); }

// ---------- view helpers ----------
function resize() { cv.width = cv.clientWidth; cv.height = cv.clientHeight; draw(); }
window.addEventListener("resize", resize);
function fit() {
  if (!skel) return;
  const pad = 40, s = Math.min((cv.width - pad) / skel.W, (cv.height - pad) / skel.H);
  view.s = s; view.tx = (cv.width - skel.W * s) / 2; view.ty = (cv.height - skel.H * s) / 2;
}
const toScreen = (x, y) => [x * view.s + view.tx, y * view.s + view.ty];
const toModel = (px, py) => [(px - view.tx) / view.s, (py - view.ty) / view.s];

// ---------- boundary edges (drawn thicker) ----------
function boundary(tris, n) {
  const ec = new Map();
  const key = (a, b) => Math.min(a, b) + "," + Math.max(a, b);
  for (let t = 0; t < tris.length; t += 3)
    for (const [a, b] of [[tris[t], tris[t+1]], [tris[t+1], tris[t+2]], [tris[t+2], tris[t]]])
      ec.set(key(a, b), (ec.get(key(a, b)) || 0) + 1);
  const bset = new Set();
  for (const [k, c] of ec) if (c === 1) { const [a, b] = k.split(",").map(Number); bset.add(a); bset.add(b); }
  return bset;
}

// ---------- draw ----------
function draw() {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (!skel) return;
  // frame
  const [ox, oy] = toScreen(0, 0);
  ctx.strokeStyle = "#2a2e37"; ctx.lineWidth = 1;
  ctx.strokeRect(ox, oy, skel.W * view.s, skel.H * view.s);

  if ($("#showBase").checked && baseImg) ctx.drawImage(baseImg, ox, oy, skel.W * view.s, skel.H * view.s);

  if (anim.playing && anim.layers.length) {
    if ($("#showArt").checked)
      for (const L of anim.layers) {                          // every animating layer's art, warped
        const img = partImg[L.slot];
        if (img && L.base) drawWarpedArt(img, L.base, L.tris, L.frames[anim.i]);
      }
    if ($("#showMesh").checked) {
      const L = anim.layers.find(x => x.slot === sel) || (anim.layers.length === 1 ? anim.layers[0] : null);
      if (L) drawWire(L.tris, L.frames[anim.i]);
    }
  } else {
    if ($("#showArt").checked && sel != null && partImg[sel]) {
      ctx.globalAlpha = 0.9; ctx.drawImage(partImg[sel], ox, oy, skel.W * view.s, skel.H * view.s); ctx.globalAlpha = 1;
    }
    if ($("#showMesh").checked && sel != null && meshes[sel]) drawMesh(meshes[sel], meshes[sel].verts);
  }
}

const anim = { layers: [], i: 0, playing: false, dur: 3, raf: 0 };

// Edges-only mesh overlay (used during animation, no draggable handles).
function drawWire(T, V) {
  const P = V.map(([x, y]) => toScreen(x, y));
  ctx.strokeStyle = "rgba(90,180,255,0.55)"; ctx.lineWidth = 1;
  for (let t = 0; t < T.length; t += 3) {
    ctx.beginPath();
    ctx.moveTo(P[T[t]][0], P[T[t]][1]); ctx.lineTo(P[T[t + 1]][0], P[T[t + 1]][1]); ctx.lineTo(P[T[t + 2]][0], P[T[t + 2]][1]);
    ctx.closePath(); ctx.stroke();
  }
}
// Make sure every slot's art image is loaded before an all-layers preview.
function ensurePartImgs(slots) {
  return Promise.all(slots.map(s => partImg[s] ? Promise.resolve() : new Promise(res => {
    const im = new Image(); im.onload = () => { partImg[s] = im; res(); }; im.onerror = res;
    im.src = "/api/part_png/" + s + "?t=" + Date.now();
  })));
}
// Warp a part image per triangle: setup verts (image px) -> deformed frame verts.
function drawWarpedArt(img, base, T, fv) {
  for (let t = 0; t < T.length; t += 3) {
    const ia = T[t], ib = T[t + 1], ic = T[t + 2];
    texTri(img, base[ia], base[ib], base[ic],
      toScreen(fv[ia][0], fv[ia][1]), toScreen(fv[ib][0], fv[ib][1]), toScreen(fv[ic][0], fv[ic][1]));
  }
}
function texTri(img, s0, s1, s2, d0, d1, d2) {
  // expand dst slightly from centroid to hide seams between triangles
  const cx = (d0[0] + d1[0] + d2[0]) / 3, cy = (d0[1] + d1[1] + d2[1]) / 3, k = 0.6;
  const ex = p => [p[0] + (p[0] - cx > 0 ? k : -k), p[1] + (p[1] - cy > 0 ? k : -k)];
  const e0 = ex(d0), e1 = ex(d1), e2 = ex(d2);
  ctx.save();
  ctx.beginPath(); ctx.moveTo(e0[0], e0[1]); ctx.lineTo(e1[0], e1[1]); ctx.lineTo(e2[0], e2[1]); ctx.closePath(); ctx.clip();
  // affine mapping image-space src tri -> screen dst tri
  const x0 = s0[0], y0 = s0[1], x1 = s1[0], y1 = s1[1], x2 = s2[0], y2 = s2[1];
  const den = x0 * (y1 - y2) - x1 * (y0 - y2) + x2 * (y0 - y1);
  if (Math.abs(den) < 1e-6) { ctx.restore(); return; }
  const u0 = d0[0], v0 = d0[1], u1 = d1[0], v1 = d1[1], u2 = d2[0], v2 = d2[1];
  const a = (u0 * (y1 - y2) - u1 * (y0 - y2) + u2 * (y0 - y1)) / den;
  const c = (x0 * (u1 - u2) - x1 * (u0 - u2) + x2 * (u0 - u1)) / den;
  const e = (x0 * (y1 * u2 - y2 * u1) - x1 * (y0 * u2 - y2 * u0) + x2 * (y0 * u1 - y1 * u0)) / den;
  const b = (v0 * (y1 - y2) - v1 * (y0 - y2) + v2 * (y0 - y1)) / den;
  const d = (x0 * (v1 - v2) - x1 * (v0 - v2) + x2 * (v0 - v1)) / den;
  const f = (x0 * (y1 * v2 - y2 * v1) - x1 * (y0 * v2 - y2 * v0) + x2 * (y0 * v1 - y1 * v0)) / den;
  ctx.setTransform(a, b, c, d, e, f);
  ctx.drawImage(img, 0, 0);
  ctx.restore();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
}
function drawMesh(m, V) {
  const T = m.tris;
  const P = V.map(([x, y]) => toScreen(x, y));
  // triangle fill
  ctx.fillStyle = "rgba(45,160,255,0.13)";
  ctx.strokeStyle = "rgba(90,180,255,0.5)"; ctx.lineWidth = 1;
  for (let t = 0; t < T.length; t += 3) {
    ctx.beginPath();
    ctx.moveTo(P[T[t]][0], P[T[t]][1]); ctx.lineTo(P[T[t+1]][0], P[T[t+1]][1]); ctx.lineTo(P[T[t+2]][0], P[T[t+2]][1]);
    ctx.closePath(); ctx.fill(); ctx.stroke();
  }
  // vertices
  const bset = boundary(T, V.length);
  for (let i = 0; i < P.length; i++) {
    const b = bset.has(i);
    ctx.beginPath(); ctx.arc(P[i][0], P[i][1], b ? 4 : 2.5, 0, 7);
    ctx.fillStyle = b ? "#ff5f6d" : "#7cd4ff"; ctx.fill();
    if (drag && drag.type === "vert" && drag.i === i) { ctx.strokeStyle = "#fff"; ctx.lineWidth = 2; ctx.stroke(); }
  }
}

// ---------- interaction ----------
cv.addEventListener("wheel", e => {
  e.preventDefault();
  const [mx, my] = toModel(e.offsetX, e.offsetY);
  view.s *= e.deltaY < 0 ? 1.1 : 1 / 1.1;
  view.tx = e.offsetX - mx * view.s; view.ty = e.offsetY - my * view.s; draw();
}, { passive: false });

// animation preview: chosen animation, one layer or all imported layers together
$("#playBtn").onclick = async () => {
  if (anim.playing) { stopAnim(); return; }
  if (!skel) return;
  const all = $("#allLayers").checked;
  let url = "/api/anim_frames?anim=" + encodeURIComponent($("#animSel").value || "");
  if (all) url += "&all=1";
  else {
    if (sel == null || !meshes[sel] || !meshes[sel].hasPart) { toast("pick an imported slot, or tick 'all layers'"); return; }
    url += "&slot=" + sel;
  }
  const r = await api(url);
  if (!r.ok) { toast(r.msg); return; }
  anim.layers = r.meshes || []; anim.dur = r.dur || 3; anim.i = 0;
  if (!anim.layers.length) { toast("nothing to animate"); return; }
  await ensurePartImgs(anim.layers.map(L => L.slot));       // load every layer's art first
  anim.playing = true; $("#playBtn").textContent = "⏸ Stop";
  const nf = anim.layers[0].frames.length;
  anim.raf = setInterval(() => { anim.i = (anim.i + 1) % nf; draw(); }, (anim.dur * 1000) / nf);
};
function stopAnim() {
  if (!anim.playing) return;
  anim.playing = false; if (anim.raf) clearInterval(anim.raf); anim.raf = 0;
  $("#playBtn").textContent = "▶ Play"; draw();
}

cv.addEventListener("mousedown", e => {
  if (anim.playing) stopAnim();
  if (sel != null && meshes[sel]) {
    const m = meshes[sel];
    for (let i = 0; i < m.verts.length; i++) {
      const [sx, sy] = toScreen(m.verts[i][0], m.verts[i][1]);
      if (Math.hypot(sx - e.offsetX, sy - e.offsetY) < 7) { drag = { type: "vert", i }; return; }
    }
  }
  drag = { type: "pan", x: e.offsetX - view.tx, y: e.offsetY - view.ty };
});
window.addEventListener("mousemove", e => {
  if (!drag) return;
  const r = cv.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
  if (drag.type === "pan") { view.tx = px - drag.x; view.ty = py - drag.y; }
  else { const [mx, my] = toModel(px, py); meshes[sel].verts[drag.i] = [mx, my]; }
  draw();
});
window.addEventListener("mouseup", () => {
  if (drag && drag.type === "vert") saveVerts();
  drag = null;
});

// ---------- data flow ----------
async function api(path, opt) { const r = await fetch(path, opt); return r.json(); }

async function loadSkeletons() {
  const list = await api("/api/skeletons");
  $("#skelSel").innerHTML = list.map(s => `<option>${s}</option>`).join("");
}

$("#loadBtn").onclick = async () => {
  const name = $("#skelSel").value;
  skelName = name;
  skel = await api("/api/init?skeleton=" + name);
  for (const k in meshes) delete meshes[k]; for (const k in partImg) delete partImg[k];
  sel = null; stopAnim(); $("#hint").style.display = "none";
  baseImg = new Image(); baseImg.onload = draw; baseImg.src = "/api/base_layers?t=" + Date.now();
  const a = await api("/api/anims");
  $("#animSel").innerHTML = (a.names || []).map(n => `<option value="${n}">${n}</option>`).join("")
    || `<option value="">(no animations)</option>`;
  renderSlots(); fit(); draw();
  toast(`${name}: ${skel.slots.length} slots, ${(a.names || []).length} anims`);
};

function renderSlots() {
  const f = $("#slotFilter").value.toLowerCase();
  const rows = skel.slots.filter(s => !f || s.name.toLowerCase().includes(f)).map(s => {
    const done = meshes[s.index] && meshes[s.index].hasPart;   // green ✓ = has a drawing, will export
    const act = sel === s.index ? "active" : "";
    return `<div class="slot ${done ? "has-mesh" : ""} ${act}" data-slot="${s.index}">
      <span>${s.name}</span><span class="cnt">${done ? "✓ " : ""}${s.candidates}</span></div>`;
  }).join("");
  $("#slotList").innerHTML = rows;
  $("#slotList").querySelectorAll(".slot").forEach(el =>
    el.onclick = () => selectSlot(+el.dataset.slot));
  renderLayerOrder();
}
$("#slotFilter").oninput = renderSlots;

// Imported pieces in Spine draw order (high slotIndex = front/on top). Read like Photoshop layers.
function renderLayerOrder() {
  const imported = Object.values(meshes).filter(m => m && m.hasPart)
    .map(m => ({ idx: m.slotIndex, name: (skel.slots.find(s => s.index === m.slotIndex) || {}).name || m.slotIndex }))
    .sort((a, b) => b.idx - a.idx);
  if (!imported.length) { $("#layerOrder").innerHTML = ""; return; }
  const rows = imported.map((r, i) =>
    `<div class="slot ${sel === r.idx ? "active" : ""}" data-slot="${r.idx}">
      <span>${i === 0 ? "▲ " : i === imported.length - 1 ? "▼ " : ""}${r.name}</span><span class="cnt">#${r.idx}</span></div>`).join("");
  $("#layerOrder").innerHTML = `<div class="panel-title">Layers · front → back</div>${rows}`;
  $("#layerOrder").querySelectorAll(".slot").forEach(el => el.onclick = () => selectSlot(+el.dataset.slot));
}

async function selectSlot(idx) {
  stopAnim();
  sel = idx;
  const s = skel.slots.find(x => x.index === idx);
  $("#selInfo").textContent = `[${idx}] ${s.name}  <${s.bone}>`;
  const r = await api("/api/select_slot?slot=" + idx);
  if (!r.ok) {
    meshes[idx] = null; delete partImg[idx];
    for (const p of ["#donorWrap", "#dilate", "#cov"]) $(p).classList.add("hidden");
    renderSlots(); draw(); toast(r.msg || "no editable mesh here");
    return;
  }
  meshes[idx] = { ...r.mesh, _cov: r.coverage };
  // show the art for this slot (your drawing if dropped, else the game's own art for the mesh)
  const im = new Image(); im.onload = () => { partImg[idx] = im; draw(); };
  im.src = "/api/ref_png/" + idx + "?t=" + Date.now();
  fillDonors(r.candidates, r.mesh.name);
  for (const p of ["#donorWrap", "#dilate", "#cov"]) $(p).classList.remove("hidden");
  refreshCovUI(r.coverage); renderSlots(); draw();
}

function fillDonors(cands, cur) {
  $("#donorSel").innerHTML = (cands || []).map(c =>
    `<option value="${c.name}" ${c.name === cur ? "selected" : ""}>${c.name} (${c.verts}v)</option>`).join("");
}

// upload part
const drop = $("#drop"), fileIn = $("#fileIn");
drop.onclick = () => fileIn.click();
drop.ondragover = e => { e.preventDefault(); drop.classList.add("hot"); };
drop.ondragleave = () => drop.classList.remove("hot");
drop.ondrop = e => { e.preventDefault(); drop.classList.remove("hot");
  const fs = e.dataTransfer.files;
  if (fs.length > 1) batchImport(fs); else if (fs.length === 1) uploadPart(fs[0]); };
fileIn.onchange = () => { if (fileIn.files[0]) uploadPart(fileIn.files[0]); };

// open an existing exported outfit (single.json + page) to tweak
$("#importOutfitBtn").onclick = () => $("#importIn").click();
$("#importIn").onchange = () => { if ($("#importIn").files.length) importOutfit($("#importIn").files); };
async function importOutfit(fileList) {
  if (!skel) { toast("load the matching skeleton first (e.g. portrait)"); return; }
  const fd = new FormData(); for (const f of fileList) fd.append("files", f);
  toast("opening outfit…", 8000);
  const r = await api("/api/import_outfit", { method: "POST", body: fd });
  if (!r.ok) { toast(r.msg || "import failed"); return; }
  for (const res of r.results) if (res.ok) meshes[res.slot] = { slotIndex: res.slot, hasPart: true };
  const rows = r.results.map(x => x.ok
    ? `<div style="color:#7cf0a8">✓ ${x.slotName} (${(x.coverage * 100).toFixed(0)}%)</div>`
    : `<div style="color:#ff8a8a">✗ ${x.file} — ${x.msg}</div>`).join("");
  $("#batchResults").innerHTML = `<div class="panel-title">opened ${r.imported}/${r.total}</div>${rows}`;
  renderSlots();
  const first = r.results.find(x => x.ok);
  if (first) selectSlot(first.slot);
  toast(`opened ${r.imported}/${r.total} meshes — tweak, then re-export`);
}

// batch import: many part PNGs at once -> auto-assigned to best slots
$("#batchBtn").onclick = () => $("#batchIn").click();
$("#batchIn").onchange = () => { if ($("#batchIn").files.length) batchImport($("#batchIn").files); };
async function batchImport(fileList) {
  if (!skel) { toast("load a skeleton first"); return; }
  const fd = new FormData();
  for (const f of fileList) fd.append("files", f);
  toast(`importing ${fileList.length} parts…`, 8000);
  const r = await api("/api/upload_batch", { method: "POST", body: fd });
  if (!r.ok) { toast("batch import failed"); return; }
  for (const res of r.results) if (res.ok) meshes[res.slot] = { slotIndex: res.slot, hasPart: true, name: res.mesh };
  const rows = r.results.map(x => x.ok
    ? `<div style="color:#7cf0a8">✓ ${x.file} → ${x.slotName} (${(x.covered * 100).toFixed(0)}%)</div>`
    : `<div style="color:#ff8a8a">✗ ${x.file} — ${x.msg}</div>`).join("");
  $("#batchResults").innerHTML = `<div class="panel-title">imported ${r.assigned}/${r.total}</div>${rows}`;
  renderSlots();
  const first = r.results.find(x => x.ok);
  if (first) selectSlot(first.slot);
  toast(`imported ${r.assigned}/${r.total} parts — review, then Export`);
}

async function uploadPart(file) {
  const pin = $("#pinSlot").checked;
  if (pin && sel == null) { toast("select a slot first to pin to it"); return; }
  const fd = new FormData(); fd.append("slot", sel == null ? -1 : sel); fd.append("file", file);
  fd.append("pin", pin ? 1 : 0);
  const r = await api("/api/upload_part", { method: "POST", body: fd });
  if (!r.ok) { toast(r.msg || "auto-pick failed"); return; }
  applyPick(r);
}
// Move selection to the auto-picked slot and show the drawing + mesh there.
function applyPick(r) {
  const prev = sel;
  sel = r.slot;
  const s = skel.slots.find(x => x.index === sel);
  $("#selInfo").textContent = `[${sel}] ${s.name}  <${s.bone}>`;
  const im = new Image(); im.onload = () => { partImg[sel] = im; loadMesh(r); };
  im.src = "/api/part_png/" + sel + "?t=" + Date.now();
  if (r.movedFrom != null && r.movedFrom !== r.slot)
    setTimeout(() => toast(`best fit is '${s.name}' — put your drawing there`), 50);
}
function loadMesh(r) {
  meshes[sel] = { ...r.mesh, _cov: r.coverage };
  fillDonors(r.candidates, r.mesh.name);
  for (const p of ["#donorWrap", "#dilate", "#cov"]) $(p).classList.remove("hidden");
  refreshCovUI(r.coverage); renderSlots(); draw();
  toast(`auto-picked ${r.mesh.name} — ${(r.coverage.covered * 100).toFixed(0)}% covered`);
}
$("#autopickBtn").onclick = async () => {
  if (sel == null || !meshes[sel] || !meshes[sel].hasPart) { toast("drop your drawing first — auto-pick fits it to a mesh"); return; }
  const r = await api("/api/autopick", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ slot: sel }) });
  if (!r.ok) { toast(r.msg); return; }
  applyPick(r);
};
$("#donorSel").onchange = async () => {
  const r = await api("/api/pick_candidate", { method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ slot: sel, name: $("#donorSel").value }) });
  if (!r.ok) return;
  meshes[sel] = { ...r.mesh, _cov: r.coverage };
  if (!r.mesh.hasPart) {   // refresh the reference art to match the new donor mesh
    const im = new Image(); im.onload = () => { partImg[sel] = im; draw(); };
    im.src = "/api/ref_png/" + sel + "?t=" + Date.now();
  }
  refreshCovUI(r.coverage); renderSlots(); draw();
};

function refreshCovUI(c) {
  if (!c) return;
  $("#covPct").textContent = (c.covered * 100).toFixed(1) + "%";
  $("#spillPct").textContent = (c.spill * 100).toFixed(1) + "%";
  $("#covPct").style.color = c.covered > 0.995 ? "#7cf0a8" : c.covered > 0.95 ? "#ffd479" : "#ff8a8a";
}

// dilate sliders
for (const id of ["margin", "baseRim", "gapR"])
  $("#" + id).oninput = () => $("#" + id + "V").textContent = $("#" + id).value;
$("#applyDilate").onclick = async () => {
  if (!meshes[sel]) return;
  const j = { slot: sel, margin: +$("#margin").value, baseRim: +$("#baseRim").value,
    gapR: +$("#gapR").value, extendUp: $("#extendUp").checked, extendDown: $("#extendDown").checked };
  const r = await api("/api/dilate", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(j) });
  meshes[sel].verts = r.verts; meshes[sel]._cov = r.coverage; refreshCovUI(r.coverage); draw(); toast("expanded");
};
$("#resetVerts").onclick = async () => {
  const r = await api("/api/reset_verts", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ slot: sel }) });
  meshes[sel].verts = r.verts; meshes[sel]._cov = r.coverage; refreshCovUI(r.coverage); draw();
};
let saveT;
function saveVerts() {
  clearTimeout(saveT);
  saveT = setTimeout(async () => {
    const r = await api("/api/set_verts", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ slot: sel, verts: meshes[sel].verts }) });
    meshes[sel]._cov = r.coverage; refreshCovUI(r.coverage);
  }, 120);
}

$("#exportBtn").onclick = async () => {
  const n = Object.values(meshes).filter(m => m && m.hasPart).length;
  if (!n) { toast("import or drop at least one part first"); return; }
  // Each skeleton is its own surface subfolder under the outfit — the mod injects whichever
  // surface's meshes resolve on the live skeleton. Never export flat into the outfit root.
  const surface = skelName === "chibi" ? "overworld" : (skelName || "portrait");
  const out = prompt(`Export ${n} part(s) — folder for page + configs:`, DEFAULT_OUT + "/" + surface);
  if (!out) return;
  const title = prompt("Outfit display name:", "Custom Outfit") || "Custom Outfit";
  const r = await api("/api/export", { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ out, title, id: "racer" }) });
  if (r.ok) toast(`exported ${r.meshes} piece(s) → page ${r.page[0]}x${r.page[1]} + ${r.configs.length} configs (${r.hidden} nude slots hidden)`, 6000);
  else toast(r.msg || "export failed");
};

for (const id of ["showArt", "showMesh", "showBase"]) $("#" + id).onchange = draw;
resize(); loadSkeletons();
