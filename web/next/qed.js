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
     level  an ORCA inp set: a whole input with {{JOB}} where the job keyword
            goes. A block made from it runs the job its kind (its Type) says;
            the backend fills {{JOB}} and adds that job's block at launch
     workflow  the steps of some blocks of one row, copied: their types,
            levels, gates and edges, without a structure -- pasted after a
            block, into an empty row, or onto new .xyz rows
   ============================================================ */

(function () {

  // ------------------------------------------------------------ geometry
  // The design's grid: a 140 x 64 block, 176px a column, 104px a row.
  const W = 140, H = 64, ROWH = 104, LANE_PAD = 28, COLW = 176, X0 = 24;
  /** @param {number} c */
  const X = (c) => X0 + c * COLW;

  /** what a set may be: an ORCA level of theory, or MLIP settings */
  const SET_KINDS = ["ORCA", "MLIP"];
  const SET_GROUP = { ORCA: "ORCA levels", MLIP: "MLIP" };
  const EDITABLE = ["pending", "blocked", "cancelled"];
  const STARTED = ["running", "done", "failed"];
  /** the name every new step of a kind gets after the system's: Ltyr-MV-opt */
  const SUFFIX = { mlip_opt: "pre", opt: "opt", ts_opt: "ts", freq: "freq", ts_freq: "freq",
                   opt_freq: "optfreq", ts_opt_freq: "tsfreq", irc: "irc", sp: "sp", nmr: "nmr" };
  /** what a calculation of each kind is called on its block */
  const KIND_LABEL = {
    mlip_opt: "Preopt", mlip_sp: "MLIP SP", mlip_freq: "MLIP Freq", mlip_opt_freq: "MLIP Opt+Freq",
    opt: "Opt", ts_opt: "OptTS", freq: "Freq", ts_freq: "TS Freq", irc: "IRC", sp: "SP",
    opt_freq: "Opt+Freq", ts_opt_freq: "OptTS+Freq", neb_ts: "NEB-TS", tddft: "TD-DFT",
    nmr: "NMR", general: "General", crest_conf: "CREST",
  };
  /** The Type select of a level's block, in the Guided builder's words
   *  (build.js KINDS[].label) and its order. Only the kinds the backend can
   *  fill {{JOB}} for are offered (get_inp_sets `jobs`). */
  const TYPE_LABEL = { opt: "Optimization", ts_opt: "TS optimization", freq: "Frequencies",
                       ts_freq: "TS frequencies", opt_freq: "Opt + Freq", ts_opt_freq: "TS Opt + Freq",
                       irc: "IRC", sp: "Single point", nmr: "NMR" };
  const TYPE_ORDER = ["opt", "ts_opt", "freq", "ts_freq", "opt_freq", "ts_opt_freq", "irc", "sp", "nmr"];
  /** the kinds whose approval gate can pass by itself on the imaginary-mode count */
  const FREQ_KINDS = ["freq", "ts_freq", "opt_freq", "ts_opt_freq"];
  const ST_LABEL = { done: "done", running: "running", waiting: "waiting for approval",
                     pending: "pending", failed: "failed", blocked: "blocked", cancelled: "cancelled" };
  const PLACE_KEYS = ["system", "node", "col", "row", "after", "gate", "auto_pass",
                      "approved", "ref_point", "set_name"];
  const GEOM = "{{GEOMETRY}}";
  const JOB = "{{JOB}}";
  const RECIPE_KEY = "queue.drop";
  /** the copied workflow, mirrored in the UI state for the main window's drop */
  const CLIP_KEY = "queue.workflow";
  const SLOTS_NEEDED = ["apply_queue_ops", "get_inp_sets", "save_inp_set", "get_system",
                        "add_xyz_systems", "open_queue_window", "pick_xyz_files", "read_xyz_paths",
                        "take_ui_state"];

  // ------------------------------------------------------------ backend mirrors
  /** the inp-set library (get_inp_sets) @type {InpSet[]} */
  let _sets = [];
  let _setsLoaded = false;
  /** each set kind's seeded text, what "+ New" starts from @type {Object<string, string>} */
  let _defaults = {};
  /** calc kind -> what {{JOB}} becomes (the kinds a level's block may run)
   *  @type {Object<string, string>} */
  let _jobs = {};
  /** calc kind -> the kind a level placed after it runs by default @type {Object<string, string>} */
  let _next = {};
  /** The copied workflow (copyWorkflow), editor-local; mirrored in the UI
   *  state under CLIP_KEY so the main window's drop can run it. @type {any} */
  let _clip = null;
  /** The inp set open in the set editor -- the inspector when nothing is
   *  selected. `id` "" is a new set not saved yet; `base` is the text and
   *  name as last saved (or as the new one started), what "unsaved" is
   *  measured against. It survives selecting blocks: a draft is never lost
   *  by looking elsewhere, only by an answered "Discard".
   *  @type {{id: string, kind: string, name: string, text: string, base: {name: string, text: string, kind: string}}|null} */
  let _ed = null;
  /** why the last save of the editor's set was refused, said in the editor */
  let _edErr = "";
  /** name -> the full calculation (get_calc), the summary it was read for,
   *  and the level texts its text migrates to (get_calc's `levels`)
   *  @type {Map<string, {sig: string, calc: any, levels?: string[]}>} */
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
  /** what is selected: a node (and which member of a stack), an edge, an
   *  empty system's lane, or several blocks of one row
   *  ({t:"multi", lane, keys, ignored}) @type {any} */
  let _sel = null;
  /** a rubber band being drawn on the canvas @type {any} */
  let _band = null;
  /** a band just ended: the click it would send to the background is not a
   *  "clear the selection" */
  let _bandEnded = false;
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
  /** bumped by every inspectNode: a paint that awaited is dropped when a
   *  newer one started meanwhile */
  let _inspTok = 0;
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

  /** The `order` an undo sends, rebuilt from the queue as it is NOW: the
   *  entry's names keep their order, rows the batch removes are left out,
   *  rows it restores stay where the entry had them, and a row added since
   *  (by a drop, another window, a run) keeps its place after the row it
   *  follows now. The store wants an exact permutation, so the stale full
   *  list an entry recorded failed after any outside change.
   *  @param {string[]} want @param {any[]} ops @returns {string[]} */
  function undoOrder(want, ops) {
    const cur = NB.queue.map((/** @type {any} */ c) => c.name);
    const gone = new Set();
    ops.forEach(o => {
      if (o.op === "remove_calc") gone.add(o.name);
      if (o.op === "remove_system") NB.queue.forEach((/** @type {any} */ c) => { if (c.system === o.id) gone.add(c.name); });
    });
    const final = new Set(cur.filter(n => !gone.has(n)));
    ops.forEach(o => { if (o.op === "add_calc" && o.calc && o.calc.name) final.add(o.calc.name); });
    const out = want.filter(n => final.has(n));
    const placed = new Set(out);
    cur.forEach((n, i) => {
      if (!final.has(n) || placed.has(n)) return;
      let at = 0;
      for (let j = i - 1; j >= 0; j--) {
        const k = out.indexOf(cur[j]);
        if (k >= 0) { at = k + 1; break; }
      }
      out.splice(at, 0, n);
      placed.add(n);
    });
    final.forEach(n => { if (!placed.has(n)) out.push(n); });
    return out;
  }

  async function undo() {
    const u = _undo.pop();
    if (!u) { NB.toast("Nothing to undo."); return; }
    // An undo never takes away a calculation that has started: an added
    // block that has run since is a result now, not an edit to take back.
    // The entry is dropped (it can no longer be undone as recorded); Remove…
    // on the block is the way to take a finished row off the list.
    const started = u.ops.filter((/** @type {any} */ o) => o.op === "remove_calc")
      .map((/** @type {any} */ o) => NB.queue.find((/** @type {any} */ c) => c.name === o.name))
      .filter((/** @type {any} */ c) => c && EDITABLE.indexOf(c.state) < 0);
    if (started.length) {
      const c = started[0];
      NB.fail("Cannot undo " + u.label + ": '" + c.name + "' has " +
              (c.state === "running" ? "started running" : "already run (" + c.state + ")") +
              ", and undoing would remove it from the queue. Use Remove… on it if you mean to.");
      return;
    }
    const ops = u.ops.map((/** @type {any} */ o) =>
      o.op === "order" && Array.isArray(o.names) ? { op: "order", names: undoOrder(o.names, u.ops) } : o);
    const r = await NB.call("apply_queue_ops", JSON.stringify(ops));
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
      // an MLIP calc's settings live in its config, which a new text rewrites
      // (the store reads the MLIP fields from it): the undo puts the whole
      // config back -- a Guided MLIP block has no text to re-read them from
      if ("raw_text" in patch && /^mlip/.test(String(was.kind || "")) && was.config && !("config" in patch)) {
        back.config = was.config;
      }
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
    _jobs = r && r.ok && r.jobs && typeof r.jobs === "object" ? r.jobs : {};
    _next = r && r.ok && r.next_kind && typeof r.next_kind === "object" ? r.next_kind : {};
    return _sets;
  }
  /** @param {string} name @param {string} kind */
  const setByName = (name, kind) =>
    _sets.find(s => s.name === name && (!kind || s.kind === kind)) || null;

  /** The kinds a level's block may run (the Type select), in the builder's
   *  order: those the backend fills {{JOB}} for. */
  const typeKinds = () => TYPE_ORDER.filter(k => k in _jobs);
  /** The set kind a calculation of `kind` is made from: "MLIP" for an MLIP
   *  pre-optimization, "ORCA" for a kind a level can run, "" otherwise.
   *  @param {string} kind */
  const setKindOf = (kind) => (kind === "mlip_opt" ? "MLIP"
    : (Object.keys(_jobs).length ? kind in _jobs : TYPE_ORDER.indexOf(kind) >= 0) ? "ORCA" : "");
  /** The natural next step after a block of `kind` ("" = a row's start):
   *  the backend's rule (inpsets.NEXT_KIND). @param {string} kind */
  const nextKind = (kind) => _next[kind || ""] || (kind ? "sp" : "opt");
  /** A frequency step after the optimization it checks: the pair whose
   *  levels have to agree. @param {string} a @param {string} b */
  const isFreqAfterOpt = (a, b) => (a === "opt" || a === "ts_opt") && (b === "freq" || b === "ts_freq");

  /** What a set's text is: MLIP settings (`key value` lines, no ! line) or
   *  an ORCA input. @param {string} text */
  function detectKind(text) {
    return /^\s*(backend|model|mlip)\b/im.test(text) && !/^\s*!/m.test(text) ? "MLIP" : "ORCA";
  }
  /** an ORCA job keyword, the vocabulary a ! line's job is read in */
  const JOB_WORD = /^(OPT|TIGHTOPT|LOOSEOPT|VERYTIGHTOPT|COPT|ZOPT|OPTTS|SCANTS|FREQ|NUMFREQ|ANFREQ|IRC|SP)$/i;
  /** The calculation kind an input's own job line says (a dropped or
   *  imported .inp), "" when it is not an ORCA input. @param {string} text */
  function jobKindOf(text) {
    if (detectKind(text) === "MLIP") return "";
    const w = String(text).split("\n").filter(l => l.trim().indexOf("!") === 0)
      .join(" ").toUpperCase().replace(/!/g, " ").split(/\s+/);
    const opt = w.some(x => /^(OPT|TIGHTOPT|LOOSEOPT|VERYTIGHTOPT|COPT|ZOPT)$/.test(x));
    const freq = w.some(x => /^(FREQ|NUMFREQ|ANFREQ)$/.test(x));
    if (w.indexOf("OPTTS") >= 0 || w.indexOf("SCANTS") >= 0) return freq ? "ts_opt_freq" : "ts_opt";
    if (w.indexOf("IRC") >= 0) return "irc";
    if (opt) return freq ? "opt_freq" : "opt";
    if (freq) return "freq";
    return "sp";
  }
  /** Whether {{JOB}} stands on a ! line, where a level needs it. @param {string} text */
  const hasJobLine = (text) => String(text || "").split("\n")
    .some(l => l.trim().indexOf("!") === 0 && l.indexOf(JOB) >= 0);
  /** An input's ! lines with every job keyword out and one {{JOB}} where the
   *  first stood (the rule a schema-1 set was migrated by), and the words
   *  taken out; null when it has no job keyword. @param {string} text */
  function jobToPlaceholder(text) {
    const taken = [];
    let placed = false;
    const out = String(text).split("\n").map(l => {
      if (l.trim().indexOf("!") !== 0) return l;
      const kept = [];
      l.trim().slice(1).split(/\s+/).filter(Boolean).forEach(t => {
        if (JOB_WORD.test(t)) { taken.push(t); if (!placed) { kept.push(JOB); placed = true; } }
        else kept.push(t);
      });
      return ("! " + kept.join(" ")).trimEnd();
    });
    return placed ? { text: out.join("\n"), words: taken } : null;
  }
  /** The level of theory a template runs at: its ! keywords without the job
   *  and convergence ones (and {{JOB}}), so an Opt and its Freq compare.
   *  @param {string} text */
  function levelOf(text) {
    const SKIP = /^(\{\{JOB\}\}|OPT|TIGHTOPT|LOOSEOPT|VERYTIGHTOPT|COPT|ZOPT|OPTTS|SCANTS|FREQ|NUMFREQ|ANFREQ|IRC|SP|NMR|ENGRAD|(VERY|EXTREME)?TIGHTSCF|NORMALSCF|LOOSESCF|SLOPPYSCF|STRONGSCF|EXTREMESCF)$/;
    return String(text || "").split("\n").filter(l => l.trim().indexOf("!") === 0)
      .join(" ").toUpperCase().replace(/!/g, " ").split(/\s+/)
      .filter(x => x && !SKIP.test(x)).sort().join(" ");
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
    const byName = new Map(q.map((/** @type {any} */ c) => [c.name, c]));
    const sysOf = (/** @type {any} */ c) => (c.system && sysById.has(c.system) ? c.system : "");
    // Standalone calculations tied by what they hang off (an `after`, or a
    // geometry reference built without the editor) share one lane: a TS, its
    // Freq and its IRC read as the chain they are, not as three loose rows.
    /** @type {Map<string, string>} */ const root = new Map();
    /** @param {string} n @returns {string} */
    const find = n => { let r = n; while (root.has(r) && root.get(r) !== r) r = /** @type {string} */ (root.get(r)); return r; };
    q.forEach((/** @type {any} */ c) => { if (!sysOf(c)) root.set(c.name, c.name); });
    q.forEach((/** @type {any} */ c) => {
      const p = byName.get(upOf(c));
      if (sysOf(c) || !p || p === c || sysOf(p)) return;
      const a = find(p.name), b = find(c.name);
      if (a !== b) {
        // the root that comes first in the queue names the lane
        if (idx.get(a) <= idx.get(b)) root.set(b, a); else root.set(a, b);
      }
    });
    /** @type {any[]} */ const lanes = [];
    const laneOf = new Map();
    q.forEach((/** @type {any} */ c) => {
      const sid = sysOf(c);
      const key = sid ? "s:" + sid : "c:" + find(c.name);
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
      // a standalone chain has no stored places: lay it out the way the
      // editor would have, each step one column right of what it hangs off,
      // a second branch on the next free row
      if (!L.sys && L.nodes.length > 1) {
        /** @type {Set<string>} */ const occ = new Set();
        // parent first: a child queued before what it hangs off is placed
        // after it, so it is drawn to its right (and adopt stores that)
        /** @type {any[]} */ const order = [];
        const seenN = new Set();
        /** @param {any} n */
        const visit = (n) => {
          if (seenN.has(n)) return;
          seenN.add(n);
          const P = nodeOf.get(upOf(n.members[0]));
          if (P && P.lane === L && P !== n) visit(P);
          order.push(n);
        };
        L.nodes.forEach(visit);
        order.forEach((/** @type {any} */ n) => {
          const P = nodeOf.get(upOf(n.members[0]));
          const free = (/** @type {number} */ c, /** @type {number} */ r) => !occ.has(c + "," + r);
          let c = 0, r = 0;
          if (P && P.lane === L && P !== n && P.placed) {
            // the design's rule: same row if free, else the first row below
            // that is free in this column and the one before it
            c = P.col + 1; r = P.row;
            if (!free(c, r)) { r = P.row + 1; while (!free(c, r) || !free(c - 1, r)) r++; }
          } else {
            while (!free(0, r)) r++;
          }
          n.col = c; n.row = r; n.placed = true; occ.add(c + "," + r);
        });
      }
      L.nodes.forEach((/** @type {any} */ n) => {
        n.stack = n.members.length > 1;
        n.first = idx.get(n.members[0].name) + 1;
        n.kind = n.members[0].kind;
        n.setKind = setKindOf(n.kind);
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
        const up = upOf(c);
        if (!up) return;
        const A = nodeOf.get(up), B = nodeOf.get(c.name);
        if (!A || A.lane !== L || A === B) return;
        const k = A.key + ">" + B.key;
        if (seen.has(k)) return;
        seen.add(k);
        L.edges.push({ key: k, from: A, to: B, gate: c.after && c.gate === "approval" ? "approval" : "auto",
                       ref: !c.after });
      });
      L.started = L.calcs.some((/** @type {any} */ c) => STARTED.indexOf(c.state) >= 0);
      L.name = L.sys ? String(L.sys.name || "") : L.calcs[0].name;
    });
    return { lanes: lanes, nodeOf: nodeOf, idx: idx };
  }
  /** What a calculation hangs off in the drawing: its `after`, else the
   *  calculation its geometry is taken from. @param {any} c @returns {string} */
  function upOf(c) {
    return c.after || (c.geometry_source === "reference" ? String(c.ref_name || "") : "");
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
  /** Whether a block is drawn selected: the one block, or one of several.
   *  @param {string} key */
  const isSelected = (key) => !!_sel && ((_sel.t === "node" && _sel.key === key)
    || (_sel.t === "multi" && _sel.keys.indexOf(key) >= 0));
  /** The selected blocks, in the model: one, several of one row, or none. */
  const selNodes = () => (!_sel ? [] : _sel.t === "node" ? [selNode()].filter(Boolean)
    : _sel.t === "multi" ? _sel.keys.map(nodeByKey).filter(Boolean) : []);
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
    if (_sel && _sel.t === "multi") {
      // blocks that went are dropped from the selection; one left is a block
      const keys = _sel.keys.filter((/** @type {string} */ k) => nodeByKey(k));
      _sel = keys.length > 1 ? Object.assign({}, _sel, { keys: keys })
        : keys.length ? { t: "node", key: keys[0], m: 0 } : null;
    }
    if (_sel && _sel.t === "edge" && !selEdge()) _sel = null;
    if (_sel && _sel.t === "sys" && !laneByKey(_sel.key)) _sel = null;
    renderBar();
    // a drag or a rubber band in progress keeps the canvas it started on
    if (!_drag && !_band) { renderLibrary(); renderCanvas(); }
    renderInspector();
  }

  function renderBar() {
    // cancelled rows re-run too (the engine runs every editable state)
    const anyRunnable = NB.queue.some((/** @type {any} */ c) => EDITABLE.indexOf(c.state) >= 0);
    NB.$("qe-run").disabled = NB.running || !anyRunnable;
    NB.$("qe-stop").disabled = !NB.running;
    NB.$("qe-cancel").disabled = !NB.running;
    NB.$("qe-clear").disabled = !NB.queue.length;
    const pb = /** @type {HTMLButtonElement} */ (NB.$("qe-paste"));
    pb.disabled = !_clip;
    pb.title = !_clip ? "Copy blocks first: select them (drag a box on the empty canvas, or Ctrl+click) and press Ctrl+C"
      : "Paste the copied workflow (" + wfSummary(_clip) + ") " + pasteTargetWords() + ". Ctrl+V does the same.";
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
        if (!isFreqAfterOpt(e.from.kind, e.to.kind)) return;
        const la = levelOf(levelText(e.from.members[0])), lb = levelOf(levelText(e.to.members[0]));
        if (la && lb && la !== lb) w.push(e.to.short + ": Freq level differs from " + e.from.short + ".");
      });
      if (L.sys && !(L.sys.inputs || []).length
          && L.calcs.some((/** @type {any} */ c) => !c.after && c.geometry_source !== "reference"
                                                && EDITABLE.indexOf(c.state) >= 0)) {
        w.push(L.name + ": choose a structure for the first calculation.");
      }
      const noGeo = L.calcs.find((/** @type {any} */ c) => c.set_name && setKindOf(c.kind) !== "MLIP"
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
    const s = setOf(c);
    return s ? s.text : "";
  }

  // ---- the library

  function renderLibrary() {
    const host = NB.$("qe-sets");
    const n = selNode();
    const editing = editorShown() && _ed ? _ed : null;
    const rows = _M ? _M.lanes.filter((/** @type {any} */ L) => L.calcs.length).map((/** @type {any} */ L) => [L.key, L.name]) : [];
    const sig = JSON.stringify([_sets.map(s => [s.id, s.name, s.kind]), n ? [n.key, n.setKind, n.editable,
      n.members[0].set_name] : null, _missing.length,
      editing ? [editing.id, editing.kind, editing.id ? "" : editing.name] : null,
      rows, _clip ? _clip.steps.length : 0]);
    if (sig === _libSig) return;
    _libSig = sig;
    if (_missing.length && !_sets.length) {
      host.innerHTML = `<p class="hint qe-pad">The queue editor needs the updated backend: it has no inp sets to offer.</p>`;
      NB.$("qe-dragimgs").innerHTML = "";
      renderRecipe();
      return;
    }
    // ORCA levels, then MLIP sets; each group shown, an empty one too:
    // "+ New" is where its first set comes from
    host.innerHTML = SET_KINDS.map(kd => {
      const sets = _sets.filter(s => s.kind === kd);
      const match = !!n && n.setKind === kd;
      const draft = editing && !editing.id && editing.kind === kd
        ? `<span class="qe-set" data-edit="1" data-draft="1">${esc(editing.name || "New set")} · unsaved</span>` : "";
      const what = kd === "ORCA" ? "level" : "MLIP set";
      return `<div class="qe-group">
        <div class="qe-ghead"><span class="qe-kind"${match ? ' data-on="1"' : ""}>${esc(SET_GROUP[kd])}</span>
          <button class="qe-new" type="button" data-new="${kd}" aria-label="New ${esc(what)}"
            title="A new ${esc(what)}, starting from ORCAdesk's default">+ New</button></div>
        ${sets.map(s => {
          const cur = !!n && n.members[0].set_name === s.name && n.setKind === kd;
          const applies = match && n.editable;
          const ed = !!editing && editing.id === s.id;
          return `<button class="qe-set" type="button" draggable="true" data-id="${esc(s.id)}"
            aria-pressed="${applies ? cur : ed}"${cur ? ' data-cur="1"' : ""}${match ? ' data-match="1"' : ""}${ed ? ' data-edit="1"' : ""}
            aria-label="${esc((applies ? "Apply " : "Edit ") + what + " " + s.name)}"
            title="${applies ? "Click to put this level on the selected block (its type stays). " : "Click to edit it. "}Drag onto a block (its level changes, its type stays) or onto the + slot (a new step). Delete removes the set.">${esc(s.name)}</button>`;
        }).join("")}${draft}
      </div>`;
    }).join("");
    NB.$("qe-dragimgs").innerHTML = _sets.map(s =>
      `<div class="qe-dragimg" id="qe-di-${esc(s.id)}"><span class="qe-nm">${
        esc(s.kind === "MLIP" ? "Preopt" : "Level")}</span><span class="qe-sub">${esc(s.name)}</span></div>`).join("");
    renderRecipe();
  }

  /** The drop recipe: what a dropped .xyz runs -- an ORCA level as a single
   *  point (`set:<id>`, the name suffix applies), the copied workflow
   *  (`clip`), or the workflow of a row (`row:<lane key>`).
   *  @returns {Promise<{run: string, set_id: string, row: string, suffix: string}>} */
  async function recipe() {
    if (!_setsLoaded) await loadSets();
    const r = await NB.call("get_ui_state", RECIPE_KEY);
    const v = r && r.ok && r.value && typeof r.value === "object" ? r.value : {};
    const levels = _sets.filter(s => s.kind === "ORCA");
    const suffix = typeof v.suffix === "string" ? v.suffix : "-sp";
    const run = typeof v.run === "string" ? v.run : v.set_id ? "set:" + v.set_id : "";
    if (run === "clip") return { run: "clip", set_id: "", row: "", suffix: suffix };
    if (run.indexOf("row:") === 0) return { run: "row", set_id: "", row: run.slice(4), suffix: suffix };
    const set = levels.find(s => "set:" + s.id === run) || levels[0] || null;
    return { run: "set", set_id: set ? set.id : "", row: "", suffix: suffix };
  }
  async function renderRecipe() {
    const box = NB.$("qe-recipe");
    const levels = _sets.filter(s => s.kind === "ORCA");
    const rows = _M ? _M.lanes.filter((/** @type {any} */ L) => L.calcs.length) : [];
    if (!levels.length && !_clip && !rows.length) { box.hidden = true; return; }
    const rec = await recipe();
    box.hidden = false;
    const cur = rec.run === "set" ? "set:" + rec.set_id : rec.run === "clip" ? "clip" : "row:" + rec.row;
    const opt = (/** @type {string} */ v, /** @type {string} */ label) =>
      `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(label)}</option>`;
    const sel = NB.$("qe-rset");
    sel.innerHTML = (levels.length ? `<optgroup label="Single point at">${
        levels.map(s => opt("set:" + s.id, s.name + " (SP)")).join("")}</optgroup>` : "")
      + (_clip || rows.length ? `<optgroup label="Workflow">${
        _clip ? opt("clip", "the copied workflow (" + wfSummary(_clip) + ")") : ""}${
        rows.map((/** @type {any} */ L) => opt("row:" + L.key, "the workflow of row " + L.name)).join("")}</optgroup>` : "")
      + (rec.run === "row" && !rows.some((/** @type {any} */ L) => L.key === rec.row)
        ? opt(cur, "a row that is gone") : "")
      + (rec.run === "clip" && !_clip ? opt("clip", "the copied workflow (nothing copied)") : "");
    const sfx = /** @type {HTMLInputElement} */ (NB.$("qe-rsfx"));
    if (document.activeElement !== sfx) sfx.value = rec.suffix;
    sfx.disabled = rec.run !== "set";
    sfx.title = rec.run === "set" ? "" : "A workflow names its steps after the file and each step: water-opt, water-freq ...";
    NB.$("qe-rhint").textContent = rec.run === "set"
      ? "Drop .xyz files anywhere on this window: each becomes a row that runs this single point."
      : "Drop .xyz files anywhere on this window: each becomes a row that runs that whole workflow, named after the file.";
  }
  function saveRecipe() {
    NB.call("set_ui_state", RECIPE_KEY, JSON.stringify({
      run: NB.$("qe-rset").value, suffix: NB.$("qe-rsfx").value }));
    renderRecipe();
  }

  // ---- the canvas

  function renderCanvas() {
    const M = _M;
    const host = NB.$("qe-graph");
    const scroller = NB.$("qe-canvas");
    const keep = [scroller.scrollLeft, scroller.scrollTop];
    // a row's grip that has the keyboard keeps it across the redraw (Alt+Up /
    // Alt+Down moves the row, and the next press must move it again)
    const ae = /** @type {HTMLElement|null} */ (document.activeElement);
    const gripKey = ae && host.contains(ae) && ae.getAttribute("data-a") === "grip" ? ae.getAttribute("data-lane") : null;
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
      const ya = Y(L, A.row) + H / 2, yb = Y(L, B.row) + H / 2;
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
    /** @type {{key: string, lane: string, x: number, y: number}[]} */ const rects = [];
    M.lanes.forEach((/** @type {any} */ L) => L.nodes.forEach((/** @type {any} */ n) => {
      const sel = isSelected(n.key);
      const open = n.stack && _expanded === n.key && sel && _sel.t === "node";
      const c0 = n.members[0];
      const mod = isModified(n);
      const setName = c0.set_name || (c0.is_raw ? ".inp text" : "guided");
      const aria = (n.stack ? `${n.name}, ${n.members.length} calculations` : `Select ${n.name}`)
        + ", " + ST_LABEL[n.state];
      rects.push({ key: n.key, lane: L.key, x: X(n.col), y: Y(L, n.row) });
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
        const ya = Y(g.lane, P.row) + H / 2, yb = Y(g.lane, g.r) + H / 2;
        if (!g.branch) {
          parts.push(`<span class="qe-gline" style="left:${X(P.col) + W + 6}px; top:${ya - 1}px; width:${X(g.c) - X(P.col) - W - 12}px"></span>`);
        } else {
          const bx = X(P.col) + W + 18;
          parts.push(`<span class="qe-gline" data-dir="v" style="left:${bx - 1}px; top:${ya}px; height:${yb - ya}px"></span>`);
          parts.push(`<span class="qe-gline" style="left:${bx}px; top:${yb - 1}px; width:${X(g.c) - bx - 6}px"></span>`);
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
    if (gripKey) {
      const back = /** @type {HTMLElement|null} */ (host.querySelector(`[data-a="grip"][data-lane="${CSS.escape(gripKey)}"]`));
      if (back) back.focus({ preventScroll: true });
    }
    host._ghosts = ghosts;
    host._rects = rects;
    host._laneTops = M.lanes.map((/** @type {any} */ L) => L.top).concat([top]);
    scroller.scrollLeft = keep[0]; scroller.scrollTop = keep[1];
    if (_pop) setTimeout(() => { _pop = ""; }, 300);
  }

  /** Whether a block's template is no longer its set's (the design's *).
   *  @param {any} n */
  function isModified(n) {
    const c = n.members[0];
    if (!c.set_name) return false;
    const s = setOf(c);
    const f = _full.get(c.name);
    if (!s || !f || !f.calc) return false;
    return !!String(f.calc.raw_text || "") && !sameAsSet(c, s.text);
  }
  /** Whether a block's text is `text`: verbatim, or -- a block made from a
   *  schema-1 set, which copied the OLD set's text (`TightOpt`, its seeded
   *  %geom) -- one of the levels its text becomes under the migration
   *  (get_calc's `levels`, inpsets.level_forms on the backend: the
   *  migration's own rule, not a copy of it here). @param {any} c @param {string} text */
  function sameAsSet(c, text) {
    const f = _full.get(c.name);
    const own = f && f.calc ? String(f.calc.raw_text || "") : "";
    return own === text || (!!f && Array.isArray(f.levels) && f.levels.indexOf(text) >= 0);
  }
  /** The set a block came from: the one its set_name names when the block's
   *  text is that set's; else -- a schema-1 set the migration merged into
   *  another, or renamed "name (Kind)" because the name was taken -- the set
   *  of its kind whose text the block's migrates to (a renamed one first);
   *  else the named set, or null. @param {any} c */
  function setOf(c) {
    if (!c || !c.set_name) return null;
    const kind = setKindOf(c.kind);
    const named = setByName(c.set_name, kind);
    if (named && sameAsSet(c, named.text)) return named;
    const pre = c.set_name + " (";
    const same = _sets.filter(x => x.kind === kind && x !== named && sameAsSet(c, x.text));
    const found = same.find(x => x.name.indexOf(pre) === 0) || (named ? null : same[0]) || null;
    return found || named;
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
    const many = _sel && _sel.t === "multi" ? selNodes() : null;
    if (!n && !e && !L && !many) edEnsure();
    const sig = JSON.stringify([_sel, !!_clip, Object.keys(_jobs).length, many ? many.map((/** @type {any} */ x) => [x.key, x.name, x.kind]) : null,
      !n && !e && !L && !many ? [_ed && [_ed.id, _ed.kind, _ed.base], _edErr,
        _sets.map(x => [x.id, x.name, x.kind]), edUsers().map((/** @type {any} */ c) => [c.name, c.state])] : null,
      n ? n.members.map((/** @type {any} */ c) => [c, _full.get(c.name) && _full.get(c.name).calc]) : null,
      e ? [e.gate, e.to.members, e.from.members] : null, L ? L.sys : null,
      n ? (n.lane.sys || null) : null, n ? isModified(n) : null, _sets.length]);
    if (sig === _inspSig) return;
    _inspSig = sig;
    if (n) inspectNode(host, n);
    else if (many) inspectMulti(host, many);
    else if (e) inspectEdge(host, e);
    else if (L) inspectSystem(host, L);
    else inspectSets(host);
  }

  /** @param {HTMLElement} host @param {any} n */
  async function inspectNode(host, n) {
    const tok = ++_inspTok;
    const L = n.lane;
    const m0 = _sel.m || 0;
    const m = Math.min(m0, n.members.length - 1);
    const c = n.members[m];
    // Every handler below acts on the node and member as the model holds
    // them WHEN it fires: a rename in this same inspector (which does not
    // repaint under the cursor) renamed the calculations this paint knew.
    const live = () => {
      const nn = nodeByKey(n.key);
      return nn ? { n: nn, L: nn.lane, c: nn.members[Math.min(m, nn.members.length - 1)] } : null;
    };
    /** a calculation whose charge / multiplicity is not its row's shows,
     *  and edits, its own @param {any} x */
    const ownCM = (x) => !!(x.lane.sys && (int(x.c.charge) !== int(x.lane.sys.charge)
      || (int(x.c.multiplicity) || 1) !== (int(x.lane.sys.multiplicity) || 1)));
    const sys = L.sys;
    const sysName = sys ? String(sys.name || "") : c.name;
    const suffix = sys && c.name.toLowerCase().indexOf(lower(sysName)) === 0 ? c.name.slice(sysName.length) : "";
    const nameLocked = L.started || !sys;
    const locked = EDITABLE.indexOf(c.state) < 0;
    const f = _full.get(c.name);
    const full = f ? f.calc : null;
    const isTemplate = !!c.set_name || (c.is_raw && !!sys);
    const isMlip = setKindOf(c.kind) === "MLIP" && !!c.set_name;
    const text = isTemplate ? templateOf(c) : "";
    const mod = isModified(n);
    const kind = detectKind(text);
    // a level's block: its Type is its kind, and {{JOB}} is filled from it
    const isLevel = isTemplate && !isMlip && hasJobLine(text);
    const types = typeKinds();
    const own = ownCM({ lane: L, c: c });
    const charge = sys && !own ? sys.charge : c.charge, mult = sys && !own ? sys.multiplicity : c.multiplicity;
    const cmLocked = sys && !own ? nameLocked : locked;

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
    if (!host.isConnected || !_sel || _sel.key !== n.key || (_sel.m || 0) !== m0
        || tok !== _inspTok || NB.build.target() !== null) return;

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
          spellcheck="false"${cmLocked ? " readonly" : ""}></label>
        <label>mult <input class="qe-num" data-f="mult" value="${esc(String(mult))}" inputmode="numeric"
          spellcheck="false"${cmLocked ? " readonly" : ""}></label>
        <span class="qe-note">${esc(ST_LABEL[nodeState({ members: [c] })] + ", run order " + (_M.idx.get(c.name) + 1))}</span>
      </div>
      ${own ? `<p class="qe-note" data-tone="warn">${esc(`${c.name} runs at its own charge ${int(c.charge)} and multiplicity ${
          int(c.multiplicity) || 1}; its row's are ${int(sys.charge)} and ${int(sys.multiplicity) || 1}. These fields change only this calculation.`)}</p>` : ""}
      ${c.message ? `<p class="qe-note" data-tone="${c.state === "failed" ? "err" : c.state === "blocked" ? "warn" : ""}">${esc(c.message)}</p>` : ""}
      ${blockedNote ? `<p class="qe-note" data-tone="warn">${esc(blockedNote)}</p>` : ""}
      ${afterNote ? `<p class="qe-note">${esc(afterNote)}</p>` : ""}
      ${c.waiting ? `<div class="qe-wait"><span>${esc((pa ? pa.name : c.after) + " finished. The queue skips "
          + c.name + " and runs the next calculation until you approve.")}</span>
        <button class="btn btn-primary" type="button" data-a="approve">Approve</button></div>` : ""}
      <select class="qe-select" data-f="geo" aria-label="Geometry"${locked ? " disabled" : ""}>${
        opts.map(o => `<option value="${esc(o.key)}"${o.key === geo ? " selected" : ""}>${esc(o.label)}</option>`).join("")}</select>
      ${isLevel ? `
        <label class="qe-typerow"><span class="qe-note">Type</span>
          <select class="qe-select" data-f="type" aria-label="Type: the job this block runs at its level"${
            locked || !n.editable ? " disabled" : ""}>${(types.indexOf(c.kind) >= 0 ? types : [c.kind].concat(types)).map(k =>
              `<option value="${esc(k)}"${k === c.kind ? " selected" : ""}>${esc(TYPE_LABEL[k] || KIND_LABEL[k] || k)}</option>`).join("")}</select></label>` : ""}
      ${isTemplate ? `
        <textarea class="qe-tpl" data-f="tpl" rows="${isLevel ? 8 : 11}" spellcheck="false"${locked || !n.editable ? " readonly" : ""}
          aria-label="${esc((n.setKind === "ORCA" ? "Level" : n.setKind || "Template") + " / " + (c.set_name || ".inp text"))}">${esc(text)}</textarea>
        ${mod && !locked && n.editable ? `
          <div class="qe-saveas">
            <input class="qe-input" data-f="newname" placeholder="${esc(kind === "ORCA" ? "Save as a new level" : "Save as a new MLIP set")}"
              aria-label="Name for the new inp set">
            <button class="btn btn-primary" type="button" data-a="saveas" disabled>Save</button>
          </div>` : ""}
        ${isMlip ? `<p class="hint">An MLIP block runs the settings these lines name.</p>` : ""}
        ${isTemplate && !isMlip && !isLevel && setKindOf(c.kind) === "ORCA" ? `<p class="hint">This text names its own job (it has no ${JOB}), so it runs as written; its type cannot change here.</p>` : ""}
        ${isLevel ? `<span class="qe-note qe-rendh">Rendered input · ${esc(TYPE_LABEL[c.kind] || c.kind)}</span>
        <textarea class="qe-tpl" data-f="rendered" rows="12" readonly spellcheck="false"
          aria-label="The input this block runs, as ORCAdesk renders it for its type">Rendering…</textarea>` : ""}`
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
      </div>
      ${workflowButtons(true)}`;

    const q = (/** @type {string} */ s) => /** @type {any} */ (host.querySelector(s));
    const sn = q('[data-f="sysname"]');
    if (sn) {
      sn.addEventListener("focus", () => sn.select());
      sn.addEventListener("input", () => { sn.style.width = Math.max(sn.value.length, 2) + 0.5 + "ch"; });
      sn.addEventListener("change", () => { const v = live(); if (v) renameSystem(v.L, sn.value); });
    }
    ["charge", "mult"].forEach(k => {
      const el = q(`[data-f="${k}"]`);
      el.addEventListener("focus", () => el.select());
      el.addEventListener("change", () => {
        const v = live();
        if (v) setChargeMult(ownCM({ lane: v.L, c: v.c }) ? { sys: null } : v.L, v.c, k, el.value);
      });
    });
    q('[data-f="geo"]').addEventListener("change", (/** @type {any} */ ev) => {
      const v = live(); if (v) setGeometry(v.L, v.c, ev.target.value);
    });
    const tpl = q('[data-f="tpl"]');
    if (tpl) {
      tpl.addEventListener("change", () => { const v = live(); if (v) setTemplate(v.n, tpl.value); });
      const nm = q('[data-f="newname"]'), sv = q('[data-a="saveas"]');
      if (nm && sv) {
        nm.addEventListener("input", () => { sv.disabled = !nm.value.trim(); });
        sv.addEventListener("click", () => { const v = live(); if (v) saveAs(v.n, tpl.value, nm.value.trim()); });
      }
    }
    const ty = q('[data-f="type"]');
    if (ty) ty.addEventListener("change", () => { const v = live(); if (v) setType(v.n, ty.value); });
    wireWorkflowButtons(host, [n], L);
    const rd = q('[data-f="rendered"]');
    if (rd) {
      const r = await NB.call("build_inp_preview", JSON.stringify(calcDict(c.name, { is_raw: true, raw_text: text })));
      if (rd.isConnected) rd.value = r && r.ok ? String(r.text || "") : "Could not render the input" + (r && r.error ? ": " + r.error : ".");
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
    if (ed) ed.addEventListener("click", () => { const v = live(); if (v) openBuilder(v.c.name); });
    const ap = q('[data-a="approve"]');
    if (ap) ap.addEventListener("click", () => {
      const v = live();
      if (!v) return;
      const u = updates([v.c.name], () => ({ approved: true }));
      apply(u.ops, u.inv, "the approval");
    });
    q('[data-a="remove"]').addEventListener("click", () => { const v = live(); if (v) requestRemove(v.n); });
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
    // the members this edge holds: those whose `after` is A, or -- an edge
    // drawn from a geometry reference -- whose reference is A
    const gated = B.members.filter((/** @type {any} */ c) => A.members.some((/** @type {any} */ a) => a.name === upOf(c)));
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
      // a gate belongs to an `after`: a reference-only edge gets one first
      const u = updates(names, n => {
        const c = gated.find((/** @type {any} */ x) => x.name === n);
        return c && !c.after ? { after: upOf(c), gate: g, approved: false } : { gate: g, approved: false };
      });
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
      <div class="qe-saveas">
        <button class="btn btn-sm btn-ghost" type="button" data-a="wfpaste"${_clip ? "" : " disabled"}
          title="${_clip ? "Ctrl+V: " + esc(wfSummary(_clip)) : "Nothing copied yet"}">Paste workflow</button>
      </div>
      <p class="hint">Drop an inp set on the + slot, or press it to build the first calculation. A copied workflow pastes here as its first steps.</p>`;
    const q = (/** @type {string} */ x) => /** @type {any} */ (host.querySelector(x));
    const wp = q('[data-a="wfpaste"]');
    if (wp) wp.addEventListener("click", () => paste());
    const sn = q('[data-f="sysname"]');
    // the lane as the model holds it when a handler fires (a rename here
    // does not repaint under the cursor, and the undo needs the new name)
    const liveL = () => laneByKey(L.key) || L;
    sn.addEventListener("focus", () => sn.select());
    sn.addEventListener("change", () => renameSystem(liveL(), sn.value));
    ["charge", "mult"].forEach(k => {
      const el = q(`[data-f="${k}"]`);
      el.addEventListener("focus", () => el.select());
      el.addEventListener("change", () => setChargeMult(liveL(), null, k, el.value));
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
    if (kind !== "MLIP" && !hasJobLine(text)) {
      NB.fail(`A level needs ${JOB} on its ! line, where the job keyword goes: put it in place of ${
        jobKindOf(text) ? "the job keyword" : "a job keyword"} and save again.`);
      return;
    }
    const r = await NB.call("save_inp_set", JSON.stringify({ name: name, kind: kind, text: text }));
    if (!r || r.ok !== true) { NB.fail(why(r, "Could not save the set")); return; }
    await loadSets();
    NB.toast(`Saved "${name}" as ${kind === "ORCA" ? "a level" : "an MLIP set"}.`, "ok");
    if (kind === n.setKind) {
      const set = r.set || setByName(name, kind);
      const u = updates(n.members.map((/** @type {any} */ c) => c.name),
                        () => ({ raw_text: text, is_raw: true, set_name: name, set_id: set ? set.id : "" }));
      await apply(u.ops, u.inv, "the set change");
    } else render();
  }

  /** The Type of a level's block: its kind, the job {{JOB}} becomes. Only
   *  this block changes -- what hangs off it, and how a new step after it is
   *  shaped (an IRC's -F / -B pair), are left as they are. A block still
   *  named after its old type (Ltyr-freq) takes the new one's name
   *  (Ltyr-irc) when nothing refers to it by name.
   *  @param {any} n @param {string} kind */
  function setType(n, kind) {
    if (!n.editable || !kind || kind === n.kind) return;
    const c0 = n.members[0];
    let rename = "";
    if (!n.stack && n.lane.sys) {
      const base = String(n.lane.sys.name || "") + "-";
      const m = c0.name.indexOf(base) === 0 ? /^([a-z]+)(\d*)$/.exec(c0.name.slice(base.length)) : null;
      const used = NB.queue.some((/** @type {any} */ x) => x.name !== c0.name
        && (x.after === c0.name || (x.geometry_source === "reference" && x.ref_name === c0.name)));
      if (m && m[1] === SUFFIX[n.kind] && SUFFIX[kind] && SUFFIX[kind] !== m[1] && !used) {
        rename = freeName(base + SUFFIX[kind]);
      }
    }
    const ops = [], inv = [];
    n.members.forEach((/** @type {any} */ c) => {
      ops.push({ op: "update_calc", name: c.name, calc: rename ? { kind: kind, name: rename } : { kind: kind } });
      inv.push({ op: "update_calc", name: rename || c.name, calc: rename ? { kind: c.kind, name: c.name } : { kind: c.kind } });
    });
    apply(ops, inv, "the type change");
  }

  /** The workflow buttons of an inspector: copy what is selected, copy the
   *  whole row, and (`withPaste`) paste the copied workflow after it.
   *  @param {boolean} withPaste */
  function workflowButtons(withPaste) {
    return `<div class="qe-saveas" data-wf="1">
        <button class="btn btn-sm" type="button" data-a="wfcopy" title="Ctrl+C: these steps without their structure">Copy workflow</button>
        <button class="btn btn-sm btn-ghost" type="button" data-a="wfrow" title="Every step of this row, without its structure">Copy the row</button>
        ${withPaste ? `<button class="btn btn-sm btn-ghost" type="button" data-a="wfpaste"${_clip ? "" : " disabled"}
          title="${_clip ? "Ctrl+V: " + esc(wfSummary(_clip)) + ", " + esc(pasteTargetWords()) : "Nothing copied yet"}">Paste workflow</button>` : ""}
      </div>`;
  }
  /** @param {HTMLElement} host @param {any[]} nodes @param {any} L */
  function wireWorkflowButtons(host, nodes, L) {
    const on = (/** @type {string} */ a, /** @type {() => void} */ fn) => {
      const el = host.querySelector(`[data-a="${a}"]`); if (el) el.addEventListener("click", fn);
    };
    // re-read from the model at click time: a rename since this was drawn
    // renamed every calculation the nodes hold
    on("wfcopy", () => copyWorkflow(nodes.map((/** @type {any} */ x) => nodeByKey(x.key) || x)));
    on("wfrow", () => copyWorkflow((laneByKey(L.key) || L).nodes));
    on("wfpaste", () => paste());
  }

  /** Several blocks of one row: what they are, and their workflow.
   *  @param {HTMLElement} host @param {any[]} nodes */
  function inspectMulti(host, nodes) {
    const L = nodes[0].lane;
    const ign = int(_sel && _sel.ignored);
    host.innerHTML = `
      <div class="qe-ititle"><span class="qe-sysname" data-fixed="1">${nodes.length} blocks</span>
        <span class="qe-sfx">&nbsp;in ${esc(L.name)}</span></div>
      <div class="qe-inputs">${nodes.map((/** @type {any} */ n) => `<span class="qe-note">${esc(n.short)} · ${
        esc((KIND_LABEL[n.kind] || n.kind) + " / " + (n.members[0].set_name || (n.members[0].is_raw ? ".inp text" : "guided")))}</span>`).join("")}</div>
      ${ign ? `<p class="qe-note" data-tone="warn">${ign} block${ign === 1 ? " in another row was" : "s in other rows were"} left out: a workflow is copied from one row.</p>` : ""}
      ${workflowButtons(false)}
      <p class="hint">Copy workflow (Ctrl+C) keeps these steps (their types, levels, gates and connections) without the structure. Paste it after a block, into an empty row, or onto new .xyz rows (Ctrl+V). Ctrl+click adds or drops a block; Escape clears.</p>`;
    wireWorkflowButtons(host, nodes, L);
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
    const what = set.kind === "ORCA" ? "level" : "MLIP set";
    NB.modalRaw(set.kind === "ORCA" ? "Replace the level?" : "Replace the inp set?",
      `<p>${esc(n.short)} changes from ${esc(from)} to ${esc(set.name)}${
        set.kind === "ORCA" ? ", and stays " + esc(TYPE_LABEL[n.kind] || KIND_LABEL[n.kind] || n.kind) : ""}.${
        esc(lost)} This is asked once. Later ${what} replacements apply right away.</p>`,
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
  const aKind = (k) => "an " + k;
  /** "level" / "MLIP set": what a set of a kind is called @param {string} k */
  const setWord = (k) => (k === "ORCA" ? "level" : "MLIP set");
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
    const s = _sets.find(x => x.id === _ed.id) || null;
    return NB.queue.filter((/** @type {any} */ c) => setKindOf(c.kind) === b.kind
      && (s ? setOf(c) === s : c.set_name === b.name));
  }

  /** Run `fn` when the editor's edits may go: right away without any, else
   *  after asking (keep / discard), as the builder treats its draft.
   *  @param {() => void} fn */
  function guardSwitch(fn) {
    if (!edDirty()) { fn(); return; }
    const d = /** @type {any} */ (_ed);
    NB.modalRaw("Unsaved changes",
      `<p>The ${esc(setWord(d.kind))} "${esc(d.name.trim() || d.base.name)}" has changes that are not saved.</p>`,
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
        : "! " + JOB + "\n\n* xyz {{CHARGE}} {{MULT}}\n" + GEOM + "\n*\n");
      const name = freeSetName(kind, kind === "ORCA" ? "New level" : "New MLIP set");
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
      // an ORCA input names its job and carries its structure; a level has
      // {{JOB}} and the placeholders there. Both are offered, never done
      // silently: each is a checkbox, both ticked
      const job = detectKind(text) === "ORCA" && !hasJobLine(text) ? jobToPlaceholder(text) : null;
      const ph = text.indexOf(GEOM) >= 0 ? null : placeholderize(text);
      if (!job && !ph) { open(text); return; }
      NB.modalRaw("Make it a level?",
        `<p>${esc(stem)}.inp is a whole input. A level leaves the job to each block's Type and runs
          on each block's structure, charge and multiplicity.</p>
        ${job ? `<label class="checkbox"><input type="checkbox" id="qe-imp-job" checked>
          <span>Put ${JOB} where its job keyword${job.words.length === 1 ? "" : "s"} ${esc(job.words.join(" "))} ${
            job.words.length === 1 ? "is" : "are"}</span></label>` : ""}
        ${ph ? `<label class="checkbox"><input type="checkbox" id="qe-imp-geo" checked>
          <span>Replace its structure (${esc(ph.what)}) with the placeholders</span></label>
        <div class="modal-code">* xyz {{CHARGE}} {{MULT}}\n${GEOM}\n*</div>` : ""}
        <p>Kept as it is, the level cannot be saved until it has ${JOB} on its ! line and ${GEOM}.</p>`,
        [{ label: "Keep as it is", cls: "btn-ghost", act: () => open(text) },
         { label: "Apply", cls: "btn-primary", focus: true, act: () => {
            const cj = /** @type {any} */ (document.getElementById("qe-imp-job"));
            const cg = /** @type {any} */ (document.getElementById("qe-imp-geo"));
            // read before the modal's body is replaced by the next one
            let t = text;
            if (ph && (!cg || cg.checked)) t = /** @type {any} */ (placeholderize(t) || { text: t }).text;
            if (job && (!cj || cj.checked)) t = /** @type {any} */ (jobToPlaceholder(t) || { text: t }).text;
            open(t);
          } }]);
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
      : kind !== "MLIP" && text.indexOf(GEOM) < 0 ? `A level needs ${GEOM} where the coordinates go.`
      : kind !== "MLIP" && !hasJobLine(text) ? `A level needs ${JOB} on its ! line, where the job keyword goes (each block's Type fills it in).`
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
    NB.toast(`Saved the ${setWord(r.set.kind)} "${r.set.name}".`, "ok");
    if (old) offerUpdate(old, r.set);
  }

  /** After a set is saved over itself: the blocks made from it that have not
   *  run, whose text is now another one, are offered the new text -- once,
   *  as one undoable batch. Blocks that have run never change.
   *  @param {{name: string, kind: string}} old @param {InpSet} set */
  function offerUpdate(old, set) {
    if (old.kind !== set.kind) return;
    const names = NB.queue.filter((/** @type {any} */ c) => c.set_name === old.name
        && setKindOf(c.kind) === old.kind && EDITABLE.indexOf(c.state) >= 0
        && (!sameAsSet(c, set.text) || c.set_name !== set.name))
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
    const users = NB.queue.filter((/** @type {any} */ c) => c.set_name === set.name && setKindOf(c.kind) === set.kind).length;
    NB.confirm("Delete the inp set",
      `The ${setWord(set.kind)} "${set.name}" leaves the library.${users ? ` The ${users} block${users === 1 ? "" : "s"} made from it keep${users === 1 ? "s" : ""} ${users === 1 ? "its" : "their"} text.` : ""}`,
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
        aria-label="${esc("The " + setWord(d.kind) + "'s text")}">${esc(d.text)}</textarea>
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
      q('[data-f="kind"]').textContent = k === "ORCA" ? "ORCA level" : "MLIP set";
      q('[data-f="dirty"]').textContent = !d.id ? "· new, not saved" : edDirty() ? "· unsaved changes" : "";
      const mv = q('[data-f="moves"]');
      mv.hidden = k === d.kind;
      mv.textContent = k === d.kind ? "" : `This text reads as ${k === "MLIP" ? "MLIP settings" : "an ORCA input"}: saved, it moves to the ${SET_GROUP[k]} group.`;
      if (k === "ORCA" && !mv.textContent && !hasJobLine(d.text)) {
        mv.hidden = false;
        mv.textContent = `Put ${JOB} on the ! line where the job keyword goes: each block's Type fills it in.`;
      }
      q('[data-f="ph"]').textContent = k === "MLIP"
        ? "One setting per line: model, device (auto, cpu, cuda), nprocs, env."
        : JOB + " the block's job, from its Type (TightOpt, OptTS, Freq, IRC, SP ...; ORCAdesk adds the %geom / %irc block that job needs unless the text has its own) · "
          + GEOM + " the block's coordinates · {{CHARGE}} {{MULT}} its charge and multiplicity · {{HESS}} optional: an IRC reads the Hessian of the step before it by itself.";
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
    // the whole standalone lane moves together -- a chain drawn as one row
    // stays one row -- each block where it is drawn now
    const nodes = P.lane.nodes.length ? P.lane.nodes : [P];
    // A system has one charge and multiplicity, the one its new steps take.
    // When the chain's calculations run at different ones (a triplet SP after
    // a singlet TS) that is the selected block's, and each calculation keeps
    // its own -- asked first, since the row then reads as one charge.
    const all = nodes.reduce((/** @type {any[]} */ a, /** @type {any} */ n) => a.concat(n.members), []);
    const cm = (/** @type {any} */ c) => int(c.charge) + "/" + (int(c.multiplicity) || 1);
    const mixed = new Set(all.map(cm)).size > 1;
    const root = nodes[0].members[0];
    const head = mixed ? P.members[0] : root;
    if (mixed && !(await askMixedCharge(all, head))) return null;
    const f = _full.get(root.name);
    const sysId = newId("s");
    const xyz = f && f.calc && f.calc.geometry_source !== "reference" ? String(f.calc.xyz || "") : "";
    const sysNames = new Set(NB.systems.map((/** @type {any} */ s) => lower(s.name)));
    const base = cleanName(root.name) || "system";
    let nm = base;
    for (let i = 2; sysNames.has(lower(nm)); i++) nm = base + "-" + i;
    /** @type {any[]} */ const ops = [
      { op: "add_system", system: { id: sysId, name: nm, charge: head.charge, multiplicity: head.multiplicity,
          inputs: xyz ? [{ label: root.name + " input", xyz: xyz }] : [] } },
    ];
    /** @type {any[]} */ const inv = [];
    let parentId = "";
    nodes.forEach((/** @type {any} */ n) => {
      const id = newId("n");
      if (n === P) parentId = id;
      n.members.forEach((/** @type {any} */ c) => {
        ops.push({ op: "place_calc", name: c.name, place: { system: sysId, node: id, col: n.col, row: n.row } });
        inv.push({ op: "place_calc", name: c.name, place: { system: "", node: "", col: 0, row: 0 } });
      });
    });
    inv.push({ op: "remove_system", id: sysId });
    return { ops: ops, inv: inv, sys: { id: sysId, name: nm, charge: head.charge, multiplicity: head.multiplicity },
             parent: { id: parentId, members: P.members, kind: P.kind, stack: P.stack } };
  }

  /** Ask before a chain of mixed charge / multiplicity becomes one system.
   *  @param {any[]} calcs @param {any} head the calculation whose values the system takes
   *  @returns {Promise<boolean>} */
  function askMixedCharge(calcs, head) {
    const list = calcs.slice(0, 6).map(c => `${c.name}: charge ${int(c.charge)}, mult ${int(c.multiplicity) || 1}`).join("; ")
      + (calcs.length > 6 ? "; …" : "");
    return new Promise(resolve => {
      NB.modalRaw("Different charges or multiplicities",
        `<p>${esc(list)}.</p><p>The row this makes takes charge ${int(head.charge)} and multiplicity ${
          int(head.multiplicity) || 1} from ${esc(head.name)}, for the steps you add to it. Each calculation already in it keeps its own; a later change of the row's charge or multiplicity changes only those that have the row's.</p>`,
        [{ label: "Cancel", cls: "btn-ghost", act: () => resolve(false) },
         { label: "Make the row", cls: "btn-primary", focus: true, act: () => resolve(true) }]);
    });
  }

  /** Add the next step from a set, on a slot. @param {any} g the slot @param {any} set */
  async function addFromSet(g, set) {
    if (!g || !set) return;
    let P = g.parent;
    let pre = { ops: /** @type {any[]} */ ([]), inv: /** @type {any[]} */ ([]) };
    let sys = g.lane.sys;
    if (!sys) {
      const a = await adopt(P);
      if (!a) return;
      pre = a; sys = a.sys; P = a.parent;
    }
    // a level runs the natural next step (Opt to start a row, Freq after
    // Opt, TS Freq after OptTS, IRC after a TS Freq, SP after an IRC or
    // anything else); the block's Type changes it afterwards
    const kind = set.kind === "MLIP" ? "mlip_opt" : nextKind(P ? P.kind : "");
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
    const root = String(sys.name || "") + "-" + (SUFFIX[kind] || "step");
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
    await applySel(ops, inv, "the new " + (TYPE_LABEL[kind] || KIND_LABEL[kind] || kind), { t: "node", key: "s:" + sys.id + "/" + nodeId, m: 0 });
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
      if (!a || !(await apply(a.ops, a.inv, "making a system of " + P.members[0].name))) return;
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

  // ------------------------------------------------------------ workflows
  // A workflow is the steps of some blocks of ONE row without the row's
  // structure: per calculation its type, its level (set name + text, or the
  // Guided config), its gate, its upstream and geometry source inside the
  // copy, and its place relative to the copy's top-left block. A step whose
  // parent or geometry lies outside the copy takes the target's structure.
  // It lives here (and in the UI state, for the main window's drop); pasting
  // it is one apply_queue_ops batch, undone with Ctrl+Z like any other.

  /** "3 steps: OptTS → TS Freq → IRC" @param {any} wf */
  function wfSummary(wf) {
    if (!wf || !Array.isArray(wf.steps)) return "nothing";
    const seen = new Set(), kinds = [];
    wf.steps.forEach((/** @type {any} */ st) => {
      if (!seen.has(st.node)) { seen.add(st.node); kinds.push(KIND_LABEL[st.kind] || st.kind); }
    });
    return wf.steps.length + " step" + (wf.steps.length === 1 ? "" : "s") + ": " + kinds.join(" → ");
  }
  /** Where a paste puts the copied workflow now, in words. */
  function pasteTargetWords() {
    const n = selNode();
    if (n) return "after " + n.short;
    const L = _sel && _sel.t === "sys" ? laneByKey(_sel.key) : null;
    if (L) return "as the first steps of " + L.name;
    return "onto new rows, one per .xyz file you pick";
  }

  /** The full records copying needs (raw text, config), read for any
   *  calculation not read yet. @param {string[]} names */
  async function fullCalcs(names) {
    await Promise.all(names.filter(n => !_full.has(n)).map(async n => {
      const g = await NB.call("get_calc", n);
      const c = NB.queue.find((/** @type {any} */ x) => x.name === n);
      if (g && g.ok && g.calc) _full.set(n, { sig: c ? JSON.stringify(c) : "", calc: g.calc, levels: Array.isArray(g.levels) ? g.levels : [] });
    }));
  }

  /** The workflow of some blocks of one row, or why it cannot be copied.
   *  @param {any[]} picked @returns {Promise<{wf?: any, error?: string}>} */
  async function buildWorkflow(picked) {
    if (!picked.length) return { error: "Select a block first." };
    const L = picked[0].lane;
    const nodes = L.nodes.filter((/** @type {any} */ n) => picked.indexOf(n) >= 0);
    const calcs = nodes.flatMap((/** @type {any} */ n) => n.members);
    await fullCalcs(calcs.map((/** @type {any} */ c) => c.name));
    /** @type {Map<string, string>} */ const local = new Map();
    calcs.forEach((/** @type {any} */ c, /** @type {number} */ i) => local.set(c.name, "c" + i));
    const minC = Math.min.apply(null, nodes.map((/** @type {any} */ n) => n.col));
    const minR = Math.min.apply(null, nodes.map((/** @type {any} */ n) => n.row));
    const laneName = String(L.name || "");
    /** @type {any[]} */ const steps = [];
    /** @type {string[]} */ const problems = [];
    nodes.forEach((/** @type {any} */ n, /** @type {number} */ ni) => {
      const head = n.stack ? stem(n.members[0].name) : n.members[0].name;
      let suffix = lower(head).indexOf(lower(laneName) + "-") === 0 ? head.slice(laneName.length + 1) : "";
      if (!suffix) suffix = SUFFIX[n.kind] || "step";
      n.members.forEach((/** @type {any} */ c) => {
        const f = _full.get(c.name);
        const full = f ? f.calc : null;
        if (!full) { problems.push(c.name + " could not be read."); return; }
        const raw = String(full.raw_text || "");
        const isRaw = !!full.is_raw || (!!c.set_name && !!raw);
        if (isRaw && setKindOf(c.kind) !== "MLIP" && raw.indexOf(GEOM) < 0) {
          problems.push(c.name + "'s text carries its own coordinates (no " + GEOM + "), so it cannot run on another structure.");
        }
        const ref = full.geometry_source === "reference" && local.has(full.ref_name) ? local.get(full.ref_name) : "";
        steps.push({ id: local.get(c.name), node: "n" + ni, suffix: suffix,
                     tail: n.stack ? c.name.slice(head.length) : "",
                     kind: c.kind, is_raw: isRaw, raw_text: isRaw ? raw : "", set_name: c.set_name || "",
                     config: isRaw ? {} : full.config || {},
                     after: c.after && local.has(c.after) ? local.get(c.after) : "",
                     ref: ref, ref_point: ref ? String(full.ref_point || "") : "",
                     gate: c.gate === "approval" ? "approval" : "auto", auto_pass: c.auto_pass !== false,
                     col: n.col - minC, row: n.row - minR });
      });
    });
    if (problems.length) return { error: problems.join(" ") };
    // parents first, so every `after` names a calculation the batch already added
    /** @type {any[]} */ const ordered = [];
    const placed = new Set();
    for (let guard = 0; ordered.length < steps.length && guard <= steps.length; guard++) {
      steps.forEach(st => {
        if (placed.has(st.id)) return;
        if ((st.after && !placed.has(st.after)) || (st.ref && !placed.has(st.ref))) return;
        placed.add(st.id); ordered.push(st);
      });
    }
    steps.forEach(st => { if (!placed.has(st.id)) ordered.push(st); });
    return { wf: { v: 1, from: laneName, steps: ordered } };
  }

  /** Copy the workflow of some blocks of one row. @param {any[]} nodes */
  async function copyWorkflow(nodes) {
    const r = await buildWorkflow(nodes);
    if (!r.wf) { NB.fail("Could not copy the workflow: " + (r.error || "nothing selected.")); return; }
    _clip = r.wf;
    NB.call("set_ui_state", CLIP_KEY, JSON.stringify(_clip));
    _libSig = ""; _inspSig = "";
    NB.toast(`Copied ${wfSummary(_clip)}. Ctrl+V pastes it after a block, into an empty row, or onto new .xyz rows.`, "ok");
    render();
  }
  /** The workflow copied last, in this window or the other. */
  async function loadClip() {
    const r = await NB.call("get_ui_state", CLIP_KEY);
    const v = r && r.ok ? r.value : null;
    return v && typeof v === "object" && Array.isArray(v.steps) && v.steps.length ? v : null;
  }

  /** The add_calc ops that run a workflow in one row: names `<row>-<step>`,
   *  a name in use taking -2, -3 ... (every member of a stack the same
   *  number); roots after `t.parent` (geometry from it), else on `t.xyz`.
   *  @param {any} wf @param {{sysId: string, laneName: string, charge: number, mult: number,
   *    xyz: string, parent: string, col0: number, row0: number, taken: Set<string>}} t */
  function instantiate(wf, t) {
    /** @type {Map<string, any[]>} */ const byNode = new Map();
    wf.steps.forEach((/** @type {any} */ st) => {
      if (!byNode.has(st.node)) byNode.set(st.node, []);
      /** @type {any[]} */ (byNode.get(st.node)).push(st);
    });
    const nodeIds = new Map(), nameOf = new Map();
    byNode.forEach((members, nid) => {
      nodeIds.set(nid, newId("n"));
      const root = t.laneName + "-" + members[0].suffix;
      let base = root;
      for (let i = 2; members.some(m => t.taken.has(lower(base + m.tail))); i++) base = root + "-" + i;
      members.forEach(m => { nameOf.set(m.id, base + m.tail); t.taken.add(lower(base + m.tail)); });
    });
    /** @type {any[]} */ const ops = [];
    /** @type {string[]} */ const fresh = [];
    let first = "";
    wf.steps.forEach((/** @type {any} */ st) => {
      const name = nameOf.get(st.id);
      const after = st.after ? nameOf.get(st.after) || "" : t.parent;
      const ref = st.ref ? nameOf.get(st.ref) || "" : t.parent;
      const node = nodeIds.get(st.node);
      ops.push({ op: "add_calc", calc: {
        name: name, kind: st.kind, charge: t.charge, multiplicity: t.mult,
        geometry_source: ref ? "reference" : "direct", xyz: ref ? "" : t.xyz,
        ref_name: ref, ref_point: st.ref ? String(st.ref_point || "") : "",
        is_raw: !!st.is_raw, raw_text: st.is_raw ? String(st.raw_text || "") : "",
        set_name: String(st.set_name || ""), config: st.is_raw ? {} : st.config || {},
        system: t.sysId, node: node, col: t.col0 + int(st.col), row: t.row0 + int(st.row),
        after: after, gate: after ? st.gate || "auto" : "auto", auto_pass: st.auto_pass !== false,
        approved: false, state: "pending", message: "",
      } });
      fresh.push(name);
      if (!first) first = "s:" + t.sysId + "/" + node;
    });
    return { ops: ops, fresh: fresh, first: first };
  }

  /** Paste the copied workflow where the selection says: after the selected
   *  block, into the selected empty row, or -- nothing selected -- onto new
   *  rows from picked .xyz files. */
  async function paste() {
    if (!_clip) _clip = await loadClip();
    if (!_clip) { NB.toast("Nothing copied yet. Select blocks (drag a box on the empty canvas, or Ctrl+click) and press Ctrl+C."); return; }
    const n = selNode();
    if (n) { await pasteAfter(n, _clip); return; }
    const L = _sel && _sel.t === "sys" ? laneByKey(_sel.key) : null;
    if (L) { await pasteIntoSystem(L, _clip); return; }
    if (_sel) { NB.toast("Select one block to paste after, an empty row, or nothing (new rows from .xyz files)."); return; }
    const r = await NB.call("pick_xyz_files");
    if (!r || r.cancelled) return;
    if (r.ok !== true) { NB.fail(why(r, "Could not read the files")); return; }
    await pasteOnFiles(_clip, Array.isArray(r.files) ? r.files : [], "the pasted workflow");
  }

  /** @param {any} P @param {any} wf */
  async function pasteAfter(P, wf) {
    if (P.stack || P.kind === "irc") {
      NB.toast(`Paste after one calculation: ${P.short} ${P.stack ? "is " + P.members.length + " calculations"
        : "has two ends, forward and backward"}. Select a block after it.`);
      return;
    }
    let pre = { ops: /** @type {any[]} */ ([]), inv: /** @type {any[]} */ ([]) };
    let sys = P.lane.sys;
    if (!sys) { const a = await adopt(P); if (!a) return; pre = a; sys = a.sys; }
    // the next column, from the block's row down to the first rows where
    // every step's cell is free
    const occ = new Set(P.lane.nodes.map((/** @type {any} */ x) => x.col + "," + x.row));
    const col0 = P.col + 1;
    let row0 = P.row;
    while (wf.steps.some((/** @type {any} */ st) => occ.has((col0 + int(st.col)) + "," + (row0 + int(st.row))))) row0++;
    const r = instantiate(wf, { sysId: sys.id, laneName: String(sys.name || ""), charge: int(sys.charge),
      mult: int(sys.multiplicity) || 1, xyz: "", parent: P.members[0].name, col0: col0, row0: row0,
      taken: new Set(NB.queue.map((/** @type {any} */ c) => lower(c.name))) });
    const order = { op: "order", names: orderWith(NB.queue.map((/** @type {any} */ c) => c.name), P.lane, col0, row0, r.fresh) };
    const inv = r.fresh.slice().reverse().map(x => ({ op: "remove_calc", name: x })).concat(pre.inv);
    await applySel(pre.ops.concat(r.ops, [order]), inv, "the pasted workflow", { t: "node", key: r.first, m: 0 });
  }

  /** @param {any} L an empty system @param {any} wf */
  async function pasteIntoSystem(L, wf) {
    const sf = await systemFull(L.sys.id);
    const xyz = sf && sf.inputs && sf.inputs[0] ? String(sf.inputs[0].xyz || "") : "";
    const r = instantiate(wf, { sysId: L.sys.id, laneName: String(L.sys.name || ""), charge: int(L.sys.charge),
      mult: int(L.sys.multiplicity) || 1, xyz: xyz, parent: "", col0: 0, row0: 0,
      taken: new Set(NB.queue.map((/** @type {any} */ c) => lower(c.name))) });
    const order = { op: "order", names: NB.queue.map((/** @type {any} */ c) => c.name).concat(r.fresh) };
    await applySel(r.ops.concat([order]), r.fresh.slice().reverse().map(x => ({ op: "remove_calc", name: x })),
                   "the pasted workflow", { t: "node", key: r.first, m: 0 });
  }

  /** a name ORCA or a folder name refuses (store._validate_calc_name) */
  const HOSTILE = /[\\/:*?"<>|\s&,;=]/;

  /** One new row per .xyz file running the workflow, named after the file,
   *  in ONE batch. A file that cannot start a row (unreadable, several
   *  frames, a name ORCA refuses, a row of that name already there) is
   *  listed with its reason, never renamed to fit.
   *  @param {any} wf @param {any[]} files XyzFileEntry @param {string} label
   *  @returns {Promise<boolean>} whether any row was added */
  async function pasteOnFiles(wf, files, label) {
    /** @type {{file: string, reason: string}[]} */ const refused = [];
    /** @type {any[]} */ const ops = [];
    /** @type {string[]} */ const fresh = [], ids = [];
    const rows = new Set(NB.systems.map((/** @type {any} */ s) => lower(s.name)));
    const taken = new Set(NB.queue.map((/** @type {any} */ c) => lower(c.name)));
    let first = "";
    files.forEach(f => {
      const file = String(f.label || f.path || "");
      const nm = String(f.name || "");
      if (f.error) { refused.push({ file: file, reason: f.error }); return; }
      if (!nm) { refused.push({ file: file, reason: "The file has no name to give the row." }); return; }
      if (HOSTILE.test(nm)) {
        refused.push({ file: file, reason: "Its name has a space or one of & , ; = \\ / : * ? \" < > |, which ORCA or a folder name refuses. Rename the file." });
        return;
      }
      if (rows.has(lower(nm)) || taken.has(lower(nm))) { refused.push({ file: file, reason: `There is already a row or calculation named '${nm}'.` }); return; }
      rows.add(lower(nm));
      const id = newId("s");
      ops.push({ op: "add_system", system: { id: id, name: nm, charge: 0, multiplicity: 1,
                 inputs: [{ label: file || nm + ".xyz", xyz: String(f.xyz || "") }] } });
      const r = instantiate(wf, { sysId: id, laneName: nm, charge: 0, mult: 1, xyz: String(f.xyz || ""),
                                  parent: "", col0: 0, row0: 0, taken: taken });
      ops.push(...r.ops);
      fresh.push(...r.fresh);
      ids.push(id);
      if (!first) first = r.first;
    });
    let ok = false;
    if (fresh.length) {
      ops.push({ op: "order", names: NB.queue.map((/** @type {any} */ c) => c.name).concat(fresh) });
      const inv = /** @type {any[]} */ (fresh.slice().reverse().map(x => ({ op: "remove_calc", name: x })))
        .concat(ids.map(id => ({ op: "remove_system", id: id })));
      ok = await applySel(ops, inv, label, { t: "node", key: first, m: 0 });
      if (ok) NB.toast(`${ids.length} row${ids.length === 1 ? "" : "s"} added, each running ${wfSummary(wf)}.`, "ok");
    }
    if (refused.length) {
      NB.modalRaw(`${refused.length} file${refused.length === 1 ? " was" : "s were"} not added`,
        `<div class="names">${refused.map(x =>
          `${esc(String(x.file).split(/[\\/]/).pop())} — ${esc(x.reason)}`).join("<br>")}</div>`);
    }
    return ok;
  }

  /** Several blocks of one row by Ctrl/Shift+click: add or drop one.
   *  @param {string} key */
  function toggleSelected(key) {
    const n = nodeByKey(key);
    if (!n) return;
    const cur = selNodes();
    if (cur.length && cur[0].lane !== n.lane) {
      NB.toast(`A workflow is copied from one row: ${n.short} is in another row than the selected blocks.`);
      return;
    }
    const keys = cur.map((/** @type {any} */ x) => x.key);
    const i = keys.indexOf(key);
    if (i >= 0) keys.splice(i, 1); else keys.push(key);
    _expanded = "";
    select(keys.length > 1 ? { t: "multi", lane: n.lane.key, keys: keys, ignored: 0 }
      : keys.length ? { t: "node", key: keys[0], m: 0 } : null);
  }

  /** The blocks a rubber band touches, by row: the selection takes the first
   *  row it reaches (top first); the others are counted, not selected.
   *  @param {any} b the band @returns {{lane: string, keys: string[], ignored: number}} */
  function bandHits(b) {
    const g = NB.$("qe-graph");
    const l = Math.min(b.x0, b.x1), r = Math.max(b.x0, b.x1), t = Math.min(b.y0, b.y1), btm = Math.max(b.y0, b.y1);
    const hits = (g._rects || []).filter((/** @type {any} */ x) => x.x < r && x.x + W > l && x.y < btm && x.y + H > t);
    const lane = hits.length ? hits[0].lane : "";
    const keys = hits.filter((/** @type {any} */ x) => x.lane === lane).map((/** @type {any} */ x) => x.key);
    return { lane: lane, keys: keys, ignored: hits.length - keys.length };
  }
  function markBand() {
    const h = bandHits(_band);
    NB.$("qe-graph").querySelectorAll(".qe-node").forEach((/** @type {HTMLElement} */ nd) =>
      nd.toggleAttribute("data-band", h.keys.indexOf(nd.getAttribute("data-k") || "") >= 0));
  }
  function endBand() {
    const b = _band;
    _band = null;
    if (!b || !b.el) return;            // a click, not a band: the background clears
    b.el.remove();
    _bandEnded = true;
    setTimeout(() => { _bandEnded = false; }, 0);
    const h = bandHits(b);
    let keys = h.keys;
    if (b.add && _sel && _sel.t === "multi" && _sel.lane === h.lane) keys = _sel.keys.concat(keys.filter(k => _sel.keys.indexOf(k) < 0));
    else if (b.add && _sel && _sel.t === "node" && keys.length) {
      const n0 = nodeByKey(_sel.key);
      if (n0 && n0.lane.key === h.lane && keys.indexOf(n0.key) < 0) keys = [n0.key].concat(keys);
    }
    _expanded = "";
    if (h.ignored) NB.toast(`${h.ignored} block${h.ignored === 1 ? " in another row was" : "s in other rows were"} left out: a selection is one row.`);
    select(keys.length > 1 ? { t: "multi", lane: h.lane, keys: keys, ignored: h.ignored }
      : keys.length ? { t: "node", key: keys[0], m: 0 } : null);
  }

  // ---- lanes: move, copy

  /** @param {any} L */
  const laneNames = (L) => L.calcs.map((/** @type {any} */ c) => c.name);

  /** Move a lane to before lane slot `to` (0 .. lanes). @param {string} key @param {number} to */
  function moveLane(key, to) {
    const lanes = _M.lanes.filter((/** @type {any} */ L) => L.calcs.length);
    const from = lanes.findIndex((/** @type {any} */ L) => L.key === key);
    if (from < 0 || to === from || to === from + 1) return Promise.resolve(false);
    const order = lanes.slice();
    const [mv] = order.splice(from, 1);
    order.splice(to > from ? to - 1 : to, 0, mv);
    const before = NB.queue.map((/** @type {any} */ c) => c.name);
    const names = order.flatMap(laneNames);
    return apply([{ op: "order", names: names }], [{ op: "order", names: before }], "the move");
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

  /** Dropped structures: one row each, running the drop recipe -- an ORCA
   *  level as a single point (add_xyz_systems), or a whole workflow (the
   *  copied one, or a row's) through one apply_queue_ops batch. Refused
   *  files are listed with the reason, never silently fixed. Runs in both
   *  windows (the main window hands its drops here).
   *  @param {string[]} paths @returns {Promise<boolean>} whether any was queued */
  async function dropXyz(paths) {
    const list = (paths || []).filter(p => /\.xyz$/i.test(String(p)));
    if (!list.length) return false;
    await loadSets();                   // the library may have changed in the other window
    const rec = await recipe();
    if (rec.run !== "set") {
      let wf = null;
      if (rec.run === "clip") wf = (await loadClip()) || _clip;
      else {
        _M = model();
        const L = laneByKey(rec.row);
        if (L) {
          const b = await buildWorkflow(L.nodes);
          if (!b.wf) { NB.fail("Could not run that row's workflow: " + (b.error || "it has no steps.")); return false; }
          wf = b.wf;
        }
      }
      if (!wf) {
        NB.fail(rec.run === "clip"
          ? "Nothing is copied: copy blocks in the queue window (Ctrl+C), or choose another run for dropped files."
          : "The row whose workflow dropped files run is gone. Choose another under the inp sets.");
        return false;
      }
      const rr = await NB.call("read_xyz_paths", JSON.stringify(list));
      if (!rr || rr.ok !== true) { NB.fail(why(rr, "Could not read the structures")); return false; }
      return pasteOnFiles(wf, Array.isArray(rr.files) ? rr.files : [], "the dropped structures");
    }
    if (!rec.set_id) { NB.fail("There is no ORCA level to run a dropped structure with."); return false; }
    const r = await NB.call("add_xyz_systems", JSON.stringify({ paths: list, set_id: rec.set_id, suffix: rec.suffix, kind: "sp" }));
    if (!r || r.ok !== true) { NB.fail(why(r, "Could not queue the structures")); return false; }
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
    const kind = jobKindOf(text) || "general";
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
      if (a === "clear") { if (_bandEnded) return; _expanded = ""; select(null); }
      else if (a === "node" && (ev.ctrlKey || ev.metaKey || ev.shiftKey)) toggleSelected(k);
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
    // a rubber band, started on the empty canvas only (never on a grip or
    // a set, whose drags are the browser's): the blocks it touches in one
    // row become the selection; Ctrl/Shift adds to it
    g.addEventListener("mousedown", (/** @type {MouseEvent} */ ev) => {
      const t = /** @type {HTMLElement} */ (ev.target);
      if (ev.button !== 0 || !t.classList || !t.classList.contains("qe-bg")) return;
      ev.preventDefault();
      const box = g.getBoundingClientRect();
      _band = { x0: ev.clientX - box.left, y0: ev.clientY - box.top, x1: 0, y1: 0, el: null,
                add: ev.ctrlKey || ev.metaKey || ev.shiftKey };
      _band.x1 = _band.x0; _band.y1 = _band.y0;
      const move = (/** @type {MouseEvent} */ e) => {
        if (!_band) return;
        const bb = g.getBoundingClientRect();
        _band.x1 = e.clientX - bb.left; _band.y1 = e.clientY - bb.top;
        if (!_band.el && Math.abs(_band.x1 - _band.x0) + Math.abs(_band.y1 - _band.y0) < 6) return;
        if (!_band.el) { _band.el = NB.el("div", "qe-band"); g.appendChild(_band.el); }
        const st = _band.el.style;
        st.left = Math.min(_band.x0, _band.x1) + "px"; st.top = Math.min(_band.y0, _band.y1) + "px";
        st.width = Math.abs(_band.x1 - _band.x0) + "px"; st.height = Math.abs(_band.y1 - _band.y0) + "px";
        markBand();
      };
      const up = () => {
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
        endBand();
      };
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    });
    g.addEventListener("keydown", (/** @type {KeyboardEvent} */ ev) => {
      const b = /** @type {HTMLElement} */ (ev.target).closest('[data-a="grip"]');
      if (!b || !ev.altKey || (ev.key !== "ArrowUp" && ev.key !== "ArrowDown")) return;
      ev.preventDefault();
      const lanes = _M.lanes.filter((/** @type {any} */ x) => x.calcs.length);
      const key = b.getAttribute("data-lane");
      const i = lanes.findIndex((/** @type {any} */ L) => L.key === key);
      const moved = ev.key === "ArrowUp" && i > 0 ? moveLane(key, i - 1)
        : ev.key === "ArrowDown" && i >= 0 && i < lanes.length - 1 ? moveLane(key, i + 2) : null;
      // the canvas is redrawn by the move: the keyboard stays on the row's
      // grip, so the next Alt+Arrow moves it again
      if (moved) moved.then(() => {
        const again = /** @type {HTMLElement|null} */ (g.querySelector(`[data-a="grip"][data-lane="${CSS.escape(String(key))}"]`));
        if (again) again.focus({ preventScroll: false });
      });
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
      // a slot the Guided builder has claimed takes no set: two blocks would share it
      if (gh.hasAttribute("data-picking")) return null;
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
      if (!gh.hasAttribute("data-picking")) gh.setAttribute("data-drop", "1");
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
      if ((ev.ctrlKey || ev.metaKey) && !ev.shiftKey && !ev.altKey && ev.key.toLowerCase() === "c") {
        // the focused grip copies its whole row; else what is selected
        const grip = t && t.closest ? t.closest('[data-a="grip"]') : null;
        const GL = grip ? laneByKey(grip.getAttribute("data-lane") || "") : null;
        const nodes = GL ? GL.nodes : selNodes();
        if (nodes.length) { ev.preventDefault(); copyWorkflow(nodes); }
        return;
      }
      if ((ev.ctrlKey || ev.metaKey) && !ev.shiftKey && !ev.altKey && ev.key.toLowerCase() === "v") {
        ev.preventDefault(); paste(); return;
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
      if (g && g.ok && g.calc) _full.set(x.name, { sig: x.sig, calc: g.calc, levels: Array.isArray(g.levels) ? g.levels : [] });
    }));
    // the guided ends of an Opt/OptTS -> Freq connection: their input, for
    // the level comparison (a template's own text is compared directly)
    const byName = new Map(NB.queue.map((/** @type {any} */ c) => [c.name, c]));
    const ends = new Set();
    NB.queue.forEach((/** @type {any} */ c) => {
      const p = c.after ? byName.get(c.after) : null;
      if (!p || !isFreqAfterOpt(p.kind, c.kind)) return;
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
            <label class="qe-rlab">Run <select class="qe-select" id="qe-rset" aria-label="What a dropped structure runs: an ORCA level as a single point, or a workflow"></select></label>
            <label class="qe-rlab">name + <input class="qe-input mono" id="qe-rsfx" spellcheck="false"
              aria-label="Suffix of a dropped structure's calculation name"></label>
            <p class="hint qe-pad qe-drophint" id="qe-rhint">Drop .xyz files anywhere on this window: each becomes a row that runs this single point.</p>
          </div>
          <div class="qe-dragimgs" id="qe-dragimgs" aria-hidden="true"></div>
        </aside>
        <main class="qe-main" aria-label="Queue">
          <div class="qe-bar">
            <span class="qe-barsp"></span>
            <button class="btn btn-ghost qe-tbtn" type="button" id="qe-paste" disabled
              title="Copy blocks first: select them (drag a box on the empty canvas, or Ctrl+click) and press Ctrl+C">Paste workflow</button>
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
    NB.$("qe-paste").addEventListener("click", () => paste());
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
      // the settings (theme included) may have been saved in the main window
      // meanwhile; boot.js's own visibility handler asks too, and
      // refreshSettings shares one request between them
      await NB.refreshSettings();
      takeHandOff();
    });
    await loadSets();
    _clip = await loadClip();
    sync();
  }

  NB.qed = { init: init, render: render, dropXyz: dropXyz, dropInp: dropInp,
             /** whether the inp-set editor has edits not saved (boot.js asks before a reload) */
             isDirty: () => edDirty() };
})();
