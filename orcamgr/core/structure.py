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

Everything here READS. There is deliberately no structure editing: building and
modifying molecules is what a molecular editor is for, Avogadro2 and its peers
do it properly, and a side feature here could only be a worse one. ORCAdesk's
job is to tell you what the file you are about to run contains.

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
