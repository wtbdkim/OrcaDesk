// @ts-check
"use strict";
/* ============================================================
   ORCAdesk notebook front-end — the queue rail.

   The rail is the one thing that never leaves the screen: in Jobs it sits
   beside the stream, in Results beside the report, so what is running is
   always visible while something else is being read.

   Rows are grouped by what they are doing (running / queued / done), because
   that is the question being asked of the list. A row expands in place into
   the builder — the form is not a separate tab you go to and come back from.
   ============================================================ */

(function () {

  const GROUPS = [
    { key: "running", label: "Running", states: ["running"] },
    { key: "queued", label: "Queued", states: ["pending", "blocked"] },
    { key: "done", label: "Done", states: ["done", "failed", "cancelled"] },
  ];
  const EDITABLE = ["pending", "blocked", "cancelled"];

  /** @param {any} c @returns {string} */
  function backendOf(c) {
    const k = c.kind || "";
    return k.startsWith("mlip") ? "MLIP" : k.startsWith("crest") ? "CREST" : "DFT";
  }

  /** How far along a running calculation is, 0..1, or null when it has not
   *  produced enough to say. Never a made-up bar: an optimization reports its
   *  own gradient progress, an SCF its convergence, and anything else gets no
   *  bar at all rather than a decorative one. @param {any} c */
  function progressOf(c) {
    if (c.state !== "running" || !NB.hasGraph(c.name)) return null;
    const b = NB.trackers(c.name);
    if (b.geo.hasData()) return b.geo.progress();
    if (b.crest.hasData() && typeof b.crest.progress === "function") return b.crest.progress();
    if (b.freq.hasData() && typeof b.freq.progress === "function") return b.freq.progress();
    if (b.scf.hasData()) return b.scf.progress(SCFGraph.targetFor(c.scf_convergence || "TightSCF"));
    return null;
  }

  /** @type {string} name of the row expanded into the builder ("" = none) */
  let _open = "";
  /** true while the builder is open on a NEW calculation */
  let _openNew = false;

  /** @param {any} c @param {number} idx @returns {HTMLElement} */
  function row(c, idx) {
    const editable = EDITABLE.indexOf(c.state) >= 0;
    const el = NB.el("div", "qrow");
    el.dataset.name = c.name;
    el.dataset.index = String(idx);

    const frac = progressOf(c);
    const isOrca = backendOf(c) === "DFT";
    const quiet = c.state === "done" && /^Completed\b/.test(c.message || "");
    const msgCls = c.state === "failed" ? "err" : c.state === "blocked" ? "warn" : "";

    el.innerHTML = `
      <span class="grip${editable ? "" : " off"}" title="Reorder">⋮⋮</span>
      <button class="qexp" type="button" aria-expanded="false"
              aria-label="Edit ${NB.esc(c.name)}"><span class="chev">▸</span></button>
      <span class="qtext">
        <span class="qhead">
          <span class="qn">${NB.esc(c.name)}</span>
          <span class="badge tag">${backendOf(c)}</span>
          ${c.is_raw ? '<span class="badge tag">raw</span>' : ""}
        </span>
        <span class="qm">${NB.esc(c.meta || (c.kind + " · charge " + c.charge + " · mult " + c.multiplicity))}</span>
        ${c.message && !quiet ? `<span class="qmsg ${msgCls}">${NB.esc(c.message)}</span>` : ""}
      </span>
      <span class="qacts">
        <span class="badge ${c.state}">${c.state}</span>
        ${isOrca ? '<button class="btn btn-xs btn-ghost" data-act="inp">.inp</button>' : ""}
        ${c.state !== "running" ? '<button class="btn btn-xs btn-ghost" data-act="rm" title="Remove">×</button>' : ""}
      </span>
      ${frac != null ? `<span class="qbar"><i style="width:${Math.round(frac * 100)}%"></i></span>` : ""}`;

    el.querySelector(".qexp").addEventListener("click", () => toggleRow(c.name));
    const inp = el.querySelector('[data-act="inp"]');
    if (inp) inp.addEventListener("click", e => { e.stopPropagation(); showInp(c); });
    const rm = el.querySelector('[data-act="rm"]');
    if (rm) rm.addEventListener("click", e => { e.stopPropagation(); removeCalc(c); });

    if (editable) attachDrag(el);
    return el;
  }

  /** Open (or close) a row's inline editor. @param {string} name */
  function toggleRow(name) {
    if (_open === name) { _open = ""; render(); return; }
    const c = NB.queue.find(x => x.name === name);
    if (!c) return;
    if (EDITABLE.indexOf(c.state) < 0) {
      NB.toast(c.state === "running"
        ? "ORCA is reading this calculation. Stop it first to edit."
        : "A finished calculation cannot be edited — start a new one from it.");
      return;
    }
    _open = name; _openNew = false;
    render();
  }

  /** @param {any} c */
  async function showInp(c) {
    let text = "";
    const disk = await NB.call("get_inp", c.name);
    if (disk && disk.ok && disk.text) text = disk.text;
    if (!text) {
      const g = await NB.call("get_calc", c.name);
      if (g && g.ok && g.calc) {
        if (g.calc.is_raw && g.calc.raw_text) text = g.calc.raw_text;
        else {
          const pv = await NB.call("build_inp_preview", JSON.stringify(g.calc));
          if (pv && pv.ok) text = pv.text;
        }
      }
    }
    NB.modal(c.name + ".inp",
      text ? `<pre>${NB.esc(text)}</pre>`
           : `<p class="hint">No input file yet, and nothing to generate one from.</p>`);
  }

  /** @param {any} c */
  function removeCalc(c) {
    NB.confirm("Remove calculation",
      `"${c.name}" leaves the queue. Files already written to disk stay where they are.`,
      "Remove", async () => {
        const r = await NB.call("remove_calc", c.name);
        if (r && r.error) { NB.fail(r.error); return; }
        if (_open === c.name) _open = "";
        await NB.poll();
      });
  }

  // ---------------------------------------------------------------- drag

  let _dragFrom = -1;
  /** @param {HTMLElement} el */
  function attachDrag(el) {
    const grip = /** @type {HTMLElement} */ (el.querySelector(".grip"));
    // draggable is set only while the pointer is down on the grip: a
    // permanently draggable row swallows text selection, and calculation names
    // are things people copy
    grip.addEventListener("mousedown", () => { el.setAttribute("draggable", "true"); });
    el.addEventListener("mouseup", () => el.removeAttribute("draggable"));
    el.addEventListener("dragstart", () => {
      _dragFrom = Number(el.dataset.index);
      el.classList.add("dragging");
    });
    el.addEventListener("dragend", () => {
      el.classList.remove("dragging");
      el.removeAttribute("draggable");
      document.querySelectorAll(".qrow.drop-target")
        .forEach(r => r.classList.remove("drop-target"));
    });
    el.addEventListener("dragover", e => {
      e.preventDefault();
      if (_dragFrom >= 0) el.classList.add("drop-target");
    });
    el.addEventListener("dragleave", () => el.classList.remove("drop-target"));
    el.addEventListener("drop", async e => {
      e.preventDefault();
      el.classList.remove("drop-target");
      const to = Number(el.dataset.index);
      if (_dragFrom < 0 || _dragFrom === to) return;
      const r = await NB.call("reorder_calc", _dragFrom, to);
      _dragFrom = -1;
      if (r && r.error) { NB.fail(r.error); return; }
      await NB.poll();
    });
  }

  // ---------------------------------------------------------------- render

  function render() {
    const host = NB.$("rail");
    if (!host) return;
    const scroll = host.scrollTop;
    host.innerHTML = "";

    NB.$("q-count").textContent = NB.queue.length
      ? NB.queue.length + (NB.queue.length === 1 ? " calculation" : " calculations")
      : "empty";

    if (!NB.queue.length && !_openNew) {
      host.appendChild(NB.el("div", "qempty",
        `<div class="t">No calculations yet</div>
         <div class="hint">New calculation starts one. It appears here, and its
           progress and output appear beside it.</div>`));
    }

    GROUPS.forEach(g => {
      const rows = NB.queue
        .map((c, i) => ({ c: c, i: i }))
        .filter(x => g.states.indexOf(x.c.state) >= 0);
      if (!rows.length) return;
      const box = NB.el("div", "qgroup");
      box.appendChild(NB.el("div", "qgh",
        `${g.label}<span class="c">${rows.length}</span>`));
      const list = NB.el("div", "qlist");
      rows.forEach(x => {
        if (_open === x.c.name) list.appendChild(NB.build.editorFor(x.c, () => { _open = ""; render(); }));
        else list.appendChild(row(x.c, x.i));
      });
      box.appendChild(list);
      host.appendChild(box);
    });

    if (_openNew) {
      const ed = NB.build.editorFor(null, () => { _openNew = false; render(); });
      host.appendChild(ed);
      ed.scrollIntoView({ block: "nearest" });
    }

    host.scrollTop = scroll;
    paintFoot();
  }

  function paintFoot() {
    const running = NB.running;
    const anyRunnable = NB.queue.some(c => c.state === "pending" || c.state === "blocked");
    NB.$("btn-run").disabled = running || !anyRunnable;
    NB.$("btn-stop").disabled = !running;
    NB.$("btn-cancel").disabled = !running;
    NB.$("btn-clear").disabled = !NB.queue.length;
  }

  // ---------------------------------------------------------------- actions

  async function runQueue() {
    if (!NB.queue.length) { NB.toast("Nothing queued."); return; }
    // ORCA is required only if something that will actually launch it is in the
    // queue: an all-MLIP or all-CREST run needs no ORCA at all.
    const needsOrca = NB.queue.some(c =>
      c.state !== "done" && c.state !== "failed"
      && !String(c.kind).startsWith("mlip") && !String(c.kind).startsWith("crest"));
    if (needsOrca && !(NB.settings && NB.settings.orca_valid)) {
      NB.fail("ORCA path not set — Settings.");
      NB.go("settings");
      return;
    }
    // A calculation whose folder already holds output would overwrite it; ask
    // before, not after.
    const chk = await NB.call("check_overwrite_conflicts");
    let skip = [];
    if (chk && chk.ok && chk.conflicts && chk.conflicts.length) {
      const names = chk.conflicts.map(n => `<li>${NB.esc(n)}</li>`).join("");
      await new Promise(res => {
        NB.modal("Existing output",
          `<p style="font-size:13px;line-height:1.6">These calculations already have output on disk:</p>
           <ul style="font-size:13px;margin:8px 0 0 20px">${names}</ul>`,
          [
            { label: "Cancel", cls: "btn-ghost", act: () => res(null) },
            { label: "Skip them", act: () => { skip = chk.conflicts; res(null); } },
            { label: "Overwrite", cls: "btn-danger", act: () => res(null) },
          ]);
      });
      if (skip === null) return;
    }
    const r = await NB.call("run_queue", JSON.stringify(skip));
    if (r && r.error) { NB.fail(r.error); return; }
    await NB.poll();
  }

  function init() {
    NB.$("btn-new").addEventListener("click", () => {
      _openNew = !_openNew; _open = "";
      render();
    });
    NB.$("btn-run").addEventListener("click", runQueue);
    NB.$("btn-stop").addEventListener("click", async () => {
      await NB.call("stop_after_current");
      NB.toast("Stopping after the running calculation.");
      await NB.poll();
    });
    NB.$("btn-cancel").addEventListener("click", () => {
      NB.confirm("Cancel the run",
        "The running calculation is stopped now. Its partial output stays on disk.",
        "Cancel run", async () => { await NB.call("cancel_queue"); await NB.poll(); });
    });
    NB.$("btn-clear").addEventListener("click", () => {
      NB.confirm("Clear the queue",
        "Every calculation leaves the queue. Files already written stay on disk.",
        "Clear all", async () => {
          const r = await NB.call("clear_queue");
          if (r && r.error) { NB.fail(r.error); return; }
          _open = ""; await NB.poll();
        });
    });
    NB.watch(() => render());
  }

  NB.rail = { init: init, render: render, progressOf: progressOf, backendOf: backendOf,
              closeEditor: () => { _open = ""; _openNew = false; render(); } };
})();
