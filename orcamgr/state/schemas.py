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
    """Compact queue-row form — mirror of store.calc_to_dict (14 keys)."""
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
    # backend-specific row detail, "" when not applicable — explicit fields so
    # the desktop can render them ESCAPED (meta embeds user-typed ref names, so
    # it must never land in innerHTML)
    mlip_model: str           # mlip* kinds: the MACE model label
    crest_method: str         # crest* kinds: the tight-binding method


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
    theme: str                # "dark" | "light"
    theme_variant: str        # "shadcn" | "liquidglass"
    glass_level: str          # restrained|moderate|bold|vivid|maximal (liquidglass)
    wallpaper: str            # aurora|aqua|sunset|grape|graphite|ocean|custom
    eta_mode: str             # "conservative" | "eager"
    geo_graph_mode: str       # "all5" | "maxgrad"
    build_mode: str           # "beginner" | "expert" | "mlip" | "crest"
    crest_distro: str         # preferred WSL distro for CREST ("" = auto-detect)
    orca_valid: bool


class MlipBackend(TypedDict):
    """One MLIP backend detected inside a registered environment."""
    key: str                  # registry key, e.g. "mace"
    label: str                # display label, e.g. "MACE"
    version: str              # installed package version, e.g. "0.3.6"


class MlipEnvPayload(TypedDict):
    """One registered MLIP environment (config merged with its live probe).
    ORCAdesk does not install the MLIP toolchain; the user points each env at
    their own Python and this reports which backends actually import. The probe
    runs in a background thread (importing torch is slow), so the UI polls."""
    id: str                   # stable env id
    name: str                 # user-facing label
    python: str               # configured interpreter path
    state: str                # "checking" | "ready" | "error"
    version: str              # interpreter Python version (e.g. "3.11.5"), or ""
    backends: "list[MlipBackend]"   # auto-detected MLIP backends present
    cuda: "bool | None"       # torch sees a CUDA GPU (None = unknown/not probed)
    cuda_name: str            # GPU name when cuda is True, else ""
    message: str              # human-readable status / error detail


class MlipStatusPayload(TypedDict):
    """Bridge.get_mlip_status() / check_mlip() / add_mlip_env() / remove_mlip_env()
    — the whole MLIP picture: an aggregate state for the top-bar pill plus every
    registered environment. Aggregate state is "ready" if any env is ready, else
    "checking" if any is probing, else "error" if envs exist but none are ready,
    else "unset"."""
    state: str                # "unset" | "checking" | "ready" | "error"
    envs: "list[MlipEnvPayload]"


class CrestDistroPayload(TypedDict):
    """One WSL distro probed for CREST (backs the "CREST ready" indicator)."""
    distro: str               # distro name (pass to `wsl -d <distro>`)
    ready: bool               # crest binary found + runnable
    crest_bin: str            # resolved binary path inside the distro, or ""
    version: str              # `crest --version` line, or ""
    error: str                # human-readable detail when not ready


class CrestStatusPayload(TypedDict):
    """Bridge.get_crest_status() / check_crest() / install_crest() — the whole
    CREST picture: an aggregate state for the top-bar pill plus every usable WSL
    distro. Aggregate state is "ready" if any distro has CREST, "checking" while
    probing, "error" if distros exist but none have CREST, "unset" if WSL/distros
    are absent."""
    state: str                # "unset" | "checking" | "ready" | "error"
    distros: "list[CrestDistroPayload]"
    wsl: bool                 # whether wsl.exe is available at all


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

class LoadResult(TypedDict):
    """Unified envelope for the four file-loader slots (load_xyz_file /
    load_xyz_path / load_inp_file / load_inp_path) — 5 keys, all always
    present. "cancelled" distinguishes the user closing the picker (a
    deliberate choice: ok=True, cancelled=True, no error) from a real read
    failure (ok=False, "error" filled) — the previous per-slot shapes
    conflated the two, so the UI couldn't tell cancel from OSError (A2).
    Loading a geometry never changes the workspace (that is a Settings-only
    action), so there is no workspace field here."""
    ok: bool
    cancelled: bool
    text: str
    name: str                 # filename stem (auto-fills the calc name), "" if none
    error: str                # "" except on the read-failure branch


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


class GeomAtomPayload(TypedDict):
    """One atom of the final/optimized geometry (Angstrom)."""
    el: str
    x: float
    y: float
    z: float


class OrbitalPayload(TypedDict):
    """One molecular-orbital energy level."""
    idx: int
    occ: float
    ev: float


class TddftStatePayload(TypedDict):
    """One TD-DFT excited state with its dominant orbital contributions.
    `contributions` is a list of [from_orbital, to_orbital, weight] triples."""
    state: int
    ev: float
    contributions: "list[tuple[str, str, float]]"


class ConformerPayload(TypedDict):
    """One CREST conformer for the Results tab's selectable ensemble list. The
    geometry itself is NOT sent (it can be large × many conformers); the batch
    "generate ORCA jobs" action re-reads it server-side from the parent calc."""
    index: int                # 1-based rank (1 = lowest energy)
    energy_eh: float          # absolute energy (Hartree)
    rel_kcal: float           # energy relative to the best conformer (kcal/mol)
    n_atoms: int


class ParsePayload(TypedDict, total=False):
    """Successful parse of a .out. All keys optional on the wire because the
    failure branch sends ErrorPayload and parse_out_file's cancelled dialog
    sends only {"cancelled": true}; a successful parse emits every data key
    it has."""
    cancelled: bool           # True only for a cancelled Open-.out dialog (A2)
    summary: "list[tuple[str, str, str]]"     # label/value/category rows
    is_optimization: bool                     # gates "Final geometry" (front-end)
    show_elec: bool                           # gates electronic-structure sections
    transitions: "list[TransitionPayload]"
    frequencies: "list[float]"                # cm^-1, negatives = imaginary
    n_imaginary: int
    mulliken: "list[tuple[str, float]]"
    loewdin: "list[tuple[str, float]]"        # Loewdin atomic charges
    mayer_valences: "list[tuple[int, str, float]]"   # (idx, element, Mayer valence)
    mayer_bonds: "list[tuple[str, str, float]]"      # (atom_i, atom_j, bond order)
    nmr: "list[NmrPayload]"
    neb_path: "list[NebPointPayload]"
    neb_path_kind: str                        # "neb" | "irc" — titles the path profile
    geometry: "list[GeomAtomPayload]"         # final/optimized coordinates (A)
    orbitals: "list[OrbitalPayload]"          # orbital energies (eV) + occupations
    tddft_states: "list[TddftStatePayload]"   # excited-state composition
    input_keywords: str                       # echoed "!" simple-input line
    input_block: str                          # echoed input block (%-blocks etc.)
    is_conformer_search: bool                 # gates the CREST conformer list
    conformers: "list[ConformerPayload]"      # CREST ensemble (ranked)


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


class ExistsResult(_Ok, total=False):
    """has_existing_output — "exists" is present on every branch. Backs the
    front-end's mid-run add warning: the queue keeps running while calcs are
    added, so a name whose workspace folder already holds a result would be
    overwritten without the Run-click conflicts modal ever seeing it."""
    error: str
    exists: bool


class AutodetectResult(_Ok, total=False):
    """autodetect_orca — "path" is present on every branch ("" when nothing
    was found); "error" only when detection itself raised. NOTE: despite the
    getter-ish name this is a MUTATION slot — a found path is also written to
    settings.orca_path and saved (A3), which the envelope makes explicit
    instead of returning a bare string."""
    path: str
    error: str


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
