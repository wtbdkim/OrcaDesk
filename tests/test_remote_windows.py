"""Windows remote devices (orcamgr/remote/winplatform.py).

Two layers:

* fake-transport tests, on any OS: the probe falls back to PowerShell when
  ``sh -s`` cannot run (Windows OpenSSH's cmd.exe) or answers from MSYS bash;
  the Windows delete guard; the sftp path form; which platform a remote folder
  is on; how the launch line quotes a path with spaces.
* real tests on a Windows machine, through tests/local_ps_transport.py: the
  PowerShell scripts run here through the very bootstrap ssh would send,
  minus ssh. A detached WMI launch, liveness, the 10 s sync from an offset,
  the listing, fetch + verify + guarded delete, and a cancel that takes the
  whole process tree down. Over a real Windows sshd these remain UNVERIFIED
  until a device is set up (docs/WINDOWS_REMOTE_SETUP_KR.md).

With ORCADESK_SMOKE=1 and ORCA at C:\\ORCA_6.1.1, a real water SP (1 and 2
MPI processes) also runs through the Windows runner here.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from orcamgr.core.parser import parse_file
from orcamgr.core.runner import OrcaCancelled
from orcamgr.remote import runner as rr
from orcamgr.remote import winplatform as wp
from orcamgr.remote.devices import probe
from orcamgr.remote.runner import (POSIX, RemoteOrcaRunner, launch_stamp, platform_for,
                                   remote_dir_for, remote_liveness, safe_remove)
from orcamgr.remote.ssh import RC_SSH, RunResult
from orcamgr.remote.winplatform import WINDOWS, guard_windows_dir

ON_WINDOWS = sys.platform.startswith("win")
local_only = pytest.mark.skipif(not ON_WINDOWS, reason="runs PowerShell on this machine")


# ---- fake-transport layer (any OS) ------------------------------------------

_WIN_PROBE_OUT = ("os=Windows\nhome=C:/Users/u\ncores=8\nmem_total_kb=33554432\n"
                  "mem_avail_kb=16777216\norca=C:/ORCA_6.1.1/orca.exe\n"
                  "mpirun=C:/Program Files/Microsoft MPI/Bin/mpiexec.exe\nlauncher=wmi\n"
                  "workdir=C:/Users/u/orcadesk-runs\nfree_kb=104857600\n")


class _Fake:
    def __init__(self, sh: RunResult, ps: RunResult = None):
        self.sh, self.ps, self.ps_calls = sh, ps, 0

    def run(self, alias, script, timeout=30.0):
        return self.sh

    def run_powershell(self, alias, script, timeout=30.0):
        self.ps_calls += 1
        return self.ps


def test_a_device_whose_shell_has_no_sh_is_probed_in_powershell():
    fake = _Fake(RunResult(1, "", "'sh' is not recognized as an internal or external command"),
                 RunResult(0, _WIN_PROBE_OUT))
    info = probe(fake, "laptop")
    assert info.usable and info.os == "Windows" and fake.ps_calls == 1
    assert info.orca_path == "C:/ORCA_6.1.1/orca.exe" and info.orca_version == "6.1.1"
    assert info.has_mpi and info.has_setsid and info.workdir == "C:/Users/u/orcadesk-runs"
    assert info.cores == 8 and info.mem_total_mb == 32768


def test_an_msys_bash_answer_is_probed_as_windows_too():
    fake = _Fake(RunResult(0, "os=MINGW64_NT-10.0-26200\ncores=8\n"), RunResult(0, _WIN_PROBE_OUT))
    assert probe(fake, "laptop").os == "Windows"


def test_an_unreachable_device_is_not_asked_twice_and_linux_is_untouched():
    down = _Fake(RunResult(RC_SSH, "", "ssh: connect to host x port 22: Connection timed out"))
    assert not probe(down, "laptop").usable and down.ps_calls == 0
    linux = _Fake(RunResult(0, "os=Linux\ncores=4\nmem_total_kb=8000000\norca=/opt/orca/orca\n"
                               "setsid=/usr/bin/setsid\nworkdir=/home/u/orcadesk-runs\n"))
    assert probe(linux, "vm").usable and linux.ps_calls == 0


def test_a_failed_powershell_probe_keeps_the_first_answer():
    fake = _Fake(RunResult(1, "", "sh: not found"), RunResult(1, "", "powershell: not found"))
    info = probe(fake, "box")
    assert not info.usable and info.os != "Windows"


@pytest.mark.parametrize("d,w,ok", [
    ("C:/Users/u/orcadesk-runs/sys/job.1", "C:/Users/u/orcadesk-runs", True),
    ("c:/users/U/ORCADESK-RUNS/x", "C:/Users/u/orcadesk-runs", True),      # NTFS: no case
    ("C:/Users/u/orcadesk-runs", "C:/Users/u/orcadesk-runs", False),       # the workdir itself
    ("C:/Users/u/orcadesk-runs/", "C:/Users/u/orcadesk-runs", False),
    ("C:/Users/u/orcadesk-runs/../x", "C:/Users/u/orcadesk-runs", False),
    ("C:/Users/u/other/x", "C:/Users/u/orcadesk-runs", False),
    ("D:/Users/u/orcadesk-runs/x", "C:/Users/u/orcadesk-runs", False),
    ("C:/x", "C:/", False), ("C:/x", "C:", False),                         # never a drive root
    ("/home/u/runs/x", "/home/u/runs", False),                             # not Windows paths
])
def test_the_windows_delete_guard(d, w, ok):
    assert guard_windows_dir(d, w) is ok


def test_platform_and_sftp_form_follow_the_remote_folder():
    assert platform_for("C:/Users/u/orcadesk-runs/a.1") is WINDOWS
    assert platform_for("/home/u/orcadesk-runs/a.1") is POSIX
    assert WINDOWS.sftp_path("C:/Users/u/x/a.inp") == "/C:/Users/u/x/a.inp"
    assert POSIX.sftp_path("/home/u/a.inp") == "/home/u/a.inp"
    assert wp.META_PREFIX == rr.META_PREFIX
    assert remote_dir_for("C:/Users/u/orcadesk-runs", "sys/job", "20261123-120000") == \
        "C:/Users/u/orcadesk-runs/sys/job.20261123-120000"


def test_the_launch_line_quotes_a_path_with_spaces_and_quotes():
    s = WINDOWS.launch("C:/Users/O'Brien/orcadesk-runs/j.1", "C:/Program Files/ORCA 6/orca.exe", "job")
    assert "'cmd.exe /c \"\"C:\\Program Files\\ORCA 6\\orca.exe\" job.inp > job.out 2>&1\"'" in s
    assert "'C:\\Users\\O''Brien\\orcadesk-runs\\j.1'" in s


def test_a_posix_fake_has_no_powershell_and_a_windows_folder_says_so():
    class Posix:
        def run(self, alias, script, timeout=30.0):
            return RunResult(0, "alive\n")
    res = WINDOWS.run(Posix(), "x", "'hi'")
    assert not res.ok and "PowerShell" in res.err


# ---- real layer: PowerShell on this machine ----------------------------------

if ON_WINDOWS:
    from local_ps_transport import LocalPowerShellTransport

_FAKE_ORCA = (
    "@echo off\r\n"
    "for /l %%i in (1,1,4) do (\r\n"
    "  echo line %%i\r\n"
    "  ping -n 2 127.0.0.1 >nul\r\n"
    ")\r\n"
    "echo ORCA TERMINATED NORMALLY\r\n"
    "echo orbitals> %~n1.gbw\r\n")

_SLOW_ORCA = (
    "@echo off\r\n"
    "echo started\r\n"
    "ping -n 120 127.0.0.77 >nul\r\n"
    "echo ORCA TERMINATED NORMALLY\r\n")


def _setup(tmp_path: Path, body: str):
    work = tmp_path / "remote-work"
    work.mkdir()
    orca = tmp_path / "fakeorca" / "orca.cmd"
    orca.parent.mkdir()
    orca.write_bytes(body.encode("ascii"))
    local = tmp_path / "local" / "sys" / "job"
    local.mkdir(parents=True)
    (local / "job.inp").write_text("! SP\n* xyz 0 1\nH 0 0 0\nH 0 0 0.74\n*\n")
    t = LocalPowerShellTransport()
    rdir = remote_dir_for(work.as_posix(), "sys/job", launch_stamp())
    r = RemoteOrcaRunner(t, "laptop", orca.as_posix(), work.as_posix(), rdir)
    r.SYNC_EVERY = 1.0
    return t, r, local


@local_only
def test_probe_here_reads_this_machine_as_a_windows_device(tmp_path):
    t = LocalPowerShellTransport()
    w = tmp_path / "한글 runs"            # a Korean user folder must survive the trip
    info = probe(t, "laptop", workdir=w.as_posix())
    assert info.os == "Windows" and info.cores > 0 and info.mem_total_mb > 0
    assert info.workdir == w.as_posix() and w.is_dir() and info.has_setsid
    if Path("C:/ORCA_6.1.1/orca.exe").is_file():
        assert info.usable, info.message
        assert info.orca_path.lower() == "c:/orca_6.1.1/orca.exe" and info.orca_version == "6.1.1"


@local_only
def test_a_detached_run_syncs_fetches_verifies_and_deletes(tmp_path):
    t, r, local = _setup(tmp_path, _FAKE_ORCA)
    pid, start = r.launch(local / "job.inp", local / "job.out")
    assert pid > 0 and start > 1.6e12          # ms since the epoch
    assert remote_liveness(t, "laptop", pid, start, WINDOWS) is True
    lines = []
    r.monitor(local / "job.out", on_line=lines.append)
    assert [ln for ln in lines if ln.startswith("line")] == ["line 1", "line 2", "line 3", "line 4"]
    assert "ORCA TERMINATED NORMALLY" in lines
    assert (local / "job.gbw").read_text().strip() == "orbitals"     # the whole folder came back
    assert not Path(r.remote_dir).exists()                           # deleted after verify
    assert remote_liveness(t, "laptop", pid, start, WINDOWS) is False
    # the sync carried only new bytes: several syncs, each from the local size
    syncs = [s for kind, s in t.calls if kind == "ps" and "ToBase64String" in s]
    assert len(syncs) >= 3


@local_only
def test_cancel_takes_the_whole_process_tree_down(tmp_path):
    t, r, local = _setup(tmp_path, _SLOW_ORCA)
    pid, start = r.launch(local / "job.inp", local / "job.out")
    caught = []

    def watch():
        try:
            r.monitor(local / "job.out")
        except OrcaCancelled as e:
            caught.append(e)
    th = threading.Thread(target=watch, daemon=True)
    th.start()
    time.sleep(3)
    r.cancel()
    th.join(60)
    assert caught and remote_liveness(t, "laptop", pid, start, WINDOWS) is False
    left = t.run_powershell("x", "@(Get-CimInstance Win32_Process | Where-Object "
                                 "{ $_.CommandLine -like '*127.0.0.77*' }).Count")
    assert left.out.strip() == "0", "the ping child outlived the cancel"
    assert not Path(r.remote_dir).exists()


@local_only
def test_a_cleanup_entry_on_a_windows_device_kills_and_deletes(tmp_path):
    # the clean-up list knows the device is Windows from the entry's own
    # remote folder (no extra field): kill the recorded process, then delete
    t, r, local = _setup(tmp_path, _SLOW_ORCA)
    pid, start = r.launch(local / "job.inp", local / "job.out")
    entry = dict(r.cleanup_entry(), fetch=False)
    assert rr.process_cleanup(t, entry, r.workdir, None) is True
    assert remote_liveness(t, "laptop", pid, start, WINDOWS) is False
    assert not Path(r.remote_dir).exists()


@local_only
def test_sync_reads_from_the_offset_and_caps_while_the_file_is_open(tmp_path):
    t = LocalPowerShellTransport()
    f = tmp_path / "a.out"
    f.write_bytes(b"0123456789")
    with open(f, "ab") as held:              # ORCA still has it open for writing
        held.write(b"AB")
        held.flush()
        res = t.run_powershell("x", WINDOWS.sync(0, 0, f.as_posix(), 4, 3))
    head, _, rest = res.out.partition("\n")
    import base64
    assert head.strip() == "dead" and base64.b64decode(rest) == b"456"
    res = t.run_powershell("x", WINDOWS.sync(0, 0, f.as_posix(), 99, 3))
    assert base64.b64decode(res.out.partition("\n")[2]) == b""


@local_only
def test_listing_and_the_remote_half_of_the_delete_guard(tmp_path):
    t = LocalPowerShellTransport()
    work = tmp_path / "w"
    d = work / "sys" / "job.1"
    (d / "sub").mkdir(parents=True)
    (d / "job.out").write_bytes(b"x" * 5)
    (d / "sub" / "job_D1.out").write_bytes(b"y" * 3)
    (d / ".orcadesk.note").write_bytes(b"z")
    files, res = rr.list_remote_files(t, "laptop", d.as_posix())
    assert files == {"job.out": 5, "sub/job_D1.out": 3}, res.err
    # the remote script refuses the workdir itself and anything outside it,
    # even when the local guard is bypassed
    assert t.run_powershell("x", WINDOWS.remove(work.as_posix(), work.as_posix())).rc == 3
    assert t.run_powershell("x", WINDOWS.remove(tmp_path.as_posix(), work.as_posix())).rc == 3
    assert safe_remove(t, "laptop", "C:/", work.as_posix()) is False
    assert safe_remove(t, "laptop", d.as_posix(), work.as_posix()) is True and not d.exists()
    assert safe_remove(t, "laptop", d.as_posix(), work.as_posix()) is True     # already gone


@local_only
@pytest.mark.skipif(not (os.environ.get("ORCADESK_SMOKE") and Path("C:/ORCA_6.1.1/orca.exe").is_file()),
                    reason="ORCADESK_SMOKE=1 and ORCA at C:\\ORCA_6.1.1")
@pytest.mark.parametrize("nprocs", [1, 2])
def test_a_real_orca_sp_through_the_windows_runner(tmp_path, nprocs):
    work = tmp_path / "remote-work"
    work.mkdir()
    local = tmp_path / "local" / "sys" / f"h2o_p{nprocs}"
    local.mkdir(parents=True)
    pal = f"%pal nprocs {nprocs} end\n" if nprocs > 1 else ""
    name = f"h2o_p{nprocs}"
    (local / f"{name}.inp").write_text(
        f"! B3LYP def2-SVP\n{pal}* xyz 0 1\nO 0 0 0\nH 0 0.757 0.587\nH 0 -0.757 0.587\n*\n")
    t = LocalPowerShellTransport()
    info = probe(t, "laptop", workdir=work.as_posix())
    assert info.usable, info.message
    r = RemoteOrcaRunner(t, "laptop", info.orca_path, info.workdir,
                         remote_dir_for(info.workdir, f"sys/{name}", launch_stamp()))
    r.SYNC_EVERY = 2.0
    r.launch(local / f"{name}.inp", local / f"{name}.out")
    r.monitor(local / f"{name}.out")
    res = parse_file(local / f"{name}.out")
    assert res.terminated_normally and abs(res.final_energy_eh - (-76.3211)) < 1e-3
    assert (local / f"{name}.gbw").is_file() and not Path(r.remote_dir).exists()
    if nprocs > 1:
        assert f"{nprocs} parallel MPI-processes" in (local / f"{name}.out").read_text(errors="replace")


# ---- the HighQoS watcher (winplatform.QOS_SCRIPT) ----------------------------
#
# Measured on a hybrid-CPU laptop: Windows classes an SSH-started ORCA as
# background work (EcoQoS) and parks it on the E-cores; opting a process out
# is not inherited by its children, and ORCA starts a new process per step, so
# a watcher re-applies the opt-out to every matching process continuously.


class _WinFake:
    """A Windows device that answers launch scripts and records them."""

    def __init__(self, qos: RunResult):
        self.qos, self.scripts = qos, []

    def run(self, alias, script, timeout=30.0, stop=None):
        return RunResult(1, "", "'sh' is not recognized")

    def run_powershell(self, alias, script, timeout=30.0, stop=None):
        self.scripts.append(script)
        if wp.QOS_SCRIPT_NAME in script:
            return self.qos
        if "Win32_Process -MethodName Create" in script:
            return RunResult(0, "pid=4242\nstart=1795000000000\n")
        return RunResult(0)

    def put(self, alias, pairs, timeout=600.0, stop=None):
        return RunResult(0)


def _win_runner(fake, tmp_path, workdir="C:/Users/u/orcadesk-runs"):
    local = tmp_path / "job"
    local.mkdir()
    (local / "job.inp").write_text("! SP\n")
    r = RemoteOrcaRunner(fake, "laptop", "C:/ORCA_6.1.1/orca.exe", workdir,
                         "C:/Users/u/orcadesk-runs/sys/job.20261123-120000")
    return r, local


def _qos_calls(fake):
    return [s for s in fake.scripts if wp.QOS_SCRIPT_NAME in s]


def test_a_windows_launch_sees_to_the_watcher_once(tmp_path):
    fake = _WinFake(RunResult(0, "qos=started pid=77\n"))
    r, local = _win_runner(fake, tmp_path)
    assert r.launch(local / "job.inp", local / "job.out") == (4242, 1795000000000.0)
    qos = _qos_calls(fake)
    assert len(qos) == 1 and "'C:\\Users\\u\\orcadesk-runs'" in qos[0]
    assert f"-Version {wp.QOS_VERSION}" in qos[0]
    r._ensure_qos()                          # already seen to: no second call
    assert len(_qos_calls(fake)) == 1


def test_a_watcher_that_cannot_start_never_costs_the_job(tmp_path, caplog):
    fake = _WinFake(RunResult(4, "qos=failed rc=8\n"))
    r, local = _win_runner(fake, tmp_path)
    with caplog.at_level("WARNING", logger="orcamgr.remote"):
        assert r.launch(local / "job.inp", local / "job.out")[0] == 4242
    assert any("HighQoS watcher not started" in m for m in caplog.messages)


def test_a_reattached_job_sees_to_the_watcher_once_its_workdir_is_known(tmp_path):
    # reattached before the device's first probe: no workdir yet, nothing to do;
    # the engine fills the workdir in later, and then the watcher is seen to
    fake = _WinFake(RunResult(0, "qos=running\n"))
    r, _ = _win_runner(fake, tmp_path, workdir="")
    r.adopt(4242, 1795000000000.0)
    r._ensure_qos()
    assert not _qos_calls(fake)
    r.workdir = "C:/Users/u/orcadesk-runs"
    r._ensure_qos()
    assert len(_qos_calls(fake)) == 1


def test_a_linux_device_never_hears_of_the_watcher():
    from fake_transport import FakeTransport
    r = RemoteOrcaRunner(FakeTransport(), "gcp", "/opt/orca/orca", "/home/u/runs",
                         "/home/u/runs/sys/job.20261123-120000")
    assert r._qos_done                      # nothing to see to on Linux
    with pytest.raises(ValueError):
        WINDOWS.qos_ensure("C:/w", pattern="orca*; Remove-Item x")
    assert wp.QOS_SCRIPT.isascii()          # PowerShell 5 reads it as ANSI


def _throttling(pid):
    """(ControlMask, StateMask) of ``pid``'s power-throttling state, or None."""
    import ctypes
    from ctypes import wintypes

    class State(ctypes.Structure):
        _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG),
                    ("StateMask", wintypes.ULONG)]
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.GetProcessInformation.restype = wintypes.BOOL
    k32.GetProcessInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                          wintypes.DWORD)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        s = State(1, 0, 0)
        if not k32.GetProcessInformation(h, 4, ctypes.byref(s), ctypes.sizeof(s)):
            return None
        return s.ControlMask, s.StateMask
    finally:
        k32.CloseHandle(h)


