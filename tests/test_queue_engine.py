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
* P29 -- the walked queue is LIVE: rows added/removed/edited/reordered
  mid-run take effect in the same run (the walk picks the first unhandled
  row of the shared list each iteration)

Everything runs against a tmp_path workspace; no ORCA executable, no user
data directories, no network.
"""

from __future__ import annotations

from pathlib import Path

import threading
import time

import pytest

from orcamgr.core.input_generator import StepConfig
from orcamgr.core.parser import ParseResult
from orcamgr.core.queue import (
    CalcState,
    Calculation,
    GeometrySource,
    QueueCallbacks,
    QueueEngine,
)
from orcamgr.core.resources import ResourceBudget
from orcamgr.core.runner import OrcaCancelled, OrcaRunError


_CALLS_LOCK = threading.Lock()


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

    def __init__(self, tmp_path, skip_names=None, budget=None):
        self.logs: list[tuple[str, str]] = []
        self.tagged: list[tuple[str, str, str]] = []   # (msg, level, calc name)
        self.calls: list[str] = []
        self.behaviors: dict = {}

        def _log(msg, level, calc=""):
            self.logs.append((msg, level))
            self.tagged.append((msg, level, calc))

        callbacks = QueueCallbacks(log=_log, calc_update=lambda index, calc: None)
        self.engine = QueueEngine(
            orca_path="orca-not-needed-in-tests",
            workspace_root=str(tmp_path),
            callbacks=callbacks,
            skip_names=skip_names,
            budget=budget,
        )
        # Instance attributes shadow the bound methods: run_all's
        # self._run_calc(...) resolves to the fake. The MLIP and CREST entry
        # points are faked too — the dispatcher routes those kinds elsewhere,
        # so a test using one would otherwise reach the real backend.
        self.engine._run_calc = self._fake_run_calc
        self.engine._run_mlip_calc = self._fake_run_calc
        self.engine._run_crest_calc = self._fake_run_calc

    def _fake_run_calc(self, calc: Calculation, index: int) -> None:
        with _CALLS_LOCK:
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


# ---- cancel: only the in-flight job is stamped; the plan is preserved ----------
def test_cancel_stamps_only_the_inflight_job_and_leaves_pending_rows_pending(tmp_path):
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
    # a REAL blocked row references its failed parent — its diagnosis is still
    # true, so it must survive the cancel untouched. (A BLOCKED row with a
    # CLEAN ancestry is normalized to PENDING at run start instead — see
    # test_stale_blocked_with_clean_ancestry_is_normalized_at_run_start.)
    blocked_prev = make_calc("blocked_prev", ref="failed_prev",
                             state=CalcState.BLOCKED)
    blocked_prev.message = "Skipped: a dependency failed."

    harness.engine.run_all([first, inflight, pending_tail,
                            done_prev, failed_prev, blocked_prev])

    assert first.state is CalcState.DONE                # finished before the stop
    assert inflight.state is CalcState.CANCELLED        # only the in-flight job
    # the rest of the queued plan is preserved: a cancel stops the run without
    # discarding the pending calcs — pending_tail stays runnable as-is.
    assert pending_tail.state is CalcState.PENDING
    assert done_prev.state is CalcState.DONE            # frozen, not re-stamped
    assert failed_prev.state is CalcState.FAILED        # locked, not re-stamped
    assert failed_prev.message == "SCF did not converge."
    assert blocked_prev.state is CalcState.BLOCKED
    assert blocked_prev.message == "Skipped: a dependency failed."


def test_cancel_leaves_reattach_pending_running_row_untouched(tmp_path):
    # a row still RUNNING further down the queue at cancel time is a reattach-
    # pending job from a previous session (its process may be alive); the cancel
    # breaks out of the walk without touching it, so its pid — the only handle to
    # the process — is preserved rather than dropped (which would orphan a live
    # detached ORCA).
    harness = EngineHarness(tmp_path)

    def cancel_and_abort(_calc: Calculation) -> None:
        harness.engine.cancel()
        raise OrcaCancelled("Cancelled by user.")

    harness.behaviors["inflight"] = cancel_and_abort
    inflight = make_calc("inflight")
    reattach_pending = make_calc("reattach_pending", state=CalcState.RUNNING)
    reattach_pending.pid = 4242
    reattach_pending.create_time = 123.0

    harness.engine.run_all([inflight, reattach_pending])

    assert inflight.state is CalcState.CANCELLED
    assert reattach_pending.state is CalcState.RUNNING   # left for reattach
    assert reattach_pending.pid == 4242                  # handle preserved


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


def test_running_calc_with_dead_process_is_judged_not_relaunched(tmp_path, monkeypatch):
    # A calc restored as RUNNING whose detached ORCA has since exited (e.g.
    # the startup auto-resume was declined and the job finished later) must
    # be judged from the .out it left behind — a fresh launch would TRUNCATE
    # the completed output and recompute it. The engine here has no fake and
    # no valid ORCA path, so falling through to a relaunch would FAIL loudly.
    import orcamgr.core.queue as queue_mod
    monkeypatch.setattr(queue_mod, "process_matches", lambda pid, ct: False)
    logs: list = []
    engine = QueueEngine(
        orca_path="orca-not-needed-in-tests", workspace_root=str(tmp_path),
        callbacks=QueueCallbacks(log=lambda m, l, _calc="": logs.append(m),
                                 calc_update=lambda i, c: None))
    calc = make_calc("orphan", kind="opt", state=CalcState.RUNNING)
    calc.xyz = "H 0.0 0.0 0.0\nH 0.0 0.0 0.74"
    calc.pid = 4242
    calc.create_time = 123.0
    out_path = _write_out(tmp_path, "orphan", PASSING_OPT_OUT)
    calc.output_path = str(out_path)

    engine.run_all([calc])

    assert calc.state is CalcState.DONE
    assert calc.pid is None
    assert "finished while ORCAdesk was closed" in calc.message
    # the completed .out was adopted verbatim, never truncated by a relaunch
    assert out_path.read_text(encoding="utf-8") == PASSING_OPT_OUT


def test_running_calc_dead_without_output_fails_and_blocks_dependents(tmp_path, monkeypatch):
    import orcamgr.core.queue as queue_mod
    monkeypatch.setattr(queue_mod, "process_matches", lambda pid, ct: False)
    engine = QueueEngine(
        orca_path="orca-not-needed-in-tests", workspace_root=str(tmp_path),
        callbacks=QueueCallbacks(log=lambda m, l, _calc="": None,
                                 calc_update=lambda i, c: None))
    calc = make_calc("orphan", kind="opt", state=CalcState.RUNNING)
    calc.pid = 4242
    calc.create_time = 123.0        # no .out anywhere
    dep = make_calc("dep", kind="freq", ref="orphan")

    engine.run_all([calc, dep])

    assert calc.state is CalcState.FAILED
    assert "Interrupted" in calc.message
    assert dep.state is CalcState.BLOCKED


def test_keep_existing_valid_output_is_kept_without_running(tmp_path):
    harness = EngineHarness(tmp_path, skip_names={"keepme"})
    calc = make_calc("keepme", kind="opt")
    # the kept result must structurally belong to this calc (identity check):
    # match the H2 geometry the synthetic .out reports
    calc.xyz = "H 0.0 0.0 0.0\nH 0.0 0.0 0.74"
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


def test_keep_existing_result_for_different_structure_reruns(tmp_path):
    # Workspace folders are never deleted and names are only unique within the
    # current queue: a NEW calc reusing a removed calc's name must not adopt
    # the old .out as its own result. Identity = atom element sequence.
    harness = EngineHarness(tmp_path, skip_names={"keepme"})
    calc = make_calc("keepme", kind="opt")
    calc.xyz = "O 0.0 0.0 0.0"                    # a different molecule than the .out's H2
    _write_out(tmp_path, "keepme", PASSING_OPT_OUT)

    harness.engine.run_all([calc])

    assert harness.calls == ["keepme"]            # honest fallback: it ran
    assert calc.state is CalcState.DONE
    assert "different structure" in harness.log_text()


# ---- failure diagnosis: a stale .out must not mask a pre-launch error (A22) -------
def test_real_prelaunch_failure_ignores_stale_out(tmp_path):
    # The real _run_calc (no fake) dies in geometry resolution — before any
    # launch — while the folder holds a failure-bearing .out from a previous
    # run. The diagnosis must be the resolution error, not the stale .out's.
    logs: list[tuple[str, str]] = []
    engine = QueueEngine(
        orca_path="orca-not-needed-in-tests",
        workspace_root=str(tmp_path),
        callbacks=QueueCallbacks(log=lambda msg, level, _calc="": logs.append((msg, level))),
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


# ---- real _run_calc pre-launch guards (NEB-TS side files, IRC hessian) -------------
# These exercise the REAL _run_calc up to (but never past) runner.launch: the
# sentinel runner turns any launch attempt into a distinct exception, so a test
# can assert both "failed before launch" and "got past the guards".
class _LaunchAttempted(Exception):
    """Sentinel: _run_calc passed its pre-launch guards and tried to launch."""


class _SentinelRunner:
    def launch(self, inp_path, out_path):
        raise _LaunchAttempted()


def _real_engine(tmp_path) -> QueueEngine:
    engine = QueueEngine(orca_path="orca-not-needed-in-tests",
                         workspace_root=str(tmp_path))
    engine._orca_runner_factory = _SentinelRunner
    return engine


RAW_NEB_TEMPLATE = """\
! wB97X-D4 def2-TZVP NEB-TS
%neb
  NEB_End_XYZFile "product.xyz"
  Nimages 8
