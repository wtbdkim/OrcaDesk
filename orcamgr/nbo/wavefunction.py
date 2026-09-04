"""
Read a converged wavefunction and recover the matrices an analysis needs.

Everything downstream of this module -- natural atomic orbitals, natural
population analysis, the NBO search, the second-order donor/acceptor table --
needs exactly four things:

* **S**, the atomic-orbital overlap matrix;
* **P**, the density matrix;
* **F**, the Fock (or Kohn-Sham) matrix; and
* the map from each basis function to its atom and its angular momentum.

The convenient part is that a Molden file already determines all four, even
though it stores none of S, P or F. Writing ``C`` for the molecular-orbital
coefficients, ``e`` for the orbital energies and ``n`` for the occupations, the
defining relations of a self-consistent-field solution are ``C^T S C = I`` and
``F C = S C e``. With a *square* ``C`` (every virtual orbital present, which is
what ``orca_2mkl -molden`` writes) those invert in closed form::

    Cinv = inv(C)
    S = Cinv^T @ Cinv
    F = Cinv^T @ diag(e) @ Cinv
    P = C @ diag(n) @ C^T

So one matrix inversion yields both S and F, and no integrals are ever
evaluated -- which is why this package needs numpy and nothing else. No
integral library (libcint, and therefore PySCF) has to be bundled into the
frozen Windows build.

The practical consequence is bigger than the code saving: because the input is
the ``.gbw`` that every finished run already leaves behind, analysis is
*retroactive*. Any calculation ORCAdesk has ever run can be analysed without
re-running it. The FILE.47 that ORCA's own NBO interface writes carries S, P
and F explicitly and is a fine input too, but it only exists for jobs that were
run with ``! NBO`` in the first place, so Molden is the primary path.

Two limits are worth stating plainly:

* ``C`` must be square. ORCA drops near-linearly-dependent basis functions in
  very diffuse basis sets, and the remaining orbitals then cannot span the
  basis. :func:`load_molden` refuses that case rather than returning a wrong S.
* Post-Hartree-Fock densities (MP2, CCSD) are not SCF eigenvectors, so the
  ``F`` recovered this way is the underlying SCF matrix, not a correlated
  analogue. S and P remain exact.

Pure and Qt-free so it stays unit-testable; the Bridge slots serialize what the
layers above return.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


class WavefunctionError(Exception):
    """A wavefunction file could not be turned into usable matrices. The message
    is one actionable sentence, ready to show the user (P28)."""


#: Molden shell letters, in angular-momentum order.
_L_OF = {"s": 0, "p": 1, "d": 2, "f": 3, "g": 4, "h": 5}

BOHR_PER_ANGSTROM = 1.0 / 0.52917720859


def _spherical_m(l: int) -> list[int]:
    """The magnetic quantum numbers of an ``l`` shell, in Molden's order.

    Molden orders spherical components ``0, +1, -1, +2, -2, ...`` -- except for
    ``p``, which it writes in Cartesian order ``px, py, pz`` even in a spherical
    file. Those correspond to ``m = +1, -1, 0``.
    """
    if l == 1:
        return [1, -1, 0]
    out = [0]
    for m in range(1, l + 1):
        out += [m, -m]
    return out


def _cartesian_count(l: int) -> int:
    return (l + 1) * (l + 2) // 2


@dataclass(frozen=True)
class Shell:
    """One contracted basis shell on one atom. The exponents/coefficients are
    kept for completeness -- nothing here evaluates integrals, but a future
    analysis (or a bug report) may want to see the basis it was handed."""

    atom: int
    l: int
    exponents: tuple[float, ...]
    coefficients: tuple[float, ...]


@dataclass
class Wavefunction:
    """A converged wavefunction, plus the S/P/F matrices it implies.

    ``coefficients`` / ``energies`` / ``occupations`` are keyed by spin: a
    restricted calculation has only ``"alpha"`` (whose occupations are 2), an
    unrestricted one has both ``"alpha"`` and ``"beta"``.
    """

    elements: list[str]
    atomic_numbers: np.ndarray            # (natoms,) int -- true Z, element identity
    nuclear_charges: np.ndarray           # (natoms,) float -- Z less any ECP core
    coordinates: np.ndarray               # (natoms, 3) in Bohr
    shells: list[Shell]
    bf_atom: np.ndarray                   # (nbf,) int -- owning atom
    bf_l: np.ndarray                      # (nbf,) int -- angular momentum
    bf_m: np.ndarray                      # (nbf,) int -- Molden-order m (see note)
    bf_shell: np.ndarray                  # (nbf,) int -- index into `shells`
    coefficients: dict[str, np.ndarray]   # spin -> (nbf, nmo)
    energies: dict[str, np.ndarray]       # spin -> (nmo,) in Hartree
    occupations: dict[str, np.ndarray]    # spin -> (nmo,)
    spherical: bool                       # False => Cartesian d/f, and bf_m is
                                          # a component index, not a real m
    source: str = ""
    _cache: dict = field(default_factory=dict, repr=False, compare=False)

    # ---- shape -------------------------------------------------------------

    @property
    def n_atoms(self) -> int:
        return len(self.elements)

    @property
    def n_basis(self) -> int:
        return int(self.bf_atom.size)

    @property
    def restricted(self) -> bool:
        return "beta" not in self.coefficients

    @property
    def spins(self) -> tuple[str, ...]:
        return ("alpha",) if self.restricted else ("alpha", "beta")

    @property
    def n_electrons(self) -> float:
        return float(sum(o.sum() for o in self.occupations.values()))

    @property
    def charge(self) -> float:
        """Total molecular charge implied by the nuclei and the electron count."""
        return float(self.nuclear_charges.sum() - self.n_electrons)

    @property
    def has_ecp(self) -> bool:
        return bool(np.any(self.nuclear_charges != self.atomic_numbers))

    # ---- the three matrices ------------------------------------------------

    def _inverse_coefficients(self, spin: str) -> np.ndarray:
        key = ("cinv", spin)
        if key not in self._cache:
            c = self.coefficients[spin]
            if c.shape[0] != c.shape[1]:
                raise WavefunctionError(
                    f"this wavefunction has {c.shape[1]} molecular orbitals for "
                    f"{c.shape[0]} basis functions, so the overlap matrix cannot "
                    "be recovered from it -- ORCA removed near-linearly-dependent "
                    "basis functions. Re-run with a less diffuse basis set to "
                    "analyse it.")
            try:
                self._cache[key] = np.linalg.inv(c)
            except np.linalg.LinAlgError as e:   # singular C: not a valid SCF solution
                raise WavefunctionError(
                    "the molecular-orbital coefficients in this file are singular, "
                    f"so the overlap matrix cannot be recovered from them ({e}).") from e
        return self._cache[key]

    def overlap(self) -> np.ndarray:
        """The AO overlap matrix ``S``, from ``C^T S C = I``.

        Derived from the alpha orbitals; in an unrestricted calculation the beta
        set spans the same basis and so implies the same S, which
        :meth:`consistency` checks rather than assumes.
        """
        if "S" not in self._cache:
            cinv = self._inverse_coefficients("alpha")
            self._cache["S"] = _symmetrize(cinv.T @ cinv)
        return self._cache["S"]

    def fock(self, spin: str = "alpha") -> np.ndarray:
        """The Fock (Kohn-Sham) matrix ``F`` in the AO basis, from ``F C = S C e``.

        Recovered as ``Cinv^T diag(e) Cinv`` -- the same inverse the overlap
        uses, weighted by the orbital energies instead of by unity.
        """
        spin = self._resolve_spin(spin)
        key = ("F", spin)
        if key not in self._cache:
            cinv = self._inverse_coefficients(spin)
            self._cache[key] = _symmetrize((cinv.T * self.energies[spin]) @ cinv)
        return self._cache[key]

    def _raw_density(self, spin: str) -> np.ndarray:
        """``C n C^T`` for one stored orbital set, exactly as the file gives it."""
        c = self.coefficients[spin]
        return _symmetrize((c * self.occupations[spin]) @ c.T)

    def density(self, spin: str = "total") -> np.ndarray:
        """The AO density matrix.

        ``"total"`` is the full electron density (in a restricted calculation
        the occupations already carry the factor of 2, so its single orbital set
        *is* the total); ``"spin"`` is ``P(alpha) - P(beta)``, zero by
        construction when restricted.
        """
        key = ("P", spin)
        if key in self._cache:
            return self._cache[key]
        if spin == "total":
            p = sum(self._raw_density(s) for s in self.spins)
        elif spin == "spin":
            p = (self._raw_density("alpha") - self._raw_density("beta")
                 if not self.restricted
                 else np.zeros((self.n_basis, self.n_basis)))
        elif spin in ("alpha", "beta"):
            # Restricted: one orbital set counting both spins, so each half is
            # P/2. Unrestricted: the stored set already is that half.
            p = (0.5 * self._raw_density("alpha") if self.restricted
                 else self._raw_density(spin))
        else:
            raise WavefunctionError(
                f"unknown density {spin!r} -- expected total, alpha, beta or spin.")
        self._cache[key] = p
        return p

    def _resolve_spin(self, spin: str) -> str:
        if spin in self.coefficients:
            return spin
        if spin == "beta" and self.restricted:
            return "alpha"
        raise WavefunctionError(f"this wavefunction has no {spin!r} orbitals.")

    # ---- self-check --------------------------------------------------------

    def consistency(self) -> dict:
        """Residuals a caller (or a test) can assert on.

        Every entry must come out ~0, or equal to a known electron count, if the
        parse and the reconstruction are both right -- so one call catches a
        basis-ordering mistake, a misread occupation column, and a numerically
        hopeless inversion alike.
        """
        s = self.overlap()
        out: dict[str, float] = {}
        for spin in self.spins:
            c = self.coefficients[spin]
            out[f"orthonormality_{spin}"] = float(
                np.abs(c.T @ s @ c - np.eye(self.n_basis)).max())
            # F must reproduce the orbital energies it was built from.
            resid = self.fock(spin) @ c - (s @ c) * self.energies[spin]
            out[f"eigenvalue_residual_{spin}"] = float(np.abs(resid).max())
        if not self.restricted:
            beta_inv = self._inverse_coefficients("beta")
            out["overlap_spin_agreement"] = float(
                np.abs(_symmetrize(beta_inv.T @ beta_inv) - s).max())
        out["electron_count"] = float(np.einsum(
            "ij,ji->", self.density("total"), s))
        out["electron_count_error"] = abs(out["electron_count"] - self.n_electrons)
        return out

    # ---- reference populations --------------------------------------------
    #
    # Mulliken and Loewdin are not the point of this package -- NPA exists
    # precisely because they misbehave in large basis sets. They are here
    # because ORCA prints both in every .out, which makes them the one thing
    # this layer can be checked against *exactly*: if S, P and the
    # basis-function-to-atom map are all right, these must reproduce ORCA's own
    # numbers to its print precision. They are the ground truth the rest of the
    # stack is built on.

    def _atom_sum(self, diagonal: np.ndarray) -> np.ndarray:
        out = np.zeros(self.n_atoms)
        np.add.at(out, self.bf_atom, diagonal)
        return out

    def gross_populations(self, kind: str = "mulliken",
                          spin: str = "total") -> np.ndarray:
        """Per-atom electron populations. ``kind`` is ``"mulliken"`` (the diagonal
        of ``PS``) or ``"loewdin"`` (the diagonal of ``S^1/2 P S^1/2``)."""
        p, s = self.density(spin), self.overlap()
        if kind == "mulliken":
            d = np.einsum("ij,ji->i", p, s)
        elif kind == "loewdin":
            s_half = _matrix_power(s, 0.5)
            d = np.einsum("ij,jk,ki->i", s_half, p, s_half)
        else:
            raise WavefunctionError(f"unknown population analysis {kind!r}.")
        return self._atom_sum(d)

    def atomic_charges(self, kind: str = "mulliken") -> np.ndarray:
        """Per-atom partial charges: nuclear charge less gross population. Uses
        :attr:`nuclear_charges`, so ECP atoms come out right."""
        return self.nuclear_charges - self.gross_populations(kind, "total")

    def spin_populations(self) -> np.ndarray:
        """Per-atom Mulliken spin densities; all zero for a restricted calculation."""
        return self._atom_sum(
            np.einsum("ij,ji->i", self.density("spin"), self.overlap()))


# ---- Molden reader ---------------------------------------------------------


def _sections(text: str) -> dict[str, list[str]]:
    """Split a Molden file into ``{lowercased section name: [lines]}``.

    The header line is kept as element 0 of each list because some sections
    carry an argument on it (``[Atoms] AU``). Repeated sections are appended to,
    and unknown ones are collected rather than skipped, so an unrecognized
    producer never silently loses data.
    """
    out: dict[str, list[str]] = {}
    current = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("["):
            current = stripped.split("]")[0].lstrip("[").strip().lower()
            out.setdefault(current, []).append(stripped)
            continue
        if current:
            out[current].append(raw)
    return out


def _float(token: str) -> float:
    """Parse a Fortran-flavoured float. Molden writers differ on the exponent
    letter: ORCA emits ``1.0E+02`` but Gaussian-derived files use ``1.0D+02``."""
    return float(token.replace("D", "E").replace("d", "e"))


def _parse_atoms(lines: list[str]) -> tuple[list[str], np.ndarray, np.ndarray]:
    """``[Atoms]`` -> elements, atomic numbers, coordinates in Bohr."""
    header = lines[0].lower() if lines else ""
    # "Angs"/"AU" ride on the header line; Bohr is the internal unit everywhere.
    scale = BOHR_PER_ANGSTROM if ("angs" in header or "ang]" in header) else 1.0
    elements: list[str] = []
    numbers: list[int] = []
    coords: list[list[float]] = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        elements.append(parts[0])
        numbers.append(int(parts[2]))
        coords.append([_float(p) * scale for p in parts[3:6]])
    if not elements:
        raise WavefunctionError("this Molden file lists no atoms.")
    return elements, np.array(numbers, dtype=int), np.array(coords, dtype=float)


def _parse_pseudo(lines: list[str], nuclear: np.ndarray) -> np.ndarray:
    """Apply ``[Pseudo]`` effective core potentials to the nuclear charges.

    ``[Atoms]`` reports the true atomic number even when an ECP replaced the
    core electrons, so an ECP atom's density accounts for fewer electrons than
    its Z. ``[Pseudo]`` names the effective charge that is actually screened
    (iodine in a def2 basis: Z = 53, effective 25). Without this correction
    every charge on such an atom would be wrong by the size of its core.
    """
    charges = nuclear.astype(float).copy()
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            index, effective = int(parts[1]) - 1, _float(parts[2])
        except ValueError:
            continue
        if 0 <= index < charges.size:
            charges[index] = effective
    return charges


def _parse_gto(lines: list[str], n_atoms: int) -> list[Shell]:
    """``[GTO]`` -> the shell list, in the order the basis functions follow.

    An atom block opens with ``<atom index> 0``; each shell then gives
    ``<letter> <n primitives> <scale>`` followed by that many exponent/
    coefficient pairs. Pople-style ``sp`` shells expand into an s and a p shell
    sharing exponents, which is how their basis functions are ordered anyway.
    """
    shells: list[Shell] = []
    atom = -1
    pending: list[tuple[str, int]] = []      # (letter, n primitives) still to read
    exps: list[float] = []
    coefs: list[list[float]] = []
    need = 0

    def flush() -> None:
        if not pending:
            return
        letters = pending[0][0]
        for column, letter in enumerate(letters):
            shells.append(Shell(atom=atom, l=_L_OF[letter],
                                exponents=tuple(exps),
                                coefficients=tuple(c[column] for c in coefs)))
        pending.clear()

    for line in lines[1:]:
        parts = line.split()
        if not parts:
            continue
        if len(parts) == 2 and parts[1] == "0" and parts[0].isdigit():
            flush()
            atom = int(parts[0]) - 1
            if not 0 <= atom < n_atoms:
                raise WavefunctionError(
                    f"this Molden file assigns a basis set to atom {atom + 1}, "
                    f"but only {n_atoms} atoms are listed.")
            continue
        letter = parts[0].lower()
        if len(parts) >= 2 and all(c in _L_OF for c in letter) and parts[1].isdigit():
            flush()
            need = int(parts[1])
            pending.append((letter, need))
            exps, coefs = [], []
            continue
        if pending and need > 0:
            values = [_float(p) for p in parts]
            exps.append(values[0])
            coefs.append(values[1:])
            need -= 1
            if need == 0:
                flush()
    flush()
    if not shells:
        raise WavefunctionError("this Molden file contains no basis set.")
    return shells


def _expand_basis(shells: list[Shell], spherical_by_l: dict[int, bool]):
    """Shells -> the per-basis-function atom / l / m / shell arrays.

    Within a shell the components follow Molden's own order, which is what the
    ``[MO]`` coefficient rows are indexed by. For a Cartesian shell there is no
    meaningful ``m``, so the component index is stored instead -- flagged by
    :attr:`Wavefunction.spherical` so no caller mistakes it for a real one.
    """
    bf_atom: list[int] = []
    bf_l: list[int] = []
    bf_m: list[int] = []
    bf_shell: list[int] = []
    for index, shell in enumerate(shells):
        if spherical_by_l.get(shell.l, True):
            ms = _spherical_m(shell.l)
        else:
            ms = list(range(_cartesian_count(shell.l)))
        bf_atom.extend([shell.atom] * len(ms))
        bf_l.extend([shell.l] * len(ms))
        bf_m.extend(ms)
        bf_shell.extend([index] * len(ms))
    return (np.array(bf_atom, dtype=int), np.array(bf_l, dtype=int),
            np.array(bf_m, dtype=int), np.array(bf_shell, dtype=int))


def _parse_mo(lines: list[str], n_basis: int):
    """``[MO]`` -> coefficients, energies and occupations, keyed by spin.

    Blocks are delimited by their key lines (``Sym=``/``Ene=``/``Spin=``/
    ``Occup=``) rather than by ``Sym=`` alone, since not every producer writes a
    symmetry label; a key line that follows coefficient rows opens the next
    orbital. Keys are matched case-insensitively and without assuming a space
    after the ``=`` -- ORCA itself writes ``Spin= Alpha`` but ``Spin=Beta``.
    """
    coefficients: dict[str, list[np.ndarray]] = {}
    energies: dict[str, list[float]] = {}
    occupations: dict[str, list[float]] = {}
    spin, energy, occupation = "alpha", 0.0, 0.0
    column = np.zeros(n_basis)
    in_body = False

    def flush() -> None:
        if not in_body:
            return
        coefficients.setdefault(spin, []).append(column)
        energies.setdefault(spin, []).append(energy)
        occupations.setdefault(spin, []).append(occupation)

    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if "=" in stripped and not stripped[0].isdigit() and not stripped[0] == "-":
            key, _, value = stripped.partition("=")
            key, value = key.strip().lower(), value.strip()
            if in_body:
                flush()
                column, in_body = np.zeros(n_basis), False
            if key == "ene":
                energy = _float(value)
            elif key == "occup":
                occupation = _float(value)
            elif key == "spin":
                spin = "beta" if value.lower().startswith("b") else "alpha"
            continue
        parts = stripped.split()
        index = int(parts[0]) - 1 if len(parts) >= 2 else int(in_body)
        value = _float(parts[-1])
        if not 0 <= index < n_basis:
            raise WavefunctionError(
                f"this Molden file indexes basis function {index + 1} in an "
                f"orbital, but the basis has only {n_basis} functions.")
        column[index] = value
        in_body = True
    flush()

    if not coefficients:
        raise WavefunctionError("this Molden file contains no molecular orbitals.")
    return ({s: np.array(v).T for s, v in coefficients.items()},
            {s: np.array(v) for s, v in energies.items()},
            {s: np.array(v) for s, v in occupations.items()})


def load_molden(path: str | Path) -> Wavefunction:
    """Read a Molden file (``orca_2mkl <base> -molden``) into a
    :class:`Wavefunction`.

    Raises :class:`WavefunctionError`, with a message fit to show the user, for
    every way the file can be unusable rather than merely unfamiliar.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise WavefunctionError(f"could not read {path.name}: {e}") from e
    if "[molden format]" not in text[:4096].lower():
        raise WavefunctionError(
            f"{path.name} does not look like a Molden file (no [Molden Format] "
            "header). Generate one with: orca_2mkl <basename> -molden")

    sections = _sections(text)
    if "atoms" not in sections or "gto" not in sections or "mo" not in sections:
        missing = [s for s in ("atoms", "gto", "mo") if s not in sections]
        raise WavefunctionError(
            f"{path.name} is missing the required Molden section(s) "
            + ", ".join(f"[{s.upper()}]" for s in missing) + ".")

    elements, numbers, coordinates = _parse_atoms(sections["atoms"])
    nuclear = _parse_pseudo(sections.get("pseudo", []), numbers)
    shells = _parse_gto(sections["gto"], len(elements))

    # d/f/g are spherical only when the file says so; absent flags mean
    # Cartesian, which is the Molden default.
    flags = set(sections)
    spherical_by_l = {
        2: bool(flags & {"5d", "5d7f", "5d10f"}),
        3: bool(flags & {"7f", "5d7f"}),
        4: "9g" in flags,
    }
    bf_atom, bf_l, bf_m, bf_shell = _expand_basis(shells, spherical_by_l)
    coefficients, energies, occupations = _parse_mo(sections["mo"], bf_atom.size)

    for spin, matrix in coefficients.items():
        if matrix.shape[0] != bf_atom.size:
            raise WavefunctionError(
                f"{path.name} declares {bf_atom.size} basis functions but its "
                f"{spin} orbitals have {matrix.shape[0]} coefficients -- the "
                "basis set and the orbitals disagree.")

    present = {l for l in bf_l.tolist() if l >= 2}
    return Wavefunction(
        elements=elements, atomic_numbers=numbers, nuclear_charges=nuclear,
        coordinates=coordinates, shells=shells,
        bf_atom=bf_atom, bf_l=bf_l, bf_m=bf_m, bf_shell=bf_shell,
        coefficients=coefficients, energies=energies, occupations=occupations,
        spherical=all(spherical_by_l.get(l, True) for l in present),
        source=str(path))


def _symmetrize(a: np.ndarray) -> np.ndarray:
    """Average out the asymmetry rounding leaves behind. S, P and F are symmetric
    by construction, and the eigensolvers downstream (``eigh``) read only one
    triangle -- so an unsymmetrized input silently picks a half instead of
    failing."""
    return 0.5 * (a + a.T)


def _matrix_power(a: np.ndarray, power: float) -> np.ndarray:
    """``a ** power`` for a symmetric positive-definite matrix, via its
    eigendecomposition (numpy only -- scipy's ``fractional_matrix_power`` would
    pull a 200 MB dependency into the frozen build for this one line)."""
    w, v = np.linalg.eigh(a)
    if w.min() <= 0:
        raise WavefunctionError(
            "the overlap matrix is not positive definite, so this wavefunction "
            "cannot be orthogonalized -- the basis set is linearly dependent.")
    return _symmetrize((v * w ** power) @ v.T)
