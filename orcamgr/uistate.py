"""Small front-end state that must outlive the app: what a window had open.

The web front-ends cannot keep it themselves. The Qt 6 default WebEngine
profile is off-the-record, so ``localStorage`` is gone on the next launch.
This is a flat ``{key: JSON value}`` file under ``user_data_root()``, written
the same tmp + ``os.replace`` way as the settings file. It holds conveniences
(the rail's opened files, an unsent New-calculation draft), never anything the
queue depends on: a missing or unreadable file reads as empty, and a failed
write is reported back, not raised (P32).

Qt-free, so it is tested directly.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

from .paths import user_data_root

FILE_NAME = "ui_state.json"
# A key names a front-end feature ("rail.opened", "build.draft"); a value is
# what one window had open. Both are bounded so a runaway caller cannot grow
# the file without limit.
MAX_KEY = 64
MAX_VALUE_BYTES = 1_000_000

_lock = threading.Lock()


def state_file(root: Optional[Path] = None) -> Path:
    return (root or user_data_root()) / FILE_NAME


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _check_key(key: str) -> None:
    if not isinstance(key, str) or not key or len(key) > MAX_KEY:
        raise ValueError(f"a UI-state key is 1 to {MAX_KEY} characters")


def get(key: str, root: Optional[Path] = None) -> Any:
    """The value stored under ``key``, or None."""
    _check_key(key)
    with _lock:
        return _read(state_file(root)).get(key)


def put(key: str, value: Any, root: Optional[Path] = None) -> None:
    """Store ``value`` under ``key``; None removes the key.

    Raises ValueError for a bad key or an oversized value, OSError when the
    file cannot be written.
    """
    _check_key(key)
    if value is not None and len(json.dumps(value)) > MAX_VALUE_BYTES:
        raise ValueError("that value is too large to keep")
    path = state_file(root)
    with _lock:
        data = _read(path)
        if value is None:
            if key not in data:
                return
            data.pop(key)
        else:
            data[key] = value
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
