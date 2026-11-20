"""
CREST (Conformer-Rotamer Ensemble Sampling Tool) backend for ORCAdesk.

This package is deliberately kept OUT of the ORCA pipeline in ``core/`` (the same
way ``orcamgr/mlip/`` is), because CREST is a separate external tool that
ORCAdesk shells out to — it never bundles or builds it.

**Where it shells out to depends on the machine, and is detected, not
configured.** CREST publishes a statically linked Linux binary and has no native
Windows build, so on Windows ORCAdesk runs it inside **WSL**: a per-job ext4
scratch directory holds the heavy I/O (9P is 5-300x slower) and results are
copied back to the Windows workspace folder. On Linux the same binary runs
directly, in the calc folder, with nothing to copy. ``shell.py`` is the single
place that decides which, so ``env.py`` / ``installer.py`` / ``runner.py`` are
one implementation rather than two. A Windows/Linux switch in Settings would be
a setting the user could only get wrong — no machine is both; what the user
actually picks is *which* target, and on Linux there is exactly one.

Either way the job is launched detached (``setsid``) so it survives ORCAdesk
closing, exactly like a detached ORCA run.

Modules:
* ``shell.py``    — the transport: WSL distro vs. this machine, and the three
                    things that differ between them (how a command runs, how a
                    path is named inside that shell, whether a scratch dir pays).
* ``wsl.py``      — low-level ``wsl.exe`` helpers, the Windows half of the above.
* ``parser.py``   — read a finished run's ``crest_conformers.xyz`` +
                    ``crest.energies`` into the shared ``ParseResult`` (the
                    conformer ensemble, ranked, with per-conformer geometry).
* ``env.py``      — detect a target that has the ``crest`` binary (backs the
                    "CREST ready" indicator).
* ``installer.py``— download the static CREST release binary onto a target
                    (no user shell interaction needed).
* ``runner.py``   — launch/monitor/cancel a CREST run (detach + process-group
                    kill, through whichever transport is active).

v1 scope: conformer search only (calc kind ``crest_conf``).
"""
