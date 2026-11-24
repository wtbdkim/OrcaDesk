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
            several sharing a node id (an SP after an IRC is _F and _B)
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
  // a stack's back cards, 5px apart: at most 3 (15px of the 36px column gap)
  const MAX_BACKS = 3;
  /** @param {number} c */
  const X = (c) => X0 + c * COLW;

  /** what a set may be: an ORCA level of theory, MLIP settings, or CREST
   *  settings (a conformer search) */
  const SET_KINDS = ["ORCA", "MLIP", "CREST"];
  const SET_GROUP = { ORCA: "ORCA levels", MLIP: "MLIP", CREST: "CREST" };
  /** the set kinds whose text is `key value` settings, not an ORCA input:
   *  no {{GEOMETRY}} / {{JOB}}; the store reads the fields from the lines */
  const SETTINGS_KINDS = ["MLIP", "CREST"];
  /** @param {string} k */
  const isSettings = (k) => SETTINGS_KINDS.indexOf(k) >= 0;
  const EDITABLE = ["pending", "blocked", "cancelled"];
  const STARTED = ["running", "done", "failed"];
  /** the name every new step of a kind gets: the system's, "_", then its job
   *  as ORCA's job keyword ({{JOB}}) reads it -- Ltyr-MV_Opt, Ltyr-MV_IRC */
  const SUFFIX = { mlip_opt: "Preopt", mlip_sp: "MLSP", mlip_freq: "MLFreq", mlip_opt_freq: "MLOptFreq",
                   opt: "Opt", ts_opt: "OptTS", freq: "Freq", ts_freq: "FreqTS",
                   opt_freq: "OptFreq", ts_opt_freq: "OptTSFreq", irc: "IRC", sp: "SP", nmr: "NMR",
                   tddft: "TDDFT", neb_ts: "NEB-TS", crest_conf: "CREST" };
  /** what joins the parts of a name the editor makes: system_Job_F */
  const SEP = "_";
  /** what a calculation of each kind is called on its block */
  const KIND_LABEL = {
    mlip_opt: "Preopt", mlip_sp: "MLIP SP", mlip_freq: "MLIP Freq", mlip_opt_freq: "MLIP Opt+Freq",
    opt: "Opt", ts_opt: "OptTS", freq: "Freq", ts_freq: "TS Freq", irc: "IRC", sp: "SP",
    opt_freq: "Opt+Freq", ts_opt_freq: "OptTS+Freq", neb_ts: "NEB-TS", tddft: "TD-DFT",
    nmr: "NMR", general: "General", crest_conf: "Conformers",
  };
  /** The Type select of a level's block, in the Guided builder's words
   *  (build.js KINDS[].label) and its order. Only the kinds the backend can
   *  fill {{JOB}} for are offered (get_inp_sets `jobs`). */
  const TYPE_LABEL = { opt: "Optimization", ts_opt: "TS optimization", freq: "Frequencies",
                       ts_freq: "TS frequencies", opt_freq: "Opt + Freq", ts_opt_freq: "TS Opt + Freq",
                       irc: "IRC", sp: "Single point", nmr: "NMR", tddft: "TD-DFT", neb_ts: "NEB-TS" };
  const TYPE_ORDER = ["opt", "ts_opt", "freq", "ts_freq", "opt_freq", "ts_opt_freq", "irc", "sp", "nmr",
                      "tddft", "neb_ts"];
  /** The Task select of an MLIP set's block, in the Guided builder's words */
  const MLIP_TYPES = [["mlip_opt", "Optimization"], ["mlip_sp", "Single point"],
                      ["mlip_freq", "Frequencies"], ["mlip_opt_freq", "Opt + Frequencies"]];
  /** the states "Clear finished" takes off the list (a cancelled block
   *  re-runs); a filtered one is as final as a done one, with no output */
  const FINISHED = ["done", "failed", "filtered"];
  /** the kinds whose approval gate can pass by itself on the imaginary-mode count */
  const FREQ_KINDS = ["freq", "ts_freq", "opt_freq", "ts_opt_freq"];
  const ST_LABEL = { done: "done", running: "running", waiting: "waiting for approval",
                     pending: "pending", failed: "failed", blocked: "blocked", cancelled: "cancelled",
                     filtered: "filtered", disconnected: "disconnected" };
  /** the graph fields: where a block is drawn and what its incoming edge
   *  carries (its gate, and the energy condition filter_de / filter_top) */
  const PLACE_KEYS = ["system", "node", "col", "row", "after", "gate", "auto_pass",
                      "approved", "ref_point", "set_name", "filter_de", "filter_top"];
  /** the tail of a conformer stack's members: system_SP_c3 runs on conformer 3 */
  const CONF_TAIL = "c";
  /** a ref_point naming a conformer of a CREST ensemble: "conf:3" @param {string} p */
  const confOf = (p) => { const m = /^conf:(\d+)$/.exec(String(p || "")); return m ? int(m[1]) : 0; };
  /** the ref_point of a conformer PLACEHOLDER: one step that stands for every
   *  conformer a CREST search that has not run will find; the engine replaces
   *  it (and what hangs off it) by one copy per conformer once the search is
   *  done (store expand_conformer_placeholders) */
  const CONF_ALL = "conf:all";
  /** @param {any} c a calculation @returns {boolean} whether it is a placeholder */
  const isConfAll = (c) => !!c && c.ref_point === CONF_ALL && c.geometry_source === "reference";
  /** a ref_point in words: "forward end", "conformer 3" ("" for the final
   *  geometry) @param {string} p */
  const pointWords = (p) => p === "irc_f" ? "forward end" : p === "irc_b" ? "backward end"
    : p === CONF_ALL ? "every conformer" : confOf(p) ? "conformer " + confOf(p) : String(p || "");
  /** whether a name in `taken` (lower case) is `base` with a conformer tail
   *  (base_c3): a placeholder named `base` would have its copies collide
   *  with it @param {Set<string>} taken @param {string} base */
  const confTailTaken = (taken, base) => {
    const head = lower(base) + SEP + CONF_TAIL;
    for (const x of taken) if (x.indexOf(head) === 0 && /^\d+$/.test(x.slice(head.length))) return true;
    return false;
  };
  const GEOM = "{{GEOMETRY}}";
  const JOB = "{{JOB}}";
  /** the copied workflow, mirrored in the UI state so it outlives a reload
   *  of the queue window */
  const CLIP_KEY = "queue.workflow";
  /** the ORCA level the + slot's add menu offers when the block before has none */
  const LEVEL_KEY = "queue.level";
  const SLOTS_NEEDED = ["apply_queue_ops", "get_inp_sets", "save_inp_set", "get_system",
                        "open_queue_window", "pick_xyz_files", "read_xyz_paths",
                        "take_ui_state", "get_workflows", "save_workflow", "delete_workflow",
                        "get_irc_structures", "get_structure_frames"];

  // ------------------------------------------------------------ backend mirrors
  /** the inp-set library (get_inp_sets) @type {InpSet[]} */
  let _sets = [];
  /** each set kind's seeded text, what "+ New" starts from @type {Object<string, string>} */
  let _defaults = {};
  /** calc kind -> what {{JOB}} becomes (the kinds a level's block may run)
   *  @type {Object<string, string>} */
  let _jobs = {};
  /** calc kind -> the kind a level placed after it runs by default @type {Object<string, string>} */
  let _next = {};
  /** the kinds a block made from an MLIP set may run (get_inp_sets `mlip_jobs`) @type {string[]} */
  let _mlipJobs = [];
  /** the kinds a block made from a CREST set runs (get_inp_sets `crest_jobs`) @type {string[]} */
  let _crestJobs = [];
  /** The last refusal of an ops batch, said in the inspector beside what it
   *  names (the error starts "'<name>': ...") as well as in a toast.
   *  @type {{text: string, name: string}|null} */
  let _refusal = null;
  /** The copied workflow (copyWorkflow), editor-local; mirrored in the UI
   *  state under CLIP_KEY so it outlives a reload. @type {any} */
  let _clip = null;
  /** the saved workflows (get_workflows), the library's Workflows group
   *  @type {any[]} */
  let _wfs = [];
  /** the id of the ORCA level last used from the add menu (LEVEL_KEY) */
  let _lastLevel = "";
  /** the + slot's add menu while it is open: the slot's key and the element
   *  @type {{key: string, el: HTMLElement}|null} */
  let _menu = null;
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
  /** StepConfig's defaults (get_step_defaults): what a block's options are
   *  compared with when its sub-label names them @type {Object<string, any>} */
  let _stepDef = {};
  /** the stage of the system panel's structure view, destroyed on repaint @type {any} */
  let _sysStage = null;

  // ------------------------------------------------------------ visual state
  /** what is selected: a node (and which member of a stack; `all` when a
   *  drag selected it whole), an edge, an empty system's lane, or several
   *  blocks of one row ({t:"multi", lane, keys, ignored}) @type {any} */
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
  /** the builder hosted over the lanes (#qe-build): which open of it the element is */
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
    if (!r || r.ok !== true) {
      const text = why(r, "The queue refused that change");
      NB.fail(text);
      // said in the inspector too, beside the block it names
      const m = /^'([^']+)':/.exec(String((r && r.error) || ""));
      _refusal = { text: text, name: m ? m[1] : "" };
      const box = /** @type {HTMLElement|null} */ (document.querySelector('#qe-insp [data-f="refusal"]'));
      if (box) { box.textContent = text; box.hidden = false; }
      return false;
    }
    _refusal = null;
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
              (c.state === "running" ? "started running" : c.state === "filtered" ? "been filtered out"
                : "already run (" + c.state + ")") +
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
      if ("raw_text" in patch && /^(mlip|crest)/.test(String(was.kind || "")) && was.config && !("config" in patch)) {
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
    _sets = r && r.ok && Array.isArray(r.sets) ? r.sets : [];
    _defaults = r && r.ok && r.defaults && typeof r.defaults === "object" ? r.defaults : {};
    _jobs = r && r.ok && r.jobs && typeof r.jobs === "object" ? r.jobs : {};
    _next = r && r.ok && r.next_kind && typeof r.next_kind === "object" ? r.next_kind : {};
    _mlipJobs = r && r.ok && Array.isArray(r.mlip_jobs) ? r.mlip_jobs.map(String) : [];
    _crestJobs = r && r.ok && Array.isArray(r.crest_jobs) ? r.crest_jobs.map(String) : [];
    return _sets;
  }
  async function loadWorkflows() {
    const r = await NB.call("get_workflows");
    _wfs = r && r.ok && Array.isArray(r.workflows) ? r.workflows : [];
    _libSig = ""; _inspSig = "";
    return _wfs;
  }
  /** A saved workflow as the editor pastes it. @param {any} w */
  const wfOf = (w) => ({ v: 1, from: String(w.from || ""), steps: w.steps });
  /** @param {string} name @param {string} kind */
  const setByName = (name, kind) =>
    _sets.find(s => s.name === name && (!kind || s.kind === kind)) || null;

  /** The kinds a level's block may run (the Type select), in the builder's
   *  order: those the backend fills {{JOB}} for. */
  const typeKinds = () => TYPE_ORDER.filter(k => k in _jobs);
  /** The set kind a calculation of `kind` is made from: "MLIP" for an MLIP
   *  pre-optimization, "CREST" for a conformer search, "ORCA" for a kind a
   *  level can run, "" otherwise. @param {string} kind */
  const setKindOf = (kind) => (/^mlip_/.test(String(kind || "")) ? "MLIP"
    : /^crest/.test(String(kind || "")) ? "CREST"
    : (Object.keys(_jobs).length ? kind in _jobs : TYPE_ORDER.indexOf(kind) >= 0) ? "ORCA" : "");
  /** The kind a block made from a settings set runs: an MLIP
   *  pre-optimization, a CREST conformer search; "" for a level (its Type
   *  says). @param {string} setKind */
  const settingsKind = (setKind) => setKind === "MLIP" ? "mlip_opt"
    : setKind === "CREST" ? (_crestJobs[0] || "crest_conf") : "";
  /** The natural next step after a block of `kind` ("" = a row's start):
   *  the backend's rule (inpsets.NEXT_KIND). @param {string} kind */
  const nextKind = (kind) => _next[kind || ""] || (kind ? "sp" : "opt");
  /** A frequency step after the optimization it checks: the pair whose
   *  levels have to agree. @param {string} a @param {string} b */
  const isFreqAfterOpt = (a, b) => (a === "opt" || a === "ts_opt") && (b === "freq" || b === "ts_freq");

  /** What a set's text is: MLIP settings (`key value` lines naming a
   *  backend or model, no ! line), CREST settings (`key value` lines naming
   *  the search -- method, ewin, preset, nci ... -- and no line an ORCA input
   *  starts with !, % or *), or an ORCA input. MLIP is asked first: none of
   *  its keys is a CREST setting, while `env` is both's.
   *  @param {string} text */
  function detectKind(text) {
    const t = String(text || "");
    if (/^\s*!/m.test(t)) return "ORCA";
    if (/^\s*(backend|model|mlip|device|nprocs)\b/im.test(t)) return "MLIP";
    if (/^\s*(method|ewin|preset|nci|solvent_model|solvent|threads)\b/im.test(t) && !/^\s*[%*]/m.test(t)) return "CREST";
    return "ORCA";
  }
  /** The model an MLIP set's text names, read as the store reads it
   *  (state/inpsets.py mlip_config_from_text: `key value`, # comments, the
   *  last `model` line wins), or "" when it names none. @param {string} text */
  function mlipModelOf(text) {
    let model = "";
    String(text || "").split("\n").forEach(raw => {
      const line = raw.split("#")[0].trim();
      const sp = line.indexOf(" ");
      if (sp > 0 && line.slice(0, sp).toLowerCase() === "model") model = line.slice(sp + 1).trim();
    });
    return model;
  }
  /** an ORCA job keyword, the vocabulary a ! line's job is read in */
  const JOB_WORD = /^(OPT|TIGHTOPT|LOOSEOPT|VERYTIGHTOPT|COPT|ZOPT|OPTTS|SCANTS|FREQ|NUMFREQ|ANFREQ|IRC|SP)$/i;
  /** The calculation kind an input's own job line says (a dropped or
   *  imported .inp), "" when it is not an ORCA input. @param {string} text */
  function jobKindOf(text) {
    if (isSettings(detectKind(text))) return "";
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

  /** Whether a calculation is a level's block: an ORCA text (from a set, or
   *  pasted) with {{JOB}} on its ! line. Only such a block takes options of
   *  its own (calc.config, merged into the level at launch); a text that
   *  names its own job runs as written, and goes to the builder as .inp text.
   *  @param {any} c */
  function isLevelCalc(c) {
    if (!c || isSettings(setKindOf(c.kind)) || (!c.set_name && !c.is_raw)) return false;
    return hasJobLine(templateOf(c));
  }

  /** The options a level's block adds to its level, in a few words each,
   *  against StepConfig's defaults ("2 constraints", "MaxIter 300", "IRC
   *  forward" ...): its sub-label and its inspector say them.
   *  @param {string} kind @param {any} cfg @returns {string[]} */
  function optWords(kind, cfg) {
    const c = cfg || {}, d = _stepDef || {};
    const out = [];
    const n = (/** @type {any} */ x) => Number(x);
    const differs = (/** @type {string} */ k) => c[k] != null && c[k] !== "" && d[k] != null && n(c[k]) !== n(d[k]);
    const cons = Array.isArray(c.geom_constraints) ? c.geom_constraints.length : 0;
    if (cons) out.push(cons + " constraint" + (cons === 1 ? "" : "s"));
    if (Array.isArray(c.geom_scan) && c.geom_scan.length) out.push("scan");
    if (/opt/.test(kind) && differs("max_iter")) out.push("MaxIter " + c.max_iter);
    if (/freq/.test(kind) && (differs("freq_temp_k") || differs("freq_pressure_atm"))) {
      out.push(c.freq_temp_k + " K, " + c.freq_pressure_atm + " atm");
    }
    if (kind === "irc") {
      if (c.irc_direction && c.irc_direction !== (d.irc_direction || "both")) out.push("IRC " + c.irc_direction);
      if (differs("irc_maxiter")) out.push(c.irc_maxiter + " points");
      if (c.irc_init_hess && c.irc_init_hess !== "auto") out.push("Hessian " + c.irc_init_hess);
    }
    if (kind === "nmr" && c.nmr_jcoupling) out.push("J coupling");
    if (kind === "tddft" && differs("tddft_nroots")) out.push(c.tddft_nroots + " roots");
    if (kind === "neb_ts" && differs("neb_nimages")) out.push(c.neb_nimages + " images");
    if (c.solvation && c.solvation.model) out.push(c.solvation.model + (c.solvation.solvent ? " " + c.solvation.solvent : ""));
    if (c.scf_convergence) out.push(c.scf_convergence);
    if (String(c.options || "").trim()) out.push(String(c.options).trim());
    return out;
  }

  /** How many atoms an .xyz text holds (its header, if any, left out).
   *  @param {string} xyz */
  const xyzAtoms = (xyz) => (typeof MOL !== "undefined" ? MOL.parseXyz(xyz).length
    : String(xyz || "").split("\n").filter(l => /^\s*[A-Za-z]{1,3}\s+[-+.\d]/.test(l)).length);
  /** The atom count of the structure a calculation runs on, followed up its
   *  geometry references to the coordinates they start from; 0 when that is
   *  not known here. @param {string} name */
  function atomsOfCalc(name) {
    const seen = new Set();
    for (let nm = name; nm && !seen.has(nm); ) {
      seen.add(nm);
      const f = _full.get(nm);
      if (!f || !f.calc) return 0;
      if (f.calc.geometry_source !== "reference") return xyzAtoms(String(f.calc.xyz || ""));
      nm = String(f.calc.ref_name || "");
    }
    return 0;
  }
  /** The atoms a workflow's constraints and scans name: a structure needs
   *  at least this many (0: none named). @param {any} wf */
  function wfAtomNeed(wf) {
    let need = 0;
    (wf && wf.steps || []).forEach((/** @type {any} */ st) => {
      const cfg = st.config || {};
      [].concat(cfg.geom_constraints || [], cfg.geom_scan || []).forEach((/** @type {any} */ c) => {
        (c && c.atoms || []).forEach((/** @type {number} */ a) => { need = Math.max(need, int(a) + 1); });
      });
    });
    return need;
  }

  // ---- the workspace: what a new name would overwrite

  /** The names among `names` whose workspace folder already holds output
   *  (existing_outputs; an older backend is asked one name at a time).
   *  @param {string[]} names @returns {Promise<string[]>} */
  async function existingOutputs(names) {
    const list = [...new Set((names || []).filter(Boolean))];
    if (!list.length) return [];
    if (NB.bridge && typeof NB.bridge.existing_outputs === "function") {
      const r = await NB.call("existing_outputs", JSON.stringify(list));
      if (r && r.ok && Array.isArray(r.existing)) return r.existing.map(String);
    }
    const hits = [];
    for (const n of list) {
      const r = await NB.call("has_existing_output", n);
      if (r && r.ok && r.exists) hits.push(n);
    }
    return hits;
  }
  /** Names for something new that avoid folders already holding output:
   *  `make(extra)` names it with `extra` (lower-cased names) taken as well,
   *  and is asked again with the names found on disk, a few times. What is
   *  left is the overwrite guard's to ask about.
   *  @template {{names: string[]}} T @param {(extra: Set<string>) => T} make @returns {Promise<T>} */
  async function avoidDisk(make) {
    const extra = new Set();
    let r = make(extra);
    for (let i = 0; i < 4; i++) {
      const hits = await existingOutputs(r.names);
      if (!hits.length) return r;
      hits.forEach(h => extra.add(lower(h)));
      r = make(extra);
    }
    return r;
  }
  /** While a run is going, an add starts at once -- no Run click screens it
   *  for results already on disk -- so any of `names` whose folder holds
   *  output is asked about first, as the Guided builder's add is
   *  (confirmOverwrite). Idle, the Run click asks. @param {string[]} names
   *  @returns {Promise<boolean>} whether to go ahead */
  async function guardOverwrite(names) {
    if (!NB.running || !names.length) return true;
    const hits = await existingOutputs(names);
    if (!hits.length) return true;
    const k = hits.length;
    return new Promise(res => {
      NB.modalRaw("Existing output",
        `<p>The workspace already holds output for ${k === 1 ? "this name" : k + " of these names"}. The queue is
          running, so ${k === 1 ? "it starts" : "they start"} at once and overwrite${k === 1 ? "s" : ""} it:</p>
         <div class="names">${NB.nameList(hits.map(esc))}</div>`,
        [{ label: "Cancel", cls: "btn-ghost", focus: true, act: () => res(false) },
         { label: "Overwrite", cls: "btn-danger", act: () => res(true) }]);
    });
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
      // what each edge holds (the members of B hanging off A) and the energy
      // condition they carry
      L.edges.forEach((/** @type {any} */ e) => {
        e.gated = e.to.members.filter((/** @type {any} */ c) => e.from.members.some((/** @type {any} */ a) => a.name === upOf(c)));
        e.cond = condOf(e.gated);
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
  /** a number as a condition says it: 3, 2.5, 0.25 @param {number} v */
  const condNum = (v) => String(Math.round(v * 100) / 100);
  /** The energy condition on the edge into some calculations (filter_de,
   *  filter_top: graph fields describing the edge INTO each, like its gate),
   *  read off the first that has one; `mixed` when they do not all agree.
   *  @param {any[]} members @returns {{de: number|null, top: number, mixed: boolean}} */
  function condOf(members) {
    const one = (/** @type {any} */ c) => ({
      de: c.filter_de == null || c.filter_de === "" || !isFinite(Number(c.filter_de)) ? null : Number(c.filter_de),
      top: Math.max(0, int(c.filter_top)) });
    const all = members.map(one);
    const k = all.find(x => x.de !== null || x.top > 0) || all[0] || { de: null, top: 0 };
    return { de: k.de, top: k.top, mixed: all.some(x => x.de !== k.de || x.top !== k.top) };
  }
  /** "ΔE ≤ 3", "top 5", "ΔE ≤ 3 · top 5" ("" for none; "*" when the
   *  members do not all carry it) @param {{de: number|null, top: number, mixed: boolean}} k */
  function condWords(k) {
    const w = [];
    if (k.de !== null) w.push("ΔE ≤ " + condNum(k.de));
    if (k.top > 0) w.push("top " + k.top);
    return w.length ? w.join(" · ") + (k.mixed ? " *" : "") : "";
  }
  /** A name without its last "_part" (or "-part", as older names were made):
   *  the stack Ltyr-MV_SP of Ltyr-MV_SP_F. @param {string} s */
  function stem(s) { const i = Math.max(s.lastIndexOf(SEP), s.lastIndexOf("-")); return i > 0 ? s.slice(0, i) : s; }
  /** A stack member's tail as a new step's name ends: _F of Ltyr_SP_F (and of
   *  an older Ltyr-sp-F). @param {string} s */
  const tailOf = (s) => SEP + s.slice(stem(s).length + 1);
  /** A block's name for its card: the system part, which gives way to an
   *  ellipsis, and the job part after it, which always shows -- a name too
   *  long for the card reads "glyc…_Freq", not "glycine_Fr…". The split is
   *  the row's system name when the name starts with it, else the last
   *  separator. @param {string} name @param {string} sys */
  function nameHtml(name, sys) {
    let i = -1;
    if (sys && name.length > sys.length + 1 && name.slice(0, sys.length).toLowerCase() === sys.toLowerCase()
        && (name[sys.length] === SEP || name[sys.length] === "-")) i = sys.length;
    else if (stem(name) !== name) i = stem(name).length;
    if (i <= 0) return `<span class="qe-nm">${esc(name)}</span>`;
    return `<span class="qe-nm" data-split="1"><span class="qe-stem">${esc(name.slice(0, i))}</span><span class="qe-tail">${
      esc(name.slice(i))}</span></span>`;
  }

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
        // what it runs after, or takes its geometry from, is gone (a
        // "Remove only this"): it cannot run until it is pointed elsewhere
        if (u && !p) { out = true; break; }
        if (!p || p === c) continue;
        if (p.state === "failed" || p.state === "blocked" || (p.state === "running" && p.faulted)
            || ((p.state === "pending" || p.state === "cancelled") && bad(p, seen))) { out = true; break; }
      }
      memo.set(c.name, out);
      return out;
    };
    _doomed = new Set(q.filter((/** @type {any} */ c) => c.state === "pending" && bad(c, new Set()))
      .map((/** @type {any} */ c) => c.name));
    // and those below a FILTERED one: the engine leaves them out too (a
    // dependent of a filtered calculation is filtered), so they are drawn
    // filtered now, as a pending one below a failed one is drawn blocked
    /** @type {Map<string, boolean>} */ const sv = new Map();
    /** @param {any} c @param {Set<string>} seen @returns {boolean} */
    const sieved = (c, seen) => {
      if (sv.has(c.name)) return /** @type {boolean} */ (sv.get(c.name));
      if (seen.has(c.name)) return false;
      seen.add(c.name);
      let out = false;
      for (const u of [c.after, c.geometry_source === "reference" ? c.ref_name : ""]) {
        const p = u ? by.get(u) : null;
        if (!p || p === c) continue;
        if (p.state === "filtered" || (p.state === "pending" && sieved(p, seen))) { out = true; break; }
      }
      sv.set(c.name, out);
      return out;
    };
    _sieved = new Set(q.filter((/** @type {any} */ c) => c.state === "pending" && !_doomed.has(c.name)
      && sieved(c, new Set())).map((/** @type {any} */ c) => c.name));
  }
  /** Pending calculations below a filtered one (doomedFrom) @type {Set<string>} */
  let _sieved = new Set();

  /** One calculation's state as the canvas draws it: a faulted run is
   *  failed, a pending one that can never run is blocked, one held at its
   *  gate is waiting. @param {any} c */
  const calcState = (c) => c.state === "running" && c.faulted ? "failed"
    // its remote device stopped answering: the orange box (design D7)
    : c.state === "running" && c.disconnected ? "disconnected"
    : c.state === "pending" && _doomed.has(c.name) ? "blocked"
    : c.state === "pending" && _sieved.has(c.name) ? "filtered"
    : c.state === "pending" && c.waiting ? "waiting" : c.state;

  /** A block's state: the most pressing of its members'. @param {any} n */
  function nodeState(n) {
    const st = n.members.map(calcState);
    // a lost connection is the one to see first: it is the state that asks
    // the user to look (is the device still on?)
    for (const s of ["disconnected", "running", "failed", "waiting", "blocked", "pending", "cancelled"]) {
      if (st.indexOf(s) >= 0) return s;
    }
    // finished: done when anything passed, filtered when nothing did
    return st.every(s => s === "filtered") ? "filtered" : "done";
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
  /** The selected rows (systems): several by Ctrl+click on their names, or
   *  the one whose name was clicked. @returns {any[]} */
  const selRows = () => (!_sel ? [] : _sel.t === "rows" ? _sel.keys.map(laneByKey).filter((/** @type {any} */ L) => L && L.sys)
    : _sel.t === "sys" ? [laneByKey(_sel.key)].filter((/** @type {any} */ L) => L && L.sys) : []);
  /** Whether a row's name is drawn selected. @param {string} key */
  const isRowSel = (key) => !!_sel && ((_sel.t === "sys" && _sel.key === key)
    || (_sel.t === "rows" && _sel.keys.indexOf(key) >= 0));
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
  /** The slot BEFORE the selected block (insertBefore): a step that takes
   *  its place in the chain -- what it ran after and the structure it
   *  started from -- the block then running after the new step, on its
   *  result. Only before a block that can still change: a started one keeps
   *  what it ran (P24). `parent` is what it hangs off now (null first in
   *  its row), so the add menu suggests as it does after it.
   *  @param {any} M */
  function beforeSlotOf(M) {
    const B = _sel && _sel.t === "node" ? M.lanes.flatMap((/** @type {any} */ L) => L.nodes)
      .find((/** @type {any} */ n) => n.key === _sel.key) : null;
    if (!B || !B.editable) return null;
    const A = M.nodeOf.get(upOf(B.members[0]));
    return { lane: B.lane, parent: A && A.lane === B.lane && A !== B ? A : null, before: B,
             c: B.col, r: B.row, branch: false, key: B.lane.key + "<" + B.key };
  }

  // ------------------------------------------------------------ render

  /** When a mouse button went down in the window (0: none held). A redraw
   *  then would replace the block or button under the pointer, and the
   *  browser drops a click whose press and release land on different
   *  elements -- a poll while the queue runs did that now and then. The
   *  redraw waits for the release; a hold longer than PRESS_MS is no click
   *  and draws as usual. */
  let _pressAt = 0, _held = false;
  const PRESS_MS = 1500;
  /** Hold redraws while a click is under way (see _pressAt). @param {HTMLElement} host */
  function wirePress(host) {
    const release = () => {
      if (!_pressAt) return;
      _pressAt = 0;
      // after the click this release makes, which redraws by itself
      if (_held) { _held = false; setTimeout(render, 0); }
    };
    host.addEventListener("mousedown", () => { _pressAt = Date.now(); }, true);
    document.addEventListener("mouseup", release, true);
    // a drag of the browser's own ends without a mouseup
    host.addEventListener("dragstart", release, true);
    window.addEventListener("blur", release);
  }

  function render() {
    if (!_active) return;
    if (_pressAt && Date.now() - _pressAt < PRESS_MS) { _held = true; return; }
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
    if (_sel && _sel.t === "rows") {
      // rows that went are dropped from the selection; one left is a row
      const keys = _sel.keys.filter((/** @type {string} */ k) => laneByKey(k));
      _sel = keys.length > 1 ? { t: "rows", keys: keys } : keys.length ? { t: "sys", key: keys[0] } : null;
    }
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
    const fin = NB.queue.filter((/** @type {any} */ c) => FINISHED.indexOf(c.state) >= 0).length;
    const cf = /** @type {HTMLButtonElement} */ (NB.$("qe-clearfin"));
    cf.disabled = !fin;
    cf.title = fin ? `Take the ${fin} finished, failed or filtered calculation${fin === 1 ? "" : "s"} off the list (their files stay)`
                   : "Nothing has finished yet";
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
    // a dependency that is gone: the run is refused until it is re-pointed
    const have = new Set(NB.queue.map((/** @type {any} */ c) => c.name));
    const lost = NB.queue.find((/** @type {any} */ c) => EDITABLE.indexOf(c.state) >= 0
      && ((c.after && !have.has(c.after)) || (c.geometry_source === "reference" && c.ref_name && !have.has(c.ref_name))));
    if (lost) {
      w.push(lost.name + (lost.after && !have.has(lost.after) ? " runs after " + lost.after : " takes its geometry from " + lost.ref_name)
        + ", which is not in the queue: choose another geometry for it or remove it.");
    }
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
      const noGeo = L.calcs.find((/** @type {any} */ c) => c.set_name && !isSettings(setKindOf(c.kind))
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
    const sig = JSON.stringify([_sets.map(s => [s.id, s.name, s.kind]), n ? [n.key, n.setKind, n.editable,
      n.members[0].set_name] : null, _missing.length,
      editing ? [editing.id, editing.kind, editing.id ? "" : editing.name] : null,
      _wfs.map(w => [w.id, w.name, wfSummary(w)]), !!_clip]);
    if (sig === _libSig) return;
    _libSig = sig;
    if (_missing.length && !_sets.length) {
      host.innerHTML = `<p class="hint qe-pad">The queue editor needs the updated backend: it has no inp sets to offer.</p>`;
      NB.$("qe-dragimgs").innerHTML = "";
      return;
    }
    // ORCA levels, then MLIP sets, then CREST sets; each group shown, an empty one too:
    // "+ New" is where its first set comes from
    host.innerHTML = SET_KINDS.map(kd => {
      const sets = _sets.filter(s => s.kind === kd);
      const match = !!n && n.setKind === kd;
      const draft = editing && !editing.id && editing.kind === kd
        ? `<span class="qe-set" data-edit="1" data-draft="1">${esc(editing.name || "New set")} · unsaved</span>` : "";
      const what = setWord(kd);
      return `<div class="qe-group">
        <div class="qe-ghead"><span class="qe-kind"${match ? ' data-on="1"' : ""}>${esc(SET_GROUP[kd])}</span>
          <button class="qe-new" type="button" data-new="${kd}" aria-label="New ${esc(what)}"
            title="A new ${esc(what)}, starting from ORCAdesk's default">+ New</button></div>
        ${sets.map(s => {
          const cur = !!n && n.members[0].set_name === s.name && n.setKind === kd;
          // a finished or failed block takes a set too: it then runs again
          // (asked first)
          const applies = match && takesSet(n);
          const ed = !!editing && editing.id === s.id;
          return `<button class="qe-set" type="button" draggable="true" data-id="${esc(s.id)}"
            aria-pressed="${applies ? cur : ed}"${cur ? ' data-cur="1"' : ""}${match ? ' data-match="1"' : ""}${ed ? ' data-edit="1"' : ""}
            aria-label="${esc((applies ? "Apply " : "Edit ") + what + " " + s.name)}"
            title="${applies ? (n.editable ? "Click to put this level on the selected block (its type stays). "
              : "Click to run the selected finished or failed block again with this level (asked first: its output is overwritten). ")
              : "Click to edit it. "}Drag onto a block (its level changes, its type stays) or onto the + slot (a new step). Delete removes the set.">${esc(s.name)}</button>`;
        }).join("")}${draft}
      </div>`;
    }).join("") + renderWorkflowGroup();
    NB.$("qe-dragimgs").innerHTML = _sets.map(s =>
      `<div class="qe-dragimg" id="qe-di-${esc(s.id)}"><span class="qe-nm">${
        esc(s.kind === "MLIP" ? "Preopt" : s.kind === "CREST" ? "Conformers" : "Level")}</span><span class="qe-sub">${esc(s.name)}</span></div>`).join("")
      + _wfs.map(w => `<div class="qe-dragimg" id="qe-di-wf-${esc(w.id)}"><span class="qe-nm">Workflow</span><span
        class="qe-sub">${esc(w.name)}</span></div>`).join("");
  }

  /** The library's Workflows group: the saved ones (a click applies one to
   *  the selected rows or after the selected block, else makes it the copied
   *  workflow; a drag puts it on a + slot), and "+ Save copied". */
  function renderWorkflowGroup() {
    return `<div class="qe-group">
        <div class="qe-ghead"><span class="qe-kind">Workflows</span>
          <button class="qe-new" type="button" data-wfsave="1"${_clip ? "" : " disabled"}
            aria-label="Save the copied workflow in the library"
            title="${_clip ? "Keep the copied workflow (" + esc(wfSummary(_clip)) + ") here by name" : "Copy blocks first (select them, Ctrl+C)"}">+ Save copied</button></div>
        ${_wfs.map(w => `<button class="qe-set" type="button" draggable="true" data-wf="${esc(w.id)}"
            aria-label="${esc("Workflow " + w.name + ", " + wfSummary(w))}"
            title="${esc(wfSummary(w))}. Click: into the selected rows, or after the selected block; with nothing selected it becomes the copied workflow. Drag onto a + slot. Delete removes it.">${esc(w.name)}</button>`).join("")}
        ${_wfs.length ? "" : `<p class="hint qe-pad">Copy some blocks (Ctrl+C) and save them here, to put the same steps on other rows with one click.</p>`}
      </div>`;
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
        // a system's name over its row: the handle of its structures panel
        // (add, paste, view, check), as an empty system's caption is
        if (L.sys) {
          const on = isRowSel(L.key);
          const k = (L.sys.inputs || []).length;
          parts.push(`<button class="qe-cap" type="button" data-a="sys" data-row="1" data-lane="${esc(L.key)}"
            style="left:${X0 - 4}px; top:${top + 4}px"${on ? ' aria-current="true"' : ""}
            aria-label="System ${esc(L.name)}: its structures" title="The row's structures: add, paste, view, check. Ctrl+click selects several rows.">${
              esc(L.name || "(unnamed)")} · ${k ? k + " structure" + (k === 1 ? "" : "s") : "no structure yet"}</button>`);
        }
      } else {
        // an empty system: its name is the handle that selects it
        const on = isRowSel(L.key);
        parts.push(`<button class="qe-cap" type="button" data-a="sys" data-lane="${esc(L.key)}"
          style="left:${X(1) - 8}px; top:${top + LANE_PAD + 20}px"${on ? ' aria-current="true"' : ""}
          aria-label="System ${esc(L.name)}, nothing queued yet" title="Ctrl+click selects several rows">${esc(L.name || "(unnamed)")} · ${
            (L.sys.inputs || []).length ? esc(L.sys.inputs[0].label || "structure") : "no structure yet"}</button>`);
      }
      L.top = top;
      top += LANE_PAD + maxRow * ROWH + 100;
    });
    const Y = (/** @type {any} */ L, /** @type {number} */ r) => laneTop.get(L.key) + LANE_PAD + r * ROWH;

    // edges, under the blocks; an energy condition on one is a chip on the
    // block it leads into (its top-left, above the card: the 36px gap between
    // columns has no room for words), once per block
    /** @type {Set<string>} */ const chipped = new Set();
    M.lanes.forEach((/** @type {any} */ L) => L.edges.forEach((/** @type {any} */ e) => {
      const A = e.from, B = e.to;
      const on = _sel && _sel.t === "edge" && _sel.key === e.key;
      const attrs = `data-gate="${e.gate}"${on ? ' data-sel="1"' : ""}`;
      const cw = condWords(e.cond);
      const aria = esc("Connection " + A.short + " to " + B.short + (e.gate === "approval" ? ", after approval" : "")
        + (cw ? ", passes on " + cw.replace("ΔE ≤", "ΔE at most") + (e.cond.de !== null ? " kcal/mol" : "") : ""));
      if (cw && !chipped.has(B.key)) {
        chipped.add(B.key);
        parts.push(`<button type="button" class="qe-echip" data-a="edge" data-k="${esc(e.key)}"${on ? ' data-sel="1"' : ""}
          style="left:${X(B.col)}px; top:${Y(L, B.row) - 18}px" aria-label="${aria}"
          title="${esc("Only what passes this goes on to " + B.short + (e.cond.de !== null ? " (ΔE in kcal/mol)" : "")
            + "; the rest ends filtered. Click to change it.")}">${esc(cw)}</button>`);
      }
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
      // a level's block says what it adds to its level ("· 2 constraints")
      const f0 = _full.get(c0.name);
      const words = f0 && f0.calc && isLevelCalc(c0) ? optWords(c0.kind, f0.calc.config) : [];
      const aria = (n.stack ? `${n.name}, ${n.members.length} calculations` : `Select ${n.name}`)
        + ", " + ST_LABEL[n.state] + (words.length ? ", with " + words.join(", ") : "");
      rects.push({ key: n.key, lane: L.key, x: X(n.col), y: Y(L, n.row) });
      // a stack draws one card per calculation (the front one and a back card
      // for each other, up to MAX_BACKS -- past that the count says it), each
      // card's border in ITS calculation's state colour (data-edge; the dot
      // stays the block's overall state), and its count sits bottom right,
      // away from the run order a block shows top right, so "2" calculations
      // never reads as run order 2
      const backs = n.stack ? Math.min(n.members.length - 1, MAX_BACKS) : 0;
      // ...except that, when there are more members than cards, the last back
      // card stands for ALL the rest and takes their most pressing state
      // (nodeState's order): otherwise the first four of 80 finishing turned
      // every card green while 76 had not run
      const edge = (/** @type {number} */ i) => i === backs && n.members.length - 1 > MAX_BACKS
        ? nodeState({ members: n.members.slice(i) }) : calcState(n.members[i]);
      // a stack whose members are partly filtered counts what passed: 3/10
      const cut = n.members.filter((/** @type {any} */ c) => calcState(c) === "filtered").length;
      const count = cut ? (n.members.length - cut) + "/" + n.members.length : String(n.members.length);
      const countWords = n.members.length + " calculations" + (cut ? ", " + (n.members.length - cut) + " passed the condition" : "");
      // a conformer placeholder (one block standing for every conformer its
      // search will find) counts "all" where a stack counts its members
      const all = !n.stack && isConfAll(c0) && calcState(c0) !== "filtered";
      const sub = `<span class="qe-sub"${mod ? ' data-mod="1"' : ""}>${esc((KIND_LABEL[n.kind] || n.kind) + " / " + setName)}${mod ? " *" : ""}</span>`;
      parts.push(`<div class="qe-node" data-k="${esc(n.key)}" data-setkind="${n.setKind}"${
          n.editable ? ' data-editable="1"' : ""}${_pop === n.key ? ' data-pop="1"' : ""}
        style="left:${X(n.col)}px; top:${Y(L, n.row)}px; z-index:${open ? 20 : 2}"${n.stack ? ' data-stack="1"' : ""}>
        ${Array.from({ length: backs }, (_, i) => `<div class="qe-back" data-depth="${backs - i}" data-edge="${edge(backs - i)}"></div>`).join("")}
        <div class="qe-card"${sel ? ' data-sel="1"' : ""} data-st="${n.state}" data-edge="${edge(0)}">
          <button type="button" class="qe-pick" data-a="node" data-k="${esc(n.key)}"
            aria-label="${esc(aria)}"${n.stack ? ` aria-expanded="${!!open}"` : ""}
            title="${esc(n.name + "\n" + (KIND_LABEL[n.kind] || n.kind) + " / " + setName
              + (!n.stack && calcState(c0) === "filtered" ? "\n" + (c0.message || "Filtered: what it depends on was filtered out.") : ""))}">
            <span class="qe-line"><span class="qe-dot" data-st="${n.state}"></span>
              ${nameHtml(n.name, L.sys ? L.name : "")}
              ${n.stack ? "" : `<span class="qe-ord" aria-label="Run order ${n.first}">${n.first}</span>`}</span>
            ${n.stack
              ? `<span class="qe-line qe-line2">${sub}<span class="qe-ord" data-stack="1"${cut ? ' data-cut="1"' : ""} aria-label="${esc(countWords)}">${count}</span></span>`
              : all ? `<span class="qe-line qe-line2">${sub}<span class="qe-ord" data-stack="1" aria-label="${
                  esc("One calculation per conformer of " + c0.ref_name + ", once it has run")}">all</span></span>`
              : sub}
          </button>
          ${open ? `<div class="qe-pop" role="list">${n.members.map((/** @type {any} */ c, /** @type {number} */ i) =>
              `<button type="button" class="qe-prow" role="listitem" data-a="member" data-k="${esc(n.key)}" data-m="${i}"${
                (_sel.m || 0) === i ? ' data-on="1"' : ""} data-st="${calcState(c)}"
                aria-label="Select ${esc(c.name)}, run order ${M.idx.get(c.name) + 1}, ${esc(ST_LABEL[calcState(c)] || c.state)}"
                title="${esc(c.name + (calcState(c) === "filtered" ? "\n" + (c.message || "Filtered: what it depends on was filtered out.") : ""))}">${
                  nameHtml(c.name, L.sys ? L.name : "")}<span
                class="qe-ord">${M.idx.get(c.name) + 1}</span></button>`).join("")}</div>` : ""}
        </div>
        ${words.length ? `<span class="qe-opts" aria-hidden="true">${esc(words.join(" · "))}</span>` : ""}
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
          ? `<span class="qe-gtext"><span>Manual build</span><span class="qe-gsub">in the builder</span></span>`
          : `<button class="qe-gbtn" type="button" data-a="ghost" data-slot="${esc(g.key)}"
              aria-label="${esc("Add a calculation " + target + ": press for the add menu, or drop a level or a saved workflow here.")}"
              title="Click: a level and the step to run with it, a workflow, or a manual build. Or drop a level or a saved workflow here.">+</button>`}
      </div>`);
    });

    // the + before the selected block, on its incoming side: in the gap
    // after what it hangs off, or on its left edge first in a row (the row's
    // grip is left of that gap)
    const before = beforeSlotOf(M);
    if (before) {
      const B = before.before;
      const cx = B.col > 0 ? X(B.col) - (COLW - W) / 2 : X(B.col);
      const what = "Add a step before " + B.short;
      parts.push(`<button class="qe-ins" type="button" data-a="ghost" data-slot="${esc(before.key)}"
        style="left:${cx - 9}px; top:${Y(before.lane, B.row) + H / 2 - 9}px"
        aria-label="${esc(what + ": press for the add menu, or drop a level here. It takes " + B.short + "'s structure, and " + B.short + " then starts from its result.")}"
        title="${esc(what + ". It takes its structure and what it ran after; " + B.short + " then runs on its result. Click for the add menu, or drop a level here.")}">+</button>`);
    }

    if (!M.lanes.length) {
      // the empty queue says how a row starts, where the rows would be
      parts.push(`<div class="qe-empty" style="top:${top + 24}px">
        <p>Nothing in the queue.</p>
        <p class="hint">Drop .xyz files on this window, or press + system: each structure becomes a row,
          and its steps are built here.</p></div>`);
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
    host._ghosts = before ? ghosts.concat([before]) : ghosts;
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
    // The builder is the main window's New calculation as it was, cell and
    // all: drawn over the lanes and the inspector, in a stream-like column of
    // the old cell's width (#qe-build), never squeezed into the inspector
    const bhost = NB.$("qe-buildinner");
    if (t !== null) {
      app.setAttribute("data-building", "1");
      NB.$("qe-build").hidden = false;
      if (_buildMounted !== _buildKey || _buildTarget !== t || !_buildCell || !bhost.contains(_buildCell)) {
        const c = t ? NB.queue.find((/** @type {any} */ x) => x.name === t) || null : null;
        _buildCell = NB.build.cellFor(c);
        _buildMounted = _buildKey; _buildTarget = t;
        bhost.innerHTML = "";
        bhost.appendChild(_buildCell);
        NB.$("qe-build").scrollTop = 0;
        _inspSig = "";
      }
      return;
    }
    app.removeAttribute("data-building");
    if (!NB.$("qe-build").hidden) { NB.$("qe-build").hidden = true; bhost.innerHTML = ""; }
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
    const rows = _sel && _sel.t === "rows" ? selRows() : null;
    if (!n && !e && !L && !many && !rows) edEnsure();
    const sig = JSON.stringify([_sel, !!_clip, Object.keys(_jobs).length, many ? many.map((/** @type {any} */ x) => [x.key, x.name, x.kind]) : null,
      rows ? rows.map((/** @type {any} */ x) => [x.key, x.name, x.calcs.length, x.started, x.sys]) : null,
      L || rows ? _wfs.map(w => [w.id, w.name]) : null,
      !n && !e && !L && !many && !rows ? [_ed && [_ed.id, _ed.kind, _ed.base], _edErr,
        _sets.map(x => [x.id, x.name, x.kind]), edUsers().map((/** @type {any} */ c) => [c.name, c.state])] : null,
      n ? n.members.map((/** @type {any} */ c) => [c, _full.get(c.name) && _full.get(c.name).calc]) : null,
      e ? [e.gate, e.to.members, e.from.members] : null, L ? L.sys : null,
      n ? (n.lane.sys || null) : null, n ? isModified(n) : null, _sets.length]);
    if (sig === _inspSig) return;
    _inspSig = sig;
    if (n) inspectNode(host, n);
    else if (many) inspectMulti(host, many);
    else if (rows) inspectRows(host, rows);
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
    // What the block is: a level's block (options of its own, merged into
    // the level), a set's text that names its own job or an MLIP set's
    // settings (edited here as text), or a Guided / pasted calculation
    // (the builder edits it -- a pasted text that is no level included,
    // so a text block can always go back to the builder)
    const isMlip = setKindOf(c.kind) === "MLIP" && !!c.set_name;
    const isCrest = setKindOf(c.kind) === "CREST" && !!c.set_name;
    const text = c.set_name || c.is_raw ? templateOf(c) : "";
    const isLevel = !isMlip && !isCrest && !!text && hasJobLine(text);
    const isSetText = !isLevel && !!c.set_name;
    const isTemplate = isLevel || isSetText;
    const mod = isModified(n);
    const kind = detectKind(text);
    const words = isLevel && full ? optWords(c.kind, full.config) : [];
    const types = typeKinds();
    const own = ownCM({ lane: L, c: c });
    const charge = sys && !own ? sys.charge : c.charge, mult = sys && !own ? sys.multiplicity : c.multiplicity;
    const cmLocked = sys && !own ? nameLocked : locked;

    // the geometry choices: the system's structures, then the outputs of
    // every ancestor block along the `after` chain -- each member of a stack,
    // an IRC's two ends -- as the design lists them; then every other
    // calculation in the queue that does not itself hang off this one
    const opts = [];
    (sys ? sys.inputs || [] : []).forEach((/** @type {any} */ inp, /** @type {number} */ i) =>
      opts.push({ key: "in:" + i, label: inp.label || "structure " + (i + 1) }));
    const seen = new Set([c.name]);
    const blocks = new Set([n.key]);
    /** @type {Set<string>} */ const listed = new Set();
    /** @param {any} x @param {any[]} into */
    const refOpts = (x, into) => {
      if (listed.has(x.name)) return;
      listed.add(x.name);
      if (x.kind === "irc") {
        // an IRC's own last geometry is the TS it started from (measured on
        // real outputs), not a structure of its own: what a step takes is an
        // end, or a point of its paths -- picked in the IRC picker
        into.push({ key: "ref:" + x.name + ":irc_f", label: "ref → " + x.name + ", forward end" });
        into.push({ key: "ref:" + x.name + ":irc_b", label: "ref → " + x.name + ", backward end" });
        into.push({ key: "pick:" + x.name, label: x.name + ": a point on its path…" });
        return;
      }
      into.push({ key: "ref:" + x.name, label: "ref → " + x.name });
      // a CREST search: its lowest conformer above, any other picked by number
      if (x.kind === "crest_conf") into.push({ key: "conf:" + x.name, label: x.name + ": a conformer…" });
    };
    for (let a = c.after; a && !seen.has(a); ) {
      seen.add(a);
      const p = NB.queue.find((/** @type {any} */ x) => x.name === a);
      if (!p) break;
      const pn = _M.nodeOf.get(p.name);
      const mem = pn && pn.lane === L ? (blocks.has(pn.key) ? [] : pn.members) : [p];
      if (pn) blocks.add(pn.key);
      mem.forEach((/** @type {any} */ x) => refOpts(x, opts));
      a = p.after;
    }
    const below = dependentsOf(c.name);
    /** @type {{key: string, label: string}[]} */ const others = [];
    NB.queue.forEach((/** @type {any} */ x) => {
      if (x.name === c.name || below.has(x.name)) return;
      refOpts(x, others);
    });
    // what it runs after, said in words when no connection on this row shows
    // it (an `after` in another row, or gone), and its gate when it waits
    const pa = c.after ? NB.queue.find((/** @type {any} */ x) => x.name === c.after) : null;
    const paNode = pa ? _M.nodeOf.get(pa.name) : null;
    const afterNote = !c.after ? ""
      : !pa ? `Runs after ${c.after}, which is not in the queue: it cannot run. Choose another geometry, or remove it.`
      : paNode && paNode.lane !== L ? `Runs after ${pa.name}, in another row${
          c.gate === "approval" ? ", once approved" : ""}.` : "";
    const refGone = c.geometry_source === "reference" && !!c.ref_name
      && !NB.queue.some((/** @type {any} */ x) => x.name === c.ref_name);
    const blockedNote = _doomed.has(c.name) && !c.message
      ? (refGone ? `Blocked: it takes its geometry from ${c.ref_name}, which is not in the queue. Choose another geometry.`
        : "Blocked: a calculation it depends on has failed. Put a new level on that one to run it again, or remove it.") : "";
    let geo = "";
    if (c.geometry_source === "reference") {
      geo = "ref:" + c.ref_name + (c.ref_point ? ":" + c.ref_point : "");
      if (!opts.some(o => o.key === geo) && !others.some(o => o.key === geo)) {
        opts.push({ key: geo, label: "ref → " + c.ref_name + (c.ref_point ? ", " + pointWords(c.ref_point) : "") + (refGone ? ", gone" : "") });
      }
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
    if (_sysStage) { _sysStage.destroy(); _sysStage = null; }
    // the last refusal, when it names this block (or names none)
    const refusalFor = _refusal && (!_refusal.name || n.members.some((/** @type {any} */ x) => x.name === _refusal.name))
      ? _refusal.text : "";
    const option = (/** @type {any} */ o) => `<option value="${esc(o.key)}"${o.key === geo ? " selected" : ""}>${esc(o.label)}</option>`;
    // the energy condition on the edge into it: offered on a stack, after a
    // stack or a CREST search, or wherever one is set already
    const upN = _M.nodeOf.get(upOf(n.members[0]));
    const cond = n.stack || (upN && upN !== n && (upN.stack || upN.kind === "crest_conf")) || !!condWords(condOf(n.members))
      ? condControl(() => { const nn = nodeByKey(n.key); return nn ? nn.members : []; }) : null;
    // its own name: the part after the row's (the whole name, standalone)
    const ownName = !locked && !(sys && !suffix);

    host.innerHTML = `
      <div class="qe-ititle">
        ${sys ? `<input class="qe-sysname" data-f="sysname" value="${esc(sysName)}" spellcheck="false"
            style="width:${Math.max(sysName.length, 2) + 0.5}ch"
            aria-label="System name, renames every calculation in this row that has not run"
            title="${esc(renameTip(L))}">`
          : ownName ? "" : `<span class="qe-sysname" data-fixed="1">${esc(c.name)}</span>`}
        ${ownName ? `<input class="qe-sysname qe-sfx" data-f="ownname" value="${esc(sys ? suffix : c.name)}" spellcheck="false"
            style="width:${Math.max((sys ? suffix : c.name).length, 2) + 0.5}ch"
            aria-label="${esc(sys ? "The end of this calculation's name, after the row's" : "This calculation's name")}"
            title="Rename this calculation alone (its folder). What runs after it follows.">`
          : sys ? `<span class="qe-sfx">${esc(suffix)}</span>` : ""}
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
      ${isConfAll(c) && calcState(c) !== "filtered" ? `<p class="qe-note">${esc(confAllNote(c))}</p>` : ""}
      <p class="qe-note" data-tone="err" data-f="refusal" role="alert"${refusalFor ? "" : " hidden"}>${esc(refusalFor)}</p>
      ${c.waiting ? `<div class="qe-wait"><span>${esc((pa ? pa.name : c.after) + " finished. The queue skips "
          + c.name + " and runs the next calculation until you approve.")}</span>
        <button class="btn btn-primary" type="button" data-a="approve">Approve</button></div>` : ""}
      <select class="qe-select" data-f="geo" aria-label="Geometry"${locked ? " disabled" : ""}>${
        opts.map(option).join("")}${others.length ? `<optgroup label="Other calculations">${
        others.map(option).join("")}</optgroup>` : ""}</select>
      ${cond ? cond.html : ""}
      ${isLevel ? `
        <label class="qe-typerow"><span class="qe-note">Type</span>
          <select class="qe-select" data-f="type" aria-label="Type: the job this block runs at its level"${
            locked || !n.editable ? " disabled" : ""}>${(types.indexOf(c.kind) >= 0 ? types : [c.kind].concat(types)).map(k =>
              `<option value="${esc(k)}"${k === c.kind ? " selected" : ""}>${esc(TYPE_LABEL[k] || KIND_LABEL[k] || k)}</option>`).join("")}</select></label>
        <div class="qe-saveas">
          <span class="qe-note">${esc(words.length ? "Adds " + words.join(", ") + "." : "No options of its own: the level as it is.")}</span>
          <button class="btn btn-sm" type="button" data-a="lvedit"${locked ? " disabled" : ""}
            title="Constraints and scan, MaxIter, the IRC / Freq / NMR settings, solvation, extra keywords">Edit options in the builder</button>
        </div>` : ""}
      ${isMlip ? `
        <label class="qe-typerow"><span class="qe-note">Task</span>
          <select class="qe-select" data-f="type" aria-label="Task: what the MLIP runs"${
            locked || !n.editable ? " disabled" : ""}>${MLIP_TYPES.filter(k => !_mlipJobs.length
              || _mlipJobs.indexOf(k[0]) >= 0 || k[0] === c.kind).map(k =>
              `<option value="${esc(k[0])}"${k[0] === c.kind ? " selected" : ""}>${esc(k[1])}</option>`).join("")}</select></label>` : ""}
      ${isTemplate ? `
        <textarea class="qe-tpl" data-f="tpl" rows="${isLevel ? 8 : 11}" spellcheck="false"${locked || !n.editable ? " readonly" : ""}
          aria-label="${esc((n.setKind === "ORCA" ? "Level" : n.setKind || "Template") + " / " + (c.set_name || ".inp text"))}">${esc(text)}</textarea>
        ${mod && !locked && n.editable ? `
          <div class="qe-saveas">
            <input class="qe-input" data-f="newname" placeholder="${esc("Save as a new " + setWord(kind))}"
              aria-label="Name for the new inp set">
            <button class="btn btn-primary" type="button" data-a="saveas" disabled>Save</button>
          </div>` : ""}
        ${isMlip ? `<p class="hint">An MLIP block runs the settings these lines name.</p>` : ""}
        ${isCrest ? `<p class="hint">A CREST block runs the settings these lines name.</p>` : ""}
        ${isMlip || isCrest ? `<div class="qe-saveas">
          <span class="qe-note">${esc(isCrest
            ? "The search speed, NCI, solvent model, MD / MTD lengths and the other CREST options are the builder's. Saved there, the block is built, no longer the set's."
            : "The task's own options are the builder's. Saved there, the block is built, no longer the set's.")}</span>
          <button class="btn btn-sm" type="button" data-a="sbedit"${locked || !n.editable ? " disabled" : ""}
            title="${esc(isCrest ? "Search speed, NCI, solvent model, MD / MTD lengths and the other CREST options"
              : "The model, device, cores and the task's options")}">Edit in the builder</button></div>` : ""}
        ${isSetText && !isMlip && setKindOf(c.kind) === "ORCA" ? `<div class="qe-saveas">
          <span class="qe-note">This text names its own job (it has no ${JOB}), so it runs as written; its type cannot change here.</span>
          <button class="btn btn-sm" type="button" data-a="edit"${locked ? " disabled" : ""}>Edit in the builder</button></div>` : ""}
        ${isLevel ? `<span class="qe-note qe-rendh" data-f="rendh">Rendered input · ${esc(TYPE_LABEL[c.kind] || c.kind)}</span>
        <textarea class="qe-tpl" data-f="rendered" rows="12" readonly spellcheck="false"
          aria-label="The input this block runs, as ORCAdesk renders it for its type">Rendering…</textarea>` : ""}`
      : `
        <span class="qe-note qe-rendh" data-f="rendh" hidden></span>
        <textarea class="qe-tpl" data-f="preview" rows="11" readonly spellcheck="false"
          aria-label="The input this calculation runs">Building the input…</textarea>
        <div class="qe-saveas">
          <span class="qe-note">${c.is_raw ? "Pasted .inp text." : "Built with the Guided builder."}</span>
          <button class="btn btn-sm" type="button" data-a="edit"${locked ? " disabled" : ""}>Edit in the builder</button>
        </div>`}
      <div class="qe-saveas" data-acts="1">
        <button class="btn btn-sm btn-ghost" type="button" data-a="earlier"${locked ? " disabled" : ""}
          title="Run it before the block before it in this row (Alt+Left)">↑ Earlier</button>
        <button class="btn btn-sm btn-ghost" type="button" data-a="later"${locked ? " disabled" : ""}
          title="Run it after the block after it in this row (Alt+Right)">↓ Later</button>
        <button class="btn btn-sm btn-ghost" type="button" data-a="dup"
          title="A copy of this calculation with all its settings, queued to run">Duplicate</button>
        <button class="btn btn-sm btn-ghost" type="button" data-a="folder"
          title="Open its folder in the workspace">Show folder</button>
        ${sys ? `<button class="btn btn-sm btn-ghost" type="button" data-a="structs"
          title="The row's structures: add, paste, view, check">Structures…</button>` : ""}
      </div>
      <div class="qe-saveas">
        <span class="qe-note">${!n.stack ? ""
          : "This block is " + n.members.length + " calculations; the template is shared. "
            + (_sel.all ? "All of them are selected, so Remove takes them all."
                        : "Remove takes the selected one; drag over the block to take them all.")}</span>
        <button class="btn btn-sm btn-danger" type="button" data-a="remove"${
          (_sel.all ? n.state === "running" || n.state === "disconnected"
                    : c.state === "running") ? " disabled" : ""}>${
          n.stack && _sel.all ? "Remove all " + n.members.length + "…" : "Remove…"}</button>
      </div>
      ${workflowButtons(true)}`;

    const q = (/** @type {string} */ s) => /** @type {any} */ (host.querySelector(s));
    const sn = q('[data-f="sysname"]');
    if (sn) {
      sn.addEventListener("focus", () => sn.select());
      sn.addEventListener("input", () => { sn.style.width = Math.max(sn.value.length, 2) + 0.5 + "ch"; });
      sn.addEventListener("change", () => { const v = live(); if (v) renameSystem(v.L, sn.value); });
    }
    const own2 = q('[data-f="ownname"]');
    if (own2) {
      own2.addEventListener("focus", () => own2.select());
      own2.addEventListener("input", () => { own2.style.width = Math.max(own2.value.length, 2) + 0.5 + "ch"; });
      own2.addEventListener("change", () => {
        const v = live();
        if (v) renameCalc(v.c, v.L.sys ? String(v.L.sys.name || "") + own2.value : own2.value);
      });
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
    if (cond) cond.wire(host);
    const btn = (/** @type {string} */ a, /** @type {(v: any) => void} */ fn) => {
      const b = q(`[data-a="${a}"]`);
      if (b) b.addEventListener("click", () => { const v = live(); if (v) fn(v); });
    };
    btn("lvedit", v => openLevelBuilder(v.c.name));
    // an MLIP or CREST block's builder edit is one undoable batch
    btn("edit", v => (/^(mlip|crest)/.test(String(v.c.kind || "")) ? openSettingsBuilder(v.c.name) : openBuilder(v.c.name)));
    btn("sbedit", v => openSettingsBuilder(v.c.name));
    btn("earlier", v => moveRun(v.c, -1));
    btn("later", v => moveRun(v.c, 1));
    btn("dup", v => duplicateBlock(v.n, v.c));
    btn("folder", v => showFolder(v.c));
    btn("structs", v => select({ t: "sys", key: v.L.key }));
    btn("approve", v => {
      const u = updates([v.c.name], () => ({ approved: true }));
      apply(u.ops, u.inv, "the approval");
    });
    btn("remove", () => requestRemove(removeChoice()));

    // What it ran, once it has: the .inp in its run folder (get_inp), not a
    // regenerated one -- the level or the builder may have changed since.
    // Before that, the input it would run (build_inp_preview, the launch
    // renderer; a level's block with its options).
    const ran = STARTED.indexOf(c.state) >= 0 || (c.state === "cancelled" && !!c.output_path);
    const onDisk = ran ? await NB.call("get_inp", c.name) : null;
    const diskText = onDisk && onDisk.ok && typeof onDisk.text === "string" ? onDisk.text : "";
    const rh = q('[data-f="rendh"]');
    if (diskText && rh && rh.isConnected) {
      rh.hidden = false;
      rh.textContent = "The input it ran · from its folder";
    }
    const rd = q('[data-f="rendered"]');
    if (rd) {
      const r = diskText ? { ok: true, text: diskText }
        : await NB.call("build_inp_preview", JSON.stringify(calcDict(c.name, { is_raw: true, raw_text: text })));
      if (rd.isConnected) rd.value = r && r.ok ? String(r.text || "") : "Could not render the input" + (r && r.error ? ": " + r.error : ".");
    }
    const pv = q('[data-f="preview"]');
    if (pv) {
      const src = full ? full : calcDict(c.name);
      const k = String(src.kind || c.kind || "");
      // an MLIP or CREST job runs no ORCA input: its settings are what it runs
      const r = diskText ? { ok: true, text: diskText }
        : /^(mlip|crest)/.test(k) ? { ok: true, text: backendSettings(k, src.config || {}) }
        : src.is_raw ? { ok: true, text: src.raw_text } : await NB.call("build_inp_preview", JSON.stringify(src));
      if (pv.isConnected) pv.value = r && r.ok ? String(r.text || "") : "Could not build the input" + (r && r.error ? ": " + r.error : ".");
    }
  }

  /** Every calculation that hangs off `name`, directly or further down (its
   *  `after` or its geometry reference): what cannot become its geometry
   *  source. @param {string} name */
  function dependentsOf(name) {
    const out = new Set();
    for (let grew = true; grew; ) {
      grew = false;
      NB.queue.forEach((/** @type {any} */ x) => {
        if (out.has(x.name) || x.name === name) return;
        const up = [x.after, x.geometry_source === "reference" ? x.ref_name : ""];
        if (up.some(u => u && (u === name || out.has(u)))) { out.add(x.name); grew = true; }
      });
    }
    return out;
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
    if (kind.indexOf("crest") === 0) {
      // and every other CREST option that is not StepConfig's default (the
      // builder's: search speed, NCI, solvent model, MD / MTD numbers, the
      // toggles), so what the block runs is all in view
      const shown = ["crest_method", "crest_solvent", "crest_ewin", "crest_threads"];
      Object.keys(cfg).filter(k => k.indexOf("crest_") === 0 && shown.indexOf(k) < 0).sort().forEach(k => {
        const val = cfg[k], d = (_stepDef || {})[k];
        if (val === "" || val == null || val === false || val === d || (typeof val === "number" && Number(d) === val)) return;
        lines.push(`${k.slice(6)} ${val === true ? "on" : String(val)}`);
      });
      // the stage's constraints (its --cinp file), atoms numbered from 1 as
      // the builder shows them; no target = hold the starting value (auto)
      (Array.isArray(cfg.geom_constraints) ? cfg.geom_constraints : []).forEach((/** @type {any} */ c) => {
        const at = (c.atoms || []).map((/** @type {number} */ a) => Number(a) + 1).join(" ");
        lines.push(`constrain ${c.kind} ${at}` + (c.kind === "C" ? "" : " " + (c.value == null ? "auto" : String(c.value))));
      });
    }
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
    // the energy condition this edge carries: offered into or out of a stack,
    // after a CREST search, or wherever one is set already
    const cond = A.stack || B.stack || A.kind === "crest_conf" || !!condWords(e.cond)
      ? condControl(() => {
          let cur = null;
          if (_M) for (const L of _M.lanes) { cur = L.edges.find((/** @type {any} */ x) => x.key === e.key) || cur; }
          return cur ? cur.gated : [];
        }) : null;
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
      ${locked ? `<p class="hint">${esc(B.short)} has started; its gate is kept as it ran.</p>` : ""}
      ${cond ? cond.html : ""}`;
    if (cond) cond.wire(host);
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

  /** The energy condition's control -- "Pass on: ΔE ≤ [ ] kcal/mol · top [ ]"
   *  -- for the calculations an edge leads into (a stack's members, or what
   *  one edge holds), and its wiring. A change goes to every member that can
   *  still take it -- one still to run, or a filtered one, which then goes
   *  back to pending with what it filtered (the store does that) -- in one
   *  undoable batch; a member that has run keeps the condition it ran under.
   *  `membersNow` gives them as the model holds them when a field fires.
   *  @param {() => any[]} membersNow @returns {{html: string, wire: (host: HTMLElement) => void}} */
  function condControl(membersNow) {
    const takes = (/** @type {any} */ c) => EDITABLE.indexOf(c.state) >= 0 || c.state === "filtered";
    const members = membersNow();
    const can = members.filter(takes);
    const k = condOf(can.length ? can : members);
    const confs = members.length > 0 && members.every((/** @type {any} */ c) => confOf(c.ref_point) > 0 || isConfAll(c));
    const how = confs ? "ΔE is measured from the CREST search's lowest conformer"
      : "ΔE is measured from the lowest of the stack, once every member's parent has finished";
    const ran = members.length - can.length;
    const dis = can.length ? "" : " disabled";
    const html = `<div class="qe-cond">
        <span class="qe-note">Pass on</span>
        <label class="qe-condf">ΔE ≤ <input class="qe-input qe-condnum" data-f="cde" inputmode="decimal" spellcheck="false"
          placeholder="off" value="${k.de === null ? "" : esc(condNum(k.de))}"${dis}
          aria-label="Pass only what lies within this many kcal/mol of the lowest; empty for no window"> kcal/mol</label>
        <span class="qe-note">·</span>
        <label class="qe-condf">top <input class="qe-input qe-condnum" data-f="ctop" inputmode="numeric" spellcheck="false"
          placeholder="off" value="${k.top > 0 ? k.top : ""}"${dis}
          aria-label="Pass only this many of the lowest; empty for all"></label>
      </div>
      <p class="hint qe-condh">${esc(how + "; the rest end filtered. A filtered one goes back to pending when this changes."
        + (k.mixed ? " The members carry different conditions: a change here sets it on all of them." : "")
        + (!can.length ? " Every member has run: the condition stays as it ran."
          : ran ? ` ${ran} that ${ran === 1 ? "has" : "have"} run keep${ran === 1 ? "s" : ""} the condition ${ran === 1 ? "it" : "they"} ran under.` : ""))}</p>`;
    const wire = (/** @type {HTMLElement} */ host) => {
      const de = /** @type {any} */ (host.querySelector('[data-f="cde"]'));
      const top = /** @type {any} */ (host.querySelector('[data-f="ctop"]'));
      /** @param {string} field @param {any} v */
      const send = (field, v) => {
        const names = membersNow().filter(takes)
          .filter((/** @type {any} */ c) => (field === "filter_de" ? condOf([c]).de : condOf([c]).top) !== v)
          .map((/** @type {any} */ c) => c.name);
        if (!names.length) return;
        const u = updates(names, () => ({ [field]: v }));
        apply(u.ops, u.inv, "the condition change");
      };
      const back = () => { _inspSig = ""; renderInspector(); };
      if (de) {
        de.addEventListener("focus", () => de.select());
        de.addEventListener("change", () => {
          const t = de.value.trim().replace(",", ".");
          const v = t === "" ? null : Number(t);
          if (v !== null && (!isFinite(v) || v < 0)) { NB.fail("The ΔE window is a number of kcal/mol, 0 or more (empty: no window)."); de.blur(); back(); return; }
          send("filter_de", v === null ? null : Math.round(v * 100) / 100);
        });
      }
      if (top) {
        top.addEventListener("focus", () => top.select());
        top.addEventListener("change", () => {
          const t = top.value.trim();
          const v = t === "" ? 0 : Number(t);
          if (!isFinite(v) || v < 0 || Math.round(v) !== v) { NB.fail("Top N is a whole number, 1 or more (empty: all pass)."); top.blur(); back(); return; }
          send("filter_top", v);
        });
      }
    };
    return { html: html, wire: wire };
  }

  /** A system's panel: its name, charge, multiplicity and structures --
   *  every row has one, not only an empty one (the row's caption on the
   *  canvas, or *Structures…* on a block, selects it). A structure is added
   *  from a file or pasted, viewed on a stage, checked (check_structure),
   *  removed; the calculations already made keep the coordinates they were
   *  given. @param {HTMLElement} host @param {any} L */
  async function inspectSystem(host, L) {
    const tok = ++_inspTok;
    const s = L.sys;
    const empty = !L.calcs.length;
    const fixed = !!L.started;
    const sf = await systemFull(s.id);
    if (!host.isConnected || !_sel || _sel.t !== "sys" || _sel.key !== L.key || tok !== _inspTok
        || NB.build.target() !== null) return;
    if (_sysStage) { _sysStage.destroy(); _sysStage = null; }
    const inputs = (sf && sf.inputs) || s.inputs || [];
    host.innerHTML = `
      <div class="qe-ititle">
        <input class="qe-sysname" data-f="sysname" value="${esc(s.name || "")}" spellcheck="false"
          style="width:${Math.max(String(s.name || "").length, 2) + 0.5}ch"
          aria-label="System name" title="${esc(renameTip(L))}">
      </div>
      <div class="qe-cm">
        <label>charge <input class="qe-num" data-f="charge" value="${esc(String(s.charge))}" inputmode="numeric" spellcheck="false"${fixed ? " readonly" : ""}></label>
        <label>mult <input class="qe-num" data-f="mult" value="${esc(String(s.multiplicity))}" inputmode="numeric" spellcheck="false"${fixed ? " readonly" : ""}></label>
        <span class="qe-note">${empty ? "nothing queued yet" : L.calcs.length + " calculation" + (L.calcs.length === 1 ? "" : "s")}</span>
      </div>
      <div class="qe-inputs">${inputs.length
        ? inputs.map((/** @type {any} */ i, /** @type {number} */ k) => {
            const n = xyzAtoms(String(i.xyz || ""));
            return `<div class="qe-saveas" data-in="${k}">
              <span class="qe-note">${esc(i.label || "structure " + (k + 1))}${n ? " · " + n + " atoms" : ""}</span>
              <button class="btn btn-sm btn-ghost" type="button" data-a="view" data-i="${k}"
                aria-label="${esc("View and check " + (i.label || "structure " + (k + 1)))}">View</button>
              <button class="btn btn-sm btn-ghost" type="button" data-a="rminput" data-i="${k}"
                aria-label="${esc("Remove " + (i.label || "structure " + (k + 1)) + " from the row")}">Remove</button>
            </div>`;
          }).join("")
        : `<span class="qe-note">No structure yet.</span>`}</div>
      <div class="qe-stage" data-x="stage" hidden>
        <canvas class="molcv" data-v="syscv" aria-label="The structure picked above"></canvas>
      </div>
      <div data-x="check" aria-live="polite"></div>
      <div class="qe-saveas" data-acts="1">
        <button class="btn btn-sm" type="button" data-a="addinput">Add a structure…</button>
        <button class="btn btn-sm" type="button" data-a="pasteinput">Paste coordinates…</button>
        ${empty ? `<button class="btn btn-sm btn-danger" type="button" data-a="rmsys">Remove system</button>` : ""}
      </div>
      ${empty ? `<div class="qe-saveas">
        <button class="btn btn-sm btn-ghost" type="button" data-a="wfpaste"${_clip ? "" : " disabled"}
          title="${_clip ? "Ctrl+V: " + esc(wfSummary(_clip)) : "Nothing copied yet"}">Paste workflow</button>
      </div>
      ${wfUseButtons()}
      <p class="hint">Press the + slot for the add menu (a level and a step type, a workflow, or a manual build), or drop a level on it. A copied or saved workflow goes here as the row's first steps.</p>`
      : `<p class="hint">A calculation keeps the coordinates it was given. A structure added here is offered in the Geometry list of the row's blocks.</p>`}`;
    const q = (/** @type {string} */ x) => /** @type {any} */ (host.querySelector(x));
    const wp = q('[data-a="wfpaste"]');
    if (wp) wp.addEventListener("click", () => paste());
    wireWfUse(host, () => [laneByKey(L.key) || L]);
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
    q('[data-a="addinput"]').addEventListener("click", () => addInput(liveL()));
    q('[data-a="pasteinput"]').addEventListener("click", () => addInput(liveL(), true));
    const rm = q('[data-a="rmsys"]');
    if (rm) rm.addEventListener("click", () => removeSystem(liveL()));
    host.querySelectorAll('[data-a="rminput"]').forEach(b => b.addEventListener("click", () =>
      removeInput(liveL(), int(b.getAttribute("data-i")))));
    host.querySelectorAll('[data-a="view"]').forEach(b => b.addEventListener("click", async () => {
      const i = inputs[int(b.getAttribute("data-i"))];
      if (!i) return;
      host.querySelectorAll('[data-a="view"]').forEach(x => x.setAttribute("aria-pressed", String(x === b)));
      const box = q('[data-x="stage"]');
      box.hidden = false;
      if (!_sysStage) _sysStage = MOL.mount(q('[data-v="syscv"]'), { labels: false });
      _sysStage.setAtoms(MOL.parseXyz(String(i.xyz || "")));
      const ls = liveL().sys || s;
      const r = await NB.call("check_structure", String(i.xyz || ""), int(ls.charge), int(ls.multiplicity) || 1);
      const out = q('[data-x="check"]');
      if (!out || !out.isConnected) return;
      if (!r || r.error) { out.innerHTML = `<p class="qe-note" data-tone="err">${esc(why(r, "Could not check the structure"))}</p>`; return; }
      const mult = int(ls.multiplicity) || 1;
      const fits = r.electrons != null && r.electrons % 2 === (mult - 1) % 2;
      out.innerHTML = (r.issues || []).map((/** @type {any} */ x) =>
          `<div class="finding ${x.level === "error" ? "err" : "warn"}"><span>${esc(x.message)}</span></div>`).join("")
        + `<div class="factline">
            <span>formula <b>${esc(r.formula || "—")}</b></span>
            ${r.electrons != null ? `<span>electrons <b>${r.electrons}</b></span>` : ""}
            <span>fragments <b>${esc(String(r.n_fragments))}</b></span>
            ${r.electrons != null ? `<span>charge ${int(ls.charge)} / mult ${mult} <b class="${fits ? "ok" : "err"}">${
              fits ? "consistent" : "impossible"}</b></span>` : ""}</div>`;
    }));
  }

  // ------------------------------------------------------------ editing

  /** A system name as ORCA and the file system will take it. @param {string} v */
  const cleanName = (v) => String(v || "").trim().replace(/[\\/:*?"<>|\s&,;=]/g, "-");

  /** What renaming a row does, said on its name field. Once something in
   *  it has run, that keeps its name and the folder it ran in (the store's
   *  update_system); what has not run follows the new name, folder and all.
   *  @param {any} L */
  function renameTip(L) {
    const ran = L.started || L.calcs.some((/** @type {any} */ c) => c.state === "cancelled" && !!c.output_path);
    return ran
      ? "Rename the row. Calculations that have not run follow; the ones that have keep their names and folders."
      : "Rename the row. Every calculation name follows.";
  }

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
      // "ref:<name>[:<point>]" -- a point is irc_f / irc_b or conf:<k>, which
      // holds a colon itself; a name never does (cleanName)
      const rest = key.slice(4);
      const pt = /:(irc_f|irc_b|conf:\d+|conf:all)$/.exec(rest);
      patch = { geometry_source: "reference", xyz: "", ref_name: pt ? rest.slice(0, -pt[0].length) : rest,
                ref_point: pt ? pt[1] : "" };
    } else if (key.indexOf("conf:") === 0) {
      // a conformer of a CREST search, asked by its number
      const cr = NB.queue.find((/** @type {any} */ x) => x.name === key.slice(5));
      const ch = cr ? await askConformers(cr, { one: true, at: confOf(c.ref_name === cr.name ? c.ref_point : "") || 1 }) : null;
      // cancelled: the select goes back to what the block has
      if (!ch) { _inspSig = ""; render(); return; }
      patch = { geometry_source: "reference", xyz: "", ref_name: cr.name, ref_point: "conf:" + ch.k };
    } else if (key.indexOf("pick:") === 0) {
      // a point of an IRC's path (or one of its ends), asked
      const irc = NB.queue.find((/** @type {any} */ x) => x.name === key.slice(5));
      const ends = irc ? await askIrcStructure(irc, false) : null;
      if (!ends || !ends.length) { render(); return; }
      const e = ends[0];
      patch = e.xyz ? { geometry_source: "direct", xyz: e.xyz, ref_name: "", ref_point: "" }
        : { geometry_source: "reference", xyz: "", ref_name: irc.name, ref_point: e.pt };
    } else return;
    // an `after` that is gone (a "Remove only this") goes with the new
    // choice: it would keep the calculation from running
    if (c.after && !NB.queue.some((/** @type {any} */ x) => x.name === c.after)) patch.after = "";
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
    if (!isSettings(kind) && text.indexOf(GEOM) < 0) { NB.fail("A set needs the " + GEOM + " placeholder."); return; }
    if (!isSettings(kind) && !hasJobLine(text)) {
      NB.fail(`A level needs ${JOB} on its ! line, where the job keyword goes: put it in place of ${
        jobKindOf(text) ? "the job keyword" : "a job keyword"} and save again.`);
      return;
    }
    const r = await NB.call("save_inp_set", JSON.stringify({ name: name, kind: kind, text: text }));
    if (!r || r.ok !== true) { NB.fail(why(r, "Could not save the set")); return; }
    await loadSets();
    NB.toast(`Saved "${name}" as ${kind === "ORCA" ? "a level" : "a " + setWord(kind)}.`, "ok");
    if (kind === n.setKind) {
      const set = r.set || setByName(name, kind);
      const u = updates(n.members.map((/** @type {any} */ c) => c.name),
                        () => ({ raw_text: text, is_raw: true, set_name: name, set_id: set ? set.id : "" }));
      await apply(u.ops, u.inv, "the set change");
    } else render();
  }

  /** The Type of a level's block: its kind, the job {{JOB}} becomes. Only
   *  this block changes -- what hangs off it, and how a new step after it is
   *  shaped (an IRC's _F / _B pair), are left as they are. A block still
   *  named after its old type (Ltyr_Freq) takes the new one's name
   *  (Ltyr_IRC) when nothing refers to it by name.
   *  @param {any} n @param {string} kind */
  async function setType(n, kind) {
    if (!n.editable || !kind || kind === n.kind) return;
    const c0 = n.members[0];
    let rename = "";
    if (!n.stack && n.lane.sys) {
      const base = String(n.lane.sys.name || "") + SEP;
      const m = c0.name.indexOf(base) === 0 ? /^([A-Za-z-]+?)(\d*)$/.exec(c0.name.slice(base.length)) : null;
      const used = NB.queue.some((/** @type {any} */ x) => x.name !== c0.name
        && (x.after === c0.name || (x.geometry_source === "reference" && x.ref_name === c0.name)));
      if (m && m[1] === SUFFIX[n.kind] && SUFFIX[kind] && SUFFIX[kind] !== m[1] && !used) {
        rename = (await avoidDisk(x => ({ names: [freeName(base + SUFFIX[kind], x)] }))).names[0];
      }
    }
    const ops = [], inv = [];
    n.members.forEach((/** @type {any} */ c) => {
      ops.push({ op: "update_calc", name: c.name, calc: rename ? { kind: kind, name: rename } : { kind: kind } });
      inv.push({ op: "update_calc", name: rename || c.name, calc: rename ? { kind: c.kind, name: c.name } : { kind: c.kind } });
    });
    apply(ops, inv, "the type change");
  }

  /** Rename one calculation (its own name names its folder): what runs after
   *  it or takes its geometry follows in the same batch. Refused when one
   *  of those has started -- a started calculation keeps what it ran.
   *  @param {any} c @param {string} value the whole new name */
  async function renameCalc(c, value) {
    const nm = cleanName(value);
    const back = () => { _inspSig = ""; renderInspector(); };
    if (!nm || nm === c.name) { back(); return; }
    if (NB.queue.some((/** @type {any} */ x) => x !== c && lower(x.name) === lower(nm))) {
      NB.fail(`There is already a calculation named '${nm}'.`); back(); return;
    }
    const refs = NB.queue.filter((/** @type {any} */ x) => x.name !== c.name
      && (x.after === c.name || (x.geometry_source === "reference" && x.ref_name === c.name)));
    const fixed = refs.find((/** @type {any} */ x) => EDITABLE.indexOf(x.state) < 0);
    if (fixed) { NB.fail(`'${fixed.name}' has run from '${c.name}' and keeps that name; rename is not possible.`); back(); return; }
    if ((await existingOutputs([nm])).length) {
      NB.toast(`The workspace already holds output under '${nm}'. Running it overwrites that.`, "warn");
    }
    /** @type {any[]} */ const ops = [{ op: "update_calc", name: c.name, calc: { name: nm } }];
    /** @type {any[]} */ const inv = [{ op: "update_calc", name: nm, calc: { name: c.name } }];
    refs.forEach((/** @type {any} */ x) => {
      /** @type {any} */ const p = {}, q = {};
      if (x.after === c.name) { p.after = nm; q.after = c.name; }
      if (x.geometry_source === "reference" && x.ref_name === c.name) { p.ref_name = nm; q.ref_name = c.name; }
      ops.push({ op: "update_calc", name: x.name, calc: p });
      inv.push({ op: "update_calc", name: x.name, calc: q });
    });
    if (!(await apply(ops, inv, "the rename"))) back();
  }

  /** Move a calculation's run order one step within its row -- before the
   *  previous calculation that can still run, or after the next -- through
   *  an `order` op. The order is the queue's priority: what is first runs
   *  first when both are ready. A step past what it depends on (or what
   *  depends on it) is refused: dependencies are never broken.
   *  @param {any} c @param {number} dir -1 earlier, +1 later */
  async function moveRun(c, dir) {
    if (EDITABLE.indexOf(c.state) < 0) { NB.toast(`${c.name} has started; its place no longer changes what runs.`); return; }
    const n = _M ? _M.nodeOf.get(c.name) : null;
    if (!n) return;
    const lane = n.lane.calcs;
    const i = lane.findIndex((/** @type {any} */ x) => x.name === c.name);
    let j = i + dir;
    while (j >= 0 && j < lane.length && EDITABLE.indexOf(lane[j].state) < 0) j += dir;
    if (j < 0 || j >= lane.length) {
      NB.toast(`${c.name} already runs ${dir < 0 ? "first" : "last"} of what can still run in its row.`);
      return;
    }
    const other = lane[j];
    const first = dir < 0 ? other : c, second = dir < 0 ? c : other;
    if (dependentsOf(first.name).has(second.name)) {
      NB.toast(`${second.name} depends on ${first.name}, so it cannot run before it.`);
      return;
    }
    const before = NB.queue.map((/** @type {any} */ x) => x.name);
    const names = before.filter((/** @type {string} */ x) => x !== c.name);
    const at = names.indexOf(other.name);
    names.splice(dir < 0 ? at : at + 1, 0, c.name);
    await apply([{ op: "order", names: names }], [{ op: "order", names: before }], "the run order change");
  }

  /** "Duplicate": a copy of one calculation with its whole setup (config,
   *  level, geometry, what it runs after), queued to run, on the next free
   *  row of its column. Its name is `<name>_copy`, clear of the queue and of
   *  the workspace's folders. @param {any} n @param {any} c */
  async function duplicateBlock(n, c) {
    await fullCalcs([c.name]);
    if (!_full.has(c.name)) { NB.fail(`Could not read '${c.name}'.`); return; }
    const L = n.lane;
    const pick = await avoidDisk(x => ({ names: [freeName(c.name + SEP + "copy", x)] }));
    const name = pick.names[0];
    if (!(await guardOverwrite([name]))) return;
    let row = n.row;
    while (L.nodes.some((/** @type {any} */ x) => x.col === n.col && x.row === row)) row++;
    const nodeId = L.sys ? newId("n") : "";
    const calc = calcDict(c.name, {
      name: name, state: "pending", message: "", approved: false,
      output_path: "", pid: null, create_time: null,
      node: nodeId, col: L.sys ? n.col : 0, row: L.sys ? row : 0,
    });
    const before = NB.queue.map((/** @type {any} */ x) => x.name);
    const order = before.slice();
    order.splice(order.indexOf(c.name) + 1, 0, name);
    await applySel([{ op: "add_calc", calc: calc }, { op: "order", names: order }],
                   [{ op: "remove_calc", name: name }], "the duplicate",
                   { t: "node", key: L.sys ? L.key + "/" + nodeId : "c:" + name, m: 0 });
  }

  /** "Show folder": the calculation's run folder in the file manager. The
   *  backend knows where it is (a system's calculations run in its own
   *  folder, and one that has run keeps the folder it ran in), so the page
   *  names the calculation, never a path. One that has not run has none yet,
   *  and says so. @param {any} c */
  async function showFolder(c) {
    const r = await NB.call("show_calc_folder", c.name);
    if (!r || r.ok !== true) {
      NB.fail(STARTED.indexOf(c.state) < 0 && !c.output_path
        ? `${c.name} has no folder yet: it has not run.`
        : why(r, "Could not show the folder of " + c.name));
    }
  }

  /** "Edit in the builder" on an MLIP or CREST block (made from a set or
   *  in the builder): the Guided builder in that backend's form, seeded from
   *  the block's whole config, whose Save is one update_calc patch through
   *  the undoable batch -- the block keeps its place in the graph (the
   *  builder sends its graph fields back) and, saved from the form, is no
   *  longer its set's (set_name and raw_text go: what the form holds is more
   *  than a set's `key value` lines say). A rename is left to the builder's
   *  own update_calc, which carries the references with it.
   *  @param {string} name */
  async function openSettingsBuilder(name) {
    await fullCalcs([name]);
    openBuilder(name, undefined, {
      save: async (/** @type {any} */ payload) => {
        if (!payload || payload.name !== name) return null;
        const patch = Object.assign({}, payload);
        ["name", "state", "message"].forEach(k => { delete patch[k]; });
        const u = updates([name], () => patch);
        const ok = await apply(u.ops, u.inv, "the builder edit of " + name);
        return ok ? { ok: true } : { ok: false, error: _refusal ? _refusal.text : "The queue refused that change." };
      },
    });
  }

  /** What Level mode needs from the editor: the Type kinds a level's block
   *  may run (get_inp_sets `jobs`) and the library's ORCA levels. */
  function levelCtx() {
    return { kinds: typeKinds().map(k => [k, TYPE_LABEL[k] || KIND_LABEL[k] || k]),
             sets: _sets.filter(s => s.kind === "ORCA").map(s => ({ id: s.id, name: s.name, text: s.text })) };
  }
  /** "Edit options in the builder" on a level's block: the Guided builder in
   *  Level mode (build.js), whose Save is one update_calc patch of this
   *  block -- kind, config, geometry, and its level only when another was
   *  picked -- through the undoable batch. @param {string} name */
  function openLevelBuilder(name) {
    const ctx = Object.assign(levelCtx(), {
      save: async (/** @type {any} */ patch) => {
        if (!NB.queue.some((/** @type {any} */ x) => x.name === name)) return { ok: false, error: `'${name}' is no longer in the queue.` };
        const u = updates([name], () => patch);
        const ok = await apply(u.ops, u.inv, "the options of " + name);
        return { ok: ok, error: ok ? "" : (_refusal ? _refusal.text : "The queue refused that change.") };
      },
    });
    openBuilder(name, null, { level: ctx });
  }

  /** "Clear finished": the done and failed calculations off the list (their
   *  folders stay), as the old queue's Finished group did. One that a
   *  calculation still to run takes its geometry from, or runs after, stays;
   *  a row left with nothing goes with them. Not undoable: the store would
   *  take a finished calculation back as one to run again. */
  function clearFinished() {
    const fin = new Set(NB.queue.filter((/** @type {any} */ c) => FINISHED.indexOf(c.state) >= 0)
      .map((/** @type {any} */ c) => c.name));
    for (let shrank = true; shrank; ) {
      shrank = false;
      NB.queue.forEach((/** @type {any} */ c) => {
        if (fin.has(c.name)) return;
        [c.after, c.geometry_source === "reference" ? c.ref_name : ""].forEach(u => {
          if (u && fin.has(u)) { fin.delete(u); shrank = true; }
        });
      });
    }
    const all = NB.queue.filter((/** @type {any} */ c) => FINISHED.indexOf(c.state) >= 0).length;
    if (!fin.size) {
      NB.toast(all ? "Every finished calculation is still needed: something yet to run starts from it." : "Nothing has finished yet.");
      return;
    }
    const kept = all - fin.size;
    const emptied = NB.systems.filter((/** @type {any} */ s) => {
      const lane = NB.queue.filter((/** @type {any} */ c) => c.system === s.id);
      return lane.length && lane.every((/** @type {any} */ c) => fin.has(c.name));
    });
    NB.modalRaw("Clear finished",
      `<p>${fin.size} finished, failed or filtered calculation${fin.size === 1 ? "" : "s"} leave${fin.size === 1 ? "s" : ""} the list${
        emptied.length ? `, with ${emptied.length === 1 ? "the row it leaves" : "the " + emptied.length + " rows they leave"} empty` : ""}.
        Their folders stay on disk. Cancelled calculations stay: they run again with the queue.${
        kept ? ` ${kept} stay${kept === 1 ? "s" : ""} because a calculation still to run starts from ${kept === 1 ? "it" : "them"}.` : ""}
        This cannot be undone with Ctrl+Z.</p>`,
      [{ label: "Cancel", cls: "btn-ghost", focus: true },
       { label: "Clear", cls: "btn-danger", act: () => {
          /** @type {any[]} */ const ops = [...fin].map(x => ({ op: "remove_calc", name: x }));
          emptied.forEach((/** @type {any} */ s) => ops.push({ op: "remove_system", id: s.id }));
          if (_sel && _sel.t === "node") { const n = selNode(); if (n && n.members.some((/** @type {any} */ c) => fin.has(c.name))) _sel = null; }
          apply(ops, null, "clearing finished calculations");
        } }]);
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
        <button class="btn btn-sm btn-ghost" type="button" data-a="wfsave" title="Keep these steps in the library under Workflows, by name">Save as workflow…</button>
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
    on("wfsave", async () => {
      const r = await buildWorkflow(nodes.map((/** @type {any} */ x) => nodeByKey(x.key) || x));
      if (!r.wf) { NB.fail("Could not save the workflow: " + (r.error || "nothing selected.")); return; }
      saveWorkflowAs(r.wf);
    });
  }

  /** The saved workflows as buttons that put one on the rows an inspector
   *  shows ("" when none is saved). */
  function wfUseButtons() {
    if (!_wfs.length) return "";
    return `<div class="qe-saveas" data-wf="1"><span class="qe-note">Saved workflow</span>${_wfs.map(w =>
      `<button class="btn btn-sm btn-ghost" type="button" data-a="wfuse" data-wf="${esc(w.id)}"
        title="${esc(wfSummary(w))}">${esc(w.name)}</button>`).join("")}</div>`;
  }
  /** @param {HTMLElement} host @param {() => any[]} lanes the rows, read at click time */
  function wireWfUse(host, lanes) {
    host.querySelectorAll('[data-a="wfuse"]').forEach(b => b.addEventListener("click", () => {
      const w = _wfs.find(x => x.id === b.getAttribute("data-wf"));
      if (w) pasteIntoSystem(lanes(), wfOf(w));
    }));
  }

  /** Several rows (Ctrl+click on their names): their charge and
   *  multiplicity, set on all at once, and a workflow put into every empty
   *  one -- what a drop of several .xyz files is followed by.
   *  @param {HTMLElement} host @param {any[]} lanes */
  function inspectRows(host, lanes) {
    const empty = lanes.filter((/** @type {any} */ L) => !L.calcs.length);
    const open = lanes.filter((/** @type {any} */ L) => !L.started);
    const same = (/** @type {string} */ f) => {
      const v = lanes.map((/** @type {any} */ L) => int(L.sys[f]));
      return v.every(x => x === v[0]) ? String(v[0]) : "";
    };
    const fixed = !open.length;
    host.innerHTML = `
      <div class="qe-ititle"><span class="qe-sysname" data-fixed="1">${lanes.length} rows</span></div>
      <div class="qe-cm">
        <label>charge <input class="qe-num" data-f="charge" value="${esc(same("charge"))}" placeholder="mixed" inputmode="numeric" spellcheck="false"${fixed ? " readonly" : ""}></label>
        <label>mult <input class="qe-num" data-f="mult" value="${esc(same("multiplicity"))}" placeholder="mixed" inputmode="numeric" spellcheck="false"${fixed ? " readonly" : ""}></label>
        <span class="qe-note">${open.length < lanes.length ? "rows that have started keep theirs" : "set on every row"}</span>
      </div>
      <div class="qe-inputs">${lanes.map((/** @type {any} */ L) => `<span class="qe-note">${esc(L.name)} · ${
        L.calcs.length ? L.calcs.length + " calculation" + (L.calcs.length === 1 ? "" : "s") : "nothing queued yet"}</span>`).join("")}</div>
      <div class="qe-saveas" data-wf="1">
        <button class="btn btn-sm" type="button" data-a="wfpaste"${_clip && empty.length ? "" : " disabled"}
          title="${!empty.length ? "None of these rows is empty" : _clip ? "Ctrl+V: " + esc(wfSummary(_clip)) : "Nothing copied yet"}">Paste workflow</button>
      </div>
      ${empty.length ? wfUseButtons() : ""}
      <p class="hint">A workflow goes into every empty row here as its first steps, in one step Ctrl+Z undoes; a row that already has steps is left as it is. Ctrl+click a row's name adds or drops it; Escape clears.</p>`;
    const keys = lanes.map((/** @type {any} */ L) => L.key);
    const live = () => keys.map(laneByKey).filter((/** @type {any} */ L) => L && L.sys);
    const wp = /** @type {HTMLElement|null} */ (host.querySelector('[data-a="wfpaste"]'));
    if (wp) wp.addEventListener("click", () => paste());
    wireWfUse(host, live);
    ["charge", "mult"].forEach(k => {
      const el = /** @type {any} */ (host.querySelector(`[data-f="${k}"]`));
      el.addEventListener("focus", () => el.select());
      el.addEventListener("change", () => setRowsChargeMult(live(), k, el.value));
    });
  }
  /** One charge or multiplicity on several rows, in one undoable batch;
   *  a row that has started keeps its own (as a single row's field is
   *  read-only then). @param {any[]} lanes @param {string} k @param {string} v */
  function setRowsChargeMult(lanes, k, v) {
    const n = parseInt(v, 10);
    if (!String(v).trim()) return;
    if (!isFinite(n) || (k === "mult" && n < 1)) {
      NB.fail(k === "mult" ? "Multiplicity is at least 1." : "The charge is a whole number.");
      _inspSig = ""; renderInspector(); return;
    }
    const field = k === "charge" ? "charge" : "multiplicity";
    const ops = [], inv = [];
    lanes.forEach((/** @type {any} */ L) => {
      if (L.started || int(L.sys[field]) === n) return;
      ops.push({ op: "update_system", id: L.sys.id, patch: { [field]: n } });
      inv.push({ op: "update_system", id: L.sys.id, patch: { [field]: int(L.sys[field]) } });
    });
    apply(ops, inv, "the " + k + " change");
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
      <div class="qe-saveas">
        <span class="qe-note"></span>
        <button class="btn btn-sm btn-danger" type="button" data-a="remove"${
          nodes.some((/** @type {any} */ n) => n.state === "running" || n.state === "disconnected")
            ? " disabled" : ""}>Remove all…</button>
      </div>
      <p class="hint">Copy workflow (Ctrl+C) keeps these steps (their types, levels, gates and connections) without the structure. Paste it after a block, into an empty row, or onto new .xyz rows (Ctrl+V). Delete removes them all. Ctrl+click adds or drops a block; Escape clears.</p>`;
    wireWorkflowButtons(host, nodes, L);
    const rm = /** @type {HTMLElement | null} */ (host.querySelector('[data-a="remove"]'));
    if (rm) rm.addEventListener("click", () => requestRemove(removeChoice()));
  }

  /** The states a set put on a block runs again (rerun_calc), behind the
   *  overwrite warning -- P24's one way back from DONE or FAILED. */
  const RERUN = ["done", "failed"];
  /** A block a set can be put on although it has finished or failed: one
   *  member at least done or failed, and each of the others too or still
   *  editable (none running). It then runs again (requestRerun).
   *  @param {any} n */
  function rerunnable(n) {
    return !!n && n.members.some((/** @type {any} */ c) => RERUN.indexOf(c.state) >= 0)
      && n.members.every((/** @type {any} */ c) => RERUN.indexOf(c.state) >= 0 || EDITABLE.indexOf(c.state) >= 0);
  }
  /** Whether a set of the block's kind can land on block `n`: it can still
   *  change, or it has finished or failed (then it is an overwrite, asked
   *  first). @param {any} n */
  function takesSet(n) {
    return !!n && (n.editable || rerunnable(n));
  }

  /** A set put on a finished or failed block: an overwrite, asked every
   *  time (never through the once-only replace answer). Confirmed, each done
   *  or failed member goes back to pending with the set (rerun_calc) and the
   *  output in its folder is overwritten when it runs, never kept; what a
   *  failed one held blocked goes back to pending with it (the store does
   *  that); a member still to run just takes the set. Undo (undo_rerun) puts
   *  the old results back while none of them has started.
   *  @param {any} n @param {any} set */
  function requestRerun(n, set) {
    const again = n.members.filter((/** @type {any} */ c) => RERUN.indexOf(c.state) >= 0);
    const names = again.map((/** @type {any} */ c) => c.name);
    const rest = n.members.filter((/** @type {any} */ c) => RERUN.indexOf(c.state) < 0)
      .map((/** @type {any} */ c) => c.name);
    const k = names.length, one = k === 1;
    const failed = again.filter((/** @type {any} */ c) => c.state === "failed").length;
    const word = !failed ? "finished" : failed === k ? "failed" : "finished or failed";
    const what = setWord(set.kind);
    const them = one ? "it" : "them";
    // what already ran after it keeps its own results (P24): said, not changed
    const kids = NB.queue.some((/** @type {any} */ c) => c.state === "done" && names.indexOf(upOf(c)) >= 0);
    const freed = !!failed && NB.queue.some((/** @type {any} */ c) => c.state === "blocked" && names.indexOf(upOf(c)) >= 0);
    const go = () => {
      const patch = { raw_text: set.text, set_name: set.name, set_id: set.id, is_raw: true };
      const u = updates(rest, () => Object.assign({}, patch));
      const ops = names.map((/** @type {string} */ nm) => ({ op: "rerun_calc", name: nm, calc: patch }))
        .concat(u.ops);
      const inv = u.inv.concat(names.map((/** @type {string} */ nm) => ({ op: "undo_rerun", name: nm })));
      _sel = { t: "node", key: n.key, m: 0 };
      apply(ops, inv, "the overwrite of " + n.short);
    };
    const label = (/** @type {any} */ c) => esc(c.name) + (failed && failed < k ? " (" + c.state + ")" : "");
    NB.modalRaw(one ? `Overwrite a ${word} calculation?` : `Overwrite ${word} calculations?`,
      `<p>${one ? esc(names[0]) + " has " + word + "." : "These have " + word + ":"}</p>
       ${one ? "" : `<div class="names">${NB.nameList(again.map(label))}</div>`}
       <p>Putting the ${what} ${esc(set.name)} on ${them} runs ${them} again: ${
         one ? "it goes" : "they go"} back to pending, and the output in ${one ? "its folder" : "their folders"} is
         overwritten when ${one ? "it runs" : "they run"}${NB.running ? " (the queue is running, so at once)" : ""}.${
         freed ? " What was blocked by " + them + " goes back to pending too." : ""}${
         kids ? " What already ran after " + them + " keeps its own results." : ""}${
         rest.length ? " " + esc(rest.length > 4 ? rest.slice(0, 3).join(", ") + " and " + (rest.length - 3) + " more"
             : rest.join(", ")) + (rest.length === 1 ? " has" : " have")
           + " not run yet and " + (rest.length === 1 ? "takes" : "take") + " the " + what + " too." : ""}</p>`,
      [{ label: "Cancel", cls: "btn-ghost", focus: true },
       { label: "Overwrite", cls: "btn-danger", act: go }]);
  }

  /** Put a set's template on a block, asking the first time; on a finished
   *  block it is an overwrite (requestRerun). @param {any} n @param {any} set */
  function requestReplace(n, set) {
    if (!set || set.kind !== n.setKind) return;
    if (!n.editable) { if (rerunnable(n)) requestRerun(n, set); return; }
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
    const what = setWord(set.kind);
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
  /** "level" / "MLIP set" / "CREST set": what a set of a kind is called @param {string} k */
  const setWord = (k) => (k === "ORCA" ? "level" : k === "CREST" ? "CREST set" : "MLIP set");
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
    if (NB.build.target() !== null) { NB.toast("The builder is open. Close it to edit the set."); return; }
    if (_ed && _ed.id === set.id) { _lastId = set.id; showEditor(); return; }
    guardSwitch(() => { _ed = edFrom(set); _lastId = set.id; showEditor(); });
  }

  /** "+ New" in a kind's group: a new set from that kind's seeded input.
   *  @param {string} kind */
  function newSet(kind) {
    if (NB.build.target() !== null) { NB.toast("The builder is open. Close it to start a set."); return; }
    guardSwitch(() => {
      const text = _defaults[kind] || (kind === "MLIP" ? "model \ndevice auto\nnprocs 1\n"
        : kind === "CREST" ? "method gfn2\newin 6\nthreads 4\n"
        : "! " + JOB + "\n\n* xyz {{CHARGE}} {{MULT}}\n" + GEOM + "\n*\n");
      const name = freeSetName(kind, "New " + setWord(kind));
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
    if (NB.build.target() !== null) { NB.toast("The builder is open. Close it to import a set."); return; }
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
      : !isSettings(kind) && text.indexOf(GEOM) < 0 ? `A level needs ${GEOM} where the coordinates go.`
      : !isSettings(kind) && !hasJobLine(text) ? `A level needs ${JOB} on its ! line, where the job keyword goes (each block's Type fills it in).`
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
       <div class="names">${NB.nameList(names.map(esc))}</div>
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
      q('[data-f="kind"]').textContent = k === "ORCA" ? "ORCA level" : setWord(k);
      q('[data-f="dirty"]').textContent = !d.id ? "· new, not saved" : edDirty() ? "· unsaved changes" : "";
      const mv = q('[data-f="moves"]');
      mv.hidden = k === d.kind;
      mv.textContent = k === d.kind ? "" : `This text reads as ${k === "MLIP" ? "MLIP settings" : k === "CREST" ? "CREST settings" : "an ORCA input"}: saved, it moves to the ${SET_GROUP[k]} group.`;
      if (k === "ORCA" && !mv.textContent && !hasJobLine(d.text)) {
        mv.hidden = false;
        mv.textContent = `Put ${JOB} on the ! line where the job keyword goes: each block's Type fills it in.`;
      }
      const ph = q('[data-f="ph"]');
      const mlipPh = "One setting per line: model, device (auto, cpu, cuda), nprocs, env.";
      ph.textContent = k === "MLIP" ? mlipPh
        : k === "CREST" ? "One setting per line: method (gfn2, gfn1, gfnff ...), solvent, solvent_model, ewin (kcal/mol), threads, preset, nci, env. "
          + "After a CREST block, a step asks how many conformers to run it on."
        : JOB + " the block's job, from its Type (TightOpt, OptTS, Freq, IRC, SP ...; ORCAdesk adds the %geom / %irc block that job needs unless the text has its own) · "
          + GEOM + " the block's coordinates · {{CHARGE}} {{MULT}} its charge and multiplicity · {{HESS}} optional: an IRC reads the Hessian of the step before it by itself · "
          + "{{NPROCS}} {{MAXCORE}} sized to the computer it runs on: cores ÷ calculations at once, memory ÷ cores (Settings). A number stays as written.";
      // the model's licence, as the builder's picker says it: some MACE
      // weights are for academic, non-commercial use only
      if (k === "MLIP") {
        const text = d.text;
        NB.build.licenseNote(mlipModelOf(text)).then(t => {
          if (t && d.text === text && ph.isConnected) ph.innerHTML = esc(mlipPh) + " " + t;
        });
      }
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
    for (let i = 2; sysNames.has(lower(nm)); i++) nm = base + SEP + i;
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

  /** Add the next step from a set, on a slot: a level runs `want` (the add
   *  menu's choice), else the natural next step.
   *  @param {any} g the slot @param {any} set @param {string} [want] */
  async function addFromSet(g, set, want) {
    if (!g || !set) return;
    if (g.before) { await insertBefore(g, set, want); return; }
    let P = g.parent;
    let pre = { ops: /** @type {any[]} */ ([]), inv: /** @type {any[]} */ ([]) };
    let sys = g.lane.sys;
    if (!sys) {
      const a = await adopt(P);
      if (!a) return;
      pre = a; sys = a.sys; P = a.parent;
    }
    // a level runs what the add menu chose, else the natural next step (Opt
    // to start a row, Freq after Opt, TS Freq after OptTS, IRC after a TS
    // Freq, SP after an IRC or anything else); the block's Type changes it
    const kind = settingsKind(set.kind) || want || nextKind(P ? P.kind : "");
    const nodeId = newId("n");
    // the members, as tails on one base name: after an IRC the structure(s)
    // picked (both ends, one, or a point on its path), one per member of a
    // stack, or a single calculation
    /** @type {{tail: string, after: string, ref: string, pt: string, xyz: string}[]} */
    let tails;
    // the energy condition the conformer picker put on the edge (null: none)
    /** @type {number|null} */ let fde = null;
    if (P && !P.stack && P.kind === "crest_conf" && kind !== "crest_conf") {
      // after a CREST search: how many conformers (one member each, _c1 ...
      // _cK, by reference to the search's k-th conformer) and the condition
      const p = P.members[0].name;
      const ch = await askConformers(P.members[0], { kind: kind });
      if (!ch) return;
      fde = ch.de;
      // every conformer it finds: one placeholder, expanded by the engine
      tails = ch.all ? [{ tail: "", after: p, ref: p, pt: CONF_ALL, xyz: "" }]
        : Array.from({ length: ch.k }, (_, i) =>
          ({ tail: SEP + CONF_TAIL + (i + 1), after: p, ref: p, pt: "conf:" + (i + 1), xyz: "" }));
    } else if (P && !P.stack && P.kind === "irc" && kind !== "irc") {
      const p = P.members[0].name;
      const ends = await askIrcStructure(P.members[0], true);
      if (!ends) return;
      tails = ends.map(e => ({ tail: e.tail, after: p, ref: e.xyz ? "" : p, pt: e.pt, xyz: e.xyz }));
    } else if (P && P.stack) {
      tails = P.members.map((/** @type {any} */ c) =>
        ({ tail: tailOf(c.name), after: c.name, ref: c.name, pt: "", xyz: "" }));
    } else if (P) {
      tails = [{ tail: "", after: P.members[0].name, ref: P.members[0].name, pt: "", xyz: "" }];
    } else {
      tails = [{ tail: "", after: "", ref: "", pt: "", xyz: "" }];
    }
    // a base whose every member name is free (Ltyr_SP, else Ltyr_SP2 ...)
    // -- and clear of folders in the workspace that already hold output
    const root = String(sys.name || "") + SEP + (SUFFIX[kind] || "step");
    const named = await avoidDisk(extra => {
      const taken = new Set(NB.queue.map((/** @type {any} */ c) => lower(c.name)));
      extra.forEach(x => taken.add(x));
      let base = root;
      // a placeholder's copies are named base_c1 ...: those must be free too
      for (let i = 2; tails.some(t => taken.has(lower(base + t.tail))
        || (t.pt === CONF_ALL && confTailTaken(taken, base + t.tail))); i++) base = root + i;
      return { names: tails.map(t => base + t.tail) };
    });
    if (!(await guardOverwrite(named.names))) return;
    const members = tails.map((t, i) => ({ name: named.names[i], after: t.after, ref: t.ref, pt: t.pt, xyz: t.xyz }));
    let xyz = "";
    if (!P && sys.id) {
      const sf = await systemFull(sys.id);
      xyz = sf && sf.inputs && sf.inputs[0] ? String(sf.inputs[0].xyz || "") : "";
    }
    const adds = members.map(m => ({ op: "add_calc", calc: {
      name: m.name, kind: kind, charge: int(sys.charge), multiplicity: int(sys.multiplicity) || 1,
      geometry_source: m.ref ? "reference" : "direct", xyz: m.ref ? "" : (m.xyz || xyz),
      ref_name: m.ref, ref_point: m.pt,
      // an MLIP set is sent as raw text too: the store reads its `key value`
      // lines into the MLIP config (store.calc_from_dict) and keeps the text
      is_raw: true, raw_text: set.text, set_name: set.name, set_id: set.id,
      system: sys.id, node: nodeId, col: g.c, row: g.r, after: m.after,
      gate: "auto", auto_pass: true, approved: false, filter_de: fde, filter_top: 0,
      config: {}, state: "pending", message: "",
    } }));
    const fresh = members.map(m => m.name);
    const order = { op: "order", names: orderWith(NB.queue.map((/** @type {any} */ c) => c.name),
                                                  g.lane, g.c, g.r, fresh) };
    const ops = pre.ops.concat(adds, [order]);
    const inv = fresh.map(n => ({ op: "remove_calc", name: n })).concat(pre.inv);
    await applySel(ops, inv, "the new " + (TYPE_LABEL[kind] || KIND_LABEL[kind] || kind), { t: "node", key: "s:" + sys.id + "/" + nodeId, m: 0 });
  }

  /** A step put BEFORE block B (its before slot, beforeSlotOf). Each member
   *  of B gets one, named as a step the editor adds is (B's tail kept: _F /
   *  _B), that takes the member's place in the chain: what it ran after,
   *  the approval gate on that connection, the structure it started from (a
   *  reference with its end, or its own coordinates) and its charge and
   *  multiplicity. The member then runs after the new step, on its final
   *  geometry. B and what hangs off it move one column right -- every block
   *  of the row from B's column on, when one of them would land on a block
   *  that stays -- and the new block takes B's cell. One batch: Ctrl+Z
   *  undoes it while B has not started.
   *  @param {any} g the before slot @param {any} set @param {string} [want] */
  async function insertBefore(g, set, want) {
    const B = g.before, L = g.lane;
    if (!B.editable) { NB.fail(`${B.short} has started: a step goes before a calculation that has not.`); return; }
    let pre = { ops: /** @type {any[]} */ ([]), inv: /** @type {any[]} */ ([]) };
    let sys = L.sys;
    if (!sys) {
      // a standalone chain becomes a row first, each block where it is drawn
      const a = await adopt(B);
      if (!a) return;
      pre = a; sys = a.sys;
    }
    const names = B.members.map((/** @type {any} */ c) => c.name);
    await fullCalcs(names);
    const full = names.map((/** @type {string} */ nm) => { const f = _full.get(nm); return f && f.calc ? f.calc : null; });
    const lost = names.filter((/** @type {string} */ nm, /** @type {number} */ i) => !full[i]);
    if (lost.length) { NB.fail(`Could not read ${lost.join(", ")}.`); return; }
    const kind = settingsKind(set.kind) || want || "opt";
    const root = String(sys.name || "") + SEP + (SUFFIX[kind] || "step");
    const tails = B.members.map((/** @type {any} */ c) => (B.stack ? tailOf(c.name) : ""));
    const named = await avoidDisk(extra => {
      const taken = new Set(NB.queue.map((/** @type {any} */ c) => lower(c.name)));
      extra.forEach(x => taken.add(x));
      let base = root;
      for (let i = 2; tails.some((/** @type {string} */ t) => taken.has(lower(base + t))); i++) base = root + i;
      return { names: tails.map((/** @type {string} */ t) => base + t) };
    });
    if (!(await guardOverwrite(named.names))) return;
    const fresh = named.names;

    // B and what hangs off it move one column right; the whole row from B's
    // column on when one of them would land on a block that stays
    const down = new Set([B]);
    for (let grew = true; grew;) {
      grew = false;
      L.edges.forEach((/** @type {any} */ e) => { if (down.has(e.from) && !down.has(e.to)) { down.add(e.to); grew = true; } });
    }
    const stay = L.nodes.filter((/** @type {any} */ n) => !down.has(n));
    const hit = [...down].some(n => stay.some((/** @type {any} */ s) => s.col === n.col + 1 && s.row === n.row));
    const moved = hit ? L.nodes.filter((/** @type {any} */ n) => n.col >= B.col) : [...down];

    const nodeId = newId("n");
    /** @type {any[]} */ const shift = [], back = [], adds = [], links = [], unlink = [];
    moved.forEach((/** @type {any} */ n) => n.members.forEach((/** @type {any} */ c) => {
      shift.push({ op: "place_calc", name: c.name, place: { col: n.col + 1 } });
      back.push({ op: "place_calc", name: c.name, place: { col: n.col } });
    }));
    B.members.forEach((/** @type {any} */ c, /** @type {number} */ i) => {
      const f = full[i];
      const byRef = f.geometry_source === "reference";
      const cond = condOf([Object.assign({}, f, c)]);
      adds.push({ op: "add_calc", calc: {
        name: fresh[i], kind: kind, charge: int(c.charge), multiplicity: int(c.multiplicity) || 1,
        geometry_source: byRef ? "reference" : "direct", xyz: byRef ? "" : String(f.xyz || ""),
        ref_name: byRef ? String(f.ref_name || "") : "", ref_point: byRef ? String(f.ref_point || "") : "",
        is_raw: true, raw_text: set.text, set_name: set.name, set_id: set.id,
        system: sys.id, node: nodeId, col: B.col, row: B.row, after: String(f.after || ""),
        // the gate and the energy condition were on the connection into B:
        // they stay on that connection
        gate: f.gate === "approval" ? "approval" : "auto", auto_pass: f.auto_pass !== false, approved: !!f.approved,
        filter_de: cond.de, filter_top: cond.top,
        config: {}, state: "pending", message: "",
      } });
      links.push({ op: "update_calc", name: c.name, calc: {
        after: fresh[i], geometry_source: "reference", ref_name: fresh[i], ref_point: "", xyz: "",
        gate: "auto", auto_pass: true, approved: false, filter_de: null, filter_top: 0 } });
      unlink.push({ op: "update_calc", name: c.name, calc: {
        after: String(f.after || ""), geometry_source: byRef ? "reference" : "direct",
        ref_name: String(f.ref_name || ""), ref_point: String(f.ref_point || ""), xyz: byRef ? "" : String(f.xyz || ""),
        gate: f.gate === "approval" ? "approval" : "auto", auto_pass: f.auto_pass !== false, approved: !!f.approved,
        filter_de: cond.de, filter_top: cond.top } });
    });
    // the new steps run just before B's
    const cur = NB.queue.map((/** @type {any} */ c) => c.name);
    const order = cur.slice();
    order.splice(Math.min(...names.map((/** @type {string} */ nm) => cur.indexOf(nm))), 0, ...fresh);
    const ops = pre.ops.concat(shift, adds, links, [{ op: "order", names: order }]);
    const inv = unlink.concat(fresh.map((/** @type {string} */ n) => ({ op: "remove_calc", name: n })), back, pre.inv);
    await applySel(ops, inv, "the new " + (TYPE_LABEL[kind] || KIND_LABEL[kind] || kind) + " before " + B.short,
                   { t: "node", key: "s:" + sys.id + "/" + nodeId, m: 0 });
  }

  /** The builder, hosted over the lanes and the inspector (#qe-build). @param {string} target "" for a new one
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
    const sysName = String(sys.name || "");
    // named as a step the editor adds is (Ltyr_Opt, Ltyr_Opt2 ...): the row's
    // name and the kind's suffix, clear of the queue and of folders holding
    // output -- one name per kind, so the Name follows the builder's Type
    // while it is still the one given here
    const kinds = Object.keys(SUFFIX);
    const named = await avoidDisk(x => ({ names: kinds.map(k => freeName(sysName + SEP + SUFFIX[k], x)) }));
    /** @type {Object<string, string>} */
    const names = {};
    kinds.forEach((k, i) => { names[k] = named.names[i]; });
    // the library's levels: a manual build may start from one (Level mode)
    const lv = levelCtx();
    openBuilder("", {
      name: names.opt, kind: "opt", is_raw: false, raw_text: "",
      xyz: xyz, geometry_source: parent ? "reference" : "direct", ref_name: parent ? parent.name : "",
      charge: int(sys.charge), multiplicity: int(sys.multiplicity) || 1, config: {},
    }, {
      slot: g.key,
      levels: lv.sets.length && lv.kinds.length ? lv : null,
      where: parent ? "after " + parent.name + " in " + sys.name : "first in " + sys.name,
      names: names,
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

  // ---- the + slot's add menu

  /** The level the add menu starts on: the one the block before runs at (a
   *  Freq has to match its Opt), else the one used last, else the first.
   *  @param {any} P the block before, or null @param {any[]} levels */
  function menuLevel(P, levels) {
    const c = P ? P.members[0] : null;
    const own = c && c.set_name && setKindOf(c.kind) === "ORCA" ? levels.find(s => s.name === c.set_name) : null;
    return own || levels.find(s => s.id === _lastLevel) || levels[0] || null;
  }
  /** The key each common step answers to in the menu, after `P`.
   *  @param {any} P @returns {Object<string, string>} */
  function menuKeys(P) {
    return { o: "opt", t: "ts_opt", f: P && P.kind === "ts_opt" ? "ts_freq" : "freq", i: "irc", s: "sp" };
  }

  /** Press + on a slot: a small menu over the canvas instead of the whole
   *  builder -- the level, the step to add with it (the natural next one
   *  first, Enter; O / T / F / I / S for the common ones), an MLIP
   *  pre-optimization, a saved or the copied workflow, or the manual build
   *  (M). Drawn fixed, outside the lanes, so a redraw of the canvas does not
   *  take it away; the slot is looked up again when a choice is made.
   *  @param {any} g the slot @param {HTMLElement} anchor */
  function openAddMenu(g, anchor) {
    closeAddMenu();
    const P = g.parent;
    // before a block (insertBefore): a level and its step only -- a workflow
    // or a manual build goes after a block, not into a chain
    const B = g.before || null;
    const levels = _sets.filter(s => s.kind === "ORCA");
    const mlips = _sets.filter(s => s.kind === "MLIP");
    // a conformer search goes after a block or first in a row, never into a
    // chain: what follows it is the conformer stack its picker makes
    const crests = B ? [] : _sets.filter(s => s.kind === "CREST");
    const kinds = typeKinds();
    // what goes before a step is most often the optimization it starts from
    let sugg = B ? "opt" : nextKind(P ? P.kind : "");
    if (kinds.indexOf(sugg) < 0) sugg = kinds[0] || "";
    const lv = menuLevel(P, levels);
    const keys = menuKeys(P);
    const keyOf = (/** @type {string} */ k) => Object.keys(keys).find(x => keys[x] === k) || "";
    // a workflow pastes after ONE calculation: a stack takes none (after an
    // IRC the structure it starts from is asked, like any step's)
    const noWf = P && P.stack
      ? P.short + " is " + P.members.length + " calculations: a workflow pastes after one calculation"
      : "";
    const isIrc = !B && !!P && !P.stack && P.kind === "irc";
    const wfBtn = (/** @type {string} */ attr, /** @type {string} */ label, /** @type {string} */ title) =>
      `<button class="btn btn-sm btn-ghost" type="button" ${attr}${noWf ? ` disabled title="${esc(noWf)}"` : ` title="${esc(title)}"`}>${esc(label)}</button>`;
    const el = NB.el("div", "qe-addmenu");
    el.id = "qe-addmenu";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-label", "Add a step " + (B ? "before " + B.short : P ? "after " + P.short : "to " + g.lane.name));
    el.innerHTML = `
      <div class="qe-amhead">${B ? "Before <b>" + esc(B.short) + "</b>" : P ? "After <b>" + esc(P.short) + "</b>" : "First step of <b>" + esc(g.lane.name) + "</b>"}</div>
      ${B ? `<p class="qe-note">It starts from ${esc(B.short)}'s structure${P ? ", after " + esc(P.short) : ""}; ${
          esc(B.short)} then runs on its result.</p>` : ""}
      ${isIrc ? `<p class="qe-note">Which structure it starts from (an end, both, or a point on the path) is asked next.</p>` : ""}
      ${!B && P && !P.stack && P.kind === "crest_conf" ? `<p class="qe-note">How many conformers it runs on (one step each, stacked) and an energy condition are asked next.</p>` : ""}
      ${levels.length && kinds.length ? `
        <label class="qe-rlab">level <select class="qe-select" data-f="level" aria-label="The level the step runs at">${
          levels.map(s => `<option value="${esc(s.id)}"${lv && s.id === lv.id ? " selected" : ""}>${esc(s.name)}</option>`).join("")}</select></label>
        <button class="btn btn-sm btn-primary qe-amsugg" type="button" data-k="${esc(sugg)}">${esc(TYPE_LABEL[sugg] || sugg)}<kbd>Enter</kbd></button>
        <div class="qe-amrow">${kinds.filter(k => k !== sugg).map(k =>
          `<button class="btn btn-sm btn-ghost" type="button" data-k="${esc(k)}">${esc(TYPE_LABEL[k] || k)}${
            keyOf(k) ? "<kbd>" + keyOf(k).toUpperCase() + "</kbd>" : ""}</button>`).join("")}</div>`
        : `<p class="qe-note">No ORCA level yet: + New under ORCA levels makes one.</p>`}
      ${mlips.length ? `<div class="qe-amrow">${mlips.map(s =>
          `<button class="btn btn-sm btn-ghost" type="button" data-mlip="${esc(s.id)}" title="An MLIP pre-optimization with this set">Preopt · ${esc(s.name)}</button>`).join("")}</div>` : ""}
      ${crests.length ? `<div class="qe-amrow">${crests.map(s =>
          `<button class="btn btn-sm btn-ghost" type="button" data-mlip="${esc(s.id)}" title="A CREST conformer search with this set">Conformers · ${esc(s.name)}</button>`).join("")}</div>` : ""}
      ${!B && (_clip || _wfs.length) ? `<div class="qe-amrow"><span class="qe-note">workflow</span>${
          _clip ? wfBtn('data-wfclip="1"', "copied", wfSummary(_clip)) : ""}${
          _wfs.map(w => wfBtn(`data-wfid="${esc(w.id)}"`, w.name, wfSummary(w))).join("")}</div>` : ""}
      ${B ? "" : `<button class="btn btn-sm btn-ghost qe-amman" type="button" data-manual="1">Manual build…<kbd>M</kbd></button>`}`;
    NB.$("qed").appendChild(el);
    // beside the slot, kept inside the window
    const r = anchor.getBoundingClientRect();
    const w = el.offsetWidth, h = el.offsetHeight;
    let left = r.right + 8, top = r.top;
    if (left + w > window.innerWidth - 8) left = Math.max(8, r.left - w - 8);
    if (top + h > window.innerHeight - 8) top = Math.max(8, window.innerHeight - h - 8);
    el.style.left = left + "px"; el.style.top = top + "px";
    _menu = { key: g.key, el: el };
    const level = () => {
      const s = /** @type {HTMLSelectElement|null} */ (el.querySelector('[data-f="level"]'));
      return s ? s.value : "";
    };
    el.addEventListener("click", (/** @type {MouseEvent} */ ev) => {
      const b = /** @type {HTMLElement} */ (ev.target).closest("button");
      if (!b || /** @type {HTMLButtonElement} */ (b).disabled) return;
      if (b.hasAttribute("data-k")) menuAdd(level(), b.getAttribute("data-k") || "");
      else if (b.hasAttribute("data-mlip")) menuAdd(b.getAttribute("data-mlip") || "", "");
      else if (b.hasAttribute("data-wfclip")) menuWorkflow(_clip);
      else if (b.hasAttribute("data-wfid")) {
        const wf = _wfs.find(x => x.id === b.getAttribute("data-wfid"));
        if (wf) menuWorkflow(wfOf(wf));
      } else if (b.hasAttribute("data-manual")) menuManual();
    });
    el.addEventListener("keydown", (/** @type {KeyboardEvent} */ ev) => {
      const t = /** @type {HTMLElement} */ (ev.target);
      if (ev.key === "Escape") { ev.preventDefault(); closeAddMenu(true); return; }
      if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
      // a focused button answers Enter itself; the level select passes it on
      if (ev.key === "Enter" && t.tagName === "SELECT" && sugg) { ev.preventDefault(); menuAdd(level(), sugg); return; }
      if (t.tagName === "SELECT") return;      // its letters pick a level
      const k = ev.key.toLowerCase();
      if (k === "m" && !B) { ev.preventDefault(); menuManual(); return; }
      const kind = keys[k];
      if (kind && kinds.indexOf(kind) >= 0 && levels.length) { ev.preventDefault(); menuAdd(level(), kind); }
    });
    document.addEventListener("mousedown", menuOutside, true);
    NB.$("qe-canvas").addEventListener("scroll", closeAddMenuQuiet, { once: true });
    window.addEventListener("resize", closeAddMenuQuiet, { once: true });
    const first = /** @type {HTMLElement|null} */ (el.querySelector(".qe-amsugg") || el.querySelector("button:not([disabled])"));
    if (first) first.focus();
  }
  /** @param {MouseEvent} ev */
  function menuOutside(ev) {
    if (_menu && !_menu.el.contains(/** @type {Node} */ (ev.target))) closeAddMenu();
  }
  function closeAddMenuQuiet() { closeAddMenu(); }
  /** @param {boolean} [refocus] put the keyboard back on the slot's + */
  function closeAddMenu(refocus) {
    document.removeEventListener("mousedown", menuOutside, true);
    if (!_menu) return;
    const key = _menu.key;
    _menu.el.remove();
    _menu = null;
    if (refocus) {
      const b = /** @type {HTMLElement|null} */ (NB.$("qe-graph").querySelector(`[data-a="ghost"][data-slot="${CSS.escape(key)}"]`));
      if (b) b.focus({ preventScroll: true });
    }
  }
  /** The menu's slot as the canvas has it now (null when it has gone: the
   *  selection moved, the block was removed). */
  function menuSlot() {
    const key = _menu ? _menu.key : "";
    closeAddMenu();
    const g = (NB.$("qe-graph")._ghosts || []).find((/** @type {any} */ x) => x.key === key) || null;
    if (!g) NB.toast("That + slot is gone: select the block again.");
    return g;
  }
  /** @param {string} setId @param {string} kind "" for an MLIP or CREST set's own */
  async function menuAdd(setId, kind) {
    const g = menuSlot();
    const set = _sets.find(s => s.id === setId);
    if (!g || !set) return;
    if (set.kind === "ORCA") {
      _lastLevel = set.id;
      NB.call("set_ui_state", LEVEL_KEY, JSON.stringify(set.id));
    }
    await addFromSet(g, set, kind || undefined);
  }
  /** @param {any} wf */
  async function menuWorkflow(wf) {
    const g = menuSlot();
    if (g && wf) await workflowAtSlot(g, wf);
  }
  function menuManual() {
    const g = menuSlot();
    if (g) manualBuild(g);
  }
  /** A workflow put on a + slot: after its block, or as an empty row's
   *  first steps. @param {any} g @param {any} wf */
  function workflowAtSlot(g, wf) {
    return g.parent ? pasteAfter(g.parent, wf) : pasteIntoSystem([g.lane], wf);
  }

  /** What Remove / Delete takes from the selection: with one block
   *  selected, the selected calculation alone -- a stack loses one member at
   *  a time -- unless the block was selected whole by a drag over it; with
   *  several blocks selected, every calculation of every one. */
  function removeChoice() {
    if (_sel && _sel.t === "multi") {
      const nodes = selNodes();
      return nodes.length ? { nodes: nodes, own: allNames(nodes) } : null;
    }
    const n = selNode(), c = selMember();
    if (!n || !c) return null;
    return { nodes: [n], own: _sel.all ? allNames([n]) : [c.name] };
  }
  /** @param {any[]} nodes */
  const allNames = (nodes) => nodes.reduce((/** @type {string[]} */ a, /** @type {any} */ n) =>
    a.concat(n.members.map((/** @type {any} */ c) => c.name)), []);

  /** Delete: the chosen calculations (removeChoice) and everything that
   *  hangs off them, asked first -- or, asked so, those alone, what hangs off
   *  them staying (blocked until it is given another geometry); a running
   *  one is refused (stop it first).
   *  @param {{nodes: any[], own: string[]} | null} pick */
  function requestRemove(pick) {
    if (!pick || !pick.own.length) return;
    const nodes = pick.nodes, own = pick.own;
    const L = nodes[0].lane;
    const gone = own.slice();
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
    const k = gone.length - own.length;
    // what the question names: the one calculation, the whole block, or the blocks
    const what = nodes.length > 1 ? nodes.length + " blocks"
      : own.length < nodes[0].members.length ? own.join(", ") : nodes[0].short;
    // a stack that keeps a member stays selected
    const keepKey = nodes.length === 1 && nodes[0].stack ? nodes[0].key : "";
    // a row left with no block goes with its last one, as the design's lanes do
    const emptiedBy = (/** @type {Set<string>} */ s) => !!L.sys && L.calcs.every((/** @type {any} */ c) => s.has(c.name));
    /** Remove `names`: one batch, undoable unless it takes a finished
     *  calculation (the store would take it back as PENDING, which is
     *  queueing it to run again, not undoing). @param {string[]} names */
    const removeNow = async (names) => {
      const s = new Set(names);
      const emptied = emptiedBy(s);
      const finished = names.some(x => {
        const c = NB.queue.find((/** @type {any} */ y) => y.name === x);
        return c && FINISHED.indexOf(c.state) >= 0;
      });
      const before = NB.queue.map((/** @type {any} */ c) => c.name);
      const sysFull = emptied ? await systemFull(L.sys.id) : null;
      const ops = /** @type {any[]} */ (names.slice().reverse().map(x => ({ op: "remove_calc", name: x })));
      if (emptied) ops.push({ op: "remove_system", id: L.sys.id });
      const inv = finished || (emptied && !sysFull) ? null
        : (/** @type {any[]} */ (sysFull ? [{ op: "add_system", system: sysFull }] : []))
          .concat(names.map(x => ({ op: "add_calc", calc: calcDict(x) })), [{ op: "order", names: before }]);
      _sel = keepKey && nodes[0].members.some((/** @type {any} */ c) => !s.has(c.name))
        ? { t: "node", key: keepKey, m: 0 } : null;
      apply(ops, inv, "the removal");
    };
    const finishedAny = calcs.some(c => c && FINISHED.indexOf(c.state) >= 0);
    const finishedOwn = own.some(x => {
      const c = NB.queue.find((/** @type {any} */ y) => y.name === x);
      return c && FINISHED.indexOf(c.state) >= 0;
    });
    // "Remove only this": what hangs off it stays, blocked until it is given
    // another geometry -- refused by the store while a run is going (a
    // calculation yet to run would point at nothing), so not offered then
    const onlyThis = k > 0 && !NB.running;
    const after = k > 0 ? ` The ${k} calculation${k === 1 ? "" : "s"} after it depend${k === 1 ? "s" : ""} on it${
      onlyThis ? ": remove them too, or keep them, blocked until each is given another geometry."
               : ", so they go too (keeping them is possible when the queue is not running)."}` : "";
    NB.modalRaw(`Remove ${gone.length} calculation${gone.length === 1 ? "" : "s"}?`,
      `<p>${esc(what)} ${nodes.length > 1 ? "are" : "is"} removed from the queue.${esc(after)}${emptiedBy(set) ? " Its row goes too." : ""} Files already written stay on disk.${
        finishedAny ? " Finished calculations cannot be brought back with Ctrl+Z." : " Ctrl+Z brings it back."}</p>`,
      [{ label: "Cancel", cls: "btn-ghost" }].concat(onlyThis ? [{
        label: own.length === 1 ? "Remove only this" : "Remove only these", cls: "btn-ghost", act: () => {
          if (finishedOwn) NB.toast("A finished calculation removed cannot be brought back with Ctrl+Z.", "warn");
          removeNow(own);
        } }] : [],
      [{ label: k > 0 ? `Remove all ${gone.length}` : "Remove", cls: "btn-danger", focus: true,
         act: () => { removeNow(gone); } }]));
  }

  /** @param {any} L */
  async function removeSystem(L) {
    if (L.calcs.length) return;
    const full = await systemFull(L.sys.id);
    _sel = null;
    apply([{ op: "remove_system", id: L.sys.id }],
          full ? [{ op: "add_system", system: full }] : null, "removing " + L.sys.name);
  }

  /** Coordinates typed or pasted, asked for in the modal: a name (for a new
   *  row) and the block (an .xyz file's text, its two header lines or not).
   *  "Load .xyz…" there takes a file instead (files, for a new row). Resolves to {name, label, xyz},
   *  {file: true} for the file, or null.
   *  @param {string} title @param {string} name "" for no name field
   *  @returns {Promise<any>} */
  function askCoords(title, name) {
    return new Promise(resolve => {
      NB.modalRaw(title,
        `${name ? `<label class="qe-rlab">name <input class="qe-input mono" id="qe-pc-name" spellcheck="false"
            value="${esc(name)}" aria-label="Name of the new row"></label>` : ""}
         <textarea class="qe-tpl" id="qe-pc-xyz" rows="12" spellcheck="false" aria-label="Coordinates"
           placeholder="O   0.000   0.000   0.000&#10;H   0.000   0.757   0.587&#10;H   0.000  -0.757   0.587"></textarea>
         <p class="hint">One atom per line: element, x, y, z in Å. An .xyz file's two header lines may stay.</p>`,
        [{ label: name ? "Load .xyz files…" : "Load .xyz…", cls: "btn-ghost", act: () => resolve({ file: true }) },
         { label: "Cancel", cls: "btn-ghost", act: () => resolve(null) },
         { label: "Add", cls: "btn-primary", act: () => {
            const ta = /** @type {any} */ (document.getElementById("qe-pc-xyz"));
            const nm = /** @type {any} */ (document.getElementById("qe-pc-name"));
            const xyz = ta ? String(ta.value || "") : "";
            resolve({ name: nm ? String(nm.value || "").trim() : "", label: "pasted", xyz: xyz });
          } }], true);
      setTimeout(() => { const ta = document.getElementById("qe-pc-xyz"); if (ta) ta.focus(); }, 0);
    });
  }
  /** The coordinate lines of a pasted block, or why there are none.
   *  @param {string} text @returns {{xyz: string, n: number, error: string}} */
  function readCoords(text) {
    const lines = String(text || "").replace(/\r/g, "").split("\n");
    if (lines.length > 2 && /^\s*\d+\s*$/.test(lines[0])) lines.splice(0, 2);
    const atoms = lines.filter(l => l.trim());
    const bad = atoms.find(l => xyzAtoms(l) !== 1);
    if (!atoms.length) return { xyz: "", n: 0, error: "There are no coordinates in it." };
    if (bad) return { xyz: "", n: 0, error: `"${bad.trim().slice(0, 40)}" is not an atom line (element x y z).` };
    return { xyz: atoms.join("\n"), n: atoms.length, error: "" };
  }

  /** Add a structure to a row: a picked .xyz file, or (`paste`) coordinates
   *  typed or pasted. @param {any} L @param {boolean} [paste] */
  async function addInput(L, paste) {
    let label = "", xyz = "";
    if (paste) {
      const a = await askCoords("Paste coordinates", "");
      if (!a) return;
      if (a.file) { addInput(L); return; }
      const c = readCoords(a.xyz);
      if (c.error) { NB.fail("Could not add the structure: " + c.error); return; }
      const n = (L.sys.inputs || []).length;
      label = "pasted " + (n + 1); xyz = c.xyz;
    } else {
      const r = await NB.call("load_xyz_file");
      if (!r || r.cancelled) return;
      if (!r.ok) { NB.fail("Could not read that .xyz."); return; }
      label = (r.name || "structure") + ".xyz"; xyz = r.text;
    }
    const full = await systemFull(L.sys.id);
    const old = full ? (full.inputs || []) : [];
    apply([{ op: "update_system", id: L.sys.id, patch: { inputs: old.concat([{ label: label, xyz: xyz }]) } }],
          [{ op: "update_system", id: L.sys.id, patch: { inputs: old } }], "the new structure");
  }
  /** Take one structure off a row (Ctrl+Z puts it back). The calculations
   *  that were given it keep their copy of it. @param {any} L @param {number} i */
  async function removeInput(L, i) {
    const full = await systemFull(L.sys.id);
    const old = full ? (full.inputs || []) : [];
    if (!old[i]) return;
    apply([{ op: "update_system", id: L.sys.id, patch: { inputs: old.filter((/** @type {any} */ _x, /** @type {number} */ k) => k !== i) } }],
          [{ op: "update_system", id: L.sys.id, patch: { inputs: old } }], "removing " + (old[i].label || "a structure"));
  }

  /** "+ system": a structure becomes an empty lane -- coordinates pasted
   *  (asked first, with the row's name), or .xyz files picked, one row each
   *  by the drop's rules (pasteOnFiles: named after the file, charge and
   *  multiplicity from its comment line, a refused file listed). */
  async function addSystem() {
    const a = await askCoords("New system", "system");
    if (!a) return;
    if (a.file) {
      const r = await NB.call("pick_xyz_files");
      if (!r || r.cancelled) return;
      if (r.ok !== true) { NB.fail(why(r, "Could not read the files")); return; }
      await pasteOnFiles(null, Array.isArray(r.files) ? r.files : [], "the new rows");
      return;
    }
    const c = readCoords(a.xyz);
    if (c.error) { NB.fail("Could not add the system: " + c.error); return; }
    const xyz = c.xyz, label = "pasted";
    let base = a.name || "system";
    const taken = new Set(NB.systems.map((/** @type {any} */ s) => lower(s.name)));
    base = cleanName(base) || "system";
    let nm = base, i = 2;
    while (taken.has(lower(nm)) || NB.queue.some((/** @type {any} */ c) => lower(c.name) === lower(nm))) nm = base + SEP + (i++);
    const id = newId("s");
    await applySel([{ op: "add_system", system: { id: id, name: nm, charge: 0, multiplicity: 1,
                      inputs: [{ label: label, xyz: xyz }] } }],
                   [{ op: "remove_system", id: id }], "the new system", { t: "sys", key: "s:" + id });
  }

  // ------------------------------------------------------------ workflows
  // A workflow is the steps of some blocks of ONE row without the row's
  // structure: per calculation its type, its level (set name + text, or the
  // Guided config), its gate, its upstream and geometry source inside the
  // copy, and its place relative to the copy's top-left block. A step whose
  // parent or geometry lies outside the copy takes the target's structure.
  // It lives here (and in the UI state, to outlive a reload); pasting
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
    const rows = selRows();
    if (rows.length > 1) return "into the empty ones of the " + rows.length + " selected rows";
    if (rows.length) return "as the first steps of " + rows[0].name;
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
      // the step's part of the name after the row's (Ltyr_Opt, or an older Ltyr-opt)
      let suffix = lower(head).indexOf(lower(laneName)) === 0 && /^[-_]./.test(head.slice(laneName.length))
        ? head.slice(laneName.length + 1) : "";
      if (!suffix) suffix = SUFFIX[n.kind] || "step";
      n.members.forEach((/** @type {any} */ c) => {
        const f = _full.get(c.name);
        const full = f ? f.calc : null;
        if (!full) { problems.push(c.name + " could not be read."); return; }
        const raw = String(full.raw_text || "");
        const isRaw = !!full.is_raw || (!!c.set_name && !!raw);
        if (isRaw && !isSettings(setKindOf(c.kind)) && raw.indexOf(GEOM) < 0) {
          problems.push(c.name + "'s text carries its own coordinates (no " + GEOM + "), so it cannot run on another structure.");
        }
        const ref = full.geometry_source === "reference" && local.has(full.ref_name) ? local.get(full.ref_name) : "";
        steps.push({ id: local.get(c.name), node: "n" + ni, suffix: suffix,
                     tail: n.stack ? tailOf(c.name) : "",
                     kind: c.kind, is_raw: isRaw, raw_text: isRaw ? raw : "", set_name: c.set_name || "",
                     // a level's block carries its options (constraints, IRC
                     // settings ...) in its config; a text that names its own
                     // job has none
                     config: !isRaw || (!isSettings(setKindOf(c.kind)) && hasJobLine(raw)) ? full.config || {} : {},
                     after: c.after && local.has(c.after) ? local.get(c.after) : "",
                     ref: ref, ref_point: ref ? String(full.ref_point || "") : "",
                     gate: c.gate === "approval" ? "approval" : "auto", auto_pass: c.auto_pass !== false,
                     // the energy condition of an edge inside the copy
                     filter_de: c.after && local.has(c.after) ? condOf([c]).de : null,
                     filter_top: c.after && local.has(c.after) ? condOf([c]).top : 0,
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

  // ---- saved workflows: the library's Workflows group (state/workflows.py)

  /** A saved workflow clicked in the library: into the selected rows (the
   *  empty ones), after the selected block, or -- nothing to put it on --
   *  made the copied workflow, for Ctrl+V. @param {any} w */
  async function useWorkflow(w) {
    const wf = wfOf(w);
    const rows = selRows();
    if (rows.length) { await pasteIntoSystem(rows, wf); return; }
    const n = selNode();
    if (n) { await pasteAfter(n, wf); return; }
    _clip = wf;
    NB.call("set_ui_state", CLIP_KEY, JSON.stringify(_clip));
    _libSig = ""; _inspSig = "";
    render();
    NB.toast(`Copied the workflow '${w.name}' (${wfSummary(wf)}). Ctrl+V pastes it after a block, into empty rows, or onto new .xyz rows.`, "ok");
  }

  /** A name asked in the modal; Enter answers. Resolves to the name, or null.
   *  @param {string} title @param {string} value @param {string} hint
   *  @returns {Promise<string|null>} */
  function askName(title, value, hint) {
    return new Promise(resolve => {
      const read = () => {
        const el = /** @type {any} */ (document.getElementById("qe-askname"));
        return el ? el.value.trim() || null : null;
      };
      NB.modalRaw(title,
        `<label class="qe-rlab">name <input class="qe-input" id="qe-askname" spellcheck="false"
            value="${esc(value)}" aria-label="Name"></label>${hint ? `<p class="hint">${esc(hint)}</p>` : ""}`,
        [{ label: "Cancel", cls: "btn-ghost", act: () => resolve(null) },
         { label: "Save", cls: "btn-primary", act: () => resolve(read()) }]);
      setTimeout(() => {
        const el = /** @type {any} */ (document.getElementById("qe-askname"));
        if (!el) return;
        el.focus(); el.select();
        el.addEventListener("keydown", (/** @type {KeyboardEvent} */ ev) => {
          if (ev.key !== "Enter") return;
          ev.preventDefault();
          const v = read();
          NB.closeModal();
          resolve(v);
        });
      }, 0);
    });
  }
  /** the IRC picker's structure view, destroyed when it is asked again or answered @type {any} */
  let _ircStage = null;
  /** a frame's coordinate block: the .xyz count and comment lines dropped @param {string} t */
  const coordsOf = (t) => {
    const ls = String(t || "").replace(/\r/g, "").split("\n");
    return (/^\s*\d+\s*$/.test(ls[0] || "") ? ls.slice(2) : ls).join("\n").trim();
  };
  const HARTREE_KCAL = 627.5095;

  /** Which structure a step after an IRC starts from, on the IRC's own
   *  energy profile: one compact graph of the whole path, backward end ->
   *  TS -> forward end (ORCA's _IRC_Full_trj.xyz -- measured: its first frame
   *  is the backward end, its last the forward end -- else the backward path
   *  reversed and the forward one), a point clicked or dragged (arrow keys
   *  too), the structure there shown. An end picked is taken by reference
   *  (_F / _B, read from _IRC_F/_B.xyz when the step runs); any other point
   *  has its coordinates copied in (_P<k>). *Both ends* makes the two-member
   *  stack. Before the IRC has run there is no path yet: the ends alone, by
   *  reference. Resolves to the members' geometry, or null.
   *  @param {any} irc the IRC calculation @param {boolean} both offer "both ends"
   *  @returns {Promise<{tail: string, pt: string, xyz: string}[] | null>} */
  async function askIrcStructure(irc, both) {
    const r = await NB.call("get_irc_structures", irc.name);
    if (!r || r.ok !== true) { NB.fail(why(r, "Could not read the IRC's structures")); return null; }
    const files = /** @type {IrcStructureFile[]} */ (Array.isArray(r.files) ? r.files : []);
    const read = async (/** @type {string} */ k) => {
      const f = files.find(x => x.key === k);
      if (!f) return [];
      const fr = await NB.call("get_structure_frames", f.path);
      return fr && fr.ok === true && Array.isArray(fr.frames) ? fr.frames : [];
    };
    /** @type {any[]} */ let pts = [];
    let bEnd = -1, fEnd = -1, fromFull = false;
    if (r.done) {
      pts = await read("Full_trj");
      if (pts.length) { bEnd = 0; fEnd = pts.length - 1; fromFull = true; }
      else {
        const bb = (await read("B_trj")).slice().reverse(), ff = await read("F_trj");
        pts = bb.concat(ff);
        if (bb.length) bEnd = 0;
        if (ff.length) fEnd = pts.length - 1;
      }
    }
    const F = { tail: SEP + "F", pt: "irc_f", xyz: "" }, B = { tail: SEP + "B", pt: "irc_b", xyz: "" };
    if (_ircStage) { _ircStage.destroy(); _ircStage = null; }
    const title = (both ? "Start from which structure of " : "Take which structure of ") + irc.name + "?";
    const verb = both ? "Add at" : "Use";
    /** the choice: a segmented control; the one confirm button says what it does
     *  @param {string[][]} opts [key, label] @param {string} on */
    const seg = (opts, on) => `<div class="qe-ircrow"><span class="qe-note">Start from</span>
      <div class="seg" role="group" aria-label="Start from">${opts.map(([k, l]) =>
        `<button type="button" data-m="${k}" aria-pressed="${k === on}">${l}</button>`).join("")}</div></div>`;
    const foot = () => /** @type {any} */ (NB.$("modal-foot").lastElementChild);
    const setSeg = (/** @type {string} */ on) => NB.$("modal-body").querySelectorAll(".seg [data-m]")
      .forEach((/** @type {any} */ b) => b.setAttribute("aria-pressed", String(b.getAttribute("data-m") === on)));

    // no path to show: the ends, by reference
    if (pts.length < 2) {
      const why2 = r.done ? `${irc.name} wrote no path files.` : `${irc.name} has not run yet.`;
      let mode = both ? "both" : "F";
      const label = () => mode === "both" ? "Add at both ends" : `${verb} the ${mode === "F" ? "forward" : "backward"} end`;
      return new Promise(resolve => {
        NB.modalRaw(title, `<p>${esc(why2)} Its ends are read from its files when the step runs; a point on the path can be picked once it has run.</p>
          ${seg((both ? [["both", "Both ends"]] : []).concat([["F", "Forward end"], ["B", "Backward end"]]), mode)}`,
          [{ label: "Cancel", cls: "btn-ghost", act: () => resolve(null) },
           { label: label(), cls: "btn-primary", focus: true,
             act: () => resolve(mode === "both" ? [F, B] : mode === "F" ? [F] : [B]) }]);
        NB.$("modal-body").querySelectorAll(".seg [data-m]").forEach((/** @type {any} */ b) =>
          b.addEventListener("click", () => { mode = b.getAttribute("data-m"); setSeg(mode); foot().textContent = label(); }));
      });
    }

    const n = pts.length;
    const es = pts.map(f => (typeof f.energy === "number" ? f.energy : null));
    const known = /** @type {number[]} */ (es.filter(e => e !== null));
    const top = known.length ? Math.max(...known) : 0, low = known.length ? Math.min(...known) : 0;
    const tsAt = known.length ? es.indexOf(top) : -1;
    let at = fEnd >= 0 ? fEnd : n - 1;
    // "both": the two ends (the add default); "point": the one picked on the
    // graph or by number -- picking one switches to it
    let mode = both && fEnd >= 0 && bEnd >= 0 ? "both" : "point";
    const pick = () => mode === "both" ? [F, B] : at === fEnd ? [F] : at === bEnd ? [B]
      : [{ tail: SEP + "P" + (at + 1), pt: "", xyz: coordsOf(pts[at].xyz) }];
    const label = () => mode === "both" ? "Add at both ends"
      : at === fEnd ? `${verb} the forward end` : at === bEnd ? `${verb} the backward end`
      : at === tsAt ? `${verb} the TS (point ${at + 1})` : `${verb} point ${at + 1}`;
    return new Promise(resolve => {
      let unhook = () => {};
      const done = (/** @type {any} */ v) => { unhook(); if (_ircStage) { _ircStage.destroy(); _ircStage = null; } resolve(v); };
      NB.modalRaw(title,
        `${both && fEnd >= 0 && bEnd >= 0 ? seg([["point", "One point"], ["both", "Both ends"]], mode) : ""}
        <svg class="qe-ircprof" data-x="prof" tabindex="0" aria-label="${esc(
            "Energy along the path of " + irc.name + ", backward end to forward end: click or drag a point, or use the arrow keys")}"></svg>
        <div class="qe-ircrow">
          <label class="qe-rlab">point <input class="qe-input qe-ircnum" type="number" data-f="num" min="1" max="${n}" step="1"
            value="${at + 1}" aria-label="${esc("Point number, 1 to " + n)}"></label>
          <span class="qe-note">of ${n}${fromFull ? " (its frame in _IRC_Full_trj.xyz)" : " (the backward path reversed, then the forward one)"}</span>
        </div>
        <p class="qe-note" data-x="fr" aria-live="polite"></p>
        <div class="qe-stage qe-ircstage"><canvas class="molcv" data-v="irccv" aria-label="The structure at the point picked"></canvas></div>`,
        [{ label: "Cancel", cls: "btn-ghost", act: () => done(null) },
         { label: label(), cls: "btn-primary", focus: true, act: () => done(pick()) }]);
      const box = NB.$("modal-body");
      const prof = /** @type {any} */ (box.querySelector('[data-x="prof"]'));
      const info = /** @type {any} */ (box.querySelector('[data-x="fr"]'));
      const num = /** @type {any} */ (box.querySelector('[data-f="num"]'));
      _ircStage = MOL.mount(box.querySelector('[data-v="irccv"]'), { labels: false });
      // drawn in the graph's own pixels, so the marks stay round at any width
      const b0 = prof.getBoundingClientRect();
      const w = Math.max(200, Math.round(b0.width)), h = Math.max(60, Math.round(b0.height));
      prof.setAttribute("viewBox", `0 0 ${w} ${h}`);
      const L = 10, R = 10, T = 14, BOT = 14;
      const X = (/** @type {number} */ i) => L + (i / (n - 1)) * (w - L - R);
      const Y = (/** @type {number|null} */ e) => e === null || top === low ? (T + h - BOT) / 2
        : T + ((top - e) / (top - low)) * (h - T - BOT);
      const line = es.map((e, i) => e === null ? "" : X(i).toFixed(1) + "," + Y(e).toFixed(1)).filter(Boolean).join(" ");
      const text = (/** @type {number} */ i, /** @type {string} */ t, /** @type {string} */ anchor, /** @type {number} */ y) =>
        `<text class="qe-irctext" x="${X(i).toFixed(1)}" y="${y}" text-anchor="${anchor}">${t}</text>`;
      const base = `<polyline class="qe-ircline" points="${line}"/>`
        + (bEnd >= 0 ? text(bEnd, "B", "start", h - 3) : "") + (fEnd >= 0 ? text(fEnd, "F", "end", h - 3) : "")
        + (tsAt >= 0 ? text(tsAt, "TS", "middle", 10) : "");
      const dot = (/** @type {number} */ i) => `<circle class="qe-ircdot" cx="${X(i).toFixed(1)}" cy="${Y(es[i]).toFixed(1)}" r="4"/>`;
      const dE = (/** @type {number} */ i) => {
        const e = es[i];
        return e !== null && known.length > 1 ? `ΔE ${((e - top) * HARTREE_KCAL).toFixed(1)} kcal/mol from the TS` : "";
      };
      const show = () => {
        if (mode === "both") {
          prof.innerHTML = base + dot(bEnd) + dot(fEnd);
          info.textContent = "Both ends: two steps, _B and _F, each read from its end-point file when it runs (shown: the forward end).";
          if (_ircStage) _ircStage.setAtoms(MOL.parseXyz(coordsOf(pts[fEnd].xyz)));
        } else {
          prof.innerHTML = base + `<line class="qe-ircguide" x1="${X(at).toFixed(1)}" x2="${X(at).toFixed(1)}" y1="${T - 4}" y2="${h - BOT + 2}"/>` + dot(at);
          const what = at === fEnd ? "Forward end" : at === bEnd ? "Backward end" : at === tsAt ? "TS (the top of the path)" : "";
          const how = at === fEnd || at === bEnd ? `read from ${irc.name}_IRC_${at === fEnd ? "F" : "B"}.xyz when the step runs`
            : "its coordinates are copied in";
          info.textContent = [what, dE(at), how].filter(Boolean).join(" · ");
          if (_ircStage) _ircStage.setAtoms(MOL.parseXyz(coordsOf(pts[at].xyz)));   // gone once answered
        }
        // the number follows the graph, except while it is being typed
        if (document.activeElement !== num) num.value = String(at + 1);
        setSeg(mode);
        foot().textContent = label();
      };
      /** a point picked on the graph, by key or by number: the choice becomes it @param {number} i */
      const goTo = (i) => { at = Math.max(0, Math.min(n - 1, i)); mode = "point"; show(); };
      box.querySelectorAll(".seg [data-m]").forEach((/** @type {any} */ b) =>
        b.addEventListener("click", () => { mode = b.getAttribute("data-m"); show(); }));
      const pickAt = (/** @type {MouseEvent} */ ev) => {
        const b = prof.getBoundingClientRect();
        const x = ((ev.clientX - b.left) / b.width) * w;
        goTo(Math.round(Math.max(0, Math.min(1, (x - L) / (w - L - R))) * (n - 1)));
      };
      prof.addEventListener("mousedown", (/** @type {MouseEvent} */ ev) => { prof.focus(); pickAt(ev); });
      prof.addEventListener("mousemove", (/** @type {MouseEvent} */ ev) => { if (ev.buttons & 1) pickAt(ev); });
      // Left / Right step the point wherever the keyboard is in the window
      // (the confirm button has it first, the number field too); Shift steps
      // 10, Home / End jump to the ends outside the number field. Taken off
      // when the window closes, however it closes.
      const keys = (/** @type {KeyboardEvent} */ ev) => {
        if (!prof.isConnected || NB.$("modal").hidden) { document.removeEventListener("keydown", keys, true); return; }
        const step = ev.shiftKey ? 10 : 1;
        const inNum = ev.target === num;
        const to = ev.key === "ArrowLeft" ? at - step : ev.key === "ArrowRight" ? at + step
          : ev.key === "Home" && !inNum ? 0 : ev.key === "End" && !inNum ? n - 1 : null;
        if (to === null || ev.altKey || ev.ctrlKey || ev.metaKey) return;
        ev.preventDefault();
        ev.stopPropagation();
        goTo(to);
        if (inNum) { num.value = String(at + 1); num.select(); }
      };
      document.addEventListener("keydown", keys, true);
      unhook = () => document.removeEventListener("keydown", keys, true);
      // a point by its number: a valid one moves the graph as it is typed,
      // anything else is put back when the field is left (or on Enter)
      const fromNum = () => {
        const v = parseInt(num.value, 10);
        goTo(isFinite(v) ? v - 1 : at);
        num.value = String(at + 1);
      };
      num.addEventListener("focus", () => num.select());
      num.addEventListener("input", () => {
        const v = parseInt(num.value, 10);
        if (v >= 1 && v <= n) goTo(v - 1);
      });
      num.addEventListener("change", fromNum);
      num.addEventListener("keydown", (/** @type {KeyboardEvent} */ ev) => {
        if (ev.key !== "Enter") return;
        ev.preventDefault();
        fromNum();
        num.select();
      });
      show();
    });
  }

  /** what a step of a kind is called, one of it ("single point"); several
   *  add an "s" when the word allows it, else " steps" @type {Object<string, string>} */
  const NOUN = { sp: "single point", opt: "optimization", ts_opt: "TS optimization", freq: "frequency calculation",
                 ts_freq: "TS frequency calculation", opt_freq: "Opt + Freq", ts_opt_freq: "TS Opt + Freq",
                 irc: "IRC", nmr: "NMR calculation", tddft: "TD-DFT calculation", neb_ts: "NEB-TS",
                 mlip_opt: "MLIP pre-optimization", mlip_sp: "MLIP single point", mlip_freq: "MLIP frequency calculation",
                 mlip_opt_freq: "MLIP Opt + Freq", crest_conf: "conformer search" };
  /** @param {string} kind @param {number} k */
  const nounFor = (kind, k) => {
    const w = NOUN[kind] || (KIND_LABEL[kind] || kind) + " step";
    return k === 1 ? w : /[a-z]$/.test(w) ? w + "s" : w + " steps";
  };
  /** one step of a kind, with its article: "a single point", "an IRC" @param {string} kind */
  const oneOf = (kind) => { const w = nounFor(kind, 1); return (/^([aeiou]|MLIP|NMR|NEB)/i.test(w) ? "an " : "a ") + w; };
  /** What a conformer placeholder (ref_point conf:all) becomes, in the
   *  inspector's words. @param {any} c */
  const confAllNote = (c) => {
    const cr = NB.queue.find((/** @type {any} */ x) => x.name === c.ref_name);
    const what = `one ${nounFor(c.kind, 1)} per conformer (${c.name}${SEP}${CONF_TAIL}1, ${c.name}${SEP}${CONF_TAIL}2 …, the lowest first), and so does everything after it`;
    return cr && cr.state === "done"
      ? `Stands for every conformer ${c.ref_name} found: it becomes ${what} when the queue next runs. It never runs itself.`
      : `Stands for every conformer ${c.ref_name} finds: when the search has finished it becomes ${what}. It never runs itself, and undo does not bring it back.`;
  };

  /** How many conformers of a CREST search a step after it runs on (a
   *  stack, one member per conformer: _c1 ... _cK, conformer 1 the lowest,
   *  as the search's {name}_c1.xyz), and the optional energy condition on
   *  that edge (ΔE ≤ … kcal/mol from the lowest conformer; the rest end
   *  filtered). Choosing and confirming are separate, as in the IRC picker:
   *  ONE confirm button says what it will do ("Add 5 single points
   *  (conformers 1–5)"). Once the search has run its ensemble is listed
   *  (parse_calc_output's `conformers`): the count is clamped to it and the
   *  list shows which are added and which the condition would leave out.
   *  Before the search has run an EMPTY count is "every conformer it finds"
   *  (`all`): one placeholder step (ref_point conf:all) that the engine
   *  replaces, with what hangs off it, by one copy per conformer once the
   *  search is done ("Add a single point for every conformer it finds").
   *  `one`: a block's geometry -- which single conformer, "Use conformer 3".
   *  Resolves to {k, de, all} (k the count, or the conformer when `one`; 0
   *  with `all`), or null.
   *  @param {any} crest the CREST calculation
   *  @param {{kind?: string, wf?: boolean, one?: boolean, at?: number}} opts
   *  @returns {Promise<{k: number, de: number|null, all: boolean} | null>} */
  async function askConformers(crest, opts) {
    /** @type {{index: number, rel_kcal: number}[]} */ let confs = [];
    if (crest.state === "done") {
      const r = await NB.call("parse_calc_output", crest.name);
      if (r && Array.isArray(r.conformers)) {
        confs = r.conformers.filter((/** @type {any} */ c) => c && isFinite(Number(c.index)))
          .map((/** @type {any} */ c) => ({ index: int(c.index), rel_kcal: Number(c.rel_kcal) }))
          .sort((a, b) => a.index - b.index);
      }
    }
    const N = confs.length;
    // before the search has run there is no bound: a member past the ensemble
    // it finds is FILTERED without running, and the ΔE condition is what
    // decides how many run
    const max = N || Infinity;
    const one = !!opts.one;
    // the count starts EMPTY: nothing is assumed for the user. Empty means
    // every conformer the search found -- or, before it has run, every one it
    // will find: a placeholder the engine expands when the search is done
    const later = !one && !N && crest.state !== "done";
    let k = one ? Math.max(1, Math.min(max, int(opts.at) || 1)) : N;
    let typed = one;
    const every = () => later && !typed;
    /** @type {number|null} */ let de = null;
    let bad = "";
    const range = (/** @type {number} */ n) => !typed && n === N ? (n === 1 ? "the only conformer" : "all " + n + " conformers")
      : n === 1 ? "conformer 1" : "conformers 1–" + n;
    const need = () => !one && k < 1 && !every();
    const label = () => need() ? "Type how many conformers"
      : one ? "Use conformer " + k
      : every() ? (opts.wf ? "Paste on every conformer it finds" : `Add ${oneOf(opts.kind || "sp")} for every conformer it finds`)
      : opts.wf ? `Paste on ${k} conformer${k === 1 ? "" : "s"} (${range(k)})`
      : `Add ${k} ${nounFor(opts.kind || "sp", k)} (${range(k)})`;
    const title = one ? "Take which conformer of " + crest.name + "?"
      : (opts.wf ? "Paste the workflow on which conformers of " : "Start from which conformers of ") + crest.name + "?";
    const known = N
      ? `${esc(crest.name)} found ${N} conformer${N === 1 ? "" : "s"}, lowest first (ΔE from the lowest, kcal/mol).`
      : later ? `${esc(crest.name)} has not run yet: how many it finds is known once it has (its energy window, ewin, bounds the ensemble). Left empty, one step stands for every conformer it finds and becomes one per conformer when the search has finished, with everything after it. A typed number is a stack of that many; a member past the ensemble it finds has no structure to start from.`
      : `${esc(crest.name)} listed no conformers here: how many it finds is known once it has (its energy window, ewin, bounds the ensemble). A member past the ensemble it finds has no structure to start from.`;
    const rows = () => confs.slice(0, Math.max(k, 12)).map(c => {
      const added = one ? c.index === k : c.index <= k;
      const cut = !one && added && de !== null && c.rel_kcal > de;
      return `<div class="qe-confrow"${added ? ' data-in="1"' : ""}${cut ? ' data-cut="1"' : ""}><span>c${c.index}</span><span>${
        isFinite(c.rel_kcal) ? c.rel_kcal.toFixed(2) : "—"}</span><span>${cut ? "filtered by ΔE" : added ? (one ? "taken" : "added") : ""}</span></div>`;
    }).join("") + (confs.length > Math.max(k, 12) ? `<div class="qe-confrow"><span>…</span><span></span><span>${confs.length - Math.max(k, 12)} more</span></div>` : "");
    const foot = () => /** @type {any} */ (NB.$("modal-foot").lastElementChild);
    return new Promise(resolve => {
      NB.modalRaw(title,
        `<p class="qe-note">${known}</p>
        <div class="qe-ircrow">
          <label class="qe-rlab">${one ? "conformer" : "conformers"} <input class="qe-input qe-ircnum" type="number" data-f="k"
            min="1"${N ? ` max="${N}"` : ""} step="1" value="${one ? k : ""}"${one ? "" : ` placeholder="${N ? "all " + N : later ? "all it finds" : "how many"}"`} aria-label="${esc((one ? "Conformer number" : "How many conformers") + (N ? ", 1 to " + N + (one ? "" : "; empty for all") : later ? ", 1 or more; empty for every conformer it finds" : ", 1 or more"))}"></label>
          <span class="qe-note">${one ? "1 is the lowest" : "the lowest first, one step each, stacked"}${one ? "" : N ? "; empty: all of them" : later ? "; empty: every one it finds" : "; those past the ensemble it finds end filtered"}</span>
        </div>
        ${one ? "" : `<div class="qe-ircrow">
          <label class="qe-rlab">pass on ΔE ≤ <input class="qe-input qe-condnum" data-f="de" inputmode="decimal" spellcheck="false"
            placeholder="off" aria-label="Pass only conformers within this many kcal/mol of the lowest; empty for all"></label>
          <span class="qe-note">kcal/mol from the lowest; empty: all pass. The rest end filtered.</span>
        </div>`}
        <p class="qe-note" data-tone="err" data-x="bad" role="alert" hidden></p>
        ${N ? `<div class="qe-conflist" data-x="list" aria-live="polite">${rows()}</div>` : ""}`,
        [{ label: "Cancel", cls: "btn-ghost", act: () => resolve(null) },
         { label: label(), cls: "btn-primary", focus: true, act: () => resolve(bad || need() ? null : { k: every() ? 0 : k, de: one ? null : de, all: every() }) }]);
      const box = NB.$("modal-body");
      const kin = /** @type {HTMLInputElement} */ (box.querySelector('[data-f="k"]'));
      const din = /** @type {HTMLInputElement|null} */ (box.querySelector('[data-f="de"]'));
      const badEl = /** @type {HTMLElement} */ (box.querySelector('[data-x="bad"]'));
      const list = /** @type {HTMLElement|null} */ (box.querySelector('[data-x="list"]'));
      const show = () => {
        badEl.hidden = !bad; badEl.textContent = bad;
        foot().disabled = !!bad || need();
        foot().textContent = label();
        if (list) list.innerHTML = rows();
      };
      const readK = (/** @type {boolean} */ clamp) => {
        if (!one && kin.value.trim() === "") { typed = false; k = N; show(); return; }
        typed = true;
        const v = parseInt(kin.value, 10);
        if (isFinite(v) && v >= 1 && v <= max) k = v;
        else if (clamp) { k = Math.max(1, Math.min(max, isFinite(v) ? v : k)); kin.value = String(k); }
        show();
      };
      const readDe = () => {
        if (!din) return;
        const t = din.value.trim().replace(",", ".");
        const v = t === "" ? null : Number(t);
        bad = v !== null && (!isFinite(v) || v < 0) ? "The ΔE window is a number of kcal/mol, 0 or more (empty: all pass)." : "";
        de = bad || v === null ? null : Math.round(v * 100) / 100;
        show();
      };
      kin.addEventListener("focus", () => kin.select());
      kin.addEventListener("input", () => readK(false));
      kin.addEventListener("change", () => readK(true));
      if (din) din.addEventListener("input", readDe);
      // Enter in a field confirms, as the button does
      [kin, din].forEach(el => el && el.addEventListener("keydown", (/** @type {KeyboardEvent} */ ev) => {
        if (ev.key !== "Enter") return;
        ev.preventDefault();
        readK(true); readDe();
        if (!bad && !need()) foot().click();
      }));
      show();
    });
  }

  /** A yes / no question in the modal. @param {string} title @param {string} body
   *  @param {string} yes @returns {Promise<boolean>} */
  function askYes(title, body, yes) {
    return new Promise(resolve => {
      NB.modalRaw(title, `<p>${esc(body)}</p>`,
        [{ label: "Cancel", cls: "btn-ghost", act: () => resolve(false) },
         { label: yes, cls: "btn-primary", focus: true, act: () => resolve(true) }]);
    });
  }

  /** Keep a workflow in the library by name; a name in use is asked about
   *  (replacing keeps that workflow's place). @param {any} wf */
  async function saveWorkflowAs(wf) {
    if (!wf || !Array.isArray(wf.steps) || !wf.steps.length) return;
    const chain = wfSummary(wf).replace(/^\d+ steps?: /, "");
    const name = await askName("Save workflow", chain,
      "Kept under Workflows in the library: click it with rows selected, drag it onto a + slot, or pick it from a slot's add menu.");
    if (!name) return;
    const same = _wfs.find(w => lower(w.name) === lower(name));
    if (same && !(await askYes("Replace the workflow?",
        `There is already a workflow named '${same.name}' (${wfSummary(same)}). Replace it with these steps (${wfSummary(wf)})?`, "Replace"))) return;
    const r = await NB.call("save_workflow", JSON.stringify({ id: same ? same.id : "", name: name, v: 1,
                                                              from: String(wf.from || ""), steps: wf.steps }));
    if (!r || r.ok !== true || !r.workflow) { NB.fail(why(r, "Could not save the workflow")); return; }
    await loadWorkflows();
    render();
    NB.toast(`Saved the workflow '${r.workflow.name}' (${wfSummary(wf)}).`, "ok");
  }
  /** Take a workflow out of the library (asked first): the rows it was put
   *  on keep their steps. @param {any} w */
  function deleteWorkflow(w) {
    NB.confirm("Delete the workflow",
      `The workflow "${w.name}" (${wfSummary(w)}) leaves the library. The rows it was put on keep their steps.`,
      "Delete", async () => {
        const r = await NB.call("delete_workflow", w.id);
        if (!r || r.ok !== true) { NB.fail(why(r, "Could not delete the workflow")); return; }
        await loadWorkflows();
        render();
      });
  }

  /** The add_calc ops that run a workflow in one row: names `<row>_<step>`,
   *  a name in use taking 2, 3 ... (every member of a stack the same
   *  number); roots after `t.parent` (geometry from it -- its `t.refPoint`
   *  end, or `t.rootXyz` copied in), else on `t.xyz`. After an IRC the
   *  workflow runs once per structure picked: each pass names its steps with
   *  `t.tail` (_F, _B) and shares `t.nodeIds`, so the passes stack per step;
   *  after a CREST search once per conformer (_c1 ..., `t.filterDe` the
   *  condition the picker put on the edge into the first steps).
   *  @param {any} wf @param {{sysId: string, laneName: string, charge: number, mult: number,
   *    xyz: string, parent: string, col0: number, row0: number, taken: Set<string>,
   *    tail?: string, refPoint?: string, rootXyz?: string, nodeIds?: Map<string, string>,
   *    filterDe?: number|null}} t */
  function instantiate(wf, t) {
    /** @type {Map<string, any[]>} */ const byNode = new Map();
    wf.steps.forEach((/** @type {any} */ st) => {
      if (!byNode.has(st.node)) byNode.set(st.node, []);
      /** @type {any[]} */ (byNode.get(st.node)).push(st);
    });
    const tl = t.tail || "";
    const nodeIds = t.nodeIds || new Map(), nameOf = new Map();
    byNode.forEach((members, nid) => {
      if (!nodeIds.has(nid)) nodeIds.set(nid, newId("n"));
      const root = t.laneName + SEP + members[0].suffix;
      let base = root;
      // on placeholders every step is copied per conformer (name_c1 ...)
      for (let i = 2; members.some(m => t.taken.has(lower(base + m.tail + tl))
        || (t.refPoint === CONF_ALL && confTailTaken(t.taken, base + m.tail + tl))); i++) base = root + i;
      members.forEach(m => { nameOf.set(m.id, base + m.tail + tl); t.taken.add(lower(base + m.tail + tl)); });
    });
    /** @type {any[]} */ const ops = [];
    /** @type {string[]} */ const fresh = [];
    let first = "";
    wf.steps.forEach((/** @type {any} */ st) => {
      const name = nameOf.get(st.id);
      const after = st.after ? nameOf.get(st.after) || "" : t.parent;
      const ref = st.ref ? nameOf.get(st.ref) || "" : (t.rootXyz ? "" : t.parent);
      const node = nodeIds.get(st.node);
      ops.push({ op: "add_calc", calc: {
        name: name, kind: st.kind, charge: t.charge, multiplicity: t.mult,
        geometry_source: ref ? "reference" : "direct", xyz: ref ? "" : (t.rootXyz || t.xyz),
        ref_name: ref, ref_point: st.ref ? String(st.ref_point || "") : (ref ? t.refPoint || "" : ""),
        is_raw: !!st.is_raw, raw_text: st.is_raw ? String(st.raw_text || "") : "",
        set_name: String(st.set_name || ""),
        config: !st.is_raw || (!isSettings(setKindOf(st.kind)) && hasJobLine(String(st.raw_text || ""))) ? st.config || {} : {},
        system: t.sysId, node: node, col: t.col0 + int(st.col), row: t.row0 + int(st.row),
        after: after, gate: after ? st.gate || "auto" : "auto", auto_pass: st.auto_pass !== false,
        filter_de: st.after ? (st.filter_de == null ? null : Number(st.filter_de)) : (t.filterDe == null ? null : t.filterDe),
        filter_top: st.after ? Math.max(0, int(st.filter_top)) : 0,
        approved: false, state: "pending", message: "",
      } });
      fresh.push(name);
      if (!first) first = "s:" + t.sysId + "/" + node;
    });
    return { ops: ops, fresh: fresh, first: first };
  }

  /** Paste the copied workflow where the selection says: after the selected
   *  block, into the selected empty row(s), or -- nothing selected -- onto
   *  new rows from picked .xyz files. */
  async function paste() {
    if (!_clip) _clip = await loadClip();
    if (!_clip) { NB.toast("Nothing copied yet. Select blocks (drag a box on the empty canvas, or Ctrl+click) and press Ctrl+C."); return; }
    const n = selNode();
    if (n) { await pasteAfter(n, _clip); return; }
    const rows = selRows();
    if (rows.some((/** @type {any} */ L) => !L.calcs.length)) { await pasteIntoSystem(rows, _clip); return; }
    if (_sel) { NB.toast("Select one block to paste after, empty rows, or nothing (new rows from .xyz files)."); return; }
    const r = await NB.call("pick_xyz_files");
    if (!r || r.cancelled) return;
    if (r.ok !== true) { NB.fail(why(r, "Could not read the files")); return; }
    await pasteOnFiles(_clip, Array.isArray(r.files) ? r.files : [], "the pasted workflow");
  }

  /** @param {any} P @param {any} wf */
  async function pasteAfter(P, wf) {
    if (P.stack) {
      NB.toast(`Paste after one calculation: ${P.short} is ${P.members.length} calculations. Select a block after it.`);
      return;
    }
    // after an IRC: which structure(s) the workflow starts from, asked;
    // it runs once per structure picked
    /** @type {{tail: string, pt: string, xyz: string}[]} */
    let passes = [{ tail: "", pt: "", xyz: "" }];
    if (P.kind === "irc") {
      const ends = await askIrcStructure(P.members[0], true);
      if (!ends) return;
      passes = ends;
    }
    // after a CREST search: on how many conformers (once each, _c1 ...),
    // and the condition on the edge into the workflow's first steps
    /** @type {number|null} */ let filterDe = null;
    if (P.kind === "crest_conf") {
      const ch = await askConformers(P.members[0], { wf: true });
      if (!ch) return;
      filterDe = ch.de;
      // every conformer it finds: ONE copy whose first steps are placeholders,
      // the rest hanging off them -- the engine makes it one per conformer
      passes = ch.all ? [{ tail: "", pt: CONF_ALL, xyz: "" }]
        : Array.from({ length: ch.k }, (_, i) => ({ tail: SEP + CONF_TAIL + (i + 1), pt: "conf:" + (i + 1), xyz: "" }));
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
    // the copied constraints name atoms: the structure it lands on has them
    const need = wfAtomNeed(wf), have = atomsOfCalc(P.members[0].name);
    if (need && have && need > have) {
      NB.fail(`The workflow's constraints name atom ${need}, but ${P.short} has ${have} atoms.`);
      return;
    }
    const sysId = sys.id, laneName = String(sys.name || ""), charge = int(sys.charge), mult = int(sys.multiplicity) || 1;
    const r = await avoidDisk(extra => {
      const taken = new Set(NB.queue.map((/** @type {any} */ c) => lower(c.name)));
      extra.forEach(x => taken.add(x));
      /** @type {Map<string, string>} */ const nodeIds = new Map();
      const outs = passes.map(e => instantiate(wf, { sysId: sysId, laneName: laneName, charge: charge, mult: mult,
        xyz: "", parent: P.members[0].name, col0: col0, row0: row0, taken: taken,
        tail: e.tail, refPoint: e.pt, rootXyz: e.xyz, nodeIds: nodeIds, filterDe: filterDe }));
      const fresh = outs.reduce((/** @type {string[]} */ a, o) => a.concat(o.fresh), []);
      return { ops: outs.reduce((/** @type {any[]} */ a, o) => a.concat(o.ops), []),
               fresh: fresh, first: outs[0].first, names: fresh };
    });
    if (!(await guardOverwrite(r.names))) return;
    const order = { op: "order", names: orderWith(NB.queue.map((/** @type {any} */ c) => c.name), P.lane, col0, row0, r.fresh) };
    const inv = r.fresh.slice().reverse().map(x => ({ op: "remove_calc", name: x })).concat(pre.inv);
    await applySel(pre.ops.concat(r.ops, [order]), inv, "the pasted workflow", { t: "node", key: r.first, m: 0 });
  }

  /** A workflow as the first steps of rows, in ONE batch: every empty row
   *  gets it on its first structure, with its own charge and multiplicity;
   *  a row that already has steps, or whose structure has fewer atoms than
   *  the workflow's constraints name, is listed with the reason and left as
   *  it is. @param {any[]} lanes systems @param {any} wf
   *  @returns {Promise<boolean>} whether anything was pasted */
  async function pasteIntoSystem(lanes, wf) {
    const need = wfAtomNeed(wf);
    /** @type {{row: string, reason: string}[]} */ const refused = [];
    /** @type {{L: any, xyz: string}[]} */ const targets = [];
    for (const L of lanes) {
      if (!L || !L.sys) continue;
      if (L.calcs.length) {
        refused.push({ row: L.name, reason: "It already has steps: select its last block and paste after it." });
        continue;
      }
      const sf = await systemFull(L.sys.id);
      const xyz = sf && sf.inputs && sf.inputs[0] ? String(sf.inputs[0].xyz || "") : "";
      const have = xyzAtoms(xyz);
      if (need && have && need > have) {
        refused.push({ row: L.name, reason: `The workflow's constraints name atom ${need}, but its structure has ${have} atoms.` });
        continue;
      }
      targets.push({ L: L, xyz: xyz });
    }
    let ok = false;
    if (targets.length) {
      const r = await avoidDisk(extra => {
        const taken = new Set(NB.queue.map((/** @type {any} */ c) => lower(c.name)));
        extra.forEach(x => taken.add(x));
        /** @type {any[]} */ const ops = [];
        /** @type {string[]} */ const fresh = [];
        let first = "";
        targets.forEach(x => {
          const s = x.L.sys;
          const out = instantiate(wf, { sysId: s.id, laneName: String(s.name || ""), charge: int(s.charge),
            mult: int(s.multiplicity) || 1, xyz: x.xyz, parent: "", col0: 0, row0: 0, taken: taken });
          ops.push(...out.ops); fresh.push(...out.fresh);
          if (!first) first = out.first;
        });
        return { ops: ops, fresh: fresh, first: first, names: fresh };
      });
      if (await guardOverwrite(r.names)) {
        const order = { op: "order", names: NB.queue.map((/** @type {any} */ c) => c.name).concat(r.fresh) };
        // one row: its first new block is selected; several: they stay selected
        ok = await applySel(r.ops.concat([order]), r.fresh.slice().reverse().map(x => ({ op: "remove_calc", name: x })),
                            "the pasted workflow",
                            targets.length > 1 && _sel ? _sel : { t: "node", key: r.first, m: 0 });
        if (ok && targets.length > 1) NB.toast(`${targets.length} rows now run ${wfSummary(wf)}.`, "ok");
      }
    }
    if (refused.length) {
      NB.modalRaw(`${refused.length} row${refused.length === 1 ? " was" : "s were"} left as ${refused.length === 1 ? "it was" : "they were"}`,
        `<div class="names">${NB.nameList(refused.map(x => `${esc(x.row)} — ${esc(x.reason)}`))}</div>`);
    }
    return ok;
  }

  /** a name ORCA or a folder name refuses (store._validate_calc_name) */
  const HOSTILE = /[\\/:*?"<>|\s&,;=]/;

  /** One new row per .xyz file running the workflow -- or, with no workflow
   *  (a drop), holding just its structure -- named after the file, in ONE
   *  batch. A file that cannot start a row (unreadable, several frames, a
   *  name ORCA refuses, a row of that name already there) is listed with its
   *  reason, never renamed to fit.
   *  @param {any} wf null for structure-only rows @param {any[]} files XyzFileEntry
   *  @param {string} label @returns {Promise<boolean>} whether any row was added */
  async function pasteOnFiles(wf, files, label) {
    const need = wf ? wfAtomNeed(wf) : 0;
    /** The batch for these files, with `extra` names taken as well (folders
     *  in the workspace that hold output). @param {Set<string>} extra */
    const plan = (extra) => {
      /** @type {{file: string, reason: string}[]} */ const refused = [];
      /** @type {any[]} */ const ops = [];
      /** @type {string[]} */ const fresh = [], ids = [];
      const rows = new Set(NB.systems.map((/** @type {any} */ s) => lower(s.name)));
      const taken = new Set(NB.queue.map((/** @type {any} */ c) => lower(c.name)));
      extra.forEach(x => taken.add(x));
      let first = "", stated = 0;
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
        // the copied constraints name atoms: each structure is checked
        const have = xyzAtoms(String(f.xyz || ""));
        if (need && have && need > have) {
          refused.push({ file: file, reason: `The workflow's constraints name atom ${need}; this structure has ${have} atoms.` });
          return;
        }
        rows.add(lower(nm));
        const id = newId("s");
        // the charge / multiplicity the file's comment line states (charge=1 mult=2)
        const charge = typeof f.charge === "number" ? f.charge : 0;
        const mult = typeof f.multiplicity === "number" ? f.multiplicity : 1;
        if (typeof f.charge === "number" || typeof f.multiplicity === "number") stated++;
        ops.push({ op: "add_system", system: { id: id, name: nm, charge: charge, multiplicity: mult,
                   inputs: [{ label: file || nm + ".xyz", xyz: String(f.xyz || "") }] } });
        ids.push(id);
        if (!wf) return;
        const r = instantiate(wf, { sysId: id, laneName: nm, charge: charge, mult: mult, xyz: String(f.xyz || ""),
                                    parent: "", col0: 0, row0: 0, taken: taken });
        ops.push(...r.ops);
        fresh.push(...r.fresh);
        if (!first) first = r.first;
      });
      return { names: fresh, refused: refused, ops: ops, fresh: fresh, ids: ids, first: first, stated: stated };
    };
    const p = await avoidDisk(plan);
    let ok = false;
    if (p.ids.length && await guardOverwrite(p.names)) {
      if (p.fresh.length) p.ops.push({ op: "order", names: NB.queue.map((/** @type {any} */ c) => c.name).concat(p.fresh) });
      const inv = /** @type {any[]} */ (p.fresh.slice().reverse().map(x => ({ op: "remove_calc", name: x })))
        .concat(p.ids.map(id => ({ op: "remove_system", id: id })));
      // structure-only rows are all selected: a workflow (Ctrl+V, or a saved
      // one clicked in the library) and a charge then go on every one at once
      const keys = p.ids.map(id => "s:" + id);
      ok = await applySel(p.ops, inv, label, wf ? { t: "node", key: p.first, m: 0 }
        : keys.length > 1 ? { t: "rows", keys: keys } : { t: "sys", key: keys[0] });
      const read = p.stated ? ` (charge / multiplicity read from ${p.stated === p.ids.length ? "the file" + (p.stated === 1 ? "" : "s")
        : p.stated + " of the files"})` : "";
      const tail = wf ? ", each running " + wfSummary(wf) + "."
        : p.ids.length > 1 ? ". They are selected: Ctrl+V or a saved workflow fills every one."
        : ": press its + slot to add a step.";
      if (ok) NB.toast(`${p.ids.length} row${p.ids.length === 1 ? "" : "s"} added${read}${tail}`, "ok");
    }
    if (p.refused.length) {
      NB.modalRaw(`${p.refused.length} file${p.refused.length === 1 ? " was" : "s were"} not added`,
        `<div class="names">${NB.nameList(p.refused.map(x =>
          `${esc(String(x.file).split(/[\\/]/).pop())} — ${esc(x.reason)}`))}</div>`);
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

  /** Several rows by Ctrl/Shift+click on their names: add or drop one.
   *  @param {string} key */
  function toggleRow(key) {
    const L = laneByKey(key);
    if (!L || !L.sys) return;
    const keys = _sel && _sel.t === "rows" ? _sel.keys.slice() : _sel && _sel.t === "sys" ? [_sel.key] : [];
    const i = keys.indexOf(key);
    if (i >= 0) keys.splice(i, 1); else keys.push(key);
    _expanded = "";
    select(keys.length > 1 ? { t: "rows", keys: keys } : keys.length ? { t: "sys", key: keys[0] } : null);
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
    // a band over one block selects it whole: a stack's Remove then takes
    // every member (a click takes the one shown)
    select(keys.length > 1 ? { t: "multi", lane: h.lane, keys: keys, ignored: h.ignored }
      : keys.length ? { t: "node", key: keys[0], m: 0, all: true } : null);
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
    const rename = (/** @type {string} */ base, /** @type {string} */ x) =>
      lower(x).indexOf(lower(oldBase)) === 0 ? base + x.slice(oldBase.length) : base + SEP + x;
    // a copy's names are clear of the queue and of folders holding output
    const pick = await avoidDisk(extra => {
      const taken = new Set(NB.queue.map((/** @type {any} */ c) => lower(c.name))
        .concat(NB.systems.map((/** @type {any} */ s) => lower(s.name))));
      extra.forEach(x => taken.add(x));
      let base = oldBase + SEP + "copy", k = 2;
      while (taken.has(lower(base)) || laneNames(L).some((/** @type {string} */ x) => taken.has(lower(rename(base, x))))) {
        base = oldBase + SEP + "copy" + (k++);
      }
      return { nm: base, names: laneNames(L).map((/** @type {string} */ x) => rename(base, x)) };
    });
    if (!(await guardOverwrite(pick.names))) return;
    const nm = pick.nm;
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

  /** Dropped structures: one row each, holding the file's structure and
   *  nothing queued yet -- the steps are built on it (a level on its + slot,
   *  a manual build, a pasted workflow). The rows are pasteOnFiles' with no
   *  workflow: one batch, named after the files, a refused file listed with
   *  its reason, never renamed to fit. Runs in both windows (the main
   *  window hands its drops here).
   *  @param {string[]} paths @returns {Promise<boolean>} whether any row was added */
  async function dropXyz(paths) {
    const list = (paths || []).filter(p => /\.xyz$/i.test(String(p)));
    if (!list.length) return false;
    const rr = await NB.call("read_xyz_paths", JSON.stringify(list));
    if (!rr || rr.ok !== true) { NB.fail(why(rr, "Could not read the structures")); return false; }
    return pasteOnFiles(null, Array.isArray(rr.files) ? rr.files : [], "the dropped structures");
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
      // a level's block opens in Level mode; an MLIP or CREST block in the
      // builder's own form for it, saved as one undoable batch; anything
      // else in the builder
      if (m.type === "edit" && c && EDITABLE.indexOf(c.state) >= 0) {
        if (isLevelCalc(c)) openLevelBuilder(m.name);
        else if (/^(mlip|crest)/.test(String(c.kind || ""))) openSettingsBuilder(m.name);
        else openBuilder(m.name);
      }
      if (n) {
        const el = NB.$("qe-graph").querySelector(`.qe-node[data-k="${CSS.escape(n.key)}"]`);
        if (el) el.scrollIntoView({ block: "nearest", inline: "nearest" });
      }
    }
  }

  // ------------------------------------------------------------ events

  /** @param {any} s */
  function select(s) {
    if (JSON.stringify(s) !== JSON.stringify(_sel)) _refusal = null;
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
      else if (a === "sys" && (ev.ctrlKey || ev.metaKey || ev.shiftKey)) toggleRow(b.getAttribute("data-lane") || "");
      else if (a === "sys") select({ t: "sys", key: b.getAttribute("data-lane") });
      else if (a === "ghost") {
        const slot = (g._ghosts || []).find((/** @type {any} */ x) => x.key === b.getAttribute("data-slot"));
        if (slot) openAddMenu(slot, /** @type {HTMLElement} */ (b));
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
      const gh = /** @type {HTMLElement} */ (ev.target).closest(".qe-ghost, .qe-ins");
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
      if (_drag.t === "wf") {
        const w = _wfs.find(x => x.id === _drag.id);
        endDrag();
        if (w && ok.slot) workflowAtSlot(ok.slot, wfOf(w));
        return;
      }
      const set = _sets.find(s => s.id === _drag.id);
      endDrag();
      if (ok.node) requestReplace(ok.node, set);
      else if (ok.slot) addFromSet(ok.slot, set);
    });
  }

  /** What a dragged set (or saved workflow: a + slot only) would land on
   *  under `t`, or null. @param {HTMLElement} t @returns {any} */
  function dropTarget(t) {
    const set = _drag && _drag.t === "set" ? _sets.find(s => s.id === _drag.id) : null;
    const wf = _drag && _drag.t === "wf" ? _wfs.find(w => w.id === _drag.id) : null;
    if (!set && !wf) return null;
    // the + before a block takes a set (a workflow goes after a block)
    const ins = t.closest(".qe-ins");
    if (ins) {
      const slot = set ? (NB.$("qe-graph")._ghosts || []).find((/** @type {any} */ x) => x.key === ins.getAttribute("data-slot")) : null;
      return slot ? { ghost: ins, slot: slot } : null;
    }
    const gh = t.closest(".qe-ghost");
    if (gh) {
      // a slot the Guided builder has claimed takes no set: two blocks would share it
      if (gh.hasAttribute("data-picking")) return null;
      const slot = (NB.$("qe-graph")._ghosts || []).find((/** @type {any} */ x) => x.key === gh.getAttribute("data-slot"));
      return slot ? { ghost: gh, slot: slot } : null;
    }
    const nd = t.closest(".qe-node");
    // a block that can still change, or a finished one (an overwrite, asked)
    if (set && nd && nd.getAttribute("data-setkind") === set.kind) {
      const n = nodeByKey(nd.getAttribute("data-k"));
      return takesSet(n) ? { node: n } : null;
    }
    return null;
  }

  /** Mark what a dragged set can land on, without redrawing the canvas (a
   *  redraw mid-drag would drop the drag). */
  function showSetTargets() {
    const set = _drag && _drag.t === "set" ? _sets.find(s => s.id === _drag.id) : null;
    const wf = _drag && _drag.t === "wf" ? _wfs.find(w => w.id === _drag.id) : null;
    if (!set && !wf) return;
    const g = NB.$("qe-graph");
    if (set) g.querySelectorAll(".qe-node").forEach((/** @type {HTMLElement} */ nd) => {
      if (nd.getAttribute("data-setkind") === set.kind && takesSet(nodeByKey(nd.getAttribute("data-k")))) nd.setAttribute("data-drop", "1");
    });
    g.querySelectorAll(".qe-ghost").forEach((/** @type {HTMLElement} */ gh) => {
      if (!gh.hasAttribute("data-picking")) gh.setAttribute("data-drop", "1");
    });
    if (set) g.querySelectorAll(".qe-ins").forEach((/** @type {HTMLElement} */ b) => b.setAttribute("data-drop", "1"));
    const b = NB.$("qe-sets").querySelector(set ? `[data-id="${CSS.escape(set.id)}"]` : `[data-wf="${CSS.escape(String(_drag.id))}"]`);
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
      if (t.closest("[data-wfsave]")) { if (_clip) saveWorkflowAs(_clip); return; }
      const wb = t.closest("button.qe-set[data-wf]");
      if (wb) {
        const w = _wfs.find(x => x.id === wb.getAttribute("data-wf"));
        if (w) useWorkflow(w);
        return;
      }
      const b = t.closest("button.qe-set");
      if (!b) return;
      const set = _sets.find(s => s.id === b.getAttribute("data-id"));
      if (!set) return;
      const n = selNode();
      // on the selected block when the kinds match and it can still change
      // (or has finished: an overwrite, asked first); otherwise the click
      // opens the set in the editor
      if (n && set.kind === n.setKind && takesSet(n)) { requestReplace(n, set); return; }
      openSet(set);
    });
    host.addEventListener("dragstart", (/** @type {DragEvent} */ ev) => {
      const b = /** @type {HTMLElement} */ (ev.target).closest(".qe-set");
      if (!b) return;
      const wid = b.getAttribute("data-wf");
      const id = wid || b.getAttribute("data-id");
      try {
        ev.dataTransfer.effectAllowed = "copy";
        ev.dataTransfer.setData("text/plain", b.textContent);
        const im = document.getElementById((wid ? "qe-di-wf-" : "qe-di-") + id);
        if (im) ev.dataTransfer.setDragImage(im, 28, 22);
      } catch (e) { /* */ }
      _drag = { t: wid ? "wf" : "set", id: id };
      setTimeout(showSetTargets, 0);
    });
    host.addEventListener("dragend", () => { if (_drag && (_drag.t === "set" || _drag.t === "wf")) endDrag(); });
    host.addEventListener("keydown", (/** @type {KeyboardEvent} */ ev) => {
      if (ev.key !== "Delete") return;
      const wb = /** @type {HTMLElement} */ (ev.target).closest(".qe-set[data-wf]");
      const w = wb ? _wfs.find(x => x.id === wb.getAttribute("data-wf")) : null;
      if (w) { ev.preventDefault(); ev.stopPropagation(); deleteWorkflow(w); return; }
      const b = /** @type {HTMLElement} */ (ev.target).closest(".qe-set");
      const set = b ? _sets.find(s => s.id === b.getAttribute("data-id")) : null;
      if (!set) return;
      ev.preventDefault();
      ev.stopPropagation();
      deleteSet(set);
    });
  }

  function wireKeys() {
    document.addEventListener("keydown", (/** @type {KeyboardEvent} */ ev) => {
      if (!_active || !NB.$("modal").hidden || !NB.$("settings").hidden) return;
      const t = /** @type {HTMLElement} */ (ev.target);
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
      // the builder's own keys, the library's (Delete there deletes a set)
      // and the add menu's (its letters add a step, Escape closes it)
      if (t && t.closest && (t.closest(".qe-build") || t.closest(".qe-lib") || t.closest(".qe-addmenu"))) return;
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
      // Alt+Left / Alt+Right: the selected block runs earlier / later in its
      // row (Alt+Up / Down on a grip moves the whole row)
      if (ev.altKey && !ev.ctrlKey && (ev.key === "ArrowLeft" || ev.key === "ArrowRight")) {
        const c = selMember();
        if (c) { ev.preventDefault(); moveRun(c, ev.key === "ArrowLeft" ? -1 : 1); }
        return;
      }
      if (ev.key === "Delete" || ev.key === "Backspace") {
        const pick = removeChoice();
        if (pick) { ev.preventDefault(); requestRemove(pick); return; }
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
          <div class="qe-dragimgs" id="qe-dragimgs" aria-hidden="true"></div>
        </aside>
        <main class="qe-main" aria-label="Queue">
          <div class="qe-bar">
            <span class="qe-barsp"></span>
            <button class="btn btn-ghost qe-tbtn" type="button" id="qe-paste" disabled
              title="Copy blocks first: select them (drag a box on the empty canvas, or Ctrl+click) and press Ctrl+C">Paste workflow</button>
            <button class="btn btn-ghost qe-tbtn" type="button" id="qe-clearfin">Clear finished</button>
            <button class="btn btn-ghost qe-tbtn" type="button" id="qe-clear">Clear all</button>
            <button class="btn btn-ghost qe-tbtn" type="button" id="qe-cancel">Cancel</button>
            <button class="btn btn-ghost qe-tbtn" type="button" id="qe-stop">Stop after current</button>
            <button class="btn btn-primary" type="button" id="qe-run">Run queue</button>
          </div>
          <p class="qe-warn" id="qe-warn" role="status" hidden></p>
          <div class="qe-canvas" id="qe-canvas"><div class="qe-graph" id="qe-graph"></div></div>
        </main>
        <aside class="qe-insp" id="qe-insp" aria-label="Inspector"></aside>
        <div class="qe-build" id="qe-build" hidden><div class="streaminner" id="qe-buildinner"></div></div>
      </div>`;
    NB.$("qe-run").addEventListener("click", () => NB.rail.runQueue());
    NB.$("qe-stop").addEventListener("click", () => NB.rail.stopAfterCurrent());
    NB.$("qe-cancel").addEventListener("click", () => NB.rail.cancelRun());
    NB.$("qe-clear").addEventListener("click", () => NB.rail.clearAll());
    NB.$("qe-clearfin").addEventListener("click", clearFinished);
    NB.$("qe-paste").addEventListener("click", () => paste());
    wireCanvas();
    wireLibrary();
    wireKeys();
    wirePress(host);
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
    await loadWorkflows();
    _clip = await loadClip();
    const lv = await NB.call("get_ui_state", LEVEL_KEY);
    _lastLevel = lv && lv.ok && typeof lv.value === "string" ? lv.value : "";
    const d = await NB.call("get_step_defaults");
    _stepDef = d && !d.error ? d : {};
    sync();
  }

  NB.qed = { init: init, render: render, dropXyz: dropXyz, dropInp: dropInp,
             /** whether the inp-set editor has edits not saved (boot.js asks before a reload) */
             isDirty: () => edDirty() };
})();
