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
    # The rail row's anatomy beyond a dot and a name -- the grip and its
    # .nogrip, the .qexp caret, .qtext/.qhead with the .qtag backend tag and the
    # .qm meta line, and .qacts with its .qeditslot/.qedit and .qinpslot/.qinp
    # -- is gone with the compact rail: the main window's rail is a narrow
    # status strip and the queue is edited in the queue window (DESIGN.md
    # §17.2, §17.6).
    OUTSTANDING = {"viewstage", "slider",
                   "nogrip", "qexp", "qtext", "qhead", "qtag", "qm",
                   "qacts", "qeditslot", "qedit", "qinpslot", "qinp"}
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


def test_the_model_picker_says_each_models_licence():
    """Some MACE weights are under the Academic Software License (academic,
    non-commercial use only), the default among them, so the picker labels
    every model and says what its licence allows and where the full text is.
    Which model is under which licence is data (data/mace_licenses.json); the
    builder only words each group, so every group needs its words."""
    from orcamgr.state.store import load_choice_groups
    src = _read(NEXT / "build.js")
    assert '"mace_licenses"' in src, "the builder no longer loads the licence groups"
    words = src[src.index("const MODEL_LICENSES"):]
    words = words[:words.index("};")]
    for code in load_choice_groups("mace_licenses"):
        assert re.search(rf"\b{re.escape(code)}: \{{ label:", words), \
            f"MODEL_LICENSES has no wording for the {code} group"
    assert "github.com/gabor1/ASL" in words      # where the ASL's full text is
    assert 'data-x="licnote"' in src             # the line under the picker
    # the queue editor's MLIP set text names a model too
    assert "NB.build.licenseNote(" in _read(NEXT / "qed.js")


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


def _function_body(src: str, name: str) -> str:
    """The text of `function name(...) { ... }` in src, braces balanced."""
    m = re.search(r"function " + re.escape(name) + r"\s*\(", src)
    assert m, f"qed.js lost {name}()"
    i = src.index("{", m.end())
    depth = 0
    for j in range(i, len(src)):
        depth += {"{": 1, "}": -1}.get(src[j], 0)
        if depth == 0:
            return src[i:j + 1]
    raise AssertionError(f"unbalanced {name}()")


def test_a_finished_block_takes_a_next_step_by_placement_alone():
    """The ghost slot follows ANY selected block, finished or not (the
    design's rule: a Freq after a done OptTS is how a follow-up is queued).
    A standalone block becomes a lane's first node through place_calc --
    layout only, allowed in every state -- never through update_calc, which
    the store refuses for a finished calc and which would re-queue it (P24)."""
    src = _read(NEXT / "qed.js")
    slot = _function_body(src, "slotOf")
    assert "editable" not in slot, "the + slot is withheld from a finished block again"
    adopt = _function_body(src, "adopt")
    assert 'op: "place_calc"' in adopt
    assert 'op: "update_calc"' not in adopt, "adoption edits the calculation"


def test_the_set_editor_writes_through_the_inp_set_slots_alone():
    """With nothing selected the inspector is the inp-set editor (it replaced
    the help text). A set is written by save_inp_set / delete_inp_set and
    nothing else -- no browser storage standing in for the library -- and a
    saved set reaches existing blocks only through the undoable ops batch."""
    src = _read(NEXT / "qed.js")
    assert 'NB.call("save_inp_set"' in src and 'NB.call("delete_inp_set"' in src
    assert "localStorage" not in src and "sessionStorage" not in src
    assert "Select a block to see its system" not in src, "the help text is back"
    assert re.search(r"else inspectSets\(host\);", src), "nothing selected no longer shows the set editor"
    offer = _function_body(src, "offerUpdate")
    assert "EDITABLE" in offer, "the set update may reach blocks that have run"
    assert "apply(" in offer


def test_the_main_rail_is_status_only():
    """The main window's rail reports and navigates; reordering, duplicating
    and editing are the queue window's (DESIGN.md §17.2). The one removal it
    keeps is the Finished group's own Clear, which tidies, not edits."""
    src = _read(NEXT / "rail.js")
    for slot in ("reorder_calc", "add_calc", "update_calc"):
        assert f'NB.call("{slot}"' not in src, f"the rail still calls {slot}"
    assert "draggable" not in src, "rail rows are not dragged any more"
    assert "open-queue" in src, "the rail lost its way to the queue window"


