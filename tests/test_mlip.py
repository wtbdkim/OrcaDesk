"""
Tests for the MLIP pipeline (orcamgr/mlip/): result parsing, the runner with a
stdlib-only stub worker (the CI substitute for a real MACE env promised in
CLAUDE.md), the QueueEngine integration, model-label heuristics, and
interpreter resolution.

Contract references (PRINCIPLES.md):
  P6  — failure is data: a "successful" result without a usable geometry is
        demoted to a failure instead of silently handing off an empty structure.
  P25 — success is judged per-kind: an mlip_opt must converge or it is FAILED.
  P36 — the worker is parameter-free JSON-I/O, which is exactly what lets these
        tests swap in a stdlib-only stub via the module-level script constant.

No ORCA executable, no MACE environment, and no network are needed; the only
subprocess spawned is sys.executable running the stub worker.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import orcamgr.mlip.runner as mlip_runner_mod
from orcamgr.core.input_generator import StepConfig
from orcamgr.core.queue import (
    Calculation, CalcState, GeometrySource, QueueCallbacks, QueueEngine,
    validate_result,
)
from orcamgr.core.runner import OrcaRunError
from orcamgr.mlip.env import order_envs_by_readiness, resolve_interpreter
from orcamgr.mlip.parser import parse_mlip_result
from orcamgr.mlip.runner import MlipRunner, parse_mace_model, write_mlip_run_files


# 1 Hartree in eV (CODATA) — mirrors the constant the parser uses so the tests
# assert the *conversion*, not just "some float came back".
EV_PER_HARTREE = 27.211386245988

# A bare coordinate block, as the Build tab supplies it (no xyz header).
WATER_XYZ = (
    "O 0.000000 0.000000 0.117300\n"
    "H 0.000000 0.757200 -0.469200\n"
    "H 0.000000 -0.757200 -0.469200"
)

# Stdlib-only stand-in for MACE_WORKER_SCRIPT. Same contract as the real
# worker (P36): read the JSON config from argv[1], echo the input geometry as
# the "optimized" one, write the output .xyz and the result JSON, log to stdout.
_STUB_WORKER_TEMPLATE = r'''
import json, sys

def main():
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        cfg = json.load(f)
    print("[stub] loading " + str(cfg.get("model", "")), flush=True)
    with open(cfg["input_xyz"], "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    atoms = []
    for ln in lines[2:]:
        parts = ln.split()
        if len(parts) >= 4:
            atoms.append([parts[0], float(parts[1]), float(parts[2]), float(parts[3])])
    with open(cfg["output_xyz"], "w", encoding="utf-8") as f:
        f.write(str(len(atoms)) + "\nstub optimized\n")
        for a in atoms:
            f.write("{0} {1:.6f} {2:.6f} {3:.6f}\n".format(a[0], a[1], a[2], a[3]))
    result = {"converged": __CONVERGED__, "energy_ev": -27.211386245988,
              "n_steps": 3, "model": cfg.get("model", ""),
              "geometry": atoms, "error": None}
    with open(cfg["result_json"], "w", encoding="utf-8") as f:
        json.dump(result, f)
    print("[stub] done", flush=True)

main()
'''
STUB_WORKER_CONVERGED = _STUB_WORKER_TEMPLATE.replace("__CONVERGED__", "True")
STUB_WORKER_UNCONVERGED = _STUB_WORKER_TEMPLATE.replace("__CONVERGED__", "False")

# Stdlib-only stand-in for a freq/opt_freq worker: same JSON-I/O contract, but it
# also emits a frequency block + thermochemistry (no ASE/torch needed). __IMAG__
# controls the number of imaginary (negative) modes so a test can drive the
# per-kind validation (a true minimum needs zero).
_STUB_FREQ_TEMPLATE = r'''
import json, sys

def main():
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        cfg = json.load(f)
    with open(cfg["input_xyz"], "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    atoms = []
    for ln in lines[2:]:
        parts = ln.split()
        if len(parts) >= 4:
            atoms.append([parts[0], float(parts[1]), float(parts[2]), float(parts[3])])
    with open(cfg["output_xyz"], "w", encoding="utf-8") as f:
        f.write(str(len(atoms)) + "\nstub freq\n")
        for a in atoms:
            f.write("{0} {1:.6f} {2:.6f} {3:.6f}\n".format(a[0], a[1], a[2], a[3]))
    imag = __IMAG__
    freqs = [-200.0 * (i + 1) for i in range(imag)] + [1500.0, 3000.0]
    result = {"task": cfg.get("task", "opt_freq"), "converged": True,
              "energy_ev": -27.211386245988, "n_steps": 2, "model": cfg.get("model", ""),
              "geometry": atoms, "error": None,
              "has_frequencies": True, "frequencies": freqs, "n_imaginary": imag,
              "zpe_ev": 0.1, "gibbs_ev": -1.2, "enthalpy_ev": -1.1,
              "entropy_term_ev": 0.03, "internal_energy_ev": -1.15,
              "temperature_k": cfg.get("temperature", 298.15),
              "pressure_atm": cfg.get("pressure", 1.0)}
    with open(cfg["result_json"], "w", encoding="utf-8") as f:
        json.dump(result, f)
    print("[stub] freq done", flush=True)

main()
'''
STUB_FREQ_MINIMUM = _STUB_FREQ_TEMPLATE.replace("__IMAG__", "0")
STUB_FREQ_SADDLE = _STUB_FREQ_TEMPLATE.replace("__IMAG__", "1")


def _recording_callbacks():
    logs: list = []
    updates: list = []
    cb = QueueCallbacks(
        log=lambda msg, level, _calc="": logs.append((level, msg)),
        calc_update=lambda i, c: updates.append((i, c.state)),
    )
    return cb, logs, updates


def _mlip_calc(name: str = "mlip_job", xyz: str = WATER_XYZ) -> Calculation:
    cfg = StepConfig(kind="mlip_opt", mlip_model="MACE-OFF medium")
    return Calculation(name=name, kind="mlip_opt", config=cfg, xyz=xyz)


def _stub_env() -> list:
    return [{"id": "env1", "name": "stub", "python": sys.executable}]


# ---------------------------------------------------------------------------
# 1. parse_mlip_result
# ---------------------------------------------------------------------------

def test_parse_mlip_result_converts_ev_to_hartree_and_reads_geometry(tmp_path):
    result_json = tmp_path / "job.mlip.json"
    result_json.write_text(json.dumps({
        "converged": True,
        "energy_ev": -2.0 * EV_PER_HARTREE,
        "n_steps": 5,
        "geometry": [["O", 0.0, 0.0, 0.1], ["H", 0.0, 0.75, -0.4]],
        "error": None,
    }), encoding="utf-8")

    r = parse_mlip_result(str(result_json))

    assert r.terminated_normally is True
    assert r.is_optimization is True
    assert r.opt_converged is True
    assert r.final_energy_eh == pytest.approx(-2.0)
    assert r.n_atoms == 2
    assert [a.symbol for a in r.geometry] == ["O", "H"]
    assert r.geometry[1].y == pytest.approx(0.75)


def test_sp_task_result_is_not_an_optimization_and_needs_no_convergence(tmp_path):
    # a single-point (mlip_sp) result: terminated fine, carries an energy +
    # geometry, but is_optimization is False so validation doesn't require
    # convergence (an SP has nothing to converge).
    result_json = tmp_path / "sp.mlip.json"
    result_json.write_text(json.dumps({
        "task": "sp", "converged": True, "energy_ev": -3.0 * EV_PER_HARTREE,
        "n_steps": 0, "fmax": 0.42,
        "geometry": [["O", 0.0, 0.0, 0.1], ["H", 0.0, 0.75, -0.4]],
        "error": None,
    }), encoding="utf-8")
    r = parse_mlip_result(str(result_json))
    assert r.terminated_normally is True
    assert r.is_optimization is False
    assert r.final_energy_eh == pytest.approx(-3.0)
    # validate_result must accept it even with opt_converged False (not checked for SP)
    r.opt_converged = False
    calc = Calculation(name="sp1", kind="mlip_sp",
                       config=StepConfig(kind="mlip_sp", mlip_model="MACE-OFF medium"))
    validate_result(calc, r)   # does not raise


def test_write_mlip_run_files_encodes_the_task(tmp_path):
    import json as _json
    # separate run folders: write_mlip_run_files uses a fixed 'mlip_config.json' name
    _, cp_sp = write_mlip_run_files(tmp_path / "sp", "sp", "MACE-OFF medium",
                                    "H 0 0 0", tmp_path / "sp.json", task="sp")
    _, cp_opt = write_mlip_run_files(tmp_path / "op", "op", "MACE-OFF medium",
                                     "H 0 0 0", tmp_path / "op.json")
    assert _json.loads(cp_sp.read_text(encoding="utf-8"))["task"] == "sp"
    assert _json.loads(cp_opt.read_text(encoding="utf-8"))["task"] == "opt"


def test_write_mlip_run_files_encodes_freq_tasks_device_and_thermo(tmp_path):
    import json as _json
    _, cp_freq = write_mlip_run_files(
        tmp_path / "fq", "fq", "MACE-OFF medium", "H 0 0 0",
        tmp_path / "fq.json", task="opt_freq", device="cuda",
        temperature=310.0, pressure=2.0)
    cfg = _json.loads(cp_freq.read_text(encoding="utf-8"))
    assert cfg["task"] == "opt_freq"
    assert cfg["device"] == "cuda"
    assert cfg["temperature"] == 310.0 and cfg["pressure"] == 2.0
    # an unknown task falls back to opt; an unknown device falls back to "" (auto)
    _, cp_bad = write_mlip_run_files(
        tmp_path / "bad", "bad", "MACE-OFF medium", "H 0 0 0",
        tmp_path / "bad.json", task="nonsense", device="tpu")
    bad = _json.loads(cp_bad.read_text(encoding="utf-8"))
    assert bad["task"] == "opt"
    assert bad["device"] == ""   # auto (resolved in the worker)


def test_write_mlip_run_files_caps_the_worker_threads(tmp_path):
    # The queue charges a CPU MLIP job `nprocs` cores (core/resources.py), so
    # the worker has to keep to them: torch and the BLAS under it default to
    # every core, which would blow the budget as soon as anything runs
    # alongside. The cap travels in the config the worker reads.
    import json as _json
    _, cp = write_mlip_run_files(
        tmp_path / "t", "t", "MACE-OFF medium", "H 0 0 0",
        tmp_path / "t.json", threads=4)
    assert _json.loads(cp.read_text(encoding="utf-8"))["threads"] == 4
    # unset stays 0 = leave torch's own default alone
    _, cp0 = write_mlip_run_files(
        tmp_path / "t0", "t0", "MACE-OFF medium", "H 0 0 0", tmp_path / "t0.json")
    assert _json.loads(cp0.read_text(encoding="utf-8"))["threads"] == 0


def test_worker_sets_the_thread_env_before_importing_torch(tmp_path):
    # _cap_threads must run before any torch import: OMP_NUM_THREADS is only
    # read at import time, so a later call would silently do nothing.
    from orcamgr.mlip.runner import MACE_WORKER_SCRIPT
    body = MACE_WORKER_SCRIPT
    assert "OMP_NUM_THREADS" in body
    assert body.index("_cap_threads(cfg.get(\"threads\"))") < body.index("from ase.io import")


def test_parse_mlip_result_reads_frequencies_and_thermochemistry(tmp_path):
    # a freq/opt_freq worker result: frequencies (cm^-1, negative = imaginary)
    # plus ideal-gas thermochemistry in eV -> converted to Hartree in ParseResult.
    result_json = tmp_path / "freq.mlip.json"
    result_json.write_text(json.dumps({
        "task": "opt_freq", "converged": True,
        "energy_ev": -5.0 * EV_PER_HARTREE, "n_steps": 7,
        "has_frequencies": True,
        "frequencies": [1200.0, 1600.0, 3700.0],
        "n_imaginary": 0,
        "zpe_ev": 0.5 * EV_PER_HARTREE,
        "gibbs_ev": -4.0 * EV_PER_HARTREE,
        "enthalpy_ev": -4.5 * EV_PER_HARTREE,
        "entropy_term_ev": 0.02 * EV_PER_HARTREE,
        "internal_energy_ev": -4.6 * EV_PER_HARTREE,
        "temperature_k": 298.15, "pressure_atm": 1.0,
        "geometry": [["O", 0.0, 0.0, 0.1], ["H", 0.0, 0.75, -0.4]],
        "error": None,
    }), encoding="utf-8")

    r = parse_mlip_result(str(result_json))

    assert r.terminated_normally is True
    assert r.is_optimization is True         # opt_freq relaxes first
    assert r.has_frequencies is True
    assert r.frequencies == [1200.0, 1600.0, 3700.0]
    assert r.n_imaginary == 0
    assert r.zpe_eh == pytest.approx(0.5)
    assert r.gibbs_eh == pytest.approx(-4.0)
    assert r.enthalpy_eh == pytest.approx(-4.5)
    assert r.entropy_term_eh == pytest.approx(0.02)
    assert r.total_thermal_eh == pytest.approx(-4.6)
    assert r.temperature_k == pytest.approx(298.15)
    assert r.pressure_atm == pytest.approx(1.0)


def test_parse_mlip_freq_task_is_not_an_optimization(tmp_path):
    # a bare freq (no opt) leaves the geometry as given: is_optimization False,
    # so validation applies the imaginary-mode rule, not a convergence check.
    result_json = tmp_path / "freqonly.mlip.json"
    result_json.write_text(json.dumps({
        "task": "freq", "converged": True, "energy_ev": -1.0,
        "has_frequencies": True, "frequencies": [-450.0, 1200.0],
        "n_imaginary": 1,
        "geometry": [["H", 0.0, 0.0, 0.0], ["H", 0.0, 0.0, 0.74]],
        "error": None,
    }), encoding="utf-8")
    r = parse_mlip_result(str(result_json))
    assert r.is_optimization is False
    assert r.has_frequencies is True
    assert r.n_imaginary == 1


def test_success_json_without_geometry_is_demoted_to_failure(tmp_path):
    # P6: a worker that claims success but returns no structure must not hand
    # an empty geometry to a downstream reference — it is a failure.
    result_json = tmp_path / "job.mlip.json"
    result_json.write_text(json.dumps({
        "converged": True, "energy_ev": -1.0, "geometry": [], "error": None,
    }), encoding="utf-8")

    r = parse_mlip_result(str(result_json))

    assert r.terminated_normally is False
    assert r.error_message  # a reason is always given
    assert r.geometry == []


def test_missing_result_file_yields_error_message_without_raising(tmp_path):
    r = parse_mlip_result(str(tmp_path / "nope" / "missing.json"))
    assert r.terminated_normally is False
    assert r.error_message


def test_corrupt_result_json_yields_error_message_without_raising(tmp_path):
    result_json = tmp_path / "broken.json"
    result_json.write_text("{{{ this is not json", encoding="utf-8")
    r = parse_mlip_result(str(result_json))
    assert r.terminated_normally is False
    assert r.error_message


def test_worker_reported_error_passes_through_as_failure(tmp_path):
    result_json = tmp_path / "err.json"
    result_json.write_text(json.dumps({
        "converged": False, "energy_ev": None,
        "geometry": [], "error": "RuntimeError: boom",
    }), encoding="utf-8")
    r = parse_mlip_result(str(result_json))
    assert r.terminated_normally is False
    assert r.error_message == "RuntimeError: boom"


def test_malformed_geometry_rows_are_skipped_not_fatal(tmp_path):
    # tolerant-parser contract (P27 spirit): one bad row must not lose the rest
    result_json = tmp_path / "job.json"
    result_json.write_text(json.dumps({
        "converged": True, "energy_ev": -1.0,
        "geometry": [["O", 0.0, 0.0, 0.0], ["H", "bad", 0.0], ["H", 0.0, 0.0, 1.0]],
        "error": None,
    }), encoding="utf-8")
    r = parse_mlip_result(str(result_json))
    assert r.terminated_normally is True
    assert r.n_atoms == 2
    assert [a.symbol for a in r.geometry] == ["O", "H"]


# ---------------------------------------------------------------------------
# 2. MlipRunner with the stdlib-only stub worker
# ---------------------------------------------------------------------------

def test_mlip_runner_runs_stub_worker_end_to_end(tmp_path, monkeypatch):
    # Swap the module-level worker constant for the stdlib stub — the exact
    # substitution point the constant exists for (see runner.py's comment).
    monkeypatch.setattr(mlip_runner_mod, "MACE_WORKER_SCRIPT", STUB_WORKER_CONVERGED)

    calc_dir = tmp_path / "job"
    result_json = calc_dir / "job.mlip.json"
    script_path, config_path = write_mlip_run_files(
        calc_dir, "job", "MACE-OFF medium", WATER_XYZ, result_json)

    # write_mlip_run_files wrapped the bare block as a standard .xyz
    input_xyz = (calc_dir / "job.xyz").read_text(encoding="utf-8")
    assert input_xyz.splitlines()[0] == "3"

    out_path = calc_dir / "job.out"
    seen_lines: list = []
    runner = MlipRunner(sys.executable)
    rc = runner.run(script_path, [str(config_path)], out_path,
                    cwd=calc_dir, on_line=seen_lines.append)

    assert rc == 0
    # stdout was tailed into the .out AND streamed to the live-log callback
    out_text = out_path.read_text(encoding="utf-8")
    assert "[stub] loading MACE-OFF medium" in out_text
    assert "[stub] done" in out_text
    assert any("[stub] done" in ln for ln in seen_lines)
    # the worker wrote its result JSON and the optimized geometry
    assert result_json.exists()
    assert (calc_dir / "job.opt.xyz").exists()

    r = parse_mlip_result(str(result_json))
    assert r.terminated_normally is True
    assert r.opt_converged is True
    assert r.final_energy_eh == pytest.approx(-1.0)  # -27.211386... eV == -1 Eh
    assert r.n_atoms == 3


def test_mlip_runner_missing_interpreter_raises_orca_run_error(tmp_path):
    runner = MlipRunner(str(tmp_path / "no_such_env" / "python.exe"))
    with pytest.raises(OrcaRunError):
        runner.run(tmp_path / "script.py", [], tmp_path / "out.out")


# ---------------------------------------------------------------------------
# 3. QueueEngine integration (_run_mlip_calc via run_all)
# ---------------------------------------------------------------------------

def test_queue_engine_runs_mlip_calc_to_done_with_geometry_and_energy(tmp_path, monkeypatch):
    monkeypatch.setattr(mlip_runner_mod, "MACE_WORKER_SCRIPT", STUB_WORKER_CONVERGED)
    cb, logs, _updates = _recording_callbacks()
    engine = QueueEngine("", str(tmp_path), cb, mlip_envs=_stub_env())
    calc = _mlip_calc()

    engine.run_all([calc])

    assert calc.state == CalcState.DONE
    assert calc.message == "Completed."
    assert calc.result is not None
    assert calc.result.final_energy_eh == pytest.approx(-1.0)
    assert len(calc.result.geometry) == 3
    assert calc.output_path.endswith(".mlip.json")
    # the run left an inspectable folder: worker stdout tailed into the .out
    out_text = (tmp_path / "mlip_job" / "mlip_job.out").read_text(encoding="utf-8")
    assert "[stub] done" in out_text
    assert any(level == "ok" for level, _msg in logs)


def test_unconverged_mlip_opt_is_marked_failed(tmp_path, monkeypatch):
    # P25: success is judged per-kind — mlip_opt must converge, or it FAILED.
    monkeypatch.setattr(mlip_runner_mod, "MACE_WORKER_SCRIPT", STUB_WORKER_UNCONVERGED)
    cb, _logs, _updates = _recording_callbacks()
    engine = QueueEngine("", str(tmp_path), cb, mlip_envs=_stub_env())
    calc = _mlip_calc()

    engine.run_all([calc])

    assert calc.state == CalcState.FAILED
    assert "converge" in calc.message.lower()


def test_queue_engine_runs_mlip_opt_freq_to_done_with_frequencies(tmp_path, monkeypatch):
    # A minimum (zero imaginary modes) opt_freq runs to DONE and carries the
    # frequency + thermochemistry block into the shared ParseResult.
    monkeypatch.setattr(mlip_runner_mod, "MACE_WORKER_SCRIPT", STUB_FREQ_MINIMUM)
    cb, _logs, _updates = _recording_callbacks()
    engine = QueueEngine("", str(tmp_path), cb, mlip_envs=_stub_env())
    cfg = StepConfig(kind="mlip_opt_freq", mlip_model="MACE-OFF medium")
    calc = Calculation(name="of", kind="mlip_opt_freq", config=cfg, xyz=WATER_XYZ)

    engine.run_all([calc])

    assert calc.state == CalcState.DONE
    assert calc.result.has_frequencies is True
    assert calc.result.n_imaginary == 0
    assert calc.result.gibbs_eh is not None
    assert calc.result.zpe_eh is not None


def test_mlip_freq_with_imaginary_mode_is_marked_failed(tmp_path, monkeypatch):
    # P25 through the MLIP freq path: a freq result with an imaginary mode is not
    # a true minimum, so the calc is FAILED (mirrors ORCA freq validation).
    monkeypatch.setattr(mlip_runner_mod, "MACE_WORKER_SCRIPT", STUB_FREQ_SADDLE)
    cb, _logs, _updates = _recording_callbacks()
    engine = QueueEngine("", str(tmp_path), cb, mlip_envs=_stub_env())
    cfg = StepConfig(kind="mlip_freq", mlip_model="MACE-OFF medium")
    calc = Calculation(name="fq", kind="mlip_freq", config=cfg, xyz=WATER_XYZ)

    engine.run_all([calc])

    assert calc.state == CalcState.FAILED
    assert "imaginary" in calc.message.lower()


def test_mlip_calc_without_registered_env_fails_with_clear_reason(tmp_path):
    cb, _logs, _updates = _recording_callbacks()
    engine = QueueEngine("", str(tmp_path), cb, mlip_envs=[])
    calc = _mlip_calc()

    engine.run_all([calc])

    assert calc.state == CalcState.FAILED
    assert "MLIP environment" in calc.message


def test_mlip_failure_blocks_dependent_calc_not_whole_queue(tmp_path, monkeypatch):
    # P23 through the MLIP path: a failed mlip_opt blocks its dependent,
    # while an unrelated mlip_opt still runs to DONE.
    monkeypatch.setattr(mlip_runner_mod, "MACE_WORKER_SCRIPT", STUB_WORKER_UNCONVERGED)
    cb, _logs, _updates = _recording_callbacks()
    engine = QueueEngine("", str(tmp_path), cb, mlip_envs=_stub_env())

    parent = _mlip_calc(name="parent")
    child = _mlip_calc(name="child", xyz="")
    child.geometry_source = GeometrySource.REFERENCE
    child.ref_name = "parent"
    unrelated = _mlip_calc(name="unrelated")

    # parent fails (unconverged); make the unrelated one succeed by swapping
    # the worker mid-queue is not possible — instead assert the blocked/failed
    # states, and that the unrelated calc still reached a terminal own state.
    engine.run_all([parent, child, unrelated])

    assert parent.state == CalcState.FAILED
    assert child.state == CalcState.BLOCKED
    # unrelated is NOT blocked — it ran on its own merits (here: also fails
    # to converge, which is its own diagnosis, not "a dependency failed")
    assert unrelated.state == CalcState.FAILED
    assert "dependency" not in unrelated.message.lower()


# ---------------------------------------------------------------------------
# 4. parse_mace_model heuristics
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label, expected", [
    ("MACE-OFF small", ("mace_off", "small", "")),
    ("MACE-OFF medium", ("mace_off", "medium", "")),
    ("MACE-OFF large", ("mace_off", "large", "")),
    ("MACE-MP small", ("mace_mp", "small", "")),
    ("MACE-MP-0 medium", ("mace_mp", "medium", "")),
    ("mace-mp large", ("mace_mp", "large", "")),
    # OMol25 models: a dedicated loader / named model arg, not a size
    ("MACE-OMOL extra-large", ("mace_omol", "extra_large", "")),
    ("mace-omol", ("mace_omol", "extra_large", "")),
    ("MACE-MH-1", ("mace_mp", "mh-1", "")),
    ("MACE-MH-0", ("mace_mp", "mh-0", "")),
    # mh-1's omol head: mace_mp loader + head selector. The "mh-1 omol" key must
    # win over the plain "omol"/"mh-1" keys (all three are substrings here).
    ("MACE-MH-1 omol", ("mace_mp", "mh-1", "omol")),
    ("mace-mh-1 omol head", ("mace_mp", "mh-1", "omol")),
])
def test_parse_mace_model_maps_family_and_model_arg(label, expected):
    assert parse_mace_model(label) == expected


@pytest.mark.parametrize("label", ["", None, "SomeUnknownModel", "MACE-OFF"])
def test_parse_mace_model_unknown_label_falls_back_to_off_medium(label):
    assert parse_mace_model(label) == ("mace_off", "medium", "")


def test_write_mlip_run_files_records_model_arg_and_charge_spin(tmp_path):
    """The worker config carries the resolved loader + model arg and the calc's
    charge/multiplicity (OMol25 / multi-head models read them; MACE-OFF/MP don't)."""
    import json as _json
    _, config_path = write_mlip_run_files(
        tmp_path, "job", "MACE-OMOL extra-large",
        "H 0 0 0\nH 0 0 0.74", tmp_path / "job.mlip.json",
        charge=-1, multiplicity=2)
    cfg = _json.loads(config_path.read_text(encoding="utf-8"))
    assert cfg["family"] == "mace_omol"
    assert cfg["model_arg"] == "extra_large"
    assert cfg["head"] == ""   # mace_omol has no multi-head selector
    assert cfg["charge"] == -1
    assert cfg["multiplicity"] == 2   # worker sets atoms.info["spin"] = multiplicity (2S+1), i.e. 2 = doublet
    # defaults when omitted (backward-compatible neutral singlet)
    _, cp2 = write_mlip_run_files(tmp_path, "j2", "MACE-OFF medium",
                                  "H 0 0 0", tmp_path / "j2.json")
    cfg2 = _json.loads(cp2.read_text(encoding="utf-8"))
    assert cfg2["charge"] == 0 and cfg2["multiplicity"] == 1
    assert cfg2["family"] == "mace_off" and cfg2["model_arg"] == "medium"
    # mh-1's omol head: mace_mp loader + head='omol' recorded for the worker
    _, cp3 = write_mlip_run_files(tmp_path, "j3", "MACE-MH-1 omol",
                                  "H 0 0 0", tmp_path / "j3.json")
    cfg3 = _json.loads(cp3.read_text(encoding="utf-8"))
    assert cfg3["family"] == "mace_mp" and cfg3["model_arg"] == "mh-1"
    assert cfg3["head"] == "omol"


# ---------------------------------------------------------------------------
# 5. env.resolve_interpreter
# ---------------------------------------------------------------------------

def _make_fake_env(tmp_path: Path, rel_parts: tuple) -> tuple:
    env_dir = tmp_path / "fake_env"
    exe = env_dir.joinpath(*rel_parts)
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("", encoding="utf-8")
    return env_dir, exe


def test_resolve_interpreter_finds_python_inside_env_directory(tmp_path):
    if sys.platform.startswith("win"):
        env_dir, exe = _make_fake_env(tmp_path, ("python.exe",))
    else:
        env_dir, exe = _make_fake_env(tmp_path, ("bin", "python3"))
    assert resolve_interpreter(str(env_dir)) == str(exe)


def test_resolve_interpreter_falls_back_to_secondary_candidate(tmp_path):
    if sys.platform.startswith("win"):
        env_dir, exe = _make_fake_env(tmp_path, ("Scripts", "python.exe"))
    else:
        env_dir, exe = _make_fake_env(tmp_path, ("bin", "python"))
    assert resolve_interpreter(str(env_dir)) == str(exe)


def test_resolve_interpreter_returns_original_when_nothing_found(tmp_path):
    empty = tmp_path / "empty_env"
    empty.mkdir()
    # no interpreter inside -> hand back the original so the caller's
    # existence/probe checks produce a clear error, not a silent guess
    assert resolve_interpreter(str(empty)) == str(empty)


def test_resolve_interpreter_strips_quotes_and_passes_files_through(tmp_path):
    f = tmp_path / "python.exe"
    f.write_text("", encoding="utf-8")
    assert resolve_interpreter(f'"{f}"') == str(f)
    assert resolve_interpreter("") == ""


def test_probe_env_non_dict_json_is_error_not_a_crash(monkeypatch, tmp_path):
    # an interpreter whose last stdout line is valid JSON but not an object
    # (a wrapper script / sitecustomize printing last) crashed probe_env with
    # AttributeError — killing the bridge's probe worker thread silently and
    # pinning the env at "Checking…" forever
    import subprocess
    import orcamgr.mlip.env as env_mod

    fake_exe = tmp_path / "python.exe"
    fake_exe.write_text("")

    class _Proc:
        returncode = 0        # it RAN; what it printed is the problem
        stdout = "42\n"
        stderr = ""

    monkeypatch.setattr(env_mod.subprocess, "run",
                        lambda *a, **k: _Proc())
    res = env_mod.probe_env(str(fake_exe))
    assert res["ready"] is False
    assert res["error"]


# ---------------------------------------------------------------------------
# Which env an "" (auto) mlip_env_id resolves to
# ---------------------------------------------------------------------------
# The engine receives only {id, name, python} -- readiness is a live probe
# result held by the Bridge -- so the documented `mlip_env_id == "" -> first
# ready env` contract is expressed as an ORDER on that list. These pin the
# ordering; the engine's own "take envs[0]" is covered below.

def _envs(*ids):
    return [{"id": i, "name": i, "python": f"/py/{i}"} for i in ids]


def test_a_ready_env_outranks_an_earlier_broken_one():
    # The bug this pins: two registered envs, the FIRST one broken. The top-bar
    # pill reads "ready" off the second, so the card unlocks -- and every MLIP
    # calc used to route to the broken first env anyway.
    out = order_envs_by_readiness(_envs("broken", "good"),
                                  {"broken": "error", "good": "ready"})
    assert [e["id"] for e in out] == ["good", "broken"]


def test_registration_order_breaks_ties_so_one_env_is_untouched():
    envs = _envs("a", "b", "c")
    assert order_envs_by_readiness(envs, {k: "ready" for k in "abc"}) == envs
    assert order_envs_by_readiness(_envs("solo"), {"solo": "error"})[0]["id"] == "solo"


def test_an_unprobed_or_checking_env_outranks_a_failed_one_but_not_a_ready_one():
    # "checking"/unknown is unproven, not disproven: better than a confirmed
    # error, worse than a confirmed ready.
    out = order_envs_by_readiness(_envs("err", "unprobed", "checking", "ready"),
                                  {"err": "error", "checking": "checking",
                                   "ready": "ready"})  # "unprobed" absent on purpose
    assert [e["id"] for e in out] == ["ready", "unprobed", "checking", "err"]


def test_nothing_is_dropped_when_every_env_failed():
    # An all-error list must still reach the engine: it then fails with a real
    # interpreter path and a real message, not "no MLIP environment registered".
    out = order_envs_by_readiness(_envs("x", "y"), {"x": "error", "y": "error"})
    assert [e["id"] for e in out] == ["x", "y"]


def test_engine_runs_the_first_env_of_the_list_it_is_given(tmp_path):
    # The other half of the contract: the engine takes envs[0] for an auto
    # ("") mlip_env_id, so ordering the list is what decides the interpreter.
    good = tmp_path / "good.exe"; good.write_text("")
    broken = tmp_path / "broken.exe"; broken.write_text("")
    ordered = order_envs_by_readiness(
        [{"id": "b", "name": "b", "python": str(broken)},
         {"id": "g", "name": "g", "python": str(good)}],
        {"b": "error", "g": "ready"})
    engine = QueueEngine("", str(tmp_path), mlip_envs=ordered)
    calc = Calculation(name="m", kind="mlip_opt", xyz="H 0 0 0",
                       config=StepConfig(kind="mlip_opt", mlip_env_id=""))
    assert engine._resolve_mlip_python(calc) == str(good)


# ---------------------------------------------------------------------------
# Probing an environment that cannot start
# ---------------------------------------------------------------------------

def test_an_interpreter_that_fails_to_start_is_reported_as_such(monkeypatch, tmp_path):
    """A venv whose base Python was uninstalled prints "No Python at ..." on
    stderr and exits non-zero with empty stdout. Reading that as an empty
    package list produced the confidently wrong "Missing core packages: torch,
    ase" — sending the user off to pip install into an interpreter that cannot
    run at all."""
    import orcamgr.mlip.env as env_mod

    fake_exe = tmp_path / "python.exe"
    fake_exe.write_text("", encoding="utf-8")

    class _Proc:
        returncode = 103
        stdout = ""
        stderr = "No Python at 'C:\\gone\\python.exe'"

    monkeypatch.setattr(env_mod.subprocess, "run", lambda *a, **k: _Proc())
    probe = env_mod.probe_env(str(fake_exe))

    assert probe["ready"] is False
    assert "No Python at" in (probe["error"] or "")
    assert probe["error"]        # not None: the payload must not fall back to
                                 # "missing packages" for an env that never ran
