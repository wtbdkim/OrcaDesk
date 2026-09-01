"""
Structure screening and local coordinate editing for hand-built geometries.

Everything here is pure geometry over an ``.xyz`` coordinate block ("El x y z"
lines, Angstrom) - no numpy, no chemistry toolkit, no Qt - so it unit-tests
directly and the app keeps its three-package dependency list.

Two jobs:

*Screening* (:func:`check_geometry`) - the cheap questions worth asking before
a multi-hour ORCA launch (P26): does the electron count agree with the spin
multiplicity, are two atoms sitting on top of each other, did a file in Bohr
arrive where Angstrom was expected. Answers are reported, never fixed (P26),
and a chemically legitimate oddity (a two-fragment complex) is a *warning*,
not an error - the honest verdict is "look at this", not "this is wrong" (P2).

*Editing* (:func:`measure`, :func:`set_internal`, :func:`translate_atoms`,
:func:`rotate_atoms`) - rigid, order-preserving edits of one structure. The
atom ORDER is never touched, which is what makes "copy the reactant, move some
atoms, that is the product" a safe way to build a NEB endpoint: the mismatch
:func:`compare_atom_order` exists to catch cannot arise from an edit made here.

Bond perception is distance-based: i and j are bonded when their separation is
under the sum of their covalent radii plus :data:`BOND_TOLERANCE`. That is the
standard cheap heuristic - it has no notion of bond order and will call a short
hydrogen bond a bond. It is used only to group atoms into fragments and to
decide which side of a bond a rigid edit moves. **ORCA never sees any of it.**
"""

from __future__ import annotations

import math
import re
from collections import Counter, namedtuple

# One atom of a coordinate block. Immutable on purpose: every edit returns a
# new list, so an editor can keep an undo stack by keeping the old one.
Atom = namedtuple("Atom", ("sym", "x", "y", "z"))


class StructureError(ValueError):
    """A structure edit that cannot be carried out, with the reason in the
    message - surfaced to the user verbatim (P28)."""


# ---------------------------------------------------------------------------
# element data
# ---------------------------------------------------------------------------

# Symbols in Z order (index 0 is a placeholder so ELEMENTS[Z] == symbol).
ELEMENTS = (
    "", "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca",
    "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr",
    "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn",
    "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd",
    "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb",
    "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
    "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th",
    "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm",
    "Md", "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds",
    "Rg", "Cn", "Nh", "Fl", "Mc", "Lv", "Ts", "Og",
)

_Z_BY_SYMBOL = {s.lower(): z for z, s in enumerate(ELEMENTS) if s}

# Covalent radii in Angstrom (Cordero et al., Dalton Trans. 2008, 2832). The
# transition metals Cordero splits by spin state (Mn/Fe/Co) are entered as the
# midpoint: this table decides *which atoms are bonded for drawing purposes*,
# and a spin-state-accurate radius would not change that verdict.
_COVALENT_RADII = {
    "H": 0.31, "He": 0.28, "Li": 1.28, "Be": 0.96, "B": 0.84, "C": 0.76,
    "N": 0.71, "O": 0.66, "F": 0.57, "Ne": 0.58, "Na": 1.66, "Mg": 1.41,
    "Al": 1.21, "Si": 1.11, "P": 1.07, "S": 1.05, "Cl": 1.02, "Ar": 1.06,
    "K": 2.03, "Ca": 1.76, "Sc": 1.70, "Ti": 1.60, "V": 1.53, "Cr": 1.39,
    "Mn": 1.50, "Fe": 1.42, "Co": 1.38, "Ni": 1.24, "Cu": 1.32, "Zn": 1.22,
    "Ga": 1.22, "Ge": 1.20, "As": 1.19, "Se": 1.20, "Br": 1.20, "Kr": 1.16,
    "Rb": 2.20, "Sr": 1.95, "Y": 1.90, "Zr": 1.75, "Nb": 1.64, "Mo": 1.54,
    "Tc": 1.47, "Ru": 1.46, "Rh": 1.42, "Pd": 1.39, "Ag": 1.45, "Cd": 1.44,
    "In": 1.42, "Sn": 1.39, "Sb": 1.39, "Te": 1.38, "I": 1.39, "Xe": 1.40,
    "Cs": 2.44, "Ba": 2.15, "La": 2.07, "Ce": 2.04, "Pr": 2.03, "Nd": 2.01,
    "Pm": 1.99, "Sm": 1.98, "Eu": 1.98, "Gd": 1.96, "Tb": 1.94, "Dy": 1.92,
    "Ho": 1.92, "Er": 1.89, "Tm": 1.90, "Yb": 1.87, "Lu": 1.87, "Hf": 1.75,
    "Ta": 1.70, "W": 1.62, "Re": 1.51, "Os": 1.44, "Ir": 1.41, "Pt": 1.36,
    "Au": 1.36, "Hg": 1.32, "Tl": 1.45, "Pb": 1.46, "Bi": 1.48, "Po": 1.40,
    "At": 1.50, "Rn": 1.50, "Fr": 2.60, "Ra": 2.21, "Ac": 2.15, "Th": 2.06,
    "Pa": 2.00, "U": 1.96, "Np": 1.90, "Pu": 1.87, "Am": 1.80, "Cm": 1.69,
}
# Cordero's table stops at Cm; the superheavies get a neutral middling radius
# so bond perception degrades to "plausible" rather than to "nothing bonded".
_DEFAULT_RADIUS = 1.60

