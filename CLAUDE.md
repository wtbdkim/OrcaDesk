# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ORCAdesk is a desktop GUI (PyQt6 + QWebEngine) for building, queuing, running, and
parsing [ORCA](https://www.faccts.de/orca/) computational-chemistry jobs. The app
shells out to the user's installed `orca` executable; it does not do the chemistry
itself. Status is beta; the current version is `APP_VERSION` in `orcamgr/paths.py`
(also the top entry of `CHANGELOG.md`). Windows is the primary tested target.

Two normative companion documents govern how this codebase is written:
**`PRINCIPLES.md`** (development principles, cited as `P1`, `P10`, …) and
**`DESIGN.md`** (visual/UX principles + the design-token reference, cited as
`D1`, `D10`, …). They are binding: follow them when writing code or UI, and
when a change introduces or amends a principle, update the relevant document
in the same commit; a deliberate deviation is justified in the commit body
and recorded in the appendices (with a disposition) if it is kept. This file
stays the operational guide;
where they overlap, the principles documents are the norm.

## Commands

```bash
# Develop (desktop app)
pip install -r requirements.txt        # PyQt6 + PyQt6-WebEngine + psutil
python main.py

# Optional phone-sync server, standalone (for API testing on localhost)
pip install -r requirements-server.txt # fastapi, uvicorn, qrcode, pillow
python -m orcamgr.server.run           # http://127.0.0.1:8000/docs for API docs

# Build a standalone Windows app -> dist\ORCAdesk\ORCAdesk.exe (+ runtime folder)
build.bat                              # installs deps + PyInstaller, then runs:
python -m PyInstaller build.spec --noconfirm

# Type-check the web/ front-end (plain JS + JSDoc, no build step; needs Node)
npx -p typescript tsc --noEmit -p jsconfig.json

# Run the automated test suite (pip install -r requirements-dev.txt once)
python -m pytest                       # 273 tests over the framework-free layers
node tests/web/scf_graph.test.js       # 27 tracker/progress tests, no npm deps
```

The **automated test suite** (`tests/`, pytest + one plain-Node script) covers the
framework-free layers: `state/` (store, schemas, session persistence), `core/`
(queue-engine semantics with a fake runner, per-kind validation, input generator,
parser on synthetic fixtures), `mlip/` (with a stdlib-only stub worker — no MACE
env needed), `crest/` (ensemble parser against a real ethanol corpus in
`tests/crest/fixtures/`, CLI-flag building, and the QueueEngine path via a fake
runner — no WSL/CREST needed), `config`, `procutil` (real child processes), and
the phone HTTP API via `fastapi.testclient` (auto-skips without fastapi). The Qt bridge/window layers
are thin adapters and are exercised manually. Tests that read the real ORCA output
corpus auto-skip when the corpus directory is absent, so the suite is green on any
machine. There is no linter configured. Parser/input-generator *evidence* still
comes from manual validation against real ORCA 6.1.1 output (P3); the
`orcamgr/server/STAGE*_TEST_KR.md` files are manual server-test checklists
(Korean), not runnable tests.

## Architecture

### UI is HTML/JS, backend is Python, glued by a QWebChannel

The entire UI lives in `web/` (HTML/CSS/JS, shadcn-style dark theme). `main.py` opens
`MainWindow` (`orcamgr/gui/window.py`), which hosts a `QWebEngineView` loading
`web/index.html` and registers a single `Bridge` object on a `QWebChannel`.

The view's page is `_ConsoleCapturePage` (`window.py`), which forwards JS console
output into the shared log buffer as `[web] ...` lines (rate-limited per identical
message) — so front-end errors are visible in the Log tab even in a deployed build;
`ORCADESK_REMOTE_DEBUG` (handled in `main.py`) remains the dev-time tool.

`orcamgr/gui/bridge.py` is the **entire backend API surface for the desktop**: every
`@pyqtSlot` is callable from JS (the slot list is documented at the top of `bridge.py`).
Slots take/return JSON strings. The JS side does not hold queue state — it **polls**
`get_queue()` / `get_log(since)` on a `QTimer`. This polling design is deliberate: it
keeps the run worker thread and Qt's UI thread decoupled, avoiding cross-thread Qt
signal juggling. If you add backend functionality, it goes through a new Bridge slot.

### One shared QueueStore is the single source of truth

`orcamgr/state/store.py` `QueueStore` holds the queue (a list of `Calculation`),
the run flag, and the log buffer, guarded by a `threading.RLock`. The **same store
instance** is shared by the desktop Bridge and the FastAPI server (constructed once in
`window.py` and passed to both), so the desktop and a connected phone always see one
queue. `QueueStore` is intentionally free of PyQt and FastAPI imports so it stays
unit-testable in isolation. It lives in `orcamgr/state/` (not `server/`) so the
dependency direction is explicit: `gui -> state <- server`. The old location
`orcamgr/server/store.py` is a deprecated re-export shim (emits a
`DeprecationWarning`); new code must import `orcamgr.state.store`.

`store.py` also owns the **shared serialization layer** — `calc_from_dict` /
`calc_to_dict` / `StepConfig` round-tripping and `load_*_choices` (reading
`data/*.json`). Both the Bridge and the HTTP server build `Calculation` objects through
the same `calc_from_dict`, so desktop and phone produce identical inputs.

`orcamgr/state/schemas.py` is the **single source of truth for the wire payloads**:
TypedDicts for everything the bridge and the HTTP API send that isn't the
Calculation/StepConfig serialization itself (settings, log lines, queue snapshots,
parse results, server status, the `{"ok": ...}`/`{"error": ...}` envelopes).
`bridge.py`, `server/app.py`, and `store.py` construct responses through these types
(plain dicts at runtime — wire format unchanged), and `web/types.js` mirrors them for
the front-end. Don't annotate FastAPI endpoints with these TypedDicts as return types
— FastAPI would infer a response_model and put pydantic between the dict and the wire;
endpoints keep `-> dict` and only *construct* through the schema types.

The **Results tab** is purely presentational over `ParsePayload`: `bridge._parse_path`
sends the *whole* `ParseResult` (every section the parser found) plus two gating flags
— `is_optimization` and `show_elec` (`= ParseResult.shows_electronic_props`) — and the
front-end (`web/app.js` `renderResultSections` / `renderSummary`) decides what to show
per calc kind. Final geometry shows only for opt jobs; general electronic-structure
sections (orbitals, charges, Mayer, dipole, rotational, SCF decomposition, and the
`"elec"`-tagged summary rows) show only for sp/opt; freq/tddft/nmr/neb sections are
present-only. The Results header's **`Show all`** toggle (`showAllResults` in `app.js`)
overrides the gating to reveal everything parsed. Keep the gating on the front-end (not
the payload) so the toggle re-renders without a re-fetch.

### Running the queue: core/ is GUI-agnostic

A run is started via `QueueStore.start_run(engine_factory)`, which spins a daemon
thread and calls `QueueEngine.run_all()` (`orcamgr/core/queue.py`). The engine talks to
the rest of the app only through `QueueCallbacks` (`log`, `calc_update`) — it has no
knowledge of Qt or HTTP. `make_engine_factory` wires those callbacks back to the store
(`log -> append_log`, `calc_update -> touch` bumps the version).

The `core/` pipeline:
- `input_generator.py` — `StepConfig` → ORCA `.inp` text (`build_input`). Handles
  solvation (CPCM/SMD), RI, per-element basis/ECP, charge/multiplicity, raw-input mode
  (verbatim text with a `{{GEOMETRY}}` placeholder), and NEB-TS side `.xyz` files.
- `runner.py` — `OrcaRunner` launches ORCA **detached**, with ORCA writing its own
  stdout straight to the `.out` file (not via a Python pipe), so a run survives
  ORCAdesk closing. Live log/progress come from **tailing** the `.out`. Verbs:
  `launch()` (returns `(pid, create_time)` to persist), `adopt()` (reattach to a
  process from a previous session), `monitor()` (tail until exit/cancel/detach),
  `cancel()` (kill the tree), `detach()` (stop monitoring, leave ORCA running).
  Process identity + tree-kill go through `procutil.py` (psutil), which guards
  against PID reuse via `create_time`.
- `procutil.py` — psutil-backed `process_matches(pid, create_time)` and
  `kill_tree(...)`, used for reattach and reliable tree termination.
- `queue.py` — `QueueEngine` orchestrates the pipeline (details below). Validation
  is the module-level `validate_result()` (shared by the engine and session
  reconciliation). Cancel verbs: hard `cancel()`, graceful `request_stop_after_current()`
  (drain), and `detach()` (shutdown — leave the running job alive).
- `parser.py` — `parse_file()` → `ParseResult` (energies + SCF energy
  decomposition, geometry, orbitals/HOMO-LUMO, Mulliken & Löwdin charges, Mayer
  population (bond orders + valences), dipole moment, rotational constants,
  frequencies, full thermochemistry (U/H/G/ZPE/T·S/temp/pressure), TD-DFT
  transitions + excited-state composition, NMR, NEB path). Marker-based,
  tolerant of `\r\n`; when a value recurs (e.g. across opt steps) the **last**
  occurrence wins. `ParseResult.summary_rows()` returns `(label, value, category)`
  rows where `category="elec"` tags general electronic-structure rows; the
  `shows_electronic_props` property (`is_optimization or not specialty`) decides
  which sections the Results tab shows per kind — see the Results-gating note.

### Queue semantics (important invariants)

These rules live in `QueueEngine.run_all` / `validate_result` and `QueueStore`:
- **Calculation `name` is unique and is used as the on-disk folder name**
  (`{workspace}/{name}/`). Uniqueness is enforced in the store.
- **The queue autosaves to `%APPDATA%\ORCAdesk\session.json`** on every mutation
  (`QueueStore._bump_and_save`) and is restored on startup (`load_session`). A
  `RUNNING` calc persists its detached ORCA's `(pid, create_time)`. On the next
  launch, `reconcile_calcs` checks that identity: still alive → stays `RUNNING`
  and is **reattached** (`OrcaRunner.adopt` + `monitor`, continuing the queue);
  gone → judged from its `.out` (`DONE` if terminated normally + valid, else
  `FAILED`). Closing ORCAdesk does **not** kill the running job — `shutdown()`
  calls `store.pause_run()` (engine `detach()`), not cancel.
- **Geometry source** is `DIRECT` (coords supplied, e.g. from `.xyz`) or `REFERENCE`
  (another queued calc by name). For a reference, the engine injects that calc's
  **optimized final geometry** at run time — so opt → freq reuses the optimized
  structure automatically.
- **Failure propagation is dependency-scoped, not whole-queue.** If a calc fails,
  every calc that references it (transitively) is marked `BLOCKED` and skipped;
  unrelated calcs continue.
- **`DONE` calcs are never recomputed** on a re-run (the result is frozen);
  **`FAILED` calcs are locked** at the moment of failure (P24): no re-run,
  no edit, no reorder — a re-run skips them and blocks their dependents up
  front; retrying means building a new calculation. Their remaining
  interactions are read-only diagnosis and × removal, which is
  queue-list-only — workspace folders are never deleted (no file-deletion
  code exists; a binding rule). Only `CANCELLED` (a deliberate user stop)
  re-runs. `EDITABLE_STATES` is `PENDING`/`CANCELLED`/`BLOCKED` — BLOCKED
  stays editable so a dependent can be re-pointed after its failed parent
  is removed. The keep-existing ("skip") run option parses the kept output
  through `result_from_output` and stamps `DONE` only if `validate_result`
  passes; otherwise the calc runs.
- **Result validation is per-kind**: `opt`/`ts_opt` (and the combined
  `opt_freq`/`ts_opt_freq`) must converge; `freq`/`opt_freq` must have zero
  imaginary frequencies; `ts_freq`/`ts_opt_freq` must have exactly one;
  `neb_ts` must have exactly one when frequencies were computed. A
  validation failure marks the calc `FAILED`. Calc kinds: `opt`, `ts_opt`,
  `freq`, `ts_freq`, `opt_freq`, `ts_opt_freq`, `irc`, `tddft`, `sp`,
  `general` (free keyword combination; no per-kind validation), `nmr`,
  `neb_ts`, `mlip_opt`, `crest_conf`. Kinds starting with `mlip` or `crest` run
  **outside** the ORCA pipeline: `QueueEngine.run_all` routes `mlip*` to
  `_run_mlip_calc` (a MACE relaxation in the user's env) and `crest*` to
  `_run_crest_calc` (a conformer search in WSL) instead of `_run_calc`.
  `crest_conf` validation requires normal termination + at least one conformer.
  See the MLIP and CREST sections.

### Optional phone-sync server

`orcamgr/server/` is a thin FastAPI layer (`app.py`) over `QueueStore`, started/stopped
from the desktop by `ServerController` (`controller.py`) running uvicorn in a daemon
thread on the shared store. It serves the mobile PWA from `web_mobile/` at `/` and the
queue API under `/api/`. fastapi/uvicorn are **optional** — `ServerController.is_available()`
gates the whole feature, and the desktop app works fine without them. Per `CHANGELOG.md`
phone-sync is in development and **not part of the packaged build**.

### MLIP environment (deliberately separate from ORCA)

`orcamgr/mlip/` is a dedicated package, kept **out of** the ORCA pipeline in
`core/` on purpose: a Machine-Learned Interatomic Potential is a separate Python
toolchain (PyTorch + mace-torch + ASE) that ORCAdesk shells out to the same way
it shells out to the ORCA executable — it never installs that toolchain.

**One environment per MLIP.** The user registers their own Python environments
in `Settings.mlip_envs` (a list of `{id, name, python}`), not a single path —
because different MLIPs pin conflicting dependencies (MACE and SevenNet pin
different `e3nn`), so they cannot share a venv. The legacy single
`mlip_python` setting is auto-migrated to one env entry on load.

The package holds `env.py` (environment detection backing the "MLIP ready"
top-bar indicator) plus the run pipeline (`runner.py`, `parser.py` — see
"Running MLIP jobs" below). Unlike `orca_is_valid()` (a file-exists check),
MLIP readiness is an **import probe**: `probe_env()` runs each registered
interpreter and **auto-detects** which backends import. The backend registry is
`MLIP_BACKENDS` (key → import-package name; MACE/SevenNet seeded, extensible);
an env is `ready` only when the common deps `COMMON_PACKAGES` (`torch`, `ase`)
*and* at least one backend import — so the indicator is honest about the common
"I pip-installed it myself but the env is incomplete" case. Probes are slow
(importing torch), so each runs in a background thread on the Bridge and the UI
polls `get_mlip_status()`. Bridge slots: `pick_mlip_python` (picker),
`add_mlip_env` / `remove_mlip_env` (manage the list, each persists + probes),
`check_mlip(id)` (re-probe one env, or all when id is `""`), `get_mlip_status`
(aggregate). Per-env live state lives in `Bridge._mlip_envs_status` (keyed by
env id, guarded by `Bridge._mlip_lock`); MLIP is **not** routed through
`save_settings` (its own channel). The wire shapes are `MlipBackend` /
`MlipEnvPayload` / `MlipStatusPayload` in `state/schemas.py` — the aggregate
`MlipStatusPayload` is `{state, envs[]}` where the top-bar `state` is `ready` if
any env is ready, else `checking`/`error`/`unset` — built by `aggregate_status`
and mirrored in `web/types.js`.

**Building MLIP jobs.** The Build tab has a third mode besides `beginner`/`expert`
— `mlip` (`Settings.build_mode`; the three-way toggle is `#bmode-*` in
`index.html`, `setBuildMode` in `app.js`). MLIP mode hides the whole ORCA build
UI (`_ORCA_BUILD`) and shows a self-contained `#card-mlip`: a name, a MACE-model
dropdown (options in `data/mace_models.json`, served via `load_choices`), and a
**geometry source** selector (`.xyz` loader **or** reference another queued calc,
mirroring the ORCA build card — `onMlipGeomSourceChange`/`refreshMlipRefSelect` in
`app.js`), which add a `mlip_opt` calc to the **shared queue** through the
same `add_calc`/`calc_from_dict` path as ORCA calcs. A referenced `mlip_opt`
resolves through the very same `_resolve_geometry` path as an opt→freq handoff, so
an MLIP pre-optimization can start from another calc's optimized geometry (e.g. a
CREST best conformer). The model lives on
`StepConfig.mlip_model` (+ `mlip_env_id`, `""` = first ready env); `build_input`
ignores those — an MLIP calc never produces an ORCA `.inp`. `_meta_line` shows
the model instead of charge/mult for `mlip*` kinds, and `editCalc` refuses
in-place editing of MLIP calcs for now (remove + re-add). The card is **locked**
(greyed, inputs/buttons disabled, `#mlip-lock-note` shown) until some MLIP env is
ready — `applyMlipLock` in `app.js`, driven by the `get_mlip_status` poll
(`_mlipReady`); `addMlipCalcToQueue` guards on it too.

**Running MLIP jobs.** The run pipeline mirrors `core/` but lives in
`orcamgr/mlip/` (kept off ORCA's path): `runner.py` (`MlipRunner` +
`write_mlip_run_files` + the `MACE_WORKER_SCRIPT`) and `parser.py`
(`parse_mlip_result`). `QueueEngine._run_mlip_calc` resolves geometry (direct or
reference) and the interpreter (`config.mlip_env_id`, else the first registered
env, via `_resolve_mlip_python`), writes an input `.xyz` + a JSON config + the
worker script into the run folder, then runs the user's interpreter on it. The
worker — running in the **user's** env, so it may import `torch`/`mace`/`ase`
(ORCAdesk's env need not) — loads a MACE calculator (`mace_off`/`mace_mp` by
model+size from `parse_mace_model`), runs an ASE `LBFGS` relaxation (CPU,
fmax 0.05), and writes the optimized geometry + energy + convergence to a JSON
result; `MlipRunner` tails its stdout into the `.out` and the live log and is
cancellable (`QueueEngine.cancel`/`detach` forward to the active `MlipRunner`).
`parse_mlip_result` reads that JSON into the **shared `ParseResult`** (geometry,
`final_energy_eh`, `opt_converged`), so a downstream ORCA calc references an
MLIP-optimized geometry through the **same** `_resolve_geometry` path an opt→freq
handoff uses. `validate_result` checks `mlip_opt` convergence; `_failure_reason`
skips the ORCA `.out` parse for mlip kinds. The engine gets the env list via
`make_engine_factory(..., mlip_envs=settings.mlip_envs)`; a valid ORCA path is
required only when a non-MLIP calc will actually run — that decision is the
shared `queue_needs_orca` (`state/store.py`), used by **both** run entry points
(the desktop bridge's `run_queue` and the phone's `/api/run`), so an all-MLIP
queue runs without ORCA from either client. Output parsing dispatches on kind through the module-level
`result_from_output` (used by reference resolution and session reconciliation),
so a DONE `mlip_opt` referenced **after a restart** is parsed by the MLIP parser,
not the ORCA one. The pipeline was validated end to end against a real MACE
environment (MACE-OFF), including the MLIP→ORCA geometry handoff; those
validation runs were performed locally — there is no checked-in test suite
or CI (see the Commands section).

### CREST environment (a WSL-backed third backend)

`orcamgr/crest/` is a dedicated package, kept **out of** the ORCA pipeline in
`core/` like `mlip/`. CREST is a conformer-sampling tool with **no native
Windows build**, so ORCAdesk runs its statically-linked Linux binary through
**WSL** — it never installs a chemistry toolchain, it shells out (here, into a
distro). v1 scope is conformer search only (`crest_conf`).

The package: `wsl.py` (low-level `wsl.exe` helpers — distro enumeration with
infrastructure distros like `docker-desktop` filtered out, `WSL_UTF8=1`, always
`-d <distro>` explicitly), `env.py` (probe each distro for the `crest` binary,
backing the "CREST ready" top-bar indicator; `aggregate_status` mirrors the MLIP
four-state pill), `installer.py` (download the static release tarball into a
distro + symlink — fully scriptable, so ORCAdesk auto-installs CREST; the one
manual prerequisite is a WSL distro existing), plus the run pipeline
(`runner.py`, `parser.py`).

**Running CREST jobs** mirrors the *ORCA* runner (not the MLIP one) because a
CREST run is long and must survive ORCAdesk closing. `QueueEngine._run_crest_calc`
resolves the distro (`config.crest_env_id`, else `settings.crest_distro`, else
the first distro with CREST — persisted back onto `config.crest_env_id` so a
reattach knows where the job runs) and writes a self-contained `run_crest.sh`
into the Windows workspace folder. `CrestRunner.launch` starts it **detached**
(`setsid`) in WSL: the script records its own Linux pid + start-time, runs CREST
in an **ext4 scratch dir** (`~/.orcadesk/scratch/<name>`, never `/mnt` — 9P is
5–300× slower and CREST is I/O-heavy), redirects CREST's stdout to the Windows
`<name>.out` for live tailing, then copies the ensemble results back and writes a
`<name>.crest.rc` marker. The launcher **waits (inside WSL) until the `.pid`
appears** before returning — without that, `wsl.exe` closes the pty before
`setsid` finishes detaching and the child is killed (a real bug found by
end-to-end testing). Because it's a true detach, `monitor` tails the `.out` +
watches for `.rc`, `cancel` does a process-group kill (`kill -- -PID`, reaching
xtb/OpenMP children), and reattach after a restart uses the pid + start-time (the
WSL analogue of `procutil.process_matches`; `reconcile_calcs` special-cases
`crest*` via `_crest_calc_alive`).

`parse_crest_result` reads `crest_conformers.xyz` (comment line = absolute energy
in Hartree, energy-sorted) + `crest.energies` into the shared `ParseResult` — new
`conformers: list[Conformer]` + `is_conformer_search`, with `geometry` /
`final_energy_eh` set to the lowest conformer so the existing best-geometry
reference path still works. `validate_result` requires normal termination
(marker `"CREST terminated normally."`, matched as a substring) + ≥1 conformer.
`queue_needs_orca` excludes `crest*` too, so an all-CREST queue runs with no ORCA
path. Charge/multiplicity come from the `Calculation` (shared with ORCA); CREST's
`--uhf` is the **number of unpaired electrons = multiplicity − 1** (not the
multiplicity). The pipeline was validated end to end against a real CREST 3.0.2
install in WSL Ubuntu (including the conformer→ORCA handoff).

`build_crest_argv` (`crest/runner.py`) maps `StepConfig.crest_*` to CLI flags:
method (`--gfn2`/`--gfn1`/`--gfn0`/`--gfnff`, exact-map with an unknown-value
`--<name>` fallback), solvent (`--alpb`/`--gbsa` by `crest_solvent_model`),
`--ewin`, `-T`, plus the optional advanced knobs — `crest_preset`
(`--quick`/`--squick`/`--mquick`), `crest_nci` (`--nci`), the MD/MTD numerics
(`--mdlen x<mult>`, `--tstep`, `--tnmd`, `--mddump`, `--vbdump`), and the
`--cbonds`/`--subrmsd`/`--norotmd`/`--keepdir`/`--cluster` toggles. Each advanced
flag is emitted only when set to a non-default value; the enum/numeric fields are
validated and clamped in `StepConfig.from_dict` (the trust boundary). File-based
options (`--cinp` constraints) and standalone modes (`--cregen`) are intentionally
out of scope. UI: the CREST build card + a collapsible "Advanced settings"
(`.adv-section`).

**Conformer → follow-up pipeline (per-conformer track expansion).** Follow-up
MLIP/ORCA calculations are built on the **Build tab** by referencing the CREST
calc through the normal geometry-source dropdown; the CREST build card's
**Conformer handoff** scope (`StepConfig.crest_handoff`: `lowest` | `all`)
decides what a reference receives. `lowest` (default) is the classic
single-geometry handoff — the best conformer via `_resolve_geometry`. With
`all`, the moment the search reaches DONE with K ≥ 2 conformers the engine
**substitutes** every PENDING calc chain referencing it with one clone chain
per conformer (`expand_conformer_tracks` in `core/queue.py`): clones are named
`{name}_c{k}` in track-major order (c1's whole chain, then c2's, …); a 1-hop
clone gets its conformer baked in as DIRECT geometry, deeper clones re-point
their REFERENCE to the same-track parent clone — so a queued `crest ← opt ←
freq` template fans out into K independent tracks and dependency-scoped
failure blocking works per track. `run_all` walks a growable list for this,
and the substitution is mirrored to the store through
`QueueCallbacks.queue_substitute` → `QueueStore.substitute_calcs` — the one
engine-driven structural mutation allowed mid-run (engine and store apply the
identical change, preserving the visible-queue == executing-queue invariant
that blocks user mutations during a run). Expansion also happens at run start
when an already-DONE `all` search has pending referencing templates (built
after it finished). The Results tab's conformer list is **read-only**: results
are for interpretation; building happens on the Build tab.

**Bridge slots** (`get_crest_status` / `check_crest` / `install_crest` /
`list_crest_distros` / `set_crest_distro`) follow
the MLIP pattern: a background probe publishes to `Bridge._crest_status` (guarded
by `_crest_lock`) and the UI polls `get_crest_status`. The Build tab gains a
fourth mode (`crest`; `Settings.build_mode`), a locked `#card-crest` (until a
distro has CREST), and a **Settings → CREST** distro picker + Install button. Wire
shapes: `CrestStatusPayload` / `CrestDistroPayload` / `ConformerPayload` in
`state/schemas.py`, mirrored in `web/types.js`.

### Paths: dev vs PyInstaller-frozen

`orcamgr/paths.py` is the single place that resolves locations, and the split matters:
- **Resource root** (`resource_root()`, read-only bundled assets: `web/`, `web_mobile/`,
  `data/`, `resources/`) is the project dir in dev, but `sys._MEIPASS` (a temp dir) in a
  frozen build. **Never write here** — in a frozen build it disappears on exit and may be
  read-only.
- **User data root** (`user_data_root()`, writable) is `%APPDATA%\ORCAdesk` on Windows.
  Settings (`settings.json`) and the default workspace live here, so they survive app
  updates. `Settings` (`orcamgr/config.py`) persists here and also auto-detects the ORCA
  executable (`autodetect_orca` scans PATH + common install roots).

When changing how assets are loaded, keep `web/` and `data/` landing at the *same*
relative paths the code expects — `build.spec` bundles them there, and `paths.py` reads
them there.

## Conventions

- **The version is single-sourced from `APP_VERSION` (`orcamgr/paths.py`).** Every
  *displayed* version derives from it: the window title (`window.py`) and the
  desktop UI (the top-bar badge + About dialog, via the `get_about()` bridge slot →
  `AboutPayload.version`, set in `app.js loadAbout()`). **Never hardcode a version
  string in `web/`** — to release, bump only `APP_VERSION` (and the prose in
  `CHANGELOG.md` / `README.md`).
- Bridge slots and API endpoints exchange **JSON strings**, typically
  `{"ok": bool, ...}` or `{"error": "..."}`; errors are returned as data, not raised
  across the JS boundary.
- **`web/` is type-checked** (`// @ts-check` + JSDoc, config in `jsconfig.json`).
  `web/types.js` holds the payload typedefs and **mirrors the Python serialization
  layer field by field** (`orcamgr/state/store.py`, `StepConfig` in
  `input_generator.py`, the per-slot payloads in `bridge.py`); `web/globals.d.ts`
  declares the Qt/bridge environment (incl. every slot's signature). When you add
  or change a payload or slot, update both, and keep
  `npx -p typescript tsc --noEmit -p jsconfig.json` at zero errors.
- ORCA defaults (functional `wB97X-D4`, basis `def2-TZVP`, `RIJCOSX`, aux `def2/J`)
  live in `input_generator.py`.
- Option lists in `data/*.json` are sourced from the ORCA 6.1.1 manual; method fields
  accept arbitrary values not in the list (used verbatim), so don't treat the lists as
  closed enums.
- **Functional-name normalization.** ORCA's simple-input parser is strict about some
  names, so `input_generator.normalize_functional()` maps the picker label to ORCA's
  accepted keyword before it goes on the `!` line (e.g. `M06-2X`→`M062X`, `M06-L`→`M06L`,
  `SCAN`→`SCANfunc`). Valid hyphenated keywords (`CAM-B3LYP`, `wB97X-D3/-D4`, `r2SCAN-3c`,
  `B97-D3`, `LC-BLYP`) are left untouched — the map is an exact dict, never a
  hyphen-stripping heuristic. When adding functionals, verify the spelling against the
  installed ORCA, not just the manual.
- **Dispersion: always write `D3BJ`, never a bare/combined `-D3`.** D3(BJ damping) and
  D3(zero damping) are different methods, and bare `-D3` is ambiguous; ORCA also rejects
  combined `FUNC-D3` tokens (it wants the dispersion as a separate keyword). So combined
  tokens like `B3LYP-D3`/`B3LYP-D3BJ` are normalized to `B3LYP D3BJ`. Use `D3BJ` (or
  `D4`) explicitly everywhere.
- **Double hybrids / MP2 need a `/C` correlation-fitting aux.** `_auto_aux` adds
  `AutoAux` (generates `/J` and `/C`) for those methods when RI is on; if the user sets
  the RI approximation to `NoRI` it adds nothing (conventional path). Plain hybrids/GGAs
  with an RI-J method get `def2/J` for def2 bases as before.

## Git workflow

Two long-lived branches:
- **`main`** — release branch. Only release commits land here (typically merged from
  `dev`). Each commit corresponds to a tagged version.
- **`dev`** — integration branch for day-to-day work. Branch feature work off `dev`
  and merge back into `dev`; promote to `main` only when cutting a release.

**When to commit.** Don't let large uncommitted diffs accumulate. Once roughly
**200–300 lines** of changes have built up (or at a natural logical boundary — a
finished feature or fix), **proactively suggest committing** to the user. Still
only actually commit when they confirm, and follow the branch/message rules below.

**Commit message format depends on the branch.** Both use a one-line subject, a blank
line, then a detailed body.

On **`main`** (releases) — subject is the version number followed by an English
one-line summary:
```
x.x.x <one-line summary of the release, in English>

- detailed change 1
- detailed change 2
```
Example:
```
0.2.0 Add NEB-TS workflow and free-energy profile

- Add neb_ts calc kind with product/ts-guess side .xyz files
- Add Gibbs free-energy profile view to the Results tab
```

On **`dev`** — subject is a `type: summary` prefix. Allowed types:
- `feat:` — a new feature
- `fix:` — a bug fix
- `hotfix:` — an urgent fix that may also be cherry-picked to `main`
- `docs:` — documentation only (README, CHANGELOG, CLAUDE.md, *_KR.md, etc.)
- `chore:` — build/packaging, dependencies, or tooling that isn't a feature or bug fix
  (e.g. `build.spec`, `installer.iss`, `requirements*.txt`, `.gitignore`)

```
feat: <one-line summary>

- detail 1
- detail 2
```
Keep the version-numbered format **only** for `main`; never prefix `main` commits with
`feat:`/`fix:`, and never put a bare `x.x.x` subject on `dev`.

**No co-author / attribution trailers.** Do not append `Co-Authored-By:` lines (or any
other tool-attribution trailer such as "Generated with Claude Code") to commit messages.
Commits should be authored solely under the repository's configured git user.

### Pre-commit documentation check (mandatory)

Before **every** commit, verify the Markdown docs reflect the change and update any
that are stale — include those updates in the same commit:

- `CHANGELOG.md` — any user-visible change (feature, fix, UI/behavior change) gets an
  entry. Version bumps must keep `APP_VERSION` in `orcamgr/paths.py` and the top
  `CHANGELOG.md` entry in sync.
- `CLAUDE.md` — update when the change touches anything this file documents:
  architecture, queue semantics/invariants, Bridge API surface, conventions
  (normalization rules, defaults), paths/build layout, or the git workflow itself.
- `README.md` — update when commands, install steps, requirements, or user-facing
  features described there change.
- `PRINCIPLES.md` / `DESIGN.md` — update when the change introduces or amends a
  convention/principle, deliberately deviates from one (if the deviation is
  kept, record it in the appendix with a disposition), or resolves a deviation
  already listed there.
  `DESIGN.md` §8 (tokens) and Part II (§9–§15: scales, component recipes,
  state matrix, copy templates) mirror the implementation — keep them in sync,
  and run the §15 checklist for any UI change.
- Other docs (`INSTALLER_GUIDE_KR.md`, `orcamgr/server/STAGE*_TEST_KR.md`, etc.) —
  update when the procedure they describe changes.

If nothing in the docs is affected, no doc edit is needed — but the check itself is
not optional: confirm it before committing, every time.

Alongside the doc check, run the test suite before every commit that touches
Python or `web/scf_graph.js` (`python -m pytest` and, for scf_graph changes,
`node tests/web/scf_graph.test.js`) — the suite is fast (~3 s) and pins the
principle contracts (P56). A red test is either a test bug or a product bug;
never adjust a test to make wrong behavior pass.
