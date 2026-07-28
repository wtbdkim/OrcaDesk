"""
Parse an MLIP run's JSON result into the shared ``ParseResult``.

The MACE worker (``runner.MACE_WORKER_SCRIPT``) writes a small JSON file with the
optimized geometry, final energy, and convergence flag. Reading it back into the
SAME ``ParseResult`` the ORCA parser produces is what lets a downstream ORCA
calc reference an MLIP-optimized geometry through the existing reference path
(``QueueEngine._resolve_geometry`` reads ``ref.result.geometry``), and lets the
Results tab show the MLIP energy/structure with no special-casing.
"""

from __future__ import annotations

import json
from pathlib import Path

# HARTREE_TO_EV (1 Hartree in eV, CODATA) converts MACE's eV energies into the
# Hartree fields of the shared ParseResult — single-sourced from the ORCA parser.
from ..core.parser import ParseResult, Atom, HARTREE_TO_EV


def parse_mlip_result(path: str) -> ParseResult:
    """Read the worker's result JSON into a ParseResult. A missing file, a read
    error, or a worker-reported error all yield ``terminated_normally = False``
    with an ``error_message``, so the engine/validation treat it as a failure."""
    # is_optimization is refined from the result's "task" once read (below); an
    # unreadable/old result defaults to optimization (the original mlip_opt kind).
    r = ParseResult(path=str(path), is_optimization=True)
    p = Path(path) if path else None
    if r.path:
        r.filename = Path(r.path).name

    if p is None or not p.exists():
        r.error_message = "MLIP run produced no result file (it may not have started)."
        return r
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as e:
        r.error_message = f"Could not read MLIP result: {e}"
        return r

    if data.get("error"):
        r.error_message = str(data["error"])
        return r   # terminated_normally stays False

    r.terminated_normally = True
    # only the opt tasks are optimizations: this gates the convergence check and
    # the Results tab's final-geometry section. sp / freq leave the geometry as
    # given, so they are not optimizations (opt_freq relaxes first, so it is).
    r.is_optimization = str(data.get("task", "opt")) in ("opt", "opt_freq")
    r.opt_converged = bool(data.get("converged"))

    e_ev = data.get("energy_ev")
    if e_ev is not None:
        try:
            r.final_energy_eh = float(e_ev) / HARTREE_TO_EV
        except (TypeError, ValueError):
            pass

    # Vibrational analysis + ideal-gas thermochemistry (freq / opt_freq tasks).
    # The worker reports frequencies in cm^-1 (negative = imaginary) and the
    # thermochemistry in eV; convert the energies to Hartree for the shared
    # ParseResult so the Results tab renders them exactly like an ORCA freq job.
    if data.get("has_frequencies"):
        r.has_frequencies = True
        freqs = []
        for v in (data.get("frequencies") or []):
            try:
                freqs.append(float(v))
            except (TypeError, ValueError):
                continue
        r.frequencies = freqs
        try:
            r.n_imaginary = int(data.get("n_imaginary") or 0)
        except (TypeError, ValueError):
            r.n_imaginary = sum(1 for f in freqs if f < 0)
        for jkey, attr in (("zpe_ev", "zpe_eh"), ("gibbs_ev", "gibbs_eh"),
                           ("enthalpy_ev", "enthalpy_eh"),
                           ("entropy_term_ev", "entropy_term_eh"),
                           ("internal_energy_ev", "total_thermal_eh")):
            val = data.get(jkey)
            if val is not None:
                try:
                    setattr(r, attr, float(val) / HARTREE_TO_EV)
                except (TypeError, ValueError):
                    pass
        for jkey, attr in (("temperature_k", "temperature_k"),
                           ("pressure_atm", "pressure_atm")):
            val = data.get(jkey)
            if val is not None:
                try:
                    setattr(r, attr, float(val))
                except (TypeError, ValueError):
                    pass

    geom = []
    for row in (data.get("geometry") or []):
        try:
            sym, x, y, z = row[0], float(row[1]), float(row[2]), float(row[3])
            geom.append(Atom(symbol=str(sym), x=x, y=y, z=z))
        except (TypeError, ValueError, IndexError):
            continue
    r.geometry = geom
    r.n_atoms = len(geom)
    if not geom:
        # terminated without a usable structure — treat as a failure so a
        # downstream reference doesn't silently get an empty geometry.
        r.terminated_normally = False
        r.error_message = r.error_message or "MLIP run returned no geometry."
    return r
