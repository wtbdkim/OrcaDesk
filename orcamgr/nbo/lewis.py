"""
The natural bond orbitals: a Lewis structure found in the density, completed.

A chemist's Lewis structure — cores, lone pairs, two-centre bonds — is a claim
that the electron density can be written as a sum of localized one- and
two-centre pairs. The NBO search tests that claim against the actual density in
the natural-atomic-orbital basis (Foster & Weinhold, *J. Am. Chem. Soc.* **102**,
7211 (1980); Reed, Curtiss & Weinhold, *Chem. Rev.* **88**, 899 (1988)):

1. **Cores** are taken as they stand: each core NAO is a core NBO.
2. **Lone pairs.** Diagonalize the density inside one atom's valence block. An
   eigenvector holding more than the threshold is a lone pair.
3. **Bonds.** Diagonalize the density inside the valence block of a pair of
   atoms. An eigenvector above threshold, with real weight on *both* atoms, is
   a bond. Its two halves, each normalized, are the atoms' **hybrids**; the
   orthogonal combination of the same hybrids is the **antibond**.
4. Repeat 2-3 until nothing new is found.

**Accepted orbitals are projected out, not merely subtracted.** The first draft
depleted the density (``P -= n v v^T``) and looked again, which is what the
papers describe — and it leaked. A lone pair the threshold had just missed on
its own atom reappeared in the next diatomic block as a slightly rotated copy
with 16% borrowed carbon character and was accepted as a third "bond"; the
resulting orbitals overlapped by 0.17, so they were not orbitals at all but
several descriptions of the same density. Working in the orthogonal complement
of everything already accepted (``Q P Q``) makes that impossible by construction
and gives orbitals that are orthonormal to 1e-16 without a final cleanup.

**The threshold is a ladder, and the highest rung that captures the most
density wins.** A delocalized system has no orbital above 1.90 in any diatomic
block — benzene's three pi bonds each hold 1.66 in a Kekulé structure — and an
amide's nitrogen lone pair sits at 1.73 because it is donating into the C=O.
So the search is repeated at 1.90, 1.85, ... 1.50 and the structure with the
largest total Lewis occupancy is kept. That criterion only works *because* of
the projection above: with overlapping orbitals an incomplete structure scored
99% and beat the right one; orthogonal, it scores 91% and loses. Measured on
formamide, which has three candidate resonance structures.

**Open shells get one Lewis structure per spin.** A doublet's unpaired electron
occupies one orbital in the alpha density and none in the beta, so a search on
the total density with a two-electron threshold cannot find it (H2O+ came out
four orbitals of five). Each spin density is searched on its own with a
one-electron threshold, and the two structures may legitimately differ — that
is what "different Lewis structures for different spins" means.

The result is a complete orthonormal basis: Lewis orbitals (CR, LP, BD),
antibonds (BD*), whatever valence space is left over (LP*), and the Rydberg
NAOs (RY) untouched. The second-order analysis in
:mod:`~orcamgr.nbo.perturbation` is a matter of transforming the Fock matrix
into it.

**These numbers are ORCAdesk's own** (P5, appendix A25). The search
reproduces the textbook structures it was checked against — water, dinitrogen,
hydrogen iodide, formamide, a Kekulé benzene — but the NBO program's exact
tie-breaking is not published and small differences in occupancy or in which
resonance form a borderline case lands on are to be expected.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .nao import NaturalAtomicOrbitals, _SHELL_LETTER, core_shell_counts
from .wavefunction import Wavefunction, WavefunctionError, _matrix_power, _symmetrize

#: Occupancy thresholds tried in order, for a doubly-occupied orbital. Halved
#: for one spin of an open shell. The first rung is NBO's default; the ladder
#: below it is for delocalized systems, where no diatomic block reaches 1.90.
THRESHOLD_LADDER = (1.90, 1.85, 1.80, 1.75, 1.70, 1.60, 1.50, 1.40, 1.30)

#: A diatomic candidate with less than this share of its density on the minor
#: atom is a lone pair leaking into the block, not a bond. Polar bonds are far
#: above it (water's O-H is 28% hydrogen), and the ladder rejects any structure
#: that mis-files a lone pair anyway, so this is a guard, not the decision.
MIN_MINOR_SHARE = 0.05

#: Search cycles per threshold. Every real structure converges in one or two;
#: the cap only stops a pathological density from looping.
MAX_CYCLES = 20


@dataclass
class Hybrid:
    """One atom's contribution to a bond: its share, and what the hybrid is
    made of — ``sp2.1`` is ``s`` 32%, ``p`` 68%."""
    atom: int
    share: float                 # fraction of the bond's density on this atom
    composition: dict            # l -> fraction of the hybrid, e.g. {0: .32, 1: .68}

    @property
    def label(self) -> str:
        """``s``, ``sp1.98``, ``sp2.50d0.03`` — the chemist's hybridization."""
        s = self.composition.get(0, 0.0)
        parts = []
        for l in sorted(self.composition):
            frac = self.composition[l]
            if l == 0 or frac < 0.005:
                continue
            ratio = frac / s if s > 1e-6 else float("inf")
            letter = _SHELL_LETTER.get(l, f"l{l}")
            parts.append(f"{letter}{ratio:.2f}" if ratio < 100 else letter)
        return "s" + "".join(parts) if s > 1e-6 else "".join(parts) or "?"


