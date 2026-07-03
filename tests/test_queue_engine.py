"""Orchestration tests for orcamgr.core.queue.QueueEngine.run_all, run
WITHOUT ORCA: the per-calc pipeline (_run_calc) is replaced by a scripted
fake, so these tests pin the queue-level invariants in isolation:

* P24 -- DONE is frozen, FAILED is locked (never re-run), CANCELLED re-runs
* P23 -- failure propagation is dependency-scoped, not whole-queue
* cancel never re-stamps a terminal state (diagnoses are preserved)
* graceful drain (request_stop_after_current) leaves the rest PENDING
* keep-existing (skip_names) gates DONE behind validate_result and falls
  back to running -- never to FAILED -- when the on-disk result is unusable
* a pre-launch failure keeps its real cause -- a stale .out from a previous
  run never masks it (A22); post-launch, the .out diagnosis still wins
* an unexpected exception fails one calc and blocks its dependents, but
  never kills the queue

Everything runs against a tmp_path workspace; no ORCA executable, no user
data directories, no network.
"""

from __future__ import annotations

from pathlib import Path

from orcamgr.core.input_generator import StepConfig
from orcamgr.core.parser import ParseResult
from orcamgr.core.queue import (
    CalcState,
    Calculation,
    GeometrySource,
    QueueCallbacks,
    QueueEngine,
)
from orcamgr.core.runner import OrcaCancelled, OrcaRunError


# ---- helpers ---------------------------------------------------------------
def make_calc(name: str, kind: str = "sp",
              state: CalcState = CalcState.PENDING, ref: str = "") -> Calculation:
    calc = Calculation(name=name, kind=kind, config=StepConfig(kind=kind))
    if ref:
        calc.geometry_source = GeometrySource.REFERENCE
        calc.ref_name = ref
    else:
        calc.xyz = "H 0.0 0.0 0.0"
    calc.state = state
    return calc


def raiser(exc: Exception):
    """A behavior that raises `exc` from inside the fake _run_calc."""
    def _raise(_calc: Calculation) -> None:
        raise exc
    return _raise


class EngineHarness:
    """A QueueEngine on a tmp workspace with _run_calc replaced by a fake.

    `behaviors` maps calc name -> callable(calc). The callable may raise to
    simulate a runner failure / cancel; if it returns (or no behavior is
    registered) the fake completes the calc as DONE, the way the real
    _monitor_and_finish would. `calls` records which calcs actually ran.
    """

    def __init__(self, tmp_path, skip_names=None):
        self.logs: list[tuple[str, str]] = []
        self.calls: list[str] = []
        self.behaviors: dict = {}
        callbacks = QueueCallbacks(
            log=lambda msg, level: self.logs.append((msg, level)),
            calc_update=lambda index, calc: None,
        )
        self.engine = QueueEngine(
            orca_path="orca-not-needed-in-tests",
            workspace_root=str(tmp_path),
            callbacks=callbacks,
            skip_names=skip_names,
        )
        # Instance attribute shadows the bound method: run_all's
        # self._run_calc(...) resolves to the fake.
        self.engine._run_calc = self._fake_run_calc

    def _fake_run_calc(self, calc: Calculation, index: int) -> None:
        self.calls.append(calc.name)
        action = self.behaviors.get(calc.name)
        if action is not None:
            action(calc)  # may raise OrcaRunError / OrcaCancelled / anything
        calc.result = ParseResult(terminated_normally=True)
        calc.output_path = str(
            Path(self.engine.workspace_root) / calc.name / f"{calc.name}.out")
        calc.state = CalcState.DONE
        calc.message = "Completed."

    def log_text(self) -> str:
        return "\n".join(msg for msg, _level in self.logs)


# ---- P24: FAILED is locked ---------------------------------------------------
def test_failed_calc_is_locked_and_never_reruns(tmp_path):
    harness = EngineHarness(tmp_path)
    failed = make_calc("bad", state=CalcState.FAILED)
    failed.message = "Optimization did not converge."
    fresh = make_calc("fresh")

    harness.engine.run_all([failed, fresh])

    assert harness.calls == ["fresh"]              # the locked calc never ran
    assert failed.state is CalcState.FAILED
    assert failed.message == "Optimization did not converge."  # diagnosis kept
    assert "locked" in harness.log_text()
    assert fresh.state is CalcState.DONE


