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

from ..core.parser import ParseResult, Atom


# 1 Hartree in eV (CODATA), to store MACE's eV energy in ParseResult.final_energy_eh.
_EV_PER_HARTREE = 27.211386245988


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
    # a single-point ("sp") task isn't an optimization: this gates validation
    # (no convergence requirement) and the Results tab's final-geometry section
    r.is_optimization = str(data.get("task", "opt")) != "sp"
    r.opt_converged = bool(data.get("converged"))

    e_ev = data.get("energy_ev")
    if e_ev is not None:
        try:
            r.final_energy_eh = float(e_ev) / _EV_PER_HARTREE
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
