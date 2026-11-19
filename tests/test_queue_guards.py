"""Guards that keep a queue row out of a state it cannot leave, run WITHOUT ORCA.

* approving a gate mid-run keeps the live row, so the engine's keep-existing
  answer (pinned to row identity) still holds for it;
* an MLIP inp-set text is the whole MLIP setting: an edit that drops a key
  returns it to the default instead of keeping the old value;
* a text that reads a parent's Hessian with no parent, and a ref_point on a
  parent that writes no IRC end points, are refused when added; at run time a
  missing parent Hessian / IRC end file stamps the row BLOCKED (editable),
  never FAILED (locked, P24);
* the gate's auto-pass never parses an output under the store lock (P37);
* a dropped .xyz whose header undercounts its atoms is refused;
* an inp set holding an unknown {{...}} placeholder is refused when saved.
"""

from __future__ import annotations

import threading
import time

import pytest

import orcamgr.core.queue as queue_mod
import orcamgr.state.store as store_mod
from orcamgr.core.input_generator import StepConfig
from orcamgr.core.parser import ParseResult
from orcamgr.core.queue import (CalcState, OrcaBlocked, QueueCallbacks,
                                QueueEngine)
from orcamgr.core.resources import ResourceBudget
from orcamgr.state import inpsets
from orcamgr.state.inpsets import seed_sets
from orcamgr.state.store import QueueStore, xyz_file_entries

from test_queue_engine import EngineHarness, _real_engine, make_calc, raiser

H2 = "H 0.0 0.0 0.0\nH 0.0 0.0 0.74"


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(store_mod, "user_data_root", lambda: tmp_path / "ud")
    (tmp_path / "ud").mkdir()
    return QueueStore()


def _wait(pred, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.02)
    return False


def _orca_set():
    return next(s for s in seed_sets() if s["kind"] == "ORCA")


def _template(name, kind, text, **extra):
    d = {"name": name, "kind": kind, "geometry_source": "direct", "xyz": H2,
         "is_raw": True, "raw_text": text, "set_name": "level"}
    d.update(extra)
    return d


HESS_TEXT = """! B3LYP def2-SVP IRC
%irc
  InitHess read
  Hess_Filename "{{HESS}}"
end
* xyz {{CHARGE}} {{MULT}}
{{GEOMETRY}}
*
"""


# ---- B1: approving mid-run keeps keep-existing ------------------------------

def test_approving_a_gate_mid_run_keeps_the_keep_existing_answer(store, tmp_path,
                                                                  monkeypatch):
    # any existing output "validates" (stands in for a real, valid G.out)
    monkeypatch.setattr(queue_mod, "result_from_output",
                        lambda c: ParseResult(terminated_normally=True))
    ws = tmp_path / "ws"
    (ws / "G").mkdir(parents=True)
    (ws / "G" / "G.out").write_text("the result the user chose to keep\n")
    calls, release = [], threading.Event()

    def fake(calc, index):
        calls.append(calc.name)
        if calc.name == "L":
            release.wait(5)
        calc.result = ParseResult(terminated_normally=True)
        calc.state = CalcState.DONE

    P = make_calc("P")
    P.state = CalcState.DONE
    G = make_calc("G")
    G.after, G.gate, G.approved = "P", "approval", False
    L = make_calc("L")
    for c in (P, G, L):
        store.add(c)
    live_g = store.get("G")

    def factory():
        eng = QueueEngine("orca", str(ws), QueueCallbacks(
            log=lambda *a, **k: None, calc_update=lambda i, c: store.touch()),
            skip_names={"G"}, queue_lock=store._lock,
            budget=ResourceBudget(max_jobs=2, cores=64, ram_mb=10**6))
        eng._run_calc = fake
        return eng

    store.start_run(factory)
    try:
        assert _wait(lambda: "L" in calls)
        store.apply_ops([{"op": "update_calc", "name": "G", "calc": {"approved": True}}])
        assert store.get("G") is live_g and live_g.approved is True
        assert _wait(lambda: live_g.state == CalcState.DONE, 3)
    finally:
        # A failed assert must not leave the run going past the fixture: its
        # last autosave would land wherever user_data_root points by then.
        release.set()
        store.wait_for_run(5)

    assert "G" not in calls                      # kept, never re-run
    assert "Kept existing result" in store.get("G").message