def _watchers(work: Path) -> list:
    import psutil
    needle = str(work / wp.QOS_SCRIPT_NAME).lower()
    out = []
    for p in psutil.process_iter(["cmdline"]):
        try:
            if needle in " ".join(p.info["cmdline"] or []).lower():
                out.append(p.pid)
        except (psutil.Error, TypeError):
            pass
    return out


def _wait_gone(work: Path, seconds: float = 30.0) -> None:
    deadline = time.monotonic() + seconds
    while _watchers(work) and time.monotonic() < deadline:
        time.sleep(0.5)


@local_only
def test_the_watcher_opts_a_later_child_out_runs_once_and_quits_when_idle(tmp_path):
    import shutil
    import subprocess
    work = tmp_path / "remote-work"
    work.mkdir()
    # a stand-in "ORCA step": a copy of ping.exe named like ORCA's processes
    root = Path(os.environ.get("SystemRoot", "C:/Windows"))
    exe = tmp_path / "orcaqostest1.exe"
    shutil.copyfile(root / "System32" / "PING.EXE", exe)
    t = LocalPowerShellTransport()
    ensure = WINDOWS.qos_ensure(work.as_posix(), idle_seconds=4, every=1, pattern="orcaqostest*")
    res = t.run_powershell("laptop", ensure)
    assert res.ok and "qos=started" in res.out, (res.out, res.err)
    child = subprocess.Popen([str(exe), "-n", "60", "127.0.0.1"], stdout=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        deadline = time.monotonic() + 20
        state = _throttling(child.pid)
        while state != (1, 0) and time.monotonic() < deadline:
            time.sleep(0.3)
            state = _throttling(child.pid)
        # started AFTER the watcher -- the case inheritance does not cover
        assert state == (1, 0), state
        # one watcher per folder, however often a launch asks
        again = t.run_powershell("laptop", ensure)
        assert again.ok and "qos=running" in again.out, (again.out, again.err)
        assert len(_watchers(work)) == 1
    finally:
        child.kill()
        child.wait(10)
    _wait_gone(work)
    assert not _watchers(work)                          # gone after the quiet spell
    assert not (work / wp.QOS_PID_NAME).exists()        # and took its pid file along


@local_only
def test_a_stale_pid_file_gets_a_fresh_watcher(tmp_path):
    work = tmp_path / "remote-work"
    work.mkdir()
    (work / wp.QOS_PID_NAME).write_text("4 0ldversion")    # pid 4 is System, not a watcher
    t = LocalPowerShellTransport()
    res = t.run_powershell("laptop", WINDOWS.qos_ensure(work.as_posix(), idle_seconds=2,
                                                        every=1, pattern="orcaqostest*"))
    assert res.ok and "qos=started" in res.out, (res.out, res.err)
    assert (work / wp.QOS_SCRIPT_NAME).read_text(encoding="utf-8") == wp.QOS_SCRIPT
    _wait_gone(work)
    assert not _watchers(work)
