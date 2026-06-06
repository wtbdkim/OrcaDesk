"""
FastAPI application exposing the shared QueueStore over HTTP.

This is intentionally a THIN layer over QueueStore (which is already unit
tested without FastAPI). Stage 1 endpoints:

    GET  /api/health           -> {"status": "ok", ...}
    GET  /api/queue            -> full queue snapshot
    POST /api/queue            -> add a calculation (JSON body)
    DELETE /api/queue/{name}   -> remove a calculation

Run it with orcamgr/server/run.py (uvicorn). Real ORCA execution, auth, QR,
the tunnel and websockets come in later stages.

NOTE: requires `fastapi` and `uvicorn` (and `pydantic`, pulled in by FastAPI).
Install with:  pip install fastapi uvicorn
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse

from .store import QueueStore, calc_from_dict, make_engine_factory, load_all_choices
from ..paths import APP_VERSION, web_mobile_dir
from ..config import Settings

# loopback peers (the desktop app's own requests). "localhost" is never a
# resolved socket peer (that is always an IP literal), so it is not listed.
_LOCAL_HOSTS = {"127.0.0.1", "::1"}

# paths that never require a token (serving the UI shell + token entry)
_OPEN_PATHS = {"/", "/manifest.webmanifest", "/api/ping"}


def create_app(store: QueueStore | None = None, bind_host: str = "127.0.0.1") -> FastAPI:
    """
    Build the FastAPI app around a QueueStore. If no store is passed, a fresh
    one is created (standalone server mode). When embedded in the PyQt app, the
    app will pass in the SAME store the GUI uses, so both see one queue.

    bind_host is the address uvicorn binds to. The loopback auth-bypass is only
    honoured when the bind is loopback-only; on a LAN bind (0.0.0.0 / a routable
    IP) the socket peer cannot be trusted (a same-host proxy/tunnel would make
    every request look like 127.0.0.1), so the PIN is required for ALL /api/.
    """
    store = store or QueueStore()
    app = FastAPI(title="ORCAdesk", version=APP_VERSION)
    # stash the store on the app so routes (and tests) can reach it
    app.state.store = store
    loopback_bind = bind_host in _LOCAL_HOSTS

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        path = request.url.path
        # always allow the UI shell, manifest, and the token-check endpoint
        if path in _OPEN_PATHS:
            return await call_next(request)
        # only guard the API; static/other paths fall through
        if path.startswith("/api/"):
            client_host = request.client.host if request.client else ""
            local_ok = loopback_bind and client_host in _LOCAL_HOSTS
            if not local_ok:
                supplied = request.headers.get("x-orcadesk-token") \
                    or request.query_params.get("token")
                if not store.check_token(supplied):
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Invalid or missing access PIN."},
                    )
        return await call_next(request)

    @app.get("/api/ping")
    def ping(token: str = "") -> dict:
        """Lightweight reachability + token check for the phone.
        Open (no middleware guard) so the phone can test a PIN; returns whether
        the supplied token is valid WITHOUT revealing the real one."""
        return {"ok": True, "authorized": store.check_token(token)}

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "app": "ORCAdesk",
            "version": APP_VERSION,
            "running": store.running,
            "queue_version": store.version(),
            "clients": store.active_clients(),
        }

    @app.post("/api/heartbeat")
    def heartbeat(payload: dict) -> dict:
        """Phone pings this periodically so the PC can show 'N phones connected'."""
        cid = str(payload.get("client_id", "")).strip()
        store.heartbeat(cid)
        return {"ok": True, "clients": store.active_clients()}

    @app.get("/api/queue")
    def get_queue() -> dict:
        return store.snapshot()

    @app.post("/api/queue")
    def add_calc(payload: dict) -> dict:
        try:
            calc = calc_from_dict(payload)
        except (KeyError, ValueError, TypeError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        try:
            store.add(calc)
        except ValueError as e:
            # duplicate name etc.
            raise HTTPException(status_code=409, detail=str(e))
        return {"ok": True, "snapshot": store.snapshot()}

    @app.delete("/api/queue/{name}")
    def remove_calc(name: str) -> dict:
        try:
            removed = store.remove(name)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        if not removed:
            raise HTTPException(status_code=404, detail=f"No calculation named '{name}'.")
        return {"ok": True, "snapshot": store.snapshot()}

    @app.post("/api/queue/reorder")
    def reorder_calc(payload: dict) -> dict:
        try:
            f = int(payload.get("from"))
            t = int(payload.get("to"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="from/to indices required.")
        try:
            store.reorder(f, t)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"ok": True, "snapshot": store.snapshot()}

    @app.post("/api/run")
    def run_queue() -> dict:
        # ORCA path / workspace come from the saved desktop settings
        settings = Settings.load()
        if not settings.orca_is_valid():
            raise HTTPException(
                status_code=400,
                detail="ORCA executable is not set. Configure it in the desktop app's Settings.",
            )
        factory = make_engine_factory(store, settings.orca_path, settings.workspace_root)
        try:
            store.start_run(factory)
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e))   # already running
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))   # empty queue
        return {"ok": True, "running": True}

    @app.post("/api/cancel")
    def cancel_queue() -> dict:
        ok = store.cancel_run()
        if not ok:
            raise HTTPException(status_code=409, detail="No run is in progress.")
        return {"ok": True}

    @app.get("/api/log")
    def get_log(since: int = 0) -> dict:
        return store.log_since(since)

    @app.get("/api/choices")
    def get_choices() -> dict:
        """Flat option lists for the mobile form's dropdowns (from data/*.json),
        plus a few desktop settings the phone needs (e.g. the opt-ETA mode)."""
        data = load_all_choices()
        try:
            data["_settings"] = {"eta_mode": Settings.load().eta_mode}
        except Exception:
            data["_settings"] = {"eta_mode": "conservative"}
        return data

    # --- serve the mobile PWA at the site root ---
    # Phone opens http://<server>:8000/ and gets this page; its fetch() calls
    # use relative paths (/api/...) so they hit this same server automatically.
    @app.get("/")
    def mobile_index():
        index = web_mobile_dir() / "index.html"
        if not index.exists():
            raise HTTPException(status_code=404, detail="Mobile UI not found.")
        return FileResponse(str(index), media_type="text/html")

    @app.get("/manifest.webmanifest")
    def manifest():
        m = web_mobile_dir() / "manifest.webmanifest"
        if m.exists():
            return FileResponse(str(m), media_type="application/manifest+json")
        raise HTTPException(status_code=404, detail="No manifest.")

    @app.get("/scf_graph.js")
    def scf_graph_js():
        # shared SCF graph module lives in web/ ; mobile loads it from here
        from ..paths import web_dir
        f = web_dir() / "scf_graph.js"
        if f.exists():
            return FileResponse(str(f), media_type="application/javascript")
        raise HTTPException(status_code=404, detail="scf_graph.js not found.")

    return app
