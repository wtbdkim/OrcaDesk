"""
MLIP environment detection.

ORCAdesk does NOT install any MLIP toolchain. The user registers one or more
Python environments — *one per MLIP*, because different MLIPs pin conflicting
dependency versions (e.g. MACE and SevenNet pin different ``e3nn``), so a single
shared env would break. ORCAdesk just points at each environment's interpreter
and probes it so the UI can show an HONEST readiness status — green only when
the packages actually import, not merely when the interpreter path exists. (The
common failure mode of "I ran pip myself" is a venv missing a package, which a
file-exists check would miss.)

Detection is **auto**: the probe imports every MLIP package it knows about
(:data:`MLIP_BACKENDS`) in the user's interpreter and reports which ones are
present, so one registered env naturally surfaces whatever backends it contains.

Kept entirely separate from the ORCA discovery logic in ``orcamgr/config.py``.
"""

from __future__ import annotations

import subprocess
import sys
import json
import uuid
from pathlib import Path

from ..core.procutil import no_window_flags


# Dependencies every MLIP environment needs regardless of which backend it hosts.
COMMON_PACKAGES = ("torch", "ase")

# Known MLIP backends: key -> {label, package}. ``package`` is the import name
# that proves the backend is installed; ``label`` is what the UI shows. The probe
# imports each of these and reports the ones that load, so detection is automatic
# (the user never declares which MLIP an env contains). Extend by adding a row —
# verify the import name against the actual installed package.
MLIP_BACKENDS = {
    "mace":     {"label": "MACE",     "package": "mace"},
    "sevennet": {"label": "SevenNet", "package": "sevenn"},
}


def new_env_id() -> str:
    """Stable short id for a registered MLIP environment."""
    return uuid.uuid4().hex[:8]


def resolve_interpreter(path: str) -> str:
    """Normalize a user-supplied interpreter path. A very common mistake is to
    point at the env *folder* (e.g. ``C:\\Program Files\\Python312``) instead of
    the interpreter inside it — launching a directory raises WinError 5. If given
    a directory, find the python executable within it. Returns the resolved path,
    or the original (stripped of surrounding quotes) when nothing better is found,
    so the caller's existence/probe checks still produce a clear error."""
    p = (path or "").strip().strip('"')
    if not p:
        return p
    pp = Path(p)
    if pp.is_dir():
        if sys.platform.startswith("win"):
            cands = [pp / "python.exe", pp / "Scripts" / "python.exe"]
        else:
            cands = [pp / "bin" / "python3", pp / "bin" / "python", pp / "python"]
        for c in cands:
            if c.exists():
                return str(c)
    return p


def _probe_names() -> list[str]:
    """All import names the probe checks: the common deps plus every backend's
    package, de-duplicated, common-first."""
    names = list(COMMON_PACKAGES)
    for spec in MLIP_BACKENDS.values():
        if spec["package"] not in names:
            names.append(spec["package"])
    return names


# Probe executed INSIDE the user's interpreter: import each name, capture its
# version (or None on failure), and report whether torch sees a CUDA GPU (so the
# UI can offer GPU execution honestly). Print a single JSON line. Built from the
# registry so there is one source of truth.
_PROBE = (
    "import importlib, json, sys\n"
    f"names = {_probe_names()!r}\n"
    "out = {}\n"
    "for _n in names:\n"
    "    try:\n"
    "        _m = importlib.import_module(_n)\n"
    "        out[_n] = getattr(_m, '__version__', '?')\n"
    "    except Exception:\n"
    "        out[_n] = None\n"
    "cuda = None\n"
    "cuda_name = ''\n"
    "try:\n"
    "    import torch as _t\n"
    "    cuda = bool(_t.cuda.is_available())\n"
    "    if cuda:\n"
    "        cuda_name = str(_t.cuda.get_device_name(0))\n"
    "except Exception:\n"
    "    cuda = None\n"
    "print(json.dumps({'python': sys.version.split()[0], 'packages': out,"
    " 'cuda': cuda, 'cuda_name': cuda_name}))\n"
)


