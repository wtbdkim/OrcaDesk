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

from PyQt6.QtWidgets import QApplication

from orcamgr.gui.window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ORCAdesk")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
