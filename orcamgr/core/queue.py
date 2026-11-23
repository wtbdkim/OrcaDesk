"""
Calculation queue with explicit geometry dependencies.

The queue unit is a single *Calculation* (one ORCA run). A calculation's
geometry comes from one of two sources:

* DIRECT    -- coordinates provided up front (typically from an .xyz file)
* REFERENCE -- the optimized geometry of another calculation in the queue,
               named explicitly by the user

Calculations are DISPATCHED in queue order, and several may be in flight at
once — `_run_walk` admits a row when its declared cores and estimated memory
still fit the run's ResourceBudget (see core/resources.py), then runs it in its
own thread. `max_concurrent_jobs` defaults to 1, so the classic
one-at-a-time queue is what a user sees until they raise it. When a calculation
references another, the engine injects the referenced calculation's final
geometry at run time — and a row whose reference may still become DONE is
deferred rather than dispatched, which is what sequential order used to
guarantee for free.

Independently of its geometry, a calculation may run `after` another one (the
queue editor's edge). That is a dependency exactly like a reference, and the
edge may carry an approval gate: a row whose `after` is DONE but whose gate is
closed is *waiting* — skipped, left PENDING, never BLOCKED — and a run with
only waiting rows left ends normally (gate_waiting).

Failure propagation: if a calculation fails, any calculation that depends on it
(directly or transitively) is marked BLOCKED and skipped. Unrelated calculations
continue. This is dependency-scoped, not whole-queue.

The edge into a calculation may also carry an energy condition (filter_de /
filter_top, judged by _judge_filter before the approval gate, once every
member of its stack has a settled parent): a calculation that fails it, a
conformer reference (ref_point conf:<k>) past the end of its CREST ensemble,
and every dependent of either are FILTERED — terminal, never run, never
BLOCKED or FAILED.

A conformer PLACEHOLDER (ref_point conf:all: "every conformer the search
finds", put after a CREST search that has not run) is never run itself. When
its search is DONE with N conformers, it and every calculation hanging off it
are replaced by N copies (expand_conformer_placeholders) -- the one place a run
rewrites the queue.

A FAILED calculation is locked (P24): it never re-enters the run loop on its
own. Only CANCELLED (a deliberate user stop, not a failure) re-runs; a FAILED or
DONE one runs again only when the queue editor puts a new set on it behind an
overwrite warning (store rerun_calc), which makes it a PENDING row again.

Folder layout (core/rundir.py): a calculation in a system runs in
``{workspace}/{system name}/{name}/``, a standalone one in ``{workspace}/{name}/``.
The folder is decided when the calculation first starts and recorded on it
(``Calculation.run_dir``); from then on it is found there (or through its
output_path), whatever happens to its system's name or its place. Names are
unique within a queue (enforced by the store — ``QueueStore`` rejects
duplicates, case-insensitively), so folders never collide.

The engine is GUI-agnostic: it communicates only through callbacks.
"""

from __future__ import annotations

import copy
import dataclasses
import os
import re
import shutil
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from .input_generator import (
    StepConfig, build_input, render_raw_input, raw_coordinate_block,
    raw_geometry_mismatch, GEOMETRY_PLACEHOLDER, HESS_PLACEHOLDER,
    render_template_text, template_hess_need, check_neb_atom_order,
    _xyz_elements, irc_hess_input_name, irc_hess_is_path, SCAN_KINDS, normalize_scan,
    is_level_text, template_option_conflicts,
)
from .structure import parse_block
from .resources import (ResourceBudget, declared_cores, estimated_ram_mb,
                        numfreq_rank_cap, ram_headroom_mb, raw_nprocs, uses_gpu,
                        worker_threads)
from .runner import OrcaRunner, OrcaRunError, OrcaCancelled, OrcaDetached, OrcaHung
from .parser import parse_file, ParseResult
from .procutil import process_matches
from .rundir import (folder_name_problem, names_with_output, planned_run_dir,
                     result_folder, run_dir_of, run_folder)
from .xyzutil import as_xyz_file
import logging
from ..remote.devices import device_prefers, resolve_device
from ..remote.runner import (RemoteCancelUnreachable, RemoteCrestRunner,
                             RemoteOrcaRunner, RemoteRequeue, RemoteUnavailable,
                             launch_stamp, remote_dir_for, remote_safe_name)


# How long the dispatcher sleeps between re-scans while it waits for a job to
# finish. A finishing job also notifies _slot_cv, so this is only a backstop.
_SLOT_POLL = 0.25
# How long _start_job waits for a row it already admitted to fit again before
# handing it back to the scan. The pick's memory test reads the machine's live
# free RAM, which can fall between the pick and the start; waiting indefinitely
# there parks the single dispatcher on one row and blocks every smaller row
# behind it.
_REFIT_GIVE_UP = 5.0

#: a pool's run state (this machine, or one remote device) -- see
#: QueueEngine._pool_state
POOL_RUNNING = "running"
POOL_DRAINING = "draining"
POOL_STOPPED = "stopped"
POOL_STATES = (POOL_RUNNING, POOL_DRAINING, POOL_STOPPED)


class CalcState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"       # a dependency failed, so this never ran
    CANCELLED = "cancelled"
    # the energy condition on the edge into it (filter_de / filter_top) or a
    # conformer reference past the end of its CREST ensemble screened it out,
    # or a calc it depends on was filtered: terminal, never ran, no output.
    # Frozen like DONE; only a change of its filter reopens it (store).
    FILTERED = "filtered"


class GeometrySource(str, Enum):
    DIRECT = "direct"
    REFERENCE = "reference"


@dataclass
class Calculation:
    name: str                              # unique; names its run folder
    # opt | ts_opt | freq | ts_freq | opt_freq | ts_opt_freq | irc | tddft |
    # sp | general | nmr | neb_ts | mlip_opt | crest_conf
    # (mlip_* runs outside ORCA in the user's MACE env; crest_* runs outside ORCA
    #  in a POSIX shell — WSL on Windows — see orcamgr/mlip/ and orcamgr/crest/)
    kind: str
    config: StepConfig
    charge: int = 0
    multiplicity: int = 1

    geometry_source: GeometrySource = GeometrySource.DIRECT
    xyz: str = ""                          # used when source == DIRECT
    ref_name: str = ""                     # used when source == REFERENCE

    # raw mode: user has hand-edited the full .inp text. When True, the engine
    # uses raw_text verbatim (substituting the geometry placeholder if present)
    # instead of generating the .inp from config.
    is_raw: bool = False
    raw_text: str = ""

    state: CalcState = CalcState.PENDING
    message: str = ""
    result: Optional[ParseResult] = None
    output_path: str = ""

    # set while RUNNING: the OS identity of the detached ORCA process, persisted
    # so a run left going when ORCAdesk closes can be reattached on next launch.
    pid: Optional[int] = None
    create_time: Optional[float] = None

    # set while RUNNING once ORCA has printed a fatal marker: the run is lost,
    # but its process is still alive (it may end on its own, or hang until the
    # runner's watchdog ends it). The row stays RUNNING — cores are still held
    # and Stop still applies — and reads as failed. Not persisted: a reattach
    # finds the marker in the .out again.
    faulted: bool = False
    # a DONE or FAILED calc the queue editor put back to PENDING with a new
    # set (store rerun_calc, behind an overwrite warning; P24): the output in its
    # folder is the old run's, which the user chose to overwrite -- so it is
    # never offered, or given, keep-existing. Meaningful only until the row
    # starts (store.is_rerun: PENDING / BLOCKED); persisted in the session.
    rerun: bool = False
    # where it runs, relative to the workspace ("{system}/{name}" or "{name}",
    # core/rundir.py): recorded by the engine when the calc FIRST starts, then
    # kept -- through a system rename, a move to another row, a CANCELLED
    # re-run, a rerun_calc -- so a calc that has run is always found where it
    # ran. "" = not started yet: the folder follows the system's current name.
    # Persisted in the session; never read from a client (calc_from_dict).
    run_dir: str = ""
    # where it runs (orcamgr/remote/): "" = this machine, else the ~/.ssh/config
    # Host alias of a remote device. Recorded by the engine when the calc
    # starts -- with it, pid/create_time name the REMOTE process (its /proc
    # start time) -- and remote_dir is the absolute folder of that launch on
    # the device ({workdir}/{run_dir}.{stamp}). Persisted in the session (a
    # reattach needs both); never read from a client (calc_from_dict).
    device: str = ""
    remote_dir: str = ""
    # set while RUNNING when the device has stopped answering (two syncs in a
    # row could not reach it): the row
    # shows the orange "Disconnected" box (design D7). Not persisted: a
    # reattach judges it again.
    disconnected: bool = False

    # ---- the queue editor's graph (all default to "standalone, as today") ----
    # system: the lane (one molecule) this calc belongs to, a QueueStore system
    # id; "" = a standalone calc. node: the block within the lane — the members
    # of a stack share it. col/row: the block's grid position. Layout only:
    # none of the four changes what runs or when.
    system: str = ""
    node: str = ""
    col: int = 0
    row: int = 0
    # after: the calc this one runs after (the edge into it), "" = a root. A
    # dependency exactly like a geometry reference (see _deps_ready /
    # _blocked_by) but independent of where the geometry comes from.
    after: str = ""
    # gate on that edge: "auto" runs as soon as `after` is DONE; "approval"
    # waits for `approved` — unless `auto_pass` and `after` is a frequency
    # calc whose imaginary-mode count is the expected one (gate_auto_passes).
    gate: str = "auto"
    auto_pass: bool = True
    approved: bool = False
    # which geometry of ref_name: "" its final one, "irc_f"/"irc_b" the end
    # points ORCA writes for an IRC ({ref}_IRC_F.xyz / {ref}_IRC_B.xyz),
    # "conf:<k>" conformer k (1 = the lowest) of a CREST search, "conf:all"
    # every conformer of one that has not run (a placeholder, never run
    # itself: see expand_conformer_placeholders)
    ref_point: str = ""
    # the energy condition on the edge into it (judged by the engine when its
    # dependencies are otherwise ready, before the approval gate; see
    # QueueEngine._judge_filter): filter_de, a window in kcal/mol above the
    # lowest of its stack (None = off); filter_top, only the N lowest of its
    # stack pass (0 = off). A calc that fails it is FILTERED.
    filter_de: Optional[float] = None
    filter_top: int = 0
    # display only: the inp set (state/inpsets.py) the input came from
    set_name: str = ""


# ---- callbacks ----------------------------------------------------------
@dataclass
class QueueCallbacks:
    # (message, level, calc_name). `calc_name` is "" for engine-level lines and
    # the owning calculation's name for anything a specific job produced — with
    # several jobs in flight their tailed output interleaves in one buffer, and
    # only an explicit tag can route a line back to its job (the front-end log
    # filter and the per-job convergence graphs both key off it).
    log: Callable[..., None] = lambda msg, level, calc="": None
    calc_update: Callable[[int, "Calculation"], None] = lambda i, c: None
    # remote devices (orcamgr/remote/): a cancel that could not reach its
    # device leaves this entry for the store's clean-up list; a device found
    # reachable again during a run is reported so that list can be settled
    remote_cleanup: Callable[[dict], None] = lambda entry: None
    device_reachable: Callable[[str], None] = lambda alias: None
    # the conformer placeholders of a finished CREST search: (crest name, N,
    # casefolded names that already hold output on disk) -> the report of
    # expand_conformer_placeholders, or None. Wired to the store's
    # expand_conformer_placeholders (it saves the session and bumps the
    # version); None = the engine rewrites the list it walks itself (a
    # standalone engine, as in the unit tests).
    expand_placeholders: "Optional[Callable[[str, int, set], Optional[dict]]]" = None


@dataclass
class _JobSlot:
    """One in-flight job: the resources it reserved and the runner to stop it.

    `cores`/`ram_mb` are the admission cost taken from the budget for as long as
    the job runs; `runner` is whichever backend runner it registered (see
    `_use_runner`), so cancel()/detach() can reach every job at once.
    """
    name: str
    cores: int = 0
    ram_mb: int = 0
    gpu: bool = False
    runner: object = None
    orca_launched: bool = False
    # where it runs: "" = this machine, else a remote device's alias. Each
    # device is its own admission pool (_fits sums only its own slots).
    device: str = ""
    # the calculation's kind: a remote CREST search needs CREST on the device
    # and counts threads (-T), not MPI ranks (_fits_remote)
    kind: str = ""


def _xyz_from_geometry(result: ParseResult) -> str:
    return "\n".join(
        f"{a.symbol} {a.x:.10f} {a.y:.10f} {a.z:.10f}" for a in result.geometry
    )


# ORCA 6 restart files it will pick up on its own (AutoStart): a {base}.gbw
# left beside {base}.inp becomes the SCF's initial guess with nothing in the
# input asking for it.
_RESTART_SUFFIXES = (".gbw", ".ges")


def _guess_signature(inp_text: str) -> str:
    """What a restart file in this folder must have been produced from — the
    charge/multiplicity of the ``* xyz`` line plus the element sequence of its
    coordinate block. '' when the text has no readable block (an ``* xyzfile``
    reference, a hand-written input we cannot judge), which callers must treat
    as "cannot tell", never as a match."""
    header = ""
    for ln in inp_text.splitlines():
        low = ln.strip().lower()
        if low.startswith("*") and low[1:].strip().startswith("xyz") and "xyzfile" not in low:
            header = " ".join(low[1:].split()[1:3])   # charge, multiplicity
            break
    els = [e.capitalize() for e in _xyz_elements(raw_coordinate_block(inp_text))]
    return f"{header}|{','.join(els)}" if els else ""


# What an output MUST contain to be a given kind's result. Only sections whose
# absence is unambiguous are listed: a freq output without a frequency table is
# not a frequency calculation, whatever else it is. (Convergence and the
# imaginary-mode count stay with validate_result — this is identity, not quality.)
def _has_vibrational_analysis(result: ParseResult) -> bool:
    """Whether the output holds a completed frequency analysis.

    An empty mode list is the answer, not a missing section, for a single atom:
    its 3N = 3 modes are all translations, which ORCA prints as 0.00 cm**-1 and
    the parser drops, while the thermochemistry (translational + electronic) is
    still computed. Rejecting it left no way to give an ion like Cl- the Gibbs
    free energy a reaction profile sums — and FAILED is locked (P24)."""
    if result.frequencies:
        return True
    return bool(result.has_frequencies) and len(result.geometry or ()) == 1


def _missing_section_for_kind(kind: str, result: ParseResult) -> str:
    kind = kind or ""
    if kind in ("freq", "ts_freq", "opt_freq", "ts_opt_freq", "mlip_freq",
                "mlip_opt_freq") and not _has_vibrational_analysis(result):
        return "frequencies"
    if kind in ("opt", "ts_opt", "opt_freq", "ts_opt_freq", "mlip_opt",
                "mlip_opt_freq") and not result.is_optimization:
        return "optimization"
    if kind == "tddft" and not result.has_tddft:
        return "TD-DFT transitions"
    if kind == "nmr" and not result.nmr_shieldings:
        return "NMR shieldings"
    if kind == "crest_conf" and not getattr(result, "conformers", None):
        return "conformers"
    if kind in ("neb_ts", "irc") and not result.neb_path:
        return "reaction path"
    return ""


def _wants_scan(calc: Calculation) -> bool:
    """Whether this calculation's generated input carries a %geom Scan — the
    same rule build_input writes it by, and render_template_text for a level
    block (whose options are its config)."""
    cfg = calc.config
    return ((not calc.is_raw or is_level_text(calc.raw_text))
            and cfg.kind in SCAN_KINDS and bool(normalize_scan(cfg.geom_scan)))


def _kept_result_matches(calc: Calculation, result: ParseResult) -> str:
    """'' when an on-disk result plausibly belongs to this calc, else a
    human-readable mismatch reason. Workspace folders are never deleted and
    queue names are only unique within the *current* queue, so a new calc that
    reuses a removed calc's name could otherwise adopt the old output as its
    own through the keep-existing run option. Identity is judged by the atom
    element sequence (coordinates move during a run; the atom list does not);
    reference-geometry calcs are skipped — their structure isn't knowable
    without running."""
    if not result.geometry:
        return ""
    # Element sequence alone does not make an output this calculation's result:
    # a folder left by an `opt` run has the right atoms for a `freq` calc of the
    # same molecule, and the freq rule ("no imaginary modes") is vacuously true
    # of an output with no frequencies at all — so the opt output was adopted as
    # a finished frequency job, with no frequencies and no thermochemistry.
    # Check for the section the kind is defined by.
    missing = _missing_section_for_kind(calc.kind, result)
    if missing:
        return f"existing result has no {missing}"
    # A relaxed scan is an optimization too, and its folder holds an output
    # that passes every opt check — but its geometry is the LAST scan point,
    # held there by the scan, not a minimum. So a scan and a plain
    # optimization are told apart in both directions. (A raw calc's scan is
    # in its text, not in geom_scan, so only the scan-wanted half applies.)
    if _wants_scan(calc) and not result.scan_points:
        return "existing result has no relaxed surface scan"
    if (not calc.is_raw and not _wants_scan(calc) and result.scan_points
            and calc.kind in ("opt", "ts_opt", "opt_freq", "ts_opt_freq")):
        return "existing result is a relaxed surface scan, not an optimization"
    expected = ""
    if calc.geometry_source == GeometrySource.DIRECT:
        if calc.is_raw and GEOMETRY_PLACEHOLDER not in calc.raw_text:
            # A no-placeholder raw calc runs its embedded coordinate block
            # verbatim, so calc.xyz may be stale against an edited text. When
            # the text has no inline block at all — "* xyzfile 0 1 mol.xyz", a
            # geometry in a file we cannot see — the structure is simply not
            # knowable here, and falling back to that stale calc.xyz answered
            # "different structure" about a result that matched perfectly: the
            # run the user asked to KEEP was recomputed over it. Unknowable is
            # the same case as a reference calc, which is skipped.
            expected = raw_coordinate_block(calc.raw_text)
            if not expected.strip():
                return ""
        else:
            expected = calc.xyz or ""
    # element symbols compare case-insensitively (ORCA accepts "CL"/"cl" and
    # echoes the canonical "Cl") — a casing difference is not a mismatch
    exp = [e.capitalize() for e in _xyz_elements(expected)]
    if not exp:
        return ""
    got = [a.symbol.capitalize() for a in result.geometry]
    if len(exp) != len(got):
        return ("existing result is for a different structure "
                f"({len(got)} atoms on disk vs {len(exp)} in this calculation)")
    if exp != got:
        i = next(i for i, (a, b) in enumerate(zip(exp, got)) if a != b)
        return ("existing result is for a different structure "
                f"(element sequence differs at atom #{i + 1}: "
                f"{got[i]} on disk vs {exp[i]} here)")
    return ""


def _imaginary_detail(result: ParseResult) -> str:
    """The imaginary frequencies of a result as a short "cm^-1" list (up to 5),
    for the validation messages below. "none" when there are none, so a caller
    reporting an unexpected count always has something to print.

    Taken from the modes ORCA MARKED imaginary, not re-derived by sign: ORCA
    prints a numerically-zero imaginary mode as "-0.00", and `-0.0 < 0` is
    False — which produced the self-contradicting "1 imaginary frequency
    (cm^-1: none)". Falls back to the sign test for a result parsed before the
    marked list existed (a session restored from an older build).
    """
    imag = list(getattr(result, "imaginary_frequencies", None) or
                [f for f in result.frequencies if f < 0])
    return ", ".join(f"{v:.2f}" for v in imag[:5]) if imag else "none"


# Every calculation kind this build knows. `kind` is not a free-text field the
# way a functional or basis name is (P30): it selects the PIPELINE (ORCA vs the
# MLIP worker vs CREST in WSL), the per-kind validation below, and what the
# Results tab shows. An unknown one was taken verbatim from an HTTP payload and
# run through the ORCA runner with StepConfig defaults — a calculation nobody
# asked for, that no rule here can judge, and that the Build tab cannot open.
KNOWN_KINDS = frozenset({
    "sp", "opt", "ts_opt", "freq", "ts_freq", "opt_freq", "ts_opt_freq",
    "irc", "tddft", "nmr", "neb_ts", "general",
    "mlip_opt", "mlip_sp", "mlip_freq", "mlip_opt_freq",
    "crest_conf",
})

# ORCA kinds whose whole point is a Hessian. neb_ts is deliberately NOT here:
# its FREQ step is optional (the check below is gated on frequencies existing).
_FREQ_KINDS = ("freq", "ts_freq", "opt_freq", "ts_opt_freq")

