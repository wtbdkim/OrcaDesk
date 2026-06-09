"""
Run a single ORCA job as a DETACHED subprocess and monitor it by tailing its
output file.

Design (changed so a run can survive ORCAdesk closing):

* ORCA writes its OWN stdout straight to the ``.out`` file (``stdout=<file>``),
  rather than us piping it through Python. A pipe would block ORCA the moment
  our reader goes away (i.e. when the app closes); writing the file directly
  lets the job keep running headless. It is also ORCA's canonical usage
  (``orca x.inp > x.out``).
* The process is started in its own group/session so it is not torn down with
  the parent and so we can kill the whole tree (launcher + orca_* / MPI workers).
* Live log + progress come from TAILING the ``.out`` file, which works
  identically whether we launched the process this session or are reattaching to
  one left running by a previous session.

Lifecycle verbs:
  launch()  -> start ORCA detached; returns (pid, create_time) to persist.
  adopt()   -> attach to a (pid, create_time) from a previous session.
  monitor() -> tail the .out and block until the process exits, is cancelled,
               or is detached.
  cancel()  -> kill the process tree (the run failed/aborted).
  detach()  -> stop monitoring but LEAVE ORCA running (used on app shutdown).

Thread-safety: ``launch``/``monitor`` run on the queue worker thread; ``cancel``
and ``detach`` are signalled from the UI/server thread. Shared state (the Popen
handle and the pid/create_time identity) is guarded by ``_lock``; the cancel and
detach signals are ``threading.Event``s. cancel() only sets its event — the
monitor loop performs the actual (bounded) kill on its next tick, so the UI
thread never blocks waiting on the process to die.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

from .procutil import process_matches, kill_tree, create_time_of


LogCallback = Callable[[str], None]

# How often the monitor re-reads the growing .out file.
_TAIL_POLL = 0.35   # seconds
# Bound on reaping our own child after it exits (avoids a POSIX zombie).
_REAP_TIMEOUT = 5.0


class OrcaRunError(RuntimeError):
    pass


class OrcaCancelled(OrcaRunError):
    """Raised when a run is stopped by the user — distinct from a real failure so
    the queue can mark the calc CANCELLED (not FAILED) and not block dependents."""
    pass


class OrcaDetached(OrcaRunError):
    """Raised when monitoring stops because the app is shutting down. The ORCA
    process is deliberately LEFT RUNNING so it can be reattached next launch —
    so the queue must NOT mark the calc finished/failed."""
    pass


class OrcaRunner:
    """Launches / reattaches to / monitors ORCA for a single .inp file."""

    def __init__(self, orca_path: str):
        self.orca_path = orca_path
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None   # set only for this-session launches
        self._pid: Optional[int] = None
        self._create_time: Optional[float] = None
        self._cancel_event = threading.Event()
        self._detach_event = threading.Event()

    # ---- control signals (called from the UI/server thread) ----
    def cancel(self) -> None:
        # Only signal; the monitor loop does the (bounded) kill on its next tick
        # so the caller's thread stays responsive.
        self._cancel_event.set()

    def detach(self) -> None:
        # Stop monitoring but leave ORCA running (app shutdown / reattach later).
        self._detach_event.set()

    # ---- launch / reattach (worker thread) ----
    def launch(self, input_path: Path, output_path: Path) -> Tuple[int, float]:
        """Start ORCA detached, writing its own stdout to ``output_path``.
        Returns (pid, create_time) so the session can persist and reattach."""
        if not self.orca_path or not Path(self.orca_path).exists():
            raise OrcaRunError(
                f"ORCA executable not found: '{self.orca_path}'. "
                "Set the correct path in Settings."
            )
        self._cancel_event.clear()
        self._detach_event.clear()
        input_path = Path(input_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [str(self.orca_path), str(input_path)]
        creationflags = 0
        start_new_session = False
        if sys.platform.startswith("win"):
            # no console window + own process group so the tree is killable and
            # the child isn't tied to the parent's console lifetime.
            creationflags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        else:
            start_new_session = True   # setsid: survives parent, killpg-able

        # ORCA writes the .out itself; the child inherits its own copy of the
        # handle, so we drop the parent's copy right after spawning.
        out_f = open(output_path, "w", encoding="utf-8", errors="replace")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(input_path.parent),
                stdout=out_f,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                start_new_session=start_new_session,
            )
        except OSError as e:
            out_f.close()
            raise OrcaRunError(f"Failed to launch ORCA: {e}") from e
        finally:
            try:
                out_f.close()
            except OSError:
                pass

        create_time = create_time_of(proc.pid) or 0.0
        with self._lock:
            self._proc = proc
            self._pid = proc.pid
            self._create_time = create_time
        return proc.pid, create_time

    def adopt(self, pid: int, create_time: Optional[float]) -> None:
        """Reattach to an ORCA process started in a previous session (we have no
        Popen handle for it — liveness is checked via psutil)."""
        self._cancel_event.clear()
        self._detach_event.clear()
        with self._lock:
            self._proc = None
            self._pid = int(pid)
            self._create_time = create_time

    @staticmethod
    def end_position(output_path: Path) -> int:
        """Current end-of-file offset of ``output_path`` as a TEXT-mode tell()
        cookie compatible with monitor()'s seek() — NOT a byte size (os.path.
        getsize would desync on CRLF). Used to start a reattach's tail at the
        current EOF so the already-written output isn't re-streamed. 0 if the
        file can't be read yet."""
        try:
            with open(output_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                return f.tell()
        except OSError:
            return 0

    # ---- monitor (worker thread, blocks) ----
    def monitor(self, output_path: Path,
                on_line: Optional[LogCallback] = None,
                start_pos: int = 0) -> None:
        """Tail ``output_path`` from ``start_pos`` and block until the process
        exits, is cancelled, or is detached. Raises OrcaCancelled / OrcaDetached
        accordingly; returns normally when ORCA finishes on its own.

        ``start_pos`` (a tell() cookie from end_position()) lets a REATTACH begin
        at the current end of file so the output written before the app closed
        isn't re-streamed into the live log; a fresh launch leaves it 0 to read
        from the start. The full graph history is rebuilt separately by the UI
        from the .out on disk."""
        output_path = Path(output_path)
        pos = int(start_pos)
        buf = ""

        def _drain() -> None:
            nonlocal pos, buf
            try:
                with open(output_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while True:
                nl = buf.find("\n")
                if nl < 0:
                    break
                line = buf[:nl].rstrip("\r")
                buf = buf[nl + 1:]
                if on_line is not None:
                    on_line(line)

        while True:
            _drain()
            if self._cancel_event.is_set():
                self._kill()
                raise OrcaCancelled("Cancelled by user.")
            if self._detach_event.is_set():
                raise OrcaDetached("Monitoring stopped; ORCA left running.")
            if not self._is_alive():
                _drain()  # capture any trailing output written just before exit
                if buf.strip() and on_line is not None:
                    on_line(buf.rstrip("\r\n"))
                break
            time.sleep(_TAIL_POLL)

        # reap our own child so it doesn't linger as a zombie (POSIX); harmless
        # no-op for an adopted foreign process (no Popen handle).
        with self._lock:
            proc = self._proc
        if proc is not None:
            try:
                proc.wait(timeout=_REAP_TIMEOUT)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                pass

    # ---- helpers ----
    def _is_alive(self) -> bool:
        with self._lock:
            proc = self._proc
            pid = self._pid
            create_time = self._create_time
        if proc is not None:
            return proc.poll() is None
        return process_matches(pid, create_time)

    def _kill(self) -> None:
        with self._lock:
            proc = self._proc
            pid = self._pid
            create_time = self._create_time
        kill_tree(pid, create_time)
        if proc is not None:
            try:
                proc.wait(timeout=_REAP_TIMEOUT)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                pass
