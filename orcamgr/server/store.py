"""
QueueStore — the single shared queue state that the PyQt app, the HTTP server,
and (through the server) the phone all read and write.

Deliberately framework-independent: no FastAPI, no PyQt imports here, so it can
be unit-tested on its own. It wraps a list of Calculation objects with a
threading.RLock so concurrent access (Qt UI thread + server worker thread) is
safe.

This is the "길 1" design we agreed on: one in-memory queue object, shared via
a lock, no SQLite.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
from typing import Optional

from ..core.queue import (
    Calculation, CalcState, GeometrySource, QueueEngine, QueueCallbacks,
    validate_result,
)
from ..core.input_generator import StepConfig
from ..core.parser import parse_file
from ..core.runner import OrcaRunError
from ..core.procutil import process_matches
from ..paths import data_dir, user_data_root

# States whose calculations the user may still edit / remove / reorder.
# PENDING: never run yet. FAILED / CANCELLED: finished unsuccessfully, so the
# user can fix and retry them. DONE is intentionally excluded (a completed
# result is frozen — make a new calculation to rerun). RUNNING/BLOCKED are
# excluded too (in-flight or dependency-gated).
EDITABLE_STATES = {CalcState.PENDING, CalcState.FAILED, CalcState.CANCELLED}


def _new_pin() -> str:
    """A fresh 6-digit access PIN (cryptographically random, zero-padded)."""
    return f"{secrets.randbelow(1_000_000):06d}"


# calc.name becomes an on-disk folder name (workspace_root / name), so it must be
# validated at this single shared serialization point — the only client-side
# guard lives in the desktop JS and is bypassed by the phone/HTTP path.
_BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|]')
_RESERVED_NAMES = {"con", "prn", "aux", "nul",
                   *(f"com{i}" for i in range(1, 10)),
                   *(f"lpt{i}" for i in range(1, 10))}


def _validate_calc_name(name: str) -> None:
    """Reject names that could escape the workspace or break on Windows.
    Allows Unicode (e.g. Korean) — only path-dangerous patterns are blocked."""
    if _BAD_NAME_CHARS.search(name):
        raise ValueError('Name contains characters not allowed in a folder name: \\ / : * ? " < > |')
    if ".." in name:
        raise ValueError("Name must not contain '..'.")
    if name.endswith("."):
        raise ValueError("Name must not end with a dot.")
    if name.split(".")[0].lower() in _RESERVED_NAMES:
        raise ValueError(f"'{name}' uses a name reserved by Windows.")


def _flatten_choices(value) -> list:
    """Flatten a choices JSON (dict of categories, or list) into a flat list,
    skipping metadata keys that start with '_'."""
    out = []
    if isinstance(value, list):
        out.extend(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            if k.startswith("_"):
                continue
            if isinstance(v, list):
                out.extend(v)
            elif isinstance(v, dict):
                if "keywords" in v and isinstance(v["keywords"], list):
                    out.extend(v["keywords"])
                elif "aliases" in v and isinstance(v["aliases"], list):
                    out.extend(v["aliases"])
                else:
                    out.extend(_flatten_choices(v))
    seen = set()
    flat = []
    for item in out:
        if isinstance(item, str) and item not in seen:
            seen.add(item)
            flat.append(item)
    return flat


def load_all_choices() -> dict:
    """Read every data/*.json and return {name: [flat list of options]}."""
    names = ["functionals", "basis_sets", "scf_convergences",
             "ri_approximations", "solvents", "calculation_types", "options"]
    result = {}
    for name in names:
        path = data_dir() / f"{name}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            result[name] = _flatten_choices(data)
        except (OSError, json.JSONDecodeError):
            result[name] = []
    return result


def load_choice_groups(name: str) -> dict:
    """Read one data/<name>.json and return it grouped: {category: [items]}.

    Used by the desktop bridge (which shows grouped dropdowns). Shares the same
    flatten rules as load_all_choices so PC and phone always see identical
    options. Metadata keys (leading '_') are skipped.
    """
    path = data_dir() / f"{name}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    groups: dict = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if k.startswith("_"):
                continue
            items = _flatten_choices(v if isinstance(v, (list, dict)) else [])
            if items:
                groups[k] = items
    elif isinstance(data, list):
        flat = _flatten_choices(data)
        if flat:
            groups["all"] = flat
    return groups


def calc_to_dict(c: Calculation) -> dict:
    """Serialize a Calculation to a plain dict for JSON responses."""
    return {
        "name": c.name,
        "kind": c.kind,
        "charge": c.charge,
        "multiplicity": c.multiplicity,
        "geometry_source": c.geometry_source.value
        if isinstance(c.geometry_source, GeometrySource) else str(c.geometry_source),
        "ref_name": c.ref_name,
        "is_raw": c.is_raw,
        "state": c.state.value if isinstance(c.state, CalcState) else str(c.state),
        "message": c.message,
        "output_path": c.output_path,
        # SCF convergence setting (used by the live graph to place the target line)
        "scf_convergence": getattr(c.config, "scf_convergence", "") if c.config else "",
        # a compact one-line summary for list rows
        "meta": _meta_line(c),
    }


def _meta_line(c: Calculation) -> str:
    if c.geometry_source == GeometrySource.REFERENCE or c.geometry_source == "reference":
        src = f"ref {c.ref_name}"
    else:
        src = ".xyz"
    return f"{c.kind} · {src} · q{c.charge} m{c.multiplicity}"


def calc_from_dict(d: dict) -> Calculation:
    """
    Build a Calculation from a client payload dict (used by both the HTTP
    server and the Qt bridge). PyQt-independent so it lives here.
    """
    cfg = StepConfig.from_dict(d.get("config", {}))
    src = d.get("geometry_source", "direct")
    name = d.get("name", "").strip()
    if not name:
        raise ValueError("Calculation name is required.")
    _validate_calc_name(name)
    return Calculation(
        name=name,
        kind=d.get("kind", cfg.kind),
        config=cfg,
        charge=int(d.get("charge", 0)),
        multiplicity=int(d.get("multiplicity", 1)),
        geometry_source=GeometrySource(src),
        xyz=d.get("xyz", ""),
        ref_name=d.get("ref_name", ""),
        is_raw=bool(d.get("is_raw", False)),
        raw_text=d.get("raw_text", ""),
    )


# ---- session persistence (autosave / restore) --------------------------
# The whole queue is mirrored to a JSON file so closing ORCAdesk does not lose
# it, and a calculation left RUNNING (its detached ORCA still going) can be
# reattached on the next launch.

def _session_file():
    return user_data_root() / "session.json"


def calc_to_session_dict(c: Calculation) -> dict:
    """Full-fidelity serialization of a Calculation for the session file
    (unlike calc_to_dict, which is the compact UI snapshot)."""
    gs = c.geometry_source.value if isinstance(c.geometry_source, GeometrySource) else str(c.geometry_source)
    st = c.state.value if isinstance(c.state, CalcState) else str(c.state)
    return {
        "name": c.name,
        "kind": c.kind,
        "config": c.config.to_dict() if c.config else {},
        "charge": c.charge,
        "multiplicity": c.multiplicity,
        "geometry_source": gs,
        "xyz": c.xyz,
        "ref_name": c.ref_name,
        "is_raw": c.is_raw,
        "raw_text": c.raw_text,
        "state": st,
        "message": c.message,
        "output_path": c.output_path,
        "pid": c.pid,
        "create_time": c.create_time,
    }


def calc_from_session_dict(d: dict) -> Calculation:
    """Rebuild a Calculation from the session file, restoring runtime state
    (state/message/output_path/pid) on top of calc_from_dict."""
    c = calc_from_dict(d)            # config + geometry + name validation
    st = d.get("state", "pending")
    try:
        c.state = CalcState(st)
    except ValueError:
        c.state = CalcState.PENDING
    c.message = d.get("message", "")
    c.output_path = d.get("output_path", "") or ""
    c.pid = d.get("pid")
    c.create_time = d.get("create_time")
    return c


def _parse_if_exists(path: str):
    from pathlib import Path
    if path and Path(path).exists():
        try:
            return parse_file(path)
        except Exception:
            return None
    return None


def reconcile_calcs(calcs: "list[Calculation]") -> None:
    """Square a freshly loaded session with reality:

    * A calc persisted as RUNNING whose process is genuinely still alive keeps
      its RUNNING state (it will be reattached when the queue resumes).
    * A calc persisted as RUNNING whose process is gone is judged from its .out:
      terminated normally + valid -> DONE, otherwise FAILED (interrupted).

    DONE calcs are deliberately NOT re-parsed here — their ParseResult isn't
    persisted, but the only consumers (geometry references in QueueEngine, and
    the free-energy profile) parse it on demand, so eagerly reading every DONE
    .out on the UI thread before the window paints would be a pure startup stall
    that scales with the restored queue size and .out file size.
    """
    for c in calcs:
        if c.state == CalcState.RUNNING:
            if c.pid and process_matches(c.pid, c.create_time):
                continue  # genuinely still running — reattach on resume
            c.pid = None
            c.create_time = None
            r = _parse_if_exists(c.output_path)
            if r is not None and r.terminated_normally:
                c.result = r
                try:
                    validate_result(c, r)
                    c.state = CalcState.DONE
                    c.message = "Completed (finished while ORCAdesk was closed)."
                except OrcaRunError as e:
                    c.state = CalcState.FAILED
                    c.message = str(e)
            else:
                c.state = CalcState.FAILED
                c.message = "Interrupted while ORCAdesk was closed."


class QueueStore:
    """Thread-safe container for the calculation queue + run state + log buffer."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._calcs: list[Calculation] = []
        self._running = False
        # monotonically increasing version, bumped on every mutation so clients
        # can cheaply poll "did anything change?"
        self._version = 0
        # log buffer: list of (seq, level, message); clients poll with ?since=
        self._log: list[tuple[int, str, str]] = []
        self._log_seq = 0
        # the background engine + thread while a run is in progress
        self._engine = None
        self._thread: Optional[threading.Thread] = None
        # access token (6-digit PIN), generated fresh per server start
        self._token = _new_pin()
        # connected phone clients: {client_id: last_seen_epoch}
        self._clients: dict[str, float] = {}
        self._client_ttl = 15.0   # seconds without heartbeat = considered gone

    # ---- auth token ----
    @property
    def token(self) -> str:
        with self._lock:
            return self._token

    def regenerate_token(self) -> str:
        with self._lock:
            self._token = _new_pin()
            return self._token

    def check_token(self, supplied: Optional[str]) -> bool:
        """Constant-time-ish comparison of a supplied token against the PIN."""
        if not supplied:
            return False
        import hmac
        with self._lock:
            return hmac.compare_digest(str(supplied), self._token)

    # ---- connected clients (phones) ----
    def heartbeat(self, client_id: str) -> None:
        """Record that a client is alive right now."""
        import time
        if not client_id:
            return
        with self._lock:
            self._clients[client_id] = time.time()

    def active_clients(self) -> int:
        """Number of clients seen within the TTL window (prunes stale ones)."""
        import time
        now = time.time()
        with self._lock:
            stale = [cid for cid, t in self._clients.items()
                     if now - t > self._client_ttl]
            for cid in stale:
                del self._clients[cid]
            return len(self._clients)

    # ---- reads ----
    def snapshot(self) -> dict:
        """Full state for a client (queue list + running flag + version)."""
        with self._lock:
            return {
                "running": self._running,
                "version": self._version,
                "calculations": [calc_to_dict(c) for c in self._calcs],
            }

    def version(self) -> int:
        with self._lock:
            return self._version

    def names(self) -> list[str]:
        with self._lock:
            return [c.name for c in self._calcs]

    def get(self, name: str) -> Optional[Calculation]:
        with self._lock:
            for c in self._calcs:
                if c.name == name:
                    return c
        return None

    # ---- mutations ----
    def add(self, calc: Calculation) -> None:
        """Append a calculation. Raises ValueError on duplicate name."""
        with self._lock:
            if self._running:
                # the engine runs a frozen snapshot of the queue; a calc added
                # mid-run would never execute, so the visible and executing
                # queues would silently diverge.
                raise ValueError("Cannot add to the queue while it is running.")
            if any(c.name == calc.name for c in self._calcs):
                raise ValueError(f"A calculation named '{calc.name}' already exists.")
            self._calcs.append(calc)
            self._bump_and_save()

    def remove(self, name: str) -> bool:
        with self._lock:
            if self._running:
                # the engine runs a frozen snapshot; removing any calc mid-run
                # would diverge the visible queue from the executing one (same
                # reason add/replace/reorder/clear are blocked while running).
                raise ValueError("Cannot remove from the queue while it is running.")
            for i, c in enumerate(self._calcs):
                if c.name == name:
                    # belt-and-suspenders: never remove the in-flight calc
                    if c.state == CalcState.RUNNING:
                        raise ValueError("Cannot remove a running calculation.")
                    del self._calcs[i]
                    self._bump_and_save()
                    return True
        return False

    def clear(self) -> None:
        with self._lock:
            if self._running:
                raise ValueError("Cannot clear the queue while it is running.")
            self._calcs.clear()
            self._bump_and_save()

    def replace(self, name: str, new_calc: Calculation) -> bool:
        """Replace an editable calculation in place (keeps its queue position).
        Editable = pending, failed, or cancelled. Editing resets the entry to
        PENDING (and clears the old result/message) so it runs on the next Run.
        Raises ValueError if the target isn't editable, or on a name clash."""
        with self._lock:
            if self._running:
                raise ValueError("Cannot edit a calculation while the queue is running.")
            idx = None
            for i, c in enumerate(self._calcs):
                if c.name == name:
                    idx = i
                    break
            if idx is None:
                raise ValueError(f"No calculation named '{name}'.")
            if self._calcs[idx].state not in EDITABLE_STATES:
                raise ValueError("Only pending, failed, or cancelled calculations can be edited.")
            # if renaming, the new name must not collide with a DIFFERENT entry
            if new_calc.name != name and any(
                c.name == new_calc.name for j, c in enumerate(self._calcs) if j != idx
            ):
                raise ValueError(f"A calculation named '{new_calc.name}' already exists.")
            # a freshly edited calc is always pending again, with no stale result
            new_calc.state = CalcState.PENDING
            new_calc.message = ""
            new_calc.result = None
            new_calc.output_path = ""
            self._calcs[idx] = new_calc
            self._bump_and_save()
            return True

    def reorder(self, from_idx: int, to_idx: int) -> bool:
        """Move an editable calculation to a new position. Both endpoints must
        be editable (pending/failed/cancelled) so running/done items keep their
        place. Returns True on move."""
        with self._lock:
            if self._running:
                raise ValueError("Cannot reorder while the queue is running.")
            n = len(self._calcs)
            if not (0 <= from_idx < n) or not (0 <= to_idx < n):
                raise ValueError("Index out of range.")
            if from_idx == to_idx:
                return False
            if self._calcs[from_idx].state not in EDITABLE_STATES:
                raise ValueError("Only pending, failed, or cancelled calculations can be moved.")
            if self._calcs[to_idx].state not in EDITABLE_STATES:
                raise ValueError("Can only reorder within editable calculations.")
            item = self._calcs.pop(from_idx)
            self._calcs.insert(to_idx, item)
            self._bump_and_save()
            return True

    def list(self) -> list[Calculation]:
        with self._lock:
            return list(self._calcs)

    # ---- run flag ----
    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def set_running(self, value: bool) -> None:
        with self._lock:
            self._running = bool(value)
            self._bump_and_save()

    def touch(self) -> None:
        """Bump version, e.g. after the engine mutates a calc's state in place."""
        with self._lock:
            self._bump_and_save()

    # ---- log buffer ----
    def append_log(self, message: str, level: str = "info") -> None:
        with self._lock:
            self._log_seq += 1
            self._log.append((self._log_seq, level, message))
            # cap buffer so a long run doesn't grow memory without bound
            if len(self._log) > 5000:
                self._log = self._log[-4000:]

    def log_since(self, since: int = 0) -> dict:
        """Return log lines with seq > since, plus the latest seq."""
        with self._lock:
            lines = [
                {"seq": s, "level": lv, "msg": m}
                for (s, lv, m) in self._log if s > since
            ]
            return {"lines": lines, "latest": self._log_seq}

    def clear_log(self) -> None:
        with self._lock:
            self._log.clear()
            self._log_seq = 0

    # ---- run management ----
    def start_run(self, engine_factory) -> None:
        """
        Start running the queue in a background thread.

        engine_factory() must return a QueueEngine already wired with callbacks
        that update THIS store (log -> append_log, calc_update -> touch). We
        keep store framework-agnostic by having the caller build the engine.

        Raises RuntimeError if a run is already in progress, or ValueError if
        the queue is empty.
        """
        with self._lock:
            if self._running:
                raise RuntimeError("A run is already in progress.")
            if not self._calcs:
                raise ValueError("The queue is empty.")
            calcs = list(self._calcs)
            engine = engine_factory()
            self._engine = engine
            self._running = True
            self._bump_and_save()

        def _worker():
            try:
                engine.run_all(calcs)
            except Exception as e:  # engine should handle most, but be safe
                self.append_log(f"Run aborted: {e}", "err")
            finally:
                with self._lock:
                    self._running = False
                    self._engine = None
                    self._thread = None
                    self._bump_and_save()
                self.append_log("Queue finished.", "info")

        t = threading.Thread(target=_worker, name="orcadesk-run", daemon=True)
        with self._lock:
            self._thread = t
        t.start()

    def cancel_run(self) -> bool:
        """Signal the running engine to cancel. Returns False if nothing runs."""
        with self._lock:
            if not self._running or self._engine is None:
                return False
            engine = self._engine
        # call cancel outside the lock (it may touch the runner/subprocess)
        engine.cancel()
        self.append_log("Cancellation requested...", "info")
        return True

    def request_stop_after_current(self) -> bool:
        """Ask the running engine to stop AFTER the current job finishes (a
        graceful drain). The in-flight job is left to complete; remaining calcs
        stay PENDING. Returns False if nothing is running."""
        with self._lock:
            if not self._running or self._engine is None:
                return False
            engine = self._engine
        engine.request_stop_after_current()
        self.append_log("Will stop after the current job finishes...", "info")
        return True

    def pause_run(self) -> bool:
        """Stop processing the queue WITHOUT killing the in-flight ORCA — used on
        app shutdown so the running calculation survives (its detached ORCA keeps
        going) and can be reattached on the next launch. Returns False if nothing
        is running."""
        with self._lock:
            if not self._running or self._engine is None:
                return False
            engine = self._engine
        engine.detach()
        return True

    def wait_for_run(self, timeout: "float | None" = None) -> bool:
        """Block until the run worker thread finishes (or timeout elapses).
        Returns True if no run is in progress afterwards. Used on app shutdown so
        we don't orphan orca.exe / leave a half-written .out behind."""
        with self._lock:
            t = self._thread
        if t is None:
            return True
        t.join(timeout)
        return not t.is_alive()

    # ---- session persistence ----
    def _bump_and_save(self) -> None:
        """Bump the change version AND autosave the queue. Replaces the bare
        version increments so every mutation is persisted. The caller holds the
        lock; the RLock is reentrant, so save_session re-acquiring is fine."""
        self._version += 1
        self.save_session()

    def save_session(self) -> None:
        """Persist the full queue to the session file (atomic replace). Best-
        effort: a save failure must never break the running app."""
        with self._lock:
            payload = {
                "schema": 1,
                "calculations": [calc_to_session_dict(c) for c in self._calcs],
            }
        try:
            path = _session_file()
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass

    def load_session(self) -> None:
        """Restore the queue from the session file and reconcile it with reality
        (see reconcile_calcs). Call once at startup. No-op if missing/unreadable."""
        path = _session_file()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        restored = []
        for d in data.get("calculations", []):
            try:
                restored.append(calc_from_session_dict(d))
            except Exception:
                continue  # skip a corrupt entry rather than lose the whole queue
        if not restored:
            return
        reconcile_calcs(restored)
        with self._lock:
            self._calcs = restored
            self._version += 1

    def has_live_running(self) -> bool:
        """True if a calculation is still RUNNING after reconciliation — i.e. a
        detached ORCA survived a previous session and should be reattached."""
        with self._lock:
            return any(c.state == CalcState.RUNNING for c in self._calcs)


def make_engine_factory(store: "QueueStore", orca_path: str, workspace_root: str,
                        skip_names: "set[str] | None" = None):
    """
    Returns a zero-arg factory that builds a QueueEngine whose callbacks feed
    the given store (log buffer + version bumps). Used by start_run().

    skip_names: calculations the user chose not to run (e.g. to avoid
    overwriting existing results on disk).
    """
    skip = set(skip_names or ())

    def factory() -> QueueEngine:
        cb = QueueCallbacks(
            log=lambda msg, level: store.append_log(msg, level),
            calc_update=lambda i, c: store.touch(),
        )
        return QueueEngine(orca_path, workspace_root, cb, skip_names=skip)
    return factory
