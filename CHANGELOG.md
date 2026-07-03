# Changelog

All notable changes to ORCAdesk are documented here.
This project loosely follows [Semantic Versioning](https://semver.org/).

## [0.4.3-beta] — 2026-07-03

Constitution-compliance release: the amended failure-lock rule (PRINCIPLES.md
P24) is implemented end to end, the appendices' fix backlog (PRINCIPLES.md
Appendix A, DESIGN.md Appendix B) is worked off, and the project gains its
first automated test suite.

### Added
- **Automated test suite** (`tests/`, PRINCIPLES.md P56): 237 pytest tests
  over the framework-free layers — queue-engine semantics (the P24 failure
  lock, dependency-scoped blocking, keep-existing validation, the three stop
  verbs) driven by a fake runner, per-kind result validation, store/session
  persistence and the structural run-freeze, name validation and
  trust-boundary clamps, input-generator normalization (exact-map
  functionals, `D3BJ`, `_auto_aux`), the parser on synthetic fixtures (plus
  a real-corpus smoke test that auto-skips off-machine), the MLIP pipeline
  end to end with a **stdlib-only stub worker** (no MACE env needed), the
  phone HTTP API via `fastapi.testclient` (auto-skips without fastapi), and
  psutil process identity/tree-kill with real child processes — plus 23
  plain-Node tests for `scf_graph.js` trackers (idempotent cycle keying, the
  99 % cap, marker case-sensitivity, ETA buckets). `pip install -r
  requirements-dev.txt`, then `python -m pytest` and
  `node tests/web/scf_graph.test.js`; the suite runs in ~3 s with no ORCA,
  no MACE, and no network. On its first run it caught the three boundary
  bugs fixed below.

### Changed
- **A failed calculation is now locked at the moment of failure.** It can no
  longer be edited, re-run, or reordered: re-running the queue skips it (with
  a log note saying to build a new calculation to retry), its transitive
  dependents are blocked up front exactly as a live failure would block them,
  and Cancel never re-stamps it. Its remaining interactions are read-only
  diagnosis (state badge, failure message, its on-disk output) and **×
  removal** — which removes only the queue entry; **workspace folders are
  never deleted** (the app contains no file-deletion code). Only a
  **cancelled** calculation (a deliberate user stop, not a failure) re-runs.
  BLOCKED calculations are now editable, so a dependent can be re-pointed at
  another calculation after its failed parent is removed —
  `EDITABLE_STATES` is now pending / cancelled / blocked, mirrored by the
  UI's edit/drag affordances.
- **"Keep existing (skip these)" now verifies the result it keeps.** The kept
  output is parsed through the per-kind dispatcher (an MLIP calc's JSON
  result is no longer fed to the ORCA parser) and stamped `DONE` only if it
  passes per-kind validation — a crashed or chemically-bad output can no
  longer be resurrected as `DONE` (the 0.4.2 incident). If the existing
  result doesn't hold up, the calc simply runs instead.
- **The overwrite-conflict warning is scoped to PENDING/BLOCKED name reuse.**
  DONE never re-runs, FAILED is locked (nothing to overwrite), a CANCELLED
  retry overwriting its own partial `.out` is the point of a retry, and a
  RUNNING calc only ever appends to its own `.out` (reattach is not an
  overwrite) — none of those raises the dialog anymore. Cancelling a run
  also preserves a BLOCKED calc's "Skipped: a dependency failed." diagnosis
  instead of re-stamping it "Cancelled."
- **File-loader slots return one unified JSON envelope** (`LoadResult`) that
  distinguishes user-cancel from a real read failure — cancelling a file
  picker is no longer conflated with an I/O error — and `autodetect_orca`
  returns an explicit `AutodetectResult` envelope (the UI now also refreshes
  the ORCA-ready pill after a successful autodetect).
- **UI conformance sweep against DESIGN.md**: segmented toggles converge on
  one canonical size and the Build-mode toggle regains a visible surface at
  page level; MLIP environment rows restyle onto the canonical list-row
  recipe; the modal radius uses `--radius-lg` and off-scale literal radii are
  normalized; dead CSS is removed (`.step-tab*`, `.btn-block`, the unused
  `--destructive` tokens); copy is normalized per the spec (`Could not
  {verb}: …` failures, `TD-DFT`, programmatic plurals instead of
  `calculation(s)`, `loaded (12 atoms)` inline statuses, the question-form
  `Overwrite existing results?` modal title, the colon-free `MLIP ready`
  pill, one single-sourced empty-queue string); failures now land on **both**
  channels (toast + persistent `err` log line) through one shared helper;
  the NMR results table gets the standard scroll wrapper and Results-chart
  ticks/dashes match the chart spec.

