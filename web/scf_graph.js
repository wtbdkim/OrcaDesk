// @ts-check
/* ============================================================
   scf_graph.js — shared by the desktop app (web/) and the mobile
   PWA (web_mobile/). Parses streaming ORCA log lines for SCF
   convergence, tracks progress vs the start point, and renders a
   small SVG convergence graph + a progress bar.

   Pure-ish: SCFTracker has no DOM deps and is unit-testable in node.
   renderSCFGraph()/renderSCFProgress() build HTML/SVG strings.
   ============================================================ */
(function (/** @type {any} */ global) {
  "use strict";

  // opt-ETA prediction mode: "conservative" (strict gating) or "eager"
  // (looser gating, predicts earlier and holds the estimate). Set from the
  // app's Settings; defaults to conservative.
  let _etaMode = "conservative";
  function setEtaMode(m) { if (m === "eager" || m === "conservative") _etaMode = m; }

  // optimization-graph mode: "all5" (all five convergence criteria as
  // value/tolerance ratios sharing one goal line at 1) or "maxgrad" (just MAX
  // gradient on an absolute axis — the original view). Set from the app Settings.
  let _geoMode = "all5";
  function setGeoMode(m) { if (m === "all5" || m === "maxgrad") _geoMode = m; }

  // SCF convergence setting -> approximate Delta-E target (Eh).
  // Used to place the "goal" line and compute progress.
  const SCF_TARGETS = {
    SloppySCF: 1e-5,
    LooseSCF: 3e-6,
    NormalSCF: 1e-6,
    StrongSCF: 1e-7,
    TightSCF: 1e-8,
    VeryTightSCF: 1e-9,
    ExtremeSCF: 1e-11,
  };
  function targetFor(scfConv) {
    return SCF_TARGETS[scfConv] || 1e-8; // default = TightSCF-ish
  }

  // A single SCF iteration row in ORCA looks like:
  //   "    2    -232.1151660682643012    -8.23e-02  4.97e-03  2.50e-02 ..."
  // i.e. <int iter> <float energy> <sci Delta-E> <more...>
  // (works for both the DIIS and SOSCF sub-tables).
  const ITER_RE = /^\s*(\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+[eE][+-]?\d+)/;
  const GEO_RE = /GEOMETRY OPTIMIZATION CYCLE\s+(\d+)/i;

  /**
   * Tracks SCF convergence from a stream of log lines.
   * Strategy (matches what we verified on real .out files):
   *  - "GEOMETRY OPTIMIZATION CYCLE N" => new optimization step; reset the
   *    current SCF curve and remember the step number.
   *  - An iteration line whose iter number is <= the last iter starts a NEW
   *    SCF block (reset). Continuing/increasing iter numbers extend the same
   *    block (so a DIIS table flowing into SOSCF, with iter 4 -> 5 -> 6, stays
   *    one curve).
   */
  function SCFTracker() {
    this.points = [];        // [{iter, dE}] for the current SCF block (dE = |Delta-E|)
    this.lastIter = 0;
    this.step = 0;           // geometry optimization step (0 = none / single-point)
    this.startDE = null;     // |Delta-E| of the first usable point in this block
  }
  SCFTracker.prototype.reset = function () {
    this.points = [];
    this.lastIter = 0;
    this.startDE = null;
  };
  // feed one log line; returns true if the curve changed
  SCFTracker.prototype.push = function (line) {
    const g = line.match(GEO_RE);
    if (g) {
      this.step = parseInt(g[1], 10);
      this.reset();
      return true;
    }
    const m = line.match(ITER_RE);
    if (!m) return false;
    const iter = parseInt(m[1], 10);
    const dE = Math.abs(parseFloat(m[3]));
    if (!isFinite(dE)) return false;
    // new SCF block if iteration counter restarted
    if (iter <= this.lastIter) this.reset();
    this.lastIter = iter;
    // the very first row has Delta-E = 0 (0.00e+00); skip it as a start anchor
    // but still show it on the curve. Use the first NONZERO dE as startDE.
    this.points.push({ iter: iter, dE: dE });
    if (this.startDE === null && dE > 0) this.startDE = dE;
    return true;
  };
  // progress 0..1 toward the target, on a log scale relative to the start
  SCFTracker.prototype.progress = function (target) {
    if (this.startDE === null || !this.points.length) return 0;
    const cur = this._lastNonZeroDE();
    if (cur === null) return 0;
    if (cur <= target) return 1;
    const ls = Math.log10(this.startDE);
    const lt = Math.log10(target);
    const lc = Math.log10(cur);
    if (ls === lt) return 1;
    const p = (ls - lc) / (ls - lt);
    return Math.max(0, Math.min(1, p));
  };
  SCFTracker.prototype._lastNonZeroDE = function () {
    for (let i = this.points.length - 1; i >= 0; i--) {
      if (this.points[i].dE > 0) return this.points[i].dE;
    }
    return null;
  };
  SCFTracker.prototype.current = function () { return this._lastNonZeroDE(); };
  SCFTracker.prototype.hasData = function () { return this.startDE !== null; };

  // ---- geometry optimization convergence ----
  // ORCA prints a "|Geometry convergence|" table each optimization step:
  //   Item            value          Tolerance      Converged
  //   MAX gradient    0.0140882096   0.0001000000   NO
  // We track MAX gradient per step (the criterion that converges last) plus
  // how many of the (up to 5) criteria are met at the latest step.
  const GEO_MAXGRAD_RE = /MAX gradient\s+([\d.]+)\s+([\d.]+)\s+(YES|NO)/i;
  const GEO_ITEM_RE = /(Energy change|RMS gradient|MAX gradient|RMS step|MAX step)\s+(-?[\d.]+)\s+([\d.]+)\s+(YES|NO)/i;
  const GEO_TABLE_RE = /\|Geometry convergence\|/i;
  // ORCA prints the real wall time of each optimization cycle. Using it for the
  // ETA (instead of Date.now poll gaps) removes UI jitter / replay-burst noise —
  // validated on 85 real opt runs: with the true cycle count, this time model is
  // accurate to ~8% (median). [the cycle-count itself is the irreducible ~65%
  // uncertainty — geometry-opt convergence has a long, unpredictable tail.]
  const GEO_ITERTIME_RE = /Time for complete geometry iter\s*:\s*([\d.]+)\s*s/i;
  // ORCA finished the geometry optimization (converged, or done before a freq/
  // property stage). The per-criteria table can read 4/5 met at that point, so we
  // must flip to 100% on this marker rather than waiting for every criterion.
  const OPT_DONE_RE = /\*\*\*\s*OPTIMIZATION RUN DONE\s*\*\*\*|THE OPTIMIZATION HAS CONVERGED|HURRAY/i;
  // a post-optimization stage starting (so we can say "running frequencies/…").
  // Case-SENSITIVE on purpose: the real markers are ORCA's UPPERCASE section
  // banners, while every output — opt-only runs included — also contains
  // mixed-case mentions that must NOT match: the header credits ("pre 5.0
  // version of the SCF Hessian") and the property-block echo ("Properties with
  // geometric perturbations:", "SCF Hessian ... NO"). A case-insensitive match
  // here showed "running frequencies…" (and the freq banner) on plain opts.
  const POST_OPT_RE = /VIBRATIONAL FREQUENCIES|ORCA SCF RESPONSE|GEOMETRIC PERTURBATIONS|CP-?SCF DRIVER|SCF HESSIAN|ANALYTICAL FREQUENCIES|NUMERICAL FREQUENCIES/;
  // job end: the post-opt stage (and the whole run) is over — a graph re-seeded
  // from a finished .out must not label a DONE calc "running frequencies / …"
  const OPT_TERM_RE = /ORCA TERMINATED NORMALLY/;

  // the (up to) five geometry-convergence criteria ORCA prints each step. Colors
  // come from CSS vars (--crit-*) so they adapt to the theme — dark keeps the
  // original "harmonious on the dark UI" palette, light gets darker, legible
  // variants. Emitted via inline style="" (SVG presentation attributes don't
  // resolve var()).
  const GEO_CRITERIA = [
    { key: "Energy change", label: "ΔE",       color: "var(--crit-de)" },
    { key: "RMS gradient",  label: "RMS grad", color: "var(--crit-rmsg)" },
    { key: "MAX gradient",  label: "MAX grad", color: "var(--crit-maxg)" },
    { key: "RMS step",      label: "RMS step", color: "var(--crit-rmss)" },
    { key: "MAX step",      label: "MAX step", color: "var(--crit-maxs)" },
  ];
  const _GEO_CANON = {};
  GEO_CRITERIA.forEach(function (c) { _GEO_CANON[c.key.toLowerCase()] = c.key; });
  function _canonCrit(name) { return _GEO_CANON[(name || "").toLowerCase()] || name; }

  // per-criterion tolerances are read live from each table, so NormalOpt vs
  // TightOpt (different tolerances) are handled automatically.
  function GeoTracker() {
    this.steps = [];          // [{step, maxGrad, tol}] — one entry per unique opt cycle
    this.tol = 1e-4;          // MAX gradient tolerance (read from the table)
    this.startGrad = null;    // first step's MAX gradient (for progress)
    this._inTable = false;    // currently inside a convergence table
    this._criteria = {};      // latest step: {name: converged-bool}
    this._pendingCriteria = {};
    this._pendingVals = {};   // {name: {val, tol}} accumulated in current table
    this.worst = [];          // worst-ratio per step: log10(max(val/tol)); 0=at-threshold
    this.stepTimes = [];      // wall-clock ms at which each step's table completed
    this._etaPred = null;     // temporally-smoothed predicted total steps
    this.curCycle = 0;        // latest "GEOMETRY OPTIMIZATION CYCLE N" (0 = none seen yet)
    this._byCycle = {};       // cycle number -> index into this.steps
    this._worstByCycle = {};  // cycle number -> index into this.worst / this.stepTimes
    this._secByCycle = {};    // cycle number -> real ORCA wall seconds for that cycle
    this.done = false;        // geometry optimization has finished (converged / RUN DONE)
    this.postStage = "";      // post-opt stage now running (e.g. "frequencies"), for the label
    this.postDone = false;    // ORCA TERMINATED NORMALLY seen — the post-opt stage completed
  }
  GeoTracker.prototype.push = function (line) {
    // Track the real ORCA optimization cycle number. Steps are keyed by this
    // number so the same cycle's table being seen (or fed) more than once can
    // never inflate the step count — it overwrites instead of appending.
    const gc = line.match(GEO_RE);
    if (gc) { this.curCycle = parseInt(gc[1], 10); return false; }
    const ts = line.match(GEO_ITERTIME_RE);
    if (ts) { if (this.curCycle > 0) this._secByCycle[this.curCycle] = parseFloat(ts[1]); return false; }
    if (OPT_DONE_RE.test(line)) { this.done = true; return true; }   // -> 100%, stop the ETA
    if (this.done && !this.postStage && POST_OPT_RE.test(line)) { this.postStage = "frequencies / properties"; return false; }
    if (this.done && OPT_TERM_RE.test(line)) { this.postDone = true; return false; }
    if (GEO_TABLE_RE.test(line)) {
      this._inTable = true;
      this._sawItem = false;
      this._pendingCriteria = {};
      this._pendingVals = {};
      return false;
    }
    if (!this._inTable) return false;
    const m = line.match(GEO_ITEM_RE);
    if (m) {
      this._sawItem = true;
      const name = _canonCrit(m[1]);
      const val = Math.abs(parseFloat(m[2]));
      const tol = parseFloat(m[3]);
      this._pendingCriteria[name] = m[4].toUpperCase() === "YES";
      if (isFinite(val) && isFinite(tol) && tol > 0) this._pendingVals[name] = { val: val, tol: tol };
      if (/MAX gradient/i.test(name) && isFinite(tol)) this.tol = tol;  // headline tol (back-compat)
      return true;
    }
    if (this._sawItem && (line.trim() === "" || /-{5,}/.test(line) || /\.{5,}/.test(line))) {
      this._commitStep();
      this._inTable = false;
      this._sawItem = false;
    }
    return false;
  };
  // Commit the just-parsed table as one optimization step. Keyed by the real
  // cycle number so a re-emitted table overwrites instead of appending. Stores
  // ALL criteria (value + tolerance), not just MAX gradient.
  GeoTracker.prototype._commitStep = function () {
    if (!Object.keys(this._pendingVals).length) return;
    this._criteria = this._pendingCriteria;
    const vals = {}, tols = {};
    for (const k in this._pendingVals) { vals[k] = this._pendingVals[k].val; tols[k] = this._pendingVals[k].tol; }
    const mg = (vals["MAX gradient"] != null) ? vals["MAX gradient"] : null;
    const key = this.curCycle > 0 ? this.curCycle : (this.steps.length + 1);
    const rec = { step: key, vals: vals, tols: tols, maxGrad: mg };
    const idx = this._byCycle[key];
    if (idx == null) { this._byCycle[key] = this.steps.length; this.steps.push(rec); }
    else { this.steps[idx] = rec; }
    if (this.startGrad === null && mg != null && mg > 0) this.startGrad = mg;
    // worst-ratio series for the ETA estimator (one entry per cycle)
    let worstLog = -99;
    for (const k in this._pendingVals) {
      const r = Math.log10(Math.max(this._pendingVals[k].val, 1e-12) / this._pendingVals[k].tol);
      if (r > worstLog) worstLog = r;
    }
    if (worstLog > -90) {
      const ckey = this.curCycle > 0 ? this.curCycle : ("seq" + this.worst.length);
      const wi = this._worstByCycle[ckey];
      if (wi == null) { this._worstByCycle[ckey] = this.worst.length; this.worst.push(worstLog); this.stepTimes.push(Date.now()); }
      else { this.worst[wi] = worstLog; }
    }
  };
  GeoTracker.prototype.allConverged = function () {
    // true only when every criterion at the latest step is YES (>=4 of them,
    // so we don't report "done" off a partial early table)
    const c = Object.keys(this._criteria).length ? this._criteria : this._pendingCriteria;
    const names = Object.keys(c);
    if (names.length < 4) return false;
    return names.every(function (n) { return c[n]; });
  };
  GeoTracker.prototype._lastMaxGrad = function () {
    for (let i = this.steps.length - 1; i >= 0; i--) {
      const g = this.steps[i].maxGrad;
      if (g != null && g > 0) return g;
    }
    return null;
  };
  GeoTracker.prototype.progress = function () {
    if (this.done) return 1;            // ORCA reported the optimization finished
    if (this.startGrad === null || !this.steps.length) return 0;
    // 100% only when the optimizer has actually met all convergence criteria;
    // otherwise cap at 99% even if MAX gradient alone reached the tolerance
    if (this.allConverged()) return 1;
    const cur = this._lastMaxGrad();
    if (cur === null) return 0;
    const ls = Math.log10(this.startGrad);
    const lt = Math.log10(this.tol);
    const lc = Math.log10(cur);
    if (ls === lt) return 0.99;
    const raw = (ls - lc) / (ls - lt);
    return Math.max(0, Math.min(0.99, raw));
  };
  GeoTracker.prototype.current = function () {
    return this._lastMaxGrad();
  };
  GeoTracker.prototype.criteriaSummary = function () {
    // returns {met, total} from the latest completed step
    const c = Object.keys(this._criteria).length ? this._criteria : this._pendingCriteria;
    const names = Object.keys(c);
    const met = names.filter(function (n) { return c[n]; }).length;
    return { met: met, total: names.length };
  };
  GeoTracker.prototype.hasData = function () { return this.steps.length > 0; };

  // ---------- ETA estimation (research-tuned ensemble) ----------
  // median-of-3 smoothing of the worst-ratio series
  function _smooth3(y) {
    const n = y.length;
    if (n < 3) return y.slice();
    const out = [y[0]];
    for (let i = 1; i < n - 1; i++) {
      const t = [y[i - 1], y[i], y[i + 1]].sort(function (a, b) { return a - b; });
      out.push(t[1]);
    }
    out.push(y[n - 1]);
    return out;
  }
  // four predictors, each returns predicted TOTAL steps or null
  function _predLinear(w, at) {
    const y = _smooth3(w.slice(0, at)); const n = y.length;
    if (n < 6) return null;
    const seg = n > 15 ? y.slice(-15) : y; const m = seg.length;
    let sx = 0, sy = 0, sxx = 0, sxy = 0;
    for (let i = 0; i < m; i++) { sx += i; sy += seg[i]; sxx += i * i; sxy += i * seg[i]; }
    const den = m * sxx - sx * sx; if (Math.abs(den) < 1e-9) return null;
    const slope = (m * sxy - sx * sy) / den, icpt = (sy - slope * sx) / m;
    if (slope >= -0.01) return null;
    return Math.max((0 - icpt) / slope, at + 1);
  }
  function _predDecay(w, at) {
    const y = _smooth3(w.slice(0, at)); const n = y.length;
    if (n < 6) return null;
    const win = n > 10 ? y.slice(-10) : y;
    const drops = []; for (let i = 1; i < win.length; i++) drops.push(win[i - 1] - win[i]);
    drops.sort(function (a, b) { return a - b; });
    const med = drops[Math.floor(drops.length / 2)];
    if (med <= 0.005) return null;
    return at + Math.max(y[n - 1] / med, 1);
  }
  function _predEma(w, at) {
    const y = _smooth3(w.slice(0, at)); const n = y.length;
    if (n < 6) return null;
    let ema = null; const a = 0.3;
    for (let i = 1; i < n; i++) { const d = y[i - 1] - y[i]; ema = ema == null ? d : a * d + (1 - a) * /** @type {number} */ (ema); }
    if (ema <= 0.005) return null;
    return at + Math.max(y[n - 1] / ema, 1);
  }
  function _predRobust(w, at) {
    const y = _smooth3(w.slice(0, at)); const n = y.length;
    if (n < 6) return null;
    let best = null;
    [6, 8, 10, 15].forEach(function (win) {
      if (win > n) return;
      const seg = y.slice(-win);
      const rate = (seg[0] - seg[seg.length - 1]) / (win - 1);
      if (rate > 0.01) {
        const pred = at + Math.max(seg[seg.length - 1] / rate, 1);
        best = best == null ? pred : 0.5 * best + 0.5 * pred;
      }
    });
    return best;
  }
  // ensemble + agreement gate; returns {total, conf} or null
  GeoTracker.prototype._rawEta = function () {
    const w = this.worst; const at = w.length;
    const minStep = _etaMode === "eager" ? 6 : 8;
    if (at < minStep) return null;
    const y = _smooth3(w);
    let preds = [_predLinear(w, at), _predDecay(w, at), _predEma(w, at), _predRobust(w, at)]
      .filter(function (p) { return p && p >= at + 1 && p < at * 4; });
    if (preds.length < 2) return null;
    preds.sort(function (a, b) { return a - b; });
    const med = preds[Math.floor(preds.length / 2)];
    const spread = med > 0 ? (preds[preds.length - 1] - preds[0]) / med : 9;
    const seg = y.slice(-Math.min(at, 10));
    let decr = 0; for (let i = 1; i < seg.length; i++) if (seg[i] <= seg[i - 1]) decr++;
    decr = decr / (seg.length - 1);
    // gating thresholds — looser in eager mode
    const spreadMax = _etaMode === "eager" ? 0.9 : 0.6;
    const decrMin = _etaMode === "eager" ? 0.45 : 0.6;
    if (spread > spreadMax) return null;     // methods disagree
    if (decr < decrMin) return null;          // not monotone enough
    const c = Math.max(0, 1 - spread) * decr;
    return { total: med, conf: c > 0.6 ? "high" : c > 0.35 ? "med" : "low" };
  };
  // public: returns {remainingSteps, etaMs, conf} or null. Uses temporal
  // smoothing on the predicted total and measured time-per-step.
  // accurate per-step wall time (ms): ORCA's real "Time for complete geometry
  // iter" (steady median, dropping the one-time-expensive cycle 1), falling back
  // to Date.now poll intervals. ~7% median error — the reliable half of the ETA.
  GeoTracker.prototype.perStepMs = function () {
    const secs = [];
    for (let i = 0; i < this.steps.length; i++) {
      const v = this._secByCycle[this.steps[i].step];
      if (v > 0) secs.push(v);
    }
    if (secs.length >= 2) {
      const body = secs.slice(1);
      const recent = (body.length ? body : secs).slice(-8).slice().sort(function (a, b) { return a - b; });
      const m = recent[Math.floor(recent.length / 2)];
      if (m > 0) return m * 1000;
    }
    const t = this.stepTimes;   // fallback: Date.now gaps (skip <500ms replay bursts)
    if (t.length >= 3) {
      const gaps = []; for (let i = 1; i < t.length; i++) gaps.push(t[i] - t[i - 1]);
      const recent = gaps.slice(-10).filter(function (g) { return g >= 500; }).sort(function (a, b) { return a - b; });
      if (recent.length) { const m = recent[Math.floor(recent.length / 2)]; if (m > 0) return m; }
    }
    return null;
  };
  GeoTracker.prototype.estimateETA = function () {
    const raw = this._rawEta();
    const at = this.worst.length;
    const minStep = _etaMode === "eager" ? 6 : 8;
    if (raw) {
      if (this._etaPred == null) this._etaPred = raw.total;
      else this._etaPred = 0.6 * this._etaPred + 0.4 * raw.total;
      this._etaPred = Math.max(this._etaPred, at + 0.5);
    }
    if (this._etaPred == null || at < minStep) return null;
    const remaining = Math.max(this._etaPred - at, 0);
    // remaining cycles x accurate per-step time (see perStepMs)
    const perMs = this.perStepMs();
    let etaMs = (perMs != null) ? remaining * perMs : null;
    // Honest uncertainty band. Geometry-opt cycle counts are intrinsically hard
    // to predict (verified ~65% median error across heuristic + regression models
    // on 85 real runs — convergence has a long, unpredictable tail), so the point
    // estimate carries a wide, data-calibrated range: the true time fell within
    // ~[0.5x, 2x] of the estimate about half the time.
    let etaLowMs = null, etaHighMs = null;
    if (etaMs != null) { etaLowMs = etaMs * 0.5; etaHighMs = etaMs * 2.0; }
    // conf semantics:
    //  - raw present: "high"/"med"/"low" (a fresh confident estimate)
    //  - raw absent + eager + we have a prior estimate: "held" (keep showing it)
    //  - otherwise: "stale" (caller shows "estimating…")
    let conf;
    if (raw) conf = raw.conf;
    else if (_etaMode === "eager" && this._etaPred != null && at >= minStep) conf = "held";
    else conf = "stale";
    return { remainingSteps: remaining, etaMs: etaMs, etaLowMs: etaLowMs, etaHighMs: etaHighMs, conf: conf };
  };

  // ---------- rendering (DOM-string builders) ----------
  function fmtSci(x) {
    if (x == null || !isFinite(x)) return "—";
    return x.toExponential(2);
  }

  // The live "~N s / SCF cycle" pace chip, right-aligned in the progress meta
  // line (.scf-prog-meta is a flex row). Rendered with the current value, then
  // kept fresh between panel re-renders by app.js's poll tick (by id).
  function _paceSpan(pace) {
    return `<span id="scf-pace" class="scf-pace" title="Average wall-clock time per SCF cycle">${pace || ""}</span>`;
  }

  // progress bar HTML
  function renderSCFProgress(tracker, scfConv, pace) {
    const target = targetFor(scfConv);
    const p = tracker.progress(target);
    const pct = Math.round(p * 100);
    const stepLabel = tracker.step > 0 ? `Geometry step ${tracker.step} · ` : "";
    return (
      `<div class="scf-prog-label">${stepLabel}SCF convergence ${pct}%</div>` +
      `<div class="scf-prog-bar"><span style="width:${pct}%"></span></div>` +
      `<div class="scf-prog-meta">${_paceSpan(pace)}</div>`
    );
  }

  // SVG convergence graph: x = cycle, y = |Delta-E| on log scale.
  // Shows the start point and a dashed goal line.
  function renderSCFGraph(tracker, scfConv, opts) {
    opts = opts || {};
    const W = opts.width || 320;
    const H = opts.height || 180;
    const padL = 58, padR = 14, padT = 14, padB = 40;
    const target = targetFor(scfConv);
    const pts = tracker.points.filter(function (p) { return p.dE > 0; });

    if (pts.length < 1 || tracker.startDE === null) {
      return `<svg viewBox="0 0 ${W} ${H}" class="scf-svg" xmlns="http://www.w3.org/2000/svg">
        <text x="${W / 2}" y="${H / 2}" text-anchor="middle" class="scf-empty-text">
          waiting for data…</text></svg>`;
    }

    // y range (log10): from a bit above the max dE down to the target (or min)
    const dEs = pts.map(function (p) { return p.dE; });
    const yMaxLog = Math.ceil(Math.log10(Math.max.apply(null, dEs)));
    const yMinLog = Math.floor(Math.log10(Math.min(target, Math.min.apply(null, dEs))));
    const xN = Math.max(pts.length, 2);

    function X(i) { return padL + (i / (xN - 1)) * (W - padL - padR); }
    function Y(dE) {
      const l = Math.log10(dE);
      const t = (yMaxLog - l) / (yMaxLog - yMinLog || 1);
      return padT + t * (H - padT - padB);
    }

    // gridlines + labels at each decade
    let grid = "";
    for (let e = yMinLog; e <= yMaxLog; e++) {
      const yy = padT + ((yMaxLog - e) / (yMaxLog - yMinLog || 1)) * (H - padT - padB);
      grid += `<line x1="${padL}" y1="${yy}" x2="${W - padR}" y2="${yy}" class="scf-grid"/>`;
      grid += `<text x="${padL - 6}" y="${yy + 3}" text-anchor="end" class="scf-axis">1e${e}</text>`;
    }

    // goal line (dashed) at the target decade
    const goalY = Y(target);
    const goal = `<line x1="${padL}" y1="${goalY}" x2="${W - padR}" y2="${goalY}" class="scf-goal"/>`;

    // the convergence polyline
    let d = "";
    pts.forEach(function (p, i) { d += (i === 0 ? "M" : "L") + X(i).toFixed(1) + "," + Y(p.dE).toFixed(1) + " "; });
    const line = `<path d="${d.trim()}" class="scf-line" fill="none"/>`;

    // start marker (first point) and current marker (last point)
    const startC = `<circle cx="${X(0).toFixed(1)}" cy="${Y(pts[0].dE).toFixed(1)}" r="3.5" class="scf-start"/>`;
    const li = pts.length - 1;
    const curC = `<circle cx="${X(li).toFixed(1)}" cy="${Y(pts[li].dE).toFixed(1)}" r="4" class="scf-cur"/>`;

    // x-axis tick numbers (cycle indices) — thin them out if there are many
    const baseY = padT + (H - padT - padB);
    const stepEvery = Math.max(1, Math.ceil(xN / 8));
    let xticks = "";
    for (let i = 0; i < pts.length; i += stepEvery) {
      const xx = X(i);
      xticks += `<line x1="${xx.toFixed(1)}" y1="${baseY}" x2="${xx.toFixed(1)}" y2="${(baseY + 4).toFixed(1)}" class="scf-grid"/>`;
      // label with the point's real SCF iteration, not the filtered-array
      // index — the ΔE=0 first row is filtered out above, so `i + 1` would
      // read one cycle off against the raw log (cf. _renderGeoMaxGrad, which
      // labels with pts[i].step for the same reason)
      xticks += `<text x="${xx.toFixed(1)}" y="${(baseY + 15).toFixed(1)}" text-anchor="middle" class="scf-axis">${pts[i].iter}</text>`;
    }

    // axis titles: y = what we plot (energy change per cycle), x = cycle count
    const yTitle = `<text x="14" y="${(padT + (H - padT - padB) / 2).toFixed(1)}" text-anchor="middle" class="scf-axis-title" transform="rotate(-90 14 ${(padT + (H - padT - padB) / 2).toFixed(1)})">|ΔE| per cycle (Eh)</text>`;
    const xTitle = `<text x="${((padL + W - padR) / 2).toFixed(1)}" y="${H - 4}" text-anchor="middle" class="scf-axis-title">SCF cycle</text>`;

    return `<svg viewBox="0 0 ${W} ${H}" class="scf-svg" xmlns="http://www.w3.org/2000/svg">
      ${grid}${goal}${line}${startC}${curC}${xticks}${yTitle}${xTitle}</svg>`;
  }

  // ---- geometry optimization renderers ----
  function _fmtDuration(ms) {
    if (ms == null || !isFinite(ms) || ms < 0) return null;
    const s = Math.round(ms / 1000);
    if (s < 60) return s + "s";
    const m = Math.round(s / 60);
    if (m < 60) return m + "m";
    const h = Math.floor(m / 60), mm = m % 60;
    return h + "h " + mm + "m";
  }
  // Coarse, order-of-magnitude time bucket. The opt cycle count is ~2x uncertain,
  // so a precise countdown would be false precision; geometric buckets (each ~one
  // order of magnitude) mean a 2x miss usually stays in the same/adjacent bucket.
  function _etaBucket(ms) {
    if (ms == null || !isFinite(ms) || ms < 0) return null;
    const s = ms / 1000;
    if (s < 45) return "under a minute";
    if (s < 8 * 60) return "a few minutes";
    if (s < 50 * 60) return "tens of minutes";
    if (s < 5 * 3600) return "a few hours";
    if (s < 24 * 3600) return "many hours";
    return "a day or more";
  }
  function renderGeoProgress(geo, pace) {
    const p = geo.progress();
    const pct = Math.round(p * 100);
    const cs = geo.criteriaSummary();
    // show the real ORCA cycle number of the latest table, not the array length
    const nPts = geo.steps.length;
    const stepN = nPts ? geo.steps[nPts - 1].step : 0;

    // optimization finished -> jump to 100% and announce the next stage, instead
    // of staying stuck at 99% (the last criteria table can read 4/5 met)
    if (geo.done) {
      const tail = geo.postStage
        ? (geo.postDone ? `, ${geo.postStage} complete` : `, running ${geo.postStage}`)
        : "";
      return (
        `<div class="scf-prog-label">Optimization complete · 100% · step ${stepN}</div>` +
        `<div class="scf-prog-bar"><span style="width:100%"></span></div>` +
        `<div class="scf-prog-meta">geometry converged${tail}${_paceSpan(pace)}</div>`
      );
    }

    // accurate, real signals first: criteria met + measured per-step rate
    const facts = [];
    if (cs.total) facts.push(`${cs.met}/${cs.total} criteria met`);
    const perMs = geo.perStepMs ? geo.perStepMs() : null;
    const rate = _fmtDuration(perMs);
    if (rate) facts.push(`~${rate}/step`);
    // always emitted (even with no facts yet) so the pace chip's span exists
    // for the poll tick's live update
    const factLine = `<div class="scf-prog-meta">${facts.join(" · ")}${_paceSpan(pace)}</div>`;

    // ETA is the unreliable part (cycle count ~2x uncertain) — keep it coarse and
    // secondary: an order-of-magnitude bucket, not a false-precise countdown.
    let etaLine = "";
    const eta = geo.estimateETA ? geo.estimateETA() : null;
    if (eta && eta.conf !== "stale") {
      const bucket = _etaBucket(eta.etaMs);
      const rem = Math.round(eta.remainingSteps);
      const more = `~${rem} more step${rem === 1 ? "" : "s"}`;
      etaLine = `<div class="scf-prog-eta">${bucket ? "roughly " + bucket + " left · " : ""}${more}</div>`;
    } else if (nPts >= 4) {
      etaLine = `<div class="scf-prog-eta">estimating time…</div>`;
    }
    return (
      `<div class="scf-prog-label">Optimization ${pct}% · step ${stepN}</div>` +
      `<div class="scf-prog-bar"><span style="width:${pct}%"></span></div>` +
      factLine +
      etaLine
    );
  }

  // dispatcher: the optimization-graph style follows the Settings toggle.
  function renderGeoGraph(geo, opts) {
    return _geoMode === "maxgrad" ? _renderGeoMaxGrad(geo, opts) : _renderGeoAll5(geo, opts);
  }

  // "maxgrad" mode (the original view): only MAX gradient on an absolute log
  // axis, with its tolerance as the single dashed goal line.
  function _renderGeoMaxGrad(geo, opts) {
    opts = opts || {};
    const W = opts.width || 320;
    const H = opts.height || 180;
    const padL = 58, padR = 14, padT = 14, padB = 40;
    const pts = (geo.steps || []).filter(function (s) { return s.maxGrad != null && s.maxGrad > 0; });
    if (!pts.length) {
      return `<svg viewBox="0 0 ${W} ${H}" class="scf-svg" xmlns="http://www.w3.org/2000/svg">
        <text x="${W / 2}" y="${H / 2}" text-anchor="middle" class="scf-empty-text">
          waiting for optimization steps…</text></svg>`;
    }
    const tol = geo.tol;
    const gs = pts.map(function (s) { return s.maxGrad; });
    const yMaxLog = Math.ceil(Math.log10(Math.max.apply(null, gs)));
    const yMinLog = Math.floor(Math.log10(Math.min(tol, Math.min.apply(null, gs))));
    const xN = Math.max(pts.length, 2);
    function X(i) { return padL + (i / (xN - 1)) * (W - padL - padR); }
    function Y(v) {
      const l = Math.log10(v);
      const t = (yMaxLog - l) / (yMaxLog - yMinLog || 1);
      return padT + t * (H - padT - padB);
    }
    let grid = "";
    for (let e = yMinLog; e <= yMaxLog; e++) {
      const yy = padT + ((yMaxLog - e) / (yMaxLog - yMinLog || 1)) * (H - padT - padB);
      grid += `<line x1="${padL}" y1="${yy}" x2="${W - padR}" y2="${yy}" class="scf-grid"/>`;
      grid += `<text x="${padL - 6}" y="${yy + 3}" text-anchor="end" class="scf-axis">1e${e}</text>`;
    }
    const goalY = Y(tol);
    const goal = `<line x1="${padL}" y1="${goalY}" x2="${W - padR}" y2="${goalY}" class="scf-goal"/>`;
    let d = "";
    pts.forEach(function (s, i) { d += (i === 0 ? "M" : "L") + X(i).toFixed(1) + "," + Y(s.maxGrad).toFixed(1) + " "; });
    const line = `<path d="${d.trim()}" class="scf-line" fill="none"/>`;
    const startC = `<circle cx="${X(0).toFixed(1)}" cy="${Y(pts[0].maxGrad).toFixed(1)}" r="3.5" class="scf-start"/>`;
    const li = pts.length - 1;
    const curC = `<circle cx="${X(li).toFixed(1)}" cy="${Y(pts[li].maxGrad).toFixed(1)}" r="4" class="scf-cur"/>`;
    const baseY = padT + (H - padT - padB);
    const stepEvery = Math.max(1, Math.ceil(xN / 8));
    let xticks = "";
    for (let i = 0; i < pts.length; i += stepEvery) {
      const xx = X(i);
      xticks += `<line x1="${xx.toFixed(1)}" y1="${baseY}" x2="${xx.toFixed(1)}" y2="${(baseY + 4).toFixed(1)}" class="scf-grid"/>`;
      xticks += `<text x="${xx.toFixed(1)}" y="${(baseY + 15).toFixed(1)}" text-anchor="middle" class="scf-axis">${pts[i].step}</text>`;
    }
    const midY = (padT + (H - padT - padB) / 2).toFixed(1);
    const yTitle = `<text x="14" y="${midY}" text-anchor="middle" class="scf-axis-title" transform="rotate(-90 14 ${midY})">MAX gradient</text>`;
    const xTitle = `<text x="${((padL + W - padR) / 2).toFixed(1)}" y="${H - 4}" text-anchor="middle" class="scf-axis-title">optimization step</text>`;
    return `<svg viewBox="0 0 ${W} ${H}" class="scf-svg" xmlns="http://www.w3.org/2000/svg">
      ${grid}${goal}${line}${startC}${curC}${xticks}${yTitle}${xTitle}</svg>`;
  }

  // "all5" mode: every criterion as value/tolerance, one shared goal line at 1.
  function _renderGeoAll5(geo, opts) {
    opts = opts || {};
    const W = opts.width || 320;
    const H = opts.height || 180;
    const padL = 58, padR = 14, padT = 28, padB = 40;   // padT leaves room for the legend
    const steps = geo.steps || [];
    const empty = `<svg viewBox="0 0 ${W} ${H}" class="scf-svg" xmlns="http://www.w3.org/2000/svg">
        <text x="${W / 2}" y="${H / 2}" text-anchor="middle" class="scf-empty-text">
          waiting for optimization steps…</text></svg>`;
    if (!steps.length) return empty;

    // Each criterion is plotted as value / its OWN tolerance, so all five share
    // a single goal line at ratio = 1: a criterion is met when its line is at or
    // below 1 (this is why the criteria N/5 count maps directly to the graph).
    let rmin = Infinity, rmax = -Infinity;
    const series = [];
    GEO_CRITERIA.forEach(function (crit) {
      const sp = [];
      steps.forEach(function (s, i) {
        const v = s.vals ? s.vals[crit.key] : (crit.key === "MAX gradient" ? s.maxGrad : null);
        const tol = s.tols ? s.tols[crit.key] : null;
        if (v != null && v > 0 && isFinite(v) && tol != null && tol > 0) {
          const r = v / tol;
          sp.push({ i: i, r: r });
          if (r < rmin) rmin = r;
          if (r > rmax) rmax = r;
        }
      });
      if (sp.length) series.push({ crit: crit, pts: sp });
    });
    if (!series.length || !isFinite(rmin) || !isFinite(rmax)) return empty;

    // keep the goal (ratio = 1, i.e. log 0) inside the range with a little margin
    const yMaxLog = Math.max(Math.ceil(Math.log10(rmax)), 1);
    const yMinLog = Math.min(Math.floor(Math.log10(rmin)), -1);
    const xN = Math.max(steps.length, 2);
    const baseY = padT + (H - padT - padB);
    function X(i) { return padL + (i / (xN - 1)) * (W - padL - padR); }
    function Y(r) {
      const l = Math.log10(r);
      const t = (yMaxLog - l) / (yMaxLog - yMinLog || 1);
      return padT + t * (H - padT - padB);
    }

    // gridlines + y labels at each ratio decade (the ratio=1 decade is drawn
    // separately as the goal line below)
    let grid = "";
    for (let e = yMinLog; e <= yMaxLog; e++) {
      if (e === 0) continue;
      const yy = padT + ((yMaxLog - e) / (yMaxLog - yMinLog || 1)) * (H - padT - padB);
      grid += `<line x1="${padL}" y1="${yy}" x2="${W - padR}" y2="${yy}" class="scf-grid"/>`;
      grid += `<text x="${padL - 6}" y="${yy + 3}" text-anchor="end" class="scf-axis">1e${e}</text>`;
    }

    // converged zone (ratio < 1) shaded faintly, plus the single dashed goal line
    const goalY = Y(1);
    // fill via inline style (SVG presentation attributes don't resolve var());
    // a hardcoded hex here stayed dark-theme green in light mode
    const zone = `<rect x="${padL}" y="${goalY.toFixed(1)}" width="${(W - padR - padL).toFixed(1)}" height="${(baseY - goalY).toFixed(1)}" style="fill:var(--crit-rmsg)" opacity="0.07"/>`;
    const goal =
      `<line x1="${padL}" y1="${goalY.toFixed(1)}" x2="${W - padR}" y2="${goalY.toFixed(1)}" class="scf-goal" stroke-width="1.1" stroke-dasharray="5 3"/>` +
      `<text x="${padL - 6}" y="${(goalY + 3).toFixed(1)}" text-anchor="end" class="scf-goal-label">1</text>`;

    let lines = "", dots = "";
    series.forEach(function (s) {
      const col = s.crit.color;
      let d = "";
      s.pts.forEach(function (p, k) { d += (k === 0 ? "M" : "L") + X(p.i).toFixed(1) + "," + Y(p.r).toFixed(1) + " "; });
      lines += `<path d="${d.trim()}" fill="none" style="stroke:${col}" stroke-width="1.1" stroke-linejoin="round"/>`;
      const last = s.pts[s.pts.length - 1];
      dots += `<circle cx="${X(last.i).toFixed(1)}" cy="${Y(last.r).toFixed(1)}" r="2.2" style="fill:${col}"/>`;
    });

    // x-axis tick numbers (real ORCA cycle numbers) — thinned when many
    const stepEvery = Math.max(1, Math.ceil(xN / 8));
    let xticks = "";
    for (let i = 0; i < steps.length; i += stepEvery) {
      const xx = X(i);
      xticks += `<line x1="${xx.toFixed(1)}" y1="${baseY}" x2="${xx.toFixed(1)}" y2="${(baseY + 4).toFixed(1)}" class="scf-grid"/>`;
      xticks += `<text x="${xx.toFixed(1)}" y="${(baseY + 15).toFixed(1)}" text-anchor="middle" class="scf-axis">${steps[i].step}</text>`;
    }

    // legend row across the top: colour swatch + label per criterion present
    let legend = "";
    const segW = (W - padL - padR) / series.length;
    series.forEach(function (s, k) {
      const lx = padL + k * segW;
      legend += `<line x1="${lx.toFixed(1)}" y1="9" x2="${(lx + 13).toFixed(1)}" y2="9" style="stroke:${s.crit.color}" stroke-width="2"/>`;
      legend += `<text x="${(lx + 17).toFixed(1)}" y="12" class="scf-axis" style="fill:${s.crit.color}">${s.crit.label}</text>`;
    });

    const midY = (padT + (H - padT - padB) / 2).toFixed(1);
    const yTitle = `<text x="12" y="${midY}" text-anchor="middle" class="scf-axis-title" transform="rotate(-90 12 ${midY})">value / tolerance</text>`;
    const xTitle = `<text x="${((padL + W - padR) / 2).toFixed(1)}" y="${H - 4}" text-anchor="middle" class="scf-axis-title">optimization step</text>`;

    return `<svg viewBox="0 0 ${W} ${H}" class="scf-svg" xmlns="http://www.w3.org/2000/svg">
      ${grid}${zone}${goal}${lines}${dots}${xticks}${legend}${yTitle}${xTitle}</svg>`;
  }

  // ---------- numerical-frequency progress (accurate ETA) ----------
  // Numerical frequencies (e.g. M06-2X + CPCM) run a FIXED number of displacements
  // (3N x 2 for central differences), each an SCF+gradient. Unlike a geometry opt,
  // the TOTAL is printed up front and the counter is exact, so the ETA is reliable:
  // its only error is per-displacement time variance (~a few %), not the ~65%
  // cycle-count uncertainty of an opt.
  // Stage-start markers are case-SENSITIVE: ORCA prints them as UPPERCASE
  // section banners, and every output (opt-only included) also carries
  // mixed-case look-alikes that must not trigger the freq banner — the header
  // credits ("pre 5.0 version of the SCF Hessian") and the property-block echo
  // ("Properties with geometric perturbations:", "SCF Hessian ... NO").
  const NFREQ_START_RE = /ORCA NUMERICAL FREQUENCIES/;
  const NDISP_RE = /Number of displacements\s+\.*\s*(\d+)/i;
  const DISP_RE = /displacement\s+(\d+)\s*\/\s*(\d+)/i;   // numerical: "...for displacement K / N"
  const VFREQ_RE = /VIBRATIONAL FREQUENCIES/;
  // analytical Hessian via coupled-perturbed SCF (CP-SCF). Not a black box: ORCA
  // prints an UPPERCASE banner for each stage of the pipeline, always in the
  // same order (verified on 4 real ORCA 6.1.1 freq outputs, 12-52 atoms,
  // CPCM/SMD): derivative integrals -> CP-SCF response (with a "K / N done"
  // perturbation counter) -> Hessian assembly -> frequencies -> normal modes
  // -> IR spectrum -> thermochemistry. The UI shows them as a 7-dot phase chain.
  // `boot: true` marks banners unique to a Hessian computation, which may
  // ACTIVATE the chain. The others only ADVANCE an already-active chain —
  // they also occur in other job types (GIAO NMR and polarizability runs
  // print "ORCA SCF RESPONSE CALCULATION" for their own CP-SCF solves, and
  // numerical-freq runs print the post-Hessian banners), so letting them
  // bootstrap showed the analytical-freq panel during plain NMR runs.
  const A_STAGES = [
    { key: "integrals", re: /GEOMETRIC PERTURBATIONS/, boot: true,                label: "Derivative integrals" },
    { key: "cpscf",     re: /ORCA SCF RESPONSE CALCULATION|SHARK CP-?SCF DRIVER/, label: "CP-SCF response" },
    { key: "hessian",   re: /\bSCF HESSIAN\b/, boot: true,                        label: "SCF Hessian" },
    { key: "freq",      re: VFREQ_RE,                                             label: "Frequencies" },
    { key: "modes",     re: /\bNORMAL MODES\b/,                                   label: "Normal modes" },
    { key: "ir",        re: /\bIR SPECTRUM\b/,                                    label: "IR spectrum" },
    { key: "thermo",    re: /THERMOCHEMISTRY AT\s+([\d.]+)/,                      label: "Thermochemistry" },
  ];
  const AFREQ_RE = /ANALYTICAL FREQUENC|Analytic(?:al)? Hessian/;  // other analytical markers (no stage)
  const ANUCLEI_RE = /GEOMETRIC PERTURBATIONS\s*\((\d+)\s*nuclei\)/; // banner carries the atom count
  const NPERT_RE = /Number of perturbations\s+\.*\s*(\d+)/i;               // CP-SCF total (= 3N)
  const CPSCF_DONE_RE = /(\d+)\s*\/\s*(\d+)\s+done/i;                      // "K/N done" per CP-SCF iter
  const ATERM_RE = /ORCA TERMINATED NORMALLY/;                             // job end -> chain complete
  // A new optimization cycle or IRC walk starting means the Hessian/TD-DFT
  // chain we were tracking belonged to a PRE-step stage — ts_opt's
  // "Calc_Hess true" and IRC's InitHess compute a full analytical Hessian
  // INSIDE cycle 1 / before the walk (verified on real BP86 OptTS + IRC
  // runs) — so the panel must clear instead of sitting stale through the
  // remaining cycles. Banners are uppercase; matched case-sensitively.
  const PHASE_RESET_RE = /GEOMETRY OPTIMIZATION CYCLE|FORWARD IRC|BACKWARD IRC/;

  // Job meta echoed at the top of the .out, shown on the stepper's meta line
  // (name · method · cores · elapsed): ORCA echoes the input file ("|  1> ! ..."
  // carries the method, "%pal nprocs N" the cores); CREST echoes its command
  // line ("$ .../crest input.xyz --gfn2 ... -T 4 ..."). First match wins.
  const ORCA_BANG_RE = /^\|\s*\d+>\s*!\s*(\S+(?:\s+\S+)?)/;      // first two "!"-line tokens (method + basis)
  const ORCA_NPROCS_RE = /^\|\s*\d+>\s*%pal\s+nprocs\s+(\d+)/i;
  function _grabOrcaMeta(t, line) {
    if (!t.method) { const m = line.match(ORCA_BANG_RE); if (m) t.method = m[1]; }
    if (!t.cores) { const p = line.match(ORCA_NPROCS_RE); if (p) t.cores = parseInt(p[1], 10); }
  }

  function FreqTracker() {
    this.mode = "";        // "" | "numerical" | "analytical"
    this.total = 0;        // numerical: total displacements (3N*2)
    this.cur = 0;          // numerical: latest displacement index seen
    this.active = false;   // a frequency stage is running
    this.dispDone = false; // displacements / Hessian finished (frequencies computed)
    this._times = [];      // numerical: Date.now() per new displacement
    this.perturbTotal = 0; // analytical: total CP-SCF perturbations (= 3N)
    this.perturbDone = 0;  // analytical: perturbations converged so far (K in "K/N done")
    this.cpscfIter = 0;    // analytical: CP-SCF iteration count
    this.aStage = "";      // analytical: key of the current A_STAGES entry ("" = not staged yet)
    this.aIdx = -1;        // analytical: index of aStage in A_STAGES (-1 = none)
    this.nuclei = 0;       // analytical: atom count (from the GEOMETRIC PERTURBATIONS banner)
    this.tempK = 0;        // analytical: thermochemistry temperature (K)
    this.finished = false; // analytical: ORCA TERMINATED NORMALLY seen — chain complete
    this.stageT = [];      // ms wall clock when each stage index was first entered
    this.subs = [];        // frozen key-result line per stage already left
    this.t0 = 0;           // ms when the chain first advanced (total elapsed)
    this.tEnd = 0;         // ms when the run finished (0 while running)
    this.noTimes = false;  // rebuilt from disk — wall-clock stamps would be meaningless
    this.method = "";      // echoed method ("!"-line, first two tokens)
    this.cores = 0;        // echoed %pal nprocs
  }
  // advance the analytical phase chain — monotonic, so a re-seen banner (or a
  // late "K/N done" line) can never move the chain backwards. Entering a new
  // stage stamps its wall clock and freezes the key result of the stage left.
  FreqTracker.prototype._advance = function (idx) {
    this.mode = "analytical"; this.active = true;
    if (!this.t0) this.t0 = Date.now();
    if (idx > this.aIdx) {
      if (this.aIdx >= 0) this.subs[this.aIdx] = this._curSub();
      this.aIdx = idx; this.aStage = A_STAGES[idx].key;
      if (this.stageT[idx] == null) this.stageT[idx] = Date.now();
    }
    if (this.aIdx >= 3) this.dispDone = true;   // "freq" and later: Hessian is done
    return true;
  };
  // the current stage's key-result line (frozen into subs[] when the stage ends)
  FreqTracker.prototype._curSub = function () {
    const dim3N = this.nuclei ? 3 * this.nuclei : this.perturbTotal;
    const cpTotal = this.perturbTotal || dim3N;
    if (this.aStage === "integrals") return this.nuclei ? `${this.nuclei} nuclei` : "";
    if (this.aStage === "cpscf") return `${this.perturbDone}/${cpTotal || "?"} perturbations${this.cpscfIter ? ` · iter ${this.cpscfIter}` : ""}`;
    if (this.aStage === "hessian") return dim3N ? `${dim3N}×${dim3N}` : "";
    if (this.aStage === "freq" || this.aStage === "modes" || this.aStage === "ir") return dim3N ? `${dim3N} modes` : "";
    if (this.aStage === "thermo") return this.tempK ? `${this.tempK} K` : "";
    return "";
  };
  // back to a clean slate (a pre-step Hessian's chain is over, see PHASE_RESET_RE)
  FreqTracker.prototype._resetChain = function () {
    this.mode = ""; this.total = 0; this.cur = 0; this.active = false;
    this.dispDone = false; this._times = [];
    this.perturbTotal = 0; this.perturbDone = 0; this.cpscfIter = 0;
    this.aStage = ""; this.aIdx = -1; this.nuclei = 0; this.tempK = 0;
    this.finished = false;
    this.stageT = []; this.subs = []; this.t0 = 0; this.tEnd = 0;   // meta (method/cores) survives: same job
  };
  FreqTracker.prototype.push = function (line) {
    _grabOrcaMeta(this, line);
    if (PHASE_RESET_RE.test(line)) {
      const was = this.active;
      if (was) this._resetChain();
      return was;   // true -> re-render so the stale panel disappears
    }
    if (VFREQ_RE.test(line) && this.mode !== "analytical") {
      // numerical (or untyped) run reaching the frequency table
      if (this.active) this.dispDone = true;
      return false;
    }
    // numerical markers take precedence and lock the mode
    if (NFREQ_START_RE.test(line)) { this.mode = "numerical"; this.active = true; return true; }
    const h = line.match(NDISP_RE);
    if (h) { this.total = parseInt(h[1], 10); this.mode = "numerical"; this.active = true; return true; }
    const d = line.match(DISP_RE);
    if (d) {
      const k = parseInt(d[1], 10), n = parseInt(d[2], 10);
      if (n > 0) this.total = n;
      if (k > this.cur) { this.cur = k; this._times.push(Date.now()); if (this._times.length > 40) this._times.shift(); }
      this.mode = "numerical"; this.active = true;
      return true;
    }
    if (this.mode === "numerical") return false;
    // analytical pipeline: stage banners (ordered, see A_STAGES)
    for (let i = 0; i < A_STAGES.length; i++) {
      if (A_STAGES[i].re.test(line)) {
        if (this.mode !== "analytical" && !A_STAGES[i].boot) break;   // shared banner: never bootstraps
        if (A_STAGES[i].key === "integrals") {
          const n = line.match(ANUCLEI_RE);
          if (n) { this.nuclei = parseInt(n[1], 10); if (!this.perturbTotal) this.perturbTotal = 3 * this.nuclei; }
        } else if (A_STAGES[i].key === "thermo") {
          const t = line.match(A_STAGES[i].re);
          if (t && t[1]) this.tempK = parseFloat(t[1]);
        }
        return this._advance(i);
      }
    }
    if (this.mode === "analytical") {
      // first NPERT wins: the geometric CP-SCF (the Hessian's 3N-ish solve) is
      // always the FIRST response solve in the pipeline; later property solves
      // (IR intensities, EPR/NMR blocks) print their own, smaller
      // "Number of perturbations" lines that must not overwrite the total
      const np = line.match(NPERT_RE);
      if (np) { if (!this.perturbTotal) this.perturbTotal = parseInt(np[1], 10); return true; }
      const cd = line.match(CPSCF_DONE_RE);
      if (cd) {
        this.perturbDone = parseInt(cd[1], 10);
        if (!this.perturbTotal) this.perturbTotal = parseInt(cd[2], 10);
        this.cpscfIter++;
        return this._advance(1);   // "cpscf"
      }
      if (ATERM_RE.test(line)) { this.finished = true; if (!this.tEnd) this.tEnd = Date.now(); return true; }
    }
    if (AFREQ_RE.test(line)) { this.mode = "analytical"; this.active = true; return true; }
    return false;
  };
  FreqTracker.prototype.hasData = function () { return this.active; };
  FreqTracker.prototype.progress = function () {
    if (this.dispDone) return 1;
    if (this.mode === "numerical") return this.total ? Math.min(this.cur / this.total, 0.999) : 0;
    if (this.mode === "analytical") return (this.perturbTotal && this.perturbDone) ? Math.min(this.perturbDone / this.perturbTotal, 0.999) : 0;
    return 0;
  };
  FreqTracker.prototype.perStepMs = function () {
    const t = this._times;
    if (t.length >= 2) {
      const gaps = []; for (let i = 1; i < t.length; i++) gaps.push(t[i] - t[i - 1]);
      const recent = gaps.slice(-8).filter(function (g) { return g >= 500; }).sort(function (a, b) { return a - b; });
      if (recent.length) return recent[Math.floor(recent.length / 2)];
    }
    return null;
  };
  FreqTracker.prototype.estimateETA = function () {
    if (!this.total || this.cur < 1 || this.dispDone) return null;
    const remaining = Math.max(this.total - this.cur, 0);
    const perMs = this.perStepMs();
    return { remaining: remaining, etaMs: perMs != null ? remaining * perMs : null };
  };

  // ---- TD-DFT stage tracking ----
  // Same idea as the analytical-frequencies chain: ORCA prints an UPPERCASE
  // banner per pipeline stage, always in the same order (verified on 7 real
  // ORCA 6.1.1 TD-DFT outputs, both full TD-DFT/RPA and TDA/Davidson):
  // XC-kernel setup -> iterative diagonalization (with "****Iteration K****"
  // lines and a fixed "Number of roots" total) -> excited-state analysis ->
  // transition spectra -> final CIS/TD-DFT total energy.
  const TD_STAGES = [
    { key: "setup",   re: /TD-DFT XC SETUP/,                              label: "XC kernel" },
    { key: "solver",  re: /RPA-DIAGONALIZATION|DAVIDSON-DIAGONALIZATION/, label: "Diagonalization" },
    { key: "states",  re: /TD-DFT(?:\/TDA)? EXCITED STATES/,              label: "Excited states" },
    { key: "spectra", re: /ABSORPTION SPECTRUM VIA TRANSITION/,           label: "Transition spectra" },
    { key: "energy",  re: /CIS\/TD-DFT TOTAL ENERGY/,                     label: "Total energy" },
  ];
  const TD_ROOTS_RE = /Number of roots to be determined\s+\.+\s*(\d+)/;
  const TD_ITER_RE = /\*{4}\s*Iteration\s+(\d+)\s*\*{4}/;

  function TddftTracker() {
    this.active = false;   // a TD-DFT stage banner has been seen
    this.aStage = "";      // key of the current TD_STAGES entry ("" = none yet)
    this.aIdx = -1;        // index of aStage in TD_STAGES (-1 = none)
    this.roots = 0;        // excited states requested ("Number of roots ...")
    this.iter = -1;        // latest solver iteration (ORCA counts from 0)
    this.solver = "";      // "RPA" | "DAVIDSON" (which solver banner matched)
    this.finished = false; // ORCA TERMINATED NORMALLY seen — chain complete
    this.stageT = [];      // ms wall clock when each stage index was first entered
    this.subs = [];        // frozen key-result line per stage already left
    this.t0 = 0;           // ms when the chain first advanced (total elapsed)
    this.tEnd = 0;         // ms when the run finished (0 while running)
    this.noTimes = false;  // rebuilt from disk — wall-clock stamps would be meaningless
    this.method = "";      // echoed method ("!"-line, first two tokens)
    this.cores = 0;        // echoed %pal nprocs
  }
  // monotonic, like FreqTracker._advance: a re-seen banner (the spectra stage
  // prints several "... SPECTRUM VIA TRANSITION ..." blocks) never moves back
  TddftTracker.prototype._advance = function (idx) {
    this.active = true;
    if (!this.t0) this.t0 = Date.now();
    if (idx > this.aIdx) {
      if (this.aIdx >= 0) this.subs[this.aIdx] = this._curSub();
      this.aIdx = idx; this.aStage = TD_STAGES[idx].key;
      if (this.stageT[idx] == null) this.stageT[idx] = Date.now();
    }
    return true;
  };
  // the current stage's key-result line (frozen into subs[] when the stage ends)
  TddftTracker.prototype._curSub = function () {
    if (this.aStage === "solver") return this.roots
      ? `${this.solver || "iterative"} · ${this.roots} roots${this.iter >= 0 ? ` · iter ${this.iter}` : ""}`
      : "diagonalizing";
    if (this.aStage === "states" || this.aStage === "spectra" || this.aStage === "energy")
      return this.roots ? `${this.roots} states` : "";
    return "";
  };
  TddftTracker.prototype.push = function (line) {
    _grabOrcaMeta(this, line);
    // same pre-step reset as FreqTracker: an excited-state optimization (raw
    // input) runs the TD-DFT module inside every cycle — show the chain while
    // it runs, clear it when the next cycle starts
    if (PHASE_RESET_RE.test(line)) {
      const was = this.active;
      if (was) {
        this.active = false; this.aStage = ""; this.aIdx = -1;
        this.roots = 0; this.iter = -1; this.solver = ""; this.finished = false;
        this.stageT = []; this.subs = []; this.t0 = 0; this.tEnd = 0;
      }
      return was;
    }
    for (let i = 0; i < TD_STAGES.length; i++) {
      if (TD_STAGES[i].re.test(line)) {
        if (TD_STAGES[i].key === "solver") this.solver = /RPA-DIAGONALIZATION/.test(line) ? "RPA" : "DAVIDSON";
        return this._advance(i);
      }
    }
    if (!this.active) return false;
    const r = line.match(TD_ROOTS_RE);
    if (r) { this.roots = parseInt(r[1], 10); return true; }
    if (this.aStage === "solver") {
      const it = line.match(TD_ITER_RE);
      if (it) { this.iter = parseInt(it[1], 10); return true; }
    }
    if (ATERM_RE.test(line)) { this.finished = true; if (!this.tEnd) this.tEnd = Date.now(); return true; }
    return false;
  };
  TddftTracker.prototype.hasData = function () { return this.active; };

  // ---------- staged-pipeline stepper (CREST / analytical freq / TD-DFT) ----------
  // Vertical stepper: a header (mono title + RUNNING/DONE/STOPPED pill) over a
  // meta line (name · method · cores · elapsed), then one row per stage — dot
  // rail on the left, stage label with its key result under it, the stage's
  // wall time right-aligned. Rows stretch between ROW_MIN and ROW_MAX so the
  // rail follows the window height (opts.height, measured by the caller); when
  // opts.height cannot fit every row without label overlap the compact strip
  // (_stepCompactHtml — the retired HUD frame minus its hazard stripes, same
  // text) is rendered instead. Live clocks (current stage + total elapsed)
  // carry data-clock="<start ms>" and are re-stamped in place by the app's 1s
  // poll, so they keep ticking while the log is silent.
  const STEP_HEAD_PX = 88, STEP_ROW_MIN = 46, STEP_ROW_MAX = 96, STEP_FOOT_PX = 30;

  function _fmtClock(sec) {
    sec = Math.max(0, Math.round(sec));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const ss = (s < 10 ? "0" : "") + s;
    if (!h) return `${m}:${ss}`;
    return `${h}:${m < 10 ? "0" : ""}${m}:${ss}`;
  }

  // per-stage wall seconds from the tracker's stage-entry stamps: a stage ends
  // when the next entered stage begins; the last entered stage ends at tEnd
  // (finish/stop) or keeps running to `now`.
  function _stageSecs(t, n, now) {
    const out = new Array(n).fill(null);
    for (let i = 0; i < n; i++) {
      const ti = t.stageT[i];
      if (ti == null) continue;
      let end = null;
      for (let j = i + 1; j < n; j++) if (t.stageT[j] != null) { end = t.stageT[j]; break; }
      if (end == null) end = t.tEnd || now;
      out[i] = (end - ti) / 1000;
    }
    return out;
  }

  // shared row builder: label + key result (frozen for past stages, live for
  // the current one) + per-stage seconds (suppressed on disk-rebuilt trackers).
  // A stage the chain jumped over — no entry stamp of its own, but a later
  // stage does have one — is flagged `skipped` (a conditional phase that didn't
  // run this time, e.g. CREST's Genetic crossing when there's nothing to cross);
  // it reads as skipped instead of a misleading instant "done".
  function _stepRows(t, stages, state, curSub, now) {
    const n = stages.length;
    const secs = t.noTimes ? new Array(n).fill(null) : _stageSecs(t, n, now);
    let lastStamped = -1;
    for (let i = n - 1; i >= 0; i--) if (t.stageT[i] != null) { lastStamped = i; break; }
    const rows = [];
    for (let i = 0; i < n; i++) {
      const cur = i === t.aIdx;
      const skipped = t.stageT[i] == null && i < lastStamped;
      rows.push({
        label: stages[i].label,
        sub: skipped ? "skipped" : (i < t.aIdx ? (t.subs[i] || "") : (cur ? curSub : "")),
        secs: secs[i],
        live: state === "running" && cur && t.stageT[i] != null,
        startMs: t.stageT[i] || 0,
        skipped: skipped,
      });
    }
    return rows;
  }

  function _stepMetaParts(t) {
    const parts = [];
    if (t.method) parts.push(t.method);
    if (t.solvent) parts.push(t.solvent);      // CREST: --alpb/--gbsa solvent
    if (t.nci) parts.push("NCI mode");         // CREST: --nci ellipsoid potential
    if (t.cores) parts.push(`${t.cores} cores`);
    return parts;
  }

  function _stepElapsed(t, now) {
    if (t.noTimes || !t.t0) return null;
    if (t.tEnd) return { sec: (t.tEnd - t.t0) / 1000, live: false, startMs: 0 };
    return { sec: (now - t.t0) / 1000, live: true, startMs: t.t0 };
  }

  function _stepMetaHtml(m) {
    const parts = m.meta.slice();
    if (m.elapsed) {
      parts.push(`elapsed <span${m.elapsed.live ? ` data-clock="${m.elapsed.startMs}"` : ""}>${_fmtClock(m.elapsed.sec)}</span>`);
    }
    return parts.join(" · ");
  }

  // m: {title, state: "running"|"done"|"error", at (n = all done),
  //     rows: [{label, sub, secs, live, startMs}], meta: string[], elapsed,
  //     foot?: string — one status line under the rows (CREST's CREGEN strip)}
  function _stepPanelHtml(m, opts) {
    const n = m.rows.length;
    const footPx = m.foot ? STEP_FOOT_PX : 0;
    const avail = opts && opts.height ? opts.height : 0;
    if (avail && avail < STEP_HEAD_PX + footPx + n * STEP_ROW_MIN) return _stepCompactHtml(m);
    const style = avail ? ` style="height:${Math.round(Math.min(avail, STEP_HEAD_PX + footPx + n * STEP_ROW_MAX))}px"` : "";
    let pill;
    if (m.state === "done") pill = `<span class="vstep-pill ok">✓ DONE</span>`;
    else if (m.state === "error") pill = `<span class="vstep-pill err">■ STOPPED</span>`;
    else pill = `<span class="vstep-pill ok">▷ RUNNING</span>`;
    let rows = "";
    for (let i = 0; i < n; i++) {
      const r = m.rows[i];
      let cls;
      if (r.skipped) cls = "skipped";
      else if (i < m.at) cls = "done";
      else if (i === m.at) cls = m.state === "error" ? "cur err" : "cur";
      else cls = "pending";
      if (i === 0) cls += " first";
      if (i === n - 1) cls += " last";
      const time = r.secs == null ? "" :
        `<div class="vstep-time"${r.live ? ` data-clock="${r.startMs}"` : ""}>${_fmtClock(r.secs)}</div>`;
      rows +=
        `<div class="vstep-row ${cls}">` +
          `<div class="vstep-rail">` +
            `<span class="vstep-line up${i <= m.at ? " on" : ""}"></span>` +
            `<span class="vstep-dot"></span>` +
            `<span class="vstep-line down${i < m.at ? " on" : ""}"></span>` +
          `</div>` +
          `<div class="vstep-body">` +
            `<div class="vstep-label">${r.label}</div>` +
            (r.sub ? `<div class="vstep-sub">${r.sub}</div>` : "") +
          `</div>` +
          (time || `<div class="vstep-time"></div>`) +
        `</div>`;
    }
    return (
      `<div class="vstep"${style}>` +
        `<div class="vstep-head"><div class="vstep-title">${m.title}</div>${pill}</div>` +
        `<div class="vstep-meta">${_stepMetaHtml(m)}</div>` +
        `<div class="vstep-rows">${rows}</div>` +
        (m.foot ? `<div class="vstep-foot">${m.foot}</div>` : "") +
      `</div>`
    );
  }

  // compact fallback for short windows: the retired HUD frame minus its hazard
  // stripes — centered title, STEP k/n (or DONE/STOPPED) left, the current
  // stage's label + key result center, the dot chain right, meta line below.
  function _stepCompactHtml(m) {
    const n = m.rows.length;
    let dots = "";
    for (let i = 0; i < n; i++) {
      if (i) dots += `<span class="stepc-link${i <= m.at ? " done" : ""}"></span>`;
      dots += `<span class="stepc-dot${i < m.at ? " done" : (i === m.at ? (m.state === "error" ? " err" : " cur") : "")}"></span>`;
    }
    let phase;
    if (m.state === "done") phase = `<div class="stepc-phase">DONE</div>`;
    else if (m.state === "error") phase = `<div class="stepc-phase err">STOPPED</div>`;
    else phase = `<div class="stepc-phase">STEP ${Math.min(m.at + 1, n)}<span class="stepc-of">/${n}</span></div>`;
    const cur = m.rows[Math.min(Math.max(m.at, 0), n - 1)];
    return (
      `<div class="stepc">` +
        `<div class="stepc-title">${m.title}</div>` +
        `<div class="stepc-row">` + phase +
          `<div class="stepc-center">` +
            `<div class="stepc-label">${cur.label}</div>` +
            (cur.sub ? `<div class="stepc-sub">${cur.sub}</div>` : "") +
          `</div>` +
          `<div class="stepc-dots">${dots}</div>` +
        `</div>` +
        `<div class="stepc-meta">${_stepMetaHtml(m)}</div>` +
      `</div>`
    );
  }

  function renderTddftProgress(td, opts) {
    const state = td.finished ? "done" : "running";
    const now = Date.now();
    return _stepPanelHtml({
      title: "TD-DFT excited states",
      state: state,
      at: state === "done" ? TD_STAGES.length : Math.max(td.aIdx, 0),
      rows: _stepRows(td, TD_STAGES, state, td._curSub(), now),
      meta: _stepMetaParts(td),
      elapsed: _stepElapsed(td, now),
    }, opts);
  }

  function _renderAnalyticalPanel(freq, opts) {
    const state = freq.finished ? "done" : "running";
    const now = Date.now();
    return _stepPanelHtml({
      title: "Analytical frequencies",
      state: state,
      at: state === "done" ? A_STAGES.length : Math.max(freq.aIdx, 0),
      rows: _stepRows(freq, A_STAGES, state, freq._curSub(), now),
      meta: _stepMetaParts(freq),
      elapsed: _stepElapsed(freq, now),
    }, opts);
  }

  function renderFreqProgress(freq, opts) {
    if (freq.mode === "analytical") return _renderAnalyticalPanel(freq, opts);
    // floor, not round: progress() caps at 0.999 while the final displacement
    // is still running, and round() displayed a premature "100%" there
    const pct = Math.floor(freq.progress() * 100);
    if (freq.dispDone) {
      return `<div class="scf-prog-label">Frequencies · displacements complete · 100%</div>` +
        `<div class="scf-prog-bar"><span style="width:100%"></span></div>` +
        `<div class="scf-prog-meta">building Hessian + thermochemistry…</div>`;
    }
    const out = [
      `<div class="scf-prog-label">Numerical frequencies ${pct}% · displacement ${freq.cur}/${freq.total}</div>`,
      `<div class="scf-prog-bar"><span style="width:${pct}%"></span></div>`,
    ];
    const perMs = freq.perStepMs();
    const rate = _fmtDuration(perMs);
    const eta = freq.estimateETA();
    // freq ETA is reliable (known total) — show the real time, not a coarse bucket
    if (eta && eta.etaMs != null) {
      out.push(`<div class="scf-prog-meta">~${_fmtDuration(eta.etaMs)} remaining · ${eta.remaining} displacement${eta.remaining === 1 ? "" : "s"} left${rate ? " · ~" + rate + " each" : ""}</div>`);
    } else if (rate) {
      out.push(`<div class="scf-prog-meta">~${rate}/displacement</div>`);
    } else {
      out.push(`<div class="scf-prog-meta">estimating…</div>`);
    }
    return out.join("");
  }

  // ---------- CREST conformer-search progress ----------
  // A CREST iMTD-GC run streams a fixed structure to its .out (verified on a real
  // CREST 3.0.2 run, 663 lines): an initial optimization, then repeated
  // "Meta-Dynamics Iteration N" cycles (each running a known number of MTDs and
  // ending in a CREGEN sort that prints the lowest energy + ensemble size within
  // a kcal window), additional MDs, and a final optimization, before
  // "CREST terminated normally.". Unlike SCF/opt there is NO single monotone
  // convergence quantity and the macro-iteration count is unknown up front, so
  // this tracks a PHASE CHAIN (init -> sampling -> MD -> final) plus — within
  // sampling — the MTD count, the multilevel ensemble-optimization sub-phase
  // ("Optimizing all N structures", crude/tight/very tight — the multi-minute
  // stretch that used to look stalled), and the CREGEN lowest-energy /
  // in-window series appended to the same key-result line; a footer strip
  // appears only once finished (ensemble statistics). NOTE the .out
  // tail delivers complete lines only, so CREST's in-place "|>10.2% ..."
  // percent tokens inside an optimization block are NOT available live — the
  // sub-phase granularity here is the finest the stream supports.
  // The iMTD-GC pipeline in order (verified on real CREST 3.0.2 output):
  // initial opt -> metadynamics sampling -> additional regular MDs -> genetic
  // structure crossing (GC) -> final opt -> final CREGEN ensemble sort. CREGEN
  // sorting also runs after every intermediate optimization block, but the
  // "Final Ensemble Information" block is the terminal, unambiguous one.
  const CREST_STAGES = [
    { key: "init",   re: /Initial Geometry Optimization/,            label: "Initial optimization" },
    { key: "sample", re: /iMTD-GC SAMPLING|Meta-Dynamics Iteration/, label: "Metadynamics sampling" },
    { key: "md",     re: /Additional regular MDs/,                   label: "Regular MD" },
    { key: "gc",     re: /Structure Crossing \(GC\)/,                label: "Genetic crossing (GC)" },
    { key: "final",  re: /Final Geometry Optimization/,              label: "Final optimization" },
    { key: "cregen", re: /Final Ensemble Information/,               label: "Ensemble sorting (CREGEN)" },
  ];
  // CREST echoes its command line ("$ /path/crest input.xyz --gfn2 ... -T 4"):
  // the --gfn* flag and -T carry the stepper meta line's method + core count
  const CREST_CMD_RE = /^\s*\$\s+\S*crest\S*\s+(.+)$/i;
  const CREST_METHOD_NAMES = { gfn2: "GFN2-xTB", gfn1: "GFN1-xTB", gfn0: "GFN0-xTB", gfnff: "GFN-FF" };
  const CREST_ELOW_RE    = /CREGEN>\s*E lowest\s*:\s*(-?\d+\.\d+)/i;
  const CREST_WINDOW_RE  = /(\d+)\s+structures?\s+remain within\s+([\d.]+)\s*kcal/i;
  const CREST_ITER_RE    = /Meta-Dynamics Iteration\s+(\d+)/i;
  const CREST_MTDTOT_RE  = /\((\d+)\s*MTDs\)/;
  // an MTD that "terminated EARLY" still delivered its trajectory — count it,
  // or the batch counter sticks below N/N forever (seen on real 3.0.2 runs)
  const CREST_MTDDONE_RE = /\*MTD\s+\d+\s+(?:completed successfully|terminated EARLY)/i;
  const CREST_DONE_RE    = /CREST terminated normally\./;
  const CREST_ERR_RE     = /Change in topology detected|safety termination of CREST/i;
  const CREST_NCONF_RE   = /number of unique conformers for further calc\s+(\d+)/i;
  // multilevel ensemble optimization (runs after each MTD batch and as the
  // final optimization; line formats verbatim from real CREST 3.0.2 output)
  const CREST_MTDLEN_RE  = /^\s*t\(MTD\)\s*\/\s*ps\s*:\s*([\d.]+)/i;
  const CREST_OPTALL_RE  = /Optimizing all\s+(\d+)\s+structures/i;
  const CREST_OPTCRUDE_RE = /^\s*crude pre-optimization/i;
  const CREST_OPTLVL_RE  = /^\s*optimization with (very tight|tight) thresholds/i;
  // " A new lower conformer was found!" is followed by this line:
  const CREST_IMPROVED_RE = /Improved by\s+[-\d.]+\s*Eh or\s+([-\d.]+)\s*kcal\/mol/i;
  // Final Ensemble Information + Wall Time Summary key stats
  const CREST_POP_RE     = /population of lowest in %\s*:\s*([\d.]+)/i;
  const CREST_GFREE_RE   = /ensemble free energy \(kcal\/mol\)\s*:\s*(-?[\d.]+)/i;
  const CREST_TMTD_RE    = /^\s*Metadynamics \(MTD\)\s+\.\.\..*\(\s*([\d.]+)%\)/;
  const CREST_TOPT_RE    = /^\s*Geometry optimization\s+\.\.\..*\(\s*([\d.]+)%\)/;

  function CrestTracker() {
    this.active = false;    // any CREST progress marker seen
    this.aIdx = -1;         // index into CREST_STAGES (-1 = none yet)
    this.aStage = "";       // key of the current stage
    this.iter = 0;          // latest "Meta-Dynamics Iteration N"
    this.mtdTotal = 0;      // MTDs per iteration (from "(14 MTDs)")
    this.mtdDone = 0;       // MTDs completed in the CURRENT iteration
    this.mtdLen = 0;        // MTD length in ps ("t(MTD) / ps : 17.0")
    this.energies = [];     // CREGEN "E lowest" series (Hartree), one per sort pass
    this.windowCount = 0;   // latest "N structures remain within ... kcal" count
    this.windowKcal = 0;    // that line's kcal/mol window (2×ewin crude, ewin tight)
    this.optTotal = 0;      // ensemble optimization: structures in the running block
    this.optLevel = "";     // "" | "crude" | "tight" | "very tight"
    this.optActive = false; // an ensemble-optimization block is running now
    this.improvedKcal = 0;  // latest "A new lower conformer" improvement (kcal/mol)
    this.pop = 0;           // final "population of lowest in %"
    this.gFree = null;      // final "ensemble free energy (kcal/mol)"
    this.mtdPct = 0;        // Wall Time Summary: % of runtime spent in MTD
    this.optPct = 0;        // Wall Time Summary: % of runtime spent optimizing
    this.nConf = 0;         // final "number of unique conformers for further calc"
    this.finished = false;  // "CREST terminated normally."
    this.error = false;     // a known abort signature (topology change / safety stop)
    this.stageT = [];       // ms wall clock when each stage index was first entered
    this.subs = [];         // frozen key-result line per stage already left
    this.t0 = 0;            // ms when the chain first advanced (total elapsed)
    this.tEnd = 0;          // ms when the run finished/stopped (0 while running)
    this.noTimes = false;   // rebuilt from disk — wall-clock stamps would be meaningless
    this.method = "";       // echoed --gfn* flag, prettified ("GFN2-xTB")
    this.solvent = "";      // echoed --alpb/--gbsa solvent, e.g. "ALPB(acetone)"
    this.nci = false;       // echoed --nci flag (NCI ellipsoid-potential mode)
    this.cores = 0;         // echoed -T thread count
  }
  // monotonic phase advance, like FreqTracker/TddftTracker
  CrestTracker.prototype._advance = function (idx) {
    this.active = true;
    if (!this.t0) this.t0 = Date.now();
    if (idx > this.aIdx) {
      if (this.aIdx >= 0) this.subs[this.aIdx] = this._curSub();
      this.aIdx = idx; this.aStage = CREST_STAGES[idx].key;
      if (this.stageT[idx] == null) this.stageT[idx] = Date.now();
    }
    return true;
  };
  // latest "N in window" text (the kcal width when known)
  CrestTracker.prototype._winTxt = function () {
    return `${this.windowCount} in ${this.windowKcal ? `${this.windowKcal} kcal ` : ""}window`;
  };
  // a running ensemble-optimization block's text, "" when none is running
  CrestTracker.prototype._optTxt = function () {
    if (!this.optActive || !this.optTotal) return "";
    return `optimizing ${this.optTotal} structures${this.optLevel ? ` (${this.optLevel})` : ""}`;
  };
  // the latest CREGEN ensemble state (best E so far, in-window count, newest
  // "new lower conformer" improvement) — appended to the current stage's
  // key-result line so it sits right next to the MTD/opt progress
  CrestTracker.prototype._cregenTxt = function () {
    if (!this.energies.length) return this.windowCount ? this._winTxt() : "";
    const bits = [`E lowest ${Math.min.apply(null, this.energies).toFixed(5)} Eh`];
    if (this.windowCount) bits.push(this._winTxt());
    if (this.improvedKcal) bits.push(`new lowest −${this.improvedKcal.toFixed(2)} kcal/mol`);
    return bits.join(" · ");
  };
  // the current stage's key-result line (frozen into subs[] when the stage ends)
  CrestTracker.prototype._curSub = function () {
    if (this.aStage === "sample") {
      const parts = [`iteration ${this.iter || 1}`];
      const opt = this._optTxt();
      const cg = this._cregenTxt();
      if (opt) parts.push(opt);
      else if (this.mtdTotal && (this.mtdDone < this.mtdTotal || !cg))
        parts.push(`${Math.min(this.mtdDone, this.mtdTotal)}/${this.mtdTotal} MTDs${!cg && this.mtdLen ? ` · ${this.mtdLen} ps each` : ""}`);
      if (cg) parts.push(cg);
      return parts.join(" · ");
    }
    if (this.aStage === "md" || this.aStage === "gc" || this.aStage === "final") {
      const parts = [];
      const opt = this._optTxt();
      if (opt) parts.push(opt);
      const cg = this._cregenTxt();
      if (cg) parts.push(cg);
      return parts.join(" · ");
    }
    return "";
  };
  CrestTracker.prototype.push = function (line) {
    const cmd = line.match(CREST_CMD_RE);
    if (cmd) {
      const gm = cmd[1].match(/--(gfn\S*)/i);
      if (gm) this.method = gm[1].toLowerCase().split("//")
        .map(function (k) { return CREST_METHOD_NAMES[k] || k.toUpperCase(); }).join("//");
      const sv = cmd[1].match(/--(alpb|gbsa)\s+(\S+)/i);
      if (sv) this.solvent = `${sv[1].toUpperCase()}(${sv[2]})`;
      if (/--nci\b/i.test(cmd[1])) this.nci = true;
      const th = cmd[1].match(/-T\s+(\d+)/);
      if (th) this.cores = parseInt(th[1], 10);
      return false;   // meta only — nothing to redraw yet
    }
    if (CREST_ERR_RE.test(line)) { this.error = true; this.active = true; if (!this.tEnd) this.tEnd = Date.now(); return true; }
    if (CREST_DONE_RE.test(line)) { this.finished = true; this.active = true; if (!this.tEnd) this.tEnd = Date.now(); return true; }
    const nc = line.match(CREST_NCONF_RE);
    if (nc) { this.nConf = parseInt(nc[1], 10); this.active = true; return true; }
    const mt = line.match(CREST_MTDTOT_RE);
    if (mt) { this.mtdTotal = parseInt(mt[1], 10); this.active = true; return true; }
    const it = line.match(CREST_ITER_RE);
    if (it) {
      this.iter = parseInt(it[1], 10); this.mtdDone = 0;
      this.optActive = false; this.optTotal = 0; this.optLevel = "";
      return this._advance(1);
    }
    if (CREST_MTDDONE_RE.test(line)) { this.mtdDone++; this.active = true; return true; }
    const ml = line.match(CREST_MTDLEN_RE);
    if (ml) { this.mtdLen = parseFloat(ml[1]); this.active = true; return true; }
    // ensemble-optimization sub-phase: "Optimizing all N structures" opens a
    // block; crude/tight/very-tight name its level. A tight sub-block reuses
    // the structures the crude CREGEN kept, so re-opening after a CREGEN pass
    // adopts the latest in-window count as its total. The block closes on the
    // CREGEN "E lowest" that follows it (and on a new iteration / MTD batch).
    const oa = line.match(CREST_OPTALL_RE);
    if (oa) { this.optTotal = parseInt(oa[1], 10); this.optLevel = ""; this.optActive = true; this.active = true; return true; }
    if (CREST_OPTCRUDE_RE.test(line)) { this.optLevel = "crude"; this.optActive = true; this.active = true; return true; }
    const ol = line.match(CREST_OPTLVL_RE);
    if (ol) {
      this.optLevel = ol[1].toLowerCase();
      if (!this.optActive) { this.optActive = true; if (this.windowCount) this.optTotal = this.windowCount; }
      this.active = true; return true;
    }
    const el = line.match(CREST_ELOW_RE);
    if (el) {
      const e = parseFloat(el[1]);
      if (isFinite(e)) this.energies.push(e);
      this.optActive = false;
      // an "Improved by" note belongs to the pass that just ended — a new
      // CREGEN pass starts fresh, so the note doesn't linger for the whole run
      this.improvedKcal = 0;
      this.active = true; return true;
    }
    const w = line.match(CREST_WINDOW_RE);
    if (w) {
      this.windowCount = parseInt(w[1], 10);
      const k = parseFloat(w[2]);
      if (isFinite(k)) this.windowKcal = k;
      this.active = true; return true;
    }
    const im = line.match(CREST_IMPROVED_RE);
    if (im) { this.improvedKcal = parseFloat(im[1]); this.active = true; return true; }
    const pp = line.match(CREST_POP_RE);
    if (pp) { this.pop = parseFloat(pp[1]); this.active = true; return true; }
    const gf = line.match(CREST_GFREE_RE);
    if (gf) { this.gFree = parseFloat(gf[1]); this.active = true; return true; }
    const tm = line.match(CREST_TMTD_RE);
    if (tm) { this.mtdPct = parseFloat(tm[1]); return false; }   // meta for the done footer
    const to = line.match(CREST_TOPT_RE);
    if (to) { this.optPct = parseFloat(to[1]); return false; }
    for (let i = 0; i < CREST_STAGES.length; i++) {
      if (CREST_STAGES[i].re.test(line)) return this._advance(i);
    }
    return false;
  };
  CrestTracker.prototype.hasData = function () { return this.active; };

  // the footer strip under the CREST stepper — finished runs only: the
  // ensemble statistics + runtime split. The LIVE CREGEN state rides the
  // current stage's key-result line instead (_cregenTxt), next to the
  // MTD/optimization progress it belongs to.
  function _crestFoot(cr) {
    if (!cr.finished) return "";
    const bits = [];
    if (cr.pop) bits.push(`lowest populated ${cr.pop.toFixed(1)}%`);
    if (cr.gFree != null) bits.push(`ensemble ΔG ${cr.gFree.toFixed(2)} kcal/mol`);
    if (cr.mtdPct && cr.optPct) bits.push(`runtime: MTD ${Math.round(cr.mtdPct)}% / opt ${Math.round(cr.optPct)}%`);
    return bits.join(" · ");
  }

  function renderCrestProgress(cr, opts) {
    const state = cr.error ? "error" : cr.finished ? "done" : "running";
    const now = Date.now();
    const curSub = cr.error ? "stopped — check the log (topology change?)" : cr._curSub();
    const rows = _stepRows(cr, CREST_STAGES, state, curSub, now);
    // the search's headline result lands on the final stage once it's done
    if (cr.finished) rows[rows.length - 1].sub = cr.nConf
      ? `${cr.nConf} conformer${cr.nConf === 1 ? "" : "s"}` : "complete";
    return _stepPanelHtml({
      title: "CREST conformer search",
      state: state,
      at: state === "done" ? CREST_STAGES.length : Math.max(cr.aIdx, 0),
      rows: rows,
      meta: _stepMetaParts(cr),
      elapsed: _stepElapsed(cr, now),
      foot: _crestFoot(cr),
    }, opts);
  }

  const api = {
    SCFTracker: SCFTracker,
    GeoTracker: GeoTracker,
    FreqTracker: FreqTracker,
    TddftTracker: TddftTracker,
    CrestTracker: CrestTracker,
    isScfIter: function (line) { return ITER_RE.test(line); },   // is this an SCF iteration row?
    fmtClock: _fmtClock,   // m:ss / h:mm:ss — app.js re-stamps [data-clock] elements with it
    targetFor: targetFor,
    renderSCFProgress: renderSCFProgress,
    renderSCFGraph: renderSCFGraph,
    renderGeoProgress: renderGeoProgress,
    renderGeoGraph: renderGeoGraph,
    renderFreqProgress: renderFreqProgress,
    renderTddftProgress: renderTddftProgress,
    renderCrestProgress: renderCrestProgress,
    setEtaMode: setEtaMode,
    setGeoMode: setGeoMode,
    SCF_TARGETS: SCF_TARGETS,
  };

  // export for browser (global) and node (module.exports) for testing
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.SCFGraph = api;
})(typeof window !== "undefined" ? window : this);