def test_dependents_of_previously_failed_calc_are_blocked_up_front(tmp_path):
    harness = EngineHarness(tmp_path)
    failed = make_calc("root", state=CalcState.FAILED)
    child = make_calc("child", ref="root")
    grandchild = make_calc("grandchild", ref="child")   # transitive dependent
    unrelated = make_calc("unrelated")

    harness.engine.run_all([failed, child, grandchild, unrelated])

    assert harness.calls == ["unrelated"]
    assert failed.state is CalcState.FAILED
    assert child.state is CalcState.BLOCKED
    assert grandchild.state is CalcState.BLOCKED
    assert child.message == "Skipped: a dependency failed."
    assert unrelated.state is CalcState.DONE


# ---- P24: DONE is frozen, CANCELLED re-runs ----------------------------------
def test_done_is_never_recomputed_but_cancelled_reruns(tmp_path):
    harness = EngineHarness(tmp_path)
    done = make_calc("finished", state=CalcState.DONE)
    done.message = "Completed."
    cancelled = make_calc("stopped", state=CalcState.CANCELLED)

    harness.engine.run_all([done, cancelled])

    assert harness.calls == ["stopped"]        # only the cancelled calc re-ran
    assert done.state is CalcState.DONE
    assert cancelled.state is CalcState.DONE   # the retry succeeded
    assert "already done" in harness.log_text()


# ---- P23: mid-run failure blocks only dependents ------------------------------
def test_midrun_failure_blocks_only_dependents_and_queue_continues(tmp_path):
    harness = EngineHarness(tmp_path)
    harness.behaviors["root"] = raiser(OrcaRunError("simulated ORCA failure"))
    root = make_calc("root")
    dependent = make_calc("dependent", ref="root")
    transitive = make_calc("transitive", ref="dependent")
    unrelated = make_calc("unrelated")

    harness.engine.run_all([root, dependent, transitive, unrelated])

    assert root.state is CalcState.FAILED
    # no .out exists in the workspace, so the raw runner message is kept
    assert root.message == "simulated ORCA failure"
    assert dependent.state is CalcState.BLOCKED
    assert transitive.state is CalcState.BLOCKED
    assert unrelated.state is CalcState.DONE
    assert harness.calls == ["root", "unrelated"]


# ---- cancel: terminal states keep their diagnosis ------------------------------
def test_cancel_marks_pending_cancelled_but_preserves_terminal_states(tmp_path):
    harness = EngineHarness(tmp_path)

    def cancel_and_abort(_calc: Calculation) -> None:
        # mimic the real flow: the user hits Stop, which sets the engine's
        # cancel event and makes the runner raise OrcaCancelled.
        harness.engine.cancel()
        raise OrcaCancelled("Cancelled by user.")

    harness.behaviors["inflight"] = cancel_and_abort
    first = make_calc("first")
    inflight = make_calc("inflight")
    pending_tail = make_calc("pending_tail")
    done_prev = make_calc("done_prev", state=CalcState.DONE)
    failed_prev = make_calc("failed_prev", state=CalcState.FAILED)
    failed_prev.message = "SCF did not converge."
    blocked_prev = make_calc("blocked_prev", state=CalcState.BLOCKED)
    blocked_prev.message = "Skipped: a dependency failed."

    harness.engine.run_all([first, inflight, pending_tail,
                            done_prev, failed_prev, blocked_prev])

    assert first.state is CalcState.DONE                # finished before the stop
    assert inflight.state is CalcState.CANCELLED        # user intent, not failure
    assert pending_tail.state is CalcState.CANCELLED
    assert done_prev.state is CalcState.DONE            # frozen, not re-stamped
    assert failed_prev.state is CalcState.FAILED        # locked, not re-stamped
    assert failed_prev.message == "SCF did not converge."
    assert blocked_prev.state is CalcState.BLOCKED
    assert blocked_prev.message == "Skipped: a dependency failed."


# ---- graceful drain -------------------------------------------------------------
def test_stop_after_current_finishes_current_and_leaves_rest_pending(tmp_path):
    harness = EngineHarness(tmp_path)

    def request_stop(_calc: Calculation) -> None:
        # the drain request arrives WHILE the current job is running; the job
        # itself still completes (the behavior returns, so the fake marks DONE).
        harness.engine.request_stop_after_current()

    harness.behaviors["current"] = request_stop
    current = make_calc("current")
    later_a = make_calc("later_a")
    later_b = make_calc("later_b")

    harness.engine.run_all([current, later_a, later_b])

    assert current.state is CalcState.DONE
    assert later_a.state is CalcState.PENDING   # still runnable on the next Run
    assert later_b.state is CalcState.PENDING
    assert harness.calls == ["current"]
    assert "left pending" in harness.log_text()


