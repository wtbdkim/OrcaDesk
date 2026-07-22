"""
Tests for orcamgr/core/procutil.py — process identity and tree termination.

Contract reference (PRINCIPLES.md):
  P21 — process identity is (pid, create_time); never kill (or claim as ours)
        a process whose creation timestamp does not match what we recorded,
        because the OS may recycle the numeric PID.

The tests spawn real child processes (sys.executable running tiny sleep
scripts) so identity and tree-kill are exercised against the actual OS, then
always reap them in finally blocks. No ORCA, no network.
"""

from __future__ import annotations

import subprocess
import sys
import time

import psutil
import pytest

from orcamgr.core.procutil import create_time_of, kill_tree, process_matches


# offset far beyond the 1.5 s tolerance procutil compares with
WRONG_CREATE_TIME_OFFSET = 3600.0

_NO_WINDOW = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
              if sys.platform.startswith("win") else 0)


def _spawn_sleeper(seconds: int = 120, **popen_kw) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds})"],
        creationflags=_NO_WINDOW, **popen_kw)


def _reap(proc: subprocess.Popen) -> None:
    """Best-effort cleanup so a failing assertion never leaks a child."""
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        pass


def _wait_until_gone(pid: int, create_time: float, timeout: float = 15.0) -> bool:
    """Poll until (pid, create_time) no longer matches a live process."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_matches(pid, create_time):
            return True
        time.sleep(0.1)
    return not process_matches(pid, create_time)


# ---------------------------------------------------------------------------
# process_matches
# ---------------------------------------------------------------------------

def test_process_matches_live_child_with_recorded_create_time():
    proc = _spawn_sleeper()
    try:
        ct = create_time_of(proc.pid)
        assert ct is not None
        assert process_matches(proc.pid, ct) is True
        # without a recorded create_time it degrades to a liveness check
        assert process_matches(proc.pid, None) is True
    finally:
        _reap(proc)


def test_process_matches_rejects_wrong_create_time_pid_reuse_guard():
    # P21: same live pid + a creation timestamp that isn't ours -> NOT our
    # process (this is exactly the recycled-PID case after a reboot/exit)
    proc = _spawn_sleeper()
    try:
        ct = create_time_of(proc.pid)
        assert ct is not None
        assert process_matches(proc.pid, ct + WRONG_CREATE_TIME_OFFSET) is False
        assert process_matches(proc.pid, ct - WRONG_CREATE_TIME_OFFSET) is False
    finally:
        _reap(proc)


def test_process_matches_false_after_process_exits():
    proc = _spawn_sleeper()
    ct = create_time_of(proc.pid)
    assert ct is not None
    _reap(proc)  # kill + wait
    assert _wait_until_gone(proc.pid, ct)


def test_process_matches_false_for_missing_or_bogus_pid():
    assert process_matches(None, None) is False
    assert process_matches(0, None) is False
    assert process_matches(0, time.time()) is False


# ---------------------------------------------------------------------------
# kill_tree
# ---------------------------------------------------------------------------

def test_kill_tree_terminates_whole_tree_including_grandchild():
    # parent (python) spawns a grandchild (python) and prints its pid, so the
    # tree is python -> python, like orca launching its orca_* / MPI children
    parent_code = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "print(p.pid, flush=True)\n"
        "time.sleep(120)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", parent_code],
                            stdout=subprocess.PIPE, text=True,
                            creationflags=_NO_WINDOW)
    grandchild_pid = None
    try:
        line = proc.stdout.readline()
        grandchild_pid = int(line.strip())
        parent_ct = create_time_of(proc.pid)
        grandchild_ct = create_time_of(grandchild_pid)
        assert parent_ct is not None and grandchild_ct is not None

        kill_tree(proc.pid, parent_ct)

        assert _wait_until_gone(proc.pid, parent_ct), "parent survived kill_tree"
        assert _wait_until_gone(grandchild_pid, grandchild_ct), \
            "grandchild survived kill_tree"
    finally:
        _reap(proc)
        if grandchild_pid is not None:
            try:
                psutil.Process(grandchild_pid).kill()
            except (psutil.Error, OSError):
                pass


def test_kill_tree_with_wrong_create_time_leaves_process_alone():
    # P21: a mismatched creation timestamp means the PID was (or could have
    # been) recycled — kill_tree must not touch it
    proc = _spawn_sleeper()
    try:
        ct = create_time_of(proc.pid)
        assert ct is not None
        kill_tree(proc.pid, ct + WRONG_CREATE_TIME_OFFSET)
        time.sleep(0.3)  # give a wrongful termination a moment to land
        assert process_matches(proc.pid, ct) is True, \
            "kill_tree killed a process whose identity did not match"
    finally:
        _reap(proc)


def test_kill_tree_without_create_time_kills_the_tree():
    # create_time is optional: when the caller has none, kill_tree still works
    proc = _spawn_sleeper()
    ct = create_time_of(proc.pid)
    try:
        assert ct is not None
        kill_tree(proc.pid, None)
        assert _wait_until_gone(proc.pid, ct)
    finally:
        _reap(proc)


def test_kill_tree_never_raises_on_missing_or_invalid_targets():
    # best-effort contract: no input may raise
    kill_tree(None)
    kill_tree(0)
    # bogus pid; the ancient create_time guarantees that even in the
    # astronomically unlikely case the pid exists, nothing gets killed
    kill_tree(2 ** 22 + 12345, 1.0)
    kill_tree("not-a-pid")              # bad type -> swallowed internally

    # an already-exited pid must be a silent no-op too
    proc = subprocess.Popen([sys.executable, "-c", "pass"],
                            creationflags=_NO_WINDOW)
    ct = create_time_of(proc.pid)
    proc.wait(timeout=30)
    kill_tree(proc.pid, ct)
    kill_tree(proc.pid, (ct or 0) + WRONG_CREATE_TIME_OFFSET)


def test_process_matches_wrong_typed_pid_is_false_not_a_crash():
    # reconcile passes session-restored values; a wrong-typed pid (corrupted
    # session) must read as "no such process", never TypeError/OverflowError
    from orcamgr.core.procutil import kill_tree, process_matches
    assert process_matches([1], 1.0) is False
    assert process_matches({"pid": 1}, None) is False
    assert process_matches(float("inf"), None) is False
    kill_tree([1])            # must not raise
    kill_tree(float("inf"))   # must not raise
