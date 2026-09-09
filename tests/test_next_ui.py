"""The notebook front-end (Settings -> Interface) and its ui_variant plumbing.

web/index_next.html is generated from web/index.html so the preview cannot
silently miss a field added to the classic UI. These tests pin the three ways
that arrangement can rot:

  * the generated file drifting from its source (someone hand-edits one of them)
  * an id the shared JS reaches for going missing from the preview, which is a
    blank pane at runtime and nothing at all at import time
  * the setting itself losing a leg — the dataclass field, the payload the UI
    reads, or the window's choice of which file to load
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
sys.path.insert(0, str(ROOT))

from orcamgr.config import Settings  # noqa: E402


def _read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def _ids(html: str) -> set[str]:
    return set(re.findall(r'\bid="([^"]+)"', html))


# ---------------------------------------------------------------- generation

def test_generated_preview_is_up_to_date():
    """web/index_next.html matches what tools/build_next_ui.py produces now."""
    sys.path.insert(0, str(ROOT / "tools"))
    import build_next_ui                      # noqa: PLC0415

    assert build_next_ui.build() == _read("index_next.html"), (
        "web/index_next.html is stale — run: python tools/build_next_ui.py"
    )


def test_preview_carries_every_classic_id():
    """Every id in the classic UI survives into the preview.

    The whole reason the notebook layout can reuse app.js and the renderers is
    that it is the same document; a dropped id is a pane that renders nothing.
    """
    missing = _ids(_read("index.html")) - _ids(_read("index_next.html"))
    assert not missing, f"ids lost in the notebook preview: {sorted(missing)}"


def test_preview_ids_are_unique():
    html = _read("index_next.html")
    found = re.findall(r'\bid="([^"]+)"', html)
    dupes = sorted({i for i in found if found.count(i) > 1})
    assert not dupes, f"duplicate ids in index_next.html: {dupes}"


def test_preview_loads_both_stylesheets_and_the_adapter_after_app():
    """next.css layers over style.css, and next.js wraps an app.js that exists."""
    html = _read("index_next.html")
    assert 'href="style.css"' in html and 'href="next.css"' in html
    assert html.index('href="style.css"') < html.index('href="next.css"')
    assert html.index('src="app.js"') < html.index('src="next.js"')
    # the shell must start on a real view, or every .panel stays hidden
    assert 'class="app" data-view="jobs" data-stream="build"' in html


def test_classic_ui_does_not_load_the_preview_layer():
    """The shipping UI is untouched by the preview's stylesheet and adapter."""
    html = _read("index.html")
    assert "next.css" not in html and "next.js" not in html


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


def test_settings_card_offers_both_front_ends():
    """The way back out of the preview is in the preview, at the bottom."""
    for name in ("index.html", "index_next.html"):
        html = _read(name)
        assert 'name="ui-variant" value="classic"' in html, name
        assert 'name="ui-variant" value="notebook"' in html, name
        # it is the last card of Settings, per the request that it live there
        assert html.index('name="ui-variant"') > html.index('id="about-body"'), name


def test_bridge_reports_and_accepts_ui_variant():
    """The payload the front-end reads carries the field, and save() takes it."""
    src = (ROOT / "orcamgr" / "gui" / "bridge.py").read_text(encoding="utf-8")
    assert "ui_variant=self.settings.ui_variant" in src
    assert '"ui_variant" in data' in src
    assert 'def reload_ui' in src
    schemas = (ROOT / "orcamgr" / "state" / "schemas.py").read_text(encoding="utf-8")
    assert "ui_variant: str" in schemas


def test_window_picks_the_front_end_from_the_setting():
    src = (ROOT / "orcamgr" / "gui" / "window.py").read_text(encoding="utf-8")
    assert 'ui_variant == "notebook"' in src
    assert '"index_next.html"' in src
    # and never loads a hard-coded index.html behind the setting's back
    assert 'web_dir() / "index.html"\n        self.view.load' not in src
