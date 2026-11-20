"""
One finished calculation's natural-orbital analysis, cached beside the run.

The layers below answer "what are the natural atomic orbitals" and "what do
they say"; this one answers "what does the Results tab show", and stores it so
the answer is computed once. The parts are:

* :func:`analyze` — a :class:`~orcamgr.nbo.wavefunction.Wavefunction` in, an
  :class:`NboAnalysis` out. Pure, and the only place the pieces are assembled.
* :func:`analysis_for` — the same starting from a run folder, converting the
  ``.gbw`` and reading or writing the cache.

**Why a cache at all.** The analysis is a few matrix products at the cube of the
basis size: 0.08 s at 117 basis functions but ten seconds at 1644, and a Results
tab that recomputed on every visit would be unusable on exactly the molecules
people care about. The wavefunction never changes after a job finishes, so the
result is worth keeping.

**What invalidates it.** The ``.gbw``'s modification time is recorded in the
cache, so re-running a calculation into the same folder produces a newer ``.gbw``
and the stale analysis is discarded rather than served for the previous run's
wavefunction. A :data:`CACHE_FORMAT` stamp does the same across ORCAdesk
versions: when the algorithm changes, old files are recomputed instead of
quietly mixing two implementations' numbers in one queue.

**What is not cached.** The NAO coefficient matrix — ``nbf**2`` floats, tens of
megabytes on a large molecule, and nothing on screen needs it. Rydberg orbitals
are not listed individually either; there are more of them than of anything
interesting and their whole content is one number per atom.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .nao import NaturalAtomicOrbitals, natural_atomic_orbitals
from .lewis import NboBasis, natural_bond_orbitals
from .perturbation import second_order_interactions
from .population import AtomPopulation, atom_populations, wiberg_bonds
from .source import wavefunction_for
from .wavefunction import Wavefunction, WavefunctionError

#: Bumped when a change to the analysis would alter published numbers. A cache
#: written by a different format is recomputed, never merged.
# 3: every orbital row carries its index into the NBO basis, which is what
#    the 3D viewer addresses a cube by.
CACHE_FORMAT = 3

CACHE_SUFFIX = ".nbo.json"


@dataclass
class NaoRow:
    """One natural atomic orbital worth showing: the minimal-basis ones."""
    atom: int
    element: str
    label: str                  # "O 2p"
    occupancy: float


@dataclass
class HybridRow:
    """One atom's half of a bond orbital, as shown: ``72% O (sp3.21)``."""
    atom: int
    element: str
    share: float
    label: str


@dataclass
class OrbitalRow:
    """One natural bond orbital worth listing: every Lewis orbital, every
    antibond and leftover valence orbital. Rydberg orbitals are summarized
    instead -- there are more of them than of everything else together.

    ``index`` is the orbital's position in its spin's NBO basis -- the
    address a cube request names it by (:mod:`orcamgr.nbo.cubes`)."""
    label: str                  # "BD (1) C1-O2"
    index: int
    kind: str                   # CR | LP | BD | BD* | LP*
    atoms: list
    occupancy: float
    energy: float               # Hartree
    hybrids: list = field(default_factory=list)   # HybridRow, BD/BD* only


@dataclass
class InteractionRow:
    """One line of the second-order donor -> acceptor table."""
    donor: str                  # "LP (1) N3"
    acceptor: str               # "BD* (1) C1-O2"
    energy_kcal: float
    gap_hartree: float
    fock_hartree: float


@dataclass
class LewisSummary:
    """One spin's Lewis structure and what lies beyond it."""
    spin: str                   # "" | "alpha" | "beta"
    threshold: float            # the ladder rung that produced it
    lewis_electrons: float
    total_electrons: float
    lewis_fraction: float
    complete: bool              # one orbital per pair (or per electron)
    rydberg_count: int
    rydberg_electrons: float
    orbitals: list = field(default_factory=list)       # OrbitalRow
    interactions: list = field(default_factory=list)   # InteractionRow, strongest first


#: Donor -> acceptor entries kept per spin. The full table on a large molecule
#: runs to thousands; what is quoted is the top of it.
MAX_INTERACTIONS = 100


