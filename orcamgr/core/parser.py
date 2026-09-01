"""
ORCA output (.out) parser for ORCA 6.x.

Parses a finished ORCA output file into a structured ParseResult.
Designed and verified against ORCA 6.1.1 output (benzene + Ltyr-MV test set).

All section detection is marker-based and tolerant of Windows (\\r\\n)
line endings. When a quantity appears multiple times (e.g. during a
geometry optimization), the LAST occurrence is used, since that is the
converged / final value.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from .tailer import decode_orca_line


# ---- physical constants -------------------------------------------------
HARTREE_TO_EV = 27.211386245988
HARTREE_TO_KCAL = 627.5094740631


# ---- data containers ----------------------------------------------------
@dataclass
class Atom:
    symbol: str
    x: float
    y: float
    z: float


@dataclass
class Orbital:
    index: int
    occ: float
    energy_eh: float
    energy_ev: float
    # "" for a restricted calculation (one manifold), else "a"/"b" — an
    # unrestricted run prints SPIN UP and SPIN DOWN as two separate tables and
    # the index restarts at 0 in each, so it does not identify an orbital alone.
    spin: str = ""


@dataclass
class Transition:
    """A single TD-DFT electronic transition."""
    state: int
    energy_ev: float
    energy_cm: float
    wavelength_nm: float
    fosc: float
    # Spin multiplicity of the excited state, read from the "N-3A" suffix of
    # ORCA's transition label (1 = singlet, 3 = triplet). With `triplets true`
    # both manifolds are printed in ONE energy-sorted table and the state number
    # restarts per multiplicity, so without this the state number is ambiguous
    # and the row count is twice the root count.
    mult: int = 1


@dataclass
class Conformer:
    """One member of a CREST conformer ensemble. ``rel_kcal`` is relative to the
    lowest-energy conformer. ``geometry`` is the full structure so a downstream
    ORCA calc can reference this exact conformer."""
    index: int              # 1-based rank (1 = lowest energy)
    energy_eh: float        # absolute energy (Hartree), from the .xyz comment line
    rel_kcal: float         # energy relative to the best conformer (kcal/mol)
    geometry: list = field(default_factory=list)   # list[Atom]


# Homogeneous scalar summary rows: (attribute, label, format, category), in
# emission order. ``summary_rows()`` interleaves these tables with the
# composite / conditional rows (File, Termination, Optimization, HOMO/LUMO,
# Rotational, Frequencies, TD-DFT, NMR), which stay explicit there. The label
# text, decimal places / units, category, and row order are a tested contract.
_SUMMARY_ENERGY_ROWS = [
    ("final_energy_eh", "Final SP energy", "{:.8f} Eh", ""),
    ("dispersion_correction_eh", "Dispersion corr.", "{:.8f} Eh", ""),
    # SCF energy decomposition (electronic)
    ("nuclear_repulsion_eh", "Nuclear repulsion", "{:.6f} Eh", "elec"),
    ("electronic_energy_eh", "Electronic energy", "{:.6f} Eh", "elec"),
    ("one_electron_eh", "One-electron energy", "{:.6f} Eh", "elec"),
    ("two_electron_eh", "Two-electron energy", "{:.6f} Eh", "elec"),
    ("kinetic_energy_eh", "Kinetic energy", "{:.6f} Eh", "elec"),
    ("potential_energy_eh", "Potential energy", "{:.6f} Eh", "elec"),
    ("virial_ratio", "Virial ratio", "{:.5f}", "elec"),
]
_SUMMARY_PROP_ROWS = [
    ("gap_ev", "HOMO-LUMO gap", "{:.4f} eV", "elec"),
    ("dipole_debye", "Dipole moment", "{:.4f} Debye", "elec"),
]
_SUMMARY_THERMO_ROWS = [
    ("gibbs_eh", "Final Gibbs G", "{:.8f} Eh", ""),
    ("total_thermal_eh", "Inner energy U", "{:.8f} Eh", ""),
    ("enthalpy_eh", "Enthalpy H", "{:.8f} Eh", ""),
    ("entropy_term_eh", "Entropy term T·S", "{:.8f} Eh", ""),
    ("zpe_eh", "ZPE", "{:.8f} Eh", ""),
    ("g_minus_e_el_eh", "G - E(el)", "{:.8f} Eh", ""),
    ("temperature_k", "Temperature", "{:.2f} K", ""),
    ("pressure_atm", "Pressure", "{:.2f} atm", ""),
]


@dataclass
class ParseResult:
    # --- file / status ---
    filename: str = ""
    path: str = ""
    orca_version: str = ""
    terminated_normally: bool = False
    error_message: str = ""
    run_time_seconds: Optional[float] = None
    run_time_string: str = ""

    # --- input echo ---
    input_keywords: str = ""
    input_block: str = ""
    charge: Optional[int] = None
    multiplicity: Optional[int] = None
    n_electrons: Optional[int] = None
    n_atoms: int = 0

    # --- energies ---
    final_energy_eh: Optional[float] = None
    dispersion_correction_eh: Optional[float] = None

    # --- SCF energy decomposition (TOTAL SCF ENERGY block) ---
    nuclear_repulsion_eh: Optional[float] = None
    electronic_energy_eh: Optional[float] = None
    one_electron_eh: Optional[float] = None
    two_electron_eh: Optional[float] = None
    kinetic_energy_eh: Optional[float] = None
    potential_energy_eh: Optional[float] = None
    virial_ratio: Optional[float] = None

    # --- electric / rotational properties ---
    dipole_au: Optional[float] = None
    dipole_debye: Optional[float] = None
    rot_const_cm: list[float] = field(default_factory=list)    # 3 constants, cm^-1
    rot_const_mhz: list[float] = field(default_factory=list)   # 3 constants, MHz

    # --- optimization ---
    is_optimization: bool = False
    opt_converged: bool = False

    # --- geometry (final) ---
    geometry: list[Atom] = field(default_factory=list)

    # --- CREST conformer search (kind == 'crest_conf'; runs via orcamgr/crest/) ---
    # A conformer search produces an ensemble rather than a single structure.
    # geometry/final_energy_eh are set to the lowest-energy conformer (so the
    # existing best-geometry reference path still works), and the full ranked
    # ensemble is exposed here for the Results tab to list + select from.
    is_conformer_search: bool = False
    conformers: list = field(default_factory=list)   # list[Conformer], rank-sorted

    # --- orbitals ---
    orbitals: list[Orbital] = field(default_factory=list)
    homo_index: Optional[int] = None
    lumo_index: Optional[int] = None
    # which manifold the frontier orbital is in ("" restricted, else "a"/"b"):
    # in an unrestricted run the index alone does not identify an orbital
    homo_spin: str = ""
    lumo_spin: str = ""
    homo_ev: Optional[float] = None
    lumo_ev: Optional[float] = None
    gap_ev: Optional[float] = None

    # --- population ---
    mulliken_charges: list[tuple[str, float]] = field(default_factory=list)
    loewdin_charges: list[tuple[str, float]] = field(default_factory=list)
    # Mayer population: per-atom total valence (idx, element, VA)
    mayer_valences: list = field(default_factory=list)
    # Mayer bond orders: (label_i e.g. "0-N", label_j e.g. "1-C", order)
    mayer_bonds: list = field(default_factory=list)

    # --- frequencies / thermochemistry ---
    has_frequencies: bool = False
    frequencies: list[float] = field(default_factory=list)
    n_imaginary: int = 0
    zpe_eh: Optional[float] = None
    total_thermal_eh: Optional[float] = None
    gibbs_eh: Optional[float] = None
    g_minus_e_el_eh: Optional[float] = None
    temperature_k: Optional[float] = None
    pressure_atm: Optional[float] = None
    enthalpy_eh: Optional[float] = None
    entropy_term_eh: Optional[float] = None   # T*S, the "Final entropy term"

    # --- TD-DFT ---
    has_tddft: bool = False
    transitions: list[Transition] = field(default_factory=list)
    # per excited state: {"state", "ev", "contributions": [(from, to, weight)]}
    tddft_states: list = field(default_factory=list)

    # --- NMR ---
    has_nmr: bool = False
    # list of (nucleus_index, element, isotropic_ppm, anisotropy_ppm)
    nmr_shieldings: list[tuple[int, str, float, float]] = field(default_factory=list)

    # --- NEB-TS / IRC reaction path (PATH SUMMARY table) ---
    has_neb_path: bool = False
    # list of dicts: {label (e.g. "0","TS","9"), e_eh, de_kcal, is_ts}
    neb_path: list = field(default_factory=list)
    # which table the path came from: "neb" (PATH SUMMARY FOR NEB-TS) or "irc"
    # (IRC PATH SUMMARY) — the Results tab titles/captions the profile per kind
    neb_path_kind: str = "neb"

    @property
    def shows_electronic_props(self) -> bool:
        """Whether to surface general electronic-structure properties (orbitals /
        HOMO-LUMO, atomic charges, Mayer bonds, dipole, rotational constants, SCF
        energy decomposition). ORCA prints these for every SCF job, but they are
        only shown as results for single-point and optimization runs; specialty
        runs (freq / tddft / nmr / neb) show only their specialty result."""
        specialty = (self.has_frequencies or self.has_tddft
                     or self.has_nmr or self.has_neb_path
                     or self.is_conformer_search)
        return self.is_optimization or not specialty

    def summary_rows(self) -> list[tuple[str, str, str]]:
        """Label/value/category rows. category is ``"elec"`` for general
        electronic-structure properties (SCF decomposition, HOMO/LUMO, dipole,
        rotational constants) which the Results tab hides on specialty jobs
        unless 'Show all' is on; ``""`` otherwise. All rows are always emitted —
        the per-kind filtering happens on the front-end using ``show_elec``."""
        rows: list[tuple[str, str, str]] = []

        def add(label: str, value: str, cat: str = "") -> None:
            rows.append((label, value, cat))

        def add_scalars(spec: list[tuple[str, str, str, str]]) -> None:
            # homogeneous rows from the module-level _SUMMARY_* tables;
            # each is emitted only when its attribute is set
            for attr, label, fmt, cat in spec:
                v = getattr(self, attr)
                if v is not None:
                    add(label, fmt.format(v), cat)

        add("File", self.filename)
        # only when a version was actually parsed — MLIP/CREST results (and
        # non-ORCA files) have none, and a "?" row is just noise for them
        if self.orca_version:
            add("ORCA version", self.orca_version)
        add("Termination",
            "Normal" if self.terminated_normally else "ABNORMAL / incomplete")
        if self.error_message:
            add("Error", self.error_message)
        if self.run_time_string:
            add("Run time", self.run_time_string)
        if self.charge is not None:
            add("Charge / Mult", f"{self.charge} / {self.multiplicity}")
        if self.n_atoms:
            add("Atoms", str(self.n_atoms))
        if self.n_electrons is not None:
            add("Electrons", str(self.n_electrons))
        add_scalars(_SUMMARY_ENERGY_ROWS)
        if self.is_optimization:
            add("Optimization",
                "converged" if self.opt_converged else "NOT converged")
        # orbital / dipole / rotational props (electronic)
        if self.homo_ev is not None:
            add("HOMO", f"#{self.homo_index}{_spin_tag(self.homo_spin)}  "
                        f"{self.homo_ev:.4f} eV", "elec")
        if self.lumo_ev is not None:
            add("LUMO", f"#{self.lumo_index}{_spin_tag(self.lumo_spin)}  "
                        f"{self.lumo_ev:.4f} eV", "elec")
        add_scalars(_SUMMARY_PROP_ROWS)
        if self.rot_const_cm:
            add("Rotational const.",
                " / ".join(f"{c:.4f}" for c in self.rot_const_cm) + " cm⁻¹", "elec")
        if self.has_frequencies:
            add("Frequencies", f"{len(self.frequencies)} modes")
            add("Imaginary modes", str(self.n_imaginary))
        # Thermochemistry can exist WITHOUT a frequencies table (e.g. an IRC job
        # reading a Hessian prints a full GIBBS FREE ENERGY block), so each row
        # is emitted on its own presence — never gated behind has_frequencies,
        # per the "all rows are always emitted" contract above.
        add_scalars(_SUMMARY_THERMO_ROWS)
        if self.has_tddft:
            # Counting rows says "6 states" for `nroots 3 / triplets true`,
            # which is three singlets and three triplets. Say which.
            by_mult: dict[int, int] = {}
            for t in self.transitions:
                by_mult[t.mult] = by_mult.get(t.mult, 0) + 1
            if len(by_mult) > 1:
                add("TD-DFT states", " + ".join(
                    f"{n} {_MULT_NAMES.get(m, str(m) + '-plet')}"
                    for m, n in sorted(by_mult.items())))
            else:
                add("TD-DFT states", str(len(self.transitions)))
            bright = self.brightest_transition()
            if bright:
                add("Brightest", f"{bright.wavelength_nm:.1f} nm  (f={bright.fosc:.4f})")
        if self.has_nmr:
            add("NMR nuclei", str(len(self.nmr_shieldings)))
        return rows

    def brightest_transition(self) -> Optional[Transition]:
        if not self.transitions:
            return None
        return max(self.transitions, key=lambda t: t.fosc)


# ---- helpers ------------------------------------------------------------
def _clean_lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _find_all(lines: list[str], needle: str) -> list[int]:
    return [i for i, ln in enumerate(lines) if needle in ln]


def _scan_scalars(lines: list[str], r: ParseResult, spec: dict) -> None:
    """Assign scalar fields from a declarative ``attr -> (pattern, cast)`` spec.

    Every line is visited and each match overwrites the previous value, which
    implements the parser-wide last-occurrence-wins rule (the converged / final
    value of a recurring quantity). ``cast`` receives capture group 1; a cast
    failure leaves the previous value in place (e.g. a malformed
    rotational-constants line)."""
    compiled = [(attr, re.compile(pat), cast) for attr, (pat, cast) in spec.items()]
    for ln in lines:
        for attr, rx, cast in compiled:
            m = rx.search(ln)
            if m:
                try:
                    setattr(r, attr, cast(m.group(1)))
                except ValueError:
                    pass


# Spin multiplicity names for the TD-DFT state count. Only the ones ORCA can
# actually print in one absorption table (a closed-shell reference gives
# singlets and, with `triplets true`, triplets).
_MULT_NAMES = {1: "singlet", 2: "doublet", 3: "triplet", 4: "quartet", 5: "quintet"}


def _spin_tag(spin: str) -> str:
    """" (alpha)"/" (beta)" for a frontier orbital in an unrestricted run, "" for
    a restricted one — where there is only one manifold and the label would be
    noise."""
    return {"a": " (alpha)", "b": " (beta)"}.get(spin, "")


def _scan_rows(lines: list[str], start: int, row_re: re.Pattern, build, stop=None) -> list:
    """Collect table rows from ``lines[start:]``: each stripped line must match
    ``row_re`` (``re.match``; the match object is fed to ``build``). A blank
    line, a line matching the optional ``stop`` predicate, or the first
    non-matching line ends the table."""
    out: list = []
    for ln in lines[start:]:
        s = ln.strip()
        if not s or (stop is not None and stop(s)):
            break
        m = row_re.match(s)
        if not m:
            break
        out.append(build(m))
    return out


def _find_header(lines: list[str], start: int, within: int, pred) -> Optional[int]:
    """Locate a column-header line (``pred(line)`` true) within ``within``
    lines after a section marker; returns its index, or None if absent."""
    for i in range(start, min(start + within, len(lines))):
        if pred(lines[i]):
            return i
    return None


# Common ORCA failure signatures, checked in priority order. Each entry is
# (case-insensitive substring to find, human-readable explanation). The first
# match wins, so put the most specific / informative ones first.
_ERROR_SIGNATURES = [
    ("SCF NOT CONVERGED", "SCF did not converge. Try more SCF iterations, a different guess/convergence setting, or a smaller step."),
    ("CALCULATION ABORTED", "Calculation aborted by ORCA."),
    ("THE OPTIMIZATION HAS NOT CONVERGED", "Geometry optimization did not converge within the step limit. Try increasing MaxIter or a better starting geometry."),
    ("OPTIMIZATION DID NOT CONVERGE", "Geometry optimization did not converge."),
    ("GEOMETRY OPTIMIZATION RUN ABORTED", "Geometry optimization aborted."),
    ("LAMBDA EQUATIONS HAVE NOT CONVERGED", "Response (lambda) equations did not converge."),
    ("CP-SCF NOT CONVERGED", "CP-SCF (response) equations did not converge — affects frequencies/properties."),
    ("INSUFFICIENT MEMORY", "Not enough memory (maxcore too low for this system/basis)."),
    ("NOT ENOUGH MEMORY", "Not enough memory (increase %maxcore or reduce the basis/system size)."),
    ("DISK FULL", "Ran out of disk space for scratch files."),
    ("UNRECOGNIZED", "ORCA did not recognize part of the input (check keywords/blocks)."),
    ("INPUT ERROR", "There is an error in the input file."),
    ("ERROR (ORCA_MAIN)", "ORCA aborted during the main run."),
    ("ABORTING THE RUN", "ORCA aborted the run."),
    ("ORCA FINISHED BY ERROR TERMINATION", "ORCA finished with an error termination — see the lines above in the .out."),
]


def _extract_orca_error(lines: list[str]) -> str:
    """Best-effort explanation of why an ORCA run failed, read from its .out.

    Returns a short message combining a known explanation (if a signature
    matches) with the actual ORCA line, so the user sees both the 'what' and
    the raw text. Falls back to the last error-ish line, then to a generic note.
    """
    upper = [ln.upper() for ln in lines]
    # 1) known signatures (most informative)
    for needle, explanation in _ERROR_SIGNATURES:
        for i, u in enumerate(upper):
            if needle in u:
                raw = lines[i].strip()
                return (f"{explanation} (ORCA: \"{raw[:160]}\")") if raw else explanation
    # 2) generic error-ish line, searched from the end
    for ln in reversed(lines):
        s = ln.strip()
        if not s:
            continue
        su = s.upper()
        if any(k in su for k in ("ERROR", "ABORT", "FATAL", "TERMINATED", "NOT CONVERGED", "FAILED")):
            return s[:200]
    # 3) nothing obvious — show the last non-empty line as a hint
    for ln in reversed(lines):
        if ln.strip():
            return f"No explicit error found; run ended abnormally. Last line: \"{ln.strip()[:140]}\""
    return "Run ended abnormally (empty output)."


# ---- main parser --------------------------------------------------------
class OrcaOutParser:
    """Parse a single ORCA .out file."""

    def __init__(self, path: str):
        self.path = path
        self.filename = os.path.basename(path)

    def parse(self) -> ParseResult:
        # ORCA itself writes 8-bit output, but externally produced files can be
        # UTF-16 (PowerShell 5.1's `orca job.inp > job.out` redirects that way);
        # decoded as utf-8 every marker would miss, so sniff the BOM first.
        with open(self.path, "rb") as f:
            raw = f.read()
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            text = raw.decode("utf-16", errors="replace")
        else:
            # surrogateescape + per-line repair: the file is mixed-encoding on
            # Windows (ORCA's own strings UTF-8, argv paths in the ANSI code
            # page). See tailer.decode_orca_line — the same judgment the live
            # log uses, so the Log tab and the Results tab agree.
            text = raw.decode("utf-8", errors="surrogateescape")
        lines = [decode_orca_line(ln) for ln in _clean_lines(text)]

        r = ParseResult(filename=self.filename, path=self.path)

        self._parse_version_and_status(lines, r)
        self._parse_input_block(lines, r)
        self._parse_charge_mult(lines, r)
        self._parse_final_energy(lines, r)
        self._parse_dispersion(lines, r)
        self._parse_optimization(lines, r)
        self._parse_geometry(lines, r)
        self._parse_orbitals(lines, r)
        self._parse_mulliken(lines, r)
        self._parse_loewdin(lines, r)
        self._parse_mayer(lines, r)
        self._parse_scf_components(lines, r)
        self._parse_dipole(lines, r)
        self._parse_rotational(lines, r)
        self._parse_frequencies(lines, r)
        self._parse_thermochemistry(lines, r)
        self._parse_tddft(lines, r)
        self._parse_tddft_states(lines, r)
        self._parse_nmr(lines, r)
        self._parse_neb_path(lines, r)

        return r

    def _parse_version_and_status(self, lines, r):
        for ln in lines:
            m = re.search(r"Program Version\s+([\d.]+)", ln)
            if m:
                r.orca_version = m.group(1)
                break
        r.terminated_normally = any("ORCA TERMINATED NORMALLY" in ln for ln in lines)
        if not r.terminated_normally:
            r.error_message = _extract_orca_error(lines)
        for ln in lines:
            m = re.search(r"TOTAL RUN TIME:\s*(\d+)\s*days?\s*(\d+)\s*hours?\s*"
                          r"(\d+)\s*minutes?\s*(\d+)\s*seconds?\s*(\d+)\s*msec", ln)
            if m:
                d, h, mi, s, ms = (int(x) for x in m.groups())
                r.run_time_seconds = d * 86400 + h * 3600 + mi * 60 + s + ms / 1000.0
                parts = []
                if d:
                    parts.append(f"{d}d")
                if h:
                    parts.append(f"{h}h")
                if mi:
                    parts.append(f"{mi}m")
                parts.append(f"{s}s")
                r.run_time_string = " ".join(parts)
                break

    def _parse_input_block(self, lines, r):
        starts = _find_all(lines, "INPUT FILE")
        if not starts:
            return
        start = starts[-1]
        block, keywords = [], []
        for ln in lines[start: start + 400]:
            if "END OF INPUT" in ln:
                break
            m = re.match(r"\|\s*\d+>\s?(.*)", ln)
            content = m.group(1) if m else ln
            block.append(content.rstrip())
            if content.lstrip().startswith("!"):
                keywords.append(content.lstrip()[1:].strip())
        r.input_block = "\n".join(block).strip()
        r.input_keywords = " ".join(keywords).strip()

    def _parse_charge_mult(self, lines, r):
        _scan_scalars(lines, r, {
            "charge": (r"Total Charge\s+Charge\s+\.+\s*(-?\d+)", int),
            "multiplicity": (r"Multiplicity\s+Mult\s+\.+\s*(\d+)", int),
            "n_electrons": (r"Number of Electrons\s+NEL\s+\.+\s*(\d+)", int),
        })

    def _parse_final_energy(self, lines, r):
        _scan_scalars(lines, r, {
            "final_energy_eh": (r"FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)", float),
        })

    def _parse_dispersion(self, lines, r):
        _scan_scalars(lines, r, {
            "dispersion_correction_eh":
                (r"^\s*Dispersion correction\s+(-?\d+\.\d+)\s*$", float),
        })

    # An optimization keyword is a whole TOKEN on the ! line, never a substring
    # of one. "opt" in the keyword line also matched the shipped basis labels
    # cc-pVDZ-F12-OptRI / cc-pVTZ-F12-OptRI and the option ExtOpt, so a
    # perfectly good single point was reported as an optimization that did NOT
    # converge — a red row and a "Final geometry" section on a job that never
    # moved an atom. Anchored so OptTS/TightOpt/... still match and OptRI does
    # not; the marker below is the second, independent witness.
    _OPT_KEYWORD = re.compile(
        r"(?:^|\s)(?:(?:very)?tight|loose|normal)?opt(?:ts)?(?:$|\s)", re.I)

    def _parse_optimization(self, lines, r):
        r.opt_converged = any("THE OPTIMIZATION HAS CONVERGED" in ln for ln in lines)
        r.is_optimization = bool(
            self._OPT_KEYWORD.search(r.input_keywords)
            # A raw .inp may drive the optimization from a %geom block with no
            # keyword at all, so the output's own cycle banner counts too.
            or any("GEOMETRY OPTIMIZATION CYCLE" in ln for ln in lines))

    def _parse_geometry(self, lines, r):
        idxs = _find_all(lines, "CARTESIAN COORDINATES (ANGSTROEM)")
        if not idxs:
            return
        atoms: list[Atom] = _scan_rows(
            lines, idxs[-1] + 2,
            re.compile(r"([A-Za-z]{1,3})\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)"),
            lambda m: Atom(m.group(1), float(m.group(2)),
                           float(m.group(3)), float(m.group(4))))
        r.geometry = atoms
        r.n_atoms = len(atoms)

    _ORBITAL_ROW = re.compile(
        r"(\d+)\s+(\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)")

    def _parse_orbitals(self, lines, r):
        """Orbital energies — BOTH manifolds when the run is unrestricted.

        UKS/UHF prints "SPIN UP ORBITALS" and "SPIN DOWN ORBITALS" as two
        tables separated by a blank line. Reading rows until the first blank
        stopped at the end of the alpha table, so an open-shell system's
        HOMO/LUMO and gap came from alpha alone — and for a radical the true
        LUMO is in the OTHER manifold. Measured on ORCA 6.1.1, OH radical
        (UKS B3LYP/def2-SVP, mult 2): alpha-only reports LUMO #5 at +1.5038 eV
        and a 10.3498 eV gap, while across both manifolds the highest occupied
        orbital is beta #3 (-8.1108 eV) and the lowest virtual is beta #4
        (-4.0376 eV) — a 4.0732 eV gap. The frontier pair is therefore taken by
        ENERGY across everything parsed, not by index within one table.
        """
        idxs = _find_all(lines, "ORBITAL ENERGIES")
        if not idxs:
            return
        start = idxs[-1]
        orbs: list[Orbital] = []
        # Each spin table has its own "NO OCC E(Eh) E(eV)" header; a restricted
        # run has exactly one, so the same walk covers both cases.
        cursor, spins = start, ["a", "b"]
        while True:
            header = _find_header(lines, cursor, 8,
                                  lambda ln: "OCC" in ln and "E(eV)" in ln)
            if header is None:
                break
            unrestricted = any("SPIN UP ORBITALS" in ln or "SPIN DOWN ORBITALS" in ln
                               for ln in lines[cursor:header + 1])
            spin = spins.pop(0) if unrestricted and spins else ""
            block = _scan_rows(
                lines, header + 1, self._ORBITAL_ROW,
                lambda m, _s=spin: Orbital(int(m.group(1)), float(m.group(2)),
                                           float(m.group(3)), float(m.group(4)),
                                           spin=_s))
            if not block:
                break
            orbs.extend(block)
            if not unrestricted:
                break
            cursor = header + 1 + len(block)
        if not orbs:
            return
        r.orbitals = orbs
        occupied = [o for o in orbs if o.occ > 0.01]
        virtual = [o for o in orbs if o.occ <= 0.01]
        if not occupied:
            return
        homo = max(occupied, key=lambda o: o.energy_ev)
        r.homo_index = homo.index
        r.homo_ev = homo.energy_ev
        r.homo_spin = homo.spin
        if virtual:
            lumo = min(virtual, key=lambda o: o.energy_ev)
            r.lumo_index = lumo.index
            r.lumo_ev = lumo.energy_ev
            r.lumo_spin = lumo.spin
            r.gap_ev = lumo.energy_ev - homo.energy_ev

    def _parse_atomic_charges(self, lines, marker) -> list[tuple[str, float]]:
        """MULLIKEN/LOEWDIN ATOMIC CHARGES share one table shape; only the
        section marker differs. Returns [] when the section is absent."""
        idxs = _find_all(lines, marker)
        if not idxs:
            return []
        return _scan_rows(
            lines, idxs[-1] + 2,
            re.compile(r"\d+\s+([A-Za-z]{1,3})\s*:\s*(-?\d+\.\d+)"),
            lambda m: (m.group(1), float(m.group(2))),
            stop=lambda s: s.startswith("Sum of atomic charges"))

    def _parse_mulliken(self, lines, r):
        r.mulliken_charges = self._parse_atomic_charges(
            lines, "MULLIKEN ATOMIC CHARGES")

    def _parse_loewdin(self, lines, r):
        r.loewdin_charges = self._parse_atomic_charges(
            lines, "LOEWDIN ATOMIC CHARGES")

    def _parse_mayer(self, lines, r):
        idxs = _find_all(lines, "MAYER POPULATION ANALYSIS")
        if not idxs:
            return
        start = idxs[-1]
        # per-atom table: header "ATOM  NA  ZA  QA  VA  BVA  FA", then rows
        head = _find_header(
            lines, start, 20,
            lambda ln: ln.strip().startswith("ATOM") and "VA" in ln)
        vals: list = []
        scan_from = start
        if head is not None:
            scan_from = head + 1
            vals = _scan_rows(
                lines, head + 1,
                re.compile(
                    r"(\d+)\s+([A-Za-z]{1,3})\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+"
                    r"(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)"),
                # groups: idx, el, NA, ZA, QA, VA, BVA, FA  -> keep VA (group 6)
                lambda m: (int(m.group(1)), m.group(2), float(m.group(6))))
        r.mayer_valences = vals
        # bond orders, printed after the table as "B(  i-El ,  j-El ) :  order"
        bond_re = re.compile(
            r"B\(\s*(\d+)-\s*([A-Za-z]{1,3})\s*,\s*(\d+)-\s*([A-Za-z]{1,3})\s*\)\s*:\s*(-?\d+\.\d+)")
        bonds: list = []
        for ln in lines[scan_from: scan_from + 1500]:
            matches = bond_re.findall(ln)
            if matches:
                for i_idx, i_el, j_idx, j_el, order in matches:
                    bonds.append((f"{i_idx}-{i_el}", f"{j_idx}-{j_el}", float(order)))
            elif bonds and ln.strip() and not ln.strip().startswith("B("):
                # bond block ended (hit a different section)
                break
        r.mayer_bonds = bonds

    def _parse_scf_components(self, lines, r):
        _scan_scalars(lines, r, {
            "nuclear_repulsion_eh": (r"Nuclear Repulsion\s*:\s*(-?\d+\.\d+)", float),
            "electronic_energy_eh": (r"Electronic Energy\s*:\s*(-?\d+\.\d+)", float),
            "one_electron_eh": (r"One Electron Energy\s*:\s*(-?\d+\.\d+)", float),
            "two_electron_eh": (r"Two Electron Energy\s*:\s*(-?\d+\.\d+)", float),
            "potential_energy_eh": (r"Potential Energy\s*:\s*(-?\d+\.\d+)", float),
            "kinetic_energy_eh": (r"Kinetic Energy\s*:\s*(-?\d+\.\d+)", float),
            "virial_ratio": (r"Virial Ratio\s*:\s*(-?\d+\.\d+)", float),
        })

    def _parse_dipole(self, lines, r):
        _scan_scalars(lines, r, {
            "dipole_au": (r"Magnitude \(a\.u\.\)\s*:\s*(-?\d+\.\d+)", float),
            "dipole_debye": (r"Magnitude \(Debye\)\s*:\s*(-?\d+\.\d+)", float),
        })

    def _parse_rotational(self, lines, r):
        # cast splits the captured tail into the first three floats; a
        # malformed line raises ValueError, which _scan_scalars swallows
        # (keeping the previous value), matching the old try/except.
        first3 = lambda tail: [float(x) for x in tail.split()][:3]  # noqa: E731
        _scan_scalars(lines, r, {
            "rot_const_cm": (r"Rotational constants in cm-1:\s*(.+)", first3),
            "rot_const_mhz": (r"Rotational constants in MHz\s*:\s*(.+)", first3),
        })

    def _parse_frequencies(self, lines, r):
        idxs = _find_all(lines, "VIBRATIONAL FREQUENCIES")
        if not idxs:
            return
        r.has_frequencies = True
        start = idxs[-1]
        freqs: list[float] = []
        n_imag = 0
        for ln in lines[start:]:
            m = re.match(r"\s*\d+:\s*(-?\d+\.\d+)\s*cm\*\*-1", ln)
            if m:
                val = float(m.group(1))
                if "imaginary" in ln.lower():
                    n_imag += 1
                    freqs.append(val)
                elif abs(val) > 1e-6:
                    freqs.append(val)
            elif "NORMAL MODES" in ln and freqs:
                break
        n_imag = max(n_imag, sum(1 for f in freqs if f < 0))
        r.frequencies = freqs
        r.n_imaginary = n_imag

    def _parse_thermochemistry(self, lines, r):
        _scan_scalars(lines, r, {
            "zpe_eh": (r"Zero point energy\s+\.*\s*(-?\d+\.\d+)\s*Eh", float),
            "total_thermal_eh": (r"Total thermal energy\s+\.*\s*(-?\d+\.\d+)\s*Eh", float),
            "gibbs_eh": (r"Final Gibbs free energy\s+\.*\s*(-?\d+\.\d+)\s*Eh", float),
            "g_minus_e_el_eh": (r"G-E\(el\)\s+\.*\s*(-?\d+\.\d+)\s*Eh", float),
            "enthalpy_eh": (r"Total Enthalpy\s+\.*\s*(-?\d+\.\d+)\s*Eh", float),
            "entropy_term_eh": (r"Final entropy term\s+\.*\s*(-?\d+\.\d+)\s*Eh", float),
            "temperature_k": (r"Temperature\s+\.*\s*(-?\d+\.\d+)\s*K\b", float),
            "pressure_atm": (r"Pressure\s+\.*\s*(-?\d+\.\d+)\s*atm", float),
        })

    def _parse_tddft(self, lines, r):
        idxs = _find_all(lines, "ABSORPTION SPECTRUM VIA TRANSITION ELECTRIC DIPOLE")
        if not idxs:
            return
        r.has_tddft = True
        start = idxs[-1]
        trans: list[Transition] = []
        # "0-1A  ->  2-3A": the digit before the letter is the excited state's
        # spin multiplicity, and it must be kept. With `triplets true` ORCA
        # prints singlets and triplets in ONE energy-sorted table, so the state
        # number restarts per multiplicity — discarding it gave two rows called
        # "state 2" and a state count of twice the root count.
        row = re.compile(
            r"->\s*(\d+)-(\d+)\S*\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)"
        )
        for ln in lines[start:start + 400]:
            m = row.search(ln)
            if m:
                trans.append(Transition(
                    state=int(m.group(1)),
                    mult=int(m.group(2)),
                    energy_ev=float(m.group(3)),
                    energy_cm=float(m.group(4)),
                    wavelength_nm=float(m.group(5)),
                    fosc=float(m.group(6)),
                ))
            elif trans and ln.strip() == "":
                # rows are contiguous (verified on real ORCA 6.x output), so
                # the first blank after any row ends the table. Breaking only
                # when >1 row was parsed let a single-root run (nroots 1) fall
                # through into the VELOCITY dipole table right below — whose
                # rows match the same regex — double-counting state 1 with the
                # velocity-gauge fosc.
                break
        r.transitions = trans

    def _parse_tddft_states(self, lines, r):
        """Excited-state composition from the 'TD-DFT/TDA EXCITED STATES' block:
        each STATE lists the dominant occupied->virtual orbital pairs and their
        weights.

        The marker match is CASE-SENSITIVE on purpose: every freq
        thermochemistry section prints "(2) There are no thermally accessible
        electronically excited states" (observed in 62 real non-TDDFT outputs
        in the corpus), which a case-folded match treats as the section header
        - and combined with last-wins it would land AFTER the real block in a
        TD-DFT+Freq run and miss the states. The LAST occurrence wins, per the
        parser-wide rule (a recurring section's final block is the definitive
        one). Validated against 9 real TD-DFT .out files (ORCA 6.x): the
        uppercase header appears exactly once per file, so first-vs-last is
        identical on this corpus; a genuinely recurring block remains
        unvalidated."""
        starts = [i for i, ln in enumerate(lines)
                  if "EXCITED STATES" in ln]
        if not starts:
            return
        # With triplets requested ORCA prints TWO blocks — "... EXCITED STATES
        # (SINGLETS)" then "(TRIPLETS)" — and plain last-wins would keep only
        # the triplet compositions while the absorption table above pairs with
        # the SINGLET states. Prefer the last SINGLETS-tagged block when one
        # exists; untagged headers (every corpus file) keep exact last-wins.
        singlet_starts = [i for i in starts if "SINGLET" in lines[i].upper()]
        start = singlet_starts[-1] if singlet_starts else starts[-1]
        # bound the scan at the NEXT excited-states header (the TRIPLETS block)
        # so the singlet scan can't run into it and mix the two state lists
        following = [i for i in starts if i > start]
        end = min(following[0] if following else len(lines), start + 4000)
        state_re = re.compile(
            r"STATE\s+(\d+):\s*E=\s*[\d.]+\s*au\s+([\d.]+)\s*eV")
        contrib_re = re.compile(
            r"(\d+[ab]?)\s*->\s*(\d+[ab]?)\s*:\s*([\d.]+)")
        states: list = []
        cur = None
        for ln in lines[start:end]:
            ms = state_re.search(ln)
            if ms:
                cur = {"state": int(ms.group(1)),
                       "ev": float(ms.group(2)), "contributions": []}
                states.append(cur)
                continue
            if cur is not None:
                mc = contrib_re.search(ln)
                if mc:
                    cur["contributions"].append(
                        (mc.group(1), mc.group(2), float(mc.group(3))))
        r.tddft_states = [s for s in states if s["contributions"]]

    # -- NMR shielding --
    def _parse_nmr(self, lines, r):
        idxs = _find_all(lines, "CHEMICAL SHIELDING SUMMARY")
        if not idxs:
            return
        start = idxs[-1]
        # find the "Nucleus  Element  Isotropic  Anisotropy" header, then the
        # dashed separator; data rows follow until a blank line
        header = _find_header(lines, start, 8,
                              lambda ln: "Nucleus" in ln and "Isotropic" in ln)
        if header is None:
            return
        # data begins after the dashed line following the header
        data_start = header + 1
        if data_start < len(lines) and set(lines[data_start].strip()) <= {"-", " "}:
            data_start += 1
        shieldings: list[tuple[int, str, float, float]] = _scan_rows(
            lines, data_start,
            re.compile(r"(\d+)\s+([A-Za-z]{1,3})\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)"),
            lambda m: (int(m.group(1)), m.group(2),
                       float(m.group(3)), float(m.group(4))))
        if shieldings:
            r.has_nmr = True
            r.nmr_shieldings = shieldings

    def _parse_neb_path(self, lines, r):
        # Parse the "PATH SUMMARY FOR NEB-TS" table that ORCA prints after a
        # NEB-TS run. Each row is an image along the minimum-energy path:
        #   Image   E(Eh)        dE(kcal/mol)  max(|Fp|)  RMS(Fp)
        #      0  -1626.95773     0.00         0.05123    0.00546
        #      5  -1626.94303     9.23         ...        <= CI
        #     TS  -1626.94483     8.10         ...        <= TS
        # The label is an integer image index or "TS"; we keep label, absolute
        # energy, relative dE (kcal/mol), and whether the row is the TS.
        # ORCA's "IRC PATH SUMMARY" table has the same row shape and matches
        # the same header scan; record WHICH table matched so the Results tab
        # can title an IRC profile as an IRC, not a NEB-TS.
        start = None
        for i, ln in enumerate(lines):
            if "PATH SUMMARY" in ln.upper():
                start = i
        if start is None:
            return
        path_kind = "irc" if "IRC" in lines[start].upper() else "neb"
        path = []
        # Column layout varies by table: the plain NEB/NEB-CI "PATH SUMMARY"
        # (also printed during every NEB-TS run) has an extra Dist.(Ang.)
        # column before E(Eh) — "Image  Dist.(Ang.)  E(Eh)  dE(kcal/mol)" —
        # while the final NEB-TS table and the IRC table go straight from the
        # label to E(Eh). Read the position of E(Eh) from the header row so a
        # distance is never mistaken for an energy.
        e_idx = 1
        for ln in lines[start: start + 8]:
            toks = ln.split()
            for j, tok in enumerate(toks):
                if "E(Eh)" in tok:
                    e_idx = j
                    break
            else:
                continue
            break
        # scan forward from the header; rows look like "<label> <float> <float> ...".
        # The blank line after the table is the real terminator — the slice cap
        # only guards a malformed file. It must comfortably exceed the longest
        # real table: a both-direction IRC prints one row per step and easily
        # passes 200 rows, which the old cap of 200 silently truncated.
        for ln in lines[start: start + 20000]:
            s = ln.strip()
            if not s:
                # stop on a blank line only after we've collected some rows
                if path:
                    break
                continue
            parts = s.split()
            if len(parts) < e_idx + 2:
                continue
            label = parts[0]
            is_ts = label.upper() == "TS"
            if not (is_ts or label.lstrip("-").isdigit()):
                continue  # skip header / non-data lines
            try:
                e_eh = float(parts[e_idx])
                de_kcal = float(parts[e_idx + 1])
            except (ValueError, IndexError):
                continue
            # a row may be flagged with "<= CI" / "<= TS"
            if "<= TS" in s:
                is_ts = True
            path.append({"label": label, "e_eh": e_eh,
                         "de_kcal": de_kcal, "is_ts": is_ts})
        if path:
            r.has_neb_path = True
            r.neb_path = path
            r.neb_path_kind = path_kind


def parse_file(path: str) -> ParseResult:
    return OrcaOutParser(path).parse()


# ORCA echoes the multiplicity in its input summary, near the top of the run.
_MULT_RE = re.compile(r"Multiplicity\s+Mult\s+\.+\s*(\d+)")
_MULT_SCAN_LINES = 4000


def read_multiplicity(path) -> Optional[int]:
    """The spin multiplicity of a finished run, read from its ``.out`` alone.

    A cheap head-scan rather than a full :func:`parse_file`: the caller (the
    orbital viewer deciding whether to offer a *spin density*) needs one integer,
    and re-parsing a multi-megabyte output for it would be absurd. ORCA prints
    the line in its input summary, so the scan stops early — and stops
    unconditionally after ``_MULT_SCAN_LINES`` so a file that never contains it
    costs a bounded read, not the whole file.

    Returns None when the file is unreadable or the line is absent — "unknown",
    which the caller must treat as *not known to be open-shell* rather than as
    a closed shell it can assert (P2).
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= _MULT_SCAN_LINES:
                    break
                m = _MULT_RE.search(line)
                if m:
                    return int(m.group(1))
    except OSError:
        return None
    return None