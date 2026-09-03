"""
Read ``.xyz`` structures into viewer frames for the in-app 3D molecule viewer.

The Results-tab viewer (3Dmol.js) renders a *list of frames* and steps through
them with the arrow keys. A frame is just the raw XYZ text of one structure plus
a label and (when the comment line is a bare number) its absolute energy — 3Dmol
parses the XYZ text directly, so we hand it through verbatim.

Two sources feed the viewer:
* a single multi-structure file (e.g. a CREST ``crest_conformers.xyz``), via
  :func:`frames_from_file`; and
* any folder of ``.xyz`` files, via :func:`frames_from_folder` (each file may
  itself be multi-frame).

Pure / file-only and Qt-free so it stays unit-testable; the Bridge slots pick
the file/folder and serialize the returned dicts to JSON.
"""

from __future__ import annotations

import re
from pathlib import Path


def _natural_key(name: str) -> list:
    """Sort key so ``c2`` precedes ``c10`` (ORCAdesk exports are zero-padded, but
    an arbitrary user folder need not be)."""
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", name)]


def _parse_energy(comment: str):
    """A CREST/xyz comment line that is a bare number is the absolute energy in
    Hartree. Return it as a float, or None when the comment is anything else."""
    tok = comment.strip().split()
    if not tok:
        return None
    try:
        return float(tok[0])
    except ValueError:
        return None


def iter_xyz_frames(text: str):
    """Yield ``(comment, frame_text)`` for each well-formed XYZ frame (``count``
    header + comment + ``count`` coordinate lines) in a possibly multi-frame
    string. ``frame_text`` is the verbatim, newline-normalized frame (ready for
    3Dmol's ``addModel(text, "xyz")``). Stops at the first malformed/truncated
    header — tolerant, never raises."""
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        while i < n and not lines[i].strip():
            i += 1
        if i >= n:
            break
        try:
            count = int(lines[i].split()[0])
        except (ValueError, IndexError):
            break
        if count < 0:
            break
        frame = lines[i:i + count + 2]
        if len(frame) < count + 2:
            break   # truncated final frame
        comment = frame[1] if len(frame) > 1 else ""
        yield comment, "\n".join(frame)
        i += count + 2


# .xyz files come from arbitrary Windows tools, which write a UTF-8 BOM freely.
# int("\ufeff3") raises, so the very first header line failed to parse and the
# frame loop ended before it began: an ensemble showed "No structures found",
# and a browsed folder just lost those files from the list with no message.
# utf-8-sig reads a file without a BOM identically, so this costs nothing.
def frames_from_file(path, label_prefix: str = "") -> list[dict]:
    """Frames for one ``.xyz`` file. Each dict is ``{label, xyz, energy}`` with
    ``energy`` a float (Hartree) or None. Labels are ``{prefix}{k}`` (1-based)
    for a multi-frame file, or the bare prefix for a single frame."""
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    frames = list(iter_xyz_frames(text))
    out: list[dict] = []
    multi = len(frames) > 1
    base = label_prefix or path.stem
    for k, (comment, frame_text) in enumerate(frames, start=1):
        label = f"{base}#{k}" if (multi and label_prefix) else \
                (f"{base} #{k}" if multi else base)
        out.append({"label": label, "xyz": frame_text,
                    "energy": _parse_energy(comment)})
    return out


def frames_from_folder(folder) -> list[dict]:
    """Frames for every ``*.xyz`` under ``folder``, in natural filename order.
    Each source file may itself be multi-frame; frame labels carry the file stem
    so the viewer's list stays legible. Unreadable files are skipped.

    A folder holding no ``.xyz`` of its own but a ``conformers/`` subfolder is
    read as that subfolder: pointing this at a finished CREST run means the
    per-conformer export, and making the caller know that is the file-dialog
    thinking this replaced."""
    folder = Path(folder)
    if not any(folder.glob("*.xyz")):
        nested = folder / "conformers"
        if nested.is_dir() and any(nested.glob("*.xyz")):
            folder = nested
    out: list[dict] = []
    for f in sorted(folder.glob("*.xyz"), key=lambda p: _natural_key(p.name)):
        try:
            out.extend(frames_from_file(f, label_prefix=f.stem))
        except OSError:
            continue
    return out


# --- discovery: what a result folder holds that the 3D viewer can open -------
#
# The viewer used to be reached through a folder picker ("Browse .xyz…"), which
# asked the user to know where ORCAdesk had put things. Everything worth opening
# already sits in the result's own run folder — the CREST ensemble, the
# per-conformer `conformers/` export, an optimization trajectory — so the tab
# lists them instead of asking. The pick is a set, not a file dialog.

