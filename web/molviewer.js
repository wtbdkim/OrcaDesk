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
// "frames" (a list of .xyz structures) or "volume" (orbital / density cubes).
// The two share the modal and the GLViewer — one WebGL context, created once —
// but nothing else, so the three list/step entry points dispatch on this.
let _mvMode = "frames";

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
  _mvMode = "frames";
  _mvFrames = frames; _mvIndex = 0; _mvFavOnly = false;
  _mvSourceKind = kind; _mvSourceRef = ref || "";
  _mvSource = `${kind}:${ref || ""}`;
  // load persisted favorites for this source
  _mvFavs = new Set();
  try {
    const fr = /** @type {FavoritesResult} */ (JSON.parse(await bridge.get_favorites(_mvSource)));
    if (fr.ok) _mvFavs = new Set(fr.labels || []);
  } catch (e) { /* favorites are best-effort — never block the viewer */ }
  // energies drive the ΔE column; compute the min once for the whole set
  _mvEmin = Math.min(...frames.map(f => (typeof f.energy === "number" ? f.energy : Infinity)));
  renderMvList();
  if (!_mvOpenStage(title || "Structure viewer")) return;
  updateFavOnlyBtn();
  molViewerShow(0);
}

/** Show the modal, create/resize the shared GLViewer, install the key handler,
 *  and switch the footer bar to the active mode. Returns false when 3Dmol is
 *  missing (the only way this fails). Shared by both modes — one WebGL context
 *  for the whole app, created lazily on first open and reused. */
function _mvOpenStage(title) {
  const $3Dmol = window["$3Dmol"] || window["3Dmol"];
  if (!$3Dmol) { failNotify("3D viewer failed to load."); return false; }
  document.getElementById("mv-title").textContent = title;
  const overlay = document.getElementById("mol-viewer");
  overlay.style.display = "flex";
  const vol = _mvMode === "volume";
  document.getElementById("mv-bar").style.display = vol ? "none" : "";
  document.getElementById("mv-vol-bar").style.display = vol ? "" : "none";
  document.getElementById("mv-hint").style.display = vol ? "none" : "";
  document.getElementById("mv-hint-vol").style.display = vol ? "" : "none";
  // create the GLViewer lazily, after the container is visible & sized
  if (!_mvViewer) {
    const bg = getComputedStyle(document.documentElement)
      .getPropertyValue("--card").trim() || "#18181b";
    _mvViewer = $3Dmol.createViewer(document.getElementById("mv-gl"),
      { backgroundColor: bg });
  }
  _mvViewer.resize();
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
  return true;
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
  if (_mvMode === "volume") return renderMvVolList();
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
  if (_mvMode === "volume") { mvVolShow(i); return; }
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
  if (_mvMode === "volume") {
    const n = _mvVolPicks.length;
    if (n) mvVolShow(((_mvVolIndex + d) % n + n) % n);
    return;
  }
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

// Favorites are a property of a *structure*, not of an isosurface, so the F key
// is inert in volume mode rather than starring whatever frame was last shown.
function toggleFavCurrent() { if (_mvMode !== "volume") toggleFav(_mvIndex); }

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
  mvVolReset();
}

/* ---------- orbital / electron-density mode (cubes from orca_plot) ----------
 * Same modal, same GLViewer; the list holds *plots* instead of structures and
 * the stage draws isosurfaces over the molecule.
 *
 * The design point worth keeping: the grid the backend generates and the
 * smoothness of what you see are separate knobs. A molecular orbital is a sum
 * of Gaussians — an analytic, smooth function — so a coarse sampling is not
 * missing physics, it is missing *mesh*. 3Dmol's marching cubes takes a
 * `smoothness` (Laplacian mesh smoothing) parameter, and that is what turns a
 * 60³ grid into a clean surface. Raising the grid costs cube-of-the-number
 * bytes and seconds for detail the isosurface cannot show; raising smoothness
 * costs nothing. So the default is grid 60 + smoothness 8, and the isovalue
 * slider re-meshes the cube already in memory — no backend round-trip, which
 * is what makes it feel instant. */

