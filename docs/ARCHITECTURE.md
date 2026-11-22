# ORCAdesk architecture reference

The detailed, per-subsystem notes that used to live in `CLAUDE.md`. `CLAUDE.md`
stays the short operational guide and points here; `PRINCIPLES.md` (P*) and
`DESIGN.md` (D*, §*) remain the norm where they overlap. Keep this file in sync
with the code the same way — the pre-commit documentation check in `CLAUDE.md`
covers it.

## Test suite

The **automated test suite** (`tests/`, pytest + one plain-Node script) covers the
framework-free layers: `state/` (store, schemas, session persistence), `core/`
(queue-engine semantics with a fake runner, per-kind validation, input generator,
parser on synthetic fixtures), `mlip/` (with a stdlib-only stub worker — no MACE
env needed), `crest/` (ensemble parser against a real ethanol corpus in
`tests/crest/fixtures/`, CLI-flag building, the QueueEngine path via a fake
runner — no WSL/CREST needed — and `test_crest_transport.py`, which renders the
WSL *and* local `run_crest.sh` from either platform so neither shape is only
ever seen by the user it breaks for), `config`, `procutil` (real child processes),
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
mirrors; it skips without PyQt6. `test_queue_slots.py` imports it too and
calls the queue editor's slots **unbound on a stub `self`** (no Qt application,
no probes) to pin their JSON envelopes, and `test_run_folders.py` does the same
for the slots that resolve a calc's run folder. The Qt bridge/window layers
are otherwise thin adapters and are exercised manually. Tests that read the real ORCA output
corpus auto-skip when the corpus directory is absent, so the suite is green on any
machine. **No test touches the user's own data folder**: `tests/conftest.py`
points `%APPDATA%` / `$XDG_CONFIG_HOME` at a throwaway folder for the whole
process (before any test module is imported, never restored), with only a copy
of the real `settings.json` in it for the smoke tier, and pins
`PYTHONUSERBASE` so child interpreters still find the user site-packages. A
per-test `monkeypatch` of `user_data_root` is not enough on its own — it is
undone at teardown, and a run thread a failed test left behind autosaved
`session.json` after that, replacing a user's real queue with the test's rows.
A test that starts a run also stops it in a `finally`. A test that needs two
jobs in flight pins `queue.ram_headroom_mb` to 0: with a job running the
engine also checks the host's live free memory against the next job's
estimate (14.4 GB for a default calc), so without the pin the test passes or
fails with how busy the machine is. There is no linter configured. Parser/input-generator *evidence* still
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

## UI is HTML/JS, backend is Python, glued by a QWebChannel

The entire UI lives in `web/next/` (HTML/CSS/JS), plus what it borrows from
`web/`: `scf_graph.js`, `progress_panels.js`, `fonts/` and
`vendor/3Dmol-min.js`. `main.py` opens `MainWindow`
(`orcamgr/gui/window.py`), which hosts a `QWebEngineView` loading
`web/next/index.html?win=main` (`page_url("main")`) and registers a
single `Bridge` object on a `QWebChannel`.

**Two windows, one backend.** The queue editor has a native window of its own,
`QueueWindow`, created on the first `open_queue_window()` (the main window's
*Open queue*): a second `QWebEngineView` on the **same page** with
`?win=queue` — the page reads its role from the URL, there is no
`get_window_role` slot — and its own `QWebChannel` registering the **same
`Bridge` object**, so both windows talk to one `QueueStore` and poll the same
snapshot. The URL is built `QUrl.fromLocalFile(...)` + `setQuery("win=...")`,
never by string concatenation. It is a top-level window with no Qt parent (its
own taskbar entry, free to sit on another screen); closing it only **hides** it
(page and editor state kept), while closing the main window closes it for real
first (`MainWindow.shutdown` → `QueueWindow.close_for_real`) — left open it
would keep the application alive with the store torn down behind it. Its
geometry (`window.queue.geometry`, base64 of `saveGeometry`) and whether it was
open (`window.queue.open`, reopened at the next launch) live in
`ui_state.json`. Both pages use `_ConsoleCapturePage`; the queue window's lines
read `[web] [queue] ...`. The shared plumbing (drops, freeze on minimize,
raise) is `_WebWindowMixin`. File dialogs a slot opens are parented on the main
window (`Bridge.window`), whichever window asked.

**One front-end ships**: the notebook layout, `web/next/index.html`, in the
two roles above (DESIGN.md §17). The classic tabbed
front-end and the `Settings.ui_variant` switch were removed in the unreleased
version; a `settings.json` still carrying a retired key (`ui_variant`,
`theme_variant`, `glass_level`, `wallpaper`) loads, because `Settings.load`
drops unknown keys (`test_retired_classic_keys_are_dropped_on_load`).

One behaviour of the notebook report is worth knowing because it is a
*deliberate absence*: the vibrational section has **no table under the
spectrum**. A real frequency job is a hundred-odd modes, and a row per mode is
the chart transcribed — it buried every section after it. The number is on the
band instead (`chart(..., {hover: true})` labels each stick, `bindCharts` in
`web/next/results.js` drives the cursor). The hit target is the whole plot with
the nearest band winning, because a band is 1.6px; ←/→/Home/End step the same
cursor and the plate is a `role="status"`, so the numbers are never hover-only.
DESIGN.md §17.4b. A line chart takes the same `hover` option: its points carry
the label (`.pt` beside `.stk`, one `bindCharts` for both). The IRC / NEB-TS
*Reaction path* uses it for "Step n · ΔE · E(Eh)"; its table stays, because a
path is tens of points, not a hundred overlapping bands. A 2-D relaxed surface
scan is a third shape, `surface()`: a grid with each point's area its ΔE, the
highest `.sp.hi` (red, `--surf-hi`) and the lowest `.sp.lo` (blue, `--surf-lo`,
ringed because it is also the smallest mark). Its svg carries `data-grid`, and
`bindCharts` then reads it in two dimensions: the nearest point in the plane
wins, there is no cursor line, and ←/→/↑/↓ step along the row or column
(`step`). Planned points the run never reached are rings, so a stopped scan
shows its hole. A scan over three or more coordinates stays a table.

A band is also a control: a click, or Enter on the cursor (`chart(..., {pick:
"mode"})`), and the per-imaginary-mode buttons under the spectrum (`modeJump`)
call `NB.viewer.playMode(n)`, which **animates that normal mode on the
Visual panel's Structure tile** (`viewer.js`; switching to that tile first if
another is on the stage — the report has one stage now, the parsed structure
being the strip's first tile; the `.modeplay` bar above the panel holds
‹ ›, Play/Pause, the swing in Å and *Show structure*/Esc). The motion is
`MOL` stage's `vibrate({base, vec, amp, playing})` in `mol.js`: a
requestAnimationFrame loop that poses the coordinates without re-running
`scene()` (centre, scale and bonds stay the structure's, so the view never
refits), ends when the canvas leaves the document (a report re-render) or on
`setAtoms`/`destroy`, idles while an IntersectionObserver says the stage is
off screen, and, paused, draws per-atom arrows instead. A mode opens paused
under `prefers-reduced-motion`. The vector comes from
**`get_vibration_mode(payload)`** — `{source, freqs, geometry, n}` →
`VibModePayload` — over Qt-free `core/normalmodes.py`: `{base}.hess` first,
else the `.out`'s own last NORMAL MODES table (with the geometry printed last
before it), each accepted only if its wavenumbers ARE the report's
(`report_columns`; a uniformly scaled run passes), so a `.hess` left by an
OptTS's first Hessian is never animated under this report's mode number.
`place_on` moves the modes' geometry onto the report's (a `.hess` is written
about the centre of mass — 17.9 Å away for phenol) and reports what
difference is left. Pinned by `tests/test_normalmodes.py` (the water fixture
plus a trimmed verbatim `h2o_freq.out`, and every freq run of the real corpus
when present). DESIGN.md §17.4q.

Its **Output pane is the whole `.out` from line one, virtualized**: a spacer as
tall as the file holds only the lines on screen, fetched by number through
`get_output_info` / `get_output_lines`, and *Find* is `find_in_output` — the
search runs on the backend because the page never holds the file. All three
read `orcamgr/outview.py` (Qt-free, numpy): a newline-offset index cached per
path, **extended** when a running job appends and **re-scanned** when a re-run
rewrites (a head/tail fingerprint tells the two apart — size and mtime alone
cannot). Case folding is ASCII-only on both sides (`bytes.lower` / `asciiLower`)
so the count and the on-screen marks agree. These slots, and `get_inp` /
`get_output_tail`, take a calc name **or `file:<path>`** (`Bridge._result_file`):
a result opened from outside the workspace has no queue entry, and resolving it
as `{workspace}/{stem}/` is how its Output pane read "No output on disk." beside
a parsed summary. DESIGN.md §17.4j.

Its **Visual strip is both halves of "what can be drawn"**: the `.xyz` sets from
`list_structure_sets` and the wavefunction's surfaces from `NB.mv.surfaces`
(`web/next/mvwin.js` — `get_plot_options` + `picksFor` + the cube-name rule, in
one place so a tile's *ready* mark and the fetch that follows it cannot disagree
about which file they mean). A tile is a KIND (Structure, Trajectory,
**Orbitals**, ESP map, Conformers) and the `.vizlist` beside it holds that
kind's instances — a whole orbital manifold is rows, not one tile each. A row
opens the pop-out viewer through `NB.mv.open({pick})`; a cube already on disk
opens at once, one that is not asks first, because the click would otherwise
spend an orca_plot run (minutes, for an ESP map) as a side effect. The per-kind cost the question quotes is `COST` in
`mvwin.js`, beside the kinds themselves. The orbital list is passed down from the
parse payload (`mountVisual(source, d)`) — `get_plot_options` deliberately does
not send a second copy (P4), and without it the pop-out could only ever draw a
density. DESIGN.md §17.4c.

Its markup and stylesheet ARE the approved design: `web/next/app.css` is a copy
of `design/notebook-reference.html`'s stylesheet, so the markup uses the design's
class vocabulary and the two can be diffed element for element —
`python tools/uispec.py mock|real <out.json>` then `python tools/uidiff.py`,
whose exit code is the number of differences (currently 0 over 121 landmarks,
measured in three passes — the window at rest, a result section, the overlays).
A deliberate departure is TWO edits, not one: the rule goes in a named block at
the bottom of `app.css`, and the landmark it moves goes in `uidiff.py`'s
`PER_LANDMARK_SKIP` with its reason — otherwise the check reports a difference
nobody can act on, and the next reader "fixes" it back.
It measures three things beyond the landmark properties, and all three exist
because a green number hid a real difference (DESIGN.md §17.0a): **`__fonts`**
is the width of one sample line rendered in each stack against an unresolvable
family as the control — a computed `fontFamily` is the *declared* stack,
identical on both sides, so it could never show that one side was drawing in a
substitute; **`__structure`** is a census of every class on each page, printed
as an advisory and **not counted**, since class presence follows the sample
data; and **`__styles`** is a metric audit over every `(parent, element)` class
combination both pages draw — padding, margin, border, radius, type, min-height,
display — which is **counted**, because the two pages share one stylesheet and a
metric that differs means the markup put the element where the rules read
differently. Its key carries the parent (the rules are contextual, and a flex
parent blockifies a child's `display`), it skips any element with its own
`style=` (the audit measures the stylesheet, not a one-off) and it omits colour
and opacity (those carry state). A height difference is printed but not counted
— a box's height is its content on a data-driven page.
`run_mock()` injects `web/fonts/fonts.css` into the reference so both sides are
measured in the faces the design is drawn in, with no network.
**Re-run it after touching web/next/.** `web/next/README.md` is the guide to
editing that UI by hand — the F5 reload, `ORCADESK_REMOTE_DEBUG=9222` for live
DevTools, and where each region is drawn.

It also links `../fonts/fonts.css`, a sibling asset directory: Inter and JetBrains Mono, the two faces the design is
drawn in, carried as variable woff2 (latin + latin-ext, SIL OFL) because the
reference pulls them from Google Fonts and this app runs offline. Without them
the stack fell through to `system-ui`/`ui-monospace` and the whole UI was
drawn in Segoe UI Variable and Consolas — see DESIGN.md §17.0c, and
`tests/test_next_ui.py::test_preview_ships_the_typefaces_the_design_is_drawn_in`,
which pins the link, the files and the stack together.

`MainWindow._SIZE` opens the notebook at **1680×1000** (the size
`uispec.py` measures at), clamped to the screen, because `app.css` narrows the
queue rail below 1320px (from the design's 600px to `clamp(360px, 40vw, 520px)`,
and below 760px stacks it above the cells with the top bar wrapping) — at
1100 it came up in a layout the design never shows. The rail
is never hidden: it is the queue, the run controls and the result picker, and a
window dragged narrower used to lose all three
(`test_the_rail_never_leaves_a_narrow_window`). The top bar carries all three
backend pills — ORCA, MLIP, CREST — always (DESIGN.md B32), polled by
`pollBackends` in `boot.js`: every second while a probe is checking, every five
once both have settled.

`web/next/` borrows two scripts from `web/` — `../scf_graph.js` and
`../progress_panels.js`, the trackers and chart/step renderers, which own no DOM
— and is otherwise `mol.js` (2D ball-and-stick; see below), `charts.js`,
`boot.js` (channel, polled state, per-calc trackers, routing, toast/modal),
`rail.js` (queue rail + run controls), `build.js` (the builder — a cell becomes
the form), `stream.js` (one cell per calculation), `viewer.js` / `mvwin.js`
(the Visual panel and the pop-out viewer), `results.js` (the report) and
`settings.js` (a slide-over), all hanging off one global `NB` (and `MOL`,
`CHARTS`); the load order is given below.
The Settings sheet has a close guard. Every way out (×, Escape, the scrim, and
F5/Ctrl+R) goes through `NB.settingsView.requestClose(after?)`. "Unsaved"
means `collect(host)`, the same reader the save uses, differs from
`_baseline`. `_baseline` is read from the freshly rendered form, and read again
after a clean save once the backend's clamped values are written back into the
fields. The MLIP and CREST controls apply immediately, so they are left out.
`save()` resolves true only on a clean save: `{error}` and `save_error` both
count as failure, and the sheet stays open. A save sends **only the fields
changed on the sheet** (those that differ from `_baseline`), so a field left
alone never writes back an old value over one the other window saved.
`NB.settings` is a copy each window holds, so `NB.refreshSettings()`
(boot.js; one request shared by overlapping callers) re-reads `get_settings`
— and re-applies the theme, workspace label, graph modes and backend pills —
when the window gets focus or becomes visible, when Settings opens, and
before Run checks the ORCA path; a malformed reply keeps the old copy. The shared `NB.modalRaw` accepts
`focus: true` on one button; that button takes focus, and `closeModal` gives
focus back to where it was.
The rail is the queue's **status strip**, not its editor (DESIGN.md §17.2):
building, editing, reordering, removing and clearing happen in the queue
window (`qed.js`), which its header's *Open queue* brings up. It holds three
state groups (Running, Queued, Finished), of which only **Finished** folds,
plus an **Opened files** group listing results read from outside the
workspace (kept across launches in `ui_state.json`, see *Front-end state
across launches*), whose header is also the rail's *Open file…*;
`results.js` hands each one over through `NB.rail.noteOpened`, and each
opened row has an × that takes it off the list. A calculation row is its
state dot and name (kind, state and last message in its tooltip; a running
one with a thin progress bar); a click takes you to it — its cell in Jobs, or
in Results a finished row's report. There are no per-row actions and no
*Clear*. The strip redraws only when what it draws changes (`signature()`:
rows, progress in whole percent, the current report, opened files, folds,
resources) and puts the keyboard focus back on the same row or control after
a redraw (`focusKey`), so a poll neither steals focus nor swallows a click.
It emits the design's own ids (`q-total`, `c-running`, `fin-toggle`,
`opened-group`, …), so a landmark can address both pages with one selector.
`mol.js` is the ONE painter behind every structure the UI draws — the cell
thumbnails, the report's Structure and Visual stages, and the pop-out viewer —
so a sizing or colour rule lives there once. It draws the way Avogadro2 does
(DESIGN.md §17.4k): lit spheres and shaded cylinder sticks, the four display
types (`STYLES`: ball and stick at 0.3 × the van der Waals radius on 0.1 Å
sticks, sticks, space-filling, wireframe), hydrogens on/off, atom labels and
multiple bonds — and, because a 2D canvas has no depth, three depth cues:
mild perspective (`PERSPECTIVE`, camera at 4 bounding radii, allowed for
exactly in the fit), depth fog toward `--stage` (`FOG`) and a dark rim on
every sphere and stick. Multiple bonds come only from the run's Mayer bond
orders (`setAtoms(atoms, {orders})`, passed by `viewer.js` for the parsed
geometry and never for a trajectory frame). The display is app-wide
(`MOL.display` / `MOL.setDisplay`, every live stage repaints, kept in
localStorage) and is set from `MOL.displayMenu` — the *Display* popover — or
keys on a focused stage (1–4, H, L, +/−, 0). The orientation is a rotation
matrix turned about the screen's axes; Shift/middle-drag pans; the wheel zooms
only on a stage mounted with `wheel: "plain"` (the pop-out), elsewhere
Ctrl+wheel, so the report still scrolls. That rule and its step are one
function, `MOL.wheelZoom(e, plain)` (a notch arrives as deltaY 60, 9%), and the
3Dmol surface view reads it too: `volumeView` puts a capturing wheel listener
on `#mv-gl` that stops the event before 3Dmol's own handler on its canvas
(×1.44 a notch measured, reversed, and bare even in the report) and calls
`gl.zoom(f)` — bare only while the box is home in `#mv-stage`. It keeps **two** radius tables with two
fallbacks, which is the thing to keep straight: the covalent table (Cordero
2008, H–Pu) decides what is bonded to what (an unknown element guesses
GENEROUSLY — a missed bond draws a molecule as dust), and the van der Waals
table (Bondi/Mantina for the main group; covalent + 0.4 Å as a stated
convention for the d/f blocks) decides how big to draw an atom (an unknown
element looks ORDINARY — carbon). The covalent rule reads only a structure
that stands on its own (the builder's box): anything a run produced is drawn
with the bonds of the structure it started from. `setAtoms(atoms,
{bondsFrom: [...]})` takes reference structures in order of preference, and the
first that is the same molecule (same elements, same order) decides what is
bonded — the calculation's input (`NB.inputAtoms`), else the set's first
frame. Drawn by distance, a relaxed scan pulling two atoms to 1.5 Å closed a
ring the run never made, and a bond stretched past 1.25 × the radii vanished;
with a reference neither happens, at the price that a bond a run really breaks
stays drawn. The builder keeps its box's bonds across the moves it makes
itself (Apply, Apply all, a push — `showGeom(true)`) and across a re-render
(the reference is `st.bondRef`, not the stage's), and reads what the user
types or pastes by distance again. The ball's one floor is `stickPx * KNOB` (0.62),
stated against the stick width rather than as a pixel count: a flat 2.5px
minimum clamps the SMALL atoms first, so it erased the H-vs-C distinction
exactly where the molecule is large against its box — every cell thumbnail of a
real molecule. The **axis marker** in a stage's bottom-left corner is drawn by the
same file and IS the control for the axis views — the old *View down x/y/z*
button is gone. The arm's LABEL is the target (two arms nearly edge-on cross at
the origin), `gizmoArms()` is the one speller of where an arm is so the drawn
one and the clicked one cannot disagree, `opts.gizmoBottom` is where a stage
that writes in that corner has its caption start, in px up from the bottom
(the cell thumbnail's `.cap` 24, the Visual stage's and the pop-out's
`.vizcap` 26), and the whole marker keeps above it — the origin sits
`GIZMO.ox` (40) higher, room for an arm pointing straight down plus its label.
It used to be the origin's own height, so the thumbnail's 26 put x's label on
*final · N atoms*. And x/y/z
on the focused canvas are the keyboard route, since nothing drawn can be
tabbed to. The view scale is fitted to the molecule's BOUNDING SPHERE, not
to its projected extent — the projected one is recomputed every frame, so a drag
re-zoomed continuously (1.6x between end-on and broadside on a Tf2O-shaped
molecule). The sphere is the smallest one holding the atom centres
(`enclose`, Bădoiu–Clarkson, never worse than the centroid's — it is also
the pivot), and `fitReach` is the farthest any ball is drawn from the centre
in any orientation, `max (r·sinθ + b)·D/(D − r·cosθ)` per ball size, cached on
the scene per display type; the edge around it is 4% of the shorter side
within 6–18 px (`PAD_*`), and the only floors (`SPAN_FLOOR` 0.3 Å,
`REACH_FLOOR` 1 Å) are for a lone atom or H₂. The 3Dmol surface view fits
in `volumeView`'s `fit()`: `zoomTo` counts atom centres and each surface's
box corners but never fits under `minimumZoomToDistance` (default 5 Å, which
drew a small molecule at two fifths of the panel), so that config value is
set before each fit to the atoms' own reach with their spheres (≥ 1 Å, +2%
for the perspective), then `gl.zoom(0.95)` leaves the edge; its box is
adopted right after the stage's atom canvas so the caption and buttons stay
above it. DESIGN.md §17.4e.

