"""
Saved workflows: the queue editor's library of step chains.

A workflow is what the queue window copies (Ctrl+C on some blocks of one
row): per step its kind, its level (set name + text, or the Guided config),
its gate, and its upstream / geometry source inside the copy, at its place
relative to the copy -- without a structure. Copied, it lives in the editor
(and the UI state) only; SAVED, it is kept here by name so it can be put on
any row again: dragged onto a + slot, clicked with rows selected, or picked
from a slot's add menu.

Stored in ``user_data_root()/workflows.json`` as ``{schema, workflows:
[{id, name, v, from, steps}]}``, written tmp + ``os.replace`` like the inp
sets. A missing or unreadable file reads as no workflows (P32); an entry that
does not validate is dropped on read, the rest kept.

A step is replayed by the editor as an ``add_calc`` op, so the store's own
trust boundary (``calc_from_dict``) judges what it would run. What is checked
here is only what a replay needs to be possible at all: a list of step
objects, each with an id, a node and a known calculation kind, and bounded
sizes -- never the chemistry.

Qt-free, so it is tested directly.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Optional

from ..core.queue import KNOWN_KINDS
from ..paths import user_data_root

FILE_NAME = "workflows.json"
SCHEMA = 1

_MAX_NAME = 120
_MAX_ID = 64
_MAX_STEPS = 200
_MAX_BYTES = 500_000
_MAX_WORKFLOWS = 200
_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

_lock = threading.Lock()


def workflows_file(root: Optional[Path] = None) -> Path:
    return (root or user_data_root()) / FILE_NAME


def new_workflow_id() -> str:
    return "wf-" + secrets.token_hex(4)


def validate_workflow(d) -> dict:
    """The normalized ``{id, name, v, from, steps}`` of a client-sent
    workflow, or a ValueError saying what is wrong. A missing id is minted."""
    if not isinstance(d, dict):
        raise ValueError("A workflow must be an object.")
    wid = d.get("id") or new_workflow_id()
    if not isinstance(wid, str) or len(wid) > _MAX_ID or not _ID_RE.match(wid):
        raise ValueError("A workflow id is 1 to 64 letters, digits, '-', '_' or '.'.")
    name = d.get("name", "")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("A workflow needs a name.")
    name = name.strip()
    if len(name) > _MAX_NAME:
        raise ValueError(f"A workflow name is at most {_MAX_NAME} characters.")
    steps = d.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("A workflow has at least one step.")
    if len(steps) > _MAX_STEPS:
        raise ValueError(f"A workflow has at most {_MAX_STEPS} steps.")
    ids = set()
    for i, st in enumerate(steps, start=1):
        if not isinstance(st, dict):
            raise ValueError(f"Step {i} of the workflow is not an object.")
        sid, node, kind = st.get("id"), st.get("node"), st.get("kind")
        if not isinstance(sid, str) or not sid or sid in ids:
            raise ValueError(f"Step {i} of the workflow has no id of its own.")
        if not isinstance(node, str) or not node:
            raise ValueError(f"Step {i} of the workflow has no node.")
        if kind not in KNOWN_KINDS:
            raise ValueError(f"Step {i} of the workflow runs an unknown kind '{kind}'.")
        ids.add(sid)
    src = d.get("from", "")
    out = {"id": wid, "name": name, "v": 1,
           "from": src if isinstance(src, str) else "", "steps": steps}
    if len(json.dumps(out, ensure_ascii=False)) > _MAX_BYTES:
        raise ValueError("That workflow is too large to keep.")
    return out


def _read(path: Path) -> "list[dict]":
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("workflows") if isinstance(data, dict) else None
    out, ids = [], set()
    for d in entries if isinstance(entries, list) else []:
        try:
            w = validate_workflow(d)
        except ValueError:
            continue
        if w["id"] in ids:
            continue
        ids.add(w["id"])
        out.append(w)
    return out


def _write(path: Path, workflows: "list[dict]") -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps({"schema": SCHEMA, "workflows": workflows},
                                  ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def load_workflows(root: Optional[Path] = None) -> "list[dict]":
    """Every saved workflow, in the order they were first saved."""
    with _lock:
        return _read(workflows_file(root))


def save_workflow(d, root: Optional[Path] = None) -> dict:
    """Add a workflow, or replace the one with the same id; returns it as
    stored (with its id). ValueError when it does not validate or another
    workflow has its name (case aside), OSError when the file cannot be
    written."""
    w = validate_workflow(d)
    path = workflows_file(root)
    with _lock:
        workflows = _read(path)
        clash = next((o for o in workflows if o["id"] != w["id"]
                      and o["name"].casefold() == w["name"].casefold()), None)
        if clash is not None:
            raise ValueError(f"There is already a workflow named '{clash['name']}'.")
        for i, old in enumerate(workflows):
            if old["id"] == w["id"]:
                workflows[i] = w
                break
        else:
            if len(workflows) >= _MAX_WORKFLOWS:
                raise ValueError(f"At most {_MAX_WORKFLOWS} workflows can be kept.")
            workflows.append(w)
        _write(path, workflows)
    return w


def delete_workflow(workflow_id: str, root: Optional[Path] = None) -> bool:
    """Remove the workflow with this id. False when there is none."""
    path = workflows_file(root)
    with _lock:
        workflows = _read(path)
        kept = [w for w in workflows if w["id"] != workflow_id]
        if len(kept) == len(workflows):
            return False
        _write(path, kept)
    return True
