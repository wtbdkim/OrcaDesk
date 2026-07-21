"""
Detect a usable CREST environment: a WSL distro that has the ``crest`` binary.

Backs the top-bar "CREST ready" indicator (polled from the Bridge, like the MLIP
indicator). Unlike ``orca_is_valid()`` (a file-exists check on Windows), CREST
lives inside WSL, so readiness is probed by running ``crest --version`` in each
candidate distro. The probe is intentionally cheap (a single ``wsl.exe`` call per
distro) but still runs off the UI thread on the Bridge.

The installer (``installer.py``) places the binary at
``~/.local/opt/crest/crest/crest`` and symlinks ``~/.local/bin/crest``; the probe
finds it there or anywhere on the login-shell PATH.
"""

from __future__ import annotations

from .wsl import list_distros, run_bash, wsl_available

# Candidate locations, checked in order. ``command -v`` first — run through a
# LOGIN shell (run_bash(login=True): plain ``wsl -e bash -c`` sources no
# profile, so a user-managed install visible only via ~/.profile / conda PATH
# would be missed) — then the paths our installer uses.
_RESOLVE_SCRIPT = (
    'p="$(command -v crest 2>/dev/null)"; '
    '[ -z "$p" ] && [ -x "$HOME/.local/bin/crest" ] && p="$HOME/.local/bin/crest"; '
    '[ -z "$p" ] && [ -x "$HOME/.local/opt/crest/crest/crest" ] && p="$HOME/.local/opt/crest/crest/crest"; '
    'if [ -n "$p" ]; then echo "CREST_BIN=$p"; "$p" --version 2>&1 | tr -d "\\r"; fi'
)


def resolve_crest_bin(distro: str, timeout: float = 30.0) -> tuple[str, str]:
    """Return (crest_bin_path, version_line) for ``distro`` — ("", "") if CREST
    isn't found. version_line is the first line of ``crest --version`` mentioning
    a version, if any."""
    rc, out, _ = run_bash(distro, _RESOLVE_SCRIPT, timeout=timeout, login=True)
    if rc != 0 or "CREST_BIN=" not in out:
        return "", ""
    path = ""
    version = ""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("CREST_BIN="):
            path = line[len("CREST_BIN="):].strip()
        elif "version" in line.lower() and not version:
            version = line
    return path, version


def probe_distro(distro: str, timeout: float = 30.0) -> dict:
    """Probe one distro for CREST. Returns
    {distro, ready, crest_bin, version, error}."""
    result = {"distro": distro, "ready": False, "crest_bin": "", "version": "", "error": ""}
    try:
        path, version = resolve_crest_bin(distro, timeout=timeout)
    except Exception as e:  # never let a probe crash the indicator
        result["error"] = f"{type(e).__name__}: {e}"
        return result
    if path:
        result["ready"] = True
        result["crest_bin"] = path
        result["version"] = version
    else:
        result["error"] = "CREST not found in this distro."
    return result


def probe_all(timeout: float = 30.0) -> list[dict]:
    """Probe every non-infrastructure WSL distro for CREST."""
    return [probe_distro(d, timeout=timeout) for d in list_distros()]


def aggregate_status(distro_probes: list[dict]) -> dict:
    """Aggregate per-distro probes into the top-bar status payload:
    {state, distros}. state is 'ready' if any distro has CREST, else 'unset' if
    no distros / WSL absent, else 'error'."""
    if not wsl_available():
        return {"state": "unset", "distros": [], "wsl": False}
    if not distro_probes:
        return {"state": "unset", "distros": [], "wsl": True}
    if any(d.get("ready") for d in distro_probes):
        state = "ready"
    else:
        state = "error"
    return {"state": state, "distros": distro_probes, "wsl": True}


def resolve_run_target(preferred_distro: str = "", timeout: float = 30.0) -> tuple[str, str]:
    """Pick the (distro, crest_bin) to run a CREST calc in: the preferred distro
    if it has CREST, else the first distro that does. Raises RuntimeError with a
    user-facing message if none is usable."""
    if not wsl_available():
        raise RuntimeError("WSL is not available. Install WSL to run CREST calculations.")
    distros = list_distros()
    if not distros:
        raise RuntimeError(
            "No usable WSL distribution found (only infrastructure distros like "
            "docker-desktop). Install a Linux distro, e.g. `wsl --install -d Ubuntu`.")
    order = ([preferred_distro] + [d for d in distros if d != preferred_distro]
             if preferred_distro else distros)
    for d in order:
        if not d:
            continue
        path, _ = resolve_crest_bin(d, timeout=timeout)
        if path:
            return d, path
    raise RuntimeError(
        "CREST is not installed in any WSL distribution. Install it from "
        "Settings → CREST, or run the auto-installer.")
