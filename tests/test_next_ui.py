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
    allowed = {"scf_graph.js", "progress_panels.js", "orcadesk_logo.png"}
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

    # Empty, and kept rather than deleted with its last entry: the two
    # assertions below are what make DROPPING an element a decision somebody
    # took instead of something that quietly happened, and an empty set is the
    # strongest form of that record. Adding a name here is allowed; doing it
    # without saying why in DESIGN.md is not.
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
    assert 'ui_variant == "notebook"' in src
    assert '"next" / "index.html"' in src
    # and never loads a hard-coded index.html behind the setting's back
    assert 'web_dir() / "index.html"\n        self.view.load' not in src


def test_frozen_build_ships_the_preview():
    """PyInstaller copies web/ wholesale, so web/next/ rides along."""
    spec = _read(ROOT / "build.spec")
    assert '("web", "web")' in spec