def _summarize(basis: NboBasis, elements: list) -> LewisSummary:
    rows = []
    for o in basis.orbitals:
        if o.kind == "RY":
            continue
        rows.append(OrbitalRow(
            label=o.label(elements), index=o.index, kind=o.kind, atoms=list(o.atoms),
            occupancy=o.occupancy, energy=o.energy,
            hybrids=[HybridRow(atom=h.atom, element=elements[h.atom],
                               share=h.share, label=h.label) for h in o.hybrids]))
    rydberg = [o for o in basis.orbitals if o.kind == "RY"]
    n_lewis = sum(o.is_lewis for o in basis.orbitals)
    expected = int(round(basis.total_electrons / (2.0 if basis.spin == "" else 1.0)))
    inter = [InteractionRow(
        donor=basis.orbitals[x.donor].label(elements),
        acceptor=basis.orbitals[x.acceptor].label(elements),
        energy_kcal=x.energy_kcal, gap_hartree=x.gap_hartree,
        fock_hartree=x.fock_hartree)
        for x in second_order_interactions(basis)[:MAX_INTERACTIONS]]
    return LewisSummary(
        spin=basis.spin, threshold=basis.threshold,
        lewis_electrons=basis.lewis_electrons, total_electrons=basis.total_electrons,
        lewis_fraction=basis.lewis_fraction, complete=(n_lewis >= expected),
        rydberg_count=len(rydberg),
        rydberg_electrons=float(sum(o.occupancy for o in rydberg)),
        orbitals=rows, interactions=inter)


def _lewis_from_dict(data: dict) -> LewisSummary:
    return LewisSummary(
        spin=str(data.get("spin", "")), threshold=float(data.get("threshold", 0.0)),
        lewis_electrons=float(data.get("lewis_electrons", 0.0)),
        total_electrons=float(data.get("total_electrons", 0.0)),
        lewis_fraction=float(data.get("lewis_fraction", 0.0)),
        complete=bool(data.get("complete", False)),
        rydberg_count=int(data.get("rydberg_count", 0)),
        rydberg_electrons=float(data.get("rydberg_electrons", 0.0)),
        orbitals=[OrbitalRow(**{**row, "hybrids": [HybridRow(**h) for h in row.get("hybrids", [])]})
                  for row in data.get("orbitals", [])],
        interactions=[InteractionRow(**row) for row in data.get("interactions", [])])


@dataclass
class NboAnalysis:
    """Everything the Results tab needs from a natural-orbital analysis."""

    base: str
    n_atoms: int
    n_basis: int
    n_electrons: float
    charge: float
    restricted: bool
    has_ecp: bool
    atoms: list[AtomPopulation] = field(default_factory=list)
    bonds: list[tuple[int, int, float]] = field(default_factory=list)
    orbitals: list[NaoRow] = field(default_factory=list)
    #: The Lewis structure(s): one for a closed shell, one per spin otherwise,
    #: each with its orbitals and second-order interactions.
    lewis: list = field(default_factory=list)          # LewisSummary
    diagnostics: dict = field(default_factory=dict)
    #: Sentences to show beside the numbers when they deserve less trust. Empty
    #: is the normal case; this reports rather than silently degrading (P2).
    warnings: list = field(default_factory=list)
    #: mtime of the ``.gbw`` this came from; "" when built straight from a
    #: Wavefunction with no run folder behind it.
    source_mtime: float = 0.0
    format: int = CACHE_FORMAT

    # ---- serialization ------------------------------------------------------

    def to_dict(self) -> dict:
        data = asdict(self)
        # tuples survive a round trip through JSON as lists; normalize now so a
        # cached analysis and a fresh one compare equal.
        data["bonds"] = [[int(i), int(j), float(o)] for i, j, o in self.bonds]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "NboAnalysis":
        return cls(
            base=str(data.get("base", "")),
            n_atoms=int(data.get("n_atoms", 0)),
            n_basis=int(data.get("n_basis", 0)),
            n_electrons=float(data.get("n_electrons", 0.0)),
            charge=float(data.get("charge", 0.0)),
            restricted=bool(data.get("restricted", True)),
            has_ecp=bool(data.get("has_ecp", False)),
            atoms=[AtomPopulation(**row) for row in data.get("atoms", [])],
            bonds=[(int(i), int(j), float(o)) for i, j, o in data.get("bonds", [])],
            orbitals=[NaoRow(**row) for row in data.get("orbitals", [])],
            lewis=[_lewis_from_dict(row) for row in data.get("lewis", [])],
            diagnostics=dict(data.get("diagnostics", {})),
            warnings=[str(w) for w in data.get("warnings", [])],
            source_mtime=float(data.get("source_mtime", 0.0)),
            format=int(data.get("format", 0)))


