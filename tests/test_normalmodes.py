"""The normal modes behind the report's vibrational section, found and matched.

Fixtures: the real ORCA 6.1.1 water frequency run whose .hess
``test_hessfile.py`` already reads, and the part of that run's .out the
fallback reads - its final geometry and the VIBRATIONAL FREQUENCIES / NORMAL
MODES tables, verbatim (``h2o_freq.out``; the rest of the 3500-line output is
SCF iterations nothing here looks at).

The contracts pinned here:

* the .out's table reads back as the same vectors the .hess holds, so the two
  sources are interchangeable;
* a report's mode N is matched to a column BY WAVENUMBER - a .hess that is some
  other Hessian's (an OptTS's first one, a previous run's) is refused, not
  animated under this report's label - while a uniformly scaled run is still
  the same modes;
* the .hess is preferred, the .out is the fallback, and having neither is a
  sentence;
* a .hess's geometry is written about the centre of mass, so it is compared to
  the report's structure after a translation, which a mode does not notice.
"""

import math
import shutil
from pathlib import Path

import pytest

from orcamgr.core.hessfile import HessError, read_hess
from orcamgr.core.normalmodes import (
    modes_for_report, place_on, read_out_modes, report_columns)

FIX = Path(__file__).parent / "hess" / "fixtures"
HESS = FIX / "h2o.hess"
OUT = FIX / "h2o_freq.out"
REPORT = [1606.44, 3822.87, 3905.68]      # what the parser lists for this run


def test_the_out_table_is_the_hess_table():
    o, h = read_out_modes(OUT), read_hess(HESS)
    assert o.symbols == h.symbols == ["O", "H", "H"]
    assert o.vibrations() == h.vibrations() == [6, 7, 8]
    assert [o.freqs[k] for k in o.vibrations()] == REPORT
    for k in o.vibrations():
        # six printed decimals against the .hess's ten
        assert max(abs(a - b) for a, b in zip(o.modes[k], h.modes[k])) < 1e-6
        assert math.sqrt(sum(v * v for v in o.modes[k])) == pytest.approx(1.0, abs=1e-5)
    # the geometry is the .out's own, in its own frame
    assert o.coords[0] == (0.0, -0.0, 0.120274)


def test_a_report_mode_is_matched_by_wavenumber():
    h = read_hess(HESS)
    assert report_columns(h, REPORT) == [6, 7, 8]
    # %freq ScalFreq: the .out prints scaled numbers, the modes are the same
    assert report_columns(h, [f * 0.97 for f in REPORT]) == [6, 7, 8]


def test_another_hessians_modes_are_refused():
    h = read_hess(HESS)
    with pytest.raises(HessError, match="mode 2 is 3822.87 cm⁻¹ where the report has 3700.00"):
        report_columns(h, [1606.44, 3700.0, 3905.68])
    with pytest.raises(HessError, match="3 vibrations and the report lists 2"):
        report_columns(h, REPORT[:2])


def test_the_hess_geometry_is_compared_after_a_translation():
    h, o = read_hess(HESS), read_out_modes(OUT)
    # measured on this run: the .hess sits 0.054 Å away along z
    assert max(abs(a[2] - b[2]) for a, b in zip(h.coords, o.coords)) > 0.05
    placed, dev = place_on(h, o.symbols, o.coords)
    assert dev < 1e-5
    assert placed[1] == pytest.approx(o.coords[1], abs=1e-5)
    with pytest.raises(HessError, match="another order"):
        place_on(h, ["H", "O", "H"], o.coords)


def test_the_hess_first_then_the_out(tmp_path):
    hess, out = tmp_path / "w.hess", tmp_path / "w.out"
    shutil.copy(OUT, out)
    # no .hess beside the output: the .out's own table
    m, cols, origin = modes_for_report(hess, out, REPORT)
    assert (origin, cols) == ("w.out", [6, 7, 8])
    # with one, the .hess
    shutil.copy(HESS, hess)
    m, cols, origin = modes_for_report(hess, out, REPORT)
    assert origin == "w.hess"
    # a .hess that is another Hessian's is passed over for the .out
    text = hess.read_text(encoding="utf-8").replace("3822.87", "3700.00")
    hess.write_text(text, encoding="utf-8")
    m, cols, origin = modes_for_report(hess, out, REPORT)
    assert origin == "w.out"


def test_no_modes_anywhere_is_a_sentence(tmp_path):
    out = tmp_path / "w.out"
    out.write_text("ORCA TERMINATED NORMALLY\n", encoding="utf-8")
    with pytest.raises(HessError, match="No normal modes to animate — there is no w.hess "
                                        "beside the output; w.out has no frequency table"):
        modes_for_report(tmp_path / "w.hess", out, REPORT)


_CORPUS = Path(r"D:\11_ComputationalChem\Orca\Orcadesk")


@pytest.mark.skipif(not _CORPUS.is_dir(), reason="real ORCA corpus not on this machine")
def test_every_real_frequency_run_reads_the_same_from_either_file():
    """Every freq run in the corpus: the report's wavenumbers select the same
    vectors from the .hess and from the .out, on the same structure. Covers a
    TS with two imaginary modes, opt+freq runs whose .out holds TWO frequency
    tables (Calc_Hess, then the final one), and a 26-atom SMD run."""
    from orcamgr.core.parser import parse_file
    seen = 0
    for out in sorted(_CORPUS.glob("*/*.out")):
        hess = out.with_suffix(".hess")
        r = parse_file(out)
        if not r.frequencies or not hess.is_file():
            continue
        h, ch, _ = modes_for_report(hess, None, r.frequencies)
        o, co, _ = modes_for_report(None, out, r.frequencies)
        assert ch == co
        for k in ch:
            assert max(abs(a - b) for a, b in zip(h.modes[k], o.modes[k])) < 1e-6
        syms = [a.symbol for a in r.geometry]
        xyz = [(a.x, a.y, a.z) for a in r.geometry]
        assert place_on(h, syms, xyz)[1] < 1e-5
        assert place_on(o, syms, xyz)[1] < 1e-5
        seen += 1
    assert seen
