"""
Main application window: a QMainWindow that hosts a QWebEngineView and wires
it to the Python Bridge over a QWebChannel.

The entire UI is HTML/CSS/JS loaded from the bundled web/ directory; Python is
the backend. This is the same architecture as the original project, but with
paths resolved through orcamgr.paths so it works both in development and inside
a PyInstaller bundle.
"""

from __future__ import annotations

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QMainWindow, QFileDialog, QMessageBox
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel

from ..paths import web_dir, resource_path, config_file, default_workspace_root, APP_VERSION
from ..config import Settings
from .bridge import Bridge
from ..server.store import QueueStore
from ..server.controller import ServerController


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"ORCAdesk {APP_VERSION}")
        self.resize(1100, 820)

        icon_path = resource_path("resources", "orcadesk.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # On the very first launch (no config yet) ask where to keep calculation
        # files, so the user picks the location instead of a silent default.
        self._first_run_setup()

        self.view = QWebEngineView(self)
        self.setCentralWidget(self.view)

        # One shared queue, used by both the GUI and (optionally) the HTTP
        # server, so the desktop and the phone see the same calculations.
        self.store = QueueStore()
        self.server_ctl = ServerController(self.store)

        # Bridge owns all backend logic; register it on the channel.
        self.bridge = Bridge(self, self.store, self.server_ctl)
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)

        index = web_dir() / "index.html"
        self.view.load(QUrl.fromLocalFile(str(index)))

    def _first_run_setup(self):
        """If this is the first launch, let the user choose the workspace folder
        (where per-calculation folders and ORCA output are written)."""
        if config_file().exists():
            return  # already configured
        settings = Settings.load()  # fills sensible defaults
        default_dir = settings.workspace_root or str(default_workspace_root())
        QMessageBox.information(
            self, "Welcome to ORCAdesk",
            "Choose a folder where ORCAdesk will store your calculation files.\n"
            "Each calculation gets its own subfolder there.\n\n"
            "You can change this later in Settings.",
        )
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose calculation workspace folder", default_dir
        )
        if chosen:
            settings.workspace_root = chosen
        # persist (writes config_file so this dialog won't show again)
        settings.save()

    def closeEvent(self, event):
        # stop a running queue and the server before the window dies
        try:
            self.bridge.cancel_queue()
        except Exception:
            pass
        try:
            self.server_ctl.stop()
        except Exception:
            pass
        super().closeEvent(event)
