// @ts-check
// Log tab + SCF/opt/CREST graph panel (the per-job trackers, renderSCFPanel,
// setLogMode, appendLog, clearLog) — split out of app.js. Plain global script,
// loaded after scf_graph.js (the trackers come from window.SCFGraph) and
// before app.js.

// ---------- per-job convergence trackers ----------
// Several calculations can run at once, and their tailed output interleaves in
// ONE log buffer — so the trackers are per calculation, keyed by the name the
// backend tags each line with (LogLine.calc). The panel still shows one job at
// a time: _jobPick when the user chose one, otherwise the newest running job.
/** @typedef {{scf: any, geo: any, freq: any, tddft: any, crest: any, iterTimes: number[]}} JobTrackers */
/** @type {Map<string, JobTrackers>} */
const _jobs = new Map();
let _jobPick = "";              // explicit job choice ("" = follow the run)
/** @type {JobTrackers|null} */
let _emptyJob = null;           // stand-in bundle when no job has produced data
let _seededGraph = new Set();   // calc names whose graph is already sourced (live stream or disk-seed)
const _OPT_KINDS = ["opt", "ts_opt", "opt_freq", "ts_opt_freq"];

function _newJobTrackers() {
  return SCFGraph ? {
    scf: new SCFGraph.SCFTracker(),
    geo: new SCFGraph.GeoTracker(),
    freq: new SCFGraph.FreqTracker(),
    tddft: new SCFGraph.TddftTracker(),
    crest: new SCFGraph.CrestTracker(),
    // arrival times (ms) of recent live SCF-iteration lines, for the s/cycle pace
    iterTimes: /** @type {number[]} */ ([]),
  } : null;
}
/** The tracker bundle for `name`, created on first sight. An empty name (an
 *  engine-level line, or no job at all) gets a shared blank bundle that is
 *  never listed as a job.
 *  @param {string} name @returns {JobTrackers|null} */
function jobTrackers(name) {
  if (!SCFGraph) return null;
  if (!name) {
    if (!_emptyJob) _emptyJob = _newJobTrackers();
    return _emptyJob;
  }
  let b = _jobs.get(name);
  if (!b) { b = /** @type {JobTrackers} */ (_newJobTrackers()); _jobs.set(name, b); }
  return b;
}
/** Name of the job the graph panel is showing: the explicit pick while it still
 *  exists, else the most recently started running job, else the newest job we
 *  have data for. (Map keeps insertion order, so "newest" is the last key.) */
function shownJob() {
  if (_jobPick && _jobs.has(_jobPick)) return _jobPick;
  const running = (queue || []).filter(c => c.state === "running").map(c => c.name);
  for (let i = running.length - 1; i >= 0; i--) {
    if (_jobs.has(running[i])) return running[i];
  }
  const names = [..._jobs.keys()];
  return names.length ? names[names.length - 1] : "";
}
/** @returns {JobTrackers|null} */
function curTrackers() { return jobTrackers(shownJob()); }
/** The queue row for the job on screen (null when it has left the queue). */
function shownCalc() {
  const name = shownJob();
  return name ? ((queue || []).find(c => c.name === name) || null) : null;
}

