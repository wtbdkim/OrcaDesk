"""RemoteOrcaRunner (orcamgr/remote/runner.py) against a fake transport:
launch uploads and starts, monitor syncs the local .out every 10 s, the
disconnected flag's hysteresis, the whole-folder fetch -> verify -> guarded
delete, cancel when reachable and when not, and the clean-up step."""
from __future__ import annotations

from pathlib import Path

import pytest

from orcamgr.core.runner import OrcaCancelled, OrcaDetached, OrcaHung
from orcamgr.remote import runner as rr
from orcamgr.remote.runner import (RemoteCancelUnreachable, RemoteOrcaRunner,
                                   RemoteUnavailable, fetch_and_verify,
                                   guard_remote_dir, process_cleanup,
                                   remote_dir_for, remote_safe_name, safe_remove)

from fake_transport import FakeClock, FakeTransport

WORK = "/home/u/orcadesk-runs"
RDIR = remote_dir_for(WORK, "sys/job", "20261123-120000")


def _folder(tmp_path) -> Path:
    d = tmp_path / "sys" / "job"
    d.mkdir(parents=True)
    (d / "job.inp").write_text("! SP\n* xyz 0 1\nH 0 0 0\nH 0 0 0.74\n*\n")
    (d / "job.out").write_text("an older run's output\n")
    (d / "job.gbw.bak").write_bytes(b"set aside")
    return d


def _runner(fake, clock=None, remote_dir=RDIR) -> RemoteOrcaRunner:
    clock = clock or FakeClock()
    r = RemoteOrcaRunner(fake, "gcp", "/opt/orca/orca", WORK, remote_dir,
                         clock=clock, sleep=clock.sleep)
    r.SYNC_EVERY = 1.0
    r.FETCH_BACKOFF = (0.5,)
    return r


# ---- names and guards ---------------------------------------------------------------

def test_the_remote_folder_is_new_for_every_launch():
    a = remote_dir_for(WORK, "sys/job", "20261123-120000")
    b = remote_dir_for(WORK, "sys/job", "20261123-120501")
    assert a == WORK + "/sys/job.20261123-120000" and a != b


@pytest.mark.parametrize("rel,ok", [
    ("sys/job", True), ("job.gbw", True), ("a+b", True),
    ("../etc", False), ("/abs", False), ("x*y", False), ("한글", False), ("a b", False),
])
def test_only_names_the_transport_carries_safely_go_remote(rel, ok):
    assert remote_safe_name(rel) is ok


@pytest.mark.parametrize("d,w,ok", [
    (WORK + "/sys/job.1", WORK, True),
    (WORK, WORK, False), (WORK + "/", WORK, False), ("/", WORK, False),
    ("/home/u/other/x", WORK, False), (WORK + "/../x", WORK, False),
    ("relative/x", WORK, False), (WORK + "/x", "", False), (WORK + "/x", "/", False),
])
def test_the_delete_guard_only_allows_a_folder_strictly_below_the_workdir(d, w, ok):
    assert guard_remote_dir(d, w) is ok


def test_safe_remove_refuses_without_touching_the_device():
    fake = FakeTransport()
    assert safe_remove(fake, "gcp", "/", WORK) is False
    assert fake.calls == []


# ---- launch ------------------------------------------------------------------------

def test_launch_uploads_the_folder_except_the_old_out_and_set_aside_files(tmp_path):
    d = _folder(tmp_path)
    fake = FakeTransport()
    r = _runner(fake)
    pid, start = r.launch(d / "job.inp", d / "job.out")
    assert (pid, start) == (4242, 999.0)
    assert sorted(fake.files_under(RDIR)) == ["job.inp"]
    assert (d / "job.out").read_bytes() == b""      # a fresh run starts empty


def test_a_failed_upload_is_unavailable_not_a_failed_calculation(tmp_path):
    d = _folder(tmp_path)
    fake = FakeTransport()
    fake.fail_put = True
    with pytest.raises(RemoteUnavailable):
        _runner(fake).launch(d / "job.inp", d / "job.out")
    fake2 = FakeTransport()
    fake2.reachable = False
    with pytest.raises(RemoteUnavailable):
        _runner(fake2).launch(d / "job.inp", d / "job.out")


