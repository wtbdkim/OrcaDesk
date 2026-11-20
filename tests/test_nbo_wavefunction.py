"""Unit tests for the wavefunction reader (orcamgr.nbo.wavefunction).

The module's claim is that a Molden file alone determines the overlap, density
and Fock matrices of the calculation that produced it. That claim is falsifiable
in a way most parsing is not: ORCA prints Mulliken and Loewdin charges in every
``.out``, and both are computable from S and P plus the basis-function-to-atom
map. So the strongest tests here reconstruct the matrices from the Molden file
and check the charges they imply against ORCA's own numbers, read back with
ORCAdesk's existing ``.out`` parser. If the basis ordering, the occupations or
the inversion were wrong, those would not agree.

Fixtures in ``tests/nbo/fixtures/`` are real ORCA 6.1.1 output, one per case the
reconstruction has to get right:

* ``h2o``          - B3LYP/def2-SVP, restricted (closed shell);
* ``h2o_cation``   - B3LYP/def2-SVP UKS, unrestricted (doublet), which also
                     covers ORCA writing ``Spin= Alpha`` but ``Spin=Beta``;
* ``hi``           - HI at B3LYP/def2-SVP, where iodine carries an effective
                     core potential, so its nuclear charge is 25 and not 53.

Structural and failure cases use synthetic minimal Molden text instead, so the
suite stays fast and needs neither ORCA nor the output corpus.
"""

import pathlib

import numpy as np
import pytest

from orcamgr.core.parser import parse_file
from orcamgr.nbo.wavefunction import (
    Wavefunction, WavefunctionError, load_molden, _spherical_m,
)

FIXTURES = pathlib.Path(__file__).parent / "nbo" / "fixtures"

# ORCA prints its populations to six decimals, so agreement can never be better
# than half a unit in the last place. Anything under 1e-5 is that rounding, not
# a difference in the analysis.
PRINT_PRECISION = 1e-5


def _load(stem: str) -> Wavefunction:
    return load_molden(FIXTURES / f"{stem}.molden.input")


def _orca_charges(stem: str) -> tuple[np.ndarray, np.ndarray]:
    """ORCA's own Mulliken and Loewdin charges, via ORCAdesk's .out parser."""
    result = parse_file(str(FIXTURES / f"{stem}.out"))
    return (np.array([v for _, v in result.mulliken_charges]),
            np.array([v for _, v in result.loewdin_charges]))


# --------------------------------------------------------------------------
# synthetic Molden text (structure and failure modes)
# --------------------------------------------------------------------------

def _molden(atoms: str = "H     1    1     0.0  0.0  0.0",
            units: str = "AU",
            gto: str = "  1 0\ns   1 1.0\n    1.0  1.0\n",
            flags: str = "",
            mos: str = " Sym= 1a\n Ene= -0.5\n Spin= Alpha\n Occup= 2.0\n  1  1.0\n") -> str:
    return (f"[Molden Format]\n[Atoms] {units}\n{atoms}\n"
            f"[GTO]\n{gto}\n{flags}[MO]\n{mos}")


def _write(tmp_path, text: str) -> pathlib.Path:
    path = tmp_path / "test.molden.input"
    path.write_text(text, encoding="utf-8")
    return path


def test_minimal_molden_reconstructs_unit_matrices(tmp_path):
    # One normalized basis function holding two electrons: S = [[1]], P = [[2]],
    # F = [[-0.5]]. Every relation the module rests on is checkable by hand here.
    wf = load_molden(_write(tmp_path, _molden()))
    assert wf.n_atoms == 1 and wf.n_basis == 1
    assert wf.restricted and wf.n_electrons == 2.0
    assert wf.overlap() == pytest.approx(np.array([[1.0]]))
    assert wf.density("total") == pytest.approx(np.array([[2.0]]))
    assert wf.fock() == pytest.approx(np.array([[-0.5]]))
    assert wf.atomic_charges("mulliken") == pytest.approx([-1.0])


def test_angstrom_coordinates_are_converted_to_bohr(tmp_path):
    wf = load_molden(_write(tmp_path, _molden(
        atoms="H     1    1     0.0  0.0  1.0", units="Angs")))
    assert wf.coordinates[0, 2] == pytest.approx(1.8897261, rel=1e-6)


def test_au_coordinates_are_left_alone(tmp_path):
    wf = load_molden(_write(tmp_path, _molden(
        atoms="H     1    1     0.0  0.0  1.0", units="AU")))
    assert wf.coordinates[0, 2] == pytest.approx(1.0)


def test_fortran_d_exponents_are_accepted(tmp_path):
    # Molden writers disagree on the exponent letter; ORCA emits E, others D.
    wf = load_molden(_write(tmp_path, _molden(
        gto="  1 0\ns   1 1.0\n    1.0D+00  1.0D+00\n",
        mos=" Ene= -5.0D-01\n Spin= Alpha\n Occup= 2.0\n  1  1.0\n")))
    assert wf.shells[0].exponents == (1.0,)
    assert wf.fock() == pytest.approx(np.array([[-0.5]]))


