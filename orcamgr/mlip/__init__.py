"""
MLIP (Machine-Learned Interatomic Potential) support.

This package is intentionally kept SEPARATE from the ORCA pipeline in
``orcamgr/core/``: ORCAdesk shells out to the user's own MLIP Python
environment (PyTorch + mace-torch + ASE) the same way it shells out to the
ORCA executable, and never installs that toolchain itself. Keeping the MLIP
code in its own package makes the ORCA / MLIP split explicit at the module
level.

Contents: environment detection (:mod:`orcamgr.mlip.env`, backing the
"MLIP ready" status indicator) plus the run pipeline mirroring
``core/runner.py`` / ``core/parser.py`` — :mod:`orcamgr.mlip.runner` (writes
the run files and drives the user's interpreter on the MACE worker script)
and :mod:`orcamgr.mlip.parser` (reads the worker's JSON result into the
shared ``ParseResult``).
"""
