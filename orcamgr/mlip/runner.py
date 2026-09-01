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
import threading
from pathlib import Path
from typing import Callable, Optional

from ..core.procutil import no_window_flags
from ..core.runner import OrcaRunError, OrcaCancelled, OrcaDetached
from ..core.xyzutil import as_xyz_file
from ..textio import decode_process_output


LogCallback = Callable[[str], None]

# Optimizer defaults. fmax in eV/Å. DEFAULT_DEVICE "" means auto — the worker
# picks CUDA when the user's torch build sees a GPU, else CPU (only the worker's
# own env can answer that, so the resolution lives there, not here).
DEFAULT_FMAX = 0.05
DEFAULT_MAX_STEPS = 500
DEFAULT_DEVICE = ""


# The worker script, run by the USER's interpreter (so it may import torch/mace/
# ase, which ORCAdesk's own env need not have). It is parameter-free — all inputs
# come from a JSON config file passed as argv[1] — so nothing is string-formatted
# into it (no code injection from model names / paths). Kept as a module-level
# constant so tests can swap in a stdlib-only stub.
MACE_WORKER_SCRIPT = r'''
import sys, json, os, shutil, traceback

def _cap_threads(n):
    # A CPU MLIP job is charged `nprocs` cores by the queue's admission control
    # (core/resources.py), so it must actually stay inside that: torch and the
    # BLAS underneath it default to EVERY core, which would silently blow the
    # budget the moment anything runs alongside. The env vars have to be set
    # before torch is imported, hence: here, first thing.
    try:
        n = int(n)
    except (TypeError, ValueError):
        return
    if n < 1:
        return
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                "NUMEXPR_NUM_THREADS"):
        os.environ[var] = str(n)
    try:
        import torch
        torch.set_num_threads(n)
    except Exception:
        pass


def _resolve_device(requested):
    # "" (or anything not cpu/cuda) means auto: use CUDA when the user's torch
    # build sees a GPU, else fall back to CPU. Only the worker's own env can
    # answer this, so the resolution happens here, never on the ORCAdesk side.
    dev = str(requested or "").strip().lower()
    if dev in ("cpu", "cuda"):
        return dev
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"

def _geometry_type(atoms):
    # IdealGasThermo needs to know how many trans/rot modes to drop: monatomic
    # (0 vib), linear (3N-5), or nonlinear (3N-6). Decide from the moments of
    # inertia (one ~0 principal moment => linear).
    n = len(atoms)
    if n == 1:
        return "monatomic"
    if n == 2:
        return "linear"
    import numpy as np
    moments = np.sort(np.abs(atoms.get_moments_of_inertia()))
    if moments[-1] > 0 and moments[0] < 1e-3 * moments[-1]:
        return "linear"
    return "nonlinear"

def _vibrational_analysis(atoms, geometry, cfg, result):
    # Finite-difference Hessian via ASE Vibrations, then ideal-gas thermochem.
    # The 3N raw modes include ~5-6 near-zero translation/rotation modes; we drop
    # the ones closest to zero (by |frequency|) so a genuine imaginary mode (a
    # large-magnitude one, e.g. a saddle point) is KEPT, never sliced off with
    # the trans/rot block the way ASE's positional slice would.
    import numpy as np
    from ase.vibrations import Vibrations
    n = len(atoms)
    ndrop = {"monatomic": 3 * n, "linear": 5, "nonlinear": 6}[geometry]
    vibdir = "vib"
    shutil.rmtree(vibdir, ignore_errors=True)   # stale cache -> wrong Hessian
    vib = Vibrations(atoms, name=vibdir)
    print("[mlip] frequencies: " + str(6 * n) + " force evaluations on "
          + str(n) + " atoms...", flush=True)
    vib.run()
    energies = np.asarray(vib.get_energies())        # complex eV, ascending |.|
    freqs = np.asarray(vib.get_frequencies())        # complex cm^-1
    order = np.argsort(np.abs(freqs))                 # near-zero (trans/rot) first
    keep = order[ndrop:] if ndrop < len(order) else np.array([], dtype=int)
    keep = keep[np.argsort(np.abs(freqs[keep]))]      # report ascending |freq|
    signed = []
    kept_energies = []
    n_imag = 0
    for i in keep:
        f = freqs[i]
        if abs(f.imag) > 1e-6:
            signed.append(-abs(float(f.imag)))
            n_imag += 1
        else:
            signed.append(float(f.real))
        kept_energies.append(energies[i])
    result["has_frequencies"] = True
    result["frequencies"] = signed
    result["n_imaginary"] = n_imag
    result["temperature_k"] = float(cfg.get("temperature", 298.15))
    result["pressure_atm"] = float(cfg.get("pressure", 1.0))
    # ZPE from the real modes only (imaginary contribute no zero-point energy)
    zpe = 0.5 * sum(float(e.real) for e in kept_energies if abs(e.imag) <= 1e-6)
    result["zpe_ev"] = zpe
    print("[mlip] " + str(len(signed)) + " modes, " + str(n_imag)
          + " imaginary; ZPE=" + repr(zpe) + " eV", flush=True)
    if n_imag == 0 and geometry != "monatomic":
        try:
            from ase.thermochemistry import IdealGasThermo
            T = result["temperature_k"]
            P_pa = result["pressure_atm"] * 101325.0
            spin = max(0.0, (int(cfg.get("multiplicity", 1)) - 1) / 2.0)
            thermo = IdealGasThermo(
                vib_energies=kept_energies, geometry=geometry,
                potentialenergy=result["energy_ev"], atoms=atoms,
                symmetrynumber=1, spin=spin, ignore_imag_modes=True)
            H = float(thermo.get_enthalpy(T, verbose=False))
            S = float(thermo.get_entropy(T, P_pa, verbose=False))
            G = float(thermo.get_gibbs_energy(T, P_pa, verbose=False))
            kB = 8.617333262e-5   # eV/K; ideal gas H = U + kB*T (per molecule)
            result["enthalpy_ev"] = H
            result["gibbs_ev"] = G
            result["entropy_term_ev"] = T * S
            result["internal_energy_ev"] = H - kB * T
            print("[mlip] thermochem @" + str(T) + "K: H=" + repr(H)
                  + " G=" + repr(G) + " eV (symmetry number 1 assumed)", flush=True)
        except Exception as te:
            # thermochem is a bonus; a failure here must not lose the geometry
            # or the frequencies. Report the reason and carry on.
            print("[mlip] thermochemistry skipped: " + repr(te), flush=True)

def main():
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        cfg = json.load(f)
    task = cfg.get("task", "opt")
    # before ANY torch import below (see _cap_threads)
    _cap_threads(cfg.get("threads"))
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
        device = _resolve_device(cfg.get("device"))
        head = cfg.get("head") or ""
        print("[mlip] loading " + str(cfg["model"]) + " (device=" + device + ")"
              + (" head=" + head if head else "")
              + (" threads=" + str(cfg.get("threads")) if cfg.get("threads") else ""),
              flush=True)
        load_kwargs = {"model": cfg["model_arg"], "device": device, "default_dtype": "float64"}
        # `head` selects a multi-head model's head and is a mace_mp-only kwarg;
        # mace_off/mace_omol don't accept it, so only pass it for mace_mp.
        if head and cfg["family"] == "mace_mp":
            load_kwargs["head"] = head
        calc = load_mace(**load_kwargs)
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
        do_opt = task in ("opt", "opt_freq")
        do_freq = task in ("freq", "opt_freq")
        if do_opt:
            from ase.optimize import LBFGS
            print("[mlip] optimizing " + str(len(atoms)) + " atoms, fmax=" + str(cfg["fmax"]), flush=True)
            opt = LBFGS(atoms, logfile="-")
            result["converged"] = bool(opt.run(fmax=cfg["fmax"], steps=cfg["max_steps"]))
            result["n_steps"] = int(opt.get_number_of_steps())
        else:
            # sp / freq: no relaxation, so there is nothing to converge
            result["converged"] = True
            result["n_steps"] = 0
        energy = float(atoms.get_potential_energy())
        result["energy_ev"] = energy
        import numpy as _np
        result["fmax"] = (float(_np.linalg.norm(atoms.get_forces(), axis=1).max())
                          if len(atoms) else 0.0)
        write(cfg["output_xyz"], atoms)
        syms = atoms.get_chemical_symbols()
        pos = atoms.get_positions()
        result["geometry"] = [[syms[i], float(pos[i][0]), float(pos[i][1]), float(pos[i][2])]
                              for i in range(len(atoms))]
        print("[mlip] energy=" + repr(energy) + " eV  fmax=" + repr(result["fmax"]) + " eV/A", flush=True)
        if do_freq:
            _vibrational_analysis(atoms, _geometry_type(atoms), cfg, result)
        print("[mlip] done.", flush=True)
    except Exception as e:
        result["error"] = type(e).__name__ + ": " + str(e)
        traceback.print_exc()
    with open(cfg["result_json"], "w", encoding="utf-8") as f:
        json.dump(result, f)

main()
'''


