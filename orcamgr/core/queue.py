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

import copy
import shutil
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from .input_generator import (
    StepConfig, build_input, render_raw_input, GEOMETRY_PLACEHOLDER, check_neb_atom_order,
    _xyz_elements,
)
from .runner import OrcaRunner, OrcaRunError, OrcaCancelled, OrcaDetached
from .parser import parse_file, ParseResult
from .procutil import process_matches


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
    # Display-only provenance for a per-conformer track clone whose geometry was
    # baked in from a conformer search (see expand_conformer_tracks): e.g.
    # "tt1 · conformer 2". "" for ordinary calcs. Never affects execution.
    conformer_origin: str = ""

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
    log: Callable[[str, str], None] = lambda msg, level: None
    calc_update: Callable[[int, "Calculation"], None] = lambda i, c: None
    # Structural queue substitution (per-conformer track expansion). When wired
    # to a store the walked list IS the store's list (live-queue, P29), and
    # this callback applies the splice there (store.substitute_calcs, in
    # place); the engine only splices its own list when the callback didn't
    # (the default no-op used by standalone/unit-test engines). Returns False
    # to REFUSE the substitution (e.g. a name collision with a calc added
    # mid-run) — the engine then leaves the templates in place
    # (single-geometry fallback) instead of diverging.
    queue_substitute: Callable[[list, list], bool] = lambda removed_names, new_calcs: True


def _xyz_from_geometry(result: ParseResult) -> str:
    return "\n".join(
        f"{a.symbol} {a.x:.10f} {a.y:.10f} {a.z:.10f}" for a in result.geometry
    )


def _as_xyz_file(coords: str) -> str:
    """Wrap a bare coordinate block ('El x y z' lines) as a standard .xyz file:
    an atom-count header, a comment line, then the coordinates. If the text
    already looks like a full .xyz (first line is just an integer), return as-is."""
    lines = [ln for ln in coords.strip().splitlines() if ln.strip()]
    if lines and lines[0].split() and lines[0].split()[0].isdigit() and len(lines[0].split()) == 1:
        return coords.strip() + "\n"   # already has an xyz header
    n = sum(1 for ln in lines if len(ln.split()) >= 4)
    body = "\n".join(lines)
    return f"{n}\ngenerated by ORCAdesk\n{body}\n"


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


def _bare_xyz_from_atoms(atoms) -> str:
    """Bare 'El x y z' coordinate block from a list of parser Atoms."""
    return "\n".join(f"{a.symbol} {a.x:.10f} {a.y:.10f} {a.z:.10f}" for a in atoms)


def _unique_track_name(base: str, taken: "set[str]") -> str:
    """taken holds CASEFOLDED names: uniqueness must match the store's
    case-insensitive rule (substitute_calcs), or a case-colliding queued name
    would slip past here only to abort the whole substitution there."""
    if base.casefold() not in taken:
        return base
    n = 2
    while f"{base}_{n}".casefold() in taken:
        n += 1
    return f"{base}_{n}"


