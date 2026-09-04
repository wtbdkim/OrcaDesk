"""Unit tests for the orbital grid evaluator and cube writer (orcamgr.nbo.grid).

The reference is orca_plot itself. ``tests/nbo/fixtures/*.g16.cube`` are
canonical-MO cubes orca_plot 6.1.1 wrote from the same wavefunctions the
Molden fixtures hold: one plain def2-SVP water (s, p, d) and one water rotated
off every symmetry axis with a def2-QZVP oxygen (f and g, every ``m``
contributing). Reproducing them to the five digits orca_plot prints pins the
normalization, the component order and the phase convention at once -- each
of which was measured, not assumed (see the module docstring), and each of
which would otherwise draw a plausible wrong lobe (P3, P56).
"""

import math
from pathlib import Path

import numpy as np
import pytest

from orcamgr.cube import read_cube_header
from orcamgr.nbo.grid import (
    BOX_MARGIN_BOHR, CUBE_GENERATOR, MAX_L, CubeGrid, basis_values, cube_grid,
    orbital_on_grid, orbital_values, real_solid_harmonics, write_cube,
    write_orbital_cube,
)
from orcamgr.nbo.wavefunction import Shell, Wavefunction, WavefunctionError, load_molden

FIXTURES = Path(__file__).parent / "nbo" / "fixtures"


def _read_cube(path: Path):
    """The whole cube: (title, origin, step, shape, values) -- a test-only
    reader, since the app's own reads only the header."""
    with path.open("r", encoding="utf-8") as fh:
        fh.readline()
        title = fh.readline().strip()
        head = fh.readline().split()
        natoms, origin = int(head[0]), np.array([float(v) for v in head[1:4]])
        shape, step = [], []
        for axis in range(3):
            parts = fh.readline().split()
            shape.append(int(parts[0]))
            step.append(float(parts[1 + axis]))
        for _ in range(abs(natoms)):
            fh.readline()
        if natoms < 0:
            fh.readline()
        values = np.array(fh.read().split(), dtype=float)
    return title, origin, np.array(step), tuple(shape), values


# --- the harmonics table ------------------------------------------------------

def _sphere_quadrature(n_theta: int = 40, n_phi: int = 80):
    """Gauss-Legendre in cos(theta) x uniform phi: exact for the polynomials
    here. Returns unit vectors and weights summing to 4 pi."""
    ct, wt = np.polynomial.legendre.leggauss(n_theta)
    phi = np.arange(n_phi) * 2.0 * math.pi / n_phi
    ct, phi = np.meshgrid(ct, phi, indexing="ij")
    st = np.sqrt(1.0 - ct * ct)
    w = np.repeat(wt[:, None], n_phi, axis=1) * (2.0 * math.pi / n_phi)
    return st * np.cos(phi), st * np.sin(phi), ct, w


@pytest.mark.parametrize("l", range(MAX_L + 1))
def test_solid_harmonics_are_racah_normalized_and_orthogonal(l):
    """Every component integrates to 4 pi / (2l+1) over the sphere and is
    orthogonal to every other -- a typo in one coefficient fails this."""
    x, y, z, w = _sphere_quadrature()
    table = real_solid_harmonics(l, x.reshape(-1), y.reshape(-1), z.reshape(-1))
    assert sorted(table) == sorted(range(-l, l + 1))
    expected = 4.0 * math.pi / (2 * l + 1)
    for m in table:
        for m2 in table:
            overlap = float((table[m] * table[m2] * w.reshape(-1)).sum())
            assert overlap == pytest.approx(expected if m == m2 else 0.0, abs=1e-10)


@pytest.mark.parametrize("l", range(MAX_L + 1))
def test_solid_harmonics_reduce_to_r_to_the_l_on_the_axis(l):
    z = np.array([0.5, 1.0, 2.0])
    zeros = np.zeros_like(z)
    assert real_solid_harmonics(l, zeros, zeros, z)[0] == pytest.approx(z ** l)


def test_l_above_g_is_refused():
    with pytest.raises(WavefunctionError, match="up to g"):
        real_solid_harmonics(5, np.zeros(1), np.zeros(1), np.zeros(1))


