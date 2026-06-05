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

import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional


LogCallback = Callable[[str], None]


class OrcaRunError(RuntimeError):
    pass


class OrcaRunner:
    """Executes ORCA on a single .inp file."""

    def __init__(self, orca_path: str):
        self.orca_path = orca_path
        self._proc: Optional[subprocess.Popen] = None
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except OSError:
                pass

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

        # On Windows, avoid popping up a console window.
        creationflags = 0
        if sys.platform.startswith("win"):
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

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
            raise OrcaRunError("Cancelled by user.")
        if ret != 0:
            raise OrcaRunError(
                f"ORCA exited with code {ret}. See {output_path.name} for details."
            )
