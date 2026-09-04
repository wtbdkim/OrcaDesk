// @ts-check
// Results-tab section renderers (renderResult/renderResultSections, renderSummary,
// renderGeometry … renderFreeEnergyProfile) — split out of app.js. Plain global
// script, loaded before app.js.

// ---------- results ----------
/* The picker addresses a result the same way the 3D viewer does: an option's
 * value is a SOURCE string, "calc:<name>" or "file:<path>". Run folders survive
 * removal from the queue, so the workspace holds results the queue no longer
 * knows about — last week's job, one cleared from the list — and those are
 * worth one click, not a file dialog.
 *
 * The two groups are not cosmetic. A queued calc is parsed by NAME so the
 * backend dispatches on its KIND; a workspace-only result is parsed by PATH,
 * through the folder heuristic. Sending a queued calc down the path route
 * would let a leftover crest_conformers.xyz in a reused folder name read an
 * ORCA job as a conformer search (see bridge.parse_calc_output). */

/** @type {WorkspaceResult[]} results on disk under the workspace root */
let _wsResults = [];
/** Paths opened from OUTSIDE the workspace scan, in the order they were opened.
 *  The scan lists only .out/.mlip.json under the workspace root, so without
 *  this a result opened from anywhere else — and every .xyz, which the scan
 *  never lists — lost its option on the next entry to the tab, leaving the box
 *  naming one result while the body showed another.
 *  @type {string[]} */
let _openedPaths = [];

/** Re-scan the workspace for results and repaint the picker. Called on entry to
 *  the Results tab, not from the poll — it touches the disk. */
async function refreshWorkspaceResults() {
  try {
    const r = /** @type {WorkspaceResultsResult} */ (JSON.parse(await bridge.list_workspace_results()));
    _wsResults = r.ok ? (r.results || []) : [];
  } catch (e) { _wsResults = []; }   // the picker still lists the queue
  refreshResultSelect();
}

function refreshResultSelect() {
  const sel = document.getElementById("result-select");
  const prev = sel.value;
  // Whether a result is IN THE QUEUE is the backend's answer (it holds the
  // store), not "have we parsed it yet" — grouping by calcResults would file a
  // queued calc under the workspace and parse it by path, through the folder
  // heuristic that parse_calc_output exists to avoid.
  const names = [];
  const seen = new Set();
  const add = (n) => { const k = n.toLowerCase(); if (!seen.has(k)) { seen.add(k); names.push(n); } };
  // already-parsed results first (they keep their fetch order), then any other
  // queued calc the workspace scan found — including, via calcResults, one whose
  // folder lives OUTSIDE the workspace (a session restored from an old one).
  Object.keys(calcResults).forEach(add);
  _wsResults.filter(w => w.queued).forEach(w => add(w.name));
  const onDisk = _wsResults.filter(w => !w.queued);
  if (!names.length && !onDisk.length && !_openedPaths.length) {
    sel.innerHTML = `<option>—</option>`; return;
  }
  const opt = (v, label) => `<option value="${escapeHtml(v)}">${escapeHtml(label)}</option>`;
  let html = "";
  if (names.length) {
    html += `<optgroup label="In the queue">` +
            names.map(n => opt("calc:" + n, n)).join("") + `</optgroup>`;
  }
  if (onDisk.length) {
    html += `<optgroup label="In the workspace">` +
            onDisk.map(w => opt("file:" + w.path,
                                w.kind === "orca" ? w.name : `${w.name}  (${w.kind.toUpperCase()})`)).join("") +
            `</optgroup>`;
  }
  // A file opened from outside the scan keeps its own group, first — and only
  // while the scan has not since found it under the workspace root.
  const listed = new Set(names.map(n => "calc:" + n).concat(onDisk.map(w => "file:" + w.path)));
  const opened = _openedPaths.filter(pth => !listed.has("file:" + pth));
  if (opened.length) {
    html = `<optgroup label="Opened file">` +
           opened.map(pth => opt("file:" + pth,
                                 pth.replace(/\\/g, "/").split("/").pop() || pth)).join("") +
           `</optgroup>` + html;
  }
  sel.innerHTML = html;
  // etiquette: a re-render must not move the user's selection (DESIGN 15.11)
  if (prev && [...sel.options].some(o => o.value === prev)) sel.value = prev;
}

async function showSelectedResult() {
  const src = document.getElementById("result-select").value;
  if (!src || src === "—") return;
  if (src.startsWith("file:")) return showWorkspaceResult(src.slice(5));
  const name = src.startsWith("calc:") ? src.slice(5) : src;
  _currentResultName = name;   // a queued calc — enables the conformer->ORCA action
  _currentResultPath = "";
  // The picker now lists every queued calc, not only the ones already fetched,
  // so a first pick may have nothing cached yet. Parse it BY NAME so the
  // backend dispatches on its kind.
  if (!_resultExtras[name]) {
    try {
      const d = /** @type {ParsePayload} */ (JSON.parse(await bridge.parse_calc_output(name)));
      if (d && d.summary) { calcResults[name] = d.summary; _resultExtras[name] = d; }
    } catch (e) { /* fall through to the empty render below */ }
  }
  _currentResult = _resultExtras[name] || null;
  if (!_currentResult) { failNotify(`No readable output for "${name}" yet.`); return; }
  renderResult(_currentResult);
}

/** Give the picker an entry for a file opened from OUTSIDE the workspace, and
 *  select it. Without one the box went on naming the previously selected
 *  calculation while the body showed the dropped file — and because re-picking
 *  the same option fires no change event, there was no way back to it either.
 *
 *  It is remembered rather than inserted into the live `<select>`: the workspace
 *  re-scan on every entry to the tab rebuilds that element, and the scan lists
 *  only .out/.mlip.json under the workspace root — so an inserted option
 *  survived until the next tab switch and no longer at all for a .xyz, which
 *  the scan never lists.
 *  @param {string} path */
function noteExternalResult(path) {
  if (!path) return;
  if (_openedPaths.indexOf(path) < 0) _openedPaths.push(path);
  refreshResultSelect();          // rebuilds the groups, this file's included
  _selectResultOption("file:" + path);
}

/** Point the picker at `value` when it lists it, so the box never names one
 *  result while the body shows another. Silent when it does not — a file opened
 *  from outside the workspace has no option to select. @param {string} value */
function _selectResultOption(value) {
  const sel = document.getElementById("result-select");
  if (sel && [...sel.options].some(o => o.value === value)) sel.value = value;
}

/** Open a result that is on disk but not in the queue. @param {string} path */
async function showWorkspaceResult(path) {
  // The picker can hold a .xyz (an "Opened file" entry), which has no output
  // for the parser to read — re-picking it must take the structure route the
  // open did, not fail as an unparseable result.
  if (/\.xyz$/i.test(path)) return openStructureFile(path);
  let data;
  try { data = /** @type {ParsePayload} */ (JSON.parse(await bridge.parse_out_path(path))); }
  catch (e) { failNotify("Could not read that result."); return; }
  if (!data || !data.summary) { failNotify("Could not parse that result."); return; }
  // not a queued calc: no conformer->ORCA action, but the plot source is the
  // file itself, so the 3D viewer still works (see viewOrbitals3D)
  _currentResultName = "";
  _currentResultPath = path;
  _currentResult = data;
  _selectResultOption("file:" + path);
  renderResult(data);
}

