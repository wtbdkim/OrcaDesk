# Editing the notebook front-end

This folder is the **notebook UI** (Settings → Interface → *Notebook (preview)*).
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
| `index.html` | the shell: top bar, rail, stream, results window, overlays |
| `app.css` | **everything visual.** One file, sectioned by region |
| `boot.js` | channel, polled queue/log, per-calculation trackers, routing, toast/modal |
| `rail.js` | the queue rail — groups, rows, drag reorder, run controls |
| `build.js` | the builder (a cell becomes the form) |
| `stream.js` | one cell per calculation: thumbnail, chart, output, facts |
| `results.js` | the report — sections, charts, input/output, free-energy profile |
| `settings.js` | the settings slide-over |
| `mol.js` | the ball-and-stick thumbnail |

Colours, radii, fonts and the rail width are **tokens** at the top of `app.css`
(`:root` for dark, `html[data-theme="light"]` for light). Changing a token
changes everywhere it is used, in both themes — that is almost always the edit
you want, rather than a rule further down.

## Keeping it honest

`app.css` is the stylesheet of `design/notebook-reference.html`, the approved
design, copied rather than re-interpreted. Both can be rendered and measured, so
"did my change move something it should not have?" is a number, not an opinion:

```bash
python tools/uispec.py mock spec_mock.json
python tools/uispec.py real spec_real.json
python tools/uidiff.py spec_mock.json spec_real.json    # exit code = differences
```

It should print `DIFFERENCES: 0`. If your change is a **deliberate** departure
from the reference, put it in the `builder corrections` block at the bottom of
`app.css` (or start a similar named block) so the departures stay in one place
instead of scattering through the sheet — and add its landmark to
`tools/uispec.py` only if you want it measured.

## Rules worth knowing before you edit

- **A rule that sets `display` must name the whole state it depends on.** The
  region rules sit in one specificity band, so two that can match the same
  element are decided by source order — which has already cost this UI two bugs
  (DESIGN.md §17.3). Regions are hidden by default and revealed by the view,
  never the other way round.
- **Charts are drawn at their box's real pixel width** and emit a matching
  `viewBox`. Do not scale an SVG to fit: that scales its stroke widths and its
  type with it.
- The classic UI in `web/` shares nothing with this folder — no ids, no classes,
  no stylesheet — so you cannot break it from here. The two files this page
  borrows (`../scf_graph.js`, `../progress_panels.js`) are shared, and changing
  those *does* affect both.
