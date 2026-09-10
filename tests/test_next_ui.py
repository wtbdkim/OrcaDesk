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
        assert set(tops) <= {"NB", "MOL"},             f"{f.name} declares globals {sorted(set(tops) - {'NB', 'MOL'})}"


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
    assert 'name="ui" value="classic"' in preview
    assert 'name="ui" value="notebook"' in preview
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