/** @typedef {{kind:string, index:number, operator:number, label:string, sub:string}} MvPick */
/** @type {MvPick[]} */ let _mvVolPicks = [];
let _mvVolIndex = 0;
// How the backend addresses the wavefunction: "calc:<name>" (a queued calc) or
// "file:<path>" (a result opened from disk / picked out of the workspace).
let _mvVolSource = "";
let _mvVolBase = "";              // filename stem the cubes are named from
let _mvVolGrid = 60;
/** @type {number[]} */ let _mvVolGrids = [40, 60, 80];
/** @type {string[]} */ let _mvVolCached = [];   // cube filenames already on disk
let _mvIso = 0.05;
/** @type {any} */ let _mvVolData = null;        // parsed $3Dmol.VolumeData
let _mvVolSigned = true;
let _mvVolTitle = "";
let _mvVolBusy = false;
// Bumped on every pick/grid change; an in-flight generate compares against it
// before touching the stage, so clicking through orbitals never renders a
// stale cube over a newer one (P44).
let _mvVolSeq = 0;

/** A design token's value, for the places that need a real colour string rather
 *  than CSS — 3Dmol's WebGL materials. Same trick openMolViewer uses for the
 *  stage background. @param {string} name */
function _cssColor(name) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || "#888888";
}

/** Slider position (0..100) to isovalue, on a log scale from 0.001 to 0.5.
 *  Linear would be useless: a spin density is drawn near 0.005 and an orbital
 *  near 0.05, a factor of ten apart at the bottom of the range. */
function _isoFromSlider(v) { return 0.001 * Math.pow(500, Number(v) / 100); }
function _sliderFromIso(iso) {
  const v = 100 * Math.log(Math.max(0.001, iso) / 0.001) / Math.log(500);
  return Math.max(0, Math.min(100, Math.round(v)));
}

/** Open the orbital / density viewer for the result on the Results tab — a
 *  queued calculation or a file opened from disk; both have a .gbw beside their
 *  output, which is all orca_plot needs.
 *  `orbs` is the orbital list the Results tab already parsed.
 *  @param {OrbitalPayload[]} orbs */
async function viewOrbitals3D(orbs) {
  const source = _currentResultName ? "calc:" + _currentResultName
               : (_currentResultPath ? "file:" + _currentResultPath : "");
  if (!source) return;
  let opts;
  try { opts = /** @type {PlotOptionsResult} */ (JSON.parse(await bridge.get_plot_options(source))); }
  catch (e) { failNotify("Could not read the result folder."); return; }
  if (!opts.ok) { failNotify(opts.error || "Could not read the result folder."); return; }
  const base = opts.base || "";
  if (!opts.has_gbw) {
    failNotify("No wavefunction file (" + base + ".gbw) next to this result — " +
               "3D orbitals need a finished ORCA job.");
    return;
  }
  _mvMode = "volume";
  _mvVolSource = source;
  _mvVolBase = base;
  _mvVolGrids = (opts.grids && opts.grids.length) ? opts.grids : [40, 60, 80];
  _mvVolGrid = opts.default_grid || 60;
  _mvVolCached = opts.cached || [];
  _mvVolPicks = _mvBuildPicks(orbs || [], opts.kinds || ["mo"]);
  if (!_mvVolPicks.length) { failNotify("Nothing to plot for this calculation."); return; }
  // start on the HOMO when there is one — the orbital people actually want
  const homo = _mvVolPicks.findIndex(p => p.sub.indexOf("HOMO ") === 0);
  _mvVolIndex = homo >= 0 ? homo : 0;
  _mvVolData = null;
  _fillGridSelect();
  renderMvList();
  if (!_mvOpenStage(base + " — orbitals & density")) return;
  mvVolShow(_mvVolIndex);
}

/** Build the pick list: the densities this wavefunction supports, then every
 *  parsed orbital level (frontier ones labelled HOMO/LUMO).
 *  @param {OrbitalPayload[]} orbs @param {string[]} kinds */
function _mvBuildPicks(orbs, kinds) {
  /** @type {MvPick[]} */ const picks = [];
  if (kinds.indexOf("eldens") >= 0)
    picks.push({ kind: "eldens", index: 0, operator: 0, label: "Electron density", sub: "" });
  if (kinds.indexOf("spindens") >= 0)
    picks.push({ kind: "spindens", index: 0, operator: 0, label: "Spin density", sub: "" });
  let homoI = -1;
  orbs.forEach((o, i) => { if (o.occ > 0.01) homoI = i; });
  orbs.forEach((o, i) => {
    let sub = "";
    if (homoI >= 0) {
      if (i === homoI) sub = "HOMO";
      else if (i === homoI + 1) sub = "LUMO";
      else if (i < homoI) sub = "HOMO-" + (homoI - i);
      else sub = "LUMO+" + (i - homoI - 1);
    }
    picks.push({ kind: "mo", index: o.idx, operator: 0,
                 label: "MO " + o.idx, sub: sub + "  " + o.ev.toFixed(2) + " eV" });
  });
  return picks;
}