### Fixed
- **A pre-launch failure is no longer misdiagnosed from a stale `.out`**
  (PRINCIPLES.md A22). When a run fails before ORCA launches — e.g. geometry
  resolution after a rejected keep-existing result — the failure message now
  keeps the real cause instead of parsing the previous run's `.out` left in
  the folder (which would report the *old* failure, like "SCF did not
  converge", and send the user chasing the wrong problem). The `.out` is
  consulted only once the current attempt has actually launched (or
  reattached to) its ORCA process.
- **Three trust-boundary crashes found by the new test suite**: a
  `settings.json` containing valid JSON that isn't an object (a list, a bare
  string) crashed startup instead of degrading to defaults; a phone-API
  payload with a non-string `basis_assignments` element crashed
  deserialization; and non-string values for string-typed `StepConfig`
  fields (`kind`, `options`, solvation) slipped through deserialization to
  crash later inside `build_input` — all three now degrade to defaults at
  the boundary, with regression tests.
- **Failure verdicts in the Results summary rendered green.** The value
  colorizer's success pattern (`/converged|Normal/i`) re-matched the
  substrings of `NOT converged` and `ABNORMAL`, repainting both failure
  verdicts as success; the checks are now exclusive, failure patterns first.
- **An MLIP job terminated on shutdown no longer logs "left running in the
  background."** MLIP jobs carry no detach/reattach machinery and are
  stopped on shutdown; the log now says so honestly ("stopped on shutdown").
- **An all-MLIP queue can now be started from the phone API — and from the
  desktop UI without an ORCA path.** The `/api/run` endpoint shares the
  desktop's ORCA-needed decision (`queue_needs_orca`, single-sourced in
  `state/store.py`) and passes the registered MLIP environments to the
  engine — previously it unconditionally required a valid ORCA path and MLIP
  calcs launched from the phone could not resolve an interpreter. The
  desktop's own Run button mirrors the same decision instead of always
  demanding an ORCA path, and locked FAILED ORCA calcs (which never launch
  ORCA) no longer count toward needing one. The shared `scf_graph.js`'s
  criterion-series tokens (`--crit-*`) are also defined in the mobile PWA
  now, so its graphs no longer render transparent series.
- **The server status no longer reports "available" when fastapi isn't
  installed** (`get_server_status` now consults
  `ServerController.is_available()` instead of the controller's mere
  existence).
- **TD-DFT excited-state composition is parsed from the right block.** The
  section marker is now case-sensitive (every freq thermochemistry section
  prints a lowercase look-alike, observed in 62 real non-TD-DFT outputs) and
  the **last** occurrence wins, per the parser-wide rule — validated against
  9 real TD-DFT `.out` files.
- **`Settings.save` is atomic** (tmp + `os.replace`, best-effort — the same
  treatment `session.json` already had), so a crash mid-save can no longer
  leave a half-written `settings.json` that loses every setting.
- **`charge`/`multiplicity` from clients are now range-clamped** (to ±100 /
  1–200) at deserialization, so a bogus payload can't emit a nonsense `.inp`
  coordinate header.
- **Escape gaps closed**: the overwrite-conflict name list, the
  remove-confirm calc name, and the queue row's `ref → name` label are now
  HTML-escaped.
- **The numerical-frequency progress no longer flashes "100%"** while the
  final displacement is still running (the displayed percent is floored, so
  the honesty cap actually shows).
- **Keyboard focus is visible** on buttons, tabs, segmented toggles, and
  remove buttons (a `:focus-visible` ring matching the input focus ring);
  previously only text fields showed focus.
- **Theme tints are token-derived.** Status-badge/toggle/error tints come
  from shared `--*-tint` tokens on the §11.19 alpha ladder (the light-theme
  raw-badge retint hack and its mismatched purple base are gone); the SCF
  graph's converged-zone shading re-colors in light theme (was a hardcoded
  dark-theme green); the combo dropdown dropped a stale pre-zinc fallback
  color; a BLOCKED badge now renders styled on desktop (neutral, like the
  other skipped states); the modal name list regains its monospace
  (undefined `var(--mono)` → `--font-mono`); new status pills default to a
  neutral dot instead of red.
