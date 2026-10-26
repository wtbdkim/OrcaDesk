"""
Structure screening for the geometry a calculation is about to run.

Everything here is pure geometry over an ``.xyz`` coordinate block ("El x y z"
lines, Angstrom) - no numpy, no chemistry toolkit, no Qt - so it unit-tests
directly and the app keeps its three-package dependency list.

*Screening* (:func:`check_geometry`) asks the cheap questions worth asking
before a multi-hour ORCA launch (P26): does the electron count agree with the
spin multiplicity, are two atoms sitting on top of each other, did a file in
Bohr arrive where Angstrom was expected. Answers are reported, never fixed
(P26), and a chemically legitimate oddity (a two-fragment complex) is a
*warning*, not an error - the honest verdict is "look at this", not "this is
wrong" (P2). :func:`compare_atom_order` is the same idea for a NEB endpoint
pair.

Everything here READS, with one narrow exception. There is deliberately no
structure editing: building and modifying molecules is what a molecular editor
is for, Avogadro2 and its peers do it properly, and a side feature here could
only be a worse one. ORCAdesk's job is to tell you what the file you are about
to run contains. The exception is :func:`apply_constraint_target`: a
constraint with a target names a geometry the run will be driven to, and
showing those coordinates before the run - one rigid motion, nothing else - is
part of telling you what you are about to run.

Bond perception is distance-based: i and j are bonded when their separation is
under the sum of their covalent radii plus :data:`BOND_TOLERANCE`. That is the
standard cheap heuristic - it has no notion of bond order and will call a short
hydrogen bond a bond. It is used only to group atoms into fragments and to
report the findings below. **ORCA never sees any of it.**
"""

from __future__ import annotations

import math
import re
from collections import Counter, namedtuple

# One atom of a coordinate block.
Atom = namedtuple("Atom", ("sym", "x", "y", "z"))


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
# parsing
# ---------------------------------------------------------------------------

def parse_block(text: str) -> "list[Atom]":
    """Atoms of an 'El x y z' coordinate block. Tolerant like the rest of the
    app's readers (P27): a full .xyz (count + comment header) is accepted too,
    and any line that is not four readable fields is skipped rather than
    raising - a half-pasted file yields the atoms it does have."""
    return [a for _, a in _atom_lines(text)]


def _atom_lines(text: str) -> "list[tuple[int, Atom]]":
    """parse_block, with the line each atom came from - so a change to some
    atoms can be written back into the user's own text without reformatting
    the lines it did not touch."""
    lines = (text or "").split("\n")
    start = 0
    if len(lines) >= 2 and re.match(r"^\s*\d+\s*$", lines[0]):
        start = 2                      # count + comment header
    out = []
    for n in range(start, len(lines)):
        parts = lines[n].split()
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
        out.append((n, Atom(parts[0], x, y, z)))
    return out


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


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(v):
    return math.sqrt(_dot(v, v))


def _pos(a):
    return (a.x, a.y, a.z)


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


# ---------------------------------------------------------------------------
# moving a structure to a constraint's target - the one place this WRITES
# ---------------------------------------------------------------------------
#
# A constraint with a target (`{ A 0 1 2 120.0 C }`) states a geometry the
# coordinates do not have yet. ORCA drags the coordinate there during the
# optimization, but the user cannot SEE that structure before it runs, and a
# large jump is a poor first step. So the builder can move the coordinates to
# the target first: one rigid motion of the smaller side of the hinge bond,
# the convention every structure editor uses. A hydroxyl turned to 120 degrees
# takes its hydrogen with it rather than leaving it where the oxygen was.
#
# This is deliberately NOT a structure editor (one was removed from here - see
# the module docstring). It answers one question - "show me these coordinates
# at the value I am about to constrain them to" - and nothing else: no free
# moves, no force field, no relaxation of what the motion bends.


class StructureError(ValueError):
    """A target the coordinates cannot be moved to rigidly; the message is the
    sentence the user reads."""


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(v, k):
    return (v[0] * k, v[1] * k, v[2] * k)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _unit(v):
    n = _norm(v)
    if n < 1e-12:
        raise StructureError("Two of those atoms are at the same position.")
    return _scale(v, 1.0 / n)


def _rodrigues(v, axis, angle_rad):
    """Rotate vector `v` about the unit vector `axis` by `angle_rad`."""
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return _add(_add(_scale(v, c), _scale(_cross(axis, v), s)),
                _scale(axis, _dot(axis, v) * (1.0 - c)))


