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
import secrets
import threading
from typing import Optional

from ..core.queue import Calculation, CalcState, GeometrySource, QueueEngine, QueueCallbacks
from ..core.input_generator import StepConfig
from ..paths import data_dir

# States whose calculations the user may still edit / remove / reorder.
# PENDING: never run yet. FAILED / CANCELLED: finished unsuccessfully, so the
# user can fix and retry them. DONE is intentionally excluded (a completed
# result is frozen — make a new calculation to rerun). RUNNING/BLOCKED are
# excluded too (in-flight or dependency-gated).
EDITABLE_STATES = {CalcState.PENDING, CalcState.FAILED, CalcState.CANCELLED}


def _new_pin() -> str:
    """A fresh 6-digit access PIN (cryptographically random, zero-padded)."""
    return f"{secrets.randbelow(1_000_000):06d}"


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
            if any(c.name == calc.name for c in self._calcs):
                raise ValueError(f"A calculation named '{calc.name}' already exists.")
            self._calcs.append(calc)
            self._version += 1

    def remove(self, name: str) -> bool:
        with self._lock:
            for i, c in enumerate(self._calcs):
                if c.name == name:
                    # don't allow removing something currently running
                    if c.state == CalcState.RUNNING:
                        raise ValueError("Cannot remove a running calculation.")
                    del self._calcs[i]
                    self._version += 1
                    return True
        return False

    def clear(self) -> None:
        with self._lock:
            if self._running:
                raise ValueError("Cannot clear the queue while it is running.")
            self._calcs.clear()
            self._version += 1

    def replace(self, name: str, new_calc: Calculation) -> bool:
        """Replace an editable calculation in place (keeps its queue position).
        Editable = pending, failed, or cancelled. Editing resets the entry to
        PENDING (and clears the old result/message) so it runs on the next Run.
        Raises ValueError if the target isn't editable, or on a name clash."""
        with self._lock:
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
            self._version += 1
            return True

    def reorder(self, from_idx: int, to_idx: int) -> bool:
        """Move an editable calculation to a new position. Both endpoints must
        be editable (pending/failed/cancelled) so running/done items keep their
        place. Returns True on move."""
        with self._lock:
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
            self._version += 1
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
            self._version += 1

    def touch(self) -> None:
        """Bump version, e.g. after the engine mutates a calc's state in place."""
        with self._lock:
            self._version += 1

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
            self._version += 1

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
                    self._version += 1
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
