"""
Natural atomic orbitals and natural population analysis.

NPA exists because Mulliken and Loewdin populations are not trustworthy in a
large basis set. Mulliken splits shared density down the middle regardless of
which atom the function is centred on, so a diffuse function on one atom charges
its neighbour; Loewdin's symmetric orthogonalization spreads it differently but
no better. Both drift with the basis and can return values no density can
justify. Measured on one water molecule across five basis sets, with the same
geometry and functional:

    NPA       spread 0.06 e   (-0.887 .. -0.948 on oxygen)
    Mulliken  spread 0.34 e
    Loewdin   spread 1.03 e   -- and at def2-QZVP it reports oxygen at +0.73,
                                 a positive oxygen in water

The natural atomic orbitals fix this by asking a different question. Rather than
dividing basis functions between atoms, they build the orbitals each atom
*actually* has in this molecule -- eigenfunctions of the density restricted to
that atom -- and then orthogonalize them in a way that leaves the occupied ones
almost untouched and makes the empty Rydberg shells absorb the distortion. A
diffuse function then cannot carry population it does not hold, which is exactly
the failure mode above.

The procedure (Reed, Weinstock & Weinhold, *J. Chem. Phys.* **83**, 735 (1985);
Reed, Curtiss & Weinhold, *Chem. Rev.* **88**, 899 (1988)), reimplemented here
from those papers:

1. **Pre-NAOs.** Diagonalize the density inside each atom's own ``(atom, l)``
   block, averaged over the ``m`` components so degenerate shells stay
   degenerate and the orbitals keep their atomic symmetry.
2. **Partition.** Split the result into the *natural minimal basis* -- the
   shells this element occupies as a free atom -- and the *natural Rydberg
   basis*, everything else.
3. **Occupancy-weighted symmetric orthogonalization** of the minimal basis
   across atoms. This is the step the whole method turns on: ordinary
   orthogonalization treats an empty Rydberg orbital as the equal of a lone
   pair, and it is that equality that lets diffuse functions steal population.
4. **Schmidt** the Rydberg set against the now-orthonormal minimal basis, then
   orthogonalize it among itself the same weighted way.
5. **Re-diagonalize** each atomic block to restore natural (eigenfunction)
   character, again averaged over ``m``.

Then NPA is simply the occupancies summed per atom, subtracted from the nuclear
charge.

**The population operator is ``Q = S P S``, not ``P``.** The generalized problem
``Q c = S c n`` *is* the natural-orbital equation, so its eigenvalues are
occupancies bounded by 2; diagonalizing ``P`` against ``S`` instead gave oxygen a
1s orbital holding 3.02 electrons. The same distinction decides how the density
transforms between bases -- contravariantly, ``P' = T^-1 P T^-T``, not by the
overlap's law -- and getting that wrong made benzene's occupancies sum to 9515
instead of 42. Both were caught by the electron-count check in
:meth:`NaturalAtomicOrbitals.consistency`, which is why it is asserted on.

**These numbers are ORCAdesk's own.** The occupancy-weighted orthogonalization
is not specified to the last detail in the literature, so small differences from
the NBO program are structural rather than bugs, and nothing here should be
presented as NBO-compatible. What *is* checked is the property the method exists
for: that the answer barely moves when the basis set changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .wavefunction import Wavefunction, WavefunctionError, _matrix_power, _symmetrize

#: Aufbau filling order as ``(n, l)``. Only the *sequence* matters: it decides
#: how many shells of each symmetry a free atom occupies, which is the natural
#: minimal basis. The half-dozen aufbau exceptions (Cr, Cu, Pd ...) move an
#: electron between shells that are both already occupied, so they do not change
#: the count -- Pd is the one that does, and it gains a nearly empty 5s.
_AUFBAU = [(1, 0), (2, 0), (2, 1), (3, 0), (3, 1), (4, 0), (3, 2), (4, 1),
           (5, 0), (4, 2), (5, 1), (6, 0), (4, 3), (5, 2), (6, 1), (7, 0),
           (5, 3), (6, 2), (7, 1)]

#: Shells an effective core potential replaces, by the number of electrons it
#: replaces. An ECP removes whole principal shells in *shell* order, not aufbau
#: order (a 28-electron core is [Ar]3d, which keeps 4s), so this cannot be
#: derived from :data:`_AUFBAU`. Each entry's electron count equals its key --
#: a test asserts that, so a typo cannot sit here quietly.
_ECP_CORES: dict[int, dict[int, int]] = {
    2:  {0: 1},                              # [He]
    10: {0: 2, 1: 1},                        # [Ne]
    18: {0: 3, 1: 2},                        # [Ar]
    28: {0: 3, 1: 2, 2: 1},                  # [Ar] 3d
    36: {0: 4, 1: 3, 2: 1},                  # [Kr]
    46: {0: 4, 1: 3, 2: 2},                  # [Kr] 4d
    54: {0: 5, 1: 4, 2: 2},                  # [Xe]
    60: {0: 4, 1: 3, 2: 2, 3: 1},            # [Kr] 4d 4f
    68: {0: 5, 1: 4, 2: 2, 3: 1},            # [Xe] 4f
    78: {0: 5, 1: 4, 2: 3, 3: 1},            # [Xe] 4f 5d
}

#: Occupancy floor for the orthogonalization weights. A weight of exactly zero
#: would make the weighted metric singular; this keeps an empty orbital's
#: influence negligible without letting it vanish.
_WEIGHT_FLOOR = 1e-4

_SHELL_LETTER = {0: "s", 1: "p", 2: "d", 3: "f", 4: "g", 5: "h"}


def free_atom_shells(atomic_number: int) -> dict[int, int]:
    """``{l: number of shells of that symmetry}`` a free atom of ``Z`` occupies."""
    remaining, shells = int(atomic_number), {}
    for _n, l in _AUFBAU:
        if remaining <= 0:
            break
        shells[l] = shells.get(l, 0) + 1
        remaining -= 2 * (2 * l + 1)
    return shells


def minimal_basis_shells(atomic_number: int, nuclear_charge: float) -> dict[int, int]:
    """The natural minimal basis of one atom, as ``{l: shell count}``.

    ``nuclear_charge`` is the *effective* one, so an ECP atom drops the shells
    its potential replaced: iodine's basis holds no 1s-3d, and counting them
    would move real valence shells into the Rydberg set and mis-weight the
    orthogonalization that follows.
    """
    shells = free_atom_shells(atomic_number)
    n_core = int(round(atomic_number - nuclear_charge))
    if n_core <= 0:
        return shells
    if n_core not in _ECP_CORES:
        raise WavefunctionError(
            f"this calculation uses an effective core potential replacing "
            f"{n_core} electrons, which ORCAdesk does not have the shell "
            "structure for. Natural population analysis needs to know which "
            "shells the potential stands in for.")
    core = _ECP_CORES[n_core]
    return {l: count - core.get(l, 0) for l, count in shells.items()
            if count - core.get(l, 0) > 0}


def _symmetry_groups(wf: Wavefunction) -> dict[tuple[int, int], list[np.ndarray]]:
    """``(atom, l)`` -> one index array per ``m``, each ordered by shell.

    The ``m`` components of one shell are equivalent by symmetry, so every step
    below averages over them and applies a single transformation to all -- which
    is what keeps a degenerate p or d shell degenerate instead of letting
    numerical noise split it.
    """
    grouped: dict[tuple[int, int], dict[int, list[int]]] = {}
    for mu in range(wf.n_basis):
        key = (int(wf.bf_atom[mu]), int(wf.bf_l[mu]))
        grouped.setdefault(key, {}).setdefault(int(wf.bf_m[mu]), []).append(mu)
    return {
        key: [np.array(sorted(idx, key=lambda i: wf.bf_shell[i]))
              for _m, idx in sorted(by_m.items())]
        for key, by_m in grouped.items()
    }


def _generalized_eigh(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Solve ``a c = b c w`` for symmetric ``a`` and positive-definite ``b``,
    most-occupied first. numpy has no generalized eigensolver, so this goes
    through ``b^-1/2`` -- which is the one place scipy would otherwise be
    needed."""
    b_half = _matrix_power(b, -0.5)
    w, v = np.linalg.eigh(_symmetrize(b_half @ a @ b_half))
    order = np.argsort(w)[::-1]
    return w[order], b_half @ v[:, order]


