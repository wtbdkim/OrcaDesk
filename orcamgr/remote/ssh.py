"""
The SSH transport: the system OpenSSH client (``ssh`` / ``sftp``), driven
non-interactively.

**The user's SSH setup is the user's** (design D1): keys, ``~/.ssh/config``,
``known_hosts`` and any ProxyJump all stay theirs, and ORCAdesk only ever names
a ``Host`` alias from that config. Nothing here stores a password or a key.

Every call runs with ``BatchMode=yes``: a password prompt or an unknown host
key ends the call with an error instead of waiting on a terminal nobody is
looking at. That error is returned as data (P6) -- a transport failure must
never raise out of a queue job, because the one place it matters (a liveness
check on a multi-hour run) exists precisely so a dropped connection cannot
condemn a healthy calculation.

Windows ships OpenSSH in System32 (Windows 10 1809+); there is no connection
multiplexing there (ControlMaster is unsupported on Win32-OpenSSH), so every
call pays a full handshake (~0.5-2 s). The runner is written for that: one
short call per job every 10 s (liveness and the .out's new bytes together),
and no connection held open in between.

Qt-free.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol, Tuple

# What a Host alias may look like before it is handed to ssh as an argument.
# A leading "-" would be read as an option (ssh -oProxyCommand=... is a shell),
# so it is refused here whatever ~/.ssh/config says (design §4, §10).
_ALIAS_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._-]*$")

# The options every call carries. ConnectTimeout bounds an unreachable device;
# ServerAlive* notices a connection that died under a long transfer.
_COMMON_OPTS = (
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=8",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=3",
)

# rc values for failures of the transport itself (not of the remote command):
# ssh's own 255, and the three this module returns when it could not even run.
RC_SSH = 255
RC_TIMEOUT = 124
RC_ERROR = 125
RC_MISSING = 127
RC_ABORTED = 130      # the caller's stop event fired (app closing): unknown
RC_BUSY = 122         # no free connection slot on THIS machine in time: the
                      # device was never asked -- not an outage, not unreachable


# A Windows device (Windows OpenSSH Server, whose default shell is cmd.exe)
# runs its scripts in PowerShell. The command line carries only this short,
# fixed bootstrap, base64 UTF-16LE in -EncodedCommand -- nothing for cmd.exe to
# re-quote, and far under its 8191-character limit whatever the script's
# size. The script itself arrives on stdin as base64 of its UTF-8 text, and
# its output leaves as UTF-8 bytes written straight to the stdout stream:
# PowerShell's own console output follows the OEM code page, which turned a
# Korean user-profile path into mojibake (measured, Windows 11, PowerShell
# 5.1). `exit N` inside the script ends the process with N (measured).
PS_BOOTSTRAP = (
    "$ProgressPreference='SilentlyContinue';"
    "$o=[Console]::OpenStandardOutput();"
    "$s=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadToEnd()));"
    "& ([scriptblock]::Create($s)) | ForEach-Object { "
    "$b=[Text.Encoding]::UTF8.GetBytes([string]$_ + [char]10); $o.Write($b,0,$b.Length) }; "
    "$o.Flush()")


def powershell_command() -> list:
    """The remote command (as words) that runs a script sent on stdin
    through PS_BOOTSTRAP; see powershell_stdin for what goes in."""
    import base64
    return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-EncodedCommand",
            base64.b64encode(PS_BOOTSTRAP.encode("utf-16-le")).decode("ascii")]


def powershell_stdin(script: str) -> bytes:
    """A PowerShell script as PS_BOOTSTRAP reads it from stdin."""
    import base64
    return base64.b64encode(script.encode("utf-8"))


def valid_alias(alias: str) -> bool:
    """Whether ``alias`` may be handed to ssh as a host argument."""
    return bool(alias) and isinstance(alias, str) and bool(_ALIAS_RE.match(alias))


def _system_openssh(name: str) -> str:
    """The OpenSSH tool ``name`` ("ssh" / "sftp"): System32's on Windows (the
    one Windows ships), else whatever PATH has. "" when there is none."""
    if sys.platform.startswith("win"):
        root = os.environ.get("SystemRoot") or r"C:\Windows"
        p = Path(root) / "System32" / "OpenSSH" / f"{name}.exe"
        if p.is_file():
            return str(p)
        return shutil.which(f"{name}.exe") or ""
    return shutil.which(name) or ""


def _no_window() -> int:
    """creationflags that keep a console window from flashing up on Windows."""
    if sys.platform.startswith("win"):
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


@dataclass
class RunResult:
    """What one transport call came back with. ``rc`` is the remote command's
    exit status, or one of the RC_* values when the transport failed."""
    rc: int
    out: str = ""
    err: str = ""

    @property
    def ok(self) -> bool:
        return self.rc == 0

    @property
    def unreachable(self) -> bool:
        """The call never reached a remote shell (connect/auth failed, timed
        out, no ssh) -- the answer is "unknown", not "no"."""
        return self.rc in (RC_SSH, RC_TIMEOUT, RC_ERROR, RC_MISSING, RC_ABORTED)

    @property
    def aborted(self) -> bool:
        """Given up because the caller's stop event fired -- not an outage."""
        return self.rc == RC_ABORTED

    @property
    def busy(self) -> bool:
        """This machine's own connection cap had no free slot in time: the
        device was never contacted, so it says nothing about the device.
        Deliberately NOT unreachable -- counting it as one flagged a healthy
        AWS device Disconnected and sent its launches back to the queue
        while 15 fetches held the slots (2026-11-23)."""
        return self.rc == RC_BUSY