# The edge gate (Calculation.gate) and the geometry points of a reference
# (Calculation.ref_point) are closed sets: each value selects engine behaviour.
GATES = ("auto", "approval")
REF_POINTS = ("", "irc_f", "irc_b")
# ...plus one open family: "conf:<k>", conformer k of a CREST ensemble, k >= 1
# and 1 the lowest-energy one -- the numbering of ParseResult.conformers[].index
# and of the exported {name}_c<k>.xyz (crest/parser.py, crest/export.py).
_CONF_REF_RE = re.compile(r"conf:[1-9][0-9]*")   # used with fullmatch
# ...and one closed value: "conf:all", every conformer of a CREST search that
# has not run yet -- a PLACEHOLDER, never run itself. When the search is DONE
# with N conformers it is replaced, with every calc hanging off it, by N copies
# (expand_conformer_placeholders).
CONF_ALL = "conf:all"
# The kinds whose result is a conformer ensemble a conf:<k> reference reads.
CONFORMER_KINDS = ("crest_conf",)
# Hartree -> kcal/mol (CODATA 2018), the energy filter's unit
HARTREE_TO_KCAL_MOL = 627.5094740631


def is_ref_point(value) -> bool:
    """Whether ``value`` is a ref_point the engine knows: one of REF_POINTS,
    ``conf:<k>`` (k a whole number >= 1, no sign, no leading zero) or
    ``conf:all`` (CONF_ALL)."""
    return isinstance(value, str) and (value in REF_POINTS or value == CONF_ALL
                                       or bool(_CONF_REF_RE.fullmatch(value)))


def is_conf_placeholder(calc) -> bool:
    """Whether ``calc`` is a conformer placeholder: a geometry reference to a
    calc with ref_point ``conf:all``. It is never run; its CREST search, once
    DONE, replaces it (expand_conformer_placeholders)."""
    return (getattr(calc, "ref_point", "") == CONF_ALL
            and calc.geometry_source == GeometrySource.REFERENCE
            and bool(calc.ref_name))


def conf_index(ref_point) -> int:
    """k of a ``conf:<k>`` ref_point, else 0."""
    if isinstance(ref_point, str) and _CONF_REF_RE.fullmatch(ref_point):
        return int(ref_point[5:])
    return 0


def has_filter(calc) -> bool:
    """Whether the edge into ``calc`` carries an energy condition."""
    return (getattr(calc, "filter_de", None) is not None
            or int(getattr(calc, "filter_top", 0) or 0) > 0)


_remote_log = logging.getLogger("orcamgr.remote")

# What a calc cancelled on a device that could not be reached says until the
# store's clean-up list has settled its remote job (design §6.3). The store
# puts the plain "Cancelled by user." back when it is done.
CLEANUP_PENDING_MESSAGE = ("Cancelled — the compute device could not be reached; "
                           "its remote job is tidied up when the device is back.")

# What a row a SPOT device gave back says until it starts again (a fresh
# launch clears the message): it is PENDING, to run anew -- here or elsewhere.
REQUEUED_MESSAGE = "Re-queued: the spot device stopped answering."

# A drive-letter path in a hand-written input (%moinp "D:/x.gbw"): a file on
# THIS machine that a remote device does not have.
_DRIVE_PATH_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")


def remote_capable(calc, run_dir: str) -> bool:
    """Whether ``calc`` may run on a remote device at all (design §2): an
    ORCA kind, or a CREST search (on a device that has CREST -- _fits_remote
    asks per device); never MLIP (it needs the user's own Python env, which
    only this machine has); no GPU, no local absolute path in a raw input,
    and a run folder the transport carries safely (``run_dir`` is the folder
    it runs in, relative to the workspace). Anything else runs here."""
    kind = getattr(calc, "kind", "") or ""
    if kind.startswith("mlip") or uses_gpu(calc):
        return False
    if getattr(calc, "is_raw", False) and _DRIVE_PATH_RE.search(calc.raw_text or ""):
        return False
    return remote_safe_name(run_dir) and remote_safe_name(calc.name)
# ref_point -> the file ORCA writes for that IRC end, beside {ref}.out
_IRC_END_FILES = {"irc_f": "_IRC_F.xyz", "irc_b": "_IRC_B.xyz"}
# The kinds whose run can write those end files: an IRC, and a "general" calc
# (a raw input of any content). Any other parent never writes them, so a
# ref_point on it is refused when it is added (QueueStore) rather than left
# to block at run time.
IRC_END_KINDS = ("irc", "general")


class OrcaBlocked(OrcaRunError):
    """A pre-launch problem the user fixes by editing the row or running its
    parent first -- a missing parent Hessian a template asks for, a missing
    IRC end file. Nothing launched, so the row is stamped BLOCKED (editable,
    re-tried on the next Run), never FAILED (locked, P24)."""


class OrcaFiltered(OrcaRunError):
    """Nothing to run, by design rather than by fault: a conformer reference
    past the end of the CREST ensemble (``conf:<k>`` with k > N). Nothing
    launched; the row is stamped FILTERED, never FAILED."""


def ref_point_problem(calc: "Calculation", parent: "Optional[Calculation]") -> str:
    """The refusal for a ref_point irc_f / irc_b whose parent's kind cannot
    write IRC end points, or a conf:<k> whose parent is not a conformer search
    (CONFORMER_KINDS), or "" (no ref_point, no parent in hand, or the right
    kind of parent)."""
    if parent is None or calc.geometry_source != GeometrySource.REFERENCE:
        return ""
    if calc.ref_point == CONF_ALL:
        if parent.kind in CONFORMER_KINDS:
            return ""
        return (f"'{calc.name}' starts from every conformer of '{parent.name}', "
                f"but '{parent.name}' is not a CREST conformer search "
                f"({parent.kind}) and has no conformers.")
    k = conf_index(calc.ref_point)
    if k:
        if parent.kind in CONFORMER_KINDS:
            return ""
        return (f"'{calc.name}' starts from conformer {k} of '{parent.name}', "
                f"but '{parent.name}' is not a CREST conformer search "
                f"({parent.kind}) and has no conformers.")
    if calc.ref_point not in _IRC_END_FILES:
        return ""
    if parent.kind in IRC_END_KINDS:
        return ""
    side = "forward" if calc.ref_point == "irc_f" else "backward"
    return (f"'{calc.name}' starts from the {side} IRC end point of "
            f"'{parent.name}', but '{parent.name}' is not an IRC "
            f"({parent.kind}) and writes no IRC end points.")


def template_hess_problem(name: str, kind: str, raw_text: str, set_name: str,
                          after: str, has_reference: bool, cfg=None) -> str:
    """The refusal for an input that needs a parent's Hessian but has no
    parent to take it from, or "". A text naming {{HESS}} takes the .hess of
    the calc it runs after or of its geometry source; so does an inp-set IRC
    whose own %irc block reads a Hessian (InitHess read + a relative
    Hess_Filename, staged from the referenced calc's folder), and a level IRC
    whose options (``cfg``) read one by name. A picked .hess (an absolute
    path, copied in at launch) needs no parent. With neither an `after` nor a
    geometry reference the run could only fail."""
    if after or has_reference:
        return ""
    text = raw_text or ""
    needs = template_hess_need(text, kind, cfg) == "required"
    if (not needs and kind == "irc" and cfg is not None and is_level_text(text)
            and (cfg.irc_init_hess or "").strip().lower() == "read"):
        picked = (cfg.irc_hess_file or "").strip()
        needs = bool(picked) and not irc_hess_is_path(picked)
    if not needs and set_name and kind == "irc":
        hess = QueueEngine._raw_irc_hess_file(text)
        needs = bool(hess) and not Path(hess).is_absolute()
    if not needs:
        return ""
    return (f"'{name}' reads a Hessian from the calculation it runs after "
            "(or its geometry source), but it has neither. Connect it after a "
            "frequency calculation first.")

# The kinds an approval gate may pass by itself after (Calculation.auto_pass):
# every kind whose result carries a frequency table, and the imaginary-mode
# count each one is supposed to have.
_GATE_FREQ_KINDS = _FREQ_KINDS + ("mlip_freq", "mlip_opt_freq")


def _expected_imaginary(kind: str) -> int:
    return 1 if kind in ("ts_freq", "ts_opt_freq") else 0


def gate_auto_passes(calc: "Calculation", parent: "Calculation") -> bool:
    """Whether `calc`'s approval gate passes by itself: auto_pass is on, the
    parent is a frequency calculation, and its imaginary-mode count is the one
    its kind expects (1 for a TS frequency, else 0).

    The count is read from the parent's result. Without one (a DONE parent
    restored from a session) the answer is what DONE already says: a
    frequency calc reaches DONE only through validate_result, and for every
    kind in _GATE_FREQ_KINDS that rule IS the expected count — so the two
    cannot disagree. Nothing is parsed here: the engine asks under the store
    lock (P37), and a large frequency output took ~1 s to read, stalling every
    snapshot the UI polls."""
    if not calc.auto_pass or parent.kind not in _GATE_FREQ_KINDS:
        return False
    result = parent.result
    if result is None:
        return parent.state == CalcState.DONE
    return int(getattr(result, "n_imaginary", 0) or 0) == _expected_imaginary(parent.kind)


def _parent_names(calc: "Calculation") -> "list[str]":
    """The calcs this one depends on: its geometry reference (when it has one)
    and the calc it runs `after`. Either may be "" / absent."""
    out = []
    if calc.geometry_source == GeometrySource.REFERENCE and calc.ref_name:
        out.append(calc.ref_name)
    if calc.after and calc.after not in out:
        out.append(calc.after)
    return out


def gate_waiting(calc: "Calculation", parent: "Optional[Calculation]") -> bool:
    """True when `calc` is held at its approval gate: it would run (PENDING or
    CANCELLED), the calc it runs after is DONE, its gate is "approval", it is
    not approved and the gate does not pass by itself. Such a row is skipped
    by the run — never BLOCKED — and a run left with only those ends
    normally. One rule for the engine and the snapshot's `waiting` flag."""
    if calc.gate != "approval" or calc.approved or parent is None:
        return False
    if calc.state not in (CalcState.PENDING, CalcState.CANCELLED):
        return False
    if parent.state != CalcState.DONE:
        return False
    return not gate_auto_passes(calc, parent)


# ---- conformer placeholders (ref_point conf:all) --------------------------
# A placeholder that has not been replaced yet: never run (it is not
# dispatched), so it is still in an editable state. BLOCKED counts -- a
# refusal stamps it, and the next run tries again.
_PLACEHOLDER_STATES = (CalcState.PENDING, CalcState.CANCELLED, CalcState.BLOCKED)
# what a copy never inherits: it is a new row that has never started
_UNSTARTED = dict(state=CalcState.PENDING, message="", result=None,
                  output_path="", pid=None, create_time=None, faulted=False,
                  rerun=False, run_dir="", device="", remote_dir="",
                  disconnected=False)


def conformer_placeholder_chain(calcs: "list[Calculation]", crest_name: str):
    """(placeholders, downstream): the conformer placeholders on the calc
    called ``crest_name`` still to be replaced, and every calc that depends on
    one of them -- by its geometry reference or its `after`, transitively --
    each in list order. Found to a fixpoint over the whole list, so the order
    rows sit in (a child before its parent) does not matter. A placeholder
    that hangs off another one is a placeholder (a root), not downstream."""
    roots = [c for c in calcs
             if is_conf_placeholder(c) and c.ref_name == crest_name
             and c.state in _PLACEHOLDER_STATES]
    if not roots:
        return [], []
    ids = {id(c) for c in roots}
    names = {c.name for c in roots}
    grew = True
    while grew:
        grew = False
        for c in calcs:
            if id(c) not in ids and any(p in names for p in _parent_names(c)):
                ids.add(id(c))
                names.add(c.name)
                grew = True
    root_ids = {id(c) for c in roots}
    return roots, [c for c in calcs if id(c) in ids and id(c) not in root_ids]


def expand_conformer_placeholders(calcs: "list[Calculation]", crest_name: str,
                                  n: int, busy: "Callable[[str], bool]" = lambda name: False,
                                  disk_taken=()) -> "Optional[dict]":
    """Replace the conformer placeholders of the DONE CREST search called
    ``crest_name`` (N = ``n`` conformers), and everything hanging off them, by
    N copies -- IN PLACE on ``calcs`` (the store's own list, walked by a
    running engine: P29), as one change. The caller holds the lock that
    guards ``calcs``. The one place a run rewrites the queue.

    Placeholder P becomes P_c1 ... P_cN (ref_point conf:k, everything else
    P's: node, lane, place, gate, filter fields, set, config, charge /
    multiplicity); each downstream calc D becomes D_c1 ... D_cN, its `after`
    and geometry reference re-pointed to the k-th copy of a parent in the
    chain (a parent outside it is kept), keeping D's node, so each step stays
    one stack of N. Each row's copies take its place in the list. Names: a
    base whose N copies are all free (case-insensitively, against the rows
    that stay and ``disk_taken``, names whose folder already holds output) --
    D, else D2, D3 ... -- the editor's rule for a stack. Every copy is a new
    row that has never started; the replaced rows never started either (a
    placeholder is never run, and what hangs off it waits for it), so they
    simply leave the list -- nothing on disk is touched.

    N = 0: P and its chain are FILTERED instead ("CREST found no
    conformers"). A chain that holds a row that has started (or is being
    started: ``busy``), or copy names that are not usable folder names, is
    refused: every P is BLOCKED with the reason (editable; tried again on
    the next run) and nothing else changes.

    Returns None when there is nothing to do (no placeholder left -- a
    second call is a no-op -- or the search is not a DONE one), else
    {placeholders, n, members, downstream, blocked}: the placeholders' names,
    N, how many member copies and downstream copies were made, and the
    refusal ("" when it expanded)."""
    crest = next((c for c in calcs if c.name == crest_name), None)
    if crest is None or crest.state != CalcState.DONE or crest.kind not in CONFORMER_KINDS:
        return None
    roots, down = conformer_placeholder_chain(calcs, crest_name)
    if not roots:
        return None
    chain = roots + down
    root_ids = {id(c) for c in roots}
    n = max(0, int(n))
    report = {"placeholders": [p.name for p in roots], "n": n,
              "members": 0, "downstream": 0, "blocked": ""}

    def refuse(why: str) -> dict:
        for p in roots:
            p.state = CalcState.BLOCKED
            p.message = f"Skipped: {why}."
        report["blocked"] = why
        return report

    started = next((c for c in chain
                    if c.state in (CalcState.RUNNING, CalcState.DONE, CalcState.FAILED)
                    or busy(c.name)), None)
    if started is not None:
        return refuse(f"'{started.name}' hangs off it and has already started, "
                      f"so it cannot become one per conformer of '{crest_name}'")
    if n == 0:
        for c in chain:
            c.state = CalcState.FILTERED
            c.pid = c.create_time = None
            c.message = ("Filtered: CREST found no conformers" if id(c) in root_ids
                         else "Filtered: what it depends on was filtered")
        return report

    chain_ids = {id(c) for c in chain}
    taken = {c.name.casefold() for c in calcs if id(c) not in chain_ids}
    taken |= {str(x).casefold() for x in disk_taken}
    base_of: "dict[str, str]" = {}
    for c in chain:
        base, i = c.name, 2
        while any(f"{base}_c{k}".casefold() in taken for k in range(1, n + 1)):
            base, i = f"{c.name}{i}", i + 1
        problem = folder_name_problem(f"{base}_c{n}")
        if problem:
            return refuse(f"'{base}_c{n}' cannot name a calculation ({problem})")
        base_of[c.name] = base
        taken |= {f"{base}_c{k}".casefold() for k in range(1, n + 1)}

    def copy_of(c: Calculation, k: int) -> Calculation:
        def remap(nm: str) -> str:
            return f"{base_of[nm]}_c{k}" if nm in base_of else nm
        root = id(c) in root_ids
        is_ref = c.geometry_source == GeometrySource.REFERENCE
        return dataclasses.replace(
            c, name=f"{base_of[c.name]}_c{k}", config=copy.deepcopy(c.config),
            ref_point=f"conf:{k}" if root else c.ref_point,
            ref_name=c.ref_name if root or not is_ref else remap(c.ref_name),
            after=remap(c.after) if c.after else "", **_UNSTARTED)

    out: "list[Calculation]" = []
    for c in calcs:
        if id(c) in chain_ids:
            out.extend(copy_of(c, k) for k in range(1, n + 1))
        else:
            out.append(c)
    calcs[:] = out
    report["members"] = n * len(roots)
    report["downstream"] = n * len(down)
    return report


def validate_result(calc: Calculation, result: ParseResult) -> None:
    """Per-kind result validation, raising OrcaRunError on a bad result. Module-
    level so both the live engine and session reconciliation (after a restart)
    apply identical rules: opt/ts_opt must converge; freq must have zero
    imaginary modes; ts_freq exactly one; neb_ts (when freqs were computed)
    exactly one."""
    if not result.terminated_normally:
        # MLIP/CREST jobs don't produce an ORCA .out; their failure reason comes
        # from the parsed result's error_message directly.
        if calc.kind.startswith("mlip"):
            raise OrcaRunError(result.error_message or "MLIP run did not finish.")
        if calc.kind.startswith("crest"):
            raise OrcaRunError(result.error_message or "CREST did not finish.")
        raise OrcaRunError(
            f"ORCA did not terminate normally. {result.error_message or 'Check the .out file.'}"
        )
    # CREST conformer search: a valid result terminated normally and produced at
    # least one conformer (no ORCA-style per-kind keyword checks).
    if calc.kind.startswith("crest"):
        if not result.conformers:
            raise OrcaRunError("CREST produced no conformers.")
        return
    # MLIP: convergence for the opt kinds; the imaginary-mode rule for the freq
    # kinds (same "a true minimum has zero imaginary modes" bar as ORCA freq).
    # No ORCA-style keyword checks — MLIP produces no ORCA .out.
    if calc.kind.startswith("mlip"):
        if calc.kind in ("mlip_opt", "mlip_opt_freq") and not result.opt_converged:
            raise OrcaRunError("MLIP optimization did not converge within the step limit.")
        if calc.kind in ("mlip_freq", "mlip_opt_freq") and result.n_imaginary > 0:
            raise OrcaRunError(
                f"{result.n_imaginary} imaginary frequency/frequencies "
                f"(cm^-1: {_imaginary_detail(result)}). Not a true minimum."
            )
        return
    if calc.kind in ("opt", "ts_opt", "opt_freq", "ts_opt_freq") and not result.opt_converged:
        raise OrcaRunError("Optimization did not converge.")
    # A relaxed scan's own bar: the points it was run for. Every point is a
    # constrained optimization, and one that does not converge ends the run in
    # an error termination (measured, 6.1.1) — caught above — so a normal end
    # with the results table is the whole scan. Without the table the run
    # scanned nothing, whatever its last optimization says.
    if _wants_scan(calc) and not result.scan_points:
        raise OrcaRunError(
            "No relaxed surface scan in the output — ORCA ran the optimization "
            "without scanning. Check the %geom Scan block in the input.")
    # A frequency job that produced no frequency table at all is not a passing
    # frequency job: the imaginary-mode rule below is vacuously satisfied by an
    # empty table (n_imaginary == 0), so such a run was stamped DONE with no
    # frequencies and no thermochemistry — while the symmetric opt-that-never-
    # optimized case correctly fails. Reachable in Expert mode, where the raw
    # text owns the keyword line and may simply not ask for frequencies.
    if calc.kind in _FREQ_KINDS and not _has_vibrational_analysis(result):
        raise OrcaRunError(
            "No vibrational frequencies in the output — this run did not "
            "compute a Hessian. Check that the input asks for it (Freq / "
            "NumFreq / AnFreq)."
        )
    if calc.kind in ("freq", "opt_freq") and result.n_imaginary > 0:
        raise OrcaRunError(
            f"{result.n_imaginary} imaginary frequency/frequencies "
            f"(cm^-1: {_imaginary_detail(result)}). Not a true minimum."
        )
    if calc.kind in ("ts_freq", "ts_opt_freq"):
        # a genuine transition state has exactly ONE imaginary frequency
        n = result.n_imaginary
        if n == 0:
            raise OrcaRunError(
                "No imaginary frequency found. Not a transition state "
                "(a TS should have exactly one)."
            )
        if n > 1:
            raise OrcaRunError(
                f"{n} imaginary frequencies (cm^-1: {_imaginary_detail(result)}). "
                "A transition state should have exactly one; this is a "
                "higher-order saddle point."
            )
    if calc.kind == "neb_ts" and result.frequencies:
        # preset_neb_ts appends FREQ to verify the located TS. If frequencies
        # were computed, require exactly one imaginary mode (a true first-order
        # saddle); skip the check when the user removed FREQ (no frequencies).
        n = result.n_imaginary
        if n != 1:
            raise OrcaRunError(
                f"NEB-TS located a structure with {n} imaginary frequency/frequencies "
                f"(cm^-1: {_imaginary_detail(result)}); a transition state must "
                "have exactly one."
            )


