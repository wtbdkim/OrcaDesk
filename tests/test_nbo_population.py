"""Unit tests for bond orders and electron configurations (orcamgr.nbo.population).

Bond orders are the one part of this package with unambiguous right answers, so
they are checked against the answers rather than against properties: dinitrogen
is a triple bond, hydrogen iodide is a single bond, and water's O-H is a polar
single bond that comes in below one. A formula that double-counted, dropped a
factor of two, or read the density in the wrong basis could not produce all
three.

The configurations are checked for the arithmetic that has to hold (core +
valence + Rydberg is the atom's whole population, and the charge is what is left
of the nuclear charge) plus the one case where the shell bookkeeping is easy to
get wrong: an ECP iodine, whose core is 4s4p and whose 4d must still appear.
"""

import numpy as np
import pytest

from orcamgr.nbo.nao import natural_atomic_orbitals
from orcamgr.nbo.population import (
    BOND_THRESHOLD, atom_populations, natural_valences, wiberg_bond_orders,
    wiberg_bonds,
)
from orcamgr.nbo.wavefunction import load_molden

from test_nbo_wavefunction import FIXTURES


def _nao(stem: str):
    return natural_atomic_orbitals(load_molden(FIXTURES / f"{stem}.molden.input"))


# --------------------------------------------------------------------------
# bond orders against bonds everyone knows
# --------------------------------------------------------------------------

def test_dinitrogen_is_a_triple_bond():
    bonds = wiberg_bonds(_nao("n2"))
    assert len(bonds) == 1
    i, j, order = bonds[0]
    assert (i, j) == (0, 1)
    assert order == pytest.approx(3.0, abs=0.1)


def test_hydrogen_iodide_is_a_single_bond():
    _i, _j, order = wiberg_bonds(_nao("hi"))[0]
    assert order == pytest.approx(1.0, abs=0.1)


def test_water_oh_is_a_polar_single_bond():
    # A polar bond comes in below one: the shared density is not shared evenly,
    # and the Wiberg index counts what is shared.
    bonds = wiberg_bonds(_nao("h2o"))
    assert len(bonds) == 2                       # two O-H, no H-H
    assert all({i, j} & {0} for i, j, _o in bonds)
    assert all(0.7 < o < 0.95 for _i, _j, o in bonds)


def test_the_two_hydrogens_of_water_are_not_bonded_to_each_other():
    orders = wiberg_bond_orders(_nao("h2o"))
    assert orders[1, 2] < BOND_THRESHOLD


def test_bond_orders_are_symmetric_with_no_self_bond():
    orders = wiberg_bond_orders(_nao("h2o"))
    assert orders == pytest.approx(orders.T)
    assert np.diag(orders) == pytest.approx(np.zeros(3))


def test_the_bond_list_is_ordered_and_upper_triangular():
    bonds = wiberg_bonds(_nao("hi"))
    assert all(i < j for i, j, _o in bonds)
    orders = [o for _i, _j, o in bonds]
    assert orders == sorted(orders, reverse=True)


def test_the_threshold_decides_what_counts_as_a_bond():
    nao = _nao("h2o")
    assert len(wiberg_bonds(nao, threshold=0.5)) == 2      # the two O-H
    assert len(wiberg_bonds(nao, threshold=5.0)) == 0      # nothing is that strong
    assert len(wiberg_bonds(nao, threshold=0.0)) == 3      # every pair, H-H included


def test_natural_valence_is_the_sum_of_an_atom_s_bond_orders():
    nao = _nao("h2o")
    orders = wiberg_bond_orders(nao)
    valences = natural_valences(nao)
    assert valences == pytest.approx(orders.sum(axis=1))
    # Oxygen's valence is its two O-H bonds exactly; a hydrogen's is its one
    # bond PLUS the weak index it has with the other hydrogen, which is why the
    # two are not in a clean 2:1 ratio.
    assert valences[0] == pytest.approx(orders[0, 1] + orders[0, 2])
    assert valences[1] == pytest.approx(orders[0, 1] + orders[1, 2])
    assert 0 < orders[1, 2] < BOND_THRESHOLD
    assert valences[0] == pytest.approx(2 * valences[1], rel=0.01)   # divalent