def expand_conformer_tracks(crest: Calculation, result: ParseResult,
                            calcs: "list[Calculation]",
                            taken_names: "set[str]"):
    """Per-conformer track substitution for a finished "all"-handoff conformer
    search (StepConfig.crest_handoff == "all").

    Every PENDING calc referencing ``crest`` — directly or transitively — is a
    TEMPLATE. Each template becomes one clone per conformer, ``{name}_c{k}``, in
    track-major order (conformer 1's whole chain, then conformer 2's, …), so a
    queued ``crest ← opt ← freq`` pipeline fans out into K independent tracks.
    A clone whose template referenced the crest directly gets that conformer's
    coordinates baked in as DIRECT geometry (the clone no longer depends on the
    crest row); deeper clones stay REFERENCE, re-pointed to the same-track clone
    of their parent — so dependency-scoped failure blocking works per track.

    Only PENDING templates participate: anything DONE/FAILED/… keeps its state
    and its original geometry. Returns ``(removed_template_names, new_calcs)``,
    or None when there is nothing to expand (handoff != "all", fewer than two
    conformers, or no pending templates). Pure — mutates nothing."""
    if getattr(crest.config, "crest_handoff", "") != "all":
        return None
    conformers = list(getattr(result, "conformers", None) or [])
    if len(conformers) < 2:
        return None

    # the PENDING reference-closure of the crest (fixpoint, then queue order)
    track_names = {crest.name}
    templates: list[Calculation] = []
    changed = True
    while changed:
        changed = False
        for c in calcs:
            if (c.state == CalcState.PENDING
                    and c.geometry_source == GeometrySource.REFERENCE
                    and c.ref_name in track_names
                    and c.name not in track_names):
                track_names.add(c.name)
                templates.append(c)
                changed = True
    if not templates:
        return None
    order = {id(c): i for i, c in enumerate(calcs)}
    templates.sort(key=lambda c: order[id(c)])

    taken = {n.casefold() for n in taken_names}
    clone_name: dict = {}          # (template_name, k) -> clone name
    added: list[Calculation] = []
    for k, conf in enumerate(conformers, start=1):
        # name every clone of this track BEFORE building any: the re-pointing
        # below needs the parent template's clone name, and a child template
        # can precede its parent in queue order (refs are free-form at add
        # time) — filling the map lazily left such a child pointing at the
        # parent's ORIGINAL name, which is removed by the substitution
        for t in templates:
            name = _unique_track_name(f"{t.name}_c{k}", taken)
            taken.add(name.casefold())
            clone_name[(t.name, k)] = name
        for t in templates:
            name = clone_name[(t.name, k)]
            if t.ref_name == crest.name:
                src, xyz, ref = GeometrySource.DIRECT, _bare_xyz_from_atoms(conf.geometry), ""
                origin = f"{crest.name} · conformer {k}"
            else:
                src, xyz, ref, origin = GeometrySource.REFERENCE, "", clone_name.get((t.ref_name, k), t.ref_name), ""
            added.append(Calculation(
                name=name, kind=t.kind, config=copy.deepcopy(t.config),
                charge=t.charge, multiplicity=t.multiplicity,
                geometry_source=src, xyz=xyz, ref_name=ref,
                conformer_origin=origin, is_raw=t.is_raw, raw_text=t.raw_text,
            ))
    return [t.name for t in templates], added


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
            imag = [f for f in result.frequencies if f < 0]
            detail = ", ".join(f"{v:.2f}" for v in imag[:5])
            raise OrcaRunError(
                f"{result.n_imaginary} imaginary frequency/frequencies "
                f"(cm^-1: {detail}). Not a true minimum."
            )
        return
    if calc.kind in ("opt", "ts_opt", "opt_freq", "ts_opt_freq") and not result.opt_converged:
        raise OrcaRunError("Optimization did not converge.")
    if calc.kind in ("freq", "opt_freq") and result.n_imaginary > 0:
        imag = [f for f in result.frequencies if f < 0]
        detail = ", ".join(f"{v:.2f}" for v in imag[:5])
        raise OrcaRunError(
            f"{result.n_imaginary} imaginary frequency/frequencies "
            f"(cm^-1: {detail}). Not a true minimum."
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
            imag = [f for f in result.frequencies if f < 0]
            detail = ", ".join(f"{v:.2f}" for v in imag[:5])
            raise OrcaRunError(
                f"{n} imaginary frequencies (cm^-1: {detail}). A transition "
                "state should have exactly one; this is a higher-order saddle point."
            )
    if calc.kind == "neb_ts" and result.frequencies:
        # preset_neb_ts appends FREQ to verify the located TS. If frequencies
        # were computed, require exactly one imaginary mode (a true first-order
        # saddle); skip the check when the user removed FREQ (no frequencies).
        n = result.n_imaginary
        if n != 1:
            imag = [f for f in result.frequencies if f < 0]
            detail = ", ".join(f"{v:.2f}" for v in imag[:5]) if imag else "none"
            raise OrcaRunError(
                f"NEB-TS located a structure with {n} imaginary frequency/frequencies "
                f"(cm^-1: {detail}); a transition state must have exactly one."
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
                 queue_lock=None):
        self.runner = OrcaRunner(orca_path)
        # Registered MLIP environments [{id, name, python}], used to resolve which
        # interpreter runs a mlip_* calc. Empty if MLIP isn't configured.
        self.mlip_envs = list(mlip_envs or [])
        # the MlipRunner of the in-flight MLIP job, so cancel()/detach() can reach
        # it (the ORCA runner and the MLIP runner are separate objects).
        self._mlip_runner = None
        # Preferred WSL distro for CREST calcs ("" = auto-detect the first distro
        # that has CREST). A crest_conf calc runs its Linux binary through WSL
        # (see orcamgr/crest/), OUTSIDE the ORCA pipeline.
        self.crest_distro = (crest_distro or "").strip()
        # the CrestRunner of the in-flight CREST job, so cancel()/detach() reach it.
        self._crest_runner = None
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
        self._by_name: dict[str, Calculation] = {}
        # calculations the user chose to skip this run (e.g. to keep existing
        # results on disk instead of overwriting them)
        self._skip_names = set(skip_names or ())
        # True once the current calc attempt has actually launched (or adopted)
        # its ORCA process — i.e. the on-disk .out belongs to THIS attempt.
        # Gates _failure_reason's .out parse: the folder may hold a stale .out
        # from a previous run (e.g. after a rejected keep-existing result), and
        # parsing it on a pre-launch failure would mask the real cause (A22).
        # Reset per calc in run_all, set in _run_calc.
        self._orca_launched = False
        # The list run_all walks is LIVE (P29): when wired to a store,
        # queue_lock IS the store's own lock (make_engine_factory), so user
        # mutations of the queue and the walk's pick step are serialized.
        # Standalone engines (tests) get a private lock — no concurrency there.
        self._queue_lock = queue_lock if queue_lock is not None else threading.RLock()
        # Name of the calc the walk is currently handling. Set at pick time
        # UNDER the lock — i.e. before the RUNNING stamp lands — so the store
        # can refuse mutations of a calc the engine has picked but not yet
        # stamped (the pick→stamp window would otherwise let an edit/remove
        # race a launch).
        self._active_name: Optional[str] = None

    @property
    def active_name(self) -> Optional[str]:
        """Name of the calc the walk is currently handling (None between runs).
        The store consults this to refuse mutations of the in-flight calc."""
        with self._queue_lock:
            return self._active_name

    def cancel(self) -> None:
        self._cancel_event.set()
        self.runner.cancel()
        if self._mlip_runner is not None:
            self._mlip_runner.cancel()
        if self._crest_runner is not None:
            self._crest_runner.cancel()

    def request_stop_after_current(self) -> None:
        """Stop processing once the in-flight job finishes; leave the remaining
        calculations PENDING. Does NOT kill the running job."""
        self._stop_after_current.set()

    def detach(self) -> None:
        """Stop monitoring the queue WITHOUT killing the running ORCA — used on
        app shutdown so the in-flight job survives and can be reattached. The
        running calc is left in the RUNNING state (its pid is already persisted)."""
        self._detach_event.set()
        self.runner.detach()
        if self._mlip_runner is not None:
            self._mlip_runner.detach()
        if self._crest_runner is not None:
            self._crest_runner.detach()

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
            self.runner.adopt(calc.pid, calc.create_time)
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
            start_pos = self.runner.end_position(out_path)
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

        # NEB-TS needs side .xyz files in the run folder that the %neb block
        # points at: the product geometry (required) and an optional TS guess.
        # build_input references fixed names "product.xyz" / "ts_guess.xyz", so
        # write them here. This must also happen in RAW mode — a raw NEB-TS keeps
        # the NEB_End_XYZFile "product.xyz" reference, so the file must exist.
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
                (calc_dir / "product.xyz").write_text(_as_xyz_file(prod), encoding="utf-8")
                guess = (calc.config.neb_ts_guess_xyz or "").strip()
                if guess:
                    (calc_dir / "ts_guess.xyz").write_text(_as_xyz_file(guess), encoding="utf-8")

        # IRC with a read-in Hessian: the generated input references the file
        # relative to the run folder, but nothing guarantees it is there (the
        # folder may not even exist before the first run). Stage it from the
        # referenced calc's folder when possible; otherwise fail fast with a
        # clear message instead of a cryptic ORCA abort.
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

        self.cb.log(f"[{calc.name}] ({calc.kind}) running ORCA...", "info")
        pid, create_time = self.runner.launch(inp_path, out_path)
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

    def _monitor_and_finish(self, calc: Calculation, index: int, out_path,
                            start_pos: int = 0) -> None:
        """Tail ORCA's output until it exits (or is cancelled/detached), then
        parse + validate + mark DONE. Shared by the fresh-launch and reattach
        paths (reattach passes start_pos = current EOF). OrcaCancelled /
        OrcaDetached propagate to run_all."""
        self.runner.monitor(out_path, on_line=lambda ln: self.cb.log(ln, "orca"),
                            start_pos=start_pos)

        result = parse_file(str(out_path))
        calc.result = result
        calc.output_path = str(out_path)

        validate_result(calc, result)

        calc.state = CalcState.DONE
        calc.pid = None
        calc.create_time = None
        calc.message = "Completed."
        self.cb.log(f"[{calc.name}] done.", "ok")
        self.cb.calc_update(index, calc)

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
            pressure=getattr(calc.config, "freq_pressure_atm", 1.0))

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
        runner = MlipRunner(python)
        self._mlip_runner = runner
        try:
            # forward a Stop/shutdown that landed BEFORE the runner was
            # registered (cancel()/detach() signal only the registered runner)
            if self._cancel_event.is_set():
                runner.cancel()
            if self._detach_event.is_set():
                runner.detach()
            rc = runner.run(script_path, [str(config_path)], out_path,
                            cwd=calc_dir, on_line=lambda ln: self.cb.log(ln, "orca"))
        finally:
            self._mlip_runner = None
        if not result_json.exists():
            raise OrcaRunError(
                f"MLIP worker exited (code {rc}) without writing a result — "
                "see the run log / .out for the underlying error.")

        result = parse_mlip_result(str(result_json))
        calc.result = result
        calc.output_path = str(result_json)

        validate_result(calc, result)   # raises on failure / non-convergence

        calc.state = CalcState.DONE
        calc.pid = None
        calc.create_time = None
        calc.message = "Completed."
        self.cb.log(f"[{calc.name}] done.", "ok")
        self.cb.calc_update(index, calc)

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
            runner = CrestRunner(distro, "") if distro else None
            if runner is not None:
                runner.adopt(calc.pid, calc.create_time)
            if runner is not None and runner.is_alive():
                self._crest_runner = runner
                # forward a Stop/shutdown that landed before registration
                if self._cancel_event.is_set():
                    runner.cancel()
                if self._detach_event.is_set():
                    runner.detach()
                self.cb.log(f"[{calc.name}] reattaching to CREST still running "
                            f"(pid {calc.pid})...", "info")
                self.cb.calc_update(index, calc)
                start_pos = runner.end_position(out_path)
                try:
                    self._crest_monitor_and_finish(calc, index, out_path,
                                                   calc.name, start_pos=start_pos)
                finally:
                    self._crest_runner = None
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
        runner = CrestRunner(distro, crest_bin)
        self._crest_runner = runner
        try:
            # forward a Stop/shutdown that landed before the runner was
            # registered (cancel()/detach() signal only the registered runner)
            if self._cancel_event.is_set():
                runner.cancel()
            if self._detach_event.is_set():
                runner.detach()
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
            self._crest_runner = None

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
            self._crest_runner.monitor(out_path, name,
                                       on_line=lambda ln: self.cb.log(ln, "orca"),
                                       start_pos=start_pos)

        result = parse_crest_result(str(out_path))
        calc.result = result
        calc.output_path = str(out_path)

        validate_result(calc, result)   # raises on failure / no conformers

        calc.state = CalcState.DONE
        calc.pid = None
        calc.create_time = None
        n = len(result.conformers)
        calc.message = f"Completed — {n} conformer(s)."
        self.cb.log(f"[{calc.name}] done — {n} conformer(s).", "ok")
        self._export_crest_conformers(calc, Path(out_path).parent)
        self.cb.calc_update(index, calc)

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

    # -- per-conformer track expansion (crest_handoff == "all") --
    def _maybe_expand_crest(self, calcs: list[Calculation], crest: Calculation) -> None:
        """If ``crest`` is a DONE conformer search whose handoff scope is "all",
        substitute every PENDING calc chain referencing it with one clone per
        conformer (expand_conformer_tracks). The owning store applies the same
        substitution through cb.queue_substitute so the UI queue and the session
        file stay in sync; if the store refuses (a name collision with a calc
        added mid-run), the templates stay as they are and simply run on the
        best conformer — the safe single-geometry fallback. Cheap no-op guards
        make this safe to call after every calc."""
        if not crest.kind.startswith("crest") or crest.state != CalcState.DONE:
            return
        if getattr(crest.config, "crest_handoff", "") != "all":
            return
        result = crest.result
        if result is None and crest.output_path:
            # DONE from a previous session: parse the on-disk ensemble now
            try:
                result = result_from_output(crest)
                crest.result = result
            except Exception:
                return
        if result is None:
            return
        # The whole expansion is one critical section: the templates are read,
        # substituted in the store, and (when needed) spliced locally against
        # ONE consistent view of the live queue — a user add/remove between
        # those steps must not interleave.
        with self._queue_lock:
            expansion = expand_conformer_tracks(
                crest, result, calcs,
                taken_names={c.name for c in calcs})
            if expansion is None:
                return
            removed, added = expansion
            removed_set = set(removed)
            if not self.cb.queue_substitute(list(removed), list(added)):
                self.cb.log(
                    f"[{crest.name}] could not expand conformer tracks (the queue "
                    "changed meanwhile); the referencing calculations keep the "
                    "lowest conformer.", "warn")
                return
            # When the store shares this list (live-queue wiring), its
            # substitute_calcs above already spliced it in place — splice
            # locally only when the templates are still present (standalone
            # engine with the default no-op callback, e.g. unit tests).
            if any(c.name in removed_set for c in calcs):
                first = min(i for i, c in enumerate(calcs) if c.name in removed_set)
                calcs[:] = [c for c in calcs if c.name not in removed_set]
                for off, nc in enumerate(added):
                    calcs.insert(first + off, nc)
            for name in removed:
                self._by_name.pop(name, None)
            for nc in added:
                self._by_name[nc.name] = nc
        k = len(getattr(result, "conformers", None) or [])
        self.cb.log(
            f"[{crest.name}] {k} conformers — expanded {len(removed)} referencing "
            f"calculation(s) into {len(added)} per-conformer track(s).", "info")

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
        # which is exactly what "edited back to PENDING" should do). The same
        # mechanism covers the per-conformer track expansion: substituted
        # clones are new objects, so they are visited by THIS run.
        # `seen` maps id() -> the object itself: the value pins the object so a
        # removed row's id can't be recycled onto a new row mid-run.
        seen: dict[int, Calculation] = {}
        try:
            self._run_walk(calcs, seen, blocked_names)
        finally:
            with self._queue_lock:
                self._active_name = None

    def _run_walk(self, calcs: list[Calculation], seen: dict,
                  blocked_names: set) -> None:
        while True:
            with self._queue_lock:
                # names must track the live list: mid-run adds resolve their
                # references (and crest expansion checks name collisions)
                # against the queue as it is NOW, not as it was at run start
                self._by_name = {c.name: c for c in calcs}
                nxt = next(((j, c) for j, c in enumerate(calcs)
                            if id(c) not in seen), None)
                if nxt is None:
                    break
                i, calc = nxt
                seen[id(calc)] = calc
                self._active_name = calc.name
            if self._detach_event.is_set():
                # App is shutting down: stop processing and leave every calc as
                # it is (a RUNNING one keeps going in the background for reattach).
                break

            if self._cancel_event.is_set():
                # Hard cancel stops the queue but only stamps the job that was
                # actually in flight: that one was killed and marked CANCELLED by
                # the OrcaCancelled handler below. Every remaining calc is left
                # UNTOUCHED — PENDING rows stay PENDING (re-runnable as-is, the
                # plan is not discarded), and a reattach-pending RUNNING row keeps
                # its pid for the next launch. (Deliberate: a cancel used to walk
                # on and stamp all remaining PENDING rows CANCELLED; now it does
                # not — stopping the run must not throw away the queued plan.)
                remaining = sum(1 for c in calcs if c.state == CalcState.PENDING)
                if remaining:
                    self.cb.log(
                        f"Cancelled; {remaining} calculation(s) left pending.", "info")
                break

            # Graceful drain: the user asked to stop AFTER the current job. By
            # now that job has finished; leave the remaining calcs PENDING (so
            # they stay runnable) and stop processing the queue.
            if self._stop_after_current.is_set():
                # count by state, not position: every already-handled row was
                # stamped out of PENDING, so the PENDING rows (including the
                # one just picked) are exactly what this drain leaves behind
                remaining = sum(1 for c in calcs if c.state == CalcState.PENDING)
                self.cb.log(
                    f"Stopped after current job; {remaining} calculation(s) left pending.",
                    "info",
                )
                break

            # Already finished successfully on a previous run: don't recompute
            # (that would waste time and overwrite good results). Checked BEFORE
            # blocked_names so a frozen DONE result is never re-stamped BLOCKED
            # by a dependency that failed later. CANCELLED calculations DO
            # re-run, so the user can retry them.
            if calc.state == CalcState.DONE:
                self.cb.log(f"[{calc.name}] already done \u2014 skipping.", "info")
                # an already-DONE "all"-handoff conformer search may have gained
                # new pending referencing chains since it finished \u2014 expand them
                # now (run-start expansion; no-op guarded inside)
                self._maybe_expand_crest(calcs, calc)
                self.cb.calc_update(i, calc)
                continue

            # FAILED is locked (P24): never re-run a failed calc; a retry is a
            # NEW calculation. Checked before blocked_names so the failure
            # diagnosis is never re-stamped BLOCKED.
            if calc.state == CalcState.FAILED:
                self.cb.log(f"[{calc.name}] failed previously \u2014 locked; "
                            "build a new calculation to retry.", "warn")
                continue

            # A row added (or edited) mid-run postdates the blocked_names
            # bookkeeping above, so also judge its DIRECT parent's live state:
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
                continue

            # User chose to keep the existing result on disk for this one.
            # Load that output (so references and the Results tab still work)
            # instead of recomputing and overwriting it. Parse through
            # result_from_output (kind dispatch: an MLIP calc's result is the
            # worker's JSON, not an ORCA .out) and gate DONE behind
            # validate_result, so a crashed or chemically-bad output can't be
            # resurrected as DONE (the 0.4.2 incident: a FAILED raw calc came
            # back DONE through this path). If the existing result doesn't
            # hold up, "keep" is impossible; the honest fallback is to run
            # the calc, never to mark it FAILED (the user asked to preserve
            # a result, not to lock the calc over a stale one).
            if calc.name in self._skip_names:
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
                        # the current queue) \u2014 never adopt a result that isn't
                        # structurally this calculation's.
                        problem = _kept_result_matches(calc, result)
                        if problem:
                            calc.output_path = prev_output_path
                        else:
                            calc.result = result
                            calc.state = CalcState.DONE
                            calc.message = "Kept existing result (not recomputed)."
                            self._by_name[calc.name] = calc
                            self.cb.log(f"[{calc.name}] kept existing result on disk \u2014 not recomputed.", "info")
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
                else:
                    # a kept crest result counts as DONE — expand its tracks too
                    self._maybe_expand_crest(calcs, calc)
                    self.cb.calc_update(i, calc)
                    continue

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
                    self.cb.log(f"[{calc.name}] stopped on shutdown.", "info")
                else:
                    self.cb.log(f"[{calc.name}] left running in the background.", "info")
                break
            except OrcaCancelled:
                # user stopped the run mid-calc: mark THIS calc CANCELLED (not
                # FAILED) and do NOT block its dependents. The remaining PENDING
                # calcs are left as-is — the top-of-loop cancel guard breaks out
                # of the walk on the next pass, so the queued plan is preserved.
                calc.state = CalcState.CANCELLED
                calc.message = "Cancelled by user."
                calc.pid = None
                calc.create_time = None
                self.cb.log(f"[{calc.name}] cancelled.", "info")
                self.cb.calc_update(i, calc)
            except OrcaRunError as e:
                calc.state = CalcState.FAILED
                calc.message = self._failure_reason(calc, str(e))
                calc.pid = None
                calc.create_time = None
                self.cb.log(f"[{calc.name}] FAILED: {calc.message}", "err")
                self.cb.calc_update(i, calc)
                with self._queue_lock:
                    blocked_names |= self._dependents_of(calcs, calc.name)
            except Exception as e:  # defensive
                calc.state = CalcState.FAILED
                calc.message = f"Unexpected error: {e}"
                calc.pid = None
                calc.create_time = None
                self.cb.log(f"[{calc.name}] FAILED: {e}", "err")
                self.cb.calc_update(i, calc)
                with self._queue_lock:
                    blocked_names |= self._dependents_of(calcs, calc.name)

            # a conformer search that just finished DONE may fan its referencing
            # chains out into per-conformer tracks (guarded inside; a no-op for
            # every other kind/state, including the failure paths above)
            self._maybe_expand_crest(calcs, calc)