The builder seeds a new calculation from **`get_step_defaults`** (StepConfig's
own defaults) and from no literal of its own: its copies had drifted — MaxIter
0 clamped to 1, IRC MaxIter 40 for 100, TD-DFT maxdim 0 clamped to 1 — and
`tests/test_next_ui.py::test_the_builder_seeds_a_new_calculation_from_stepconfig_alone`
fails if a numeric default reappears in `build.js`.

One tsc project checks every script under `web/`, so they share one global
scope: **a top-level declaration in `web/next/` other than `NB` / `MOL` /
`CHARTS` is refused** by `tests/test_next_ui.py`, and
`tests/test_bridge_slots.py` checks that every slot the page calls is a real
`@pyqtSlot`.

The Liquid-Glass variants (DESIGN.md B30) went with the classic front-end; the
notebook draws flat.

The builder builds **all three backends** (DESIGN.md §17.4s): the reference's
backend control at the top of the form — `DFT | MLIP | CREST`, then `.seg .sep`
and `Guided | .inp text` for DFT only — puts the backend's own block
(`backendForm` in `build.js`) where the ORCA method form sits, under the shared
name / charge / multiplicity row and the shared geometry block (coordinates +
stage + structure check, or a reference). Nothing of the ORCA form is drawn or
read for MLIP/CREST (no constraints, no push along a mode, no Preview .inp);
`readForm` hands the config to `backendConfig`. MLIP: Task (the four `mlip_*`
kinds), MACE model (grouped from `load_choices("mace_models")`), environment
(`""` = first ready), device (GPU offered only when a READY env's probe reports
CUDA; a stored `cuda` with none falls back to Auto), CPU threads with a
per-device note, T/P for the freq tasks. CREST: method, search speed,
ewin, threads, solvent model + solvent, NCI, a *Run in* target only where
there is a choice (more than one WSL distro with CREST, or one stored), and a
closed `<details class="advsec">` for the MD/MTD numbers and toggles, opened by
itself when one is set. Hidden/locked from the polled `NB.backends`
(`backendState`): an unadopted backend has no button, only
*+ More backends…* (opens Settings); adopted-but-not-ready opens LOCKED (`.bkform.locked`,
every control and *Add* disabled, the reason in a `.finding` with *Open
Settings*), and the save handler refuses on its own (P44). A backend stays
visible while the form is on it or the queue holds one of its calcs, so an
`mlip*`/`crest*` row's *edit* opens this builder on its own form. Each backend
keeps its settings across a switch away and back (`st.cfgBy`). The MACE model
default is `mlip/runner.py DEFAULT_MACE_MODEL`, which `get_step_defaults`
fills in for StepConfig's `""` — the one non-StepConfig value it sends. In the
report, an MLIP/CREST result has **no Input and no Natural-orbitals section**
(`resultBackend()` in `results.js`): it was handed no `.inp` (the Input pane
used to show a `build_inp_preview` of an ORCA job that never ran) and has no
`.gbw`.

The backends themselves are set up from its Settings sheet: the MLIP environments
card, and a CREST card (`paintCrest` in `settings.js` — one `.envrow` per
target, the WSL-distribution picker, *Install CREST* / *Re-check*) over the
four CREST slots (`get_crest_status`, `check_crest`, `install_crest`,
`set_crest_distro`); while the sheet is open both poll, and
the CREST card hands each payload to the top-bar pill (`NB.backends`).

The JS is plain scripts sharing one global scope (no modules/bundler), loaded by
`web/next/index.html` in this order: `../scf_graph.js` → `../progress_panels.js`
→ `mol.js` → `charts.js` → `boot.js` → `rail.js` → `build.js` → `stream.js` →
`viewer.js` → `mvwin.js` → `results.js` → `settings.js`.
Cross-file calls resolve at runtime, so only top-level statements may not touch
a later file's bindings (TDZ) — keep new top-level code declaration-only.

`progress_panels.js` is the one **hard** ordering constraint in that order (the
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

**One instance per user** (`orcamgr/gui/single_instance.py`, wired in
`main.py` before `MainWindow` exists — the window restores the session and
reattaches running jobs, which a second instance must never do). Two
mechanisms because each answers one question: a `QLockFile`
(`user_data_root()/instance.lock`, stale only when its owner PID is gone) says
whether one is running — a `QLocalServer` cannot, since on Windows a second
`listen()` on the same pipe name succeeds — and a per-user `QLocalServer` is
how a later launch asks the running one to `bring_to_front()`. The server opens
before the window is built, so a launch made during startup is still answered;
`tests/test_single_instance.py` pins the lock, crash recovery and the round trip.

The view's page is `_ConsoleCapturePage` (`window.py`), which forwards JS console
output into the shared log buffer as `[web] ...` lines (rate-limited per identical
message) — so front-end errors reach the log ring even in a deployed build.
The notebook draws no Log tab (it reads `get_log` only to feed its trackers);
the phone's `/api/log` serves the ring. Its trim retains older `[web] ` lines
preferentially, so ORCA's stdout flood can't evict them
(`QueueStore.append_log`). `ORCADESK_REMOTE_DEBUG` (handled in `main.py`)
remains the dev-time tool.
`window.py` also trims the embedded browser: Chromium's built-in PDF viewer is
disabled (the UI never opens PDFs). **WebGL is enabled** — the isosurfaces
(3Dmol.js, loaded on first use by `web/next/mvwin.js`) need it; every other
stage is `mol.js`'s 2D canvas, so WebGL is used only inside that one surface
renderer. Minimizing the window sets the
page's lifecycle state to Frozen so Chromium releases memory while ORCAdesk sits
in the taskbar (both windows, `_WebWindowMixin.changeEvent`). JS timers stop
while frozen, and the poll simply resumes on restore: it reads the queue
snapshot whole and the log from its last sequence number, so nothing is lost.
The poll does **not** skip ticks of its own — `setInterval(poll, 1200)` runs
in every page that is not frozen, so a queue window that is hidden (closed,
not minimized) keeps polling `get_queue` (only that: the queue window reads no
log and no output). (No profile/cache tuning: the Qt 6 default profile is
already off-the-record.)
**Dropped files** are handed to the page by `runJavaScript`, guarded by
`window.fn && ...`, so a page that defines no handler ignores the drop. Both
windows go through `_WebWindowMixin._dispatch_drops`: **every dropped `.xyz`
at once** goes to `window.onXyzFilesDropped(paths)` (a JS array — one batch
per drop, not N); a drop with no `.xyz` takes the single-file route, the first
handled `.inp` / `.out` to `window.onInpDropped(path)` /
`onOutDropped(path)`. All are
declared on `Window` in `web/globals.d.ts`.

`orcamgr/gui/bridge.py` is the **entire backend API surface for the desktop**: every
`@pyqtSlot` is callable from JS (the full slot list, with signatures, is the `Bridge`
declaration in `web/globals.d.ts` — kept honest by the tsc check).
Slots take/return JSON strings. The JS side does not hold queue state — it **polls**
`get_queue()` / `get_log(since)` on a `QTimer`. This polling design is deliberate: it
keeps the run worker thread and Qt's UI thread decoupled, avoiding cross-thread Qt
signal juggling. If you add backend functionality, it goes through a new Bridge slot.

## One shared QueueStore is the single source of truth

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

**The queue editor's model.** A `Calculation` carries the editor's graph:
`system` (the lane, a system id; `""` = a standalone calc), `node` (stack
members share one), `col` / `row` (grid position), `after` (the calc it runs
after — the edge), `gate` (`"auto"` | `"approval"`), `auto_pass`, `approved`,
`ref_point` (`""` | `"irc_f"` | `"irc_b"` | `"conf:<k>"`, the last strictly
`^conf:[1-9][0-9]*$`, `core.queue.is_ref_point`), `set_name` (display only),
and the energy condition on the edge into it: `filter_de` (a window in
kcal/mol, a finite number ≥ 0, or `null` = off) and `filter_top` (a whole
number; 0 = off, N ≥ 1 = only the N lowest of the stack pass). All
default to "standalone, as today", ride `calc_from_dict` / `calc_to_dict` /
`calc_to_session_dict` (`CalcSummary` / `CalcFull`: `filter_de: float | None`,
`filter_top: int`; a session without the keys loads with the defaults), and
are checked at the trust boundary by
`_graph_fields` (a closed-set or wrong-typed value is a `ValueError` for a new
payload, degraded to the default on restore — evicting a run's record over a
layout field is the thing `_validate_calc_name`'s `restoring` rule exists to
avoid). `CalcSummary.waiting` is derived in `snapshot()` by
`core.queue.gate_waiting` (Queue semantics). An MLIP template (`kind mlip*`
sent with `is_raw` and `key value` text) is turned into the MLIP config by
`calc_from_dict` (`inpsets.mlip_config_from_text`) and runs as an ordinary
MLIP calc; the text stays in `raw_text` for the editor, `is_raw` false. A
CREST template (`kind crest*`, `inpsets.crest_config_from_text`, the
`CREST_KEYS` fields) is handled the same way and runs as an ordinary
conformer search. The
text is the whole MLIP (or CREST) setting: the `MLIP_KEYS` fields are reset to
`StepConfig` defaults before it is applied, so an edited text that drops a key
(`update_calc` merges the old config in first) returns that setting to the
default rather than keeping the old value. A **level block** (an ORCA inp-set
text with `{{JOB}}` on its `!` line, `input_generator.is_level_text`) reads
its `config` as the block's **options** (`template_config`: StepConfig field
names, `config.kind` forced to the block's kind, an `irc_init_hess` the
payload does not name read as `"auto"`); see *Running the queue* for how they
merge. For such a block the store also runs the constraint / scan index
checks against a direct `xyz` (as for a guided calc), refuses options the
level cannot take (`template_option_conflicts`, the message prefixed with the
calc name: a key both set, constraints into a one-line `%geom`, an IRC
`read` without a file, an SMD solvent SMD lacks), and `constraint_warning`
judges its constraints too. `session.json` is **schema 3** (3 records each
calc's run folder, see *Queue semantics*): a schema-1 file's
level blocks had a config nobody chose as options (it was never read), so
`load_session` reads it as none (`template_config({}, kind)`) and the block
renders exactly as before. Also refused for a new payload
(never on restore), because at launch they could only fail and FAILED locks:
a raw text that reads a parent's Hessian (`{{HESS}}`, or an inp-set IRC whose
own `%irc` block reads one — `InitHess read` with a relative
`Hess_Filename`, or a level block whose options read one by name; a picked
`.hess`, an absolute path, needs no parent) with neither an `after` nor a
geometry reference (`core.queue.template_hess_problem`); and a `ref_point`
`irc_f` / `irc_b` on a
parent whose kind writes no IRC end points (anything but `irc` / `general`,
`ref_point_problem`), or a `conf:<k>` on a parent that is not a conformer
search (`CONFORMER_KINDS` = `crest_conf`) — checked by `add` / `replace` and, for `apply_ops`, on
the batch's final state for every row it added or rebuilt (or whose parent it
rebuilt).

**Systems** (the lanes) live in the store beside the calculations,
`{id, name, charge, multiplicity, inputs: [{label, xyz}]}` (`system_from_dict`:
the name obeys the calc-name rules because it is the prefix of every calc name
in the lane and, verbatim, the folder the lane's calcs run in — an unsafe one
is refused, never mapped, see *Queue semantics*; an input's `.xyz` header is
stripped to a coordinate block), are
persisted under `"systems"` in `session.json` and restored on load (each
re-validated; a corrupt or duplicate one skipped alone; a calc whose lane did
not come back becomes standalone; a session without the key loads as before).
The poll carries them as `QueueSnapshot.systems` with inputs **by label only**;
`get_system(id)` serves the coordinates. `clear()` removes them too.

**`apply_queue_ops(ops_json)`** is the editor's one write path: a list of
`add_system` / `update_system {id, patch}` / `remove_system` / `add_calc` /
`update_calc {name, calc}` / `rerun_calc {name, calc}` / `undo_rerun {name}` /
`place_calc {name, place}` / `remove_calc` / `order {names}` applied by
`QueueStore.apply_ops` **atomically**: every op runs against a working copy
through the single mutations' own rules (`_add_into` / `_replace_in` /
`_remove_from` — the bodies `add` / `replace` / `remove` now call: name rules,
case-insensitive uniqueness, `EDITABLE_STATES`, the engine-active refusal), and
only a fully successful batch is committed — **in place** (`self._calcs[:] =`),
because a running engine walks that list object (P29). Reference integrity is
judged once on the final state (only while running, like `add`): a batch that
adds a child before its parent, or removes a node with its descendants, is
consistent as a whole. A refusal is `{ok:false, error, op_index}` (`-1` = the
batch as a whole) and changes nothing. `update_calc`'s `calc` is a **patch**
(omitted keys keep their value, so `{"approved": true}` approves a gate).
An approval-only patch on a PENDING row keeps the **live** row object
(written back at commit like `place_calc`), because the engine pins its
keep-existing answers to row identity (`_skip_ids`): a replacement made a row
the user chose to keep re-run mid-run and overwrite its output. Any other
patch rebuilds the row (a new object, PENDING again). On a level block a
patch `{kind, config, geometry_source, xyz, ref_name, ref_point}` (the
builder's *Level* mode) keeps `raw_text` / `set_name` — the patch merges over
the calc — and `config` is the block's whole option set. A calc that
BECOMES a level block through a patch without `config` (a level dropped on a
guided calc) gets no options: its old config was a whole guided input. An
MLIP (or CREST) set's block is re-derived from its text (`is_raw` set again) for a
`raw_text`, `config` or `kind` patch, so an options edit (T / P for an MLIP
frequency) never replaces the model the text sets.
A patch of **only** `filter_de` / `filter_top` on a row that is not editable
is the energy condition being changed (`_refilter`): a **FILTERED** row goes
back to PENDING with no message and the new condition, and so does every
FILTERED row downstream of it (by reference or `after`, transitively), each
as a new object so a running engine picks it again (P29) and judges it
afresh; a DONE, FAILED or RUNNING row keeps its state and everything it ran,
only the condition being written onto the live object at commit (as for
`place_calc`), so the queue editor can change a stack's condition on all its
members in one batch. Any other edit of a FILTERED row is refused like a
DONE one's (it is not in `EDITABLE_STATES`); a lane rename / recharge
(`update_system`) still carries it along (new name, references, charge),
leaving it FILTERED — it never ran and owns nothing on disk.
`rerun_calc` is P24's one explicit way back from DONE and FAILED: the queue
editor sends it when a set is dropped on (or clicked onto) a finished or
failed block, only after an overwrite warning that is asked every time
(`requestRerun`, never the once-only replace answer). A stack whose other
members are still editable (none running) takes the set too, by
`update_calc` in the same batch. It is refused for anything but a DONE or
FAILED row the engine is not handling (a FILTERED row, which never ran and has
nothing to overwrite, is refused with a pointer to its energy condition), and its `calc` may hold only the set fields
(`raw_text` / `set_name` / `set_id` / `is_raw` / `config`); the patch merges
like `update_calc`'s (the same `_patched` helper, so a guided calc becoming a
level block drops its old config). The row is rebuilt PENDING with no
message, result, output path or pid, keeping its name, place and edges, and
is marked `Calculation.rerun`. While the row has not started (`is_rerun`:
PENDING / BLOCKED), the mark keeps it out of `_overwrite_conflicts` (the
Run-click modal and the startup resume), and `make_engine_factory` drops it
from any keep answers. So the old output in its folder is overwritten, never
adopted. The mark is persisted in the session and is carried by an
`update_calc` while unstarted. A row that starts, is cancelled, or is removed
and re-added loses it, and `calc_from_dict` never reads it, so no client can
set it. For a FAILED row, `_unblock_after` also puts every BLOCKED row
downstream of it (by reference or `after`, transitively) back to PENDING with
no message, each as a **new object**: a running engine has already handled
(seen) the blocked one and tracks rows by identity, so only a new object is
picked again in the same run (P29). One still blocked by another failure is
stamped BLOCKED again when the walk reaches it, as at a run's start. The
replaced row and the (blocked, freed) pairs are held in an in-memory stash.
`undo_rerun {name}` puts that same object back while the row under the name
is still the unstarted rerun, and each freed row that is still the freed
object (one edited since stays as edited); once it has started, or after a
restart, it is refused (its output may already be overwritten). DONE rows
that ran after it keep their results; pending ones wait for it and read its
new geometry.
`place_calc`'s `place` holds only `system` / `node` / `col` / `row` (anything
else is refused) and is allowed in **every** state: where the editor draws a
calc is not what it ran (P24 freezes the latter). It is how a finished
standalone calc becomes the first node of a new system so a follow-up can hang
off it — `update_calc` stays refused for DONE/FAILED, and would re-queue the
row (`rerun_calc` is the explicit, warned way to do that). The state, message, result, `after` and inputs are untouched and the row
is not reset to PENDING. The op works on a batch-private copy (so a refused
batch leaves the row where it was); at commit the placement is written back
onto the **same** row object, because the engine tracks rows by identity and
may hold this one (running, or finished and already seen).
`update_system` with a new name renames every calc of the lane that has **not
started** and whose name starts with the old one (`new + rest`), and re-points
every editable calc's `ref_name` / `after` at the new names; those run under
the new name's folder when they start. A calc that has started
(`_has_started`: RUNNING / DONE / FAILED, or one carrying a recorded run folder
— a CANCELLED one, a `rerun_calc` row) keeps its name and the folder it ran
in, under the old name: P24 freezes it, others name it, and nothing on disk is
moved (a job may be running there). Until run folders were recorded the rename
was refused outright once any calc of the lane had started, because a calc's
name alone was its folder. Refused while the engine holds a calc it would
rename. The editor's name field is editable in every state, and its tooltip
says which calcs follow (`renameTip` in `qed.js`); charge
/ multiplicity propagate to the lane's editable calcs that had the lane's OLD
value, each field on its own (each touched calc is an edit: a new object,
PENDING again) — a calc set apart on purpose (a triplet SP in a singlet row)
keeps its own, and undoing the recharge returns exactly the calcs it moved.
The editor shows such a calc's own charge / multiplicity, says so, and edits
it on the calc alone. `remove_system` takes the lane's calcs with
it and is refused while the lane holds a started one (remove those rows first —
queue-list only); a `remove_calc` naming a calc an earlier op of the same batch
already removed is a no-op. `order` takes any full permutation: the editor's
run order is lane by lane and a lane moves with its finished rows, which
changes nothing for the engine (it walks rows by identity).