/** Render summary + every applicable section for a parsed payload. @param {ParsePayload} [d] */
function renderResult(d) {
  if (!d) return;
  if (d.summary) renderSummary(d.summary, d);
  renderResultSections(d);
  // Visual reads the same result, so a new one has to re-discover what it can
  // draw — otherwise the rows go on describing the previous calculation.
  if (_resultsMode === "visual") renderVisual();
}

/** Toggle "Show all": reveal every parsed value regardless of calc-type gating. */
function toggleShowAll() {
  showAllResults = !showAllResults;
  const btn = document.getElementById("btn-showall");
  if (btn) btn.classList.toggle("on", showAllResults);   // tinted box when on
  if (_currentResult) renderResult(_currentResult);
}

/**
 * Render every non-summary section we have for a parsed result, in a fixed
 * order. Accepts a ParsePayload (Open .out) or the cached payload of a queue
 * result. renderSummary must be called first — these all append to the body.
 * @param {ParsePayload} [d]
 */
function renderResultSections(d) {
  if (!d) return;
  // Reset the orbital cache first: Visual reads it on its own now (its Orbitals
  // row counts the levels), so a result without orbitals must not go on
  // reporting the previous one's.
  _lastOrbitals = [];
  // "Final geometry" → opt jobs; electronic-structure sections → sp/opt jobs.
  // "Show all" overrides both. Specialty sections (freq/tddft/nmr/neb) are
  // present-only — they only exist for their kind, so no extra gating needed.
  const geom = showAllResults || d.is_optimization;
  const elec = showAllResults || d.show_elec;
  // CREST conformer ensemble: a selectable list with a batch "re-optimize in
  // ORCA" action. Present-only (only crest_conf results carry it).
  if (d.is_conformer_search && d.conformers && d.conformers.length) renderConformers(d.conformers);
  if (geom && d.geometry && d.geometry.length) renderGeometry(d.geometry);
  // Everything that DRAWS this result — its structures, its orbitals, the ESP
  // map — is in Visual mode, so Output stays what the parser read.
  if (elec && d.orbitals && d.orbitals.length) renderOrbitals(d.orbitals);
  if (elec && d.mulliken && d.mulliken.length) renderMulliken(d.mulliken);
  if (elec && d.loewdin && d.loewdin.length) renderLoewdin(d.loewdin);
  if (elec && ((d.mayer_bonds && d.mayer_bonds.length) || (d.mayer_valences && d.mayer_valences.length)))
    renderMayer(d.mayer_bonds || [], d.mayer_valences || []);
  if (d.frequencies && d.frequencies.length) renderFreqSpectrum(d.frequencies, d.n_imaginary);
  if (d.transitions && d.transitions.length) renderSpectrum(d.transitions);
  if (d.tddft_states && d.tddft_states.length) renderTddftStates(d.tddft_states);
  if (d.nmr && d.nmr.length) renderNmr(d.nmr);
  if (d.neb_path && d.neb_path.length) renderNebPath(d.neb_path, d.neb_path_kind || "neb");
  if (d.input_keywords || d.input_block) renderInputEcho(d.input_keywords, d.input_block);
}
/* ---------- Results › Visual ----------
 * Output is what the parser read; Visual is what can be DRAWN from the same
 * result. One selected result, two ways of reading it — so the picker and the
 * file button are shared and only the mode changes underneath them.
 *
 * The rows are discovered, not asked for. Everything openable already sits in
 * the result's own folder — the CREST ensemble, the per-conformer conformers/
 * export, a trajectory, and the .gbw the orbitals and the ESP map are computed
 * from — so the tab lists what is there. That is what replaced a "Browse .xyz…"
 * folder picker, which asked the user to know where ORCAdesk had put things.
 *
 * Plotting happens on the ROW, not behind the modal. An ESP map is minutes; the
 * old flow opened a full-window modal onto an empty stage for all of it, with
 * nothing else reachable. Now the row's button says it is working, the rest of
 * the tab stays usable, and the viewer opens onto a finished plot. */

let _resultsMode = "output";                     // "output" | "visual"
/** @type {StructureSetPayload[]} */ let _visSets = [];
/** @type {PlotOptionsResult|null} */ let _visPlot = null;
let _visBusy = "";        // key of the row currently plotting ("" = none)
let _visBusyAt = 0;       // when it started, for the elapsed clock
/** @type {any} */ let _visBusyTimer = null;
let _visSeq = 0;          // discards a discovery that lands after a newer one

/** Switch the Results tab between Output and Visual. @param {string} mode */
function setResultsMode(mode) {
  _resultsMode = mode === "visual" ? "visual" : "output";
  const vis = _resultsMode === "visual";
  document.getElementById("rmode-output").classList.toggle("active", !vis);
  document.getElementById("rmode-visual").classList.toggle("active", vis);
  /** @type {HTMLElement} */ (document.getElementById("result-body")).hidden = vis;
  /** @type {HTMLElement} */ (document.getElementById("visual-body")).hidden = !vis;
  // "Show all" gates the parsed sections and the free-energy profile is a chart
  // over parsed numbers: both are Output's, and neither says anything in Visual.
  const showall = document.getElementById("btn-showall");
  if (showall) showall.style.display = vis ? "none" : "";
  const fep = document.getElementById("card-fep");
  if (fep) fep.style.display = vis ? "none" : "";
  if (vis) renderVisual();
}

/** Discover what the selected result can be drawn as, then paint the rows.
 *  Two backend reads (the folder's .xyz sets, and what its wavefunction
 *  supports); both touch the disk, so this runs on entry to the mode and on a
 *  result change, never from the poll. */
async function renderVisual() {
  const body = document.getElementById("visual-body");
  if (!body) return;
  // the same address the plot slots take: "calc:<name>" or "file:<path>"
  const source = _mvPlotSource();
  if (!source) {
    body.innerHTML = `<div class="hint">No result selected — pick one above, or <b>Open file…</b>.</div>`;
    return;
  }
  const seq = ++_visSeq;
  body.innerHTML = `<div class="hint">Looking for what this result can show…</div>`;
  _visSets = []; _visPlot = null;
  try {
    const r = /** @type {StructureSetsResult} */ (JSON.parse(await bridge.list_structure_sets(source)));
    if (seq !== _visSeq) return;
    if (r.ok) _visSets = r.sets || [];
  } catch (e) { /* a folder we cannot read still leaves the plot rows */ }
  try {
    const o = /** @type {PlotOptionsResult} */ (JSON.parse(await bridge.get_plot_options(source)));
    if (seq !== _visSeq) return;
    if (o.ok) _visPlot = o;
  } catch (e) { /* no wavefunction rows, then */ }
  if (seq !== _visSeq) return;
  renderVisualRows();
}

