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
pip install -r requirements.txt        # PyQt6 + PyQt6-WebEngine + psutil + numpy
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
python -m pytest                       # 1001 tests over the framework-free layers
node tests/web/scf_graph.test.js       # 40 tracker/progress tests, no npm deps
                                       # (covers progress_panels.js too)

# Real-backend smoke matrix (opt-in): one answer-known input per calc kind,
# run through the real QueueEngine against YOUR installed ORCA/MACE/CREST.
# Skips itself without the env var; each backend section auto-skips when its
# backend is absent. Wall-clock: ~1.5 h for the ORCA section (IRC and NEB
# dominate); MLIP + CREST add ~2 min. Run it before a release cut.
ORCADESK_SMOKE=1 python -m pytest tests/smoke -v
```

The **automated test suite** (`tests/`, pytest + one plain-Node script) covers the
framework-free layers: `state/` (store, schemas, session persistence), `core/`
(queue-engine semantics with a fake runner, per-kind validation, input generator,
parser on synthetic fixtures), `mlip/` (with a stdlib-only stub worker — no MACE
env needed), `crest/` (ensemble parser against a real ethanol corpus in
`tests/crest/fixtures/`, CLI-flag building, and the QueueEngine path via a fake
runner — no WSL/CREST needed), `config`, `procutil` (real child processes),
`cube` + `core/plot` (cube-header parsing and the `orca_plot` menu sequences /
trust-boundary clamp / pre-flight refusals, all without ORCA), and
the phone HTTP API via `fastapi.testclient` (auto-skips without fastapi).
Three tests are **static guards over the source** rather than behaviour:
`test_no_undefined_names.py` (a name bound nowhere in its module),
`test_no_lock_reentry.py` (a `self.foo()` called under a lock `foo` itself
takes — a UI-thread deadlock that only a second click during a running job
reaches, so no behavioural test finds it; P37), and `test_bridge_slots.py`
(every `bridge.X` the front-end calls is a real `@pyqtSlot`, and every slot
`web/globals.d.ts` declares still exists). QWebChannel exposes **slots**, not
methods, and every bridge call site is wrapped in a `try` — so a missing
decorator is a feature that silently does nothing. `check_overwrite_conflicts`
shipped that way and the Run-click overwrite screen never appeared.
The one exception to "framework-free" is `test_log_replay.py`, which imports
`gui/bridge.py` for its module-level `.out`-filter patterns and tail reader
only (no Bridge instance) to pin the filter against the tracker regexes it
mirrors; it skips without PyQt6. The Qt bridge/window layers
are thin adapters and are exercised manually. Tests that read the real ORCA output
corpus auto-skip when the corpus directory is absent, so the suite is green on any
machine. There is no linter configured. Parser/input-generator *evidence* still
comes from manual validation against real ORCA 6.1.1 output (P3); the
`orcamgr/server/STAGE*_TEST_KR.md` files are manual server-test checklists
(Korean), not runnable tests.

A second, opt-in tier lives in `tests/smoke/` (`ORCADESK_SMOKE=1`, see the
command above): a **real-backend smoke matrix** that runs one minimal
answer-known input per calc kind through the real `QueueEngine` — H₂O/H₂CO for
the plain kinds, HCN⇌HNC for the ts/neb/irc chain, ethanol for CREST — so a
pass proves generation → real execution → parse → per-kind validation per kind,
including the reference handoffs (opt→freq, ts_opt_freq→ts_freq/irc,
mlip_opt→sp, crest_conf→sp). The asserts lean on `validate_result`'s own
scientific pass bar (DONE requires convergence / the right imaginary-mode
count / ≥1 conformer), so the matrix stays honest without duplicating the
rules. Option combinatorics are deliberately out of scope (unit suite + P3
targeted validation cover those). Intended cadence: run it before a release
cut, on the release machine.

## Architecture

### UI is HTML/JS, backend is Python, glued by a QWebChannel

The entire UI lives in `web/` (HTML/CSS/JS, shadcn-style dark theme). `main.py` opens
`MainWindow` (`orcamgr/gui/window.py`), which hosts a `QWebEngineView` loading
`web/index.html` and registers a single `Bridge` object on a `QWebChannel`.

The JS is plain scripts sharing one global scope (no modules/bundler). `app.js`
holds the app shell (tabs, build cards, queue, polling); self-contained sections
live in their own files, loaded by `index.html` **before** `app.js` in this
order: `scf_graph.js` → `progress_panels.js` → `combo.js` (combobox widget) →
`appearance.js` (theme variant / wallpaper / Liquid-Glass pulse) →
`log_graph.js` (log pane + progress trackers) → `results_render.js`
(Results-tab section renderers) → `nbo_render.js` (the natural-orbital
analysis card) → `molviewer.js` (3D viewer + favorites) →
`structview.js` (Build-tab structure preview / screening / NEB endpoint
comparison).
Cross-file calls resolve at runtime, so only top-level statements may not touch
a later file's bindings (TDZ) — keep new top-level code declaration-only.

`progress_panels.js` is the one **hard** ordering constraint in that list (the
rest resolve at call time): it doesn't create its own global, it `Object.assign`s
the freq/TD-DFT/CREST trackers and step-panel renderers onto the **same
`SCFGraph` namespace** `scf_graph.js` publishes, so it must load immediately
after it. That keeps `SCFGraph.*` the single entry point for every caller while
splitting the file along its real seam — SCF/geometry convergence graph vs.
step panels, which share exactly one helper (`_fmtDuration`, re-exported on the
namespace). The mobile PWA loads **only** `scf_graph.js`: it uses the SCF/geo
graph alone, so it never ships the panel half. In node both files export the
namespace, so `tests/web/scf_graph.test.js` requires `scf_graph.js` and then
`progress_panels.js` for its side effect.

The view's page is `_ConsoleCapturePage` (`window.py`), which forwards JS console
output into the shared log buffer as `[web] ...` lines (rate-limited per identical
message) — so front-end errors are visible in the Log tab even in a deployed build
(the log ring's trim retains older `[web] ` lines preferentially, so ORCA's stdout
flood can't evict them — `QueueStore.append_log`);
`ORCADESK_REMOTE_DEBUG` (handled in `main.py`) remains the dev-time tool.
`window.py` also trims the embedded browser: Chromium's built-in PDF viewer is
disabled (the UI never opens PDFs). **WebGL is enabled** — the Results-tab 3D
structure viewer (3Dmol.js) needs it; the Liquid-Glass wallpaper canvas is still
2D, so WebGL is used only inside the viewer's own canvas. Minimizing the window sets the
page's lifecycle state to Frozen so Chromium releases memory while ORCAdesk sits
in the taskbar — safe because the JS side polls and already skips hidden ticks,
catching up on restore. (No profile/cache tuning: the Qt 6 default profile is
already off-the-record.)

`orcamgr/gui/bridge.py` is the **entire backend API surface for the desktop**: every
`@pyqtSlot` is callable from JS (the full slot list, with signatures, is the `Bridge`
declaration in `web/globals.d.ts` — kept honest by the tsc check).
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

The **Results tab has two modes over one selected result**: `Output` (what the
parser read) and `Visual` (what can be *drawn* from it) — `setResultsMode` in
`results_render.js` flips `#result-body` / `#visual-body`. The picker and the
single *Open file…* button are **shared**, so switching modes never changes
which calculation is on screen; `Show all` and the free-energy-profile card are
Output's and are hidden in Visual. See the Visual-mode note below for what the
mode discovers and why plotting happens on its rows.

The Output mode is purely presentational over `ParsePayload` — with one
section that is presentational over a *second* payload: the natural-orbital
analysis card (`web/nbo_render.js`) is computed from the run's `.gbw` on
demand rather than read from the parse, so it opens as a *Run analysis*
button and renders in place when the background job lands (see the NBO
section below). It is gated like the other electronic-structure sections.
A QUEUED
calc's result is fetched by NAME through `bridge.parse_calc_output`, which
dispatches on the calc's **kind** (`result_from_output`); external files
(drag-drop / *Open file…*) go through `bridge._parse_path`, which has no kind and
dispatches per backend heuristically (an engine-written `*.mlip.json` → the MLIP
parser; `crest_conformers.xyz`/`crest_best.xyz` siblings → the CREST parser; else
the ORCA parser). Keep the heuristic OFF the queued path: workspace folders
survive removal, so a calc reusing a removed CREST calc's name would otherwise
parse as a conformer search.

**The result picker lists more than the queue.** Those same surviving folders
are results the user still wants — `bridge.list_workspace_results()` scans
`{workspace}/*/` for `{name}.mlip.json` else `{name}.out` (newest first, capped
at `_WORKSPACE_SCAN_MAX`; the folder convention itself is
`orcamgr/resultfolder.py` `result_artifact`, shared with the folder picker
below) and the Results `<select>` shows them in two
`<optgroup>`s. The grouping is the parse-route split above, not decoration:
an option's value is a **source string** (`calc:<name>` / `file:<path>`, the
same addressing the cube slots take), and `queued` comes from the store, never
from "have we parsed it yet" — grouping by `calcResults` would file a queued
calc under the workspace and send it through the folder heuristic. Picking a
queued calc not yet fetched parses it by name on demand. The scan runs on entry
to the tab (`switchTab`), never from the 1-second poll — it touches the disk.
A result opened from **outside** that scan is remembered in `_openedPaths` and
re-emitted as an "Opened file" `<optgroup>` on every rebuild: the scan lists
only `.out`/`.mlip.json` under the workspace root, so an option merely *inserted*
into the live `<select>` was lost on the next tab entry — and never survived at
all for a `.xyz`, which the scan does not list.

*Open file…* and *Open folder…* are two pickers over **one** route for both
modes, because the user is choosing a result, not a way of reading it:
`bridge.pick_result_file()` picks the file and returns
`{route: "parse"|"structure"}` by suffix, and the front-end reads it through
`parse_out_path` or `get_structure_frames` (`_openPicked`, the tail both
share). A `.xyz` becomes the shown result in
Visual mode with nothing parsed (`_currentResult = null`), which is also the
route `showWorkspaceResult` takes when such a path is re-picked.
`bridge.pick_result_folder()` names a **run folder** instead and resolves it
to the result file inside (`resultfolder.find_result`: the convention above
first, then the folder's only — or newest — `.out`, since an ORCA run made by
hand names its output freely), always the parse route; a folder holding
nothing to read is refused with the sentence that says what would have
worked. The picker does not take structure-only folders: Visual already
lists every `.xyz` set beside a result.

Both parse paths
send the *whole* `ParseResult` (every section the parser found) plus two gating flags
— `is_optimization` and `show_elec` (`= ParseResult.shows_electronic_props`) — and the
front-end (`web/results_render.js` `renderResultSections` / `renderSummary`) decides what to show
per calc kind. Final geometry shows only for opt jobs; general electronic-structure
sections (orbitals, charges, Mayer, dipole, rotational, SCF decomposition, and the
`"elec"`-tagged summary rows) show only for sp/opt; freq/tddft/nmr/neb sections are
present-only. The Results header's **`Show all`** toggle (`showAllResults` in `app.js`)
overrides the gating to reveal everything parsed. Keep the gating on the front-end (not
the payload) so the toggle re-renders without a re-fetch. Nothing in Output
*draws* — no 3D entry points live there — so a section that only had a "View in
3D" button (the ESP map) is gone from it entirely.