def measure(atoms, indices) -> float:
    """Distance (2 indices, Angstrom), angle (3, degrees, the middle atom the
    vertex) or dihedral (4, IUPAC-signed degrees) - the coordinate ORCA's
    B / A / D constraint names."""
    p = [_pos(atoms[i]) for i in indices]
    if len(p) == 2:
        return _norm(_sub(p[1], p[0]))
    if len(p) == 3:
        u, v = _unit(_sub(p[0], p[1])), _unit(_sub(p[2], p[1]))
        return math.degrees(math.acos(max(-1.0, min(1.0, _dot(u, v)))))
    # The atan2 form, stable near 0 and 180 where an acos of the two plane
    # normals loses both the sign and most of the precision. The sign is
    # IUPAC's - positive when the front bond turns CLOCKWISE onto the back one,
    # looking from j to k - which is ORCA's and the stage's (MOL.dihedral). The
    # editor this came from had it the other way round, and an Apply to +90
    # read back as -90 on the row beside it.
    b1, b2, b3 = _sub(p[1], p[0]), _sub(p[2], p[1]), _sub(p[3], p[2])
    y = _norm(b2) * _dot(b1, _cross(b2, b3))
    x = _dot(_cross(b1, b2), _cross(b2, b3))
    return math.degrees(math.atan2(y, x))


def _moving_side(adj, anchor: int, moving: int):
    """Everything reachable from `moving` without crossing the anchor-moving
    bond - the part that moves rigidly when that bond is the hinge.

    None when `anchor` is still reachable: the two are in a ring, and no rigid
    motion changes the coordinate without deforming the ring. Refusing is the
    honest answer; quietly bending the ring would not be (P2). When the two are
    not bonded at all the cut is a no-op, so for two separate fragments this is
    "the whole fragment `moving` belongs to", which is what setting an approach
    distance in a complex wants."""
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


def _side_for(adj, idx):
    """The side that moves for this ordering of the atoms: the one carrying the
    LAST atom, cut at the bond nearest it (a dihedral's at its central bond, an
    angle's at the vertex-last bond). None when it cannot move rigidly."""
    if len(idx) == 2:
        return _moving_side(adj, idx[0], idx[1])
    if len(idx) == 3:
        return _moving_side(adj, idx[1], idx[2])
    side = _moving_side(adj, idx[1], idx[2])
    if side is not None and idx[3] not in side:
        return None
    return side


def set_internal(atoms, indices, value: float):
    """Move atoms rigidly so the coordinate `indices` names reads `value`.
    Returns (new atom list, set of moved indices).

    Of the two sides of the hinge, the SMALLER one moves: turning a hydroxyl on
    a ring moves O and H, not the ring - whichever end the drag happened to
    start from. A coordinate reads the same in either direction (B i j = B j i,
    A i j k = A k j i, D i j k l = D l k j i), so the choice is free. The atom
    order is preserved by construction."""
    n = len(indices)
    adj = _adjacency(len(atoms), bonds(atoms))
    options = []
    for idx in (tuple(indices), tuple(reversed(indices))):
        side = _side_for(adj, idx)
        if side is not None:
            options.append((len(side), idx, side))
    if not options:
        a, b = (indices[1], indices[2]) if n == 4 else (indices[0], indices[1])
        what = {2: "pull them apart", 3: "open the angle",
                4: "turn about that bond"}[n]
        raise StructureError(
            f"Atoms {a + 1} and {b + 1} are in a ring, so the coordinates cannot "
            f"{what} without bending the ring. The optimization will still move "
            f"it to the target.")
    # fewest atoms first; a stable sort keeps the side of the last atom named
    # on a tie, which is what "select outwards along the chain" reads as
    options.sort(key=lambda o: o[0])
    _, idx, side = options[0]
    p = [_pos(atoms[i]) for i in idx]

    if n == 2:
        shift = _scale(_unit(_sub(p[1], p[0])), value - _norm(_sub(p[1], p[0])))

        def fn(q):
            return _add(q, shift)
    else:
        if n == 3:
            axis_raw = _cross(_sub(p[0], p[1]), _sub(p[2], p[1]))
            if _norm(axis_raw) < 1e-8:
                raise StructureError("Those three atoms are in a straight line, "
                                     "so the angle has no plane to open in.")
            axis = _unit(axis_raw)
            delta = math.radians(value - measure(atoms, idx))
        else:
            # Seen from j looking at k, a right-handed turn about j->k moves
            # the back bond clockwise, so it INCREASES the IUPAC dihedral.
            axis = _unit(_sub(p[2], p[1]))
            delta = math.radians(value - measure(atoms, idx))
        origin = p[1]

        def fn(q):
            return _add(origin, _rodrigues(_sub(q, origin), axis, delta))

    out = []
    for i, a in enumerate(atoms):
        if i in side:
            x, y, z = fn(_pos(a))
            out.append(Atom(a.sym, x, y, z))
        else:
            out.append(a)
    return out, side


