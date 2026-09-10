"""
ServerController — lets the desktop app start/stop the FastAPI server in a
background thread, sharing the SAME QueueStore the GUI uses (so the phone and
the desktop see one queue).

LAN only: it binds 0.0.0.0 so other devices on the same Wi-Fi can reach it,
and access is gated by the per-launch PIN (P35 — the loopback exemption applies
only when the bind really is loopback and nothing is forwarding to it). A QR
code for the URL is available from the desktop; an outbound tunnel is not built,
but `proxied=True` is the switch a future one (or an nginx in front) flips.

uvicorn is driven via its programmatic Server API so we can stop it cleanly.
Requires fastapi + uvicorn (see requirements-server.txt). If they're missing,
ServerController stays "unavailable" and the desktop app still works normally.
"""

from __future__ import annotations

import socket
import threading
from typing import Optional

from ..state.store import QueueStore

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
                 host: str = "0.0.0.0", *, proxied: bool = False,
                 trusted_proxies: str = "127.0.0.1"):
        self.store = store
        self.port = port
        self.host = host
        # proxied: a reverse proxy / tunnel (nginx, cloudflared) forwards to us.
        # It decides TWO things that must agree, which is why it is one flag:
        # the PIN bypass is off (create_app), and uvicorn is allowed to read
        # X-Forwarded-For so request.client.host is the real client rather than
        # the proxy. trusted_proxies is the peer address that header is honoured
        # from -- passed explicitly so the value never comes from uvicorn's
        # FORWARDED_ALLOW_IPS environment variable, which ORCAdesk does not own.
        self.proxied = proxied
        self.trusted_proxies = trusted_proxies
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
        # pass the bind host so the app only honours the loopback auth-bypass when
        # actually bound to loopback (a LAN bind requires the PIN for all /api/),
        # and the proxy flag so a forwarded deployment does not honour it either
        app = create_app(self.store, bind_host=self.host,   # SHARE the GUI's store
                         proxied=self.proxied)
        # log_config=None avoids uvicorn trying to load its default logging
        # dictConfig, which fails inside a PyInstaller bundle with
        # "Unable to configure formatter 'default'".
        #
        # proxy_headers is uvicorn's default-ON, and it REPLACES
        # request.client.host with an X-Forwarded-For element. Off unless a proxy
        # was declared: the real socket peer is the only address the kernel
        # vouches for, and it is what the P35 bypass reads. On when one was --
        # there the bypass is already off, so the header informs and decides
        # nothing.
        config = uvicorn.Config(app, host=self.host, port=self.port,
                                log_config=None, log_level="warning",
                                proxy_headers=self.proxied,
                                forwarded_allow_ips=self.trusted_proxies)
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
            if t.is_alive():
                # uvicorn didn't honour should_exit in time: keep the refs so
                # is_running() stays True and start() refuses to spawn a second
                # server thread over the zombie one (which still holds the
                # socket) — dropping them here would leak the thread untracked.
                return
        self._thread = None
        self._server = None
