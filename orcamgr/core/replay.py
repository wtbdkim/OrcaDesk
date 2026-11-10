"""What the Jobs view replays from a run's .out after a reattach.

A run reattached in a new session streams only what ORCA writes from that
moment, so the progress trackers (web/scf_graph.js, web/progress_panels.js)
are rebuilt from the file: graph_lines_of picks out the lines they read, and
Bridge.get_graph_lines hands them over. The patterns below are a MIRROR of the
trackers' regexes; a marker the live stream would feed a tracker but this
filter drops is a panel that silently stays empty.

What is kept, and why. The history used to be "the last 2000 relevant lines",
which on a long run cut off the very lines that say what the run IS: a
61-point scan lost its "There will be 100 constrained geometry optimizations"
header and its first 35 points, so the cell forgot it was a scan.

- The run's SKELETON, all of it: cycle banners and wall times, scan point
  banners and header, done / post-stage markers, an IRC's settings, banners
  and step rows, CREST's phase markers, the freq / TD-DFT stage banners.
  It grows with cycles and steps, about 100 bytes per optimization cycle.
- The echoed input lines (method, cores), the first of them.
- The geometry-convergence tables of the current optimization (a scan's
  current point; the tracker files a finished point by its cycle count and
  wall time, both in the skeleton).
- The BULK, windowed to the recent end: SCF iteration rows (the cell draws the
  SCF running now) and the freq / TD-DFT progress counters (the latest wins).

A 157 MB output read line by line through a dozen regexes took 33 s, on the Qt
main thread. So the file is read as bytes, and only the lines holding one of
the patterns' fixed words (or starting like a numeric row, or like an echoed
input line) are decoded and classified: a superset filter, pinned by
tests/test_log_replay.py against every sample line. ReplayCache keeps each
file's digest with the byte offset it reached, so a second read covers only
what was appended, and a large first read runs on a worker thread.

Qt-free, so it is tested directly.
"""

from __future__ import annotations

import os
import re
import threading
from collections import OrderedDict, deque
from typing import Optional

from ..textio import repair_ansi_line


# Line patterns the SCF/geo graph trackers care about (mirror of web/scf_graph.js
# ITER_RE / GEO_RE / GEO_TABLE_RE / GEO_ITEM_RE). graph_lines_of() filters the
# .out to just these so the UI can rebuild the graph history on reattach,
# without re-streaming the whole file through the capped log buffer.
_G_CYCLE = re.compile(r"GEOMETRY OPTIMIZATION CYCLE\s+\d+", re.I)
_G_ITER = re.compile(r"^\s*\d+\s+-?\d+\.\d+\s+-?\d+\.\d+[eE][+-]?\d+")
_G_TABLE = re.compile(r"\|Geometry convergence\|", re.I)
_G_ITEM = re.compile(
    r"(Energy change|RMS gradient|MAX gradient|RMS step|MAX step)\s+-?[\d.]+\s+[\d.]+\s+(YES|NO)", re.I)
_G_DASHES = re.compile(r"-{5,}")
_G_DOTS = re.compile(r"\.{5,}")
# optimization-finished + post-opt-stage markers, so a reattach-seeded graph also
# flips to 100% / "running frequencies…" (mirror of scf_graph.js OPT_DONE_RE / POST_OPT_RE).
# _G_POST is case-SENSITIVE like its JS mirror: the real markers are ORCA's
# UPPERCASE section banners, while every output (opt-only included) carries
# mixed-case look-alikes that must not match (header credits "pre 5.0 version
# of the SCF Hessian"; property echo "Properties with geometric perturbations:",
# "SCF Hessian ... NO").
_G_DONE = re.compile(r"\*\*\*\s*OPTIMIZATION RUN DONE\s*\*\*\*|THE OPTIMIZATION HAS CONVERGED|HURRAY", re.I)
_G_POST = re.compile(
    r"VIBRATIONAL FREQUENCIES|ORCA SCF RESPONSE|GEOMETRIC PERTURBATIONS|CP-?SCF DRIVER|SCF HESSIAN|ANALYTICAL FREQUENCIES|NUMERICAL FREQUENCIES")
