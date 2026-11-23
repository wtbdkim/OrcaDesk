"""
The remote scripts for a WINDOWS device -- the PowerShell counterparts of the
POSIX ones in runner.py, behind the same small interface (``Platform``), so
RemoteOrcaRunner, the fetch / verify / delete helpers and the clean-up list
drive a Windows laptop exactly as they drive a Linux VM.

Which platform a remote folder is on is read from the folder itself: the
probe resolves a device's workdir to ``C:/Users/u/orcadesk-runs`` on Windows
and ``/home/u/orcadesk-runs`` on Linux, every remote folder is a child of it,
and a persisted ``remote_dir`` (a reattach, a clean-up entry) carries the same
shape -- so no extra field, and the remote platform is what the probe found,
never this machine's (P58).

Remote paths are kept in ONE canonical form, forward slashes with the drive
(``C:/Users/u/...``): PowerShell takes it as is, and ``sftp_path`` turns it
into the ``/C:/Users/u/...`` Windows OpenSSH's sftp server expects, at the
transport edge only.

How a job runs (design §6 on Windows):

* **launch** -- ``Invoke-CimMethod Win32_Process Create`` starts
  ``cmd.exe /c ""<orca.exe>" <name>.inp > <name>.out 2>&1"`` in the run
  folder. The new process is a child of the WMI provider host, not of the SSH
  session, so it lives on after the session ends (a process started inside a
  Windows sshd session belongs to the session's job object and dies with it).
  Its pid and CreationDate (ms since the epoch, UTC) identify it, as pid +
  /proc start time do on Linux. The input goes by bare file name, as
  OrcaRunner passes it.
* **liveness** -- that pid exists with that creation time.
* **kill** -- ``taskkill /PID <pid> /T /F``: the cmd.exe and everything under
  it (orca.exe, mpiexec, the orca_* ranks).
* **sync** -- line 1 alive/dead, then base64 of the .out from the offset,
  opened FileShare.ReadWrite (ORCA is still writing it), capped.

Verified on this Windows 11 machine by running the scripts through a local
PowerShell (tests/test_remote_windows.py); over a real Windows sshd they are
UNVERIFIED until a device is set up (docs/WINDOWS_REMOTE_SETUP_KR.md).

Qt-free.
"""

from __future__ import annotations

import posixpath
import re
from typing import Optional

# The runner's own files in a remote folder (runner.META_PREFIX, repeated
# here because runner imports this module; a test pins the two together).
META_PREFIX = ".orcadesk"

_WIN_ABS_RE = re.compile(r"^[A-Za-z]:/")


def is_windows_path(path: str) -> bool:
    """A canonical Windows remote path (``C:/...``)."""
    return bool(path) and bool(_WIN_ABS_RE.match(path))


def psq(s) -> str:
    """``s`` as a single-quoted PowerShell literal (no expansion at all)."""
    return "'" + str(s).replace("'", "''") + "'"


def _backslash(path: str) -> str:
    return str(path).replace("/", "\\")


# A process's identity: its CreationDate as integer milliseconds since the
# epoch (UTC) -- exact in a float, which is how the session stores it.
_START_FN = (
    "function S($i){$p=Get-CimInstance Win32_Process -Filter \"ProcessId=$i\" "
    "-ErrorAction SilentlyContinue; if(-not $p -or -not $p.CreationDate){return ''}; "
    "[string][int64][math]::Floor(($p.CreationDate.ToUniversalTime()-"
    "[datetime]::new(1970,1,1,0,0,0,[DateTimeKind]::Utc)).TotalMilliseconds)}\n")


def _start_str(start) -> str:
    try:
        return str(int(float(start)))
    except (TypeError, ValueError):
        return ""


def guard_windows_dir(remote_dir: str, workdir: str) -> bool:
    """The local half of the delete guard for a Windows device: both paths
    canonical (``X:/...``), ``remote_dir`` already normal (no ``..``, no
    doubled or trailing separators) and strictly below a ``workdir`` that is
    not a drive root -- compared without regard to case, as NTFS does. The
    remote half re-checks the full paths before Remove-Item."""
    if not is_windows_path(remote_dir) or not is_windows_path(workdir):
        return False
    r = remote_dir
    w = workdir.rstrip("/")
    if ".." in r.split("/") or ".." in w.split("/"):
        return False
    if posixpath.normpath(r) != r.rstrip("/") or r != r.rstrip("/"):
        return False
    if len(w) <= 3:          # "C:" / "C:/" -- a drive root is never a workdir
        return False
    return r.lower().startswith(w.lower() + "/") and len(r) > len(w) + 1