# ---- monitor -----------------------------------------------------------------------

def _finish_on_device(fake, d=RDIR, out=b"ORCA TERMINATED NORMALLY\n"):
    fake.write(d + "/job.out", out)
    fake.write(d + "/job.gbw", b"orbitals")
    fake.write(d + "/sub/job_D1.out", b"displacement")
    fake.alive = False


def test_monitor_syncs_the_local_out_then_fetches_the_whole_folder(tmp_path):
    d = _folder(tmp_path)
    fake = FakeTransport()
    r = _runner(fake)
    r.launch(d / "job.inp", d / "job.out")
    lines = []
    fake.write(RDIR + "/job.out", b"line one\nline two\n")
    fake.liveness_script = [True, False]

    def on_line(ln):
        lines.append(ln)
        if ln == "line two":       # the device goes on writing, then ends
            _finish_on_device(fake, out=b"line one\nline two\nORCA TERMINATED NORMALLY\n")

    r.monitor(d / "job.out", on_line=on_line)

    assert lines[:2] == ["line one", "line two"]
    assert "ORCA TERMINATED NORMALLY" in lines
    assert (d / "job.out").read_bytes().endswith(b"ORCA TERMINATED NORMALLY\n")
    assert (d / "job.gbw").read_bytes() == b"orbitals"
    assert (d / "sub" / "job_D1.out").exists()
    assert (d / "job.gbw.bak").read_bytes() == b"set aside"     # local-only file kept
    assert fake.files_under(RDIR) == {}                         # deleted after verify
    assert not any(p.name.startswith(".orcadesk-fetch-") for p in d.iterdir())


def test_each_sync_carries_only_what_the_out_gained(tmp_path):
    # No connection is held open: one call per SYNC_EVERY, and each brings
    # only the bytes written since the last one.
    import base64
    d = _folder(tmp_path)
    fake = FakeTransport()
    clock = FakeClock()
    r = _runner(fake, clock=clock)
    r.SYNC_EVERY = 10.0
    r.launch(d / "job.inp", d / "job.out")
    fake.write(RDIR + "/job.out", b"a\n")
    sent = []
    real_run = fake.run

    def run(alias, script, timeout=30.0, stop=None):
        res = real_run(alias, script, timeout, stop)
        if "| base64" in script:
            sent.append(base64.b64decode(res.out.split("\n", 1)[1]))
            fake.write(RDIR + "/job.out", fake.remote[RDIR + "/job.out"] + b"b\n")
        return res
    fake.run = run
    fake.liveness_script = [True, True, False]
    t0 = clock.t
    r.monitor(d / "job.out")

    assert sent == [b"a\n", b"b\n", b"b\n"]           # never the whole file again
    assert fake.syncs == 3 and clock.t - t0 >= 20     # 10 s apart


def test_a_sync_is_capped_and_the_next_one_carries_on(tmp_path):
    d = _folder(tmp_path)
    fake = FakeTransport()
    r = _runner(fake)
    r.SYNC_CAP = 4
    r.launch(d / "job.inp", d / "job.out")
    fake.write(RDIR + "/job.out", b"0123456789")
    assert r.sync(0) == (True, b"0123")
    assert r.sync(4) == (True, b"4567")
    assert r.sync(8) == (True, b"89")
    fake.reachable = False
    assert r.sync(10) == (None, b"")


def test_busy_syncs_never_count_as_a_disconnect(tmp_path):
    # Syncs that found no free connection slot on THIS machine (RC_BUSY) say
    # nothing about the device; counting them flagged a healthy AWS device
    # Disconnected while fetches held the slots (2026-11-23).
    from orcamgr.remote.ssh import RC_BUSY, RunResult
    d = _folder(tmp_path)
    fake = FakeTransport()
    r = _runner(fake)
    r.launch(d / "job.inp", d / "job.out")
    flips = []
    r.on_disconnect = flips.append
    real_run = fake.run
    busy_left = [5]

    def run(alias, script, timeout=30.0, **kw):
        if "| base64" in script and busy_left[0] > 0:
            busy_left[0] -= 1
            return RunResult(RC_BUSY, "", "busy: no free connection slot")
        return real_run(alias, script, timeout)
    fake.run = run
    _finish_on_device(fake)
    r.monitor(d / "job.out")
    assert flips == [] and busy_left[0] == 0


