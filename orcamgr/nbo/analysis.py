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

from .nao import NaturalAtomicOrbitals, natural_atomic_orbitals
from .population import AtomPopulation, atom_populations, wiberg_bonds
from .source import wavefunction_for
from .wavefunction import Wavefunction, WavefunctionError

#: Bumped when a change to the analysis would alter published numbers. A cache
#: written by a different format is recomputed, never merged.
CACHE_FORMAT = 1

CACHE_SUFFIX = ".nbo.json"


@dataclass
class NaoRow:
    """One natural atomic orbital worth showing: the minimal-basis ones."""
    atom: int
    element: str
    label: str                  # "O 2p"
    occupancy: float


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
            diagnostics=dict(data.get("diagnostics", {})),
            warnings=[str(w) for w in data.get("warnings", [])],
            source_mtime=float(data.get("source_mtime", 0.0)),
            format=int(data.get("format", 0)))


def analyze(wf: Wavefunction, base: str = "",
            source_mtime: float = 0.0) -> NboAnalysis:
    """Run the whole analysis on an already-loaded wavefunction."""
    nao: NaturalAtomicOrbitals = natural_atomic_orbitals(wf)
    diagnostics = nao.consistency()
    diagnostics["minimal_fraction"] = nao.minimal_fraction
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
        diagnostics=diagnostics,
        warnings=_warnings(diagnostics),
        source_mtime=source_mtime)


#: Below this share of the density in the natural minimal basis, the molecule is
#: not well described as atoms plus small corrections and the charges deserve
#: less weight. Set below where this implementation normally lands (99.3-99.7%,
#: see nao.py) so it flags a real problem rather than firing on every result.
MINIMAL_FRACTION_FLOOR = 0.99


def _warnings(diagnostics: dict) -> list:
    out = []
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


def analysis_for(orca_path: str | Path, run_dir: str | Path, base: str,
                 use_cache: bool = True) -> NboAnalysis:
    """The natural-orbital analysis of one finished calculation.

    Converts the ``.gbw`` if needed, serves a current cache when there is one,
    and stores what it computes. Raises :class:`WavefunctionError` with an
    actionable message when the calculation cannot be analysed at all.
    """
    run_dir = Path(run_dir)
    try:
        source_mtime = (run_dir / f"{base}.gbw").stat().st_mtime
    except OSError:
        source_mtime = 0.0

    if use_cache and source_mtime:
        cached = read_cache(run_dir, base, source_mtime)
        if cached is not None:
            return cached

    wf = wavefunction_for(orca_path, run_dir, base)
    analysis = analyze(wf, base=base, source_mtime=source_mtime)
    if use_cache and source_mtime:
        write_cache(run_dir, base, analysis)
    return analysis