# CREST iMTD-GC progress markers (mirror of web/scf_graph.js CrestTracker regexes)
# so get_graph_lines can reseed a reattached CREST run's phase/energy graph. These
# never appear in an ORCA .out (and vice versa), so matching them is additive/safe.
_CREST_GRAPH = re.compile(
    r"CREGEN>\s*E lowest|structures?\s+remain within|Meta-Dynamics Iteration|"
    r"\(\d+\s*MTDs\)|\*MTD\s+\d+\s+completed successfully|CREST terminated normally|"
    r"Change in topology detected|safety termination of CREST|"
    r"number of unique conformers for further calc|Initial Geometry Optimization|"
    r"iMTD-GC SAMPLING|Additional regular MDs|Final Geometry Optimization")
# Frequency (numerical + analytical CP-SCF) and TD-DFT phase-chain markers —
# the mirror of web/progress_panels.js (A_STAGES / TD_STAGES and their
# counters), so a reattached run rebuilds its STEP PANEL too, not just the
# SCF/geometry curve: without these a restart mid-Hessian showed an empty
# Graph tab until the next stage banner happened to stream by (hours, on a
# large molecule). The stage banners are matched case-SENSITIVELY for the
# same reason as _G_POST (ORCA prints them uppercase, and mixed-case
# look-alikes appear in every output); the counter lines accept ORCA's own
# capitalization, mirroring the /i on their JS regexes.
# ORCA's own per-cycle wall time. GeoTracker reads it as the PRIMARY source for
# the "~N s/step" figure and the ETA (its Date.now fallback drops gaps under
# 500 ms, so a burst replay yields nothing) — and this filter mirrored every
# other tracker regex but not this one, so a graph re-seeded from disk after a
# restart came back without the timing it had before the restart.
_G_ITERTIME = re.compile(r"Time for complete geometry iter\s*:\s*[\d.]+\s*s", re.I)
# A relaxed surface scan's point count and point banners (mirror of
# SCAN_TOTAL_RE / SCAN_STEP_RE in web/scf_graph.js).
_G_SCAN = re.compile(r"RELAXED SURFACE SCAN STEP\s+\d+|"
                     r"There will be\s+\d+\s+constrained geometry optimizations")
# An IRC's path, mirror of web/progress_panels.js IrcTracker: the settings echo
# (MaxIter, which directions), each direction's banner and thresholds, its
# per-step rows (exactly five numbers - an SCF row has seven or more) and the
# convergence / summary markers. Without these a reopened IRC cell drew the
# last single SCF instead of the path walked so far.
_IRC_GRAPH = re.compile(
    r"Intrinsic Reaction Coordinate Calculation|Max\.\s*no of cycles\s+MaxIter|"
    r"^Direction\s+\.+|\*\s+(?:FORWARD|BACKWARD) IRC\s+\*|^Convergence thresholds\s|"
    r"THE IRC HAS CONVERGED|IRC PATH SUMMARY")
_IRC_ROW = re.compile(
    r"^\s*\d+\s+-?\d+\.\d+\s+-?\d+\.\d+\s+\d+\.\d+\s+\d+\.\d+\s*$")

_PHASE_GRAPH = re.compile(
    # "displaced geometry K (of N)" is what ORCA 6.1.1 prints; the old
    # "displacement K / N" matched nothing, so a graph rebuilt after a restart
    # was as blank as the live one. Mirrors DISP_RE in web/progress_panels.js.
    r"ORCA NUMERICAL FREQUENCIES|[Nn]umber of displacements|"
    r"displaced geometry\s+\d+\s*\(\s*of\s+\d+\s*\)|"
    r"\bNORMAL MODES\b|\bIR SPECTRUM\b|THERMOCHEMISTRY AT\s+[\d.]+|"
    r"Analytic(?:al)? Hessian|[Nn]umber of perturbations|\d+\s*/\s*\d+\s+done|"
    r"\bBATCH\s+\d+\s*:\s*Atoms\s+\d+\s*-\s*\d+|"
    r"TD-DFT XC SETUP|RPA-DIAGONALIZATION|DAVIDSON-DIAGONALIZATION|"
    r"TD-DFT(?:/TDA)? EXCITED STATES|ABSORPTION SPECTRUM VIA TRANSITION|"
    r"CIS/TD-DFT TOTAL ENERGY|Number of roots to be determined|"
    r"\*{4}\s*Iteration\s+\d+\s*\*{4}|ORCA TERMINATED NORMALLY|"
    # what tells a pre-step Hessian (OptTS Calc_Hess, IRC InitHess) from a
    # frequency job, and what clears it: mirrors OPT_DONE_RE / IRC_START_RE /
    # PHASE_RESET_RE in progress_panels.js
    r"FINAL ENERGY EVALUATION AT THE STATIONARY POINT|"
    r"Intrinsic Reaction Coordinate Calculation|FORWARD IRC|BACKWARD IRC")
