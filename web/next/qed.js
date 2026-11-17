// @ts-check
"use strict";
/* ============================================================
   ORCAdesk notebook front-end — the queue editor (the queue window).

   The queue window loads this page with ?win=queue and draws only this: the
   inp-set library on the left, the queue as lanes of nodes in the middle,
   and an inspector on the right (DESIGN.md §17.6). The layout and the
   interactions are the approved design canvas's (the "ORCAdesk Queue"
   prototype); its zinc hexes are mapped onto this sheet's tokens.

   It holds NO queue state of its own. Every change is a list of ops sent to
   the backend in one call (apply_queue_ops, validated and applied as a whole
   under the store's lock), and the editor redraws from the next polled
   snapshot. What lives here is only what the backend has no business
   knowing: the selection, a popover being open, a drag in progress, and the
   undo stack -- each entry of which is itself a list of ops (the inverse of
   what was done), sent back the same way.

   Words the code uses:
     lane   one system (a molecule), or one standalone calculation
     node   one block in a lane at (col, row): one calculation, or a STACK of
            several sharing a node id (an SP after an IRC is -F and -B)
     edge   the `after` dependency between two nodes, with its gate
     slot   the ghost "+" after the selected node, where the next step goes
   ============================================================ */

(function () {

  // ------------------------------------------------------------ geometry
  // The design's grid: a 140 x 64 block, 176px a column, 104px a row.
  const W = 140, H = 64, ROWH = 104, LANE_PAD = 28, COLW = 176, X0 = 24;
  /** @param {number} c */
  const X = (c) => X0 + c * COLW;

  const SET_KINDS = ["MLIP", "Opt", "OptTS", "Freq", "IRC", "SP"];
  const EDITABLE = ["pending", "blocked", "cancelled"];
  const STARTED = ["running", "done", "failed"];
  /** the name every new step of a kind gets after the system's: Ltyr-MV-opt */
  const SUFFIX = { MLIP: "pre", Opt: "opt", OptTS: "ts", Freq: "freq", IRC: "irc", SP: "sp" };
  /** what a calculation of each kind is called on its block */
  const KIND_LABEL = {
    mlip_opt: "Preopt", mlip_sp: "MLIP SP", mlip_freq: "MLIP Freq", mlip_opt_freq: "MLIP Opt+Freq",
    opt: "Opt", ts_opt: "OptTS", freq: "Freq", ts_freq: "Freq", irc: "IRC", sp: "SP",
    opt_freq: "Opt+Freq", ts_opt_freq: "OptTS+Freq", neb_ts: "NEB-TS", tddft: "TD-DFT",
    nmr: "NMR", general: "General", crest_conf: "CREST",
  };
  /** the inp-set kind a calculation's kind takes, "" for one no set builds */
  const SET_KIND_OF = { mlip_opt: "MLIP", opt: "Opt", ts_opt: "OptTS", freq: "Freq",
                        ts_freq: "Freq", irc: "IRC", sp: "SP" };
  /** the kinds whose approval gate can pass by itself on the imaginary-mode count */
  const FREQ_KINDS = ["freq", "ts_freq", "opt_freq", "ts_opt_freq"];
  const ST_LABEL = { done: "done", running: "running", waiting: "waiting for approval",
                     pending: "pending", failed: "failed", blocked: "blocked", cancelled: "cancelled" };
  const PLACE_KEYS = ["system", "node", "col", "row", "after", "gate", "auto_pass",
                      "approved", "ref_point", "set_name"];
  const GEOM = "{{GEOMETRY}}";
  const RECIPE_KEY = "queue.drop";
  const SLOTS_NEEDED = ["apply_queue_ops", "get_inp_sets", "save_inp_set", "get_system",
                        "add_xyz_systems", "open_queue_window"];

  // ------------------------------------------------------------ backend mirrors
  /** the inp-set library (get_inp_sets) @type {InpSet[]} */
  let _sets = [];
  let _setsLoaded = false;
  /** each set kind's seeded text, what "+ New" starts from @type {Object<string, string>} */
  let _defaults = {};
  /** The inp set open in the set editor -- the inspector when nothing is
   *  selected. `id` "" is a new set not saved yet; `base` is the text and
   *  name as last saved (or as the new one started), what "unsaved" is
   *  measured against. It survives selecting blocks: a draft is never lost
   *  by looking elsewhere, only by an answered "Discard".
   *  @type {{id: string, kind: string, name: string, text: string, base: {name: string, text: string, kind: string}}|null} */
  let _ed = null;
  /** why the last save of the editor's set was refused, said in the editor */
  let _edErr = "";
  /** name -> the full calculation (get_calc) and the summary it was read for
   *  @type {Map<string, {sig: string, calc: any}>} */
  const _full = new Map();
  /** system id -> the full system, with its inputs' xyz (get_system)
   *  @type {Map<string, SystemFull>} */
  const _sysFull = new Map();
  /** name -> the input a guided calculation's config writes (build_inp_preview),
   *  for the Freq-level warning, and the summary it was built for
   *  @type {Map<string, {sig: string, text: string}>} */
  const _preview = new Map();
  /** slots the running backend does not have (an older one) @type {string[]} */
  let _missing = [];

  // ------------------------------------------------------------ visual state
  /** what is selected: a node (and which member of a stack), an edge, or an
   *  empty system's lane @type {any} */
  let _sel = null;
  /** the stack whose member list is unfolded (a node key), "" for none */
  let _expanded = "";
  /** a drag in progress: {t:"set", id} or {t:"lane", key} @type {any} */
  let _drag = null;
  let _ghostHot = false;
  /** the node just added, drawn with the design's pop once @type {string} */
  let _pop = "";
  /** the undo stack: {label, ops} @type {any[]} */
  const _undo = [];
  /** "Replace the inp set?" is asked once a session, as the design has it */
  let _replaceOk = false;
  /** the builder hosted in the inspector: which open of it the element is */
  let _buildKey = 0, _buildMounted = -1;
  /** @type {HTMLElement|null} */ let _buildCell = null;
  let _buildTarget = /** @type {string|null} */ (null);
  /** the last model drawn (lanes, nodes, edges) @type {any} */
  let _M = null;
  let _active = false;
  let _inspSig = "";
  /** the selection the inspector was last drawn for */
  let _inspFor = "";
  let _libSig = "";

  // ------------------------------------------------------------ helpers

  const esc = (/** @type {any} */ s) => NB.esc(s);
  /** a fresh client-side id: the store takes the ids it is given */
  const newId = (/** @type {string} */ p) =>
    p + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  /** @param {any} v @returns {number} */
  const int = (v) => { const n = Math.round(Number(v)); return isFinite(n) ? n : 0; };
  const lower = (/** @type {string} */ s) => String(s || "").toLowerCase();

  /** The reason a reply is not a success, in words. A slot the backend does
   *  not have is said as such: that is an old backend, not a refusal.
   *  @param {any} r @param {string} what */
  function why(r, what) {
    const e = (r && (r.error || r.message)) || "";
    if (/not a function|undefined/i.test(e)) return what + ": the queue editor needs the updated backend.";
    return what + (e ? ": " + e : ".");
  }

  /** Send ops; on success remember their inverse for Ctrl+Z and repaint.
   *  The ops are QueueOp objects (web/types.js); built as plain literals here,
   *  so they are typed loosely rather than narrowed op by op.
   *  @param {any[]} ops @param {any[]|null} inverse @param {string} label
   *  @returns {Promise<boolean>} */
  async function apply(ops, inverse, label) {
    if (!ops.length) return true;
    const r = await NB.call("apply_queue_ops", JSON.stringify(ops));
    if (!r || r.ok !== true) { NB.fail(why(r, "The queue refused that change")); return false; }
    if (inverse && inverse.length) {
      _undo.push({ label: label, ops: inverse });
      if (_undo.length > 50) _undo.shift();
    }
    // the reply carries the queue as the batch left it: what is drawn next
    // is that, even when a poll already in flight makes NB.poll() a no-op
    if (r.snapshot && Array.isArray(r.snapshot.calculations)) {
      NB.queue = r.snapshot.calculations;
      NB.systems = Array.isArray(r.snapshot.systems) ? r.snapshot.systems : [];
    }
    await NB.poll();
    return true;
  }

  /** apply(), then select what the batch made. Selected only once it is in
   *  the snapshot: a render in between (a drag's end, a poll) drops a
   *  selection whose object does not exist yet, which left a new block
   *  unselected. @param {any[]} ops @param {any[]|null} inverse
   *  @param {string} label @param {any} sel */
  async function applySel(ops, inverse, label, sel) {
    const ok = await apply(ops, inverse, label);
    if (ok) {
      _sel = sel;
      if (sel.t === "node") { _expanded = ""; _pop = sel.key; }
      render();
    }
    return ok;
  }

  async function undo() {
    const u = _undo.pop();
    if (!u) { NB.toast("Nothing to undo."); return; }
    const r = await NB.call("apply_queue_ops", JSON.stringify(u.ops));
    if (!r || r.ok !== true) { NB.fail(why(r, "Could not undo " + u.label)); return; }
    NB.toast("Undone: " + u.label + ".");
    await NB.poll();
  }

  /** The calculation as the store holds it (get_calc), with the queue
   *  fields the poll carries, plus `patch`: what an add_calc op sends (a
   *  copy, an undone removal). Before its full record has been read only what
   *  the poll knows is in it -- never an empty xyz / raw_text / config
   *  standing in for the real ones.
   *  @param {string} name @param {any} [patch] */
  function calcDict(name, patch) {
    const f = _full.get(name);
    const c = NB.queue.find((/** @type {any} */ x) => x.name === name) || {};
    const base = Object.assign({}, f ? f.calc : {
      name: c.name, kind: c.kind, charge: c.charge, multiplicity: c.multiplicity,
      geometry_source: c.geometry_source, ref_name: c.ref_name, is_raw: c.is_raw,
    });
    PLACE_KEYS.forEach(k => { if (k in c) base[k] = c[k]; });
    return Object.assign(base, patch || {});
  }
  /** update_calc ops for some calculations, and the ops that undo them. The
   *  store takes update_calc as a PATCH, so each op carries only the keys it
   *  changes, and its inverse those keys' values before -- a key whose old
   *  value is not known here is left out of the undo rather than guessed.
   *  @param {string[]} names @param {(name: string) => any} patchOf */
  function updates(names, patchOf) {
    const ops = [], inv = [];
    names.forEach(n => {
      const patch = patchOf(n), was = calcDict(n), back = {};
      Object.keys(patch).forEach(k => { if (k in was) back[k] = was[k]; });
      ops.push({ op: "update_calc", name: n, calc: patch });
      if (Object.keys(back).length) inv.push({ op: "update_calc", name: n, calc: back });
    });
    return { ops: ops, inv: inv };
  }

  /** @param {string} id */
  async function systemFull(id) {
    if (_sysFull.has(id)) return _sysFull.get(id);
    const r = await NB.call("get_system", id);
    if (r && r.ok && r.system) { _sysFull.set(id, r.system); return r.system; }
    return null;
  }

  async function loadSets() {
    const r = await NB.call("get_inp_sets");
    _setsLoaded = true;
    _sets = r && r.ok && Array.isArray(r.sets) ? r.sets : [];
    _defaults = r && r.ok && r.defaults && typeof r.defaults === "object" ? r.defaults : {};
    return _sets;
  }
  /** @param {string} name @param {string} kind */
  const setByName = (name, kind) =>
    _sets.find(s => s.name === name && (!kind || s.kind === kind)) || null;

  /** The kind a template's text is, read off its job line -- the design's
   *  auto-detection for "Save as". @param {string} text */
  function detectKind(text) {
    if (/^\s*(backend|model|mlip)\b/im.test(text) && !/^\s*!/m.test(text)) return "MLIP";
    const line = (String(text).split("\n").find(l => l.trim().indexOf("!") === 0) || "")
      .toUpperCase().replace(/!/g, " ");
    const w = line.split(/\s+/);
    if (w.indexOf("OPTTS") >= 0 || w.indexOf("SCANTS") >= 0) return "OptTS";
    if (w.indexOf("IRC") >= 0) return "IRC";
    if (w.some(x => /^(OPT|TIGHTOPT|LOOSEOPT|VERYTIGHTOPT|COPT|ZOPT)$/.test(x))) return "Opt";
    if (w.some(x => /^(FREQ|NUMFREQ|ANFREQ)$/.test(x))) return "Freq";
    return "SP";
  }
  /** The level of theory a template runs at: its ! keywords without the job
   *  and convergence ones, so an Opt and its Freq compare. @param {string} text */
  function levelOf(text) {
    const JOB = /^(OPT|TIGHTOPT|LOOSEOPT|VERYTIGHTOPT|COPT|ZOPT|OPTTS|SCANTS|FREQ|NUMFREQ|ANFREQ|IRC|SP|ENGRAD|(VERY|EXTREME)?TIGHTSCF|NORMALSCF|LOOSESCF|SLOPPYSCF|STRONGSCF|EXTREMESCF)$/;
    return String(text || "").split("\n").filter(l => l.trim().indexOf("!") === 0)
      .join(" ").toUpperCase().replace(/!/g, " ").split(/\s+/)
      .filter(x => x && !JOB.test(x)).sort().join(" ");
  }
  /** The calculation kind a set builds after `parent`. @param {string} setKind @param {any} parent */
  function kindFor(setKind, parent) {
    const pk = parent ? parent.members[0].kind : "";
    return { MLIP: "mlip_opt", Opt: "opt", OptTS: "ts_opt", IRC: "irc", SP: "sp",
             Freq: pk === "ts_opt" || pk === "ts_opt_freq" ? "ts_freq" : "freq" }[setKind] || "sp";
  }

  // ------------------------------------------------------------ the model

  /** The lanes, nodes and edges, built from the polled snapshot alone. Run
   *  order is the queue's own: lanes in the order their first calculation
   *  appears, systems with nothing queued yet at the end. */
  function model() {
    const q = NB.queue;
    const idx = new Map(q.map((/** @type {any} */ c, /** @type {number} */ i) => [c.name, i]));
    doomedFrom(q);
    const sysById = new Map(NB.systems.map((/** @type {any} */ s) => [s.id, s]));
    /** @type {any[]} */ const lanes = [];
    const laneOf = new Map();
    q.forEach((/** @type {any} */ c) => {
      const sid = c.system && sysById.has(c.system) ? c.system : "";
      const key = sid ? "s:" + sid : "c:" + c.name;
      let L = laneOf.get(key);
      if (!L) {
        L = { key: key, sys: sid ? sysById.get(sid) : null, calcs: [], nodes: [], edges: [] };
        laneOf.set(key, L); lanes.push(L);
      }
      L.calcs.push(c);
    });
    NB.systems.forEach((/** @type {any} */ s) => {
      if (laneOf.has("s:" + s.id)) return;
      const L = { key: "s:" + s.id, sys: s, calcs: [], nodes: [], edges: [] };
      laneOf.set(L.key, L); lanes.push(L);
    });
    const nodeOf = new Map();
    lanes.forEach(L => {
      const byKey = new Map();
      L.calcs.forEach((/** @type {any} */ c) => {
        const nk = L.sys && c.node ? L.key + "/" + c.node : "c:" + c.name;
        let n = byKey.get(nk);
        if (!n) {
          n = { key: nk, id: c.node || "", lane: L, members: [],
                col: L.sys ? Math.max(0, int(c.col)) : 0, row: L.sys ? Math.max(0, int(c.row)) : 0 };
          byKey.set(nk, n); L.nodes.push(n);
        }
        n.members.push(c);
        nodeOf.set(c.name, n);
      });
      L.nodes.forEach((/** @type {any} */ n) => {
        n.stack = n.members.length > 1;
        n.first = idx.get(n.members[0].name) + 1;
        n.kind = n.members[0].kind;
        n.setKind = SET_KIND_OF[n.kind] || "";
        n.state = nodeState(n);
        n.started = n.members.some((/** @type {any} */ c) => STARTED.indexOf(c.state) >= 0);
        n.editable = n.members.every((/** @type {any} */ c) => EDITABLE.indexOf(c.state) >= 0);
        n.name = n.stack ? stem(n.members[0].name) : n.members[0].name;
        n.short = n.stack ? n.name + " +" + (n.members.length - 1) : n.name;
      });
    });
    lanes.forEach(L => {
      const seen = new Set();
      L.calcs.forEach((/** @type {any} */ c) => {
        if (!c.after) return;
        const A = nodeOf.get(c.after), B = nodeOf.get(c.name);
        if (!A || A.lane !== L || A === B) return;
        const k = A.key + ">" + B.key;
        if (seen.has(k)) return;
        seen.add(k);
        L.edges.push({ key: k, from: A, to: B, gate: c.gate === "approval" ? "approval" : "auto" });
      });
      L.started = L.calcs.some((/** @type {any} */ c) => STARTED.indexOf(c.state) >= 0);
      L.name = L.sys ? String(L.sys.name || "") : L.calcs[0].name;
    });
    return { lanes: lanes, nodeOf: nodeOf, idx: idx };
  }
  /** A name without its last "-part": the stack Ltyr-MV-sp of Ltyr-MV-sp-F. @param {string} s */
  function stem(s) { const i = s.lastIndexOf("-"); return i > 0 ? s.slice(0, i) : s; }

  /** Pending calculations that cannot run because something above them, on
   *  the `after` or the geometry chain, has failed or is blocked: drawn as
   *  blocked now, as the design derives it, not only once a run has said so.
   *  @type {Set<string>} */
  let _doomed = new Set();
  /** @param {any[]} q */
  function doomedFrom(q) {
    const by = new Map(q.map((/** @type {any} */ c) => [c.name, c]));
    /** @type {Map<string, boolean>} */ const memo = new Map();
    /** @param {any} c @param {Set<string>} seen @returns {boolean} */
    const bad = (c, seen) => {
      if (memo.has(c.name)) return /** @type {boolean} */ (memo.get(c.name));
      if (seen.has(c.name)) return false;
      seen.add(c.name);
      let out = false;
      const ups = [c.after, c.geometry_source === "reference" ? c.ref_name : ""];
      for (const u of ups) {
        const p = u ? by.get(u) : null;
        if (!p || p === c) continue;
        if (p.state === "failed" || p.state === "blocked" || (p.state === "running" && p.faulted)
            || ((p.state === "pending" || p.state === "cancelled") && bad(p, seen))) { out = true; break; }
      }
      memo.set(c.name, out);
      return out;
    };
    _doomed = new Set(q.filter((/** @type {any} */ c) => c.state === "pending" && bad(c, new Set()))
      .map((/** @type {any} */ c) => c.name));
  }

  /** @param {any} n */
  function nodeState(n) {
    const st = n.members.map((/** @type {any} */ c) =>
      c.state === "running" && c.faulted ? "failed"
        : c.state === "pending" && _doomed.has(c.name) ? "blocked"
        : c.state === "pending" && c.waiting ? "waiting" : c.state);
    for (const s of ["running", "failed", "waiting", "blocked", "pending", "cancelled"]) {
      if (st.indexOf(s) >= 0) return s;
    }
    return "done";
  }

  /** @param {string} key */
  const nodeByKey = (key) => {
    if (!_M) return null;
    for (const L of _M.lanes) { const n = L.nodes.find((/** @type {any} */ x) => x.key === key); if (n) return n; }
    return null;
  };
  /** @param {string} key */
  const laneByKey = (key) => (_M ? _M.lanes.find((/** @type {any} */ L) => L.key === key) : null) || null;
  const selNode = () => (_sel && _sel.t === "node" ? nodeByKey(_sel.key) : null);
  const selMember = () => {
    const n = selNode();
    return n ? n.members[Math.min(_sel.m || 0, n.members.length - 1)] : null;
  };
  /** the selected edge, looked up again in the current model */
  const selEdge = () => {
    if (!_sel || _sel.t !== "edge" || !_M) return null;
    for (const L of _M.lanes) { const e = L.edges.find((/** @type {any} */ x) => x.key === _sel.key); if (e) return e; }
    return null;
  };

  /** Where the selected node can take a next calculation: the next column,
   *  same row if free, else the first row below that is free in both this
   *  column and the one before (the design's rule). An empty system's lane
   *  always offers its first slot. @returns {any} */
  function slotOf(M) {
    if (_sel && _sel.t === "sys") {
      const L = M.lanes.find((/** @type {any} */ x) => x.key === _sel.key);
      if (L && L.sys && !L.nodes.length) return { lane: L, parent: null, c: 0, r: 0, branch: false, key: L.key + "@0,0" };
    }
    const P = _sel && _sel.t === "node" ? M.lanes.flatMap((/** @type {any} */ L) => L.nodes)
      .find((/** @type {any} */ n) => n.key === _sel.key) : null;
    if (!P) return null;
    // After ANY block, finished or not, as the design has it: a next step
    // after a done OptTS or IRC is how a follow-up is queued. A standalone
    // block becomes a one-node system first (adopt), which moves it in the
    // layout only -- nothing it ran or produced changes (P24).
    const L = P.lane;
    const occ = (/** @type {number} */ c, /** @type {number} */ r) =>
      L.nodes.some((/** @type {any} */ n) => n.col === c && n.row === r);
    const c = P.col + 1;
    let r = P.row;
    if (occ(c, r)) { r = P.row + 1; while (occ(c, r) || occ(c - 1, r)) r += 1; }
    return { lane: L, parent: P, c: c, r: r, branch: r !== P.row, key: L.key + "@" + c + "," + r };
  }

  // ------------------------------------------------------------ render

  function render() {
    if (!_active) return;
    _M = model();
    // a selection whose object has gone is dropped, not pointed at nothing
    if (_sel && _sel.t === "node" && !nodeByKey(_sel.key)) _sel = null;
    if (_sel && _sel.t === "edge" && !selEdge()) _sel = null;
    if (_sel && _sel.t === "sys" && !laneByKey(_sel.key)) _sel = null;
    renderBar();
    if (!_drag) { renderLibrary(); renderCanvas(); }
    renderInspector();
  }

  function renderBar() {
    const anyRunnable = NB.queue.some((/** @type {any} */ c) => c.state === "pending" || c.state === "blocked");
    NB.$("qe-run").disabled = NB.running || !anyRunnable;
    NB.$("qe-stop").disabled = !NB.running;
    NB.$("qe-cancel").disabled = !NB.running;
    NB.$("qe-clear").disabled = !NB.queue.length;
    const w = warnings(_M);
    const el = NB.$("qe-warn");
    el.hidden = !w.length;
    el.textContent = w.length ? w[0] + (w.length > 1 ? `  (+${w.length - 1} more)` : "") : "";
    el.title = w.join("\n");
  }

  /** What would stop the queue doing what it looks like it will, worst first.
   *  @param {any} M @returns {string[]} */
  function warnings(M) {
    const w = [];
    if (_missing.length) w.push("The queue editor needs the updated backend (missing: " + _missing.join(", ") + ").");
    const names = NB.systems.map((/** @type {any} */ s) => String(s.name || ""));
    if (names.some((/** @type {string} */ x) => !x.trim())) w.push("A row needs a name.");
    const dup = names.find((/** @type {string} */ x, /** @type {number} */ i) =>
      x.trim() && names.findIndex((/** @type {string} */ y) => lower(y) === lower(x)) !== i);
    if (dup) w.push(dup + " is already in the queue. Names must be unique.");
    M.lanes.forEach((/** @type {any} */ L) => {
      L.edges.forEach((/** @type {any} */ e) => {
        if (e.to.setKind !== "Freq" || (e.from.setKind !== "Opt" && e.from.setKind !== "OptTS")) return;
        const la = levelOf(levelText(e.from.members[0])), lb = levelOf(levelText(e.to.members[0]));
        if (la && lb && la !== lb) w.push(e.to.short + ": Freq level differs from " + e.from.short + ".");
      });
      if (L.sys && !(L.sys.inputs || []).length
          && L.calcs.some((/** @type {any} */ c) => !c.after && c.geometry_source !== "reference"
                                                && EDITABLE.indexOf(c.state) >= 0)) {
        w.push(L.name + ": choose a structure for the first calculation.");
      }
      const noGeo = L.calcs.find((/** @type {any} */ c) => c.set_name && SET_KIND_OF[c.kind] !== "MLIP"
        && _full.has(c.name) && templateOf(c).indexOf(GEOM) < 0);
      if (noGeo) w.push(noGeo.name + ": its template has no " + GEOM + " placeholder.");
    });
    return w;
  }

  /** The input whose level of theory a block runs at: its template, else
   *  (a block built with the Guided builder -- a finished OptTS adopted into
   *  a row, typically) the input the builder writes for it, read once per
   *  change (refreshFull). @param {any} c */
  function levelText(c) {
    const t = templateOf(c);
    if (t) return t;
    const p = _preview.get(c.name);
    return p ? p.text : "";
  }

  /** The template text a calculation runs: its own raw text, or (an MLIP
   *  block not yet read) its set's. @param {any} c */
  function templateOf(c) {
    const f = _full.get(c.name);
    const own = f && f.calc ? String(f.calc.raw_text || "") : "";
    if (own) return own;
    const s = c.set_name ? setByName(c.set_name, SET_KIND_OF[c.kind]) : null;
    return s ? s.text : "";
  }

  // ---- the library

  function renderLibrary() {
    const host = NB.$("qe-sets");
    const n = selNode();
    const editing = editorShown() && _ed ? _ed : null;
    const sig = JSON.stringify([_sets.map(s => [s.id, s.name, s.kind]), n ? [n.key, n.setKind, n.editable,
      n.members[0].set_name] : null, _missing.length,
      editing ? [editing.id, editing.kind, editing.id ? "" : editing.name] : null]);
    if (sig === _libSig) return;
    _libSig = sig;
    if (_missing.length && !_sets.length) {
      host.innerHTML = `<p class="hint qe-pad">The queue editor needs the updated backend: it has no inp sets to offer.</p>`;
      NB.$("qe-dragimgs").innerHTML = "";
      renderRecipe();
      return;
    }
    // every kind has its group, an empty one too: "+ New" is where a kind's
    // first set comes from
    host.innerHTML = SET_KINDS.map(kd => {
      const sets = _sets.filter(s => s.kind === kd);
      const match = !!n && n.setKind === kd;
      const draft = editing && !editing.id && editing.kind === kd
        ? `<span class="qe-set" data-edit="1" data-draft="1">${esc(editing.name || "New set")} · unsaved</span>` : "";
      return `<div class="qe-group">
        <div class="qe-ghead"><span class="qe-kind"${match ? ' data-on="1"' : ""}>${esc(kd)}</span>
          <button class="qe-new" type="button" data-new="${kd}" aria-label="New ${esc(kd)} set"
            title="A new ${esc(kd)} set, starting from ORCAdesk's default ${esc(kd)} input">+ New</button></div>
        ${sets.map(s => {
          const cur = !!n && n.members[0].set_name === s.name && n.setKind === kd;
          const applies = match && n.editable;
          const ed = !!editing && editing.id === s.id;
          return `<button class="qe-set" type="button" draggable="true" data-id="${esc(s.id)}"
            aria-pressed="${applies ? cur : ed}"${cur ? ' data-cur="1"' : ""}${match ? ' data-match="1"' : ""}${ed ? ' data-edit="1"' : ""}
            aria-label="${esc((applies ? "Apply " : "Edit ") + kd + " set " + s.name)}"
            title="${applies ? "Click to put it on the selected block. " : "Click to edit it. "}Drag onto a block or an empty slot. Delete removes the set.">${esc(s.name)}</button>`;
        }).join("")}${draft}
      </div>`;
    }).join("");
    NB.$("qe-dragimgs").innerHTML = _sets.map(s =>
      `<div class="qe-dragimg" id="qe-di-${esc(s.id)}"><span class="qe-nm">${
        esc(s.kind === "MLIP" ? "Preopt" : s.kind)}</span><span class="qe-sub">${esc(s.name)}</span></div>`).join("");
    renderRecipe();
  }

  /** The drop recipe: what a dropped .xyz runs. */
  async function recipe() {
    if (!_setsLoaded) await loadSets();
    const r = await NB.call("get_ui_state", RECIPE_KEY);
    const v = r && r.ok && r.value && typeof r.value === "object" ? r.value : {};
    const sps = _sets.filter(s => s.kind === "SP");
    const set = sps.find(s => s.id === v.set_id) || sps[0] || null;
    return { set_id: set ? set.id : "", suffix: typeof v.suffix === "string" ? v.suffix : "-sp" };
  }
  async function renderRecipe() {
    const box = NB.$("qe-recipe");
    const sps = _sets.filter(s => s.kind === "SP");
    if (!sps.length) { box.hidden = true; return; }
    const rec = await recipe();
    box.hidden = false;
    const sel = NB.$("qe-rset");
    sel.innerHTML = sps.map(s => `<option value="${esc(s.id)}"${s.id === rec.set_id ? " selected" : ""}>${esc(s.name)}</option>`).join("");
    if (document.activeElement !== NB.$("qe-rsfx")) NB.$("qe-rsfx").value = rec.suffix;
  }
  function saveRecipe() {
    NB.call("set_ui_state", RECIPE_KEY, JSON.stringify({
      set_id: NB.$("qe-rset").value, suffix: NB.$("qe-rsfx").value }));
  }

  // ---- the canvas

  function renderCanvas() {
    const M = _M;
    const host = NB.$("qe-graph");
    const scroller = NB.$("qe-canvas");
    const keep = [scroller.scrollLeft, scroller.scrollTop];
    const slot = slotOf(M);
    const parts = [];
    let top = 0, maxCol = 0;
    const laneTop = new Map();
    const buildingAt = (() => { const p = NB.build.place(); return p && p.slot ? p.slot : ""; })();
    parts.push(`<button type="button" class="qe-bg" data-a="clear" tabindex="-1" aria-label="Clear selection"></button>`);
    M.lanes.forEach((/** @type {any} */ L, /** @type {number} */ li) => {
      laneTop.set(L.key, top);
      const rows = L.nodes.map((/** @type {any} */ n) => n.row).concat(slot && slot.lane === L ? [slot.r] : [0]);
      const maxRow = Math.max.apply(null, rows);
      L.nodes.forEach((/** @type {any} */ n) => { maxCol = Math.max(maxCol, n.col); });
      if (slot && slot.lane === L) maxCol = Math.max(maxCol, slot.c);
      if (li > 0) parts.push(`<div class="qe-rule" style="top:${top}px"></div>`);
      if (L.calcs.length) {
        parts.push(`<button class="qe-grip" type="button" draggable="true" data-a="grip" data-lane="${esc(L.key)}"
          style="top:${top + LANE_PAD + 6}px"
          aria-label="Row ${esc(L.name)}. Drag to move, hold Ctrl to copy. Press to copy below. Alt+Up or Alt+Down moves it."
          title="Drag to move. Hold Ctrl while dropping to copy. Click to copy below. Alt+Up or Alt+Down moves the row.">⋮</button>`);
      } else {
        // an empty system: its name is the handle that selects it
        const on = _sel && _sel.t === "sys" && _sel.key === L.key;
        parts.push(`<button class="qe-cap" type="button" data-a="sys" data-lane="${esc(L.key)}"
          style="left:${X(1) - 8}px; top:${top + LANE_PAD + 20}px"${on ? ' aria-current="true"' : ""}
          aria-label="System ${esc(L.name)}, nothing queued yet">${esc(L.name || "(unnamed)")} · ${
            (L.sys.inputs || []).length ? esc(L.sys.inputs[0].label || "structure") : "no structure yet"}</button>`);
      }
      L.top = top;
      top += LANE_PAD + maxRow * ROWH + 100;
    });
    const Y = (/** @type {any} */ L, /** @type {number} */ r) => laneTop.get(L.key) + LANE_PAD + r * ROWH;

    // edges, under the blocks
    M.lanes.forEach((/** @type {any} */ L) => L.edges.forEach((/** @type {any} */ e) => {
      const A = e.from, B = e.to;
      const on = _sel && _sel.t === "edge" && _sel.key === e.key;
      const attrs = `data-gate="${e.gate}"${on ? ' data-sel="1"' : ""}`;
      const aria = esc("Connection " + A.short + " to " + B.short + (e.gate === "approval" ? ", after approval" : ""));
      const ya = Y(L, A.row) + 22, yb = Y(L, B.row) + 22;
      const hseg = (/** @type {number} */ x1, /** @type {number} */ x2, /** @type {number} */ y) =>
        `<button type="button" class="qe-edge" data-a="edge" data-k="${esc(e.key)}" aria-label="${aria}"
          style="left:${x1 + 6}px; top:${y - 12}px; width:${Math.max(0, x2 - x1 - 12)}px; height:24px">
          <span class="qe-eline" data-dir="h" ${attrs}></span><span class="qe-ehead" ${attrs}></span></button>`;
      if (A.row === B.row) parts.push(hseg(X(A.col) + W, X(B.col), ya));
      else {
        const bx = X(A.col) + W + 18;
        parts.push(`<button type="button" class="qe-edge" data-a="edge" data-k="${esc(e.key)}" aria-label="${aria}"
          style="left:${bx - 12}px; top:${ya + 1}px; width:24px; height:${yb - ya}px">
          <span class="qe-eline" data-dir="v" ${attrs}></span></button>`);
        parts.push(hseg(bx - 6, X(B.col), yb));
      }
    }));

    // the blocks
    M.lanes.forEach((/** @type {any} */ L) => L.nodes.forEach((/** @type {any} */ n) => {
      const sel = _sel && _sel.t === "node" && _sel.key === n.key;
      const open = n.stack && _expanded === n.key && sel;
      const c0 = n.members[0];
      const mod = isModified(n);
      const setName = c0.set_name || (c0.is_raw ? ".inp text" : "guided");
      const aria = (n.stack ? `${n.name}, ${n.members.length} calculations` : `Select ${n.name}`)
        + ", " + ST_LABEL[n.state];
      parts.push(`<div class="qe-node" data-k="${esc(n.key)}" data-setkind="${n.setKind}"${
          n.editable ? ' data-editable="1"' : ""}${_pop === n.key ? ' data-pop="1"' : ""}
        style="left:${X(n.col)}px; top:${Y(L, n.row)}px; z-index:${open ? 20 : 2}">
        ${n.stack ? `<div class="qe-back" data-depth="2"></div><div class="qe-back" data-depth="1"></div>` : ""}
        <div class="qe-card"${sel ? ' data-sel="1"' : ""} data-st="${n.state}">
          <button type="button" class="qe-pick" data-a="node" data-k="${esc(n.key)}"
            aria-label="${esc(aria)}"${n.stack ? ` aria-expanded="${!!open}"` : ""}>
            <span class="qe-line"><span class="qe-dot" data-st="${n.state}"></span>
              <span class="qe-nm">${esc(n.name)}</span>
              ${n.stack
                ? `<span class="qe-ord" data-stack="1" aria-label="${n.members.length} calculations">${n.members.length}</span>`
                : `<span class="qe-ord" aria-label="Run order ${n.first}">${n.first}</span>`}</span>
            <span class="qe-sub"${mod ? ' data-mod="1"' : ""}>${esc((KIND_LABEL[n.kind] || n.kind) + " / " + setName)}${mod ? " *" : ""}</span>
          </button>
          ${open ? `<div class="qe-pop" role="list">${n.members.map((/** @type {any} */ c, /** @type {number} */ i) =>
              `<button type="button" class="qe-prow" role="listitem" data-a="member" data-k="${esc(n.key)}" data-m="${i}"${
                (_sel.m || 0) === i ? ' data-on="1"' : ""}
                aria-label="Select ${esc(c.name)}, run order ${M.idx.get(c.name) + 1}"><span class="qe-nm">${esc(c.name)}</span><span
                class="qe-ord">${M.idx.get(c.name) + 1}</span></button>`).join("")}</div>` : ""}
        </div>
      </div>`);
    }));

    // the ghost slot, and every empty system's first one
    const ghosts = [];
    if (slot) ghosts.push(slot);
    M.lanes.forEach((/** @type {any} */ L) => {
      if (L.sys && !L.nodes.length && !(slot && slot.lane === L)) {
        ghosts.push({ lane: L, parent: null, c: 0, r: 0, branch: false, key: L.key + "@0,0", idle: true });
      }
    });
    ghosts.forEach(g => {
      const P = g.parent;
      if (P) {
        const ya = Y(g.lane, P.row) + 22, yb = Y(g.lane, g.r) + 22;
        if (!g.branch) {
          parts.push(`<span class="qe-gline" style="left:${X(P.col) + W + 6}px; top:${ya}px; width:${X(g.c) - X(P.col) - W - 12}px"></span>`);
        } else {
          const bx = X(P.col) + W + 18;
          parts.push(`<span class="qe-gline" data-dir="v" style="left:${bx}px; top:${ya}px; height:${yb - ya}px"></span>`);
          parts.push(`<span class="qe-gline" style="left:${bx}px; top:${yb}px; width:${X(g.c) - bx - 6}px"></span>`);
        }
      }
      const picking = buildingAt === g.key;
      const target = P ? "after " + P.short : "as the first calculation of " + g.lane.name;
      parts.push(`<div class="qe-ghost" data-slot="${esc(g.key)}"${picking ? ' data-picking="1"' : ""}${
          P && P.stack ? ' data-afterstack="1"' : ""}
        style="left:${X(g.c)}px; top:${Y(g.lane, g.r)}px">
        ${picking
          ? `<span class="qe-gtext"><span>Manual build</span><span class="qe-gsub">in the inspector</span></span>`
          : `<button class="qe-gbtn" type="button" data-a="ghost" data-slot="${esc(g.key)}"
              aria-label="${esc("Add a calculation " + target + ". Drop an inp set here, or press for a manual build.")}"
              title="Drop an inp set here, or click for a manual build">+</button>`}
      </div>`);
    });

    if (!M.lanes.length) {
      // the empty queue says how a row starts, where the rows would be
      parts.push(`<div class="qe-empty" style="top:${top + 24}px">
        <p>Nothing in the queue.</p>
        <p class="hint">Drop .xyz files on this window: each becomes a row that runs the set chosen
          under the inp sets. Or press + system to start a row from a structure and build its steps here.</p></div>`);
      top += 132;
    }
    parts.push(`<button class="qe-addsys" type="button" data-a="addsys" style="top:${top + 6}px"
      aria-label="Add a system from a structure file">+ system</button>`);
    host.style.width = (X(maxCol + 2) + 40) + "px";
    host.style.height = (top + 48) + "px";
    host.innerHTML = parts.join("");
    host._ghosts = ghosts;
    host._laneTops = M.lanes.map((/** @type {any} */ L) => L.top).concat([top]);
    scroller.scrollLeft = keep[0]; scroller.scrollTop = keep[1];
    if (_pop) setTimeout(() => { _pop = ""; }, 300);
  }

  /** Whether a block's template is no longer its set's (the design's *).
   *  @param {any} n */
  function isModified(n) {
    const c = n.members[0];
    if (!c.set_name) return false;
    const s = setByName(c.set_name, n.setKind);
    const f = _full.get(c.name);
    if (!s || !f || !f.calc) return false;
    const own = String(f.calc.raw_text || "");
    return !!own && own !== s.text;
  }

  // ---- the inspector

  function renderInspector() {
    const host = NB.$("qe-insp");
    const app = NB.$("qed").querySelector(".qe-app");
    const t = NB.build.target();
    if (t !== null) {
      app.setAttribute("data-building", "1");
      if (_buildMounted !== _buildKey || _buildTarget !== t || !_buildCell || !host.contains(_buildCell)) {
        const c = t ? NB.queue.find((/** @type {any} */ x) => x.name === t) || null : null;
        _buildCell = NB.build.cellFor(c);
        _buildMounted = _buildKey; _buildTarget = t;
        host.innerHTML = "";
        host.appendChild(_buildCell);
        _inspSig = "";
      }
      return;
    }
    app.removeAttribute("data-building");
    if (_buildCell) { _buildCell = null; _buildTarget = null; _inspSig = ""; }
    // never repaint under the cursor: a field being typed in is the user's --
    // while it still shows what is selected; another selection is drawn
    const a = document.activeElement;
    const what = JSON.stringify(_sel);
    if (a && host.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) && what === _inspFor) return;
    _inspFor = what;
    const n = selNode(), e = selEdge();
    const L = _sel && _sel.t === "sys" ? laneByKey(_sel.key) : null;
    if (!n && !e && !L) edEnsure();
    const sig = JSON.stringify([_sel, !n && !e && !L ? [_ed && [_ed.id, _ed.kind, _ed.base], _edErr,
        _sets.map(x => [x.id, x.name, x.kind]), edUsers().map((/** @type {any} */ c) => [c.name, c.state])] : null,
      n ? n.members.map((/** @type {any} */ c) => [c, _full.get(c.name) && _full.get(c.name).calc]) : null,
      e ? [e.gate, e.to.members, e.from.members] : null, L ? L.sys : null,
      n ? (n.lane.sys || null) : null, n ? isModified(n) : null, _sets.length]);
    if (sig === _inspSig) return;
    _inspSig = sig;
    if (n) inspectNode(host, n);
    else if (e) inspectEdge(host, e);
    else if (L) inspectSystem(host, L);
    else inspectSets(host);
  }

  /** @param {HTMLElement} host @param {any} n */
  async function inspectNode(host, n) {
    const L = n.lane;
    const m = Math.min(_sel.m || 0, n.members.length - 1);
    const c = n.members[m];
    const sys = L.sys;
    const sysName = sys ? String(sys.name || "") : c.name;
    const suffix = sys && c.name.toLowerCase().indexOf(lower(sysName)) === 0 ? c.name.slice(sysName.length) : "";
    const nameLocked = L.started || !sys;
    const locked = EDITABLE.indexOf(c.state) < 0;
    const f = _full.get(c.name);
    const full = f ? f.calc : null;
    const isTemplate = !!c.set_name || (c.is_raw && !!sys);
    const isMlip = SET_KIND_OF[c.kind] === "MLIP" && !!c.set_name;
    const text = isTemplate ? templateOf(c) : "";
    const mod = isModified(n);
    const kind = detectKind(text);
    const charge = sys ? sys.charge : c.charge, mult = sys ? sys.multiplicity : c.multiplicity;

    // the geometry choices: the system's structures, then the outputs of
    // every ancestor block along the `after` chain -- each member of a stack,
    // an IRC's two ends -- as the design lists them
    const opts = [];
    (sys ? sys.inputs || [] : []).forEach((/** @type {any} */ inp, /** @type {number} */ i) =>
      opts.push({ key: "in:" + i, label: inp.label || "structure " + (i + 1) }));
    const seen = new Set([c.name]);
    const blocks = new Set([n.key]);
    for (let a = c.after; a && !seen.has(a); ) {
      seen.add(a);
      const p = NB.queue.find((/** @type {any} */ x) => x.name === a);
      if (!p) break;
      const pn = _M.nodeOf.get(p.name);
      const mem = pn && pn.lane === L ? (blocks.has(pn.key) ? [] : pn.members) : [p];
      if (pn) blocks.add(pn.key);
      mem.forEach((/** @type {any} */ m) => {
        opts.push({ key: "ref:" + m.name, label: "ref → " + m.name });
        if (m.kind === "irc") {
          opts.push({ key: "ref:" + m.name + ":irc_f", label: "ref → " + m.name + ", forward end" });
          opts.push({ key: "ref:" + m.name + ":irc_b", label: "ref → " + m.name + ", backward end" });
        }
      });
      a = p.after;
    }
    // what it runs after, said in words when no connection on this row shows
    // it (an `after` in another row, or gone), and its gate when it waits
    const pa = c.after ? NB.queue.find((/** @type {any} */ x) => x.name === c.after) : null;
    const paNode = pa ? _M.nodeOf.get(pa.name) : null;
    const afterNote = !c.after ? ""
      : !pa ? `Runs after ${c.after}, which is not in the queue: it cannot run.`
      : paNode && paNode.lane !== L ? `Runs after ${pa.name}, in another row${
          c.gate === "approval" ? ", once approved" : ""}.` : "";
    const blockedNote = _doomed.has(c.name) && !c.message
      ? "Blocked: a calculation it depends on has failed. Remove or replace that one first." : "";
    let geo = "";
    if (c.geometry_source === "reference") {
      geo = "ref:" + c.ref_name + (c.ref_point ? ":" + c.ref_point : "");
      if (!opts.some(o => o.key === geo)) opts.push({ key: geo, label: "ref → " + c.ref_name + (c.ref_point ? " (" + c.ref_point + ")" : "") });
    } else {
      const sf = sys ? await systemFull(sys.id) : null;
      const xyz = full ? String(full.xyz || "").trim() : "";
      const at = sf ? (sf.inputs || []).findIndex((/** @type {any} */ i) => String(i.xyz || "").trim() === xyz && xyz) : -1;
      geo = at >= 0 ? "in:" + at : "own";
      if (at < 0) opts.unshift({ key: "own", label: xyz ? "its own coordinates" : "no structure yet" });
    }
    // the selection moved while the structures were read: that one draws
    if (!host.isConnected || !_sel || _sel.key !== n.key || NB.build.target() !== null) return;

    host.innerHTML = `
      <div class="qe-ititle">
        ${sys ? `<input class="qe-sysname" data-f="sysname" value="${esc(sysName)}" spellcheck="false"
            style="width:${Math.max(sysName.length, 2) + 0.5}ch"${nameLocked ? " readonly" : ""}
            aria-label="System name, renames every calculation in this row"
            title="${nameLocked ? "This row has started. Its calculations keep their names."
                                : "Rename the row. Every calculation name follows."}">`
          : `<span class="qe-sysname" data-fixed="1">${esc(c.name)}</span>`}
        <span class="qe-sfx">${esc(suffix)}</span>
      </div>
      <div class="qe-cm">
        <label>charge <input class="qe-num" data-f="charge" value="${esc(String(charge))}" inputmode="numeric"
          spellcheck="false"${nameLocked && sys ? " readonly" : locked && !sys ? " readonly" : ""}></label>
        <label>mult <input class="qe-num" data-f="mult" value="${esc(String(mult))}" inputmode="numeric"
          spellcheck="false"${nameLocked && sys ? " readonly" : locked && !sys ? " readonly" : ""}></label>
        <span class="qe-note">${esc(ST_LABEL[nodeState({ members: [c] })] + ", run order " + (_M.idx.get(c.name) + 1))}</span>
      </div>
      ${c.message ? `<p class="qe-note" data-tone="${c.state === "failed" ? "err" : c.state === "blocked" ? "warn" : ""}">${esc(c.message)}</p>` : ""}
      ${blockedNote ? `<p class="qe-note" data-tone="warn">${esc(blockedNote)}</p>` : ""}
      ${afterNote ? `<p class="qe-note">${esc(afterNote)}</p>` : ""}
      ${c.waiting ? `<div class="qe-wait"><span>${esc((pa ? pa.name : c.after) + " finished. The queue skips "
          + c.name + " and runs the next calculation until you approve.")}</span>
        <button class="btn btn-primary" type="button" data-a="approve">Approve</button></div>` : ""}
      <select class="qe-select" data-f="geo" aria-label="Geometry"${locked ? " disabled" : ""}>${
        opts.map(o => `<option value="${esc(o.key)}"${o.key === geo ? " selected" : ""}>${esc(o.label)}</option>`).join("")}</select>
      ${isTemplate ? `
        <textarea class="qe-tpl" data-f="tpl" rows="11" spellcheck="false"${locked ? " readonly" : ""}
          aria-label="${esc((n.setKind || "Template") + " / " + (c.set_name || ".inp text"))}">${esc(text)}</textarea>
        ${mod && !locked ? `
          <div class="qe-saveas">
            <input class="qe-input" data-f="newname" placeholder="Save as (${esc(kind)})"
              aria-label="Name for the new inp set">
            <button class="btn btn-primary" type="button" data-a="saveas" disabled>Save</button>
          </div>` : ""}
        ${isMlip ? `<p class="hint">An MLIP block runs the settings these lines name.</p>` : ""}`
      : `
        <textarea class="qe-tpl" data-f="preview" rows="11" readonly spellcheck="false"
          aria-label="The input this calculation runs">Building the input…</textarea>
        <div class="qe-saveas">
          <span class="qe-note">${c.is_raw ? "Pasted .inp text." : "Built with the Guided builder."}</span>
          <button class="btn btn-sm" type="button" data-a="edit"${locked ? " disabled" : ""}>Edit in the builder</button>
        </div>`}
      <div class="qe-saveas">
        <span class="qe-note">${n.stack ? "This block is " + n.members.length + " calculations; the template is shared." : ""}</span>
        <button class="btn btn-sm btn-danger" type="button" data-a="remove"${n.state === "running" ? " disabled" : ""}>Remove…</button>
      </div>`;

    const q = (/** @type {string} */ s) => /** @type {any} */ (host.querySelector(s));
    const sn = q('[data-f="sysname"]');
    if (sn) {
      sn.addEventListener("focus", () => sn.select());
      sn.addEventListener("input", () => { sn.style.width = Math.max(sn.value.length, 2) + 0.5 + "ch"; });
      sn.addEventListener("change", () => renameSystem(L, sn.value));
    }
    ["charge", "mult"].forEach(k => {
      const el = q(`[data-f="${k}"]`);
      el.addEventListener("focus", () => el.select());
      el.addEventListener("change", () => setChargeMult(L, c, k, el.value));
    });
    q('[data-f="geo"]').addEventListener("change", (/** @type {any} */ ev) => setGeometry(L, c, ev.target.value));
    const tpl = q('[data-f="tpl"]');
    if (tpl) {
      tpl.addEventListener("change", () => setTemplate(n, tpl.value));
      const nm = q('[data-f="newname"]'), sv = q('[data-a="saveas"]');
      if (nm && sv) {
        nm.addEventListener("input", () => { sv.disabled = !nm.value.trim(); });
        sv.addEventListener("click", () => saveAs(n, tpl.value, nm.value.trim()));
      }
    }
    const pv = q('[data-f="preview"]');
    if (pv) {
      const src = full ? full : calcDict(c.name);
      const k = String(src.kind || c.kind || "");
      // an MLIP or CREST job runs no ORCA input: its settings are what it runs
      const r = /^(mlip|crest)/.test(k) ? { ok: true, text: backendSettings(k, src.config || {}) }
        : src.is_raw ? { ok: true, text: src.raw_text } : await NB.call("build_inp_preview", JSON.stringify(src));
      if (pv.isConnected) pv.value = r && r.ok ? String(r.text || "") : "Could not build the input" + (r && r.error ? ": " + r.error : ".");
    }
    const ed = q('[data-a="edit"]');
    if (ed) ed.addEventListener("click", () => openBuilder(c.name));
    const ap = q('[data-a="approve"]');
    if (ap) ap.addEventListener("click", () => {
      const u = updates([c.name], () => ({ approved: true }));
      apply(u.ops, u.inv, "the approval");
    });
    q('[data-a="remove"]').addEventListener("click", () => requestRemove(n));
  }

  /** What an MLIP or CREST calculation runs, in the `key value` form an MLIP
   *  set is written in. @param {string} kind @param {any} cfg */
  function backendSettings(kind, cfg) {
    const v = (/** @type {any} */ x, /** @type {string} */ dflt) => x === "" || x == null ? dflt : String(x);
    const lines = kind.indexOf("mlip") === 0
      ? [`model ${v(cfg.mlip_model, "(the default model)")}`, `device ${v(cfg.mlip_device, "auto")}`,
         `nprocs ${v(cfg.nprocs, "1")}`, cfg.mlip_env_id ? `env ${cfg.mlip_env_id}` : ""]
      : [`method ${v(cfg.crest_method, "gfn2")}`, `solvent ${v(cfg.crest_solvent, "none")}`,
         `ewin ${v(cfg.crest_ewin, "6")}`, `threads ${v(cfg.crest_threads, "4")}`];
    return "# " + (KIND_LABEL[kind] || kind) + ": not an ORCA input, these are the settings it runs\n"
      + lines.filter(Boolean).join("\n");
  }

  /** @param {HTMLElement} host @param {any} e */
  function inspectEdge(host, e) {
    const A = e.from, B = e.to;
    const appr = e.gate === "approval";
    const gated = B.members.filter((/** @type {any} */ c) => A.members.some((/** @type {any} */ a) => a.name === c.after));
    const freqA = FREQ_KINDS.indexOf(A.kind) >= 0;
    const expect = A.kind === "ts_freq" || A.kind === "ts_opt_freq" ? 1 : 0;
    const autoOn = gated.every((/** @type {any} */ c) => c.auto_pass !== false);
    const waiting = gated.filter((/** @type {any} */ c) => c.waiting);
    const approved = appr && gated.length && gated.every((/** @type {any} */ c) => c.approved);
    const locked = !gated.every((/** @type {any} */ c) => EDITABLE.indexOf(c.state) >= 0);
    host.innerHTML = `
      <span class="qe-ititle">${esc(A.short)} → ${esc(B.short)}</span>
      <div class="seg" role="group" aria-label="Next calculation runs">
        <button type="button" data-a="auto" aria-pressed="${!appr}"${locked ? " disabled" : ""}>Auto</button>
        <button type="button" data-a="approval" aria-pressed="${appr}"${locked ? " disabled" : ""}>After approval</button>
      </div>
      ${appr && freqA ? `<label class="qe-check"><input type="checkbox" data-f="autopass"${autoOn ? " checked" : ""}${locked ? " disabled" : ""}>
        <span>Pass automatically when Freq finds ${expect} imaginary ${
          expect === 1 ? "frequency (a transition state)" : "frequencies (a minimum)"}</span></label>` : ""}
      ${waiting.length ? `<div class="qe-wait">
        <span>${esc((waiting[0].message ? waiting[0].message + " " : A.short + " finished. ")
          + "The queue skips " + B.short + " and runs the next calculation until you approve.")}</span>
        <button class="btn btn-primary" type="button" data-a="approve">Approve</button></div>` : ""}
      ${approved && !waiting.length ? `<span class="qe-ok">Approved. ${esc(B.short)} runs when the queue reaches it.</span>` : ""}
      ${locked ? `<p class="hint">${esc(B.short)} has started; its gate is kept as it ran.</p>` : ""}`;
    const names = gated.map((/** @type {any} */ c) => c.name);
    const on = (/** @type {string} */ a, /** @type {() => void} */ fn) => {
      const el = host.querySelector(`[data-a="${a}"]`); if (el) el.addEventListener("click", fn);
    };
    const setGate = (/** @type {string} */ g) => {
      if (g === e.gate) return;
      const u = updates(names, () => ({ gate: g, approved: false }));
      apply(u.ops, u.inv, "the gate change");
    };
    on("auto", () => setGate("auto"));
    on("approval", () => setGate("approval"));
    on("approve", () => {
      const u = updates(waiting.map((/** @type {any} */ c) => c.name), () => ({ approved: true }));
      apply(u.ops, u.inv, "the approval");
    });
    const ap = /** @type {any} */ (host.querySelector('[data-f="autopass"]'));
    if (ap) ap.addEventListener("change", () => {
      const u = updates(names, () => ({ auto_pass: !!ap.checked }));
      apply(u.ops, u.inv, "the auto-pass change");
    });
  }

  /** An empty system: its name, charge, structures. @param {HTMLElement} host @param {any} L */
  function inspectSystem(host, L) {
    const s = L.sys;
    host.innerHTML = `
      <div class="qe-ititle">
        <input class="qe-sysname" data-f="sysname" value="${esc(s.name || "")}" spellcheck="false"
          style="width:${Math.max(String(s.name || "").length, 2) + 0.5}ch"
          aria-label="System name" title="Rename the row. Every calculation name follows.">
      </div>
      <div class="qe-cm">
        <label>charge <input class="qe-num" data-f="charge" value="${esc(String(s.charge))}" inputmode="numeric" spellcheck="false"></label>
        <label>mult <input class="qe-num" data-f="mult" value="${esc(String(s.multiplicity))}" inputmode="numeric" spellcheck="false"></label>
      </div>
      <div class="qe-inputs">${(s.inputs || []).length
        ? s.inputs.map((/** @type {any} */ i) => `<span class="qe-note">${esc(i.label || "structure")}</span>`).join("")
        : `<span class="qe-note">No structure yet.</span>`}</div>
      <div class="qe-saveas">
        <button class="btn btn-sm" type="button" data-a="addinput">Add a structure…</button>
        <button class="btn btn-sm btn-danger" type="button" data-a="rmsys">Remove system</button>
      </div>
      <p class="hint">Drop an inp set on the + slot, or press it to build the first calculation.</p>`;
    const q = (/** @type {string} */ x) => /** @type {any} */ (host.querySelector(x));
    const sn = q('[data-f="sysname"]');
    sn.addEventListener("focus", () => sn.select());
    sn.addEventListener("change", () => renameSystem(L, sn.value));
    ["charge", "mult"].forEach(k => {
      const el = q(`[data-f="${k}"]`);
      el.addEventListener("focus", () => el.select());
      el.addEventListener("change", () => setChargeMult(L, null, k, el.value));
    });
    q('[data-a="addinput"]').addEventListener("click", () => addInput(L));
    q('[data-a="rmsys"]').addEventListener("click", () => removeSystem(L));
  }

  // ------------------------------------------------------------ editing

  /** A system name as ORCA and the file system will take it. @param {string} v */
  const cleanName = (v) => String(v || "").trim().replace(/[\\/:*?"<>|\s&,;=]/g, "-");

  /** @param {any} L @param {string} value */
  function renameSystem(L, value) {
    const nm = cleanName(value);
    const old = String(L.sys.name || "");
    if (nm === old) { _inspSig = ""; renderInspector(); return; }
    if (!nm) { NB.fail("A row needs a name."); _inspSig = ""; renderInspector(); return; }
    apply([{ op: "update_system", id: L.sys.id, patch: { name: nm } }],
          [{ op: "update_system", id: L.sys.id, patch: { name: old } }], "the rename");
  }
  /** @param {any} L @param {any} c a calculation, for a standalone lane @param {string} k @param {string} v */
  function setChargeMult(L, c, k, v) {
    const n = parseInt(v, 10);
    if (!isFinite(n) || (k === "mult" && n < 1)) {
      NB.fail(k === "mult" ? "Multiplicity is at least 1." : "The charge is a whole number.");
      _inspSig = ""; renderInspector(); return;
    }
    const field = k === "charge" ? "charge" : "multiplicity";
    if (L.sys) {
      if (int(L.sys[field]) === n) return;
      apply([{ op: "update_system", id: L.sys.id, patch: { [field]: n } }],
            [{ op: "update_system", id: L.sys.id, patch: { [field]: int(L.sys[field]) } }], "the " + k + " change");
    } else if (c) {
      const u = updates([c.name], () => ({ [field]: n }));
      apply(u.ops, u.inv, "the " + k + " change");
    }
  }
  /** @param {any} L @param {any} c @param {string} key */
  async function setGeometry(L, c, key) {
    let patch;
    if (key.indexOf("in:") === 0) {
      const sf = L.sys ? await systemFull(L.sys.id) : null;
      const inp = sf && sf.inputs ? sf.inputs[int(key.slice(3))] : null;
      if (!inp) { NB.fail("Could not read that structure."); return; }
      patch = { geometry_source: "direct", xyz: inp.xyz, ref_name: "", ref_point: "" };
    } else if (key.indexOf("ref:") === 0) {
      const rest = key.slice(4);
      const pt = /:(irc_f|irc_b)$/.exec(rest);
      patch = { geometry_source: "reference", xyz: "", ref_name: pt ? rest.slice(0, -pt[0].length) : rest,
                ref_point: pt ? pt[1] : "" };
    } else return;
    const u = updates([c.name], () => patch);
    apply(u.ops, u.inv, "the geometry change");
  }
  /** @param {any} n @param {string} text */
  function setTemplate(n, text) {
    // is_raw again for an MLIP block, so the store re-reads its settings
    const u = updates(n.members.map((/** @type {any} */ c) => c.name), () => ({ raw_text: text, is_raw: true }));
    apply(u.ops, u.inv, "the template edit");
  }
  /** @param {any} n @param {string} text @param {string} name */
  async function saveAs(n, text, name) {
    if (!name) return;
    const kind = detectKind(text);
    if (kind !== "MLIP" && text.indexOf(GEOM) < 0) { NB.fail("A set needs the " + GEOM + " placeholder."); return; }
    const r = await NB.call("save_inp_set", JSON.stringify({ name: name, kind: kind, text: text }));
    if (!r || r.ok !== true) { NB.fail(why(r, "Could not save the set")); return; }
    await loadSets();
    NB.toast(`Saved "${name}" as a ${kind} set.`, "ok");
    if (kind === n.setKind) {
      const set = r.set || setByName(name, kind);
      const u = updates(n.members.map((/** @type {any} */ c) => c.name),
                        () => ({ raw_text: text, is_raw: true, set_name: name, set_id: set ? set.id : "" }));
      await apply(u.ops, u.inv, "the set change");
    } else render();
  }

  /** Put a set's template on a block, asking the first time. @param {any} n @param {any} set */
  function requestReplace(n, set) {
    if (!set || !n.editable || set.kind !== n.setKind) return;
    if (n.members[0].set_name === set.name) { select({ t: "node", key: n.key, m: 0 }); return; }
    const go = () => {
      _replaceOk = true;
      const u = updates(n.members.map((/** @type {any} */ c) => c.name), () => ({
        raw_text: set.text, set_name: set.name, set_id: set.id, is_raw: true }));
      _sel = { t: "node", key: n.key, m: 0 };
      apply(u.ops, u.inv, "the set change");
    };
    if (_replaceOk) { go(); return; }
    const from = n.members[0].set_name || "its own template";
    const lost = isModified(n) ? " Its edited template is replaced too." : "";
    NB.modalRaw("Replace the inp set?",
      `<p>${esc(n.short)} changes from ${esc(n.setKind + " / " + from)} to ${esc(set.kind + " / " + set.name)}.${
        esc(lost)} This is asked once. Later replacements apply right away.</p>`,
      [{ label: "Cancel", cls: "btn-ghost" }, { label: "Replace", cls: "btn-primary", act: go, focus: true }]);
  }

  // ------------------------------------------------------------ the set editor
  // With nothing selected the inspector edits the inp sets themselves. A set
  // is a whole ORCA input with {{GEOMETRY}} where the coordinates go (an MLIP
  // set: its `key value` lines). It is written through save_inp_set /
  // delete_inp_set alone. Blocks copied their text when they were made, so a
  // saved set changes none of them by itself; the pending ones made from it
  // are offered the new text once, as an undoable batch.

  /** "a Freq", "an Opt" / "an SP" / "an IRC" / "an MLIP": the kinds as said aloud
   *  @param {string} k */
  const aKind = (k) => (k === "Freq" ? "a " : "an ") + k;
  const cap = (/** @type {string} */ s) => s.charAt(0).toUpperCase() + s.slice(1);

  /** the set the editor falls back to: the last one opened or saved */
  let _lastId = "";

  /** Whether the inspector is the set editor now: nothing selected, the
   *  builder not open in it. */
  const editorShown = () => !_sel && NB.build.target() === null;

  /** @param {InpSet} s */
  const edFrom = (s) => ({ id: s.id, kind: s.kind, name: s.name, text: s.text,
                           base: { name: s.name, text: s.text, kind: s.kind } });

  /** Unsaved edits in the editor (a new set as it started is not). */
  const edDirty = () => !!_ed && (_ed.name !== _ed.base.name || _ed.text !== _ed.base.text);

  /** The editor's set: the one open, kept as it is while it has edits; one
   *  saved elsewhere meanwhile (a block's "Save as") is read again; a deleted
   *  one falls back to the last one opened, else the first. */
  function edEnsure() {
    if (_ed && !_ed.id) return;
    const cur = _ed ? _sets.find(x => x.id === _ed.id) : null;
    if (cur) {
      if (!edDirty() && (cur.name !== _ed.base.name || cur.text !== _ed.base.text || cur.kind !== _ed.base.kind)) _ed = edFrom(cur);
      return;
    }
    if (_ed && edDirty()) { _ed.id = ""; return; }   // deleted under its edits: keep them, as a new set
    const s = _sets.find(x => x.id === _lastId) || _sets[0] || null;
    _ed = s ? edFrom(s) : null;
  }

  /** The blocks made from the editor's set (as last saved). */
  function edUsers() {
    if (!_ed || !_ed.id) return [];
    const b = _ed.base;
    return NB.queue.filter((/** @type {any} */ c) => c.set_name === b.name && SET_KIND_OF[c.kind] === b.kind);
  }

  /** Run `fn` when the editor's edits may go: right away without any, else
   *  after asking (keep / discard), as the builder treats its draft.
   *  @param {() => void} fn */
  function guardSwitch(fn) {
    if (!edDirty()) { fn(); return; }
    const d = /** @type {any} */ (_ed);
    NB.modalRaw("Unsaved changes",
      `<p>The ${esc(d.kind)} set "${esc(d.name.trim() || d.base.name)}" has changes that are not saved.</p>`,
      [{ label: "Keep editing", cls: "btn-ghost", focus: true, act: () => { _sel = null; _expanded = ""; render(); } },
       { label: "Discard changes", cls: "btn-danger", act: fn }]);
  }

  /** A set name not taken in its kind: base, else "base 2", "base 3" ...
   *  @param {string} kind @param {string} base */
  function freeSetName(kind, base) {
    const taken = new Set(_sets.filter(x => x.kind === kind).map(x => lower(x.name)));
    let nm = base, i = 2;
    while (taken.has(lower(nm))) nm = base + " " + (i++);
    return nm;
  }

  /** Redraw now, the editor included even with the cursor in one of its
   *  fields (the inspector otherwise never repaints under the cursor) -- a
   *  save, a refusal or a delete must show at once, Ctrl+S included. The
   *  field keeps the cursor and its selection. */
  function repaintEditor() {
    _inspSig = ""; _libSig = "";
    const host = NB.$("qe-insp");
    const a = /** @type {any} */ (document.activeElement);
    const f = a && host.contains(a) ? a.getAttribute("data-f") : null;
    const range = f && typeof a.selectionStart === "number" ? [a.selectionStart, a.selectionEnd] : null;
    if (f) a.blur();
    render();
    if (f && editorShown()) {
      const el = /** @type {any} */ (host.querySelector(`[data-f="${f}"]`));
      if (el) { el.focus(); if (range && el.setSelectionRange) el.setSelectionRange(range[0], range[1]); }
    }
  }

  /** Show the editor (nothing selected) and put the cursor in a field.
   *  @param {string} [field] */
  function showEditor(field) {
    _sel = null; _expanded = ""; _edErr = "";
    repaintEditor();
    if (field) setTimeout(() => {
      const el = /** @type {any} */ (NB.$("qe-insp").querySelector(`[data-f="${field}"]`));
      if (el) { el.focus(); if (el.select && field === "setname") el.select(); }
    }, 0);
  }

  /** @param {InpSet} set */
  function openSet(set) {
    if (NB.build.target() !== null) { NB.toast("The builder is open in the inspector. Close it to edit the set."); return; }
    if (_ed && _ed.id === set.id) { _lastId = set.id; showEditor(); return; }
    guardSwitch(() => { _ed = edFrom(set); _lastId = set.id; showEditor(); });
  }

  /** "+ New" in a kind's group: a new set from that kind's seeded input.
   *  @param {string} kind */
  function newSet(kind) {
    if (NB.build.target() !== null) { NB.toast("The builder is open in the inspector. Close it to start a set."); return; }
    guardSwitch(() => {
      const text = _defaults[kind] || (kind === "MLIP" ? "model \ndevice auto\nnprocs 1\n"
        : "! \n\n* xyz {{CHARGE}} {{MULT}}\n" + GEOM + "\n*\n");
      const name = freeSetName(kind, "New " + kind);
      _ed = { id: "", kind: kind, name: name, text: text, base: { name: name, text: text, kind: kind } };
      showEditor("setname");
    });
  }

  /** The coordinates an imported .inp carries, and the text with the
   *  placeholders in their place, or null when it has none ORCAdesk reads
   *  (`* xyz c m ... *`, `* xyzfile c m file`). @param {string} text */
  function placeholderize(text) {
    const block = "* xyz {{CHARGE}} {{MULT}}\n" + GEOM + "\n*";
    const m = /^[ \t]*\*[ \t]*xyz[ \t]+(-?\d+)[ \t]+(\d+)[^\n]*\n([\s\S]*?)^[ \t]*\*[ \t]*$/im.exec(text);
    if (m) {
      const atoms = m[3].split("\n").filter(l => l.trim()).length;
      return { text: text.slice(0, m.index) + block + text.slice(m.index + m[0].length),
               what: `${atoms} atom${atoms === 1 ? "" : "s"}, charge ${m[1]}, multiplicity ${m[2]}` };
    }
    const f = /^[ \t]*\*[ \t]*xyzfile[ \t]+(-?\d+)[ \t]+(\d+)[ \t]+(\S+)[^\n]*$/im.exec(text);
    if (f) {
      return { text: text.slice(0, f.index) + block + text.slice(f.index + f[0].length),
               what: `read from ${f[3]}, charge ${f[1]}, multiplicity ${f[2]}` };
    }
    return null;
  }

  /** "Import .inp…": a picked ORCA input as a new set. Coordinates it carries
   *  are replaced by the placeholders only when the user says so. */
  function edImport() {
    if (NB.build.target() !== null) { NB.toast("The builder is open in the inspector. Close it to import a set."); return; }
    guardSwitch(async () => {
      const r = await NB.call("load_inp_file");
      if (!r || r.cancelled) return;
      if (!r.ok) { NB.fail("Could not read that .inp."); return; }
      const text = String(r.text || "").replace(/\r\n?/g, "\n");
      const stem = String(r.name || "imported").replace(/\.inp$/i, "");
      const open = (/** @type {string} */ t) => {
        const kind = detectKind(t);
        const name = freeSetName(kind, stem);
        // `base` empty: an import is unsaved until it is saved
        _ed = { id: "", kind: kind, name: name, text: t, base: { name: name, text: "", kind: kind } };
        showEditor();
      };
      const ph = text.indexOf(GEOM) >= 0 ? null : placeholderize(text);
      if (!ph) { open(text); return; }
      NB.modalRaw("Use the placeholders?",
        `<p>${esc(stem)}.inp carries its own structure (${esc(ph.what)}). A set runs on each
          block's structure, charge and multiplicity. Replace that block with</p>
        <div class="modal-code">* xyz {{CHARGE}} {{MULT}}\n${GEOM}\n*</div>
        <p>Kept as it is, the set cannot be saved until it has ${GEOM}.</p>`,
        [{ label: "Keep as it is", cls: "btn-ghost", act: () => open(text) },
         { label: "Replace", cls: "btn-primary", focus: true, act: () => open(ph.text) }]);
    });
  }

  /** @param {string} msg */
  function edError(msg) { _edErr = msg; repaintEditor(); }

  /** Save the editor's set: over itself, or as a new one. Refusals are said
   *  in the editor, with the reason. @param {boolean} asNew */
  async function edSave(asNew) {
    const d = _ed;
    if (!d) return;
    const name = d.name.trim(), text = d.text, kind = detectKind(text);
    const clash = name ? _sets.find(x => x.kind === kind && lower(x.name) === lower(name)
                                         && (asNew || !d.id || x.id !== d.id)) : null;
    const err = !name ? "A set needs a name."
      : kind !== "MLIP" && text.indexOf(GEOM) < 0 ? `${cap(aKind(kind))} set needs ${GEOM} where the coordinates go.`
      : clash ? `There is already ${aKind(kind)} set named "${clash.name}". Give this one another name.`
      : "";
    if (err) { edError(err); return; }
    const id = asNew ? "" : d.id;
    const r = await NB.call("save_inp_set", JSON.stringify({ id: id, name: name, kind: kind, text: text }));
    if (!r || r.ok !== true || !r.set) { edError(why(r, "The set was not saved")); return; }
    const old = id ? { name: d.base.name, kind: d.base.kind } : null;
    await loadSets();
    _ed = edFrom(r.set);
    _lastId = r.set.id;
    _edErr = "";
    repaintEditor();
    NB.toast(`Saved the ${r.set.kind} set "${r.set.name}".`, "ok");
    if (old) offerUpdate(old, r.set);
  }

  /** After a set is saved over itself: the blocks made from it that have not
   *  run, whose text is now another one, are offered the new text -- once,
   *  as one undoable batch. Blocks that have run never change.
   *  @param {{name: string, kind: string}} old @param {InpSet} set */
  function offerUpdate(old, set) {
    if (old.kind !== set.kind) return;
    const names = NB.queue.filter((/** @type {any} */ c) => c.set_name === old.name
        && SET_KIND_OF[c.kind] === old.kind && EDITABLE.indexOf(c.state) >= 0
        && (templateOf(c) !== set.text || c.set_name !== set.name))
      .map((/** @type {any} */ c) => c.name);
    if (!names.length) return;
    const k = names.length;
    NB.modalRaw(`Also update ${k} pending block${k === 1 ? "" : "s"}?`,
      `<p>${k === 1 ? "One block that has not run was" : k + " blocks that have not run were"} made from
        "${esc(old.name)}" and still ${k === 1 ? "has" : "have"} the text it had then:</p>
       <div class="names">${names.map(esc).join("<br>")}</div>
       <p>Blocks keep their text unless you update them. Blocks that have run never change. Ctrl+Z undoes the update.</p>`,
      [{ label: "Keep their text", cls: "btn-ghost" },
       { label: `Update ${k}`, cls: "btn-primary", focus: true, act: () => {
          const u = updates(names, () => ({ raw_text: set.text, is_raw: true, set_name: set.name, set_id: set.id }));
          apply(u.ops, u.inv, "the set update");
        } }]);
  }

  /** "Duplicate": a copy of the set as saved, opened. */
  function edDuplicate() {
    const s = _ed && _ed.id ? _sets.find(x => x.id === _ed.id) : null;
    if (!s) return;
    guardSwitch(async () => {
      const r = await NB.call("save_inp_set", JSON.stringify({
        name: freeSetName(s.kind, s.name + " copy"), kind: s.kind, text: s.text }));
      if (!r || r.ok !== true || !r.set) { NB.fail(why(r, "Could not duplicate the set")); return; }
      await loadSets();
      _ed = edFrom(r.set); _lastId = r.set.id;
      showEditor("setname");
    });
  }

  /** Delete a set, asked first. @param {InpSet} set */
  function deleteSet(set) {
    const users = NB.queue.filter((/** @type {any} */ c) => c.set_name === set.name && SET_KIND_OF[c.kind] === set.kind).length;
    NB.confirm("Delete the inp set",
      `The ${set.kind} set "${set.name}" leaves the library.${users ? ` The ${users} block${users === 1 ? "" : "s"} made from it keep${users === 1 ? "s" : ""} ${users === 1 ? "its" : "their"} text.` : ""}`,
      "Delete", async () => {
        const r = await NB.call("delete_inp_set", set.id);
        if (!r || r.ok !== true) { NB.fail(why(r, "Could not delete the set")); return; }
        if (_ed && _ed.id === set.id) {
          // the editor falls back to a set of the same kind, else the first
          _ed = null;
          const near = _sets.find(x => x.kind === set.kind && x.id !== set.id);
          _lastId = near ? near.id : "";
        }
        await loadSets();
        repaintEditor();
      });
  }

  /** The set editor, drawn in the inspector. @param {HTMLElement} host */
  function inspectSets(host) {
    const d = _ed;
    if (!d) {
      host.innerHTML = `
        <div class="qe-ititle"><span class="qe-sysname" data-fixed="1">Inp sets</span></div>
        <p class="hint">${_missing.length ? "The queue editor needs the updated backend." : "There are no inp sets."}
          Start one with + New beside a kind on the left, or import an ORCA input.</p>
        <div class="qe-saveas"><button class="btn btn-sm" type="button" data-a="import">Import .inp…</button></div>`;
      const im = host.querySelector('[data-a="import"]');
      if (im) im.addEventListener("click", edImport);
      return;
    }
    const users = edUsers();
    const pending = users.filter((/** @type {any} */ c) => EDITABLE.indexOf(c.state) >= 0).length;
    host.innerHTML = `
      <div class="qe-ititle">
        <input class="qe-sysname" data-f="setname" value="${esc(d.name)}" spellcheck="false"
          style="width:${Math.max(d.name.length, 4) + 0.5}ch" aria-label="Set name"
          title="The set's name, as the library and its blocks show it">
      </div>
      <div class="qe-cm"><span data-f="kind"></span><span data-f="dirty"></span></div>
      <p class="qe-note" data-tone="warn" data-f="moves" hidden></p>
      <textarea class="qe-tpl" data-f="settext" rows="16" spellcheck="false"
        aria-label="${esc("The " + d.kind + " set's input")}">${esc(d.text)}</textarea>
      <p class="hint qe-ph" data-f="ph"></p>
      <p class="qe-note" data-tone="err" data-f="err" role="alert"${_edErr ? "" : " hidden"}>${esc(_edErr)}</p>
      <div class="qe-saveas">
        <button class="btn btn-primary btn-sm" type="button" data-a="save">Save</button>
        <button class="btn btn-sm" type="button" data-a="saveas">Save as new</button>
        <span class="qe-barsp"></span>
        ${d.id ? `<button class="btn btn-sm btn-danger" type="button" data-a="delete">Delete…</button>`
               : `<button class="btn btn-sm" type="button" data-a="discard">Discard</button>`}
      </div>
      <div class="qe-saveas">
        ${d.id ? `<button class="btn btn-sm btn-ghost" type="button" data-a="dup">Duplicate</button>` : ""}
        <button class="btn btn-sm btn-ghost" type="button" data-a="import">Import .inp…</button>
      </div>
      <p class="hint">${!d.id ? "Not saved yet."
        : users.length ? `${users.length} block${users.length === 1 ? " in the queue was" : "s in the queue were"} made from it${
            pending ? `, ${pending} not run yet` : ""}. A block keeps the text it was made with; saving offers the new one to those that have not run.`
        : "No block in the queue was made from it. Blocks keep the text they were made with."}</p>`;
    const q = (/** @type {string} */ x) => /** @type {any} */ (host.querySelector(x));
    const nm = q('[data-f="setname"]'), ta = q('[data-f="settext"]');
    const sync = () => {
      const k = detectKind(d.text);
      q('[data-f="kind"]').textContent = k + " set";
      q('[data-f="dirty"]').textContent = !d.id ? "· new, not saved" : edDirty() ? "· unsaved changes" : "";
      const mv = q('[data-f="moves"]');
      mv.hidden = k === d.kind;
      mv.textContent = k === d.kind ? "" : `This text reads as ${k === "MLIP" ? "MLIP settings" : aKind(k) + " input"}: saved, the set moves to the ${k} group.`;
      q('[data-f="ph"]').textContent = k === "MLIP"
        ? "One setting per line: model, device (auto, cpu, cuda), nprocs, env."
        : GEOM + " the block's coordinates · {{CHARGE}} {{MULT}} its charge and multiplicity · {{HESS}} the Hessian of the step it runs after (IRC).";
    };
    sync();
    const typed = () => {
      d.name = nm.value; d.text = ta.value;
      nm.style.width = Math.max(nm.value.length, 4) + 0.5 + "ch";
      if (_edErr) { _edErr = ""; q('[data-f="err"]').hidden = true; }
      sync();
    };
    nm.addEventListener("input", typed);
    ta.addEventListener("input", typed);
    [nm, ta].forEach(el => el.addEventListener("keydown", (/** @type {KeyboardEvent} */ ev) => {
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s") { ev.preventDefault(); typed(); edSave(false); }
    }));
    q('[data-a="save"]').addEventListener("click", () => edSave(false));
    q('[data-a="saveas"]').addEventListener("click", () => edSave(true));
    const del = q('[data-a="delete"]');
    if (del) del.addEventListener("click", () => { const s = _sets.find(x => x.id === d.id); if (s) deleteSet(s); });
    const dis = q('[data-a="discard"]');
    if (dis) dis.addEventListener("click", () => { _ed = null; _edErr = ""; edEnsure(); showEditor(); });
    const dup = q('[data-a="dup"]');
    if (dup) dup.addEventListener("click", edDuplicate);
    q('[data-a="import"]').addEventListener("click", edImport);
  }

  /** A free calculation name: `base`, else base2, base3 ... (the store's
   *  names are unique without regard to case). @param {string} base @param {Set<string>} [also] */
  function freeName(base, also) {
    const taken = new Set(NB.queue.map((/** @type {any} */ c) => lower(c.name)));
    if (also) also.forEach(x => taken.add(lower(x)));
    let nm = base, i = 2;
    while (taken.has(lower(nm))) nm = base + (i++);
    return nm;
  }

  /** Where a new calculation goes in the queue list: after the last one of
   *  its lane that comes before it on the grid (lanes top to bottom, then
   *  column, then row), so the run order stays the drawing's.
   *  @param {string[]} names the current order @param {any} L @param {number} col @param {number} row
   *  @param {string[]} fresh the new names, in their own order */
  function orderWith(names, L, col, row, fresh) {
    const inLane = L ? L.calcs.map((/** @type {any} */ c) => c) : [];
    let at = -1;
    inLane.forEach((/** @type {any} */ c) => {
      const n = _M.nodeOf.get(c.name);
      if (n && (n.col < col || (n.col === col && n.row <= row))) at = Math.max(at, names.indexOf(c.name));
    });
    if (at < 0 && inLane.length) at = names.indexOf(inLane[0].name) - 1;
    if (at < 0 && !inLane.length) at = names.length - 1;
    const out = names.slice();
    out.splice(at + 1, 0, ...fresh);
    return out;
  }

  /** The ops that turn a standalone calculation into a one-node system, so a
   *  next step can hang off it. place_calc moves the calculation in the
   *  layout only and is allowed in every state, so a finished one is adopted
   *  without being edited (P24: what it ran and produced stays as it is).
   *  The system is named after it -- the prefix its follow-ups take -- made
   *  ORCA-safe and unique among the systems. @param {any} P */
  async function adopt(P) {
    const c = P.members[0];
    const f = _full.get(c.name);
    const sysId = newId("s"), nodeId = newId("n");
    const xyz = f && f.calc && f.calc.geometry_source !== "reference" ? String(f.calc.xyz || "") : "";
    const sysNames = new Set(NB.systems.map((/** @type {any} */ s) => lower(s.name)));
    const base = cleanName(c.name) || "system";
    let nm = base;
    for (let i = 2; sysNames.has(lower(nm)); i++) nm = base + "-" + i;
    const ops = [
      { op: "add_system", system: { id: sysId, name: nm, charge: c.charge, multiplicity: c.multiplicity,
          inputs: xyz ? [{ label: c.name + " input", xyz: xyz }] : [] } },
      { op: "place_calc", name: c.name, place: { system: sysId, node: nodeId, col: 0, row: 0 } },
    ];
    const inv = [
      { op: "place_calc", name: c.name, place: { system: "", node: "", col: 0, row: 0 } },
      { op: "remove_system", id: sysId },
    ];
    return { ops: ops, inv: inv, sys: { id: sysId, name: nm, charge: c.charge, multiplicity: c.multiplicity },
             parent: { id: nodeId, members: [c], kind: c.kind, stack: false } };
  }

  /** Add the next step from a set, on a slot. @param {any} g the slot @param {any} set */
  async function addFromSet(g, set) {
    if (!g || !set) return;
    let P = g.parent;
    if (P && P.stack && set.kind === "IRC") { NB.toast("An IRC starts from one transition state, not a stack."); return; }
    let pre = { ops: /** @type {any[]} */ ([]), inv: /** @type {any[]} */ ([]) };
    let sys = g.lane.sys;
    if (!sys) {
      const a = await adopt(P);
      pre = a; sys = a.sys; P = a.parent;
    }
    const kind = kindFor(set.kind, P);
    const nodeId = newId("n");
    // the members, as tails on one base name: an IRC's two ends, one per
    // member of a stack, or a single calculation
    /** @type {{tail: string, after: string, ref: string, pt: string}[]} */
    let tails;
    if (P && !P.stack && P.kind === "irc" && kind !== "irc") {
      const p = P.members[0].name;
      tails = [{ tail: "-F", after: p, ref: p, pt: "irc_f" }, { tail: "-B", after: p, ref: p, pt: "irc_b" }];
    } else if (P && P.stack) {
      tails = P.members.map((/** @type {any} */ c) =>
        ({ tail: "-" + c.name.slice(c.name.lastIndexOf("-") + 1), after: c.name, ref: c.name, pt: "" }));
    } else if (P) {
      tails = [{ tail: "", after: P.members[0].name, ref: P.members[0].name, pt: "" }];
    } else {
      tails = [{ tail: "", after: "", ref: "", pt: "" }];
    }
    // a base whose every member name is free (Ltyr-sp, else Ltyr-sp2 ...)
    const root = String(sys.name || "") + "-" + SUFFIX[set.kind];
    const taken = new Set(NB.queue.map((/** @type {any} */ c) => lower(c.name)));
    let base = root;
    for (let i = 2; tails.some(t => taken.has(lower(base + t.tail))); i++) base = root + i;
    const members = tails.map(t => ({ name: base + t.tail, after: t.after, ref: t.ref, pt: t.pt }));
    let xyz = "";
    if (!P && sys.id) {
      const sf = await systemFull(sys.id);
      xyz = sf && sf.inputs && sf.inputs[0] ? String(sf.inputs[0].xyz || "") : "";
    }
    const adds = members.map(m => ({ op: "add_calc", calc: {
      name: m.name, kind: kind, charge: int(sys.charge), multiplicity: int(sys.multiplicity) || 1,
      geometry_source: m.ref ? "reference" : "direct", xyz: m.ref ? "" : xyz,
      ref_name: m.ref, ref_point: m.pt,
      // an MLIP set is sent as raw text too: the store reads its `key value`
      // lines into the MLIP config (store.calc_from_dict) and keeps the text
      is_raw: true, raw_text: set.text, set_name: set.name, set_id: set.id,
      system: sys.id, node: nodeId, col: g.c, row: g.r, after: m.after,
      gate: "auto", auto_pass: true, approved: false,
      config: {}, state: "pending", message: "",
    } }));
    const fresh = members.map(m => m.name);
    const order = { op: "order", names: orderWith(NB.queue.map((/** @type {any} */ c) => c.name),
                                                  g.lane, g.c, g.r, fresh) };
    const ops = pre.ops.concat(adds, [order]);
    const inv = fresh.map(n => ({ op: "remove_calc", name: n })).concat(pre.inv);
    await applySel(ops, inv, "the new " + set.kind, { t: "node", key: "s:" + sys.id + "/" + nodeId, m: 0 });
  }

  /** The builder, hosted in the inspector. @param {string} target "" for a new one
   *  @param {any} [seed] @param {any} [place] */
  function openBuilder(target, seed, place) {
    _buildKey++;
    if (NB.build.target() === target && !seed) { render(); return; }
    NB.build.open(target, seed, place);
  }

  /** "Manual build" on a slot: the Guided builder, seeded for that place,
   *  and the calculation it adds lands there. @param {any} g */
  async function manualBuild(g) {
    let P = g.parent;
    let sys = g.lane.sys;
    if (!sys) {
      const a = await adopt(P);
      if (!(await apply(a.ops, a.inv, "making a system of " + P.members[0].name))) return;
      sys = a.sys; P = a.parent;
      // the block is the new row's first node now: keep it selected, so the
      // slot it offers (and its "Manual build" mark) stays drawn
      _sel = { t: "node", key: "s:" + sys.id + "/" + P.id, m: 0 };
      g = Object.assign({}, g, { key: "s:" + sys.id + "@" + g.c + "," + g.r });
    }
    const parent = P ? P.members[Math.min((_sel && _sel.m) || 0, P.members.length - 1)] : null;
    let xyz = "";
    if (!P) {
      const sf = await systemFull(sys.id);
      xyz = sf && sf.inputs && sf.inputs[0] ? String(sf.inputs[0].xyz || "") : "";
    }
    const nodeId = newId("n");
    const col = g.c, row = g.r;
    const laneKey = "s:" + sys.id;
    openBuilder("", {
      name: freeName(String(sys.name || "") + "-calc"), kind: "opt", is_raw: false, raw_text: "",
      xyz: xyz, geometry_source: parent ? "reference" : "direct", ref_name: parent ? parent.name : "",
      charge: int(sys.charge), multiplicity: int(sys.multiplicity) || 1, config: {},
    }, {
      slot: g.key,
      label: "New calculation " + (parent ? "after " + parent.name : "in " + sys.name),
      fields: { system: sys.id, node: nodeId, col: col, row: row, after: parent ? parent.name : "",
                gate: "auto", auto_pass: true, approved: false, ref_point: "" },
      done: async (/** @type {string} */ name) => {
        _undo.push({ label: "the new calculation", ops: [{ op: "remove_calc", name: name }] });
        await NB.poll();
        _M = model();
        const L = laneByKey(laneKey);
        const names = NB.queue.map((/** @type {any} */ c) => c.name).filter((/** @type {string} */ x) => x !== name);
        const want = orderWith(names, L ? Object.assign({}, L, { calcs: L.calcs.filter((/** @type {any} */ c) => c.name !== name) }) : null,
                               col, row, [name]);
        if (want.join("\n") !== NB.queue.map((/** @type {any} */ c) => c.name).join("\n")) {
          await NB.call("apply_queue_ops", JSON.stringify([{ op: "order", names: want }]));
        }
        _sel = { t: "node", key: laneKey + "/" + nodeId, m: 0 };
        _pop = _sel.key;
        await NB.poll();
      },
    });
  }

  /** Delete: a block and everything that hangs off it, asked first; a
   *  running one is refused (stop it first). @param {any} n */
  function requestRemove(n) {
    const L = n.lane;
    const gone = n.members.map((/** @type {any} */ c) => c.name);
    const set = new Set(gone);
    for (let grew = true; grew; ) {
      grew = false;
      L.calcs.forEach((/** @type {any} */ c) => {
        if (set.has(c.name)) return;
        if (set.has(c.after) || (c.geometry_source === "reference" && set.has(c.ref_name))) {
          set.add(c.name); gone.push(c.name); grew = true;
        }
      });
    }
    const calcs = gone.map(x => NB.queue.find((/** @type {any} */ c) => c.name === x));
    const running = calcs.find(c => c && c.state === "running");
    if (running) {
      NB.modalRaw("Cancel it first", `<p>${esc(running.name)} is running. Cancel it, then remove it.</p>`,
                  [{ label: "OK", cls: "btn-primary", focus: true }]);
      return;
    }
    const k = gone.length - n.members.length;
    const after = k > 0 ? ` The ${k} calculation${k === 1 ? "" : "s"} after it depend on it, so they go too.` : "";
    // the store takes a calculation back as PENDING, never as the finished
    // run it was -- so a removal that includes one is not offered as undoable
    // (an "undo" that queues a finished job to run again is not an undo)
    const finished = calcs.some(c => c && (c.state === "done" || c.state === "failed"));
    // a row left with no block goes with its last one, as the design's lanes do
    const emptied = !!L.sys && L.calcs.every((/** @type {any} */ c) => set.has(c.name));
    NB.modalRaw(`Remove ${gone.length} calculation${gone.length === 1 ? "" : "s"}?`,
      `<p>${esc(n.short)} is removed from the queue.${esc(after)}${emptied ? " Its row goes too." : ""} Files already written stay on disk.${
        finished ? " Finished calculations cannot be brought back with Ctrl+Z." : " Ctrl+Z brings it back."}</p>`,
      [{ label: "Cancel", cls: "btn-ghost" },
       { label: "Remove", cls: "btn-danger", focus: true, act: async () => {
          const before = NB.queue.map((/** @type {any} */ c) => c.name);
          const sysFull = emptied ? await systemFull(L.sys.id) : null;
          const ops = /** @type {any[]} */ (gone.slice().reverse().map(x => ({ op: "remove_calc", name: x })));
          if (emptied) ops.push({ op: "remove_system", id: L.sys.id });
          const inv = finished || (emptied && !sysFull) ? null
            : (/** @type {any[]} */ (sysFull ? [{ op: "add_system", system: sysFull }] : []))
              .concat(gone.map(x => ({ op: "add_calc", calc: calcDict(x) })), [{ op: "order", names: before }]);
          _sel = null;
          apply(ops, inv, "the removal");
        } }]);
  }

  /** @param {any} L */
  async function removeSystem(L) {
    if (L.calcs.length) return;
    const full = await systemFull(L.sys.id);
    _sel = null;
    apply([{ op: "remove_system", id: L.sys.id }],
          full ? [{ op: "add_system", system: full }] : null, "removing " + L.sys.name);
  }

  /** @param {any} L */
  async function addInput(L) {
    const r = await NB.call("load_xyz_file");
    if (!r || r.cancelled) return;
    if (!r.ok) { NB.fail("Could not read that .xyz."); return; }
    const full = await systemFull(L.sys.id);
    const old = full ? (full.inputs || []) : [];
    apply([{ op: "update_system", id: L.sys.id, patch: { inputs: old.concat([{ label: (r.name || "structure") + ".xyz", xyz: r.text }]) } }],
          [{ op: "update_system", id: L.sys.id, patch: { inputs: old } }], "the new structure");
  }

  /** "+ system": a structure file becomes an empty lane. */
  async function addSystem() {
    const r = await NB.call("load_xyz_file");
    if (!r || r.cancelled) return;
    if (!r.ok) { NB.fail("Could not read that .xyz."); return; }
    const taken = new Set(NB.systems.map((/** @type {any} */ s) => lower(s.name)));
    const base = cleanName(r.name || "system") || "system";
    let nm = base, i = 2;
    while (taken.has(lower(nm)) || NB.queue.some((/** @type {any} */ c) => lower(c.name) === lower(nm))) nm = base + "-" + (i++);
    const id = newId("s");
    await applySel([{ op: "add_system", system: { id: id, name: nm, charge: 0, multiplicity: 1,
                      inputs: [{ label: (r.name || nm) + ".xyz", xyz: r.text }] } }],
                   [{ op: "remove_system", id: id }], "the new system", { t: "sys", key: "s:" + id });
  }

  // ---- lanes: move, copy

  /** @param {any} L */
  const laneNames = (L) => L.calcs.map((/** @type {any} */ c) => c.name);

  /** Move a lane to before lane slot `to` (0 .. lanes). @param {string} key @param {number} to */
  function moveLane(key, to) {
    const lanes = _M.lanes.filter((/** @type {any} */ L) => L.calcs.length);
    const from = lanes.findIndex((/** @type {any} */ L) => L.key === key);
    if (from < 0 || to === from || to === from + 1) return;
    const order = lanes.slice();
    const [mv] = order.splice(from, 1);
    order.splice(to > from ? to - 1 : to, 0, mv);
    const before = NB.queue.map((/** @type {any} */ c) => c.name);
    const names = order.flatMap(laneNames);
    apply([{ op: "order", names: names }], [{ op: "order", names: before }], "the move");
  }

  /** Copy a lane to lane slot `to`: a new system with the same steps, every
   *  name carrying the new system's, nothing started. @param {string} key @param {number} to */
  async function copyLane(key, to) {
    const L = laneByKey(key);
    if (!L || !L.calcs.length) return;
    const lanes = _M.lanes.filter((/** @type {any} */ x) => x.calcs.length);
    const ops = [], inv = [];
    const oldBase = L.name;
    const taken = new Set(NB.queue.map((/** @type {any} */ c) => lower(c.name))
      .concat(NB.systems.map((/** @type {any} */ s) => lower(s.name))));
    let nm = oldBase + "-copy", k = 2;
    const rename = (/** @type {string} */ base, /** @type {string} */ x) =>
      lower(x).indexOf(lower(oldBase)) === 0 ? base + x.slice(oldBase.length) : base + "-" + x;
    while (taken.has(lower(nm)) || laneNames(L).some((/** @type {string} */ x) => taken.has(lower(rename(nm, x))))) {
      nm = oldBase + "-copy" + (k++);
    }
    const map = new Map(laneNames(L).map((/** @type {string} */ x) => [x, rename(nm, x)]));
    let sysId = "";
    if (L.sys) {
      const full = await systemFull(L.sys.id);
      sysId = newId("s");
      ops.push({ op: "add_system", system: { id: sysId, name: nm, charge: L.sys.charge,
        multiplicity: L.sys.multiplicity, inputs: full ? full.inputs || [] : [] } });
    }
    const nodeMap = new Map();
    // parents first, so every `after` names a calculation already added
    const done = new Set();
    const pending = L.calcs.slice();
    while (pending.length) {
      const i = pending.findIndex((/** @type {any} */ c) => !c.after || !map.has(c.after) || done.has(c.after));
      const c = pending.splice(i < 0 ? 0 : i, 1)[0];
      done.add(c.name);
      if (c.node && !nodeMap.has(c.node)) nodeMap.set(c.node, newId("n"));
      ops.push({ op: "add_calc", calc: calcDict(c.name, {
        name: map.get(c.name), state: "pending", message: "", approved: false,
        output_path: "", pid: null, create_time: null,
        system: sysId, node: c.node ? nodeMap.get(c.node) : "",
        after: c.after && map.has(c.after) ? map.get(c.after) : (L.sys ? c.after : ""),
        ref_name: c.ref_name && map.has(c.ref_name) ? map.get(c.ref_name) : c.ref_name,
      }) });
    }
    const fresh = laneNames(L).map((/** @type {string} */ x) => map.get(x));
    const order = lanes.slice(0, to).flatMap(laneNames).concat(fresh, lanes.slice(to).flatMap(laneNames));
    ops.push({ op: "order", names: order });
    fresh.forEach((/** @type {string} */ x) => inv.push({ op: "remove_calc", name: x }));
    if (sysId) inv.push({ op: "remove_system", id: sysId });
    const first = L.nodes[0];
    await applySel(ops, inv, "the copy", sysId && first && first.id
      ? { t: "node", key: "s:" + sysId + "/" + nodeMap.get(first.id), m: 0 }
      : { t: "node", key: "c:" + fresh[0], m: 0 });
  }

  // ------------------------------------------------------------ drops of files

  /** Dropped structures: one system each, with the drop recipe's single
   *  point. Refused files are listed with the reason, never silently fixed.
   *  @param {string[]} paths @returns {Promise<boolean>} whether any was queued */
  async function dropXyz(paths) {
    const list = (paths || []).filter(p => /\.xyz$/i.test(String(p)));
    if (!list.length) return false;
    const rec = await recipe();
    if (!rec.set_id) { NB.fail("There is no SP inp set to run a dropped structure with."); return false; }
    const r = await NB.call("add_xyz_systems", JSON.stringify({ paths: list, set_id: rec.set_id, suffix: rec.suffix }));
    if (!r || (r.ok !== true && !r.added)) { NB.fail(why(r, "Could not queue the structures")); return false; }
    const added = Array.isArray(r.added) ? r.added : [];
    const refused = Array.isArray(r.refused) ? r.refused : [];
    if (added.length) {
      NB.toast(`${added.length} structure${added.length === 1 ? "" : "s"} queued: ${added.join(", ")}.`, "ok");
      const ids = Array.isArray(r.systems) ? r.systems : [];
      _undo.push({ label: "the dropped structures",
                   ops: added.map((/** @type {string} */ n) => ({ op: "remove_calc", name: n }))
                     .concat(ids.map((/** @type {string} */ id) => ({ op: "remove_system", id: id }))) });
    }
    if (refused.length) {
      NB.modalRaw(`${refused.length} file${refused.length === 1 ? " was" : "s were"} not added`,
        `<div class="names">${refused.map((/** @type {any} */ x) =>
          `${esc(String(x.file || "").split(/[\\/]/).pop())} — ${esc(x.reason || "refused")}`).join("<br>")}</div>`);
    }
    await NB.poll();
    return added.length > 0;
  }

  /** A dropped .inp opens in the builder as pasted input, to be looked at
   *  before it is queued. @param {string} path */
  async function dropInp(path) {
    const r = await NB.call("load_inp_path", path);
    if (!r || r.cancelled) return;
    if (!r.ok) { NB.fail("Could not read that .inp."); return; }
    const text = String(r.text || "");
    const cm = /^\s*\*\s*xyz(?:file)?\s+(-?\d+)\s+(\d+)/im.exec(text);
    const kind = { MLIP: "general", Opt: "opt", OptTS: "ts_opt", Freq: "freq", IRC: "irc", SP: "sp" }[detectKind(text)];
    openBuilder("", { name: freeName(r.name || "input"), kind: kind, is_raw: true, raw_text: text, xyz: "",
                      geometry_source: "direct", ref_name: "", charge: cm ? int(cm[1]) : 0,
                      multiplicity: cm ? int(cm[2]) || 1 : 1, config: {} });
  }

  /** What the main window handed over (boot.js NB.handOff). */
  async function takeHandOff() {
    if (document.visibilityState === "hidden") return;
    const m = await NB.takeHandOff();
    if (!m) return;
    if (m.type === "build") openBuilder("", m.seed || undefined);
    else if (m.type === "inp" && m.path) dropInp(m.path);
    else if ((m.type === "select" || m.type === "edit") && m.name) {
      _M = model();
      const n = _M.nodeOf.get(m.name);
      if (n) select({ t: "node", key: n.key, m: Math.max(0, n.members.findIndex((/** @type {any} */ c) => c.name === m.name)) });
      const c = NB.queue.find((/** @type {any} */ x) => x.name === m.name);
      if (m.type === "edit" && c && !c.set_name && EDITABLE.indexOf(c.state) >= 0) openBuilder(m.name);
      if (n) {
        const el = NB.$("qe-graph").querySelector(`.qe-node[data-k="${CSS.escape(n.key)}"]`);
        if (el) el.scrollIntoView({ block: "nearest", inline: "nearest" });
      }
    }
  }

  // ------------------------------------------------------------ events

  /** @param {any} s */
  function select(s) {
    _sel = s;
    render();
  }

  function wireCanvas() {
    const g = NB.$("qe-graph");
    g.addEventListener("click", (/** @type {MouseEvent} */ ev) => {
      const b = /** @type {HTMLElement} */ (ev.target).closest("[data-a]");
      if (!b) return;
      const a = b.getAttribute("data-a"), k = b.getAttribute("data-k") || "";
      if (a === "clear") { _expanded = ""; select(null); }
      else if (a === "node") {
        const n = nodeByKey(k);
        if (!n) return;
        if (n.stack) _expanded = _expanded === k && _sel && _sel.key === k ? "" : k;
        else _expanded = "";
        select({ t: "node", key: k, m: _sel && _sel.key === k ? _sel.m || 0 : 0 });
      } else if (a === "member") {
        select({ t: "node", key: k, m: int(b.getAttribute("data-m")) });
      } else if (a === "edge") { _expanded = ""; select({ t: "edge", key: k }); }
      else if (a === "sys") select({ t: "sys", key: b.getAttribute("data-lane") });
      else if (a === "ghost") {
        const slot = (g._ghosts || []).find((/** @type {any} */ x) => x.key === b.getAttribute("data-slot"));
        if (slot) manualBuild(slot);
      } else if (a === "grip") {
        const L = laneByKey(b.getAttribute("data-lane"));
        if (L) copyLane(L.key, _M.lanes.filter((/** @type {any} */ x) => x.calcs.length).indexOf(L) + 1);
      } else if (a === "addsys") addSystem();
    });
    g.addEventListener("keydown", (/** @type {KeyboardEvent} */ ev) => {
      const b = /** @type {HTMLElement} */ (ev.target).closest('[data-a="grip"]');
      if (!b || !ev.altKey || (ev.key !== "ArrowUp" && ev.key !== "ArrowDown")) return;
      ev.preventDefault();
      const lanes = _M.lanes.filter((/** @type {any} */ x) => x.calcs.length);
      const i = lanes.findIndex((/** @type {any} */ L) => L.key === b.getAttribute("data-lane"));
      if (ev.key === "ArrowUp" && i > 0) moveLane(lanes[i].key, i - 1);
      if (ev.key === "ArrowDown" && i >= 0 && i < lanes.length - 1) moveLane(lanes[i].key, i + 2);
    });

    // lane drag: the grip is the source, slots appear between the lanes
    g.addEventListener("dragstart", (/** @type {DragEvent} */ ev) => {
      const b = /** @type {HTMLElement} */ (ev.target).closest('[data-a="grip"]');
      if (!b) return;
      try { ev.dataTransfer.effectAllowed = "copyMove"; ev.dataTransfer.setData("text/plain", b.getAttribute("data-lane")); } catch (e) { /* */ }
      _drag = { t: "lane", key: b.getAttribute("data-lane") };
      setTimeout(showLaneSlots, 0);
    });
    g.addEventListener("dragend", () => { if (_drag && _drag.t === "lane") endDrag(); });

    // drops: a set on a block or the ghost; a lane on a slot
    g.addEventListener("dragover", (/** @type {DragEvent} */ ev) => {
      if (!_drag) return;
      const t = /** @type {HTMLElement} */ (ev.target);
      if (_drag.t === "lane") {
        const s = t.closest(".qe-slot");
        if (!s) return;
        ev.preventDefault();
        const cp = !!(ev.ctrlKey || ev.altKey || ev.metaKey);
        try { ev.dataTransfer.dropEffect = cp ? "copy" : "move"; } catch (e) { /* */ }
        g.querySelectorAll(".qe-slot").forEach((/** @type {HTMLElement} */ x) => {
          x.toggleAttribute("data-on", x === s);
          x.textContent = cp ? "Copy here" : "Move here";
        });
        return;
      }
      const ok = dropTarget(t);
      if (!ok) return;
      ev.preventDefault();
      try { ev.dataTransfer.dropEffect = "copy"; } catch (e) { /* */ }
      if (ok.ghost && !_ghostHot) { _ghostHot = true; ok.ghost.setAttribute("data-hot", "1"); }
    });
    g.addEventListener("dragleave", (/** @type {DragEvent} */ ev) => {
      const gh = /** @type {HTMLElement} */ (ev.target).closest(".qe-ghost");
      if (gh && _ghostHot && !gh.contains(/** @type {Node} */ (ev.relatedTarget))) {
        _ghostHot = false; gh.removeAttribute("data-hot");
      }
    });
    g.addEventListener("drop", (/** @type {DragEvent} */ ev) => {
      if (!_drag) return;
      const t = /** @type {HTMLElement} */ (ev.target);
      if (_drag.t === "lane") {
        const s = t.closest(".qe-slot");
        if (!s) return;
        ev.preventDefault();
        const key = _drag.key, at = int(s.getAttribute("data-i"));
        const cp = !!(ev.ctrlKey || ev.altKey || ev.metaKey);
        endDrag();
        if (cp) copyLane(key, at); else moveLane(key, at);
        return;
      }
      const ok = dropTarget(t);
      if (!ok) return;
      ev.preventDefault();
      const set = _sets.find(s => s.id === _drag.id);
      endDrag();
      if (ok.node) requestReplace(ok.node, set);
      else if (ok.slot) addFromSet(ok.slot, set);
    });
  }

  /** What a dragged set would land on under `t`, or null.
   *  @param {HTMLElement} t @returns {any} */
  function dropTarget(t) {
    const set = _drag && _drag.t === "set" ? _sets.find(s => s.id === _drag.id) : null;
    if (!set) return null;
    const gh = t.closest(".qe-ghost");
    if (gh) {
      if (gh.hasAttribute("data-afterstack") && set.kind === "IRC") return null;
      const slot = (NB.$("qe-graph")._ghosts || []).find((/** @type {any} */ x) => x.key === gh.getAttribute("data-slot"));
      return slot ? { ghost: gh, slot: slot } : null;
    }
    const nd = t.closest(".qe-node");
    if (nd && nd.getAttribute("data-setkind") === set.kind && nd.hasAttribute("data-editable")) {
      const n = nodeByKey(nd.getAttribute("data-k"));
      return n ? { node: n } : null;
    }
    return null;
  }

  /** Mark what a dragged set can land on, without redrawing the canvas (a
   *  redraw mid-drag would drop the drag). */
  function showSetTargets() {
    const set = _drag && _drag.t === "set" ? _sets.find(s => s.id === _drag.id) : null;
    if (!set) return;
    const g = NB.$("qe-graph");
    g.querySelectorAll(".qe-node").forEach((/** @type {HTMLElement} */ nd) => {
      if (nd.getAttribute("data-setkind") === set.kind && nd.hasAttribute("data-editable")) nd.setAttribute("data-drop", "1");
    });
    g.querySelectorAll(".qe-ghost").forEach((/** @type {HTMLElement} */ gh) => {
      if (!(gh.hasAttribute("data-afterstack") && set.kind === "IRC")) gh.setAttribute("data-drop", "1");
    });
    const b = NB.$("qe-sets").querySelector(`[data-id="${CSS.escape(set.id)}"]`);
    if (b) b.setAttribute("data-dragging", "1");
  }
  function showLaneSlots() {
    if (!_drag || _drag.t !== "lane") return;
    const g = NB.$("qe-graph");
    const tops = _M.lanes.filter((/** @type {any} */ L) => L.calcs.length).map((/** @type {any} */ L) => L.top)
      .concat([(g._laneTops || []).slice(-1)[0] || 0]);
    tops.forEach((/** @type {number} */ y, /** @type {number} */ i) => {
      const s = NB.el("div", "qe-slot", "Move here");
      s.setAttribute("data-i", String(i));
      s.style.top = Math.max(y - 14, 2) + "px";
      g.appendChild(s);
    });
  }
  function endDrag() {
    _drag = null;
    _ghostHot = false;
    NB.$("qe-sets").querySelectorAll("[data-dragging]").forEach((/** @type {HTMLElement} */ b) =>
      b.removeAttribute("data-dragging"));
    setTimeout(render, 0);
  }

  function wireLibrary() {
    const host = NB.$("qe-sets");
    host.addEventListener("click", (/** @type {MouseEvent} */ ev) => {
      const t = /** @type {HTMLElement} */ (ev.target);
      const nw = t.closest("[data-new]");
      if (nw) { newSet(nw.getAttribute("data-new") || "SP"); return; }
      const b = t.closest("button.qe-set");
      if (!b) return;
      const set = _sets.find(s => s.id === b.getAttribute("data-id"));
      if (!set) return;
      const n = selNode();
      // on the selected block when the kinds match and it can still change;
      // otherwise the click opens the set in the editor
      if (n && set.kind === n.setKind && n.editable) { requestReplace(n, set); return; }
      openSet(set);
    });
    host.addEventListener("dragstart", (/** @type {DragEvent} */ ev) => {
      const b = /** @type {HTMLElement} */ (ev.target).closest(".qe-set");
      if (!b) return;
      const id = b.getAttribute("data-id");
      try {
        ev.dataTransfer.effectAllowed = "copy";
        ev.dataTransfer.setData("text/plain", b.textContent);
        const im = document.getElementById("qe-di-" + id);
        if (im) ev.dataTransfer.setDragImage(im, 28, 22);
      } catch (e) { /* */ }
      _drag = { t: "set", id: id };
      setTimeout(showSetTargets, 0);
    });
    host.addEventListener("dragend", () => { if (_drag && _drag.t === "set") endDrag(); });
    host.addEventListener("keydown", (/** @type {KeyboardEvent} */ ev) => {
      if (ev.key !== "Delete") return;
      const b = /** @type {HTMLElement} */ (ev.target).closest(".qe-set");
      const set = b ? _sets.find(s => s.id === b.getAttribute("data-id")) : null;
      if (!set) return;
      ev.preventDefault();
      ev.stopPropagation();
      deleteSet(set);
    });
    NB.$("qe-rset").addEventListener("change", saveRecipe);
    NB.$("qe-rsfx").addEventListener("change", saveRecipe);
  }

  function wireKeys() {
    document.addEventListener("keydown", (/** @type {KeyboardEvent} */ ev) => {
      if (!_active || !NB.$("modal").hidden || !NB.$("settings").hidden) return;
      const t = /** @type {HTMLElement} */ (ev.target);
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
      // the builder's own keys, and the library's (Delete there deletes a set)
      if (t && t.closest && (t.closest(".qe-insp .cell") || t.closest(".qe-lib"))) return;
      if ((ev.ctrlKey || ev.metaKey) && !ev.shiftKey && ev.key.toLowerCase() === "z") {
        ev.preventDefault(); undo(); return;
      }
      if (ev.key === "Escape" && (_sel || _expanded)) { _expanded = ""; select(null); return; }
      if (ev.key === "Delete" || ev.key === "Backspace") {
        const n = selNode();
        if (n) { ev.preventDefault(); requestRemove(n); return; }
        const L = _sel && _sel.t === "sys" ? laneByKey(_sel.key) : null;
        if (L && !L.calcs.length) { ev.preventDefault(); removeSystem(L); }
      }
    }, true);   // capture: an Escape that closes a modal must not also drop the selection
  }

  // ------------------------------------------------------------ sync

  /** Bring the full calculations up to the snapshot. A template edit does
   *  not show in the summary (the poll carries no raw_text), so every
   *  calculation that can still be edited is read again; one that has started
   *  only when its summary moved. */
  async function refreshFull() {
    const live = new Set();
    const need = [];
    NB.queue.forEach((/** @type {any} */ c) => {
      live.add(c.name);
      const sig = JSON.stringify(c);
      const f = _full.get(c.name);
      if (!f || f.sig !== sig || EDITABLE.indexOf(c.state) >= 0) need.push({ name: c.name, sig: sig });
    });
    [..._full.keys()].forEach(k => { if (!live.has(k)) _full.delete(k); });
    [..._preview.keys()].forEach(k => { if (!live.has(k)) _preview.delete(k); });
    await Promise.all(need.map(async x => {
      const g = await NB.call("get_calc", x.name);
      if (g && g.ok && g.calc) _full.set(x.name, { sig: x.sig, calc: g.calc });
    }));
    // the guided ends of an Opt/OptTS -> Freq connection: their input, for
    // the level comparison (a template's own text is compared directly)
    const byName = new Map(NB.queue.map((/** @type {any} */ c) => [c.name, c]));
    const ends = new Set();
    NB.queue.forEach((/** @type {any} */ c) => {
      const p = c.after ? byName.get(c.after) : null;
      if (!p || SET_KIND_OF[c.kind] !== "Freq" || (SET_KIND_OF[p.kind] !== "Opt" && SET_KIND_OF[p.kind] !== "OptTS")) return;
      [c, p].forEach(x => ends.add(x));
    });
    await Promise.all([...ends].map(async (/** @type {any} */ c) => {
      const f = _full.get(c.name);
      if (!f || !f.calc || f.calc.is_raw || f.calc.raw_text) return;
      const pv = _preview.get(c.name);
      if (pv && pv.sig === f.sig) return;
      const r = await NB.call("build_inp_preview", JSON.stringify(f.calc));
      if (r && r.ok) _preview.set(c.name, { sig: f.sig, text: String(r.text || "") });
    }));
    _sysFull.clear();
  }

  let _syncing = false, _again = false;
  async function sync() {
    if (_syncing) { _again = true; return; }
    _syncing = true;
    try {
      await refreshFull();
      render();
    } finally {
      _syncing = false;
      if (_again) { _again = false; sync(); }
    }
  }

  async function init() {
    _active = true;
    const host = NB.$("qed");
    host.hidden = false;
    host.innerHTML = `
      <div class="qe-app">
        <aside class="qe-lib" aria-label="Inp sets">
          <span class="qe-libh">inp sets</span>
          <div class="qe-sets" id="qe-sets"></div>
          <div class="qe-recipe" id="qe-recipe" hidden>
            <span class="qe-kind">Dropped .xyz</span>
            <label class="qe-rlab">runs <select class="qe-select" id="qe-rset" aria-label="The SP set a dropped structure runs"></select></label>
            <label class="qe-rlab">name + <input class="qe-input mono" id="qe-rsfx" spellcheck="false"
              aria-label="Suffix of a dropped structure's calculation name"></label>
            <p class="hint qe-pad qe-drophint">Drop .xyz files anywhere on this window: each becomes a row that runs this set.</p>
          </div>
          <div class="qe-dragimgs" id="qe-dragimgs" aria-hidden="true"></div>
        </aside>
        <main class="qe-main" aria-label="Queue">
          <div class="qe-bar">
            <span class="qe-barsp"></span>
            <button class="btn btn-ghost qe-tbtn" type="button" id="qe-clear">Clear all</button>
            <button class="btn btn-ghost qe-tbtn" type="button" id="qe-cancel">Cancel</button>
            <button class="btn btn-ghost qe-tbtn" type="button" id="qe-stop">Stop after current</button>
            <button class="btn btn-primary" type="button" id="qe-run">Run queue</button>
          </div>
          <p class="qe-warn" id="qe-warn" role="status" hidden></p>
          <div class="qe-canvas" id="qe-canvas"><div class="qe-graph" id="qe-graph"></div></div>
        </main>
        <aside class="qe-insp" id="qe-insp" aria-label="Inspector"></aside>
      </div>`;
    NB.$("qe-run").addEventListener("click", () => NB.rail.runQueue());
    NB.$("qe-stop").addEventListener("click", () => NB.rail.stopAfterCurrent());
    NB.$("qe-cancel").addEventListener("click", () => NB.rail.cancelRun());
    NB.$("qe-clear").addEventListener("click", () => NB.rail.clearAll());
    wireCanvas();
    wireLibrary();
    wireKeys();
    NB.$("qe-insp").addEventListener("focusout", () => setTimeout(() => {
      const a = document.activeElement;
      if (!(a && NB.$("qe-insp").contains(a))) renderInspector();
    }, 0));
    _missing = SLOTS_NEEDED.filter(s => !NB.bridge || typeof NB.bridge[s] !== "function");
    NB.watch((changed) => { if (changed) sync(); else renderBar(); takeHandOff(); });
    document.addEventListener("visibilitychange", async () => {
      if (document.visibilityState !== "visible") return;
      // the theme may have been switched in the main window meanwhile
      const s = await NB.call("get_settings");
      if (s && s.theme) { NB.settings = s; NB.applyTheme(s.theme); }
      takeHandOff();
    });
    await loadSets();
    sync();
  }

  NB.qed = { init: init, render: render, dropXyz: dropXyz, dropInp: dropInp };
})();
