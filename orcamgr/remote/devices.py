"""
Which remote devices exist, what each one is, and how the user configured it.

* **Which exist** -- the ``Host`` aliases in ``~/.ssh/config`` (with its
  ``Include``s). The user set them up (design D1); ORCAdesk only reads them,
  the way it detects WSL distros for CREST rather than asking for one (P58).
* **What each is** -- one non-interactive SSH call runs ``PROBE_SCRIPT``:
  OS, cores, memory, where ORCA is, OpenMPI, ``setsid``, free disk.
* **How it is configured** -- ``Settings.remote_devices``, one dict per alias:
  whether to use it and its caps (the choice within what was detected).

Qt-free. ``DeviceMonitor`` runs the probes on daemon threads and keeps the
latest answer per alias for the bridge to poll.
"""

from __future__ import annotations

import glob
import os
import re
import shlex
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from ..core.resources import ResourceBudget
from .ssh import RunResult, Transport, valid_alias

DEFAULT_WORKDIR = "~/orcadesk-runs"

# Bounds the settings normalizer clamps to (the same as the local budgets'
# in Bridge.save_settings).
_MAX_JOBS = 64
_MAX_CORES = 1024
_MAX_RAM_MB = 4_000_000

# Share of the device's RAM an "auto" budget offers, as on this machine
# (core/resources._AUTO_RAM_SHARE).
_AUTO_RAM_SHARE = 0.75

# Which calculations a device takes (Settings.remote_devices "kinds").
# "single_point" is for a device that may vanish under a job -- a spot /
# preemptible cloud VM: only kinds that never move the geometry and keep
# nothing worth resuming, so losing one costs its own run time and nothing
# else. sp, tddft and nmr are exactly the ORCA kinds of KNOWN_KINDS that read
# a fixed structure and write a result in one pass; every other ORCA kind
# walks a geometry (opt / ts_opt / irc / neb_ts), builds a Hessian
# displacement by displacement (freq), or is free text that may do either
# (general) -- a lost one is hours of progress, and a half-finished geometry
# is a trap for whatever references it.
DEVICE_KINDS = ("all", "single_point")
SINGLE_POINT_KINDS = frozenset({"sp", "tddft", "nmr"})

# A spot device unreachable this long gives its jobs back to the queue
# ("requeue_after_min", minutes; clamped). Long enough that a dropped Wi-Fi
# or a VM reboot does not throw away a run that would have come back.
DEFAULT_REQUEUE_AFTER_MIN = 5
_MIN_REQUEUE_AFTER_MIN = 1
_MAX_REQUEUE_AFTER_MIN = 1440


def device_takes(kinds: str, calc_kind: str) -> bool:
    """Whether a device whose ``kinds`` setting is ``kinds`` takes a calc of
    ``calc_kind`` (remote_capable is checked separately)."""
    if kinds == "single_point":
        return calc_kind in SINGLE_POINT_KINDS
    return True


# --------------------------------------------------------------------------
# ~/.ssh/config
# --------------------------------------------------------------------------

def default_ssh_config() -> Path:
    return Path.home() / ".ssh" / "config"


def _is_pattern(name: str) -> bool:
    """A Host entry that is a pattern (or a negation), not one device."""
    return any(ch in name for ch in "*?!")


def _split_keyword(line: str):
    """``Keyword value`` or ``Keyword=value`` -> (keyword lowercased, value)."""
    m = re.match(r"\s*([A-Za-z]+)\s*(?:=\s*|\s+)(.*)$", line)
    if not m:
        return "", ""
    return m.group(1).lower(), m.group(2).strip()


def _include_paths(value: str, base: Path) -> List[Path]:
    out: List[Path] = []
    for raw in shlex.split(value, posix=True) if value else []:
        p = os.path.expanduser(raw)
        if not os.path.isabs(p):
            # ssh resolves a relative user-config Include against ~/.ssh
            p = str(base / p)
        out.extend(Path(x) for x in sorted(glob.glob(p)))
    return out