/** The cube file a pick produces — mirrors core/plot.cube_filename, so the list
 *  can mark which picks are already on disk (those open with no orca_plot run).
 *  Grid-qualified like the Python side, so switching the grid correctly shows
 *  the not-yet-generated picks as not generated.
 *  @param {MvPick} p */
function _mvCubeName(p) {
  const stem = p.kind === "mo"
    ? _mvVolBase + ".mo" + p.index + (p.operator ? "b" : "a")
    : _mvVolBase + "." + p.kind;
  return stem + ".g" + _mvVolGrid + ".cube";
}

function renderMvVolList() {
  document.getElementById("mv-list").innerHTML = _mvVolPicks.map((p, i) => {
    const ready = _mvVolCached.indexOf(_mvCubeName(p)) >= 0;
    const tip = ready ? "Already generated — opens instantly" : "Not generated yet";
    return '<div class="mv-row ' + (i === _mvVolIndex ? "active" : "") + '" data-i="' + i + '"' +
           ' onclick="mvVolShow(' + i + ')">' +
           '<span class="mv-star mv-dot' + (ready ? " ready" : "") + '" title="' + tip + '">' +
           (ready ? "●" : "○") + '</span>' +
           '<span class="mv-row-label">' + escapeHtml(p.label) + '</span>' +
           '<span class="mv-row-de">' + escapeHtml(p.sub) + '</span></div>';
  }).join("");
}

function _fillGridSelect() {
  const sel = /** @type {HTMLSelectElement} */ (document.getElementById("mv-grid"));
  if (!sel) return;
  sel.innerHTML = _mvVolGrids.map(g =>
    '<option value="' + g + '"' + (g === _mvVolGrid ? " selected" : "") + ">" + g + "³</option>").join("");
}

/** Select pick i: generate its cube if needed, then draw it. @param {number} i */
async function mvVolShow(i) {
  const n = _mvVolPicks.length;
  if (!n || !_mvViewer) return;
  _mvVolIndex = ((i % n) + n) % n;
  const pick = _mvVolPicks[_mvVolIndex];
  renderMvVolList();
  const active = document.querySelector('#mv-list .mv-row[data-i="' + _mvVolIndex + '"]');
  if (active) active.scrollIntoView({ block: "nearest" });

  const seq = ++_mvVolSeq;
  _mvVolBusy = true;
  _mvVolCaption(pick.label + " — generating at " + _mvVolGrid + "³ …");
  const payload = JSON.stringify({
    source: _mvVolSource, kind: pick.kind, index: pick.index,
    operator: pick.operator, grid: _mvVolGrid });

  // The backend runs one orca_plot at a time and REFUSES a second, handing back
  // the status of the job already in flight. That reply describes someone
  // else's request, so it must not be mistaken for ours: polling it to "done"
  // and then fetching the cube would draw the previous orbital under this
  // one's label. Observed exactly that way. So: keep asking until the backend
  // reports it is working on OUR request, and check again before taking data.
  let job = null;
  for (;;) {
    try { job = /** @type {CubeJob} */ (JSON.parse(await bridge.generate_cube(payload))); }
    catch (e) { _mvVolFail(seq, "Could not start orca_plot."); return; }
    if (seq !== _mvVolSeq) return;                     // superseded by a newer pick
    if (_mvJobIs(job, pick)) break;
    _mvVolCaption(pick.label + " — waiting for the previous plot …");
    await new Promise(r => setTimeout(r, 250));
    if (seq !== _mvVolSeq) return;
  }
  while (job && job.state === "running") {
    await new Promise(r => setTimeout(r, 250));
    if (seq !== _mvVolSeq) return;
    try { job = /** @type {CubeJob} */ (JSON.parse(await bridge.get_cube_status())); }
    catch (e) { _mvVolFail(seq, "Lost contact with orca_plot."); return; }
    if (seq !== _mvVolSeq) return;
  }
  if (!job || job.state !== "done" || !_mvJobIs(job, pick)) {
    _mvVolFail(seq, (job && job.error) || "orca_plot failed."); return;
  }

  let data;
  try { data = /** @type {CubeDataResult} */ (JSON.parse(await bridge.get_cube_data())); }
  catch (e) { _mvVolFail(seq, "Could not read the cube file."); return; }
  if (seq !== _mvVolSeq) return;
  if (!data.ok) { _mvVolFail(seq, data.error || "Could not read the cube file."); return; }

  const $3Dmol = window["$3Dmol"] || window["3Dmol"];
  _mvVolBusy = false;
  _mvVolTitle = data.title || pick.label;
  _mvVolSigned = data.signed !== false;
  _mvIso = data.isovalue || 0.05;
  const slider = /** @type {HTMLInputElement} */ (document.getElementById("mv-iso"));
  if (slider) slider.value = String(_sliderFromIso(_mvIso));
  const lbl = document.getElementById("mv-iso-val");
  if (lbl) lbl.textContent = _mvIso.toFixed(3);
  // remember that this one is on disk now, so the list dot flips to "instant"
  const fname = _mvCubeName(pick);
  if (_mvVolCached.indexOf(fname) < 0) { _mvVolCached.push(fname); renderMvVolList(); }

  // Parse the cube ONCE: the molecule comes from its atom block and the volume
  // from its values, and the isovalue slider re-meshes this same object.
  _mvViewer.clear();
  _mvViewer.addModel(data.text, "cube");
  _mvViewer.setStyle({}, { stick: { radius: 0.1 }, sphere: { scale: 0.18 } });
  _mvVolData = new $3Dmol.VolumeData(data.text, "cube");
  mvRenderCube(true);
}