# The job meta the step panel's header line shows (method · cores): ORCA's own
# echo of the input file (mirror of progress_panels.js _grabOrcaMeta).
_G_META = re.compile(r"^\|\s*\d+>\s*(?:!|%pal\s+nprocs\b)")

# The bulk windows: the most recent SCF iteration rows, and the most recent
# freq / TD-DFT progress counters. Everything else is kept whole, bounded only
# by safety caps no real run reaches (a million skeleton lines is ~400,000
# optimization cycles).
_GRAPH_LINES_MAX = 2000
_SKELETON_MAX = 1_000_000
_TABLE_LINES_MAX = 200_000
_META_MAX = 200

# The freq / TD-DFT lines that COUNT (one per displacement, perturbation batch
# or Davidson iteration): the trackers read the latest, so these are windowed.
# Every other _PHASE_GRAPH line is a stage banner and is kept.
_PHASE_COUNTER = re.compile(
    r"displaced geometry\s+\d+\s*\(\s*of\s+\d+\s*\)|\d+\s*/\s*\d+\s+done|"
    r"\bBATCH\s+\d+\s*:\s*Atoms|\*{4}\s*Iteration\s+\d+\s*\*{4}")

# --- the byte-level prefilter ------------------------------------------------
# A fixed word every pattern above needs, in ORCA's (and CREST's) own spelling.
# A line holding none of them, and not starting like a numeric row or an
# echoed input line, cannot match any pattern, so it is never decoded. A new
# pattern needs its word here too: tests/test_log_replay.py runs every sample
# line through this filter.
_WORDS = (
    # optimization cycles, tables, done / post-stage markers
    b"GEOMETRY OPTIMIZATION CYCLE", b"Geometry convergence", b"Energy change",
    b"RMS gradient", b"MAX gradient", b"RMS step", b"MAX step",
    b"OPTIMIZATION RUN DONE", b"HAS CONVERGED", b"HURRAY",
    b"FREQUENCIES", b"SCF RESPONSE", b"PERTURBATIONS", b"DRIVER", b"SCF HESSIAN",
    b"Time for complete geometry iter",
    # a relaxed surface scan
    b"SURFACE SCAN STEP", b"There will be",
    # an IRC
    b"Intrinsic Reaction Coordinate", b"MaxIter", b"Direction", b"IRC",
    b"Convergence thresholds",
    # frequencies / TD-DFT stages and counters
    b"umber of displacements", b"displaced geometry", b"NORMAL MODES",
    b"IR SPECTRUM", b"THERMOCHEMISTRY AT", b"Analytic", b"umber of perturbations",
    b"done", b"BATCH", b"TD-DFT", b"DIAGONALIZATION", b"ABSORPTION SPECTRUM",
    b"Number of roots", b"Iteration", b"TERMINATED NORMALLY", b"STATIONARY POINT",
    # CREST
    b"CREGEN>", b"remain within", b"Meta-Dynamics Iteration", b"MTDs",
    b"completed successfully", b"CREST terminated normally", b"topology detected",
    b"safety termination", b"unique conformers", b"Geometry Optimization",
    b"iMTD-GC", b"regular MDs",
)
# an SCF iteration row or an IRC step row (both "<int> <float> <float> ..."),
# and ORCA's echo of the input file ("|  1> ! ...")
_ROW_START = re.compile(
    rb"^[ \t\f\v]*\d+[ \t\f\v]+-?\d+\.\d+[ \t\f\v]+-?\d+\.\d+|^\|[ \t\f\v]*\d+>", re.M)


