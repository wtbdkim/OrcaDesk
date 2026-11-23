"""Per-pool run controls (core/queue.py QueueEngine pools).

Each pool -- "" for this machine, a device alias for a remote one -- is
running (takes new jobs), draining (finishes what it has, takes none) or
stopped (takes none). _place honours it; a job already running somewhere is
reattached whatever its pool says. The whole-queue Stop-after-current drains
every pool and keeps the walk alive until nothing is in flight, so a Run in
the meantime resumes the SAME run instead of being refused. A device's Run on
an idle queue starts a run in which only that pool takes jobs.

Today's case (2026-11-23): after "Stop after current" the rail looked exactly
like a stall -- idle devices, 98 pending, no hint. Each pool now reports its
state (RunResources.state / DeviceUsage.state)."""
from __future__ import annotations

import threading

import orcamgr.core.queue as queue_mod
from orcamgr.core.queue import (POOL_DRAINING, POOL_RUNNING, POOL_STOPPED,
                                CalcState, QueueEngine)
from orcamgr.core.resources import ResourceBudget
from orcamgr.core.runner import OrcaCancelled
from orcamgr.remote.devices import normalize_device

from fake_transport import FakeTransport
from test_queue_engine import EngineHarness, make_calc
from test_remote_engine import _info, _record_devices


def _engine(harness, pool_states=None, max_jobs=2):
    """The harness's engine with one enabled device "gcp" and pool states."""
    eng = harness.engine
    entry = normalize_device({"alias": "gcp", "enabled": True, "max_jobs": max_jobs})
    fresh = QueueEngine(eng.orca_path, str(eng.workspace_root), eng.cb,
                        budget=eng.budget, device_entries=[entry],
                        transport=FakeTransport(), device_info=lambda a: _info(),
                        pool_states=pool_states)
    fresh._run_calc = harness._fake_run_calc
    fresh._run_mlip_calc = harness._fake_run_calc
    fresh._run_crest_calc = harness._fake_run_calc
    harness.engine = fresh
    return fresh


def _one_core(*names):
    calcs = [make_calc(n) for n in names]
    for c in calcs:
        c.config.nprocs = 1
    return calcs


def _harness(tmp_path, monkeypatch, local_jobs=1):
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    return EngineHarness(tmp_path, budget=ResourceBudget(max_jobs=local_jobs, cores=64,
                                                         ram_mb=10**6))


def test_a_stopped_device_takes_nothing(tmp_path, monkeypatch):
    harness = _harness(tmp_path, monkeypatch)
    _engine(harness, pool_states={"gcp": POOL_STOPPED})
    where = _record_devices(harness, ["a", "b", "c"])
    calcs = _one_core("a", "b", "c")
    harness.engine.run_all(calcs)
    assert set(where.values()) == {""}                  # all on this machine
    assert all(c.state == CalcState.DONE for c in calcs)


def test_a_device_only_run_leaves_this_machine_idle_and_ends(tmp_path, monkeypatch):
    # a device's Run on an idle queue: only that pool takes jobs; a row only
    # this machine can run (CREST here) stays PENDING and the run ends
    harness = _harness(tmp_path, monkeypatch)
    _engine(harness, pool_states={"": POOL_STOPPED, "gcp": POOL_RUNNING})
    where = _record_devices(harness, ["a", "b"])
    a, b = _one_core("a", "b")
    local_only = make_calc("search", kind="crest_conf")
    harness.engine.run_all([a, b, local_only])
    assert set(where.values()) == {"gcp"}
    assert (a.state, b.state) == (CalcState.DONE, CalcState.DONE)
    assert local_only.state == CalcState.PENDING        # never stamped BLOCKED
    assert "left pending" in harness.log_text()


def test_a_device_drained_mid_run_starts_nothing_more_there(tmp_path, monkeypatch):
    harness = _harness(tmp_path, monkeypatch, local_jobs=1)
    eng = _engine(harness, max_jobs=1)
    started = threading.Event()
    release = threading.Event()
    where = {}

    def first(c):
        where[c.name] = eng._job.device
        started.set()
        release.wait(20)

    def note(c):
        where[c.name] = eng._job.device
    harness.behaviors.update({"a": first, "b": first, "c": note, "d": note})
    calcs = _one_core("a", "b", "c", "d")
    run = threading.Thread(target=lambda: eng.run_all(calcs))
    run.start()
    assert started.wait(20)
    # a and b fill both pools; drain the device before either finishes
    deadline = threading.Event()
    deadline.wait(0.5)
    eng.set_pool_state("gcp", POOL_DRAINING)
    assert eng.resource_usage()["devices"][0]["state"] in (POOL_DRAINING, POOL_STOPPED)
    release.set()
    run.join(30)
    assert all(c.state == CalcState.DONE for c in calcs)
    assert where["c"] == "" and where["d"] == ""        # nothing new on gcp
    assert eng.resource_usage()["devices"][0]["state"] == POOL_STOPPED


