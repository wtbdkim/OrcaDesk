"""A relaxed surface scan, end to end below the GUI: what ORCA writes, what the
parser reads out of it, how the queue judges it, and which of its structures
the Visual strip offers.

The fixture is a real ORCA 6.1.1 run (tests/scan/fixtures/h2o_angle_scan.*):
water at HF/def2-SVP, the H-O-H angle scanned 100 -> 120 degrees in 3 points
while a bond constraint holds O-H(2) — so it also pins that a Scan and
Constraints share one %geom block and both act (the held bond read 0.9578 A
at every point).
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from orcamgr.core.input_generator import StepConfig
from orcamgr.core.parser import parse_file
from orcamgr.core.queue import (Calculation, _kept_result_matches,
                                validate_result)
from orcamgr.core.runner import OrcaRunError
from orcamgr.molview import discover_structure_sets, frames_from_file

FIX = Path(__file__).parent / "scan" / "fixtures"
OUT = FIX / "h2o_angle_scan.out"

_WATER = ("O 0.000000 0.000000 0.117300\n"
          "H 0.000000 0.757200 -0.469200\n"
          "H 0.000000 -0.757200 -0.469200")
_SCAN = [{"kind": "A", "atoms": [1, 0, 2], "start": 100, "end": 120, "steps": 3}]


def _calc(scan=None, kind="opt", **kw) -> Calculation:
    cfg = StepConfig.from_dict({"kind": kind, "geom_scan": scan or []})
    return Calculation(name="h2o_angle_scan", kind=kind, config=cfg, xyz=_WATER, **kw)


# ---- the parser, on what ORCA really wrote --------------------------------

def test_the_real_scan_reads_its_coordinate_and_every_point():
    r = parse_file(str(OUT))
    assert r.terminated_normally and r.is_optimization and r.opt_converged
    assert r.scan_coords == [{"kind": "A", "atoms": [1, 0, 2], "start": 100.0,
                              "end": 120.0, "steps": 3}]
    # the results table, 'Actual Energy', exactly as printed
    assert [(p["values"], p["e_eh"]) for p in r.scan_points] == [
        ([100.0], -75.96050332), ([110.0], -75.96049539), ([120.0], -75.95575331)]
    # against the lowest point, in kcal/mol
    assert r.scan_points[0]["de_kcal"] == 0.0
    assert r.scan_points[2]["de_kcal"] == pytest.approx(2.9808, abs=1e-3)
    # and the table and ORCA's own .dat agree
    dat = [ln.split() for ln in (FIX / "h2o_angle_scan.relaxscanact.dat")
           .read_text().splitlines() if ln.strip()]
    assert [float(e) for _, e in dat] == [p["e_eh"] for p in r.scan_points]
    assert ("Surface scan", "3 points", "") in r.summary_rows()


def test_a_scan_that_stopped_keeps_the_points_it_finished(tmp_path):
    # an error termination mid-scan (or a scan still running) has no results
    # table: the points whose optimization finished are read off the steps
    lines = OUT.read_text(encoding="utf-8", errors="replace").splitlines()
    cut = next(i for i, ln in enumerate(lines) if "RELAXED SURFACE SCAN STEP   3" in ln)
    path = tmp_path / "partial.out"
    path.write_text("\n".join(lines[:cut]), encoding="utf-8")
    r = parse_file(str(path))
    assert not r.terminated_normally
    assert [p["values"] for p in r.scan_points] == [[100.0], [110.0]]
    assert [round(p["e_eh"], 8) for p in r.scan_points] == [-75.96050332, -75.96049539]


_SYNTH = """\
                       ******************************
                       *    Relaxed Surface Scan    *
                       ******************************

                      Bond (  1,   0):   range=   0.95000000 ..   1.05000000  steps =    2
                Angle (  1,   0,   2):   range= 100.00000000 .. 110.00000000  steps =    2

There are 2 parameters to be scanned.
...
RELAXED SURFACE SCAN RESULTS
----------------------------

Column   1: NONAME
Column   2: NONAME

The Calculated Surface using the 'Actual Energy'
   0.95000000 100.00000000 -75.96061563
   0.95000000 110.00000000 -75.96068085
   1.05000000 100.00000000 -75.95121500
   1.05000000 110.00000000 -75.95036894

The Calculated Surface using the SCF energy
   0.95000000 100.00000000 -75.96061563