def parse_ssh_config(path: Optional[Path] = None) -> List[str]:
    """Every concrete ``Host`` alias in ``path`` (default ``~/.ssh/config``)
    and the files it Includes, in file order, de-duplicated. Patterns
    (``*``, ``?``, ``!``) and aliases ssh could read as an option are left
    out. A missing or unreadable file is an empty list, never an error."""
    path = Path(path) if path else default_ssh_config()
    base = path.parent
    seen: Dict[str, None] = {}

    def walk(p: Path, depth: int) -> None:
        if depth > 8:
            return
        try:
            text = p.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            key, value = _split_keyword(stripped)
            if key == "include":
                for inc in _include_paths(value, base):
                    walk(inc, depth + 1)
            elif key == "host":
                try:
                    names = shlex.split(value, posix=True)
                except ValueError:
                    names = value.split()
                for n in names:
                    if _is_pattern(n) or not valid_alias(n):
                        continue
                    seen.setdefault(n, None)

    walk(path, 0)
    return list(seen)


# --------------------------------------------------------------------------
# the probe
# --------------------------------------------------------------------------

# One POSIX sh script, sent on stdin (ssh.py run). Prints key=value lines.
# ORCA is only accepted where its own helper tools sit beside it -- the same
# proof config._looks_like_orca demands locally, because GNOME's screen reader
# is ALSO called `orca` and is on PATH on every Ubuntu desktop.
_PROBE = r"""
echo "os=$(uname -s 2>/dev/null)"
echo "home=$HOME"
n=$(lscpu -p=Core,Socket 2>/dev/null | grep -v '^#' | sort -u | wc -l | tr -d ' ')
case "$n" in ''|0) n=$(nproc 2>/dev/null || echo 0) ;; esac
echo "cores=$n"
echo "mem_total_kb=$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null)"
echo "mem_avail_kb=$(awk '/^MemAvailable:/{print $2}' /proc/meminfo 2>/dev/null)"
o=__ORCA__
if [ -z "$o" ]; then
  o=$(command -v orca 2>/dev/null)
  if [ -n "$o" ] && [ ! -x "$(dirname "$(readlink -f "$o" 2>/dev/null || echo "$o")")/orca_2mkl" ]; then o=""; fi
  if [ -z "$o" ]; then
    for c in "$HOME"/orca*/orca "$HOME"/opt/orca*/orca /opt/orca*/orca /opt/orca/orca /usr/local/orca*/orca; do
      if [ -x "$c" ] && [ -x "$(dirname "$c")/orca_2mkl" ]; then o=$c; break; fi
    done
  fi
fi
if [ -n "$o" ]; then
  if [ -x "$o" ]; then o=$(readlink -f "$o" 2>/dev/null || echo "$o"); else o="missing:$o"; fi
fi
echo "orca=$o"
echo "mpirun=$(command -v mpirun 2>/dev/null)"
echo "setsid=$(command -v setsid 2>/dev/null)"
w=__WORKDIR__
case "$w" in "~") w="$HOME" ;; "~/"*) w="$HOME/${w#\~/}" ;; esac
mkdir -p "$w" 2>/dev/null
echo "workdir=$w"
echo "free_kb=$(df -Pk "$w" 2>/dev/null | awk 'NR==2{print $4}')"
"""


def probe_script(workdir: str = DEFAULT_WORKDIR, orca_path: str = "") -> str:
    """The probe for one device: ``workdir`` is where its runs will go (``~``
    expands on the device), ``orca_path`` a path the user set by hand (empty =
    look for it). Both are quoted for the remote shell."""
    w = (workdir or DEFAULT_WORKDIR).strip()
    # keep a leading ~ unquoted-equivalent: it is expanded by the script's
    # own case statement, so quote the literal and let the script rewrite it
    return (_PROBE.replace("__ORCA__", shlex.quote(orca_path or ""))
                  .replace("__WORKDIR__", shlex.quote(w)))


_ORCA_VERSION_RE = re.compile(r"orca[_-]?(\d+)[._](\d+)[._](\d+)", re.IGNORECASE)


