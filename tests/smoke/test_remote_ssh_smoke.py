"""Opt-in smoke test of the remote-device pipeline against a REAL SSH host.

The unit tests drive orcamgr/remote/ through a fake transport; this one uses
the real ``SshTransport`` (the system OpenSSH client) against a host from the
user's ``~/.ssh/config``, so what it proves is the plumbing the fakes cannot:
the probe script on a real shell, sftp upload, the ``setsid`` launch, the
10-second sync (liveness + the .out's new bytes, base64), the whole-folder
fetch with its name/size verification, the guarded remote delete, and cancel.

It does NOT need ORCA: the host must carry a *fake* ORCA that replays a real
finished output -- ``~/orca_fake/orca`` beside a dummy ``orca_2mkl`` and a
``template.out`` -- and that refuses any input not marked
``ORCADESK-SMOKE-TEST`` (so a real calculation sent there by mistake fails
instead of receiving fake results). A WSL distro with ``openssh-server`` on
port 2222 serves as the host on a single machine.

Nothing here touches the app's queue, session or settings: the run folder is
pytest's tmp_path and the remote folders live under ``~/orcadesk-smoke``.

Run with:

    ORCADESK_SMOKE_REMOTE=wsl-orca python -m pytest tests/smoke/test_remote_ssh_smoke.py -v

About two minutes (three replays of ~40 s each).
"""
from __future__ import annotations

import os
import posixpath
import threading
import time
from pathlib import Path

import pytest

from orcamgr.core.parser import parse_file
from orcamgr.core.runner import OrcaCancelled
from orcamgr.remote.devices import parse_ssh_config, probe
from orcamgr.remote.runner import (RemoteOrcaRunner, launch_stamp,
                                   remote_dir_for, remote_liveness)
from orcamgr.remote.ssh import SshTransport

ALIAS = os.environ.get("ORCADESK_SMOKE_REMOTE", "").strip()
pytestmark = pytest.mark.skipif(not ALIAS, reason="set ORCADESK_SMOKE_REMOTE=<ssh alias>")

WORKDIR = "~/orcadesk-smoke"
MARKED = ("! SP\n# ORCADESK-SMOKE-TEST\n* xyz 0 1\nH 0 0 0\nH 0 0 0.74\n*\n")
UNMARKED = ("! SP\n* xyz 0 1\nH 0 0 0\nH 0 0 0.74\n*\n")


@pytest.fixture(scope="module")
def transport():
    t = SshTransport()
    assert t.available(), "no OpenSSH client found"
    return t


@pytest.fixture(scope="module")
def info(transport):
    i = probe(transport, ALIAS, workdir=WORKDIR)
    assert i.usable, f"{ALIAS} is not ready: {i.message}"
    return i


def _folder(tmp_path: Path, text: str) -> Path:
    d = tmp_path / "smoke" / "job"
    d.mkdir(parents=True)
    (d / "job.inp").write_text(text, encoding="utf-8")
    return d


def _runner(transport, info) -> RemoteOrcaRunner:
    rdir = remote_dir_for(info.workdir, "smoke/job", launch_stamp())
    return RemoteOrcaRunner(transport, ALIAS, info.orca_path, info.workdir, rdir)


def _exists(transport, path: str) -> bool:
    res = transport.run(ALIAS, f"[ -e '{path}' ] && echo yes || echo no", timeout=30)
    assert res.ok, res.err
    return res.out.strip() == "yes"


def test_the_host_is_in_the_ssh_config_and_probes_ready(info):
    assert ALIAS in parse_ssh_config()
    assert info.os == "Linux" and info.cores >= 1 and info.mem_total_mb > 0
    assert info.has_setsid
    assert info.orca_path.endswith("/orca")
    assert info.workdir.startswith("/")          # "~" expanded on the device


def test_a_run_syncs_every_10_s_then_fetches_verifies_and_deletes(transport, info, tmp_path):
    d = _folder(tmp_path, MARKED)
    r = _runner(transport, info)
    out = d / "job.out"
    pid, start = r.launch(d / "job.inp", out)
    assert pid > 0 and start > 0

    # watch the LOCAL .out grow: it should grow in steps, one per sync
    sizes, stop = [], threading.Event()

    def watch():
        while not stop.is_set():
            try:
                s = out.stat().st_size
            except OSError:
                s = -1
            if not sizes or sizes[-1] != s:
                sizes.append(s)
            time.sleep(0.5)
    w = threading.Thread(target=watch, daemon=True)
    w.start()
    lines = []
    t0 = time.monotonic()
    try:
        r.monitor(out, on_line=lines.append)
    finally:
        stop.set()
        w.join(5)
    took = time.monotonic() - t0

    expected = int(transport.run(ALIAS, "wc -l < ~/orca_fake/template.out").out.strip())
    assert len(lines) >= expected - 1
    assert len(sizes) >= 3, f"the .out did not grow in steps: {sizes}"
    assert took >= 20                              # it ran, it was not a single shot
    r_ = parse_file(out)
    assert r_.terminated_normally and r_.final_energy_eh is not None
    assert (d / "job.gbw").read_text().startswith("fake orbitals")
    assert (d / "job_property.txt").exists()
    assert not _exists(transport, r.remote_dir), "remote folder left behind"


def test_the_fake_refuses_an_unmarked_input(transport, info, tmp_path):
    d = _folder(tmp_path, UNMARKED)
    r = _runner(transport, info)
    r.launch(d / "job.inp", d / "job.out")
    r.monitor(d / "job.out")
    text = (d / "job.out").read_text()
    assert "FAKE ORCA" in text and "error termination" in text
    assert not parse_file(d / "job.out").terminated_normally
    assert not _exists(transport, r.remote_dir)


def test_cancel_stops_the_remote_job_fetches_and_deletes(transport, info, tmp_path):
    d = _folder(tmp_path, MARKED)
    r = _runner(transport, info)
    out = d / "job.out"
    pid, start = r.launch(d / "job.inp", out)
    caught = []

    def run():
        try:
            r.monitor(out)
        except OrcaCancelled as e:
            caught.append(e)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(15)                                 # past the first sync or two
    r.cancel()
    t.join(120)
    assert caught, "monitor did not end with a cancel"
    assert remote_liveness(transport, ALIAS, pid, start) is False
    assert not _exists(transport, r.remote_dir)
    partial = out.read_text()
    assert partial and "ORCA TERMINATED NORMALLY" not in partial
