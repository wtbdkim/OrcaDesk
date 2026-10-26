"""
A finished frequency run's normal modes, matched to the modes the report lists.

The report numbers vibrations the way the parser read them out of the
``.out`` (``ParseResult.frequencies``: every mode but the translations and
rotations, ORCA's order, imaginary ones first). Animating "mode 3" means
finding THAT mode's vector, and two files carry vectors:

* ``{base}.hess`` - the primary source: full-precision vectors and the
  geometry the Hessian was taken at (:mod:`.hessfile`).
* the ``.out``'s own ``NORMAL MODES`` table - the fallback, for a result whose
  ``.hess`` is not beside it (a folder copied without it) or is not the one the
  report's wavenumbers came from. The ``.out`` prints exactly the table the
  report was parsed from, so its LAST ``VIBRATIONAL FREQUENCIES`` block is the
  one read, and the geometry is the last ``CARTESIAN COORDINATES (ANGSTROEM)``
  block printed before it - the structure those modes belong to.

The match is checked by WAVENUMBER, never by index arithmetic alone: a ``.hess``
in the folder may be an OptTS's starting Hessian or a previous run's, and its
columns would then animate a different molecule's motion under this report's
label. A uniformly scaled run (``%freq ScalFreq``) is accepted: the ``.out``
prints the scaled numbers, and one common ratio is still the same modes.

Qt-free and numpy-free like ``hessfile.py``; every refusal is a
:class:`~.hessfile.HessError` whose message is the sentence the user reads.
"""

from __future__ import annotations

import re
from pathlib import Path

from .hessfile import HessError, HessModes, read_hess

_FREQ_LINE = re.compile(r"^\s*(\d+):\s+(-?\d+\.\d+)\s+cm\*\*-1")


def _rows_after(lines: "list[str]", start: int) -> int:
    """The first line after a section title and its dashed underline."""
    k = start + 1
    while k < len(lines) and (not lines[k].strip() or set(lines[k].strip()) == {"-"}):
        k += 1
    return k


def read_out_modes(path) -> HessModes:
    """The normal modes an ORCA ``.out`` prints, in the shape :func:`read_hess`
    returns: the last ``VIBRATIONAL FREQUENCIES`` block (3N wavenumbers,
    rigid modes 0.00), the ``NORMAL MODES`` table after it (3N columns of 3N,
    printed in blocks of six) and the geometry printed last before them."""
    p = Path(path)
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        raise HessError(f"Could not read {p.name}: {e.strerror or e}.") from None
    fi = [i for i, ln in enumerate(lines) if ln.strip() == "VIBRATIONAL FREQUENCIES"]
    if not fi:
        raise HessError(f"{p.name} has no frequency table.")
    start = fi[-1]
    out = HessModes()
    try:
        ci = [i for i in range(start) if lines[i].strip() == "CARTESIAN COORDINATES (ANGSTROEM)"]
        if not ci:
            raise HessError(f"{p.name} prints no geometry before its frequency table.")
        k = _rows_after(lines, ci[-1])
        while k < len(lines) and lines[k].strip():
            parts = lines[k].split()
            out.symbols.append(parts[0])
            out.coords.append((float(parts[1]), float(parts[2]), float(parts[3])))
            k += 1

        k = start + 1
        nm = None
        while k < len(lines):
            ln = lines[k]
            m = _FREQ_LINE.match(ln)
            if m:
                out.freqs.append(float(m.group(2)))
            elif ln.strip() == "NORMAL MODES":
                nm = k
                break
            k += 1
        if nm is None:
            raise HessError(f"{p.name} prints its frequencies but no NORMAL MODES table.")

        n = len(out.freqs)
        out.modes = [[0.0] * n for _ in range(n)]
        k = nm + 1
        done = False
        # blocks: a header of column numbers, then n rows "row v v v ..."
        while k < len(lines) and not done:
            head = lines[k].split()
            if not head or not all(t.isdigit() for t in head):
                k += 1
                continue
            idx = [int(t) for t in head]
            for r in range(n):
                vals = lines[k + 1 + r].split()
                if int(vals[0]) != r:
                    raise ValueError
                for c, v in zip(idx, vals[1:]):
                    out.modes[c][r] = float(v)
            k += 1 + n
            done = idx[-1] >= n - 1
        if not done:
            raise ValueError
    except (IndexError, ValueError):
        raise HessError(f"{p.name}'s NORMAL MODES table is not in the layout "
                        f"ORCA writes.") from None
    if len(out.coords) * 3 != len(out.freqs):
        raise HessError(f"{p.name} is inconsistent: {len(out.coords)} atoms but "
                        f"{len(out.freqs)} modes.")
    return out


