"""The queue engine with remote devices (design §5, §7): placement across
device pools, what may go remote, a device that cannot take a job, a whole
remote run through the real _run_calc against a fake transport, and the
store's persistence of where a calc ran."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import orcamgr.core.queue as queue_mod
from orcamgr.core.queue import CalcState, QueueCallbacks, QueueEngine, remote_capable
from orcamgr.core.resources import ResourceBudget
from orcamgr.remote.devices import interpret_probe, normalize_device
from orcamgr.remote.runner import RemoteUnavailable
from orcamgr.remote.ssh import RunResult
from orcamgr.state.store import (calc_from_dict, calc_from_session_dict,
                                 calc_to_session_dict, reconcile_calcs)

from fake_transport import FakeTransport
from test_queue_engine import EngineHarness, make_calc

_READY = """os=Linux
home=/home/u
cores=8
mem_total_kb=33554432
mem_avail_kb=30000000
orca=/home/u/orca_6_1_1_linux/orca
mpirun={mpi}
setsid=/usr/bin/setsid
workdir=/home/u/orcadesk-runs
free_kb=500000000
"""


def _info(alias="gcp", mpi="/usr/bin/mpirun", ok=True):
    if not ok:
        return interpret_probe(alias, RunResult(255, "", "Connection timed out"))
    return interpret_probe(alias, RunResult(0, _READY.format(mpi=mpi)))


def _with_device(harness, info, max_jobs=2, transport=None):
    """Give a harness's engine one enabled remote device."""
    eng = harness.engine
    entry = normalize_device({"alias": "gcp", "enabled": True, "max_jobs": max_jobs})
    fresh = QueueEngine(eng.orca_path, str(eng.workspace_root), eng.cb,
                        budget=eng.budget, device_entries=[entry],
                        transport=transport or FakeTransport(),
                        device_info=lambda a: info)
    fresh._run_calc = harness._fake_run_calc
    fresh._run_mlip_calc = harness._fake_run_calc
    fresh._run_crest_calc = harness._fake_run_calc
    harness.engine = fresh
    return fresh


def _record_devices(harness, names, barrier=None):
    where = {}

    def note(c):
        where[c.name] = harness.engine._job.device
        if barrier is not None:
            barrier.wait()
    for n in names:
        harness.behaviors[n] = note
    return where


