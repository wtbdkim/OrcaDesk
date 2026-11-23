"""CREST searches on a remote LINUX device (phase 1 kept CREST here): the
probe finds the device's CREST and asks that binary whether its --cinp parser
takes "force constant=" / "reference=", placement admits a search only where
CREST is (by threads, not MPI), the launch sends exactly the search's own
files, and the fetched folder goes through the local CREST result path --
fresh and after a reattach. Against the fake transport; the real-device run is
tests/smoke/test_remote_crest_smoke.py."""
from __future__ import annotations

import threading
from pathlib import Path

import orcamgr.core.queue as queue_mod
import orcamgr.remote.devices as devices_mod
from orcamgr.core.input_generator import StepConfig
from orcamgr.core.queue import CalcState, QueueCallbacks, QueueEngine
from orcamgr.core.resources import ResourceBudget
from orcamgr.remote.devices import interpret_probe, normalize_device, probe
from orcamgr.remote.runner import RemoteCrestRunner, crest_upload_names
from orcamgr.remote.ssh import RunResult

from fake_transport import FakeTransport
from test_queue_engine import EngineHarness, make_calc

_READY = """os=Linux
home=/home/u
cores=16
mem_total_kb=67108864
mem_avail_kb=60000000
orca=/home/u/orca_6_1_1_linux/orca
mpirun={mpi}
setsid=/usr/bin/setsid
crest={crest}
crest_version=Version 3.0.2, Tue, 16 June 10:18:10, 06/16/2026
workdir=/home/u/orcadesk-runs
free_kb=500000000
"""

ETHANOL = ("C 1.275 -0.252 -0.016\nC -0.015 0.561 0.033\nO -1.169 -0.242 -0.056\n"
           "H 1.303 -0.853 -0.921\nH 2.134 0.413 -0.007\nH 1.340 -0.916 0.845\n"
           "H -0.040 1.164 0.953\nH -0.068 1.238 -0.823\nH -1.139 -0.902 0.645")

# a two-conformer ensemble as CREST writes it (energy on the comment line)
_ENSEMBLE = ("9\n -12.000100\n" + ETHANOL + "\n9\n -11.999000\n" + ETHANOL + "\n").encode()


def _info(crest="/home/u/.local/bin/crest", mpi="/usr/bin/mpirun", extended=True):
    info = interpret_probe("aws", RunResult(0, _READY.format(crest=crest, mpi=mpi)))
    info.crest_extended = extended and bool(crest)
    return info


def _crest_calc(name="conf", threads=4, constraints=None, **kw):
    cfg = StepConfig.from_dict({"kind": "crest_conf", "crest_threads": threads,
                                "crest_preset": "quick",
                                "geom_constraints": constraints or []})
    c = make_calc(name, kind="crest_conf", **kw)
    c.config = cfg
    c.xyz = ETHANOL
    return c


# ---- the probe ------------------------------------------------------------------

def test_the_probe_reports_crest_and_asks_that_binary_once():
    fake = FakeTransport()
    fake.probe_out = _READY.format(crest="/home/u/.local/bin/crest", mpi="/usr/bin/mpirun")
    devices_mod._crest_cache.clear()
    info = probe(fake, "aws")
    assert info.usable and info.crest_path == "/home/u/.local/bin/crest"
    assert "16 June" in info.crest_version and info.crest_extended is True
    probe(fake, "aws")
    assert fake.crest_probes == 1                     # remembered per binary
    # the 3.0.2 release's segfault reads as "no"
    devices_mod._crest_cache.clear()
    fake.crest_probe_out = "PROBE_RC=139\nPROBE_CRASH=1\n"
    assert probe(fake, "aws").crest_extended is False


def test_no_crest_on_the_device_means_no_crest_fields():
    info = interpret_probe("aws", RunResult(0, _READY.format(crest="", mpi="/usr/bin/mpirun")))
    assert info.usable and info.crest_path == "" and info.crest_version == ""
    assert "crest_path" in info.to_payload()


# ---- placement ------------------------------------------------------------------

def _with_device(harness, info):
    eng = harness.engine
    entry = normalize_device({"alias": "aws", "enabled": True})
    fresh = QueueEngine(eng.orca_path, str(eng.workspace_root), eng.cb,
                        budget=eng.budget, device_entries=[entry],
                        transport=FakeTransport(), device_info=lambda a: info)
    fresh._run_calc = harness._fake_run_calc
    fresh._run_crest_calc = harness._fake_run_calc
    harness.engine = fresh
    return fresh


def _where(harness, names, barrier=None):
    seen = {}

    def note(c):
        seen[c.name] = harness.engine._job.device
        if barrier is not None:
            barrier.wait()
    for n in names:
        harness.behaviors[n] = note
    return seen