def test_nitrogen_is_trivalent_and_hydrogen_monovalent():
    assert natural_valences(_nao("n2"))[0] == pytest.approx(3.0, abs=0.1)
    assert natural_valences(_nao("hi"))[1] == pytest.approx(1.0, abs=0.1)


def test_bond_orders_barely_move_between_basis_sets():
    # Same reason NPA charges do not: the NAO basis is what absorbs the extra
    # functions, so the bond they describe is the same bond.
    svp = wiberg_bonds(_nao("h2o"))[0][2]
    tzvp = wiberg_bonds(_nao("h2o_tzvp"))[0][2]
    assert abs(svp - tzvp) < 0.05


# --------------------------------------------------------------------------
# the per-atom table
# --------------------------------------------------------------------------

def test_the_population_split_accounts_for_every_electron():
    nao = _nao("h2o")
    rows = atom_populations(nao)
    for row in rows:
        assert row.core + row.valence + row.rydberg == pytest.approx(row.total)
        # ... and the total is what the NAOs on that atom actually hold
        assert row.total == pytest.approx(
            nao.occupations[nao.atom == row.index].sum())
    assert sum(r.total for r in rows) == pytest.approx(nao.n_electrons)


def test_charge_is_what_is_left_of_the_nuclear_charge():
    wf = load_molden(FIXTURES / "h2o.molden.input")
    for row in atom_populations(natural_atomic_orbitals(wf)):
        assert row.charge == pytest.approx(
            wf.nuclear_charges[row.index] - row.total, abs=1e-9)


def test_water_reads_as_a_chemist_would_write_it():
    oxygen, hydrogen, _ = atom_populations(_nao("h2o"))
    assert oxygen.element == "O"
    assert oxygen.core == pytest.approx(2.0, abs=2e-3)     # the 1s pair
    assert oxygen.configuration.startswith("[core] 2s(")
    assert "2p(" in oxygen.configuration
    # Hydrogen has no core at all, so it gets no [core] prefix.
    assert hydrogen.core == 0.0
    assert hydrogen.configuration.startswith("1s(")


def test_an_ecp_atom_keeps_its_shells_straight():
    # Iodine's core is 4s4p (8 electrons) once the potential has replaced 1s-3d;
    # its filled 4d is not in krypton, so it is reported in the valence and must
    # still appear, at ten electrons.
    iodine, hydrogen = atom_populations(_nao("hi"))
    assert iodine.element == "I"
    assert iodine.core == pytest.approx(8.0, abs=1e-3)
    assert "4d( 10.00)" in iodine.configuration
    assert "5s(" in iodine.configuration and "5p(" in iodine.configuration
    assert "1s(" not in iodine.configuration          # replaced by the potential
    assert hydrogen.core == 0.0


def test_symmetry_equivalent_atoms_get_identical_rows():
    left, right = atom_populations(_nao("n2"))
    assert left.charge == pytest.approx(right.charge, abs=1e-9)
    assert left.configuration == right.configuration
    assert left.valence_index == pytest.approx(right.valence_index)


def test_spin_rides_along_from_the_orbital_analysis():
    assert all(row.spin == 0.0 for row in atom_populations(_nao("h2o")))
    rows = atom_populations(_nao("h2o_cation"))
    assert sum(r.spin for r in rows) == pytest.approx(1.0, abs=1e-6)


def test_a_rydberg_population_too_small_to_matter_is_left_out_of_the_string():
    # The configuration is meant to be read, so a 0.001-electron tail is noise.
    for row in atom_populations(_nao("n2")):
        assert ("Ryd(" in row.configuration) == (row.rydberg >= 0.005)
