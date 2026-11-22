"""ORCA's orbital localization printout (%loc), read from the .out.

``sn2_oh_ch3br_iaoibo.out`` is real ORCA 6.1.1 output (IAO-IBO localization of
an OH- + CH3Br backside complex, the start of an SN2 reaction, M06-2X/
6-311++G**), trimmed to the input echo and the localization block.
"""

from pathlib import Path

import pytest

from orcamgr.core.parser import parse_file
from orcamgr.state import schemas

FIX = Path(__file__).parent / "loc" / "fixtures"


@pytest.fixture(scope="module")
def loc():
    L = parse_file(str(FIX / "sn2_oh_ch3br_iaoibo.out")).localization
    assert L is not None
    return L


def test_a_run_without_localization_has_none():
    assert parse_file(str(Path(__file__).parent / "nbo" / "fixtures" / "h2o.out")).localization is None


def test_the_settings_and_counts_are_read(loc):
    assert loc["method"] == "IAO-IBO"
    assert loc["range"] == [11, 26]
    assert (loc["strong_threshold"], loc["bond_threshold"]) == (0.95, 0.85)
    # Br keeps its 3d shell in the valence range: eight Br orbitals, three O
    # lone pairs; three C-H, the C-Br and the O-H bond
    assert (loc["n_strong"], loc["n_bond"], loc["n_deloc"]) == (11, 5, 0)


def test_every_lmo_is_listed_once_in_mo_order_with_its_atoms(loc):
    orbs = loc["orbitals"]
    assert [o["mo"] for o in orbs] == list(range(11, 27))
    assert sum(o["kind"] == "one" for o in orbs) == loc["n_strong"]
    assert sum(o["kind"] == "two" for o in orbs) == loc["n_bond"]
    by = {o["mo"]: o for o in orbs}
    # "MO  18:   1Br -   1.005991": a two-letter element follows its index
    assert by[18] == {"mo": 18, "kind": "one",
                      "parts": [{"atom": 1, "el": "Br", "pop": 1.005991}]}
    assert by[21] == {"mo": 21, "kind": "one",
                      "parts": [{"atom": 5, "el": "O", "pop": 0.981649}]}
    # the C-Br bond the reaction breaks
    assert by[22] == {"mo": 22, "kind": "two",
                      "parts": [{"atom": 1, "el": "Br", "pop": 0.654019},
                                {"atom": 0, "el": "C", "pop": 0.347814}]}


def test_the_parsed_shape_is_the_wire_payload(loc):
    assert set(loc) == set(schemas.LocalizationPayload.__annotations__)
    assert set(loc["orbitals"][0]) == set(schemas.LmoPayload.__annotations__)
    assert set(loc["orbitals"][0]["parts"][0]) == set(schemas.LmoPartPayload.__annotations__)