def test_a_search_goes_to_a_device_that_has_crest(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    harness = EngineHarness(tmp_path, budget=ResourceBudget(max_jobs=1, cores=8, ram_mb=10**6))
    _with_device(harness, _info())
    gate = threading.Barrier(2, timeout=20)
    where = _where(harness, ["a", "b"], gate)
    harness.engine.run_all([_crest_calc("a", threads=8), _crest_calc("b", threads=8)])
    assert sorted(where.values()) == ["", "aws"]


def test_no_crest_on_the_device_keeps_the_search_here(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    harness = EngineHarness(tmp_path, budget=ResourceBudget(max_jobs=1, cores=8, ram_mb=10**6))
    _with_device(harness, _info(crest=""))
    where = _where(harness, ["a", "b"])
    harness.engine.run_all([_crest_calc("a", threads=8), _crest_calc("b", threads=8)])
    assert where == {"a": "", "b": ""}


def test_crest_threads_need_no_mpi_on_the_device(tmp_path, monkeypatch):
    # -T is OpenMP threads in one process: a device without mpirun still takes
    # a 16-thread search (an ORCA job of 16 ranks it would refuse)
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    harness = EngineHarness(tmp_path, budget=ResourceBudget(max_jobs=1, cores=16, ram_mb=10**6))
    _with_device(harness, _info(mpi=""))
    gate = threading.Barrier(2, timeout=20)
    where = _where(harness, ["a", "b"], gate)
    harness.engine.run_all([_crest_calc("a", threads=16), _crest_calc("b", threads=16)])
    assert sorted(where.values()) == ["", "aws"]


# ---- the launch ------------------------------------------------------------------

def test_a_launch_sends_only_the_searchs_own_files(tmp_path):
    d = tmp_path / "sys" / "conf"
    d.mkdir(parents=True)
    for n in crest_upload_names("conf"):
        (d / n).write_text("x")
    (d / "crest_conformers.xyz").write_text("an older search's ensemble")
    (d / "run_crest.sh").write_text("#!/bin/bash")
    fake = FakeTransport()
    rdir = "/home/u/orcadesk-runs/sys/conf.20261123-120000"
    r = RemoteCrestRunner(fake, "aws", "/home/u/.local/bin/crest", "/home/u/orcadesk-runs",
                          rdir, flags=["--gfn2", "--cinp", "constraints.inp"])
    r.launch(d / "conf.xyz", d / "conf.out")
    assert sorted(fake.files_under(rdir)) == ["conf.xyz", "constraints.inp", "coord.ref"]
    started = [s for s in fake.calls if "setsid sh -c" in s][-1]
    assert "/home/u/.local/bin/crest conf.out conf.xyz --gfn2 --cinp constraints.inp" in started


def _crest_on_device(fake):
    def finish(f, remote_dir):
        f.write(remote_dir + "/conf.out", b"... CREST terminated normally.\n")
        f.write(remote_dir + "/crest_conformers.xyz", _ENSEMBLE)
        f.write(remote_dir + "/crest_best.xyz", _ENSEMBLE[: _ENSEMBLE.index(b"\n9\n") + 1])
        f.alive = False
    fake.on_launch = finish


def _remote_engine(tmp_path, fake, info):
    # this machine cannot take it (1 core), so the 4-thread search goes there
    return QueueEngine("orca-not-needed", str(tmp_path), QueueCallbacks(),
                       budget=ResourceBudget(max_jobs=1, cores=1, ram_mb=10**6),
                       device_entries=[normalize_device({"alias": "aws", "enabled": True})],
                       transport=fake, device_info=lambda a: info)


def test_a_remote_search_comes_back_and_reads_as_a_local_one(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    fake = FakeTransport()
    _crest_on_device(fake)
    calc = _crest_calc(constraints=[{"kind": "C", "atoms": [0], "value": None},
                                    {"kind": "C", "atoms": [8], "value": None}])
    _remote_engine(tmp_path, fake, _info(extended=True)).run_all([calc])
    assert calc.state == CalcState.DONE, calc.message
    assert calc.device == "aws" and calc.result and len(calc.result.conformers) == 2
    folder = Path(tmp_path) / "conf"
    # the extended device got the force constant and the reference structure
    cinp = (folder / "constraints.inp").read_text()
    assert "force constant=0.5" in cinp and "reference=coord.ref" in cinp
    assert (folder / "coord.ref").exists()
    # the local CREST path ran on the fetched folder: the conformer export
    assert len(list((folder / "conformers").glob("*.xyz"))) == 2
    assert fake.files_under(calc.remote_dir) == {}          # deleted after verify


def test_a_device_whose_crest_is_the_302_release_gets_the_plain_file(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    fake = FakeTransport()
    _crest_on_device(fake)
    calc = _crest_calc(constraints=[{"kind": "C", "atoms": [0], "value": None},
                                    {"kind": "C", "atoms": [8], "value": None}])
    _remote_engine(tmp_path, fake, _info(extended=False)).run_all([calc])
    assert calc.state == CalcState.DONE, calc.message
    folder = Path(tmp_path) / "conf"
    assert "force constant" not in (folder / "constraints.inp").read_text()
    assert not (folder / "coord.ref").exists()


def test_a_search_left_running_on_the_device_is_reattached(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_mod, "ram_headroom_mb", lambda: 0)
    fake = FakeTransport()
    rdir = "/home/u/orcadesk-runs/conf.20261123-120000"
    fake.write(rdir + "/conf.out", b"CREST terminated normally.\n")
    fake.write(rdir + "/crest_conformers.xyz", _ENSEMBLE)
    fake.alive = False                  # it finished while ORCAdesk was closed
    eng = _remote_engine(tmp_path, fake, _info())
    calc = _crest_calc(state=CalcState.RUNNING)
    calc.device, calc.remote_dir, calc.pid, calc.create_time = "aws", rdir, 4242, 999.0
    calc.run_dir = "conf"
    eng.run_all([calc])
    assert calc.state == CalcState.DONE, calc.message
    assert len(calc.result.conformers) == 2
    assert (Path(tmp_path) / "conf" / "crest_conformers.xyz").exists()