`pick_xyz_files()` (a multi-select picker) and `read_xyz_paths(json)` (dropped
paths) read several `.xyz` at once — `XyzFilesResult {cancelled, files:
[{path, label, name, xyz, error, charge, multiplicity}]}` through
`store.xyz_file_entries`: per file the system name its stem gives, its one
structure, and the charge / multiplicity its comment line states
(`store.xyz_charge_mult`: `charge` / `chrg` / `chg` and `mult` /
`multiplicity`, `=` or `:` or a space, case aside; xtb's `uhf=N` as
multiplicity N+1 when none is stated; `null` when not stated, and a value no
molecule has — multiplicity outside 1–20, a charge past ±50 — is not read
rather than clamped; only the line after an `.xyz` count is read), or why it
cannot start a row (unreadable, several frames, no coordinates, a header that
counts fewer atoms than follow it). *+ system*'s file branch is the same
multi-pick, so it makes rows by the drop's rules. The editor then builds ONE `apply_queue_ops`
batch: per file a system holding the structure, plus the copied workflow's
calcs for a **paste** onto picked files, or nothing else for a **drop** (a
dropped `.xyz` queues nothing; its steps are built on the row). A file whose
name ORCA or a folder refuses, or whose row already exists, is listed with
the reason, never renamed to fit. A workflow (copied in the queue window)
lives in the editor and under the UI-state key `queue.workflow`, never in the
store. (Until 0.11.1 a drop ran a "drop recipe" — by default a single point
at an ORCA level, through an `add_xyz_systems` slot; both are gone.)

**Saved workflows** (`orcamgr/state/workflows.py`, slots `get_workflows`,
`save_workflow(json)` → the workflow with its id, `delete_workflow(id)`) are
copied workflows kept by name: `{id, name, v, from, steps}` in
`user_data_root()/workflows.json` (schema 1, tmp + `os.replace`). `steps` is
the editor's own workflow step list (`buildWorkflow` in `qed.js`), replayed
as `add_calc` ops by `instantiate`, so the store's trust boundary judges what
a step would run; the module checks only what a replay needs — a non-empty
list (≤ 200) of objects, each with its own `id`, a `node` and a kind in
`KNOWN_KINDS` — plus a name (unique case-insensitively), an id pattern and a
500 kB cap. A missing or unreadable file reads as none and a bad entry is
dropped on read, the rest kept (P32). The editor uses them from the
library's *Workflows* group, the + slot's add menu, and a row inspector's
buttons — always through `pasteAfter` / `pasteIntoSystem`, the paste's own
one-batch paths (`avoidDisk` + `guardOverwrite` included).

The + slot's **add menu** adds through `addFromSet(slot, set, kind)` — the
same batch a set dropped on the slot makes, with the Type chosen in the menu
instead of `next_kind`; the level used is remembered under the UI-state key
`queue.level`. `pasteIntoSystem(lanes, wf)` takes several rows (a
Ctrl+click selection, `{t: "rows", keys}`) and fills every empty one in ONE
batch, each with its own charge / multiplicity; a row with steps, or a
structure with fewer atoms than the workflow's constraints name, is listed
and left as it is. A rows selection's charge / multiplicity is one batch of
`update_system` ops, skipping rows that have started.

**A step before a block** goes through the same menu from a second slot,
`beforeSlotOf` (`{before: B, parent, c, r, key: "<lane><<node>"}`, offered for
an editable B only and kept in `_ghosts` so `menuSlot` / `dropTarget` find
it); `addFromSet` hands a `before` slot to `insertBefore`. One batch:
`place_calc {col: +1}` for B and its descendants (by the lane's edges — or
for every node from B's column on when one would land on a node that
stays), one `add_calc` per member of B carrying the member's `after`, gate,
geometry (reference + `ref_point`, or its `xyz` read with `get_calc`) and
charge / multiplicity, an `update_calc` re-pointing the member (`after` =
`ref_name` = the new step, gate auto), and an `order` placing the new names
just before B's. The inverse restores the members first, so the
`remove_calc`s find nothing referring to them. A standalone chain is
adopted first, as for an add after it.

**After an IRC** every add path — `addFromSet` (the menu, a dropped set),
`pasteAfter` (a paste or saved workflow) and `setGeometry("pick:<irc>")` (a
block's geometry list) — asks `askIrcStructure(irc, both)` first. It reads
`get_irc_structures(name)` → `IrcStructuresResult {ok, done, files: [{key,
label, path, count}]}`: the `_IRC_F.xyz` / `_IRC_B.xyz` / `_IRC_F_trj` /
`_IRC_B_trj` / `_IRC_Full_trj.xyz` files in the IRC's run folder
(`_calc_run_dir`), listed **only once it is DONE** — a re-queued IRC's
folder still holds the last run's files. The frames come from the viewer's
`get_structure_frames(path)`. The graph is the whole path from
`_IRC_Full_trj.xyz` (measured on real ORCA 6.1.1 runs: frame 1 is the
backward end, the last the forward end, the TS in between), else the
backward path reversed + the forward one. An end picked becomes a reference
(`ref_point` `irc_f` / `irc_b`, resolved from the end-point file at run time
as before); any other point is copied in as direct coordinates (`_P<k>`),
still `after` the IRC. A paste after an IRC runs `instantiate` once per
structure with a name tail (`_F`, `_B`) and shared node ids, so each step's
copies stack. The IRC's bare final geometry (`ref_point ""`) is no longer
offered: measured, it is the starting TS (RMSD 0 to the OptTS geometry).

**Names** the editor makes are `<system>_<Job>` (`SUFFIX` + `SEP` in
`qed.js`): the job word reads like ORCA's job keyword but is a name, not
`TEMPLATE_JOB_KEYWORDS` itself (that has `TightOpt Freq`, with a space, and
an empty TD-DFT) — `Opt`, `OptTS`, `FreqTS`, `OptFreq`, `IRC`, `SP`, `NEB-TS`,
`Preopt` / `MLSP` / … for MLIP. A taken base is numbered (`_SP2`), stack
members are tailed `_F` / `_B` / `_P<k>` (`tailOf`), a copy is `<name>_copy`;
`stem` splits on the last `_` or `-`, so older hyphen names still form stacks.
**A redraw waits for a click**: the editor redraws the canvas and inspector
by `innerHTML` on every changed poll, and a redraw between a press and its
release replaced the element under the pointer, so the browser dropped the
click. `render()` returns while a button is down (`wirePress`, at most
`PRESS_MS` = 1.5 s, released on mouseup / dragstart / blur) and draws once
on the release.

**Inp sets** (`orcamgr/state/inpsets.py`, slots `get_inp_sets`,
`save_inp_set(json)` → the set with its id, `delete_inp_set(id)`) are saved
templates `{id, name, kind, text}`, `kind` ∈ **ORCA** / **MLIP** / **CREST**
(`SET_KINDS`), in `user_data_root()/inp_sets.json` (schema 3). An ORCA set is a **level of
theory**: a whole input whose `!` line holds `{{JOB}}` where the job keyword
goes (required, with `{{GEOMETRY}}`; `{{CHARGE}}` / `{{MULT}}` optional) — the
block made from it runs the job its own `kind` says (the editor's Type), so
one level serves every step. An MLIP set is `model` / `device` / `nprocs` /
`env` lines. A CREST set is `method` / `solvent` / `ewin` / `threads` /
`preset` / `nci` / `solvent_model` / `env` lines (`CREST_KEYS` →
`crest_method` … `crest_env_id`), held by `crest_config_from_text` to
`StepConfig`'s own rules — method and solvent one token (each becomes a CREST
argument), `ewin` 0–1000 kcal/mol, `threads` ≥ 1 (≤ 1024), `preset`
`quick` / `squick` / `mquick` (`none` = the full run), `solvent_model`
`alpb` / `gbsa`, `nci` a yes/no word — an unknown key or a bad value refused
naming the line; its block runs `crest_conf` (`CREST_TEMPLATE_KINDS`). A
missing file seeds one level (`wB97X-D4 / def2-TZVP`, from
`StepConfig` + `build_input` with `calculation_type="{{JOB}}"`), the MACE
set and the CREST set `seed-crest` (`GFN2-xTB`: `method gfn2` / `ewin 6` /
`threads 4`), at the user's `default_nprocs` / `default_maxcore_mb` written literally
(so a template is admitted by what it asks — `core/resources.py` reads a raw
input's own `%pal` / `%maxcore`); a file emptied by the user stays empty, an
unreadable one reads as the seeds and is replaced only on the next save. A
name is unique within its kind (case-insensitive): a block names its set by
(kind, name). `get_inp_sets` also returns `defaults` (each kind's seeded text,
what *+ New* starts from), `jobs` (`TEMPLATE_JOB_KEYWORDS`: the kinds a
level's block may run — the Type select; `tddft` maps to `""`), `mlip_jobs`
(`MLIP_TEMPLATE_KINDS`: `mlip_opt` / `mlip_sp` / `mlip_freq` /
`mlip_opt_freq`, every task the MLIP pipeline runs — the frequency ones read
`freq_temp_k` / `freq_pressure_atm`), `crest_jobs` (`CREST_TEMPLATE_KINDS`:
`crest_conf`) and `next_kind` (`NEXT_KIND`: the
natural next step a level dropped on the + slot starts as — `opt` at a row's
start or after `mlip_opt`, `freq` after `opt`, `ts_freq` after `ts_opt`,
`irc` after `ts_freq` / `ts_opt_freq`, `sp` after `crest_conf` (the
conformer-screening stack) and after anything else). The queue
window's set editor (DESIGN.md §17.6) writes through `save_inp_set` /
`delete_inp_set` only. `save_set` also refuses a `{{...}}` that is not one of
`KNOWN_PLACEHOLDERS` (`{{GEOMETRY}}`, `{{JOB}}`, `{{CHARGE}}`, `{{MULT}}`,
`{{HESS}}`): nothing fills it, so it would reach ORCA verbatim. Only on save —
a set already in the file is never dropped on read for that rule. Pushing a
saved set's text onto the pending blocks made
from it is an ordinary `update_calc` batch the user is offered, never
automatic. A block's Type change is an `update_calc` `{kind}` patch; the store
moves `config.kind` with it, and refuses (not on restore) a `{{JOB}}` text for
a kind with no job keyword (`template_job_error`) — at launch it would be a
locked FAILED calc.

**Schema 1 → 2.** A file with `schema` < 2 (or a schema-1 kind) is migrated on
read: its bytes are copied to `inp_sets.v1.json` beside it first — once; an
existing backup is never overwritten, and a backup that cannot be written
leaves the original in place (load keeps the migrated sets in memory; a save
fails with the OSError). Each Opt/OptTS/Freq/IRC/SP set's `!`-line job
keywords (`Opt TightOpt LooseOpt VeryTightOpt COpt ZOpt OptTS ScanTS Freq
NumFreq AnFreq IRC SP`) become one `{{JOB}}` (appended when it had none), and
exactly the block the renderer now adds for its kind is removed when it is
there unchanged (the seeded `%geom MaxIter 200` [+ `Calc_Hess true`], `%irc
MaxIter 100 / InitHess read / Hess_Filename "{{HESS}}"`; whitespace and case
aside) — anything else the user wrote stays. Sets whose converted text is
identical merge into the first (its id and name kept; the five seeds become
one level); a converted set whose name another kept level has is renamed
`name (Kind)`. Calcs already in the queue keep their `raw_text`: a text
without `{{JOB}}` renders exactly as before. Such a block's text is the OLD
set's, so the editor would draw it as modified (`*`, amber) against the
migrated level although nothing was edited: `get_calc` therefore also returns
`levels`, `inpsets.level_forms(raw_text)` — what that text becomes under
`convert_legacy_text` for each legacy kind (the migration's own rule, so the
editor holds no second copy of it). A block is unmodified when its text is
its set's or one of those; and its set is the one its `set_name` names when
that matches, else (a set merged into another, or renamed `name (Kind)`) the
set of its kind whose text it migrates to, a renamed one first (`setOf` in
`qed.js`).

**Schema 2 → 3** only adds the CREST seed: a schema-2 file is read as it is,
`seed-crest` appended unless a set already holds that id or that name among
the CREST sets, and written back as schema 3 (no backup — nothing is changed).
A user who deletes the seed afterwards keeps it deleted: the file is schema 3
from then on. A schema-1 file gets the seed in the same pass as its migration.

The slot list is in `web/globals.d.ts`; the payloads are `InpSet`,
`InpSetsResult`, `InpSetResult`, `SystemSummary` / `SystemFull`,
`SystemResult`, `QueueOpsResult`, `SavedWorkflow`, `WorkflowsResult`,
`WorkflowResult`, `IrcStructureFile` / `IrcStructuresResult`, `XyzFileEntry` /
`XyzFilesResult` (schemas.py ↔ types.js),
plus the `QueueOp` union in `types.js`. `open_queue_window()` → `{ok}` shows /
raises the queue window (`MainWindow.show_queue_window`).

The Results window has no result picker of its own:
the rail is the picker (DESIGN.md §17.0e), *Open file… / Open folder…* bring in
anything else and put it in the rail's **Opened files** group (kept across
launches, see below), and the toolbar's
`Output | Visual` pair jumps to a section rather than switching a mode — Visual
is one of the report's sections there. `list_workspace_results` is therefore
called by no page now (the classic result picker was its one caller); the slot
stays. A finished cell also
carries **New from this**. The builder lives in the queue window, so the cell
hands a `{type: "build", seed}` message over (`NB.handOff`, the mailbox under
*Front-end state across launches*) and raises that window, which opens its
builder on a NEW calculation already referencing that run
(`openBuilder("", seed)`); the geometry is a reference, resolved at run time by
the same `_resolve_geometry` path as opt→freq.

**Front-end state across launches** (`orcamgr/uistate.py`, slots
`get_ui_state(key)` / `set_ui_state(key, valueJson)` / `take_ui_state(key)`). The Qt 6 default
WebEngine profile is off-the-record, so `localStorage` only lasts one sitting
(the display style in `mol.js` is per-sitting for that reason). What a window
must find again on the next launch goes to `ui_state.json` under
`user_data_root()`: a flat `{key: JSON}` map written tmp + `os.replace`, keys
≤ 64 chars, values ≤ 1 MB, `"null"` removes a key. A missing or corrupt file
reads as empty and a failed write is returned as data. It holds conveniences
only, never queue state. Keys: `rail.opened` (the Opened files rows, newest
first, ≤ 50; the file is re-read on click, so a moved file reports itself
then), `build.draft` (below) and `queue.handoff`: the one-slot mailbox the
main window leaves a message for the queue window in (*New from this*'s seed,
a dropped `.inp`, a calculation to select or edit), stamped `t` so one left
behind by a crash (older than 2 min) is ignored. The queue window takes it on
every poll and when it becomes visible, through `take_ui_state`, which reads
and removes the key under `uistate`'s lock in one step (`uistate.take`), so
two overlapping takes cannot both act on one message and a message written
meanwhile is never cleared unread; `NB.takeHandOff` also keeps one take in
flight per page.

**New-calculation draft.** `build.js` keeps the new calculation being built
under `build.draft`. The draft is `readForm()`'s payload (what `render()` takes
as its `calc`) plus what that payload leaves out:
- the Guided/.inp mode and text;
- the coordinates box, even under a referenced geometry;
- the constraint and scan rows, and `pickMode`;
- each backend's `cfgBy` / `kindBy`.

It carries a version, `v: 1`; an unreadable draft is dropped.

It is written 400 ms after the last edit, and at once when `open()` leaves the
new calculation. It is compared against the form as it opened: an untouched
blank or seeded form writes nothing, and a form emptied back to that baseline
removes the draft it wrote. A fresh unseeded *New calculation* opens on the
draft. A *New from this* seed wins, and replaces the draft only once edited.
*Add to queue* and the editbar's *Start over* forget the draft. The
edit-in-place builder never reads or writes it. An in-memory copy serves the
session, and a failed write is toasted once (P32).

**Hidden for now:** the reaction profile below and
ORCAdesk's own natural-orbital section (`SHOW_PROFILE` / `SHOW_OWN_NBO` at the
top of `web/next/results.js`). Their backends (`get_free_energy_profile`,
`run_nbo_analysis` and the rest) and their drawing code stay.