@dataclass
class NboOrbitalSet:
    """The orbitals themselves -- what the tables are read off, and what the
    3D viewer draws. Kept apart from :class:`NboAnalysis` because it holds the
    ``nbf**2`` coefficient matrices that are never cached or sent anywhere:
    the bridge keeps one in memory beside the analysis it came from."""
    wavefunction: Wavefunction
    nao: NaturalAtomicOrbitals
    bases: list                       # NboBasis, one per spin
    source_mtime: float = 0.0

    @classmethod
    def from_wavefunction(cls, wf: Wavefunction, source_mtime: float = 0.0) -> "NboOrbitalSet":
        nao = natural_atomic_orbitals(wf)
        return cls(wavefunction=wf, nao=nao, bases=natural_bond_orbitals(nao),
                   source_mtime=source_mtime)

    def basis(self, operator: int) -> NboBasis:
        """The NBO basis of spin ``operator`` (0 = alpha or closed shell, 1 = beta)."""
        if operator < 0 or operator >= len(self.bases):
            raise WavefunctionError(
                "this calculation is closed-shell: it has no beta orbital set."
                if operator == 1 else f"there is no orbital set {operator}.")
        return self.bases[operator]

    def orbital(self, operator: int, index: int):
        """One :class:`~orcamgr.nbo.lewis.NaturalBondOrbital`, by position."""
        basis = self.basis(operator)
        if index < 0 or index >= len(basis.orbitals):
            raise WavefunctionError(
                f"natural bond orbital {index} is out of range "
                f"(this set has {len(basis.orbitals)}).")
        return basis.orbitals[index]

    def ao_coefficients(self, operator: int, index: int) -> np.ndarray:
        """The orbital as a vector over the basis functions the Molden file
        lists: NAO -> NBO, then AO -> NAO. What the grid evaluates."""
        self.orbital(operator, index)          # range check
        return self.nao.coefficients @ self.basis(operator).coefficients[:, index]


def analyze_set(orbitals: NboOrbitalSet, base: str = "") -> NboAnalysis:
    """The tables of an orbital set already built."""
    wf, nao = orbitals.wavefunction, orbitals.nao
    diagnostics = nao.consistency()
    diagnostics["minimal_fraction"] = nao.minimal_fraction
    lewis = [_summarize(b, wf.elements) for b in orbitals.bases]
    diagnostics["lewis_fraction"] = min(l.lewis_fraction for l in lewis)
    return NboAnalysis(
        base=base or Path(wf.source).stem,
        n_atoms=wf.n_atoms, n_basis=wf.n_basis,
        n_electrons=wf.n_electrons, charge=wf.charge,
        restricted=wf.restricted, has_ecp=wf.has_ecp,
        atoms=atom_populations(nao),
        bonds=wiberg_bonds(nao),
        orbitals=[NaoRow(atom=int(nao.atom[i]), element=wf.elements[nao.atom[i]],
                         label=nao.labels[i], occupancy=float(nao.occupations[i]))
                  for i in range(wf.n_basis) if nao.minimal[i]],
        lewis=lewis,
        diagnostics=diagnostics,
        warnings=_warnings(diagnostics, lewis),
        source_mtime=orbitals.source_mtime)


def analyze(wf: Wavefunction, base: str = "",
            source_mtime: float = 0.0) -> NboAnalysis:
    """Run the whole analysis on an already-loaded wavefunction."""
    return analyze_set(NboOrbitalSet.from_wavefunction(wf, source_mtime), base)


#: Below this share of the density in the natural minimal basis, the molecule is
#: not well described as atoms plus small corrections and the charges deserve
#: less weight. Set below where this implementation normally lands (99.3-99.7%,
#: see nao.py) so it flags a real problem rather than firing on every result.
MINIMAL_FRACTION_FLOOR = 0.99


