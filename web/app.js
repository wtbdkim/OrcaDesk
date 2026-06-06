/* ============================================================
   ORCAdesk front-end logic — calculation-based queue
   ============================================================ */

let bridge = null;
let settings = {};
let queue = [];                 // UI mirror of the shared store's queue
let directXyz = "";             // last loaded .xyz coordinate block
const calcResults = {};         // name -> summaryRows
const _resultExtras = {};       // name -> {transitions?, nmr?}
const localCalcs = {};          // name -> full calc (config/xyz/raw) added on THIS PC,
                                // so editing keeps the details the store snapshot omits

let editIndex = -1;             // queue index being edited, or -1 for "new"
let rawMode = false;            // is the current build form in raw mode?
let rawText = "";               // current raw .inp text being edited
let _running = false;           // mirrors store.running

// Calculations the user can still edit / remove / reorder: never-run (pending)
// plus finished-unsuccessfully (failed/cancelled), so they can be fixed and
// retried. done/running/blocked are frozen. Mirrors EDITABLE_STATES in store.py.
function isEditableState(state) {
  return state === "pending" || state === "failed" || state === "cancelled";
}

// per-kind defaults for the config form
const KIND_DEFS = {
  opt:     { calcGroup: "calculation_types_geometry",  calcDefault: "TightOpt", scfDefault: "TightSCF",     showMaxIter: true,  showTddft: false, showFreq: false, showNmr: false, allTypes: false, options: "" },
  ts_opt:  { calcGroup: "calculation_types_geometry",  calcDefault: "OptTS",    scfDefault: "TightSCF",     showMaxIter: true,  showTddft: false, showFreq: false, showNmr: false, allTypes: false, options: "" },
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

// ---------- bridge bootstrap ----------
new QWebChannel(qt.webChannelTransport, async function(channel) {
  bridge = channel.objects.bridge;

  await loadAllChoices();
  await loadSettings();
  await loadAbout();
  if (SCFGraph && SCFGraph.setEtaMode && settings.eta_mode) SCFGraph.setEtaMode(settings.eta_mode);
  renderConfigForm("opt");

  // The queue + log now live in a shared store (also used by the phone). We
  // poll cheap getters instead of using Qt signals, so the desktop reflects
  // changes made from any device, and the store's worker thread stays cleanly
  // separated from the UI thread.
  await refreshQueue();
  startPolling();

  appendLog("Ready.", "info");
});

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
    const logRes = JSON.parse(await bridge.get_log(_logSeq));
    if (logRes && logRes.lines) {
      for (const ln of logRes.lines) appendLog(ln.msg, ln.level);
      if (typeof logRes.latest === "number") _logSeq = logRes.latest;
    }
    // queue changes (only re-render if version changed)
    const snap = JSON.parse(await bridge.get_queue());
    if (snap && snap.version !== _queueVersion) {
      _queueVersion = snap.version;
      queue = (snap.calculations || []).map(mirrorCalc);
      renderQueue();
      _running = !!snap.running;
      setRunUI(_running);
      // auto-load results for any finished calculation
      for (const c of queue) {
        if (c.state === "done" && c.output_path) maybeFetchResult(c.name, c.output_path);
      }
    } else if (snap) {
      if (!!snap.running !== _running) { _running = !!snap.running; setRunUI(_running); }
    }
    // redraw SCF graph at most once per tick, only if new data arrived
    if (_logMode === "graph" && _scfDirty) renderSCFPanel();
  } catch (e) { /* transient; try again next tick */ }
}

// turn a store snapshot calc into the shape the UI render expects
function mirrorCalc(c) {
  return {
    name: c.name, kind: c.kind, state: c.state, message: c.message,
    is_raw: c.is_raw, charge: c.charge, multiplicity: c.multiplicity,
    geometry_source: c.geometry_source, ref_name: c.ref_name,
    output_path: c.output_path || "",
    scf_convergence: c.scf_convergence || "TightSCF",
    // config/xyz aren't returned by the snapshot; editing pulls from here only
    // for display. (Full re-edit of phone-added calcs is a later refinement.)
  };
}

async function refreshQueue() {
  try {
    const snap = JSON.parse(await bridge.get_queue());
    _queueVersion = snap.version;
    queue = (snap.calculations || []).map(mirrorCalc);
    renderQueue();
  } catch (e) { /* ignore */ }
}

