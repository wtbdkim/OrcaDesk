"""Parallel queue runs: admission by core/RAM budget, and what may overlap.

The engine can run several calculations at once (``budget.max_jobs`` > 1, see
``core/resources.py``). These pin the scheduling contract: what must overlap,
what must not, how a reference waits for its parent now that queue order no
longer guarantees it, and what happens to a calculation bigger than the whole
machine. The per-calc pipeline itself is faked (``EngineHarness`` in
test_queue_engine) — nothing here launches a real process.
"""
from __future__ import annotations

import threading
import time

from orcamgr.core.queue import CalcState
from orcamgr.core.resources import ResourceBudget
from orcamgr.core.runner import OrcaCancelled, OrcaRunError

from test_queue_engine import EngineHarness, make_calc, raiser


def _wide(max_jobs: int = 4) -> ResourceBudget:
    """A budget that limits only the job count (cores/RAM effectively free)."""
    return ResourceBudget(max_jobs=max_jobs, cores=4096, ram_mb=4_000_000)


class _Overlap:
    """Records the peak number of calculations running at the same time."""

    def __init__(self, hold: float = 0.05):
        self._lock = threading.Lock()
        self._live = 0
        self._hold = hold
        self.peak = 0

    def behavior(self, _calc=None) -> None:
        with self._lock:
            self._live += 1
            self.peak = max(self.peak, self._live)
        time.sleep(self._hold)
        with self._lock:
            self._live -= 1


# ---- what runs together ------------------------------------------------------

def test_independent_calcs_run_at_the_same_time(tmp_path):
    # A barrier is the deterministic form of "these must overlap": if the engine
    # serialized them the first wait() would time out, break the barrier and
    # fail the calc.
    harness = EngineHarness(tmp_path, budget=_wide(2))
    gate = threading.Barrier(2, timeout=20)
    harness.behaviors["a"] = lambda _c: gate.wait()
    harness.behaviors["b"] = lambda _c: gate.wait()
    a, b = make_calc("a"), make_calc("b")

    harness.engine.run_all([a, b])

    assert (a.state, b.state) == (CalcState.DONE, CalcState.DONE)


def test_max_jobs_one_keeps_the_queue_sequential(tmp_path):
    # The default budget is the classic one-at-a-time queue, in order.
    harness = EngineHarness(tmp_path, budget=ResourceBudget(max_jobs=1))
    overlap = _Overlap()
    harness.behaviors["a"] = overlap.behavior
    harness.behaviors["b"] = overlap.behavior

    harness.engine.run_all([make_calc("a"), make_calc("b")])

    assert overlap.peak == 1
    assert harness.calls == ["a", "b"]


def test_core_budget_limits_how_many_run_together(tmp_path):
    # Four job slots, but only 8 cores: two 6-core calcs cannot share them.
    harness = EngineHarness(
        tmp_path, budget=ResourceBudget(max_jobs=4, cores=8, ram_mb=4_000_000))
    overlap = _Overlap()
    calcs = []
    for name in ("a", "b"):
        c = make_calc(name)
        c.config.nprocs = 6
        harness.behaviors[name] = overlap.behavior
        calcs.append(c)

    harness.engine.run_all(calcs)

    assert overlap.peak == 1
    assert all(c.state is CalcState.DONE for c in calcs)


def test_core_budget_admits_what_fits(tmp_path):
    # Same 8 cores, but 4-core calcs: two of them fit exactly, so they overlap.
    harness = EngineHarness(
        tmp_path, budget=ResourceBudget(max_jobs=4, cores=8, ram_mb=4_000_000))
    gate = threading.Barrier(2, timeout=20)
    calcs = []
    for name in ("a", "b"):
        c = make_calc(name)
        c.config.nprocs = 4
        harness.behaviors[name] = lambda _c: gate.wait()
        calcs.append(c)

    harness.engine.run_all(calcs)

    assert all(c.state is CalcState.DONE for c in calcs)


def test_ram_budget_limits_how_many_run_together(tmp_path):
    # Cores are free but memory is not: ORCA's %maxcore is PER CORE, so each of
    # these reserves 4 x 2000 MB and only one fits in a 10 GB budget.
    harness = EngineHarness(
        tmp_path, budget=ResourceBudget(max_jobs=4, cores=4096, ram_mb=10_000))
    overlap = _Overlap()
    calcs = []
    for name in ("a", "b"):
        c = make_calc(name)
        c.config.nprocs = 4
        c.config.maxcore_mb = 2000
        harness.behaviors[name] = overlap.behavior
        calcs.append(c)

    harness.engine.run_all(calcs)

    assert overlap.peak == 1


def test_oversized_calc_runs_alone_instead_of_starving(tmp_path):
    # A calculation bigger than the whole budget must still run (on its own) —
    # otherwise it would sit PENDING forever, with nothing able to free enough.
    harness = EngineHarness(
        tmp_path, budget=ResourceBudget(max_jobs=4, cores=4, ram_mb=4_000_000))
    big = make_calc("big")
    big.config.nprocs = 64

    harness.engine.run_all([big])

    assert big.state is CalcState.DONE
    assert "running it on its own" in harness.log_text()


