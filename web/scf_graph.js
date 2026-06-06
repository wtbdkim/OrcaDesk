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
  }
  GeoTracker.prototype.push = function (line) {
    // Track the real ORCA optimization cycle number. Steps are keyed by this
    // number so the same cycle's table being seen (or fed) more than once can
    // never inflate the step count — it overwrites instead of appending.
    const gc = line.match(GEO_RE);
    if (gc) { this.curCycle = parseInt(gc[1], 10); return false; }
    if (GEO_TABLE_RE.test(line)) {
      this._inTable = true;
      this._sawItem = false;
      this._pendingCriteria = {};
      this._pendingVals = {};
      return false;
    }
    if (this._inTable) {
      const m = line.match(GEO_ITEM_RE);
      if (m) {
        this._sawItem = true;
        const name = m[1];
        const val = parseFloat(m[2]);
        const tol = parseFloat(m[3]);
        const conv = m[4].toUpperCase() === "YES";
        this._pendingCriteria[name] = conv;
        if (isFinite(val) && isFinite(tol) && tol > 0) this._pendingVals[name] = { val: Math.abs(val), tol: tol };
        if (/MAX gradient/i.test(name) && isFinite(val)) {
          this.tol = tol;
          // key by real cycle number; fall back to a fresh sequential key only
          // if ORCA hasn't printed a cycle header yet (defensive)
          const key = this.curCycle > 0 ? this.curCycle : (this.steps.length + 1);
          const idx = this._byCycle[key];
          if (idx == null) {
            this._byCycle[key] = this.steps.length;
            this.steps.push({ step: key, maxGrad: val, tol: tol });
          } else {
            this.steps[idx].maxGrad = val;   // same cycle re-emitted: overwrite
            this.steps[idx].tol = tol;
          }
          if (this.startGrad === null) this.startGrad = val;
        }
        return true;
      }
      if (this._sawItem && (line.trim() === "" || /-{5,}/.test(line) || /\.{5,}/.test(line))) {
        if (Object.keys(this._pendingCriteria).length) {
          this._criteria = this._pendingCriteria;
          // compute worst-ratio for this step from all criteria present
          let worstLog = -99;
          for (const k in this._pendingVals) {
            const r = Math.log10(Math.max(this._pendingVals[k].val, 1e-12) / this._pendingVals[k].tol);
            if (r > worstLog) worstLog = r;
          }
          if (worstLog > -90) {
            // keep the worst/time series one-per-cycle too, so the ETA estimator
            // isn't fed duplicate points when a cycle's table is re-emitted
            const ckey = this.curCycle > 0 ? this.curCycle : ("seq" + this.worst.length);
            const wi = this._worstByCycle[ckey];
            if (wi == null) {
              this._worstByCycle[ckey] = this.worst.length;
              this.worst.push(worstLog);
              this.stepTimes.push(Date.now());
            } else {
              this.worst[wi] = worstLog;   // overwrite; keep the original stepTime
            }
          }
        }
        this._inTable = false;
        this._sawItem = false;
      }
    }
    return false;
  };
  GeoTracker.prototype.allConverged = function () {
    // true only when every criterion at the latest step is YES (>=4 of them,
    // so we don't report "done" off a partial early table)
    const c = Object.keys(this._criteria).length ? this._criteria : this._pendingCriteria;
    const names = Object.keys(c);
    if (names.length < 4) return false;
    return names.every(function (n) { return c[n]; });
  };
  GeoTracker.prototype.progress = function () {
    if (this.startGrad === null || !this.steps.length) return 0;
    // 100% only when the optimizer has actually met all convergence criteria;
    // otherwise cap at 99% even if MAX gradient alone reached the tolerance
    if (this.allConverged()) return 1;
    const cur = this.steps[this.steps.length - 1].maxGrad;
    const ls = Math.log10(this.startGrad);
    const lt = Math.log10(this.tol);
    const lc = Math.log10(cur);
    if (ls === lt) return 0.99;
    const raw = (ls - lc) / (ls - lt);
    return Math.max(0, Math.min(0.99, raw));
  };
  GeoTracker.prototype.current = function () {
    return this.steps.length ? this.steps[this.steps.length - 1].maxGrad : null;
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
    // median time-per-step from recent measured intervals
    const t = this.stepTimes; let etaMs = null;
    if (t.length >= 3) {
      const gaps = []; for (let i = 1; i < t.length; i++) gaps.push(t[i] - t[i - 1]);
      const recent = gaps.slice(-10).sort(function (a, b) { return a - b; });
      const medGap = recent[Math.floor(recent.length / 2)];
      if (medGap > 0) etaMs = remaining * medGap;
    }
    // conf semantics:
    //  - raw present: "high"/"med"/"low" (a fresh confident estimate)
    //  - raw absent + eager + we have a prior estimate: "held" (keep showing it)
    //  - otherwise: "stale" (caller shows "estimating…")
    let conf;
    if (raw) conf = raw.conf;
    else if (_etaMode === "eager" && this._etaPred != null && at >= minStep) conf = "held";
    else conf = "stale";
    return { remainingSteps: remaining, etaMs: etaMs, conf: conf };
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
  function renderGeoProgress(geo) {
    const p = geo.progress();
    const pct = Math.round(p * 100);
    const cs = geo.criteriaSummary();
    // show the real ORCA cycle number of the latest table, not the array length
    const nPts = geo.steps.length;
    const stepN = nPts ? geo.steps[nPts - 1].step : 0;
    const critLabel = cs.total ? ` · criteria ${cs.met}/${cs.total} met` : "";
    // ETA line (only when the estimator is confident enough)
    let etaLine = "";
    const eta = geo.estimateETA ? geo.estimateETA() : null;
    if (eta && eta.conf !== "stale") {
      const rem = Math.round(eta.remainingSteps);
      const t = _fmtDuration(eta.etaMs);
      const qual = eta.conf === "high" ? "" : " (rough)";
      if (t) etaLine = `<div class="scf-prog-meta">~${t} remaining · about ${rem} more step${rem === 1 ? "" : "s"}${qual}</div>`;
      else etaLine = `<div class="scf-prog-meta">about ${rem} more step${rem === 1 ? "" : "s"}${qual}</div>`;
    } else if (nPts >= 4) {
      etaLine = `<div class="scf-prog-meta">estimating…</div>`;
    }
    return (
      `<div class="scf-prog-label">Optimization ${pct}% · step ${stepN}${critLabel}</div>` +
      `<div class="scf-prog-bar"><span style="width:${pct}%"></span></div>` +
      etaLine
    );
  }

  // x = optimization step, y = MAX gradient (log scale); dashed goal at tol
  function renderGeoGraph(geo, opts) {
    opts = opts || {};
    const W = opts.width || 320;
    const H = opts.height || 180;
    const padL = 58, padR = 14, padT = 14, padB = 40;
    const pts = geo.steps.filter(function (s) { return s.maxGrad > 0; });

    if (pts.length < 1) {
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

    // x-axis tick numbers (real ORCA cycle numbers) — thinned when many
    const baseY = padT + (H - padT - padB);
    const stepEvery = Math.max(1, Math.ceil(xN / 8));
    let xticks = "";
    for (let i = 0; i < pts.length; i += stepEvery) {
      const xx = X(i);
      xticks += `<line x1="${xx.toFixed(1)}" y1="${baseY}" x2="${xx.toFixed(1)}" y2="${(baseY + 4).toFixed(1)}" class="scf-grid"/>`;
      xticks += `<text x="${xx.toFixed(1)}" y="${(baseY + 15).toFixed(1)}" text-anchor="middle" class="scf-axis">${pts[i].step}</text>`;
    }

    // axis titles: y = MAX gradient (what we plot), x = optimization step
    const midY = (padT + (H - padT - padB) / 2).toFixed(1);
    const yTitle = `<text x="14" y="${midY}" text-anchor="middle" class="scf-axis-title" transform="rotate(-90 14 ${midY})">MAX gradient</text>`;
    const xTitle = `<text x="${((padL + W - padR) / 2).toFixed(1)}" y="${H - 4}" text-anchor="middle" class="scf-axis-title">optimization step</text>`;

    return `<svg viewBox="0 0 ${W} ${H}" class="scf-svg" xmlns="http://www.w3.org/2000/svg">
      ${grid}${goal}${line}${startC}${curC}${xticks}${yTitle}${xTitle}</svg>`;
  }

  const api = {
    SCFTracker: SCFTracker,
    GeoTracker: GeoTracker,
    targetFor: targetFor,
    renderSCFProgress: renderSCFProgress,
    renderSCFGraph: renderSCFGraph,
    renderGeoProgress: renderGeoProgress,
    renderGeoGraph: renderGeoGraph,
    setEtaMode: setEtaMode,
    SCF_TARGETS: SCF_TARGETS,
  };

  // export for browser (global) and node (module.exports) for testing
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.SCFGraph = api;
})(typeof window !== "undefined" ? window : this);
