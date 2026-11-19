"""
The application windows: QMainWindows that host a QWebEngineView each and wire
it to the Python Bridge over a QWebChannel.

The entire UI is HTML/CSS/JS loaded from the bundled web/ directory; Python is
the backend. This is the same architecture as the original project, but with
paths resolved through orcamgr.paths so it works both in development and inside
a PyInstaller bundle.

Two windows, one backend: MainWindow (Jobs + Results, ``?win=main``) and
QueueWindow (the queue editor, ``?win=queue``) load the same page and publish
the SAME Bridge object — so one QueueStore — each on a channel of its own.
"""

from __future__ import annotations

import atexit
import json
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QByteArray, QUrl, QEvent, QTimer, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QMainWindow, QFileDialog, QMessageBox, QApplication
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebChannel import QWebChannel

from ..paths import web_dir, resource_path, config_file, default_workspace_root, APP_VERSION
from ..config import Settings
from .. import uistate
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

    def __init__(self, store: QueueStore, parent=None, tag: str = ""):
        super().__init__(parent)
        self._store = store
        # which window's page this is ("" = the main one): with two windows on
        # one log, a front-end error must say where it happened. The line keeps
        # its "[web] " prefix, which the log ring's retention keys on.
        self._tag = f"[{tag}] " if tag else ""
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
            f"[web] {self._tag}level={label} line={line_number} source={src}: "
            f"{message}{suffix}",
            log_level,
        )


def index_file() -> Path:
    """The front-end entry point: web/next/index.html, the notebook UI.

    It has its own markup, stylesheet and logic against the Bridge, and
    borrows from web/ only the two tracker/renderer scripts
    (../scf_graph.js, ../progress_panels.js) that have no DOM of their own,
    plus the bundled fonts and 3Dmol. Both windows load it; the query string
    says which one the page is (?win=main | ?win=queue) and the page lays
    itself out accordingly. If it is missing, MainWindow._check_ui_loaded says
    so instead of leaving a blank window.
    """
    return web_dir() / "next" / "index.html"


def page_url(win: str) -> QUrl:
    """index_file() as a file URL carrying ``?win=<win>``. setQuery on the URL
    fromLocalFile built, never a string concatenated onto a path: QUrl
    percent-encodes a path with spaces or non-ASCII characters, and a literal
    "?" in a folder name would otherwise read as the start of the query."""
    url = QUrl.fromLocalFile(str(index_file()))
    url.setQuery(f"win={win}")
    return url


def _make_web_view(owner, store: QueueStore, tag: str = ""):
    """A QWebEngineView with the console-capture page and the settings both
    windows share; returns (view, page). The page is set before the channel
    and the load, so the channel lands on the page that serves the UI.

    WebGL is ON: the pop-out viewer's isosurfaces (3Dmol.js, web/next/
    mvwin.js) render with it; every other picture is a 2D canvas or SVG.
    Chromium's built-in PDF viewer stays off (the UI never opens PDFs).
    (No cache/cookie tuning needed: the Qt 6 default profile is already
    off-the-record — in-memory cache, no persistent cookies.)"""
    view = QWebEngineView(owner)
    page = _ConsoleCapturePage(store, view, tag=tag)
    view.setPage(page)
    ws = page.settings()
    ws.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
    ws.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, False)
    return view, page


