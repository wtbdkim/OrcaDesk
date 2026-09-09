// @ts-check
"use strict";
/* ============================================================
   ORCAdesk — Notebook front-end adapter (preview)

   Loaded only by index_next.html, and only AFTER app.js, so every handler,
   renderer and poll in the classic front-end is already in place. This file
   adds exactly one thing: the five classic tabs are three views here (Jobs /
   Results / Settings), with the queue rail permanently beside the working
   column, so it translates between the two vocabularies.

   It does not replace switchTab() — it wraps it. Everything the classic
   function does on entry to a tab (loading the free-energy profile, rescanning
   the workspace, re-rendering the SCF graph and the geometry stage now that
   their boxes have a non-zero size) still has to happen here, and those are
   exactly the fixes that are easy to lose in a re-layout. Wrapping keeps them
   for free, and keeps the ~15 switchTab() calls scattered through app.js and
   results_render.js working unchanged.
   ============================================================ */

(function () {
  /** Which view (and, for the split right column, which pane) each classic tab
   *  belongs to. A null stream means "leave the right column as it was": the
   *  queue is always on screen, so switchTab("queue") is a no-op here rather
   *  than a jump that would throw away what the user was looking at. */
  const VIEW_OF = {
    build:    { view: "jobs",     stream: "build" },
    queue:    { view: "jobs",     stream: null },
    log:      { view: "jobs",     stream: "log" },
    results:  { view: "results",  stream: null },
    settings: { view: "settings", stream: null },
  };

  /** @returns {HTMLElement|null} the shell element carrying data-view/data-stream */
  function shell() {
    return /** @type {HTMLElement|null} */ (document.querySelector(".app"));
  }

  /** Mirror the current tab onto the shell's view state and the nav's pressed
   *  state. Pure paint — no data loading; switchTab() has already done that.
   *  @param {string} name classic tab name */
  function paintView(name) {
    const m = VIEW_OF[name];
    const el = shell();
    if (!m || !el) return;
    el.dataset.view = m.view;
    if (m.stream) el.dataset.stream = m.stream;
    document.querySelectorAll(".nbnav button[data-view]").forEach(b => {
      const btn = /** @type {HTMLElement} */ (b);
      btn.setAttribute("aria-pressed", String(btn.dataset.view === el.dataset.view));
    });
    document.querySelectorAll(".nbstreamseg button[data-stream]").forEach(b => {
      const btn = /** @type {HTMLElement} */ (b);
      btn.setAttribute("aria-pressed", String(btn.dataset.stream === el.dataset.stream));
    });
  }

  // Wrap, don't replace. `switchTab` is a top-level function declaration in a
  // classic script, so the global binding and window.switchTab are the same
  // slot: reassigning it re-points every caller, inline onclick attributes and
  // app.js's own calls alike.
  const classicSwitchTab = window.switchTab;
  window.switchTab = function (name) {
    classicSwitchTab(name);
    paintView(name);
  };

  /** Top-level navigation. "Jobs" returns to whichever of Build / Live output
   *  was last open rather than always resetting to Build — coming back from
   *  Results while a calculation runs should land on the run, not on the form.
   *  @param {string} view "jobs" | "results" | "settings" */
  window.nbGo = function (view) {
    if (view !== "jobs") { window.switchTab(view); return; }
    const el = shell();
    window.switchTab(el && el.dataset.stream === "log" ? "log" : "build");
  };

  // The markup ships with data-view="jobs" data-stream="build" already set, so
  // the window is correct before this runs; this only syncs the nav's pressed
  // state and the classic .tab/.panel .active flags to match it.
  window.switchTab("build");
})();
