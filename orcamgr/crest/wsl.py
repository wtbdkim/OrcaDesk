"""
Low-level WSL helpers shared by the CREST runner, environment probe, and
installer. Kept dependency-free (no PyQt, no core imports) so it stays testable.

Key facts baked in (verified on Windows 11 / WSL 2.6.1.0):
* ``wsl.exe`` management output (``--list``) is UTF-16LE unless ``WSL_UTF8=1`` is
  set in the environment — we always set it.
* The default distro can be an infrastructure distro (``docker-desktop``) that
  has no bash and mounts drives differently, so we NEVER trust the default:
  callers enumerate real distros here and pass ``-d <distro>`` explicitly.
* ``wsl.exe`` propagates the Linux command's exit code; its own launch failures
  print a ``Wsl/...`` error token and a nonzero code.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Tuple

# Infrastructure distros that aren't a usable general-purpose Linux — filter them
# out of the candidate list (they lack bash / mount drives differently).
_INFRA_DISTROS = {
    "docker-desktop", "docker-desktop-data",
    "rancher-desktop", "rancher-desktop-data",
    "podman-machine-default",
}


def _no_window_flags() -> int:
    return (getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if sys.platform.startswith("win") else 0)


def _wsl_env() -> dict:
    env = dict(os.environ)
    env["WSL_UTF8"] = "1"
    return env


def wsl_available() -> bool:
    """True if wsl.exe can be invoked at all."""
    try:
        p = subprocess.run(["wsl.exe", "--status"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=15,
                           creationflags=_no_window_flags(), env=_wsl_env())
        return p.returncode == 0 or bool((p.stdout or "").strip())
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return False


def list_distros(include_infra: bool = False) -> list[str]:
    """Return installed WSL distro names (UTF-8 via WSL_UTF8), infrastructure
    distros filtered out unless ``include_infra``. Empty list if WSL is absent."""
    try:
        p = subprocess.run(["wsl.exe", "--list", "--quiet"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=15,
                           creationflags=_no_window_flags(), env=_wsl_env())
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return []
    names = []
    for line in (p.stdout or "").splitlines():
        n = line.strip().strip("\x00").replace("﻿", "")
        if not n:
            continue
        if not include_infra and n.lower() in _INFRA_DISTROS:
            continue
        names.append(n)
    return names


def run_bash(distro: str, bash_cmd: str, timeout: float = 20.0,
             login: bool = False) -> Tuple[int, str, str]:
    """Run ``bash -c <bash_cmd>`` in ``distro``; return (rc, stdout, stderr).
    ``-e bash`` execs bash directly (NOT a login shell by default — fast, no
    profile side effects); ``login=True`` adds ``-l`` so /etc/profile and
    ~/.profile are sourced, which the CREST probe needs to see a user-managed
    PATH (conda, ~/.local/bin on distros that add it there). Never raises on
    nonzero rc; returns (127, "", "wsl-not-found") if wsl.exe is missing,
    (124, ...) on timeout."""
    argv = ["bash", "-lc", bash_cmd] if login else ["bash", "-c", bash_cmd]
    cmd = ["wsl.exe", "-d", distro, "-e", *argv]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout,
                           creationflags=_no_window_flags(), env=_wsl_env())
    except FileNotFoundError:
        return 127, "", "wsl-not-found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except (subprocess.SubprocessError, OSError) as e:
        # Same envelope as wsl_available/list_distros in this module. Anything
        # else escaping here lands in CrestRunner._liveness, whose entire
        # purpose is that "a single transient wsl.exe failure must not abandon a
        # healthy run" — an exception walks straight past that guard, out of
        # monitor(), and FAILS (and therefore locks, P24) a CREST job that is
        # still running happily in WSL. Errors are data here (P6).
        return 125, "", f"wsl-error: {e}"
    return p.returncode, (p.stdout or ""), (p.stderr or "")