@dataclass
class NaturalBondOrbital:
    """One orbital of the completed NBO basis."""
    index: int
    kind: str                    # CR | LP | BD | BD* | LP* | RY
    atoms: tuple                 # (a,) or (a, b)
    occupancy: float
    energy: float                # diagonal Fock element, Hartree
    vector: np.ndarray           # NAO-basis coefficients, unit norm
    hybrids: list = field(default_factory=list)   # Hybrid per atom, BD/BD* only
    number: int = 1              # BD (2) C1-O2: the 2nd bond of that pair

    @property
    def is_lewis(self) -> bool:
        return self.kind in ("CR", "LP", "BD")

    def label(self, elements: list) -> str:
        who = "-".join(f"{elements[a]}{a + 1}" for a in self.atoms)
        return f"{self.kind} ({self.number}) {who}"


@dataclass
class NboBasis:
    """A complete orthonormal NBO set for one spin (or the whole closed shell)."""
    spin: str                    # "" closed shell | "alpha" | "beta"
    orbitals: list               # NaturalBondOrbital, Lewis first
    threshold: float             # the ladder rung that won
    lewis_electrons: float
    total_electrons: float
    coefficients: np.ndarray     # (nbf, n_orbitals) NAO -> NBO, one column each
    fock: np.ndarray             # (n, n) Fock matrix in this basis

    @property
    def lewis_fraction(self) -> float:
        return self.lewis_electrons / self.total_electrons if self.total_electrons else 0.0

    @property
    def non_lewis_electrons(self) -> float:
        return self.total_electrons - self.lewis_electrons


# ---------------------------------------------------------------------------
# partitioning the NAO basis
# ---------------------------------------------------------------------------

def _partition(nao: NaturalAtomicOrbitals, wf: Wavefunction):
    """Core / valence / Rydberg masks over the NAOs, and each atom's valence
    indices. Core is the preceding noble gas (see nao.core_shell_counts)."""
    n = nao.occupations.size
    core_counts = [core_shell_counts(int(wf.atomic_numbers[a]))
                   for a in range(wf.n_atoms)]
    is_core = np.zeros(n, dtype=bool)
    for mu in range(n):
        if not nao.minimal[mu]:
            continue
        a, l, p = int(nao.atom[mu]), int(nao.l[mu]), int(nao.principal[mu])
        if p - l <= core_counts[a].get(l, 0):
            is_core[mu] = True
    valence = nao.minimal & ~is_core
    per_atom = {a: np.flatnonzero(valence & (nao.atom == a))
                for a in range(wf.n_atoms)}
    return is_core, valence, per_atom


def _nao_density(nao: NaturalAtomicOrbitals, wf: Wavefunction, spin: str) -> np.ndarray:
    """The density of one spin (or the total) in the NAO basis."""
    if spin == "":
        return nao.density
    s, c = wf.overlap(), nao.coefficients
    return _symmetrize(c.T @ s @ wf.density(spin) @ s @ c)


# ---------------------------------------------------------------------------
# the search
# ---------------------------------------------------------------------------

