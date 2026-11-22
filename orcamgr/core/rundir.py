"""
Where a calculation runs: its run folder.

A calculation that belongs to a system (the queue editor's row:
``Calculation.system`` is a QueueStore system id, and the system has a name)
runs in ``{workspace}/{system name}/{name}/``; a standalone one in
``{workspace}/{name}/``. Up to 0.11.3 every calculation ran in
``{workspace}/{name}/``, so a workspace of 20 molecules x 6 steps was 120
folders side by side.

The folder is decided when the calculation FIRST STARTS and recorded on it
(``Calculation.run_dir``, relative to the workspace, ``/``-separated), and from
then on the calculation is found through that record (or its ``output_path``,
the file it actually wrote). So renaming a system, or moving a block to
another row, never changes where a calculation that has run lives, and nothing
on disk is ever moved or renamed for it: a job may be running in that folder.
A calculation that has not started has no record; its folder follows its
system's CURRENT name.

This module is the one place the rule lives (P4). Every caller -- the engine,
the store, the desktop bridge -- resolves a folder through it, giving the
system NAME (the store holds the systems; the engine asks it through the
lookup make_engine_factory wires in).

A system name IS its folder name, verbatim. It is held to the same folder rule
as a calculation name (``folder_name_problem``, applied by the store's name
validator to both) and refused when it fails it, rather than mapped to a safe
spelling: a mapped folder is one more name to find on disk, and two names can
map to one folder ("a:b" and "a_b"), which a refusal cannot. ``system_folder``
still checks it, so a name that somehow bypassed validation runs in the
workspace root -- as a standalone calculation would -- and never escapes it.

Qt-free; unit-tests on a ``tmp_path``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, Optional

# ---- the folder rule (one path component) ----------------------------------
# A calculation name is a folder name (and its system's name is the folder its
# run folder sits in), so both are validated here once. The store's
# _validate_calc_name applies it to every calc and system name on every entry
# path -- the phone/HTTP path included, where the desktop's own field guard
# does not reach (P33).

#: separators and the characters Windows refuses in a file name
BAD_FOLDER_CHARS = re.compile(r'[\\/:*?"<>|]')
# Control characters and lone surrogates are not typeable in the desktop's
# single-line input but arrive freely through calc_from_dict (the phone/HTTP
# path, a hand-edited session.json). A control character reaches mkdir and dies
# with a localized WinError 123, which stamps the calc FAILED -- locked (P24);
# a surrogate cannot be encoded to UTF-8 at all and used to poison session
# autosave for the rest of the app's life.
UNUSABLE_CHARS = re.compile(r"[\x00-\x1f\x7f\ud800-\udfff]")
# Windows caps a path component at 255 characters, and the workspace path plus
# "{name}/{name}.inp" (and a system folder in front) is spent on top of it. The
# cap is here rather than at mkdir so the refusal is a sentence instead of a
# WinError 3.
MAX_NAME_LEN = 120
# Device names Windows reserves whatever the extension ("con.inp" is CON).
# The superscript digits are the ISO 8859-1 ones Windows also reads as digits.
# Measured on Windows 11 (26300): mkdir of these now succeeds there, but they
# are documented as reserved and refused by older Windows, so they stay out.
RESERVED_NAMES = frozenset({"con", "prn", "aux", "nul",
                            *(f"com{i}" for i in (*range(1, 10), "¹", "²", "³")),
                            *(f"lpt{i}" for i in (*range(1, 10), "¹", "²", "³"))})


def folder_name_problem(name: str) -> str:
    """Why ``name`` cannot be one folder of a run path, or "".

    Measured on Windows 11: a folder made as ``"abc."`` or ``"aux "`` is
    created WITHOUT the trailing dot / space, so the path ORCAdesk then builds
    from the name points somewhere else ("aux \\x.txt" failed to open), and
    two names would share one folder. A leading space is legal and kept."""
    if not isinstance(name, str) or not name:
        return "Name is required."
    if BAD_FOLDER_CHARS.search(name):
        return 'Name contains characters not allowed in a folder name: \\ / : * ? " < > |'
    if UNUSABLE_CHARS.search(name):
        return "Name contains a character that cannot be used in a folder name."
    if len(name) > MAX_NAME_LEN:
        return (f"Name is too long ({len(name)} characters); "
                f"keep it under {MAX_NAME_LEN}.")
    if ".." in name:
        return "Name must not contain '..'."
    if name.endswith("."):
        return "Name must not end with a dot."
    if name.endswith(" "):
        return "Name must not end with a space."
    # "con .txt" is CON too: Windows drops the spaces before the extension
    if name.split(".")[0].rstrip(" ").lower() in RESERVED_NAMES:
        return f"'{name}' uses a name reserved by Windows."
    return ""


# ---- the layout --------------------------------------------------------------

def system_folder(system_name: str) -> str:
    """The workspace folder a system's calculations run under: its name,
    verbatim. "" -- no system folder, the calc runs in the workspace root --
    for no name, or for one that is not a safe folder (system_from_dict
    never lets one in; see the module docstring)."""
    name = system_name if isinstance(system_name, str) else ""
    if not name or folder_name_problem(name):
        return ""
    return name


def planned_run_dir(name: str, system_name: str = "") -> str:
    """The run folder, relative to the workspace, a calc named ``name`` gets
    when it starts now: ``"{system}/{name}"``, or ``"{name}"`` standalone."""
    sys_dir = system_folder(system_name)
    return f"{sys_dir}/{name}" if sys_dir else name


def run_dir_of(calc, system_name: str = "") -> str:
    """The calc's run folder relative to the workspace: the one recorded when
    it first started, else the one it would get now (planned_run_dir)."""
    return getattr(calc, "run_dir", "") or planned_run_dir(calc.name, system_name)


def run_folder(workspace, calc, system_name: str = "") -> Path:
    """Where the calc runs (and runs again: a CANCELLED re-run or a rerun_calc
    keeps its recorded folder, the one the overwrite question was about)."""
    return Path(workspace).joinpath(*run_dir_of(calc, system_name).split("/"))


def result_folder(workspace, calc, system_name: str = "") -> Path:
    """Where the calc's result is read: the folder of the output it wrote
    (output_path survives a change of workspace setting), else run_folder."""
    out = getattr(calc, "output_path", "") or ""
    if out:
        return Path(out).parent
    return run_folder(workspace, calc, system_name)


def restored_run_dir(value, name: str) -> str:
    """A recorded run folder read back from session.json, checked: ``name``
    itself, or ``"{folder}/{name}"`` with a folder that passes the folder rule
    -- always relative, always inside the workspace, always named after the
    calc. Anything else reads as "" (not recorded). A client never sets it at
    all: calc_from_dict does not read it."""
    if not isinstance(value, str) or not value or not isinstance(name, str):
        return ""
    parts = value.split("/")
    if parts[-1] != name or len(parts) > 2:
        return ""
    if any(folder_name_problem(p) for p in parts):
        return ""
    return value


# ---- what a folder holds -------------------------------------------------------

def holds_output(folder: Path, name: str) -> bool:
    """Whether ``folder`` holds the result artifact of a calc named ``name``:
    ``{name}.out``, or the MLIP worker's ``{name}.mlip.json`` (the convention
    resultfolder.result_artifact reads)."""
    folder = Path(folder)
    return (folder / f"{name}.out").is_file() or (folder / f"{name}.mlip.json").is_file()


def names_with_output(workspace, names: Iterable[str]) -> "list[str]":
    """The names among ``names`` that already have output somewhere in the
    workspace: in ``{workspace}/{name}/`` or in ``{workspace}/{any folder}/{name}/``
    (a system's folder, current or left by a removed row -- folders survive
    removal). In the order given, each once.

    For a name that is not in the queue yet, which system it will run under is
    not known, so every place it could land counts -- the conservative answer
    for an overwrite warning and for the editor's free-name search, and the
    same answer as before system folders for an old ``{workspace}/{name}/``.
    One listing of the workspace and of each of its folders, whatever the
    number of names."""
    ws = Path(workspace)
    wanted = [n for n in dict.fromkeys(names)
              if isinstance(n, str) and n and not folder_name_problem(n)]
    if not wanted:
        return []
    found: "list[str]" = []
    children: "Optional[list[tuple[Path, set]]]" = None
    for n in wanted:
        if holds_output(ws / n, n):
            found.append(n)
            continue
        if children is None:
            children = _subfolders(ws)
        key = n.casefold()
        if any(key in names_in and holds_output(folder / n, n)
               for folder, names_in in children):
            found.append(n)
    return found


def _subfolders(ws: Path) -> "list[tuple[Path, set]]":
    """Each folder of the workspace with the (casefolded) names of the folders
    in it. An unreadable folder is skipped (P6)."""
    out: "list[tuple[Path, set]]" = []
    try:
        with os.scandir(ws) as it:
            top = [e.path for e in it if e.is_dir()]
    except OSError:
        return out
    for path in top:
        try:
            with os.scandir(path) as it:
                inner = {c.name.casefold() for c in it if c.is_dir()}
        except OSError:
            continue
        if inner:
            out.append((Path(path), inner))
    return out
