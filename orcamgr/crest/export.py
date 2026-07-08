"""
Split a CREST conformer ensemble into per-conformer ``.xyz`` files.

A finished CREST run writes ``crest_conformers.xyz`` — a single multi-structure
XYZ, energy-sorted, the first frame being the lowest-energy conformer (identical
to ``crest_best.xyz``). This module splits that file into one standalone ``.xyz``
per conformer, written verbatim (frame count + comment/energy line + coordinates
exactly as CREST produced them) into a ``conformers/`` subfolder of the run
folder. c1 is the best conformer, so "all conformers including crest best" are
covered by splitting the ensemble alone.

Pure/file-only (no PyQt): the bridge slot is a thin wrapper, and this stays
unit-testable against the real ethanol corpus.
"""

from __future__ import annotations

from pathlib import Path


def split_conformer_frames(text: str) -> list[str]:
    """Split a multi-structure XYZ into a list of standalone ``.xyz`` frame
    strings, each ``"{natoms}\\n{comment}\\n{coords}"`` preserving CREST's exact
    lines. Tolerant of ``\\r\\n`` and blank lines between frames; a frame whose
    coordinate count doesn't match its header is skipped (same rule as the
    parser), so a truncated trailing frame never yields a malformed file."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    frames: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        while i < n and not lines[i].strip():
            i += 1
        if i >= n:
            break
        tok = lines[i].split()
        if not tok or not tok[0].lstrip("+-").isdigit():
            i += 1
            continue
        natoms = int(tok[0])
        if natoms <= 0:
            i += 1
            continue
        comment = lines[i + 1] if i + 1 < n else ""
        coord_lines = []
        base = i + 2
        for j in range(base, min(base + natoms, n)):
            if len(lines[j].split()) < 4:
                break
            coord_lines.append(lines[j].rstrip())
        if len(coord_lines) == natoms:
            frames.append(f"{natoms}\n{comment.rstrip()}\n" + "\n".join(coord_lines) + "\n")
        i = base + natoms
    return frames


def export_conformers(conformers_xyz: Path, dest_dir: Path, base_name: str) -> list[Path]:
    """Read ``conformers_xyz`` and write each conformer to
    ``dest_dir/{base_name}_c{k}.xyz`` (k 1-based, zero-padded to the ensemble
    width; c1 = the best conformer). Creates ``dest_dir``. Returns the written
    paths. Raises ``FileNotFoundError`` if the ensemble file is absent and
    ``ValueError`` if it holds no parseable frame."""
    conformers_xyz = Path(conformers_xyz)
    if not conformers_xyz.exists():
        raise FileNotFoundError(f"{conformers_xyz.name} not found (was the CREST run finished?)")
    frames = split_conformer_frames(conformers_xyz.read_text(encoding="utf-8", errors="replace"))
    if not frames:
        raise ValueError("no conformers found in the ensemble file")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    width = len(str(len(frames)))
    written: list[Path] = []
    for k, frame in enumerate(frames, start=1):
        out = dest_dir / f"{base_name}_c{k:0{width}d}.xyz"
        out.write_text(frame, encoding="utf-8")
        written.append(out)
    return written
