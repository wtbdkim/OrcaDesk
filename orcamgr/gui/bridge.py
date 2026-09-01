"""
QWebChannel bridge between the HTML/JS front-end and the Python core.

Stage 3 change: the queue now lives in a shared QueueStore (the same object the
HTTP server uses), instead of being owned by the JS side. The desktop UI reads
the queue/log by polling cheap @pyqtSlot getters (get_queue / get_log) on a
QTimer in JS — this keeps the store's worker thread and Qt's UI thread cleanly
separated (no cross-thread Qt signal juggling).

The complete, always-current slot list (with signatures) is the Bridge
declaration in web/globals.d.ts — kept in sync by the tsc check.
"""

from __future__ import annotations

import json
import re
import sys
import threading
from collections import deque
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSlot
from PyQt6.QtWidgets import QFileDialog

from ..config import Settings, autodetect_orca
from ..mlip.env import (
    probe_env, new_env_id, resolve_interpreter, MLIP_BACKENDS,
    env_payload_checking, env_payload_error, env_payload_from_probe, aggregate_status,
    order_envs_by_readiness,
)
from ..crest.env import (
    probe_all as crest_probe_all, aggregate_status as crest_aggregate_status,
)
from ..crest.wsl import list_distros as crest_list_distros
from ..paths import APP_VERSION, APP_AUTHOR, APP_ORG, APP_EMAIL, user_data_root
from ..core.input_generator import StepConfig, build_input_template
from ..core.queue import CalcState, result_from_output
from ..core.parser import parse_file
from ..core.resources import ResourceBudget, auto_cores, auto_ram_mb
from ..state.store import (
    QueueStore, calc_from_dict, calc_to_session_dict, make_engine_factory,
    load_choice_groups, queue_needs_orca, find_dangling_reference,
)
# Response payloads are CONSTRUCTED through these TypedDicts (plain dicts at
# runtime, so the JSON wire format is unchanged) — schemas.py is the single
# source of truth, mirrored for the front-end by web/types.js.
from ..state.schemas import (
    AboutPayload, AutodetectResult, ConflictsResult, ErrorPayload, ExistsResult,
    FepPoint,
    FepResult, GeomAtomPayload, GetCalcResult, GraphLinesResult, LoadResult,
    LogTailResult,
    MlipStatusPayload, MlipInstallPayload, MlipInstallOptionsPayload,
    MutationResult, NmrPayload, OkResult, OrbitalPayload,
    ParsePayload, QrResult, ServerStatusPayload, SettingsPayload,
    StartServerResult, TextResult, TransitionPayload,
    CrestStatusPayload, ConformerPayload,
    WallpaperResult, ExportResult, FramesResult, FavoritesResult,
)


# Line patterns the SCF/geo graph trackers care about (mirror of web/scf_graph.js
# ITER_RE / GEO_RE / GEO_TABLE_RE / GEO_ITEM_RE). get_graph_lines() filters the
# .out to just these so the UI can rebuild the FULL graph history on reattach,
# without re-streaming the whole file through the capped log buffer.
_G_CYCLE = re.compile(r"GEOMETRY OPTIMIZATION CYCLE\s+\d+", re.I)
_G_ITER = re.compile(r"^\s*\d+\s+-?\d+\.\d+\s+-?\d+\.\d+[eE][+-]?\d+")
_G_TABLE = re.compile(r"\|Geometry convergence\|", re.I)
_G_ITEM = re.compile(
    r"(Energy change|RMS gradient|MAX gradient|RMS step|MAX step)\s+-?[\d.]+\s+[\d.]+\s+(YES|NO)", re.I)
_G_DASHES = re.compile(r"-{5,}")
_G_DOTS = re.compile(r"\.{5,}")
# optimization-finished + post-opt-stage markers, so a reattach-seeded graph also
# flips to 100% / "running frequencies…" (mirror of scf_graph.js OPT_DONE_RE / POST_OPT_RE).
# _G_POST is case-SENSITIVE like its JS mirror: the real markers are ORCA's
# UPPERCASE section banners, while every output (opt-only included) carries
# mixed-case look-alikes that must not match (header credits "pre 5.0 version
# of the SCF Hessian"; property echo "Properties with geometric perturbations:",
# "SCF Hessian ... NO").
_G_DONE = re.compile(r"\*\*\*\s*OPTIMIZATION RUN DONE\s*\*\*\*|THE OPTIMIZATION HAS CONVERGED|HURRAY", re.I)
_G_POST = re.compile(
    r"VIBRATIONAL FREQUENCIES|ORCA SCF RESPONSE|GEOMETRIC PERTURBATIONS|CP-?SCF DRIVER|SCF HESSIAN|ANALYTICAL FREQUENCIES|NUMERICAL FREQUENCIES")
# CREST iMTD-GC progress markers (mirror of web/scf_graph.js CrestTracker regexes)
# so get_graph_lines can reseed a reattached CREST run's phase/energy graph. These
# never appear in an ORCA .out (and vice versa), so matching them is additive/safe.
_CREST_GRAPH = re.compile(
    r"CREGEN>\s*E lowest|structures?\s+remain within|Meta-Dynamics Iteration|"
    r"\(\d+\s*MTDs\)|\*MTD\s+\d+\s+completed successfully|CREST terminated normally|"
    r"Change in topology detected|safety termination of CREST|"
    r"number of unique conformers for further calc|Initial Geometry Optimization|"
    r"iMTD-GC SAMPLING|Additional regular MDs|Final Geometry Optimization")
# Frequency (numerical + analytical CP-SCF) and TD-DFT phase-chain markers —
# the mirror of web/progress_panels.js (A_STAGES / TD_STAGES and their
# counters), so a reattached run rebuilds its STEP PANEL too, not just the
# SCF/geometry curve: without these a restart mid-Hessian showed an empty
# Graph tab until the next stage banner happened to stream by (hours, on a
# large molecule). The stage banners are matched case-SENSITIVELY for the
# same reason as _G_POST (ORCA prints them uppercase, and mixed-case
# look-alikes appear in every output); the counter lines accept ORCA's own
# capitalization, mirroring the /i on their JS regexes.
_PHASE_GRAPH = re.compile(
    r"ORCA NUMERICAL FREQUENCIES|[Nn]umber of displacements|[Dd]isplacement\s+\d+\s*/\s*\d+|"
    r"\bNORMAL MODES\b|\bIR SPECTRUM\b|THERMOCHEMISTRY AT\s+[\d.]+|"
    r"Analytic(?:al)? Hessian|[Nn]umber of perturbations|\d+\s*/\s*\d+\s+done|"
    r"\bBATCH\s+\d+\s*:\s*Atoms\s+\d+\s*-\s*\d+|"
    r"TD-DFT XC SETUP|RPA-DIAGONALIZATION|DAVIDSON-DIAGONALIZATION|"
    r"TD-DFT(?:/TDA)? EXCITED STATES|ABSORPTION SPECTRUM VIA TRANSITION|"
    r"CIS/TD-DFT TOTAL ENERGY|Number of roots to be determined|"
    r"\*{4}\s*Iteration\s+\d+\s*\*{4}|ORCA TERMINATED NORMALLY")
# The job meta the step panel's header line shows (method · cores): ORCA's own
# echo of the input file (mirror of progress_panels.js _grabOrcaMeta).
_G_META = re.compile(r"^\|\s*\d+>\s*(?:!|%pal\s+nprocs\b)")
# get_graph_lines keeps only this many most-recent relevant lines, per KIND of
# history (~130 full opt cycles' worth), so a pathological many-thousand-cycle
# .out can't balloon the payload/JSON string. The two kinds are counted apart
# so neither can evict the other: an opt_freq's per-nucleus Hessian batches
# must not push the optimization's cycles out of the window, and the freq
# chain must keep the boot banner that activates it.
_GRAPH_LINES_MAX = 2000
# get_output_tail bounds: at most this many lines, read as one block sized by
# a deliberately generous bytes-per-line estimate — a real 78,846-line ORCA
# opt+freq output averages 61 bytes/line, so 400 leaves room for the widest
# tables and still bounds the read; hard-capped either way.
_LOG_TAIL_MAX = 2000
_LOG_TAIL_BYTES_PER_LINE = 400
_LOG_TAIL_MAX_BYTES = 4 << 20