**Visual mode discovers what there is to show.** `renderVisual` asks two slots
about the selected result's source string and builds one row per openable thing:
the parsed geometry (no backend call — P4), every `.xyz` set
`bridge.list_structure_sets(source)` found beside the result
(`molview.discover_structure_sets`: direct subfolders holding `.xyz` — the
`conformers/` export sorts first — then the folder's own `.xyz`, each labelled
by what it *is*; past `_MANY_XYZ` unnamed ones they collapse into a single
whole-folder set, so a hundreds-strong export is one row and not a hundred file
reads on a tab switch), and, when a `.gbw` sits there, the orbital browser and
the ESP map. Each row opens the same `#mol-viewer`; a set opens through
`bridge.get_structure_frames(path)`, which takes a file or a folder. This
replaced a *Browse .xyz…* folder picker (`browse_xyz_folder`) and the
CREST-only `get_conformer_frames`, both removed: everything worth opening
already sits in the run folder, so the tab lists it rather than asking the user
to find it. `frames_from_folder` reads a folder's `conformers/` child when the
folder itself holds no `.xyz`, so pointing it at a CREST run means the export.
The CREST ensemble keeps its `calc:<name>` favorites key and its `{run}_c1`
frame labels (`_CREST_ENSEMBLE` in `bridge.py`) — labels key the favorites store
and name what *Export ★* writes, so the two must agree.

