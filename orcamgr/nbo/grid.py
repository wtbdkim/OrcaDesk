"""
Evaluate an orbital on a grid and write it as a Gaussian cube file.

``orca_plot`` draws canonical molecular orbitals and densities, and nothing
else: it has no way to take a coefficient vector of ours. A natural bond
orbital is exactly that -- a vector over the basis functions the Molden file
already lists, with their exponents and contraction coefficients -- so drawing
one means evaluating the basis on a grid ourselves. This module does that, and
only that: no integrals, no analysis, numpy and the wavefunction alone.

Two conventions here are measured rather than assumed, because the picture is
wrong in a way nobody would notice if they were guessed (P3):

* **Normalization.** ORCA's Molden writer folds the primitive normalization
  into each contraction coefficient -- ``(2a/pi)^(3/4) (4a)^(l/2)``, the
  constant of the all-distinct Cartesian component (``x``, ``xy``, ``xyz``).
  The Racah-normalized real solid harmonic ``C_lm`` (``C_l0 = r^l`` on the
  axis) must then be divided by ``sqrt((2l-1)!!)`` for the function to be
  normalized. For ``g`` ORCA writes the coefficient ``1/sqrt(3)`` smaller than
  that pattern (measured on every single-primitive shell of def2-QZVP: the
  ratio is exactly 1 for s-f and ``0.57735`` for g), so the divisor here is
  ``sqrt(35)`` -- ``sqrt(105)/sqrt(3)`` -- and the function it draws is the
  same normalized one as for every other ``l``.
* **Phase.** ORCA writes ``f(+-3)``, ``g(+-3)`` and ``g(+-4)`` with the
  opposite sign from the standard real harmonics. The same fit found each of
  those components at exactly ``-1`` (``-sqrt(3)`` for g) and every other at
  ``+1``. Both tables were pinned by fitting orca_plot's MO cubes of a water
  molecule rotated off every symmetry axis (so every ``m`` contributes) back
  onto this evaluator: residual ``1e-6`` against values printed to five
  digits, i.e. the model is complete. :mod:`tests.test_nbo_grid` keeps that
  comparison as a fixture.

None of this touches the analysis. The NPA/NBO layers work from ``S``, ``P``
and ``F`` recovered in closed form and never evaluate a function, so a wrong
phase here would draw a wrong lobe and change no number.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .wavefunction import Wavefunction, WavefunctionError, _spherical_m

#: Highest angular momentum this evaluator draws. def2-QZVP tops out at g for
#: main-group atoms; an h shell is refused rather than guessed (P2).
MAX_L = 4

#: Divisor applied to the Racah solid harmonic per l -- see the module note.
_DIVISOR = {0: 1.0, 1: 1.0, 2: math.sqrt(3.0), 3: math.sqrt(15.0), 4: math.sqrt(35.0)}

#: Components ORCA's Molden writer emits with the opposite sign from the
#: standard real solid harmonics. Measured on ORCA 6.1.1 (orca_2mkl / orca_plot).
_ORCA_SIGN = {(3, 3): -1.0, (3, -3): -1.0,
              (4, 3): -1.0, (4, -3): -1.0, (4, 4): -1.0, (4, -4): -1.0}

#: Primitives contribute nothing past this many e-foldings; a shell whose
#: nearest grid point is farther than that is skipped for the whole chunk.
_EXP_CUTOFF = 30.0

#: Margin around the molecule's bounding box, in Bohr. orca_plot's own: its
#: cube origin sits exactly 7 Bohr below the lowest atom on every axis, so a
#: cube written here lands in the same box as an orca_plot one of the same
#: molecule and the viewer's camera does not jump between them.
BOX_MARGIN_BOHR = 7.0


# ---------------------------------------------------------------------------
# real solid harmonics
# ---------------------------------------------------------------------------

def real_solid_harmonics(l: int, x: np.ndarray, y: np.ndarray, z: np.ndarray) -> dict:
    """The Racah-normalized real solid harmonics ``C_lm`` of one ``l``, keyed
    by ``m`` (``+m`` is the cosine-like component, ``-m`` the sine-like one).

    ``C_l0`` reduces to ``r^l`` on the z axis, and every component integrates
    to ``4 pi / (2l+1)`` over the unit sphere -- what :mod:`tests.test_nbo_grid`
    checks the table against, since a typo in a coefficient here draws a
    plausible wrong lobe.
    """
    if l == 0:
        return {0: np.ones_like(x)}
    if l == 1:
        return {1: x, -1: y, 0: z}
    r2 = x * x + y * y + z * z
    if l == 2:
        s3 = math.sqrt(3.0)
        return {0: (3.0 * z * z - r2) / 2.0,
                1: s3 * x * z, -1: s3 * y * z,
                2: s3 / 2.0 * (x * x - y * y), -2: s3 * x * y}
    if l == 3:
        return {0: z * (5.0 * z * z - 3.0 * r2) / 2.0,
                1: math.sqrt(3.0 / 8.0) * x * (5.0 * z * z - r2),
                -1: math.sqrt(3.0 / 8.0) * y * (5.0 * z * z - r2),
                2: math.sqrt(15.0) / 2.0 * z * (x * x - y * y),
                -2: math.sqrt(15.0) * x * y * z,
                3: math.sqrt(5.0 / 8.0) * x * (x * x - 3.0 * y * y),
                -3: math.sqrt(5.0 / 8.0) * y * (3.0 * x * x - y * y)}
    if l == 4:
        zz = z * z
        return {0: (35.0 * zz * zz - 30.0 * zz * r2 + 3.0 * r2 * r2) / 8.0,
                1: math.sqrt(10.0) / 4.0 * x * z * (7.0 * zz - 3.0 * r2),
                -1: math.sqrt(10.0) / 4.0 * y * z * (7.0 * zz - 3.0 * r2),
                2: math.sqrt(5.0) / 4.0 * (x * x - y * y) * (7.0 * zz - r2),
                -2: math.sqrt(5.0) / 2.0 * x * y * (7.0 * zz - r2),
                3: math.sqrt(70.0) / 4.0 * x * z * (x * x - 3.0 * y * y),
                -3: math.sqrt(70.0) / 4.0 * y * z * (3.0 * x * x - y * y),
                4: math.sqrt(35.0) / 8.0 * (x ** 4 - 6.0 * x * x * y * y + y ** 4),
                -4: math.sqrt(35.0) / 2.0 * x * y * (x * x - y * y)}
    raise WavefunctionError(
        f"basis functions with l = {l} cannot be drawn (up to g is supported).")


def _check_drawable(wf: Wavefunction) -> None:
    if not wf.spherical:
        raise WavefunctionError(
            "this wavefunction uses Cartesian d/f functions, which the orbital "
            "grid does not evaluate; ORCA's own Molden files are spherical.")
    top = int(wf.bf_l.max()) if wf.n_basis else 0
    if top > MAX_L:
        raise WavefunctionError(
            f"this basis set has l = {top} functions, which cannot be drawn "
            f"(up to g is supported).")


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------

def _shell_columns(wf: Wavefunction) -> list:
    """``(shell, first column)`` for every shell, in basis-function order."""
    out = []
    col = 0
    for index, shell in enumerate(wf.shells):
        out.append((shell, col))
        col += len(_spherical_m(shell.l))
    if col != wf.n_basis:
        raise WavefunctionError(
            f"the shell list spans {col} basis functions but the orbitals have "
            f"{wf.n_basis} coefficients.")
    return out


def basis_values(wf: Wavefunction, points: np.ndarray) -> np.ndarray:
    """Every basis function at every point: ``(npoints, nbf)``.

    The whole matrix, so meant for tests and small point sets -- a 60**3 grid
    against a few hundred functions is hundreds of megabytes. Production goes
    through :func:`orbital_values`, which never forms it.
    """
    _check_drawable(wf)
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    out = np.zeros((points.shape[0], wf.n_basis))
    for shell, col in _shell_columns(wf):
        d = points - wf.coordinates[shell.atom]
        r2 = (d * d).sum(axis=1)
        radial = np.zeros(points.shape[0])
        for a, c in zip(shell.exponents, shell.coefficients):
            radial += c * np.exp(-a * r2)
        angular = real_solid_harmonics(shell.l, d[:, 0], d[:, 1], d[:, 2])
        scale = 1.0 / _DIVISOR[shell.l]
        for k, m in enumerate(_spherical_m(shell.l)):
            out[:, col + k] = (_ORCA_SIGN.get((shell.l, m), 1.0) * scale
                               * radial * angular[m])
    return out


def orbital_values(wf: Wavefunction, coefficients: np.ndarray,
                   points: np.ndarray) -> np.ndarray:
    """One orbital -- ``sum_mu c_mu phi_mu`` -- at every point, ``(npoints,)``.

    Accumulates shell by shell so the basis matrix is never formed, and skips a
    shell for the whole point set when its slowest primitive has decayed to
    nothing by the nearest point -- what keeps a large molecule's grid at
    seconds rather than minutes.
    """
    _check_drawable(wf)
    coefficients = np.asarray(coefficients, dtype=float).reshape(-1)
    if coefficients.size != wf.n_basis:
        raise WavefunctionError(
            f"an orbital with {coefficients.size} coefficients cannot be drawn "
            f"in a basis of {wf.n_basis} functions.")
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    psi = np.zeros(points.shape[0])
    for shell, col in _shell_columns(wf):
        n = len(_spherical_m(shell.l))
        c = coefficients[col:col + n]
        if not np.any(c):
            continue
        d = points - wf.coordinates[shell.atom]
        r2 = (d * d).sum(axis=1)
        if r2.min() * min(shell.exponents) > _EXP_CUTOFF:
            continue
        radial = np.zeros(points.shape[0])
        for a, ck in zip(shell.exponents, shell.coefficients):
            radial += ck * np.exp(-a * r2)
        angular = real_solid_harmonics(shell.l, d[:, 0], d[:, 1], d[:, 2])
        scale = 1.0 / _DIVISOR[shell.l]
        for k, m in enumerate(_spherical_m(shell.l)):
            if c[k]:
                psi += (c[k] * _ORCA_SIGN.get((shell.l, m), 1.0) * scale) * radial * angular[m]
    return psi


# ---------------------------------------------------------------------------
# the cube grid
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CubeGrid:
    """A rectilinear grid in Bohr, in cube-file order (x slowest, z fastest)."""
    origin: tuple            # (3,)
    step: tuple              # (3,) spacing along each axis
    shape: tuple             # (nx, ny, nz)

    @property
    def n_points(self) -> int:
        return int(self.shape[0] * self.shape[1] * self.shape[2])

    def slab(self, i: int) -> np.ndarray:
        """The ``(ny * nz, 3)`` points of x-index ``i``, in cube order."""
        nx, ny, nz = self.shape
        if not 0 <= i < nx:
            raise IndexError(i)
        jy, kz = np.meshgrid(np.arange(ny), np.arange(nz), indexing="ij")
        pts = np.empty((ny * nz, 3))
        pts[:, 0] = self.origin[0] + i * self.step[0]
        pts[:, 1] = self.origin[1] + jy.reshape(-1) * self.step[1]
        pts[:, 2] = self.origin[2] + kz.reshape(-1) * self.step[2]
        return pts

    def points(self) -> np.ndarray:
        """Every point, ``(n_points, 3)``, in cube order. Tests and small grids."""
        return np.vstack([self.slab(i) for i in range(self.shape[0])])


def cube_grid(wf: Wavefunction, n: int = 60,
              margin: float = BOX_MARGIN_BOHR) -> CubeGrid:
    """``n`` points per axis over the molecule's bounding box plus ``margin``
    on every side -- orca_plot's own box, so the two kinds of cube coincide."""
    n = max(2, int(n))
    lo = wf.coordinates.min(axis=0) - margin
    hi = wf.coordinates.max(axis=0) + margin
    step = (hi - lo) / (n - 1)
    return CubeGrid(origin=tuple(float(v) for v in lo),
                    step=tuple(float(v) for v in step), shape=(n, n, n))


