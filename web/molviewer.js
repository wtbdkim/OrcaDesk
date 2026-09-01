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

/** Stop a 3Dmol viewer from drawing into a container that is currently hidden.
 *
 *  3Dmol registers its OWN window-resize listener per viewer
 *  (`addEventListener("resize", this.resize.bind(this))`) and that listener
 *  renders unconditionally — its resize() ends in `this.show()`, which calls
 *  `renderer.render(scene, camera)` whatever the size is. A hidden container is
 *  a 0x0 canvas, so every window resize made EVERY viewer this app has ever
 *  created draw into a zero-size framebuffer: one
 *  GL_INVALID_FRAMEBUFFER_OPERATION for the glClear and one for the
 *  glDrawElements, per viewer, per resize. Wasted GPU work, a console the real
 *  warnings drown in, and on some drivers a step toward a lost context.
 *
 *  The listener is bound to the prototype method at construction, so replacing
 *  the instance's resize() would not intercept it — but resize() reaches the
 *  GL through `this.show()` on the instance, and `this` is the viewer itself
 *  (3Dmol sets `this._viewer = this`). Guarding show() and render() there
 *  catches every path. Nothing is lost by skipping: a hidden canvas has no
 *  pixels to keep, and both call sites (_mvOpenStage, _stViewer) resize() the
 *  viewer once its container is visible again, which redraws it.
 *
 *  @param {any} v the viewer from createViewer
 *  @param {HTMLElement} node its container
 *  @returns {any} v */
function glGuardViewer(v, node) {
  if (!v || v._odSizeGuarded) return v;
  const drawable = () => !!(node && node.clientWidth && node.clientHeight);
  for (const name of ["show", "render"]) {
    const inner = v[name];
    if (typeof inner !== "function") continue;
    v[name] = function (/** @type {any[]} */ ...args) {
      if (!drawable()) return this;
      return inner.apply(this, args);
    };
  }
  v._odSizeGuarded = true;
  return v;
}

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
  _mvListVisible(true);          // the ESP map puts it away; frames need it back
  renderMvList();
  if (!_mvOpenStage(title || "Structure viewer")) return;
  updateFavOnlyBtn();
  molViewerShow(0);
}

/** Show the modal, create/resize the shared GLViewer, install the key handler,
 *  and switch the footer bar to the active mode. Returns false when 3Dmol is
 *  missing or the display cannot give it a WebGL context — the caller must
 *  stop, since neither the viewer nor its key handler exists. Shared by both
 *  modes — one WebGL context for the whole app, created lazily on first open
 *  and reused. */
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
    // No hex fallback (DESIGN §15.1: tokens only): "#18181b" is the DARK
    // --card, so on a light theme a token read that came back empty painted the
    // viewer near-black. An empty string lets 3Dmol use its own default, which
    // is at least not the wrong theme's colour.
    const bg = getComputedStyle(document.documentElement)
      .getPropertyValue("--card").trim();
    const glNode = document.getElementById("mv-gl");
    try {
      _mvViewer = glGuardViewer(
        $3Dmol.createViewer(glNode, bg ? { backgroundColor: bg } : {}), glNode);
    } catch (e) {
      // No WebGL context (a remote desktop, a VM, a blocklisted GPU, a driver
      // reset). Thrown from here the whole open() rejected: the overlay was
      // already display:flex, the key handler was never installed, and the user
      // got a full-screen empty modal with a dead Esc and no message at all.
      _mvViewer = null;
      overlay.style.display = "none";
      failNotify("3D rendering is unavailable on this display — the graphics "
                 + "driver would not provide a WebGL context.");
      return false;
    }
    if (!_mvViewer) {
      overlay.style.display = "none";
      failNotify("3D rendering is unavailable on this display.");
      return false;
    }
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
    // The star lighting up IS the confirmation, so a star that could not be
    // written must say so — otherwise the user works through a whole ensemble
    // and finds every star gone at the next launch. The list still follows what
    // the backend holds in memory, so the session itself keeps working.
    else if (r.error) { _mvFavs = new Set(r.labels || _mvFavs); failNotify(r.error); }
  } catch (e) { /* keep the optimistic state if the channel call itself fails */ }
  // Unstarring the LAST favorite leaves "★ only" filtering to nothing: the
  // visible list is empty, so the arrows and Prev/Next go dead while the button
  // still reads as on. The filter refuses to be switched on with no favorites;
  // it must switch itself off for the same reason.
  if (_mvFavOnly && !_mvFrames.some(fr => _mvFavs.has(fr.label))) {
    _mvFavOnly = false;
    updateFavOnlyBtn();
  }
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
  // Release GPU memory: clear the scene but keep the viewer/context for reuse.
  // Before the overlay is hidden, not after — a render into an already
  // display:none container draws into a 0x0 framebuffer, which is both an
  // invalid GL op and a no-op that leaves the old buffers on the card.
  if (_mvViewer) { _mvViewer.clear(); _mvViewer.render(); }
  document.getElementById("mol-viewer").style.display = "none";
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