def result_from_output(calc: Calculation):
    """Parse a finished calc's on-disk output into a ParseResult, dispatching on
    kind: MLIP calcs use the MLIP parser (a JSON result), CREST calcs the CREST
    parser (crest_conformers.xyz + crest.energies), ORCA calcs the ORCA .out
    parser. Shared by the engine's reference resolution and the session
    reconciliation in store.py so both stay consistent. The mlip/crest imports
    are lazy to keep core/ importable without those packages."""
    if calc.kind.startswith("mlip"):
        from ..mlip.parser import parse_mlip_result
        return parse_mlip_result(calc.output_path)
    if calc.kind.startswith("crest"):
        from ..crest.parser import parse_crest_result
        return parse_crest_result(str(calc.output_path))
    return parse_file(str(calc.output_path))


def system_name_of(calc, system_names: "Optional[Callable[[str], str]]") -> str:
    """The name of the system ``calc`` belongs to, through a system id -> name
    lookup (the store's QueueStore.system_name); "" for a standalone calc, no
    lookup, or one that fails -- the folder is then the workspace root's."""
    sid = getattr(calc, "system", "") or ""
    if not sid or system_names is None:
        return ""
    try:
        return system_names(sid) or ""
    except Exception:
        return ""


def calc_result_folder(workspace_root, name: str, by_name,
                       system_names: "Optional[Callable[[str], str]]" = None) -> Path:
    """The folder a queued calc's files are read from, by NAME
    (core.rundir.result_folder: its output_path's folder, else its recorded
    run folder, else the one it would get now). A name that is not in the
    queue is looked for where a standalone calc of that name runs."""
    calc = by_name.get(name) if name else None
    if calc is None:
        return Path(workspace_root) / name
    return result_folder(workspace_root, calc, system_name_of(calc, system_names))


def find_parent_hessian(calc: "Calculation", by_name: "dict",
                        workspace_root: Path,
                        system_names: "Optional[Callable[[str], str]]" = None,
                        ) -> "tuple[Optional[Path], list[str]]":
    """The Hessian an inp-set template takes from a parent, and every place
    looked: the ``{after}.hess`` of the calc this one runs after, else the
    ``{ref}.hess`` of its geometry source. Each parent's folder is its
    output_path's (a restored session may live in another workspace), else
    its run folder (calc_result_folder: a parent in another system is looked
    for in that system's folder). (None, looked) when there is none. Shared by
    the engine (which stages what it finds) and the queue editor's preview."""
    parents = [calc.after]
    if calc.geometry_source == GeometrySource.REFERENCE:
        parents.append(calc.ref_name)
    looked: "list[str]" = []
    for pname in dict.fromkeys(p for p in parents if p and p != calc.name):
        folder = calc_result_folder(workspace_root, pname, by_name, system_names)
        src = folder / f"{pname}.hess"
        looked.append(str(src))
        if src.is_file():
            return src, looked
    return None, looked


