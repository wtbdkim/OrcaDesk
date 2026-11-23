"""
Run one ORCA job on a remote device over SSH -- ``OrcaRunner``'s verbs
(launch / adopt / monitor / cancel / detach / end_position), so the queue
engine's ``_run_calc`` drives either one the same way.

This machine stays the source of truth (design D4/D5):

* **launch** uploads the local run folder's top-level files (what ORCA would
  see if it ran here, minus the previous run's .out and the ``.bak`` files set
  aside) into a remote folder of its own -- ``{workdir}/{run_dir}.{stamp}``,
  new for every launch so a re-run can never share a folder with an old run
  still going on a device that dropped off -- and starts ORCA there with
  ``setsid``, recording its pid and ``/proc`` start time (CREST's pattern,
  crest/runner.py).
* **monitor** syncs every ``SYNC_EVERY`` (10 s) with ONE short ssh call that
  answers both questions at once: is the process still the recorded one, and
  what has the remote ``.out`` gained since the last sync (only the new bytes,
  base64 so they arrive exact). Those are appended to the LOCAL ``.out``, which
  is tailed exactly like OrcaRunner, so the live log, the graphs and a
  reattach's history rebuild see an ordinary growing file -- ten seconds
  behind. No connection is held open between syncs: nothing streams, and the
  network carries only what ORCA wrote. An unknown answer is not a death
  (P6); two unknowns in a row raise the ``disconnected`` flag (the orange
  "Disconnected" box, design D7).
* when the process has ended -- normally, failed, cancelled or hung -- the
  **whole remote folder** comes back (staged, then verified by name and size,
  then moved into the run folder) and only then is the remote copy deleted,
  behind a path guard. The engine judges the result from the local files.
* on a **spot** device (``spot`` / ``requeue_after``, set by the engine) a job
  is given back instead (``RemoteRequeue``): after ``requeue_after`` seconds
  of CONTINUOUS silence (any answered sync resets the count), or when it is
  found dead after an outage with neither ORCA's normal end nor a fatal
  marker in its output -- a preempted VM, not an ORCA failure.

Nothing here writes to the shared log: transport detail goes to the
``orcamgr.remote`` logger (the bridge sends it to ``remote.log``), design D6.

Qt-free.
"""

from __future__ import annotations

import logging
import os
import base64
import binascii
import posixpath
import re
import shlex
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from ..core.runner import (OrcaCancelled, OrcaDetached, OrcaHung, OrcaRunError,
                           _FATAL_MARKERS, _HUNG_AFTER, _fatal_line)
from ..core.tailer import FileTailer
from .ssh import RunResult, Transport
from .winplatform import WINDOWS, is_windows_path

log = logging.getLogger("orcamgr.remote")

# The runner's own files in the remote run folder (pid, start time). Never
# fetched: they are bookkeeping, not results.
META_PREFIX = ".orcadesk"
# Local staging folder for a fetch, inside the run folder; removed after.
STAGING_PREFIX = ".orcadesk-fetch-"

# What a run folder / file name may contain to go to a device. sftp globs a
# path even inside quotes, and non-ASCII names through Win32-OpenSSH's sftp are
# unverified, so anything else simply runs on this machine (design §2).
_SAFE_REL_RE = re.compile(r"^[A-Za-z0-9._+\-/]+$")


class RemoteUnavailable(OrcaRunError):
    """The device could not take the job (upload or start failed on the
    transport). Not a failure of the calculation: the engine marks the device
    offline and offers the row again (design §5)."""


class RemoteBusy(RemoteUnavailable):
    """The job could not be handed over because THIS machine had no free
    connection slot in time (RunResult.busy): the device was never asked, so
    it is not marked offline -- the row just goes back to be offered again."""
    busy = True


class RemoteCancelUnreachable(OrcaCancelled):
    """Cancelled while the device could not be reached (or the fetch after
    the kill could not finish). The calc is CANCELLED at once; ``entry`` goes
    on the store's clean-up list, to be settled when the device is back
    (design §6.3)."""

    def __init__(self, msg: str, entry: dict):
        super().__init__(msg)
        self.entry = entry


class RemoteRequeue(OrcaRunError):
    """A job on a SPOT device given back to the queue: the device stopped
    answering for longer than its requeue_after_min without a single answered
    sync in between, or the job was found dead after an outage with neither
    ORCA's normal-termination line nor a fatal marker in its output (the VM
    was preempted; ORCA did not fail). Not the calculation's failure: the
    engine puts the row back to PENDING to run again (FAILED is locked, P24).
    ``entry`` (or None) is a clean-up entry with ``fetch`` False -- the old
    remote folder is deleted, never brought back -- and ``unreachable`` says
    whether the device is to be taken offline."""

    def __init__(self, msg: str, entry: Optional[dict], unreachable: bool):
        super().__init__(msg)
        self.entry = entry
        self.unreachable = unreachable