def test_orbital_block_without_a_symmetry_label_is_still_one_orbital(tmp_path):
    # Blocks are delimited by any key line following coefficients, not by Sym=,
    # which not every Molden writer emits.
    wf = load_molden(_write(tmp_path, _molden(
        gto="  1 0\ns   1 1.0\n    1.0  1.0\ns   1 1.0\n    2.0  1.0\n",
        mos=(" Ene= -0.5\n Spin= Alpha\n Occup= 2.0\n  1  1.0\n  2  0.0\n"
             " Ene=  0.3\n Spin= Alpha\n Occup= 0.0\n  1  0.0\n  2  1.0\n"))))
    assert wf.coefficients["alpha"].shape == (2, 2)
    assert wf.occupations["alpha"] == pytest.approx([2.0, 0.0])


def test_basis_map_has_one_entry_per_basis_function(tmp_path):
    # s + p + d(spherical) on one atom = 1 + 3 + 5 functions.
    wf = load_molden(_write(tmp_path, _molden(
        gto=("  1 0\ns   1 1.0\n    1.0  1.0\np   1 1.0\n    1.0  1.0\n"
             "d   1 1.0\n    1.0  1.0\n"),
        flags="[5D]\n",
        mos="".join(
            f" Ene= {i}.0\n Spin= Alpha\n Occup= 0.0\n"
            + "".join(f"  {j + 1}  {float(i == j)}\n" for j in range(9))
            for i in range(9)))))
    assert wf.n_basis == 9
    assert wf.bf_l.tolist() == [0] + [1] * 3 + [2] * 5
    assert wf.bf_atom.tolist() == [0] * 9
    assert wf.bf_shell.tolist() == [0] + [1] * 3 + [2] * 5
    assert wf.spherical


def test_cartesian_d_shell_has_six_components(tmp_path):
    # No [5D] flag means Cartesian, which is Molden's default.
    wf = load_molden(_write(tmp_path, _molden(
        gto="  1 0\nd   1 1.0\n    1.0  1.0\n",
        mos="".join(
            f" Ene= {i}.0\n Spin= Alpha\n Occup= 0.0\n"
            + "".join(f"  {j + 1}  {float(i == j)}\n" for j in range(6))
            for i in range(6)))))
    assert wf.n_basis == 6
    assert not wf.spherical


def test_molden_orders_p_functions_as_x_y_z():
    # Molden writes p as px, py, pz even in a spherical file: m = +1, -1, 0.
    assert _spherical_m(1) == [1, -1, 0]
    assert _spherical_m(0) == [0]
    assert _spherical_m(2) == [0, 1, -1, 2, -2]
    assert _spherical_m(3) == [0, 1, -1, 2, -2, 3, -3]


# --------------------------------------------------------------------------
# failure modes -- each message has to be one sentence a user can act on
# --------------------------------------------------------------------------

def test_rejects_a_file_that_is_not_molden(tmp_path):
    path = tmp_path / "not.molden.input"
    path.write_text("just some text\n", encoding="utf-8")
    with pytest.raises(WavefunctionError, match="orca_2mkl"):
        load_molden(path)


def test_rejects_a_molden_missing_a_required_section(tmp_path):
    path = _write(tmp_path, "[Molden Format]\n[Atoms] AU\nH 1 1 0.0 0.0 0.0\n")
    with pytest.raises(WavefunctionError, match=r"\[GTO\]"):
        load_molden(path)


def test_refuses_a_coefficient_matrix_that_is_not_square(tmp_path):
    # Two basis functions but one orbital: the virtual space was truncated, so S
    # is not recoverable. Refusing beats returning a plausible wrong overlap.
    wf = load_molden(_write(tmp_path, _molden(
        gto="  1 0\ns   1 1.0\n    1.0  1.0\ns   1 1.0\n    2.0  1.0\n",
        mos=" Ene= -0.5\n Spin= Alpha\n Occup= 2.0\n  1  1.0\n  2  0.0\n")))
    assert wf.n_basis == 2
    with pytest.raises(WavefunctionError, match="linearly-dependent"):
        wf.overlap()


def test_rejects_an_orbital_indexing_past_the_basis(tmp_path):
    with pytest.raises(WavefunctionError, match="basis function"):
        load_molden(_write(tmp_path, _molden(
            mos=" Ene= -0.5\n Spin= Alpha\n Occup= 2.0\n  1  1.0\n  7  1.0\n")))


def test_unknown_density_and_population_names_are_refused(tmp_path):
    wf = load_molden(_write(tmp_path, _molden()))
    with pytest.raises(WavefunctionError, match="unknown density"):
        wf.density("sideways")
    with pytest.raises(WavefunctionError, match="unknown population"):
        wf.gross_populations("bader")


# --------------------------------------------------------------------------
# real wavefunctions: the reconstruction itself
# --------------------------------------------------------------------------

