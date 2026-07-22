// @ts-check
/* ============================================================
   ORCAdesk front-end logic — calculation-based queue

   Payload typedefs (CalcSummary, QueueSnapshot, LogPayload, ...) live in
   web/types.js and mirror the Python serialization layer field by field;
   the bridge/Qt environment is declared in web/globals.d.ts.
   ============================================================ */

/** @type {OrcaBridge|null} */
let bridge = null;
/** @type {Partial<SettingsPayload>} */
let settings = {};
/** The store snapshot calc reduced to what the UI renders (see mirrorCalc).
 * @typedef {Omit<CalcSummary, "meta">} CalcMirror */
/** @type {CalcMirror[]} */
let queue = [];                 // UI mirror of the shared store's queue
let directXyz = "";             // last loaded .xyz coordinate block
/** @type {Object<string, [string, string, string][]>} */
const calcResults = {};         // name -> summaryRows (label/value/category)
/** @type {Object<string, ParsePayload>} */
const _resultExtras = {};       // name -> full parsed payload (everything but is keyed for re-render)
let showAllResults = false;     // "Show all" toggle: ignore per-calc-type gating
/** @type {ParsePayload|null} */
let _currentResult = null;      // last-rendered payload, for re-render on toggle
let _currentResultName = "";    // queue calc name of the shown result ("" for an external Open .out) — the CREST conformer->ORCA action needs it
/** @type {Object<string, CalcInput|CalcFull>} */
const localCalcs = {};          // name -> full calc (config/xyz/raw) added on THIS PC,
                                // so editing keeps the details the store snapshot omits

/** @type {string|null} */
let editName = null;            // NAME of the queue calc being edited, or null for "new".
                                // A name, never an index: the queue can shift under an
                                // open edit (remove/reorder here, phone adds, conformer
                                // fan-out), and a stale index made Update overwrite an
                                // unrelated calculation.
let rawMode = false;            // is the current build form in raw mode?
let rawText = "";               // current raw .inp text being edited
// Build backends: DFT (with Beginner/Expert sub-modes), MLIP, CREST. The
// persisted Settings.build_mode keeps the four historical values — "beginner"
// and "expert" are the two DFT sub-modes, so old settings load unchanged.
let buildMode = "beginner";     // "beginner"/"expert" (DFT sub-modes), "mlip" (MACE), or "crest" (conformer search via WSL)
let _dftSub = "beginner";       // last DFT sub-mode — clicking the main DFT button restores it
// controls hidden in expert mode (the guided method form + charge/mult; the
// raw text carries its own "* xyz charge mult" line)
const _EXPERT_HIDDEN = ["card-method", "field-charge", "field-mult"];
// the whole ORCA build UI — hidden in mlip/crest mode (which show their own card)
const _ORCA_BUILD = ["card-calc", "card-geometry", "card-method", "raw-card", "build-actions"];
function _showIds(ids, on) {
  ids.forEach(id => { const e = document.getElementById(id); if (e) e.style.display = on ? "" : "none"; });
}
let _running = false;           // mirrors store.running
let _stopRequested = false;     // user asked to stop after the current job
let _starting = false;          // runQueue() is mid-start (guards the pre-_running await window)

// Calculations the user can still edit / remove / reorder: pending, cancelled,
// or blocked. Mirrors EDITABLE_STATES in store.py — P24 amended: FAILED is
// locked, so the failure evidence (.out, message, the exact input that failed)
// can't be edited away before it's inspected. done/running stay frozen too.
function isEditableState(state) {
  return state === "pending" || state === "cancelled" || state === "blocked";
}

// per-kind defaults for the config form
const KIND_DEFS = {
  opt:     { calcGroup: "calculation_types_geometry",  calcDefault: "TightOpt", scfDefault: "TightSCF",     showMaxIter: true,  showTddft: false, showFreq: false, showNmr: false, allTypes: false, options: "" },
  opt_freq:    { calcGroup: null,                      calcDefault: "TightOpt Freq", scfDefault: "VeryTightSCF", showMaxIter: true, showTddft: false, showFreq: true,  showNmr: false, allTypes: false, options: "", showIrc: false, showNeb: false },
  ts_opt:  { calcGroup: "calculation_types_geometry",  calcDefault: "OptTS",    scfDefault: "TightSCF",     showMaxIter: true,  showTddft: false, showFreq: false, showNmr: false, allTypes: false, options: "" },
  ts_opt_freq: { calcGroup: null,                      calcDefault: "OptTS Freq",    scfDefault: "VeryTightSCF", showMaxIter: true, showTddft: false, showFreq: true,  showNmr: false, allTypes: false, options: "", showIrc: false, showNeb: false },
  freq:    { calcGroup: "calculation_types_frequency", calcDefault: "Freq",     scfDefault: "VeryTightSCF", showMaxIter: false, showTddft: false, showFreq: true,  showNmr: false, allTypes: false, options: "" },
  ts_freq: { calcGroup: "calculation_types_frequency", calcDefault: "Freq",     scfDefault: "VeryTightSCF", showMaxIter: false, showTddft: false, showFreq: true,  showNmr: false, allTypes: false, options: "" },
  tddft:   { calcGroup: null,                          calcDefault: "",         scfDefault: "TightSCF",     showMaxIter: false, showTddft: true,  showFreq: false, showNmr: false, allTypes: false, options: "" },
  nmr:     { calcGroup: null,                          calcDefault: "NMR",      scfDefault: "TightSCF",     showMaxIter: false, showTddft: false, showFreq: false, showNmr: true,  allTypes: false, options: "" },
  sp:      { calcGroup: "calculation_types_energy",    calcDefault: "SP",       scfDefault: "TightSCF",     showMaxIter: false, showTddft: false, showFreq: false, showNmr: false, allTypes: false, options: "", showIrc: false, showNeb: false },
  irc:     { calcGroup: null,                          calcDefault: "IRC",      scfDefault: "TightSCF",     showMaxIter: false, showTddft: false, showFreq: false, showNmr: false, allTypes: false, options: "", showIrc: true,  showNeb: false },
  neb_ts:  { calcGroup: null,                          calcDefault: "NEB-TS",   scfDefault: "TightSCF",     showMaxIter: false, showTddft: false, showFreq: false, showNmr: false, allTypes: false, options: "FREQ", showIrc: false, showNeb: true },
  general: { calcGroup: null,                          calcDefault: "",         scfDefault: "TightSCF",     showMaxIter: false, showTddft: false, showFreq: false, showNmr: false, allTypes: true,  options: "", showIrc: false, showNeb: false },
};

let choicesCache = {};
let _configKind = "";           // calc kind the method form was last rendered for (renderConfigForm)

// ---------- bridge bootstrap ----------
new QWebChannel(qt.webChannelTransport, async function(channel) {
  bridge = channel.objects.bridge;

  await loadAllChoices();
  await loadSettings();
  await loadAbout();
  if (SCFGraph && SCFGraph.setEtaMode && settings.eta_mode) SCFGraph.setEtaMode(settings.eta_mode);
  if (SCFGraph && SCFGraph.setGeoMode && settings.geo_graph_mode) SCFGraph.setGeoMode(settings.geo_graph_mode);
  renderConfigForm("opt");
  if (settings.build_mode) setBuildMode(settings.build_mode, false);   // apply saved build mode (no re-save)

  // The queue + log now live in a shared store (also used by the phone). We
  // poll cheap getters instead of using Qt signals, so the desktop reflects
  // changes made from any device, and the store's worker thread stays cleanly
  // separated from the UI thread.
  await refreshQueue();
  startPolling();

  appendLog("Ready.", "info");
});

// ---------- drag & drop of files (from Explorer) ----------
// window.py captures the OS-level drop, resolves the real file path, and calls
// one of these by extension: .inp -> Build raw editor, .xyz -> Build geometry,
// .out -> Results. Using the path (not FileReader content) keeps huge .out files
// off the JS heap. preventDefault stops Chromium navigating away if a stray drop
// reaches the page.
window.addEventListener("dragover", (e) => e.preventDefault(), false);
window.addEventListener("drop", (e) => e.preventDefault(), false);

window.onInpDropped = async function (path) {
  if (!bridge) return;
  try {
    const res = /** @type {LoadResult} */ (JSON.parse(await bridge.load_inp_path(path)));
    if (res.cancelled) return;   // deliberate dismissal, never an error
    if (!res.ok) { failNotify("Could not read that .inp."); return; }
    // a drop starts a NEW calc: never load into an in-progress edit (the
    // same-mode no-op in setBuildMode would otherwise leave the edit active
    // and Update would silently overwrite the edited calc with this file)
    if (editName !== null) exitEditMode();
    setBuildMode("expert");
    enterRawWithText(res.text);
    const nameEl = document.getElementById("calc-name");
    if (nameEl && res.name && !nameEl.value.trim()) nameEl.value = res.name;
    switchTab("build");
    appendLog(`Dropped .inp loaded${res.name ? " (" + res.name + ")" : ""}. Next: calc type, then Add to queue.`, "ok");
  } catch (e) { failNotify("Could not load the dropped file."); }
};

window.onXyzDropped = async function (path) {
  if (!bridge) return;
  // The OS drop is window-level and mode-agnostic (window.py routes by
  // extension only), so deliver the geometry to the card the user is looking
  // at: in MLIP/CREST mode the DFT card is hidden — writing directXyz there
  // would log success while the visible card stays empty and Add refuses.
  if (buildMode === "mlip" || buildMode === "crest") {
    await dropXyzIntoBackendCard(path);
    return;
  }
  if (rawBlocksXyzLoad()) return;
  try {
    const res = /** @type {LoadResult} */ (JSON.parse(await bridge.load_xyz_path(path)));
    if (res.cancelled) return;   // deliberate dismissal, never an error
    if (!res.ok) { failNotify("Could not read that .xyz."); return; }
    directXyz = parseXyzText(res.text);
    const n = directXyz ? directXyz.split("\n").length : 0;
    const st = document.getElementById("xyz-status");
    if (st) st.textContent = n ? `loaded (${n} atoms)` : "No atoms in file.";
    // a dropped file means direct geometry (mirror of the MLIP branch) — but
    // only for the FORM: in raw mode the radio is hidden form state, and a raw
    // {{GEOMETRY}} calc legitimately takes a drop while set to reference
    if (!rawMode) {
      const dr = document.querySelector('input[name="geomsrc"][value="direct"]');
      if (dr && !dr.checked) { dr.checked = true; onGeomSourceChange(); }
    }
    switchTab("build");
    appendLog(`Dropped .xyz loaded (${n} atoms).`, n ? "ok" : "warn");
    nebAtomCheck();   // a replaced reactant must never leave a stale ✓/⚠ verdict
  } catch (e) { failNotify("Could not load the dropped file."); }
};

/** Route a dropped .xyz into the visible MLIP or CREST card (mirror of
 *  loadMlipXyz / loadCrestXyz, which the native-dialog buttons use).
 *  @param {string} path */
async function dropXyzIntoBackendCard(path) {
  const isMlip = buildMode === "mlip";
  // a locked card's Add would refuse anyway — surface the reason at drop time
  if (isMlip ? !_mlipReady : !_crestReady) {
    failNotify(isMlip ? "Ready MACE environment required (see Settings)."
      : "CREST in a WSL distribution required (see Settings → CREST).");
    return;
  }
  try {
    const res = /** @type {LoadResult} */ (JSON.parse(await bridge.load_xyz_path(path)));
    if (res.cancelled) return;   // deliberate dismissal, never an error
    if (!res.ok) { failNotify("Could not read that .xyz."); return; }
    const coords = parseXyzText(res.text);
    const n = coords ? coords.split("\n").length : 0;
    if (isMlip) mlipXyz = coords; else crestXyz = coords;
    const st = document.getElementById(isMlip ? "mlip-xyz-status" : "crest-xyz-status");
    if (st) st.textContent = n ? `loaded (${n} atoms)` : "No atoms in file.";
    if (isMlip) {
      // a dropped file means direct geometry — reveal the branch that shows it
      const dr = document.querySelector('input[name="mlip-geomsrc"][value="direct"]');
      if (dr && !dr.checked) {
        dr.checked = true;
        onMlipGeomSourceChange();
      }
    }
    switchTab("build");
    appendLog(`Dropped .xyz loaded into the ${isMlip ? "MLIP" : "CREST"} card (${n} atoms).`, n ? "ok" : "warn");
  } catch (e) { failNotify("Could not load the dropped file."); }
}

window.onOutDropped = async function (path) {
  if (!bridge) return;
  try {
    const raw = await bridge.parse_out_path(path);
    /** @type {ParsePayload} */
    let data; try { data = JSON.parse(raw); } catch { toast("Could not parse that .out."); return; }
    if (!data || !data.summary) { toast("Could not parse that .out."); return; }
    // an external file, not a queued calc (mirror openOutFile): a stale queued
    // name here would route Export-as-.xyz to the WRONG calc's ensemble
    _currentResultName = "";
    _currentResult = data;
    renderResult(data);
    switchTab("results");
    appendLog("Dropped .out parsed.", "ok");
  } catch (e) { failNotify("Could not load the dropped file."); }
};

// ---------- shared-store polling ----------
let _logSeq = 0;          // last log sequence number we've shown
let _queueVersion = -1;   // last queue version we've rendered
let _pollTimer = null;

function startPolling() {
  if (_pollTimer) return;
  _pollTimer = setInterval(pollTick, 1000);
}

async function pollTick() {
  // While the window is hidden, skip the work that forces DOM/SVG repaints so
  // Chromium can release renderer memory; we resume on the next visible tick.
  // (The backend keeps buffering; we catch up from _logSeq when shown again.)
  if (document.hidden) return;
  try {
    // new log lines
    const logRes = /** @type {LogPayload} */ (JSON.parse(await bridge.get_log(_logSeq)));
    if (logRes && logRes.lines) {
      for (const ln of logRes.lines) appendLog(ln.msg, ln.level);
      if (typeof logRes.latest === "number") _logSeq = logRes.latest;
    }
    // queue changes (only re-render if version changed)
    const snap = /** @type {QueueSnapshot} */ (JSON.parse(await bridge.get_queue()));
    if (snap && snap.version !== _queueVersion) {
      _queueVersion = snap.version;
      queue = (snap.calculations || []).map(mirrorCalc);
      renderQueue();
      // Poll-delivered queue changes (a phone client's add, the engine's
      // conformer fan-out substitution) must keep the reference dropdowns live
      // too — same visibility-gated refresh as refreshQueue, both refreshers
      // preserve the current selection so re-running them is safe.
      if (document.getElementById("geom-reference")?.style.display === "block") refreshRefSelect();
      if (document.getElementById("mlip-geom-reference")?.style.display === "block") refreshMlipRefSelect();
      _running = !!snap.running;
      setRunUI(_running);
      // auto-load results for any finished calculation
      for (const c of queue) {
        if (c.state === "done" && c.output_path) maybeFetchResult(c.name, c.output_path);
      }
    } else if (snap) {
      if (!!snap.running !== _running) { _running = !!snap.running; setRunUI(_running); }
    }
    // seed the graph from the full .out for a reattached / finished-while-closed
    // opt whose live stream didn't capture its history (see maybeSeedGraph)
    await maybeSeedGraph();
    await maybeSeedCrestGraph();
    // small "~N s / SCF cycle" pace indicator — lives in the graph summary's
    // progress meta line, so the span only exists while the SCF panel is shown;
    // keep it fresh between (throttled) panel re-renders
    const _paceEl = document.getElementById("scf-pace");
    if (_paceEl) _paceEl.textContent = scfPaceText();
    // stepper clocks (current stage + total elapsed) tick in place between
    // full re-renders, so they don't freeze while the log is silent
    if (SCFGraph && _logMode === "graph") {
      document.querySelectorAll("#scf-panel [data-clock]").forEach(function (el) {
        el.textContent = SCFGraph.fmtClock((Date.now() - Number(el.getAttribute("data-clock"))) / 1000);
      });
    }
    // redraw SCF graph at most once per tick, only if new data arrived
    if (_logMode === "graph" && _scfDirty) renderSCFPanel();
  } catch (e) { /* transient; try again next tick */ }
}
// Rebuild the SCF/opt graph history from the .out on disk for an opt calc the
// live stream never fully saw: a job reattached after a close (its monitor now
// tails from EOF, so earlier cycles never stream) or one that FINISHED while
// ORCAdesk was closed (no monitor ran at all). Fresh-launch calcs are skipped
// because appendLog's start marker already added them to _seededGraph and the
// live stream owns their graph. Idempotent: GeoTracker keys steps by cycle, so
// any overlap with subsequent live lines overwrites rather than duplicates.
async function maybeSeedGraph() {
  if (!SCFGraph) return;
  let target = (queue || []).find(c => c.state === "running" && _OPT_KINDS.includes(c.kind));
  // The done-calc fallback only fires while NOTHING is running: a live
  // non-opt job (MLIP, CREST) must never have a finished calc's replayed
  // graph seeded over its stream (e.g. a mid-run log Clear resets the
  // trackers and would otherwise trigger exactly that).
  const anyRunning = (queue || []).some(c => c.state === "running");
  if (!target && !anyRunning && _geoTracker && !_geoTracker.hasData()) {
    // no live opt running: fill an empty graph from the most recent done opt
    const dones = (queue || []).filter(c => c.state === "done" && _OPT_KINDS.includes(c.kind));
    target = dones.length ? dones[dones.length - 1] : null;
  }
  if (!target || _seededGraph.has(target.name)) return;
  _seededGraph.add(target.name);   // guard before the await: no double-seed across overlapping ticks
  try {
    const r = /** @type {GraphLinesResult} */ (JSON.parse(await bridge.get_graph_lines(target.name)));
    if (r && r.ok && r.lines && r.lines.length) {
      const t = new SCFGraph.SCFTracker();
      const g = new SCFGraph.GeoTracker();
      for (const ln of r.lines) { t.push(ln); g.push(ln); }   // offline: never via appendLog (no DOM flood)
      _scfTracker = t; _geoTracker = g; _scfDirty = true;
    }
  } catch (e) {
    _seededGraph.delete(target.name);   // let a later tick retry
  }
}