def test_the_queue_editor_draws_a_geometry_reference_as_an_edge():
    # a calculation built from another's geometry (outside the editor, so with
    # no `after`) hangs off it in the drawing and shares its lane; adopting a
    # block takes its whole chain into one system
    src = (WEB / "next" / "qed.js").read_text(encoding="utf-8")
    up = _function_body(src, "upOf")
    assert "c.after" in up and "ref_name" in up and '"reference"' in up
    model = _function_body(src, "model")
    assert "upOf(c)" in model, "edges and lanes must follow upOf, not `after` alone"
    adopt = _function_body(src, "adopt")
    assert "P.lane.nodes" in adopt and "place_calc" in adopt and "update_calc" not in adopt


def test_a_level_block_has_a_type_and_a_rendered_preview():
    """An ORCA inp set is a level: the block made from it runs the job its
    kind says. The inspector's Type select changes that kind with an
    update_calc patch (one undoable batch), offers only the kinds the backend
    fills {{JOB}} for (get_inp_sets `jobs`), and the rendered input comes from
    build_inp_preview -- the renderer the engine runs, not a JS copy."""
    src = (WEB / "next" / "qed.js").read_text(encoding="utf-8")
    node = _function_body(src, "inspectNode")
    assert 'data-f="type"' in node and "typeKinds()" in node
    assert 'NB.call("build_inp_preview"' in node and 'data-f="rendered"' in node
    st = _function_body(src, "setType")
    assert 'op: "update_calc"' in st and "kind: kind" in st and "apply(" in st
    assert "r.jobs" in _function_body(src, "loadSets")
    assert "{{JOB}}" in src and "TightOpt" not in _function_body(src, "inspectNode")
    save = _function_body(src, "edSave")
    assert "hasJobLine" in save and "GEOM" in save, "the set editor no longer asks for {{JOB}}"


def test_a_workflow_is_copied_locally_and_pasted_through_the_ops_batch():
    """Copy keeps the workflow in the editor (and the UI state, to outlive a
    reload) -- never in browser storage, never as queue state; a paste
    is ONE apply_queue_ops batch with its inverse, like every other edit."""
    src = (WEB / "next" / "qed.js").read_text(encoding="utf-8")
    copy = _function_body(src, "copyWorkflow")
    assert 'NB.call("set_ui_state", CLIP_KEY' in copy and "apply" not in copy
    assert 'const CLIP_KEY = "queue.workflow"' in src
    assert "localStorage" not in src and "sessionStorage" not in src
    for fn in ("pasteAfter", "pasteIntoSystem", "pasteOnFiles"):
        body = _function_body(src, fn)
        assert body.count("applySel(") == 1, f"{fn} must apply one batch"
        assert "NB.call(" not in body, f"{fn} writes around the ops batch"
        assert "remove_calc" in body, f"{fn} has no undo"
    assert 'op: "add_calc"' in _function_body(src, "instantiate")
    paste = _function_body(src, "paste")
    assert 'NB.call("pick_xyz_files")' in paste
    drop = _function_body(src, "dropXyz")
    assert 'NB.call("read_xyz_paths"' in drop and "pasteOnFiles(null" in drop


def test_a_rubber_band_starts_on_the_empty_canvas_only():
    """The band must not start a lane's or a set's drag: it starts on the
    background button alone, and a selection is one row."""
    src = (WEB / "next" / "qed.js").read_text(encoding="utf-8")
    wire = _function_body(src, "wireCanvas")
    assert 'addEventListener("mousedown"' in wire and 'contains("qe-bg")' in wire
    hits = _function_body(src, "bandHits")
    assert "x.lane === lane" in hits and "ignored" in hits