def orca_version_from_path(path: str) -> str:
    """The version an ORCA install's folder name carries
    (``orca_6_1_1_linux_x86-64_shared_openmpi418`` -> ``6.1.1``), or "".

    UNVERIFIED (P3): read from the tarball's folder name, which is how ORCA
    for Linux ships; an install renamed by hand reports no version. Asking
    the binary itself is left out until the right flag is checked against a
    real install."""
    m = _ORCA_VERSION_RE.search(path or "")
    return ".".join(m.groups()) if m else ""


@dataclass
class DeviceInfo:
    """One device as the probe last saw it."""
    alias: str
    state: str = "checking"       # "checking" | "ready" | "error"
    message: str = ""             # plain-language reason when not ready
    os: str = ""
    cores: int = 0
    mem_total_mb: int = 0
    mem_avail_mb: int = 0
    orca_path: str = ""
    orca_version: str = ""
    has_mpi: bool = False
    has_setsid: bool = False
    workdir: str = ""             # resolved on the device ("~" expanded)
    free_disk_mb: int = 0
    checked_at: float = 0.0

    @property
    def usable(self) -> bool:
        return self.state == "ready"

    def to_payload(self) -> dict:
        d = asdict(self)
        d.pop("checked_at", None)
        return d


def _plain_failure(alias: str, res: RunResult) -> str:
    """What went wrong, in words a user who does not know SSH can act on."""
    err = (res.err or "").strip()
    low = err.lower()
    if res.rc == 127 or "ssh-not-found" in low:
        return "The OpenSSH client (ssh) was not found on this computer."
    if "remote host identification has changed" in low:
        # a static IP keeps the ~/.ssh/config entry working across a stop /
        # start, but a REPLACED instance at that address has a new host key,
        # which BatchMode refuses
        return ("The device's host key changed (a replaced cloud instance has a "
                f"new one). Run \"ssh {alias}\" once in a terminal: it says how "
                "to remove the old key; then confirm the new one.")
    if "host key verification failed" in low or "no matching host key" in low:
        return (f"This computer has not confirmed the device yet: run "
                f"\"ssh {alias}\" once in a terminal and answer yes.")
    if "permission denied" in low:
        return (f"The device refused the key. \"ssh {alias}\" must log in "
                "without asking for a password.")
    if res.rc == 124 or any(s in low for s in (
            "could not resolve", "timed out", "no route", "connection refused",
            "network is unreachable", "connection closed", "connection reset")):
        return "Could not reach the device. Is it switched on?"
    first = err.splitlines()[0] if err else f"exit status {res.rc}"
    return f"Could not connect: {first}"


def _int(v: str) -> int:
    try:
        return int(str(v).strip() or 0)
    except ValueError:
        return 0


def interpret_probe(alias: str, res: RunResult) -> DeviceInfo:
    """A DeviceInfo from the probe's result (or its failure)."""
    info = DeviceInfo(alias=alias, checked_at=time.time())
    if not res.ok:
        info.state = "error"
        info.message = _plain_failure(alias, res)
        return info
    kv: Dict[str, str] = {}
    for line in res.out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            kv[k.strip()] = v.strip()
    info.os = kv.get("os", "")
    info.cores = _int(kv.get("cores", "0"))
    info.mem_total_mb = _int(kv.get("mem_total_kb", "0")) // 1024
    info.mem_avail_mb = _int(kv.get("mem_avail_kb", "0")) // 1024
    orca = kv.get("orca", "")
    info.has_mpi = bool(kv.get("mpirun"))
    info.has_setsid = bool(kv.get("setsid"))
    info.workdir = kv.get("workdir", "")
    info.free_disk_mb = _int(kv.get("free_kb", "0")) // 1024
    if info.os != "Linux":
        info.state = "error"
        info.message = ("Only Linux devices can run calculations"
                        + (f" (this one reports {info.os})." if info.os else "."))
        return info
    if orca.startswith("missing:"):
        info.state = "error"
        info.message = f"No ORCA at {orca[len('missing:'):]} on the device."
        return info
    if not orca:
        info.state = "error"
        info.message = "ORCA was not found on the device. Install it there, or set its path."
        return info
    info.orca_path = orca
    info.orca_version = orca_version_from_path(orca)
    if not info.has_setsid:
        info.state = "error"
        info.message = "The device has no setsid, which ORCAdesk needs to start a calculation."
        return info
    if not info.workdir:
        info.state = "error"
        info.message = "Could not use the run folder on the device."
        return info
    info.state = "ready"
    return info