def test_an_approval_batch_that_fails_leaves_the_row_unapproved(store):
    P = make_calc("P")
    P.state = CalcState.DONE
    G = make_calc("G")
    G.after, G.gate = "P", "approval"
    store.add(P)
    store.add(G)
    with pytest.raises(ValueError):
        store.apply_ops([{"op": "update_calc", "name": "G", "calc": {"approved": True}},
                         {"op": "remove_calc", "name": "nope"}])
    assert store.get("G").approved is False


# ---- B2: an MLIP template edit resets what the text no longer writes --------

def test_an_mlip_template_edit_resets_a_setting_the_text_dropped(store):
    store.apply_ops([{"op": "add_calc", "calc": {
        "name": "m1", "kind": "mlip_opt", "geometry_source": "direct", "xyz": H2,
        "is_raw": True, "raw_text": "model MACE-OFF medium\ndevice cuda\nenv envA\nnprocs 3\n",
        "set_name": "s"}}])
    c = store.get("m1")
    assert (c.config.mlip_device, c.config.mlip_env_id, c.config.nprocs) == ("cuda", "envA", 3)
    store.apply_ops([{"op": "update_calc", "name": "m1", "calc": {
        "raw_text": "model MACE-OFF small\n", "is_raw": True}}])
    c = store.get("m1")
    d = StepConfig()
    assert c.config.mlip_model == "MACE-OFF small"
    assert c.config.mlip_device == d.mlip_device
    assert c.config.mlip_env_id == d.mlip_env_id
    assert c.config.nprocs == d.nprocs


# ---- B3: a Hessian with nowhere to come from ---------------------------------

def test_a_hess_text_with_no_parent_is_refused_when_added(store):
    with pytest.raises(ValueError, match="reads a Hessian"):
        store.apply_ops([{"op": "add_calc",
                          "calc": _template("irc1", "irc", HESS_TEXT)}])
    assert store.list() == []


def test_a_hess_text_after_a_parent_or_on_a_reference_is_accepted(store):
    store.apply_ops([
        {"op": "add_calc", "calc": {"name": "f", "kind": "freq",
                                    "geometry_source": "direct", "xyz": H2}},
        {"op": "add_calc", "calc": _template("irc1", "irc", HESS_TEXT, after="f")},
        {"op": "add_calc", "calc": _template(
            "irc2", "irc", HESS_TEXT, geometry_source="reference", ref_name="f",
            xyz="")},
    ])
    assert [c.name for c in store.list()] == ["f", "irc1", "irc2"]


def test_cutting_the_edge_of_a_hess_text_is_refused(store):
    store.apply_ops([
        {"op": "add_calc", "calc": {"name": "f", "kind": "freq",
                                    "geometry_source": "direct", "xyz": H2}},
        {"op": "add_calc", "calc": _template("irc1", "irc", HESS_TEXT, after="f")},
    ])
    with pytest.raises(ValueError, match="reads a Hessian"):
        store.apply_ops([{"op": "update_calc", "name": "irc1", "calc": {"after": ""}}])
    assert store.get("irc1").after == "f"


def test_a_level_irc_whose_own_block_reads_a_hessian_needs_a_parent(store):
    text = HESS_TEXT.replace("{{HESS}}", "ts.hess").replace("IRC", "{{JOB}}", 1)
    with pytest.raises(ValueError, match="reads a Hessian"):
        store.apply_ops([{"op": "add_calc", "calc": _template("irc1", "irc", text)}])


def test_a_job_irc_without_a_parent_is_accepted(store):
    # the {{JOB}} IRC computes its own Hessian (calc_anfreq) when there is no
    # parent .hess -- nothing to refuse
    store.apply_ops([{"op": "add_calc",
                      "calc": _template("irc1", "irc", _orca_set()["text"])}])
    assert store.get("irc1") is not None