class WindowsPlatform:
    """PowerShell scripts for a Windows device (see the module docstring)."""

    name = "windows"

    @staticmethod
    def run(transport, alias: str, script: str, timeout: float = 30.0, stop=None):
        """Send ``script`` the way this platform runs one. A transport with no
        PowerShell path (a POSIX-only fake) answers as a shell that cannot.
        ``stop`` abandons the call (see ssh._gated_run)."""
        fn = getattr(transport, "run_powershell", None)
        if fn is None:
            from .ssh import RC_ERROR, RunResult
            return RunResult(RC_ERROR, "", "this transport cannot run PowerShell")
        if stop is None:
            return fn(alias, script, timeout=timeout)
        return fn(alias, script, timeout=timeout, stop=stop)

    @staticmethod
    def sftp_path(path: str) -> str:
        """``C:/Users/u/x`` -> ``/C:/Users/u/x`` (Windows OpenSSH sftp)."""
        return "/" + path if is_windows_path(path) else path

    guard = staticmethod(guard_windows_dir)

    @staticmethod
    def mkdir(remote_dir: str) -> str:
        return f"[void][IO.Directory]::CreateDirectory({psq(_backslash(remote_dir))})\n"

    @staticmethod
    def launch(remote_dir: str, orca: str, name: str) -> str:
        cmd = ('cmd.exe /c ""' + _backslash(orca) + '" ' + name + ".inp > "
               + name + '.out 2>&1"')
        return (
            _START_FN
            + f"$d={psq(_backslash(remote_dir))}\n"
            "if(-not (Test-Path -LiteralPath $d -PathType Container)){exit 3}\n"
            f"$r=Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
            f"-Arguments @{{CommandLine={psq(cmd)}; CurrentDirectory=$d}}\n"
            "if($r.ReturnValue -ne 0){exit 4}\n"
            "$s=S $r.ProcessId; if(-not $s){$s='0'}\n"
            "\"pid=$($r.ProcessId)\"\n"
            "\"start=$s\"\n")

    @staticmethod
    def liveness(pid: int, start) -> str:
        st = psq(_start_str(start))
        return (_START_FN
                + f"$w={st}; if($w -ne '' -and (S {int(pid)}) -eq $w){{'alive'}}else{{'dead'}}\n")

    @classmethod
    def sync(cls, pid: int, start, remote_out: str, offset: int, cap: int) -> str:
        return (cls.liveness(pid, start)
                + f"$f={psq(_backslash(remote_out))}; $o=[int64]{int(offset)}; "
                  f"$c=[int64]{int(cap)}; $b=[byte[]]@()\n"
                "try{$fs=[IO.File]::Open($f,[IO.FileMode]::Open,[IO.FileAccess]::Read,"
                "[IO.FileShare]::ReadWrite)\n"
                " try{$n=[Math]::Min($c,$fs.Length-$o); if($n -gt 0){$fs.Position=$o; "
                "$b=New-Object byte[] $n; $r=0; while($r -lt $n){$k=$fs.Read($b,$r,$n-$r); "
                "if($k -le 0){break}; $r+=$k}; if($r -lt $n){$t=New-Object byte[] $r; "
                "[Array]::Copy($b,$t,$r); $b=$t}}} finally {$fs.Close()}} catch {}\n"
                "[Convert]::ToBase64String($b)\n")

    @staticmethod
    def kill(pid: int, start) -> str:
        p = int(pid)
        return (_START_FN
                + f"$w={psq(_start_str(start))}\n"
                f"if($w -eq '' -or (S {p}) -ne $w){{'gone'; exit 0}}\n"
                f"& taskkill.exe /PID {p} /T /F *> $null\n"
                "for($i=0;$i -lt 20;$i++){"
                f"if((S {p}) -ne $w){{'killed'; exit 0}}; Start-Sleep -Milliseconds 500}}\n"
                "'alive'; exit 1\n")

    @staticmethod
    def idle(remote_dir: str) -> str:
        return (f"$d={psq(_backslash(remote_dir))}\n"
                "if(-not (Test-Path -LiteralPath $d -PathType Container)){exit 3}\n"
                "$i=Get-ChildItem -LiteralPath $d -Force -ErrorAction SilentlyContinue "
                "| Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1\n"
                "if(-not $i){'0'; exit 0}\n"
                "[string][int64]([DateTime]::UtcNow-$i.LastWriteTimeUtc).TotalSeconds\n")

    @staticmethod
    def listing(remote_dir: str) -> str:
        return (f"$d={psq(_backslash(remote_dir))}\n"
                "if(-not (Test-Path -LiteralPath $d -PathType Container)){exit 3}\n"
                "$root=(Get-Item -LiteralPath $d -Force).FullName.TrimEnd('\\')\n"
                "Get-ChildItem -LiteralPath $root -Recurse -File -Force | "
                f"Where-Object {{ -not $_.Name.StartsWith({psq(META_PREFIX)}) }} | "
                "ForEach-Object { [string]$_.Length + [char]9 + "
                "$_.FullName.Substring($root.Length+1).Replace('\\','/') }\n")

    @staticmethod
    def remove(remote_dir: str, workdir: str) -> str:
        # GetFullPath folds "..", "." and doubled separators; the folder must
        # sit strictly below the workdir, and the workdir is never a drive root
        return (f"$r={psq(_backslash(remote_dir))}; $w={psq(_backslash(workdir))}\n"
                "if(-not (Test-Path -LiteralPath $r)){'gone'; exit 0}\n"
                "$rf=[IO.Path]::GetFullPath($r).TrimEnd('\\'); "
                "$wf=[IO.Path]::GetFullPath($w).TrimEnd('\\')\n"
                "if($wf.Length -le 3 -or $rf.Length -le $wf.Length+1 -or "
                "-not $rf.StartsWith($wf+'\\',[StringComparison]::OrdinalIgnoreCase)){exit 3}\n"
                "Remove-Item -LiteralPath $rf -Recurse -Force -ErrorAction Stop\n"
                "'removed'\n")