def probe(transport: Transport, alias: str, workdir: str = DEFAULT_WORKDIR,
          orca_path: str = "", timeout: float = 30.0) -> DeviceInfo:
    """Probe one device. Never raises."""
    if not valid_alias(alias):
        return DeviceInfo(alias=alias, state="error", checked_at=time.time(),
                          message="Not a usable SSH host name.")
    return interpret_probe(alias, transport.run(alias, probe_script(workdir, orca_path),
                                                timeout=timeout))


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------

def _clamp(v, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError, OverflowError):
        return lo


def normalize_device(raw: dict) -> Optional[dict]:
    """One ``Settings.remote_devices`` entry in its exact shape, or None when
    it cannot be one (no usable alias). The trust boundary for both a
    hand-edited settings.json and the bridge's ``set_remote_device``."""
    if not isinstance(raw, dict):
        return None
    alias = raw.get("alias")
    if not isinstance(alias, str) or not valid_alias(alias):
        return None
    orca = raw.get("orca_path", "")
    work = raw.get("workdir", DEFAULT_WORKDIR)
    kinds = raw.get("kinds", "all")
    requeue = raw.get("requeue_after_min", DEFAULT_REQUEUE_AFTER_MIN)
    if requeue is None or isinstance(requeue, bool):
        requeue = DEFAULT_REQUEUE_AFTER_MIN
    return {
        "alias": alias,
        # only a real true: "false" (a string) must not switch a device on
        "enabled": raw.get("enabled") is True,
        "max_jobs": _clamp(raw.get("max_jobs", 0), 0, _MAX_JOBS),
        "cores": _clamp(raw.get("cores", 0), 0, _MAX_CORES),
        "ram_mb": _clamp(raw.get("ram_mb", 0), 0, _MAX_RAM_MB),
        "orca_path": orca.strip() if isinstance(orca, str) else "",
        "workdir": (work.strip() if isinstance(work, str) else "") or DEFAULT_WORKDIR,
        # an unknown word is "all" -- today's behaviour, never a silent lock-out
        "kinds": kinds if kinds in DEVICE_KINDS else "all",
        "spot": raw.get("spot") is True,
        "requeue_after_min": _clamp(requeue, _MIN_REQUEUE_AFTER_MIN, _MAX_REQUEUE_AFTER_MIN),
    }


def normalize_devices(raw) -> List[dict]:
    """``Settings.remote_devices`` coerced to a list of valid, unique entries."""
    if not isinstance(raw, list):
        return []
    out: List[dict] = []
    seen = set()
    for r in raw:
        d = normalize_device(r)
        if d is None or d["alias"] in seen:
            continue
        seen.add(d["alias"])
        out.append(d)
    return out


# --------------------------------------------------------------------------
# a device as the queue engine sees it
# --------------------------------------------------------------------------

@dataclass
class RemoteDevice:
    """One enabled device, resolved for a run: its budget (from the user's
    caps, else the probe), where ORCA and the runs live on it, and what it can
    do. ``online`` / ``mem_avail_mb`` are refreshed during a run."""
    alias: str
    budget: ResourceBudget
    orca_path: str
    workdir: str                  # absolute, on the device
    has_mpi: bool = False
    online: bool = False
    mem_avail_mb: int = 0
    kinds: str = "all"            # DEVICE_KINDS
    spot: bool = False            # may vanish: its jobs are re-queued when it does
    requeue_after_min: int = DEFAULT_REQUEUE_AFTER_MIN
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False,
                                 compare=False)