def test_a_restored_hess_text_without_a_parent_is_not_evicted():
    from orcamgr.state.store import calc_from_dict
    c = calc_from_dict(_template("irc1", "irc", HESS_TEXT), restoring=True)
    assert c.name == "irc1"


def test_a_missing_parent_hessian_at_run_time_blocks_instead_of_failing(tmp_path):
    # the parent ran, but wrote no .hess: the row is BLOCKED (editable), with
    # where it looked, and it is re-tried on the next Run
    engine = _real_engine(tmp_path)
    parent = make_calc("freq1", kind="freq", state=CalcState.DONE)
    parent.output_path = str(tmp_path / "freq1" / "freq1.out")
    calc = make_calc("irc1", kind="irc")
    calc.is_raw, calc.raw_text, calc.xyz, calc.after = True, HESS_TEXT, H2, "freq1"
    engine.run_all([parent, calc])
    assert calc.state == CalcState.BLOCKED
    assert "freq1.hess" in calc.message


# ---- B4: a ref_point on a parent that writes no IRC end ----------------------

def test_a_ref_point_on_a_non_irc_parent_is_refused(store):
    with pytest.raises(ValueError, match="not an IRC"):
        store.apply_ops([
            {"op": "add_calc", "calc": {"name": "sp1", "kind": "sp",
                                        "geometry_source": "reference",
                                        "ref_name": "opt1", "ref_point": "irc_f"}},
            {"op": "add_calc", "calc": {"name": "opt1", "kind": "opt",
                                        "geometry_source": "direct", "xyz": H2}},
        ])
    assert store.list() == []


def test_a_ref_point_on_an_irc_parent_is_accepted_and_its_type_then_pinned(store):
    store.apply_ops([
        {"op": "add_calc", "calc": {"name": "irc1", "kind": "irc",
                                    "geometry_source": "direct", "xyz": H2}},
        {"op": "add_calc", "calc": {"name": "sp1", "kind": "sp",
                                    "geometry_source": "reference",
                                    "ref_name": "irc1", "ref_point": "irc_b"}},
    ])
    assert store.get("sp1").ref_point == "irc_b"
    # changing the parent's Type away from IRC would orphan the end point
    with pytest.raises(ValueError, match="not an IRC"):
        store.apply_ops([{"op": "update_calc", "name": "irc1", "calc": {"kind": "opt"}}])


def test_store_add_refuses_a_ref_point_on_a_non_irc_parent(store):
    store.add(make_calc("opt1", kind="opt"))
    c = make_calc("sp1", ref="opt1")
    c.ref_point = "irc_f"
    with pytest.raises(ValueError, match="not an IRC"):
        store.add(c)


def test_a_missing_irc_end_file_at_run_time_blocks_instead_of_failing(tmp_path):
    engine = _real_engine(tmp_path)
    irc = make_calc("irc1", kind="irc", state=CalcState.DONE)
    irc.output_path = str(tmp_path / "irc1" / "irc1.out")
    (tmp_path / "irc1").mkdir()
    end = make_calc("end", ref="irc1")
    end.ref_point = "irc_f"
    engine.run_all([irc, end])
    assert end.state == CalcState.BLOCKED
    assert "irc1_IRC_F.xyz" in end.message


def test_an_orca_blocked_error_blocks_the_row_and_its_dependents(tmp_path):
    h = EngineHarness(tmp_path)
    a = make_calc("a")
    b = make_calc("b", ref="a")
    h.behaviors["a"] = raiser(OrcaBlocked("no Hessian"))
    h.engine.run_all([a, b])
    assert a.state == CalcState.BLOCKED and "no Hessian" in a.message
    assert b.state == CalcState.BLOCKED


# ---- B5: the gate never parses under the store lock --------------------------