// CREST analogue of maybeSeedGraph: a conformer search reattached after a close
// (or finished while ORCAdesk was closed) never streamed its history, so rebuild
// the CrestTracker from the .out on disk. Fresh-launch CREST calcs are already in
// _seededGraph (appendLog's start marker), so the live stream owns them.
async function maybeSeedCrestGraph() {
  if (!SCFGraph) return;
  let target = (queue || []).find(c => c.state === "running" && (c.kind || "").startsWith("crest"));
  // same running-gate as maybeSeedGraph: a DONE search must not take the
  // graph panel over from a live non-CREST job (CrestTracker with data wins
  // the panel priority in renderSCFPanel)
  const anyRunning = (queue || []).some(c => c.state === "running");
  if (!target && !anyRunning && _crestTracker && !_crestTracker.hasData()) {
    const dones = (queue || []).filter(c => c.state === "done" && (c.kind || "").startsWith("crest"));
    target = dones.length ? dones[dones.length - 1] : null;
  }
  if (!target || _seededGraph.has(target.name)) return;
  _seededGraph.add(target.name);
  try {
    const r = /** @type {GraphLinesResult} */ (JSON.parse(await bridge.get_graph_lines(target.name)));
    if (r && r.ok && r.lines && r.lines.length) {
      const c = new SCFGraph.CrestTracker();
      for (const ln of r.lines) c.push(ln);   // offline: never via appendLog
      c.noTimes = true;   // replayed in one burst — its wall-clock stamps are meaningless
      _crestTracker = c; _scfDirty = true;
    }
  } catch (e) {
    _seededGraph.delete(target.name);   // let a later tick retry
  }
}

// turn a store snapshot calc into the shape the UI render expects
/** @param {CalcSummary} c @returns {CalcMirror} */
function mirrorCalc(c) {
  return {
    name: c.name, kind: c.kind, state: c.state, message: c.message,
    is_raw: c.is_raw, charge: c.charge, multiplicity: c.multiplicity,
    geometry_source: c.geometry_source, ref_name: c.ref_name,
    conformer_origin: c.conformer_origin || "",
    output_path: c.output_path || "",
    scf_convergence: c.scf_convergence || "TightSCF",
    mlip_model: c.mlip_model || "",
    crest_method: c.crest_method || "",
    crest_handoff: c.crest_handoff || "",
    // config/xyz aren't returned by the snapshot; editing pulls from here only
    // for display. (Full re-edit of phone-added calcs is a later refinement.)
  };
}

async function refreshQueue() {
  try {
    const snap = /** @type {QueueSnapshot} */ (JSON.parse(await bridge.get_queue()));
    _queueVersion = snap.version;
    queue = (snap.calculations || []).map(mirrorCalc);
    renderQueue();
    // Keep the reference dropdowns live: they're only rebuilt when the geometry
    // source is toggled, so a calc added while "From another calculation" was
    // already selected would otherwise be missing from a stale list. Both
    // refreshers preserve the current selection, so re-running them is safe.
    if (document.getElementById("geom-reference")?.style.display === "block") refreshRefSelect();
    if (document.getElementById("mlip-geom-reference")?.style.display === "block") refreshMlipRefSelect();
  } catch (e) { /* ignore */ }
}

async function loadAbout() {
  try {
    const a = /** @type {AboutPayload} */ (JSON.parse(await bridge.get_about()));
    // single source of truth for the version: APP_VERSION (paths.py) flows here
    // via get_about(), so the top-bar badge never drifts from CHANGELOG/README.
    const badge = document.getElementById("ver-badge");
    if (badge) badge.textContent = a.version.replace("-", " ");
    const body = document.getElementById("about-body");
    body.innerHTML =
      `<div class="k">Version</div><div class="v">${a.version}</div>` +
      `<div class="k">Developed by</div><div class="v">${escapeHtml(a.author)}</div>` +
      `<div class="k">Organization</div><div class="v">${escapeHtml(a.org)}</div>` +
      `<div class="k">Contact</div><div class="v">${escapeHtml(a.email || "")}</div>` +
      `<div class="k">License</div><div class="v">MIT</div>`;
  } catch (e) { /* ignore */ }
}

// ---------- choices ----------
async function loadAllChoices() {
  const names = ["functionals","basis_sets","calculation_types","scf_convergences","ri_approximations","solvents","mace_models"];
  for (const n of names) {
    try { choicesCache[n] = /** @type {ChoiceGroups} */ (JSON.parse(await bridge.load_choices(n))); }
    catch (e) { choicesCache[n] = {}; }
  }
}
function flatItems(groups, onlyGroup) {
  const out = [];
  for (const [k, items] of Object.entries(groups || {})) {
    if (onlyGroup && k !== onlyGroup) continue;
    out.push(...items);
  }
  return out;
}
// human-readable labels for the JSON group keys (ascending level order is
// already encoded by the order of keys in the data files)
const GROUP_LABELS = {
  // functionals (Jacob's ladder, low -> high)
  lda: "LDA", gga: "GGA", meta_gga: "meta-GGA", hybrid: "hybrid GGA",
  meta_gga_hybrid: "hybrid meta-GGA", range_separated_hybrid: "range-separated hybrid",
  double_hybrid: "double hybrid", composite_3c: "composite (3c)",
  wavefunction_methods: "wavefunction (HF/post-HF)", semiempirical: "semi-empirical",
  // basis sets (small -> large / specialized)
  pople_minimal: "Pople — minimal", pople_split_valence: "Pople — split-valence",
  pople_polarized: "Pople — polarized", karlsruhe_def2: "Karlsruhe def2",
  karlsruhe_def2_diffuse: "Karlsruhe def2 (diffuse)",
  karlsruhe_relativistic_zora: "Karlsruhe (ZORA)", karlsruhe_relativistic_dkh: "Karlsruhe (DKH)",
  correlation_consistent_dunning: "Dunning cc", correlation_consistent_core_valence: "Dunning cc (core-valence)",
  correlation_consistent_relativistic: "Dunning cc (relativistic)", f12_basis: "F12",
  ano_basis: "ANO", jensen_pcseg: "Jensen pcseg", composite_method_internal: "composite (internal)",
  auxiliary_coulomb_J: "auxiliary (/J)", auxiliary_coulomb_exchange_JK: "auxiliary (/JK)",
  auxiliary_correlation_C: "auxiliary (/C)", f12_cabs: "F12 CABS",
};
function prettyGroup(key) {
  return GROUP_LABELS[key] || key.replace(/_/g, " ");
}
// fill a <select> preserving group structure as <optgroup>s (level-ordered)
function fillGroupedSelect(sel, groups, def) {
  if (!sel) return;
  sel.innerHTML = "";
  for (const [key, items] of Object.entries(groups || {})) {
    if (!items || !items.length) continue;
    const og = document.createElement("optgroup");
    og.label = prettyGroup(key);
    for (const it of items) {
      const o = document.createElement("option");
      o.value = it; o.textContent = it; og.appendChild(o);
    }
    sel.appendChild(og);
  }
  if (def) {
    // select the default if present anywhere
    const all = flatItems(groups);
    if (all.includes(def)) sel.value = def;
  }
}
function fillSelect(sel, items, def) {
  if (!sel) return;
  sel.innerHTML = "";
  for (const it of items) {
    const o = document.createElement("option");
    o.value = it; o.textContent = it; sel.appendChild(o);
  }
  if (def && items.includes(def)) sel.value = def;
}
// ---- custom searchable combobox (search + group headers + scroll + free text) ----
// Registry of combo instances by container id, so editCalc can set values.
const _combos = {};
// Build a combobox inside container `#combo-<key>`. `groups` is the level-ordered
// {groupKey: [items]} dict. The input keeps any typed value (out-of-list allowed).
function setupCombo(containerId, groups, def) {
  const root = document.getElementById(containerId);
  if (!root) return;
  const input = root.querySelector(".combo-input");
  const list = root.querySelector(".combo-list");
  // flatten with group tags, preserving order
  const entries = [];   // {value, group}
  for (const [key, items] of Object.entries(groups || {})) {
    if (!items || !items.length) continue;
    const label = prettyGroup(key);
    for (const it of items) entries.push({ value: it, group: label });
  }
  let activeIdx = -1;    // highlighted row index (into the currently rendered list)
  let rendered = [];     // current filtered entries (flat, excluding headers)

  function render(filter) {
    const q = (filter || "").trim().toLowerCase();
    list.innerHTML = "";
    rendered = [];
    let lastGroup = null;
    let count = 0;
    for (const e of entries) {
      if (q && !e.value.toLowerCase().includes(q)) continue;
      if (e.group !== lastGroup) {
        const h = document.createElement("div");
        h.className = "combo-group";
        h.textContent = e.group;
        list.appendChild(h);
        lastGroup = e.group;
      }
      const row = document.createElement("div");
      row.className = "combo-item";
      row.textContent = e.value;
      const idx = rendered.length;
      row.addEventListener("mousedown", (ev) => {
        // mousedown (not click) so it fires before input blur
        ev.preventDefault();
        choose(e.value);
      });
      row.addEventListener("mouseenter", () => setActive(idx));
      list.appendChild(row);
      rendered.push({ value: e.value, el: row });
      count++;
    }
    if (count === 0) {
      const none = document.createElement("div");
      none.className = "combo-none";
      none.textContent = q ? `No match — "${filter}" will be used as-is` : "No options";
      list.appendChild(none);
    }
    activeIdx = -1;
  }
  function open() {
    // show the full list on focus (don't pre-filter by the current value, so
    // the user can browse freely); highlight the current value if it's present
    render("");
    list.style.display = "block";
    const cur = input.value;
    const i = rendered.findIndex((r) => r.value === cur);
    if (i >= 0) setActive(i);
  }
  function close() { list.style.display = "none"; activeIdx = -1; }
  function isOpen() { return list.style.display !== "none"; }
  function choose(val) { input.value = val; close(); input.dispatchEvent(new Event("change", { bubbles: true })); }
  function setActive(i) {
    if (activeIdx >= 0 && rendered[activeIdx]) rendered[activeIdx].el.classList.remove("active");
    activeIdx = i;
    if (activeIdx >= 0 && rendered[activeIdx]) {
      rendered[activeIdx].el.classList.add("active");
      rendered[activeIdx].el.scrollIntoView({ block: "nearest" });
    }
  }

  input.addEventListener("focus", open);
  input.addEventListener("input", () => {
    render(input.value);
    list.style.display = "block";
    // auto-highlight the first match so Enter picks it right away
    if (rendered.length) setActive(0);
  });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      if (!isOpen()) { open(); if (rendered.length) setActive(0); return; }
      setActive(Math.min(activeIdx + 1, rendered.length - 1));
    } else if (ev.key === "ArrowUp") {
      ev.preventDefault();
      if (!isOpen()) return;
      setActive(Math.max(activeIdx - 1, 0));
    } else if (ev.key === "Enter") {
      // pick the highlighted row, or the first match if none highlighted
      if (isOpen() && rendered.length) {
        ev.preventDefault();
        const pick = activeIdx >= 0 ? activeIdx : 0;
        choose(rendered[pick].value);
      }
    } else if (ev.key === "Escape") {
      close();
    } else if (ev.key === "Tab") {
      close();
    }
  });
  // close when focus leaves the combo
  input.addEventListener("blur", () => { setTimeout(close, 120); });

  if (def != null) input.value = def;
  _combos[containerId] = {
    get: () => input.value,
    set: (v) => { input.value = v == null ? "" : v; },
  };
  // initial (hidden) render so the list is ready
  render("");
  close();
}
function comboValue(containerId) {
  const c = _combos[containerId];
  return c ? c.get() : "";
}

// ---------- settings ----------
// ---- theme (light / dark) ----
function applyTheme(theme) {
  const t = theme === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", t);
  const btn = document.getElementById("theme-toggle");
  if (btn) {
    btn.textContent = t === "light" ? "☀" : "☽";   // ☀ / ☽
    btn.title = t === "light" ? "Dark theme" : "Light theme";
  }
}
async function toggleTheme() {
  const next = (settings.theme === "light") ? "dark" : "light";
  applyTheme(next);   // flip the UI instantly, then persist
  const res = /** @type {SaveSettingsResult} */ (JSON.parse(await bridge.save_settings(JSON.stringify({ theme: next }))));
  // bad input comes back as {error} — don't clobber the settings mirror with it
  if ("error" in res) { failNotify("Could not save theme: " + res.error); return; }
  settings = res;
}

async function loadSettings() {
  settings = /** @type {SettingsPayload} */ (JSON.parse(await bridge.get_settings()));
  applyTheme(settings.theme);
  // theme *variant* (shadcn / liquidglass) + intensity — orthogonal to light/dark
  applyThemeVariant(settings.theme_variant, settings.glass_level);
  if (settings.theme_variant === "liquidglass") await initWallpaper();
  document.getElementById("set-orca").value = settings.orca_path || "";
  document.getElementById("set-ws").value = settings.workspace_root || "";
  document.getElementById("set-nprocs").value = String(settings.default_nprocs || 6);
  document.getElementById("set-maxcore").value = String(settings.default_maxcore_mb || 2400);
  // ETA mode radio
  const mode = settings.eta_mode || "conservative";
  const radio = document.querySelector(`input[name="eta-mode"][value="${mode}"]`);
  if (radio) radio.checked = true;
  // optimization-graph mode radio
  const gmode = settings.geo_graph_mode || "all5";
  const grad = document.querySelector(`input[name="geo-mode"][value="${gmode}"]`);
  if (grad) grad.checked = true;
  updateOrcaStatus(settings.orca_valid);
  // MLIP environments are managed in their own channel (a background probe per
  // env); render from get_mlip_status() and poll while any is still checking.
  pollMlipStatus();
  // CREST readiness (WSL distro probe) is likewise its own background channel.
  pollCrestStatus();
}

// ---- theme variant: shadcn (flat) / Liquid Glass ----
// The whole Liquid-Glass CSS layer is gated on html[data-theme-variant] +
// html[data-glass]; this code only flips those attributes, paints the wallpaper
// canvas, and mirrors the Settings → Appearance controls. Orthogonal to the
// light/dark toggle (applyTheme), which stays on the top-bar ☽ button.
const LG_LEVELS = ["restrained", "moderate", "bold", "vivid", "maximal"];
/** @typedef {{ base:string, prev:string, pools:Array<[string,number,number,number,number]> }} LgWallpaper */
/** Procedural wallpaper presets — `base` fill + additive radial `pools`
 * ([color, cx, cy, radius, alpha] as viewport fractions); `prev` is the swatch
 * gradient. Mirrors the design-preview renderer. @type {Object<string,LgWallpaper>} */
const LG_WALLPAPERS = {
  aurora:   { base:"#0a1622", prev:"linear-gradient(135deg,#2fd6a0,#2a7bff 58%,#7a5cff)", pools:[["#2fd6a0",.2,.28,.55,.8],["#2a7bff",.62,.5,.6,.85],["#3ad16b",.35,.82,.5,.7],["#7a5cff",.85,.2,.5,.7]] },
  aqua:     { base:"#071a2e", prev:"linear-gradient(135deg,#22c3ff,#2a6bff 70%,#0a3a7a)", pools:[["#22c3ff",.25,.3,.55,.8],["#2a6bff",.6,.55,.6,.85],["#1fd6c0",.8,.8,.45,.6],["#0a3a7a",.4,.9,.6,.7]] },
  sunset:   { base:"#2a0e1e", prev:"linear-gradient(135deg,#ffc24d,#ff4d8d 55%,#c04bff)", pools:[["#ffc24d",.2,.8,.5,.8],["#ff6a3d",.5,.6,.55,.8],["#ff4d8d",.7,.35,.55,.85],["#c04bff",.85,.12,.5,.7]] },
  grape:    { base:"#150a2a", prev:"linear-gradient(135deg,#5b4bff,#a44bff 55%,#ff5ca8)", pools:[["#5b4bff",.28,.3,.6,.8],["#a44bff",.62,.5,.55,.85],["#ff5ca8",.8,.8,.5,.7],["#3a7bff",.2,.85,.5,.6]] },
  graphite: { base:"#0c0e14", prev:"linear-gradient(135deg,#2b3550,#35507a)", pools:[["#35507a",.3,.35,.6,.55],["#2b3550",.7,.6,.6,.6],["#3a6a9a",.85,.85,.4,.4]] },
  ocean:    { base:"#04121f", prev:"linear-gradient(135deg,#0a6cff,#00c2c7 60%,#0a3a7a)", pools:[["#0a6cff",.28,.35,.6,.8],["#00c2c7",.7,.6,.55,.7],["#1f8cff",.85,.85,.45,.5],["#062a52",.2,.9,.5,.6]] },
};
const LG_WALL_ORDER = ["aurora", "aqua", "sunset", "grape", "graphite", "ocean"];
// data-URI ceiling for a custom wallpaper — mirrors bridge.py _WALLPAPER_MAX
// (24 MB ≈ an 18 MB image). Checked here so an oversize upload is rejected with
// feedback instead of showing this session then silently vanishing on restart.
const LG_WALL_MAX = 24 * 1024 * 1024;
/** @type {HTMLImageElement|null} */
let _lgCustomImg = null;        // loaded custom wallpaper image (if any)
let _lgCustomData = "";         // its data URI (upload-swatch thumbnail + re-persist)
let _wallpaperInited = false;   // custom image fetched from the backend once

function _clampVariant(v) { return v === "liquidglass" ? "liquidglass" : "shadcn"; }
function _clampLevel(l) { return LG_LEVELS.indexOf(l) >= 0 ? l : "moderate"; }

/** Flip the DOM attributes + Settings UI state for a variant/level. Paints the
 * wallpaper when liquidglass is active. Does NOT persist (callers do). */
function applyThemeVariant(variant, level) {
  const v = _clampVariant(variant), lv = _clampLevel(level);
  const root = document.documentElement;
  root.setAttribute("data-theme-variant", v);
  root.setAttribute("data-glass", lv);
  const bs = document.getElementById("variant-shadcn");
  const bl = document.getElementById("variant-liquidglass");
  if (bs) bs.classList.toggle("on", v === "shadcn");
  if (bl) bl.classList.toggle("on", v === "liquidglass");
  const opts = document.getElementById("lg-options");
  if (opts) opts.style.display = v === "liquidglass" ? "" : "none";
  document.querySelectorAll("#lg-level-row button").forEach(b =>
    b.classList.toggle("on", b.getAttribute("data-level") === lv));
  if (v === "liquidglass") { renderWallpaper(); _lgPulseStart(); }
  else _lgPulseStop();
}

/** Persist a settings patch; refresh the mirror. Returns false on backend error. */
async function _persistAppearance(patch) {
  const res = /** @type {SaveSettingsResult} */ (JSON.parse(await bridge.save_settings(JSON.stringify(patch))));
  if ("error" in res) { failNotify("Could not save appearance: " + res.error); return false; }
  settings = res;
  return true;
}

async function setThemeVariant(variant) {
  const v = _clampVariant(variant);
  settings.theme_variant = v;
  applyThemeVariant(v, settings.glass_level);
  if (v === "liquidglass") await initWallpaper();
  await _persistAppearance({ theme_variant: v });
}

async function setGlassLevel(level) {
  const lv = _clampLevel(level);
  settings.glass_level = lv;
  applyThemeVariant(settings.theme_variant, lv);
  await _persistAppearance({ glass_level: lv });
}

