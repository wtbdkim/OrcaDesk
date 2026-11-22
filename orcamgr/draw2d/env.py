"""
The 2D-drawing environment: one registered Python interpreter that has RDKit.

Readiness is an IMPORT probe, as for the MLIP environments
(``orcamgr/mlip/env.py``): green only when ``import rdkit`` and the module the
worker actually uses (``rdkit.Chem.rdDetermineBonds``) load in that
interpreter, never merely because the path exists. One environment, not a
list: RDKit has no backends to keep apart, so a second one would only be a
second way to be out of date.

States (DESIGN.md D42): ``unset`` (nothing registered), ``checking`` (a probe
is in flight), ``ready``, ``error`` (registered, but it does not import --
the message says why).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..core.procutil import no_window_flags
from ..mlip.env import resolve_interpreter  # noqa: F401  (re-exported: same rule)


# The import that proves the worker can run. rdDetermineBonds arrived in RDKit
# 2022.09; an older RDKit imports fine and still cannot perceive bond orders,
# which is exactly the "green but broken" a file-exists check would give.
PROBE_MODULES = ("rdkit", "rdkit.Chem.rdDetermineBonds")

_PROBE = (
    "import importlib, json, sys\n"
    "out = {'python': sys.version.split()[0], 'rdkit': None, 'missing': []}\n"
    f"for _n in {list(PROBE_MODULES)!r}:\n"
    "    try:\n"
    "        _m = importlib.import_module(_n)\n"
    "        if _n == 'rdkit':\n"
    "            out['rdkit'] = getattr(_m, '__version__', '?')\n"
    "    except Exception as _e:\n"
    "        out['missing'].append(_n + ': ' + str(_e)[:200])\n"
    "print(json.dumps(out))\n"
)


def last_json_line(text: str, key: str):
    """The last line of ``text`` that is a JSON object holding ``key``, or None.

    Scanned backwards for the same reason the MLIP probe is: a conda banner,
    an atexit hook or a sitecustomize print can follow the line we want."""
    for ln in reversed((text or "").strip().splitlines()):
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            obj = json.loads(ln)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict) and key in obj:
            return obj
    return None


def probe_env(python_path: str, timeout: float = 30.0) -> dict:
    """Run the interpreter and check that RDKit imports.

    Returns {python, version, rdkit, ready, error}. Never raises (P6)."""
    path = (python_path or "").strip()
    base = {"python": path, "version": "", "rdkit": "", "ready": False,
            "error": None}
    if not path or not Path(path).exists():
        base["error"] = "Interpreter path is not set or does not exist."
        return base
    if Path(path).is_dir():
        base["error"] = ("That path is a folder, not a Python interpreter — "
                         "point at the python executable inside it.")
        return base
    try:
        proc = subprocess.run([path, "-c", _PROBE], capture_output=True,
                              text=True, timeout=timeout,
                              creationflags=no_window_flags())
    except subprocess.TimeoutExpired:
        base["error"] = "Environment check timed out."
        return base
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        base["error"] = f"Could not launch interpreter: {e}"
        return base
    # Non-zero exit = the probe never ran (a venv whose base Python is gone
    # prints "No Python at ..." and exits 103) -- not "RDKit missing".
    if proc.returncode != 0:
        msg = ((proc.stderr or "").strip() or (proc.stdout or "").strip()
               or f"Interpreter exited with code {proc.returncode}.")
        base["error"] = msg[:500]
        return base
    data = last_json_line(proc.stdout, "missing")
    if data is None:
        base["error"] = ((proc.stderr or "").strip()
                         or "Interpreter did not return a valid probe result.")[:500]
        return base
    base["version"] = str(data.get("python") or "")
    base["rdkit"] = str(data.get("rdkit") or "")
    missing = data.get("missing") or []
    if not base["rdkit"]:
        base["error"] = "RDKit is not installed in this Python (pip install rdkit)."
    elif missing:
        base["error"] = (f"RDKit {base['rdkit']} is too old for bond perception "
                         "(rdDetermineBonds needs RDKit 2022.09 or newer).")
    else:
        base["ready"] = True
    return base


# ---- wire shape (Draw2dStatusPayload's env half, state/schemas.py) ----------

def status_unset() -> dict:
    return {"state": "unset", "python": "", "version": "", "rdkit": "",
            "message": "Not set — 2D drawing needs a Python environment with RDKit."}


def status_checking(python: str) -> dict:
    return {"state": "checking", "python": python, "version": "", "rdkit": "",
            "message": "Checking environment…"}


def status_from_probe(probe: dict) -> dict:
    if probe.get("ready"):
        return {"state": "ready", "python": probe.get("python", ""),
                "version": probe.get("version", ""), "rdkit": probe.get("rdkit", ""),
                "message": f"Ready: RDKit {probe.get('rdkit', '')}"}
    return {"state": "error", "python": probe.get("python", ""),
            "version": probe.get("version", ""), "rdkit": probe.get("rdkit", ""),
            "message": (probe.get("error") or "Environment not ready.")[:500]}


def status_error(python: str, message: str) -> dict:
    return {"state": "error", "python": python, "version": "", "rdkit": "",
            "message": message[:500]}
