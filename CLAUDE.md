# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
It is the **short operational guide**. The per-subsystem detail (why each rule
exists, what was measured, which test pins it) lives in
**[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** — read the relevant section
there before changing a subsystem; the section names below match its headings.

## What this is

ORCAdesk is a desktop GUI (PyQt6 + QWebEngine) for building, queuing, running, and
parsing [ORCA](https://www.faccts.de/orca/) computational-chemistry jobs. The app
shells out to the user's installed `orca` executable (and optionally to a MACE
Python env and to CREST); it does not do the chemistry itself — except the
in-process NBO analysis, which post-processes a finished `.gbw`. Status is beta;
the current version is `APP_VERSION` in `orcamgr/paths.py` (also the top entry
of `CHANGELOG.md`). Windows is the primary tested target and the only packaged
one (`build.bat` / `build.spec` / `installer.iss`); **Linux runs from source** —
every platform difference is a `sys.platform` branch, never a setting (P58).
macOS is untested.

Two normative companion documents govern how this codebase is written:
**`PRINCIPLES.md`** (development principles, cited as `P1`, `P10`, …) and
**`DESIGN.md`** (visual/UX principles + design tokens, cited as `D1`, `§17.4a`, …).
They are binding: follow them when writing code or UI, and when a change
introduces or amends a principle, update the relevant document in the same
commit; a deliberate deviation is justified in the commit body and recorded in
the appendices (with a disposition) if it is kept. Where they overlap with this
file or `docs/ARCHITECTURE.md`, the principles documents are the norm.

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

# Type-check the web/ front-ends (plain JS + JSDoc, no build step; needs Node)
npx -p typescript tsc --noEmit -p jsconfig.json

# Check the notebook front-end against the approved design (DESIGN.md 17.0)
python tools/uispec.py mock spec_mock.json
python tools/uispec.py real spec_real.json
python tools/uidiff.py spec_mock.json spec_real.json   # exit code = differences

# Automated tests (pip install -r requirements-dev.txt once) — fast, ~1200 tests
python -m pytest
node tests/web/scf_graph.test.js       # tracker/progress tests, no npm deps

# Real-backend smoke matrix (opt-in, ~1.5 h): one answer-known input per calc
# kind through the real QueueEngine against YOUR ORCA/MACE/CREST. Run before a
# release cut; each backend section auto-skips when its backend is absent.
ORCADESK_SMOKE=1 python -m pytest tests/smoke -v
```

Testing notes (full description: ARCHITECTURE.md *Test suite*):
- pytest covers the **framework-free** layers (`state/`, `core/`, `mlip/` via a
  stub worker, `crest/` via a fake runner, `nbo/`, `cube`/`core/plot`, the
  phone API via `fastapi.testclient`). The Qt bridge/window layers are thin
  adapters exercised manually. Corpus-reading tests auto-skip when the real
  ORCA output corpus is absent. No linter is configured.
- Three **static guards** over the source: `test_no_undefined_names.py`,
  `test_no_lock_reentry.py` (P37), and `test_bridge_slots.py` — every
  `bridge.X` the front-end calls must be a real `@pyqtSlot` (QWebChannel exposes
  slots only, and call sites are wrapped in `try`, so a missing decorator is a
  feature that silently does nothing).
- `tests/test_next_ui.py` pins the notebook front-end's contracts (no new
  top-level globals, bridge slots exist, fonts shipped, builder seeds from
  `get_step_defaults`).
- Parser / input-generator *evidence* comes from manual validation against real
  ORCA 6.1.1 output (P3). `orcamgr/server/STAGE*_TEST_KR.md` are manual
  checklists, not runnable tests.

## Architecture map

```
main.py ── single_instance (QLockFile + QLocalServer) ── MainWindow (gui/window.py)
              QWebEngineView ── web/ (classic)  or  web/next/ (notebook preview)
                   │  QWebChannel, JSON strings, JS *polls* get_queue()/get_log()
              gui/bridge.py  (Bridge: the ENTIRE desktop backend API, @pyqtSlot only)
                   │
  state/store.py QueueStore  ◄── shared ──►  server/app.py (FastAPI, optional phone sync)
     │  (queue, run flag, log ring, session.json autosave; RLock)
  core/queue.py QueueEngine ── core/runner.py (ORCA, detached) / mlip/ / crest/
     │  core/input_generator.py → .inp      core/parser.py → ParseResult
  nbo/ (in-process NBO from .gbw)   core/plot.py + cube.py (orca_plot cubes)
```

Dependency direction is `gui -> state <- server`; `core/`, `state/`, `mlip/`,
`crest/`, `nbo/` and the helper modules are **Qt-free** (that is what makes them
testable). Where to look, by area (each is a section of `docs/ARCHITECTURE.md`):

| Area | Key files | One-line rule |
|---|---|---|
| Front-end shell | `gui/window.py`, `web/`, `web/next/` | Two separate front-ends over one Bridge; `Settings.ui_variant` picks. |
| Bridge API | `gui/bridge.py`, `web/globals.d.ts` | New backend functionality = new `@pyqtSlot`; declare it in `globals.d.ts`. |
| Wire payloads | `state/schemas.py` ↔ `web/types.js` | TypedDicts are the source of truth; mirror field by field. |
| Queue store | `state/store.py` | Single source of truth, shared by desktop + phone; `calc_from_dict` is the trust boundary. |
| Run engine | `core/queue.py`, `core/resources.py` | Budget-admitted concurrency (P57), dependency-ready dispatch, per-kind `validate_result`. |
| ORCA input | `core/input_generator.py` | `StepConfig` → `.inp`; normalization rules in *Conventions* below. |
| ORCA run | `core/runner.py`, `core/tailer.py`, `procutil.py` | Detached launch by bare file name in the run folder; reattach via `(pid, create_time)`; hang watchdog. |
| Parsing | `core/parser.py` | Marker-based, last occurrence wins; `result_from_output` dispatches on kind. |
| Geometry | `core/structure.py`, `core/hessfile.py`, `core/normalmodes.py` | Reads only, except constraint-target Apply and push-along-mode. |
| MLIP | `mlip/` | User's own env runs the worker; one env per MLIP; import-probe readiness. |
| CREST | `crest/` (`shell.py` = transport) | WSL distro on Windows, `"local"` elsewhere; detected, never configured. |
| Cubes / viewer | `core/plot.py`, `cube.py`, `molview.py`, `web/molviewer.js`, `web/next/mvwin.js` | orca_plot driven by verified menu sequences; grid-qualified cube names. |
| NBO | `nbo/` | ORCAdesk's own reimplementation — never presented as NBO-program-compatible (P5, A25). |
| Text encoding | `textio.py` | ORCA `.out` on Windows is mixed UTF-8 / ANSI; decode process output via `decode_process_output`. |
| Paths | `paths.py`, `config.py` | Never write under `resource_root()`; user data under `user_data_root()`. |
| External open | `openwith.py` | `OPENABLE_SUFFIXES` is a default-deny trust boundary. |

### Front-end essentials

- **Classic** (`web/index.html`, default): plain scripts sharing one global
  scope, loaded in order `scf_graph.js` → `progress_panels.js` (must follow it:
  it `Object.assign`s onto `SCFGraph`) → `combo.js` → `appearance.js` →
  `log_graph.js` → `results_render.js` → `nbo_render.js` → `molviewer.js` →
  `structview.js` → `app.js`. Keep new top-level code declaration-only (TDZ).
- **Notebook preview** (`web/next/`, DESIGN.md §17): its own markup/CSS/logic,
  sharing no ids/classes with classic; borrows only `../scf_graph.js` and
  `../progress_panels.js` as parsers. Everything hangs off one global `NB`
  (plus `MOL` from `mol.js`, the one structure painter). **A top-level
  declaration other than `NB` collides with `app.js`** (one tsc project) — a
  test fails the build. `web/next/app.css` IS the approved design's stylesheet:
  a deliberate departure is two edits (named block at the bottom of `app.css`
  **and** `uidiff.py`'s `PER_LANDMARK_SKIP` with its reason). **Re-run
  `uispec`/`uidiff` after touching `web/next/`.** Editing guide:
  `web/next/README.md`.
- The JS side holds no queue state; it polls. Front-end errors reach the Log tab
  as `[web] ...` lines; `ORCADESK_REMOTE_DEBUG=9222` enables live DevTools.

### Queue invariants (don't break these)

Full statement and rationale: ARCHITECTURE.md *Queue semantics*.
- **Calc `name`** is unique case-insensitively and is the on-disk folder
  `{workspace}/{name}/`. ORCA-backed kinds also refuse space, `&`, `,`, `;`,
  `=` — but that rule is **not** applied on session restore.
- **`kind` is a closed set** (`KNOWN_KINDS`); `mlip*` / `crest*` route outside
  the ORCA pipeline.
- **`DONE` is frozen, `FAILED` is locked** (P24): no re-run, edit or reorder;
  only `CANCELLED` re-runs. `EDITABLE_STATES` = PENDING / CANCELLED / BLOCKED.
  Removal is queue-list-only — **nothing on the removal path touches the
  filesystem**.
- **Live queue (P29)**: the engine walks the store's own list under the store's
  lock, so adds/edits/reorders land in the running run; in-flight, DONE/FAILED
  rows and reference integrity stay protected.
- **References** (`REFERENCE` geometry) resolve to the parent's final geometry
  at run time via `_resolve_geometry`; failure blocks dependents (BLOCKED, read
  from the live queue each time), never the whole queue.
- **Closing the app never kills an ORCA/CREST job** (detach + reattach on next
  launch); MLIP is the exception (stamped CANCELLED on shutdown).
- Keep-existing ("skip") adopts an old output only if it validates, matches the
  calc's atoms, and contains the kind's defining section; a stale `{name}.gbw`
  is renamed aside (never deleted) when the structure changed.

## Conventions

- **The version is single-sourced from `APP_VERSION` (`orcamgr/paths.py`).**
  Window title and the UI badge/About (via `get_about()`) derive from it —
  **never hardcode a version string in `web/`**. To release, bump `APP_VERSION`,
  the `CHANGELOG.md` / `README.md` prose, **and** `installer.iss`'s
  `MyAppVersion` / `MyAppVersionFull` (Inno Setup cannot read `paths.py`).
- Bridge slots and API endpoints exchange **JSON strings**, typically
  `{"ok": bool, ...}` or `{"error": "..."}`; errors are returned as data, never
  raised across the JS boundary. Don't annotate FastAPI endpoints with the
  schema TypedDicts as return types (it would insert pydantic); keep `-> dict`.
- **`web/` is type-checked** (`// @ts-check` + JSDoc, `jsconfig.json`). When you
  add or change a payload or slot, update `state/schemas.py`, `web/types.js`
  and `web/globals.d.ts` together, and keep `tsc` at zero errors.
- **Theme variants** (classic only): `html[data-theme-variant]` (`shadcn` |
  `liquidglass`) + `html[data-glass]` intensity; the Liquid-Glass layer is gated
  on those attributes. **Compositor resilience is binding** (DESIGN.md §16.5):
  SVG lens on chrome only, no `backdrop-filter` on small/numerous controls,
  backdrop chains on `::before` overlays, and the `--lg-pulse` must stay
  paint-only (never `will-change`/`transform`).
- ORCA defaults (`wB97X-D4`, `def2-TZVP`, `RIJCOSX`, `def2/J`) live in
  `input_generator.py`. Option lists in `data/*.json` come from the ORCA 6.1.1
  manual and are **not closed enums** — unknown values pass through verbatim.
- **The generated `.inp` carries no comments of ORCAdesk's own** (pinned by
  `test_the_input_carries_no_comments_of_its_own`; PRINCIPLES.md A26).
  What the generator drops or changes is documented here and in
  ARCHITECTURE.md; constraint problems are warned about at add time.
