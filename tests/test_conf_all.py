"""Conformer placeholders: ``ref_point`` ``conf:all`` (core/queue.py,
state/store.py).

Before a CREST search has run, the queue editor's conformer picker with an
EMPTY count adds one placeholder step on the stack node ("every conformer it
finds"). It is never run. When the search is DONE with N conformers, the
placeholder and every calc hanging off it (by `after` or a geometry
reference, transitively) are replaced -- as one change under the store lock
-- by N copies: ``P_c<k>`` with ``conf:<k>``, and ``D_c<k>`` re-pointed at the
k-th copy of its parent, each keeping its node so every step stays one stack.
After that the normal filter / barrier rules apply unchanged.

Same harness as test_filter (a scripted engine, the ethanol CREST corpus: two
conformers 1.55 kcal/mol apart).
"""

from __future__ import annotations

import json
import shutil
import threading

import pytest

import orcamgr.core.queue as queue_mod
import orcamgr.state.store as store_mod
from orcamgr.core.queue import (CONF_ALL, CalcState, Calculation, QueueCallbacks,
                                QueueEngine, conformer_placeholder_chain,
                                expand_conformer_placeholders, is_conf_placeholder,
                                is_ref_point, ref_point_problem)
from orcamgr.core.resources import ResourceBudget
from orcamgr.core.runner import OrcaRunError
from orcamgr.state.store import (QueueOpError, QueueStore, calc_from_dict,
                                 calc_from_session_dict, calc_to_session_dict)

from test_filter import FIXTURES, H2, crest_done, run_bounded, wide
from test_queue_engine import EngineHarness, make_calc, raiser


# ---- helpers -------------------------------------------------------------------

def placeholder(name: str = "mol-SP", parent: str = "crest", node: str = "n1",
                **extra) -> Calculation:
    c = make_calc(name, ref=parent)
    c.ref_point = CONF_ALL
    c.after = parent
    c.system, c.node, c.col, c.row = "s1", node, 1, 0
    for key, value in extra.items():
        setattr(c, key, value)
    return c


def step(name: str, parent: str, node: str, kind: str = "opt", by_ref: bool = True) -> Calculation:
    c = make_calc(name, kind=kind, ref=parent if by_ref else "")
    c.after = parent
    c.system, c.node, c.col = "s1", node, 2
    return c


def crest_runs_in(harness: EngineHarness, tmp_path, name: str = "crest"):
    """Make the fake CREST backend finish with the corpus ensemble (two
    conformers) -- the search runs IN this run."""
    folder = tmp_path / f"{name}_run"
    folder.mkdir(parents=True, exist_ok=True)
    for f in FIXTURES.iterdir():
        shutil.copy(f, folder / f.name)

    def run(calc, index):
        harness.calls.append(calc.name)
        calc.output_path = str(folder / "crest.out")
        calc.result = queue_mod.result_from_output(calc)
        calc.state = CalcState.DONE
        calc.message = "Completed — 2 conformer(s)."
    harness.engine._run_crest_calc = run


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(store_mod, "user_data_root", lambda: tmp_path)
    return QueueStore()


def _lane(store) -> None:
    store.apply_ops([{"op": "add_system", "system": {
        "id": "s1", "name": "mol", "inputs": [{"label": "x", "xyz": H2}]}}])


def _add(store, name: str, **d) -> None:
    store.apply_ops([{"op": "add_calc", "calc": {"name": name, "system": "s1", **d}}])


def _ph(**extra) -> dict:
    return {"kind": "sp", "geometry_source": "reference", "ref_name": "mol-crest",
            "ref_point": "conf:all", "after": "mol-crest", "node": "n1", "col": 1, **extra}


# ---- the trust boundary ----------------------------------------------------------