async function setWallpaper(key) {
  settings.wallpaper = key;
  renderWallpaper();
  _markWallpaperSel();
  await _persistAppearance({ wallpaper: key });
}

/** Build the wallpaper swatch grid once: presets + a custom-image swatch
 * (hidden until an image exists) + the upload tile. The custom image gets its
 * OWN swatch so the ＋ tile stays visible after an upload — turning the ＋
 * tile itself into the thumbnail removed the only affordance for picking a
 * different image (the "＋ disappears" bug). */
function buildWallpaperSwatches() {
  const grid = document.getElementById("lg-wall-grid");
  if (!grid || grid.dataset.built) return;
  grid.dataset.built = "1";
  LG_WALL_ORDER.forEach(k => {
    const b = document.createElement("button");
    b.className = "lg-wall-sw"; b.dataset.k = k; b.title = k;
    b.style.background = LG_WALLPAPERS[k].prev;
    const lab = document.createElement("span");
    lab.className = "lg-wall-label"; lab.textContent = k;
    b.appendChild(lab);
    b.onclick = () => setWallpaper(k);
    grid.appendChild(b);
  });
  const cust = document.createElement("button");
  cust.className = "lg-wall-sw custom"; cust.dataset.k = "custom";
  cust.title = "Custom image"; cust.style.display = "none";
  const clab = document.createElement("span");
  clab.className = "lg-wall-label"; clab.textContent = "custom";
  cust.appendChild(clab);
  cust.onclick = () => setWallpaper("custom");
  grid.appendChild(cust);
  const up = document.createElement("button");
  up.className = "lg-wall-sw up";   // no data-k: it's an action, never "selected"
  up.title = "Upload image"; up.textContent = "＋";
  up.onclick = () => onWallpaperUpload();
  grid.appendChild(up);
}

function _markWallpaperSel() {
  const grid = document.getElementById("lg-wall-grid");
  if (!grid) return;
  const cur = settings.wallpaper || "aurora";
  grid.querySelectorAll(".lg-wall-sw").forEach(b =>
    b.classList.toggle("on", b.getAttribute("data-k") === cur));
}

/** Reflect a loaded custom image onto its own swatch (thumbnail) and show it. */
function _applyCustomSwatch() {
  const grid = document.getElementById("lg-wall-grid");
  if (!grid) return;
  const cust = /** @type {HTMLElement|null} */ (grid.querySelector(".lg-wall-sw.custom"));
  if (cust && _lgCustomData) {
    cust.style.display = "";
    cust.style.background = "center/cover url(" + _lgCustomData + ")";
  }
}

function onWallpaperUpload() {
  // always opens the OS picker — selecting the existing custom image is the
  // custom swatch's job (the old re-select-first behavior made the ＋ tile
  // need two clicks to actually replace the image)
  let fi = /** @type {HTMLInputElement|null} */ (/** @type {unknown} */ (document.getElementById("lg-wall-file")));
  if (!fi) {
    fi = document.createElement("input");
    fi.type = "file"; fi.accept = "image/*"; fi.id = "lg-wall-file"; fi.style.display = "none";
    fi.addEventListener("change", _onWallpaperFile);
    document.body.appendChild(fi);
  }
  fi.value = "";
  fi.click();
}

function _onWallpaperFile(ev) {
  const inp = /** @type {HTMLInputElement} */ (ev.target);
  const f = inp.files && inp.files[0];
  if (!f || !/^image\//.test(f.type || "")) return;
  const rd = new FileReader();
  rd.onload = () => {
    const data = /** @type {string} */ (rd.result);
    // reject oversize BEFORE committing, so we never switch to a custom wallpaper
    // the backend can't persist (which would fall back to a preset on restart
    // while settings still said "custom" — a silent loss + state desync)
    if (data.length > LG_WALL_MAX) {
      failNotify("That image is too large (max ~18 MB). Pick a smaller one.");
      return;
    }
    const img = new Image();
    img.onload = async () => {
      _lgCustomImg = img; _lgCustomData = data;
      _applyCustomSwatch();
      settings.wallpaper = "custom";
      renderWallpaper();
      _markWallpaperSel();
      // persist the image blob (a file in user_data_root) then the key choice.
      // Backstop: if the backend still couldn't store it (write error / cap),
      // warn — the image shows this session but won't survive a restart.
      const r = /** @type {{ok?:boolean, stored?:boolean, error?:string}} */ (
        JSON.parse(await bridge.set_wallpaper_image(data)));
      if (r.error || r.stored === false) {
        failNotify("That wallpaper could not be saved; it will not persist after a restart.");
      }
      await _persistAppearance({ wallpaper: "custom" });
    };
    img.onerror = () => failNotify("That image could not be loaded.");
    img.src = data;
  };
  rd.readAsDataURL(f);
}

/** First-time liquidglass setup: build swatches, pull the stored custom image
 * from the backend (so it survives restarts), then paint. */
async function initWallpaper() {
  buildWallpaperSwatches();
  if (!_wallpaperInited) {
    _wallpaperInited = true;
    try {
      const data = await bridge.get_wallpaper_image();
      if (data) {
        await new Promise(resolve => {
          const img = new Image();
          img.onload = () => { _lgCustomImg = img; _lgCustomData = data; _applyCustomSwatch(); resolve(null); };
          img.onerror = () => resolve(null);
          img.src = data;
        });
      }
    } catch (e) { /* no custom image stored */ }
  }
  renderWallpaper();
  _markWallpaperSel();
}

// ---- wallpaper canvas renderer (procedural presets or a custom image) ----
function _a2(a) { const h = Math.round(a * 255).toString(16); return h.length < 2 ? "0" + h : h; }
/** @param {CanvasRenderingContext2D} ctx @param {LgWallpaper} wp */
function _wallPools(ctx, wp, W, H) {
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = wp.base; ctx.fillRect(0, 0, W, H);
  ctx.globalCompositeOperation = "lighter";
  wp.pools.forEach(p => {
    const cx = p[1] * W, cy = p[2] * H, r = p[3] * Math.max(W, H);
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
    g.addColorStop(0, p[0] + _a2(p[4]));
    g.addColorStop(1, p[0] + "00");
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, 7); ctx.fill();
  });
  ctx.globalCompositeOperation = "source-over";
}
/** @param {CanvasRenderingContext2D} ctx */
function _wallVignette(ctx, W, H, s) {
  const g = ctx.createRadialGradient(W / 2, H * .42, 0, W / 2, H * .42, Math.max(W, H) * .78);
  g.addColorStop(0, "transparent");
  g.addColorStop(1, "rgba(0,0,0," + s + ")");
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
}
/** @param {CanvasRenderingContext2D} ctx @param {HTMLImageElement} img */
function _wallImage(ctx, img, W, H) {
  const iw = img.naturalWidth || img.width, ih = img.naturalHeight || img.height;
  if (!iw || !ih) return;
  ctx.clearRect(0, 0, W, H);
  const s = Math.max(W / iw, H / ih), dw = iw * s, dh = ih * s;
  ctx.drawImage(img, (W - dw) / 2, (H - dh) / 2, dw, dh);
}
/** Paint the wallpaper canvas for the current settings.wallpaper. No-op if the
 * canvas is missing (should never happen — it's in index.html). */
function renderWallpaper() {
  const cv = /** @type {HTMLCanvasElement|null} */ (/** @type {unknown} */ (document.getElementById("lgWall")));
  if (!cv || !cv.getContext) return;
  const ctx = cv.getContext("2d");
  if (!ctx) return;
  const DPR = Math.min(window.devicePixelRatio || 1, 2);
  const W = window.innerWidth, H = window.innerHeight;
  cv.width = Math.max(1, W * DPR);
  cv.height = Math.max(1, H * DPR);
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  const key = settings.wallpaper || "aurora";
  if (key === "custom" && _lgCustomImg) {
    _wallImage(ctx, _lgCustomImg, W, H);
    _wallVignette(ctx, W, H, .2);
    return;
  }
  const wp = LG_WALLPAPERS[key] || LG_WALLPAPERS.aurora;
  _wallPools(ctx, wp, W, H);
  _wallVignette(ctx, W, H, .34);
}
// repaint on resize (viewport-sized canvas), only while liquidglass is active
window.addEventListener("resize", () => {
  if (settings.theme_variant === "liquidglass") renderWallpaper();
});

// ---- compositor self-heal heartbeat (DESIGN.md §16.5 rule 4) ----
// The §16.5 layer rules make compositor drops unlikely but can't prevent the
// ones caused by EXTERNAL GPU events (sleep/resume, driver reset, GPU memory
// pressure) — and a static chrome bar is never re-invalidated, so a dropped
// layer would stay gone until restart. While Liquid Glass is active we flip
// --lg-pulse between 0 and 0.004 every LG_PULSE_MS; style.css routes that
// imperceptible delta through all three composited pieces of both bars
// (tint alpha, saturate() in the backdrop chain, an inset outline painted
// with the labels), so any dropped piece is re-rastered — healed — within
// one period. The pulse must stay PAINT-ONLY: a will-change/transform nudge
// would make the bar its own render surface and break what its
// backdrop-filter samples.
const LG_PULSE_MS = 250;  // heal-latency upper bound; cost per tick is re-rastering two thin bars
let _lgPulseTimer = 0;
let _lgPulseOn = false;

function _lgPulseStart() {
  if (_lgPulseTimer) return;
  _lgPulseTimer = window.setInterval(() => {
    _lgPulseOn = !_lgPulseOn;
    document.documentElement.style.setProperty("--lg-pulse", _lgPulseOn ? "0.004" : "0");
  }, LG_PULSE_MS);
}

function _lgPulseStop() {
  if (_lgPulseTimer) { clearInterval(_lgPulseTimer); _lgPulseTimer = 0; }
  _lgPulseOn = false;
  document.documentElement.style.removeProperty("--lg-pulse");
}

function updateOrcaStatus(valid) {
  const pill = document.getElementById("orca-status");
  pill.classList.toggle("ok", !!valid);
  document.getElementById("orca-status-text").textContent = valid ? "ORCA ready" : "ORCA not set";
}

let _mlipPollTimer = 0;
let _mlipReady = false;   // any registered MACE env is ready — gates the MLIP build card
/** Grey out and lock the MLIP build card when no MACE environment is ready. */
function applyMlipLock() {
  const card = document.getElementById("card-mlip");
  if (!card) return;
  const locked = !_mlipReady;
  card.classList.toggle("locked", locked);
  const note = document.getElementById("mlip-lock-note");
  if (note) note.style.display = locked ? "" : "none";
  for (const id of ["mlip-name", "mlip-task", "mlip-model", "mlip-charge", "mlip-mult", "mlip-ref-select"]) {
    const el = document.getElementById(id);
    if (el) el.disabled = locked;
  }
  card.querySelectorAll("button").forEach(b => { b.disabled = locked; });
}
/** Backend list -> "MACE 0.3.6, SevenNet 0.10.0".
 *  @param {MlipBackend[]} backends */
function mlipBackendText(backends) {
  return (backends || []).map(b => b.label + " " + b.version).join(", ");
}
/** Reflect an aggregate MlipStatusPayload on the top-bar pill (state + hover
 *  detail) and the Settings env list.
 *  @param {MlipStatusPayload} st */
function renderMlip(st) {
  const state = (st && st.state) || "unset";
  const envs = (st && st.envs) || [];
  _mlipReady = (state === "ready");
  applyMlipLock();
  const pill = document.getElementById("mlip-status");
  pill.classList.toggle("ok", state === "ready");
  pill.classList.toggle("err", state === "error");
  // no colon — same "NAME state" form as the ORCA pill ("ORCA ready")
  document.getElementById("mlip-status-text").textContent =
    state === "ready" ? "MLIP ready"
    : state === "checking" ? "MLIP checking…"
    : state === "error" ? "MLIP error"
    : "MLIP not set";
  // hover tooltip: one line per env with its detected backends / status
  pill.title = envs.length
    ? envs.map(e =>
        e.name + ": " + (
          e.state === "ready" ? mlipBackendText(e.backends)
          : e.state === "checking" ? "checking…"
          : (e.message || "not ready"))).join("\n")
    : "No MLIP environment registered";
  renderMlipEnvList(envs);
}
/** Render the registered-environment rows in the Settings tab.
 *  @param {MlipEnvPayload[]} envs */
function renderMlipEnvList(envs) {
  const box = document.getElementById("mlip-env-list");
  if (!box) return;
  box.textContent = "";
  if (!envs.length) {
    const d = document.createElement("div");
    d.className = "hint";
    d.textContent = "No environment registered yet.";
    box.appendChild(d);
    return;
  }
  for (const e of envs) {
    const row = document.createElement("div");
    row.className = "mlip-env-row";

    const dot = document.createElement("span");
    dot.className = "mlip-dot" + (e.state === "ready" ? " ok" : e.state === "error" ? " err" : "");

    const info = document.createElement("div");
    info.className = "mlip-env-info";
    const nm = document.createElement("div");
    nm.className = "mlip-env-name";
    nm.textContent = e.name || "(unnamed)";
    const pa = document.createElement("div");
    pa.className = "mlip-env-path mono";
    pa.textContent = e.python;
    const de = document.createElement("div");
    de.className = "mlip-env-detail";
    if (e.state === "ready") {
      de.textContent = (mlipBackendText(e.backends) || "ready") + (e.version ? " · python " + e.version : "");
      de.style.color = "var(--ok)";
    } else if (e.state === "checking") {
      de.textContent = "checking…";
    } else {
      de.textContent = e.message || "not ready";
      de.style.color = "var(--err)";
    }
    info.append(nm, pa, de);

    const btns = document.createElement("div");
    btns.className = "mlip-env-btns";
    const chk = document.createElement("button");
    chk.className = "btn btn-sm btn-ghost";
    chk.textContent = "Check";
    chk.onclick = () => checkMlipEnv(e.id);
    const rm = document.createElement("button");
    rm.className = "btn btn-sm btn-ghost";
    rm.textContent = "Remove";
    rm.onclick = () => removeMlipEnv(e.id);
    btns.append(chk, rm);

    row.append(dot, info, btns);
    box.appendChild(row);
  }
}
/** Poll get_mlip_status() until every env probe settles. */
async function pollMlipStatus() {
  const st = /** @type {MlipStatusPayload} */ (JSON.parse(await bridge.get_mlip_status()));
  renderMlip(st);
  clearTimeout(_mlipPollTimer);
  if (st.state === "checking") _mlipPollTimer = setTimeout(pollMlipStatus, 800);
}
/** Register the interpreter currently in the "Add" field as a new MLIP env. */
async function addMlipEnv() {
  const input = document.getElementById("set-mlip");
  const python = input.value.trim();
  if (!python) { toast("A Python interpreter path (enter or browse)."); return; }
  const res = JSON.parse(await bridge.add_mlip_env(JSON.stringify({ python })));
  if (res && res.error) { failNotify("Could not add environment: " + res.error); return; }
  input.value = "";
  renderMlip(/** @type {MlipStatusPayload} */ (res));
  pollMlipStatus();
}
async function removeMlipEnv(id) {
  const res = JSON.parse(await bridge.remove_mlip_env(id));
  renderMlip(/** @type {MlipStatusPayload} */ (res));
}
/** Re-probe a single registered env. */
async function checkMlipEnv(id) {
  const res = JSON.parse(await bridge.check_mlip(id));
  renderMlip(/** @type {MlipStatusPayload} */ (res));
  pollMlipStatus();
}
async function pickMlipPython() {
  const p = await bridge.pick_mlip_python();
  if (p) document.getElementById("set-mlip").value = p;
}

// ---------- CREST (WSL) status ----------
let _crestPollTimer = 0;
let _crestReady = false;   // some WSL distro has CREST — gates the CREST build card
/** Grey out and lock the CREST build card when no distro has CREST. */
function applyCrestLock() {
  const card = document.getElementById("card-crest");
  if (!card) return;
  const locked = !_crestReady;
  card.classList.toggle("locked", locked);
  const note = document.getElementById("crest-lock-note");
  if (note) note.style.display = locked ? "" : "none";
  for (const id of ["crest-name", "crest-charge", "crest-mult", "crest-method",
                    "crest-solvent", "crest-ewin", "crest-threads"]) {
    const el = document.getElementById(id);
    if (el) el.disabled = locked;
  }
  card.querySelectorAll("button").forEach(b => { b.disabled = locked; });
}
/** Reflect a CrestStatusPayload on the top-bar pill and the Settings section.
 *  @param {CrestStatusPayload} st */
function renderCrest(st) {
  const state = (st && st.state) || "unset";
  const distros = (st && st.distros) || [];
  _crestReady = (state === "ready");
  applyCrestLock();
  const pill = document.getElementById("crest-status");
  if (pill) {
    pill.classList.toggle("ok", state === "ready");
    pill.classList.toggle("err", state === "error");
    document.getElementById("crest-status-text").textContent =
      state === "ready" ? "CREST ready"
      : state === "checking" ? "CREST checking…"
      : state === "error" ? "CREST error"
      : "CREST not set";
    pill.title = !st || !st.wsl ? "WSL not available — install WSL to run CREST"
      : distros.length
        ? distros.map(d => d.distro + ": " + (d.ready ? (d.version || "ready") : (d.error || "not installed"))).join("\n")
        : "No usable WSL distribution found";
  }
  renderCrestSettings(st);
}
/** Fill the Settings distro dropdown + status detail. @param {CrestStatusPayload} [st] */
function renderCrestSettings(st) {
  const sel = document.getElementById("set-crest-distro");
  const detail = document.getElementById("crest-detail");
  const distros = (st && st.distros) || [];
  if (sel) {
    const prev = settings && settings.crest_distro || sel.value || "";
    sel.innerHTML = `<option value="">auto-detect</option>`;
    for (const d of distros) {
      const o = document.createElement("option");
      o.value = d.distro;
      o.textContent = d.distro + (d.ready ? " ✓" : "");
      sel.appendChild(o);
    }
    // include a saved distro even if it wasn't in the probe list (e.g. offline)
    if (prev && !distros.some(d => d.distro === prev)) {
      const o = document.createElement("option"); o.value = prev; o.textContent = prev; sel.appendChild(o);
    }
    sel.value = prev;
  }
  const btn = document.getElementById("crest-install-btn");
  if (btn) {
    // Installing is redundant once CREST is present in the target distro
    // (auto-detect ⇒ any distro; a specific pick ⇒ that distro).
    const target = sel ? sel.value : "";
    const alreadyReady = target
      ? distros.some(d => d.distro === target && d.ready)
      : distros.some(d => d.ready);
    btn.disabled = alreadyReady;
    btn.title = alreadyReady ? "CREST is already installed here" : "";
  }
  if (detail) {
    if (st && !st.wsl) detail.textContent = "WSL is not available on this machine.";
    else if (!distros.length) detail.textContent = "No usable WSL distribution found. Install one, e.g. `wsl --install -d Ubuntu`.";
    else {
      const ready = distros.filter(d => d.ready).map(d => d.distro);
      detail.textContent = ready.length
        ? "CREST found in: " + ready.join(", ")
        : "CREST not installed in any distribution yet — pick one and click Install CREST.";
    }
  }
}
/** Poll get_crest_status() until the probe settles. */
async function pollCrestStatus() {
  const st = /** @type {CrestStatusPayload} */ (JSON.parse(await bridge.get_crest_status()));
  renderCrest(st);
  clearTimeout(_crestPollTimer);
  if (st.state === "checking") _crestPollTimer = setTimeout(pollCrestStatus, 900);
}
async function checkCrest() {
  const st = /** @type {CrestStatusPayload} */ (JSON.parse(await bridge.check_crest()));
  renderCrest(st);
  pollCrestStatus();
}
async function installCrest() {
  const sel = document.getElementById("set-crest-distro");
  const distro = sel ? sel.value : "";
  toast("Installing CREST into WSL — this downloads ~8 MB…");
  const st = /** @type {CrestStatusPayload} */ (JSON.parse(await bridge.install_crest(distro || "")));
  renderCrest(st);
  pollCrestStatus();
}
async function onCrestDistroChange() {
  const sel = document.getElementById("set-crest-distro");
  const st = /** @type {CrestStatusPayload} */ (JSON.parse(await bridge.set_crest_distro(sel ? sel.value : "")));
  if (settings) settings.crest_distro = sel ? sel.value : "";
  renderCrest(st);
  pollCrestStatus();
}