def test_a_busy_launch_is_remote_busy_not_a_device_failure(tmp_path):
    from orcamgr.remote.runner import RemoteBusy
    from orcamgr.remote.ssh import RC_BUSY, RunResult
    d = _folder(tmp_path)
    fake = FakeTransport()
    fake.run = lambda alias, script, timeout=30.0, **kw: RunResult(RC_BUSY, "", "busy")
    with pytest.raises(RemoteBusy) as ei:
        _runner(fake).launch(d / "job.inp", d / "job.out")
    assert getattr(ei.value, "busy", False) is True


def test_one_unknown_liveness_answer_is_not_a_disconnect_two_are(tmp_path):
    d = _folder(tmp_path)
    fake = FakeTransport()
    r = _runner(fake)
    r.launch(d / "job.inp", d / "job.out")
    flips = []
    r.on_disconnect = flips.append
    fake.liveness_script = [None, True, None, None, True, False]
    _finish_on_device(fake)
    r.monitor(d / "job.out")
    assert flips == [True, False]          # only the two-in-a-row raised it


def test_a_fetch_that_fails_is_retried_and_nothing_is_deleted_until_it_lands(tmp_path):
    d = _folder(tmp_path)
    fake = FakeTransport()
    r = _runner(fake)
    r.launch(d / "job.inp", d / "job.out")
    fake.liveness_script = [False]
    _finish_on_device(fake)
    fake.fail_get_times = 3
    r.monitor(d / "job.out")
    assert (d / "job.gbw").exists() and fake.files_under(RDIR) == {}


def test_a_size_mismatch_keeps_the_remote_copy(tmp_path, monkeypatch):
    d = _folder(tmp_path)
    fake = FakeTransport()
    fake.write(RDIR + "/job.out", b"12345")
    real_get = fake.get

    def short_get(alias, pairs, timeout=0):
        res = real_get(alias, pairs)
        for _, local in pairs:
            Path(local).write_bytes(b"12")
        return res
    fake.get = short_get
    fr = fetch_and_verify(fake, "gcp", RDIR, d)
    assert not fr.ok and "size mismatch" in fr.message
    assert fake.files_under(RDIR)                 # nothing deleted
    assert (d / "job.out").read_text() == "an older run's output\n"   # nothing replaced


def test_detach_leaves_the_remote_job_running(tmp_path):
    d = _folder(tmp_path)
    fake = FakeTransport()
    r = _runner(fake)
    r.launch(d / "job.inp", d / "job.out")
    r.detach()
    with pytest.raises(OrcaDetached):
        r.monitor(d / "job.out")
    assert fake.alive and fake.files_under(RDIR)


def test_cancel_on_a_reachable_device_kills_fetches_and_deletes(tmp_path):
    d = _folder(tmp_path)
    fake = FakeTransport()
    r = _runner(fake)
    r.launch(d / "job.inp", d / "job.out")
    fake.write(RDIR + "/job.out", b"partial\n")
    r.cancel()
    with pytest.raises(OrcaCancelled) as ei:
        r.monitor(d / "job.out")
    assert not isinstance(ei.value, RemoteCancelUnreachable)
    assert not fake.alive and fake.files_under(RDIR) == {}
    assert (d / "job.out").read_bytes() == b"partial\n"


def test_cancel_on_an_unreachable_device_returns_a_cleanup_entry(tmp_path):
    d = _folder(tmp_path)
    fake = FakeTransport()
    r = _runner(fake)
    r.launch(d / "job.inp", d / "job.out")
    fake.reachable = False
    r.cancel()
    with pytest.raises(RemoteCancelUnreachable) as ei:
        r.monitor(d / "job.out")
    e = ei.value.entry
    assert e["device"] == "gcp" and e["pid"] == 4242 and e["remote_dir"] == RDIR
    assert e["launched_at"] == "20261123-120000"