def test_a_level_block_takes_its_options_through_the_builders_level_mode():
    """Every option the old queue's builder had reaches a level's block:
    "Edit options in the builder" opens the Guided builder in Level mode
    (the level's text read-only, no Method / Resources / Per-element basis)
    and its Save is ONE update_calc patch through the editor's undoable
    batch -- kind, config (StepConfig field names, LEVEL_KEYS only),
    geometry -- keeping raw_text / set_name unless another level was picked."""
    build = _read(NEXT / "build.js")
    for name in ("levelForm", "levelRow", "kindBlocks"):
        assert f"function {name}(" in build, f"build.js lost {name}()"
    assert "const LEVEL_KEYS = [" in build
    keys = re.search(r"const LEVEL_KEYS = \[(.*?)\];", build, re.S).group(1)
    for k in ("geom_constraints", "geom_scan", "max_iter", "irc_init_hess", "irc_hess_file",
              "freq_temp_k", "nmr_jcoupling", "options", "solvation", "scf_convergence"):
        assert f'"{k}"' in keys, f"Level mode no longer sends {k}"
    for k in ("functional", "basis_set", "nprocs", "maxcore_mb", "basis_assignments"):
        assert f'"{k}"' not in keys, f"Level mode sends {k}, which is the level's"
    form = _function_body(build, "levelForm")
    assert "Per-element" not in form and "Resources" not in form and "readonly" in form
    assert "st.lvlCtx.save(" in build, "a level's Save no longer goes through the editor's batch"
    src = _read(NEXT / "qed.js")
    opener = _function_body(src, "openLevelBuilder")
    assert "level:" in opener and "apply(" in opener and "updates(" in opener
    assert 'data-a="lvedit"' in _function_body(src, "inspectNode")


def test_the_irc_direction_is_read_from_its_own_select():
    """The push-along-a-mode plate has a "Direction" field too (a +/- toggle
    without a control); found first by label, it made every IRC's Direction
    read as the default. A field is found by its label AND its control."""
    body = _function_body(_read(NEXT / "build.js"), "readForm")
    assert 'x.querySelector("input, select, textarea")' in body.split("const val")[0]


def test_every_add_outside_the_builder_is_guarded_against_overwriting_output():
    """While a run is going an add starts at once, with no Run click to screen
    it: a set drop, a paste, a row copy and a duplicate ask first
    (existing_outputs) as the builder does, and the names they make step
    around folders that already hold output. (An .xyz drop makes rows with
    no calculation in them, so there is no folder to overwrite.)"""
    src = _read(NEXT / "qed.js")
    assert 'NB.call("existing_outputs"' in _function_body(src, "existingOutputs")
    for fn in ("addFromSet", "pasteAfter", "pasteIntoSystem", "pasteOnFiles", "copyLane",
               "duplicateBlock"):
        assert "guardOverwrite(" in _function_body(src, fn), f"{fn} adds without asking"
    for fn in ("addFromSet", "pasteAfter", "pasteIntoSystem", "pasteOnFiles", "copyLane",
               "duplicateBlock", "manualBuild"):
        assert "avoidDisk(" in _function_body(src, fn), f"{fn} names over existing folders"


def test_a_copied_workflow_keeps_a_level_blocks_options():
    """buildWorkflow / instantiate dropped the config of every text step, so
    a copied level block lost its constraints and IRC settings; a level
    ('{{JOB}}' on its ! line) keeps them, and a paste checks the atoms
    they name against each structure it lands on."""
    src = _read(NEXT / "qed.js")
    for fn in ("buildWorkflow", "instantiate"):
        assert "hasJobLine(" in _function_body(src, fn), f"{fn} drops a level's options"
    for fn in ("pasteAfter", "pasteIntoSystem", "pasteOnFiles"):
        assert "wfAtomNeed(" in _function_body(src, fn), f"{fn} does not check constraint atoms"


def test_the_old_queues_block_actions_are_in_the_inspector():
    """Duplicate, Show folder, run earlier / later, the structures panel, the
    input the run folder holds, and Clear finished (done and failed only: a
    cancelled block runs again) -- each through the ops batch."""
    src = _read(NEXT / "qed.js")
    node = _function_body(src, "inspectNode")
    for a in ("dup", "folder", "earlier", "later", "structs", "ownname"):
        assert f'"{a}"' in node, f"the inspector lost {a}"
    assert 'NB.call("get_inp"' in node, "a finished block shows a regenerated input again"
    assert 'const FINISHED = ["done", "failed"]' in src
    assert 'id="qe-clearfin"' in src
    move = _function_body(src, "moveRun")
    assert 'op: "order"' in move and "dependentsOf(" in move, "a reorder may break a dependency"
    rm = _function_body(src, "requestRemove")
    assert "Remove only this" in rm and "NB.running" in rm
    assert 'NB.call("check_structure"' in _function_body(src, "inspectSystem")

