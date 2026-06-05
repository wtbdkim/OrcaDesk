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

    # --- frequencies / thermochemistry ---
    has_frequencies: bool = False
    frequencies: list[float] = field(default_factory=list)
    n_imaginary: int = 0
    zpe_eh: Optional[float] = None
    total_thermal_eh: Optional[float] = None
    gibbs_eh: Optional[float] = None
    g_minus_e_el_eh: Optional[float] = None

    # --- TD-DFT ---
    has_tddft: bool = False
    transitions: list[Transition] = field(default_factory=list)

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

    def summary_rows(self) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        rows.append(("File", self.filename))
        rows.append(("ORCA version", self.orca_version or "?"))
        status = "Normal" if self.terminated_normally else "ABNORMAL / incomplete"
        rows.append(("Termination", status))
        if self.error_message:
            rows.append(("Error", self.error_message))
        if self.run_time_string:
            rows.append(("Run time", self.run_time_string))
        if self.charge is not None:
            rows.append(("Charge / Mult", f"{self.charge} / {self.multiplicity}"))
        if self.n_atoms:
            rows.append(("Atoms", str(self.n_atoms)))
        if self.final_energy_eh is not None:
            rows.append(("Final SP energy", f"{self.final_energy_eh:.8f} Eh"))
        if self.is_optimization:
            rows.append(("Optimization", "converged" if self.opt_converged else "NOT converged"))
        if self.homo_ev is not None:
            rows.append(("HOMO", f"#{self.homo_index}  {self.homo_ev:.4f} eV"))
        if self.lumo_ev is not None:
            rows.append(("LUMO", f"#{self.lumo_index}  {self.lumo_ev:.4f} eV"))
        if self.gap_ev is not None:
            rows.append(("HOMO-LUMO gap", f"{self.gap_ev:.4f} eV"))
        if self.has_frequencies:
            rows.append(("Frequencies", f"{len(self.frequencies)} modes"))
            rows.append(("Imaginary modes", str(self.n_imaginary)))
            if self.gibbs_eh is not None:
                rows.append(("Final Gibbs G", f"{self.gibbs_eh:.8f} Eh"))
            if self.zpe_eh is not None:
                rows.append(("ZPE", f"{self.zpe_eh:.8f} Eh"))
        if self.has_tddft:
            rows.append(("TD-DFT states", str(len(self.transitions))))
            bright = self.brightest_transition()
            if bright:
                rows.append(("Brightest", f"{bright.wavelength_nm:.1f} nm  (f={bright.fosc:.4f})"))
        if self.has_nmr:
            rows.append(("NMR nuclei", str(len(self.nmr_shieldings))))
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
        self._parse_frequencies(lines, r)
        self._parse_thermochemistry(lines, r)
        self._parse_tddft(lines, r)
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