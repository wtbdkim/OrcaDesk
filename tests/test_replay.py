"""core/replay.py: what the Jobs view replays from a .out, and how it reads it.

The pattern-by-pattern filter is pinned in test_log_replay.py; this file pins
the digest (which lines are kept whole and which are windowed), the byte
prefilter's equivalence with reading every line, and the incremental cache.
Qt-free.
"""
import threading

from orcamgr.core.replay import (
    GraphDigest, ReplayCache, graph_lines_of, _GRAPH_LINES_MAX,
)


def _write(path, lines, end="\n"):
    path.write_bytes(("\n".join(lines) + end).encode("utf-8"))
    return path


def _table(cycle_e):
    """A geometry-convergence table as ORCA 6.1.1 prints it, closing rules and
    all (the prefilter does not decode those)."""
    return [
        "          ----------------------|Geometry convergence|-------------------------",
        "          Item                value                   Tolerance       Converged",
        "          ---------------------------------------------------------------------",
        "          Energy change      %.10f            0.0000010000      NO" % cycle_e,
        "          RMS gradient        0.0067227982            0.0000300000      NO",
        "          MAX gradient        0.0558546848            0.0001000000      NO",
        "          RMS step            0.0143673943            0.0006000000      NO",
        "          MAX step            0.0642281876            0.0010000000      NO",
        "          -------------------------------------------------------------------------",
        "          ........................................................",
        "          Max(Bonds)      0.0340      Max(Angles)    1.81",
        "          ---------------------------------------------------------------------",
        "",
    ]


NOISE = [
    "                    Basis set information",
    "  0 C     6.0000    0    12.011    1.234567    2.345678    3.456789",
    "Total Energy       :         -232.12345678 Eh           -6316.40 eV",
    "-----------------------------------------------------------",
]


def _scan(points=3, cycles=4, scf_rows=5):
    out = ["|  1> ! r2SCAN-3c TightSCF TightOpt", "|  2> %pal nprocs 15 end",
           "There will be   %d constrained geometry optimizations." % points]
    for p in range(1, points + 1):
        out.append("         *               RELAXED SURFACE SCAN STEP  %2d               *" % p)
        for c in range(1, cycles + 1):
            out.append("        *                GEOMETRY OPTIMIZATION CYCLE   %d            *" % c)
            out += NOISE
            out += ["  %d   -2095.%09d   -1.0e-05  4.97e-03  2.50e-02" % (k, k)
                    for k in range(1, scf_rows + 1)]
            out += _table(-0.001 * c)
            out.append("Time for complete geometry iter:     12.500 sec")
        out.append("                                *** OPTIMIZATION RUN DONE ***")
    return out


def _is_rule(ln):
    s = ln.strip()
    return s == "" or set(s) <= set("-.")


def test_the_prefilter_keeps_what_reading_every_line_keeps(tmp_path):
    # the byte prefilter only decides which lines get decoded; what the digest
    # keeps must not change. The one difference is the table's closing rule,
    # which the prefilter never decodes and a blank line stands in for.
    p = _write(tmp_path / "s.out", _scan())
    fast = [ln for ln in graph_lines_of(p, prefilter=True) if not _is_rule(ln)]
    full = [ln for ln in graph_lines_of(p, prefilter=False) if not _is_rule(ln)]
    assert fast == full
    assert not any("Basis set information" in ln for ln in fast)


def test_every_table_is_closed_for_the_tracker(tmp_path):
    p = _write(tmp_path / "s.out", _scan(points=1, cycles=3))
    lines = graph_lines_of(p)
    items = [i for i, ln in enumerate(lines) if "MAX step" in ln]
    assert len(items) == 3
    # the line after a table's last item closes it (blank, dashes or dots)
    assert all(_is_rule(lines[i + 1]) for i in items)


def test_a_scan_keeps_only_the_current_points_tables(tmp_path):
    p = _write(tmp_path / "s.out", _scan(points=3, cycles=4))
    lines = graph_lines_of(p)
    last = max(i for i, ln in enumerate(lines) if "SCAN STEP" in ln)
    tables = [i for i, ln in enumerate(lines) if "|Geometry convergence|" in ln]
    assert len(tables) == 4 and min(tables) > last
    # while every point's skeleton stays
    assert sum("GEOMETRY OPTIMIZATION CYCLE" in ln for ln in lines) == 12
    assert sum("OPTIMIZATION RUN DONE" in ln for ln in lines) == 3


def test_the_echoed_input_is_kept_however_long_the_run(tmp_path):
    p = _write(tmp_path / "s.out", _scan(points=2, cycles=3, scf_rows=1500))
    lines = graph_lines_of(p)
    assert lines[0].startswith("|  1> ! r2SCAN-3c")
    assert sum(1 for ln in lines if ln.lstrip()[:1].isdigit()) == _GRAPH_LINES_MAX


def test_an_incremental_read_equals_one_read_of_the_whole_file(tmp_path):
    lines = _scan(points=3, cycles=4)
    p = tmp_path / "s.out"
    cache = ReplayCache()
    _write(p, lines[:150])
    cache.lines(p)
    _write(p, lines)
    assert cache.lines(p) == graph_lines_of(p)


def test_a_line_still_being_written_is_read_once_it_is_whole(tmp_path):
    p = tmp_path / "s.out"
    head = ["        *                GEOMETRY OPTIMIZATION CYCLE   1            *"]
    cache = ReplayCache()
    _write(p, head + ["Time for complete geometry iter:     12.5"], end="")
    assert not any("iter" in ln for ln in cache.lines(p))
    _write(p, head + ["Time for complete geometry iter:     12.500 sec"])
    got = cache.lines(p)
    assert sum("Time for complete geometry iter" in ln for ln in got) == 1
    assert got[-1].endswith("12.500 sec")


def test_a_rewritten_file_is_read_again_from_the_start(tmp_path):
    p = tmp_path / "s.out"
    cache = ReplayCache()
    _write(p, _scan(points=2, cycles=2))
    cache.lines(p)
    # a re-run rewrites the .out: longer than before, different bytes
    _write(p, ["|  1> ! B3LYP def2-SVP Opt"] + _scan(points=1, cycles=5)[3:])
    assert cache.lines(p) == graph_lines_of(p)
    assert not any("SCAN STEP   2" in ln for ln in cache.lines(p))


def test_a_large_read_runs_on_a_worker_thread(tmp_path):
    p = _write(tmp_path / "s.out", _scan(points=2, cycles=3))
    cache = ReplayCache(sync_bytes=0)
    assert cache.lines(p) is None            # handed to a worker
    for t in threading.enumerate():
        if t.name == "orcadesk-replay":
            t.join(10)
    assert cache.lines(p) == graph_lines_of(p)


def test_digest_keys_order_pieces_like_a_single_read():
    d = GraphDigest()
    d.feed_line(10.0, "        *                GEOMETRY OPTIMIZATION CYCLE   2            *")
    d.feed_line(5.0 + 0, "|  1> ! r2SCAN-3c")
    assert d.lines()[0].startswith("|  1>")