/* ---- the ESP map ----
 * The one pick built from TWO cubes: the potential is not an isosurface of
 * itself, it is a colour painted on the electron-density surface, so the map is
 * `eldens` (the shape) plus `esp` (the colour) sampled on the same grid. The
 * isovalue slider therefore steers the DENSITY level here, and a second slider
 * steers the colour scale — two independent knobs over one drawing.
 *
 * ESP is also the one plot whose cost is minutes rather than seconds (49 s at
 * 40³ on 52 atoms / 987 basis functions; ~2.8 min at 60³), which is why it
 * keeps its own, coarser grid instead of sharing the orbitals'. */
/** @type {any} */ let _mvEspData = null;        // the potential's VolumeData
let _mvEspRange = 0.05;                          // colour-scale half-width, a.u.
let _mvEspSurfaceIso = 0.002;                    // density level the map is drawn on
// Grid per KIND, not per viewer: switching from an orbital to the ESP map must
// not silently re-plot the orbital at 40³, nor the map at 60³ (D60).
/** @type {Object<string, number>} */ const _mvGridByKind = {};
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

/** The isovalue enclosing `frac` of the field's total psi-squared — i.e. the
 *  surface that contains that much of the electron probability. Computed from
 *  the values 3Dmol has already parsed, so it costs a sort and no round-trip.
 *  @param {ArrayLike<number>} data @param {number} frac */
function _isoForFraction(data, frac) {
  const n = data.length;
  if (!n) return 0;
  // NaN-safe on purpose. 3Dmol builds this array with
  // Float32Array.from(text.split(/[\s\r]+/), parseFloat), and a cube file's
  // trailing separator yields one empty token — so `data` carries a single NaN
  // past the real values (216001 entries for a 60³ grid). One NaN poisons the
  // sum, `total > 0` goes false, and the whole fit silently returns 0.
  const sq = new Float64Array(n);
  let total = 0;
  for (let i = 0; i < n; i++) {
    const x = data[i];
    const v = Number.isFinite(x) ? x * x : 0;
    sq[i] = v; total += v;
  }
  if (!(total > 0)) return 0;
  sq.sort();                       // typed-array sort is numeric, ascending
  let run = 0;
  for (let i = n - 1; i >= 0; i--) {
    run += sq[i];
    if (run >= frac * total) return Math.sqrt(sq[i]);
  }
  return 0;
}

