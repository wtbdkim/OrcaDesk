"""
ORCA .inp generation.

Derived from the original NoClaudeProject pipeline templates, refactored into
composable per-step configs. Adds:

* charge / multiplicity as explicit fields (was hard-coded "0 1")
* implicit-solvation support (CPCM / SMD) wired to solvents.json choices
* a single ``build_input`` entry point shared by all step types

Each step produces a complete .inp text given an XYZ coordinate block.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


# ---- defaults (match the user's established workflow) -------------------
DEFAULT_FUNCTIONAL = "wB97X-D4"
DEFAULT_BASIS_SET = "def2-TZVP"
DEFAULT_RI = "RIJCOSX"
DEFAULT_AUX = "def2/J"


# ORCA's simple-input parser is strict about a few functional names: it spells
# some Minnesota functionals WITHOUT the hyphen that common usage / the picker
# list uses, and rejects the hyphenated form outright. Map the user-facing name
# to ORCA's accepted keyword. Keys are matched case-insensitively on the whole
# token. Only names whose ORCA form genuinely differs AND is accepted are listed
# (verified against ORCA 6.1.1); valid hyphenated keywords such as CAM-B3LYP,
# wB97X-D3, r2SCAN-3c, B97-D3 or LC-BLYP are intentionally NOT here so they pass
# through untouched.
_FUNCTIONAL_ALIASES = {
    # Minnesota functionals: ORCA drops the hyphen and rejects the hyphenated form
    "m06-2x": "M062X",
    "m06-l": "M06L",
    # ORCA spells the SCAN meta-GGA "SCANfunc"; bare "SCAN" is rejected
    "scan": "SCANfunc",
    # Combined dispersion tokens (FUNC-D3): ORCA rejects them and wants the
    # dispersion as a SEPARATE keyword. We always emit D3BJ (Becke-Johnson
    # damping) — never the bare "-D3" — so D3(BJ) is never confused with
    # D3(zero). See the dispersion convention in CLAUDE.md.
    "blyp-d3": "BLYP D3BJ",   "blyp-d3bj": "BLYP D3BJ",
    "b3lyp-d3": "B3LYP D3BJ", "b3lyp-d3bj": "B3LYP D3BJ",
    "pbe0-d3": "PBE0 D3BJ",   "pbe0-d3bj": "PBE0 D3BJ",
    "b2plyp-d3": "B2PLYP D3BJ", "b2plyp-d3bj": "B2PLYP D3BJ",
}


def normalize_functional(name: str) -> str:
    """Return ORCA's accepted simple-input keyword for a functional name.

    ORCA's parser is strict: it rejects e.g. "M06-2X" (wants "M062X"), "SCAN"
    (wants "SCANfunc"), and combined dispersion tokens like "B3LYP-D3" (wants
    "B3LYP D3BJ"). Unknown / already-correct names are returned unchanged
    (trimmed), so the 'arbitrary value used verbatim' contract still holds for
    everything that isn't a known mismatch. Valid hyphenated keywords
    (CAM-B3LYP, wB97X-D3/-D4, r2SCAN-3c, B97-D3, LC-BLYP, ...) are not in the
    table and pass through untouched.
    """
    token = (name or "").strip()
    return _FUNCTIONAL_ALIASES.get(token.lower(), token)


# Double-hybrid functionals: their MP2 correlation part needs a correlation-
# fitting ("/C") auxiliary basis. Verified to run in ORCA 6.1.1 (the others
# from the old list — B2PLYP-D/-D3, revDSD/revDOD-PBEP86-D4, B2PLYP21,
# wB97M(2) — are rejected by this build and were dropped from the picker).
_DOUBLE_HYBRIDS = frozenset({
    "b2plyp", "b2gp-plyp", "mpw2plyp", "b2k-plyp", "b2t-plyp",
    "dsd-blyp", "dsd-pbep86", "dsd-pbeb95", "wb2plyp", "wb2gp-plyp",
    "pbe-qidh", "pbe0-dh", "pbe0-2",
    "pr2scan50", "pr2scan69", "wpr2scan50", "kpr2scan50",
})


def _needs_auxc(functional: str) -> bool:
    """True if the method needs a correlation-fitting (/C) aux basis: any MP2/MP3
    method or a double hybrid (the MP2-like correlation step needs AuxC)."""
    norm = normalize_functional(functional).lower()
    if "mp2" in norm or "mp3" in norm:
        return True
    base = norm.split()[0] if norm.strip() else ""   # strip a trailing " d3bj"
    return base in _DOUBLE_HYBRIDS


# ---- solvation ----------------------------------------------------------
@dataclass
class Solvation:
    """Implicit solvation. model is one of: '', 'CPCM', 'SMD'."""
    model: str = ""        # empty = gas phase
    solvent: str = "Water"

    def keyword(self) -> str:
        """Simple-input keyword fragment, e.g. 'CPCM(Water)'. Empty if gas phase."""
        if not self.model:
            return ""
        solv = (self.solvent or "").strip()
        if not solv:
            # CPCM alone = infinite dielectric (a valid ORCA shortcut);
            # SMD requires a named solvent, so without one we emit nothing.
            return "CPCM" if self.model.upper() == "CPCM" else ""
        return f"{self.model}({solv})"


# ---- per-step configuration ---------------------------------------------
@dataclass
class BasisAssignment:
    """Per-element basis/ECP assignment for the %basis block."""
    element: str = ""        # element symbol, e.g. "I", "Fe"
    basis: str = ""          # newgto basis, e.g. "def2-TZVP" (blank = skip)
    ecp: str = ""            # newecp, e.g. "def2-ECP" (blank = skip)


@dataclass
class StepConfig:
    """
    One ORCA calculation step.

    ``kind`` is a label ('opt' | 'ts_opt' | 'freq' | 'ts_freq' | 'tddft' |
    'nmr' | 'sp' | 'general') used by the queue to decide ordering and result
    handling. The actual ORCA behaviour is driven by the keyword fields, so
    custom combinations are possible.
    """
    kind: str = "opt"
    functional: str = DEFAULT_FUNCTIONAL
    basis_set: str = DEFAULT_BASIS_SET
    ri_approximation: str = DEFAULT_RI
    scf_convergence: str = "TightSCF"
    calculation_type: str = "TightOpt"   # e.g. TightOpt / OptTS / Freq / "" for SP
    options: str = ""                    # extra simple-input keywords (user-supplied)
    maxcore_mb: int = 2400
    nprocs: int = 6
    max_iter: int = 200                  # geometry optimiser cap
    solvation: Solvation = field(default_factory=Solvation)

    # per-element basis/ECP (%basis newgto/newecp), applies to any kind
    basis_assignments: list = field(default_factory=list)  # list[BasisAssignment]

    # frequency block (emitted only for freq / ts_freq when non-default)
    freq_temp_k: float = 298.15          # ORCA default temperature
    freq_pressure_atm: float = 1.0       # ORCA default pressure

    # NMR (emitted only for kind == 'nmr')
    nmr_jcoupling: bool = False          # add %eprnmr SSALL for J-couplings

    # TD-DFT block (only emitted when kind == 'tddft')
    tddft_nroots: int = 40
    tddft_maxdim: int = 10
    tddft_tda: bool = False
    tddft_triplets: bool = False
    # IRC (intrinsic reaction coordinate) — starts from a TS structure and
    # follows the path downhill both ways to verify which minima it connects.
    irc_maxiter: int = 100               # max IRC steps
    irc_direction: str = "both"          # both | forward | backward
    irc_init_hess: str = "calc_anfreq"   # calc_anfreq | calc_numfreq | read
    irc_hess_file: str = ""              # .hess to read when init_hess == "read"
    # NEB-TS (nudged elastic band -> transition state). The reactant goes in the
    # usual coordinate block; the PRODUCT geometry is written to a side .xyz file
    # that the %neb block points at. neb_product_xyz holds that product geometry.
    neb_product_xyz: str = ""            # product coordinates (atom order MUST match reactant)
    neb_nimages: int = 8                 # number of images along the band
    neb_preopt_ends: bool = False        # re-optimize endpoints before the band
    neb_ts_guess_xyz: str = ""           # optional TS guess geometry

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "StepConfig":
        data = dict(data)
        solv = data.pop("solvation", None)
        basis_list = data.pop("basis_assignments", None)
        cfg = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        if isinstance(solv, dict):
            cfg.solvation = Solvation(**{k: v for k, v in solv.items()
                                         if k in Solvation.__dataclass_fields__})
        if isinstance(basis_list, list):
            cfg.basis_assignments = [
                BasisAssignment(
                    element=str(b.get("element", "")).strip(),
                    basis=str(b.get("basis", "")).strip(),
                    ecp=str(b.get("ecp", "")).strip(),
                )
                for b in basis_list if isinstance(b, dict) and b.get("element", "").strip()
            ]
        # Coerce/clamp untrusted numeric fields. A phone-API payload reaches here
        # via calc_from_dict, and these values are interpolated verbatim into
        # %pal/%maxcore/%geom/%tddft — so a string (line injection) or an absurd
        # value (resource exhaustion) must not pass through.
        cfg.nprocs = _clamp_int(cfg.nprocs, 6, 1, 1024)
        cfg.maxcore_mb = _clamp_int(cfg.maxcore_mb, 2400, 64, 1_000_000)
        cfg.max_iter = _clamp_int(cfg.max_iter, 200, 1, 100_000)
        cfg.neb_nimages = _clamp_int(cfg.neb_nimages, 8, 2, 200)
        cfg.irc_maxiter = _clamp_int(cfg.irc_maxiter, 100, 1, 100_000)
        cfg.tddft_nroots = _clamp_int(cfg.tddft_nroots, 40, 1, 10_000)
        cfg.tddft_maxdim = _clamp_int(cfg.tddft_maxdim, 10, 1, 100_000)
        cfg.freq_temp_k = _clamp_float(cfg.freq_temp_k, 298.15, 0.0, 100_000.0)
        cfg.freq_pressure_atm = _clamp_float(cfg.freq_pressure_atm, 1.0, 0.0, 100_000.0)
        return cfg


def _clamp_int(v, default: int, lo: int, hi: int) -> int:
    """Coerce v to an int and clamp to [lo, hi]; fall back to default if not numeric."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(n, hi))