def test_a_hung_run_is_stopped_fetched_and_reported(tmp_path):
    d = _folder(tmp_path)
    fake = FakeTransport()
    r = _runner(fake)
    r.launch(d / "job.inp", d / "job.out")
    fake.write(RDIR + "/job.out", b"ORCA finished by error termination in SCF\n")
    fake.idle = 3600
    fatal = []
    with pytest.raises(OrcaHung):
        r.monitor(d / "job.out", on_fatal=fatal.append)
    assert fatal and not fake.alive and fake.files_under(RDIR) == {}


# ---- clean-up list -------------------------------------------------------------------

def _entry(pid=4242):
    return {"device": "gcp", "pid": pid, "create_time": 999, "remote_dir": RDIR,
            "calc_name": "job", "launched_at": "20261123-120000"}


def test_cleanup_kills_fetches_and_deletes_when_the_calc_was_not_rerun(tmp_path):
    d = _folder(tmp_path)
    fake = FakeTransport()
    _finish_on_device(fake)
    fake.alive = True
    assert process_cleanup(fake, _entry(), WORK, d) is True
    assert not fake.alive and (d / "job.gbw").exists() and fake.files_under(RDIR) == {}


def test_cleanup_after_a_rerun_deletes_without_fetching(tmp_path):
    d = _folder(tmp_path)
    fake = FakeTransport()
    _finish_on_device(fake)
    assert process_cleanup(fake, _entry(), WORK, None) is True
    assert not (d / "job.gbw").exists() and fake.files_under(RDIR) == {}


def test_cleanup_on_a_device_still_unreachable_is_left_for_later(tmp_path):
    fake = FakeTransport()
    _finish_on_device(fake)
    fake.reachable = False
    assert process_cleanup(fake, _entry(), WORK, None) is False


def test_cleanup_of_a_folder_already_gone_is_done(tmp_path):
    fake = FakeTransport()
    fake.alive = False
    assert process_cleanup(fake, _entry(pid=None), WORK, tmp_path) is True


# ---- an app close never waits on an in-flight call -----------------------------

class _BlockingFake(FakeTransport):
    """A device whose sync (or download) hangs until the caller's stop fires,
    as a slow link does -- the real transport kills the ssh child then."""

    def __init__(self, block):
        super().__init__()
        self.block = block          # "sync" | "get"

    def run(self, alias, script, timeout=30.0, stop=None):
        if self.block == "sync" and "| base64" in script:
            return self._hang(stop)
        return super().run(alias, script, timeout, stop)

    def get(self, alias, pairs, timeout=3600.0, stop=None):
        if self.block == "get":
            return self._hang(stop)
        return super().get(alias, pairs, timeout, stop)

    @staticmethod
    def _hang(stop):
        import time
        from orcamgr.remote.ssh import RC_ABORTED, RunResult
        end = time.monotonic() + 30
        while time.monotonic() < end:
            if stop is not None and stop.is_set():
                return RunResult(RC_ABORTED, "", "aborted")
            time.sleep(0.02)
        return RunResult(124, "", "timeout")


@pytest.mark.parametrize("block", ["sync", "get"])
def test_a_detach_does_not_wait_for_a_hung_sync_or_download(tmp_path, block):
    # Closing the window waited for every remote monitor's in-flight call (a
    # sync up to 60 s, a fetch much longer): a close with jobs on two devices
    # froze the window past 20 s (2026-11-23). The call is abandoned now.
    import threading
    import time
    d = _folder(tmp_path)
    fake = _BlockingFake(block)
    r = RemoteOrcaRunner(fake, "gcp", "/opt/orca/orca", WORK, RDIR)   # real clock
    r.launch(d / "job.inp", d / "job.out")
    if block == "get":
        _finish_on_device(fake)       # the job ended: the monitor goes to the fetch
    caught = []

    def watch():
        try:
            r.monitor(d / "job.out")
        except OrcaDetached as e:
            caught.append(e)
    th = threading.Thread(target=watch, daemon=True)
    th.start()
    time.sleep(0.5)                   # well inside the hung call
    t0 = time.monotonic()
    r.detach()
    th.join(5)
    assert caught and time.monotonic() - t0 < 1.5
    # a close is no outage: the Disconnected flag was not raised by it
    assert not r.disconnected
    assert fake.files_under(RDIR)     # nothing deleted: the next launch fetches it
