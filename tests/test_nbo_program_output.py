"""The NBO program's own printout, read from an ORCA ``! NBO`` run's .out.

``fch2ch2f_anti_nbo7.out`` is a real ORCA 6.1.1 + NBO 7.0.10 output
(FCH2CH2F, anti, wB97X-D4/def2-TZVP, closed shell), trimmed: the input echo,
then the NBO block verbatim except the per-NBO coefficient listing, which the
parser does not read. These numbers are NBO's, not ORCAdesk's own analysis
(orcamgr/nbo/), and the parser keeps them in ``ParseResult.nbo_program``.
"""

import glob
import os
from pathlib import Path

import pytest

from orcamgr.core.parser import parse_file
from orcamgr.state import schemas

FIX = Path(__file__).parent / "nbo" / "fixtures"
# cis/trans N2F2 NBO 7 runs, when the example folder is on this machine
_NBO = sorted(glob.glob(str(Path(r"D:\11_ComputationalChem\KC_ORCA calculation example by Yeosan Lee")
                            / "08_Natural Bond Orbital Analysis" / "*NBO*" / "*.out")))


@pytest.fixture(scope="module")
def nbo():
    n = parse_file(str(FIX / "fch2ch2f_anti_nbo7.out")).nbo_program
    assert n is not None
    return n


def test_a_run_without_nbo_has_no_nbo_program_section():
    assert parse_file(str(FIX / "h2o.out")).nbo_program is None


def test_the_version_and_shell_are_read(nbo):
    assert nbo["version"] == "7.0.10"
    assert nbo["open_shell"] is False


def test_the_natural_population_summary_is_read_atom_by_atom(nbo):
    atoms = nbo["atoms"]
    assert [(a["index"], a["el"]) for a in atoms] == [
        (0, "C"), (1, "C"), (2, "F"), (3, "H"), (4, "H"), (5, "H"), (6, "H"), (7, "F")]
    f3 = atoms[2]
    assert (f3["charge"], f3["core"], f3["valence"], f3["rydberg"], f3["total"]) == (
        -0.35458, 2.0, 7.34595, 0.00863, 9.35458)
    assert f3["config"] == "[core]2s( 1.85)2p( 5.50)3d( 0.01)"
    assert atoms[3]["config"] == "1s( 0.84)"
    # a neutral molecule: the natural charges sum to zero, the populations to 34
    assert sum(a["charge"] for a in atoms) == pytest.approx(0.0, abs=1e-4)
    assert sum(a["total"] for a in atoms) == pytest.approx(34.0, abs=1e-4)


def test_the_lewis_accounting_is_read_from_the_nbo_section_not_the_npa_one(nbo):
    # "Core" also heads a line of the NPA block above, at 99.9997%
    assert nbo["lewis"]["core"] == {"e": 7.99997, "pct": 100.0, "of": 8}
    assert nbo["lewis"]["total_lewis"] == {"e": 33.82726, "pct": 99.492, "of": 34}
    assert nbo["lewis"]["rydberg_nonlewis"]["e"] == 0.03047
    assert nbo["accepted"] == "Structure accepted: No low occupancy Lewis orbitals"


def test_the_nbo_summary_lists_every_orbital_with_its_centres(nbo):
    orbs = nbo["orbitals"]
    assert len(orbs) == 148 and [o["index"] for o in orbs] == list(range(1, 149))
    assert sum(o["lewis"] for o in orbs) == 17            # 4 CR + 6 LP + 7 BD
    by = {o["index"]: o for o in orbs}
    assert (by[1]["label"], by[1]["atoms"]) == ("CR(1) C1", [0])
    assert (by[13]["label"], by[13]["atoms"], by[13]["occ"]) == ("BD(1) C1–H4", [0, 3], 1.99209)
    assert (by[18]["type"], by[18]["label"], by[18]["lewis"]) == ("BD*", "BD*(1) C1–C2", False)
    assert (by[148]["label"], by[148]["energy_eh"]) == ("RY(26) F8", 6.03719)
    # every electron is in some NBO
    assert sum(o["occ"] for o in orbs) == pytest.approx(34.0, abs=1e-3)


def test_the_e2_table_is_read_and_agrees_with_the_orbital_list(nbo):
    e2 = nbo["interactions"]
    assert len(e2) == 30
    assert e2[4] == {"donor": 7, "donor_label": "LP(3) F3", "acceptor": 18,
                     "acceptor_label": "BD*(1) C1–C2",
                     "e2_kcal": 8.33, "de_au": 0.94, "f_au": 0.079}
    by = {o["index"]: o for o in nbo["orbitals"]}
    for x in e2:
        # a donor is a Lewis orbital, an acceptor a non-Lewis one, and both
        # tables spell them the same way
        assert by[x["donor"]]["lewis"] and not by[x["acceptor"]]["lewis"]
        assert by[x["donor"]]["label"] == x["donor_label"]
        assert by[x["acceptor"]]["label"] == x["acceptor_label"]


def test_the_parsed_shape_is_the_wire_payload(nbo):
    assert set(nbo) == set(schemas.NboProgramPayload.__annotations__)
    assert set(nbo["atoms"][0]) == set(schemas.NboAtomPopPayload.__annotations__)
    assert set(nbo["orbitals"][0]) == set(schemas.NboProgramOrbitalPayload.__annotations__)
    assert set(nbo["interactions"][0]) == set(schemas.NboProgramE2Payload.__annotations__)
    assert set(nbo["lewis"]["core"]) == set(schemas.NboLewisShare.__annotations__)


@pytest.mark.skipif(not _NBO, reason="example corpus not on this machine")
@pytest.mark.parametrize("path", _NBO, ids=os.path.basename)
def test_every_nbo_example_conserves_its_electrons(path):
    n = parse_file(path).nbo_program
    assert n and not n["open_shell"]
    total = sum(a["total"] for a in n["atoms"])
    assert sum(o["occ"] for o in n["orbitals"]) == pytest.approx(total, abs=2e-3)
    by = {o["index"]: o for o in n["orbitals"]}
    for x in n["interactions"]:
        assert by[x["donor"]]["label"] == x["donor_label"]
        assert by[x["acceptor"]]["label"] == x["acceptor_label"]
