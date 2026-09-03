"""Static guard: every ``bridge.X`` the front-end calls is a real ``@pyqtSlot``.

QWebChannel exposes an object's **slots**, not its methods. A ``Bridge`` method
without the decorator is simply absent on the JS side, so the call is a
``TypeError`` on ``undefined`` — and the call sites are wrapped in ``try``
(they have to be: a bridge call is an IPC round-trip that can fail), which
swallows it. The feature then does nothing, silently, for as long as nobody
happens to try it.

That is not hypothetical: ``check_overwrite_conflicts`` shipped without its
decorator, so the "these calculations already have results — overwrite?"
screen never appeared before a run, from the initial commit until it was
found by auditing the slot surface.

This is a source guard rather than a behavioural test (P37, like
``test_no_undefined_names``): a behavioural one would need Qt, a QWebChannel
and a page, and would still only cover the slots it thought to call.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "orcamgr" / "gui" / "bridge.py"
WEB = ROOT / "web"

# `bridge` in the JS is the registered Bridge object; `bridge.py` in prose is not
_CALL = re.compile(r"\bbridge\.([A-Za-z_]\w*)\s*\(")


def _declared_slots() -> set[str]:
    """Names of Bridge methods carrying a ``@pyqtSlot`` decorator."""
    tree = ast.parse(BRIDGE.read_text(encoding="utf-8"))
    slots: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "Bridge":
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in item.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                name = getattr(target, "attr", None) or getattr(target, "id", None)
                if name == "pyqtSlot":
                    slots.add(item.name)
    return slots


def _called_from_web() -> dict[str, str]:
    """Every ``bridge.X(`` the desktop front-end calls, mapped to where."""
    found: dict[str, str] = {}
    for f in sorted(WEB.glob("*.js")):
        for name in _CALL.findall(f.read_text(encoding="utf-8")):
            found.setdefault(name, f.name)
    return found


def test_every_bridge_call_from_the_web_ui_is_a_slot():
    slots = _declared_slots()
    assert slots, "no @pyqtSlot methods found — the parser, not the Bridge, is wrong"
    missing = {n: where for n, where in _called_from_web().items() if n not in slots}
    assert not missing, (
        "called from the front-end but not exposed to QWebChannel "
        "(missing @pyqtSlot): " + ", ".join(f"{n} ({w})" for n, w in sorted(missing.items()))
    )


def test_every_slot_the_typings_declare_exists():
    """``web/globals.d.ts`` is the front-end's contract for the Bridge; a slot
    listed there but gone from Python is a call that type-checks and fails."""
    text = (WEB / "globals.d.ts").read_text(encoding="utf-8")
    # the Bridge interface only — the file also declares DOM/helper shapes
    block = re.search(r"interface\s+\w*Bridge\w*\s*\{(.*?)\n\}", text, re.S)
    assert block, "no Bridge interface in globals.d.ts"
    declared = set(re.findall(r"^\s*(\w+)\s*\(", block.group(1), re.M))
    slots = _declared_slots()
    missing = sorted(declared - slots)
    assert not missing, (
        "declared in web/globals.d.ts but not a @pyqtSlot on Bridge: " + ", ".join(missing)
    )
