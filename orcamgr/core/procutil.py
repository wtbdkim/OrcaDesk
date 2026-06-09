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

from typing import Optional

import psutil

# psutil's create_time() is stable for a given process, but it is a float and
# can differ in the least significant digits across reads / persistence, so we
# compare with a small tolerance rather than for exact equality.
_CREATE_TIME_TOL = 1.5  # seconds


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
    except (psutil.Error, OSError, ValueError):
        return False


def kill_tree(pid: Optional[int], create_time: Optional[float] = None,
              timeout: float = 5.0) -> None:
    """Terminate pid and all its descendants. Verifies identity first (so a
    recycled PID is never killed), terminates the whole tree, waits, then
    force-kills any survivor. Never raises; best-effort."""
    if not pid:
        return
    try:
        p = psutil.Process(int(pid))
        if create_time and abs(p.create_time() - float(create_time)) >= _CREATE_TIME_TOL:
            return  # PID was reused — this is not our process, leave it alone
    except (psutil.Error, OSError, ValueError):
        return

    try:
        procs = p.children(recursive=True)
    except (psutil.Error, OSError):
        procs = []
    procs.append(p)

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
