// @ts-check
"use strict";
/* ============================================================
   ORCAdesk notebook front-end — the stream.

   One cell per calculation, in queue order: the structure it is working on,
   how its convergence is going, and what its output says — together, in one
   place. This is what the notebook layout is for. The classic UI asks you to
   pick a job and then pick a view of it (Raw or Graph, one at a time, for one
   job at a time); here you read down the run.

   The cell is the approved design's three-column row: a 220px structure
   thumbnail (236px when the run is a stage chain rather than a curve), the
   chart at 1.5fr, the output at 1fr. Cells are built once and updated in
   place, so a poll never throws away a scroll position or a selection inside
   an output pane.
   ============================================================ */

(function () {

  const TAIL_LINES = 260;

  /** @type {Map<string, HTMLElement>} */
  const _cells = new Map();
  /** @type {Map<string, {lines: string[], file: string, truncated: boolean}>} */
  const _tails = new Map();
  /** @type {Set<string>} */
  const _busy = new Set();
  /** geometry per calc, for the thumbnail @type {Map<string, any[]>} */
  const _geom = new Map();
  /** @type {Set<string>} */
  const _geomBusy = new Set();

  /** What this calculation has to show in the middle column, or null. Same
   *  choice the classic graph panel makes — the phase chain is the live edge of
   *  a run, the convergence curve is the story of one that has been going a
   *  while — but per cell. @param {any} c @param {number} px the box width */
  function chartFor(c, px) {
    if (!NB.hasGraph(c.name)) return null;
    const b = NB.trackers(c.name);
    if (b.crest.hasData()) return { t: "Conformers", stages: true, h: SCFGraph.renderCrestProgress(b.crest, {}) };
    if (b.freq.hasData()) return { t: "Frequencies", stages: true, h: SCFGraph.renderFreqProgress(b.freq, {}) };
    if (b.tddft.hasData()) return { t: "TD-DFT", stages: true, h: SCFGraph.renderTddftProgress(b.tddft, {}) };
    // The SVG is drawn at the box's REAL pixel size. A fixed viewBox scaled to
    // fit would scale its stroke widths and type with it — the reason the chart
    // has to be measured before it is drawn, not after.
    const w = Math.max(px, 320);
    const opts = { width: Math.round(w), height: Math.round(w * 0.52) };
    if (b.geo.hasData()) {
      return { t: "Geometry convergence",
               h: `<div style="margin-bottom:10px">${SCFGraph.renderGeoProgress(b.geo)}</div>`
                  + SCFGraph.renderGeoGraph(b.geo, opts) };
    }
    if (b.scf.hasData()) {
      const conv = c.scf_convergence || "TightSCF";
      return { t: "SCF convergence",
               h: `<div style="margin-bottom:10px">${SCFGraph.renderSCFProgress(b.scf, conv)}</div>`
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
        <span class="nmwrap">
          <span class="nm"></span>
          <button class="btn btn-xs btn-ghost cellinp" type="button"
                  title="Show the input this calculation ran">.inp</button>
        </span>
        <span class="acts">
          <span class="badge"></span>
          <button class="btn btn-sm cellres" type="button">Results</button>
        </span>
      </div>
      <div class="cellbody">
        <div class="celgrid">
          <div class="firstcol">
            <div class="thumb">
              <canvas aria-label="Structure, ball and stick"></canvas>
              <span class="cap"></span>
            </div>
          </div>
          <div class="pane chart">
            <div class="panehead"><span class="t"></span><span class="sp"></span>
              <span class="meta"></span></div>
            <div class="chartbox"></div>
          </div>
          <div class="pane out">
            <div class="panehead"><span class="t">Output</span><span class="sp"></span>
              <span class="meta"></span></div>
            <div class="logbox" role="log"></div>
          </div>
        </div>
      </div>
      <div class="factline"></div>`;
    el.querySelector(".cellinp").addEventListener("click", () => NB.results.showInp(c.name));
    el.querySelector(".cellres").addEventListener("click", () => NB.results.open(c.name));
    return el;
  }

  /** @param {HTMLElement} el @param {any} c @param {number} i */
  function update(el, c, i) {
    const q = (s) => /** @type {any} */ (el.querySelector(s));
    q(".idx").textContent = "[" + (i + 1) + "]";
    q(".nm").textContent = c.name;

    const badge = q(".acts .badge");
    badge.textContent = c.state;
    badge.className = "badge " + c.state;

    el.className = "cell" + (c.state === "running" ? " live"
                           : c.state === "failed" ? " dead" : "");

    // .inp is ORCA-only: an MLIP or CREST calculation produces no ORCA input,
    // so the button would open a preview of something that never ran.
    q(".cellinp").hidden = NB.rail.backendOf(c) !== "DFT";
    q(".cellres").hidden = !(c.state === "done" || c.state === "failed");

    // --- middle column: the chart, measured then drawn
    const box = q(".chartbox");
    const px = box.clientWidth || Math.round((el.clientWidth || 1024) * 0.42);
    const ch = chartFor(c, px);
    const grid = q(".celgrid");
    if (ch) {
      q(".pane.chart").hidden = false;
      q(".pane.chart .panehead .t").textContent = ch.t;
      box.innerHTML = ch.h;
    } else {
      q(".pane.chart").hidden = true;
      box.innerHTML = "";
    }

    // --- first column: the structure, or the stage chain's own width
    const geom = _geom.get(c.name);
    const thumb = q(".thumb");
    const hasThumb = !!(geom && geom.length);
    thumb.hidden = !hasThumb;
    q(".firstcol").hidden = !hasThumb;
    grid.className = "celgrid"
      + (hasThumb ? (ch && ch.stages ? " has-stages" : " has-thumb") : "");
    if (hasThumb) {
      q(".thumb .cap").textContent =
        (c.state === "done" ? "final · " : "input · ") + geom.length + " atoms";
      MOL.draw(q(".thumb canvas"), geom);
    }

    // --- right column: the output
    const tail = _tails.get(c.name);
    const log = q(".logbox");
    const meta = q(".out .meta");
    if (tail && tail.lines.length) {
      meta.textContent = tail.file + (tail.truncated ? " · last " + tail.lines.length + " lines" : "");
      const stick = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
      log.innerHTML = tail.lines.map(l => `<div class="l-orca">${NB.esc(l)}</div>`).join("");
      if (stick || c.state === "running") log.scrollTop = log.scrollHeight;
    } else {
      meta.textContent = "";
      log.innerHTML = `<div class="l-info">${
        c.state === "pending" ? "Not started yet."
        : c.state === "blocked" ? "Waiting on another calculation."
        : "No output on disk yet."}</div>`;
    }

    // --- facts
    const bits = [];
    bits.push(`<span>${NB.esc(c.kind)}</span>`);
    bits.push(`<span>charge <b>${c.charge}</b></span>`);
    bits.push(`<span>mult <b>${c.multiplicity}</b></span>`);
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
        ? `<span class="err">${NB.esc(c.message)}</span>`
        : `<span>${NB.esc(c.message)}</span>`);
    }
    q(".factline").innerHTML = bits.join("");
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
        repaintOne(c.name);
      });
    });
  }

  /** The geometry the thumbnail draws: the finished structure where there is
   *  one, else the input the calculation was given. @param {any[]} calcs */
  function fetchGeometry(calcs) {
    calcs.forEach(c => {
      if (_geom.has(c.name) || _geomBusy.has(c.name)) return;
      if (String(c.kind).startsWith("crest")) return;   // an ensemble, not one structure
      _geomBusy.add(c.name);
      (async () => {
        let atoms = null;
        if (c.state === "done") {
          const d = await NB.call("parse_calc_output", c.name);
          if (d && d.geometry && d.geometry.length) atoms = d.geometry;
        }
        if (!atoms) {
          const g = await NB.call("get_calc", c.name);
          if (g && g.ok && g.calc && g.calc.xyz) atoms = MOL.parseXyz(g.calc.xyz);
        }
        _geomBusy.delete(c.name);
        if (!atoms || !atoms.length) { _geom.set(c.name, []); return; }
        _geom.set(c.name, atoms);
        repaintOne(c.name);
      })();
    });
  }

  /** @param {string} name */
  function repaintOne(name) {
    const el = _cells.get(name);
    const i = NB.queue.findIndex(x => x.name === name);
    if (el && i >= 0) update(el, NB.queue[i], i);
  }

  /** The invitation at the end of the stream. It is a cell because that is
   *  what the next calculation will be. @returns {HTMLElement} */
  function newStub() {
    const el = NB.el("article", "cell stub");
    el.innerHTML = `
      <div class="cellhead">
        <span class="idx">[+]</span>
        <span class="nmwrap"><span class="nm">New calculation</span></span>
        <span class="acts"><button class="btn btn-sm" type="button">Build one</button></span>
      </div>`;
    const go = () => NB.build.open("");
    el.querySelector("button").addEventListener("click", go);
    el.addEventListener("click", e => { if (e.target === el) go(); });
    return el;
  }

  function render() {
    const host = NB.$("streaminner");
    if (!host || NB.view !== "jobs") return;
    const calcs = NB.queue;

    const editing = NB.build.target();

    if (!host.children.length) _cells.clear();

    // An editor cell for a calculation that is no longer being edited has to go
    // before its ordinary cell is placed: inserting the new one in front of it
    // would leave both on screen, which is how closing the form appeared to do
    // nothing.
    host.querySelectorAll(".cell.editing:not(.stub)").forEach(e => {
      if (/** @type {HTMLElement} */ (e).dataset.name !== editing) e.remove();
    });

    const live = new Set();
    calcs.forEach((c, i) => {
      live.add(c.name);
      // the calculation being edited IS the form: the cell becomes it, rather
      // than the form opening somewhere else and leaving the cell behind
      if (editing === c.name) {
        const old = _cells.get(c.name);
        if (old) { old.remove(); _cells.delete(c.name); }
        const ed = host.children[i];
        if (!ed || !ed.classList.contains("editing")) {
          host.insertBefore(NB.build.cellFor(c), host.children[i] || null);
        }
        return;
      }
      let cell = _cells.get(c.name);
      if (!cell) { cell = build(c); _cells.set(c.name, cell); }
      if (host.children[i] !== cell) host.insertBefore(cell, host.children[i] || null);
      update(cell, c, i);
    });
    // The last cell in the stream is always the next calculation: a dashed
    // stub that becomes the form when it is clicked. A queue is a thing you add
    // to, so the place to add is at the end of what is already there.
    const stub = host.querySelector(".cell.stub");
    if (editing === "") {
      if (!stub || !stub.classList.contains("editing")) {
        if (stub) stub.remove();
        host.appendChild(NB.build.cellFor(null));
      }
    } else if (!stub || stub.classList.contains("editing")) {
      if (stub) stub.remove();
      host.appendChild(newStub());
    } else {
      host.appendChild(stub);   // keep it last as cells are inserted above it
    }
    if (editing !== "" && editing !== null) {
      // an editor for a calculation that has left the queue has nothing to edit
      if (!calcs.some(c => c.name === editing)) NB.build.open(null);
    }
    [..._cells.keys()].forEach(n => {
      if (live.has(n)) return;
      const dead = _cells.get(n);
      if (dead && dead.parentNode) dead.parentNode.removeChild(dead);
      _cells.delete(n);
      _tails.delete(n);
      _geom.delete(n);
    });
    fetchTails(calcs);
    fetchGeometry(calcs);
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