The **reaction profile** (`results.js`, the `#fep` section — it was the
free-energy profile, and the slot keeps that name) is built by the
user, not discovered: a list of **stages**, each holding species with
stoichiometric coefficients, where X(stage) = Σ coeff × X(calc) and the profile
is every stage against a chosen zero. It used to plot every DONE frequency calc
in queue order, which is a list of unrelated molecules rather than a reaction.
`get_free_energy_profile` therefore sends each candidate's **composition and
charge** (`FepPoint.atoms` / `.charge`, summed from the parsed geometry) so the
front-end can say when two stages do not hold the same matter — reported, never
enforced (P2). It sends **every DONE calc with an energy**, not only those with
a Gibbs value (`gibbs_eh` may be null): a monatomic ion has no vibrations to
compute, so filtering on "has frequencies" removed exactly the species that
cannot have them — `CoCl4²⁻ + 6 H₂O → Co(H₂O)₆²⁺ + 4 Cl⁻` needs that Cl⁻. X is any of
`RXN_QTY` — G, H, T·S, S, U, E+ZPE, E — so each `FepPoint` carries the whole
thermochemistry (`zpe_eh`, `total_thermal_eh`, `enthalpy_eh`,
`entropy_term_eh`, `temperature_k`, each nullable); S is derived per species as
T·S / T, since ORCA prints the term. The diagram is drawn on one quantity and
the table shows every quantity some stage has, against the reference or per
step (`_fepCmp`). Quantities are never mixed: a species that cannot supply the
chosen one is listed in the picker saying so, and the stages holding it read as
a dash. A stage with an unfilled row likewise has no value at all rather than a
partial sum, and species at different temperatures are flagged, not refused.

A cell's chart is `web/next/charts.js`, which uses `scf_graph.js` /
`progress_panels.js` as **parsers only** and draws in the design's own class
vocabulary. Three rules there are load-bearing: the chart height is a **fixed
number with its budget beside it** (270) because the pane next to it caps its
`.logbox` at 425px and a proportional height pulled the two columns apart; a
geometry convergence is drawn as **five series**, each value over *its own*
tolerance, so one goal line at ratio 1 serves all of them; and a running cell's
two convergences — the whole optimization and the cycle solving now — are a
**toggle** (`_gkind` in `stream.js`, `CHARTS.convergence(..., kind)`), shown only
when both have data, never two stacked charts. DESIGN.md §17.4h / §17.4i.
An **IRC** cell draws its path instead (`CHARTS.irc`, from
`progress_panels.js` `IrcTracker`): ΔE against the TS in kcal/mol, linear,
backward to the left and forward to the right of the TS, each direction's
steps / ΔE / max|G| and RMS(G) against their thresholds in the key (ORCA
needs both under to end a direction), and progress from
MaxIter and the directions asked for. Before it the cell showed whichever
single SCF had run last. `get_graph_lines` keeps the same lines
(`_IRC_GRAPH` / `_IRC_ROW` in `core/replay.py`, pinned in `test_log_replay.py`)
so a reopened IRC rebuilds its path from the `.out`.

A **relaxed surface scan** is one optimization per point in one `.out`, each
point's cycles numbered from 1 again, so `GeoTracker` (keyed by cycle number)
starts a fresh chart on each `RELAXED SURFACE SCAN STEP N` banner and reads the
point count from `There will be N constrained geometry optimizations`. A point's
`OPTIMIZATION RUN DONE` is not the run's: `done` waits for the last point,
`progress()` is over the points, and `estimateETA()` is the points left at the
mean finished point's own ORCA seconds (none until one has finished). The cell
shows the current point's chart, *scan point k/N* and `totalCycles()`.

**What a reopened cell replays** (`orcamgr/core/replay.py`, Qt-free, behind
`get_graph_lines`). The history used to be "the last 2000 relevant lines" per
kind. On a long run that cut off exactly the lines that say what the run is:
a 61-point, 2809-cycle 2D scan lost its `There will be 100 ...` header and
points 1–35, so the reopened cell had no *scan point k/N*, a cycle count of
the points it still saw, and the ETA of one point. `GraphDigest` now keeps
lines by class:
- **skeleton, whole:** cycle banners and wall times, scan header and point
  banners, done / post-stage markers, each SCF settings block's `Energy
  Change TolE .... x Eh` line (`_G_TOLE`, prefilter word `TolE`; it mirrors
  `scf_graph.js`'s `TOLE_RE` — the optimizer's flush-left "Energy Change
  TolE" criterion is not kept), an IRC's settings, banners and step
  rows, CREST's markers, freq / TD-DFT stage banners. It grows about
  100 bytes per optimization cycle;
- **the echoed input** (`|  1> ! ...`, `%pal`), the first 200 lines;
- **the current optimization's convergence tables** (a scan's current point;
  a finished point is filed by its cycle count and seconds, both in the
  skeleton);
- **windowed to the recent 2000:** SCF iteration rows and the freq / TD-DFT
  progress counters (`_PHASE_COUNTER`), where the latest is what counts.

Reading is byte-level: `candidate_starts` finds the lines holding one of the
patterns' fixed words (`_WORDS`, `bytes.find` per word) or starting like a
numeric row or an echoed input line, and only those are decoded and
classified. A table's closing rule is not decoded; a blank line stands in for
it, so the tracker still commits the step. On a 157 MB scan output this is
3.6 s against 33 s for line-by-line regexes, with identical output (checked
on real scan, IRC, CREST and freq outputs; `test_replay.py` pins the
equivalence, and `test_log_replay.py` runs every sample line through the
prefilter, so a pattern added without its word fails a test).

`ReplayCache` (one per Bridge, LRU of 16 files) keeps each digest with the
byte offset it reached, plus the 64 bytes before it. A later call reads only
what was appended, and only whole lines, so a line ORCA is still writing is
read once, when complete. A file that shrank, or whose bytes before the
offset changed (a re-run), is read from the start. With more than 4 MB unread
the read runs on a worker thread and the slot answers `{ok, pending: true}`:
`boot.js` `seed()` asks again on the next poll, and the cell's fact line says
*reading the output…* until it lands (`NB.seedPending`).

**A queued calc's report off the UI thread** (`parse_calc_output_async`). A
finished 157 MB scan output took 20 s to parse, and `parse_calc_output` did it
on the Qt main thread whenever the Jobs thumbnail (a DONE calc's final
geometry) or the report asked, so a finished long run froze the window at
every launch. The payload is now cached per `(output path, kind)`, stamped
with the file's `(size, mtime_ns)` (LRU of 8). The async slot answers from the
cache, parses an output of 8 MB or less at once, and hands a larger one to a
worker thread, answering `{ok, pending: true}` until it is done; a failure is
returned once, to the next ask. `NB.parseCalc(name, onWait)` polls it every
500 ms (`stream.js` thumbnail, `results.js` `open`, which shows *Reading the
output of …* and drops a late answer if another result was opened since).
CREST and MLIP results are small and parse at once. The synchronous
`parse_calc_output` reads and fills the same cache; no page calls it since
the classic front-end went, and the slot stays.

A cell's **Output pane** (the `.logbox` beside the chart, the last 260 lines
of the `.out`, re-read every poll while the job runs) follows the end **only
while the reader is at the end** — per box in `stream.js` `_logs`, read off
the box's own scroll events. Scrolled up, a poll leaves it where it is and an
`.act` pill, *↓ Latest · N new*, appears on the box's corner. Each read is laid
over the previous one rather than replacing it (`slid` / `paintLog`: the lines
that left the window come off the top, new ones are appended), and the first
line on screen is put back at its offset. That keeps the reader on their line
while the window slides, and keeps a selection through a poll. It used to scroll
every RUNNING job to the end on every poll. DESIGN.md §17.4n.

**A surface is drawn in the Visual panel, not in a pop-out**:
picking an orbital or the ESP map plots it on the panel's own stage, and the
window is the same picture with the isovalue beside it. The isosurface renderer
is one shared object (`volumeView` in `mvwin.js`, reached as `NB.mv.vol`) with
**one WebGL context** for the whole app; the box that holds it (`#mv-gl`) is
re-parented to whichever stage is drawing (`adopt`), and anything about to
re-render a stage sends it home first (`park` — `results.js paint()` does, since
it replaces `#reswin` wholesale). Closing the pop-out hands the box back through
`NB.viewer.reclaimSurface()`. The panel carries the surface controls under its
list (`.vizctl`, an addition to the reference — DESIGN.md §17.4a): **isovalue**
(and, for an ESP map, the colour half-width) re-mesh in place through
`NB.mv.vol.setIso` / `setEspRange` with no backend call, while **grid**
(`_gridByKind`, per kind) is a fresh orca_plot run — so it goes back through the
same question and re-answers every *ready* mark via `NB.mv.readyAt(opts, pick,
grid)`, which takes the grid because the same orbital at 40³ and 80³ are two
different files. A kind with one instance gets no list at all (§17.4g). Two contexts would be one too many: a browser
keeps only a handful alive and drops the oldest without asking, and the panel's
stage is destroyed on every report render.

One rule the stages all depend on: **a `.molcv` is
`position:absolute; inset:0` and its ResizeObserver watches the stage, not the
canvas.** `mol.js` sets the bitmap from the canvas's own measured box every
frame, so in flow the bitmap becomes its own input wherever `height:100%` cannot
resolve — which is every stage sized by a grid row. Invisible at
`devicePixelRatio: 1`, a 1.25x-per-resize runaway above it. DESIGN.md §17.4f,
and the named block at the bottom of `app.css`.

The report's Output sections are purely presentational over `ParsePayload` —
with one section that is presentational over a *second* payload: the
**Natural orbitals** section (`viewer.js`, over `run_nbo_analysis` /
`get_nbo_status` / `get_nbo_result`) is computed from the run's `.gbw` on
demand rather than read from the parse, so it opens as a *Compute natural orbitals*
button and renders in place when the background job lands (see the NBO
section below). It is gated like the other electronic-structure sections.
A QUEUED
calc's result is fetched by NAME through `bridge.parse_calc_output_async` (or the
synchronous `parse_calc_output`), which
dispatches on the calc's **kind** (`result_from_output`); external files
(*Open file…* / *Open folder…*, `parse_out_path`) go through `bridge._parse_path`, which has no kind and
dispatches per backend heuristically (an engine-written `*.mlip.json` → the MLIP
parser; `crest_conformers.xyz`/`crest_best.xyz` siblings → the CREST parser; else
the ORCA parser). Keep the heuristic OFF the queued path: workspace folders
survive removal, so a calc reusing a removed CREST calc's name would otherwise
parse as a conformer search.

**`list_workspace_results`** scans `{workspace}/*/` and, for a system's
calcs, `{workspace}/*/*/` for `{name}.mlip.json`
else `{name}.out` (newest first, capped at `_WORKSPACE_SCAN_MAX`; the folder
convention itself is `orcamgr/resultfolder.py` `result_artifact`, shared with
the folder picker below). The workspace result picker that listed them went
with the classic front-end — the rail is the picker now (above). A result is
addressed by a **source string** (`calc:<name>` / `file:<path>`, the same
addressing the cube slots take), and whether it is queued comes from the
store, never from "have we parsed it yet" — otherwise a queued calc would be
sent through the folder heuristic.

*Open file…* and *Open folder…* are two pickers over **one** route,
because the user is choosing a result, not a way of reading it:
`bridge.pick_result_file()` picks the file and returns
`{route: "parse"|"structure"}` by suffix; the notebook reads the parse route
through `parse_out_path` (`openPath` in `results.js`) and answers the
structure route (a bare `.xyz`) with a toast — opening a lone `.xyz` went with
the classic front-end.
`bridge.pick_result_folder()` names a **run folder** instead and resolves it
to the result file inside (`resultfolder.find_result`: the convention above
first, then the folder's only — or newest — `.out`, since an ORCA run made by
hand names its output freely), always the parse route; a folder holding
nothing to read is refused with the sentence that says what would have
worked. The picker does not take structure-only folders: the Visual panel
already lists every `.xyz` set beside a result.

Both parse paths
send the *whole* `ParseResult` (every section the parser found) plus two gating flags
— `is_optimization` and `show_elec` (`= ParseResult.shows_electronic_props`) — and the
front-end (`web/next/results.js`) decides what to show per calc kind (the
`"elec"`-tagged summary rows, for instance, only under `show_elec`). The
report's **Show all** (`_showAll` in `results.js`) overrides the gating to
reveal everything parsed. Keep the gating on the front-end (not the payload)
so the toggle re-renders without a re-fetch.

