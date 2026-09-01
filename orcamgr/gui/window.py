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
import json
import sys
import time

from PyQt6.QtCore import QUrl, QEvent, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QMainWindow, QFileDialog, QMessageBox, QApplication
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebChannel import QWebChannel

from ..paths import web_dir, resource_path, config_file, default_workspace_root, APP_VERSION
from ..config import Settings
from .bridge import Bridge
from ..state.store import QueueStore
from ..server.controller import ServerController


class _ConsoleCapturePage(QWebEnginePage):
    """QWebEnginePage that forwards JS console messages — especially errors —
    into the shared QueueStore log buffer, so front-end failures are visible
    in the Log tab of a deployed build. This is post-deployment diagnostics;
    ORCADESK_REMOTE_DEBUG (handled in main.py) stays the dev-time tool.

    Identical repeated messages are rate-limited: the same (level, source,
    line, text) fires at most once per _REPEAT_WINDOW seconds and carries a
    suppressed-repeat count, so a JS error inside the 1 s poll timer cannot
    flood the capped log buffer."""

    _REPEAT_WINDOW = 5.0   # seconds a signature stays muted after being logged
    _MAX_TRACKED = 256     # bound on the dedup map itself

    # console level -> (label shown in the line, store log level for styling)
    _LEVELS = {
        QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel: ("info", "info"),
        QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel: ("warn", "warn"),
        QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel: ("error", "err"),
    }

    def __init__(self, store: QueueStore, parent=None):
        super().__init__(parent)
        self._store = store
        # signature -> [suppressed_count, last_emit_monotonic]
        self._repeats: dict = {}

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        label, log_level = self._LEVELS.get(level, ("info", "info"))
        # full source is a long file:// URL; the basename is what's useful
        # (data: URLs from setHtml have no path — cap them instead)
        src = (source_id or "").replace("\\", "/").rsplit("/", 1)[-1] or "?"
        if len(src) > 60:
            src = src[:57] + "..."
        sig = (label, src, line_number, message)
        now = time.monotonic()
        entry = self._repeats.get(sig)
        if entry is not None and now - entry[1] < self._REPEAT_WINDOW:
            entry[0] += 1   # muted — just count it
            return
        if len(self._repeats) >= self._MAX_TRACKED:
            self._repeats.clear()
        suffix = ""
        if entry is not None and entry[0]:
            suffix = f" (repeated {entry[0]}x in the last {now - entry[1]:.0f}s)"
        self._repeats[sig] = [0, now]
        self._store.append_log(
            f"[web] level={label} line={line_number} source={src}: {message}{suffix}",
            log_level,
        )