# Special models whose loader/model-arg can't be expressed as a size heuristic:
# a label containing the key maps to (loader family, model= argument, head).
# Checked before the small/medium/large heuristic. mace_omol is the dedicated
# OMol25 model; mh-0/mh-1 are the multi-head models and load through mace_mp.
# `head` is the multi-head selector passed to mace_mp(head=...) — "" means the
# model's own default head (omat_pbe for mh-1). "MACE-MH-1 omol" selects the
# omol head (wB97M-VV10, organic/organometallic; the best mh-1 head for
# molecular / host-guest energetics — S30L). Order matters: the more specific
# "mh-1 omol" must precede the plain "omol" and "mh-1" keys, since a label like
# "MACE-MH-1 omol" contains all three as substrings and the first hit wins.
_SPECIAL_MODELS = {
    "mh-1 omol": ("mace_mp", "mh-1", "omol"),
    "omol": ("mace_omol", "extra_large", ""),
    "mh-1": ("mace_mp", "mh-1", ""),
    "mh-0": ("mace_mp", "mh-0", ""),
}


def parse_mace_model(model: str) -> tuple[str, str, str]:
    """Map a dropdown label to (family, model_arg, head) for the worker. family
    is one of {'mace_off','mace_mp','mace_omol'} (the loader function); model_arg
    is passed verbatim to that loader (a size 'small'/'medium'/'large', or a
    named model like 'extra_large'/'mh-1'); head is the mace_mp multi-head
    selector ("" = the model's default head; only mace_mp accepts it).
    'MACE-OFF medium' -> ('mace_off','medium',''), 'MACE-OMOL extra-large' ->
    ('mace_omol','extra_large',''), 'MACE-MH-1' -> ('mace_mp','mh-1',''),
    'MACE-MH-1 omol' -> ('mace_mp','mh-1','omol'). Unknown labels default to
    MACE-OFF medium."""
    m = (model or "").strip().lower()
    for key, fam_arg_head in _SPECIAL_MODELS.items():
        if key in m:
            return fam_arg_head
    family = "mace_mp" if "mp" in m else "mace_off"
    size = next((s for s in ("small", "medium", "large") if s in m), "medium")
    return family, size, ""


