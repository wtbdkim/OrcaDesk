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
* everything here READS but one thing: ``apply_constraint_target`` moves a
  constraint's atoms to its target, rigidly, and nothing else. There is no
  structure editor to test, because building molecules is a molecular
  editor's job and not ORCAdesk's.
"""

import math

import pytest

from orcamgr.core.input_generator import check_neb_atom_order
from orcamgr.core.structure import (
    apply_constraint_target, atomic_number, bonds, check_geometry,
    compare_atom_order, covalent_radius, electron_count, formula, fragments,
    measure, normalize_symbol, parse_block,
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


# ---- moving to a constraint's target (the builder's Apply) ---------------------

def _phenol():
    """A planar phenol: ring C1..C6 (atoms 0-5), ring H on C2..C6 (6-10), the
    hydroxyl O (11) on C1 and its H (12) - a group hanging off a ring, which is
    the case the button exists for."""
    rows = []
    for k in range(6):
        a = math.radians(60 * k)
        rows.append(("C", 1.39 * math.cos(a), 1.39 * math.sin(a), 0.0))
    for k in range(1, 6):
        a = math.radians(60 * k)
        rows.append(("H", 2.47 * math.cos(a), 2.47 * math.sin(a), 0.0))
    rows.append(("O", 2.75, 0.0, 0.0))
    rows.append(("H", 3.06, 0.91, 0.0))
    return "\n".join(f"{e:<2s} {x:10.5f} {y:10.5f} {z:10.5f}" for e, x, y, z in rows)


def _dist(a, b):
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


def test_a_dihedral_turns_the_hydroxyl_and_takes_its_hydrogen():
    """The case that asked for this: turning C-O-H out of plane moves the H,
    rigidly, and leaves the ring where it was."""
    text = _phenol()
    r = apply_constraint_target(text, "D", [1, 0, 11, 12], 90.0)
    assert r["ok"], r["error"]
    assert r["moved"] == [12]          # O sits on the hinge axis and stays
    after = parse_block(r["xyz"])
    assert measure(after, [1, 0, 11, 12]) == pytest.approx(90.0, abs=1e-4)
    assert r["after"] == pytest.approx(90.0, abs=1e-4)
    assert r["before"] == pytest.approx(0.0, abs=1e-6)   # H cis to C2
    before = parse_block(text)
    assert _dist(after[11], after[12]) == pytest.approx(_dist(before[11], before[12]), abs=1e-5)
    assert measure(after, [0, 11, 12]) == pytest.approx(measure(before, [0, 11, 12]), abs=1e-4)


def test_the_smaller_side_moves_whichever_way_the_atoms_were_named():
    """A drag can start at either end. The ring never moves for a group
    hanging off it, so naming the angle O-C1-C2 or C2-C1-O is the same edit."""
    text = _phenol()
    fwd = apply_constraint_target(text, "A", [1, 0, 11], 100.0)
    rev = apply_constraint_target(text, "A", [11, 0, 1], 100.0)
    assert fwd["ok"] and rev["ok"]
    assert fwd["moved"] == rev["moved"] == [11, 12]
    assert fwd["xyz"] == rev["xyz"]
    assert measure(parse_block(fwd["xyz"]), [1, 0, 11]) == pytest.approx(100.0, abs=1e-4)


def test_a_bond_target_moves_the_group_along_the_bond():
    text = _phenol()
    r = apply_constraint_target(text, "B", [0, 11], 1.50)
    assert r["ok"] and r["moved"] == [11, 12]
    after, before = parse_block(r["xyz"]), parse_block(text)
    assert _dist(after[0], after[11]) == pytest.approx(1.50, abs=1e-5)
    # a translation along the bond: the O-H vector is untouched
    assert (after[12].x - after[11].x) == pytest.approx(before[12].x - before[11].x)
    assert (after[12].y - after[11].y) == pytest.approx(before[12].y - before[11].y)


def test_a_ring_bond_is_refused_not_bent():
    """No rigid motion changes a ring bond without deforming the ring (P2): the
    answer says so, and that the optimization will still get there."""
    r = apply_constraint_target(_phenol(), "B", [0, 1], 1.60)
    assert not r["ok"]
    assert "ring" in r["error"] and r["xyz"] == ""


def test_only_the_moved_lines_are_rewritten():
    """Every other line is the user's text, byte for byte - header, comments,
    CRLF and all - so an Apply is a small edit and not a reformat."""
    body = _phenol().replace("\n", "\r\n")
    text = "13\r\nphenol, hand made\r\n" + body + "\r\n"
    r = apply_constraint_target(text, "D", [1, 0, 11, 12], 45.0)
    assert r["ok"]
    old, new = text.split("\n"), r["xyz"].split("\n")
    assert len(old) == len(new)
    changed = [n for n, (a, b) in enumerate(zip(old, new)) if a != b]
    assert changed == [2 + 12]
    assert new[2 + 12].endswith("\r") and new[2 + 12].split()[0] == "H"


def test_landing_on_another_atom_is_reported_not_refused():
    r = apply_constraint_target(_phenol(), "A", [1, 0, 11], 35.0)
    assert r["ok"]
    assert r["warnings"] and "closer than a bond" in r["warnings"][0]


@pytest.mark.parametrize("kind, atoms, value, says", [
    ("C", [0], 1.0, "bond, angle or dihedral"),
    ("B", [0, 1, 2], 1.0, "2 different"),
    ("B", [0, 0], 1.0, "different"),
    ("B", [0, 99], 1.0, "no atom 100"),
    ("B", [0, 11], float("nan"), "not a number"),
    ("B", [0, 11], None, "no target"),
    ("B", [0, 11], 0.0, "between"),
    ("A", [1, 0, 11], 200.0, "180"),
])
def test_a_target_it_cannot_reach_is_a_sentence(kind, atoms, value, says):
    r = apply_constraint_target(_phenol(), kind, atoms, value)
    assert not r["ok"] and says in r["error"]


def test_the_dihedral_sign_is_iupacs_like_orcas_and_the_stages():
    """Looking from C1 down to O, C2 points up and H points left: the front
    bond turns ANTI-clockwise onto the back one, which IUPAC (and ORCA, and
    MOL.dihedral on the stage) call -90. The editor this was revived from said
    +90, so an Apply to 90 read back as -90 on the row beside the button."""
    atoms = parse_block("C 0.695 1.2038 0\nC 1.39 0 0\nO 2.75 0 0\nH 3.06 0 -0.91")
    assert measure(atoms, [0, 1, 2, 3]) == pytest.approx(-90.0, abs=1e-6)
    assert measure(atoms, [3, 2, 1, 0]) == pytest.approx(-90.0, abs=1e-6)