def probe_env(python_path: str, timeout: float = 30.0) -> dict:
    """
    Run the user's Python interpreter and check which MLIP packages import.

    Returns a plain dict:
      python         : str                 -- the configured interpreter path
      version        : str                 -- interpreter version ("3.11.5"), or ""
      packages       : dict[str, str|None] -- import name -> version, or None
      common_missing : list[str]           -- of COMMON_PACKAGES, which failed
      backends       : list[dict]          -- detected: {key, label, version}
      cuda           : bool | None          -- torch sees a CUDA GPU (None = unknown)
      cuda_name      : str                  -- GPU name when cuda is True
      ready          : bool                -- all common deps import AND >=1 backend
      error          : str | None          -- why the probe could not run at all
    """
    path = (python_path or "").strip()
    base: dict = {
        "python": path, "version": "", "packages": {},
        "common_missing": list(COMMON_PACKAGES), "backends": [],
        "cuda": None, "cuda_name": "",
        "ready": False, "error": None,
    }

    if not path or not Path(path).exists():
        base["error"] = "Interpreter path is not set or does not exist."
        return base
    if Path(path).is_dir():
        base["error"] = ("That path is a folder, not a Python interpreter — "
                         "point at the python.exe inside it.")
        return base

    try:
        proc = subprocess.run(
            [path, "-c", _PROBE],
            capture_output=True, text=True, timeout=timeout,
            creationflags=no_window_flags(),
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
    if not isinstance(data, dict):
        # valid JSON but not the probe's object (a wrapper script or
        # sitecustomize printing after our line) — treat like unparseable
        base["error"] = "Interpreter did not return a valid probe result."
        return base

    packages = data.get("packages", {}) or {}
    common_missing = [p for p in COMMON_PACKAGES if not packages.get(p)]
    backends = [
        {"key": key, "label": spec["label"], "version": packages[spec["package"]]}
        for key, spec in MLIP_BACKENDS.items()
        if packages.get(spec["package"])
    ]
    base.update(
        version=data.get("python", ""),
        packages=packages,
        common_missing=common_missing,
        backends=backends,
        cuda=data.get("cuda"),
        cuda_name=data.get("cuda_name", "") or "",
        ready=(not common_missing and bool(backends)),
    )
    return base


# ---- wire-shape helpers (MlipEnvPayload / MlipStatusPayload in schemas.py) ----
# These build the dicts the bridge serializes to the front-end. Kept here so the
# MLIP status vocabulary lives with the MLIP code, not in the bridge.

def env_payload_checking(env_id: str, name: str, python: str) -> dict:
    """MlipEnvPayload while a probe is running in the background."""
    return {
        "id": env_id, "name": name, "python": python,
        "state": "checking", "version": "", "backends": [],
        "cuda": None, "cuda_name": "",
        "message": "Checking environment…",
    }


def env_payload_error(env_id: str, name: str, python: str, message: str) -> dict:
    """MlipEnvPayload for a probe that crashed (rather than reporting a
    not-ready env) — the bridge publishes this so the env never sticks at
    "Checking…" with a dead worker behind it."""
    return {
        "id": env_id, "name": name, "python": python,
        "state": "error", "version": "", "backends": [],
        "cuda": None, "cuda_name": "",
        "message": message[:500],
    }


def env_payload_from_probe(env_id: str, name: str, probe: dict) -> dict:
    """Map a :func:`probe_env` result to the MlipEnvPayload wire shape."""
    backends = probe.get("backends") or []
    cuda = probe.get("cuda")
    cuda_name = probe.get("cuda_name", "") or ""
    if probe.get("ready"):
        state = "ready"
        message = "Ready: " + ", ".join(f"{b['label']} {b['version']}" for b in backends)
        # surface the compute device so the user knows a freq job won't crawl on CPU
        if cuda:
            message += f" · GPU: {cuda_name}" if cuda_name else " · GPU available"
        elif cuda is False:
            message += " · CPU only"
    else:
        state = "error"
        common_missing = probe.get("common_missing") or []
        if probe.get("error"):
            message = probe["error"]
        elif common_missing:
            message = "Missing core packages: " + ", ".join(common_missing)
        elif not backends:
            known = ", ".join(spec["label"] for spec in MLIP_BACKENDS.values())
            message = f"No MLIP package found (need one of: {known})."
        else:
            message = "Environment not ready."
    return {
        "id": env_id, "name": name, "python": probe.get("python", ""),
        "state": state, "version": probe.get("version", ""),
        "backends": backends, "cuda": cuda, "cuda_name": cuda_name,
        "message": message,
    }


# Which env a calc with no explicit ``mlip_env_id`` runs in. The engine only
# ever receives {id, name, python} from Settings -- readiness is a live probe
# result that lives on the Bridge -- so "first ready" has to be expressed as an
# ORDER on the list handed to the engine, which then keeps taking envs[0].
_READINESS_RANK = {"ready": 0, "checking": 1, "error": 2}


def order_envs_by_readiness(envs: list[dict], states: dict) -> list[dict]:
    """Order registered envs [{id, name, python}] so a **ready** one comes
    first, honouring the documented ``mlip_env_id == "" -> first ready env``
    contract (`StepConfig.mlip_env_id`).

    ``states`` maps env id -> probe state ("ready"/"checking"/"error"); an id
    with no probe yet ranks with "checking" -- unproven, but not disproven.
    The sort is **stable**, so registration order breaks ties and the common
    single-env case is untouched. Nothing is dropped: an all-error list still
    runs (and fails) with a real interpreter and a real error message, rather
    than the engine's "no MLIP environment registered".
    """
    return sorted(envs, key=lambda e: _READINESS_RANK.get(
        states.get(e.get("id"), "checking"), 1))


def aggregate_status(envs: list[dict]) -> dict:
    """Roll a list of MlipEnvPayload up into the top-bar MlipStatusPayload.
    Aggregate state: ``ready`` if any env is ready, else ``checking`` if any is
    still probing, else ``error`` if envs exist but none are ready, else
    ``unset`` when nothing is registered."""
    if not envs:
        return {"state": "unset", "envs": []}
    if any(e["state"] == "ready" for e in envs):
        state = "ready"
    elif any(e["state"] == "checking" for e in envs):
        state = "checking"
    else:
        state = "error"
    return {"state": state, "envs": envs}