async function saveSettings() {
  const etaEl = document.querySelector('input[name="eta-mode"]:checked');
  const geoEl = document.querySelector('input[name="geo-mode"]:checked');
  const payload = {
    orca_path: document.getElementById("set-orca").value.trim(),
    workspace_root: document.getElementById("set-ws").value.trim(),
    default_nprocs: parseInt(document.getElementById("set-nprocs").value, 10) || 6,
    default_maxcore_mb: parseInt(document.getElementById("set-maxcore").value, 10) || 2400,
    eta_mode: etaEl ? etaEl.value : "conservative",
    geo_graph_mode: geoEl ? geoEl.value : "all5",
  };
  const res = /** @type {SaveSettingsResult} */ (JSON.parse(await bridge.save_settings(JSON.stringify(payload))));
  // bad input comes back as {error} — don't clobber the settings mirror with it
  if ("error" in res) { failNotify("Could not save settings: " + res.error); return; }
  settings = res;
  updateOrcaStatus(settings.orca_valid);
  // push the new modes to the live graph immediately
  if (SCFGraph && SCFGraph.setEtaMode) SCFGraph.setEtaMode(settings.eta_mode);
  if (SCFGraph && SCFGraph.setGeoMode) SCFGraph.setGeoMode(settings.geo_graph_mode);
  if (_logMode === "graph") renderSCFPanel();   // redraw with the new style
  const s = document.getElementById("set-saved");
  s.textContent = "Saved."; setTimeout(() => s.textContent = "", 2000);
}
async function pickOrca() { const p = await bridge.pick_orca_executable(); if (p) document.getElementById("set-orca").value = p; }
async function pickWorkspace() { const p = await bridge.pick_workspace(); if (p) document.getElementById("set-ws").value = p; }
async function autodetectOrca() {
  const res = /** @type {AutodetectResult} */ (JSON.parse(await bridge.autodetect_orca()));
  if (res.ok && res.path) {
    document.getElementById("set-orca").value = res.path;
    // the slot persisted the path backend-side — re-pull settings so the
    // orca_valid mirror (gates runQueue) and the top-bar pill don't go stale
    settings = /** @type {SettingsPayload} */ (JSON.parse(await bridge.get_settings()));
    updateOrcaStatus(settings.orca_valid);
    appendLog("Auto-detected ORCA: " + res.path, "ok");
  } else if (res.error) {
    failNotify("Auto-detect failed: " + res.error);
  } else {
    appendLog("ORCA not auto-detectable — manual path entry needed.", "warn");
  }
}

// ---------- tabs ----------
function switchTab(name) {
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".panel").forEach(p => p.classList.toggle("active", p.dataset.panel === name));
  if (name === "results") loadFreeEnergyProfile();
  // the graph is sized by measuring its on-screen box, which is 0 while the Log
  // tab is hidden — so re-render on entry to get a correct measurement
  if (name === "log" && _logMode === "graph") renderSCFPanel();
}

// ---------- geometry source ----------
function currentGeomSource() {
  const r = document.querySelector('input[name="geomsrc"]:checked');
  return r ? r.value : "direct";
}
function onGeomSourceChange() {
  const src = currentGeomSource();
  document.getElementById("geom-direct").style.display = src === "direct" ? "block" : "none";
  document.getElementById("geom-reference").style.display = src === "reference" ? "block" : "none";
  if (src === "reference") refreshRefSelect();
}
function refreshRefSelect() {
  const sel = document.getElementById("ref-select");
  const prev = sel.value;
  sel.innerHTML = "";
  if (!queue.length) {
    sel.innerHTML = `<option value="">(no calculations in queue yet)</option>`;
    return;
  }
  // a calc must not reference its own geometry (it would depend on itself), so
  // when editing, exclude the calc being edited from the candidate list
  const selfName = editName;
  const candidates = queue.filter(c => c.name !== selfName);
  if (!candidates.length) {
    sel.innerHTML = `<option value="">(no other calculation to reference)</option>`;
    return;
  }
  for (const c of candidates) {
    const o = document.createElement("option");
    o.value = c.name; o.textContent = `${c.name}  (${c.kind})`;
    sel.appendChild(o);
  }
  if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
}

// Parse a raw .xyz file's text into a normalized "El x y z" coordinate block.
// The load_* slots return a LoadResult JSON envelope; callers feed this the
// envelope's .text field (the file body itself is plain text, not JSON).
function parseXyzText(content) {
  const lines = (content || "").split(/\r?\n/);
  let start = 0;
  if (lines.length >= 2 && /^\s*\d+\s*$/.test(lines[0])) start = 2;  // skip count+comment
  const coords = [];
  for (let i = start; i < lines.length; i++) {
    const p = lines[i].trim().split(/\s+/);
    if (p.length < 4) continue;
    const [e, x, y, z] = p;
    if ([x, y, z].some(v => isNaN(parseFloat(v)))) continue;
    coords.push(`${e} ${x} ${y} ${z}`);
  }
  return coords.join("\n");
}

// In raw mode the text runs verbatim: without a {{GEOMETRY}} placeholder a
// loaded .xyz would be stored on the calc but silently IGNORED at run time
// (the embedded "* xyz" block wins) — refuse instead of desyncing. An EMPTY
// editor is exempt: nothing can desync yet, and the load-geometry-first
// paste-a-{{GEOMETRY}}-template workflow must keep working.
function rawBlocksXyzLoad() {
  if (rawMode && rawText.trim() && !rawText.includes("{{GEOMETRY}}")) {
    failNotify("Raw input runs its own coordinates — edit the '* xyz' block in the editor, or insert the {{GEOMETRY}} snippet first.");
    return true;
  }
  return false;
}

async function loadXyz() {
  if (rawBlocksXyzLoad()) return;
  const res = /** @type {LoadResult} */ (JSON.parse(await bridge.load_xyz_file()));
  if (res.cancelled) return;   // deliberate dismissal, never an error
  if (!res.ok) { failNotify("Could not read that .xyz."); return; }  directXyz = parseXyzText(res.text);
  const n = directXyz ? directXyz.split("\n").length : 0;
  const st = document.getElementById("xyz-status");
  st.textContent = n ? `loaded (${n} atoms)` : "No atoms in file.";
  appendLog(`${n} atoms loaded from .xyz.`, n ? "ok" : "warn");
  nebAtomCheck();   // a replaced reactant must never leave a stale ✓/⚠ verdict
}

// ---------- per-element basis / ECP ----------
function addBasisRow(element, basis, ecp) {
  const host = document.getElementById("basis-rows");
  const row = document.createElement("div");
  row.className = "basis-row";
  row.innerHTML = `
    <input class="be-el mono" type="text" placeholder="I" value="${escapeHtml(element ?? "")}">
    <input class="be-basis mono" type="text" placeholder="def2-TZVP" value="${escapeHtml(basis ?? "")}">
    <input class="be-ecp mono" type="text" placeholder="def2-ECP" value="${escapeHtml(ecp ?? "")}">
    <button class="rm" title="Remove" onclick="this.parentElement.remove()">×</button>`;
  host.appendChild(row);
}

function collectBasisAssignments() {
  const rows = document.querySelectorAll("#basis-rows .basis-row");
  const out = [];
  rows.forEach((r) => {
    const el = r.querySelector(".be-el").value.trim();
    const basis = r.querySelector(".be-basis").value.trim();
    const ecp = r.querySelector(".be-ecp").value.trim();
    if (!el) return;
    if (!/^[A-Za-z]{1,3}$/.test(el))
      throw new Error(`"${el}" is not a valid element symbol.`);
    if (!basis && !ecp) return;
    out.push({ element: el, basis, ecp });
  });
  return out;
}

function fillBasisRows(list) {
  const host = document.getElementById("basis-rows");
  host.innerHTML = "";
  (list || []).forEach((b) => addBasisRow(b.element, b.basis, b.ecp));
}

// ---------- config form ----------
// Snapshot the method-form fields that must survive a kind re-render: the
// kind-specific rows (tddft/irc/neb/...) deliberately reset, but everything
// the user set that still applies to the new kind is carried over — including
// the kind-independent resources (maxcore/nprocs) and the numeric fields
// shared by several kinds (MaxIter across the opt kinds, Temp/Pressure across
// the freq kinds). options and SCF are kind-AWARE: an untouched kind default
// must not shadow the NEW kind's default (switching to NEB-TS must regain its
// FREQ option; opt→freq must still bump TightSCF→VeryTightSCF), while an
// explicit user override always survives.
function collectPreserve(newKind) {
  const val = (id) => { const e = document.getElementById(id); return e ? e.value : null; };
  const oldDef = KIND_DEFS[_configKind] || null;
  const newDef = KIND_DEFS[newKind] || null;
  const p = {
    functional: comboValue("combo-functional"),
    basis_set: comboValue("combo-basis"),
    solvent: comboValue("combo-solvent"),
    ri: val("cfg-ri"),
    solvmodel: val("cfg-solvmodel"),
    maxcore: val("cfg-maxcore"),
    nprocs: val("cfg-nprocs"),
    max_iter: val("cfg-maxiter"),
    freq_temp: val("cfg-temp"),
    freq_pressure: val("cfg-pressure"),
    options: /** @type {string|null} */ (null),
    scf: /** @type {string|null} */ (null),
  };
  const opts = val("cfg-options");
  if (opts != null && (!oldDef || opts !== oldDef.options)) p.options = opts;
  const scf = val("cfg-scf");
  if (scf && oldDef && newDef
      && (scf !== oldDef.scfDefault || oldDef.scfDefault === newDef.scfDefault)) p.scf = scf;
  return p;
}

function onKindChange() {
  if (rawMode) return;  // raw calcs are locked to their text; kind change ignored
  const kind = document.getElementById("calc-kind").value;
  renderConfigForm(kind, collectPreserve(kind));
}

function renderConfigForm(kind, preserve) {
  const def = KIND_DEFS[kind];
  _configKind = kind;   // which kind the form's rows currently reflect
  const host = document.getElementById("calc-config");
  // Calc type field: form kinds use a filtered group; General lists every
  // run type from calculation_types.json; nmr/tddft are fixed (no selector).
  let calcRow = "";
  if (def.allTypes) {
    calcRow = `<div class="field"><label>Run type</label><select id="cfg-calc"></select></div>`;
  } else if (def.calcGroup) {
    calcRow = `<div class="field"><label>Calc type</label><select id="cfg-calc"></select></div>`;
  }
  const maxIterRow = def.showMaxIter
    ? `<div class="field" style="flex:0 0 130px"><label>MaxIter</label><input id="cfg-maxiter" type="number" value="200" min="1"></div>` : "";
  const nmrRows = def.showNmr ? `
    <div class="field-row">
      <label class="checkbox"><input id="cfg-jcoupling" type="checkbox"> Additional J-couplings (%eprnmr SSALL)</label>
    </div>
    <div class="hint">NMR shielding always computed; checkbox adds spin-spin (J) couplings.</div>` : "";
  const tddftRows = def.showTddft ? `
    <div class="field-row">
      <div class="field"><label>nroots</label><input id="cfg-nroots" type="number" value="40" min="1"></div>
      <div class="field"><label>maxdim</label><input id="cfg-maxdim" type="number" value="10" min="1"></div>
      <div class="field"><label class="checkbox" style="margin-top:24px"><input id="cfg-tda" type="checkbox"> TDA</label></div>
      <div class="field"><label class="checkbox" style="margin-top:24px"><input id="cfg-triplets" type="checkbox"> Triplets</label></div>
    </div>` : "";
  const freqRows = def.showFreq ? `
    <div class="field-row">
      <div class="field"><label>Temperature (K)</label><input id="cfg-temp" type="number" value="298.15" step="0.01" min="0"></div>
      <div class="field"><label>Pressure (atm)</label><input id="cfg-pressure" type="number" value="1.0" step="0.1" min="0"></div>
    </div>
    <div class="hint">Default 298.15 K / 1.0 atm omits the %freq block; any change emits it.</div>` : "";
  const ircRows = def.showIrc ? `
    <div class="field-row">
      <div class="field"><label>Direction</label>
        <select id="cfg-irc-direction"><option value="both">both</option><option value="forward">forward</option><option value="backward">backward</option></select>
      </div>
      <div class="field"><label>Initial Hessian</label>
        <select id="cfg-irc-inithess" onchange="onIrcHessChange()">
          <option value="calc_anfreq">calculate (analytic)</option>
          <option value="calc_numfreq">calculate (numerical)</option>
          <option value="read">read from .hess file</option>
        </select>
      </div>
      <div class="field" style="flex:0 0 130px"><label>MaxIter</label><input id="cfg-irc-maxiter" type="number" value="100" min="1"></div>
    </div>
    <div class="field-row" id="cfg-irc-hessfile-row" style="display:none">
      <div class="field"><label>.hess filename</label><input id="cfg-irc-hessfile" type="text" class="mono" placeholder="e.g. TS2.hess (auto-copied from the referenced calc's folder)"></div>
    </div>
    <div class="hint">IRC start point: a TS geometry — Geometry below to <b>reference</b> a TS calc. Fastest with a .hess from that TS's freq run, else recomputed here.</div>` : "";
  const nebRows = def.showNeb ? `
    <div class="field-row">
      <div class="field"><label>Product geometry (.xyz)</label>
        <button class="btn btn-sm" onclick="loadNebProduct()">Load product .xyz</button>
        <span id="cfg-neb-prod-status" class="hint" style="margin-left:8px">no product loaded</span>
      </div>
      <div class="field" style="flex:0 0 120px"><label>Images</label><input id="cfg-neb-nimages" type="number" value="8" min="3"></div>
      <div class="field"><label class="checkbox" style="margin-top:24px"><input id="cfg-neb-preopt" type="checkbox"> Endpoint pre-optimization</label></div>
    </div>
    <div id="cfg-neb-atomcheck" class="hint" style="margin-top:4px"></div>
    <div class="hint">NEB-TS: the TS between reactant (Geometry below) and product. <b>Identical atoms in identical order in both</b> — product built by copying the reactant and moving atoms, then loaded here.</div>` : "";

  host.innerHTML = `
    <div class="field-row">
      <div class="field"><label>Functional</label>
        <div class="combo" id="combo-functional">
          <input type="text" class="mono combo-input" autocomplete="off" placeholder="searchable — or your own value">
          <div class="combo-list" style="display:none"></div>
        </div>
      </div>
      <div class="field"><label>Basis set</label>
        <div class="combo" id="combo-basis">
          <input type="text" class="mono combo-input" autocomplete="off" placeholder="searchable — or your own value">
          <div class="combo-list" style="display:none"></div>
        </div>
      </div>
    </div>
    <div class="field-row">
      ${calcRow}
      <div class="field"><label>SCF conv.</label><select id="cfg-scf"></select></div>
      <div class="field"><label>RI approx.</label><select id="cfg-ri"></select></div>
    </div>
    <div class="field-row">
      <div class="field"><label>Solvation</label>
        <select id="cfg-solvmodel" onchange="onSolvChange()">
          <option value="">Gas phase</option><option value="CPCM">CPCM</option><option value="SMD">SMD</option>
        </select>
      </div>
      <div class="field" id="cfg-solvent-field"><label>Solvent</label>
        <div class="combo" id="combo-solvent">
          <input type="text" class="mono combo-input" autocomplete="off" placeholder="searchable — or your own value">
          <div class="combo-list" style="display:none"></div>
        </div>
      </div>
    </div>
    <div class="field-row">
      <div class="field"><label>Extra options</label><input id="cfg-options" type="text" class="mono" value="${def.options}"></div>
      <div class="field" style="flex:0 0 130px"><label>maxcore (MB / core)</label><input id="cfg-maxcore" type="number" value="${settings.default_maxcore_mb||2400}" min="100" step="100"></div>
      <div class="field" style="flex:0 0 110px"><label>nprocs</label><input id="cfg-nprocs" type="number" value="${settings.default_nprocs||6}" min="1"></div>
      ${maxIterRow}
    </div>
    ${freqRows}
    ${nmrRows}
    ${tddftRows}
    ${ircRows}
    ${nebRows}`;

  setupCombo("combo-functional", choicesCache.functionals, (preserve && preserve.functional) || "wB97X-D4");
  setupCombo("combo-basis", choicesCache.basis_sets, (preserve && preserve.basis_set) || "def2-TZVP");
  fillSelect(document.getElementById("cfg-scf"), flatItems(choicesCache.scf_convergences), (preserve && preserve.scf) || def.scfDefault);
  fillSelect(document.getElementById("cfg-ri"), flatItems(choicesCache.ri_approximations), (preserve && preserve.ri) || "RIJCOSX");
  if (def.allTypes) {
    fillSelect(document.getElementById("cfg-calc"), flatItems(choicesCache.calculation_types), "SP");
  } else if (def.calcGroup) {
    fillSelect(document.getElementById("cfg-calc"), flatItems(choicesCache.calculation_types, def.calcGroup), def.calcDefault);
  }
  setupCombo("combo-solvent", choicesCache.solvents, (preserve && preserve.solvent) || "Water");
  // restore the preserved kind-independent fields (see collectPreserve)
  if (preserve) {
    const sm = document.getElementById("cfg-solvmodel"); if (sm && preserve.solvmodel != null) sm.value = preserve.solvmodel;
    const op = document.getElementById("cfg-options"); if (op && preserve.options != null) op.value = preserve.options;
    const mc = document.getElementById("cfg-maxcore"); if (mc && preserve.maxcore != null) mc.value = preserve.maxcore;
    const np = document.getElementById("cfg-nprocs"); if (np && preserve.nprocs != null) np.value = preserve.nprocs;
    const mi = document.getElementById("cfg-maxiter"); if (mi && preserve.max_iter != null) mi.value = preserve.max_iter;
    const tp = document.getElementById("cfg-temp"); if (tp && preserve.freq_temp != null) tp.value = preserve.freq_temp;
    const pr = document.getElementById("cfg-pressure"); if (pr && preserve.freq_pressure != null) pr.value = preserve.freq_pressure;
  }
  // freshly rendered NEB rows: reflect a product that is still loaded (the
  // global survives a kind round-trip — the label must not claim otherwise)
  if (def.showNeb && _nebProductXyz) {
    const nst = document.getElementById("cfg-neb-prod-status");
    if (nst) nst.textContent = `loaded (${countAtoms(_nebProductXyz)} atoms)`;
    nebAtomCheck();
  }
  onSolvChange();  // hide solvent if gas phase
}

