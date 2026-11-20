"""One ORCAdesk per user (orcamgr/gui/single_instance.py).

A second instance would restore the same session.json, reattach the same
running ORCA job and overwrite the first one's autosave, so the guarantee is
worth pinning: the lock refuses a second holder, a lock left by a crashed
instance does not lock the user out, and a later launch's "show" reaches the
running one. Qt (QtCore/QtNetwork only, no window); skipped without PyQt6.
"""
import subprocess
import sys
import time

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QCoreApplication  # noqa: E402

from orcamgr.gui.single_instance import (  # noqa: E402
    InstanceServer, acquire_lock, notify_running, server_name,
)


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QCoreApplication(sys.argv[:1])


def test_a_held_lock_refuses_a_second_instance(tmp_path):
    path = tmp_path / "instance.lock"
    first = acquire_lock(path)
    assert first is not None
    assert acquire_lock(path) is None
    first.unlock()
    again = acquire_lock(path)
    assert again is not None
    again.unlock()


def test_a_lock_left_by_a_crashed_instance_is_reclaimed(tmp_path):
    # the holder dies without unlocking, as a crash or a killed process would
    path = tmp_path / "instance.lock"
    code = (
        "import sys, os\n"
        "from orcamgr.gui.single_instance import acquire_lock\n"
        "from pathlib import Path\n"
        f"lock = acquire_lock(Path(r'{path}'))\n"
        "sys.stdout.write('held' if lock is not None else 'refused')\n"
        "sys.stdout.flush()\n"
        "os._exit(0)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=60)
    assert out.stdout == "held"
    assert path.exists(), "the crashed holder left its lock file behind"
    lock = acquire_lock(path)
    assert lock is not None, "a dead owner's lock must not lock the user out"
    lock.unlock()


def test_a_later_launch_asks_the_running_one_to_show(qapp):
    name = f"{server_name()}-test-{int(time.time() * 1000)}"
    shown = []
    server = InstanceServer(name, lambda: shown.append(True))
    assert server.listening
    assert notify_running(name)
    deadline = time.monotonic() + 5
    while not shown and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert shown == [True]


def test_nobody_listening_means_no_instance_answered(qapp):
    assert not notify_running(f"{server_name()}-nobody-{int(time.time() * 1000)}")


def test_the_name_is_per_user_and_pipe_safe():
    name = server_name()
    assert name.startswith("ORCAdesk-single-instance-")
    assert all(c.isalnum() or c in "_.-" for c in name)