- **Functional names** go through an exact map (`normalize_functional`:
  `M06-2X`→`M062X`, `SCAN`→`SCANfunc`, …) — never a hyphen-stripping heuristic.
  Verify new spellings against the installed ORCA, not just the manual.
- **Composite "3c" methods bring their own basis**: never append one (or its
  aux). Detection is the `-3c` suffix (`is_composite_method`).
- **Dispersion: always write `D3BJ` (or `D4`), never bare/combined `-D3`**;
  `B3LYP-D3BJ` is normalized to `B3LYP D3BJ`.
- **Double hybrids / MP2 / correlated methods need a `/C` aux**: `_auto_aux`
  adds `AutoAux` when RI is on (`_CORRELATED_MARKERS`); nothing under `NoRI`.
- **Solvent names** go through `normalize_solvent`; a name with spaces is
  selected in a quoted `%cpcm` block, not on the `!` line.
- Picked/dropped `.inp` / `.xyz` are read `utf-8-sig` (ORCA refuses a BOM).
- When changing asset loading, keep `web/` and `data/` at the same relative
  paths — `build.spec` bundles them there and `paths.py` reads them there.
- Phone sync is **not** in the packaged build (`build.spec` `excludes`); drop
  those excludes deliberately when it ships.

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

On **`dev`** (and feature branches) — subject is a `type: summary` prefix:
`feat:` (new feature), `fix:` (bug fix), `hotfix:` (urgent fix that may be
cherry-picked to `main`), `docs:` (documentation only — README, CHANGELOG,
CLAUDE.md, docs/, *_KR.md), `chore:` (build/packaging, dependencies, tooling —
`build.spec`, `installer.iss`, `requirements*.txt`, `.gitignore`).
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
- `docs/ARCHITECTURE.md` — update the section for any subsystem whose
  architecture, invariants, Bridge API surface, or measured behaviour changed.
