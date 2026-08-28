"""
Calculation queue with explicit geometry dependencies.

The queue unit is a single *Calculation* (one ORCA run). A calculation's
geometry comes from one of two sources:

* DIRECT    -- coordinates provided up front (typically from an .xyz file)
* REFERENCE -- the optimized geometry of another calculation in the queue,
               named explicitly by the user

Calculations run in queue order. When a calculation references another, the
engine injects the referenced calculation's final geometry at run time.

Failure propagation: if a calculation fails, any calculation that depends on it
(directly or transitively) is marked BLOCKED and skipped. Unrelated calculations
continue. This is dependency-scoped, not whole-queue.

A FAILED calculation is locked (P24): it never re-enters the run loop — retrying
means building a new calculation. Only CANCELLED (a deliberate user stop, not a
failure) re-runs.

Folder layout: each calculation gets its own folder ``{workspace}/{name}/``.
Names are unique within a queue (enforced by the store — ``QueueStore`` rejects
duplicates), so folders never collide.

The engine is GUI-agnostic: it communicates only through callbacks.
"""

from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from .input_generator import (
    StepConfig, build_input, render_raw_input, GEOMETRY_PLACEHOLDER, check_neb_atom_order,
    _xyz_elements,
)
from .resources import ResourceBudget, declared_cores, estimated_ram_mb
from .runner import OrcaRunner, OrcaRunError, OrcaCancelled, OrcaDetached
from .parser import parse_file, ParseResult
from .procutil import process_matches
from .xyzutil import as_xyz_file


# How long the dispatcher sleeps between re-scans while it waits for a job to
# finish. A finishing job also notifies _slot_cv, so this is only a backstop.
_SLOT_POLL = 0.25


class CalcState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"       # a dependency failed, so this never ran
    CANCELLED = "cancelled"


class GeometrySource(str, Enum):
    DIRECT = "direct"
    REFERENCE = "reference"


@dataclass
class Calculation:
    name: str                              # unique; used as folder name
    # opt | ts_opt | freq | ts_freq | opt_freq | ts_opt_freq | irc | tddft |
    # sp | general | nmr | neb_ts | mlip_opt | crest_conf
    # (mlip_* runs outside ORCA in the user's MACE env; crest_* runs outside ORCA
    #  in WSL — see orcamgr/mlip/ and orcamgr/crest/)
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
    runner: object = None
    orca_launched: bool = False


def _xyz_from_geometry(result: ParseResult) -> str:
    return "\n".join(
        f"{a.symbol} {a.x:.10f} {a.y:.10f} {a.z:.10f}" for a in result.geometry
    )