def candidate_starts(data: bytes) -> "list[int]":
    """Start offsets, ascending, of the lines in ``data`` that may match a
    pattern. ``bytes.find`` per word runs at memory speed, which a regex
    alternation of fifty words does not (11 s against ~1.5 s on 157 MB)."""
    starts = set()
    for w in _WORDS:
        i = data.find(w)
        while i >= 0:
            starts.add(data.rfind(b"\n", 0, i) + 1)
            # the rest of this line is already in; go on from the next one
            nl = data.find(b"\n", i)
            if nl < 0:
                break
            i = data.find(w, nl + 1)
    for m in _ROW_START.finditer(data):
        starts.add(m.start())
    return sorted(starts)


def _is_terminator(ln: str) -> bool:
    return ln.strip() == "" or bool(_G_DASHES.search(ln)) or bool(_G_DOTS.search(ln))


class GraphDigest:
    """The lines of one .out the trackers are rebuilt from, kept by class (see
    the module docstring). Fed in file order; ``lines()`` merges the classes
    back into file order. Keys are the lines' byte offsets, so a digest fed in
    several pieces orders exactly like one fed at once."""

    def __init__(self):
        self.skeleton: deque = deque(maxlen=_SKELETON_MAX)
        self.meta: list = []
        self.tables: deque = deque(maxlen=_TABLE_LINES_MAX)
        self.scf: deque = deque(maxlen=_GRAPH_LINES_MAX)
        self.counters: deque = deque(maxlen=_GRAPH_LINES_MAX)
        self._in_table = False
        self._saw_item = False

    def feed_line(self, key: float, ln: str) -> None:
        """One line of the file, ``key`` its position (anything increasing)."""
        if self._in_table and self._saw_item and not _G_ITEM.search(ln):
            self._in_table = self._saw_item = False
            if _is_terminator(ln):
                self.tables.append((key, ln))
                return
            # the prefilter skips the table's own closing rule; a blank line
            # in its place closes the table where the tracker expects it
            self.tables.append((key - 0.5, ""))
        if _G_SCAN.search(ln):
            if "SCAN STEP" in ln:
                # a new point: the tracker starts an empty chart, so the
                # finished point's tables would only be read and dropped
                self.tables.clear()
            self.skeleton.append((key, ln))
        elif _G_CYCLE.search(ln):
            self.skeleton.append((key, ln))
        elif _G_TABLE.search(ln):
            self._in_table = True
            self._saw_item = False
            self.tables.append((key, ln))
        elif self._in_table and _G_ITEM.search(ln):
            self._saw_item = True
            self.tables.append((key, ln))
        elif _G_DONE.search(ln) or _G_POST.search(ln):
            self.skeleton.append((key, ln))
        elif _G_ITER.match(ln):
            self.scf.append((key, ln))
        elif (_IRC_GRAPH.search(ln) or _IRC_ROW.match(ln) or _G_ITERTIME.search(ln)
              or _CREST_GRAPH.search(ln)):
            self.skeleton.append((key, ln))
        elif _G_META.match(ln):
            if len(self.meta) < _META_MAX:
                self.meta.append((key, ln))
        elif _PHASE_GRAPH.search(ln):
            (self.counters if _PHASE_COUNTER.search(ln) else self.skeleton).append((key, ln))

    def feed_bytes(self, data: bytes, base: int, prefilter: bool = True) -> None:
        """Whole lines of the file starting at byte ``base`` (``data`` ends at
        a line end). With ``prefilter`` only the candidate lines are decoded."""
        if prefilter:
            starts = candidate_starts(data)
        else:
            starts, i = [], 0
            while i < len(data):
                starts.append(i)
                nl = data.find(b"\n", i)
                i = len(data) if nl < 0 else nl + 1
        for s in starts:
            nl = data.find(b"\n", s)
            raw = data[s:nl if nl >= 0 else len(data)]
            # surrogateescape + per-line repair: an ORCA .out is mixed-encoding
            # on Windows (see orcamgr/textio.py)
            ln = repair_ansi_line(
                raw.decode("utf-8", errors="surrogateescape").rstrip("\r"))
            self.feed_line(float(base + s), ln)

    def lines(self) -> "list[str]":
        merged = sorted([*self.skeleton, *self.meta, *self.tables,
                         *self.scf, *self.counters], key=lambda t: t[0])
        return [ln for _, ln in merged]


