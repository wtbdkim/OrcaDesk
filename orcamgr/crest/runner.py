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
from ..core.xyzutil import as_xyz_file
from .wsl import run_bash


LogCallback = Callable[[str], None]

_TAIL_POLL = 0.5        # seconds between .out re-reads
_LIVENESS_EVERY = 6.0   # seconds between WSL liveness checks (keep wsl.exe spawns rare)
# Consecutive INDETERMINATE liveness checks (wsl.exe itself failing, not the
# process being gone) before monitor gives up the tail — ~2 min of continuous
# WSL outage, so the distro-deleted-mid-run case still terminates while a single
# transient hiccup can never condemn a healthy run.
_MAX_INDETERMINATE = 20
_PID_WAIT = 10.0        # seconds to wait for the launched script to write its .pid
_WSL_CALL_TIMEOUT = 20.0

def build_crest_argv(config, charge: int, multiplicity: int) -> list[str]:
    """Build CREST CLI flags (excluding the binary and the input filename) from a
    StepConfig + the calc's charge/multiplicity. CREST's --uhf is the number of
    unpaired electrons (multiplicity - 1), NOT the multiplicity."""
    # The method label maps 1:1 onto CREST's flag (gfn2 -> --gfn2, incl.
    # gfn2//gfnff); an already-dashed value passes through verbatim.
    method = (getattr(config, "crest_method", "") or "gfn2").strip().lower()
    argv = [method if method.startswith("-") else "--" + method]

    solvent = (getattr(config, "crest_solvent", "") or "").strip()
    if solvent:
        model = (getattr(config, "crest_solvent_model", "alpb") or "alpb").strip().lower()
        argv += ["--gbsa" if model == "gbsa" else "--alpb", solvent]

    # Numeric knobs. StepConfig.from_dict is the trust boundary — it coerces and
    # clamps every one of these — so the fallback here only covers a hand-built
    # StepConfig (tests / direct construction). A bad value degrades to the
    # field's default rather than dropping the flag: a silently missing -T would
    # ignore the user's thread setting without saying so.
    def _num(attr, default=0.0):
        try:
            return float(getattr(config, attr, default) or default)
        except (TypeError, ValueError):
            return default

    argv += ["--ewin", f"{_num('crest_ewin', 6.0):g}"]
    argv += ["-T", str(max(1, int(_num('crest_threads', 4.0))))]

    # --- advanced knobs (each emitted only when set to a non-default value) ---

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


