"""
Application settings, persisted as JSON in the user data directory.

Replaces the old hard-coded ``PATH_ORCA`` constant. The ORCA executable
location is now (a) auto-detected from common install locations and PATH,
and (b) overridable + persisted via the GUI. This is what lets the app run
on a friend's machine where ORCA lives somewhere else.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass, asdict, field, fields as dataclass_fields
from pathlib import Path

from .paths import config_file, default_workspace_root


# ---- ORCA discovery -----------------------------------------------------
# Proof that a candidate really is ORCA and not a same-named stranger. GNOME
# ships an accessibility screen reader whose executable is ALSO called `orca`
# and is on PATH by default on every Ubuntu desktop, so shutil.which("orca")
# finds it first -- a real, executable file that orca_is_valid() accepts and
# autodetect_orca() persisted as orca_path, leaving Settings reporting a
# healthy ORCA while every calculation failed. Windows never saw this: there
# the name is `orca.exe`.
#
# The two sentinels are the helper tools the app ALREADY requires through
# orca_tool (core/plot.py wants orca_plot, nbo/source.py wants orca_2mkl), so
# demanding them asks nothing of an install that it did not already owe.
_ORCA_SENTINELS = ("orca_2mkl", "orca_plot")


def _looks_like_orca(p: Path) -> bool:
    """True when ORCA's own helper tools sit beside ``p``.

    Symlinks are resolved first: a link in ~/.local/bin or /usr/local/bin
    pointing into a real install is a normal way to put ORCA on PATH, and the
    tools are beside the TARGET, never beside the link (P4).

    Discovery only. A path the user picked by hand is still judged by
    orca_is_valid alone, so an unusual or trimmed install stays selectable
    instead of becoming unconfigurable.
    """
    try:
        real = p.resolve()
    except OSError:                 # broken link, permission, cycle
        real = p
    return any(orca_tool(real, name) for name in _ORCA_SENTINELS)


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
        # 3) common POSIX install roots. ORCA for Linux ships as a tarball the
        #    user extracts wherever they like, and the directory keeps its
        #    version in the name (orca_6_1_1_linux_x86-64_shared_openmpi418),
        #    so this scans for a child whose name mentions orca exactly the way
        #    the Windows branch above does -- a single fixed /opt/orca path
        #    found almost nobody's actual install.
        roots = [Path("/opt"), Path("/usr/local"), Path.home(),
                 Path.home() / "opt", Path("/usr/local/share")]
        for root in roots:
            if not root.is_dir():
                continue
            direct = root / "orca" / exe
            if direct.exists():
                candidates.append(direct)
            try:
                for child in root.iterdir():
                    if child.is_dir() and "orca" in child.name.lower():
                        p = child / exe
                        if p.exists():
                            candidates.append(p)
            except (PermissionError, OSError):
                pass

    # de-duplicate, keep order. The key is case-folded only where the
    # filesystem is: on ext4 /opt/Orca and /opt/orca are two different installs,
    # and collapsing them would silently drop one.
    seen, unique = set(), []
    fold = sys.platform.startswith("win") or sys.platform == "darwin"
    for c in candidates:
        key = str(c).lower() if fold else str(c)
        if key in seen:
            continue
        seen.add(key)
        # Every candidate here is a GUESS, and a wrong guess is silent: it gets
        # saved as orca_path and only surfaces as failing calculations. So each
        # one has to prove it is ORCA (see _looks_like_orca).
        if _looks_like_orca(c):
            unique.append(c)
    return unique


def orca_tool(orca_path, name: str):
    """One of ORCA's helper executables (``orca_plot``, ``orca_2mkl``, ...) beside
    the configured ``orca``, or None when it is not there.

    They ship in ORCA's own directory and are never on PATH separately, so the
    user configures one location and every tool follows from it (P4). Lives here
    rather than beside its first caller because there is now more than one:
    ``core/plot.py`` wants orca_plot, ``nbo/source.py`` wants orca_2mkl.
    """
    p = Path(orca_path or "")
    if not p.name:
        return None
    exe = p.with_name(f"{name}.exe" if p.suffix.lower() == ".exe" else name)
    return exe if exe.exists() else None


def autodetect_orca() -> str:
    cands = _candidate_orca_paths()
    return str(cands[0]) if cands else ""


# ---- settings dataclass -------------------------------------------------
@dataclass
class Settings:
    orca_path: str = ""
    # Registered MLIP environments, one dict per env: {"id", "name", "python"}.
    # ORCAdesk does not install any MLIP toolchain — it shells out to each env's
    # interpreter the same way it shells out to orca_path. There is one env *per
    # MLIP* because different MLIPs pin conflicting dependencies (MACE vs SevenNet
    # both pin different e3nn), so they cannot share a venv. Readiness (do the
    # packages import? which backends?) is probed in orcamgr/mlip/env.py, not
    # stored here.
    mlip_envs: list = field(default_factory=list)
    # Preferred WSL distro for CREST conformer searches ("" = auto-detect the
    # first distro that has the crest binary). CREST has no native Windows build,
    # so ORCAdesk runs its Linux binary through WSL (see orcamgr/crest/); the
    # binary is detected/installed per-distro, not stored here.
    crest_distro: str = ""
    workspace_root: str = ""
    # default compute resources (used to seed the GUI)
    default_nprocs: int = 6
    default_maxcore_mb: int = 2400
    # --- parallel queue admission (see core/resources.py) ---
    # How many calculations may run at once. 1 = the classic one-at-a-time
    # queue, which stays the default: raising it is a deliberate choice about
    # this machine, not something to inherit silently on upgrade. 0 = as many as
    # the core/RAM budget allows, which is the usual way to think about it (you
    # cap the machine, not the job count).
    max_concurrent_jobs: int = 1
    # Total cores the queue may occupy across all running jobs (0 = auto, the
    # machine's physical core count). Each calculation declares its own share
    # (ORCA %pal nprocs / CREST -T), so "two 8-core jobs" and "four 4-core jobs"
    # are the same budget — ORCAdesk never rewrites a calculation's nprocs.
    max_total_cores: int = 0
    # Total memory the queue may occupy, MB (0 = auto, 75% of installed RAM).
    # ORCA's %maxcore is PER CORE, so a 6-core job at 2400 MB reserves 14.4 GB:
    # without this guard two of them can quietly push a 32 GB machine into swap.
    max_total_ram_mb: int = 0
    theme: str = "dark"
    # Theme *variant*, orthogonal to `theme` (light/dark): "shadcn" is the flat
    # default; "liquidglass" renders the Apple Liquid-Glass chrome (a refracting
    # frosted top bar / tabs over a wallpaper). Each variant works in both light
    # and dark. The whole Liquid-Glass CSS layer is gated on this — shadcn users
    # are untouched. See web/style.css and DESIGN.md §16.
    theme_variant: str = "shadcn"
    # Liquid-Glass intensity, meaningful only when theme_variant=="liquidglass":
    # restrained|moderate|bold|vivid|maximal — rising blur / refraction / glass
    # surface count (maps to the design2..design6 previews). restrained/moderate/
    # bold keep content opaque (glass = chrome only); vivid/maximal also glassify
    # the card layer (the exploratory extreme).
    glass_level: str = "moderate"
    # Liquid-Glass wallpaper key: one of the built-in procedural presets
    # (aurora|aqua|sunset|grape|graphite|ocean) or "custom". The custom image
    # itself is NOT stored here (it would bloat settings.json, which is rewritten
    # on every queue mutation) — it lives in a dedicated file in user_data_root,
    # written/read via the Bridge's set/get_wallpaper_image slots.
    wallpaper: str = "aurora"
    # opt ETA prediction mode: "conservative" (predict only when confident) or
    # "eager" (predict earlier / more often, may be less accurate)
    eta_mode: str = "conservative"
    # optimization graph style: "all5" (all five convergence criteria as
    # value/tolerance ratios sharing one goal line) or "maxgrad" (MAX gradient only)
    geo_graph_mode: str = "all5"
    # build-tab mode: "beginner" (the guided form), "expert" (paste/load a
    # complete .inp and only pick the calc kind), "mlip" (MACE relaxation), or
    # "crest" (conformer search via WSL)
    build_mode: str = "beginner"
    # Where a Visual row's click goes: "in_app" (ORCAdesk's own 3D viewer) or
    # "system" (hand the file to whatever program the OS associates with it —
    # Avogadro, VMD, ChimeraX, whatever the user already installed). ORCAdesk
    # does the same work either way; only the last step differs, so choosing
    # "system" never means generating less (P5). In-app stays the default: it
    # is the path that needs no second program at all.
    viewer_target: str = "in_app"

    @classmethod
    def load(cls) -> "Settings":
        path = config_file()
        data: dict = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                # Valid JSON that isn't an object (a list, a string, a bare
                # number) must degrade to defaults like corrupt JSON does
                # (P32) — data.items() on a non-dict would crash startup.
                if not isinstance(data, dict):
                    data = {}
                s = cls(**{k: v for k, v in data.items()
                           if k in cls.__dataclass_fields__})
            except (json.JSONDecodeError, TypeError, OSError):
                data, s = {}, cls()
        else:
            s = cls()

        # Every str-typed field is consumed as a string (Path(orca_path),
        # .strip(), f-strings into the UI payload); a wrong-typed value in a
        # hand-edited/corrupted settings.json would otherwise persist itself
        # and crash every get_settings/run from then on (P32). Same guard
        # style as StepConfig.from_dict.
        for f in dataclass_fields(s):
            if isinstance(f.default, str) and not isinstance(getattr(s, f.name), str):
                setattr(s, f.name, f.default)

        # Same guard for the int-typed fields. The parallel-run budgets ride
        # straight into ResourceBudget.resolved(), which compares them with `>`:
        # a string from a hand-edited settings.json would raise TypeError out of
        # a pyqtSlot — errors must be data, never exceptions across that boundary
        # (P32). bool is excluded: it is an int subclass but a different field.
        for f in dataclass_fields(s):
            if type(f.default) is int:
                try:
                    setattr(s, f.name, int(getattr(s, f.name)))
                except (TypeError, ValueError, OverflowError):
                    # OverflowError is the one json can hand us that the other
                    # two do not: `Infinity` is valid JSON to Python's decoder,
                    # and int(float("inf")) raises it. Uncaught, that is a crash
                    # in Settings.load -> Bridge.__init__ on EVERY launch until
                    # the file is edited by hand — exactly what P32 forbids.
                    # (NaN is already a ValueError.)
                    setattr(s, f.name, f.default)

        # mlip_envs is iterated (and .get()-ed) at startup by the Bridge, so a
        # wrong-typed value would crash EVERY launch until the file is fixed
        # (P32). Coerce to the expected shape: a list of dict entries whose
        # id/name/python are non-empty-id strings — the Bridge hard-indexes
        # e["id"], so an id-less entry is exactly the startup crash loop this
        # guard exists to prevent.
        if not isinstance(s.mlip_envs, list):
            s.mlip_envs = []
        else:
            s.mlip_envs = [
                e for e in s.mlip_envs
                if isinstance(e, dict)
                and isinstance(e.get("id"), str) and e.get("id")
                and isinstance(e.get("python"), str)
                and isinstance(e.get("name", ""), str)
            ]

        # fill in sensible defaults on first run
        if not s.orca_path:
            s.orca_path = autodetect_orca()
        if not s.workspace_root:
            s.workspace_root = str(default_workspace_root())
        # migrate the old single-interpreter setting to the env list. Use a
        # deterministic id (hash of the interpreter path) rather than a random
        # one, so that if the app is reloaded before the first save() lands, the
        # re-migration yields the SAME env id instead of churning it.
        # The entry guard above runs BEFORE this, so anything migrated here
        # bypasses it: {"mlip_python": 123} produced an env whose `python` was
        # an int, which the Bridge then probed ("'int' object has no attribute
        # 'strip'") and showed as a broken environment. Apply the same shape
        # rule the guard applies.
        legacy = data.get("mlip_python")
        if not isinstance(legacy, str) or not legacy.strip():
            legacy = ""
        else:
            legacy = legacy.strip()
        if legacy and not s.mlip_envs:
            mig_id = hashlib.sha1(str(legacy).encode("utf-8")).hexdigest()[:8]
            s.mlip_envs = [{"id": mig_id, "name": "MLIP", "python": legacy}]
        return s

    def save(self) -> bool:
        """Persist settings atomically (same tmp + os.replace pattern as
        QueueStore.save_session). Writing settings.json in place could leave a
        half-written file on a crash/power loss, which the next session's
        load() would reject — silently losing every setting. Best-effort: a
        save failure must never break the running app (P32).

        Returns whether it landed. Swallowing the error is right; telling the
        caller nothing is not — Bridge.save_settings answered "Saved." to a
        write that never happened, and the settings were simply gone at the next
        launch (a read-only settings.json, a locked %APPDATA%, a full disk). A
        leftover .tmp is cleaned up so a failed save leaves nothing behind."""
        path = config_file()
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
            os.replace(tmp, path)
            return True
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            return False

    def orca_is_valid(self) -> bool:
        """Whether orca_path points at something that could BE the executable.

        exists() alone said yes to the install directory itself — the natural
        thing to pick in a folder browser — and to any file in it (a .rtf, a
        .txt). Settings then showed ORCA as valid, the run pre-flight passed,
        and the calculation failed at launch with a WinError, which locks it
        (P24). is_file() plus, on Windows, an executable extension is as far as
        this can go without running the thing."""
        if not self.orca_path:
            return False
        p = Path(self.orca_path)
        if not p.is_file():
            return False
        if os.name == "nt":
            return p.suffix.lower() in (".exe", ".bat", ".cmd", ".com")
        # The POSIX analogue of "is this actually runnable": a tarball extracted
        # without preserving modes, or a path pointed at orca's README, is a
        # file that exists and cannot execute -- and the launch would fail with
        # PermissionError into a FAILED calc, which is locked (P24).
        return os.access(p, os.X_OK)

    def mlip_env(self, env_id: str) -> dict | None:
        """The registered MLIP environment with this id, or None."""
        return next((e for e in self.mlip_envs if e.get("id") == env_id), None)