def test_the_queue_window_hosts_the_main_windows_new_calculation_unsqueezed():
    """The builder in the queue window IS the main window's New calculation:
    the same cell (.cell.editing.stub, "New calculation" in its editbar), in a
    stream-like column (.streaminner) of the reference's cell width drawn over
    the lanes and the inspector -- not squeezed into the 360px inspector,
    which stacked Mult. on a row of its own and narrowed the stage."""
    src = _read(NEXT / "qed.js")
    insp = _function_body(src, "renderInspector")
    assert 'NB.$("qe-buildinner")' in insp and "bhost.appendChild(_buildCell)" in insp
    assert not re.search(r"(?<!b)host\.appendChild\(_buildCell\)", insp), "the builder is back in the inspector"
    assert '<div class="qe-build" id="qe-build" hidden><div class="streaminner" id="qe-buildinner">' in src
    css = _read(NEXT / "app.css")
    assert re.search(r"\.qe-build\{position:absolute; top:0; bottom:0; left:220px; right:0;", css)
    assert ".qe-build > .streaminner > .cell{width:100%; max-width:1040px;}" in css
    assert ".qe-app[data-building]{grid-template-columns" not in css, "the inspector widens for the builder again"
    build = _read(NEXT / "build.js")
    cell = _function_body(build, "cellFor")
    assert '"New calculation"}</b>' in cell, "a new calculation's title is not the old builder's"


def test_a_manual_build_opens_on_the_block_it_follows():
    """"New calculation after X" takes X's optimized geometry, whatever else
    the queue holds: the seed carries X as its reference, the builder cell
    takes the seed when it is made (a mount reading the shared one after its
    awaits could find it gone and open on an old draft's reference -- the
    first name in the list -- under X's title), and the select defaults to
    the seed's reference, offered even when X's state would not list it. The
    name is the row's name, "_" and the kind's job (mol_Opt2), as the editor
    names a step, never "<row>-calc"."""
    src = _read(NEXT / "qed.js")
    mb = _function_body(src, "manualBuild")
    assert 'geometry_source: parent ? "reference" : "direct", ref_name: parent ? parent.name : ""' in mb
    assert '"-calc"' not in mb
    assert 'freeName(sysName + SEP + SUFFIX[k], x)' in mb and "name: names.opt" in mb
    build = _read(NEXT / "build.js")
    cell = _function_body(build, "cellFor")
    assert "seed: calc ? null : _seed" in cell and "_seed = null;" in cell
    mount = _function_body(build, "mount")
    assert "_seed" not in mount, "mount reads the shared seed again"
    assert "calc = ctx.seed;" in mount
    render = _function_body(build, "render")
    assert 'calc.geometry_source === "reference" && calc.ref_name && refs.indexOf(calc.ref_name) < 0' in render
    assert 'select("Take the optimized geometry from", refs, calc ? calc.ref_name : refs[0])' in render
    # the Name follows the Type while it is still the one the place gave
    follow = _function_body(build, "followName")
    assert "name === n[from] ? n[to] : name" in follow


def test_the_plus_slot_opens_the_add_menu():
    """Pressing + opens the add menu instead of the whole builder: a level
    and a step type go through addFromSet (one undoable, guarded batch) with
    the kind chosen, a workflow through pasteAfter / pasteIntoSystem, and the
    manual build is one of its choices. The level used is remembered in the
    UI state, and the menu's own keys never reach the canvas's (Escape would
    clear the selection, Delete remove a block)."""
    src = _read(NEXT / "qed.js")
    wire = _function_body(src, "wireCanvas")
    assert "openAddMenu(" in wire and "manualBuild(" not in wire
    add = _function_body(src, "menuAdd")
    assert "addFromSet(g, set, kind" in add and 'NB.call("set_ui_state", LEVEL_KEY' in add
    assert 'want || nextKind(' in _function_body(src, "addFromSet")
    slot = _function_body(src, "workflowAtSlot")
    assert "pasteAfter(g.parent" in slot and "pasteIntoSystem([g.lane]" in slot
    assert "manualBuild(" in _function_body(src, "menuManual")
    assert 'closest(".qe-addmenu")' in _function_body(src, "wireKeys")


