"""
MDL MOL (V2000) writer for the 2D drawing -- the editable half of its output.

Its input is the drawing exactly as the front-end drew it
(``web/next/draw2d.js``): projected 2D coordinates in Å, the bond orders RDKit
perceived, and per bond the wedge (1) / hash (6) flag the out-of-plane rule
chose, with the bond's FIRST atom being the wedge's narrow end (the MOL
convention, which is why the front-end orders each bond anchor-first). Nothing
is re-projected here: one projection, the one on screen.

ChemDraw, ChemSketch, Avogadro and RDKit itself open the result. Hydrogens the
drawing hides are simply absent -- every one of those programs fills a neutral
atom's valence with implicit H -- while formal charges and radicals are written
as ``M  CHG`` / ``M  RAD`` so a charged or open-shell atom is not "repaired".
"""

from __future__ import annotations

import math
import time

# V2000's counts line has three digits per count.
MAX_ATOMS = 999
MAX_BONDS = 999
_STEREO = {0, 1, 6}           # none, wedge (up), hash (down)
# unpaired electrons -> the M  RAD code (1 singlet, 2 doublet, 3 triplet)
_RAD_CODE = {1: 2, 2: 3}


class MolfileError(ValueError):
    """The drawing handed in cannot be written -- the message says why."""


def _title(text) -> str:
    """One printable ASCII line, at most 80 characters (the format's header
    line); anything else is replaced rather than refused."""
    s = "".join(c if 32 <= ord(c) < 127 else "_" for c in str(text or ""))
    return s.strip()[:80]


def _clean(drawing) -> tuple:
    if not isinstance(drawing, dict):
        raise MolfileError("Nothing to write.")
    atoms, bonds = drawing.get("atoms"), drawing.get("bonds")
    if not isinstance(atoms, list) or not atoms:
        raise MolfileError("The drawing has no atoms.")
    if not isinstance(bonds, list):
        raise MolfileError("The drawing's bonds are unreadable.")
    if len(atoms) > MAX_ATOMS or len(bonds) > MAX_BONDS:
        raise MolfileError(f"A V2000 MOL file holds at most {MAX_ATOMS} atoms "
                           f"and {MAX_BONDS} bonds.")
    out_atoms = []
    for a in atoms:
        try:
            el = str(a["el"]).strip()
            x, y = float(a["x"]), float(a["y"])
            chg = int(a.get("charge") or 0)
            rad = int(a.get("radical") or 0)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise MolfileError("An atom in the drawing is unreadable.") from None
        if not (el.isalpha() and 1 <= len(el) <= 3):
            raise MolfileError(f"'{el}' is not an element symbol.")
        if not (math.isfinite(x) and math.isfinite(y)):
            raise MolfileError("An atom has a coordinate that is not a number.")
        if abs(x) >= 1e5 or abs(y) >= 1e5:
            raise MolfileError("A coordinate does not fit the MOL format.")
        if not -15 <= chg <= 15:
            raise MolfileError(f"A charge of {chg} does not fit the MOL format.")
        out_atoms.append((el[0].upper() + el[1:].lower(), x, y, chg, max(0, rad)))
    n = len(out_atoms)
    out_bonds = []
    for b in bonds:
        try:
            i, j = int(b["a"]), int(b["b"])
            order = int(b.get("order") or 1)
            stereo = int(b.get("stereo") or 0)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise MolfileError("A bond in the drawing is unreadable.") from None
        if not (0 <= i < n and 0 <= j < n) or i == j:
            raise MolfileError("A bond names an atom the drawing does not have.")
        if order not in (1, 2, 3):
            raise MolfileError(f"Bond order {order} is not 1, 2 or 3.")
        if stereo not in _STEREO:
            raise MolfileError(f"Bond stereo flag {stereo} is not 0, 1 or 6.")
        # a wedge on a double or triple bond means nothing in V2000
        out_bonds.append((i, j, order, stereo if order == 1 else 0))
    return out_atoms, out_bonds


def _props(tag: str, pairs: list) -> list:
    """``M  CHG`` / ``M  RAD`` lines, at most eight entries per line."""
    lines = []
    for k in range(0, len(pairs), 8):
        chunk = pairs[k:k + 8]
        lines.append(f"M  {tag}{len(chunk):3d}"
                     + "".join(f" {i + 1:3d} {v:3d}" for i, v in chunk))
    return lines


def write_molblock(drawing: dict, now: float | None = None) -> str:
    """The V2000 MOL text for a drawing {title?, atoms, bonds}.

    atoms: [{el, x, y, charge?, radical?}] (Å, y up, radical = unpaired
    electrons); bonds: [{a, b, order, stereo}] (0-based atom indices, stereo
    0 / 1 wedge / 6 hash, the narrow end at ``a``). Raises MolfileError."""
    atoms, bonds = _clean(drawing)
    stamp = time.strftime("%m%d%y%H%M", time.localtime(now))
    lines = [_title(drawing.get("title")) or "ORCAdesk 2D drawing",
             f"  ORCAdesk{stamp}2D",
             "",
             f"{len(atoms):3d}{len(bonds):3d}  0  0  0  0  0  0  0  0999 V2000"]
    for el, x, y, _chg, _rad in atoms:
        lines.append(f"{x:10.4f}{y:10.4f}{0.0:10.4f} {el:<3} 0  0  0  0  0  0  0  0  0  0  0  0")
    for i, j, order, stereo in bonds:
        lines.append(f"{i + 1:3d}{j + 1:3d}{order:3d}{stereo:3d}  0  0  0")
    lines += _props("CHG", [(i, a[3]) for i, a in enumerate(atoms) if a[3]])
    lines += _props("RAD", [(i, _RAD_CODE[a[4]]) for i, a in enumerate(atoms)
                            if a[4] in _RAD_CODE])
    lines.append("M  END")
    return "\n".join(lines) + "\n"
