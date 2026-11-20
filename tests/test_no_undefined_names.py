"""No module references a name that does not exist.

There is no linter in this project, so a name that only *some* code path reads —
a helper deleted in a refactor, an import that was never added — survives every
green test run and then raises NameError in front of the user. That is exactly
how CREST shipped broken: a deduplication moved the tail loop to
``core.tailer.FileTailer`` but left ``crest/runner.py`` without the import, and
the two remaining references to the deleted local helper, in a branch no test
reaches. The fake-runner tests never execute the real runner, so nothing failed.

This walks every module's AST and flags a Load of a name that is bound nowhere
in the file and is not a builtin. It is deliberately permissive about scope
(module-level knowledge of every binding in the file, not per-function), so it
cannot produce false positives from ordinary shadowing — it only catches a name
that appears nowhere at all.
"""
from __future__ import annotations

import ast
import builtins
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "orcamgr"


def _bound_names(tree: ast.AST) -> set[str]:
    """Every name bound anywhere in the module: imports, defs, classes,
    assignments, arguments, except-handlers, comprehension targets, globals."""
    names = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__",
                                  "__spec__", "__loader__", "__builtins__"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.comprehension):
            for target in ast.walk(node.target):
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
    return names


def test_every_module_only_reads_names_that_exist():
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        bound = _bound_names(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id not in bound:
                    rel = path.relative_to(PACKAGE.parent)
                    offenders.append(f"{rel}:{node.lineno}: {node.id}")
    assert not offenders, "names read but never bound:\n  " + "\n  ".join(offenders)