def _search(density: np.ndarray, is_core: np.ndarray, per_atom: dict,
            atom_of: np.ndarray, threshold: float, n_atoms: int):
    """One pass of the Lewis search at one threshold.

    Returns ``(found, lewis_occupancy)`` where ``found`` is a list of
    ``(kind, atoms, occupancy, vector)``. Vectors are unit and mutually
    orthogonal by construction — see the module docstring.

    Runs entirely inside the natural minimal basis. The Rydberg NAOs take no
    part in a Lewis structure and are orthogonal to everything that does, so
    carrying them costs a cube of the full basis size per candidate for
    nothing: aspirin (451 functions, 73 minimal) took 36 s that way and 0.2 s
    this way. Vectors are embedded back into the full basis on the way out.
    """
    minimal = np.flatnonzero(is_core | np.isin(np.arange(density.shape[0]),
                                                 np.concatenate(list(per_atom.values()))))
    local = {int(mu): k for k, mu in enumerate(minimal)}
    n_full, n = density.shape[0], minimal.size
    p_min = density[np.ix_(minimal, minimal)]
    core_local = np.array([local[int(mu)] for mu in np.flatnonzero(is_core)], dtype=int)
    atoms_local = {a: np.array([local[int(mu)] for mu in idx], dtype=int)
                   for a, idx in per_atom.items()}

    projector = np.eye(n)              # onto the not-yet-assigned space
    working = p_min.copy()             # Q P Q, refreshed only on accept
    found: list = []

    def accept(kind, atoms, occ, v):
        nonlocal projector, working
        full = np.zeros(n_full)
        full[minimal] = v
        found.append((kind, atoms, occ, full))
        projector = projector - np.outer(v, v)
        working = projector @ p_min @ projector

    for k in core_local:
        v = np.zeros(n)
        v[k] = 1.0
        accept("CR", (int(atom_of[minimal[k]]),), float(p_min[k, k]), v)

    def best_in(idx: np.ndarray):
        """Most-occupied direction of this block within the working space,
        with its occupancy read off the ORIGINAL density."""
        if idx.size == 0:
            return 0.0, None
        w, vec = np.linalg.eigh(_symmetrize(working[np.ix_(idx, idx)]))
        v = np.zeros(n)
        v[idx] = vec[:, int(np.argmax(w))]
        v = projector @ v
        norm = float(np.linalg.norm(v))
        if norm < 1e-8:
            return 0.0, None
        v /= norm
        return float(v @ p_min @ v), v
    per_atom = atoms_local

    for _cycle in range(MAX_CYCLES):
        progress = False
        for a in range(n_atoms):
            while per_atom[a].size:
                occ, v = best_in(per_atom[a])
                if v is None or occ < threshold:
                    break
                accept("LP", (a,), occ, v)
                progress = True
        for a in range(n_atoms):
            for b in range(a + 1, n_atoms):
                idx = np.concatenate([per_atom[a], per_atom[b]])
                while idx.size:
                    occ, v = best_in(idx)
                    if v is None or occ < threshold:
                        break
                    on_a = float(np.sum(v[per_atom[a]] ** 2))
                    on_b = float(np.sum(v[per_atom[b]] ** 2))
                    if min(on_a, on_b) / max(on_a + on_b, 1e-12) < MIN_MINOR_SHARE:
                        break                      # a lone pair, not a bond
                    accept("BD", (a, b), occ, v)
                    progress = True
        if not progress:
            break

    return found, float(sum(f[2] for f in found))


# ---------------------------------------------------------------------------
# completing the basis
# ---------------------------------------------------------------------------

def _hybrids(vector: np.ndarray, atoms: tuple, nao: NaturalAtomicOrbitals) -> list:
    """Split a bond vector into its per-atom hybrids, with composition by l."""
    out = []
    for a in atoms:
        on_atom = vector * (nao.atom == a)
        share = float(np.sum(on_atom ** 2))
        composition: dict = {}
        if share > 1e-12:
            for l in np.unique(nao.l[nao.atom == a]):
                part = float(np.sum(on_atom[nao.l == l] ** 2)) / share
                if part > 1e-6:
                    composition[int(l)] = part
        out.append(Hybrid(atom=int(a), share=share, composition=composition))
    return out