/** Where to open the isovalue slider for this field.
 *
 *  A fixed 0.05 is the convention for an orbital, and it is right — for a small
 *  molecule. It is not a property of orbitals, it is a property of *localized*
 *  ones. CB8's HOMO is spread over eight carbonyls and peaks at 0.0996, so only
 *  222 of 216,000 grid points clear 0.05: the surface exists, and is invisible.
 *  The viewer looked broken on exactly the molecules worth looking at.
 *
 *  So take the conventional value as a CEILING and lower it when the data has
 *  nothing up there, using the level that encloses 90% of psi-squared. Measured:
 *  that level is 0.060 for water's HOMO (so the cap holds and the conventional
 *  picture is unchanged) and 0.0097 for CB8's (so it drops, and a real surface
 *  appears). Never raises — a level above convention would be a claim about the
 *  orbital that convention does not make.
 *  @param {ArrayLike<number>} data @param {number} cap per-kind conventional value */
function _defaultIso(data, cap) {
  const fitted = _isoForFraction(data, ISO_ENCLOSED_FRACTION);
  if (!(fitted > 0)) return cap;
  return Math.max(ISO_MIN, Math.min(cap, fitted));
}

const ISO_ENCLOSED_FRACTION = 0.90;
const ISO_MIN = 0.001;             // the slider's floor; below it nothing reads

/** Slider position (0..100) to isovalue, on a log scale from 0.001 to 0.5.
 *  Linear would be useless: a spin density is drawn near 0.005 and an orbital
 *  near 0.05, a factor of ten apart at the bottom of the range. */
function _isoFromSlider(v) { return 0.001 * Math.pow(500, Number(v) / 100); }
function _sliderFromIso(iso) {
  const v = 100 * Math.log(Math.max(0.001, iso) / 0.001) / Math.log(500);
  return Math.max(0, Math.min(100, Math.round(v)));
}

/** Which wavefunction the Results tab is showing: a queued calc or a file
 *  opened from disk — both have a .gbw beside their output, which is all
 *  orca_plot needs. "" when there is no result on screen. */
function _mvPlotSource() {
  return _currentResultName ? "calc:" + _currentResultName
       : (_currentResultPath ? "file:" + _currentResultPath : "");
}

/** Ask the backend what this result can be plotted as and adopt the answer into
 *  the viewer's volume state. Shared by both entry points — the orbital browser
 *  and the ESP map — so neither can drift from the other's idea of the grid
 *  choices or the ESP conventions (P4).
 *  @param {string} what named in the "no wavefunction" refusal
 *  @returns {Promise<PlotOptionsResult|null>} null when it cannot be plotted */
async function _mvVolSetup(what) {
  const source = _mvPlotSource();
  if (!source) return null;
  let opts;
  try { opts = /** @type {PlotOptionsResult} */ (JSON.parse(await bridge.get_plot_options(source))); }
  catch (e) { failNotify("Could not read the result folder."); return null; }
  if (!opts.ok) { failNotify(opts.error || "Could not read the result folder."); return null; }
  const base = opts.base || "";
  if (!opts.has_gbw) {
    failNotify("No wavefunction file (" + base + ".gbw) next to this result — " +
               what + " needs a finished ORCA job.");
    return null;
  }
  _mvMode = "volume";
  _mvVolSource = source;
  _mvVolBase = base;
  _mvVolGrids = (opts.grids && opts.grids.length) ? opts.grids : [40, 60, 80];
  _mvVolGrid = opts.default_grid || 60;
  // per-kind grid + the ESP conventions, all decided by the backend (P4)
  _mvGridByKind.mo = _mvGridByKind.eldens = _mvGridByKind.spindens = _mvVolGrid;
  _mvGridByKind.esp = opts.esp_grid || 40;
  _mvEspSurfaceIso = opts.esp_surface_iso || 0.002;
  _mvEspRange = opts.esp_range || 0.05;
  _mvVolCached = opts.cached || [];
  _mvVolData = null;
  _mvEspData = null;
  return opts;
}

/** Open the orbital / density viewer for the result on the Results tab.
 *  `orbs` is the orbital list the Results tab already parsed.
 *  @param {OrbitalPayload[]} orbs */
