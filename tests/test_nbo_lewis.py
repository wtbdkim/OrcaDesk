"""Unit tests for the Lewis-structure search and the second-order table
(orcamgr.nbo.lewis / orcamgr.nbo.perturbation).

The search is a heuristic on top of exact linear algebra, so it is tested two
ways. The algebra has invariants that must hold to machine precision -- the NBO
basis is orthonormal, its occupancies sum to the electron count, the Lewis
orbitals are orthogonal to each other by construction -- and those are asserted
tightly. The heuristic is tested against Lewis structures every chemist can
write down:

* water            O with two lone pairs and two polar O-H bonds (~72% O);
* dinitrogen       one lone pair on each N and a triple bond, 50/50;
* hydrogen iodide  one single bond; the iodine's 4d shell as five lone pairs;
* formamide        the CANONICAL amide, not the zwitterion: a lone pair on N,
                   a C=O double bond whose pi part is pure p, a C-N single --
                   found only at threshold 1.70, because the N lone pair is
                   delocalized into the C=O and holds 1.73;
* the water cation one Lewis structure per spin, the beta one a lone pair short.

And the number that makes the whole package worth having: the amide resonance
LP(N) -> pi*(C=O) at 64 kcal/mol, where the literature puts it at 55-65.
"""

import numpy as np
import pytest

from orcamgr.nbo.lewis import (
    Hybrid, THRESHOLD_LADDER, natural_bond_orbitals,
)
from orcamgr.nbo.nao import natural_atomic_orbitals
from orcamgr.nbo.perturbation import (
    HARTREE_TO_KCAL, PRINT_THRESHOLD_KCAL, second_order_interactions,
)
from orcamgr.nbo.wavefunction import load_molden

from test_nbo_wavefunction import FIXTURES


def _bases(stem: str):
    wf = load_molden(FIXTURES / f"{stem}.molden.input")
    return wf, natural_bond_orbitals(natural_atomic_orbitals(wf))


def _kinds(basis) -> dict:
    out: dict = {}
    for o in basis.orbitals:
        out[o.kind] = out.get(o.kind, 0) + 1
    return out


def _find(basis, elements, kind, *symbols):
    """Orbitals of `kind` whose atoms are exactly these element symbols."""
    return [o for o in basis.orbitals if o.kind == kind
            and sorted(elements[a] for a in o.atoms) == sorted(symbols)]


# --------------------------------------------------------------------------
# invariants that must hold to machine precision
# --------------------------------------------------------------------------

@pytest.mark.parametrize("stem", ["h2o", "n2", "hi", "formamide", "h2o_cation", "h2o_tzvp"])
def test_the_nbo_basis_is_complete_and_orthonormal(stem):
    wf, bases = _bases(stem)
    for basis in bases:
        c = basis.coefficients
        assert c.shape == (wf.n_basis, wf.n_basis)      # one orbital per NAO
        assert np.abs(c.T @ c - np.eye(wf.n_basis)).max() < 1e-9
        assert np.abs(basis.fock - basis.fock.T).max() < 1e-12


@pytest.mark.parametrize("stem", ["h2o", "n2", "hi", "formamide", "h2o_cation"])
def test_occupancies_sum_to_the_electrons_of_that_spin(stem):
    wf, bases = _bases(stem)
    for basis in bases:
        total = sum(o.occupancy for o in basis.orbitals)
        assert total == pytest.approx(basis.total_electrons, abs=1e-8)
        assert basis.lewis_electrons == pytest.approx(
            sum(o.occupancy for o in basis.orbitals if o.is_lewis), abs=1e-8)
    assert sum(b.total_electrons for b in bases) == pytest.approx(wf.n_electrons)


@pytest.mark.parametrize("stem", ["h2o", "n2", "hi", "formamide"])
def test_a_closed_shell_gets_one_orbital_per_electron_pair(stem):
    wf, (basis,) = _bases(stem)
    assert basis.spin == ""
    n_lewis = sum(o.is_lewis for o in basis.orbitals)
    assert n_lewis == round(wf.n_electrons / 2)
    assert basis.lewis_fraction > 0.97


