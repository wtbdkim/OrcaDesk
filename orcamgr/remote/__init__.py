"""
Remote compute devices reached over SSH (docs/REMOTE_DEVICES_DESIGN_KR.md).

The queue hands a dependency-ready ORCA calculation to this machine or to a
Linux device the user has already set up in ``~/.ssh/config``; the device is a
scratch space, this machine stays the source of truth (the whole run folder
comes back before the calculation is DONE).

* ``ssh.py``      the transport: the system OpenSSH client, errors as data.
* ``devices.py``  which devices exist (``~/.ssh/config``) and what each one is
                  (a probe over SSH); the per-device settings' shape.
* ``runner.py``   ``RemoteOrcaRunner`` -- ``OrcaRunner``'s verbs over SSH --
                  and the fetch / verify / guarded-delete steps.

Qt-free, like ``core/``, ``crest/`` and ``mlip/``.
"""