# ---- keep-existing (skip_names) ---------------------------------------------------
# A synthetic ORCA .out that passes validate_result for kind "opt": input echo
# with an Opt keyword (=> is_optimization), the convergence marker, a final
# geometry, a final energy, and normal termination. Markers mirror parser.py.
PASSING_OPT_OUT = """\
                                 * O   R   C   A *

                         Program Version 6.1.1  - RELEASE

================================================================================
                                       INPUT FILE
================================================================================
NAME = keepme.inp
|  1> ! wB97X-D4 def2-TZVP TightSCF Opt
|  2> * xyz 0 1
|  3> H   0.000000   0.000000   0.000000
|  4> H   0.000000   0.000000   0.740000
|  5> *
|  6> ****END OF INPUT****

                    ***********************HURRAY********************
                    ***        THE OPTIMIZATION HAS CONVERGED     ***
                    *************************************************

---------------------------------
CARTESIAN COORDINATES (ANGSTROEM)
---------------------------------
  H      0.000000    0.000000    0.000000
  H      0.000000    0.000000    0.740000

-------------------------   --------------------
FINAL SINGLE POINT ENERGY        -1.170000000000
-------------------------   --------------------

                             ****ORCA TERMINATED NORMALLY****
TOTAL RUN TIME: 0 days 0 hours 0 minutes 3 seconds 42 msec
"""

# A synthetic .out of a run that died before finishing: no normal-termination
# marker, so validate_result must reject it.
ABORTED_OPT_OUT = """\
                         Program Version 6.1.1  - RELEASE

================================================================================
                                       INPUT FILE
================================================================================
NAME = keepme.inp
|  1> ! wB97X-D4 def2-TZVP TightSCF Opt
|  2> * xyz 0 1
|  3> H   0.000000   0.000000   0.000000
|  4> *
|  5> ****END OF INPUT****

ORCA finished by error termination in SCF
"""


def _write_out(tmp_path, name: str, text: str) -> Path:
    out_dir = tmp_path / name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.out"
    out_path.write_text(text, encoding="utf-8")
    return out_path


def test_keep_existing_valid_output_is_kept_without_running(tmp_path):
    harness = EngineHarness(tmp_path, skip_names={"keepme"})
    calc = make_calc("keepme", kind="opt")
    out_path = _write_out(tmp_path, "keepme", PASSING_OPT_OUT)

    harness.engine.run_all([calc])

    assert harness.calls == []                    # never launched
    assert calc.state is CalcState.DONE
    assert calc.message == "Kept existing result (not recomputed)."
    assert calc.output_path == str(out_path)
    assert calc.result is not None
    assert calc.result.opt_converged
    assert calc.result.geometry                   # usable for downstream refs
    assert "kept existing result" in harness.log_text().lower()


def test_keep_existing_invalid_output_falls_back_to_running_not_failed(tmp_path):
    harness = EngineHarness(tmp_path, skip_names={"keepme"})
    observed = {}

    def record_output_path(calc: Calculation) -> None:
        # capture what the run sees AFTER the failed keep-attempt
        observed["output_path_at_run"] = calc.output_path

    harness.behaviors["keepme"] = record_output_path
    calc = make_calc("keepme", kind="opt")
    _write_out(tmp_path, "keepme", ABORTED_OPT_OUT)

    harness.engine.run_all([calc])

    assert harness.calls == ["keepme"]            # honest fallback: it ran
    assert calc.state is CalcState.DONE           # ... and the run succeeded
    assert "existing result invalid" in harness.log_text()
    # a bad keep-attempt must never mark the calc FAILED
    assert not any("FAILED" in msg for msg, _lvl in harness.logs)
    # ... and must not leave a stale output_path pointing at the bad file
    assert observed["output_path_at_run"] == ""


def test_keep_existing_missing_output_falls_back_to_running(tmp_path):
    harness = EngineHarness(tmp_path, skip_names={"keepme"})
    calc = make_calc("keepme", kind="opt")       # no .out written at all

    harness.engine.run_all([calc])

    assert harness.calls == ["keepme"]
    assert calc.state is CalcState.DONE
    assert "no existing result found" in harness.log_text()


