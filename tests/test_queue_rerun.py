"""A set put on a DONE calculation runs it again (state/store.py rerun_calc).

P24's one explicit way back from DONE: the queue editor sends rerun_calc only
after an overwrite warning. What is pinned here:

* rerun_calc puts the set on a DONE row and returns it to PENDING (no message,
  result or output path), keeping its name, place and edges; it refuses every
  other state and a row the engine is handling, and changes nothing but the set;
* undo_rerun puts the finished row back (the same object) while the rerun has
  not started, and refuses once it has;
* a rerun row is never kept: it is left out of the overwrite conflicts (the Run
  modal and the startup resume) and dropped from any keep answers the engine
  is given; the mark survives a session round trip and an edit, never a start.

Session I/O is redirected to tmp_path (see test_store.py).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import orcamgr.state.store as store_mod
from orcamgr.core.queue import CalcState
from orcamgr.gui.bridge import Bridge
from orcamgr.state.store import (QueueOpError, QueueStore, calc_from_session_dict,
                                 calc_to_session_dict, is_rerun, make_engine_factory)

H2 = "H 0.0 0.0 0.0\nH 0.0 0.0 0.74"
OLD = "! B3LYP def2-SVP {{JOB}}\n* xyz {{CHARGE}} {{MULT}}\n{{GEOMETRY}}\n*\n"
NEW = "! r2SCAN-3c {{JOB}}\n* xyz {{CHARGE}} {{MULT}}\n{{GEOMETRY}}\n*\n"
RERUN = {"raw_text": NEW, "set_name": "r2scan-3c", "set_id": "s-r2", "is_raw": True}


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(store_mod, "user_data_root", lambda: tmp_path)
    s = QueueStore()
    s.apply_ops([
        {"op": "add_system", "system": {"id": "s1", "name": "mol",
                                        "inputs": [{"label": "start", "xyz": H2}]}},
        {"op": "add_calc", "calc": {"name": "mol_Opt", "kind": "opt", "is_raw": True,
                                    "raw_text": OLD, "set_name": "b3lyp", "xyz": H2,
                                    "system": "s1", "node": "n1", "col": 1}},
        {"op": "add_calc", "calc": {"name": "mol_Freq", "kind": "freq", "is_raw": True,
                                    "raw_text": OLD, "geometry_source": "reference",
                                    "ref_name": "mol_Opt", "after": "mol_Opt",
                                    "system": "s1", "node": "n2", "col": 2}},
    ])
    c = s.get("mol_Opt")
    c.state, c.message, c.output_path = CalcState.DONE, "converged", "x/mol_Opt.out"
    return s


def test_a_set_on_a_done_calc_runs_it_again(store):
    done = store.get("mol_Opt")
    store.apply_ops([{"op": "rerun_calc", "name": "mol_Opt", "calc": RERUN}])
    c = store.get("mol_Opt")
    assert c is not done
    assert (c.state, c.message, c.output_path, c.result) == (CalcState.PENDING, "", "", None)
    assert (c.raw_text, c.set_name, c.kind) == (NEW, "r2scan-3c", "opt")
    assert (c.system, c.node, c.col, c.xyz) == ("s1", "n1", 1, H2)
    assert c.rerun and is_rerun(c) and store.rerun_names() == {"mol_Opt"}
    # a pending child waits for it, and reads its new geometry when it runs
    assert store.get("mol_Freq").ref_name == "mol_Opt"


def test_a_guided_done_calc_becoming_a_level_block_drops_its_old_config(store):
    store.apply_ops([{"op": "add_calc", "calc": {
        "name": "mol_G", "kind": "opt", "xyz": H2, "system": "s1", "node": "n3",
        "config": {"method": "PBE0", "basis": "def2-SVP", "max_iter": 60}}}])
    store.get("mol_G").state = CalcState.DONE
    store.apply_ops([{"op": "rerun_calc", "name": "mol_G", "calc": RERUN}])
    c = store.get("mol_G")
    assert c.is_raw and c.raw_text == NEW and c.config.max_iter != 60


@pytest.mark.parametrize("state", [CalcState.PENDING, CalcState.FAILED,
                                   CalcState.RUNNING, CalcState.CANCELLED,
                                   CalcState.BLOCKED])
def test_only_a_done_calc_runs_again(store, state):
    store.get("mol_Opt").state = state
    with pytest.raises(QueueOpError, match="only a done"):
        store.apply_ops([{"op": "rerun_calc", "name": "mol_Opt", "calc": RERUN}])
    assert store.get("mol_Opt").state == state


def test_rerun_changes_the_set_and_nothing_else(store):
    for patch in ({"name": "other"}, {"kind": "freq"}, {"state": "pending"},
                  {"xyz": H2}, {}, None):
        with pytest.raises(QueueOpError):
            store.apply_ops([{"op": "rerun_calc", "name": "mol_Opt", "calc": patch}])
    assert store.get("mol_Opt").state == CalcState.DONE


def test_a_row_the_engine_is_handling_is_never_touched(store):
    store.set_running(True)
    store._engine = SimpleNamespace(active_names={"mol_Opt"})
    with pytest.raises(QueueOpError, match="handling"):
        store.apply_ops([{"op": "rerun_calc", "name": "mol_Opt", "calc": RERUN}])
    store._engine = None
    store.set_running(False)


def test_undo_puts_the_finished_row_back_until_it_starts(store):
    done = store.get("mol_Opt")
    store.apply_ops([{"op": "rerun_calc", "name": "mol_Opt", "calc": RERUN}])
    # an edit in between (a Type change, a gate) keeps it the same rerun
    store.apply_ops([{"op": "update_calc", "name": "mol_Opt", "calc": {"gate": "approval"}}])
    assert is_rerun(store.get("mol_Opt"))
    store.apply_ops([{"op": "update_calc", "name": "mol_Opt", "calc": {"gate": "auto"}},
                     {"op": "undo_rerun", "name": "mol_Opt"}])
    back = store.get("mol_Opt")
    assert back is done and back.state == CalcState.DONE and back.message == "converged"
    assert store.rerun_names() == set()


def test_undo_is_refused_once_the_rerun_has_started(store):
    store.apply_ops([{"op": "rerun_calc", "name": "mol_Opt", "calc": RERUN}])
    store.get("mol_Opt").state = CalcState.RUNNING
    with pytest.raises(QueueOpError, match="started since"):
        store.apply_ops([{"op": "undo_rerun", "name": "mol_Opt"}])
    # started and then cancelled: a cancelled row re-runs as an ordinary one
    store.get("mol_Opt").state = CalcState.CANCELLED
    assert not is_rerun(store.get("mol_Opt"))
    store.apply_ops([{"op": "update_calc", "name": "mol_Opt", "calc": {"gate": "approval"}}])
    assert not store.get("mol_Opt").rerun


def test_a_removed_and_readded_row_is_not_the_rerun(store):
    store.apply_ops([{"op": "rerun_calc", "name": "mol_Opt", "calc": RERUN}])
    store.apply_ops([{"op": "remove_calc", "name": "mol_Freq"},
                     {"op": "remove_calc", "name": "mol_Opt"},
                     {"op": "add_calc", "calc": {"name": "mol_Opt", "kind": "opt",
                                                 "xyz": H2, "rerun": True}}])
    assert not store.get("mol_Opt").rerun          # no client can set the mark
    with pytest.raises(QueueOpError):
        store.apply_ops([{"op": "undo_rerun", "name": "mol_Opt"}])


def test_the_mark_survives_a_session_round_trip_but_not_the_undo_stash(store):
    store.apply_ops([{"op": "rerun_calc", "name": "mol_Opt", "calc": RERUN}])
    d = json.loads(json.dumps(calc_to_session_dict(store.get("mol_Opt"))))
    assert d["rerun"] is True and calc_from_session_dict(d).rerun is True
    again = QueueStore()
    again.load_session()
    assert again.rerun_names() == {"mol_Opt"}
    with pytest.raises(QueueOpError, match="restarted"):
        again.apply_ops([{"op": "undo_rerun", "name": "mol_Opt"}])


def test_a_rerun_row_is_never_offered_or_given_keep_existing(store, tmp_path):
    ws = tmp_path / "ws"
    for name in ("mol_Opt", "mol_Freq"):
        (ws / name).mkdir(parents=True)
        (ws / name / f"{name}.out").write_text("old", encoding="utf-8")
    stub = SimpleNamespace(store=store, settings=SimpleNamespace(workspace_root=str(ws)))
    conflicts = lambda: Bridge._overwrite_conflicts(stub)      # noqa: E731
    assert conflicts() == {"mol_Freq"}                         # DONE is not asked about
    store.apply_ops([{"op": "rerun_calc", "name": "mol_Opt", "calc": RERUN}])
    assert conflicts() == {"mol_Freq"}                         # nor is its rerun
    engine = make_engine_factory(store, "orca", str(ws), {"mol_Opt", "mol_Freq"})()
    assert engine._skip_names == {"mol_Freq"}                  # a keep answer is dropped
    store.get("mol_Opt").state = CalcState.DONE                # once it has run: an ordinary row
    assert store.rerun_names() == set()