def _clamp_float(v, default: float, lo: float, hi: float) -> float:
    """Coerce v to a float and clamp to [lo, hi]; fall back to default if not numeric/NaN."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if x != x:  # NaN
        return default
    return max(lo, min(x, hi))


def _needs_auxj(ri: str) -> bool:
    """True if the RI approximation needs a Coulomb-fitting (/J) aux basis."""
    r = (ri or "").upper().replace("-", "").replace("_", "")
    # RIJCOSX, RIJDX, RIJONX, RIJ, SPLITRIJ all fit Coulomb with /J.
    # (RIJK needs /JK, NoRI/NORI need nothing.)
    return any(tag in r for tag in ("RIJCOSX", "RIJDX", "RIJONX")) or r == "RIJ" or r == "SPLITRIJ"


def _ri_is_off(ri: str) -> bool:
    """True if the user turned RI off (no RI keyword / NoRI)."""
    r = (ri or "").strip().upper()
    return r == "" or r == "NORI"


def _auto_aux(cfg: "StepConfig") -> str:
    """Pick an auxiliary basis if the method needs one and the user didn't put
    an aux in extra options.

    - Double hybrids / MP2 need a correlation-fitting (/C) aux as well as the
      Coulomb (/J) one, so we add AutoAux (it generates both). The user can opt
      out by setting the RI approximation to NoRI, in which case ORCA runs the
      conventional (non-RI) path and we add nothing.
    - Plain hybrids/GGAs with an RI-J method just need /J; for def2 orbital
      bases that is the universal def2/J. Other bases are left to the user."""
    opts = (cfg.options or "").upper()
    if "/J" in opts or "AUTOAUX" in opts:
        return ""  # user already supplied an aux (or AutoAux)

    if _needs_auxc(cfg.functional):
        # double hybrid / MP2: /C (and /J) needed unless RI is off
        return "" if _ri_is_off(cfg.ri_approximation) else "AutoAux"

    basis = (cfg.basis_set or "").lower()
    ri = (cfg.ri_approximation or "").upper().replace("-", "").replace("_", "")
    if ri == "RIJK":
        # RIJK fits BOTH Coulomb and exchange, so it needs a /JK aux (not /J).
        return "def2/JK" if "def2" in basis else "AutoAux"
    if not _needs_auxj(cfg.ri_approximation):
        return ""
    if "def2" in basis:
        return DEFAULT_AUX  # def2/J
    return ""  # unknown orbital basis: don't guess, let the user decide


def _keyword_line(cfg: StepConfig) -> str:
    parts = [
        "!",
        normalize_functional(cfg.functional),
        cfg.basis_set,
        cfg.scf_convergence,
        cfg.ri_approximation,
    ]
    if cfg.calculation_type.strip():
        parts.append(cfg.calculation_type)
    aux = _auto_aux(cfg)
    if aux:
        parts.append(aux)
    if cfg.options.strip():
        parts.append(cfg.options)
    solv = cfg.solvation.keyword()
    if solv:
        parts.append(solv)
    # collapse multiple spaces
    return " ".join(p for p in parts if p).strip()


# Placeholder used in raw .inp text where the geometry will be injected at run
# time (for calculations whose coordinates come from a referenced calculation).
GEOMETRY_PLACEHOLDER = "{{GEOMETRY}}"


def _xyz_elements(xyz: str) -> list:
    """Element symbol of each atom line in an xyz coordinate block (no header)."""
    els = []
    for ln in xyz.strip().splitlines():
        parts = ln.split()
        if len(parts) >= 4:  # "El x y z"
            els.append(parts[0])
    return els


def check_neb_atom_order(reactant_xyz: str, product_xyz: str) -> dict:
    """Verify reactant and product are compatible for NEB-TS.

    NEB-TS requires atom i in the reactant to be the SAME atom (same element,
    same index) in the product. We can't *fix* the ordering automatically (which
    atom maps to which is a chemical decision, and bonds change in a reaction),
    but we can catch the common mistakes:

      - different atom counts
      - different element composition
      - same composition but wrong ORDER (and where it first diverges)

    Returns {"ok": bool, "error": str, "mismatch_index": int|None}. On success
    error is "" and mismatch_index is None.
    """
    r = _xyz_elements(reactant_xyz)
    p = _xyz_elements(product_xyz)
    if not r or not p:
        return {"ok": False, "error": "Could not read coordinates from reactant or product.", "mismatch_index": None}
    if len(r) != len(p):
        return {"ok": False,
                "error": f"Atom count differs: reactant has {len(r)}, product has {len(p)}. "
                         f"NEB-TS needs the same atoms in both.",
                "mismatch_index": None}
    # composition (multiset of elements) must match
    from collections import Counter
    if Counter(r) != Counter(p):
        rc = ", ".join(f"{el}{n}" for el, n in sorted(Counter(r).items()))
        pc = ", ".join(f"{el}{n}" for el, n in sorted(Counter(p).items()))
        return {"ok": False,
                "error": f"Element composition differs — reactant: {rc}; product: {pc}.",
                "mismatch_index": None}
    # same composition: check order, report first divergence
    for i, (a, b) in enumerate(zip(r, p)):
        if a != b:
            return {"ok": False,
                    "error": f"Atom order differs at atom #{i + 1}: reactant has {a}, product has {b}. "
                             f"The atom order must be identical in both structures "
                             f"(tip: build the product by copying the reactant and moving atoms, "
                             f"so the order is preserved).",
                    "mismatch_index": i}
    return {"ok": True, "error": "", "mismatch_index": None}


def build_input(cfg: StepConfig, xyz: str, charge: int = 0, multiplicity: int = 1) -> str:
    """Return the full text of an ORCA .inp file for this step."""
    lines = [_keyword_line(cfg)]
    lines.append(f"%maxcore {cfg.maxcore_mb}")
    lines.append(f"%pal nprocs {cfg.nprocs} end")

    # per-element basis / ECP assignments
    basis_lines = []
    for b in cfg.basis_assignments:
        el = getattr(b, "element", "") if not isinstance(b, dict) else b.get("element", "")
        bs = getattr(b, "basis", "") if not isinstance(b, dict) else b.get("basis", "")
        ecp = getattr(b, "ecp", "") if not isinstance(b, dict) else b.get("ecp", "")
        el, bs, ecp = el.strip(), bs.strip(), ecp.strip()
        if not el:
            continue
        if bs:
            basis_lines.append(f'  newgto {el} "{bs}" end')
        if ecp:
            basis_lines.append(f'  newecp {el} "{ecp}" end')
    if basis_lines:
        lines.append("%basis")
        lines.extend(basis_lines)
        lines.append("end")

    if cfg.kind in ("opt", "ts_opt", "opt_freq", "ts_opt_freq"):
        lines.append("%geom")
        lines.append(f"  MaxIter {cfg.max_iter}")
        if cfg.kind in ("ts_opt", "ts_opt_freq"):
            # TS optimization needs a good initial Hessian; the manual computes
            # an exact Hessian before the first step (%geom Calc_Hess true).
            lines.append("  Calc_Hess true")
        lines.append("end")

    if cfg.kind in ("freq", "ts_freq", "opt_freq", "ts_opt_freq"):
        # only emit the %freq block when temperature/pressure differ from the
        # ORCA defaults (298.15 K, 1.0 atm), matching the example convention
        if abs(cfg.freq_temp_k - 298.15) > 1e-6 or abs(cfg.freq_pressure_atm - 1.0) > 1e-6:
            lines.append("%freq")
            lines.append(f"  Temp {cfg.freq_temp_k}")
            lines.append(f"  Pressure {cfg.freq_pressure_atm}")
            lines.append("end")

    if cfg.kind == "tddft":
        lines.append("%tddft")
        lines.append(f"  nroots {cfg.tddft_nroots}")
        lines.append(f"  maxdim {cfg.tddft_maxdim}")
        lines.append(f"  tda {'true' if cfg.tddft_tda else 'false'}")
        lines.append(f"  triplets {'true' if cfg.tddft_triplets else 'false'}")
        lines.append("end")

    if cfg.kind == "irc":
        # IRC follows the reaction path downhill from a TS in both directions.
        # It needs an initial Hessian to know which way is "downhill" (the
        # imaginary mode): either compute one (calc_anfreq/calc_numfreq) or read
        # a .hess from a previous freq/TS run.
        lines.append("%irc")
        lines.append(f"  MaxIter {cfg.irc_maxiter}")
        direction = (cfg.irc_direction or "both").strip().lower()
        if direction in ("forward", "backward"):
            lines.append(f"  Direction {direction}")
        init = (cfg.irc_init_hess or "calc_anfreq").strip()
        if init == "read" and cfg.irc_hess_file.strip():
            lines.append("  InitHess read")
            lines.append(f'  Hess_Filename "{cfg.irc_hess_file.strip()}"')
        else:
            # calc_anfreq (analytic) or calc_numfreq (numerical)
            lines.append(f"  InitHess {init if init in ('calc_anfreq', 'calc_numfreq') else 'calc_anfreq'}")
        lines.append("end")

    if cfg.kind == "neb_ts":
        # NEB-TS finds the minimum-energy path between reactant (the coordinate
        # block below) and product, then refines the highest point to a TS.
        # The product geometry lives in a side file; we use the fixed name
        # "product.xyz" (each job runs in its own folder, so no clash). A TS
        # guess, if provided, goes in "ts_guess.xyz".
        lines.append("%neb")
        lines.append('  NEB_End_XYZFile "product.xyz"')
        lines.append(f"  Nimages {cfg.neb_nimages}")
        if cfg.neb_preopt_ends:
            lines.append("  PreOpt_Ends true")
        if cfg.neb_ts_guess_xyz.strip():
            lines.append('  NEB_TS_XYZFile "ts_guess.xyz"')
        lines.append("end")

    lines.append(f"* xyz {charge} {multiplicity}")
    lines.append(xyz.strip("\n"))
    lines.append("*")

    if cfg.kind == "nmr" and cfg.nmr_jcoupling:
        # Spin-spin (J) coupling request. ORCA requires this block AFTER the
        # coordinates (otherwise "all H" can't be resolved), and the correct
        # form is `Nuclei = all <El> { ssall }` — a bare "SSALL" is invalid and
        # aborts the run. Shieldings come from the `! NMR` keyword; here we add
        # couplings for the usual NMR-active light nuclei (H and C).
        lines.append("%eprnmr")
        lines.append("  Nuclei = all H { shift, ssall }")
        lines.append("  Nuclei = all C { shift, ssall }")
        lines.append("end")

    return "\n".join(lines) + "\n"


def build_input_template(cfg: StepConfig, charge: int, multiplicity: int,
                         use_placeholder: bool, xyz: str = "") -> str:
    """
    Build an .inp for editing in raw mode.

    When ``use_placeholder`` is True (geometry comes from a reference), the
    coordinate block is filled with GEOMETRY_PLACEHOLDER so the user can edit
    everything else and the engine substitutes real coordinates at run time.
    Otherwise the provided ``xyz`` is embedded directly.
    """
    geom = GEOMETRY_PLACEHOLDER if use_placeholder else xyz.strip("\n")
    return build_input(cfg, geom, charge, multiplicity)


def render_raw_input(raw_text: str, xyz: str) -> str:
    """
    Produce the final .inp text from a raw template, substituting the geometry
    placeholder with actual coordinates if present. If no placeholder exists,
    the raw text is used verbatim (coordinates were embedded directly).
    """
    if GEOMETRY_PLACEHOLDER in raw_text:
        return raw_text.replace(GEOMETRY_PLACEHOLDER, xyz.strip("\n"))
    return raw_text


# ---- convenient step presets (seed values for the GUI) ------------------
def preset_opt() -> StepConfig:
    return StepConfig(
        kind="opt", calculation_type="TightOpt", scf_convergence="TightSCF",
        maxcore_mb=2400, nprocs=6,
    )


def preset_freq() -> StepConfig:
    return StepConfig(
        kind="freq", calculation_type="Freq", scf_convergence="VeryTightSCF",
        maxcore_mb=4000, nprocs=6,
    )


def preset_ts_opt() -> StepConfig:
    return StepConfig(
        kind="ts_opt", calculation_type="OptTS", scf_convergence="TightSCF",
        maxcore_mb=2400, nprocs=6,
    )


def preset_ts_freq() -> StepConfig:
    return StepConfig(
        kind="ts_freq", calculation_type="Freq", scf_convergence="VeryTightSCF",
        maxcore_mb=4000, nprocs=6,
    )


def preset_irc() -> StepConfig:
    return StepConfig(
        kind="irc", calculation_type="IRC", scf_convergence="TightSCF",
        maxcore_mb=2400, nprocs=6,
    )


def preset_neb_ts() -> StepConfig:
    # FREQ is appended so the converged TS is verified (exactly one imaginary
    # mode). NEB-TS itself only needs gradients.
    return StepConfig(
        kind="neb_ts", calculation_type="NEB-TS", scf_convergence="TightSCF",
        options="FREQ", maxcore_mb=2400, nprocs=6,
    )


def preset_tddft() -> StepConfig:
    return StepConfig(
        kind="tddft", calculation_type="", scf_convergence="TightSCF",
        maxcore_mb=2400, nprocs=6,
    )


def preset_nmr() -> StepConfig:
    return StepConfig(
        kind="nmr", calculation_type="NMR", scf_convergence="TightSCF",
        maxcore_mb=2400, nprocs=6,
    )


def preset_general() -> StepConfig:
    return StepConfig(
        kind="general", calculation_type="", scf_convergence="TightSCF",
        maxcore_mb=2400, nprocs=6,
    )


def preset_sp() -> StepConfig:
    return StepConfig(
        kind="sp", calculation_type="SP", scf_convergence="TightSCF",
        maxcore_mb=2400, nprocs=6,
    )