// hide the solvent dropdown when gas phase is selected
function onSolvChange() {
  const model = document.getElementById("cfg-solvmodel").value;
  const field = document.getElementById("cfg-solvent-field");
  if (field) field.style.display = model ? "block" : "none";
}

// ---- IRC / NEB-TS form helpers ----
let _nebProductXyz = "";   // product geometry loaded for a NEB-TS calc

function onIrcHessChange() {
  const mode = document.getElementById("cfg-irc-inithess").value;
  const row = document.getElementById("cfg-irc-hessfile-row");
  if (row) row.style.display = (mode === "read") ? "flex" : "none";
}

async function loadNebProduct() {
  const res = /** @type {LoadResult} */ (JSON.parse(await bridge.load_xyz_file()));
  if (res.cancelled) return;   // deliberate dismissal, never an error
  if (!res.ok) { failNotify("Could not read that .xyz."); return; }  const xyz = parseXyzText(res.text);
  if (!xyz) { appendLog("No atoms in the product .xyz.", "warn"); return; }
  _nebProductXyz = xyz;
  const st = document.getElementById("cfg-neb-prod-status");
  if (st) st.textContent = `loaded (${countAtoms(xyz)} atoms)`;
  nebAtomCheck();
}

function countAtoms(xyz) {
  return xyz.trim().split("\n").filter(l => l.trim().split(/\s+/).length >= 4).length;
}

// element sequence of an xyz block (for order comparison)
function xyzElements(xyz) {
  return xyz.trim().split("\n")
    .map(l => l.trim().split(/\s+/))
    .filter(p => p.length >= 4)
    .map(p => p[0]);
}

// compare reactant (directXyz) vs product (_nebProductXyz) and show the result
function nebAtomCheck() {
  const box = document.getElementById("cfg-neb-atomcheck");
  if (!box) return;
  // the ✓ branch sets an inline green that would otherwise survive into a
  // later ⚠ verdict (inline style beats .qerror's red) — reset it up front
  box.style.color = "";
  const react = directXyz, prod = _nebProductXyz;
  if (!react || !prod) { box.className = "hint"; box.textContent = ""; return; }
  // element symbols compare case-insensitively (ORCA itself does; legacy
  // tools write "CL") — mirrors check_neb_atom_order on the Python side
  const cap = (e) => e.charAt(0).toUpperCase() + e.slice(1).toLowerCase();
  const r = xyzElements(react).map(cap), p = xyzElements(prod).map(cap);
  if (r.length !== p.length) {
    box.className = "qerror"; box.textContent = `⚠ Atom count differs: reactant ${r.length}, product ${p.length}. NEB-TS needs the same atoms in both.`;
    return;
  }
  // composition
  const tally = arr => arr.reduce((m, e) => (m[e] = (m[e]||0)+1, m), {});
  const tr = tally(r), tp = tally(p);
  const composMismatch = Object.keys({...tr, ...tp}).some(e => tr[e] !== tp[e]);
  if (composMismatch) {
    box.className = "qerror"; box.textContent = `⚠ Element composition mismatch between reactant and product.`;
    return;
  }
  // order
  for (let i = 0; i < r.length; i++) {
    if (r[i] !== p[i]) {
      box.className = "qerror";
      box.textContent = `⚠ Atom order differs at atom #${i+1}: reactant ${r[i]}, product ${p[i]}. Order must match (build the product by copying the reactant and moving atoms).`;
      return;
    }
  }
  box.className = "hint"; box.style.color = "var(--ok)";
  box.textContent = `✓ Reactant and product match (${r.length} atoms, same order).`;
}

/** Read the method form into the config payload sent to Python.
 * @param {string} kind @returns {Partial<StepConfigPayload>} */
function collectConfig(kind) {
  const def = KIND_DEFS[kind];
  const v = (id) => { const e = document.getElementById(id); return e ? e.value : ""; };
  const num = (id, d) => { const e = document.getElementById(id); return e ? (parseInt(e.value,10) || d) : d; };
  // NOT `parseFloat(...) || d`: an explicit 0 (e.g. 0 K thermochemistry) is
  // falsy and would silently become the default — only blank/garbage falls back
  const fnum = (id, d) => { const e = document.getElementById(id); if (!e) return d; const x = parseFloat(e.value); return Number.isFinite(x) ? x : d; };
  const chk = (id) => { const e = document.getElementById(id); return e ? e.checked : false; };
  // calc type: use the selector if present (form-group or general), else the
  // kind's fixed default (e.g. nmr -> "NMR", tddft -> "")
  const calcEl = document.getElementById("cfg-calc");
  const calcType = calcEl ? calcEl.value : def.calcDefault;
  return {
    kind,
    functional: comboValue("combo-functional"),
    basis_set: comboValue("combo-basis"),
    scf_convergence: v("cfg-scf"),
    ri_approximation: v("cfg-ri"),
    calculation_type: calcType,
    options: v("cfg-options"),
    basis_assignments: collectBasisAssignments(),
    maxcore_mb: num("cfg-maxcore", 2400),
    nprocs: num("cfg-nprocs", 6),
    max_iter: def.showMaxIter ? num("cfg-maxiter", 200) : 200,
    solvation: { model: v("cfg-solvmodel"), solvent: comboValue("combo-solvent") },
    freq_temp_k: def.showFreq ? fnum("cfg-temp", 298.15) : 298.15,
    freq_pressure_atm: def.showFreq ? fnum("cfg-pressure", 1.0) : 1.0,
    nmr_jcoupling: def.showNmr ? chk("cfg-jcoupling") : false,
    tddft_nroots: def.showTddft ? num("cfg-nroots", 40) : 40,
    tddft_maxdim: def.showTddft ? num("cfg-maxdim", 10) : 10,
    tddft_tda: def.showTddft ? chk("cfg-tda") : false,
    tddft_triplets: def.showTddft ? chk("cfg-triplets") : false,
    irc_direction: def.showIrc ? v("cfg-irc-direction") : "both",
    irc_init_hess: def.showIrc ? v("cfg-irc-inithess") : "calc_anfreq",
    irc_hess_file: def.showIrc ? v("cfg-irc-hessfile") : "",
    irc_maxiter: def.showIrc ? num("cfg-irc-maxiter", 100) : 100,
    neb_product_xyz: def.showNeb ? (_nebProductXyz || "") : "",
    neb_nimages: def.showNeb ? num("cfg-neb-nimages", 8) : 8,
    neb_preopt_ends: def.showNeb ? chk("cfg-neb-preopt") : false,
  };
}

// ---------- add / update queue ----------
// forPreview = true is used when entering raw mode to generate the .inp
// template: geometry isn't needed yet (raw carries its own coords, or a
// reference is filled in at run time), so the "load .xyz" / "select a
// reference" checks are relaxed — exactly like .xyz already behaves.
/** Read the whole Build form into the calc payload for add_calc/update_calc.
 * @param {boolean} [forPreview] @returns {CalcInput} */