def test_saved_workflows_go_through_their_own_slots():
    """The library's Workflows group is the backend's workflows.json (never
    browser storage, never queue state); a saved workflow clicked goes into
    the selected rows or after the selected block, else becomes the copy."""
    src = _read(NEXT / "qed.js")
    assert 'NB.call("get_workflows")' in _function_body(src, "loadWorkflows")
    assert 'NB.call("save_workflow"' in _function_body(src, "saveWorkflowAs")
    assert 'NB.call("delete_workflow"' in _function_body(src, "deleteWorkflow")
    use = _function_body(src, "useWorkflow")
    assert "pasteIntoSystem(rows" in use and "pasteAfter(n" in use and "CLIP_KEY" in use


def test_several_rows_take_one_paste_and_one_charge():
    """Ctrl+click on row names selects several; a paste fills every empty
    one in one batch; their charge / multiplicity is set in one batch (a row
    that has started keeps its own); dropped files arrive selected, with the
    charge / multiplicity their comment line states."""
    src = _read(NEXT / "qed.js")
    assert "pasteIntoSystem(rows, _clip)" in _function_body(src, "paste")
    files = _function_body(src, "pasteOnFiles")
    assert 't: "rows"' in files and "f.charge" in files and "f.multiplicity" in files
    rc = _function_body(src, "setRowsChargeMult")
    assert "update_system" in rc and "apply(" in rc and "L.started" in rc
    assert "toggleRow(" in _function_body(src, "wireCanvas")


def test_a_stack_removes_one_member_and_draws_one_card_each():
    """Remove / Delete on one selected block takes the selected calculation
    only -- a stack loses one member at a time -- unless a band selected the
    block whole; several selected blocks go together. A stack draws a back
    card per other member (capped) and its count bottom right, apart from the
    run order a block shows top right."""
    src = _read(NEXT / "qed.js")
    pick = _function_body(src, "removeChoice")
    assert "_sel.all ? allNames([n]) : [c.name]" in pick, "a stack's Remove takes every member again"
    assert 't === "multi"' in pick and "allNames(nodes)" in pick
    assert "all: true" in _function_body(src, "endBand"), "a band over one block no longer selects it whole"
    assert "requestRemove(removeChoice())" in _function_body(src, "inspectNode")
    assert "requestRemove(removeChoice())" in _function_body(src, "inspectMulti")
    assert "requestRemove(pick)" in _function_body(src, "wireKeys")
    draw = _function_body(src, "renderCanvas")
    assert "Math.min(n.members.length - 1, MAX_BACKS)" in draw
    # each card's border is its own calculation's state: front = member 0
    assert "const edge = (/** @type {number} */ i) => calcState(n.members[i]);" in draw
    assert 'data-depth="${backs - i}" data-edge="${edge(backs - i)}"' in draw
    assert 'data-st="${n.state}" data-edge="${edge(0)}"' in draw
    assert "n.members.map(calcState)" in _function_body(src, "nodeState")
    css = _read(NEXT / "app.css")
    assert ':is(.qe-card,.qe-back)[data-edge="running"]' in css
    assert 'class="qe-line qe-line2">${sub}<span class="qe-ord" data-stack="1"' in draw


