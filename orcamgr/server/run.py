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
"""

from __future__ import annotations

HOST = "127.0.0.1"   # loopback only: the desktop app is what binds a LAN address
PORT = 8000


def main() -> None:
    try:
        import uvicorn
    except ImportError:
        raise SystemExit(
            "uvicorn is not installed. Run:  pip install fastapi uvicorn"
        )
    from .app import create_app

    app = create_app()
    print(f"ORCAdesk server (stage 1) on http://{HOST}:{PORT}")
    print(f"  health : http://{HOST}:{PORT}/api/health")
    print(f"  queue  : http://{HOST}:{PORT}/api/queue")
    print(f"  docs   : http://{HOST}:{PORT}/docs")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