def test_every_bond_has_exactly_one_antibond():
    wf, (basis,) = _bases("formamide")
    bonds = [o for o in basis.orbitals if o.kind == "BD"]
    stars = [o for o in basis.orbitals if o.kind == "BD*"]
    assert len(bonds) == len(stars) == 6
    assert sorted(o.atoms for o in bonds) == sorted(o.atoms for o in stars)


def test_orbital_indices_match_their_positions():
    _wf, (basis,) = _bases("h2o")
    assert [o.index for o in basis.orbitals] == list(range(len(basis.orbitals)))
    assert all(o.is_lewis for o in basis.orbitals[:5])        # Lewis first


# --------------------------------------------------------------------------
# Lewis structures a chemist would draw
# --------------------------------------------------------------------------

def test_water_has_two_lone_pairs_and_two_polar_bonds():
    wf, (basis,) = _bases("h2o")
    assert basis.threshold == THRESHOLD_LADDER[0]
    assert _kinds(basis) == {"CR": 1, "LP": 2, "BD": 2, "BD*": 2, "RY": 17}
    for bond in _find(basis, wf.elements, "BD", "O", "H"):
        assert bond.occupancy > 1.98
        oxygen = next(h for h in bond.hybrids if wf.elements[h.atom] == "O")
        hydrogen = next(h for h in bond.hybrids if wf.elements[h.atom] == "H")
        assert 0.68 < oxygen.share < 0.76          # the O-H polarity
        assert hydrogen.label == "s"
        assert oxygen.label.startswith("sp3")      # ~sp3 on oxygen


def test_dinitrogen_is_a_triple_bond_with_a_lone_pair_on_each_end():
    wf, (basis,) = _bases("n2")
    assert _kinds(basis)["LP"] == 2 and _kinds(basis)["BD"] == 3
    bonds = _find(basis, wf.elements, "BD", "N", "N")
    for bond in bonds:
        assert all(abs(h.share - 0.5) < 1e-6 for h in bond.hybrids)   # homonuclear
    # One sigma bond (s character on both ends), two pi bonds (pure p).
    labels = sorted(h.label for b in bonds for h in b.hybrids)
    assert labels.count("p") == 4
    assert sum(lab.startswith("sp") for lab in labels) == 2


def test_hydrogen_iodide_keeps_the_d_shell_as_lone_pairs():
    wf, (basis,) = _bases("hi")
    k = _kinds(basis)
    assert k["BD"] == 1 and k["CR"] == 4              # 4s 4p as core
    assert k["LP"] == 8                                # 4d x5, 5s, two 5p
    bond = _find(basis, wf.elements, "BD", "I", "H")[0]
    assert bond.occupancy > 1.98


def test_formamide_is_the_canonical_amide_not_the_zwitterion():
    wf, (basis,) = _bases("formamide")
    k = _kinds(basis)
    assert k == {"CR": 3, "LP": 3, "BD": 6, "BD*": 6, "RY": 39}
    assert len(_find(basis, wf.elements, "LP", "O")) == 2
    assert len(_find(basis, wf.elements, "LP", "N")) == 1
    assert len(_find(basis, wf.elements, "BD", "C", "O")) == 2     # a double bond
    assert len(_find(basis, wf.elements, "BD", "C", "N")) == 1     # a single bond
    # The threshold had to come down to catch the delocalized N lone pair.
    assert basis.threshold == pytest.approx(1.70)
    nitrogen_lp = _find(basis, wf.elements, "LP", "N")[0]
    assert 1.65 < nitrogen_lp.occupancy < 1.80


def test_the_carbonyl_pi_bond_is_pure_p_and_its_antibond_is_populated():
    wf, (basis,) = _bases("formamide")
    co = _find(basis, wf.elements, "BD", "C", "O")
    pi = [b for b in co if all(h.label == "p" for h in b.hybrids)]
    sigma = [b for b in co if b not in pi]
    assert len(pi) == 1 and len(sigma) == 1
    assert all(h.label.startswith("sp") for h in sigma[0].hybrids)
    # Where the nitrogen lone pair's missing 0.27 e went: into pi*.
    pi_star = [o for o in _find(basis, wf.elements, "BD*", "C", "O")
               if all(h.label == "p" for h in o.hybrids)][0]
    assert 0.2 < pi_star.occupancy < 0.35


