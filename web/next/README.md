# Editing the notebook front-end

This folder is the **notebook UI**, the desktop's only front-end. It serves
**two windows**: the main window (Jobs + Results + the queue rail) and the
queue window (the queue editor). Both load `index.html`; the URL says which
one the page is (see *Two windows, one page* below).
It is a plain HTML/CSS/JS page — no build step, no bundler, no framework — so
changing how it looks is: edit a file, save, press a key.

## The one-second loop

1. Edit **`app.css`** (or any `.js` here).
2. Save.
3. Press **F5** (or Ctrl+R) **in ORCAdesk**.

The page reloads and nothing is lost: the queue, the running calculation and
every watcher live in Python, so a reload is a repaint, not a restart. You can
do this while a calculation is running.

## Finding what to change: live DevTools

To point at something on screen and see which rule draws it, run ORCAdesk with
Chromium's inspector switched on:

```bat
set ORCADESK_REMOTE_DEBUG=9222
python main.py
```

Then open **http://localhost:9222** in Chrome or Edge and click the *ORCAdesk*
page. You get the full Elements / Styles panel: hover to highlight, edit any
value live, see it in the app instantly. Nothing you type there is saved — when
it looks right, copy the value into `app.css` and press F5.

(The env var is read in `main.py` before Qt starts, so it has to be set *before*
launching. Any port above 1024 works. It is off unless you set it.)

## What is where

| file | what it draws |
|------|---------------|
| `index.html` | the shell: top bar, rail, stream, results window, the queue editor's root (`#qed`), overlays |
| `app.css` | **everything visual.** One file, sectioned by region |
| `boot.js` | channel, polled queue/log, per-calculation trackers, routing (`?win=`, dropped files, the hand-over to the queue window), toast/modal |
| `rail.js` | the main window's queue rail — a 256px status strip (dot + name per row), run controls, *Open queue* |
| `build.js` | the builder (a cell becomes the form); hosted in the queue editor's inspector |
| `qed.js` | the queue window: inp-set library (ORCA levels, MLIP sets), lanes of nodes and edges, multi-select and workflow copy/paste, inspector (a block with its Type and rendered input, several blocks, an edge, or -- nothing selected -- the inp-set editor), undo |
| `stream.js` | one cell per calculation: thumbnail, chart, output, facts |
| `results.js` | the report — sections, charts, input/output, reaction profile |
| `settings.js` | the settings slide-over |
| `viewer.js` | Structure (picking, measuring), Visual, natural orbitals |
| `mvwin.js` | the pop-out viewer: frame walker, favourites, orbitals & maps |
| `charts.js` | a cell's convergence trace, stage chain and progress row |
| `mol.js` | every structure drawn (Avogadro-style display types, depth cues, the Display menu): the cell thumbnails and the interactive stages |

Colours, radii, fonts and the rail width are **tokens** at the top of `app.css`
(`:root` for dark, `html[data-theme="light"]` for light). Changing a token
changes everywhere it is used, in both themes — that is almost always the edit
you want, rather than a rule further down.

## Two windows, one page

`window.py` loads `index.html?win=main` in the main window and
`index.html?win=queue` in the queue window, each with its own QWebChannel to
the same Bridge. `boot.js` reads the query into `NB.win` and
`<html data-win>`; anything but `queue` (no query included) is the main
window. In the queue window `.app` is not drawn, `rail.js` / `stream.js` /
`results.js` stay idle, and the poll skips the log and the output replay;
`qed.js` draws `#qed`. The settings sheet, modal, toasts and the builder work
in both.

The two pages share no JS. What the main window asks of the queue window (open
the builder with a seed, load a dropped `.inp`, show a calculation) is written
to the UI-state key `queue.handoff` by `NB.handOff()`, which then calls
`open_queue_window`; the queue window takes it (`NB.takeHandOff`, through
the `take_ui_state` slot: read and cleared in one step) on its next poll and
when it becomes visible. The editor holds no queue state: every change is an
`apply_queue_ops` batch, and Ctrl+Z sends the inverse batch -- refused when it
would remove a calculation that has started, with an `order` rebuilt from the
queue as it is then. DESIGN.md §17.6.
The one thing besides the hand-over that crosses windows is the copied
**workflow** (Ctrl+C in the queue window): kept in the editor and mirrored
under the UI-state key `queue.workflow`, so a `.xyz` dropped on the main
window can run it when the drop recipe says so. A level's rendered input is
`build_inp_preview` -- never render `{{JOB}}` in JS.

To work on the queue window, open it (*Open queue*) and press F5 in it: it
reloads on its own, like the main window.

The main window's rail is a **compact strip**, narrower and plainer than the
reference's rail (DESIGN.md §17.2), and the stream has no *New calculation*
stub. Those departures are recorded in `tools/uidiff.py` (`REMOVED`,
`PER_LANDMARK_SKIP`, `STYLE_SKIP`) and in the `OUTSTANDING` set of
`tests/test_next_ui.py`; its CSS is the "the compact rail" block at the bottom
of `app.css`.

## Keeping it honest

