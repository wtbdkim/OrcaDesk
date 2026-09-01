"""
Incremental text-mode tail of a growing output file, shared by the ORCA and
CREST runners (both stream a detached process's ``.out`` into the live log).

The subtlety this class exists to keep in ONE place: positions are text-mode
``tell()`` cookies, not byte offsets — on Windows the ``.out`` may contain CRLF,
and ``os.path.getsize`` would desync from a text-mode ``seek``. A partial final
line (no newline yet) stays buffered across drains and is only emitted by
``flush_tail`` when the run is known to be over.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from ..textio import repair_ansi_line

LineCallback = Optional[Callable[[str], None]]


def decode_orca_line(line: str) -> str:
    """One line of an ORCA `.out`, read with ``errors="surrogateescape"``.

    The file is mixed-encoding on Windows: ORCA writes its own ASCII strings as
    UTF-8 but echoes the paths it was given on argv in the process ANSI code
    page. Calc names may be Unicode by design (Korean names are explicitly
    allowed) and the default workspace lives under the user's profile folder, so
    every line quoting the run path used to arrive as a run of U+FFFD. The
    repair itself is shared with the MLIP worker and the installers — see
    orcamgr/textio.py.
    """
    return repair_ansi_line(line)


class FileTailer:
    """Tails one file from a starting ``tell()`` cookie, emitting complete lines."""

    def __init__(self, path: Path, start_pos: int = 0):
        self.path = Path(path)
        self.pos = int(start_pos)
        self.buf = ""

    def drain(self, on_line: LineCallback) -> None:
        """Read text appended since the last drain and emit each complete line
        (CR stripped) to ``on_line``. Unreadable file / no new text is a no-op."""
        try:
            # surrogateescape, not replace: see decode_orca_line — the bytes of a
            # non-ASCII path must survive the read to be repairable.
            with open(self.path, "r", encoding="utf-8",
                      errors="surrogateescape") as f:
                f.seek(self.pos)
                chunk = f.read()
                self.pos = f.tell()
        except OSError:
            return
        if not chunk:
            return
        self.buf += chunk
        # Split the whole buffer ONCE. Slicing it per line (`buf = buf[nl+1:]`)
        # copies the remainder every time, which is quadratic in the text read
        # in one poll — and ORCA emits multi-MB bursts (normal modes, MO
        # coefficients). Measured on the old code: 1 MB 0.07 s, 4 MB 11 s, 8 MB
        # 47 s. The monitor only checks cancel/detach BETWEEN drains, so Stop
        # went unanswered for that whole time while the next chunk grew larger.
        if "\n" not in self.buf:
            return
        *complete, self.buf = self.buf.split("\n")
        if on_line is not None:
            for line in complete:
                on_line(decode_orca_line(line.rstrip("\r")))

    def flush_tail(self, on_line: LineCallback) -> None:
        """Emit the trailing partial line, if any. Call once, after the final
        drain, when the process is known to have exited."""
        if self.buf.strip() and on_line is not None:
            on_line(decode_orca_line(self.buf.rstrip("\r\n")))

    @staticmethod
    def end_position(path: Path) -> int:
        """Current end-of-file offset as a TEXT-mode tell() cookie compatible
        with drain()'s seek — NOT a byte size. Used to start a reattach's tail
        at the current EOF so already-written output isn't re-streamed. 0 if
        the file can't be read yet."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                return f.tell()
        except OSError:
            return 0
