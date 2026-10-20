"""One ORCAdesk per user: a second launch shows the window already open.

Two instances are not two views of one app. Each owns a QueueStore restored from
the same session.json, so each would reattach the same running ORCA job, walk
the same queue, and overwrite the other's autosave on every mutation — a
calculation could be launched twice into one folder. So the second launch asks
the first to come forward and exits before it builds a window.

Two mechanisms, because each answers only one of the two questions:

* **Is one running?** — a QLockFile in the user data root. A QLocalServer cannot
  answer this: on Windows a second ``listen()`` on the same pipe name succeeds
  (measured), so two instances started together would both come up. The lock
  records its owner's PID, so one left behind by a crash is reclaimed on the
  next launch rather than locking the user out.
* **Show yourself** — a QLocalServer (a named pipe on Windows, a socket file
  elsewhere) named per user, which the second launch connects to.
"""

from __future__ import annotations

import getpass
import re
import sys
import time
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QLockFile, QObject
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

_PREFIX = "ORCAdesk-single-instance"
_TIMEOUT_MS = 1000


def server_name() -> str:
    """The rendezvous name for this user (pipe/socket-name safe)."""
    try:
        user = getpass.getuser()
    except Exception:          # no login name in the environment: still one per machine
        user = "user"
    return f"{_PREFIX}-{re.sub(r'[^A-Za-z0-9_.-]', '_', user)}"


def acquire_lock(path: Path) -> QLockFile | None:
    """Take the one-instance lock, or None when a live instance holds it.

    The returned lock must stay referenced for the life of the app: it is
    released when the object is destroyed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(path))
    lock.setStaleLockTime(0)     # stale only when its owner process is gone, never by age
    return lock if lock.tryLock(0) else None


def notify_running(name: str) -> bool:
    """Ask a running instance to show its window. True if one answered."""
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(_TIMEOUT_MS):
        return False
    if sys.platform == "win32":
        # Windows only lets the foreground process hand the foreground to
        # another one; this launch is it (the user just started it), so it
        # grants the running instance the right to take the focus.
        try:
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)   # ASFW_ANY
        except Exception:
            pass
    sock.write(b"show\n")
    sock.flush()
    sock.waitForBytesWritten(_TIMEOUT_MS)
    sock.disconnectFromServer()
    return True


def wait_and_notify(name: str, seconds: float = 10.0) -> bool:
    """notify_running, retried while the instance holding the lock is still
    starting up (it opens its server right after taking the lock, but a slow
    disk or a first-run dialog can stretch that)."""
    deadline = time.monotonic() + seconds
    while True:
        if notify_running(name):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


class InstanceServer(QObject):
    """Listens for later launches and calls ``on_activate`` for each."""

    def __init__(self, name: str, on_activate: Callable[[], None], parent=None):
        super().__init__(parent)
        self._on_activate = on_activate
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._accept)
        # A socket file left by a crashed instance (non-Windows) would make
        # listen() fail. Safe to clear: we hold the lock, so nobody else is
        # serving this name.
        QLocalServer.removeServer(name)
        self.listening = self._server.listen(name)

    def _accept(self):
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            sock.disconnected.connect(sock.deleteLater)
            self._on_activate()
