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
from collections import OrderedDict
from types import SimpleNamespace
import time
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSlot
from PyQt6.QtWidgets import QFileDialog

from ..config import Settings, autodetect_orca
from ..mlip.env import (
    probe_env, new_env_id, resolve_interpreter, MLIP_BACKENDS,
    env_payload_checking, env_payload_error, env_payload_from_probe, aggregate_status,
    order_envs_by_readiness,
)
from ..draw2d import env as d2env
from ..crest.env import (
    probe_all as crest_probe_all, aggregate_status as crest_aggregate_status,
)
from ..crest.shell import (
    list_targets as crest_list_targets, transport_kind as crest_transport,
)
from ..paths import (APP_VERSION, APP_AUTHOR, APP_EMAIL,
                     config_file, user_data_root)
from ..textio import repair_ansi_line
from .. import uistate
from ..core.input_generator import (GEOMETRY_PLACEHOLDER, StepConfig,
                                    CREST_TEMPLATE_KINDS, MLIP_TEMPLATE_KINDS,
                                    TEMPLATE_JOB_KEYWORDS,
                                    build_input_template, is_level_text,
                                    render_raw_input, render_template_text,
                                    template_config, template_hess_need)
from ..core.queue import (CalcState, GeometrySource, find_parent_hessian,
                          result_from_output)
from ..core.structure import (
    apply_constraint_target, apply_constraint_targets, check_geometry, compare_atom_order,
    displace_along_mode, parse_block,
)
from ..core.hessfile import HessError, read_hess
from ..core.normalmodes import modes_for_report, place_on, read_out_modes
from ..core.parser import parse_file
from ..core.replay import (  # noqa: F401  (the patterns: tests read them here)
    ReplayCache, graph_lines_of, _GRAPH_LINES_MAX, _G_CYCLE, _G_ITER, _G_TABLE, _G_ITEM,
    _G_DASHES, _G_DOTS, _G_DONE, _G_POST, _CREST_GRAPH, _G_ITERTIME, _G_SCAN,
    _IRC_GRAPH, _IRC_ROW, _PHASE_GRAPH, _G_META,
)
from ..core.resources import ResourceBudget, auto_cores, auto_ram_mb
from ..core.rundir import RESERVED_NAMES, holds_output, names_with_output
from ..state.store import (
    QueueStore, QueueOpError, calc_from_dict, calc_to_session_dict, is_rerun,
    make_engine_factory, load_choice_groups, queue_needs_orca,
    find_dangling_reference, constraint_warning, raw_geometry_warning,
    keep_graph_fields, xyz_file_entries,
)
from ..state import inpsets, workflows
# Response payloads are CONSTRUCTED through these TypedDicts (plain dicts at
# runtime, so the JSON wire format is unchanged) — schemas.py is the single
# source of truth, mirrored for the front-end by web/types.js.
from ..state.schemas import (
    AboutPayload, AutodetectResult, ConflictsResult, ErrorPayload, ExistsResult,
    ExistingOutputsResult, FepPoint,
    FepResult, GeomAtomPayload, GetCalcResult, GraphLinesResult, LoadResult,
    LogTailResult, OutputInfoResult, OutputLinesResult, OutputFindResult,
    MlipStatusPayload, MlipInstallPayload, MlipInstallOptionsPayload,
    Draw2dStatusPayload, Draw2dInstallPayload, Draw2dPerceptionPayload,
    Draw2dSaveResult, MoLobesPayload,
    MutationResult, NboJobPayload, NboResultPayload, NmrPayload, OkResult,
    OrbitalPayload, SavedFileResult,
    ParsePayload, QrResult, ScanCoordPayload, ScanPointPayload,
    ServerStatusPayload, SettingsPayload,
    StartServerResult, TextResult, TransitionPayload,
    CrestStatusPayload, ConformerPayload,
    WallpaperResult, ExportResult, FramesResult, FavoritesResult,
    StructureSetsResult, PickedResultPayload, PickedFilePayload,
    PlotOptionsResult, CubeJobPayload, CubeDataResult, WorkspaceResultsResult,
    AtomOrderPayload, StructureCheckPayload, ConstraintApplyPayload,
    ConstraintApplyAllPayload,
    HessModesPayload, HessModePayload, ModeShiftPayload, VibModePayload,
    InpSetsResult, InpSetResult, SystemResult, QueueOpsResult,
    XyzFilesResult, WorkflowsResult, WorkflowResult,
    IrcStructureFile, IrcStructuresResult,
    DevicePayload, DevicesPayload,
)
from ..remote.devices import (DEFAULT_WORKDIR, DeviceInfo, DeviceMonitor,
                              default_ssh_config, normalize_device,
                              normalize_devices, parse_ssh_config)
from ..remote.ssh import SshTransport

# Ceiling on the workspace scan behind the Results picker. Generous next to any
# real workspace, and the point is only that a pathological folder cannot turn a
# tab switch into an unbounded directory crawl.
_WORKSPACE_SCAN_MAX = 500

# The two files a CREST run leaves its ensemble in. Their frames are labelled
# after the RUN ("{run}_c1", the name the per-conformer export writes) rather
# than after the file, because those labels key the favorites store.
_CREST_ENSEMBLE = {"crest_conformers.xyz", "crest_best.xyz"}


# get_output_tail bounds: at most this many lines, read as one block sized by
# a deliberately generous bytes-per-line estimate — a real 78,846-line ORCA
# opt+freq output averages 61 bytes/line, so 400 leaves room for the widest
# tables and still bounds the read; hard-capped either way.
_LOG_TAIL_MAX = 2000
_LOG_TAIL_BYTES_PER_LINE = 400
_LOG_TAIL_MAX_BYTES = 4 << 20


_REMOTE_LOG_DONE = False


def _install_remote_log() -> None:
    """Send the ``orcamgr.remote`` logger (transport detail the UI never
    shows, design D6) to a rotating ``remote.log`` in the user data folder,
    once per process. Best-effort: no log is better than no app."""
    global _REMOTE_LOG_DONE
    if _REMOTE_LOG_DONE:
        return
    _REMOTE_LOG_DONE = True
    import logging
    from logging.handlers import RotatingFileHandler
    try:
        h = RotatingFileHandler(str(user_data_root() / "remote.log"),
                                maxBytes=1 << 20, backupCount=2, encoding="utf-8")
    except OSError:
        return
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    lg = logging.getLogger("orcamgr.remote")
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    lg.propagate = False


def _bond_pairs(bonds_json: str):
    """A slot's `bonds_json` as a list, or None (empty or unreadable): perceive
    the bonds from the coordinates instead."""
    try:
        v = json.loads(bonds_json) if bonds_json else None
    except ValueError:
        return None
    return v if isinstance(v, list) else None


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
    # surrogateescape + per-line repair, like the live tail: an ORCA .out is
    # mixed-encoding on Windows (its own strings UTF-8, argv paths in the ANSI
    # code page), so a Korean calc name or workspace decoded as pure UTF-8 came
    # back as a run of U+FFFD. See orcamgr/textio.py.
    lines = [repair_ansi_line(ln)
             for ln in raw.decode("utf-8", errors="surrogateescape").splitlines()]
    if start > 0 and lines:
        lines = lines[1:]
    return lines[-n:], (start > 0 or len(lines) > n)


# Windows refuses these as file or directory names whatever the extension, and
# "nul" is worse than refused: it is a path that always exists and cannot be
# listed. The folder rule's own set (core/rundir.py), which guards calc and
# system names for the same reason.
_WINDOWS_DEVICE_NAMES = RESERVED_NAMES


