# Changelog

All notable changes to ORCAdesk are documented here.
This project loosely follows [Semantic Versioning](https://semver.org/).

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