function collectCalcFromForm(forPreview = false) {
  const name = document.getElementById("calc-name").value.trim();
  if (!name) throw new Error("Name is required.");
  if (/[\\/:*?"<>|]/.test(name))
    throw new Error(`Name contains characters not allowed in folder names: \\ / : * ? " < > |`);
  // P1: name collision (allow self when editing)
  const clash = queue.findIndex((c) => c.name === name && c.name !== editName);
  if (clash !== -1)
    throw new Error(`A calculation named "${name}" is already in the queue. Names must be unique (used as folder names).`);

  const kind = document.getElementById("calc-kind").value;
  const src = currentGeomSource();
  let xyz = "", ref_name = "";
  if (src === "direct") {
    // in raw+direct the coords live in the raw text; xyz may be empty
    if (!forPreview && !rawMode && !directXyz) throw new Error("Load an .xyz file first.");
    xyz = directXyz;
  } else {
    ref_name = document.getElementById("ref-select").value;
    // a reference is required to actually queue the calc, but NOT to open the
    // raw editor (you pick the reference before adding; the field stays enabled)
    if (!forPreview && !ref_name) throw new Error("Select a calculation to reference.");
    if (ref_name && ref_name === name) throw new Error("A calculation can't reference its own geometry.");
  }

  // raw integrity: must have actual .inp text, and reference mode needs the placeholder
  if (rawMode && !forPreview && !rawText.trim())
    throw new Error("Paste or load a complete .inp first.");
  if (rawMode && src === "reference" && !rawText.includes("{{GEOMETRY}}"))
    throw new Error("Raw input references another calculation but is missing the {{GEOMETRY}} placeholder.");

  // NEB-TS needs a product geometry (unless the user is hand-writing raw input).
  // Relaxed for previews like the other geometry checks: the Beginner->Expert
  // conversion must be able to generate the template before the product is loaded.
  if (!forPreview && !rawMode && kind === "neb_ts" && !_nebProductXyz)
    throw new Error("NEB-TS needs a product geometry. Load a product .xyz first.");
  // A raw NEB-TS that keeps the generated "product.xyz" reference still needs
  // the stored product — the engine writes that side file from it at run time
  // (the engine re-checks this too; this is the early, Add-time feedback).
  if (!forPreview && rawMode && kind === "neb_ts" && !_nebProductXyz && rawText.includes('"product.xyz"'))
    throw new Error('The raw input references "product.xyz" but no product geometry is loaded. ' +
                    "Load a product .xyz first (Beginner form), or point NEB_End_XYZFile at your own file.");
  // IRC "read from .hess file" without a filename would silently run a
  // different method — the generator refuses it; this is the early check.
  if (!forPreview && !rawMode && kind === "irc") {
    const ih = document.getElementById("cfg-irc-inithess");
    const hf = document.getElementById("cfg-irc-hessfile");
    if (ih && ih.value === "read" && (!hf || !hf.value.trim()))
      throw new Error("IRC 'read from .hess file' needs a .hess filename.");
  }

  // a raw calc runs its own "* xyz C M" line verbatim — mirror those values on
  // the stored calc so the queue row shows the charge state that actually runs
  let charge = parseInt(document.getElementById("calc-charge").value, 10) || 0;
  let multiplicity = parseInt(document.getElementById("calc-mult").value, 10) || 1;
  if (rawMode) {
    // both coordinate forms: "* xyz C M" (embedded) and "* xyzfile C M file.xyz"
    const cm = rawText.match(/^\s*\*\s*xyz(?:file)?\s+([+-]?\d+)\s+(\d+)/im);
    if (cm) { charge = parseInt(cm[1], 10); multiplicity = parseInt(cm[2], 10); }
  }

  return {
    name, kind,
    charge,
    multiplicity,
    geometry_source: /** @type {"direct"|"reference"} */ (src),
    xyz, ref_name,
    is_raw: rawMode,
    raw_text: rawMode ? rawText : "",
    config: collectConfig(kind),
    state: "pending", message: "",
  };
}

async function addCalcToQueue() {
  try {
    const calc = collectCalcFromForm();
    // Resolve the edit target by NAME at Update time — the queue may have
    // shifted since the edit opened. A vanished target (removed via phone /
    // fan-out) falls through to a plain add, mirroring updateEditUI.
    const oldName = editName !== null && queue.some((c) => c.name === editName)
      ? editName : null;
    const wasEditing = oldName !== null;

    if (wasEditing && oldName) {
      // preserve provenance the form doesn't carry: without this, saving any
      // edit of a conformer-fan-out clone silently erased its
      // "from crest · conformer k" origin (calc_from_dict defaults it to "")
      const orig = queue.find((c) => c.name === oldName);
      if (orig && orig.conformer_origin) calc.conformer_origin = orig.conformer_origin;
      // edit in place: preserves the calc's position in the queue
      const res = /** @type {MutationResult} */ (JSON.parse(await bridge.update_calc(oldName, JSON.stringify(calc))));
      if (!res.ok) { appendLog("Could not update: " + res.error, "err"); toast(res.error); await refreshQueue(); return; }
      if (oldName !== calc.name) delete localCalcs[oldName];
      localCalcs[calc.name] = calc;
      appendLog(`"${calc.name}" updated.`, "ok");
      exitEditMode();
      await refreshQueue();
      switchTab("queue");
      return;
    }

    const res = /** @type {MutationResult} */ (JSON.parse(await bridge.add_calc(JSON.stringify(calc))));
    if (!res.ok) {
      appendLog("Could not add: " + res.error, "err");
      toast(res.error);
      await refreshQueue();
      return;
    }
    localCalcs[calc.name] = calc;
    appendLog(`"${calc.name}" (${calc.kind}${calc.is_raw ? ", raw" : ""}) added to queue.`, "ok");
    exitEditMode();
    await refreshQueue();
    switchTab("queue");
  } catch (e) {
    appendLog(e.message, "err"); toast(e.message);
  }
}

// ---------- MLIP build mode (MACE pre-optimization) ----------
let mlipXyz = "";               // last loaded .xyz coordinate block for the MLIP form
let _mlipModelFilled = false;   // populate the model dropdown once
function renderMlipForm() {
  const sel = document.getElementById("mlip-model");
  if (sel && !_mlipModelFilled) {
    fillGroupedSelect(sel, choicesCache.mace_models, "MACE-OFF medium");
    _mlipModelFilled = true;
  }
}
async function loadMlipXyz() {
  const res = /** @type {LoadResult} */ (JSON.parse(await bridge.load_xyz_file()));
  if (res.cancelled) return;   // deliberate dismissal, never an error
  if (!res.ok) { failNotify("Could not read that .xyz."); return; }  mlipXyz = parseXyzText(res.text);
  const n = mlipXyz ? mlipXyz.split("\n").length : 0;
  document.getElementById("mlip-xyz-status").textContent =
    n ? `loaded (${n} atoms)` : "No atoms in file.";
}
function resetMlipForm() {
  document.getElementById("mlip-name").value = "";
  document.getElementById("mlip-task").value = "opt";
  document.getElementById("mlip-charge").value = "0";
  document.getElementById("mlip-mult").value = "1";
  mlipXyz = "";
  document.getElementById("mlip-xyz-status").textContent = "";
  const dr = document.querySelector('input[name="mlip-geomsrc"][value="direct"]');
  if (dr) dr.checked = true;
  onMlipGeomSourceChange();
}
/** @returns {string} the selected MLIP geometry source ("direct" | "reference"). */
function currentMlipGeomSource() {
  const r = document.querySelector('input[name="mlip-geomsrc"]:checked');
  return r ? r.value : "direct";
}
/** Toggle the MLIP .xyz-loader vs reference-picker branch (mirror of onGeomSourceChange). */
function onMlipGeomSourceChange() {
  const src = currentMlipGeomSource();
  document.getElementById("mlip-geom-direct").style.display = src === "direct" ? "block" : "none";
  document.getElementById("mlip-geom-reference").style.display = src === "reference" ? "block" : "none";
  if (src === "reference") refreshMlipRefSelect();
}
/** Fill the MLIP reference dropdown from the current queue (mirror of refreshRefSelect).
 *  Lists every queued calc; the engine validates at run time that the ref produced a geometry. */
function refreshMlipRefSelect() {
  const sel = document.getElementById("mlip-ref-select");
  if (!sel) return;
  const prev = sel.value;
  sel.innerHTML = "";
  if (!queue.length) {
    sel.innerHTML = `<option value="">(no calculations in queue yet)</option>`;
    return;
  }
  for (const c of queue) {
    const o = document.createElement("option");
    o.value = c.name; o.textContent = `${c.name}  (${c.kind})`;
    sel.appendChild(o);
  }
  if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
}
async function addMlipCalcToQueue() {
  try {
    if (!_mlipReady) throw new Error("Ready MACE environment required (see Settings).");
    const name = document.getElementById("mlip-name").value.trim();
    if (!name) throw new Error("Name is required.");
    if (/[\\/:*?"<>|]/.test(name))
      throw new Error(`Name contains characters not allowed in folder names: \\ / : * ? " < > |`);
    if (queue.some(c => c.name === name))
      throw new Error(`A calculation named "${name}" is already in the queue. Names must be unique (used as folder names).`);
    const model = document.getElementById("mlip-model").value;
    const src = currentMlipGeomSource();
    let xyz = "", ref_name = "";
    if (src === "reference") {
      ref_name = document.getElementById("mlip-ref-select").value;
      if (!ref_name) throw new Error("Select a calculation to reference.");
      if (ref_name === name) throw new Error("A calculation can't reference its own geometry.");
    } else {
      if (!mlipXyz) throw new Error("Load an .xyz file first.");
      xyz = mlipXyz;
    }
    const charge = parseInt(document.getElementById("mlip-charge").value, 10) || 0;
    const mult = Math.max(1, parseInt(document.getElementById("mlip-mult").value, 10) || 1);
    const kind = document.getElementById("mlip-task").value === "sp" ? "mlip_sp" : "mlip_opt";
    const calc = /** @type {CalcInput} */ ({
      name, kind,
      charge, multiplicity: mult,
      geometry_source: src,
      xyz, ref_name,
      is_raw: false, raw_text: "",
      config: { kind, mlip_model: model, mlip_env_id: "" },
      state: "pending", message: "",
    });
    const res = /** @type {MutationResult} */ (JSON.parse(await bridge.add_calc(JSON.stringify(calc))));
    if (!res.ok) {
      appendLog("Could not add: " + res.error, "err");
      toast(res.error);
      await refreshQueue();
      return;
    }
    localCalcs[calc.name] = calc;
    appendLog(`"${calc.name}" (MLIP ${kind === "mlip_sp" ? "single point" : "opt"} · ${model}) added to queue.`, "ok");
    resetMlipForm();
    await refreshQueue();
    switchTab("queue");
  } catch (e) {
    appendLog(e.message, "err"); toast(e.message);
  }
}

// ---------- CREST conformer-search build form ----------
let crestXyz = "";              // last loaded .xyz coordinate block for the CREST form
async function loadCrestXyz() {
  const res = /** @type {LoadResult} */ (JSON.parse(await bridge.load_xyz_file()));
  if (res.cancelled) return;
  if (!res.ok) { failNotify("Could not read that .xyz."); return; }  crestXyz = parseXyzText(res.text);
  const n = crestXyz ? crestXyz.split("\n").length : 0;
  document.getElementById("crest-xyz-status").textContent =
    n ? `loaded (${n} atoms)` : "No atoms in file.";
}
function resetCrestForm() {
  document.getElementById("crest-name").value = "";
  crestXyz = "";
  document.getElementById("crest-xyz-status").textContent = "";
  // basics back to their index.html defaults too — Reset restores defaults on
  // every build card (MLIP, DFT), so CREST must not keep e.g. a typed mult
  document.getElementById("crest-charge").value = "0";
  document.getElementById("crest-mult").value = "1";
  document.getElementById("crest-method").value = "gfn2";
  document.getElementById("crest-solvent").value = "";
  document.getElementById("crest-ewin").value = "6";
  document.getElementById("crest-threads").value = "4";
  const ho = document.getElementById("crest-handoff");
  if (ho) ho.value = "lowest";
  // advanced knobs back to CREST defaults
  for (const id of ["crest-preset", "crest-solvent-model"]) {
    const el = document.getElementById(id);
    if (el) el.value = id === "crest-solvent-model" ? "alpb" : "";
  }
  for (const id of ["crest-mdlen", "crest-tstep", "crest-tnmd", "crest-mddump", "crest-vbdump"]) {
    const el = document.getElementById(id);
    if (el) el.value = "";
  }
  for (const id of ["crest-nci", "crest-norotmd", "crest-cbonds", "crest-subrmsd",
                    "crest-cluster", "crest-keepdir"]) {
    const el = document.getElementById(id);
    if (el) el.checked = false;
  }
}
async function addCrestCalcToQueue() {
  try {
    if (!_crestReady) throw new Error("CREST in a WSL distribution required (see Settings → CREST).");
    const name = document.getElementById("crest-name").value.trim();
    if (!name) throw new Error("Name is required.");
    if (/[\\/:*?"<>|]/.test(name))
      throw new Error(`Name contains characters not allowed in folder names: \\ / : * ? " < > |`);
    if (queue.some(c => c.name === name))
      throw new Error(`A calculation named "${name}" is already in the queue. Names must be unique (used as folder names).`);
    if (!crestXyz) throw new Error("Load an .xyz file first.");
    const charge = parseInt(document.getElementById("crest-charge").value, 10) || 0;
    const mult = Math.max(1, parseInt(document.getElementById("crest-mult").value, 10) || 1);
    const method = document.getElementById("crest-method").value;
    const solvent = document.getElementById("crest-solvent").value;
    const ewin = parseFloat(document.getElementById("crest-ewin").value) || 6.0;
    const threads = Math.max(1, parseInt(document.getElementById("crest-threads").value, 10) || 4);
    const handoff = document.getElementById("crest-handoff").value === "all" ? "all" : "lowest";
    const numVal = id => parseFloat(document.getElementById(id).value) || 0;
    const checked = id => document.getElementById(id).checked;
    const preset = document.getElementById("crest-preset").value;
    const calc = /** @type {CalcInput} */ ({
      name, kind: "crest_conf",
      charge, multiplicity: mult,
      geometry_source: "direct",
      xyz: crestXyz, ref_name: "",
      is_raw: false, raw_text: "",
      config: {
        kind: "crest_conf", crest_method: method, crest_solvent: solvent,
        crest_ewin: ewin, crest_threads: threads, crest_env_id: "",
        crest_handoff: handoff,
        crest_preset: ["quick", "squick", "mquick"].includes(preset) ? preset : "",
        crest_nci: checked("crest-nci"),
        crest_solvent_model: document.getElementById("crest-solvent-model").value === "gbsa" ? "gbsa" : "alpb",
        crest_mdlen_mult: numVal("crest-mdlen"),
        crest_tstep_fs: numVal("crest-tstep"),
        crest_tnmd_k: numVal("crest-tnmd"),
        crest_mddump_fs: Math.round(numVal("crest-mddump")),
        crest_vbdump_ps: numVal("crest-vbdump"),
        crest_norotmd: checked("crest-norotmd"),
        crest_cbonds: checked("crest-cbonds"),
        crest_subrmsd: checked("crest-subrmsd"),
        crest_cluster: checked("crest-cluster"),
        crest_keepdir: checked("crest-keepdir"),
      },
      state: "pending", message: "",
    });
    const res = /** @type {MutationResult} */ (JSON.parse(await bridge.add_calc(JSON.stringify(calc))));
    if (!res.ok) {
      appendLog("Could not add: " + res.error, "err");
      toast(res.error);
      await refreshQueue();
      return;
    }
    localCalcs[calc.name] = calc;
    appendLog(`"${calc.name}" (CREST ${method}) added to queue.`, "ok");
    resetCrestForm();
    await refreshQueue();
    switchTab("queue");
  } catch (e) {
    appendLog(e.message, "err"); toast(e.message);
  }
}

// ---------- editing existing calcs ----------
async function editCalc(i) {
  const mirror = queue[i];
  if (!mirror) return;
  if (!isEditableState(mirror.state)) { toast("Editing limited to pending, cancelled, or blocked calculations."); return; }
  // MLIP calcs use the separate MLIP form, not the ORCA editor. In-place editing
  // isn't wired yet — remove and re-add from the MLIP build mode instead.
  if ((mirror.kind || "").startsWith("mlip")) { toast("No in-place editing of MLIP calculations yet — removal, then re-add from the MLIP build mode."); return; }
  if ((mirror.kind || "").startsWith("crest")) { toast("No in-place editing of CREST calculations yet — removal, then re-add from the CREST build mode."); return; }
  // raw calcs edit in the Expert editor, form calcs in the Beginner form —
  // align the mode (this also leaves MLIP/CREST mode if we're in it, and drops
  // any previous in-progress edit; editName is set below, after the switch)
  setBuildMode(mirror.is_raw ? "expert" : "beginner", false);
  // prefer the full local copy (has config/xyz/raw_text added on this PC)
  let c = localCalcs[mirror.name];
  if (!c) {
    // not added in this session (restored from a previous run, or added via the
    // phone): fetch the full calc so config/xyz/raw_text are editable here
    try {
      const res = /** @type {GetCalcResult} */ (JSON.parse(await bridge.get_calc(mirror.name)));
      if (res && res.ok && res.calc) { c = res.calc; localCalcs[mirror.name] = c; }
    } catch (e) { /* fall through to the warning */ }
    // the queue may have shifted during the await — make sure i still points at us
    if (!queue[i] || queue[i].name !== mirror.name) return;
  }
  if (!c) {
    c = mirror;
    appendLog(`"${mirror.name}": full options couldn't be loaded; the edit may be incomplete.`, "warn");
  }
  editName = mirror.name;

  document.getElementById("calc-name").value = c.name;
  document.getElementById("calc-charge").value = String(c.charge);
  document.getElementById("calc-mult").value = String(c.multiplicity);
  document.getElementById("calc-kind").value = c.kind;

  // geometry source. directXyz/label sync is unconditional: a reference calc
  // must clear any coordinates left over from a previous build/edit, or
  // flipping this edit to "direct" would silently adopt another calc's
  // geometry behind a plausible-looking "loaded (N atoms)" label.
  document.querySelector(`input[name="geomsrc"][value="${c.geometry_source}"]`).checked = true;
  onGeomSourceChange();
  directXyz = c.xyz || "";
  document.getElementById("xyz-status").textContent =
    directXyz ? `loaded (${directXyz.split("\n").filter(Boolean).length} atoms)` : "";
  if (c.geometry_source !== "direct") {
    refreshRefSelect();
    document.getElementById("ref-select").value = c.ref_name;
  }

  if (c.is_raw) {
    // raw calcs: only the raw editor is shown (Expert layout, set above)
    rawMode = true; rawText = c.raw_text || "";
    renderConfigForm(c.kind);   // populate (hidden behind the editor)
    fillConfigForm(c.config);
    showRawCard(true);
    document.getElementById("raw-text").value = rawText;
  } else {
    rawMode = false; rawText = "";
    renderConfigForm(c.kind);
    fillConfigForm(c.config);
    showRawCard(false);
  }

  updateEditUI();
  switchTab("build");
}

/** Push a stored config back into the method form (inverse of collectConfig).
 * @param {Partial<StepConfigPayload>} cfg */
function fillConfigForm(cfg) {
  if (!cfg) return;
  const set = (id, val) => { const e = document.getElementById(id); if (e != null && val != null) e.value = val; };
  const setCombo = (cid, val) => { if (_combos[cid] && val != null) _combos[cid].set(val); };
  setCombo("combo-functional", cfg.functional);
  setCombo("combo-basis", cfg.basis_set);
  set("cfg-scf", cfg.scf_convergence);
  set("cfg-ri", cfg.ri_approximation);
  set("cfg-calc", cfg.calculation_type);
  set("cfg-options", cfg.options);
  set("cfg-maxcore", cfg.maxcore_mb);
  set("cfg-nprocs", cfg.nprocs);
  set("cfg-maxiter", cfg.max_iter);
  set("cfg-temp", cfg.freq_temp_k);
  set("cfg-pressure", cfg.freq_pressure_atm);
  set("cfg-nroots", cfg.tddft_nroots);
  set("cfg-maxdim", cfg.tddft_maxdim);
  const tda = document.getElementById("cfg-tda"); if (tda && cfg.tddft_tda != null) tda.checked = cfg.tddft_tda;
  const tri = document.getElementById("cfg-triplets"); if (tri && cfg.tddft_triplets != null) tri.checked = cfg.tddft_triplets;
  const jc = document.getElementById("cfg-jcoupling"); if (jc && cfg.nmr_jcoupling != null) jc.checked = cfg.nmr_jcoupling;
  // IRC fields
  set("cfg-irc-direction", cfg.irc_direction);
  set("cfg-irc-inithess", cfg.irc_init_hess);
  set("cfg-irc-maxiter", cfg.irc_maxiter);
  set("cfg-irc-hessfile", cfg.irc_hess_file);
  if (document.getElementById("cfg-irc-inithess")) onIrcHessChange();
  // NEB-TS fields. The product sync is UNCONDITIONAL: a leftover product from
  // a previously built/edited calc must never leak into this one (a calc with
  // no product would otherwise silently inherit it on Update).
  set("cfg-neb-nimages", cfg.neb_nimages);
  const preopt = document.getElementById("cfg-neb-preopt");
  if (preopt && cfg.neb_preopt_ends != null) preopt.checked = cfg.neb_preopt_ends;
  _nebProductXyz = cfg.neb_product_xyz || "";
  const nst = document.getElementById("cfg-neb-prod-status");
  if (nst) nst.textContent = _nebProductXyz
    ? `loaded (${countAtoms(_nebProductXyz)} atoms)` : "no product loaded";
  nebAtomCheck();
  if (cfg.solvation) {
    set("cfg-solvmodel", cfg.solvation.model);
    setCombo("combo-solvent", cfg.solvation.solvent);
    onSolvChange();
  }
  fillBasisRows(cfg.basis_assignments);
}

function updateEditUI() {
  const banner = document.getElementById("edit-banner");
  const addBtn = document.getElementById("add-btn");
  const editing = editName === null
    ? null : queue.find((c) => c.name === editName) || null;
  if (!editing) {
    // not editing, or the edited entry vanished (e.g. removed via phone/poll)
    editName = null;
    banner.style.display = "none";
    addBtn.textContent = "Add to queue →";
  } else {
    banner.style.display = "block";
    banner.textContent = `Editing: ${editing.name}${rawMode ? " (raw)" : ""}`;
    addBtn.textContent = "Update";
  }
}

function exitEditMode() {
  editName = null;
  if (buildMode === "expert") {
    // back to the plain Expert view: editor cleared, guided form re-hidden
    rawMode = true; rawText = "";
    const ta = document.getElementById("raw-text"); if (ta) ta.value = "";
    _showIds(_EXPERT_HIDDEN, false);
    showRawCard(true);
  } else {
    rawMode = false; rawText = "";
    showRawCard(false);
  }
  updateEditUI();
}

// ---------- raw snippets ----------
const SNIPPETS = {
  geometry: "{{GEOMETRY}}\n",
  scf:    "%scf\n  MaxIter 300\n  ConvForced true\nend\n",
  basis:  "%basis\n  newgto I \"def2-TZVP\" end\n  newecp I \"def2-ECP\" end\nend\n",
  geom:   "%geom\n  Constraints\n    { B 0 1 C }\n  end\n  # Scan B 0 1 = 1.5, 2.5, 10 end\nend\n",
  plots:  "%plots\n  dim1 60\n  dim2 60\n  dim3 60\n  Format Gaussian_Cube\n  MO(\"homo.cube\", 0, 0);\nend\n",
  eprnmr: "%eprnmr\n  Nuclei = all C { shift }\n  Nuclei = all H { shift }\nend\n",
  tddft:  "%tddft\n  nroots 20\n  maxdim 5\n  tda false\nend\n",
  cpcm:   "%cpcm\n  smd true\n  SMDsolvent \"water\"\nend\n",
  irc:    "%irc\n  MaxIter 100\n  InitHess calc_anfreq\nend\n",
};

function insertSnippet(key) {
  const block = SNIPPETS[key];
  if (!block) return;
  const ta = document.getElementById("raw-text");
  const pos = ta.selectionStart != null ? ta.selectionStart : ta.value.length;
  let before = ta.value.slice(0, pos);
  let after = ta.value.slice(pos);
  if (before.length && !before.endsWith("\n")) before += "\n";
  ta.value = before + block + after;
  rawText = ta.value;
  const np = (before + block).length;
  ta.focus();
  ta.setSelectionRange(np, np);
}

// ---------- raw mode ----------
// (the form -> .inp conversion that lived here as enterRawMode() is now the
// Beginner -> Expert sub-toggle switch — see setDftSub)

// shared raw-editor entry: put `text` in the editor and make sure the Expert
// layout is showing (raw editing IS the Expert sub-mode — the old
// "Beginner with a dimmed, locked form" hybrid state is gone). Used by the
// Beginner->Expert conversion (setDftSub), the file loader (loadInpFile), and
// the .inp drop handler.
function enterRawWithText(text) {
  rawText = text || "";
  if (buildMode !== "expert") setBuildMode("expert", true, editName !== null);
  rawMode = true;
  const ta = document.getElementById("raw-text");
  ta.value = rawText;
  ta.oninput = (e) => { rawText = /** @type {ORCAFormElement} */ (e.target).value; };
  showRawCard(true);
  updateEditUI();
}

// Load a complete ORCA .inp from disk straight into the raw editor (no form
// generation). Lands in the Expert sub-mode from anywhere.
async function loadInpFile() {
  // converting an in-progress FORM edit to raw is irreversible — confirm first
  if (editName !== null && !rawMode) {
    const ok = await confirmModal({
      title: "Load an .inp here?",
      body: "This calculation becomes raw input, no longer form-editable.",
      confirm: "Load .inp", danger: true });
    if (!ok) return;
  }
  const res = /** @type {LoadResult} */ (JSON.parse(await bridge.load_inp_file()));
  if (res.cancelled) return;   // deliberate dismissal, never an error
  if (!res.ok) { failNotify("Could not read that .inp."); return; }
  enterRawWithText(res.text);
  // auto-fill the calculation name from the .inp filename (only when the user
  // hasn't already typed a name, so a deliberate name isn't clobbered)
  const nameEl = document.getElementById("calc-name");
  if (nameEl && res.name && !nameEl.value.trim()) nameEl.value = res.name;
  appendLog(".inp loaded into the editor. Next: calc type (plus Geometry source for {{GEOMETRY}}), then Add to queue.", "info");
}

// Build backends (DFT / MLIP / CREST) + the DFT Beginner/Expert sub-modes.
// keepEdit=true preserves an in-progress edit across the switch — used only by
// the Beginner->Expert conversion of an edited form calc (setDftSub) and by
// enterRawWithText (loading an .inp into an edit), never by a backend switch.
function setBuildMode(mode, persist = true, keepEdit = false) {
  if (mode !== "beginner" && mode !== "expert" && mode !== "mlip" && mode !== "crest") return;
  // Re-click of the active mode is a FULL no-op — never destructive. (It used
  // to fall through while editing, silently dropping the edit and, in Expert,
  // blanking the raw editor. editCalc doesn't need the fall-through: it fills
  // the form/editor itself after switching.)
  if (mode === buildMode) return;
  if (editName !== null && !keepEdit) exitEditMode();   // a real mode switch drops an in-progress edit (unless converting it)
  buildMode = mode;
  const dft = (mode === "beginner" || mode === "expert");
  if (dft) _dftSub = mode;
  document.getElementById("bmode-dft").classList.toggle("active", dft);
  document.getElementById("bmode-mlip").classList.toggle("active", mode === "mlip");
  document.getElementById("bmode-crest").classList.toggle("active", mode === "crest");
  // the Beginner/Expert sub-toggle exists only inside DFT
  const sub = document.getElementById("bsub-dft");
  if (sub) sub.style.display = dft ? "" : "none";
  document.getElementById("bsub-beginner").classList.toggle("active", mode === "beginner");
  document.getElementById("bsub-expert").classList.toggle("active", mode === "expert");
  const hint = document.getElementById("bmode-hint");
  const mlip = (mode === "mlip");
  const crest = (mode === "crest");
  // MLIP / CREST modes swap the entire ORCA build UI for a self-contained card.
  _showIds(_ORCA_BUILD, dft);
  _showIds(["card-mlip"], mlip);
  _showIds(["card-crest"], crest);
  if (hint) hint.textContent = "";
  if (mlip) {
    // rawText survives the excursion — coming back to DFT-Expert restores it
    rawMode = false;
    renderMlipForm();
    applyMlipLock();
  } else if (crest) {
    rawMode = false;
    applyCrestLock();
  } else if (mode === "expert") {
    // raw editor: hide the method form + charge/mult, show the .inp editor with
    // the current raw text (a Beginner->Expert conversion fills it, see setDftSub)
    _showIds(_EXPERT_HIDDEN, false);
    rawMode = true;
    showRawCard(true);
    const ta = document.getElementById("raw-text");
    if (ta) ta.value = rawText;
  } else {
    // guided form. rawText is already empty on every path into Beginner (the
    // discard confirm, editing a form calc, reset) — cleared defensively anyway.
    _showIds(_EXPERT_HIDDEN, true);
    rawMode = false; rawText = "";
    showRawCard(false);
    // Keep the user's method setup: the form DOM persists while hidden (Expert/
    // MLIP/CREST excursions only hide the card), so re-rendering here would
    // reset it to defaults. Render on the very first entry, or when the Type
    // select changed while the form was hidden (it stays visible in Expert but
    // onKindChange no-ops in raw mode) — then with the same field preservation
    // onKindChange uses, so the kind-specific rows can never go stale against
    // the selected kind (a stale cfg-calc would emit the wrong calc type).
    const host = document.getElementById("calc-config");
    const kindSel = document.getElementById("calc-kind").value;
    if (host && !host.childElementCount) {
      renderConfigForm(kindSel);
    } else if (_configKind !== kindSel) {
      renderConfigForm(kindSel, collectPreserve(kindSel));
    }
  }
  if (persist && bridge && bridge.save_settings) bridge.save_settings(JSON.stringify({ build_mode: mode }));
}

// DFT sub-mode switch. The linkage is ONE-WAY by design: Beginner -> Expert
// converts the current form to a generated .inp (build_inp_preview — what the
// removed "Edit raw .inp" button used to do); raw text can never be converted
// back into the form, so Expert -> Beginner only confirms discarding it (the
// name/type/charge/mult/geometry cards persist — they live outside the editor).
async function setDftSub(sub) {
  if (sub !== "beginner" && sub !== "expert") return;
  if (sub === buildMode && editName === null) return;
  const editing = editName !== null;

  if (sub === "expert") {
    if (editing && !rawMode) {
      // converting an in-progress FORM edit is irreversible for this calc
      const ok = await confirmModal({
        title: "Switch to raw input?",
        body: "Direct edit of the ORCA .inp. After saving: no more form editing — " +
              "raw text only, irreversibly.",
        confirm: "Switch to raw", danger: true,
      });
      if (!ok) return;
    }
    // generate the .inp from the form when there is something to convert (a
    // name is the minimum build_inp_preview needs); rawText is empty here in
    // every non-edit flow, so a pasted .inp is never clobbered. A FAILED
    // conversion never switches — the filled form stays visible (parity with
    // the removed "Edit raw .inp" button, which rolled back and stayed).
    let gen = "";
    const converting = buildMode === "beginner" && !rawText.trim();
    if (converting && document.getElementById("calc-name").value.trim()) {
      try {
        const calc = collectCalcFromForm(true);   // preview: geometry not required yet
        const res = /** @type {TextResult} */ (JSON.parse(await bridge.build_inp_preview(JSON.stringify(calc))));
        if (!res.ok) { failNotify("Could not generate .inp: " + res.error); return; }
        gen = res.text;
      } catch (e) {
        toast(e.message);   // e.g. a duplicate name — fix it, then convert
        return;
      }
    }
    setBuildMode("expert", true, editing);
    if (gen) {
      enterRawWithText(gen);
      appendLog("Form converted to .inp — editable below (coordinates after the '* xyz' line), then Add/Update.", "info");
    } else if (converting) {
      // nothing to convert (no name yet): a plain empty editor, said out loud
      appendLog("Expert: nothing converted (no name in the form) — empty editor; paste or Load .inp file. The Beginner form is kept.", "info");
    }
    if (editing) updateEditUI();
  } else {
    if (editing && rawMode) {
      const q = editName === null
        ? null : queue.find((c) => c.name === editName) || null;
      if (q && !q.is_raw) {
        // an UNSAVED Beginner->Expert conversion: the queued calc is still
        // form-based, so backing out just re-opens the form edit (the modal
        // scoped irreversibility to "after saving" — honor that)
        const ok = await confirmModal({
          title: "Back out of the raw conversion?",
          body: "The editor text is <b>discarded</b> and the form edit of this " +
                "calculation reopens (nothing was saved yet).",
          confirm: "Discard & reopen form", danger: true,
        });
        if (!ok) return;
        // the queue may have shifted during the modal (poll / conformer
        // fan-out) — re-resolve by name, like editCalc's own await guard; if
        // the calc vanished or left an editable state, just drop the edit
        const idx = queue.findIndex(c => c.name === q.name);
        if (idx === -1 || !isEditableState(queue[idx].state)) {
          exitEditMode();
          setBuildMode("beginner");
          return;
        }
        await editCalc(idx);
        return;
      }
      toast("Raw input can't go back to the form — this calculation stays raw.");
      return;
    }
    if (buildMode === "expert" && rawText.trim()) {
      const ok = await confirmModal({
        title: "Back to the Beginner form?",
        body: "The raw .inp text can't be converted back to the form and will be " +
              "<b>discarded</b>. Name, type, charge/multiplicity, and geometry are kept.",
        confirm: "Discard & switch", danger: true,
      });
      if (!ok) return;
      rawText = "";
    }
    setBuildMode("beginner");
  }
}

function showRawCard(show) {
  document.getElementById("raw-card").style.display = show ? "block" : "none";
  if (show) {
    document.getElementById("raw-text").oninput = (e) => { rawText = /** @type {ORCAFormElement} */ (e.target).value; };
  }
}

// ---------- queue ----------
let _toastTimer = null;
function toast(msg) {
  /** @type {HTMLElement|null} */
  let t = document.getElementById("toast");
  if (!t) {
    t = document.createElement("div");
    t.id = "toast";
    t.className = "toast";
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

// Single failure channel: toast (immediate, visible from any tab) + log
// (persistent, reviewable later). Single-channel failures either vanished
// after 2.2s (toast-only) or went unseen unless the Log tab happened to be
// open (log-only) — every failure call site goes through here.
function failNotify(msg) {
  toast(msg);
  appendLog(msg, "err");
}

// view the ORCA .inp (read-only) for any calc, including a running one.
// Which source is the truth depends on the state: an EDITABLE calc has never
// produced this attempt's on-disk input — the calc itself (raw text or a
// preview) is authoritative, and the disk may hold a previous same-named
// calc's .inp (workspace folders are never deleted). For running/finished
// calcs the on-disk file is what actually launched.
async function viewInp(i) {
  const c = queue[i];
  if (!c) return;
  const fromDisk = async () => {
    try {
      const res = /** @type {TextResult} */ (JSON.parse(await bridge.get_inp(c.name)));
      return (res.ok && res.text) ? res.text : null;
    } catch (e) { return null; }
  };
  const fromCalc = async () => {
    let full = localCalcs[c.name];
    if (!full) { try { const r = /** @type {GetCalcResult} */ (JSON.parse(await bridge.get_calc(c.name))); if (r.ok) full = r.calc; } catch (e) { } }
    if (full && full.is_raw && full.raw_text) return full.raw_text;
    if (full) {
      try { const pv = /** @type {TextResult} */ (JSON.parse(await bridge.build_inp_preview(JSON.stringify(full)))); if (pv.ok) return pv.text; } catch (e) { }
    }
    return null;
  };
  const text = isEditableState(c.state)
    ? (await fromCalc()) ?? (await fromDisk())
    : (await fromDisk()) ?? (await fromCalc());
  if (text == null) { toast("Input not available yet (queue run needed first)."); return; }
  // the title lands via textContent (showModal) — pre-escaping would show "&amp;"
  await showModal(`Input · ${c.name}`, `<pre class="inp-view">${escapeHtml(text)}</pre>`, [{ label: "Close", value: null }]);
}

function renderQueue() {
  const el = document.getElementById("queue-list");
  if (!queue.length) { el.innerHTML = `<div class="queue-empty">
    <svg viewBox="0 0 24 24" width="40" height="40" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="queue-empty-icon">
      <path d="M3 7h18M3 12h18M3 17h18"/><circle cx="6" cy="7" r="0.5" fill="currentColor"/>
    </svg>
    <div class="queue-empty-title">No calculations queued</div>
    <div class="queue-empty-sub">A new calculation from the Build tab to get started.</div>
  </div>`; return; }
  el.innerHTML = "";
  queue.forEach((c, i) => {
    // ref_name is user-typed (a calc name) and lands in innerHTML — escape it
    // a per-conformer track clone bakes its conformer's geometry in as DIRECT,
    // so show its provenance ("from tt1 · conformer 2") instead of a bare "xyz"
    const srcLabel = c.geometry_source === "reference" ? `ref → ${escapeHtml(c.ref_name)}`
      : c.conformer_origin ? `from ${escapeHtml(c.conformer_origin)}` : "xyz";
    const isMlip = (c.kind || "").startsWith("mlip");
    const isCrest = (c.kind || "").startsWith("crest");
    const rawBadge = c.is_raw ? `<span class="qstate raw">raw</span>` : "";
    // every row shows its execution backend: MLIP / CREST / DFT (ORCA)
    const backendLabel = isMlip ? "MLIP" : isCrest ? "CREST" : "DFT";
    const backendBadge = `<span class="qstate raw">${backendLabel}</span>`;
    // ORCA always shows charge/mult; MLIP shows them only when non-default,
    // since OMol25 / multi-head MACE models use them (ions, radicals) while
    // MACE-OFF/MP ignore them — a neutral singlet MLIP row stays clean.
    const cmLabel = (!isMlip || c.charge !== 0 || c.multiplicity !== 1)
      ? ` · charge ${c.charge} · mult ${c.multiplicity}` : "";
    // backend detail (explicit CalcSummary fields, escaped): the MACE model is
    // otherwise invisible on desktop (MLIP calcs have no edit and no .inp view);
    // the CREST method + all-conformers handoff change what a run will do.
    const backendDetail = isMlip && c.mlip_model ? ` · ${escapeHtml(c.mlip_model)}`
      : isCrest ? `${c.crest_method ? " · " + escapeHtml(c.crest_method) : ""}${c.crest_handoff === "all" ? " · all conformers" : ""}`
      : "";
    // a "Completed." note is redundant with the done badge — hide completion notices
    const showMsg = !!c.message && !(c.state === "done" && /^Completed\b/.test(c.message));
    const editable = isEditableState(c.state);   // pending/cancelled/blocked: edit + drag
    const removable = c.state !== "running";       // anything but running can be deleted
    const div = document.createElement("div");
    div.className = "queue-item" + (editable ? " draggable" : "");
    div.dataset.index = String(i);
    if (editable) div.setAttribute("draggable", "true");
    const handle = editable
      ? `<span class="drag-handle" title="Reorder handle">≡</span>` : `<span class="drag-handle placeholder"></span>`;
    // view the input (.inp) — available for ANY state, incl. running/done
    // .inp is ORCA-only: MLIP/CREST calcs produce no ORCA input, so showing it
    // would fall back to a bogus generated preview — hide the button for them.
    const viewBtn = (isMlip || isCrest) ? ""
      : `<button class="btn btn-sm btn-ghost" onclick="viewInp(${i})" title="Input (.inp)">.inp</button>`;
    const editBtn = editable
      ? `<button class="btn btn-sm btn-ghost" onclick="editCalc(${i})">edit</button>` : "";
    const delBtn = removable
      ? `<button class="btn btn-sm btn-ghost" onclick="removeCalc(${i})" title="Remove">×</button>` : "";
    div.innerHTML = `
      ${handle}
      <div style="flex:1">
        <div class="qname">${escapeHtml(c.name)}${rawBadge}${backendBadge}</div>
        <div class="qsteps">${escapeHtml(c.kind)} · ${srcLabel}${backendDetail}${cmLabel}</div>
        ${showMsg ? (
          c.state === "failed"
            ? `<div class="qerror">⚠ ${escapeHtml(c.message)}</div>`
            : `<div class="qsteps" style="color:var(--muted-foreground)">${escapeHtml(c.message)}</div>`
        ) : ""}
      </div>
      <span class="qstate ${c.state}">${c.state}</span>
      ${viewBtn}
      ${editBtn}
      ${delBtn}`;
    if (editable) attachDragHandlers(div);
    el.appendChild(div);
  });
}

// ---- drag-to-reorder (pending items only) ----
let _dragFrom = null;
function attachDragHandlers(div) {
  div.addEventListener("dragstart", (e) => {
    _dragFrom = parseInt(div.dataset.index, 10);
    div.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
  });
  div.addEventListener("dragend", () => {
    div.classList.remove("dragging");
    _dragFrom = null;
    document.querySelectorAll(".queue-item.drop-target").forEach(x => x.classList.remove("drop-target"));
  });
  div.addEventListener("dragover", (e) => {
    if (_dragFrom === null) return;
    const target = parseInt(div.dataset.index, 10);
    if (queue[target] && isEditableState(queue[target].state)) {
      e.preventDefault();
      div.classList.add("drop-target");
    }
  });
  div.addEventListener("dragleave", () => div.classList.remove("drop-target"));
  div.addEventListener("drop", (e) => {
    e.preventDefault();
    div.classList.remove("drop-target");
    const to = parseInt(div.dataset.index, 10);
    if (_dragFrom !== null && _dragFrom !== to) reorderCalc(_dragFrom, to);
  });
}

async function reorderCalc(from, to) {
  // both endpoints must be editable (server enforces too)
  if (!queue[from] || !queue[to]) return;
  if (!isEditableState(queue[from].state) || !isEditableState(queue[to].state)) {
    failNotify("Reordering limited to pending, cancelled, or blocked calculations.");
    return;
  }
  try {
    await bridge.reorder_calc(from, to);
    await refreshQueue();
  } catch (e) { failNotify("Reorder failed."); }
}
async function removeCalc(i) {
  const c = queue[i];
  if (!c) return;
  if (c.state === "running") { failNotify("Could not remove: the calculation is running."); return; }
  // c.name is user-typed and goes into the modal's innerHTML — escape it
  if (!await confirmModal({ title: "Remove calculation?",
      body: `Remove <b>${escapeHtml(c.name)}</b> from the queue?`, confirm: "Remove", danger: true })) return;
  await bridge.remove_calc(c.name);
  delete localCalcs[c.name];
  // The name is free for reuse now: a kept display-cache entry would keep
  // serving the REMOVED calc's result under a reused name for the rest of the
  // session (maybeFetchResult early-returns on a cache hit), so invalidate.
  delete calcResults[c.name];
  delete _resultExtras[c.name];
  if (_currentResultName === c.name) _currentResultName = "";
  refreshResultSelect();
  await refreshQueue();
}
async function clearQueue() {
  if (isRunning()) return;
  if (!queue.length) return;
  if (!await confirmModal({ title: "Clear the whole queue?",
      body: `Remove all <b>${queue.length}</b> ${queue.length === 1 ? "calculation" : "calculations"} from the queue? This can't be undone.`,
      confirm: "Clear all", danger: true })) return;
  const res = /** @type {MutationResult} */ (JSON.parse(await bridge.clear_queue()));
  if (!res.ok) { failNotify(res.error || "Could not clear queue."); return; }
  for (const k of Object.keys(localCalcs)) delete localCalcs[k];
  // every name is free for reuse — drop the display caches too (see removeCalc)
  for (const k of Object.keys(calcResults)) delete calcResults[k];
  for (const k of Object.keys(_resultExtras)) delete _resultExtras[k];
  _currentResultName = "";
  refreshResultSelect();
  await refreshQueue();
}

function isRunning() { return _running; }

// generic modal: buttons = [{label, value, primary?, danger?}], returns chosen value (or null if dismissed)
function showModal(title, bodyHtml, buttons) {
  return new Promise((resolve) => {
    const overlay = document.getElementById("modal-overlay");
    document.getElementById("modal-title").textContent = title;
    document.getElementById("modal-body").innerHTML = bodyHtml;
    const actions = document.getElementById("modal-actions");
    actions.innerHTML = "";
    let onKey;
    const close = (v) => {
      overlay.style.display = "none";
      overlay.onclick = null;
      document.removeEventListener("keydown", onKey);
      resolve(v);
    };
    // dismiss (= null) on Escape or a click on the backdrop, so the themed modal
    // behaves like a normal dialog
    onKey = (e) => { if (e.key === "Escape") close(null); };
    document.addEventListener("keydown", onKey);
    overlay.onclick = (e) => { if (e.target === overlay) close(null); };
    for (const b of buttons) {
      const btn = document.createElement("button");
      btn.className = "btn" + (b.primary ? " btn-primary" : "") + (b.danger ? " btn-danger" : "");
      btn.textContent = b.label;
      btn.onclick = () => close(b.value);
      actions.appendChild(btn);
    }
    overlay.style.display = "flex";
  });
}

// themed yes/no confirmation (replaces the system confirm() for irreversible
// actions). opts: {title, body, confirm, cancel, danger}. Returns true if confirmed.
async function confirmModal(opts) {
  const v = await showModal(opts.title, opts.body || "", [
    { label: opts.cancel || "Cancel", value: false },
    { label: opts.confirm || "Confirm", value: true, danger: !!opts.danger, primary: !opts.danger },
  ]);
  return v === true;
}

async function runQueue() {
  // Re-entry guard: _running flips only AFTER the awaits below, so without
  // _starting a fast double-click would open the overwrite modal twice (orphaning
  // its promise). The backend still rejects a double start_run regardless.
  if (_running || _starting) return;
  if (!queue.length) { appendLog("No calculations queued.", "warn"); return; }
  // Mirrors store.queue_needs_orca (P4): ORCA is required only if a calc that
  // will actually launch it exists — an all-MLIP or all-CREST queue (or one
  // whose only ORCA calcs are DONE/FAILED, which never re-run) runs with no
  // ORCA configured. Both mlip* and crest* run outside ORCA, exactly like the
  // backend helper — the mirror omitting crest* wrongly blocked supported runs.
  const needsOrca = queue.some((c) =>
    c.state !== "done" && c.state !== "failed"
    && !c.kind.startsWith("mlip") && !c.kind.startsWith("crest"));
  if (needsOrca && !settings.orca_valid) { failNotify("ORCA path not set (see Settings)."); switchTab("settings"); return; }

  _starting = true;
  try {
    // Check whether any queued calc would overwrite an existing result on disk.
    let skipNames = [];
    try {
      const chk = /** @type {ConflictsResult} */ (JSON.parse(await bridge.check_overwrite_conflicts()));
      if (chk.ok && chk.conflicts && chk.conflicts.length) {
        // conflict names are user-typed calc names landing in innerHTML — escape
        const list = `<div class="names">${chk.conflicts.map(escapeHtml).join(", ")}</div>`;
        const nc = chk.conflicts.length;
        const choice = await showModal(
          "Overwrite existing results?",
          `${nc} ${nc === 1 ? "calculation already has" : "calculations already have"} results saved on disk:<br><br>${list}<br>` +
          `Running again will <b>overwrite</b> them. What would you like to do?`,
          [
            { label: "Cancel", value: "cancel" },
            { label: "Keep existing (skip these)", value: "skip" },
            { label: "Overwrite", value: "overwrite", danger: true },
          ]
        );
        if (choice === "cancel" || choice == null) { appendLog("Run cancelled.", "info"); return; }
        if (choice === "skip") skipNames = chk.conflicts;
        // "overwrite" → skipNames stays empty, everything runs
      }
    } catch (e) { /* if the check fails, fall through and run normally */ }

    appendLog("--- running queue ---", "info");
    const res = /** @type {OkResult} */ (JSON.parse(await bridge.run_queue(JSON.stringify(skipNames))));
    if (!res.ok) {
      failNotify("Could not start: " + res.error);
    } else {
      _running = true; _stopRequested = false; setRunUI(true);
    }
  } finally {
    _starting = false;
  }
}
async function cancelQueue() {
  if (!_running) return;
  // Cancel kills the running ORCA job and skips the rest — irreversible, so confirm.
  if (!await confirmModal({ title: "Stop the running job?",
      body: "This <b>kills the running ORCA process</b> and cancels the remaining queue. " +
            "Progress on the current job is lost. (To finish the current job first, use " +
            "<b>Stop after current</b> instead.)",
      confirm: "Stop run", cancel: "Keep running", danger: true })) return;
  await bridge.cancel_queue();
}
async function stopAfterCurrent() {
  _stopRequested = true;                  // one-shot for this run
  setRunUI(_running);
  const res = /** @type {OkResult} */ (JSON.parse(await bridge.stop_after_current()));
  appendLog(res.ok ? "Stop scheduled after the current job finishes."
                    : "Nothing running.", "info");
}
function setRunUI(running) {
  const rb = document.getElementById("run-btn");
  const cb = document.getElementById("cancel-btn");
  const sb = document.getElementById("stop-after-btn");
  if (rb) rb.disabled = running;
  if (cb) cb.disabled = !running;
  // stop-after is available only while running and until it's been requested
  if (sb) sb.disabled = !running || _stopRequested;
}

// queue/log/state changes are now reflected by pollTick() (shared store),
// so the old Qt-signal handlers (onCalcUpdate/onQueueFinished) are gone.
// We still pull result summaries when a calc finishes, lazily:
async function maybeFetchResult(name, outputPath) {
  if (!outputPath || calcResults[name]) return;
  try {
    // by NAME so the backend dispatches on the calc's KIND — the path-based
    // parse_out_path uses a folder heuristic meant for external files, which
    // can mis-fire when this name's folder holds a removed calc's leftovers
    const raw = await bridge.parse_calc_output(name);
    const data = /** @type {ParsePayload} */ (JSON.parse(raw));
    if (data && data.summary) {
      calcResults[name] = data.summary;
      _resultExtras[name] = data;   // keep the whole payload for rich rendering
      refreshResultSelect();
    }
  } catch (e) { /* parsing failed; skip */ }
}

// ---------- log ----------
let _scfTracker = SCFGraph ? new SCFGraph.SCFTracker() : null;
let _geoTracker = SCFGraph ? new SCFGraph.GeoTracker() : null;
let _freqTracker = SCFGraph ? new SCFGraph.FreqTracker() : null;
let _tddftTracker = SCFGraph ? new SCFGraph.TddftTracker() : null;
let _crestTracker = SCFGraph ? new SCFGraph.CrestTracker() : null;
let _seededGraph = new Set();   // calc names whose graph is already sourced (live stream or disk-seed)
const _OPT_KINDS = ["opt", "ts_opt", "opt_freq", "ts_opt_freq"];
let _scfIterTimes = [];         // arrival times (ms) of recent live SCF-iteration lines, for s/cycle pace
// Average wall-clock seconds per SCF iteration over the recent window. Uses
// (last - first)/(n-1) so it stays accurate even though lines arrive batched
// per 1s poll; null until there's enough of a time span to be meaningful.
function scfSecPerIter() {
  const t = _scfIterTimes;
  if (t.length < 3) return null;
  const span = t[t.length - 1] - t[0];
  if (span < 800) return null;
  return span / (t.length - 1) / 1000;
}
// display text for the pace chip ("" while there's no estimate yet)
function scfPaceText() {
  const p = scfSecPerIter();
  return p == null ? "" : `~${p < 10 ? p.toFixed(1) : Math.round(p)} s / SCF cycle`;
}
let _logMode = "raw";
let _graphKind = "auto";   // "auto" | "scf" | "geo"  (sub-mode inside graph)
function currentRunningScf() {
  const r = (queue || []).find(c => c.state === "running");
  return r ? (r.scf_convergence || "TightSCF") : "TightSCF";
}
function runningIsOpt() {
  const r = (queue || []).find(c => c.state === "running");
  return r ? ["opt", "ts_opt", "opt_freq", "ts_opt_freq"].includes(r.kind) : false;
}
// which graph to actually show: explicit choice, else geo if we have opt data
function effectiveGraphKind() {
  if (_graphKind === "scf") return "scf";
  if (_graphKind === "geo") return "geo";
  // auto: prefer geometry when the run is an opt and we have steps
  if (_geoTracker && _geoTracker.hasData() && runningIsOpt()) return "geo";
  return "scf";
}
const _LOG_MAX_LINES = 2000;     // cap DOM nodes so long runs don't lag
let _scfDirty = false;
function setLogMode(mode) {
  _logMode = mode;
  document.getElementById("logmode-raw").classList.toggle("active", mode === "raw");
  document.getElementById("logmode-graph").classList.toggle("active", mode === "graph");
  document.getElementById("log").style.display = mode === "raw" ? "block" : "none";
  document.getElementById("scf-panel").style.display = mode === "graph" ? "block" : "none";
  if (mode === "graph") renderSCFPanel();
  else scrollLogToBottom();   // entering raw: jump to latest and refresh the button
  updateLogJump();
}
function setGraphKind(k) { _graphKind = k; renderSCFPanel(); }
function renderSCFPanel() {
  if (!SCFGraph) return;
  const panel = document.getElementById("scf-panel");
  // A phase-chain run — a CREST conformer search, or the frequency / TD-DFT
  // pipeline — has no meaningful convergence curve below it: its stepper fills
  // the whole panel, no secondary graph. (CREST wins, then freq, then TD-DFT.)
  // The stepper's rail follows the window height: pass the space left below
  // the panel top (minus panel padding/border + the 16px window gutter); the
  // renderer falls back to its compact strip when that can't fit the rows.
  let phaseHtml = "";
  const phaseOpts = {
    height: Math.max(window.innerHeight - panel.getBoundingClientRect().top - 46, 0),
  };
  if (_crestTracker && _crestTracker.hasData()) phaseHtml = SCFGraph.renderCrestProgress(_crestTracker, phaseOpts);
  else if (_freqTracker && _freqTracker.hasData()) phaseHtml = SCFGraph.renderFreqProgress(_freqTracker, phaseOpts);
  else if (_tddftTracker && _tddftTracker.hasData()) phaseHtml = SCFGraph.renderTddftProgress(_tddftTracker, phaseOpts);
  if (phaseHtml) {
    panel.innerHTML = phaseHtml;
    _scfDirty = false;
    return;
  }
  const kind = effectiveGraphKind();
  // sub-toggle (SCF vs geometry) — only meaningful for opt runs
  const showToggle = (_geoTracker && _geoTracker.hasData());
  let head = "";
  if (showToggle) {
    head = `<div class="graph-subtoggle">
      <button class="${kind === 'geo' ? 'active' : ''}" onclick="setGraphKind('geo')">Optimization</button>
      <button class="${kind === 'scf' ? 'active' : ''}" onclick="setGraphKind('scf')">Current SCF</button>
    </div>`;
  }
  const isGeo = (kind === "geo" && _geoTracker && _geoTracker.hasData());
  let body;
  if (isGeo) {
    body = `<div class="graph-summary">${SCFGraph.renderGeoProgress(_geoTracker, scfPaceText())}</div>` +
           `<div class="graph-divider"></div>` +
           `<div class="graph-plot"></div>`;
  } else {
    body = `<div class="graph-summary">${SCFGraph.renderSCFProgress(_scfTracker, currentRunningScf(), scfPaceText())}</div>` +
           `<div class="graph-divider"></div>` +
           `<div class="graph-plot"></div>`;
  }
  panel.innerHTML = head + body;
  // Two-pass render: the summary/legend above the plot varies in height, so
  // measure the plot box NOW and pick a viewBox height whose aspect ratio makes
  // the SVG (width:100%, height:auto) end flush with the bottom 16px gutter —
  // no leftover space below, no scrolling.
  const plot = panel.querySelector(".graph-plot");
  if (plot) {
    const innerW = plot.clientWidth - 20;   // minus the plot box's 10px side padding
    // chrome below the SVG: plot padding/border + panel padding/border + window gutter ≈ 54px
    const innerH = Math.max(window.innerHeight - plot.getBoundingClientRect().top - 54, 220);
    // clientWidth is 0 while the Log tab is hidden — fall back to the flat
    // default; switchTab re-renders with a real measurement on entry
    const gopts = innerW > 0
      ? { width: 1100, height: Math.round(1100 * innerH / innerW) }
      : { width: 1100, height: 360 };
    plot.innerHTML = isGeo
      ? SCFGraph.renderGeoGraph(_geoTracker, gopts)
      : SCFGraph.renderSCFGraph(_scfTracker, currentRunningScf(), gopts);
  }
  _scfDirty = false;
}
// the graph is sized to the viewport, so it must follow window resizes
window.addEventListener("resize", () => {
  if (_logMode === "graph") renderSCFPanel();
});
// matches the queue's per-calc start marker, e.g. "[opt1] (opt) running ORCA..."
function logAtBottom() {
  const b = document.getElementById("log");
  return !b || b.scrollHeight - b.scrollTop - b.clientHeight < 40;
}
function scrollLogToBottom() {
  const b = document.getElementById("log");
  if (b) b.scrollTop = b.scrollHeight;
  updateLogJump();
}
// show the "jump to latest" button only when raw log is visible AND scrolled up
function updateLogJump() {
  const btn = document.getElementById("log-to-bottom");
  if (!btn) return;
  btn.hidden = !(_logMode === "raw" && !logAtBottom());
}
// every backend's per-calc start marker: ORCA "running ORCA…", CREST "running
// CREST…", MLIP "optimizing with…" / "single-point energy with…". Matching them
// resets the graph trackers so a new job's graph never inherits the previous curve.
const _CALC_START_RE = /^\[(.+?)\]\s*\([^)]*\)\s*(?:running ORCA|running CREST|optimizing|single-point)/i;
function appendLog(msg, level) {
  // a new calculation is starting: reset the convergence trackers so the graph
  // reflects the new job (and not the previous opt/freq)
  const _startM = SCFGraph ? _CALC_START_RE.exec(msg) : null;
  if (_startM) {
    _scfTracker = new SCFGraph.SCFTracker();
    _geoTracker = new SCFGraph.GeoTracker();
    _freqTracker = new SCFGraph.FreqTracker();
    _tddftTracker = new SCFGraph.TddftTracker();
    _crestTracker = new SCFGraph.CrestTracker();
    _graphKind = "auto";
    _scfDirty = true;
    _scfIterTimes = [];   // new job: restart the s/cycle pace estimate
    // this session's live stream owns this calc's graph from the start, so the
    // reattach disk-seed (maybeSeedGraph) must not also rebuild it
    _seededGraph.add(_startM[1]);
  }
  const box = document.getElementById("log");
  // only auto-follow if the user is already at (near) the bottom — don't yank
  // them down while they've scrolled up to read earlier output
  const stick = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  const div = document.createElement("div");
  div.className = "log-line log-" + (level || "info");
  div.textContent = msg;
  box.appendChild(div);
  // trim old lines so the DOM doesn't grow without bound (this was the lag)
  while (box.childElementCount > _LOG_MAX_LINES) box.removeChild(box.firstChild);
  if (stick) box.scrollTop = box.scrollHeight;
  updateLogJump();
  // feed both trackers; mark dirty (redraw is throttled in pollTick)
  let changed = false;
  if (_scfTracker && _scfTracker.push(msg)) changed = true;
  if (_geoTracker && _geoTracker.push(msg)) changed = true;
  if (_freqTracker && _freqTracker.push(msg)) changed = true;
  if (_tddftTracker && _tddftTracker.push(msg)) changed = true;
  if (_crestTracker && _crestTracker.push(msg)) changed = true;
  if (changed) _scfDirty = true;
  // record SCF-iteration arrival times for the s/cycle pace (live lines only;
  // disk-seeded lines bypass appendLog so they don't skew the timing)
  if (SCFGraph && SCFGraph.isScfIter(msg)) {
    _scfIterTimes.push(Date.now());
    if (_scfIterTimes.length > 40) _scfIterTimes.shift();
  }
}
function clearLog() {
  document.getElementById("log").innerHTML = "";
  _scfIterTimes = [];
  updateLogJump();
  const paceEl = document.getElementById("scf-pace");
  if (paceEl) paceEl.textContent = "";
  if (SCFGraph) {
    _scfTracker = new SCFGraph.SCFTracker();
    _geoTracker = new SCFGraph.GeoTracker();
    _freqTracker = new SCFGraph.FreqTracker();
    _tddftTracker = new SCFGraph.TddftTracker();
    _crestTracker = new SCFGraph.CrestTracker();
    _seededGraph.clear();   // allow every calc's graph to re-seed
    if (_logMode === "graph") renderSCFPanel();
  }
}

// ---------- results ----------
function refreshResultSelect() {
  const sel = document.getElementById("result-select");
  const prev = sel.value;
  sel.innerHTML = "";
  const names = Object.keys(calcResults);
  if (!names.length) { sel.innerHTML = `<option>—</option>`; return; }
  for (const n of names) { const e = document.createElement("option"); e.value=n; e.textContent=n; sel.appendChild(e); }
  if (names.includes(prev)) sel.value = prev;
}
function showSelectedResult() {
  const name = document.getElementById("result-select").value;
  if (!name || name === "—") return;
  _currentResultName = name;   // a queued calc — enables the conformer->ORCA action
  _currentResult = _resultExtras[name] || null;
  renderResult(_currentResult);
}

/** Render summary + every applicable section for a parsed payload. @param {ParsePayload} [d] */
function renderResult(d) {
  if (!d) return;
  if (d.summary) renderSummary(d.summary, d);
  renderResultSections(d);
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
  // "Final geometry" → opt jobs; electronic-structure sections → sp/opt jobs.
  // "Show all" overrides both. Specialty sections (freq/tddft/nmr/neb) are
  // present-only — they only exist for their kind, so no extra gating needed.
  const geom = showAllResults || d.is_optimization;
  const elec = showAllResults || d.show_elec;
  // CREST conformer ensemble: a selectable list with a batch "re-optimize in
  // ORCA" action. Present-only (only crest_conf results carry it).
  if (d.is_conformer_search && d.conformers && d.conformers.length) renderConformers(d.conformers);
  if (geom && d.geometry && d.geometry.length) renderGeometry(d.geometry);
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
 *  referencing the CREST search (its "Conformer handoff" scope decides whether
 *  the referencing chain fans out per conformer). @param {ConformerPayload[]} confs */
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
  // Export is only meaningful for a queued CREST calc (needs its workspace
  // folder server-side); an externally opened .out has no _currentResultName.
  const exportBtn = _currentResultName
    ? `<button class="btn btn-sm btn-ghost" onclick="exportConformers()">Export as .xyz</button>` : "";
  body.innerHTML += `
    <div class="divider"></div>
    <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px">
      <div class="card-title">Conformers (${confs.length})</div>
      ${exportBtn}
    </div>
    <div style="max-height:280px;overflow:auto">
      <table class="data">
        <thead><tr><th>#</th><th>ΔE (kcal/mol)</th><th>E (Eh)</th><th>atoms</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="hint" style="margin-top:6px">Follow-up calculations are built on the Build tab: reference this search from a geometry source — its Conformer handoff setting decides whether the chain runs on the lowest conformer or fans out per conformer. <b>Export as .xyz</b> writes every conformer (c1 = the best) to a <code>conformers/</code> subfolder of the run.</div>`;
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
  _lastGeomXyz = `${geom.length}\n\n` + geom.map(a => `${a.el}  ${a.x.toFixed(6)}  ${a.y.toFixed(6)}  ${a.z.toFixed(6)}`).join("\n");
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

/** @param {OrbitalPayload[]} orbs */
function renderOrbitals(orbs) {
  const body = document.getElementById("result-body");
  let homoI = -1;
  orbs.forEach((o, i) => { if (o.occ > 0.01) homoI = i; });
  let rows = "";
  orbs.forEach((o, i) => {
    let tag = "", color = "";
    if (i === homoI) { tag = " ← HOMO"; color = "color:var(--ok);font-weight:600"; }
    else if (i === homoI + 1) { tag = " ← LUMO"; color = "color:var(--warn);font-weight:600"; }
    rows += `<tr><td>${o.idx}</td><td>${o.occ.toFixed(3)}</td><td style="${color}">${o.ev.toFixed(4)}${tag}</td></tr>`;
  });
  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">Orbital energies (${orbs.length} levels)</div>
    <div style="max-height:280px;overflow:auto">
      <table class="data">
        <thead><tr><th>#</th><th>Occ</th><th>E (eV)</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
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
  const pre = (t) => `<pre class="mono" style="white-space:pre-wrap;word-break:break-word;font-size:12px;background:rgba(127,127,127,0.08);padding:10px;border-radius:var(--radius-sm);margin:6px 0 0;overflow:auto">${escapeHtml(t)}</pre>`;
  let html = `<div class="divider"></div><div class="card-title">Input echo</div>`;
  if (keywords) html += `<div class="hint" style="margin-top:8px">Keywords</div>${pre(keywords)}`;
  if (block) html += `<div class="hint" style="margin-top:8px">Input block</div>${pre(block)}`;
  body.innerHTML += html;
}

async function openOutFile() {
  const raw = await bridge.parse_out_file();
  /** @type {ParsePayload} */
  let data; try { data = JSON.parse(raw); } catch { return; }
  if (data.cancelled) return; // user closed the picker — not an error
  if (!data.summary) { appendLog("Could not parse file.", "err"); return; }
  _currentResultName = "";   // an external file, not a queued calc → no conformer->ORCA action
  _currentResult = data;
  renderResult(data);
  switchTab("results");
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

// ---------- misc ----------
function resetBuild() {
  document.getElementById("calc-name").value = "";
  document.getElementById("calc-charge").value = "0";
  document.getElementById("calc-mult").value = "1";
  directXyz = "";
  _nebProductXyz = "";          // clear any loaded NEB product geometry
  document.getElementById("xyz-status").textContent = "";
  document.querySelector('input[name="geomsrc"][value="direct"]').checked = true;
  onGeomSourceChange();
  document.getElementById("calc-kind").value = "opt";
  renderConfigForm("opt");
  fillBasisRows([]);
  exitEditMode();
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
}