def _raw_coordinate_block(inp_text: str) -> str:
    """Coordinate lines of the first ``* xyz charge mult ... *`` block in an
    .inp text ('' when there is none, e.g. an ``* xyzfile`` reference). For a
    raw calculation this is the reactant that actually runs — ``calc.xyz`` may
    be absent (hand-pasted input) or stale against the edited text."""
    lines = inp_text.splitlines()
    for i, ln in enumerate(lines):
        s = ln.strip().lower()
        if s.startswith("*") and s[1:].strip().startswith("xyz") and "xyzfile" not in s:
            block = []
            for follow in lines[i + 1:]:
                if follow.strip().startswith("*"):
                    break
                block.append(follow)
            return "\n".join(block)
    return ""


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
    expected = ""
    if calc.geometry_source == GeometrySource.DIRECT:
        if calc.is_raw and GEOMETRY_PLACEHOLDER not in calc.raw_text:
            # a no-placeholder raw calc runs its embedded coordinate block
            # verbatim — calc.xyz may be stale against an edited text
            expected = _raw_coordinate_block(calc.raw_text) or (calc.xyz or "")
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
    reporting an unexpected count always has something to print."""
    imag = [f for f in result.frequencies if f < 0]
    return ", ".join(f"{v:.2f}" for v in imag[:5]) if imag else "none"


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


class QueueEngine:
    """Runs a list of Calculations in order, resolving geometry references."""

    def __init__(self, orca_path: str, workspace_root: str,
                 callbacks: Optional[QueueCallbacks] = None,
                 skip_names: Optional[set] = None,
                 mlip_envs: Optional[list] = None,
                 crest_distro: str = "",
                 queue_lock=None,
                 budget: "Optional[ResourceBudget]" = None):
        self.orca_path = orca_path
        # One OrcaRunner per JOB, not per engine: with several calculations in
        # flight each needs its own process handle and its own cancel/detach
        # events. A factory (rather than a fixed instance) is also the seam a
        # test uses to intercept a launch.
        self._orca_runner_factory = lambda: OrcaRunner(orca_path)
        # Registered MLIP environments [{id, name, python}], used to resolve which
        # interpreter runs a mlip_* calc. Empty if MLIP isn't configured.
        self.mlip_envs = list(mlip_envs or [])
        # Preferred WSL distro for CREST calcs ("" = auto-detect the first distro
        # that has CREST). A crest_conf calc runs its Linux binary through WSL
        # (see orcamgr/crest/), OUTSIDE the ORCA pipeline.
        self.crest_distro = (crest_distro or "").strip()
        self.workspace_root = Path(workspace_root)
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
        # In-flight jobs by calc name -> _JobSlot (its reserved cores/RAM and its
        # backend runner). Guarded by _queue_lock; _slot_cv wakes the dispatcher
        # when a job finishes and its budget comes back.
        self._slots: "dict[str, _JobSlot]" = {}
        self._slot_cv = threading.Condition(self._queue_lock)
        # Per-job state for the thread running it (see _job).
        self._job_local = threading.local()

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
        remaining calculations stay PENDING. Does NOT kill anything."""
        self._stop_after_current.set()
        with self._slot_cv:
            self._slot_cv.notify_all()

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

    # -- geometry resolution --
    def _resolve_geometry(self, calc: Calculation) -> str:
        if calc.geometry_source == GeometrySource.DIRECT:
            # In raw+direct mode the coordinates live inside raw_text already,
            # so an empty xyz here is fine (placeholder won't be used).
            if not calc.xyz.strip() and not calc.is_raw:
                raise OrcaRunError("No coordinates provided (direct source is empty).")
            return calc.xyz
        # REFERENCE
        if calc.ref_name == calc.name:
            raise OrcaRunError("A calculation can't reference its own geometry.")
        ref = self._by_name.get(calc.ref_name)
        if ref is None:
            raise OrcaRunError(f"Referenced calculation '{calc.ref_name}' not found in queue.")
        # Parse-on-miss: a DONE ref restored from a previous session has no
        # in-memory ParseResult (we don't eagerly re-parse on load), so read its
        # output now. Dispatch on kind: an MLIP ref's output is the worker's JSON
        # result, NOT an ORCA .out — parsing it with the ORCA parser would yield
        # no geometry and break the handoff after a restart.
        if ref.state == CalcState.DONE and not ref.result and ref.output_path:
            try:
                ref.result = result_from_output(ref)
            except Exception:
                pass  # leave result None; the checks below give a clear error
        if ref.state == CalcState.DONE and ref.result and ref.result.geometry:
            return _xyz_from_geometry(ref.result)
        if ref.state in (CalcState.FAILED, CalcState.BLOCKED, CalcState.CANCELLED):
            raise OrcaRunError(f"Referenced calculation '{calc.ref_name}' did not succeed.")
        if ref.state == CalcState.DONE and (not ref.result or not ref.result.geometry):
            raise OrcaRunError(
                f"Referenced calculation '{calc.ref_name}' produced no geometry "
                "(was it an optimization?)."
            )
        raise OrcaRunError(f"Referenced calculation '{calc.ref_name}' has no usable geometry yet.")

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
        self.cb.log(f"[{calc.name}] finished while unmonitored — result adopted.", "ok")
        self.cb.calc_update(index, calc)

    # -- single calculation --
    def _run_calc(self, calc: Calculation, index: int) -> None:
        out_path = self.workspace_root / calc.name / f"{calc.name}.out"
        # A RUNNING calc restored from a previous session may have been
        # launched under a DIFFERENT workspace root: its persisted output_path
        # is where the detached ORCA is actually writing. Deriving from the
        # current setting would tail a nonexistent file and then FAILED-lock a
        # successfully finished job at process exit (and clobber the one
        # pointer the finished-while-closed → DONE judgment depends on).
        if calc.state == CalcState.RUNNING and calc.output_path:
            out_path = Path(calc.output_path)

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
                        f"(pid {calc.pid})...", "info")
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
        self.cb.calc_update(index, calc)

        xyz = self._resolve_geometry(calc)

        calc_dir = self.workspace_root / calc.name
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
            text = render_raw_input(calc.raw_text, xyz)
        else:
            try:
                text = build_input(calc.config, xyz, calc.charge, calc.multiplicity)
            except ValueError as e:
                # the generator refuses inputs it cannot honestly express
                # (e.g. IRC InitHess "read" without a filename) — surface that
                # as a normal run failure, not an "Unexpected error".
                raise OrcaRunError(str(e))
        inp_path.write_text(text, encoding="utf-8")

        self._prepare_neb_side_files(calc, calc_dir, xyz, text)
        self._stage_irc_hessian(calc, calc_dir)

        self.cb.log(f"[{calc.name}] ({calc.kind}) running ORCA...", "info", calc.name)
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
            uses_generated = (not calc.is_raw) or ('"product.xyz"' in calc.raw_text)
            if not calc.is_raw and not prod:
                raise OrcaRunError("NEB-TS needs a product geometry, but none was provided.")
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
                reactant = xyz if not calc.is_raw else _raw_coordinate_block(text)
                if _xyz_elements(reactant):
                    chk = check_neb_atom_order(reactant, prod)
                    if not chk.get("ok"):
                        raise OrcaRunError(chk.get("error") or "NEB-TS reactant/product atom mismatch.")
                (calc_dir / "product.xyz").write_text(as_xyz_file(prod), encoding="utf-8")
                guess = (calc.config.neb_ts_guess_xyz or "").strip()
                if guess:
                    (calc_dir / "ts_guess.xyz").write_text(as_xyz_file(guess), encoding="utf-8")

    def _stage_irc_hessian(self, calc: Calculation, calc_dir: Path) -> None:
        """IRC pre-launch guard for a read-in Hessian: the generated input
        references the file relative to the run folder, but nothing guarantees
        it is there (the folder may not even exist before the first run). Stage
        it from the referenced calc's folder when possible; otherwise fail fast
        with a clear message instead of a cryptic ORCA abort. No-op for every
        other kind (and for raw IRC inputs)."""
        if (calc.kind == "irc" and not calc.is_raw
                and (calc.config.irc_init_hess or "").strip() == "read"):
            hess_name = (calc.config.irc_hess_file or "").strip()
            hess_path = Path(hess_name)
            if not hess_path.is_absolute():
                hess_path = calc_dir / hess_name
            if hess_name and not hess_path.exists():
                looked = [str(hess_path)]
                if calc.geometry_source == GeometrySource.REFERENCE and calc.ref_name:
                    src = self.workspace_root / calc.ref_name / hess_name
                    looked.append(str(src))
                    if src.exists():
                        shutil.copyfile(src, calc_dir / hess_name)
                        self.cb.log(f"[{calc.name}] copied {hess_name} from "
                                    f"{calc.ref_name}'s folder.", "info")
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
        self._job.runner.monitor(
            out_path, on_line=lambda ln: self.cb.log(ln, "orca", calc.name),
            start_pos=start_pos)
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
        self.cb.calc_update(index, calc)

        xyz = self._resolve_geometry(calc)
        if not xyz.strip():
            raise OrcaRunError("No coordinates to optimize (geometry is empty).")
        python = self._resolve_mlip_python(calc)

        calc_dir = self.workspace_root / calc.name
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
            # what admission control charged this job (core/resources.py) --
            # the worker must not quietly take every core on the machine
            threads=declared_cores(calc))

        model = calc.config.mlip_model or "MACE"
        verb = {"sp": "single-point energy", "freq": "frequencies",
                "opt_freq": "optimizing + frequencies"}.get(task, "optimizing")
        self.cb.log(f"[{calc.name}] ({calc.kind}) {verb} with {model} via {python}...", "info")
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
        """Run a crest_* calc: a CREST conformer search in WSL. Like ORCA it is
        launched DETACHED (survives app close) and reattached across a restart;
        the resolved WSL distro is stashed on config.crest_env_id so a reattach
        knows where the job runs. Mirrors _run_calc's fresh-launch/reattach shape
        but uses CrestRunner + parse_crest_result."""
        from ..crest.runner import CrestRunner, write_crest_run_files, build_crest_argv
        from ..crest.env import resolve_run_target

        calc_dir = self.workspace_root / calc.name
        out_path = calc_dir / f"{calc.name}.out"

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
                        "reading results...", "info")
            self._crest_monitor_and_finish(calc, index, out_path, calc.name,
                                           already_done=True)
            return

        # Fresh launch.
        calc.state = CalcState.RUNNING
        calc.message = ""
        self.cb.calc_update(index, calc)

        xyz = self._resolve_geometry(calc)
        if not xyz.strip():
            raise OrcaRunError("No coordinates for CREST (geometry is empty).")

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

        calc_dir.mkdir(parents=True, exist_ok=True)
        argv = build_crest_argv(calc.config, calc.charge, calc.multiplicity)
        script_path = write_crest_run_files(calc_dir, calc.name, xyz, crest_bin, argv)

        method = getattr(calc.config, "crest_method", "") or "gfn2"
        self.cb.log(f"[{calc.name}] (crest_conf) running CREST [{method}] "
                    f"in WSL:{distro} ...", "info")
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
            self._job.runner.monitor(
                out_path, name,
                on_line=lambda ln: self.cb.log(ln, "orca", calc.name),
                start_pos=start_pos)

        result = parse_crest_result(str(out_path))
        n = len(result.conformers)
        self._finish_ok(calc, index, result, out_path,
                        message=f"Completed — {n} conformer(s).",
                        log_msg=f"done — {n} conformer(s).")
        self._export_crest_conformers(calc, Path(out_path).parent)

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
            self.cb.log(f"[{calc.name}] conformer .xyz export skipped: {e}", "warn")
            return
        self.cb.log(f"[{calc.name}] exported {len(written)} conformer(s) "
                    f"to conformers/.", "info")

    # -- dependency-scoped failure propagation --
    def _dependents_of(self, calcs: list[Calculation], failed_name: str) -> set[str]:
        """All calc names that depend on failed_name, directly or transitively."""
        blocked: set[str] = set()
        bad = {failed_name}
        changed = True
        while changed:
            changed = False
            for c in calcs:
                if c.name in blocked:
                    continue
                if (c.geometry_source == GeometrySource.REFERENCE
                        and c.ref_name in bad):
                    blocked.add(c.name)
                    bad.add(c.name)
                    changed = True
        return blocked

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
            out_path = self.workspace_root / calc.name / f"{calc.name}.out"
            if out_path.exists():
                r = parse_file(str(out_path))
                if r.error_message:
                    return r.error_message
        except Exception as e:
            # Diagnosing the failure must not itself fail silently — fall back to
            # the raw runner message, but leave a breadcrumb so a broken parse
            # path is discoverable instead of swallowed.
            self.cb.log(f"[{calc.name}] could not parse .out for a detailed reason ({e}).", "warn")
        return runner_msg

    # -- the whole queue --
    def run_all(self, calcs: list[Calculation]) -> None:
        with self._queue_lock:
            self._by_name = {c.name: c for c in calcs}
            blocked_names: set[str] = set()
            # Calcs already FAILED from a previous run are failure seeds (P24:
            # FAILED is locked, never re-run): block their transitive dependents
            # up front, exactly as a live failure would — otherwise a dependent
            # would launch and die later in reference resolution with a murkier
            # message.
            for c in calcs:
                if c.state == CalcState.FAILED:
                    blocked_names |= self._dependents_of(calcs, c.name)

            # The symmetric normalization: a row still BLOCKED from a previous
            # run whose ancestry is now clean (its failed parent was removed, or
            # its reference re-pointed) WILL run this pass — reset it up front so
            # the visible queue never shows a will-run row labelled with the
            # stale "Skipped: a dependency failed." until the walk reaches it.
            for idx, c in enumerate(calcs):
                if c.state == CalcState.BLOCKED and c.name not in blocked_names:
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
        try:
            self._run_walk(calcs, seen, blocked_names)
        finally:
            with self._queue_lock:
                self._active_names.clear()
                self._slots.clear()

    def _run_walk(self, calcs: list[Calculation], seen: dict,
                  blocked_names: set) -> None:
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
                with self._slot_cv:
                    if self._should_stop(calcs):
                        break
                    picked = self._pick_ready(calcs, seen, blocked_names)
                    if picked is None:
                        if all(id(c) in seen for c in calcs):
                            break                     # queue exhausted
                        if self._slots:
                            # a job in flight will free budget (or satisfy the
                            # dependency the rest are waiting on) and wake us
                            self._slot_cv.wait(_SLOT_POLL)
                            continue
                        # Nothing running and nothing affordable: let the first
                        # ready row through regardless of budget (see above).
                        picked = self._pick_ready(calcs, seen, blocked_names,
                                                  ignore_budget=True)
                        if picked is None:
                            # ...and nothing is even dependency-ready, with no
                            # job left to unblock it: the rest is a cycle.
                            self._block_unreachable(calcs, seen)
                            break
                    i, calc = picked
                    seen[id(calc)] = calc
                    self._active_names.add(calc.name)
                if not self._precheck(calc, i, blocked_names):
                    self._release(calc.name)
                    continue
                if self._try_keep_existing(calc, i):
                    self._release(calc.name)
                    continue
                if not self._start_job(calc, i, calcs, blocked_names, threads):
                    break
        finally:
            # The run is over only when every job it started has come back:
            # the store flips its "running" flag on this call returning, and a
            # detached/cancelled job still has state to stamp.
            for t in threads:
                t.join()

    def _pick_ready(self, calcs: list[Calculation], seen: dict,
                    blocked_names: set, ignore_budget: bool = False):
        """(index, calc) of the first row that may be handled now, or None.

        Caller must hold the lock: the name map, the seen-stamp and the
        _active_names entry have to land atomically so the store can refuse
        mutations of a calc that is picked but not yet stamped RUNNING (the
        pick->stamp window).
        """
        # names must track the live list: mid-run adds resolve their references
        # against the queue as it is NOW, not as it was at run start
        self._by_name = {c.name: c for c in calcs}
        for j, c in enumerate(calcs):
            if id(c) in seen:
                continue
            if not self._deps_ready(c):
                continue
            if ignore_budget or self._handled_without_running(c, blocked_names):
                return j, c
            if self._fits(self._slot_for(c)):
                return j, c
        return None

    def _deps_ready(self, calc: Calculation) -> bool:
        """False while this calc's geometry reference may still become DONE, so
        the dispatcher leaves it for a later scan instead of failing it.

        A dangling or self-reference is 'ready': it has always failed in
        _resolve_geometry with a precise message, and deferring it forever would
        turn a clear error into a hang. A FAILED/BLOCKED/CANCELLED parent is
        ready too — _precheck blocks the child, or the run fails it, as before.
        """
        if (calc.geometry_source != GeometrySource.REFERENCE) or not calc.ref_name:
            return True
        parent = self._by_name.get(calc.ref_name)
        if parent is None or parent is calc:
            return True
        return parent.state not in (CalcState.PENDING, CalcState.RUNNING)

    def _handled_without_running(self, calc: Calculation,
                                 blocked_names: set) -> bool:
        """True when _precheck / _try_keep_existing will dispose of this row
        without launching anything, so it needs no share of the budget."""
        return (calc.state in (CalcState.DONE, CalcState.FAILED)
                or calc.name in blocked_names
                or calc.name in self._skip_names)

    def _slot_for(self, calc: Calculation) -> "_JobSlot":
        return _JobSlot(name=calc.name, cores=declared_cores(calc),
                        ram_mb=estimated_ram_mb(calc))

    def _fits(self, slot: "_JobSlot") -> bool:
        """Does `slot` fit alongside what is already running? Caller holds the
        lock. Jobs, cores and estimated memory are all hard limits; see
        core/resources.py for how a calculation's cost is read."""
        if len(self._slots) >= self.budget.max_jobs:
            return False
        if sum(s.cores for s in self._slots.values()) + slot.cores > self.budget.cores:
            return False
        return (sum(s.ram_mb for s in self._slots.values()) + slot.ram_mb
                <= self.budget.ram_mb)

    def _release(self, name: str) -> None:
        """Drop a picked-but-not-started row's claim on the in-flight set."""
        with self._slot_cv:
            self._active_names.discard(name)
            self._slot_cv.notify_all()

    def _block_unreachable(self, calcs: list[Calculation], seen: dict) -> None:
        """Nothing is running and every remaining row is waiting on a geometry
        reference that can never complete — i.e. the references form a cycle.
        Stamp them BLOCKED instead of spinning forever."""
        for j, c in enumerate(calcs):
            if id(c) in seen:
                continue
            seen[id(c)] = c
            c.state = CalcState.BLOCKED
            c.message = "Skipped: its geometry reference can never complete."
            self.cb.log(f"[{c.name}] blocked (circular geometry reference).",
                        "warn", c.name)
            self.cb.calc_update(j, c)

    def _start_job(self, calc: Calculation, i: int, calcs: list[Calculation],
                   blocked_names: set, threads: list) -> bool:
        """Reserve this calc's resources and run it in its own thread. Returns
        False if a stop landed while waiting for room (nothing was started)."""
        slot = self._slot_for(calc)
        with self._slot_cv:
            # A row picked as "will be handled without running" (a keep-existing
            # candidate) can still fall through to a real run, so admission is
            # confirmed HERE, not at pick time. Waiting stops as soon as nothing
            # is in flight — the run-alone rule that keeps big jobs from
            # starving.
            while self._slots and not self._fits(slot):
                if self._stop_pending():
                    break
                self._slot_cv.wait(_SLOT_POLL)
            if self._stop_pending():
                self._active_names.discard(calc.name)
                self._slot_cv.notify_all()
                return False
            if not self._fits(slot) and self.budget.max_jobs > 1:
                self.cb.log(
                    f"[{calc.name}] needs {slot.cores} core(s) / "
                    f"{slot.ram_mb} MB — more than the whole budget "
                    f"({self.budget.cores} core(s) / {self.budget.ram_mb} MB); "
                    "running it on its own.", "warn", calc.name)
            self._slots[calc.name] = slot

        def _work() -> None:
            self._job_local.slot = slot
            try:
                if not self._dispatch_and_handle(calc, i, calcs, blocked_names):
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

    def resource_usage(self) -> dict:
        """What the in-flight jobs have reserved, against the budget. Read by
        the store for the queue snapshot (duck-typed, so the store never imports
        the engine)."""
        with self._queue_lock:
            return {
                "cores_used": sum(s.cores for s in self._slots.values()),
                "cores_budget": self.budget.cores,
                "ram_used_mb": sum(s.ram_mb for s in self._slots.values()),
                "ram_budget_mb": self.budget.ram_mb,
                "jobs": len(self._slots),
                "max_jobs": self.budget.max_jobs,
            }

    def _stop_pending(self) -> bool:
        """A cancel or a shutdown has been signalled — start nothing more."""
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

        # Graceful drain: the user asked to stop AFTER the current job. By
        # now that job has finished; leave the remaining calcs PENDING (so
        # they stay runnable) and stop processing the queue.
        if self._stop_after_current.is_set():
            # count by state, not position: every already-handled row was
            # stamped out of PENDING, so the PENDING rows (including the
            # one just picked) are exactly what this drain leaves behind
            remaining = sum(1 for c in calcs if c.state == CalcState.PENDING)
            self.cb.log(
                f"Stopping after the running job(s); "
                f"{remaining} calculation(s) left pending.",
                "info",
            )
            return True
        return False

    def _precheck(self, calc: Calculation, i: int, blocked_names: set) -> bool:
        """State screening before a calc may run. False → the row was handled
        here (the walk skips it); True → proceed to run. May add to
        blocked_names (a mid-run add whose parent already failed)."""
        # Already finished successfully on a previous run: don't recompute
        # (that would waste time and overwrite good results). Checked BEFORE
        # blocked_names so a frozen DONE result is never re-stamped BLOCKED
        # by a dependency that failed later. CANCELLED calculations DO
        # re-run, so the user can retry them.
        if calc.state == CalcState.DONE:
            self.cb.log(f"[{calc.name}] already done — skipping.", "info")
            self.cb.calc_update(i, calc)
            return False

        # FAILED is locked (P24): never re-run a failed calc; a retry is a
        # NEW calculation. Checked before blocked_names so the failure
        # diagnosis is never re-stamped BLOCKED.
        if calc.state == CalcState.FAILED:
            self.cb.log(f"[{calc.name}] failed previously — locked; "
                        "build a new calculation to retry.", "warn")
            return False

        # A row added (or edited) mid-run postdates run_all's blocked_names
        # bookkeeping, so also judge its DIRECT parent's live state:
        # referencing a FAILED/BLOCKED calc blocks it exactly like a
        # dependent known at seed time. (Transitively sound: mid-run adds
        # append, so a parent is always visited before its new dependent.)
        if calc.name not in blocked_names and calc.ref_name and (
                calc.geometry_source == GeometrySource.REFERENCE):
            parent = self._by_name.get(calc.ref_name)
            if parent is not None and parent.state in (
                    CalcState.FAILED, CalcState.BLOCKED):
                blocked_names.add(calc.name)

        if calc.name in blocked_names:
            calc.state = CalcState.BLOCKED
            calc.message = "Skipped: a dependency failed."
            self.cb.log(f"[{calc.name}] blocked (a dependency failed).", "warn")
            self.cb.calc_update(i, calc)
            return False
        return True

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
        if calc.name not in self._skip_names:
            return False
        if calc.kind.startswith("mlip"):
            out_path = self.workspace_root / calc.name / f"{calc.name}.mlip.json"
        else:
            out_path = self.workspace_root / calc.name / f"{calc.name}.out"
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
                    self._by_name[calc.name] = calc
                    self.cb.log(f"[{calc.name}] kept existing result on disk — not recomputed.", "info")
            except OrcaRunError as e:
                problem = f"existing result invalid ({e})"
                calc.output_path = prev_output_path
            except Exception as e:
                problem = f"could not read existing result ({e})"
                calc.output_path = prev_output_path
        else:
            problem = "no existing result found"
        if problem:
            self.cb.log(f"[{calc.name}] {problem}; running.", "warn")
            self._skip_names.discard(calc.name)
            return False
        self.cb.calc_update(i, calc)
        return True

    def _dispatch_and_handle(self, calc: Calculation, i: int,
                             calcs: list[Calculation], blocked_names: set) -> bool:
        """Run one calc through its backend and translate the outcome into a
        queue state. False → break out of the walk (app shutdown); True →
        walk on to the next row. May add to blocked_names (dependency-scoped
        failure propagation)."""
        # No launch has happened for this attempt yet — until _run_calc
        # reports one, _failure_reason must not trust an on-disk .out (A22).
        self._orca_launched = False
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
        except OrcaCancelled:
            # user stopped the run mid-calc: mark THIS calc CANCELLED (not
            # FAILED) and do NOT block its dependents. The remaining PENDING
            # calcs are left as-is — the top-of-loop cancel guard breaks out
            # of the walk on the next pass, so the queued plan is preserved.
            calc.state = CalcState.CANCELLED
            calc.message = "Cancelled by user."
            calc.pid = None
            calc.create_time = None
            self.cb.log(f"[{calc.name}] cancelled.", "info", calc.name)
            self.cb.calc_update(i, calc)
        except OrcaRunError as e:
            calc.state = CalcState.FAILED
            calc.message = self._failure_reason(calc, str(e))
            calc.pid = None
            calc.create_time = None
            self.cb.log(f"[{calc.name}] FAILED: {calc.message}", "err", calc.name)
            self.cb.calc_update(i, calc)
            with self._queue_lock:
                blocked_names |= self._dependents_of(calcs, calc.name)
        except Exception as e:  # defensive
            calc.state = CalcState.FAILED
            calc.message = f"Unexpected error: {e}"
            calc.pid = None
            calc.create_time = None
            self.cb.log(f"[{calc.name}] FAILED: {e}", "err", calc.name)
            self.cb.calc_update(i, calc)
            with self._queue_lock:
                blocked_names |= self._dependents_of(calcs, calc.name)
        return True