@pytest.mark.parametrize("value, ok", [
    ("conf:all", True), ("CONF:ALL", False), ("conf:All", False),
    ("conf:all ", False), ("conf:all\n", False), ("conf:al", False), ("all", False),
])
def test_conf_all_is_one_closed_value(value, ok):
    assert is_ref_point(value) is ok
    d = {"name": "a", "kind": "sp", "xyz": H2, "ref_point": value}
    if ok:
        assert calc_from_dict(d).ref_point == CONF_ALL
    else:
        with pytest.raises(ValueError, match="conf:all"):
            calc_from_dict(d)
        assert calc_from_dict(d, restoring=True).ref_point == ""


def test_conf_all_needs_a_crest_parent(store):
    opt = make_calc("opt", kind="opt")
    p = placeholder("sp", parent="opt")
    assert "every conformer of 'opt'" in ref_point_problem(p, opt)
    assert "not a CREST conformer search (opt)" in ref_point_problem(p, opt)
    assert ref_point_problem(p, make_calc("opt", kind="crest_conf")) == ""

    store.add(calc_from_dict({"name": "opt", "kind": "opt", "xyz": H2}))
    with pytest.raises(ValueError, match="not a CREST conformer search"):
        store.add(calc_from_dict({"name": "sp", "kind": "sp", "geometry_source": "reference",
                                  "ref_name": "opt", "ref_point": "conf:all"}))
    with pytest.raises(QueueOpError, match="not a CREST conformer search"):
        store.apply_ops([{"op": "add_calc", "calc": {
            "name": "sp", "kind": "sp", "geometry_source": "reference",
            "ref_name": "opt", "ref_point": "conf:all"}}])
    store.add(calc_from_dict({"name": "crest", "kind": "crest_conf", "xyz": H2}))
    store.add(calc_from_dict({"name": "sp", "kind": "sp", "geometry_source": "reference",
                              "ref_name": "crest", "ref_point": "conf:all", "after": "crest"}))
    assert is_conf_placeholder(store.get("sp"))


def test_a_placeholder_round_trips_through_the_session(store, tmp_path):
    _lane(store)
    _add(store, "mol-crest", kind="crest_conf", xyz=H2, node="n0")
    _add(store, "mol-SP", **_ph(filter_de=2.0, filter_top=3))
    store.save_session()
    again = QueueStore()
    again.load_session()
    p = again.get("mol-SP")
    assert (p.ref_point, p.ref_name, p.after, p.node) == (CONF_ALL, "mol-crest", "mol-crest", "n1")
    assert (p.filter_de, p.filter_top, p.state) == (2.0, 3, CalcState.PENDING)
    back = calc_from_session_dict(json.loads(json.dumps(calc_to_session_dict(p))))
    assert back.ref_point == CONF_ALL
    snap = {c["name"]: c for c in again.snapshot()["calculations"]}
    assert snap["mol-SP"]["ref_point"] == "conf:all"


# ---- the expansion, in the store -----------------------------------------------------

def test_a_placeholder_becomes_one_member_per_conformer(store):
    _lane(store)
    _add(store, "mol-crest", kind="crest_conf", xyz=H2, node="n0")
    _add(store, "mol-SP", **_ph(filter_de=2.5, filter_top=2, gate="approval", auto_pass=False))
    store.get("mol-crest").state = CalcState.DONE
    v0 = store.version()

    report = store.expand_conformer_placeholders("mol-crest", 3)

    assert report == {"placeholders": ["mol-SP"], "n": 3, "members": 3,
                      "downstream": 0, "blocked": ""}
    assert store.names() == ["mol-crest", "mol-SP_c1", "mol-SP_c2", "mol-SP_c3"]
    for k in (1, 2, 3):
        c = store.get(f"mol-SP_c{k}")
        assert (c.ref_point, c.ref_name, c.after) == (f"conf:{k}", "mol-crest", "mol-crest")
        assert (c.system, c.node, c.col, c.row) == ("s1", "n1", 1, 0)
        assert (c.filter_de, c.filter_top, c.gate, c.auto_pass) == (2.5, 2, "approval", False)
        assert (c.state, c.message, c.run_dir, c.output_path) == (CalcState.PENDING, "", "", "")
        assert c.kind == "sp" and c.charge == 0 and c.multiplicity == 1
    # each copy has its own config object
    assert store.get("mol-SP_c1").config is not store.get("mol-SP_c2").config
    assert store.version() > v0                        # saved, and the poll sees it
    # idempotent: nothing left to expand
    assert store.expand_conformer_placeholders("mol-crest", 3) is None
    assert len(store.names()) == 4


