"""The notebook front-end (web/next/), the desktop window's only UI.

Its own markup, its own stylesheet, its own logic against the Bridge. These
tests pin the seams that can rot without anything failing loudly at import time:

  * the page reaching for files in web/ beyond the DOM-free scripts it borrows
  * a script the shell references going missing, which is a blank pane at runtime
  * the window loading something other than web/next/index.html, or at a size
    the layout is not drawn at
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
NEXT = WEB / "next"
sys.path.insert(0, str(ROOT))

from orcamgr.config import Settings  # noqa: E402


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------- the shell

def test_preview_ships_every_file_its_shell_loads():
    """Every <script>/<link> in web/next/index.html resolves to a real file."""
    html = _read(NEXT / "index.html")
    refs = re.findall(r'(?:src|href)="([^"]+)"', html)
    missing = []
    for r in refs:
        if r.startswith(("qrc:", "http:", "https:", "data:")):
            continue
        if not (NEXT / r).resolve().exists():
            missing.append(r)
    assert not missing, f"web/next/index.html references missing files: {missing}"


def test_preview_borrows_only_the_dom_free_scripts():
    """The only files above web/next/ it may reach for are the trackers.

    scf_graph.js and progress_panels.js parse output and return HTML strings —
    they own no ids and no elements, so sharing them is reuse, not coupling.
    Anything else pulled in from web/ would be markup or styling outside the
    approved design's own folder.
    """
    html = _read(NEXT / "index.html")
    borrowed = sorted(set(re.findall(r'(?:src|href)="\.\./([^"]+)"', html)))
    # fonts/ is a sibling asset directory holding the two typefaces the design
    # is drawn in, and an @font-face sheet owns no DOM and no ids. Everything
    # else above the preview's own folder is still refused.
    allowed = {"scf_graph.js", "progress_panels.js", "fonts/fonts.css"}
    assert set(borrowed) <= allowed, f"unexpected dependencies outside web/next/: {sorted(set(borrowed) - allowed)}"


def test_preview_declares_no_globals_beyond_its_own():
    """One tsc project checks every script under web/, so they share a global
    scope with ../scf_graph.js and ../progress_panels.js.

    The preview keeps everything on NB for that reason; a top-level
    `let bridge` / `queue` / `settings` would be one more name in that shared
    scope, waiting to become a duplicate declaration.
    """
    for f in sorted(NEXT.glob("*.js")):
        src = _read(f)
        tops = re.findall(r"^(?:let|const|var|function|class)\s+([A-Za-z_$][\w$]*)",
                          src, re.MULTILINE)
        allowed = {"NB", "MOL", "CHARTS"}
        assert set(tops) <= allowed,             f"{f.name} declares globals {sorted(set(tops) - allowed)}"


def test_preview_scripts_are_strict_and_type_checked():
    for f in sorted(NEXT.glob("*.js")):
        head = _read(f)[:200]
        assert "@ts-check" in head, f"{f.name} is not type-checked"
        assert '"use strict"' in head, f"{f.name} is not strict-mode"


def test_preview_calls_only_slots_the_bridge_actually_has():
    """Every NB.call("slot") / NB.bridge.slot() names a real @pyqtSlot.

    A typo here is a silent dead button: the channel resolves the property to
    undefined and the promise never settles.
    """
    bridge = _read(ROOT / "orcamgr" / "gui" / "bridge.py")
    slots = set(re.findall(r"def (\w+)\(self", bridge))
    used = set()
    for f in sorted(NEXT.glob("*.js")):
        src = _read(f)
        used |= set(re.findall(r'NB\.call\(\s*"(\w+)"', src))
        used |= set(re.findall(r"NB\.bridge\.(\w+)\(", src))
    unknown = sorted(used - slots)
    assert not unknown, f"the preview calls bridge slots that do not exist: {unknown}"


def test_the_design_check_measures_inside_a_section_too():
    """The landmark list must reach past the chrome.

    The first version of it was top bar, rail, cell, pane — so the diff read 0
    while every section of the report was markup app.css had never heard of.
    A landmark list that only covers what is easy to measure is worse than none,
    because the green number stops the looking.
    """
    spec = _read(ROOT / "tools" / "uispec.py")
    assert "RSEL = [" in spec, "the report landmark list is gone"
    for needed in (".resinner .plate", ".resinner .tw", ".resinner .secdesc",
                   ".resinner .sech h2", ".secblock", ".rawin", ".rawout"):
        assert needed in spec, f"the design check no longer measures {needed}"
    # ...and past the window at rest. The same hole had opened at the far end:
    # the viewer, the settings cards and the builder's conditional blocks do not
    # exist until something is opened, so two passes could not see any of them.
    assert "OSEL = [" in spec, "the overlay landmark list is gone"
    for needed in (".mvwin", ".mvstage", "#mv-volume", ".mvramp", ".envrow",
                   "input.slider", ".nebslot", ".basis-row", ".snips"):
        assert needed in spec, f"the design check no longer measures {needed}"


def test_every_class_the_preview_emits_has_a_rule():
    """A class app.css has no rule for renders as nothing.

    That is what turned the result summary into a stacked list of bare divs:
    the stylesheet was copied from the design but the section contents were
    written with invented names. The exceptions are behaviour hooks the design
    itself uses the same way.
    """
    css = re.sub(r"/\*.*?\*/", "", _read(NEXT / "app.css"), flags=re.S)
    styled = set(re.findall(r"\.([A-Za-z][\w-]*)", css))
    # selected by JS or by an ancestor rule, never styled by their own name —
    # the design uses .molcv the same way (.viewstage canvas / .vizstage canvas)
    hooks = {"cellinp", "cellres", "qinp", "out", "molcv", "chartbox"}
    emitted = {}
    for f in sorted(list(NEXT.glob("*.js")) + [NEXT / "index.html"]):
        src = re.sub(r"/\*.*?\*/", "", _read(f), flags=re.S)
        found = (re.findall(r'class="([^"$`]*)"', src)
                 + re.findall(r'NB\.el\(\s*"[a-z]+",\s*"([^"]+)"', src))
        for group in found:
            for c in group.split():
                if c and not c.startswith("$"):
                    emitted.setdefault(c, set()).add(f.name)
    unstyled = sorted(c for c in emitted if c not in styled and c not in hooks)
    assert not unstyled, (
        "classes with no rule in app.css: "
        + ", ".join(f"{c} ({', '.join(sorted(emitted[c]))})" for c in unstyled))


def test_the_preview_uses_the_references_own_component_vocabulary():
    """Element-by-element against design/notebook-reference.html.

    A class the reference's MARKUP uses and this front-end never emits is a
    piece of the design that is simply not here — the gap that has to be an
    explicit, listed decision rather than something nobody noticed. What is
    still outstanding is named below; the assertion is that the list has not
    grown.
    """
    ref = _read(ROOT / "design" / "notebook-reference.html")
    body = re.sub(r"<script.*?</script>", "", ref[ref.index("MAIN WINDOW"):], flags=re.S)
    used = set()
    for m in re.finditer(r'class="([^"]*)"', body):
        used |= set(m.group(1).split())
    src = chr(10).join(_read(f) for f in sorted(NEXT.glob("*.js")) + [NEXT / "index.html"])
    missing = {c for c in used if not re.search("\\b" + re.escape(c) + "\\b", src)}

    # The two assertions below are what make DROPPING an element a decision
    # somebody took instead of something that quietly happened. Adding a name
    # here is allowed; doing it without saying why in DESIGN.md is not.
    # .seg .sep left this list when the preview had MLIP and CREST builders to
    # put on the other side of it (DESIGN.md §17.4s).
    # .viewstage is the Structure section's stage, which is gone on purpose:
    # the parsed structure is the Visual panel's first tile, so one stage draws
    # the molecule instead of two side by side (DESIGN.md §17.4c).
    # .slider is the Settings sheet's Liquid-Glass intensity control, which
    # only ever configured the classic front-end and went with it (DESIGN.md
    # §17.5).
    OUTSTANDING = {"viewstage", "slider"}
    unexpected = sorted(missing - OUTSTANDING)
    assert not unexpected, (
        "design elements dropped without a decision: " + ", ".join(unexpected))
    # and nothing on the list has quietly become present without being removed
    gone = sorted(OUTSTANDING - missing)
    assert not gone, "these are implemented now — take them off OUTSTANDING: " + ", ".join(gone)


def test_the_reference_is_a_standards_mode_document():
    """Without a doctype it renders in quirks mode, where a table does not
    inherit line-height — which the design check reads as a real difference."""
    assert _read(ROOT / "design" / "notebook-reference.html").lstrip()[:15].lower()         .startswith("<!doctype html>")


# ---------------------------------------------------------------- the window

def test_window_loads_the_notebook_front_end():
    """There is one front-end and one entry point; no setting picks another."""
    src = _read(ROOT / "orcamgr" / "gui" / "window.py")
    assert '"next" / "index.html"' in src
    assert 'web_dir() / "index.html"' not in src
    assert "ui_variant" not in src


def test_frozen_build_ships_the_preview():
    """PyInstaller copies web/ wholesale, so web/next/ rides along."""
    spec = _read(ROOT / "build.spec")
    assert '("web", "web")' in spec


def test_preview_ships_the_typefaces_the_design_is_drawn_in():
    """The design's two faces are carried, not linked, and actually resolve.

    design/notebook-reference.html pulls Inter and JetBrains Mono from Google
    Fonts. The preview shipped no copy, so its stack fell through to system-ui
    and ui-monospace -- Segoe UI Variable and Consolas on Windows -- and every
    10-11px muted label in the queue rail was drawn in a face with a smaller
    x-height than the one it was spaced for. Nothing caught it: a computed
    fontFamily is the DECLARED stack, identical on both sides, so uidiff read
    zero differences (it now probes the resolved face too, under __fonts).

    This is the static half of that check: the link exists, the files behind it
    exist, and the stack in app.css still names what they provide.
    """
    html = _read(NEXT / "index.html")
    assert '"../fonts/fonts.css"' in html, "the preview does not link the bundled fonts"

    sheet = ROOT / "web" / "fonts" / "fonts.css"
    css = _read(sheet)
    families = set(re.findall(r"font-family:\s*'([^']+)'", css))
    assert {"Inter", "JetBrains Mono"} <= families, f"fonts.css declares {families}"

    missing = [u for u in re.findall(r"url\('([^']+)'\)", css)
               if not (sheet.parent / u).exists()]
    assert not missing, f"fonts.css points at files that are not there: {missing}"

    app_css = _read(NEXT / "app.css")
    sans = re.search(r"--font-sans:([^;]+);", app_css, re.S).group(1)
    mono = re.search(r"--font-mono:([^;]+);", app_css, re.S).group(1)
    assert "Inter" in sans, "--font-sans no longer names the face that is bundled"
    assert "JetBrains Mono" in mono, "--font-mono no longer names the face that is bundled"


def test_window_opens_the_notebook_above_its_own_breakpoint():
    """app.css narrows the queue rail below 1320px; the preview opens above
    that, in the layout the design is drawn in (a 600px rail). At 1100 it came
    up in the narrow layout, which reads as a different UI.
    """
    app_css = _read(NEXT / "app.css")
    narrow = re.search(r"@media \(max-width:(\d+)px\)\{\s*\.app\{grid-template-columns:clamp\(",
                       app_css)
    assert narrow, "the rail's narrowing breakpoint moved or changed shape"
    limit = int(narrow.group(1))

    src = _read(ROOT / "orcamgr" / "gui" / "window.py")
    width = int(re.search(r"^\s*_SIZE = \((\d+),", src, re.M).group(1))
    assert width > limit, f"the notebook opens at {width}px, under the {limit}px breakpoint"


def test_the_rail_never_leaves_a_narrow_window():
    """The rail is the queue, the run controls and the result picker for both
    views. It used to be display:none below 1320px, so a window dragged
    narrower lost all three; it narrows (and below 760 stacks) instead."""
    app_css = _read(NEXT / "app.css")
    assert not re.search(r"\.rail\{[^}]*display:none", app_css)


def test_the_builder_seeds_a_new_calculation_from_stepconfig_alone():
    """A new calculation's numbers come from get_step_defaults, never from a
    literal in build.js. The builder kept its own once and every one of them
    drifted somewhere: MaxIter 0 (clamped to 1, so an optimization stopped
    after one cycle), IRC MaxIter 40 against 100, TD-DFT maxdim 0 (clamped to
    1) and nroots 10 against 40."""
    src = re.sub(r"/\*.*?\*/", "", _read(NEXT / "build.js"), flags=re.S)
    # "" is "nothing", not a default; a number or a non-empty string is one
    seeded = re.findall(r'cfg\.(\w+) \|\| (?:d\.\w+ \|\| )?([0-9][^,)}]*|"[^"]+")', src)
    assert not seeded, f"literal defaults in build.js: {seeded}"
    fallback = re.findall(r'num\("[^"]+", (?:d\.\w+ \|\| )?([0-9][0-9.]*)\)', src)
    # Charge 0 / Mult. 1 are the calculation's own, not StepConfig's
    assert sorted(fallback) == ["0", "1"], f"literal fallbacks in build.js: {fallback}"
    from orcamgr.gui import bridge
    assert "def get_step_defaults" in Path(bridge.__file__).read_text(encoding="utf-8")


def test_the_builder_offers_every_mlip_and_crest_kind_the_engine_runs():
    """The notebook builder's MLIP Task field and CREST form are the only way
    the preview makes those calculations (DESIGN.md §17.4s). A kind the engine
    runs and the builder cannot pick would be reachable from nowhere."""
    from orcamgr.core.queue import KNOWN_KINDS
    src = _read(NEXT / "build.js")
    offered = set(re.findall(r'\["((?:mlip|crest)_\w+)", "', src))
    wanted = {k for k in KNOWN_KINDS if k.startswith(("mlip", "crest"))}
    assert offered == wanted, f"builder offers {sorted(offered)}, engine runs {sorted(wanted)}"


def test_the_default_mace_model_is_one_the_picker_lists():
    """StepConfig keeps mlip_model "" (an ORCA calc carries no model) and the
    runner runs DEFAULT_MACE_MODEL for it; get_step_defaults hands the builder
    that same name to preselect. A default the closed picker does not list
    would open the builder on an option that is not there."""
    from orcamgr.mlip.runner import DEFAULT_MACE_MODEL, parse_mace_model
    from orcamgr.state.store import load_choice_groups
    groups = load_choice_groups("mace_models")
    listed = [m for k, g in groups.items() if not k.startswith("_")
              for m in (g if isinstance(g, list) else g.get("keywords", []))]
    assert DEFAULT_MACE_MODEL in listed
    assert parse_mace_model("") == parse_mace_model(DEFAULT_MACE_MODEL)
    src = Path(__import__("orcamgr.gui.bridge", fromlist=["x"]).__file__).read_text(encoding="utf-8")
    body = src[src.index("def get_step_defaults"):src.index("def load_choices")]
    assert "DEFAULT_MACE_MODEL" in body


# ---------------------------------------------------------------- two windows

def test_the_page_lays_itself_out_by_its_window():
    """Both windows load index.html; ?win=queue is the queue editor and
    anything else the main window (DESIGN.md §17.6). The query is the page's
    only way to know, so the read of it, the queue editor's root and its
    script have to be there."""
    boot = _read(NEXT / "boot.js")
    assert 'get("win") === "queue"' in boot, "boot.js no longer reads ?win="
    assert 'setAttribute("data-win"' in boot
    html = _read(NEXT / "index.html")
    assert 'id="qed"' in html and 'src="qed.js"' in html
    assert re.search(r'html\[data-win="queue"\] \.app\{display:none;\}', _read(NEXT / "app.css")),         "the queue window must not draw the main layout"


def test_the_queue_editor_writes_through_the_batched_ops_alone():
    """The editor holds no queue state (CLAUDE.md): every change it makes is
    one apply_queue_ops batch, validated and applied as a whole, and Ctrl+Z
    sends the inverse the same way. A single-row slot called from it would be
    a second write path the undo stack cannot see."""
    src = _read(NEXT / "qed.js")
    assert 'NB.call("apply_queue_ops"' in src
    for slot in ("add_calc", "update_calc", "remove_calc", "reorder_calc"):
        assert f'NB.call("{slot}"' not in src, f"qed.js calls {slot} directly"


def test_the_main_rail_is_status_only():
    """The main window's rail reports and navigates; reordering, duplicating
    and editing are the queue window's (DESIGN.md §17.2). The one removal it
    keeps is the Finished group's own Clear, which tidies, not edits."""
    src = _read(NEXT / "rail.js")
    for slot in ("reorder_calc", "add_calc", "update_calc"):
        assert f'NB.call("{slot}"' not in src, f"the rail still calls {slot}"
    assert "draggable" not in src, "rail rows are not dragged any more"
    assert "open-queue" in src, "the rail lost its way to the queue window"