#: Slack added to the sum of covalent radii before two atoms count as bonded.
BOND_TOLERANCE = 0.45

#: Two atoms closer than this fraction of their covalent-radius sum are too
#: close to be a real bond (a C-C triple bond is still 0.79 of that sum).
_OVERLAP_FACTOR = 0.65

#: Below this separation the second atom is a duplicate line, not a short bond.
_DUPLICATE_ANGSTROM = 0.10


def normalize_symbol(sym: str) -> str:
    """Capitalized element symbol ('CL' -> 'Cl'), with any trailing label or
    isotope digits dropped ('C1', 'H_a' -> 'C', 'H'). ORCA itself is
    case-insensitive and legacy tools write 'CL', so comparisons and lookups
    all go through this."""
    core = re.match(r"[A-Za-z]+", (sym or "").strip())
    return core.group(0).capitalize() if core else ""


def atomic_number(sym: str):
    """Z for an element symbol, or None when it is not an element."""
    return _Z_BY_SYMBOL.get(normalize_symbol(sym).lower())


def covalent_radius(sym: str) -> float:
    """Covalent radius in Angstrom; a middling default for anything unlisted."""
    return _COVALENT_RADII.get(normalize_symbol(sym), _DEFAULT_RADIUS)


# ---------------------------------------------------------------------------
# parsing / formatting
# ---------------------------------------------------------------------------

def parse_block(text: str) -> "list[Atom]":
    """Atoms of an 'El x y z' coordinate block. Tolerant like the rest of the
    app's readers (P27): a full .xyz (count + comment header) is accepted too,
    and any line that is not four readable fields is skipped rather than
    raising - a half-pasted file yields the atoms it does have."""
    lines = (text or "").splitlines()
    start = 0
    if len(lines) >= 2 and re.match(r"^\s*\d+\s*$", lines[0]):
        start = 2                      # count + comment header
    atoms = []
    for ln in lines[start:]:
        parts = ln.split()
        if len(parts) < 4:
            continue
        try:
            x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
        except ValueError:
            continue
        # "inf"/"nan" parse as floats and would then poison every distance and
        # the neighbour grid's cell arithmetic — they are not coordinates
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            continue
        atoms.append(Atom(parts[0], x, y, z))
    return atoms


def format_block(atoms) -> str:
    """The coordinate block an edited structure goes back into the form as.
    Fixed 8-decimal columns: the same structure edited twice must produce the
    same text, so an unchanged edit never looks like a change."""
    return "\n".join(f"{a.sym:<2s} {a.x:14.8f} {a.y:14.8f} {a.z:14.8f}"
                     for a in atoms)


def formula(atoms) -> str:
    """Hill-notation molecular formula (C, then H, then the rest alphabetical)."""
    tally = Counter(normalize_symbol(a.sym) or "?" for a in atoms)
    order = []
    if "C" in tally:
        order.append("C")
        if "H" in tally:
            order.append("H")
    order += sorted(e for e in tally if e not in order)
    return "".join(e + (str(tally[e]) if tally[e] > 1 else "") for e in order)