def test_nothing_expands_before_the_search_is_done(store):
    _lane(store)
    _add(store, "mol-crest", kind="crest_conf", xyz=H2, node="n0")
    _add(store, "mol-SP", **_ph())
    assert store.expand_conformer_placeholders("mol-crest", 2) is None
    assert store.names() == ["mol-crest", "mol-SP"]


def test_a_chain_listed_child_before_parent_expands_track_by_track(store):
    # the queue lists Freq, then Opt, then the placeholder: the old fan-out
    # re-pointed such children at the parent's ORIGINAL name, which it removed
    _lane(store)
    _add(store, "mol-crest", kind="crest_conf", xyz=H2, node="n0")
    store.apply_ops([
        {"op": "add_calc", "calc": {"name": "mol-Freq", "system": "s1", "kind": "freq",
                                    "geometry_source": "reference", "ref_name": "mol-Opt",
                                    "after": "mol-Opt", "node": "n3", "col": 3}},
        {"op": "add_calc", "calc": {"name": "mol-Opt", "system": "s1", "kind": "opt",
                                    "geometry_source": "reference", "ref_name": "mol-SP",
                                    "after": "mol-SP", "node": "n2", "col": 2,
                                    "filter_top": 1}},
        {"op": "add_calc", "calc": {"name": "mol-SP", "system": "s1", **_ph()}},
    ])
    store.get("mol-crest").state = CalcState.DONE

    report = store.expand_conformer_placeholders("mol-crest", 2)

    assert (report["members"], report["downstream"]) == (2, 4)
    assert store.names() == ["mol-crest", "mol-Freq_c1", "mol-Freq_c2",
                             "mol-Opt_c1", "mol-Opt_c2", "mol-SP_c1", "mol-SP_c2"]
    for k in (1, 2):
        opt, freq = store.get(f"mol-Opt_c{k}"), store.get(f"mol-Freq_c{k}")
        assert (opt.ref_name, opt.after, opt.ref_point) == (f"mol-SP_c{k}", f"mol-SP_c{k}", "")
        assert (freq.ref_name, freq.after) == (f"mol-Opt_c{k}", f"mol-Opt_c{k}")
        assert (opt.node, freq.node, opt.filter_top) == ("n2", "n3", 1)
    from orcamgr.state.store import find_dangling_reference
    assert find_dangling_reference(store.list()) is None


def test_no_conformers_filters_the_placeholder_and_its_chain(store):
    _lane(store)
    _add(store, "mol-crest", kind="crest_conf", xyz=H2, node="n0")
    _add(store, "mol-SP", **_ph())
    _add(store, "mol-Opt", kind="opt", geometry_source="reference", ref_name="mol-SP",
         after="mol-SP", node="n2")
    store.get("mol-crest").state = CalcState.DONE

    report = store.expand_conformer_placeholders("mol-crest", 0)

    assert report["n"] == 0 and report["members"] == 0
    sp, opt = store.get("mol-SP"), store.get("mol-Opt")
    assert (sp.state, sp.message) == (CalcState.FILTERED, "Filtered: CREST found no conformers")
    assert opt.state == CalcState.FILTERED
    assert store.expand_conformer_placeholders("mol-crest", 0) is None


