"""
MLIP (Machine-Learned Interatomic Potential) support.

This package is intentionally kept SEPARATE from the ORCA pipeline in
``orcamgr/core/``: ORCAdesk shells out to the user's own MLIP Python
environment (PyTorch + mace-torch + ASE) the same way it shells out to the
ORCA executable, and never installs that toolchain itself. Keeping the MLIP
code in its own package makes the ORCA / MLIP split explicit at the module
level.

Currently this holds only environment detection (:mod:`orcamgr.mlip.env`),
which backs the "MLIP ready" status indicator. The run pipeline (a dedicated
runner/parser mirroring ``core/runner.py`` and ``core/parser.py``) is a later
addition.
"""
