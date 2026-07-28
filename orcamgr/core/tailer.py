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

LineCallback = Optional[Callable[[str], None]]


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
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self.pos)
                chunk = f.read()
                self.pos = f.tell()
        except OSError:
            return
        if not chunk:
            return
        self.buf += chunk
        while True:
            nl = self.buf.find("\n")
            if nl < 0:
                break
            line = self.buf[:nl].rstrip("\r")
            self.buf = self.buf[nl + 1:]
            if on_line is not None:
                on_line(line)

    def flush_tail(self, on_line: LineCallback) -> None:
        """Emit the trailing partial line, if any. Call once, after the final
        drain, when the process is known to have exited."""
        if self.buf.strip() and on_line is not None:
            on_line(self.buf.rstrip("\r\n"))

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