# A .xyz big enough that counting its frames is not worth a read on tab entry.
# Frame counts are a convenience in the row label, never a correctness matter.
_COUNT_LIMIT_BYTES = 8_000_000

# Folder names that are ORCAdesk's own exports, so their rows can say what they
# are rather than repeat the directory name.
_FOLDER_LABELS = {
    "conformers": "Conformers (exported)",
    "favorites": "Starred structures",
}

# Above this many .xyz in one folder, the unnamed ones collapse into a single
# "browse the whole folder" set. A run folder holds a handful and each is worth
# its own row; a conformers/ export holds hundreds, and listing those one per
# row would be a list nobody reads built from hundreds of file reads.
_MANY_XYZ = 12


def count_xyz_frames(path) -> int:
    """How many well-formed frames a ``.xyz`` holds. 0 when it is unreadable or
    too large to be worth reading (the count is a label, not a correctness
    matter — a set with an unknown count still opens)."""
    path = Path(path)
    try:
        if path.stat().st_size > _COUNT_LIMIT_BYTES:
            return 0
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return 0
    return sum(1 for _ in iter_xyz_frames(text))


def _file_label(name: str, base: str) -> str:
    """What a ``.xyz`` in a run folder actually is, said in the row rather than
    left to the file name. The three ORCA/CREST ones a user opens are worth
    naming; anything else keeps its own name, which is the honest answer."""
    low = name.lower()
    if low == "crest_conformers.xyz":
        return "Conformer ensemble"
    if low == "crest_best.xyz":
        return "Best conformer"
    if base and low == f"{base.lower()}_trj.xyz":
        return "Optimization trajectory"
    return name


def discover_structure_sets(folder, base: str = "") -> list[dict]:
    """Every ``.xyz`` set the viewer can open in ``folder``, as
    ``{key, label, kind, path, count}`` dicts.

    Two kinds. A ``"folder"`` set is a **direct** subfolder holding ``.xyz``
    files — ORCAdesk's own ``conformers/`` export is the one that matters, and
    it sorts first because it is what someone opening a finished conformer
    search is looking for. A ``"file"`` set is one ``.xyz`` in the folder
    itself, which may be multi-frame (a CREST ensemble, an optimization
    trajectory). ``count`` is files for a folder and frames for a file.

    Only one level down: a run folder's subfolders are ORCAdesk's own exports,
    and walking deeper would turn a tab switch into a disk crawl. For the same
    reason a folder holding more than ``_MANY_XYZ`` unnamed ``.xyz`` offers one
    whole-folder set instead of a row (and a file read) each — the files whose
    name says what they are keep their own rows either way.
    """
    folder = Path(folder)
    sets: list[dict] = []
    try:
        entries = sorted(folder.iterdir(), key=lambda p: _natural_key(p.name))
    except OSError:
        return sets

    dirs, files = [], []
    for p in entries:
        try:
            if p.is_dir():
                dirs.append(p)
            elif p.suffix.lower() == ".xyz":
                files.append(p)
        except OSError:
            continue

    for d in dirs:
        try:
            n = sum(1 for _ in d.glob("*.xyz"))
        except OSError:
            n = 0
        if not n:
            continue
        sets.append({"key": "folder:" + str(d), "kind": "folder", "path": str(d),
                     "label": _FOLDER_LABELS.get(d.name.lower(), d.name), "count": n})
    # conformers/ first: on a finished CREST search it is the set being looked
    # for, and on anything else it does not exist.
    sets.sort(key=lambda s: 0 if s["label"].startswith("Conformers") else 1)

    # A file whose name says what it is always gets its own row; the rest are
    # collapsed once there are enough of them to make the list useless.
    named = [f for f in files if _file_label(f.name, base) != f.name]
    rest = [f for f in files if f not in named]
    for f in named:
        sets.append({"key": "file:" + str(f), "kind": "file", "path": str(f),
                     "label": _file_label(f.name, base), "count": count_xyz_frames(f)})
    if len(rest) > _MANY_XYZ:
        sets.append({"key": "folder:" + str(folder), "kind": "folder",
                     "path": str(folder), "label": f"All structures in {folder.name}",
                     "count": len(files)})
    else:
        for f in rest:
            sets.append({"key": "file:" + str(f), "kind": "file", "path": str(f),
                         "label": f.name, "count": count_xyz_frames(f)})
    return sets
