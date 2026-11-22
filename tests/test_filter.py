"""CREST conformers -> a stack of single points, screened by the energy
condition on the edge into the stack (core/queue.py, state/store.py).

* ``ref_point`` ``conf:<k>`` starts a calc from conformer k (1 = the lowest)
  of a CREST search; k past the end of the ensemble is FILTERED, never FAILED.
* ``filter_de`` (kcal/mol window) / ``filter_top`` (the N lowest) on a calc
  rank it among the members of its stack (same system and node). The engine
  judges it once every member's parent is settled (the barrier), before the
  approval gate; a calc that fails it is FILTERED -- terminal, never run --
  and so is everything downstream of it.
* A change of the condition on a FILTERED row reopens it (and the FILTERED
  rows downstream of it) through update_calc; every other edit stays refused.

Same harness as test_queue_graph (a scripted _run_calc), with the parents'
energies set by the fake; CREST results come from the real ethanol corpus
(two conformers, -11.39433939 and -11.39186743 Eh: 1.55 kcal/mol apart).
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

import pytest

import orcamgr.core.queue as queue_mod
import orcamgr.state.store as store_mod
from orcamgr.core.parser import ParseResult
from orcamgr.core.queue import (HARTREE_TO_KCAL_MOL, CalcState, Calculation,
                                OrcaFiltered, conf_index, is_ref_point,
                                ref_point_problem)
from orcamgr.core.resources import ResourceBudget
from orcamgr.core.runner import OrcaRunError
from orcamgr.state.store import (QueueOpError, QueueStore, calc_from_dict,
                                 calc_from_session_dict, calc_to_dict,
                                 calc_to_session_dict)

from test_queue_engine import EngineHarness, _real_engine, make_calc, raiser

FIXTURES = Path(__file__).parent / "crest" / "fixtures" / "ethanol"
H2 = "H 0.0 0.0 0.0\nH 0.0 0.0 0.74"
E1, E2 = -11.39433939, -11.39186743              # the corpus's two conformers
DE_21 = (E2 - E1) * HARTREE_TO_KCAL_MOL           # 1.55 kcal/mol


# ---- helpers -------------------------------------------------------------------

def crest_done(tmp_path, name: str = "crest") -> Calculation:
    """A DONE CREST search whose folder holds the corpus ensemble, restored
    from a session: no result in memory (it is read on demand)."""
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    for f in FIXTURES.iterdir():
        shutil.copy(f, folder / f.name)
    c = make_calc(name, kind="crest_conf", state=CalcState.DONE)
    c.output_path = str(folder / "crest.out")
    return c


def conf_member(name: str, k: int, parent: str = "crest", node: str = "stack",
                **filt) -> Calculation:
    c = make_calc(name, ref=parent)
    c.ref_point = f"conf:{k}"
    c.after = parent
    c.system, c.node = "s1", node
    for key, value in filt.items():
        setattr(c, key, value)
    return c


def member(name: str, parent: str, node: str = "stack", **filt) -> Calculation:
    c = make_calc(name)
    c.after = parent
    c.system, c.node = "s1", node
    for key, value in filt.items():
        setattr(c, key, value)
    return c


def done_with_energy(name: str, energy) -> Calculation:
    c = make_calc(name, state=CalcState.DONE)
    c.result = ParseResult(terminated_normally=True, final_energy_eh=energy)
    return c


def energies(harness: EngineHarness, table: dict) -> None:
    """Make the fake runner finish each named calc with that final energy."""
    fake = harness.engine._run_calc

    def run(calc, index):
        fake(calc, index)
        if calc.name in table:
            calc.result = ParseResult(terminated_normally=True,
                                      final_energy_eh=table[calc.name])
    harness.engine._run_calc = run


def run_bounded(harness: EngineHarness, calcs, timeout: float = 10.0) -> None:
    t = threading.Thread(target=harness.engine.run_all, args=(calcs,), daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), "the run did not end"


def wide(tmp_path) -> EngineHarness:
    return EngineHarness(tmp_path, budget=ResourceBudget(max_jobs=4, cores=64,
                                                         ram_mb=10**6))


# ---- the trust boundary ----------------------------------------------------------

@pytest.mark.parametrize("value, ok", [
    ("conf:1", True), ("conf:12", True), ("", True), ("irc_f", True),
    ("conf:0", False), ("conf:01", False), ("conf:-1", False), ("conf:", False),
    ("conf:1 ", False), ("CONF:1", False), ("conf:1.5", False), (3, False),
])
def test_conf_ref_point_is_strict(value, ok):
    assert is_ref_point(value) is ok
    d = {"name": "a", "kind": "sp", "xyz": H2, "ref_point": value}
    if ok:
        assert calc_from_dict(d).ref_point == value
    else:
        with pytest.raises(ValueError, match="ref_point"):
            calc_from_dict(d)
        assert calc_from_dict(d, restoring=True).ref_point == ""
    assert conf_index("conf:12") == 12 and conf_index("irc_f") == 0


@pytest.mark.parametrize("payload, want", [
    ({}, (None, 0)),
    ({"filter_de": None, "filter_top": None}, (None, 0)),
    ({"filter_de": 3, "filter_top": 5}, (3.0, 5)),
    ({"filter_de": 0.0, "filter_top": 0}, (0.0, 0)),
    ({"filter_de": 2.5, "filter_top": 4.0}, (2.5, 4)),
])
def test_filter_fields_at_the_trust_boundary(payload, want):
    c = calc_from_dict({"name": "a", "kind": "sp", "xyz": H2, **payload})
    assert (c.filter_de, c.filter_top) == want


@pytest.mark.parametrize("bad", [
    {"filter_de": -1}, {"filter_de": float("inf")}, {"filter_de": float("nan")},
    {"filter_de": "3"}, {"filter_de": True},
    {"filter_top": -1}, {"filter_top": 2.5}, {"filter_top": "5"}, {"filter_top": True},
])
def test_a_bad_filter_value_is_refused_and_degrades_on_restore(bad):
    d = {"name": "a", "kind": "sp", "xyz": H2, **bad}
    key = next(iter(bad))
    with pytest.raises(ValueError, match=key):
        calc_from_dict(d)
    c = calc_from_dict(d, restoring=True)
    assert (c.filter_de, c.filter_top) == (None, 0)


def test_the_new_state_and_fields_round_trip_through_the_session():
    c = calc_from_dict({"name": "sp-c3", "kind": "sp", "geometry_source": "reference",
                        "ref_name": "crest", "ref_point": "conf:3", "after": "crest",
                        "filter_de": 3.0, "filter_top": 5})
    c.state = CalcState.FILTERED
    c.message = "Filtered: CREST found only 2 conformers"
    back = calc_from_session_dict(json.loads(json.dumps(calc_to_session_dict(c))))
    assert back.state == CalcState.FILTERED
    assert back.message == c.message
    assert (back.ref_point, back.filter_de, back.filter_top) == ("conf:3", 3.0, 5)
    summary = calc_to_dict(back)
    assert (summary["state"], summary["filter_de"], summary["filter_top"]) == (
        "filtered", 3.0, 5)


def test_an_old_session_entry_without_the_keys_loads_with_the_defaults():
    old = calc_to_session_dict(calc_from_dict({"name": "a", "kind": "sp", "xyz": H2}))
    del old["filter_de"], old["filter_top"]
    back = calc_from_session_dict(old)
    assert (back.filter_de, back.filter_top) == (None, 0)


def test_a_filtered_row_is_restored_as_is(monkeypatch, tmp_path):
    monkeypatch.setattr(store_mod, "user_data_root", lambda: tmp_path)
    store = QueueStore()
    c = make_calc("x")
    c.state, c.message = CalcState.FILTERED, "Filtered: rank 7 > top 5"
    store.add(c)
    store.save_session()
    again = QueueStore()
    again.load_session()
    got = again.get("x")
    assert (got.state, got.message, got.run_dir) == (
        CalcState.FILTERED, "Filtered: rank 7 > top 5", "")


# ---- conf:<k> needs a CREST parent ---------------------------------------------------

def test_conf_ref_point_is_refused_on_a_parent_that_is_not_a_crest_search(monkeypatch, tmp_path):
    opt = make_calc("opt", kind="opt")
    child = make_calc("sp", ref="opt")
    child.ref_point = "conf:2"
    assert "is not a CREST conformer search (opt)" in ref_point_problem(child, opt)
    assert ref_point_problem(child, make_calc("crest", kind="crest_conf")) == ""
    # ...and irc_f is refused on a CREST parent, as before
    child.ref_point = "irc_f"
    assert "is not an IRC" in ref_point_problem(child, make_calc("crest", kind="crest_conf"))

    monkeypatch.setattr(store_mod, "user_data_root", lambda: tmp_path)
    store = QueueStore()
    store.add(calc_from_dict({"name": "opt", "kind": "opt", "xyz": H2}))
    with pytest.raises(ValueError, match="not a CREST conformer search"):
        store.add(calc_from_dict({"name": "sp", "kind": "sp", "geometry_source": "reference",
                                  "ref_name": "opt", "ref_point": "conf:1"}))
    with pytest.raises(QueueOpError, match="not a CREST conformer search"):
        store.apply_ops([{"op": "add_calc", "calc": {
            "name": "sp", "kind": "sp", "geometry_source": "reference",
            "ref_name": "opt", "ref_point": "conf:1"}}])


# ---- _resolve_geometry -----------------------------------------------------------------

def test_conf_k_reads_conformer_k_of_the_crest_ensemble(tmp_path):
    engine = _real_engine(tmp_path)
    crest = crest_done(tmp_path)
    engine._by_name = {"crest": crest}
    first = engine._resolve_geometry(conf_member("c1", 1))
    second = engine._resolve_geometry(conf_member("c2", 2))
    # the ensemble was read on demand (a restored session holds no result)
    assert [c.index for c in crest.result.conformers] == [1, 2]
    assert first.splitlines()[0].split()[0] == "C"
    assert first.splitlines()[0].split()[1] == "1.2750049697"
    assert len(first.splitlines()) == len(second.splitlines()) == 9
    assert first != second
    # the same numbering as the per-conformer export ({name}_c<k>.xyz)
    lines = (FIXTURES / "crest_conformers.xyz").read_text().splitlines()
    assert second.splitlines()[0].split()[1:] == lines[13].split()[1:]


def test_conf_past_the_end_of_the_ensemble_is_filtered_not_failed(tmp_path):
    engine = _real_engine(tmp_path)
    engine._by_name = {"crest": crest_done(tmp_path)}
    with pytest.raises(OrcaFiltered, match="CREST found only 2 conformers"):
        engine._resolve_geometry(conf_member("c3", 3))


def test_conf_of_an_unfinished_crest_is_not_read(tmp_path):
    engine = _real_engine(tmp_path)
    engine._by_name = {"crest": make_calc("crest", kind="crest_conf")}
    with pytest.raises(OrcaRunError, match="no usable geometry yet"):
        engine._resolve_geometry(conf_member("c1", 1))


# ---- the engine: conformer stacks ----------------------------------------------------

def test_a_stack_larger_than_the_ensemble_filters_the_rest(tmp_path):
    harness = wide(tmp_path)
    crest = crest_done(tmp_path)
    stack = [conf_member(f"c{k}", k) for k in range(1, 5)]   # no condition at all

    run_bounded(harness, [crest, *stack])

    assert sorted(harness.calls) == ["c1", "c2"]
    for c in stack[2:]:
        assert c.state == CalcState.FILTERED
        assert c.message == "Filtered: CREST found only 2 conformers"
        assert c.run_dir == ""                   # it never ran: no folder
    assert "[c3] filtered: CREST found only 2 conformers." in harness.log_text()


def test_a_crest_window_is_relative_to_conformer_1(tmp_path):
    harness = wide(tmp_path)
    crest = crest_done(tmp_path)
    c1, c2 = (conf_member(f"c{k}", k, filter_de=1.0) for k in (1, 2))

    run_bounded(harness, [crest, c1, c2])

    assert harness.calls == ["c1"]
    assert c2.state == CalcState.FILTERED
    assert c2.message == f"Filtered: ΔE {DE_21:.2f} kcal/mol > 1.00" == (
        "Filtered: ΔE 1.55 kcal/mol > 1.00")


def test_a_lone_conformer_is_judged_against_the_ensemble_minimum(tmp_path):
    # a group of one (no node) is still judged against conformer 1, not itself
    harness = wide(tmp_path)
    lone = conf_member("c2", 2, node="", filter_de=1.0)
    wider = conf_member("w2", 2, node="", filter_de=2.0)

    run_bounded(harness, [crest_done(tmp_path), lone, wider])

    assert lone.state == CalcState.FILTERED
    assert harness.calls == ["w2"]


def test_crest_top_n(tmp_path):
    harness = wide(tmp_path)
    stack = [conf_member(f"c{k}", k, filter_top=1) for k in (1, 2)]
    run_bounded(harness, [crest_done(tmp_path), *stack])
    assert harness.calls == ["c1"]
    assert stack[1].message == "Filtered: rank 2 > top 1"


# ---- the engine: parent energies -----------------------------------------------------

def test_top_n_ranks_by_the_parents_energies(tmp_path):
    harness = wide(tmp_path)
    parents = [done_with_energy("o1", -1.000), done_with_energy("o2", -1.010),
               done_with_energy("o3", -1.005)]
    stack = [member(f"s{i}", f"o{i}", filter_top=2) for i in (1, 2, 3)]

    run_bounded(harness, [*parents, *stack])

    assert sorted(harness.calls) == ["s2", "s3"]
    assert stack[0].state == CalcState.FILTERED
    assert stack[0].message == "Filtered: rank 3 > top 2"


def test_a_window_on_parent_energies_is_relative_to_the_group_minimum(tmp_path):
    harness = wide(tmp_path)
    kcal = 1 / HARTREE_TO_KCAL_MOL
    parents = [done_with_energy("o1", -2.0), done_with_energy("o2", -2.0 + 2.9 * kcal),
               done_with_energy("o3", -2.0 + 3.1 * kcal)]
    stack = [member(f"s{i}", f"o{i}", filter_de=3.0) for i in (1, 2, 3)]

    run_bounded(harness, [*parents, *stack])

    assert sorted(harness.calls) == ["s1", "s2"]
    assert stack[2].message == "Filtered: ΔE 3.10 kcal/mol > 3.00"


def test_ties_rank_by_queue_order_and_the_window_edge_passes(tmp_path):
    harness = wide(tmp_path)
    parents = [done_with_energy("ob", -1.0), done_with_energy("oa", -1.0)]
    stack = [member("sb", "ob", filter_top=1, filter_de=0.0),
             member("sa", "oa", filter_top=1, filter_de=0.0)]
    run_bounded(harness, [*parents, *stack])
    assert harness.calls == ["sb"]                 # first in the queue, not by name
    assert stack[1].message == "Filtered: rank 2 > top 1"


def test_ties_between_conformers_rank_by_conformer_number_not_name_text(tmp_path):
    # equal energies (CREST frames without one read as 0.0): _c2 before _c10,
    # though "c10" < "c2" as text and c10 is first in the queue
    harness = wide(tmp_path)
    crest = crest_done(tmp_path)
    stack = [conf_member("c10", 1, filter_top=1), conf_member("c2", 1, filter_top=1)]
    stack[0].ref_point, stack[1].ref_point = "conf:2", "conf:1"
    from orcamgr.core.parser import Conformer
    crest.result = ParseResult(terminated_normally=True, conformers=[
        Conformer(index=1, energy_eh=0.0, rel_kcal=0.0),
        Conformer(index=2, energy_eh=0.0, rel_kcal=0.0)])
    run_bounded(harness, [crest, *stack])
    assert harness.calls == ["c2"]
    assert stack[0].message == "Filtered: rank 2 > top 1"


def test_a_restored_running_member_is_reattached_never_judged(tmp_path):
    # a detached job of a stack member restarted with the app: even ranked
    # out, it is reattached (its pid kept), never FILTERED or BLOCKED
    harness = wide(tmp_path)
    parents = [done_with_energy("o1", -1.0), done_with_energy("o2", -2.0)]
    stack = [member("s1", "o1", filter_top=1), member("s2", "o2", filter_top=1)]
    stack[0].state, stack[0].pid, stack[0].create_time = CalcState.RUNNING, 4242, 1.0
    run_bounded(harness, [*parents, *stack])
    assert "s1" in harness.calls
    assert stack[0].state != CalcState.FILTERED


def test_a_running_member_is_reattached_though_a_siblings_parent_waits_at_a_gate(tmp_path):
    # the barrier holds an unstarted member; a RUNNING one is reattached anyway
    harness = wide(tmp_path)
    held = member("o2", "o1", node="", gate="approval")
    stack = [member("s1", "o1", filter_top=1), member("s2", "o2", filter_top=1)]
    stack[0].state, stack[0].pid, stack[0].create_time = CalcState.RUNNING, 4242, 1.0
    run_bounded(harness, [done_with_energy("o1", -1.0), held, *stack])
    assert "s1" in harness.calls


def test_a_running_member_whose_parent_cannot_be_read_is_still_reattached(tmp_path):
    harness = wide(tmp_path)
    o1 = make_calc("o1", state=CalcState.DONE)          # restored, output gone
    o1.output_path = str(tmp_path / "gone" / "o1.out")
    s1 = member("s1", "o1", filter_top=1)
    s1.state, s1.pid, s1.create_time = CalcState.RUNNING, 4242, 1.0
    run_bounded(harness, [o1, s1])
    assert "s1" in harness.calls and s1.state != CalcState.BLOCKED


def test_a_rerun_row_is_not_screened_out_again(tmp_path):
    harness = wide(tmp_path)
    parents = [done_with_energy("o1", -1.0), done_with_energy("o2", -2.0)]
    stack = [member("s1", "o1", filter_top=1), member("s2", "o2", filter_top=1)]
    stack[0].rerun = True
    run_bounded(harness, [*parents, *stack])
    assert sorted(harness.calls) == ["s1", "s2"]


def test_a_conf_ref_point_with_a_trailing_newline_is_refused():
    d = {"name": "a", "kind": "sp", "xyz": H2, "ref_point": "conf:1\n"}
    assert is_ref_point("conf:1\n") is False and conf_index("conf:1\n") == 0
    with pytest.raises(ValueError, match="ref_point"):
        calc_from_dict(d)


def test_the_barrier_waits_for_every_parent_of_the_stack(tmp_path):
    # o1 finishes first with the HIGHER energy; without the barrier s1 would
    # run before o2 (slow, lower) is known
    harness = wide(tmp_path)
    energies(harness, {"o1": -1.0, "o2": -1.5})
    harness.behaviors["o2"] = lambda c: time.sleep(0.4)
    o1, o2 = make_calc("o1"), make_calc("o2")
    s1, s2 = member("s1", "o1", filter_top=1), member("s2", "o2", filter_top=1)

    run_bounded(harness, [o1, o2, s1, s2])

    assert s1.state == CalcState.FILTERED
    assert "s1" not in harness.calls
    assert harness.calls[-1] == "s2"


def test_a_failed_parent_is_left_out_and_its_member_blocked(tmp_path):
    harness = wide(tmp_path)
    energies(harness, {"o1": -1.0})
    harness.behaviors["o2"] = raiser(OrcaRunError("SCF did not converge"))
    o1, o2 = make_calc("o1"), make_calc("o2")
    s1, s2 = member("s1", "o1", filter_top=1), member("s2", "o2", filter_top=1)

    run_bounded(harness, [o1, o2, s1, s2])

    assert o2.state == CalcState.FAILED
    assert s2.state == CalcState.BLOCKED          # normal semantics, editable
    assert s1.state == CalcState.DONE             # ranked alone: first
    assert "s1" in harness.calls


def test_a_done_parent_without_an_energy_blocks_its_member(tmp_path):
    harness = wide(tmp_path)
    parents = [done_with_energy("o1", -1.0), done_with_energy("o2", None)]
    stack = [member("s1", "o1", filter_top=1), member("s2", "o2", filter_top=1)]
    run_bounded(harness, [*parents, *stack])
    assert stack[1].state == CalcState.BLOCKED
    assert "finished without a final energy" in stack[1].message
    assert harness.calls == ["s1"]


def test_a_restored_parents_energy_is_read_outside_the_store_lock(tmp_path, monkeypatch):
    out = tmp_path / "o1" / "o1.out"
    out.parent.mkdir()
    out.write_text("FINAL SINGLE POINT ENERGY      -1.250000000\n", encoding="utf-8")
    parent = make_calc("o1", state=CalcState.DONE)
    parent.output_path = str(out)
    other = done_with_energy("o2", -1.0)
    harness = wide(tmp_path)
    real = queue_mod.result_from_output
    held = []

    def watched(calc):
        held.append(harness.engine._queue_lock._is_owned())
        return real(calc)
    monkeypatch.setattr(queue_mod, "result_from_output", watched)
    s1, s2 = member("s1", "o1", filter_top=1), member("s2", "o2", filter_top=1)

    run_bounded(harness, [parent, other, s1, s2])

    assert held == [False]                       # read once, never under the lock
    assert parent.result.final_energy_eh == -1.25
    assert harness.calls == ["s1"] and s2.state == CalcState.FILTERED


# ---- FILTERED: cascade, terminal, before the gate -------------------------------------

def test_the_dependents_of_a_filtered_calc_are_filtered(tmp_path):
    harness = wide(tmp_path)
    crest = crest_done(tmp_path)
    c3 = conf_member("c3", 3)
    opt = make_calc("opt3", ref="c3")              # geometry from the filtered one
    freq = member("freq3", "opt3", node="")         # after it, one further down

    run_bounded(harness, [freq, opt, crest, c3])

    assert harness.calls == []
    assert c3.state == opt.state == freq.state == CalcState.FILTERED
    assert opt.message == "Filtered: parent c3 was filtered"
    assert freq.message == "Filtered: parent opt3 was filtered"


def test_a_filtered_row_never_runs_and_never_keeps_the_run_alive(tmp_path):
    harness = EngineHarness(tmp_path)
    old = make_calc("old")
    old.state, old.message = CalcState.FILTERED, "Filtered: rank 3 > top 2"
    fresh = make_calc("fresh")

    run_bounded(harness, [old, fresh], timeout=5.0)

    assert harness.calls == ["fresh"]
    assert (old.state, old.message) == (CalcState.FILTERED, "Filtered: rank 3 > top 2")


def test_the_condition_is_judged_before_the_approval_gate(tmp_path):
    harness = wide(tmp_path)
    crest = crest_done(tmp_path)
    c1 = conf_member("c1", 1, filter_top=1, gate="approval")
    c2 = conf_member("c2", 2, filter_top=1, gate="approval")

    run_bounded(harness, [crest, c1, c2])

    assert c2.state == CalcState.FILTERED          # never left waiting for approval
    assert c1.state == CalcState.PENDING           # passed, now held at its gate
    assert harness.calls == []


def test_a_member_whose_parent_waits_at_a_gate_holds_the_stack_not_blocks_it(tmp_path):
    harness = wide(tmp_path)
    o1 = done_with_energy("o1", -1.0)
    held = member("o2", "o1", node="", gate="approval")   # never approved this run
    s1, s2 = member("s1", "o1", filter_top=1), member("s2", "o2", filter_top=1)

    run_bounded(harness, [o1, held, s1, s2])

    assert held.state == CalcState.PENDING
    assert s1.state == CalcState.PENDING and s2.state == CalcState.PENDING
    assert harness.calls == []


# ---- the store: reopening on a change of the condition -------------------------------

@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(store_mod, "user_data_root", lambda: tmp_path)
    return QueueStore()


def _filtered_chain(store) -> None:
    store.apply_ops([{"op": "add_system", "system": {"id": "s1", "name": "mol",
                                                     "inputs": [{"label": "x", "xyz": H2}]}}])
    for name, d in (
            ("mol-crest", {"kind": "crest_conf", "xyz": H2, "node": "n0"}),
            ("mol-c1", {"kind": "sp", "geometry_source": "reference", "ref_name": "mol-crest",
                        "ref_point": "conf:1", "after": "mol-crest", "node": "n1",
                        "filter_top": 1}),
            ("mol-c2", {"kind": "sp", "geometry_source": "reference", "ref_name": "mol-crest",
                        "ref_point": "conf:2", "after": "mol-crest", "node": "n1",
                        "filter_top": 1}),
            ("mol-o2", {"kind": "opt", "geometry_source": "reference", "ref_name": "mol-c2",
                        "after": "mol-c2", "node": "n2"})):
        store.apply_ops([{"op": "add_calc", "calc": {"name": name, "system": "s1", **d}}])
    store.get("mol-crest").state = CalcState.DONE
    store.get("mol-c1").state = CalcState.DONE
    for n in ("mol-c2", "mol-o2"):
        store.get(n).state = CalcState.FILTERED
        store.get(n).message = "Filtered: whatever"


def test_a_filter_edit_reopens_the_filtered_row_and_its_downstream(store):
    _filtered_chain(store)
    done_c1 = store.get("mol-c1")
    store.apply_ops([{"op": "update_calc", "name": n, "calc": {"filter_top": 2}}
                     for n in ("mol-c1", "mol-c2")])
    c1, c2, o2 = store.get("mol-c1"), store.get("mol-c2"), store.get("mol-o2")
    # the DONE member keeps what it ran (the same object), the condition recorded
    assert c1 is done_c1 and c1.state == CalcState.DONE and c1.filter_top == 2
    assert (c2.state, c2.message, c2.filter_top) == (CalcState.PENDING, "", 2)
    assert (o2.state, o2.message) == (CalcState.PENDING, "")
    snap = {c["name"]: c for c in store.snapshot()["calculations"]}
    assert snap["mol-c2"]["filter_top"] == 2 and snap["mol-c2"]["state"] == "pending"


def test_any_other_edit_of_a_filtered_row_is_refused(store):
    _filtered_chain(store)
    with pytest.raises(QueueOpError, match="can be edited"):
        store.apply_ops([{"op": "update_calc", "name": "mol-c2",
                          "calc": {"filter_top": 2, "charge": 1}}])
    with pytest.raises(QueueOpError, match="filter_de"):
        store.apply_ops([{"op": "update_calc", "name": "mol-c2",
                          "calc": {"filter_de": -2}}])
    with pytest.raises(QueueOpError, match="screened out"):
        store.apply_ops([{"op": "rerun_calc", "name": "mol-c2",
                          "calc": {"raw_text": "x"}}])
    with pytest.raises(ValueError, match="moved"):
        names = store.names()
        store.reorder(names.index("mol-c2"), names.index("mol-o2"))
    assert store.get("mol-c2").state == CalcState.FILTERED


def test_a_filtered_row_follows_a_lane_rename_and_stays_filtered(store):
    _filtered_chain(store)
    store.apply_ops([{"op": "update_system", "id": "s1", "patch": {"name": "eth"}}])
    names = set(store.names())
    assert {"eth-c2", "eth-o2"} <= names
    o2 = store.get("eth-o2")
    assert (o2.state, o2.ref_name, o2.after) == (CalcState.FILTERED, "eth-c2", "eth-c2")


def test_a_filtered_row_leaves_the_list_and_needs_no_orca(store):
    from orcamgr.state.store import find_dangling_reference, queue_needs_orca
    _filtered_chain(store)
    calcs = store.list()
    assert not queue_needs_orca([c for c in calcs if c.state == CalcState.FILTERED])
    store.remove("mol-c2")              # its FILTERED dependent needs nothing of it
    assert find_dangling_reference(store.list()) is None


def test_a_crest_set_put_on_a_guided_block_drops_its_advanced_options():
    # the set's text is the whole setting: advanced options the text cannot
    # name (cluster, MTD length, ...) must not survive from the old config
    d = {"name": "a_CREST", "kind": "crest_conf", "xyz": H2, "is_raw": True,
         "raw_text": "method gfn2\newin 6\nthreads 4",
         "config": {"crest_cluster": True, "crest_mdlen_mult": 2.0, "crest_ewin": 9.0}}
    cfg = calc_from_dict(d).config
    assert cfg.crest_cluster is False and cfg.crest_mdlen_mult == 0.0
    assert cfg.crest_ewin == 6.0 and cfg.crest_method == "gfn2"