- `CLAUDE.md` — update only when the change affects what this short guide says
  (commands, the architecture map, the queue invariants, conventions, paths/build
  layout, or the git workflow). **Keep it short**: detail goes in
  `docs/ARCHITECTURE.md`, not here.
- `README.md` — update when commands, install steps, requirements, or user-facing
  features described there change.
- `PRINCIPLES.md` / `DESIGN.md` — update when the change introduces or amends a
  convention/principle, deliberately deviates from one (if the deviation is
  kept, record it in the appendix with a disposition), or resolves a deviation
  already listed there. `DESIGN.md` §8 (tokens) and Part II (§9–§15) mirror the
  implementation — keep them in sync, and run the §15 checklist for any UI change.
- Other docs (`INSTALLER_GUIDE_KR.md`, `orcamgr/server/STAGE*_TEST_KR.md`,
  `web/next/README.md`, etc.) — update when the procedure they describe changes.

If nothing in the docs is affected, no doc edit is needed — but the check itself is
not optional: confirm it before committing, every time.

Alongside the doc check, run the test suite before every commit that touches
Python or the `SCFGraph` modules (`python -m pytest` and, for
`scf_graph.js` / `progress_panels.js` changes,
`node tests/web/scf_graph.test.js`) — the suite is fast and pins the
principle contracts (P56). A red test is either a test bug or a product bug;
never adjust a test to make wrong behavior pass.
