"""
QWebChannel bridge between the HTML/JS front-end and the Python core.

Stage 3 change: the queue now lives in a shared QueueStore (the same object the
HTTP server uses), instead of being owned by the JS side. The desktop UI reads
the queue/log by polling cheap @pyqtSlot getters (get_queue / get_log) on a
QTimer in JS — this keeps the store's worker thread and Qt's UI thread cleanly
separated (no cross-thread Qt signal juggling).

JS calls these slots:
  get_about, get_settings, save_settings, autodetect_orca,
  pick_orca_executable, pick_workspace, load_xyz_file, load_inp_file, load_choices,
  parse_out_file, build_inp_preview,
  add_calc, remove_calc, clear_queue, get_queue, get_log,
  run_queue, cancel_queue,
  get_server_status, start_server, stop_server
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSlot
from PyQt6.QtWidgets import QFileDialog

from ..config import Settings, autodetect_orca
from ..paths import APP_VERSION, APP_AUTHOR, APP_ORG, APP_EMAIL
from ..core.input_generator import StepConfig, build_input_template
from ..core.queue import GeometrySource, CalcState
from ..core.parser import parse_file
from ..server.store import QueueStore, calc_from_dict, make_engine_factory, load_choice_groups


class Bridge(QObject):
    def __init__(self, parent_window, store: QueueStore, server_ctl=None):
        super().__init__()
        self.window = parent_window
        self.settings = Settings.load()
        self.store = store            # shared with the HTTP server
        self.server_ctl = server_ctl  # ServerController (may be None)

    # --- about / metadata ---
    @pyqtSlot(result=str)
    def get_about(self) -> str:
        return json.dumps({
            "version": APP_VERSION,
            "author": APP_AUTHOR,
            "org": APP_ORG,
            "email": APP_EMAIL,
        })

    # --- settings ---
    @pyqtSlot(result=str)
    def get_settings(self) -> str:
        return json.dumps({
            "orca_path": self.settings.orca_path,
            "workspace_root": self.settings.workspace_root,
            "default_nprocs": self.settings.default_nprocs,
            "default_maxcore_mb": self.settings.default_maxcore_mb,
            "theme": self.settings.theme,
            "eta_mode": self.settings.eta_mode,
            "geo_graph_mode": self.settings.geo_graph_mode,
            "build_mode": self.settings.build_mode,
            "orca_valid": self.settings.orca_is_valid(),
        })

    @pyqtSlot(str, result=str)
    def save_settings(self, payload_json: str) -> str:
        try:
            data = json.loads(payload_json or "{}")
            for k in ("orca_path", "workspace_root", "theme"):
                if k in data:
                    setattr(self.settings, k, data[k])
            for k in ("default_nprocs", "default_maxcore_mb"):
                if k in data:
                    setattr(self.settings, k, int(data[k]))
            # opt-ETA mode: only accept known values
            if "eta_mode" in data and data["eta_mode"] in ("conservative", "eager"):
                self.settings.eta_mode = data["eta_mode"]
            # optimization graph mode: only accept known values
            if "geo_graph_mode" in data and data["geo_graph_mode"] in ("all5", "maxgrad"):
                self.settings.geo_graph_mode = data["geo_graph_mode"]
            # build-tab mode: only accept known values
            if "build_mode" in data and data["build_mode"] in ("beginner", "expert"):
                self.settings.build_mode = data["build_mode"]
            self.settings.save()
            return self.get_settings()
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            return json.dumps({"error": str(e)})

    @pyqtSlot(result=str)
    def autodetect_orca(self) -> str:
        path = autodetect_orca()
        if path:
            self.settings.orca_path = path
            self.settings.save()
        return path

    # --- file pickers ---
    @pyqtSlot(result=str)
    def pick_orca_executable(self) -> str:
        filt = "ORCA executable (orca.exe);;All files (*.*)"
        path, _ = QFileDialog.getOpenFileName(self.window, "Locate orca executable", "", filt)
        return path or ""

    @pyqtSlot(result=str)
    def pick_workspace(self) -> str:
        path = QFileDialog.getExistingDirectory(self.window, "Select workspace folder")
        return path or ""

    @pyqtSlot(result=str)
    def load_xyz_file(self) -> str:
        path, _ = QFileDialog.getOpenFileName(
            self.window, "Load .xyz file", "", "XYZ file (*.xyz);;All files (*.*)"
        )
        if not path:
            return ""
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError:
            return ""

    @pyqtSlot(result=str)
    def load_inp_file(self) -> str:
        """Pick a complete ORCA .inp and return its text (used by expert/raw mode)."""
        path, _ = QFileDialog.getOpenFileName(
            self.window, "Load ORCA .inp file", "", "ORCA input (*.inp);;All files (*.*)"
        )
        if not path:
            return ""
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError:
            return ""

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
            return "{}"
        return self._parse_path(path)

    @pyqtSlot(str, result=str)
    def parse_out_path(self, path: str) -> str:
        """Parse a specific .out path (used to auto-load finished queue results)."""
        if not path or not Path(path).exists():
            return json.dumps({"error": "file not found"})
        return self._parse_path(path)

    def _parse_path(self, path: str) -> str:
        try:
            r = parse_file(path)
            return json.dumps({
                "summary": r.summary_rows(),
                "transitions": [
                    {"state": t.state, "ev": t.energy_ev, "nm": t.wavelength_nm, "fosc": t.fosc}
                    for t in r.transitions
                ],
                "frequencies": r.frequencies,
                "n_imaginary": r.n_imaginary,
                "mulliken": r.mulliken_charges,
                "nmr": [
                    {"idx": i, "el": el, "iso": iso, "aniso": an}
                    for (i, el, iso, an) in r.nmr_shieldings
                ],
                "neb_path": r.neb_path,
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

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
            return json.dumps({"ok": True, "text": text})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})

    # --- queue management (shared store) ---
    @pyqtSlot(str, result=str)
    def add_calc(self, calc_json: str) -> str:
        try:
            d = json.loads(calc_json)
            calc = calc_from_dict(d)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return json.dumps({"ok": False, "error": f"Invalid calculation data: {e}"})
        try:
            self.store.add(calc)
        except ValueError as e:
            return json.dumps({"ok": False, "error": str(e)})
        return json.dumps({"ok": True, "snapshot": self.store.snapshot()})

    @pyqtSlot(str, result=str)
    def remove_calc(self, name: str) -> str:
        try:
            ok = self.store.remove(name)
        except ValueError as e:
            return json.dumps({"ok": False, "error": str(e)})
        return json.dumps({"ok": ok, "snapshot": self.store.snapshot()})

    @pyqtSlot(result=str)
    def clear_queue(self) -> str:
        try:
            self.store.clear()
        except ValueError as e:
            return json.dumps({"ok": False, "error": str(e)})
        return json.dumps({"ok": True, "snapshot": self.store.snapshot()})

    @pyqtSlot(int, int, result=str)
    def reorder_calc(self, from_idx: int, to_idx: int) -> str:
        try:
            self.store.reorder(int(from_idx), int(to_idx))
        except ValueError as e:
            return json.dumps({"ok": False, "error": str(e)})
        return json.dumps({"ok": True, "snapshot": self.store.snapshot()})

    @pyqtSlot(str, str, result=str)
    def update_calc(self, old_name: str, calc_json: str) -> str:
        """Edit a pending calc in place (preserves its queue position)."""
        try:
            d = json.loads(calc_json)
            calc = calc_from_dict(d)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return json.dumps({"ok": False, "error": f"Invalid calculation data: {e}"})
        try:
            self.store.replace(old_name, calc)
        except ValueError as e:
            return json.dumps({"ok": False, "error": str(e)})
        return json.dumps({"ok": True, "snapshot": self.store.snapshot()})

    @pyqtSlot(result=str)
    def get_queue(self) -> str:
        return json.dumps(self.store.snapshot())

    @pyqtSlot(int, result=str)
    def get_log(self, since: int) -> str:
        return json.dumps(self.store.log_since(since))

    # --- run / cancel ---
    @pyqtSlot(result=str)
    def get_free_energy_profile(self) -> str:
        """Gibbs free energies of finished calculations, in queue order, for the
        free-energy profile. Only calcs that have a Gibbs value (i.e. a frequency
        run) are included. Energies are absolute Hartree; the UI picks a
        reference and converts to relative kcal/mol or kJ/mol."""
        pts = []
        for c in self.store.list():
            if c.state != CalcState.DONE or not c.result:
                continue
            g = getattr(c.result, "gibbs_eh", None)
            if g is None:
                continue
            pts.append({
                "name": c.name,
                "gibbs_eh": g,
                "final_energy_eh": getattr(c.result, "final_energy_eh", None),
                "kind": c.kind,
            })
        return json.dumps({"ok": True, "points": pts})

    @pyqtSlot(result=str)
    def check_overwrite_conflicts(self) -> str:
        """Return the names of queued calculations that would overwrite an
        existing result on disk (a {name}.out already in the workspace), so the
        UI can warn before a run clobbers earlier work. DONE calcs are excluded
        because the run loop skips them anyway."""
        try:
            ws = Path(self.settings.workspace_root)
            conflicts = []
            for c in self.store.list():
                if c.state == CalcState.DONE:
                    continue
                out_path = ws / c.name / f"{c.name}.out"
                if out_path.exists():
                    conflicts.append(c.name)
            return json.dumps({"ok": True, "conflicts": conflicts})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e), "conflicts": []})

    @pyqtSlot(str, result=str)
    def run_queue(self, skip_names_json: str = "") -> str:
        if not self.settings.orca_is_valid():
            return json.dumps({"ok": False, "error": "ORCA path is not set or invalid. Check Settings."})
        # names the user chose to skip (e.g. to preserve existing results)
        try:
            skip_names = set(json.loads(skip_names_json)) if skip_names_json else set()
        except Exception:
            skip_names = set()
        # validate references resolve to existing names
        calcs = self.store.list()
        names = {c.name for c in calcs}
        for c in calcs:
            if c.geometry_source == GeometrySource.REFERENCE and c.ref_name not in names:
                return json.dumps({"ok": False,
                    "error": f"'{c.name}' references '{c.ref_name}', which is not in the queue."})
        factory = make_engine_factory(self.store, self.settings.orca_path,
                                      self.settings.workspace_root, skip_names)
        try:
            self.store.start_run(factory)
        except RuntimeError as e:
            return json.dumps({"ok": False, "error": str(e)})
        except ValueError as e:
            return json.dumps({"ok": False, "error": str(e)})
        return json.dumps({"ok": True})

    @pyqtSlot(result=str)
    def cancel_queue(self) -> str:
        ok = self.store.cancel_run()
        return json.dumps({"ok": ok})

    # --- server control (phone sync) ---
    @pyqtSlot(result=str)
    def get_server_status(self) -> str:
        if not self.server_ctl:
            return json.dumps({"available": False, "running": False})
        return json.dumps({
            "available": True,
            "running": self.server_ctl.is_running(),
            "url": self.server_ctl.url(),
            "token": self.store.token,
            "clients": self.store.active_clients(),
        })

    @pyqtSlot(result=str)
    def get_connect_qr(self) -> str:
        """Return a data-URI PNG QR encoding the connect URL (address + PIN),
        so scanning it on a phone opens the UI already authorized."""
        if not self.server_ctl or not self.server_ctl.is_running():
            return json.dumps({"ok": False, "error": "Server is not running."})
        connect_url = f"{self.server_ctl.url()}/?pin={self.store.token}"
        try:
            import qrcode
            import io, base64
            img = qrcode.make(connect_url)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data = base64.b64encode(buf.getvalue()).decode("ascii")
            return json.dumps({
                "ok": True,
                "data_uri": f"data:image/png;base64,{data}",
                "url": connect_url,
            })
        except ImportError:
            return json.dumps({"ok": False, "error": "qrcode library not installed.",
                               "url": connect_url})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e), "url": connect_url})

    @pyqtSlot(result=str)
    def start_server(self) -> str:
        if not self.server_ctl:
            return json.dumps({"ok": False, "error": "Server support is not available."})
        try:
            self.server_ctl.start()
            return json.dumps({"ok": True, "url": self.server_ctl.url(),
                               "token": self.store.token})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})

    @pyqtSlot(result=str)
    def stop_server(self) -> str:
        if not self.server_ctl:
            return json.dumps({"ok": False, "error": "Server support is not available."})
        try:
            self.server_ctl.stop()
            return json.dumps({"ok": True})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})
