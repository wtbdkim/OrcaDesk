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

  // `fold` is the design's shape, not a preference: Running and Queued are a
  // plain label, and only the group that grows without bound folds away and
  // offers to be emptied. A fold control on a two-row group is a control that
  // never needs pressing.
  // `key` is the design's own id stem (q-running / c-running / ...). Carrying
  // the ids, not just the classes, is what lets the two pages be addressed by
  // the same selector -- tools/uispec.py measures them that way.
  const GROUPS = [
    { key: "running", label: "Running", states: ["running"], fold: false },
    { key: "queued", label: "Queued", states: ["pending", "blocked"], fold: false },
    { key: "done", label: "Finished", states: ["done", "failed", "cancelled"], fold: true },
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
    // Every row carries its message, finished ones included: the design reads
    // a done row as the answer it found ("0 imaginary / G -1268.19844 Eh"),
    // and the "Completed ..." line used to be filtered out as noise -- which
    // left a finished row two lines tall and saying nothing.
    const msgCls = c.state === "failed" ? "err" : c.state === "blocked" ? "warn" : "";

    el.innerHTML = `
      <span class="grip" aria-hidden="true">⋮</span>
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
          ${c.message
            ? `<span class="qmsg ${msgCls}">${
                c.state === "failed" ? "⚠ " : ""}${NB.esc(c.message)}</span>` : ""}
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
    const queued = c.state === "pending" || c.state === "blocked";
    const readable = c.state === "done" || c.state === "failed";
    const det = NB.el("div", "qdet");
    // The design's row controls, in its order and its wording. Edit is not
    // among them: it is the .qedit button on the row itself, and the same
    // action in two places is two things to keep in step.
    //
    // Two of the design's are deliberately absent, and both for a reason the
    // mockup could not know. "requeue" on a failed row contradicts P24 -- a
    // FAILED calculation is locked at the moment it failed and retrying means
    // building a new one. "stop" on a running row would have to call
    // cancel_queue, which stops the whole run: with several jobs in flight
    // (P57) a button on ONE row that kills the others is worse than no button.
    const b = (label, cls, act) =>
      `<button class="btn btn-xs${cls ? " " + cls : ""}" type="button" data-a="${act}">${label}</button>`;
    det.innerHTML =
      // reordering needs a path that works from the keyboard; the drag is the
      // addition, never the only way (reference §rail)
      (queued ? b("↑ earlier", "btn-ghost qmove up", "up")
              + b("↓ later", "btn-ghost qmove down", "down") : "")
      + (readable ? b("results", "btn-ghost", "res") : "")
      + b("duplicate", "btn-ghost", "dup")
      + b("folder", "btn-ghost", "folder")
      + (c.state !== "running" ? b("remove", "btn-danger", "rm") : "");
    const on = (a, fn) => { const x = det.querySelector(`[data-a="${a}"]`); if (x) x.addEventListener("click", fn); };
    if (queued) {
      const sibs = groupSiblings(c);
      const at = sibs.indexOf(c.name);
      const up = det.querySelector('[data-a="up"]');
      const dn = det.querySelector('[data-a="down"]');
      if (at <= 0) up.disabled = true; else on("up", () => moveTo(c, sibs[at - 1]));
      if (at < 0 || at >= sibs.length - 1) dn.disabled = true;
      else on("down", () => moveTo(c, sibs[at + 1]));
    }
    on("res", () => NB.results.open(c.name));
    on("dup", () => duplicateCalc(c));
    on("folder", () => NB.call("show_path_in_folder", c.output_path || c.name));
    on("rm", () => removeCalc(c));
    el.appendChild(det);
  }

  /** The names in this row's own group, in queue order -- what "earlier" and
   *  "later" mean. Moving past a RUNNING row is not a reorder anybody asked
   *  for, so the pair walks the group, not the whole queue.
   *  @param {any} c @returns {string[]} */
  function groupSiblings(c) {
    const g = GROUPS.find(x => x.states.indexOf(c.state) >= 0);
    return NB.queue.filter(x => g.states.indexOf(x.state) >= 0).map(x => x.name);
  }

  /** Swap a row with the named one, by their positions in the live queue.
   *  @param {any} c @param {string} otherName */
  async function moveTo(c, otherName) {
    const from = NB.queue.findIndex(x => x.name === c.name);
    const to = NB.queue.findIndex(x => x.name === otherName);
    if (from < 0 || to < 0 || from === to) return;
    const r = await NB.call("reorder_calc", from, to);
    if (r && r.error) { NB.fail(r.error); return; }
    await NB.poll();
  }

  /** Copy a calculation back into the queue under a free name. The copy is a
   *  new PENDING row whatever the original became: what is being reused is the
   *  setup, not the result. @param {any} c */
  async function duplicateCalc(c) {
    const g = await NB.call("get_calc", c.name);
    if (!g || !g.ok || !g.calc) { NB.fail(`Could not read "${c.name}".`); return; }
    const taken = NB.queue.map(x => String(x.name).toLowerCase());
    let name = c.name + "-copy", k = 2;
    while (taken.indexOf(name.toLowerCase()) >= 0) name = c.name + "-copy" + (k++);
    const copy = Object.assign({}, g.calc, {name: name, state: "pending", message: ""});
    const r = await NB.call("add_calc", JSON.stringify(copy));
    if (r && r.error) { NB.fail(r.error); return; }
    NB.toast(`Copied to "${name}".`);
    await NB.poll();
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

  /** Take the finished rows out of the queue -- the group's own Clear, which
   *  the footer's "Clear all" does not cover: the list that gets in the way is
   *  the one that only grows. Queue-list only, like every removal here: a run
   *  folder is never touched, so this drops the record and not the results.
   *  @param {string[]} names */
  function clearFinished(names) {
    if (!names.length) return;
    NB.confirm("Clear finished",
      `${names.length} finished calculation${names.length === 1 ? "" : "s"} leave the
       queue. Files already written to disk stay where they are.`,
      "Clear", async () => {
        for (const n of names) {
          const r = await NB.call("remove_calc", n);
          if (r && r.error) { NB.fail(r.error); break; }
        }
        await NB.poll();
      });
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

  /** groups the user has folded away, by label. A long Done list is the one
   *  that gets in the way, and it is the one worth folding. @type {string[]} */
  const _folded = [];

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
    head.innerHTML = `Queue<span class="sp"></span><span class="n" id="q-total">${
      NB.queue.length ? NB.queue.length + (NB.queue.length === 1 ? " calculation" : " calculations")
                      : "empty"}</span>`;
    host.appendChild(head);

    if (!NB.queue.length) {
      host.appendChild(NB.el("div", "qgroup",
        `<p class="hint" style="padding:0 6px">Nothing queued yet.</p>`));
    }

    GROUPS.forEach(g => {
      const rows = NB.queue
        .map((c, i) => ({ c: c, i: i }))
        .filter(x => g.states.indexOf(x.c.state) >= 0);
      if (!rows.length) return;
      const box = NB.el("div", "qgroup");
      const open = _folded.indexOf(g.label) < 0;
      const head = NB.el("div", "qgh");
      if (g.fold) {
        head.innerHTML = `<button class="qgtoggle" type="button" id="fin-toggle"
            aria-expanded="${open}" aria-controls="q-${g.key}">
            <span class="chev" aria-hidden="true">▸</span>${g.label}<span
              class="c" id="c-${g.key}">${rows.length}</span></button>
          <span class="sp"></span>
          <button class="btn btn-xs btn-ghost" type="button" id="clear-finished"
            data-a="clearfin">Clear</button>`;
        head.querySelector(".qgtoggle").addEventListener("click", () => {
          const k = _folded.indexOf(g.label);
          if (k < 0) _folded.push(g.label); else _folded.splice(k, 1);
          render();
        });
        head.querySelector('[data-a="clearfin"]')
            .addEventListener("click", () => clearFinished(rows.map(x => x.c.name)));
      } else {
        head.innerHTML = `${g.label}<span class="c" id="c-${g.key}">${rows.length}</span>`;
      }
      box.appendChild(head);
      if (open) {
        const list = NB.el("div", "qlist");
        list.id = "q-" + g.key;
        rows.forEach(x => list.appendChild(row(x.c, x.i)));
        box.appendChild(list);
      }
      host.appendChild(box);
    });

    host.appendChild(openedGroup());

    if (NB.running && NB.resources) host.appendChild(occupancy(NB.resources));
    host.scrollTop = scroll;
    paintFoot();
  }

  // ------------------------------------------------------- opened from disk

  /** Results read from outside the workspace, this sitting. They are not queue
   *  rows -- no state, nothing to run, nothing to edit -- but they are the
   *  other half of "what can I look at", and the design lists them in the rail
   *  rather than leaving the Results window as the only way back to a file
   *  already read once. Session-lived on purpose: it is a record of what THIS
   *  sitting opened, and the workspace scan already offers everything that
   *  lives in the workspace.
   *  @type {{path:string,name:string,backend:string,meta:string,folder:string}[]} */
  const _opened = [];

  /** Remember a result opened from disk, newest first, no duplicates.
   *  @param {{path:string,name:string,backend:string,meta:string,folder:string}} o */
  function noteOpened(o) {
    const at = _opened.findIndex(x => x.path === o.path);
    if (at >= 0) _opened.splice(at, 1);
    _opened.unshift(o);
    render();
  }

  /** The group is drawn even when it is empty, because its header IS the way
   *  in: "Open file..." is the rail's only route to a result that is not in
   *  the workspace, and a control that appears only once it has been used is
   *  not a route to anything. @returns {HTMLElement} */
  function openedGroup() {
    const box = NB.el("div", "qgroup");
    box.id = "opened-group";
    const head = NB.el("div", "qgh");
    head.innerHTML = `Opened files<span class="c">${_opened.length}</span>
      <span class="sp"></span>
      <button class="btn btn-xs btn-ghost" type="button" data-a="openfile">Open file…</button>`;
    head.querySelector('[data-a="openfile"]')
        .addEventListener("click", () => NB.results.openFileDialog());
    box.appendChild(head);
    if (_opened.length) {
      const list = NB.el("div", "qlist");
      list.id = "q-opened";
      _opened.forEach(o => list.appendChild(openedRow(o)));
      box.appendChild(list);
    }
    return box;
  }

  /** @param {any} o @returns {HTMLElement} */
  function openedRow(o) {
    const el = NB.el("div", "qrow nogrip");
    el.dataset.state = "opened";
    if (NB.results.currentPath() === o.path) el.setAttribute("aria-current", "true");
    el.innerHTML = `
      <span class="grip" aria-hidden="true">⋮</span>
      <button class="qexp" type="button" aria-expanded="false"
              aria-label="Show controls for ${NB.esc(o.name)}"><span
              class="chev" aria-hidden="true">▸</span></button>
      <button class="qmain" type="button">
        <span class="d ${o.backend === "MLIP" ? "raw" : "done"}"></span>
        <span class="qtext">
          <span class="qhead"><span class="qn">${NB.esc(o.name)}</span><span
            class="qtag">${NB.esc(o.backend)}</span></span>
          <span class="qm">${NB.esc(o.meta)}</span>
          <span class="qmsg">${NB.esc(o.folder)}</span>
        </span>
      </button>
      <span class="qacts">
        <span class="badge done">done</span>
        <span class="qeditslot"></span>
        <span class="qinpslot"></span>
      </span>`;
    el.querySelector(".qexp").addEventListener("click", () => toggleOpenedDetail(el, o));
    el.querySelector(".qmain").addEventListener("click", () => NB.results.openPath(o.path));
    return el;
  }

  /** The opened row's controls. Fewer than a calculation's, because there is
   *  less true of it: nothing to edit, nothing to run again. "Forget" drops the
   *  row from this list and touches no file -- the same contract as Remove.
   *  @param {HTMLElement} el @param {any} o */
  function toggleOpenedDetail(el, o) {
    const btn = /** @type {HTMLElement} */ (el.querySelector(".qexp"));
    const open = btn.getAttribute("aria-expanded") === "true";
    const old = el.querySelector(".qdet");
    if (old) old.remove();
    btn.setAttribute("aria-expanded", String(!open));
    if (open) return;
    const det = NB.el("div", "qdet");
    // the design's wording for this row: it is closed, not removed -- nothing
    // of it was ever in the workspace to take out
    det.innerHTML =
      `<button class="btn btn-xs btn-ghost" type="button" data-a="folder">show in folder</button>`
      + `<button class="btn btn-xs btn-danger" type="button" data-a="rm">close</button>`;
    const on = (a, fn) => det.querySelector(`[data-a="${a}"]`).addEventListener("click", fn);
    on("folder", () => NB.call("show_path_in_folder", o.path));
    on("rm", () => {
      const at = _opened.findIndex(x => x.path === o.path);
      if (at >= 0) _opened.splice(at, 1);
      render();
    });
    el.appendChild(det);
  }

  /** What the run is holding of this machine. Only while something is
   *  running: an idle queue occupies nothing, and a row of zeroes is noise.
   *  @param {any} r RunResources @returns {HTMLElement} */
  function occupancy(r) {
    const box = NB.el("div", "railres");
    /** @param {string} label @param {number} used @param {number} budget @param {string} unit */
    const meter = (label, used, budget, unit) => {
      const pct = budget > 0 ? Math.min(100, Math.round((used / budget) * 100)) : 0;
      return `<div class="meter">
        <div class="res-line"><span>${NB.esc(label)}</span>
          <span>${used}${unit} / ${budget > 0 ? budget + unit : "auto"}</span></div>
        <div class="res-bar"><span style="width:${pct}%"></span></div>
      </div>`;
    };
    box.innerHTML = meter("Cores", r.cores_used, r.cores_budget, "")
      + meter("Memory", Math.round(r.ram_used_mb / 1024), Math.round(r.ram_budget_mb / 1024), " GB")
      + `<div class="meter wide"><div class="res-line"><span>Running</span>
           <span>${r.jobs} of ${r.max_jobs || "as many as fit"}</span></div></div>`;
    return box;
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
              openEditor: openEditor, noteOpened: noteOpened };
})();
