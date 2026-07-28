// @ts-check
// In-app 3D structure viewer (3Dmol.js): openMolViewer, the _mv* globals and
// viewer favorites — split out of app.js. Plain global script, loaded before
// app.js.

/* ---------- in-app 3D structure viewer (3Dmol.js) ----------
 * Opens a modal over the Results tab and flips through a list of frames with
 * the ← / → keys. Frames come from the backend as {label, xyz, energy}; xyz is
 * raw .xyz text that 3Dmol parses directly. The viewer is created lazily on
 * first open (WebGL context) and reused thereafter.
 *
 * Favorites: the user stars structures worth following up (F key or the star in
 * the list); stars persist across sessions server-side, keyed by a viewer
 * *source* ("calc:<name>" for a CREST ensemble, "folder:<path>" for a browsed
 * folder). "★ only" steps through starred frames only; "Export ★" writes them
 * to a favorites/ folder. */
// (the MolFrame frame typedef lives in web/types.js — mirror of the Python
//  MolFramePayload)
/** @type {MolFrame[]} */ let _mvFrames = [];
let _mvIndex = 0;
let _mvEmin = Infinity;         // lowest frame energy, for the ΔE column/caption
/** @type {any} */ let _mvViewer = null;
let _mvKeyHandler = null;
let _mvSource = "";             // favorites key: "calc:<name>" | "folder:<path>"
let _mvSourceKind = "calc";     // "calc" | "folder" — for Export ★ destination
let _mvSourceRef = "";          // calc name or folder path (export destination)
/** @type {Set<string>} */ let _mvFavs = new Set();
let _mvFavOnly = false;         // "★ only" filter active?

/** View a queued CREST search's conformers in 3D (button in renderConformers). */
async function viewConformers3D() {
  if (!_currentResultName) return;
  let r; try { r = /** @type {FramesResult} */ (JSON.parse(await bridge.get_conformer_frames(_currentResultName))); }
  catch (e) { failNotify("Could not load conformers."); return; }
  if (!r.ok) { failNotify(r.error || "Could not load conformers."); return; }
  await openMolViewer(r.title, r.frames, "calc", _currentResultName);
}

/** Pick any folder of .xyz files and browse them in 3D (Results header button). */
async function browseXyzFolder() {
  let r; try { r = /** @type {FramesResult} */ (JSON.parse(await bridge.browse_xyz_folder())); }
  catch (e) { failNotify("Could not open the folder."); return; }
  if (r.cancelled) return;               // picker closed — not an error
  if (!r.ok) { failNotify(r.error || "Could not open the folder."); return; }
  await openMolViewer(r.title, r.frames, "folder", r.folder || "");
}

/** @param {string} title @param {MolFrame[]} frames
 *  @param {"calc"|"folder"} kind @param {string} ref calc name or folder path */
async function openMolViewer(title, frames, kind, ref) {
  if (!frames || !frames.length) { failNotify("No structures to show."); return; }
  const $3Dmol = window["$3Dmol"] || window["3Dmol"];
  if (!$3Dmol) { failNotify("3D viewer failed to load."); return; }
  _mvFrames = frames; _mvIndex = 0; _mvFavOnly = false;
  _mvSourceKind = kind; _mvSourceRef = ref || "";
  _mvSource = `${kind}:${ref || ""}`;
  document.getElementById("mv-title").textContent = title || "Structure viewer";
  // load persisted favorites for this source
  _mvFavs = new Set();
  try {
    const fr = /** @type {FavoritesResult} */ (JSON.parse(await bridge.get_favorites(_mvSource)));
    if (fr.ok) _mvFavs = new Set(fr.labels || []);
  } catch (e) { /* favorites are best-effort — never block the viewer */ }
  // energies drive the ΔE column; compute the min once for the whole set
  _mvEmin = Math.min(...frames.map(f => (typeof f.energy === "number" ? f.energy : Infinity)));
  renderMvList();
  const overlay = document.getElementById("mol-viewer");
  overlay.style.display = "flex";
  // create the GLViewer lazily, after the container is visible & sized
  if (!_mvViewer) {
    const bg = getComputedStyle(document.documentElement)
      .getPropertyValue("--card").trim() || "#18181b";
    _mvViewer = $3Dmol.createViewer(document.getElementById("mv-gl"),
      { backgroundColor: bg });
  }
  _mvViewer.resize();
  updateFavOnlyBtn();
  molViewerShow(0);
  if (!_mvKeyHandler) {
    _mvKeyHandler = (e) => {
      if (overlay.style.display === "none") return;
      if (e.key === "ArrowRight") { molViewerStep(1); e.preventDefault(); }
      else if (e.key === "ArrowLeft") { molViewerStep(-1); e.preventDefault(); }
      else if (e.key === "Escape") { closeMolViewer(); e.preventDefault(); }
      else if (e.key === "f" || e.key === "F") { toggleFavCurrent(); e.preventDefault(); }
    };
    document.addEventListener("keydown", _mvKeyHandler);
  }
}

/** ΔE in kcal/mol vs the lowest-energy frame, or "" when energies are absent. */
function _mvDeltaE(f) {
  if (typeof f.energy === "number" && isFinite(_mvEmin)) {
    return ((f.energy - _mvEmin) * 627.5094740631).toFixed(2);
  }
  return "";
}