def test_an_open_shell_gets_one_structure_per_spin():
    wf, bases = _bases("h2o_cation")
    assert [b.spin for b in bases] == ["alpha", "beta"]
    alpha, beta = bases
    assert alpha.threshold == pytest.approx(THRESHOLD_LADDER[0] / 2)
    assert _kinds(alpha)["LP"] == 2 and "LP*" not in _kinds(alpha)
    # Beta is one electron short: one lone pair fewer, and its hole shows up
    # as an empty valence orbital on the oxygen.
    assert _kinds(beta)["LP"] == 1 and _kinds(beta)["LP*"] == 1
    hole = [o for o in beta.orbitals if o.kind == "LP*"][0]
    assert wf.elements[hole.atoms[0]] == "O"
    assert hole.occupancy < 0.05
    for basis in bases:
        assert all(o.occupancy <= 1.0 + 1e-6 for o in basis.orbitals)


def test_labels_read_like_an_nbo_listing():
    wf, (basis,) = _bases("formamide")
    labels = [o.label(wf.elements) for o in basis.orbitals]
    assert "LP (1) N3" in labels
    assert "BD (1) C1-O2" in labels and "BD (2) C1-O2" in labels
    assert "BD* (1) C1-O2" in labels
    assert "CR (1) O2" in labels


def test_hybrid_labels():
    assert Hybrid(0, 1.0, {0: 1.0}).label == "s"
    assert Hybrid(0, 1.0, {1: 1.0}).label == "p"
    assert Hybrid(0, 1.0, {0: 0.25, 1: 0.75}).label == "sp3.00"
    assert Hybrid(0, 1.0, {0: 0.30, 1: 0.65, 2: 0.05}).label == "sp2.17d0.17"
    assert Hybrid(0, 1.0, {0: 0.5, 1: 0.5, 2: 0.001}).label == "sp1.00"   # noise dropped


# --------------------------------------------------------------------------
# the second-order table
# --------------------------------------------------------------------------

def test_amide_resonance_is_the_strongest_interaction_in_formamide():
    wf, (basis,) = _bases("formamide")
    table = second_order_interactions(basis)
    top = table[0]
    donor, acceptor = basis.orbitals[top.donor], basis.orbitals[top.acceptor]
    assert donor.label(wf.elements) == "LP (1) N3"
    assert acceptor.kind == "BD*" and sorted(wf.elements[a] for a in acceptor.atoms) == ["C", "O"]
    assert 50 < top.energy_kcal < 75                    # literature: 55-65
    assert top.gap_hartree > 0
    # ... and the formula is the formula
    assert top.energy_kcal == pytest.approx(
        donor.occupancy * top.fock_hartree ** 2 / top.gap_hartree * HARTREE_TO_KCAL)


def test_the_table_is_sorted_and_cut_at_the_threshold():
    _wf, (basis,) = _bases("formamide")
    table = second_order_interactions(basis)
    energies = [x.energy_kcal for x in table]
    assert energies == sorted(energies, reverse=True)
    assert all(e >= PRINT_THRESHOLD_KCAL for e in energies)
    assert second_order_interactions(basis, threshold_kcal=1e6) == []


def test_donors_are_lewis_and_acceptors_are_not():
    _wf, (basis,) = _bases("formamide")
    for x in second_order_interactions(basis):
        assert basis.orbitals[x.donor].is_lewis
        assert not basis.orbitals[x.acceptor].is_lewis


def test_water_has_nothing_worth_delocalizing():
    # No conjugation, no hyperconjugation partner: nothing above a few kcal.
    _wf, (basis,) = _bases("h2o")
    table = second_order_interactions(basis)
    assert table and table[0].energy_kcal < 5.0


def test_an_open_shell_table_is_per_spin():
    _wf, bases = _bases("h2o_cation")
    for basis in bases:
        for x in second_order_interactions(basis):
            assert x.spin == basis.spin
            assert basis.orbitals[x.donor].occupancy <= 1.0 + 1e-6
