"""
Detect a usable CREST environment: somewhere with the ``crest`` binary in it.

"Somewhere" is what ``shell.py`` decides — a WSL distro on Windows, this machine
on Linux — and this module is written against that abstraction rather than
against WSL, so the probe, the indicator and the run target are one piece of
logic on both platforms. A *target* is therefore a distro name or
``shell.LOCAL_TARGET``; the payload keeps calling it ``distro`` because that is
the wire field the desktop and the phone already speak.

Backs the top-bar "CREST ready" indicator (polled from the Bridge, like the MLIP
indicator). Unlike ``orca_is_valid()`` (a file-exists check), CREST readiness is
probed by actually running ``crest --version`` on each candidate target. The
probe is intentionally cheap (a single transport call per target) but still runs
off the UI thread on the Bridge.

The installer (``installer.py``) places the binary at
``~/.local/opt/crest/crest/crest`` and symlinks ``~/.local/bin/crest``; the probe
finds it there or anywhere on the login-shell PATH — which on Linux is also
where a distro package or a conda install of CREST already lives, so a user who
set CREST up themselves is detected with nothing to install.
"""

from __future__ import annotations

from .shell import (
    available as shell_available, is_local, list_targets,
    missing_message, run_bash, transport_kind,
)

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
    """Probe one target for CREST (a WSL distro, or this machine). Returns
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
        result["error"] = ("CREST not found on this machine." if is_local()
                           else "CREST not found in this distro.")
    return result


def probe_all(timeout: float = 30.0) -> list[dict]:
    """Probe every candidate target for CREST (WSL distros, or this machine)."""
    return [probe_distro(d, timeout=timeout) for d in list_targets()]


def aggregate_status(target_probes: list[dict]) -> dict:
    """Aggregate per-target probes into the top-bar status payload:
    {state, distros, wsl, transport}. state is 'ready' if any target has CREST,
    else 'unset' if there is no target at all (no WSL / no distro / no bash),
    else 'error'.

    ``wsl`` is kept as the field name — it is what the desktop and the phone
    already read — but it means "the transport is available", so a Linux machine
    with a working shell reports True. ``transport`` says which one it is, so
    the UI can word "no WSL distribution" and "no bash" as the different
    problems they are."""
    kind = transport_kind()
    if not shell_available():
        return {"state": "unset", "distros": [], "wsl": False, "transport": kind}
    if not target_probes:
        return {"state": "unset", "distros": [], "wsl": True, "transport": kind}
    state = "ready" if any(d.get("ready") for d in target_probes) else "error"
    return {"state": state, "distros": target_probes, "wsl": True, "transport": kind}


def no_target_message() -> str:
    """Why there is nowhere to run CREST. On Windows that is a missing distro
    the user must create themselves (D41: the one step ORCAdesk cannot script);
    locally the transport IS the target, so reaching here means bash is gone."""
    if is_local():
        return missing_message()
    return ("No usable WSL distribution found (only infrastructure distros like "
            "docker-desktop). Install a Linux distro, e.g. `wsl --install -d Ubuntu`.")


def not_installed_message() -> str:
    """Why the targets that exist cannot run CREST."""
    where = "on this machine" if is_local() else "in any WSL distribution"
    return (f"CREST is not installed {where}. Install it from "
            "Settings → CREST, or run the auto-installer.")


def resolve_run_target(preferred_distro: str = "", timeout: float = 30.0) -> tuple[str, str]:
    """Pick the (target, crest_bin) to run a CREST calc on: the preferred target
    if it has CREST, else the first one that does. Raises RuntimeError with a
    user-facing message if none is usable."""
    if not shell_available():
        raise RuntimeError(missing_message())
    targets = list_targets()
    if not targets:
        raise RuntimeError(no_target_message())
    order = ([preferred_distro] + [d for d in targets if d != preferred_distro]
             if preferred_distro else targets)
    for d in order:
        if not d:
            continue
        path, _ = resolve_crest_bin(d, timeout=timeout)
        if path:
            return d, path
    raise RuntimeError(not_installed_message())