/** @typedef {{key:string, label:string, sub:string, call:string, ready:boolean,
 *             dot:boolean, path?:string}} VisRow
 *  `path` is the file this row already IS on disk, when there is one — what the
 *  external route hands over without generating anything. */

/** The rows Visual offers for the current result, in the order they are useful:
 *  the structure first (it is what the calculation produced), then the .xyz
 *  sets found on disk, then the two things that must be computed from the .gbw.
 *  @returns {VisRow[]} */
function _visualRows() {
  /** @type {VisRow[]} */ const rows = [];
  const geom = _currentResult && _currentResult.geometry;
  if (geom && geom.length)
    rows.push({ key: "geom", label: "Structure",
                sub: geom.length + " atoms — the geometry this result ends on",
                call: "viewFinalGeometry()", ready: true, dot: false });
  for (const st of _visSets) {
    const what = st.kind === "folder"
      ? st.count + " file" + (st.count === 1 ? "" : "s")
      : (st.count ? st.count + " structure" + (st.count === 1 ? "" : "s") : "structure file");
    rows.push({ key: st.key, label: st.label,
                sub: what + " — " + st.path,
                call: "openStructureSet(" + _jsArg(st.path) + ", " + _jsArg(st.label) + ")",
                ready: true, dot: false, path: st.path });
  }
  if (_visPlot && _visPlot.has_gbw) {
    const orbs = _visOrbitals().length;
    rows.push({ key: "mo", label: "Orbitals & density",
                sub: (orbs ? orbs + " levels — " : "") +
                     "opens on the HOMO; step with ← / →. About a second each.",
                call: "viewOrbitals3D(_visOrbitals())", ready: true, dot: false });
    if ((_visPlot.kinds || []).indexOf("esp") >= 0) {
      const grid = _visPlot.esp_grid || 40;
      const cached = _visPlot.cached || [];
      const ready = mvEspCubeNames(_visPlot.base || "", grid)
        .every(n => cached.indexOf(n) >= 0);
      rows.push({ key: "esp", label: "Electrostatic potential map",
                  sub: ready
                    ? "Already plotted at " + grid + "³ — opens straight away."
                    : "The density surface coloured by the potential, at " + grid + "³. " +
                      "A Coulomb sum at every grid point: minutes on a large molecule.",
                  call: "viewEspMap()", ready: ready, dot: true });
    }
  }
  return rows;
}

/** The orbital list to plot from: the one Output rendered when it did, else
 *  the parsed payload's own. Visual is not gated by calc kind the way Output's
 *  sections are, so a job whose levels Output hides is still plottable.
 *  @returns {OrbitalPayload[]} */
function _visOrbitals() {
  return _lastOrbitals.length ? _lastOrbitals
       : ((_currentResult && _currentResult.orbitals) || []);
}

/** Paint the rows. Separate from the discovery above so the busy clock can
 *  repaint a button every second without touching the disk again. */
function renderVisualRows() {
  const body = document.getElementById("visual-body");
  if (!body) return;
  const rows = _visualRows();
  if (!rows.length) {
    body.innerHTML = `<div class="hint">Nothing to draw for this result — no structure was
      parsed, no <code>.xyz</code> sits in its folder, and there is no <code>.gbw</code>
      to compute orbitals from.</div>`;
    return;
  }
  const external = viewerTarget() === "system";
  body.innerHTML = rows.map(r => {
    const busy = _visBusy === r.key;
    // A row that must run orca_plot says "Plot", not "View": the button names
    // what the click costs, and then changes to the state it is in. Sending the
    // file elsewhere renames the destination, never the cost — a cube still has
    // to be computed before any program can be handed it.
    const done = external ? (r.key === "esp" ? "Show" : "Open") : "View";
    const face = busy ? "Plotting… " + _visElapsed()
                      : (r.dot && !r.ready ? "Plot" : done);
    const action = external ? `visOpenExternal(visRow(${_jsArg(r.key)}))` : r.call;
    const dot = r.dot
      ? `<span class="vis-dot ${r.ready ? "ready" : ""}" title="${r.ready ?
           "Already on disk — opens instantly" : "Not plotted yet"}">${r.ready ? "●" : "○"}</span>`
      : "";
    return `<div class="vis-row">
      ${dot}
      <div class="vis-row-main">
        <div class="vis-row-label">${escapeHtml(r.label)}</div>
        <div class="vis-row-sub" title="${escapeHtml(r.sub)}">${escapeHtml(r.sub)}</div>
      </div>
      <button class="btn btn-sm ${busy ? "vis-busy" : ""}" ${_visBusy ? "disabled" : ""}
              onclick="visOpen(${_jsArg(r.key)}, () => ${action})">${escapeHtml(face)}</button>
    </div>`;
  }).join("") + (external
    ? `<div class="hint" style="margin-top:8px">These open in whichever program this PC
       uses for the file — ORCAdesk still computes everything first. An ESP map is two
       cubes, so that one is shown in its folder rather than opened.
       <a href="#" onclick="switchTab('settings');return false">Change this</a> to use ORCAdesk's
       own viewer.</div>`
    : `<div class="hint" style="margin-top:8px">Everything here opens the same
       viewer: ← / → steps, <b>F</b> stars a structure, Esc closes, drag rotates.</div>`);
}

/** One Visual row by key, rebuilt from the current result — how the rendered
 *  button reaches its row without an object riding through an HTML attribute
 *  (a calc name may hold a quote; the Log tab's job strip avoids the same trap
 *  with data-job). @param {string} key @returns {VisRow|null} */
function visRow(key) {
  return _visualRows().find(r => r.key === key) || null;
}

/** A string as a JS single-quoted literal, safe inside an HTML attribute. A
 *  label or a Windows path may hold a backslash or a quote, either of which
 *  would end the literal (or the attribute) early — the same hazard the Log
 *  tab's job strip avoids with data-job.
 *  @param {string} v */
function _jsArg(v) {
  return "'" + String(v)
    .replace(/\\/g, "\\\\").replace(/'/g, "\\'")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;") + "'";
}

/** Elapsed time on the running plot, m:ss. */
function _visElapsed() {
  const s = Math.max(0, Math.round((Date.now() - _visBusyAt) / 1000));
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}

/** Run one row's action with the row marked busy. The wait is the row's, not a
 *  modal's: the button says it is working, every row is disabled while it runs
 *  (the backend serializes orca_plot anyway), and the rest of the tab — Output
 *  included — stays usable while a multi-minute ESP map computes.
 *  @param {string} key @param {() => any} fn */
