"""
The RDKit worker: bond perception for one structure, run in the USER's
environment, never imported here.

Protocol -- one JSON object each way:

  stdin   {"atoms": [{"el": "C", "x": 0.0, "y": 0.0, "z": 0.0}, ...],
           "charge": 0, "multiplicity": 1}
  stdout  {"ok": true, "rdkit": "2026.09.2",
           "method": "bonds" | "connectivity",
           "note": "",                       # why bond orders are missing, or ""
           "bonds": [[i, j, order], ...],    # 0-based atom indices, order 1/2/3
           "charges": [0, ...],              # formal charge per atom
           "radicals": [0, ...],             # unpaired electrons per atom
           "stereocentres": [i, ...]}        # tetrahedral centres (RDKit's view)
          {"ok": false, "error": "..."}      # could not even read the atoms

Bond orders come from ``rdDetermineBonds.DetermineBonds(mol, charge=...)``.
When that cannot assign them -- a transition state, a metal complex, a charge
that does not fit -- the worker falls back to ``DetermineConnectivity`` and
every bond single, and SAYS so in ``note`` (method "connectivity"): a drawing
with invented double bonds would be worse than one that admits it has none.

The script is parameter-free (inputs arrive on stdin, nothing is formatted into
it) and is run with ``python -c``: there is no file to find, bundle or write,
which is what keeps it working in a frozen build where package sources do not
exist on disk.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Optional

from ..core.procutil import no_window_flags
from .env import last_json_line

# Bond perception is fast for a molecule (well under a second after the
# import), but DetermineBondOrders is combinatorial on large conjugated or
# odd systems; a generous ceiling keeps a pathological structure from hanging
# the panel forever.
DEFAULT_TIMEOUT = 90.0
# Beyond this many atoms the drawing is unreadable anyway, and bond-order
# assignment can run for minutes. Refused before anything is launched.
MAX_ATOMS = 500

WORKER_SCRIPT = r'''
import sys, json

def main():
    req = json.loads(sys.stdin.read() or "{}")
    atoms = req.get("atoms") or []
    charge = int(req.get("charge") or 0)
    from rdkit import Chem, rdBase
    from rdkit.Chem import rdDetermineBonds
    rdBase.DisableLog("rdApp.*")
    lines = [str(len(atoms)), "orcadesk"]
    for a in atoms:
        lines.append("%s %.6f %.6f %.6f" % (a["el"], float(a["x"]), float(a["y"]), float(a["z"])))
    raw = Chem.MolFromXYZBlock("\n".join(lines) + "\n")
    if raw is None:
        print(json.dumps({"ok": False, "error": "RDKit could not read these atoms."}))
        return
    method, note = "bonds", ""
    pt = Chem.GetPeriodicTable()
    ORD = {1: Chem.BondType.SINGLE, 2: Chem.BondType.DOUBLE, 3: Chem.BondType.TRIPLE}

    def tidy(m):
        # xyz2mol leaves charge-separated pairs (S2+ with O-, C+ next to C-):
        # each bond between opposite charges becomes one order higher with
        # both charges a step nearer zero -- only while the result sanitizes
        # and the total |charge| strictly drops (nitro's N+/O- stays). Pairs
        # with a terminal atom first, so a sulfonyl becomes S=O, not N=S.
        rw = Chem.RWMol(m)
        while True:
            tot = sum(abs(a.GetFormalCharge()) for a in rw.GetAtoms())
            cands = []
            for b in rw.GetBonds():
                a, c = b.GetBeginAtom(), b.GetEndAtom()
                if a.GetFormalCharge() * c.GetFormalCharge() >= 0 or b.GetBondTypeAsDouble() >= 3:
                    continue
                cands.append((0 if min(a.GetDegree(), c.GetDegree()) == 1 else 1, b.GetIdx()))
            moved = False
            for _, bi in sorted(cands):
                t = Chem.RWMol(rw)
                tb = t.GetBondWithIdx(bi)
                tb.SetBondType(ORD[int(round(tb.GetBondTypeAsDouble())) + 1])
                for at in (tb.GetBeginAtom(), tb.GetEndAtom()):
                    q = at.GetFormalCharge()
                    at.SetFormalCharge(q - (1 if q > 0 else -1))
                try:
                    Chem.SanitizeMol(t)
                    Chem.Kekulize(t, clearAromaticFlags=True)
                except Exception:
                    continue
                if sum(abs(a.GetFormalCharge()) for a in t.GetAtoms()) < tot:
                    rw, moved = t, True
                    break
            if not moved:
                return rw.GetMol()

    # Partial bonds (a transition state's forming / breaking bonds) are far
    # longer than covalent: over PARTIAL x the covalent-radius sum. Measured on
    # an NBS bromination TS: N...Br 1.16, Br...C 1.18, every real bond <= 1.05.
    # Bond orders assigned THROUGH them come out as a spray of formal charges
    # (20 on a neutral molecule), so they are left out of the assignment, drawn
    # as plain lines, and their atoms' charges are not shown.
    PARTIAL = 1.10
    conn = Chem.Mol(raw)
    rdDetermineBonds.DetermineConnectivity(conn)
    pos = conn.GetConformer()
    partial = []
    for b in conn.GetBonds():
        i, j = b.GetBeginAtomIdx(), b.GetEndAtomIdx()
        rc = (pt.GetRcovalent(conn.GetAtomWithIdx(i).GetAtomicNum())
              + pt.GetRcovalent(conn.GetAtomWithIdx(j).GetAtomicNum()))
        if rc > 0 and (pos.GetAtomPosition(i) - pos.GetAtomPosition(j)).Length() / rc > PARTIAL:
            partial.append((i, j))
    core = Chem.RWMol(conn)
    for i, j in partial:
        core.RemoveBond(i, j)
    core = core.GetMol()
    # without the partial bonds the fragments' charges no longer have to add
    # up to the molecule's: the molecule's first, then the nearest others
    tries = [charge] if not partial else [charge, charge - 1, charge + 1, charge - 2, charge + 2]
    mol, err = None, ""
    for c in tries:
        trial = Chem.Mol(core)
        try:
            rdDetermineBonds.DetermineBondOrders(trial, charge=c)
            Chem.Kekulize(trial, clearAromaticFlags=True)
        except Exception as e:
            err = err or (str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__)
            continue
        mol = tidy(trial)
        break
    ends = set(k for pair in partial for k in pair)
    if mol is not None:
        # what is left beyond the molecule's own charge, away from the partial
        # bonds, means the assignment is not a Lewis structure worth drawing
        left = sum(abs(a.GetFormalCharge()) for a in mol.GetAtoms() if a.GetIdx() not in ends)
        if left > abs(charge) + 2:
            err, mol = "it needs %d formal charges on a molecule of charge %d" % (left, charge), None
    if mol is None:
        method = "connectivity"
        note = ("Bond orders could not be determined (" + err[:160] + "); "
                "every bond is drawn single.")
        mol = Chem.Mol(raw)
        rdDetermineBonds.DetermineConnectivity(mol)
    else:
        rw = Chem.RWMol(mol)
        for i, j in partial:
            rw.AddBond(i, j, Chem.BondType.SINGLE)
        for k in ends:
            rw.GetAtomWithIdx(k).SetFormalCharge(0)
            rw.GetAtomWithIdx(k).SetNumRadicalElectrons(0)
        mol = rw.GetMol()
        if partial:
            sym = lambda k: mol.GetAtomWithIdx(k).GetSymbol() + str(k + 1)
            note = ("Partial bonds (" + ", ".join(sym(i) + "..." + sym(j) for i, j in partial)
                    + ", longer than 1.10 x covalent) are drawn as plain lines, left out of "
                    "the bond-order assignment; their atoms' charges are not shown.")
    bonds = []
    for b in mol.GetBonds():
        o = b.GetBondTypeAsDouble() if method == "bonds" else 1.0
        o = int(round(o)) if o in (1.0, 2.0, 3.0) else 1
        bonds.append([b.GetBeginAtomIdx(), b.GetEndAtomIdx(), o])
    charges = [a.GetFormalCharge() if method == "bonds" else 0 for a in mol.GetAtoms()]
    radicals = [a.GetNumRadicalElectrons() if method == "bonds" else 0 for a in mol.GetAtoms()]
    centres = []
    try:
        m2 = Chem.Mol(mol)
        if method == "connectivity" or partial:
            for a in m2.GetAtoms():
                a.SetNoImplicit(True)
            m2.UpdatePropertyCache(strict=False)
        Chem.AssignStereochemistryFrom3D(m2)
        found = Chem.FindMolChiralCenters(m2, includeUnassigned=True,
                                          useLegacyImplementation=False)
        centres = sorted(int(i) for i, _ in found)
    except Exception:
        centres = []
    print(json.dumps({"ok": True, "rdkit": rdBase.rdkitVersion, "method": method,
                      "note": note, "bonds": bonds, "charges": charges,
                      "radicals": radicals, "stereocentres": centres}))

try:
    main()
except Exception as e:
    print(json.dumps({"ok": False, "error": (type(e).__name__ + ": " + str(e))[:400]}))
'''


def clean_atoms(atoms) -> list[dict]:
    """The atoms a request may carry: element symbol + three finite numbers.
    Raises ValueError with a sentence on anything else -- this is the trust
    boundary between the front-end's payload and a subprocess's stdin."""
    if not isinstance(atoms, list) or not atoms:
        raise ValueError("No atoms to draw.")
    if len(atoms) > MAX_ATOMS:
        raise ValueError(f"{len(atoms)} atoms is more than a 2D drawing can show "
                         f"(the limit is {MAX_ATOMS}).")
    out = []
    for a in atoms:
        try:
            el = str(a["el"]).strip()
            x, y, z = float(a["x"]), float(a["y"]), float(a["z"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Unreadable atom in the request.") from None
        if not (el.isalpha() and 1 <= len(el) <= 3):
            raise ValueError(f"'{el}' is not an element symbol.")
        if not all(math.isfinite(v) for v in (x, y, z)):
            raise ValueError("An atom has a coordinate that is not a number.")
        out.append({"el": el[0].upper() + el[1:].lower(), "x": x, "y": y, "z": z})
    return out


def _check_result(res: dict, n: int) -> dict:
    """Validate what the worker printed against the atoms it was given. A
    worker from a different ORCAdesk version, or a user's sitecustomize, must
    not be able to hand the panel indices it will index out of range with."""
    if not res.get("ok"):
        return {"ok": False, "error": str(res.get("error") or "The worker failed.")[:500]}
    try:
        bonds = []
        for i, j, o in res.get("bonds") or []:
            i, j, o = int(i), int(j), int(o)
            if not (0 <= i < n and 0 <= j < n and i != j and o in (1, 2, 3)):
                raise ValueError
            bonds.append([i, j, o])
        charges = [int(c) for c in (res.get("charges") or [0] * n)]
        radicals = [int(r) for r in (res.get("radicals") or [0] * n)]
        centres = sorted({int(c) for c in res.get("stereocentres") or []
                          if 0 <= int(c) < n})
        if len(charges) != n or len(radicals) != n:
            raise ValueError
    except (TypeError, ValueError):
        return {"ok": False, "error": "The worker returned a result that does not "
                                      "fit these atoms."}
    method = "connectivity" if res.get("method") == "connectivity" else "bonds"
    return {"ok": True, "rdkit": str(res.get("rdkit") or ""), "method": method,
            "note": str(res.get("note") or "")[:400], "bonds": bonds,
            "charges": charges, "radicals": radicals, "stereocentres": centres}


def run_worker(python: str, atoms, charge: int = 0, multiplicity: int = 1,
               timeout: float = DEFAULT_TIMEOUT, script: Optional[str] = None,
               cwd: Optional[str] = None) -> dict:
    """Perceive bonds for ``atoms`` in the interpreter at ``python``.

    Blocking (a second or so, mostly RDKit's import) -- the Bridge runs it on
    a background thread. Returns the validated result dict (see the module
    docstring) or {ok: False, error}. Never raises (P6)."""
    try:
        clean = clean_atoms(atoms)
        charge = int(charge)
        multiplicity = int(multiplicity)
    except (ValueError, TypeError, OverflowError) as e:
        return {"ok": False, "error": str(e)}
    if not python or not Path(python).is_file():
        return {"ok": False, "error": "The 2D-drawing environment's interpreter "
                                      "is missing. Check Settings › 2D drawing."}
    req = json.dumps({"atoms": clean, "charge": charge,
                      "multiplicity": multiplicity})
    try:
        proc = subprocess.run(
            [python, "-c", script if script is not None else WORKER_SCRIPT],
            input=req.encode("utf-8"), capture_output=True, timeout=timeout,
            cwd=cwd, creationflags=no_window_flags())
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Bond perception took longer than "
                                      f"{int(timeout)} s and was stopped."}
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": f"Could not launch the worker: {e}"}
    # The worker writes ASCII JSON (json.dumps escapes everything else), so
    # UTF-8 with replacement is exact for the line that matters; stderr is
    # only ever shown as a message.
    out = proc.stdout.decode("utf-8", "replace")
    res = last_json_line(out, "ok")
    if res is None:
        err = proc.stderr.decode("utf-8", "replace").strip()
        tail = err.splitlines()[-1] if err else f"exit code {proc.returncode}"
        return {"ok": False, "error": f"The worker printed no result ({tail[:300]})."}
    out = _check_result(res, len(clean))
    # RDKit's Lewis structure knows nothing of the multiplicity; when the two
    # disagree the drawing's radicals are RDKit's guess, and the panel says so.
    if out.get("ok") and out["method"] == "bonds" and multiplicity >= 1:
        unpaired = sum(out["radicals"])
        if unpaired + 1 != multiplicity:
            extra = (f"RDKit's Lewis structure has {unpaired} unpaired "
                     f"electron{'s' if unpaired != 1 else ''}; the calculation's "
                     f"multiplicity is {multiplicity}.")
            out["note"] = (out["note"] + " " + extra).strip()
    return out