# ---------------------------------------------------------------------------
# vector helpers (pure python: numpy is not a dependency of this app)
# ---------------------------------------------------------------------------

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(v, k):
    return (v[0] * k, v[1] * k, v[2] * k)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _norm(v):
    return math.sqrt(_dot(v, v))


def _unit(v):
    n = _norm(v)
    if n < 1e-12:
        raise StructureError("Two of the selected atoms are at the same position.")
    return _scale(v, 1.0 / n)


def _pos(a):
    return (a.x, a.y, a.z)


def _rodrigues(v, axis, angle_rad):
    """Rotate vector `v` about the unit vector `axis` by `angle_rad`."""
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return _add(_add(_scale(v, c), _scale(_cross(axis, v), s)),
                _scale(axis, _dot(axis, v) * (1.0 - c)))


# ---------------------------------------------------------------------------
# connectivity
# ---------------------------------------------------------------------------

def bonds(atoms) -> "list[tuple]":
    """Perceived bonds as sorted (i, j) index pairs, i < j. Distance-based (see
    the module docstring): no bond orders, and only ever used for fragments and
    for choosing the moving side of an edit.

    Neighbours are found through a uniform grid rather than an all-pairs sweep.
    That is not premature: this runs on the Qt UI thread on every charge
    keystroke and every tab entry, and the quadratic version turns a
    thousand-atom structure into a visible freeze. With the cell edge set to the
    longest bond any pair could have, every partner is inside the 27 cells
    around an atom, so the cost tracks the atom count instead of its square.
    """
    n = len(atoms)
    if n < 2:
        return []
    radii = [covalent_radius(a.sym) for a in atoms]
    cell = 2.0 * max(radii) + BOND_TOLERANCE

    def _key(a):
        return (int(math.floor(a.x / cell)), int(math.floor(a.y / cell)),
                int(math.floor(a.z / cell)))

    buckets = {}
    for i, a in enumerate(atoms):
        buckets.setdefault(_key(a), []).append(i)

    out = []
    for i, a in enumerate(atoms):
        kx, ky, kz = _key(a)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in buckets.get((kx + dx, ky + dy, kz + dz), ()):
                        if j <= i:
                            continue        # each pair once, in i < j order
                        cutoff = radii[i] + radii[j] + BOND_TOLERANCE
                        d = _sub(_pos(atoms[j]), _pos(atoms[i]))
                        if _dot(d, d) <= cutoff * cutoff:
                            out.append((i, j))
    out.sort()
    return out


def _adjacency(n: int, bond_list) -> "list[set]":
    adj = [set() for _ in range(n)]
    for i, j in bond_list:
        adj[i].add(j)
        adj[j].add(i)
    return adj


def fragments(atoms, bond_list=None) -> "list[list]":
    """Connected components as lists of atom indices, each sorted, ordered by
    their lowest index - so a two-molecule complex reports [first, partner] in
    the order the file lists them."""
    n = len(atoms)
    adj = _adjacency(n, bond_list if bond_list is not None else bonds(atoms))
    seen, groups = set(), []
    for start in range(n):
        if start in seen:
            continue
        comp, stack = [], [start]
        seen.add(start)
        while stack:
            a = stack.pop()
            comp.append(a)
            for b in adj[a]:
                if b not in seen:
                    seen.add(b)
                    stack.append(b)
        groups.append(sorted(comp))
    return groups


def fragment_of(atoms, index: int) -> "list[int]":
    """Indices of the connected fragment containing `index` (itself included)."""
    for group in fragments(atoms):
        if index in group:
            return group
    return [index]