# What ORCA prints last on a run it finished itself (core/parser.py reads the
# same line for terminated_normally).
_NORMAL_END = "ORCA TERMINATED NORMALLY"


# What CREST prints on a run it ended itself: its success line
# (crest/parser.CREST_SUCCESS_MARKER) and its ERROR STOP's last words.
_CREST_END_MARKERS = ("crest terminated normally", "error termination")


def _ended_by_orca(output_path: Path, markers=None) -> bool:
    """Whether the .out shows the program ending the run itself -- normally,
    or with a fatal marker (``markers``, lower-case; ORCA's by default).
    Neither means something outside it stopped it (on a spot device: the VM
    went away). Read in binary chunks, ASCII markers."""
    if markers is None:
        markers = [_NORMAL_END.lower(), *_FATAL_MARKERS]
    needles = [m.encode() for m in markers]
    try:
        with open(output_path, "rb") as f:
            prev = b""
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    return False
                buf = (prev + chunk).lower()
                if any(n in buf for n in needles):
                    return True
                prev = buf[-256:]
    except OSError:
        return False


def remote_safe_name(rel: str) -> bool:
    """Whether a relative run-folder path (or file name) may go to a device."""
    if not rel or rel.startswith("/") or not _SAFE_REL_RE.match(rel):
        return False
    return ".." not in rel.split("/")


def remote_dir_for(workdir: str, run_dir: str, stamp: str) -> str:
    """``{workdir}/{run_dir}.{stamp}`` -- the remote folder of one launch."""
    return posixpath.join(workdir.rstrip("/") or "/", run_dir.strip("/")) + "." + stamp


def launch_stamp(now: Optional[float] = None) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(now))


def guard_remote_dir(remote_dir: str, workdir: str) -> bool:
    """The local half of the delete guard (design §6.4): ``remote_dir`` is an
    absolute, already-normal path strictly below an absolute ``workdir``. The
    remote half re-checks with ``realpath`` before ``rm -rf``."""
    if not remote_dir or not workdir:
        return False
    if not remote_dir.startswith("/") or not workdir.startswith("/"):
        return False
    w = posixpath.normpath(workdir)
    r = posixpath.normpath(remote_dir)
    if r != remote_dir.rstrip("/") or w in ("/", ""):
        return False
    return r.startswith(w + "/") and len(r) > len(w) + 1


def _q(s: str) -> str:
    return shlex.quote(str(s))


# ---- remote scripts ---------------------------------------------------------

def _launch_script(remote_dir: str, orca: str, name: str) -> str:
    # The inner sh records its own pid (it becomes the session leader under
    # setsid, so the pid is also the process group to kill) and its /proc
    # start time, then execs ORCA -- same pid, so the record names ORCA. ORCA
    # is run by absolute path and given the input by bare file name, as
    # OrcaRunner does (a space anywhere in a path truncates ORCA's argv).
    inner = ('echo $$ > .orcadesk.pid; '
             'awk "{print \\$22}" /proc/$$/stat > .orcadesk.start; '
             'exec "$0" "$1" > "$2" 2>&1')
    return f"""
cd {_q(remote_dir)} || exit 3
rm -f .orcadesk.pid .orcadesk.start
setsid sh -c {_q(inner)} {_q(orca)} {_q(name + '.inp')} {_q(name + '.out')} </dev/null >/dev/null 2>&1 &
i=0
while [ $i -lt 100 ]; do [ -s .orcadesk.start ] && break; sleep 0.1; i=$((i+1)); done
[ -s .orcadesk.start ] || exit 4
printf 'pid=%s\\nstart=%s\\n' "$(cat .orcadesk.pid)" "$(cat .orcadesk.start)"
"""


def _crest_launch_script(remote_dir: str, crest: str, name: str, flags) -> str:
    """_launch_script's shape for a CREST search: the same setsid / pid /
    /proc start record, then ``crest {name}.xyz <flags>`` with its output in
    {name}.out. CREST runs in the remote folder itself -- a device's disk is
    a native filesystem, so there is no scratch to stage into (the WSL 9P
    reason does not apply); --cinp names constraints.inp relative to it."""
    inner = ('echo $$ > .orcadesk.pid; '
             'awk "{print \\$22}" /proc/$$/stat > .orcadesk.start; '
             'out="$1"; shift; exec "$0" "$@" > "$out" 2>&1')
    args = " ".join(_q(a) for a in [name + ".out", name + ".xyz", *flags])
    return f"""
cd {_q(remote_dir)} || exit 3
rm -f .orcadesk.pid .orcadesk.start
setsid sh -c {_q(inner)} {_q(crest)} {args} </dev/null >/dev/null 2>&1 &
i=0
while [ $i -lt 100 ]; do [ -s .orcadesk.start ] && break; sleep 0.1; i=$((i+1)); done
[ -s .orcadesk.start ] || exit 4
printf 'pid=%s\\nstart=%s\\n' "$(cat .orcadesk.pid)" "$(cat .orcadesk.start)"
"""


