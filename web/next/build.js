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
    opt:          { label: "Optimization",     group: "calculation_types_geometry",  calc: "TightOpt",      scf: "TightSCF",     maxiter: true },
    opt_freq:     { label: "Opt + Freq",       group: null,                          calc: "TightOpt Freq", scf: "VeryTightSCF", maxiter: true,  freq: true },
    ts_opt:       { label: "TS optimization",  group: "calculation_types_geometry",  calc: "OptTS",         scf: "TightSCF",     maxiter: true },
    ts_opt_freq:  { label: "TS Opt + Freq",    group: null,                          calc: "OptTS Freq",    scf: "VeryTightSCF", maxiter: true,  freq: true },
    freq:         { label: "Frequencies",      group: "calculation_types_frequency", calc: "Freq",          scf: "VeryTightSCF", freq: true },
    ts_freq:      { label: "TS frequencies",   group: "calculation_types_frequency", calc: "Freq",          scf: "VeryTightSCF", freq: true },
    neb_ts:       { label: "NEB-TS (find TS)", group: null,                          calc: "NEB-TS",        scf: "TightSCF",     neb: true, options: "FREQ" },
    irc:          { label: "IRC (verify TS)",  group: null,                          calc: "IRC",           scf: "TightSCF",     irc: true },
    tddft:        { label: "TD-DFT",           group: null,                          calc: "",              scf: "TightSCF",     tddft: true },
    nmr:          { label: "NMR",              group: null,                          calc: "NMR",           scf: "TightSCF",     nmr: true },
    sp:           { label: "Single point",     group: "calculation_types_energy",    calc: "SP",            scf: "TightSCF" },
    general:      { label: "General (any)",    group: null,                          calc: "",              scf: "TightSCF",     allTypes: true },
  };
  const KIND_ORDER = ["opt", "opt_freq", "ts_opt", "ts_opt_freq", "freq", "ts_freq",
                      "neb_ts", "irc", "tddft", "nmr", "sp", "general"];

  /** @type {Record<string, any>} option groups from data/*.json, loaded once */
  const CHOICES = {};
  let _loaded = false;

  async function loadChoices() {
    if (_loaded) return;
    const names = ["functionals", "basis_sets", "calculation_types",
                   "scf_convergences", "ri_approximations", "solvents"];
    await Promise.all(names.map(async n => {
      const g = await NB.call("load_choices", n);
      CHOICES[n] = (g && !g.error) ? g : {};
    }));
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

  /** Which calculation the builder is open on: null = closed, "" = a new one,
   *  otherwise the name being edited. The stream reads it. @type {string|null} */
  let _target = null;

  /** Open the builder on a calculation, or on a new one with "". Closing is
   *  open(null). @param {string|null} target */
  function open(target) {
    _target = _target === target ? null : target;
    NB.stream.render();
    if (_target !== null) {
      const cell = document.querySelector(".cell.editing, .cell.stub.editing");
      if (cell) cell.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }
  function target() { return _target; }

  /** The builder as a CELL — the design edits a calculation where it is read.
   *  @param {any} calc a CalcSummary, or null for a new calculation
   *  @returns {HTMLElement} */
  function cellFor(calc) {
    const el = NB.el("article", "cell editing" + (calc ? "" : " stub"));
    el.dataset.name = calc ? calc.name : "";
    el.innerHTML = `
      <div class="editbar">
        <b>${calc ? "Editing " + NB.esc(calc.name) : "New calculation"}</b>
        <span>${calc ? "changes apply to the queued calculation"
                     : "it joins the queue when you add it"}</span>
        <span class="sp"></span>
        <button class="btn btn-xs btn-ghost" type="button" data-act="cancel">Close</button>
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
    const st = {
      editing: summary ? summary.name : "",
      raw: calc ? !!calc.is_raw : false,
      kind: calc ? calc.kind : "opt",
      xyz: calc ? (calc.xyz || "") : "",
      rawText: calc ? (calc.raw_text || "") : "",
    };
    if (!KINDS[st.kind]) st.kind = "general";
    render(body, st, calc, close);
  }

  /** @param {HTMLElement} el @param {any} st @param {any} calc @param {() => void} close */
  function render(el, st, calc, close) {
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

      <div class="frow">
        ${text("Name", calc ? calc.name : "", "")}
        <div class="field mid"><label>Type</label><select data-f="kind">${
          KIND_ORDER.map(k => `<option value="${k}"${k === st.kind ? " selected" : ""}>${KINDS[k].label}</option>`).join("")
        }</select></div>
        ${text("Charge", calc ? calc.charge : 0, "narrow", "number")}
        ${text("Mult.", calc ? calc.multiplicity : 1, "narrow", "number")}
      </div>

      <div class="esec">
        <div class="st">Geometry</div>
        <div class="frow" style="gap:18px">
          <label class="check"><input type="radio" name="gsrc" value="direct" ${src !== "reference" ? "checked" : ""}> Coordinates</label>
          <label class="check"><input type="radio" name="gsrc" value="reference" ${src === "reference" ? "checked" : ""}> From another calculation</label>
        </div>
        <div data-g="direct" ${src === "reference" ? 'hidden' : ""}>
          <div class="frow" style="margin-bottom:7px">
            <button class="btn btn-sm" type="button" data-act="loadxyz">Load .xyz…</button>
            <span class="hint" data-x="status">${st.xyz ? st.xyz.trim().split("\n").length + " atoms" : ""}</span>
          </div>
          <div class="field"><textarea rows="7" data-f="xyz" spellcheck="false"
            placeholder="C   0.000   0.000   0.000">${NB.esc(st.xyz)}</textarea></div>
        </div>
        <div data-g="reference" ${src === "reference" ? "" : 'hidden'}>
          ${refs.length
            ? select("Take the optimized geometry from", refs, calc ? calc.ref_name : refs[0])
            : `<div class="hint">No other calculation to take a geometry from yet.</div>`}
        </div>
      </div>

      <div data-form ${st.raw ? "hidden" : ""}>
        <div class="esec">
          <div class="st">Method</div>
          <div class="frow">
            ${combo("Functional", items(CHOICES.functionals), cfg.functional || "wB97X-D4")}
            ${combo("Basis set", items(CHOICES.basis_sets), cfg.basis_set || "def2-TZVP")}
          </div>
          <div class="frow">
            ${select("RI", items(CHOICES.ri_approximations), cfg.ri_approximation || "RIJCOSX", "mid")}
            ${select("SCF convergence", items(CHOICES.scf_convergences), cfg.scf_convergence || K.scf, "mid")}
            ${calcTypes.length
              ? select("Calculation type", calcTypes, cfg.calculation_type || K.calc)
              : text("Calculation type", cfg.calculation_type != null ? cfg.calculation_type : K.calc)}
          </div>
          <div class="frow">
            ${text("Extra keywords", cfg.options != null ? cfg.options : (K.options || ""))}
          </div>
        </div>

        <div class="esec">
          <div class="st">Solvation</div>
          <div class="frow">
            ${select("Model", ["", "CPCM", "SMD"], (cfg.solvation && cfg.solvation.model) || "", "mid")}
            ${combo("Solvent", items(CHOICES.solvents), (cfg.solvation && cfg.solvation.solvent) || "")}
          </div>
        </div>

        <div class="esec">
          <div class="st">Resources</div>
          <div class="frow">
            ${text("nprocs", cfg.nprocs || d.default_nprocs || 6, "narrow", "number")}
            ${text("maxcore (MB / core)", cfg.maxcore_mb || d.default_maxcore_mb || 2400, "mid", "number")}
            ${K.maxiter ? text("Max iterations", cfg.max_iter || 0, "mid", "number") : ""}
          </div>
        </div>

        ${K.freq ? `<div class="esec"><div class="st">Thermochemistry</div><div class="frow">
          ${text("Temperature (K)", cfg.freq_temp_k || 298.15, "mid", "number")}
          ${text("Pressure (atm)", cfg.freq_pressure_atm || 1.0, "mid", "number")}
        </div></div>` : ""}

        ${K.tddft ? `<div class="esec"><div class="st">TD-DFT</div><div class="frow">
          ${text("Roots", cfg.tddft_nroots || 10, "narrow", "number")}
          ${text("MaxDim", cfg.tddft_maxdim || 0, "narrow", "number")}
          <label class="check"><input type="checkbox" data-f="tda" ${cfg.tddft_tda !== false ? "checked" : ""}> TDA</label>
          <label class="check"><input type="checkbox" data-f="triplets" ${cfg.tddft_triplets ? "checked" : ""}> Triplets</label>
        </div></div>` : ""}

        ${K.nmr ? `<div class="esec"><div class="st">NMR</div>
          <label class="check"><input type="checkbox" data-f="jcoupling" ${cfg.nmr_jcoupling ? "checked" : ""}> Spin-spin coupling (J)</label>
        </div>` : ""}

        ${K.irc ? `<div class="esec"><div class="st">IRC</div><div class="frow">
          ${text("Max points", cfg.irc_maxiter || 40, "narrow", "number")}
          ${select("Direction", ["both", "forward", "backward"], cfg.irc_direction || "both", "mid")}
          ${select("Initial Hessian", ["calc_anfreq", "calc_numfreq", "read"], cfg.irc_init_hess || "calc_anfreq", "mid")}
          ${text(".hess file", cfg.irc_hess_file || "")}
        </div></div>` : ""}

        ${K.neb ? `<div class="esec"><div class="st">NEB</div><div class="frow">
          ${text("Product .xyz", cfg.neb_product_xyz || "")}
          ${text("Images", cfg.neb_nimages || 8, "narrow", "number")}
          <label class="check"><input type="checkbox" data-f="preopt" ${cfg.neb_preopt_ends ? "checked" : ""}> Pre-optimize ends</label>
        </div></div>` : ""}
      </div>

      <div data-raw ${st.raw ? "" : "hidden"}>
        <div class="frow" style="margin-bottom:7px">
          <button class="btn btn-sm" type="button" data-act="loadinp">Load .inp…</button>
          <span class="hint">The text below is what ORCA runs, verbatim.</span>
        </div>
        <div class="field"><textarea class="raw-editor" data-f="rawtext" spellcheck="false"
          placeholder="! B3LYP def2-SVP Opt&#10;* xyz 0 1&#10;…&#10;*">${NB.esc(st.rawText)}</textarea></div>
      </div>

      <div class="snips" style="margin:16px 0 0">
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

    all(".backendrow .seg button").forEach(b => b.addEventListener("click", () => {
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
      st.raw = wantRaw;
      st.xyz = snap.xyz; st.rawText = snap.raw_text;
      render(el, st, snap, close);
    }));

    q('[data-f="kind"]').addEventListener("change", e => {
      const snap = readForm(el, st, calc);
      st.kind = e.target.value;
      st.xyz = snap.xyz; st.rawText = snap.raw_text;
      // the kind decides which blocks exist, so the form is rebuilt rather than
      // shown/hidden — a hidden block that still reads its value is how stale
      // settings ride into a calculation nobody configured
      render(el, st, Object.assign({}, snap, { kind: st.kind, config: snap.config }), close);
    });

    all('input[name="gsrc"]').forEach(r => r.addEventListener("change", () => {
      const ref = q('input[name="gsrc"][value="reference"]').checked;
      q('[data-g="direct"]').hidden = ref;
      q('[data-g="reference"]').hidden = !ref;
    }));

    const act = (name, fn) => { const b = q(`[data-act="${name}"]`); if (b) b.addEventListener("click", fn); };

    act("loadxyz", async () => {
      const r = await NB.call("load_xyz_file");
      if (!r || r.cancelled) return;
      if (!r.ok) { NB.fail("Could not read that .xyz."); return; }
      const body = stripXyzHeader(r.text || "");
      q('[data-f="xyz"]').value = body;
      q('[data-x="status"]').textContent = body ? body.trim().split("\n").length + " atoms" : "";
    });
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
      close();
      await NB.poll();
    });
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
    const fields = [...el.querySelectorAll(".frow .field")];
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
      maxcore_mb: Math.round(num("maxcore (MB / core)", d.default_maxcore_mb || 2400)),
      nprocs: Math.round(num("nprocs", d.default_nprocs || 6)),
      max_iter: K.maxiter ? Math.round(num("Max iterations", 0)) : 0,
      solvation: { model: val("Model", ""), solvent: val("Solvent", "") },
      basis_assignments: (calc && calc.config && calc.config.basis_assignments) || [],
    };
    if (K.freq) {
      config.freq_temp_k = num("Temperature (K)", 298.15);
      config.freq_pressure_atm = num("Pressure (atm)", 1.0);
    }
    if (K.tddft) {
      config.tddft_nroots = Math.round(num("Roots", 10));
      config.tddft_maxdim = Math.round(num("MaxDim", 0));
      config.tddft_tda = chk("tda");
      config.tddft_triplets = chk("triplets");
    }
    if (K.nmr) config.nmr_jcoupling = chk("jcoupling");
    if (K.irc) {
      config.irc_maxiter = Math.round(num("Max points", 40));
      config.irc_direction = val("Direction", "both");
      config.irc_init_hess = val("Initial Hessian", "calc_anfreq");
      config.irc_hess_file = val(".hess file", "");
    }
    if (K.neb) {
      config.neb_product_xyz = val("Product .xyz", "");
      config.neb_nimages = Math.round(num("Images", 8));
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
