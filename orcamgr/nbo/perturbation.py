"""
Second-order donor-acceptor analysis: what the Lewis structure leaves out.

A Lewis structure is a zeroth-order description. Everything it misses — the
few percent of density in antibonds and Rydberg orbitals — is *delocalization*,
and the NBO basis makes it quantifiable one interaction at a time. Each filled
Lewis orbital ``i`` interacts with each empty non-Lewis orbital ``j`` through
the off-diagonal Fock element ``F_ij``, and second-order perturbation theory
puts an energy on it:

    ΔE(2)  =  - q_i · F_ij² / (ε_j − ε_i)

with ``q_i`` the donor's occupancy (two, or one per spin) and ``ε`` the diagonal
Fock elements. That number is the stabilization the molecule gains by letting
``i`` leak into ``j`` — and it is what people mean when they say "NBO analysis":
the amide nitrogen lone pair donating into the carbonyl π* (resonance),
σ(C–H) → σ*(C–C) (hyperconjugation), a lone pair into an adjacent σ* (the
anomeric effect), LP(O) → σ*(O–H) across a hydrogen bond. Each gets a
kcal/mol, which is why the table is quoted in papers.

This is the whole module: the hard work was building a basis in which the
question is one matrix element. Everything here is a transform and a formula.

**Read the number for what it is.** ΔE(2) is a perturbative estimate in a
specific basis, not an observable, and it grows as the donor and acceptor get
closer in energy — so a large value between near-degenerate orbitals overstates
the case. It is the standard tool for comparing interactions *within* one
molecule and it is presented as such; it is not presented as NBO-program
output (P5, appendix A25).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .lewis import NboBasis

HARTREE_TO_KCAL = 627.5094740631

#: Interactions weaker than this are not listed. NBO's own print threshold;
#: below it the table is noise, and a large molecule has thousands of entries.
PRINT_THRESHOLD_KCAL = 0.5


@dataclass
class Interaction:
    """One donor → acceptor entry of the second-order table."""
    donor: int                   # index into NboBasis.orbitals
    acceptor: int
    energy_kcal: float           # ΔE(2), positive = stabilizing
    gap_hartree: float           # ε_j − ε_i
    fock_hartree: float          # F_ij
    spin: str                    # "" | "alpha" | "beta"


def second_order_interactions(basis: NboBasis,
                              threshold_kcal: float = PRINT_THRESHOLD_KCAL) -> list:
    """Every donor → acceptor interaction above ``threshold_kcal``, strongest first.

    Donors are the Lewis orbitals (CR, LP, BD), acceptors everything else
    (BD*, LP*, RY). Core donors are included — their contributions are tiny and
    NBO lists them too — but nothing is ever paired with itself or with another
    Lewis orbital.
    """
    orbitals = basis.orbitals
    fock = basis.fock
    energies = np.diag(fock)
    donors = [o for o in orbitals if o.is_lewis]
    acceptors = [o for o in orbitals if not o.is_lewis]
    if not donors or not acceptors:
        return []

    d_idx = np.array([o.index for o in donors])
    a_idx = np.array([o.index for o in acceptors])
    q = np.array([o.occupancy for o in donors])
    f = fock[np.ix_(d_idx, a_idx)]
    gap = energies[a_idx][None, :] - energies[d_idx][:, None]
    # A non-positive gap means the "acceptor" lies below the donor, where the
    # formula is meaningless; those pairs are skipped rather than reported as
    # a negative or infinite stabilization.
    with np.errstate(divide="ignore", invalid="ignore"):
        de = np.where(gap > 1e-8, q[:, None] * f ** 2 / gap, 0.0) * HARTREE_TO_KCAL

    out = []
    for r, c in zip(*np.nonzero(de >= threshold_kcal)):
        out.append(Interaction(
            donor=int(d_idx[r]), acceptor=int(a_idx[c]),
            energy_kcal=float(de[r, c]), gap_hartree=float(gap[r, c]),
            fock_hartree=float(f[r, c]), spin=basis.spin))
    out.sort(key=lambda x: -x.energy_kcal)
    return out
