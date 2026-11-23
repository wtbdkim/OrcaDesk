"""Spot devices (orcamgr/remote/): a device kept to single points takes
nothing costly to lose, and a job on a spot / preemptible VM is given back to
the queue -- PENDING, never FAILED (P24) -- when the device stays silent past
its limit or the VM was taken away under it. Fake transport, fake clock."""
from __future__ import annotations

import threading

import pytest

import orcamgr.core.queue as queue_mod
from orcamgr.core.queue import (REQUEUED_MESSAGE, CalcState, QueueCallbacks,
                                QueueEngine)
from orcamgr.core.resources import ResourceBudget
from orcamgr.remote.devices import (SINGLE_POINT_KINDS, device_takes,
                                    normalize_device, resolve_device)
from orcamgr.remote.runner import RemoteRequeue
from orcamgr.state.store import QueueStore

from fake_transport import FakeClock, FakeTransport
from test_queue_engine import make_calc
from test_remote_engine import _info
from test_remote_runner import RDIR, WORK, _folder, _runner

_PARTIAL = b"                       ORCA\n  SCF ITERATIONS\n   1   -1.10\n"
_DONE = _PARTIAL + b"FINAL SINGLE POINT ENERGY   -1.1175\n****ORCA TERMINATED NORMALLY****\n"
_ERROR = _PARTIAL + b"ORCA finished by error termination in SCF\n"


# ---- settings -------------------------------------------------------------------

def test_device_settings_default_to_today_and_are_clamped():
    d = normalize_device({"alias": "gcp"})
    assert (d["kinds"], d["spot"], d["requeue_after_min"]) == ("all", False, 5)
    d = normalize_device({"alias": "gcp", "kinds": "single_point", "spot": True,
                          "requeue_after_min": 0})
    assert (d["kinds"], d["spot"], d["requeue_after_min"]) == ("single_point", True, 1)
    # an unknown word is "all" (never a silent lock-out); only a real True is spot
    d = normalize_device({"alias": "gcp", "kinds": "opt_only", "spot": "true",
                          "requeue_after_min": 10**9})
    assert (d["kinds"], d["spot"], d["requeue_after_min"]) == ("all", False, 1440)


def test_single_point_devices_take_only_what_never_moves_the_geometry():
    assert SINGLE_POINT_KINDS == {"sp", "tddft", "nmr"}
    for k in ("sp", "tddft", "nmr"):
        assert device_takes("single_point", k)
    for k in ("opt", "ts_opt", "freq", "opt_freq", "irc", "neb_ts", "general"):
        assert not device_takes("single_point", k) and device_takes("all", k)
    dev = resolve_device(normalize_device({"alias": "gcp", "kinds": "single_point",
                                           "spot": True, "requeue_after_min": 7}), _info())
    assert (dev.kinds, dev.spot, dev.requeue_after_min) == ("single_point", True, 7)


# ---- placement ------------------------------------------------------------------