**Plotting happens on the row, not behind the modal.** `mvVolShow` was split
into `_mvVolFetch` (generate/read the cube(s)) + `_mvVolDraw` (touch the
GLViewer) + `_mvVolSelect` (the stage's chrome), because the two halves now
happen in different places: `viewOrbitals3D` / `viewEspMap` fetch **first** and
call `_mvOpenStage` only once there is something to draw, while a pick clicked
*inside* the viewer keeps the in-stage busy overlay (the stage is already on
screen, so something has to say why it has not changed). The Visual row's
`visOpen` wrapper is what reports the wait — the button reads `Plotting… m:ss`
and every row is disabled (the backend serializes orca_plot anyway), leaving the
rest of the tab usable for the minutes an ESP map takes. The ESP row also shows
an on-disk dot via `mvEspCubeNames(base, grid)`, which goes through
`_mvCubeName` so the dot and the fetch cannot disagree about the filename.

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
  ORCAdesk closing. It is invoked with the input's **bare file name** and `cwd`
  set to the run folder, never an absolute path: ORCA 6 passes its own argument
  on to `orca_startup` and the MPI ranks **unquoted**, so a single space
  anywhere in the path truncates it (`Cannot open input file C:/Users/John`,
  error termination in Startup — measured on 6.1.1, serial and 2-rank MPI).
  The default workspace lives under the user profile, so this is what lets an
  account named `John Smith` run at all. The *file name* still cannot contain a
  space — see the name rule below. It is invoked with the input's **bare file name** and `cwd`
  set to the run folder, never an absolute path: ORCA 6 passes its own argument
  on to `orca_startup` and the MPI ranks **unquoted**, so a single space
  anywhere in the path truncates it (`Cannot open input file C:/Users/John`,
  error termination in Startup — measured on 6.1.1, serial and 2-rank MPI).
  The default workspace lives under the user profile, so this is what lets an
  account named `John Smith` run at all. The *file name* still cannot contain a
  space — see the name rule below. Live log/progress come from **tailing** the `.out`. Verbs:
  `launch()` (returns `(pid, create_time)` to persist), `adopt()` (reattach to a
  process from a previous session), `monitor()` (tail until exit/cancel/detach),
  `cancel()` (kill the tree), `detach()` (stop monitoring, leave ORCA running).
  Process identity + tree-kill go through `procutil.py` (psutil), which guards
  against PID reuse via `create_time`.
- `procutil.py` — psutil-backed `process_matches(pid, create_time)` and
  `kill_tree(...)`, used for reattach and reliable tree termination.
- `tailer.py` — `FileTailer`, the incremental tail behind the live log for both
  the ORCA and CREST runners, plus `decode_orca_line`. It reads with
  `errors="surrogateescape"` and repairs each line through
  `orcamgr/textio.py`: an ORCA `.out` on Windows is **mixed-encoding** — ORCA
  writes its own (ASCII) strings as UTF-8 but echoes the paths it was handed on
  argv in the **process ANSI code page** — so decoding the file as UTF-8 turned
  every line naming a Korean calc name or workspace into U+FFFD. The buffer is
  split once per drain, not sliced per line: the old quadratic loop took 47 s on
  an 8 MB burst (ORCA emits those) and the monitor only checks cancel/detach
  *between* drains, so Stop went unanswered for that whole time.
- `resources.py` — what a calculation *costs* (`declared_cores`,
  `estimated_ram_mb`, reading a raw `.inp`'s own `%pal`/`%maxcore`), the
  `ResourceBudget` the dispatcher admits against, and `ram_headroom_mb()` —
  the machine's real free memory minus a reserve, consulted before a **second**
  job starts because the memory estimates are wrong in both directions. It also
  owns the one *ceiling* on parallelism that is not a budget:
  `numfreq_rank_cap()` — a numerical frequency runs one displaced-geometry
  gradient per process and deadlocks when given more processes than it has
  displacements (`6N - 6`), so the generator caps a generated `%pal nprocs` to
  it with a `#` note and the engine warns about a raw one (P57). Qt-free, psutil
  for the machine's physical core count / installed RAM.
- `queue.py` — `QueueEngine` orchestrates the pipeline (details below). Validation
  is the module-level `validate_result()` (shared by the engine and session
  reconciliation). Cancel verbs: hard `cancel()` (kill the in-flight job → it
  becomes CANCELLED; break out of the walk and leave every remaining calc as-is,
  so PENDING rows stay PENDING — a stop does not discard the queued plan),
  graceful `request_stop_after_current()` (drain — let the in-flight job finish,
  rest stays PENDING), and `detach()` (shutdown — leave the running job alive).
- `parser.py` — `parse_file()` → `ParseResult` (energies + SCF energy
  decomposition, geometry, orbitals/HOMO-LUMO, Mulliken & Löwdin charges, Mayer
  population (bond orders + valences), dipole moment, rotational constants,
  frequencies, full thermochemistry (U/H/G/ZPE/T·S/temp/pressure), TD-DFT
  transitions + excited-state composition, NMR, NEB/IRC path — both print a
  `PATH SUMMARY` table; `neb_path_kind` records which, so the Results tab titles
  an IRC profile as an IRC). Marker-based,
  tolerant of `\r\n`; when a value recurs (e.g. across opt steps) the **last**
  occurrence wins. `ParseResult.summary_rows()` returns `(label, value, category)`
  rows where `category="elec"` tags general electronic-structure rows; the
  `shows_electronic_props` property (`is_optimization or not specialty`) decides
  which sections the Results tab shows per kind — see the Results-gating note.

### Queue semantics (important invariants)

These rules live in `QueueEngine.run_all` / `validate_result` and `QueueStore`:
- **Several calculations can run at once, admitted by budget (P57).**
  `ResourceBudget(max_jobs, cores, ram_mb)` (from `Settings.max_concurrent_jobs`
  / `max_total_cores` / `max_total_ram_mb`) is passed to `make_engine_factory` by
  **both** run entry points. 0 is "auto" for all three: for the two budgets it
  means this machine (physical cores / 75% of RAM), and for `max_jobs` it means
  **as many as those budgets allow** — every job takes at least one core, so the
  core budget is already the ceiling and the job count needn't be a second
  number to keep in sync with it. `_run_walk` is a
  dispatcher: it picks in queue order under the store's lock, then runs each
  calc in its own thread with its own backend runner (`_JobSlot`, thread-local
  via `_job`) and its own reserved cores/RAM, released when it finishes. A row
  is picked only if it is **dependency-ready** — a REFERENCE whose parent may
  still become DONE is *deferred*, not failed (sequential order used to
  guarantee this). "May still become DONE" covers RUNNING, picked-but-not-yet-
  stamped (`_active_names`), **and PENDING/CANCELLED rows this run has not
  handled yet** — a CANCELLED calc re-runs (P24), so treating it as final would
  admit its dependent alongside it and FAILED-lock it — and **affordable**; a row that does not fit is skipped over,
  and if nothing fits with nothing in flight the first ready row runs alone over
  budget (no starvation). All-deferred with nothing in flight = a reference
  cycle, stamped BLOCKED (`_block_unreachable`), never a hang. Cost is read from
  what will actually execute (`core/resources.py`): a raw calc's own `%pal` (and
  `%maxcore`, falling back to ORCA's default), not the hidden form field. The
  MLIP worker's thread cap is `worker_threads`, deliberately **not**
  `declared_cores`: a CUDA job is charged 1 core but still needs threads for its
  CPU-side work. **GPU work runs one job at a time** (`uses_gpu` → `_JobSlot.gpu`
  → `_fits`): a CUDA job costs one core, so the budget would admit a dozen onto
  one card, and there is no number ORCAdesk can put on video memory. Only an
  explicit `mlip_device == "cuda"` counts — `""` (auto) is resolved inside the
  worker, the only place that can ask torch. `max_jobs` defaults to **1** — the classic sequential
  queue — so nothing changes until the user raises it.
- **Every log line carries the calc that produced it** (`LogLine.calc`, `""` =
  engine-level). With jobs interleaving into one buffer the raw ORCA tail has no
  name of its own, so the tag is what lets the Log tab and the per-job graphs
  separate them. `QueueCallbacks.log` is `(msg, level, calc="")`. On the
  front-end (`web/log_graph.js`) the five convergence trackers are no longer
  globals but a **per-calc bundle** in the `_jobs` Map keyed by that tag
  (`jobTrackers(name)`); the panel renders one job at a time — `shownJob()` =
  the explicit pick (`setGraphJob`) else the newest running job — and the job
  picker (only jobs with an actual curve — `graphJobs()`) and the raw-log filter
  (every job that produced a line) are **button strips**, not selects, and only
  appear once a second calc has produced output — so a single-calculation run is
  visually unchanged, and a sequential run of several grows the strip when the
  second one starts (that is when the log first holds two jobs' lines, which is
  what the strip is for). The job name rides in `data-job` with one delegated click handler:
  a calc name may contain a quote, which would break an inline `onclick`. `QueueSnapshot.resources` feeds the
  queue's occupancy strip.
- **The queue stays editable while it runs (live queue, P29).** `start_run`
  hands the engine the store's **own list**, and the engine walk picks the
  next unhandled row (tracked by object identity) under the store's **own
  lock** (`make_engine_factory` passes it). So while a run is in progress:
  a calc added lands in the *same* run; PENDING/CANCELLED/BLOCKED rows can
  be removed, edited (an edited row is a new object — the walk picks it in
  its edited form), and reordered (the walk follows live list order).
  Still protected mid-run: the in-flight calcs (state RUNNING plus
  `QueueEngine.active_names` for the picked-but-not-yet-stamped window),
  DONE/FAILED rows (remove/edit refused until the run ends), `clear`, and
  reference integrity — a mid-run add/edit with a dangling reference and a
  remove/rename of a referenced parent are refused at the store, because
  the pre-run screen (`find_dangling_reference`) never sees mid-run
  mutations. A mid-run add whose name already has a result on disk is
  confirmed in the UI first (`bridge.has_existing_output`) — the Run-click
  overwrite modal never sees it, and the engine's keep-existing set is
  fixed at run start, so it would otherwise overwrite silently. The
  keep-existing answers from the Run-click modal are pinned to the ROWS they
  were given about (`_skip_ids`, taken at run start): a row removed and re-added
  — or edited in place — under the same name mid-run is a different
  calculation and must actually run, not be stamped DONE from an output it never
  produced.
- **`kind` is a closed set** (`KNOWN_KINDS` in `core/queue.py`), validated in
  `calc_from_dict` alongside the name. It selects the pipeline (ORCA / the MLIP
  worker / CREST), the per-kind validation and the Results gating, so it is not
  a free-text field the way a functional name is (P30) — an unknown one used to
  be taken verbatim from an HTTP payload and run through the ORCA runner with
  default settings.
- **Calculation `name` is unique and is used as the on-disk folder name**
  (`{workspace}/{name}/`). Uniqueness is enforced in the store,
  **case-insensitively** — Windows resolves `water` and `Water` to the same
  folder, so accepting both would share one `.out` between two calcs. The
  invariant holds on every entry path: `add`/`replace` **and session
  restore** (`load_session` dedups, first occurrence wins). The name is also
  the `.inp` file name ORCA is *invoked* with, so for an ORCA-backed kind it
  refuses a **space or `&`** on top of the Windows path-dangerous set
  (`_ORCA_HOSTILE_CHARS`; `mlip*`/`crest*` are exempt — those pipelines pass the
  name as an argv element or a shell-quoted variable, and refusing it for every
  kind would drop such a calculation from a restored session):
  ORCA 6 splits its own argument on whitespace, and the run then dies in
  Startup — a FAILED calc, which is locked (P24), so the name could never be
  run again. Measured on 6.1.1; `(`, `)`, `'`, `,`, `=`, `;` all run normally,
  which is why this is a two-character rule and not a safe-character list.
- **The queue autosaves to `%APPDATA%\ORCAdesk\session.json`** on every mutation
  (`QueueStore._bump_and_save`) and is restored on startup (`load_session`). A
  `RUNNING` calc persists its detached ORCA's `(pid, create_time)` **and its
  output path** (recorded at launch — without it the finished-while-closed →
  DONE judgment below has nothing to read). On the next
  launch, `reconcile_calcs` checks that identity: still alive → stays `RUNNING`
  and is **reattached** (`OrcaRunner.adopt` + `monitor`, continuing the queue).
  The monitor tails from the **current EOF** — replaying hours of output
  through the capped buffer would evict the graph — so the UI rebuilds what
  that tail missed from the file instead: the Graph panel (SCF/geo curve
  **and** the freq/TD-DFT/CREST step chains) via `get_graph_lines` +
  `maybeSeedGraph`, and the Raw log's last 500 lines via `get_output_tail` +
  `insertRestoredLog`, fenced by `log-mark` rules so restored history never
  reads as live output. The restore is triggered by the engine's own reattach
  line (`_CALC_REATTACH_RE`), never by queue state — a job THIS session
  started has a complete log and must never be restored over.
  The resume is a **third run entry point** and applies the Run button's two
  pre-run screens as far as it can: it passes the overwrite-conflict names as
  the keep-existing set (nobody is at the keyboard to answer the modal, so
  existing results are preserved rather than recomputed over, and the log says
  which), and reports a dangling reference — which it cannot refuse, having a
  running job to reattach, but which the engine now blocks rather than fails.
  A process that is gone → judged from its output (`DONE` if terminated
  normally + valid, else
  `FAILED`). The engine applies the **same judgment mid-run**
  (`_judge_dead_running`): a RUNNING calc whose process died while unmonitored
  (e.g. the startup reattach was declined and the job finished later) is
  adopted from its `.out`, never relaunched — a relaunch would truncate the
  completed output. Closing ORCAdesk does **not** kill the running job — `shutdown()`
  calls `store.pause_run()` (engine `detach()`), not cancel. **MLIP is the
  exception**: its worker has no detach/reattach machinery and is terminated on
  shutdown, so a mid-run mlip calc is stamped `CANCELLED` ("Stopped on
  shutdown." — re-runnable, never the locked FAILED) at detach, and
  `reconcile_calcs` applies the same judgment to a session persisted before the
  stamp landed (a worker that raced to completion still restores `DONE` from
  its result JSON).
- **Geometry source** is `DIRECT` (coords supplied, e.g. from `.xyz`) or `REFERENCE`
  (another queued calc by name). For a reference, the engine injects that calc's
  **optimized final geometry** at run time — so opt → freq reuses the optimized
  structure automatically.
- **Failure propagation is dependency-scoped, not whole-queue.** If a calc fails,
  every calc that references it (transitively) is marked `BLOCKED` and skipped;
  unrelated calcs continue. The chain STOPS at a `DONE` parent — its geometry is
  on disk and frozen (P24), so whatever failed further up was needed to produce
  it and already has. The verdict is read from the LIVE queue every time
  (`_blocked_by` walks the row's reference chain), never from a set of names
  captured at run start: a name is not an identity, and the queue is live (P29),
  so a BLOCKED row edited to point at a healthy parent — or removed and re-added
  with its own coordinates — was being blocked again by a decision about a
  calculation that no longer existed. Walking the chain also makes transitivity
  independent of queue order. A reference to a calculation that is **not in the
  queue at all** blocks the dependent too (both run entry points screen for it
  up front, but the startup resume cannot refuse and a mid-run add is never
  screened) — BLOCKED rather than FAILED, because FAILED is locked and a
  re-pointable row must stay editable.
- **A stale ORCA restart file is set aside before a launch**, and the
  keep-existing skip set is keyed by row IDENTITY, not by name — see the two
  bullets below.
- **`DONE` calcs are never recomputed** on a re-run (the result is frozen);
  **`FAILED` calcs are locked** at the moment of failure (P24): no re-run,
  no edit, no reorder — a re-run skips them and blocks their dependents up
  front; retrying means building a new calculation. Their remaining
  interactions are read-only diagnosis and × removal, which is
  queue-list-only — a workspace folder is never deleted, and nothing on the
  removal path touches the filesystem at all (a binding rule). Deletion does
  exist elsewhere, and only ever for work this app produced and is about to
  replace: a re-run clears what the previous attempt left in the run folder
  (the MLIP worker's `vib/` scratch and stale result JSON, CREST's markers and
  ensemble, the WSL scratch dir), and the MLIP installer removes a half-built
  environment before retrying that name. Only `CANCELLED` (a deliberate user stop)
  re-runs. `EDITABLE_STATES` is `PENDING`/`CANCELLED`/`BLOCKED` — BLOCKED
  stays editable so a dependent can be re-pointed after its failed parent
  is removed. The keep-existing ("skip") run option parses the kept output
  through `result_from_output` and stamps `DONE` only if `validate_result`
  passes **and** the result structurally belongs to the calc
  (`_kept_result_matches`: case-insensitive atom element sequence; a
  no-placeholder raw calc is judged by its raw text's coordinate block, not
  the possibly-stale `calc.xyz` — folders survive removal, so a new calc
  reusing a removed calc's name must not adopt the old output) **and the output
  contains the section the kind is defined by** (`_missing_section_for_kind`: a
  freq output has frequencies, an opt output is an optimization, …). The element
  sequence alone is not identity — an `opt` run's folder has the right atoms for
  a `freq` calc of the same molecule, and the freq rule "no imaginary modes" is
  vacuously true of an output with no frequency table, so an opt result was
  adopted as a finished frequency job. Otherwise the calc runs.
- **A stale ORCA restart file in the run folder is renamed aside before a
  launch** (`_set_aside_stale_guess`). ORCA 6 reads a `{name}.gbw` sitting
  beside `{name}.inp` as the SCF's initial guess on its own (AutoStart), and
  aborts in GUESS when the atoms differ: `Error: Input geometry does not match
  current geometry`, exit 55 (verified on 6.1.1). Folders survive removal and a
  name is unique only within the *current* queue, so that file may be from
  another molecule — a reused name, or the same row pointed at a new `.xyz`,
  both flows the overwrite modal allows — and the result is a FAILED calc,
  which is locked (P24), for a reason the user cannot see. The decision is made
  from the PREVIOUS `.inp` still on disk (`_guess_signature`: the `* xyz` line's
  charge/multiplicity plus the element sequence), so a same-structure restart is
  kept as a free warm start and only a mismatch — or an unreadable comparison —
  moves the file to `{name}.gbw.bak`. An input that asks for those orbitals
  itself (`MOREAD`/`%moinp`) is never touched. Renamed, never deleted.
- **Pre-launch guards run for raw calcs too.** The engine writes NEB-TS side
  files (`product.xyz` / `ts_guess.xyz`) from config at run time in both form
  and raw mode — but for a raw calc **only when its text actually uses the
  generated `"product.xyz"` reference**: a custom `NEB_End_XYZFile` path is
  the power-user escape, and the stored product is dead state for it (the
  `_nebProductXyz` global survives excursions, so it must trigger neither
  the side-file write nor the checks). With the reference present, a missing
  stored product is refused pre-launch, and the reactant/product atom-order
  check runs against the coordinate block of the **rendered** text
  (`_raw_coordinate_block` — the reactant that actually executes). An `irc`
  with `InitHess read` stages the
  named `.hess` from the referenced calc's folder when it isn't in the run
  folder, and fails fast when it can't be found; a blank filename is refused
  by `build_input` itself (`ValueError` — the trust boundary, so phone/API
  payloads fail loudly too).
- **Result validation is per-kind**: `opt`/`ts_opt` (and the combined
  `opt_freq`/`ts_opt_freq`) must converge; every freq kind must have PRODUCED
  frequencies at all (the imaginary-mode rule is vacuously true of an empty
  table, so a run that never computed a Hessian used to pass);
  `freq`/`opt_freq` must have zero imaginary frequencies; `ts_freq`/`ts_opt_freq` must have exactly one;
  `neb_ts` must have exactly one when frequencies were computed. A
  validation failure marks the calc `FAILED`. Calc kinds: `opt`, `ts_opt`,
  `freq`, `ts_freq`, `opt_freq`, `ts_opt_freq`, `irc`, `tddft`, `sp`,
  `general` (free keyword combination; no per-kind validation), `nmr`,
  `neb_ts`, `mlip_opt`, `mlip_sp`, `mlip_freq`, `mlip_opt_freq`, `crest_conf`.
  Kinds starting with `mlip` or
  `crest` run **outside** the ORCA pipeline: `QueueEngine.run_all` routes `mlip*`
  to `_run_mlip_calc` and `crest*` to `_run_crest_calc` (a conformer search in
  WSL) instead of `_run_calc`. The MLIP worker branches on a `task` field mapped
  from the kind (`mlip_opt`→`opt` LBFGS relaxation, `mlip_sp`→`sp` single-point
  energy, `mlip_freq`→`freq` finite-difference vibrational analysis at the given
  geometry, `mlip_opt_freq`→`opt_freq` relax-then-frequencies). MLIP validation
  is per-kind too: `mlip_opt`/`mlip_opt_freq` must converge; `mlip_freq`/
  `mlip_opt_freq` must have zero imaginary frequencies (same "true minimum" bar
  as ORCA freq); `mlip_sp` requires only normal termination (an SP has no
  convergence — `parse_mlip_result` sets `is_optimization` from `task`: True for
  the opt kinds, False for sp/freq).
  `crest_conf` validation requires normal termination + at least one conformer.
  See the MLIP and CREST sections.

### Structure screening and editing (Build tab)

`orcamgr/core/structure.py` is the one place geometry is reasoned about. It is
pure and Qt-free (it needs no numpy, though `orcamgr/nbo/` has since made numpy
a core dependency — see P5), so it unit-tests directly (`tests/test_structure.py`); the front-end reaches it only
through five thin Bridge slots — `check_structure`, `compare_structures`,
`measure_structure`, `structure_fragment`, `edit_structure` — and holds **no**
geometry logic of its own. That is deliberate (P4): the Build tab's NEB badge
and `input_generator.check_neb_atom_order`'s gate are now the same judgment,
where they used to be a Python implementation and a hand-mirrored JS copy that
could drift. `check_neb_atom_order` is a three-key *projection* of
`structure.compare_atom_order` and must stay one — the queue engine and the
generator consume those three keys, and a test pins the projection.

`check_geometry` answers the questions worth asking before a multi-hour launch
(P26) and *reports* them (P2): a spin multiplicity the electron count cannot
produce, duplicate/overlapping atoms, coordinates that are really in Bohr (they
read as a molecule with no bonds), disconnected fragments. `level` separates
"ORCA will refuse this" (error) from "worth a look" (warn) — a two-fragment
complex never blocks the queue. Nothing is auto-corrected.

**Everything in this module READS.** There was a structure editor here (rigid
bond/angle/dihedral edits, fragment moves); it was removed, along with its three
Bridge slots and the geometry math behind it, because building and modifying
molecules is a molecular editor's job — Avogadro2 and its peers do it properly,
and a side feature here could only be a worse one. If a future change needs to
*write* a structure, weigh that first: ORCAdesk's job is to tell you what the
file you are about to run contains.

One implementation note worth keeping: **bond perception is a uniform grid, not
an all-pairs sweep.** `check_structure` runs on the Qt UI thread on every charge
keystroke and every Build-tab entry; with the cell edge set to the longest
possible bond, every partner is inside the 27 cells around an atom (~20 ms for
3000 atoms, vs. a visible freeze). It is distance-based only — no bond orders —
and feeds only the fragment count and the screening findings. **ORCA never sees
it.**

Front-end side: `web/structview.js` owns the geometry card's inline preview and
findings list, and the `card-neb` endpoint comparison.
`card-neb` lives in `index.html` rather than in the re-rendered method form
**because a WebGL stage must not be re-created**: `renderConfigForm` replaces
`#calc-config`'s innerHTML on every calc-type switch, which would orphan a
viewer's GL context each time. For the same reason every stage re-renders on
`switchTab("build")` and on the return to a DFT build mode — a canvas created
while its card is `display:none` sizes to zero.

### Optional phone-sync server

`orcamgr/server/` is a thin FastAPI layer (`app.py`) over `QueueStore`, started/stopped
from the desktop by `ServerController` (`controller.py`) running uvicorn in a daemon
thread on the shared store. It serves the mobile PWA from `web_mobile/` at `/` and the
queue API under `/api/`. fastapi/uvicorn are **optional** — `ServerController.is_available()`
gates the whole feature, and the desktop app works fine without them. Per `CHANGELOG.md`
phone-sync is in development and **not part of the packaged build** — enforced by
`build.spec`'s `excludes` (fastapi/uvicorn/starlette/pydantic/anyio/qrcode/PIL),
which must be dropped deliberately when phone-sync ships.

### MLIP environment (deliberately separate from ORCA)

`orcamgr/mlip/` is a dedicated package, kept **out of** the ORCA pipeline in
`core/` on purpose: a Machine-Learned Interatomic Potential is a separate Python
toolchain (PyTorch + mace-torch + ASE) that ORCAdesk shells out to the same way
it shells out to the ORCA executable. ORCAdesk can *build* such an environment
for the user (`installer.py`, below) but never installs into the user's own
Python, and never requires a compiler or a manual shell session — the same bar
CREST's installer meets.

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
"I pip-installed it myself but the env is incomplete" case. The probe also
reports whether the interpreter's torch sees a **CUDA GPU** (`cuda` / `cuda_name`
on the env payload), which drives the build card's Device selector and the
GPU/CPU note shown in Settings → MLIP. Probes are slow
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

**Build-tab modes.** The Build tab's mode toggle is **backend-first**: `DFT` /
`MLIP` / `CREST` (`#bmode-*` in `index.html`), with a **Beginner/Expert
sub-toggle** (`#bsub-*`) shown only while DFT is active. Internally and in
`Settings.build_mode` the mode is still one of the four historical values
`beginner`/`expert`/`mlip`/`crest` (beginner/expert are the DFT sub-modes), so
persisted settings are untouched; `setBuildMode` in `app.js` maps them onto the
toggle and `setDftSub` handles the sub-switch. **The MLIP and CREST buttons —
and their top-bar pills — are hidden until the user has actually set that
backend up** (`applyBackendVisibility` in `app.js`, called from `renderMlip` /
`renderCrest` / `refreshQueue` / `setBuildMode`; both start `display:none` in
`index.html`). Adoption is read from what the user did, never from the probe's
verdict: MLIP from `MlipStatusPayload.envs.length` (known from settings at the
first poll, so the button never pops in after torch imports) and CREST from
`state === "ready"` or an explicit `Settings.crest_distro` — CREST's aggregate
`"error"` means "WSL present, no distro has CREST", i.e. the ordinary
never-installed case, so it hides. A **registered-but-broken** setup stays
visible and locked with its reason (the `applyBackendCardLock` path is
unchanged), and a backend stays visible while the queue holds one of its calcs
(`_queueHasBackend`) or while it is the active mode — otherwise an `mlip_*` /
`crest_*` row would become uneditable. A `#bmode-more` chip ("+ More
backends…") shows whenever one is hidden and switches to Settings, whose MLIP
and CREST sections are unconditional. This amends DESIGN.md D41 (see its scope
clause) — hide for "never adopted", lock for "adopted but not ready". **The Beginner↔Expert linkage is
one-way by design**: Beginner → Expert converts the current form to a generated
`.inp` via `build_inp_preview` and puts it in the raw editor (this replaced the
"Edit raw .inp" button / `enterRawMode`); raw text can never be converted back,
so Expert → Beginner confirms discarding the editor content (the
name/type/charge/mult/geometry cards persist outside the editor). Conversion
failures never switch (the filled form stays visible); an empty name skips
conversion and opens a plain empty editor (logged) for the paste workflow. Raw
editing **is** the Expert sub-mode: `enterRawWithText` forces the Expert layout,
editing a raw calc opens Expert, editing a form calc opens Beginner (the old
"beginner with a dimmed locked form" hybrid and `lockFormForRaw` are gone);
backing out of a **not-yet-saved** conversion of an edit confirms and reopens
the form edit via `editCalc` — only a saved raw calc is locked to raw. Guard
invariants: re-clicking the active mode button is a strict no-op in
`setBuildMode` (never drops an edit or blanks the editor; `onInpDropped`
therefore exits any in-progress edit itself before loading — a drop starts a
NEW calc, never an overwrite of the edited one); the Beginner branch re-renders
the method form only when `#calc-config` is empty or the Type select changed
while the form was hidden (`_configKind`, re-rendered with the same field
preservation as `onKindChange` — `collectPreserve`, which also carries
maxcore/nprocs and the kind-shared numerics, and is kind-AWARE for Extra
options and SCF: an untouched kind default follows the new kind, an explicit
user override survives), so the user's method setup survives Expert/MLIP/CREST
excursions without ever going stale against the selected
kind; the edit target is tracked **by name** (`editName`), never by queue
index — the queue can shift under an open edit (remove/reorder, phone adds),
and Update re-resolves the target at save time (a vanished target degrades
to a plain add); `collectCalcFromForm(forPreview)` relaxes the NEB-TS
product requirement like the other geometry checks. `rawText` survives an MLIP/CREST
excursion and is cleared only by an explicit discard, edit, or reset. Raw-mode
geometry can't desync from the text: loading a `.xyz` in raw mode is refused
unless the text has a `{{GEOMETRY}}` placeholder (embedded coords run
verbatim), and a raw calc's stored charge/multiplicity mirror its own
`* xyz C M` line, not the hidden form fields.

**Creating an MLIP environment (one click).** `orcamgr/mlip/installer.py` is
Qt-free and builds an env the user does not have: `find_base_pythons()` detects
candidate interpreters (the `py` launcher, PATH, and — running from source only
— `sys.executable`; a **frozen** ORCAdesk has no Python of its own, since
`sys.executable` is `ORCAdesk.exe` and cannot run `-m venv`, which is why a base
interpreter is the one manual prerequisite, the analogue of CREST's "a WSL
distro must exist"). `install_plan()` returns the ordered steps — `venv` →
upgrade pip → **torch from the device's own index** (cpu ≈150 MB, CUDA ≈2.5 GB)
→ the backend's pip requirement (`BACKEND_REQUIREMENTS`, keyed like
`MLIP_BACKENDS`). For a GPU env the CUDA index is **chosen from the card's
compute capability** (`detect_gpu()` via `nvidia-smi --query-gpu=compute_cap`
→ `cuda_index_for()` → `CUDA_INDEX_BY_CAPABILITY`, highest match first): a
torch wheel only ships kernels for the architectures its toolkit knew, and too
old a pick fails *late* — it installs, imports, reports the GPU, then dies at
the first kernel launch with `no kernel image is available for execution on the
device` (observed on an RTX 5080, sm_120, against cu124, whose kernels stop at
sm_90). `gpu_name`/`cuda_index` ride on the options payload so the card can name
the build it will fetch. Torch goes in **before** the backend and from a pinned
index because `mace-torch` would otherwise pull whatever plain torch pip
resolves, silently turning a GPU install into a CPU one. `MlipEnvInstaller.run()`
streams every command's output through a callback and is cancellable — the
cancel **terminates the child** rather than waiting for its next line, because
pip is silent for minutes while a 2.5 GB wheel downloads. Pre-flight gates
(a missing base, a CPython outside `MIN_PY..MAX_PY`, a non-empty target dir,
and the **Windows path budget**) return `{ok: False, error}` before anything is
created. The path budget is measured, not guessed (P3): a real
`torch 2.13.0+cpu` install writes 38,469 files whose deepest path relative to
the env root is **189 chars**, so with the default `MAX_PATH` the env dir gets
~70 -- and `%APPDATA%\ORCAdesk\mlip_envs` already spends ~51, leaving ~18 for
the name. Overflow otherwise surfaces as an opaque `OSError` at the *end* of a
finished download (2.5 GB for a GPU env), so `path_budget_error()` refuses up
front and names the number of characters to cut; `long_paths_enabled()` (the
`LongPathsEnabled` registry value) lifts the gate entirely. The device is never
guessed: `has_nvidia_gpu()` only *defaults* the choice and warns. Bridge slots
`get_mlip_install_options` / `get_mlip_install_status` / `create_mlip_env` /
`cancel_mlip_install` follow the probe pattern (background thread + UI polling,
guarded by `_mlip_install_lock`); on success the env is appended to
`Settings.mlip_envs` and probed through the existing `_start_mlip_probe`. Envs
are created under `user_data_root()/mlip_envs/<slug>` (P18 — never the resource
root), the slug sanitized because the name becomes a real directory. Wire
shapes: `MlipInstallOptionsPayload` / `MlipInstallPayload` / `BasePythonPayload`.

**Building MLIP jobs.** MLIP mode hides the whole ORCA build
UI (`_ORCA_BUILD`) and shows a self-contained `#card-mlip`: a name, a MACE-model
dropdown (options in `data/mace_models.json`, served via `load_choices`),
charge/multiplicity inputs (used only by charge/spin-aware models — OMol25 /
multi-head; MACE-OFF/MP ignore them), a **CPU threads** field
(`StepConfig.nprocs`, seeded from `Settings.default_nprocs`) that both caps the
worker and is what the core budget charges the job, and a
**geometry source** selector (`.xyz` loader **or** reference another queued calc,
mirroring the ORCA build card — `onMlipGeomSourceChange`/`refreshMlipRefSelect` in
`app.js`), which add a calc of the kind chosen by the card's Task selector
(`#mlip-task`: `mlip_opt` / `mlip_sp` / `mlip_freq` / `mlip_opt_freq`) to the
**shared queue** through the
same `add_calc`/`calc_from_dict` path as ORCA calcs. The frequency tasks reveal a
temperature/pressure row (`#mlip-thermo-row` → `StepConfig.freq_temp_k` /
`freq_pressure_atm`, reused from the ORCA freq fields) for the ideal-gas
thermochemistry. A **Device** selector (`#mlip-device` →
`StepConfig.mlip_device`: `""` auto / `cpu` / `cuda`) picks the torch device; the
GPU option is enabled only when a ready env's probe reports CUDA (`_mlipCuda`,
`refreshMlipDeviceOptions`). Because the threads field means **different things
per device** — always the worker's cap, but the queue's core charge only off the
GPU (`declared_cores` returns 1 for an explicit `cuda`, which takes the GPU lane
instead) — `onMlipDeviceChange` writes a per-device note into
`#mlip-threads-note` (D2: unlabelled, "CPU threads: 6" beside "Device: GPU"
reads as either a no-op or six reserved cores, and is neither). Every path that
changes the device calls it: the `onchange`, `refreshMlipDeviceOptions` (which
may itself clear a `cuda` value), and `fillMlipForm` **after** it assigns the
value; `resetMlipForm` deliberately leaves the device alone, so the note stays
valid across a reset. A referenced `mlip_opt`/`mlip_opt_freq`
resolves through the very same `_resolve_geometry` path as an opt→freq handoff, so
an MLIP pre-optimization can start from another calc's optimized geometry (e.g. a
CREST best conformer). The model lives on
`StepConfig.mlip_model` (+ `mlip_env_id`, `""` = first ready env — the engine
takes `mlip_envs[0]`, and the Bridge orders that list ready-first via
`_mlip_envs_for_run` / `order_envs_by_readiness`, since readiness is a live
probe result the engine never sees; `mlip_device`);
`build_input` ignores those — an MLIP calc never produces an ORCA `.inp`. `_meta_line` shows
the model instead of charge/mult for `mlip*` kinds, and `CalcSummary` also carries
the model/method as **explicit fields** (`mlip_model`, `crest_method`) so the
desktop queue row can render them escaped (never innerHTML
the pre-joined `meta` — it embeds user-typed ref names). MLIP/CREST calcs are
**editable in place** in their own build cards: `editCalc` routes an `mlip*`/
`crest*` kind to `editBackendCalc`, which loads the full calc (via `localCalcs`
or `bridge.get_calc`), switches to the card, fills it (`fillMlipForm` /
`fillCrestForm`, the inverse of the add functions), then sets `editName` **after**
the mode switch so `setBuildMode`'s own `exitEditMode()` can't clobber the target
— and the card's Add button flips to **Update** (`updateEditUI` now drives
`#mlip-add-btn` / `#crest-add-btn` alongside `#add-btn`). All three add
functions (`addCalcToQueue` / `addMlipCalcToQueue` / `addCrestCalcToQueue`) are
**one implementation of everything but the card's own fields**: `readCalcName`
(non-empty, folder-name safe, unique with the edited calc excluded),
`collectGeomSource(prefix, …)` (the `{xyz, ref_name}` pair, with `requireXyz` /
`requireRef` relaxed for DFT raw mode and previews), and `submitCalc` — the
shared commit tail that re-resolves the edit target **by name** at save time
(the queue may have shifted; a vanished target degrades to a plain add),
calls `update_calc` in place or `add_calc` behind the mid-run overwrite gate,
then logs, resets, refreshes and switches to the Queue tab. `submitCalc`'s
`exitEditOnAdd` flag is DFT-only on purpose: `exitEditMode()` clears `rawText`,
which must survive an MLIP/CREST excursion. `update_calc` is kind-agnostic at the store
(`replace`, gated by `EDITABLE_STATES` and the mid-run protections), so no backend
change was needed. The card is **locked**
(greyed, inputs/buttons disabled, `#mlip-lock-note` shown) until some MLIP env is
ready — `applyMlipLock` in `app.js`, driven by the `get_mlip_status` poll
(`_mlipReady`); `addMlipCalcToQueue` guards on it too.

**Running MLIP jobs.** The run pipeline mirrors `core/` but lives in
`orcamgr/mlip/` (kept off ORCA's path): `runner.py` (`MlipRunner` +
`write_mlip_run_files` + the `MACE_WORKER_SCRIPT`) and `parser.py`
(`parse_mlip_result`). `QueueEngine._run_mlip_calc` resolves geometry (direct or
reference) and the interpreter (`config.mlip_env_id`, else `mlip_envs[0]`, via
`_resolve_mlip_python`), writes an input `.xyz` + a JSON config + the
worker script into the run folder, then runs the user's interpreter on it. The
worker — running in the **user's** env, so it may import `torch`/`mace`/`ase`
(ORCAdesk's env need not) — loads a MACE calculator via `parse_mace_model`,
which maps the dropdown label to `(family, model_arg, head)`: `mace_off` (SPICE,
organic), `mace_mp` (materials + the multi-head `mh-1`/`mh-0`), or `mace_omol`
(the OMol25 model, `extra_large`). `head` is the multi-head selector passed as
`mace_mp(head=...)` — `""` is the model's default head (`omat_pbe` for `mh-1`);
the `MACE-MH-1 omol` label selects `mh-1`'s `omol` head (wB97M-VV10,
organic/organometallic — the best `mh-1` head for molecular / host-guest
energetics). Only `mace_mp` accepts `head`, so the worker passes it only for
that family. Charge/multiplicity flow from the
`Calculation` into the worker as `atoms.info["charge"]` and `["spin"]` — where
**`spin` is the spin multiplicity 2S+1 (the multiplicity itself, not
`mult−1`)**; the OMol25 / multi-head models consume them (ions, radicals) while
MACE-OFF/MP ignore them. The worker resolves the compute **device** itself
(`cfg["device"]`: `cpu`/`cuda`, or `""` = auto → CUDA when the user's torch build
sees a GPU, else CPU) — only the worker's own env can answer that, so the
resolution lives there, never on the ORCAdesk side. It also caps its own CPU
threads to the cores the queue charged it (`threads` in the config ->
`_cap_threads`, setting `OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS` **before** torch
is imported, then `torch.set_num_threads`): torch otherwise takes every core,
invisible one job at a time and a blown budget with two. Per task it then runs an ASE
`LBFGS` relaxation (fmax 0.05) for the opt kinds and/or an ASE `Vibrations`
finite-difference Hessian for the freq kinds; a freq result on a true minimum
(zero imaginary modes) also gets ideal-gas thermochemistry via
`IdealGasThermo` (ZPE / H / G / T·S / U, symmetry number 1 assumed, `spin` =
(mult−1)/2, at `cfg["temperature"]`/`["pressure"]`). It writes the geometry +
energy (+ frequencies + thermochemistry) + convergence to a JSON
result; `MlipRunner` tails its stdout into the `.out` and the live log and is
cancellable (`QueueEngine.cancel`/`detach` forward to the active `MlipRunner`).
`parse_mlip_result` reads that JSON into the **shared `ParseResult`** (geometry,
`final_energy_eh`, `opt_converged`, and — for freq kinds — `frequencies`/
`n_imaginary`/`zpe_eh`/`gibbs_eh`/`enthalpy_eh`/`entropy_term_eh`/
`total_thermal_eh`, rendered on the Results tab like an ORCA freq job), so a
downstream ORCA calc references an
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
in Hartree, energy-sorted) into the shared `ParseResult` — `crest.energies` is
copied back out of WSL but not parsed: the relative energies are computed from
the absolute ones in the `.xyz`, which is the single source of truth — new
`conformers: list[Conformer]` + `is_conformer_search`, with `geometry` /
`final_energy_eh` set to the lowest conformer so the existing best-geometry
reference path still works. `validate_result` requires normal termination
(marker `"CREST terminated normally."`, matched as a substring) + ≥1 conformer.
`queue_needs_orca` excludes `crest*` too, so an all-CREST queue runs with no ORCA
path. Charge/multiplicity come from the `Calculation` (shared with ORCA); CREST's
`--uhf` is the **number of unpaired electrons = multiplicity − 1** (not the
multiplicity). The build card has a **geometry source** selector mirroring the
MLIP card (`.xyz` loader **or** reference another queued calc —
`onCrestGeomSourceChange`/`refreshCrestRefSelect` in `app.js`); a referenced
CREST search resolves through the same `_resolve_geometry` path at run time,
so it can start from e.g. an MLIP- or ORCA-optimized geometry. The pipeline
was validated end to end against a real CREST 3.0.2 install in WSL Ubuntu
(including the conformer→ORCA handoff).

`build_crest_argv` (`crest/runner.py`) maps `StepConfig.crest_*` to CLI flags:
method (the label maps 1:1 onto CREST's flag — `gfn2` → `--gfn2`, incl.
`gfn2//gfnff`; an already-dashed value passes through verbatim), solvent
(`--alpb`/`--gbsa` by `crest_solvent_model`),
`--ewin`, `-T`, plus the optional advanced knobs — `crest_preset`
(`--quick`/`--squick`/`--mquick`), `crest_nci` (`--nci`), the MD/MTD numerics
(`--mdlen x<mult>`, `--tstep`, `--tnmd`, `--mddump`, `--vbdump`), and the
`--cbonds`/`--subrmsd`/`--norotmd`/`--keepdir`/`--cluster` toggles. Each advanced
flag is emitted only when set to a non-default value; the enum/numeric fields are
validated and clamped in `StepConfig.from_dict` (the trust boundary). File-based
options (`--cinp` constraints) and standalone modes (`--cregen`) are intentionally
out of scope. UI: the CREST build card + a collapsible "Advanced settings"
(`.adv-section`).

**Conformer → follow-up pipeline.** Follow-up MLIP/ORCA calculations are built
on the **Build tab** by referencing the CREST calc through the normal
geometry-source dropdown; a reference always receives the **lowest-energy
conformer** (the classic single-geometry handoff via `_resolve_geometry` —
`parse_crest_result` sets `geometry`/`final_energy_eh` to the best conformer).
The per-conformer track fan-out (`crest_handoff="all"`, `expand_conformer_tracks`
/ `substitute_calcs`) was removed in 0.6.0-beta. The Results tab's conformer
list is **read-only** for building: results are for interpretation; building
happens on the Build tab.

**Per-conformer `.xyz` are exported automatically on finish.** When a
`crest_conf` search reaches DONE the engine
(`QueueEngine._export_crest_conformers`, at the single finish point
`_crest_monitor_and_finish` — so it covers a fresh run, a mid-run reattach, and
the finished-while-closed judge) splits `crest_conformers.xyz` into one
standalone `.xyz` per conformer (verbatim frames — count + energy comment +
coords) under a `conformers/` subfolder of the run, named `{name}_c{k}.xyz`
(zero-padded; `c1` = the best conformer, == `crest_best.xyz`). A search judged
DONE at startup that never went through the engine (finished while the app was
closed, never re-run) gets the same split via `store._auto_export_crest` inside
`reconcile_calcs`. The export is **best-effort** everywhere: a missing/empty
ensemble or a write error is logged (engine path) or swallowed (reconcile path)
and never turns a completed search into a FAILED one. The
Results tab still offers **Export as .xyz** (`bridge.export_conformers` →
`crest/export.py`, the same `export_conformers` split), now just a manual
re-run of the automatic export. No file dialog — the files land next to the
run; this only reads/writes, never touches the queue.

**In-app 3D structure viewer.** The Results tab renders structures with
**3Dmol.js** (vendored at `web/vendor/3Dmol-min.js` — bundled locally, no CDN;
this is why WebGL is enabled in `window.py`). It opens as a modal over the
Results tab (`#mol-viewer` in `index.html`, `openMolViewer`/`molViewerShow`/
`closeMolViewer` in `web/molviewer.js`) and flips through a list of **frames** with the
←/→ keys (Esc closes, drag rotates). A frame is `{label, xyz, energy}`; `xyz`
is raw `.xyz` text 3Dmol parses directly, and the caption shows ΔE vs the
lowest-energy frame when energies are present. Every entry point is a **Visual**
row (see the Results-tab note): the parsed structure, or one of the `.xyz` sets
discovered beside the result, opened through `bridge.get_structure_frames(path)`
— one slot for a file or a folder. Frames are read on demand (never in the
result payload, so a 100-conformer render stays light) by the Qt-free
`orcamgr/molview.py` (`frames_from_file` / `frames_from_folder` /
`discover_structure_sets`, unit-tested). The GLViewer is created lazily on first
open and reused; closing clears the scene but keeps the WebGL context.

**Viewer favorites (starred structures).** In the viewer the user stars
structures worth following up (the **F** key or the list star); stars persist
across sessions via the Qt-free `orcamgr/favorites.py` (a small
`favorites.json` in `user_data_root`, **not** `settings.json` — which is
rewritten on every queue mutation), keyed by a namespaced *source*:
`"calc:<name>"` for a queued calculation's own CREST ensemble,
`"folder:<path>"` for any other structure set, so two sources never share a star
set. That is why a queued ensemble is opened as kind `"calc"` even though its
frames now come from the generic `get_structure_frames`: stars set before the
Visual mode existed still resolve. **★ only** steps the ←/→ keys through
starred frames alone; **Export ★** writes them to a `favorites/` subfolder next
to the source. Bridge slots: `get_favorites(source)`, `toggle_favorite(source,
label, on)`, `export_frames(dest_kind, dest, frames_json)` — `export_frames`
takes the frames' xyz straight from the front-end (favorites are few) and
sanitizes each label into a filename, so it needs no re-parse.
`get_structure_frames` returns a `folder` (the set's own folder, or a file's
parent) — the export/favorites destination.

**Orbital / electron-density surfaces (the viewer's second mode).** The same
modal and the **same GLViewer instance** render volumetric data: `_mvMode` is
`"frames"` (the .xyz list above) or `"volume"`, and `renderMvList` /
`molViewerShow` / `molViewerStep` dispatch on it, so one WebGL context serves
both. Entry is the **Orbitals & density** row in Visual
(`viewOrbitals3D(_visOrbitals())`) — the front-end passes the orbital list it
already parsed rather than the payload carrying a second copy (P4);
`_visOrbitals()` falls back to the payload's own list because Visual is not
gated by calc kind the way Output's sections are.

Cubes come from **`orca_plot`**, shelled out post-hoc against a finished run's
`.gbw` (`orcamgr/core/plot.py`, Qt-free): nothing is recomputed and no job is
re-run, so a DONE calc from any earlier session plots. The wavefunction is
addressed by a **source string** — `calc:<name>` (folder via `_calc_run_dir`,
multiplicity from the `Calculation`) or `file:<path>` (folder = the file's
parent, base = its stem, multiplicity read back out of the `.out` by
`parser.read_multiplicity`) — resolved in `Bridge._plot_source`. So a result
that was never in the queue, or came from outside ORCAdesk entirely, plots the
same way. The prefix is required, not sniffed: a Windows path always contains
the `:` that a calc name never can. Measured on ORCA 6.1.1
(52 atoms / 987 basis functions): one MO at 60³ = **0.17 s / 3.1 MB**, an SCF
density over the same grid = **9.9 s**; a grid is 0.9 / 3.1 / 7.3 MB at
40 / 60 / 80. `orcamgr/cube.py` reads only the cube **header** and hands the
file through verbatim — 3Dmol parses cube text itself, and ORCA writes the
*orbital* variant (negative atom count + an extra MO-index line), which 3Dmol's
own parser already handles.

Two things about that shell-out are load-bearing:
- **`orca_plot` is driven through its interactive menu, not the advertised
  `plot-inputfile`.** That file's parser reads ~17 positional fields in an
  undocumented order; the menu sequences in `_SEQUENCES`/`_menu_sequence` were
  each *run* against the real binary and confirmed (`tests/test_plot.py` pins
  them character for character, P56). Verified: MO = `5,7,4,G,1,1,3,op,2,n,11,12`;
  density/spin = `5,7,4,G,1,2|3,y,11,12` — the `y` answers a density-filename
  prompt the MO path never sees, and **omitting it desynchronizes the run**,
  after which orca_plot spins forever on EOF printing `Invalid input`
  (observed: 3 min of CPU and 2 GB of accumulated output). Hence the bounded
  read + hard timeout + explicit desync check in `_run_bounded`.
  ESP is a **third** shape and shares neither's assumptions:
  `5,7,4,G,1,43,<base>.scfp,11,12` — plot type 43 is `Electrostatic Potential`,
  and its density prompt takes the name **typed in full**, not a y/n. An empty
  answer draws `Wrong Density Name selected` and then the same endless loop,
  which is why `_BAD_DENSITY` is checked *before* `_DESYNC`: otherwise the
  generic "this ORCA build's plot menu differs" sentence blames the wrong thing.
- **The stored filename is grid-qualified** (`{name}.mo4a.g60.cube` in a
  `cubes/` subfolder), because orca_plot's own name is not: it names a plot by
  *what* it is, so the reuse check would otherwise hand back a 60³ cube under
  an 80³ label. `plot_output_name()` is what orca_plot writes,
  `cube_filename()` what we keep; `web/molviewer.js` `_mvCubeName` mirrors the
  latter so the list's "already generated" dots track the selected grid. ESP is
  the exception to *what orca_plot writes*: it names the file after the DENSITY
  it was computed from, so the file is `{name}.scfp.esp.cube`, not
  `{name}.esp.cube` — get it wrong and every ESP looks like a failed plot,
  because the path the code looks for is never there.

**The ESP map is built from two cubes, and has its own entry point.** A
potential is not an isosurface of itself; the figure everyone means is the
potential *painted on* an electron-density surface, so the map is `eldens`
(shape) + `esp` (colour) at the same grid — 3Dmol's
`addIsosurface(dens, {voldata: esp, volscheme})` samples the second field per
surface vertex. `_mvEspParts()` issues the two requests in order through
`_mvFetchCube` (the single-cube generate/poll/fetch loop, extracted for exactly
this); both must ride the ESP grid or they would be two different boxes.

It is deliberately **not** a row in the orbital viewer's pick list: it is one
figure, not a set to step through, and it answers a different question. It is
its own **Visual** row with its own opener, `viewEspMap()`, which shows a single
pick with the frame list put away. `_mvVolSetup()` is the shared
options-fetch-and-adopt both openers use, so neither can drift from the other's
idea of the grid choices or the ESP conventions (P4). Its row is also the one
that carries an on-disk dot and reads *Plot* rather than *View* — the cost is
minutes, so the button names it before the click and then reports the wait
(`visOpen`). Consequences worth knowing:
- The isovalue slider steers the **density** level here, opening at
  `cube.ESP_SURFACE_ISOVALUE` (0.002, the van-der-Waals-like contour) — *not*
  the enclosed-fraction fit, which exists because an orbital's peak varies with
  delocalization, while a total density's 0.002 contour is the same physical
  surface in every molecule. A second slider sets the colour half-width
  (`ESP_DEFAULT_RANGE`, ±0.05 a.u.); both re-draw in place.
- **The grid is remembered per kind** (`_mvGridByKind`), because ESP costs
  minutes where an orbital costs a second: measured **49.3 s at 40³** on that
  same 52-atom system (~2.8 min at 60³, ~6.6 min at 80³), and 0.2 s on water at
  def2-SVP — it scales with the basis as well as the grid. `DEFAULT_GRIDS` in
  `plot.py` opens ESP at 40³ and `default_grid_for()` is the accessor; the busy
  card's copy is per-kind so neither wait is described by the other's sentence.

Rendering separates **sampling** from **smoothness**: an MO is a sum of
Gaussians, so a coarse grid is missing *mesh*, not physics. 3Dmol's marching
cubes gets `smoothness: 8` (Laplacian mesh smoothing) and the isovalue slider
re-meshes the `VolumeData` already in memory — no backend round-trip — which is
why 60³ is the default and the grid selector is the rare escape hatch.
Per-kind display defaults live in `cube.py` (`DEFAULT_ISOVALUES`,
`SIGNED_KINDS`: mo/spindens draw a ± pair, an electron density one surface) —
but `DEFAULT_ISOVALUES` is a **ceiling, not the opening value**. 0.05 is the
convention for a *localized* orbital, which is not the same as for an orbital:
CB8's HOMO peaks at 0.0996, so at 0.05 only 222 of 216,000 points clear the
level and the surface is invisible — the viewer looked broken on exactly the
molecules worth looking at. `_defaultIso` (molviewer.js) therefore fits the
level enclosing 90% of ψ² from the values 3Dmol has already parsed and takes
`min(convention, fit)`; measured, that is 0.060 for water's HOMO (cap holds,
conventional picture unchanged) and 0.0097 for CB8's. It never raises above
convention. The fit must be **NaN-safe**: 3Dmol builds its value array with
`Float32Array.from(split(...), parseFloat)`, and a cube's trailing separator
leaves one NaN past the data (216001 entries for a 60³ grid) — one NaN poisons
a sum and silently voids the whole fit. While orca_plot runs for a pick chosen
*inside* the viewer, the stage shows a *Plotting…* overlay (`#mv-vol-busy`); a
cube already on disk opens with no flash. The overlay belongs to that path only
— the wait for the pick a Visual row *opens with* is reported on the row's own
button instead, so the viewer never covers the window with an empty stage.
Measured cost of what it covers: a density on a 144-atom / 3648-BF system is
22 s at 60³, against 0.34 s for one MO.

**Bridge slots**: `list_structure_sets(source)` / `get_structure_frames(path)`
(the Visual mode's discovery and its one opener),
`pick_result_file()` / `pick_result_folder()` (the tab's two pickers: a file
routes by suffix, a folder resolves to the result inside it),
`get_plot_options(source)` (base, has_gbw, the kinds this
wavefunction supports — spin density only when the run is open-shell — grids,
and the cubes already on disk), `generate_cube(payload)` / `get_cube_status()` /
`get_cube_data()`. Generation is a background thread + UI polling like the MLIP
probe, guarded by `Bridge._cube_lock`; the ~3 MB cube rides `get_cube_data()`
**once**, never the poll. Only one orca_plot runs at a time (it writes a fixed
filename beside the `.gbw`, so two would race), and a refused request returns
the *in-flight* job's status — so the front-end checks `_mvJobIs(job, pick)`
before adopting a result, or it draws the previous orbital under this one's
label. Wire shapes: `PlotOptionsResult` / `CubeJobPayload` / `CubeDataResult`.

**Bridge slots** (`get_crest_status` / `check_crest` / `install_crest` /
`set_crest_distro`) follow
the MLIP pattern: a background probe publishes to `Bridge._crest_status` (guarded
by `_crest_lock`) and the UI polls `get_crest_status`. `install_crest` runs off
the click too, so its outcome rides back on `CrestStatusPayload.install_error`
(`""` = none) — the installer's diagnostics would otherwise reach only the Log
tab, and the Install button reads as dead. A plain re-probe publishes no
`install_error`, so **Re-check clears a stale one**: the field describes the last
install *attempt*, not the current state. The button itself disables whenever
there is nothing to install into (no WSL, no distro, or a probe in flight) —
creating a distro needs a Linux account and is the one step ORCAdesk cannot
script (D41). The Build tab gains the
`crest` mode (`Settings.build_mode`; the CREST button on the backend toggle — see
"Build-tab modes" — the button is hidden until CREST is set up), a locked
`#card-crest` (until a
distro has CREST), and a **Settings → CREST** distro picker + Install button
(that section is unconditional — it is the way in when the button is hidden). Wire
shapes: `CrestStatusPayload` / `CrestDistroPayload` / `ConformerPayload` in
`state/schemas.py`, mirrored in `web/types.js`.

### Text that Windows does not write in UTF-8

`orcamgr/textio.py` is the one place that judgment lives (Qt-free, stdlib-only,
so `core/`, `mlip/`, `gui/` and the installers can all share it — P4):

* `decode_process_output(bytes)` — UTF-8 if valid, else the ANSI code page, else
  UTF-8 with replacement. A CPython child (`pip`, `python -m venv`, the MLIP
  worker) encodes its pipe output with `locale.getencoding()`, and console tools
  (`where`, `py -0p`) print in the console code page — so those callers pass
  **bytes** (no `text=True`, no `encoding=`) and decode here. It never raises: a
  diagnostic that cannot be read still beats an exception thrown while
  reporting an error.
* `repair_ansi_line(str)` — for the genuinely mixed file (the ORCA `.out`), read
  with `surrogateescape` and repaired per line. A line with no surrogates is
  already valid UTF-8 and is returned untouched.

WSL is deliberately NOT in this: `crest/wsl.py` sets `WSL_UTF8=1`, so its output
really is UTF-8. Picked/dropped `.inp` and `.xyz` files are read `utf-8-sig`,
because Notepad/VS Code/PowerShell write a BOM freely and ORCA refuses an input
that starts with one.

### Handing a file to the user's own programs

`orcamgr/openwith.py` is a Qt-free two-verb module: `open_with_default(path)`
(the OS file association) and `show_in_folder(path)` (reveal it, selected).
Bridge slots `open_path_external` / `show_path_in_folder` are thin wrappers;
both return `OkResult` and log the refusal, because a missing association is a
normal thing for a user to have (P6).

P5 governs the shape: an external program is **launched, never merely named** —
telling the user which file to open is the command line by another route. So
the setting is `Settings.viewer_target` (`"in_app"` default | `"system"`), which
changes **where a Visual row's click ends**, never what ORCAdesk does first:
orca_plot still runs, the same cube at the same grid, opening on the same
orbital. Choosing an external viewer must never mean getting less.

**`OPENABLE_SUFFIXES` is a trust boundary, not tidiness.** These paths arrive
from the front-end, and `os.startfile` on a path is double-clicking it — handed
an `.exe` it would *run* it. Opening is therefore default-deny against a list of
data formats, so no list of dangerous extensions has to be kept correct.
Revealing has no such hazard (the file manager selects, it does not execute) and
accepts any existing path, falling back to the parent when the file is gone.

Front-end (`web/results_render.js`): `viewerTarget()` reads the setting live,
`visOpenExternal(row)` is the external counterpart of a row's in-app action, and
`visRow(key)` re-derives a row from its key — the row cannot ride through an
HTML attribute, since a calc name may hold a quote (the same trap `data-job`
avoids in the Log tab). Rows carry `path` when they already *are* a file on
disk; the rest produce one:
- **Structure** — the one row backed by no file (the geometry lives in the parse
  payload), so `bridge.save_structure_xyz` writes `<base>_structure.xyz` from the
  front-end's own `geomToXyz` (one definition, P4).
- **Orbitals** — `orbitalCubeForExternal` runs the same generation and returns the
  cube *path* via `_mvCubePath`. `_mvFetchCube` was split into `_mvRunCube`
  (generate + poll, returns the job) and the data read, so the external route
  never pulls a 60³ cube's megabytes across the bridge for nothing.
- **ESP map** — `espCubesForExternal` generates both cubes and the caller
  *reveals* the potential one. An ESP map **is** two cubes (a density surface
  coloured by a potential); opening one and calling it the map would be a quiet
  lie, so the pair is shown instead.

`PlotOptionsResult.folder` and `CubeJobPayload.path` exist for this: the
front-end holds a calc *name*, not a path, and must never assemble a workspace
path itself (P4).

### NBO analysis (ORCAdesk's own, in process)

`orcamgr/nbo/` is a dedicated package, kept **out of** `core/` like `mlip/` and
`crest/` — but for the opposite reason. Those two are backends that *run*
calculations; this one runs nothing. It post-processes a wavefunction a finished
run already produced, the way `core/plot.py` post-processes a `.gbw` into a
cube, so it touches neither the queue engine nor the store and adds no calc
kind. Qt-free, numpy and nothing else.

The motivation is licensing: ORCA's `! NBO` is an *interface* that writes a
FILE.47 and shells out to the user's separately licensed `gennbo`. The methods
are published (Reed/Weinstock/Weinhold *JCP* **83**, 735 (1985);
Reed/Curtiss/Weinhold *Chem. Rev.* **88**, 899 (1988)) and are reimplemented
here from those papers. **Numbers are ORCAdesk's own and are never to be
presented as NBO-program-compatible** — the occupancy-weighted orthogonalization
at the heart of NPA is not specified to the last detail in the literature, so
small differences from NBO 7 are structural, not bugs. See P5 (amended for this
package) and appendix A25.

`wavefunction.py` is the bottom layer and the whole reason the package is
feasible without a chemistry stack. A Molden file (`orca_2mkl <base> -molden`)
stores only `C`, `e` and `n`, but the SCF relations `C^T S C = I` and
`F C = S C e` invert in closed form when `C` is square, which is what
`orca_2mkl` writes:

```
Cinv = inv(C);  S = Cinv^T Cinv;  F = Cinv^T diag(e) Cinv;  P = C diag(n) C^T
```

One inversion yields both S and F and **no integrals are ever evaluated** — so
no libcint, and therefore no PySCF, in the frozen build. Because the input is
the `.gbw` every finished run already leaves behind, analysis is **retroactive**:
any past calculation can be analysed without re-running it. FILE.47 carries S/P/F
explicitly and is a fine input too, but it exists only for jobs run with
`! NBO`, so Molden is the primary path.

Invariants worth keeping:
- **`C` must be square.** ORCA drops near-linearly-dependent basis functions in
  very diffuse basis sets; `overlap()` then raises `WavefunctionError` rather
  than returning a plausible wrong S.
- **ECP atoms read their charge from `[Pseudo]`, not `[Atoms]`.** Molden reports
  iodine's true Z (53) while its density accounts for 25 electrons, so skipping
  `[Pseudo]` would put every charge on such an atom out by the whole core.
- **Mulliken/Löwdin live here as the ground truth, not as a feature.** NPA exists
  because they misbehave in large basis sets — but ORCA prints both in every
  `.out`, which makes them the one thing this layer can be checked against
  *exactly*. `tests/test_nbo_wavefunction.py` reconstructs the matrices from a
  Molden file and compares the charges they imply against ORCA's own, read back
  through `core/parser.py`; agreement to ORCA's six-decimal print precision is
  what pins the basis ordering, the occupations and the inversion all at once.
  Fixtures (`tests/nbo/fixtures/`) are real ORCA 6.1.1 output covering restricted
  (`h2o`), unrestricted (`h2o_cation` — which also pins ORCA writing
  `Spin= Alpha` but `Spin=Beta`) and ECP (`hi`).
- **`consistency()` is the self-check** every layer above should assert on:
  orthonormality residual, the eigenvalue residual of the recovered F, the
  electron count, and — when unrestricted — that the beta orbitals imply the
  same S as the alpha ones, an independent derivation of the same matrix.

`nao.py` builds the **natural atomic orbitals** and the NPA charges on top:
pre-NAOs (diagonalize each `(atom, l)` block, averaged over `m` so degenerate
shells stay degenerate) → split into the natural minimal basis (the shells the
free atom occupies) and the Rydberg set → **occupancy-weighted symmetric
orthogonalization** of the minimal basis → Schmidt the Rydberg set against it,
then OWSO it too → re-diagonalize each atomic block. `natural_atomic_orbitals(wf)`
returns coefficients, occupancies, per-orbital labels (`"O 2p"`, `"I 5p"`,
`"C Ryd(d)"`), the minimal-basis mask, NPA charges and NAO spin populations;
`npa_charges(wf)` is the one-line form.

Three things here are easy to get wrong and were:
- **The population operator is `Q = S P S`, not `P`.** `Q c = S c n` *is* the
  natural-orbital equation, so its eigenvalues are occupancies bounded by 2.
  Diagonalizing `P` against `S` gave oxygen a 1s holding 3.02 electrons.
- **The density transforms contravariantly**: `P' = T⁻¹ P T⁻ᵀ`, not by the
  overlap's law `Tᵀ P T`. With the wrong one benzene's occupancies summed to
  9515 instead of 42. Both bugs surface in `consistency()` as an electron count
  that is not the electron count — which is why tests assert on it.
- **An ECP atom's minimal basis must drop the replaced shells** (`_ECP_CORES`,
  keyed by the electron count the potential replaces, each entry's shells
  summing to its own key — a test pins that). Iodine keeps 4s/5s/4p/5p/4d;
  counting from 1s would push real valence shells into the Rydberg set. An
  unrecognized core is refused, never guessed.

`population.py` reads chemistry off that basis, and is short because the NAO
layer did the work. **Wiberg bond orders** are the summed squares of the
NAO-basis density between two atoms' orbitals — a formula that means nothing in
a non-orthogonal basis, which is why ORCA's Mayer bond orders (overlap-corrected
instead) are a different number for the same bond, and why a paper asking for
"Wiberg index in the NAO basis" wants this one. `natural_valences` sums a row;
`wiberg_bonds` filters by `BOND_THRESHOLD` (0.1 — deliberately inclusive, so
1,3 neighbours do come through; `wiberg_bond_orders` returns the whole matrix
and judges nothing). **`atom_populations`** gives the per-atom table: charge,
the core/valence/Rydberg split, and the natural electron configuration
(`[core] 2s( 1.75) 2p( 5.12)`).

