"""
QWebChannel bridge between the HTML/JS front-end and the Python core.

Stage 3 change: the queue now lives in a shared QueueStore (the same object the
HTTP server uses), instead of being owned by the JS side. The desktop UI reads
the queue/log by polling cheap @pyqtSlot getters (get_queue / get_log) on a
QTimer in JS — this keeps the store's worker thread and Qt's UI thread cleanly
separated (no cross-thread Qt signal juggling).

JS calls these slots:
  get_about, get_settings, save_settings, autodetect_orca,
  pick_orca_executable, pick_workspace, load_xyz_file, load_inp_file,
  load_inp_path, load_xyz_path, load_choices,
  parse_out_file, build_inp_preview,
  add_calc, remove_calc, clear_queue, get_queue, get_calc, get_inp, get_log, get_graph_lines,
  run_queue, cancel_queue, stop_after_current,
  get_server_status, start_server, stop_server
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSlot
from PyQt6.QtWidgets import QFileDialog

from ..config import Settings, autodetect_orca
from ..paths import APP_VERSION, APP_AUTHOR, APP_ORG, APP_EMAIL
from ..core.input_generator import StepConfig, build_input_template
from ..core.queue import GeometrySource, CalcState
from ..core.parser import parse_file
from ..server.store import (
    QueueStore, calc_from_dict, calc_to_session_dict, make_engine_factory, load_choice_groups,
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
# flips to 100% / "running frequencies…" (mirror of scf_graph.js OPT_DONE_RE / POST_OPT_RE)
_G_DONE = re.compile(r"\*\*\*\s*OPTIMIZATION RUN DONE\s*\*\*\*|THE OPTIMIZATION HAS CONVERGED|HURRAY", re.I)
_G_POST = re.compile(
    r"VIBRATIONAL FREQUENCIES|ORCA SCF RESPONSE|GEOMETRIC PERTURBATIONS|CP-?SCF DRIVER|SCF HESSIAN|ANALYTICAL FREQUENCIES|NUMERICAL FREQUENCIES", re.I)


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
        """Pick a complete ORCA .inp; return its text PLUS the file's base name
        so expert/raw mode can auto-fill the calculation name. JSON:
        {"text": "<.inp contents>", "name": "<filename without .inp>"}."""
        path, _ = QFileDialog.getOpenFileName(
            self.window, "Load ORCA .inp file", "", "ORCA input (*.inp);;All files (*.*)"
        )
        if not path:
            return json.dumps({"text": "", "name": ""})
        try:
            p = Path(path)
            return json.dumps({"text": p.read_text(encoding="utf-8"), "name": p.stem})
        except OSError:
            return json.dumps({"text": "", "name": ""})

    @pyqtSlot(str, result=str)
    def load_inp_path(self, path: str) -> str:
        """Load a .inp by PATH (drag-and-drop). Same {text, name} shape as
        load_inp_file, so the JS side is identical."""
        try:
            p = Path(path)
            return json.dumps({"text": p.read_text(encoding="utf-8", errors="replace"), "name": p.stem})
        except OSError as e:
            return json.dumps({"text": "", "name": "", "error": str(e)})

    @pyqtSlot(str, result=str)
    def load_xyz_path(self, path: str) -> str:
        """Load a .xyz by PATH (drag-and-drop). Returns raw text like load_xyz_file."""
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
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

    @pyqtSlot(str, result=str)
    def get_calc(self, name: str) -> str:
        """Return the FULL data (config / xyz / raw_text / charge / ...) for one
        calculation, so it can be edited even when it isn't in this session's
        in-memory copy — e.g. a calc restored from a previous session or added
        from the phone (the polled snapshot omits these fields)."""
        c = self.store.get(name)
        if c is None:
            return json.dumps({"ok": False, "error": "not found"})
        return json.dumps({"ok": True, "calc": calc_to_session_dict(c)})

    @pyqtSlot(int, result=str)
    def get_log(self, since: int) -> str:
        return json.dumps(self.store.log_since(since))

    @pyqtSlot(str, result=str)
    def get_inp(self, name: str) -> str:
        """Return the on-disk ORCA .inp for a calculation, so a running or finished
        job's actual input can be viewed read-only (not only editable ones)."""
        try:
            p = Path(self.settings.workspace_root) / name / f"{name}.inp"
            if not p.exists():
                return json.dumps({"ok": False, "error": "no input on disk yet"})
            return json.dumps({"ok": True, "text": p.read_text(encoding="utf-8", errors="replace")})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})

    @pyqtSlot(str, result=str)
    def get_graph_lines(self, name: str) -> str:
        """Return ONLY the SCF-iteration / geometry-convergence lines of a
        calculation's .out, in file order, so the UI can rebuild the full live
        SCF/optimization graph history — independent of the capped log buffer.

        Takes the calc NAME (a still-RUNNING calc has no output_path yet) and
        resolves {workspace}/{name}/{name}.out server-side. Reads line-by-line
        and keeps a few hundred relevant lines even from a huge .out."""
        try:
            out_path = Path(self.settings.workspace_root) / name / f"{name}.out"
            if not out_path.exists():
                return json.dumps({"ok": False, "error": "no output", "lines": []})
            lines = []
            in_table = False
            saw_item = False
            with open(out_path, "r", encoding="utf-8", errors="replace") as f:
                for raw in f:
                    ln = raw.rstrip("\r\n")
                    if _G_CYCLE.search(ln):
                        lines.append(ln)
                    elif _G_TABLE.search(ln):
                        in_table = True
                        saw_item = False
                        lines.append(ln)
                    elif in_table and _G_ITEM.search(ln):
                        saw_item = True
                        lines.append(ln)
                    elif in_table and saw_item and (
                            ln.strip() == "" or _G_DASHES.search(ln) or _G_DOTS.search(ln)):
                        # table terminator — kept so GeoTracker commits the step
                        lines.append(ln)
                        in_table = False
                        saw_item = False
                    elif _G_ITER.match(ln):
                        lines.append(ln)
            return json.dumps({"ok": True, "lines": lines})
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e), "lines": []})

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
            if not c.result and c.output_path:
                try:
                    c.result = parse_file(c.output_path)
                except Exception:
                    pass
            if not c.result:
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

    @pyqtSlot(result=str)
    def stop_after_current(self) -> str:
        """Graceful drain: finish the running job, then stop; leave the rest
        pending (as opposed to cancel_queue, which kills the running job)."""
        ok = self.store.request_stop_after_current()
        return json.dumps({"ok": ok})

    def resume_session_if_running(self) -> None:
        """Startup hook (not a JS slot): if a calculation from the previous
        session is still running, reattach to it and continue the queue. The
        store has already restored + reconciled the queue in load_session()."""
        if not self.store.has_live_running():
            return
        if not self.settings.orca_is_valid():
            self.store.append_log(
                "A calculation from the previous session is still running, but the "
                "ORCA path is invalid — cannot reattach. Fix it in Settings.", "warn")
            return
        factory = make_engine_factory(self.store, self.settings.orca_path,
                                      self.settings.workspace_root)
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