_CHUNK = 32 << 20


def _feed_file(digest: GraphDigest, path, offset: int, prefilter: bool = True) -> int:
    """Feed ``digest`` the whole lines of ``path`` from byte ``offset`` on;
    returns the offset reached: just past the last complete line, so a line
    ORCA is still writing is read whole next time, not twice."""
    with open(path, "rb") as f:
        f.seek(offset)
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                return offset
            cut = chunk.rfind(b"\n")
            if cut < 0:
                if len(chunk) < _CHUNK:
                    return offset          # an unfinished last line: wait for it
                cut = len(chunk) - 1       # a 32 MB line holds no tracker line
            digest.feed_bytes(chunk[:cut + 1], offset, prefilter)
            offset += cut + 1
            f.seek(offset)


def graph_lines_of(out_path, prefilter: bool = True) -> "list[str]":
    """The lines of an ORCA (or CREST) .out the progress trackers are built
    from, in file order: what Bridge.get_graph_lines replays."""
    d = GraphDigest()
    _feed_file(d, out_path, 0, prefilter)
    return d.lines()


class _Entry:
    __slots__ = ("digest", "offset", "sig", "busy", "error")

    def __init__(self):
        self.digest = GraphDigest()
        self.offset = 0
        self.sig = b""        # the bytes just before `offset`, to spot a rewrite
        self.busy = False
        self.error = ""


class ReplayCache:
    """Each .out's digest and how far it has been read.

    ``lines(path)`` answers at once when at most ``sync_bytes`` are unread;
    otherwise it starts a worker thread on the rest and returns None, and the
    caller asks again. A file that shrank, or whose bytes before the offset
    changed (a re-run rewrote it), is read again from the start."""

    _SIG = 64

    def __init__(self, sync_bytes: int = 4 << 20, max_entries: int = 16):
        self.sync_bytes = sync_bytes
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: "OrderedDict[str, _Entry]" = OrderedDict()

    def _same_file(self, e: _Entry, path, size: int) -> bool:
        if size < e.offset:
            return False
        if e.offset == 0:
            return True
        try:
            with open(path, "rb") as f:
                f.seek(e.offset - len(e.sig))
                return f.read(len(e.sig)) == e.sig
        except OSError:
            return False

    def _update(self, e: _Entry, path) -> None:
        try:
            e.offset = _feed_file(e.digest, path, e.offset)
            if e.offset:
                with open(path, "rb") as f:
                    f.seek(max(0, e.offset - self._SIG))
                    e.sig = f.read(min(self._SIG, e.offset))
        except Exception as ex:   # handed to the next caller, never raised in a thread
            e.error = str(ex) or type(ex).__name__
        finally:
            with self._lock:
                e.busy = False

    def _take_error(self, key: str, e: _Entry) -> None:
        """Forget an entry whose read failed and raise its error. Under the lock."""
        err, e.error = e.error, ""
        if self._entries.get(key) is e:
            del self._entries[key]
        raise OSError(err)

    def lines(self, path) -> "Optional[list[str]]":
        """The replay lines, or None while a worker is still reading.
        Raises OSError when the file cannot be read."""
        key = os.path.normcase(os.path.abspath(str(path)))
        size = os.path.getsize(path)
        with self._lock:
            e = self._entries.get(key)
            if e is None:
                e = self._entries[key] = _Entry()
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                old_key, old = next(iter(self._entries.items()))
                if old.busy or old_key == key:
                    break
                del self._entries[old_key]
            if e.busy:
                return None
            if e.error:
                self._take_error(key, e)
            if not self._same_file(e, path, size):
                e = self._entries[key] = _Entry()
            e.busy = True
            background = size - e.offset > self.sync_bytes
        if background:
            threading.Thread(target=self._update, args=(e, path),
                             name="orcadesk-replay", daemon=True).start()
            return None
        self._update(e, path)
        with self._lock:
            if e.error:
                self._take_error(key, e)
        return e.digest.lines()