def test_the_gate_auto_pass_does_not_hold_the_store_lock_on_a_parse(store, tmp_path,
                                                                    monkeypatch):
    ws = tmp_path / "ws"
    (ws / "F").mkdir(parents=True)
    (ws / "F" / "F.out").write_text("x")
    F = make_calc("F", kind="freq", state=CalcState.DONE)
    F.output_path = str(ws / "F" / "F.out")       # restored: no in-memory result
    C = make_calc("C")
    C.after, C.gate = "F", "approval"
    store.add(F)
    store.add(C)
    parsed = []
    monkeypatch.setattr(queue_mod, "result_from_output",
                        lambda c: parsed.append(c.name) or time.sleep(1.5))

    def factory():
        eng = QueueEngine("orca", str(ws), QueueCallbacks(
            log=lambda *a, **k: None, calc_update=lambda i, c: store.touch()),
            queue_lock=store._lock)
        eng._run_calc = lambda c, i: setattr(c, "state", CalcState.DONE)
        return eng

    store.start_run(factory)
    store.wait_for_run(5)
    assert parsed == []
    assert store.get("C").state == CalcState.DONE   # passed on DONE's guarantee


# ---- B6: an .xyz header that undercounts --------------------------------------

UNDERCOUNT = "2\ncomment\nO 0 0 0\nH 0 0 0.96\nH 0.93 0 -0.24\n"


def test_a_dropped_xyz_with_an_undercounting_header_is_refused(store):
    out = store.add_xyz_systems([("water.xyz", UNDERCOUNT)], _orca_set(), "_sp", "sp")
    assert out["added"] == []
    assert "says 2 atoms" in str(out)
    assert store.list() == []


def test_xyz_file_entries_refuses_an_undercounting_header(tmp_path):
    p = tmp_path / "water.xyz"
    p.write_text(UNDERCOUNT, encoding="utf-8")
    ok = tmp_path / "ok.xyz"
    ok.write_text("3\nc\nO 0 0 0\nH 0 0 0.96\nH 0.93 0 -0.24\n\n", encoding="utf-8")
    bad, good = xyz_file_entries([p, ok])
    assert "says 2 atoms" in bad["error"] and bad["xyz"] == ""
    assert good["error"] == "" and len(good["xyz"].splitlines()) == 3


# ---- B7: an unknown placeholder in an inp set ----------------------------------

@pytest.mark.parametrize("bad", ["{{JOBS}}", "{{job}}", "{{ HESS }}", "{{GEOM}}"])
def test_an_inp_set_with_an_unknown_placeholder_is_refused_on_save(tmp_path, bad):
    text = _orca_set()["text"] + f"# {bad}\n"
    with pytest.raises(ValueError, match="Unknown placeholder"):
        inpsets.save_set({"name": "x", "kind": "ORCA", "text": text}, root=tmp_path)


def test_an_inp_set_with_only_known_placeholders_saves(tmp_path):
    text = _orca_set()["text"].replace("* xyz 0 1", "* xyz {{CHARGE}} {{MULT}}")
    saved = inpsets.save_set({"name": "x", "kind": "ORCA", "text": text}, root=tmp_path)
    assert saved["text"] == text


# ---- Q4: a system recharge moves only the members that had its old value ----

def test_a_recharge_leaves_a_member_set_apart_alone_and_undo_restores(store):
    store.apply_ops([
        {"op": "add_system", "system": {"id": "s1", "name": "M", "charge": 0,
                                        "multiplicity": 1, "inputs": []}},
        {"op": "add_calc", "calc": {"name": "M-opt", "kind": "opt", "system": "s1",
                                    "geometry_source": "direct", "xyz": H2}},
        {"op": "add_calc", "calc": {"name": "M-trip", "kind": "sp", "system": "s1",
                                    "multiplicity": 3, "geometry_source": "direct",
                                    "xyz": H2}},
    ])
    store.apply_ops([{"op": "update_system", "id": "s1",
                      "patch": {"charge": 1, "multiplicity": 2}}])
    opt, trip = store.get("M-opt"), store.get("M-trip")
    assert (opt.charge, opt.multiplicity) == (1, 2)
    assert (trip.charge, trip.multiplicity) == (1, 3)   # its own multiplicity kept
    store.apply_ops([{"op": "update_system", "id": "s1",
                      "patch": {"charge": 0, "multiplicity": 1}}])
    opt, trip = store.get("M-opt"), store.get("M-trip")
    assert (opt.charge, opt.multiplicity) == (0, 1)
    assert (trip.charge, trip.multiplicity) == (0, 3)
