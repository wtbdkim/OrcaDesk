"""Unit tests for natural atomic orbitals and NPA (orcamgr.nbo.nao).

There is no NBO program here to compare against, and the occupancy-weighted
orthogonalization is not specified to the last detail in the literature, so
"matches NBO" is not a claim these tests can or should make. What they check
instead is stronger than a table of numbers would be: the properties that make
the method worth having.

* **Basis-set insensitivity** is the property NPA exists for, and it is
  falsifiable on its own. The same water molecule at def2-SVP and def2-TZVP must
  give nearly the same NPA charge while Mulliken's moves by an order of
  magnitude more. If the orthogonalization were wrong, this is the test that
  would fail.
* **Conservation**: occupancies sum to the electron count, charges to the
  molecular charge, no orbital holds more than two electrons. Both real bugs
  found while writing the module (populations from ``P`` rather than ``S P S``,
  and a density transformed by the overlap's law) violated these and nothing
  else.
* **Chemistry**: core shells come out full, water's two hydrogens come out
  identical, an ECP iodine's minimal basis is 4s/5s/4p/5p/4d and nothing else,
  and the unpaired electron of a cobalt(II) complex sits on the cobalt.
"""

import numpy as np
import pytest

from orcamgr.nbo.nao import (
    _ECP_CORES, NaturalAtomicOrbitals, free_atom_shells, minimal_basis_shells,
    natural_atomic_orbitals, npa_charges,
)
from orcamgr.nbo.wavefunction import WavefunctionError, load_molden

from test_nbo_wavefunction import FIXTURES


def _nao(stem: str) -> NaturalAtomicOrbitals:
    return natural_atomic_orbitals(load_molden(FIXTURES / f"{stem}.molden.input"))


# --------------------------------------------------------------------------
# the minimal basis: which shells a free atom occupies
# --------------------------------------------------------------------------

@pytest.mark.parametrize("z,expected", [
    (1,  {0: 1}),                            # H   1s
    (6,  {0: 2, 1: 1}),                      # C   1s 2s 2p
    (8,  {0: 2, 1: 1}),                      # O   same shells as carbon
    (11, {0: 3, 1: 1}),                      # Na  1s 2s 2p 3s
    (19, {0: 4, 1: 2}),                      # K   4s fills before 3d, so no d
    (27, {0: 4, 1: 2, 2: 1}),                # Co  [Ar] 3d 4s
    (53, {0: 5, 1: 4, 2: 2}),                # I   through 5p
])
def test_free_atom_shell_counts(z, expected):
    assert free_atom_shells(z) == expected


def test_potassium_has_no_d_shell_but_scandium_does():
    # The aufbau order is the point: 4s fills before 3d, so the minimal basis of
    # potassium stops at p while its neighbour scandium gains a d.
    assert 2 not in free_atom_shells(19)
    assert free_atom_shells(21)[2] == 1


def test_every_ecp_core_holds_the_electrons_it_claims():
    # A typo in this table would silently mis-partition an ECP atom's basis, so
    # each entry is checked against its own key.
    for n_core, shells in _ECP_CORES.items():
        electrons = sum(2 * (2 * l + 1) * count for l, count in shells.items())
        assert electrons == n_core, f"core {n_core} lists {electrons} electrons"


def test_an_ecp_removes_shells_from_the_minimal_basis():
    # Iodine keeps 4s 5s / 4p 5p / 4d once a 28-electron core is replaced.
    assert minimal_basis_shells(53, 25.0) == {0: 2, 1: 2, 2: 1}
    # ... and without an ECP it keeps everything.
    assert minimal_basis_shells(53, 53.0) == free_atom_shells(53)


def test_an_unknown_core_is_refused_rather_than_guessed():
    with pytest.raises(WavefunctionError, match="effective core potential"):
        minimal_basis_shells(53, 53.0 - 7)      # no ECP replaces 7 electrons


# --------------------------------------------------------------------------
# conservation -- what the two real bugs violated
# --------------------------------------------------------------------------

@pytest.mark.parametrize("stem", ["h2o", "h2o_cation", "hi", "h2o_tzvp"])
def test_populations_conserve_electrons_and_charge(stem):
    result = _nao(stem)
    checks = result.consistency()
    assert checks["orthonormality"] < 1e-9
    assert checks["electron_count_error"] < 1e-8
    assert checks["charge_error"] < 1e-8


@pytest.mark.parametrize("stem", ["h2o", "h2o_cation", "hi", "h2o_tzvp"])
def test_no_orbital_holds_more_than_two_electrons(stem):
    # Diagonalizing P against S instead of S P S gave oxygen a 1s of 3.02.
    checks = _nao(stem).consistency()
    assert checks["max_occupancy"] <= 2.0 + 1e-6
    assert checks["min_occupancy"] > -1e-8


@pytest.mark.parametrize("stem", ["h2o", "h2o_cation", "hi", "h2o_tzvp"])
def test_the_minimal_basis_holds_almost_all_the_density(stem):
    # The headline NPA diagnostic: the chemistry is meant to live in the
    # minimal basis, with the Rydberg set only its polarization tail.
    assert _nao(stem).minimal_fraction > 0.99


