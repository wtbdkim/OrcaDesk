// @ts-check
"use strict";
/* ============================================================
   ORCAdesk notebook front-end — the stream.

   One cell per calculation, in queue order: what it is, how its convergence is
   going, and what its output says — together, in one place. This is what the
   notebook layout is for. The classic UI asks you to pick a job and then pick
   a view of it (Raw or Graph, one at a time, for one job at a time); here you
   read down the run.

   Cells are built once and updated in place, so a poll never throws away the
   scroll position or a selection inside an output pane.
   ============================================================ */

(function () {

  const TAIL_LINES = 260;

  /** @type {Map<string, HTMLElement>} */
  const _cells = new Map();
  /** @type {Map<string, {lines: string[], file: string, truncated: boolean}>} */
  const _tails = new Map();
  /** @type {Set<string>} */
  const _busy = new Set();

  /** What this calculation has to show, or null. Same choice the classic graph
   *  panel makes — the phase chain is the live edge of a run, the convergence
   *  curve is the story of one that has been going a while — but per cell.
   *  @param {any} c @param {number} px the chart box's width on screen */
  function chartFor(c, px) {
    if (!NB.hasGraph(c.name)) return null;
    const b = NB.trackers(c.name);
    if (b.crest.hasData()) return { t: "Conformers", h: SCFGraph.renderCrestProgress(b.crest, {}) };
    if (b.freq.hasData()) return { t: "Frequencies", h: SCFGraph.renderFreqProgress(b.freq, {}) };
    if (b.tddft.hasData()) return { t: "TD-DFT", h: SCFGraph.renderTddftProgress(b.tddft, {}) };
    // The SVG is drawn at the box's REAL pixel size. A fixed viewBox scaled to
    // fit would scale its stroke widths and type with it — the reason the chart
    // has to be measured before it is drawn, not after.
    const w = Math.max(px, 320);
    const opts = { width: Math.round(w), height: Math.round(w * 0.38) };
    if (b.geo.hasData()) {
      return { t: "Geometry convergence",
               h: `<div style="margin-bottom:8px">${SCFGraph.renderGeoProgress(b.geo)}</div>`
                  + SCFGraph.renderGeoGraph(b.geo, opts) };
    }
    if (b.scf.hasData()) {
      const conv = c.scf_convergence || "TightSCF";
      return { t: "SCF convergence",
               h: `<div style="margin-bottom:8px">${SCFGraph.renderSCFProgress(b.scf, conv)}</div>`
                  + SCFGraph.renderSCFGraph(b.scf, conv, opts) };
    }
    return null;
  }

  /** @param {any} c @returns {HTMLElement} */
  function build(c) {
    const el = NB.el("article", "cell");
    el.dataset.name = c.name;
    el.innerHTML = `
      <div class="cellhead">
        <span class="idx"></span>
        <span class="nm"></span>
        <span class="badge tag bk"></span>
        <span class="acts">
          <span class="badge stt"></span>
          <button class="btn btn-sm btn-ghost" type="button" data-a="inp">.inp</button>
          <button class="btn btn-sm" type="button" data-a="res">Results</button>
        </span>
      </div>
      <div class="cellbody">
        <div class="pane chart" hidden>
          <div class="panehead"><span class="t"></span></div>
          <div class="panebody"></div>
        </div>
        <div class="pane out">
          <div class="panehead"><span class="t">Output</span><span class="sp"></span>
            <span class="meta"></span></div>
          <div class="logbox" role="log"></div>
        </div>
      </div>
      <div class="cellfacts"></div>`;
    el.querySelector('[data-a="inp"]').addEventListener("click", () => NB.results.showInp(c.name));
    el.querySelector('[data-a="res"]').addEventListener("click", () => NB.results.open(c.name));
    return el;
  }

  /** @param {HTMLElement} el @param {any} c @param {number} i */
  function update(el, c, i) {
    const q = (s) => /** @type {any} */ (el.querySelector(s));
    q(".idx").textContent = "[" + (i + 1) + "]";
    q(".nm").textContent = c.name;
    q(".bk").textContent = NB.rail.backendOf(c) + (c.is_raw ? " · raw" : "");

    const stt = q(".stt");
    stt.textContent = c.state;
    stt.className = "badge stt " + c.state;

    // .inp is ORCA-only: an MLIP or CREST calculation produces no ORCA input,
    // so the button would open a preview of something that never ran.
    q('[data-a="inp"]').hidden = NB.rail.backendOf(c) !== "DFT";
    q('[data-a="res"]').hidden = !(c.state === "done" || c.state === "failed");

    // chart — measured, then drawn
    const pane = q(".pane.chart");
    const box = q(".pane.chart .panebody");
    const px = box.clientWidth || Math.round((el.clientWidth || 900) / 2) - 60;
    const ch = chartFor(c, px);
    if (ch) {
      pane.hidden = false;
      q(".pane.chart .panehead .t").textContent = ch.t;
      box.innerHTML = ch.h;
    } else {
      pane.hidden = true;
      box.innerHTML = "";
    }
    q(".cellbody").className = "cellbody" + (ch ? " two" : "");

    // output
    const tail = _tails.get(c.name);
    const log = q(".logbox");
    const meta = q(".out .meta");
    if (tail && tail.lines.length) {
      meta.textContent = tail.file + (tail.truncated ? " · last " + tail.lines.length + " lines" : "");
      const stick = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
      log.textContent = tail.lines.join("\n");
      if (stick || c.state === "running") log.scrollTop = log.scrollHeight;
    } else {
      meta.textContent = "";
      log.innerHTML = `<span class="hint">${
        c.state === "pending" ? "Not started yet."
        : c.state === "blocked" ? "Waiting on another calculation."
        : "No output on disk yet."}</span>`;
    }

    // facts
    const bits = [];
    bits.push(`<span>${NB.esc(c.meta || (c.kind + " · charge " + c.charge + " · mult " + c.multiplicity))}</span>`);
    if (NB.hasGraph(c.name)) {
      const b = NB.trackers(c.name);
      if (b.geo.hasData()) {
        const s = b.geo.criteriaSummary();
        bits.push(`<span>cycles <b>${b.geo.steps.length}</b></span>`);
        if (s && s.total) bits.push(`<span>criteria met <b>${s.met}/${s.total}</b></span>`);
      } else if (b.scf.hasData()) {
        bits.push(`<span>SCF iterations <b>${b.scf.points.length}</b></span>`);
      }
    }
    const quiet = c.state === "done" && /^Completed\b/.test(c.message || "");
    if (c.message && !quiet) {
      bits.push(c.state === "failed"
        ? `<span class="err">⚠ ${NB.esc(c.message)}</span>`
        : `<span>${NB.esc(c.message)}</span>`);
    }
    q(".cellfacts").innerHTML = bits.join("");
  }

  /** Read the output the screen is missing. A finished calculation's tail never
   *  changes, so it is read once; a running one is re-read, which is what makes
   *  the pane a live terminal. @param {any[]} calcs */
  function fetchTails(calcs) {
    calcs.forEach(c => {
      if (c.state === "pending" || c.state === "blocked") return;
      if (_busy.has(c.name)) return;
      if (_tails.has(c.name) && c.state !== "running") return;
      _busy.add(c.name);
      NB.call("get_output_tail", c.name, TAIL_LINES).then(r => {
        _busy.delete(c.name);
        if (!r || !r.ok || !r.lines || !r.lines.length) return;
        _tails.set(c.name, { lines: r.lines, file: r.file || (c.name + ".out"),
                             truncated: !!r.truncated });
        const el = _cells.get(c.name);
        const i = NB.queue.findIndex(x => x.name === c.name);
        if (el && i >= 0) update(el, NB.queue[i], i);
      });
    });
  }

  function render() {
    const host = NB.$("stream");
    if (!host || NB.view !== "jobs") return;
    const calcs = NB.queue;

    if (!calcs.length) {
      _cells.clear();
      host.innerHTML = `<div class="empty">
        <div class="t">Nothing in the queue</div>
        <div class="s">Build a calculation on the left. It appears here as it runs —
          its convergence, its output and what it found, one cell per run.</div>
      </div>`;
      return;
    }
    if (host.querySelector(".empty")) { host.innerHTML = ""; _cells.clear(); }

    const live = new Set();
    calcs.forEach((c, i) => {
      live.add(c.name);
      let cell = _cells.get(c.name);
      if (!cell) { cell = build(c); _cells.set(c.name, cell); }
      if (host.children[i] !== cell) host.insertBefore(cell, host.children[i] || null);
      update(cell, c, i);
    });
    [..._cells.keys()].forEach(n => {
      if (live.has(n)) return;
      const dead = _cells.get(n);
      if (dead && dead.parentNode) dead.parentNode.removeChild(dead);
      _cells.delete(n);
      _tails.delete(n);
    });
    fetchTails(calcs);
  }

  NB.watch(() => render());
  // charts are drawn at their measured pixel width, so a resized window has to
  // redraw them rather than let the browser scale the strokes
  let _rz = 0;
  window.addEventListener("resize", () => {
    clearTimeout(_rz);
    _rz = setTimeout(() => { if (NB.view === "jobs") render(); }, 150);
  });

  NB.stream = { render: render };
})();
