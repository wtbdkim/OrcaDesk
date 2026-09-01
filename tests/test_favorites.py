"""Unit tests for the viewer-favorites store (orcamgr.favorites).

Pure file I/O — point the store at a temp favorites.json via monkeypatch and
exercise toggle/get/persistence the way the Bridge slots do.
"""

import orcamgr.favorites as fav


def _use_tmp(monkeypatch, tmp_path):
    f = tmp_path / "favorites.json"
    monkeypatch.setattr(fav, "_fav_file", lambda: f)
    return f


def test_toggle_adds_and_persists(monkeypatch, tmp_path):
    f = _use_tmp(monkeypatch, tmp_path)
    labels, saved = fav.toggle("calc:asp", "asp_c5", True)
    assert saved is True     # the star lighting up IS the confirmation
    assert labels == ["asp_c5"]
    assert f.exists()
    # a fresh read (simulating a restart) sees the star
    assert fav.get("calc:asp") == ["asp_c5"]


def test_toggle_off_removes_and_prunes_empty_source(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    fav.toggle("calc:asp", "asp_c5", True)
    fav.toggle("calc:asp", "asp_c5", False)
    assert fav.get("calc:asp") == []
    # the now-empty source key is pruned from the file
    assert "calc:asp" not in fav.load_all()


def test_toggle_is_idempotent(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    fav.toggle("folder:/x", "m1", True)
    fav.toggle("folder:/x", "m1", True)      # star again -> still one
    assert fav.get("folder:/x") == ["m1"]
    fav.toggle("folder:/x", "m1", False)
    fav.toggle("folder:/x", "m1", False)     # unstar again -> no error
    assert fav.get("folder:/x") == []


def test_sources_are_independent(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    fav.toggle("calc:a", "a_c1", True)
    fav.toggle("folder:/b", "b1", True)
    assert fav.get("calc:a") == ["a_c1"]
    assert fav.get("folder:/b") == ["b1"]


def test_order_is_insertion_order(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    fav.toggle("s", "c3", True)
    fav.toggle("s", "c1", True)
    fav.toggle("s", "c2", True)
    assert fav.get("s") == ["c3", "c1", "c2"]


def test_corrupt_file_degrades_to_empty(monkeypatch, tmp_path):
    f = _use_tmp(monkeypatch, tmp_path)
    f.write_text("{ not valid json", encoding="utf-8")
    assert fav.load_all() == {}
    assert fav.get("anything") == []


def test_non_dict_json_degrades_to_empty(monkeypatch, tmp_path):
    f = _use_tmp(monkeypatch, tmp_path)
    f.write_text("[1, 2, 3]", encoding="utf-8")
    assert fav.load_all() == {}
