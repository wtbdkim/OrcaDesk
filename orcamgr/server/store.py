"""
DEPRECATED shim — the queue store moved to orcamgr.state.store.

QueueStore is shared by the desktop Bridge AND the HTTP server, so living
under server/ misrepresented the dependency direction (gui -> state <- server).
This module re-exports the public surface of orcamgr.state.store so existing
``orcamgr.server.store`` imports keep working, but emits a DeprecationWarning:
new code must import from ``orcamgr.state.store``.
"""

from __future__ import annotations

import warnings

from ..state import store as _store
from ..state.store import (  # noqa: F401
    EDITABLE_STATES,
    QueueStore,
    calc_from_dict,
    calc_from_session_dict,
    calc_to_dict,
    calc_to_session_dict,
    load_all_choices,
    load_choice_groups,
    make_engine_factory,
    reconcile_calcs,
)

warnings.warn(
    "orcamgr.server.store is deprecated; import from orcamgr.state.store instead.",
    DeprecationWarning,
    stacklevel=2,
)


def __getattr__(name: str):
    """Proxy anything not re-exported above (incl. private helpers) so no
    import of the old path can break silently."""
    return getattr(_store, name)
