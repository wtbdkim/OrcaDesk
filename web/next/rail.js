// @ts-check
"use strict";
/* ============================================================
   ORCAdesk notebook front-end — the queue rail.

   The rail is the one thing that never leaves the screen: in Jobs it sits
   beside the stream, in Results beside the report, so what is running is
   always visible while something else is being read.

   Rows are grouped by what they are doing (running / queued / done), because
   that is the question being asked of the list, and a row expands in place
   into the builder — the form is not a separate tab you go to and come back
   from. Markup and class names are the approved design's, so app.css applies
   to them verbatim.
   ============================================================ */

(function () {

  const GROUPS = [
    { label: "Running", states: ["running"] },
    { label: "Queued", states: ["pending", "blocked"] },
    { label: "Done", states: ["done", "failed", "cancelled"] },
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

  /** The dot's state class: what the row is doing, or that it is raw input. */
  function dotClass(c) {
    if (c.state === "running") return "running";
    if (c.state === "done") return "done";
    if (c.state === "failed") return "failed";
    if (c.state === "blocked") return "blocked";
    return c.is_raw ? "raw" : "";
  }

  /** @param {any} c @param {number} idx @returns {HTMLElement} */
  function row(c, idx) {
    const editable = EDITABLE.indexOf(c.state) >= 0;
    const el = NB.el("div", "qrow" + (editable ? "" : " nogrip"));
    el.dataset.name = c.name;
    el.dataset.index = String(idx);
    // the group a row belongs to, on the row itself: what it is doing is the
    // thing every rule and every reader keys off
    el.dataset.state = c.state === "running" ? "running"
      : (c.state === "pending" || c.state === "blocked") ? "queued" : "done";
    if (NB.results && NB.results.currentName() === c.name) el.setAttribute("aria-current", "true");

    const frac = progressOf(c);
    const isOrca = backendOf(c) === "DFT";
    const quiet = c.state === "done" && /^Completed\b/.test(c.message || "");
    const msgCls = c.state === "failed" ? "err" : c.state === "blocked" ? "warn" : "";

    el.innerHTML = `
      <span class="grip" aria-hidden="true">⋮⋮</span>
      <button class="qexp" type="button" aria-expanded="false"
              aria-label="Show controls for ${NB.esc(c.name)}"><span class="chev" aria-hidden="true">▸</span></button>
      <button class="qmain" type="button">
        <span class="d ${dotClass(c)}"></span>
        <span class="qtext">
          <span class="qhead">
            <span class="qn">${NB.esc(c.name)}</span>
            <span class="qtag">${backendOf(c)}</span>
          </span>
          <span class="qm">${NB.esc(c.meta || (c.kind + " · charge " + c.charge + " · mult " + c.multiplicity))}</span>
          ${c.message && !quiet ? `<span class="qmsg ${msgCls}">${NB.esc(c.message)}</span>` : ""}
        </span>
      </button>
      <span class="qacts">
        <span class="badge ${c.state}">${c.state}</span>
        <span class="qeditslot">${editable
          ? `<button class="btn btn-xs btn-ghost qedit" type="button">edit</button>`
          : `<button class="btn btn-xs btn-ghost qedit" type="button" disabled title="${
              c.state === "running"
                ? "ORCA is reading this file. Stop the calculation first."
                : "A finished calculation cannot be edited — start a new one from it."
            }">edit</button>`}</span>
        <span class="qinpslot">${isOrca
          ? `<button class="btn btn-xs btn-ghost qinp" type="button" title="Input (.inp)">.inp</button>` : ""}</span>
      </span>
      ${frac != null ? `<span class="qbar"><span style="width:${Math.round(frac * 100)}%"></span></span>` : ""}`;

    el.querySelector(".qexp").addEventListener("click", () => toggleDetail(el, c));
    el.querySelector(".qmain").addEventListener("click", () => {
      if (c.state === "done" || c.state === "failed") NB.results.open(c.name);
      else toggleDetail(el, c);
    });
    const ed = el.querySelector(".qedit");
    if (ed && editable) ed.addEventListener("click", e => { e.stopPropagation(); openEditor(c.name); });
    const inp = el.querySelector(".qinp");
    if (inp) inp.addEventListener("click", e => { e.stopPropagation(); NB.results.showInp(c.name); });

    if (editable) attachDrag(el);
    return el;
  }

  /** The per-row controls, under the row rather than to the right of it, so a
   *  long calculation name never squeezes them out.
   *  @param {HTMLElement} el @param {any} c */
  function toggleDetail(el, c) {
    const btn = /** @type {HTMLElement} */ (el.querySelector(".qexp"));
    const open = btn.getAttribute("aria-expanded") === "true";
    const old = el.querySelector(".qdet");
    if (old) old.remove();
    btn.setAttribute("aria-expanded", String(!open));
    if (open) return;
    const editable = EDITABLE.indexOf(c.state) >= 0;
    const readable = c.state === "done" || c.state === "failed";
    const det = NB.el("div", "qdet");
    det.innerHTML =
      (editable ? `<button class="btn btn-xs" type="button" data-a="edit">Edit</button>` : "")
      + (readable ? `<button class="btn btn-xs" type="button" data-a="res">Results</button>` : "")
      + `<button class="btn btn-xs btn-ghost" type="button" data-a="folder">Show in folder</button>`
      + (c.state !== "running"
          ? `<button class="btn btn-xs btn-danger" type="button" data-a="rm">Remove</button>` : "");
    const on = (a, fn) => { const b = det.querySelector(`[data-a="${a}"]`); if (b) b.addEventListener("click", fn); };
    on("edit", () => openEditor(c.name));
    on("res", () => NB.results.open(c.name));
    on("folder", () => NB.call("show_path_in_folder", c.output_path || c.name));
    on("rm", () => removeCalc(c));
    el.appendChild(det);
  }

  /** @param {string} name */
  function openEditor(name) {
    const c = NB.queue.find(x => x.name === name);
    if (!c) return;
    if (EDITABLE.indexOf(c.state) < 0) {
      NB.toast(c.state === "running"
        ? "ORCA is reading this calculation. Stop it first to edit."
        : "A finished calculation cannot be edited — start a new one from it.");
      return;
    }
    NB.build.open(name);
  }

  /** @param {any} c */
  function removeCalc(c) {
    NB.confirm("Remove calculation",
      `"${c.name}" leaves the queue. Files already written to disk stay where they are.`,
      "Remove", async () => {
        const r = await NB.call("remove_calc", c.name);
        if (r && r.error) { NB.fail(r.error); return; }
        if (NB.build.target() === c.name) NB.build.open(null);
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
    grip.addEventListener("mousedown", () => el.setAttribute("draggable", "true"));
    el.addEventListener("mouseup", () => el.removeAttribute("draggable"));
    el.addEventListener("dragstart", () => {
      _dragFrom = Number(el.dataset.index);
      el.classList.add("dragging");
    });
    el.addEventListener("dragend", () => {
      el.classList.remove("dragging");
      el.removeAttribute("draggable");
      document.querySelectorAll(".qrow.dropbefore, .qrow.dropafter")
        .forEach(r => r.classList.remove("dropbefore", "dropafter"));
    });
    el.addEventListener("dragover", e => {
      e.preventDefault();
      if (_dragFrom < 0) return;
      const after = Number(el.dataset.index) > _dragFrom;
      el.classList.toggle("dropafter", after);
      el.classList.toggle("dropbefore", !after);
    });
    el.addEventListener("dragleave", () => el.classList.remove("dropbefore", "dropafter"));
    el.addEventListener("drop", async e => {
      e.preventDefault();
      el.classList.remove("dropbefore", "dropafter");
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

    const head = NB.el("div", "rail-h");
    head.innerHTML = `Queue<span class="sp"></span><span class="n">${
      NB.queue.length ? NB.queue.length + (NB.queue.length === 1 ? " calculation" : " calculations")
                      : "empty"}</span>`;
    host.appendChild(head);

    if (!NB.queue.length) {
      host.appendChild(NB.el("div", "qgroup",
        `<p class="hint" style="padding:0 6px">Nothing queued yet. <b>New calculation</b>
          starts one; it appears here, and its progress and output appear beside it.</p>`));
    }

    GROUPS.forEach(g => {
      const rows = NB.queue
        .map((c, i) => ({ c: c, i: i }))
        .filter(x => g.states.indexOf(x.c.state) >= 0);
      if (!rows.length) return;
      const box = NB.el("div", "qgroup");
      box.appendChild(NB.el("div", "qgh",
        `${g.label}<span class="sp"></span><span class="c">${rows.length}</span>`));
      const list = NB.el("div", "qlist");
      rows.forEach(x => list.appendChild(row(x.c, x.i)));
      box.appendChild(list);
      host.appendChild(box);
    });

    host.scrollTop = scroll;
    paintFoot();
  }

  function paintFoot() {
    const anyRunnable = NB.queue.some(c => c.state === "pending" || c.state === "blocked");
    NB.$("btn-run").disabled = NB.running || !anyRunnable;
    NB.$("btn-stop").disabled = !NB.running;
    NB.$("btn-cancel").disabled = !NB.running;
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
      NB.settingsView.open();
      return;
    }
    // A calculation whose folder already holds output would overwrite it; ask
    // before, not after.
    const chk = await NB.call("check_overwrite_conflicts");
    let skip = [];
    let go = true;
    if (chk && chk.ok && chk.conflicts && chk.conflicts.length) {
      go = await new Promise(res => {
        NB.modalRaw("Existing output",
          `<p>These calculations already have output on disk. Running them again
             overwrites it.</p><div class="names">${
             chk.conflicts.map(n => NB.esc(n)).join("<br>")}</div>`,
          [{ label: "Cancel", cls: "btn-ghost", act: () => res(false) },
           { label: "Skip them", act: () => { skip = chk.conflicts; res(true); } },
           { label: "Overwrite", cls: "btn-danger", act: () => res(true) }]);
      });
    }
    if (!go) return;
    const r = await NB.call("run_queue", JSON.stringify(skip));
    if (r && r.error) { NB.fail(r.error); return; }
    await NB.poll();
  }

  function init() {
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
          NB.build.open(null); await NB.poll();
        });
    });
    NB.watch(() => render());
  }

  NB.rail = { init: init, render: render, progressOf: progressOf, backendOf: backendOf,
              openEditor: openEditor };
})();