# --------------------------------------------------------------------------
# the property NPA exists for
# --------------------------------------------------------------------------

def test_npa_barely_moves_between_basis_sets_where_mulliken_lurches():
    svp = load_molden(FIXTURES / "h2o.molden.input")
    tzvp = load_molden(FIXTURES / "h2o_tzvp.molden.input")
    assert svp.n_basis == 24 and tzvp.n_basis == 43     # same molecule, 1.8x basis

    npa_shift = abs(npa_charges(svp)[0] - npa_charges(tzvp)[0])
    mulliken_shift = abs(svp.atomic_charges("mulliken")[0]
                         - tzvp.atomic_charges("mulliken")[0])
    loewdin_shift = abs(svp.atomic_charges("loewdin")[0]
                        - tzvp.atomic_charges("loewdin")[0])

    assert npa_shift < 0.06
    # Both of the basis-dependent schemes must be visibly worse, or the
    # orthogonalization is not doing what it is there for.
    assert mulliken_shift > 4 * npa_shift
    assert loewdin_shift > 2 * npa_shift


def test_npa_finds_the_polarity_mulliken_understates():
    # Oxygen in water is strongly negative; Mulliken at this basis reports less
    # than a third of it. NPA landing near -0.9 is what the literature reports.
    wf = load_molden(FIXTURES / "h2o.molden.input")
    npa = npa_charges(wf)[0]
    assert -1.05 < npa < -0.80
    assert npa < wf.atomic_charges("mulliken")[0] - 0.4


def test_symmetry_equivalent_atoms_get_equal_charges():
    # Water's hydrogens are related by symmetry; the m-averaging in the pre-NAO
    # step is what keeps numerical noise from splitting them.
    charges = _nao("h2o").charges
    assert charges[1] == pytest.approx(charges[2], abs=1e-9)


# --------------------------------------------------------------------------
# chemistry
# --------------------------------------------------------------------------

def test_core_shells_come_out_full():
    result = _nao("h2o")
    core = [o for lab, o in zip(result.labels, result.occupations) if lab == "O 1s"]
    assert core and core[0] == pytest.approx(2.0, abs=2e-3)


def test_an_ecp_atom_is_labelled_with_the_shells_it_actually_has():
    # Iodine's 1s-3d were replaced by the potential, so its minimal basis starts
    # at 4s. Counting from 1s instead would push real valence shells into the
    # Rydberg set and mis-weight the orthogonalization.
    result = _nao("hi")
    shells = {lab for lab, keep in zip(result.labels, result.minimal) if keep
              and lab.startswith("I ")}
    assert shells == {"I 4s", "I 5s", "I 4p", "I 5p", "I 4d"}


def test_the_iodine_lone_pairs_are_full_and_the_bonding_p_is_not():
    # HI bonds through one 5p; the other two stay lone pairs. That asymmetry
    # inside a degenerate shell is the whole point of resolving m separately.
    result = _nao("hi")
    p5 = sorted(o for lab, o in zip(result.labels, result.occupations)
                if lab == "I 5p")
    assert len(p5) == 3
    assert p5[0] < 1.5                       # depleted by the bond to hydrogen
    assert p5[1] > 1.9 and p5[2] > 1.9       # the two lone pairs


def test_a_closed_shell_molecule_has_no_spin_density():
    result = _nao("h2o")
    assert np.abs(result.spin_populations).max() == 0.0


def test_the_unpaired_electron_is_accounted_for_and_placed():
    result = _nao("h2o_cation")
    assert result.spin_populations.sum() == pytest.approx(1.0, abs=1e-6)
    assert result.spin_populations[0] > 0.5          # mostly on the oxygen


def test_charges_and_occupancies_line_up_with_the_atoms():
    wf = load_molden(FIXTURES / "h2o.molden.input")
    result = natural_atomic_orbitals(wf)
    assert result.charges.size == wf.n_atoms
    assert result.occupations.size == wf.n_basis
    assert result.atom.tolist() == wf.bf_atom.tolist()
    # every basis function is labelled, and every label names its element
    assert all(result.labels)
    assert all(lab.split()[0] == wf.elements[a]
               for lab, a in zip(result.labels, result.atom))


def test_npa_charges_matches_the_full_analysis():
    wf = load_molden(FIXTURES / "h2o.molden.input")
    assert npa_charges(wf) == pytest.approx(natural_atomic_orbitals(wf).charges)


# --------------------------------------------------------------------------
# refusals
# --------------------------------------------------------------------------

def test_a_cartesian_basis_is_refused(tmp_path):
    # Cartesian d components are not equivalent by symmetry, so averaging over
    # them would be meaningless rather than merely approximate.
    from test_nbo_wavefunction import _molden, _write
    path = _write(tmp_path, _molden(
        gto="  1 0\nd   1 1.0\n    1.0  1.0\n",
        mos="".join(
            f" Ene= {i}.0\n Spin= Alpha\n Occup= 0.0\n"
            + "".join(f"  {j + 1}  {float(i == j)}\n" for j in range(6))
            for i in range(6))))
    with pytest.raises(WavefunctionError, match="Cartesian"):
        natural_atomic_orbitals(load_molden(path))
