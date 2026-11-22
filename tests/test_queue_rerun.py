"""A set put on a DONE or FAILED calculation runs it again (state/store.py
rerun_calc).

P24's one explicit way back from DONE and FAILED: the queue editor sends
rerun_calc only after an overwrite warning. What is pinned here:

* rerun_calc puts the set on a DONE or FAILED row and returns it to PENDING (no
  message, result or output path), keeping its name, place and edges; it
  refuses every other state and a row the engine is handling, and changes
  nothing but the set;
* the rows a FAILED one held BLOCKED (downstream, transitively) go back to
  PENDING as new objects, so a running engine picks them again in the same run;
* undo_rerun puts the old row back (the same object), and the blocked rows it
  freed that are still the freed objects, while the rerun has not started, and
  refuses once it has;
* a rerun row is never kept: it is left out of the overwrite conflicts (the Run
  modal and the startup resume) and dropped from any keep answers the engine
  is given; the mark survives a session round trip and an edit, never a start.

Session I/O is redirected to tmp_path (see test_store.py).
"""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

import orcamgr.core.queue as queue_mod
import orcamgr.state.store as store_mod
from orcamgr.core.parser import ParseResult
from orcamgr.core.queue import CalcState, QueueCallbacks, QueueEngine
from orcamgr.core.resources import ResourceBudget
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


@pytest.mark.parametrize("state", [CalcState.PENDING, CalcState.RUNNING,
                                   CalcState.CANCELLED, CalcState.BLOCKED])
def test_only_a_done_or_failed_calc_runs_again(store, state):
    store.get("mol_Opt").state = state
    with pytest.raises(QueueOpError, match="only a done or failed"):
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
    for name in ("mol_Opt", "mol_Freq"):            # where system "mol" runs them
        (ws / "mol" / name).mkdir(parents=True)
        (ws / "mol" / name / f"{name}.out").write_text("old", encoding="utf-8")
    stub = SimpleNamespace(store=store, settings=SimpleNamespace(workspace_root=str(ws)))
    conflicts = lambda: Bridge._overwrite_conflicts(stub)      # noqa: E731
    assert conflicts() == {"mol_Freq"}                         # DONE is not asked about
    store.apply_ops([{"op": "rerun_calc", "name": "mol_Opt", "calc": RERUN}])
    assert conflicts() == {"mol_Freq"}                         # nor is its rerun
    engine = make_engine_factory(store, "orca", str(ws), {"mol_Opt", "mol_Freq"})()
    assert engine._skip_names == {"mol_Freq"}                  # a keep answer is dropped
    store.get("mol_Opt").state = CalcState.DONE                # once it has run: an ordinary row
    assert store.rerun_names() == set()


# ---- a FAILED calculation ----------------------------------------------------

def _fail_with_dependents(store):
    """mol_Opt FAILED; mol_Freq (after it) and mol_SP (after mol_Freq) BLOCKED
    by it; mol_X BLOCKED for its own reason, downstream of nothing."""
    store.apply_ops([
        {"op": "add_calc", "calc": {"name": "mol_SP", "kind": "sp", "is_raw": True,
                                    "raw_text": OLD, "geometry_source": "reference",
                                    "ref_name": "mol_Freq", "after": "mol_Freq",
                                    "system": "s1", "node": "n4", "col": 3}},
        {"op": "add_calc", "calc": {"name": "mol_X", "kind": "sp", "is_raw": True,
                                    "raw_text": OLD, "xyz": H2,
                                    "system": "s1", "node": "n5", "col": 1, "row": 1}},
    ])
    c = store.get("mol_Opt")
    c.state, c.message = CalcState.FAILED, "SCF did not converge."
    for name, why in (("mol_Freq", "Skipped: a dependency failed."),
                      ("mol_SP", "Skipped: a dependency failed."),
                      ("mol_X", "Skipped: something else.")):
        b = store.get(name)
        b.state, b.message = CalcState.BLOCKED, why
    return {n: store.get(n) for n in ("mol_Opt", "mol_Freq", "mol_SP", "mol_X")}


