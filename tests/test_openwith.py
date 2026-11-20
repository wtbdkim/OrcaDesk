"""Unit tests for handing a file to the user's own programs (orcamgr.openwith).

The interesting half is the refusal rule, not the launch: ``os.startfile`` on a
path is double-clicking it, so an allowlist of data-file suffixes is what stands
between the front-end and running an executable. That half is pure and is tested
exhaustively here; the side-effecting half is exercised with the two launchers
replaced, so no test ever opens a window.
"""

import sys

import pytest

from orcamgr import openwith
from orcamgr.openwith import (
    OPENABLE_SUFFIXES, open_with_default, show_in_folder, why_not_openable,
)


@pytest.fixture
def launched(monkeypatch):
    """Capture what would have been launched instead of launching it."""
    calls: list = []
    monkeypatch.setattr(openwith, "_launch", lambda argv: calls.append(("argv", argv)))
    monkeypatch.setattr(openwith, "_startfile", lambda p: calls.append(("startfile", p)))
    return calls


def _file(tmp_path, name: str):
    p = tmp_path / name
    p.write_text("x", encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# the refusal rule
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "water.xyz", "water.cube", "water.out", "water.inp", "water.hess",
    "water.molden.input",              # ORCA's own name -> suffix is .input
    "water.property.txt",              # ... and this one -> .txt
    "FILE.47", "spectrum.csv", "result.json", "plot.png",
])
def test_data_files_are_openable(tmp_path, name):
    assert why_not_openable(_file(tmp_path, name)) == ""


@pytest.mark.parametrize("name", [
    "evil.exe", "evil.bat", "evil.cmd", "evil.ps1", "evil.vbs", "evil.msi",
    "evil.scr", "evil.lnk", "evil.reg", "evil.com", "evil.js", "evil.jar",
])
def test_executables_are_refused(tmp_path, name):
    # Default-deny is the point: no list of dangerous suffixes has to stay
    # correct, because anything not recognized as data is already refused.
    refusal = why_not_openable(_file(tmp_path, name))
    assert refusal and "does not open" in refusal


def test_no_executable_suffix_slipped_into_the_allowlist():
    dangerous = {".exe", ".bat", ".cmd", ".com", ".ps1", ".psm1", ".vbs", ".vbe",
                 ".js", ".jse", ".wsf", ".wsh", ".msi", ".msp", ".scr", ".lnk",
                 ".reg", ".hta", ".cpl", ".jar", ".pif", ".sh", ".py"}
    assert not (OPENABLE_SUFFIXES & dangerous)


def test_a_file_with_no_suffix_is_refused(tmp_path):
    assert "no file type" in why_not_openable(_file(tmp_path, "README"))


def test_a_missing_file_is_refused(tmp_path):
    assert "not there any more" in why_not_openable(tmp_path / "gone.xyz")


def test_an_empty_path_is_refused():
    assert why_not_openable("") == "No file to open."
    assert why_not_openable("   ") == "No file to open."


def test_a_directory_is_openable(tmp_path):
    assert why_not_openable(tmp_path) == ""


# --------------------------------------------------------------------------
# opening
# --------------------------------------------------------------------------

def test_open_hands_the_path_to_the_platform(tmp_path, launched):
    path = _file(tmp_path, "water.cube")
    assert open_with_default(path) == {"ok": True}
    assert len(launched) == 1
    kind, arg = launched[0]
    if sys.platform == "win32":
        assert kind == "startfile" and arg == str(path)
    else:
        assert kind == "argv" and arg[-1] == str(path)
        assert arg[0] in ("open", "xdg-open")


def test_open_refuses_without_launching_anything(tmp_path, launched):
    result = open_with_default(_file(tmp_path, "evil.exe"))
    assert result["ok"] is False and result["error"]
    assert launched == []                       # the refusal came first


def test_open_reports_a_launch_failure_as_data(tmp_path, monkeypatch):
    # A file type with no registered program raises OSError; a slot may not.
    def boom(*_a, **_k):
        raise OSError("no application is associated")
    monkeypatch.setattr(openwith, "_launch", boom)
    monkeypatch.setattr(openwith, "_startfile", boom)
    result = open_with_default(_file(tmp_path, "water.cube"))
    assert result["ok"] is False
    assert "no application is associated" in result["error"]


# --------------------------------------------------------------------------
# revealing
# --------------------------------------------------------------------------

def test_show_selects_a_file_in_its_folder(tmp_path, launched):
    path = _file(tmp_path, "water.out")
    assert show_in_folder(path) == {"ok": True}
    kind, arg = launched[0]
    if sys.platform == "win32":
        # explorer wants the path glued to the switch, as one argv element.
        assert kind == "argv" and arg[0] == "explorer"
        assert arg[1] == f"/select,{path}"
    else:
        assert kind == "argv"


def test_show_opens_a_folder_directly(tmp_path, launched):
    assert show_in_folder(tmp_path) == {"ok": True}
    kind, arg = launched[0]
    if sys.platform == "win32":
        assert kind == "startfile" and arg == str(tmp_path)


def test_show_accepts_any_suffix_because_revealing_never_executes(tmp_path, launched):
    # The allowlist guards opening, not revealing -- the file manager selects.
    assert show_in_folder(_file(tmp_path, "evil.exe")) == {"ok": True}
    assert launched


def test_show_falls_back_to_the_folder_when_the_file_is_gone(tmp_path, launched):
    # "Where was it written?" is still answerable after the file disappeared.
    assert show_in_folder(tmp_path / "gone.cube") == {"ok": True}
    kind, arg = launched[0]
    target = arg if kind == "startfile" else arg[-1]
    assert str(tmp_path) in str(target)


def test_show_refuses_when_neither_the_file_nor_its_folder_exists(tmp_path, launched):
    result = show_in_folder(tmp_path / "nowhere" / "gone.cube")
    assert result["ok"] is False and result["error"]
    assert launched == []


def test_show_refuses_an_empty_path(launched):
    assert show_in_folder("")["ok"] is False
    assert launched == []
