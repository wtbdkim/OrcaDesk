"""Reading a whole ORCA ``.out`` a screenful at a time.

The Results window shows the output from its first line, and an ``.out`` can be
hundreds of MB -- a frequency job on a large molecule easily is. Sending that
across the bridge and drawing a DOM line per output line is a frozen window, so
the front-end draws only what is on screen and asks for lines by NUMBER. This
module answers those questions without ever holding the file's text:

* :class:`OutputIndex` -- the byte offset of every newline, found once with
  numpy (a 300 MB file is ~1 s) and extended, not rebuilt, when a running job
  appends to the file;
* :meth:`OutputIndex.lines` -- any range, read straight off disk, decoded the
  way the live tail decodes (``surrogateescape`` + :func:`repair_ansi_line`: the
  ``.out`` is mixed-encoding on Windows, see ``textio.py``);
* :meth:`OutputIndex.find` -- every line a query occurs on, scanned over bytes
  in chunks, so a search costs one pass of the file and no decoding.

Qt-free and numpy-only (P5), so it unit-tests directly.
"""
from __future__ import annotations

import os
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np

from .textio import repair_ansi_line

# Read size for the index build and the search scan. Large enough that the
# per-chunk numpy/bytes call dominates, small enough to stay out of the way.
_CHUNK = 16 << 20
# A range request reads its whole span in one go up to this size; past it (a
# pathological line) each line is read on its own and cut at _LINE_CAP.
_RANGE_MAX_BYTES = 8 << 20
_LINE_CAP = 64 << 10
# The most lines one request returns, and the most hit positions one search
# returns (it still COUNTS every hit).
MAX_LINES_PER_REQUEST = 5000
MAX_HITS = 100_000


class OutputIndex:
    """Line offsets of one file, refreshed against its size and mtime."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.size = 0
        self.mtime = 0.0
        self._ends = np.zeros(0, dtype=np.int64)   # byte position of every b"\n"
        self.max_len = 0
        self._print = b""
        self.refresh()

    # ---------------------------------------------------------------- index
    def refresh(self) -> None:
        """Bring the index up to the file as it is now. A file that only grew
        (a running job) is scanned from where the last scan stopped; anything
        else -- shrunk, rewritten in place by a re-run -- is re-scanned whole."""
        st = os.stat(self.path)
        if st.st_size == self.size and st.st_mtime == self.mtime:
            return
        # a re-run truncates and rewrites, and may well end up LONGER than the
        # file it replaced -- size and mtime alone cannot tell that from an
        # append, so the bytes already indexed must still be the same bytes
        if (self.size and st.st_size >= self.size and st.st_mtime >= self.mtime
                and self._fingerprint(self.size) == self._print):
            new = self._scan(self.size, st.st_size)
            self._ends = np.concatenate((self._ends, new))
        else:
            self._ends = self._scan(0, st.st_size)
        self.size, self.mtime = st.st_size, st.st_mtime
        self._print = self._fingerprint(self.size)
        if self.size:
            starts = np.concatenate(([0], self._ends + 1))
            stops = np.concatenate((self._ends, [self.size]))
            self.max_len = int((stops - starts).max())
        else:
            self.max_len = 0

    def _fingerprint(self, size: int) -> bytes:
        """The first and last 4 KB of the file's first ``size`` bytes."""
        with open(self.path, "rb") as f:
            head = f.read(min(4096, size))
            f.seek(max(0, size - 4096))
            return head + f.read(min(4096, size))

    def _scan(self, start: int, stop: int) -> np.ndarray:
        found = []
        with open(self.path, "rb") as f:
            f.seek(start)
            pos = start
            while pos < stop:
                buf = f.read(min(_CHUNK, stop - pos))
                if not buf:
                    break
                arr = np.frombuffer(buf, dtype=np.uint8)
                found.append(np.flatnonzero(arr == 10).astype(np.int64) + pos)
                pos += len(buf)
        return np.concatenate(found) if found else np.zeros(0, dtype=np.int64)

    @property
    def line_count(self) -> int:
        n = len(self._ends)
        last_start = int(self._ends[-1]) + 1 if n else 0
        return n + (1 if self.size > last_start else 0)

    def _span(self, k: int) -> "tuple[int, int]":
        start = 0 if k == 0 else int(self._ends[k - 1]) + 1
        stop = int(self._ends[k]) if k < len(self._ends) else self.size
        return start, stop

    # ---------------------------------------------------------------- read
    def lines(self, start: int, count: int) -> "list[str]":
        """Lines ``start .. start+count-1`` (clamped to the file), decoded."""
        total = self.line_count
        start = max(0, int(start))
        count = max(0, min(int(count), MAX_LINES_PER_REQUEST, total - start))
        if count <= 0:
            return []
        lo, _ = self._span(start)
        _, hi = self._span(start + count - 1)
        with open(self.path, "rb") as f:
            if hi - lo <= _RANGE_MAX_BYTES:
                f.seek(lo)
                raw = f.read(hi - lo).split(b"\n")
            else:
                raw = []
                for k in range(start, start + count):
                    a, b = self._span(k)
                    f.seek(a)
                    raw.append(f.read(min(b - a, _LINE_CAP)))
        out = []
        for b in raw[:count]:
            if b.endswith(b"\r"):
                b = b[:-1]
            out.append(repair_ansi_line(b.decode("utf-8", errors="surrogateescape")))
        return out

    # ---------------------------------------------------------------- search
    def find(self, query: str, limit: int = MAX_HITS) -> "tuple[list[int], int]":
        """``(line numbers, total)`` for every occurrence of ``query``,
        ignoring ASCII case. A line holding it twice is listed twice, so the
        list IS the matches in order and the front-end can step through them.
        Only the first ``limit`` positions are returned; ``total`` counts all.

        Case folding is ASCII-only on purpose: it is what ``bytes.lower`` does
        at C speed over a 300 MB file, and ORCA's own text is ASCII. The
        front-end folds the same way when it marks a match on screen, so the
        count and the marks agree."""
        q = (query or "").encode("utf-8").lower()
        if not q or b"\n" in q:
            return [], 0
        positions: list[int] = []
        total = 0
        overlap = len(q) - 1
        with open(self.path, "rb") as f:
            pos = 0
            while pos < self.size:
                f.seek(pos)
                buf = f.read(min(_CHUNK + overlap, self.size - pos)).lower()
                if not buf:
                    break
                # a match counted here starts inside [pos, pos + _CHUNK): one
                # starting later would need more bytes than the overlap holds
                total += buf.count(q)
                if len(positions) < limit:
                    i = buf.find(q)
                    while i != -1 and len(positions) < limit:
                        positions.append(pos + i)
                        i = buf.find(q, i + len(q))
                pos += _CHUNK
        if not positions:
            return [], total
        lines = np.searchsorted(self._ends, np.asarray(positions, dtype=np.int64),
                                side="left")
        return [int(k) for k in lines], total


_cache: "OrderedDict[str, OutputIndex]" = OrderedDict()
_lock = threading.Lock()
_CACHE_SIZE = 4


def index_for(path) -> OutputIndex:
    """The (refreshed) index of ``path``, cached across calls -- the front-end
    asks for a few hundred lines at a time while scrolling, and re-indexing a
    large file per request would cost a second each time."""
    key = os.path.normcase(os.path.abspath(str(path)))
    with _lock:
        idx = _cache.get(key)
        if idx is None:
            idx = OutputIndex(Path(path))
            _cache[key] = idx
            while len(_cache) > _CACHE_SIZE:
                _cache.popitem(last=False)
        else:
            _cache.move_to_end(key)
            idx.refresh()
        return idx
