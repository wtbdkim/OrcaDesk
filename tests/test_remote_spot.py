"""Spot devices (orcamgr/remote/): a device's preferred kinds go to it first
-- a SOFT preference, the user's decision of 2026-11-23, which replaced a
hard "single points only" switch: it still takes other ORCA kinds when none
it prefers is ready -- and a job on a spot / preemptible VM is given back to
the queue -- PENDING, never FAILED (P24) -- when the device stays silent past
its limit or the VM was taken away under it. Fake transport, fake clock."""
from __future__ import annotations

import threading

import pytest

import orcamgr.core.queue as queue_mod
from orcamgr.core.queue import (REQUEUED_MESSAGE, CalcState, QueueCallbacks,
                                QueueEngine)
from orcamgr.core.resources import ResourceBudget
from orcamgr.remote.devices import (ORCA_KINDS, device_prefers, normalize_device,
                                    resolve_device)
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
    assert (d["prefer"], d["spot"], d["requeue_after_min"]) == ([], False, 5)
    d = normalize_device({"alias": "gcp", "prefer": ["sp", "nmr", "sp"], "spot": True,
                          "requeue_after_min": 0})
    assert (d["prefer"], d["spot"], d["requeue_after_min"]) == (["nmr", "sp"], True, 1)
    # unknown and non-ORCA kinds are dropped; only a real True is spot
    d = normalize_device({"alias": "gcp", "prefer": ["opt_only", "crest_conf", 3, "opt"],
                          "spot": "true", "requeue_after_min": 10**9})
    assert (d["prefer"], d["spot"], d["requeue_after_min"]) == (["opt"], False, 1440)
    assert normalize_device({"alias": "gcp", "prefer": "sp"})["prefer"] == []


def test_the_retired_kinds_switch_migrates_to_a_preference():
    # 6b845eb's hard filter, replaced by the user's decision: what it meant
    # becomes a preference, and the key itself is gone
    d = normalize_device({"alias": "gcp", "kinds": "single_point"})
    assert d["prefer"] == ["tddft", "nmr", "sp"] and "kinds" not in d
    assert normalize_device({"alias": "gcp", "kinds": "all"})["prefer"] == []
    # an explicit preference wins over the old switch
    assert normalize_device({"alias": "gcp", "kinds": "single_point",
                             "prefer": ["opt"]})["prefer"] == ["opt"]
    dev = resolve_device({"alias": "gcp", "kinds": "single_point", "spot": True,
                          "requeue_after_min": 7}, _info())
    assert (dev.prefer, dev.spot, dev.requeue_after_min) == (("tddft", "nmr", "sp"), True, 7)


def test_a_preference_is_soft_and_empty_means_none():
    assert device_prefers((), "opt") and device_prefers(("sp",), "sp")
    assert not device_prefers(("sp",), "opt")
    # the list ORCAdesk offers is exactly the ORCA kinds the engine knows
    assert set(ORCA_KINDS) == {k for k in queue_mod.KNOWN_KINDS
                               if not k.startswith(("mlip", "crest"))}


# ---- placement ------------------------------------------------------------------

def _pref_engine(tmp_path, prefer, max_jobs=2):
    entry = normalize_device({"alias": "gcp", "enabled": True, "max_jobs": max_jobs,
                              "prefer": prefer})
    return QueueEngine("orca-not-needed", str(tmp_path), QueueCallbacks(),
                       budget=ResourceBudget(max_jobs=1, cores=64, ram_mb=10**6),
                       device_entries=[entry], transport=FakeTransport(),
                       device_info=lambda a: _info())


def _run_holding(eng, calcs, together):
    """Run ``calcs`` with the fake job body; the names in ``together`` wait
    for each other, so they are provably in flight at the same time."""
    where, gate = {}, threading.Barrier(len(together), timeout=20)

    def fake(calc, index):
        where[calc.name] = eng._job.device
        if calc.name in together:
            gate.wait()
        calc.state = CalcState.DONE
    eng._run_calc = fake
    for c in calcs:
        c.config.nprocs = 1
    eng.run_all(calcs)
    return where


def test_a_device_preferring_sp_takes_the_sp_behind_an_opt(tmp_path, monkeypatch):
    # this machine is full (hold); an Opt and an SP are ready, the Opt first in
    # the queue: the device that prefers single points takes the SP first --
    # and, with room left and nothing it prefers ready, the Opt after it
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    eng = _pref_engine(tmp_path, ["sp"])
    hold, opt1, sp1 = make_calc("hold"), make_calc("opt1", kind="opt"), make_calc("sp1")
    order = []
    real = eng._pick_ready

    def spy(*a, **kw):
        got = real(*a, **kw)
        if got is not None:
            order.append(got[1].name)
        return got
    eng._pick_ready = spy
    where = _run_holding(eng, [hold, opt1, sp1], {"hold", "sp1", "opt1"})
    assert order[:3] == ["hold", "sp1", "opt1"]
    assert where == {"hold": "", "sp1": "gcp", "opt1": "gcp"}


def test_with_only_an_opt_ready_the_sp_device_still_takes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    eng = _pref_engine(tmp_path, ["sp"])
    hold, opt1 = make_calc("hold"), make_calc("opt1", kind="opt")
    assert _run_holding(eng, [hold, opt1], {"hold", "opt1"}) == {"hold": "", "opt1": "gcp"}


def test_a_device_with_no_preference_behaves_as_before(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    eng = _pref_engine(tmp_path, [])
    hold, opt1, sp1 = make_calc("hold"), make_calc("opt1", kind="opt"), make_calc("sp1")
    where = _run_holding(eng, [hold, opt1, sp1], {"hold", "opt1", "sp1"})
    assert where == {"hold": "", "opt1": "gcp", "sp1": "gcp"}


def test_this_machine_still_comes_first_for_a_preferred_kind(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    eng = _pref_engine(tmp_path, ["sp"])
    assert _run_holding(eng, [make_calc("sp1")], {"sp1"}) == {"sp1": ""}


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
                              "prefer": ["sp"]})
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
