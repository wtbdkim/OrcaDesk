"""
Hand a file ORCAdesk produced to the program the user already has for it.

ORCAdesk's in-app viewer is the fast path: a structure or an orbital is one
click and no second program. It is not the *only* path a chemist wants, though
— Avogadro, VMD, ChimeraX and Jmol each do things a 3Dmol canvas embedded in a
queue manager should not try to. P5 settles what that means here: an external
program is **launched**, never merely named. Telling the user which file to open
and where to find it is homework, and homework is the command line by another
route.

Two verbs, deliberately the two that need no configuration at all:

* :func:`open_with_default` — hand the file to whatever the operating system
  already associates with its type. If the user has Avogadro installed and
  ``.cube`` opens in it, this opens it in Avogadro, with nothing to set up.
* :func:`show_in_folder` — reveal the file in the file manager, selected. The
  answer to "where did that actually get written?", which a run folder full of
  ORCA's own output otherwise makes a hunt.

**The extension allowlist is a trust boundary, not tidiness.** These paths
arrive from the front-end, and ``os.startfile`` on a path is indistinguishable
from double-clicking it — handed an ``.exe`` it would *run* the thing. So
opening is default-deny: a suffix this module does not recognize as a data
format is refused, and no list of dangerous extensions has to be kept correct.
Revealing has no such hazard (the file manager selects, it does not execute), so
it accepts any path that exists.

Qt-free and side-effect-only, so the Bridge slots are thin wrappers and the
decision half unit-tests on its own.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: Suffixes :func:`open_with_default` will hand to the shell. Data formats a
#: chemistry program reads, nothing that any platform treats as executable.
#: ``.input`` is here for ORCA's ``<base>.molden.input``, and ``.txt`` for its
#: ``<base>.property.txt``.
OPENABLE_SUFFIXES = frozenset({
    # structures
    ".xyz", ".pdb", ".mol", ".mol2", ".sdf", ".cif", ".gro",
    # volumetric / surfaces
    ".cube", ".cub", ".chg", ".vtk",
    # wavefunction interchange
    ".molden", ".input", ".47", ".wfn", ".wfx", ".fchk",
    # ORCA's own text output
    ".out", ".inp", ".log", ".hess", ".engrad", ".trj", ".txt",
    # data
    ".csv", ".dat", ".json", ".png", ".svg", ".pdf",
})


def why_not_openable(path: str | Path) -> str:
    """One sentence saying why this path may not be opened, or ``""`` if it may.

    Split out from :func:`open_with_default` so the rule is testable without
    launching anything, and so a caller can grey a button rather than offering
    a click that will only report a refusal.
    """
    p = Path(path)
    if not str(path).strip():
        return "No file to open."
    if not p.exists():
        return f"{p.name} is not there any more."
    if p.is_dir():
        return ""                       # a folder opens in the file manager
    suffix = p.suffix.lower()
    if not suffix:
        return f"{p.name} has no file type, so there is nothing to open it with."
    if suffix not in OPENABLE_SUFFIXES:
        return (f"ORCAdesk does not open {suffix} files — it hands over data "
                "files, not programs.")
    return ""


def _launch(argv: list[str]) -> None:
    """Start a detached child and do not wait for it. Split out so tests can
    replace it: everything above is a decision, this is the side effect."""
    subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _startfile(path: str) -> None:
    """``os.startfile`` behind a name tests can replace. Windows only — the
    other platforms go through :func:`_launch`."""
    os.startfile(path)                                      # type: ignore[attr-defined]


def open_with_default(path: str | Path) -> dict:
    """Open ``path`` with the program the OS associates with its type.

    Returns ``{"ok": True}`` or ``{"ok": False, "error": "..."}`` — failure is
    data (P6), because a missing association is a normal thing for a user to
    have and must not cross the JS boundary as an exception.
    """
    refusal = why_not_openable(path)
    if refusal:
        return {"ok": False, "error": refusal}
    target = str(Path(path))
    try:
        if sys.platform == "win32":
            _startfile(target)
        elif sys.platform == "darwin":
            _launch(["open", target])
        else:
            _launch(["xdg-open", target])
    except OSError as e:
        # On Windows a type with no registered program raises here; the message
        # is the one thing that tells the user what to do about it.
        return {"ok": False, "error":
                f"Windows has no program registered for {Path(target).suffix} "
                f"files ({e})." if sys.platform == "win32" else str(e)}
    return {"ok": True}


def show_in_folder(path: str | Path) -> dict:
    """Reveal ``path`` in the file manager, selected where the platform can.

    A directory is opened; a file is selected inside its parent. Accepts any
    existing path: revealing does not execute anything.
    """
    p = Path(path)
    if not str(path).strip():
        return {"ok": False, "error": "No file to show."}
    if not p.exists():
        # Fall back to the parent when only the file is gone -- "the folder it
        # was written to" is still the answer the user wanted.
        if p.parent.is_dir():
            p = p.parent
        else:
            return {"ok": False, "error": f"{p.name} is not there any more."}
    target = str(p)
    try:
        if sys.platform == "win32":
            if p.is_dir():
                _startfile(target)
            else:
                # explorer wants the path glued to the switch, and it exits
                # non-zero even when it worked -- so this is fire-and-forget,
                # never a return-code check.
                _launch(["explorer", f"/select,{target}"])
        elif sys.platform == "darwin":
            _launch(["open", "-R", target] if p.is_file() else ["open", target])
        else:
            _launch(["xdg-open", target if p.is_dir() else str(p.parent)])
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}
