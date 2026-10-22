"""
Tests for the hung-ORCA watchdog in orcamgr/core/runner.py.

ORCA 6.1.1 can print a fatal marker ("error termination") and then never
exit: a CCSD(T) NumFreq whose AUTOCI died on two displacements spun one core
for 14 hours with nothing written in its folder, and the queue showed it
RUNNING the whole time. The monitor now ends such a job — only when a fatal
marker was seen AND the whole run folder has gone silent.

The tests adopt real child processes (sys.executable sleeping) as the "ORCA"
the runner watches, so liveness and the tree kill are the real ones. No ORCA.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import psutil
import pytest

import orcamgr.core.runner as runner_mod
from orcamgr.core.input_generator import StepConfig
from orcamgr.core.procutil import create_time_of
from orcamgr.core.queue import Calculation, QueueCallbacks, QueueEngine
from orcamgr.core.runner import OrcaHung, OrcaRunner
from orcamgr.state.store import CalcState

_NO_WINDOW = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
              if sys.platform.startswith("win") else 0)

FATAL_OUT = (
    "\t<< Calculating gradient on displaced geometry  12 (of  12) >>\n"
    "ORCA finished by error termination in AUTOCI\n"
    "[file orca_numfreq/numfreq_utils.cpp, line 2253]: Skipping calculation . . .\n"
)
HEALTHY_OUT = "\t<< Calculating gradient on displaced geometry   3 (of  12) >>\n"


@pytest.fixture
def sleeper():
    procs = []

    def spawn(seconds: float = 120):
        p = subprocess.Popen(
            [sys.executable, "-c", f"import time; time.sleep({seconds})"],
            creationflags=_NO_WINDOW)
        procs.append(p)
        return p

    yield spawn
    for p in procs:
        try:
            p.kill()
        except OSError:
            pass
        try:
            p.wait(timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            pass


@pytest.fixture(autouse=True)
def fast_watchdog(monkeypatch):
    monkeypatch.setattr(runner_mod, "_HUNG_AFTER", 60.0)
    monkeypatch.setattr(runner_mod, "_HUNG_CHECK", 0.0)
    monkeypatch.setattr(runner_mod, "_TAIL_POLL", 0.05)


def _run_folder(tmp_path, text: str, age: float):
    out = tmp_path / "job" / "job.out"
    out.parent.mkdir()
    out.write_text(text, encoding="utf-8")
    (out.parent / "job_D00011.gbw").write_bytes(b"x")
    stamp = time.time() - age
    for f in out.parent.iterdir():
        os.utime(f, (stamp, stamp))
    return out


def _adopted(proc) -> OrcaRunner:
    r = OrcaRunner("orca-not-needed")
    r.adopt(proc.pid, create_time_of(proc.pid))
    return r


def _gone(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def test_a_fatal_marker_then_a_silent_folder_is_killed_and_reported(tmp_path, sleeper):
    proc = sleeper()
    out = _run_folder(tmp_path, FATAL_OUT, age=3600)
    seen = []

    with pytest.raises(OrcaHung, match="wrote nothing for 60 min"):
        _adopted(proc).monitor(out, on_line=seen.append)

    proc.wait(timeout=10)
    assert _gone(proc.pid)
    assert any("error termination" in ln for ln in seen)   # still logged


def test_a_reattach_finds_the_marker_written_while_orcadesk_was_closed(tmp_path, sleeper):
    # The reattach tails from EOF, so the marker never passes through the
    # tail — it has to be read off the file. This is the 14-hour case.
    proc = sleeper()
    out = _run_folder(tmp_path, HEALTHY_OUT * 5000 + FATAL_OUT, age=3600)

    with pytest.raises(OrcaHung):
        _adopted(proc).monitor(out, start_pos=OrcaRunner.end_position(out))


def test_a_marker_while_the_folder_still_moves_is_left_alone(tmp_path, sleeper):
    # N2O went on computing displacements 9-12 for half an hour after one
    # died; that is not a hang, and the run must end on ORCA's own exit.
    proc = sleeper(1.5)
    out = _run_folder(tmp_path, FATAL_OUT, age=0)

    _adopted(proc).monitor(out)   # returns normally when the process exits


def test_a_silent_folder_without_a_marker_is_left_alone(tmp_path, sleeper):
    # A long CCSD(T) step can be silent for a long time; silence alone is
    # never evidence of a hang.
    proc = sleeper(1.5)
    out = _run_folder(tmp_path, HEALTHY_OUT, age=3600)

    _adopted(proc).monitor(out)


def test_a_hung_job_fails_with_both_the_cause_and_who_ended_it(tmp_path):
    engine = QueueEngine(
        orca_path="orca-not-needed-in-tests",
        workspace_root=str(tmp_path),
        callbacks=QueueCallbacks(log=lambda m, l, _calc="": None,
                                 calc_update=lambda i, c: None))

    class _HungRunner:
        def launch(self, inp_path, out_path):
            out_path.write_text(FATAL_OUT, encoding="utf-8")
            return 4321, 1234.5

        def end_position(self, out_path):
            return 0

        def monitor(self, out_path, on_line=None, start_pos=0):
            raise OrcaHung("ORCA reported an error and then wrote nothing "
                           "for 10 min without exiting; ORCAdesk stopped it.")

    engine._orca_runner_factory = _HungRunner
    calc = Calculation(name="hung", kind="sp", config=StepConfig(kind="sp"))
    calc.xyz = "H 0.0 0.0 0.0"

    engine.run_all([calc])

    assert calc.state is CalcState.FAILED
    assert "error termination" in calc.message.lower()     # from the .out
    assert calc.message.endswith("ORCAdesk stopped it.")   # from the runner