The core/valence split is `core_shell_counts` — the preceding noble gas, counted
in **absolute** shells (1s is the 1st s, 4s the 4th) and therefore *not*
ECP-adjusted: a replaced shell is core and is already absent from the basis, and
subtracting it again left iodine with no core at all. The rule puts a transition
metal's (n−1)d and ns both in the valence, where chemistry wants them; its one
arguable result is iodine's filled 4d landing in the valence (4d is not in
krypton) where some programs call it core. Only the subtotals move — every shell
is printed with its own occupancy, and no charge or bond order depends on it.

Bond orders are the one part of this package with unambiguous right answers, so
they are tested against the answers: N₂ = 3.03, HI = 0.99, water O–H = 0.81,
benzene C–C = 1.44, aspirin's two carbonyls 1.81/1.71. The strongest check is
that natural valences reproduce the periodic table without being told it —
aspirin's carbons come out 3.83–4.03, oxygens 2.01–2.19, hydrogens 0.75–0.95.

**Validation is by property, not by table.** There is no NBO program here to
compare against and the weighted orthogonalization is under-specified in the
literature, so "matches NBO" is not claimed. What is tested is what the method
exists for: the same water molecule at def2-SVP and def2-TZVP must give nearly
the same NPA charge (0.036 e) where Mulliken lurches (0.339 e). Conservation,
occupancies ≤ 2, and >99% of the density in the minimal basis hold across the
fixtures and across 12 real corpus wavefunctions up to 1644 basis functions
(~10 s at that size; 0.08 s at 117).

