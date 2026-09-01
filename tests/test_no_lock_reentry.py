"""No slot calls another method that re-takes a lock it already holds.

The Bridge guards each background job with a plain ``threading.Lock``, and the
natural way to write a "busy" branch is to hand back the sibling status slot::

    with self._x_lock:
        if self._x["state"] == "running":
            return self.get_x_status()      # <-- takes self._x_lock again

That is a deadlock, and a nasty one: the lock is non-reentrant and the caller is
Qt's UI thread, so the whole window freezes — the user sees the app hang, not
one click fail. It also hides well. The branch only runs on a *second* click
during an already-running job, which no unit test reaches and no ordinary use
provokes, so it survives every green run. All three occurrences in this file
(cube generation, MLIP env creation, CREST install) were written that way; the
first was found only by driving the real UI.

This walks the AST for a ``self.foo()`` call made while a ``with self._x_lock``
is open, where ``foo`` itself opens ``self._x_lock``. The fix is always the
same: snapshot under the lock, serialize outside it.
"""
from __future__ import annotations

import ast
from pathlib import Path

GUI = Path(__file__).resolve().parent.parent / "orcamgr" / "gui"


def _lock_of(node: ast.With) -> str | None:
    """The ``self._x_lock`` a with-statement acquires, if any."""
    for item in node.items:
        expr = item.context_expr
        if isinstance(expr, ast.Attribute) and expr.attr.endswith("_lock"):
            return expr.attr
    return None


def _reentrant_calls(tree: ast.AST) -> list[tuple[int, str, str]]:
    methods = {n.name: n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    found: list[tuple[int, str, str]] = []

    class Walk(ast.NodeVisitor):
        def __init__(self) -> None:
            self.held: list[str] = []

        def visit_With(self, node: ast.With) -> None:
            self.held.append(_lock_of(node) or "")
            self.generic_visit(node)
            self.held.pop()

        visit_AsyncWith = visit_With        # type: ignore[assignment]

        def visit_Call(self, node: ast.Call) -> None:
            held = [lock for lock in self.held if lock]
            func = node.func
            if (held and isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name) and func.value.id == "self"):
                callee = methods.get(func.attr)
                if callee is not None:
                    for inner in ast.walk(callee):
                        if isinstance(inner, ast.With) and _lock_of(inner) in held:
                            found.append((node.lineno, func.attr, _lock_of(inner) or ""))
            self.generic_visit(node)

    Walk().visit(tree)
    return found


def test_no_self_call_retakes_a_held_lock():
    problems: list[str] = []
    for path in sorted(GUI.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, callee, lock in _reentrant_calls(tree):
            problems.append(f"{path.name}:{line} calls self.{callee}() "
                            f"while holding {lock}")
    assert not problems, (
        "deadlock: a method called under a lock re-takes it.\n  "
        + "\n  ".join(problems)
        + "\nSnapshot the state under the lock and serialize it outside.")


def test_the_detector_catches_the_shape_it_is_meant_to():
    """A guard this quiet is worth proving against a known-bad sample."""
    bad = ast.parse(
        "import threading\n"
        "class B:\n"
        "    def status(self):\n"
        "        with self._x_lock:\n"
        "            return 1\n"
        "    def start(self):\n"
        "        with self._x_lock:\n"
        "            return self.status()\n")
    hits = _reentrant_calls(bad)
    assert [(h[1], h[2]) for h in hits] == [("status", "_x_lock")]


def test_the_detector_does_not_flag_a_call_outside_the_lock():
    good = ast.parse(
        "class B:\n"
        "    def status(self):\n"
        "        with self._x_lock:\n"
        "            return 1\n"
        "    def start(self):\n"
        "        with self._x_lock:\n"
        "            self._busy = True\n"
        "        return self.status()\n")
    assert _reentrant_calls(good) == []


def test_the_detector_does_not_flag_a_different_lock():
    other = ast.parse(
        "class B:\n"
        "    def status(self):\n"
        "        with self._y_lock:\n"
        "            return 1\n"
        "    def start(self):\n"
        "        with self._x_lock:\n"
        "            return self.status()\n")
    assert _reentrant_calls(other) == []