def _sync_script(pid: int, start, remote_out: str, offset: int, cap: int) -> str:
    """One sync: line 1 says alive/dead (the _liveness_script test), the rest
    is base64 of the remote .out from byte ``offset`` on, at most ``cap``
    bytes (the next sync carries on from where this one stopped)."""
    return (_liveness_script(pid, start)
            + f"tail -c +{int(offset) + 1} -- {_q(remote_out)} 2>/dev/null"
              f" | head -c {int(cap)} | base64\n")


def _liveness_script(pid: int, start) -> str:
    return (f"s=$(awk '{{print $22}}' /proc/{int(pid)}/stat 2>/dev/null)\n"
            f"if [ -n \"$s\" ] && [ \"$s\" = {_q(_start_str(start))} ]; "
            f"then echo alive; else echo dead; fi\n")


def _start_str(start) -> str:
    try:
        return str(int(float(start)))
    except (TypeError, ValueError):
        return ""


def _kill_script(pid: int, start) -> str:
    # Only the process recorded at launch: pid AND start time must match, so a
    # pid the kernel reused for something else is never signalled.
    st = _q(_start_str(start))
    p = int(pid)
    return f"""
same() {{ s=$(awk '{{print $22}}' /proc/{p}/stat 2>/dev/null); [ -n "$s" ] && [ "$s" = {st} ]; }}
same || {{ echo gone; exit 0; }}
kill -TERM -- -{p} 2>/dev/null || kill -TERM {p} 2>/dev/null
i=0
while [ $i -lt 20 ]; do same || {{ echo killed; exit 0; }}; sleep 0.5; i=$((i+1)); done
kill -KILL -- -{p} 2>/dev/null || kill -KILL {p} 2>/dev/null
sleep 0.5
same && {{ echo alive; exit 1; }}
echo killed
"""


def _idle_script(remote_dir: str) -> str:
    return (f"cd {_q(remote_dir)} 2>/dev/null || exit 3\n"
            "n=$(find . -maxdepth 1 -printf '%T@\\n' 2>/dev/null | sort -n | tail -1)\n"
            "n=${n%.*}; [ -n \"$n\" ] || n=$(date +%s)\n"
            "echo $(( $(date +%s) - n ))\n")


def _list_script(remote_dir: str) -> str:
    return (f"cd {_q(remote_dir)} || exit 3\n"
            f"find . -type f ! -name '{META_PREFIX}*' -printf '%s\\t%P\\n'\n")


def _remove_script(remote_dir: str, workdir: str) -> str:
    return (f"r=$(realpath -- {_q(remote_dir)} 2>/dev/null) || {{ echo gone; exit 0; }}\n"
            f"w=$(realpath -- {_q(workdir)} 2>/dev/null) || exit 3\n"
            "case \"$r\" in \"$w\"/?*) rm -rf -- \"$r\" && echo removed ;; *) exit 3 ;; esac\n")


# ---- the remote platform -----------------------------------------------------

class PosixPlatform:
    """The POSIX scripts above behind the interface winplatform.WindowsPlatform
    shares, so everything below drives either kind of device."""

    name = "posix"

    @staticmethod
    def run(transport, alias: str, script: str, timeout: float = 30.0,
            stop=None) -> RunResult:
        if stop is None:
            return transport.run(alias, script, timeout=timeout)
        return transport.run(alias, script, timeout=timeout, stop=stop)

    @staticmethod
    def sftp_path(path: str) -> str:
        return path

    guard = staticmethod(guard_remote_dir)

    @staticmethod
    def mkdir(remote_dir: str) -> str:
        return f"mkdir -p -- {_q(remote_dir)}"

    launch = staticmethod(_launch_script)
    sync = staticmethod(_sync_script)
    liveness = staticmethod(_liveness_script)
    kill = staticmethod(_kill_script)
    idle = staticmethod(_idle_script)
    listing = staticmethod(_list_script)
    remove = staticmethod(_remove_script)


POSIX = PosixPlatform()


def platform_for(path: str):
    """The platform a remote folder (or workdir) lives on, read from its
    shape: the probe resolved the workdir to ``C:/...`` on a Windows device
    and ``/...`` on a POSIX one, and every remote folder is a child of it."""
    return WINDOWS if is_windows_path(path or "") else POSIX


# ---- fetch / verify / delete ------------------------------------------------

@dataclass
class FetchResult:
    ok: bool
    unreachable: bool = False
    message: str = ""
    missing: bool = False     # the remote folder is not there at all


def list_remote_files(transport: Transport, alias: str, remote_dir: str,
                      stop=None) -> Tuple[Optional[Dict[str, int]], RunResult]:
    """{relative path: size} of every file in ``remote_dir`` (the runner's own
    bookkeeping files left out), or None with the failed result."""
    pf = platform_for(remote_dir)
    res = pf.run(transport, alias, pf.listing(remote_dir), timeout=120, stop=stop)
    if not res.ok:
        return None, res
    files: Dict[str, int] = {}
    for line in res.out.splitlines():
        size, sep, rel = line.partition("\t")
        if not sep or not rel:
            continue
        try:
            files[rel] = int(size)
        except ValueError:
            continue
    return files, res


