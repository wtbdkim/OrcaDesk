"""What the UI replays from disk after a reattach: the .out line filter behind
get_graph_lines, and the tail behind get_output_tail.

A run reattached in a new session streams only what ORCA writes from that
moment on, so both the Graph panel and the Raw log are rebuilt from the file.
The filter's regexes are a MIRROR of the tracker regexes in web/scf_graph.js
and web/progress_panels.js: a marker the live stream would have fed a tracker
but the filter drops is a panel that silently stays empty for hours (that is
exactly how a restart mid-Hessian lost its frequency stepper). The samples
below are verbatim lines from real ORCA 6.1.1 output.

Imports gui/bridge.py for its module-level patterns and its tail reader only
(no Bridge instance, no Qt objects); skipped where PyQt6 is not installed.
"""
import pytest

pytest.importorskip("PyQt6")

from orcamgr.gui.bridge import (  # noqa: E402
    _PHASE_GRAPH, _G_META, _G_POST, _G_CYCLE, tail_lines, _LOG_TAIL_MAX,
)


ANALYTICAL_FREQ_LINES = [
    "GEOMETRIC PERTURBATIONS (144 nuclei)",          # boots the chain (_G_POST)
    "ORCA SCF RESPONSE CALCULATION",
    "                              SCF HESSIAN",
    "VIBRATIONAL FREQUENCIES",
]
PHASE_LINES = [
    "BATCH 135: Atoms  135 -  135 (  3 perturbations)",
    "BATCH   0: Atoms    0 -    3 ( 12 perturbations)",
    "Number of perturbations            ...    432",
    "    12 /  432 done",
    "                              NORMAL MODES",
    "                              IR SPECTRUM",
    "THERMOCHEMISTRY AT 298.15K",
    "ORCA NUMERICAL FREQUENCIES",
    "Number of displacements            ...     30",
    "... for displacement    7 /   30",
    "                       TD-DFT XC SETUP",
    "               RPA-DIAGONALIZATION",
    "                     TD-DFT/TDA EXCITED STATES",
    "         ABSORPTION SPECTRUM VIA TRANSITION ELECTRIC DIPOLE MOMENTS",
    "Number of roots to be determined            ...   10",
    "****Iteration 3****",
    "                             ****ORCA TERMINATED NORMALLY****",
]
META_LINES = [
    "|  1> ! wB97X-D4 def2-TZVP TightSCF RIJCOSX TightOpt Freq",
    "|  3> %pal nprocs 15 end",
]


@pytest.mark.parametrize("line", ANALYTICAL_FREQ_LINES)
def test_stage_banners_survive_the_filter(line):
    # these boot/advance the chain and are kept by the post-opt branch
    assert _G_POST.search(line), line


@pytest.mark.parametrize("line", PHASE_LINES)
def test_phase_counters_survive_the_filter(line):
    assert _PHASE_GRAPH.search(line), line


@pytest.mark.parametrize("line", META_LINES)
def test_echoed_method_and_cores_survive_the_filter(line):
    assert _G_META.match(line), line


def test_the_reset_marker_is_kept_for_the_phase_chain():
    # a pre-step Hessian (ts_opt Calc_Hess, IRC InitHess) must be able to CLEAR
    # its chain on the next cycle, exactly as it does live
    assert _G_CYCLE.search("*** GEOMETRY OPTIMIZATION CYCLE   7 ***")


def test_ordinary_output_is_not_dragged_in():
    # the filter is a whitelist: bulk output (this is 78k lines on a real run)
    # must not ride along, or the payload stops being bounded
    for line in [
        "  0 C     6.0000    0    12.011    1.234567    2.345678    3.456789",
        "Total Energy       :         -232.12345678 Eh           -6316.40 eV",
        "                    Basis set information",
        "  Sum of atomic charges                 :   -0.0000000",
    ]:
        assert not _PHASE_GRAPH.search(line), line
        assert not _G_META.match(line), line


# ---------------------------------------------------------------------------
# get_output_tail's reader: the Raw log's restored history after a reattach
# ---------------------------------------------------------------------------


def _write(tmp_path, text, name="run.out"):
    p = tmp_path / name
    p.write_bytes(text.encode("utf-8"))
    return p


def test_tail_returns_the_last_lines_in_order(tmp_path):
    p = _write(tmp_path, "".join(f"line {i}\n" for i in range(100)))
    lines, more = tail_lines(p, 10)
    assert lines == [f"line {i}" for i in range(90, 100)]
    assert more is True          # 90 lines precede them


def test_a_short_file_comes_back_whole_and_says_so(tmp_path):
    p = _write(tmp_path, "one\ntwo\nthree\n")
    lines, more = tail_lines(p, 500)
    assert lines == ["one", "two", "three"]
    assert more is False         # nothing was left out, so the marker says so


def test_crlf_output_loses_its_carriage_returns(tmp_path):
    # ORCA on Windows writes CRLF; the log lines must not carry a stray CR
    p = _write(tmp_path, "a\r\nb\r\nc\r\n")
    lines, _ = tail_lines(p, 500)
    assert lines == ["a", "b", "c"]


def test_a_file_past_the_block_size_drops_the_partial_first_line(tmp_path):
    # 5 lines * 400 bytes/line = a 2000-byte block over a much longer file, so
    # the read starts mid-line — that fragment must never reach the log
    body = "".join(f"{i:06d} " + "x" * 90 + "\n" for i in range(500))
    p = _write(tmp_path, body)
    lines, more = tail_lines(p, 5)
    assert lines == [f"{i:06d} " + "x" * 90 for i in range(495, 500)]
    assert more is True


def test_empty_file_yields_nothing(tmp_path):
    lines, more = tail_lines(_write(tmp_path, ""), 100)
    assert lines == []
    assert more is False


def test_line_count_is_clamped_to_a_sane_range(tmp_path):
    p = _write(tmp_path, "".join(f"line {i}\n" for i in range(5000)))
    assert len(tail_lines(p, 0)[0]) == 1                 # 0/None -> at least one
    assert len(tail_lines(p, -20)[0]) == 1
    assert len(tail_lines(p, 10 ** 6)[0]) == _LOG_TAIL_MAX