WINDOWS = WindowsPlatform()


# --------------------------------------------------------------------------
# the probe
# --------------------------------------------------------------------------

# The Windows probe, sent when `sh -s` fails (cmd.exe has no sh) or answers
# from an MSYS/Cygwin shell. Same key=value lines as the POSIX probe (with
# "launcher" in place of setsid). ORCA is accepted only with orca_2mkl.exe
# beside it, the proof config._looks_like_orca demands locally.
_WIN_PROBE = r"""
$ErrorActionPreference='SilentlyContinue'
"os=Windows"
"home=$($env:USERPROFILE -replace '\\','/')"
$c=(Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfCores -Sum).Sum
"cores=$c"
$m=Get-CimInstance Win32_OperatingSystem
"mem_total_kb=$($m.TotalVisibleMemorySize)"
"mem_avail_kb=$($m.FreePhysicalMemory)"
function Ok($p){ $p -and (Test-Path -LiteralPath $p -PathType Leaf) -and (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $p) 'orca_2mkl.exe')) }
$o=__ORCA__
if($o){ if(Test-Path -LiteralPath $o -PathType Leaf){ $o=(Get-Item -LiteralPath $o).FullName } else { $o="missing:$o" } }
else {
  $g=Get-Command orca.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if($g -and (Ok $g.Source)){ $o=$g.Source }
  else {
    $cands=@(Get-ChildItem -Path 'C:\ORCA*\orca.exe','C:\Program Files\ORCA*\orca.exe',(Join-Path $env:USERPROFILE 'ORCA*\orca.exe'),(Join-Path $env:USERPROFILE 'orca*\orca.exe') -ErrorAction SilentlyContinue)
    foreach($x in $cands){ if(Ok $x.FullName){ $o=$x.FullName; break } }
  }
}
"orca=$($o -replace '\\','/')"
$mp=Get-Command mpiexec.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if($mp){ $mp=$mp.Source } elseif($env:MSMPI_BIN -and (Test-Path (Join-Path $env:MSMPI_BIN 'mpiexec.exe'))){ $mp=Join-Path $env:MSMPI_BIN 'mpiexec.exe' } elseif(Test-Path 'C:\Program Files\Microsoft MPI\Bin\mpiexec.exe'){ $mp='C:\Program Files\Microsoft MPI\Bin\mpiexec.exe' } else { $mp='' }
"mpirun=$($mp -replace '\\','/')"
"launcher=wmi"
$w=__WORKDIR__
if($w -eq '~'){ $w=$env:USERPROFILE } elseif($w.StartsWith('~/') -or $w.StartsWith('~\')){ $w=Join-Path $env:USERPROFILE $w.Substring(2) }
$w=$w -replace '/','\'
[void][IO.Directory]::CreateDirectory($w)
if(Test-Path -LiteralPath $w -PathType Container){
  $w=(Get-Item -LiteralPath $w -Force).FullName.TrimEnd('\')
  "workdir=$($w -replace '\\','/')"
  $dr=Get-PSDrive -Name $w.Substring(0,1) -ErrorAction SilentlyContinue
  if($dr){ "free_kb=$([int64]($dr.Free/1024))" }
}
"""


def windows_probe_script(workdir: str, orca_path: str = "") -> str:
    """The Windows probe for one device (``~`` expands to USERPROFILE)."""
    return (_WIN_PROBE.replace("__ORCA__", psq(_backslash(orca_path or "")))
                      .replace("__WORKDIR__", psq(workdir or "~/orcadesk-runs")))


def looks_like_windows_shell(out_os: str) -> bool:
    """A POSIX probe that answered from Git-for-Windows / MSYS / Cygwin bash:
    the machine is Windows all the same, and is probed as one."""
    low = (out_os or "").lower()
    return low.startswith(("mingw", "msys", "cygwin", "windows"))


def platform_name(path: Optional[str]) -> str:
    return "windows" if is_windows_path(path or "") else "posix"