# ---- dependencies ------------------------------------------------------------

def test_a_reference_waits_for_its_parent_even_when_listed_first(tmp_path):
    # Sequential running got this for free. In parallel the child would be
    # picked while its parent is still RUNNING and die in geometry resolution,
    # so the dispatcher defers it instead of failing it.
    harness = EngineHarness(tmp_path, budget=_wide(4))
    child = make_calc("child", ref="parent")
    parent = make_calc("parent", kind="opt")

    harness.engine.run_all([child, parent])          # child listed FIRST

    assert harness.calls == ["parent", "child"]
    assert (parent.state, child.state) == (CalcState.DONE, CalcState.DONE)


def test_a_failed_parent_still_blocks_its_dependent_in_parallel(tmp_path):
    harness = EngineHarness(tmp_path, budget=_wide(4))
    harness.behaviors["parent"] = raiser(OrcaRunError("boom"))
    parent = make_calc("parent", kind="opt")
    child = make_calc("child", ref="parent")

    harness.engine.run_all([parent, child])

    assert parent.state is CalcState.FAILED
    assert child.state is CalcState.BLOCKED
    assert "child" not in harness.calls


def test_circular_references_are_blocked_not_hung(tmp_path):
    # Every remaining row waits on a reference that can never complete: without
    # the unreachable-row guard the dispatcher would spin forever.
    harness = EngineHarness(tmp_path, budget=_wide(4))
    a = make_calc("a", ref="b")
    b = make_calc("b", ref="a")

    harness.engine.run_all([a, b])

    assert (a.state, b.state) == (CalcState.BLOCKED, CalcState.BLOCKED)
    assert harness.calls == []
    assert "circular geometry reference" in harness.log_text()


# ---- stopping, reporting -----------------------------------------------------

def test_active_names_holds_every_job_in_flight(tmp_path):
    harness = EngineHarness(tmp_path, budget=_wide(2))
    gate = threading.Barrier(2, timeout=20)
    seen: list = []

    def _hold(_c):
        gate.wait()
        seen.append(harness.engine.active_names)

    harness.behaviors["a"] = _hold
    harness.behaviors["b"] = _hold

    harness.engine.run_all([make_calc("a"), make_calc("b")])

    assert {"a", "b"} in seen                      # both held at once
    assert harness.engine.active_names == set()    # cleared when the run ends


def test_cancel_stops_every_job_in_flight(tmp_path):
    harness = EngineHarness(tmp_path, budget=_wide(2))
    gate = threading.Barrier(2, timeout=20)

    def _cancel_here(_c):
        gate.wait()                     # both jobs are in flight
        harness.engine.cancel()         # what the Cancel button does
        raise OrcaCancelled("Cancelled by user.")

    harness.behaviors["a"] = _cancel_here
    harness.behaviors["b"] = _cancel_here
    a, b, rest = make_calc("a"), make_calc("b"), make_calc("rest")

    harness.engine.run_all([a, b, rest])

    assert (a.state, b.state) == (CalcState.CANCELLED, CalcState.CANCELLED)
    assert rest.state is CalcState.PENDING       # the queued plan is preserved


def test_log_lines_carry_the_calculation_they_came_from(tmp_path):
    # With several jobs interleaving into one buffer, the tag is the only thing
    # that can route a line back to its job (the raw ORCA tail carries no name
    # of its own). Engine-level lines stay untagged.
    harness = EngineHarness(tmp_path, budget=_wide(2))
    harness.behaviors["tagged"] = raiser(OrcaRunError("boom"))

    harness.engine.run_all([make_calc("tagged")])

    tags = {calc for (_m, _l, calc) in harness.tagged}
    assert "tagged" in tags
    assert any(calc == "tagged" and "FAILED" in msg
               for (msg, _l, calc) in harness.tagged)


def test_resource_usage_reports_budget_and_occupancy(tmp_path):
    harness = EngineHarness(
        tmp_path, budget=ResourceBudget(max_jobs=2, cores=32, ram_mb=64_000))
    gate = threading.Barrier(2, timeout=20)
    seen: list = []

    def _hold(_c):
        gate.wait()
        seen.append(harness.engine.resource_usage())

    a = make_calc("a")
    a.config.nprocs = 3
    b = make_calc("b")
    b.config.nprocs = 5
    harness.behaviors["a"] = _hold
    harness.behaviors["b"] = _hold

    harness.engine.run_all([a, b])

    usage = seen[0]
    assert usage["jobs"] == 2 and usage["max_jobs"] == 2
    assert usage["cores_used"] == 8            # 3 + 5, both in flight
    assert usage["cores_budget"] == 32
    assert usage["ram_budget_mb"] == 64_000