def test_what_does_not_fit_here_goes_to_the_device(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    harness = EngineHarness(tmp_path, budget=ResourceBudget(max_jobs=1, cores=64,
                                                            ram_mb=10**6))
    _with_device(harness, _info())
    gate = threading.Barrier(3, timeout=20)
    where = _record_devices(harness, ["a", "b", "c"], gate)
    calcs = [make_calc(n) for n in "abc"]
    for c in calcs:
        c.config.nprocs = 1
    harness.engine.run_all(calcs)
    assert sorted(where.values()) == ["", "gcp", "gcp"]
    assert all(c.state == CalcState.DONE for c in calcs)


def test_an_offline_device_takes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    harness = EngineHarness(tmp_path, budget=ResourceBudget(max_jobs=1, cores=64,
                                                            ram_mb=10**6))
    _with_device(harness, _info(ok=False))
    where = _record_devices(harness, ["a", "b"])
    harness.engine.run_all([make_calc("a"), make_calc("b")])
    assert where == {"a": "", "b": ""}


def test_a_parallel_job_needs_mpi_on_the_device(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    harness = EngineHarness(tmp_path, budget=ResourceBudget(max_jobs=1, cores=64,
                                                            ram_mb=10**6))
    _with_device(harness, _info(mpi=""))
    where = _record_devices(harness, ["a", "b"])
    a, b = make_calc("a"), make_calc("b")
    a.config.nprocs = b.config.nprocs = 4
    harness.engine.run_all([a, b])
    assert where == {"a": "", "b": ""}


def test_mlip_gpu_and_local_paths_stay_here():
    # A CREST search is no longer local-only (the user's decision, after
    # CREST was installed on the AWS device): it is admitted by kind here and
    # gated per device by _fits_remote (CREST must be there) -- see
    # test_remote_crest.py. MLIP still never leaves this machine.
    sp = make_calc("ok")
    assert remote_capable(sp, "sys/ok")
    assert not remote_capable(make_calc("m", kind="mlip_opt"), "m")
    assert remote_capable(make_calc("c", kind="crest_conf"), "c")
    raw = make_calc("r")
    raw.is_raw = True
    raw.raw_text = '! SP\n%moinp "D:/work/x.gbw"\n'
    assert not remote_capable(raw, "r")
    assert not remote_capable(make_calc("한글"), "한글")


def test_a_device_that_cannot_take_a_job_goes_offline_and_the_row_runs_elsewhere(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    harness = EngineHarness(tmp_path, budget=ResourceBudget(max_jobs=1, cores=64,
                                                            ram_mb=10**6))
    eng = _with_device(harness, _info())
    gate = threading.Event()
    tried = []

    def a(c):
        gate.wait(10)          # hold the local slot until b has been refused

    def b(c):
        tried.append(eng._job.device)
        if eng._job.device == "gcp":
            gate.set()
            raise RemoteUnavailable("upload failed")
    harness.behaviors["a"] = a
    harness.behaviors["b"] = b
    ca, cb = make_calc("a"), make_calc("b")
    eng.run_all([ca, cb])
    assert tried == ["gcp", ""]
    assert cb.state == CalcState.DONE and not eng._devices["gcp"].online


def test_a_busy_hand_over_keeps_the_device_online_and_offers_the_row_again(tmp_path, monkeypatch):
    # RemoteBusy: this machine had no free connection slot, the device was
    # never asked. It used to be marked offline like a refused upload
    # (2026-11-23: a healthy AWS device lost its launches while fetches held
    # the slots); now the row is just offered again and the device stays up.
    from orcamgr.remote.runner import RemoteBusy
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    harness = EngineHarness(tmp_path, budget=ResourceBudget(max_jobs=1, cores=64,
                                                            ram_mb=10**6))
    eng = _with_device(harness, _info())
    gate = threading.Event()
    tried = []

    def a(c):
        gate.wait(10)          # hold the local slot so b goes to the device

    def b(c):
        tried.append(eng._job.device)
        if len(tried) == 1:
            raise RemoteBusy("mkdir failed on gcp: busy")
        gate.set()
    harness.behaviors["a"] = a
    harness.behaviors["b"] = b
    ca, cb = make_calc("a"), make_calc("b")
    eng.run_all([ca, cb])
    assert cb.state == CalcState.DONE and ca.state == CalcState.DONE
    assert tried[:2] == ["gcp", "gcp"]       # offered again -- to the same device
    assert eng._devices["gcp"].online


def test_resource_usage_reports_each_device(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    harness = EngineHarness(tmp_path, budget=ResourceBudget(max_jobs=1, cores=64,
                                                            ram_mb=10**6))
    eng = _with_device(harness, _info())
    seen = []
    gate = threading.Barrier(2, timeout=20)

    def hold(c):
        gate.wait()
        seen.append(eng.resource_usage())
    harness.behaviors["a"] = hold
    harness.behaviors["b"] = hold
    a, b = make_calc("a"), make_calc("b")
    a.config.nprocs = b.config.nprocs = 1
    eng.run_all([a, b])
    u = seen[0]
    assert u["jobs"] == 1 and u["devices"][0]["id"] == "gcp"
    assert u["devices"][0]["jobs"] == 1 and u["devices"][0]["online"] is True


# ---- a whole remote run through the real _run_calc ----------------------------

_OUT = (b"                       ORCA\n"
        b"FINAL SINGLE POINT ENERGY        -1.117506095000\n"
        b"                             ****ORCA TERMINATED NORMALLY****\n")


def test_a_remote_run_comes_back_whole_before_it_is_done(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    fake = FakeTransport()

    at_launch = []

    def finish(f, remote_dir):
        at_launch.append(updates[-1] if updates else None)
        f.write(remote_dir + "/remote1.out", _OUT)
        f.write(remote_dir + "/remote1.gbw", b"orbitals")
        f.alive = False
    fake.on_launch = finish
    entry = normalize_device({"alias": "gcp", "enabled": True})
    updates = []
    cb = QueueCallbacks(calc_update=lambda i, c: updates.append((c.device, c.remote_dir)))
    # this machine cannot take it (1 MB of memory), so it goes to the device
    eng = QueueEngine("orca-not-needed", str(tmp_path), cb,
                      budget=ResourceBudget(max_jobs=1, cores=1, ram_mb=1),
                      device_entries=[entry], transport=fake,
                      device_info=lambda a: _info())
    calc = make_calc("remote1")
    calc.xyz = "H 0 0 0\nH 0 0 0.74"
    calc.config.nprocs = 1
    eng.run_all([calc])

    assert calc.state == CalcState.DONE, calc.message
    assert calc.device == "gcp"
    assert calc.remote_dir.startswith("/home/u/orcadesk-runs/remote1.")
    folder = Path(tmp_path) / "remote1"
    assert (folder / "remote1.gbw").read_bytes() == b"orbitals"
    assert b"TERMINATED NORMALLY" in (folder / "remote1.out").read_bytes()
    assert fake.files_under(calc.remote_dir) == {}          # deleted after verify
    # where it runs was recorded (and so persisted) before ORCA started there
    assert at_launch == [("gcp", calc.remote_dir)]


def test_a_remote_running_calc_is_reattached_on_its_device(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    fake = FakeTransport()
    rdir = "/home/u/orcadesk-runs/re1.20261123-120000"
    fake.write(rdir + "/re1.out", _OUT)
    fake.alive = False                  # it finished while ORCAdesk was closed
    eng = QueueEngine("orca-not-needed", str(tmp_path), QueueCallbacks(),
                      budget=ResourceBudget(max_jobs=1, cores=1, ram_mb=1),
                      device_entries=[normalize_device({"alias": "gcp", "enabled": True})],
                      transport=fake, device_info=lambda a: _info(ok=False))
    calc = make_calc("re1", state=CalcState.RUNNING)
    calc.device, calc.remote_dir, calc.pid, calc.create_time = "gcp", rdir, 4242, 999.0
    calc.run_dir = "re1"
    eng.run_all([calc])
    assert calc.state == CalcState.DONE, calc.message
    assert b"TERMINATED" in (Path(tmp_path) / "re1" / "re1.out").read_bytes()


def test_a_job_reattached_before_the_first_probe_gets_the_workdir_later(tmp_path):
    # After a restart the jobs were reattached before the device's first
    # probe came back, so their runners had workdir "" and the delete guard
    # refused every folder they fetched (15 on the AWS device, 2026-11-23).
    # The watch thread's probe now fills an empty workdir in -- never a set one.
    import types
    eng = QueueEngine("orca", str(tmp_path), QueueCallbacks(),
                      budget=ResourceBudget(max_jobs=1, cores=1, ram_mb=1),
                      device_entries=[normalize_device({"alias": "gcp", "enabled": True})],
                      transport=FakeTransport(), device_info=lambda a: _info(ok=False),
                      device_probe=lambda e: _info())
    empty = types.SimpleNamespace(workdir="")
    kept = types.SimpleNamespace(workdir="/somewhere/else")
    eng._slots["a"] = queue_mod._JobSlot(name="a", device="gcp", runner=empty)
    eng._slots["b"] = queue_mod._JobSlot(name="b", device="gcp", runner=kept)
    t = threading.Thread(target=eng._watch_devices, daemon=True)
    t.start()
    deadline = time.monotonic() + 10
    while not empty.workdir and time.monotonic() < deadline:
        time.sleep(0.05)
    eng._watch_stop.set()
    t.join(5)
    assert empty.workdir == "/home/u/orcadesk-runs"
    assert kept.workdir == "/somewhere/else"


def test_a_device_entry_saved_mid_run_changes_its_budget_at_once(tmp_path):
    # A laptop set to 12 cores in the Compute devices card kept its run-start
    # 16 until the next Run (2026-11-23). The running engine now takes the
    # saved entry; what only the probe knows (workdir, online) is kept.
    eng = QueueEngine("orca", str(tmp_path), QueueCallbacks(),
                      budget=ResourceBudget(max_jobs=1, cores=1, ram_mb=1),
                      device_entries=[normalize_device({"alias": "gcp", "enabled": True})],
                      transport=FakeTransport(), device_info=lambda a: _info())
    assert eng._devices["gcp"].budget.cores == 8 and eng._devices["gcp"].online
    eng.update_device_entry(normalize_device({"alias": "gcp", "enabled": True,
                                              "cores": 6, "ram_mb": 30000}))
    dev = eng._devices["gcp"]
    assert (dev.budget.cores, dev.budget.ram_mb) == (6, 30000)
    assert dev.online and dev.workdir == "/home/u/orcadesk-runs"
    # switched off mid-run: no new jobs there
    eng.update_device_entry({"alias": "gcp", "enabled": False})
    assert eng._pool_state["gcp"] == queue_mod.POOL_STOPPED
    assert not eng._pool_takes("gcp")


def test_the_store_hands_a_device_entry_only_to_a_running_engine():
    from orcamgr.state.store import QueueStore
    store = QueueStore()
    assert store.update_run_device({"alias": "gcp", "enabled": True}) is False
    got = []

    class _Engine:
        def update_device_entry(self, entry):
            got.append(entry)
    store._running, store._engine = True, _Engine()
    try:
        assert store.update_run_device({"alias": "gcp", "enabled": True}) is True
    finally:
        store._running, store._engine = False, None
    assert got == [{"alias": "gcp", "enabled": True}]


# ---- persistence --------------------------------------------------------------

def test_where_a_calc_ran_round_trips_but_no_client_can_set_it():
    c = make_calc("x", state=CalcState.RUNNING)
    c.device, c.remote_dir = "gcp", "/home/u/orcadesk-runs/x.1"
    d = calc_to_session_dict(c)
    back = calc_from_session_dict(d)
    assert (back.device, back.remote_dir) == ("gcp", "/home/u/orcadesk-runs/x.1")
    assert calc_from_dict(dict(d)).device == ""
    bad = dict(d, device="-oProxyCommand=x", remote_dir="relative")
    assert calc_from_session_dict(bad).device == ""


def test_a_windows_devices_folder_survives_a_restart():
    # Restore used to accept POSIX folders only, so every job on a Windows
    # device came back with remote_dir "" -- synced and fetched from nowhere
    # and flagged Disconnected seconds after the app restarted (the laptop,
    # 2026-11-23). Its clean-up entries were dropped the same way.
    c = make_calc("x", state=CalcState.RUNNING)
    c.device, c.remote_dir = "laptop", "C:/Users/pc/orcadesk-runs/sys/x.20261123-191722"
    back = calc_from_session_dict(calc_to_session_dict(c))
    assert back.remote_dir == "C:/Users/pc/orcadesk-runs/sys/x.20261123-191722"
    for bad in ("C:/Users/pc/../x", "relative/x", "C:relative", ""):
        assert calc_from_session_dict(dict(calc_to_session_dict(c), remote_dir=bad)).remote_dir == ""
    from orcamgr.state.store import _clean_cleanup_entry
    e = _clean_cleanup_entry({"device": "laptop", "pid": 1, "create_time": 2.0,
                              "remote_dir": "C:/Users/pc/orcadesk-runs/sys/x.1",
                              "calc_name": "x", "launched_at": "1"})
    assert e is not None and e["remote_dir"] == "C:/Users/pc/orcadesk-runs/sys/x.1"


def test_reconcile_leaves_a_remote_running_calc_alone():
    c = make_calc("x", state=CalcState.RUNNING)
    c.device, c.pid, c.create_time = "gcp", 1, 1.0
    reconcile_calcs([c])
    assert c.state == CalcState.RUNNING and c.pid == 1


# ---- the clean-up list (design §6.3) -------------------------------------------

from orcamgr.core.queue import CLEANUP_PENDING_MESSAGE   # noqa: E402
from orcamgr.state.store import QueueStore                # noqa: E402

_RDIR = "/home/u/orcadesk-runs/job.20261123-120000"


def _cancelled_store():
    store = QueueStore()
    c = make_calc("job")
    store.add(c)
    c = store.get("job")
    c.state = CalcState.CANCELLED
    c.message = CLEANUP_PENDING_MESSAGE
    c.device, c.remote_dir, c.run_dir = "gcp", _RDIR, "job"
    store.add_remote_cleanup({"device": "gcp", "pid": 4242, "create_time": 999,
                              "remote_dir": _RDIR, "calc_name": "job",
                              "launched_at": "20261123-120000"})
    return store, c


def test_a_device_back_settles_its_entries_and_brings_the_folder_home(tmp_path):
    store, c = _cancelled_store()
    fake = FakeTransport()
    fake.write(_RDIR + "/job.out", b"partial\n")
    assert store.settle_remote_cleanup("gcp", fake, "/home/u/orcadesk-runs",
                                       str(tmp_path)) == 1
    assert store.remote_cleanup_entries() == []
    assert (Path(tmp_path) / "job" / "job.out").read_bytes() == b"partial\n"
    assert fake.files_under(_RDIR) == {} and not fake.alive
    assert c.message == "Cancelled by user."


def test_a_calc_rerun_since_is_not_overwritten_by_the_old_folder(tmp_path):
    store, c = _cancelled_store()
    c.state = CalcState.RUNNING
    c.remote_dir = "/home/u/orcadesk-runs/job.20261123-130000"   # a new launch
    fake = FakeTransport()
    fake.write(_RDIR + "/job.out", b"old partial\n")
    store.settle_remote_cleanup("gcp", fake, "/home/u/orcadesk-runs", str(tmp_path))
    assert not (Path(tmp_path) / "job" / "job.out").exists()
    assert fake.files_under(_RDIR) == {}


def test_an_unreachable_device_keeps_its_entries(tmp_path):
    store, _ = _cancelled_store()
    fake = FakeTransport()
    fake.reachable = False
    assert store.settle_remote_cleanup("gcp", fake, "/home/u/orcadesk-runs",
                                       str(tmp_path)) == 0
    assert len(store.remote_cleanup_entries("gcp")) == 1


def test_forgetting_a_device_drops_its_entries():
    store, c = _cancelled_store()
    store.forget_remote_device("gcp")
    assert store.remote_cleanup_entries() == [] and c.message == "Cancelled by user."


def test_the_cleanup_list_round_trips_through_the_session():
    store, _ = _cancelled_store()
    store.save_session()
    again = QueueStore()
    again.load_session()
    assert again.remote_cleanup_entries()[0]["remote_dir"] == _RDIR


def test_a_bad_cleanup_entry_is_refused():
    store = QueueStore()
    store.add_remote_cleanup({"device": "-oProxyCommand=x", "remote_dir": "/x"})
    store.add_remote_cleanup({"device": "gcp", "remote_dir": "relative"})
    assert store.remote_cleanup_entries() == []