`lewis.py` finds the **Lewis structure** in the NAO-basis density and completes
it into a full orthonormal NBO basis; `perturbation.py` turns that into the
second-order donor→acceptor table (`ΔE(2) = −q·F²/Δε`, kcal/mol) that is what
people mean by "NBO analysis". The search is cores as they stand → lone pairs
(one-atom blocks) → bonds (two-atom blocks), repeated, at a threshold; a bond's
two halves are the atoms' **hybrids** (`sp2.48`) and their orthogonal
combination is the **antibond**. Three decisions here were each forced by a
failure and are load-bearing:
- **Accepted orbitals are projected out (`Q P Q`), not subtracted.** The
  papers' depletion leaked: a lone pair the threshold had just missed on its
  own atom reappeared in the next diatomic block as a rotated copy with 16%
  borrowed carbon and was accepted as a third "bond" — the orbitals overlapped
  by 0.17, i.e. several descriptions of one density. Projection makes that
  impossible and gives orthonormality to 1e-16 with no cleanup step.
- **The threshold is a ladder (1.90 … 1.30) and the first *complete* rung
  wins** (one orbital per electron pair, or per electron for one spin). A
  delocalized system has no orbital above 1.90 in any diatomic block —
  benzene's Kekulé π bonds hold 1.66, an amide's N lone pair 1.73 — so the
  ladder descends; because projection makes over-assignment impossible, the
  first complete structure is the best one. Formamide has three candidate
  resonance forms and this picks the canonical amide at 1.70; "most Lewis
  electrons" picked the zwitterion.
