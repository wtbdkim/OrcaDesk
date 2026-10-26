"""
An ORCA ``.hess`` file, read for its vibrational modes.

A frequency run leaves ``{base}.hess`` beside its output, and it carries the
three things a mode needs to be followed: the geometry the Hessian was taken
at (``$atoms``, Bohr), every mode's wavenumber (``$vibrational_frequencies``,
3N values, an imaginary one negative, the translations and rotations exactly
0) and every mode's direction (``$normal_modes``, a 3N x 3N matrix printed in
blocks of five COLUMNS, one column per mode, one row per Cartesian component).

The vectors are ORCA's Cartesian displacements - the mass-weighted
eigenvectors scaled back by 1/sqrt(m) and normalized, as its NORMAL MODES
section says - so a scaled column added to the coordinates is the motion the
mode describes. Verified on ORCA 6.1.1 output (phenol opt+freq, 13 atoms, 39
modes).

Qt-free and numpy-free, like ``structure.py`` beside it; reads never raise
past :class:`HessError`, whose message is the sentence the user reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

BOHR_TO_ANGSTROM = 0.529177210903


class HessError(ValueError):
    """A .hess that cannot be read for its modes; the message says why."""


@dataclass
class HessModes:
    symbols: "list[str]" = field(default_factory=list)
    coords: "list[tuple[float, float, float]]" = field(default_factory=list)  # Angstrom
    freqs: "list[float]" = field(default_factory=list)                          # 3N, cm^-1
    modes: "list[list[float]]" = field(default_factory=list)                    # 3N columns of 3N

    def vibrations(self) -> "list[int]":
        """The indices of the real vibrations: every mode but the translations
        and rotations, which ORCA writes as exactly 0 cm^-1 with a zero vector."""
        return [k for k, f in enumerate(self.freqs) if f != 0.0]


def _sections(lines: "list[str]") -> "dict[str, int]":
    return {ln.strip(): i for i, ln in enumerate(lines) if ln.startswith("$")}


def read_hess(path) -> HessModes:
    """Read the geometry, wavenumbers and normal modes out of an ORCA .hess."""
    p = Path(path)
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        raise HessError(f"Could not read {p.name}: {e.strerror or e}.") from None
    at = _sections(lines)
    for need in ("$atoms", "$vibrational_frequencies", "$normal_modes"):
        if need not in at:
            raise HessError(
                f"{p.name} has no {need} section — it is not the .hess of a "
                f"finished frequency calculation.")
    out = HessModes()
    try:
        i = at["$atoms"]
        n = int(lines[i + 1].split()[0])
        for ln in lines[i + 2:i + 2 + n]:
            parts = ln.split()
            out.symbols.append(parts[0])
            x, y, z = (float(v) * BOHR_TO_ANGSTROM for v in parts[2:5])
            out.coords.append((x, y, z))

        i = at["$vibrational_frequencies"]
        nf = int(lines[i + 1].split()[0])
        out.freqs = [float(lines[i + 2 + k].split()[1]) for k in range(nf)]

        i = at["$normal_modes"]
        rows, cols = (int(v) for v in lines[i + 1].split()[:2])
        out.modes = [[0.0] * rows for _ in range(cols)]
        k = i + 2
        # blocks: a header naming the columns, then `rows` rows of values
        while k < len(lines) and not lines[k].startswith("$"):
            head = lines[k].split()
            if not head:
                k += 1
                continue
            idx = [int(v) for v in head]
            for r in range(rows):
                vals = lines[k + 1 + r].split()
                for c, v in zip(idx, vals[1:]):
                    out.modes[c][int(vals[0])] = float(v)
            k += 1 + rows
            if idx[-1] >= cols - 1:
                break
    except (IndexError, ValueError):
        raise HessError(f"{p.name} could not be read: its mode tables are not in "
                        f"the layout ORCA writes.") from None
    if len(out.coords) * 3 != len(out.freqs) or len(out.modes) != len(out.freqs):
        raise HessError(f"{p.name} is inconsistent: {len(out.coords)} atoms but "
                        f"{len(out.freqs)} modes.")
    return out