def _weighted_orthogonalization(overlap: np.ndarray,
                                weights: np.ndarray) -> np.ndarray:
    """Occupancy-weighted symmetric orthogonalization (OWSO).

    Symmetric orthogonalization is the one that changes the orbitals *least* in
    a least-squares sense. Weighting that measure by occupancy changes what
    "least" means: a doubly-occupied lone pair is held nearly fixed while an
    empty Rydberg orbital, which nothing observable depends on, absorbs the
    distortion. With equal weights this reduces exactly to Loewdin's.
    """
    w = np.diag(weights)
    return w @ _matrix_power(_symmetrize(w @ overlap @ w), -0.5)


@dataclass
class NaturalAtomicOrbitals:
    """The NAO basis of one wavefunction, and the populations it implies."""

    coefficients: np.ndarray        # (nbf, nbf) AO -> NAO, columns are orbitals
    # The density in the NAO basis, C^T (S P S) C. Its diagonal is
    # `occupations`; the off-diagonal is what bond orders are read from, so
    # it is kept rather than recomputed (it is the expensive product here).
    density: np.ndarray             # (nbf, nbf)
    occupations: np.ndarray         # (nbf,) electrons in each NAO
    atom: np.ndarray                # (nbf,) owning atom
    l: np.ndarray                   # (nbf,) angular momentum
    minimal: np.ndarray             # (nbf,) bool -- in the natural minimal basis
    principal: np.ndarray           # (nbf,) int -- shell n, 0 for Rydberg
    labels: list[str]               # e.g. "O 2p", "H 1s", "C Ryd(d)"
    charges: np.ndarray             # (natoms,) NPA charges
    spin_populations: np.ndarray    # (natoms,) NAO spin densities (0 if closed)
    _wf: Wavefunction = field(repr=False, default=None)  # type: ignore[assignment]

    @property
    def n_electrons(self) -> float:
        return float(self.occupations.sum())

    @property
    def minimal_fraction(self) -> float:
        """Share of the electron density the natural minimal basis holds.

        The headline diagnostic of an NPA: a well-behaved molecule keeps
        >99% here, because the minimal basis is meant to be the chemistry and
        the Rydberg set only its polarization tail. A low value means the
        density is not describable as atoms-plus-small-corrections, and the
        charges deserve less trust.
        """
        total = self.occupations.sum()
        return float(self.occupations[self.minimal].sum() / total) if total else 0.0

    def consistency(self) -> dict:
        """Residuals a caller (or a test) can assert on. Both of the errors this
        module was written around -- a density transformed by the overlap's law,
        and populations taken from ``P`` rather than ``S P S`` -- show up here as
        an electron count that is not the electron count."""
        overlap = _symmetrize(self.coefficients.T @ self._wf.overlap()
                              @ self.coefficients)
        return {
            "orthonormality": float(
                np.abs(overlap - np.eye(overlap.shape[0])).max()),
            "electron_count": self.n_electrons,
            "electron_count_error": abs(self.n_electrons - self._wf.n_electrons),
            "charge_error": abs(float(self.charges.sum()) - self._wf.charge),
            "max_occupancy": float(self.occupations.max()),
            "min_occupancy": float(self.occupations.min()),
            "minimal_fraction": self.minimal_fraction,
        }