def _antibond(vector: np.ndarray, atoms: tuple, nao: NaturalAtomicOrbitals) -> np.ndarray:
    """The orthogonal combination of a bond's two hybrids: ``b h_A - a h_B``."""
    a, b = atoms
    v_a = vector * (nao.atom == a)
    v_b = vector * (nao.atom == b)
    na, nb = np.linalg.norm(v_a), np.linalg.norm(v_b)
    if na < 1e-12 or nb < 1e-12:
        return None
    star = nb * (v_a / na) - na * (v_b / nb)
    return star / np.linalg.norm(star)


def _find_lewis(density: np.ndarray, is_core: np.ndarray, per_atom: dict,
                atom_of: np.ndarray, n_atoms: int, closed_shell: bool):
    """Run the threshold ladder.

    Stops at the first rung whose structure is *complete* -- one orbital per
    electron pair (or per electron, for one spin). Because accepted orbitals are
    projected out, a rung can never over-assign, so the first complete structure
    is the highest-threshold one and the best; a rung that comes up short is an
    incomplete structure and the ladder goes on. If no rung completes, the one
    with the most Lewis density is kept and the analysis reports it as such.
    """
    expected = int(round(np.trace(density) / (2.0 if closed_shell else 1.0)))
    best = None
    for rung in THRESHOLD_LADDER:
        threshold = rung if closed_shell else rung / 2.0
        found, lewis = _search(density, is_core, per_atom, atom_of, threshold, n_atoms)
        if best is None or lewis > best[1] + 1e-6:
            best = (found, lewis, threshold)
        if len(found) >= expected:
            break
    return best


