"""
TypedDict schemas for every payload exchanged across the QWebChannel bridge
(orcamgr/gui/bridge.py) and the phone HTTP API (orcamgr/server/app.py) that is
NOT already covered by the Calculation/StepConfig serialization in store.py /
input_generator.py: settings, log entries, queue snapshots, parse-result
summaries, 3D-viewer frames, export/favorites envelopes, server status, and
the ok/error envelopes.

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
    # Live CPU/RAM occupancy of the run. All zeros while nothing runs — the
    # budgets are only known to the engine, so an idle client shows the limits
    # from SettingsPayload instead.
    resources: "RunResources"


class LogLine(TypedDict):
    seq: int
    # "info" | "warn" | "err" | "ok" (job done) | "orca" (tailed ORCA stdout
    # lines streamed during a run — see core/queue.py)
    level: str
    msg: str
    # Name of the calculation this line belongs to, "" for engine-level lines.
    # Several calculations can run at once, so their tailed output interleaves
    # in one buffer: this tag is what routes a line back to its job (the Log
    # tab's per-job filter and the per-job convergence graphs both key off it).
    calc: str


class LogPayload(TypedDict):
    """store.QueueStore.log_since() — what get_log / GET /api/log serve."""
    lines: "list[LogLine]"
    latest: int


class RunResources(TypedDict):
    """Live CPU/RAM occupancy of a parallel run — the queue header's readout.

    `cores_used`/`ram_used_mb` are what the in-flight jobs reserved (declared
    cores; ORCA %maxcore x cores for memory, a flat estimate for MLIP/CREST —
    see core/resources.py), the budgets are the resolved limits, and `jobs` is
    how many calculations are in flight against `max_jobs`.
    """
    cores_used: int
    cores_budget: int
    ram_used_mb: int
    ram_budget_mb: int
    jobs: int
    max_jobs: int


# ---- settings / about (desktop bridge) --------------------------------------

class SettingsPayload(TypedDict):
    orca_path: str
    workspace_root: str
    default_nprocs: int
    default_maxcore_mb: int
    # Parallel-run admission (core/resources.py). 0 = auto for the two budgets;
    # auto_cores / auto_ram_mb report what auto resolves to on THIS machine so
    # the UI can label the control ("auto (16 cores)") without guessing.
    max_concurrent_jobs: int
    max_total_cores: int
    max_total_ram_mb: int
    auto_cores: int
    auto_ram_mb: int
    theme: str                # "dark" | "light"
    theme_variant: str        # "shadcn" | "liquidglass"
    glass_level: str          # restrained|moderate|bold|vivid|maximal (liquidglass)
    wallpaper: str            # aurora|aqua|sunset|grape|graphite|ocean|custom
    eta_mode: str             # "conservative" | "eager"
    geo_graph_mode: str       # "all5" | "maxgrad"
    build_mode: str           # "beginner" | "expert" | "mlip" | "crest"
    crest_distro: str         # preferred WSL distro for CREST ("" = auto-detect)
    orca_valid: bool
    # Why the settings on screen are not the settings on disk. "" = they are.
    # Settings.save() is best-effort by design (P32 — a failed write must not
    # break the running app), but the UI used to answer "Saved." to a write
    # that never happened and lose everything at the next launch.
    save_error: str


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


class BasePythonPayload(TypedDict):
    """One interpreter that could serve as the base for a new MLIP venv."""
    python: str               # absolute path to python.exe
    version: str              # "3.12"
    supported: bool           # torch publishes wheels for this CPython


class MlipInstallOptionsPayload(TypedDict):
    """Bridge.get_mlip_install_options() — what a one-click env creation can be
    built from on this machine. Detection runs in the background (each candidate
    is launched to report its version), so the UI polls this."""
    state: str                # "checking" | "ready"
    base_pythons: "list[BasePythonPayload]"
    gpu: bool                 # an NVIDIA GPU is visible to the driver
    gpu_name: str             # e.g. "NVIDIA GeForce RTX 5080", or ""
    cuda_index: str           # torch wheel index its architecture needs, e.g. "cu128"
    # {key, label} per installable backend, in MLIP_BACKENDS order — the
    # install card builds its Backend dropdown from this rather than
    # hardcoding a second copy of the registry.
    backends: "list[dict]"


class MlipInstallPayload(TypedDict):
    """Bridge.get_mlip_install_status() / create_mlip_env() / cancel_mlip_install()
    — progress of the running (or last) environment creation. `step`/`steps` drive
    the progress line; `error` is why it failed, "" when it did not."""
    state: str                # "idle" | "running" | "done" | "error"
    step: int                 # 1-based, 0 before the first step
    steps: int                # total steps in the plan
    label: str                # what that step is doing
    error: str                # failure reason, or ""
    cancelled: bool


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
    install_error: str        # why the last install attempt failed, or ""


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
    # "" for a restricted run, else "a"/"b": an unrestricted calculation prints
    # two manifolds whose indices both start at 0, so idx alone is ambiguous.
    spin: str
    # "homo" | "lumo" | "": which frontier orbital this is, decided by the
    # parser across BOTH manifolds. The front-end used to re-derive it as "the
    # last row with occupation" — right for one manifold, wrong for two (P4:
    # one judgment, made where the data is).
    frontier: str


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
    cancelled: bool           # True only for a cancelled Open-file dialog (A2)
    path: str                 # set only on the path-addressed routes (_parse_path),
                              # so a result opened from disk stays plottable
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


class LogTailResult(_Ok, total=False):
    """get_output_tail — the last lines of a run's .out, for the Raw log's
    restored history after a reattach. "lines" is present on every branch."""
    error: str
    lines: "list[str]"
    file: str        # basename shown in the restored-history marker
    truncated: bool  # the file held more than the requested tail


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


# ---- appearance / 3D viewer / conformer export (desktop bridge) ---------------

class WallpaperResult(_Ok, total=False):
    """set_wallpaper_image — the ok branches carry "stored" (False = the input
    was empty / not an image / oversize, so the stored file was cleared, not
    written). The OSError branch keeps its bare ErrorPayload wire ({"error":
    ...}, no "ok"); "error" is declared here for the web/types.js mirror."""
    stored: bool
    error: str


class ExportResult(_Ok, total=False):
    """export_conformers / export_frames — how many .xyz files were written
    and into which folder."""
    error: str
    count: int
    folder: str


class MolFramePayload(TypedDict):
    """One 3D-viewer frame (rides FramesResult.frames; built by
    molview.frames_from_file / frames_from_folder). "xyz" is raw .xyz frame
    text 3Dmol parses directly."""
    label: str
    xyz: str
    energy: "float | None"    # absolute energy (Hartree), None when unknown


class FramesResult(_Ok, total=False):
    """get_conformer_frames / browse_xyz_folder — viewer frame lists.
    "folder" rides only browse_xyz_folder's success (the picked path, used as
    the export/favorites source); "cancelled" only its closed picker."""
    cancelled: bool
    error: str
    title: str
    folder: str
    frames: "list[MolFramePayload]"


class FavoritesResult(_Ok, total=False):
    """get_favorites / toggle_favorite — "labels" is present on every branch
    ([] on failure)."""
    error: str
    labels: "list[str]"


# ---- structure screening + editing (Build tab) --------------------------------

class StructureIssuePayload(TypedDict):
    """One finding from core.structure.check_geometry — 4 keys. "level" is
    "error" (ORCA will refuse this, or it is not the structure anyone meant) or
    "warn" (legitimate, but worth a look); "atoms" are 0-based indices the
    front-end highlights in the 3D preview."""
    level: str
    code: str
    message: str
    atoms: "list[int]"


class StructureCheckPayload(TypedDict):
    """check_structure — 7 keys, exactly core.structure.check_geometry's dict.
    "ok" is the *verdict* (no error-level issues), not a call-succeeded flag:
    the slot cannot fail, since an unreadable block is itself a finding.
    "electrons" is None when a symbol is not an element, so the count is
    genuinely unknown rather than guessed (P2)."""
    ok: bool
    n_atoms: int
    formula: str
    electrons: "int | None"
    n_bonds: int
    n_fragments: int
    issues: "list[StructureIssuePayload]"


class AtomOrderMismatchPayload(TypedDict):
    """One diverging index in a NEB endpoint pair — 3 keys, 0-based index."""
    index: int
    reactant: str
    product: str


class AtomOrderPayload(TypedDict):
    """compare_structures — 9 keys, exactly core.structure.compare_atom_order's
    dict. "mismatch_index" is the FIRST divergence (the three-key gate
    input_generator.check_neb_atom_order projects); "mismatches" is the whole
    list the Build tab tabulates, capped, with "n_mismatches" the true total."""
    ok: bool
    error: str
    mismatch_index: "int | None"
    n_reactant: int
    n_product: int
    n_mismatches: int
    mismatches: "list[AtomOrderMismatchPayload]"
    formula_reactant: str
    formula_product: str


class MeasureResult(_Ok, total=False):
    """measure_structure — the internal coordinate named by a 2/3/4-atom
    selection. "kind" is distance | angle | dihedral (Angstrom / degrees);
    both it and "value" ride only the success branch."""
    error: str
    kind: str
    value: float


class StructureEditResult(_Ok, total=False):
    """edit_structure — the edited coordinate block plus what it did. "xyz" and
    "moved" (the indices that changed, for the viewer's highlight) ride only
    success; "value" is the resulting measurement for a "set" op, absent for a
    fragment move. A refused edit (a ring bond, a broken selection) comes back
    ok=false with the reason in "error" — never a silently deformed structure."""
    error: str
    xyz: str
    moved: "list[int]"
    value: float


class FragmentResult(_Ok, total=False):
    """structure_fragment — the connected fragment an atom belongs to, as
    0-based indices. "indices" rides only success."""
    error: str
    indices: "list[int]"


# ---- orbital / density cubes (Results tab 3D viewer) --------------------------

class WorkspaceResultPayload(TypedDict):
    """One result found in the workspace. "path" is the artifact to parse
    ({name}.mlip.json for an MLIP run, else {name}.out); "queued" marks the ones
    the queue also holds — those must be parsed by NAME (kind dispatch), not by
    path (folder heuristic), so the front-end keeps the two routes apart.
    "kind" is a display hint only."""
    name: str
    path: str
    queued: bool
    kind: str                 # "orca" | "mlip" | "crest"


class WorkspaceResultsResult(_Ok, total=False):
    """list_workspace_results — every result on disk under the workspace root,
    newest first. "results" is present on every branch ([] on failure)."""
    error: str
    root: str
    results: "list[WorkspaceResultPayload]"


class PlotOptionsResult(_Ok, total=False):
    """get_plot_options — what a finished calculation can be visualized as.
    "kinds" holds only what this wavefunction actually supports (spin density
    needs an open-shell calc) and "cached" names the cubes already on disk, so
    the UI can mark which picks are instant. The orbital *list* is deliberately
    absent: the Results tab already holds it from the parse payload, and a
    second copy would be a second source of truth (P4). "base" is the filename
    stem the cubes are named from — the calc name for a queued source, the
    file's stem for one opened from disk."""
    error: str
    base: str
    has_gbw: bool
    open_shell: bool
    kinds: "list[str]"
    grids: "list[int]"
    default_grid: int
    cached: "list[str]"
    # ESP is the one kind whose cost is minutes rather than seconds, and the one
    # drawn from two cubes instead of one, so it carries its own defaults rather
    # than sharing the others': the grid it should open at, the density level its
    # surface is drawn at, and the half-width of its colour scale. All three are
    # conventions defined in orcamgr/cube.py and core/plot.py — sent here so the
    # front-end never holds a second copy (P4).
    esp_grid: int
    esp_surface_iso: float
    esp_range: float


class CubeJobPayload(TypedDict, total=False):
    """get_cube_status / generate_cube — progress of the running (or last) cube
    generation. "state" is idle | running | done | error; "cached" marks a
    result served from an existing file rather than a fresh orca_plot run."""
    state: str
    label: str
    error: str
    kind: str
    index: int
    operator: int
    grid: int
    cached: bool
    seconds: float


class CubeDataResult(_Ok, total=False):
    """get_cube_data — the finished cube, ready for 3Dmol's
    addVolumetricData(text, "cube"). "text" is the file verbatim; "title" is
    ORCA's own description of what it plotted, "isovalue"/"signed" seed the
    viewer's surface controls."""
    error: str
    text: str
    title: str
    npoints: int
    dims: "list[int]"
    bytes: int
    isovalue: float
    signed: bool


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
