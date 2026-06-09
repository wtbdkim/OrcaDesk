"""
Main application window: a QMainWindow that hosts a QWebEngineView and wires
it to the Python Bridge over a QWebChannel.

The entire UI is HTML/CSS/JS loaded from the bundled web/ directory; Python is
the backend. This is the same architecture as the original project, but with
paths resolved through orcamgr.paths so it works both in development and inside
a PyInstaller bundle.
"""

from __future__ import annotations

import atexit
import sys

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QMainWindow, QFileDialog, QMessageBox, QApplication
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
        # Restore the previous session's queue (autosaved on every change) and
        # reconcile it with reality — a calc left RUNNING when ORCAdesk closed
        # keeps RUNNING if its detached ORCA is still alive, else is judged from
        # its .out. Done before the WebView loads so the queue is there to poll.
        self.store.load_session()
        self.server_ctl = ServerController(self.store)

        # Bridge owns all backend logic; register it on the channel.
        self.bridge = Bridge(self, self.store, self.server_ctl)
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)

        index = web_dir() / "index.html"
        self.view.load(QUrl.fromLocalFile(str(index)))

        # If a calculation from the previous session is still running, reattach
        # and continue the queue from where it left off.
        self.bridge.resume_session_if_running()

        # Cleanup must run no matter how the app exits, not only on a window
        # close. aboutToQuit covers QApplication.quit() (e.g. a Ctrl-C handler
        # in main()); atexit is the interpreter-exit backstop. shutdown() is
        # idempotent, so being reached by several of these paths is harmless.
        self._shutdown_done = False
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)
        atexit.register(self.shutdown)

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

    def shutdown(self):
        """Idempotent teardown. The in-flight ORCA is deliberately LEFT RUNNING
        so closing ORCAdesk doesn't stop a calculation: we only PAUSE the queue
        (stop monitoring, no kill), wait — bounded — for the worker to unwind,
        persist the queue (incl. the running pid) so it can be reattached next
        launch, then stop the phone server. Safe to call multiple times and from
        any exit path (closeEvent, aboutToQuit, atexit). Errors are logged, not
        swallowed — this is the one moment cleanup matters.

        (Explicit Cancel / Stop-after-current from the UI still kill / drain as
        usual; only an app *close* leaves the job running.)"""
        if self._shutdown_done:
            return
        self._shutdown_done = True
        try:
            self.store.pause_run()      # stop monitoring; do NOT kill ORCA
        except Exception as e:
            print(f"[shutdown] pause failed: {e}", file=sys.stderr)
        try:
            self.store.wait_for_run(timeout=10)
        except Exception as e:
            print(f"[shutdown] wait_for_run failed: {e}", file=sys.stderr)
        try:
            self.store.save_session()   # persist queue + running pid for reattach
        except Exception as e:
            print(f"[shutdown] save_session failed: {e}", file=sys.stderr)
        try:
            self.server_ctl.stop()
        except Exception as e:
            print(f"[shutdown] server stop failed: {e}", file=sys.stderr)

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
