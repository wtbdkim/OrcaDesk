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
from orcamgr.mlip.env import resolve_interpreter
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


def _recording_callbacks():
    logs: list = []
    updates: list = []
    cb = QueueCallbacks(
        log=lambda msg, level: logs.append((level, msg)),
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
    ("MACE-OFF small", ("mace_off", "small")),
    ("MACE-OFF medium", ("mace_off", "medium")),
    ("MACE-OFF large", ("mace_off", "large")),
    ("MACE-MP small", ("mace_mp", "small")),
    ("MACE-MP-0 medium", ("mace_mp", "medium")),
    ("mace-mp large", ("mace_mp", "large")),
    # OMol25 models: a dedicated loader / named model arg, not a size
    ("MACE-OMOL extra-large", ("mace_omol", "extra_large")),
    ("mace-omol", ("mace_omol", "extra_large")),
    ("MACE-MH-1", ("mace_mp", "mh-1")),
    ("MACE-MH-0", ("mace_mp", "mh-0")),
])
def test_parse_mace_model_maps_family_and_model_arg(label, expected):
    assert parse_mace_model(label) == expected


@pytest.mark.parametrize("label", ["", None, "SomeUnknownModel", "MACE-OFF"])
def test_parse_mace_model_unknown_label_falls_back_to_off_medium(label):
    assert parse_mace_model(label) == ("mace_off", "medium")


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
    assert cfg["charge"] == -1
    assert cfg["multiplicity"] == 2   # worker sets atoms.info["spin"] = multiplicity (2S+1), i.e. 2 = doublet
    # defaults when omitted (backward-compatible neutral singlet)
    _, cp2 = write_mlip_run_files(tmp_path, "j2", "MACE-OFF medium",
                                  "H 0 0 0", tmp_path / "j2.json")
    cfg2 = _json.loads(cp2.read_text(encoding="utf-8"))
    assert cfg2["charge"] == 0 and cfg2["multiplicity"] == 1
    assert cfg2["family"] == "mace_off" and cfg2["model_arg"] == "medium"


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
        stdout = "42\n"
        stderr = ""

    monkeypatch.setattr(env_mod.subprocess, "run",
                        lambda *a, **k: _Proc())
    res = env_mod.probe_env(str(fake_exe))
    assert res["ready"] is False
    assert res["error"]
