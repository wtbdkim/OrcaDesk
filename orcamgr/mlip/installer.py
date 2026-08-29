"""
Create an MLIP environment (venv + PyTorch + a backend) with one click.

Unlike CREST -- a statically linked binary that installs by download+extract
(``crest/installer.py``) -- an MLIP is a **Python toolchain**, so there is
nothing to unpack: an interpreter has to build a venv and pip has to resolve
wheels into it. Two consequences shape this module:

* **A base interpreter is the one manual prerequisite** (the analogue of CREST's
  "a WSL distro must exist"). A frozen ORCAdesk has no Python of its own --
  ``sys.executable`` is ``ORCAdesk.exe``, which cannot run ``-m venv`` -- so the
  base Python is detected on the machine (``find_base_pythons``) and only picked
  by hand when detection finds nothing.
* **The device is an explicit choice, never guessed.** The CPU torch wheel is
  ~120 MB; the CUDA build bundles the CUDA runtime and is ~2.5 GB. Silently
  downloading either one would be dishonest about what the click costs.

Everything here is Qt-free and streams its output through a callback, so the
Bridge can run it on a background thread and the UI can poll -- the same shape
as the MLIP run pipeline (``mlip/runner.py``).
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from .env import MLIP_BACKENDS

# Where pip fetches torch from. Pinning the index (rather than letting plain
# `pip install torch` decide) is what makes the CPU/GPU choice real: PyPI's
# Windows wheel is CPU-only, so a GPU env MUST come from PyTorch's own index.
_INDEX_ROOT = "https://download.pytorch.org/whl"
TORCH_INDEX = {"cpu": f"{_INDEX_ROOT}/cpu"}

# A torch wheel only carries kernels for the GPU architectures its CUDA
# toolkit knew about. Pick one too old and the failure is nasty rather than
# obvious: the wheel installs, imports, and *reports the GPU*, then dies at the
# first kernel launch with `CUDA error: no kernel image is available for
# execution on the device`. Observed exactly that on an RTX 5080 (Blackwell,
# sm_120) against cu124, whose kernels stop at sm_90.
#
# So the index is chosen from the GPU's own compute capability, read from
# nvidia-smi before anything is downloaded. Highest matching entry wins.
# Verified present on download.pytorch.org (cp312/win_amd64 wheels): cu126,
# cu128, cu129, cu130.
CUDA_INDEX_BY_CAPABILITY = (
    (12.0, "cu128"),   # Blackwell (RTX 50-series) — needs CUDA >= 12.8
    (0.0, "cu126"),    # everything older that current torch still builds for
)
DEFAULT_CUDA_INDEX = "cu128"

# pip requirement per backend key. Keys mirror MLIP_BACKENDS in env.py -- the
# probe imports `package`, this installs `requirement`; they are different
# strings for the same backend (import `mace`, install `mace-torch`).
BACKEND_REQUIREMENTS = {
    "mace": ["mace-torch"],
    "sevennet": ["sevenn"],
}

# The CPython window torch publishes wheels for. A moving target -- verified
# against torch's current wheel set, not guessed -- and only a pre-flight
# courtesy: picking an unsupported base Python otherwise fails deep inside pip
# with "no matching distribution", hundreds of lines in.
MIN_PY = (3, 9)
MAX_PY = (3, 14)

# Windows' default path limit, and how much of it torch spends by itself.
# MEASURED, not guessed (P3): a real `torch 2.13.0+cpu` install writes 38,469
# files whose deepest path *relative to the env root* is 189 characters --
# vendored licence texts nested through
# kineto/libkineto/dynolog/prometheus-cpp/civetweb/duktape. So the env
# directory itself only gets 260 - 1 - 189 = 70 characters, and
# `%APPDATA%\ORCAdesk\mlip_envs` already spends ~51 of them: with the default
# Windows setting an environment NAME longer than ~18 characters overflows.
# The failure lands at the very end of `pip install torch` -- after the whole
# download -- as an opaque `OSError: [Errno 2]`, which is exactly why this is
# checked up front instead of being left to fail.
MAX_PATH = 260
DEEPEST_PACKAGE_PATH = 189


def long_paths_enabled() -> bool:
    """Whether Windows' MAX_PATH limit has been lifted machine-wide. Anything
    that is not Windows has no such limit, so True there."""
    if not sys.platform.startswith("win"):
        return True
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\FileSystem") as k:
            return bool(winreg.QueryValueEx(k, "LongPathsEnabled")[0])
    except (OSError, ImportError, ValueError):
        return False


def path_budget_error(env_dir) -> str:
    """"" if a full install fits under the path limit, else why it will not --
    naming the number of characters to cut, because the only fix the user has
    is a shorter environment name (the envs root is not theirs to move)."""
    if long_paths_enabled():
        return ""
    room = MAX_PATH - 1 - DEEPEST_PACKAGE_PATH
    over = len(str(Path(env_dir))) - room
    if over <= 0:
        return ""
    return (f"The path would be {over} character(s) too long for Windows: "
            f"PyTorch nests files up to {DEEPEST_PACKAGE_PATH} characters deep "
            f"and this environment would sit at '{env_dir}'. Use a name "
            f"{over} character(s) shorter, or enable Win32 long paths "
            f"(Group Policy: Enable Win32 long paths).")


def _no_window_flags() -> int:
    return (getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if sys.platform.startswith("win") else 0)


def python_version(path: str, timeout: float = 15.0) -> tuple:
    """(major, minor) of the interpreter at ``path``, or () if it won't report
    one. Never raises: a bad path is an empty answer, not an exception (P6)."""
    try:
        p = subprocess.run([str(path), "-c",
                            "import sys;print('%d.%d' % sys.version_info[:2])"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout,
                           creationflags=_no_window_flags())
    except (OSError, subprocess.SubprocessError):
        return ()
    m = re.search(r"(\d+)\.(\d+)", (p.stdout or "") + (p.stderr or ""))
    return (int(m.group(1)), int(m.group(2))) if m else ()


def is_supported_python(version: tuple) -> bool:
    """Whether torch publishes wheels for this CPython."""
    return bool(version) and MIN_PY <= version <= MAX_PY


def find_base_pythons() -> list[dict]:
    """Interpreters on this machine usable as a venv base, best first:
    [{python, version, supported}]. Deduplicated by resolved path.

    A frozen ORCAdesk contributes nothing here -- its `sys.executable` is the
    app exe, which cannot create a venv -- so detection is entirely external
    (the `py` launcher, then PATH). Running from source, the interpreter
    running ORCAdesk is a perfectly good base and is included.
    """
    cands: list[str] = []
    if sys.platform.startswith("win"):
        try:  # the py launcher knows every registered install
            p = subprocess.run(["py", "-0p"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=15,
                               creationflags=_no_window_flags())
            for line in (p.stdout or "").splitlines():
                m = re.search(r"([A-Za-z]:\\[^\r\n]*?python\.exe)", line)
                if m:
                    cands.append(m.group(1))
        except (OSError, subprocess.SubprocessError):
            pass
    if not getattr(sys, "frozen", False):
        cands.append(sys.executable)
    exe = "python.exe" if sys.platform.startswith("win") else "python3"
    try:
        which = "where" if sys.platform.startswith("win") else "which"
        p = subprocess.run([which, exe], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=15,
                           creationflags=_no_window_flags())
        cands += [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()]
    except (OSError, subprocess.SubprocessError):
        pass

    out: list[dict] = []
    seen = set()
    for c in cands:
        try:
            rp = str(Path(c).resolve())
        except OSError:
            continue
        # A Windows Store stub resolves to a 0-byte alias that hangs on launch;
        # the existence check plus the version probe below filters those out.
        if rp.lower() in seen or not Path(rp).exists():
            continue
        seen.add(rp.lower())
        ver = python_version(rp)
        if not ver:
            continue
        out.append({"python": rp, "version": "%d.%d" % ver,
                    "supported": is_supported_python(ver)})
    # supported first, then newest -- so the best default sits at index 0
    out.sort(key=lambda e: (not e["supported"],
                            [-int(x) for x in e["version"].split(".")]))
    return out


def detect_gpu() -> dict:
    """The NVIDIA GPU this machine would compute on: {name, capability}.
    ``capability`` is the CUDA compute capability as a float (12.0 for
    Blackwell), 0.0 when there is no GPU or nvidia-smi cannot say. Never
    raises -- a machine with no NVIDIA driver simply has no GPU."""
    blank = {"name": "", "capability": 0.0}
    try:
        p = subprocess.run(["nvidia-smi", "--query-gpu=name,compute_cap",
                            "--format=csv,noheader"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=20,
                           creationflags=_no_window_flags())
    except (OSError, subprocess.SubprocessError):
        return blank
    if p.returncode != 0:
        return blank
    line = next((l for l in (p.stdout or "").splitlines() if l.strip()), "")
    if not line:
        return blank
    name, _, cap = line.partition(",")
    try:
        capability = float(cap.strip())
    except ValueError:
        capability = 0.0
    return {"name": name.strip(), "capability": capability}


def has_nvidia_gpu() -> bool:
    """Whether an NVIDIA GPU is visible to the driver. Used to default the
    device choice and to warn before a multi-GB CUDA download on a machine that
    cannot use it -- never to silently override the user's pick."""
    return bool(detect_gpu()["name"])