- **Mobile PWA token bugs**: two uses of the undefined `var(--fg)`, `--muted`
  (a background tone) used as text color, and a hardcoded log background are
  fixed with the desktop's token names (`--foreground`,
  `--muted-foreground`, `--code-bg`).
- The dead `regenerate_token` method is removed and the token comment now
  states the truth: the phone-sync PIN is generated once per app launch and
  lives for the whole session. Stale docstrings were corrected (name
  uniqueness is enforced by the store, complete calc-kind lists, the MLIP
  package contents, six slots missing from the bridge's slot list), and a
  leftover Korean phrase in the store docstring is now English.

## [0.4.2-beta] — 2026-07-01

### Added
- **The MLIP build card is locked until a MACE environment is ready.** When no
  registered environment reports ready (checking / error / unset), the whole
  "MLIP pre-optimization" card greys out, its inputs and buttons are disabled, and
  a `Ready MACE environment required.` note appears. It unlocks automatically once
  a MACE environment becomes ready, and adding an MLIP job is guarded regardless.

### Changed
- **UI copy overhaul.** Card descriptions, hints, radio explanations,
  status/toast/log messages, and live-graph labels across Build / Queue / Log /
  Results / Settings were rewritten into concise **noun-form** phrasing, with
  redundant filler and restating removed (e.g. the `converged ≤ 1` graph label,
  the duplicated MLIP-card description, "Settings for this calculation.",
  "(e.g. heavy atoms)", "(the original view)"). No behavior change.
- **README** refreshed to match the current app — the three build modes
  (Beginner / Expert / MLIP), the full calc-type list (incl. NEB-TS, IRC, NMR,
  Opt+Freq…), the MLIP pre-optimization workflow, the Settings contents, and the
  `orcamgr/mlip/` package in the project layout.

## [0.4.1-beta] — 2026-06-30

### Added
- **The Results tab now surfaces every value the parser extracts.** In addition
  to the existing summary/spectra it shows the **final geometry** (coordinate
  table + a *Copy .xyz* button), the **full orbital-energy list** (HOMO/LUMO
  highlighted), **Mulliken and Löwdin atomic charges**, **Mayer population**
  (bond orders + per-atom valence), the **dipole moment**, **rotational
  constants**, the **SCF energy decomposition** (nuclear repulsion, electronic,
  one-/two-electron, kinetic, potential, virial ratio), an extended
  **thermochemistry breakdown** (inner energy *U*, enthalpy *H*, entropy term
  *T·S*, temperature, pressure), the **TD-DFT excited-state composition**
  (dominant orbital transitions per state), and the **input echo** (keywords +
  input block).
- **`Show all` toggle** in the Results header reveals every parsed value
  regardless of calculation type; the button is tinted while active.

### Changed
- **Result sections are gated to the relevant calculation type.** The final
  geometry shows only for optimizations; general electronic-structure properties
  (orbitals, charges, Mayer, dipole, rotational constants, SCF decomposition)
  show only for single-point / optimization jobs; freq/TD-DFT/NMR/NEB results
  show only their specialty. The `Show all` toggle overrides this gating.
- The parser (`parse_file`) now also extracts Löwdin charges, Mayer population,
  dipole moment, rotational constants, the SCF energy decomposition, the extended
  thermochemistry quantities, and the TD-DFT excited-state composition. Validated
  against real ORCA 6.1.1 output (sp / opt / freq / TD-DFT / NMR / NEB).
- **Queue cards are tidier:** charge/multiplicity are shown only for ORCA calcs
  (hidden for MLIP, where they are meaningless), the direct-geometry source label
  is simplified from `.xyz` to `xyz`, and the redundant "Completed." note is
  hidden for finished jobs (the `done` badge already conveys it).
- **Rolled back the convergence/SCF graph plot surface to the page tint**
  (`var(--background)`); it had been pure black/white since 0.3.4. The
  window-height fill from 0.3.4 is kept.

### Fixed
- **The top-bar version badge now derives from `APP_VERSION`** instead of a
  hardcoded string, so it can no longer drift from the actual version (the window
  title and About dialog already read `APP_VERSION`). The version is now
  single-sourced — bump only `APP_VERSION` in `orcamgr/paths.py`.
- Removed a duplicated *ZPE* row from the frequency summary.

## [0.4.0-beta] — 2026-06-28

### Fixed
- **The frequency/TD-DFT phase panel no longer lingers through `ts_opt` and
  `irc` runs.** ORCAdesk's `ts_opt` input sets `%geom Calc_Hess true` and
  `irc` sets `InitHess calc_anfreq`, so ORCA computes a full analytical
  Hessian inside optimization cycle 1 / before the IRC walk — the panel
  correctly appeared for that Hessian but then sat stale ("ASSEMBLING SCF
  HESSIAN") for the entire remaining run. A new `GEOMETRY OPTIMIZATION CYCLE`
  / `FORWARD IRC` / `BACKWARD IRC` banner now clears the chain, so the panel
  shows only while a Hessian (or, in raw-input excited-state opts, a TD-DFT
  stage) is actually being computed. Verified against freshly-run ORCA 6.1.1
  `OptTS Calc_Hess` and `IRC` outputs plus a per-kind exclusivity audit over
  263 classified real outputs (opt/opt_freq/ts_opt/ts_freq/irc/freq/tddft/
  nmr/neb_ts/sp): each panel appears only for its own stage, zero violations.
- **NMR runs no longer show the analytical-frequencies panel.** GIAO NMR (and
  polarizability) jobs solve their own CP-SCF equations and print the same
  "ORCA SCF RESPONSE CALCULATION" banner as the Hessian pipeline, which
  falsely activated the frequency phase panel. Shared banners now only
  *advance* an already-active chain; only the Hessian-specific banners
  (derivative integrals, SCF HESSIAN) can activate it. Verified against 286
  real ORCA outputs (60 opt, 38 opt+freq, 22 freq, 9 TD-DFT, NMR/SP/NEB/
  Docker/utility runs): zero false activations, zero missed stages.
- **A finished opt's graph seeded from disk now reads 100% / converged.**
  `get_graph_lines` defined the optimization-finished and post-opt-stage
  patterns but never included matching lines in its filter, so a graph rebuilt
  from the `.out` (reattach, or finished while ORCAdesk was closed) stayed at
  ~99% with no "✓ geometry converged" even for a converged opt.
- **The CP-SCF perturbation total no longer gets overwritten by later property
  solves.** Some runs print several "Number of perturbations" lines (the
  geometric 3N solve first, then smaller IR/EPR property solves); the first
  one now wins, and the Hessian-dimension / mode-count display uses the true
  3N from the atom count.
- **Opt-only runs no longer show the frequency-stage banner.** The freq/post-opt
  stage detectors matched ORCA's section banners case-insensitively, so
  mixed-case lines present in *every* output — the header credits ("pre 5.0
  version of the SCF Hessian") and the end-of-run property echo ("Properties
  with geometric perturbations:", "SCF Hessian ... NO") — falsely switched a
  plain optimization into the "Analytical frequencies" display and appended
  "running frequencies / properties…" to the converged line. The markers are
  now case-sensitive (the real banners are uppercase), verified against real
  opt-only and analytical-frequency ORCA 6.1.1 outputs.

### Added
- **"MLIP ready" status indicator + environment probe (multi-environment).** A
  second status pill in the top bar (next to "ORCA ready") reports whether any
  Machine-Learned Interatomic Potential environment is usable; hovering lists
  each registered environment and the backends it provides. Because ORCAdesk
  does **not** install the MLIP toolchain, the user registers their own Python
  environments under a new *MLIP environments* setting — **one per MLIP**, since
  different MLIPs pin conflicting dependencies (e.g. MACE and SevenNet need
  different `e3nn`) and cannot share a venv. For each, the indicator does an
  honest **import probe** rather than a mere file-exists check: it shells out to
  that interpreter, **auto-detects** which known MLIP backends import (MACE,
  SevenNet; the registry is extensible), and only goes green when the common
  deps (`torch`, `ase`) plus at least one backend actually load — showing the
  detected backend/Python versions, or naming what is missing otherwise. Probes
  run in background threads (importing torch is slow) and the UI polls, so it
  never blocks. New, deliberately ORCA-independent package `orcamgr/mlip/` holds
  the detection logic; new bridge slots `pick_mlip_python` / `add_mlip_env` /
  `remove_mlip_env` / `check_mlip` / `get_mlip_status`. This is the first piece
  of the planned MLIP→ORCA bridge (pre-optimize cheaply with an MLIP, then refine
  with ORCA).
- **MLIP build mode + MACE pre-optimization that actually runs.** The Build tab
  gains a third mode next to Beginner/Expert: **MLIP**. It hides the ORCA build
  form and shows a small dedicated form — pick a MACE model (MACE-OFF for
  organics or MACE-MP-0 for materials, three sizes each), load an `.xyz`, name
  it, and add a `mlip_opt` calculation to the same queue as ORCA jobs. Running
  the queue now executes MLIP jobs end to end: the queue engine shells out to the
  MACE interpreter registered in *MLIP environments*, runs an ASE `LBFGS`
  geometry optimization (CPU) with the chosen model, and streams its progress to
  the live log. The optimized geometry is parsed into the same result shape as an
  ORCA job, so a **downstream ORCA calc can reference an MLIP-optimized geometry**
  — pre-optimize cheaply with MACE, then refine with DFT — through the same
  "reference another calculation" mechanism as an opt→freq handoff (works across
  restarts too). An all-MLIP queue runs without ORCA configured. Validated end to
  end against a real MACE environment (MACE-OFF), including the MLIP→ORCA handoff.
- **JS console messages now land in the Log tab.** The WebEngine page forwards
  `console.*` output (and uncaught front-end errors) into the app log as
  `[web] level=... line=... source=...` lines, so UI failures are diagnosable
  in a deployed build without remote debugging. Identical repeated messages
  are rate-limited (once per 5 s with a suppressed-repeat count), so a JS
  error loop cannot flood the log buffer.

### Changed
- **TD-DFT runs get the same HUD-style phase panel** as analytical
  frequencies, with a 5-dot chain over ORCA's TD-DFT pipeline: XC-kernel
  setup → iterative diagonalization (live `RPA`/`DAVIDSON` label and
  iteration counter) → excited-state analysis → transition spectra → final
  CIS/TD-DFT total energy (banner order verified against 7 real ORCA 6.1.1
  TD-DFT outputs, both full TD-DFT and TDA). The panel center shows the
  requested root count.
- **The analytical-frequencies stage is now a HUD-style phase panel** —
  hazard-striped border, centered "ANALYTICAL FREQUENCIES" title, a `PHASE k/7`
  label, and a 7-dot phase chain (done = filled, current = pulsing) tracking
  ORCA's real Hessian pipeline: derivative integrals → CP-SCF response →
  Hessian assembly → frequencies → normal modes → IR spectrum →
  thermochemistry (banner order verified against 4 real ORCA 6.1.1 freq
  outputs). The panel center shows the stage's headline number (atom count,
  K/N perturbations with CP-SCF iteration, 3N×3N Hessian dimension, mode
  count, temperature) and the bottom status line names the running stage;
  `ORCA TERMINATED NORMALLY` completes the chain. Numerical frequencies keep
  the displacement progress bar.
- **The "~N s / SCF cycle" pace indicator moved from the Log-tab toolbar into
  the SCF panel's progress meta line** (right-aligned on the same row as the
  criteria/"✓ geometry converged" status), where the rest of the run pacing
  info lives.
- **Bridge/API payloads now have a single source of truth:**
  `orcamgr/state/schemas.py` defines TypedDicts for every payload crossing the
  QWebChannel bridge and the phone HTTP API (settings, log, queue snapshots,
  parse results, server status, ok/error envelopes), and `bridge.py` /
  `server/app.py` / `store.py` build their responses through them instead of
  ad-hoc dicts. The JSON wire format is byte-identical (verified against 40
  captured payloads); `web/types.js` cross-references it as the JS mirror.
- **The web front-end is now type-checked** (`// @ts-check` + JSDoc against
  `jsconfig.json`; payload typedefs in `web/types.js` mirror the Python
  serialization layer). Typing surfaced one real bug, now fixed: a failed
  settings save returned `{"error": ...}` and silently replaced the in-memory
  settings mirror; it now shows a toast and keeps the previous settings.
- **`QueueStore` moved from `orcamgr/server/store.py` to `orcamgr/state/store.py`.**
  The store is the single source of truth shared by the desktop Bridge *and* the
  phone-sync HTTP server, so living under `server/` misrepresented the dependency
  direction (now `gui -> state <- server`). The old import path still works through
  a deprecation shim.

## [0.3.4-beta] — 2026-06-11

### Changed
- **The convergence/SCF graph now fills the window height.** The viewBox height
  is computed per-render from the space left below the plot (and follows window
  resizes), so the graph ends flush with the bottom gutter instead of leaving a
  large empty band under a fixed-ratio chart.
- **The graph plot sits on a pure black/white surface** (`#000000` dark,
  `#FFFFFF` light) instead of the page tint, so the chart pops from the panel.

## [0.3.3-beta] — 2026-06-11

### Changed
- **The convergence/SCF graph now fills its box and is larger.** It was sized to
  the viewport height, so its width was fixed and left empty space on the right
  of the (wide) graph box. It now fills the box width on a flatter viewBox
  (1100×360), so it's bigger and the leftover space is gone, while still fitting
  without scrolling.

## [0.3.2-beta] — 2026-06-10

Light-theme layering and uniform spacing.

### Changed
- **Light theme inverted: ivory page (`#FFFFF0`) with slightly-darker beige
  cards (`#F5F5DC`)**, so boxes read as raised panels on a pale page (was the
  other way around).
- **The convergence/SCF graph now sits in its own nested box** — ivory in light
  (lighter than the beige panel), a darker inset in dark — so the chart has its
  own surface.
- **Uniform 16 px spacing**: the gap between boxes and the gap from a box to the
  window edge are now both 16 px, and the top bar, tabs and cards all align to
  the same 16 px gutter (the page gutter was 20 px while cards sat 16 px apart).

## [0.3.1-beta] — 2026-06-10

Light-theme warmth and graph/log sizing.

### Changed
- **Light theme is now warm, not stark white**: a beige page background
  (`#F5F5DC`) with ivory cards/inputs/popovers (`#FFFFF0`) and beige-tinted fills
  and borders, instead of white-on-white.
- **The "raw" badge is teal in light mode** (was purple, which clashed with the
  warm palette).
- **The convergence graph is sized to the viewport height** instead of a fixed
  920 px width cap, so it's much larger on a wide window while still always
  fitting without scrolling; the raw-log box reclaims the vertical space freed by
  dropping the old "Live output" header card.

### Fixed
- **The ORCA-status dot now turns green when ORCA is ready.** The "ready" class
  was already applied, but a more-specific `#orca-status .dot` rule overrode the
  `.ok` colour and kept the dot grey — added an `#id`-level ready rule.

## [0.3.0-beta] — 2026-06-10

Drag-and-drop, live frequency progress, and UI polish.

### Added
- **Drag & drop files from Explorer.** Drop a `.inp` onto the window to load it
  into the Build editor (the calculation name auto-fills from the filename), a
  `.xyz` to set the Build geometry, or a `.out` to open it in Results. Routed by
  extension; `.out` is parsed by path, so even multi-hundred-MB outputs stay off
  the JS heap.
- **Live frequency progress** in the graph panel:
  - *Numerical* frequencies — a displacement counter (K / 6N) with a reliable
    ETA (the total is known up front, unlike an optimization).
  - *Analytical* frequencies (CP-SCF) — a real progress bar driven by the
    coupled-perturbed-SCF "K / N perturbations done" counter (N = 3·atoms), with
    a stage label for the derivative-integral build. Verified against a 58-atom
    M06-2X/CPCM run.
- **View the input of any queued job — including a RUNNING one** — via a ".inp"
  button that shows the on-disk input read-only.

### Changed
- **An optimization flips to 100% the moment ORCA reports it finished**
  (`*** OPTIMIZATION RUN DONE ***`) instead of being stuck at 99% when the last
  criteria table reads e.g. 4/5 met, and it announces the next stage.
- **The optimization ETA is shown as an order-of-magnitude bucket** ("a few
  minutes" / "tens of minutes" / …) with the accurate signals (progress, step,
  criteria met, measured per-step rate) up front and the uncertain time estimate
  visually subordinate — the cycle count is irreducibly ~2× uncertain, so this
  avoids false precision.
- **Log tab layout.** Dropped the "Live output" header card, moved **Clear** to
  the top-right of the toggle row, and capped the convergence-graph width so it
  no longer grows to ~750 px tall and forces scrolling. The "converged ≤ 1" label
  moved to the left so the descending curves don't cover it.

### Fixed
- **Design consistency: badges/chips reused outside their original parent now
  render correctly.** The pill/box styling for `.qstate` (the Build "raw" tag),
  `.qerror` (the Build NEB atom-mismatch warning) and `.rm` (remove buttons) was
  scoped to one ancestor (`.queue-item …`), so the same class used elsewhere
  rendered unstyled. Hoisted the shared look to ancestor-free base rules and
  removed dead `.atom-row` styles.

## [0.2.1-beta] — 2026-06-10

Optimization ETA accuracy + honesty, tuned against 85 real ORCA opt runs.

### Changed
- **Per-cycle time now comes from ORCA's own `Time for complete geometry iter`
  timing** (steady median, excluding the one-time-expensive first cycle) instead
  of wall-clock poll gaps — eliminating UI-jitter / log-replay artifacts. On the
  85 runs this time model is accurate to ~8% (median) given the cycle count.
- **The ETA is shown as an honest range (≈[0.5×, 2×])** rather than a single
  number. Geometry-optimization cycle counts are intrinsically hard to predict —
  verified ~65% median error across heuristic *and* regression models on the same
  runs, because convergence has a long, unpredictable tail near the tolerance —
  so the estimate is presented as a calibrated band, not false precision.

### Notes
- Other methods (frequencies, scans, NEB) were evaluated but the available data
  showed no step-predictable structure to model honestly (DFT frequencies are a
  single analytical Hessian), so step-based ETA remains opt-only.

## [0.2.0-beta] — 2026-06-10

Theming release.

### Added
- **Light theme.** A full shadcn-zinc light palette alongside the existing dark
  theme, toggled from a ☀/☽ button in the top bar and remembered across launches.
  Every element themes through CSS variables; the optimization-graph series and
  the "raw" badge get darker, legible variants on the light card.

## [0.1.2-beta] — 2026-06-10

Survive-close + reliability release. A running calculation now keeps going when
ORCAdesk is closed and is re-attached on the next launch, plus a batch of
editing/robustness fixes.

### Added
- **Run survives closing the app.** ORCA is launched detached and writes its own
  `.out`; closing ORCAdesk no longer kills the running job — it is left running
  and reattached on the next launch.
- **Session restore.** The queue autosaves and is restored on startup; a job that
  finished while closed is reconciled from its `.out` (done/failed), and a still-
  running one is reattached live (the SCF/optimization graph history is rebuilt
  from the `.out`).
- **"Stop after current"** — finish the running job, then stop, leaving the rest
  pending (vs. Cancel, which kills the running job).
- **Editable raw calculations after queueing** — a raw `.inp` calc restored from a
  previous session or added from the phone can now be edited (full data is fetched
  on demand), not only same-session ones.
- **Log: jump-to-latest.** Scrolling up no longer yanks you back down; a "↓ Latest"
  button appears bottom-right when scrolled up.
- **Average "s / SCF cycle"** indicator on the Log tab.
- **Expert mode:** loading a complete `.inp` auto-fills the calculation name from
  the file name.

### Changed
- **Confirmation dialogs are themed** (no more system pop-ups) and irreversible
  actions — Cancel, Clear all, Remove, switch-to-raw — now confirm first.
- Shorter default raw-log box so the Log panel fits without an outer scroll.
- Cancellation hardened: psutil process-tree kill with confirm + escalation,
  bounded waits, and a centralized idempotent shutdown.

### Fixed
- Optimization-graph history is no longer truncated after a close/reopen of a long
  job (reattach tails from the current EOF; the graph is rebuilt from the `.out`).
- Startup no longer re-parses every finished `.out` on the UI thread (parsed on
  demand instead), avoiding a cold-start stall with a large restored queue.

## [0.1.1-beta] — 2026-06-07

Correctness and robustness release. Focus: ORCA input that this build actually
accepts, safer cancellation/shutdown, and a clearer optimization graph.

### Added
- **Composite calc kinds** `Opt + Freq` and `TS Opt + Freq` — one ORCA run that
  optimizes then runs frequencies, validated for convergence *and* the
  imaginary-frequency count (0 for a minimum, 1 for a TS).
- **Optimization graph: all five convergence criteria.** Each criterion is
  plotted as value ÷ its own tolerance, so they share a single goal line at 1
  (below the line = met). A Settings toggle switches between this and the
  original MAX-gradient-only view.
- **Build tab: Beginner / Expert modes.** Beginner is the guided form (and can
  now also load a complete `.inp` directly); Expert is a paste/load-a-full-`.inp`
  view where you only pick the calc kind (for parsing), with `{{GEOMETRY}}` +
  reference still supported. The chosen mode is remembered.
- Opt-in WebEngine remote debugging via `ORCADESK_REMOTE_DEBUG` (diagnostics).

### Changed
- **ORCA functional/basis compatibility.** Strict-name normalization on the
  keyword line (`M06-2X`→`M062X`, `M06-L`→`M06L`, `SCAN`→`SCANfunc`); combined
  dispersion tokens rewritten to the explicit separate keyword (`B3LYP-D3` →
  `B3LYP D3BJ`); `RIJK` gets a `/JK` aux and double hybrids/MP2 get `AutoAux`
  (skipped when RI is off). Functional/basis lists were validated against ORCA
  6.1.1 and entries this build rejects were removed.
- Optimization-graph step count is keyed to the real ORCA cycle number.

### Fixed
- **Cancellation/shutdown:** cancel now kills the whole ORCA process tree (no
  orphaned MPI workers); a cancelled calc is marked CANCELLED (not FAILED) and
  no longer blocks its dependents; closing the app waits for ORCA to stop so it
  doesn't orphan `orca.exe` or leave a half-written `.out`.
- **NEB-TS:** product/TS-guess side files are written in raw mode too,
  reactant/product atom order is checked before launch, and the result is
  validated (one imaginary mode) when frequencies were computed.
- **UI:** NEB-TS product `.xyz` loading (was JSON-parsing raw text and failing);
  "Reference another calculation" can enter raw mode without first picking a
  reference; per-element basis/ECP values are HTML-escaped on edit; the opt-ETA
  no longer flashes "~0s" after a burst of replayed log lines; log/graph
  repaints are skipped while the window is hidden.
- **Input validation / phone-sync (in development):** untrusted numeric fields
  are coerced/clamped; calc names are validated against path traversal and
  reserved names at the shared layer; the queue can't be edited while running;
  the loopback auth-bypass is honoured only when bound to loopback.

## [0.1.0-beta] — 2026-06-03

First beta of the **desktop** app. Core workflow (build → queue → run → parse)
is complete and has been validated against real ORCA 6.1.1 output. Phone-sync
is in active development by a contributor and is **not** part of this build.

### Added

**Building calculations**
- Visual form for opt, TS-opt, freq, TS-freq, TD-DFT, NMR, single-point, and a
  general free-form mode, each emitting ORCA 6.1.1-correct input.
- Searchable comboboxes for functional, basis set, and solvent: type to filter,
  grouped by level, and any value not in the list is accepted verbatim (so the
  full LibXC functional space and any custom basis are reachable).
- Per-element basis/ECP assignments, solvation (CPCM/SMD), SCF and RI options,
  and adjustable nprocs / maxcore.
- Raw-input mode for hand-written ORCA decks.

**Queue and execution**
- Drag-to-reorder queue with per-calculation folders.
- Geometry hand-off between steps (e.g. opt → freq reuses the optimized
  geometry); dependent steps are blocked automatically if a parent fails.
- Live streaming log with cancel support.

**Live convergence graphs**
- SCF convergence graph (|ΔE| per cycle, log scale) with axis labels and cycle
  ticks.
- Geometry-optimization graph (MAX gradient vs step) with a progress bar and a
  criteria-met counter; progress is capped at 99 % until all five convergence
  criteria are actually satisfied.
- **Optimization time estimate (ETA):** a research-tuned, non-linear estimator
  predicts remaining steps and time during a geometry optimization. It uses a
  per-file-normalized "worst-ratio" of the five convergence criteria, an
  ensemble of four predictors with agreement gating, and temporal smoothing, so
  it shows "estimating…" rather than a wrong number when the trajectory is
  erratic. Selectable **Conservative** (predict only when confident) or
  **Eager** (predict earlier, hold the estimate) mode in Settings.

**Results**
- Parsed energies, geometry, frequencies, and TD-DFT transitions, with a simple
  UV-Vis spectrum and an NMR summary view. Finished jobs load into Results
  automatically.

**Desktop app**
- Standalone Windows build (PyInstaller) and an Inno Setup installer, with a
  custom application icon.
- First-launch prompt to choose where calculation files are stored.

### Fixed
- NMR J-coupling input now emits valid `Nuclei … { shift, ssall }` blocks and
  places `%eprnmr` after the coordinates.
- TS-opt now requests an initial Hessian (`Calc_Hess true`).
- Solvation no longer emits empty `CPCM()` / `SMD()` when no solvent is chosen.
- Auxiliary basis (`def2/J`) is added automatically only when appropriate.
- Long runs no longer lag: the log DOM is capped and graph redraws are
  throttled.
- ETA mode is now persisted correctly through the desktop settings bridge.

### Known limitations
- ETA is meaningful only for geometry optimizations. Other calculation types
  (analytic frequencies, single point, TD-DFT, NTO) do not expose a usable
  progress signal, so no ETA is shown for them.
- ETA accuracy is inherently limited for difficult or erratic optimizations
  (transition-metal complexes, large flexible molecules); the estimate is
  labelled "rough" and may stay at "estimating…".
- Phone-sync (controlling the queue from a phone) is in development and not
  included in this build.
- Windows is the primary tested target for the packaged app.
