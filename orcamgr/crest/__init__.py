"""
CREST (Conformer-Rotamer Ensemble Sampling Tool) backend for ORCAdesk.

This package is deliberately kept OUT of the ORCA pipeline in ``core/`` (the same
way ``orcamgr/mlip/`` is), because CREST is a separate external tool that
ORCAdesk shells out to — it never bundles or builds it. On Windows, CREST has no
native binary, so ORCAdesk runs it through **WSL**: a per-job ext4 scratch
directory holds the heavy I/O, results are copied back to the Windows workspace
folder, and the job is launched detached (``setsid``) so it survives ORCAdesk
closing, exactly like a detached ORCA run.

Modules:
* ``parser.py``   — read a finished run's ``crest_conformers.xyz`` +
                    ``crest.energies`` into the shared ``ParseResult`` (the
                    conformer ensemble, ranked, with per-conformer geometry).
* ``env.py``      — detect a usable WSL distro + the ``crest`` binary in it
                    (backs the "CREST ready" indicator).
* ``installer.py``— download the static CREST release binary into the WSL distro
                    (no user shell interaction needed).
* ``runner.py``   — launch/monitor/cancel a CREST run in WSL (WSL-aware detach
                    + process-group kill).

v1 scope: conformer search only (calc kind ``crest_conf``).
"""