class Bridge(QObject):
    def __init__(self, parent_window, store: QueueStore, server_ctl=None):
        super().__init__()
        self.window = parent_window
        self.settings = Settings.load()
        self.store = store            # shared with the HTTP server
        # each .out's replay digest and how far it was read (core/replay.py)
        self._replay = ReplayCache()
        # a queued calc's parsed report, keyed by (output path, kind) and
        # stamped with the file's (size, mtime_ns); see parse_calc_output_async
        self._parse_lock = threading.Lock()
        self._parse_cache: "OrderedDict[tuple, tuple]" = OrderedDict()
        self._parse_busy: set = set()
        self._parse_failed: dict = {}
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
        # Remote compute devices (orcamgr/remote/): the ~/.ssh/config hosts,
        # probed on daemon threads; the Settings card polls get_devices(). A
        # probe that finds a device ready settles the clean-up list a cancel
        # left there. The enabled ones are probed now, so the first run
        # already knows which are up.
        _install_remote_log()
        self._remote_transport = SshTransport()
        self._remote = DeviceMonitor(self._remote_transport,
                                     on_ready=self._remote_device_ready)
        enabled = [d for d in self.settings.remote_devices if d.get("enabled")]
        if enabled and self._remote_transport.available():
            self._remote.refresh(enabled)
        # Orbital / density cubes for the Results-tab 3D viewer. orca_plot runs
        # from ~0.2 s (one MO) to ~10 s (an SCF density) on a mid-sized system —
        # short, but not short enough to block Qt's UI thread — so it follows the
        # probe pattern: background thread, UI polls get_cube_status(). The cube
        # itself is fetched once by get_cube_data() and never rides the poll; at
        # ~3 MB per file that would be megabytes a second across the channel.
        self._cube_lock = threading.Lock()
        self._cube: dict = {"state": "idle", "label": "", "error": "", "kind": "",
                            "index": 0, "operator": 0, "grid": 0,
                            "cached": False, "seconds": 0.0, "path": ""}
        self._cube_path = ""              # finished cube, for get_cube_data()
        # natural-orbital analysis: same background-thread + poll shape as the
        # cube generation above, one at a time, the result fetched once
        self._nbo_lock = threading.Lock()
        self._nbo: dict = {"state": "idle", "source": "", "error": "", "seconds": 0.0}
        self._nbo_result: dict = {}
        # The NBO basis itself (nbf**2 floats per spin) for the result last
        # analysed -- what a natural bond orbital is drawn from. One entry,
        # keyed by folder/base and checked against the .gbw's mtime; a cube
        # request for another result rebuilds it (seconds), never caches it
        # to disk. Guarded by _nbo_lock, held only for the dict access.
        self._nbo_sets: dict = {}
        # The 2D drawing's orbital overlay (orcamgr/nbo/molobes.py): ONE
        # wavefunction plus its S^1/2, keyed by folder/base and the .gbw's
        # mtime, built on a background thread the first time an orbital of
        # it is asked for; every orbital after that is a matrix-vector
        # product answered inline. Guarded by its own lock, held only for
        # the dict access.
        self._lobe_lock = threading.Lock()
        self._lobe: dict = {"key": None, "mtime": 0.0, "state": "idle",
                            "error": "", "set": None}
        # Live CREST status (a probe of every target: WSL distros on Windows,
        # this machine on Linux). Like MLIP the probe is slow (it runs
        # `crest --version`), so it runs in a background thread and the UI polls
        # get_crest_status(). Guarded by its own lock.
        self._crest_lock = threading.Lock()
        self._crest_status: dict = {"state": "checking", "distros": [], "wsl": True,
                                    "transport": crest_transport()}
        self._crest_installing = False
        # CREST probe generation (same stale-overwrite guard as MLIP's).
        self._crest_probe_seq = 0
        self._start_crest_probe()
        # The 2D-drawing add-on (orcamgr/draw2d/): one registered RDKit
        # interpreter, probed in the background like an MLIP env; base-Python
        # detection for its Create runs lazily, on the first status poll (it
        # launches every interpreter on the machine, and most sessions never
        # open the card); the install and the bond-perception job are each a
        # background thread the UI polls. One lock for all of it.
        self._d2_lock = threading.Lock()
        self._d2_env: dict = d2env.status_unset()
        self._d2_probe_seq = 0
        self._d2_bases: dict = {"state": "", "base_pythons": []}
        self._d2_install: dict = {"state": "idle", "step": 0, "steps": 0,
                                  "label": "", "error": "", "cancelled": False}
        self._d2_installer = None
        self._d2_job: dict = {"job": 0, "state": "idle"}
        self._d2_job_seq = 0
        if self.settings.draw2d_python:
            self._start_d2_probe()

    # --- about / metadata ---
    @pyqtSlot(result=str)
    def get_about(self) -> str:
        return json.dumps(AboutPayload(
            version=APP_VERSION,
            author=APP_AUTHOR,
            email=APP_EMAIL,
        ))

    # --- front-end state kept across launches (uistate.py) ---
    @pyqtSlot(str, result=str)
    def get_ui_state(self, key: str) -> str:
        try:
            return json.dumps({"ok": True, "value": uistate.get(key)})
        except ValueError as e:
            return json.dumps({"ok": False, "error": str(e)})

    @pyqtSlot(str, str, result=str)
    def set_ui_state(self, key: str, value_json: str) -> str:
        """value_json "null" removes the key."""
        try:
            uistate.put(key, json.loads(value_json or "null"))
        except (ValueError, OSError) as e:
            return json.dumps({"ok": False, "error": str(e)})
        return json.dumps({"ok": True})

    @pyqtSlot(str, result=str)
    def take_ui_state(self, key: str) -> str:
        """{ok, value}: the value under ``key``, removed in the same step
        under the uistate lock -- a mailbox two windows poll (the queue
        editor's hand-off) is taken by exactly one of them."""
        try:
            return json.dumps({"ok": True, "value": uistate.take(key)})
        except (ValueError, OSError) as e:
            return json.dumps({"ok": False, "error": str(e)})

    # --- settings ---
    @pyqtSlot(result=str)
    def get_settings(self, save_error: str = "") -> str:
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
            eta_mode=self.settings.eta_mode,
            geo_graph_mode=self.settings.geo_graph_mode,
            build_mode=self.settings.build_mode,
            viewer_target=self.settings.viewer_target,
            crest_distro=self.settings.crest_distro,
            orca_valid=self.settings.orca_is_valid(),
            save_error=save_error,
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
            # An empty workspace root is not "unset, fall back to the default":
            # Settings.load() only fills the default at the NEXT launch, so for
            # the rest of this session every run builds Path("")/name and writes
            # its calculation folders into the process's working directory —
            # wherever the app happened to be started from (in a frozen build,
            # beside the .exe). Refuse it while the old value is still live.
            if not self.settings.workspace_root.strip():
                self.settings.workspace_root = Settings.load().workspace_root
                raise ValueError("Workspace folder cannot be empty.")
            # Range-clamped like the three budgets below, and for the same
            # reason: these seed every new calculation's %pal/%maxcore and the
            # MLIP threads field. A bare int() saved -5, 0, or a 100-digit
            # number, which then rode into the build form as the default the
            # run silently clamps somewhere else.
            for k, lo, hi in (("default_nprocs", 1, 1024),
                              ("default_maxcore_mb", 64, 1_000_000)):
                if k in data:
                    setattr(self.settings, k, max(lo, min(hi, int(data[k]))))
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
            # ...and they apply to a run already going, not only the next one
            if any(k in data for k in ("max_concurrent_jobs", "max_total_cores",
                                       "max_total_ram_mb")):
                self.store.update_run_budget(self._budget())
            # opt-ETA mode: only accept known values
            if "eta_mode" in data and data["eta_mode"] in ("conservative", "eager"):
                self.settings.eta_mode = data["eta_mode"]
            # optimization graph mode: only accept known values
            if "geo_graph_mode" in data and data["geo_graph_mode"] in ("all5", "maxgrad"):
                self.settings.geo_graph_mode = data["geo_graph_mode"]
            # build-tab mode: only accept known values
            if "build_mode" in data and data["build_mode"] in ("beginner", "expert", "mlip", "crest"):
                self.settings.build_mode = data["build_mode"]
            # where a Visual row opens: ORCAdesk's viewer or the OS association
            if "viewer_target" in data and data["viewer_target"] in ("in_app", "system"):
                self.settings.viewer_target = data["viewer_target"]
            saved = self.settings.save()
            return self.get_settings(
                "" if saved else
                f"Settings could not be written to {config_file()}. They apply "
                "to this session but will be lost when ORCAdesk is closed.")
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError,
                OverflowError) as e:
            # A slot may not raise: PyQt treats an unhandled exception in a
            # Qt-invoked slot as fatal. Valid-but-non-object JSON (null, [],
            # "x") reaches d.get() as an AttributeError, and a JSON number like
            # 1e999 decodes to inf, where int() raises OverflowError — neither
            # is a decode error or a ValueError. Errors are data here (P6).
            return json.dumps(ErrorPayload(error=str(e)))

    # --- Liquid-Glass custom wallpaper ---
    # Retired with the classic front-end, which was the only page that drew a
    # wallpaper: nothing under web/next/ calls these two slots any more, and the
    # `wallpaper` setting they paired with is gone from Settings. Kept, unused,
    # until they are removed deliberately.
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
    def _dialog_start_dir(self) -> str:
        """Directory a file dialog should open at: the workspace, when it is set
        and still there. Results, inputs and structures all live under
        ``{workspace}/{name}/``, so a dialog that starts anywhere else makes the
        user navigate back to the workspace by hand every single time.

        A workspace that has gone away - an unplugged drive, a folder moved
        between sessions - is deliberately NOT handed to Qt: it would take the
        missing path as the starting point and open on an empty view, which is
        worse than no hint at all. "" lets Qt fall back to its own last-used
        directory. Never raises: a malformed path must not stop a dialog from
        opening (P32).

        Only for dialogs looking for the user's OWN calculation files. The ORCA
        executable and an MLIP interpreter live in an install tree, not the
        workspace, so their pickers keep Qt's default.
        """
        root = (self.settings.workspace_root or "").strip()
        try:
            return root if root and Path(root).is_dir() else ""
        except OSError:
            return ""

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

        threading.Thread(target=_worker, daemon=True,
                         name="orcadesk-mlip-probe").start()

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
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError,
                OverflowError) as e:   # a slot may not raise — see save_settings
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
        if not env_id:
            # A full re-check also re-detects the base interpreters and the GPU.
            # That ran once, in __init__, while the card told the user to
            # "install Python ... then Re-check" — advice nothing could act on:
            # after installing Python they had to restart ORCAdesk, and nothing
            # said so.
            self._start_mlip_install_probe()
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
        # ...and publish it, so the phone's /api/run can apply the same order:
        # it has no probe of its own, and the contract is not a desktop one.
        self.store.set_mlip_readiness(states)
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
                        "backends": [{"key": k, "label": v["label"]}
                                     for k, v in MLIP_BACKENDS.items()]}
            except Exception as e:   # detection must never kill the panel
                opts = {"state": "ready", "base_pythons": [], "gpu": False,
                        "gpu_name": "", "cuda_index": "",
                        "backends": [{"key": k, "label": v["label"]}
                                     for k, v in MLIP_BACKENDS.items()]}
                self.store.append_log(f"MLIP base-Python detection failed: {e}", "warn")
            with self._mlip_install_lock:
                self._mlip_install_opts = opts

        threading.Thread(target=_worker, daemon=True,
                         name="orcadesk-mlip-install").start()

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
        # characters that cannot escape the envs root or trip Windows. The
        # character filter is not enough on its own — Windows' reserved DEVICE
        # names survive it unchanged, and "nul" in particular is a path that
        # always "exists" and cannot be listed, so it reached the installer's
        # non-empty check and came back as a raw WinError (localized mojibake on
        # a Korean system) instead of a sentence.
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-") or "mlip"
        if slug.split(".")[0].lower() in _WINDOWS_DEVICE_NAMES:
            return json.dumps(MlipInstallPayload(
                state="error", step=0, steps=0, label="",
                error=f"'{name}' is a name Windows reserves for a device. "
                      "Choose another one.", cancelled=False))
        env_dir = user_data_root() / "mlip_envs" / slug

        with self._mlip_install_lock:
            if self._mlip_install.get("state") == "running":
                # Inline, not get_mlip_install_status(): that slot re-takes this
                # non-reentrant lock, so a second click during an install would
                # deadlock Qt's UI thread and freeze the window (same defect as
                # the cube slot's busy branch).
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
                # Every registered interpreter, so a directory somebody
                # depends on is never mistaken for a leftover of ours.
                with self._mlip_lock:
                    in_use = [e.get("python", "") for e in self.settings.mlip_envs]
                res = installer.run(base, env_dir, backend=backend, device=device,
                                    on_line=_on_line, on_step=_on_step,
                                    in_use=in_use)
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

        threading.Thread(target=_worker, daemon=True,
                         name="orcadesk-crest-probe").start()
        return self.get_mlip_install_status()

    @pyqtSlot(result=str)
    def cancel_mlip_install(self) -> str:
        """Stop a running environment creation (terminates pip)."""
        with self._mlip_install_lock:
            installer = self._mlip_installer
        if installer is not None:
            installer.cancel()
        return self.get_mlip_install_status()

    # --- 2D drawing add-on (orcamgr/draw2d/, an RDKit env of its own) ---
    def _d2_env_dir(self) -> Path:
        from ..draw2d.installer import ENV_DIRNAME
        return user_data_root() / ENV_DIRNAME

    def _start_d2_probe(self) -> None:
        """Probe the registered RDKit interpreter off the UI thread (importing
        RDKit takes a second). A probe that finishes after the interpreter was
        changed or removed -- or after a newer probe started -- is dropped."""
        with self._d2_lock:
            python = self.settings.draw2d_python
            if not python:
                self._d2_env = d2env.status_unset()
                return
            self._d2_env = d2env.status_checking(python)
            self._d2_probe_seq += 1
            seq = self._d2_probe_seq

        def _worker() -> None:
            try:
                st = d2env.status_from_probe(d2env.probe_env(python))
            except Exception as e:    # a crash must still publish (never stuck)
                st = d2env.status_error(python, f"Probe failed: {e}")
            with self._d2_lock:
                if (self._d2_probe_seq == seq
                        and self.settings.draw2d_python == python):
                    self._d2_env = st

        threading.Thread(target=_worker, daemon=True,
                         name="orcadesk-draw2d-probe").start()

    def _start_d2_bases(self) -> None:
        """Detect base interpreters for Create (each is launched for its
        version, so never on the UI thread)."""
        with self._d2_lock:
            if self._d2_bases.get("state") == "checking":
                return
            self._d2_bases = {"state": "checking", "base_pythons": []}

        def _worker() -> None:
            from ..draw2d.installer import base_pythons
            try:
                found = base_pythons()
            except Exception as e:     # detection must never kill the card
                found = []
                self.store.append_log(
                    f"2D drawing: base-Python detection failed: {e}", "warn")
            with self._d2_lock:
                self._d2_bases = {"state": "ready", "base_pythons": found}

        threading.Thread(target=_worker, daemon=True,
                         name="orcadesk-draw2d-bases").start()

    def _d2_status_json(self) -> str:
        """Draw2dStatusPayload. Takes the lock itself, so it is never called
        with the lock held (P37)."""
        with self._d2_lock:
            env = dict(self._d2_env)
            bases = dict(self._d2_bases)
            inst = dict(self._d2_install)
        return json.dumps(Draw2dStatusPayload(
            state=env["state"], python=env["python"], version=env["version"],
            rdkit=env["rdkit"], message=env["message"],
            bases_state=bases.get("state") or "checking",
            base_pythons=list(bases.get("base_pythons") or []),
            env_dir=str(self._d2_env_dir()),
            install=Draw2dInstallPayload(**inst)))

    @pyqtSlot(result=str)
    def get_draw2d_status(self) -> str:
        """The add-on's whole state (Draw2dStatusPayload). The first call also
        starts base-Python detection for Create."""
        with self._d2_lock:
            need = not self._d2_bases.get("state")
        if need:
            self._start_d2_bases()
        return self._d2_status_json()

    @pyqtSlot(result=str)
    def pick_draw2d_python(self) -> str:
        exe = "python.exe" if sys.platform.startswith("win") else "python"
        filt = f"Python interpreter ({exe});;All files (*.*)"
        path, _ = QFileDialog.getOpenFileName(
            self.window, "Locate a Python interpreter that has RDKit", "", filt)
        return path or ""

    @pyqtSlot(str, result=str)
    def set_draw2d_python(self, python: str) -> str:
        """Register the RDKit interpreter (a folder pick is resolved to the
        python inside it) and probe it."""
        python = d2env.resolve_interpreter(python or "")
        if not python:
            return json.dumps(ErrorPayload(error="No interpreter path given."))
        with self._d2_lock:
            self.settings.draw2d_python = python
        self.settings.save()
        self._start_d2_probe()
        return self._d2_status_json()

    @pyqtSlot(result=str)
    def remove_draw2d_env(self) -> str:
        """Stop using the registered interpreter. Nothing is deleted from disk:
        a Create-built environment stays where it is and Create adopts it."""
        with self._d2_lock:
            self.settings.draw2d_python = ""
            self._d2_probe_seq += 1
            self._d2_env = d2env.status_unset()
        self.settings.save()
        return self._d2_status_json()

    @pyqtSlot(result=str)
    def check_draw2d(self) -> str:
        """Re-probe the interpreter and re-detect base Pythons."""
        self._start_d2_probe()
        self._start_d2_bases()
        return self._d2_status_json()

    @pyqtSlot(str, result=str)
    def create_draw2d_env(self, payload_json: str) -> str:
        """Build a venv with RDKit under user_data_root and register it on
        success. {base_python?}: "" takes the best detected interpreter.
        Returns at once; the UI polls get_draw2d_status(). Refuses while one is
        already running -- two pips into one tree is corruption."""
        from ..draw2d.installer import Draw2dEnvInstaller, venv_python
        try:
            data = json.loads(payload_json or "{}")
            base = str(data.get("base_python") or "").strip()
        except (json.JSONDecodeError, AttributeError) as e:
            return json.dumps(ErrorPayload(error=str(e)))
        env_dir = self._d2_env_dir()
        with self._d2_lock:
            running = self._d2_install.get("state") == "running"
            if not running:
                if not base:
                    found = [b for b in self._d2_bases.get("base_pythons") or []
                             if b.get("supported")]
                    base = found[0]["python"] if found else ""
                self._d2_install = {"state": "running", "step": 0, "steps": 3,
                                    "label": "Starting…", "error": "",
                                    "cancelled": False}
                installer = self._d2_installer = Draw2dEnvInstaller()
                in_use = [self.settings.draw2d_python]
        if running:
            return self._d2_status_json()

        def _on_line(line: str) -> None:
            self.store.append_log(f"[draw2d-install] {line}", "info")

        def _on_step(i: int, total: int, label: str) -> None:
            with self._d2_lock:
                self._d2_install.update({"step": i, "steps": total, "label": label})

        def _worker() -> None:
            try:
                res = installer.run(base, env_dir, on_line=_on_line,
                                    on_step=_on_step, in_use=in_use)
            except Exception as e:
                res = {"ok": False, "python": "", "error": str(e), "cancelled": False}
            if res.get("ok"):
                python = res.get("python") or str(venv_python(env_dir))
                with self._d2_lock:
                    self.settings.draw2d_python = python
                self.settings.save()
                self._start_d2_probe()
                self.store.append_log(
                    f"2D drawing: RDKit environment ready at {env_dir}.", "ok")
            elif res.get("cancelled"):
                self.store.append_log("2D drawing: environment creation cancelled.",
                                      "warn")
            else:
                self.store.append_log(
                    "2D drawing: environment creation failed: "
                    f"{res.get('error') or 'unknown error'}", "err")
            with self._d2_lock:
                self._d2_installer = None
                self._d2_install = {
                    "state": "done" if res.get("ok") else "error",
                    "step": self._d2_install.get("step", 0), "steps": 3,
                    "label": self._d2_install.get("label", ""),
                    "error": "" if res.get("ok") else (res.get("error") or ""),
                    "cancelled": bool(res.get("cancelled"))}

        threading.Thread(target=_worker, daemon=True,
                         name="orcadesk-draw2d-install").start()
        return self._d2_status_json()

    @pyqtSlot(result=str)
    def cancel_draw2d_install(self) -> str:
        """Stop a running Create (terminates pip)."""
        with self._d2_lock:
            installer = self._d2_installer
        if installer is not None:
            installer.cancel()
        return self._d2_status_json()

    def _d2_charge_for(self, source: str) -> tuple:
        """(charge, multiplicity, where from) for the structure on show: the
        queued calculation's own, else its .out's input summary, else 0/1."""
        from ..core.parser import read_charge, read_multiplicity
        src = (source or "").strip()
        if src.startswith("file:"):
            p = Path(src[5:])
            out = p if p.suffix.lower() == ".out" else p.with_suffix(".out")
            try:
                if out.is_file():
                    c = read_charge(out)
                    if c is not None:
                        return c, read_multiplicity(out) or 1, "output"
            except (OSError, ValueError):
                pass
            return 0, 1, "default"
        name = src[5:] if src.startswith("calc:") else src
        calc = self.store.get(name) if name else None
        if calc is not None:
            return (int(getattr(calc, "charge", 0) or 0),
                    int(getattr(calc, "multiplicity", 1) or 1), "calculation")
        return 0, 1, "default"

    @pyqtSlot(str, result=str)
    def draw2d_perceive(self, payload_json: str) -> str:
        """Start RDKit bond perception for one structure: {source, atoms:
        [{el, x, y, z}], charge?: int|null} (null = the calculation's own,
        _d2_charge_for). Returns {job, state: "running"} at once, or an error;
        the UI polls get_draw2d_job(). Called once per structure -- the drawing
        is redrawn in the front-end on every rotation, never here."""
        from ..draw2d.worker import clean_atoms, run_worker
        with self._d2_lock:
            self._d2_job_seq += 1
            job = self._d2_job_seq
            python = self.settings.draw2d_python
            ready = self._d2_env.get("state") == "ready"

        def fail(msg: str) -> str:
            res = Draw2dPerceptionPayload(job=job, state="error", error=msg)
            with self._d2_lock:
                if self._d2_job_seq == job:
                    self._d2_job = dict(res)
            return json.dumps(res)

        if not python or not ready:
            return fail("2D drawing needs a Python environment with RDKit — "
                        "set one up in Settings › 2D drawing.")
        try:
            d = json.loads(payload_json or "{}")
            atoms = clean_atoms(d.get("atoms"))
            source = str(d.get("source") or "")
            req_charge = d.get("charge")
        except (json.JSONDecodeError, ValueError, AttributeError, TypeError) as e:
            return fail(str(e) or "Unreadable request.")
        charge, mult, origin = self._d2_charge_for(source)
        if req_charge is not None:
            try:
                charge, origin = int(req_charge), "request"
            except (TypeError, ValueError, OverflowError):
                return fail("The charge must be a whole number.")
        running = Draw2dPerceptionPayload(job=job, state="running", charge=charge,
                                          multiplicity=mult, charge_from=origin)
        with self._d2_lock:
            self._d2_job = dict(running)

        def _worker() -> None:
            try:
                r = run_worker(python, atoms, charge, mult, cwd=str(user_data_root()))
            except Exception as e:
                r = {"ok": False, "error": str(e)}
            if r.get("ok"):
                res = Draw2dPerceptionPayload(
                    job=job, state="done", method=r["method"], note=r["note"],
                    rdkit=r["rdkit"], bonds=r["bonds"], charges=r["charges"],
                    radicals=r["radicals"], stereocentres=r["stereocentres"],
                    charge=charge, multiplicity=mult, charge_from=origin)
            else:
                res = Draw2dPerceptionPayload(
                    job=job, state="error", error=r.get("error") or "Failed.",
                    charge=charge, multiplicity=mult, charge_from=origin)
            with self._d2_lock:
                if self._d2_job_seq == job:
                    self._d2_job = dict(res)

        threading.Thread(target=_worker, daemon=True,
                         name="orcadesk-draw2d-job").start()
        return json.dumps(running)

    @pyqtSlot(result=str)
    def get_draw2d_job(self) -> str:
        """The latest perception job (Draw2dPerceptionPayload)."""
        with self._d2_lock:
            job = dict(self._d2_job)
        return json.dumps(job)

    @pyqtSlot(str, result=str)
    def draw2d_save(self, payload_json: str) -> str:
        """Save the drawing on screen through a Save dialog:
        {format: "svg"|"png"|"mol", source, name?, svg?, png?, drawing?}.
        svg is the text the front-end drew, png a data:image/png;base64 URI,
        drawing the MOL writer's input (draw2d/molfile.py: the 2D coordinates
        and wedge flags exactly as drawn -- one projection, not two). The
        suffix is forced to the format's own and nothing is opened afterwards,
        so openwith's default-deny rule is untouched. Draw2dSaveResult; closing
        the dialog is {ok: false, cancelled: true}, not an error."""
        import base64
        import binascii
        from ..draw2d.molfile import MolfileError, write_molblock
        try:
            d = json.loads(payload_json or "{}")
            fmt = str(d.get("format") or "")
        except (json.JSONDecodeError, AttributeError):
            return json.dumps(Draw2dSaveResult(ok=False, error="Unreadable request."))
        specs = {"svg": ("SVG image (*.svg)", ".svg"),
                 "png": ("PNG image (*.png)", ".png"),
                 "mol": ("MDL MOL file (*.mol)", ".mol")}
        if fmt not in specs:
            return json.dumps(Draw2dSaveResult(ok=False, error=f"Unknown format '{fmt}'."))
        try:
            if fmt == "svg":
                text = str(d.get("svg") or "")
                if not text.lstrip().startswith("<svg"):
                    raise ValueError("There is no drawing to save.")
                blob = text.encode("utf-8")
            elif fmt == "png":
                uri = str(d.get("png") or "")
                head = "data:image/png;base64,"
                if not uri.startswith(head):
                    raise ValueError("There is no image to save.")
                try:
                    blob = base64.b64decode(uri[len(head):], validate=True)
                except binascii.Error:
                    raise ValueError("The image is unreadable.") from None
                if not blob.startswith(b"\x89PNG"):
                    raise ValueError("The image is not a PNG.")
            else:
                blob = write_molblock(d.get("drawing") or {}).encode("ascii")
        except (MolfileError, ValueError, TypeError) as e:
            return json.dumps(Draw2dSaveResult(ok=False, error=str(e)))
        # start beside the result when it has a folder, else in the workspace
        start = self._dialog_start_dir()
        try:
            run_dir, _base, _open, err = self._plot_source(str(d.get("source") or ""))
            if not err and run_dir is not None and Path(run_dir).is_dir():
                start = str(run_dir)
        except (OSError, ValueError):
            pass
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_",
                      str(d.get("name") or "structure")).strip("._") or "structure"
        filt, suffix = specs[fmt]
        name = f"{stem}_2d{suffix}"
        path, _ = QFileDialog.getSaveFileName(
            self.window, "Save the 2D drawing",
            str(Path(start) / name) if start else name, filt)
        if not path:
            return json.dumps(Draw2dSaveResult(ok=False, cancelled=True))
        out = Path(path)
        if out.suffix.lower() != suffix:
            out = out.with_name(out.name + suffix)
        try:
            out.write_bytes(blob)
        except OSError as e:
            return json.dumps(Draw2dSaveResult(
                ok=False, error=f"Could not write {out.name}: {e.strerror or e}"))
        self.store.append_log(f"2D drawing saved: {out}", "ok")
        return json.dumps(Draw2dSaveResult(ok=True, path=str(out)))

    @pyqtSlot(str, str, result=str)
    def copy_draw2d_image(self, png_data_uri: str, svg: str) -> str:
        """Put the drawing on the clipboard as an image (PNG) and as SVG, so it
        pastes into a document or a slide as a picture and into an SVG-aware
        program as vectors. Through Qt rather than navigator.clipboard, whose
        image writes QtWebEngine does not grant a page."""
        import base64
        import binascii
        from PyQt6.QtCore import QMimeData
        from PyQt6.QtGui import QGuiApplication, QImage
        head = "data:image/png;base64,"
        if not (png_data_uri or "").startswith(head):
            return json.dumps(OkResult(ok=False, error="There is no image to copy."))
        try:
            blob = base64.b64decode(png_data_uri[len(head):], validate=True)
        except binascii.Error:
            return json.dumps(OkResult(ok=False, error="The image is unreadable."))
        img = QImage.fromData(blob, "PNG")
        if img.isNull():
            return json.dumps(OkResult(ok=False, error="The image is unreadable."))
        mime = QMimeData()
        mime.setImageData(img)
        if (svg or "").lstrip().startswith("<svg"):
            mime.setData("image/svg+xml", svg.encode("utf-8"))
        QGuiApplication.clipboard().setMimeData(mime)
        return json.dumps(OkResult(ok=True))

    # --- CREST environment (WSL-backed, kept separate from ORCA) ---
    def _start_crest_probe(self) -> None:
        """Probe every CREST target in a background thread (running
        `crest --version`, possibly through wsl.exe, must not block the Qt UI
        thread). Publishes the aggregate on self._crest_status for
        get_crest_status()."""
        with self._crest_lock:
            prev = self._crest_status.get("distros", [])
            self._crest_status = {"state": "checking", "distros": prev,
                                  "wsl": self._crest_status.get("wsl", True),
                                  "transport": crest_transport()}
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

        threading.Thread(target=_worker, daemon=True,
                         name="orcadesk-crest-install").start()

    # ---------------------------------------------------- remote devices
    def _remote_device_ready(self, alias: str, info: DeviceInfo) -> None:
        """A probe found ``alias`` ready (probe thread): settle the remote
        jobs a cancel could not reach there (design §6.3)."""
        if self.store.remote_cleanup_entries(alias):
            self.store.settle_remote_cleanup(alias, self._remote_transport,
                                             info.workdir,
                                             self.settings.workspace_root)

    def _probe_device_now(self, entry: dict):
        """Blocking re-probe for the engine's device watch (its own thread)."""
        self._remote.refresh([entry], wait=True)
        return self._remote.get(entry.get("alias", ""))

    def _device_args(self) -> dict:
        """The remote-device half of make_engine_factory's arguments."""
        if not self._remote_transport.available():
            return {}
        return dict(device_entries=list(self.settings.remote_devices),
                    transport=self._remote_transport,
                    device_info=self._remote.get,
                    device_probe=self._probe_device_now)

    def _devices_payload(self, save_error: str = "") -> str:
        aliases = parse_ssh_config()
        conf = {d["alias"]: d for d in self.settings.remote_devices}
        snap = self._remote.snapshot()
        # a device gone from the SSH config can never be reached again: its
        # clean-up entries are dropped (design §6.3 step 5)
        for e in self.store.remote_cleanup_entries():
            if e["device"] not in aliases:
                self.store.forget_remote_device(e["device"])
        pending: dict = {}
        for e in self.store.remote_cleanup_entries():
            pending[e["device"]] = pending.get(e["device"], 0) + 1
        rows = []
        for alias in aliases + [a for a in conf if a not in aliases]:
            entry = conf.get(alias) or normalize_device({"alias": alias})
            info = snap.get(alias) or DeviceInfo(alias=alias, state="unknown")
            p = info.to_payload()
            if alias not in aliases:
                p["state"] = "error"
                p["message"] = "No longer a Host in your SSH config."
            rows.append(DevicePayload(
                alias=alias, state=p["state"], message=p["message"], os=p["os"],
                cores=p["cores"], mem_total_mb=p["mem_total_mb"],
                mem_avail_mb=p["mem_avail_mb"], orca_path=p["orca_path"],
                orca_version=p["orca_version"], has_mpi=p["has_mpi"],
                has_setsid=p["has_setsid"], workdir=p["workdir"],
                free_disk_mb=p["free_disk_mb"], in_config=alias in aliases,
                cleanup_pending=pending.get(alias, 0),
                enabled=entry["enabled"], max_jobs=entry["max_jobs"],
                cores_cap=entry["cores"], ram_cap_mb=entry["ram_mb"],
                orca_path_setting=entry["orca_path"],
                workdir_setting=entry["workdir"],
                prefer=list(entry.get("prefer") or []), spot=bool(entry.get("spot")),
                requeue_after_min=int(entry.get("requeue_after_min", 5))))
        return json.dumps(DevicesPayload(
            ssh=self._remote_transport.available(),
            config_path=str(default_ssh_config()), devices=rows,
            save_error=save_error))

    @pyqtSlot(result=str)
    def get_devices(self) -> str:
        try:
            return self._devices_payload()
        except Exception as e:      # a slot may not raise (P6)
            return json.dumps(ErrorPayload(error=str(e)))

    @pyqtSlot(result=str)
    def detect_devices(self) -> str:
        """Re-read ~/.ssh/config and probe every host in it (background)."""
        try:
            if self._remote_transport.available():
                conf = {d["alias"]: d for d in self.settings.remote_devices}
                self._remote.refresh(
                    [conf.get(a) or {"alias": a, "workdir": DEFAULT_WORKDIR,
                                     "orca_path": ""} for a in parse_ssh_config()])
            return self._devices_payload()
        except Exception as e:
            return json.dumps(ErrorPayload(error=str(e)))

    @pyqtSlot(str, result=str)
    def set_remote_device(self, payload_json: str) -> str:
        """Change one device's settings entry ({alias, enabled?, max_jobs?,
        cores?, ram_mb?, orca_path?, workdir?, prefer?, spot?,
        requeue_after_min?}); keys left out keep their value. Applies at once
        (no Save), like the CREST card -- and to the next run: a run in
        progress keeps the pools it started with."""
        try:
            data = json.loads(payload_json or "{}")
            if not isinstance(data, dict):
                raise ValueError("expected an object")
            alias = data.get("alias")
            current = next((d for d in self.settings.remote_devices
                            if d["alias"] == alias), None)
            base = dict(current) if current else {"alias": alias}
            base.update({k: v for k, v in data.items()
                         if k in ("enabled", "max_jobs", "cores", "ram_mb",
                                  "orca_path", "workdir", "prefer", "spot",
                                  "requeue_after_min")})
            entry = normalize_device(base)
            if entry is None:
                raise ValueError("Not a usable SSH host name.")
            others = [d for d in self.settings.remote_devices if d["alias"] != alias]
            self.settings.remote_devices = normalize_devices(others + [entry])
            saved = self.settings.save()
            # where ORCA or the runs live changed: what the probe said is stale
            if (current is None or entry["orca_path"] != current["orca_path"]
                    or entry["workdir"] != current["workdir"]
                    or (entry["enabled"] and not current["enabled"])):
                if self._remote_transport.available():
                    self._remote.refresh([entry])
            return self._devices_payload(
                "" if saved else
                f"Settings could not be written to {config_file()}.")
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError) as e:
            return json.dumps(ErrorPayload(error=str(e)))

    @pyqtSlot(str, result=str)
    def remove_remote_device(self, alias: str) -> str:
        """Forget a device's settings entry and its clean-up list (design §6.3
        step 5). Nothing on the device is touched."""
        try:
            self.settings.remote_devices = [d for d in self.settings.remote_devices
                                            if d["alias"] != alias]
            saved = self.settings.save()
            self.store.forget_remote_device(alias)
            self._remote.forget(alias)
            return self._devices_payload(
                "" if saved else
                f"Settings could not be written to {config_file()}.")
        except Exception as e:
            return json.dumps(ErrorPayload(error=str(e)))

    @pyqtSlot(result=str)
    def get_crest_status(self) -> str:
        """The latest aggregate CrestStatusPayload (state + per-distro probes).
        The probe runs in the background; the UI polls this."""
        with self._crest_lock:
            status = dict(self._crest_status)
        status.setdefault("state", "checking")
        status.setdefault("distros", [])
        status.setdefault("wsl", True)
        status.setdefault("transport", crest_transport())
        # A plain re-probe publishes no install_error, so a Re-check clears a
        # stale one on its own -- the error describes the last install ATTEMPT.
        status.setdefault("install_error", "")
        return json.dumps(CrestStatusPayload(**status))

    @pyqtSlot(result=str)
    def check_crest(self) -> str:
        """Re-probe every CREST target. Returns the current status immediately;
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
        # and freeze the window — same defect as the cube and MLIP-install busy
        # branches. Snapshot here, serialize after the lock is released.
        with self._crest_lock:
            busy = dict(self._crest_status) if self._crest_installing else None
            if busy is None:
                self._crest_installing = True
                self._crest_status = {"state": "checking",
                                      "distros": self._crest_status.get("distros", []),
                                      "wsl": self._crest_status.get("wsl", True),
                                      "transport": crest_transport()}
        if busy is not None:
            busy.setdefault("state", "checking")
            busy.setdefault("distros", [])
            busy.setdefault("wsl", True)
            busy.setdefault("transport", crest_transport())
            busy.setdefault("install_error", "")
            return json.dumps(CrestStatusPayload(**busy))

        def _worker() -> None:
            from ..crest.installer import install_crest as do_install
            target = (distro or "").strip()
            if not target:
                targets = crest_list_targets()
                target = targets[0] if targets else ""
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
                # Why there is nowhere to install names the thing that is
                # actually missing on THIS platform -- "install WSL" is useless
                # advice on a machine that has none to install.
                from ..crest.env import no_target_message
                install_error = no_target_message()
                self.store.append_log(
                    f"CREST install failed: {install_error}", "err")
            status = crest_aggregate_status(crest_probe_all())
            with self._crest_lock:
                self._crest_installing = False
                self._crest_status = {**status, "install_error": install_error}

        threading.Thread(target=_worker, daemon=True,
                         name="orcadesk-cube").start()
        return self.get_crest_status()

    @pyqtSlot(str, result=str)
    def set_crest_distro(self, distro: str) -> str:
        """Persist the preferred CREST target (a WSL distro, or "local") and
        re-probe."""
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
            # utf-8-SIG, not utf-8: Notepad, VS Code and PowerShell all write a
            # UTF-8 BOM, and it survived into rawText where it is invisible in
            # the textarea. The engine then wrote it back into the .inp and ORCA
            # refused the file outright ("non-ASCII character(s) found in words
            # on line 1") — every run, and a failed calculation is locked (P24),
            # so the only way out was to rebuild it. utf-8-sig reads plain UTF-8
            # unchanged, so this costs nothing for a file without one.
            # errors="replace": a stray non-UTF-8 byte must degrade to a
            # visible replacement char, not escape the OSError handling as a
            # UnicodeDecodeError crossing the JS boundary.
            text = p.read_text(encoding="utf-8-sig", errors="replace")
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
            self.window, "Load .xyz file", self._dialog_start_dir(),
            "XYZ file (*.xyz);;All files (*.*)"
        )
        return self._load_result(path)

    @pyqtSlot(result=str)
    def pick_hess_file(self) -> str:
        """Pick the .hess an IRC starts from (InitHess read); returns a
        PickedFilePayload JSON. Only the PATH comes back — the file is copied
        into the run folder at launch (QueueEngine._stage_irc_hessian), so a
        Hessian picked from a TS frequency run's folder needs no typing and
        no manual copy. Opens at the workspace, where those runs live."""
        path, _ = QFileDialog.getOpenFileName(
            self.window, "Load ORCA Hessian (.hess)", self._dialog_start_dir(),
            "ORCA Hessian (*.hess);;All files (*.*)"
        )
        if not path:
            return json.dumps(PickedFilePayload(ok=False, cancelled=True))
        return json.dumps(PickedFilePayload(ok=True, path=path))

    @pyqtSlot(result=str)
    def load_inp_file(self) -> str:
        """Pick a complete ORCA .inp; returns a LoadResult JSON (its "name"
        lets expert/raw mode auto-fill the calculation name). Opens at the
        workspace like the .xyz loader (_dialog_start_dir)."""
        path, _ = QFileDialog.getOpenFileName(
            self.window, "Load ORCA .inp file", self._dialog_start_dir(),
            "ORCA input (*.inp);;All files (*.*)"
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

    # --- structure screening (Build tab) ---
    # Thin adapters over core/structure.py: the geometry lives there, pure and
    # unit-tested, so the desktop and any later caller reach one implementation
    # (P4). Every slot answers from the coordinate text the front-end holds —
    # nothing here touches the store or the disk. ORCAdesk reads a structure
    # and reports on it; the one thing that writes coordinates is
    # apply_constraint_target, which moves a constraint's atoms to its target
    # and hands the TEXT back for the user to keep or undo.

    @pyqtSlot(str, int, int, result=str)
    def check_structure(self, xyz: str, charge: int, multiplicity: int) -> str:
        """Screen a coordinate block for the mistakes that cost a whole run
        (P26): a spin multiplicity the electron count cannot produce, atoms on
        top of each other, coordinates that are really in Bohr. Returns a
        StructureCheckPayload; it never fails, because an unreadable block is
        itself one of the findings."""
        return json.dumps(StructureCheckPayload(
            **check_geometry(xyz, charge, multiplicity)))

    @pyqtSlot(str, str, result=str)
    def compare_structures(self, reactant_xyz: str, product_xyz: str) -> str:
        """Compare two coordinate blocks as NEB endpoints — the verdict the
        Build tab badges, plus every diverging index for its mismatch table.
        Returns an AtomOrderPayload."""
        return json.dumps(AtomOrderPayload(
            **compare_atom_order(reactant_xyz, product_xyz)))

    @pyqtSlot(str, str, str, result=str)
    def apply_constraint_target(self, xyz: str, payload: str, bonds_json: str = "") -> str:
        """Move a constraint's atoms to its target value — one rigid motion of
        the smaller side of the hinge bond, so a group turns as a group. The
        payload is one GeomConstraint ({kind, atoms (0-based), value}); the
        answer is the coordinate text back with only the moved lines rewritten
        (ConstraintApplyPayload). Nothing is saved: the builder puts the text
        in its own box, where the user can undo it. `bonds_json` is the bonds
        the stage draws, [[i, j], ...], which decide the hinge ("" = by
        distance; structure._bond_list_of)."""
        try:
            c = json.loads(payload or "{}")
            if not isinstance(c, dict):
                raise ValueError
        except ValueError:
            return json.dumps(ConstraintApplyPayload(
                ok=False, error="Unreadable constraint.", xyz="", moved=[],
                before=None, after=None, warnings=[]))
        return json.dumps(ConstraintApplyPayload(**apply_constraint_target(
            xyz, c.get("kind", ""), c.get("atoms") or [], c.get("value"),
            _bond_pairs(bonds_json))))

    @pyqtSlot(str, str, str, result=str)
    def apply_constraint_targets(self, xyz: str, payload: str, bonds_json: str = "") -> str:
        """Every constraint's target at once — the builder's *Apply all*: the
        same rigid turns as one Apply, taken in rounds until all of them hold,
        so targets that share atoms (three angles about one centre) stop
        undoing each other. The payload is the list of GeomConstraints; the
        answer a ConstraintApplyAllPayload. Nothing is saved. `bonds_json` as
        for apply_constraint_target."""
        try:
            cs = json.loads(payload or "[]")
            if not isinstance(cs, list):
                raise ValueError
        except ValueError:
            return json.dumps(ConstraintApplyAllPayload(
                ok=False, error="Unreadable constraints.", xyz="", moved=[],
                before=[], after=[], warnings=[]))
        return json.dumps(ConstraintApplyAllPayload(**apply_constraint_targets(
            xyz, cs, _bond_pairs(bonds_json))))

    def _hess(self, path: str):
        """The parsed .hess at `path`, cached for the last one read and
        re-read when the file changes: the builder asks for a displaced
        structure on every move of the amplitude slider, and a 300-atom
        Hessian is a 900x900 table to parse."""
        p = Path(path)
        key = (str(p), p.stat().st_mtime)
        if getattr(self, "_hess_cache", None) and self._hess_cache[0] == key:
            return self._hess_cache[1]
        data = read_hess(p)
        self._hess_cache = (key, data)
        return data

    @pyqtSlot(str, result=str)
    def read_hess_modes(self, path: str) -> str:
        """The vibrations an ORCA .hess carries, for the builder's "push along
        a mode": each one's wavenumber, and the geometry they were computed at
        (an empty coordinate box takes that one). HessModesPayload; a file that
        cannot be read is an error sentence, never an exception."""
        try:
            h = self._hess(path)
        except (OSError, HessError) as e:
            return json.dumps(HessModesPayload(ok=False, error=str(e)))
        vib = h.vibrations()
        return json.dumps(HessModesPayload(
            ok=True, path=str(path), name=Path(path).name, n_atoms=len(h.symbols),
            symbols=h.symbols,
            xyz="\n".join(f"{s:<2s} {x:14.8f} {y:14.8f} {z:14.8f}"
                          for s, (x, y, z) in zip(h.symbols, h.coords)),
            modes=[HessModePayload(index=k, n=i + 1, freq=h.freqs[k])
                   for i, k in enumerate(vib)]))

    @pyqtSlot(str, str, int, float, result=str)
    def displace_mode(self, xyz: str, path: str, index: int, amplitude: float) -> str:
        """The coordinate box pushed along one mode of the .hess at `path`: the
        most-moved atom goes `amplitude` Angstrom, its sign the direction
        (core.structure.displace_along_mode). Nothing is saved - the builder
        shows it on its stage, and puts it in the box on Apply, where Ctrl+Z
        undoes it. ModeShiftPayload."""
        try:
            h = self._hess(path)
        except (OSError, HessError) as e:
            return json.dumps(ModeShiftPayload(ok=False, error=str(e), xyz="", moved=[],
                                               max_shift=0.0, atom=""))
        if not 0 <= index < len(h.modes):
            return json.dumps(ModeShiftPayload(ok=False, error="No such mode in that file.",
                                               xyz="", moved=[], max_shift=0.0, atom=""))
        return json.dumps(ModeShiftPayload(**displace_along_mode(
            xyz, h.symbols, h.coords, h.modes[index], amplitude)))

    def _modes_file(self, path: Path, kind: str):
        """A .hess (kind "hess") or an .out's NORMAL MODES table ("out"),
        parsed once per file version: the report asks again for every band
        picked, and the .out a .hess is missing from can be megabytes. Two
        entries, so a result's .hess and its .out do not evict each other."""
        key = (str(path), path.stat().st_mtime, kind)
        cache = getattr(self, "_modes_cache", None)
        if cache is None:
            cache = self._modes_cache = {}
        if key not in cache:
            data = read_hess(path) if kind == "hess" else read_out_modes(path)
            if len(cache) >= 2:
                cache.pop(next(iter(cache)))
            cache[key] = data
        return cache[key]

    @pyqtSlot(str, result=str)
    def get_vibration_mode(self, payload_json: str) -> str:
        """One vibration of a finished result, for the report to animate.

        ``payload_json`` is ``{source, freqs, geometry, n}``: the result's
        source string (``calc:<name>`` / ``file:<path>``, as every report slot
        takes it), the wavenumbers the report LISTS, its geometry
        (``[{el, x, y, z}]``) and the 1-based number of the mode wanted. The
        vector comes from ``{base}.hess`` beside the output, else from the
        output's own NORMAL MODES table, and either is accepted only when its
        wavenumbers are the report's (core.normalmodes) - a .hess left by an
        OptTS's first Hessian would otherwise animate another geometry's motion
        under this label. VibModePayload; a refusal is a sentence."""
        def fail(msg: str) -> str:
            return json.dumps(VibModePayload(ok=False, error=msg))
        try:
            d = json.loads(payload_json or "{}")
            source = str(d.get("source") or "")
            freqs = [float(f) for f in d.get("freqs") or []]
            geom = d.get("geometry") or []
            symbols = [str(a["el"]) for a in geom]
            coords = [(float(a["x"]), float(a["y"]), float(a["z"])) for a in geom]
            n = int(d.get("n") or 0)
        except (TypeError, ValueError, KeyError, AttributeError):
            return fail("Unreadable mode request.")
        if not freqs or not 1 <= n <= len(freqs):
            return fail("No such mode in this result.")
        name = source[5:] if source.startswith("calc:") else source
        if not name:
            return fail("No result selected.")
        try:
            out = self._result_file(name, ".out")
            hess = self._result_file(name, ".hess")
            modes, cols, origin = modes_for_report(hess, out, freqs, read=self._modes_file)
        except HessError as e:
            return fail(str(e))
        except OSError as e:
            return fail(f"Could not read the modes: {e.strerror or e}.")
        try:
            placed, dev = place_on(modes, symbols, coords)
        except HessError as e:
            return fail(f"The modes in {origin} do not fit this structure: {e}")
        col = modes.modes[cols[n - 1]]
        return json.dumps(VibModePayload(
            ok=True, n=n, count=len(freqs), freq=freqs[n - 1], origin=origin,
            coords=[list(c) for c in placed],
            vec=[col[3 * i:3 * i + 3] for i in range(len(placed))],
            max_dev=dev))

    @pyqtSlot(result=str)
    def get_step_defaults(self) -> str:
        """StepConfig's own defaults, so a builder seeds a new calculation from
        the one place they are defined (P4). The notebook builder kept its own
        literals and they drifted: MaxIter 0 (clamped to 1), IRC MaxIter 40
        against 100, TD-DFT nroots 10 / maxdim 0 against 40 / 10.

        The one value filled in here is the MACE model: StepConfig keeps ""
        ("whatever the runner defaults to", so an ORCA calc carries no model),
        and a builder has to preselect the model that "" actually runs."""
        from orcamgr.mlip.runner import DEFAULT_MACE_MODEL   # noqa: PLC0415
        d = StepConfig().to_dict()
        d["mlip_model"] = d.get("mlip_model") or DEFAULT_MACE_MODEL
        return json.dumps(d)

    # --- option lists ---
    @pyqtSlot(str, result=str)
    def load_choices(self, name: str) -> str:
        # uses the shared loader so PC and phone always see identical options
        return json.dumps(load_choice_groups(name))

    # --- open an external result (the Results tab's one file button) ---
    @pyqtSlot(result=str)
    def pick_result_file(self) -> str:
        """Pick any result file and say which way it should be read.

        One button for both Results modes, because the user is choosing a
        *result*, not a route: an ``.out`` / ``.mlip.json`` is parsed into the
        Output mode (``route="parse"``), a ``.xyz`` is a structure and opens in
        the 3D viewer (``route="structure"``). The routing is by suffix and it
        is the only thing this slot decides — the caller does the reading, so
        the two routes stay the ones the tab already has.

        Closing the picker is a deliberate choice, not a parse failure — a bare
        "{}" was indistinguishable from a bad parse and made the UI log a
        spurious error on every cancel (A2).

        Opens at the workspace (_dialog_start_dir): nearly every result worth
        opening by hand is a run folder under it, and this dialog used to start
        wherever Qt happened to be last — the install directory, on a fresh
        session.
        """
        path, _ = QFileDialog.getOpenFileName(
            self.window, "Open a result file", self._dialog_start_dir(),
            "Results (*.out *.mlip.json *.xyz);;ORCA output (*.out);;"
            "MLIP result (*.mlip.json);;Structure (*.xyz);;All files (*.*)"
        )
        if not path:
            return json.dumps(PickedResultPayload(ok=False, cancelled=True))
        route = "structure" if path.lower().endswith(".xyz") else "parse"
        return json.dumps(PickedResultPayload(ok=True, path=path, route=route))

    @pyqtSlot(result=str)
    def pick_result_folder(self) -> str:
        """Pick a run folder and open the result inside it.

        The file picker's sibling, for the folder a user actually has in
        mind: a run ORCA (or ORCAdesk, in another workspace) wrote into
        ``{folder}/``. ORCAdesk finds the result -- ``{name}.mlip.json``,
        ``{name}.out``, or the ``.out`` the folder holds when it was not made
        here -- so the user does not have to know which file the parser
        reads (:mod:`orcamgr.resultfolder`). Same payload as the file picker:
        the caller reads the path through the parse route."""
        from ..resultfolder import find_result
        folder = QFileDialog.getExistingDirectory(
            self.window, "Open a result folder", self._dialog_start_dir())
        if not folder:
            return json.dumps(PickedResultPayload(ok=False, cancelled=True))
        found = find_result(folder)
        if not found.get("ok"):
            return json.dumps(PickedResultPayload(ok=False, error=str(found.get("error") or "")))
        return json.dumps(PickedResultPayload(ok=True, path=str(found["path"]), route="parse"))

    @pyqtSlot(str, result=str)
    def parse_out_path(self, path: str) -> str:
        """Parse a specific .out path (used to auto-load finished queue results)."""
        if not path or not Path(path).exists():
            return json.dumps(ErrorPayload(error="file not found"))
        return self._parse_path(path)

    @pyqtSlot(result=str)
    def list_workspace_results(self) -> str:
        """Every result sitting in the workspace, whether or not it is still in
        the queue — so the Results tab's picker can open any of them in a click
        instead of sending the user through a file dialog.

        A run folder is ORCAdesk's own unit (``{workspace}/{name}/``, or
        ``{workspace}/{system}/{name}/`` for a calc in a system, so the scan
        reads one level into each folder too), and those
        folders **survive removal from the queue** — that is exactly why this is
        worth listing: last week's calculation, or one cleared from the queue, is
        still on disk and still interpretable. Each entry names the artifact the
        parser should read: the engine-written ``{name}.mlip.json`` for an MLIP
        run, else ``{name}.out``.

        ``queued`` marks the ones the queue also knows about, because the two are
        parsed by *different* routes: a queued calc must go through
        ``parse_calc_output`` (dispatch on its KIND), never the folder heuristic
        in ``_parse_path`` — a calc reusing a removed CREST calc's name would
        otherwise be read as a conformer search. The front-end keeps that split.

        Newest first, and bounded: a workspace with thousands of folders must not
        turn a tab switch into a directory crawl.
        """
        from ..resultfolder import result_artifact
        root = Path(self.settings.workspace_root or "")
        if not root.is_dir():
            return json.dumps(WorkspaceResultsResult(
                ok=False, error="The workspace folder is not set or does not exist.",
                results=[], root=str(root)))
        queued = {c.name.casefold() for c in self.store.list()}
        found: list[tuple[float, dict]] = []

        def take(entry: Path) -> None:
            found_artifact = result_artifact(entry)
            if found_artifact is None:
                return              # a folder with no result is not a result
            artifact, kind = found_artifact
            try:
                mtime = artifact.stat().st_mtime
            except OSError:
                mtime = 0.0
            name = entry.name
            found.append((mtime, {"name": name, "path": str(artifact),
                                  "queued": name.casefold() in queued,
                                  "kind": kind}))

        try:
            for entry in root.iterdir():
                if len(found) >= _WORKSPACE_SCAN_MAX:
                    break
                if not entry.is_dir():
                    continue
                take(entry)
                # a system's folder holds its calculations' run folders
                try:
                    inner = [e for e in entry.iterdir() if e.is_dir()]
                except OSError:
                    continue
                for sub in inner:
                    if len(found) >= _WORKSPACE_SCAN_MAX:
                        break
                    take(sub)
        except OSError as e:
            return json.dumps(WorkspaceResultsResult(
                ok=False, error=str(e), results=[], root=str(root)))
        found.sort(key=lambda t: t[0], reverse=True)
        return json.dumps(WorkspaceResultsResult(
            ok=True, results=[d for _, d in found], root=str(root)))

    @pyqtSlot(str, result=str)
    def parse_calc_output(self, name: str) -> str:
        """Parse a QUEUED calc's finished output by NAME, dispatching on its
        KIND (result_from_output) — never the external-file folder heuristic in
        _parse_path, which can mis-fire for a calc reusing a removed calc's
        name (workspace folders survive removal, so e.g. a leftover
        crest_conformers.xyz would route a new ORCA .out to the CREST parser)."""
        calc, err = self._parse_target(name)
        if err:
            return err
        try:
            key, stamp = self._parse_stamp(calc)
        except OSError as e:
            return json.dumps(ErrorPayload(error=str(e)))
        with self._parse_lock:
            hit = self._parse_hit(key, stamp)
        if hit is not None:
            return hit
        return self._parse_now(calc, key, stamp)[0]

    # A finished 2D scan's .out is 150 MB and more, and parsing it took 20 s.
    # parse_calc_output ran that on the Qt main thread every time the Jobs
    # thumbnail or the report asked, freezing the window. The async slot below
    # answers from a cache stamped with the file's size and mtime, parses a
    # small output at once, and hands a large one to a worker thread, answering
    # {ok, pending} until it is done. The synchronous slot above reads and
    # fills the same cache; no page calls it any more since the classic
    # front-end was removed.
    _PARSE_SYNC_BYTES = 8 << 20
    _PARSE_CACHE_MAX = 8

    def _parse_target(self, name: str):
        """(a snapshot of the queued calc, None) or (None, an error payload)."""
        calc = next((c for c in self.store.list() if c.name == name), None)
        if calc is None:
            return None, json.dumps(ErrorPayload(error=f"'{name}' is not in the queue."))
        # a folder the user moved by hand is followed (it records in place)
        self.store.relocate_moved(self.settings.workspace_root, (calc.name,))
        if not calc.output_path or not Path(calc.output_path).exists():
            return None, json.dumps(ErrorPayload(error="no output file on disk"))
        # the worker must not hold the store's own object
        return SimpleNamespace(kind=calc.kind, output_path=calc.output_path), None

    @staticmethod
    def _parse_stamp(calc):
        st = Path(calc.output_path).stat()
        return (str(calc.output_path), calc.kind), (st.st_size, st.st_mtime_ns)

    def _parse_hit(self, key, stamp):
        """The cached payload for this file as it is now, or None. Under _parse_lock."""
        hit = self._parse_cache.get(key)
        if hit is None or hit[0] != stamp:
            return None
        self._parse_cache.move_to_end(key)
        return hit[1]

    def _parse_now(self, calc, key, stamp) -> "tuple[str, bool]":
        """Parse and cache a success; (the payload or the error, ok)."""
        try:
            out = self._payload_json(result_from_output(calc))
        except Exception as e:
            return json.dumps(ErrorPayload(error=str(e))), False
        with self._parse_lock:
            self._parse_cache[key] = (stamp, out)
            self._parse_cache.move_to_end(key)
            while len(self._parse_cache) > self._PARSE_CACHE_MAX:
                self._parse_cache.popitem(last=False)
        return out, True

    def _parse_worker(self, calc, key, stamp) -> None:
        out, ok = self._parse_now(calc, key, stamp)
        with self._parse_lock:
            self._parse_busy.discard(key)
            if not ok:
                self._parse_failed[key] = out   # said once, to the next ask

    @pyqtSlot(str, result=str)
    def parse_calc_output_async(self, name: str) -> str:
        """parse_calc_output, never blocking on a large output: the payload,
        or {"ok": true, "pending": true} while a worker parses it (ask again).
        CREST and MLIP results are small files and are parsed at once."""
        calc, err = self._parse_target(name)
        if err:
            return err
        if calc.kind.startswith(("mlip", "crest")):
            return self.parse_calc_output(name)
        try:
            key, stamp = self._parse_stamp(calc)
        except OSError as e:
            return json.dumps(ErrorPayload(error=str(e)))
        with self._parse_lock:
            hit = self._parse_hit(key, stamp)
            if hit is not None:
                return hit
            failed = self._parse_failed.pop(key, None)
            if failed is not None:
                return failed
            if key in self._parse_busy:
                return json.dumps({"ok": True, "pending": True})
            background = stamp[0] > self._PARSE_SYNC_BYTES
            if background:
                self._parse_busy.add(key)
        if not background:
            return self._parse_now(calc, key, stamp)[0]
        threading.Thread(target=self._parse_worker, args=(calc, key, stamp),
                         name="orcadesk-parse", daemon=True).start()
        return json.dumps({"ok": True, "pending": True})

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
            return self._payload_json(r, path=path)
        except Exception as e:
            return json.dumps(ErrorPayload(error=str(e)))

    def _payload_json(self, r, path: str = "") -> str:
            # Send everything that was parsed plus the gating flags; the
            # front-end decides what to show per calc kind (and "Show all"
            # overrides it). is_optimization gates "Final geometry"; show_elec
            # gates general electronic-structure sections; is_conformer_search
            # gates the CREST ensemble list.
            # `path` rides only the path-addressed routes (_parse_path): the
            # front-end keeps it so a result opened from disk can still plot its
            # orbitals, which need the .gbw sitting beside it. A calc parsed by
            # NAME is addressed by name and gets no path.
            return json.dumps(ParsePayload(
                path=path,
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
                ir_intensities=r.ir_intensities,
                n_imaginary=r.n_imaginary,
                mulliken=r.mulliken_charges,
                nmr=[
                    NmrPayload(idx=i, el=el, iso=iso, aniso=an)
                    for (i, el, iso, an) in r.nmr_shieldings
                ],
                neb_path=r.neb_path,
                neb_path_kind=r.neb_path_kind,
                scan_coords=[ScanCoordPayload(**c) for c in r.scan_coords],
                scan_points=[ScanPointPayload(**p) for p in r.scan_points],
                nbo_program=r.nbo_program,
                localization=r.localization,
                geometry=[
                    GeomAtomPayload(el=a.symbol, x=a.x, y=a.y, z=a.z)
                    for a in r.geometry
                ],
                orbitals=[
                    OrbitalPayload(
                        idx=o.index, occ=o.occ, ev=o.energy_ev, spin=o.spin,
                        frontier=("homo" if (o.index == r.homo_index
                                             and o.spin == r.homo_spin) else
                                  "lumo" if (o.index == r.lumo_index
                                             and o.spin == r.lumo_spin) else ""))
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
        """The input a calculation runs. A guided one: what build_input writes
        from its config. One with raw text (an inp-set block, a pasted .inp):
        that text through render_template_text -- the renderer the engine
        runs at launch -- for the calc's kind, its coordinates in place when
        they are its own ({{GEOMETRY}} kept for a reference, resolved at run
        time). A level block's options are its config, read as the store
        reads them (template_config) and rendered by the same call with the
        same atom count, so the preview is the launch renderer's output. The
        Hessian a {{JOB}} IRC reads is the parent's .hess when it is on disk,
        or the one its frequency parent will leave; else it computes its own
        (_preview_hess)."""
        try:
            d = json.loads(calc_json)
            if d.get("is_raw") and isinstance(d.get("raw_text"), str) and d["raw_text"]:
                kind = str(d.get("kind", "") or "")
                text = d["raw_text"].replace("\r\n", "\n")
                cfg = template_config(d.get("config"), kind) if is_level_text(text) else None
                xyz = d.get("xyz", "") if d.get("geometry_source", "direct") != "reference" else ""
                n_atoms = None
                if isinstance(xyz, str) and xyz.strip() and GEOMETRY_PLACEHOLDER in text:
                    text = render_raw_input(text, xyz)
                    n_atoms = len(parse_block(xyz)) or None
                text = render_template_text(
                    text, kind, int(d.get("charge", 0)), int(d.get("multiplicity", 1)),
                    self._preview_hess(d, text, kind, cfg), cfg, n_atoms)
                return json.dumps(TextResult(ok=True, text=text))
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

    #: kinds whose finished run leaves a {name}.hess beside its output
    _HESS_KINDS = ("freq", "ts_freq", "opt_freq", "ts_opt_freq")

    def _preview_hess(self, d: dict, text: str, kind: str, cfg=None) -> str:
        """The .hess name the preview shows a template reading: what the
        engine would stage now (find_parent_hessian, the same search), else
        -- the parent has not run yet -- the {parent}.hess a frequency parent
        will leave; "" when it will have none to read (or the block's options
        choose its initial Hessian themselves)."""
        if not template_hess_need(text, kind, cfg):
            return ""
        try:
            src = GeometrySource(d.get("geometry_source", "direct"))
        except ValueError:
            src = GeometrySource.DIRECT
        probe = SimpleNamespace(name=str(d.get("name", "") or ""),
                                after=str(d.get("after", "") or ""),
                                geometry_source=src,
                                ref_name=str(d.get("ref_name", "") or ""))
        found, _ = find_parent_hessian(probe, SimpleNamespace(get=self.store.get),
                                       Path(self.settings.workspace_root),
                                       self.store.system_name)
        if found is not None:
            return found.name
        parents = [probe.after] + ([probe.ref_name] if src == GeometrySource.REFERENCE else [])
        for pname in parents:
            parent = self.store.get(pname) if pname else None
            if (parent is not None and parent.kind in self._HESS_KINDS
                    and parent.state != CalcState.DONE):
                return f"{pname}.hess"
        return ""

    # --- queue management (shared store) ---
    @pyqtSlot(str, result=str)
    def add_calc(self, calc_json: str) -> str:
        try:
            d = json.loads(calc_json)
            calc = calc_from_dict(d)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError,
                AttributeError, OverflowError) as e:   # a slot may not raise
            return json.dumps(MutationResult(ok=False, error=f"Invalid calculation data: {e}"))
        try:
            self.store.add(calc)
        except ValueError as e:
            return json.dumps(MutationResult(ok=False, error=str(e)))
        return json.dumps(self._accepted(calc))

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
        """Edit a pending calc in place (preserves its queue position). The
        queue editor's fields the payload leaves out are kept
        (keep_graph_fields): the guided builder edits a lane's calc without
        knowing about lanes."""
        try:
            d = json.loads(calc_json)
            calc = calc_from_dict(keep_graph_fields(d, self.store.get(old_name)))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError,
                AttributeError, OverflowError) as e:   # a slot may not raise
            return json.dumps(MutationResult(ok=False, error=f"Invalid calculation data: {e}"))
        try:
            self.store.replace(old_name, calc)
        except ValueError as e:
            return json.dumps(MutationResult(ok=False, error=str(e)))
        return json.dumps(self._accepted(calc))

    def _accepted(self, calc) -> MutationResult:
        """The success answer for add/update, carrying anything the queue
        accepted the calculation DESPITE, for the builder to show."""
        out = MutationResult(ok=True, snapshot=self.store.snapshot())
        warning = " ".join(w for w in (constraint_warning(calc),
                                       raw_geometry_warning(calc)) if w)
        if warning:
            out["warning"] = warning
        return out

    @pyqtSlot(result=str)
    def get_queue(self) -> str:
        return json.dumps(self.store.snapshot())

    # --- the queue editor (the queue window) ---
    # Inp sets are saved templates (state/inpsets.py); systems are the lanes,
    # kept in the store beside the calculations; apply_queue_ops is the one
    # write path the editor needs — a batch, all or nothing.

    def _inpset_defaults(self) -> dict:
        """What a seeded inp set writes for %pal / %maxcore: the user's own
        defaults, the ones every new calculation starts from."""
        return dict(nprocs=self.settings.default_nprocs,
                    maxcore_mb=self.settings.default_maxcore_mb)

    @pyqtSlot(result=str)
    def get_inp_sets(self) -> str:
        """Every saved inp set (seeded from ORCAdesk's defaults on first use,
        a schema-1 file migrated), each kind's seeded text (`defaults`, what
        "+ New" starts from), and what {{JOB}} becomes per calculation kind
        (`jobs`: the kinds a level's block may run, the editor's Type; "" for
        TD-DFT, whose %tddft block is the job), the kinds an MLIP set's block
        may run (`mlip_jobs`), the kinds a CREST set's block may run
        (`crest_jobs`), and the natural next step after each kind
        (`next_kind`, the Type a level dropped on the + slot starts with; any
        other kind: "sp")."""
        try:
            sets = inpsets.load_sets(**self._inpset_defaults())
            defaults = inpsets.seed_texts(**self._inpset_defaults())
        except Exception as e:   # a slot may not raise
            return json.dumps(InpSetsResult(ok=False, error=str(e), sets=[]))
        return json.dumps(InpSetsResult(ok=True, sets=sets, defaults=defaults,
                                        jobs=dict(TEMPLATE_JOB_KEYWORDS),
                                        mlip_jobs=list(MLIP_TEMPLATE_KINDS),
                                        crest_jobs=list(CREST_TEMPLATE_KINDS),
                                        next_kind=dict(inpsets.NEXT_KIND)))

    @pyqtSlot(str, result=str)
    def save_inp_set(self, set_json: str) -> str:
        """Add a set, or replace the one with the same id. Returns the set as
        stored (with its id — minted when the payload had none)."""
        try:
            saved = inpsets.save_set(json.loads(set_json or "null"),
                                     **self._inpset_defaults())
        except (ValueError, OSError, TypeError) as e:
            return json.dumps(InpSetResult(ok=False, error=str(e)))
        return json.dumps(InpSetResult(ok=True, set=saved))

    @pyqtSlot(str, result=str)
    def delete_inp_set(self, set_id: str) -> str:
        try:
            ok = inpsets.delete_set(str(set_id), **self._inpset_defaults())
        except (ValueError, OSError) as e:
            return json.dumps(InpSetResult(ok=False, error=str(e)))
        if not ok:
            return json.dumps(InpSetResult(ok=False, error=f"No inp set with id '{set_id}'."))
        return json.dumps(InpSetResult(ok=True))

    @pyqtSlot(result=str)
    def get_workflows(self) -> str:
        """Every saved workflow (state/workflows.py), in the order they were
        first saved."""
        try:
            wfs = workflows.load_workflows()
        except Exception as e:   # a slot may not raise
            return json.dumps(WorkflowsResult(ok=False, error=str(e), workflows=[]))
        return json.dumps(WorkflowsResult(ok=True, workflows=wfs))

    @pyqtSlot(str, result=str)
    def save_workflow(self, workflow_json: str) -> str:
        """Add a workflow, or replace the one with the same id. Returns it as
        stored (with its id — minted when the payload had none)."""
        try:
            saved = workflows.save_workflow(json.loads(workflow_json or "null"))
        except (ValueError, OSError, TypeError) as e:
            return json.dumps(WorkflowResult(ok=False, error=str(e)))
        return json.dumps(WorkflowResult(ok=True, workflow=saved))

    @pyqtSlot(str, result=str)
    def delete_workflow(self, workflow_id: str) -> str:
        try:
            ok = workflows.delete_workflow(str(workflow_id))
        except (ValueError, OSError) as e:
            return json.dumps(WorkflowResult(ok=False, error=str(e)))
        if not ok:
            return json.dumps(WorkflowResult(ok=False, error=f"No workflow with id '{workflow_id}'."))
        return json.dumps(WorkflowResult(ok=True))

    # The structure files ORCA writes for an IRC, in the order the queue
    # editor offers them: the two end points (what a step after it references
    # by ref_point irc_f / irc_b), then the paths a point is picked from.
    _IRC_FILES = (("F", "_IRC_F.xyz", "Forward end"),
                  ("B", "_IRC_B.xyz", "Backward end"),
                  ("F_trj", "_IRC_F_trj.xyz", "Forward path"),
                  ("B_trj", "_IRC_B_trj.xyz", "Backward path"),
                  ("Full_trj", "_IRC_Full_trj.xyz", "Whole path"))

    @pyqtSlot(str, result=str)
    def get_irc_structures(self, name: str) -> str:
        """What a step after an IRC can start from, as files in its run
        folder: {ok, done, files:[{key, label, path, count}]}. Listed only
        once the IRC is DONE -- a re-queued IRC's folder still holds the last
        run's files, and offering those as its result would be wrong; before
        that the editor offers the end points by reference (resolved when the
        step runs). The frames themselves come from get_structure_frames."""
        from ..molview import count_xyz_frames
        calc = self.store.get(name or "")
        if calc is None:
            return json.dumps(IrcStructuresResult(ok=False, error=f"No calculation named '{name}'."))
        if calc.kind != "irc":
            return json.dumps(IrcStructuresResult(ok=False, error=f"{name} is not an IRC."))
        done = calc.state == CalcState.DONE
        files: "list[IrcStructureFile]" = []
        if done:
            folder = self._calc_run_dir(calc.name)
            for key, suffix, label in self._IRC_FILES:
                p = folder / f"{calc.name}{suffix}"
                if p.is_file():
                    files.append(IrcStructureFile(key=key, label=label, path=str(p),
                                                  count=count_xyz_frames(p)))
        return json.dumps(IrcStructuresResult(ok=True, done=done, files=files))

    @pyqtSlot(str, result=str)
    def get_system(self, system_id: str) -> str:
        """One lane in full — its inputs' coordinates included (the polled
        snapshot carries their labels only)."""
        s = self.store.get_system(str(system_id))
        if s is None:
            return json.dumps(SystemResult(ok=False, error=f"No system with id '{system_id}'."))
        return json.dumps(SystemResult(ok=True, system=s))

    @pyqtSlot(str, result=str)
    def apply_queue_ops(self, ops_json: str) -> str:
        """Apply a list of queue-editor operations atomically (see
        QueueStore.apply_ops for the operations and their rules). All applied
        -> {ok, snapshot}; any refused -> {ok:false, error, op_index} and the
        queue is exactly as it was."""
        try:
            ops = json.loads(ops_json or "null")
        except ValueError as e:
            return json.dumps(QueueOpsResult(ok=False, error=f"Invalid operations: {e}",
                                             op_index=-1))
        try:
            self.store.apply_ops(ops)
        except QueueOpError as e:
            return json.dumps(QueueOpsResult(
                ok=False, error=str(e), op_index=-1 if e.index is None else e.index))
        except (ValueError, TypeError) as e:
            return json.dumps(QueueOpsResult(ok=False, error=str(e), op_index=-1))
        return json.dumps(QueueOpsResult(ok=True, snapshot=self.store.snapshot()))

    @pyqtSlot(result=str)
    def pick_xyz_files(self) -> str:
        """Pick one or more .xyz files (the queue editor's "paste the copied
        workflow onto new rows"): each read, with the system name its stem
        gives or why it cannot start a row (store.xyz_file_entries)."""
        paths, _ = QFileDialog.getOpenFileNames(
            self.window, "Load .xyz files", self._dialog_start_dir(),
            "XYZ file (*.xyz);;All files (*.*)")
        if not paths:
            return json.dumps(XyzFilesResult(ok=True, cancelled=True, files=[]))
        return json.dumps(XyzFilesResult(ok=True, cancelled=False,
                                         files=xyz_file_entries(paths)))

    @pyqtSlot(str, result=str)
    def read_xyz_paths(self, paths_json: str) -> str:
        """Dropped .xyz files by path, read like pick_xyz_files."""
        try:
            paths = json.loads(paths_json or "null")
            if not isinstance(paths, list) or not all(isinstance(x, str) for x in paths):
                raise ValueError("expected a list of paths")
        except ValueError as e:
            return json.dumps(XyzFilesResult(ok=False, error=str(e), files=[]))
        return json.dumps(XyzFilesResult(ok=True, cancelled=False,
                                         files=xyz_file_entries(paths)))

    @pyqtSlot(result=str)
    def open_queue_window(self) -> str:
        """Show (or raise) the queue editor's own window."""
        show = getattr(self.window, "show_queue_window", None)
        if show is None:
            return json.dumps(OkResult(ok=False, error="The queue window is not available."))
        try:
            show()
        except Exception as e:
            return json.dumps(OkResult(ok=False, error=str(e)))
        return json.dumps(OkResult(ok=True))

    @pyqtSlot(str, result=str)
    def get_calc(self, name: str) -> str:
        """Return the FULL data (config / xyz / raw_text / charge / ...) for one
        calculation, so it can be edited even when it isn't in this session's
        in-memory copy — e.g. a calc restored from a previous session or added
        from the phone (the polled snapshot omits these fields)."""
        c = self.store.get(name)
        if c is None:
            return json.dumps(GetCalcResult(ok=False, error="not found"))
        return json.dumps(GetCalcResult(
            ok=True, calc=calc_to_session_dict(c),
            levels=inpsets.level_forms(c.raw_text) if c.set_name else []))

    @pyqtSlot(int, result=str)
    def get_log(self, since: int) -> str:
        return json.dumps(self.store.log_since(since))

    def _calc_run_dir(self, name: str) -> Path:
        """The run folder for a queued calc: derived from its persisted
        output_path when it has one (a session-restored calc may live in a
        DIFFERENT workspace than the currently-configured one — parse_calc_output
        already resolves through output_path, so the .inp viewer, graph reseed
        and conformer export must too, or the same calc renders its result but
        fails these three), else the folder recorded when it started, else the
        one it would get now ({workspace}/{system}/{name}/ or
        {workspace}/{name}/, core/rundir.py). A name not in the queue is looked
        for where a standalone calc of that name runs. A calc whose recorded
        output is gone because the user moved its folder is found again first
        (QueueStore.relocate_moved, which records the new place on the calc)."""
        ws = Path(self.settings.workspace_root)
        calc = self.store.get(name)
        if calc is None:
            return ws / name
        self.store.relocate_moved(ws, (calc.name,))
        return self.store.result_folder(calc, ws)

    def _result_file(self, name: str, suffix: str) -> Path:
        """``{base}{suffix}`` for a calc NAME or a ``file:<path>`` result.

        A result opened from disk has no queue entry, so its name says nothing
        about where it lives: resolving it as ``{workspace}/{stem}/`` looked in
        a folder that does not exist and the notebook's Output pane read "No
        output on disk." beside a fully parsed summary. The file's own folder
        is the run folder (ORCA writes ``{base}.inp``/``.out`` side by side).
        A calc name cannot contain ``:``, so the prefix is unambiguous."""
        if name.startswith("file:"):
            p = Path(name[5:])
            if suffix == ".out" and p.name.lower().endswith(".out"):
                return p
            low = p.name.lower()
            base = (p.name[:-len(".mlip.json")] if low.endswith(".mlip.json")
                    else p.name[:-4] if low.endswith(".out") else p.stem)
            return p.parent / f"{base}{suffix}"
        return self._calc_run_dir(name) / f"{name}{suffix}"

    @pyqtSlot(str, result=str)
    def get_inp(self, name: str) -> str:
        """Return the on-disk ORCA .inp for a calculation, so a running or finished
        job's actual input can be viewed read-only (not only editable ones).
        ``name`` is a calc name or ``file:<path>`` (see :meth:`_result_file`)."""
        try:
            p = self._result_file(name, ".inp")
            if not p.exists():
                return json.dumps(TextResult(ok=False, error="no input on disk yet"))
            return json.dumps(TextResult(ok=True, text=p.read_text(encoding="utf-8", errors="replace")))
        except Exception as e:
            return json.dumps(TextResult(ok=False, error=str(e)))

    @pyqtSlot(str, result=str)
    def get_graph_lines(self, name: str) -> str:
        """The lines of a calculation's .out the Jobs view's trackers are
        rebuilt from, in file order (core/replay.py says which and why).

        Takes the calc NAME and resolves the run folder server-side via
        _calc_run_dir (works mid-run and for restored sessions alike, even
        from an old workspace). Answers at once when little of the file is
        unread; a large first read runs on a worker thread and this returns
        ``pending`` until it is done, so a 150 MB output never holds the Qt
        main thread. A later call reads only what was appended since."""
        try:
            out_path = self._calc_run_dir(name) / f"{name}.out"
            if not out_path.exists():
                return json.dumps(GraphLinesResult(ok=False, error="no output", lines=[]))
            lines = self._replay.lines(out_path)
            if lines is None:
                return json.dumps(GraphLinesResult(ok=True, pending=True, lines=[]))
            return json.dumps(GraphLinesResult(ok=True, lines=lines))
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
        line. ``name`` is a calc name or ``file:<path>`` (see
        :meth:`_result_file`)."""
        try:
            out_path = self._result_file(name, ".out")
            if not out_path.exists():
                return json.dumps(LogTailResult(ok=False, error="no output", lines=[]))
            lines, more = tail_lines(out_path, max_lines)
            return json.dumps(LogTailResult(
                ok=True, lines=lines, file=out_path.name, truncated=more))
        except Exception as e:
            return json.dumps(LogTailResult(ok=False, error=str(e), lines=[]))

    # The Results window's output pane: the whole file, a screenful at a time.
    # Every slot takes a calc name or "file:<path>" (see _result_file).
    @pyqtSlot(str, result=str)
    def get_output_info(self, name: str) -> str:
        """Line count, size and longest line of a result's .out -- what the
        pane needs to size its scrollbar before it has read a single line."""
        from ..outview import index_for
        try:
            p = self._result_file(name, ".out")
            if not p.exists():
                return json.dumps(OutputInfoResult(ok=False, error="no output"))
            idx = index_for(p)
            return json.dumps(OutputInfoResult(
                ok=True, file=p.name, bytes=idx.size, lines=idx.line_count,
                max_len=idx.max_len))
        except Exception as e:
            return json.dumps(OutputInfoResult(ok=False, error=str(e)))

    @pyqtSlot(str, int, int, result=str)
    def get_output_lines(self, name: str, start: int, count: int) -> str:
        """Lines ``start .. start+count-1`` of a result's .out (capped at
        outview.MAX_LINES_PER_REQUEST)."""
        from ..outview import index_for
        try:
            p = self._result_file(name, ".out")
            if not p.exists():
                return json.dumps(OutputLinesResult(ok=False, error="no output", lines=[]))
            return json.dumps(OutputLinesResult(
                ok=True, start=max(0, start), lines=index_for(p).lines(start, count)))
        except Exception as e:
            return json.dumps(OutputLinesResult(ok=False, error=str(e), lines=[]))

    @pyqtSlot(str, str, result=str)
    def find_in_output(self, name: str, query: str) -> str:
        """Every match of ``query`` in a result's .out, ASCII case ignored --
        searched here rather than in the page, which never holds the file."""
        from ..outview import index_for
        try:
            p = self._result_file(name, ".out")
            if not p.exists():
                return json.dumps(OutputFindResult(ok=False, error="no output", hits=[]))
            hits, total = index_for(p).find(query)
            return json.dumps(OutputFindResult(ok=True, hits=hits, total=total))
        except Exception as e:
            return json.dumps(OutputFindResult(ok=False, error=str(e), hits=[]))

    @pyqtSlot(str, result=str)
    def export_conformers(self, name: str) -> str:
        """Split a finished CREST search's ensemble into per-conformer .xyz files
        under its run folder's conformers/ ({name}_c1.xyz = the best conformer,
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
    def list_structure_sets(self, source: str) -> str:
        """Every ``.xyz`` set sitting with a result that the 3D viewer can open —
        the CREST ensemble, the per-conformer ``conformers/`` export, an
        optimization trajectory. ``source`` is ``calc:<name>`` or ``file:<path>``
        (:meth:`_plot_source` resolves the folder; a file's folder is its
        parent), so a queued calculation and a result opened from disk are both
        addressable.

        This replaced a folder picker. Everything worth opening already sits in
        the result's own run folder, so the tab lists what is there instead of
        asking the user to know where ORCAdesk put it.
        """
        from ..molview import discover_structure_sets
        try:
            run_dir, base, _open_shell, err = self._plot_source(source)
            if err:
                return json.dumps(StructureSetsResult(ok=False, error=err))
            sets = discover_structure_sets(run_dir, base)
        except OSError as e:
            return json.dumps(StructureSetsResult(ok=False, error=str(e)))
        return json.dumps(StructureSetsResult(ok=True, sets=sets))

    @pyqtSlot(str, result=str)
    def get_structure_frames(self, path: str) -> str:
        """Viewer frames for one structure set: a folder of ``.xyz`` files
        (natural filename order, each possibly multi-frame) or a single
        ``.xyz``. Returns {ok, title, folder, frames:[{label, xyz, energy}]};
        ``folder`` is the favorites/export destination — the folder itself, or a
        file's parent."""
        from ..molview import frames_from_file, frames_from_folder
        p = Path(path or "")
        if not path or not p.exists():
            return json.dumps(FramesResult(ok=False, error="That structure is no longer on disk."))
        # A CREST ensemble's frames are labelled the way its exported files are
        # named ({run}_c1 …, c1 = the best conformer), not after the file they
        # happen to live in: those labels are what the favorites store is keyed
        # on and what "Export ★" writes, so the two must agree.
        prefix = f"{p.parent.name}_c" if p.name.lower() in _CREST_ENSEMBLE else ""
        try:
            frames = frames_from_folder(p) if p.is_dir() else frames_from_file(p, prefix)
        except OSError as e:
            return json.dumps(FramesResult(ok=False, error=str(e)))
        if not frames:
            return json.dumps(FramesResult(ok=False, error="No .xyz structures there."))
        folder = str(p if p.is_dir() else p.parent)
        return json.dumps(FramesResult(ok=True, title=p.name, folder=folder, frames=frames))

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
            labels, saved = _toggle(source, label, on)
            # The star lighting up IS the confirmation, so a write that did not
            # land must not report ok — the user would star their way through an
            # ensemble and find every star gone at the next launch.
            if not saved:
                return json.dumps(FavoritesResult(
                    ok=False, labels=labels,
                    error="The star could not be saved — favorites.json is not "
                          "writable, so it will be gone when ORCAdesk restarts."))
            return json.dumps(FavoritesResult(ok=True, labels=labels))
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
            used: set = set()
            for fr in frames:
                xyz = fr.get("xyz", "")
                if not isinstance(xyz, str) or not xyz:
                    # a malformed frame must not abort the export half-way and
                    # leave the user with some of their favorites on disk
                    continue
                # The label is sanitized to ASCII, so two labels that differ
                # only in non-ASCII characters — every all-Korean pair, which
                # collapses to "frame" — produced ONE file while the toast
                # counted them all. Number the collisions instead of losing them.
                stem = re.sub(r"[^A-Za-z0-9._-]+", "_",
                              str(fr.get("label") or "frame")).strip("_") or "frame"
                fname = stem
                n = 2
                while fname.lower() in used:
                    fname = f"{stem}_{n}"
                    n += 1
                used.add(fname.lower())
                text = xyz if xyz.endswith("\n") else xyz + "\n"
                (out_dir / f"{fname}.xyz").write_text(text, encoding="utf-8")
                count += 1
            return json.dumps(ExportResult(ok=True, count=count, folder=str(out_dir)))
        except Exception as e:
            return json.dumps(ExportResult(ok=False, error=str(e)))

    # --- orbital / density cubes (Results tab 3D viewer) ---
    def _plot_source(self, source: str) -> tuple:
        """Resolve a plot source to ``(run_dir, base, open_shell, error)``.

        Two addressing schemes, mirroring the viewer's existing favorites keys:

        * ``calc:<name>`` — a queued calculation. The folder comes from
          :meth:`_calc_run_dir`, so a session-restored calc living in an old
          workspace still resolves, and the multiplicity from the Calculation.
        * ``file:<path>`` — a result opened from disk (*Open file…* or a drop),
          which has no queue entry at all. ORCA writes ``{base}.gbw`` beside
          ``{base}.out``, so the folder is the file's parent and the base its
          stem; the multiplicity is read back out of the ``.out``.

        An unprefixed string is a calc name (names are folder-safe, so they
        cannot contain the ``:`` that a Windows path always does — which is why
        the prefix is required rather than sniffed).
        """
        from ..core.parser import read_multiplicity
        src = (source or "").strip()
        if src.startswith("file:"):
            p = Path(src[5:])
            if not p.exists():
                return None, "", False, "That file is no longer on disk."
            base = p.name[:-4] if p.name.lower().endswith(".out") else p.stem
            mult = read_multiplicity(p)
            # None = the .out never said. Not knowing is not the same as knowing
            # it is closed-shell, but the honest fallback is still to withhold
            # the spin-density option: offering one that cannot work is worse
            # than omitting one that might have (P2).
            return p.parent, base, bool(mult and mult > 1), ""
        name = src[5:] if src.startswith("calc:") else src
        if not name:
            return None, "", False, "No calculation selected."
        calc = self.store.get(name)
        return (self._calc_run_dir(name), name,
                bool(calc is not None and calc.multiplicity > 1), "")

    @pyqtSlot(str, result=str)
    def get_plot_options(self, source: str) -> str:
        """What a finished result can be visualized as: whether its ``.gbw`` is on
        disk, which plot kinds this wavefunction supports, the grid choices, and
        which cubes already exist (those open instantly). ``source`` is
        ``calc:<name>`` or ``file:<path>`` — see :meth:`_plot_source`.

        Spin density is offered only for an open-shell run: a closed-shell one
        writes no spin density for orca_plot to read, and a button that can only
        fail is exactly the reassurance P2 rules out. The orbital list is not
        returned — the Results tab already holds it from the parse payload.

        The ESP map rides the same availability as the electron density: both are
        computed from the SCF density orca_plot lists as ``<base>.scfp``, so a run
        that can plot one can plot the other."""
        from ..core.plot import (GRID_CHOICES, DEFAULT_GRID, PNBO_KIND, default_grid_for,
                                 cube_is_current)
        from ..cube import ESP_SURFACE_ISOVALUE, ESP_DEFAULT_RANGE
        try:
            run_dir, base, open_shell, err = self._plot_source(source)
            if err:
                return json.dumps(PlotOptionsResult(ok=False, error=err))
            gbw = run_dir / f"{base}.gbw"
            has_gbw = gbw.exists()
            # the NBO program's orbitals (`! NBO`), in .gbw format beside it;
            # offered for a closed shell only -- an open-shell .nbo has not
            # been measured, so which orbital is which is not known (P3)
            nbo_file = run_dir / f"{base}.nbo"
            has_nbo = nbo_file.exists() and not open_shell
            cubes = run_dir / "cubes"
            # only cubes of THIS wavefunction: a re-run under the same name
            # leaves the previous molecule's cubes behind. An NBO cube is dated
            # against the .nbo it was plotted from.
            pnbo_prefix = f"{base}.{PNBO_KIND}"
            cached = sorted(
                p.name for p in cubes.glob("*.cube")
                if cube_is_current(p, nbo_file if p.name.startswith(pnbo_prefix) else gbw)
            ) if cubes.is_dir() else []
        except OSError as e:
            return json.dumps(PlotOptionsResult(ok=False, error=str(e)))
        return json.dumps(PlotOptionsResult(
            ok=True, base=base, folder=str(run_dir), has_gbw=has_gbw,
            open_shell=open_shell,
            kinds=(["mo", "eldens", "esp"] + (["spindens"] if open_shell else [])
                   + ([PNBO_KIND] if has_nbo else [])),
            has_nbo=has_nbo,
            grids=list(GRID_CHOICES), default_grid=DEFAULT_GRID, cached=cached,
            esp_grid=default_grid_for("esp"),
            esp_surface_iso=ESP_SURFACE_ISOVALUE, esp_range=ESP_DEFAULT_RANGE))

    @pyqtSlot(str, result=str)
    def generate_cube(self, payload_json: str) -> str:
        """Run orca_plot for one ``{source, kind, index, operator, grid}`` request
        on a background thread; returns the status immediately and the UI polls
        get_cube_status(), then fetches get_cube_data() once when it is done.
        ``source`` addresses a queued calc or a file on disk (:meth:`_plot_source`).

        Refuses while another cube is in flight: orca_plot writes a *fixed*
        filename beside the ``.gbw``, so two runs in one folder would race for the
        same path and the loser would be served the winner's data."""
        from ..core.plot import CubeRequest, DEFAULT_GRID, KIND_LABELS, NBO_KIND, PNBO_KIND
        from ..core.plot import generate_cube as _generate
        from ..nbo.cubes import generate_nbo_cube
        from ..nbo.wavefunction import WavefunctionError
        try:
            d = json.loads(payload_json or "{}")
        except json.JSONDecodeError as e:
            return json.dumps(ErrorPayload(error=str(e)))
        run_dir, base, _open_shell, err = self._plot_source(str(d.get("source") or ""))
        if err:
            return json.dumps(ErrorPayload(error=err))
        req = CubeRequest(kind=str(d.get("kind") or "mo"),
                          index=int(d.get("index") or 0),
                          operator=int(d.get("operator") or 0),
                          grid=int(d.get("grid") or DEFAULT_GRID)).normalized()
        label = (f"NBO {req.index + 1}" if req.kind == PNBO_KIND
                 else f"{KIND_LABELS[req.kind]} {req.index}{'β' if req.operator else 'α'}"
                 if req.kind in ("mo", NBO_KIND) else KIND_LABELS.get(req.kind, req.kind))

        with self._cube_lock:
            if self._cube.get("state") == "running":
                # Serialize inline, NOT via get_cube_status(): that slot takes
                # this same non-reentrant lock, so calling it here deadlocks —
                # and it deadlocks on Qt's UI thread, freezing the whole window
                # rather than failing one click. Reproduced.
                return json.dumps(CubeJobPayload(**self._cube))
            self._cube = {"state": "running", "label": label, "error": "",
                          "kind": req.kind, "index": req.index,
                          "operator": req.operator, "grid": req.grid,
                          "cached": False, "seconds": 0.0, "path": ""}
            self._cube_path = ""

        orca_path = self.settings.orca_path

        def _worker() -> None:
            try:
                if req.kind == NBO_KIND:
                    # ORCAdesk's own vector, drawn in process: orca_plot has
                    # no way to take it (orcamgr/nbo/cubes.py)
                    orbitals = self._nbo_orbital_set(orca_path, run_dir, base)
                    res = generate_nbo_cube(
                        orbitals, base, req, dest_dir=run_dir / "cubes",
                        on_log=lambda m: self.store.append_log(m, "info"))
                else:
                    res = _generate(orca_path, run_dir, base, req,
                                    on_log=lambda m: self.store.append_log(m, "info"))
            except WavefunctionError as e:
                res = {"ok": False, "error": str(e)}
            except Exception as e:          # a crash must still release the UI
                res = {"ok": False, "error": str(e)}
            if not res.get("ok"):
                self.store.append_log(
                    f"Could not plot {label} for {base}: {res.get('error', '')}", "err")
            with self._cube_lock:
                self._cube_path = res.get("path", "") if res.get("ok") else ""
                self._cube.update({
                    "label": str(res.get("label") or label),
                    "path": self._cube_path,
                    "state": "done" if res.get("ok") else "error",
                    "error": "" if res.get("ok") else str(res.get("error") or ""),
                    "cached": bool(res.get("cached")),
                    "seconds": float(res.get("seconds") or 0.0)})

        threading.Thread(target=_worker, name="orca-plot", daemon=True).start()
        return self.get_cube_status()

    @pyqtSlot(result=str)
    def get_cube_status(self) -> str:
        """Progress of the running (or last) cube generation. Deliberately tiny —
        it is polled, and the cube itself goes through get_cube_data()."""
        with self._cube_lock:
            return json.dumps(CubeJobPayload(**self._cube))

    @pyqtSlot(result=str)
    def get_cube_data(self) -> str:
        """The finished cube, for 3Dmol's ``addVolumetricData(text, "cube")``.
        Fetched once per generation, never from the poll."""
        from ..core.plot import MAX_CUBE_BYTES
        from ..cube import load_cube
        with self._cube_lock:
            path = self._cube_path
            kind = str(self._cube.get("kind") or "mo")
        if not path:
            return json.dumps(CubeDataResult(ok=False, error="No cube to show yet."))
        # load_cube already returns the CubeDataResult, ok/error included, so the
        # payload has ONE construction site (P4). It used to be re-spelled field
        # by field here, which is a second place to forget a key.
        return json.dumps(load_cube(path, kind, max_bytes=MAX_CUBE_BYTES))

    # --- natural-orbital analysis (orcamgr/nbo), computed in process ---
    @pyqtSlot(str, result=str)
    def run_nbo_analysis(self, source: str) -> str:
        """Start the NPA/NBO analysis of one finished calculation on a background
        thread; returns the status at once and the UI polls get_nbo_status(),
        then fetches get_nbo_result() once when it is done. ``source`` addresses
        a queued calc or a file on disk exactly as the plot slots do.

        One at a time: the analysis converts the .gbw and writes a cache beside
        the run, and two on one folder would race for the same files. A refused
        start hands back the in-flight job's status, which names ITS source."""
        from ..nbo.analysis import analysis_for
        from ..nbo.wavefunction import WavefunctionError
        run_dir, base, _open_shell, err = self._plot_source(str(source or ""))
        if err:
            return json.dumps(NboJobPayload(state="error", source=source, error=err,
                                            seconds=0.0))
        with self._nbo_lock:
            if self._nbo.get("state") == "running":
                return json.dumps(NboJobPayload(**self._nbo))
            self._nbo = {"state": "running", "source": source, "error": "",
                         "seconds": 0.0}
            self._nbo_result = {}
        orca_path = self.settings.orca_path
        started = time.time()

        def _worker() -> None:
            try:
                analysis = analysis_for(
                    orca_path, run_dir, base,
                    keep=lambda s: self._remember_nbo_set(run_dir, base, s))
                result = {"ok": True, **analysis.to_dict()}
                error = ""
            except WavefunctionError as e:
                result, error = {"ok": False, "error": str(e)}, str(e)
            except Exception as e:          # a crash must still release the UI
                result, error = {"ok": False, "error": str(e)}, str(e)
            if error:
                self.store.append_log(
                    f"Natural-orbital analysis of {base} failed: {error}", "err")
            else:
                self.store.append_log(
                    f"Natural-orbital analysis of {base}: {time.time() - started:.1f} s", "info")
            with self._nbo_lock:
                self._nbo_result = result
                self._nbo.update({"state": "error" if error else "done",
                                  "error": error,
                                  "seconds": float(time.time() - started)})

        threading.Thread(target=_worker, name="nbo-analysis", daemon=True).start()
        return self.get_nbo_status()

    def _remember_nbo_set(self, run_dir: Path, base: str, orbital_set) -> None:
        """Keep ONE orbital set: the one most recently built."""
        with self._nbo_lock:
            self._nbo_sets = {(str(run_dir), base): orbital_set}

    def _nbo_orbital_set(self, orca_path: str, run_dir: Path, base: str):
        """The NBO basis of a result: the remembered one when it is this
        result's and still describes the .gbw on disk, else built now (and
        remembered). Raises WavefunctionError like the analysis does. Runs
        on the cube worker thread -- the build is seconds and must not hold
        a lock the UI thread takes."""
        from ..nbo.analysis import orbital_set_for
        key = (str(run_dir), base)
        try:
            mtime = (run_dir / f"{base}.gbw").stat().st_mtime
        except OSError:
            mtime = 0.0
        with self._nbo_lock:
            kept = self._nbo_sets.get(key)
        if kept is not None and kept.source_mtime == mtime:
            return kept
        built = orbital_set_for(orca_path, run_dir, base)
        self._remember_nbo_set(run_dir, base, built)
        return built

    # --- the 2D drawing's orbital overlay (orcamgr/nbo/molobes.py) ---
    @pyqtSlot(str, str, int, result=str)
    def get_mo_lobes(self, source: str, spin: str, index: int) -> str:
        """Per-atom lobes of one MO -- {ok, state: "done", atoms: [{share, s,
        p, s_share, p_share, d_share}], elements, coords, ...} -- for the 2D
        drawing to draw under its bonds. ``source`` addresses a result as the
        plot slots do; ``spin`` is "alpha"/"beta" (or the parse's ""/"a"/"b");
        ``index < 0`` returns the orbital list instead of one orbital.

        The wavefunction comes from the same .gbw -> Molden conversion the NBO
        tiles use (nbo/source.py: reused, not re-run, while it is newer than
        the .gbw), or straight from the NBO set already in memory when that
        is this result's. Reading it and forming S^1/2 can take seconds, so
        the first call starts a background build and answers state
        "running"; the front-end calls again until it is "done". Errors are
        data, never raised."""
        from ..nbo.molobes import MoLobeSet
        from ..nbo.source import wavefunction_for
        from ..nbo.wavefunction import WavefunctionError

        def fail(msg: str) -> str:
            return json.dumps(MoLobesPayload(ok=False, state="error", error=msg))

        run_dir, base, _open_shell, err = self._plot_source(str(source or ""))
        if err:
            return fail(err)
        try:
            mtime = (run_dir / f"{base}.gbw").stat().st_mtime
        except OSError:
            return fail(f"There is no {base}.gbw beside this result, so there is "
                        "no wavefunction to draw orbitals from.")
        key = (str(run_dir), base)
        start = False
        with self._lobe_lock:
            cur = self._lobe
            mine = cur.get("key") == key and cur.get("mtime") == mtime
            if mine:
                state, lset, error = cur["state"], cur.get("set"), cur.get("error", "")
            elif cur.get("state") == "running":
                # another result's build is in flight: ask again once it is done
                state, lset, error = "running", None, ""
            else:
                self._lobe = {"key": key, "mtime": mtime, "state": "running",
                              "error": "", "set": None}
                state, lset, error, start = "running", None, "", True
        if start:
            orca_path = self.settings.orca_path

            def _worker() -> None:
                try:
                    with self._nbo_lock:
                        kept = self._nbo_sets.get(key)
                    wf = (kept.wavefunction if kept is not None
                          and kept.source_mtime == mtime
                          else wavefunction_for(orca_path, run_dir, base))
                    built, failure = MoLobeSet.from_wavefunction(wf, mtime), ""
                except WavefunctionError as e:
                    built, failure = None, str(e)
                except Exception as e:          # a crash must still answer the UI
                    built, failure = None, str(e) or type(e).__name__
                if failure:
                    self.store.append_log(
                        f"Orbital lobes for {base}: {failure}", "err")
                with self._lobe_lock:
                    if self._lobe.get("key") == key and self._lobe.get("mtime") == mtime:
                        self._lobe.update({"state": "error" if failure else "done",
                                           "error": failure, "set": built})

            threading.Thread(target=_worker, daemon=True,
                             name="orcadesk-mo-lobes").start()
        if state == "running":
            return json.dumps(MoLobesPayload(ok=True, state="running"))
        if state == "error" or lset is None:
            return fail(error or "The wavefunction could not be read.")
        try:
            if int(index) < 0:
                return json.dumps(MoLobesPayload(
                    ok=True, state="done", orbitals=lset.orbital_list(),
                    elements=lset.elements(), coords=lset.coordinates_angstrom()))
            r = lset.lobes(spin, int(index))
        except WavefunctionError as e:
            return fail(str(e))
        return json.dumps(MoLobesPayload(
            ok=True, state="done", spin=r["spin"], index=r["index"],
            energy_ev=r["energy_ev"], occupation=r["occupation"], norm=r["norm"],
            atoms=r["atoms"], elements=lset.elements(),
            coords=lset.coordinates_angstrom()))

    @pyqtSlot(result=str)
    def get_nbo_status(self) -> str:
        """Progress of the running (or last) analysis. Tiny -- it is polled."""
        with self._nbo_lock:
            return json.dumps(NboJobPayload(**self._nbo))

    @pyqtSlot(result=str)
    def get_nbo_result(self) -> str:
        """The finished analysis, fetched once. Empty until a run has finished."""
        with self._nbo_lock:
            if not self._nbo_result:
                return json.dumps(NboResultPayload(ok=False, error="No analysis to show yet."))
            return json.dumps(self._nbo_result)

    # --- hand a generated file to the user's own programs (P5) ---
    @pyqtSlot(str, result=str)
    def open_path_external(self, path: str) -> str:
        """Open one generated file with whatever the OS associates with its type.

        The Visual tab's alternative destination: ORCAdesk still runs orca_plot
        and writes the file, then hands it over instead of drawing it. Refuses
        anything that is not a data format — see :mod:`orcamgr.openwith`, where
        that allowlist is the trust boundary."""
        from ..openwith import open_with_default
        res = open_with_default(path)
        if not res.get("ok"):
            self.store.append_log(
                f"Could not open {Path(path).name}: {res.get('error', '')}", "err")
        return json.dumps(OkResult(ok=bool(res.get("ok")),
                                   error=str(res.get("error") or "")))

    @pyqtSlot(str, result=str)
    def show_path_in_folder(self, path: str) -> str:
        """Reveal a file (or folder) in the file manager, selected.

        Answers "where did that actually get written?" without making the user
        reconstruct a workspace path by hand."""
        from ..openwith import show_in_folder
        res = show_in_folder(path)
        if not res.get("ok"):
            self.store.append_log(
                f"Could not show {Path(path).name}: {res.get('error', '')}", "err")
        return json.dumps(OkResult(ok=bool(res.get("ok")),
                                   error=str(res.get("error") or "")))

    @pyqtSlot(str, result=str)
    def show_calc_folder(self, name: str) -> str:
        """The queue editor's *Show folder*: a queued calculation's run folder
        in the file manager -- its output file selected when it has one, else
        the folder it ran in (or will run in), which the backend resolves
        (core/rundir.py) so the page never builds a workspace path itself.
        A folder that does not exist yet is refused with a reason."""
        calc = self.store.get(str(name or ""))
        if calc is None:
            return json.dumps(OkResult(ok=False, error=f"No calculation named '{name}'."))
        # a folder the user moved by hand is followed (it records in place)
        self.store.relocate_moved(self.settings.workspace_root, (calc.name,))
        if calc.output_path and Path(calc.output_path).exists():
            return self.show_path_in_folder(calc.output_path)
        folder = self._calc_run_dir(calc.name)
        if not folder.is_dir():
            return json.dumps(OkResult(ok=False, error=f"{calc.name} has no folder yet."))
        return self.show_path_in_folder(str(folder))

    @pyqtSlot(str, str, result=str)
    def save_structure_xyz(self, source: str, xyz: str) -> str:
        """Write a parsed geometry into the run folder as ``<base>_structure.xyz``.

        The Structure row is the one Visual row backed by no file at all — the
        geometry lives in the parse payload. Sending it to another program means
        it has to become one, and ``.xyz`` is the format every viewer reads. The
        text comes from the front-end's own ``geomToXyz`` so there is one
        definition of it (P4), not a second formatter here."""
        run_dir, base, _open_shell, err = self._plot_source(source)
        if err:
            return json.dumps(SavedFileResult(ok=False, error=err))
        if not (xyz or "").strip():
            return json.dumps(SavedFileResult(
                ok=False, error="This result has no geometry to write."))
        out = run_dir / f"{base}_structure.xyz"
        try:
            out.write_text(xyz if xyz.endswith("\n") else xyz + "\n",
                           encoding="utf-8")
        except OSError as e:
            return json.dumps(SavedFileResult(
                ok=False, error=f"Could not write {out.name}: {e}"))
        return json.dumps(SavedFileResult(ok=True, path=str(out)))

    # --- run / cancel ---
    @pyqtSlot(result=str)
    def get_free_energy_profile(self) -> str:
        """Finished calculations that can stand in a reaction profile, in queue
        order, with whatever energies they have.

        Every DONE calc with an energy is here, not only those with a Gibbs
        value. A chloride ion has no vibrations to compute, and an `sp` of one
        is a perfectly good species in `CoCl4(2-) + 6 H2O -> Co(H2O)6(2+) +
        4 Cl(-)`; filtering on "has frequencies" removed exactly the species
        that cannot have them. `gibbs_eh` is None when the run produced no
        thermochemistry, and the UI picks the QUANTITY (G, H, T·S, S, U, E+ZPE,
        E) and refuses to mix them rather than quietly substituting one for
        another. Every thermochemical term is sent, not just G, because the
        reaction profile analyses each of them.

        Energies are absolute Hartree; the UI picks a reference and converts to
        relative kcal/mol or kJ/mol."""
        pts = []
        for c in self.store.list():
            if c.state != CalcState.DONE:
                continue
            # Parse-on-miss: DONE calcs restored from a previous session aren't
            # eagerly re-parsed at startup, so read the .out on demand here (only
            # when the user actually opens the reaction profile).
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
            e = getattr(c.result, "final_energy_eh", None)
            if g is None and e is None:
                continue
            # the composition, so the UI can say whether a step's two sides hold
            # the same matter before it prints a difference between them
            atoms: dict[str, int] = {}
            for a in getattr(c.result, "geometry", None) or []:
                sym = str(getattr(a, "symbol", "") or "").strip().capitalize()
                if sym:
                    atoms[sym] = atoms.get(sym, 0) + 1
            pts.append(FepPoint(
                name=c.name,
                gibbs_eh=g,
                final_energy_eh=e,
                zpe_eh=getattr(c.result, "zpe_eh", None),
                total_thermal_eh=getattr(c.result, "total_thermal_eh", None),
                enthalpy_eh=getattr(c.result, "enthalpy_eh", None),
                entropy_term_eh=getattr(c.result, "entropy_term_eh", None),
                temperature_k=getattr(c.result, "temperature_k", None),
                kind=c.kind,
                atoms=atoms,
                charge=int(getattr(c, "charge", 0) or 0),
            ))
        return json.dumps(FepResult(ok=True, points=pts))

    @pyqtSlot(result=str)
    def _overwrite_conflicts(self) -> set:
        """Names of queued calculations whose folder already holds a result they
        would overwrite. Shared by the Run-button check below and the startup
        resume, so the two cannot disagree about what "would be clobbered"
        means (P4)."""
        ws = Path(self.settings.workspace_root)
        # DONE and FILTERED are skipped by the run loop; FAILED is locked (P24) and
        # never runs as it is; a CANCELLED calc overwriting its own partial .out
        # is the point of a retry; a RUNNING one is appending to its own file. A
        # rerun row (a set put on a DONE or FAILED calc, store rerun_calc) was confirmed
        # as an overwrite when it was made: it is not asked about again, and
        # never kept (make_engine_factory drops it from the keep answers too).
        skip = {CalcState.DONE, CalcState.FAILED, CalcState.CANCELLED,
                CalcState.RUNNING, CalcState.FILTERED}
        out = set()
        for c in self.store.list():
            if c.state in skip or is_rerun(c):
                continue
            # the folder it will run in: the one recorded when it started, else
            # the one its system's name gives now (core/rundir.py) -- the
            # folder the engine's keep-existing reads, so the question and the
            # answer are about the same output. A {workspace}/{name}/ from
            # before system folders is not where a calc in a system runs, so
            # running it cannot overwrite that one.
            if holds_output(self.store.run_folder(c, ws), c.name):
                out.add(c.name)
        return out

    @pyqtSlot(result=str)
    def check_overwrite_conflicts(self) -> str:
        """Return the names of queued calculations that would overwrite an
        existing result on disk (a {name}.out already in the workspace), so the
        UI can warn before a run clobbers earlier work. Excluded states:
        DONE is skipped by the run loop anyway; FAILED is locked (P24) — it
        never runs as it is, so it has nothing to overwrite (a rerun of it was
        asked about as an overwrite when it was made); and a CANCELLED calc
        overwriting its own partial .out is the whole point of a retry, not a
        loss worth warning about. The warning exists for PENDING/BLOCKED name
        reuse — a fresh calc whose name would clobber an earlier good result."""
        try:
            return json.dumps(ConflictsResult(
                ok=True, conflicts=sorted(self._overwrite_conflicts())))
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
            return json.dumps(ExistsResult(ok=True, exists=self._holds_output(str(name))))
        except Exception as e:
            return json.dumps(ExistsResult(ok=False, error=str(e), exists=False))

    def _holds_output(self, name: str) -> bool:
        """Whether the workspace already holds a result artifact ({name}.out,
        or the MLIP worker's {name}.mlip.json) for a NEW calc called `name`:
        in {workspace}/{name}/, or in a {workspace}/{folder}/{name}/ -- the
        folder of a system, current or left by a removed row. Which system the
        new calc will run under is not known here, so every place it could
        land counts (core.rundir.names_with_output)."""
        return bool(names_with_output(self.settings.workspace_root, [name]))

    @pyqtSlot(str, result=str)
    def existing_outputs(self, names_json: str) -> str:
        """has_existing_output for many names at once: ``["a", "b"]`` ->
        {ok, existing: [the names whose workspace folder already holds an
        output]}, in the order given. The queue editor's adds that bypass the
        builder (a dropped set, a paste, a row copy, a dropped .xyz) ask here
        before they land in a running queue, and its free-name search skips
        such names. A name that is not a plain folder name (empty, a path) is
        simply not listed. Where a name counts as holding output is
        _holds_output's rule, read with one listing of the workspace for all
        the names at once."""
        try:
            names = json.loads(names_json or "[]")
            if not isinstance(names, list):
                raise ValueError("expected a list of calculation names")
            plain = [n for n in names[:10_000]
                     if isinstance(n, str) and n.strip() and Path(n).name == n
                     and n not in (".", "..")]
            existing = names_with_output(self.settings.workspace_root, plain)
            return json.dumps(ExistingOutputsResult(ok=True, existing=existing))
        except Exception as e:   # a slot may not raise
            return json.dumps(ExistingOutputsResult(ok=False, error=str(e), existing=[]))

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
        factory = make_engine_factory(self.store, self.settings.orca_path,
                                      self.settings.workspace_root, skip_names,
                                      mlip_envs=self._mlip_envs_for_run(),
                                      crest_distro=self.settings.crest_distro,
                                      budget=self._budget(),
                                      **self._device_args())
        try:
            # the reference screen runs inside start_run's lock, against the
            # live list — see start_run(precheck=...)
            self.store.start_run(factory, precheck=find_dangling_reference)
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

    def relocate_moved_calcs(self) -> None:
        """Startup hook (not a JS slot): find again, in the background, every
        restored calculation whose folder the user moved by hand while the app
        was closed (QueueStore.relocate_moved). A thread, so a large workspace
        never holds up the first paint; the store's lock covers what it
        records, and the page sees it on its next poll."""
        ws = self.settings.workspace_root
        threading.Thread(target=self.store.relocate_moved, args=(ws,),
                         name="relocate-moved", daemon=True).start()

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
        # This starts a full run over the RESTORED queue — every PENDING row
        # runs too, exactly as if Run had been pressed. But nobody pressed it:
        # there is no one at the keyboard to answer the overwrite modal the Run
        # button shows, so a queued row whose folder already holds a result
        # would silently recompute over it. Keep those results instead (the
        # engine re-validates each before adopting it, and falls through to a
        # real run when one does not hold up), and say which ones.
        keep = self._overwrite_conflicts()
        if keep:
            self.store.append_log(
                "Keeping the existing results of " + ", ".join(sorted(keep))
                + " — a resumed run cannot ask before overwriting them. "
                  "Re-run them from the Run button to recompute.", "info")
        factory = make_engine_factory(self.store, self.settings.orca_path,
                                      self.settings.workspace_root, keep,
                                      mlip_envs=self._mlip_envs_for_run(),
                                      crest_distro=self.settings.crest_distro,
                                      budget=self._budget(),
                                      **self._device_args())
        # The other screen the Run button applies. A reference whose parent is
        # gone cannot be refused here — the running job must still be
        # reattached — so it is reported, and the engine blocks that row rather
        # than failing it (a FAILED calc is locked, P24; a BLOCKED one can be
        # re-pointed).
        ref_err = find_dangling_reference(self.store.list())
        if ref_err:
            self.store.append_log(ref_err, "warn")
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