def cuda_index_for(capability: float) -> str:
    """The CUDA wheel index whose kernels cover ``capability``."""
    for minimum, index in CUDA_INDEX_BY_CAPABILITY:
        if capability >= minimum:
            return index
    return DEFAULT_CUDA_INDEX


def torch_index(device: str, capability: float = 0.0) -> str:
    """The pip index URL torch must come from for this device."""
    if (device or "cpu") != "cuda":
        return TORCH_INDEX["cpu"]
    return f"{_INDEX_ROOT}/{cuda_index_for(capability)}"


def venv_python(env_dir) -> Path:
    """The interpreter inside a venv at ``env_dir`` (platform layout)."""
    d = Path(env_dir)
    return (d / "Scripts" / "python.exe" if sys.platform.startswith("win")
            else d / "bin" / "python")


def install_plan(base_python: str, env_dir, backend: str = "mace",
                 device: str = "cpu", capability: float = 0.0) -> list[dict]:
    """The ordered steps of an install: [{label, argv}].

    Torch is installed **before** the backend and from the device's own index:
    `mace-torch` would otherwise pull whatever plain torch pip resolves first,
    quietly turning a GPU install into a CPU one. For a GPU env the index is
    chosen from the card's compute ``capability`` -- see
    CUDA_INDEX_BY_CAPABILITY for why a too-old one fails only at run time.
    """
    py = venv_python(env_dir)
    index = torch_index(device, capability)
    reqs = BACKEND_REQUIREMENTS.get(backend, BACKEND_REQUIREMENTS["mace"])
    label = MLIP_BACKENDS.get(backend, {}).get("label", backend)
    gpu = (device == "cuda")
    # The progress bar is a carriage-return animation: left on, a 2.5 GB
    # download alone writes thousands of lines into the shared log ring and
    # evicts everything else in it.
    pip = [str(py), "-m", "pip", "install",
           "--progress-bar", "off", "--disable-pip-version-check"]
    return [
        {"label": "Creating the environment",
         "argv": [str(base_python), "-m", "venv", str(env_dir)]},
        {"label": "Updating pip",
         "argv": [*pip, "--upgrade", "pip"]},
        {"label": ("Downloading PyTorch (GPU/CUDA, ~2.5 GB)" if gpu
                   else "Downloading PyTorch (CPU, ~150 MB)"),
         "argv": [*pip, "torch", "--index-url", index]},
        {"label": f"Installing {label}",
         "argv": [*pip, *reqs]},
    ]