`app.css` is the stylesheet of `design/notebook-reference.html`, the approved
design, copied rather than re-interpreted. Both can be rendered and measured, so
"did my change move something it should not have?" is a number, not an opinion:

```bash
python tools/uispec.py mock spec_mock.json
python tools/uispec.py real spec_real.json
python tools/uidiff.py spec_mock.json spec_real.json    # exit code = differences
```

It should print `DIFFERENCES: 0` over 121 landmarks, measured in three passes —
the window at rest, a result section (with a result open and *Show all* on), and
the overlays (the pop-out viewer, the settings sheet, the builder's NEB /
per-element-basis / snippet blocks, all opened first).
**If you add a region, add a landmark for it**: the first version of this list
was chrome only and read 0 while every section of the report was unstyled; the
second stopped at the window at rest and could not see a single overlay.

Two of its sections are not landmarks and are worth reading when it prints 0:

- **`__fonts`** — the width of one sample line rendered in each stack. A
  computed `font-family` is the list that was *declared*, the same on both
  sides, so it can never say that one side fell through to a substitute. That
  is exactly what had happened: the reference loads Inter and JetBrains Mono
  from Google Fonts and the app shipped neither, so the whole window was drawn
  in Segoe UI Variable and Consolas while this printed 0. The faces now live in
  `web/fonts/` and `index.html` links them; `uispec.py` injects the same sheet
  into the reference, so both sides are measured in them with no network. **Do
  not add a CDN `<link>` here** — the app runs offline.
- **`__structure`** — every class present on each page, mock-only and real-only
  listed. Advisory, not counted, because a class appears when the sample data
  calls for it (`.badge.failed` needs a failed row). It is the half of the check
  that can see a control that simply is not there: the Finished group's *Clear*
  and the whole *Opened files* group were missing from the rail without a single
  landmark disagreeing.
- **`__styles`** — a metric audit over every `(parent, element)` class
  combination both pages draw: padding, margin, border, radius, font size,
  weight, leading, tracking, min-height, display. **Counted.** The landmarks are
  121 selectors somebody chose, and a padding a pixel off on a control nobody
  named is exactly as visible — this is what found an 11px `.sep` in a
  two-button segmented control. If it reports something, the two pages put the
  same element somewhere their shared stylesheet reads differently; look at the
  markup, not the CSS. Heights are printed under it but not counted (a box's
  height is its content). If your change is a **deliberate** departure
from the reference, put it in the `builder corrections` block at the bottom of
`app.css` (or start a similar named block) so the departures stay in one place
instead of scattering through the sheet — and add its landmark to
`tools/uispec.py` only if you want it measured.

## Rules worth knowing before you edit

- **Only use class names `app.css` has a rule for.** It is the design's sheet;
  a name it does not know renders as nothing, which is how the result summary
  once became a stacked list of bare divs. The vocabulary is `.plate`,
  `.secdesc`, `.sech`, `.tw`/`table`, `.kvline`, `.factline`, `.field-row`,
  `.checkbox`, `.grouplabel`, `.divider`, `.btn`, `.badge`, `.seg`.
  `tests/test_next_ui.py` fails the build if you invent one.
- **A rule that sets `display` must name the whole state it depends on.** The
  region rules sit in one specificity band, so two that can match the same
  element are decided by source order — which has already cost this UI two bugs
  (DESIGN.md §17.3). Regions are hidden by default and revealed by the view,
  never the other way round.
- **Anything that costs an orca_plot run asks before it spends one.** The
  Visual strip lists the wavefunction's surfaces beside the `.xyz` sets, as
  KINDS — one **Orbitals** tile, not one per orbital; the list on the left holds
  the instances. A row marked *ready* opens straight away, one that is not puts
  up the question first. Get the picks and their ready marks from
  `NB.mv.surfaces` —
  it owns the cube-naming rule, and a second copy of that rule is how a "ready"
  mark and the file the fetch looks for come to disagree. DESIGN.md §17.4c.
- **A stick chart's numbers live on the band, not in a table under it.** Pass
  `{hover: true}` and a `lab` per point to `chart()`; `bindCharts` then owns the
  cursor, the `.chtip` plate and the arrow keys. The whole plot is the hit
  target (a band is 1.6px) and the keyboard path is not optional — it is what
  keeps the values from being hover-only. DESIGN.md §17.4b.
- **Charts are drawn at their box's real pixel width** and emit a matching
  `viewBox`. Do not scale an SVG to fit: that scales its stroke widths and its
  type with it.
- **A `.molcv` is `position:absolute; inset:0`, and its ResizeObserver watches
  the stage.** The canvas sizes its own bitmap from its own box every frame, so
  in flow it can become its own input: at `devicePixelRatio` 1.25 the Structure
  stage grew 1.25x per resize and drifted away from the Visual panel beside it.
  If you add a stage, give it `position:relative` and let the rule at the bottom
  of `app.css` ("the stage owns its height") reach it. DESIGN.md §17.4f.
- The two scripts this page borrows from `web/` (`../scf_graph.js`,
  `../progress_panels.js`) are shared with the Node tests in
  `tests/web/scf_graph.test.js`; changing them means re-running those.