def test_cancelling_one_device_cancels_only_its_jobs(tmp_path, monkeypatch):
    harness = _harness(tmp_path, monkeypatch, local_jobs=1)
    eng = _engine(harness, max_jobs=1)
    in_flight = threading.Semaphore(0)
    release = threading.Event()

    class _Runner:
        def __init__(self):
            self.cancelled = threading.Event()

        def cancel(self):
            self.cancelled.set()

        def detach(self):
            pass

    def hold(c):
        r = eng._use_runner(_Runner())
        in_flight.release()
        while not release.is_set():
            if r.cancelled.wait(0.05):
                raise OrcaCancelled("Cancelled by user.")
    harness.behaviors.update({"local": hold, "remote": hold})
    local, remote = _one_core("local", "remote")
    run = threading.Thread(target=lambda: eng.run_all([local, remote]))
    run.start()
    assert in_flight.acquire(timeout=20) and in_flight.acquire(timeout=20)
    on = {s.name: s.device for s in eng._slots.values()}
    gone = "local" if on["local"] == "gcp" else "remote"
    kept = "remote" if gone == "local" else "local"
    assert eng.cancel_pool("gcp") == 1
    threading.Event().wait(0.5)
    release.set()
    run.join(30)
    by = {"local": local, "remote": remote}
    assert by[gone].state == CalcState.CANCELLED
    assert by[kept].state == CalcState.DONE              # the other pool went on
    assert eng.pool_states()["gcp"] == POOL_STOPPED


def test_a_run_during_a_drain_resumes_the_same_run(tmp_path, monkeypatch):
    harness = _harness(tmp_path, monkeypatch, local_jobs=1)
    eng = harness.engine            # this machine only
    started = threading.Event()
    release = threading.Event()

    def first(_c):
        started.set()
        release.wait(20)
    harness.behaviors["a"] = first
    calcs = _one_core("a", "b", "c")
    run = threading.Thread(target=lambda: eng.run_all(calcs))
    run.start()
    assert started.wait(20)
    eng.request_stop_after_current()
    assert eng.resource_usage()["state"] == POOL_DRAINING   # visible, not a stall
    eng.resume_pools()
    release.set()
    run.join(30)
    assert all(c.state == CalcState.DONE for c in calcs)
    assert harness.calls == ["a", "b", "c"]


def test_a_drain_with_nothing_in_flight_reads_stopped_and_ends(tmp_path, monkeypatch):
    harness = _harness(tmp_path, monkeypatch, local_jobs=1)
    eng = harness.engine
    harness.behaviors["a"] = lambda _c: eng.request_stop_after_current()
    calcs = _one_core("a", "b")
    eng.run_all(calcs)
    assert calcs[1].state == CalcState.PENDING
    assert eng.pool_states() == {"": POOL_STOPPED}


def test_a_new_run_never_inherits_a_drain(tmp_path, monkeypatch):
    # the resumed run after a restart (resume_session_if_running) and every
    # Run build a FRESH engine: the drain of the last one is not carried over
    from orcamgr.state.store import QueueStore
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    store = QueueStore()
    engines = []

    def factory():
        h = EngineHarness(tmp_path, budget=ResourceBudget(max_jobs=1, cores=64, ram_mb=10**6))
        engines.append(h.engine)
        if len(engines) == 1:
            h.behaviors["x"] = lambda _c: store.request_stop_after_current()
        return h.engine
    store.add(make_calc("x"))
    store.add(make_calc("y"))
    store.start_run(factory)
    store.wait_for_run(20)
    assert store.get("y").state == CalcState.PENDING
    store.start_run(factory)
    store.wait_for_run(20)
    assert engines[1].pool_states() == {"": POOL_RUNNING}
    assert store.get("y").state == CalcState.DONE


def test_the_idle_snapshot_lists_each_enabled_device_stopped():
    from orcamgr.state.store import QueueStore
    store = QueueStore()
    store.idle_devices = lambda: [{"id": "aws", "online": True}, {"id": "lap", "online": False}]
    r = store.run_resources()
    assert r["state"] == POOL_STOPPED
    assert [(d["id"], d["state"], d["online"]) for d in r["devices"]] == [
        ("aws", POOL_STOPPED, True), ("lap", POOL_STOPPED, False)]
