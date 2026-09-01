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
    so the viewer's list stays legible. Unreadable files are skipped."""
    folder = Path(folder)
    out: list[dict] = []
    for f in sorted(folder.glob("*.xyz"), key=lambda p: _natural_key(p.name)):
        try:
            out.extend(frames_from_file(f, label_prefix=f.stem))
        except OSError:
            continue
    return out
