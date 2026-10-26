"""The notebook front-end (web/next/) and its ui_variant plumbing.

web/next/ is a SECOND front-end, not a skin of the classic one: its own markup,
its own stylesheet, its own logic against the same Bridge. These tests pin the
seams that can rot without anything failing loudly at import time:

  * the two front-ends leaking into each other (the classic UI must stay exactly
    as it was, and the preview must not depend on classic files that move)
  * a script the shell references going missing, which is a blank pane at runtime
  * the setting losing a leg — the dataclass field, the payload the UI reads, or
    the window's choice of which file to load
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
    """The only classic files it may reach for are the trackers and renderers.

    scf_graph.js and progress_panels.js parse output and return HTML strings —
    they own no ids and no elements, so sharing them is reuse, not coupling.
    Anything else from web/ (app.js, style.css, results_render.js) would make
    the preview a skin of the classic UI again, which it deliberately is not.
    """
    html = _read(NEXT / "index.html")
    borrowed = sorted(set(re.findall(r'(?:src|href)="\.\./([^"]+)"', html)))
    # fonts/ is not classic UI: it is a sibling asset directory holding the two
    # typefaces the design is drawn in, and an @font-face sheet owns no DOM and
    # no ids. Everything else above the preview's own folder is still refused.
    allowed = {"scf_graph.js", "progress_panels.js", "orcadesk_logo.png",
               "fonts/fonts.css"}
    assert set(borrowed) <= allowed, f"unexpected classic dependencies: {sorted(set(borrowed) - allowed)}"


def test_classic_ui_is_untouched_by_the_preview():
    """No file under web/next/ is referenced by the shipping front-end."""
    html = _read(WEB / "index.html")
    assert "next/" not in html


def test_preview_declares_no_globals_that_collide_with_app_js():
    """One tsc project checks both front-ends, so they share a global scope.

    The preview keeps everything on NB for that reason; a second top-level
    `let bridge` / `queue` / `settings` would be a duplicate declaration of the
    classic UI's and fail the type check for both.
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
    # Empty, and kept that way. The last entry was .seg .sep, the divider
    # between the backends and Guided | .inp text in the builder's backend
    # control -- absent until the preview had MLIP and CREST builders to put
    # on the other side of it (DESIGN.md §17.4s).
    OUTSTANDING = set()
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


# ---------------------------------------------------------------- the setting

def test_ui_variant_defaults_to_classic():
    assert Settings().ui_variant == "classic"


@pytest.mark.parametrize("variant", ["classic", "notebook"])
def test_ui_variant_round_trips_through_settings(tmp_path, monkeypatch, variant):
    import orcamgr.config as config                      # noqa: PLC0415

    cfg = tmp_path / "settings.json"
    monkeypatch.setattr(config, "config_file", lambda: cfg)
    s = Settings()
    s.ui_variant = variant
    assert s.save()
    assert Settings.load().ui_variant == variant


def test_unknown_ui_variant_degrades_to_classic(tmp_path, monkeypatch):
    """A hand-edited or downgraded settings.json must not strand the window.

    Settings.load() keeps whatever string is on disk (it does not validate
    values), so the guard that matters is _index_file's: anything that is not
    exactly "notebook" loads the classic UI.
    """
    import orcamgr.config as config                      # noqa: PLC0415

    cfg = tmp_path / "settings.json"
    cfg.write_text('{"ui_variant": "banana"}', encoding="utf-8")
    monkeypatch.setattr(config, "config_file", lambda: cfg)
    assert Settings.load().ui_variant != "notebook"


def test_the_way_out_of_the_preview_is_inside_the_preview():
    """Both front-ends carry the switch, at the bottom of their settings."""
    classic = _read(WEB / "index.html")
    assert 'name="ui-variant" value="classic"' in classic
    assert 'name="ui-variant" value="notebook"' in classic
    assert classic.index('name="ui-variant"') > classic.index('id="about-body"')

    preview = _read(NEXT / "settings.js")
    assert 'value="classic"' in preview
    assert 'value="notebook"' in preview
    assert 'data-s2="ui"' in preview
    assert preview.index('<div class="scard-title">Interface</div>')         > preview.index('<div class="scard-title">About</div>')


def test_bridge_reports_and_accepts_ui_variant():
    src = _read(ROOT / "orcamgr" / "gui" / "bridge.py")
    assert "ui_variant=self.settings.ui_variant" in src
    assert '"ui_variant" in data' in src
    assert "def reload_ui" in src
    assert "ui_variant: str" in _read(ROOT / "orcamgr" / "state" / "schemas.py")


def test_window_picks_the_front_end_from_the_setting():
    src = _read(ROOT / "orcamgr" / "gui" / "window.py")
    # one reader for the setting, because the file to load and the size to open
    # at are both chosen by it and must not disagree
    assert "def _ui_variant" in src
    assert "Settings.load().ui_variant" in src
    assert 'self._ui_variant() == "notebook"' in src
    assert '"next" / "index.html"' in src
    # and never loads a hard-coded index.html behind the setting's back
    assert 'web_dir() / "index.html"\n        self.view.load' not in src


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
    that, in the layout the design is drawn in (a 600px rail). At the classic
    1100 it came up in the narrow layout, which reads as a different UI.
    """
    app_css = _read(NEXT / "app.css")
    narrow = re.search(r"@media \(max-width:(\d+)px\)\{\s*\.app\{grid-template-columns:clamp\(",
                       app_css)
    assert narrow, "the rail's narrowing breakpoint moved or changed shape"
    limit = int(narrow.group(1))

    src = _read(ROOT / "orcamgr" / "gui" / "window.py")
    sizes = re.search(r"_SIZE_BY_VARIANT = \{([^}]*)\}", src).group(1)
    width = int(re.search(r'"notebook":\s*\((\d+)', sizes).group(1))
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
    runs and the builder cannot pick would be reachable only from classic."""
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