def _moving_side(adj, anchor: int, moving: int):
    """Indices rigidly attached to `moving` once the anchor-moving bond is cut:
    everything reachable from `moving` without crossing that bond.

    Returns None when `anchor` is still reachable - the two are in a ring (or
    joined by some other path), and then no rigid rotation can change the
    internal coordinate without deforming that path. Refusing is the honest
    answer; silently deforming the ring would not be (P2).

    When the two are not bonded at all the cut is a no-op, so this doubles as
    "the whole fragment `moving` belongs to, provided `anchor` is not in it" -
    which is exactly the wanted behaviour for setting the approach distance
    between two separate fragments of a complex.
    """
    seen = {moving}
    stack = [moving]
    while stack:
        a = stack.pop()
        for b in adj[a]:
            if (a == moving and b == anchor) or (a == anchor and b == moving):
                continue                      # the cut bond
            if b not in seen:
                seen.add(b)
                stack.append(b)
    return None if anchor in seen else seen


# ---------------------------------------------------------------------------
# measuring
# ---------------------------------------------------------------------------

def measure(atoms, indices) -> float:
    """Distance (2 indices, Angstrom), angle (3, degrees) or dihedral
    (4, signed degrees) for the selected atoms, in selection order."""
    n = len(indices)
    if n not in (2, 3, 4):
        raise StructureError("Select 2 atoms for a distance, 3 for an angle, "
                             "4 for a dihedral.")
    for i in indices:
        if not 0 <= i < len(atoms):
            raise StructureError(f"Atom #{i + 1} is not in this structure.")
    p = [_pos(atoms[i]) for i in indices]
    if n == 2:
        return _norm(_sub(p[1], p[0]))
    if n == 3:
        u, v = _unit(_sub(p[0], p[1])), _unit(_sub(p[2], p[1]))
        return math.degrees(math.acos(max(-1.0, min(1.0, _dot(u, v)))))
    # IUPAC-signed dihedral via the standard atan2 form - stable near 0 and 180
    # degrees, where taking an acos of the two plane normals loses both the sign
    # and most of the precision.
    b1, b2, b3 = _sub(p[1], p[0]), _sub(p[2], p[1]), _sub(p[3], p[2])
    n1, n2 = _cross(b1, b2), _cross(b2, b3)
    m = _cross(n1, _unit(b2))
    return math.degrees(math.atan2(_dot(m, n2), _dot(n1, n2)))


# ---------------------------------------------------------------------------
# editing (rigid, order-preserving)
# ---------------------------------------------------------------------------

def _apply(atoms, moving, fn):
    """New atom list with `fn(position)` applied to the `moving` indices. The
    list is rebuilt in place order, so the atom ORDER is preserved by
    construction - the property the whole editor exists to guarantee."""
    out = []
    for i, a in enumerate(atoms):
        if i in moving:
            x, y, z = fn(_pos(a))
            out.append(Atom(a.sym, x, y, z))
        else:
            out.append(a)
    return out


