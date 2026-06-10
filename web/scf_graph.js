/* ============================================================
   scf_graph.js — shared by the desktop app (web/) and the mobile
   PWA (web_mobile/). Parses streaming ORCA log lines for SCF
   convergence, tracks progress vs the start point, and renders a
   small SVG convergence graph + a progress bar.

   Pure-ish: SCFTracker has no DOM deps and is unit-testable in node.
   renderSCFGraph()/renderSCFProgress() build HTML/SVG strings.
   ============================================================ */
(function (global) {
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
    MediumSCF: 1e-7,
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
  // a post-optimization stage starting (so we can say "running frequencies/…")
  const POST_OPT_RE = /VIBRATIONAL FREQUENCIES|ORCA SCF RESPONSE|GEOMETRIC PERTURBATIONS|CP-?SCF DRIVER|SCF HESSIAN|ANALYTICAL FREQUENCIES|NUMERICAL FREQUENCIES/i;

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
    for (let i = 1; i < n; i++) { const d = y[i - 1] - y[i]; ema = ema == null ? d : a * d + (1 - a) * ema; }
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

  // progress bar HTML
  function renderSCFProgress(tracker, scfConv) {
    const target = targetFor(scfConv);
    const p = tracker.progress(target);
    const pct = Math.round(p * 100);
    const stepLabel = tracker.step > 0 ? `Geometry step ${tracker.step} · ` : "";
    return (
      `<div class="scf-prog-label">${stepLabel}SCF convergence ${pct}%</div>` +
      `<div class="scf-prog-bar"><span style="width:${pct}%"></span></div>`
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
          waiting for SCF data…</text></svg>`;
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
      xticks += `<text x="${xx.toFixed(1)}" y="${(baseY + 15).toFixed(1)}" text-anchor="middle" class="scf-axis">${i + 1}</text>`;
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
  function renderGeoProgress(geo) {
    const p = geo.progress();
    const pct = Math.round(p * 100);
    const cs = geo.criteriaSummary();
    // show the real ORCA cycle number of the latest table, not the array length
    const nPts = geo.steps.length;
    const stepN = nPts ? geo.steps[nPts - 1].step : 0;

    // optimization finished -> jump to 100% and announce the next stage, instead
    // of staying stuck at 99% (the last criteria table can read 4/5 met)
    if (geo.done) {
      const tail = geo.postStage ? ` — running ${geo.postStage}…` : "";
      return (
        `<div class="scf-prog-label">Optimization complete · 100% · step ${stepN}</div>` +
        `<div class="scf-prog-bar"><span style="width:100%"></span></div>` +
        `<div class="scf-prog-meta">✓ geometry converged${tail}</div>`
      );
    }

    // accurate, real signals first: criteria met + measured per-step rate
    const facts = [];
    if (cs.total) facts.push(`${cs.met}/${cs.total} criteria met`);
    const perMs = geo.perStepMs ? geo.perStepMs() : null;
    const rate = _fmtDuration(perMs);
    if (rate) facts.push(`~${rate}/step`);
    const factLine = facts.length ? `<div class="scf-prog-meta">${facts.join(" · ")}</div>` : "";

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
    const zone = `<rect x="${padL}" y="${goalY.toFixed(1)}" width="${(W - padR - padL).toFixed(1)}" height="${(baseY - goalY).toFixed(1)}" fill="#52b788" opacity="0.07"/>`;
    const goal =
      `<line x1="${padL}" y1="${goalY.toFixed(1)}" x2="${W - padR}" y2="${goalY.toFixed(1)}" class="scf-goal" stroke-width="1.1" stroke-dasharray="5 3"/>` +
      `<text x="${padL - 6}" y="${(goalY + 3).toFixed(1)}" text-anchor="end" class="scf-goal-label">1</text>` +
      // left-aligned so the descending convergence lines (which crowd the right
      // edge near the goal as it converges) don't cover the label
      `<text x="${(padL + 5).toFixed(1)}" y="${(goalY - 4).toFixed(1)}" text-anchor="start" class="scf-axis">converged ≤ 1</text>`;

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
  const NFREQ_START_RE = /ORCA NUMERICAL FREQUENCIES/i;
  const NDISP_RE = /Number of displacements\s+\.*\s*(\d+)/i;
  const DISP_RE = /displacement\s+(\d+)\s*\/\s*(\d+)/i;   // numerical: "...for displacement K / N"
  const VFREQ_RE = /VIBRATIONAL FREQUENCIES/i;
  // analytical Hessian via coupled-perturbed SCF (CP-SCF). Not a black box: ORCA
  // builds nuclear-perturbation ("geometric") integrals, then solves the CP-SCF
  // response over 3N perturbations, printing "K / N done" each iteration — so we
  // can show a real progress bar (verified against a 58-atom M06-2X/CPCM run).
  const AINTEG_RE = /GEOMETRIC PERTURBATIONS/i;                            // derivative-integral build
  const ACPSCF_RE = /ORCA SCF RESPONSE CALCULATION|SHARK CP-?SCF DRIVER/i; // CP-SCF response solve
  const AFREQ_RE = /\bSCF HESSIAN\b|ANALYTICAL FREQUENC|Analytic(?:al)? Hessian/i;  // other analytical markers
  const NPERT_RE = /Number of perturbations\s+\.*\s*(\d+)/i;               // CP-SCF total (= 3N)
  const CPSCF_DONE_RE = /(\d+)\s*\/\s*(\d+)\s+done/i;                      // "K/N done" per CP-SCF iter

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
    this.aStage = "";      // analytical: "integrals" | "cpscf"
  }
  FreqTracker.prototype.push = function (line) {
    if (VFREQ_RE.test(line)) { if (this.active) this.dispDone = true; return false; }
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
    // analytical (CP-SCF) — two trackable stages: derivative-integral build,
    // then the CP-SCF response solve with a "K / N done" perturbation counter
    if (AINTEG_RE.test(line)) { this.mode = "analytical"; this.active = true; this.aStage = "integrals"; return true; }
    if (ACPSCF_RE.test(line)) { this.mode = "analytical"; this.active = true; this.aStage = "cpscf"; return true; }
    if (this.mode === "analytical") {
      const np = line.match(NPERT_RE);
      if (np) { this.perturbTotal = parseInt(np[1], 10); return true; }
      const cd = line.match(CPSCF_DONE_RE);
      if (cd) {
        this.perturbDone = parseInt(cd[1], 10);
        if (!this.perturbTotal) this.perturbTotal = parseInt(cd[2], 10);
        this.cpscfIter++; this.aStage = "cpscf";
        return true;
      }
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
  function renderFreqProgress(freq) {
    // analytical Hessian (CP-SCF): show the perturbation counter when solving,
    // a stage label while building integrals
    if (freq.mode === "analytical") {
      if (freq.dispDone) {
        return `<div class="scf-prog-label">Analytical frequencies · Hessian complete · 100%</div>` +
          `<div class="scf-prog-bar"><span style="width:100%"></span></div>` +
          `<div class="scf-prog-meta">✓ diagonalizing + thermochemistry…</div>`;
      }
      if (freq.perturbTotal && freq.perturbDone) {
        const apct = Math.round(freq.progress() * 100);
        return `<div class="scf-prog-label">Analytical freq · CP-SCF ${apct}% · ${freq.perturbDone}/${freq.perturbTotal} perturbations</div>` +
          `<div class="scf-prog-bar"><span style="width:${apct}%"></span></div>` +
          `<div class="scf-prog-meta">coupled-perturbed SCF, iter ${freq.cpscfIter} · K/N is non-linear in time, so no ETA</div>`;
      }
      const what = freq.aStage === "cpscf" ? "coupled-perturbed SCF response" : "nuclear-perturbation (derivative) integrals";
      return `<div class="scf-prog-label">Analytical frequencies</div>` +
        `<div class="scf-prog-meta">building ${what}… (single analytical Hessian — a long step with no per-unit progress yet)</div>`;
    }
    const pct = Math.round(freq.progress() * 100);
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

  const api = {
    SCFTracker: SCFTracker,
    GeoTracker: GeoTracker,
    FreqTracker: FreqTracker,
    isScfIter: function (line) { return ITER_RE.test(line); },   // is this an SCF iteration row?
    targetFor: targetFor,
    renderSCFProgress: renderSCFProgress,
    renderSCFGraph: renderSCFGraph,
    renderGeoProgress: renderGeoProgress,
    renderGeoGraph: renderGeoGraph,
    renderFreqProgress: renderFreqProgress,
    setEtaMode: setEtaMode,
    setGeoMode: setGeoMode,
    SCF_TARGETS: SCF_TARGETS,
  };

  // export for browser (global) and node (module.exports) for testing
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.SCFGraph = api;
})(typeof window !== "undefined" ? window : this);