end
* xyz 0 1
{coords}
*
"""


def _raw_neb_calc(name: str, coords: str = "H 0.0 0.0 0.0\nO 0.0 0.0 1.0",
                  product: str = "") -> Calculation:
    calc = Calculation(name=name, kind="neb_ts",
                       config=StepConfig(kind="neb_ts", neb_product_xyz=product))
    calc.is_raw = True
    calc.raw_text = RAW_NEB_TEMPLATE.format(coords=coords)
    calc.xyz = ""   # hand-pasted raw input: coordinates live only in the text
    calc.state = CalcState.PENDING
    return calc


def test_raw_neb_without_stored_product_fails_before_launch(tmp_path):
    # The raw text keeps the generated "product.xyz" reference but no product
    # geometry is stored: launching would fail cryptically inside ORCA — or
    # silently reuse a stale product.xyz left by an earlier same-named run.
    engine = _real_engine(tmp_path)
    calc = _raw_neb_calc("rawneb")
    with pytest.raises(OrcaRunError, match='references "product.xyz"'):
        engine._run_calc(calc, 0)


def test_raw_neb_with_custom_product_path_still_launches(tmp_path):
    # A hand-written NEB pointing NEB_End_XYZFile at the user's own file is the
    # raw power-user path: no stored product, but no generated reference either.
    engine = _real_engine(tmp_path)
    calc = _raw_neb_calc("rawneb2")
    calc.raw_text = calc.raw_text.replace('"product.xyz"', '"my_product.xyz"')
    with pytest.raises(_LaunchAttempted):
        engine._run_calc(calc, 0)


def test_raw_neb_atom_order_mismatch_fails_before_launch(tmp_path):
    # Raw calcs get the same reactant/product order guard as form calcs; the
    # reactant is read from the coordinate block that actually runs (the text).
    engine = _real_engine(tmp_path)
    calc = _raw_neb_calc("rawneb3",
                         coords="H 0.0 0.0 0.0\nO 0.0 0.0 1.0",
                         product="O 0.0 0.0 0.0\nH 0.0 0.0 1.0")  # swapped order
    with pytest.raises(OrcaRunError, match="Atom order differs"):
        engine._run_calc(calc, 0)


def test_raw_neb_matching_product_writes_side_file_and_launches(tmp_path):
    engine = _real_engine(tmp_path)
    calc = _raw_neb_calc("rawneb4",
                         coords="H 0.0 0.0 0.0\nO 0.0 0.0 1.0",
                         product="H 0.0 0.0 0.5\nO 0.0 0.0 1.5")
    with pytest.raises(_LaunchAttempted):
        engine._run_calc(calc, 0)
    assert (tmp_path / "rawneb4" / "product.xyz").exists()


def test_irc_read_hess_missing_fails_before_launch(tmp_path):
    engine = _real_engine(tmp_path)
    calc = Calculation(name="irc1", kind="irc",
                       config=StepConfig(kind="irc", irc_init_hess="read",
                                         irc_hess_file="TS2.hess"))
    calc.xyz = "H 0.0 0.0 0.0"
    with pytest.raises(OrcaRunError, match="TS2.hess"):
        engine._run_calc(calc, 0)


def test_irc_read_hess_staged_from_referenced_calc_folder(tmp_path):
    # The .hess of a reference-geometry IRC is auto-copied from the referenced
    # calc's folder into the run folder before launch.
    engine = _real_engine(tmp_path)
    (tmp_path / "ts").mkdir()
    (tmp_path / "ts" / "TS2.hess").write_text("$hessian\n", encoding="utf-8")
    calc = Calculation(name="irc2", kind="irc",
                       config=StepConfig(kind="irc", irc_init_hess="read",
                                         irc_hess_file="TS2.hess"))
    calc.geometry_source = GeometrySource.REFERENCE
    calc.ref_name = "ts"
    engine._resolve_geometry = lambda c: "H 0.0 0.0 0.0"   # ref resolution not under test
    with pytest.raises(_LaunchAttempted):
        engine._run_calc(calc, 0)
    assert (tmp_path / "irc2" / "TS2.hess").exists()


def test_raw_neb_custom_end_file_ignores_stale_stored_product(tmp_path):
    # A stored product is dead state for a raw text that points NEB_End_XYZFile
    # at its own file (the _nebProductXyz global survives excursions): no
    # atom-order check against it, no product.xyz side file written.
    engine = _real_engine(tmp_path)
    calc = _raw_neb_calc("rawneb5",
                         coords="H 0.0 0.0 0.0\nO 0.0 0.0 1.0",
                         product="C 0.0 0.0 0.0")   # mismatched leftover
    calc.raw_text = calc.raw_text.replace('"product.xyz"', '"my_product.xyz"')
    with pytest.raises(_LaunchAttempted):
        engine._run_calc(calc, 0)
    assert not (tmp_path / "rawneb5" / "product.xyz").exists()


# ---- keep-existing identity: raw calcs are judged by their raw text ---------------
def test_keep_existing_raw_calc_judged_by_its_raw_text_not_stale_xyz(tmp_path):
    # A converted raw calc keeps calc.xyz from before the editor edit; the
    # coordinate block in the TEXT is what ran and must drive the identity check.
    harness = EngineHarness(tmp_path, skip_names={"keepme"})
    calc = make_calc("keepme", kind="opt")
    calc.is_raw = True
    calc.raw_text = "! Opt\n* xyz 0 1\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74\n*\n"
    calc.xyz = "O 0.0 0.0 0.0"                    # stale pre-edit molecule
    _write_out(tmp_path, "keepme", PASSING_OPT_OUT)   # H2 result on disk

    harness.engine.run_all([calc])

    assert harness.calls == []                    # kept, not recomputed
    assert calc.state is CalcState.DONE


def test_keep_existing_raw_calc_rejects_result_matching_only_stale_xyz(tmp_path):
    harness = EngineHarness(tmp_path, skip_names={"keepme"})
    calc = make_calc("keepme", kind="opt")
    calc.is_raw = True
    calc.raw_text = "! Opt\n* xyz 0 1\nO 0.0 0.0 0.0\n*\n"   # what runs: one O
    calc.xyz = "H 0.0 0.0 0.0\nH 0.0 0.0 0.74"               # stale: matches the .out
    _write_out(tmp_path, "keepme", PASSING_OPT_OUT)          # H2 on disk (foreign)

    harness.engine.run_all([calc])

    assert harness.calls == ["keepme"]            # the foreign result was not adopted
    assert "different structure" in harness.log_text()


def test_keep_existing_element_case_difference_is_not_a_mismatch(tmp_path):
    # ORCA accepts "h"/"CL" and echoes canonical symbols — casing alone must
    # never make a genuinely-matching kept result read as foreign.
    harness = EngineHarness(tmp_path, skip_names={"keepme"})
    calc = make_calc("keepme", kind="opt")
    calc.xyz = "h 0.0 0.0 0.0\nH 0.0 0.0 0.74"
    _write_out(tmp_path, "keepme", PASSING_OPT_OUT)

    harness.engine.run_all([calc])

    assert harness.calls == []
    assert calc.state is CalcState.DONE


def test_keep_existing_same_count_mismatch_names_the_element_sequence(tmp_path):
    # equal atom counts with a different sequence must not log the nonsensical
    # "N atoms on disk vs N in this calculation"
    harness = EngineHarness(tmp_path, skip_names={"keepme"})
    calc = make_calc("keepme", kind="opt")
    calc.xyz = "H 0.0 0.0 0.0\nO 0.0 0.0 1.0"
    _write_out(tmp_path, "keepme", PASSING_OPT_OUT)   # H2 on disk

    harness.engine.run_all([calc])

    assert harness.calls == ["keepme"]
    assert "element sequence differs at atom #2" in harness.log_text()


# ---- shutdown (OrcaDetached) semantics --------------------------------------
from orcamgr.core.runner import OrcaDetached


class _DetachingRunner:
    """launch succeeds (fake pid), monitor immediately reports app shutdown."""
    def launch(self, inp_path, out_path):
        return 4321, 1234.5

    def end_position(self, out_path):
        return 0

    def monitor(self, out_path, on_line=None, start_pos=0):
        raise OrcaDetached("Monitoring stopped; ORCA left running.")


def test_orca_launch_persists_output_path_before_detach(tmp_path):
    # A job that keeps running (or even finishes) while ORCAdesk is closed can
    # only be judged DONE by reconcile_calcs if its output path was persisted
    # AT LAUNCH — not only after a successful monitor. Without that, the
    # "finished while closed -> DONE" branch is unreachable and a successful
    # run gets locked FAILED on the next start.
    updates = []
    engine = QueueEngine(
        orca_path="orca-not-needed-in-tests",
        workspace_root=str(tmp_path),
        callbacks=QueueCallbacks(
            log=lambda m, level, _calc="": None,
            calc_update=lambda i, c: updates.append((c.output_path, c.state)),
        ),
    )
    engine._orca_runner_factory = _DetachingRunner
    calc = make_calc("shutme", kind="sp")

    engine.run_all([calc])

    assert calc.state is CalcState.RUNNING           # left for reattach
    assert calc.pid == 4321
    assert calc.output_path.endswith("shutme.out")   # persisted at launch
    # and the persist callback saw the path BEFORE monitor raised
    assert any(p.endswith("shutme.out") and s is CalcState.RUNNING
               for (p, s) in updates)


def test_mlip_detach_is_stamped_cancelled_not_left_running(tmp_path):
    # The MLIP worker is TERMINATED on shutdown (mlip/runner.py) — leaving the
    # calc RUNNING would let the next launch's reconcile lock it FAILED; a
    # deliberate shutdown stop is CANCELLED (re-runnable).
    harness = EngineHarness(tmp_path)
    mlip = make_calc("mace1", kind="mlip_opt")
    after = make_calc("after")

    def _detach(calc, index):
        raise OrcaDetached("MLIP run terminated on shutdown.")

    harness.engine._run_mlip_calc = _detach
    harness.engine.run_all([mlip, after])

    assert mlip.state is CalcState.CANCELLED
    assert mlip.message == "Stopped on shutdown."
    assert mlip.pid is None
    assert after.state is CalcState.PENDING          # shutdown stops the walk


def test_stale_blocked_with_clean_ancestry_is_normalized_at_run_start(tmp_path):
    # A BLOCKED row whose ancestry is now clean (failed parent removed /
    # reference re-pointed) WILL run this pass — the stale "Skipped: a
    # dependency failed." must be reset UP FRONT, not when the walk happens to
    # reach the row (the queue would visibly label a will-run row as skipped).
    harness = EngineHarness(tmp_path)
    ok = make_calc("ok")
    stale = make_calc("stale", ref="ok", state=CalcState.BLOCKED)
    stale.message = "Skipped: a dependency failed."

    seen_at_first_run = []
    harness.behaviors["ok"] = lambda c: seen_at_first_run.append(
        (stale.state, stale.message))

    harness.engine.run_all([ok, stale])

    assert seen_at_first_run == [(CalcState.PENDING, "")]
    assert stale.state is CalcState.DONE


def test_reattach_prefers_persisted_output_path_over_current_workspace(tmp_path, monkeypatch):
    # A RUNNING calc restored from a previous session may have been launched
    # under a DIFFERENT workspace root. The reattach must tail (and keep) the
    # persisted output_path — deriving it from the current setting tailed a
    # nonexistent file, FAILED-locked the finished job at process exit, and
    # clobbered the pointer the finished-while-closed judgment depends on.
    import orcamgr.core.queue as queue_mod
    monkeypatch.setattr(queue_mod, "process_matches", lambda pid, ct: True)
    engine = QueueEngine(
        orca_path="orca-not-needed-in-tests",
        workspace_root=str(tmp_path / "NEW_WS"),
        callbacks=QueueCallbacks(log=lambda m, l, _calc="": None,
                                 calc_update=lambda i, c: None))
    old_out = tmp_path / "OLD_WS" / "job" / "job.out"
    old_out.parent.mkdir(parents=True)
    old_out.write_text(PASSING_OPT_OUT, encoding="utf-8")
    calc = make_calc("job", kind="opt", state=CalcState.RUNNING)
    calc.pid = 4242
    calc.create_time = 123.0
    calc.output_path = str(old_out)

    seen = {}

    class _AdoptRunner:
        def adopt(self, pid, ct): return None
        def end_position(self, path): return 0

    engine._orca_runner_factory = _AdoptRunner

    def fake_monitor(calc_, index, out_path, start_pos=0):
        seen["path"] = str(out_path)
        calc_.state = CalcState.DONE

    monkeypatch.setattr(engine, "_monitor_and_finish", fake_monitor)
    engine.run_all([calc])

    assert seen["path"] == str(old_out)
    assert calc.output_path == str(old_out)


# ---- how ORCA is invoked ----------------------------------------------------
# ORCA 6 hands its own argument on to orca_startup and the MPI ranks unquoted,
# so an absolute path with a space in it truncates: "Error: Cannot open input
# file C:/Users/John" and an error termination in Startup (verified on 6.1.1,
# serial and 2-rank MPI). The default workspace sits under the user profile, so
# an account name with a space would break every run. Naming the input relative
# to the folder we already run in leaves nothing to split.

def test_orca_is_launched_with_the_bare_input_file_name(tmp_path, monkeypatch):
    import orcamgr.core.runner as runner_mod

    folder = tmp_path / "John Smith" / "work space"
    folder.mkdir(parents=True)
    inp = folder / "h2.inp"
    inp.write_text("! B3LYP\n", encoding="utf-8")
    orca = tmp_path / "orca.exe"
    orca.write_text("", encoding="utf-8")
    seen = {}

    class _Proc:
        pid = 4321

    def fake_popen(cmd, cwd=None, **kw):
        seen["cmd"], seen["cwd"] = cmd, cwd
        return _Proc()

    monkeypatch.setattr(runner_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner_mod, "create_time_of", lambda pid: 1.0, raising=False)

    runner = runner_mod.OrcaRunner(str(orca))
    runner.launch(inp, folder / "h2.out")

    assert seen["cmd"][1] == "h2.inp"          # not the absolute path
    assert " " not in seen["cmd"][1]
    assert Path(seen["cwd"]) == folder         # ...which is why cwd must be it


# ---- P29: the walked queue is LIVE ------------------------------------------
# run_all picks the first unhandled row of the (shared) list each iteration,
# so structural mutations that land mid-run take effect in the SAME run:
# an appended row executes, a removed pending row never does, an edited row
# (a new object under the same list slot) runs in its edited form, and a
# mid-run add referencing an already-failed parent is blocked, not FAILED.

def test_calc_added_mid_run_executes_in_same_run(tmp_path):
    harness = EngineHarness(tmp_path)
    calcs = [make_calc("first")]
    late = make_calc("late")
    harness.behaviors["first"] = lambda _c: calcs.append(late)

    harness.engine.run_all(calcs)

    assert harness.calls == ["first", "late"]
    assert late.state is CalcState.DONE


def test_pending_calc_removed_mid_run_never_executes(tmp_path):
    harness = EngineHarness(tmp_path)
    first = make_calc("first")
    doomed = make_calc("doomed")
    tail = make_calc("tail")
    calcs = [first, doomed, tail]
    harness.behaviors["first"] = lambda _c: calcs.remove(doomed)

    harness.engine.run_all(calcs)

    assert harness.calls == ["first", "tail"]
    assert doomed.state is CalcState.PENDING   # untouched, just never picked


def test_calc_edited_mid_run_runs_the_edited_object(tmp_path):
    harness = EngineHarness(tmp_path)
    first = make_calc("first")
    original = make_calc("target")
    edited = make_calc("target", kind="opt")   # same name, NEW object
    calcs = [first, original]
    harness.behaviors["first"] = lambda _c: calcs.__setitem__(1, edited)

    harness.engine.run_all(calcs)

    assert harness.calls == ["first", "target"]
    assert edited.state is CalcState.DONE
    assert original.state is CalcState.PENDING   # the stale object never ran


def test_mid_run_add_referencing_failed_parent_is_blocked_not_failed(tmp_path):
    harness = EngineHarness(tmp_path)
    boom = make_calc("boom")
    dep = make_calc("dep", ref="boom")
    calcs = [boom]

    def fail_and_spawn_dependent(_c):
        calcs.append(dep)   # lands before the failure is even stamped
        raise OrcaRunError("simulated failure")

    harness.behaviors["boom"] = fail_and_spawn_dependent
    harness.engine.run_all(calcs)

    assert boom.state is CalcState.FAILED
    assert dep.state is CalcState.BLOCKED
    assert dep.message == "Skipped: a dependency failed."
    assert harness.calls == ["boom"]


def test_rows_reordered_mid_run_execute_in_new_order(tmp_path):
    harness = EngineHarness(tmp_path)
    first = make_calc("first")
    second = make_calc("second")
    third = make_calc("third")
    calcs = [first, second, third]
    # move "third" ahead of "second" while "first" runs — the walk follows
    # live list order for the rows it hasn't reached yet
    harness.behaviors["first"] = lambda _c: calcs.insert(1, calcs.pop(2))

    harness.engine.run_all(calcs)

    assert harness.calls == ["first", "third", "second"]


def test_active_names_expose_every_calc_being_handled(tmp_path):
    harness = EngineHarness(tmp_path)
    seen: list = []
    harness.behaviors["only"] = lambda _c: seen.append(
        harness.engine.active_names)

    harness.engine.run_all([make_calc("only")])

    assert seen == [{"only"}]                     # set while handling
    assert harness.engine.active_names == set()   # cleared when the run ends
