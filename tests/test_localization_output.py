"""ORCA's orbital localization printout (%loc), read from the .out.

``sn2_step01_iaoibo.out`` is real ORCA 6.1.1 output (IAO-IBO localization at
the first point of an SN2 reaction path, OH- + 2-bromobutane, M06-2X/
6-311++G**), trimmed to the input echo and the localization block. The corpus
test walks the whole path when the example folder is on this machine.
"""

import glob
from pathlib import Path

import pytest

from orcamgr.core.parser import parse_file
from orcamgr.state import schemas

FIX = Path(__file__).parent / "loc" / "fixtures"
_EXAMPLES = Path(r"D:\11_ComputationalChem\KC_ORCA calculation example by Yeosan Lee")


@pytest.fixture(scope="module")
def loc():
    L = parse_file(str(FIX / "sn2_step01_iaoibo.out")).localization
    assert L is not None
    return L


def test_a_run_without_localization_has_none():
    assert parse_file(str(Path(__file__).parent / "nbo" / "fixtures" / "h2o.out")).localization is None


def test_the_settings_and_counts_are_read(loc):
    assert loc["method"] == "IAO-IBO"
    assert loc["range"] == [14, 38]
    assert (loc["strong_threshold"], loc["bond_threshold"]) == (0.95, 0.85)
    assert (loc["n_strong"], loc["n_bond"], loc["n_deloc"]) == (11, 14, 0)


def test_every_lmo_is_listed_once_in_mo_order_with_its_atoms(loc):
    orbs = loc["orbitals"]
    assert [o["mo"] for o in orbs] == list(range(14, 39))
    assert sum(o["kind"] == "one" for o in orbs) == loc["n_strong"]
    assert sum(o["kind"] == "two" for o in orbs) == loc["n_bond"]
    by = {o["mo"]: o for o in orbs}
    # "MO  24:  14O  -   0.986817": a two-letter element follows its index
    assert by[24] == {"mo": 24, "kind": "one",
                      "parts": [{"atom": 14, "el": "O", "pop": 0.986817}]}
    # the C-Br bond the reaction breaks
    assert by[37] == {"mo": 37, "kind": "two",
                      "parts": [{"atom": 13, "el": "Br", "pop": 0.705144},
                                {"atom": 5, "el": "C", "pop": 0.298393}]}


def test_the_parsed_shape_is_the_wire_payload(loc):
    assert set(loc) == set(schemas.LocalizationPayload.__annotations__)
    assert set(loc["orbitals"][0]) == set(schemas.LmoPayload.__annotations__)
    assert set(loc["orbitals"][0]["parts"][0]) == set(schemas.LmoPartPayload.__annotations__)


_IBO = sorted(glob.glob(str(_EXAMPLES / "07_Intrinsic Bond Orbital Analysis" / "019*" / "*.out")))


@pytest.mark.skipif(not _IBO, reason="example corpus not on this machine")
def test_the_c_br_bond_hands_its_electrons_to_bromine_along_the_path():
    shares = []
    for f in _IBO:
        L = parse_file(f).localization
        assert len(L["orbitals"]) == L["n_strong"] + L["n_bond"]   # none delocalized here
        cbr = [o for o in L["orbitals"] if o["kind"] == "two"
               and {p["el"] for p in o["parts"]} == {"C", "Br"}]
        shares.append(cbr[0]["parts"][0]["pop"] if cbr else None)
    # the last point is past the reaction: the bond is a bromide lone pair
    assert shares[-1] is None
    path = [s for s in shares if s is not None]
    assert path == sorted(path) and path[0] < 0.75 < path[-1]