#: The coordinates a target can be applied to, by ORCA constraint letter. A
#: fixed position (C) has no value to move to.
_TARGET_ATOMS = {"B": 2, "A": 3, "D": 4}

_MAX_LISTED_CLASHES = 3


def _decimals(tok: str) -> int:
    return len(tok.split(".", 1)[1]) if "." in tok else 0


def _rewrite(text: str, rows, new_atoms, moved) -> str:
    """`text` with the atoms in `moved` set to their `new_atoms` position. Only
    those lines change, and only their three numbers: the symbol, the spacing
    between fields and anything after them stay as the user wrote them, at the
    line's own precision but never fewer than 6 decimals (at 5 a 100 degree
    target read back as 99.9998: the rounding is a bend). `rows` is
    _atom_lines(text)."""
    lines = (text or "").split("\n")
    for k in sorted(moved):
        n_line = rows[k][0]
        pieces = re.split(r"(\s+)", lines[n_line])
        words = [n for n, t in enumerate(pieces) if t and not t.isspace()]
        dec = min(10, max(6, *(_decimals(pieces[n]) for n in words[1:4])))
        a = new_atoms[k]
        for n, v in zip(words[1:4], (a.x, a.y, a.z)):
            pieces[n] = f"{v:.{dec}f}"
        lines[n_line] = "".join(pieces)
    return "\n".join(lines)


def apply_constraint_target(text: str, kind: str, indices, value) -> dict:
    """The coordinate block `text` with a constraint's atoms moved to its
    target - the builder's *Apply* button.

    Returns {ok, error, xyz, moved, before, after, warnings}. Only the lines of
    atoms that moved are rewritten, at their own precision but never fewer than
    6 decimals (at 5 a 100 degree target read back as 99.9998: the rounding is
    a bend); every other line is the user's text, byte for byte. "after" is
    measured on the text handed back, not on the unrounded motion, so it states
    what the box now holds. A moved atom that lands on one that
    stayed is reported, not refused (P26): it is the structure that was asked
    for, and worth a look before it runs."""
    out = {"ok": False, "error": "", "xyz": "", "moved": [],
           "before": None, "after": None, "warnings": []}
    n = _TARGET_ATOMS.get(str(kind or "").upper())
    if n is None:
        out["error"] = "Only a bond, angle or dihedral has a target to move to."
        return out
    try:
        idx = [int(i) for i in indices]
        v = float(value)
    except (TypeError, ValueError):
        out["error"] = "That constraint has no target value."
        return out
    if len(idx) != n or len(set(idx)) != n:
        out["error"] = f"That constraint needs {n} different atoms."
        return out
    if not math.isfinite(v):
        out["error"] = "That target is not a number."
        return out
    rows = _atom_lines(text)
    atoms = [a for _, a in rows]
    for i in idx:
        if not 0 <= i < len(atoms):
            out["error"] = f"There is no atom {i + 1} — the structure has {len(atoms)}."
            return out
    if n == 2 and not 0.1 <= v <= 50:
        out["error"] = "A bond target must be between 0.1 and 50 Å."
        return out
    if n == 3 and not 0 < v <= 180:
        out["error"] = "An angle target must be above 0° and at most 180°."
        return out
    try:
        before = measure(atoms, idx)
        moved_atoms, side = set_internal(atoms, idx, v)
    except StructureError as e:
        out["error"] = str(e)
        return out

    # an atom ON the hinge axis (a dihedral's own k) belongs to the moving side
    # but does not move, and its line stays the user's
    side = {k for k in side
            if _norm(_sub(_pos(moved_atoms[k]), _pos(atoms[k]))) > 1e-9}
    xyz = _rewrite(text, rows, moved_atoms, side)

    radii = [covalent_radius(a.sym) for a in moved_atoms]
    clashes = []
    for i in sorted(side):
        for j in range(len(moved_atoms)):
            if j in side:
                continue
            d = _norm(_sub(_pos(moved_atoms[i]), _pos(moved_atoms[j])))
            if d < _OVERLAP_FACTOR * (radii[i] + radii[j]):
                clashes.append((d, i, j))
    clashes.sort()

    def name(i):
        return f"{normalize_symbol(atoms[i].sym) or atoms[i].sym}{i + 1}"

    out["warnings"] = [
        f"{name(i)} is now {d:.2f} Å from {name(j)} — closer than a bond."
        for d, i, j in clashes[:_MAX_LISTED_CLASHES]]
    out.update(ok=True, xyz=xyz, moved=sorted(side), before=before,
               after=measure(parse_block(xyz), idx))
    return out


