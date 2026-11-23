"""The per-device connection cap in orcamgr/remote/ssh.py.

A dispatch burst used to open one ssh per job at once; the AWS device's sshd
(default MaxStartups 10:30:100) dropped the overflow and five launches went
back to PENDING (measured 2026-11-23). The transport now queues: at most
MAX_CONNECTIONS_PER_DEVICE ssh/sftp processes per device at a time, the cap
counted per alias so one busy device never holds up another."""
from __future__ import annotations

import subprocess
import threading
import time

import orcamgr.remote.ssh as ssh_mod
from orcamgr.remote.ssh import RC_TIMEOUT, SshTransport


class _FakeRun:
    def __init__(self, hold: float = 0.05):
        self.lock = threading.Lock()
        self.live: dict = {}
        self.peak: dict = {}
        self.hold = hold

    def __call__(self, argv, input=None, capture_output=True, timeout=None, creationflags=0):
        alias = next(a for a in argv if a in ("dev-a", "dev-b"))
        with self.lock:
            self.live[alias] = self.live.get(alias, 0) + 1
            self.peak[alias] = max(self.peak.get(alias, 0), self.live[alias])
        time.sleep(self.hold)
        with self.lock:
            self.live[alias] -= 1
        return subprocess.CompletedProcess(argv, 0, b"ok\n", b"")


def _burst(transport, alias, n):
    threads = [threading.Thread(target=transport.run, args=(alias, "true")) for _ in range(n)]
    for t in threads:
        t.start()
    return threads


def test_a_burst_never_opens_more_than_the_cap_per_device(monkeypatch):
    fake = _FakeRun()
    monkeypatch.setattr(ssh_mod.subprocess, "run", fake)
    monkeypatch.setattr(ssh_mod, "_gates", {})
    t = SshTransport(ssh="ssh", sftp="sftp")
    threads = _burst(t, "dev-a", 16) + _burst(t, "dev-b", 16)
    for th in threads:
        th.join(10)
    cap = ssh_mod.MAX_CONNECTIONS_PER_DEVICE
    assert fake.peak["dev-a"] <= cap and fake.peak["dev-b"] <= cap
    # the cap is per device: the two bursts ran side by side
    assert fake.peak["dev-a"] == cap and fake.peak["dev-b"] == cap


def test_no_free_slot_within_the_timeout_reads_as_a_timeout(monkeypatch):
    fake = _FakeRun(hold=2.0)
    monkeypatch.setattr(ssh_mod.subprocess, "run", fake)
    monkeypatch.setattr(ssh_mod, "_gates", {})
    t = SshTransport(ssh="ssh", sftp="sftp")
    busy = _burst(t, "dev-a", ssh_mod.MAX_CONNECTIONS_PER_DEVICE)
    time.sleep(0.2)
    res = t.run("dev-a", "true", timeout=1.0)
    assert res.rc == RC_TIMEOUT and res.unreachable     # an unknown, never a "no"
    for th in busy:
        th.join(10)