class Transport(Protocol):
    """What the remote runner and the device probe need. ``SshTransport`` is
    the real one; tests use a fake with the same four methods."""

    def available(self) -> bool: ...

    def run(self, alias: str, script: str, timeout: float = 30.0,
            stop=None) -> RunResult: ...

    def put(self, alias: str, pairs: Iterable[Tuple[Path, str]],
            timeout: float = 600.0, stop=None) -> RunResult: ...

    def get(self, alias: str, pairs: Iterable[Tuple[str, Path]],
            timeout: float = 3600.0, stop=None) -> RunResult: ...


def sftp_quote(path: str) -> str:
    """``path`` as one sftp batch-file argument: double-quoted, with the two
    characters sftp's own parser escapes inside quotes (backslash, quote)
    escaped. Callers keep glob characters out of remote names -- see
    runner.remote_safe_name -- because sftp globs a get/put path even when it
    is quoted (unverified against every OpenSSH build, so not relied on)."""
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _local_for_sftp(p: Path) -> str:
    """A local path as sftp wants it. Win32-OpenSSH's sftp takes forward
    slashes for local paths; a backslash would be read as an escape."""
    return Path(p).as_posix()


# At most this many ssh/sftp processes at once per device. Without a cap a
# dispatch burst opened one connection per job at once -- 16 SPs starting on a
# 16-core device -- and the device's sshd dropped the overflow: OpenSSH's
# default MaxStartups 10:30:100 refuses unauthenticated connections past 10
# ("Connection closed by <host> port 22", measured on the AWS device,
# 2026-11-23; five launches failed and went back to PENDING). Win32-OpenSSH
# has no connection multiplexing to fall back on, so the client queues
# instead: a call waits for a free slot within its own timeout.
#
# Commands (sync, liveness, mkdir, launch, delete -- short) and file transfers
# (sftp put/get -- long: a whole run folder from Sydney takes tens of seconds)
# have SEPARATE slots. With one shared cap of 4, fifteen jobs finishing
# together filled every slot with fetches; the 10 s syncs and new launches
# waited past their timeout, were counted as the device not answering, and a
# healthy AWS device was flagged Disconnected and lost its launches
# (2026-11-23). 6 + 2 stays under sshd's default 10.
MAX_CONNECTIONS_PER_DEVICE = 6     # commands
MAX_TRANSFERS_PER_DEVICE = 2       # sftp put/get
_gates: "dict[tuple, threading.BoundedSemaphore]" = {}
_gates_lock = threading.Lock()


def _gate(alias: str, kind: str = "cmd") -> "threading.BoundedSemaphore":
    with _gates_lock:
        g = _gates.get((alias, kind))
        if g is None:
            n = MAX_TRANSFERS_PER_DEVICE if kind == "xfer" else MAX_CONNECTIONS_PER_DEVICE
            g = _gates[(alias, kind)] = threading.BoundedSemaphore(n)
        return g


