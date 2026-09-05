"""
Tests for orcamgr/config.py — Settings persistence and ORCA path validity.

Contracts under test come from PRINCIPLES.md:
  * P32 — persistence never crashes the app: corrupt settings JSON degrades
    to defaults; saves are best-effort and atomic (tmp + os.replace).
  * P33 — settings accept only allowlisted (dataclass) keys on load and save.
  * legacy `mlip_python` migrates to `mlip_envs` with a deterministic id, so
    re-loading before the first save yields the same id instead of churning.

No conftest.py: fixtures live here. All file I/O is redirected to tmp_path by
monkeypatching the names bound inside orcamgr.config (config_file /
default_workspace_root / autodetect_orca) — patching orcamgr.paths would not
reach them because config.py imports them at module top.
"""

from __future__ import annotations

import json
import os
from dataclasses import fields as dataclass_fields

import pytest

import orcamgr.config as config_mod
from orcamgr.config import Settings


@pytest.fixture
def settings_file(monkeypatch, tmp_path):
    """Isolate Settings from the real %APPDATA% and from the machine's ORCA
    install: config file, workspace default, and autodetection all point at
    tmp_path / deterministic stubs. Returns the settings.json path."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(config_mod, "config_file", lambda: path)
    monkeypatch.setattr(config_mod, "default_workspace_root",
                        lambda: tmp_path / "workspaces")
    monkeypatch.setattr(config_mod, "autodetect_orca", lambda: "")
    return path


def write_settings(path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


# ---- 1. Settings.load robustness (P32/P33) --------------------------------

def test_load_with_missing_file_yields_defaults(settings_file, tmp_path):
    s = Settings.load()
    assert s.orca_path == ""          # autodetect stubbed to nothing
    assert s.default_nprocs == 6
    assert s.default_maxcore_mb == 2400
    assert s.theme == "dark"
    assert s.theme_variant == "shadcn"      # Liquid-Glass is opt-in
    assert s.glass_level == "moderate"
    assert s.wallpaper == "aurora"
    assert s.eta_mode == "conservative"
    assert s.geo_graph_mode == "all5"
    assert s.build_mode == "beginner"
    assert s.mlip_envs == []
    # empty workspace_root is filled from the (stubbed) default
    assert s.workspace_root == str(tmp_path / "workspaces")


def test_load_with_corrupt_json_falls_back_to_defaults(settings_file):
    settings_file.write_text("{this is not json", encoding="utf-8")
    s = Settings.load()  # must not raise (P32)
    assert s.theme == "dark"
    assert s.default_nprocs == 6
    assert s.mlip_envs == []


def test_load_filters_unknown_keys(settings_file):
    write_settings(settings_file, {
        "theme": "light",
        "default_nprocs": 12,
        "key_from_the_future": True,
        "__class__": "evil",
    })
    s = Settings.load()  # unknown keys must be dropped, not rejected (P33)
    assert s.theme == "light"
    assert s.default_nprocs == 12
    assert not hasattr(s, "key_from_the_future")


def test_load_applies_only_dataclass_fields(settings_file):
    write_settings(settings_file, {"orca_path": "C:/orca/orca.exe",
                                   "build_mode": "expert"})
    s = Settings.load()
    assert s.orca_path == "C:/orca/orca.exe"
    assert s.build_mode == "expert"
    # untouched fields keep their dataclass defaults
    assert s.eta_mode == "conservative"


# ---- 2. Settings.save atomicity / best-effort (P32) -----------------------

def test_save_writes_valid_json_with_no_tmp_leftover(settings_file, tmp_path):
    s = Settings(orca_path="C:/orca/orca.exe", theme="light",
                 workspace_root=str(tmp_path / "ws"))
    s.save()
    assert settings_file.exists()
    data = json.loads(settings_file.read_text(encoding="utf-8"))
    assert data["orca_path"] == "C:/orca/orca.exe"
    assert data["theme"] == "light"
    # the atomic-replace temp file must not survive a successful save
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_save_emits_only_allowlisted_keys(settings_file, tmp_path):
    s = Settings(workspace_root=str(tmp_path / "ws"))
    s.save()
    data = json.loads(settings_file.read_text(encoding="utf-8"))
    allowed = {f.name for f in dataclass_fields(Settings)}
    assert set(data.keys()) <= allowed


def test_save_load_roundtrip_preserves_values(settings_file, tmp_path):
    s = Settings(orca_path="", theme="light", default_nprocs=16,
                 default_maxcore_mb=8000, eta_mode="eager",
                 build_mode="mlip", workspace_root=str(tmp_path / "ws"),
                 mlip_envs=[{"id": "ab12cd34", "name": "MACE",
                             "python": "C:/envs/mace/python.exe"}])
    s.save()
    loaded = Settings.load()
    assert loaded.theme == "light"
    assert loaded.default_nprocs == 16
    assert loaded.default_maxcore_mb == 8000
    assert loaded.eta_mode == "eager"
    assert loaded.build_mode == "mlip"
    assert loaded.workspace_root == str(tmp_path / "ws")
    assert loaded.mlip_envs == s.mlip_envs


def test_appearance_fields_roundtrip(settings_file, tmp_path):
    """Liquid-Glass appearance settings persist and reload verbatim."""
    s = Settings(theme="light", theme_variant="liquidglass",
                 glass_level="vivid", wallpaper="sunset",
                 workspace_root=str(tmp_path / "ws"))
    s.save()
    loaded = Settings.load()
    assert loaded.theme_variant == "liquidglass"
    assert loaded.glass_level == "vivid"
    assert loaded.wallpaper == "sunset"


def test_save_failure_does_not_raise(monkeypatch, tmp_path):
    # best-effort save (P32): block the write by making the parent a FILE,
    # so both the tmp write and the replace must fail with OSError
    blocker = tmp_path / "blocker"
    blocker.write_text("I am a file, not a directory", encoding="utf-8")
    monkeypatch.setattr(config_mod, "config_file",
                        lambda: blocker / "settings.json")
    assert Settings().save() is False   # swallowed, but reported
    assert blocker.read_text(encoding="utf-8").startswith("I am a file")


def test_save_reports_success(settings_file):
    # The caller cannot tell "written" from "silently lost" without this:
    # Bridge.save_settings answered "Saved." to a write that never happened,
    # and every setting was gone at the next launch.
    assert Settings(theme="light").save() is True
    assert '"theme": "light"' in settings_file.read_text(encoding="utf-8")


def test_failed_save_leaves_no_tmp_file_behind(monkeypatch, tmp_path):
    # the tmp write can succeed and only the replace fail (the file is locked,
    # read-only, held by a virus scanner) — the intended content must not be
    # left lying beside settings.json under a .tmp name
    target = tmp_path / "settings.json"
    monkeypatch.setattr(config_mod, "config_file", lambda: target)

    def boom(*_a, **_kw):
        raise OSError("locked")

    monkeypatch.setattr(config_mod.os, "replace", boom)

    assert Settings().save() is False
    assert list(tmp_path.iterdir()) == []


# ---- 3. legacy mlip_python -> mlip_envs migration --------------------------

def test_legacy_mlip_python_migrates_to_single_env(settings_file):
    write_settings(settings_file,
                   {"mlip_python": "C:/envs/mace/python.exe"})
    s = Settings.load()
    assert len(s.mlip_envs) == 1
    env = s.mlip_envs[0]
    assert env["python"] == "C:/envs/mace/python.exe"
    assert env["name"] == "MLIP"
    assert isinstance(env["id"], str) and len(env["id"]) == 8
    int(env["id"], 16)  # id is hex — derived from a hash, not random


def test_migration_id_is_deterministic_across_reloads(settings_file):
    # the app may reload before the first save() lands — re-migration must
    # yield the SAME id for the same interpreter path, not churn it
    write_settings(settings_file,
                   {"mlip_python": "C:/envs/mace/python.exe"})
    first = Settings.load().mlip_envs[0]["id"]
    second = Settings.load().mlip_envs[0]["id"]
    assert first == second


def test_migration_ids_differ_for_different_paths(settings_file):
    write_settings(settings_file, {"mlip_python": "C:/envs/a/python.exe"})
    id_a = Settings.load().mlip_envs[0]["id"]
    write_settings(settings_file, {"mlip_python": "C:/envs/b/python.exe"})
    id_b = Settings.load().mlip_envs[0]["id"]
    assert id_a != id_b


def test_migration_skipped_when_env_list_already_present(settings_file):
    existing = [{"id": "deadbeef", "name": "SevenNet",
                 "python": "C:/envs/7net/python.exe"}]
    write_settings(settings_file, {"mlip_python": "C:/envs/old/python.exe",
                                   "mlip_envs": existing})
    s = Settings.load()
    assert s.mlip_envs == existing  # the registered list wins over legacy


def test_no_migration_without_legacy_key(settings_file):
    write_settings(settings_file, {"theme": "dark"})
    assert Settings.load().mlip_envs == []


# ---- 4. orca_is_valid ------------------------------------------------------

def test_orca_is_valid_requires_existing_file(tmp_path):
    assert Settings(orca_path="").orca_is_valid() is False
    assert Settings(
        orca_path=str(tmp_path / "nope" / "orca.exe")).orca_is_valid() is False
    exe = tmp_path / "orca.exe"
    exe.write_bytes(b"fake orca binary")
    # The POSIX branch of orca_is_valid asks os.access(X_OK), so a file written
    # without the bit is not "something that could BE the executable" there --
    # this test asserted the Windows answer on every platform.
    exe.chmod(0o755)
    assert Settings(orca_path=str(exe)).orca_is_valid() is True


def test_orca_is_valid_rejects_a_directory(tmp_path):
    # The ORCA install FOLDER is the natural thing to pick in a browser, and
    # exists() said yes to it: Settings showed ORCA as valid, the run pre-flight
    # passed, and the calculation died at launch with a WinError — which locks
    # it (P24).
    d = tmp_path / "ORCA_6.1.1"
    d.mkdir()
    assert Settings(orca_path=str(d)).orca_is_valid() is False


@pytest.mark.skipif(os.name != "nt", reason="executable extensions are a Windows rule")
def test_orca_is_valid_rejects_a_non_executable_file(tmp_path):
    doc = tmp_path / "EULA_ORCA.rtf"
    doc.write_text("not a program", encoding="utf-8")
    assert Settings(orca_path=str(doc)).orca_is_valid() is False


def test_infinity_in_settings_file_does_not_crash_the_launch(settings_file):
    """`Infinity` and `NaN` are valid JSON to Python's decoder, and
    int(float("inf")) raises OverflowError — which the coercion guard did not
    catch, so it propagated out of Settings.load -> Bridge.__init__ and the app
    could not start at all until the file was edited by hand (P32)."""
    settings_file.write_text(
        '{"max_total_ram_mb": Infinity, "max_total_cores": -Infinity, '
        '"default_nprocs": NaN}', encoding="utf-8")

    s = Settings.load()   # must not raise

    assert s.max_total_ram_mb == 0
    assert s.max_total_cores == 0
    assert s.default_nprocs == 6


def test_mlip_env_lookup_by_id():
    envs = [{"id": "aa11bb22", "name": "MACE", "python": "p1"},
            {"id": "cc33dd44", "name": "SevenNet", "python": "p2"}]
    s = Settings(mlip_envs=envs)
    assert s.mlip_env("cc33dd44") == envs[1]
    assert s.mlip_env("nope") is None


def test_load_with_valid_json_non_object_degrades_to_defaults(tmp_path, monkeypatch):
    """P32: valid JSON that is not an object (a list / string / number) must
    degrade to defaults exactly like corrupt JSON — not crash startup.
    Regression: data.items() used to raise an uncaught AttributeError."""
    for payload in ('[1, 2, 3]', '"just a string"', '123'):
        cfg = tmp_path / f"settings-{hash(payload) & 0xffff}.json"
        cfg.write_text(payload, encoding="utf-8")
        monkeypatch.setattr(config_mod, "config_file", lambda p=cfg: p)
        monkeypatch.setattr(config_mod, "autodetect_orca", lambda: "")
        monkeypatch.setattr(config_mod, "default_workspace_root",
                            lambda: tmp_path / "ws")
        s = Settings.load()
        assert s.build_mode == "beginner"  # a default, not a crash


# ---- wrong-typed scalar / env entries in settings.json (P32) ---------------

def test_load_coerces_wrong_typed_scalar_fields_to_defaults(settings_file):
    # a non-string orca_path would persist itself and then crash every
    # get_settings/run via Path(orca_path) — every session until hand-fixed
    write_settings(settings_file, {"orca_path": 123, "workspace_root": ["x"],
                                   "theme": None, "crest_distro": 7})
    s = Settings.load()
    assert isinstance(s.orca_path, str)
    assert isinstance(s.workspace_root, str)
    assert isinstance(s.theme, str)
    assert isinstance(s.crest_distro, str)
    s.orca_is_valid()          # must not raise


def test_load_drops_mlip_env_entries_without_a_string_id(settings_file):
    # the Bridge hard-indexes env["id"] at startup: an id-less dict entry was
    # a KeyError crash loop on every launch
    write_settings(settings_file, {"mlip_envs": [
        {},                                        # no keys at all
        {"python": "C:/x/python.exe"},             # missing id
        {"id": 3, "python": "C:/x/python.exe"},    # wrong-typed id
        {"id": "ok1", "python": 5},                # wrong-typed python
        {"id": "ok2", "name": "MACE", "python": "C:/y/python.exe"},
    ]})
    s = Settings.load()
    assert [e["id"] for e in s.mlip_envs] == ["ok2"]


def test_int_fields_survive_a_corrupted_settings_file(settings_file):
    # P32: a hand-edited/corrupted settings.json must degrade to defaults, not
    # crash the app. The parallel-run budgets ride into ResourceBudget.resolved(),
    # which compares them with `>` -- a string there raises TypeError out of a
    # pyqtSlot, and errors must be data, never exceptions across that boundary.
    import json

    settings_file.write_text(json.dumps({
        "max_concurrent_jobs": "4",      # string that happens to parse
        "max_total_cores": "nonsense",   # string that does not
        "default_nprocs": None,
    }), encoding="utf-8")

    s = Settings.load()

    assert s.max_concurrent_jobs == 4          # coerced
    assert s.max_total_cores == 0              # fell back to the default
    assert s.default_nprocs == 6
    # and the budget it feeds can actually be resolved
    from orcamgr.core.resources import ResourceBudget
    assert ResourceBudget.from_settings(s).resolved().cores > 0


# ---- ORCA discovery must not pick a same-named stranger --------------------

def _fake_orca(dir_path, *, with_tools: bool):
    """An executable named `orca`, optionally with ORCA's helper tools beside
    it — the only thing that separates an install from a namesake."""
    dir_path.mkdir(parents=True, exist_ok=True)
    exe = dir_path / "orca"
    exe.write_bytes(b"#!/bin/sh\n")
    exe.chmod(0o755)
    if with_tools:
        for tool in ("orca_2mkl", "orca_plot"):
            t = dir_path / tool
            t.write_bytes(b"#!/bin/sh\n")
            t.chmod(0o755)
    return exe


def test_lone_executable_named_orca_is_not_an_install(tmp_path):
    # GNOME's accessibility screen reader is also called `orca` and is on PATH
    # by default on every Ubuntu desktop. It is a genuine executable file, so
    # is_file() + X_OK — everything orca_is_valid can see — says yes to it.
    stranger = _fake_orca(tmp_path / "usr_bin", with_tools=False)
    assert config_mod._looks_like_orca(stranger) is False


def test_install_with_its_helper_tools_is_recognized(tmp_path):
    real = _fake_orca(tmp_path / "orca_6_1_1", with_tools=True)
    assert config_mod._looks_like_orca(real) is True


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privilege on Windows")
def test_recognition_follows_a_symlink_into_the_install(tmp_path):
    # Linking the driver into ~/.local/bin is a normal way to put ORCA on PATH;
    # the helper tools are beside the TARGET, never beside the link (P4).
    real = _fake_orca(tmp_path / "orca_6_1_1", with_tools=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    link = bindir / "orca"
    link.symlink_to(real)
    assert config_mod._looks_like_orca(link) is True


def test_autodetect_skips_a_stranger_found_on_path(tmp_path, monkeypatch):
    """The regression: which("orca") hit the screen reader, autodetect returned
    it, and it was persisted as orca_path — Settings then showed a healthy ORCA
    while every calculation failed."""
    stranger = _fake_orca(tmp_path / "usr_bin", with_tools=False)
    monkeypatch.setattr(config_mod.shutil, "which", lambda _exe: str(stranger))
    found = [str(c) for c in config_mod._candidate_orca_paths()]
    assert str(stranger) not in found


def test_autodetect_returns_a_real_install_found_on_path(tmp_path, monkeypatch):
    real = _fake_orca(tmp_path / "orca_6_1_1", with_tools=True)
    monkeypatch.setattr(config_mod.shutil, "which", lambda _exe: str(real))
    assert config_mod.autodetect_orca() == str(real)


def test_a_hand_picked_path_is_still_the_users_call(tmp_path):
    """Discovery is strict because a wrong guess is silent. An explicit choice
    is not second-guessed, so a trimmed or unusual install stays selectable
    rather than becoming unconfigurable."""
    lone = _fake_orca(tmp_path / "custom", with_tools=False)
    assert config_mod._looks_like_orca(lone) is False
    assert Settings(orca_path=str(lone)).orca_is_valid() is True