class MlipEnvInstaller:
    """Runs an install plan, streaming each command's output. Cancellable from
    another thread (the UI's), like MlipRunner: the signal terminates the child
    rather than waiting for its next line -- pip is silent for minutes at a time
    while a 2.5 GB wheel downloads."""

    def __init__(self):
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()
        with self._lock:
            proc = self._proc
        if proc is not None:
            self._terminate(proc)

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
        except OSError:
            pass

    def _stream(self, argv: list, on_line: Callable[[str], None]) -> int:
        try:
            proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=_no_window_flags())
        except OSError as e:
            on_line(f"Failed to launch: {e}")
            return 127
        with self._lock:
            self._proc = proc
        # A cancel that landed between the check above and the handle being
        # published would otherwise be lost, leaving pip running unattended.
        if self._cancel.is_set():
            self._terminate(proc)
        try:
            for line in proc.stdout or ():
                line = line.rstrip()
                if line:
                    on_line(line)
            return proc.wait()
        finally:
            with self._lock:
                self._proc = None

    def run(self, base_python: str, env_dir, backend: str = "mace",
            device: str = "cpu", capability: Optional[float] = None,
            on_line: Optional[Callable[[str], None]] = None,
            on_step: Optional[Callable[[int, int, str], None]] = None) -> dict:
        """Create the env and install the toolchain. Returns
        {ok, python, error, cancelled}. Blocking -- run it off the UI thread."""
        say = on_line or (lambda _s: None)
        env_dir = Path(env_dir)
        if not base_python or not Path(base_python).exists():
            return {"ok": False, "python": "", "cancelled": False,
                    "error": "No base Python interpreter to build the "
                             "environment with. Pick one in Settings -> MLIP."}
        ver = python_version(base_python)
        if not is_supported_python(ver):
            shown = "%d.%d" % ver if ver else "unknown"
            lo = "%d.%d" % MIN_PY
            hi = "%d.%d" % MAX_PY
            return {"ok": False, "python": "", "cancelled": False,
                    "error": (f"Python {shown} is outside the range PyTorch "
                              f"publishes wheels for ({lo}-{hi}). "
                              "Pick another interpreter.")}
        if env_dir.exists() and any(env_dir.iterdir()):
            return {"ok": False, "python": "", "cancelled": False,
                    "error": f"'{env_dir.name}' already exists. "
                             "Choose another name."}
        # Before the download, not after it: this failure otherwise arrives
        # minutes in (2.5 GB in, for a GPU env) as an opaque pip OSError.
        too_long = path_budget_error(env_dir)
        if too_long:
            return {"ok": False, "python": "", "cancelled": False,
                    "error": too_long}
        try:
            env_dir.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {"ok": False, "python": "", "cancelled": False,
                    "error": f"Could not create {env_dir.parent}: {e}"}

        # Ask the card what it is before choosing a wheel: a cu124 wheel on a
        # Blackwell GPU installs, imports, reports the GPU, then dies at the
        # first kernel launch. Detected here rather than passed in so a caller
        # that forgets still gets the right build.
        if capability is None:
            capability = detect_gpu()["capability"] if device == "cuda" else 0.0
        if device == "cuda":
            say(f"--- target GPU compute capability {capability or 'unknown'} "
                f"-> {cuda_index_for(capability)} wheels ---")
        steps = install_plan(base_python, env_dir, backend, device, capability)
        for i, step in enumerate(steps, 1):
            if self._cancel.is_set():
                return {"ok": False, "python": "", "cancelled": True,
                        "error": "Cancelled."}
            if on_step:
                on_step(i, len(steps), step["label"])
            say(f"--- {step['label']} ---")
            rc = self._stream(step["argv"], say)
            if self._cancel.is_set():
                return {"ok": False, "python": "", "cancelled": True,
                        "error": "Cancelled."}
            if rc != 0:
                return {"ok": False, "python": "", "cancelled": False,
                        "error": f"{step['label']} failed (exit {rc}). "
                                 "See the log for pip's own message."}
        py = venv_python(env_dir)
        if not py.exists():
            return {"ok": False, "python": "", "cancelled": False,
                    "error": "The environment was built but its interpreter "
                             f"is missing at {py}."}
        return {"ok": True, "python": str(py), "error": "", "cancelled": False}
