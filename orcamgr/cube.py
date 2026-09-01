"""
Read Gaussian ``.cube`` volumetric files for the in-app 3D viewer.

The viewer (3Dmol.js) parses cube *text* itself, so this module deliberately
does **not** re-decode the value block: it reads the header — which is all the
UI needs to describe and size the grid — and hands the file through verbatim.
That keeps a 3 MB cube out of Python's memory as a float list and out of the
JSON payload as a re-serialized array.

One ORCA quirk makes the header parse non-trivial. ORCA writes the *orbital*
variant of the format: the atom count on line 3 is **negative** (``-52``), which
per the cube convention means one extra line follows the atom block listing the
MO indices in the file (``1   96``). 3Dmol handles that case (it takes
``Math.abs`` of the count and skips the extra line), and so must any code here
that wants to know where the values start.

Pure / file-only and Qt-free so it stays unit-testable; the Bridge slot reads
the file and serializes the returned dict.
"""

from __future__ import annotations

from pathlib import Path

# Conventional isosurface levels per plot kind, in atomic units: an MO is
# normally drawn at 0.05, a total electron density lower (0.02 sits between the
# tight bonding view and the ~0.002 van-der-Waals-like surface), and a spin
# density lower still because it is a small difference of two large numbers.
#
# The viewer treats these as a CEILING, not the answer. 0.05 is right for a
# *localized* orbital, which is not the same as right for an orbital: CB8's HOMO
# is spread over eight carbonyls and peaks at 0.0996, so at 0.05 only 222 of
# 216,000 grid points clear the level and the surface is invisible. So
# web/molviewer.js `_defaultIso` lowers it to the level enclosing 90% of
# psi-squared when the data has nothing up here, and never raises it above the
# convention. Fitting needs the values; this file only reads the header.
DEFAULT_ISOVALUES = {"mo": 0.05, "eldens": 0.02, "spindens": 0.005, "esp": 0.05}

# Which kinds have meaningful negative lobes and are drawn as a ± pair. A total
# electron density is positive everywhere, so a second surface would be noise.
SIGNED_KINDS = {"mo", "spindens"}

# ---- the ESP map --------------------------------------------------------
# An ESP map is not an isosurface of the potential; it is the potential painted
# ON an electron-density surface, and it therefore needs TWO cubes on the same
# grid. These two constants are the convention it is drawn by, kept here so the
# front-end does not carry a second copy of them (P4).

#: The density isosurface an ESP map is painted on, in e/bohr³. 0.002 is the
#: literature's isodensity surface — it traces the molecule at roughly its
#: van-der-Waals boundary, which is where an electrostatic potential is a
#: statement about *approach*. Deliberately NOT the 0.02 above: that level sits
#: in the bonding region, where the potential is dominated by the nuclei and the
#: map would be uniformly positive and say nothing.
ESP_SURFACE_ISOVALUE = 0.002

#: Half-width of the colour scale, in atomic units (±0.05 Eh/e is the usual
#: figure). Measured on neutral water at def2-SVP: the potential runs −0.097 to
#: +20.1 over the whole box, but its 1st and 99th percentiles are −0.051 and
#: +0.114, so ±0.05 spans the range that is actually on the surface while
#: keeping the near-nucleus spike from flattening everything to one colour.
ESP_DEFAULT_RANGE = 0.05


def _floats(line: str) -> list[float]:
    out = []
    for tok in line.split():
        try:
            out.append(float(tok))
        except ValueError:
            break
    return out


def read_cube_header(path) -> dict:
    """Header facts about a cube file: ``{ok, title, natoms, nx, ny, nz, npoints,
    origin}``, or ``{ok: False, error}``. Tolerant — a malformed file is reported,
    never raised (P27).

    ``title`` is the cube's second comment line, which ORCA fills in with what it
    actually plotted (``"Molecular orbital 96 of operator 0"``, ``"Total electron
    density"``, ``"Spin density"``). Preferring it over a label we compose keeps
    the caption honest about what is on screen.
    """
    p = Path(path)
    try:
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            head = [fh.readline() for _ in range(6)]
    except FileNotFoundError:
        # The raw OSError repeats the full path, which the UI already shows.
        return {"ok": False, "error": "That cube file is no longer on disk."}
    except OSError as e:
        return {"ok": False, "error": str(e)}
    if len(head) < 6 or not head[5].strip():
        return {"ok": False, "error": "Not a cube file (header is truncated)."}

    title = head[1].strip()
    a = _floats(head[2])
    dims = [_floats(head[i]) for i in (3, 4, 5)]
    if len(a) < 4 or any(len(d) < 4 for d in dims):
        return {"ok": False, "error": "Not a cube file (unreadable header)."}

    # A negative atom count is the orbital-cube variant: |natoms| atoms, then one
    # extra line of MO indices before the values.
    natoms = int(abs(a[0]))
    nx, ny, nz = (int(d[0]) for d in dims)
    if min(nx, ny, nz) <= 0 or natoms <= 0:
        return {"ok": False, "error": "Not a cube file (degenerate grid)."}
    return {"ok": True, "title": title, "natoms": natoms,
            "nx": nx, "ny": ny, "nz": nz, "npoints": nx * ny * nz,
            "origin": [a[1], a[2], a[3]]}


def load_cube(path, kind: str = "mo", max_bytes: int = 0) -> dict:
    """Read a cube file for the viewer: ``{ok, text, title, npoints, dims, bytes,
    isovalue, signed}`` or ``{ok: False, error}``.

    ``text`` is the file verbatim — 3Dmol's ``addVolumetricData(text, "cube")``
    consumes it directly. ``max_bytes`` (0 = no limit) refuses an oversized file
    up front rather than letting it land on the JS heap (P48); the caller passes
    the cap so the policy stays with the wire, not the file reader.
    """
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as e:
        return {"ok": False, "error": str(e)}
    if max_bytes and size > max_bytes:
        return {"ok": False,
                "error": f"That cube is {size / 1e6:.0f} MB — too large to display. "
                         f"Regenerate it at a coarser grid."}
    header = read_cube_header(p)
    if not header.get("ok"):
        return header
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "text": text, "title": header["title"],
            "npoints": header["npoints"],
            "dims": [header["nx"], header["ny"], header["nz"]],
            "bytes": size,
            "isovalue": DEFAULT_ISOVALUES.get(kind, 0.05),
            "signed": kind in SIGNED_KINDS}