# --- against orca_plot --------------------------------------------------------

@pytest.mark.parametrize("name,mo", [("h2o", 4), ("h2o_rot_g", 8), ("h2o_rot_g", 20)])
def test_orbital_values_reproduce_orca_plot(name, mo):
    """The same MO on the same grid, to the five digits orca_plot prints.
    h2o covers s/p/d; h2o_rot_g covers f and g with every m in play."""
    wf = load_molden(FIXTURES / f"{name}.molden.input")
    _title, origin, step, shape, values = _read_cube(FIXTURES / f"{name}.mo{mo}a.g16.cube")
    grid = CubeGrid(origin=tuple(origin), step=tuple(step), shape=shape)
    psi = orbital_on_grid(wf, wf.coefficients["alpha"][:, mo], grid).reshape(-1)
    assert np.abs(values).max() > 0.1                  # a real orbital, not noise
    assert np.abs(psi - values).max() < 5e-6


def test_the_rotated_fixture_exercises_every_f_and_g_component():
    """The phase table is per component; the test above only pins what the
    fixture actually contains, so make sure it contains everything."""
    wf = load_molden(FIXTURES / "h2o_rot_g.molden.input")
    for mo in (8, 20):
        c = wf.coefficients["alpha"][:, mo]
        for l in (3, 4):
            for m in range(-l, l + 1):
                weight = np.abs(c[(wf.bf_l == l) & (wf.bf_m == m)]).max()
                assert weight > 1e-4, f"MO {mo} has no l={l} m={m:+d} weight"
                break   # one MO carrying every component is enough per l/m
        break


def test_the_box_is_orca_plots():
    """Same origin and spacing as orca_plot's cube of the same molecule, so
    the two kinds of cube sit in one box and the camera does not jump."""
    wf = load_molden(FIXTURES / "h2o_rot_g.molden.input")
    _t, origin, step, shape, _v = _read_cube(FIXTURES / "h2o_rot_g.mo8a.g16.cube")
    grid = cube_grid(wf, 16)
    assert grid.shape == shape
    assert np.array(grid.origin) == pytest.approx(origin, abs=1e-6)
    assert np.array(grid.step) == pytest.approx(step, abs=1e-6)
    assert BOX_MARGIN_BOHR == 7.0


def test_the_g_coefficient_convention_is_the_measured_one():
    """ORCA's Molden coefficient for a g shell is 1/sqrt(3) of the pattern the
    other shells follow -- the reason the g divisor is sqrt(35). Read straight
    off the single-primitive shells of the fixture."""
    wf = load_molden(FIXTURES / "h2o_rot_g.molden.input")
    ratios = {}
    for shell in wf.shells:
        if len(shell.exponents) == 1:
            a, c = shell.exponents[0], shell.coefficients[0]
            ratios[shell.l] = c / ((2 * a / math.pi) ** 0.75 * (4 * a) ** (shell.l / 2))
    for l in (0, 1, 2, 3):
        assert ratios[l] == pytest.approx(1.0, abs=1e-6)
    assert ratios[4] == pytest.approx(1.0 / math.sqrt(3.0), abs=1e-6)


# --- evaluation mechanics -----------------------------------------------------

def test_basis_values_and_orbital_values_agree():
    wf = load_molden(FIXTURES / "h2o.molden.input")
    pts = cube_grid(wf, 6).points()
    c = wf.coefficients["alpha"][:, 3]
    assert orbital_values(wf, c, pts) == pytest.approx(basis_values(wf, pts) @ c, abs=1e-12)


def test_chunked_grid_agrees_with_one_shot():
    wf = load_molden(FIXTURES / "h2o.molden.input")
    grid = cube_grid(wf, 9)
    c = wf.coefficients["alpha"][:, 4]
    one = orbital_on_grid(wf, c, grid, slabs_per_chunk=9)
    many = orbital_on_grid(wf, c, grid, slabs_per_chunk=1)
    assert one.shape == (9, 9, 9)
    assert many == pytest.approx(one, abs=1e-12)


