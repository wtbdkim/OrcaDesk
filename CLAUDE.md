# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ORCAdesk is a desktop GUI (PyQt6 + QWebEngine) for building, queuing, running, and
parsing [ORCA](https://www.faccts.de/orca/) computational-chemistry jobs. The app
shells out to the user's installed `orca` executable; it does not do the chemistry
itself. Status is `0.1.0-beta`, Windows is the primary tested target.

## Commands

```bash
# Develop (desktop app)
pip install -r requirements.txt        # PyQt6 + PyQt6-WebEngine
python main.py

# Optional phone-sync server, standalone (for API testing on localhost)
pip install -r requirements-server.txt # fastapi, uvicorn, qrcode, pillow
python -m orcamgr.server.run           # http://127.0.0.1:8000/docs for API docs

# Build a standalone Windows app -> dist\ORCAdesk\ORCAdesk.exe (+ runtime folder)
build.bat                              # installs deps + PyInstaller, then runs:
python -m PyInstaller build.spec --noconfirm
```

There is **no automated test suite** and no linter configured. The parser and input
generator were validated manually against real ORCA 6.1.1 output. The
`orcamgr/server/STAGE*_TEST_KR.md` files are manual server-test checklists (Korean),
not runnable tests.

## Architecture

### UI is HTML/JS, backend is Python, glued by a QWebChannel

The entire UI lives in `web/` (HTML/CSS/JS, shadcn-style dark theme). `main.py` opens
`MainWindow` (`orcamgr/gui/window.py`), which hosts a `QWebEngineView` loading
`web/index.html` and registers a single `Bridge` object on a `QWebChannel`.

`orcamgr/gui/bridge.py` is the **entire backend API surface for the desktop**: every
`@pyqtSlot` is callable from JS (the slot list is documented at the top of `bridge.py`).
Slots take/return JSON strings. The JS side does not hold queue state — it **polls**
`get_queue()` / `get_log(since)` on a `QTimer`. This polling design is deliberate: it
keeps the run worker thread and Qt's UI thread decoupled, avoiding cross-thread Qt
signal juggling. If you add backend functionality, it goes through a new Bridge slot.

### One shared QueueStore is the single source of truth

`orcamgr/server/store.py` `QueueStore` holds the queue (a list of `Calculation`),
the run flag, and the log buffer, guarded by a `threading.RLock`. The **same store
instance** is shared by the desktop Bridge and the FastAPI server (constructed once in
`window.py` and passed to both), so the desktop and a connected phone always see one
queue. `QueueStore` is intentionally free of PyQt and FastAPI imports so it stays
unit-testable in isolation.

`store.py` also owns the **shared serialization layer** — `calc_from_dict` /
`calc_to_dict` / `StepConfig` round-tripping and `load_*_choices` (reading
`data/*.json`). Both the Bridge and the HTTP server build `Calculation` objects through
the same `calc_from_dict`, so desktop and phone produce identical inputs.

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
- `runner.py` — `OrcaRunner` runs one `.inp` as a subprocess, streaming stdout
  line-by-line to a callback while also writing the `.out` file. Supports cancel.
- `queue.py` — `QueueEngine` orchestrates the pipeline (details below).
- `parser.py` — `parse_file()` → `ParseResult` (energies, geometry, HOMO/LUMO,
  frequencies, thermochemistry, TD-DFT transitions, NMR, NEB path). Marker-based,
  tolerant of `\r\n`; when a value recurs (e.g. across opt steps) the **last**
  occurrence wins.

### Queue semantics (important invariants)

These rules live in `QueueEngine.run_all` / `_validate` and `QueueStore`:
- **Calculation `name` is unique and is used as the on-disk folder name**
  (`{workspace}/{name}/`). Uniqueness is enforced in the store.
- **Geometry source** is `DIRECT` (coords supplied, e.g. from `.xyz`) or `REFERENCE`
  (another queued calc by name). For a reference, the engine injects that calc's
  **optimized final geometry** at run time — so opt → freq reuses the optimized
  structure automatically.
- **Failure propagation is dependency-scoped, not whole-queue.** If a calc fails,
  every calc that references it (transitively) is marked `BLOCKED` and skipped;
  unrelated calcs continue.
- **`DONE` calcs are never recomputed** on a re-run (the result is frozen);
  `FAILED`/`CANCELLED` calcs *do* re-run so the user can retry. Only
  `PENDING`/`FAILED`/`CANCELLED` are editable/reorderable (`EDITABLE_STATES`).
- **Result validation is per-kind**: `opt`/`ts_opt` must converge; `freq` must have
  zero imaginary frequencies; `ts_freq` must have exactly one. A validation failure
  marks the calc `FAILED`. Calc kinds: `opt`, `ts_opt`, `freq`, `ts_freq`, `tddft`,
  `sp`, `nmr`, `neb_ts`.

### Optional phone-sync server

`orcamgr/server/` is a thin FastAPI layer (`app.py`) over `QueueStore`, started/stopped
from the desktop by `ServerController` (`controller.py`) running uvicorn in a daemon
thread on the shared store. It serves the mobile PWA from `web_mobile/` at `/` and the
queue API under `/api/`. fastapi/uvicorn are **optional** — `ServerController.is_available()`
gates the whole feature, and the desktop app works fine without them. Per `CHANGELOG.md`
phone-sync is in development and **not part of the packaged build**.

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

- Bridge slots and API endpoints exchange **JSON strings**, typically
  `{"ok": bool, ...}` or `{"error": "..."}`; errors are returned as data, not raised
  across the JS boundary.
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
