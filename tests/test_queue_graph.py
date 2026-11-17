"""The queue editor's engine rules (core/queue.py), run WITHOUT ORCA.

A calculation may run ``after`` another one — the edge of the queue editor's
graph — independently of where its geometry comes from:

* ``after`` is a dependency exactly like a geometry reference: the dispatcher
  waits for it, a FAILED/BLOCKED ancestor on EITHER chain blocks, and a
  dangling ``after`` is BLOCKED with a message (never FAILED — that locks, P24);
* an approval gate on the edge holds a row whose ``after`` is DONE until it is
  approved — or passes by itself after a frequency calc with the expected
  imaginary-mode count (auto_pass). A held row is *waiting*: skipped, left
  PENDING, never BLOCKED, and a run left with nothing else ends normally;
* ``ref_point`` irc_f / irc_b starts from an IRC's end points;
* an inp-set template's {{CHARGE}} / {{MULT}} / {{HESS}} are filled at launch,
  the Hessian staged from the parent's folder;
* a template is admitted by the %pal / %maxcore its own text asks for.

Same harness as test_queue_engine (a scripted _run_calc) for the walk, and the
real _run_calc stopped at launch for the pre-launch work.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

import orcamgr.core.queue as queue_mod
from orcamgr.core.input_generator import StepConfig
from orcamgr.core.parser import ParseResult
from orcamgr.core.queue import (CalcState, Calculation, GeometrySource,
                                gate_auto_passes, gate_waiting)
from orcamgr.core.resources import (ResourceBudget, declared_cores,
                                    estimated_ram_mb)
from orcamgr.core.runner import OrcaRunError
from orcamgr.state.inpsets import seed_sets
from orcamgr.state.store import find_dangling_reference

from test_queue_engine import EngineHarness, _LaunchAttempted, _real_engine, make_calc, raiser


def after(calc: Calculation, parent: str, gate: str = "auto",
          approved: bool = False, auto_pass: bool = True) -> Calculation:
    calc.after = parent
    calc.gate = gate
    calc.approved = approved
    calc.auto_pass = auto_pass
    return calc


def done(name: str, kind: str = "sp", n_imaginary: "int | None" = None,
         tmp_path=None) -> Calculation:
    c = make_calc(name, kind=kind, state=CalcState.DONE)
    if n_imaginary is not None:
        c.result = ParseResult(terminated_normally=True, n_imaginary=n_imaginary)
    if tmp_path is not None:
        c.output_path = str(Path(tmp_path) / name / f"{name}.out")
    return c


def run_bounded(harness: EngineHarness, calcs, timeout: float = 10.0) -> None:
    """run_all on a thread, failing the test instead of hanging the suite."""
    t = threading.Thread(target=harness.engine.run_all, args=(calcs,), daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), "the run did not end"


# ---- `after` is a dependency ------------------------------------------------

def test_a_calc_runs_after_its_after_parent_whatever_the_list_order(tmp_path):
    harness = EngineHarness(tmp_path, budget=ResourceBudget(max_jobs=4, cores=64,
                                                            ram_mb=10**6))
    child = after(make_calc("child"), "parent")
    parent = make_calc("parent")
    harness.behaviors["parent"] = lambda c: time.sleep(0.2)

    run_bounded(harness, [child, parent])

    assert harness.calls == ["parent", "child"]
    assert child.state == CalcState.DONE


def test_a_failed_after_parent_blocks_the_child(tmp_path):
    harness = EngineHarness(tmp_path)
    parent = make_calc("parent")
    child = after(make_calc("child"), "parent")
    harness.behaviors["parent"] = raiser(OrcaRunError("SCF did not converge"))

    run_bounded(harness, [parent, child])

    assert parent.state == CalcState.FAILED
    assert child.state == CalcState.BLOCKED
    assert "child" not in harness.calls


def test_blocked_by_walks_after_through_a_pending_parent(tmp_path):
    harness = EngineHarness(tmp_path)
    gp = make_calc("gp", state=CalcState.FAILED)
    parent = after(make_calc("parent"), "gp")
    child = after(make_calc("child"), "parent")
    # the child is listed first: it is blocked by its grandparent on the spot
    run_bounded(harness, [child, parent, gp])

    assert child.state == CalcState.BLOCKED and parent.state == CalcState.BLOCKED
    assert harness.calls == []


def test_blocked_by_walks_after_and_reference_chains_together(tmp_path):
    # child takes its GEOMETRY from `mid`, and mid runs AFTER a failed calc
    harness = EngineHarness(tmp_path)
    bad = make_calc("bad", state=CalcState.FAILED)
    mid = after(make_calc("mid"), "bad")
    child = make_calc("child", ref="mid")

    run_bounded(harness, [child, mid, bad])

    assert child.state == CalcState.BLOCKED
    assert mid.state == CalcState.BLOCKED


def test_a_done_after_parent_ends_the_walk(tmp_path):
    # a DONE parent is self-sufficient: what failed before it does not matter
    harness = EngineHarness(tmp_path)
    gp = make_calc("gp", state=CalcState.FAILED)
    parent = after(make_calc("parent", state=CalcState.DONE), "gp")
    child = after(make_calc("child"), "parent")

    run_bounded(harness, [gp, parent, child])

    assert harness.calls == ["child"]


def test_a_dangling_after_is_blocked_with_a_message_not_failed(tmp_path):
    harness = EngineHarness(tmp_path)
    child = after(make_calc("child"), "gone")

    run_bounded(harness, [child])

    assert child.state == CalcState.BLOCKED
    assert "'gone'" in child.message and "not in the queue" in child.message


def test_a_calc_running_after_itself_is_blocked(tmp_path):
    harness = EngineHarness(tmp_path)
    c = after(make_calc("loop"), "loop")

    run_bounded(harness, [c])

    assert c.state == CalcState.BLOCKED
    assert "after itself" in c.message


def test_an_after_cycle_is_blocked_not_spun_on(tmp_path):
    harness = EngineHarness(tmp_path)
    a = after(make_calc("a"), "b")
    b = after(make_calc("b"), "a")

    run_bounded(harness, [a, b])

    assert a.state == CalcState.BLOCKED and b.state == CalcState.BLOCKED


def test_the_pre_run_screen_refuses_a_dangling_or_self_after():
    assert "runs after 'gone'" in find_dangling_reference(
        [after(make_calc("c"), "gone")])
    assert "after itself" in find_dangling_reference([after(make_calc("c"), "c")])
    assert find_dangling_reference(
        [make_calc("p"), after(make_calc("c"), "p")]) is None
    # a finished row is never re-screened (P24)
    assert find_dangling_reference(
        [after(make_calc("c", state=CalcState.DONE), "gone")]) is None


# ---- the approval gate ----------------------------------------------------------

def test_a_closed_approval_gate_leaves_the_row_waiting_and_the_run_ends(tmp_path):
    harness = EngineHarness(tmp_path)
    parent = done("opt1", kind="opt")
    gated = after(make_calc("sp1"), "opt1", gate="approval")
    downstream = after(make_calc("sp2"), "sp1")
    other = make_calc("other")

    run_bounded(harness, [parent, gated, downstream, other])

    assert harness.calls == ["other"]
    # never BLOCKED, never touched: waiting for the user
    assert gated.state == CalcState.PENDING and gated.message == ""
    assert downstream.state == CalcState.PENDING and downstream.message == ""
    assert "waiting for approval" in harness.log_text()
    assert gate_waiting(gated, parent)
    assert not gate_waiting(downstream, gated)   # its parent is not DONE


def test_a_run_of_nothing_but_waiting_rows_ends_in_parallel_mode_too(tmp_path):
    harness = EngineHarness(tmp_path, budget=ResourceBudget(max_jobs=4, cores=64,
                                                            ram_mb=10**6))
    parent = done("opt1", kind="opt")
    gated = [after(make_calc(f"g{i}"), "opt1", gate="approval") for i in range(3)]

    run_bounded(harness, [parent, *gated])

    assert harness.calls == []
    assert all(g.state == CalcState.PENDING for g in gated)


def test_an_approved_gate_runs(tmp_path):
    harness = EngineHarness(tmp_path)
    parent = done("opt1", kind="opt")
    gated = after(make_calc("sp1"), "opt1", gate="approval", approved=True)

    run_bounded(harness, [parent, gated])

    assert harness.calls == ["sp1"]


def test_a_gate_waits_while_its_parent_runs_then_holds(tmp_path):
    # the gate is asked once the parent is DONE — not before (the row first
    # waits for its parent like any dependent)
    harness = EngineHarness(tmp_path)
    parent = make_calc("opt1", kind="opt")
    gated = after(make_calc("sp1"), "opt1", gate="approval", auto_pass=True)

    run_bounded(harness, [parent, gated])

    assert harness.calls == ["opt1"]               # an opt never auto-passes
    assert gated.state == CalcState.PENDING


def test_approving_during_a_live_run_lets_the_engine_pick_it_up(tmp_path):
    harness = EngineHarness(tmp_path)
    parent = done("opt1", kind="opt")
    gated = after(make_calc("sp1"), "opt1", gate="approval")
    slow = make_calc("slow")
    calcs = [parent, gated, slow]

    def approve_while_running(_c):
        # what an update_calc op does mid-run: a NEW object replaces the row
        with harness.engine._queue_lock:
            approved = after(make_calc("sp1"), "opt1", gate="approval", approved=True)
            calcs[1] = approved
        time.sleep(0.1)

    harness.behaviors["slow"] = approve_while_running
    run_bounded(harness, calcs)

    assert harness.calls == ["slow", "sp1"]
    assert calcs[1].state == CalcState.DONE


@pytest.mark.parametrize("kind, n_imag, auto_pass, runs", [
    ("freq", 0, True, True),
    ("freq", 0, False, False),
    ("freq", 1, True, False),          # not the count a minimum has
    ("ts_freq", 1, True, True),
    ("ts_freq", 1, False, False),
    ("ts_freq", 0, True, False),       # a TS frequency wants exactly one
    ("ts_opt_freq", 1, True, True),
    ("opt", 0, True, False),           # auto-pass is for frequency calcs only
])
def test_auto_pass_after_a_frequency_calc(tmp_path, kind, n_imag, auto_pass, runs):
    harness = EngineHarness(tmp_path)
    parent = done("p", kind=kind, n_imaginary=n_imag)
    gated = after(make_calc("next"), "p", gate="approval", auto_pass=auto_pass)

    run_bounded(harness, [parent, gated])

    assert (harness.calls == ["next"]) is runs
    assert (gated.state == CalcState.PENDING) is (not runs)


def test_auto_pass_parses_the_parent_on_a_miss(tmp_path, monkeypatch):
    # a DONE parent restored from a session has no result in memory
    out = tmp_path / "f" / "f.out"
    out.parent.mkdir()
    out.write_text("ORCA output", encoding="utf-8")
    parent = done("f", kind="freq", tmp_path=tmp_path)
    parsed = []

    def fake_parse(calc):
        parsed.append(calc.name)
        return ParseResult(terminated_normally=True, n_imaginary=1)

    monkeypatch.setattr(queue_mod, "result_from_output", fake_parse)
    harness = EngineHarness(tmp_path)
    gated = after(make_calc("next"), "f", gate="approval")

    run_bounded(harness, [parent, gated])

    assert parsed == ["f"]                       # read once, and kept
    assert parent.result is not None and parent.result.n_imaginary == 1
    assert harness.calls == []                   # 1 imaginary mode: not a minimum
    assert gated.state == CalcState.PENDING


def test_without_a_result_the_snapshot_trusts_what_done_already_says(tmp_path):
    # validate_result is what made a freq DONE, and for a freq that IS the
    # expected count — the poll never parses, and needs not
    parent = done("f", kind="freq")
    gated = after(make_calc("next"), "f", gate="approval")
    assert parent.result is None
    assert gate_auto_passes(gated, parent) is True
    assert gate_waiting(gated, parent) is False


# ---- ref_point: an IRC's end points ---------------------------------------------

def _irc_parent(tmp_path, name="irc1") -> Calculation:
    irc = done(name, kind="irc", tmp_path=tmp_path)
    (tmp_path / name).mkdir(exist_ok=True)
    return irc


def _ref_point_calc(point: str, ref: str = "irc1") -> Calculation:
    c = make_calc("end", ref=ref)
    c.ref_point = point
    return c


def test_ref_point_irc_f_reads_the_forward_end_file(tmp_path):
    engine = _real_engine(tmp_path)
    irc = _irc_parent(tmp_path)
    (tmp_path / "irc1" / "irc1_IRC_F.xyz").write_text(
        "2\nforward end\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74\n", encoding="utf-8")
    (tmp_path / "irc1" / "irc1_IRC_B.xyz").write_text(
        "2\nbackward end\nH 0.0 0.0 0.0\nH 0.0 0.0 0.90\n", encoding="utf-8")
    engine._by_name = {"irc1": irc}

    fwd = engine._resolve_geometry(_ref_point_calc("irc_f"))
    bwd = engine._resolve_geometry(_ref_point_calc("irc_b"))

    assert fwd.splitlines() == ["H 0.0 0.0 0.0", "H 0.0 0.0 0.74"]
    assert bwd.splitlines()[-1] == "H 0.0 0.0 0.90"


def test_ref_point_with_its_end_file_missing_says_precisely_what_is_missing(tmp_path):
    engine = _real_engine(tmp_path)
    irc = _irc_parent(tmp_path)
    (tmp_path / "irc1" / "irc1_IRC_F.xyz").write_text(
        "1\n\nH 0 0 0\n", encoding="utf-8")
    engine._by_name = {"irc1": irc}

    with pytest.raises(OrcaRunError, match=r"no backward IRC end point: irc1_IRC_B\.xyz"):
        engine._resolve_geometry(_ref_point_calc("irc_b"))


def test_ref_point_of_an_irc_that_has_not_finished_is_not_read(tmp_path):
    engine = _real_engine(tmp_path)
    irc = make_calc("irc1", kind="irc")              # PENDING
    engine._by_name = {"irc1": irc}
    with pytest.raises(OrcaRunError, match="no usable geometry yet"):
        engine._resolve_geometry(_ref_point_calc("irc_f"))
    irc.state = CalcState.FAILED
    with pytest.raises(OrcaRunError, match="did not succeed"):
        engine._resolve_geometry(_ref_point_calc("irc_f"))


def test_ref_point_reads_the_irc_folder_from_its_output_path(tmp_path):
    # a session restored from another workspace keeps where the IRC ran
    engine = _real_engine(tmp_path / "ws")
    elsewhere = tmp_path / "old_ws" / "irc1"
    elsewhere.mkdir(parents=True)
    (elsewhere / "irc1_IRC_F.xyz").write_text("1\n\nO 1 2 3\n", encoding="utf-8")
    irc = make_calc("irc1", kind="irc", state=CalcState.DONE)
    irc.output_path = str(elsewhere / "irc1.out")
    engine._by_name = {"irc1": irc}

    assert engine._resolve_geometry(_ref_point_calc("irc_f")) == "O 1 2 3"


# ---- template placeholders -----------------------------------------------------

TEMPLATE = """! B3LYP def2-SVP SP
%pal nprocs 2 end
* xyz {{CHARGE}} {{MULT}}
{{GEOMETRY}}
*
"""

IRC_TEMPLATE = """! B3LYP def2-SVP IRC
%irc
  InitHess read
  Hess_Filename "{{HESS}}"
