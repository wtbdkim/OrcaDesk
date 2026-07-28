// @ts-check
// Log tab + SCF/opt/CREST graph panel (the tracker globals, renderSCFPanel,
// setLogMode, appendLog, clearLog) — split out of app.js. Plain global script,
// loaded after scf_graph.js (top-level tracker init reads window.SCFGraph) and
// before app.js.

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