**The Visual panel discovers what there is to show.** It (`viewer.js`, and the
pop-out's list in `mvwin.js`) asks about the result's source string and builds
one entry per openable thing:
the parsed geometry (no backend call — P4), every `.xyz` set
`bridge.list_structure_sets(source)` found beside the result
(`molview.discover_structure_sets`: direct subfolders holding `.xyz` — the
`conformers/` export sorts first — then the folder's own `.xyz`, each labelled
by what it *is*; past `_MANY_XYZ` unnamed ones they collapse into a single
whole-folder set, so a hundreds-strong export is one entry and not a hundred file
reads; a relaxed scan's `{base}.allxyz` is the *Scan points*
set, read in step order and named `Step k`, and ORCA's numbered per-point
copies `{base}.NNN.xyz` are then left out as duplicates. `iter_xyz_frames` skips
the `>` lines between `.allxyz` frames, and `_parse_energy` reads ORCA's
trailing `E <Eh>` comment), and, when a `.gbw` sits there, the orbitals and
the ESP map. A set opens through
`bridge.get_structure_frames(path)`, which takes a file or a folder. This
replaced a *Browse .xyz…* folder picker (`browse_xyz_folder`) and the
CREST-only `get_conformer_frames`, both removed: everything worth opening
already sits in the run folder, so the panel lists it rather than asking the user
to find it. `frames_from_folder` reads a folder's `conformers/` child when the
folder itself holds no `.xyz`, so pointing it at a CREST run means the export.
The CREST ensemble keeps its `calc:<name>` favorites key and its `{run}_c1`
frame labels (`_CREST_ENSEMBLE` in `bridge.py`) — labels key the favorites store
and name what the favourites export (`export_frames`) writes, so the two must agree.

A surface's *ready* mark and the fetch that follows it go through one
cube-name rule (`cubeName` / `NB.mv.readyAt` in `mvwin.js`, above), so the
mark and the fetch cannot disagree about the filename.

## Running the queue: core/ is GUI-agnostic

A run is started via `QueueStore.start_run(engine_factory)`, which spins a daemon
thread and calls `QueueEngine.run_all()` (`orcamgr/core/queue.py`). The engine talks to
the rest of the app only through `QueueCallbacks` (`log`, `calc_update`) — it has no
knowledge of Qt or HTTP. `make_engine_factory` wires those callbacks back to the store
(`log -> append_log`, `calc_update -> touch` bumps the version).

The `core/` pipeline:
- `input_generator.py` — `StepConfig` → ORCA `.inp` text (`build_input`). Handles
  solvation (CPCM/SMD), RI, per-element basis/ECP, charge/multiplicity, raw-input mode
  (verbatim text with a `{{GEOMETRY}}` placeholder — and, for an inp-set
  template, `{{JOB}}` / `{{CHARGE}}` / `{{MULT}}` / `{{HESS}}`, filled at
  launch by `render_template_text` (called from
  `QueueEngine._render_template_values`; the queue editor's preview,
  `build_inp_preview`, shows the same function's output — there is no second
  renderer). `{{JOB}}` becomes the calc kind's job keyword
  (`TEMPLATE_JOB_KEYWORDS`: opt `TightOpt`, ts_opt `OptTS`, freq / ts_freq
  `Freq`, opt_freq `TightOpt Freq`, ts_opt_freq `OptTS Freq`, irc `IRC`, sp
  `SP`, nmr `NMR`, neb_ts `NEB-TS`, tddft none — the token is removed, its
  `%tddft` block is the job, and a `!` line left empty goes; general and the
  MLIP / CREST kinds are a ValueError → `OrcaRunError` before launch) and that
  kind's blocks are merged in by the same helpers `build_input` uses
  (`_geom_job_lines`, `_irc_lines`, `_constraint_line`, `_scan_line`), with
  the **block's options** — its `config`, applied only to a level
  (`is_level_text`; the level owns method / basis / RI / `%pal` / `%maxcore`
  / `%basis`, so those fields are never read). With no options the result is
  byte-identical to what a level always rendered as (pinned in
  `test_level_options.py`, and a level with options renders exactly as a
  Guided calc with the same settings). The merge never writes a second copy
  of a block: `!` line — the job keyword, then the SCF tier, `options` and
  the solvation keyword, each only when no `!` line holds it already (another
  SCF tier, or any solvent keyword / `%cpcm` in the level beside a block
  solvation, is refused). `%cpcm` (a solvent named with spaces), `%geom`
  (optimizations: `MaxIter`, `Calc_Hess true` for a TS, `Constraints`,
  `Scan` for opt), `%freq` (`Temp` / `Pressure` off their defaults), `%tddft`,
  `%irc`, `%neb` (`NEB_End_XYZFile "product.xyz"`, `Nimages`, `PreOpt_Ends`,
  `NEB_TS_XYZFile`): a block the level lacks is inserted whole right before
  the coordinate block (`* xyz` / `* xyzfile`; at the end when there is
  none); into one it has (`_find_block`: a small parser counting nested
  sub-block `end`s) only the lines it lacks go, before its closing `end` — or,
  for a block written on one line, as words before that line's last `end`
  (verified on 6.1.1: `%geom Trust 0.2 MaxIter 200 end`, `%freq Temp 300
  Pressure 2.0 end` both honoured). `%geom` gets `MaxIter` only where it has
  none and `Calc_Hess` only where it names no `Calc_Hess` / `InHess` — so a
  level with its own `%geom` no longer loses the TS Hessian and the MaxIter
  cap (it used to win whole). An `%irc` the level has takes only options off
  their default (an `auto` initial Hessian adds nothing). `%eprnmr` (NMR
  J-couplings) goes after the coordinate block's closing `*`. **Refused,
  never guessed** (`TemplateConflict`, a ValueError naming the block and key):
  a key set on both sides, a `Constraints` / `Scan` list on both sides,
  constraints / scan into a one-line `%geom`, a block with no closing `end`
  that must take lines, a second `%eprnmr`. Two `%geom` blocks in one input
  are not relied on. `template_option_conflicts` is the one judgement: the
  store refuses on it, and `_render_template_values` stamps the row BLOCKED
  (`OrcaBlocked`) on it before launch. The resolved structure's atom count
  checks constraint / scan indices as `build_input` does (a referenced
  geometry's at launch). IRC initial Hessian (`irc_init_hess`): `auto`, the
  level block's default — `%irc MaxIter 100` with `InitHess read` +
  `Hess_Filename "<staged .hess>"` when a parent left one, else `InitHess
  calc_anfreq`; an explicit `calc_anfreq` / `calc_numfreq` stages no parent
  Hessian (`template_hess_need` honours it); `read` names `irc_hess_file`, a
  picked absolute path copied into the run folder by `_stage_irc_hessian`
  (the input names it bare) or a run-folder name. `_wants_scan` and the
  kept-result scan check count a level block's `geom_scan`;
  `_prepare_neb_side_files` reads the rendered text (a level NEB-TS without
  `neb_product_xyz` is BLOCKED, not FAILED). The `.hess` is found by
  `find_parent_hessian` (the `after` parent's folder, `{after}.hess`, else the
  geometry reference's, `{ref}.hess` — each parent's own result folder, in
  its own system's folder) and copied into the run folder; a text
  that names `{{HESS}}` itself needs one (none is an `OrcaBlocked` before
  launch, naming where it looked: the row is BLOCKED — editable, re-tried on
  the next Run — not FAILED), a `{{JOB}}` IRC does not. The raw IRC
  Hessian guard then reads the RENDERED text), NEB-TS side `.xyz` files,
  and **`%geom Constraints`** (below). **A setting at ORCA's own default is not
  written**: only the method and basis must be named, and the builders seed
  every other field with ORCA's default (`StepConfig`'s, via
  `get_step_defaults`; an empty RI / SCF select is "ORCA default"). Measured on
  6.1.1 (H2O, def2-SVP) with the keyword left out: a hybrid runs RIJCOSX with
  def2/J chosen by ORCA (also with a bare `RIJCOSX`); HF and MP2 run without
  RI; Opt, OptTS, IRC and NEB raise the SCF to TightSCF themselves (TolE
  1e-8), the `TightOpt` ORCAdesk writes for opt / opt_freq raises it to 1e-9
  (VeryTightSCF's TolE), while SP, Freq and TD-DFT stay at NormalSCF (1e-6) —
  `SCFGraph.tierFor` gives the progress bar that same tier for an empty
  setting until ORCA's own printed `TolE` arrives (`SCFTracker.tolE`, read
  from the SCF settings block by `TOLE_RE`, which then wins:
  `SCFGraph.scfTarget(tracker, scfConvergence, kind)` is what every progress
  bar and goal line reads), so a template with its own SCF keyword is aimed
  right too; `%tddft` TDA on, triplets
  off, 3 roots; `%neb` 8 images. So `%tddft` is `nroots` plus whatever
  differs; `%pal` only above one rank. **Kept as ORCAdesk's own by the user's
  choice**: an optimization is `TightOpt` (`TightOpt Freq`), and `%geom
  MaxIter` (default 200) and `%irc MaxIter` (default 100) are always written —
  ORCA's 50 / 20 run out on a floppy molecule or a long path. What else is
  still written although ORCA would default otherwise, each for a measured
  reason: `AutoAux` for a double hybrid (ORCA aborts "RI-MP2 needs an
  AuxC basis") unless RI is NoRI, likewise for a method RI by its own name
  (RI-MP2, OO-RI-MP2, OO-RI-SCS-MP2, F12-RI-MP2, RI-MP3) or RI-only (ADC2:
  "Aux/C requested but not available" after the SCF) — `_is_ri_method`, so
  at RI "ORCA default" they run (RI-MP2 on H2 verified on 6.1.1) — and for
  DLPNO always; plain MP2 / MP3 / CC still run conventionally without an aux; `def2/JK` under RIJK
  ("no AuxJK basis set"); `def2/J` beside an aux the user typed that is not a
  /J one (ORCA then picks none: "no AuxJ basis set"); `Calc_Hess true` for an
  OptTS; `InitHess calc_anfreq` for an IRC (ORCA's own is a numerical Hessian).
  The NEB-TS kind's `FREQ` is in its Extra keywords, where it can be removed.
  Calculations stored under the older defaults (TightOpt, TightSCF /
  VeryTightSCF, RIJCOSX, nroots 40) keep and write those values.
  About **`%geom Constraints`** (`StepConfig.geom_constraints`: a list of
  `{kind: C|B|A|D, atoms: [0-based], value: float|None}` where None holds the
  starting value). They are written inside the ONE `%geom` block, and only for
  `CONSTRAINED_KINDS` (opt / opt_freq / ts_opt / ts_opt_freq); any other kind
  writes no `%geom` block, so they are simply not in its input. `general` is excluded on purpose:
  its job line is free text. `normalize_constraints` (called from `from_dict`,
  the trust boundary) DROPS a malformed entry (unknown kind, wrong atom count,
  non-int/negative/duplicate index, non-finite value) rather than guessing,
  clamps a bond to 0.1–50 Å and an angle to 0–180°, wraps a dihedral, and
  dedups a constraint named in either direction. An index past the last atom
  is refused (`constraint_index_error`) by `build_input`, and by
  `calc_from_dict` at add time for direct coordinates (not on restore). ORCA
  6.1.1's own response to such an index is `Could not make G matrix` in setup.
  A template's `{{GEOMETRY}}` defers the check to the run-time call with the
  resolved geometry. A level block's constraints and scan (its options) are
  held to the same index rule, at add time against its direct coordinates and
  at launch against the resolved ones (`render_template_text(..., n_atoms)`).
  **An angle past 175° is a linear bend to ORCA**, which it
  can only hold where it starts: a valued `A` constraint whose start OR target
  is past `LINEAR_BEND_DEG` stops in setup ("For a linear bend you can only
  keep the initial value"), even a 180.00 target on a 180° start — measured on
  6.1.1, 175.0 runs and 175.5 stops; without a value the same angle is held.
  `constraint_linear_warning` WARNS rather than refuses (the user's call, and
  the rule is one version's measurement): `store.constraint_warning` rides on
  add/update as `MutationResult.warning`, which the builder shows as a `warn`
  toast (the target alone for a referenced geometry). The format and ORCA's IUPAC dihedral sign
  were verified on 6.1.1. The builder picks them on its structure stage (DESIGN.md
  §17.4l).
  **`%geom Scan`** (`StepConfig.geom_scan`: a list of `{kind: B|A|D, atoms,
  start, end, steps}`) is a relaxed surface scan, written in the same `%geom`
  block after the `Constraints` (`    B 0 1 = 0.9000, 1.1000, 5`), and only
  for **`SCAN_KINDS` = `("opt",)`**. Measured on 6.1.1: an SP reads the block
  and ignores it, an `Opt Freq` computes the frequencies after the FIRST point
  only, a Scan and Constraints coexist (both act), four coordinates stop in
  input parsing (ORCA's limit is 3), a dihedral range past 180 runs, and a point
  whose optimization does not converge error-terminates the whole run.
  `normalize_scan` (from `from_dict`) drops a malformed entry (no `C`, wrong
  atom count, bad index, non-finite start/end, start == end as written),
  clamps the ends (bond 0.1–50 Å, angle 0–180°, dihedral −360…360°, never
  wrapped, because a full turn is a range) and `steps` to 2–1000, dedups, and
  cuts past three. An index past the last atom is refused by the same
  `constraint_index_error(..., "scan")`, in `build_input` and at add time.
  The builder offers one coordinate, as the *Scan* half of the constraint
  panel's *Constrain | Scan* switch (DESIGN.md §17.4r).
- `runner.py` — `OrcaRunner` launches ORCA **detached**, with ORCA writing its own
  stdout straight to the `.out` file (not via a Python pipe), so a run survives
  ORCAdesk closing. It is invoked with the input's **bare file name** and `cwd`
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
  against PID reuse via `create_time`. `monitor()` has one more way out, a
  **hang watchdog** (`OrcaHung`): ORCA 6.1.1 can print `error termination` for
  a failed NumFreq displacement and then never exit (measured: a CCSD(T)
  NumFreq that spun one core for 14 h with an idle folder). After a fatal marker
  (`_FATAL_MARKERS`), once the whole run folder has been silent for
  `_HUNG_AFTER` (10 min), the tree is killed and the calc FAILED. The queue
  appends the runner's note to the `.out` cause. Silence is measured over the
  folder rather than the `.out`, because a NumFreq's `.out` is quiet while its
  `_D` files move. A reattach scans the file for the marker, since it tails from
  EOF. Silence without a marker is never a hang. The first marker also fires
  `monitor(on_fatal=...)`. The queue sets `Calculation.faulted` and puts
  ORCA's line in the message, but leaves the state RUNNING: the process still
  holds its cores and Stop still applies. `calc_to_dict` sends `faulted` only
  while RUNNING, and both front-ends draw such a row as failed inside the
  running group (DESIGN.md §12). The flag is not persisted, because a
  reattach finds the marker again.
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
  it and the engine warns about a raw one (P57). Qt-free, psutil
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
  frequencies with their IR intensities (`ir_intensities`, km/mol, one per
  `frequencies` entry, from the `IR SPECTRUM` table after the last frequency
  block, matched by ORCA's mode number — the table starts at mode 6, or 5 for a
  linear molecule, and on ORCA 6.1.1 has no row for an imaginary mode, which
  therefore reads 0; `[]` when there is no table, and the report then draws
  every band at one height), full thermochemistry (U/H/G/ZPE/T·S/temp/pressure), TD-DFT
  transitions + excited-state composition, NMR, NEB/IRC path — both print a
  `PATH SUMMARY` table; `neb_path_kind` records which, so the report titles
  an IRC profile as an IRC — and a relaxed surface scan: `scan_coords` from
  the banner, in ORCA's printed atom order, and `scan_points`, one
  `{values, e_eh, de_kcal}` per point, from the `RELAXED SURFACE SCAN
  RESULTS` table's *Actual Energy* rows (any number of columns). If there is
  no table (an error termination mid-scan, or a scan still running), the
  points come from the step boxes whose optimization reached `OPTIMIZATION
  RUN DONE`), and the **NBO program's printout** (`nbo_program`, from a
  `! NBO` run: NPA summary + configurations, the Lewis accounting, the NBO
  summary list and the E(2) table; formats measured on NBO 7.0.10 closed
  shell, `tests/test_nbo_program_output.py`; an open-shell run keeps only the
  first NPA summary, its per-spin tables unread until there is a sample).
  These are NBO's numbers, never mixed with ORCAdesk's own `orcamgr/nbo/`
  analysis: the notebook report draws them as three `NBO · …` sections
  (`nboProgram` in `web/next/results.js`) above the *Natural orbitals* one.
  ORCA's own **orbital localization**
  printout (`localization`, `%loc` — method, MO range, thresholds, the
  one-/two-centre counts and each LMO's atom shares; measured on IAO-IBO,
  `tests/test_localization_output.py`) is read the same way and drawn as the
  *Localized orbitals* section (`localized` in `results.js`); its delocalized
  LMOs are counted but not listed, and the IAO partial charges are skipped
  (ORCA's own warning: meaningless unless every occupied MO was localized).
  Marker-based,
  tolerant of `\r\n`; when a value recurs (e.g. across opt steps) the **last**
  occurrence wins. `ParseResult.summary_rows()` returns `(label, value, category)`
  rows where `category="elec"` tags general electronic-structure rows; the
  `shows_electronic_props` property (`is_optimization or not specialty`) decides
  which sections the report shows per kind — see the Results-gating note.

## Queue semantics (important invariants)

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
  name of its own, so the tag is what lets the per-job graphs
  separate them. `QueueCallbacks.log` is `(msg, level, calc="")`. On the
  front-end (`web/next/boot.js`) the convergence trackers are a **per-calc
  bundle** keyed by that tag (`NB.trackers(name)`, fed by `feed(calc, msg)`
  from the `get_log` poll, dropped by `pruneTrackers` when the calc leaves the
  queue), so each cell charts its own job. `QueueSnapshot.resources` feeds the
  rail's occupancy strip (`occupancy` in `rail.js`).
- **The queue stays editable while it runs (live queue, P29).** `start_run`
  hands the engine the store's **own list**, and the engine walk picks the
  next unhandled row (tracked by object identity) under the store's **own
  lock** (`make_engine_factory` passes it). So while a run is in progress:
  a calc added lands in the *same* run; PENDING/CANCELLED/BLOCKED rows can
  be removed, edited (an edited row is a new object — the walk picks it in
  its edited form), and reordered (the walk follows live list order).
  Still protected mid-run: the in-flight calcs (state RUNNING plus
  `QueueEngine.active_names` for the picked-but-not-yet-stamped window),
  DONE/FAILED rows (never edited — P24; but they CAN be removed mid-run, the
  list being only the record), `clear`, and
  reference integrity — a mid-run add/edit with a dangling reference and a
  remove/rename of a referenced parent are refused at the store, because
  the pre-run screen (`find_dangling_reference`) never sees mid-run
  mutations. A mid-run add whose name already has a result on disk is
  confirmed in the UI first (`bridge.has_existing_output`; the queue editor's
  adds that bypass the builder — a dropped set, a paste, a row copy, a
  duplicate — ask `existing_outputs(names_json)` → `{ok, existing}`,
  the same rule for many names, which its free-name search also skips; a new
  name counts output in `{workspace}/{name}/` or in any system folder, since
  where it will run is not known yet — see the run-folder bullet) — the
  Run-click overwrite modal never sees it, and the engine's keep-existing set is
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
- **A calculation runs in its own folder, and a system's calculations run
  under the system's folder** (`orcamgr/core/rundir.py`, the one place the
  rule lives): `{workspace}/{system name}/{name}/` for a calc whose `system`
  names a lane, `{workspace}/{name}/` for a standalone one. (Up to 0.11.3 every
  calc ran in `{workspace}/{name}/`: 20 molecules x 6 steps was 120 folders
  side by side.)
  - **The folder is decided when the calc first starts, and recorded**
    (`Calculation.run_dir`, relative to the workspace, `/`-separated): the
    engine's `_claim_run_folder` sets it at the RUNNING stamp of every backend
    (ORCA, MLIP, CREST) and when keep-existing adopts an output, from the
    system's name **at that moment** (`QueueEngine(system_names=...)`, wired to
    `QueueStore.system_name` by `make_engine_factory`, so a rename made while
    the queue runs is followed by every calc not yet started). From then on
    the record wins: `run_folder` = recorded, else planned from the current
    system name; `result_folder` (where files are READ: references, `.hess`,
    IRC end points, the report's slots via `_calc_run_dir`, the IRC picker) =
    `output_path`'s folder first, then `run_folder`. A system renamed or a
    block moved to another row (`place_calc`) therefore never moves a calc
    that has run, and **nothing on disk is ever moved or renamed for it** (a
    job may be running there) — the same rule as removal. A calc BLOCKED by a
    pre-launch check (`OrcaBlocked`: nothing ran) hands back a folder that
    attempt claimed, so it follows its system like any unstarted calc.
  - **A re-run runs where the calc ran**: a CANCELLED calc keeps `run_dir`
    (an edit under the same name carries it, `_replace_in`; a rename drops it,
    a new name being a new folder), and `rerun_calc` copies it onto the rebuilt
    row — the overwrite warning was about the output in THAT folder.
  - **The record is never taken from a client**: `calc_from_dict` does not
    read it (like `rerun`); `calc_from_session_dict` restores it only through
    `restored_run_dir` — `name` or `{folder}/{name}`, each part passing the
    folder rule, so it is always inside the workspace and named after the calc.
    It rides `calc_to_session_dict` (`CalcFull.run_dir`, read-only for the
    page).
  - **`session.json` schema 3.** A calc of an older file that has run (done,
    failed, cancelled or running, or holding an `output_path`, or a pending
    `rerun_calc` row) ran in `{workspace}/{name}/`, and `load_session` records
    exactly that, so it is found, reattached and re-run there whatever system
    it belongs to now; one that has not run follows today's layout.
  - **A system name is its folder, verbatim, and an unsafe one is refused,
    never mapped.** It already obeyed the calc-name rules (`system_from_dict`
    → `_validate_calc_name`), whose path-safety part now lives in
    `rundir.folder_name_problem` for both: separators and `: * ? " < > |`,
    control characters and lone surrogates, > 120 characters, `..`, a trailing
    dot **or space** (measured on Windows 11: `mkdir("aux ")` makes `aux`, and
    the path built from the name then opens nothing), and the Windows device
    names whatever the extension — `con .txt` included, and the superscript
    `COM¹²³` / `LPT¹²³` Windows reads as digits. Mapping was rejected because
    the user finds the folder by the name they see, and two names could map
    to one folder (`a:b` / `a_b`), which a refusal cannot. Restoring relaxes
    only ORCA's rule (a space is fine in a folder). `system_folder` still
    checks, so a name that bypassed validation runs in the workspace root.
  - **Overwrite and keep-existing look where the calc runs.**
    `_overwrite_conflicts` (the Run modal and the startup resume) and the
    engine's `_try_keep_existing` read the calc's `run_folder`; a
    `{workspace}/{name}/` from before system folders under a calc that is now in a
    system is neither a conflict nor kept — running the calc cannot overwrite
    it, and it is not this calc's output. For a name NOT in the queue yet
    (`has_existing_output`, `existing_outputs`: the builder's and the queue
    editor's mid-run guard and free-name search) the system it will run under
    is not known, so `rundir.names_with_output` counts output in
    `{workspace}/{name}/` or in any `{workspace}/{folder}/{name}/` (one listing
    of the workspace and of each folder for all names).
  - A system folder may share its name with a standalone calc's folder; the
    files cannot collide (a run folder holds `{name}.*` and a few fixed names,
    a system folder holds run folders named after their calcs).
- **Calculation `name` is unique and names its run folder** (above).
  Uniqueness is enforced in the store,
  **case-insensitively** — Windows resolves `water` and `Water` to the same
  folder, so accepting both would share one `.out` between two calcs. The
  invariant holds on every entry path: `add`/`replace` **and session
  restore** (`load_session` dedups, first occurrence wins). The name is also
  the `.inp` file name ORCA is *invoked* with, so for an ORCA-backed kind it
  refuses a **space, `&`, `,`, `;` or `=`** on top of the Windows
  path-dangerous set
  (`_ORCA_HOSTILE_CHARS`; `mlip*`/`crest*` are exempt — those pipelines pass the
  name as an argv element or a shell-quoted variable, and refusing it for every
  kind would drop such a calculation from a restored session):
  ORCA 6 splits its own argument on whitespace, and the run then dies in
  Startup — a FAILED calc, which is locked (P24), so the name could never be
  run again. `,`/`;`/`=` are refused for a **second** reason, found after they
  had been measured harmless to that first parse: ORCA collects its gCP
  helper's output through a cmd.exe redirection it does **not** quote
  (`otool_gcp "{name}.gcp.in.tmp" ... > {name}.gcp.out`), and cmd ends a
  redirection target at exactly those delimiters — so under `(S,S)mol` the
  result lands in a file called `(S` and every gCP-carrying composite method
  (r2SCAN-3c, B97-3c, HF-3c, PBEh-3c, an explicit `! GCP(...)`) dies with
  `Calculation of the gCP correction failed!` once the SCF is already paid
  for. `(`, `)` and `'` survive both parses and stay legal, which is why this
  is still a five-character rule and not a safe-character list.
  **The rule is not applied retroactively**: `_validate_calc_name(...,
  restoring=True)` (the `calc_from_session_dict` path) skips it, because it
  says "a new run of this would fail", not "this path is dangerous" — and
  `load_session` DROPS an entry that fails validation, so tightening it would
  silently evict the user's record of an already finished or failed run. Every
  path-safety rule still applies on restore; only this one bends.
- **The queue autosaves to `%APPDATA%\ORCAdesk\session.json`** on every mutation
  (`QueueStore._bump_and_save`) and is restored on startup (`load_session`). A
  `RUNNING` calc persists its detached ORCA's `(pid, create_time)` **and its
  output path** (recorded at launch — without it the finished-while-closed →
  DONE judgment below has nothing to read), and its run folder (`run_dir`,
  recorded at the RUNNING stamp: the reattach tails that folder, never the
  one its system's name gives now; ORCA and CREST alike). On the next
  launch, `reconcile_calcs` checks that identity: still alive → stays `RUNNING`
  and is **reattached** (`OrcaRunner.adopt` + `monitor`, continuing the queue).
  The monitor tails from the **current EOF** — replaying hours of output
  through the capped buffer would evict the graph — so the UI rebuilds what
  that tail missed from the file instead: each cell's chart (SCF/geo curve
  **and** the freq/TD-DFT/CREST step chains) is replayed once from
  `get_graph_lines` (`seed()` in `boot.js`, for every calc with output on disk
  not yet seeded), and its Output pane reads the `.out`'s own tail through
  `get_output_tail` (see the Output-pane note), so it never depends on the
  log ring.
  The resume is a **third run entry point** and applies the Run button's two
  pre-run screens as far as it can: it passes the overwrite-conflict names as
  the keep-existing set (nobody is at the keyboard to answer the modal, so
  existing results are preserved rather than recomputed over, and the log says
  which; a `rerun_calc` row is never among them, as its overwrite was already
  confirmed), and reports a dangling reference — which it cannot refuse, having a
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
  structure automatically. `ref_point` picks another geometry of the
  reference: `irc_f` / `irc_b` read the `{ref}_IRC_F.xyz` / `{ref}_IRC_B.xyz`
  ORCA writes beside an IRC's `.out` (the IRC's `output_path` folder, else
  its recorded run folder — in its own system's folder, whatever the
  dependent's); a missing file is an `OrcaBlocked` saying which end
  point and which file — the IRC finished, it just did not run that way — so
  the row is BLOCKED (editable), not FAILED. A `ref_point` on a parent whose
  kind writes no IRC ends is refused when it is added (Queue store above).
  `conf:<k>` reads conformer k of a CREST search: `conformers[k-1]` of its
  parsed result (`crest_conformers.xyz`, energy-sorted; 1 = the lowest, the
  numbering of `Conformer.index` and of the exported `{name}_c<k>.xyz`), read
  on demand from its folder when a restored session holds no result
  (`_conformer_geometry`). k past the end of the ensemble is no fault — CREST
  found fewer conformers than the stack has members — so the row is
  **FILTERED** (`Filtered: CREST found only N conformers`), never FAILED or
  BLOCKED; the engine decides it at pick time from the parsed ensemble (below),
  and `_resolve_geometry` raises `OrcaFiltered` as the backstop.
- **The energy condition on an edge (`filter_de` / `filter_top`) and FILTERED.**
  A calc *has a filter* when `filter_de` is not None or `filter_top` > 0. Its
  **group** is its stack: the calcs with the same `system` and the same
  non-empty `node` that have a filter too (a calc with node `""` is a group
  of one). Each member is **scored** by conformer k's energy (Eh) when its
  `ref_point` is `conf:<k>`, else by the `final_energy_eh` of its parent (its
  `after`, else its geometry reference). `_judge_filter` runs in
  `_pick_ready` once the calc's own dependencies are settled and **before its
  approval gate** (a row the condition screens out is FILTERED, never left
  waiting for an approval):
  **barrier** — while any member's parent may still change state in this run
  (`_settled`: RUNNING, picked, or PENDING/CANCELLED not yet handled) the calc
  is not ready (PENDING, unseen) and keeps the run alive like any unmet
  dependency; a member whose parent waits at an approval gate holds the whole
  stack the same way (`_held_at_gates`), so the run ends with them PENDING,
  never BLOCKED. Members whose parent is not DONE are left out of the ranking
  and follow the normal rules (a FAILED parent → BLOCKED, a FILTERED one →
  FILTERED). The rest are ranked by score (ascending; ties by conformer
  number, then queue order, never by name as text; rank 0-based); the calc **passes** iff (`filter_top` == 0 or rank <
  `filter_top`) and (`filter_de` is None or (score − reference) ×
  627.5094740631 ≤ `filter_de` + 1e-9), the **reference** being conformer 1 of
  the CREST for a `conf:<k>` member (so a lone `conf:5` is judged against the
  ensemble's minimum, not itself) and the lowest score of the group otherwise
  (a group is expected to be one or the other). Failing it stamps FILTERED
  with `Filtered: ΔE 4.21 kcal/mol > 3.00` (checked first) or `Filtered: rank
  7 > top 5`. A DONE parent with **no energy to rank by** (no final energy,
  an output that cannot be read, a CREST result without an ensemble) leaves
  its member out and stamps it **BLOCKED** with the reason — editable: drop
  the condition or fix the parent; re-judged next Run. Scores come from
  results in memory only (the judgment runs under the store lock, P37): a DONE
  parent restored without one is asked for (`_to_load`), the dispatcher reads
  it outside the lock (`_load_results`, once per run per parent; the write is
  safe through the DONE-frozen invariant, as for parse-on-miss) and scans
  again. Only a row that has **not started** is judged: a RUNNING row (a
  detached job to reattach after a restart) is never filtered, blocked or
  held by the barrier, and a **rerun** row (`rerun_calc`, asked for
  explicitly) is not screened out again.
  again. The verdict reached at pick time is handed to `_precheck`
  (`_verdicts`), which stamps it. **FILTERED** is terminal: it never ran, has
  no output and no run folder, is never re-run (`_handled_without_running`),
  never keeps a run alive, is not in `EDITABLE_STATES` (only a change of its
  condition reopens it, Queue store above), is skipped by
  `_block_unreachable`, is restored from the session as it is, and counts as
  settled for removal and the pre-run screens (`SETTLED_STATES`). Its
  dependents (by `after` or reference) are stamped FILTERED in turn
  (`_filtered_by`: `Filtered: parent X was filtered`) — never BLOCKED, never
  FAILED; a FILTERED parent ends `_blocked_by`'s walk like a DONE one. Each
  filtered calc logs one line (`[name] filtered: …`).
- **`after` is a dependency like a reference, and its edge may be gated.** The
  queue editor's edge (`Calculation.after`) is independent of the geometry, but
  the engine treats it exactly like a reference: `_deps_ready` waits for it,
  `_blocked_by` walks **both** chains (`_parent_names`: reference and `after`;
  a FAILED/BLOCKED ancestor on either blocks, a DONE one ends that branch),
  and a dangling or self `after` is refused by the pre-run screen
  (`find_dangling_reference`), by a mid-run add/rename/remove at the store, and
  — when it still reaches the engine — BLOCKED with a message by `_precheck`,
  never FAILED. **Approval gate**: a row whose `after` is DONE, `gate ==
  "approval"`, not `approved`, and not auto-passed is **waiting**
  (`gate_waiting`, the one rule for the engine and the snapshot's `waiting`):
  it is not dependency-ready, so it stays PENDING and unseen; rows downstream of
  it wait behind it. When nothing is in flight and only waiting rows (and their
  descendants) remain, `_block_unreachable` leaves them **untouched** —
  `_held_at_gates` — logs "N calculation(s) waiting for approval", and the run
  ends normally: never BLOCKED, never a hang. Approving mid-run (an
  approval-only `update_calc` patch, which keeps the live row object so a
  keep-existing answer pinned to it still holds) is picked up by the live
  walk (P29).
  **Auto-pass**: with `auto_pass` on, a gate after a frequency kind
  (`freq`, `ts_freq`, `opt_freq`, `ts_opt_freq`, `mlip_freq`, `mlip_opt_freq`)
  passes by itself when the parent's imaginary-mode count is the expected one
  (1 for `ts_freq` / `ts_opt_freq`, else 0), read from the parent's result in
  memory. Nothing is parsed for it — the engine asks under the store lock
  (P37), and parsing a large frequency output there stalled every UI snapshot
  for ~1 s. With no result in memory (a restored parent) both the engine and
  the snapshot trust what DONE already says — a frequency calc reaches DONE
  only through `validate_result`, whose rule for every one of those kinds IS
  the expected count, so the two cannot disagree. (The same fact
  means a frequency parent with the wrong count is FAILED, and its gated child
  BLOCKED, before any gate is asked.)
- **Failure propagation is dependency-scoped, not whole-queue.** If a calc fails,
  every calc that references it or runs `after` it (transitively) is marked `BLOCKED` and skipped;
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
  payloads fail loudly too). The notebook builder fills `irc_hess_file` from a
  **Load .hess…** picker (`pick_hess_file`) instead of a typed name, and the
  load itself switches Initial Hessian to `read`. What it stores is the picked
  file's absolute path: `irc_hess_input_name` writes only the file name into
  `Hess_Filename` (ORCA runs in the run folder and truncates arguments at a
  space), and `_stage_irc_hessian` copies the file in before every launch.
  A plain name still means "in the run folder / the referenced calc's
  folder", and a raw input's own `Hess_Filename` is never rewritten.
- **Result validation is per-kind**: `opt`/`ts_opt` (and the combined
  `opt_freq`/`ts_opt_freq`) must converge; every freq kind must have PRODUCED
  frequencies at all (the imaginary-mode rule is vacuously true of an empty
  table, so a run that never computed a Hessian used to pass — the one
  exception is a single atom, whose 3 translational modes print as 0.00 and are
  dropped while its thermochemistry is still there, `_has_vibrational_analysis`);
  `freq`/`opt_freq` must have zero imaginary frequencies; `ts_freq`/`ts_opt_freq` must have exactly one;
  `neb_ts` must have exactly one when frequencies were computed. A
  validation failure marks the calc `FAILED`. An `opt` carrying a
  `geom_scan` (`_wants_scan`, the rule `build_input` writes it by) must also
  have produced `scan_points`. Each point is an optimization, and an
  unconverged one error-terminates the run, which the termination check
  catches, so the normal end plus the table is the whole bar. The
  keep-existing match tells a scan and a plain optimization apart in both
  directions: a scan's geometry is its last point, held there, not a minimum.
  A REFERENCE to a scan calc therefore receives that last point; nothing
  refuses it. Calc kinds: `opt`, `ts_opt`,
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

## Structure screening and editing (builder)

`orcamgr/core/structure.py` is the one place geometry is reasoned about. It is
pure and Qt-free (it needs no numpy, though `orcamgr/nbo/` has since made numpy
a core dependency — see P5), so it unit-tests directly (`tests/test_structure.py`); the front-end reaches it only
through thin Bridge slots — `check_structure`, `compare_structures`,
`apply_constraint_target`, `apply_constraint_targets` and `displace_mode` — and
holds **no**
geometry logic of its own. That is deliberate (P4): the builder's NEB endpoint verdict
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

**Everything in this module READS, with one narrow exception.** There was a
structure editor here (rigid bond/angle/dihedral edits, fragment moves); it was
removed, along with its three Bridge slots, because building and modifying
molecules is a molecular editor's job — Avogadro2 and its peers do it properly,
and a side feature here could only be a worse one. The exception is
`apply_constraint_target`, the notebook builder's **Apply** on a constraint
row: a constraint with a target names a geometry the run will be driven to,
and showing those coordinates before the run is part of telling you what you
are about to run. It is one rigid motion (`set_internal`) of the **smaller**
side of the hinge bond — so a hydroxyl turned to 120° takes its H with it, and
the drag direction does not matter — refused for a ring bond, with no free
moves, no force field and no relaxation. Only the moved atoms' three numbers
are rewritten in the user's text (≥ 6 decimals; 5 rounded a 100° target to
99.9998°), and the builder inserts the result with `insertText` so Ctrl+Z in
the box undoes it. **Apply all** (`apply_constraint_targets`, slot of the
same name) exists because targets sharing atoms undo each other one Apply at a
time — three angles about one carbon at 120° never read 120/120/120. It takes
the same turns (`_hinge` once per constraint, then `_turn` in place) in rounds
until all hold to `_ALL_TOL` (0.01° / 0.0001 Å), and refuses a set that
cannot hold together after `_ALL_ROUNDS`. A planar centre is the slow case:
the targets only just fit, so it takes about 2500 rounds (0.03 s on 16
atoms). The hinge is found on the bonds the builder's stage **draws** (the
slots' third argument, `bonds_json`; `_bond_list_of`), not on the Applied
coordinates' distances: an Apply that brings two atoms close made the distance
rule see a bond and a ring, and every later Apply on that centre was refused as
"in a ring". A straight angle (180°) Applied to its own value is a no-op, and
bent elsewhere turns in the plane of the nearest atom off the line
(`_bend_plane`). `measure`'s dihedral is **IUPAC-signed**, matching ORCA and
`MOL.dihedral`: the editor it was revived from had the sign reversed, and an
Apply to +90 read back as −90 (pinned by
`test_the_dihedral_sign_is_iupacs_like_orcas_and_the_stages`). If a future
change needs to write a structure in any other way, weigh that first.

The **second** write is `displace_along_mode`, the builder's *push along a
mode*: **Load .hess…** beside *Load .xyz…* reads a frequency run's `.hess`
(`core/hessfile.py`, Qt-free: `$atoms` in Bohr, `$vibrational_frequencies`,
`$normal_modes` - a 3N x 3N table printed in blocks of five COLUMNS, one per
mode; ORCA's Cartesian displacement vectors, normalized), and a panel under
the stage picks a mode (every vibration; the first imaginary preselected), a
direction and a distance. The distance is how far the **most-moved atom**
goes, in Å (up to `MAX_MODE_SHIFT`, 2 Å), because a normalized vector's own
scale is not a number anyone pictures. The stage previews it; *Apply* writes
it through the same `_rewrite` the constraint Apply uses (only the moved
lines' numbers, Ctrl+Z undoes it) and the distance goes back to 0, so a
second push starts from the pushed box rather than pushing twice. An empty box
takes the `.hess`'s own geometry; a box with other atoms or another order is
refused, since a mode is a list of per-atom arrows. A small imaginary mode
(> −50 cm⁻¹) gets a note that it is often noise a tighter optimization or a
finer grid removes. Bridge slots `read_hess_modes(path)` /
`displace_mode(xyz, path, index, amplitude)` (the parsed `.hess` cached by
path + mtime, since the slider asks on every move); `tests/test_hessfile.py`
on a real ORCA 6.1.1 water `.hess` (`tests/hess/fixtures/`).

One implementation note worth keeping: **bond perception is a uniform grid, not
an all-pairs sweep.** `check_structure` runs on the Qt UI thread on every charge
keystroke and every builder entry; with the cell edge set to the longest
possible bond, every partner is inside the 27 cells around an atom (~20 ms for
3000 atoms, vs. a visible freeze). It is distance-based only — no bond orders —
and feeds only the fragment count and the screening findings. **ORCA never sees
it.**

Front-end side: `web/next/build.js` owns the builder's structure check
(`check_structure`, findings as `.finding` rows) and the NEB endpoint comparison
(*Compare endpoints*, over `compare_structures`).

## Optional phone-sync server

`orcamgr/server/` is a thin FastAPI layer (`app.py`) over `QueueStore`, started/stopped
from the desktop by `ServerController` (`controller.py`) running uvicorn in a daemon
thread on the shared store. It serves the mobile PWA from `web_mobile/` at `/` and the
queue API under `/api/`. fastapi/uvicorn are **optional** — `ServerController.is_available()`
gates the whole feature, and the desktop app works fine without them.

**The PIN exemption reads the socket peer, and only when that peer proves
something (P35).** `create_app(bind_host=..., proxied=...)` computes
`loopback_bind = bind_host in _LOCAL_HOSTS and not proxied`, because a bind
stops being evidence about the client in *two* ways: a LAN bind (a same-host
proxy would make every request look like 127.0.0.1) and a declared reverse
proxy or tunnel in front of a loopback bind — nginx/cloudflared forward from
127.0.0.1, so without the flag every relayed request takes the exemption and
the PIN is off for whoever can reach the proxy. Either way the answer is to
drop the exemption, **never** to move the decision onto `X-Forwarded-For`:
that header's authority depends on how the proxy merges a client-supplied one
(nginx's `$proxy_add_x_forwarded_for` *appends*) and on which element the ASGI
layer reads. `ServerController(..., proxied=, trusted_proxies=)` is the one
flag both halves come from — it sets `create_app`'s `proxied` **and**
uvicorn's `proxy_headers`, which is default-ON and REPLACES
`request.client.host` with a header element. Off unless a proxy was declared
(the exemption reads that address); on when one was (the exemption is already
gone, so the header informs and decides nothing). `forwarded_allow_ips` is
passed explicitly so it never comes from the `FORWARDED_ALLOW_IPS` environment
variable. `run.py` exposes the pair as `ORCADESK_PROXIED` /
`ORCADESK_TRUSTED_PROXIES`. Per `CHANGELOG.md`
phone-sync is in development and **not part of the packaged build** — enforced by
`build.spec`'s `excludes` (fastapi/uvicorn/starlette/pydantic/anyio/qrcode/PIL),
which must be dropped deliberately when phone-sync ships.

## MLIP environment (deliberately separate from ORCA)

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

**Builder modes.** The builder's backend control (`DFT | MLIP | CREST`, then
`Guided | .inp text` for DFT) is described under the front-end section above.
`Settings.build_mode` (`beginner`/`expert`/`mlip`/`crest`) is still stored and
round-tripped by `get_settings` / `save_settings`, but no page reads it since
the classic Build tab went. **The MLIP and CREST buttons are hidden until the
user has actually set that backend up** (the top-bar pills always show, B32)
(`backendState` in `build.js`). Adoption is read from what the user did, never from the probe's
verdict: MLIP from `MlipStatusPayload.envs.length` (known from settings at the
first poll, so the button never pops in after torch imports) and CREST from
`state === "ready"` or an explicit `Settings.crest_distro` — CREST's aggregate
`"error"` means "WSL present, no distro has CREST", i.e. the ordinary
never-installed case, so it hides. A **registered-but-broken** setup stays
visible and locked with its reason, and a backend stays visible while the queue
holds one of its calcs or while the form is on it — otherwise an `mlip_*` /
`crest_*` row would become uneditable. *+ More backends…* shows whenever one is
hidden and opens Settings, whose MLIP and CREST sections are unconditional.
This amends DESIGN.md D41 (see its scope clause) — hide for "never adopted",
lock for "adopted but not ready". **The Guided → `.inp text` linkage is
one-way by design**: Guided → `.inp text` converts the current form to the
generated `.inp` via `build_inp_preview` (no coordinates yet → a
`{{GEOMETRY}}` placeholder, filled from the box at run time); raw text can
never be converted back, so `.inp text` → Guided confirms discarding it. A
failed conversion never switches (the filled form stays). The edit target is
tracked **by name** (`update_calc(name, …)`), never by queue index — the
queue can shift under an open edit (remove/reorder, phone adds). The builder
keeps a raw calc's coordinates and its text in step (DESIGN.md §17.4t): the coordinates box
stays usable in `.inp text` mode, and while the text carries its own
`* xyz` block the box IS that block (`rawCoords` in `build.js` finds it; an
edit on either side writes the other) — it used to be saved and never read,
and a copied OptTS ran its own TS guess with IRC endpoints pasted beside it.
`input_generator.raw_geometry_mismatch` (over `raw_coordinate_block`, moved
there from `queue.py`) is the backend's half: a raw DIRECT calc whose stored
`xyz` differs from its text's block is warned about on add/edit
(`store.raw_geometry_warning`, joined into `MutationResult.warning` beside
the constraint warning) and in the log before launch. Never refused — the
text is what the user wrote (P26).

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
guessed: `has_nvidia_gpu()` only *defaults* the choice and warns. Unlike the
CREST installer, these are pip installs at the current versions pip resolves
(PyPI over HTTPS, torch from `download.pytorch.org`) — not pinned or hashed by
ORCAdesk. The Settings card names what *Create* downloads and under which
licences: PyTorch (BSD-3-Clause; a GPU build also brings NVIDIA's CUDA
libraries under NVIDIA's terms), mace-torch / sevenn (both MIT), their
dependencies (ase is LGPL-2.1-or-later). Bridge slots
`get_mlip_install_options` / `get_mlip_install_status` / `create_mlip_env` /
`cancel_mlip_install` follow the probe pattern (background thread + UI polling,
guarded by `_mlip_install_lock`); on success the env is appended to
`Settings.mlip_envs` and probed through the existing `_start_mlip_probe`. Envs
are created under `user_data_root()/mlip_envs/<slug>` (P18 — never the resource
root), the slug sanitized because the name becomes a real directory. Wire
shapes: `MlipInstallOptionsPayload` / `MlipInstallPayload` / `BasePythonPayload`.

**Building MLIP jobs.** On MLIP the builder draws none of the ORCA form, only
the backend's own block (`backendForm` in `build.js`, see the front-end
section): the shared name / charge / multiplicity row (charge and multiplicity
used only by charge/spin-aware models — OMol25 / multi-head; MACE-OFF/MP
ignore them), a MACE-model dropdown (options in `data/mace_models.json`, served
via `load_choices`) **labelled with each model's licence**, a **CPU threads** field (`StepConfig.nprocs`, seeded from
`Settings.default_nprocs`) that both caps the worker and is what the core
budget charges the job, and the shared **geometry source** (coordinates **or**
a reference to another queued calc). It adds a calc of the kind chosen by its
Task selector (`mlip_opt` / `mlip_sp` / `mlip_freq` / `mlip_opt_freq`) to the
**shared queue** through the same `add_calc`/`calc_from_dict` path as ORCA
calcs. The frequency tasks add temperature/pressure (`StepConfig.freq_temp_k` /
`freq_pressure_atm`, reused from the ORCA freq fields) for the ideal-gas
thermochemistry. A **Device** selector (`StepConfig.mlip_device`: `""` auto /
`cpu` / `cuda`) picks the torch device; the GPU option is offered only when a
ready env's probe reports CUDA. Because the threads field means **different
things per device** — always the worker's cap, but the queue's core charge
only off the GPU (`declared_cores` returns 1 for an explicit `cuda`, which
takes the GPU lane instead) — the field carries a per-device note
(`threadsNote` in `build.js`; D2: unlabelled, "CPU threads: 6" beside "Device:
GPU" reads as either a no-op or six reserved cores, and is neither). A
referenced `mlip_opt`/`mlip_opt_freq` resolves through the very same
`_resolve_geometry` path as an opt→freq handoff, so an MLIP pre-optimization
can start from another calc's optimized geometry (e.g. a CREST best
conformer). The model lives on
`StepConfig.mlip_model` (+ `mlip_env_id`, `""` = first ready env — the engine
takes `mlip_envs[0]`, and the Bridge orders that list ready-first via
`_mlip_envs_for_run` / `order_envs_by_readiness`, since readiness is a live
probe result the engine never sees; `mlip_device`);
`build_input` ignores those — an MLIP calc never produces an ORCA `.inp`. `_meta_line` shows
the model instead of charge/mult for `mlip*` kinds, and `CalcSummary` also carries
the model/method as **explicit fields** (`mlip_model`, `crest_method`); the
rail renders the pre-joined `meta` escaped (`NB.esc`), never as markup — it
embeds user-typed ref names. MLIP/CREST calcs are **editable in place**: a
row's *edit* opens the builder on that backend's own form and saves through
`update_calc`, which is kind-agnostic at the store (`replace`, gated by
`EDITABLE_STATES` and the mid-run protections), so no backend change was
needed. The form is **locked** until some MLIP env is ready (`backendState`,
from the `get_mlip_status` poll), and the save handler refuses on its own
(P44).

**Each model's weights carry their own licence, and the picker says it.**
ORCAdesk never ships or downloads weights itself: the worker calls
`mace_off` / `mace_mp` / `mace_omol`, and mace-torch fetches the file on first
use into its cache (`~/.cache/mace`) from the authors' GitHub —
`raw.githubusercontent.com/ACEsuit/mace-off` for MACE-OFF23,
`github.com/ACEsuit/mace-mp/releases` for MACE-MP-0, and
`github.com/ACEsuit/mace-foundations/releases` for MACE-OMOL-0 and MACE-MH-1
(the URLs in mace-torch 0.3.16's `foundations_models.py`). The licences differ
and some forbid commercial use, the default MACE-OFF medium among them, so
`data/mace_licenses.json` groups every offered model by its weights' licence —
**ASL** (Academic Software License, github.com/gabor1/ASL: academic,
non-commercial use only): MACE-OFF small/medium/large, MACE-OMOL extra-large,
MACE-MH-1 (and its omol head); **MIT**: MACE-MP-0 small/medium/large. Each
entry was checked against its primary source, recorded in the file's
`_source`: the mace-off `LICENSE.md` and README, the mace-mp `LICENSE`, the
model table in the mace-foundations README, the MACE-MH-1 model card on
Hugging Face, and mace-torch's own docstrings. (The mace-foundations
*repository* `LICENSE` is MIT; that covers its scripts, while its README table
gives OMOL-0 and MH-0/1 as ASL.) The file goes through `load_choices` like any
option list, so neither the bridge nor the store changed; `build.js` loads it,
words each group (`MODEL_LICENSES`: label + where the full terms are), suffixes
every option with its group (`MACE-OFF medium · ASL`) and keeps a line under
the picker (`data-x="licnote"`, `licenseNote`) on the selected model's licence;
a model typed in that no group lists says the licence is not recorded. The
queue editor's MLIP-set editor shows the same line for the set's `model` line
(the default's, when there is none) through `NB.build.licenseNote`.
`tests/test_mlip.py` pins that every offered model is listed exactly once and
the verified grouping; `tests/test_next_ui.py` that every group has wording.
The weights' licence is not repeated in the run: mace-torch itself prints its
ASL notice into the calculation's `.out`.

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
`total_thermal_eh`, rendered in the report like an ORCA freq job), so a
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
validation runs were performed locally; the unit suite covers the pipeline
with a stub worker, and `tests/smoke` runs it against a real MACE env.

## CREST environment (a WSL-backed third backend)

`orcamgr/crest/` is a dedicated package, kept **out of** the ORCA pipeline in
`core/` like `mlip/`. CREST is a conformer-sampling tool with **no native
Windows build**, so on Windows ORCAdesk runs its statically-linked Linux binary
through **WSL** — it never installs a chemistry toolchain, it shells out (there,
into a distro). v1 scope is conformer search only (`crest_conf`).

**Where it shells out to is one decision in `shell.py`, and it is detected, not
configured.** A *target* is a WSL distro on Windows and the single pseudo-target
`"local"` (`shell.LOCAL_TARGET`) elsewhere, where the same binary runs directly.
There is deliberately no Windows/Linux setting: no machine is both, `sys.platform`
already knows, and such a toggle could only ever be set wrong — what the user
picks is *which* target (`Settings.crest_distro` / `StepConfig.crest_env_id`,
unchanged), and locally there is exactly one, so the Settings picker is hidden
rather than shown with a single entry. `shell.py` is the whole of the difference:
`run_bash(target, ...)` (via `wsl.exe -d` or plain `bash -c`), `shell_path_expr`
(a bash *expression* — `"$(wslpath -u '...')"` vs the path itself — so the
generated script and the launch command cannot disagree), and `uses_scratch`.
`env.py`/`installer.py`/`runner.py` are written against it, once.

The package: `shell.py` (the transport, above), `wsl.py` (low-level `wsl.exe`
helpers — distro enumeration with infrastructure distros like `docker-desktop`
filtered out, `WSL_UTF8=1`, always `-d <distro>` explicitly — now the Windows
half of `shell.py`), `env.py` (probe each target for the `crest` binary, backing
the "CREST ready" top-bar indicator; `aggregate_status` mirrors the MLIP
four-state pill and carries `transport` so the UI can word "no WSL distribution"
and "no bash" as the different problems they are), `installer.py` (download the
static release tarball onto a target + symlink — fully scriptable, so ORCAdesk
auto-installs CREST; on Windows the one manual prerequisite is a WSL distro
existing, and on macOS it refuses up front rather than fetching a Linux binary),
plus the run pipeline (`runner.py`, `parser.py`).

**The installer fetches one pinned release and checks it before unpacking.**
`CREST_VERSION` / `CREST_ASSET` name the release (`v3.0.2`,
`crest-gnu-12-ubuntu-latest.tar.xz` — the GNU/OpenBLAS static build), and
`CREST_RELEASE_URL` is built from the tag; `/releases/latest/` is never used,
because it moves under the user (it resolved to v3.0.2 when this was pinned;
the `latest` *tag* is a rolling prerelease). `CREST_SHA256` is that asset's
SHA-256. v3.0.2 predates GitHub's published asset digests, so it was computed:
the asset downloaded twice (tag URL and `/releases/latest/` redirect, both
8,764,012 bytes) and hashed with `sha256sum` and `certutil`, all agreeing.
`install_script()` renders the bash the transport runs: pre-flight
(`tar`/`xz`, `curl`/`wget`, and a hash tool — `sha256sum`, else `shasum`, else
`openssl`; none is exit 96, not an unchecked install), download into a
`mktemp -d` that the `EXIT` trap deletes, compare, and only then remove the old
`~/.local/opt/crest` and extract. A mismatch is exit 97 with an
`ORCAdesk-ERR:` sentence naming both hashes and saying the file was deleted
unpacked and unrun and nothing was changed — it reaches the Settings card
through `install_error` like every other installer diagnostic, and an existing
CREST survives it (the removal comes after the check). `tests/test_crest_installer.py`
pins the order, the pin and the message, fakes the transport for both
platforms, and runs the script for real (Linux bash, or Git for Windows' bash —
never `System32\bash.exe`, which is WSL) against a local archive with a wrong
and then the right hash. **Bumping the release**: change `CREST_VERSION`,
download the new asset, take its SHA-256 (from the release's own digest where
GitHub shows one, otherwise compute it as above) into `CREST_SHA256`, and
update the version named in `README.md` / `README.ko.md`. What is installed is
CREST, LGPL-3.0-or-later (`LICENSE` + `LICENSE.LESSER` in the archive), whose
binary includes the xTB methods through tblite (LGPL-3.0-or-later); the
separate `xtb` program is not installed. Settings → CREST says so under the
Install button, with where it comes from.

**Running CREST jobs** mirrors the *ORCA* runner (not the MLIP one) because a
CREST run is long and must survive ORCAdesk closing. `QueueEngine._run_crest_calc`
resolves the target (`config.crest_env_id`, else `settings.crest_distro`, else
the first target with CREST — persisted back onto `config.crest_env_id` so a
reattach knows where the job runs) and writes a self-contained `run_crest.sh`
into the workspace folder. `CrestRunner.launch` starts it **detached**
(`setsid`): the script records its own pid + start-time, redirects CREST's stdout
to `<name>.out` for live tailing, and writes a `<name>.crest.rc` marker last —
after the ensemble is in place, because that marker is what `monitor` stops on
and what a finished-while-closed run is judged from.

**The generated script has two shapes, and the difference is the scratch dir.**
Under WSL it runs CREST in an **ext4 scratch dir** (`~/.orcadesk/scratch/<name>`,
never `/mnt` — 9P is 5–300× slower and CREST is I/O-heavy) and copies the
ensemble back to the Windows folder. Locally there is no such mount, so the run
happens in the calc folder itself: nothing to copy, and `--keepdir` output stays
where the user can see it. The `rm -rf "$SCRATCH"` cleanup is **not generated at
all** in the local shape — there, `SCRATCH` would be the user's calc folder,
results and all — and `CrestRunner._kill`'s scratch cleanup is guarded on
`shell.uses_scratch()` for the same reason. `tests/test_crest_transport.py`
renders both shapes from either platform and pins that absence. The launcher **waits (inside WSL) until the `.pid`
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
multiplicity). The builder's CREST form takes the shared **geometry source**
(coordinates **or** a reference to another queued calc); a referenced
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
validated and clamped in `StepConfig.from_dict` (the trust boundary). Standalone
modes (`--cregen`) are intentionally out of scope. UI: the builder's CREST form
+ a closed `<details class="advsec">` for the advanced knobs.

**Constraints reach CREST as a `--cinp` file.** A `crest_conf` takes the same
`StepConfig.geom_constraints` list the ORCA form writes (kinds C/B/A/D, atoms
0-based, `value` None = hold the start; `normalize_constraints` is the trust
boundary for both), picked on the builder's stage (DESIGN.md §17.4s). When it
holds something CREST can use (`has_crest_constraints`: a bond/angle/dihedral,
or ≥2 fixed atoms) `write_crest_run_files` writes `constraints.inp` beside the
input `.xyz` and `build_crest_argv` adds `--cinp constraints.inp` — relative,
because CREST's cwd is the folder holding both (under WSL the script copies it
into the ext4 scratch dir with the input). Without constraints a previous
attempt's `constraints.inp` is removed, so a re-run starts from what the calc
says now. The file (`crest_constraints_text`) is the xtb Detailed-Input syntax
CREST documents for `--cinp`, 1-based, Å / degrees, `auto` for no target:

```
$constrain
  distance: 1, 3, auto
  angle: 1, 2, 3, 109.5
  dihedral: 1, 2, 3, 9, 180
  atoms: 1,2,3
$metadyn
  atoms: 4,5,6,7,8,9
$end
```

What the pinned **CREST 3.0.2 accepts was measured** (WSL Ubuntu, ethanol,
`--squick`), and it is narrower than the docs: a `force constant=` line (any
value, any spacing) kills the parser (`parse_xtbinput.f90`: "Error allocating
… bytes", or a segfault), so none is written and CREST's 0.05 Eh/Bohr² applies;
`atoms:` takes an explicit comma list only — a range (`1-3`) or a single atom
dies the same way. `atoms:` holds the atoms at their pairwise **distances**
(the run log lists them as distance constraints), not at fixed positions, so
one fixed atom holds nothing: `calc_from_dict` refuses a crest calc with
exactly one C constraint, and the file never writes one. Beside a fixed list
the file restricts the metadynamics bias to the other atoms (`$metadyn atoms:`
— what CREST's own `--constrain` generates), which is also the subset
`--subrmsd` compares. `auto` works for distance, angle and dihedral;
`reference=` is not needed. A constrained run (dihedral held at 180°) ended
normally with every conformer at that dihedral. Atom indices are checked like
ORCA's: against coordinates in hand when the calc is added (`calc_from_dict`),
against a referenced geometry in `_run_crest_calc` before CREST starts. A
CREST *set* has no constraint line, so a set put on a calc clears them. The
queue editor's CREST settings view lists them as `constrain B 12 27 auto`.

**Conformer → follow-up pipeline.** Follow-up MLIP/ORCA calculations are built
in the **builder** by referencing the CREST calc through the normal
geometry source; a reference receives the **lowest-energy
conformer** (the classic single-geometry handoff via `_resolve_geometry` —
`parse_crest_result` sets `geometry`/`final_energy_eh` to the best conformer)
— except with `ref_point` `conf:<k>`, which reads conformer k (Queue
semantics). The queue editor pre-allocates a stack of K such single points
(`conf:1` … `conf:K`) and may put an energy condition on the edge into it;
members past the end of the ensemble, or outside the condition, end
FILTERED. The engine never adds or rewrites rows for this.
The per-conformer track fan-out (`crest_handoff="all"`, `expand_conformer_tracks`
/ `substitute_calcs`) was removed in 0.6.0-beta. The report's conformer
list is **read-only** for building: results are for interpretation; building
happens in the builder.

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
and never turns a completed search into a FAILED one. The slot
`bridge.export_conformers` (→ `crest/export.py`, the same `export_conformers`
split) is a manual re-run of the automatic export; its button went with the
classic front-end and no page calls it now. No file dialog — the files land
next to the run; it only reads/writes, never touches the queue.

**In-app 3D structure viewer.** Every structure stage, the pop-out viewer
included (`web/next/mvwin.js`, `NB.mv.open`; `open({file})` shows that one
`.xyz` without looking for other sets beside it — *Open file…* with an `.xyz`
opens it so), is drawn by `mol.js`; the pop-out
walks a list of **frames** with the ←/→ keys (Esc closes). A frame is `{label,
xyz, energy}`; `xyz` is raw `.xyz` text, and the caption shows ΔE vs the
lowest-energy frame of the set when energies are present. Every entry point is
a Visual-panel entry (see the Visual note above): the parsed structure, or one
of the `.xyz` sets discovered beside the result, opened through
`bridge.get_structure_frames(path)` — one slot for a file or a folder. Frames
are read on demand (never in the result payload, so a 100-conformer render
stays light) by the Qt-free `orcamgr/molview.py` (`frames_from_file` /
`frames_from_folder` / `discover_structure_sets`, unit-tested). **3Dmol.js**
(vendored at `web/vendor/3Dmol-min.js` — bundled locally, no CDN; this is why
WebGL is enabled in `window.py`) draws only the surfaces below, and is loaded
on first use.

**Viewer favorites (starred structures).** In the viewer the user stars
structures worth following up (the **F** key or the list star); stars persist
across sessions via the Qt-free `orcamgr/favorites.py` (a small
`favorites.json` in `user_data_root`, **not** `settings.json` — which is
rewritten on every queue mutation), keyed by a namespaced *source*:
`"calc:<name>"` for a queued calculation's own CREST ensemble,
`"folder:<path>"` for any other structure set, so two sources never share a star
set. That is why a queued ensemble is opened as kind `"calc"` even though its
frames now come from the generic `get_structure_frames`: stars set before then
still resolve. *Favourites only* steps the ←/→ keys through
starred frames alone; the export writes them to a `favorites/` subfolder next
to the source. Bridge slots: `get_favorites(source)`, `toggle_favorite(source,
label, on)`, `export_frames(dest_kind, dest, frames_json)` — `export_frames`
takes the frames' xyz straight from the front-end (favorites are few) and
sanitizes each label into a filename, so it needs no re-parse.
`get_structure_frames` returns a `folder` (the set's own folder, or a file's
parent) — the export/favorites destination.

**Orbital / electron-density surfaces (the viewer's second mode).** The pop-out
(`_mode` `"frames"` | `"volume"` in `mvwin.js`) and the Visual panel draw
volumetric data through the one shared 3Dmol renderer (`volumeView`, reached as
`NB.mv.vol` — one WebGL context, see the Visual-panel note above). The orbital
list is the one the front-end already parsed (`mountVisual(source, d)`) rather
than the payload carrying a second copy (P4).

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
file through verbatim (`load_cube` returns the `CubeDataResult` itself, so
`get_cube_data` serializes what it is handed rather than re-spelling the eight
fields — P4; `read_cube_header` stays a plain dict, being internal to the NBO
cube writer and the tests) — 3Dmol parses cube text itself, and ORCA writes the
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
  `cube_filename()` what we keep; `cubeName` in `web/next/mvwin.js` mirrors the
  latter so the list's *ready* marks track the selected grid. ESP is
  the exception to *what orca_plot writes*: it names the file after the DENSITY
  it was computed from, so the file is `{name}.scfp.esp.cube`, not
  `{name}.esp.cube` — get it wrong and every ESP looks like a failed plot,
  because the path the code looks for is never there.
- **The NBO program's orbitals are a fifth orca_plot kind, `pnbo`.** A
  `! NBO` run leaves `{base}.nbo`, a .gbw-format file whose orbital k-1 is
  NBO k of the program's summary (measured on ORCA 6.1.1 + NBO 7.0.10: the
  orbital energies are the summary's to the last digit, and NBO 11 =
  BD(1) C1–C2 peaks between C1 and C2). orca_plot is handed that file
  (`wavefunction_file`) and driven through the MO menu; it writes the cube
  under the MO name, so it is kept as `{base}.pnbo{N}a.g60.cube`
  (`cube_filename`), and its currency is judged against the `.nbo`.
  `get_plot_options` offers it (`has_nbo`) for a closed shell only — an
  open-shell `.nbo` has not been measured. The list it is picked from is the
  parse's `nbo_program.orbitals` (the notebook's **NBOs** tile, `picksFor` in
  `mvwin.js`); ORCAdesk's own in-process `nbo` kind is unrelated and hidden.

**The ESP map is built from two cubes, and has its own entry point.** A
potential is not an isosurface of itself; the figure everyone means is the
potential *painted on* an electron-density surface, so the map is `eldens`
(shape) + `esp` (colour) at the same grid — 3Dmol's
`addIsosurface(dens, {voldata: esp, volscheme})` samples the second field per
surface vertex. `plot` in `mvwin.js` issues the two requests in order through
`fetchCube` (the single-cube generate/poll/fetch loop); both must ride the ESP
grid or they would be two different boxes.

It is one figure, not a set to step through, and it answers a different
question, so it is its own Visual tile (*ESP map*). Its cost is minutes, so a
cube not yet on disk is asked about before the click spends an orca_plot run
(`COST` in `mvwin.js`). Consequences worth knowing:
- The isovalue slider steers the **density** level here, opening at
  `cube.ESP_SURFACE_ISOVALUE` (0.002, the van-der-Waals-like contour): a total
  density's 0.002 contour is the same physical surface in every molecule. A
  second control sets the colour half-width (`ESP_DEFAULT_RANGE`, ±0.05 a.u.);
  both re-draw in place.
- **The grid is remembered per kind** (`_gridByKind` in `viewer.js`), because ESP costs
  minutes where an orbital costs a second: measured **49.3 s at 40³** on that
  same 52-atom system (~2.8 min at 60³, ~6.6 min at 80³), and 0.2 s on water at
  def2-SVP — it scales with the basis as well as the grid. `DEFAULT_GRIDS` in
  `plot.py` opens ESP at 40³ and `default_grid_for()` is the accessor.

Rendering separates **sampling** from **smoothness**: an MO is a sum of
Gaussians, so a coarse grid is missing *mesh*, not physics. 3Dmol's marching
cubes gets `smoothness: 8` (Laplacian mesh smoothing) and the isovalue slider
re-meshes the `VolumeData` already in memory — no backend round-trip — which is
why 60³ is the default and the grid selector is the rare escape hatch.
Per-kind display defaults live in `cube.py` (`DEFAULT_ISOVALUES`,
`SIGNED_KINDS`: mo/spindens draw a ± pair, an electron density one surface) —
and `DEFAULT_ISOVALUES` is a **starting point, not the answer**. 0.05 is the
convention for a *localized* orbital, which is not the same as for an orbital:
CB8's HOMO peaks at 0.0996, so at 0.05 only 222 of 216,000 points clear the
level and the surface is invisible. The classic viewer lowered it to the level
enclosing 90% of ψ² (0.0097 for CB8's HOMO, while water's HOMO kept 0.05);
that auto-fit went with the classic front-end — `mvwin.js` opens at
`CubeDataResult.isovalue` (this table) and the user steers it from there. A
cube already on disk opens with no wait.
Measured cost of what it covers: a density on a 144-atom / 3648-BF system is
22 s at 60³, against 0.34 s for one MO.

**Bridge slots**: `list_structure_sets(source)` / `get_structure_frames(path)`
(the Visual panel's discovery and its one opener),
`pick_result_file()` / `pick_result_folder()` (the two pickers: a file
routes by suffix, a folder resolves to the result inside it),
`get_plot_options(source)` (base, has_gbw, the kinds this
wavefunction supports — spin density only when the run is open-shell — grids,
and the cubes already on disk), `generate_cube(payload)` / `get_cube_status()` /
`get_cube_data()`. Generation is a background thread + UI polling like the MLIP
probe, guarded by `Bridge._cube_lock`; the ~3 MB cube rides `get_cube_data()`
**once**, never the poll. Only one orca_plot runs at a time (it writes a fixed
filename beside the `.gbw`, so two would race), and a refused request returns
the *in-flight* job's status — so the front-end checks the job against its pick
(`mine(job)` in `fetchCube`) before adopting a result, or it draws the previous orbital under this one's
label. Wire shapes: `PlotOptionsResult` / `CubeJobPayload` / `CubeDataResult`.

**Bridge slots** (`get_crest_status` / `check_crest` / `install_crest` /
`set_crest_distro` — "distro" is the wire name for a target on both platforms)
follow
the MLIP pattern: a background probe publishes to `Bridge._crest_status` (guarded
by `_crest_lock`) and the UI polls `get_crest_status`. `install_crest` runs off
the click too, so its outcome rides back on `CrestStatusPayload.install_error`
(`""` = none) — the installer's diagnostics would otherwise reach only the log,
and the Install button reads as dead. A plain re-probe publishes no
`install_error`, so **Re-check clears a stale one**: the field describes the last
install *attempt*, not the current state. The button itself disables whenever
there is nothing to install into (no WSL, no distro, or a probe in flight) —
creating a distro needs a Linux account and is the one step ORCAdesk cannot
script (D41). The builder's CREST button (see "Builder modes" — hidden until
CREST is set up), a locked CREST form (until a distro has CREST), and a
**Settings → CREST** distro picker + Install button (that section is
unconditional — it is the way in when the button is hidden). Wire
shapes: `CrestStatusPayload` / `CrestDistroPayload` / `ConformerPayload` in
`state/schemas.py`, mirrored in `web/types.js`.

## Text that Windows does not write in UTF-8

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
really is UTF-8. Picked `.inp` and `.xyz` files (`load_inp_file` / `load_xyz_file`,
and the path variants `load_inp_path` / `load_xyz_path`) are read `utf-8-sig`,
because Notepad/VS Code/PowerShell write a BOM freely and ORCA refuses an input
that starts with one.

## Handing a file to the user's own programs

`orcamgr/openwith.py` is a Qt-free two-verb module: `open_with_default(path)`
(the OS file association) and `show_in_folder(path)` (reveal it, selected).
Bridge slots `open_path_external` / `show_path_in_folder` are thin wrappers;
both return `OkResult` and log the refusal, because a missing association is a
normal thing for a user to have (P6). The notebook calls
`show_path_in_folder` (the report's folder button) and, from the queue
editor's *Show folder*, `show_calc_folder(name)`: the backend resolves a queued
calc's folder (its output selected, else its run folder; one that does not
exist yet is refused with a reason), so the page never assembles a workspace
path — a system's calcs run in its own folder, and one that ran keeps where it
ran.

P5 governs the shape: an external program is **launched, never merely named** —
telling the user which file to open is the command line by another route. So
the setting is `Settings.viewer_target` (`"in_app"` default | `"system"`), which
changed **where a Visual row's click ended**, never what ORCAdesk did first:
orca_plot still ran, the same cube at the same grid. Choosing an external viewer
must never mean getting less. The setting is still stored and round-tripped,
but no page reads it since the classic front-end went.

**`OPENABLE_SUFFIXES` is a trust boundary, not tidiness.** These paths arrive
from the front-end, and `os.startfile` on a path is double-clicking it — handed
an `.exe` it would *run* it. Opening is therefore default-deny against a list of
data formats, so no list of dangerous extensions has to be kept correct.
Revealing has no such hazard (the file manager selects, it does not execute) and
accepts any existing path, falling back to the parent when the file is gone.

The external route (an in-app row's counterpart that handed the structure,
an orbital cube, or an ESP map's cube pair to the OS) went with the classic
front-end. Its backend pieces stay: `bridge.save_structure_xyz` (writes
`<base>_structure.xyz` for a structure that is no file on disk — no page calls
it now), and `PlotOptionsResult.folder` / `CubeJobPayload.path`, which exist
because the front-end holds a calc *name*, not a path, and must never assemble
a workspace path itself (P4).

## NBO analysis (ORCAdesk's own, in process)

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
another's name. The notebook's section (`mountNbo` in `web/next/viewer.js`)
keeps no session cache of its own — the classic `_nboCache` went with that
front-end — and relies on the backend's on-disk cache, which makes a click on
an already-analysed run ~4 ms.
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
member named in `core/plot.py` (`{base}.nbo7a.g60.cube`, so `cubeName` in
`mvwin.js` mirrors one rule) but not a `PLOT_KINDS` one — `_menu_sequence` raises and
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
second search; a cube for another result rebuilds it in seconds). The
per-orbital *View* button that requested it lived in the classic card's Lewis
table and went with that front-end; the notebook's section draws no Lewis
table and makes no `kind: "nbo"` request, so this path has no caller now.

`warnings` is the honest half: below 99% of the density in the minimal basis the
molecule is not atoms-plus-corrections and the analysis says so rather than
degrading quietly (P2). The floor sits below where this implementation normally
lands so it flags a real problem, not every result.

## Paths: dev vs PyInstaller-frozen

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

## Packaging the Windows build (build.bat, build.spec, installer.iss)

**The two artifacts are licensed differently.** The source is MIT. The Windows
build bundles PyQt6 and PyQt6-WebEngine (GPL-3.0-only), Qt 6.11.1 and Qt WebEngine
(LGPL-3.0, with Chromium's components under their own licenses, LGPL-2.1 for
FFmpeg the most restrictive), Python 3.12.10 (PSF, with OpenSSL under Apache-2.0)
and smaller pieces, so the installer and `dist\ORCAdesk\` are conveyed under the
GPL-3.0 as a whole. MIT is GPL-compatible, so the repository stays MIT; only the
binary has the GPL's obligations: the license texts, the notices, and the
corresponding source. Three files say so, and `build.spec` copies all of them
**next to `ORCAdesk.exe`** (not into `_internal\`), so the zipped dist folder and
the installed folder both carry them:
- `LICENSE-BINARY.txt` — what the binary is licensed under and where the
  corresponding source is (this release's git tag, the PyQt6 sdists on PyPI, the
  Qt 6.11.1 sources on download.qt.io). It is the installer's license page
  (`LicenseFile=`), so the wizard no longer presents MIT alone.
- `THIRD_PARTY_NOTICES.txt` — every bundled component with its version, license,
  copyright line, files, and the `licenses\` file holding its full text, made
  from the files actually present in `dist\ORCAdesk`; it ends with where each
  text came from.
- `licenses/` — the full texts (GPL-3.0, LGPL-3.0/2.1, Apache-2.0, MPL-2.0, the
  packages' own dist-info license files, Python's `LICENSE.txt` and its
  incorporated-software section, a Qt notice, Qt's and Qt WebEngine's third-party
  attributions collected from doc.qt.io/qt-6.11 per component, Mesa/LLVM for
  `opengl32sw.dll`, 3Dmol.js, the fonts' OFL, PyInstaller's COPYING).

`installer.iss` is **tracked** (it was git-ignored until 0.11.3): with `build.spec`,
`build.bat` and `requirements-build.txt` it is the "scripts used to control
compilation and installation" the GPL counts as corresponding source. It installs
the dist folder recursively — the notices come with it — and an ISPP `#error`
stops the compile when `dist\ORCAdesk` lacks them, so an installer cannot be made
from an older build. Settings → About states the source license, where the
notices are, that ORCAdesk is an independent personal project, and that there is
no warranty. `tests/test_release_notices.py` pins the seams: `installer.iss`'s
version against `APP_VERSION`, the source tag in `LICENSE-BINARY.txt`, every
`requirements-build.txt` pin against its notice, and the notices against the
files in `licenses/`.

**Build only from the clean environment.** PyInstaller bundles whatever the
building interpreter can import, so `build.bat` recreates `.venv-build\`
(git-ignored) on every run and installs `requirements-build.txt`, which pins the
app's dependencies and PyInstaller with `==`. Built from the global site-packages,
the 0.11.3 candidate shipped PyYAML, charset_normalizer and typing_extensions,
none of which the app imports, and Git for Windows' OpenSSL 3.5.6 DLLs, which
PyInstaller's QtNetwork hook found on the Git Bash `PATH` for Qt's OpenSSL TLS
backend. `build.spec` excludes those packages too, and drops `qopensslbackend.dll`
with `libssl-3-x64.dll` / `libcrypto-3-x64.dll` (no TLS goes through Qt;
Schannel stays; Python's own `libssl-3.dll` / `libcrypto-3.dll` are other files).
A changed pin or Python version changes what the notices must say — update
`THIRD_PARTY_NOTICES.txt`, `LICENSE-BINARY.txt` and `licenses/` with it.

**Unused Qt is kept out by an allowlist of PyQt6 modules.** `build.spec` excludes
every PyQt6 extension module except the eight the app loads (QtCore, QtGui,
QtWidgets, QtNetwork, QtWebChannel, QtWebEngineCore, QtWebEngineWidgets, and
QtPrintSupport, which `PyQt6.QtWebEngineWidgets` imports itself). Without it,
Qt6WebEngineWidgets' link to Qt6QuickWidgets made PyInstaller hidden-import
`PyQt6.QtQuickWidgets` → `QtQuick` → `QtQml`, whose hook collects the whole
`qml/` tree; the QML plugins in it pulled in 58 Qt DLLs the app never loads,
among them Quick3D, Quick3DPhysics and QuickTimeline, which Qt offers only under
the GPL. The Qt DLLs Qt WebEngine really links (Qt6Quick, Qt6QuickWidgets, the
Qt6Qml* four, Qt6OpenGL, Qt6Positioning, Qt6WebChannel, Qt6PrintSupport) still
arrive through PyInstaller's binary dependency analysis — a PE-import walk from
the kept modules, QtWebEngineProcess.exe and the collected plugins reaches
exactly those 17 Qt6 DLLs — and Qt WebEngine Widgets needs no QML import at
run time. The PDF image-format plugin (`qpdf.dll`, the only thing linking
Qt6Pdf) is dropped too. `collect_all("PyQt6.QtWebEngine*")`, which the spec used
to run, collected nothing (the two are extension modules, not packages);
PyInstaller's own QtWebEngineCore hook brings QtWebEngineProcess, the `.pak`
resources, ICU data and the locales. Measured on the 0.11.3 build:
1,793 files / 167 DLLs / 545 MiB before, 352 files / 50 DLLs / 478 MiB after
(the `qml/` tree was 1,341 of the removed files; Qt6WebEngineCore.dll and its
resources are most of what remains). Checked by launching the built exe: the
first-run dialog, then the notebook page over QWebChannel, WebGL and a 3Dmol.js
viewer, driven through `ORCADESK_REMOTE_DEBUG`.
