// @ts-check
/* ============================================================
   progress_panels.js — the step-panel progress trackers/renderers
   that are NOT the SCF/geometry convergence graph: numerical and
   analytical FREQUENCIES, TD-DFT, and the CREST conformer search.

   Split out of scf_graph.js, which kept growing past the "SCF
   convergence graph" its name promises. The two halves share
   almost nothing — one lone formatting helper (_fmtDuration),
   taken from the SCFGraph namespace below — so the seam is real,
   not just a line-count cut.

   Loading: this file EXTENDS the same SCFGraph namespace
   scf_graph.js creates, so it must load AFTER it (index.html) or
   be required after it (node). Callers keep using SCFGraph.*
   exactly as before the split.
   ============================================================ */
(function (/** @type {any} */ global) {
  "use strict";

  // The namespace scf_graph.js already published: in node it is that module's
  // exports (require caches it, so this is the same object app.js/the tests
  // hold); in the browser it is the global it assigned.
  const SCFGraph = (typeof module !== "undefined" && module.exports)
    ? require("./scf_graph.js")
    : global.SCFGraph;

  // The one helper that crosses the seam: "41s" / "7m" / "1h 20m".
  const _fmtDuration = SCFGraph._fmtDuration;

  // ---------- numerical-frequency progress (accurate ETA) ----------
  // Numerical frequencies (e.g. M06-2X + CPCM) run a FIXED number of displacements
  // (3N x 2 for central differences), each an SCF+gradient. Unlike a geometry opt,
  // the TOTAL is printed up front and the counter is exact, so the ETA is reliable:
  // its only error is per-displacement time variance (~a few %), not the ~65%
  // cycle-count uncertainty of an opt.
  // Stage-start markers are case-SENSITIVE: ORCA prints them as UPPERCASE
  // section banners, and every output (opt-only included) also carries
  // mixed-case look-alikes that must not trigger the freq banner — the header
  // credits ("pre 5.0 version of the SCF Hessian") and the property-block echo
  // ("Properties with geometric perturbations:", "SCF Hessian ... NO").
  const NFREQ_START_RE = /ORCA NUMERICAL FREQUENCIES/;
  const NDISP_RE = /Number of displacements\s+\.*\s*(\d+)/i;
  const DISP_RE = /displacement\s+(\d+)\s*\/\s*(\d+)/i;   // numerical: "...for displacement K / N"
  const VFREQ_RE = /VIBRATIONAL FREQUENCIES/;
  // analytical Hessian via coupled-perturbed SCF (CP-SCF). Not a black box: ORCA
  // prints an UPPERCASE banner for each stage of the pipeline, always in the
  // same order (verified on 4 real ORCA 6.1.1 freq outputs, 12-52 atoms,
  // CPCM/SMD): derivative integrals -> CP-SCF response (with a "K / N done"
  // perturbation counter) -> Hessian assembly -> frequencies -> normal modes
  // -> IR spectrum -> thermochemistry. The UI shows them as a 7-dot phase chain.
  // `boot: true` marks banners unique to a Hessian computation, which may
  // ACTIVATE the chain. The others only ADVANCE an already-active chain —
  // they also occur in other job types (GIAO NMR and polarizability runs
  // print "ORCA SCF RESPONSE CALCULATION" for their own CP-SCF solves, and
  // numerical-freq runs print the post-Hessian banners), so letting them
  // bootstrap showed the analytical-freq panel during plain NMR runs.
  const A_STAGES = [
    { key: "integrals", re: /GEOMETRIC PERTURBATIONS/, boot: true,                label: "Derivative integrals" },
    { key: "cpscf",     re: /ORCA SCF RESPONSE CALCULATION|SHARK CP-?SCF DRIVER/, label: "CP-SCF response" },
    { key: "hessian",   re: /\bSCF HESSIAN\b/, boot: true,                        label: "SCF Hessian" },
    { key: "freq",      re: VFREQ_RE,                                             label: "Frequencies" },
    { key: "modes",     re: /\bNORMAL MODES\b/,                                   label: "Normal modes" },
    { key: "ir",        re: /\bIR SPECTRUM\b/,                                    label: "IR spectrum" },
    { key: "thermo",    re: /THERMOCHEMISTRY AT\s+([\d.]+)/,                      label: "Thermochemistry" },
  ];
  const AFREQ_RE = /ANALYTICAL FREQUENC|Analytic(?:al)? Hessian/;  // other analytical markers (no stage)
  const ANUCLEI_RE = /GEOMETRIC PERTURBATIONS\s*\((\d+)\s*nuclei\)/; // banner carries the atom count
  // Inside the derivative-integrals stage ORCA works through the nuclei in
  // batches, announcing each one ("BATCH 135: Atoms  135 -  135 (  3
  // perturbations)"). On a large molecule that single stage runs for hours, so
  // the batch line is the only progress signal there is — the atom range is
  // what turns "144 nuclei" into "135/144 nuclei". Atoms per batch are summed
  // from the ranges rather than read off the index, because the index is
  // 0-based (verified on a real ORCA 6.1.1 run) and a batch may cover several
  // atoms — a sum needs neither assumption.
  const ABATCH_RE = /\bBATCH\s+(\d+)\s*:\s*Atoms\s+(\d+)\s*-\s*(\d+)/;
  const NPERT_RE = /Number of perturbations\s+\.*\s*(\d+)/i;               // CP-SCF total (= 3N)
  const CPSCF_DONE_RE = /(\d+)\s*\/\s*(\d+)\s+done/i;                      // "K/N done" per CP-SCF iter
  const ATERM_RE = /ORCA TERMINATED NORMALLY/;                             // job end -> chain complete
  // A new optimization cycle or IRC walk starting means the Hessian/TD-DFT
  // chain we were tracking belonged to a PRE-step stage — ts_opt's
  // "Calc_Hess true" and IRC's InitHess compute a full analytical Hessian
  // INSIDE cycle 1 / before the walk (verified on real BP86 OptTS + IRC
  // runs) — so the panel must clear instead of sitting stale through the
  // remaining cycles. Banners are uppercase; matched case-sensitively.
  const PHASE_RESET_RE = /GEOMETRY OPTIMIZATION CYCLE|FORWARD IRC|BACKWARD IRC/;

  // Job meta echoed at the top of the .out, shown on the stepper's meta line
  // (name · method · cores · elapsed): ORCA echoes the input file ("|  1> ! ..."
  // carries the method, "%pal nprocs N" the cores); CREST echoes its command
  // line ("$ .../crest input.xyz --gfn2 ... -T 4 ..."). First match wins.
  const ORCA_BANG_RE = /^\|\s*\d+>\s*!\s*(\S+(?:\s+\S+)?)/;      // first two "!"-line tokens (method + basis)
  const ORCA_NPROCS_RE = /^\|\s*\d+>\s*%pal\s+nprocs\s+(\d+)/i;
  function _grabOrcaMeta(t, line) {
    if (!t.method) { const m = line.match(ORCA_BANG_RE); if (m) t.method = m[1]; }
    if (!t.cores) { const p = line.match(ORCA_NPROCS_RE); if (p) t.cores = parseInt(p[1], 10); }
  }

  function FreqTracker() {
    this.mode = "";        // "" | "numerical" | "analytical"
    this.total = 0;        // numerical: total displacements (3N*2)
    this.cur = 0;          // numerical: latest displacement index seen
    this.active = false;   // a frequency stage is running
    this.dispDone = false; // displacements / Hessian finished (frequencies computed)
    this._times = [];      // numerical: Date.now() per new displacement
    this.perturbTotal = 0; // analytical: total CP-SCF perturbations (= 3N)
    this.perturbDone = 0;  // analytical: perturbations converged so far (K in "K/N done")
    this.cpscfIter = 0;    // analytical: CP-SCF iteration count
    this.aStage = "";      // analytical: key of the current A_STAGES entry ("" = not staged yet)
    this.aIdx = -1;        // analytical: index of aStage in A_STAGES (-1 = none)
    this.nuclei = 0;       // analytical: atom count (from the GEOMETRIC PERTURBATIONS banner)
    this.atomsDone = 0;    // analytical: nuclei whose derivative integrals are finished
    this.batchAtoms = 0;   // analytical: nuclei in the batch currently running
    this.batchIdx = -1;    // analytical: index of that batch (monotonic guard)
    this.tempK = 0;        // analytical: thermochemistry temperature (K)
    this.finished = false; // analytical: ORCA TERMINATED NORMALLY seen — chain complete
    this.stageT = [];      // ms wall clock when each stage index was first entered
    this.subs = [];        // frozen key-result line per stage already left
    this.t0 = 0;           // ms when the chain first advanced (total elapsed)
    this.tEnd = 0;         // ms when the run finished (0 while running)
    this.noTimes = false;  // rebuilt from disk — wall-clock stamps would be meaningless
    this.method = "";      // echoed method ("!"-line, first two tokens)
    this.cores = 0;        // echoed %pal nprocs
  }
  // advance the analytical phase chain — monotonic, so a re-seen banner (or a
  // late "K/N done" line) can never move the chain backwards. Entering a new
  // stage stamps its wall clock and freezes the key result of the stage left.
  FreqTracker.prototype._advance = function (idx) {
    this.mode = "analytical"; this.active = true;
    if (!this.t0) this.t0 = Date.now();
    if (idx > this.aIdx) {
      // leaving the integrals stage: its last batch finished, so freeze the
      // count as complete instead of the "N-1 of N" the last BATCH line left
      if (this.aStage === "integrals") { this.atomsDone = this.nuclei; this.batchAtoms = 0; }
      if (this.aIdx >= 0) this.subs[this.aIdx] = this._curSub();
      this.aIdx = idx; this.aStage = A_STAGES[idx].key;
      if (this.stageT[idx] == null) this.stageT[idx] = Date.now();
    }
    if (this.aIdx >= 3) this.dispDone = true;   // "freq" and later: Hessian is done
    return true;
  };
  // the current stage's key-result line (frozen into subs[] when the stage ends)
  FreqTracker.prototype._curSub = function () {
    const dim3N = this.nuclei ? 3 * this.nuclei : this.perturbTotal;
    const cpTotal = this.perturbTotal || dim3N;
    if (this.aStage === "integrals") {
      if (!this.nuclei) return this.atomsDone ? `${this.atomsDone} nuclei` : "";
      return this.atomsDone && this.atomsDone < this.nuclei
        ? `${this.atomsDone}/${this.nuclei} nuclei`
        : `${this.nuclei} nuclei`;
    }
    if (this.aStage === "cpscf") return `${this.perturbDone}/${cpTotal || "?"} perturbations${this.cpscfIter ? ` · iter ${this.cpscfIter}` : ""}`;
    if (this.aStage === "hessian") return dim3N ? `${dim3N}×${dim3N}` : "";
    if (this.aStage === "freq" || this.aStage === "modes" || this.aStage === "ir") return dim3N ? `${dim3N} modes` : "";
    if (this.aStage === "thermo") return this.tempK ? `${this.tempK} K` : "";
    return "";
  };
  // back to a clean slate (a pre-step Hessian's chain is over, see PHASE_RESET_RE)
  FreqTracker.prototype._resetChain = function () {
    this.mode = ""; this.total = 0; this.cur = 0; this.active = false;
    this.dispDone = false; this._times = [];
    this.perturbTotal = 0; this.perturbDone = 0; this.cpscfIter = 0;
    this.aStage = ""; this.aIdx = -1; this.nuclei = 0; this.tempK = 0;
    this.atomsDone = 0; this.batchAtoms = 0; this.batchIdx = -1;
    this.finished = false;
    this.stageT = []; this.subs = []; this.t0 = 0; this.tEnd = 0;   // meta (method/cores) survives: same job
  };
  FreqTracker.prototype.push = function (line) {
    _grabOrcaMeta(this, line);
    if (PHASE_RESET_RE.test(line)) {
      const was = this.active;
      if (was) this._resetChain();
      return was;   // true -> re-render so the stale panel disappears
    }
    if (VFREQ_RE.test(line) && this.mode !== "analytical") {
      // numerical (or untyped) run reaching the frequency table
      if (this.active) this.dispDone = true;
      return false;
    }
    // numerical markers take precedence and lock the mode
    if (NFREQ_START_RE.test(line)) { this.mode = "numerical"; this.active = true; return true; }
    const h = line.match(NDISP_RE);
    if (h) { this.total = parseInt(h[1], 10); this.mode = "numerical"; this.active = true; return true; }
    const d = line.match(DISP_RE);
    if (d) {
      const k = parseInt(d[1], 10), n = parseInt(d[2], 10);
      if (n > 0) this.total = n;
      if (k > this.cur) { this.cur = k; this._times.push(Date.now()); if (this._times.length > 40) this._times.shift(); }
      this.mode = "numerical"; this.active = true;
      return true;
    }
    if (this.mode === "numerical") return false;
    // analytical pipeline: stage banners (ordered, see A_STAGES)
    for (let i = 0; i < A_STAGES.length; i++) {
      if (A_STAGES[i].re.test(line)) {
        if (this.mode !== "analytical" && !A_STAGES[i].boot) break;   // shared banner: never bootstraps
        if (A_STAGES[i].key === "integrals") {
          const n = line.match(ANUCLEI_RE);
          if (n) { this.nuclei = parseInt(n[1], 10); if (!this.perturbTotal) this.perturbTotal = 3 * this.nuclei; }
        } else if (A_STAGES[i].key === "thermo") {
          const t = line.match(A_STAGES[i].re);
          if (t && t[1]) this.tempK = parseFloat(t[1]);
        }
        return this._advance(i);
      }
    }
    if (this.mode === "analytical") {
      if (this.aStage === "integrals") {
        const b = line.match(ABATCH_RE);
        if (b) {
          const k = parseInt(b[1], 10);
          if (k > this.batchIdx) {
            // the announced batch is only STARTING: what is finished is every
            // batch before it, so credit the previous batch's atoms now
            this.batchIdx = k;
            this.atomsDone += this.batchAtoms;
            this.batchAtoms = Math.max(parseInt(b[3], 10) - parseInt(b[2], 10) + 1, 1);
          }
          return true;
        }
      }
      // first NPERT wins: the geometric CP-SCF (the Hessian's 3N-ish solve) is
      // always the FIRST response solve in the pipeline; later property solves
      // (IR intensities, EPR/NMR blocks) print their own, smaller
      // "Number of perturbations" lines that must not overwrite the total
      const np = line.match(NPERT_RE);
      if (np) { if (!this.perturbTotal) this.perturbTotal = parseInt(np[1], 10); return true; }
      const cd = line.match(CPSCF_DONE_RE);
      if (cd) {
        this.perturbDone = parseInt(cd[1], 10);
        if (!this.perturbTotal) this.perturbTotal = parseInt(cd[2], 10);
        this.cpscfIter++;
        return this._advance(1);   // "cpscf"
      }
      if (ATERM_RE.test(line)) { this.finished = true; if (!this.tEnd) this.tEnd = Date.now(); return true; }
    }
    if (AFREQ_RE.test(line)) { this.mode = "analytical"; this.active = true; return true; }
    return false;
  };
  FreqTracker.prototype.hasData = function () { return this.active; };
  FreqTracker.prototype.progress = function () {
    if (this.dispDone) return 1;
    if (this.mode === "numerical") return this.total ? Math.min(this.cur / this.total, 0.999) : 0;
    if (this.mode === "analytical") return (this.perturbTotal && this.perturbDone) ? Math.min(this.perturbDone / this.perturbTotal, 0.999) : 0;
    return 0;
  };
  FreqTracker.prototype.perStepMs = function () {
    const t = this._times;
    if (t.length >= 2) {
      const gaps = []; for (let i = 1; i < t.length; i++) gaps.push(t[i] - t[i - 1]);
      const recent = gaps.slice(-8).filter(function (g) { return g >= 500; }).sort(function (a, b) { return a - b; });
      if (recent.length) return recent[Math.floor(recent.length / 2)];
    }
    return null;
  };
  FreqTracker.prototype.estimateETA = function () {
    if (!this.total || this.cur < 1 || this.dispDone) return null;
    const remaining = Math.max(this.total - this.cur, 0);
    const perMs = this.perStepMs();
    return { remaining: remaining, etaMs: perMs != null ? remaining * perMs : null };
  };

  // ---- TD-DFT stage tracking ----
  // Same idea as the analytical-frequencies chain: ORCA prints an UPPERCASE
  // banner per pipeline stage, always in the same order (verified on 7 real
  // ORCA 6.1.1 TD-DFT outputs, both full TD-DFT/RPA and TDA/Davidson):
  // XC-kernel setup -> iterative diagonalization (with "****Iteration K****"
  // lines and a fixed "Number of roots" total) -> excited-state analysis ->
  // transition spectra -> final CIS/TD-DFT total energy.
  const TD_STAGES = [
    { key: "setup",   re: /TD-DFT XC SETUP/,                              label: "XC kernel" },
    { key: "solver",  re: /RPA-DIAGONALIZATION|DAVIDSON-DIAGONALIZATION/, label: "Diagonalization" },
    { key: "states",  re: /TD-DFT(?:\/TDA)? EXCITED STATES/,              label: "Excited states" },
    { key: "spectra", re: /ABSORPTION SPECTRUM VIA TRANSITION/,           label: "Transition spectra" },
    { key: "energy",  re: /CIS\/TD-DFT TOTAL ENERGY/,                     label: "Total energy" },
  ];
  const TD_ROOTS_RE = /Number of roots to be determined\s+\.+\s*(\d+)/;
  const TD_ITER_RE = /\*{4}\s*Iteration\s+(\d+)\s*\*{4}/;

  function TddftTracker() {
    this.active = false;   // a TD-DFT stage banner has been seen
    this.aStage = "";      // key of the current TD_STAGES entry ("" = none yet)
    this.aIdx = -1;        // index of aStage in TD_STAGES (-1 = none)
    this.roots = 0;        // excited states requested ("Number of roots ...")
    this.iter = -1;        // latest solver iteration (ORCA counts from 0)
    this.solver = "";      // "RPA" | "DAVIDSON" (which solver banner matched)
    this.finished = false; // ORCA TERMINATED NORMALLY seen — chain complete
    this.stageT = [];      // ms wall clock when each stage index was first entered
    this.subs = [];        // frozen key-result line per stage already left
    this.t0 = 0;           // ms when the chain first advanced (total elapsed)
    this.tEnd = 0;         // ms when the run finished (0 while running)
    this.noTimes = false;  // rebuilt from disk — wall-clock stamps would be meaningless
    this.method = "";      // echoed method ("!"-line, first two tokens)
    this.cores = 0;        // echoed %pal nprocs
  }
  // monotonic, like FreqTracker._advance: a re-seen banner (the spectra stage
  // prints several "... SPECTRUM VIA TRANSITION ..." blocks) never moves back
  TddftTracker.prototype._advance = function (idx) {
    this.active = true;
    if (!this.t0) this.t0 = Date.now();
    if (idx > this.aIdx) {
      if (this.aIdx >= 0) this.subs[this.aIdx] = this._curSub();
      this.aIdx = idx; this.aStage = TD_STAGES[idx].key;
      if (this.stageT[idx] == null) this.stageT[idx] = Date.now();
    }
    return true;
  };
  // the current stage's key-result line (frozen into subs[] when the stage ends)
  TddftTracker.prototype._curSub = function () {
    if (this.aStage === "solver") return this.roots
      ? `${this.solver || "iterative"} · ${this.roots} roots${this.iter >= 0 ? ` · iter ${this.iter}` : ""}`
      : "diagonalizing";
    if (this.aStage === "states" || this.aStage === "spectra" || this.aStage === "energy")
      return this.roots ? `${this.roots} states` : "";
    return "";
  };
  TddftTracker.prototype.push = function (line) {
    _grabOrcaMeta(this, line);
    // same pre-step reset as FreqTracker: an excited-state optimization (raw
    // input) runs the TD-DFT module inside every cycle — show the chain while
    // it runs, clear it when the next cycle starts
    if (PHASE_RESET_RE.test(line)) {
      const was = this.active;
      if (was) {
        this.active = false; this.aStage = ""; this.aIdx = -1;
        this.roots = 0; this.iter = -1; this.solver = ""; this.finished = false;
        this.stageT = []; this.subs = []; this.t0 = 0; this.tEnd = 0;
      }
      return was;
    }
    for (let i = 0; i < TD_STAGES.length; i++) {
      if (TD_STAGES[i].re.test(line)) {
        if (TD_STAGES[i].key === "solver") this.solver = /RPA-DIAGONALIZATION/.test(line) ? "RPA" : "DAVIDSON";
        return this._advance(i);
      }
    }
    if (!this.active) return false;
    const r = line.match(TD_ROOTS_RE);
    if (r) { this.roots = parseInt(r[1], 10); return true; }
    if (this.aStage === "solver") {
      const it = line.match(TD_ITER_RE);
      if (it) { this.iter = parseInt(it[1], 10); return true; }
    }
    if (ATERM_RE.test(line)) { this.finished = true; if (!this.tEnd) this.tEnd = Date.now(); return true; }
    return false;
  };
  TddftTracker.prototype.hasData = function () { return this.active; };

  // ---------- staged-pipeline stepper (CREST / analytical freq / TD-DFT) ----------
  // Vertical stepper: a header (mono title + RUNNING/DONE/STOPPED pill) over a
  // meta line (name · method · cores · elapsed), then one row per stage — dot
  // rail on the left, stage label with its key result under it, the stage's
  // wall time right-aligned. Rows stretch between ROW_MIN and ROW_MAX so the
  // rail follows the window height (opts.height, measured by the caller); when
  // opts.height cannot fit every row without label overlap the compact strip
  // (_stepCompactHtml — the retired HUD frame minus its hazard stripes, same
  // text) is rendered instead. Live clocks (current stage + total elapsed)
  // carry data-clock="<start ms>" and are re-stamped in place by the app's 1s
  // poll, so they keep ticking while the log is silent.
  const STEP_HEAD_PX = 88, STEP_ROW_MIN = 46, STEP_ROW_MAX = 96, STEP_FOOT_PX = 30;

  function _fmtClock(sec) {
    sec = Math.max(0, Math.round(sec));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const ss = (s < 10 ? "0" : "") + s;
    if (!h) return `${m}:${ss}`;
    return `${h}:${m < 10 ? "0" : ""}${m}:${ss}`;
  }

  // per-stage wall seconds from the tracker's stage-entry stamps: a stage ends
  // when the next entered stage begins; the last entered stage ends at tEnd
  // (finish/stop) or keeps running to `now`.
  function _stageSecs(t, n, now) {
    const out = new Array(n).fill(null);
    for (let i = 0; i < n; i++) {
      const ti = t.stageT[i];
      if (ti == null) continue;
      let end = null;
      for (let j = i + 1; j < n; j++) if (t.stageT[j] != null) { end = t.stageT[j]; break; }
      if (end == null) end = t.tEnd || now;
      out[i] = (end - ti) / 1000;
    }
    return out;
  }

  // shared row builder: label + key result (frozen for past stages, live for
  // the current one) + per-stage seconds (suppressed on disk-rebuilt trackers).
  // A stage the chain jumped over — no entry stamp of its own, but a later
  // stage does have one — is flagged `skipped` (a conditional phase that didn't
  // run this time, e.g. CREST's Genetic crossing when there's nothing to cross);
  // it reads as skipped instead of a misleading instant "done".
  function _stepRows(t, stages, state, curSub, now) {
    const n = stages.length;
    const secs = t.noTimes ? new Array(n).fill(null) : _stageSecs(t, n, now);
    let lastStamped = -1;
    for (let i = n - 1; i >= 0; i--) if (t.stageT[i] != null) { lastStamped = i; break; }
    const rows = [];
    for (let i = 0; i < n; i++) {
      const cur = i === t.aIdx;
      const skipped = t.stageT[i] == null && i < lastStamped;
      rows.push({
        label: stages[i].label,
        sub: skipped ? "skipped" : (i < t.aIdx ? (t.subs[i] || "") : (cur ? curSub : "")),
        secs: secs[i],
        live: state === "running" && cur && t.stageT[i] != null,
        startMs: t.stageT[i] || 0,
        skipped: skipped,
      });
    }
    return rows;
  }

  function _stepMetaParts(t) {
    const parts = [];
    if (t.method) parts.push(t.method);
    if (t.solvent) parts.push(t.solvent);      // CREST: --alpb/--gbsa solvent
    if (t.nci) parts.push("NCI mode");         // CREST: --nci ellipsoid potential
    if (t.cores) parts.push(`${t.cores} cores`);
    return parts;
  }

  function _stepElapsed(t, now) {
    if (t.noTimes || !t.t0) return null;
    if (t.tEnd) return { sec: (t.tEnd - t.t0) / 1000, live: false, startMs: 0 };
    return { sec: (now - t.t0) / 1000, live: true, startMs: t.t0 };
  }

  function _stepMetaHtml(m) {
    const parts = m.meta.slice();
    if (m.elapsed) {
      parts.push(`elapsed <span${m.elapsed.live ? ` data-clock="${m.elapsed.startMs}"` : ""}>${_fmtClock(m.elapsed.sec)}</span>`);
    }
    return parts.join(" · ");
  }

  // m: {title, state: "running"|"done"|"error", at (n = all done),
  //     rows: [{label, sub, secs, live, startMs}], meta: string[], elapsed,
  //     foot?: string — one status line under the rows (CREST's CREGEN strip)}
  function _stepPanelHtml(m, opts) {
    const n = m.rows.length;
    const footPx = m.foot ? STEP_FOOT_PX : 0;
    const avail = opts && opts.height ? opts.height : 0;
    if (avail && avail < STEP_HEAD_PX + footPx + n * STEP_ROW_MIN) return _stepCompactHtml(m);
    const style = avail ? ` style="height:${Math.round(Math.min(avail, STEP_HEAD_PX + footPx + n * STEP_ROW_MAX))}px"` : "";
    let pill;
    if (m.state === "done") pill = `<span class="vstep-pill ok">✓ DONE</span>`;
    else if (m.state === "error") pill = `<span class="vstep-pill err">■ STOPPED</span>`;
    else pill = `<span class="vstep-pill ok">▷ RUNNING</span>`;
    let rows = "";
    for (let i = 0; i < n; i++) {
      const r = m.rows[i];
      let cls;
      if (r.skipped) cls = "skipped";
      else if (i < m.at) cls = "done";
      else if (i === m.at) cls = m.state === "error" ? "cur err" : "cur";
      else cls = "pending";
      if (i === 0) cls += " first";
      if (i === n - 1) cls += " last";
      const time = r.secs == null ? "" :
        `<div class="vstep-time"${r.live ? ` data-clock="${r.startMs}"` : ""}>${_fmtClock(r.secs)}</div>`;
      rows +=
        `<div class="vstep-row ${cls}">` +
          `<div class="vstep-rail">` +
            `<span class="vstep-line up${i <= m.at ? " on" : ""}"></span>` +
            `<span class="vstep-dot"></span>` +
            `<span class="vstep-line down${i < m.at ? " on" : ""}"></span>` +
          `</div>` +
          `<div class="vstep-body">` +
            `<div class="vstep-label">${r.label}</div>` +
            (r.sub ? `<div class="vstep-sub">${r.sub}</div>` : "") +
          `</div>` +
          (time || `<div class="vstep-time"></div>`) +
        `</div>`;
    }
    return (
      `<div class="vstep"${style}>` +
        `<div class="vstep-head"><div class="vstep-title">${m.title}</div>${pill}</div>` +
        `<div class="vstep-meta">${_stepMetaHtml(m)}</div>` +
        `<div class="vstep-rows">${rows}</div>` +
        (m.foot ? `<div class="vstep-foot">${m.foot}</div>` : "") +
      `</div>`
    );
  }

  // compact fallback for short windows: the retired HUD frame minus its hazard
  // stripes — centered title, STEP k/n (or DONE/STOPPED) left, the current
  // stage's label + key result center, the dot chain right, meta line below.
  function _stepCompactHtml(m) {
    const n = m.rows.length;
    let dots = "";
    for (let i = 0; i < n; i++) {
      if (i) dots += `<span class="stepc-link${i <= m.at ? " done" : ""}"></span>`;
      dots += `<span class="stepc-dot${i < m.at ? " done" : (i === m.at ? (m.state === "error" ? " err" : " cur") : "")}"></span>`;
    }
    let phase;
    if (m.state === "done") phase = `<div class="stepc-phase">DONE</div>`;
    else if (m.state === "error") phase = `<div class="stepc-phase err">STOPPED</div>`;
    else phase = `<div class="stepc-phase">STEP ${Math.min(m.at + 1, n)}<span class="stepc-of">/${n}</span></div>`;
    const cur = m.rows[Math.min(Math.max(m.at, 0), n - 1)];
    return (
      `<div class="stepc">` +
        `<div class="stepc-title">${m.title}</div>` +
        `<div class="stepc-row">` + phase +
          `<div class="stepc-center">` +
            `<div class="stepc-label">${cur.label}</div>` +
            (cur.sub ? `<div class="stepc-sub">${cur.sub}</div>` : "") +
          `</div>` +
          `<div class="stepc-dots">${dots}</div>` +
        `</div>` +
        `<div class="stepc-meta">${_stepMetaHtml(m)}</div>` +
      `</div>`
    );
  }

  function renderTddftProgress(td, opts) {
    const state = td.finished ? "done" : "running";
    const now = Date.now();
    return _stepPanelHtml({
      title: "TD-DFT excited states",
      state: state,
      at: state === "done" ? TD_STAGES.length : Math.max(td.aIdx, 0),
      rows: _stepRows(td, TD_STAGES, state, td._curSub(), now),
      meta: _stepMetaParts(td),
      elapsed: _stepElapsed(td, now),
    }, opts);
  }

  function _renderAnalyticalPanel(freq, opts) {
    const state = freq.finished ? "done" : "running";
    const now = Date.now();
    return _stepPanelHtml({
      title: "Analytical frequencies",
      state: state,
      at: state === "done" ? A_STAGES.length : Math.max(freq.aIdx, 0),
      rows: _stepRows(freq, A_STAGES, state, freq._curSub(), now),
      meta: _stepMetaParts(freq),
      elapsed: _stepElapsed(freq, now),
    }, opts);
  }

  function renderFreqProgress(freq, opts) {
    if (freq.mode === "analytical") return _renderAnalyticalPanel(freq, opts);
    // floor, not round: progress() caps at 0.999 while the final displacement
    // is still running, and round() displayed a premature "100%" there
    const pct = Math.floor(freq.progress() * 100);
    if (freq.dispDone) {
      return `<div class="scf-prog-label">Frequencies · displacements complete · 100%</div>` +
        `<div class="scf-prog-bar"><span style="width:100%"></span></div>` +
        `<div class="scf-prog-meta">building Hessian + thermochemistry…</div>`;
    }
    const out = [
      `<div class="scf-prog-label">Numerical frequencies ${pct}% · displacement ${freq.cur}/${freq.total}</div>`,
      `<div class="scf-prog-bar"><span style="width:${pct}%"></span></div>`,
    ];
    const perMs = freq.perStepMs();
    const rate = _fmtDuration(perMs);
    const eta = freq.estimateETA();
    // freq ETA is reliable (known total) — show the real time, not a coarse bucket
    if (eta && eta.etaMs != null) {
      out.push(`<div class="scf-prog-meta">~${_fmtDuration(eta.etaMs)} remaining · ${eta.remaining} displacement${eta.remaining === 1 ? "" : "s"} left${rate ? " · ~" + rate + " each" : ""}</div>`);
    } else if (rate) {
      out.push(`<div class="scf-prog-meta">~${rate}/displacement</div>`);
    } else {
      out.push(`<div class="scf-prog-meta">estimating…</div>`);
    }
    return out.join("");
  }

  // ---------- CREST conformer-search progress ----------
  // A CREST iMTD-GC run streams a fixed structure to its .out (verified on a real
  // CREST 3.0.2 run, 663 lines): an initial optimization, then repeated
  // "Meta-Dynamics Iteration N" cycles (each running a known number of MTDs and
  // ending in a CREGEN sort that prints the lowest energy + ensemble size within
  // a kcal window), additional MDs, and a final optimization, before
  // "CREST terminated normally.". Unlike SCF/opt there is NO single monotone
  // convergence quantity and the macro-iteration count is unknown up front, so
  // this tracks a PHASE CHAIN (init -> sampling -> MD -> final) plus — within
  // sampling — the MTD count, the multilevel ensemble-optimization sub-phase
  // ("Optimizing all N structures", crude/tight/very tight — the multi-minute
  // stretch that used to look stalled), and the CREGEN lowest-energy /
  // in-window series appended to the same key-result line; a footer strip
  // appears only once finished (ensemble statistics). NOTE the .out
  // tail delivers complete lines only, so CREST's in-place "|>10.2% ..."
  // percent tokens inside an optimization block are NOT available live — the
  // sub-phase granularity here is the finest the stream supports.
  // The iMTD-GC pipeline in order (verified on real CREST 3.0.2 output):
  // initial opt -> metadynamics sampling -> additional regular MDs -> genetic
  // structure crossing (GC) -> final opt -> final CREGEN ensemble sort. CREGEN
  // sorting also runs after every intermediate optimization block, but the
  // "Final Ensemble Information" block is the terminal, unambiguous one.
  const CREST_STAGES = [
    { key: "init",   re: /Initial Geometry Optimization/,            label: "Initial optimization" },
    { key: "sample", re: /iMTD-GC SAMPLING|Meta-Dynamics Iteration/, label: "Metadynamics sampling" },
    { key: "md",     re: /Additional regular MDs/,                   label: "Regular MD" },
    { key: "gc",     re: /Structure Crossing \(GC\)/,                label: "Genetic crossing (GC)" },
    { key: "final",  re: /Final Geometry Optimization/,              label: "Final optimization" },
    { key: "cregen", re: /Final Ensemble Information/,               label: "Ensemble sorting (CREGEN)" },
  ];
  // CREST echoes its command line ("$ /path/crest input.xyz --gfn2 ... -T 4"):
  // the --gfn* flag and -T carry the stepper meta line's method + core count
  const CREST_CMD_RE = /^\s*\$\s+\S*crest\S*\s+(.+)$/i;
  const CREST_METHOD_NAMES = { gfn2: "GFN2-xTB", gfn1: "GFN1-xTB", gfn0: "GFN0-xTB", gfnff: "GFN-FF" };
  const CREST_ELOW_RE    = /CREGEN>\s*E lowest\s*:\s*(-?\d+\.\d+)/i;
  const CREST_WINDOW_RE  = /(\d+)\s+structures?\s+remain within\s+([\d.]+)\s*kcal/i;
  const CREST_ITER_RE    = /Meta-Dynamics Iteration\s+(\d+)/i;
  const CREST_MTDTOT_RE  = /\((\d+)\s*MTDs\)/;
  // an MTD that "terminated EARLY" still delivered its trajectory — count it,
  // or the batch counter sticks below N/N forever (seen on real 3.0.2 runs)
  const CREST_MTDDONE_RE = /\*MTD\s+\d+\s+(?:completed successfully|terminated EARLY)/i;
  const CREST_DONE_RE    = /CREST terminated normally\./;
  const CREST_ERR_RE     = /Change in topology detected|safety termination of CREST/i;
  const CREST_NCONF_RE   = /number of unique conformers for further calc\s+(\d+)/i;
  // multilevel ensemble optimization (runs after each MTD batch and as the
  // final optimization; line formats verbatim from real CREST 3.0.2 output)
  const CREST_MTDLEN_RE  = /^\s*t\(MTD\)\s*\/\s*ps\s*:\s*([\d.]+)/i;
  const CREST_OPTALL_RE  = /Optimizing all\s+(\d+)\s+structures/i;
  const CREST_OPTCRUDE_RE = /^\s*crude pre-optimization/i;
  const CREST_OPTLVL_RE  = /^\s*optimization with (very tight|tight) thresholds/i;
  // " A new lower conformer was found!" is followed by this line:
  const CREST_IMPROVED_RE = /Improved by\s+[-\d.]+\s*Eh or\s+([-\d.]+)\s*kcal\/mol/i;
  // Final Ensemble Information + Wall Time Summary key stats
  const CREST_POP_RE     = /population of lowest in %\s*:\s*([\d.]+)/i;
  const CREST_GFREE_RE   = /ensemble free energy \(kcal\/mol\)\s*:\s*(-?[\d.]+)/i;
  const CREST_TMTD_RE    = /^\s*Metadynamics \(MTD\)\s+\.\.\..*\(\s*([\d.]+)%\)/;
  const CREST_TOPT_RE    = /^\s*Geometry optimization\s+\.\.\..*\(\s*([\d.]+)%\)/;

  function CrestTracker() {
    this.active = false;    // any CREST progress marker seen
    this.aIdx = -1;         // index into CREST_STAGES (-1 = none yet)
    this.aStage = "";       // key of the current stage
    this.iter = 0;          // latest "Meta-Dynamics Iteration N"
    this.mtdTotal = 0;      // MTDs per iteration (from "(14 MTDs)")
    this.mtdDone = 0;       // MTDs completed in the CURRENT iteration
    this.mtdLen = 0;        // MTD length in ps ("t(MTD) / ps : 17.0")
    this.energies = [];     // CREGEN "E lowest" series (Hartree), one per sort pass
    this.windowCount = 0;   // latest "N structures remain within ... kcal" count
    this.windowKcal = 0;    // that line's kcal/mol window (2×ewin crude, ewin tight)
    this.optTotal = 0;      // ensemble optimization: structures in the running block
    this.optLevel = "";     // "" | "crude" | "tight" | "very tight"
    this.optActive = false; // an ensemble-optimization block is running now
    this.improvedKcal = 0;  // latest "A new lower conformer" improvement (kcal/mol)
    this.pop = 0;           // final "population of lowest in %"
    this.gFree = null;      // final "ensemble free energy (kcal/mol)"
    this.mtdPct = 0;        // Wall Time Summary: % of runtime spent in MTD
    this.optPct = 0;        // Wall Time Summary: % of runtime spent optimizing
    this.nConf = 0;         // final "number of unique conformers for further calc"
    this.finished = false;  // "CREST terminated normally."
    this.error = false;     // a known abort signature (topology change / safety stop)
    this.stageT = [];       // ms wall clock when each stage index was first entered
    this.subs = [];         // frozen key-result line per stage already left
    this.t0 = 0;            // ms when the chain first advanced (total elapsed)
    this.tEnd = 0;          // ms when the run finished/stopped (0 while running)
    this.noTimes = false;   // rebuilt from disk — wall-clock stamps would be meaningless
    this.method = "";       // echoed --gfn* flag, prettified ("GFN2-xTB")
    this.solvent = "";      // echoed --alpb/--gbsa solvent, e.g. "ALPB(acetone)"
    this.nci = false;       // echoed --nci flag (NCI ellipsoid-potential mode)
    this.cores = 0;         // echoed -T thread count
  }
  // monotonic phase advance, like FreqTracker/TddftTracker
  CrestTracker.prototype._advance = function (idx) {
    this.active = true;
    if (!this.t0) this.t0 = Date.now();
    if (idx > this.aIdx) {
      if (this.aIdx >= 0) this.subs[this.aIdx] = this._curSub();
      this.aIdx = idx; this.aStage = CREST_STAGES[idx].key;
      if (this.stageT[idx] == null) this.stageT[idx] = Date.now();
    }
    return true;
  };
  // latest "N in window" text (the kcal width when known)
  CrestTracker.prototype._winTxt = function () {
    return `${this.windowCount} in ${this.windowKcal ? `${this.windowKcal} kcal ` : ""}window`;
  };
  // a running ensemble-optimization block's text, "" when none is running
  CrestTracker.prototype._optTxt = function () {
    if (!this.optActive || !this.optTotal) return "";
    return `optimizing ${this.optTotal} structures${this.optLevel ? ` (${this.optLevel})` : ""}`;
  };
  // the latest CREGEN ensemble state (best E so far, in-window count, newest
  // "new lower conformer" improvement) — appended to the current stage's
  // key-result line so it sits right next to the MTD/opt progress
  CrestTracker.prototype._cregenTxt = function () {
    if (!this.energies.length) return this.windowCount ? this._winTxt() : "";
    const bits = [`E lowest ${Math.min.apply(null, this.energies).toFixed(5)} Eh`];
    if (this.windowCount) bits.push(this._winTxt());
    if (this.improvedKcal) bits.push(`new lowest −${this.improvedKcal.toFixed(2)} kcal/mol`);
    return bits.join(" · ");
  };
  // the current stage's key-result line (frozen into subs[] when the stage ends)
  CrestTracker.prototype._curSub = function () {
    if (this.aStage === "sample") {
      const parts = [`iteration ${this.iter || 1}`];
      const opt = this._optTxt();
      const cg = this._cregenTxt();
      if (opt) parts.push(opt);
      else if (this.mtdTotal && (this.mtdDone < this.mtdTotal || !cg))
        parts.push(`${Math.min(this.mtdDone, this.mtdTotal)}/${this.mtdTotal} MTDs${!cg && this.mtdLen ? ` · ${this.mtdLen} ps each` : ""}`);
      if (cg) parts.push(cg);
      return parts.join(" · ");
    }
    if (this.aStage === "md" || this.aStage === "gc" || this.aStage === "final") {
      const parts = [];
      const opt = this._optTxt();
      if (opt) parts.push(opt);
      const cg = this._cregenTxt();
      if (cg) parts.push(cg);
      return parts.join(" · ");
    }
    return "";
  };
  CrestTracker.prototype.push = function (line) {
    const cmd = line.match(CREST_CMD_RE);
    if (cmd) {
      const gm = cmd[1].match(/--(gfn\S*)/i);
      if (gm) this.method = gm[1].toLowerCase().split("//")
        .map(function (k) { return CREST_METHOD_NAMES[k] || k.toUpperCase(); }).join("//");
      const sv = cmd[1].match(/--(alpb|gbsa)\s+(\S+)/i);
      if (sv) this.solvent = `${sv[1].toUpperCase()}(${sv[2]})`;
      if (/--nci\b/i.test(cmd[1])) this.nci = true;
      const th = cmd[1].match(/-T\s+(\d+)/);
      if (th) this.cores = parseInt(th[1], 10);
      return false;   // meta only — nothing to redraw yet
    }
    if (CREST_ERR_RE.test(line)) { this.error = true; this.active = true; if (!this.tEnd) this.tEnd = Date.now(); return true; }
    if (CREST_DONE_RE.test(line)) { this.finished = true; this.active = true; if (!this.tEnd) this.tEnd = Date.now(); return true; }
    const nc = line.match(CREST_NCONF_RE);
    if (nc) { this.nConf = parseInt(nc[1], 10); this.active = true; return true; }
    const mt = line.match(CREST_MTDTOT_RE);
    if (mt) { this.mtdTotal = parseInt(mt[1], 10); this.active = true; return true; }
    const it = line.match(CREST_ITER_RE);
    if (it) {
      this.iter = parseInt(it[1], 10); this.mtdDone = 0;
      this.optActive = false; this.optTotal = 0; this.optLevel = "";
      return this._advance(1);
    }
    if (CREST_MTDDONE_RE.test(line)) { this.mtdDone++; this.active = true; return true; }
    const ml = line.match(CREST_MTDLEN_RE);
    if (ml) { this.mtdLen = parseFloat(ml[1]); this.active = true; return true; }
    // ensemble-optimization sub-phase: "Optimizing all N structures" opens a
    // block; crude/tight/very-tight name its level. A tight sub-block reuses
    // the structures the crude CREGEN kept, so re-opening after a CREGEN pass
    // adopts the latest in-window count as its total. The block closes on the
    // CREGEN "E lowest" that follows it (and on a new iteration / MTD batch).
    const oa = line.match(CREST_OPTALL_RE);
    if (oa) { this.optTotal = parseInt(oa[1], 10); this.optLevel = ""; this.optActive = true; this.active = true; return true; }
    if (CREST_OPTCRUDE_RE.test(line)) { this.optLevel = "crude"; this.optActive = true; this.active = true; return true; }
    const ol = line.match(CREST_OPTLVL_RE);
    if (ol) {
      this.optLevel = ol[1].toLowerCase();
      if (!this.optActive) { this.optActive = true; if (this.windowCount) this.optTotal = this.windowCount; }
      this.active = true; return true;
    }
    const el = line.match(CREST_ELOW_RE);
    if (el) {
      const e = parseFloat(el[1]);
      if (isFinite(e)) this.energies.push(e);
      this.optActive = false;
      // an "Improved by" note belongs to the pass that just ended — a new
      // CREGEN pass starts fresh, so the note doesn't linger for the whole run
      this.improvedKcal = 0;
      this.active = true; return true;
    }
    const w = line.match(CREST_WINDOW_RE);
    if (w) {
      this.windowCount = parseInt(w[1], 10);
      const k = parseFloat(w[2]);
      if (isFinite(k)) this.windowKcal = k;
      this.active = true; return true;
    }
    const im = line.match(CREST_IMPROVED_RE);
    if (im) { this.improvedKcal = parseFloat(im[1]); this.active = true; return true; }
    const pp = line.match(CREST_POP_RE);
    if (pp) { this.pop = parseFloat(pp[1]); this.active = true; return true; }
    const gf = line.match(CREST_GFREE_RE);
    if (gf) { this.gFree = parseFloat(gf[1]); this.active = true; return true; }
    const tm = line.match(CREST_TMTD_RE);
    if (tm) { this.mtdPct = parseFloat(tm[1]); return false; }   // meta for the done footer
    const to = line.match(CREST_TOPT_RE);
    if (to) { this.optPct = parseFloat(to[1]); return false; }
    for (let i = 0; i < CREST_STAGES.length; i++) {
      if (CREST_STAGES[i].re.test(line)) return this._advance(i);
    }
    return false;
  };
  CrestTracker.prototype.hasData = function () { return this.active; };

  // the footer strip under the CREST stepper — finished runs only: the
  // ensemble statistics + runtime split. The LIVE CREGEN state rides the
  // current stage's key-result line instead (_cregenTxt), next to the
  // MTD/optimization progress it belongs to.
  function _crestFoot(cr) {
    if (!cr.finished) return "";
    const bits = [];
    if (cr.pop) bits.push(`lowest populated ${cr.pop.toFixed(1)}%`);
    if (cr.gFree != null) bits.push(`ensemble ΔG ${cr.gFree.toFixed(2)} kcal/mol`);
    if (cr.mtdPct && cr.optPct) bits.push(`runtime: MTD ${Math.round(cr.mtdPct)}% / opt ${Math.round(cr.optPct)}%`);
    return bits.join(" · ");
  }

  function renderCrestProgress(cr, opts) {
    const state = cr.error ? "error" : cr.finished ? "done" : "running";
    const now = Date.now();
    const curSub = cr.error ? "stopped — check the log (topology change?)" : cr._curSub();
    const rows = _stepRows(cr, CREST_STAGES, state, curSub, now);
    // the search's headline result lands on the final stage once it's done
    if (cr.finished) rows[rows.length - 1].sub = cr.nConf
      ? `${cr.nConf} conformer${cr.nConf === 1 ? "" : "s"}` : "complete";
    return _stepPanelHtml({
      title: "CREST conformer search",
      state: state,
      at: state === "done" ? CREST_STAGES.length : Math.max(cr.aIdx, 0),
      rows: rows,
      meta: _stepMetaParts(cr),
      elapsed: _stepElapsed(cr, now),
      foot: _crestFoot(cr),
    }, opts);
  }

  // Extend the shared namespace in place — SCFGraph.* is the single entry point
  // for every caller, before and after this split.
  Object.assign(SCFGraph, {
    FreqTracker: FreqTracker,
    TddftTracker: TddftTracker,
    CrestTracker: CrestTracker,
    fmtClock: _fmtClock,   // m:ss / h:mm:ss — app.js re-stamps [data-clock] with it
    renderFreqProgress: renderFreqProgress,
    renderTddftProgress: renderTddftProgress,
    renderCrestProgress: renderCrestProgress,
  });

  if (typeof module !== "undefined" && module.exports) module.exports = SCFGraph;
})(typeof window !== "undefined" ? window : this);
