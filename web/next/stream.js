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
  /** Geometry per calc, for the thumbnail. `final` records WHICH geometry it
   *  is — the run's own output, or the coordinates it was handed. Without that
   *  flag the cache was keyed by name alone, so a calculation watched from
   *  PENDING to DONE kept the input structure it was first drawn with while
   *  the caption underneath flipped to "final": the optimized geometry, the
   *  one thing the run exists to produce, appeared only after a reload.
   *  `at` is the state it was read at, which is what stops the re-read from
   *  becoming a loop: a DONE run whose output holds no geometry is asked once,
   *  not once per poll.
   *  @type {Map<string, {final: boolean, at: string, atoms: any[]}>} */
  const _geom = new Map();
  /** @type {Set<string>} */
  const _geomBusy = new Set();

  /** What this calculation has to show in the middle column, or null.
   *
   *  A run that is a sequence of named stages (frequencies, TD-DFT, a conformer
   *  search) shows the stages; one that converges shows its trace. Both are
   *  drawn by charts.js in the design's vocabulary — the trackers' own render*
   *  helpers emit the classic front-end's classes, which app.css does not style.
   *  @param {any} c @param {number} px the box width */
  function chartFor(c, px) {
    if (!NB.hasGraph(c.name)) return null;
    const b = NB.trackers(c.name);
    const st = CHARTS.stages(b);
    if (st) return { t: st.title, stages: true, h: st.html };
    // An optimization is an SCF per cycle, so a running cell has two convergences
    // to look at: the whole run, and the one solving right now. They are the same
    // question at two scales and the answer is a TOGGLE -- stacking them halves
    // both charts and pushes the column past the output pane beside it.
    const cv = CHARTS.convergence(b, c, px, _gkind.get(c.name));
    if (!cv) return null;
    return { t: cv.title, meta: cv.meta, has: cv.has, kind: cv.kind,
             h: cv.html + (c.state === "running"
                  ? CHARTS.progress(cv.progress, cv.eta) : "") };
  }

  /** What a line of ORCA output is, for colour. The design gives the log four
   *  tones rather than one wall of grey: what ORCA said, what went right, what
   *  is worth a look, and what failed.
   *  @param {string} l */
  function logClass(l) {
    if (/\berror\b|aborting|ABNORMAL|not converged/i.test(l)) return "l-err";
    if (/\bwarning\b|caution/i.test(l)) return "l-warn";
    if (/HURRAY|CONVERGED|TERMINATED NORMALLY/i.test(l)) return "l-ok";
    return "l-orca";
  }

  /** One mol.js stage per thumbnail canvas, so a cell's structure turns under
   *  the pointer like every other stage here. Keyed by the canvas because the
   *  cell element is reused across polls and the canvas is what the listeners
   *  are bound to. @type {WeakMap<HTMLCanvasElement, any>} */
  const _thumbs = new WeakMap();

  /** Which convergence each cell is showing: "geo" (the whole optimization) or
   *  "scf" (the cycle running now). Session-lived and per calculation, because
   *  it is a question about one run -- and sticky, because a chart that flipped
   *  back on the next poll would be a control that undoes itself.
   *  @type {Map<string, string>} */
  const _gkind = new Map();

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
          <button class="btn btn-sm" type="button" data-act="newfrom"
                  title="Start a new calculation from this one's optimized geometry"
                  >New from this</button>
          <button class="btn btn-sm cellres" type="button">Results</button>
        </span>
      </div>
      <div class="cellbody">
        <!-- why it failed, where the run is being read: the rail row carries a
             one-line .qmsg, the reason itself belongs here (reference §cell) -->
        <div class="qerror" style="margin-bottom:18px" hidden></div>
        <div class="celgrid">
          <div class="firstcol">
            <div class="thumb">
              <canvas class="molcv"
                aria-label="Structure, ball and stick. Drag to turn it; the arrow keys
                  turn it too, and x, y or z looks straight down that axis."></canvas>
              <button class="act" type="button" data-open="viewer">Open in viewer</button>
              <span class="cap"></span>
            </div>
          </div>
          <div class="pane chart">
            <div class="panehead"><span class="t"></span>
              <div class="seg" role="group" aria-label="Graph view" hidden>
                <button type="button" data-gkind="geo" aria-pressed="true">Optimization</button>
                <button type="button" data-gkind="scf" aria-pressed="false">Current SCF</button>
              </div>
              <span class="sp"></span><span class="meta"></span></div>
            <div class="chartbox"></div>
          </div>
          <div class="pane out">
            <div class="panehead"><span class="t">Output</span><span class="sp"></span>
              <span class="meta"></span></div>
            <div class="logbox" role="log"></div>
          </div>
        </div>
        <div class="factline"></div>
      </div>`;
    el.querySelectorAll("[data-gkind]").forEach(b => b.addEventListener("click", () => {
      _gkind.set(c.name, /** @type {HTMLElement} */ (b).dataset.gkind);
      render();      // now, not on the next poll: a control waits for nothing
    }));
    el.querySelector(".cellinp").addEventListener("click", () => NB.results.showInp(c.name));
    el.querySelector(".cellres").addEventListener("click", () => NB.results.open(c.name));
    // the geometry is taken by REFERENCE, not copied: the engine resolves it to
    // this calculation's optimized structure when the new one actually runs, so
    // it is the geometry as it finally was and not as it was when the button
    // was pressed. The same handoff opt -> freq uses.
    el.querySelector('[data-act="newfrom"]').addEventListener("click", () => NB.build.open("", {
      name: "", kind: "opt", is_raw: false, raw_text: "", xyz: "",
      geometry_source: "reference", ref_name: c.name,
      charge: c.charge, multiplicity: c.multiplicity, config: {},
    }));
    return el;
  }

  /** @param {HTMLElement} el @param {any} c @param {number} i */
  function update(el, c, i) {
    const q = (s) => /** @type {any} */ (el.querySelector(s));
    q(".idx").textContent = "[" + (i + 1) + "]";
    q(".nm").textContent = c.name;

    // a running calc ORCA has already failed (its process lives on -- see the
    // rail's row()) reads as failed here too
    const lost = c.state === "running" && !!c.faulted;
    const badge = q(".acts .badge");
    badge.textContent = lost ? "failed" : c.state;
    badge.className = "badge " + (lost ? "failed" : c.state);

    el.className = "cell" + (lost ? " dead" : c.state === "running" ? " live"
                           : c.state === "failed" ? " dead" : "");

    // .inp is ORCA-only: an MLIP or CREST calculation produces no ORCA input,
    // so the button would open a preview of something that never ran.
    q(".cellinp").hidden = NB.rail.backendOf(c) !== "DFT";
    q(".cellres").hidden = !(c.state === "done" || c.state === "failed");
    // only a finished run HAS a geometry to start from; a failed one may not
    // have got far enough to write one
    q('[data-act="newfrom"]').hidden = c.state !== "done";

    // --- middle column: the chart, measured then drawn
    const box = q(".chartbox");
    const px = box.clientWidth || Math.round((el.clientWidth || 1024) * 0.42);
    const ch = chartFor(c, px);
    const grid = q(".celgrid");
    if (ch) {
      q(".pane.chart").hidden = false;
      q(".pane.chart .panehead .t").textContent = ch.t;
      q(".pane.chart .panehead .meta").textContent = ch.meta || "";
      // the toggle appears only once there are two things to toggle between:
      // before the first cycle lands there is only an SCF, and after the run
      // ends there is no cycle solving now
      const seg = q(".pane.chart .panehead .seg");
      seg.hidden = !(ch.has && ch.has.geo && ch.has.scf);
      seg.querySelectorAll("[data-gkind]").forEach(b => b.setAttribute(
        "aria-pressed", String(/** @type {HTMLElement} */ (b).dataset.gkind === ch.kind)));
      box.innerHTML = ch.h;
    } else {
      q(".pane.chart").hidden = true;
      box.innerHTML = "";
    }

    // --- first column: the structure, or the stage chain's own width
    const got = _geom.get(c.name);
    const geom = got ? got.atoms : null;
    const thumb = q(".thumb");
    const hasThumb = !!(geom && geom.length);
    thumb.hidden = !hasThumb;
    q(".firstcol").hidden = !hasThumb;
    grid.className = "celgrid"
      + (hasThumb ? (ch && ch.stages ? " has-stages" : " has-thumb") : "");
    if (hasThumb) {
      // the caption states which of the two this is, so it can never claim a
      // final structure over input coordinates
      q(".thumb .cap").textContent =
        (got.final ? "final · " : "input · ") + geom.length + " atoms";
      // MOUNTED, not drawn once: every other stage in this UI turns under the
      // pointer, and a thumbnail that does not is the one place the molecule is
      // a picture instead of a thing. The stage is per canvas and outlives the
      // 1s poll -- setAtoms only when the geometry has actually changed, or a
      // measurement in progress would be cleared every second.
      const cv = q(".thumb canvas");
      let st = _thumbs.get(cv);
      if (!st) { st = MOL.mount(cv, { gizmoBottom: 26 }); _thumbs.set(cv, st); }
      if (st.__atoms !== geom) { st.setAtoms(geom); st.__atoms = geom; }
      // the trajectory, not this one frame: the thumbnail is the door to every
      // structure the run has written, which is what the big window is for
      q('.thumb [data-open="viewer"]').onclick = () => NB.mv.open({
        source: "calc:" + c.name, title: c.name + " — structures" });
    }

    // --- right column: the output
    const tail = _tails.get(c.name);
    const log = q(".logbox");
    const meta = q(".out .meta");
    if (tail && tail.lines.length) {
      meta.textContent = tail.file + (tail.truncated ? " · last " + tail.lines.length + " lines" : "");
      const stick = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
      log.innerHTML = tail.lines.map(l => `<div class="${logClass(l)}">${NB.esc(l)}</div>`).join("");
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
    // a failure is not a fact among facts: the reason goes in the .qerror box
    // at the top of the body, so no cell states the same sentence twice
    if (c.message && !quiet && c.state !== "failed" && !lost) {
      bits.push(`<span>${NB.esc(c.message)}</span>`);
    }
    q(".factline").innerHTML = bits.join("");

    const err = q(".qerror");
    const failed = (c.state === "failed" || lost) && !!c.message;
    err.hidden = !failed;
    err.innerHTML = failed ? `<span>${NB.esc(c.message)}</span>` : "";
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
      // re-fetch once the run has an output of its own: a cached INPUT
      // geometry is not the answer to a calculation that has since finished
      const have = _geom.get(c.name);
      if (_geomBusy.has(c.name)) return;
      if (have && (have.final || have.at === c.state)) return;
      if (String(c.kind).startsWith("crest")) return;   // an ensemble, not one structure
      _geomBusy.add(c.name);
      (async () => {
        let atoms = null, final = false;
        if (c.state === "done") {
          const d = await NB.call("parse_calc_output", c.name);
          if (d && d.geometry && d.geometry.length) { atoms = d.geometry; final = true; }
        }
        if (!atoms) {
          const g = await NB.call("get_calc", c.name);
          if (g && g.ok && g.calc && g.calc.xyz) atoms = MOL.parseXyz(g.calc.xyz);
        }
        _geomBusy.delete(c.name);
        if (!atoms || !atoms.length) {
          _geom.set(c.name, { final: false, at: c.state, atoms: [] });
          return;
        }
        _geom.set(c.name, { final: final, at: c.state, atoms: atoms });
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
    // The whole head is the control. A row that says "New calculation" and then
    // puts a button beside it asks twice for one thing — and the text is the
    // bigger, more obvious target of the two.
    el.innerHTML = `
      <button class="cellhead stubhead" type="button">
        <span class="idx">[+]</span>
        <span class="nmwrap"><span class="nm">New calculation</span></span>
      </button>`;
    el.querySelector(".stubhead").addEventListener("click", () => NB.build.open(""));
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