def natural_atomic_orbitals(wf: Wavefunction) -> NaturalAtomicOrbitals:
    """Build the natural atomic orbitals of ``wf`` and the NPA charges they give.

    Raises :class:`WavefunctionError` for a Cartesian basis (the symmetry
    averaging below assumes the ``m`` components of a shell are equivalent,
    which Cartesian d/f functions are not) and for an effective core potential
    whose shell structure is unknown.
    """
    if not wf.spherical:
        raise WavefunctionError(
            "this wavefunction uses Cartesian d/f functions, whose components "
            "are not equivalent by symmetry, so natural atomic orbitals cannot "
            "be formed from it. Re-run with a spherical-harmonic basis.")

    overlap = wf.overlap()
    # The population operator. Q c = S c n IS the natural-orbital equation, so
    # its eigenvalues are occupancies -- see the module docstring.
    population = _symmetrize(overlap @ wf.density("total") @ overlap)
    n = wf.n_basis
    groups = _symmetry_groups(wf)

    def metric(t: np.ndarray) -> np.ndarray:
        return _symmetrize(t.T @ overlap @ t)

    def occupancies(t: np.ndarray) -> np.ndarray:
        """Per-orbital occupancy in a basis that need not be normalized yet."""
        return (np.diag(_symmetrize(t.T @ population @ t))
                / np.maximum(np.diag(metric(t)), 1e-30))

    # ---- 1. pre-NAOs: diagonalize each atomic block, averaged over m --------
    pre = np.zeros((n, n))
    pre_occupations = np.zeros(n)
    for _key, per_m in groups.items():
        block_q = np.mean([population[np.ix_(i, i)] for i in per_m], axis=0)
        block_s = np.mean([overlap[np.ix_(i, i)] for i in per_m], axis=0)
        w, c = _generalized_eigh(_symmetrize(block_q), _symmetrize(block_s))
        for idx in per_m:                      # one transformation for every m
            pre[np.ix_(idx, idx)] = c
            pre_occupations[idx] = w

    # ---- 2. natural minimal basis vs natural Rydberg basis ------------------
    minimal = np.zeros(n, dtype=bool)
    for (atom, l), per_m in groups.items():
        wanted = minimal_basis_shells(int(wf.atomic_numbers[atom]),
                                      float(wf.nuclear_charges[atom])).get(l, 0)
        for idx in per_m:                      # pre-NAOs are occupancy-ordered
            minimal[idx[:wanted]] = True
    nmb = np.flatnonzero(minimal)
    nrb = np.flatnonzero(~minimal)

    # ---- 3. OWSO across atoms within the minimal basis ----------------------
    step_nmb = np.eye(n)
    if nmb.size:
        step_nmb[np.ix_(nmb, nmb)] = _weighted_orthogonalization(
            metric(pre)[np.ix_(nmb, nmb)],
            np.maximum(pre_occupations[nmb], _WEIGHT_FLOOR))

    # ---- 4. Schmidt the Rydberg set against it, then OWSO it too ------------
    step_schmidt = np.eye(n)
    if nmb.size and nrb.size:
        step_schmidt[np.ix_(nmb, nrb)] = -metric(pre @ step_nmb)[np.ix_(nmb, nrb)]
    step_nrb = np.eye(n)
    if nrb.size:
        current = pre @ step_nmb @ step_schmidt
        step_nrb[np.ix_(nrb, nrb)] = _weighted_orthogonalization(
            metric(current)[np.ix_(nrb, nrb)],
            np.maximum(occupancies(current)[nrb], _WEIGHT_FLOOR))

    coefficients = pre @ step_nmb @ step_schmidt @ step_nrb

    # ---- 5. restore natural character within each atomic block --------------
    orthonormal_q = _symmetrize(coefficients.T @ population @ coefficients)
    restore = np.eye(n)
    for _key, per_m in groups.items():
        block = np.mean([orthonormal_q[np.ix_(i, i)] for i in per_m], axis=0)
        w, c = np.linalg.eigh(_symmetrize(block))
        c = c[:, np.argsort(w)[::-1]]
        for idx in per_m:
            restore[np.ix_(idx, idx)] = c
    coefficients = coefficients @ restore

    # ---- populations --------------------------------------------------------
    nao_density = _symmetrize(coefficients.T @ population @ coefficients)
    occupations = np.diag(nao_density).copy()
    charges = wf.nuclear_charges.astype(float).copy()
    np.add.at(charges, wf.bf_atom, -occupations)

    labels, principal = _name_orbitals(wf, groups, minimal)

    spin = np.zeros(wf.n_atoms)
    if not wf.restricted:
        spin_q = _symmetrize(overlap @ wf.density("spin") @ overlap)
        np.add.at(spin, wf.bf_atom,
                  np.diag(_symmetrize(coefficients.T @ spin_q @ coefficients)))

    return NaturalAtomicOrbitals(
        coefficients=coefficients, density=nao_density, occupations=occupations,
        atom=wf.bf_atom.copy(), l=wf.bf_l.copy(), minimal=minimal,
        principal=principal, labels=labels,
        charges=charges, spin_populations=spin, _wf=wf)