def orbital_on_grid(wf: Wavefunction, coefficients: np.ndarray,
                    grid: CubeGrid, slabs_per_chunk: int = 0) -> np.ndarray:
    """The orbital over a whole grid, ``(nx, ny, nz)``, evaluated a few x-slabs
    at a time so memory stays at a few tens of megabytes regardless of size."""
    nx, ny, nz = grid.shape
    if slabs_per_chunk <= 0:
        slabs_per_chunk = max(1, 20000 // max(1, ny * nz))
    out = np.empty((nx, ny, nz))
    for start in range(0, nx, slabs_per_chunk):
        stop = min(nx, start + slabs_per_chunk)
        pts = np.vstack([grid.slab(i) for i in range(start, stop)])
        out[start:stop] = orbital_values(wf, coefficients, pts).reshape(stop - start, ny, nz)
    return out


# ---------------------------------------------------------------------------
# the cube file
# ---------------------------------------------------------------------------

#: First line of every cube this module writes. The reader checks it before
#: reusing a file, so a cube from an older algorithm is regenerated rather than
#: shown under a new label.
CUBE_GENERATOR = "Cube data generated by ORCAdesk"


def write_cube(path: str | Path, wf: Wavefunction, grid: CubeGrid,
               values: np.ndarray, title: str, index: int = 1,
               generator: str = CUBE_GENERATOR) -> Path:
    """Write ``values`` (``(nx, ny, nz)``) as the *orbital* variant of the cube
    format ORCA itself writes -- a negative atom count and one line naming the
    orbital -- so :mod:`orcamgr.cube` and 3Dmol read it exactly as they read
    an orca_plot cube. ``title`` becomes line 2, the caption the viewer shows.
    """
    path = Path(path)
    values = np.asarray(values, dtype=float)
    if values.shape != tuple(grid.shape):
        raise ValueError(f"values {values.shape} do not fit the grid {grid.shape}")
    lines = [generator, " ".join(str(title).split()) or "Orbital",
             f"{-wf.n_atoms:5d}{grid.origin[0]:12.6f}{grid.origin[1]:12.6f}{grid.origin[2]:12.6f}"]
    for axis in range(3):
        step = [0.0, 0.0, 0.0]
        step[axis] = grid.step[axis]
        lines.append(f"{grid.shape[axis]:5d}{step[0]:12.6f}{step[1]:12.6f}{step[2]:12.6f}")
    for i in range(wf.n_atoms):
        x, y, z = wf.coordinates[i]
        lines.append(f"{int(wf.atomic_numbers[i]):5d}{float(wf.nuclear_charges[i]):12.6f}"
                     f"{x:12.6f}{y:12.6f}{z:12.6f}")
    lines.append(f"{1:5d}{int(index):5d}")
    flat = values.reshape(-1)
    rows = flat.size // 6
    body = []
    if rows:
        body.append("\n".join(
            "".join(f"{v:14.5E}" for v in row) for row in flat[:rows * 6].reshape(rows, 6)))
    tail = flat[rows * 6:]
    if tail.size:
        body.append("".join(f"{v:14.5E}" for v in tail))
    text = "\n".join(lines) + "\n" + "\n".join(body) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_orbital_cube(path: str | Path, wf: Wavefunction, coefficients: np.ndarray,
                       n: int, title: str, index: int = 1,
                       generator: str = CUBE_GENERATOR) -> dict:
    """Evaluate one orbital on an ``n``**3 grid and write it. Returns
    ``{path, seconds, npoints}``; errors are :class:`WavefunctionError`."""
    started = time.time()
    grid = cube_grid(wf, n)
    values = orbital_on_grid(wf, coefficients, grid)
    out = write_cube(path, wf, grid, values, title, index, generator)
    return {"path": str(out), "seconds": time.time() - started, "npoints": grid.n_points}
