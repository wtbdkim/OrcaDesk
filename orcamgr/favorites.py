"""
Persisted "favorite" markers for the in-app 3D structure viewer.

A user browsing a large conformer set stars the ones worth following up; the
stars survive restarts. Storage is a small ``favorites.json`` in the writable
user-data dir — deliberately NOT in ``settings.json`` (which is rewritten on
every queue mutation) — mapping a *source key* to the list of favorited frame
labels under it. The source key is namespaced by the viewer's entry point:
``"calc:<name>"`` for a queued CREST search's ensemble, ``"folder:<path>"`` for
an arbitrary browsed folder — so two sources never share a star set.

Pure / file-only and Qt-free so it stays unit-testable; the Bridge slots wrap it.
"""

from __future__ import annotations

import json
import os

from .paths import user_data_root


def _fav_file():
    return user_data_root() / "favorites.json"


def load_all() -> dict:
    """The whole ``{source: [label, ...]}`` map, or ``{}`` when the file is
    absent/corrupt (degrade like Settings.load rather than crash the viewer)."""
    try:
        data = json.loads(_fav_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): [str(x) for x in v]
            for k, v in data.items() if isinstance(v, list)}


def get(source: str) -> list[str]:
    """Favorited labels for one source, in the order they were starred."""
    return load_all().get(source, [])


def toggle(source: str, label: str, on: bool) -> "tuple[list[str], bool]":
    """Star (``on=True``) or unstar (``on=False``) ``label`` under ``source``,
    persist, and return ``(labels, saved)``. Idempotent.

    ``saved`` is why this returns a pair: the write is best-effort (P32 — a
    failed save must never break the viewer), but the star lighting up IS the
    confirmation, and reporting success for a write that did not happen meant a
    user could star their way through a 100-conformer ensemble and find every
    star gone at the next launch."""
    data = load_all()
    labels = data.get(source, [])
    if on:
        if label not in labels:
            labels.append(label)
    else:
        labels = [x for x in labels if x != label]
    if labels:
        data[source] = labels
    else:
        data.pop(source, None)   # keep the file tidy — no empty source keys
    return labels, _save(data)


def _save(data: dict) -> bool:
    """Atomic write (tmp + os.replace, same pattern as Settings.save). Best
    effort: a write failure must never break the viewer — but it must be
    REPORTED, so the caller can stop claiming the star was kept. Returns
    whether it landed, and leaves no .tmp behind when it did not."""
    path = _fav_file()
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=0), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
