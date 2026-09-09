#!/usr/bin/env python3
"""Generate web/index_next.html from web/index.html.

The notebook front-end (Settings -> Interface -> "Notebook (preview)") is the
SAME markup as the classic UI in a different shell: the same ids, the same
classes, the same inline handlers, so app.js and every renderer drive it
untouched. Keeping it as a hand-maintained second copy of a 760-line file would
mean every Build-tab field added to index.html silently missing from the
preview — so the copy is generated, and tests/test_next_ui.py fails the suite if
the committed file has drifted from what this script produces.

    python tools/build_next_ui.py            # rewrite web/index_next.html
    python tools/build_next_ui.py --check    # exit 1 if it is out of date

Everything the notebook layout needs beyond the shared markup lives in
web/next.css (layout + surface) and web/next.js (the tab/view adapter); this
script only inserts the handful of anchors those two need.
"""
from __future__ import annotations

import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
SRC = WEB / "index.html"
DST = WEB / "index_next.html"

BANNER = """<!--
  GENERATED FILE — do not edit.

  Built from web/index.html by tools/build_next_ui.py. Edit index.html (or the
  script), then re-run it; tests/test_next_ui.py fails if this file is stale.

  This is the notebook front-end: the classic markup re-arranged by next.css
  into three views (Jobs / Results / Settings) with the queue as a permanent
  rail, and next.js translating between the classic tab names and those views.
-->
"""

# The notebook's own navigation, dropped into the top bar between the brand and
# the status pills. Labels, not pictograms — the window has no icon vocabulary
# to lean on, and "Jobs" says more than any glyph for it would.
NAV = """    <nav class="nbnav" aria-label="View">
      <div class="seg" role="group">
        <button type="button" data-view="jobs" aria-pressed="true"
                onclick="nbGo('jobs')">Jobs</button>
        <button type="button" data-view="results" aria-pressed="false"
                onclick="nbGo('results')">Results</button>
        <button type="button" data-view="settings" aria-pressed="false"
                onclick="nbGo('settings')">Settings</button>
      </div>
    </nav>
"""

# Build and the live output share the right column beside the queue rail, so
# that column needs its own switch. These call switchTab() rather than a
# notebook-only function on purpose: switchTab carries the on-entry re-render
# that gives the SCF graph and the geometry stage a measurable box.
STREAM = """
    <!-- right-column switch (Jobs view): the form, or the run in progress -->
    <div class="nbstream-head">
      <div class="seg nbstreamseg" role="group" aria-label="Right pane">
        <button type="button" data-stream="build" aria-pressed="true"
                onclick="switchTab('build')">Build</button>
        <button type="button" data-stream="log" aria-pressed="false"
                onclick="switchTab('log')">Live output</button>
      </div>
    </div>
"""


def _replace_once(text: str, old: str, new: str, what: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(
            f"tools/build_next_ui.py: expected exactly one {what} in "
            f"{SRC.name}, found {n}. index.html changed shape — update this "
            f"script rather than hand-editing {DST.name}."
        )
    return text.replace(old, new, 1)


def build() -> str:
    s = SRC.read_text(encoding="utf-8")

    # next.css layers on top of style.css; it never replaces it, so both load
    # and every classic component keeps its colors, states and focus rings.
    s = _replace_once(
        s,
        '<link rel="stylesheet" href="style.css">\n',
        '<link rel="stylesheet" href="style.css">\n'
        '<link rel="stylesheet" href="next.css">\n',
        "stylesheet link",
    )
    s = _replace_once(s, "<body>\n", "<body>\n" + BANNER, "<body>")
    # the shell starts on Jobs/Build, so the first paint is already correct and
    # next.js only has to sync the nav to it
    s = _replace_once(
        s, '<div class="app">',
        '<div class="app" data-view="jobs" data-stream="build">', "app shell",
    )
    s = _replace_once(
        s, '    <div class="status-group">', NAV + '    <div class="status-group">',
        "status group",
    )
    s = _replace_once(
        s, '  <div class="tab-content">\n', '  <div class="tab-content">\n' + STREAM,
        "tab-content opener",
    )
    # after app.js: next.js wraps switchTab, which must already exist
    s = _replace_once(
        s, '<script src="app.js"></script>',
        '<script src="app.js"></script>\n<script src="next.js"></script>',
        "app.js script tag",
    )
    return s


def main(argv: list[str]) -> int:
    out = build()
    if "--check" in argv:
        current = DST.read_text(encoding="utf-8") if DST.exists() else ""
        if current != out:
            print(f"{DST} is out of date — run: python tools/build_next_ui.py")
            return 1
        print(f"{DST.name} is up to date.")
        return 0
    DST.write_text(out, encoding="utf-8")
    print(f"wrote {DST} ({len(out)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