/** Rebuild the side list from state (labels, stars, ΔE, active highlight). */
function renderMvList() {
  document.getElementById("mv-list").innerHTML = _mvFrames.map((f, i) => {
    const fav = _mvFavs.has(f.label);
    const de = _mvDeltaE(f);
    return `<div class="mv-row ${i === _mvIndex ? "active" : ""}" data-i="${i}">
      <span class="mv-star ${fav ? "on" : ""}" onclick="event.stopPropagation();toggleFav(${i})"
            title="Star / unstar">${fav ? "★" : "☆"}</span>
      <span class="mv-row-label" onclick="molViewerShow(${i})">${escapeHtml(f.label)}</span>
      <span class="mv-row-de" onclick="molViewerShow(${i})">${de === "" ? "" : de}</span>
    </div>`;
  }).join("");
}

/** Frame indices the ← / → keys traverse — all, or favorites only when filtered. */
function _mvVisibleIndices() {
  if (!_mvFavOnly) return _mvFrames.map((_, i) => i);
  return _mvFrames.map((_, i) => i).filter(i => _mvFavs.has(_mvFrames[i].label));
}

/** @param {number} i show frame i (absolute index; wraps within the full set) */
function molViewerShow(i) {
  const n = _mvFrames.length; if (!n || !_mvViewer) return;
  _mvIndex = ((i % n) + n) % n;
  const f = _mvFrames[_mvIndex];
  _mvViewer.clear();
  _mvViewer.addModel(f.xyz, "xyz");
  _mvViewer.setStyle({}, { stick: { radius: 0.14 }, sphere: { scale: 0.26 } });
  _mvViewer.zoomTo();
  _mvViewer.render();
  const de = _mvDeltaE(f);
  const star = _mvFavs.has(f.label) ? "★ " : "";
  document.getElementById("mv-caption").textContent =
    `${star}${_mvIndex + 1} / ${n}  ·  ${f.label}` + (de === "" ? "" : `  ·  ΔE ${de} kcal/mol`);
  updateFavBtn();
  // highlight the active list row
  document.querySelectorAll("#mv-list .mv-row").forEach(el =>
    el.classList.toggle("active", Number(el.getAttribute("data-i")) === _mvIndex));
  const active = document.querySelector(`#mv-list .mv-row[data-i="${_mvIndex}"]`);
  if (active) active.scrollIntoView({ block: "nearest" });
}

/** @param {number} d step by d visible frames (±1), respecting the ★-only filter */
function molViewerStep(d) {
  const vis = _mvVisibleIndices();
  if (!vis.length) return;
  let pos = vis.indexOf(_mvIndex);
  pos = pos === -1 ? 0 : (pos + d + vis.length) % vis.length;
  molViewerShow(vis[pos]);
}

/** Star/unstar frame i; persist and re-render. @param {number} i */
async function toggleFav(i) {
  const f = _mvFrames[i]; if (!f) return;
  const on = !_mvFavs.has(f.label);
  if (on) _mvFavs.add(f.label); else _mvFavs.delete(f.label);   // optimistic
  renderMvList(); updateFavBtn(); updateFavOnlyBtn();
  try {
    const r = /** @type {FavoritesResult} */ (JSON.parse(await bridge.toggle_favorite(_mvSource, f.label, on)));
    if (r.ok) _mvFavs = new Set(r.labels || []);
  } catch (e) { /* keep the optimistic state if the persist call fails */ }
  renderMvList();
}

function toggleFavCurrent() { toggleFav(_mvIndex); }

/** Toggle the "★ only" filter; snap to a favorite if the current frame isn't one. */
function toggleFavOnly() {
  const anyFav = _mvFrames.some(f => _mvFavs.has(f.label));
  if (!_mvFavOnly && !anyFav) { toast("No favorites yet — star a structure first (F)."); return; }
  _mvFavOnly = !_mvFavOnly;
  updateFavOnlyBtn();
  if (_mvFavOnly && !_mvFavs.has(_mvFrames[_mvIndex].label)) {
    const vis = _mvVisibleIndices();
    if (vis.length) molViewerShow(vis[0]);
  }
}

function updateFavBtn() {
  const btn = document.getElementById("mv-fav-btn");
  if (!btn) return;
  const fav = _mvFrames[_mvIndex] && _mvFavs.has(_mvFrames[_mvIndex].label);
  btn.textContent = fav ? "★ Favorited" : "☆ Favorite";
  btn.classList.toggle("mv-on", !!fav);
}

function updateFavOnlyBtn() {
  const btn = document.getElementById("mv-favonly-btn");
  if (btn) btn.classList.toggle("mv-on", _mvFavOnly);
}

/** Write the starred structures to a favorites/ folder next to the source. */
async function exportFavorites() {
  const favFrames = _mvFrames.filter(f => _mvFavs.has(f.label))
    .map(f => ({ label: f.label, xyz: f.xyz }));
  if (!favFrames.length) { toast("No favorites to export — star some first (F)."); return; }
  try {
    const r = /** @type {ExportResult} */ (JSON.parse(await bridge.export_frames(
      _mvSourceKind, _mvSourceRef, JSON.stringify(favFrames))));
    if (!r.ok) { failNotify(r.error || "Could not export favorites."); return; }
    toast(`Exported ${r.count} favorite${r.count === 1 ? "" : "s"} to ${r.folder}`);
    appendLog(`Exported ${r.count} favorite .xyz to ${r.folder}`, "ok");
  } catch (e) { failNotify("Could not export favorites."); }
}

function closeMolViewer() {
  document.getElementById("mol-viewer").style.display = "none";
  // release GPU memory: clear the scene but keep the viewer/context for reuse
  if (_mvViewer) { _mvViewer.clear(); _mvViewer.render(); }
  _mvFrames = [];
}