class _WebWindowMixin:
    """What both windows do around their web view: take dropped files and hand
    them to the page, and freeze the page while minimized. Mixed in BEFORE
    QMainWindow, so its event overrides win and reach Qt's through super().
    The host sets ``self.view`` / ``self._page`` and calls _init_drops()."""

    # files that can be dropped onto the window -> routed by extension
    DROP_EXTS = (".inp", ".xyz", ".out")
    # the page's entry point for one dropped file, by extension
    _DROP_FN = {"inp": "onInpDropped", "xyz": "onXyzDropped", "out": "onOutDropped"}

    def _init_drops(self) -> None:
        # QtWebEngine's real drop target is an internal child widget, so
        # accepting drops on the window isn't enough — we also install an
        # event filter on the view's focus proxy (the render widget) once it
        # exists. See _install_drop_filter.
        self.setAcceptDrops(True)
        self._drop_child = None
        self.view.loadFinished.connect(self._install_drop_filter)

    # ------------------------------------------------------------- drag & drop
    def _drop_paths(self, mime) -> "list[str]":
        """Every dropped local file with a handled extension, in drop order."""
        if mime is None or not mime.hasUrls():
            return []
        out = []
        for url in mime.urls():
            p = url.toLocalFile()
            if p and p.lower().endswith(self.DROP_EXTS):
                out.append(p)
        return out

    def _call_page(self, fn: str, arg) -> None:
        """``window.<fn>(arg)`` in the page, when the page defines it.
        json.dumps makes a safely-escaped JS literal of the argument (Windows
        backslashes; a list becomes an array)."""
        self.view.page().runJavaScript(f"window.{fn} && window.{fn}({json.dumps(arg)})")

    def _dispatch_single(self, path: str) -> None:
        """Route one dropped file to its page entry point by extension."""
        fn = self._DROP_FN.get(path.lower().rsplit(".", 1)[-1])
        if fn:
            self._call_page(fn, path)

    def _dispatch_drops(self, paths: "list[str]") -> None:
        """Every dropped .xyz goes to the page in ONE call,
        ``window.onXyzFilesDropped(paths)`` (a multi-file drop is one
        batch of new rows, not N); otherwise the first handled file takes
        the single-file route (onInpDropped / onOutDropped). Both windows."""
        xyz = [p for p in paths if p.lower().endswith(".xyz")]
        if xyz:
            self._call_page("onXyzFilesDropped", xyz)
            return
        if paths:
            self._dispatch_single(paths[0])

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
            if self._drop_paths(event.mimeData()):
                event.acceptProposedAction()
                return True
        elif et == QEvent.Type.Drop:
            paths = self._drop_paths(event.mimeData())
            if paths:
                self._dispatch_drops(paths)
                event.acceptProposedAction()
                return True
        return super().eventFilter(obj, event)

    # window-level fallback, in case a drop reaches the window directly
    def dragEnterEvent(self, event):
        if self._drop_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        paths = self._drop_paths(event.mimeData())
        if paths:
            self._dispatch_drops(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    # ------------------------------------------------- renderer lifecycle
    def changeEvent(self, event):
        # Minimized -> Frozen lets Chromium run its memory-pressure GC and drop
        # raster layers while the app sits in the taskbar during a long ORCA
        # run. Deferred so Chromium registers the visibility change first (a
        # visible page cannot be frozen); JS timers stop while frozen, which is
        # safe — on resume the poll reads the queue snapshot whole and the log
        # from its last sequence number. (A window hidden but not minimized is
        # not frozen, and keeps polling.)
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

    def _raise_window(self):
        """Show this window over everything. Un-minimizing keeps a maximized
        window maximized, and the state change wakes a frozen page
        (changeEvent)."""
        self.setWindowState(
            (self.windowState() & ~Qt.WindowState.WindowMinimized)
            | Qt.WindowState.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()
        if sys.platform == "win32":
            # activateWindow alone only flashes the taskbar button on Windows;
            # the launching process has granted us the foreground already.
            try:
                import ctypes
                ctypes.windll.user32.SetForegroundWindow(int(self.winId()))
            except Exception:
                pass


class QueueWindow(_WebWindowMixin, QMainWindow):
    """The queue editor's own window: a second QWebEngineView on the same page
    (web/next/index.html?win=queue), with its own QWebChannel registering the
    SAME Bridge object — one backend, one QueueStore, two windows.

    A top-level window of its own (no Qt parent), so it has its own taskbar
    entry and can sit on another screen. Closing it only hides it; closing the
    main window closes it for real (MainWindow.shutdown). Its geometry and
    whether it was open are kept across launches in ui_state.json (uistate).

    Dropped files take the same route as on the main window
    (_WebWindowMixin._dispatch_drops): every dropped .xyz in one
    ``window.onXyzFilesDropped(paths)`` call, a .inp / .out one at a time."""

    GEOMETRY_KEY = "window.queue.geometry"
    OPEN_KEY = "window.queue.open"
    _SIZE = (1500, 900)

    def __init__(self, store: QueueStore, bridge):
        super().__init__()
        self.setWindowTitle(f"ORCAdesk {APP_VERSION} — Queue")
        icon_path = resource_path("resources", "orcadesk.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self._store = store
        self._closing_for_real = False
        if not self._restore_geometry():
            self.resize(*self._SIZE)

        self.view, self._page = _make_web_view(self, store, tag="queue")
        self.setCentralWidget(self.view)
        # A channel of its own (a channel serves one page), publishing the
        # same Bridge object the main window's channel does.
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", bridge)
        self._page.setWebChannel(self.channel)
        self._init_drops()
        self.view.loadFinished.connect(self._check_loaded)
        self.view.load(page_url("queue"))

    def _check_loaded(self, ok: bool) -> None:
        # the main window has already said so in a dialog; a line is enough
        if not ok:
            try:
                self._store.append_log(
                    f"The queue window could not load {index_file()}.", "err")
            except Exception:
                pass

    # ---- geometry / visibility kept across launches ----
    def _restore_geometry(self) -> bool:
        try:
            saved = uistate.get(self.GEOMETRY_KEY)
            if isinstance(saved, str) and saved:
                return bool(self.restoreGeometry(
                    QByteArray.fromBase64(saved.encode("ascii"))))
        except Exception:
            pass
        return False

    def remember(self, open_: bool) -> None:
        """Keep this window's geometry and whether it is open. Best effort: a
        convenience is never worth an error (P32)."""
        try:
            uistate.put(self.GEOMETRY_KEY,
                        bytes(self.saveGeometry().toBase64()).decode("ascii"))
            uistate.put(self.OPEN_KEY, bool(open_))
        except Exception:
            pass

    def close_for_real(self) -> None:
        """Close (not hide): the main window is closing."""
        if self._closing_for_real:
            return
        self.remember(self.isVisible())
        self._closing_for_real = True
        self.close()

    def closeEvent(self, event):
        if self._closing_for_real:
            super().closeEvent(event)
            return
        # The user closed the queue window: hide it and keep the page (and the
        # editor's state) for the next "Open queue".
        self.remember(False)
        event.ignore()
        self.hide()


class MainWindow(_WebWindowMixin, QMainWindow):
    # The size the window opens at. This is not a preference: the notebook
    # layout is DRAWN at one width. design/notebook-reference.html and
    # tools/uispec.py both measure at 1680x1000, and app.css narrows the
    # queue rail below 1320 (@media max-width:1320px), so opening at 1100 put
    # it in a layout the design never shows -- a narrow rail, cells in one
    # column -- which reads as a different UI rather than as a narrow one.
    _SIZE = (1680, 1000)
    # ...but never larger than the screen it opens on. On a display too narrow
    # for the design (a 1366-wide laptop) the clamp does drop below 1320 and the
    # rail narrows -- that is the responsive layout doing its job, and the
    # floor only keeps the clamp from producing something absurd.
    _MIN_SIZE = (1100, 720)
    _SCREEN_MARGIN = 80

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"ORCAdesk {APP_VERSION}")
        self.resize(*self._initial_size())

        icon_path = resource_path("resources", "orcadesk.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # On the very first launch (no config yet) ask where to keep calculation
        # files, so the user picks the location instead of a silent default.
        self._first_run_setup()

        # One shared queue, used by both the GUI and (optionally) the HTTP
        # server, so the desktop and the phone see the same calculations.
        self.store = QueueStore()
        # Restore the previous session's queue (autosaved on every change) and
        # reconcile it with reality — a calc left RUNNING when ORCAdesk closed
        # keeps RUNNING if its detached ORCA is still alive, else is judged from
        # its .out. Done before the WebView loads so the queue is there to poll.
        self.store.load_session()
        self.server_ctl = ServerController(self.store)

        # The page forwards JS console output into the shared log buffer
        # (_ConsoleCapturePage); _make_web_view holds the shared settings.
        self.view, self._page = _make_web_view(self, self.store)
        self.setCentralWidget(self.view)

        # Bridge owns all backend logic; register it on the channel. The queue
        # window (created on the first "Open queue") registers this same object
        # on a channel of its own.
        self.bridge = Bridge(self, self.store, self.server_ctl)
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)
        self._queue_window: "QueueWindow | None" = None

        self.view.load(page_url("main"))

        # Drag-and-drop a .inp/.xyz onto Build, a .out onto Results.
        self._init_drops()
        # ...and check that it loaded at all. An absent or unreadable
        # web/next/index.html (a broken bundle, web/ quarantined by an antivirus)
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

        # Reopen the queue window when it was open at the last exit — once the
        # event loop runs, so it appears after (and over) the main window.
        try:
            reopen = uistate.get(QueueWindow.OPEN_KEY) is True
        except Exception:
            reopen = False
        if reopen:
            QTimer.singleShot(0, self.show_queue_window)

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

    def _initial_size(self) -> tuple[int, int]:
        """The window size the layout is drawn at (see _SIZE), clamped to the
        screen."""
        w, h = self._SIZE
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            w = min(w, max(self._MIN_SIZE[0], avail.width() - self._SCREEN_MARGIN))
            h = min(h, max(self._MIN_SIZE[1], avail.height() - self._SCREEN_MARGIN))
        return w, h

    @staticmethod
    def _index_file() -> Path:
        """See index_file(): both windows load it, each with its own ?win=."""
        return index_file()

    def _check_ui_loaded(self, ok: bool) -> None:
        """Say so when the bundled UI could not be shown (see the connect)."""
        if ok:
            return
        index = self._index_file()
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

    # ---------------------------------------------------------- queue window
    def show_queue_window(self) -> None:
        """Show (creating it on first use) and raise the queue window — what
        the Bridge's open_queue_window slot calls."""
        if getattr(self, "_shutdown_done", False):
            return
        if self._queue_window is None:
            self._queue_window = QueueWindow(self.store, self.bridge)
        self._queue_window._raise_window()
        self._queue_window.remember(True)

    def _close_queue_window(self) -> None:
        qw = self._queue_window
        if qw is None:
            return
        try:
            qw.close_for_real()
        except Exception as e:     # e.g. a C++ object already gone at exit
            self._shutdown_note(f"queue window close failed: {e}")

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
        # The queue window goes with the main one: left open it would keep the
        # application alive (Qt quits when the LAST window closes) with nothing
        # behind it but a store being torn down.
        self._close_queue_window()
        try:
            # stop the phone server FIRST: until it is down, /api/run on the
            # shared store could start a fresh run that pause_run no longer
            # monitors and wait_for_run no longer waits on
            self.server_ctl.stop()
        except Exception as e:
            self._shutdown_note(f"server stop failed: {e}")
        try:
            # An environment install is NOT like a calculation: there is nothing
            # to reattach to, its pip children are not detached, and what it
            # leaves behind is a half-built directory that refuses its own name
            # on the next launch. Downloading 2.5 GB into a folder nobody will
            # ever register is not a background job worth keeping.
            self.bridge.cancel_mlip_install()
        except Exception as e:
            self._shutdown_note(f"MLIP install cancel failed: {e}")
        try:
            self.store.pause_run()      # stop monitoring; do NOT kill ORCA
        except Exception as e:
            self._shutdown_note(f"pause failed: {e}")
        try:
            self.store.wait_for_run(timeout=10)
        except Exception as e:
            self._shutdown_note(f"wait_for_run failed: {e}")
        try:
            self.store.save_session()   # persist queue + running pid for reattach
        except Exception as e:
            self._shutdown_note(f"save_session failed: {e}")

    def bring_to_front(self):
        """Show this window over everything — what a second launch asks for
        (see single_instance.py)."""
        self._raise_window()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