- **Open shells get one Lewis structure per spin** (thresholds halved). The
  total density with a two-electron threshold cannot see an unpaired electron
  (H₂O⁺ came out four orbitals of five); the β structure is legitimately a
  lone pair short, and its hole shows up as an empty `LP*` on the oxygen.

The non-Lewis space is built **rank-exact**: its rank is `n_minimal − n_lewis`
the moment the Lewis structure is, antibonds are symmetrically orthogonalized
when independent and Gram-Schmidt-ed when not (renormalizing a rank-deficient
symmetric step produced a basis 0.8 off orthonormal), and leftover `LP*`
orbitals are the eigenvalue-1 directions of the remaining projector, read once
(a per-atom loop counted one direction from two atoms: 773 orbitals for 772
functions). The search runs inside the minimal basis — aspirin took 36 s over
the full 451 functions and 0.2 s over its 73 minimal ones.

Validation is against Lewis structures every chemist can draw, plus the one
number the package exists for: water (2 LP, 2 BD at 72% O, `sp3`), N₂ (2 LP,
3 BD at 50/50, one σ + two pure-p π), HI, a Kekulé benzene (π\* each 0.33 e),
and formamide — LP(N) 1.73, a pure-p C=O π bond with **0.26 e in π\***, and
**LP(N) → π\*(C=O) = 63.7 kcal/mol** where the literature puts amide resonance
at 55–65; benzene π→π\* 32.3 × 6 by symmetry. **A strongly delocalized ring can
legitimately come out incomplete** (a substituted aromatic H-bonded to an
acid catalyst: 87 of 88, one ring π unassignable at any rung) — that is
reported as a warning with the best structure, never papered over; the NBO
program reports partial structures for such cases too. 1644 basis functions:
~7 s for the whole search.

