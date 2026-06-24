"""
MLIP environment detection.

ORCAdesk does NOT install the MLIP toolchain. The user creates their own Python
environment (PyTorch + mace-torch + ASE) and points ORCAdesk at its interpreter;
this module probes that interpreter so the UI can show an HONEST "MLIP ready"
status — green only when the required packages actually import, not merely when
the interpreter path exists. (The common failure mode of "I ran pip myself" is a
venv that's missing a package, which a file-exists check would miss.)

Kept entirely separate from the ORCA discovery logic in ``orcamgr/config.py``.
"""

from __future__ import annotations

import subprocess
import sys
import json
from pathlib import Path


# Packages the MLIP pre-optimization workflow needs in the user's environment.
REQUIRED_PACKAGES = ("torch", "mace", "ase")

# Probe executed INSIDE the user's interpreter: import each required package,
# capture its version (or None on failure), and print a single JSON line.
# Built from REQUIRED_PACKAGES so there is one source of truth.
_PROBE = (
    "import importlib, json, sys\n"
    f"pkgs = {list(REQUIRED_PACKAGES)!r}\n"
    "out = {}\n"
    "for _name in pkgs:\n"
    "    try:\n"
    "        _m = importlib.import_module(_name)\n"
    "        out[_name] = getattr(_m, '__version__', '?')\n"
    "    except Exception:\n"
    "        out[_name] = None\n"
    "print(json.dumps({'python': sys.version.split()[0], 'packages': out}))\n"
)


def _no_window_flags() -> int:
    """Avoid a console-window flash when spawning the probe on Windows."""
    if sys.platform.startswith("win"):
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def probe_mlip(python_path: str, timeout: float = 30.0) -> dict:
    """
    Run the user's Python interpreter and check the MLIP packages import.

    Returns a plain dict:
      ready    : bool                 -- path exists AND all REQUIRED_PACKAGES import
      python   : str                  -- the configured interpreter path
      version  : str                  -- interpreter version (e.g. "3.11.5"), or ""
      packages : dict[str, str|None]  -- package -> version, or None if missing
      missing  : list[str]            -- required packages that failed to import
      error    : str | None           -- why the probe could not run at all
    """
    path = (python_path or "").strip()
    base: dict = {
        "ready": False, "python": path, "version": "",
        "packages": {}, "missing": list(REQUIRED_PACKAGES), "error": None,
    }

    if not path or not Path(path).exists():
        base["error"] = "Interpreter path is not set or does not exist."
        return base

    try:
        proc = subprocess.run(
            [path, "-c", _PROBE],
            capture_output=True, text=True, timeout=timeout,
            creationflags=_no_window_flags(),
        )
    except subprocess.TimeoutExpired:
        base["error"] = "Environment check timed out."
        return base
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        base["error"] = f"Could not launch interpreter: {e}"
        return base

    lines = (proc.stdout or "").strip().splitlines()
    try:
        data = json.loads(lines[-1]) if lines else {}
    except (json.JSONDecodeError, ValueError):
        msg = (proc.stderr or "").strip() or "Interpreter did not return a valid probe result."
        base["error"] = msg[:500]
        return base

    packages = data.get("packages", {}) or {}
    missing = [p for p in REQUIRED_PACKAGES if not packages.get(p)]
    base.update(
        version=data.get("python", ""),
        packages=packages,
        missing=missing,
        ready=(len(missing) == 0),
    )
    return base


# ---- wire-shape helpers (MlipStatusPayload in state/schemas.py) -------------
# These build the dict the bridge serializes to the front-end. Kept here so the
# MLIP status vocabulary lives with the MLIP code, not in the bridge.

def status_from_probe(probe: dict) -> dict:
    """Map a :func:`probe_mlip` result to the MlipStatusPayload wire shape."""
    if probe.get("ready"):
        mace_v = (probe.get("packages") or {}).get("mace") or "?"
        return {
            "state": "ready",
            "python": probe.get("python", ""),
            "version": probe.get("version", ""),
            "label": f"mace {mace_v}",
            "missing": [],
            "message": "All required packages available.",
        }
    missing = probe.get("missing") or []
    if probe.get("error"):
        message = probe["error"]
    elif missing:
        message = "Missing: " + ", ".join(missing)
    else:
        message = "Environment not ready."
    return {
        "state": "error",
        "python": probe.get("python", ""),
        "version": probe.get("version", ""),
        "label": "",
        "missing": list(missing),
        "message": message,
    }


def unset_status() -> dict:
    """Status when no MLIP interpreter is configured."""
    return {
        "state": "unset", "python": "", "version": "", "label": "",
        "missing": list(REQUIRED_PACKAGES),
        "message": "MLIP interpreter not set.",
    }


def checking_status(python_path: str) -> dict:
    """Status while a probe is running in the background."""
    return {
        "state": "checking", "python": python_path or "", "version": "",
        "label": "", "missing": [], "message": "Checking environment…",
    }
