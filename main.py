"""
ORCAdesk - entry point.

Run in development:   python main.py
Frozen build:         ORCAdesk.exe  (see build.spec)
"""

import os
import sys

# Opt-in Chromium remote debugging, for diagnosing QtWebEngine memory/behavior.
# Set ORCADESK_REMOTE_DEBUG=9222 (any port > 1024) before launching, then open
# http://localhost:<port> in Chrome/Edge to attach DevTools (Memory tab → heap
# snapshots). Must be set before QtWebEngine initializes, so do it before any
# PyQt import. No effect unless the env var is set, so it's safe to leave in.
_dbg = os.environ.get("ORCADESK_REMOTE_DEBUG")
if _dbg:
    _port = _dbg if (_dbg.isdigit() and int(_dbg) > 1024) else "9222"
    os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", _port)

import signal

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from orcamgr.gui.window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ORCAdesk")
    window = MainWindow()
    window.show()

    # Let Ctrl-C / SIGTERM from a console launch (dev) shut the app down cleanly
    # via aboutToQuit -> MainWindow.shutdown, instead of killing the interpreter
    # and orphaning ORCA. A periodic no-op timer hands control back to Python so
    # the handler gets a chance to run under Qt's C++ event loop. (No effect in
    # the windowed packaged build, which has no console to Ctrl-C.)
    def _handle_signal(*_args):
        app.quit()

    try:
        signal.signal(signal.SIGINT, _handle_signal)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handle_signal)
    except (ValueError, OSError):
        pass  # not on the main thread, or unsupported — non-fatal
    _sig_timer = QTimer()
    _sig_timer.timeout.connect(lambda: None)
    _sig_timer.start(300)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