async function visOpen(key, fn) {
  if (_visBusy) return;
  _visBusy = key; _visBusyAt = Date.now();
  renderVisualRows();
  _visBusyTimer = setInterval(() => { if (_visBusy) renderVisualRows(); }, 1000);
  try { await fn(); }
  catch (e) { failNotify("Could not open that."); }
  finally {
    clearInterval(_visBusyTimer); _visBusyTimer = null;
    _visBusy = "";
    // a plot that just ran is on disk now, so its dot flips — re-read the
    // options rather than guessing which file appeared
    renderVisual();
  }
}

/** Where a Visual row's button takes the user: ORCAdesk's own viewer, or the
 *  program this PC associates with the file (Settings → Opening structures and
 *  maps). Read live rather than cached, so changing it takes effect on the next
 *  click without re-rendering the tab. @returns {"in_app"|"system"} */
function viewerTarget() {
  return (typeof settings === "object" && settings
          && settings.viewer_target === "system") ? "system" : "in_app";
}

/** Hand one path to the user's own programs. `reveal` shows it in the file
 *  manager instead of opening it — which is what a set of files (an ESP map's
 *  two cubes, a folder of conformers) needs.
 *  @param {string} path @param {boolean} [reveal] */
async function openExternally(path, reveal) {
  if (!path) return;
  /** @type {OkResult} */ let r;
  const call = reveal ? bridge.show_path_in_folder(path) : bridge.open_path_external(path);
  try { r = /** @type {OkResult} */ (JSON.parse(await call)); }
  catch (e) { failNotify("Could not reach the desktop to open that."); return; }
  // The refusal sentence is the useful part -- "no program is registered for
  // .cube files" tells the user exactly what to do, which a generic toast does not.
  if (!r.ok) failNotify(r.error || "Could not open that file.");
}

/** The run folder of the result on screen — where ORCA actually wrote it.
 *  `_visPlot` has it once the Visual tab has rendered; otherwise ask, so the
 *  button works from the Output tab too. The front-end never assembles a
 *  workspace path itself (P4). @returns {Promise<string>} */
async function resultFolder() {
  if (_visPlot && _visPlot.folder) return _visPlot.folder;
  const source = _mvPlotSource();
  if (!source) return "";
  try {
    const o = /** @type {PlotOptionsResult} */ (JSON.parse(await bridge.get_plot_options(source)));
    if (o.ok && o.folder) return o.folder;
  } catch (e) { /* fall through to the path we may already hold */ }
  return _currentResultPath ? _folderOf(_currentResultPath) : "";
}

/** Results header: reveal this result's folder in the file manager. Answers
 *  "where did that actually get written?", which a workspace full of run
 *  folders otherwise makes a hunt. */
async function showResultFolder() {
  const folder = await resultFolder();
  if (!folder) { failNotify("No result selected."); return; }
  await openExternally(folder, true);
}

/** The external counterpart of a Visual row's in-app action: produce the file
 *  the row stands for — generating it if it is not on disk yet — and hand it
 *  over. Returns nothing; failures have already been reported.
 *  @param {VisRow} row */
async function visOpenExternal(row) {
  if (!row) return;                       // the result changed under the click
  if (row.path) { await openExternally(row.path); return; }
  if (row.key === "geom") {
    // The one row backed by no file: the geometry lives in the parse payload,
    // so it has to become an .xyz before any other program can read it.
    const geom = (_currentResult && _currentResult.geometry) || [];
    const source = _mvPlotSource();
    if (!geom.length || !source) { failNotify("This result has no geometry."); return; }
    /** @type {SavedFileResult} */ let r;
    try {
      r = /** @type {SavedFileResult} */ (JSON.parse(
        await bridge.save_structure_xyz(source, geomToXyz(geom, _visBase()))));
    } catch (e) { failNotify("Could not write the structure file."); return; }
    if (!r.ok || !r.path) { failNotify(r.error || "Could not write the structure file."); return; }
    await openExternally(r.path);
    renderVisual();            // the new .xyz is a discoverable set now
    return;
  }
  if (row.key === "mo") {
    await openExternally(await orbitalCubeForExternal(_visOrbitals()));
    return;
  }
  if (row.key === "esp") {
    // Revealed, not opened: an ESP map is two cubes -- see espCubesForExternal.
    await openExternally(await espCubesForExternal(), true);
    return;
  }
}

/** Open one discovered .xyz set: a folder of them, or one multi-frame file.
 *  @param {string} path @param {string} label */
async function openStructureSet(path, label) {
  /** @type {FramesResult} */ let r;
  try { r = JSON.parse(await bridge.get_structure_frames(path)); }
  catch (e) { failNotify("Could not read that structure."); return; }
  if (!r.ok) { failNotify(r.error || "Could not read that structure."); return; }
  // Favorites and "Export ★" are keyed by SOURCE. A queued calculation's own
  // CREST ensemble keeps its "calc:<name>" key, so stars set before this tab
  // existed still resolve; every other set is keyed by the folder it came from.
  const ensemble = /crest_(conformers|best)\.xyz$/i.test(path);
  const title = _visTitle(label || r.title || "");
  if (ensemble && _currentResultName)
    await openMolViewer(title, r.frames, "calc", _currentResultName);
  else
    await openMolViewer(title, r.frames, "folder", r.folder || "");
}

/** The geometry the parsed result ends on, as a single viewer frame. No
 *  backend call — the Results tab already holds it (P4). */
async function viewFinalGeometry() {
  const geom = _currentResult && _currentResult.geometry;
  if (!geom || !geom.length) { failNotify("No geometry in this result."); return; }
  const name = _currentResultName || _visBase();
  const frames = [{ label: name || "structure", xyz: geomToXyz(geom, name), energy: null }];
  const title = _visTitle("structure");
  if (_currentResultName) await openMolViewer(title, frames, "calc", _currentResultName);
  else await openMolViewer(title, frames, "folder", _folderOf(_currentResultPath));
}

/** The result's own stem, for viewer titles: the plot options' base when we
 *  have it, else the calc name or the opened file's own name. */
function _visBase() {
  if (_visPlot && _visPlot.base) return _visPlot.base;
  if (_currentResultName) return _currentResultName;
  return _folderOf(_currentResultPath, true);
}

/** "<result> — <what>", or just <what> when there is no result name.
 *  @param {string} what */
function _visTitle(what) {
  const b = _visBase();
  return b ? b + " — " + what : what;
}

/** The folder a path sits in — or, with `leaf`, the file's own stem.
 *  @param {string} path @param {boolean} [leaf] */
function _folderOf(path, leaf) {
  const parts = String(path || "").replace(/\\/g, "/").split("/");
  const name = parts.pop() || "";
  return leaf ? name.replace(/\.[^.]*$/, "") : parts.join("/");
}

/** A parsed geometry as .xyz text. One definition, shared by the Output table's
 *  Copy button and the viewer frame above.
 *  @param {GeomAtomPayload[]} geom @param {string} [comment] */
function geomToXyz(geom, comment) {
  return geom.length + "\n" + (comment || "") + "\n" +
    geom.map(a => a.el + "  " + a.x.toFixed(6) + "  " + a.y.toFixed(6) + "  " + a.z.toFixed(6)).join("\n");
}

