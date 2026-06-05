"""
ServerController — lets the desktop app start/stop the FastAPI server in a
background thread, sharing the SAME QueueStore the GUI uses (so the phone and
the desktop see one queue).

Stage 3: LAN only (binds 0.0.0.0 so other devices on the same Wi-Fi can reach
it). The Cloudflare tunnel + QR + token auth come in later stages.

uvicorn is driven via its programmatic Server API so we can stop it cleanly.
Requires fastapi + uvicorn (see requirements-server.txt). If they're missing,
ServerController stays "unavailable" and the desktop app still works normally.
"""

from __future__ import annotations

import socket
import threading
from typing import Optional

from .store import QueueStore

DEFAULT_PORT = 8000


def _local_ip() -> str:
    """Best-effort LAN IP of this machine (the address the phone connects to)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't actually send anything; just picks the outbound interface
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


class ServerController:
    def __init__(self, store: QueueStore, port: int = DEFAULT_PORT,
                 host: str = "0.0.0.0"):
        self.store = store
        self.port = port
        self.host = host
        self._server = None          # uvicorn.Server
        self._thread: Optional[threading.Thread] = None
        self._ip = "127.0.0.1"

    @staticmethod
    def is_available() -> bool:
        try:
            import fastapi  # noqa: F401
            import uvicorn  # noqa: F401
            return True
        except Exception:
            # any import-time failure (missing dep, logging config, etc.)
            return False

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def url(self) -> str:
        # the address a phone on the same Wi-Fi would open
        return f"http://{self._ip}:{self.port}"

    def start(self) -> None:
        if self.is_running():
            return
        if not self.is_available():
            raise RuntimeError(
                "fastapi/uvicorn are not installed. Run: pip install -r requirements-server.txt"
            )
        import uvicorn
        from .app import create_app

        self._ip = _local_ip()
        app = create_app(self.store)   # SHARE the GUI's store
        # log_config=None avoids uvicorn trying to load its default logging
        # dictConfig, which fails inside a PyInstaller bundle with
        # "Unable to configure formatter 'default'".
        config = uvicorn.Config(app, host=self.host, port=self.port,
                                log_config=None, log_level="warning")
        self._server = uvicorn.Server(config)
        # uvicorn normally installs signal handlers; disable since we're not in
        # the main thread.
        self._server.install_signal_handlers = False

        def _serve():
            self._server.run()

        self._thread = threading.Thread(target=_serve, name="orcadesk-server",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            # ask uvicorn to exit; the serving thread will wind down
            self._server.should_exit = True
        t = self._thread
        if t is not None and t.is_alive():
            # wait briefly so the port is released before any restart
            t.join(timeout=5.0)
        self._thread = None
        self._server = None