async function viewOrbitals3D(orbs) {
  const opts = await _mvVolSetup("3D orbitals");
  if (!opts) return;
  _mvVolPicks = _mvBuildPicks(orbs || [], opts.kinds || ["mo"]);
  if (!_mvVolPicks.length) { failNotify("Nothing to plot for this calculation."); return; }
  // start on the HOMO when there is one — the orbital people actually want
  const homo = _mvVolPicks.findIndex(p => p.sub.indexOf("HOMO ") === 0);
  _mvVolIndex = homo >= 0 ? homo : 0;
  _mvListVisible(true);
  _fillGridSelect();
  renderMvList();
  if (!_mvOpenStage((opts.base || "") + " — orbitals & density")) return;
  mvVolShow(_mvVolIndex);
}

/** Open the ESP map — its own entry point, from its own card on the Results
 *  tab. It is one figure rather than a set to click through, so the frame list
 *  is put away and the stage takes the whole panel. */
async function viewEspMap() {
  const opts = await _mvVolSetup("an ESP map");
  if (!opts) return;
  if ((opts.kinds || []).indexOf("esp") < 0) {
    failNotify("This wavefunction has no SCF density to compute a potential from.");
    return;
  }
  _mvVolPicks = [{ kind: "esp", index: 0, operator: 0, label: "ESP map", sub: "" }];
  _mvVolIndex = 0;
  _mvListVisible(false);
  _fillGridSelect();
  if (!_mvOpenStage((opts.base || "") + " — ESP map")) return;
  mvVolShow(0);
}

/** Show or put away the left-hand pick list. @param {boolean} on */
function _mvListVisible(on) {
  const list = document.getElementById("mv-list");
  if (list) list.style.display = on ? "" : "none";
}

/** Build the pick list: the densities this wavefunction supports, then every
 *  parsed orbital level (frontier ones labelled HOMO/LUMO).
 *  @param {OrbitalPayload[]} orbs @param {string[]} kinds */
function _mvBuildPicks(orbs, kinds) {
  /** @type {MvPick[]} */ const picks = [];
  if (kinds.indexOf("eldens") >= 0)
    picks.push({ kind: "eldens", index: 0, operator: 0, label: "Electron density", sub: "" });
  // The ESP map is deliberately NOT in this list: it is not another orbital to
  // click through. It costs minutes rather than a second, it is one figure
  // rather than a set, and it answers a different question — so it has its own
  // card under Final geometry and its own entry point (viewEspMap).
  if (kinds.indexOf("spindens") >= 0)
    picks.push({ kind: "spindens", index: 0, operator: 0, label: "Spin density", sub: "" });
  // An unrestricted run has two manifolds. They are separate orbital sets:
  // orca_plot addresses them by OPERATOR (0 = alpha, 1 = beta) and the index
  // restarts at 0 in each, so "MO 4" alone names two different orbitals — and
  // HOMO/LUMO are relative to the manifold they live in, while the overall
  // frontier pair (which the parser decides, across both) may straddle them.
  const spinName = { a: "\u03b1", b: "\u03b2" };
  const manifolds = [...new Set(orbs.map(o => o.spin))];
  for (const spin of manifolds) {
    const set = orbs.filter(o => o.spin === spin);
    let homoI = -1;
    set.forEach((o, i) => { if (o.occ > 0.01) homoI = i; });
    set.forEach((o, i) => {
      let sub = "";
      if (homoI >= 0) {
        if (i === homoI) sub = "HOMO";
        else if (i === homoI + 1) sub = "LUMO";
        else if (i < homoI) sub = "HOMO-" + (homoI - i);
        else sub = "LUMO+" + (i - homoI - 1);
      }
      const tag = spin ? " " + (spinName[spin] || spin) : "";
      picks.push({ kind: "mo", index: o.idx, operator: spin === "b" ? 1 : 0,
                   label: "MO " + o.idx + tag,
                   sub: sub + tag + "  " + o.ev.toFixed(2) + " eV" });
    });
  }
  return picks;
}

