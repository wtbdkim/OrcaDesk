// @ts-check
"use strict";
/* ============================================================
   ORCAdesk notebook front-end — core.

   Everything shared by the rail, the stream, the report and settings: the
   QWebChannel connection, the polled mirrors of the backend's state, the
   per-calculation convergence trackers, and the small chrome (toast, modal,
   confirm) the rest of the UI builds on.

   One global, `NB`. This file and web/app.js are checked by the same tsc
   project and share one global scope, so a second top-level `bridge`/`queue`/
   `settings` would collide with the classic front-end's. Every module here is
   an IIFE that hangs its exports off NB instead.
   ============================================================ */

/** @type {any} */
var NB = {};

(function () {

  // ---------------------------------------------------------------- state
  NB.bridge = null;
  /** @type {any} */ NB.settings = null;
  /** @type {any[]} CalcSummary[] — the queue, as the backend last reported it */
  NB.queue = [];
  NB.running = false;
  NB.view = "jobs";
  NB.about = null;

  let _qversion = -1;          // last queue snapshot version rendered
  let _logSeq = 0;             // last log line consumed
  let _pollBusy = false;

  const POLL_MS = 1200;

  // ---------------------------------------------------------------- helpers

  /** @param {string} s @returns {string} */
  NB.esc = function (s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  };
  /** @param {string} id @returns {any} */
  NB.$ = function (id) { return document.getElementById(id); };
  /** @param {string} tag @param {string} [cls] @param {string} [html] */
  NB.el = function (tag, cls, html) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  };
  /** The tail of a path — the last two segments, which is what identifies a
   *  workspace to the person who chose it. The whole thing is a line of chrome
   *  wide on a real machine, and the button says what it is, not where the OS
   *  keeps it. @param {string} p */
  NB.shortPath = function (p) {
    const parts = String(p || "").split(/[\\/]/).filter(Boolean);
    if (!parts.length) return "(no workspace set)";
    if (parts.length <= 2) return parts.join("\\");
    return "…\\" + parts.slice(-2).join("\\");
  };
  const shortPath = NB.shortPath;

  /** Seconds as a short clock. @param {number} sec */
  NB.dur = function (sec) {
    if (!isFinite(sec) || sec <= 0) return "";
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.round(sec % 60);
    return h ? `${h}h ${m}m` : m ? `${m}m ${s}s` : `${s}s`;
  };

  /** @param {string} msg @param {string} [kind] "" | "ok" | "err" */
  NB.toast = function (msg, kind) {
    const wrap = NB.$("toastwrap");
    if (!wrap) return;
    const t = NB.el("div", "toast" + (kind ? " " + kind : ""));
    t.textContent = msg;
    wrap.appendChild(t);
    setTimeout(() => t.remove(), 3200);
  };
  /** An error the user has to see: the toast, and nothing swallowed silently.
   *  @param {string} msg */
  NB.fail = function (msg) { NB.toast(msg, "err"); };

  /** Open the shared modal.
   *  @param {string} title
   *  @param {string} bodyHtml
   *  @param {{label: string, cls?: string, act?: () => void}[]} [buttons] */
  NB.modalRaw = function (title, bodyHtml, buttons, wide) {
    NB.$("modal-title").textContent = title;
    NB.$("modal-body").innerHTML = bodyHtml;
    const foot = NB.$("modal-foot");
    foot.innerHTML = "";
    (buttons && buttons.length ? buttons : [{ label: "Close" }]).forEach(b => {
      const btn = NB.el("button", "btn " + (b.cls || ""));
      btn.type = "button";
      btn.textContent = b.label;
      btn.onclick = () => { NB.closeModal(); if (b.act) b.act(); };
      foot.appendChild(btn);
    });
    NB.$("modal").classList.toggle("wide", !!wide);
    NB.$("modal-scrim").hidden = false;
    NB.$("modal").hidden = false;
  };
  /** A wall of text ORCA wrote — an .inp, a generated preview — in its own
   *  code block. @param {string} title @param {string} text */
  NB.modalCode = function (title, text) {
    NB.modalRaw(title, `<div class="modal-code">${NB.esc(text)}</div>`, null, true);
  };
  NB.closeModal = function () {
    NB.$("modal").hidden = true;
    NB.$("modal-scrim").hidden = true;
  };
  /** @param {string} title @param {string} text @param {string} okLabel @param {() => void} act */
  NB.confirm = function (title, text, okLabel, act) {
    NB.modalRaw(title, `<p>${NB.esc(text)}</p>`, [
      { label: "Cancel", cls: "btn-ghost" },
      { label: okLabel, cls: "btn-danger", act: act },
    ]);
  };

  /** Call a bridge slot and decode its JSON. Errors are DATA on this channel
   *  (P6) — a slot never raises across it — so the only failure this has to
   *  handle is a malformed reply, which becomes {ok:false}.
   *  @param {string} slot @param {...any} args @returns {Promise<any>} */
  NB.call = async function (slot, ...args) {
    try {
      const raw = await NB.bridge[slot](...args);
      if (typeof raw !== "string") return raw;
      return JSON.parse(raw);
    } catch (e) {
      return { ok: false, error: String(e && e.message ? e.message : e) };
    }
  };

  // ---------------------------------------------------------------- trackers
  /* The five convergence trackers are per CALCULATION: several can run at once
     and their tailed output arrives interleaved in one buffer, tagged with the
     calc it came from. ../scf_graph.js and ../progress_panels.js own the
     parsing and the drawing; this only routes lines and remembers who has
     been seeded from disk. */

  /** @type {Map<string, any>} */
  const _jobs = new Map();
  /** calcs whose history has already been replayed from their .out @type {Set<string>} */
  const _seeded = new Set();
  /** seeds in flight, so a slow read is not re-issued every poll @type {Set<string>} */
  const _seeding = new Set();

  function newBundle() {
    return {
      scf: new SCFGraph.SCFTracker(),
      geo: new SCFGraph.GeoTracker(),
      freq: new SCFGraph.FreqTracker(),
      tddft: new SCFGraph.TddftTracker(),
      crest: new SCFGraph.CrestTracker(),
    };
  }
  /** @param {string} name @returns {any} */
  NB.trackers = function (name) {
    let b = _jobs.get(name);
    if (!b) { b = newBundle(); _jobs.set(name, b); }
    return b;
  };
  /** Whether this calculation has produced anything drawable. A calc that only
   *  logged a status line has a bundle but nothing to show, and a chart box for
   *  it would just be an empty axis. @param {string} name */
  NB.hasGraph = function (name) {
    const b = _jobs.get(name);
    if (!b) return false;
    return !!(b.geo.hasData() || b.freq.hasData() || b.tddft.hasData()
              || b.crest.hasData() || b.scf.hasData?.() || b.scf.points.length > 0);
  };

  /** @param {string} name @param {string} msg */
  function feed(name, msg) {
    if (!name) return false;
    const b = NB.trackers(name);
    let changed = false;
    if (b.scf.push(msg)) changed = true;
    if (b.geo.push(msg)) changed = true;
    if (b.freq.push(msg)) changed = true;
    if (b.tddft.push(msg)) changed = true;
    if (b.crest.push(msg)) changed = true;
    return changed;
  }

  /** Replay a calculation's convergence history out of its .out.
   *
   *  The live stream only carries what ORCA writes from the moment the app is
   *  watching — so a run finished in an earlier session, or reattached
   *  mid-flight, has no curve at all until its file is read back. The backend
   *  returns only the lines the trackers care about, windowed, so this costs
   *  the same on a 400 MB output as on a small one.
   *  @param {string} name */
  async function seed(name) {
    if (_seeded.has(name) || _seeding.has(name)) return false;
    _seeding.add(name);
    const r = await NB.call("get_graph_lines", name);
    _seeding.delete(name);
    _seeded.add(name);                       // a file that isn't there won't appear later
    if (!r || !r.ok || !r.lines || !r.lines.length) return false;
    const b = NB.trackers(name);
    r.lines.forEach(ln => {
      b.scf.push(ln); b.geo.push(ln); b.freq.push(ln);
      b.tddft.push(ln); b.crest.push(ln);
    });
    return true;
  }
  NB.seed = seed;

  /** Forget calculations that have left the queue: five trackers and their
   *  point arrays each, otherwise kept for the whole session.
   *  @param {Set<string>} live */
  function pruneTrackers(live) {
    [..._jobs.keys()].forEach(n => {
      if (live.has(n)) return;
      _jobs.delete(n); _seeded.delete(n);
    });
  }

  // ---------------------------------------------------------------- polling

  /** Subscribers repainted after each poll. @type {Array<(changed: boolean) => void>} */
  NB.watchers = [];
  /** @param {(changed: boolean) => void} fn */
  NB.watch = function (fn) { NB.watchers.push(fn); };
  /** @param {boolean} changed the queue snapshot itself moved */
  function publish(changed) { NB.watchers.forEach(fn => { try { fn(changed); } catch (e) { /* one bad watcher must not stop the rest */ } }); }

  async function poll() {
    if (_pollBusy || !NB.bridge) return;
    _pollBusy = true;
    try {
      const snap = await NB.call("get_queue");
      let changed = false;
      if (snap && snap.calculations) {
        changed = snap.version !== _qversion;
        _qversion = snap.version;
        NB.queue = snap.calculations;
        NB.running = !!snap.running;
        NB.resources = snap.resources;
        if (changed) pruneTrackers(new Set(NB.queue.map(c => c.name)));
      }
      const log = await NB.call("get_log", _logSeq);
      let fed = false;
      if (log && log.lines && log.lines.length) {
        log.lines.forEach(ln => { if (feed(ln.calc, ln.msg)) fed = true; });
        _logSeq = log.latest;
      }
      // Anything with output on disk but no curve yet gets replayed once. This
      // is what makes a queue restored from a previous session show its graphs
      // instead of three empty panes.
      const need = NB.queue.filter(c =>
        c.state !== "pending" && c.state !== "blocked" && !_seeded.has(c.name));
      if (need.length) {
        const got = await Promise.all(need.slice(0, 3).map(c => seed(c.name)));
        if (got.some(Boolean)) fed = true;
      }
      publish(changed || fed);
    } finally {
      _pollBusy = false;
    }
  }
  NB.poll = poll;

  // ---------------------------------------------------------------- routing

  /** The window has two places, Jobs and Results. Settings is not a third of
   *  the same kind — it is somewhere you go and come straight back from — so it
   *  is a slide-over, not a view. @param {string} view "jobs" | "results" */
  NB.go = function (view) {
    NB.view = view;
    document.querySelector(".app").setAttribute("data-view", view);
    document.querySelectorAll(".viewseg button[data-view]").forEach(b => {
      const btn = /** @type {HTMLElement} */ (b);
      btn.setAttribute("aria-pressed", String(btn.dataset.view === view));
    });
    if (view === "jobs" && NB.stream) NB.stream.render();
    if (view === "results" && NB.results) NB.results.enter();
  };

  // ---------------------------------------------------------------- theme

  /** @param {string} theme "dark" | "light" */
  NB.applyTheme = function (theme) {
    document.documentElement.setAttribute("data-theme", theme === "light" ? "light" : "dark");
    NB.$("theme-toggle").textContent = theme === "light" ? "☀" : "☽";
  };
  NB.toggleTheme = async function () {
    const next = (NB.settings && NB.settings.theme) === "light" ? "dark" : "light";
    NB.applyTheme(next);
    if (NB.settings) NB.settings.theme = next;
    await NB.call("save_settings", JSON.stringify({ theme: next }));
  };

  // ---------------------------------------------------------------- status pills

  function renderPills() {
    const host = NB.$("pills");
    if (!host || !NB.settings) return;
    const bits = [];
    const runN = NB.queue.filter(c => c.state === "running").length;
    const pendN = NB.queue.filter(c => c.state === "pending" || c.state === "blocked").length;
    if (runN || pendN) {
      bits.push(`<span class="status-pill"><span class="dot ${runN ? "warn" : ""}"></span>${
        runN ? runN + " running" : ""}${runN && pendN ? " · " : ""}${pendN ? pendN + " queued" : ""}</span>`);
    }
    bits.push(`<span class="status-pill"><span class="dot ${NB.settings.orca_valid ? "ok" : ""}"></span>${
      NB.settings.orca_valid ? "ORCA ready" : "ORCA not set"}</span>`);
    host.innerHTML = bits.join("");
  }
  NB.watch(() => renderPills());
  NB.renderPills = renderPills;

  // ---------------------------------------------------------------- start

  NB.start = async function () {
    // settings first: the theme is on it, and a repaint after the first frame
    // is a visible flash of the wrong one
    const s = await NB.call("get_settings");
    NB.settings = s;
    NB.applyTheme(s && s.theme);
    NB.$("ws-path").textContent = shortPath((s && s.workspace_root) || "");

    const about = await NB.call("get_about");
    NB.about = about;
    if (about && about.version) NB.$("ver").textContent = about.version;

    // chrome
    document.querySelectorAll(".viewseg button[data-view]").forEach(b => {
      const btn = /** @type {HTMLElement} */ (b);
      btn.addEventListener("click", () => NB.go(btn.dataset.view));
    });
    NB.$("theme-toggle").addEventListener("click", () => NB.toggleTheme());
    NB.$("open-settings").addEventListener("click", () => NB.settingsView.open());
    NB.$("modal-scrim").addEventListener("click", () => NB.closeModal());
    NB.$("scrim").addEventListener("click", () => NB.settingsView.close());
    document.addEventListener("keydown", e => {
      if (e.key !== "Escape") return;
      if (!NB.$("modal").hidden) NB.closeModal();
      else if (!NB.$("settings").hidden) NB.settingsView.close();
    });
    NB.$("ws-pick").addEventListener("click", async () => {
      const p = await NB.bridge.pick_workspace();
      if (!p) return;
      const res = await NB.call("save_settings", JSON.stringify({ workspace_root: p }));
      if (res && res.error) { NB.fail(res.error); return; }
      NB.settings = res;
      NB.$("ws-path").title = res.workspace_root;
      NB.$("ws-path").textContent = shortPath(res.workspace_root);
      NB.toast("Workspace changed.", "ok");
    });

    if (NB.rail) NB.rail.init();
    if (NB.settingsView) NB.settingsView.init();

    await poll();
    NB.go("jobs");
    setInterval(poll, POLL_MS);
  };

})();

// ---------------------------------------------------------------- connect
new QWebChannel(qt.webChannelTransport, function (channel) {
  NB.bridge = /** @type {any} */ (channel.objects).bridge;
  NB.start();
});