// Average wall-clock seconds per SCF iteration over the recent window. Uses
// (last - first)/(n-1) so it stays accurate even though lines arrive batched
// per 1s poll; null until there's enough of a time span to be meaningful.
function scfSecPerIter() {
  const b = curTrackers();
  const t = b ? b.iterTimes : [];
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
  const r = shownCalc();
  return r ? (r.scf_convergence || "TightSCF") : "TightSCF";
}
function runningIsOpt() {
  const r = shownCalc();
  return r ? _OPT_KINDS.includes(r.kind) : false;
}
// which graph to actually show: explicit choice, else geo if we have opt data
function effectiveGraphKind() {
  if (_graphKind === "scf") return "scf";
  if (_graphKind === "geo") return "geo";
  // auto: prefer geometry when the run is an opt and we have steps
  const b = curTrackers();
  if (b && b.geo.hasData() && runningIsOpt()) return "geo";
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
/** Show another job's graph. "" follows the run again. @param {string} name */
function setGraphJob(name) {
  _jobPick = name || "";
  _graphKind = "auto";        // the new job may be a different kind of run
  renderSCFPanel();
}
function renderSCFPanel() {
  if (!SCFGraph) return;
  const panel = document.getElementById("scf-panel");
  const b = curTrackers();
  if (!b) return;
  // With more than one job in flight the panel has to say WHOSE curve this is,
  // and let the user follow the other one.
  const picker = jobPickerHtml();
  // A phase-chain run — a CREST conformer search, or the frequency / TD-DFT
  // pipeline — has no meaningful convergence curve below it: its stepper fills
  // the whole panel, no secondary graph. (CREST wins, then freq, then TD-DFT.)
  // The stepper's rail follows the window height: pass the space left below
  // the panel top (minus panel padding/border + the 16px window gutter); the
  // renderer falls back to its compact strip when that can't fit the rows.
  let phaseHtml = "";
  const phaseOpts = {
    height: Math.max(window.innerHeight - panel.getBoundingClientRect().top - 46
                     - (picker ? 34 : 0), 0),
  };
  if (b.crest.hasData()) phaseHtml = SCFGraph.renderCrestProgress(b.crest, phaseOpts);
  else if (b.freq.hasData()) phaseHtml = SCFGraph.renderFreqProgress(b.freq, phaseOpts);
  else if (b.tddft.hasData()) phaseHtml = SCFGraph.renderTddftProgress(b.tddft, phaseOpts);
  if (phaseHtml) {
    panel.innerHTML = picker + phaseHtml;
    _scfDirty = false;
    return;
  }
  const kind = effectiveGraphKind();
  // sub-toggle (SCF vs geometry) — only meaningful for opt runs
  const showToggle = b.geo.hasData();
  let head = "";
  if (showToggle) {
    head = `<div class="graph-subtoggle">
      <button class="${kind === 'geo' ? 'active' : ''}" onclick="setGraphKind('geo')">Optimization</button>
      <button class="${kind === 'scf' ? 'active' : ''}" onclick="setGraphKind('scf')">Current SCF</button>
    </div>`;
  }
  const isGeo = (kind === "geo" && b.geo.hasData());
  let body;
  if (isGeo) {
    body = `<div class="graph-summary">${SCFGraph.renderGeoProgress(b.geo, scfPaceText())}</div>` +
           `<div class="graph-divider"></div>` +
           `<div class="graph-plot"></div>`;
  } else {
    body = `<div class="graph-summary">${SCFGraph.renderSCFProgress(b.scf, currentRunningScf(), scfPaceText())}</div>` +
           `<div class="graph-divider"></div>` +
           `<div class="graph-plot"></div>`;
  }
  panel.innerHTML = picker + head + body;
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
      ? SCFGraph.renderGeoGraph(b.geo, gopts)
      : SCFGraph.renderSCFGraph(b.scf, currentRunningScf(), gopts);
  }
  _scfDirty = false;
}
/** The "showing: <job>" selector above the graph. Empty while only one job has
 *  ever produced data — a single-job run should look exactly as it always did. */
function jobPickerHtml() {
  const names = [..._jobs.keys()];
  if (names.length < 2) return "";
  const cur = shownJob();
  const opts = names.map(n =>
    `<option value="${escapeHtml(n)}"${n === cur ? " selected" : ""}>${escapeHtml(n)}</option>`
  ).join("");
  return `<div class="graph-jobpick">
    <span class="hint" style="margin:0">Showing</span>
    <select onchange="setGraphJob(this.value)">${opts}</select>
  </div>`;
}
// the graph is sized to the viewport, so it must follow window resizes
window.addEventListener("resize", () => {
  if (_logMode === "graph") renderSCFPanel();
});
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

// ---------- raw-log job filter ----------
// Interleaved output is readable line by line (each line is tagged), but a
// long parallel run still needs "show me only this job".
let _logFilter = "";            // "" = every line
/** @param {string} name */
function setLogFilter(name) {
  _logFilter = name || "";
  applyLogFilter();
}
/** @param {Element} el */
function _lineHidden(el) {
  if (!_logFilter) return false;
  return (el.getAttribute("data-calc") || "") !== _logFilter;
}
function applyLogFilter() {
  const box = document.getElementById("log");
  if (!box) return;
  for (const el of Array.from(box.children)) {
    /** @type {any} */ (el).hidden = _lineHidden(el);
  }
  updateLogJump();
}
/** Keep the raw-log filter's options in step with the jobs that have produced
 *  output. Called from the poll tick; a no-op unless the set changed. */