def test_a_step_after_an_irc_asks_which_structure():
    """Every add after an IRC -- the + menu / a dropped set (addFromSet), a
    paste (pasteAfter, once per structure picked, sharing nodes), a block's
    geometry (setGeometry "pick:") -- goes through askIrcStructure: the
    IRC's energy profile from its _Full_trj (else B reversed + F), an end
    taken by reference, any other point copied in; and the inspector no
    longer offers the IRC's bare final geometry, which is the TS."""
    src = _read(NEXT / "qed.js")
    ask = _function_body(src, "askIrcStructure")
    assert 'NB.call("get_irc_structures", irc.name)' in ask and 'NB.call("get_structure_frames"' in ask
    assert 'read("Full_trj")' in ask and '(await read("B_trj")).slice().reverse()' in ask
    assert 'at === fEnd ? [F] : at === bEnd ? [B]' in ask, "an end picked on the graph is copied instead of referenced"
    assert 'tail: SEP + "P" + (at + 1), pt: "", xyz: coordsOf(pts[at].xyz)' in ask
    # Left / Right step the point wherever the keyboard is in the window (the
    # confirm button has it first), and the hook goes when the window does
    assert 'document.addEventListener("keydown", keys, true)' in ask
    assert 'document.removeEventListener("keydown", keys, true)' in ask and "unhook();" in ask
    assert 'prof.addEventListener("keydown"' not in ask, "the arrows work on the graph alone again"
    css = _read(NEXT / "app.css")
    assert ".qe-ircnum::-webkit-inner-spin-button" in css, "the number field shows its spin arrows again"
    # a point can be named by its number (its frame in _IRC_Full_trj.xyz), clamped
    assert 'data-f="num" min="1" max="${n}"' in ask
    assert "goTo(v - 1)" in ask and 'num.addEventListener("input"' in ask
    assert "const goTo = (i) => { at = Math.max(0, Math.min(n - 1, i)); mode = \"point\"; show(); };" in ask
    # One point / Both ends is a choice in the window; ONE confirm button says what it does
    assert 'seg([["point", "One point"], ["both", "Both ends"]], mode)' in ask
    assert "foot().textContent = label();" in ask and '"Add at both ends"' in ask
    assert '{ label: "This point"' not in ask, "picking and confirming are one click again"
    assert "await askIrcStructure(P.members[0], true)" in _function_body(src, "addFromSet")
    paste = _function_body(src, "pasteAfter")
    assert "await askIrcStructure(P.members[0], true)" in paste and "nodeIds: nodeIds" in paste
    assert 'P.kind === "irc"' not in paste.split("askIrcStructure")[0].replace('if (P.kind === "irc")', ""), \
        "a paste after an IRC is refused again"
    assert "askIrcStructure(irc, false)" in _function_body(src, "setGeometry")
    insp = _function_body(src, "inspectNode")
    assert 'key: "pick:" + x.name' in insp
    irc_branch = insp[insp.index('if (x.kind === "irc")'):]
    assert irc_branch.index("return;") < irc_branch.index('into.push({ key: "ref:" + x.name, label'), \
        "an IRC's bare final geometry (the TS) is offered again"
    for slot in ("get_irc_structures", "get_structure_frames"):
        assert f'"{slot}"' in src[src.index("const SLOTS_NEEDED"):src.index("];", src.index("const SLOTS_NEEDED"))]


def test_the_editor_names_a_step_system_underscore_job():
    """A step the editor makes is `<system>_<Job>` -- the job as ORCA's
    keyword ({{JOB}}) reads it, Ltyr_Opt, Ltyr_IRC -- its tails _F / _B /
    _P<k>, a copy <name>_copy; older hyphen names still split into a stack."""
    src = _read(NEXT / "qed.js")
    assert 'const SEP = "_";' in src
    assert 'opt: "Opt", ts_opt: "OptTS"' in src and 'irc: "IRC", sp: "SP"' in src
    assert 'const root = String(sys.name || "") + SEP + (SUFFIX[kind] || "step");' in _function_body(src, "addFromSet")
    assert "tail: tailOf(c.name)" in _function_body(src, "addFromSet")
    assert "const root = t.laneName + SEP + members[0].suffix;" in _function_body(src, "instantiate")
    assert 'freeName(c.name + SEP + "copy", x)' in _function_body(src, "duplicateBlock")
    assert "Math.max(s.lastIndexOf(SEP), s.lastIndexOf(\"-\"))" in _function_body(src, "stem")
    for old in ('+ "-" + SUFFIX', '"-copy"', 'tail: "-F"', 'tail: "-P"'):
        assert old not in src, old


def test_a_redraw_waits_for_a_click_under_way():
    """A poll's redraw between a press and its release replaced the block
    under the pointer, and the click was lost: render() holds while a button
    is down (up to PRESS_MS) and draws once it is released."""
    src = _read(NEXT / "qed.js")
    body = _function_body(src, "render")
    assert "if (_pressAt && Date.now() - _pressAt < PRESS_MS) { _held = true; return; }" in body
    press = _function_body(src, "wirePress")
    assert 'document.addEventListener("mouseup", release, true)' in press
    assert 'host.addEventListener("dragstart", release, true)' in press
    assert "setTimeout(render, 0)" in press
    assert "wirePress(host);" in _function_body(src, "init")


