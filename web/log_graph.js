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
/** @typedef {{scf: any, geo: any, freq: any, tddft: any, crest: any}} JobTrackers */
/** @type {Map<string, JobTrackers>} */
const _jobs = new Map();
let _jobPick = "";              // explicit job choice ("" = follow the run)
/** @type {JobTrackers|null} */
let _emptyJob = null;           // stand-in bundle when no job has produced data
let _seededGraph = new Set();   // calc names whose graph is already sourced (live stream or disk-seed)
/** Calcs whose EARLIER output should be pulled back into the Raw log: a run
 *  reattached at startup streams only what ORCA writes from that moment, so
 *  the Raw tab opened empty on a job that had been going for hours. Filled
 *  from the engine's own reattach line (below) rather than from queue state —
 *  that line is the exact signal, and it can never fire for a job THIS
 *  session started, whose log is complete already. @type {Set<string>} */
const _restoreWanted = new Set();
const _OPT_KINDS = ["opt", "ts_opt", "opt_freq", "ts_opt_freq"];
/** ORCA-backend kinds: everything that is not the MLIP or CREST pipeline.
 *  Their run folder holds a real ORCA .out, so every tracker the Graph tab
 *  draws from — SCF, geometry, frequencies, TD-DFT — can be replayed out of
 *  it. @param {string} kind */
function isOrcaKind(kind) {
  const k = kind || "";
  return !k.startsWith("mlip") && !k.startsWith("crest");
}

function _newJobTrackers() {
  return SCFGraph ? {
    scf: new SCFGraph.SCFTracker(),
    geo: new SCFGraph.GeoTracker(),
    freq: new SCFGraph.FreqTracker(),
    tddft: new SCFGraph.TddftTracker(),
    crest: new SCFGraph.CrestTracker(),
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
    if (jobHasGraph(running[i])) return running[i];
  }
  const withData = graphJobs();
  if (withData.length) return withData[withData.length - 1];
  const names = [..._jobs.keys()];
  return names.length ? names[names.length - 1] : "";
}
/** @returns {JobTrackers|null} */
function curTrackers() { return jobTrackers(shownJob()); }
/** True when this job has produced something the graph panel can draw. A calc
 *  that only logged a status line ("already done - skipping.") has a bundle but
 *  nothing to show, and listing it would offer an empty graph.
 *  @param {string} name */
function jobHasGraph(name) {
  const b = _jobs.get(name);
  return !!b && (b.scf.hasData?.() || b.scf.points.length > 0 || b.geo.hasData()
                 || b.freq.hasData() || b.tddft.hasData() || b.crest.hasData());
}
/** Jobs worth offering in the graph picker, in the order they started. */
function graphJobs() { return [..._jobs.keys()].filter(jobHasGraph); }
/** The queue row for the job on screen (null when it has left the queue). */
function shownCalc() {
  const name = shownJob();
  return name ? ((queue || []).find(c => c.name === name) || null) : null;
}

let _logMode = "raw";
let _graphKind = "auto";   // "auto" | "scf" | "geo" | "phase"  (view inside the graph panel)
/** The phase chain this job ran, if any, as a view the sub-toggle can offer.
 *  A run has at most one (CREST wins, then frequencies, then TD-DFT).
 *  @param {JobTrackers} b */
