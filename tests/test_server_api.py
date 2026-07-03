"""
Tests for the phone-sync HTTP API (orcamgr/server/app.py) via FastAPI's
TestClient over an isolated QueueStore.

Contract references (PRINCIPLES.md):
  P4  — the run entry points share one decision (queue_needs_orca): an
        all-MLIP queue must start without an ORCA path configured.
  P33 — the HTTP path goes through the SAME calc_from_dict choke point as the
        desktop, so path-dangerous names are rejected server-side.
  P35 — the loopback auth bypass is honoured only on a loopback bind; on a
        LAN bind every /api/ request needs the PIN, even from 127.0.0.1.

fastapi/uvicorn are optional dependencies (P17), so the whole module skips
when the test client stack is unavailable. Everything is isolated: the store's
session autosave and Settings both land in tmp_path, never in %APPDATA%.
"""

from __future__ import annotations

import sys

import pytest

try:
    from fastapi.testclient import TestClient
except Exception:  # ImportError (no fastapi) or RuntimeError (no httpx)
    pytest.skip("fastapi test client not available", allow_module_level=True)

import orcamgr.server.app as server_app_mod
import orcamgr.state.store as store_mod
from orcamgr.config import Settings
from orcamgr.paths import APP_VERSION
from orcamgr.server.app import create_app
from orcamgr.state.store import QueueStore, calc_from_dict


@pytest.fixture
def store(tmp_path, monkeypatch) -> QueueStore:
    """An isolated QueueStore whose session autosave goes to tmp_path
    (never the real %APPDATA%)."""
    monkeypatch.setattr(store_mod, "user_data_root", lambda: tmp_path)
    return QueueStore()


def _client(app, host: str = "127.0.0.1") -> TestClient:
    """A test client whose socket peer address the middleware will see."""
    return TestClient(app, client=(host, 50000))


def _calc_payload(name: str, kind: str = "sp") -> dict:
    return {
        "name": name,
        "kind": kind,
        "config": {"kind": kind},
        "charge": 0,
        "multiplicity": 1,
        "geometry_source": "direct",
        "xyz": "H 0.0 0.0 0.0",
    }


def _stub_settings(monkeypatch, tmp_path, mlip_envs=None) -> None:
    """Make Settings.load() return a crafted instance: no ORCA configured,
    workspace under tmp_path — without touching the real settings.json."""
    stub = Settings(orca_path="",
                    workspace_root=str(tmp_path / "ws"),
                    mlip_envs=list(mlip_envs or []))
    monkeypatch.setattr(server_app_mod.Settings, "load",
                        classmethod(lambda cls: stub))


# ---------------------------------------------------------------------------
# health / version
# ---------------------------------------------------------------------------

def test_health_reports_single_sourced_app_version(store):
    client = _client(create_app(store, bind_host="127.0.0.1"))
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] == APP_VERSION
    assert body["running"] is False
    assert isinstance(body["queue_version"], int)


# ---------------------------------------------------------------------------
# auth: loopback bypass vs LAN PIN (P35)
# ---------------------------------------------------------------------------

def test_loopback_bind_allows_untokened_local_requests(store):
    client = _client(create_app(store, bind_host="127.0.0.1"), host="127.0.0.1")
    r = client.get("/api/queue")
    assert r.status_code == 200
    assert r.json()["calculations"] == []


def test_loopback_bind_still_requires_pin_for_nonlocal_peer(store):
    # defense in depth: even on a loopback bind, a non-loopback peer address
    # gets no bypass
    client = _client(create_app(store, bind_host="127.0.0.1"), host="192.168.0.55")
    assert client.get("/api/queue").status_code == 401
    ok = client.get("/api/queue", headers={"x-orcadesk-token": store.token})
    assert ok.status_code == 200


def test_lan_bind_requires_pin_even_from_localhost_peer(store):
    # on a LAN bind the peer address is spoofable (proxy/tunnel), so the
    # loopback bypass must be off for ALL /api/ requests (P35)
    client = _client(create_app(store, bind_host="0.0.0.0"), host="127.0.0.1")
    assert client.get("/api/queue").status_code == 401
    # a wrong token (chosen so it can never equal the random 6-digit PIN)
    assert client.get("/api/queue",
                      headers={"x-orcadesk-token": "not-the-pin"}).status_code == 401
    ok = client.get("/api/queue", headers={"x-orcadesk-token": store.token})
    assert ok.status_code == 200