@pytest.mark.parametrize("stem", ["h2o", "h2o_cation", "hi"])
def test_reconstruction_is_self_consistent(stem):
    wf = _load(stem)
    checks = wf.consistency()
    for spin in wf.spins:
        # C^T S C = I is the relation S was inverted out of ...
        assert checks[f"orthonormality_{spin}"] < 1e-9
        # ... and F C = S C e is the one F was inverted out of.
        assert checks[f"eigenvalue_residual_{spin}"] < 1e-8
    assert checks["electron_count_error"] < 1e-8
    assert checks["electron_count"] == pytest.approx(wf.n_electrons)


@pytest.mark.parametrize("stem", ["h2o", "h2o_cation", "hi"])
def test_reproduces_orca_mulliken_and_loewdin_charges(stem):
    # The load-bearing test: ORCA computed these from its own S and P, so
    # agreeing to print precision means ours are the same matrices.
    wf = _load(stem)
    ref_mulliken, ref_loewdin = _orca_charges(stem)
    assert wf.atomic_charges("mulliken") == pytest.approx(
        ref_mulliken, abs=PRINT_PRECISION)
    assert wf.atomic_charges("loewdin") == pytest.approx(
        ref_loewdin, abs=PRINT_PRECISION)


@pytest.mark.parametrize("stem,charge", [("h2o", 0.0), ("h2o_cation", 1.0),
                                         ("hi", 0.0)])
def test_charge_follows_from_the_nuclei_and_the_electron_count(stem, charge):
    assert _load(stem).charge == pytest.approx(charge, abs=1e-9)


def test_restricted_water_has_one_orbital_set_and_no_spin_density():
    wf = _load("h2o")
    assert wf.restricted and wf.spins == ("alpha",)
    assert wf.occupations["alpha"].max() == pytest.approx(2.0)
    assert np.abs(wf.density("spin")).max() == 0.0
    assert np.abs(wf.spin_populations()).max() == 0.0
    # Each spin half of a restricted density is exactly half of the total.
    assert wf.density("alpha") == pytest.approx(0.5 * wf.density("total"))
    assert wf.density("beta") == pytest.approx(wf.density("alpha"))


def test_unrestricted_cation_separates_the_spins():
    wf = _load("h2o_cation")
    assert not wf.restricted and wf.spins == ("alpha", "beta")
    assert wf.occupations["alpha"].sum() == pytest.approx(5.0)
    assert wf.occupations["beta"].sum() == pytest.approx(4.0)
    assert wf.density("total") == pytest.approx(
        wf.density("alpha") + wf.density("beta"))
    assert wf.density("spin") == pytest.approx(
        wf.density("alpha") - wf.density("beta"))
    # One unpaired electron, distributed over the atoms.
    assert wf.spin_populations().sum() == pytest.approx(1.0, abs=1e-8)


def test_both_spin_sets_imply_the_same_overlap():
    # S is derived from the alpha orbitals alone. The beta set spans the same
    # basis, so it is an independent derivation of the same matrix -- a check
    # that the basis ordering is not merely self-consistent but right.
    wf = _load("h2o_cation")
    assert wf.consistency()["overlap_spin_agreement"] < 1e-9


def test_alpha_and_beta_fock_matrices_differ_when_unrestricted():
    wf = _load("h2o_cation")
    assert np.abs(wf.fock("alpha") - wf.fock("beta")).max() > 1e-4


def test_ecp_atom_uses_its_effective_nuclear_charge():
    # Iodine in a def2 basis hides 28 core electrons in an ECP: [Atoms] still
    # reports Z = 53 while the density accounts for 25. Reading [Pseudo] is what
    # keeps its charge from being wrong by the whole core.
    wf = _load("hi")
    assert wf.has_ecp
    assert wf.elements == ["I", "H"]
    assert wf.atomic_numbers.tolist() == [53, 1]
    assert wf.nuclear_charges.tolist() == [25.0, 1.0]
    assert wf.n_electrons == pytest.approx(26.0)


def test_wavefunction_without_ecp_keeps_its_atomic_numbers():
    wf = _load("h2o")
    assert not wf.has_ecp
    assert wf.nuclear_charges.tolist() == wf.atomic_numbers.tolist()


def test_matrices_are_symmetric_and_cached():
    wf = _load("h2o")
    for matrix in (wf.overlap(), wf.density("total"), wf.fock()):
        assert np.abs(matrix - matrix.T).max() == 0.0
    # Repeated access returns the cached object, not a recomputation: the
    # inversion is the expensive step and every layer above calls these.
    assert wf.overlap() is wf.overlap()
    assert wf.fock() is wf.fock()
    assert wf.density("total") is wf.density("total")


def test_basis_covers_every_atom_in_a_real_molecule():
    wf = _load("h2o")
    assert wf.n_basis == 24                      # def2-SVP: O 14 + 2 x H 5
    assert sorted(set(wf.bf_atom.tolist())) == [0, 1, 2]
    assert wf.bf_atom.size == wf.bf_l.size == wf.bf_m.size == wf.bf_shell.size
    assert wf.bf_shell.max() == len(wf.shells) - 1
    assert set(wf.bf_l.tolist()) == {0, 1, 2}    # O carries a polarization d