/** The cube file a pick produces — mirrors core/plot.cube_filename, so the list
 *  can mark which picks are already on disk (those open with no orca_plot run).
 *  Grid-qualified like the Python side, so switching the grid correctly shows
 *  the not-yet-generated picks as not generated.
 *  @param {MvPick} p */
function _mvCubeName(p, grid) {
  const g = grid || _mvGridFor(p.kind);
  // orca_plot names an ESP after the DENSITY it came from, not after the plot
  // type — water.scfp.esp.cube, not water.esp.cube (core/plot.plot_output_name).
  const stem = p.kind === "mo"
    ? _mvVolBase + ".mo" + p.index + (p.operator ? "b" : "a")
    : (p.kind === "esp" ? _mvVolBase + ".scfp.esp" : _mvVolBase + "." + p.kind);
  return stem + ".g" + g + ".cube";
}

/** The grid this kind plots at. @param {string} kind */
function _mvGridFor(kind) {
  return _mvGridByKind[kind] || _mvVolGrid;
}

/** The two requests an ESP map is built from, in the order they are fetched.
 *  Both ride the ESP grid: the colour is sampled per density-surface vertex, so
 *  the two fields have to be on the SAME grid — plotting the density at the
 *  orbitals' 60³ and the potential at 40³ would be two different boxes.
 *  @returns {MvPick[]} */
function _mvEspParts() {
  return [{ kind: "eldens", index: 0, operator: 0, label: "density surface", sub: "" },
          { kind: "esp", index: 0, operator: 0, label: "potential", sub: "" }];
}

/** Every cube a pick needs on disk before it can open instantly. The ESP map is
 *  the only pick that needs two. @param {MvPick} p @returns {string[]} */
function _mvCubesFor(p) {
  if (p.kind !== "esp") return [_mvCubeName(p)];
  return _mvEspParts().map(q => _mvCubeName(q, _mvGridFor("esp")));
}

/** Is every cube this pick needs already on disk? @param {MvPick} p */
function _mvPickCached(p) {
  return _mvCubesFor(p).every(n => _mvVolCached.indexOf(n) >= 0);
}