def _agrees(ours: float, theirs: float) -> bool:
    # the .out prints two decimals; the .hess sixteen
    return abs(ours - theirs) <= max(0.02, 1e-5 * abs(theirs))


def report_columns(modes: HessModes, report_freqs: "list[float]") -> "list[int]":
    """The column of ``modes`` that each of the report's vibrations is, in the
    report's order - checked by wavenumber, so a ``.hess`` from some other
    Hessian is refused rather than animated under this report's numbers."""
    vib = modes.vibrations()
    want = [float(f) for f in report_freqs]
    if len(vib) != len(want):
        raise HessError(f"it holds {len(vib)} vibrations and the report lists {len(want)}.")
    have = [modes.freqs[k] for k in vib]
    if all(_agrees(h, w) for h, w in zip(have, want)):
        return vib
    # one common factor is a scaled run, not a different one
    ratios = [w / h for h, w in zip(have, want) if abs(h) > 1.0]
    if ratios and all(abs(r - ratios[0]) <= 1e-4 * abs(ratios[0]) + 2e-4 for r in ratios) \
            and all((h < 0) == (w < 0) for h, w in zip(have, want)):
        return vib
    bad = next(i for i, (h, w) in enumerate(zip(have, want)) if not _agrees(h, w))
    raise HessError(f"its mode {bad + 1} is {have[bad]:.2f} cm⁻¹ where the report "
                    f"has {want[bad]:.2f} cm⁻¹ — it is another Hessian's.")


def place_on(modes: HessModes, symbols: "list[str]",
             coords: "list[tuple[float, float, float]]"):
    """The modes' own geometry moved onto the report's structure, and how far
    it then is from it: ``(coords, max_dev)`` in Angstrom.

    A ``.hess`` writes its geometry about the centre of mass while the ``.out``
    prints it where the input put it (measured: phenol 17.9 Å apart, water
    0.054 Å), so the two are compared - and drawn - after a translation, which
    a normal mode does not notice. What is left is a real difference: the
    Hessian was taken at another geometry, and the caller says so."""
    if len(symbols) != len(modes.symbols) or any(
            a.strip().lower() != b.strip().lower() for a, b in zip(symbols, modes.symbols)):
        raise HessError("they are for other atoms, or the same atoms in another order.")
    n = len(coords) or 1
    shift = [sum(c[i] for c in coords) / n - sum(c[i] for c in modes.coords) / n
             for i in range(3)]
    moved = [(x + shift[0], y + shift[1], z + shift[2]) for x, y, z in modes.coords]
    dev = max((max(abs(a - b) for a, b in zip(p, q)) for p, q in zip(moved, coords)),
              default=0.0)
    return moved, dev


def modes_for_report(hess_path, out_path, report_freqs: "list[float]",
                     read=None) -> "tuple[HessModes, list[int], str]":
    """``(modes, columns, origin)`` for a result's vibrations: the ``.hess``
    when it is there and agrees with the report, else the ``.out``'s own
    table. ``origin`` is the name of the file the vectors came from.
    ``read(path, kind)`` lets a caller cache the parses ("hess" / "out")."""
    read = read or (lambda p, kind: read_hess(p) if kind == "hess" else read_out_modes(p))
    why = []
    for path, kind in ((hess_path, "hess"), (out_path, "out")):
        if not path:
            continue
        p = Path(path)
        if not p.is_file():
            if kind == "hess":
                why.append(f"there is no {p.name} beside the output")
            continue
        try:
            m = read(p, kind)
            return m, report_columns(m, report_freqs), p.name
        except HessError as e:
            msg = str(e)
            why.append(msg if msg.startswith(p.name) else f"{p.name}: {msg}")
    reason = "; ".join(why) if why else "no output file to read them from"
    raise HessError(f"No normal modes to animate — {reason}.")
