"""A transport that is a Windows "device" on THIS machine: the PowerShell
scripts of orcamgr/remote/winplatform.py run through the very bootstrap
SshTransport.run_powershell sends over ssh (ssh.powershell_command /
powershell_stdin), only without ssh in between. ``run`` answers as Windows
OpenSSH's cmd.exe does to ``sh -s``; ``put`` / ``get`` copy files, taking the
``/C:/...`` paths sftp to Windows OpenSSH would. Lets the Windows scripts be
tested for real on a Windows dev machine that has no sshd (and no admin
rights to install one)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from orcamgr.remote.ssh import RC_ERROR, RC_TIMEOUT, RunResult, powershell_command, powershell_stdin


def _local(sftp_path: str) -> Path:
    # "/C:/Users/..." -> "C:/Users/..."
    p = sftp_path[1:] if len(sftp_path) > 3 and sftp_path[0] == "/" and sftp_path[2] == ":" else sftp_path
    return Path(p)


class LocalPowerShellTransport:
    def __init__(self):
        self.calls = []

    def available(self) -> bool:
        return True

    def run(self, alias: str, script: str, timeout: float = 30.0) -> RunResult:
        self.calls.append(("sh", script))
        return RunResult(1, "", "'sh' is not recognized as an internal or external command,\r\n"
                                "operable program or batch file.\r\n")

    def run_powershell(self, alias: str, script: str, timeout: float = 30.0) -> RunResult:
        self.calls.append(("ps", script))
        try:
            p = subprocess.run(powershell_command(), input=powershell_stdin(script),
                               capture_output=True, timeout=timeout,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return RunResult(RC_TIMEOUT, "", "timeout")
        except OSError as e:
            return RunResult(RC_ERROR, "", str(e))
        return RunResult(p.returncode, p.stdout.decode("utf-8", "replace"),
                         p.stderr.decode("utf-8", "replace"))

    def put(self, alias: str, pairs, timeout: float = 600.0) -> RunResult:
        for local, remote in pairs:
            dest = _local(remote)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local, dest)
        return RunResult(0)

    def get(self, alias: str, pairs, timeout: float = 3600.0) -> RunResult:
        for remote, local in pairs:
            src = _local(remote)
            if not src.is_file():
                return RunResult(1, "", f"no such file: {remote}")
            Path(local).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, local)
        return RunResult(0)
