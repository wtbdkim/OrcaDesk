"""
Shared application state: the queue store and its serialization layer.

This package is the single source of truth consumed by BOTH the desktop
Bridge (orcamgr.gui) and the optional phone-sync HTTP server
(orcamgr.server) — it lives outside server/ so the dependency direction is
explicit: gui -> state <- server, never gui -> server.

Deliberately free of PyQt and FastAPI imports so it stays unit-testable in
isolation.
"""