class Busy(Exception):
    """No connection slot freed up in time on this machine (RC_BUSY)."""


class Aborted(Exception):
    """A transport call given up because its ``stop`` event was set (the app
    is closing): the child was killed, the answer is unknown."""


# How often a waiting call looks at its stop event. Closing the window used to
# wait for every remote monitor's in-flight call -- a sync may take up to 60 s,
# a fetch much longer, and the per-device cap queues the rest behind them --
# so a close with jobs on two devices froze the window past 20 s (2026-11-23).
_STOP_POLL = 0.2


def _gated_run(alias: str, argv: list, data: bytes, timeout: float, stop=None,
               kind: str = "cmd"):
    """Run ``argv`` under the device's connection cap (``kind`` "cmd" or
    "xfer"), feeding it ``data``.

    Raises Busy when no slot frees up within ``timeout`` (the device was never
    asked: RC_BUSY), subprocess.TimeoutExpired when the child does not finish
    within ``timeout`` once started -- the callers turn that into RC_TIMEOUT
    (an unknown, never a "no") -- and Aborted as soon as ``stop`` (a
    threading.Event, optional) is set, while waiting for a slot or while the
    child runs. Either way the child is killed and reaped: no ssh/sftp is
    ever left behind."""
    gate = _gate(alias, kind)
    deadline = time.monotonic() + max(1.0, timeout)
    while not gate.acquire(timeout=_STOP_POLL):
        if stop is not None and stop.is_set():
            raise Aborted()
        if time.monotonic() >= deadline:
            raise Busy()
    try:
        p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, creationflags=_no_window())
        end = time.monotonic() + timeout
        feed = data
        while True:
            try:
                # communicate() may be called again after TimeoutExpired
                # without losing output; the input goes in on the first call
                out, err = p.communicate(input=feed, timeout=_STOP_POLL)
                break
            except subprocess.TimeoutExpired:
                feed = None
                stopped = stop is not None and stop.is_set()
                if stopped or time.monotonic() >= end:
                    p.kill()
                    try:
                        p.communicate(timeout=5)
                    except (subprocess.SubprocessError, OSError, ValueError):
                        pass
                    if stopped:
                        raise Aborted()
                    raise subprocess.TimeoutExpired(argv, timeout)
        return subprocess.CompletedProcess(argv, p.returncode, out, err)
    finally:
        gate.release()


