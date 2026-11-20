"""
Chemistry read off the natural atomic orbitals: bond orders and configurations.

:mod:`~orcamgr.nbo.nao` does the hard part — building an orthonormal set of
orbitals that each belong to one atom. Once that exists, two of the things
people actually want from an NBO run are a few lines of arithmetic on it.

**Wiberg bond indices.** ``WBI(A,B)`` is the sum of squared density-matrix
elements between the orbitals of A and those of B. The formula only means
anything in an *orthogonal* basis — in the AO basis, where the functions
overlap, the same sum double-counts shared density and is not a bond order at
all. That is precisely what the NAO basis provides, and why ORCA's own Mayer
bond orders (which correct for the overlap instead) are a different number for
the same bond. Both are useful; a paper asking for "Wiberg bond index in the
NAO basis" is asking for this one.

**Natural electron configuration.** The occupancies grouped by shell —
``O [core] 2s(1.76) 2p(4.65)`` — which is how you see that a formally d⁷
cobalt(II) is really holding 7.66 d electrons because its ligands are donating,
or that a bond is polarized long before you look at any orbital picture.

The core/valence split follows the preceding noble gas (see
:func:`~orcamgr.nbo.nao.core_shell_counts`), which puts a transition metal's
``(n-1)d`` and ``ns`` both in the valence where chemistry wants them.

Qt-free and pure, like the rest of the package.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .nao import NaturalAtomicOrbitals, _SHELL_LETTER, core_shell_counts

#: Bond orders below this are left out of :func:`wiberg_bonds`. Every pair of
#: atoms in a molecule has some non-zero index, so a list without a cut is a
#: list of every pair. It sits below the weakest thing anyone calls a bond, so
#: no bond is hidden -- but it is deliberately inclusive, and 1,3 neighbours
#: across a ring or a carboxyl group (0.10-0.16 in aspirin) do come through.
#: Raise it at the call site to see bonds only; :func:`wiberg_bond_orders`
#: returns the whole matrix and judges nothing.
BOND_THRESHOLD = 0.1


def wiberg_bond_orders(nao: NaturalAtomicOrbitals) -> np.ndarray:
    """The full ``(n_atoms, n_atoms)`` Wiberg bond-index matrix in the NAO basis.

    The diagonal is left at zero: an atom's index with itself is not a bond
    order, and leaving it in would quietly corrupt any row sum taken as a
    valence.
    """
    n_atoms = int(nao.atom.max()) + 1 if nao.atom.size else 0
    squared = nao.density ** 2
    orders = np.zeros((n_atoms, n_atoms))
    # Sum the squared block for every atom pair: rows by atom, then columns.
    by_row = np.zeros((n_atoms, squared.shape[1]))
    np.add.at(by_row, nao.atom, squared)
    np.add.at(orders.T, nao.atom, by_row.T)
    np.fill_diagonal(orders, 0.0)
    return orders


def natural_valences(nao: NaturalAtomicOrbitals) -> np.ndarray:
    """Per-atom natural valence: how much bonding each atom is doing in total,
    as the sum of its bond orders to every other atom."""
    return wiberg_bond_orders(nao).sum(axis=1)


def wiberg_bonds(nao: NaturalAtomicOrbitals,
                 threshold: float = BOND_THRESHOLD) -> list[tuple[int, int, float]]:
    """Atom pairs with a bond order above ``threshold``, strongest first, as
    ``(atom_i, atom_j, order)`` with ``i < j``."""
    orders = wiberg_bond_orders(nao)
    pairs = [(i, j, float(orders[i, j]))
             for i, j in zip(*np.triu_indices(orders.shape[0], k=1))
             if orders[i, j] >= threshold]
    return sorted(pairs, key=lambda p: -p[2])


@dataclass
class AtomPopulation:
    """One atom's line in a natural population analysis."""

    index: int
    element: str
    charge: float
    core: float                 # electrons in the atom's core shells
    valence: float              # ... in its valence shells
    rydberg: float              # ... outside the natural minimal basis
    total: float
    configuration: str          # "[core] 2s( 1.76) 2p( 4.65)"
    spin: float                 # NAO spin density (0 when closed-shell)
    valence_index: float        # natural valence: sum of its bond orders


def atom_populations(nao: NaturalAtomicOrbitals) -> list[AtomPopulation]:
    """The per-atom table: charge, the core/valence/Rydberg split, and the
    natural electron configuration."""
    wf = nao._wf
    valences = natural_valences(nao)
    n_atoms = wf.n_atoms

    core = np.zeros(n_atoms)
    valence = np.zeros(n_atoms)
    rydberg = np.zeros(n_atoms)
    # shell populations per atom, keyed by (n, l) so they can be printed in order
    shells: list[dict[tuple[int, int], float]] = [{} for _ in range(n_atoms)]

    core_counts = [core_shell_counts(int(wf.atomic_numbers[a]))
                   for a in range(n_atoms)]
    for mu in range(wf.n_basis):
        atom = int(nao.atom[mu])
        occupancy = float(nao.occupations[mu])
        if not nao.minimal[mu]:
            rydberg[atom] += occupancy
            continue
        n, l = int(nao.principal[mu]), int(nao.l[mu])
        # A shell is core when its n is within the count the noble-gas core
        # allows for that l -- the k-th shell of symmetry l starts at n = l + 1.
        if n - l <= core_counts[atom].get(l, 0):
            core[atom] += occupancy
        else:
            valence[atom] += occupancy
            shells[atom][(n, l)] = shells[atom].get((n, l), 0.0) + occupancy

    out = []
    for atom in range(n_atoms):
        parts = [f"{n}{_SHELL_LETTER.get(l, l)}({pop:6.2f})"
                 for (n, l), pop in sorted(shells[atom].items())]
        if rydberg[atom] >= 0.005:
            parts.append(f"Ryd({rydberg[atom]:6.2f})")
        prefix = "[core] " if core[atom] > 0 else ""
        out.append(AtomPopulation(
            index=atom, element=wf.elements[atom],
            charge=float(nao.charges[atom]),
            core=float(core[atom]), valence=float(valence[atom]),
            rydberg=float(rydberg[atom]),
            total=float(core[atom] + valence[atom] + rydberg[atom]),
            configuration=(prefix + " ".join(parts)).strip(),
            spin=float(nao.spin_populations[atom]),
            valence_index=float(valences[atom])))
    return out
