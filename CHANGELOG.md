# Changelog

All notable changes to ORCAdesk are documented here.
This project loosely follows [Semantic Versioning](https://semver.org/).

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