def _name_orbitals(wf: Wavefunction, groups: dict,
                   minimal: np.ndarray) -> tuple[list[str], np.ndarray]:
    """Chemist's names and principal quantum numbers: ``"O 2p"``, ``"C Ryd(d)"``.

    ``n`` is *counted*, not read from the basis: the minimal-basis orbitals of
    symmetry ``l`` are the free atom's occupied shells in order, so the k-th is
    ``n = l + 1 + k`` -- 1s, 2s, 3s, or 2p, 3p. For an ECP atom the replaced
    shells shift that start, which is what the offset is. A Rydberg orbital gets
    ``n = 0``: it is not one of the atom's shells at all, and inventing a number
    for it would invite someone to sort by it.

    Returned together because they are one rule. Anything downstream that needs
    the shell (the core/valence split, an electron configuration) reads ``n``
    rather than parsing it back out of the label (P4).
    """
    labels = [""] * wf.n_basis
    principal = np.zeros(wf.n_basis, dtype=int)
    for (atom, l), per_m in groups.items():
        element = wf.elements[atom]
        n_core = int(round(wf.atomic_numbers[atom] - wf.nuclear_charges[atom]))
        offset = _ECP_CORES.get(n_core, {}).get(l, 0) if n_core > 0 else 0
        letter = _SHELL_LETTER.get(l, f"l{l}")
        for idx in per_m:
            for k, mu in enumerate(idx):
                if minimal[mu]:
                    principal[mu] = l + 1 + k + offset
                    labels[mu] = f"{element} {principal[mu]}{letter}"
                else:
                    labels[mu] = f"{element} Ryd({letter})"
    return labels, principal


