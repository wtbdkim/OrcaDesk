"""
Run a single CREST conformer search through WSL, DETACHED, and monitor it by
tailing its output file.

Windows has no native CREST binary, so ORCAdesk runs the Linux binary inside a
WSL distro. The design mirrors ``core/runner.OrcaRunner`` (not the simpler
``mlip`` runner), because a CREST run is long and MUST survive ORCAdesk closing:

* A generated ``run_crest.sh`` is launched with ``setsid`` inside WSL so it
  becomes a new session/process-group leader that is NOT torn down when the
  launching ``wsl.exe`` returns. (Verified: such a process keeps running for
  well beyond the WSL idle timeout after every ``wsl.exe`` exits.)
* The script runs CREST in an **ext4 scratch dir** (``~/.orcadesk/scratch/<name>``)
  — never on ``/mnt`` — because CREST's many-small-file I/O is pathologically slow
  over the 9P mount. It copies the input in and the ensemble results back to the
  Windows workspace folder, then writes a ``<name>.crest.rc`` marker with the exit
  code. So even if ORCAdesk is closed for the whole run, results still land in the
  Windows folder.
* CREST writes its stdout straight to the Windows ``<name>.out``; live log +
  progress come from TAILING that file, identical whether we launched it this
  session or are reattaching to one from a previous session.
* The script records its own Linux PID (``$$``) and start-time (``/proc`` field
  22) so ORCAdesk can persist ``(pid, create_time)`` and later check liveness /
  kill the whole process group across a restart — the WSL analogue of
  ``procutil.process_matches`` / ``kill_tree`` (psutil can't see inside WSL).

Reuses ``OrcaRunError`` / ``OrcaCancelled`` / ``OrcaDetached`` from
``core/runner.py`` so ``QueueEngine.run_all``'s existing except-handlers treat a
CREST job's cancel/shutdown/failure exactly like an ORCA job's.
"""

from __future__ import annotations

import shlex
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

from ..core.runner import OrcaRunError, OrcaCancelled, OrcaDetached
from .wsl import run_bash


LogCallback = Callable[[str], None]

_TAIL_POLL = 0.5        # seconds between .out re-reads
_LIVENESS_EVERY = 6.0   # seconds between WSL liveness checks (keep wsl.exe spawns rare)
_PID_WAIT = 10.0        # seconds to wait for the launched script to write its .pid
_WSL_CALL_TIMEOUT = 20.0

# Map the UI method label to CREST's tight-binding flag.
_METHOD_FLAGS = {
    "gfn2": "--gfn2",
    "gfnff": "--gfnff",
    "gfn0": "--gfn0",
    "gfn1": "--gfn1",
    "gfn2//gfnff": "--gfn2//gfnff",
}