def test_a_set_on_a_failed_calc_runs_it_again_with_what_it_blocked(store):
    old = _fail_with_dependents(store)
    store.apply_ops([{"op": "rerun_calc", "name": "mol_Opt", "calc": RERUN}])
    c = store.get("mol_Opt")
    assert (c.state, c.message, c.raw_text) == (CalcState.PENDING, "", NEW)
    assert c.rerun and is_rerun(c)
    for name in ("mol_Freq", "mol_SP"):                 # downstream, transitively
        freed = store.get(name)
        assert freed is not old[name]                   # a new object: picked again
        assert (freed.state, freed.message) == (CalcState.PENDING, "")
        assert (freed.raw_text, freed.after) == (old[name].raw_text, old[name].after)
        assert not freed.rerun                          # its own set is unchanged
    assert store.get("mol_X") is old["mol_X"]           # not downstream: left alone
    assert store.get("mol_X").state == CalcState.BLOCKED


def test_undo_puts_the_failed_row_and_what_it_blocked_back(store):
    old = _fail_with_dependents(store)
    store.apply_ops([{"op": "rerun_calc", "name": "mol_Opt", "calc": RERUN}])
    # an edit in between makes mol_SP the user's row: the undo leaves it be
    store.apply_ops([{"op": "update_calc", "name": "mol_SP", "calc": {"gate": "approval"}}])
    edited = store.get("mol_SP")
    store.apply_ops([{"op": "undo_rerun", "name": "mol_Opt"}])
    assert store.get("mol_Opt") is old["mol_Opt"]
    assert store.get("mol_Opt").state == CalcState.FAILED
    assert store.get("mol_Opt").message == "SCF did not converge."
    assert store.get("mol_Freq") is old["mol_Freq"]
    assert store.get("mol_Freq").state == CalcState.BLOCKED
    assert store.get("mol_SP") is edited and edited.gate == "approval"


def _wait(pred, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_a_failed_calc_rerun_mid_run_runs_what_it_blocked_in_that_run(store, tmp_path,
                                                                      monkeypatch):
    # "hold" keeps the run alive while the rerun is put on; the budget below
    # admits two jobs at once, whatever the free memory (0 = cannot tell)
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    store.apply_ops([{"op": "add_calc", "calc": {
        "name": "hold", "kind": "sp", "is_raw": True, "raw_text": OLD, "xyz": H2}}])
    store.get("mol_Opt").state = CalcState.FAILED
    calls, release = [], threading.Event()

    def fake(calc, index):
        calls.append(calc.name)
        if calc.name == "hold":
            release.wait(5)
        calc.result = ParseResult(terminated_normally=True)
        calc.state = CalcState.DONE

    def factory():
        eng = QueueEngine("orca", str(tmp_path / "ws"), QueueCallbacks(
            log=lambda *a, **k: None, calc_update=lambda i, c: store.touch()),
            queue_lock=store._lock,
            budget=ResourceBudget(max_jobs=2, cores=64, ram_mb=10**6))
        eng._run_calc = fake
        return eng

    store.start_run(factory)
    try:
        assert _wait(lambda: "hold" in calls)
        assert _wait(lambda: store.get("mol_Freq").state == CalcState.BLOCKED)
        store.apply_ops([{"op": "rerun_calc", "name": "mol_Opt", "calc": RERUN}])
        assert _wait(lambda: "mol_Opt" in calls)
        release.set()
        assert _wait(lambda: store.get("mol_Freq").state == CalcState.DONE)
    finally:
        # stop the run before the fixture's data folder goes away
        release.set()
        store.wait_for_run(5)
    assert calls.count("mol_Opt") == 1 and calls.count("mol_Freq") == 1
    assert store.get("mol_Opt").state == CalcState.DONE
