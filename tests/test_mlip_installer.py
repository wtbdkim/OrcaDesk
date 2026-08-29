"""
One-click MLIP environment creation (``orcamgr/mlip/installer.py``).

The module is Qt-free and shells out, so everything here either exercises pure
logic (the install plan, the version gate, base-Python ranking) or drives the
streaming/cancel machinery against a *real* child process -- a tiny Python
script rather than pip, so the tests stay fast and need no network.
"""

from __future__ import annotations

import sys
import threading
import time

from orcamgr.mlip.installer import (
    BACKEND_REQUIREMENTS, CUDA_INDEX_BY_CAPABILITY, DEEPEST_PACKAGE_PATH,
    DEFAULT_CUDA_INDEX, MAX_PATH, MAX_PY, MIN_PY, TORCH_INDEX,
    MlipEnvInstaller, cuda_index_for, install_plan, is_supported_python,
    path_budget_error, python_version, torch_index, venv_python,
)
import orcamgr.mlip.installer as installer_mod


# ---------------------------------------------------------------------------
# the install plan
# ---------------------------------------------------------------------------

def test_cpu_and_gpu_plans_differ_only_in_the_torch_index(tmp_path):
    cpu = install_plan("py.exe", tmp_path / "e", "mace", "cpu")
    gpu = install_plan("py.exe", tmp_path / "e", "mace", "cuda", capability=12.0)
    assert TORCH_INDEX["cpu"] in cpu[2]["argv"]
    assert torch_index("cuda", 12.0) in gpu[2]["argv"]
    # everything else about the two plans is identical
    assert [s["argv"] for s in cpu[:2]] == [s["argv"] for s in gpu[:2]]
    assert cpu[3]["argv"] == gpu[3]["argv"]


# ---------------------------------------------------------------------------
# picking the CUDA wheel for the actual card
# ---------------------------------------------------------------------------
# A torch wheel only ships kernels for the architectures its CUDA toolkit knew.
# Too old a pick is a LATE failure: it installs, imports, reports the GPU, and
# then dies at the first kernel launch with "no kernel image is available for
# execution on the device" -- observed on an RTX 5080 (sm_120) against cu124.

def test_blackwell_gets_a_cuda_12_8_or_newer_wheel():
    # The regression this exists for: RTX 50-series reports capability 12.0 and
    # has no kernels in anything below cu128.
    idx = cuda_index_for(12.0)
    assert int(idx.removeprefix("cu")) >= 128, idx


def test_older_cards_get_an_older_but_still_published_index():
    assert cuda_index_for(8.9) == "cu126"     # Ada
    assert cuda_index_for(7.5) == "cu126"     # Turing


def test_an_unknown_capability_still_yields_a_usable_index():
    # nvidia-smi missing or unparseable -> 0.0; must not produce "" or crash.
    assert cuda_index_for(0.0).startswith("cu")


def test_every_mapped_index_is_one_pytorch_publishes():
    # Guards a typo in the table: these were verified present on
    # download.pytorch.org with cp312/win_amd64 wheels.
    published = {"cu126", "cu128", "cu129", "cu130"}
    assert {idx for _cap, idx in CUDA_INDEX_BY_CAPABILITY} <= published
    assert DEFAULT_CUDA_INDEX in published


def test_the_capability_table_is_ordered_highest_first():
    caps = [c for c, _ in CUDA_INDEX_BY_CAPABILITY]
    assert caps == sorted(caps, reverse=True), "first match wins, so order matters"


def test_a_cpu_device_never_reaches_a_cuda_index():
    assert torch_index("cpu", 12.0) == TORCH_INDEX["cpu"]
    assert torch_index("", 12.0) == TORCH_INDEX["cpu"]


def test_detect_gpu_never_raises_and_always_answers_the_same_shape(monkeypatch):
    import subprocess as sp
    def boom(*a, **k):
        raise OSError("nvidia-smi missing")
    monkeypatch.setattr(installer_mod.subprocess, "run", boom)
    g = installer_mod.detect_gpu()
    assert g == {"name": "", "capability": 0.0}


def test_detect_gpu_parses_the_nvidia_smi_line(monkeypatch):
    class P:
        returncode = 0
        stdout = "NVIDIA GeForce RTX 5080, 12.0\n"
        stderr = ""
    monkeypatch.setattr(installer_mod.subprocess, "run", lambda *a, **k: P())
    assert installer_mod.detect_gpu() == {"name": "NVIDIA GeForce RTX 5080",
                                          "capability": 12.0}


def test_torch_is_installed_before_the_backend_and_from_a_pinned_index(tmp_path):
    # The whole point of the device choice: `mace-torch` would otherwise pull
    # whatever plain torch pip resolves, silently making a GPU install CPU-only.
    plan = install_plan("py.exe", tmp_path / "e", "mace", "cuda")
    labels = [s["label"] for s in plan]
    torch_at = next(i for i, s in enumerate(plan) if "torch" in s["argv"])
    mace_at = next(i for i, s in enumerate(plan)
                   if any("mace" in a for a in s["argv"]))
    assert torch_at < mace_at, labels
    assert "--index-url" in plan[torch_at]["argv"]


def test_the_plan_builds_the_venv_with_the_base_python_then_uses_its_own(tmp_path):
    env = tmp_path / "e"
    plan = install_plan("C:/base/python.exe", env, "mace", "cpu")
    assert plan[0]["argv"][:3] == ["C:/base/python.exe", "-m", "venv"]
    inner = str(venv_python(env))
    assert all(s["argv"][0] == inner for s in plan[1:])