def set_internal(atoms, indices, value: float) -> "list[Atom]":
    """Set the internal coordinate named by `indices` to `value` (Angstrom for
    2 indices, degrees for 3 or 4) and return the new atom list.

    The side that moves is the one carrying the LAST selected atom, split at
    the bond nearest it - the convention every structure editor uses, and the
    one that makes "select outwards along the chain, then type a value" behave
    the way it reads. Everything is rigid: bond lengths and angles inside the
    moving side are untouched.
    """
    n = len(indices)
    if n not in (2, 3, 4):
        raise StructureError("Select 2 atoms for a distance, 3 for an angle, "
                             "4 for a dihedral.")
    if len(set(indices)) != n:
        raise StructureError("The same atom is selected twice.")
    for i in indices:
        if not 0 <= i < len(atoms):
            raise StructureError(f"Atom #{i + 1} is not in this structure.")

    adj = _adjacency(len(atoms), bonds(atoms))
    p = [_pos(atoms[i]) for i in indices]

    if n == 2:
        i, j = indices
        if value <= 0:
            raise StructureError("A bond length must be greater than zero.")
        side = _moving_side(adj, i, j)
        if side is None:
            raise StructureError(
                f"Atoms #{i + 1} and #{j + 1} are in a ring — moving them apart "
                f"would have to stretch the rest of the ring, which a rigid "
                f"edit cannot do.")
        shift = _scale(_unit(_sub(p[1], p[0])), value - _norm(_sub(p[1], p[0])))
        return _apply(atoms, side, lambda q: _add(q, shift))

    if n == 3:
        i, j, k = indices
        side = _moving_side(adj, j, k)
        if side is None:
            raise StructureError(
                f"Atoms #{j + 1} and #{k + 1} are in a ring — a rigid edit "
                f"cannot open the angle without deforming it.")
        axis_raw = _cross(_sub(p[0], p[1]), _sub(p[2], p[1]))
        if _norm(axis_raw) < 1e-8:
            raise StructureError("Those three atoms are in a straight line, so "
                                 "the angle has no plane to open in.")
        axis = _unit(axis_raw)
        delta = math.radians(value - measure(atoms, indices))
        origin = p[1]
        return _apply(atoms, side,
                      lambda q: _add(origin, _rodrigues(_sub(q, origin), axis, delta)))

    i, j, k, l = indices
    side = _moving_side(adj, j, k)
    if side is None:
        raise StructureError(
            f"Atoms #{j + 1} and #{k + 1} are in a ring — a dihedral about a "
            f"ring bond cannot be changed by a rigid rotation.")
    if l not in side:
        raise StructureError(
            f"Atom #{l + 1} is not on the far side of the #{j + 1}-#{k + 1} "
            f"bond, so rotating about it would not change this dihedral. "
            f"Select the four atoms along the chain.")
    # A right-handed rotation about the j->k axis *decreases* the dihedral it
    # is measured from (the IUPAC sign convention runs the other way), so the
    # step is current - target, not target - current. The angle case above has
    # the opposite handedness because its axis is derived from the two arms.
    axis = _unit(_sub(p[2], p[1]))
    delta = math.radians(measure(atoms, indices) - value)
    origin = p[1]
    return _apply(atoms, side,
                  lambda q: _add(origin, _rodrigues(_sub(q, origin), axis, delta)))


def translate_atoms(atoms, indices, vector) -> "list[Atom]":
    """Move the given atoms by `vector` (Angstrom), leaving the rest in place."""
    v = (float(vector[0]), float(vector[1]), float(vector[2]))
    return _apply(atoms, set(indices), lambda q: _add(q, v))


def rotate_atoms(atoms, indices, axis, degrees: float, origin=None) -> "list[Atom]":
    """Rotate the given atoms about `axis` through `origin` (their own centroid
    when omitted) by `degrees`. Rotating a fragment about its own centroid is
    the move that reorients a partner molecule without translating it away."""
    moving = sorted(set(indices))
    if not moving:
        return list(atoms)
    unit_axis = _unit((float(axis[0]), float(axis[1]), float(axis[2])))
    if origin is None:
        k = 1.0 / len(moving)
        centre = (sum(atoms[i].x for i in moving) * k,
                  sum(atoms[i].y for i in moving) * k,
                  sum(atoms[i].z for i in moving) * k)
    else:
        centre = (float(origin[0]), float(origin[1]), float(origin[2]))
    rad = math.radians(degrees)
    return _apply(atoms, set(moving),
                  lambda q: _add(centre, _rodrigues(_sub(q, centre), unit_axis, rad)))


# ---------------------------------------------------------------------------
# screening: the questions worth asking before a multi-hour launch (P26)
# ---------------------------------------------------------------------------

def electron_count(atoms, charge: int):
    """Total electrons for these atoms at this charge, or None when some
    symbol is not an element (the count is then genuinely unknown, and saying
    so beats guessing - P2)."""
    total = 0
    for a in atoms:
        z = atomic_number(a.sym)
        if z is None:
            return None
        total += z
    return total - int(charge)


def _issue(level: str, code: str, message: str, atoms=()) -> dict:
    return {"level": level, "code": code, "message": message,
            "atoms": list(atoms)}


def _charge_text(charge: int) -> str:
    """Charge as it reads in a sentence: signed when it is one, plain 0 when it
    is not ("at charge +0" is not something anyone writes)."""
    return f"{charge:+d}" if charge else "0"


#: How many too-close pairs are listed before the rest are summarized. A
#: coordinate file duplicated wholesale produces one pair per atom, and a
#: thousand-line panel would bury the one line that says what to do.
_MAX_LISTED_CONTACTS = 5