# ---------------------------------------------------------------------------
# pushing a structure along a vibrational mode - the second place this WRITES
# ---------------------------------------------------------------------------
#
# A frequency run's .hess holds every mode's direction. Moving the coordinates
# a little along one - most often an imaginary mode, to step off a saddle
# point before re-optimizing to a minimum - is the standard repair, done by
# hand in a text editor because ORCA has no option for it. Like
# apply_constraint_target this answers one narrow question - "these
# coordinates, pushed this far along that mode" - and writes nothing else.

#: The largest push offered, in Angstrom of the most-moved atom. Past this a
#: mode is no longer a small displacement and the result is a new structure.
MAX_MODE_SHIFT = 2.0


def displace_along_mode(text: str, symbols, coords, vector, amplitude) -> dict:
    """The coordinates pushed along one mode.

    `symbols` / `coords` (Angstrom) are the geometry the mode was computed at
    (the .hess's own) and `vector` its 3N Cartesian direction. `amplitude` is
    how far the MOST-moved atom goes, in Angstrom, and its sign the direction
    (+ along the vector, - against it): a mode's vector is normalized over all
    atoms, so its own scale says nothing a reader can picture, while "the
    furthest atom moves 0.2 A" does.

    `text` is the coordinate box. When it holds atoms they must be the .hess's
    atoms in the .hess's order - a mode is a list of per-atom arrows, and
    applied to another ordering it pushes the wrong atoms - and only the moved
    lines are rewritten (_rewrite). An empty box gets the .hess's own geometry,
    pushed. Returns {ok, error, xyz, moved, max_shift, atom}."""
    out = {"ok": False, "error": "", "xyz": "", "moved": [], "max_shift": 0.0, "atom": ""}
    n = len(symbols)
    if len(vector) != 3 * n or len(coords) != n:
        out["error"] = "That mode does not belong to this Hessian's atoms."
        return out
    try:
        amp = float(amplitude)
    except (TypeError, ValueError):
        out["error"] = "The push is not a number."
        return out
    if not math.isfinite(amp) or abs(amp) > MAX_MODE_SHIFT:
        out["error"] = f"Push at most {MAX_MODE_SHIFT:g} Å."
        return out
    lens = [_norm(tuple(vector[3 * i:3 * i + 3])) for i in range(n)]
    peak = max(lens) if lens else 0.0
    if peak < 1e-12:
        out["error"] = ("That mode moves nothing - it is a translation or a "
                        "rotation, not a vibration.")
        return out
    scale = amp / peak

    rows = _atom_lines(text)
    if rows:
        if len(rows) != n:
            out["error"] = (f"The coordinates have {len(rows)} atoms and the Hessian "
                            f"{n}. Load the structure the frequencies were computed "
                            f"for, or clear the box to use the Hessian's own.")
            return out
        for i, (_, a) in enumerate(rows):
            if normalize_symbol(a.sym) != normalize_symbol(symbols[i]):
                out["error"] = (f"Atom {i + 1} is {a.sym} in the coordinates and "
                                f"{symbols[i]} in the Hessian: the mode would push "
                                f"the wrong atoms. The order has to match.")
                return out
        base = [_pos(a) for _, a in rows]
        syms = [a.sym for _, a in rows]
    else:
        base = [tuple(c) for c in coords]
        syms = list(symbols)

    moved_atoms = []
    for i in range(n):
        v = vector[3 * i:3 * i + 3]
        moved_atoms.append(Atom(syms[i], base[i][0] + scale * v[0],
                                base[i][1] + scale * v[1], base[i][2] + scale * v[2]))
    moved = {i for i in range(n) if lens[i] * abs(scale) > 1e-9}
    if rows:
        xyz = _rewrite(text, rows, moved_atoms, moved)
    else:
        xyz = "\n".join(f"{a.sym:<2s} {a.x:14.8f} {a.y:14.8f} {a.z:14.8f}"
                        for a in moved_atoms)
    top = max(range(n), key=lambda i: lens[i])
    out.update(ok=True, xyz=xyz, moved=sorted(moved), max_shift=abs(amp),
               atom=f"{normalize_symbol(syms[top]) or syms[top]}{top + 1}")
    return out
