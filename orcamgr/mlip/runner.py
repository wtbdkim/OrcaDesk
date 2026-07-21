"""
Run a single MLIP (MACE) geometry optimization by shelling out to the USER's
MLIP Python environment.

This deliberately mirrors ``core/runner.py`` but is much simpler, because an
MLIP optimization is short and does not need to survive ORCAdesk closing (no
detached/reattach machinery):

* We launch the user's interpreter on a generated worker script (written into
  the calc's run folder, so it is inspectable / re-runnable), piping its stdout
  into the ``.out`` file and the live log line by line.
* The worker imports torch/mace/ase IN THE USER'S ENV (ORCAdesk never imports
  them), runs an ASE optimizer with a MACE calculator, and writes a small JSON
  result + the optimized ``.xyz``. ``mlip/parser.py`` reads that JSON back into
  the shared ``ParseResult`` so the geometry hands off to a downstream ORCA calc
  exactly like an ORCA opt would.

It reuses ``OrcaRunError`` / ``OrcaCancelled`` / ``OrcaDetached`` from
``core/runner.py`` so ``QueueEngine.run_all``'s existing except-handlers treat an
MLIP job's cancel/shutdown/failure identically to an ORCA job's. Those exception
types are generic "subprocess run" signals, not ORCA-specific behaviour.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from ..core.runner import OrcaRunError, OrcaCancelled, OrcaDetached


LogCallback = Callable[[str], None]

# Optimizer defaults (v1; CPU only — see the env discussion). fmax in eV/Å.
DEFAULT_FMAX = 0.05
DEFAULT_MAX_STEPS = 500
DEFAULT_DEVICE = "cpu"


# The worker script, run by the USER's interpreter (so it may import torch/mace/
# ase, which ORCAdesk's own env need not have). It is parameter-free — all inputs
# come from a JSON config file passed as argv[1] — so nothing is string-formatted
# into it (no code injection from model names / paths). Kept as a module-level
# constant so tests can swap in a stdlib-only stub.
MACE_WORKER_SCRIPT = r'''
import sys, json, traceback

def main():
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        cfg = json.load(f)
    task = cfg.get("task", "opt")
    result = {"converged": False, "energy_ev": None, "n_steps": None, "task": task,
              "model": cfg.get("model", ""), "geometry": [], "error": None}
    try:
        from ase.io import read, write
        if cfg["family"] == "mace_off":
            from mace.calculators import mace_off as load_mace
        elif cfg["family"] == "mace_omol":
            from mace.calculators import mace_omol as load_mace
        else:
            from mace.calculators import mace_mp as load_mace
        print("[mlip] loading " + str(cfg["model"]) + " (device=" + str(cfg["device"]) + ")", flush=True)
        calc = load_mace(model=cfg["model_arg"], device=cfg["device"], default_dtype="float64")
        atoms = read(cfg["input_xyz"])
        # OMol25 / multi-head models are charge- and spin-aware: MACECalculator
        # reads atoms.info["charge"] and ["spin"], where "spin" is the SPIN
        # MULTIPLICITY (2S+1) — MACE's own default total_spin is 1.0 (singlet)
        # and its electrostatics use (total_spin - 1) = 2S. So pass the calc's
        # multiplicity directly, NOT multiplicity-1. MACE-OFF/MP ignore both, so
        # setting them unconditionally is harmless and keeps the worker uniform.
        atoms.info["charge"] = int(cfg.get("charge", 0))
        atoms.info["spin"] = max(1, int(cfg.get("multiplicity", 1)))
        atoms.calc = calc
        if task == "sp":
            # single point: energy (+ forces) at the given geometry, no relaxation
            print("[mlip] single point on " + str(len(atoms)) + " atoms", flush=True)
            energy = float(atoms.get_potential_energy())
            import numpy as _np
            fmax = float(_np.linalg.norm(atoms.get_forces(), axis=1).max()) if len(atoms) else 0.0
            result["converged"] = True     # an SP has nothing to converge
            result["n_steps"] = 0
            result["fmax"] = fmax
            print("[mlip] done: energy=" + repr(energy) + " eV  fmax=" + repr(fmax) + " eV/A", flush=True)
        else:
            from ase.optimize import LBFGS
            print("[mlip] optimizing " + str(len(atoms)) + " atoms, fmax=" + str(cfg["fmax"]), flush=True)
            opt = LBFGS(atoms, logfile="-")
            result["converged"] = bool(opt.run(fmax=cfg["fmax"], steps=cfg["max_steps"]))
            result["n_steps"] = int(opt.get_number_of_steps())
            energy = float(atoms.get_potential_energy())
            print("[mlip] done: converged=" + str(result["converged"]) + " energy=" + repr(energy) +
                  " eV steps=" + str(result["n_steps"]), flush=True)
        write(cfg["output_xyz"], atoms)
        syms = atoms.get_chemical_symbols()
        pos = atoms.get_positions()
        result["geometry"] = [[syms[i], float(pos[i][0]), float(pos[i][1]), float(pos[i][2])]
                              for i in range(len(atoms))]
        result["energy_ev"] = energy
    except Exception as e:
        result["error"] = type(e).__name__ + ": " + str(e)
        traceback.print_exc()
    with open(cfg["result_json"], "w", encoding="utf-8") as f:
        json.dump(result, f)

main()
'''


# Special models whose loader/model-arg can't be expressed as a size heuristic:
# a label containing the key maps to (loader family, model= argument). Checked
# before the small/medium/large heuristic. mace_omol is the dedicated OMol25
# model; mh-0/mh-1 are the multi-head models (which include an OMol head) and
# load through mace_mp. Order matters only in that these win over the heuristic.
_SPECIAL_MODELS = {
    "omol": ("mace_omol", "extra_large"),
    "mh-1": ("mace_mp", "mh-1"),
    "mh-0": ("mace_mp", "mh-0"),
}


def parse_mace_model(model: str) -> tuple[str, str]:
    """Map a dropdown label to (family, model_arg) for the worker. family is one
    of {'mace_off','mace_mp','mace_omol'} (the loader function); model_arg is
    passed verbatim to that loader (a size 'small'/'medium'/'large', or a named
    model like 'extra_large'/'mh-1'). 'MACE-OFF medium' -> ('mace_off','medium'),
    'MACE-OMOL extra-large' -> ('mace_omol','extra_large'), 'MACE-MH-1' ->
    ('mace_mp','mh-1'). Unknown labels default to MACE-OFF medium."""
    m = (model or "").strip().lower()
    for key, fam_arg in _SPECIAL_MODELS.items():
        if key in m:
            return fam_arg
    family = "mace_mp" if "mp" in m else "mace_off"
    size = next((s for s in ("small", "medium", "large") if s in m), "medium")
    return family, size


def _as_xyz_file(coords: str) -> str:
    """Wrap a bare 'El x y z' coordinate block as a standard .xyz (count header +
    comment + body). If it already starts with a lone integer, assume it's a full
    .xyz and return as-is. (Local copy to keep mlip/ independent of core/queue.)"""
    lines = [ln for ln in coords.strip().splitlines() if ln.strip()]
    if lines and lines[0].split() and lines[0].split()[0].isdigit() and len(lines[0].split()) == 1:
        return coords.strip() + "\n"
    n = sum(1 for ln in lines if len(ln.split()) >= 4)
    return f"{n}\ngenerated by ORCAdesk\n" + "\n".join(lines) + "\n"


def write_mlip_run_files(calc_dir, name: str, model: str, xyz: str, result_json,
                         charge: int = 0, multiplicity: int = 1,
                         task: str = "opt") -> tuple[Path, Path]:
    """Write the input .xyz, the JSON config, and the worker script into the run
    folder. Returns (script_path, config_path) for MlipRunner.run(). charge and
    multiplicity are passed to the worker for charge/spin-aware models (OMol25 /
    multi-head); MACE-OFF/MP ignore them. task is "opt" (LBFGS relaxation) or
    "sp" (single-point energy at the given geometry)."""
    calc_dir = Path(calc_dir)
    calc_dir.mkdir(parents=True, exist_ok=True)
    input_xyz = calc_dir / f"{name}.xyz"
    output_xyz = calc_dir / f"{name}.opt.xyz"
    config_path = calc_dir / "mlip_config.json"
    script_path = calc_dir / "mlip_opt.py"

    input_xyz.write_text(_as_xyz_file(xyz), encoding="utf-8")
    family, model_arg = parse_mace_model(model)
    cfg = {
        "model": model or "MACE-OFF medium",
        "family": family, "model_arg": model_arg, "device": DEFAULT_DEVICE,
        "charge": int(charge), "multiplicity": int(multiplicity),
        "task": "sp" if str(task).lower() == "sp" else "opt",
        "fmax": DEFAULT_FMAX, "max_steps": DEFAULT_MAX_STEPS,
        "input_xyz": str(input_xyz), "output_xyz": str(output_xyz),
        "result_json": str(result_json),
    }
    config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    script_path.write_text(MACE_WORKER_SCRIPT, encoding="utf-8")
    return script_path, config_path


class MlipRunner:
    """Launches the user's interpreter on the worker script and tails its stdout.
    Cancel/detach are signalled from the UI thread; the streaming loop terminates
    the process on the next line (mirrors OrcaRunner's signal-then-act model)."""

    def __init__(self, python_path: str):
        self.python_path = python_path
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._cancel = threading.Event()
        self._detach = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def detach(self) -> None:
        self._detach.set()

    def run(self, script_path, args, output_path, cwd=None,
            on_line: Optional[LogCallback] = None) -> int:
        """Run ``python script_path args...``, streaming stdout (stderr merged) to
        ``output_path`` and ``on_line``. Returns the exit code. Raises OrcaRunError
        if the interpreter can't launch, OrcaCancelled / OrcaDetached if signalled."""
        py = self.python_path
        if not py or not Path(py).exists():
            raise OrcaRunError(f"MLIP interpreter not found: '{py}'. "
                               "Check Settings → MLIP environments.")
        # No event clearing: the runner is created fresh per calc, and the
        # engine forwards a Stop/shutdown that landed before it was registered
        # — clearing would erase exactly that signal.
        cmd = [str(py), str(script_path), *[str(a) for a in (args or [])]]
        creationflags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                         if sys.platform.startswith("win") else 0)
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(cwd) if cwd else None,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=creationflags,
            )
        except OSError as e:
            raise OrcaRunError(f"Failed to launch MLIP interpreter: {e}") from e
        with self._lock:
            self._proc = proc

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # The read loop blocks in readline, so a cancel/detach while the worker
        # is SILENT (model download, a long optimizer step) would otherwise be
        # acted on only when the next stdout line arrives — possibly minutes
        # later, and past shutdown's bounded wait. This watcher terminates the
        # process on the signal itself; the read loop then unblocks at EOF and
        # the post-loop event checks raise the right exception.
        watcher_stop = threading.Event()

        def _watch() -> None:
            while not watcher_stop.wait(0.3):
                if self._cancel.is_set() or self._detach.is_set():
                    self._terminate(proc)
                    return

        watcher = threading.Thread(target=_watch, daemon=True)
        watcher.start()
        try:
            with open(output_path, "w", encoding="utf-8", errors="replace") as outf:
                for line in (proc.stdout or ()):
                    line = line.rstrip("\r\n")
                    outf.write(line + "\n")
                    outf.flush()
                    if on_line is not None:
                        on_line(line)
                    if self._cancel.is_set():
                        self._terminate(proc)
                        raise OrcaCancelled("Cancelled by user.")
                    if self._detach.is_set():
                        self._terminate(proc)
                        raise OrcaDetached("MLIP run terminated on shutdown.")
        finally:
            watcher_stop.set()
            try:
                if proc.stdout:
                    proc.stdout.close()
            except OSError:
                pass

        rc = proc.wait()
        # a signal that arrived exactly as stdout closed
        if self._cancel.is_set():
            raise OrcaCancelled("Cancelled by user.")
        if self._detach.is_set():
            raise OrcaDetached("MLIP run terminated on shutdown.")
        return rc

    def _terminate(self, proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            try:
                proc.kill()
            except OSError:
                pass