function phaseView(b) {
  if (b.crest.hasData()) return { label: "Conformers", tracker: b.crest, render: SCFGraph.renderCrestProgress };
  if (b.freq.hasData()) return { label: "Frequencies", tracker: b.freq, render: SCFGraph.renderFreqProgress };
  if (b.tddft.hasData()) return { label: "TD-DFT", tracker: b.tddft, render: SCFGraph.renderTddftProgress };
  return null;
}
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
  const b = curTrackers();
  const phase = b ? phaseView(b) : null;
  // an explicit pick is honoured while that view still exists — a pre-step
  // Hessian's chain is cleared when the next optimization cycle starts, and
  // the panel must fall back rather than render nothing
  if (_graphKind === "phase" && phase) return "phase";
  if (_graphKind === "scf") return "scf";
  if (_graphKind === "geo") return "geo";
  // auto: the phase chain is the live edge of the run (an opt_freq is on its
  // frequencies by the time one exists), then geometry for an opt, then SCF
  if (phase) return "phase";
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
  const kind = effectiveGraphKind();
  const phase = phaseView(b);
  // Sub-toggle: the views THIS job actually has, in pipeline order. A phase
  // chain has no meaningful convergence curve *below* it (its stepper fills
  // the panel), but a two-stage run has both — an opt_freq optimizes and then
  // runs frequencies, and losing the optimization curve the moment the
  // Hessian starts hid half the run. So the chain becomes a third view
  // instead of taking the panel over. A job with only a chain (plain freq,
  // CREST, TD-DFT) has nothing to toggle between, and shows no strip.
  const views = [];
  if (b.geo.hasData()) {
    views.push({ k: "geo", label: "Optimization" });
    views.push({ k: "scf", label: "Current SCF" });   // the pair an opt run always has
  }
  if (phase) views.push({ k: "phase", label: phase.label });
  const head = views.length > 1
    ? `<div class="graph-subtoggle">` + views.map(v =>
        `<button class="${kind === v.k ? "active" : ""}" onclick="setGraphKind('${v.k}')">${v.label}</button>`
      ).join("") + `</div>`
    : "";
  // The stepper's rail follows the window height: pass the space left below
  // the panel top (minus panel padding/border + the 16px window gutter, and
  // the strips above it); the renderer falls back to its compact strip when
  // that can't fit the rows.
  if (kind === "phase" && phase) {
    const phaseOpts = {
      height: Math.max(window.innerHeight - panel.getBoundingClientRect().top - 46
                       - (picker ? 34 : 0) - (head ? 41 : 0), 0),   // strips: 34 / 41 tall
    };
    panel.innerHTML = picker + head + phase.render(phase.tracker, phaseOpts);
    _scfDirty = false;
    return;
  }
  const isGeo = (kind === "geo" && b.geo.hasData());
  let body;
  if (isGeo) {
    body = `<div class="graph-summary">${SCFGraph.renderGeoProgress(b.geo)}</div>` +
           `<div class="graph-divider"></div>` +
           `<div class="graph-plot"></div>`;
  } else {
    body = `<div class="graph-summary">${SCFGraph.renderSCFProgress(b.scf, currentRunningScf())}</div>` +
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
/** The "Showing <job>" picker above the graph: one button per job, so switching
 *  is a single click. Empty while only one job has ever produced data — a
 *  single-job run should look exactly as it always did.
 *
 *  The job name goes in `data-job`, never in an inline handler: a calculation
 *  name may legally contain a quote (only path-dangerous characters are
 *  rejected), which would break out of an onclick attribute. */
function jobPickerHtml() {
  const names = graphJobs();
  if (names.length < 2) return "";
  const cur = shownJob();
  const btns = names.map(n =>
    `<button data-job="${escapeHtml(n)}"${n === cur ? ' class="active"' : ""}>${escapeHtml(n)}</button>`
  ).join("");
  return `<div class="graph-jobpick">
    <span class="hint" style="margin:0">Showing</span>
    <div class="graph-subtoggle">${btns}</div>
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
  refreshLogFilterOptions();   // move the active state onto the clicked button
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
/** Keep the raw-log filter in step with the jobs that have produced output:
 *  an "All" button plus one per job. Called from the poll tick and whenever a
 *  new job first appears; a no-op unless the set or the selection changed. */
function refreshLogFilterOptions() {
  const box = document.getElementById("log-filter");
  if (!box) return;
  const names = [..._jobs.keys()];
  // a filtered-on job that left the queue falls back to showing everything
  if (_logFilter && !names.includes(_logFilter)) {
    _logFilter = "";
    applyLogFilter();
  }
  const wanted = _logFilter + "|" + names.join("|");
  if (box.dataset.state === wanted) return;
  box.dataset.state = wanted;
  const btn = (value, label) =>
    `<button data-job="${escapeHtml(value)}"` +
    `${value === _logFilter ? ' class="active"' : ""}>${escapeHtml(label)}</button>`;
  box.innerHTML = btn("", "All") + names.map(n => btn(n, n)).join("");
  box.hidden = names.length < 2;
}

// One delegated handler for both button strips. Delegation (rather than an
// inline onclick) keeps the job name in a data attribute, where a quote in a
// calculation name cannot break out of it.
document.addEventListener("click", (e) => {
  const btn = /** @type {Element} */ (e.target)?.closest?.("[data-job]");
  if (!btn) return;
  const job = btn.getAttribute("data-job") || "";
  if (btn.closest("#log-filter")) setLogFilter(job);
  else if (btn.closest(".graph-jobpick")) setGraphJob(job);
});

// every backend's per-calc start marker: ORCA "running ORCA…", CREST "running
// CREST…", MLIP "optimizing with…" / "single-point energy with…". Matching them
// resets that job's trackers so a re-run never inherits the previous curve.
const _CALC_START_RE = /^\[(.+?)\]\s*\([^)]*\)\s*(?:running ORCA|running CREST|optimizing|single-point|frequencies)/i;
// the engine's reattach line (core/queue.py): this job was already running
// when ORCAdesk opened, so its earlier output exists only on disk.
const _CALC_REATTACH_RE = /^\[(.+?)\]\s*reattaching to (?:ORCA|CREST) still running/i;
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
    _restoreWanted.delete(name);   // nothing to restore: we have it all
  }
  if (name && _CALC_REATTACH_RE.test(msg)) _restoreWanted.add(name);
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
  }
}
/** Put a reattached run's earlier output back at the TOP of the Raw log,
 *  fenced by two markers so it can never be mistaken for live output. The
 *  lines come straight from the .out (bridge.get_output_tail) and are NOT fed
 *  to the trackers: the graph seed already rebuilds those from the whole file,
 *  and pushing the same lines twice would double-count them.
 *  @param {string} name @param {string[]} lines
 *  @param {string} file basename of the output file, for the marker
 *  @param {boolean} truncated the file holds more than these lines */
function insertRestoredLog(name, lines, file, truncated) {
  const box = document.getElementById("log");
  if (!box || !lines || !lines.length) return false;
  const frag = document.createDocumentFragment();
  const add = (text, cls) => {
    const d = document.createElement("div");
    d.className = "log-line " + cls;
    if (name) d.setAttribute("data-calc", name);   // the job filter covers history too
    d.textContent = text;
    d.hidden = _lineHidden(d);
    frag.appendChild(d);
  };
  const what = (truncated ? "last " : "") + lines.length + " line" + (lines.length === 1 ? "" : "s");
  add(`── restored history · ${file || name + ".out"} · ${what} ──`, "log-mark");
  for (const ln of lines) add(ln, "log-restored");
  add("── live output continues below ──", "log-mark");
  const stick = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  box.insertBefore(frag, box.firstChild);
  // same cap as appendLog, and it trims from the top — so restored history is
  // the first thing dropped once the live stream fills the box again
  while (box.childElementCount > _LOG_MAX_LINES) box.removeChild(box.firstChild);
  if (stick) box.scrollTop = box.scrollHeight;
  updateLogJump();
  return true;
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
    _restoreWanted.delete(name);
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
  if (SCFGraph) {
    _jobs.clear();
    _emptyJob = null;
    _jobPick = "";
    _seededGraph.clear();   // allow every calc's graph to re-seed
    _restoreWanted.clear();   // a cleared log stays cleared (the graph is a view, this is the text)
    refreshLogFilterOptions();
    if (_logMode === "graph") renderSCFPanel();
  }
}
