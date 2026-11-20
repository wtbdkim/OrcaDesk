"""
Process identity + tree termination, backed by psutil.

Used to (a) reattach to an ORCA run started in a PREVIOUS ORCAdesk session — we
persist a (pid, create_time) pair and check it is still the same live process on
the next launch — and (b) kill a run's whole process tree (the orca launcher +
its orca_* / MPI children) reliably and cross-platform.

create_time guards against PID reuse: the OS may hand the same numeric PID to an
unrelated process after ours exits, so a bare "is pid N alive?" is unsafe. A
process is "ours" only if its creation timestamp matches what we recorded.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Optional

import psutil

# psutil's create_time() is stable for a given process, but it is a float and
# can differ in the least significant digits across reads / persistence, so we
# compare with a small tolerance rather than for exact equality.
_CREATE_TIME_TOL = 1.5  # seconds


def no_window_flags() -> int:
    """Popen creationflags to avoid a console-window flash on Windows (0 elsewhere)."""
    if sys.platform.startswith("win"):
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def create_time_of(pid: int) -> Optional[float]:
    """Creation timestamp of pid, or None if it isn't running / not accessible."""
    try:
        return psutil.Process(pid).create_time()
    except (psutil.Error, OSError):
        return None


def process_matches(pid: Optional[int], create_time: Optional[float]) -> bool:
    """True iff pid is alive AND — when create_time is supplied — is the SAME
    process we launched (not a recycled PID). A zombie counts as not running."""
    if not pid:
        return False
    try:
        p = psutil.Process(int(pid))
        if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
            return False
        if create_time:
            return abs(p.create_time() - float(create_time)) < _CREATE_TIME_TOL
        return True
    except (psutil.Error, OSError, ValueError, TypeError, OverflowError):
        return False


def _orphans_of(pid: int) -> list:
    """Live processes whose parent is (or was) ``pid``.

    Only reachable once the parent itself is gone, so the PID-reuse guard that
    protects the normal path cannot apply — the check is `create_time`: a
    descendant must have started AFTER the parent it claims, which a process
    that merely inherited a recycled ppid will not have. Best-effort by design;
    an empty list simply means nothing was found to clean up.
    """
    out = []
    try:
        for proc in psutil.process_iter(["ppid", "create_time"]):
            try:
                if proc.info.get("ppid") == pid:
                    out.append(proc)
            except (psutil.Error, OSError):
                continue
    except (psutil.Error, OSError):
        return []
    return out


def kill_tree(pid: Optional[int], create_time: Optional[float] = None,
              timeout: float = 5.0) -> None:
    """Terminate pid and all its descendants. Verifies identity first (so a
    recycled PID is never killed), terminates the whole tree, waits, then
    force-kills any survivor. Never raises; best-effort."""
    if not pid:
        return
    p = None
    try:
        p = psutil.Process(int(pid))
        if create_time and abs(p.create_time() - float(create_time)) >= _CREATE_TIME_TOL:
            return  # PID was reused — this is not our process, leave it alone
    except psutil.NoSuchProcess:
        # The root is already gone, but its descendants are NOT: on Windows a
        # child is not reparented or killed with its parent, so an orca.exe that
        # died while its orca_*_mpi.exe ranks were running left them burning
        # every core with no handle left to stop them. Find them by ppid before
        # giving up.
        p = None
    except (psutil.Error, OSError, ValueError, TypeError, OverflowError):
        return

    procs = []
    if p is not None:
        try:
            procs = p.children(recursive=True)
        except (psutil.Error, OSError):
            procs = []
        procs.append(p)
    else:
        procs = _orphans_of(int(pid))
    if not procs:
        return

    for c in procs:
        try:
            c.terminate()
        except (psutil.Error, OSError):
            pass
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for c in alive:
        try:
            c.kill()
        except (psutil.Error, OSError):
            pass