def test_pip_steps_silence_the_progress_bar(tmp_path):
    # Left on, a 2.5 GB download writes thousands of carriage-return lines into
    # the shared log ring and evicts everything else in it.
    for step in install_plan("py.exe", tmp_path / "e", "mace", "cuda")[1:]:
        assert "--progress-bar" in step["argv"]
        assert step["argv"][step["argv"].index("--progress-bar") + 1] == "off"


def test_an_unknown_backend_falls_back_to_mace_rather_than_crashing(tmp_path):
    plan = install_plan("py.exe", tmp_path / "e", "nonesuch", "cpu")
    assert BACKEND_REQUIREMENTS["mace"][0] in plan[3]["argv"]


def test_every_known_backend_has_a_pip_requirement():
    from orcamgr.mlip.env import MLIP_BACKENDS
    assert set(MLIP_BACKENDS) <= set(BACKEND_REQUIREMENTS)


# ---------------------------------------------------------------------------
# the pre-flight gates
# ---------------------------------------------------------------------------

def test_version_gate_accepts_the_window_torch_publishes_for():
    assert is_supported_python(MIN_PY) and is_supported_python(MAX_PY)
    assert not is_supported_python((MIN_PY[0], MIN_PY[1] - 1))
    assert not is_supported_python((MAX_PY[0], MAX_PY[1] + 1))
    assert not is_supported_python(())        # unreadable interpreter


def test_python_version_reads_a_real_interpreter_and_never_raises():
    assert python_version(sys.executable) == sys.version_info[:2]
    assert python_version("no-such-interpreter-anywhere") == ()


def test_a_missing_base_python_fails_before_anything_is_created(tmp_path):
    env = tmp_path / "env"
    res = MlipEnvInstaller().run("no-such-python.exe", env)
    assert res["ok"] is False and res["error"]
    assert not env.exists(), "nothing may be created when the base is unusable"


def test_a_non_empty_target_is_refused_rather_than_overwritten(tmp_path):
    env = tmp_path / "env"
    env.mkdir()
    (env / "keep.txt").write_text("existing")
    res = MlipEnvInstaller().run(sys.executable, env)
    assert res["ok"] is False and "already exists" in res["error"]
    assert (env / "keep.txt").read_text() == "existing"


# ---------------------------------------------------------------------------
# the Windows path-length gate
# ---------------------------------------------------------------------------
# Measured from a real install: torch ships files 189 chars deep relative to
# the env root, so with Windows' default MAX_PATH the env dir gets ~70 chars.
# The overflow surfaces at the END of `pip install torch` -- after the whole
# download -- as an opaque OSError, hence a pre-flight check.

def test_a_short_path_is_accepted(monkeypatch):
    monkeypatch.setattr(installer_mod, "long_paths_enabled", lambda: False)
    assert path_budget_error("C:\\o\\envs\\mace") == ""


def test_an_overlong_path_is_refused_and_says_how_much_to_cut(monkeypatch):
    monkeypatch.setattr(installer_mod, "long_paths_enabled", lambda: False)
    room = MAX_PATH - 1 - DEEPEST_PACKAGE_PATH
    err = path_budget_error("x" * (room + 5))
    assert err and "5 character(s)" in err


def test_the_boundary_is_exactly_the_measured_budget(monkeypatch):
    monkeypatch.setattr(installer_mod, "long_paths_enabled", lambda: False)
    room = MAX_PATH - 1 - DEEPEST_PACKAGE_PATH
    assert path_budget_error("x" * room) == ""
    assert path_budget_error("x" * (room + 1)) != ""


def test_the_gate_lifts_when_windows_long_paths_are_enabled(monkeypatch):
    monkeypatch.setattr(installer_mod, "long_paths_enabled", lambda: True)
    assert path_budget_error("C:\\" + "x" * 400) == ""


def test_run_refuses_an_overlong_target_before_downloading_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(installer_mod, "long_paths_enabled", lambda: False)
    env = tmp_path / ("e" * 250)
    res = MlipEnvInstaller().run(sys.executable, env)
    assert res["ok"] is False and "too long" in res["error"]
    assert not env.exists()


# ---------------------------------------------------------------------------
# streaming + cancel (real child processes, no network)
# ---------------------------------------------------------------------------

def test_output_is_streamed_line_by_line(tmp_path):
    lines = []
    inst = MlipEnvInstaller()
    rc = inst._stream([sys.executable, "-c",
                       "print('one');print('two');print('three')"], lines.append)
    assert rc == 0
    assert lines == ["one", "two", "three"]


def test_a_nonzero_child_is_reported_not_raised(tmp_path):
    inst = MlipEnvInstaller()
    assert inst._stream([sys.executable, "-c", "import sys;sys.exit(3)"],
                        lambda _l: None) == 3


def test_an_unlaunchable_command_is_data_not_an_exception():
    lines = []
    rc = MlipEnvInstaller()._stream(["no-such-binary-at-all"], lines.append)
    assert rc == 127 and lines


def test_cancel_terminates_a_child_that_is_silent(tmp_path):
    # pip is silent for minutes while a 2.5 GB wheel downloads, so cancel must
    # not wait for the next output line.
    inst = MlipEnvInstaller()
    threading.Timer(0.4, inst.cancel).start()
    started = time.monotonic()
    inst._stream([sys.executable, "-c", "import time;time.sleep(30)"],
                 lambda _l: None)
    assert time.monotonic() - started < 15, "cancel did not reach the child"


def test_a_cancel_before_the_first_step_stops_the_run(tmp_path, monkeypatch):
    # pytest's own tmp_path is already past the 70-char budget, so neutralise
    # the path gate: this test is about cancel, not about MAX_PATH.
    monkeypatch.setattr(installer_mod, "long_paths_enabled", lambda: True)
    inst = MlipEnvInstaller()
    inst.cancel()
    res = inst.run(sys.executable, tmp_path / "env")
    assert res["cancelled"] is True and res["ok"] is False