end
* xyz {{CHARGE}} {{MULT}}
{{GEOMETRY}}
*
"""


def _template_calc(name: str, text: str, kind: str = "sp") -> Calculation:
    c = Calculation(name=name, kind=kind, config=StepConfig(kind=kind))
    c.is_raw = True
    c.raw_text = text
    c.xyz = "H 0.0 0.0 0.0\nH 0.0 0.0 0.74"
    return c


def test_charge_and_multiplicity_are_filled_in_at_launch(tmp_path):
    engine = _real_engine(tmp_path)
    calc = _template_calc("t1", TEMPLATE)
    calc.charge, calc.multiplicity = -1, 2

    with pytest.raises(_LaunchAttempted):
        engine._run_calc(calc, 0)

    inp = (tmp_path / "t1" / "t1.inp").read_text(encoding="utf-8")
    assert "* xyz -1 2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74\n*" in inp
    assert "{{" not in inp
    assert calc.raw_text == TEMPLATE                 # the template is not touched


def test_hess_is_staged_from_the_after_parent_and_named_bare(tmp_path):
    engine = _real_engine(tmp_path)
    (tmp_path / "ts-freq").mkdir()
    (tmp_path / "ts-freq" / "ts-freq.hess").write_text("$hessian\n", encoding="utf-8")
    freq = done("ts-freq", kind="ts_freq", tmp_path=tmp_path)
    calc = _template_calc("irc1", IRC_TEMPLATE, kind="irc")
    calc.after = "ts-freq"
    engine._by_name = {"ts-freq": freq, "irc1": calc}

    with pytest.raises(_LaunchAttempted):          # _stage_irc_hessian is satisfied
        engine._run_calc(calc, 0)

    assert (tmp_path / "irc1" / "ts-freq.hess").read_text(encoding="utf-8") == "$hessian\n"
    inp = (tmp_path / "irc1" / "irc1.inp").read_text(encoding="utf-8")
    assert 'Hess_Filename "ts-freq.hess"' in inp


def test_hess_falls_back_to_the_reference_parent(tmp_path):
    engine = _real_engine(tmp_path)
    (tmp_path / "ts").mkdir()
    (tmp_path / "ts" / "ts.hess").write_text("$hessian\n", encoding="utf-8")
    ts = done("ts", kind="ts_opt", tmp_path=tmp_path)
    ts.result = ParseResult(terminated_normally=True)
    calc = _template_calc("irc2", IRC_TEMPLATE, kind="irc")
    calc.after = "sp-elsewhere"                     # has no .hess
    calc.geometry_source = GeometrySource.REFERENCE
    calc.ref_name = "ts"
    engine._by_name = {"ts": ts, "irc2": calc,
                       "sp-elsewhere": done("sp-elsewhere", tmp_path=tmp_path)}
    engine._resolve_geometry = lambda c: "H 0.0 0.0 0.0"   # not under test

    with pytest.raises(_LaunchAttempted):
        engine._run_calc(calc, 0)

    assert (tmp_path / "irc2" / "ts.hess").exists()


def test_a_missing_hess_fails_before_launch_naming_where_it_looked(tmp_path):
    engine = _real_engine(tmp_path)
    parent = done("freq1", kind="freq", tmp_path=tmp_path)
    calc = _template_calc("irc3", IRC_TEMPLATE, kind="irc")
    calc.after = "freq1"
    engine._by_name = {"freq1": parent, "irc3": calc}

    with pytest.raises(OrcaRunError, match=r"\{\{HESS\}\}.*freq1\.hess"):
        engine._run_calc(calc, 0)
    assert not (tmp_path / "irc3" / "irc3.inp").exists()


# ---- a template is admitted by what its text asks for ----------------------------

def test_a_template_is_admitted_by_its_own_pal_and_maxcore():
    text = TEMPLATE.replace("%pal nprocs 2 end", "%maxcore 3000\n%pal nprocs 8 end")
    calc = _template_calc("big", text)
    calc.config.nprocs, calc.config.maxcore_mb = 1, 100   # hidden form: not what runs
    assert declared_cores(calc) == 8
    assert estimated_ram_mb(calc) == 24000


def test_a_seeded_set_carries_the_users_defaults_literally():
    sp = next(s for s in seed_sets(nprocs=12, maxcore_mb=5000) if s["kind"] == "SP")
    calc = _template_calc("seeded", sp["text"])
    assert declared_cores(calc) == 12
    assert estimated_ram_mb(calc) == 60000
