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
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from .input_generator import (
    StepConfig, build_input, render_raw_input, GEOMETRY_PLACEHOLDER, check_neb_atom_order,
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
    # Structural queue substitution (per-conformer track expansion): the engine
    # runs on a snapshot list, so replacing template rows with clones must also
    # be applied to the owning store. Returns False to REFUSE the substitution
    # (e.g. a name collision with a calc added mid-run) — the engine then leaves
    # the templates in place (single-geometry fallback) instead of diverging.
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


def _bare_xyz_from_atoms(atoms) -> str:
    """Bare 'El x y z' coordinate block from a list of parser Atoms."""
    return "\n".join(f"{a.symbol} {a.x:.10f} {a.y:.10f} {a.z:.10f}" for a in atoms)


def _unique_track_name(base: str, taken: "set[str]") -> str:
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
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

    taken = set(taken_names)
    clone_name: dict = {}          # (template_name, k) -> clone name
    added: list[Calculation] = []
    for k, conf in enumerate(conformers, start=1):
        for t in templates:
            name = _unique_track_name(f"{t.name}_c{k}", taken)
            taken.add(name)
            clone_name[(t.name, k)] = name
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
    # MLIP optimization: the only check is convergence (no ORCA-style keywords).
    if calc.kind.startswith("mlip"):
        if result.is_optimization and not result.opt_converged:
            raise OrcaRunError("MLIP optimization did not converge within the step limit.")
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
                 crest_distro: str = ""):
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

    # -- single calculation --
    def _run_calc(self, calc: Calculation, index: int) -> None:
        out_path = self.workspace_root / calc.name / f"{calc.name}.out"

        # Reattach path: this calc was left RUNNING by a previous session and its
        # ORCA process is genuinely still alive — don't relaunch, just resume
        # monitoring the live process and tailing its .out.
        if (calc.state == CalcState.RUNNING and calc.pid
                and process_matches(calc.pid, calc.create_time)):
            self.runner.adopt(calc.pid, calc.create_time)
            # The adopted process owns the on-disk .out, so a failure diagnosis
            # may trust it (see _failure_reason).
            self._orca_launched = True
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
            text = build_input(calc.config, xyz, calc.charge, calc.multiplicity)
        inp_path.write_text(text, encoding="utf-8")

        # NEB-TS needs side .xyz files in the run folder that the %neb block
        # points at: the product geometry (required) and an optional TS guess.
        # build_input references fixed names "product.xyz" / "ts_guess.xyz", so
        # write them here. This must also happen in RAW mode — a raw NEB-TS keeps
        # the NEB_End_XYZFile "product.xyz" reference, so the file must exist.
        if calc.kind == "neb_ts":
            prod = (calc.config.neb_product_xyz or "").strip()
            if not calc.is_raw:
                if not prod:
                    raise OrcaRunError("NEB-TS needs a product geometry, but none was provided.")
                # reactant (xyz) and product must have identical atoms in identical
                # order — catch the #1 NEB-TS user error before launching ORCA.
                chk = check_neb_atom_order(xyz, prod)
                if not chk.get("ok"):
                    raise OrcaRunError(chk.get("error") or "NEB-TS reactant/product atom mismatch.")
            if prod:
                (calc_dir / "product.xyz").write_text(_as_xyz_file(prod), encoding="utf-8")
                guess = (calc.config.neb_ts_guess_xyz or "").strip()
                if guess:
                    (calc_dir / "ts_guess.xyz").write_text(_as_xyz_file(guess), encoding="utf-8")

        self.cb.log(f"[{calc.name}] ({calc.kind}) running ORCA...", "info")
        pid, create_time = self.runner.launch(inp_path, out_path)
        # From here the .out on disk is THIS attempt's output — a failure
        # diagnosis may trust it (see _failure_reason).
        self._orca_launched = True
        calc.pid = pid
        calc.create_time = create_time
        # persist the pid immediately so a reattach is possible even if ORCAdesk
        # is closed seconds after the job starts.
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
        task = "sp" if calc.kind == "mlip_sp" else "opt"
        script_path, config_path = write_mlip_run_files(
            calc_dir, calc.name, calc.config.mlip_model, xyz, result_json,
            charge=calc.charge, multiplicity=calc.multiplicity, task=task)

        model = calc.config.mlip_model or "MACE"
        verb = "single-point energy" if task == "sp" else "optimizing"
        self.cb.log(f"[{calc.name}] ({calc.kind}) {verb} with {model} via {python}...", "info")
        runner = MlipRunner(python)
        self._mlip_runner = runner
        try:
            runner.run(script_path, [str(config_path)], out_path,
                       cwd=calc_dir, on_line=lambda ln: self.cb.log(ln, "orca"))
        finally:
            self._mlip_runner = None

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
        self.cb.calc_update(index, calc)

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
        expansion = expand_conformer_tracks(crest, result, calcs,
                                            taken_names=set(self._by_name))
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
        # mirror the substitution on the engine's own snapshot list
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
        self._by_name = {c.name: c for c in calcs}
        blocked_names: set[str] = set()
        # Calcs already FAILED from a previous run are failure seeds (P24:
        # FAILED is locked, never re-run): block their transitive dependents up
        # front, exactly as a live failure would — otherwise a dependent would
        # launch and die later in reference resolution with a murkier message.
        for c in calcs:
            if c.state == CalcState.FAILED:
                blocked_names |= self._dependents_of(calcs, c.name)

        # A growable walk (not `for … in enumerate`): a finished "all"-handoff
        # conformer search substitutes template rows further down the list with
        # per-conformer clones (see _maybe_expand_crest), and those inserted
        # rows must be visited by THIS run.
        i = -1
        while i + 1 < len(calcs):
            i += 1
            calc = calcs[i]
            if self._detach_event.is_set():
                # App is shutting down: stop processing and leave every calc as
                # it is (a RUNNING one keeps going in the background for reattach).
                break

            if self._cancel_event.is_set():
                # Never stamp over a terminal state: DONE is frozen and FAILED
                # is locked (P24) — re-stamping them CANCELLED would make them
                # re-runnable and lose the result / failure diagnosis. BLOCKED
                # keeps its "Skipped: a dependency failed." diagnosis too.
                if calc.state not in (CalcState.DONE, CalcState.FAILED,
                                      CalcState.BLOCKED):
                    calc.state = CalcState.CANCELLED
                    calc.message = "Cancelled."
                    calc.pid = None
                    calc.create_time = None
                    self.cb.calc_update(i, calc)
                continue

            # Graceful drain: the user asked to stop AFTER the current job. By
            # now that job has finished; leave the remaining calcs PENDING (so
            # they stay runnable) and stop processing the queue.
            if self._stop_after_current.is_set():
                remaining = sum(1 for c in calcs[i:] if c.state == CalcState.PENDING)
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
                # TERMINATED by detach (mlip/runner.py); claiming it is still
                # running would be a false log line (P2).
                if calc.kind.startswith("mlip"):
                    self.cb.log(f"[{calc.name}] stopped on shutdown.", "info")
                else:
                    self.cb.log(f"[{calc.name}] left running in the background.", "info")
                break
            except OrcaCancelled:
                # user stopped the run mid-calc: mark THIS calc CANCELLED (not
                # FAILED) and do NOT block its dependents — the remaining calcs
                # are marked CANCELLED by the top-of-loop guard on the next pass.
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
                blocked_names |= self._dependents_of(calcs, calc.name)
            except Exception as e:  # defensive
                calc.state = CalcState.FAILED
                calc.message = f"Unexpected error: {e}"
                calc.pid = None
                calc.create_time = None
                self.cb.log(f"[{calc.name}] FAILED: {e}", "err")
                self.cb.calc_update(i, calc)
                blocked_names |= self._dependents_of(calcs, calc.name)

            # a conformer search that just finished DONE may fan its referencing
            # chains out into per-conformer tracks (guarded inside; a no-op for
            # every other kind/state, including the failure paths above)
            self._maybe_expand_crest(calcs, calc)