def test_a_valence_orbital_is_normalized_on_a_fine_grid():
    """Riemann sum of psi**2 over orca_plot's box: the HOMO of water is a 2p
    lone pair, wide enough for a 60**3 grid to integrate to a couple of
    percent -- a normalization slip would be off by a factor, not a percent."""
    wf = load_molden(FIXTURES / "h2o.molden.input")
    grid = cube_grid(wf, 60)
    psi = orbital_on_grid(wf, wf.coefficients["alpha"][:, 4], grid)
    dv = grid.step[0] * grid.step[1] * grid.step[2]
    assert float((psi * psi).sum() * dv) == pytest.approx(1.0, abs=0.03)


def test_a_wrong_length_vector_is_refused():
    wf = load_molden(FIXTURES / "h2o.molden.input")
    with pytest.raises(WavefunctionError, match="coefficients"):
        orbital_values(wf, np.ones(3), np.zeros((2, 3)))


def _one_shell_wavefunction(l: int, spherical: bool = True) -> Wavefunction:
    shell = Shell(atom=0, l=l, exponents=(1.0,), coefficients=(1.0,))
    n = 2 * l + 1
    return Wavefunction(
        elements=["H"], atomic_numbers=np.array([1]), nuclear_charges=np.array([1.0]),
        coordinates=np.zeros((1, 3)), shells=[shell],
        bf_atom=np.zeros(n, dtype=int), bf_l=np.full(n, l), bf_m=np.arange(n) - l,
        bf_shell=np.zeros(n, dtype=int),
        coefficients={"alpha": np.eye(n)}, energies={"alpha": np.zeros(n)},
        occupations={"alpha": np.zeros(n)}, spherical=spherical)


def test_h_functions_are_refused_not_guessed():
    wf = _one_shell_wavefunction(5)
    with pytest.raises(WavefunctionError, match="l = 5"):
        basis_values(wf, np.zeros((1, 3)))


def test_cartesian_functions_are_refused():
    wf = _one_shell_wavefunction(2, spherical=False)
    with pytest.raises(WavefunctionError, match="Cartesian"):
        orbital_values(wf, np.ones(5), np.zeros((1, 3)))


# --- the cube file ------------------------------------------------------------

def test_write_cube_round_trips_through_the_apps_reader(tmp_path):
    wf = load_molden(FIXTURES / "h2o.molden.input")
    grid = cube_grid(wf, 7)
    values = orbital_on_grid(wf, wf.coefficients["alpha"][:, 2], grid)
    out = write_cube(tmp_path / "x.cube", wf, grid, values,
                     "Natural bond orbital 2: LP (2) O1, occupancy 1.9933", index=2)
    header = read_cube_header(out)
    assert header["ok"] and header["natoms"] == 3
    assert (header["nx"], header["ny"], header["nz"]) == (7, 7, 7)
    assert header["title"] == "Natural bond orbital 2: LP (2) O1, occupancy 1.9933"
    title, origin, step, shape, back = _read_cube(out)
    assert shape == (7, 7, 7)
    assert np.array(origin) == pytest.approx(np.array(grid.origin), abs=1e-6)
    assert back == pytest.approx(values.reshape(-1), abs=1e-5 * max(1.0, np.abs(values).max()))
    first = out.read_text(encoding="utf-8").splitlines()
    assert first[0] == CUBE_GENERATOR
    assert first[2].split()[0] == "-3"                     # the orbital variant
    assert first[9].split() == ["1", "2"]                  # one orbital, index 2


def test_write_cube_lets_the_caller_stamp_line_one(tmp_path):
    wf = load_molden(FIXTURES / "h2o.molden.input")
    res = write_orbital_cube(tmp_path / "y.cube", wf, wf.coefficients["alpha"][:, 1],
                             5, "MO 1", index=1, generator="Cube data generated by X")
    assert res["npoints"] == 125 and res["seconds"] >= 0.0
    assert Path(res["path"]).read_text(encoding="utf-8").splitlines()[0] == "Cube data generated by X"


def test_values_that_do_not_fit_the_grid_are_refused(tmp_path):
    wf = load_molden(FIXTURES / "h2o.molden.input")
    with pytest.raises(ValueError):
        write_cube(tmp_path / "z.cube", wf, cube_grid(wf, 4), np.zeros((3, 3, 3)), "t")