def build_crest_argv(config, charge: int, multiplicity: int) -> list[str]:
    """Build CREST CLI flags (excluding the binary and the input filename) from a
    StepConfig + the calc's charge/multiplicity. CREST's --uhf is the number of
    unpaired electrons (multiplicity - 1), NOT the multiplicity."""
    method = (getattr(config, "crest_method", "") or "gfn2").strip().lower()
    flag = _METHOD_FLAGS.get(method)
    if flag is None:
        flag = method if method.startswith("-") else "--" + method
    argv = [flag]

    solvent = (getattr(config, "crest_solvent", "") or "").strip()
    if solvent:
        model = (getattr(config, "crest_solvent_model", "alpb") or "alpb").strip().lower()
        argv += ["--gbsa" if model == "gbsa" else "--alpb", solvent]

    try:
        ewin = float(getattr(config, "crest_ewin", 6.0) or 6.0)
        argv += ["--ewin", f"{ewin:g}"]
    except (TypeError, ValueError):
        pass

    try:
        threads = int(getattr(config, "crest_threads", 4) or 4)
        argv += ["-T", str(max(1, threads))]
    except (TypeError, ValueError):
        pass

    # --- advanced knobs (each emitted only when set to a non-default value) ---
    def _num(attr):
        try:
            return float(getattr(config, attr, 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    preset = (getattr(config, "crest_preset", "") or "").strip().lower()
    if preset in ("quick", "squick", "mquick"):
        argv.append("--" + preset)
    if getattr(config, "crest_nci", False):
        argv.append("--nci")

    mult = _num("crest_mdlen_mult")
    if mult > 0:
        argv += ["--mdlen", f"x{mult:g}"]     # CREST accepts a multiplier as "x<real>"
    tstep = _num("crest_tstep_fs")
    if tstep > 0:
        argv += ["--tstep", f"{tstep:g}"]
    tnmd = _num("crest_tnmd_k")
    if tnmd > 0:
        argv += ["--tnmd", f"{tnmd:g}"]
    mddump = _num("crest_mddump_fs")
    if mddump > 0:
        argv += ["--mddump", str(int(mddump))]
    vbdump = _num("crest_vbdump_ps")
    if vbdump > 0:
        argv += ["--vbdump", f"{vbdump:g}"]

    for attr, flag in (("crest_norotmd", "--norotmd"), ("crest_cbonds", "--cbonds"),
                       ("crest_subrmsd", "--subrmsd"), ("crest_cluster", "--cluster"),
                       ("crest_keepdir", "--keepdir")):
        if getattr(config, attr, False):
            argv.append(flag)

    argv += ["--chrg", str(int(charge))]
    argv += ["--uhf", str(max(0, int(multiplicity) - 1))]
    return argv


def _as_xyz_file(coords: str) -> str:
    """Wrap a bare 'El x y z' coordinate block as a standard .xyz (count header +
    comment + body). If it already starts with a lone integer, assume it's a full
    .xyz and return as-is. (Local copy to keep crest/ independent of core/queue.)"""
    lines = [ln for ln in coords.strip().splitlines() if ln.strip()]
    if lines and lines[0].split() and lines[0].split()[0].isdigit() and len(lines[0].split()) == 1:
        return coords.strip() + "\n"
    n = sum(1 for ln in lines if len(ln.split()) >= 4)
    return f"{n}\ngenerated by ORCAdesk\n" + "\n".join(lines) + "\n"


def write_crest_run_files(calc_dir, name: str, xyz: str, crest_bin: str,
                          argv_flags: list[str]) -> Path:
    """Write the input ``{name}.xyz`` and the self-contained ``run_crest.sh`` into
    the (Windows) calc folder. Returns the run_crest.sh path. The script is
    written with LF line endings (bash chokes on CRLF)."""
    calc_dir = Path(calc_dir)
    calc_dir.mkdir(parents=True, exist_ok=True)
    (calc_dir / f"{name}.xyz").write_text(_as_xyz_file(xyz), encoding="utf-8", newline="\n")

    win_dir = str(calc_dir)                       # e.g. D:\workspace\name  (backslashes)
    flags = " ".join(shlex.quote(a) for a in argv_flags)
    script = (
        "#!/bin/bash\n"
        "set -u\n"
        f"WIN_DIR={shlex.quote(win_dir)}\n"
        f"NAME={shlex.quote(name)}\n"
        f"CRESTBIN={shlex.quote(crest_bin)}\n"
        'MNT="$(wslpath -u "$WIN_DIR")"\n'
        # record our own real PID (== session/group leader after setsid) + start
        # time, for liveness / group-kill / reattach across an app restart.
        'echo "$$" > "$MNT/$NAME.crest.pid"\n'
        "awk '{print $22}' /proc/$$/stat > \"$MNT/$NAME.crest.start\" 2>/dev/null || true\n"
        'SCRATCH="$HOME/.orcadesk/scratch/$NAME"\n'
        'rm -rf "$SCRATCH"; mkdir -p "$SCRATCH" || { echo "ORCAdesk: could not create WSL scratch dir" > "$MNT/$NAME.out"; echo 96 > "$MNT/$NAME.crest.rc"; exit 96; }\n'
        'cp "$MNT/$NAME.xyz" "$SCRATCH/input.xyz" || { echo "ORCAdesk: could not copy input .xyz into WSL scratch" > "$MNT/$NAME.out"; echo 95 > "$MNT/$NAME.crest.rc"; exit 95; }\n'
        'cd "$SCRATCH" || { echo 94 > "$MNT/$NAME.crest.rc"; exit 94; }\n'
        f'"$CRESTBIN" input.xyz {flags} > "$MNT/$NAME.out" 2>&1\n'
        "rc=$?\n"
        'for f in crest_conformers.xyz crest_best.xyz crest.energies crest_rotamers.xyz; do\n'
        '  [ -f "$f" ] && cp "$f" "$MNT/" 2>/dev/null\n'
        "done\n"
        'echo "$rc" > "$MNT/$NAME.crest.rc"\n'
        'cd "$HOME" 2>/dev/null && rm -rf "$SCRATCH"\n'
        "exit $rc\n"
    )
    script_path = calc_dir / "run_crest.sh"
    script_path.write_text(script, encoding="utf-8", newline="\n")
    return script_path


class CrestRunner:
    """Launches / reattaches to / monitors a CREST run in WSL for one calc."""

    def __init__(self, distro: str, crest_bin: str):
        self.distro = distro
        self.crest_bin = crest_bin
        self._lock = threading.Lock()
        self._pid: Optional[int] = None
        self._start_time: Optional[int] = None   # /proc/<pid>/stat field 22
        self._cancel_event = threading.Event()
        self._detach_event = threading.Event()

    # ---- control signals (UI/server thread) ----
    def cancel(self) -> None:
        self._cancel_event.set()

    def detach(self) -> None:
        self._detach_event.set()

    # ---- launch / reattach (worker thread) ----
    def launch(self, script_path: Path, name: str, out_path: Path) -> Tuple[int, float]:
        """Launch run_crest.sh detached in WSL. Returns (pid, create_time) — the
        Linux PID and start-time — to persist for reattach. Raises OrcaRunError if
        the launch fails or no PID appears."""
        if not self.distro:
            raise OrcaRunError("No WSL distro configured for CREST. Set one in Settings.")
        self._cancel_event.clear()
        self._detach_event.clear()

        script_path = Path(script_path)
        out_path = Path(out_path)
        pid_file = out_path.parent / f"{name}.crest.pid"
        start_file = out_path.parent / f"{name}.crest.start"
        rc_file = out_path.parent / f"{name}.crest.rc"
        for f in (pid_file, start_file, rc_file):
            try:
                f.unlink()
            except OSError:
                pass

        # setsid → new session leader that outlives wsl.exe; redirect all stdio
        # away so the job is fully detached. CRITICAL: after backgrounding, the
        # launcher WAITS (inside WSL) until the detached script has written its
        # .pid file, THEN returns it. Without this wait, `wsl.exe` returns so fast
        # that it closes the pty before setsid finishes detaching and the child is
        # killed before it runs a single line (the .pid never appears). Keeping the
        # foreground alive until .pid exists lets the child detach cleanly; it then
        # runs to completion independently (verified: survives the app closing).
        pid_wsl = f'$(wslpath -u {shlex.quote(str(pid_file))})'
        launch_cmd = (
            f'setsid bash "$(wslpath -u {shlex.quote(str(script_path))})" '
            "</dev/null >/dev/null 2>&1 & "
            f'p="{pid_wsl}"; '
            'for i in $(seq 1 100); do [ -s "$p" ] && break; sleep 0.1; done; '
            'cat "$p" 2>/dev/null'
        )
        rc, out, err = run_bash(self.distro, launch_cmd, timeout=30.0)
        if err == "wsl-not-found":
            raise OrcaRunError("wsl.exe not found — WSL is required to run CREST.")
        if rc != 0 and "Wsl/" in (err or ""):
            raise OrcaRunError(f"Could not start CREST in WSL: {err.strip()}")

        # Prefer the PID the launcher echoed (it only returns once the .pid file
        # exists); fall back to reading the file directly. The script writes its
        # own real PID (== session/group leader), authoritative for kill/reattach.
        pid = None
        for tok in reversed((out or "").split()):
            if tok.strip().isdigit():
                pid = int(tok.strip())
                break
        if pid is None:
            deadline = time.monotonic() + _PID_WAIT
            while time.monotonic() < deadline:
                try:
                    txt = pid_file.read_text(encoding="utf-8").strip()
                    if txt:
                        pid = int(txt)
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(0.2)
        if pid is None:
            raise OrcaRunError(
                "CREST did not start in WSL (no PID file was written). "
                f"Check the WSL distro '{self.distro}' and the CREST install."
            )
        start_time = None
        try:
            start_time = int(start_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pass
        with self._lock:
            self._pid = pid
            self._start_time = start_time
        return pid, float(start_time or 0)

    def adopt(self, pid: int, create_time: Optional[float]) -> None:
        """Reattach to a CREST process launched in a previous session."""
        self._cancel_event.clear()
        self._detach_event.clear()
        with self._lock:
            self._pid = int(pid)
            self._start_time = int(create_time) if create_time else None

    @staticmethod
    def end_position(output_path: Path) -> int:
        """text-mode tell() cookie for the current EOF of output_path (0 if
        unreadable) — a reattach tails from here so pre-close output isn't
        re-streamed. Mirrors OrcaRunner.end_position."""
        try:
            with open(output_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                return f.tell()
        except OSError:
            return 0

    # ---- monitor (worker thread, blocks) ----
    def monitor(self, out_path: Path, name: str,
                on_line: Optional[LogCallback] = None, start_pos: int = 0) -> Optional[int]:
        """Tail out_path and block until the run finishes (its .crest.rc marker
        appears), is cancelled, or is detached. Returns the CREST exit code (from
        the marker) or None. Raises OrcaCancelled / OrcaDetached accordingly."""
        out_path = Path(out_path)
        rc_path = out_path.parent / f"{name}.crest.rc"
        pos = int(start_pos)
        buf = ""
        last_liveness = time.monotonic()

        def _drain() -> None:
            nonlocal pos, buf
            try:
                with open(out_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while True:
                nl = buf.find("\n")
                if nl < 0:
                    break
                line = buf[:nl].rstrip("\r")
                buf = buf[nl + 1:]
                if on_line is not None:
                    on_line(line)

        while True:
            _drain()
            if self._cancel_event.is_set():
                self._kill()
                raise OrcaCancelled("Cancelled by user.")
            if self._detach_event.is_set():
                # True detach: leave CREST running in WSL for reattach next launch.
                raise OrcaDetached("Monitoring stopped; CREST left running.")
            if rc_path.exists():
                _drain()
                if buf.strip() and on_line is not None:
                    on_line(buf.rstrip("\r\n"))
                break
            # Safety net for a job killed without writing its .rc (e.g. WSL shut
            # down): check liveness occasionally so we don't tail forever.
            now = time.monotonic()
            if now - last_liveness >= _LIVENESS_EVERY:
                last_liveness = now
                if not self._is_alive():
                    time.sleep(0.5)   # give a just-finishing script time to flush .rc
                    _drain()
                    if rc_path.exists():
                        break
                    if buf.strip() and on_line is not None:
                        on_line(buf.rstrip("\r\n"))
                    break
            time.sleep(_TAIL_POLL)

        try:
            return int(rc_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    # ---- helpers ----
    def is_alive(self) -> bool:
        return self._is_alive()

    def _is_alive(self) -> bool:
        with self._lock:
            pid = self._pid
            start = self._start_time
        if not pid:
            return False
        # kill -0 tests existence; the start-time comparison guards against PID
        # reuse (the WSL analogue of procutil's create_time check).
        cmd = (f"kill -0 {pid} 2>/dev/null || exit 1; "
               f"awk '{{print $22}}' /proc/{pid}/stat 2>/dev/null")
        rc, out, _ = run_bash(self.distro, cmd, timeout=_WSL_CALL_TIMEOUT)
        if rc != 0:
            return False
        if start is not None:
            got = out.strip().split()
            if got:
                try:
                    if int(got[0]) != int(start):
                        return False   # PID was reused by a different process
                except ValueError:
                    pass
        return True

    def _kill(self) -> None:
        with self._lock:
            pid = self._pid
        if not pid:
            return
        # Kill the whole process group (leader PID == PGID after setsid) so CREST
        # and its xtb/OpenMP children all die; then the leader PID as a fallback.
        cmd = (f"kill -TERM -{pid} 2>/dev/null; kill -TERM {pid} 2>/dev/null; "
               f"sleep 0.3; kill -KILL -{pid} 2>/dev/null; kill -KILL {pid} 2>/dev/null; true")
        run_bash(self.distro, cmd, timeout=_WSL_CALL_TIMEOUT)