"""


def test_a_two_dimensional_table_reads_a_value_per_coordinate(tmp_path):
    # the shape of a measured 2-D run (water, bond x angle, 2 x 2), with \r\n
    p = tmp_path / "grid.out"
    p.write_bytes(_SYNTH.replace("\n", "\r\n").encode())
    r = parse_file(str(p))
    assert [c["kind"] for c in r.scan_coords] == ["B", "A"]
    assert r.scan_coords[0]["atoms"] == [1, 0]
    assert [p["values"] for p in r.scan_points] == [
        [0.95, 100.0], [0.95, 110.0], [1.05, 100.0], [1.05, 110.0]]
    # the SCF table after it is not read as more points
    assert len(r.scan_points) == 4
    assert min(p["de_kcal"] for p in r.scan_points) == 0.0


def test_an_output_without_a_scan_has_none(tmp_path):
    p = tmp_path / "plain.out"
    p.write_text("  *** OPTIMIZATION RUN DONE ***\nFINAL SINGLE POINT ENERGY  -1.0\n")
    r = parse_file(str(p))
    assert r.scan_coords == [] and r.scan_points == []


# ---- the queue's judgment --------------------------------------------------

def test_a_finished_scan_passes_as_an_optimization():
    validate_result(_calc(_SCAN), parse_file(str(OUT)))


def test_a_scan_that_scanned_nothing_fails():
    # measured: "! HF def2-SVP" with the same %geom Scan runs one single point
    # and terminates normally. An opt that converged without a results table
    # is the same thing — the scan the calculation was for never happened.
    r = parse_file(str(OUT))
    r.scan_points = []
    with pytest.raises(OrcaRunError, match="No relaxed surface scan"):
        validate_result(_calc(_SCAN), r)
    # a plain optimization is not asked for one
    validate_result(_calc(), r)


def test_a_kept_scan_and_a_kept_optimization_are_told_apart():
    r = parse_file(str(OUT))
    assert _kept_result_matches(_calc(_SCAN), r) == ""
    # a plain opt must not adopt a scan's folder: its geometry is the last
    # point, held there by the scan, not a minimum
    assert "relaxed surface scan" in _kept_result_matches(_calc(), r)
    # and a scan must not adopt a plain optimization's
    r.scan_points = []
    assert "no relaxed surface scan" in _kept_result_matches(_calc(_SCAN), r)
    # a raw calc's scan is in its text, not its config: not second-guessed
    raw = _calc(is_raw=True, raw_text="! Opt\n%geom Scan A 1 0 2 = 100, 120, 3 end end")
    assert _kept_result_matches(raw, parse_file(str(OUT))) == ""


# ---- the structures, in the Visual strip ----------------------------------

def test_the_scan_points_are_one_set_and_the_numbered_copies_are_not_listed(tmp_path):
    for f in FIX.glob("h2o_angle_scan.*"):
        (tmp_path / f.name).write_bytes(f.read_bytes())
    # what ORCA writes beside the .allxyz: the same points, one file each
    for k in (1, 2, 3):
        (tmp_path / f"h2o_angle_scan.00{k}.xyz").write_text("3\nx\nO 0 0 0\nH 0 0 1\nH 0 1 0\n")
    (tmp_path / "h2o_angle_scan_trj.xyz").write_text("3\nx\nO 0 0 0\nH 0 0 1\nH 0 1 0\n")
    sets = discover_structure_sets(tmp_path, base="h2o_angle_scan")
    labels = [s["label"] for s in sets]
    assert labels[0] == "Scan points" and sets[0]["count"] == 3
    assert "Optimization trajectory" in labels
    assert not [lb for lb in labels if re.search(r"\.00\d\.xyz$", lb)]


def test_the_scan_points_read_in_order_with_their_energies():
    frames = frames_from_file(FIX / "h2o_angle_scan.allxyz")
    assert [f["label"] for f in frames] == ["Step 1", "Step 2", "Step 3"]
    # ORCA's ">" separators are not frames, and the comment's "E <Eh>" is read
    assert [round(f["energy"], 8) for f in frames] == [
        -75.96050332, -75.96049539, -75.95575331]
    # the angle each frame holds is the one its step was scanned to
    for f, want in zip(frames, (100.0, 110.0, 120.0)):
        xyz = [ln.split() for ln in f["xyz"].splitlines()[2:]]
        p = [[float(v) for v in r[1:4]] for r in xyz]
        v1 = [p[1][i] - p[0][i] for i in range(3)]
        v2 = [p[2][i] - p[0][i] for i in range(3)]
        cos = sum(a * b for a, b in zip(v1, v2)) / math.dist(p[0], p[1]) / math.dist(p[0], p[2])
        assert math.degrees(math.acos(cos)) == pytest.approx(want, abs=0.01)
        # and the constraint held the other bond where it started
        assert math.dist(p[0], p[2]) == pytest.approx(0.9578, abs=1e-3)


def test_an_allxyz_that_is_not_a_scan_is_not_called_one(tmp_path):
    (tmp_path / "run.allxyz").write_text("1\nsomething else\nH 0 0 0\n")
    assert discover_structure_sets(tmp_path, base="run") == []