function renderMvVolList() {
  document.getElementById("mv-list").innerHTML = _mvVolPicks.map((p, i) => {
    const ready = _mvPickCached(p);
    const tip = ready ? "Already generated — opens instantly"
      : (p.kind === "esp" ? "Not generated yet — the potential takes minutes"
                          : "Not generated yet");
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

/** Generate (if needed) and read ONE cube. Returns its CubeDataResult, or null
 *  when the request was superseded or failed — the caller has already been told
 *  in the failure case.
 *
 *  The backend runs one orca_plot at a time and REFUSES a second, handing back
 *  the status of the job already in flight. That reply describes someone else's
 *  request, so it must not be mistaken for ours: polling it to "done" and then
 *  fetching the cube would draw the previous orbital under this one's label.
 *  Observed exactly that way. So: keep asking until the backend reports it is
 *  working on OUR request, and check again before taking data.
 *  @param {MvPick} part what to plot @param {number} grid
 *  @param {number} seq the caller's _mvVolSeq stamp
 *  @param {string} waitLabel what the caption calls this while it runs */
async function _mvFetchCube(part, grid, seq, waitLabel) {
  const payload = JSON.stringify({
    source: _mvVolSource, kind: part.kind, index: part.index,
    operator: part.operator, grid: grid });
  /** @type {CubeJob|null} */ let job = null;
  const mine = (j) => !!j && j.kind === part.kind && j.index === part.index
    && j.operator === part.operator && j.grid === grid;
  for (;;) {
    try { job = /** @type {CubeJob} */ (JSON.parse(await bridge.generate_cube(payload))); }
    catch (e) { _mvVolFail(seq, "Could not start orca_plot."); return null; }
    if (seq !== _mvVolSeq) return null;                // superseded by a newer pick
    if (mine(job)) break;
    _mvVolCaption(waitLabel + " — waiting for the previous plot …");
    await new Promise(r => setTimeout(r, 250));
    if (seq !== _mvVolSeq) return null;
  }
  while (job && job.state === "running") {
    await new Promise(r => setTimeout(r, 250));
    if (seq !== _mvVolSeq) return null;
    try { job = /** @type {CubeJob} */ (JSON.parse(await bridge.get_cube_status())); }
    catch (e) { _mvVolFail(seq, "Lost contact with orca_plot."); return null; }
    if (seq !== _mvVolSeq) return null;
  }
  if (!job || job.state !== "done" || !mine(job)) {
    _mvVolFail(seq, (job && job.error) || "orca_plot failed."); return null;
  }
  let data;
  try { data = /** @type {CubeDataResult} */ (JSON.parse(await bridge.get_cube_data())); }
  catch (e) { _mvVolFail(seq, "Could not read the cube file."); return null; }
  if (seq !== _mvVolSeq) return null;
  if (!data.ok) { _mvVolFail(seq, data.error || "Could not read the cube file."); return null; }
  // it is on disk now, so the list dot flips to "instant"
  const fname = _mvCubeName(part, grid);
  if (_mvVolCached.indexOf(fname) < 0) { _mvVolCached.push(fname); renderMvVolList(); }
  return data;
}

/** Select pick i: generate its cube(s) if needed, then draw it. @param {number} i */
async function mvVolShow(i) {
  const n = _mvVolPicks.length;
  if (!n || !_mvViewer) return;
  _mvVolIndex = ((i % n) + n) % n;
  const pick = _mvVolPicks[_mvVolIndex];
  const grid = _mvGridFor(pick.kind);
  _mvVolGrid = grid;                   // the selector shows this kind's grid
  _fillGridSelect();
  _mvEspControls(pick.kind === "esp");
  renderMvVolList();
  const active = document.querySelector('#mv-list .mv-row[data-i="' + _mvVolIndex + '"]');
  if (active) active.scrollIntoView({ block: "nearest" });

  const seq = ++_mvVolSeq;
  _mvVolBusy = true;
  _mvVolBusyOverlay(!_mvPickCached(pick), pick.label, pick.kind);
  _mvVolCaption(pick.label + " — generating at " + grid + "³ …");

  const $3Dmol = window["$3Dmol"] || window["3Dmol"];

  if (pick.kind === "esp") {
    // Two cubes, in order: the density that gives the surface, then the
    // potential that colours it. Sequential because the backend serializes
    // orca_plot anyway — one folder, one fixed output name per plot.
    const [densPart, espPart] = _mvEspParts();
    const dens = await _mvFetchCube(densPart, grid, seq, "ESP map — density surface");
    if (!dens) return;
    _mvVolCaption("ESP map — computing the potential at " + grid + "³ …");
    const esp = await _mvFetchCube(espPart, grid, seq, "ESP map — potential");
    if (!esp) return;
    _mvVolBusy = false;
    _mvVolBusyOverlay(false);
    _mvVolTitle = "ESP map";
    _mvVolSigned = false;              // the surface is a density: one surface
    _mvViewer.clear();
    _mvViewer.addModel(dens.text, "cube");
    _mvViewer.setStyle({}, { stick: { radius: 0.1 }, sphere: { scale: 0.18 } });
    _mvVolData = new $3Dmol.VolumeData(dens.text, "cube");
    _mvEspData = new $3Dmol.VolumeData(esp.text, "cube");
    // The density level here is the ESP convention (~0.002, the van-der-Waals-
    // like surface), NOT the enclosed-fraction fit: that fit exists because an
    // orbital's peak varies with how delocalized it is, while a total density's
    // 0.002 contour is the same physical surface in every molecule.
    _mvIso = _mvEspSurfaceIso;
    _mvSyncIsoControls();
    _mvSyncEspControls();
    mvRenderCube(true);
    return;
  }

  _mvEspData = null;
  const data = await _mvFetchCube(pick, grid, seq, pick.label);
  if (!data) return;
  _mvVolBusy = false;
  _mvVolBusyOverlay(false);
  _mvVolTitle = data.title || pick.label;
  _mvVolSigned = data.signed !== false;

  // Parse the cube ONCE: the molecule comes from its atom block and the volume
  // from its values, and the isovalue slider re-meshes this same object.
  _mvViewer.clear();
  _mvViewer.addModel(data.text, "cube");
  _mvViewer.setStyle({}, { stick: { radius: 0.1 }, sphere: { scale: 0.18 } });
  _mvVolData = new $3Dmol.VolumeData(data.text, "cube");
  // the opening isovalue needs the values, so it is picked here, not by the
  // backend, which only supplies the per-kind conventional ceiling
  _mvIso = _defaultIso(_mvVolData.data, data.isovalue || 0.05);
  _mvSyncIsoControls();
  mvRenderCube(true);
}

/* The "is this status describing MY request?" check that used to live here is
 * now local to _mvFetchCube — it has to compare against the grid of the request
 * being fetched, not the viewer's current one, because an ESP map issues two
 * requests and the viewer-global grid is not the right authority for either. */

/** Show or hide the "plotting" overlay. Only raised when orca_plot will really
 *  run: a cube already on disk opens straight into the stage, and an overlay
 *  that flashed on every pick would be noise rather than information.
 *  @param {boolean} on @param {string} [label] @param {string} [kind] */
function _mvVolBusyOverlay(on, label, kind) {
  const box = document.getElementById("mv-vol-busy");
  if (!box) return;
  if (on) {
    const t = document.getElementById("mv-busy-title");
    if (t) t.textContent = `Plotting ${label || ""}…`.replace("  ", " ");
    // The wait is per-kind and the ESP one is an order of magnitude longer, so
    // the copy has to say which is happening rather than average them (D2).
    const s = document.getElementById("mv-busy-sub");
    if (s) {
      s.textContent = kind === "esp"
        ? "Two grids: the electron density, then the potential over it. The "
          + "potential is a Coulomb sum at every grid point — minutes on a large "
          + "molecule, and longer at a finer grid."
        : "ORCA is computing the grid. An orbital takes about a second; an "
          + "electron density can take half a minute on a large molecule.";
    }
  }
  /** @type {HTMLElement} */ (box).hidden = !on;
}

/** @param {number} seq @param {string} msg */
function _mvVolFail(seq, msg) {
  if (seq !== _mvVolSeq) return;
  _mvVolBusy = false;
  _mvVolBusyOverlay(false);
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
  if (_mvEspData) {
    // The ESP map: ONE surface — the density isocontour — with the potential
    // sampled per vertex and run through the colour ramp. `voldata` is the
    // second field, `volscheme` the mapping; the ramp's ends come from the
    // design tokens so the legend swatch and the surface cannot disagree.
    const $3Dmol = window["$3Dmol"] || window["3Dmol"];
    _mvViewer.addIsosurface(_mvVolData, Object.assign({
      isoval: _mvIso,
      voldata: _mvEspData,
      volscheme: new $3Dmol.Gradient.CustomLinear(
        -_mvEspRange, _mvEspRange,
        [_cssColor("--esp-neg"), _cssColor("--esp-mid"), _cssColor("--esp-pos")]),
    }, common, { opacity: 0.92 }));
    if (zoom) _mvViewer.zoomTo();
    _mvViewer.render();
    _mvVolCaption("ESP map  ·  density surface " + _mvIso.toFixed(3) +
                  " e/bohr³  ·  scale ±" + _mvEspRange.toFixed(3) +
                  " a.u.  ·  grid " + _mvVolGrid + "³  ·  red −, blue +");
    return;
  }
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

/** Push _mvIso onto the slider and its readout (after a cube load picks it). */
function _mvSyncIsoControls() {
  const slider = /** @type {HTMLInputElement} */ (document.getElementById("mv-iso"));
  if (slider) slider.value = String(_sliderFromIso(_mvIso));
  const lbl = document.getElementById("mv-iso-val");
  if (lbl) lbl.textContent = _mvIso.toFixed(3);
}

/** Isovalue slider: re-mesh the cube already in memory — no backend call, which
 *  is the whole reason a coarse grid is enough. In ESP mode it steers the
 *  DENSITY surface the map is painted on. @param {string|number} v */
function mvSetIso(v) {
  _mvIso = _isoFromSlider(v);
  const lbl = document.getElementById("mv-iso-val");
  if (lbl) lbl.textContent = _mvIso.toFixed(3);
  if (!_mvVolBusy) mvRenderCube(false);
}

/* ---- the ESP colour scale ----
 * Log-scaled 0.005 … 0.5 a.u. so the conventional ±0.05 lands at the middle of
 * the track, and so a charged species (whose potential is offset by whole units)
 * is still reachable without the neutral case living in the first few pixels —
 * the same argument as the isovalue slider's scale. */
function _espFromSlider(v) { return 0.005 * Math.pow(100, Number(v) / 100); }
function _sliderFromEsp(r) {
  const v = 100 * Math.log(Math.max(0.005, r) / 0.005) / Math.log(100);
  return Math.max(0, Math.min(100, Math.round(v)));
}

/** Colour-scale slider: re-colours the surface already meshed, no backend call.
 *  @param {string|number} v */
function mvSetEspRange(v) {
  _mvEspRange = _espFromSlider(v);
  _mvSyncEspControls(true);
  if (!_mvVolBusy) mvRenderCube(false);
}

/** Push _mvEspRange onto the slider readout and the legend's end labels.
 *  @param {boolean} [fromSlider] skip writing back to the slider the user holds */
function _mvSyncEspControls(fromSlider) {
  const slider = /** @type {HTMLInputElement} */ (document.getElementById("mv-esp"));
  if (slider && !fromSlider) slider.value = String(_sliderFromEsp(_mvEspRange));
  const lbl = document.getElementById("mv-esp-val");
  if (lbl) lbl.textContent = _mvEspRange.toFixed(3);
  const lo = document.getElementById("mv-esp-lo");
  const hi = document.getElementById("mv-esp-hi");
  if (lo) lo.textContent = "−" + _mvEspRange.toFixed(2);
  if (hi) hi.textContent = "+" + _mvEspRange.toFixed(2);
}

/** Show the colour-scale slider and the legend only for the ESP map — for every
 *  other pick they would describe nothing. Stepping goes away whenever there is
 *  only one plot to step through, which the ESP map's own entry point is.
 *  @param {boolean} on */
function _mvEspControls(on) {
  const ctl = document.getElementById("mv-esp-ctl");
  const key = document.getElementById("mv-esp-legend");
  if (ctl) ctl.style.display = on ? "" : "none";
  if (key) key.style.display = on ? "" : "none";
  const many = _mvVolPicks.length > 1;
  ["mv-vol-prev", "mv-vol-next"].forEach(id => {
    const b = document.getElementById(id);
    if (b) b.style.display = many ? "" : "none";
  });
}

/** Grid selector: this one DOES need a new cube, so it re-runs the current pick.
 *  The choice is remembered per KIND — the ESP map's grid is its own (its cost
 *  is minutes, not seconds), and moving back to an orbital must not inherit it.
 *  @param {string|number} g */
function mvSetGrid(g) {
  const pick = _mvVolPicks[_mvVolIndex];
  _mvVolGrid = Number(g) || 60;
  if (pick) _mvGridByKind[pick.kind] = _mvVolGrid;
  mvVolShow(_mvVolIndex);
}

function mvVolReset() {
  _mvVolSeq++;              // orphan any in-flight generate
  _mvVolBusyOverlay(false);
  _mvEspControls(false);
  _mvVolPicks = []; _mvVolData = null; _mvEspData = null; _mvVolBusy = false;
  _mvVolSource = ""; _mvVolBase = "";
}