`source.py` is the way **in** and `analysis.py` the way **out**, so nothing in
between has to know where a wavefunction comes from or who is asking.
`wavefunction_for(orca_path, run_dir, base)` converts the run's `.gbw` with
`orca_2mkl` (found by `config.orca_tool`, lifted out of `core/plot.py` now that
two callers want a sibling executable) and caches the Molden file **beside the
run** — where a user hunting for something to open in Avogadro also finds it
(P5). Reuse is by mtime against the `.gbw`, because a re-run rewrites it and a
stale Molden would describe the previous run.

`analysis_for(orca_path, run_dir, base)` is the single call a bridge slot needs:
it returns an `NboAnalysis` (charges, per-atom populations with configurations,
bonds, the minimal-basis NAO list, diagnostics, `warnings`) and caches it as
`<base>.nbo.json`. Measured on aspirin: 2.7 s cold, 0.004 s cached. Two things
invalidate the cache and both matter — the `.gbw` mtime (a re-run must not be
served the old wavefunction's numbers) and `CACHE_FORMAT` (an algorithm change
must not mix two implementations' numbers across one queue). Reads never raise:
a corrupt cache is a reason to recompute, not to fail (P6). The NAO coefficient
matrix is deliberately **not** cached — `nbf²` floats, and nothing on screen
needs it.

**Bridge slots** `run_nbo_analysis(source)` / `get_nbo_status()` /
`get_nbo_result()` follow the cube-generation shape exactly: a background
thread, one job at a time (the analysis converts the `.gbw` and writes a
cache beside the run, so two on one folder would race), the status polled,
the result fetched once. A refused start hands back the in-flight job's
status, which names *its* `source` — the front-end checks that before
adopting a result, or it would render one calculation's tables under
another's name. `web/nbo_render.js` caches by source string for the session
(`_nboCache`), so re-rendering Output never re-runs anything; the backend's
own on-disk cache makes the first click on an already-analysed run ~4 ms.
Wire shapes: `NboJobPayload` / `NboResultPayload` (+ the atom / orbital /
interaction / Lewis rows) in `state/schemas.py`, mirrored in `web/types.js`.

**An NBO can be drawn in 3D, and orca_plot cannot draw it.** orca_plot plots
only what the `.gbw` holds; an NBO is ORCAdesk's own vector. So `nbo/grid.py`
evaluates the basis itself — numpy only, chunked by x-slab so a 60³ grid
never forms the `(points × nbf)` matrix (3.2 s at 1644 basis functions) —
and writes the same orbital-variant cube orca_plot writes, on **orca_plot's
own box** (bounding box ± 7 Bohr, `BOX_MARGIN_BOHR`) so the two kinds of
cube coincide. Two conventions in it are *measured*, not assumed, by
fitting orca_plot's MO cubes of a water molecule rotated off every symmetry
axis back onto the evaluator (residual 1e-6 against five printed digits):
ORCA's Molden coefficients carry `(2α/π)^¾ (4α)^(l/2)` and the Racah solid
harmonic is divided by `√((2l−1)!!)` — except that the **g** coefficient is
written `1/√3` smaller, so g divides by `√35`; and **f(±3), g(±3), g(±4) are
sign-flipped** relative to the standard real harmonics (`_ORCA_SIGN`).
`tests/test_nbo_grid.py` keeps two of those cubes as fixtures and reproduces
them to 5e-6; h functions and Cartesian d/f are refused, never guessed.
None of this touches the analysis, which never evaluates a function.

`nbo/cubes.py` is the in-process twin of `core/plot.generate_cube`, and the
request rides the **same** cube pipeline: `kind: "nbo"` is a `CUBE_KINDS`
member named in `core/plot.py` (`{base}.nbo7a.g60.cube`, so `_mvCubeName`
mirrors one rule) but not a `PLOT_KINDS` one — `_menu_sequence` raises and
`generate_cube` refuses it, and `bridge.generate_cube` branches to
`generate_nbo_cube` under the same `_cube_lock`, status and fetch-once
data path. `index` is the orbital's position in its spin's `NboBasis`
(`OrbitalRow.index`, cache format 3) and `operator` the spin, exactly as an
MO is addressed. Because that index is a position the Lewis search
assigned rather than a physical label, reuse is stricter than for an MO
cube: the file must be newer than the `.gbw` **and** its first line must
name the analysis format that wrote it (`GENERATOR_LINE`). The AO-basis
vector comes from `NboOrbitalSet` (`analysis.py`: wavefunction + NAO + the
per-spin bases, `ao_coefficients = C_NAO · C_NBO[:, k]`), which the bridge
keeps in memory for the last analysed result (`_nbo_sets`, one entry,
checked against the `.gbw` mtime; `analysis_for(keep=…)` hands it over when
a fresh analysis builds one, so the first cube after an analysis costs no
second search; a cube for another result rebuilds it in seconds). The card's
Lewis table gets a *View* button per orbital (`nboOrbital3D` in
`nbo_render.js` → `viewNboOrbitals3D` in `molviewer.js`, the spin's whole
list as picks so ←/→ step through them); it follows `viewer_target` like a
Visual row (`nboCubeForExternal` + `openExternally`), and reports the wait
on the button that was clicked without re-rendering the tables.

`warnings` is the honest half: below 99% of the density in the minimal basis the
molecule is not atoms-plus-corrections and the analysis says so rather than
degrading quietly (P2). The floor sits below where this implementation normally
lands so it flags a real problem, not every result.

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
  `CHANGELOG.md` / `README.md`). The one version that cannot derive from it is
  `installer.iss`'s `MyAppVersion` / `MyAppVersionFull` — Inno Setup cannot
  read `paths.py` — so those two constants are part of the release bump and
  are worth checking whenever `APP_VERSION` moves (they had fallen two
  releases behind).
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
- **Theme variants (shadcn / Liquid Glass).** Orthogonal to light/dark, the UI
  has a second *variant* selected by `html[data-theme-variant]` (`shadcn`, the
  flat default, or `liquidglass`) plus an intensity `html[data-glass]`
  (`restrained`/`moderate`/`bold`/`vivid`/`maximal`, mapping to the design2..6
  previews). `applyThemeVariant` in `web/appearance.js` flips those attributes; the whole
  Liquid-Glass CSS layer (an `--lg-*` token group + refracting `backdrop-filter:
  url(#lgLens*)` chrome over a `<canvas id="lgWall">` wallpaper) is **gated on
  those attributes**, so shadcn is untouched when off. Persisted as
  `Settings.theme_variant` / `glass_level` / `wallpaper`; the custom wallpaper
  image is stored outside `settings.json` (a `user_data_root` file via the
  `set_wallpaper_image` / `get_wallpaper_image` bridge slots) to keep the settings
  file small. Controls live in **Settings → Appearance** and persist on
  interaction (like the ☽ light/dark toggle). **Compositor resilience is
  binding** (DESIGN.md §16.5): the SVG lens is chrome-only (top bar + tab strip;
  cards frost with native blur), small/numerous controls never get
  `backdrop-filter` (tint only), every backdrop chain lives on a `::before`
  overlay, not the load-bearing element — too many backdrop layers made Qt
  WebEngine drop the chrome bars from the on-screen composite (the 0.5.0-beta
  invisible-tab-strip bug) — and, because external GPU events (sleep/resume,
  driver reset) can still drop a bar's layer permanently (a static bar is never
  re-invalidated), `appearance.js` pulses an imperceptible `--lg-pulse` paint delta
  through both bars every 250 ms so any dropped layer re-rasters within ~0.25 s
  (§16.5 rule 4; the pulse must stay paint-only — never `will-change`/
  `transform`, which would break backdrop sampling). Full spec: DESIGN.md §16.
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
- **Composite ("3c") methods bring their own basis; never append one.** HF-3c,
  PBEh-3c, B97-3c, B3LYP-3c, r2SCAN-3c and wB97X-3c are functional + basis +
  gCP + dispersion parameterized *together*, and ORCA treats an explicit basis
  on the `!` line as an override — so appending the picker's default runs a
  different method that terminates normally and validates DONE. `_keyword_line`
  drops the basis (and `_auto_aux` the aux, which is chosen from it) when
  `is_composite_method()` says so, and `build_input` writes a `#` note into the
  `.inp` saying why (P2: reported, not silently swallowed). Detection is the
  `-3c` suffix, not a closed list — the picker lists are not closed enums.
  Measured on ORCA 6.1.1 (water, sp): `! r2SCAN-3c def2-TZVP` → def2-TZVP,
  43 bf, −76.417967 Eh; `! r2SCAN-3c` → def2-mTZVPP, 34 bf, −76.418907 Eh.
  The RI keyword is kept — the same measurement shows it is inert here.
