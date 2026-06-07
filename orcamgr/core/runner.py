"""
Run a single ORCA job as a subprocess, streaming output line-by-line.

Improvements over the original ``call_orca`` (which used ``communicate()`` and
only returned once the whole job finished):

* live stdout streaming via a line callback, so the GUI log updates in real time
* output is both written to the .out file AND forwarded to the callback
* cancellation support (terminate the running process)
* no shell pipe redirection; we capture stdout in Python and write it ourselves,
  which is portable and avoids quoting issues with paths containing spaces
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional


LogCallback = Callable[[str], None]


class OrcaRunError(RuntimeError):
    pass


class OrcaCancelled(OrcaRunError):
    """Raised when a run is stopped by the user — distinct from a real failure so
    the queue can mark the calc CANCELLED (not FAILED) and not block dependents."""
    pass


def _kill_process_tree(proc: "subprocess.Popen") -> None:
    """Kill the ORCA launcher AND its children (orca_* modules, MPI workers).
    Popen.terminate() on Windows only kills the launcher PID, orphaning the
    workers — they keep burning cores and locking scratch/.gbw files."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if sys.platform.startswith("win"):
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (OSError, ValueError, subprocess.SubprocessError):
        try:
            proc.terminate()
        except OSError:
            pass


class OrcaRunner:
    """Executes ORCA on a single .inp file."""

    def __init__(self, orca_path: str):
        self.orca_path = orca_path
        self._proc: Optional[subprocess.Popen] = None
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        _kill_process_tree(self._proc)

    def run(
        self,
        input_path: Path,
        output_path: Path,
        on_line: Optional[LogCallback] = None,
    ) -> None:
        """
        Run ORCA on ``input_path``, writing stdout to ``output_path``.

        ``on_line`` (if given) receives each stdout line as it arrives.
        Raises OrcaRunError on non-zero exit or if ORCA cannot be launched.
        """
        if not self.orca_path or not Path(self.orca_path).exists():
            raise OrcaRunError(
                f"ORCA executable not found: '{self.orca_path}'. "
                "Set the correct path in Settings."
            )

        self._cancelled = False
        input_path = Path(input_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # ORCA must be invoked with the full path to the input file so that
        # parallel runs find their resources; run in the input's directory.
        cmd = [str(self.orca_path), str(input_path)]

        # On Windows, avoid popping up a console window AND put ORCA in its own
        # process group so cancel() can kill the whole tree. On POSIX, start a new
        # session (setsid) so os.killpg can reach the MPI workers.
        creationflags = 0
        start_new_session = False
        if sys.platform.startswith("win"):
            creationflags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        else:
            start_new_session = True

        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(input_path.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                universal_newlines=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
                start_new_session=start_new_session,
            )
        except OSError as e:
            raise OrcaRunError(f"Failed to launch ORCA: {e}") from e

        with open(output_path, "w", encoding="utf-8", errors="replace") as out:
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                out.write(line)
                out.flush()
                if on_line is not None:
                    on_line(line.rstrip("\n"))
                if self._cancelled:
                    break

        ret = self._proc.wait()

        if self._cancelled:
            raise OrcaCancelled("Cancelled by user.")
        if ret != 0:
            raise OrcaRunError(
                f"ORCA exited with code {ret}. See {output_path.name} for details."
            )
