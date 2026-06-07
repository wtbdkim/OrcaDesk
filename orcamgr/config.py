"""
Application settings, persisted as JSON in the user data directory.

Replaces the old hard-coded ``PATH_ORCA`` constant. The ORCA executable
location is now (a) auto-detected from common install locations and PATH,
and (b) overridable + persisted via the GUI. This is what lets the app run
on a friend's machine where ORCA lives somewhere else.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path

from .paths import config_file, default_workspace_root


# ---- ORCA discovery -----------------------------------------------------
def _candidate_orca_paths() -> list[Path]:
    """Likely ORCA executable locations, OS-dependent."""
    exe = "orca.exe" if sys.platform.startswith("win") else "orca"
    candidates: list[Path] = []

    # 1) anything already on PATH
    found = shutil.which(exe)
    if found:
        candidates.append(Path(found))

    # 2) common Windows install roots
    if sys.platform.startswith("win"):
        roots = [
            Path("C:/"), Path("C:/Program Files"), Path("C:/ORCA"),
            Path("D:/"), Path("D:/ORCA"),
        ]
        for root in roots:
            if not root.exists():
                continue
            # match folders like ORCA, ORCA_6.1.1, orca6, ...
            try:
                for child in root.iterdir():
                    if child.is_dir() and "orca" in child.name.lower():
                        p = child / exe
                        if p.exists():
                            candidates.append(p)
            except (PermissionError, OSError):
                pass
    else:
        for p in (Path("/usr/local/orca") / exe, Path("/opt/orca") / exe):
            if p.exists():
                candidates.append(p)

    # de-duplicate, keep order
    seen, unique = set(), []
    for c in candidates:
        key = str(c).lower()
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def autodetect_orca() -> str:
    cands = _candidate_orca_paths()
    return str(cands[0]) if cands else ""


# ---- settings dataclass -------------------------------------------------
@dataclass
class Settings:
    orca_path: str = ""
    workspace_root: str = ""
    # default compute resources (used to seed the GUI)
    default_nprocs: int = 6
    default_maxcore_mb: int = 2400
    theme: str = "dark"
    # opt ETA prediction mode: "conservative" (predict only when confident) or
    # "eager" (predict earlier / more often, may be less accurate)
    eta_mode: str = "conservative"
    # optimization graph style: "all5" (all five convergence criteria as
    # value/tolerance ratios sharing one goal line) or "maxgrad" (MAX gradient only)
    geo_graph_mode: str = "all5"
    # build-tab mode: "beginner" (the guided form) or "expert" (paste/load a
    # complete .inp and only pick the calc kind, for parsing/validation)
    build_mode: str = "beginner"

    @classmethod
    def load(cls) -> "Settings":
        path = config_file()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                s = cls(**{k: v for k, v in data.items()
                           if k in cls.__dataclass_fields__})
            except (json.JSONDecodeError, TypeError, OSError):
                s = cls()
        else:
            s = cls()

        # fill in sensible defaults on first run
        if not s.orca_path:
            s.orca_path = autodetect_orca()
        if not s.workspace_root:
            s.workspace_root = str(default_workspace_root())
        return s

    def save(self) -> None:
        config_file().write_text(
            json.dumps(asdict(self), indent=2), encoding="utf-8"
        )

    def orca_is_valid(self) -> bool:
        return bool(self.orca_path) and Path(self.orca_path).exists()
