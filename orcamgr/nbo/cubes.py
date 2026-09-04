"""
Natural bond orbitals as cube files -- the in-process twin of
:func:`orcamgr.core.plot.generate_cube`.

orca_plot draws canonical orbitals from the ``.gbw``; it cannot be handed a
vector of ours. So an NBO is drawn here instead: the orbital's NAO-basis
vector is carried back to the AO basis, evaluated on orca_plot's own box with
:mod:`orcamgr.nbo.grid`, and written as the same orbital-variant cube orca_plot
writes, under the same grid-qualified name convention (``{base}.nbo7a.g60.cube``
in ``cubes/``), so the viewer reads it through the one path it already has.

Reuse is stricter than for an orca_plot cube, because the orbital an index
names is not fixed by physics: it is the position the Lewis search assigned.
A file is served again only when it is newer than the ``.gbw`` **and** its
first line names the analysis format that wrote it -- an algorithm change bumps
that format, and a cube from the old one would otherwise be shown under the new
label. The check reads one line, so it costs nothing.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from ..core.plot import CubeRequest, NBO_KIND, cube_filename
from .analysis import CACHE_FORMAT, NboOrbitalSet
from .grid import CUBE_GENERATOR, write_orbital_cube
from .wavefunction import WavefunctionError

#: Line 1 of every NBO cube; the reuse check matches it exactly.
GENERATOR_LINE = f"{CUBE_GENERATOR} (NBO format {CACHE_FORMAT})"

_SPIN_TAG = {"": "", "alpha": " (alpha)", "beta": " (beta)"}


def is_current(path: str | Path, source_mtime: float) -> bool:
    """Is this cube from the current wavefunction and the current algorithm?"""
    p = Path(path)
    try:
        st = p.stat()
        if st.st_size <= 0 or (source_mtime and st.st_mtime < source_mtime):
            return False
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.readline().strip() == GENERATOR_LINE
    except OSError:
        return False


def cube_title(orbital, spin: str, elements: list) -> str:
    """Line 2 of the cube -- what the viewer captions the surface with:
    ``Natural bond orbital 7: BD (1) O1-H2, occupancy 1.9988``."""
    return (f"Natural bond orbital {orbital.index}{_SPIN_TAG.get(spin, '')}: "
            f"{orbital.label(elements)}, occupancy {orbital.occupancy:.4f}")


def generate_nbo_cube(orbital_set: NboOrbitalSet, base: str, req: CubeRequest, *,
                      dest_dir: str | Path, reuse: bool = True,
                      on_log: Optional[Callable[[str], None]] = None) -> dict:
    """Write (or reuse) the cube of one natural bond orbital.

    ``req.index`` is the orbital's position in its spin's NBO list and
    ``req.operator`` the spin (0 = alpha or closed shell, 1 = beta), the same
    addressing orca_plot uses for canonical orbitals. Returns ``{ok, path,
    cached, seconds, label}`` or ``{ok: False, error}`` -- errors are values,
    never exceptions, because the caller is a background thread whose only
    channel to the UI is a status dict (P6).
    """
    req = req.normalized()
    if req.kind != NBO_KIND:
        return {"ok": False, "error": f"{req.kind} is not a natural bond orbital request."}
    try:
        orbital = orbital_set.orbital(req.operator, req.index)
    except WavefunctionError as e:
        return {"ok": False, "error": str(e)}
    spin = orbital_set.basis(req.operator).spin
    elements = orbital_set.wavefunction.elements
    label = orbital.label(elements) + (f" ({spin})" if spin else "")
    dest = Path(dest_dir)
    final = dest / cube_filename(base, req)

    if reuse and is_current(final, orbital_set.source_mtime):
        return {"ok": True, "path": str(final), "cached": True, "seconds": 0.0,
                "label": label}

    if on_log:
        on_log(f"NBO {label}: evaluating on a {req.grid}³ grid …")
    started = time.time()
    try:
        # line 1 names the format, so is_current() can tell this cube apart
        # from one an earlier algorithm wrote under the same name
        write_orbital_cube(final, orbital_set.wavefunction,
                           orbital_set.ao_coefficients(req.operator, req.index),
                           req.grid, cube_title(orbital, spin, elements),
                           index=orbital.index, generator=GENERATOR_LINE)
    except WavefunctionError as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": f"Could not write the cube: {e}"}
    elapsed = time.time() - started
    if on_log:
        on_log(f"NBO {label}: wrote {final.name} "
               f"({final.stat().st_size / 1e6:.1f} MB) in {elapsed:.1f} s")
    return {"ok": True, "path": str(final), "cached": False, "seconds": elapsed,
            "label": label}
