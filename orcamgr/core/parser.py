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
from dataclasses import dataclass, field, asdict
from typing import Optional


# ---- physical constants -------------------------------------------------
HARTREE_TO_EV = 27.211386245988


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


@dataclass
class Transition:
    """A single TD-DFT electronic transition."""
    state: int
    energy_ev: float
    energy_cm: float
    wavelength_nm: float
    fosc: float


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

    # --- orbitals ---
    orbitals: list[Orbital] = field(default_factory=list)
    homo_index: Optional[int] = None
    lumo_index: Optional[int] = None
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

    # --- NEB-TS reaction path (PATH SUMMARY table) ---
    has_neb_path: bool = False
    # list of dicts: {label (e.g. "0","TS","9"), e_eh, de_kcal, is_ts}
    neb_path: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def shows_electronic_props(self) -> bool:
        """Whether to surface general electronic-structure properties (orbitals /
        HOMO-LUMO, atomic charges, Mayer bonds, dipole, rotational constants, SCF
        energy decomposition). ORCA prints these for every SCF job, but they are
        only shown as results for single-point and optimization runs; specialty
        runs (freq / tddft / nmr / neb) show only their specialty result."""
        specialty = (self.has_frequencies or self.has_tddft
                     or self.has_nmr or self.has_neb_path)
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

        add("File", self.filename)
        add("ORCA version", self.orca_version or "?")
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
        if self.final_energy_eh is not None:
            add("Final SP energy", f"{self.final_energy_eh:.8f} Eh")
        if self.dispersion_correction_eh is not None:
            add("Dispersion corr.", f"{self.dispersion_correction_eh:.8f} Eh")
        # SCF energy decomposition (electronic)
        if self.nuclear_repulsion_eh is not None:
            add("Nuclear repulsion", f"{self.nuclear_repulsion_eh:.6f} Eh", "elec")
        if self.electronic_energy_eh is not None:
            add("Electronic energy", f"{self.electronic_energy_eh:.6f} Eh", "elec")
        if self.one_electron_eh is not None:
            add("One-electron energy", f"{self.one_electron_eh:.6f} Eh", "elec")
        if self.two_electron_eh is not None:
            add("Two-electron energy", f"{self.two_electron_eh:.6f} Eh", "elec")
        if self.kinetic_energy_eh is not None:
            add("Kinetic energy", f"{self.kinetic_energy_eh:.6f} Eh", "elec")
        if self.potential_energy_eh is not None:
            add("Potential energy", f"{self.potential_energy_eh:.6f} Eh", "elec")
        if self.virial_ratio is not None:
            add("Virial ratio", f"{self.virial_ratio:.5f}", "elec")
        if self.is_optimization:
            add("Optimization",
                "converged" if self.opt_converged else "NOT converged")
        # orbital / dipole / rotational props (electronic)
        if self.homo_ev is not None:
            add("HOMO", f"#{self.homo_index}  {self.homo_ev:.4f} eV", "elec")
        if self.lumo_ev is not None:
            add("LUMO", f"#{self.lumo_index}  {self.lumo_ev:.4f} eV", "elec")
        if self.gap_ev is not None:
            add("HOMO-LUMO gap", f"{self.gap_ev:.4f} eV", "elec")
        if self.dipole_debye is not None:
            add("Dipole moment", f"{self.dipole_debye:.4f} Debye", "elec")
        if self.rot_const_cm:
            add("Rotational const.",
                " / ".join(f"{c:.4f}" for c in self.rot_const_cm) + " cm⁻¹", "elec")
        if self.has_frequencies:
            add("Frequencies", f"{len(self.frequencies)} modes")
            add("Imaginary modes", str(self.n_imaginary))
            if self.gibbs_eh is not None:
                add("Final Gibbs G", f"{self.gibbs_eh:.8f} Eh")
            if self.total_thermal_eh is not None:
                add("Inner energy U", f"{self.total_thermal_eh:.8f} Eh")
            if self.enthalpy_eh is not None:
                add("Enthalpy H", f"{self.enthalpy_eh:.8f} Eh")
            if self.entropy_term_eh is not None:
                add("Entropy term T·S", f"{self.entropy_term_eh:.8f} Eh")
            if self.zpe_eh is not None:
                add("ZPE", f"{self.zpe_eh:.8f} Eh")
            if self.g_minus_e_el_eh is not None:
                add("G - E(el)", f"{self.g_minus_e_el_eh:.8f} Eh")
            if self.temperature_k is not None:
                add("Temperature", f"{self.temperature_k:.2f} K")
            if self.pressure_atm is not None:
                add("Pressure", f"{self.pressure_atm:.2f} atm")
        if self.has_tddft:
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
        with open(self.path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        lines = _clean_lines(text)

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
        for ln in lines:
            m = re.search(r"Total Charge\s+Charge\s+\.+\s*(-?\d+)", ln)
            if m:
                r.charge = int(m.group(1))
            m = re.search(r"Multiplicity\s+Mult\s+\.+\s*(\d+)", ln)
            if m:
                r.multiplicity = int(m.group(1))
            m = re.search(r"Number of Electrons\s+NEL\s+\.+\s*(\d+)", ln)
            if m:
                r.n_electrons = int(m.group(1))

    def _parse_final_energy(self, lines, r):
        vals = []
        for ln in lines:
            m = re.search(r"FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)", ln)
            if m:
                vals.append(float(m.group(1)))
        if vals:
            r.final_energy_eh = vals[-1]

    def _parse_dispersion(self, lines, r):
        vals = []
        for ln in lines:
            m = re.match(r"\s*Dispersion correction\s+(-?\d+\.\d+)\s*$", ln)
            if m:
                vals.append(float(m.group(1)))
        if vals:
            r.dispersion_correction_eh = vals[-1]

    def _parse_optimization(self, lines, r):
        kw = r.input_keywords.lower()
        r.is_optimization = ("opt" in kw)
        r.opt_converged = any("THE OPTIMIZATION HAS CONVERGED" in ln for ln in lines)

    def _parse_geometry(self, lines, r):
        idxs = _find_all(lines, "CARTESIAN COORDINATES (ANGSTROEM)")
        if not idxs:
            return
        start = idxs[-1] + 2
        atoms: list[Atom] = []
        for ln in lines[start:]:
            s = ln.strip()
            if not s:
                break
            m = re.match(r"([A-Za-z]{1,3})\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)", s)
            if not m:
                break
            atoms.append(Atom(m.group(1), float(m.group(2)),
                              float(m.group(3)), float(m.group(4))))
        r.geometry = atoms
        r.n_atoms = len(atoms)

    def _parse_orbitals(self, lines, r):
        idxs = _find_all(lines, "ORBITAL ENERGIES")
        if not idxs:
            return
        start = idxs[-1]
        header = None
        for i in range(start, min(start + 8, len(lines))):
            if "OCC" in lines[i] and "E(eV)" in lines[i]:
                header = i
                break
        if header is None:
            return
        orbs: list[Orbital] = []
        for ln in lines[header + 1:]:
            s = ln.strip()
            if not s:
                break
            m = re.match(r"(\d+)\s+(\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)", s)
            if not m:
                break
            orbs.append(Orbital(int(m.group(1)), float(m.group(2)),
                                float(m.group(3)), float(m.group(4))))
        if not orbs:
            return
        r.orbitals = orbs
        homo = None
        for o in orbs:
            if o.occ > 0.01:
                homo = o
        if homo is not None:
            r.homo_index = homo.index
            r.homo_ev = homo.energy_ev
            lumo = next((o for o in orbs if o.index == homo.index + 1), None)
            if lumo is not None:
                r.lumo_index = lumo.index
                r.lumo_ev = lumo.energy_ev
                r.gap_ev = lumo.energy_ev - homo.energy_ev

    def _parse_mulliken(self, lines, r):
        idxs = _find_all(lines, "MULLIKEN ATOMIC CHARGES")
        if not idxs:
            return
        start = idxs[-1] + 2
        charges: list[tuple[str, float]] = []
        for ln in lines[start:]:
            s = ln.strip()
            if s.startswith("Sum of atomic charges") or not s:
                break
            m = re.match(r"\d+\s+([A-Za-z]{1,3})\s*:\s*(-?\d+\.\d+)", s)
            if not m:
                break
            charges.append((m.group(1), float(m.group(2))))
        r.mulliken_charges = charges

    def _parse_loewdin(self, lines, r):
        idxs = _find_all(lines, "LOEWDIN ATOMIC CHARGES")
        if not idxs:
            return
        start = idxs[-1] + 2
        charges: list[tuple[str, float]] = []
        for ln in lines[start:]:
            s = ln.strip()
            if s.startswith("Sum of atomic charges") or not s:
                break
            m = re.match(r"\d+\s+([A-Za-z]{1,3})\s*:\s*(-?\d+\.\d+)", s)
            if not m:
                break
            charges.append((m.group(1), float(m.group(2))))
        r.loewdin_charges = charges

    def _parse_mayer(self, lines, r):
        idxs = _find_all(lines, "MAYER POPULATION ANALYSIS")
        if not idxs:
            return
        start = idxs[-1]
        # per-atom table: header "ATOM  NA  ZA  QA  VA  BVA  FA", then rows
        head = None
        for i in range(start, min(start + 20, len(lines))):
            if lines[i].strip().startswith("ATOM") and "VA" in lines[i]:
                head = i
                break
        vals: list = []
        scan_from = start
        if head is not None:
            scan_from = head + 1
            for ln in lines[head + 1:]:
                s = ln.strip()
                if not s:
                    break
                m = re.match(
                    r"(\d+)\s+([A-Za-z]{1,3})\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+"
                    r"(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)", s)
                if not m:
                    break
                # groups: idx, el, NA, ZA, QA, VA, BVA, FA  -> keep VA (group 6)
                vals.append((int(m.group(1)), m.group(2), float(m.group(6))))
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
        pats = {
            "nuclear_repulsion_eh": r"Nuclear Repulsion\s*:\s*(-?\d+\.\d+)",
            "electronic_energy_eh": r"Electronic Energy\s*:\s*(-?\d+\.\d+)",
            "one_electron_eh": r"One Electron Energy\s*:\s*(-?\d+\.\d+)",
            "two_electron_eh": r"Two Electron Energy\s*:\s*(-?\d+\.\d+)",
            "potential_energy_eh": r"Potential Energy\s*:\s*(-?\d+\.\d+)",
            "kinetic_energy_eh": r"Kinetic Energy\s*:\s*(-?\d+\.\d+)",
            "virial_ratio": r"Virial Ratio\s*:\s*(-?\d+\.\d+)",
        }
        for ln in lines:
            for attr, pat in pats.items():
                m = re.search(pat, ln)
                if m:
                    setattr(r, attr, float(m.group(1)))

    def _parse_dipole(self, lines, r):
        for ln in lines:
            m = re.search(r"Magnitude \(a\.u\.\)\s*:\s*(-?\d+\.\d+)", ln)
            if m:
                r.dipole_au = float(m.group(1))
            m = re.search(r"Magnitude \(Debye\)\s*:\s*(-?\d+\.\d+)", ln)
            if m:
                r.dipole_debye = float(m.group(1))

    def _parse_rotational(self, lines, r):
        for ln in lines:
            m = re.search(r"Rotational constants in cm-1:\s*(.+)", ln)
            if m:
                try:
                    r.rot_const_cm = [float(x) for x in m.group(1).split()][:3]
                except ValueError:
                    pass
            m = re.search(r"Rotational constants in MHz\s*:\s*(.+)", ln)
            if m:
                try:
                    r.rot_const_mhz = [float(x) for x in m.group(1).split()][:3]
                except ValueError:
                    pass

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
        for ln in lines:
            m = re.search(r"Zero point energy\s+\.*\s*(-?\d+\.\d+)\s*Eh", ln)
            if m:
                r.zpe_eh = float(m.group(1))
            m = re.search(r"Total thermal energy\s+\.*\s*(-?\d+\.\d+)\s*Eh", ln)
            if m:
                r.total_thermal_eh = float(m.group(1))
            m = re.search(r"Final Gibbs free energy\s+\.*\s*(-?\d+\.\d+)\s*Eh", ln)
            if m:
                r.gibbs_eh = float(m.group(1))
            m = re.search(r"G-E\(el\)\s+\.*\s*(-?\d+\.\d+)\s*Eh", ln)
            if m:
                r.g_minus_e_el_eh = float(m.group(1))
            m = re.search(r"Total Enthalpy\s+\.*\s*(-?\d+\.\d+)\s*Eh", ln)
            if m:
                r.enthalpy_eh = float(m.group(1))
            m = re.search(r"Final entropy term\s+\.*\s*(-?\d+\.\d+)\s*Eh", ln)
            if m:
                r.entropy_term_eh = float(m.group(1))
            m = re.search(r"Temperature\s+\.*\s*(-?\d+\.\d+)\s*K\b", ln)
            if m:
                r.temperature_k = float(m.group(1))
            m = re.search(r"Pressure\s+\.*\s*(-?\d+\.\d+)\s*atm", ln)
            if m:
                r.pressure_atm = float(m.group(1))

    def _parse_tddft(self, lines, r):
        idxs = _find_all(lines, "ABSORPTION SPECTRUM VIA TRANSITION ELECTRIC DIPOLE")
        if not idxs:
            return
        r.has_tddft = True
        start = idxs[-1]
        trans: list[Transition] = []
        row = re.compile(
            r"->\s*(\d+)-\S+\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)"
        )
        for ln in lines[start:start + 400]:
            m = row.search(ln)
            if m:
                trans.append(Transition(
                    state=int(m.group(1)),
                    energy_ev=float(m.group(2)),
                    energy_cm=float(m.group(3)),
                    wavelength_nm=float(m.group(4)),
                    fosc=float(m.group(5)),
                ))
            elif trans and ln.strip() == "":
                if len(trans) > 1:
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
        start = starts[-1]
        state_re = re.compile(
            r"STATE\s+(\d+):\s*E=\s*[\d.]+\s*au\s+([\d.]+)\s*eV")
        contrib_re = re.compile(
            r"(\d+[ab]?)\s*->\s*(\d+[ab]?)\s*:\s*([\d.]+)")
        states: list = []
        cur = None
        for ln in lines[start:start + 4000]:
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
        header = None
        for i in range(start, min(start + 8, len(lines))):
            if "Nucleus" in lines[i] and "Isotropic" in lines[i]:
                header = i
                break
        if header is None:
            return
        # data begins after the dashed line following the header
        data_start = header + 1
        if data_start < len(lines) and set(lines[data_start].strip()) <= {"-", " "}:
            data_start += 1
        shieldings: list[tuple[int, str, float, float]] = []
        for ln in lines[data_start:]:
            s = ln.strip()
            if not s:
                break
            m = re.match(r"(\d+)\s+([A-Za-z]{1,3})\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)", s)
            if not m:
                break
            shieldings.append((int(m.group(1)), m.group(2),
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
        start = None
        for i, ln in enumerate(lines):
            if "PATH SUMMARY" in ln.upper():
                start = i
        if start is None:
            return
        path = []
        # scan forward from the header; rows look like "<label> <float> <float> ..."
        for ln in lines[start: start + 200]:
            s = ln.strip()
            if not s:
                # stop on a blank line only after we've collected some rows
                if path:
                    break
                continue
            parts = s.split()
            if len(parts) < 3:
                continue
            label = parts[0]
            is_ts = label.upper() == "TS"
            if not (is_ts or label.lstrip("-").isdigit()):
                continue  # skip header / non-data lines
            try:
                e_eh = float(parts[1])
                de_kcal = float(parts[2])
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


def parse_file(path: str) -> ParseResult:
    return OrcaOutParser(path).parse()