/** @param {[string, string, string][]} rows @param {ParsePayload} [d] */
function renderSummary(rows, d) {
  const body = document.getElementById("result-body");
  // electronic-structure rows (category "elec") only show for sp/opt jobs,
  // unless "Show all" is on
  const showElec = showAllResults || !!(d && d.show_elec);
  let html = `<div class="kv">`;
  for (const row of rows) {
    const k = row[0], v = row[1], cat = row[2] || "";
    if (cat === "elec" && !showElec) continue;
    // Failure patterns FIRST, and else-if so later tests can't overwrite:
    // "NOT converged" contains "converged" and "ABNORMAL" contains "Normal",
    // so sequential ifs let the success test repaint failures green.
    let cls = "";
    if (/ABNORMAL|NOT converged/i.test(String(v))) cls = "err";
    else if (/imaginary/i.test(k) && !/^0$/.test(String(v))) cls = "warn";
    else if (/converged|Normal/i.test(String(v))) cls = "ok";
    html += `<div class="k">${escapeHtml(k)}</div><div class="v ${cls}">${escapeHtml(String(v))}</div>`;
  }
  html += `</div>`;
  body.innerHTML = html;
}

/** CREST conformer ensemble: a read-only ranked list. The Results tab is for
 *  interpreting results — follow-up calculations are built on the Build tab by
 *  referencing the CREST search (a reference receives the lowest-energy
 *  conformer's geometry). @param {ConformerPayload[]} confs */
function renderConformers(confs) {
  const body = document.getElementById("result-body");
  let rows = "";
  for (const c of confs) {
    rows += `<tr>
      <td>${c.index}</td>
      <td>${c.rel_kcal.toFixed(3)}</td>
      <td>${c.energy_eh.toFixed(8)}</td>
      <td>${c.n_atoms}</td></tr>`;
  }
  // Export needs the queued calc's workspace folder server-side, so it is only
  // offered for one; an externally opened .out has no _currentResultName.
  // Viewing the ensemble is under Visual, which reaches it by either address.
  const actions = _currentResultName
    ? `<div class="btn-group">
         <button class="btn btn-sm btn-ghost" onclick="exportConformers()">Export as .xyz</button>
       </div>` : "";
  body.innerHTML += `
    <div class="divider"></div>
    <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px">
      <div class="card-title">Conformers (${confs.length})</div>
      ${actions}
    </div>
    <div style="max-height:280px;overflow:auto">
      <table class="data">
        <thead><tr><th>#</th><th>ΔE (kcal/mol)</th><th>E (Eh)</th><th>atoms</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="hint" style="margin-top:6px">Follow-up calculations are built on the Build tab: reference this search from a geometry source — the referencing calculation runs on the lowest-energy conformer. <b>Visual</b> flips through every conformer with the ← / → keys; <b>Export as .xyz</b> writes each one (c1 = the best) to a <code>conformers/</code> subfolder of the run.</div>`;
}

/** Split the shown CREST search's ensemble into per-conformer .xyz files
 *  (in a conformers/ subfolder of the run folder). */
async function exportConformers() {
  if (!_currentResultName) return;
  try {
    const r = /** @type {ExportResult} */ (JSON.parse(await bridge.export_conformers(_currentResultName)));
    if (!r.ok) { failNotify(r.error || "Could not export conformers."); return; }
    toast(`Exported ${r.count} conformer${r.count === 1 ? "" : "s"} to ${r.folder}`);
    appendLog(`Exported ${r.count} conformer .xyz files to ${r.folder}`, "ok");
  } catch (e) { failNotify("Could not export conformers."); }
}

let _lastGeomXyz = "";   // last rendered geometry, for the Copy .xyz button
/** @param {GeomAtomPayload[]} geom */
function renderGeometry(geom) {
  const body = document.getElementById("result-body");
  let rows = "";
  geom.forEach((a, i) => {
    rows += `<tr><td>${i+1}</td><td>${escapeHtml(a.el)}</td><td>${a.x.toFixed(6)}</td><td>${a.y.toFixed(6)}</td><td>${a.z.toFixed(6)}</td></tr>`;
  });
  _lastGeomXyz = geomToXyz(geom);
  body.innerHTML += `
    <div class="divider"></div>
    <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px">
      <div class="card-title">Final geometry (${geom.length} atoms, Å)</div>
      <button class="btn btn-sm btn-ghost" onclick="copyGeometryXyz()">Copy .xyz</button>
    </div>
    <div style="max-height:280px;overflow:auto">
      <table class="data">
        <thead><tr><th>#</th><th>El</th><th>x</th><th>y</th><th>z</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}
async function copyGeometryXyz() {
  if (!_lastGeomXyz) return;
  try { await navigator.clipboard.writeText(_lastGeomXyz); toast("Geometry copied as .xyz."); }
  catch (e) { failNotify("Copy failed — clipboard unavailable."); }
}

/** The orbital list currently on the Results tab, handed to the 3D viewer so it
 *  need not re-parse the .out for something the tab already holds (P4). */
/** @type {OrbitalPayload[]} */ let _lastOrbitals = [];

/** @param {OrbitalPayload[]} orbs */
function renderOrbitals(orbs) {
  const body = document.getElementById("result-body");
  _lastOrbitals = orbs;
  // An unrestricted run has two manifolds, each numbered from 0, and the
  // frontier orbitals can sit in different ones — so which row is HOMO/LUMO is
  // decided by the parser (over both, by energy) and rides on the payload.
  const unrestricted = orbs.some(o => o.spin);
  const spinName = { a: "α", b: "β" };
  let rows = "";
  orbs.forEach((o) => {
    let tag = "", color = "";
    if (o.frontier === "homo") { tag = " ← HOMO"; color = "color:var(--ok);font-weight:600"; }
    else if (o.frontier === "lumo") { tag = " ← LUMO"; color = "color:var(--warn);font-weight:600"; }
    const spinCell = unrestricted ? `<td>${spinName[o.spin] || ""}</td>` : "";
    rows += `<tr><td>${o.idx}</td>${spinCell}<td>${o.occ.toFixed(3)}</td><td style="${color}">${o.ev.toFixed(4)}${tag}</td></tr>`;
  });
  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">Orbital energies (${orbs.length} levels)</div>
    <div style="max-height:280px;overflow:auto">
      <table class="data">
        <thead><tr><th>#</th>${unrestricted ? "<th>Spin</th>" : ""}<th>Occ</th><th>E (eV)</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="hint" style="margin-top:6px">Drawing any of these is under <b>Visual</b>.</div>`;
}

