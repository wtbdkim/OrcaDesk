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
    mol = Chem.Mol(raw)
    try:
        rdDetermineBonds.DetermineBonds(mol, charge=charge)
        Chem.Kekulize(mol, clearAromaticFlags=True)
    except Exception as e:
        method = "connectivity"
        msg = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        note = ("Bond orders could not be determined (" + msg[:160] + "); "
                "every bond is drawn single.")
        mol = Chem.Mol(raw)
        rdDetermineBonds.DetermineConnectivity(mol)
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
        if method == "connectivity":
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