def _complete(found: list, density: np.ndarray, fock_nao: np.ndarray,
              nao: NaturalAtomicOrbitals, valence: np.ndarray,
              spin: str, threshold: float, lewis: float) -> NboBasis:
    """Add antibonds, leftover valence and Rydberg orbitals to a Lewis set and
    assemble the orthonormal basis with its Fock matrix."""
    n = density.shape[0]
    columns: list = []
    orbitals: list = []
    counters: dict = {}

    def number(kind, atoms):
        key = (kind, atoms)
        counters[key] = counters.get(key, 0) + 1
        return counters[key]

    def add(kind, atoms, vector, hybrids=None):
        columns.append(vector)
        orbitals.append(NaturalBondOrbital(
            index=len(orbitals), kind=kind, atoms=atoms,
            occupancy=float(vector @ density @ vector),
            energy=float(vector @ fock_nao @ vector),
            vector=vector, hybrids=hybrids or [], number=number(kind, atoms)))

    # Lewis orbitals, in the order found (cores, then by cycle)
    for kind, atoms, _occ, v in found:
        add(kind, atoms, v, _hybrids(v, atoms, nao) if kind == "BD" else None)
    lewis_columns = np.column_stack(columns) if columns else np.zeros((n, 0))

    # ---- the non-Lewis space is the exact complement of the Lewis space ----
    #
    # Its rank is fixed the moment the Lewis structure is: n_minimal - n_lewis.
    # Everything below assigns orbitals INSIDE that complement and never past
    # its rank. The first draft built antibonds and leftover orbitals one at a
    # time and checked nothing, and on real molecules it produced 773 orbitals
    # for 772 functions (a leftover direction counted from two atoms) and a
    # singular Gram matrix (two antibonds that had become the same vector).
    minimal_idx = np.flatnonzero(nao.minimal)
    complement = np.eye(n) - lewis_columns @ lewis_columns.T
    complement[np.ix_(~nao.minimal, ~nao.minimal)] = 0.0   # Rydberg handled last
    complement[~nao.minimal, :] = 0.0
    complement[:, ~nao.minimal] = 0.0
    rank = minimal_idx.size - lewis_columns.shape[1]

    # Antibonds, one per bond, orthogonalized against the Lewis set and then
    # symmetrically among themselves -- which changes each as little as
    # possible. A candidate that the Lewis set already spans (its projection
    # has no length) is dropped rather than divided by zero; the direction it
    # would have described is picked up as a leftover orbital below.
    star_vectors, star_atoms = [], []
    for kind, atoms, _occ, v in found:
        if kind != "BD":
            continue
        star = _antibond(v, atoms, nao)
        if star is None:
            continue
        star = complement @ star
        norm = float(np.linalg.norm(star))
        if norm > 1e-6:
            star_vectors.append(star / norm)
            star_atoms.append(atoms)
    if star_vectors and rank > 0:
        stars = np.column_stack(star_vectors)
        gram = _symmetrize(stars.T @ stars)
        w = np.linalg.eigvalsh(gram)
        if w.min() > 1e-6 and stars.shape[1] <= rank:
            # Independent: symmetric orthogonalization, the least-change one.
            stars = stars @ _matrix_power(gram, -0.5)
            kept = [(k, stars[:, k]) for k in range(stars.shape[1])]
        else:
            # Dependent (two bonds whose antibonds coincide): Gram-Schmidt in
            # order, dropping what is already spanned. The first draft
            # renormalized the output of a rank-deficient symmetric step and
            # produced a basis 0.8 off orthonormal.
            kept = []
            for k in range(stars.shape[1]):
                v = complement @ stars[:, k]
                for _j, u in kept:
                    v = v - u * float(u @ v)
                norm = float(np.linalg.norm(v))
                if norm > 1e-6 and len(kept) < rank:
                    kept.append((k, v / norm))
        for k, v in kept:
            add("BD*", star_atoms[k], v, _hybrids(v, star_atoms[k], nao))
            complement = complement - np.outer(v, v)

    # Whatever valence space is left: the range of the remaining complement,
    # as natural orbitals of the density there, each filed under the atom
    # carrying most of it. The count is read off the projector's eigenvalues
    # (those at 1), never computed from the bookkeeping above, so it is exact.
    # Only the minimal block can have rank: the complement is zero on the
    # Rydberg rows by construction, and an eigh of the full basis (1644**2 on
    # the largest corpus system) tripled the run for nothing.
    w, vec_min = np.linalg.eigh(_symmetrize(complement[np.ix_(minimal_idx, minimal_idx)]))
    span = np.zeros((n, int((w > 0.5).sum())))
    span[minimal_idx, :] = vec_min[:, w > 0.5]
    if span.shape[1]:
        block = _symmetrize(span.T @ density @ span)
        occ, rot = np.linalg.eigh(block)
        span = span @ rot[:, np.argsort(occ)[::-1]]
        for k in range(span.shape[1]):
            v = span[:, k]
            weight = np.zeros(int(nao.atom.max()) + 1)
            np.add.at(weight, nao.atom, v ** 2)
            add("LP*", (int(np.argmax(weight)),), v)

    # Rydberg NAOs are already orthogonal to everything above
    for mu in np.flatnonzero(~nao.minimal):
        v = np.zeros(n)
        v[mu] = 1.0
        add("RY", (int(nao.atom[mu]),), v)

    coefficients = np.column_stack(columns)
    if coefficients.shape[1] != n:
        raise WavefunctionError(
            f"the NBO basis has {coefficients.shape[1]} orbitals for {n} "
            "natural atomic orbitals -- the Lewis search left the basis "
            "incomplete, which should not happen.")
    fock_nbo = _symmetrize(coefficients.T @ fock_nao @ coefficients)
    return NboBasis(spin=spin, orbitals=orbitals, threshold=threshold,
                    lewis_electrons=lewis, total_electrons=float(np.trace(density)),
                    coefficients=coefficients, fock=fock_nbo)


def natural_bond_orbitals(nao: NaturalAtomicOrbitals) -> list:
    """The complete NBO basis of a wavefunction: one :class:`NboBasis` for a
    closed shell, or one per spin for an open shell."""
    wf = nao._wf
    is_core, valence, per_atom = _partition(nao, wf)
    spins = [""] if wf.restricted else ["alpha", "beta"]
    out = []
    for spin in spins:
        density = _nao_density(nao, wf, spin)
        fock_nao = _symmetrize(nao.coefficients.T @ wf.fock(spin or "alpha")
                               @ nao.coefficients)
        found, lewis, threshold = _find_lewis(density, is_core, per_atom, nao.atom,
                                              wf.n_atoms, closed_shell=(spin == ""))
        out.append(_complete(found, density, fock_nao, nao, valence,
                             spin, threshold, lewis))
    return out