def fetch_and_verify(transport: Transport, alias: str, remote_dir: str,
                     local_dir: Path, stop=None) -> FetchResult:
    """Bring the WHOLE ``remote_dir`` into ``local_dir`` (design D4).

    Files land in a staging folder of this launch's own first -- ``get -a``
    resumes a partial download there across retries, and can never mistake an
    older run's same-named file for a finished download -- are checked one by
    one against the remote listing (name and size, D5), and only then replace
    their counterparts in the run folder. Files that exist only locally (a
    ``.bak`` set aside) are left alone. Never raises. ``stop`` abandons it
    (the staging folder stays, and the next attempt resumes into it)."""
    files, res = list_remote_files(transport, alias, remote_dir, stop=stop)
    if files is None:
        return FetchResult(False, res.unreachable,
                           res.err.strip() or f"listing failed ({res.rc})",
                           missing=(res.rc == 3))
    local_dir = Path(local_dir)
    staging = local_dir / (STAGING_PREFIX + posixpath.basename(remote_dir.rstrip("/")))
    pf = platform_for(remote_dir)
    pairs = []
    for rel in files:
        if not remote_safe_name(rel):
            # a name the transport cannot carry safely: report, do not guess
            return FetchResult(False, False, f"cannot fetch file with this name: {rel}")
        dest = staging / Path(*rel.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        pairs.append((pf.sftp_path(posixpath.join(remote_dir, rel)), dest))
    if pairs:
        got = (transport.get(alias, pairs) if stop is None
               else transport.get(alias, pairs, stop=stop))
        if not got.ok:
            return FetchResult(False, got.unreachable,
                               got.err.strip() or f"download failed ({got.rc})")
    for rel, size in files.items():
        p = staging / Path(*rel.split("/"))
        try:
            have = p.stat().st_size
        except OSError:
            return FetchResult(False, False, f"missing after download: {rel}")
        if have != size:
            return FetchResult(False, False,
                               f"size mismatch for {rel}: {have} != {size}")
    for rel in files:
        src = staging / Path(*rel.split("/"))
        dest = local_dir / Path(*rel.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dest)
    shutil.rmtree(staging, ignore_errors=True)
    return FetchResult(True)


def safe_remove(transport: Transport, alias: str, remote_dir: str,
                workdir: str) -> bool:
    """Delete ``remote_dir`` on the device, only behind both guards. True when
    it is gone (deleted now, or already absent)."""
    pf = platform_for(remote_dir)
    if platform_for(workdir) is not pf or not pf.guard(remote_dir, workdir):
        log.warning("refused to delete %s:%s (outside %s)", alias, remote_dir, workdir)
        return False
    res = pf.run(transport, alias, pf.remove(remote_dir, workdir), timeout=300)
    if res.ok and res.out.strip() in ("removed", "gone"):
        return True
    log.warning("delete of %s:%s failed: rc=%s %s", alias, remote_dir, res.rc,
                res.err.strip())
    return False


def kill_remote(transport: Transport, alias: str, pid: int, start,
                pf=POSIX) -> Optional[bool]:
    """Stop the remote process group if it is still the one recorded. True =
    not running any more, False = still alive, None = device unreachable.
    ``pf`` is the device's platform (platform_for its remote folder)."""
    res = pf.run(transport, alias, pf.kill(pid, start), timeout=60)
    if res.unreachable or res.busy:
        return None
    return res.out.strip().endswith(("killed", "gone"))


def remote_liveness(transport: Transport, alias: str, pid, start,
                    pf=POSIX) -> Optional[bool]:
    """True / False, or None when the device could not be asked."""
    if not pid:
        return False
    res = pf.run(transport, alias, pf.liveness(int(pid), start), timeout=30)
    if res.unreachable or res.busy:
        return None
    out = res.out.strip()
    if out == "alive":
        return True
    if out == "dead":
        return False
    return None


def process_cleanup(transport: Transport, entry: dict, workdir: str,
                    local_dir: Optional[Path]) -> bool:
    """Settle one clean-up entry (design §6.3 step 4) on a device that is
    reachable again: stop the old process if it is still the recorded one,
    then -- when ``local_dir`` is given (the calc has not been re-run since) --
    fetch the whole folder and verify it first; delete the remote copy last.
    True when the entry is done."""
    alias = entry.get("device", "")
    remote_dir = entry.get("remote_dir", "")
    pid = entry.get("pid")
    if pid:
        if kill_remote(transport, alias, int(pid), entry.get("create_time"),
                       platform_for(remote_dir)) is not True:
            return False
    if local_dir is not None:
        fr = fetch_and_verify(transport, alias, remote_dir, local_dir)
        if not fr.ok and not fr.missing:
            return False      # a folder already gone has nothing to bring back
    return safe_remove(transport, alias, remote_dir, workdir)


# ---- the runner --------------------------------------------------------------

class RemoteOrcaRunner:
    """Launches / reattaches to / monitors one ORCA job on a remote device."""

    SYNC_EVERY = 10.0         # seconds between syncs (liveness + new .out bytes)
    SYNC_CAP = 4 << 20        # at most this much new .out per sync
    TAIL_POLL = 0.35          # local .out re-read / cancel-check cadence
    DISCONNECT_AFTER = 2      # consecutive unknown answers -> disconnected
    HUNG_CHECK = 30.0
    HUNG_AFTER = _HUNG_AFTER
    FETCH_BACKOFF = (5.0, 10.0, 30.0, 60.0)
    FETCH_NOTICE_AFTER = 600.0
    FETCH_NOTICE = ("Could not bring the results back yet — check that the "
                    "compute device is switched on.")

    def __init__(self, transport: Transport, alias: str, orca_path: str,
                 workdir: str, remote_dir: str,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.transport = transport
        self.alias = alias
        self.orca_path = orca_path
        self.workdir = workdir
        self.remote_dir = remote_dir
        # which script set drives the device: read from the folder's shape
        # (C:/... on a Windows device), never from this machine's platform
        self.pf = platform_for(remote_dir or workdir)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._pid: Optional[int] = None
        self._start: Optional[float] = None
        self._name = ""
        self._cancel_event = threading.Event()
        self._detach_event = threading.Event()
        self._disconnected = False
        # A spot device (set by the engine from Settings): a job unreachable
        # for requeue_after seconds of CONTINUOUS silence, or found dead after
        # an outage with no word from ORCA, is given back (RemoteRequeue).
        self.spot = False
        self.requeue_after = 300.0
        self._adopted = False
        # the device's HighQoS watcher (Windows only, winplatform.QOS_SCRIPT)
        # has been seen to, for this job -- at launch, or on a reattached job's
        # first answered sync once the workdir is known
        self._qos_done = self.pf is not WINDOWS
        # set by the engine: called with True/False when the flag flips, and
        # with a message when a fetch keeps failing
        self.on_disconnect: Optional[Callable[[bool], None]] = None
        self.on_notice: Optional[Callable[[str], None]] = None

    # ---- signals (UI thread) ----
    def cancel(self) -> None:
        self._cancel_event.set()

    def detach(self) -> None:
        self._detach_event.set()

    @property
    def disconnected(self) -> bool:
        return self._disconnected

    def _set_disconnected(self, value: bool) -> None:
        if value == self._disconnected:
            return
        self._disconnected = value
        log.info("%s %s", self.alias, "disconnected" if value else "reconnected")
        if self.on_disconnect is not None:
            try:
                self.on_disconnect(value)
            except Exception:
                pass

    # ---- launch / adopt (worker thread) ----
    def launch(self, input_path: Path, output_path: Path) -> Tuple[int, float]:
        input_path = Path(input_path)
        output_path = Path(output_path)
        name = input_path.stem
        self._name = name
        self._check_program()
        if (platform_for(self.workdir) is not self.pf
                or not self.pf.guard(self.remote_dir, self.workdir)):
            raise RemoteUnavailable(f"remote folder {self.remote_dir} is not under {self.workdir}")
        files = self._files_to_send(input_path.parent, output_path)
        for p in files:
            if not remote_safe_name(p.name):
                raise RemoteUnavailable(f"file name not safe to send: {p.name}")
        pf = self.pf
        res = pf.run(self.transport, self.alias, pf.mkdir(self.remote_dir), timeout=60)
        if not res.ok:
            raise (RemoteBusy if res.busy else RemoteUnavailable)(
                f"mkdir failed on {self.alias}: {res.err.strip() or res.rc}")
        if files:
            put = self.transport.put(self.alias, [
                (p, pf.sftp_path(posixpath.join(self.remote_dir, p.name))) for p in files])
            if not put.ok:
                raise (RemoteBusy if put.busy else RemoteUnavailable)(
                    f"upload failed to {self.alias}: {put.err.strip() or put.rc}")
        res = pf.run(self.transport, self.alias, self._launch_script_for(name), timeout=60)
        kv = dict((k.strip(), v.strip()) for k, v in
                  (line.split("=", 1) for line in res.out.splitlines() if "=" in line))
        if not res.ok or not kv.get("pid") or not kv.get("start"):
            raise (RemoteBusy if res.busy else RemoteUnavailable)(
                f"start failed on {self.alias}: rc={res.rc} {res.err.strip()}")
        # a fresh run: the local .out starts empty, as OrcaRunner's "w" open
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"")
        with self._lock:
            self._pid = int(kv["pid"])
            self._start = float(kv["start"])
        log.info("launched %s on %s:%s pid %s", name, self.alias, self.remote_dir, self._pid)
        self._ensure_qos()
        return self._pid, self._start

    def _ensure_qos(self) -> None:
        """On a Windows device, make sure the HighQoS watcher runs (see
        winplatform.QOS_SCRIPT: without it Windows parks an SSH-started ORCA
        on the E-cores). Errors are data: the job runs regardless, only
        slower, and remote.log says why. Nothing on Linux."""
        if self._qos_done or not self.workdir:
            return
        self._qos_done = True
        try:
            res = self.pf.run(self.transport, self.alias, WINDOWS.qos_ensure(self.workdir),
                              timeout=60, stop=self._detach_event)
        except Exception as e:          # never let the watcher cost the job
            log.warning("%s: HighQoS watcher not started: %s", self.alias, e)
            return
        said = next((ln.strip() for ln in res.out.splitlines() if ln.startswith("qos=")), "")
        if res.ok and said.startswith(("qos=running", "qos=started")):
            log.info("%s HighQoS watcher: %s", self.alias, said)
        else:
            log.warning("%s: HighQoS watcher not started (rc=%s %s %s); ORCA runs, "
                        "but Windows may keep it on the slow cores", self.alias, res.rc,
                        said, res.err.strip()[:200])

    # ---- what a launch sends and starts (RemoteCrestRunner overrides) ----
    #: what the program prints when IT ends a run (normally or with an
    #: error), lower-case: a spot job found dead without one was preempted
    END_MARKERS = None            # None = ORCA's (_ended_by_orca's default)

    def _check_program(self) -> None:
        if not self.orca_path:
            raise RemoteUnavailable(f"no ORCA path for {self.alias}")

    def _files_to_send(self, folder: Path, output_path: Path) -> list:
        """The run folder's top-level files -- what ORCA would see if it ran
        here -- minus the previous run's .out and the .bak files set aside."""
        files = []
        for p in sorted(folder.iterdir()):
            if not p.is_file():
                continue
            if p.name == output_path.name or p.name.endswith(".bak") \
                    or p.name.startswith(META_PREFIX):
                continue
            files.append(p)
        return files

    def _launch_script_for(self, name: str) -> str:
        return self.pf.launch(self.remote_dir, self.orca_path, name)

    def adopt(self, pid: int, create_time: Optional[float]) -> None:
        with self._lock:
            self._pid = int(pid) if pid else None
            self._start = create_time
        # a reattach: nobody watched while ORCAdesk was closed
        self._adopted = True

    @staticmethod
    def end_position(output_path: Path) -> int:
        return FileTailer.end_position(output_path)

    def liveness(self) -> Optional[bool]:
        with self._lock:
            pid, start = self._pid, self._start
        return remote_liveness(self.transport, self.alias, pid, start, self.pf)

    def cleanup_entry(self) -> dict:
        with self._lock:
            pid, start = self._pid, self._start
        return {"device": self.alias, "pid": pid, "create_time": start,
                "remote_dir": self.remote_dir, "calc_name": self._name,
                "launched_at": self.remote_dir.rsplit(".", 1)[-1]}

    # ---- one sync: liveness + what the remote .out gained ----
    def sync(self, offset: int) -> Tuple[Optional[bool], bytes]:
        """(alive, new bytes of the remote .out from ``offset``). alive is
        None when the device could not be asked -- and then no bytes."""
        with self._lock:
            pid, start = self._pid, self._start
        if not pid:
            return False, b""
        remote_out = posixpath.join(self.remote_dir, f"{self._name}.out")
        # an app close abandons the call at once (stop = the detach event):
        # the job keeps running on the device and is reattached next launch
        res = self.pf.run(
            self.transport, self.alias,
            self.pf.sync(pid, start, remote_out, offset, self.SYNC_CAP), timeout=60,
            stop=self._detach_event)
        # no free connection slot HERE: the device was never asked -- the
        # monitor retries soon and counts nothing (see RunResult.busy)
        self.last_sync_busy = bool(getattr(res, "busy", False))
        if res.unreachable or self.last_sync_busy:
            return None, b""
        head, _, rest = res.out.partition("\n")
        head = head.strip()
        if head not in ("alive", "dead"):
            return None, b""
        try:
            data = base64.b64decode(rest) if rest.strip() else b""
        except (ValueError, binascii.Error):
            data = b""
        return head == "alive", data

    # ---- monitor (worker thread, blocks) ----
    def monitor(self, output_path: Path, on_line=None, start_pos: int = 0,
                on_fatal=None) -> None:
        output_path = Path(output_path)
        if not self._name:
            self._name = output_path.stem
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not output_path.exists():
            output_path.write_bytes(b"")
        tail = FileTailer(output_path, start_pos)
        first = _fatal_line(output_path) if start_pos else ""
        fatal = bool(first)
        if fatal and on_fatal is not None:
            on_fatal(first)

        def see(line: str) -> None:
            nonlocal fatal
            if not fatal:
                low = line.lower()
                fatal = any(m in low for m in _FATAL_MARKERS)
                if fatal and on_fatal is not None:
                    on_fatal(line.strip())
            if on_line is not None:
                on_line(line)

        unknown = 0
        # when the current run of unanswered syncs began (None while the device
        # answers): the spot requeue counts CONTINUOUS silence only, so a
        # connection that flaps never adds its outages up
        lost_since: Optional[float] = None
        # an outage happened while this job ran -- or a reattach, where the app
        # was not watching at all: the precondition for reading a silent death
        # as a preemption
        had_outage = self._adopted
        next_sync = self._clock()          # sync at once (a reattach needs it)
        next_hung = 0.0
        while True:
            tail.drain(see)
            if self._cancel_event.is_set():
                self._cancel_and_fetch(output_path)    # raises
            if self._detach_event.is_set():
                raise OrcaDetached("Monitoring stopped; ORCA left running on the device.")
            now = self._clock()
            if now >= next_sync:
                next_sync = now + self.SYNC_EVERY
                try:
                    offset = output_path.stat().st_size
                except OSError:
                    offset = 0
                alive, data = self.sync(offset)
                if self._detach_event.is_set():
                    # a sync abandoned by the close is no outage: neither the
                    # Disconnected flag nor the spot requeue may count it
                    raise OrcaDetached("Monitoring stopped; ORCA left running on the device.")
                if alive is None and getattr(self, "last_sync_busy", False):
                    # this machine's connection slots were all taken: the
                    # device said nothing, so nothing is counted; ask again soon
                    next_sync = now + min(self.SYNC_EVERY, 2.0)
                elif alive is None:
                    unknown += 1
                    had_outage = True
                    if lost_since is None:
                        lost_since = now
                    if unknown >= self.DISCONNECT_AFTER:
                        self._set_disconnected(True)
                    if self.spot and now - lost_since >= self.requeue_after:
                        raise RemoteRequeue(
                            "Re-queued: the spot device stopped answering.",
                            dict(self.cleanup_entry(), fetch=False), unreachable=True)
                else:
                    unknown = 0
                    lost_since = None
                    self._set_disconnected(False)
                    if data:
                        with open(output_path, "ab") as f:
                            f.write(data)
                        tail.drain(see)
                    if alive is False:
                        if self.spot and had_outage and not _ended_by_orca(
                                output_path, self.END_MARKERS):
                            self._requeue_preempted()      # raises
                        break
                    # a reattached job (or one whose workdir the engine filled
                    # in only after the device's first probe) sees to the
                    # device's HighQoS watcher once, so a restart of ORCAdesk
                    # does not leave ORCA on the slow cores (Windows only)
                    if not self._qos_done and self.workdir:
                        self._ensure_qos()
            if fatal and now >= next_hung:
                next_hung = now + self.HUNG_CHECK
                idle = self._remote_idle()
                if idle is not None and idle >= self.HUNG_AFTER:
                    with self._lock:
                        pid, start = self._pid, self._start
                    kill_remote(self.transport, self.alias, pid or 0, start, self.pf)
                    self._fetch_until_done(output_path)
                    tail.drain(see)
                    tail.flush_tail(see)
                    self._remove()
                    raise OrcaHung(
                        f"ORCA reported an error and then wrote nothing for "
                        f"{int(idle // 60)} min without exiting; ORCAdesk "
                        f"stopped it.")
            self._sleep(self.TAIL_POLL)

        # the process has ended: bring the folder back before anything judges it
        self._fetch_until_done(output_path)
        tail.drain(see)
        tail.flush_tail(see)
        self._set_disconnected(False)
        self._remove()

    def _requeue_preempted(self) -> None:
        """The job died during an outage with no word from ORCA (no normal end,
        no fatal marker): the spot VM was taken away under it. Its folder holds
        nothing worth judging -- it is deleted now if the device lets us, else
        left to the clean-up list -- and the row runs again."""
        log.warning("%s:%s died without ORCA ending it; re-queued",
                    self.alias, self.remote_dir)
        entry = None
        if not safe_remove(self.transport, self.alias, self.remote_dir, self.workdir):
            entry = dict(self.cleanup_entry(), pid=None, fetch=False)
        raise RemoteRequeue("Re-queued: the spot device stopped answering.",
                            entry, unreachable=False)

    def _remote_idle(self) -> Optional[float]:
        res = self.pf.run(self.transport, self.alias, self.pf.idle(self.remote_dir), timeout=30,
                          stop=self._detach_event)
        if not res.ok:
            return None
        try:
            return float(res.out.strip())
        except ValueError:
            return None

    def _remove(self) -> None:
        if not safe_remove(self.transport, self.alias, self.remote_dir, self.workdir):
            log.warning("left %s:%s in place", self.alias, self.remote_dir)

    def _fetch_until_done(self, output_path: Path) -> None:
        """Fetch + verify, retrying with back-off until it lands. A cancel
        while it cannot land leaves the rest to the clean-up list; a detach
        (app closing) leaves the calc RUNNING -- the next launch's reattach
        finds the process dead and fetches again."""
        started = self._clock()
        attempt = 0
        noticed = False
        while True:
            fr = fetch_and_verify(self.transport, self.alias, self.remote_dir,
                                  output_path.parent, stop=self._detach_event)
            if fr.ok:
                return
            if self._detach_event.is_set():
                raise OrcaDetached("App closing while results were coming back.")
            if fr.missing:
                # nothing left on the device to bring back (removed by hand):
                # the synced .out is all there is, and the engine judges it
                log.warning("%s:%s is gone; judging the synced output",
                            self.alias, self.remote_dir)
                return
            log.warning("fetch from %s:%s failed: %s", self.alias, self.remote_dir, fr.message)
            if fr.unreachable:
                attempt += 1
                if attempt >= self.DISCONNECT_AFTER:
                    self._set_disconnected(True)
            if not noticed and self._clock() - started >= self.FETCH_NOTICE_AFTER:
                noticed = True
                if self.on_notice is not None:
                    try:
                        self.on_notice(self.FETCH_NOTICE)
                    except Exception:
                        pass
            wait = self.FETCH_BACKOFF[min(attempt, len(self.FETCH_BACKOFF) - 1)]
            deadline = self._clock() + wait
            while self._clock() < deadline:
                if self._detach_event.is_set():
                    raise OrcaDetached("App closing while results were coming back.")
                if self._cancel_event.is_set():
                    raise RemoteCancelUnreachable("Cancelled by user.", self.cleanup_entry())
                self._sleep(min(self.TAIL_POLL, max(0.0, deadline - self._clock())))

    def _cancel_and_fetch(self, output_path: Path) -> None:
        """Cancel: stop the remote process, bring the folder back, delete it.
        If the device cannot be reached for any of it, the calc is cancelled
        at once all the same and the rest goes on the clean-up list."""
        with self._lock:
            pid, start = self._pid, self._start
        stopped = (kill_remote(self.transport, self.alias, pid or 0, start, self.pf)
                   if pid else True)
        if stopped is not True:
            raise RemoteCancelUnreachable("Cancelled by user.", self.cleanup_entry())
        fr = fetch_and_verify(self.transport, self.alias, self.remote_dir, output_path.parent)
        if not fr.ok:
            entry = self.cleanup_entry()
            entry["pid"] = None     # it is stopped; only fetch + delete remain
            raise RemoteCancelUnreachable("Cancelled by user.", entry)
        self._remove()
        raise OrcaCancelled("Cancelled by user.")


#: the files a CREST launch sends -- the input structure, and the constraints
#: file with the reference structure it names (crest/runner.py writes all
#: three locally). Never the run folder wholesale: a previous search's
#: ensemble (crest_conformers.xyz ...) left beside them must not reach the
#: device, or a search that dies early would come back looking finished.
def crest_upload_names(name: str) -> tuple:
    from ..crest.runner import CREST_CONSTRAINTS_FILE, CREST_REFERENCE_FILE
    return (f"{name}.xyz", CREST_CONSTRAINTS_FILE, CREST_REFERENCE_FILE)


class RemoteCrestRunner(RemoteOrcaRunner):
    """A CREST conformer search on a remote LINUX device, with everything
    RemoteOrcaRunner does after the launch unchanged: the 10 s sync into the
    local .out, the Disconnected flag, spot re-queue, whole-folder fetch ->
    verify -> guarded delete, cancel and the clean-up list, reattach. Only
    what is sent and what is started differ: ``launch(xyz_path, out_path)``
    sends the files crest_upload_names lists and starts ``crest`` with
    ``flags`` (crest.runner.build_crest_argv) in the remote folder itself.
    The engine reads the fetched folder with the local CREST result path
    (parse_crest_result, the conformer export), as for a run here."""

    END_MARKERS = list(_CREST_END_MARKERS)

    def __init__(self, transport: Transport, alias: str, crest_path: str,
                 workdir: str, remote_dir: str, flags=(), **kw):
        super().__init__(transport, alias, "", workdir, remote_dir, **kw)
        self.crest_path = crest_path
        self.flags = list(flags)

    def _check_program(self) -> None:
        if not self.crest_path:
            raise RemoteUnavailable(f"no CREST on {self.alias}")
        if self.pf is not POSIX:
            # CREST ships a Linux binary only; placement never sends it here
            raise RemoteUnavailable(f"{self.alias} is not a Linux device; CREST cannot run there")

    def _files_to_send(self, folder: Path, output_path: Path) -> list:
        return [folder / n for n in crest_upload_names(self._name)
                if (folder / n).is_file()]

    def _launch_script_for(self, name: str) -> str:
        return _crest_launch_script(self.remote_dir, self.crest_path, name, self.flags)
