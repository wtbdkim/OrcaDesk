"""
TypedDict schemas for every payload exchanged across the QWebChannel bridge
(orcamgr/gui/bridge.py) and the phone HTTP API (orcamgr/server/app.py) that is
NOT already covered by the Calculation/StepConfig serialization in store.py /
input_generator.py: settings, log entries, queue snapshots, parse-result
summaries, server status, and the ok/error envelopes.

MIRROR CONTRACT: web/types.js holds the JSDoc mirror of these shapes for the
front-end. The two files must stay in sync field by field — when you add or
rename a key here, change web/types.js (and the consuming JS) in the same
commit, and keep `npx -p typescript tsc --noEmit -p jsconfig.json` clean.

These are *declarations only*: constructing a TypedDict is constructing a
plain dict, so json.dumps output (key order included, since kwargs preserve
order) is byte-identical to the ad-hoc literals they replaced. Keyword order
at every construction site deliberately matches the original literal.

total=False bases express keys that are only sometimes present (e.g. "error"
only on failure) without requiring Python 3.11's NotRequired — the project
supports 3.10.
"""

from __future__ import annotations

from typing import TypedDict


# ---- calculations as serialized by store.py --------------------------------
# (The construction itself lives in store.calc_to_dict / calc_to_session_dict;
# these names exist so snapshot/envelope schemas can reference the shapes.)

class CalcSummary(TypedDict):
    """Compact queue-row form — mirror of store.calc_to_dict (12 keys)."""
    name: str
    kind: str
    charge: int
    multiplicity: int
    geometry_source: str      # "direct" | "reference"
    ref_name: str
    is_raw: bool
    state: str                # "pending"|"running"|"done"|"failed"|"blocked"|"cancelled"
    message: str
    output_path: str
    scf_convergence: str
    meta: str


class CalcFull(TypedDict):
    """Full-fidelity form — mirror of store.calc_to_session_dict (15 keys)."""
    name: str
    kind: str
    config: dict              # StepConfig.to_dict(), {} when absent
    charge: int
    multiplicity: int
    geometry_source: str
    xyz: str
    ref_name: str
    is_raw: bool
    raw_text: str
    state: str
    message: str
    output_path: str
    pid: "int | None"
    create_time: "float | None"


# ---- queue snapshot / log ---------------------------------------------------

class QueueSnapshot(TypedDict):
    """store.QueueStore.snapshot() — what get_queue / GET /api/queue serve."""
    running: bool
    version: int
    calculations: "list[CalcSummary]"


class LogLine(TypedDict):
    seq: int
    # "info" | "warn" | "err" | "ok" (job done) | "orca" (tailed ORCA stdout
    # lines streamed during a run — see core/queue.py)
    level: str
    msg: str


class LogPayload(TypedDict):
    """store.QueueStore.log_since() — what get_log / GET /api/log serve."""
    lines: "list[LogLine]"
    latest: int


# ---- settings / about (desktop bridge) --------------------------------------

class SettingsPayload(TypedDict):
    orca_path: str
    workspace_root: str
    default_nprocs: int
    default_maxcore_mb: int
    theme: str
    eta_mode: str             # "conservative" | "eager"
    geo_graph_mode: str       # "all5" | "maxgrad"
    build_mode: str           # "beginner" | "expert"
    orca_valid: bool


class ErrorPayload(TypedDict):
    """Bare {"error": ...} (no "ok" key): save_settings failure and the
    parse slots' failure branch."""
    error: str


class AboutPayload(TypedDict):
    version: str
    author: str
    org: str
    email: str


# ---- file loaders ------------------------------------------------------------

class _InpFileBase(TypedDict):
    text: str
    name: str                 # filename stem, auto-fills the calc name


class InpFilePayload(_InpFileBase, total=False):
    error: str                # only on load_inp_path's OSError branch


# ---- parse results (Bridge._parse_path) --------------------------------------

class TransitionPayload(TypedDict):
    state: int
    ev: float
    nm: float
    fosc: float


class NmrPayload(TypedDict):
    idx: int
    el: str
    iso: float
    aniso: float


class NebPointPayload(TypedDict):
    """Built by parser.py for the NEB path summary."""
    label: str
    e_eh: float
    de_kcal: float
    is_ts: bool


class ParsePayload(TypedDict, total=False):
    """Successful parse of a .out. All keys optional on the wire because the
    failure branch sends ErrorPayload and parse_out_file returns "{}" on a
    cancelled dialog; a successful parse emits all seven data keys."""
    summary: "list[tuple[str, str]]"          # label/value rows (JSON: arrays)
    transitions: "list[TransitionPayload]"
    frequencies: "list[float]"                # cm^-1, negatives = imaginary
    n_imaginary: int
    mulliken: "list[tuple[str, float]]"
    nmr: "list[NmrPayload]"
    neb_path: "list[NebPointPayload]"


# ---- ok/error envelopes -------------------------------------------------------

class _Ok(TypedDict):
    ok: bool


class OkResult(_Ok, total=False):
    """{"ok": bool[, "error": str]} — run_queue, cancel_queue,
    stop_after_current, stop_server (cancel/stop never carry "error")."""
    error: str


class MutationResult(_Ok, total=False):
    """Queue mutations (add/update/remove/clear/reorder, desktop and HTTP):
    a fresh snapshot rides along on success."""
    error: str
    snapshot: QueueSnapshot


class GetCalcResult(_Ok, total=False):
    error: str
    calc: CalcFull


class TextResult(_Ok, total=False):
    """build_inp_preview / get_inp."""
    error: str
    text: str


class GraphLinesResult(_Ok, total=False):
    """get_graph_lines — "lines" is present on every branch."""
    error: str
    lines: "list[str]"


class FepPoint(TypedDict):
    name: str
    gibbs_eh: float
    final_energy_eh: "float | None"
    kind: str


class FepResult(TypedDict):
    """get_free_energy_profile — ok is always True."""
    ok: bool
    points: "list[FepPoint]"


class ConflictsResult(_Ok, total=False):
    """check_overwrite_conflicts — "conflicts" is present on every branch."""
    error: str
    conflicts: "list[str]"


# ---- phone-sync server (bridge slots + HTTP endpoints) ------------------------

class ServerStatusPayload(TypedDict, total=False):
    """get_server_status — url/token/clients absent when unavailable."""
    available: bool
    running: bool
    url: str
    token: str
    clients: int


class QrResult(_Ok, total=False):
    """get_connect_qr — failures after URL construction still carry "url"."""
    error: str
    data_uri: str             # data:image/png;base64,...
    url: str


class StartServerResult(_Ok, total=False):
    error: str
    url: str
    token: str


class PingPayload(TypedDict):
    """GET /api/ping — open route; reports PIN validity without revealing it."""
    ok: bool
    authorized: bool


class HealthPayload(TypedDict):
    """GET /api/health."""
    status: str
    app: str
    version: str
    running: bool
    queue_version: int
    clients: int


class HeartbeatResult(TypedDict):
    """POST /api/heartbeat."""
    ok: bool
    clients: int


class RunStartedResult(TypedDict):
    """POST /api/run."""
    ok: bool
    running: bool
