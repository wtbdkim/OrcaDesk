"""
Where CREST's POSIX shell lives on this machine.

CREST ships a statically linked **Linux** binary and has no native Windows
build, so ORCAdesk has always run it through WSL. On Linux that indirection is
not just unnecessary, it is impossible — there is no ``wsl.exe`` — and the very
same binary runs directly. This module is the one place that decides which of
the two it is, so ``env.py`` / ``installer.py`` / ``runner.py`` can be written
once against "a bash somewhere".

**The transport is detected, never configured.** A Windows/Linux switch in
Settings would be a setting the user can only get *wrong*: the platform already
knows the answer, and no machine is both. What genuinely deserves a setting is
*which* target — which WSL distro on Windows — and that is
``Settings.crest_distro``, unchanged. On a local transport there is exactly one
target, :data:`LOCAL_TARGET`, so the same "preferred target" plumbing carries
through with nothing to pick.

The two transports differ in exactly three ways, all of them here:

* **how a command is run** — ``wsl.exe -d <distro> -e bash -c`` vs plain ``bash -c``.
* **how a Windows path is named inside that shell** — ``wslpath -u`` vs itself.
  :func:`shell_path_expr` returns a bash *expression*, so the generated
  ``run_crest.sh`` and the launch command share one answer.
* **whether a scratch directory is worth it** — see :func:`uses_scratch`.

Qt-free and dependency-free, like ``wsl.py`` itself.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from typing import Tuple

from . import wsl

# The single target name on a local transport. Not a distro and not a path: it
# is what goes in ``Settings.crest_distro`` / ``StepConfig.crest_env_id`` so a
# persisted calc round-trips and a reattach after a restart still resolves.
LOCAL_TARGET = "local"

# Error strings run_bash returns instead of raising, from either transport.
# Callers test them through is_missing() rather than comparing to one spelling.
_MISSING_ERRORS = ("wsl-not-found", "bash-not-found")


def transport_kind() -> str:
    """``"wsl"`` on Windows, ``"local"`` everywhere else."""
    return "wsl" if sys.platform.startswith("win") else "local"


def is_local() -> bool:
    """True when CREST runs directly on this machine (no WSL in between)."""
    return transport_kind() == "local"


def is_missing(err: str) -> bool:
    """True when ``run_bash``'s stderr says the transport itself is absent —
    ``wsl.exe`` not installed, or no ``bash`` on PATH."""
    return (err or "") in _MISSING_ERRORS


def missing_message() -> str:
    """The sentence to show when :func:`available` is False. Names the thing
    that is actually missing on THIS platform — "install WSL" is useless advice
    on a machine that has no WSL to install."""
    if is_local():
        return "No POSIX shell found — CREST needs `bash` on PATH."
    return "WSL is not available. Install WSL to run CREST calculations."


def available() -> bool:
    """True when the transport can be invoked at all."""
    if not is_local():
        return wsl.wsl_available()
    try:
        p = subprocess.run(["bash", "-c", "exit 0"], capture_output=True,
                           timeout=15)
        return p.returncode == 0
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return False


def list_targets() -> list[str]:
    """Every place CREST could run: the installed WSL distros, or — locally —
    the single pseudo-target :data:`LOCAL_TARGET`. Empty when the transport
    itself is absent, which is what makes "nothing to install into" a state the
    UI can report on either platform."""
    if not is_local():
        return wsl.list_distros()
    return [LOCAL_TARGET] if available() else []


def run_bash(target: str, bash_cmd: str, timeout: float = 20.0,
             login: bool = False) -> Tuple[int, str, str]:
    """Run ``bash -c <bash_cmd>`` on ``target``; return (rc, stdout, stderr).

    Same contract as ``wsl.run_bash`` — and deliberately the same signature, so
    every caller reads identically on both platforms. Never raises: a transport
    failure is data (P6), because the one caller that matters
    (``CrestRunner._liveness``) exists precisely so that a transient failure
    cannot condemn a healthy multi-hour run.
    """
    if not is_local():
        return wsl.run_bash(target, bash_cmd, timeout=timeout, login=login)
    argv = ["bash", "-lc", bash_cmd] if login else ["bash", "-c", bash_cmd]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except FileNotFoundError:
        return 127, "", "bash-not-found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except (subprocess.SubprocessError, OSError) as e:
        return 125, "", f"shell-error: {e}"
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def shell_path_expr(path: str, transport: str = "") -> str:
    """A bash expression evaluating to ``path`` as the transport's shell sees it.

    ORCAdesk always holds native paths (``D:/work/job`` on Windows,
    ``/home/u/work/job`` on Linux); WSL needs ``wslpath -u`` to turn the first
    into ``/mnt/d/work/job``, and a local shell needs nothing at all. Returning
    an *expression* rather than a resolved string is what lets the generated
    ``run_crest.sh`` and the launch command agree without either of them
    knowing which platform they are on.
    """
    quoted = shlex.quote(str(path))
    if (transport or transport_kind()) == "wsl":
        return f'"$(wslpath -u {quoted})"'
    return quoted


def uses_scratch(transport: str = "") -> bool:
    """Whether a CREST run should be staged in a scratch directory.

    Under WSL, yes and emphatically: CREST's many-small-file I/O over the 9P
    ``/mnt`` mount is 5-300x slower than ext4, so the run happens in
    ``~/.orcadesk/scratch/<name>`` and the ensemble is copied back. Locally the
    workspace is already a native filesystem, so there is nothing to stage away
    from — and running in the calc folder means what CREST leaves behind
    (``--keepdir``, a crashed run's trajectories) is where the user can look at
    it, instead of inside a VHD that the script deletes.
    """
    return (transport or transport_kind()) == "wsl"
