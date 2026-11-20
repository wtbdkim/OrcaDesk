"""Saved workflows (state/workflows.py): the queue editor's library of step
chains, kept by name in workflows.json.

* a workflow validates its shape (a name, a list of step objects with an id,
  a node and a known kind) and its size -- never the chemistry, which the
  store judges when a step is replayed as add_calc;
* save adds or replaces by id, refuses a name another workflow has (case
  aside), and keeps the save order;
* a missing or unreadable file reads as none, and a bad entry is dropped on
  read without costing the others (P32).
"""

from __future__ import annotations

import json

import pytest

from orcamgr.state import workflows as wfm


def _step(sid="c0", kind="opt", **extra):
    d = {"id": sid, "node": "n0", "kind": kind, "suffix": "opt", "tail": "",
         "is_raw": True, "raw_text": "! B3LYP {{JOB}}\n* xyz 0 1\n{{GEOMETRY}}\n*\n",
         "set_name": "B3LYP", "config": {}, "after": "", "ref": "", "ref_point": "",
         "gate": "auto", "auto_pass": True, "col": 0, "row": 0}
    d.update(extra)
    return d


def _wf(name="TS chain", **extra):
    d = {"name": name, "v": 1, "from": "mol",
         "steps": [_step("c0", "ts_opt"), _step("c1", "ts_freq", node="n1", after="c0", ref="c0")]}
    d.update(extra)
    return d


def test_a_workflow_is_saved_with_a_minted_id_and_read_back(tmp_path):
    saved = wfm.save_workflow(_wf(), tmp_path)
    assert saved["id"].startswith("wf-") and saved["name"] == "TS chain"
    assert saved["from"] == "mol" and saved["v"] == 1
    assert wfm.load_workflows(tmp_path) == [saved]
    data = json.loads((tmp_path / wfm.FILE_NAME).read_text(encoding="utf-8"))
    assert data["schema"] == wfm.SCHEMA and data["workflows"] == [saved]


def test_saving_the_same_id_replaces_it_in_place(tmp_path):
    a = wfm.save_workflow(_wf("A"), tmp_path)
    b = wfm.save_workflow(_wf("B"), tmp_path)
    wfm.save_workflow(dict(_wf("A2"), id=a["id"], steps=[_step()]), tmp_path)
    got = wfm.load_workflows(tmp_path)
    assert [w["name"] for w in got] == ["A2", "B"] and got[1]["id"] == b["id"]
    assert len(got[0]["steps"]) == 1


def test_a_name_another_workflow_has_is_refused(tmp_path):
    wfm.save_workflow(_wf("TS chain"), tmp_path)
    with pytest.raises(ValueError, match="already a workflow named"):
        wfm.save_workflow(_wf("ts CHAIN"), tmp_path)


@pytest.mark.parametrize("bad, words", [
    ({"name": "x", "steps": []}, "at least one step"),
    ({"name": "", "steps": [_step()]}, "needs a name"),
    ({"name": "x", "steps": ["opt"]}, "not an object"),
    ({"name": "x", "steps": [_step(kind="dance")]}, "unknown kind"),
    ({"name": "x", "steps": [_step("c0"), _step("c0", node="n1")]}, "no id of its own"),
    ({"name": "x", "steps": [_step(node="")]}, "no node"),
    ({"name": "x", "id": "a b", "steps": [_step()]}, "workflow id"),
    ({"name": "x" * 121, "steps": [_step()]}, "at most 120"),
])
def test_what_a_replay_needs_is_checked(tmp_path, bad, words):
    with pytest.raises(ValueError, match=words):
        wfm.save_workflow(bad, tmp_path)
    assert wfm.load_workflows(tmp_path) == []


def test_an_oversized_workflow_is_refused(tmp_path):
    big = [_step(f"c{i}", raw_text="x" * 5000, node=f"n{i}") for i in range(150)]
    with pytest.raises(ValueError, match="too large"):
        wfm.save_workflow({"name": "big", "steps": big}, tmp_path)


def test_delete_removes_one_and_says_when_there_was_none(tmp_path):
    a = wfm.save_workflow(_wf("A"), tmp_path)
    wfm.save_workflow(_wf("B"), tmp_path)
    assert wfm.delete_workflow(a["id"], tmp_path) is True
    assert [w["name"] for w in wfm.load_workflows(tmp_path)] == ["B"]
    assert wfm.delete_workflow(a["id"], tmp_path) is False


def test_a_missing_or_unreadable_file_reads_as_none(tmp_path):
    assert wfm.load_workflows(tmp_path) == []
    (tmp_path / wfm.FILE_NAME).write_text("{not json", encoding="utf-8")
    assert wfm.load_workflows(tmp_path) == []
    # and the next save replaces it rather than failing
    wfm.save_workflow(_wf(), tmp_path)
    assert len(wfm.load_workflows(tmp_path)) == 1


def test_a_bad_entry_is_dropped_on_read_and_the_rest_kept(tmp_path):
    good = wfm.validate_workflow(dict(_wf("good"), id="wf-1"))
    (tmp_path / wfm.FILE_NAME).write_text(json.dumps({"schema": 1, "workflows": [
        {"name": "no steps", "id": "wf-0", "steps": []}, good, dict(good)]}), encoding="utf-8")
    assert wfm.load_workflows(tmp_path) == [good]