def test_copy_names_avoid_names_in_use_and_on_disk(store):
    # a stack's copies share one base: D, else D2, D3 ... as the editor names
    _lane(store)
    _add(store, "mol-crest", kind="crest_conf", xyz=H2, node="n0")
    _add(store, "mol-SP_C2", kind="sp", xyz=H2, node="n9")      # case-insensitive
    _add(store, "mol-SP", **_ph())
    _add(store, "mol-Opt", kind="opt", geometry_source="reference", ref_name="mol-SP",
         after="mol-SP", node="n2")
    store.get("mol-crest").state = CalcState.DONE

    store.expand_conformer_placeholders("mol-crest", 2, disk_taken={"MOL-OPT_c1"})

    names = store.names()
    assert {"mol-SP2_c1", "mol-SP2_c2", "mol-Opt2_c1", "mol-Opt2_c2"} <= set(names)
    assert store.get("mol-Opt2_c2").ref_name == "mol-SP2_c2"


def test_two_placeholders_joining_downstream_copy_once_per_conformer(store):
    # a step after two placeholders of the same search (a workflow with two
    # first steps that meet) is copied N times, not N x N
    _lane(store)
    _add(store, "mol-crest", kind="crest_conf", xyz=H2, node="n0")
    _add(store, "mol-A", **_ph(node="na"))
    _add(store, "mol-B", **_ph(node="nb"))
    _add(store, "mol-J", kind="opt", geometry_source="reference", ref_name="mol-A",
         after="mol-B", node="nj")
    store.get("mol-crest").state = CalcState.DONE

    report = store.expand_conformer_placeholders("mol-crest", 2)

    assert report["placeholders"] == ["mol-A", "mol-B"]
    assert (report["members"], report["downstream"]) == (4, 2)
    j2 = store.get("mol-J_c2")
    assert (j2.ref_name, j2.after) == ("mol-A_c2", "mol-B_c2")
    assert len(store.names()) == 1 + 4 + 2


def test_placeholders_of_several_searches_expand_separately(store):
    _lane(store)
    _add(store, "mol-crest", kind="crest_conf", xyz=H2, node="n0")
    _add(store, "mol-crestB", kind="crest_conf", xyz=H2, node="m0")
    _add(store, "mol-SP", **_ph())
    _add(store, "mol-SPB", **_ph(ref_name="mol-crestB", after="mol-crestB", node="m1"))
    store.get("mol-crest").state = CalcState.DONE

    store.expand_conformer_placeholders("mol-crest", 2)

    assert is_conf_placeholder(store.get("mol-SPB"))       # its search has not run
    assert store.get("mol-SP") is None and store.get("mol-SP_c2") is not None
    store.get("mol-crestB").state = CalcState.DONE
    store.expand_conformer_placeholders("mol-crestB", 1)
    assert store.get("mol-SPB_c1").ref_point == "conf:1"


def test_a_chain_row_removed_meanwhile_is_not_copied(store):
    # the batch sees the live list: what was removed a moment ago stays gone
    _lane(store)
    _add(store, "mol-crest", kind="crest_conf", xyz=H2, node="n0")
    _add(store, "mol-SP", **_ph())
    _add(store, "mol-Opt", kind="opt", geometry_source="reference", ref_name="mol-SP",
         after="mol-SP", node="n2")
    store.get("mol-crest").state = CalcState.DONE
    store.remove("mol-Opt")
    report = store.expand_conformer_placeholders("mol-crest", 2)
    assert report["downstream"] == 0
    assert store.names() == ["mol-crest", "mol-SP_c1", "mol-SP_c2"]


def test_a_chain_holding_a_started_row_is_refused_and_blocks_the_placeholder(store):
    _lane(store)
    _add(store, "mol-crest", kind="crest_conf", xyz=H2, node="n0")
    _add(store, "mol-SP", **_ph())
    _add(store, "mol-Opt", kind="opt", geometry_source="reference", ref_name="mol-SP",
         after="mol-SP", node="n2")
    store.get("mol-crest").state = CalcState.DONE
    store.get("mol-Opt").state = CalcState.DONE           # impossible by the rules

    report = store.expand_conformer_placeholders("mol-crest", 2)

    assert "'mol-Opt' hangs off it and has already started" in report["blocked"]
    assert store.names() == ["mol-crest", "mol-SP", "mol-Opt"]
    sp = store.get("mol-SP")
    assert sp.state == CalcState.BLOCKED and sp.message.startswith("Skipped: 'mol-Opt'")


