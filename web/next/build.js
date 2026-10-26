// @ts-check
"use strict";
/* ============================================================
   ORCAdesk notebook front-end — the builder.

   The form is not a place you go to. It opens where the calculation lives: at
   the end of the rail for a new one, in the row itself when an existing one is
   edited. Two ways to say the same thing — a guided form, or the .inp text —
   and the same payload comes out of both.

   Everything it offers comes from the backend: the option lists via
   load_choices(), the generated input via build_inp_preview(), the queue
   mutation via add_calc()/update_calc(). Nothing about ORCA is decided here.
   ============================================================ */

(function () {

  /* Per-kind form shape, mirroring core/queue.py's KNOWN_KINDS and the
     generator's expectations: which calculation-type group to offer, the
     convergence default that kind deserves, and the extra block it needs. */
  const KINDS = {
    opt:          { label: "Optimization",     group: "calculation_types_geometry",  calc: "TightOpt",      scf: "TightSCF",     maxiter: true, cons: true },
    opt_freq:     { label: "Opt + Freq",       group: null,                          calc: "TightOpt Freq", scf: "VeryTightSCF", maxiter: true, cons: true,  freq: true },
    ts_opt:       { label: "TS optimization",  group: "calculation_types_geometry",  calc: "OptTS",         scf: "TightSCF",     maxiter: true, cons: true },
    ts_opt_freq:  { label: "TS Opt + Freq",    group: null,                          calc: "OptTS Freq",    scf: "VeryTightSCF", maxiter: true, cons: true,  freq: true },
    freq:         { label: "Frequencies",      group: "calculation_types_frequency", calc: "Freq",          scf: "VeryTightSCF", freq: true },
    ts_freq:      { label: "TS frequencies",   group: "calculation_types_frequency", calc: "Freq",          scf: "VeryTightSCF", freq: true },
    neb_ts:       { label: "NEB-TS",           group: null,                          calc: "NEB-TS",        scf: "TightSCF",     neb: true, options: "FREQ" },
    irc:          { label: "IRC",              group: null,                          calc: "IRC",           scf: "TightSCF",     irc: true },
    tddft:        { label: "TD-DFT",           group: null,                          calc: "",              scf: "TightSCF",     tddft: true },
    nmr:          { label: "NMR",              group: null,                          calc: "NMR",           scf: "TightSCF",     nmr: true },
    sp:           { label: "Single point",     group: "calculation_types_energy",    calc: "SP",            scf: "TightSCF" },
    general:      { label: "General",          group: null,                          calc: "",              scf: "TightSCF",     allTypes: true },
  };
  const KIND_ORDER = ["opt", "opt_freq", "ts_opt", "ts_opt_freq", "freq", "ts_freq",
                      "neb_ts", "irc", "tddft", "nmr", "sp", "general"];

  /** @type {Record<string, any>} option groups from data/*.json, loaded once */
  const CHOICES = {};
  /** StepConfig's own defaults (get_step_defaults), loaded once. A new
   *  calculation is seeded from these and from nothing else: this file kept
   *  its own numbers once, and they drifted from the backend's — MaxIter 0
   *  (clamped to 1), IRC MaxIter 40 for 100, TD-DFT maxdim 0 (clamped to 1).
   *  @type {Record<string, any>} */
  let DEF = {};
  let _loaded = false;

  async function loadChoices() {
    if (_loaded) return;
    const names = ["functionals", "basis_sets", "calculation_types",
                   "scf_convergences", "ri_approximations", "solvents"];
    await Promise.all([...names.map(async n => {
      const g = await NB.call("load_choices", n);
      CHOICES[n] = (g && !g.error) ? g : {};
    }), NB.call("get_step_defaults").then(r => { DEF = (r && !r.error) ? r : {}; })]);
    _loaded = true;
  }

  /** Flatten the grouped choice file into a plain list, optionally one group
   *  only. The "_source"/"_note" keys are documentation, not options.
   *  @param {any} groups @param {string} [only] @returns {string[]} */
  function items(groups, only) {
    const out = [];
    Object.keys(groups || {}).forEach(k => {
      if (k.charAt(0) === "_") return;
      if (only && k !== only) return;
      const v = groups[k];
      const list = Array.isArray(v) ? v : (v && Array.isArray(v.keywords) ? v.keywords : null);
      if (list) list.forEach(x => { if (out.indexOf(x) < 0) out.push(x); });
    });
    return out;
  }

  let _uid = 0;
  /** A free-text field with suggestions — a combo, not a closed picker: ORCA
   *  accepts keywords these lists do not carry, and a closed <select> would
   *  make those unreachable (D63).
   *  @param {string} label @param {string[]} opts @param {string} value */
  function combo(label, opts, value) {
    const id = "dl" + (++_uid);
    return `<div class="field"><label>${NB.esc(label)}</label>
      <input class="mono" list="${id}" value="${NB.esc(value)}">
      <datalist id="${id}">${opts.map(o => `<option value="${NB.esc(o)}"></option>`).join("")}</datalist>
    </div>`;
  }
  /** @param {string} label @param {string[]} opts @param {string} value @param {string} [cls] */
  function select(label, opts, value, cls) {
    return `<div class="field ${cls || ""}"><label>${NB.esc(label)}</label><select class="mono">${
      opts.map(o => `<option value="${NB.esc(o)}"${o === value ? " selected" : ""}>${NB.esc(o)}</option>`).join("")
    }</select></div>`;
  }
  /** @param {string} label @param {string|number} value @param {string} [cls] @param {string} [type] */
  function text(label, value, cls, type) {
    return `<div class="field ${cls || ""}"><label>${NB.esc(label)}</label>
      <input type="${type || "text"}" class="${type === "number" ? "mono" : ""}" value="${NB.esc(String(value))}"></div>`;
  }

  /* The blocks a hand-written .inp is actually built from. {{GEOMETRY}} is the
     generator's own placeholder (input_generator.GEOMETRY_PLACEHOLDER): raw
     input carrying it gets the loaded coordinates substituted, raw input
     without it must carry its own * xyz block. The rest are the %-blocks ORCA
     takes, at their commonest setting, as something to edit rather than recall.
     @type {[string, string][]} */
  const SNIPPETS = [
    ["{{GEOMETRY}}", "{{GEOMETRY}}"],
    ["%scf", "%scf\n  MaxIter 250\nend"],
    ["%basis", "%basis\n  newgto Fe \"def2-TZVPP\" end\nend"],
    ["%geom", "%geom\n  MaxIter 200\nend"],
    ["%plots", "%plots\n  dim1 60\n  dim2 60\n  dim3 60\nend"],
    ["%eprnmr", "%eprnmr\n  Nuclei = all H { shift }\n  Nuclei = all C { shift }\nend"],
    ["%tddft", "%tddft\n  NRoots 10\n  TDA true\nend"],
    ["%cpcm", "%cpcm\n  smd true\n  SMDsolvent \"water\"\nend"],
    ["%irc", "%irc\n  MaxIter 40\n  InitHess calc_anfreq\nend"],
  ];

  /** Which calculation the builder is open on: null = closed, "" = a new one,
   *  otherwise the name being edited. The stream reads it. @type {string|null} */
  let _target = null;

  /** What a NEW calculation starts from, when it starts from something: the
   *  partial calc "New from this" hands over. Cleared as soon as it is used,
   *  so the next new calculation is a blank one. @type {any} */
  let _seed = null;

  /** Open the builder on a calculation, or on a new one with "". Closing is
   *  open(null).
   *  @param {string|null} target
   *  @param {any} [seed] for a new one: fields it opens already carrying */
  function open(target, seed) {
    // a seeded open is never a toggle -- clicking "New from this" on a second
    // calculation while the builder is open must move it, not close it
    _target = (_target === target && !seed) ? null : target;
    _seed = _target === null ? null : (seed || null);
    NB.stream.render();
    if (_target !== null) {
      const cell = document.querySelector(".cell.editing, .cell.stub.editing");
      if (cell) cell.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }
  function target() { return _target; }

  /** One per-element override. Its labels are deliberately not the method
   *  block's ("Basis set"): readForm finds fields by label text, and two fields
   *  called the same thing in one form is how the wrong value gets saved.
   *  @param {any} a a BasisAssignment @returns {string} */
  function basisRow(a) {
    const v = (k) => NB.esc(String((a && a[k]) || ""));
    return `<div class="basis-row">
      <div class="field narrow"><label>Element</label>
        <input class="mono" data-b="element" value="${v("element")}" placeholder="Fe"></div>
      <div class="field"><label>Basis</label>
        <input class="mono" data-b="basis" value="${v("basis")}" placeholder="def2-TZVPP"></div>
      <div class="field"><label>ECP</label>
        <input class="mono" data-b="ecp" value="${v("ecp")}" placeholder="def2-ECP"></div>
      <button class="rm" type="button" aria-label="Remove this override">×</button>
    </div>`;
  }

  /** The builder as a CELL — the design edits a calculation where it is read.
   *  @param {any} calc a CalcSummary, or null for a new calculation
   *  @returns {HTMLElement} */
  function cellFor(calc) {
    const el = NB.el("article", "cell editing" + (calc ? "" : " stub"));
    el.dataset.name = calc ? calc.name : "";
    el.innerHTML = `
      <div class="editbar">
        <b>${calc ? "Editing " + NB.esc(calc.name) : "New calculation"}</b>
        ${calc ? "<span>changes apply to the queued calculation</span>" : ""}
        <span class="sp"></span>
        <button class="iconbtn ebclose" type="button" data-act="cancel"
                aria-label="Close the builder" title="Close">×</button>
      </div>
      <div class="stubbody"><p class="hint" style="margin:0">Loading options…</p></div>`;
    el.querySelector('[data-act="cancel"]').addEventListener("click", () => open(null));
    mount(el, calc, () => open(null));
    return el;
  }

  /** @param {HTMLElement} el @param {any} summary @param {() => void} close */
  async function mount(el, summary, close) {
    await loadChoices();
    const body = /** @type {HTMLElement} */ (el.querySelector(".stubbody"));
    // Editing needs the FULL calculation (the summary in the queue carries no
    // config), and it has to come from the store rather than be reconstructed.
    let calc = null;
    if (summary) {
      const g = await NB.call("get_calc", summary.name);
      if (g && g.ok && g.calc) calc = g.calc;
      if (!calc) {
        body.innerHTML = `<p class="hint" style="margin:0">Could not read "${NB.esc(summary.name)}".</p>`;
        return;
      }
    }
    // A new calculation started FROM another one opens carrying that one's
    // name as its geometry source -- the same reference an opt->freq handoff
    // uses, resolved to the optimized geometry at run time. It is a seed, not
    // an edit: nothing is saved until Add.
    if (!calc && _seed) { calc = _seed; _seed = null; }
    const st = {
      editing: summary ? summary.name : "",
      raw: calc ? !!calc.is_raw : false,
      kind: calc ? calc.kind : "opt",
      xyz: calc ? (calc.xyz || "") : "",
      rawText: calc ? (calc.raw_text || "") : "",
      // the constraints live here rather than in the form: they are picked on
      // the stage, and a re-render (a kind switch, Guided <-> .inp) must not
      // lose them — a switch to a kind that cannot use them only hides them
      cons: ((calc && calc.config && calc.config.geom_constraints) || [])
        .map(c => ({ kind: c.kind, atoms: c.atoms.slice(), value: c.value })),
      /** @type {any} the structure stage, destroyed on the next render */
      stage: null,
    };
    if (!KINDS[st.kind]) st.kind = "general";
    render(body, st, calc, close);
  }

  /** @param {HTMLElement} el @param {any} st @param {any} calc @param {() => void} close */
  function render(el, st, calc, close) {
    if (st.stage) { st.stage.destroy(); st.stage = null; }
    const K = KINDS[st.kind];
    const cfg = (calc && calc.config) || {};
    const d = NB.settings || {};
    const refs = NB.queue
      .filter(c => c.name !== st.editing && (c.state === "done" || c.state === "pending" || c.state === "running"))
      .map(c => c.name);
    const src = calc ? calc.geometry_source : "direct";

    const calcTypes = K.allTypes ? items(CHOICES.calculation_types)
                     : K.group ? items(CHOICES.calculation_types, K.group)
                     : [];

    el.innerHTML = `
      <div class="backendrow">
        <div class="seg" role="group" aria-label="Input mode">
          <button type="button" data-mode="form" aria-pressed="${!st.raw}">Guided</button>
          <button type="button" data-mode="raw" aria-pressed="${st.raw}">.inp text</button>
        </div>
      </div>

      <div class="field-row">
        ${text("Name", calc ? calc.name : "", "")}
        <div class="field mid"><label>Type</label><select data-f="kind">${
          KIND_ORDER.map(k => `<option value="${k}"${
            k === st.kind ? " selected" : ""}>${KINDS[k].label}</option>`).join("")
        }</select></div>
        ${text("Charge", calc ? calc.charge : 0, "narrow", "number")}
        ${text("Mult.", calc ? calc.multiplicity : 1, "narrow", "number")}
      </div>

      <div class="divider"></div>
      <div class="grouplabel">Geometry source</div>
      <div class="field-row" style="gap:20px">
        <label class="checkbox"><input type="radio" name="gsrc" value="direct"
          ${src !== "reference" ? "checked" : ""}> Coordinates</label>
        <label class="checkbox"><input type="radio" name="gsrc" value="reference"
          ${src === "reference" ? "checked" : ""}> From another calculation</label>
      </div>
      <div data-g="direct"${src === "reference" ? " hidden" : ""}>
        <div class="btn-group" style="align-items:center;margin-bottom:10px">
          <button class="btn btn-sm" type="button" data-act="loadxyz">Load .xyz…</button>
          <button class="btn btn-sm btn-ghost" type="button" data-act="loadhess"
                  title="Push the coordinates along a vibrational mode of a frequency run">Load .hess…</button>
          <span class="hint" style="margin:0" data-x="status">${
            st.xyz ? st.xyz.trim().split("\n").length + " atoms" : ""}</span>
        </div>
        <!-- the coordinates and the structure they describe, side by side: the
             stage redraws as the text changes, and is where constraints are
             picked (DESIGN.md §17.4l) -->
        <div class="geomsplit">
          <div class="field"><textarea rows="7" data-f="xyz" spellcheck="false"
            aria-label="Coordinates"
            placeholder="C   0.000   0.000   0.000">${NB.esc(st.xyz)}</textarea></div>
          <div class="geomstage">
            <canvas class="molcv" data-v="geomcv"
                    aria-label="The structure in the coordinates box"></canvas>
            <p class="geomempty" data-x="geomempty">No coordinates yet</p>
            ${K.cons && !st.raw ? `<div class="stageacts">
              <button class="btn btn-sm" type="button" data-act="fixatom" disabled
                      title="Hold the selected atom's position">Fix atom</button></div>` : ""}
          </div>
        </div>
        <!-- push along a mode: a frequency run's .hess, a mode, how far. The
             stage previews it; Apply puts it in the box (DESIGN.md §17.4p) -->
        <div class="plate modepush" data-x="modepush" hidden>
          <div class="grouplabel">Push along a mode <span class="hint" data-x="hessname"></span></div>
          <div class="field-row">
            <div class="field"><label for="mp-mode">Mode</label>
              <select id="mp-mode" data-f="mode"></select></div>
            <div class="field narrow"><label>Direction</label>
              <div class="seg" role="group" aria-label="Direction">
                <button type="button" data-dir="1" aria-pressed="true">+</button>
                <button type="button" data-dir="-1" aria-pressed="false">−</button></div></div>
            <div class="field"><label for="mp-amp">Push (Å, the atom that moves most)</label>
              <div class="mprange">
                <input id="mp-amp" type="range" min="0" max="1" step="0.01" value="0.2" data-f="amp">
                <input class="mono" type="number" min="0" max="2" step="0.01" value="0.2"
                       data-f="ampn" aria-label="Push, Å"></div></div>
          </div>
          <p class="hint" data-x="pushnote" style="margin:0 0 10px"></p>
          <button class="btn btn-sm btn-primary" type="button" data-act="pushapply">Apply to coordinates</button>
        </div>
        <!-- what is wrong with this structure, said BEFORE it costs hours of
             compute rather than after: a multiplicity the electron count cannot
             produce, two atoms on top of each other, coordinates in Bohr -->
        <div data-a="check"></div>
      </div>
      <div data-g="reference"${src === "reference" ? "" : " hidden"}>
        ${refs.length
          ? `<div class="field-row" style="margin-bottom:0">${
              select("Take the optimized geometry from", refs, calc ? calc.ref_name : refs[0])}</div>`
          : `<p class="hint" style="margin:0">No other calculation to take a geometry from yet.</p>`}
      </div>

      ${K.cons && !st.raw ? `<div data-cons>
        <div class="divider"></div>
        <div class="grouplabel">Constraints</div>
        <p class="hint" style="margin:0 0 10px" data-x="conshint"></p>
        <div class="conslist" data-a="cons"></div>
        <!-- the keyboard's way in, and the only one for a referenced geometry,
             which has no coordinates here to pick on -->
        <div class="consadd">
          <input class="mono" data-f="consatoms" spellcheck="false"
                 placeholder="atoms, e.g. 3 7" aria-label="Atoms to constrain, numbered from 1">
          <button class="btn btn-sm" type="button" data-act="consadd">Add</button>
        </div>
      </div>` : ""}

      <div data-form${st.raw ? " hidden" : ""}>
        <div class="divider"></div>
        <div class="grouplabel">Method &amp; options</div>
        <div class="field-row">
          ${combo("Functional", items(CHOICES.functionals), cfg.functional || DEF.functional)}
          ${combo("Basis set", items(CHOICES.basis_sets), cfg.basis_set || DEF.basis_set)}
        </div>
        <div class="field-row">
          ${select("RI", items(CHOICES.ri_approximations), cfg.ri_approximation || DEF.ri_approximation, "mid")}
          ${select("SCF convergence", items(CHOICES.scf_convergences), cfg.scf_convergence || K.scf, "mid")}
          ${calcTypes.length
            ? select("Calculation type", calcTypes, cfg.calculation_type || K.calc)
            : text("Calculation type", cfg.calculation_type != null ? cfg.calculation_type : K.calc)}
        </div>
        <div class="field-row">
          ${text("Extra keywords", cfg.options != null ? cfg.options : (K.options || ""))}
        </div>

        <div class="divider"></div>
        <div class="grouplabel">Solvation</div>
        <div class="field-row">
          ${select("Model", ["", "CPCM", "SMD"], (cfg.solvation && cfg.solvation.model) || "", "mid")}
          ${combo("Solvent", items(CHOICES.solvents), (cfg.solvation && cfg.solvation.solvent) || "")}
        </div>

        <div class="divider"></div>
        <div class="grouplabel">Resources</div>
        <div class="field-row">
          ${text("nprocs", cfg.nprocs || d.default_nprocs || DEF.nprocs, "narrow", "number")}
          ${text("maxcore (MB / core)", cfg.maxcore_mb || d.default_maxcore_mb || DEF.maxcore_mb, "mid", "number")}
          ${K.maxiter ? text("Max iterations", cfg.max_iter || DEF.max_iter, "mid", "number") : ""}
        </div>

        ${K.freq ? `<div class="divider"></div>
        <div class="grouplabel">Thermochemistry</div>
        <div class="field-row">
          ${text("Temperature (K)", cfg.freq_temp_k || DEF.freq_temp_k, "mid", "number")}
          ${text("Pressure (atm)", cfg.freq_pressure_atm || DEF.freq_pressure_atm, "mid", "number")}
        </div>` : ""}

        ${K.tddft ? `<div class="divider"></div>
        <div class="grouplabel">TD-DFT</div>
        <div class="field-row" style="align-items:center">
          ${text("Roots", cfg.tddft_nroots || DEF.tddft_nroots, "narrow", "number")}
          ${text("MaxDim", cfg.tddft_maxdim || DEF.tddft_maxdim, "narrow", "number")}
          <label class="checkbox"><input type="checkbox" data-f="tda"
            ${cfg.tddft_tda !== false ? "checked" : ""}> TDA</label>
          <label class="checkbox"><input type="checkbox" data-f="triplets"
            ${cfg.tddft_triplets ? "checked" : ""}> Triplets</label>
        </div>` : ""}

        ${K.nmr ? `<div class="divider"></div>
        <div class="grouplabel">NMR</div>
        <div class="field-row">
          <label class="checkbox"><input type="checkbox" data-f="jcoupling"
            ${cfg.nmr_jcoupling ? "checked" : ""}> Spin-spin coupling (J)</label>
        </div>` : ""}

        ${K.irc ? `<div class="divider"></div>
        <div class="grouplabel">IRC</div>
        <div class="field-row">
          ${text("Max points", cfg.irc_maxiter || DEF.irc_maxiter, "narrow", "number")}
          ${select("Direction", ["both", "forward", "backward"], cfg.irc_direction || DEF.irc_direction, "mid")}
          ${select("Initial Hessian", ["calc_anfreq", "calc_numfreq", "read"],
                   cfg.irc_init_hess || DEF.irc_init_hess, "mid")}
        </div>
        <!-- a picked .hess, by path: it is copied into the run folder at launch,
             so the Hessian a TS frequency run already paid for is reused
             rather than computed again -->
        <div class="btn-group" style="align-items:center;margin-top:10px">
          <button class="btn btn-sm" type="button" data-act="loadhess">Load .hess…</button>
          <span class="hint" style="margin:0" data-x="hess"></span>
          <button class="btn btn-sm btn-ghost" type="button" data-act="clearhess">Clear</button>
        </div>
        <input type="hidden" data-f="hessfile" value="${NB.esc(cfg.irc_hess_file || "")}">` : ""}

        ${K.neb ? `<div class="divider"></div>
        <div class="grouplabel">NEB endpoints</div>
        <p class="hint" style="margin:0 0 12px">The reactant is the geometry above; the
          product goes here. <b>Identical atoms in identical order in both</b> — build the
          product by copying the reactant and moving atoms, which never reorders anything.</p>
        <div class="nebrow">
          <div class="nebslot">
            <div class="lb">Reactant</div>
            <div class="fn" data-a="neb-react">—</div>
            <div class="hint" style="margin:6px 0 0">from the geometry source above</div>
          </div>
          <div class="nebslot">
            <div class="lb">Product</div>
            <div class="fn" data-a="neb-prod">no product loaded</div>
            <div class="btn-group" style="margin-top:10px">
              <button class="btn btn-sm" type="button" data-act="loadneb">Load product .xyz…</button>
            </div>
          </div>
        </div>
        <!-- the coordinates themselves, not a path: the generator writes the
             product block into the .inp, so what is stored is the block -->
        <textarea data-f="nebxyz" hidden>${NB.esc(cfg.neb_product_xyz || "")}</textarea>
        <div class="field-row" style="margin-top:14px;align-items:center">
          ${text("Images", cfg.neb_nimages || DEF.neb_nimages, "narrow", "number")}
          <label class="checkbox"><input type="checkbox" data-f="preopt"
            ${cfg.neb_preopt_ends ? "checked" : ""}> Pre-optimize ends</label>
        </div>
        <div class="btn-group" style="margin-top:12px;align-items:center">
          <button class="btn btn-sm btn-ghost" type="button" data-act="nebcompare">Compare endpoints</button>
          <span class="hint" style="margin:0">Checks that both endpoints carry the same
            atoms in the same order — a reordered atom makes the band meaningless.</span>
        </div>
        <div data-a="neb-verdict"></div>` : ""}

        <div class="divider"></div>
        <div style="display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:12px">
          <div class="grouplabel" style="margin:0">Per-element basis / ECP</div>
          <button class="btn btn-sm" type="button" data-act="addbasis">+ Element</button>
        </div>
        <div data-a="basis">${
          ((cfg.basis_assignments && cfg.basis_assignments.length)
            ? cfg.basis_assignments : []).map(basisRow).join("")}</div>
      </div>

      <div data-raw${st.raw ? "" : " hidden"}>
        <div class="divider"></div>
        <div class="btn-group" style="align-items:center;margin-bottom:10px">
          <button class="btn btn-sm" type="button" data-act="loadinp">Load .inp…</button>
        </div>
        <!-- the %-blocks, at the cursor: typing them out from memory is where
             a hand-written .inp usually goes wrong -->
        <div class="snips">
          <span class="hint" style="margin:0">Insert at the cursor:</span>
          ${SNIPPETS.map((sn, i) =>
            `<button class="btn btn-sm btn-ghost" type="button" data-snip="${i}">${
              NB.esc(sn[0])}</button>`).join("")}
        </div>
        <div class="field"><textarea class="raw-editor" data-f="rawtext" spellcheck="false"
          placeholder="! B3LYP def2-SVP Opt&#10;* xyz 0 1&#10;…&#10;*">${NB.esc(st.rawText)}</textarea></div>
      </div>

      <div class="divider"></div>
      <div class="btn-group" style="align-items:center">
        <button class="btn btn-sm btn-ghost" type="button" data-act="preview">Preview .inp</button>
        <span class="sp" style="flex:1"></span>
        <button class="btn btn-sm btn-ghost" type="button" data-act="cancel">Cancel</button>
        <button class="btn btn-sm btn-primary" type="button" data-act="save">${
          st.editing ? "Save changes" : "Add to queue"}</button>
      </div>`;

    wire(el, st, calc, close);
  }

  /** @param {HTMLElement} el @param {any} st @param {any} calc @param {() => void} close */
  function wire(el, st, calc, close) {
    /** @param {string} sel */
    const q = (sel) => /** @type {any} */ (el.querySelector(sel));
    /** @param {string} sel */
    const all = (sel) => [...el.querySelectorAll(sel)];

    all(".backendrow .seg button").forEach(b => b.addEventListener("click", async () => {
      const wantRaw = b.getAttribute("data-mode") === "raw";
      if (wantRaw === st.raw) return;
      // The two modes are not two views of one thing: the guided form GENERATES
      // input, raw text IS input. Going back would have to un-parse text nobody
      // promised was generated, so it is a discard, and it is asked for.
      const snap = readForm(el, st, calc);
      if (!wantRaw && st.rawText.trim()) {
        NB.confirm("Discard the .inp text",
          "The guided form builds its own input. The text you pasted is not converted back and will be lost.",
          "Discard", () => { st.raw = false; st.rawText = ""; st.xyz = snap.xyz; render(el, st, snap, close); });
        return;
      }
      // Going the other way IS a conversion: the form becomes the .inp it would
      // have generated (the classic setDftSub does the same), so the text starts
      // as what Preview showed rather than an empty editor that throws the form
      // away. No coordinates yet -> {{GEOMETRY}}, filled from the box at run
      // time. A failed conversion does not switch; the filled form stays.
      let text = snap.raw_text;
      if (wantRaw && !text.trim()) {
        const src = (snap.geometry_source === "direct" && !snap.xyz)
          ? Object.assign({}, snap, { geometry_source: "reference" }) : snap;
        const pv = await NB.call("build_inp_preview", JSON.stringify(src));
        if (!pv || !pv.ok) {
          NB.fail("Could not convert the form to .inp: " + ((pv && pv.error) || "no answer"));
          return;
        }
        text = pv.text || "";
      }
      st.raw = wantRaw;
      st.xyz = snap.xyz; st.rawText = text;
      render(el, st, Object.assign({}, snap, { raw_text: text }), close);
    }));

    q('[data-f="kind"]').addEventListener("change", e => {
      const snap = readForm(el, st, calc);
      const prev = KINDS[st.kind] || KINDS.general;
      st.kind = e.target.value;
      st.xyz = snap.xyz; st.rawText = snap.raw_text;
      // The calculation type IS the kind, so it always takes the new kind's
      // default (as the classic onKindChange does). Carried over, Optimization's
      // "TightOpt" became an Opt + Freq's whole job line: no Freq keyword, a run
      // that never computed a Hessian, FAILED and locked. SCF and extra keywords
      // follow the new kind only while they still hold the old kind's default —
      // a value the user chose survives the switch.
      const cfg = Object.assign({}, snap.config);
      delete cfg.calculation_type;
      if (cfg.scf_convergence === prev.scf) delete cfg.scf_convergence;
      if ((cfg.options || "") === (prev.options || "")) delete cfg.options;
      // the kind decides which blocks exist, so the form is rebuilt rather than
      // shown/hidden — a hidden block that still reads its value is how stale
      // settings ride into a calculation nobody configured
      render(el, st, Object.assign({}, snap, { kind: st.kind, config: cfg }), close);
    });

    all('input[name="gsrc"]').forEach(r => r.addEventListener("change", () => {
      const ref = q('input[name="gsrc"][value="reference"]').checked;
      q('[data-g="direct"]').hidden = ref;
      q('[data-g="reference"]').hidden = !ref;
    }));

    const act = (name, fn) => { const b = q(`[data-act="${name}"]`); if (b) b.addEventListener("click", fn); };

    // --- the IRC's starting Hessian -------------------------------------------
    // Loading a .hess IS choosing to read it, so the Initial Hessian follows the
    // load. The line beside the button says what will actually happen, because
    // a file loaded under calc_anfreq is ignored.
    const hessEl = q('[data-f="hessfile"]');
    if (hessEl) {
      const initField = [...el.querySelectorAll(".field")].find(f => {
        const l = f.querySelector("label");
        return l && l.textContent.trim() === "Initial Hessian";
      });
      const initSel = /** @type {any} */ (initField ? initField.querySelector("select") : null);
      const sayHess = () => {
        const path = hessEl.value.trim();
        const file = path.split(/[\\/]/).pop();
        const reading = initSel && initSel.value === "read";
        q('[data-x="hess"]').textContent = !path
          ? (reading ? "no .hess loaded" : "computed at the start of the run")
          : (reading ? file : `${file} — not used unless Initial Hessian is read`);
        q('[data-act="clearhess"]').hidden = !path;
      };
      if (initSel) initSel.addEventListener("change", sayHess);
      act("loadhess", async () => {
        const r = await NB.call("pick_hess_file");
        if (!r || r.cancelled) return;
        if (!r.ok || !r.path) { NB.fail(r && r.error ? r.error : "Could not pick that file."); return; }
        hessEl.value = r.path;
        if (initSel) initSel.value = "read";
        sayHess();
      });
      act("clearhess", () => { hessEl.value = ""; sayHess(); });
      sayHess();
    }

    act("loadxyz", async () => {
      const r = await NB.call("load_xyz_file");
      if (!r || r.cancelled) return;
      if (!r.ok) { NB.fail("Could not read that .xyz."); return; }
      const body = stripXyzHeader(r.text || "");
      q('[data-f="xyz"]').value = body;
      q('[data-x="status"]').textContent = body ? body.trim().split("\n").length + " atoms" : "";
      // a value set from code fires nothing; the stage listens for "input"
      q('[data-f="xyz"]').dispatchEvent(new Event("input"));
      recheck();
    });
    wireStage(el, st);
    // --- the structure, checked ---------------------------------------------
    /** Ask the backend what is wrong with the coordinates on screen. It cannot
     *  fail — an unreadable block is itself one of the findings — so there is
     *  no error branch, only a verdict. */
    async function recheck() {
      const box = q('[data-a="check"]');
      if (!box) return;
      const xyz = q('[data-f="xyz"]') ? q('[data-f="xyz"]').value.trim() : "";
      const react = q('[data-a="neb-react"]');
      if (react) react.textContent = xyz
        ? xyz.split("\n").filter(l => l.trim()).length + " atoms" : "no geometry yet";
      if (!xyz) { box.innerHTML = ""; return; }
      const charge = Math.round(Number(byLabelIn(el, "Charge") || 0));
      const mult = Math.round(Number(byLabelIn(el, "Mult.") || 1));
      const r = await NB.call("check_structure", xyz, charge, mult);
      if (!r || r.error) { box.innerHTML = ""; return; }
      const issues = (r.issues || []).map(i =>
        `<div class="finding ${i.level === "error" ? "err" : "warn"}">
           <span>${NB.esc(i.message)}</span></div>`).join("");
      const cm = r.electrons == null ? ""
        : `<span>charge ${charge} / mult ${mult} <b class="${
             (r.electrons - charge) % 2 === (mult - 1) % 2 ? "ok" : "err"}">${
             (r.electrons - charge) % 2 === (mult - 1) % 2 ? "consistent" : "impossible"}</b></span>`;
      box.innerHTML = issues + `<div class="factline" style="margin-top:10px">
        <span>formula <b>${NB.esc(r.formula || "—")}</b></span>
        ${r.electrons != null ? `<span>electrons <b>${r.electrons}</b></span>` : ""}
        <span>fragments <b>${r.n_fragments}</b></span>${cm}</div>`;
    }
    const xyzEl0 = q('[data-f="xyz"]');
    if (xyzEl0) xyzEl0.addEventListener("change", recheck);
    all(".field-row .field input").forEach(i => {
      const lab = i.closest(".field").querySelector("label");
      if (lab && (lab.textContent.trim() === "Charge" || lab.textContent.trim() === "Mult.")) {
        i.addEventListener("change", recheck);
      }
    });
    recheck();

    // --- NEB endpoints --------------------------------------------------------
    /** @param {string} cls @param {string} msg */
    const verdict = (cls, msg) => {
      const v = q('[data-a="neb-verdict"]');
      if (v) v.innerHTML = `<div class="finding ${cls}" style="margin-top:12px">
        <span>${NB.esc(msg)}</span></div>`;
    };
    const nebEl = q('[data-f="nebxyz"]');
    if (nebEl) {
      const prod = q('[data-a="neb-prod"]');
      const sayProd = () => {
        const n = nebEl.value.trim()
          ? nebEl.value.trim().split("\n").filter(l => l.trim()).length : 0;
        prod.textContent = n ? n + " atoms loaded" : "no product loaded";
      };
      sayProd();
      verdict("warn", "Load a product, then compare — both endpoints must carry the "
                    + "same atoms in the same order.");
      act("loadneb", async () => {
        const r = await NB.call("load_xyz_file");
        if (!r || r.cancelled) return;
        if (!r.ok) { NB.fail("Could not read that .xyz."); return; }
        nebEl.value = stripXyzHeader(r.text || "");
        sayProd();
        compare();
      });
      act("nebcompare", compare);
      async function compare() {
        const a = q('[data-f="xyz"]') ? q('[data-f="xyz"]').value.trim() : "";
        const b = nebEl.value.trim();
        if (!a || !b) { verdict("warn", "Both endpoints need coordinates before they can be compared."); return; }
        const r = await NB.call("compare_structures", a, b);
        if (!r || r.error) { verdict("err", (r && r.error) || "Could not compare the endpoints."); return; }
        if (r.ok) {
          verdict("warn", `Same ${r.n_reactant} atoms in the same order — ${
            r.formula_reactant} on both sides. The band is well posed.`);
          const v = q('[data-a="neb-verdict"] .finding');
          if (v) { v.classList.remove("warn"); v.classList.add("ok"); }
          return;
        }
        verdict("err", r.n_reactant !== r.n_product
          ? `${r.n_reactant} atoms in the reactant, ${r.n_product} in the product — `
            + "NEB needs the same atoms on both sides."
          : `First divergence at atom ${(r.mismatch_index || 0) + 1}: `
            + `${r.mismatches[0].reactant} in the reactant, ${r.mismatches[0].product} `
            + `in the product (${r.n_mismatches} in all). Rebuild the product by moving `
            + "the reactant's atoms rather than re-typing them.");
      }
    }

    // --- per-element basis ----------------------------------------------------
    const basisBox = q('[data-a="basis"]');
    /** the × on each row, rebound after every add */
    function wireBasis() {
      basisBox.querySelectorAll(".basis-row .rm").forEach(b =>
        b.addEventListener("click", () => b.closest(".basis-row").remove()));
    }
    if (basisBox) {
      wireBasis();
      act("addbasis", () => {
        basisBox.insertAdjacentHTML("beforeend", basisRow({}));
        wireBasis();
        const last = basisBox.querySelector(".basis-row:last-child input");
        if (last) /** @type {any} */ (last).focus();
      });
    }

    // --- raw-input snippets ---------------------------------------------------
    all("[data-snip]").forEach(b => b.addEventListener("click", () => {
      const ta = q('[data-f="rawtext"]');
      if (!ta) return;
      const sn = SNIPPETS[Number(b.getAttribute("data-snip"))];
      if (!sn) return;
      // at the cursor, not at the end: a block belongs where the user is typing
      const at = ta.selectionStart, to = ta.selectionEnd;
      const before = ta.value.slice(0, at);
      const lead = (!before || before.endsWith("\n")) ? "" : "\n";
      const text = lead + sn[1] + "\n";
      ta.value = before + text + ta.value.slice(to);
      ta.focus();
      ta.selectionStart = ta.selectionEnd = at + text.length;
    }));

    act("loadinp", async () => {
      const r = await NB.call("load_inp_file");
      if (!r || r.cancelled) return;
      if (!r.ok) { NB.fail("Could not read that .inp."); return; }
      q('[data-f="rawtext"]').value = r.text || "";
    });

    act("preview", async () => {
      const payload = readForm(el, st, calc);
      if (payload.is_raw) { NB.modalCode("Input preview", payload.raw_text); return; }
      const pv = await NB.call("build_inp_preview", JSON.stringify(payload));
      if (pv && pv.ok) NB.modalCode("Input preview", pv.text);
      else NB.modalRaw("Input preview",
        `<p>${NB.esc((pv && pv.error) || "Could not build a preview.")}</p>`);
    });

    act("cancel", () => close());

    act("save", async () => {
      const payload = readForm(el, st, calc);
      const err = validate(payload);
      if (err) { NB.fail(err); return; }
      const proceed = await confirmOverwrite(payload.name, st.editing);
      if (!proceed) return;
      const r = st.editing
        ? await NB.call("update_calc", st.editing, JSON.stringify(payload))
        : await NB.call("add_calc", JSON.stringify(payload));
      if (r && r.error) { NB.fail(r.error); return; }
      if (r && r.ok === false) { NB.fail(r.error || "The queue refused that calculation."); return; }
      NB.toast(st.editing ? `"${payload.name}" saved.`
                          : `"${payload.name}" added to the queue.`, "ok");
      // accepted DESPITE this - said, not refused (store.constraint_warning)
      if (r && r.warning) NB.toast(r.warning, "warn");
      close();
      await NB.poll();
    });
  }

  /* %geom Constraints, as input_generator.CONSTRAINT_ATOMS has them: what a
     chain of n atoms constrains is the coordinate n atoms define. */
  const CONS_NAME = { C: "Position", B: "Bond", A: "Angle", D: "Dihedral" };
  const CONS_BY_COUNT = ["", "C", "B", "A", "D"];

  /** The same coordinate, named in either direction — O1–H2 is H2–O1, and
   *  ORCA would be handed it twice. @param {any} a @param {any} b */
  function sameCons(a, b) {
    if (a.kind !== b.kind || a.atoms.length !== b.atoms.length) return false;
    const n = a.atoms.length;
    return a.atoms.every((v, k) => v === b.atoms[k])
        || a.atoms.every((v, k) => v === b.atoms[n - 1 - k]);
  }

  /** The structure stage beside the coordinates, and the constraints picked on
   *  it. The stage draws whatever the box holds as it is typed; for a kind that
   *  optimizes, a drag across atoms adds the constraint they name, a selected
   *  atom can be fixed, and a row in the list shows its atoms on the stage.
   *  @param {HTMLElement} el @param {any} st */
  function wireStage(el, st) {
    /** @param {string} sel */
    const q = (sel) => /** @type {any} */ (el.querySelector(sel));
    const ta = q('[data-f="xyz"]'), cv = q('[data-v="geomcv"]');
    if (!ta || !cv) return;
    const K = KINDS[st.kind] || KINDS.general;
    const picking = !!K.cons && !st.raw;
    const fix = q('[data-act="fixatom"]');
    const useRef = () => q('input[name="gsrc"][value="reference"]').checked;
    /** @type {any[]} */
    let atoms = [];
    /** the atom a click selected, for Fix @type {number|null} */
    let picked = null;

    /** the Ctrl+clicked atoms, in order, waiting for the button @type {number[]} */
    let sel = [];
    const stage = MOL.mount(cv, Object.assign({
      gizmoBottom: 26,
      onPick: (/** @type {any} */ p) => { picked = p && p.kind === "atom" ? p.i : null; sayFix(); },
    }, picking ? {
      onChain: (/** @type {number[]} */ idx) => add(idx),
      onSelect: (/** @type {number[]} */ idx) => { sel = idx; sayFix(); },
    } : {}));
    st.stage = stage;

    /** @param {number} i */
    const atomName = (i) => atoms[i] ? atoms[i].el + (i + 1) : String(i + 1);
    /** C3–O7, the report's own naming; bare numbers when there is nothing to
     *  read the elements from @param {any} c @param {boolean} ref */
    const consLabel = (c, ref) => c.atoms.map(i => ref ? String(i + 1) : atomName(i)).join("–");

    /** The stage's one button names what it would add: the atom a click
     *  picked (fix it), or the 2-4 atoms Ctrl+click selected (hold that bond,
     *  angle or dihedral). The selection outlives the Ctrl key, so it is this
     *  press that turns it into a constraint (or Enter on the stage). */
    function sayFix() {
      if (!fix) return;
      if (sel.length >= 2) {
        fix.disabled = false;
        fix.textContent = "Add " + CONS_NAME[CONS_BY_COUNT[sel.length]].toLowerCase()
          + " " + sel.map(atomName).join("–");
        return;
      }
      const one = sel.length === 1 ? sel[0] : picked;
      fix.disabled = one == null;
      fix.textContent = one == null ? "Fix atom" : "Fix " + atomName(one);
    }

    /** @param {number[]} idx 0-based */
    function add(idx) {
      const c = { kind: CONS_BY_COUNT[idx.length], atoms: idx.slice(), value: null };
      if (!c.kind) return;
      if (st.cons.some(x => sameCons(x, c))) {
        NB.toast(`${CONS_NAME[c.kind]} ${consLabel(c, useRef())} is already constrained.`);
      } else {
        st.cons.push(c);
        list();
      }
      if (!useRef()) stage.setHighlight(c.atoms);
    }

    function list() {
      const box = q('[data-a="cons"]');
      if (!box) return;
      const ref = useRef();
      const hint = q('[data-x="conshint"]');
      if (hint) hint.textContent = ref
        ? "Atoms are numbered from 1. They are checked against the referenced geometry when the run starts."
        : "Drag across 2, 3 or 4 atoms for a bond, angle or dihedral — or Ctrl+click them in order and press Add; select one atom to fix it. Leave a target empty to hold the current value.";
      box.innerHTML = st.cons.map((c, k) => {
        const lab = consLabel(c, ref);
        const miss = ref ? null : c.atoms.find(i => i >= atoms.length);
        const unit = c.kind === "B" ? "Å" : "°";
        const mv = ref || miss != null ? null : MOL.measureChain(atoms, c.atoms);
        const now = miss != null ? `no atom ${miss + 1}`
          : mv ? mv.value.toFixed(c.kind === "B" ? 3 : 1) + " " + unit
          : c.kind === "C" ? "fixed" : "";
        return `<div class="consrow${miss != null ? " bad" : ""}" data-k="${k}" tabindex="0">
          <span class="ck">${CONS_NAME[c.kind]}</span>
          <span class="ca">${NB.esc(lab)}</span>
          <span class="cv" title="measured on the coordinates above">${NB.esc(now)}</span>
          ${c.kind === "C" ? "<span></span><span></span>" : `<label class="ct">to <input class="mono" type="number"
            step="any" value="${c.value == null ? "" : NB.esc(String(c.value))}" placeholder="current"
            aria-label="Target for ${CONS_NAME[c.kind].toLowerCase()} ${NB.esc(lab)}, ${unit}"> ${unit}</label>
            <button class="btn btn-sm btn-ghost ap" type="button" data-apply="1"${
              ref || miss != null || c.value == null ? " disabled" : ""}
              title="Move the coordinates to this target: the smaller side of the bond turns as one piece"
              aria-label="Move the coordinates to the target for ${NB.esc(lab)}">Apply</button>`}
          <button class="rm" type="button" aria-label="Remove the ${
            CONS_NAME[c.kind].toLowerCase()} constraint on ${NB.esc(lab)}">×</button>
        </div>`;
      }).join("");
      box.querySelectorAll(".consrow").forEach(r => {
        const c = st.cons[Number(/** @type {HTMLElement} */ (r).dataset.k)];
        const show = () => { if (!useRef()) stage.setHighlight(c.atoms); };
        const hide = () => stage.setHighlight([]);
        r.addEventListener("mouseenter", show); r.addEventListener("focusin", show);
        r.addEventListener("mouseleave", hide); r.addEventListener("focusout", hide);
        const inp = /** @type {HTMLInputElement|null} */ (r.querySelector("input"));
        // the value is kept as it is typed, and the list is NOT redrawn for
        // it: a redraw would take the field out from under the cursor
        const ap = /** @type {HTMLButtonElement|null} */ (r.querySelector("[data-apply]"));
        if (inp) inp.addEventListener("input", () => {
          const v = parseFloat(inp.value);
          c.value = isFinite(v) ? v : null;
          if (ap) ap.disabled = useRef() || c.value == null
            || c.atoms.some(i => i >= atoms.length);
        });
        if (ap) ap.addEventListener("click", () => applyTarget(c));
        /** @type {HTMLElement} */ (r.querySelector(".rm")).addEventListener("click", () => {
          st.cons.splice(st.cons.indexOf(c), 1);
          stage.setHighlight([]);
          list();
        });
      });
    }

    /** Move the coordinates to a constraint's target. The geometry is the
     *  backend's (core/structure.py, P4) — one rigid motion of the smaller side
     *  of the hinge bond, so a hydroxyl turned to 120° takes its hydrogen with
     *  it. The text comes back with only the moved lines rewritten and goes
     *  into the box as an EDIT of it, so Ctrl+Z there puts the old coordinates
     *  back (DESIGN.md §17.4l). @param {any} c */
    async function applyTarget(c) {
      if (c.value == null || useRef()) return;
      const r = await NB.call("apply_constraint_target", ta.value,
        JSON.stringify({ kind: c.kind, atoms: c.atoms, value: c.value }));
      if (!r || !r.ok) { NB.fail((r && r.error) || "Could not move the coordinates."); return; }
      putText(r.xyz);
      stage.setHighlight(c.atoms);
      const moved = (r.moved || []).map(atomName);
      NB.toast(moved.length
        ? `Moved ${moved.length > 4 ? moved.length + " atoms" : moved.join(", ")}. `
          + "Ctrl+Z in the coordinates puts them back."
        : "Already at the target.", "ok");
      (r.warnings || []).forEach(w => NB.fail(w));
    }

    /** Replace the box's text as an EDIT of it: insertText rather than
     *  .value, so the box keeps its own undo history and Ctrl+Z there puts the
     *  old coordinates back. @param {string} text */
    function putText(text) {
      const top = ta.scrollTop;
      ta.focus({ preventScroll: true });
      ta.select();
      if (!document.execCommand("insertText", false, text)) ta.value = text;
      ta.scrollTop = top;
      clearTimeout(timer);
      showGeom();
    }

    // ---- push along a mode (DESIGN.md §17.4p) ------------------------------
    /* A frequency run's .hess holds every mode's direction. Picking one and a
       distance previews the pushed structure on this stage; Apply puts it in
       the box. The geometry is the backend's (structure.displace_along_mode,
       P4): the distance is how far the MOST-moved atom goes, which is a number
       a reader can picture, where a normalized vector's own scale is not. */
    const mp = q('[data-x="modepush"]');
    /** @type {{path: string, modes: any[]}|null} */
    let hess = null;
    let dir = 1;
    /** @type {any} */
    let ptimer = 0;
    const ampOf = () => {
      const v = parseFloat(q('[data-f="ampn"]').value);
      return isFinite(v) && v >= 0 ? Math.min(v, 2) : 0;
    };
    const modeOf = () => hess && hess.modes[Number(q('[data-f="mode"]').value)];
    const sayPush = (/** @type {string} */ t) => { q('[data-x="pushnote"]').textContent = t; };

    /** Show the pushed structure on the stage, not yet in the box. */
    async function preview() {
      const m = modeOf();
      if (!m) return;
      const amp = ampOf();
      const r = await NB.call("displace_mode", ta.value, hess.path, m.index, dir * amp);
      if (!r || !r.ok) {
        sayPush((r && r.error) || "Could not push along that mode.");
        stage.setAtoms(atoms);
        return;
      }
      stage.setAtoms(MOL.parseXyz(r.xyz));
      // an empty box previews the Hessian's own geometry, so the stage is not
      // empty whatever the box says
      const empty = q('[data-x="geomempty"]');
      if (empty) empty.hidden = true;
      const small = m.freq < 0 && m.freq > -50
        ? " A small imaginary mode is often numerical noise: a tighter optimization or a"
          + " finer grid may remove it without a push." : "";
      sayPush((amp ? `Preview: ${r.atom} moves ${amp.toFixed(2)} Å `
                    + `${dir > 0 ? "along" : "against"} mode ${m.n}. `
                    + "Apply puts it in the coordinates."
                   : "A push of 0 is the coordinates as they are.") + small);
    }
    const later = () => { clearTimeout(ptimer); ptimer = setTimeout(preview, 60); };

    const lh = q('[data-act="loadhess"]');
    if (lh && mp) lh.addEventListener("click", async () => {
      const pick = await NB.call("pick_hess_file");
      if (!pick || !pick.ok) { if (pick && pick.error) NB.fail(pick.error); return; }
      const r = await NB.call("read_hess_modes", pick.path);
      if (!r || !r.ok) { NB.fail((r && r.error) || "Could not read that .hess."); return; }
      if (!r.modes || !r.modes.length) { NB.fail(`${r.name} has no vibrations to push along.`); return; }
      hess = { path: r.path, modes: r.modes };
      q('[data-x="hessname"]').textContent = `${r.name} · ${r.n_atoms} atoms`;
      // every mode, imaginary ones marked; the first imaginary is the one a
      // push is usually for, else the softest
      const first = Math.max(0, r.modes.findIndex(x => x.freq < 0));
      q('[data-f="mode"]').innerHTML = r.modes.map((x, i) =>
        `<option value="${i}"${i === first ? " selected" : ""}>Mode ${x.n} · ${
          x.freq.toFixed(2)} cm⁻¹${x.freq < 0 ? " · imaginary" : ""}</option>`).join("");
      mp.hidden = false;
      later();
    });
    if (mp) {
      q('[data-f="mode"]').addEventListener("change", later);
      mp.querySelectorAll("[data-dir]").forEach(b => b.addEventListener("click", () => {
        dir = Number(/** @type {HTMLElement} */ (b).dataset.dir);
        mp.querySelectorAll("[data-dir]").forEach(o =>
          o.setAttribute("aria-pressed", String(o === b)));
        later();
      }));
      q('[data-f="amp"]').addEventListener("input", () => {
        q('[data-f="ampn"]').value = q('[data-f="amp"]').value;
        later();
      });
      q('[data-f="ampn"]').addEventListener("input", () => {
        q('[data-f="amp"]').value = String(Math.min(1, ampOf()));
        later();
      });
      q('[data-act="pushapply"]').addEventListener("click", async () => {
        const m = modeOf();
        if (!m) return;
        const amp = ampOf();
        if (!amp) { NB.toast("A push of 0 changes nothing."); return; }
        const r = await NB.call("displace_mode", ta.value, hess.path, m.index, dir * amp);
        if (!r || !r.ok) { NB.fail((r && r.error) || "Could not push along that mode."); return; }
        putText(r.xyz);
        // the box now IS the pushed structure, so the next preview starts from
        // there: the distance goes back to 0 rather than pushing twice
        q('[data-f="amp"]').value = "0";
        q('[data-f="ampn"]').value = "0";
        sayPush(`Applied: ${r.atom} moved ${amp.toFixed(2)} Å `
                + `${dir > 0 ? "along" : "against"} mode ${m.n}. `
                + "Ctrl+Z in the coordinates puts it back; a further push starts from here.");
        NB.toast(`Pushed along mode ${m.n}.`, "ok");
      });
    }

    /** What is in the box, drawn — and the list re-measured against it. */
    function showGeom() {
      atoms = MOL.parseXyz(ta.value);
      stage.setAtoms(atoms);
      picked = null; sayFix();
      const empty = q('[data-x="geomempty"]');
      if (empty) empty.hidden = atoms.length > 0;
      const status = q('[data-x="status"]');
      if (status) status.textContent = atoms.length ? atoms.length + " atoms" : "";
      list();
    }
    /** @type {any} */
    let timer = 0;
    ta.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(showGeom, 180); });

    if (fix) fix.addEventListener("click", () => {
      if (sel.length) { add(stage.takePicks()); return; }
      if (picked == null) return;
      add([picked]);
      stage.clearPick();
      picked = null; sayFix();
    });

    const typed = q('[data-f="consatoms"]');
    const addTyped = () => {
      const nums = String(typed.value || "").split(/[\s,;–-]+/).filter(Boolean).map(Number);
      const n = useRef() ? Infinity : atoms.length;
      if (!nums.length || nums.length > 4) { NB.fail("Name 1 to 4 atoms: 1 fixes a position, 2 a bond, 3 an angle, 4 a dihedral."); return; }
      if (nums.some(x => !Number.isInteger(x) || x < 1)) { NB.fail("Atoms are whole numbers, counted from 1."); return; }
      if (new Set(nums).size !== nums.length) { NB.fail("Name each atom once."); return; }
      const over = nums.find(x => x > n);
      if (over != null) { NB.fail(`There is no atom ${over} — the structure has ${n}.`); return; }
      add(nums.map(x => x - 1));
      typed.value = "";
    };
    const addBtn = q('[data-act="consadd"]');
    if (addBtn) addBtn.addEventListener("click", addTyped);
    if (typed) typed.addEventListener("keydown", (/** @type {KeyboardEvent} */ e) => {
      if (e.key === "Enter") { e.preventDefault(); addTyped(); }
    });

    // the source decides what a row can say: a referenced geometry is not
    // here, so its rows are numbers without elements or measurements
    el.querySelectorAll('input[name="gsrc"]').forEach(r => r.addEventListener("change", () => {
      stage.setHighlight([]);
      list();
      if (!useRef()) stage.repaint();
    }));

    showGeom();
  }

  /** The control under a labelled field, by the label's text. Shared by the
   *  form reader and the live structure check, which need the same two numbers.
   *  @param {HTMLElement} root @param {string} label @returns {string|null} */
  function byLabelIn(root, label) {
    const f = [...root.querySelectorAll(".field-row .field")].find(x => {
      const l = x.querySelector("label");
      return l && l.textContent.trim() === label;
    });
    if (!f) return null;
    const c = /** @type {any} */ (f.querySelector("input, select, textarea"));
    return c ? c.value : null;
  }

  /** An .xyz file starts with a count and a comment line; the generator wants
   *  only the coordinate block. @param {string} t */
  function stripXyzHeader(t) {
    const lines = String(t || "").replace(/\r/g, "").split("\n");
    if (lines.length > 2 && /^\s*\d+\s*$/.test(lines[0])) return lines.slice(2).join("\n").trim();
    return lines.join("\n").trim();
  }

  /** Read the form into the payload add_calc/update_calc take.
   *  @param {HTMLElement} el @param {any} st @param {any} calc */
  function readForm(el, st, calc) {
    const q = (sel) => /** @type {any} */ (el.querySelector(sel));
    const fields = [...el.querySelectorAll(".field-row .field")];
    /** @param {string} label */
    const byLabel = (label) => {
      const f = fields.find(x => {
        const l = x.querySelector("label");
        return l && l.textContent.trim() === label;
      });
      if (!f) return null;
      return /** @type {any} */ (f.querySelector("input, select, textarea"));
    };
    const val = (label, dflt) => { const e = byLabel(label); return e ? e.value : dflt; };
    const num = (label, dflt) => { const e = byLabel(label); const n = e ? parseFloat(e.value) : NaN; return isFinite(n) ? n : dflt; };
    const chk = (name) => { const e = q(`[data-f="${name}"]`); return !!(e && e.checked); };

    const kind = q('[data-f="kind"]').value;
    const K = KINDS[kind] || KINDS.general;
    const refSel = q('[data-g="reference"] select');
    const useRef = q('input[name="gsrc"][value="reference"]').checked;
    const xyzEl = q('[data-f="xyz"]');
    const rawEl = q('[data-f="rawtext"]');
    const d = NB.settings || {};

    /** @type {any} */
    const config = {
      kind: kind,
      functional: val("Functional", "wB97X-D4"),
      basis_set: val("Basis set", "def2-TZVP"),
      ri_approximation: val("RI", "RIJCOSX"),
      scf_convergence: val("SCF convergence", K.scf),
      calculation_type: val("Calculation type", K.calc),
      options: val("Extra keywords", K.options || ""),
      maxcore_mb: Math.round(num("maxcore (MB / core)", d.default_maxcore_mb || DEF.maxcore_mb)),
      nprocs: Math.round(num("nprocs", d.default_nprocs || DEF.nprocs)),
      // an empty field falls back to StepConfig's own default (DEF): 0 here
      // was clamped to MaxIter 1 by from_dict, and every new optimization
      // stopped after one cycle
      max_iter: K.maxiter ? Math.round(num("Max iterations", DEF.max_iter)) : 0,
      solvation: { model: val("Model", ""), solvent: val("Solvent", "") },
      // read off the rows on screen, not carried over from the stored calc:
      // a row the user deleted has to actually go
      basis_assignments: [...el.querySelectorAll(".basis-row")].map(r => ({
        element: /** @type {any} */ (r.querySelector('[data-b="element"]')).value.trim(),
        basis: /** @type {any} */ (r.querySelector('[data-b="basis"]')).value.trim(),
        ecp: /** @type {any} */ (r.querySelector('[data-b="ecp"]')).value.trim(),
      })).filter(a => a.element),
    };
    // only for a kind that optimizes: a constraint on a frequency job is a
    // setting nobody can see, riding along into a calculation it cannot affect
    if (K.cons) {
      config.geom_constraints = (st.cons || []).map(c =>
        ({ kind: c.kind, atoms: c.atoms.slice(), value: c.value }));
    }
    if (K.freq) {
      config.freq_temp_k = num("Temperature (K)", DEF.freq_temp_k);
      config.freq_pressure_atm = num("Pressure (atm)", DEF.freq_pressure_atm);
    }
    if (K.tddft) {
      config.tddft_nroots = Math.round(num("Roots", DEF.tddft_nroots));
      config.tddft_maxdim = Math.round(num("MaxDim", DEF.tddft_maxdim));
      config.tddft_tda = chk("tda");
      config.tddft_triplets = chk("triplets");
    }
    if (K.nmr) config.nmr_jcoupling = chk("jcoupling");
    if (K.irc) {
      config.irc_maxiter = Math.round(num("Max points", DEF.irc_maxiter));
      config.irc_direction = val("Direction", DEF.irc_direction);
      config.irc_init_hess = val("Initial Hessian", DEF.irc_init_hess);
      const hf = q('[data-f="hessfile"]');
      config.irc_hess_file = hf ? hf.value.trim() : "";
    }
    if (K.neb) {
      const nb = q('[data-f="nebxyz"]');
      config.neb_product_xyz = nb ? nb.value.trim() : "";
      config.neb_nimages = Math.round(num("Images", DEF.neb_nimages));
      config.neb_preopt_ends = chk("preopt");
    }

    return {
      name: String(val("Name", "")).trim(),
      kind: kind,
      charge: Math.round(num("Charge", 0)),
      multiplicity: Math.round(num("Mult.", 1)),
      geometry_source: useRef ? "reference" : "direct",
      xyz: useRef ? "" : (xyzEl ? xyzEl.value.trim() : ""),
      ref_name: useRef && refSel ? refSel.value : "",
      is_raw: st.raw,
      raw_text: st.raw && rawEl ? rawEl.value : "",
      config: config,
      state: "pending",
      message: "",
    };
  }

  /** The checks that can be made here — everything about ORCA itself is the
   *  backend's to refuse, and it does (P4). @param {any} p @returns {string} */
  function validate(p) {
    if (!p.name) return "Give the calculation a name — it becomes its folder.";
    if (/[\\/:*?"<>|]/.test(p.name)) return "A calculation name cannot contain \\ / : * ? \" < > |";
    if (p.is_raw && !p.raw_text.trim()) return "Paste or load a complete .inp first.";
    if (!p.is_raw && p.geometry_source === "direct" && !p.xyz) return "Load or paste a geometry first.";
    if (p.geometry_source === "reference" && !p.ref_name) return "Pick the calculation to take the geometry from.";
    if (p.multiplicity < 1) return "Multiplicity is at least 1.";
    // the backend refuses this too (calc_from_dict), but the row that is wrong
    // is on screen here, marked
    const cons = (p.config && p.config.geom_constraints) || [];
    if (!p.is_raw && p.geometry_source === "direct" && cons.length) {
      const n = MOL.parseXyz(p.xyz).length;
      const bad = cons.find(c => Math.max(...c.atoms) >= n);
      if (bad) {
        return `The ${CONS_NAME[bad.kind].toLowerCase()} constraint names atom ${
          Math.max(...bad.atoms) + 1}, but the structure has ${n} atoms.`;
      }
    }
    if (!p.is_raw && p.kind === "irc" && p.config.irc_init_hess === "read"
        && !p.config.irc_hess_file) {
      return "Initial Hessian is read — load the .hess it starts from.";
    }
    return "";
  }

  /** Adding to a RUNNING queue starts the calculation immediately, with no Run
   *  click to screen it — so an existing result under that name is confirmed
   *  here instead. @param {string} name @param {string} editing */
  async function confirmOverwrite(name, editing) {
    if (!NB.running || name === editing) return true;
    const r = await NB.call("has_existing_output", name);
    if (!r || !r.ok || !r.exists) return true;
    return await new Promise(res => {
      NB.modalRaw("Existing output",
        `<p>The workspace already holds output for <b>${NB.esc(name)}</b>. The queue is
         running, so this calculation starts at once and overwrites it.</p>`,
        [{ label: "Cancel", cls: "btn-ghost", act: () => res(false) },
         { label: "Overwrite", cls: "btn-danger", act: () => res(true) }]);
    });
  }

  NB.build = { open: open, target: target, cellFor: cellFor, KINDS: KINDS };
})();