/** Does this job status describe the plot we asked for? The status carries the
 *  full request (kind/index/operator/grid) precisely so the answer is decidable
 *  — without it a refused request is indistinguishable from an accepted one.
 *  @param {CubeJob} job @param {MvPick} pick */
function _mvJobIs(job, pick) {
  return !!job && job.kind === pick.kind && job.index === pick.index
      && job.operator === pick.operator && job.grid === _mvVolGrid;
}

/** @param {number} seq @param {string} msg */
function _mvVolFail(seq, msg) {
  if (seq !== _mvVolSeq) return;
  _mvVolBusy = false;
  _mvVolCaption(msg);
  failNotify(msg);
}

/** Draw the isosurface(s) at the current isovalue. Removes only the shapes, so
 *  the molecule and the camera survive a slider move. @param {boolean} zoom */
function mvRenderCube(zoom) {
  if (!_mvViewer || !_mvVolData) return;
  _mvViewer.removeAllShapes();
  // smoothness = Laplacian mesh smoothing on the marching-cubes output. This is
  // the knob that makes a 60³ sampling look like a surface; raising the GRID
  // instead would cost cube-of-the-number bytes and seconds for detail an
  // isosurface cannot show.
  const common = { opacity: 0.85, smoothness: 8 };
  if (_mvVolSigned) {
    _mvViewer.addIsosurface(_mvVolData, Object.assign({ isoval: _mvIso, color: _cssColor("--orb-pos") }, common));
    _mvViewer.addIsosurface(_mvVolData, Object.assign({ isoval: -_mvIso, color: _cssColor("--orb-neg") }, common));
  } else {
    _mvViewer.addIsosurface(_mvVolData, Object.assign({ isoval: _mvIso, color: _cssColor("--orb-dens") }, common));
  }
  if (zoom) _mvViewer.zoomTo();
  _mvViewer.render();
  _mvVolCaption(_mvVolTitle + "  ·  " + (_mvVolIndex + 1) + " / " + _mvVolPicks.length +
                "  ·  isovalue " + _mvIso.toFixed(3) + " a.u.  ·  grid " + _mvVolGrid + "³" +
                (_mvVolSigned ? "  ·  blue +, red −" : ""));
}

/** @param {string} text */
function _mvVolCaption(text) {
  const el = document.getElementById("mv-vol-caption");
  if (el) el.textContent = text;
}

/** Isovalue slider: re-mesh the cube already in memory — no backend call, which
 *  is the whole reason a coarse grid is enough. @param {string|number} v */
function mvSetIso(v) {
  _mvIso = _isoFromSlider(v);
  const lbl = document.getElementById("mv-iso-val");
  if (lbl) lbl.textContent = _mvIso.toFixed(3);
  if (!_mvVolBusy) mvRenderCube(false);
}

/** Grid selector: this one DOES need a new cube, so it re-runs the current pick.
 *  @param {string|number} g */
function mvSetGrid(g) {
  _mvVolGrid = Number(g) || 60;
  mvVolShow(_mvVolIndex);
}

function mvVolReset() {
  _mvVolSeq++;              // orphan any in-flight generate
  _mvVolPicks = []; _mvVolData = null; _mvVolBusy = false;
  _mvVolSource = ""; _mvVolBase = "";
}