def test_ping_validates_pin_without_revealing_it(store):
    client = _client(create_app(store, bind_host="0.0.0.0"), host="10.0.0.7")
    # /api/ping is open (so the phone can test a PIN before authenticating)
    r_bad = client.get("/api/ping", params={"token": "999999x"})
    assert r_bad.status_code == 200
    assert r_bad.json() == {"ok": True, "authorized": False}
    r_ok = client.get("/api/ping", params={"token": store.token})
    assert r_ok.status_code == 200
    assert r_ok.json()["authorized"] is True
    # the real PIN never appears in either response body
    assert store.token not in r_bad.text
    assert store.token not in r_ok.text


# ---------------------------------------------------------------------------
# POST /api/queue — same calc_from_dict gate as the desktop (P33)
# ---------------------------------------------------------------------------

def test_add_calc_via_api_lands_in_shared_store(store):
    client = _client(create_app(store, bind_host="127.0.0.1"))
    r = client.post("/api/queue", json=_calc_payload("job1"))
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert [c["name"] for c in body["snapshot"]["calculations"]] == ["job1"]
    # the HTTP layer wrote into the SAME store object (one shared queue)
    assert store.get("job1") is not None


def test_duplicate_calc_name_is_conflict_409(store):
    client = _client(create_app(store, bind_host="127.0.0.1"))
    assert client.post("/api/queue", json=_calc_payload("dup")).status_code == 200
    r = client.post("/api/queue", json=_calc_payload("dup"))
    assert r.status_code == 409
    assert "dup" in r.json()["detail"]


@pytest.mark.parametrize("bad_name", [
    "../evil",        # path traversal
    "a/b",            # separator
    "a\\b",           # separator (Windows)
    'quo"te',         # forbidden filename char
    "trailingdot.",   # Windows silently strips a trailing dot
    "con",            # Windows reserved device name
    "nul.txt",        # reserved base name with extension
    "",               # empty name
])
def test_path_dangerous_calc_name_rejected_with_400(store, bad_name):
    client = _client(create_app(store, bind_host="127.0.0.1"))
    r = client.post("/api/queue", json=_calc_payload(bad_name))
    assert r.status_code == 400
    assert store.names() == []  # nothing slipped into the queue


def test_remove_missing_calc_is_404(store):
    client = _client(create_app(store, bind_host="127.0.0.1"))
    assert client.delete("/api/queue/ghost").status_code == 404


# ---------------------------------------------------------------------------
# POST /api/run — the queue_needs_orca gate (P4)
# ---------------------------------------------------------------------------

def test_all_mlip_queue_starts_without_orca_configured(store, tmp_path, monkeypatch):
    _stub_settings(monkeypatch, tmp_path,
                   mlip_envs=[{"id": "e1", "name": "stub", "python": sys.executable}])
    payload = _calc_payload("mlip_job", kind="mlip_opt")
    payload["config"] = {"kind": "mlip_opt", "mlip_model": "MACE-OFF medium"}
    store.add(calc_from_dict(payload))

    # never spawn the real run thread — just record that a start was requested
    started = []
    monkeypatch.setattr(store, "start_run", lambda factory: started.append(factory))

    client = _client(create_app(store, bind_host="127.0.0.1"))
    r = client.post("/api/run")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "running": True}
    assert len(started) == 1


def test_orca_queue_without_orca_path_is_rejected_400(store, tmp_path, monkeypatch):
    _stub_settings(monkeypatch, tmp_path)  # orca_path == "" -> invalid
    store.add(calc_from_dict(_calc_payload("orca_job", kind="sp")))

    started = []
    monkeypatch.setattr(store, "start_run", lambda factory: started.append(factory))

    client = _client(create_app(store, bind_host="127.0.0.1"))
    r = client.post("/api/run")
    assert r.status_code == 400
    assert "ORCA" in r.json()["detail"]
    assert started == []  # the gate fired before any run started


def test_run_with_empty_queue_is_rejected(store, tmp_path, monkeypatch):
    _stub_settings(monkeypatch, tmp_path)
    client = _client(create_app(store, bind_host="127.0.0.1"))
    r = client.post("/api/run")
    # empty queue -> queue_needs_orca is False, so start_run raises ValueError
    # which the endpoint converts to a 400 (P16: domain raises, boundary converts)
    assert r.status_code == 400
    assert store.running is False