class MainWindow(QMainWindow):
    # files that can be dropped onto the window -> routed by extension
    DROP_EXTS = (".inp", ".xyz", ".out")

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

        # Replace the default page with one that forwards JS console output
        # into the shared log buffer. Must happen before setWebChannel/load so
        # the channel lands on the page that actually serves the UI.
        self._page = _ConsoleCapturePage(self.store, self.view)
        self.view.setPage(self._page)

        # WebGL is ON: the Results-tab molecule viewer (3Dmol.js) renders
        # conformers/structures with it. The Liquid-Glass wallpaper canvas is
        # still 2D — WebGL is used only inside the viewer's own canvas. Chromium's
        # built-in PDF viewer stays off (the UI never opens PDFs).
        # (No cache/cookie tuning needed: the Qt 6 default profile is already
        # off-the-record — in-memory cache, no persistent cookies.)
        ws = self._page.settings()
        ws.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        ws.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, False)

        # Bridge owns all backend logic; register it on the channel.
        self.bridge = Bridge(self, self.store, self.server_ctl)
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)

        index = web_dir() / "index.html"
        self.view.load(QUrl.fromLocalFile(str(index)))

        # Drag-and-drop a .inp/.xyz onto Build, a .out onto Results. QtWebEngine's
        # real drop target is an internal child widget, so accepting drops on the
        # window isn't enough — we also install an event filter on the view's
        # focus proxy (the render widget) once it exists. See _install_drop_filter.
        self.setAcceptDrops(True)
        self._drop_child = None
        self.view.loadFinished.connect(self._install_drop_filter)
        # ...and check that it loaded at all. An absent or unreadable
        # web/index.html (a broken bundle, web/ quarantined by an antivirus)
        # otherwise left Chromium's own ERR_FILE_NOT_FOUND page on screen, in
        # the OS language, with nothing in the log and the backend running
        # happily behind it.
        self.view.loadFinished.connect(self._check_ui_loaded)

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

    def _check_ui_loaded(self, ok: bool) -> None:
        """Say so when the bundled UI could not be shown (see the connect)."""
        if ok:
            return
        index = web_dir() / "index.html"
        detail = ("it is missing" if not index.exists()
                  else "it could not be read")
        msg = (f"The application UI could not be loaded from {index} — "
               f"{detail}. The window will be blank; reinstall ORCAdesk, or "
               f"check whether an antivirus has quarantined its web folder.")
        try:
            self.store.append_log(msg, "err")
        except Exception:
            pass
        try:
            QMessageBox.critical(self, "ORCAdesk could not start", msg)
        except Exception:
            pass

    def _shutdown_note(self, msg: str) -> None:
        """Report a failed teardown step on a channel that exists.

        build.spec ships with `console=False`, so a windowed CPython has no
        standard handles at all: sys.stdout/sys.stderr are None and
        `print(..., file=None)` returns silently. Every one of these handlers
        was therefore a no-op in the deployed app — including the one on the
        final save_session, the write that records the detached ORCA's pid so
        the next launch can reattach instead of locking the job as failed. The
        store's log is persisted and visible in the Log tab; stderr still gets
        it when there IS a console (running from source).
        """
        try:
            self.store.append_log(f"[shutdown] {msg}", "err")
        except Exception:
            pass
        if sys.stderr is not None:
            try:
                print(f"[shutdown] {msg}", file=sys.stderr)
            except Exception:
                pass

    def shutdown(self):
        """Idempotent teardown. The in-flight ORCA is deliberately LEFT RUNNING
        so closing ORCAdesk doesn't stop a calculation: we stop the phone
        server (so no new run can start on the shared store mid-teardown),
        PAUSE the queue (stop monitoring, no kill), wait — bounded — for the
        worker to unwind, then persist the queue (incl. the running pid) so it
        can be reattached next launch. Safe to call multiple times and from
        any exit path (closeEvent, aboutToQuit, atexit). Errors are logged, not
        swallowed — this is the one moment cleanup matters.

        (Explicit Cancel / Stop-after-current from the UI still kill / drain as
        usual; only an app *close* leaves the job running.)"""
        if self._shutdown_done:
            return
        self._shutdown_done = True
        try:
            # stop the phone server FIRST: until it is down, /api/run on the
            # shared store could start a fresh run that pause_run no longer
            # monitors and wait_for_run no longer waits on
            self.server_ctl.stop()
        except Exception as e:
            self._shutdown_note("server stop failed: {e}")
        try:
            # An environment install is NOT like a calculation: there is nothing
            # to reattach to, its pip children are not detached, and what it
            # leaves behind is a half-built directory that refuses its own name
            # on the next launch. Downloading 2.5 GB into a folder nobody will
            # ever register is not a background job worth keeping.
            self.bridge.cancel_mlip_install()
        except Exception as e:
            self._shutdown_note("MLIP install cancel failed: {e}")
        try:
            self.store.pause_run()      # stop monitoring; do NOT kill ORCA
        except Exception as e:
            self._shutdown_note("pause failed: {e}")
        try:
            self.store.wait_for_run(timeout=10)
        except Exception as e:
            self._shutdown_note("wait_for_run failed: {e}")
        try:
            self.store.save_session()   # persist queue + running pid for reattach
        except Exception as e:
            self._shutdown_note("save_session failed: {e}")

    # ------------------------------------------------------------- drag & drop
    def _drop_path(self, mime):
        """First dropped local file with a handled extension, else None."""
        if mime is None or not mime.hasUrls():
            return None
        for url in mime.urls():
            p = url.toLocalFile()
            if p and p.lower().endswith(self.DROP_EXTS):
                return p
        return None

    def _dispatch_drop(self, path: str):
        """Route a dropped file to the right tab via a JS entrypoint."""
        ext = path.lower().rsplit(".", 1)[-1]
        fn = {"inp": "onInpDropped", "xyz": "onXyzDropped", "out": "onOutDropped"}.get(ext)
        if not fn:
            return
        # json.dumps -> a safely-escaped JS string literal (Windows backslashes)
        self.view.page().runJavaScript(f"window.{fn} && window.{fn}({json.dumps(path)})")

    def _install_drop_filter(self, _ok=False):
        """QtWebEngine hosts the page in an internal child widget that is the real
        drop target, so accepting drops on the window isn't enough. Accept drops on
        the view's focus proxy (the render widget) and filter its drag/drop events.
        The child is created lazily / can be recreated, so (re)install on each load."""
        child = self.view.focusProxy()
        if child is not None and child is not self._drop_child:
            child.setAcceptDrops(True)
            child.installEventFilter(self)
            self._drop_child = child

    def eventFilter(self, obj, event):
        et = event.type()
        if et in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if self._drop_path(event.mimeData()):
                event.acceptProposedAction()
                return True
        elif et == QEvent.Type.Drop:
            path = self._drop_path(event.mimeData())
            if path:
                self._dispatch_drop(path)
                event.acceptProposedAction()
                return True
        return super().eventFilter(obj, event)

    # window-level fallback, in case a drop reaches the window directly
    def dragEnterEvent(self, event):
        if self._drop_path(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        path = self._drop_path(event.mimeData())
        if path:
            self._dispatch_drop(path)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    # ------------------------------------------------- renderer lifecycle
    def changeEvent(self, event):
        # Minimized -> Frozen lets Chromium run its memory-pressure GC and drop
        # raster layers while the app sits in the taskbar during a long ORCA
        # run. Deferred so Chromium registers the visibility change first (a
        # visible page cannot be frozen); JS timers stop while frozen, which is
        # safe — the poll loop already skips hidden ticks and catches up from
        # its last log sequence number on resume.
        if event.type() == QEvent.Type.WindowStateChange and getattr(self, "_page", None):
            if self.isMinimized():
                QTimer.singleShot(500, self._freeze_if_minimized)
            else:
                self._page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
        super().changeEvent(event)

    def _freeze_if_minimized(self):
        # recommendedState stays Active when something must keep the page live
        # (e.g. attached devtools via ORCADESK_REMOTE_DEBUG) — respect that.
        if (
            self.isMinimized()
            and self._page.recommendedState() != QWebEnginePage.LifecycleState.Active
        ):
            self._page.setLifecycleState(QWebEnginePage.LifecycleState.Frozen)

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
