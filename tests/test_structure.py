"""Unit tests for structure screening (orcamgr.core.structure).

Pure geometry, no Qt and no ORCA — the same functions the Build tab's geometry
preview and NEB endpoint card call through the Bridge.

The contracts pinned here:

* **P26** — every finding is *detected and reported*, never auto-corrected: a
  parity clash, an overlap and an endpoint order mismatch all come back as
  data, and no function ever returns a "fixed" structure the user did not ask
  for.
* **P2** — a chemically legitimate oddity (a two-fragment complex, a file in
  Bohr) is a warning that leaves ``ok`` true; only what ORCA will actually
  refuse is an error.
* **P4** — ``input_generator.check_neb_atom_order`` is a projection of
  ``compare_atom_order``, not a second implementation.
* everything here READS: there is no structure editing to test, because
  building molecules is a molecular editor's job and not ORCAdesk's.
"""

import math

import pytest

from orcamgr.core.input_generator import check_neb_atom_order
from orcamgr.core.structure import (
    atomic_number, bonds, check_geometry, compare_atom_order, covalent_radius,
    electron_count, formula, fragments, normalize_symbol, parse_block,
)

# trans-H2O2 — a four-atom chain, so bond perception has something to chain.
HOOH = (
    "H  -0.300   0.900   0.000\n"
    "O   0.000   0.000   0.000\n"
    "O   1.450   0.000   0.000\n"
    "H   1.750  -0.900   0.000"
)

# Water at its experimental geometry (r 0.958 A, angle 104.5 deg).
WATER = (
    "O   0.0000   0.0000   0.0000\n"
    "H   0.9584   0.0000   0.0000\n"
    "H  -0.2396   0.9273   0.0000"
)

def _atoms(text):
    return parse_block(text)


# ---------------------------------------------------------------------------
# element data and parsing
# ---------------------------------------------------------------------------

def test_symbols_normalize_case_and_labels():
    """ORCA is case-insensitive and legacy tools write CL; numbered labels
    (C1, C2) name the same element."""
    assert normalize_symbol("CL") == "Cl"
    assert normalize_symbol("c") == "C"
    assert normalize_symbol("C12") == "C"
    assert normalize_symbol("") == ""


def test_atomic_number_and_radius_survive_odd_symbols():
    assert atomic_number("h") == 1
    assert atomic_number("CL") == 17
    assert atomic_number("Xx") is None
    assert covalent_radius("C") == pytest.approx(0.76)
    assert covalent_radius("Xx") > 0        # unlisted falls back, never raises


def test_parse_block_accepts_a_full_xyz_header_and_skips_junk():
    """P27: tolerant reader — a header, a blank line and a truncated line all
    pass through without an exception."""
    atoms = parse_block("3\nsome comment\nO 0 0 0\n\nH 1 0\nH 0 1 0")
    assert [a.sym for a in atoms] == ["O", "H"]


def test_formula_is_hill_notation():
    assert formula(_atoms(WATER)) == "H2O"
    assert formula(parse_block("C 0 0 0\nH 1 0 0\nO 0 1 0\nH 0 0 1")) == "CH2O"


# ---------------------------------------------------------------------------
# connectivity
# ---------------------------------------------------------------------------

def test_bonds_and_fragments_of_a_single_molecule():
    atoms = _atoms(HOOH)
    assert sorted(bonds(atoms)) == [(0, 1), (1, 2), (2, 3)]
    assert fragments(atoms) == [[0, 1, 2, 3]]


def test_two_molecules_far_apart_are_two_fragments():
    far = WATER + "\n" + "\n".join(
        f"{a.sym} {a.x + 8:.4f} {a.y:.4f} {a.z:.4f}" for a in _atoms(WATER))
    groups = fragments(_atoms(far))
    assert [len(g) for g in groups] == [3, 3]
    assert groups[0] == [0, 1, 2]        # ordered by lowest index, i.e. by file


# ---------------------------------------------------------------------------
# screening (P26): caught before the launch, never corrected
# ---------------------------------------------------------------------------

def test_a_clean_neutral_molecule_passes_with_its_census():
    res = check_geometry(WATER, charge=0, multiplicity=1)
    assert res["ok"] is True
    assert res["issues"] == []
    assert (res["n_atoms"], res["formula"], res["electrons"]) == (3, "H2O", 10)
    assert (res["n_bonds"], res["n_fragments"]) == (2, 1)