def check_geometry(xyz: str, charge: int = 0, multiplicity: int = 1) -> dict:
    """Screen a coordinate block against the mistakes that cost a whole run.

    Returns ``{ok, n_atoms, formula, electrons, n_bonds, n_fragments, issues}``
    where each issue is ``{level, code, message, atoms}`` and ``level`` is
    "error" (ORCA will refuse this, or it is not the structure anyone meant) or
    "warn" (legitimate, but worth a look). ``ok`` is false only for errors, so
    a two-fragment complex still passes.

    Nothing here is a correction: every finding names what was seen and leaves
    the decision to the user (P26).
    """
    atoms = parse_block(xyz)
    issues = []
    n = len(atoms)
    if not n:
        return {"ok": False, "n_atoms": 0, "formula": "", "electrons": None,
                "n_bonds": 0, "n_fragments": 0,
                "issues": [_issue("error", "no_atoms",
                                  "No coordinates read from this structure.")]}

    unknown = sorted({normalize_symbol(a.sym) or a.sym
                      for a in atoms if atomic_number(a.sym) is None})
    if unknown:
        bad = [i for i, a in enumerate(atoms) if atomic_number(a.sym) is None]
        issues.append(_issue(
            "error", "unknown_element",
            f"Not an element symbol: {', '.join(unknown)}. ORCA reads the first "
            f"column as an element, so it will refuse this file too.", bad))

    electrons = electron_count(atoms, charge)
    mult = int(multiplicity)
    if mult < 1:
        issues.append(_issue("error", "multiplicity_invalid",
                             f"Multiplicity {mult} is not a spin state; the "
                             f"lowest is 1 (all electrons paired)."))
    elif electrons is not None:
        unpaired = mult - 1
        if electrons < 0:
            issues.append(_issue(
                "error", "electron_count_negative",
                f"Charge {_charge_text(charge)} removes more electrons than the "
                f"molecule has ({electrons + charge})."))
        elif unpaired > electrons:
            issues.append(_issue(
                "error", "multiplicity_too_high",
                f"Multiplicity {mult} needs {unpaired} unpaired electrons, but "
                f"{formula(atoms)} at charge {_charge_text(charge)} has only "
                f"{electrons}."))
        elif (electrons - unpaired) % 2:
            suggest = 1 if electrons % 2 == 0 else 2
            parity = "even" if electrons % 2 == 0 else "odd"
            wants = "odd (1, 3, 5, ...)" if electrons % 2 == 0 else "even (2, 4, 6, ...)"
            issues.append(_issue(
                "error", "multiplicity_parity",
                f"{formula(atoms)} at charge {_charge_text(charge)} has {electrons} "
                f"electrons — an {parity} count, which needs an {wants} "
                f"multiplicity, not {mult}. Nearest: {suggest}."))

    bond_list = bonds(atoms)
    groups = fragments(atoms, bond_list)

    # Overlaps. Only pairs already close enough to be *considered* a bond can
    # be too close, so this rides the same O(N^2) sweep rather than a second one.
    radii = [covalent_radius(a.sym) for a in atoms]
    duplicates, too_close = [], []
    for i, j in bond_list:
        d = _norm(_sub(_pos(atoms[j]), _pos(atoms[i])))
        if d < _DUPLICATE_ANGSTROM:
            duplicates.append((i, j, d))
        elif d < _OVERLAP_FACTOR * (radii[i] + radii[j]):
            too_close.append((i, j, d))

    def _pairs(rows):
        return ", ".join(f"#{i + 1} {normalize_symbol(atoms[i].sym)}–"
                         f"#{j + 1} {normalize_symbol(atoms[j].sym)} "
                         f"({d:.2f} Å)" for i, j, d in rows[:_MAX_LISTED_CONTACTS])

    def _more(rows):
        extra = len(rows) - _MAX_LISTED_CONTACTS
        return f", and {extra} more" if extra > 0 else ""

    if duplicates:
        issues.append(_issue(
            "error", "duplicate_atom",
            f"{len(duplicates)} pair(s) of atoms sit on the same spot — the "
            f"same atom listed twice: {_pairs(duplicates)}{_more(duplicates)}.",
            sorted({i for i, j, _ in duplicates} | {j for _, j, _ in duplicates})))
    if too_close:
        issues.append(_issue(
            "error", "atoms_too_close",
            f"{len(too_close)} pair(s) are far closer than any bond between "
            f"those elements: {_pairs(too_close)}{_more(too_close)}. The SCF "
            f"will not converge on this.",
            sorted({i for i, j, _ in too_close} | {j for _, j, _ in too_close})))

    if n > 1 and not bond_list:
        issues.append(_issue(
            "warn", "no_bonds",
            f"No bonds found between the {n} atoms. Coordinates written in "
            f"Bohr read exactly like this — every distance comes out 1.89× too "
            f"long. Check the file's units."))
    elif len(groups) > 1:
        sizes = ", ".join(str(len(g)) for g in groups)
        issues.append(_issue(
            "warn", "disconnected",
            f"{len(groups)} disconnected fragments ({sizes} atoms). Expected "
            f"for a complex or a reactant pair; otherwise the file is missing "
            f"atoms.", [g[0] for g in groups]))

    return {
        "ok": not any(i["level"] == "error" for i in issues),
        "n_atoms": n,
        "formula": formula(atoms),
        "electrons": electrons,
        "n_bonds": len(bond_list),
        "n_fragments": len(groups),
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# NEB endpoint comparison
# ---------------------------------------------------------------------------

#: Mismatching indices carried in the payload. The message states the true
#: total; the table is what a person can actually read.
_MAX_LISTED_MISMATCHES = 100


def compare_atom_order(reactant_xyz: str, product_xyz: str) -> dict:
    """Compare two coordinate blocks as NEB endpoints.

    NEB-TS requires atom i of the reactant to be the SAME atom in the product.
    We cannot *fix* the ordering (which atom maps to which is a chemical
    decision, and bonds change in a reaction), but we can catch every way it
    goes wrong: different atom counts, different composition, or the same
    composition in the wrong order (P26).

    Returns ``{ok, error, mismatch_index, n_reactant, n_product,
    n_mismatches, mismatches, formula_reactant, formula_product}``.
    ``mismatch_index`` is the FIRST divergence (0-based, None when there is
    none) and ``mismatches`` lists up to :data:`_MAX_LISTED_MISMATCHES` of them
    as ``{index, reactant, product}`` with 0-based indices - populated whenever
    the two blocks have the same length, since only then do indices line up.

    :func:`orcamgr.core.input_generator.check_neb_atom_order` is the narrow
    three-key projection of this, kept for the input generator's own gate.
    """
    ra, pa = parse_block(reactant_xyz), parse_block(product_xyz)
    r = [normalize_symbol(a.sym) for a in ra]
    p = [normalize_symbol(a.sym) for a in pa]
    out = {
        "ok": False, "error": "", "mismatch_index": None,
        "n_reactant": len(r), "n_product": len(p),
        "n_mismatches": 0, "mismatches": [],
        "formula_reactant": formula(ra), "formula_product": formula(pa),
    }
    if not r or not p:
        out["error"] = "Could not read coordinates from reactant or product."
        return out
    if len(r) != len(p):
        out["error"] = (f"Atom count differs: reactant has {len(r)}, product "
                        f"has {len(p)}. NEB-TS needs the same atoms in both.")
        return out

    diffs = [{"index": i, "reactant": a, "product": b}
             for i, (a, b) in enumerate(zip(r, p)) if a != b]
    out["n_mismatches"] = len(diffs)
    out["mismatches"] = diffs[:_MAX_LISTED_MISMATCHES]

    if Counter(r) != Counter(p):
        rc = ", ".join(f"{el}{k}" for el, k in sorted(Counter(r).items()))
        pc = ", ".join(f"{el}{k}" for el, k in sorted(Counter(p).items()))
        out["error"] = (f"Element composition differs — reactant: {rc}; "
                        f"product: {pc}.")
        return out

    if diffs:
        first = diffs[0]
        out["mismatch_index"] = first["index"]
        out["error"] = (
            f"Atom order differs at atom #{first['index'] + 1}: reactant has "
            f"{first['reactant']}, product has {first['product']}. The atom "
            f"order must be identical in both structures (tip: build the "
            f"product by copying the reactant and moving atoms, so the order "
            f"is preserved).")
        return out

    out["ok"] = True
    return out
