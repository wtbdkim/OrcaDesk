// @ts-check
"use strict";
/* ============================================================
   ORCAdesk notebook front-end — the queue rail.

   The rail is the one thing that never leaves the screen: in Jobs it sits
   beside the stream, in Results beside the report, so what is running is
   always visible while something else is being read.

   Rows are grouped by what they are doing (running / queued / done), because
   that is the question being asked of the list. Markup and class names are
   the approved design's, so app.css applies to them verbatim.

   It is the queue's STATUS, not its editor: a narrow strip (DESIGN.md §17.2)
   of a state dot and a name per row, the rest in the row's tooltip. Building,
   editing, reordering, removing and clearing happen in the queue window
   (qed.js), which "Open queue" brings up. A row takes you to its calculation
   -- its cell in Jobs, its report in Results.
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
    { key: "done", label: "Finished", states: ["done", "failed", "cancelled", "filtered"], fold: true },
  ];

  /** @param {any} c @returns {string} */
  function backendOf(c) {
    const k = c.kind || "";
    return k.startsWith("mlip") ? "MLIP" : k.startsWith("crest") ? "CREST" : "DFT";
  }

  /** How far along a running calculation is, 0..1, or null when it has not
   *  produced enough to say. Never a made-up bar: every share is a tracker's
   *  own reading of what ORCA printed, and anything else gets no bar at all
   *  rather than a decorative one.
   *
   *  It is the WHOLE job, not the stage running now: a TS optimization is a
   *  Hessian, then the optimization, then (ts_opt_freq) the frequencies, and
   *  reading only the current stage filled the bar when the Hessian finished
   *  and emptied it when the optimization began. Each phase the kind runs is
   *  an equal share; how long each takes is not knowable in advance.
   *  @param {any} c */
  function progressOf(c) {
    if (c.state !== "running" || !NB.hasGraph(c.name)) return null;
    const b = NB.trackers(c.name);
    const ph = phasesOf(c.kind || "", b);
    if (ph) return ph.reduce((a, p) => a + Math.max(0, Math.min(1, p())), 0) / ph.length;
    if (b.geo.hasData()) return b.geo.progress();
    if (b.crest.hasData() && typeof b.crest.progress === "function") return b.crest.progress();
    if (b.freq.hasData() && typeof b.freq.progress === "function") return b.freq.progress();
    if (b.scf.hasData()) return b.scf.progress(b.scf.target(c.scf_convergence, c.kind));
    return null;
  }

  /** The phases a kind runs, each a 0..1 reading, or null for a kind that is
   *  one stage (its tracker's own progress is then the whole job).
   *  A leading Hessian counts when one ran, and is expected until the next
   *  phase starts where the kind usually computes one (a TS optimization's
   *  Calc_Hess, an IRC's initial Hessian); a run that skipped it simply drops
   *  that share. @param {string} k @param {any} b */
  function phasesOf(k, b) {
    const f = b.freq, g = b.geo, ir = b.irc;
    // done once the next phase has data, or once its chain was wiped by the
    // next phase's banner (an IRC's first step comes a while after it)
    const hess = () => g.hasData() || (ir && ir.hasData()) || (f.hadPre && !f.hasData())
      ? 1 : f.hasData() ? f.progress() : 0;
    if (k === "opt" || k === "ts_opt" || k === "opt_freq" || k === "ts_opt_freq") {
      const ph = [];
      if (f.preHessian() || (k.startsWith("ts_") && !g.hasData())) ph.push(hess);
      // GeoTracker reads a cycle with every criterion YES as 1, and a TS
      // search went on past three of them; only ORCA's own end is the end
      ph.push(() => g.done ? 1 : g.hasData() ? Math.min(0.99, g.progress()) : 0);
      // the frequencies after the optimization (not a Hessian inside it)
      if (k.endsWith("_freq")) ph.push(() => g.done && f.hasData() && !f.prestep ? f.progress() : 0);
      return ph;
    }
    if (k === "freq" || k === "ts_freq") return [() => f.hasData() ? f.progress() : 0];
    if (k === "irc" && ir) {
      const ph = [];
      if (f.preHessian() || !ir.hasData()) ph.push(hess);
      ph.push(() => ir.hasData() ? (ir.progress() || 0) : 0);
      return ph;
    }
    return null;
  }

  /** The dot's state class: what the row is doing, or that it is raw input. */
  function dotClass(c) {
    // a job on a remote device that stopped answering: orange (design D7)
    if (c.state === "running") return c.faulted ? "failed" : c.disconnected ? "lost" : "running";
    if (c.state === "done") return "done";
    if (c.state === "failed") return "failed";
    if (c.state === "blocked") return "blocked";
    // left out by the energy condition on its edge: neutral, not a failure
    if (c.state === "filtered") return "filtered";
    return c.is_raw ? "raw" : "";
  }

  /** What a row says about itself when it is pointed at or read out: the strip
   *  shows a dot and a name, and everything else about the calculation lives
   *  here -- what it is, what it is doing, and its last message.
   *  @param {any} c @param {boolean} lost */
  function describe(c, lost) {
    const what = lost ? "failed (ORCA reported an error; its process is still running)"
      : c.state === "running" && c.disconnected
        ? "disconnected (its compute device stopped answering; ORCAdesk keeps trying)"
      : c.state === "pending" && c.waiting ? "waiting for approval" : c.state;
    return [c.name, c.meta || c.kind, what, c.message].filter(Boolean).join(" · ");
  }

  /** One calculation: its state dot and its name, and while it runs the thin
   *  progress bar under them. A click takes you to it.
   *  @param {any} c @returns {HTMLElement} */
  function row(c) {
    const el = NB.el("div", "qrow");
    el.dataset.name = c.name;
    // the group a row belongs to, on the row itself: what it is doing is the
    // thing every rule and every reader keys off
    el.dataset.state = c.state === "running" ? "running"
      : (c.state === "pending" || c.state === "blocked") ? "queued" : "done";
    if (NB.results && NB.results.currentName() === c.name) el.setAttribute("aria-current", "true");

    // ORCA has reported a fatal error but its process lives on (a NumFreq
    // can spin for hours after a displacement dies): it is failed, and it is
    // still running -- it holds its cores and Stop still reaches it. So it
    // stays in this group and reads as failed, with no progress to show.
    const lost = c.state === "running" && !!c.faulted;
    const frac = lost ? null : progressOf(c);
    const says = describe(c, lost);
    el.title = says;
    el.innerHTML = `
      <button class="qmain" type="button" aria-label="${NB.esc(says)}">
        <span class="d ${dotClass(c)}"></span>
        <span class="qn">${NB.esc(c.name)}</span>
      </button>
      ${frac != null ? `<span class="qbar"><span style="width:${Math.floor(frac * 100)}%"></span></span>` : ""}`;

    // A row takes you to the calculation. In Results the rail IS the result
    // picker (DESIGN.md §17.0e), so a finished row opens its report there; a
    // row with no report yet, and every row in Jobs, brings its cell into view.
    el.querySelector(".qmain").addEventListener("click", () => {
      if (NB.view === "results" && (c.state === "done" || c.state === "failed")) {
        NB.results.open(c.name);
      } else showCell(c.name);
    });
    return el;
  }

  /** Bring a calculation's cell into view in Jobs and mark it for a moment.
   *  @param {string} name */
  function showCell(name) {
    if (NB.view !== "jobs") NB.go("jobs");
    const cell = [...document.querySelectorAll("#streaminner > .cell")]
      .find(e => /** @type {HTMLElement} */ (e).dataset.name === name);
    if (!cell) return;
    cell.scrollIntoView({ block: "nearest", behavior: "smooth" });
    cell.classList.add("sel");
    setTimeout(() => cell.classList.remove("sel"), 1600);
  }

  /** groups the user has folded away, by label. A long Done list is the one
   *  that gets in the way, and it is the one worth folding. @type {string[]} */
  const _folded = [];

  // ---------------------------------------------------------------- render

  /** Everything the strip draws, as one string: a poll that changes none of it
   *  leaves the DOM alone. Rebuilding it every 1.2 s took the keyboard focus
   *  off a row and swallowed a click whose press and release straddled a
   *  render (F4). The progress is in whole percent, as drawn. */
  function signature() {
    const cur = NB.results ? [NB.results.currentName(), NB.results.currentPath()] : [];
    const rows = NB.queue.map(c => {
      const lost = c.state === "running" && !!c.faulted;
      const frac = lost ? null : progressOf(c);
      return [c.name, c.state, !!c.faulted, !!c.disconnected, !!c.waiting, c.kind, c.meta, c.message, !!c.is_raw,
              frac == null ? null : Math.floor(frac * 100)];
    });
    const res = NB.running && NB.resources ? NB.resources : null;
    return JSON.stringify([rows, cur, _opened, _folded, res]);
  }
  let _sig = "";

  /** The poll's repaint: only when something drawn has changed. The run
   *  buttons are cheap and are set every time. */
  function refresh() {
    if (signature() !== _sig) render();
    else paintFoot();
  }

  /** Where the keyboard focus is inside the strip, as something that survives
   *  a rebuild: the row (by calculation name, or an opened file's path) and
   *  which of its buttons, or the control's id / data-a.
   *  @param {HTMLElement} host @returns {null | (() => any)} */
  function focusKey(host) {
    const a = /** @type {any} */ (document.activeElement);
    if (!a || a === host || !host.contains(a)) return null;
    const r = a.closest(".qrow");
    const cls = a.classList.contains("qclose") ? ".qclose" : ".qmain";
    if (r && r.dataset.name != null) {
      const n = r.dataset.name;
      return () => [...host.querySelectorAll(".qrow")]
        .find(e => /** @type {any} */ (e).dataset.name === n)?.querySelector(cls);
    }
    if (r && r.dataset.path != null) {
      const p = r.dataset.path;
      return () => [...host.querySelectorAll(".qrow")]
        .find(e => /** @type {any} */ (e).dataset.path === p)?.querySelector(cls);
    }
    if (a.id) { const id = a.id; return () => host.querySelector("#" + CSS.escape(id)); }
    if (a.dataset && a.dataset.a) { const k = a.dataset.a; return () => host.querySelector(`[data-a="${CSS.escape(k)}"]`); }
    return null;
  }

  function render() {
    const host = NB.$("rail");
    if (!host) return;
    const scroll = host.scrollTop;
    const refocus = focusKey(host);
    _sig = signature();
    host.innerHTML = "";

    const head = NB.el("div", "rail-h");
    const n = NB.queue.length;
    head.innerHTML = `Queue<span class="n" id="q-total" aria-label="${
      n === 1 ? "1 calculation" : n + " calculations"}">${n}</span><span class="sp"></span>
      <button class="btn btn-xs btn-ghost" type="button" id="open-queue"
        title="Build and edit the queue in its own window">Open queue</button>`;
    head.querySelector("#open-queue").addEventListener("click", () => NB.openQueueWindow());
    host.appendChild(head);

    if (!n) {
      host.appendChild(NB.el("div", "qgroup",
        `<p class="hint" style="padding:0 4px; margin:0">Nothing queued yet.</p>`));
    }

    GROUPS.forEach(g => {
      const rows = NB.queue.filter(c => g.states.indexOf(c.state) >= 0);
      if (!rows.length) return;
      const box = NB.el("div", "qgroup");
      const open = _folded.indexOf(g.label) < 0;
      const head = NB.el("div", "qgh");
      if (g.fold) {
        // emptying the Finished group is the queue window's (Clear all there,
        // or Delete on a block): this strip only reads
        head.innerHTML = `<button class="qgtoggle" type="button" id="fin-toggle"
            aria-expanded="${open}" aria-controls="q-${g.key}">
            <span class="chev" aria-hidden="true">▸</span>${g.label}<span
              class="c" id="c-${g.key}">${rows.length}</span></button>`;
        head.querySelector(".qgtoggle").addEventListener("click", () => {
          const k = _folded.indexOf(g.label);
          if (k < 0) _folded.push(g.label); else _folded.splice(k, 1);
          render();
        });
      } else {
        head.innerHTML = `${g.label}<span class="c" id="c-${g.key}">${rows.length}</span>`;
      }
      box.appendChild(head);
      if (open) {
        const list = NB.el("div", "qlist");
        list.id = "q-" + g.key;
        rows.forEach(c => list.appendChild(row(c)));
        box.appendChild(list);
      }
      host.appendChild(box);
    });

    host.appendChild(openedGroup());

    if (NB.running && NB.resources) host.appendChild(occupancy(NB.resources));
    host.scrollTop = scroll;
    if (refocus) {
      const back = refocus();
      if (back && back.focus) back.focus({ preventScroll: true });
    }
    paintFoot();
  }

  // ------------------------------------------------------- opened from disk

  /** Results read from outside the workspace. They are not queue rows -- no
   *  state, nothing to run, nothing to edit -- but they are the other half of
   *  "what can I look at", and the design lists them in the rail rather than
   *  leaving the Results window as the only way back to a file already read
   *  once. The list outlives the app (uistate.py, key "rail.opened"): a file
   *  opened yesterday is still one click away until it is closed here. Only
   *  the row is kept; the file is read again when it is clicked, and one that
   *  has moved says so then.
   *  @type {{path:string,name:string,backend:string,meta:string,folder:string}[]} */
  const _opened = [];
  const OPENED_KEY = "rail.opened";
  const OPENED_MAX = 50;

  /** Write the list back. A failed write costs only the next launch's list. */
  function keepOpened() {
    NB.call("set_ui_state", OPENED_KEY, JSON.stringify(_opened.slice(0, OPENED_MAX)));
  }

  /** The list the last launch left, read once at start. Rows opened before it
   *  arrives stay on top. */
  async function restoreOpened() {
    const r = await NB.call("get_ui_state", OPENED_KEY);
    const kept = r && r.ok && Array.isArray(r.value) ? r.value : [];
    const str = (/** @type {any} */ v) => (typeof v === "string" ? v : "");
    kept.forEach((/** @type {any} */ o) => {
      if (!o || !str(o.path) || _opened.some(x => x.path === o.path)) return;
      _opened.push({ path: o.path, name: str(o.name) || o.path, backend: str(o.backend) || "DFT",
                     meta: str(o.meta), folder: str(o.folder) });
    });
    if (kept.length) render();
  }

  /** Remember a result opened from disk, newest first, no duplicates.
   *  @param {{path:string,name:string,backend:string,meta:string,folder:string}} o */
  function noteOpened(o) {
    const at = _opened.findIndex(x => x.path === o.path);
    if (at >= 0) _opened.splice(at, 1);
    _opened.unshift(o);
    if (_opened.length > OPENED_MAX) _opened.length = OPENED_MAX;
    keepOpened();
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

  /** An opened file, in the same strip row as a calculation: a dot and its
   *  name, the rest in the tooltip, and an × that takes it off this list --
   *  closed, not removed: nothing of it was ever in the workspace, and the
   *  file itself is not touched. Show-in-folder is Results' own button.
   *  @param {any} o @returns {HTMLElement} */
  function openedRow(o) {
    const el = NB.el("div", "qrow");
    el.dataset.state = "opened";
    el.dataset.path = o.path;
    if (NB.results.currentPath() === o.path) el.setAttribute("aria-current", "true");
    const says = [o.name, o.backend, o.meta, o.folder].filter(Boolean).join(" · ");
    el.title = says;
    el.innerHTML = `
      <button class="qmain" type="button" aria-label="${NB.esc("Open " + says)}">
        <span class="d ${o.backend === "MLIP" ? "raw" : "done"}"></span>
        <span class="qn">${NB.esc(o.name)}</span>
      </button>
      <button class="qclose" type="button" aria-label="Close ${NB.esc(o.name)}"
        title="Take it off this list (the file stays where it is)">×</button>`;
    el.querySelector(".qmain").addEventListener("click", () => NB.results.openPath(o.path));
    el.querySelector(".qclose").addEventListener("click", () => {
      const at = _opened.findIndex(x => x.path === o.path);
      if (at >= 0) _opened.splice(at, 1);
      keepOpened();
      render();
    });
    return el;
  }

  /** What the run is holding of this machine. Only while something is
   *  running: an idle queue occupies nothing, and a row of zeroes is noise.
   *  @param {any} r RunResources @returns {HTMLElement} */
  function occupancy(r) {
    const box = NB.el("div", "railres");
    /** @param {string} label @param {number} used @param {number} budget @param {string} unit */
    /** @param {string} [tip] what the figure is, on hover */
    const meter = (label, used, budget, unit, tip) => {
      const pct = budget > 0 ? Math.min(100, Math.round((used / budget) * 100)) : 0;
      return `<div class="meter"${tip ? ` title="${NB.esc(tip)}"` : ""}>
        <div class="res-line"><span>${NB.esc(label)}</span>
          <span>${used}${unit} / ${budget > 0 ? budget + unit : "auto"}</span></div>
        <div class="res-bar"><span style="width:${pct}%"></span></div>
      </div>`;
    };
    // both are what the run's budget sets aside for the jobs it admitted, not
    // what they use: cores are declared (exact), memory is an estimate
    box.innerHTML = meter("Cores reserved", r.cores_used, r.cores_budget, "",
                          "Cores the running jobs declared (%pal nprocs, CREST -T, the MLIP thread cap), against the run's budget.")
      + meter("Memory reserved", Math.round(r.ram_used_mb / 1024), Math.round(r.ram_budget_mb / 1024), " GB",
              "What the run's budget sets aside for the running jobs, not what they use: ORCA %maxcore × cores, 3 GB an MLIP job, 128 MB per CREST thread (a search uses far less; under WSL its real use is inside the WSL VM). Before a second job starts, the machine's free memory is checked too.")
      + `<div class="meter wide"><div class="res-line"><span>Running</span>
           <span>${r.jobs} of ${r.max_jobs || "as many as fit"}</span></div></div>`;
    // each enabled remote device's own pool, by its SSH host name: what is
    // reserved there, and "Disconnected" when it stopped answering (D7)
    (r.devices || []).forEach((/** @type {any} */ d) => {
      const pct = d.cores_budget > 0 ? Math.min(100, Math.round((d.cores_used / d.cores_budget) * 100)) : 0;
      box.insertAdjacentHTML("beforeend", `<div class="meter wide" title="${NB.esc(
          "Remote device " + d.id + ": cores the jobs running there declared, against its own budget.")}">
        <div class="res-line"><span>${NB.esc(d.id)}${d.online ? "" : '<span class="lost">Disconnected</span>'}</span>
          <span>${d.jobs} job${d.jobs === 1 ? "" : "s"} · ${d.cores_used} / ${d.cores_budget} cores</span></div>
        <div class="res-bar"><span style="width:${pct}%"></span></div></div>`);
    });
    return box;
  }

  function paintFoot() {
    // a CANCELLED calculation re-runs (P24; the engine picks it up like a
    // pending one), so a queue of only cancelled rows is still runnable
    const anyRunnable = NB.queue.some(c =>
      c.state === "pending" || c.state === "blocked" || c.state === "cancelled");
    NB.$("btn-run").disabled = NB.running || !anyRunnable;
    NB.$("btn-stop").disabled = !NB.running;
    NB.$("btn-cancel").disabled = !NB.running;
  }

  // ---------------------------------------------------------------- actions

  async function runQueue() {
    if (!NB.queue.length) { NB.toast("Nothing queued."); return; }
    // ORCA is required only if something that will actually launch it is in the
    // queue: an all-MLIP or all-CREST run needs no ORCA at all.
    const needsOrca = NB.queue.some(c =>
      c.state !== "done" && c.state !== "failed"
      && !String(c.kind).startsWith("mlip") && !String(c.kind).startsWith("crest"));
    // the path may have been set (or cleared) in the other window since this
    // one last read its settings
    if (needsOrca) await NB.refreshSettings();
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
             NB.nameList(chk.conflicts.map(n => NB.esc(n)))}</div>`,
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
    NB.$("btn-stop").addEventListener("click", stopAfterCurrent);
    NB.$("btn-cancel").addEventListener("click", cancelRun);
    NB.watch(() => refresh());
    restoreOpened();
  }

  async function stopAfterCurrent() {
    await NB.call("stop_after_current");
    NB.toast("Stopping after the running calculation.");
    await NB.poll();
  }
  function cancelRun() {
    NB.confirm("Cancel the run",
      "The running calculation is stopped now. Its partial output stays on disk.",
      "Cancel run", async () => { await NB.call("cancel_queue"); await NB.poll(); });
  }
  function clearAll() {
    NB.confirm("Clear the queue",
      "Every calculation leaves the queue. Files already written stay on disk.",
      "Clear all", async () => {
        const r = await NB.call("clear_queue");
        if (r && r.error) { NB.fail(r.error); return; }
        NB.build.open(null); await NB.poll();
      });
  }

  // the run controls: the main window's strip has three of them, the queue
  // window's top bar all four (Clear all lives only there)
  NB.rail = { init: init, render: render, progressOf: progressOf, backendOf: backendOf,
              noteOpened: noteOpened,
              runQueue: runQueue, stopAfterCurrent: stopAfterCurrent,
              cancelRun: cancelRun, clearAll: clearAll };
})();