function refreshLogFilterOptions() {
  const sel = document.getElementById("log-filter");
  if (!sel) return;
  const names = [..._jobs.keys()];
  const wanted = "|" + names.join("|");
  if (sel.dataset.names === wanted) return;
  sel.dataset.names = wanted;
  sel.innerHTML = `<option value="">All jobs</option>` +
    names.map(n => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join("");
  // a filtered-on job that left the queue falls back to showing everything
  if (_logFilter && !names.includes(_logFilter)) _logFilter = "";
  sel.value = _logFilter;
  sel.hidden = names.length < 2;
}

// every backend's per-calc start marker: ORCA "running ORCA…", CREST "running
// CREST…", MLIP "optimizing with…" / "single-point energy with…". Matching them
// resets that job's trackers so a re-run never inherits the previous curve.
const _CALC_START_RE = /^\[(.+?)\]\s*\([^)]*\)\s*(?:running ORCA|running CREST|optimizing|single-point|frequencies)/i;
/** @param {string} msg @param {string} level @param {string} [calc] owning calculation ("" = engine-level) */
function appendLog(msg, level, calc) {
  const name = calc || "";
  // A name we have not seen before adds a row to the filter/picker lists. Doing
  // it here rather than only on the poll tick keeps the controls correct no
  // matter how a line arrives.
  const newJob = !!name && !_jobs.has(name);
  if (SCFGraph && name && _CALC_START_RE.test(msg)) {
    // a new run of this calculation owns its graph from here
    _jobs.set(name, /** @type {JobTrackers} */ (_newJobTrackers()));
    _graphKind = "auto";
    _scfDirty = true;
    // this session's live stream owns this calc's graph from the start, so the
    // reattach disk-seed (maybeSeedGraph) must not also rebuild it
    _seededGraph.add(name);
  }
  const box = document.getElementById("log");
  // only auto-follow if the user is already at (near) the bottom — don't yank
  // them down while they've scrolled up to read earlier output
  const stick = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  const div = document.createElement("div");
  div.className = "log-line log-" + (level || "info");
  if (name) div.setAttribute("data-calc", name);
  div.textContent = msg;
  div.hidden = _lineHidden(div);
  box.appendChild(div);
  // trim old lines so the DOM doesn't grow without bound (this was the lag)
  while (box.childElementCount > _LOG_MAX_LINES) box.removeChild(box.firstChild);
  if (stick) box.scrollTop = box.scrollHeight;
  updateLogJump();
  // feed this job's trackers; mark dirty (redraw is throttled in pollTick)
  const b = name ? jobTrackers(name) : null;
  if (newJob) refreshLogFilterOptions();
  if (b) {
    let changed = false;
    if (b.scf.push(msg)) changed = true;
    if (b.geo.push(msg)) changed = true;
    if (b.freq.push(msg)) changed = true;
    if (b.tddft.push(msg)) changed = true;
    if (b.crest.push(msg)) changed = true;
    if (changed) _scfDirty = true;
    // record SCF-iteration arrival times for the s/cycle pace (live lines only;
    // disk-seeded lines bypass appendLog so they don't skew the timing)
    if (SCFGraph && SCFGraph.isScfIter(msg)) {
      b.iterTimes.push(Date.now());
      if (b.iterTimes.length > 40) b.iterTimes.shift();
    }
  }
}
/** Drop the tracker bundles of calculations that have left the queue.
 *  Without this the Map grows for the whole session -- five trackers and their
 *  point arrays per calculation ever seen -- and the job picker / log filter
 *  list calcs that were removed hours ago instead of the run's.
 *  @param {Set<string>} liveNames names still in the queue */
function pruneJobTrackers(liveNames) {
  let dropped = false;
  for (const name of [..._jobs.keys()]) {
    if (liveNames.has(name)) continue;
    _jobs.delete(name);
    _seededGraph.delete(name);   // a name reused later must be able to re-seed
    dropped = true;
  }
  if (!dropped) return;
  if (_jobPick && !_jobs.has(_jobPick)) _jobPick = "";
  refreshLogFilterOptions();
  if (_logMode === "graph") _scfDirty = true;
}
function clearLog() {
  document.getElementById("log").innerHTML = "";
  updateLogJump();
  const paceEl = document.getElementById("scf-pace");
  if (paceEl) paceEl.textContent = "";
  if (SCFGraph) {
    _jobs.clear();
    _emptyJob = null;
    _jobPick = "";
    _seededGraph.clear();   // allow every calc's graph to re-seed
    refreshLogFilterOptions();
    if (_logMode === "graph") renderSCFPanel();
  }
}