def test_the_chain_is_found_whatever_the_list_order():
    crest = make_calc("crest", kind="crest_conf", state=CalcState.DONE)
    p = placeholder("p")
    a = step("a", "p", "na")
    b = step("b", "a", "nb", by_ref=False)
    other = make_calc("other")
    roots, down = conformer_placeholder_chain([b, other, a, crest, p], "crest")
    assert roots == [p] and down == [b, a]


# ---- the engine ---------------------------------------------------------------

def test_a_search_finishing_in_the_run_expands_then_the_filter_judges(tmp_path):
    harness = wide(tmp_path)
    crest = make_calc("crest", kind="crest_conf")
    crest_runs_in(harness, tmp_path)
    p = placeholder("sp", filter_de=1.0)                     # c2 is 1.55 above c1
    calcs = [p, crest]                                       # placeholder first

    run_bounded(harness, calcs)

    assert [c.name for c in calcs] == ["sp_c1", "sp_c2", "crest"]
    assert harness.calls == ["crest", "sp_c1"]               # never "sp"
    c1, c2 = calcs[0], calcs[1]
    assert c1.state == CalcState.DONE
    assert (c2.state, c2.message) == (CalcState.FILTERED, "Filtered: ΔE 1.55 kcal/mol > 1.00")
    assert "[crest] expanded sp into 2 copies (+0 downstream)" in harness.log_text()


def test_a_search_done_before_the_run_expands_at_pick_time(tmp_path):
    # restored DONE, no result in memory: read outside the lock, then expanded
    harness = wide(tmp_path)
    crest = crest_done(tmp_path)
    opt = step("opt", "sp", "n2")
    p = placeholder("sp")
    calcs = [opt, crest, p]

    run_bounded(harness, calcs)

    assert [c.name for c in calcs] == ["opt_c1", "opt_c2", "crest", "sp_c1", "sp_c2"]
    assert sorted(harness.calls) == ["opt_c1", "opt_c2", "sp_c1", "sp_c2"]
    assert harness.calls.index("sp_c1") < harness.calls.index("opt_c1")
    assert all(c.state == CalcState.DONE for c in calcs)
    assert calcs[0].ref_name == "sp_c1"


def test_a_failed_search_blocks_the_placeholder_and_its_chain(tmp_path):
    harness = wide(tmp_path)
    harness.behaviors["crest"] = raiser(OrcaRunError("CREST died"))
    crest = make_calc("crest", kind="crest_conf")
    p = placeholder("sp")
    opt = step("opt", "sp", "n2")

    run_bounded(harness, [crest, p, opt])

    assert crest.state == CalcState.FAILED
    assert p.state == CalcState.BLOCKED and opt.state == CalcState.BLOCKED
    assert harness.calls == ["crest"]


def test_a_placeholder_is_never_dispatched_while_its_search_waits(tmp_path):
    # the search held at an approval gate: the placeholder waits, PENDING,
    # and the run ends normally
    harness = wide(tmp_path)
    root = make_calc("root")
    crest = make_calc("crest", kind="crest_conf")
    crest.after, crest.gate = "root", "approval"
    p = placeholder("sp")

    run_bounded(harness, [root, crest, p])

    assert harness.calls == ["root"]
    assert (crest.state, p.state, p.name) == (CalcState.PENDING, CalcState.PENDING, "sp")


def test_a_placeholder_reaching_precheck_is_blocked_not_run(tmp_path):
    harness = wide(tmp_path)
    p = placeholder("sp")
    harness.engine._by_name = {"crest": make_calc("crest", kind="crest_conf",
                                                  state=CalcState.DONE), "sp": p}
    assert harness.engine._precheck(p, 0) is False
    assert p.state == CalcState.BLOCKED and "never run itself" in p.message


