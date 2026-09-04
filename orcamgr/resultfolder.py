"""
What result a folder holds.

ORCAdesk's own unit of work is a run folder, ``{workspace}/{name}/``, and the
artifact the parser reads has a fixed name inside it: the engine-written
``{name}.mlip.json`` for an MLIP run, else ``{name}.out`` (a CREST search
keeps its ensemble beside the ``.out``). That convention is read in two places
-- the Results tab's workspace scan and its *Open folder…* button -- so it
lives here once (P4), Qt-free, and unit-tests on a ``tmp_path``.

The button is looser than the scan on purpose. The scan lists ORCAdesk's own
folders and must not mistake a stray file for a result, so it holds to the
convention. A folder the user *points at* is a statement that there is a
result in it, and an ORCA run made by hand names its output freely -- so when
the convention finds nothing, the ``.out`` files the folder holds are the
next answer: the one there is, or the newest of several.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

#: The backends a result can come from, in the order the folder is read.
RESULT_KINDS = ("mlip", "crest", "orca")


def _crest_siblings(folder: Path) -> bool:
    return (folder / "crest_conformers.xyz").exists() or (folder / "crest_best.xyz").exists()


def result_artifact(folder: str | Path) -> Optional[tuple]:
    """``(path, kind)`` of the result ORCAdesk's convention says this run
    folder holds, or ``None`` -- a folder with no result is not a result."""
    entry = Path(folder)
    name = entry.name
    mlip = entry / f"{name}.mlip.json"
    out = entry / f"{name}.out"
    if mlip.exists():
        return mlip, "mlip"
    if out.exists():
        return out, "crest" if _crest_siblings(entry) else "orca"
    return None


def find_result(folder: str | Path) -> dict:
    """The result to open for a folder the user chose: ``{ok, path, kind}``, or
    ``{ok: False, error}`` with a sentence that says what would have worked.

    Convention first (:func:`result_artifact`); failing that, the folder's
    ``.out`` files -- the only one, or the newest. Never raises: a folder that
    cannot be read is a refusal with a reason (P6).
    """
    entry = Path(str(folder or "").strip())
    if not str(folder or "").strip():
        return {"ok": False, "error": "No folder chosen."}
    if not entry.is_dir():
        return {"ok": False, "error": f"{entry.name or entry} is not a folder that exists."}
    found = result_artifact(entry)
    if found is not None:
        return {"ok": True, "path": str(found[0]), "kind": found[1]}
    try:
        outs = [p for p in entry.iterdir() if p.is_file() and p.suffix.lower() == ".out"]
    except OSError as e:
        return {"ok": False, "error": f"Could not read that folder: {e}"}
    if not outs:
        return {"ok": False,
                "error": f"No calculation output in {entry.name}: it holds no "
                         f".out or .mlip.json. For a folder of structures, open "
                         f"one of its .xyz files instead."}
    newest = max(outs, key=lambda p: p.stat().st_mtime)
    return {"ok": True, "path": str(newest),
            "kind": "crest" if _crest_siblings(entry) else "orca"}