def _warnings(diagnostics: dict, lewis: list = ()) -> list:
    out = []
    for summary in lewis:
        which = f" ({summary.spin})" if summary.spin else ""
        usual = 1.90 if summary.spin == "" else 0.95
        if not summary.complete:
            out.append(
                f"No single Lewis structure{which} accounts for every electron "
                f"pair even at an occupancy threshold of {summary.threshold:.2f}: "
                "this molecule is strongly delocalized, and the structure shown "
                "is the best of several.")
        elif summary.threshold < usual - 1e-9:
            out.append(
                f"The Lewis structure{which} was found at an occupancy threshold "
                f"of {summary.threshold:.2f}, below the usual {usual:.2f}: some of "
                "its orbitals are noticeably delocalized (an aromatic ring, an "
                "amide, a conjugated system). The second-order table says where.")
    fraction = float(diagnostics.get("minimal_fraction", 1.0))
    if fraction < MINIMAL_FRACTION_FLOOR:
        out.append(
            f"Only {100 * fraction:.1f}% of the electron density fits in the "
            "natural minimal basis, so this molecule is not well described as "
            "atoms plus small corrections. Treat the charges as indicative.")
    if float(diagnostics.get("electron_count_error", 0.0)) > 1e-6:
        out.append(
            "The natural orbitals do not quite account for every electron "
            f"({diagnostics.get('electron_count_error'):.2e} out), which points "
            "at a near-linearly-dependent basis set.")
    return out


def cache_path(run_dir: str | Path, base: str) -> Path:
    return Path(run_dir) / f"{base}{CACHE_SUFFIX}"


def read_cache(run_dir: str | Path, base: str, source_mtime: float):
    """A cached analysis for this exact wavefunction, or None.

    Never raises: an unreadable or half-written cache is a reason to recompute,
    not to fail an analysis the user asked for (P6).
    """
    path = cache_path(run_dir, base)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or data.get("format") != CACHE_FORMAT:
        return None
    try:
        cached = NboAnalysis.from_dict(data)
    except (TypeError, ValueError, KeyError):
        return None
    # A re-run rewrites the .gbw; anything older describes a wavefunction that
    # no longer exists.
    if abs(cached.source_mtime - source_mtime) > 1e-6:
        return None
    return cached


def write_cache(run_dir: str | Path, base: str, analysis: NboAnalysis) -> bool:
    """Store an analysis beside its run. Best-effort: a read-only or full disk
    costs the cache, never the result the caller already has (P32)."""
    try:
        cache_path(run_dir, base).write_text(
            json.dumps(analysis.to_dict(), indent=1), encoding="utf-8")
        return True
    except (OSError, TypeError, ValueError):
        return False


def _source_mtime(run_dir: Path, base: str) -> float:
    try:
        return (run_dir / f"{base}.gbw").stat().st_mtime
    except OSError:
        return 0.0


def orbital_set_for(orca_path: str | Path, run_dir: str | Path, base: str) -> NboOrbitalSet:
    """The NBO basis of one finished calculation, built from its ``.gbw``
    (converted if needed). Never cached on disk -- see :class:`NboOrbitalSet`."""
    run_dir = Path(run_dir)
    wf = wavefunction_for(orca_path, run_dir, base)
    return NboOrbitalSet.from_wavefunction(wf, _source_mtime(run_dir, base))


def analysis_for(orca_path: str | Path, run_dir: str | Path, base: str,
                 use_cache: bool = True,
                 keep: Optional[Callable[[NboOrbitalSet], None]] = None) -> NboAnalysis:
    """The natural-orbital analysis of one finished calculation.

    Converts the ``.gbw`` if needed, serves a current cache when there is one,
    and stores what it computes. Raises :class:`WavefunctionError` with an
    actionable message when the calculation cannot be analysed at all.

    ``keep`` receives the :class:`NboOrbitalSet` whenever one is actually
    built -- the bridge holds it for the 3D viewer, so the first cube after
    a fresh analysis costs no second search. A cache hit builds none.
    """
    run_dir = Path(run_dir)
    source_mtime = _source_mtime(run_dir, base)

    if use_cache and source_mtime:
        cached = read_cache(run_dir, base, source_mtime)
        if cached is not None:
            return cached

    orbitals = orbital_set_for(orca_path, run_dir, base)
    if keep is not None:
        keep(orbitals)
    analysis = analyze_set(orbitals, base=base)
    if use_cache and source_mtime:
        write_cache(run_dir, base, analysis)
    return analysis