def write_mlip_run_files(calc_dir, name: str, model: str, xyz: str, result_json,
                         charge: int = 0, multiplicity: int = 1,
                         task: str = "opt", device: str = "",
                         temperature: float = 298.15,
                         pressure: float = 1.0,
                         threads: int = 0) -> tuple[Path, Path]:
    """Write the input .xyz, the JSON config, and the worker script into the run
    folder. Returns (script_path, config_path) for MlipRunner.run(). charge and
    multiplicity are passed to the worker for charge/spin-aware models (OMol25 /
    multi-head); MACE-OFF/MP ignore them. task is one of "opt" (LBFGS relaxation),
    "sp" (single-point energy), "freq" (vibrational analysis + thermochemistry at
    the given geometry) or "opt_freq" (relax, then frequencies); an unknown value
    falls back to "opt". device is "" (auto: CUDA when the worker's env sees a GPU,
    else CPU), "cpu", or "cuda" — resolved inside the worker. temperature (K) and
    pressure (atm) drive the ideal-gas thermochemistry for the freq tasks. threads
    caps the worker's CPU threads (0 = leave torch's default): the queue charges
    a CPU MLIP job that many cores, so the worker has to keep to them."""
    calc_dir = Path(calc_dir)
    calc_dir.mkdir(parents=True, exist_ok=True)
    input_xyz = calc_dir / f"{name}.xyz"
    output_xyz = calc_dir / f"{name}.opt.xyz"
    config_path = calc_dir / "mlip_config.json"
    script_path = calc_dir / "mlip_opt.py"

    input_xyz.write_text(as_xyz_file(xyz), encoding="utf-8")
    family, model_arg, head = parse_mace_model(model)
    task = str(task).lower()
    if task not in ("opt", "sp", "freq", "opt_freq"):
        task = "opt"
    device = str(device).lower()
    if device not in ("cpu", "cuda"):
        device = DEFAULT_DEVICE   # "" = auto (resolved in the worker)
    cfg = {
        "model": model or "MACE-OFF medium",
        "family": family, "model_arg": model_arg, "head": head, "device": device,
        "charge": int(charge), "multiplicity": int(multiplicity),
        "task": task,
        "temperature": float(temperature), "pressure": float(pressure),
        "threads": max(0, int(threads or 0)),
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
        try:
            # Binary pipe, decoded per line (see orcamgr/textio): the worker is
            # a CPython child in the USER's environment, and on Windows it
            # encodes stdout with the locale ANSI code page. Its traceback is
            # the whole diagnosis when a run fails, and it is written to the
            # calculation's .out as well as the live log — so mis-decoding it
            # persisted the corruption.
            proc = subprocess.Popen(
                cmd, cwd=str(cwd) if cwd else None,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=no_window_flags(),
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

        watcher = threading.Thread(target=_watch, daemon=True,
                                   name="orcadesk-mlip-watch")
        watcher.start()
        try:
            with open(output_path, "w", encoding="utf-8", errors="replace") as outf:
                for raw_line in (proc.stdout or ()):
                    line = decode_process_output(raw_line).rstrip("\r\n")
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
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass
