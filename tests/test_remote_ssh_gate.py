"""The per-device connection cap and the stop event in orcamgr/remote/ssh.py.

A dispatch burst used to open one ssh per job at once; the AWS device's sshd
(default MaxStartups 10:30:100) dropped the overflow and five launches went
back to PENDING (measured 2026-11-23). The transport now queues: at most
MAX_CONNECTIONS_PER_DEVICE ssh/sftp processes per device at a time, the cap
counted per alias so one busy device never holds up another.

Closing the window waited for every remote monitor's in-flight call (a sync
can take 60 s, a fetch much longer, and the cap queues the rest behind them):
a close with jobs on two devices froze the window past 20 s. A call given a
``stop`` event now gives up at once -- while waiting for a slot or while its
child runs -- and kills the child (RC_ABORTED, an unknown)."""
from __future__ import annotations

import subprocess
import threading
import time

import orcamgr.remote.ssh as ssh_mod
from orcamgr.remote.ssh import RC_ABORTED, RC_BUSY, SshTransport


class _FakePopen:
    """Stands in for subprocess.Popen: 'runs' for ``hold`` seconds, counting
    how many run at once per device; kill() ends it early."""
    lock = threading.Lock()
    live: dict = {}
    peak: dict = {}
    killed = 0
    hold = 0.05

    def __init__(self, argv, stdin=None, stdout=None, stderr=None, creationflags=0):
        self.alias = next(a for a in argv if a in ("dev-a", "dev-b"))
        self.returncode = None
        self._end = time.monotonic() + type(self).hold
        self._dead = False
        with type(self).lock:
            type(self).live[self.alias] = type(self).live.get(self.alias, 0) + 1
            type(self).peak[self.alias] = max(type(self).peak.get(self.alias, 0),
                                              type(self).live[self.alias])

    def _finish(self, rc):
        if self.returncode is None:
            self.returncode = rc
            with type(self).lock:
                type(self).live[self.alias] -= 1

    def communicate(self, input=None, timeout=None):
        if self._dead:
            self._finish(-9)
            return b"", b""
        left = self._end - time.monotonic()
        if timeout is not None and left > timeout:
            time.sleep(timeout)
            raise subprocess.TimeoutExpired("ssh", timeout)
        time.sleep(max(0.0, left))
        self._finish(0)
        return b"ok\n", b""

    def kill(self):
        self._dead = True
        with type(self).lock:
            type(self).killed += 1


def _fresh(monkeypatch, hold):
    class P(_FakePopen):
        pass
    P.lock, P.live, P.peak, P.killed, P.hold = threading.Lock(), {}, {}, 0, hold
    monkeypatch.setattr(ssh_mod.subprocess, "Popen", P)
    monkeypatch.setattr(ssh_mod, "_gates", {})
    return P


def _burst(transport, alias, n, **kw):
    threads = [threading.Thread(target=transport.run, args=(alias, "true"), kwargs=kw)
               for _ in range(n)]
    for t in threads:
        t.start()
    return threads


def test_a_burst_never_opens_more_than_the_cap_per_device(monkeypatch):
    P = _fresh(monkeypatch, hold=0.3)
    t = SshTransport(ssh="ssh", sftp="sftp")
    threads = _burst(t, "dev-a", 16) + _burst(t, "dev-b", 16)
    for th in threads:
        th.join(20)
    cap = ssh_mod.MAX_CONNECTIONS_PER_DEVICE
    assert P.peak["dev-a"] <= cap and P.peak["dev-b"] <= cap
    # the cap is per device: the two bursts ran side by side
    assert P.peak["dev-a"] == cap and P.peak["dev-b"] == cap


def test_no_free_slot_within_the_timeout_reads_as_busy_not_as_an_outage(monkeypatch):
    # The device was never asked, so the answer says nothing about it. This
    # used to be RC_TIMEOUT (unreachable): fifteen fetches holding the slots
    # got a healthy AWS device flagged Disconnected and its launches sent back
    # to the queue (2026-11-23). Now RC_BUSY: neither unreachable nor a "no".
    _fresh(monkeypatch, hold=3.0)
    t = SshTransport(ssh="ssh", sftp="sftp")
    busy = _burst(t, "dev-a", ssh_mod.MAX_CONNECTIONS_PER_DEVICE)
    time.sleep(0.3)
    res = t.run("dev-a", "true", timeout=1.0)
    assert res.rc == RC_BUSY and res.busy and not res.unreachable
    for th in busy:
        th.join(10)


def test_transfers_have_their_own_slots_and_never_starve_commands(monkeypatch):
    # A long sftp per finished job filled the one shared cap; now transfers
    # queue on MAX_TRANSFERS_PER_DEVICE slots and commands keep theirs.
    _fresh(monkeypatch, hold=3.0)
    t = SshTransport(ssh="ssh", sftp="sftp")
    xfers = [threading.Thread(target=t._sftp_batch, args=("dev-a", ["get a b"], 30))
             for _ in range(ssh_mod.MAX_TRANSFERS_PER_DEVICE + 3)]
    for th in xfers:
        th.start()
    time.sleep(0.3)
    t0 = time.monotonic()
    res = t.run("dev-a", "true", timeout=10)
    assert res.ok and time.monotonic() - t0 < 5     # a command got a slot at once
    for th in xfers:
        th.join(30)


def test_a_stop_abandons_a_running_call_at_once_and_kills_its_child(monkeypatch):
    P = _fresh(monkeypatch, hold=30.0)
    t = SshTransport(ssh="ssh", sftp="sftp")
    stop = threading.Event()
    out = []
    th = threading.Thread(target=lambda: out.append(t.run("dev-a", "true", timeout=60, stop=stop)))
    th.start()
    time.sleep(0.3)
    t0 = time.monotonic()
    stop.set()
    th.join(5)
    assert not th.is_alive() and time.monotonic() - t0 < 2
    assert out[0].rc == RC_ABORTED and out[0].aborted and out[0].unreachable
    assert P.killed == 1 and P.live["dev-a"] == 0      # the child is gone


def test_a_stop_abandons_a_call_still_waiting_for_a_slot(monkeypatch):
    _fresh(monkeypatch, hold=30.0)
    t = SshTransport(ssh="ssh", sftp="sftp")
    holders = threading.Event()
    busy = _burst(t, "dev-a", ssh_mod.MAX_CONNECTIONS_PER_DEVICE, stop=holders)
    time.sleep(0.3)
    stop = threading.Event()
    out = []
    th = threading.Thread(target=lambda: out.append(t.run("dev-a", "true", timeout=60, stop=stop)))
    th.start()
    time.sleep(0.3)
    stop.set()
    th.join(5)
    assert not th.is_alive() and out[0].rc == RC_ABORTED
    holders.set()
    for b in busy:
        b.join(5)
