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
from pathlib import Path

import pytest

from orcamgr.mlip.installer import (
    BACKEND_REQUIREMENTS, CUDA_INDEX_BY_CAPABILITY, DEEPEST_PACKAGE_PATH,
    DEFAULT_CUDA_INDEX, MAX_PATH, MAX_PY, MIN_PY, TORCH_INDEX,
    MAX_PY as _MAX_PY, MlipEnvInstaller, _installed_backends, _is_half_built,
    _is_registered, MIN_PY, cuda_index_for, find_base_pythons, install_plan,
    is_supported_python, path_budget_error, python_version, torch_index,
    venv_python,
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
    err = path_budget_error("x" * (installer_mod.path_budget() + 5))
    assert err and "5 character(s)" in err


def test_the_boundary_is_exactly_the_measured_budget(monkeypatch):
    # asserted against the module's own budget, not a second copy of the
    # arithmetic — the copy is what let an off-by-one live in the real one
    monkeypatch.setattr(installer_mod, "long_paths_enabled", lambda: False)
    room = installer_mod.path_budget()
    assert path_budget_error("x" * room) == ""
    assert path_budget_error("x" * (room + 1)) != ""


def test_the_budget_leaves_room_for_the_separator_and_the_248_directory_cap():
    # A path is env_dir + SEPARATOR + the deepest package path, and it must fit
    # under BOTH limits: MAX_PATH (260, counting the NUL) for the file and 248
    # for creating the directories it sits in.
    room = installer_mod.path_budget()
    assert room + 1 + DEEPEST_PACKAGE_PATH < MAX_PATH
    assert room + 1 + DEEPEST_PACKAGE_PATH <= installer_mod._MAX_DIR_PATH


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


# ---------------------------------------------------------------------------
# leftovers from a failed create
# ---------------------------------------------------------------------------
# `python -m venv` links the interpreter BEFORE it installs pip, so a failure in
# between leaves a directory whose interpreter runs and whose environment is
# useless. Debian and Ubuntu reach that state on a stock machine: ensurepip is
# not in their standard library but in a separate python3.N-venv package.

def _site_packages(root):
    return (root / "Lib" / "site-packages" if sys.platform.startswith("win")
            else root / "lib" / "python3.99" / "site-packages")


def _fake_venv(root, *, backend: str = ""):
    """A venv-shaped directory, optionally with an MLIP backend installed.

    ``backend`` is the import name (``mace``); "" is what every failed create
    leaves behind, whichever step it died at.
    """
    root = Path(root)
    bindir = root / ("Scripts" if sys.platform.startswith("win") else "bin")
    bindir.mkdir(parents=True)
    (root / "include").mkdir()
    (root / "lib").mkdir()
    (root / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    venv_python(root).write_text("", encoding="utf-8")
    if backend:
        (_site_packages(root) / backend).mkdir(parents=True)
    return root


def test_a_create_that_died_before_pip_counts_as_half_built(tmp_path):
    """Debian and Ubuntu keep ensurepip in a separate package, and venv links
    bin/python before it fails on the missing module -- so the wreckage has a
    working interpreter."""
    env = _fake_venv(tmp_path / "MACE")
    assert _is_half_built(env) is True


def test_a_create_that_died_installing_the_backend_counts_as_half_built(tmp_path):
    """The step after: pip and torch are in, the backend never arrived. This is
    where a Python newer than the backend's wheels lands you -- 2.5 GB in."""
    env = _fake_venv(tmp_path / "MACE")
    (_site_packages(env) / "torch").mkdir(parents=True)
    (env / ("Scripts" if sys.platform.startswith("win") else "bin") / "pip").write_text(
        "", encoding="utf-8")
    assert _installed_backends(env) == set()
    assert _is_half_built(env) is True


def test_an_environment_holding_a_backend_is_never_ours_to_delete(tmp_path):
    """The guard for a lost registry: Settings degrades to defaults on corrupt
    JSON (P32), and that must not make a working env deletable."""
    env = _fake_venv(tmp_path / "MACE", backend="mace")
    assert _installed_backends(env) == {"mace"}
    assert _is_half_built(env, registered=False) is False


def test_a_registered_environment_is_never_ours_to_delete(tmp_path):
    env = _fake_venv(tmp_path / "MACE")          # no backend on disk at all
    assert _is_half_built(env, registered=True) is False


def test_registration_is_matched_without_resolving_the_interpreter(tmp_path):
    """Inside a venv bin/python is a symlink to the BASE interpreter, so
    following it walks out of the environment; every registered env then looked
    unregistered."""
    env = _fake_venv(tmp_path / "MACE")
    py = venv_python(env)
    if not sys.platform.startswith("win"):
        base = tmp_path / "elsewhere" / "python3.12"
        base.parent.mkdir()
        base.write_text("", encoding="utf-8")
        py.unlink()
        py.symlink_to(base)
    assert _is_registered(env, [str(py)]) is True
    assert _is_registered(env, ["/usr/bin/python3"]) is False
    assert _is_registered(env, []) is False


def test_a_pipless_leftover_stops_refusing_its_own_name(tmp_path, monkeypatch):
    """The regression: the create failed, the directory stayed, and because its
    interpreter existed it was read as a finished env — so "MACE", the name the
    card fills in, was refused for good, even once the missing package was
    installed."""
    env = _fake_venv(tmp_path / "MACE")
    # Stop the run right after the leftover is dealt with: what happens next is
    # a 2.5 GB download this test has no business starting.
    monkeypatch.setattr(installer_mod, "path_budget_error", lambda _d: "stopped here")
    res = MlipEnvInstaller().run(sys.executable, env)
    assert res["error"] == "stopped here", "the name must not be refused"
    assert not env.exists(), "the unusable leftover should have been removed"


def test_a_working_env_is_still_refused_by_name(tmp_path, monkeypatch):
    """The other side of the same decision: a complete environment is never
    deleted to make room for a new one of the same name."""
    env = _fake_venv(tmp_path / "MACE", backend="mace")
    monkeypatch.setattr(installer_mod, "path_budget_error", lambda _d: "stopped here")
    res = MlipEnvInstaller().run(sys.executable, env)
    assert res["ok"] is False and "already exists" in res["error"]
    assert venv_python(env).exists(), "a usable env must survive the refusal"


# ---------------------------------------------------------------------------
# finding a base interpreter
# ---------------------------------------------------------------------------
# `which python3` names one interpreter, the distribution's default. Windows
# never depended on that -- the py launcher enumerates every install -- but on
# POSIX it was the only question asked, so a second Python installed alongside
# was invisible. That is not an edge case: the newest Python is regularly ahead
# of the wheels a backend needs.

@pytest.mark.skipif(sys.platform.startswith("win"),
                    reason="the py launcher already enumerates installs there")
def test_versioned_interpreters_are_asked_for_by_name(monkeypatch):
    """A stub file cannot report a version, so it would be dropped by the probe
    that follows. What matters is that the names are asked for at all: before
    this, `python3` was the only one, and a 3.12 sitting beside the default
    could not be chosen however it had been installed."""
    asked = []

    def spy(name):
        asked.append(name)
        return sys.executable if name == "python3" else None

    monkeypatch.setattr(installer_mod.shutil, "which", spy)
    find_base_pythons()
    wanted = {f"3.{m}" for m in range(MIN_PY[1], _MAX_PY[1] + 1)}
    seen = {n.removeprefix("python") for n in asked if n.startswith("python3.")}
    assert wanted <= seen, f"not probed: {sorted(wanted - seen)}"


def test_the_running_interpreter_is_always_a_candidate():
    found = {e["python"] for e in find_base_pythons()}
    assert str(Path(sys.executable).resolve()) in found