def test_a_single_point_device_takes_the_sp_and_leaves_the_opt_here(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    entry = normalize_device({"alias": "gcp", "enabled": True, "max_jobs": 2,
                              "kinds": "single_point"})
    eng = QueueEngine("orca-not-needed", str(tmp_path), QueueCallbacks(),
                      budget=ResourceBudget(max_jobs=1, cores=64, ram_mb=10**6),
                      device_entries=[entry], transport=FakeTransport(),
                      device_info=lambda a: _info())
    where, gate = {}, threading.Barrier(2, timeout=20)

    def fake(calc, index):
        where[calc.name] = eng._job.device
        if calc.name in ("hold", "sp1"):
            gate.wait()            # both in flight together: sp1 found a device
        calc.state = CalcState.DONE
    eng._run_calc = fake
    hold, opt1, sp1 = make_calc("hold"), make_calc("opt1", kind="opt"), make_calc("sp1")
    for c in (hold, opt1, sp1):
        c.config.nprocs = 1
    eng.run_all([hold, opt1, sp1])
    assert where == {"hold": "", "sp1": "gcp", "opt1": ""}


# ---- the runner: requeue only on a spot device ------------------------------------

def _spot(fake, minutes=1.0):
    r = _runner(fake)
    r.spot = True
    r.requeue_after = minutes * 60.0
    return r


def test_a_spot_job_silent_past_the_limit_is_given_back(tmp_path):
    d = _folder(tmp_path)
    fake = FakeTransport()
    r = _spot(fake)
    r.launch(d / "job.inp", d / "job.out")
    fake.reachable = False
    with pytest.raises(RemoteRequeue) as ei:
        r.monitor(d / "job.out")
    e = ei.value
    assert e.unreachable and str(e) == REQUEUED_MESSAGE
    # the clean-up kills the old process when the device is back and deletes
    # its folder WITHOUT fetching it: the calc runs again
    assert e.entry["fetch"] is False and e.entry["pid"] == 4242
    assert e.entry["remote_dir"] == RDIR


def test_a_flapping_connection_never_adds_its_outages_up(tmp_path):
    # three outages of ~30 s each against a 60 s limit (90 s in all): any
    # answered sync starts the count again, so the job just carries on
    d = _folder(tmp_path)
    fake = FakeTransport()
    r = _spot(fake)
    r.launch(d / "job.inp", d / "job.out")
    flips = []
    r.on_disconnect = flips.append
    fake.write(RDIR + "/job.out", _DONE)
    fake.liveness_script = ([None] * 30 + [True]) * 3 + [False]
    r.monitor(d / "job.out")
    assert flips == [True, False] * 3                   # orange on and off, as today
    assert b"TERMINATED NORMALLY" in (d / "job.out").read_bytes()
    assert fake.files_under(RDIR) == {}                 # fetched, verified, deleted


def test_a_non_spot_device_is_never_given_back(tmp_path):
    d = _folder(tmp_path)
    fake = FakeTransport()
    r = _runner(fake)                                   # spot stays False
    r.requeue_after = 60.0
    r.launch(d / "job.inp", d / "job.out")
    fake.write(RDIR + "/job.out", _DONE)
    fake.liveness_script = [None] * 300 + [False]      # 5 min of silence, then done
    r.monitor(d / "job.out")
    assert b"TERMINATED NORMALLY" in (d / "job.out").read_bytes()


def test_a_spot_job_dead_after_an_outage_with_no_word_from_orca_was_preempted(tmp_path):
    d = _folder(tmp_path)
    fake = FakeTransport()
    r = _spot(fake)
    r.launch(d / "job.inp", d / "job.out")
    fake.write(RDIR + "/job.out", _PARTIAL)
    fake.liveness_script = [True, None, None, False]   # back fast, job gone
    with pytest.raises(RemoteRequeue) as ei:
        r.monitor(d / "job.out")
    assert ei.value.unreachable is False               # the device answers again
    assert fake.files_under(RDIR) == {}                # deleted now, never fetched
    assert ei.value.entry is None
    assert not (d / "job.gbw").exists()


def test_a_real_orca_failure_on_a_spot_device_is_still_judged(tmp_path):
    d = _folder(tmp_path)
    fake = FakeTransport()
    r = _spot(fake)
    r.launch(d / "job.inp", d / "job.out")
    fake.write(RDIR + "/job.out", _ERROR)
    fake.write(RDIR + "/job.gbw", b"orbitals")
    fake.liveness_script = [True, None, None, False]
    r.monitor(d / "job.out")                           # no requeue: fetched to be judged
    assert b"error termination" in (d / "job.out").read_bytes()
    assert (d / "job.gbw").exists()


def test_a_silent_death_on_a_non_spot_device_keeps_todays_path(tmp_path):
    d = _folder(tmp_path)
    fake = FakeTransport()
    r = _runner(fake)
    r.launch(d / "job.inp", d / "job.out")
    fake.write(RDIR + "/job.out", _PARTIAL)
    fake.liveness_script = [True, None, None, False]
    r.monitor(d / "job.out")                           # fetched; the engine judges it
    assert (d / "job.out").read_bytes() == _PARTIAL


def test_a_reattach_to_a_spot_job_that_died_unseen_requeues(tmp_path):
    d = _folder(tmp_path)
    fake = FakeTransport()
    r = _spot(fake)
    r.adopt(4242, 999)                                 # ORCAdesk was closed meanwhile
    (d / "job.out").write_bytes(b"")
    fake.write(RDIR + "/job.out", _PARTIAL)
    fake.alive = False
    with pytest.raises(RemoteRequeue):
        r.monitor(d / "job.out")


# ---- the engine and the store -----------------------------------------------------

def test_the_engine_puts_a_requeued_row_back_and_runs_it_again(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    cleanups, messages = [], []
    cb = QueueCallbacks(remote_cleanup=cleanups.append,
                        calc_update=lambda i, c: messages.append((c.name, c.state, c.message)))
    entry = normalize_device({"alias": "gcp", "enabled": True, "spot": True,
                              "kinds": "single_point"})
    eng = QueueEngine("orca-not-needed", str(tmp_path), cb,
                      budget=ResourceBudget(max_jobs=1, cores=64, ram_mb=10**6),
                      device_entries=[entry], transport=FakeTransport(),
                      device_info=lambda a: _info())
    gate, tried = threading.Event(), []

    def fake(calc, index):
        tried.append((calc.name, eng._job.device))
        if calc.name == "hold":
            gate.wait(10)
        elif eng._job.device == "gcp":
            gate.set()
            raise RemoteRequeue(REQUEUED_MESSAGE,
                                {"device": "gcp", "pid": 4242, "create_time": 999.0,
                                 "remote_dir": "/home/u/orcadesk-runs/sp1.1",
                                 "calc_name": "sp1", "launched_at": "1",
                                 "fetch": False}, unreachable=True)
        calc.state = CalcState.DONE
    eng._run_calc = fake
    hold, sp1 = make_calc("hold"), make_calc("sp1")
    hold.config.nprocs = sp1.config.nprocs = 1
    eng.run_all([hold, sp1])
    assert ("sp1", CalcState.PENDING, REQUEUED_MESSAGE) in messages
    assert tried[-1] == ("sp1", "") and sp1.state == CalcState.DONE
    assert cleanups and cleanups[0]["fetch"] is False
    assert not eng._devices["gcp"].online


def test_a_requeue_entry_is_deleted_without_fetching_when_the_device_is_back(tmp_path):
    store = QueueStore()
    c = make_calc("sp1")
    store.add(c)
    c = store.get("sp1")
    c.state = CalcState.CANCELLED          # even a row that looks like a cancel
    c.remote_dir, c.run_dir = RDIR, "sp1"
    store.add_remote_cleanup({"device": "gcp", "pid": 4242, "create_time": 999,
                              "remote_dir": RDIR, "calc_name": "sp1",
                              "launched_at": "20261123-120000", "fetch": False})
    assert store.remote_cleanup_entries()[0]["fetch"] is False
    fake = FakeTransport()
    fake.write(RDIR + "/sp1.out", b"partial\n")
    assert store.settle_remote_cleanup("gcp", fake, WORK, str(tmp_path)) == 1
    assert not fake.alive and fake.files_under(RDIR) == {}
    assert not (tmp_path / "sp1" / "sp1.out").exists()     # never brought back
