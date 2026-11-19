"""Keep the whole test run away from the user's own data.

paths.user_data_root() reads %APPDATA% (Windows) / $XDG_CONFIG_HOME (Linux) on
every call, and the store autosaves session.json there. Fixtures monkeypatch
user_data_root per test, but a monkeypatch is undone at teardown -- and a run
thread a failed test leaves behind saves AFTER that, into the real folder.
That once replaced a user's queue with a test's three rows.

So, for the whole process and never restored: the variables point at a
throwaway folder, set here before any test module is imported. Only a copy of
the real settings.json goes in (the opt-in smoke matrix reads the configured
ORCA / MLIP / CREST from it); nothing is ever written back. PYTHONUSERBASE is
pinned first, because on Windows the user site-packages hang off %APPDATA% too
and child interpreters a test starts must still find them.

macOS keeps its data under ~/Library and is not redirected (untested platform).
"""

from __future__ import annotations

import atexit
import os
import shutil
import site
import sys
import tempfile
from pathlib import Path

from orcamgr import paths

_real_root = paths.user_data_root()
os.environ.setdefault("PYTHONUSERBASE", site.getuserbase())

_sandbox = Path(tempfile.mkdtemp(prefix="orcadesk-pytest-"))
os.environ["APPDATA"] = str(_sandbox)
os.environ["XDG_CONFIG_HOME"] = str(_sandbox)
atexit.register(shutil.rmtree, _sandbox, True)

_settings = _real_root / "settings.json"
if _settings.is_file():
    (_sandbox / paths.APP_NAME).mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(_settings, _sandbox / paths.APP_NAME / "settings.json")
    except OSError:
        pass

assert sys.platform == "darwin" or paths.user_data_root() != _real_root, \
    "tests must not run against the real user data folder"