def resolve_device(entry: dict, info: Optional[DeviceInfo]) -> RemoteDevice:
    """An enabled settings entry plus its latest probe -> a RemoteDevice. A
    device never probed (or whose probe failed) is offline: it takes no job
    until a later probe finds it ready."""
    ready = info is not None and info.usable
    cores = entry.get("cores") or (info.cores if ready else 0) or 1
    total = info.mem_total_mb if ready else 0
    ram = entry.get("ram_mb") or (int(total * _AUTO_RAM_SHARE) if total else 1024)
    jobs = int(entry.get("max_jobs") or 0) or max(1, cores)
    return RemoteDevice(
        alias=entry["alias"],
        budget=ResourceBudget(max_jobs=jobs, cores=int(cores), ram_mb=int(ram)),
        orca_path=(entry.get("orca_path") or (info.orca_path if ready else "")),
        workdir=(info.workdir if ready else ""),
        has_mpi=bool(info.has_mpi) if ready else False,
        online=ready,
        mem_avail_mb=(info.mem_avail_mb if ready else 0),
        kinds=entry.get("kinds", "all") if entry.get("kinds") in DEVICE_KINDS else "all",
        spot=entry.get("spot") is True,
        requeue_after_min=_clamp(entry.get("requeue_after_min", DEFAULT_REQUEUE_AFTER_MIN),
                                 _MIN_REQUEUE_AFTER_MIN, _MAX_REQUEUE_AFTER_MIN),
    )


# --------------------------------------------------------------------------
# background probing
# --------------------------------------------------------------------------

class DeviceMonitor:
    """The latest probe per alias, refreshed on daemon threads.

    ``on_ready(alias, info)`` runs (on the probe thread) whenever a probe finds
    a device usable -- the hook the store's remote clean-up list hangs off, so
    a cancel left behind on a device that was off is tidied when it is back
    (design §6.3, §7)."""

    def __init__(self, transport: Transport,
                 on_ready: Optional[Callable[[str, DeviceInfo], None]] = None):
        self.transport = transport
        self._on_ready = on_ready
        self._lock = threading.Lock()
        self._info: Dict[str, DeviceInfo] = {}
        self._seq: Dict[str, int] = {}

    def refresh(self, entries: Iterable[dict], wait: bool = False) -> None:
        """Probe each ``{alias, workdir, orca_path}`` entry (a settings entry,
        or a bare alias's defaults). ``wait`` blocks until all are done (the
        engine's periodic re-probe runs on its own thread already)."""
        threads = []
        for e in entries:
            alias = e.get("alias", "")
            with self._lock:
                self._seq[alias] = seq = self._seq.get(alias, 0) + 1
                prev = self._info.get(alias)
                if prev is None or prev.state != "ready":
                    self._info[alias] = DeviceInfo(alias=alias, state="checking")
            t = threading.Thread(target=self._one, args=(dict(e), seq), daemon=True,
                                 name=f"orcadesk-probe-{alias}")
            t.start()
            threads.append(t)
        if wait:
            for t in threads:
                t.join()

    def _one(self, entry: dict, seq: int) -> None:
        alias = entry.get("alias", "")
        info = probe(self.transport, alias, entry.get("workdir") or DEFAULT_WORKDIR,
                     entry.get("orca_path") or "")
        with self._lock:
            if self._seq.get(alias) != seq:
                return        # a newer probe of this alias was started
            self._info[alias] = info
        if info.usable and self._on_ready is not None:
            try:
                self._on_ready(alias, info)
            except Exception:
                pass

    def get(self, alias: str) -> Optional[DeviceInfo]:
        with self._lock:
            return self._info.get(alias)

    def snapshot(self) -> Dict[str, DeviceInfo]:
        with self._lock:
            return dict(self._info)

    def forget(self, alias: str) -> None:
        with self._lock:
            self._info.pop(alias, None)
            self._seq[alias] = self._seq.get(alias, 0) + 1
