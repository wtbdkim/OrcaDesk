"""Unit tests for "what result does this folder hold" (orcamgr.resultfolder).

Two callers, one rule: the Results tab's workspace scan (strict -- ORCAdesk's
own folder convention, nothing else) and its *Open folder…* button (the
convention first, then the .out files the folder actually holds). Both
judgments live in one module so they cannot drift (P4).
"""

import os
import time

from orcamgr.resultfolder import find_result, result_artifact


def _touch(path, text="x", mtime=None):
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


# --- the convention (the scan's rule) ------------------------------------------

def test_an_orca_run_is_its_out_file(tmp_path):
    run = tmp_path / "water"; run.mkdir()
    out = _touch(run / "water.out")
    assert result_artifact(run) == (out, "orca")


def test_an_mlip_result_wins_over_a_stray_out(tmp_path):
    run = tmp_path / "relax"; run.mkdir()
    _touch(run / "relax.out")
    mlip = _touch(run / "relax.mlip.json")
    assert result_artifact(run) == (mlip, "mlip")


def test_crest_siblings_make_it_a_conformer_search(tmp_path):
    run = tmp_path / "search"; run.mkdir()
    out = _touch(run / "search.out")
    _touch(run / "crest_best.xyz")
    assert result_artifact(run) == (out, "crest")


def test_a_folder_with_no_result_is_not_a_result(tmp_path):
    run = tmp_path / "empty"; run.mkdir()
    _touch(run / "notes.txt")
    _touch(run / "other.out")        # not named after the folder: not the convention
    assert result_artifact(run) is None


# --- the button's rule ------------------------------------------------------------

def test_find_result_takes_the_convention_first(tmp_path):
    run = tmp_path / "water"; run.mkdir()
    _touch(run / "water.out")
    _touch(run / "older_attempt.out", mtime=time.time() + 100)   # newer, but not the rule
    r = find_result(run)
    assert r["ok"] and r["path"].endswith("water.out") and r["kind"] == "orca"


def test_a_hand_made_run_opens_by_its_only_out(tmp_path):
    run = tmp_path / "from_the_cluster"; run.mkdir()
    out = _touch(run / "job12345.out")
    r = find_result(run)
    assert r["ok"] and r["path"] == str(out) and r["kind"] == "orca"


def test_several_outs_pick_the_newest(tmp_path):
    run = tmp_path / "many"; run.mkdir()
    now = time.time()
    _touch(run / "first.out", mtime=now - 200)
    newest = _touch(run / "second.out", mtime=now - 10)
    _touch(run / "third.OUT", mtime=now - 100)         # suffix matched case-insensitively
    r = find_result(run)
    assert r["ok"] and r["path"] == str(newest)


def test_crest_siblings_mark_a_hand_made_run_too(tmp_path):
    run = tmp_path / "conf"; run.mkdir()
    _touch(run / "anything.out")
    _touch(run / "crest_conformers.xyz")
    assert find_result(run)["kind"] == "crest"


def test_nothing_to_read_is_a_refusal_that_says_what_would_work(tmp_path):
    run = tmp_path / "structures"; run.mkdir()
    _touch(run / "a.xyz")
    r = find_result(run)
    assert not r["ok"]
    assert "structures" in r["error"] and ".xyz" in r["error"] and ".out" in r["error"]


def test_a_missing_folder_and_a_blank_choice_are_refusals(tmp_path):
    assert not find_result(tmp_path / "gone")["ok"]
    assert not find_result("")["ok"]
    assert not find_result("   ")["ok"]


def test_a_file_is_not_a_folder(tmp_path):
    f = _touch(tmp_path / "water.out")
    r = find_result(f)
    assert not r["ok"] and "not a folder" in r["error"]
