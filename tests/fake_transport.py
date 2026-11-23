"""A fake SSH transport for the remote-device tests: an in-memory remote
filesystem and one remote process, driven by recognising the scripts
orcamgr/remote/runner.py and devices.py send. Nothing here opens a socket."""
from __future__ import annotations

import base64
import posixpath
import re
import shlex
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional

from orcamgr.remote.ssh import RC_SSH, RunResult


def _cd_target(script: str) -> str:
    for line in script.splitlines():
        line = line.strip()
        if line.startswith("cd "):
            return shlex.split(line[3:].split("||")[0].split("2>")[0])[0]
    return ""


def _quoted_after(script: str, keyword: str) -> str:
    m = re.search(keyword + r"\s+--\s+(\S+)", script)
    return shlex.split(m.group(1))[0] if m else ""


class FakeTransport:
    def __init__(self):
        self.remote: Dict[str, bytes] = {}
        self.reachable = True
        self.alive = True              # the remote ORCA process
        self.pid, self.start = 4242, 999
        self.idle = 0.0
        self.calls: List[str] = []
        self.syncs = 0
        self.fail_put = False
        self.fail_get_times = 0
        self.probe_out = ""
        # what crest/env.py's capability probe prints for the device's CREST
        self.crest_probe_out = "PROBE_RC=124\nPROBE_CRASH=0\n"
        self.crest_probes = 0
        self.liveness_script: Optional[List[Optional[bool]]] = None
        self.on_launch: Optional[Callable[["FakeTransport", str], None]] = None
        self._lock = threading.Lock()
        self._dirs: set = set()

    def available(self) -> bool:
        return True

    # -- helpers for tests --
    def write(self, path: str, data: bytes) -> None:
        self.remote[path] = data

    def files_under(self, d: str) -> Dict[str, bytes]:
        d = d.rstrip("/") + "/"
        return {k[len(d):]: v for k, v in self.remote.items() if k.startswith(d)}

    # -- Transport --
    def run(self, alias: str, script: str, timeout: float = 30.0, stop=None) -> RunResult:
        with self._lock:
            self.calls.append(script)
        if not self.reachable:
            return RunResult(RC_SSH, "", "ssh: connect to host x port 22: Connection timed out")
        if script.startswith("mkdir -p"):
            self._dirs.add(shlex.split(script.split("--", 1)[1])[0])
            return RunResult(0)
        if "setsid sh -c" in script:
            d = _cd_target(script)
            self.alive = True
            if self.on_launch:
                self.on_launch(self, d)
            return RunResult(0, f"pid={self.pid}\nstart={self.start}\n")
        if "sig TERM" in script:            # the POSIX kill script
            self.alive = False
            return RunResult(0, "killed\n")
        if "| base64" in script:
            # a sync: liveness, then the .out's bytes from the offset, base64
            self.syncs += 1
            live = self._live()
            if live is None:
                return RunResult(RC_SSH, "", "timeout")
            m = re.search(r"tail -c \+(\d+) -- (\S+) .*head -c (\d+)", script)
            start, path, cap = int(m.group(1)), shlex.split(m.group(2))[0], int(m.group(3))
            data = self.remote.get(path, b"")[start - 1:start - 1 + cap]
            return RunResult(0, ("alive\n" if live else "dead\n")
                             + base64.encodebytes(data).decode("ascii"))
        if "echo alive" in script:
            if self.liveness_script:
                v = self.liveness_script.pop(0)
                if v is None:
                    return RunResult(RC_SSH, "", "timeout")
                return RunResult(0, "alive\n" if v else "dead\n")
            return RunResult(0, "alive\n" if self.alive else "dead\n")
        if "find . -type f" in script:
            d = _cd_target(script)
            files = self.files_under(d)
            if not files and not any(k.startswith(d.rstrip("/") + "/") for k in self.remote) \
                    and d not in self.dirs():
                return RunResult(3, "", "no such dir")
            out = "".join(f"{len(v)}\t{k}\n" for k, v in sorted(files.items())
                          if not posixpath.basename(k).startswith(".orcadesk"))
            return RunResult(0, out)
        if "realpath" in script:
            d = re.search(r"realpath -- (\S+) ", script).group(1)
            d = shlex.split(d)[0]
            if d not in self.dirs() and not self.files_under(d):
                return RunResult(0, "gone\n")
            for k in list(self.remote):
                if k.startswith(d.rstrip("/") + "/"):
                    del self.remote[k]
            self._dirs.discard(d)
            return RunResult(0, "removed\n")
        if "find . -maxdepth 1" in script:
            return RunResult(0, f"{int(self.idle)}\n")
        if "PROBE_RC" in script:
            # crest/env.py's capability probe for the device's CREST
            self.crest_probes += 1
            return RunResult(0, self.crest_probe_out)
        if "uname -s" in script:
            return RunResult(0, self.probe_out)
        return RunResult(0)

    def _live(self):
        """The scripted liveness answer (None = unreachable), else the state."""
        if self.liveness_script:
            return self.liveness_script.pop(0)
        return self.alive

    def dirs(self) -> set:
        return self._dirs

    def put(self, alias: str, pairs, timeout: float = 600.0, stop=None) -> RunResult:
        if not self.reachable or self.fail_put:
            return RunResult(RC_SSH, "", "connection lost")
        for local, remote in pairs:
            self.remote[remote] = Path(local).read_bytes()
            self._dirs.add(posixpath.dirname(remote))
        return RunResult(0)

    def get(self, alias: str, pairs, timeout: float = 3600.0, stop=None) -> RunResult:
        if not self.reachable:
            return RunResult(RC_SSH, "", "connection lost")
        if self.fail_get_times > 0:
            self.fail_get_times -= 1
            return RunResult(RC_SSH, "", "connection lost")
        for remote, local in pairs:
            Path(local).write_bytes(self.remote[remote])
        return RunResult(0)


class FakeClock:
    """A monotonic clock that advances only when slept on."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += max(s, 0.01)