async function loadAbout() {
  try {
    const a = JSON.parse(await bridge.get_about());
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
  const names = ["functionals","basis_sets","calculation_types","scf_convergences","ri_approximations","solvents"];
  for (const n of names) {
    try { choicesCache[n] = JSON.parse(await bridge.load_choices(n)); }
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
async function loadSettings() {
  settings = JSON.parse(await bridge.get_settings());
  document.getElementById("set-orca").value = settings.orca_path || "";
  document.getElementById("set-ws").value = settings.workspace_root || "";
  document.getElementById("set-nprocs").value = settings.default_nprocs || 6;
  document.getElementById("set-maxcore").value = settings.default_maxcore_mb || 2400;
  // ETA mode radio
  const mode = settings.eta_mode || "conservative";
  const radio = document.querySelector(`input[name="eta-mode"][value="${mode}"]`);
  if (radio) radio.checked = true;
  updateOrcaStatus(settings.orca_valid);
}
function updateOrcaStatus(valid) {
  const pill = document.getElementById("orca-status");
  pill.classList.toggle("ok", !!valid);
  document.getElementById("orca-status-text").textContent = valid ? "ORCA ready" : "ORCA not set";
}
async function saveSettings() {
  const etaEl = document.querySelector('input[name="eta-mode"]:checked');
  const payload = {
    orca_path: document.getElementById("set-orca").value.trim(),
    workspace_root: document.getElementById("set-ws").value.trim(),
    default_nprocs: parseInt(document.getElementById("set-nprocs").value, 10) || 6,
    default_maxcore_mb: parseInt(document.getElementById("set-maxcore").value, 10) || 2400,
    eta_mode: etaEl ? etaEl.value : "conservative",
  };
  settings = JSON.parse(await bridge.save_settings(JSON.stringify(payload)));
  updateOrcaStatus(settings.orca_valid);
  // push the new mode to the live graph immediately
  if (SCFGraph && SCFGraph.setEtaMode) SCFGraph.setEtaMode(settings.eta_mode);
  const s = document.getElementById("set-saved");
  s.textContent = "Saved."; setTimeout(() => s.textContent = "", 2000);
}
async function pickOrca() { const p = await bridge.pick_orca_executable(); if (p) document.getElementById("set-orca").value = p; }
async function pickWorkspace() { const p = await bridge.pick_workspace(); if (p) document.getElementById("set-ws").value = p; }
async function autodetectOrca() {
  const p = await bridge.autodetect_orca();
  if (p) { document.getElementById("set-orca").value = p; appendLog("Auto-detected ORCA: " + p, "ok"); }
  else appendLog("Could not auto-detect ORCA. Set the path manually.", "warn");
}

// ---------- tabs ----------
function switchTab(name) {
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".panel").forEach(p => p.classList.toggle("active", p.dataset.panel === name));
  if (name === "results") loadFreeEnergyProfile();
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
  const selfName = editIndex !== -1 && queue[editIndex] ? queue[editIndex].name : null;
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

async function loadXyz() {
  const content = await bridge.load_xyz_file();
  if (!content) return;
  const lines = content.split(/\r?\n/);
  let start = 0;
  if (lines.length >= 2 && /^\s*\d+\s*$/.test(lines[0])) start = 2;  // skip count+comment
  const coords = [];
  for (let i = start; i < lines.length; i++) {
    const p = lines[i].trim().split(/\s+/);
    if (p.length < 4) continue;
    const [e, x, y, z] = p;
    if ([x,y,z].some(v => isNaN(parseFloat(v)))) continue;
    coords.push(`${e} ${x} ${y} ${z}`);
  }
  directXyz = coords.join("\n");
  const st = document.getElementById("xyz-status");
  st.textContent = coords.length ? `${coords.length} atoms loaded.` : "No atoms found in file.";
  appendLog(`Loaded ${coords.length} atoms from .xyz.`, coords.length ? "ok" : "warn");
}

// ---------- per-element basis / ECP ----------
function addBasisRow(element, basis, ecp) {
  const host = document.getElementById("basis-rows");
  const row = document.createElement("div");
  row.className = "basis-row";
  row.innerHTML = `
    <input class="be-el mono" type="text" placeholder="I" value="${element ?? ""}">
    <input class="be-basis mono" type="text" placeholder="def2-TZVP" value="${basis ?? ""}">
    <input class="be-ecp mono" type="text" placeholder="def2-ECP" value="${ecp ?? ""}">
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
function onKindChange() {
  if (rawMode) return;  // raw calcs are locked to their text; kind change ignored
  // keep the method-related fields the user already set; only the kind-specific
  // rows (opt/freq/tddft/nmr options) should change
  const preserve = {
    functional: comboValue("combo-functional"),
    basis_set: comboValue("combo-basis"),
    solvent: comboValue("combo-solvent"),
    ri: (document.getElementById("cfg-ri") || {}).value,
    solvmodel: (document.getElementById("cfg-solvmodel") || {}).value,
    options: (document.getElementById("cfg-options") || {}).value,
  };
  renderConfigForm(document.getElementById("calc-kind").value, preserve);
}

function renderConfigForm(kind, preserve) {
  const def = KIND_DEFS[kind];
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
      <label class="checkbox"><input id="cfg-jcoupling" type="checkbox"> Also compute J-couplings (%eprnmr SSALL)</label>
    </div>
    <div class="hint">NMR shielding is always computed; check the box to add spin-spin (J) couplings.</div>` : "";
  const tddftRows = def.showTddft ? `
    <div class="field-row">
      <div class="field"><label>nroots</label><input id="cfg-nroots" type="number" value="40" min="1"></div>
      <div class="field"><label>maxdim</label><input id="cfg-maxdim" type="number" value="10" min="1"></div>
      <div class="field"><label class="checkbox" style="margin-top:24px"><input id="cfg-tda" type="checkbox"> TDA</label></div>
      <div class="field"><label class="checkbox" style="margin-top:24px"><input id="cfg-triplets" type="checkbox"> triplets</label></div>
    </div>` : "";
  const freqRows = def.showFreq ? `
    <div class="field-row">
      <div class="field"><label>Temperature (K)</label><input id="cfg-temp" type="number" value="298.15" step="0.01" min="0"></div>
      <div class="field"><label>Pressure (atm)</label><input id="cfg-pressure" type="number" value="1.0" step="0.1" min="0"></div>
    </div>
    <div class="hint">Defaults (298.15 K, 1.0 atm) omit the %freq block; change them to emit it.</div>` : "";
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
      <div class="field"><label>.hess filename</label><input id="cfg-irc-hessfile" type="text" class="mono" placeholder="e.g. TS2.hess (in this calc's folder)"></div>
    </div>
    <div class="hint">IRC starts from a TS structure. Set Geometry below to <b>reference</b> a TS calculation. Reading a .hess from that TS's freq run is fastest; otherwise it's recomputed here.</div>` : "";
  const nebRows = def.showNeb ? `
    <div class="field-row">
      <div class="field"><label>Product geometry (.xyz)</label>
        <button class="btn btn-sm" onclick="loadNebProduct()">Load product .xyz…</button>
        <span id="cfg-neb-prod-status" class="hint" style="margin-left:8px">no product loaded</span>
      </div>
      <div class="field" style="flex:0 0 120px"><label>Images</label><input id="cfg-neb-nimages" type="number" value="8" min="3"></div>
      <div class="field"><label class="checkbox" style="margin-top:24px"><input id="cfg-neb-preopt" type="checkbox"> Pre-opt ends</label></div>
    </div>
    <div id="cfg-neb-atomcheck" class="hint" style="margin-top:4px"></div>
    <div class="hint">NEB-TS finds the TS between the reactant (set Geometry below) and the product. <b>The two structures must have the same atoms in the same order</b> — build the product by copying the reactant and moving atoms, then load it here.</div>` : "";

  host.innerHTML = `
    <div class="field-row">
      <div class="field"><label>Functional</label>
        <div class="combo" id="combo-functional">
          <input type="text" class="mono combo-input" autocomplete="off" placeholder="type to search or enter your own">
          <div class="combo-list" style="display:none"></div>
        </div>
      </div>
      <div class="field"><label>Basis set</label>
        <div class="combo" id="combo-basis">
          <input type="text" class="mono combo-input" autocomplete="off" placeholder="type to search or enter your own">
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
          <input type="text" class="mono combo-input" autocomplete="off" placeholder="type to search or enter your own">
          <div class="combo-list" style="display:none"></div>
        </div>
      </div>
    </div>
    <div class="field-row">
      <div class="field"><label>Extra options</label><input id="cfg-options" type="text" class="mono" value="${def.options}"></div>
      <div class="field" style="flex:0 0 130px"><label>maxcore (MB)</label><input id="cfg-maxcore" type="number" value="${settings.default_maxcore_mb||2400}" min="100" step="100"></div>
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
  fillSelect(document.getElementById("cfg-scf"), flatItems(choicesCache.scf_convergences), def.scfDefault);
  fillSelect(document.getElementById("cfg-ri"), flatItems(choicesCache.ri_approximations), (preserve && preserve.ri) || "RIJCOSX");
  if (def.allTypes) {
    fillSelect(document.getElementById("cfg-calc"), flatItems(choicesCache.calculation_types), "SP");
  } else if (def.calcGroup) {
    fillSelect(document.getElementById("cfg-calc"), flatItems(choicesCache.calculation_types, def.calcGroup), def.calcDefault);
  }
  setupCombo("combo-solvent", choicesCache.solvents, (preserve && preserve.solvent) || "Water");
  // restore solvation model + extra options if preserved
  if (preserve) {
    const sm = document.getElementById("cfg-solvmodel"); if (sm && preserve.solvmodel != null) sm.value = preserve.solvmodel;
    const op = document.getElementById("cfg-options"); if (op && preserve.options != null) op.value = preserve.options;
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
  const raw = await bridge.load_xyz_file();
  const data = JSON.parse(raw);
  if (data && data.xyz) {
    _nebProductXyz = data.xyz;
    const st = document.getElementById("cfg-neb-prod-status");
    if (st) st.textContent = `loaded (${countAtoms(data.xyz)} atoms)`;
    nebAtomCheck();
  }
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
  const react = directXyz, prod = _nebProductXyz;
  if (!react || !prod) { box.className = "hint"; box.textContent = ""; return; }
  const r = xyzElements(react), p = xyzElements(prod);
  if (r.length !== p.length) {
    box.className = "qerror"; box.textContent = `⚠ Atom count differs: reactant ${r.length}, product ${p.length}. NEB-TS needs the same atoms in both.`;
    return;
  }
  // composition
  const tally = arr => arr.reduce((m, e) => (m[e] = (m[e]||0)+1, m), {});
  const tr = tally(r), tp = tally(p);
  const composMismatch = Object.keys({...tr, ...tp}).some(e => tr[e] !== tp[e]);
  if (composMismatch) {
    box.className = "qerror"; box.textContent = `⚠ Element composition differs between reactant and product.`;
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

function collectConfig(kind) {
  const def = KIND_DEFS[kind];
  const v = (id) => { const e = document.getElementById(id); return e ? e.value : ""; };
  const num = (id, d) => { const e = document.getElementById(id); return e ? (parseInt(e.value,10) || d) : d; };
  const fnum = (id, d) => { const e = document.getElementById(id); return e ? (parseFloat(e.value) || d) : d; };
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
function collectCalcFromForm() {
  const name = document.getElementById("calc-name").value.trim();
  if (!name) throw new Error("Name is required.");
  if (/[\\/:*?"<>|]/.test(name))
    throw new Error(`Name contains characters not allowed in folder names: \\ / : * ? " < > |`);
  // P1: name collision (allow self when editing)
  const clash = queue.findIndex((c, idx) => c.name === name && idx !== editIndex);
  if (clash !== -1)
    throw new Error(`A calculation named "${name}" is already in the queue. Names must be unique (used as folder names).`);

  const kind = document.getElementById("calc-kind").value;
  const src = currentGeomSource();
  let xyz = "", ref_name = "";
  if (src === "direct") {
    // in raw+direct the coords live in the raw text; xyz may be empty
    if (!rawMode && !directXyz) throw new Error("Load an .xyz file first.");
    xyz = directXyz;
  } else {
    ref_name = document.getElementById("ref-select").value;
    if (!ref_name) throw new Error("Select a calculation to reference.");
    if (ref_name === name) throw new Error("A calculation can't reference its own geometry.");
  }

  // raw integrity: reference mode requires the placeholder
  if (rawMode && src === "reference" && !rawText.includes("{{GEOMETRY}}"))
    throw new Error("Raw input references another calculation but is missing the {{GEOMETRY}} placeholder.");

  // NEB-TS needs a product geometry (unless the user is hand-writing raw input)
  if (!rawMode && kind === "neb_ts" && !_nebProductXyz)
    throw new Error("NEB-TS needs a product geometry. Load a product .xyz first.");

  return {
    name, kind,
    charge: parseInt(document.getElementById("calc-charge").value, 10) || 0,
    multiplicity: parseInt(document.getElementById("calc-mult").value, 10) || 1,
    geometry_source: src,
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
    const wasEditing = editIndex !== -1;
    const oldName = wasEditing && queue[editIndex] ? queue[editIndex].name : null;

    if (wasEditing && oldName) {
      // edit in place: preserves the calc's position in the queue
      const res = JSON.parse(await bridge.update_calc(oldName, JSON.stringify(calc)));
      if (!res.ok) { appendLog("Could not update: " + res.error, "err"); alert(res.error); await refreshQueue(); return; }
      if (oldName !== calc.name) delete localCalcs[oldName];
      localCalcs[calc.name] = calc;
      appendLog(`Updated "${calc.name}".`, "ok");
      exitEditMode();
      await refreshQueue();
      switchTab("queue");
      return;
    }

    const res = JSON.parse(await bridge.add_calc(JSON.stringify(calc)));
    if (!res.ok) {
      appendLog("Could not add: " + res.error, "err");
      alert(res.error);
      await refreshQueue();
      return;
    }
    localCalcs[calc.name] = calc;
    appendLog(`Added "${calc.name}" (${calc.kind}${calc.is_raw ? ", raw" : ""}) to queue.`, "ok");
    exitEditMode();
    await refreshQueue();
    switchTab("queue");
  } catch (e) {
    alert(e.message); appendLog(e.message, "err");
  }
}

// ---------- editing existing calcs ----------
function editCalc(i) {
  const mirror = queue[i];
  if (!mirror) return;
  if (!isEditableState(mirror.state)) { toast("Only pending, failed, or cancelled calculations can be edited."); return; }
  // prefer the full local copy (has config/xyz/raw_text); fall back to mirror
  const c = localCalcs[mirror.name] || mirror;
  if (!localCalcs[mirror.name]) {
    appendLog(`"${mirror.name}" was added from another device; full options aren't available to edit here. You can remove it and recreate it.`, "warn");
  }
  editIndex = i;

  document.getElementById("calc-name").value = c.name;
  document.getElementById("calc-charge").value = c.charge;
  document.getElementById("calc-mult").value = c.multiplicity;
  document.getElementById("calc-kind").value = c.kind;

  // geometry source
  document.querySelector(`input[name="geomsrc"][value="${c.geometry_source}"]`).checked = true;
  onGeomSourceChange();
  if (c.geometry_source === "direct") {
    directXyz = c.xyz || "";
    document.getElementById("xyz-status").textContent =
      directXyz ? `${directXyz.split("\n").filter(Boolean).length} atoms loaded.` : "";
  } else {
    refreshRefSelect();
    document.getElementById("ref-select").value = c.ref_name;
  }

  if (c.is_raw) {
    // raw calcs: form is locked; only the raw editor is shown
    rawMode = true; rawText = c.raw_text || "";
    renderConfigForm(c.kind);   // populate (will be hidden)
    fillConfigForm(c.config);
    showRawCard(true);
    document.getElementById("raw-text").value = rawText;
    lockFormForRaw(true);
  } else {
    rawMode = false; rawText = "";
    renderConfigForm(c.kind);
    fillConfigForm(c.config);
    showRawCard(false);
    lockFormForRaw(false);
  }

  updateEditUI();
  switchTab("build");
}

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
  // NEB-TS fields
  set("cfg-neb-nimages", cfg.neb_nimages);
  const preopt = document.getElementById("cfg-neb-preopt");
  if (preopt && cfg.neb_preopt_ends != null) preopt.checked = cfg.neb_preopt_ends;
  if (cfg.neb_product_xyz) {
    _nebProductXyz = cfg.neb_product_xyz;
    const st = document.getElementById("cfg-neb-prod-status");
    if (st) st.textContent = `loaded (${countAtoms(cfg.neb_product_xyz)} atoms)`;
    nebAtomCheck();
  }
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
  if (editIndex === -1) {
    banner.style.display = "none";
    addBtn.textContent = "Add to queue →";
  } else {
    banner.style.display = "block";
    banner.textContent = `Editing: ${queue[editIndex].name}${rawMode ? " (raw)" : ""}`;
    addBtn.textContent = "Update";
  }
}

function exitEditMode() {
  editIndex = -1; rawMode = false; rawText = "";
  showRawCard(false); lockFormForRaw(false);
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
async function enterRawMode() {
  if (rawMode) { switchTab("build"); return; }
  const ok = confirm(
    "Switch to RAW mode?\n\n" +
    "You'll edit the ORCA .inp directly. After saving, this calculation can " +
    "no longer be edited through the form — only as raw text. This cannot be undone.\n\n" +
    "Continue?"
  );
  if (!ok) return;

  // Enter raw mode BEFORE collecting the form: raw input carries its own
  // coordinates (typed directly into the .inp), so the "load an .xyz first"
  // check must not fire here. If anything below fails, roll rawMode back.
  rawMode = true;
  let calc;
  try {
    calc = collectCalcFromForm();
  } catch (e) {
    rawMode = false;
    alert(e.message);
    return;
  }

  const res = JSON.parse(await bridge.build_inp_preview(JSON.stringify(calc)));
  if (!res.ok) {
    rawMode = false;
    appendLog("Could not generate .inp: " + res.error, "err");
    return;
  }

  rawText = res.text;
  document.getElementById("raw-text").value = rawText;
  document.getElementById("raw-text").oninput = (e) => { rawText = e.target.value; };
  showRawCard(true);
  lockFormForRaw(true);
  updateEditUI();
  appendLog("Raw mode: edit the .inp below (type your coordinates after the '* xyz' line), then Add/Update.", "info");
}

function showRawCard(show) {
  document.getElementById("raw-card").style.display = show ? "block" : "none";
  if (show) {
    document.getElementById("raw-text").oninput = (e) => { rawText = e.target.value; };
  }
}

function lockFormForRaw(locked) {
  // disable the form controls so raw calcs aren't form-edited
  document.getElementById("calc-config").style.opacity = locked ? "0.45" : "1";
  document.getElementById("calc-config").style.pointerEvents = locked ? "none" : "auto";
  const br = document.getElementById("basis-rows");
  if (br) { br.style.opacity = locked ? "0.45" : "1"; br.style.pointerEvents = locked ? "none" : "auto"; }
  document.getElementById("raw-btn").style.display = locked ? "none" : "inline-flex";
}

// ---------- queue ----------
let _toastTimer = null;
function toast(msg) {
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

function renderQueue() {
  const el = document.getElementById("queue-list");
  if (!queue.length) { el.innerHTML = `<div class="queue-empty">
    <svg viewBox="0 0 24 24" width="40" height="40" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="queue-empty-icon">
      <path d="M3 7h18M3 12h18M3 17h18"/><circle cx="6" cy="7" r="0.5" fill="currentColor"/>
    </svg>
    <div class="queue-empty-title">No calculations queued</div>
    <div class="queue-empty-sub">Build one in the Build tab to get started.</div>
  </div>`; return; }
  el.innerHTML = "";
  queue.forEach((c, i) => {
    const srcLabel = c.geometry_source === "reference" ? `ref → ${c.ref_name}` : ".xyz";
    const rawBadge = c.is_raw ? `<span class="qstate raw">raw</span>` : "";
    const editable = isEditableState(c.state);   // pending/failed/cancelled: edit + drag
    const removable = c.state !== "running";       // anything but running can be deleted
    const div = document.createElement("div");
    div.className = "queue-item" + (editable ? " draggable" : "");
    div.dataset.index = i;
    if (editable) div.setAttribute("draggable", "true");
    const handle = editable
      ? `<span class="drag-handle" title="Drag to reorder">≡</span>` : `<span class="drag-handle placeholder"></span>`;
    const editBtn = editable
      ? `<button class="btn btn-sm btn-ghost" onclick="editCalc(${i})">edit</button>` : "";
    const delBtn = removable
      ? `<button class="btn btn-sm btn-ghost" onclick="removeCalc(${i})" title="Remove">×</button>` : "";
    div.innerHTML = `
      ${handle}
      <div style="flex:1">
        <div class="qname">${escapeHtml(c.name)} ${rawBadge}</div>
        <div class="qsteps">${c.kind} · ${srcLabel} · charge ${c.charge} · mult ${c.multiplicity}</div>
        ${c.message ? (
          c.state === "failed"
            ? `<div class="qerror">⚠ ${escapeHtml(c.message)}</div>`
            : `<div class="qsteps" style="color:var(--muted-foreground)">${escapeHtml(c.message)}</div>`
        ) : ""}
      </div>
      <span class="qstate ${c.state}">${c.state}</span>
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
    toast("Only pending, failed, or cancelled calculations can be reordered.");
    return;
  }
  try {
    await bridge.reorder_calc(from, to);
    await refreshQueue();
  } catch (e) { toast("Reorder failed."); }
}
async function removeCalc(i) {
  const c = queue[i];
  if (!c) return;
  if (c.state === "running") { toast("Cannot remove a running calculation."); return; }
  if (!confirm(`Remove "${c.name}" from the queue?`)) return;
  await bridge.remove_calc(c.name);
  delete localCalcs[c.name];
  await refreshQueue();
}
async function clearQueue() {
  if (isRunning()) return;
  const res = JSON.parse(await bridge.clear_queue());
  if (!res.ok) { appendLog(res.error || "Could not clear queue.", "warn"); return; }
  for (const k of Object.keys(localCalcs)) delete localCalcs[k];
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
    const close = (v) => { overlay.style.display = "none"; resolve(v); };
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

async function runQueue() {
  if (_running) return;
  if (!queue.length) { appendLog("No calculations queued.", "warn"); return; }
  if (!settings.orca_valid) { alert("ORCA path is not set. Go to Settings."); switchTab("settings"); return; }

  // Check whether any queued calc would overwrite an existing result on disk.
  let skipNames = [];
  try {
    const chk = JSON.parse(await bridge.check_overwrite_conflicts());
    if (chk.ok && chk.conflicts && chk.conflicts.length) {
      const list = `<div class="names">${chk.conflicts.join(", ")}</div>`;
      const choice = await showModal(
        "Existing results found",
        `${chk.conflicts.length} calculation(s) already have results saved on disk:<br><br>${list}<br>` +
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

  appendLog("--- starting queue ---", "info");
  const res = JSON.parse(await bridge.run_queue(JSON.stringify(skipNames)));
  if (!res.ok) {
    appendLog("Could not start: " + res.error, "err");
  } else {
    _running = true; setRunUI(true);
  }
}
async function cancelQueue() { await bridge.cancel_queue(); }
function setRunUI(running) {
  const rb = document.getElementById("run-btn");
  const cb = document.getElementById("cancel-btn");
  if (rb) rb.disabled = running;
  if (cb) cb.disabled = !running;
}

// queue/log/state changes are now reflected by pollTick() (shared store),
// so the old Qt-signal handlers (onCalcUpdate/onQueueFinished) are gone.
// We still pull result summaries when a calc finishes, lazily:
async function maybeFetchResult(name, outputPath) {
  if (!outputPath || calcResults[name]) return;
  try {
    const raw = await bridge.parse_out_path(outputPath);
    const data = JSON.parse(raw);
    if (data && data.summary) {
      calcResults[name] = data.summary;
      if (data.transitions && data.transitions.length) _resultExtras[name] = { transitions: data.transitions };
      if (data.nmr && data.nmr.length) _resultExtras[name] = Object.assign(_resultExtras[name] || {}, { nmr: data.nmr });
      if (data.frequencies && data.frequencies.length) _resultExtras[name] = Object.assign(_resultExtras[name] || {}, { frequencies: data.frequencies, n_imaginary: data.n_imaginary || 0 });
      if (data.neb_path && data.neb_path.length) _resultExtras[name] = Object.assign(_resultExtras[name] || {}, { neb_path: data.neb_path });
      refreshResultSelect();
    }
  } catch (e) { /* parsing failed; skip */ }
}

// ---------- log ----------
let _scfTracker = SCFGraph ? new SCFGraph.SCFTracker() : null;
let _geoTracker = SCFGraph ? new SCFGraph.GeoTracker() : null;
let _logMode = "raw";
let _graphKind = "auto";   // "auto" | "scf" | "geo"  (sub-mode inside graph)
function currentRunningScf() {
  const r = (queue || []).find(c => c.state === "running");
  return r ? (r.scf_convergence || "TightSCF") : "TightSCF";
}
function runningIsOpt() {
  const r = (queue || []).find(c => c.state === "running");
  return r ? (r.kind === "opt" || r.kind === "ts_opt") : false;
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
}
function setGraphKind(k) { _graphKind = k; renderSCFPanel(); }
function renderSCFPanel() {
  if (!SCFGraph) return;
  const panel = document.getElementById("scf-panel");
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
  let body;
  if (kind === "geo" && _geoTracker && _geoTracker.hasData()) {
    body = `<div class="graph-summary">${SCFGraph.renderGeoProgress(_geoTracker)}</div>` +
           `<div class="graph-divider"></div>` +
           `<div class="graph-plot">${SCFGraph.renderGeoGraph(_geoTracker, { width: 560, height: 220 })}</div>`;
  } else {
    const scf = currentRunningScf();
    body = `<div class="graph-summary">${SCFGraph.renderSCFProgress(_scfTracker, scf)}</div>` +
           `<div class="graph-divider"></div>` +
           `<div class="graph-plot">${SCFGraph.renderSCFGraph(_scfTracker, scf, { width: 560, height: 220 })}</div>`;
  }
  panel.innerHTML = head + body;
  _scfDirty = false;
}
// matches the queue's per-calc start marker, e.g. "[opt1] (opt) running ORCA..."
const _CALC_START_RE = /^\[.+\]\s*\(.+\)\s*running ORCA/i;
function appendLog(msg, level) {
  // a new calculation is starting: reset the convergence trackers so the graph
  // reflects the new job (and not the previous opt/freq)
  if (_CALC_START_RE.test(msg) && SCFGraph) {
    _scfTracker = new SCFGraph.SCFTracker();
    _geoTracker = new SCFGraph.GeoTracker();
    _graphKind = "auto";
    _scfDirty = true;
  }
  const box = document.getElementById("log");
  const div = document.createElement("div");
  div.className = "log-line log-" + (level || "info");
  div.textContent = msg;
  box.appendChild(div);
  // trim old lines so the DOM doesn't grow without bound (this was the lag)
  while (box.childElementCount > _LOG_MAX_LINES) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
  // feed both trackers; mark dirty (redraw is throttled in pollTick)
  let changed = false;
  if (_scfTracker && _scfTracker.push(msg)) changed = true;
  if (_geoTracker && _geoTracker.push(msg)) changed = true;
  if (changed) _scfDirty = true;
}
function clearLog() {
  document.getElementById("log").innerHTML = "";
  if (SCFGraph) {
    _scfTracker = new SCFGraph.SCFTracker();
    _geoTracker = new SCFGraph.GeoTracker();
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
  const rows = calcResults[name];
  if (rows) renderSummary(rows);
  const extras = _resultExtras[name];
  if (extras) {
    if (extras.frequencies && extras.frequencies.length) renderFreqSpectrum(extras.frequencies, extras.n_imaginary);
    if (extras.neb_path && extras.neb_path.length) renderNebPath(extras.neb_path);
    if (extras.transitions && extras.transitions.length) renderSpectrum(extras.transitions);
    if (extras.nmr && extras.nmr.length) renderNmr(extras.nmr);
  }
}
function renderSummary(rows) {
  const body = document.getElementById("result-body");
  let html = `<div class="kv">`;
  for (const [k, v] of rows) {
    let cls = "";
    if (/imaginary/i.test(k) && !/^0$/.test(String(v))) cls = "warn";
    if (/ABNORMAL|NOT converged/i.test(String(v))) cls = "err";
    if (/converged|Normal/i.test(String(v))) cls = "ok";
    html += `<div class="k">${escapeHtml(k)}</div><div class="v ${cls}">${escapeHtml(String(v))}</div>`;
  }
  html += `</div>`;
  body.innerHTML = html;
}
async function openOutFile() {
  const raw = await bridge.parse_out_file();
  let data; try { data = JSON.parse(raw); } catch { return; }
  if (!data.summary) { appendLog("Could not parse file.", "err"); return; }
  renderSummary(data.summary);
  if (data.frequencies && data.frequencies.length) renderFreqSpectrum(data.frequencies, data.n_imaginary);
  if (data.neb_path && data.neb_path.length) renderNebPath(data.neb_path);
  if (data.transitions && data.transitions.length) renderSpectrum(data.transitions);
  if (data.nmr && data.nmr.length) renderNmr(data.nmr);
  switchTab("results");
}
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
      <text x="${pad}" y="${H-8}" fill="var(--muted-foreground)" font-size="10">${minNm.toFixed(0)} nm</text>
      <text x="${W-pad-30}" y="${H-8}" fill="var(--muted-foreground)" font-size="10">${maxNm.toFixed(0)} nm</text>
    </svg>`;
}

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
    : `<div class="hint" style="margin-top:6px">No imaginary modes — structure is a local minimum.</div>`;

  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">Vibrational frequencies (${real.length} real${imag.length?`, <span style="color:var(--err)">${imag.length} imaginary</span>`:""})</div>
    <svg class="spectrum" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      <line x1="${padL}" y1="${baseY}" x2="${W-padR}" y2="${baseY}" stroke="var(--border)"/>
      ${sticks}
      <text x="${x(minF).toFixed(1)}" y="${H-8}" fill="var(--muted-foreground)" font-size="10">${minF.toFixed(0)}</text>
      <text x="${(W-padR-50)}" y="${H-8}" fill="var(--muted-foreground)" font-size="10">${maxF.toFixed(0)} cm⁻¹</text>
    </svg>
    ${warn}`;
}

function renderNmr(nmr) {
  const body = document.getElementById("result-body");
  let rows = "";
  for (const n of nmr) {
    rows += `<tr><td>${n.idx} ${escapeHtml(n.el)}</td><td>${n.iso.toFixed(3)}</td><td>${n.aniso.toFixed(3)}</td></tr>`;
  }
  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">NMR chemical shielding (ppm)</div>
    <table class="data">
      <thead><tr><th>Nucleus</th><th>Isotropic</th><th>Anisotropy</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="hint">Absolute shieldings — subtract from a reference (e.g. TMS) to get chemical shifts.</div>`;
}

function renderNebPath(path) {
  const body = document.getElementById("result-body");
  if (!path || !path.length) return;

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
  // reactant / product labels (first and last)
  const first = path[0], last = path[n - 1];
  const reactLbl = `<text x="${x(0).toFixed(1)}" y="${H-padB+16}" fill="var(--muted-foreground)" font-size="10" text-anchor="middle">reactant</text>`;
  const prodLbl  = `<text x="${x(n-1).toFixed(1)}" y="${H-padB+16}" fill="var(--muted-foreground)" font-size="10" text-anchor="middle">product</text>`;
  // zero baseline
  const zeroY = y(0).toFixed(1);

  const ts = path.find(p => p.is_ts);
  const barrier = ts ? ts.de_kcal : Math.max(...de);
  const dErxn = last.de_kcal - first.de_kcal;

  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">NEB-TS reaction path (${n} images)</div>
    <svg class="spectrum" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      <line x1="${padL}" y1="${zeroY}" x2="${W-padR}" y2="${zeroY}" stroke="var(--border)" stroke-dasharray="3 3"/>
      <polyline points="${pts}" fill="none" stroke="var(--muted-foreground)" stroke-width="1.5"/>
      ${dots}
      <text x="14" y="${padT+4}" fill="var(--muted-foreground)" font-size="10">${hi.toFixed(1)}</text>
      <text x="14" y="${(H-padB).toFixed(1)}" fill="var(--muted-foreground)" font-size="10">${lo.toFixed(1)}</text>
      <text x="14" y="${H/2}" fill="var(--muted-foreground)" font-size="10" transform="rotate(-90 14 ${H/2})" text-anchor="middle">ΔE (kcal/mol)</text>
      ${reactLbl}${prodLbl}
    </svg>
    <div class="hint">Forward barrier ≈ <b>${barrier.toFixed(1)} kcal/mol</b>; reaction energy ΔE ≈ <b>${dErxn.toFixed(1)} kcal/mol</b>. Energies are from the NEB path summary (electronic, not free energies).</div>`;
}

// ---- free energy profile (Results tab) ----
let _fepPoints = [];   // cached [{name, gibbs_eh, ...}] in queue order

async function loadFreeEnergyProfile() {
  try {
    const res = JSON.parse(await bridge.get_free_energy_profile());
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
    body.innerHTML = `<div class="hint">No finished frequency calculations yet. Run jobs with FREQ to build a profile.</div>`;
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
  // zero baseline
  svg += `<line x1="${padL}" y1="${y(0).toFixed(1)}" x2="${W-padR}" y2="${y(0).toFixed(1)}" stroke="var(--border)" stroke-dasharray="4 4"/>`;
  svg += `<text x="${padL-8}" y="${y(0).toFixed(1)}" text-anchor="end" dominant-baseline="middle" class="scf-axis" style="font-size:10px">0</text>`;
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
  svg += `<text x="14" y="${(padT+(H-padT-padB)/2).toFixed(1)}" text-anchor="middle" transform="rotate(-90 14 ${(padT+(H-padT-padB)/2).toFixed(1)})" class="scf-axis-title" style="font-size:11px">ΔG (${unitLabel})</text>`;

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