- **Dispersion: always write `D3BJ`, never a bare/combined `-D3`.** D3(BJ damping) and
  D3(zero damping) are different methods, and bare `-D3` is ambiguous; ORCA also rejects
  combined `FUNC-D3` tokens (it wants the dispersion as a separate keyword). So combined
  tokens like `B3LYP-D3`/`B3LYP-D3BJ` are normalized to `B3LYP D3BJ`. Use `D3BJ` (or
  `D4`) explicitly everywhere.
- **Double hybrids / MP2 / correlated wavefunction methods need a `/C`
  correlation-fitting aux.** `_auto_aux` adds `AutoAux` (generates `/J` and `/C`)
  for those methods when RI is on — the correlated set covers the CC/QCISD/
  NEVPT2/CASPT2/ADC2 families via `_CORRELATED_MARKERS`, not just mp2/mp3; a
  `/J`-only aux makes ORCA abort *after* the full SCF. If the user sets the RI
  approximation to `NoRI` it adds nothing (conventional path). Plain hybrids/GGAs
  with an RI-J method get `def2/J` for def2 bases as before.
- **Solvent-name normalization + `%cpcm` block routing.** Like functionals,
  solvent picker labels go through an exact map (`normalize_solvent` /
  `_SOLVENT_ALIASES`) to the spelling ORCA's solvent table accepts (e.g.
  `Ethylene Glycol`→`1,2-ethanediol`); unknown names pass through verbatim.
  A resolved name containing spaces cannot ride the simple keyword (ORCA's
  parser splits on whitespace) — `Solvation.keyword()` then emits only the
  `CPCM` activation keyword and `Solvation.block()` selects the solvent in a
  quoted `%cpcm` block (`solvent "..."`; SMD: `smd true` + `smdsolvent "..."`).
  Every name in `data/solvents.json` is verified to exist in ORCA 6.1.1.

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
Python or the `SCFGraph` modules (`python -m pytest` and, for
`scf_graph.js` / `progress_panels.js` changes,
`node tests/web/scf_graph.test.js`) — the suite is fast (~3 s) and pins the
principle contracts (P56). A red test is either a test bug or a product bug;
never adjust a test to make wrong behavior pass.
