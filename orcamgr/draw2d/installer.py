"""
Create the 2D-drawing environment with one click: a venv under
``user_data_root()/draw2d_env`` and ``pip install rdkit`` into it.

The machinery is the MLIP installer's (``orcamgr/mlip/installer.py``): the
same base-interpreter detection (a frozen ORCAdesk has no Python of its own),
the same streamed, cancellable subprocess runner, the same ``venv_python``
layout rule. What differs is the plan -- no PyTorch, no device choice, one
package -- and the supported-Python window, which is RDKit's wheels', not
torch's.

What Create downloads (said on the Settings card, P-honesty): RDKit
(BSD-3-Clause) and the two packages its wheel depends on, NumPy (BSD-3-Clause)
and Pillow (MIT-CMU), from PyPI -- about 50 MB, ~150 MB installed.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Optional, Sequence

from ..mlip.installer import (MlipEnvInstaller, find_base_pythons, python_version,
                              venv_python)

# RDKit publishes wheels for these CPythons. MEASURED against PyPI (pip
# resolving win_amd64 wheels, 2026-11): rdkit 2026.09.2 ships cp310 ... cp315,
# and a 3.9 base resolves to 2025.09.2 (cp39), which still has
# rdDetermineBonds. A pre-flight courtesy only -- an unsupported base
# otherwise fails deep inside pip with "no matching distribution".
MIN_PY = (3, 9)
MAX_PY = (3, 15)

ENV_DIRNAME = "draw2d_env"
REQUIREMENT = "rdkit"


def is_supported_python(version: tuple) -> bool:
    return bool(version) and MIN_PY <= version <= MAX_PY


def base_pythons() -> list[dict]:
    """Interpreters usable as the venv base, best first, marked against
    RDKit's window (find_base_pythons marks them against torch's)."""
    out = []
    for e in find_base_pythons():
        try:
            ver = tuple(int(x) for x in e["version"].split(".")[:2])
        except (ValueError, KeyError):
            continue
        out.append({"python": e["python"], "version": e["version"],
                    "supported": is_supported_python(ver)})
    out.sort(key=lambda e: (not e["supported"],
                            [-int(x) for x in e["version"].split(".")]))
    return out


def install_plan(base_python: str, env_dir) -> list[dict]:
    """The ordered steps: [{label, argv}]."""
    py = venv_python(env_dir)
    pip = [str(py), "-m", "pip", "install",
           "--progress-bar", "off", "--disable-pip-version-check"]
    return [
        {"label": "Creating the environment",
         "argv": [str(base_python), "-m", "venv", str(env_dir)]},
        {"label": "Updating pip", "argv": [*pip, "--upgrade", "pip"]},
        {"label": "Downloading RDKit (~50 MB)", "argv": [*pip, REQUIREMENT]},
    ]


def _has_rdkit(env_dir: Path) -> bool:
    d = Path(env_dir)
    roots = list(d.glob("lib/python*/site-packages")) + [d / "Lib" / "site-packages"]
    for sp in roots:
        try:
            if (sp / "rdkit").exists() or any(sp.glob("rdkit-*.dist-info")):
                return True
        except OSError:
            continue
    return False


def _is_half_built(env_dir: Path, registered: bool) -> bool:
    """A directory ORCAdesk itself left behind by a cancelled or failed Create:
    unregistered, without RDKit, and holding nothing but a venv's own
    entries. Deliberately narrow -- it decides what may be deleted."""
    if registered:
        return False
    try:
        if _has_rdkit(env_dir):
            return False
        names = {p.name.lower() for p in env_dir.iterdir()}
    except OSError:
        return False
    return bool(names) and names <= {"scripts", "bin", "lib", "lib64",
                                     "include", "share", "pyvenv.cfg", ".gitignore"}


def _holds(env_dir: Path, python: str) -> bool:
    """Whether ``python`` lives inside ``env_dir`` (its folder resolved, never
    the interpreter itself: a venv's bin/python links out to the base)."""
    if not python:
        return False
    try:
        root = env_dir.resolve()
        holder = Path(python).parent.resolve()
    except OSError:
        return False
    return root == holder or root in holder.parents


class Draw2dEnvInstaller(MlipEnvInstaller):
    """The MLIP installer's streaming/cancel machinery with RDKit's plan."""

    def run(self, base_python: str, env_dir, *,     # type: ignore[override]
            on_line: Optional[Callable[[str], None]] = None,
            on_step: Optional[Callable[[int, int, str], None]] = None,
            in_use: Optional[Sequence[str]] = None) -> dict:
        """Build the env. Returns {ok, python, error, cancelled}. Blocking."""
        say = on_line or (lambda _s: None)
        env_dir = Path(env_dir)

        def fail(msg: str, cancelled: bool = False) -> dict:
            return {"ok": False, "python": "", "error": msg, "cancelled": cancelled}

        if not base_python or not Path(base_python).exists():
            return fail("No base Python interpreter to build the environment "
                        "with. Install Python 3, then Re-check.")
        ver = python_version(base_python)
        if not is_supported_python(ver):
            shown = "%d.%d" % ver if ver else "unknown"
            return fail(f"Python {shown} is outside the range RDKit publishes "
                        f"wheels for ({MIN_PY[0]}.{MIN_PY[1]}-{MAX_PY[0]}.{MAX_PY[1]}).")
        if env_dir.exists() and any(env_dir.iterdir()):
            registered = any(_holds(env_dir, p) for p in (in_use or ()))
            if _is_half_built(env_dir, registered):
                say(f"--- removing the incomplete {env_dir.name} left by an "
                    "earlier attempt ---")
                try:
                    shutil.rmtree(env_dir)
                except OSError as e:
                    return fail(f"'{env_dir}' is left over from an interrupted "
                                f"install and could not be removed ({e}). "
                                "Delete it, then try again.")
            elif registered:
                return fail("That environment is already registered.")
            else:
                # An RDKit env ORCAdesk built before (Settings lost, or Remove
                # was used): adopt it rather than download it again.
                py = venv_python(env_dir)
                if _has_rdkit(env_dir) and py.exists():
                    say(f"--- {env_dir} already holds RDKit; using it ---")
                    return {"ok": True, "python": str(py), "error": "",
                            "cancelled": False}
                return fail(f"'{env_dir}' exists and is not an environment "
                            "ORCAdesk recognises. Move it aside, then try again.")
        try:
            env_dir.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return fail(f"Could not create {env_dir.parent}: {e}")
        steps = install_plan(base_python, env_dir)
        for i, step in enumerate(steps, 1):
            if self._cancel.is_set():
                return fail("Cancelled.", True)
            if on_step:
                on_step(i, len(steps), step["label"])
            say(f"--- {step['label']} ---")
            rc = self._stream(step["argv"], say)
            if self._cancel.is_set():
                return fail("Cancelled.", True)
            if rc != 0:
                return fail(f"{step['label']} failed (exit {rc}). "
                            "See the log for pip's own message.")
        py = venv_python(env_dir)
        if not py.exists():
            return fail(f"The environment was built but its interpreter is "
                        f"missing at {py}.")
        return {"ok": True, "python": str(py), "error": "", "cancelled": False}