class QueueEngine:
    """Runs a list of Calculations in order, resolving geometry references."""

    # seconds between re-probes of the remote devices during a run (design §5)
    DEVICE_WATCH = 60.0

    def __init__(self, orca_path: str, workspace_root: str,
                 callbacks: Optional[QueueCallbacks] = None,
                 skip_names: Optional[set] = None,
                 mlip_envs: Optional[list] = None,
                 crest_distro: str = "",
                 queue_lock=None,
                 budget: "Optional[ResourceBudget]" = None,
                 system_names: "Optional[Callable[[str], str]]" = None,
                 device_entries: "Optional[list]" = None,
                 transport=None,
                 device_info: "Optional[Callable[[str], object]]" = None,
                 device_probe: "Optional[Callable[[dict], object]]" = None,
                 pool_states: "Optional[dict]" = None):
        self.orca_path = orca_path
        # One OrcaRunner per JOB, not per engine: with several calculations in
        # flight each needs its own process handle and its own cancel/detach
        # events. A factory (rather than a fixed instance) is also the seam a
        # test uses to intercept a launch. It reads self.orca_path so the field
        # and what actually launches cannot diverge.
        self._orca_runner_factory = lambda: OrcaRunner(self.orca_path)
        # Registered MLIP environments [{id, name, python}], used to resolve which
        # interpreter runs a mlip_* calc. Empty if MLIP isn't configured.
        self.mlip_envs = list(mlip_envs or [])
        # Preferred WSL distro for CREST calcs ("" = auto-detect the first distro
        # that has CREST). A crest_conf calc runs its Linux binary through WSL
        # (see orcamgr/crest/), OUTSIDE the ORCA pipeline.
        self.crest_distro = (crest_distro or "").strip()
        self.workspace_root = Path(workspace_root)
        # system id -> its CURRENT name ("" = none): where a calc in a system
        # that has not started yet will run (core/rundir.py). Wired to the
        # store's own systems by make_engine_factory and asked at the moment a
        # calc starts, so a rename made while the queue runs is followed by
        # every calc that has not started. Without one, every calc runs in the
        # workspace root, as before systems had folders.
        self._system_names = system_names
        self.cb = callbacks or QueueCallbacks()
        # hard cancel: kill the current job and skip the rest (CANCELLED).
        self._cancel_event = threading.Event()
        # graceful drain: let the current job finish, then stop; the rest stay
        # PENDING so they remain runnable on the next Run.
        self._stop_after_current = threading.Event()
        # shutdown detach: stop monitoring but LEAVE the running ORCA alive so it
        # survives the app closing and can be reattached next launch.
        self._detach_event = threading.Event()
        # A job came back reporting app shutdown (OrcaDetached). Single-threaded
        # this was the walk's own `break`; with jobs on their own threads the
        # outcome has to travel back to the dispatcher, which must then start
        # nothing more — including a row it already picked and is holding for a
        # free slot.
        self._shutdown_seen = threading.Event()
        self._by_name: dict[str, Calculation] = {}
        # calculations the user chose to skip this run (e.g. to keep existing
        # results on disk instead of overwriting them)
        self._skip_names = set(skip_names or ())
        # Which ROWS those names were about; filled at run start (see run_all),
        # because a name outlives the calculation that carried it.
        self._skip_ids: set[int] = set()
        # The list run_all walks is LIVE (P29): when wired to a store,
        # queue_lock IS the store's own lock (make_engine_factory), so user
        # mutations of the queue and the walk's pick step are serialized.
        # Standalone engines (tests) get a private lock — no concurrency there.
        self._queue_lock = queue_lock if queue_lock is not None else threading.RLock()
        # Names the dispatcher is currently handling. A name is added at pick
        # time UNDER the lock — i.e. before the RUNNING stamp lands — so the
        # store can refuse mutations of a calc the engine has picked but not yet
        # stamped (the pick->stamp window would otherwise let an edit/remove
        # race a launch). A set, not a name: several jobs run at once.
        self._active_names: "set[str]" = set()
        # CPU/RAM admission limits, resolved against this machine at construction
        # (0 = auto). max_jobs 1 is the classic one-at-a-time queue.
        self.budget = (budget or ResourceBudget()).resolved()
        # Remote devices (orcamgr/remote/, design §5): the enabled settings
        # entries, in the order the user listed them -- which is the order a
        # job that does not fit here is offered to them. Each is resolved
        # against its latest probe (device_info) into a RemoteDevice with its
        # own budget; device_probe re-probes one during the run (the watch
        # thread, every DEVICE_WATCH seconds), so a device that comes on
        # mid-run starts taking jobs and one that drops off stops.
        self._transport = transport
        self._device_entries = [dict(e) for e in (device_entries or [])
                                if e.get("enabled")] if transport is not None else []
        self._device_info = device_info or (lambda alias: None)
        self._device_probe = device_probe
        self._devices: dict = {
            e["alias"]: resolve_device(e, self._device_info(e["alias"]))
            for e in self._device_entries}
        # Per-pool run state ("" = this machine, else a device alias):
        # POOL_RUNNING takes new jobs, POOL_DRAINING finishes what it has and
        # takes none, POOL_STOPPED takes none. _place honours it; a job already
        # running somewhere is reattached whatever its pool says. The whole-
        # queue Stop-after-current drains every pool, Run sets every pool
        # running; a device's own controls set only its pool. ``pool_states``
        # seeds it (a device's Run while the queue is idle starts a run with
        # only that pool running).
        self._pool_state: "dict[str, str]" = {"": POOL_RUNNING}
        self._pool_state.update({a: POOL_RUNNING for a in self._devices})
        for pool, state in (pool_states or {}).items():
            if pool in self._pool_state and state in POOL_STATES:
                self._pool_state[pool] = state
        # rows to offer again (a device could not take them: RemoteUnavailable)
        self._unsee: "set[int]" = set()
        # where _pick_ready placed a row, for _start_job (name -> device)
        self._placed: "dict[str, str]" = {}
        self._watch_stop = threading.Event()
        # In-flight jobs by calc name -> _JobSlot (its reserved cores/RAM and its
        # backend runner). Guarded by _queue_lock; _slot_cv wakes the dispatcher
        # when a job finishes and its budget comes back.
        self._slots: "dict[str, _JobSlot]" = {}
        self._slot_cv = threading.Condition(self._queue_lock)
        # Per-job state for the thread running it (see _job).
        self._job_local = threading.local()
        # The energy condition (_judge_filter): the verdicts the pick reached
        # for rows it hands to _precheck to stamp (id -> (row, (state, msg))),
        # the DONE parents whose results a scan asked for (read outside the
        # lock by _load_results), and those already tried this run (id ->
        # row; the value pins the object so its id is not recycled).
        self._verdicts: "dict[int, tuple]" = {}
        self._to_load: "dict[int, Calculation]" = {}
        self._load_tried: "dict[int, Calculation]" = {}
        # Conformer placeholders (ref_point conf:all): the DONE CREST searches
        # whose placeholders a scan found ready to replace (id -> (search, N),
        # replaced outside the scan by _expand_conformers), the placeholders
        # an attempt left in place (id -> row: their next pick is a BLOCKED
        # verdict, never another attempt -- so a refusal cannot spin the
        # dispatcher), and the list the run walks (for an engine with no
        # store to rewrite it through).
        self._to_expand: "dict[int, tuple]" = {}
        self._expand_tried: "dict[int, Calculation]" = {}
        self._walk: "Optional[list[Calculation]]" = None

    # -- per-job state ----------------------------------------------------
    @property
    def _job(self) -> "_JobSlot":
        """The slot of the job running on THIS thread. Job bodies run in their
        own threads, so state that used to be a single engine field (the backend
        runner, the "has ORCA launched yet" flag) is per-thread. A direct call
        outside a run (tests, reconciliation) gets a private throwaway slot."""
        slot = getattr(self._job_local, "slot", None)
        if slot is None:
            slot = _JobSlot(name="")
            self._job_local.slot = slot
        return slot

    @property
    def _orca_launched(self) -> bool:
        """True once THIS job actually launched (or adopted) its ORCA process --
        i.e. the on-disk .out belongs to this attempt. Gates _failure_reason's
        .out parse: the folder may hold a stale .out from a previous run (e.g.
        after a rejected keep-existing result), and parsing it on a pre-launch
        failure would mask the real cause (A22)."""
        return self._job.orca_launched

    @_orca_launched.setter
    def _orca_launched(self, value: bool) -> None:
        self._job.orca_launched = value

    def _use_runner(self, runner):
        """Register `runner` as this job's backend runner so cancel()/detach()
        can reach it, and replay a stop that landed before it existed."""
        self._job.runner = runner
        self._forward_pending_signals(runner)
        return runner

    def _remote_runner(self, calc: Calculation, index: int, device: str,
                       crest_flags=None):
        """A RemoteOrcaRunner for ``calc`` on ``device`` -- a RemoteCrestRunner
        for a CREST search (``crest_flags``: build_crest_argv's, needed only
        to launch) -- wired to the row: the disconnected flag (design D7) and
        a fetch that keeps failing (§6.4) reach the queue as calc updates. A
        device no longer configured (a reattach after the user removed it)
        still gets a runner: it can watch and fetch, but with no workdir it
        never deletes (the guard)."""
        dev = self._devices.get(device)
        if (calc.kind or "").startswith("crest"):
            runner = RemoteCrestRunner(self._transport, device,
                                       getattr(dev, "crest_path", "") if dev else "",
                                       dev.workdir if dev else "",
                                       calc.remote_dir, flags=crest_flags or ())
        else:
            runner = RemoteOrcaRunner(self._transport, device,
                                      dev.orca_path if dev else "",
                                      dev.workdir if dev else "",
                                      calc.remote_dir)

        def flag(lost: bool) -> None:
            calc.disconnected = lost
            self.cb.calc_update(index, calc)

        def notice(msg: str) -> None:
            calc.message = msg
            self.cb.calc_update(index, calc)

        runner.on_disconnect = flag
        runner.on_notice = notice
        if dev is not None and dev.spot:
            runner.spot = True
            runner.requeue_after = float(dev.requeue_after_min) * 60.0
        return runner

    def _live_runners(self) -> list:
        with self._queue_lock:
            return [s.runner for s in self._slots.values() if s.runner is not None]

    @property
    def active_names(self) -> "set[str]":
        """Names of the calcs the dispatcher is currently handling (empty between
        runs). The store consults this to refuse mutations of an in-flight calc."""
        with self._queue_lock:
            return set(self._active_names)

    def cancel(self) -> None:
        """Hard stop: kill every job in flight and start no more."""
        self._cancel_event.set()
        for runner in self._live_runners():
            runner.cancel()
        with self._slot_cv:
            self._slot_cv.notify_all()

    def request_stop_after_current(self) -> None:
        """Stop starting new jobs; let the ones already in flight finish. The
        remaining calculations stay PENDING. Does NOT kill anything.

        Every pool drains. The walk itself stays alive until nothing is in
        flight -- then it ends ("left pending") -- so a Run in the meantime
        (resume_pools) puts the pools back to work in the same run instead of
        being refused as "already running"."""
        self._stop_after_current.set()
        self.set_pool_state(None, POOL_DRAINING)

    def resume_pools(self) -> None:
        """The whole-queue Run while a run is alive (draining, or with some
        pools stopped): every pool takes jobs again."""
        self._stop_after_current.clear()
        self.set_pool_state(None, POOL_RUNNING)

    def set_pool_state(self, pool: "Optional[str]", state: str) -> bool:
        """Set one pool's run state ("" = this machine, an alias = a device),
        or every pool's with ``pool`` None. False for an unknown pool or
        state. A pool set running clears the whole-queue drain flag (a device
        Run during a drain resumes that device only)."""
        if state not in POOL_STATES:
            return False
        with self._slot_cv:
            if pool is None:
                for k in self._pool_state:
                    self._pool_state[k] = state
            elif pool in self._pool_state:
                self._pool_state[pool] = state
            else:
                return False
            if state == POOL_RUNNING:
                self._stop_after_current.clear()
            self._slot_cv.notify_all()
        return True

    def cancel_pool(self, pool: str) -> int:
        """Cancel the jobs in flight in one pool ("" = this machine) -- each
        ends CANCELLED, as with the whole-queue Cancel -- and stop the pool so
        it takes nothing new. Other pools are not touched. Returns how many
        jobs were signalled."""
        if not self.set_pool_state(pool, POOL_STOPPED):
            return 0
        with self._queue_lock:
            runners = [s.runner for s in self._slots.values()
                       if (s.device or "") == pool and s.runner is not None]
        for r in runners:
            r.cancel()
        with self._slot_cv:
            self._slot_cv.notify_all()
        return len(runners)

    def pool_states(self) -> "dict[str, str]":
        """Each pool's state as shown: a draining pool with nothing left in
        flight reads "stopped"."""
        with self._queue_lock:
            busy = {(s.device or "") for s in self._slots.values()}
            return {k: (POOL_STOPPED if v == POOL_DRAINING and k not in busy else v)
                    for k, v in self._pool_state.items()}

    def _pool_takes(self, pool: str) -> bool:
        """Whether pool ("" = this machine) may be given a new job."""
        return self._pool_state.get(pool or "", POOL_RUNNING) == POOL_RUNNING

    def _any_pool_running(self) -> bool:
        return any(v == POOL_RUNNING for v in self._pool_state.values())

    def detach(self) -> None:
        """Stop monitoring the queue WITHOUT killing the running ORCA — used on
        app shutdown so the in-flight job survives and can be reattached. The
        running calc is left in the RUNNING state (its pid is already persisted)."""
        self._detach_event.set()
        for runner in self._live_runners():
            runner.detach()
        with self._slot_cv:
            self._slot_cv.notify_all()

    def _forward_pending_signals(self, runner) -> None:
        """Replay a Stop/shutdown that landed BEFORE this runner was registered.
        cancel()/detach() above only reach the runner currently stored on the
        engine, so a signal arriving in the window between picking a calc and
        assigning its runner would be lost — the job would keep running through
        a cancel. Every MLIP/CREST registration site calls this right after
        storing the runner."""
        if self._cancel_event.is_set():
            runner.cancel()
        if self._detach_event.is_set():
            runner.detach()

    # -- run folders (core/rundir.py) --
    def _system_name(self, calc: Calculation) -> str:
        return system_name_of(calc, self._system_names)

    def _run_folder(self, calc: Calculation) -> Path:
        """Where `calc` runs: its recorded folder, else the one it gets now."""
        return run_folder(self.workspace_root, calc, self._system_name(calc))

    def _result_folder(self, calc: Calculation) -> Path:
        """Where `calc`'s files are read: its output's folder, else its run
        folder."""
        return result_folder(self.workspace_root, calc, self._system_name(calc))

    def _folder_of(self, name: str) -> Path:
        """The result folder of the queued calc called `name` (a parent whose
        .hess or IRC end points a child reads -- in its own system's folder,
        which may not be the child's)."""
        return calc_result_folder(self.workspace_root, name, self._by_name,
                                  self._system_names)

    def _claim_run_folder(self, calc: Calculation) -> Path:
        """Record where `calc` runs, the moment it first starts, and return
        that folder. The folder follows the system's name AT THIS MOMENT; once
        recorded it is kept -- a system renamed or a block moved afterwards
        never moves a calculation that has run, and nothing on disk is moved
        for it (a job may be running there). A calc that already has one (a
        CANCELLED re-run, a rerun_calc, a session from before system folders,
        recorded as its {workspace}/{name}/) runs there again: the overwrite
        question it was asked was about that folder."""
        if not calc.run_dir:
            calc.run_dir = planned_run_dir(calc.name, self._system_name(calc))
        return self._run_folder(calc)

    # -- geometry resolution --
    def _resolve_geometry(self, calc: Calculation) -> str:
        if calc.geometry_source == GeometrySource.DIRECT:
            # In raw+direct mode the coordinates normally live inside raw_text,
            # so an empty xyz is fine — UNLESS the text has the placeholder,
            # which is a promise that coordinates will be injected here. With
            # none loaded, render_raw_input substituted "" and ORCA was launched
            # on "* xyz 0 1 \n *", failing minutes later into a locked FAILED
            # (P24) instead of being refused before anything started.
            if not calc.xyz.strip():
                if not calc.is_raw:
                    raise OrcaRunError(
                        "No coordinates provided (direct source is empty).")
                if GEOMETRY_PLACEHOLDER in calc.raw_text:
                    raise OrcaRunError(
                        f"Raw input has a {GEOMETRY_PLACEHOLDER} placeholder but "
                        "no geometry was loaded for it. Load an .xyz, or paste "
                        "the coordinates into the input itself.")
            return calc.xyz
        # REFERENCE
        if calc.ref_name == calc.name:
            raise OrcaRunError("A calculation can't reference its own geometry.")
        ref = self._by_name.get(calc.ref_name)
        if ref is None:
            raise OrcaRunError(f"Referenced calculation '{calc.ref_name}' not found in queue.")
        if ref.state == CalcState.FILTERED:
            # normally stamped before it gets here (_precheck's cascade)
            raise OrcaFiltered(f"parent {ref.name} was filtered")
        if calc.ref_point in _IRC_END_FILES:
            return self._irc_end_geometry(calc, ref)
        if calc.ref_point == CONF_ALL:
            # a placeholder is never run (_pick_pass replaces it): this is
            # the backstop, and BLOCKED keeps it editable
            raise OrcaBlocked(
                f"it stands for every conformer of '{ref.name}' and becomes one "
                "calculation per conformer once that search has finished")
        if conf_index(calc.ref_point):
            return self._conformer_geometry(calc, ref)
        # Parse-on-miss: a DONE ref restored from a previous session has no
        # in-memory ParseResult (we don't eagerly re-parse on load), so read its
        # output now. Dispatch on kind: an MLIP ref's output is the worker's JSON
        # result, NOT an ORCA .out — parsing it with the ORCA parser would yield
        # no geometry and break the handoff after a restart.
        # Why the re-read failed, if it did — a DONE calc's output is normally
        # right there, so "could not be read" and "held no geometry" are
        # different problems and only one of them is the parent's fault.
        reread_problem = ""
        if ref.state == CalcState.DONE and not ref.result and ref.output_path:
            out = Path(ref.output_path)
            if not out.exists():
                reread_problem = (f"its output file is missing ({out}) — the "
                                  "folder may have been moved or deleted")
            else:
                try:
                    ref.result = result_from_output(ref)
                except Exception as e:
                    reread_problem = f"its output could not be read ({e})"
        if ref.state == CalcState.DONE and ref.result and ref.result.geometry:
            return _xyz_from_geometry(ref.result)
        if ref.state in (CalcState.FAILED, CalcState.BLOCKED, CalcState.CANCELLED):
            raise OrcaRunError(f"Referenced calculation '{calc.ref_name}' did not succeed.")
        if ref.state == CalcState.DONE and (not ref.result or not ref.result.geometry):
            if reread_problem:
                # Not "produced no geometry": the calculation finished and very
                # likely produced one — we just cannot get at it. Saying the
                # wrong thing here is expensive, because the dependent is
                # FAILED and FAILED is locked (P24).
                raise OrcaRunError(
                    f"Referenced calculation '{calc.ref_name}' finished, but "
                    f"{reread_problem}."
                )
            raise OrcaRunError(
                f"Referenced calculation '{calc.ref_name}' produced no geometry "
                "(was it an optimization?)."
            )
        raise OrcaRunError(f"Referenced calculation '{calc.ref_name}' has no usable geometry yet.")

    def _irc_end_geometry(self, calc: Calculation, ref: Calculation) -> str:
        """The forward or backward end point of an IRC (ref_point irc_f /
        irc_b): the {ref}_IRC_F.xyz / {ref}_IRC_B.xyz ORCA writes beside the
        IRC's .out. Read from the IRC's own run folder (its output_path, which
        a session restored from another workspace keeps), else its recorded
        run folder -- in its own system's folder, which need not be this
        calc's. A missing file is a precise error, not "no geometry": the IRC
        finished, it just did not run in that direction."""
        if ref.state in (CalcState.FAILED, CalcState.BLOCKED, CalcState.CANCELLED):
            raise OrcaRunError(f"Referenced calculation '{calc.ref_name}' did not succeed.")
        if ref.state != CalcState.DONE:
            raise OrcaRunError(
                f"Referenced calculation '{calc.ref_name}' has no usable geometry yet.")
        folder = self._result_folder(ref)
        side = "forward" if calc.ref_point == "irc_f" else "backward"
        path = folder / f"{ref.name}{_IRC_END_FILES[calc.ref_point]}"
        if not path.is_file():
            raise OrcaBlocked(
                f"'{calc.ref_name}' has no {side} IRC end point: {path.name} is "
                f"not in {folder}. ORCA writes it only when the IRC runs in that "
                "direction (check its %irc Direction).")
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as e:
            raise OrcaRunError(f"Could not read {path}: {e}")
        from ..molview import iter_xyz_frames
        frames = list(iter_xyz_frames(text))
        coords = frames[-1][1].splitlines()[2:] if frames else []
        if not any(ln.strip() for ln in coords):
            raise OrcaRunError(f"{path.name} in {folder} holds no coordinates.")
        return "\n".join(coords)

    def _conformer_geometry(self, calc: Calculation, ref: Calculation) -> str:
        """Conformer k of a CREST search (ref_point conf:<k>): conformers[k-1]
        of its parsed result (crest_conformers.xyz, energy-sorted, 1 = the
        lowest -- the {name}_c<k>.xyz export's numbering), read from its run
        folder when a restored session holds no result yet. k past the end of
        the ensemble is no fault of anyone's -- CREST found fewer conformers
        than the stack has members -- so it is OrcaFiltered, not a failure."""
        k = conf_index(calc.ref_point)
        if ref.kind not in CONFORMER_KINDS:
            raise OrcaBlocked(
                f"'{calc.ref_name}' is not a CREST conformer search ({ref.kind}); "
                f"it has no conformer {k}.")
        if ref.state in (CalcState.FAILED, CalcState.BLOCKED, CalcState.CANCELLED):
            raise OrcaRunError(f"Referenced calculation '{calc.ref_name}' did not succeed.")
        if ref.state != CalcState.DONE:
            raise OrcaRunError(
                f"Referenced calculation '{calc.ref_name}' has no usable geometry yet.")
        if not ref.result and ref.output_path:
            # parse-on-miss, as for a final geometry (DONE is frozen, P24, so
            # the write cannot race the engine)
            try:
                ref.result = result_from_output(ref)
            except Exception as e:
                raise OrcaBlocked(f"'{calc.ref_name}' finished, but its conformers "
                                  f"could not be read ({e}).")
        confs = list(getattr(ref.result, "conformers", None) or ())
        if not confs:
            raise OrcaBlocked(f"'{calc.ref_name}' finished, but its conformer "
                              "ensemble could not be read (crest_conformers.xyz).")
        if k > len(confs):
            raise OrcaFiltered(f"CREST found only {len(confs)} conformers")
        conf = confs[k - 1]
        return "\n".join(f"{a.symbol} {a.x:.10f} {a.y:.10f} {a.z:.10f}"
                         for a in conf.geometry)

    def _judge_dead_running(self, calc: Calculation, index: int, out_path) -> None:
        """A calc restored as RUNNING whose detached ORCA is no longer alive:
        judge it from the .out it left behind (DONE if terminated normally and
        valid), NEVER relaunch. Mirrors reconcile_calcs' judgment; the failure
        branches raise OrcaRunError so run_all stamps FAILED and blocks the
        dependents exactly like a live failure."""
        calc.pid = None
        calc.create_time = None
        path = calc.output_path or str(out_path)
        result = None
        if path and Path(path).is_file():
            try:
                result = parse_file(path)
            except Exception:
                result = None
        if result is None or not result.terminated_normally:
            raise OrcaRunError("Interrupted while ORCAdesk was closed.")
        calc.result = result
        calc.output_path = path
        validate_result(calc, result)   # raises OrcaRunError on a bad result
        calc.state = CalcState.DONE
        calc.message = "Completed (finished while ORCAdesk was closed)."
        self.cb.log(f"[{calc.name}] finished while unmonitored — result adopted.",
                    "ok", calc.name)
        self.cb.calc_update(index, calc)

    # -- single calculation --
    def _run_calc(self, calc: Calculation, index: int) -> None:
        # Reattach: the folder recorded when it started (a session from
        # before system folders reads as {workspace}/{name}/, see
        # store.load_session) -- never the one its system's name gives now.
        out_path = self._run_folder(calc) / f"{calc.name}.out"
        # A RUNNING calc restored from a previous session may have been
        # launched under a DIFFERENT workspace root: its persisted output_path
        # is where the detached ORCA is actually writing. Deriving from the
        # current setting would tail a nonexistent file and then FAILED-lock a
        # successfully finished job at process exit (and clobber the one
        # pointer the finished-while-closed → DONE judgment depends on).
        if calc.state == CalcState.RUNNING and calc.output_path:
            out_path = Path(calc.output_path)

        # A job left running on a remote device: its liveness is a question
        # for the device, never for psutil here. The remote runner's monitor
        # asks at once -- alive: tail on; dead (it ended while ORCAdesk was
        # closed): fetch the folder, and the judgment below reads it; no
        # answer: keep it RUNNING, try again, show "Disconnected".
        if calc.state == CalcState.RUNNING and calc.device:
            runner = self._use_runner(self._remote_runner(calc, index, calc.device))
            runner.adopt(calc.pid, calc.create_time)
            self._orca_launched = True
            calc.output_path = str(out_path)
            self.cb.log(f"[{calc.name}] reattaching to ORCA still running...",
                        "info", calc.name)
            self.cb.calc_update(index, calc)
            start_pos = runner.end_position(out_path)
            self._monitor_and_finish(calc, index, out_path, start_pos=start_pos)
            return

        # Reattach path: this calc was left RUNNING by a previous session and its
        # ORCA process is genuinely still alive — don't relaunch, just resume
        # monitoring the live process and tailing its .out.
        if calc.state == CalcState.RUNNING and calc.pid and not process_matches(
                calc.pid, calc.create_time):
            # The process is GONE (it exited while the calc sat RUNNING with no
            # monitor — e.g. reconcile saw it alive at startup but the auto-
            # resume was declined over an invalid ORCA path, and the job later
            # finished). Judge it from its output like reconcile_calcs would —
            # falling through to a fresh launch would TRUNCATE the completed
            # .out and recompute it (the CREST path at _run_crest_calc has the
            # same never-relaunch branch).
            self._judge_dead_running(calc, index, out_path)
            return
        if (calc.state == CalcState.RUNNING and calc.pid
                and process_matches(calc.pid, calc.create_time)):
            runner = self._use_runner(self._orca_runner_factory())
            runner.adopt(calc.pid, calc.create_time)
            # The adopted process owns the on-disk .out, so a failure diagnosis
            # may trust it (see _failure_reason).
            self._orca_launched = True
            # Persist the output path too (sessions from before it was recorded
            # at launch lack it): if the app closes again and the job finishes
            # while closed, reconcile_calcs can only judge DONE from a path.
            calc.output_path = str(out_path)
            self.cb.log(f"[{calc.name}] reattaching to ORCA still running "
                        f"(pid {calc.pid})...", "info", calc.name)
            self.cb.calc_update(index, calc)
            # Tail from the CURRENT end of file: don't re-stream the output
            # written before the app closed (that would flood the live log and,
            # past the log-buffer cap, truncate the graph). The UI rebuilds the
            # full SCF/opt-graph history separately from the .out on disk.
            start_pos = runner.end_position(out_path)
            self._monitor_and_finish(calc, index, out_path, start_pos=start_pos)
            return

        # Fresh launch.
        calc.state = CalcState.RUNNING
        # ...and it is a NEW run: a re-run of a CANCELLED calc (or a BLOCKED one
        # that was re-pointed) kept showing "Cancelled by user." / "Skipped: a
        # dependency failed." for the whole time it was running. The MLIP and
        # CREST entry points already clear it.
        calc.message = ""
        calc.faulted = False
        # It has started: its folder is decided now and recorded (persisted
        # with the RUNNING stamp below).
        calc_dir = self._claim_run_folder(calc)
        out_path = calc_dir / f"{calc.name}.out"
        self.cb.calc_update(index, calc)

        xyz = self._resolve_geometry(calc)

        calc_dir.mkdir(parents=True, exist_ok=True)
        inp_path = calc_dir / f"{calc.name}.inp"

        if calc.is_raw:
            # raw mode: use the hand-edited text. If it references geometry,
            # the placeholder must be present so we can inject coordinates.
            if (calc.geometry_source == GeometrySource.REFERENCE
                    and GEOMETRY_PLACEHOLDER not in calc.raw_text):
                raise OrcaRunError(
                    f"Raw input references another calculation but has no "
                    f"{GEOMETRY_PLACEHOLDER} placeholder for the geometry."
                )
            mismatch = raw_geometry_mismatch(calc.raw_text, xyz)
            if mismatch:
                self.cb.log(f"[{calc.name}] {mismatch}", "warn", calc.name)
            text = render_raw_input(calc.raw_text, xyz)
            text = self._render_template_values(calc, text, calc_dir, xyz)
        else:
            try:
                text = build_input(calc.config, xyz, calc.charge, calc.multiplicity)
            except ValueError as e:
                # the generator refuses inputs it cannot honestly express
                # (e.g. IRC InitHess "read" without a filename) — surface that
                # as a normal run failure, not an "Unexpected error".
                raise OrcaRunError(str(e))
        self._warn_numfreq_ranks(calc, text)
        # Must happen while the PREVIOUS .inp is still on disk — it is what
        # says whether the restart files beside it belong to this structure.
        self._set_aside_stale_guess(calc, calc_dir, text)
        inp_path.write_text(text, encoding="utf-8")

        self._prepare_neb_side_files(calc, calc_dir, xyz, text)
        self._stage_irc_hessian(calc, calc_dir, text)

        self.cb.log(f"[{calc.name}] ({calc.kind}) running ORCA...", "info", calc.name)
        device = self._job.device
        if device:
            # its remote folder is decided and recorded (persisted with the
            # calc_update) before anything is started there, so a launch the
            # app closes on is found again (design §6.2)
            dev = self._devices.get(device)
            calc.device = device
            calc.remote_dir = remote_dir_for(dev.workdir if dev else "",
                                             calc.run_dir, launch_stamp())
            self.cb.calc_update(index, calc)
            runner = self._use_runner(self._remote_runner(calc, index, device))
        else:
            calc.device = ""
            calc.remote_dir = ""
            runner = self._use_runner(self._orca_runner_factory())
        pid, create_time = runner.launch(inp_path, out_path)
        # From here the .out on disk is THIS attempt's output — a failure
        # diagnosis may trust it (see _failure_reason).
        self._orca_launched = True
        calc.pid = pid
        calc.create_time = create_time
        # persist the pid AND the output path immediately so the next launch can
        # deal with this job even if ORCAdesk is closed seconds after it starts:
        # the pid enables a live reattach, and the path lets reconcile_calcs
        # judge a job that FINISHED while closed as DONE from its .out — without
        # it that branch is unreachable and a successful run gets locked FAILED.
        calc.output_path = str(out_path)
        self.cb.calc_update(index, calc)

        self._monitor_and_finish(calc, index, out_path)

    def _warn_numfreq_ranks(self, calc, text: str) -> None:
        """Say so when a numerical frequency is about to run on more processes
        than it has displacements — the ORCA deadlock measured in
        resources.numfreq_rank_cap.

        The generated path has already capped itself, so in practice this fires
        for a hand-written `.inp`, which is exactly the one ORCAdesk will not
        edit: raw text is the user's own (P26). It reports and runs; refusing to
        launch would be ORCAdesk overruling an input someone typed on purpose,
        and the ceiling here is measured on one ORCA build rather than
        documented by ORCA.
        """
        cap = numfreq_rank_cap(text, len(_xyz_elements(raw_coordinate_block(text))),
                               raw_nprocs(text))
        if not cap:
            return
        self.cb.log(
            f"[{calc.name}] this numerical frequency has {cap} displacements "
            f"but asks for {raw_nprocs(text)} processes. ORCA runs one "
            f"displacement per process and hangs after the last gradient when "
            f"it is given more — set %pal nprocs to {cap} or fewer. Running as "
            f"written.", "warn", calc.name)

    def _set_aside_stale_guess(self, calc: Calculation, calc_dir: Path,
                               text: str) -> None:
        """Rename a restart file left in the run folder by a run of a DIFFERENT
        structure, so ORCA does not abort on it.

        Workspace folders are never deleted and a queue name is unique only
        within the current queue, so the folder we are about to run in may hold
        a {name}.gbw from another molecule — a removed row's name reused, or the
        same row pointed at a new .xyz, both flows the overwrite modal
        explicitly allows. ORCA 6 reads that file as the SCF's initial guess
        without being asked (AutoStart) and, when the atoms differ, dies in
        GUESS: "Error: Input geometry does not match current geometry", exit
        code 55 (verified on 6.1.1). That is a FAILED calc, and FAILED is locked
        (P24) — the name runs again only through an explicit overwrite, and
        the reported reason says nothing about a stale file the user cannot
        see.

        A restart from the SAME structure and charge/multiplicity is left alone:
        that is a free warm start, and it is what a re-run of a cancelled job
        has. So is one an input asks for explicitly (MOREAD/%moinp) — the whole
        point of that input is to read those orbitals.

        The file is renamed, never deleted (no result in a workspace folder is
        ever destroyed): {name}.gbw -> {name}.gbw.bak.
        """
        stale = [calc_dir / f"{calc.name}{suf}" for suf in _RESTART_SUFFIXES]
        stale = [p for p in stale if p.exists()]
        if not stale:
            return
        low = text.lower()
        if "moread" in low or "%moinp" in low:
            return

        prev_inp = calc_dir / f"{calc.name}.inp"
        prev_sig = ""
        if prev_inp.exists():
            try:
                prev_sig = _guess_signature(
                    prev_inp.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                prev_sig = ""
        now_sig = _guess_signature(text)
        # Unreadable on either side means "cannot tell", and the cost of the two
        # answers is not symmetric: a needless cold SCF start against a locked
        # calculation. So only a positive match keeps the file.
        if prev_sig and prev_sig == now_sig:
            return

        for p in stale:
            try:
                os.replace(p, p.parent / f"{p.name}.bak")
            except OSError as e:
                self.cb.log(f"[{calc.name}] could not set aside {p.name} "
                            f"({e}); ORCA may refuse to start.", "warn", calc.name)
                continue
            self.cb.log(
                f"[{calc.name}] {p.name} in this folder is from a different "
                f"structure — renamed to {p.name}.bak so ORCA does not read it "
                "as the initial guess.", "warn", calc.name)

    def _prepare_neb_side_files(self, calc: Calculation, calc_dir: Path,
                                xyz: str, text: str) -> None:
        """NEB-TS pre-launch guard: write the side .xyz files the %neb block
        points at — the product geometry (required) and an optional TS guess.
        build_input references fixed names "product.xyz" / "ts_guess.xyz", so
        write them here. This must also happen in RAW mode — a raw NEB-TS keeps
        the NEB_End_XYZFile "product.xyz" reference, so the file must exist.
        No-op for every other kind."""
        if calc.kind == "neb_ts":
            prod = (calc.config.neb_product_xyz or "").strip()
            # A raw text that points NEB_End_XYZFile at its own file never
            # consumes the stored product — it is dead state carried over from
            # the form (the _nebProductXyz global survives excursions), so
            # neither the atom-order check nor the side-file write may act on it.
            # read from the RENDERED text: a level block's %neb is added by
            # render_template_text, and names the generated side file
            uses_generated = (not calc.is_raw) or ('"product.xyz"' in (text or calc.raw_text))
            if not calc.is_raw and not prod:
                raise OrcaRunError("NEB-TS needs a product geometry, but none was provided.")
            if calc.is_raw and not prod and uses_generated and is_level_text(calc.raw_text):
                # a level block: nothing launched yet, so BLOCKED (editable),
                # never a locked FAILED for a choice the user has yet to make
                raise OrcaBlocked(
                    "NEB-TS needs a product geometry, but this block has none. "
                    "Pick the product structure in the block's options (Edit "
                    "options in the builder).")
            if calc.is_raw and not prod and uses_generated:
                # The raw text keeps the generated side-file reference but no
                # product geometry is stored: launching would fail cryptically
                # inside ORCA — or worse, silently pick up a stale product.xyz
                # left in this folder by an earlier same-named run.
                raise OrcaRunError(
                    'Raw NEB-TS input references "product.xyz" but no product '
                    "geometry is stored for this calculation. Load a product "
                    ".xyz on the Build tab, or point NEB_End_XYZFile at your "
                    "own file."
                )
            if prod and uses_generated:
                # reactant and product must have identical atoms in identical
                # order — catch the #1 NEB-TS user error before launching ORCA.
                # For a raw calc the reactant that actually runs is the
                # coordinate block inside the rendered text (calc.xyz may be
                # absent or stale); skip the check only when no coordinate
                # block is visible at all (e.g. an xyzfile reference).
                reactant = xyz if not calc.is_raw else raw_coordinate_block(text)
                if _xyz_elements(reactant):
                    chk = check_neb_atom_order(reactant, prod)
                    if not chk.get("ok"):
                        raise OrcaRunError(chk.get("error") or "NEB-TS reactant/product atom mismatch.")
                (calc_dir / "product.xyz").write_text(as_xyz_file(prod), encoding="utf-8")
                guess = (calc.config.neb_ts_guess_xyz or "").strip()
                if guess:
                    (calc_dir / "ts_guess.xyz").write_text(as_xyz_file(guess), encoding="utf-8")

    @staticmethod
    def _raw_irc_hess_file(raw_text: str) -> str:
        """The .hess a raw ``%irc`` block asks to READ, or "".

        Read from the text rather than the stored config, which is dead state
        for a raw calc. Only an explicit ``InitHess read`` counts — the same
        rule the raw NEB-TS guard uses for its side files: an input that
        computes its own Hessian must not have one staged for it.
        """
        text = raw_text or ""
        if not re.search(r"^\s*inithess\s+read\b", text, re.M | re.I):
            return ""
        m = re.search(r'^\s*hess_filename\s+"([^"]+)"', text, re.M | re.I)
        return m.group(1).strip() if m else ""

    def _render_template_values(self, calc: Calculation, text: str,
                                calc_dir: Path, xyz: str = "") -> str:
        """Fill an inp-set template's own placeholders (the geometry one is
        render_raw_input's) through render_template_text, the one renderer
        the queue editor's preview shows too: {{JOB}} with the calculation
        kind's job keyword plus that kind's block (%geom / %irc, unless the
        text has its own), {{CHARGE}} / {{MULT}} with its charge and
        multiplicity, and {{HESS}} with the bare file name of a Hessian staged
        into the run folder. A {{JOB}} IRC reads the parent's .hess when one
        is there and computes its own Hessian otherwise; a text naming
        {{HESS}} needs one (none is an OrcaRunError before launch). A text
        without placeholders is returned unchanged; a {{JOB}} text for a kind
        with no job keyword is an OrcaRunError before anything launches.

        A level block's options (calc.config) are rendered in with it, the
        resolved structure's atom count checking constraint / scan indices; a
        conflict between them and the level (template_option_conflicts, the
        store's refusal too) is an OrcaBlocked -- nothing launched, editable."""
        cfg = calc.config if is_level_text(calc.raw_text) else None
        if cfg is not None:
            problem = template_option_conflicts(calc.raw_text, calc.kind, cfg)
            if problem:
                raise OrcaBlocked(problem)
        need = template_hess_need(text, calc.kind, cfg)
        hess = ""
        if need:
            src, looked = find_parent_hessian(calc, self._by_name, self.workspace_root,
                                              self._system_names)
            if src is not None:
                hess = self._stage_template_hessian(calc, calc_dir, src)
            elif need == "required":
                where = "; ".join(looked) if looked else (
                    "this calculation runs after nothing and references no calculation")
                raise OrcaBlocked(
                    f"The input asks for a Hessian ({HESS_PLACEHOLDER}), but none was "
                    f"found (looked in: {where}). It is the .hess of the calculation this "
                    "one runs after, or of its geometry source — run a frequency "
                    "calculation there first.")
        n_atoms = len(parse_block(xyz)) if (xyz or "").strip() else None
        try:
            return render_template_text(text, calc.kind, calc.charge,
                                        calc.multiplicity, hess, cfg, n_atoms)
        except ValueError as e:
            raise OrcaRunError(str(e))

    def _stage_template_hessian(self, calc: Calculation, calc_dir: Path,
                                src: Path) -> str:
        """Copy the parent Hessian a template uses (find_parent_hessian) into
        the run folder and return its bare file name."""
        dest = calc_dir / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.resolve() != dest.resolve():
            shutil.copyfile(src, dest)
            self.cb.log(f"[{calc.name}] copied {src.name} from "
                        f"{src.parent.name}'s folder.", "info", calc.name)
        return src.name

    def _stage_irc_hessian(self, calc: Calculation, calc_dir: Path,
                           text: str = "") -> None:
        """IRC pre-launch guard for a read-in Hessian: the input references the
        file relative to the run folder, but nothing guarantees it is there (the
        folder may not even exist before the first run). Stage it from the
        referenced calc's folder when possible; otherwise fail fast with a clear
        message instead of a cryptic ORCA abort.

        Raw inputs are covered too. They used to be exempt, so the ts->irc
        Hessian handoff — the whole point of the guard — simply did not happen
        in Expert mode: ORCA launched with no .hess and died inside. What the
        raw text asks for is read from the text (`_raw_irc_hess_file`), the way
        the raw NEB-TS guard reads its own side-file reference.

        ``text`` is the rendered input when the caller has it: a template's
        ``Hess_Filename "{{HESS}}"`` names the real file only after
        _render_template_values, which has already staged it.

        No-op for every other kind.
        """
        if calc.kind != "irc":
            return
        picked = ""
        if calc.is_raw:
            hess_name = self._raw_irc_hess_file(text or calc.raw_text)
            # a level block's own "read" names a picked file by its bare name
            # in the rendered text; the file is copied in like the Guided one
            cfg = calc.config
            if (is_level_text(calc.raw_text)
                    and (cfg.irc_init_hess or "").strip().lower() == "read"
                    and irc_hess_is_path(cfg.irc_hess_file)
                    and hess_name == irc_hess_input_name(cfg.irc_hess_file)):
                picked = hess_name = cfg.irc_hess_file.strip()
        elif (calc.config.irc_init_hess or "").strip().lower() == "read":
            hess_name = (calc.config.irc_hess_file or "").strip()
        else:
            hess_name = ""
        if not hess_name:
            return

        if irc_hess_is_path(hess_name) and (not calc.is_raw or picked):
            # A picked file: the generated input names only the file
            # (irc_hess_input_name), so it must be in the run folder by launch.
            # Copied fresh every time, so re-picking a file of the same name
            # never leaves the previous one in place.
            src = Path(hess_name)
            if not src.is_file():
                raise OrcaRunError(
                    f'IRC needs the Hessian file "{hess_name}", but it is not '
                    f"there any more. Load the .hess again.")
            dest = calc_dir / irc_hess_input_name(hess_name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.resolve() != dest.resolve():
                shutil.copyfile(src, dest)
                self.cb.log(f"[{calc.name}] copied {src.name} into the run folder.",
                            "info", calc.name)
            return

        hess_path = Path(hess_name)
        if not hess_path.is_absolute():
            hess_path = calc_dir / hess_name
        if hess_path.exists():
            return

        looked = [str(hess_path)]
        if calc.geometry_source == GeometrySource.REFERENCE and calc.ref_name:
            # the referenced calc's own folder (another system's, perhaps)
            src = self._folder_of(calc.ref_name) / hess_name
            looked.append(str(src))
            if src.exists():
                dest = calc_dir / hess_name
                # the name may carry a sub-path ("sub/x.hess"), whose parent
                # does not exist in a fresh run folder — FileNotFoundError
                # escaped as "Unexpected error: [Errno 2]" instead of this
                # guard's own clear message
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
                self.cb.log(f"[{calc.name}] copied {hess_name} from "
                            f"{calc.ref_name}'s folder.", "info", calc.name)
        if not hess_path.exists():
            raise OrcaRunError(
                f'IRC needs the Hessian file "{hess_name}", but it was '
                f"not found (looked in: {'; '.join(looked)})."
            )

    def _finish_ok(self, calc: Calculation, index: int, result, out_path,
                   message: str = "Completed.", log_msg: str = "done.") -> None:
        """Stamp a successfully finished calc DONE: store the result, validate
        it (raises OrcaRunError on a bad result — the calc is then FAILED by
        run_all like any other failure), clear the process identity, notify.
        The single point where a run of ANY backend converges to DONE."""
        calc.result = result
        calc.output_path = str(out_path)

        validate_result(calc, result)

        calc.state = CalcState.DONE
        calc.pid = None
        calc.create_time = None
        calc.message = message
        self.cb.log(f"[{calc.name}] {log_msg}", "ok", calc.name)
        self.cb.calc_update(index, calc)

    def _monitor_and_finish(self, calc: Calculation, index: int, out_path,
                            start_pos: int = 0) -> None:
        """Tail ORCA's output until it exits (or is cancelled/detached), then
        parse + validate + mark DONE. Shared by the fresh-launch and reattach
        paths (reattach passes start_pos = current EOF). OrcaCancelled /
        OrcaDetached propagate to run_all."""
        def fatal(line: str) -> None:
            calc.faulted = True
            calc.message = line
            self.cb.log(f"[{calc.name}] ORCA reported an error; the run is lost "
                        f"but its process is still going.", "err", calc.name)
            self.cb.calc_update(index, calc)

        self._job.runner.monitor(
            out_path, on_line=lambda ln: self.cb.log(ln, "orca", calc.name),
            start_pos=start_pos, on_fatal=fatal)
        self._finish_ok(calc, index, parse_file(str(out_path)), out_path)

    # -- single MLIP calculation (runs OUTSIDE the ORCA pipeline) --
    def _resolve_mlip_python(self, calc: Calculation) -> str:
        """The interpreter that runs this calc's MLIP job: the env named by
        config.mlip_env_id, or the first registered env when unset."""
        if not self.mlip_envs:
            raise OrcaRunError(
                "No MLIP environment registered. Add one in Settings → "
                "MLIP environments before running an MLIP calculation.")
        env_id = (getattr(calc.config, "mlip_env_id", "") or "").strip()
        if env_id:
            env = next((e for e in self.mlip_envs if e.get("id") == env_id), None)
            if env is None:
                raise OrcaRunError(
                    f"The MLIP environment for '{calc.name}' is no longer registered.")
        else:
            env = self.mlip_envs[0]
        python = (env.get("python") or "").strip()
        if not python or not Path(python).exists():
            raise OrcaRunError(
                f"MLIP interpreter not found: '{python}'. Check Settings → "
                "MLIP environments.")
        return python

    def _run_mlip_calc(self, calc: Calculation, index: int) -> None:
        """Run a mlip_* calc: relax the geometry with a MACE model in the user's
        env, parse the result into a ParseResult (so it hands off to ORCA refs
        just like an opt does), validate convergence, mark DONE. Mirrors
        _run_calc/_monitor_and_finish but uses MlipRunner instead of ORCA."""
        from ..mlip.runner import MlipRunner, write_mlip_run_files
        from ..mlip.parser import parse_mlip_result

        calc.state = CalcState.RUNNING
        calc.message = ""
        calc_dir = self._claim_run_folder(calc)     # started: its folder is decided
        self.cb.calc_update(index, calc)

        xyz = self._resolve_geometry(calc)
        if not xyz.strip():
            raise OrcaRunError("No coordinates to optimize (geometry is empty).")
        python = self._resolve_mlip_python(calc)

        calc_dir.mkdir(parents=True, exist_ok=True)
        out_path = calc_dir / f"{calc.name}.out"
        result_json = calc_dir / f"{calc.name}.mlip.json"
        task = {"mlip_sp": "sp", "mlip_freq": "freq",
                "mlip_opt_freq": "opt_freq"}.get(calc.kind, "opt")
        script_path, config_path = write_mlip_run_files(
            calc_dir, calc.name, calc.config.mlip_model, xyz, result_json,
            charge=calc.charge, multiplicity=calc.multiplicity, task=task,
            device=getattr(calc.config, "mlip_device", ""),
            temperature=getattr(calc.config, "freq_temp_k", 298.15),
            pressure=getattr(calc.config, "freq_pressure_atm", 1.0),
            # the worker must not quietly take every core on the machine
            # (worker_threads, NOT declared_cores: a CUDA job is charged 1 core
            # but still needs threads for its CPU-side work)
            threads=worker_threads(calc))

        model = calc.config.mlip_model or "MACE"
        verb = {"sp": "single-point energy", "freq": "frequencies",
                "opt_freq": "optimizing + frequencies"}.get(task, "optimizing")
        self.cb.log(f"[{calc.name}] ({calc.kind}) {verb} with {model} via {python}...",
                    "info", calc.name)
        # Persist the result path up front (mirror of the ORCA launch path): if
        # the worker races to completion just as the app shuts down, the next
        # launch's reconcile can still judge it DONE from the JSON on disk.
        calc.output_path = str(result_json)
        self.cb.calc_update(index, calc)
        # Remove a stale result JSON before launching: folders survive removal,
        # so a removed same-named calc's JSON could otherwise be adopted as THIS
        # calc's DONE result if the new worker dies without writing one (e.g. a
        # torch native crash — the exit-code check below has nothing to read
        # a fresh result from).
        try:
            result_json.unlink()
        except OSError:
            pass
        runner = self._use_runner(MlipRunner(python))
        try:
            rc = runner.run(script_path, [str(config_path)], out_path, cwd=calc_dir,
                            on_line=lambda ln: self.cb.log(ln, "orca", calc.name))
        finally:
            self._job.runner = None
        if not result_json.exists():
            raise OrcaRunError(
                f"MLIP worker exited (code {rc}) without writing a result — "
                "see the run log / .out for the underlying error.")

        self._finish_ok(calc, index, parse_mlip_result(str(result_json)), result_json)

    # -- single CREST conformer search (runs in WSL, OUTSIDE the ORCA pipeline) --
    def _run_crest_calc(self, calc: Calculation, index: int) -> None:
        """Run a crest_* calc: a CREST conformer search. Like ORCA it is launched
        DETACHED (survives app close) and reattached across a restart; the
        resolved TARGET -- a WSL distro on Windows, this machine on Linux, see
        crest/shell.py -- is stashed on config.crest_env_id so a reattach knows
        where the job runs. Mirrors _run_calc's fresh-launch/reattach shape but
        uses CrestRunner + parse_crest_result."""
        from ..crest.runner import CrestRunner, write_crest_run_files, build_crest_argv
        from ..crest.env import resolve_run_target

        # Reattach: where it was launched -- the output path recorded at
        # launch (it survives a change of workspace setting, as for ORCA),
        # else the recorded run folder.
        calc_dir = self._run_folder(calc)
        out_path = calc_dir / f"{calc.name}.out"
        if calc.state == CalcState.RUNNING and calc.output_path:
            out_path = Path(calc.output_path)

        # A search left running on a remote device: a question for the device
        # (remote/runner.py), exactly as for an ORCA job there -- alive: sync
        # on; dead: fetch the folder, then the local result path reads it.
        if calc.state == CalcState.RUNNING and calc.device:
            runner = self._use_runner(self._remote_runner(calc, index, calc.device))
            runner.adopt(calc.pid, calc.create_time)
            calc.output_path = str(out_path)
            self.cb.log(f"[{calc.name}] reattaching to CREST still running...",
                        "info", calc.name)
            self.cb.calc_update(index, calc)
            try:
                self._crest_monitor_and_finish(calc, index, out_path, calc.name,
                                               start_pos=runner.end_position(out_path))
            finally:
                self._job.runner = None
            return

        # Reattach path: left RUNNING by a previous session. We only need the
        # distro (persisted on config.crest_env_id) + pid/create_time to check
        # liveness and resume tailing; the binary path isn't needed for monitoring.
        if calc.state == CalcState.RUNNING and calc.pid:
            distro = (getattr(calc.config, "crest_env_id", "") or self.crest_distro or "").strip()
            runner = CrestRunner(distro) if distro else None
            if runner is not None:
                runner.adopt(calc.pid, calc.create_time)
            if runner is not None and runner.is_alive():
                self._use_runner(runner)
                self.cb.log(f"[{calc.name}] reattaching to CREST still running "
                            f"(pid {calc.pid})...", "info", calc.name)
                self.cb.calc_update(index, calc)
                start_pos = runner.end_position(out_path)
                try:
                    self._crest_monitor_and_finish(calc, index, out_path,
                                                   calc.name, start_pos=start_pos)
                finally:
                    self._job.runner = None
                return
            # Not alive (or distro unknown): the job finished while we were away.
            # The detached script already copied the ensemble back + wrote the .rc
            # marker, so JUDGE it from those files — never relaunch (that would
            # overwrite a completed result). A crash without an ensemble parses to
            # zero conformers and validate_result fails it, as it should.
            self.cb.log(f"[{calc.name}] CREST finished while the app was closed; "
                        "reading results...", "info", calc.name)
            self._crest_monitor_and_finish(calc, index, out_path, calc.name,
                                           already_done=True)
            return

        # Fresh launch.
        calc.state = CalcState.RUNNING
        calc.message = ""
        calc_dir = self._claim_run_folder(calc)     # started: its folder is decided
        out_path = calc_dir / f"{calc.name}.out"
        self.cb.calc_update(index, calc)

        xyz = self._resolve_geometry(calc)
        if not xyz.strip():
            raise OrcaRunError("No coordinates for CREST (geometry is empty).")

        # placed on a remote device (_place): its own CREST runs it there
        device = self._job.device
        dev = self._devices.get(device) if device else None
        if device:
            distro, crest_bin = "", getattr(dev, "crest_path", "") if dev else ""
        else:
            preferred = (getattr(calc.config, "crest_env_id", "") or self.crest_distro or "").strip()
            try:
                distro, crest_bin = resolve_run_target(preferred)
            except RuntimeError as e:
                raise OrcaRunError(str(e)) from e
            # Persist the resolved distro so a reattach after restart knows where the
            # job runs (session.json round-trips config).
            try:
                calc.config.crest_env_id = distro
            except (AttributeError, TypeError):
                pass

        # The constraints (--cinp) name atoms of the structure that runs: one it
        # does not have is refused here, before CREST is started -- the same
        # rule build_input applies to an ORCA calc. Coordinates in hand were
        # checked when the calc was added; a referenced geometry exists only now.
        constraints = list(getattr(calc.config, "geom_constraints", None) or [])
        if constraints:
            from .input_generator import constraint_index_error
            n_atoms = int(as_xyz_file(xyz).split("\n", 1)[0].strip() or 0)
            err = constraint_index_error(constraints, n_atoms)
            if err:
                raise OrcaRunError(err)

        # A CREST whose --cinp parser takes "force constant=" / "reference="
        # gets them (the 3.0.2 release segfaults on either, so it is asked of
        # the binary, crest/env.py); otherwise the file stays the shape every
        # build runs and CREST's own force constant applies -- said once.
        extended = False
        if constraints:
            if device:
                # the DEVICE's CREST, as its probe found it -- not this machine's
                extended = bool(getattr(dev, "crest_extended", False)) if dev else False
            else:
                from ..crest.env import cinp_extended
                extended = cinp_extended(distro, crest_bin)
            if not extended:
                self.cb.log(f"[{calc.name}] this CREST build cannot take a constraint "
                            "force constant (the 3.0.2 release crashes on it); CREST's "
                            "own default applies.", "warn", calc.name)

        calc_dir.mkdir(parents=True, exist_ok=True)
        argv = build_crest_argv(calc.config, calc.charge, calc.multiplicity)
        script_path = write_crest_run_files(
            calc_dir, calc.name, xyz, crest_bin, argv, constraints=constraints,
            extended=extended,
            force_constant=float(getattr(calc.config, "crest_force_constant", 0.0) or 0.0))

        method = getattr(calc.config, "crest_method", "") or "gfn2"
        if device:
            # its remote folder is decided and recorded (persisted with the
            # calc_update) before anything is started there (design §6.2)
            calc.device = device
            calc.remote_dir = remote_dir_for(dev.workdir if dev else "",
                                             calc.run_dir, launch_stamp())
            self.cb.calc_update(index, calc)
            self.cb.log(f"[{calc.name}] (crest_conf) running CREST [{method}] ...",
                        "info", calc.name)
            runner = self._use_runner(self._remote_runner(calc, index, device,
                                                          crest_flags=argv))
            try:
                pid, create_time = runner.launch(calc_dir / f"{calc.name}.xyz", out_path)
                calc.pid = pid
                calc.create_time = create_time
                calc.output_path = str(out_path)
                self.cb.calc_update(index, calc)
                self._crest_monitor_and_finish(calc, index, out_path, calc.name)
            finally:
                self._job.runner = None
            return
        calc.device = ""
        calc.remote_dir = ""
        # Name where it runs, because the answer differs per machine and the log
        # is where a user diagnoses "which CREST did this actually use".
        from ..crest.shell import is_local as _crest_is_local
        where = "locally" if _crest_is_local() else f"in WSL:{distro}"
        self.cb.log(f"[{calc.name}] (crest_conf) running CREST [{method}] "
                    f"{where} ...", "info", calc.name)
        runner = self._use_runner(CrestRunner(distro))
        try:
            pid, create_time = runner.launch(script_path, calc.name, out_path)
            calc.pid = pid
            calc.create_time = create_time
            # Persist pid + output_path immediately so a reattach (or a
            # judge-from-files after the app was closed) works even if ORCAdesk is
            # closed seconds after the job starts. The detached WSL script writes
            # results + a .rc marker here regardless of whether we're watching.
            calc.output_path = str(out_path)
            self.cb.calc_update(index, calc)
            self._crest_monitor_and_finish(calc, index, out_path, calc.name)
        finally:
            self._job.runner = None

    def _crest_monitor_and_finish(self, calc: Calculation, index: int, out_path,
                                  name: str, start_pos: int = 0,
                                  already_done: bool = False) -> None:
        """Parse the CREST ensemble + validate + mark DONE. Normally tails the
        output until the run finishes first; with ``already_done`` (the job ended
        while the app was closed) it skips straight to parsing the files the
        detached script left behind. OrcaCancelled / OrcaDetached propagate to
        run_all."""
        from ..crest.parser import parse_crest_result

        if not already_done:
            runner = self._job.runner
            if isinstance(runner, RemoteOrcaRunner):
                # remote: returns once the whole folder is back (and verified)
                runner.monitor(out_path,
                               on_line=lambda ln: self.cb.log(ln, "orca", calc.name),
                               start_pos=start_pos)
            else:
                runner.monitor(
                    out_path, name,
                    on_line=lambda ln: self.cb.log(ln, "orca", calc.name),
                    start_pos=start_pos)

        result = parse_crest_result(str(out_path))
        n = len(result.conformers)
        self._finish_ok(calc, index, result, out_path,
                        message=f"Completed — {n} conformer(s).",
                        log_msg=f"done — {n} conformer(s).")
        self._export_crest_conformers(calc, Path(out_path).parent)
        # the steps put after it for "every conformer it finds" become one
        # per conformer now (the scan would find them too; this is at once)
        self._expand_conformers(calc, n)

    def _expand_conformers(self, crest: Calculation, n: int) -> None:
        """Replace the conformer placeholders of the DONE search ``crest``
        (N = ``n``) and everything hanging off them by N copies
        (expand_conformer_placeholders), as ONE change of the queue under its
        lock -- through the store when one is wired (it saves the session),
        else on the list this run walks. Called OUTSIDE the scan and the lock
        (from the dispatcher, or the job thread that finished the search):
        the names that already hold output on disk are looked up first,
        without the lock (P37), so a copy never takes a folder with old
        results in it. Idempotent: with no placeholder left it does nothing.
        Logs one line."""
        with self._queue_lock:
            walk = self._walk
            if walk is None:
                return
            roots, down = conformer_placeholder_chain(walk, crest.name)
            bases = [c.name for c in roots + down]
        if not bases:
            return
        n = max(0, int(n))
        try:
            # the base itself and the next two a collision moves to
            disk = {x.casefold() for x in names_with_output(
                self.workspace_root,
                [f"{b}{s}_c{k}" for b in bases for s in ("", "2", "3")
                 for k in range(1, n + 1)])}
        except OSError:
            disk = set()
        if self.cb.expand_placeholders is not None:
            report = self.cb.expand_placeholders(crest.name, n, disk)
        else:
            with self._queue_lock:
                report = expand_conformer_placeholders(
                    walk, crest.name, n, busy=self._active_names.__contains__,
                    disk_taken=disk)
        with self._slot_cv:
            # whatever placeholder of it is still here was refused (or
            # appeared meanwhile): its next pick is a verdict, not a retry
            for c in walk:
                if is_conf_placeholder(c) and c.ref_name == crest.name:
                    self._expand_tried[id(c)] = c
            self._slot_cv.notify_all()
        if not report:
            return
        who = ", ".join(report["placeholders"])
        if report["blocked"]:
            self.cb.log(f"[{crest.name}] could not expand {who}: {report['blocked']}.",
                        "warn", crest.name)
        elif report["n"] == 0:
            self.cb.log(f"[{crest.name}] found no conformers: {who} filtered.",
                        "info", crest.name)
        else:
            self.cb.log(f"[{crest.name}] expanded {who} into {report['members']} "
                        f"copies (+{report['downstream']} downstream)", "info",
                        crest.name)

    def _export_crest_conformers(self, calc: Calculation, calc_dir) -> None:
        """Auto-split the finished ensemble into per-conformer ``.xyz`` files under
        ``conformers/`` — every CREST run leaves individual files without a manual
        export (the Results-tab button re-runs the same split). Best-effort: a
        missing/empty ensemble or a write error is logged, never fatal, so it can't
        turn a DONE search into a FAILED one."""
        from ..crest.export import export_conformers

        try:
            written = export_conformers(Path(calc_dir) / "crest_conformers.xyz",
                                        Path(calc_dir) / "conformers", calc.name)
        except (FileNotFoundError, ValueError, OSError) as e:
            self.cb.log(f"[{calc.name}] conformer .xyz export skipped: {e}",
                        "warn", calc.name)
            return
        self.cb.log(f"[{calc.name}] exported {len(written)} conformer(s) "
                    f"to conformers/.", "info", calc.name)

    # -- dependency-scoped failure propagation --
    def _blocked_by(self, calc: Calculation) -> "Optional[Calculation]":
        """The FAILED/BLOCKED calculation this row's geometry reference chain
        reaches, or None.

        Read live from the queue every time, rather than from a set of names
        filled in at run start. The queue is live (P29) and rows are tracked by
        object identity, but a name is not an identity: a BLOCKED row edited to
        point at a healthy parent — or removed and re-added with DIRECT
        geometry — kept its old name and was blocked again by a run-start
        decision about a calculation that no longer exists.

        Walking the chain also makes transitivity independent of queue order: a
        row whose parent is still PENDING but whose grandparent has FAILED is
        blocked on the spot, without waiting for the parent to be stamped first.
        A dangling or self-reference is NOT blocked here — _resolve_geometry
        reports those precisely, and a vague "a dependency failed" would be a
        worse message.

        A row has two parents: its geometry reference and the calc it runs
        `after` (the queue editor's edge). Both are dependencies, so the walk
        follows both — a FAILED/BLOCKED ancestor on either chain blocks.
        """
        stack, walked = [calc], {calc.name}
        while stack:
            cur = stack.pop()
            for pname in _parent_names(cur):
                if pname in walked:
                    continue
                walked.add(pname)
                parent = self._by_name.get(pname)
                if parent is None or parent is cur:
                    continue
                if parent.state in (CalcState.FAILED, CalcState.BLOCKED):
                    return parent
                if parent.state == CalcState.FILTERED:
                    # terminal and never ran: its dependents are FILTERED
                    # (_filtered_by), whatever lies further up its chain
                    continue
                if parent.state == CalcState.DONE:
                    # The chain ENDS at a finished parent. Its geometry is on
                    # disk and frozen (P24), so it is self-sufficient —
                    # whatever failed further up the chain was needed to
                    # PRODUCE it, and that already happened. Walking past it
                    # blocked a row whose direct parent was DONE and
                    # perfectly usable.
                    continue
                stack.append(parent)
        return None

    def _filtered_by(self, calc: Calculation) -> "Optional[Calculation]":
        """The FILTERED calc this row depends on directly (its geometry
        reference or the calc it runs `after`), or None. Its dependents are
        FILTERED in turn -- never BLOCKED (nothing failed), never FAILED. One
        level is enough: a filtered parent's own dependents are stamped as
        the walk reaches each of them, parent first (_deps_ready)."""
        for pname in _parent_names(calc):
            parent = self._by_name.get(pname)
            if (parent is not None and parent is not calc
                    and parent.state == CalcState.FILTERED):
                return parent
        return None

    # -- the energy condition on an edge (filter_de / filter_top) --
    def _settled(self, parent: Calculation, seen: dict) -> bool:
        """Whether ``parent`` will not change state again in this run -- the
        rule _deps_ready waits on: not RUNNING, not picked, and not a PENDING
        or CANCELLED row this run has yet to handle."""
        if parent.state == CalcState.RUNNING or parent.name in self._active_names:
            return False
        return not (parent.state in (CalcState.PENDING, CalcState.CANCELLED)
                    and id(parent) not in seen)

    @staticmethod
    def _filter_group(calc: Calculation, calcs: list) -> "list[Calculation]":
        """The calcs ``calc``'s energy condition ranks it among: the members of
        its stack (same system and the same, non-empty node) that carry a
        condition too. A calc with no node is a group of one."""
        if not calc.node:
            return [calc]
        group = [c for c in calcs if c.node == calc.node
                 and c.system == calc.system and has_filter(c)]
        return group if any(c is calc for c in group) else group + [calc]

    def _score_of(self, m: Calculation, seen: dict) -> tuple:
        """What ranks group member ``m``, read from memory only (this runs
        under the store lock, P37 -- a result not loaded yet is asked for with
        "load" and read by the dispatcher outside the lock):

        * ("score", energy_eh, reference_eh | None) -- a conf:<k> member is
          scored by conformer k's energy, its reference the ensemble's lowest
          (conformer 1); any other member by its parent's final_energy_eh
          (the calc it runs `after`, else its geometry reference), its
          reference the lowest score of the group (None here);
        * ("wait",) -- the parent may still change state in this run;
        * ("load", parent) -- a DONE parent whose result is not in memory;
        * ("past_end", n) -- conf:<k> with k > n conformers;
        * ("no_energy", message) -- a DONE parent with nothing to rank by;
        * ("no_parent",) / ("not_done", parent) -- nothing to rank, by the
          normal dependency rules' account (a FAILED parent blocks, etc.)."""
        if is_conf_placeholder(m):
            # it becomes the stack's members once its search has finished
            # (_expand_conformers); until then nothing is ranked against it
            if m.state in (CalcState.PENDING, CalcState.CANCELLED):
                return ("wait",)
            return ("no_parent",)
        is_ref = m.geometry_source == GeometrySource.REFERENCE
        k = conf_index(m.ref_point) if is_ref else 0
        pname = m.ref_name if k else (m.after or (m.ref_name if is_ref else ""))
        parent = self._by_name.get(pname) if pname else None
        if parent is None or parent is m:
            return ("no_parent",)
        if not self._settled(parent, seen):
            return ("wait",)
        if parent.state != CalcState.DONE:
            return ("not_done", parent)
        if parent.result is None:
            if id(parent) not in self._load_tried:
                return ("load", parent)
            return ("no_energy", f"the result of '{parent.name}' could not be read")
        if k:
            confs = list(getattr(parent.result, "conformers", None) or ())
            if not confs:
                return ("no_energy", f"'{parent.name}' holds no conformer ensemble")
            if k > len(confs):
                return ("past_end", len(confs))
            return ("score", confs[k - 1].energy_eh, confs[0].energy_eh)
        energy = getattr(parent.result, "final_energy_eh", None)
        if energy is None:
            return ("no_energy", f"'{parent.name}' finished without a final energy")
        return ("score", float(energy), None)

    def _judge_filter(self, calc: Calculation, calcs: list, seen: dict):
        """The energy condition's verdict on ``calc``, whose own dependencies
        are ready (_deps_ready), under the store lock, from memory only:

        * None -- nothing to judge, or it passes (on to its approval gate);
        * "wait" -- the barrier: a member of its group still has a parent that
          may change state in this run (or a result still to load) -- left
          PENDING and unseen, like any unmet dependency;
        * (CalcState, message) -- FILTERED (or BLOCKED, editable) without
          running, stamped by _precheck.

        A conf:<k> reference past the end of the ensemble is FILTERED with or
        without a condition. With one, the members whose parent is DONE are
        ranked by score (ascending, ties by conformer number then queue order, rank 0-based): ``calc``
        passes iff (filter_top == 0 or rank < filter_top) and (filter_de is
        None or (score - reference) * HARTREE_TO_KCAL_MOL <= filter_de, to
        1e-9) -- the reference being conformer 1 of the CREST for a conf:<k>
        member, so a lone conf:5 is still judged against the ensemble's
        minimum, and the group's lowest score otherwise. Members whose parent
        is not DONE are left out of the ranking (each follows the normal
        rules: a FAILED parent blocks it, a FILTERED one filters it). A DONE
        parent with no energy to rank by makes its member BLOCKED (editable:
        remove the condition, or fix the parent) and leaves it out."""
        if conf_index(calc.ref_point) and calc.geometry_source == GeometrySource.REFERENCE:
            own = self._score_of(calc, seen)
            if own[0] == "load":
                self._to_load[id(own[1])] = own[1]
                return "wait"
            if own[0] == "past_end":
                return (CalcState.FILTERED, f"Filtered: CREST found only {own[1]} conformers")
        # a rerun row (a set put on a DONE/FAILED block behind an overwrite
        # warning, store rerun_calc) was asked for explicitly: the condition
        # does not screen it out again
        if not has_filter(calc) or getattr(calc, "rerun", False):
            return None
        scored: "list[tuple[float, str, Calculation, Optional[float]]]" = []
        own = None
        waiting = False
        for m in self._filter_group(calc, calcs):
            st = self._score_of(m, seen)
            if m is calc:
                own = st
            if st[0] == "wait":
                waiting = True
            elif st[0] == "load":
                self._to_load[id(st[1])] = st[1]
                waiting = True
            elif st[0] == "score":
                scored.append((st[1], m.name, m, st[2]))
        if waiting:
            return "wait"
        if own is None or own[0] == "no_parent":
            return (CalcState.BLOCKED, "Skipped: the energy condition on its edge has "
                                       "no parent energy to compare (it runs after "
                                       "no calculation).")
        if own[0] == "no_energy":
            return (CalcState.BLOCKED, "Skipped: the energy condition on its edge "
                                       f"cannot be judged: {own[1]}.")
        if own[0] == "past_end":
            return (CalcState.FILTERED, f"Filtered: CREST found only {own[1]} conformers")
        if own[0] == "not_done":
            return (CalcState.BLOCKED, f"Skipped: '{own[1].name}' did not finish.")
        # ties (equal energies -- CREST frames without one read as 0.0)
        # go by conformer number, then queue order: never by name as
        # text, where _c10 sorts before _c2
        pos = {id(x): n for n, x in enumerate(calcs)}
        scored.sort(key=lambda t: (t[0], conf_index(t[2].ref_point),
                                   pos.get(id(t[2]), 0), t[1]))
        rank = next(i for i, t in enumerate(scored) if t[2] is calc)
        score, _, _, reference = scored[rank]
        if reference is None:
            reference = scored[0][0]
        de = (score - reference) * HARTREE_TO_KCAL_MOL
        if calc.filter_de is not None and de > calc.filter_de + 1e-9:
            return (CalcState.FILTERED,
                    f"Filtered: ΔE {de:.2f} kcal/mol > {calc.filter_de:.2f}")
        if calc.filter_top and rank >= calc.filter_top:
            return (CalcState.FILTERED,
                    f"Filtered: rank {rank + 1} > top {calc.filter_top}")
        return None

    def _load_results(self, parents: list) -> None:
        """Read the results the energy condition asked for ("load"), outside
        the store lock (a large .out takes a second, P37). Writing a DONE
        calc's result here is safe through the DONE-frozen invariant (P24),
        as for _resolve_geometry's parse-on-miss. Each is tried once a run: a
        result that cannot be read is "no energy" from then on."""
        for p in parents:
            if p.result is None and p.output_path:
                try:
                    if Path(p.output_path).is_file():
                        p.result = result_from_output(p)
                except Exception as e:
                    self.cb.log(f"[{p.name}] could not read its result for an "
                                f"energy condition ({e}).", "warn", p.name)
        with self._slot_cv:
            for p in parents:
                self._load_tried[id(p)] = p

    def _failure_reason(self, calc: Calculation, runner_msg: str) -> str:
        """Turn a raw runner error into a user-facing reason. If ORCA wrote an
        .out during THIS attempt, read the actual failure cause from it (SCF not
        converged, etc.); otherwise fall back to the runner's message.
        'Cancelled' passes through."""
        if "cancel" in runner_msg.lower():
            return runner_msg
        # MLIP/CREST failures already carry a precise reason (worker error / non-
        # convergence / WSL launch failure); their .out is not an ORCA file, so
        # don't run it through the ORCA parser (it would invent an ORCA-style
        # message and clobber the real one).
        if calc.kind.startswith("mlip") or calc.kind.startswith("crest"):
            return runner_msg
        # Pre-launch failure (geometry resolution, input generation, the launch
        # itself): any .out in the folder predates this attempt, so its parsed
        # cause would be a stale diagnosis — keep the real error (A22).
        if not self._orca_launched:
            return runner_msg
        try:
            # calc.output_path is where THIS attempt wrote, recorded at launch —
            # and a reattached job (or one from a session whose workspace has
            # since changed) does not write under the current workspace root.
            # Re-deriving the path there read a stale .out from an unrelated,
            # same-named run and quoted its cause; on a FAILED calc that
            # diagnosis is permanent, because FAILED is locked (P24).
            out_path = Path(calc.output_path) if calc.output_path else (
                self._run_folder(calc) / f"{calc.name}.out")
            if out_path.exists():
                r = parse_file(str(out_path))
                if r.error_message:
                    return r.error_message
        except Exception as e:
            # Diagnosing the failure must not itself fail silently — fall back to
            # the raw runner message, but leave a breadcrumb so a broken parse
            # path is discoverable instead of swallowed.
            self.cb.log(f"[{calc.name}] could not parse .out for a detailed "
                        f"reason ({e}).", "warn", calc.name)
        return runner_msg

    # -- the whole queue --
    def run_all(self, calcs: list[Calculation]) -> None:
        with self._queue_lock:
            self._by_name = {c.name: c for c in calcs}
            # Pin the keep-existing answers to the rows they were given about.
            self._skip_ids = {id(c) for c in calcs if c.name in self._skip_names}
            self._verdicts.clear()
            self._to_load.clear()
            self._load_tried.clear()
            self._to_expand.clear()
            self._expand_tried.clear()
            self._walk = calcs
            # A row still BLOCKED from a previous run whose ancestry is now
            # clean (its failed parent was removed, or its reference
            # re-pointed) WILL run this pass — reset it up front so the visible
            # queue never shows a will-run row still labelled with the stale
            # "Skipped: a dependency failed." until the walk reaches it. The
            # converse — a dependent of a calc that is ALREADY FAILED (P24:
            # locked, never re-run) — needs no seeding: _blocked_by reads the
            # live chain at pick time, so it is blocked when it is reached.
            for idx, c in enumerate(calcs):
                if c.state == CalcState.BLOCKED and self._blocked_by(c) is None:
                    c.state = CalcState.PENDING
                    c.message = ""
                    self.cb.calc_update(idx, c)

        # The walk is over a LIVE list (P29): the owning store shares this very
        # list (and the lock), and allows editable-state rows to be added,
        # removed, edited, or reordered while the run is in progress. So the
        # walk cannot be a positional `for … in enumerate` — each iteration
        # picks, under the lock, the first list row not yet handled (tracked by
        # object identity: an edited row is a NEW object and is picked again,
        # which is exactly what "edited back to PENDING" should do).
        # `seen` maps id() -> the object itself: the value pins the object so a
        # removed row's id can't be recycled onto a new row mid-run.
        seen: dict[int, Calculation] = {}
        watch = None
        if self._device_entries and self._device_probe is not None:
            self._watch_stop.clear()
            watch = threading.Thread(target=self._watch_devices, daemon=True,
                                     name="orcadesk-device-watch")
            watch.start()
        try:
            self._run_walk(calcs, seen)
        finally:
            self._watch_stop.set()
            with self._queue_lock:
                self._active_names.clear()
                self._slots.clear()

    def _watch_devices(self) -> None:
        """Re-probe the enabled remote devices while the run lasts: at once for
        those not online at the start, then all of them every DEVICE_WATCH
        seconds. A probe that finds a device ready re-resolves its budget (a
        device off at the start had none) and reports it reachable, so the
        store can settle its clean-up list (design §6.3)."""
        first = True
        while not self._watch_stop.is_set():
            for entry in list(self._device_entries):
                alias = entry["alias"]
                with self._queue_lock:
                    dev = self._devices.get(alias)
                if first and dev is not None and dev.online:
                    continue
                try:
                    info = self._device_probe(entry)
                except Exception:
                    info = None
                if self._watch_stop.is_set():
                    return
                fresh = resolve_device(entry, info)
                with self._slot_cv:
                    self._devices[alias] = fresh
                    # A job reattached before the device's first probe came
                    # back got a runner with no workdir, so the delete guard
                    # refused every folder it fetched (15 on the AWS device
                    # after a restart, 2026-11-23: "outside ''"). Hand it the
                    # probed workdir now; only an empty one is filled, never
                    # a recorded one changed.
                    if fresh.workdir:
                        for s in self._slots.values():
                            r = s.runner
                            if (s.device == alias and r is not None
                                    and hasattr(r, "workdir") and not r.workdir):
                                r.workdir = fresh.workdir
                    self._slot_cv.notify_all()
                if fresh.online:
                    try:
                        self.cb.device_reachable(alias)
                    except Exception:
                        pass
            first = False
            self._watch_stop.wait(self.DEVICE_WATCH)

    def _device_offline(self, alias: str) -> None:
        """A device failed to take a job: no more jobs for it until the watch
        thread finds it ready again (design §5)."""
        with self._slot_cv:
            dev = self._devices.get(alias)
            if dev is not None:
                dev.online = False
            self._slot_cv.notify_all()

    def _run_walk(self, calcs: list[Calculation], seen: dict) -> None:
        """One pass over the live queue, running up to `budget.max_jobs`
        calculations at a time.

        Picking stays serialized in this one dispatcher thread; each picked calc
        then gets its OWN thread, its own backend runner and its own reserved
        slice of the core/RAM budget, released when it finishes. So the queue
        order, the live-queue rules (P29) and the per-calc pipeline are exactly
        what they were single-threaded — only the number of jobs in flight
        changed.

        The scan takes the first row that is (a) not yet handled, (b)
        dependency-ready and (c) affordable:

        * dependency-ready — a REFERENCE calc whose parent may still become DONE
          is DEFERRED, not picked. Sequential running got this for free (the
          parent always finished first); in parallel the child would otherwise
          be picked while its parent is still RUNNING and die in geometry
          resolution.
        * affordable — declared cores + estimated RAM still fit the budget
          (core/resources.py). A row that does not fit is skipped OVER rather
          than blocking the rows behind it; if nothing fits and nothing is in
          flight, the first ready row runs alone over budget, so an oversized
          calculation can never starve.
        """
        threads: "list[threading.Thread]" = []
        try:
            while True:
                to_load: "list[Calculation]" = []
                to_expand: "list[tuple]" = []
                with self._slot_cv:
                    if self._should_stop(calcs):
                        break
                    if self._unsee:
                        # a device could not take these (RemoteUnavailable):
                        # offer them again, to wherever has room now
                        for cid in self._unsee:
                            seen.pop(cid, None)
                        self._unsee.clear()
                    self._to_load = {}
                    self._to_expand = {}
                    picked = self._pick_ready(calcs, seen)
                    if picked is None and (self._to_load or self._to_expand):
                        # An energy condition (or a placeholder) needs results
                        # not in memory yet, or a finished search's
                        # placeholders are to be replaced: done outside the
                        # lock (below), then scan again.
                        to_load = list(self._to_load.values())
                        to_expand = list(self._to_expand.values())
                    elif picked is None:
                        if self._slots:
                            # A job in flight will free budget (or satisfy the
                            # dependency the rest are waiting on) and wake us.
                            # This is checked BEFORE "every row is seen": the
                            # queue is live (P29), so while a job runs the user
                            # can still append/edit a row, and the walk has to
                            # be here to see it. Exiting because the rows known
                            # a moment ago were all handled would strand that
                            # row PENDING in a run that says "Queue finished."
                            self._slot_cv.wait(_SLOT_POLL)
                            continue
                        if all(id(c) in seen for c in calcs):
                            break     # queue exhausted, nothing left in flight
                        if not self._any_pool_running():
                            # drained (Stop after current, or every pool
                            # stopped) and nothing in flight: end the run
                            self._end_of_drain(calcs)
                            break
                        if not self._pool_takes(""):
                            # this machine takes nothing new and no running
                            # device can take what is left right now -- never
                            # read as a cycle. A running device that is off
                            # may come back (the watch re-probes it): wait.
                            # Every running device up and still nothing it can
                            # take (local-only rows, or nothing ready): the run
                            # is over for now.
                            if any(self._pool_takes(a) and not d.online
                                   for a, d in self._devices.items()):
                                self._slot_cv.wait(_SLOT_POLL)
                                continue
                            self._end_of_drain(calcs)
                            break
                        # Nothing running and nothing affordable: let the first
                        # ready row through regardless of budget (see above).
                        picked = self._pick_ready(calcs, seen,
                                                  ignore_budget=True)
                        if picked is None:
                            # ...and nothing is even dependency-ready, with no
                            # job left to unblock it: the rest is a cycle.
                            self._block_unreachable(calcs, seen)
                            break
                    if picked is not None:
                        i, calc = picked
                        seen[id(calc)] = calc
                        self._active_names.add(calc.name)
                if picked is None:
                    self._load_results(to_load)
                    for crest, n in to_expand:
                        self._expand_conformers(crest, n)
                    continue
                if not self._precheck(calc, i):
                    self._release(calc.name)
                    continue
                if self._try_keep_existing(calc, i):
                    self._release(calc.name)
                    continue
                started = self._start_job(calc, i, calcs, threads)
                if started == "requeue":
                    # It stopped fitting while we waited (live memory moved).
                    # Un-see it so the scan can offer it again — and meanwhile
                    # reach the rows behind it, which may well fit.
                    with self._slot_cv:
                        seen.pop(id(calc), None)
                    continue
                if not started:
                    break
        finally:
            # The run is over only when every job it started has come back:
            # the store flips its "running" flag on this call returning, and a
            # detached/cancelled job still has state to stamp.
            for t in threads:
                t.join()

    def _pick_ready(self, calcs: list[Calculation], seen: dict,
                    ignore_budget: bool = False):
        """(index, calc) of the first row that may be handled now, or None.

        Caller must hold the lock: the name map, the seen-stamp and the
        _active_names entry have to land atomically so the store can refuse
        mutations of a calc that is picked but not yet stamped RUNNING (the
        pick->stamp window).
        """
        # names must track the live list: mid-run adds resolve their references
        # against the queue as it is NOW, not as it was at run start
        self._by_name = {c.name: c for c in calcs}
        # Two passes for the devices' preferences (remote/devices.py): the
        # first places a row only on this machine, on a device that prefers
        # its kind, or on a device with no preference; only when that places
        # nothing may a device take a kind it does not prefer -- so a spot VM
        # that prefers single points takes the SP behind an Opt in the queue,
        # and still takes the Opt when no SP is ready.
        picked = self._pick_pass(calcs, seen, ignore_budget, relax=False)
        if picked is None and not ignore_budget and self._devices:
            picked = self._pick_pass(calcs, seen, ignore_budget, relax=True)
        return picked

    def _pick_pass(self, calcs: list[Calculation], seen: dict,
                   ignore_budget: bool, relax: bool):
        """One scan of _pick_ready; ``relax`` lets a device take a kind it
        does not prefer. Caller holds the lock."""
        for j, c in enumerate(calcs):
            if id(c) in seen:
                continue
            # Asked BEFORE _deps_ready: a DONE/FAILED/blocked row is disposed
            # of by _precheck without launching anything, so waiting for its
            # geometry reference is pointless — and when those references form
            # a cycle it left the row unhandled all the way to
            # _block_unreachable, which used to stamp a frozen DONE result (or
            # a locked FAILED, or a reattachable RUNNING job) BLOCKED.
            if self._handled_without_running(c):
                return j, c
            if not self._deps_ready(c, seen):
                continue
            # a conformer placeholder is never dispatched: once its search is
            # settled it is replaced (outside the scan), or given a verdict
            if is_conf_placeholder(c) and c.state != CalcState.RUNNING:
                verdict = self._placeholder_verdict(c)
                if verdict is None:
                    continue
                self._verdicts[id(c)] = (c, verdict)
                return j, c
            # The energy condition on its edge is judged BEFORE its approval
            # gate: a row it screens out is FILTERED, never left waiting for
            # an approval that would be pointless. Only a row that has not
            # started is judged: a RUNNING one is a detached job to reattach,
            # and filtering (or blocking) it would orphan that job.
            verdict = (None if c.state == CalcState.RUNNING
                       else self._judge_filter(c, calcs, seen))
            if verdict == "wait":
                continue
            if verdict is not None:
                self._verdicts[id(c)] = (c, verdict)
                return j, c
            if self._gate_holds(c):
                continue
            if ignore_budget:
                # the over-budget fallback runs a row on this machine: never
                # while this machine's pool is draining or stopped
                if c.state != CalcState.RUNNING and not self._pool_takes(""):
                    continue
                self._placed[c.name] = (c.device if c.state == CalcState.RUNNING
                                        else "")
                return j, c
            where = self._place(c, relax=relax)
            if where is not None:
                self._placed[c.name] = where
                return j, c
        return None

    def _placeholder_verdict(self, calc: Calculation):
        """What becomes of a conformer placeholder whose search is settled
        (its dependencies are ready), under the lock, from memory only: None
        -- not ready, its search's result is asked for (_to_load) or its
        replacement is (_to_expand), both done outside the scan -- or a
        (BLOCKED, message) verdict for _precheck to stamp. A FAILED or
        BLOCKED search never gets here: _handled_without_running blocks the
        placeholder by the normal rule first, and a FILTERED one filters it."""
        crest = self._by_name.get(calc.ref_name)
        if calc.state == CalcState.BLOCKED or id(calc) in self._expand_tried:
            # an attempt this run left it in place (a run's start resets a
            # BLOCKED row whose dependencies are clean, so this is one)
            return (CalcState.BLOCKED, calc.message or (
                "Skipped: it could not become one calculation per conformer "
                f"of '{calc.ref_name}'."))
        if crest is None or crest is calc:
            return (CalcState.BLOCKED, f"Skipped: '{calc.ref_name}' is not in the queue.")
        if crest.state != CalcState.DONE:
            return (CalcState.BLOCKED, f"Skipped: '{crest.name}' did not finish.")
        if crest.result is None:
            if id(crest) not in self._load_tried:
                self._to_load[id(crest)] = crest
                return None
            return (CalcState.BLOCKED, "Skipped: the conformers of "
                                       f"'{crest.name}' could not be read.")
        confs = getattr(crest.result, "conformers", None) or ()
        if not confs:
            return (CalcState.BLOCKED, f"Skipped: '{crest.name}' holds no "
                                       "conformer ensemble.")
        self._to_expand[id(crest)] = (crest, len(confs))
        return None

    def _deps_ready(self, calc: Calculation, seen: dict) -> bool:
        """False while this calc's geometry reference may still become DONE, so
        the dispatcher leaves it for a later scan instead of failing it.

        "May still become DONE" is about what this RUN will do, not just the
        parent's current state:

        * RUNNING, or picked and not yet stamped (`_active_names` — the
          pick->stamp window), is obviously not final;
        * PENDING **or CANCELLED** and not yet handled this run is not final
          either: a CANCELLED calc re-runs (P24, and _precheck says so), so
          reading its state as terminal would admit the dependent alongside its
          re-running parent, and _resolve_geometry would FAIL it — a state that
          is LOCKED, forcing the user to rebuild the calculation.

        A dangling or self-reference is 'ready': it has always failed in
        _resolve_geometry with a precise message, and deferring it forever would
        turn a clear error into a hang. A FAILED/BLOCKED parent is ready too —
        _precheck blocks the child, as before.

        The calc this one runs `after` is waited for by the same rule. Once
        both are settled, _pick_ready asks the edge's energy condition
        (_judge_filter) and then its approval gate: a row held at its gate
        (gate_waiting) is not ready — it stays PENDING and unseen, and a run
        left with nothing but such rows ends (_block_unreachable leaves them
        alone).
        """
        for pname in _parent_names(calc):
            parent = self._by_name.get(pname)
            if parent is None or parent is calc:
                continue
            if not self._settled(parent, seen):
                return False
        return True

    def _gate_holds(self, calc: Calculation) -> bool:
        """True when `calc` waits at its approval gate (gate_waiting). Read
        from the parent's in-memory result or its DONE state -- never by
        parsing its output, which this runs under the store lock (P37)."""
        if calc.gate != "approval" or calc.approved or not calc.after:
            return False
        parent = self._by_name.get(calc.after)
        if parent is None or parent is calc:
            return False
        return gate_waiting(calc, parent)

    def _handled_without_running(self, calc: Calculation) -> bool:
        """True when _precheck will dispose of this row without launching
        anything, so it needs no share of the budget. These are pure state
        checks, and therefore certain.

        A keep-existing (`_skip_names`) row is deliberately NOT here: whether it
        is adopted or falls through to a real run is only known after parsing
        its output, so it goes through admission like any other row. Treating it
        as free would let it reach _start_job with no room, where waiting would
        park the single dispatcher and head-of-line block every row behind it.
        """
        return (calc.state in (CalcState.DONE, CalcState.FAILED, CalcState.FILTERED)
                or (calc.state != CalcState.RUNNING
                    and self._filtered_by(calc) is not None)
                or self._blocked_by(calc) is not None)

    def _slot_for(self, calc: Calculation) -> "_JobSlot":
        return _JobSlot(name=calc.name, cores=declared_cores(calc),
                        ram_mb=estimated_ram_mb(calc), gpu=uses_gpu(calc),
                        kind=calc.kind or "")

    def _fits(self, slot: "_JobSlot") -> bool:
        """Does `slot` fit alongside what is already running ON ITS DEVICE
        (slot.device: "" = this machine)? Caller holds the lock. Jobs, cores
        and estimated memory are all hard limits, per device; see
        core/resources.py for how a calculation's cost is read."""
        if slot.device:
            return self._fits_remote(slot)
        local = [s for s in self._slots.values() if not s.device]
        if len(local) >= self.budget.max_jobs:
            return False
        # One GPU job at a time. A CUDA job is charged a single core (its CPU
        # use is marginal), so the core budget would happily admit a dozen of
        # them onto one card — where they do not queue politely, they run out of
        # VRAM. There is no budget number for video memory: ORCAdesk cannot see
        # inside the user's torch, so the honest limit is a lane.
        if slot.gpu and any(s.gpu for s in local):
            return False

        if sum(s.cores for s in local) + slot.cores > self.budget.cores:
            return False
        if sum(s.ram_mb for s in local) + slot.ram_mb > self.budget.ram_mb:
            return False
        # The memory budget is spent against ESTIMATES, and they are wrong in
        # both directions (core/resources.py): ORCA may exceed its %maxcore,
        # CREST is charged far more than it takes. So before adding to what is
        # already running, ask the machine what it actually has left. Skipped
        # when nothing is in flight — the first job must always be able to
        # start, whatever the memory situation, or the queue could stall
        # outright.
        if local:
            headroom = ram_headroom_mb()
            if headroom and slot.ram_mb > headroom:
                return False
        return True

    def _fits_remote(self, slot: "_JobSlot") -> bool:
        """The remote half of _fits: the device must be online, carry MPI for a
        parallel job, and have room in its own budget; free memory is the
        device's last probed MemAvailable, asked only when it already runs
        something (the same rule as ram_headroom_mb here)."""
        dev = self._devices.get(slot.device)
        if dev is None or not dev.online:
            return False
        if slot.kind.startswith("crest"):
            # a CREST search needs CREST there; its -T threads are OpenMP
            # threads in one process, so MPI does not matter
            if not getattr(dev, "crest_path", ""):
                return False
        elif slot.cores > 1 and not dev.has_mpi:
            return False
        mine = [s for s in self._slots.values() if s.device == slot.device]
        b = dev.budget
        if len(mine) >= b.max_jobs:
            return False
        if sum(s.cores for s in mine) + slot.cores > b.cores:
            return False
        if sum(s.ram_mb for s in mine) + slot.ram_mb > b.ram_mb:
            return False
        if mine and dev.mem_avail_mb:
            if slot.ram_mb > max(0, dev.mem_avail_mb - 2048):
                return False
        return True

    def _place(self, calc: Calculation, relax: bool = True) -> "Optional[str]":
        """Where ``calc`` would run now: "" (this machine), a device alias, or
        None when nowhere has room. This machine first, then the devices in
        the user's order (design §5). Without ``relax`` a device whose
        preference leaves out ``calc.kind`` is skipped (_pick_ready's first
        pass). A RUNNING row with a device is a job already there to
        reattach: it goes back to its device whatever that device's state, or
        the job would be orphaned. Caller holds the lock."""
        if calc.state == CalcState.RUNNING:
            if calc.device:
                return calc.device
            return "" if self._fits(self._slot_for(calc)) else None
        slot = self._slot_for(calc)
        if self._pool_takes("") and self._fits(slot):
            return ""
        if self._devices and remote_capable(
                calc, run_dir_of(calc, self._system_name(calc))):
            for alias, dev in self._devices.items():
                # a draining or stopped device takes nothing new
                if not self._pool_takes(alias):
                    continue
                # a preference is soft: skipped in the first pass only
                if not relax and not device_prefers(dev.prefer, calc.kind):
                    continue
                slot.device = alias
                if self._fits(slot):
                    return alias
        return None

    def _release(self, name: str) -> None:
        """Drop a picked-but-not-started row's claim on the in-flight set."""
        with self._slot_cv:
            self._active_names.discard(name)
            self._slot_cv.notify_all()

    def _block_unreachable(self, calcs: list[Calculation], seen: dict) -> None:
        """Nothing is running and every remaining row is waiting on a geometry
        reference that can never complete — i.e. the references form a cycle.
        Stamp them BLOCKED instead of spinning forever.

        Except the rows held at an approval gate, and every row downstream of
        one: those are not unreachable, they are waiting for the user. They are
        left exactly as they are (PENDING, not seen) and the run ends normally
        — approving later and pressing Run picks them up."""
        held = self._held_at_gates(calcs, seen)
        if held:
            n_gate = sum(1 for c in held.values() if self._gate_holds(c))
            self.cb.log(
                f"{n_gate} calculation(s) waiting for approval"
                + (f" ({len(held) - n_gate} more after them)"
                   if len(held) > n_gate else "")
                + "; approve to continue.", "info")
        for j, c in enumerate(calcs):
            if id(c) in seen or id(c) in held:
                continue
            seen[id(c)] = c
            # Only a row that was actually going to run can be blocked. A DONE
            # result is frozen, a FAILED one is locked with its diagnosis (P24),
            # and a RUNNING one has a detached ORCA behind it — stamping any of
            # them here would unfreeze, unlock or orphan them. They cannot be in
            # this loop any more (see _pick_ready), so this is the second lock
            # on the door, not the first.
            if c.state in (CalcState.DONE, CalcState.FAILED, CalcState.RUNNING,
                           CalcState.FILTERED):
                continue
            c.state = CalcState.BLOCKED
            c.message = "Skipped: its geometry reference can never complete."
            self.cb.log(f"[{c.name}] blocked (circular geometry reference).",
                        "warn", c.name)
            self.cb.calc_update(j, c)

    def _held_at_gates(self, calcs: list[Calculation],
                       seen: dict) -> "dict[int, Calculation]":
        """Unhandled rows waiting at an approval gate, plus every unhandled row
        whose dependencies (geometry reference or `after`) reach one of them,
        and every row with an energy condition whose stack has a member that
        depends on one (its barrier waits for that member's parent) —
        id -> row. Caller holds the lock."""
        held: "dict[int, Calculation]" = {
            id(c): c for c in calcs if id(c) not in seen and self._gate_holds(c)}
        if not held:
            return held
        held_names = {c.name for c in held.values()}
        grew = True
        while grew:
            grew = False
            for c in calcs:
                if id(c) in seen or id(c) in held:
                    continue
                if c.state not in (CalcState.PENDING, CalcState.CANCELLED):
                    continue
                if any(p in held_names for p in _parent_names(c)) or (
                        has_filter(c) and any(
                            p in held_names
                            for m in self._filter_group(c, calcs)
                            for p in _parent_names(m))):
                    held[id(c)] = c
                    held_names.add(c.name)
                    grew = True
        return held

    def _start_job(self, calc: Calculation, i: int, calcs: list[Calculation],
                   threads: list):
        """Reserve this calc's resources and run it in its own thread.

        Returns True when it started, False when a stop landed while waiting for
        room (nothing was started, and the walk ends), and "requeue" when the
        row stopped fitting while we waited — the walk then un-sees it and moves
        on to the rows behind it."""
        slot = self._slot_for(calc)
        with self._slot_cv:
            slot.device = self._placed.pop(calc.name, "")
            # a remote job to reattach holds its slot whatever its device says
            # (it is already running there): never waited on, never requeued
            forced = calc.state == CalcState.RUNNING and bool(slot.device)
            # _pick_ready already admitted this row — but its RAM test consults
            # the machine's live free memory, which can drop between the pick
            # and here. Waiting it out parked the dispatcher on this one row
            # (still in _active_names, so not even editable) and head-of-line
            # blocked every smaller row behind it, which contradicts the
            # "a row that does not fit is skipped OVER" rule. Only wait while
            # something is in flight that will free room, and re-offer the row
            # to the scan instead of holding it.
            # its pool was drained or stopped between the pick and here (a
            # device's Stop): hand the row back to the scan, the walk goes on
            if (not forced and calc.state != CalcState.RUNNING
                    and not self._pool_takes(slot.device)):
                self._active_names.discard(calc.name)
                self._slot_cv.notify_all()
                return "requeue"
            waited = 0.0
            while not forced and self._slots and not self._fits(slot):
                if self._stop_pending():
                    break
                if not self._pool_takes(slot.device):
                    self._active_names.discard(calc.name)
                    self._slot_cv.notify_all()
                    return "requeue"
                if waited >= _REFIT_GIVE_UP:
                    self._active_names.discard(calc.name)
                    self._slot_cv.notify_all()
                    return "requeue"
                self._slot_cv.wait(_SLOT_POLL)
                waited += _SLOT_POLL
            if self._stop_pending():
                self._active_names.discard(calc.name)
                self._slot_cv.notify_all()
                return False
            if (not forced and not slot.device and not self._fits(slot)
                    and self.budget.max_jobs > 1):
                self.cb.log(
                    f"[{calc.name}] needs {slot.cores} core(s) / "
                    f"{slot.ram_mb} MB — more than the whole budget "
                    f"({self.budget.cores} core(s) / {self.budget.ram_mb} MB); "
                    "running it on its own.", "warn", calc.name)
            self._slots[calc.name] = slot

        def _work() -> None:
            self._job_local.slot = slot
            try:
                if not self._dispatch_and_handle(calc, i, calcs):
                    self._shutdown_seen.set()   # app is closing: stop the walk
            finally:
                with self._slot_cv:
                    self._slots.pop(calc.name, None)
                    self._active_names.discard(calc.name)
                    self._slot_cv.notify_all()

        t = threading.Thread(target=_work, name=f"orcadesk-job-{calc.name}",
                             daemon=True)
        threads.append(t)
        t.start()
        return True

    def update_device_entry(self, entry: dict) -> None:
        """Take a device's changed settings entry while the run goes on (the
        Compute devices card saved new caps, prefer kinds, spot...). Like the
        local budget (set_budget), a device's caps are live: before this a
        laptop set to 12 cores kept its run-start 16 until the next Run.
        Jobs already there keep what they reserved. A device switched off
        stops taking jobs (its pool is stopped) but its in-flight jobs are
        still watched and fetched; one switched on mid-run joins at once and
        the watch thread probes it."""
        alias = entry.get("alias")
        if not alias:
            return
        fresh = (resolve_device(entry, self._device_info(alias))
                 if entry.get("enabled") else None)
        with self._slot_cv:
            self._device_entries = [e for e in self._device_entries
                                    if e.get("alias") != alias]
            if fresh is None:
                if alias in self._pool_state:
                    self._pool_state[alias] = POOL_STOPPED
            else:
                self._device_entries.append(dict(entry))
                old = self._devices.get(alias)
                if old is not None:
                    # what only a probe knows, never lost to a settings save
                    if not fresh.workdir:
                        fresh.workdir = old.workdir
                    if not fresh.online and old.online:
                        fresh.online = old.online
                        fresh.has_mpi = old.has_mpi
                        fresh.mem_avail_mb = old.mem_avail_mb
                    if not fresh.orca_path:
                        fresh.orca_path = old.orca_path
                    if not fresh.crest_path:
                        fresh.crest_path = old.crest_path
                        fresh.crest_extended = old.crest_extended
                self._devices[alias] = fresh
                self._pool_state.setdefault(alias, POOL_RUNNING)
            self._slot_cv.notify_all()

    def set_budget(self, budget: "ResourceBudget") -> None:
        """Take a new admission budget while the run goes on (the Settings
        sheet saved new limits). The queue is live (P29), and its limits are
        too: a run started at one job at a time used to keep running one at a
        time after the user raised the limit, until it was stopped and started
        again. Jobs already running keep what they reserved; a lower budget
        only means nothing new starts until they fit under it. The dispatcher
        re-reads the budget on its next scan (it polls _slot_cv)."""
        resolved = budget.resolved()
        with self._slot_cv:
            self.budget = resolved
            self._slot_cv.notify_all()

    def resource_usage(self) -> dict:
        """What the in-flight jobs have reserved, against the budget. Read by
        the store for the queue snapshot (duck-typed, so the store never imports
        the engine)."""
        with self._queue_lock:
            states = self.pool_states()
            local = [s for s in self._slots.values() if not s.device]
            devices = []
            for alias, dev in self._devices.items():
                mine = [s for s in self._slots.values() if s.device == alias]
                lost = any(getattr(self._by_name.get(s.name), "disconnected", False)
                           for s in mine)
                devices.append({
                    "id": alias,
                    "cores_used": sum(s.cores for s in mine),
                    "cores_budget": dev.budget.cores,
                    "ram_used_mb": sum(s.ram_mb for s in mine),
                    "ram_budget_mb": dev.budget.ram_mb,
                    "jobs": len(mine),
                    "max_jobs": dev.budget.max_jobs,
                    "online": bool(dev.online) and not lost,
                    "state": states.get(alias, POOL_RUNNING),
                })
            return {
                # this machine (the figures the rail always showed)
                "cores_used": sum(s.cores for s in local),
                "cores_budget": self.budget.cores,
                "ram_used_mb": sum(s.ram_mb for s in local),
                "ram_budget_mb": self.budget.ram_mb,
                "jobs": len(local),
                "max_jobs": self.budget.max_jobs,
                # this machine's pool: running / draining / stopped
                "state": states.get("", POOL_RUNNING),
                # each enabled remote device, in the user's order
                "devices": devices,
            }

    def _stop_pending(self) -> bool:
        """A stop of any kind has been signalled — start nothing more. The
        graceful drain counts: it means "let what is running finish", and a row
        picked but still waiting for room has NOT started."""
        # (a drain is no longer one of these: it drains the pools, and a row
        # picked for a pool that stopped meanwhile is handed back -- requeue --
        # so the walk survives to be resumed by a Run)
        return (self._cancel_event.is_set() or self._detach_event.is_set()
                or self._shutdown_seen.is_set())

    def _should_stop(self, calcs: list[Calculation]) -> bool:
        """Top-of-iteration stop guards. True → break out of the walk."""
        if self._detach_event.is_set() or self._shutdown_seen.is_set():
            # App is shutting down: stop processing and leave every calc as
            # it is (a RUNNING one keeps going in the background for reattach).
            return True

        if self._cancel_event.is_set():
            # Hard cancel stops the queue but only stamps the job that was
            # actually in flight: that one was killed and marked CANCELLED by
            # the OrcaCancelled handler in _dispatch_and_handle. Every remaining
            # calc is left UNTOUCHED — PENDING rows stay PENDING (re-runnable
            # as-is, the plan is not discarded), and a reattach-pending RUNNING
            # row keeps its pid for the next launch. (Deliberate: a cancel used
            # to walk on and stamp all remaining PENDING rows CANCELLED; now it
            # does not — stopping the run must not throw away the queued plan.)
            remaining = sum(1 for c in calcs if c.state == CalcState.PENDING)
            if remaining:
                self.cb.log(
                    f"Cancelled; {remaining} calculation(s) left pending.", "info")
            return True

        # (The graceful drain is not a stop here: it drains the pools, and the
        # walk ends where nothing is in flight and no pool takes jobs --
        # _run_walk -- so a Run while it drains resumes the same run.)
        return False

    def _end_of_drain(self, calcs: list[Calculation]) -> None:
        """Every pool is draining or stopped and nothing is in flight: the
        rows not started stay PENDING (runnable on the next Run)."""
        # count by state, not position: every already-handled row was
        # stamped out of PENDING, so the PENDING rows are exactly what this
        # drain leaves behind
        remaining = sum(1 for c in calcs if c.state == CalcState.PENDING)
        self.cb.log(
            f"Stopping after the running job(s); "
            f"{remaining} calculation(s) left pending.",
            "info",
        )

    def _precheck(self, calc: Calculation, i: int) -> bool:
        """State screening before a calc may run. False → the row was handled
        here (the walk skips it); True → proceed to run."""
        # Already finished successfully on a previous run: don't recompute
        # (that would waste time and overwrite good results). Checked BEFORE
        # the dependency check so a frozen DONE result is never re-stamped BLOCKED
        # by a dependency that failed later. CANCELLED calculations DO
        # re-run, so the user can retry them.
        if calc.state == CalcState.DONE:
            self.cb.log(f"[{calc.name}] already done — skipping.", "info", calc.name)
            self.cb.calc_update(i, calc)
            return False

        # FAILED is locked (P24): never re-run a failed calc implicitly; a
        # retry is a new set put on it in the queue editor (store rerun_calc,
        # which makes it PENDING again). Checked before the dependency check so
        # the failure diagnosis is never re-stamped BLOCKED.
        if calc.state == CalcState.FAILED:
            self.cb.log(f"[{calc.name}] failed previously — locked; put a "
                        "new set on it in the queue editor to retry.", "warn",
                        calc.name)
            return False

        # FILTERED is terminal (it never ran and has nothing to run): left as
        # it is, quietly -- it was logged when it was filtered.
        if calc.state == CalcState.FILTERED:
            self._verdicts.pop(id(calc), None)
            return False

        # A reference to a calculation that is not in the queue at all. Both
        # run entry points screen for this up front (find_dangling_reference),
        # but the startup resume cannot refuse — it has a running job to
        # reattach — and a mid-run add is never screened. Reaching
        # _resolve_geometry it became a FAILED calc, which is LOCKED (P24), so
        # a queue the user could have fixed by re-pointing one row needed that
        # row rebuilt instead. BLOCKED says the same thing and stays editable.
        if (calc.geometry_source == GeometrySource.REFERENCE and calc.ref_name
                and calc.ref_name != calc.name
                and self._by_name.get(calc.ref_name) is None):
            calc.state = CalcState.BLOCKED
            calc.message = (f"Skipped: referenced calculation "
                            f"'{calc.ref_name}' is not in the queue.")
            self.cb.log(f"[{calc.name}] blocked (referenced calculation "
                        f"'{calc.ref_name}' is not in the queue).", "warn", calc.name)
            self.cb.calc_update(i, calc)
            return False

        # The same two refusals for the calc this one runs `after`: gone from
        # the queue, or itself. BLOCKED (editable), never FAILED (locked).
        if calc.after and (calc.after == calc.name
                           or self._by_name.get(calc.after) is None):
            calc.state = CalcState.BLOCKED
            if calc.after == calc.name:
                calc.message = "Skipped: a calculation cannot run after itself."
                why = "it runs after itself"
            else:
                calc.message = (f"Skipped: '{calc.after}', the calculation this "
                                "one runs after, is not in the queue.")
                why = f"'{calc.after}', which it runs after, is not in the queue"
            self.cb.log(f"[{calc.name}] blocked ({why}).", "warn", calc.name)
            self.cb.calc_update(i, calc)
            return False

        # A calc it depends on was filtered: so is this one -- before the
        # failure check, since a screened-out branch is not fixed by an edit.
        filtered = (self._filtered_by(calc)
                    if calc.state != CalcState.RUNNING else None)
        if filtered is not None:
            self._verdicts.pop(id(calc), None)
            self._stamp_filtered(calc, i, f"Filtered: parent {filtered.name} "
                                          "was filtered")
            return False

        # Whether a dependency failed is decided from the queue as it is NOW
        # (see _blocked_by), so a row edited or re-added mid-run is judged on
        # what it references now, not on a name recorded at run start.
        if self._blocked_by(calc) is not None:
            self._verdicts.pop(id(calc), None)
            calc.state = CalcState.BLOCKED
            calc.message = "Skipped: a dependency failed."
            self.cb.log(f"[{calc.name}] blocked (a dependency failed).",
                        "warn", calc.name)
            self.cb.calc_update(i, calc)
            return False

        # The energy condition's verdict, reached when the scan picked it
        # (_judge_filter, under the lock).
        entry = self._verdicts.pop(id(calc), None)
        if entry is not None and entry[0] is calc and calc.state != CalcState.RUNNING:
            state, message = entry[1]
            if state == CalcState.FILTERED:
                self._stamp_filtered(calc, i, message)
            else:
                calc.state = state
                calc.message = message
                self.cb.log(f"[{calc.name}] blocked: {message}", "warn", calc.name)
                self.cb.calc_update(i, calc)
            return False
        # A conformer placeholder never runs: it is replaced by one copy per
        # conformer (_expand_conformers). _pick_pass never hands one over
        # without a verdict; this is the second lock on the door.
        if is_conf_placeholder(calc) and calc.state != CalcState.RUNNING:
            calc.state = CalcState.BLOCKED
            calc.message = (f"Skipped: it stands for every conformer of "
                            f"'{calc.ref_name}' and is never run itself.")
            self.cb.log(f"[{calc.name}] blocked: {calc.message}", "warn", calc.name)
            self.cb.calc_update(i, calc)
            return False
        return True

    def _stamp_filtered(self, calc: Calculation, i: int, message: str) -> None:
        """Stamp ``calc`` FILTERED with its reason, and say so in the log."""
        calc.state = CalcState.FILTERED
        calc.message = message
        calc.pid = None
        calc.create_time = None
        self.cb.log(f"[{calc.name}] {message[0].lower() + message[1:]}.",
                    "info", calc.name)
        self.cb.calc_update(i, calc)

    def _try_keep_existing(self, calc: Calculation, i: int) -> bool:
        """The keep-existing ("skip") run option: the user chose to keep the
        existing result on disk for this calc. True → it was adopted and the
        row is done; False → the calc must run.

        Load that output (so references and the Results tab still work)
        instead of recomputing and overwriting it. Parse through
        result_from_output (kind dispatch: an MLIP calc's result is the
        worker's JSON, not an ORCA .out) and gate DONE behind
        validate_result, so a crashed or chemically-bad output can't be
        resurrected as DONE (the 0.4.2 incident: a FAILED raw calc came
        back DONE through this path). If the existing result doesn't
        hold up, "keep" is impossible; the honest fallback is to run
        the calc, never to mark it FAILED (the user asked to preserve
        a result, not to lock the calc over a stale one)."""
        # Identity, not name: _skip_names comes from the overwrite modal, which
        # was shown for the rows in the queue at run start. The queue is live
        # (P29), so a row removed and re-added — or edited in place — under the
        # same name is a DIFFERENT calculation, and it must not inherit a "keep
        # the existing result" answer given about the old one (it would be
        # stamped DONE from an output it never produced).
        if id(calc) not in self._skip_ids:
            return False
        # The folder the overwrite question was about (bridge._overwrite_
        # conflicts asks the same rule): its recorded run folder, else the one
        # it would run in now. An output elsewhere -- a {workspace}/{name}/
        # from before system folders, for a calc in a system -- is not this
        # calc's to keep: running it cannot overwrite that one either.
        rel = run_dir_of(calc, self._system_name(calc))
        calc_dir = self.workspace_root.joinpath(*rel.split("/"))
        if calc.kind.startswith("mlip"):
            out_path = calc_dir / f"{calc.name}.mlip.json"
        else:
            out_path = calc_dir / f"{calc.name}.out"
        problem = ""
        if out_path.exists():
            # result_from_output reads calc.output_path; restore it on
            # failure so a bad keep-attempt leaves no stale pointer.
            prev_output_path = calc.output_path
            calc.output_path = str(out_path)
            try:
                result = result_from_output(calc)
                validate_result(calc, result)
                # the folder may hold a REMOVED same-named calc's output
                # (folders are never deleted; names are only unique in
                # the current queue) — never adopt a result that isn't
                # structurally this calculation's.
                problem = _kept_result_matches(calc, result)
                if problem:
                    calc.output_path = prev_output_path
                else:
                    calc.result = result
                    calc.state = CalcState.DONE
                    calc.message = "Kept existing result (not recomputed)."
                    # adopted: that folder is where its result lives
                    calc.run_dir = rel
                    self._by_name[calc.name] = calc
                    self.cb.log(f"[{calc.name}] kept existing result on disk "
                                "— not recomputed.", "info", calc.name)
            except OrcaRunError as e:
                problem = f"existing result invalid ({e})"
                calc.output_path = prev_output_path
            except Exception as e:
                problem = f"could not read existing result ({e})"
                calc.output_path = prev_output_path
        else:
            problem = "no existing result found"
        if problem:
            self.cb.log(f"[{calc.name}] {problem}; running.", "warn", calc.name)
            self._skip_names.discard(calc.name)
            return False
        self.cb.calc_update(i, calc)
        return True

    def _dispatch_and_handle(self, calc: Calculation, i: int,
                             calcs: list[Calculation]) -> bool:
        """Run one calc through its backend and translate the outcome into a
        queue state. False → break out of the walk (app shutdown); True →
        walk on to the next row. (Failure propagation is dependency-scoped
        failure propagation)."""
        # No launch has happened for this attempt yet — until _run_calc
        # reports one, _failure_reason must not trust an on-disk .out (A22).
        self._orca_launched = False
        # whether its run folder was recorded before this attempt (a BLOCKED
        # outcome hands back a folder this attempt claimed: nothing ran)
        had_run_dir = bool(calc.run_dir)
        try:
            # MLIP calcs run OUTSIDE the ORCA pipeline (see orcamgr/mlip/):
            # a MACE relaxation in the user's env, not an ORCA .inp. The
            # except-handlers below treat cancel/shutdown/failure identically.
            if calc.kind.startswith("mlip"):
                self._run_mlip_calc(calc, i)
            elif calc.kind.startswith("crest"):
                # CREST runs its Linux binary through WSL (see orcamgr/crest/),
                # OUTSIDE the ORCA pipeline. Like ORCA (and unlike MLIP) it is
                # truly detachable and reattachable across an app restart.
                self._run_crest_calc(calc, i)
            else:
                self._run_calc(calc, i)
        except RemoteUnavailable as e:
            # The device could not take it (upload or start failed on the
            # transport): not the calculation's failure. The device is offline
            # until the watch finds it ready, and the row goes back to PENDING
            # and is offered again -- here or on another device (design §5).
            self._device_offline(calc.device or self._job.device)
            calc.state = CalcState.PENDING
            calc.message = ""
            calc.pid = None
            calc.create_time = None
            calc.device = ""
            calc.remote_dir = ""
            calc.disconnected = False
            if not had_run_dir:
                calc.run_dir = ""
            with self._slot_cv:
                self._unsee.add(id(calc))
                self._slot_cv.notify_all()
            _remote_log.warning("%s not started remotely: %s", calc.name, e)
            self.cb.calc_update(i, calc)
            return True
        except RemoteRequeue as e:
            # A spot device lost the job (gone longer than its limit, or the VM
            # was preempted under it): not the calculation's failure, and FAILED
            # would lock it (P24). PENDING again, offered anew -- here or on
            # another device; the old remote folder is deleted without being
            # brought back (the clean-up entry says fetch False). It keeps its
            # recorded run folder: the re-run starts there, like any re-run.
            if e.unreachable:
                self._device_offline(calc.device or self._job.device)
            if e.entry:
                self.cb.remote_cleanup(e.entry)
            calc.state = CalcState.PENDING
            calc.message = REQUEUED_MESSAGE
            calc.pid = None
            calc.create_time = None
            calc.device = ""
            calc.remote_dir = ""
            calc.disconnected = False
            calc.faulted = False
            with self._slot_cv:
                self._unsee.add(id(calc))
                self._slot_cv.notify_all()
            _remote_log.warning("%s re-queued from a spot device: %s", calc.name, e)
            self.cb.log(f"[{calc.name}] re-queued: the spot device stopped answering.",
                        "warn", calc.name)
            self.cb.calc_update(i, calc)
            return True
        except OrcaDetached:
            # app shutdown mid-calc: leave THIS calc RUNNING (its ORCA keeps
            # going headless; pid is persisted) and stop processing so it can
            # be reattached on the next launch. Do not touch its state.
            # An MLIP job has no detach/reattach machinery and was
            # TERMINATED by detach (mlip/runner.py) — leaving it RUNNING
            # would make the next launch's reconcile lock it FAILED
            # ("interrupted"); a deliberate shutdown stop is a cancel
            # (re-runnable), not a failure.
            if calc.kind.startswith("mlip"):
                calc.state = CalcState.CANCELLED
                calc.message = "Stopped on shutdown."
                calc.pid = None
                calc.create_time = None
                self.cb.calc_update(i, calc)
                self.cb.log(f"[{calc.name}] stopped on shutdown.", "info", calc.name)
            else:
                self.cb.log(f"[{calc.name}] left running in the background.",
                            "info", calc.name)
            return False
        except OrcaCancelled as e_cancel:
            # user stopped the run mid-calc: mark THIS calc CANCELLED (not
            # FAILED) and do NOT block its dependents. The remaining PENDING
            # calcs are left as-is — the top-of-loop cancel guard breaks out
            # of the walk on the next pass, so the queued plan is preserved.
            calc.state = CalcState.CANCELLED
            calc.message = "Cancelled by user."
            calc.pid = None
            calc.create_time = None
            calc.disconnected = False
            if isinstance(e_cancel, RemoteCancelUnreachable):
                # the device could not be reached: cancelled here all the
                # same, and its remote job is settled when it is back
                # (design §6.3) -- said, because it may still be running
                calc.message = CLEANUP_PENDING_MESSAGE
                self.cb.remote_cleanup(e_cancel.entry)
            self.cb.log(f"[{calc.name}] cancelled.", "info", calc.name)
            self.cb.calc_update(i, calc)
        except OrcaFiltered as e:
            # nothing launched, by design (a conformer past the ensemble's
            # end): FILTERED, never FAILED. It has no folder of its own.
            if not had_run_dir:
                calc.run_dir = ""
            self._stamp_filtered(calc, i, f"Filtered: {e}")
        except OrcaBlocked as e:
            # nothing launched, and the fix is an edit (or a parent's run):
            # BLOCKED stays editable and is re-tried on the next Run
            calc.state = CalcState.BLOCKED
            calc.message = f"Skipped: {e}"
            calc.pid = None
            calc.create_time = None
            if not had_run_dir:
                # it never ran, so it has no folder yet: one moved or renamed
                # before it is retried follows its system like any other
                calc.run_dir = ""
            self.cb.log(f"[{calc.name}] blocked: {e}", "warn", calc.name)
            self.cb.calc_update(i, calc)
        except OrcaRunError as e:
            calc.state = CalcState.FAILED
            calc.message = self._failure_reason(calc, str(e))
            if isinstance(e, OrcaHung) and calc.message != str(e):
                # The .out names what died; the runner's note says why the
                # process is gone — ORCAdesk ended it, ORCA never did.
                calc.message = f"{calc.message} {e}"
            calc.pid = None
            calc.create_time = None
            self.cb.log(f"[{calc.name}] FAILED: {calc.message}", "err", calc.name)
            self.cb.calc_update(i, calc)
        except Exception as e:  # defensive
            calc.state = CalcState.FAILED
            calc.message = f"Unexpected error: {e}"
            calc.pid = None
            calc.create_time = None
            self.cb.log(f"[{calc.name}] FAILED: {e}", "err", calc.name)
            self.cb.calc_update(i, calc)
        return True