def test_electron_count_follows_the_charge():
    atoms = _atoms(WATER)
    assert electron_count(atoms, 0) == 10
    assert electron_count(atoms, 1) == 9
    assert electron_count(atoms, -1) == 11
    assert electron_count(parse_block("Xx 0 0 0"), 0) is None


def test_even_electron_count_with_even_multiplicity_is_an_error():
    """The mistake that costs a whole job: ORCA aborts on this at the first SCF
    step, so it is caught in the form instead (P26)."""
    res = check_geometry(WATER, charge=0, multiplicity=2)
    codes = [i["code"] for i in res["issues"]]
    assert res["ok"] is False
    assert "multiplicity_parity" in codes
    msg = next(i["message"] for i in res["issues"] if i["code"] == "multiplicity_parity")
    assert "10 electrons" in msg and "Nearest: 1" in msg


def test_odd_electron_count_needs_an_even_multiplicity():
    radical = "C 0 0 0\nH 1.09 0 0\nH -0.54 0.94 0\nH -0.54 -0.94 0"   # CH3
    assert check_geometry(radical, charge=0, multiplicity=2)["ok"] is True
    bad = check_geometry(radical, charge=0, multiplicity=1)
    assert bad["ok"] is False
    assert "multiplicity_parity" in [i["code"] for i in bad["issues"]]


def test_a_charge_that_fixes_the_parity_passes():
    """Hydroxide: 9 electrons neutral, 10 as the anion — the anion is the
    closed shell, and the checker must agree."""
    oh = "O 0 0 0\nH 0.96 0 0"
    assert check_geometry(oh, charge=-1, multiplicity=1)["ok"] is True
    assert check_geometry(oh, charge=0, multiplicity=1)["ok"] is False
    assert check_geometry(oh, charge=0, multiplicity=2)["ok"] is True


def test_multiplicity_beyond_the_available_electrons_is_rejected():
    res = check_geometry("H 0 0 0", charge=0, multiplicity=4)
    assert res["ok"] is False
    assert "multiplicity_too_high" in [i["code"] for i in res["issues"]]


def test_duplicate_and_too_close_atoms_are_errors_with_their_indices():
    dup = check_geometry("O 0 0 0\nO 0 0 0", 0, 1)
    assert dup["ok"] is False
    issue = next(i for i in dup["issues"] if i["code"] == "duplicate_atom")
    assert issue["atoms"] == [0, 1]

    tight = check_geometry("C 0 0 0\nC 0.80 0 0", 0, 1)
    assert tight["ok"] is False
    assert "atoms_too_close" in [i["code"] for i in tight["issues"]]


def test_a_normal_triple_bond_is_not_flagged_as_too_close():
    """The overlap threshold has to clear the shortest real bond there is:
    acetylene's C-C is 1.20 A, well inside covalent-radius-sum territory."""
    hccH = "C 0 0 0\nC 1.20 0 0\nH -1.06 0 0\nH 2.26 0 0"
    codes = [i["code"] for i in check_geometry(hccH, 0, 1)["issues"]]
    assert "atoms_too_close" not in codes and "duplicate_atom" not in codes


def test_coordinates_in_bohr_read_as_a_molecule_with_no_bonds():
    """P2: a units mistake is a warning that names the cause, not a refusal —
    a lone atom or a genuine van der Waals pair is legitimate."""
    bohr = "\n".join(f"{a.sym} {a.x / 0.529177:.4f} {a.y / 0.529177:.4f} "
                     f"{a.z / 0.529177:.4f}" for a in _atoms(WATER))
    res = check_geometry(bohr, 0, 1)
    warn = next(i for i in res["issues"] if i["code"] == "no_bonds")
    assert res["ok"] is True                    # a warning never blocks
    assert warn["level"] == "warn" and "Bohr" in warn["message"]


def test_a_two_molecule_complex_warns_but_still_passes():
    pair = WATER + "\n" + "\n".join(
        f"{a.sym} {a.x + 8:.4f} {a.y:.4f} {a.z:.4f}" for a in _atoms(WATER))
    res = check_geometry(pair, 0, 1)
    assert res["ok"] is True and res["n_fragments"] == 2
    assert "disconnected" in [i["code"] for i in res["issues"]]


def test_an_unknown_element_is_an_error_because_orca_refuses_it_too():
    res = check_geometry("Xx 0 0 0\nH 1 0 0", 0, 1)
    assert res["ok"] is False
    assert "unknown_element" in [i["code"] for i in res["issues"]]
    assert res["electrons"] is None             # unknowable, not guessed


def test_empty_coordinates_report_rather_than_raise():
    res = check_geometry("", 0, 1)
    assert res["ok"] is False and res["n_atoms"] == 0
    assert res["issues"][0]["code"] == "no_atoms"