def write_crest_run_files(calc_dir, name: str, xyz: str, crest_bin: str,
                          argv_flags: list[str]) -> Path:
    """Write the input ``{name}.xyz`` and the self-contained ``run_crest.sh`` into
    the (Windows) calc folder. Returns the run_crest.sh path. The script is
    written with LF line endings (bash chokes on CRLF)."""
    calc_dir = Path(calc_dir)
    calc_dir.mkdir(parents=True, exist_ok=True)
    (calc_dir / f"{name}.xyz").write_text(as_xyz_file(xyz), encoding="utf-8", newline="\n")

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

    def __init__(self, distro: str):
        self.distro = distro
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
        # No event clearing (here or in adopt): the runner is created fresh per
        # calc, and the engine forwards a Stop/shutdown that landed before the
        # runner was registered — clearing would erase exactly that signal.

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
        """Reattach to a CREST process launched in a previous session. No event
        clearing — see launch()."""
        with self._lock:
            self._pid = int(pid)
            self._start_time = int(create_time) if create_time else None

    @staticmethod
    def end_position(output_path: Path) -> int:
        """Text-mode tell() cookie for the current EOF of ``output_path`` — see
        FileTailer.end_position (a reattach tails from here)."""
        return FileTailer.end_position(output_path)

    # ---- monitor (worker thread, blocks) ----
    def monitor(self, out_path: Path, name: str,
                on_line: Optional[LogCallback] = None, start_pos: int = 0) -> Optional[int]:
        """Tail out_path and block until the run finishes (its .crest.rc marker
        appears), is cancelled, or is detached. Returns the CREST exit code (from
        the marker) or None. Raises OrcaCancelled / OrcaDetached accordingly."""
        out_path = Path(out_path)
        rc_path = out_path.parent / f"{name}.crest.rc"
        tail = FileTailer(out_path, start_pos)
        last_liveness = time.monotonic()
        indeterminate = 0   # consecutive liveness checks WSL couldn't answer

        while True:
            tail.drain(on_line)
            if self._cancel_event.is_set():
                self._kill(scratch_name=name)
                raise OrcaCancelled("Cancelled by user.")
            if self._detach_event.is_set():
                # True detach: leave CREST running in WSL for reattach next launch.
                raise OrcaDetached("Monitoring stopped; CREST left running.")
            if rc_path.exists():
                tail.drain(on_line)
                tail.flush_tail(on_line)
                break
            # Safety net for a job killed without writing its .rc (e.g. WSL shut
            # down): check liveness occasionally so we don't tail forever. Only
            # a DEFINITIVE "gone" (or a long unbroken run of failed checks —
            # e.g. the distro was deleted mid-run) ends the tail: a single
            # transient wsl.exe failure (timeout, service hiccup) must not
            # abandon a healthy run — the resulting no-ensemble parse would
            # FAILED-lock it (P24) while CREST keeps producing a good result.
            now = time.monotonic()
            if now - last_liveness >= _LIVENESS_EVERY:
                last_liveness = now
                alive = self._liveness()
                if alive is None:
                    indeterminate += 1
                else:
                    indeterminate = 0
                if alive is False or indeterminate >= _MAX_INDETERMINATE:
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
        """True unless the process is DEFINITIVELY gone. An indeterminate check
        (wsl.exe timed out / WSL service hiccup) reports alive: the callers'
        not-alive branches are one-way doors (reconcile judges from files;
        monitor gives up the tail), so a transient probe failure must never
        condemn a healthy multi-hour run."""
        return self._liveness() is not False

    def _liveness(self) -> Optional[bool]:
        """Three-state liveness: True (alive), False (definitively gone —
        kill -0 said so), None (could not ask WSL: timeout, wsl.exe missing,
        or a Wsl/ service error)."""
        with self._lock:
            pid = self._pid
            start = self._start_time
        if not pid:
            return False
        # kill -0 tests existence (its failure exits 1 — the only DEFINITIVE
        # "gone"); the start-time comparison guards against PID reuse (the WSL
        # analogue of procutil's create_time check).
        cmd = (f"kill -0 {pid} 2>/dev/null || exit 1; "
               f"awk '{{print $22}}' /proc/{pid}/stat 2>/dev/null")
        rc, out, err = run_bash(self.distro, cmd, timeout=_WSL_CALL_TIMEOUT)
        if rc == 0:
            if start is not None:
                got = out.strip().split()
                if got:
                    try:
                        if int(got[0]) != int(start):
                            return False   # PID was reused by a different process
                    except ValueError:
                        pass
            return True
        if rc == 1 and "Wsl/" not in (err or ""):
            return False                    # kill -0 failed: the process is gone
        return None                         # wsl.exe itself failed — unknown

    def _kill(self, scratch_name: str = "") -> None:
        with self._lock:
            pid = self._pid
        if not pid:
            return
        # Kill the whole process group (leader PID == PGID after setsid) so CREST
        # and its xtb/OpenMP children all die; then the leader PID as a fallback.
        cmd = (f"kill -TERM -{pid} 2>/dev/null; kill -TERM {pid} 2>/dev/null; "
               f"sleep 0.3; kill -KILL -{pid} 2>/dev/null; kill -KILL {pid} 2>/dev/null; true")
        if scratch_name:
            # run_crest.sh only removes its ext4 scratch dir on a normal finish;
            # a kill would otherwise leak MD trajectories (hundreds of MB per
            # cancelled run) inside the WSL VHD forever. (Adjacent-string
            # concatenation: "$HOME/..." expands, the quoted name doesn't.)
            cmd += (f'; rm -rf "$HOME/.orcadesk/scratch/"'
                    f'{shlex.quote(scratch_name)} 2>/dev/null; true')
        run_bash(self.distro, cmd, timeout=_WSL_CALL_TIMEOUT)
