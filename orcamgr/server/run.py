"""
Standalone server entry point — for testing the API on your PC before it gets
embedded into the desktop app.

Usage (after `pip install fastapi uvicorn`):

    python -m orcamgr.server.run

Then open in a browser on the SAME PC:

    http://127.0.0.1:8000/api/health     -> should return JSON {"status":"ok",...}
    http://127.0.0.1:8000/api/queue       -> empty queue snapshot
    http://127.0.0.1:8000/docs            -> interactive API docs (FastAPI auto)

This entry point is for testing the API in isolation. It builds its OWN
QueueStore, so it does not share the desktop app's queue — run it INSTEAD of
ORCAdesk, never alongside it, or the two will write the same session file and
the same workspace folders. The API itself is complete: /api/run launches the
real QueueEngine, and PIN auth applies exactly as it does from the desktop
(loopback is exempt, which is why this binds to 127.0.0.1).

Behind a reverse proxy (nginx, cloudflared), set ORCADESK_PROXIED=1. A proxy
forwards from 127.0.0.1, so without it every forwarded request would wear a
loopback peer address and take the loopback exemption — i.e. the PIN would be
off for the whole internet. With it the exemption is off and uvicorn reads
X-Forwarded-For, so the access log names the real client instead of the proxy.
ORCADESK_TRUSTED_PROXIES (default 127.0.0.1) is the peer that header is
honoured from; widen it only if the proxy is on another host.
"""

from __future__ import annotations

import os

HOST = "127.0.0.1"   # loopback only: the desktop app is what binds a LAN address
PORT = 8000
# "", "0" and "false" are all off, so ORCADESK_PROXIED=0 reads as it looks
PROXIED = (os.environ.get("ORCADESK_PROXIED", "").strip().lower()
           not in ("", "0", "false", "no"))
TRUSTED_PROXIES = (os.environ.get("ORCADESK_TRUSTED_PROXIES", "").strip()
                   or "127.0.0.1")


def main() -> None:
    try:
        import uvicorn
    except ImportError:
        raise SystemExit(
            "uvicorn is not installed. Run:  pip install fastapi uvicorn"
        )
    from .app import create_app

    app = create_app(proxied=PROXIED)
    print(f"ORCAdesk server (stage 1) on http://{HOST}:{PORT}")
    print(f"  health : http://{HOST}:{PORT}/api/health")
    print(f"  queue  : http://{HOST}:{PORT}/api/queue")
    print(f"  docs   : http://{HOST}:{PORT}/docs")
    if PROXIED:
        print(f"  proxied: PIN required for all /api/; "
              f"X-Forwarded-For honoured from {TRUSTED_PROXIES}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info",
                proxy_headers=PROXIED, forwarded_allow_ips=TRUSTED_PROXIES)


if __name__ == "__main__":
    main()