class SshTransport:
    """The real transport over the system OpenSSH client."""

    def __init__(self, ssh: str = "", sftp: str = ""):
        self.ssh = ssh or _system_openssh("ssh")
        self.sftp = sftp or _system_openssh("sftp")

    def available(self) -> bool:
        if not self.ssh:
            return False
        return Path(self.ssh).exists() if os.path.isabs(self.ssh) else True

    # -- one command ---------------------------------------------------------
    def run(self, alias: str, script: str, timeout: float = 30.0,
            stop=None) -> RunResult:
        """Run ``script`` with ``sh -s`` on ``alias`` (the script goes in on
        stdin, so nothing in it is ever parsed by a local shell or by ssh's
        own argument joining). ``stop`` (a threading.Event) abandons the call
        at once -- RC_ABORTED -- and kills the ssh child."""
        if not valid_alias(alias):
            return RunResult(RC_ERROR, "", f"not a usable host alias: {alias!r}")
        if not self.ssh:
            return RunResult(RC_MISSING, "", "ssh-not-found")
        argv = [self.ssh, *_COMMON_OPTS, alias, "sh", "-s"]
        try:
            p = _gated_run(alias, argv, script.encode("utf-8"), timeout, stop)
        except Aborted:
            return RunResult(RC_ABORTED, "", "aborted")
        except Busy:
            return RunResult(RC_BUSY, "", "busy: no free connection slot")
        except FileNotFoundError:
            return RunResult(RC_MISSING, "", "ssh-not-found")
        except subprocess.TimeoutExpired:
            return RunResult(RC_TIMEOUT, "", "timeout")
        except (subprocess.SubprocessError, OSError) as e:
            return RunResult(RC_ERROR, "", f"ssh-error: {e}")
        return RunResult(p.returncode,
                         (p.stdout or b"").decode("utf-8", "replace"),
                         (p.stderr or b"").decode("utf-8", "replace"))

    def run_powershell(self, alias: str, script: str, timeout: float = 30.0,
                       stop=None) -> RunResult:
        """Run a PowerShell ``script`` on a Windows ``alias`` (see
        PS_BOOTSTRAP). Same contract as run: never raises."""
        if not valid_alias(alias):
            return RunResult(RC_ERROR, "", f"not a usable host alias: {alias!r}")
        if not self.ssh:
            return RunResult(RC_MISSING, "", "ssh-not-found")
        argv = [self.ssh, *_COMMON_OPTS, alias, *powershell_command()]
        try:
            p = _gated_run(alias, argv, powershell_stdin(script), timeout, stop)
        except Aborted:
            return RunResult(RC_ABORTED, "", "aborted")
        except Busy:
            return RunResult(RC_BUSY, "", "busy: no free connection slot")
        except FileNotFoundError:
            return RunResult(RC_MISSING, "", "ssh-not-found")
        except subprocess.TimeoutExpired:
            return RunResult(RC_TIMEOUT, "", "timeout")
        except (subprocess.SubprocessError, OSError) as e:
            return RunResult(RC_ERROR, "", f"ssh-error: {e}")
        return RunResult(p.returncode,
                         (p.stdout or b"").decode("utf-8", "replace"),
                         (p.stderr or b"").decode("utf-8", "replace"))

    # -- files -----------------------------------------------------------------
    def _sftp_batch(self, alias: str, lines: list, timeout: float,
                    stop=None) -> RunResult:
        if not valid_alias(alias):
            return RunResult(RC_ERROR, "", f"not a usable host alias: {alias!r}")
        if not self.sftp:
            return RunResult(RC_MISSING, "", "sftp-not-found")
        if not lines:
            return RunResult(0)
        argv = [self.sftp, *_COMMON_OPTS, "-q", "-b", "-", alias]
        batch = ("\n".join(lines) + "\n").encode("utf-8")
        try:
            p = _gated_run(alias, argv, batch, timeout, stop, kind="xfer")
        except Aborted:
            return RunResult(RC_ABORTED, "", "aborted")
        except Busy:
            return RunResult(RC_BUSY, "", "busy: no free connection slot")
        except FileNotFoundError:
            return RunResult(RC_MISSING, "", "sftp-not-found")
        except subprocess.TimeoutExpired:
            return RunResult(RC_TIMEOUT, "", "timeout")
        except (subprocess.SubprocessError, OSError) as e:
            return RunResult(RC_ERROR, "", f"sftp-error: {e}")
        # sftp in batch mode exits 1 on a failed command and 255 when the
        # connection itself failed -- keep that distinction
        return RunResult(p.returncode,
                         (p.stdout or b"").decode("utf-8", "replace"),
                         (p.stderr or b"").decode("utf-8", "replace"))

    def put(self, alias: str, pairs: Iterable[Tuple[Path, str]],
            timeout: float = 600.0, stop=None) -> RunResult:
        """Upload each (local file, remote path). The remote folders must
        exist already (the runner makes them with ``mkdir -p``)."""
        lines = [f"put {sftp_quote(_local_for_sftp(lp))} {sftp_quote(rp)}"
                 for lp, rp in pairs]
        return self._sftp_batch(alias, lines, timeout, stop)

    def get(self, alias: str, pairs: Iterable[Tuple[str, Path]],
            timeout: float = 3600.0, stop=None) -> RunResult:
        """Download each (remote file, local path), resuming a partial local
        file (``get -a``). Local folders must exist already.

        ``-a`` compares sizes only, and skips a local file that is already as
        large as the remote one -- so a caller must download into a folder of
        its own (the runner's per-launch staging folder), never straight over
        a previous run's files of the same name."""
        lines = [f"get -a {sftp_quote(rp)} {sftp_quote(_local_for_sftp(lp))}"
                 for rp, lp in pairs]
        return self._sftp_batch(alias, lines, timeout, stop)