# ---------------------------------------------------------------------------
# NEB endpoint comparison
# ---------------------------------------------------------------------------

REACTANT = "C 0 0 0\nH 0 0 1.1\nO 1.2 0 0"


def test_matching_endpoints_report_every_census_field():
    res = compare_atom_order(REACTANT, REACTANT)
    assert res["ok"] is True and res["error"] == ""
    assert res["mismatch_index"] is None and res["n_mismatches"] == 0
    assert res["n_reactant"] == res["n_product"] == 3
    assert res["formula_reactant"] == res["formula_product"] == "CHO"


def test_a_swap_reports_every_diverging_index_not_just_the_first():
    """What the mismatch table needs and the three-key gate cannot give: the
    full list, so the user sees the shape of the error at a glance."""
    swapped = "C 0 0 0\nO 1.2 0 0\nH 0 0 1.1"
    res = compare_atom_order(REACTANT, swapped)
    assert res["ok"] is False
    assert res["mismatch_index"] == 1
    assert res["n_mismatches"] == 2
    assert res["mismatches"] == [
        {"index": 1, "reactant": "H", "product": "O"},
        {"index": 2, "reactant": "O", "product": "H"},
    ]


def test_endpoint_count_and_composition_errors_name_the_difference():
    fewer = compare_atom_order(REACTANT, "C 0 0 0\nH 0 0 1.1")
    assert "count" in fewer["error"].lower() and fewer["mismatches"] == []

    other = compare_atom_order(REACTANT, "C 0 0 0\nH 0 0 1.1\nN 1.2 0 0")
    assert "composition" in other["error"].lower()
    assert other["mismatch_index"] is None      # indices line up, elements don't
    assert other["n_mismatches"] == 1           # ...and the table still says where


def test_endpoint_symbols_compare_case_insensitively():
    res = compare_atom_order("Cl 0 0 0\nH 1 0 0", "CL 0 0 1\nH 1 0 1")
    assert res["ok"] is True


def test_check_neb_atom_order_is_a_projection_of_the_rich_comparison():
    """P4: one judgment, two callers — the generator's three-key gate must not
    drift from the checker the Build tab shows."""
    for product in (REACTANT, "C 0 0 0\nO 1.2 0 0\nH 0 0 1.1", "C 0 0 0", ""):
        rich = compare_atom_order(REACTANT, product)
        gate = check_neb_atom_order(REACTANT, product)
        assert gate == {"ok": rich["ok"], "error": rich["error"],
                        "mismatch_index": rich["mismatch_index"]}
        assert set(gate) == {"ok", "error", "mismatch_index"}


def test_bond_perception_scales_to_a_large_structure():
    """Bond perception runs on the Qt UI thread on every charge keystroke, so
    the grid search has to hold up where an all-pairs sweep would not — and
    give the identical answer."""
    atoms = parse_block("\n".join(f"C {i * 1.4:.3f} 0 0" for i in range(2000)))
    found = bonds(atoms)
    assert found == [(i, i + 1) for i in range(1999)]   # a chain, each to the next
    assert len(fragments(atoms, found)) == 1


def test_the_grid_search_agrees_with_the_all_pairs_answer():
    """The grid is an optimization, not a different heuristic: on a jumbled 3D
    structure it must reproduce the definition exactly."""
    import random
    rnd = random.Random(20260901)
    syms = ["C", "H", "O", "N", "Cl", "Fe"]
    atoms = parse_block("\n".join(
        f"{rnd.choice(syms)} {rnd.uniform(-9, 9):.4f} {rnd.uniform(-9, 9):.4f} "
        f"{rnd.uniform(-9, 9):.4f}" for _ in range(220)))
    radii = [covalent_radius(a.sym) for a in atoms]

    def dist(a, b):
        return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)

    naive = [(i, j)
             for i in range(len(atoms)) for j in range(i + 1, len(atoms))
             if dist(atoms[i], atoms[j]) <= radii[i] + radii[j] + 0.45]
    assert bonds(atoms) == naive


def test_non_finite_coordinates_are_not_coordinates():
    """'inf'/'nan' parse as floats and would poison every distance and the
    neighbour grid's cell arithmetic."""
    atoms = parse_block("O 0 0 0\nH inf 0 0\nH nan 1 0\nH 0.96 0 0")
    assert len(atoms) == 2
    assert check_geometry("O 0 0 0\nH inf 0 0", 0, 1)["n_atoms"] == 1