# ---- failure diagnosis: a stale .out must not mask a pre-launch error (A22) -------
def test_real_prelaunch_failure_ignores_stale_out(tmp_path):
    # The real _run_calc (no fake) dies in geometry resolution — before any
    # launch — while the folder holds a failure-bearing .out from a previous
    # run. The diagnosis must be the resolution error, not the stale .out's.
    logs: list[tuple[str, str]] = []
    engine = QueueEngine(
        orca_path="orca-not-needed-in-tests",
        workspace_root=str(tmp_path),
        callbacks=QueueCallbacks(log=lambda msg, level: logs.append((msg, level))),
    )
    calc = make_calc("child", kind="opt", ref="ghost")   # ref not in the queue
    _write_out(tmp_path, "child", ABORTED_OPT_OUT)       # stale diagnosis on disk

    engine.run_all([calc])

    assert calc.state is CalcState.FAILED
    assert calc.message == "Referenced calculation 'ghost' not found in queue."
    assert "error termination" not in calc.message       # the stale .out stayed out


def test_keep_existing_fallback_prelaunch_failure_keeps_real_cause(tmp_path):
    # The A22 reproduction: a keep-existing result is rejected (so its
    # failure-bearing .out stays on disk), then the fallback run dies before
    # ORCA launches. The user must see the pre-launch cause.
    harness = EngineHarness(tmp_path, skip_names={"keepme"})
    harness.behaviors["keepme"] = raiser(
        OrcaRunError("No coordinates provided (direct source is empty)."))
    calc = make_calc("keepme", kind="opt")
    _write_out(tmp_path, "keepme", ABORTED_OPT_OUT)

    harness.engine.run_all([calc])

    assert harness.calls == ["keepme"]                   # the fallback ran
    assert calc.state is CalcState.FAILED
    assert calc.message == "No coordinates provided (direct source is empty)."
    assert "error termination" not in calc.message


def test_postlaunch_failure_still_reads_out_diagnosis(tmp_path):
    # Counterpart: once ORCA has launched, the .out on disk IS this attempt's
    # output, so the parsed diagnosis must still win over the runner message.
    harness = EngineHarness(tmp_path)

    def launch_then_die(_calc: Calculation) -> None:
        # what the real _run_calc does right after runner.launch() succeeds
        harness.engine._orca_launched = True
        raise OrcaRunError("ORCA exited with code 1")

    harness.behaviors["job"] = launch_then_die
    calc = make_calc("job", kind="opt")
    _write_out(tmp_path, "job", ABORTED_OPT_OUT)

    harness.engine.run_all([calc])

    assert calc.state is CalcState.FAILED
    assert "error termination" in calc.message           # parsed from the .out
    assert calc.message != "ORCA exited with code 1"


# ---- defensive handling: unexpected exceptions -----------------------------------
def test_unexpected_exception_fails_calc_and_blocks_dependents_only(tmp_path):
    harness = EngineHarness(tmp_path)
    harness.behaviors["root"] = raiser(ValueError("boom"))
    root = make_calc("root")
    dependent = make_calc("dependent", ref="root")
    unrelated = make_calc("unrelated")

    harness.engine.run_all([root, dependent, unrelated])

    assert root.state is CalcState.FAILED
    assert "Unexpected error: boom" in root.message
    assert dependent.state is CalcState.BLOCKED
    assert unrelated.state is CalcState.DONE      # the queue survived
    assert harness.calls == ["root", "unrelated"]


# ---- kind routing: mlip_* runs outside the ORCA pipeline ---------------------------
def test_mlip_kind_routes_to_mlip_pipeline_not_orca(tmp_path):
    harness = EngineHarness(tmp_path)
    mlip_calls: list[str] = []

    def fake_run_mlip(calc: Calculation, index: int) -> None:
        mlip_calls.append(calc.name)
        calc.state = CalcState.DONE
        calc.message = "Completed."

    harness.engine._run_mlip_calc = fake_run_mlip
    orca_job = make_calc("orca_job", kind="sp")
    mlip_job = make_calc("mlip_job", kind="mlip_opt")

    harness.engine.run_all([orca_job, mlip_job])

    assert harness.calls == ["orca_job"]          # ORCA pipeline
    assert mlip_calls == ["mlip_job"]             # MLIP pipeline
    assert mlip_job.state is CalcState.DONE