def tail_lines(path, max_lines: int) -> "tuple[list[str], bool]":
    """The last `max_lines` lines of a text file, plus whether more precede
    them. Reads one bounded block from the END (never a full scan), so a
    multi-hundred-MB .out costs the same as a small one; the first line of that
    block is dropped when the block did not start at byte 0, because it is
    usually a partial line. Decoding is lenient (errors="replace") — the block
    boundary can also split a multi-byte character."""
    n = max(1, min(int(max_lines or 0), _LOG_TAIL_MAX))
    block = min(n * _LOG_TAIL_BYTES_PER_LINE, _LOG_TAIL_MAX_BYTES)
    size = path.stat().st_size
    start = max(0, size - block)
    with open(path, "rb") as f:
        f.seek(start)
        raw = f.read()
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if start > 0 and lines:
        lines = lines[1:]
    return lines[-n:], (start > 0 or len(lines) > n)



class Bridge(QObject):
    def __init__(self, parent_window, store: QueueStore, server_ctl=None):
        super().__init__()
        self.window = parent_window
        self.settings = Settings.load()
        self.store = store            # shared with the HTTP server
        self.server_ctl = server_ctl  # ServerController (may be None)
        # Live MLIP probe results, keyed by env id (each value an MlipEnvPayload
        # dict). Probes import torch (slow), so they run in background threads and
        # the UI polls get_mlip_status(). The dict and settings.mlip_envs are
        # touched from both the worker threads and the Qt UI thread, so all access
        # goes through this lock.
        self._mlip_lock = threading.Lock()
        self._mlip_envs_status: dict[str, dict] = {}
        # Per-env probe generation: a probe publishes only if it is still the
        # LATEST probe for its env — otherwise a slow older probe (e.g. one
        # hanging toward its subprocess timeout) that finishes late would
        # overwrite a newer probe's fresher result.
        self._mlip_probe_seq: dict[str, int] = {}
        for env in self.settings.mlip_envs:
            self._start_mlip_probe(env["id"], env.get("name", ""), env.get("python", ""))
        # One-click MLIP environment creation (venv + torch + a backend). Its
        # own channel like the probe's: pip runs for minutes, so the work is a
        # background thread and the UI polls get_mlip_install_status().
        self._mlip_install_lock = threading.Lock()
        self._mlip_install: dict = {"state": "idle", "step": 0, "steps": 0,
                                    "label": "", "error": "", "cancelled": False}
        self._mlip_installer = None       # live MlipEnvInstaller, for cancel
        self._mlip_install_opts: dict = {"state": "checking", "base_pythons": [],
                                         "gpu": False, "gpu_name": "",
                                         "cuda_index": "", "backends": []}
        self._start_mlip_install_probe()
        # Live CREST status (WSL distro probe). Like MLIP the probe is slow (it
        # spawns wsl.exe + runs `crest --version`), so it runs in a background
        # thread and the UI polls get_crest_status(). Guarded by its own lock.
        self._crest_lock = threading.Lock()
        self._crest_status: dict = {"state": "checking", "distros": [], "wsl": True}
        self._crest_installing = False
        # CREST probe generation (same stale-overwrite guard as MLIP's).
        self._crest_probe_seq = 0
        self._start_crest_probe()

    # --- about / metadata ---
    @pyqtSlot(result=str)
    def get_about(self) -> str:
        return json.dumps(AboutPayload(
            version=APP_VERSION,
            author=APP_AUTHOR,
            org=APP_ORG,
            email=APP_EMAIL,
        ))

    # --- settings ---
    @pyqtSlot(result=str)
    def get_settings(self) -> str:
        return json.dumps(SettingsPayload(
            orca_path=self.settings.orca_path,
            workspace_root=self.settings.workspace_root,
            default_nprocs=self.settings.default_nprocs,
            default_maxcore_mb=self.settings.default_maxcore_mb,
            max_concurrent_jobs=self.settings.max_concurrent_jobs,
            max_total_cores=self.settings.max_total_cores,
            max_total_ram_mb=self.settings.max_total_ram_mb,
            auto_cores=auto_cores(),
            auto_ram_mb=auto_ram_mb(),
            theme=self.settings.theme,
            theme_variant=self.settings.theme_variant,
            glass_level=self.settings.glass_level,
            wallpaper=self.settings.wallpaper,
            eta_mode=self.settings.eta_mode,
            geo_graph_mode=self.settings.geo_graph_mode,
            build_mode=self.settings.build_mode,
            crest_distro=self.settings.crest_distro,
            orca_valid=self.settings.orca_is_valid(),
        ))

    @pyqtSlot(str, result=str)
    def save_settings(self, payload_json: str) -> str:
        try:
            data = json.loads(payload_json or "{}")
            for k in ("orca_path", "workspace_root", "theme", "crest_distro"):
                if k in data:
                    if not isinstance(data[k], str):
                        # a non-string would be persisted and then poison
                        # Path(orca_path) on every later call, every session
                        raise ValueError(f"{k} must be a string.")
                    setattr(self.settings, k, data[k])
            for k in ("default_nprocs", "default_maxcore_mb"):
                if k in data:
                    setattr(self.settings, k, int(data[k]))
            # Parallel-run budgets — clamped here, the trust boundary: these ride
            # straight into admission control, where a negative or absurd value
            # would either stall the queue forever or let it oversubscribe the
            # machine. 0 stays 0 for the two budgets ("auto").
            for key, lo, hi in (("max_concurrent_jobs", 0, 64),   # 0 = as many as fit
                                ("max_total_cores", 0, 1024),
                                ("max_total_ram_mb", 0, 4_000_000)):
                if key in data:
                    try:
                        val = int(data[key])
                    except (TypeError, ValueError):
                        continue
                    setattr(self.settings, key, max(lo, min(hi, val)))
            # opt-ETA mode: only accept known values
            if "eta_mode" in data and data["eta_mode"] in ("conservative", "eager"):
                self.settings.eta_mode = data["eta_mode"]
            # optimization graph mode: only accept known values
            if "geo_graph_mode" in data and data["geo_graph_mode"] in ("all5", "maxgrad"):
                self.settings.geo_graph_mode = data["geo_graph_mode"]
            # build-tab mode: only accept known values
            if "build_mode" in data and data["build_mode"] in ("beginner", "expert", "mlip", "crest"):
                self.settings.build_mode = data["build_mode"]
            # theme variant: shadcn (flat default) or liquidglass
            if "theme_variant" in data and data["theme_variant"] in ("shadcn", "liquidglass"):
                self.settings.theme_variant = data["theme_variant"]
            # Liquid-Glass intensity
            if "glass_level" in data and data["glass_level"] in (
                    "restrained", "moderate", "bold", "vivid", "maximal"):
                self.settings.glass_level = data["glass_level"]
            # Liquid-Glass wallpaper key (preset or "custom"); the custom image
            # itself is written separately via set_wallpaper_image
            if "wallpaper" in data and data["wallpaper"] in (
                    "aurora", "aqua", "sunset", "grape", "graphite", "ocean", "custom"):
                self.settings.wallpaper = data["wallpaper"]
            self.settings.save()
            return self.get_settings()
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            return json.dumps(ErrorPayload(error=str(e)))

    # --- Liquid-Glass custom wallpaper ---
    # The custom wallpaper image is kept OUT of settings.json (which is rewritten
    # on every queue mutation and would otherwise carry a multi-MB base64 blob).
    # It lives as a verbatim data-URI text file in user_data_root, robustly
    # persisted across restarts (unlike the WebEngine profile's localStorage,
    # whose persistence depends on the profile being on-the-record).
    _WALLPAPER_MAX = 24 * 1024 * 1024   # 24 MB data-URI ceiling (~18 MB image)

    def _wallpaper_file(self) -> Path:
        return user_data_root() / "wallpaper_custom.dat"

    @pyqtSlot(str, result=str)
    def set_wallpaper_image(self, data_uri: str) -> str:
        """Persist (or, with ""/oversize input, clear) the Liquid-Glass custom
        wallpaper image as a data URI. Does not touch settings.wallpaper — the
        caller also save_settings({wallpaper:"custom"}). Returns {"ok": true,
        "stored": bool}: `stored` is False when the input was empty / not an
        image / over the size cap (so the file was cleared, not written) — the
        front-end uses it to warn that an oversize image won't persist rather
        than losing it silently at the next launch."""
        try:
            path = self._wallpaper_file()
            text = data_uri or ""
            if not text.startswith("data:image/") or len(text) > self._WALLPAPER_MAX:
                # empty / invalid / too large -> clear any stored image
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                return json.dumps(WallpaperResult(ok=True, stored=False))
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
            return json.dumps(WallpaperResult(ok=True, stored=True))
        except OSError as e:
            return json.dumps(ErrorPayload(error=str(e)))

    @pyqtSlot(result=str)
    def get_wallpaper_image(self) -> str:
        """The stored custom-wallpaper data URI, or "" if none. Returned as a
        bare string (not a JSON envelope) — the front-end feeds it straight to
        an Image src, and "" simply means 'fall back to a preset'."""
        try:
            path = self._wallpaper_file()
            if path.exists():
                return path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            # ValueError covers UnicodeDecodeError (a corrupt/non-UTF-8 file from
            # external tampering) — degrade to "" like Settings.load(), never let
            # it escape the slot across the Qt boundary.
            pass
        return ""

    @pyqtSlot(result=str)
    def autodetect_orca(self) -> str:
        """Scan PATH + common install roots for the ORCA executable and return
        an AutodetectResult JSON. Despite the getter-ish name this is a
        MUTATION slot: a found path is written to settings.orca_path and saved
        immediately (A3) — the JSON envelope (rather than a bare path string)
        exists so the outcome, including failure, is explicit on the wire."""
        try:
            path = autodetect_orca()
        except Exception as e:
            return json.dumps(AutodetectResult(ok=False, path="", error=str(e)))
        if path:
            self.settings.orca_path = path
            self.settings.save()
            return json.dumps(AutodetectResult(ok=True, path=path))
        return json.dumps(AutodetectResult(ok=False, path=""))

    # --- file pickers ---
    @pyqtSlot(result=str)
    def pick_orca_executable(self) -> str:
        filt = "ORCA executable (orca.exe);;All files (*.*)"
        path, _ = QFileDialog.getOpenFileName(self.window, "Locate orca executable", "", filt)
        return path or ""

    # --- MLIP environment (kept separate from ORCA) ---
    @pyqtSlot(result=str)
    def pick_mlip_python(self) -> str:
        exe = "python.exe" if sys.platform.startswith("win") else "python"
        filt = f"Python interpreter ({exe});;All files (*.*)"
        path, _ = QFileDialog.getOpenFileName(
            self.window, "Locate the MLIP environment's Python interpreter", "", filt)
        return path or ""

    def _start_mlip_probe(self, env_id: str, name: str, python: str) -> None:
        """Probe one registered MLIP interpreter in a background thread (importing
        torch is slow and must not block the Qt UI thread). Publishes the result on
        self._mlip_envs_status[env_id] for get_mlip_status() to serve. A probe that
        finishes after its env was removed — or after a NEWER probe of the same
        env started (the generation check) — is discarded."""
        with self._mlip_lock:
            self._mlip_envs_status[env_id] = env_payload_checking(env_id, name, python)
            self._mlip_probe_seq[env_id] = seq = self._mlip_probe_seq.get(env_id, 0) + 1

        def _worker() -> None:
            try:
                result = env_payload_from_probe(env_id, name, probe_env(python))
            except Exception as e:
                # a probe crash must still publish: a silently dead worker
                # would pin the env at "Checking…" (and the build card locked)
                # forever, with re-checks restarting the same failing probe
                result = env_payload_error(env_id, name, python,
                                           f"Probe failed: {e}")
            with self._mlip_lock:
                if (self.settings.mlip_env(env_id) is not None
                        and self._mlip_probe_seq.get(env_id) == seq):
                    self._mlip_envs_status[env_id] = result

        threading.Thread(target=_worker, daemon=True).start()

    @pyqtSlot(str, result=str)
    def add_mlip_env(self, payload_json: str) -> str:
        """Register a new MLIP environment {name?, python} and probe it. Returns the
        updated MlipStatusPayload (or {error} on bad input)."""
        try:
            data = json.loads(payload_json or "{}")
            python = (data.get("python") or "").strip()
            if not python:
                return json.dumps(ErrorPayload(error="No interpreter path given."))
            # a folder pick (e.g. the env dir) -> the python.exe inside it
            python = resolve_interpreter(python)
            name = (data.get("name") or "").strip() or Path(python).parent.name or "MLIP"
            env_id = new_env_id()
            with self._mlip_lock:
                self.settings.mlip_envs.append(
                    {"id": env_id, "name": name, "python": python})
            self.settings.save()
            self._start_mlip_probe(env_id, name, python)
            return self.get_mlip_status()
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            return json.dumps(ErrorPayload(error=str(e)))

    @pyqtSlot(str, result=str)
    def remove_mlip_env(self, env_id: str) -> str:
        """Unregister an MLIP environment. Returns the updated MlipStatusPayload."""
        with self._mlip_lock:
            self.settings.mlip_envs = [
                e for e in self.settings.mlip_envs if e.get("id") != env_id]
            self._mlip_envs_status.pop(env_id, None)
            self._mlip_probe_seq.pop(env_id, None)
        self.settings.save()
        return self.get_mlip_status()

    @pyqtSlot(str, result=str)
    def check_mlip(self, env_id: str = "") -> str:
        """(Re)start the probe for one env (by id) or all envs (empty id). Returns
        the current MlipStatusPayload immediately; the UI polls get_mlip_status()."""
        with self._mlip_lock:
            targets = [e for e in self.settings.mlip_envs
                       if not env_id or e.get("id") == env_id]
        for e in targets:
            self._start_mlip_probe(e["id"], e.get("name", ""), e.get("python", ""))
        return self.get_mlip_status()

    def _mlip_envs_for_run(self) -> list:
        """The registered MLIP envs, ready ones first, for the engine to resolve
        `mlip_env_id == ""` against.

        The engine is handed only {id, name, python}: readiness is a live probe
        result and lives here, so the documented `"" = first ready env` contract
        can only be honoured by ORDERING the list before it goes out. Without
        this, a broken first env captured every MLIP calc while the top-bar pill
        read "ready" off a *different* env (D2: green means verified)."""
        with self._mlip_lock:
            envs = list(self.settings.mlip_envs)
            states = {env_id: (st or {}).get("state", "checking")
                      for env_id, st in self._mlip_envs_status.items()}
        return order_envs_by_readiness(envs, states)

    @pyqtSlot(result=str)
    def get_mlip_status(self) -> str:
        """Aggregate MlipStatusPayload: every registered env (config order) merged
        with its latest probe result."""
        with self._mlip_lock:
            envs = []
            for cfg in self.settings.mlip_envs:
                st = self._mlip_envs_status.get(cfg["id"])
                if st is None:
                    st = env_payload_checking(
                        cfg["id"], cfg.get("name", ""), cfg.get("python", ""))
                envs.append(st)
        return json.dumps(MlipStatusPayload(**aggregate_status(envs)))

    # --- one-click MLIP environment creation ---
    def _start_mlip_install_probe(self) -> None:
        """Detect base interpreters + an NVIDIA GPU in the background. Each
        candidate is LAUNCHED to report its version, so this must never run on
        the Qt UI thread."""
        def _worker() -> None:
            from ..mlip.installer import (find_base_pythons, detect_gpu,
                                          cuda_index_for)
            try:
                gpu = detect_gpu()
                opts = {"state": "ready", "base_pythons": find_base_pythons(),
                        "gpu": bool(gpu["name"]), "gpu_name": gpu["name"],
                        # surfaced so the card can name the build it will fetch:
                        # the wrong one installs fine and dies at the first
                        # kernel launch, which is not a thing to discover later
                        "cuda_index": cuda_index_for(gpu["capability"]),
                        "backends": list(MLIP_BACKENDS.keys())}
            except Exception as e:   # detection must never kill the panel
                opts = {"state": "ready", "base_pythons": [], "gpu": False,
                        "gpu_name": "", "cuda_index": "",
                        "backends": list(MLIP_BACKENDS.keys())}
                self.store.append_log(f"MLIP base-Python detection failed: {e}", "warn")
            with self._mlip_install_lock:
                self._mlip_install_opts = opts

        threading.Thread(target=_worker, daemon=True).start()

    @pyqtSlot(result=str)
    def get_mlip_install_options(self) -> str:
        """What a new MLIP environment can be built from here (detected base
        interpreters, whether a GPU exists, installable backends)."""
        with self._mlip_install_lock:
            opts = dict(self._mlip_install_opts)
        return json.dumps(MlipInstallOptionsPayload(**opts))

    @pyqtSlot(result=str)
    def get_mlip_install_status(self) -> str:
        """Progress of the running (or last) environment creation."""
        with self._mlip_install_lock:
            st = dict(self._mlip_install)
        return json.dumps(MlipInstallPayload(**st))

    @pyqtSlot(str, result=str)
    def create_mlip_env(self, payload_json: str) -> str:
        """Build a new MLIP environment (venv + torch for the chosen device + a
        backend) and register it on success. Returns the install status
        immediately; the UI polls get_mlip_install_status(). Refuses while one
        is already running -- two pips into one tree is corruption."""
        from ..mlip.installer import MlipEnvInstaller, venv_python
        try:
            data = json.loads(payload_json or "{}")
        except json.JSONDecodeError as e:
            return json.dumps(ErrorPayload(error=str(e)))
        name = (data.get("name") or "").strip() or "MACE"
        backend = (data.get("backend") or "mace").strip()
        device = (data.get("device") or "cpu").strip()
        base = (data.get("base_python") or "").strip()
        if not base:      # default to the best detected interpreter
            with self._mlip_install_lock:
                found = list(self._mlip_install_opts.get("base_pythons") or [])
            base = found[0]["python"] if found else ""
        # The folder name is user-typed and becomes a real directory: keep it to
        # characters that cannot escape the envs root or trip Windows.
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-") or "mlip"
        env_dir = user_data_root() / "mlip_envs" / slug

        with self._mlip_install_lock:
            if self._mlip_install.get("state") == "running":
                # Inline, not get_mlip_install_status(): that slot re-takes this
                # non-reentrant lock, so a second click during an install would
                # deadlock Qt's UI thread and freeze the window.
                return json.dumps(MlipInstallPayload(**self._mlip_install))
            self._mlip_install = {"state": "running", "step": 0, "steps": 4,
                                  "label": "Starting…", "error": "",
                                  "cancelled": False}
            installer = self._mlip_installer = MlipEnvInstaller()

        def _on_line(line: str) -> None:
            self.store.append_log(f"[mlip-install] {line}", "info")

        def _on_step(i: int, total: int, label: str) -> None:
            with self._mlip_install_lock:
                self._mlip_install.update({"step": i, "steps": total, "label": label})

        def _worker() -> None:
            try:
                res = installer.run(base, env_dir, backend=backend, device=device,
                                    on_line=_on_line, on_step=_on_step)
            except Exception as e:      # a crash must still release the UI
                res = {"ok": False, "python": "", "error": str(e), "cancelled": False}
            if res.get("ok"):
                python = res.get("python") or str(venv_python(env_dir))
                env_id = new_env_id()
                with self._mlip_lock:
                    self.settings.mlip_envs.append(
                        {"id": env_id, "name": name, "python": python})
                self.settings.save()
                self._start_mlip_probe(env_id, name, python)
                self.store.append_log(
                    f"MLIP environment '{name}' created ({device.upper()}).", "ok")
            elif res.get("cancelled"):
                self.store.append_log("MLIP environment creation cancelled.", "warn")
            else:
                self.store.append_log(
                    f"MLIP environment creation failed: "
                    f"{res.get('error') or 'unknown error'}", "err")
            with self._mlip_install_lock:
                self._mlip_installer = None
                self._mlip_install = {
                    "state": "done" if res.get("ok") else "error",
                    "step": self._mlip_install.get("step", 0), "steps": 4,
                    "label": self._mlip_install.get("label", ""),
                    "error": "" if res.get("ok") else (res.get("error") or ""),
                    "cancelled": bool(res.get("cancelled"))}

        threading.Thread(target=_worker, daemon=True).start()
        return self.get_mlip_install_status()

    @pyqtSlot(result=str)
    def cancel_mlip_install(self) -> str:
        """Stop a running environment creation (terminates pip)."""
        with self._mlip_install_lock:
            installer = self._mlip_installer
        if installer is not None:
            installer.cancel()
        return self.get_mlip_install_status()

    # --- CREST environment (WSL-backed, kept separate from ORCA) ---
    def _start_crest_probe(self) -> None:
        """Probe every usable WSL distro for CREST in a background thread (spawning
        wsl.exe + running `crest --version` must not block the Qt UI thread).
        Publishes the aggregate on self._crest_status for get_crest_status()."""
        with self._crest_lock:
            prev = self._crest_status.get("distros", [])
            self._crest_status = {"state": "checking", "distros": prev,
                                  "wsl": self._crest_status.get("wsl", True)}
            self._crest_probe_seq += 1
            seq = self._crest_probe_seq

        def _worker() -> None:
            status = crest_aggregate_status(crest_probe_all())
            with self._crest_lock:
                # don't clobber a status set by an in-flight install, or a
                # NEWER probe's result (the generation check — a slow older
                # probe must not overwrite a fresher one)
                if not self._crest_installing and self._crest_probe_seq == seq:
                    self._crest_status = status

        threading.Thread(target=_worker, daemon=True).start()

    @pyqtSlot(result=str)
    def get_crest_status(self) -> str:
        """The latest aggregate CrestStatusPayload (state + per-distro probes).
        The probe runs in the background; the UI polls this."""
        with self._crest_lock:
            status = dict(self._crest_status)
        status.setdefault("state", "checking")
        status.setdefault("distros", [])
        status.setdefault("wsl", True)
        # A plain re-probe publishes no install_error, so a Re-check clears a
        # stale one on its own -- the error describes the last install ATTEMPT.
        status.setdefault("install_error", "")
        return json.dumps(CrestStatusPayload(**status))

    @pyqtSlot(result=str)
    def check_crest(self) -> str:
        """Re-probe WSL distros for CREST. Returns the current status immediately;
        the UI polls get_crest_status() for the refreshed result."""
        self._start_crest_probe()
        return self.get_crest_status()

    @pyqtSlot(str, result=str)
    def install_crest(self, distro: str = "") -> str:
        """Auto-install the static CREST binary into ``distro`` (or the first
        usable distro) in a background thread. Returns the current status
        immediately; the UI polls get_crest_status(). No-op if already installing."""
        # The check-and-set stays inside ONE lock (two would let a second click
        # slip between them and start a parallel install). What must not happen
        # inside it is a call to get_crest_status(): that slot re-takes this
        # non-reentrant lock, so the busy branch would deadlock Qt's UI thread
        # and freeze the window. Snapshot here, serialize after the release.
        with self._crest_lock:
            busy = dict(self._crest_status) if self._crest_installing else None
            if busy is None:
                self._crest_installing = True
                self._crest_status = {"state": "checking",
                                      "distros": self._crest_status.get("distros", []),
                                      "wsl": self._crest_status.get("wsl", True)}
        if busy is not None:
            busy.setdefault("state", "checking")
            busy.setdefault("distros", [])
            busy.setdefault("wsl", True)
            busy.setdefault("install_error", "")
            return json.dumps(CrestStatusPayload(**busy))

        def _worker() -> None:
            from ..crest.installer import install_crest as do_install
            target = (distro or "").strip()
            if not target:
                distros = crest_list_distros()
                target = distros[0] if distros else ""
            # Carried onto the status payload: the install runs here, off the
            # click, so the Settings card is the only place the user can learn
            # the outcome without opening the Log tab.
            install_error = ""
            if target:
                # The installer computes actionable diagnostics ("xz missing —
                # sudo apt install xz-utils", download failures); discarding
                # them left only the generic post-probe "CREST not found",
                # with no way to learn WHY the install failed.
                try:
                    res = do_install(target)
                except Exception as e:
                    res = {"ok": False, "error": str(e)}
                if res.get("ok"):
                    self.store.append_log(
                        f"CREST {res.get('version', '')} installed into "
                        f"'{target}'.".replace("  ", " "), "ok")
                else:
                    # A bare reason: every consumer prefixes its own "install
                    # failed" (the card's detail line, the toast), so repeating
                    # the verb here reads as "Install failed - ... failed:".
                    install_error = (f"{target}: "
                                     f"{res.get('error') or 'unknown error'}")
                    self.store.append_log(
                        f"CREST install failed in '{target}': "
                        f"{res.get('error') or 'unknown error'}", "err")
            else:
                install_error = ("no usable WSL distribution found. Install one "
                                 "first, e.g. `wsl --install -d Ubuntu`.")
                self.store.append_log(
                    "CREST install failed: no usable WSL distro found.", "err")
            status = crest_aggregate_status(crest_probe_all())
            with self._crest_lock:
                self._crest_installing = False
                self._crest_status = {**status, "install_error": install_error}

        threading.Thread(target=_worker, daemon=True).start()
        return self.get_crest_status()

    @pyqtSlot(str, result=str)
    def set_crest_distro(self, distro: str) -> str:
        """Persist the preferred WSL distro for CREST and re-probe."""
        self.settings.crest_distro = (distro or "").strip()
        self.settings.save()
        self._start_crest_probe()
        return self.get_crest_status()

    @pyqtSlot(result=str)
    def pick_workspace(self) -> str:
        path = QFileDialog.getExistingDirectory(self.window, "Select workspace folder")
        return path or ""

    def _load_result(self, path: str) -> str:
        """Build the unified LoadResult envelope shared by every load_* slot.
        An empty path means the user cancelled the file dialog — a deliberate
        choice, reported as ok=True/cancelled=True so the front-end never
        surfaces it as an error; ok=False is reserved for real read failures
        (the old per-slot shapes conflated the two — A2). "name" is the file
        stem, used to auto-fill the calculation name.

        Loading a geometry never changes the workspace — the workspace is set
        only from Settings. (It previously adopted the loaded file's parent
        folder as the workspace; reloading a prior run's optimized ``.xyz``,
        which lives inside that calc's own run folder, then silently descended
        the workspace into that folder and nested every later run — the bug
        this removed.)"""
        if not path:
            return json.dumps(LoadResult(
                ok=True, cancelled=True, text="", name="", error=""))
        try:
            p = Path(path)
            # errors="replace": a stray non-UTF-8 byte must degrade to a
            # visible replacement char, not escape the OSError handling as a
            # UnicodeDecodeError crossing the JS boundary.
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return json.dumps(LoadResult(
                ok=False, cancelled=False, text="", name="", error=str(e)))
        return json.dumps(LoadResult(
            ok=True, cancelled=False, text=text, name=p.stem, error=""))

    @pyqtSlot(result=str)
    def load_xyz_file(self) -> str:
        """Pick a .xyz file; returns a LoadResult JSON. The dialog opens at the
        current workspace for convenience; loading does not change it."""
        path, _ = QFileDialog.getOpenFileName(
            self.window, "Load .xyz file", self.settings.workspace_root or "",
            "XYZ file (*.xyz);;All files (*.*)"
        )
        return self._load_result(path)

    @pyqtSlot(result=str)
    def load_inp_file(self) -> str:
        """Pick a complete ORCA .inp; returns a LoadResult JSON (its "name"
        lets expert/raw mode auto-fill the calculation name)."""
        path, _ = QFileDialog.getOpenFileName(
            self.window, "Load ORCA .inp file", "", "ORCA input (*.inp);;All files (*.*)"
        )
        return self._load_result(path)

    @pyqtSlot(str, result=str)
    def load_inp_path(self, path: str) -> str:
        """Load a .inp by PATH (drag-and-drop). Same LoadResult as
        load_inp_file, so the JS side is identical."""
        return self._load_result(path)

    @pyqtSlot(str, result=str)
    def load_xyz_path(self, path: str) -> str:
        """Load a .xyz by PATH (drag-and-drop). Same LoadResult as
        load_xyz_file; loading does not change the workspace."""
        return self._load_result(path)

    # --- option lists ---
    @pyqtSlot(str, result=str)
    def load_choices(self, name: str) -> str:
        # uses the shared loader so PC and phone always see identical options
        return json.dumps(load_choice_groups(name))

    # --- parse an external .out (Results tab) ---
    @pyqtSlot(result=str)
    def parse_out_file(self) -> str:
        path, _ = QFileDialog.getOpenFileName(
            self.window, "Open ORCA .out", "", "ORCA output (*.out);;All files (*.*)"
        )
        if not path:
            # closing the picker is a deliberate choice, not a parse failure —
            # a bare "{}" was indistinguishable from a bad parse and made the
            # UI log a spurious error on every cancel (A2)
            return json.dumps(ParsePayload(cancelled=True))
        return self._parse_path(path)

    @pyqtSlot(str, result=str)
    def parse_out_path(self, path: str) -> str:
        """Parse a specific .out path (used to auto-load finished queue results)."""
        if not path or not Path(path).exists():
            return json.dumps(ErrorPayload(error="file not found"))
        return self._parse_path(path)

    @pyqtSlot(str, result=str)
    def parse_calc_output(self, name: str) -> str:
        """Parse a QUEUED calc's finished output by NAME, dispatching on its
        KIND (result_from_output) — never the external-file folder heuristic in
        _parse_path, which can mis-fire for a calc reusing a removed calc's
        name (workspace folders survive removal, so e.g. a leftover
        crest_conformers.xyz would route a new ORCA .out to the CREST parser)."""
        calc = next((c for c in self.store.list() if c.name == name), None)
        if calc is None:
            return json.dumps(ErrorPayload(error=f"'{name}' is not in the queue."))
        if not calc.output_path or not Path(calc.output_path).exists():
            return json.dumps(ErrorPayload(error="no output file on disk"))
        try:
            return self._payload_json(result_from_output(calc))
        except Exception as e:
            return json.dumps(ErrorPayload(error=str(e)))

    def _parse_path(self, path: str) -> str:
        try:
            # Per-backend dispatch for EXTERNAL files (the path-based mirror of
            # result_from_output's kind dispatch — queued calcs use
            # parse_calc_output's kind dispatch instead): an MLIP result is the
            # engine-written "{name}.mlip.json" (core/queue.py), so route it by
            # suffix — the ORCA text parser would render its JSON as ABNORMAL.
            # A CREST conformer search stores its data in sibling files
            # (crest_conformers.xyz), not the .out itself, so dispatch on their
            # presence to the CREST parser; otherwise the ORCA .out parser.
            folder = Path(path).parent
            if path.endswith(".mlip.json"):
                from ..mlip.parser import parse_mlip_result
                r = parse_mlip_result(path)
            elif (folder / "crest_conformers.xyz").exists() or (folder / "crest_best.xyz").exists():
                from ..crest.parser import parse_crest_result
                r = parse_crest_result(path)
            else:
                r = parse_file(path)
            return self._payload_json(r)
        except Exception as e:
            return json.dumps(ErrorPayload(error=str(e)))

    def _payload_json(self, r) -> str:
            # Send everything that was parsed plus the gating flags; the
            # front-end decides what to show per calc kind (and "Show all"
            # overrides it). is_optimization gates "Final geometry"; show_elec
            # gates general electronic-structure sections; is_conformer_search
            # gates the CREST ensemble list.
            return json.dumps(ParsePayload(
                summary=r.summary_rows(),
                is_optimization=r.is_optimization,
                show_elec=r.shows_electronic_props,
                is_conformer_search=r.is_conformer_search,
                conformers=[
                    ConformerPayload(index=c.index, energy_eh=c.energy_eh,
                                     rel_kcal=c.rel_kcal, n_atoms=len(c.geometry))
                    for c in getattr(r, "conformers", [])
                ],
                transitions=[
                    TransitionPayload(state=t.state, ev=t.energy_ev,
                                      nm=t.wavelength_nm, fosc=t.fosc)
                    for t in r.transitions
                ],
                frequencies=r.frequencies,
                n_imaginary=r.n_imaginary,
                mulliken=r.mulliken_charges,
                nmr=[
                    NmrPayload(idx=i, el=el, iso=iso, aniso=an)
                    for (i, el, iso, an) in r.nmr_shieldings
                ],
                neb_path=r.neb_path,
                neb_path_kind=r.neb_path_kind,
                geometry=[
                    GeomAtomPayload(el=a.symbol, x=a.x, y=a.y, z=a.z)
                    for a in r.geometry
                ],
                orbitals=[
                    OrbitalPayload(idx=o.index, occ=o.occ, ev=o.energy_ev)
                    for o in r.orbitals
                ],
                loewdin=r.loewdin_charges,
                mayer_valences=r.mayer_valences,
                mayer_bonds=r.mayer_bonds,
                tddft_states=r.tddft_states,
                input_keywords=r.input_keywords,
                input_block=r.input_block,
            ))

    # --- raw .inp preview (for entering raw-edit mode) ---
    @pyqtSlot(str, result=str)
    def build_inp_preview(self, calc_json: str) -> str:
        try:
            d = json.loads(calc_json)
            cfg = StepConfig.from_dict(d.get("config", {}))
            use_ph = d.get("geometry_source", "direct") == "reference"
            text = build_input_template(
                cfg,
                int(d.get("charge", 0)),
                int(d.get("multiplicity", 1)),
                use_placeholder=use_ph,
                xyz=d.get("xyz", ""),
            )
            return json.dumps(TextResult(ok=True, text=text))
        except Exception as e:
            return json.dumps(TextResult(ok=False, error=str(e)))

    # --- queue management (shared store) ---
    @pyqtSlot(str, result=str)
    def add_calc(self, calc_json: str) -> str:
        try:
            d = json.loads(calc_json)
            calc = calc_from_dict(d)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return json.dumps(MutationResult(ok=False, error=f"Invalid calculation data: {e}"))
        try:
            self.store.add(calc)
        except ValueError as e:
            return json.dumps(MutationResult(ok=False, error=str(e)))
        return json.dumps(MutationResult(ok=True, snapshot=self.store.snapshot()))

    @pyqtSlot(str, result=str)
    def remove_calc(self, name: str) -> str:
        try:
            ok = self.store.remove(name)
        except ValueError as e:
            return json.dumps(MutationResult(ok=False, error=str(e)))
        return json.dumps(MutationResult(ok=ok, snapshot=self.store.snapshot()))

    @pyqtSlot(result=str)
    def clear_queue(self) -> str:
        try:
            self.store.clear()
        except ValueError as e:
            return json.dumps(MutationResult(ok=False, error=str(e)))
        return json.dumps(MutationResult(ok=True, snapshot=self.store.snapshot()))

    @pyqtSlot(int, int, result=str)
    def reorder_calc(self, from_idx: int, to_idx: int) -> str:
        try:
            self.store.reorder(int(from_idx), int(to_idx))
        except ValueError as e:
            return json.dumps(MutationResult(ok=False, error=str(e)))
        return json.dumps(MutationResult(ok=True, snapshot=self.store.snapshot()))

    @pyqtSlot(str, str, result=str)
    def update_calc(self, old_name: str, calc_json: str) -> str:
        """Edit a pending calc in place (preserves its queue position)."""
        try:
            d = json.loads(calc_json)
            calc = calc_from_dict(d)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return json.dumps(MutationResult(ok=False, error=f"Invalid calculation data: {e}"))
        try:
            self.store.replace(old_name, calc)
        except ValueError as e:
            return json.dumps(MutationResult(ok=False, error=str(e)))
        return json.dumps(MutationResult(ok=True, snapshot=self.store.snapshot()))

    @pyqtSlot(result=str)
    def get_queue(self) -> str:
        return json.dumps(self.store.snapshot())

    @pyqtSlot(str, result=str)
    def get_calc(self, name: str) -> str:
        """Return the FULL data (config / xyz / raw_text / charge / ...) for one
        calculation, so it can be edited even when it isn't in this session's
        in-memory copy — e.g. a calc restored from a previous session or added
        from the phone (the polled snapshot omits these fields)."""
        c = self.store.get(name)
        if c is None:
            return json.dumps(GetCalcResult(ok=False, error="not found"))
        return json.dumps(GetCalcResult(ok=True, calc=calc_to_session_dict(c)))

    @pyqtSlot(int, result=str)
    def get_log(self, since: int) -> str:
        return json.dumps(self.store.log_since(since))

    def _calc_run_dir(self, name: str) -> Path:
        """The run folder for a queued calc: derived from its persisted
        output_path when it has one (a session-restored calc may live in a
        DIFFERENT workspace than the currently-configured one — parse_calc_output
        already resolves through output_path, so the .inp viewer, graph reseed
        and conformer export must too, or the same calc renders its result but
        fails these three), else {current workspace}/{name}."""
        calc = self.store.get(name)
        if calc is not None and calc.output_path:
            return Path(calc.output_path).parent
        return Path(self.settings.workspace_root) / name

    @pyqtSlot(str, result=str)
    def get_inp(self, name: str) -> str:
        """Return the on-disk ORCA .inp for a calculation, so a running or finished
        job's actual input can be viewed read-only (not only editable ones)."""
        try:
            p = self._calc_run_dir(name) / f"{name}.inp"
            if not p.exists():
                return json.dumps(TextResult(ok=False, error="no input on disk yet"))
            return json.dumps(TextResult(ok=True, text=p.read_text(encoding="utf-8", errors="replace")))
        except Exception as e:
            return json.dumps(TextResult(ok=False, error=str(e)))

    @pyqtSlot(str, result=str)
    def get_graph_lines(self, name: str) -> str:
        """Return ONLY the lines of a calculation's .out that the Graph tab is
        built from — SCF iterations, geometry convergence, and the frequency /
        TD-DFT / CREST phase chains — in file order, so the UI can rebuild the
        full graph history independent of the capped log buffer.

        Takes the calc NAME and resolves the run folder server-side via
        _calc_run_dir (state-independent — works mid-run and for restored
        sessions alike, even from an old workspace). Reads line-by-line and
        keeps only the most recent _GRAPH_LINES_MAX relevant lines, so even a
        many-thousand-cycle .out yields a bounded payload (the trackers only
        need the recent history; dropped oldest cycles just shorten the
        seeded graph's tail). Convergence history and phase-chain history get
        a window EACH, merged back into file order, so a Hessian's hundreds of
        per-nucleus batch lines cannot evict the optimization cycles that ran
        before them — and a line both windows keep is emitted once."""
        try:
            out_path = self._calc_run_dir(name) / f"{name}.out"
            if not out_path.exists():
                return json.dumps(GraphLinesResult(ok=False, error="no output", lines=[]))
            conv: deque[tuple[int, str]] = deque(maxlen=_GRAPH_LINES_MAX)   # SCF/geo/CREST curve
            phase: deque[tuple[int, str]] = deque(maxlen=_GRAPH_LINES_MAX)  # freq/TD-DFT step chain
            in_table = False
            saw_item = False
            with open(out_path, "r", encoding="utf-8", errors="replace") as f:
                for i, raw in enumerate(f):
                    ln = raw.rstrip("\r\n")
                    if _G_CYCLE.search(ln):
                        # also the phase chain's reset marker (a pre-step
                        # Hessian belongs to the cycle it ran in), so it goes
                        # in both windows
                        conv.append((i, ln))
                        phase.append((i, ln))
                    elif _G_TABLE.search(ln):
                        in_table = True
                        saw_item = False
                        conv.append((i, ln))
                    elif in_table and _G_ITEM.search(ln):
                        saw_item = True
                        conv.append((i, ln))
                    elif in_table and saw_item and (
                            ln.strip() == "" or _G_DASHES.search(ln) or _G_DOTS.search(ln)):
                        # table terminator — kept so GeoTracker commits the step
                        conv.append((i, ln))
                        in_table = False
                        saw_item = False
                    elif _G_DONE.search(ln):
                        # opt-finished marker — a seeded graph flips to 100%
                        conv.append((i, ln))
                    elif _G_POST.search(ln):
                        # post-opt-stage banners do double duty: they flip a
                        # seeded opt graph to "running frequencies…" AND boot
                        # the freq step chain, so they go in both windows
                        conv.append((i, ln))
                        phase.append((i, ln))
                    elif _G_ITER.match(ln):
                        conv.append((i, ln))
                    elif _CREST_GRAPH.search(ln):
                        # CREST phase/energy markers (a CREST .out has no ORCA
                        # SCF/geo lines, so this branch is reached cleanly)
                        conv.append((i, ln))
                    elif _PHASE_GRAPH.search(ln) or _G_META.match(ln):
                        # freq / TD-DFT stage counters + the echoed method/cores
                        phase.append((i, ln))
            merged = sorted(set(conv) | set(phase))   # a shared line lands once
            return json.dumps(GraphLinesResult(ok=True, lines=[ln for _, ln in merged]))
        except Exception as e:
            return json.dumps(GraphLinesResult(ok=False, error=str(e), lines=[]))

    @pyqtSlot(str, int, result=str)
    def get_output_tail(self, name: str, max_lines: int) -> str:
        """The LAST `max_lines` lines of a calculation's .out, for the Raw
        log's restored history after a reattach.

        The live log is a tail, not a file view: a job reattached in a new
        session streams only what ORCA writes from that moment, so the Raw tab
        opened empty on a job that had been running for hours. This is the
        recent past, read straight off disk and marked as history in the UI —
        it deliberately does NOT go through the shared log buffer, which is
        capped and shared with the phone client.

        Reads from the END of the file (a bounded seek + decode, never a full
        scan) so a multi-hundred-MB .out costs the same as a small one, and
        drops the first line of the block read, which is usually a partial
        line."""
        try:
            out_path = self._calc_run_dir(name) / f"{name}.out"
            if not out_path.exists():
                return json.dumps(LogTailResult(ok=False, error="no output", lines=[]))
            lines, more = tail_lines(out_path, max_lines)
            return json.dumps(LogTailResult(
                ok=True, lines=lines, file=out_path.name, truncated=more))
        except Exception as e:
            return json.dumps(LogTailResult(ok=False, error=str(e), lines=[]))

    @pyqtSlot(str, result=str)
    def export_conformers(self, name: str) -> str:
        """Split a finished CREST search's ensemble into per-conformer .xyz files
        under {workspace}/{name}/conformers/ ({name}_c1.xyz = the best conformer,
        …). No file dialog — the files land next to the run, in a conformers/
        subfolder. Returns {ok, count, folder} or {ok:false, error}."""
        from ..crest.export import export_conformers as _export
        try:
            folder = self._calc_run_dir(name)
            dest = folder / "conformers"
            written = _export(folder / "crest_conformers.xyz", dest, name)
            return json.dumps(ExportResult(ok=True, count=len(written), folder=str(dest)))
        except Exception as e:
            return json.dumps(ExportResult(ok=False, error=str(e)))

    # --- in-app 3D structure viewer (Results tab) ---
    @pyqtSlot(str, result=str)
    def get_conformer_frames(self, name: str) -> str:
        """Frames for a queued CREST search's ensemble, for the in-app 3D viewer:
        reads {run}/crest_conformers.xyz (or crest_best.xyz) and returns
        {ok, title, frames:[{label, xyz, energy}]} — xyz is raw frame text 3Dmol
        parses directly. On-demand (not in the result payload) so the normal
        Results render stays light even for a 100-conformer ensemble."""
        from ..molview import frames_from_file
        try:
            folder = self._calc_run_dir(name)
            p = folder / "crest_conformers.xyz"
            if not p.exists():
                p = folder / "crest_best.xyz"
            if not p.exists():
                return json.dumps(FramesResult(ok=False, error="No conformer ensemble on disk."))
            frames = frames_from_file(p, label_prefix=f"{name}_c")
        except OSError as e:
            return json.dumps(FramesResult(ok=False, error=str(e)))
        if not frames:
            return json.dumps(FramesResult(ok=False, error="No structures found in the ensemble."))
        return json.dumps(FramesResult(ok=True, title=name, frames=frames))

    @pyqtSlot(result=str)
    def browse_xyz_folder(self) -> str:
        """Pick any folder and return every ``*.xyz`` structure in it (natural
        filename order; each file may itself be multi-frame) as viewer frames —
        {ok, title, frames:[{label, xyz, energy}]}. General: works on any folder
        (e.g. a CREST ``conformers/`` folder). {ok:false, cancelled:true} when the
        picker is closed, {ok:false, error} otherwise."""
        folder = QFileDialog.getExistingDirectory(
            self.window, "Select a folder of .xyz structures")
        if not folder:
            return json.dumps(FramesResult(ok=False, cancelled=True))
        from ..molview import frames_from_folder
        try:
            frames = frames_from_folder(folder)
        except OSError as e:
            return json.dumps(FramesResult(ok=False, error=str(e)))
        if not frames:
            return json.dumps(FramesResult(ok=False, error="No .xyz structures in that folder."))
        return json.dumps(FramesResult(ok=True, title=Path(folder).name,
                                       folder=folder, frames=frames))

    # --- 3D viewer favorites (starred conformers/structures) ---
    @pyqtSlot(str, result=str)
    def get_favorites(self, source: str) -> str:
        """Favorited frame labels for a viewer source ("calc:<name>" /
        "folder:<path>"). Returns {"ok": true, "labels": [...]}."""
        from ..favorites import get as _get
        try:
            return json.dumps(FavoritesResult(ok=True, labels=_get(source)))
        except Exception as e:
            return json.dumps(FavoritesResult(ok=False, error=str(e), labels=[]))

    @pyqtSlot(str, str, bool, result=str)
    def toggle_favorite(self, source: str, label: str, on: bool) -> str:
        """Star/unstar one frame under a source; persist and return the updated
        label list. {"ok": true, "labels": [...]}."""
        from ..favorites import toggle as _toggle
        try:
            return json.dumps(FavoritesResult(ok=True, labels=_toggle(source, label, on)))
        except Exception as e:
            return json.dumps(FavoritesResult(ok=False, error=str(e), labels=[]))

    @pyqtSlot(str, str, str, result=str)
    def export_frames(self, dest_kind: str, dest: str, frames_json: str) -> str:
        """Write a set of viewer frames (favorites) to a `favorites/` subfolder as
        one .xyz each. `dest_kind` is "calc" (dest = a queued calc name → its run
        folder) or "folder" (dest = a folder path). `frames_json` is a list of
        {label, xyz}. Returns {ok, count, folder} or {ok:false, error}."""
        try:
            frames = json.loads(frames_json or "[]")
            if dest_kind == "calc":
                base = self._calc_run_dir(dest)
            else:
                base = Path(dest)
            out_dir = base / "favorites"
            out_dir.mkdir(parents=True, exist_ok=True)
            count = 0
            for fr in frames:
                xyz = fr.get("xyz", "")
                if not xyz:
                    continue
                fname = re.sub(r"[^A-Za-z0-9._-]+", "_", str(fr.get("label", "frame"))).strip("_") or "frame"
                text = xyz if xyz.endswith("\n") else xyz + "\n"
                (out_dir / f"{fname}.xyz").write_text(text, encoding="utf-8")
                count += 1
            return json.dumps(ExportResult(ok=True, count=count, folder=str(out_dir)))
        except Exception as e:
            return json.dumps(ExportResult(ok=False, error=str(e)))

    # --- run / cancel ---
    @pyqtSlot(result=str)
    def get_free_energy_profile(self) -> str:
        """Gibbs free energies of finished calculations, in queue order, for the
        free-energy profile. Only calcs that have a Gibbs value (i.e. a frequency
        run) are included. Energies are absolute Hartree; the UI picks a
        reference and converts to relative kcal/mol or kJ/mol."""
        pts = []
        for c in self.store.list():
            if c.state != CalcState.DONE:
                continue
            # Parse-on-miss: DONE calcs restored from a previous session aren't
            # eagerly re-parsed at startup, so read the .out on demand here (only
            # when the user actually opens the free-energy profile).
            # Writing c.result WITHOUT the store lock is safe only via the
            # DONE-frozen invariant (P24): the engine never touches a DONE calc
            # again, and this loop reads DONE calcs exclusively, so no other
            # thread writes this field concurrently (A4). If DONE calcs ever
            # become mutable elsewhere, this must take the store lock.
            if not c.result and c.output_path:
                try:
                    # kind dispatch, NOT parse_file: a DONE mlip/crest calc's
                    # output must go through its own parser — caching a wrong
                    # ORCA parse here would poison ref.result for the engine's
                    # reference resolution (which only parses on a miss).
                    c.result = result_from_output(c)
                except Exception:
                    pass
            if not c.result:
                continue
            g = getattr(c.result, "gibbs_eh", None)
            if g is None:
                continue
            pts.append(FepPoint(
                name=c.name,
                gibbs_eh=g,
                final_energy_eh=getattr(c.result, "final_energy_eh", None),
                kind=c.kind,
            ))
        return json.dumps(FepResult(ok=True, points=pts))

    @pyqtSlot(result=str)
    def check_overwrite_conflicts(self) -> str:
        """Return the names of queued calculations that would overwrite an
        existing result on disk (a {name}.out already in the workspace), so the
        UI can warn before a run clobbers earlier work. Excluded states:
        DONE is skipped by the run loop anyway; FAILED is locked (P24) — it
        never runs again, so it has nothing to overwrite; and a CANCELLED calc
        overwriting its own partial .out is the whole point of a retry, not a
        loss worth warning about. The warning exists for PENDING/BLOCKED name
        reuse — a fresh calc whose name would clobber an earlier good result."""
        try:
            ws = Path(self.settings.workspace_root)
            conflicts = []
            # RUNNING is excluded too: a reattached (or reattachable) detached
            # job keeps appending to its own .out — that is not an overwrite.
            skip = {CalcState.DONE, CalcState.FAILED, CalcState.CANCELLED,
                    CalcState.RUNNING}
            for c in self.store.list():
                if c.state in skip:
                    continue
                out_path = ws / c.name / f"{c.name}.out"
                if out_path.exists():
                    conflicts.append(c.name)
            return json.dumps(ConflictsResult(ok=True, conflicts=conflicts))
        except Exception as e:
            return json.dumps(ConflictsResult(ok=False, error=str(e), conflicts=[]))

    @pyqtSlot(str, result=str)
    def has_existing_output(self, name: str) -> str:
        """True when the workspace already holds a result artifact for `name`
        ({name}.out, or the MLIP worker's {name}.mlip.json). The live queue
        accepts adds while a run is in progress, and those start without the
        Run-click overwrite-conflicts modal — the front-end asks here first so
        a name reuse can be confirmed before it lands in a running queue."""
        try:
            d = Path(self.settings.workspace_root) / str(name)
            exists = (d / f"{name}.out").exists() or (
                d / f"{name}.mlip.json").exists()
            return json.dumps(ExistsResult(ok=True, exists=exists))
        except Exception as e:
            return json.dumps(ExistsResult(ok=False, error=str(e), exists=False))

    @pyqtSlot(str, result=str)
    def run_queue(self, skip_names_json: str = "") -> str:
        calcs = self.store.list()
        # ORCA is required only if a non-MLIP calc will actually run — an
        # all-MLIP queue runs through the user's MACE env without ORCA
        # configured. Shared helper so this decision cannot drift from the
        # phone's /api/run (P4, A18).
        if queue_needs_orca(calcs) and not self.settings.orca_is_valid():
            return json.dumps(OkResult(ok=False, error="ORCA path is not set or invalid. Check Settings."))
        # names the user chose to skip (e.g. to preserve existing results)
        try:
            skip_names = set(json.loads(skip_names_json)) if skip_names_json else set()
        except Exception:
            skip_names = set()
        # validate references resolve to existing names (shared with /api/run)
        ref_err = find_dangling_reference(calcs)
        if ref_err:
            return json.dumps(OkResult(ok=False, error=ref_err))
        factory = make_engine_factory(self.store, self.settings.orca_path,
                                      self.settings.workspace_root, skip_names,
                                      mlip_envs=self._mlip_envs_for_run(),
                                      crest_distro=self.settings.crest_distro,
                                      budget=self._budget())
        try:
            self.store.start_run(factory)
        except RuntimeError as e:
            return json.dumps(OkResult(ok=False, error=str(e)))
        except ValueError as e:
            return json.dumps(OkResult(ok=False, error=str(e)))
        return json.dumps(OkResult(ok=True))

    def _budget(self) -> ResourceBudget:
        """The parallel-run admission budget from settings (0 = auto, resolved
        against this machine by the engine)."""
        return ResourceBudget.from_settings(self.settings)

    @pyqtSlot(result=str)
    def cancel_queue(self) -> str:
        ok = self.store.cancel_run()
        return json.dumps(OkResult(ok=ok))

    @pyqtSlot(result=str)
    def stop_after_current(self) -> str:
        """Graceful drain: finish the running job, then stop; leave the rest
        pending (as opposed to cancel_queue, which kills the running job)."""
        ok = self.store.request_stop_after_current()
        return json.dumps(OkResult(ok=ok))

    def resume_session_if_running(self) -> None:
        """Startup hook (not a JS slot): if a calculation from the previous
        session is still running, reattach to it and continue the queue. The
        store has already restored + reconciled the queue in load_session()."""
        if not self.store.has_live_running():
            return
        # Only ORCA calcs need a valid ORCA path; an all-CREST/MLIP queue can
        # reattach with no ORCA configured (P4, same gate as run_queue).
        if queue_needs_orca(self.store.list()) and not self.settings.orca_is_valid():
            self.store.append_log(
                "A calculation from the previous session is still running, but the "
                "ORCA path is invalid — cannot reattach. Fix it in Settings.", "warn")
            return
        factory = make_engine_factory(self.store, self.settings.orca_path,
                                      self.settings.workspace_root,
                                      mlip_envs=self._mlip_envs_for_run(),
                                      crest_distro=self.settings.crest_distro,
                                      budget=self._budget())
        try:
            self.store.start_run(factory)
            self.store.append_log(
                "Reattached to a calculation still running from the previous session.",
                "info")
        except (RuntimeError, ValueError) as e:
            self.store.append_log(f"Could not reattach: {e}", "warn")

    # --- server control (phone sync) ---
    @pyqtSlot(result=str)
    def get_server_status(self) -> str:
        # "available" must mean "start_server would work": the controller
        # object alone is not enough — an install without fastapi/uvicorn
        # would otherwise show the feature as available and fail on use (A9).
        if not self.server_ctl or not self.server_ctl.is_available():
            return json.dumps(ServerStatusPayload(available=False, running=False))
        return json.dumps(ServerStatusPayload(
            available=True,
            running=self.server_ctl.is_running(),
            url=self.server_ctl.url(),
            token=self.store.token,
            clients=self.store.active_clients(),
        ))

    @pyqtSlot(result=str)
    def get_connect_qr(self) -> str:
        """Return a data-URI PNG QR encoding the connect URL (address + PIN),
        so scanning it on a phone opens the UI already authorized."""
        if not self.server_ctl or not self.server_ctl.is_running():
            return json.dumps(QrResult(ok=False, error="Server is not running."))
        connect_url = f"{self.server_ctl.url()}/?pin={self.store.token}"
        try:
            import qrcode
            import io, base64
            img = qrcode.make(connect_url)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data = base64.b64encode(buf.getvalue()).decode("ascii")
            return json.dumps(QrResult(
                ok=True,
                data_uri=f"data:image/png;base64,{data}",
                url=connect_url,
            ))
        except ImportError:
            return json.dumps(QrResult(ok=False, error="qrcode library not installed.",
                                       url=connect_url))
        except Exception as e:
            return json.dumps(QrResult(ok=False, error=str(e), url=connect_url))

    @pyqtSlot(result=str)
    def start_server(self) -> str:
        if not self.server_ctl:
            return json.dumps(StartServerResult(ok=False, error="Server support is not available."))
        try:
            self.server_ctl.start()
            return json.dumps(StartServerResult(ok=True, url=self.server_ctl.url(),
                                                token=self.store.token))
        except Exception as e:
            return json.dumps(StartServerResult(ok=False, error=str(e)))

    @pyqtSlot(result=str)
    def stop_server(self) -> str:
        if not self.server_ctl:
            return json.dumps(OkResult(ok=False, error="Server support is not available."))
        try:
            self.server_ctl.stop()
            return json.dumps(OkResult(ok=True))
        except Exception as e:
            return json.dumps(OkResult(ok=False, error=str(e)))