def test_the_disk_lookup_runs_outside_the_lock(tmp_path, monkeypatch):
    harness = wide(tmp_path)
    crest = crest_done(tmp_path)
    p = placeholder("sp")
    held = []
    real = queue_mod.names_with_output

    def watched(ws, names):
        held.append(harness.engine._queue_lock._is_owned())
        return real(ws, names)
    monkeypatch.setattr(queue_mod, "names_with_output", watched)
    # a folder left by an earlier run holds output under the first copy's name
    old = tmp_path / "sp_c1"
    old.mkdir()
    (old / "sp_c1.out").write_text("old\n", encoding="utf-8")

    run_bounded(harness, [crest, p])

    assert held and not any(held)
    assert sorted(harness.calls) == ["sp2_c1", "sp2_c2"]


def test_the_crest_finish_point_expands_at_once(tmp_path):
    # the real finish point (finished while closed: judged from its files)
    folder = tmp_path / "crest"
    folder.mkdir()
    for f in FIXTURES.iterdir():
        shutil.copy(f, folder / f.name)
    logs = []
    engine = QueueEngine(orca_path="x", workspace_root=str(tmp_path),
                         callbacks=QueueCallbacks(log=lambda m, lv, c="": logs.append(m)))
    crest = make_calc("crest", kind="crest_conf", state=CalcState.RUNNING)
    p = placeholder("sp")
    engine._walk = [crest, p]

    engine._crest_monitor_and_finish(crest, 0, folder / "crest.out", "crest",
                                     already_done=True)

    assert crest.state == CalcState.DONE
    assert [c.name for c in engine._walk] == ["crest", "sp_c1", "sp_c2"]
    assert "[crest] expanded sp into 2 copies (+0 downstream)" in logs


def test_a_store_wired_run_expands_in_the_store(store, tmp_path):
    # the live queue (P29): the engine walks the store's own list, under its
    # lock, and the replacement goes through the store (saved, version bumped)
    _lane(store)
    _add(store, "mol-crest", kind="crest_conf", xyz=H2, node="n0")
    _add(store, "mol-SP", **_ph(filter_top=1))
    _add(store, "mol-Opt", kind="opt", geometry_source="reference", ref_name="mol-SP",
         after="mol-SP", node="n2")
    harness = EngineHarness(tmp_path, budget=ResourceBudget(max_jobs=4, cores=64,
                                                           ram_mb=10**6))
    engine = QueueEngine(orca_path="x", workspace_root=str(tmp_path),
                         callbacks=QueueCallbacks(
                             log=lambda m, lv, c="": harness.logs.append((m, lv)),
                             expand_placeholders=store.expand_conformer_placeholders),
                         queue_lock=store._lock,
                         budget=ResourceBudget(max_jobs=4, cores=64, ram_mb=10**6))
    harness.engine = engine
    engine._run_calc = engine._run_mlip_calc = harness._fake_run_calc
    crest_runs_in(harness, tmp_path, "mol-crest")

    t = threading.Thread(target=engine.run_all, args=(store._calcs,), daemon=True)
    t.start()
    t.join(10)
    assert not t.is_alive()

    assert store.names() == ["mol-crest", "mol-SP_c1", "mol-SP_c2", "mol-Opt_c1", "mol-Opt_c2"]
    # top 1 by conformer energy: c1 runs, c2 and what hangs off it filtered
    assert sorted(harness.calls) == ["mol-Opt_c1", "mol-SP_c1", "mol-crest"]
    assert store.get("mol-SP_c2").state == CalcState.FILTERED
    assert store.get("mol-Opt_c2").state == CalcState.FILTERED
    again = QueueStore()
    again.load_session()
    assert again.get("mol-SP") is None and again.get("mol-Opt_c1") is not None


def test_the_pure_expansion_needs_a_done_conformer_search():
    crest = make_calc("crest", kind="crest_conf")
    p = placeholder("p")
    calcs = [crest, p]
    assert expand_conformer_placeholders(calcs, "crest", 2) is None
    crest.state = CalcState.DONE
    assert expand_conformer_placeholders(calcs, "crest", 2)["members"] == 2
    assert expand_conformer_placeholders(calcs, "crest", 2) is None
