"""
ChemDraw CDXML writer for the 2D drawing -- the editable output that opens in
ChemDraw looking like the drawing on screen.

A MOL file carries atoms, bonds and wedge flags but no style, and ChemDraw
redraws one in its own document style: its bond length, its label size, its
line widths (measured: labels about two thirds of a bond, heavier than the
lines, so crowded hydrogens collide where the drawing had room). CDXML is
ChemDraw's own format and carries that style, so here it is the drawing's:
the same coordinates as the MOL (``web/next/draw2d.js`` molInput, scaled so
image px = Å x 24), and the SVG's sizes converted at 1 px = 0.75 pt --
13 px Arial labels, 1.5 px lines, 6.4 px wedges, the fixed bond length.

The input is the MOL writer's (``molfile.write_molblock``), plus each atom's
``label`` as drawn (``"OH"``, ``"H"``, ``""`` for a bare vertex): a labelled
atom gets that text, element symbol over the atom as ChemDraw places it.
"""
from __future__ import annotations

from xml.sax.saxutils import quoteattr

from ..core.structure import atomic_number
from .molfile import MolfileError, _clean

# draw2d.js's units: px per Å of the coordinates handed in, and the SVG's
# fixed sizes in px -- converted to points (CSS px = 0.75 pt)
_PX_PER_A = 24.0
_PT_PER_PX = 0.75
_BOND_PX = 43.2            # draw2d.js BOND_PX: the fixed bond length
_FONT_PX = 13.0
_LINE_PX = 1.5
_WEDGE_PX = 6.4            # a wedge's wide end, both sides of the bond
_HASH_PX = 3.2             # rung spacing of a hashed wedge
_BOND_SPACING = 16         # a double bond's gap, % of the bond (draw2d.js 0.16)
_MARGIN = 36.0             # pt of page around the drawing
_FONT_ID = 3

_DISPLAY = {1: "WedgeBegin", 6: "WedgedHashBegin"}   # narrow end at B
_RADICAL = {1: "Doublet", 2: "Triplet"}


def _f(v: float) -> str:
    return f"{v:.2f}"


def write_cdxml(drawing: dict) -> str:
    """The CDXML text for a drawing {title?, atoms, bonds} -- atoms
    [{el, x, y, charge?, radical?, label?}] in Å (y up), bonds
    [{a, b, order, stereo}] with the wedge's narrow end at ``a``.
    Raises MolfileError for what cannot be written."""
    atoms, bonds = _clean(drawing)
    raw = drawing.get("atoms") or []
    labels = []
    for i, a in enumerate(raw[:len(atoms)]):
        lab = a.get("label") if isinstance(a, dict) else None
        lab = str(lab) if lab is not None else (atoms[i][0] if atoms[i][0] != "C" else "")
        labels.append("".join(c for c in lab if c.isalnum())[:12])
    zs = []
    for el, *_ in atoms:
        z = atomic_number(el)
        if z is None:
            raise MolfileError(f"'{el}' is not an element symbol.")
        zs.append(z)

    k = _PX_PER_A * _PT_PER_PX                      # pt per Å
    xs = [a[1] * k for a in atoms]
    ys = [-a[2] * k for a in atoms]                 # CDXML's y points down
    x0, y0 = min(xs) - _MARGIN, min(ys) - _MARGIN
    pts = [(x - x0, y - y0) for x, y in zip(xs, ys)]
    w = max(x for x, _ in pts) + _MARGIN
    h = max(y for _, y in pts) + _MARGIN
    size = _FONT_PX * _PT_PER_PX

    nid = 1000
    out = ['<?xml version="1.0" encoding="UTF-8" ?>',
           '<!DOCTYPE CDXML SYSTEM "http://www.cambridgesoft.com/xml/cdxml.dtd" >',
           f'<CDXML CreationProgram="ORCAdesk" Name={quoteattr(str(drawing.get("title") or "structure"))}'
           f' BoundingBox="0 0 {_f(w)} {_f(h)}"'
           f' BondLength="{_f(_BOND_PX * _PT_PER_PX)}" LineWidth="{_f(_LINE_PX * _PT_PER_PX)}"'
           f' BoldWidth="{_f(_WEDGE_PX * _PT_PER_PX)}" HashSpacing="{_f(_HASH_PX * _PT_PER_PX)}"'
           f' MarginWidth="1.60" BondSpacing="{_BOND_SPACING}"'
           f' LabelFont="{_FONT_ID}" LabelSize="{_f(size)}" LabelFace="96"'
           f' CaptionFont="{_FONT_ID}" CaptionSize="{_f(size)}">',
           f'<fonttable><font id="{_FONT_ID}" charset="iso-8859-1" name="Arial"/></fonttable>',
           f'<page id="1" BoundingBox="0 0 {_f(w)} {_f(h)}" HeightPages="1" WidthPages="1">',
           '<fragment id="2">']
    ids = []
    for i, ((el, _x, _y, chg, rad), (px, py), z) in enumerate(zip(atoms, pts, zs)):
        nid += 1
        ids.append(nid)
        attrs = [f'id="{nid}"', f'p="{_f(px)} {_f(py)}"']
        if z != 6:
            attrs.append(f'Element="{z}"')
        if chg:
            attrs.append(f'Charge="{chg}"')
        if rad in _RADICAL:
            attrs.append(f'Radical="{_RADICAL[rad]}"')
        lab = labels[i] if i < len(labels) else ""
        if lab:
            # hydrogens the drawing counted on this atom ("OH", "NH2")
            rest = lab[len(el):] if lab.startswith(el) else ""
            if rest.startswith("H"):
                n = rest[1:]
                attrs.append(f'NumHydrogens="{int(n) if n.isdigit() else 1}"')
            elif z != 1:
                attrs.append('NumHydrogens="0"')
            # the element symbol centred on the atom, the rest running right,
            # as ChemDraw places a label: p is the text's baseline start
            tx, ty = px - 0.32 * size * len(el), py + 0.35 * size
            out.append(f'<n {" ".join(attrs)}><t p="{_f(tx)} {_f(ty)}" LabelJustification="Left"'
                       f' InterpretChemically="yes"><s font="{_FONT_ID}" size="{_f(size)}"'
                       f' face="96">{lab}</s></t></n>')
        else:
            out.append(f'<n {" ".join(attrs)}/>')
    for a, b, order, stereo in bonds:
        nid += 1
        attrs = [f'id="{nid}"', f'B="{ids[a]}"', f'E="{ids[b]}"']
        if order != 1:
            attrs.append(f'Order="{order}"')
        if stereo in _DISPLAY:
            attrs.append(f'Display="{_DISPLAY[stereo]}"')
        out.append(f'<b {" ".join(attrs)}/>')
    out += ['</fragment>', '</page>', '</CDXML>']
    return "\n".join(out) + "\n"