def core_shell_counts(atomic_number: int) -> dict[int, int]:
    """``{l: shell count}`` of the atom's *core*: the shells of the noble gas
    before it. The rest of the minimal basis is its valence.

    Counted in absolute shells (1s is the 1st s, 4s the 4th), so this is
    **not** ECP-adjusted: a replaced shell is core and is simply absent from
    the basis, and subtracting it here as well would count it twice -- which it
    was, leaving iodine with no core at all.

    The preceding-noble-gas rule beats a hand-written table for the case that
    matters most: it puts a transition metal's ``(n-1)d`` and ``ns`` both in the
    valence, where chemistry wants them (cobalt: core [Ar], valence 3d + 4s).
    Its one arguable result is iodine's filled 4d, which lands in the valence
    because 4d is not in krypton, where some programs call it core. Only the
    core/valence *subtotals* move; every shell is printed with its own occupancy
    either way, and no charge or bond order depends on the split.
    """
    preceding = 0
    for noble in (2, 10, 18, 36, 54, 86, 118):
        if noble < atomic_number:
            preceding = noble
    return free_atom_shells(preceding) if preceding else {}


def npa_charges(wf: Wavefunction) -> np.ndarray:
    """NPA atomic charges — the common case, without the orbital detail."""
    return natural_atomic_orbitals(wf).charges