/** @param {[string, number][]} mulliken */
function renderMulliken(mulliken) {
  const body = document.getElementById("result-body");
  let rows = "", sum = 0;
  mulliken.forEach(([el, q], i) => {
    sum += q;
    rows += `<tr><td>${i+1} ${escapeHtml(el)}</td><td>${q.toFixed(6)}</td></tr>`;
  });
  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">Mulliken atomic charges</div>
    <div style="max-height:280px;overflow:auto">
      <table class="data">
        <thead><tr><th>Atom</th><th>Charge (e)</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="hint">Sum of charges = ${sum.toFixed(4)} e</div>`;
}

/** @param {[string, number][]} loewdin */
function renderLoewdin(loewdin) {
  const body = document.getElementById("result-body");
  let rows = "", sum = 0;
  loewdin.forEach(([el, q], i) => {
    sum += q;
    rows += `<tr><td>${i+1} ${escapeHtml(el)}</td><td>${q.toFixed(6)}</td></tr>`;
  });
  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">Löwdin atomic charges</div>
    <div style="max-height:280px;overflow:auto">
      <table class="data">
        <thead><tr><th>Atom</th><th>Charge (e)</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="hint">Sum of charges = ${sum.toFixed(4)} e</div>`;
}

/** @param {[string, string, number][]} bonds @param {[number, string, number][]} valences */
function renderMayer(bonds, valences) {
  const body = document.getElementById("result-body");
  let bRows = "";
  bonds.forEach(([i, j, order]) => {
    const cls = order >= 0.6 ? "" : "color:var(--muted-foreground)";
    bRows += `<tr><td style="${cls}">${escapeHtml(i)} – ${escapeHtml(j)}</td><td style="${cls}">${order.toFixed(4)}</td></tr>`;
  });
  let vRows = "";
  valences.forEach(([idx, el, va]) => {
    vRows += `<tr><td>${idx} ${escapeHtml(el)}</td><td>${va.toFixed(4)}</td></tr>`;
  });
  const bondsTbl = bonds.length ? `
    <div class="card-title" style="font-size:13px;margin-top:8px">Bond orders</div>
    <div style="max-height:240px;overflow:auto">
      <table class="data">
        <thead><tr><th>Bond</th><th>Order</th></tr></thead>
        <tbody>${bRows}</tbody>
      </table>
    </div>` : "";
  const valTbl = valences.length ? `
    <div class="card-title" style="font-size:13px;margin-top:8px">Total valence (VA)</div>
    <div style="max-height:240px;overflow:auto">
      <table class="data">
        <thead><tr><th>Atom</th><th>Valence</th></tr></thead>
        <tbody>${vRows}</tbody>
      </table>
    </div>` : "";
  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">Mayer population analysis</div>
    ${bondsTbl}${valTbl}`;
}

/** @param {TddftStatePayload[]} states */
function renderTddftStates(states) {
  const body = document.getElementById("result-body");
  let rows = "";
  for (const s of states) {
    const top = (s.contributions || []).slice(0, 4)
      .map(([f, t, w]) => `${escapeHtml(f)}→${escapeHtml(t)} (${(w*100).toFixed(0)}%)`)
      .join(", ");
    rows += `<tr><td>${s.state}</td><td>${s.ev.toFixed(3)}</td><td>${top}</td></tr>`;
  }
  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">TD-DFT excited-state composition (${states.length})</div>
    <div style="max-height:280px;overflow:auto">
      <table class="data">
        <thead><tr><th>State</th><th>E (eV)</th><th>Dominant orbital transitions</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

/** @param {string} [keywords] @param {string} [block] */
function renderInputEcho(keywords, block) {
  const body = document.getElementById("result-body");
  const pre = (t) => `<pre class="mono" style="white-space:pre-wrap;word-break:break-word;font-size:12px;background:var(--input-bg);padding:10px;border-radius:var(--radius-sm);margin:6px 0 0;overflow:auto">${escapeHtml(t)}</pre>`;
  let html = `<div class="divider"></div><div class="card-title">Input echo</div>`;
  if (keywords) html += `<div class="hint" style="margin-top:8px">Keywords</div>${pre(keywords)}`;
  if (block) html += `<div class="hint" style="margin-top:8px">Input block</div>${pre(block)}`;
  body.innerHTML += html;
}

/** The Results tab's one file button. The backend picks the file and says how
 *  it should be read — a parsed output goes to Output, a .xyz to the viewer —
 *  because what the user chose is a RESULT, not a route (this replaced a second
 *  "Browse .xyz…" button that asked them to decide first). */
async function openOutFile() {
  /** @type {PickedResultPayload} */
  let r; try { r = JSON.parse(await bridge.pick_result_file()); } catch { return; }
  if (r.cancelled) return;   // user closed the picker — not an error
  if (!r.ok || !r.path) { failNotify(r.error || "Could not open that file."); return; }
  if (r.route === "structure") return openStructureFile(r.path);
  const raw = await bridge.parse_out_path(r.path);
  /** @type {ParsePayload} */
  let data; try { data = JSON.parse(raw); } catch { return; }
  // The backend's own reason, on both channels (D65 / B23): "Could not parse
  // file." on the log alone went unseen unless the Log tab happened to be open,
  // and it threw away the `{"error": "..."}` the bridge returns — which is the
  // only thing that says WHY (missing file, permission, not an ORCA output).
  if (!data.summary) {
    failNotify(data.error || "Could not read that output file.");
    return;
  }
  _currentResultName = "";   // an external file, not a queued calc → no conformer->ORCA action
  // …but keep its PATH: the .gbw sits beside it, so orbitals and density are
  // still plottable for a file from anywhere on disk
  _currentResultPath = data.path || r.path;
  _currentResult = data;
  noteExternalResult(_currentResultPath);
  renderResult(data);
  setResultsMode("output");
  switchTab("results");
  // switchTab re-scans the workspace; point the picker at this file if it is one
  if (_currentResultPath) _selectResultOption("file:" + _currentResultPath);
}

/** Open a picked .xyz: it becomes the shown result, in Visual mode. There is
 *  nothing for the parser to read in a structure file, so Output says so rather
 *  than going on showing the PREVIOUS result's numbers under this file's name.
 *  Its folder is what Visual lists, which is how a folder of structures is
 *  reached now that the folder picker is gone. @param {string} path */
async function openStructureFile(path) {
  _currentResultName = "";
  _currentResultPath = path;
  _currentResult = null;
  noteExternalResult(path);
  document.getElementById("result-body").innerHTML =
    `<div class="hint">A structure file — there is no calculation output to read here.
     Its structures, and anything else in its folder, are under <b>Visual</b>.</div>`;
  setResultsMode("visual");
  switchTab("results");
  _selectResultOption("file:" + path);
  await openStructureSet(path, "");
}

/** @param {TransitionPayload[]} transitions */
function renderSpectrum(transitions) {
  const body = document.getElementById("result-body");
  const maxF = Math.max(...transitions.map(t => t.fosc), 1e-6);
  const minNm = Math.min(...transitions.map(t => t.nm));
  const maxNm = Math.max(...transitions.map(t => t.nm));
  const W = 640, H = 200, pad = 30;
  const x = nm => pad + (nm - minNm) / (maxNm - minNm || 1) * (W - 2*pad);
  let bars = "";
  for (const t of transitions) {
    const h = (t.fosc / maxF) * (H - 2*pad);
    const bright = t.fosc > 0.5 * maxF;
    bars += `<rect class="spectrum-bar ${bright?'bright':''}" x="${x(t.nm).toFixed(1)}" y="${(H-pad-h).toFixed(1)}" width="2.5" height="${h.toFixed(1)}"></rect>`;
  }
  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">UV-Vis (oscillator strength vs wavelength)</div>
    <svg class="spectrum" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      <line x1="${pad}" y1="${H-pad}" x2="${W-pad}" y2="${H-pad}" stroke="var(--border)"/>
      ${bars}
      <text class="mono" x="${pad}" y="${H-8}" fill="var(--muted-foreground)" font-size="10">${minNm.toFixed(0)} nm</text>
      <text class="mono" x="${W-pad-30}" y="${H-8}" fill="var(--muted-foreground)" font-size="10">${maxNm.toFixed(0)} nm</text>
    </svg>`;
}

