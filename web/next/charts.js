// @ts-check
"use strict";
/* ============================================================
   ORCAdesk notebook front-end — a cell's picture of its run.

   ../scf_graph.js and ../progress_panels.js are used here as PARSERS only.
   Their own render* helpers emit the classic front-end's class vocabulary
   (.scf-line, .vstep-*, .stepc-*), and app.css — which is the design's
   stylesheet — has no rule for any of it: a chart drawn that way had no stroke
   colour and a stage chain no dots. Borrowing a renderer means borrowing its
   stylesheet, and this front-end deliberately does not load the other one.

   So the trackers hand over their numbers and this file draws them in the
   design's vocabulary: `.chart` + `.serieskey` for a converging trace,
   `.stages`/`.stagerow` for a run that is a sequence of named stages, and
   `.progrow`/`.prog`/`.eta` for how far along it is.

   Every SVG is drawn at the box's real pixel width and carries its colours
   inline, so a line is 1.6px at any window width and needs no stylesheet.
   ============================================================ */

/** @type {any} */
var CHARTS = {};

(function () {

  /** @param {string} name @param {string} fallback */
  function token(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v || "").trim() || fallback;
  }

  /** Converging traces on a log axis: the shape every ORCA convergence has.
   *
   *  Several series, not one, because a geometry optimization converges five
   *  things at once and which one is holding it up is the question being asked.
   *  Each is plotted as value / its OWN tolerance, so they share a single goal
   *  line at ratio 1 and "3/5 met" is readable straight off the picture.
   *
   *  Gridlines land on whole decades. They used to be five evenly spaced lines
   *  labelled with a ROUNDED exponent, which on a range of about three decades
   *  printed 1e3 twice and skipped a value that was actually on the axis.
   *
   *  @param {{color: string, pts: {x: number, y: number}[]}[]} series  y in log10
   *  @param {number} w @param {number} h
   *  @param {{xlab: string, goal?: number, goalLabel?: string, done?: boolean}} o */
  function trace(series, w, h, o) {
    const L = 52, R = 14, T = 12, B = 34;
    const iw = Math.max(w - L - R, 40), ih = Math.max(h - T - B, 40);
    const grid = token("--grid", "rgba(161,161,170,.15)");
    const axis = token("--border", "#27272a");
    const mut = token("--muted-foreground", "#a1a1aa");
    const mono = token("--font-mono", "monospace");
    const ok = token("--ok", "#4ade80");

    const ys = series.reduce((a, s) => a.concat(s.pts.map(p => p.y)), []);
    let lo = Math.floor(Math.min.apply(null, ys));
    let hi = Math.ceil(Math.max.apply(null, ys));
    if (o.goal != null) {
      lo = Math.min(lo, Math.floor(o.goal));
      hi = Math.max(hi, Math.ceil(o.goal));
    }
    if (hi - lo < 2) hi = lo + 2;             // a flat trace still gets an axis
    // one label per decade until they would crowd, then every second or third
    const dstep = Math.ceil((hi - lo) / 7) || 1;

    const xs = series.reduce((a, s) => a.concat(s.pts.map(p => p.x)), []);
    const x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
    const sx = (v) => L + (x1 === x0 ? iw / 2 : ((v - x0) / (x1 - x0)) * iw);
    const sy = (v) => T + ih - ((v - lo) / (hi - lo)) * ih;

    let g = "";
    for (let d = lo; d <= hi; d += dstep) {
      const yy = sy(d);
      if (o.goal === d) continue;             // the goal draws its own line below
      g += `<line x1="${L}" y1="${yy.toFixed(1)}" x2="${L + iw}" y2="${yy.toFixed(1)}"
              stroke="${grid}" stroke-width="1"/>`
        + `<text x="${L - 7}" y="${(yy + 3).toFixed(1)}" text-anchor="end" font-size="9"
             font-family="${mono}" fill="${mut}">1e${d}</text>`;
    }
    const goal = o.goal == null ? "" :
      `<line x1="${L}" y1="${sy(o.goal).toFixed(1)}" x2="${L + iw}" y2="${sy(o.goal).toFixed(1)}"
         stroke="${ok}" stroke-width="1" stroke-dasharray="4 3" opacity=".75"/>`
      + `<text x="${L - 7}" y="${(sy(o.goal) + 3).toFixed(1)}" text-anchor="end" font-size="9"
           font-family="${mono}" fill="${ok}">${NB.esc(o.goalLabel || "goal")}</text>`;

    const lines = series.map(s => {
      if (!s.pts.length) return "";
      const d = s.pts.map((p, i) =>
        `${i ? "L" : "M"}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(" ");
      const last = s.pts[s.pts.length - 1];
      return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="1.6"
          stroke-linejoin="round" stroke-linecap="round" opacity=".92"/>`
        + `<circle cx="${sx(last.x).toFixed(1)}" cy="${sy(last.y).toFixed(1)}"
             r="3" fill="${s.color}"/>`;
    }).join("");

    const ticks = [x0, x1].map((v, i) =>
      `<text x="${sx(v).toFixed(1)}" y="${(T + ih + 14).toFixed(1)}"
         text-anchor="${i ? "end" : "start"}" font-size="9" font-family="${mono}"
         fill="${mut}">${Math.round(v)}</text>`).join("");

    return `<div class="chart"><svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}"
        role="img" aria-label="${NB.esc(o.xlab)}">
      ${g}${goal}
      <line x1="${L}" y1="${T}" x2="${L}" y2="${T + ih}" stroke="${axis}" stroke-width="1"/>
      <line x1="${L}" y1="${T + ih}" x2="${L + iw}" y2="${T + ih}" stroke="${axis}" stroke-width="1"/>
      ${lines}
      ${ticks}
      <text x="${(L + iw / 2).toFixed(1)}" y="${h - 3}" text-anchor="middle" font-size="9"
        font-family="${mono}" fill="${mut}">${NB.esc(o.xlab)}</text>
    </svg></div>`;
  }

  /** The legend under a trace: what the line is, and where it has got to.
   *  @param {{color: string, label: string, value: string, state?: string}[]} keys */
  function serieskey(keys) {
    return `<div class="serieskey">${keys.map(k =>
      `<span class="sk"><i style="background:${k.color}"></i>
         <span class="lb">${NB.esc(k.label)}</span>
         <span class="vl${k.state ? " " + k.state : ""}">${NB.esc(k.value)}</span></span>`
    ).join("")}</div>`;
  }

  /** The five geometry criteria ORCA prints each cycle, with the colour each
   *  one is drawn in. The keys are the tracker's (scf_graph.js GEO_CRITERIA);
   *  the tokens exist for exactly this and were going unused while the chart
   *  drew a single line. */
  const GEO_SERIES = [
    { key: "Energy change", label: "ΔE", token: "--crit-de", fallback: "#4cc9f0" },
    { key: "RMS gradient", label: "RMS grad", token: "--crit-rmsg", fallback: "#52b788" },
    { key: "MAX gradient", label: "MAX grad", token: "--crit-maxg", fallback: "#ffd166" },
    { key: "RMS step", label: "RMS step", token: "--crit-rmss", fallback: "#c08eff" },
    { key: "MAX step", label: "MAX step", token: "--crit-maxs", fallback: "#ff8fa3" },
  ];

  /** The chart is drawn at a FIXED height, not at a fraction of its width. It
   *  shares a row with the output pane, whose .logbox tops out at 425px, so a
   *  chart that grew with the window pushed the two panes out of step -- at a
   *  wide window the left column ran 200px past the right one, which is the
   *  misalignment you see rather than any rule about either box. 270 is the
   *  reference's own height for a cell chart.
   *
   *  Measured, not guessed: at 270 the column's content is 345px and with its
   *  own pane head 383, against the 463 the output column can give (a .logbox is
   *  flex:1 up to 425, under a 38px head). The number is written down because it
   *  is a BUDGET -- grow the chart and the two columns stop ending level, which
   *  is the whole reason this is a fixed height and not a proportional one. */
  const CHART_H = 270;

  /** The converging trace for a calculation, or null when it has none yet.
   *  @param {any} b the tracker bundle @param {any} c the queue row
   *
   *  `kind` is which of the two the cell is showing -- the whole optimization,
   *  or the SCF solving right now inside the current cycle. They are the same
   *  question at two scales and the answer is a toggle, not two charts: stacking
   *  them halves both and pushes the column past the output pane beside it.
   *  Falls back to whichever HAS data, so the toggle can be asked for before
   *  there is anything to show.
   *  @param {number} px the box width in device pixels
   *  @param {string} [kind] "geo" (default) or "scf" */
  CHARTS.convergence = function (b, c, px, kind) {
    const w = Math.max(px, 280);
    const h = CHART_H;
    const hasGeo = b.geo.hasData() && b.geo.steps.length > 1;
    const hasScf = b.scf.hasData() && b.scf.points.filter(p => p.dE > 0).length > 1;
    const has = { geo: hasGeo, scf: hasScf };
    const show = (kind === "scf" && hasScf) ? "scf" : (hasGeo ? "geo" : "scf");
    if (show === "geo" && hasGeo) {
      // Each criterion as value / its OWN tolerance, so one goal line at ratio 1
      // serves all five and "n/5 met" is the count of lines at or below it.
      const series = [], keys = [];
      GEO_SERIES.forEach(g => {
        const color = token(g.token, g.fallback);
        const pts = [];
        b.geo.steps.forEach((s, i) => {
          const v = s.vals ? s.vals[g.key] : (g.key === "MAX gradient" ? s.maxGrad : null);
          const tol = s.tols ? s.tols[g.key] : null;
          if (v != null && v > 0 && isFinite(v) && tol != null && tol > 0) {
            pts.push({ x: i + 1, y: Math.log10(v / tol) });
          }
        });
        if (!pts.length) return;
        series.push({ color: color, pts: pts });
        const last = pts[pts.length - 1];
        keys.push({ color: color, label: g.label,
                    value: Math.pow(10, last.y).toPrecision(2) + "×",
                    state: last.y <= 0 ? "met" : "pend" });
      });
      if (!series.length) return null;
      const s = b.geo.criteriaSummary();
      const eta = b.geo.estimateETA && b.geo.estimateETA();
      return {
        has: has, kind: "geo",
        title: "Geometry convergence",
        meta: b.geo.steps.length + (b.geo.steps.length === 1 ? " cycle" : " cycles")
          + (s && s.total ? ` · ${s.met}/${s.total} met` : ""),
        html: trace(series, w, h, { xlab: "optimization cycle", goal: 0,
                                    goalLabel: "limit", done: b.geo.done })
          + serieskey(keys),
        eta: eta && eta.etaMs ? eta : null,
        progress: b.geo.progress(),
      };
    }
    if (hasScf) {
      const pts = b.scf.points
        .filter(p => p.dE > 0)
        .map(p => ({ x: p.iter, y: Math.log10(p.dE) }));
      const target = SCFGraph.targetFor(c.scf_convergence || "TightSCF");
      const cur = pts[pts.length - 1];
      const warn = token("--warn", "#fbbf24");
      return {
        has: has, kind: "scf",
        // named for WHICH SCF it is: during an optimization this is the cycle
        // running right now, not the whole run
        title: hasGeo ? "SCF, this cycle" : "SCF convergence",
        meta: b.scf.points.length + " iterations",
        html: trace([{ color: warn, pts: pts }], w, h,
                    { xlab: "SCF iteration", goal: Math.log10(target),
                      goalLabel: "target" })
          + serieskey([
              { color: warn, label: "|ΔE|",
                value: Math.pow(10, cur.y).toExponential(1),
                state: cur.y <= Math.log10(target) ? "met" : "pend" },
            ]),
        progress: b.scf.progress(target),
      };
    }
    return null;
  };


  /* A run that is a sequence of named stages rather than a converging trace.
     The labels are the backends' own (progress_panels.js A_STAGES / the TD-DFT
     and CREST chains); the detail under each is read off the tracker. */
  const FREQ_STAGES = [
    ["integrals", "Derivative integrals"], ["cpscf", "CP-SCF response"],
    ["hessian", "SCF Hessian"], ["freq", "Frequencies"], ["modes", "Normal modes"],
    ["ir", "IR spectrum"], ["thermo", "Thermochemistry"],
  ];
  const TDDFT_STAGES = [
    ["setup", "XC kernel"], ["solver", "Diagonalization"], ["states", "Excited states"],
    ["spectra", "Transition spectra"], ["energy", "Total energy"],
  ];
  const CREST_STAGES = [
    ["init", "Initial optimization"], ["sample", "Metadynamics sampling"],
    ["md", "Regular MD"], ["gc", "Genetic crossing (GC)"],
    ["final", "Final optimization"], ["cregen", "Ensemble sorting (CREGEN)"],
  ];

  /** @param {string} title @param {[string, string][]} stages @param {number} at
   *  @param {boolean} finished @param {(key: string) => string} detail */
  function chain(title, stages, at, finished, detail) {
    const rows = stages.map((s, i) => {
      const state = finished || i < at ? "done" : i === at ? "cur" : "pending";
      const d = detail(s[0]);
      return `<div class="stagerow ${state}">
        <span class="sdot"></span>
        <span class="stext"><span class="sl">${NB.esc(s[1])}</span>${
          d ? `<span class="sd">${NB.esc(d)}</span>` : ""}</span>
      </div>`;
    }).join("");
    return `<div class="stages">
      <div class="stagestitle">${NB.esc(title)}</div>${rows}</div>`;
  }

  /** The stage chain for a calculation, or null when it is not running one.
   *  @param {any} b the tracker bundle */
  CHARTS.stages = function (b) {
    const f = b.freq, t = b.tddft, cr = b.crest;
    if (f && f.hasData()) {
      if (f.mode === "numerical") {
        const pct = f.total ? Math.round((f.cur / f.total) * 100) : 0;
        return { title: "Numerical frequencies",
                 html: chain("Numerical frequencies",
                             [["disp", "Displacements"], ["freq", "Frequencies"]],
                             f.dispDone ? 1 : 0, !!f.finished,
                             k => k === "disp"
                               ? `${f.cur}/${f.total} · ${pct}%` : "") };
      }
      return { title: "Analytical frequencies",
               html: chain("Analytical frequencies", FREQ_STAGES,
                           Math.max(f.aIdx, 0), !!f.finished, k => {
        if (k === "integrals") return f.nuclei
          ? (f.atomsDone ? `${f.atomsDone}/${f.nuclei} nuclei` : `${f.nuclei} nuclei`) : "";
        if (k === "cpscf") return f.perturbTotal
          ? `${f.perturbDone}/${f.perturbTotal} perturbations${
              f.cpscfIter ? " · iter " + f.cpscfIter : ""}` : "";
        if (k === "hessian") return f.nuclei ? `${f.nuclei * 3}×${f.nuclei * 3}` : "";
        if (k === "thermo") return f.tempK ? `${f.tempK} K` : "";
        return "";
      }) };
    }
    if (t && t.hasData()) {
      return { title: "TD-DFT",
               html: chain("TD-DFT", TDDFT_STAGES, Math.max(t.aIdx, 0), !!t.finished,
                           () => "") };
    }
    if (cr && cr.hasData()) {
      return { title: "Conformer search",
               html: chain("Conformer search", CREST_STAGES, Math.max(cr.aIdx, 0),
                           !!cr.finished, () => "") };
    }
    return null;
  };

  /** How far along, as the design draws it: a bar, a percentage, and the
   *  estimate under it when there is one worth stating.
   *  @param {number|null} frac @param {any} [eta] */
  CHARTS.progress = function (frac, eta) {
    if (frac == null) return "";
    const pct = Math.round(Math.max(0, Math.min(1, frac)) * 100);
    const line = (eta && eta.etaMs)
      ? `<p class="eta">about ${SCFGraph.fmtClock(Math.round(eta.etaMs / 1000))} left${
          eta.conf === "low" || eta.conf === "held" ? " — a rough estimate" : ""}</p>`
      : "";
    return `<div class="progrow"><span class="prog"><span style="width:${pct}%"></span></span>
      <span class="pv">${pct}%</span></div>${line}`;
  };
})();