def test_a_set_on_a_finished_block_is_an_overwrite_asked_every_time():
    """A level / MLIP set dropped on (or clicked onto) a DONE or FAILED block
    runs it again -- P24's one way back -- behind an overwrite warning that is
    never skipped by the once-only replace answer; undo is undo_rerun. A
    member still to run (none running) just takes the set in the same batch."""
    src = _read(NEXT / "qed.js")
    assert 'const RERUN = ["done", "failed"];' in src
    rn = _function_body(src, "rerunnable")
    assert "n.members.some((/** @type {any} */ c) => RERUN.indexOf(c.state) >= 0)" in rn
    assert "RERUN.indexOf(c.state) >= 0 || EDITABLE.indexOf(c.state) >= 0" in rn
    assert "n.editable || rerunnable(n)" in _function_body(src, "takesSet")
    rp = _function_body(src, "requestReplace")
    assert "if (!n.editable) { if (rerunnable(n)) requestRerun(n, set); return; }" in rp
    assert rp.index("requestRerun") < rp.index("_replaceOk")
    rr = _function_body(src, "requestRerun")
    assert '({ op: "rerun_calc", name: nm, calc: patch })' in rr
    assert '({ op: "undo_rerun", name: nm })' in rr
    assert "updates(rest, " in rr                  # a member still to run: update_calc
    assert "`Overwrite a ${word} calculation?`" in rr
    assert '{ label: "Overwrite", cls: "btn-danger", act: go }' in rr
    assert '{ label: "Cancel", cls: "btn-ghost", focus: true }' in rr
    assert "_replaceOk" not in rr
    assert "takesSet(n) ? { node: n } : null" in _function_body(src, "dropTarget")
    assert "takesSet(nodeByKey(" in _function_body(src, "showSetTargets")
    assert "set.kind === n.setKind && takesSet(n)) { requestReplace(n, set); return; }" in src


def test_a_step_can_go_before_a_block():
    """The selected block that can still change has a + on its incoming
    side (beforeSlotOf): the add menu opens "Before X" (a level and its step
    only, Opt suggested), a level can be dropped on it, and insertBefore puts
    one new calculation per member in the member's place -- its `after`, the
    gate on that connection, its structure -- re-points the member at it,
    and moves the member and what hangs off it one column right (the whole
    row from its column when that would land on a block)."""
    src = _read(NEXT / "qed.js")
    slot = _function_body(src, "beforeSlotOf")
    assert "if (!B || !B.editable) return null;" in slot, "a + before a started block"
    draw = _function_body(src, "renderCanvas")
    assert 'class="qe-ins" type="button" data-a="ghost"' in draw
    assert "host._ghosts = before ? ghosts.concat([before]) : ghosts;" in draw
    assert "if (g.before) { await insertBefore(g, set, want); return; }" in _function_body(src, "addFromSet")
    ins = _function_body(src, "insertBefore")
    assert 'after: fresh[i], geometry_source: "reference", ref_name: fresh[i], ref_point: ""' in ins
    assert 'gate: f.gate === "approval" ? "approval" : "auto"' in ins
    assert "const moved = hit ? L.nodes.filter((/** @type {any} */ n) => n.col >= B.col) : [...down];" in ins
    # undo: the members first (nothing then refers to the new steps), then the removal
    assert "const inv = unlink.concat(fresh.map(" in ins
    assert "B.stack ? tailOf(c.name)" in ins
    menu = _function_body(src, "openAddMenu")
    assert 'let sugg = B ? "opt" : nextKind(P ? P.kind : "");' in menu
    assert '${B ? "" : `<button class="btn btn-sm btn-ghost qe-amman"' in menu, "a manual build before a block"
    assert "!B && (_clip || _wfs.length)" in menu, "a workflow before a block"
    assert 't.closest(".qe-ins")' in _function_body(src, "dropTarget")
    assert ".qe-ins{" in _read(NEXT / "app.css")


def test_a_long_block_name_keeps_its_job_part():
    """A block name too long for its 140px card gives way in its system part
    ("glyc…_Freq"), never in the job part ("glycine_Fr…"): nameHtml splits it
    at the row's system name (else the last separator), the CSS lets only the
    system part shrink first, and the card's title carries the whole name."""
    src = _read(NEXT / "qed.js")
    body = _function_body(src, "nameHtml")
    assert 'data-split="1"' in body and "qe-stem" in body and "qe-tail" in body
    assert "stem(name)" in body                       # the fallback split
    assert src.count("nameHtml(n.name, L.sys ? L.name : \"\")") == 1
    assert src.count("nameHtml(c.name, L.sys ? L.name : \"\")") == 1
    css = _read(NEXT / "app.css")
    assert ".qe-nm[data-split]{display:flex;}" in css
    assert ".qe-stem{flex:0 100000 auto;" in css and ".qe-tail{flex:0 1 auto;" in css