/** @param {number[]} frequencies @param {number} [nImaginary] */
function renderFreqSpectrum(frequencies, nImaginary) {
  const body = document.getElementById("result-body");
  if (!frequencies || !frequencies.length) return;

  // Split out the (near-)zero translational/rotational modes so they don't
  // dominate the axis; they're shown faint near 0.
  const real = frequencies.filter(f => f > 0.01);
  const imag = frequencies.filter(f => f < -0.01);
  const zero = frequencies.filter(f => Math.abs(f) <= 0.01);

  const maxF = Math.max(...frequencies.map(Math.abs), 1);
  const minF = Math.min(0, ...imag);          // negative if any imaginary
  const W = 640, H = 200, padL = 36, padR = 16, padB = 28, padT = 14;
  const span = maxF - minF || 1;
  const x = f => padL + (f - minF) / span * (W - padL - padR);
  const baseY = H - padB;
  const stickH = H - padB - padT;

  let sticks = "";
  // zero line (x=0) marker if we have imaginary modes
  if (imag.length) {
    sticks += `<line x1="${x(0).toFixed(1)}" y1="${padT}" x2="${x(0).toFixed(1)}" y2="${baseY}" stroke="var(--border)" stroke-dasharray="3 3"/>`;
  }
  for (const f of zero)
    sticks += `<line class="freq-stick zero" x1="${x(f).toFixed(1)}" y1="${baseY}" x2="${x(f).toFixed(1)}" y2="${(baseY - stickH*0.25).toFixed(1)}"/>`;
  for (const f of real)
    sticks += `<line class="freq-stick" x1="${x(f).toFixed(1)}" y1="${baseY}" x2="${x(f).toFixed(1)}" y2="${padT}"/>`;
  for (const f of imag)
    sticks += `<line class="freq-stick imag" x1="${x(f).toFixed(1)}" y1="${baseY}" x2="${x(f).toFixed(1)}" y2="${padT}"/>`;

  const warn = nImaginary > 0
    ? `<div class="freq-warn">⚠ ${nImaginary} imaginary mode${nImaginary>1?"s":""} (negative frequency) — this is a saddle point, not a minimum. Re-optimize (try TightOpt / a tighter grid) before trusting thermochemistry.</div>`
    : `<div class="hint" style="margin-top:6px">No imaginary modes — a local minimum.</div>`;

  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">Vibrational frequencies (${real.length} real${imag.length?`, <span style="color:var(--err)">${imag.length} imaginary</span>`:""})</div>
    <svg class="spectrum" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      <line x1="${padL}" y1="${baseY}" x2="${W-padR}" y2="${baseY}" stroke="var(--border)"/>
      ${sticks}
      <text class="mono" x="${x(minF).toFixed(1)}" y="${H-8}" fill="var(--muted-foreground)" font-size="10">${minF.toFixed(0)}</text>
      <text class="mono" x="${(W-padR-50)}" y="${H-8}" fill="var(--muted-foreground)" font-size="10">${maxF.toFixed(0)} cm⁻¹</text>
    </svg>
    ${warn}`;
}

/** @param {NmrPayload[]} nmr */
function renderNmr(nmr) {
  const body = document.getElementById("result-body");
  let rows = "";
  for (const n of nmr) {
    rows += `<tr><td>${n.idx} ${escapeHtml(n.el)}</td><td>${n.iso.toFixed(3)}</td><td>${n.aniso.toFixed(3)}</td></tr>`;
  }
  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">NMR chemical shielding (ppm)</div>
    <div style="max-height:280px;overflow:auto">
      <table class="data">
        <thead><tr><th>Nucleus</th><th>Isotropic</th><th>Anisotropy</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="hint">Absolute shieldings — reference subtraction (e.g. TMS) for chemical shifts.</div>`;
}

