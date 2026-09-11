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

  /** A converging trace on a log axis: the shape every ORCA convergence has.
   *  @param {{x: number, y: number}[]} pts  y already in log10
   *  @param {number} w @param {number} h
   *  @param {{xlab: string, ylab: string, goal?: number, done?: boolean}} o */
  function trace(pts, w, h, o) {
    const L = 52, R = 14, T = 10, B = 34;
    const iw = Math.max(w - L - R, 40), ih = Math.max(h - T - B, 40);
    const grid = token("--border", "#27272a");
    const mut = token("--muted-foreground", "#a1a1aa");
    const mono = token("--font-mono", "monospace");
    const warn = token("--warn", "#fbbf24");
    const ok = token("--ok", "#4ade80");
    const line = o.done ? ok : warn;

    const ys = pts.map(p => p.y);
    let y0 = Math.min(...ys), y1 = Math.max(...ys);
    if (o.goal != null) { y0 = Math.min(y0, o.goal); y1 = Math.max(y1, o.goal); }
    if (y1 - y0 < 1) { const c = (y0 + y1) / 2; y0 = c - 0.5; y1 = c + 0.5; }
    const pad = (y1 - y0) * 0.12; y0 -= pad; y1 += pad;
    const x0 = pts[0].x, x1 = pts[pts.length - 1].x;
    const sx = (v) => L + (x1 === x0 ? iw / 2 : ((v - x0) / (x1 - x0)) * iw);
    const sy = (v) => T + ih - ((v - y0) / (y1 - y0)) * ih;

    let g = "";
    for (let i = 0; i <= 4; i++) {
      const yy = T + (ih * i) / 4;
      const v = y1 - ((y1 - y0) * i) / 4;
      g += `<line x1="${L}" y1="${yy.toFixed(1)}" x2="${L + iw}" y2="${yy.toFixed(1)}"
              stroke="${grid}" stroke-width="1"/>`
        + `<text x="${L - 7}" y="${(yy + 3).toFixed(1)}" text-anchor="end" font-size="9"
             font-family="${mono}" fill="${mut}">1e${Math.round(v)}</text>`;
    }
    const goal = o.goal == null ? "" :
      `<line x1="${L}" y1="${sy(o.goal).toFixed(1)}" x2="${L + iw}" y2="${sy(o.goal).toFixed(1)}"
         stroke="${ok}" stroke-width="1" stroke-dasharray="4 3" opacity=".75"/>`;
    const d = pts.map((p, i) =>
      `${i ? "L" : "M"}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(" ");
    const last = pts[pts.length - 1];
    const ticks = [x0, x1].map((v, i) =>
      `<text x="${sx(v).toFixed(1)}" y="${(T + ih + 14).toFixed(1)}"
         text-anchor="${i ? "end" : "start"}" font-size="9" font-family="${mono}"
         fill="${mut}">${Math.round(v)}</text>`).join("");

    return `<div class="chart"><svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}"
        role="img" aria-label="${NB.esc(o.ylab)} against ${NB.esc(o.xlab)}">
      ${g}${goal}
      <line x1="${L}" y1="${T}" x2="${L}" y2="${T + ih}" stroke="${grid}" stroke-width="1"/>
      <line x1="${L}" y1="${T + ih}" x2="${L + iw}" y2="${T + ih}" stroke="${grid}" stroke-width="1"/>
      <path d="${d}" fill="none" stroke="${line}" stroke-width="1.6"
        stroke-linejoin="round" stroke-linecap="round"/>
      <circle cx="${sx(last.x).toFixed(1)}" cy="${sy(last.y).toFixed(1)}" r="3" fill="${line}"/>
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

  /** The converging trace for a calculation, or null when it has none yet.
   *  @param {any} b the tracker bundle @param {any} c the queue row
   *  @param {number} px the box width in device pixels */
  CHARTS.convergence = function (b, c, px) {
    const w = Math.max(px, 280);
    const h = Math.round(w * 0.52);
    if (b.geo.hasData() && b.geo.worst.length > 1) {
      // worst-ratio per cycle: log10(max(value / tolerance)). Below 0 is met,
      // which is why the goal line sits there and needs no separate legend.
      const pts = b.geo.worst.map((y, i) => ({ x: i + 1, y: y }));
      const s = b.geo.criteriaSummary();
      const met = s && s.total ? `${s.met}/${s.total} met` : "";
      const eta = b.geo.estimateETA && b.geo.estimateETA();
      return {
        title: "Geometry convergence",
        meta: b.geo.steps.length + (b.geo.steps.length === 1 ? " cycle" : " cycles"),
        html: trace(pts, w, h, { xlab: "optimization cycle", ylab: "worst ratio",
                                 goal: 0, done: b.geo.done })
          + serieskey([
              { color: b.geo.done ? token("--ok", "#4ade80") : token("--warn", "#fbbf24"),
                label: "worst criterion", value: met || "—",
                state: b.geo.done ? "met" : "pend" },
            ]),
        eta: eta && eta.etaMs ? eta : null,
        progress: b.geo.progress(),
      };
    }
    if (b.scf.hasData() && b.scf.points.length > 1) {
      const pts = b.scf.points
        .filter(p => p.dE > 0)
        .map(p => ({ x: p.iter, y: Math.log10(p.dE) }));
      if (pts.length < 2) return null;
      const target = SCFGraph.targetFor(c.scf_convergence || "TightSCF");
      const cur = pts[pts.length - 1];
      return {
        title: "SCF convergence",
        meta: b.scf.points.length + " iterations",
        html: trace(pts, w, h, { xlab: "SCF iteration", ylab: "|ΔE|",
                                 goal: Math.log10(target) })
          + serieskey([
              { color: token("--warn", "#fbbf24"), label: "|ΔE|",
                value: Math.pow(10, cur.y).toExponential(1),
                state: cur.y <= Math.log10(target) ? "met" : "pend" },
              { color: token("--ok", "#4ade80"), label: "target",
                value: target.toExponential(0) },
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
