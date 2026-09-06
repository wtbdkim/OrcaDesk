"""
QueueStore — the single shared queue state that the PyQt app, the HTTP server,
and (through the server) the phone all read and write.

Deliberately framework-independent: no FastAPI, no PyQt imports here, so it can
be unit-tested on its own. It wraps a list of Calculation objects with a
threading.RLock so concurrent access (Qt UI thread + server worker thread) is
safe.

This is the option-1 design we agreed on: one in-memory queue object, shared
via a lock, no SQLite.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
from typing import Optional

from ..core.queue import (
    KNOWN_KINDS, Calculation, CalcState, GeometrySource, QueueEngine,
    QueueCallbacks, result_from_output, validate_result,
)
from ..core.input_generator import StepConfig
from ..core.runner import OrcaRunError
from ..core.procutil import process_matches
from ..paths import data_dir, user_data_root
# Wire-payload shapes (plain dicts at runtime; see schemas.py for the mirror
# contract with web/types.js)
from .schemas import (CalcFull, CalcSummary, LogLine, LogPayload,
                      QueueSnapshot, RunResources)

# States whose calculations the user may still edit / reorder.
# PENDING: never run yet. CANCELLED: a deliberate user stop, not a failure, so
# it can be fixed and retried. BLOCKED: must stay editable because after its
# failed parent is removed the user has to re-point the reference at another
# calculation — locking it would strand it forever. FAILED is deliberately NOT
# editable (P24 revision): a failure locks the calc at the moment it fails —
# no edit, no re-run; the only remaining interaction is removal (×). DONE is
# excluded too (a completed result is frozen — make a new calculation to
# rerun), and RUNNING is in flight.
EDITABLE_STATES = {CalcState.PENDING, CalcState.CANCELLED, CalcState.BLOCKED}


def _new_pin() -> str:
    """A fresh 6-digit access PIN (cryptographically random, zero-padded)."""
    return f"{secrets.randbelow(1_000_000):06d}"


# calc.name becomes an on-disk folder name (workspace_root / name), so it must be
# validated at this single shared serialization point — the only client-side
# guard lives in the desktop JS and is bypassed by the phone/HTTP path.
_BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|]')
# ...and it is also what ORCA is invoked with. ORCA 6 splits its own argument on
# whitespace before handing it to orca_startup, so a space or an '&' in the file
# name makes every run of that calculation die in Startup ("Cannot open input
# file <first word>" / "no input files") — a FAILED calc, and FAILED is locked
# (P24), so the name can never be run again. The FOLDER may contain spaces —
# OrcaRunner.launch passes the bare file name from inside the folder, which is
# what makes a workspace under "C:\Users\John Smith" work.
#
# ',', ';' and '=' were measured as harmless on 6.1.1 and used to be allowed
# here, but that measurement only covered the argument ORCA parses ITSELF. ORCA
# also shells out to helper binaries and collects their output through a
# redirection it does NOT quote:
#     otool_gcp "<name>.gcp.in.tmp" -level R2SCAN3C > <name>.gcp.out
# cmd.exe ends a redirection target at its token delimiters — space, tab, ',',
# ';', '=' — so under a name like "(S,S)mol" gCP's result lands in a file called
# "(S", ORCA never finds "<name>.gcp.out", and the run dies with "Error
# (ORCA_SCF/GCP/Energy): Calculation of the gCP correction failed!" after the
# SCF is already paid for. Only the gCP-carrying composite methods hit it
# (r2SCAN-3c, B97-3c, HF-3c, PBEh-3c, and an explicit ! GCP(...)), which is why
# a plain functional/basis run hid this for so long. '(', ')' and "'" survive
# that parse and stay legal.
_ORCA_HOSTILE_CHARS = re.compile(r"[\s&,;=]")
# Control characters and lone surrogates are not typeable in the desktop's
# single-line input but arrive freely through calc_from_dict (the phone/HTTP
# path, a hand-edited session.json). A control character reaches mkdir and dies
# with a localized WinError 123, which stamps the calc FAILED — locked (P24);
# a surrogate cannot be encoded to UTF-8 at all and used to poison session
# autosave for the rest of the app's life.
_UNUSABLE_NAME_CHARS = re.compile(r"[\x00-\x1f\x7f\ud800-\udfff]")
# Windows caps a path component at 255 characters, and the workspace path plus
# "{name}/{name}.inp" is spent twice over on top of it. The cap is here rather
# than at mkdir so the refusal is a sentence instead of a WinError 3.
_MAX_NAME_LEN = 120
_RESERVED_NAMES = {"con", "prn", "aux", "nul",
                   *(f"com{i}" for i in range(1, 10)),
                   *(f"lpt{i}" for i in range(1, 10))}


def _validate_calc_name(name: str, kind: str = "", restoring: bool = False) -> None:
    """Reject names that could escape the workspace or break on Windows.
    Allows Unicode (e.g. Korean) — only path-dangerous patterns are blocked.

    ``kind`` selects the ORCA-only rule below. It defaults to "" (apply it),
    because a caller that does not know the kind is building an ORCA calc or
    checking a name in the abstract, and the stricter answer is the safe one.

    ``restoring`` marks the session-restore path, where the name is a fact on
    disk rather than a proposal. The ORCA rule is a "this run will fail" rule,
    not a "this path is dangerous" rule, so applying it retroactively would
    silently EVICT calcs that predate a tightening (the ',' / ';' / '=' one
    below) from the queue at the next launch — deleting the user's record of a
    finished or failed run to enforce something only a NEW run has to obey.
    Every path-safety rule still applies; only the ORCA rule is grandfathered.
    """
    if _BAD_NAME_CHARS.search(name):
        raise ValueError('Name contains characters not allowed in a folder name: \\ / : * ? " < > |')
    # ORCA's constraint, applied where ORCA runs. The MLIP and CREST pipelines
    # pass the name as a subprocess argv element / a shell-quoted variable, so a
    # space is genuinely fine for them — and refusing it for every kind would
    # DROP an existing MLIP/CREST calculation from the queue at the next launch
    # (load_session skips entries that fail validation).
    if (not restoring and not (kind or "").startswith(("mlip", "crest"))
            and _ORCA_HOSTILE_CHARS.search(name)):
        raise ValueError("Name must not contain a space or one of & , ; = — ORCA "
                         "cannot open an input file whose name has one, and its "
                         "gCP helper writes its result to the wrong file.")
    if _UNUSABLE_NAME_CHARS.search(name):
        raise ValueError("Name contains a character that cannot be used in a "
                         "folder name.")
    if len(name) > _MAX_NAME_LEN:
        raise ValueError(f"Name is too long ({len(name)} characters); "
                         f"keep it under {_MAX_NAME_LEN}.")
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
             "ri_approximations", "solvents", "calculation_types", "options",
             "mace_models"]
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


def calc_to_dict(c: Calculation) -> CalcSummary:
    """Serialize a Calculation to a plain dict for JSON responses."""
    return CalcSummary(
        name=c.name,
        kind=c.kind,
        charge=c.charge,
        multiplicity=c.multiplicity,
        geometry_source=c.geometry_source.value
        if isinstance(c.geometry_source, GeometrySource) else str(c.geometry_source),
        ref_name=c.ref_name,
        is_raw=c.is_raw,
        state=c.state.value if isinstance(c.state, CalcState) else str(c.state),
        message=c.message,
        output_path=c.output_path,
        # SCF convergence setting (used by the live graph to place the target line)
        scf_convergence=getattr(c.config, "scf_convergence", "") if c.config else "",
        # a compact one-line summary for list rows
        meta=_meta_line(c),
        # backend-specific row detail as EXPLICIT fields (not just inside meta):
        # the desktop escapes user-facing strings before innerHTML, so it needs
        # the raw values, not the pre-joined line
        mlip_model=(getattr(c.config, "mlip_model", "") if c.config else "")
        if c.kind.startswith("mlip") else "",
        crest_method=(getattr(c.config, "crest_method", "") if c.config else "")
        if c.kind.startswith("crest") else "",
    )


def _meta_line(c: Calculation) -> str:
    if c.geometry_source == GeometrySource.REFERENCE or c.geometry_source == "reference":
        src = f"ref {c.ref_name}"
    else:
        src = ".xyz"
    # MLIP calcs don't use charge/multiplicity; show the MACE model instead.
    if c.kind.startswith("mlip"):
        model = getattr(c.config, "mlip_model", "") if c.config else ""
        return f"{c.kind} · {src} · {model or 'MACE'}"
    # CREST calcs use charge/multiplicity too; also show the tight-binding method.
    if c.kind.startswith("crest"):
        method = getattr(c.config, "crest_method", "") if c.config else ""
        return f"{c.kind} · {src} · {method or 'gfn2'} · q{c.charge} m{c.multiplicity}"
    return f"{c.kind} · {src} · q{c.charge} m{c.multiplicity}"


def calc_from_dict(d: dict, restoring: bool = False) -> Calculation:
    """
    Build a Calculation from a client payload dict (used by both the HTTP
    server and the Qt bridge). PyQt-independent so it lives here.

    ``restoring`` is passed straight to _validate_calc_name — see its docstring
    for why the session-restore path is held to a weaker name rule.
    """
    cfg = StepConfig.from_dict(d.get("config", {}))
    src = d.get("geometry_source", "direct")
    # This is the trust boundary for phone/HTTP payloads too: wrong-typed
    # values must surface as ValueError (the callers' 400/{"error"} path),
    # never as AttributeError/OverflowError escaping the slot or endpoint.
    raw_name = d.get("name", "")
    if not isinstance(raw_name, str):
        raise ValueError("Calculation name must be a string.")
    name = raw_name.strip()
    if not name:
        raise ValueError("Calculation name is required.")
    kind = str(d.get("kind", "") or "").strip()
    if kind not in KNOWN_KINDS:
        raise ValueError(
            f"Unknown calculation type '{kind}'. Supported types: "
            + ", ".join(sorted(KNOWN_KINDS)) + ".")
    _validate_calc_name(name, kind, restoring=restoring)
    # charge/multiplicity are f-string-interpolated into the .inp's
    # "* xyz {charge} {multiplicity}" line. int() already blocks string
    # injection, but not absurd magnitudes — clamp to physically generous
    # ranges so a bogus client payload can't emit a nonsense input file.
    try:
        charge = max(-100, min(100, int(d.get("charge", 0))))
        multiplicity = max(1, min(200, int(d.get("multiplicity", 1))))
    except OverflowError:                    # int(float("inf")) from JSON 1e999
        raise ValueError("charge/multiplicity must be finite integers.")

    def _s(key: str) -> str:
        v = d.get(key, "")
        return v if isinstance(v, str) else ""

    kind = d.get("kind", cfg.kind)
    return Calculation(
        name=name,
        kind=kind if isinstance(kind, str) else cfg.kind,
        config=cfg,
        charge=charge,
        multiplicity=multiplicity,
        geometry_source=GeometrySource(src),
        xyz=_s("xyz"),
        ref_name=_s("ref_name"),
        is_raw=bool(d.get("is_raw", False)),
        raw_text=_s("raw_text"),
    )


# ---- session persistence (autosave / restore) --------------------------
# The whole queue is mirrored to a JSON file so closing ORCAdesk does not lose
# it, and a calculation left RUNNING (its detached ORCA still going) can be
# reattached on the next launch.

def _session_file():
    return user_data_root() / "session.json"


def calc_to_session_dict(c: Calculation) -> CalcFull:
    """Full-fidelity serialization of a Calculation for the session file
    (unlike calc_to_dict, which is the compact UI snapshot)."""
    gs = c.geometry_source.value if isinstance(c.geometry_source, GeometrySource) else str(c.geometry_source)
    st = c.state.value if isinstance(c.state, CalcState) else str(c.state)
    return CalcFull(
        name=c.name,
        kind=c.kind,
        config=c.config.to_dict() if c.config else {},
        charge=c.charge,
        multiplicity=c.multiplicity,
        geometry_source=gs,
        xyz=c.xyz,
        ref_name=c.ref_name,
        is_raw=c.is_raw,
        raw_text=c.raw_text,
        state=st,
        message=c.message,
        output_path=c.output_path,
        pid=c.pid,
        create_time=c.create_time,
    )


def calc_from_session_dict(d: dict) -> Calculation:
    """Rebuild a Calculation from the session file, restoring runtime state
    (state/message/output_path/pid) on top of calc_from_dict."""
    c = calc_from_dict(d, restoring=True)   # config + geometry + name validation
    st = d.get("state", "pending")
    try:
        c.state = CalcState(st)
    except ValueError:
        c.state = CalcState.PENDING
    # type-checked like output_path/pid below: it rides the CalcSummary wire
    # declared `str`, and a dict from a hand-edited session.json rendered as
    # "[object Object]" in the queue row
    msg = d.get("message", "")
    c.message = msg if isinstance(msg, str) else ""
    out = d.get("output_path", "") or ""
    c.output_path = out if isinstance(out, str) else ""
    # pid/create_time flow into psutil.Process(int(pid)) during reconciliation;
    # a wrong-typed value (hand-edited/corrupted session) must degrade to
    # "no recorded process", not crash every startup (P32)
    pid = d.get("pid")
    try:
        c.pid = int(pid) if pid is not None else None
    except (TypeError, ValueError, OverflowError):
        c.pid = None
    ct = d.get("create_time")
    try:
        c.create_time = float(ct) if ct is not None else None
    except (TypeError, ValueError):
        c.create_time = None
    return c


def _parse_if_exists(calc):
    """Parse a calc's on-disk output if present, via the engine's own kind
    dispatch (result_from_output — one dispatch for engine and reconciliation).
    Returns None on any read/parse error so reconciliation can fall back to
    FAILED rather than crash."""
    from pathlib import Path
    path = getattr(calc, "output_path", "") or ""
    try:
        # is_file(), not exists(): Windows resolves device names like "CON" to
        # the console, which exists() passes and open() then blocks on forever
        # — a corrupted session.json must not hang startup (P32)
        if not (path and Path(path).is_file()):
            return None
        return result_from_output(calc)
    except Exception:
        return None


def _auto_export_crest(calc) -> None:
    """Best-effort split of a finished CREST search's ensemble into per-conformer
    ``.xyz`` files under ``conformers/`` (mirrors the engine's finish-time export,
    for a search judged DONE at startup that never went through the engine). Pure
    file I/O, all errors swallowed — reconciliation must never fail on it. Lazy
    import keeps state/ free of the crest package at import time."""
    from pathlib import Path
    path = getattr(calc, "output_path", "") or ""
    if not path:
        return
    try:
        from ..crest.export import export_conformers
        calc_dir = Path(path).parent
        export_conformers(calc_dir / "crest_conformers.xyz",
                          calc_dir / "conformers", calc.name)
    except Exception:
        pass


def _crest_calc_alive(calc) -> bool:
    """Transport-aware liveness for a RUNNING crest_* calc. Under WSL the pid is
    a Linux one that psutil (process_matches) cannot see, so the check goes
    through the CrestRunner (kill -0 + start-time guard) on the target persisted
    at launch in config.crest_env_id. Locally that pid IS a native one, but the
    same check is used rather than psutil: one code path, and the start-time
    comparison against /proc field 22 is the same guard either way. Lazy import
    keeps state/ free of the crest package at import time."""
    if not getattr(calc, "pid", None):
        return False
    distro = (getattr(calc.config, "crest_env_id", "") or "").strip() if calc.config else ""
    if not distro:
        # A session written before the target was persisted (or by an older
        # build) has nothing here. On a local transport there is only ever one
        # place the job can be, so the answer is knowable; under WSL it is not,
        # and an unknown distro still means "judge it from its files".
        from ..crest.shell import LOCAL_TARGET, is_local
        if not is_local():
            return False
        distro = LOCAL_TARGET
    try:
        from ..crest.runner import CrestRunner
        runner = CrestRunner(distro)
        runner.adopt(calc.pid, calc.create_time)
        return runner.is_alive()
    except Exception:
        return False


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
            # CREST runs in WSL (Linux pid), so psutil can't judge it — use the
            # WSL-aware check. Everything else (ORCA) uses psutil process_matches.
            if c.kind.startswith("crest"):
                alive = _crest_calc_alive(c)
            else:
                alive = bool(c.pid and process_matches(c.pid, c.create_time))
            if alive:
                continue  # genuinely still running — reattach on resume
            c.pid = None
            c.create_time = None
            r = _parse_if_exists(c)
            if r is not None and r.terminated_normally:
                c.result = r
                try:
                    validate_result(c, r)
                    c.state = CalcState.DONE
                    c.message = "Completed (finished while ORCAdesk was closed)."
                    if c.kind.startswith("crest"):
                        _auto_export_crest(c)
                except OrcaRunError as e:
                    c.state = CalcState.FAILED
                    c.message = str(e)
            elif c.kind.startswith("mlip"):
                # An MLIP worker never survives shutdown (detach terminates it —
                # mlip/runner.py), so a still-RUNNING mlip calc here was stopped
                # deliberately with the app: CANCELLED (re-runnable), not
                # FAILED-locked. (The engine stamps this at detach too; this
                # covers the race where the app exits before that lands.)
                c.state = CalcState.CANCELLED
                c.message = "Stopped when ORCAdesk closed."
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
        # log buffer: list of (seq, level, message, calc); clients poll with ?since=
        self._log: list[tuple[int, str, str, str]] = []
        self._log_seq = 0
        # env id -> MLIP probe state, published by the desktop Bridge so the
        # phone's /api/run can resolve mlip_env_id == "" the same way (P4)
        self._mlip_readiness: dict = {}
        # the background engine + thread while a run is in progress
        self._engine = None
        self._thread: Optional[threading.Thread] = None
        # access token (6-digit PIN), generated once per app launch (at store
        # construction) — it lives for the whole app session, across server
        # stop/starts
        self._token = _new_pin()
        # connected phone clients: {client_id: last_seen_epoch}
        self._clients: dict[str, float] = {}
        self._client_ttl = 15.0   # seconds without heartbeat = considered gone

    # ---- auth token ----
    @property
    def token(self) -> str:
        with self._lock:
            return self._token

    def check_token(self, supplied: Optional[str]) -> bool:
        """Constant-time-ish comparison of a supplied token against the PIN."""
        if not supplied:
            return False
        import hmac
        with self._lock:
            # compare as bytes: compare_digest on str requires ASCII and raises
            # TypeError otherwise — a non-ASCII token pasted on the phone must
            # be an auth failure, not an HTTP 500.
            return hmac.compare_digest(
                str(supplied).encode("utf-8"), self._token.encode("utf-8"))

    # ---- connected clients (phones) ----
    def heartbeat(self, client_id: str) -> None:
        """Record that a client is alive right now (prunes stale ones — without
        this, clients minting fresh ids per page load would grow the dict for as
        long as the server runs when nothing polls active_clients())."""
        import time
        now = time.time()
        if not client_id:
            return
        with self._lock:
            stale = [cid for cid, t in self._clients.items()
                     if now - t > self._client_ttl]
            for cid in stale:
                del self._clients[cid]
            self._clients[client_id] = now

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
    def snapshot(self) -> QueueSnapshot:
        """Full state for a client (queue list + running flag + version +
        the run's live resource occupancy)."""
        with self._lock:
            return QueueSnapshot(
                running=self._running,
                version=self._version,
                calculations=[calc_to_dict(c) for c in self._calcs],
                resources=self.run_resources(),
            )

    def run_resources(self) -> RunResources:
        """Cores/memory the in-flight jobs reserved, against the run's budget.
        All zeros between runs: the budget lives on the engine, and an idle
        client reads the limits from the settings payload instead. Duck-typed so
        the store never imports the engine."""
        idle = RunResources(cores_used=0, cores_budget=0, ram_used_mb=0,
                            ram_budget_mb=0, jobs=0, max_jobs=0)
        eng = self._engine
        if eng is None:
            return idle
        try:
            return RunResources(**eng.resource_usage())
        except Exception:
            return idle

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
    # While a run is in progress the queue is LIVE, not frozen (P29): the
    # engine walks this store's own list under this store's own lock
    # (make_engine_factory passes both), picking the next unhandled row each
    # iteration — so a row added mid-run executes in the same run, and a
    # removed/edited PENDING row is simply never picked / picked in its edited
    # form. What the guards below still protect mid-run:
    #   - the engine's in-flight calc (state RUNNING, plus _engine_active for
    #     the picked-but-not-yet-stamped window) is untouchable;
    #   - non-EDITABLE_STATES rows (DONE/FAILED) keep their mid-run immunity —
    #     a DONE row may be a reference parent the walk still needs;
    #   - a mid-run add/edit must not carry a dangling reference, and a
    #     mid-run remove must not create one: the idle-time laxness is safe
    #     only because the pre-run check (find_dangling_reference) re-screens,
    #     which a mid-run mutation bypasses — an unresolvable reference would
    #     FAIL (and lock, P24) the dependent instead of refusing up front.

    def _engine_active(self, name: str) -> bool:
        """True when the running engine is currently handling `name` (duck-typed
        so the store never imports the engine)."""
        eng = self._engine
        if eng is None:
            return False
        try:
            return name in (getattr(eng, "active_names", None) or ())
        except Exception:
            return False

    @staticmethod
    def _is_reference(calc: Calculation) -> bool:
        """Robust GeometrySource.REFERENCE test (enum or its str value)."""
        src = calc.geometry_source
        return src == GeometrySource.REFERENCE or str(
            getattr(src, "value", src)) == "reference"

    def add(self, calc: Calculation) -> None:
        """Append a calculation. Raises ValueError on duplicate name (and, while
        the queue is running, on a reference that isn't in the queue)."""
        with self._lock:
            # case-insensitive: calc.name is an on-disk folder name and Windows
            # (the primary target) resolves "water" and "Water" to the SAME
            # folder — accepting both would silently share one .out between two
            # calculations.
            if any(c.name.casefold() == calc.name.casefold() for c in self._calcs):
                raise ValueError(f"A calculation named '{calc.name}' already exists.")
            if self._running and self._is_reference(calc) and (
                    calc.ref_name not in {c.name for c in self._calcs}):
                # no pre-run screen will catch this one — it would run (and
                # FAIL, locking) with an unresolvable reference
                raise ValueError(
                    f"'{calc.name}' references '{calc.ref_name}', "
                    "which is not in the queue.")
            self._calcs.append(calc)
            self._bump_and_save()

    def remove(self, name: str) -> bool:
        with self._lock:
            if self._running:
                target = next((c for c in self._calcs if c.name == name), None)
                if target is None:
                    return False
                if target.state not in EDITABLE_STATES:
                    raise ValueError(
                        "While the queue is running, only pending, cancelled, "
                        "or blocked calculations can be removed.")
                if self._engine_active(name):
                    raise ValueError(
                        "Cannot remove: this calculation is about to run.")
                dep = next(
                    (c for c in self._calcs
                     if c.name != name
                     and c.state not in (CalcState.DONE, CalcState.FAILED)
                     and self._is_reference(c) and c.ref_name == name),
                    None)
                if dep is not None:
                    raise ValueError(
                        f"Cannot remove '{name}' while the queue is running: "
                        f"'{dep.name}' references its geometry.")
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
        Editable = pending, cancelled, or blocked (see EDITABLE_STATES). Editing
        resets the entry to PENDING (and clears the old result/message) so it
        runs on the next Run.
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
                raise ValueError("Only pending, cancelled, or blocked calculations can be edited.")
            if self._running:
                if self._engine_active(name):
                    raise ValueError(
                        "Cannot edit: this calculation is about to run.")
                if new_calc.name != name and any(
                        c.name != name
                        and c.state not in (CalcState.DONE, CalcState.FAILED)
                        and self._is_reference(c) and c.ref_name == name
                        for c in self._calcs):
                    raise ValueError(
                        f"Cannot rename '{name}' while the queue is running: "
                        "another calculation references its geometry.")
                if self._is_reference(new_calc) and new_calc.ref_name not in {
                        c.name for c in self._calcs if c.name != name}:
                    raise ValueError(
                        f"'{new_calc.name}' references '{new_calc.ref_name}', "
                        "which is not in the queue.")
            # if renaming, the new name must not collide with a DIFFERENT entry
            # (case-insensitive — names are Windows folder names, see add())
            if new_calc.name != name and any(
                c.name.casefold() == new_calc.name.casefold()
                for j, c in enumerate(self._calcs) if j != idx
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
        be editable (see EDITABLE_STATES) so running/done/failed items keep
        their place. Returns True on move. Allowed mid-run too: the engine
        picks the first unhandled row in live list order, so moving editable
        rows genuinely reorders the remaining execution."""
        with self._lock:
            n = len(self._calcs)
            if not (0 <= from_idx < n) or not (0 <= to_idx < n):
                raise ValueError("Index out of range.")
            if from_idx == to_idx:
                return False
            if self._calcs[from_idx].state not in EDITABLE_STATES:
                raise ValueError("Only pending, cancelled, or blocked calculations can be moved.")
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
    def append_log(self, message: str, level: str = "info",
                   calc: str = "") -> None:
        """Append one log line. `calc` names the calculation that produced it
        ("" = engine/app level): with several jobs in flight their output
        interleaves here, and the tag is what lets a client separate them."""
        with self._lock:
            self._log_seq += 1
            self._log.append((self._log_seq, level, message, calc))
            # cap buffer so a long run doesn't grow memory without bound.
            # Preferential retention for "[web] " lines: ORCA stdout floods this
            # ring (a single run emits thousands of lines) and would evict the
            # rate-limited console-capture lines whose whole purpose is post-hoc
            # front-end diagnosis (window.py _ConsoleCapturePage) — keep the
            # newest 4000 lines plus up to 500 older [web] lines (seq order is
            # preserved: every retained [web] seq predates the tail's).
            if len(self._log) > 5000:
                tail = self._log[-4000:]
                web_keep = [t for t in self._log[:-4000]
                            if t[2].startswith("[web] ")][-500:]
                self._log = web_keep + tail

    def log_since(self, since: int = 0) -> LogPayload:
        """Return log lines with seq > since, plus the latest seq."""
        with self._lock:
            lines = [
                LogLine(seq=s, level=lv, msg=m, calc=c)
                for (s, lv, m, c) in self._log if s > since
            ]
            return LogPayload(lines=lines, latest=self._log_seq)

    # (No backend log-clear: the Log tab's Clear is deliberately view-only on
    # every client. If one is ever added, it must keep _log_seq monotonic —
    # resetting it would corrupt every client's since-cursor.)

    # ---- run management ----
    def start_run(self, engine_factory, precheck=None) -> None:
        """
        Start running the queue in a background thread.

        engine_factory() must return a QueueEngine already wired with callbacks
        that update THIS store (log -> append_log, calc_update -> touch). We
        keep store framework-agnostic by having the caller build the engine.

        ``precheck(calcs) -> str | None`` is the caller's pre-run screen, run
        HERE, under the lock, against the live list. Callers used to screen
        their own snapshot and then call this — and `add()` only enforces
        reference integrity while a run is in progress, so a calculation added
        in that window (another window, a phone client) slipped past both
        guards and was FAILED-locked at run time for a reference the screen
        would have refused. Returning a message aborts the start.

        Raises RuntimeError if a run is already in progress, ValueError if the
        queue is empty or the precheck refuses.
        """
        with self._lock:
            if self._running:
                raise RuntimeError("A run is already in progress.")
            if not self._calcs:
                raise ValueError("The queue is empty.")
            if precheck is not None:
                problem = precheck(list(self._calcs))
                if problem:
                    raise ValueError(problem)
            # the engine walks the store's OWN list (live queue, P29) — the
            # factory also hands it this store's lock, so its per-iteration
            # pick and the user mutations above are serialized
            calcs = self._calcs
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

    # --- MLIP readiness (published by the desktop probe, read by both runs) ---
    def set_mlip_readiness(self, states: dict) -> None:
        """Publish env id -> probe state ("ready"/"checking"/"error").

        Readiness is a live probe result that only the desktop Bridge produces
        (it costs an interpreter launch and a torch import), but BOTH run entry
        points have to honour `mlip_env_id == "" -> first ready env`. Putting it
        on the shared store is what lets the phone's /api/run order the env list
        the same way the desktop does, instead of taking settings order and
        sending a phone-started calculation into a broken interpreter while the
        desktop pill reads "ready" off a different env.
        """
        with self._lock:
            self._mlip_readiness = dict(states or {})

    def mlip_readiness(self) -> dict:
        """The last published readiness map ({} before the first probe)."""
        with self._lock:
            return dict(self._mlip_readiness)

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
        except (OSError, UnicodeError):
            # UnicodeError is not hypothetical: json.loads turns the escape
            # "\ud800" into a lone surrogate, which no UTF-8 encoder will take.
            # It is a ValueError, so it escaped this guard and propagated out of
            # _bump_and_save and out of add/remove/replace/reorder — and since
            # the calc is already in the list by then, EVERY later mutation
            # raised too and the session was never written again. Names are
            # refused surrogates at the validator now; this is the backstop for
            # every other persisted string.
            try:
                tmp.unlink()
            except (OSError, NameError, UnboundLocalError):
                pass

    def load_session(self) -> None:
        """Restore the queue from the session file and reconcile it with reality
        (see reconcile_calcs). Call once at startup. No-op if missing/unreadable."""
        path = _session_file()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        # Valid JSON that isn't an object (a list, null, a bare number — e.g. a
        # hand-repaired or externally-truncated file) must degrade like corrupt
        # JSON does: .get() on a non-dict would crash EVERY startup until the
        # user deletes session.json by hand (same guard as Settings.load, P32).
        if not isinstance(data, dict):
            return
        # ...and neither is the value under "calculations" necessarily a list:
        # null or a number is not iterable, and the TypeError would propagate
        # out of MainWindow.__init__ — the app would not start at all until the
        # file was deleted by hand (P32).
        entries = data.get("calculations", [])
        if not isinstance(entries, list):
            entries = []
        restored = []
        seen_names: set[str] = set()
        for d in entries:
            try:
                c = calc_from_session_dict(d)
            except Exception:
                continue  # skip a corrupt entry rather than lose the whole queue
            # The restore path must uphold the same case-insensitive name
            # uniqueness add() enforces (Windows folder identity): a session
            # written by an older build (or edited/merged externally) could
            # otherwise resurrect two calcs sharing one on-disk folder.
            key = c.name.casefold()
            if key in seen_names:
                continue  # first occurrence wins, matching store.get()
            seen_names.add(key)
            restored.append(c)
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


def queue_needs_orca(calcs: "list[Calculation]") -> bool:
    """True if running these calculations would launch ORCA at least once —
    i.e. some calc is not DONE and its kind is neither an mlip_* nor a crest_*
    kind (both run outside ORCA: mlip in the user's MACE env, crest in a POSIX
    shell -- WSL on Windows, this machine on Linux).

    Single shared decision (P4) for both run entry points (the desktop bridge
    and the phone server): an all-MLIP or all-CREST queue must be runnable with
    no ORCA executable configured, so requiring a valid ORCA path is gated on
    this instead of being unconditional. DONE calcs are never recomputed and
    FAILED calcs are locked (P24) — neither will ever launch ORCA, so they don't
    count (a stale FAILED ORCA calc must not force an ORCA path onto an otherwise
    all-MLIP/CREST queue).
    """
    return any(
        c.state not in (CalcState.DONE, CalcState.FAILED)
        and not c.kind.startswith("mlip")
        and not c.kind.startswith("crest")
        for c in calcs
    )


def find_dangling_reference(calcs: "list[Calculation]") -> "str | None":
    """First error message for a REFERENCE geometry that resolves to no queued
    calc name, else None. Like queue_needs_orca, a single shared pre-run check
    (P4) for both run entry points — the desktop bridge and the phone's
    /api/run — so a dangling reference is refused up front with the same
    message everywhere instead of failing mid-run with a murkier one."""
    names = {c.name for c in calcs}
    for c in calcs:
        # DONE results are frozen and FAILED calcs are locked (P24) — neither
        # will ever run again, so a dangling reference on them must not veto
        # the whole run (e.g. a FAILED calc whose referenced parent was later
        # removed would otherwise lock the Run button for good).
        if c.state in (CalcState.DONE, CalcState.FAILED):
            continue
        src = c.geometry_source
        is_ref = src == GeometrySource.REFERENCE or str(
            getattr(src, "value", src)) == "reference"
        if is_ref and c.ref_name == c.name:
            # Trivially "in the queue", so the name test below waves it
            # through; the engine then fails it at run time, and FAILED is
            # locked (P24). A calculation cannot be its own geometry source, and
            # that is knowable before anything starts.
            return (f"'{c.name}' uses its own geometry as its input. "
                    "Pick another calculation, or load coordinates directly.")
        if is_ref and c.ref_name not in names:
            return (f"'{c.name}' references '{c.ref_name}', "
                    "which is not in the queue.")
    return None


def make_engine_factory(store: "QueueStore", orca_path: str, workspace_root: str,
                        skip_names: "set[str] | None" = None,
                        mlip_envs: "list | None" = None,
                        crest_distro: str = "",
                        budget=None):
    """
    Returns a zero-arg factory that builds a QueueEngine whose callbacks feed
    the given store (log buffer + version bumps). Used by start_run().

    skip_names: calculations the user chose not to run (e.g. to avoid
    overwriting existing results on disk).
    mlip_envs: registered MLIP environments [{id, name, python}], so the engine
    can run mlip_* calcs in the user's MACE interpreter. None/empty disables MLIP.
    crest_distro: preferred target for crest_* calcs -- a WSL distro, or
        "local" on a native transport ("" = auto-detect the
    first distro that has CREST).
    budget: a core/resources.ResourceBudget capping how many calculations run at
    once and how many cores / how much memory they may occupy together. None =
    the default one-at-a-time queue.
    """
    skip = set(skip_names or ())
    envs = list(mlip_envs or [])
    crest_pref = (crest_distro or "").strip()

    def factory() -> QueueEngine:
        cb = QueueCallbacks(
            log=lambda msg, level, calc="": store.append_log(msg, level, calc),
            calc_update=lambda i, c: store.touch(),
        )
        # the store's own lock doubles as the engine's queue lock: run_all
        # walks the store's live list (start_run), so the walk's pick step and
        # user mutations must serialize on the same lock (P29)
        return QueueEngine(orca_path, workspace_root, cb, skip_names=skip,
                           mlip_envs=envs, crest_distro=crest_pref,
                           queue_lock=store._lock, budget=budget)
    return factory