/** @param {NebPointPayload[]} path @param {string} [kind] "neb" | "irc" */
function renderNebPath(path, kind) {
  const body = document.getElementById("result-body");
  if (!path || !path.length) return;
  // Both NEB-TS and IRC print a PATH SUMMARY table; the parser records which
  // one matched. An IRC profile is not made of NEB "images", and (for a one-
  // sided IRC) its endpoints aren't reactant/product — label per kind.
  const isIrc = kind === "irc";

  const de = path.map(p => p.de_kcal);
  const lo = Math.min(...de, 0), hi = Math.max(...de, 0);
  const span = (hi - lo) || 1;
  const W = 640, H = 240, padL = 48, padR = 20, padT = 20, padB = 40;
  const n = path.length;
  const x = i => padL + (n === 1 ? 0.5 : i / (n - 1)) * (W - padL - padR);
  const y = v => padT + (hi - v) / span * (H - padT - padB);

  // path line through the points
  const pts = path.map((p, i) => `${x(i).toFixed(1)},${y(p.de_kcal).toFixed(1)}`).join(" ");
  let dots = "";
  path.forEach((p, i) => {
    const cx = x(i).toFixed(1), cy = y(p.de_kcal).toFixed(1);
    if (p.is_ts) {
      dots += `<circle cx="${cx}" cy="${cy}" r="5.5" fill="var(--err)"/>` +
              `<text x="${cx}" y="${(y(p.de_kcal)-10).toFixed(1)}" fill="var(--err)" font-size="11" text-anchor="middle" font-weight="600">TS ${p.de_kcal.toFixed(1)}</text>`;
    } else {
      dots += `<circle cx="${cx}" cy="${cy}" r="3.5" fill="var(--foreground)"/>`;
    }
  });
  // reactant / product labels (first and last) — NEB only: an IRC's endpoints
  // depend on its direction (a forward- or backward-only IRC starts at the TS)
  const first = path[0], last = path[n - 1];
  const reactLbl = isIrc ? "" : `<text x="${x(0).toFixed(1)}" y="${H-padB+16}" fill="var(--muted-foreground)" font-size="10" text-anchor="middle">reactant</text>`;
  const prodLbl  = isIrc ? "" : `<text x="${x(n-1).toFixed(1)}" y="${H-padB+16}" fill="var(--muted-foreground)" font-size="10" text-anchor="middle">product</text>`;
  // zero baseline
  const zeroY = y(0).toFixed(1);

  const ts = path.find(p => p.is_ts);
  const barrier = ts ? ts.de_kcal : Math.max(...de);
  const dErxn = last.de_kcal - first.de_kcal;

  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">${isIrc ? `IRC path (${n} steps)` : `NEB-TS reaction path (${n} images)`}</div>
    <svg class="spectrum" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      <line x1="${padL}" y1="${zeroY}" x2="${W-padR}" y2="${zeroY}" stroke="var(--border)" stroke-dasharray="3 3"/>
      <polyline points="${pts}" fill="none" stroke="var(--muted-foreground)" stroke-width="1.5"/>
      ${dots}
      <text class="mono" x="14" y="${padT+4}" fill="var(--muted-foreground)" font-size="10">${hi.toFixed(1)}</text>
      <text class="mono" x="14" y="${(H-padB).toFixed(1)}" fill="var(--muted-foreground)" font-size="10">${lo.toFixed(1)}</text>
      <text x="14" y="${H/2}" fill="var(--muted-foreground)" font-size="10" transform="rotate(-90 14 ${H/2})" text-anchor="middle">ΔE (kcal/mol)</text>
      ${reactLbl}${prodLbl}
    </svg>
    <div class="hint">${isIrc
      ? `Relative energies along the intrinsic reaction coordinate (electronic, not free energies).`
      : `Forward barrier ≈ <b>${barrier.toFixed(1)} kcal/mol</b>; reaction energy ΔE ≈ <b>${dErxn.toFixed(1)} kcal/mol</b>. Energies are from the NEB path summary (electronic, not free energies).`}</div>`;
}

// ---- free energy profile (Results tab) ----
/** @type {FepPoint[]} */
let _fepPoints = [];   // cached [{name, gibbs_eh, ...}] in queue order

async function loadFreeEnergyProfile() {
  try {
    const res = /** @type {FepResult} */ (JSON.parse(await bridge.get_free_energy_profile()));
    _fepPoints = (res.ok && res.points) ? res.points : [];
  } catch (e) { _fepPoints = []; }
  // populate the reference dropdown (default: first point = RC)
  const refSel = document.getElementById("fep-ref");
  if (refSel) {
    const cur = refSel.value;
    refSel.innerHTML = _fepPoints.length
      ? _fepPoints.map((p, i) => `<option value="${i}">ref: ${escapeHtml(p.name)}</option>`).join("")
      : `<option value="">—</option>`;
    if (cur && _fepPoints[cur]) refSel.value = cur;
  }
  renderFreeEnergyProfile();
}

function renderFreeEnergyProfile() {
  const body = document.getElementById("fep-body");
  if (!body) return;
  if (!_fepPoints.length) {
    body.innerHTML = `<div class="hint">No finished frequency calculations yet — Freq calculations build the profile.</div>`;
    return;
  }
  const units = (document.getElementById("fep-units") || {}).value || "kcal";
  const factor = units === "kj" ? 2625.499639 : 627.5094740631;  // Hartree -> kJ/mol or kcal/mol
  const unitLabel = units === "kj" ? "kJ/mol" : "kcal/mol";
  const refIdx = parseInt((document.getElementById("fep-ref") || {}).value, 10) || 0;
  const ref = _fepPoints[refIdx] ? _fepPoints[refIdx].gibbs_eh : _fepPoints[0].gibbs_eh;

  // relative energies
  const pts = _fepPoints.map(p => ({ name: p.name, dg: (p.gibbs_eh - ref) * factor }));

  // SVG geometry
  const W = 660, H = 300, padL = 56, padR = 20, padT = 24, padB = 56;
  const n = pts.length;
  const dgs = pts.map(p => p.dg);
  let lo = Math.min(0, ...dgs), hi = Math.max(0, ...dgs);
  if (hi === lo) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.12 || 1;
  lo -= pad; hi += pad;
  const x = i => padL + (n === 1 ? 0.5 : i / (n - 1)) * (W - padL - padR);
  const y = v => padT + (hi - v) / (hi - lo) * (H - padT - padB);
  const levelHalf = Math.min(34, (W - padL - padR) / (n * 2.4));  // half-width of each level bar

  let svg = "";
  // zero baseline ("3 3" dashes: same rhythm as every other zero/goal line)
  svg += `<line x1="${padL}" y1="${y(0).toFixed(1)}" x2="${W-padR}" y2="${y(0).toFixed(1)}" stroke="var(--border)" stroke-dasharray="3 3"/>`;
  svg += `<text x="${padL-8}" y="${y(0).toFixed(1)}" text-anchor="end" dominant-baseline="middle" class="scf-axis">0</text>`;
  // connectors (dashed, sloping between level ends)
  for (let i = 0; i < n - 1; i++) {
    svg += `<line x1="${(x(i)+levelHalf).toFixed(1)}" y1="${y(pts[i].dg).toFixed(1)}" x2="${(x(i+1)-levelHalf).toFixed(1)}" y2="${y(pts[i+1].dg).toFixed(1)}" stroke="var(--muted-foreground)" stroke-width="1" stroke-dasharray="3 3" opacity="0.6"/>`;
  }
  // level bars + labels
  for (let i = 0; i < n; i++) {
    const px = x(i), py = y(pts[i].dg);
    svg += `<line x1="${(px-levelHalf).toFixed(1)}" y1="${py.toFixed(1)}" x2="${(px+levelHalf).toFixed(1)}" y2="${py.toFixed(1)}" stroke="var(--foreground)" stroke-width="2.5"/>`;
    // dG value above the bar
    svg += `<text x="${px.toFixed(1)}" y="${(py-8).toFixed(1)}" text-anchor="middle" style="font-size:11px;fill:var(--foreground);font-weight:600">${pts[i].dg.toFixed(1)}</text>`;
    // name below, rotated if many
    const label = pts[i].name.length > 10 ? pts[i].name.slice(0, 9) + "…" : pts[i].name;
    svg += `<text x="${px.toFixed(1)}" y="${(H-padB+16).toFixed(1)}" text-anchor="middle" style="font-size:10px;fill:var(--muted-foreground)">${escapeHtml(label)}</text>`;
  }
  // y axis title
  svg += `<text x="14" y="${(padT+(H-padT-padB)/2).toFixed(1)}" text-anchor="middle" transform="rotate(-90 14 ${(padT+(H-padT-padB)/2).toFixed(1)})" class="scf-axis-title">ΔG (${unitLabel})</text>`;

  body.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">${svg}</svg>
    <div class="hint">Relative to <b>${escapeHtml(_fepPoints[refIdx] ? _fepPoints[refIdx].name : "")}</b> (= 0). Each level is a finished frequency calculation; values are ΔG in ${unitLabel}. Order follows the queue.</div>`;
}
