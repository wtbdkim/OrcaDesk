"""
Path resolution for both development and PyInstaller-frozen execution.

Two distinct roots:

* RESOURCE root  -- read-only bundled assets (web/, data/).
  In a frozen build PyInstaller unpacks these into a temp dir exposed as
  ``sys._MEIPASS``; in development they sit next to the project.

* USER DATA root  -- writable per-user storage for settings, cache, and
  (by default) calculation workspaces. Never write into the resource root,
  because in a frozen build it is a temp dir that disappears on exit and may
  be read-only.

This separation is what makes the app safely distributable as an .exe.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "ORCAdesk"
APP_VERSION = "0.1.2-beta"
APP_AUTHOR = "Taewoo Kim"
APP_ORG = "Korea Science Academy of KAIST"
APP_EMAIL = "wtbdkim@gmail.com"


def is_frozen() -> bool:
    """True when running inside a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def resource_root() -> Path:
    """Root for read-only bundled resources (web/, data/)."""
    if is_frozen():
        # PyInstaller unpacks data files here
        return Path(getattr(sys, "_MEIPASS"))
    # dev: project root = two levels up from this file
    #   <project>/orcamgr/paths.py  ->  <project>
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    """Build a path to a bundled resource, e.g. resource_path('web', 'index.html')."""
    return resource_root().joinpath(*parts)


def data_dir() -> Path:
    """Directory holding the bundled JSON option lists."""
    return resource_path("data")


def web_dir() -> Path:
    """Directory holding the HTML/JS/CSS front-end."""
    return resource_path("web")


def web_mobile_dir() -> Path:
    """Directory holding the mobile PWA front-end."""
    return resource_path("web_mobile")


def user_data_root() -> Path:
    """
    Writable per-user application directory.

    Windows : %APPDATA%/ORCAdesk
    macOS   : ~/Library/Application Support/ORCAdesk
    Linux   : $XDG_CONFIG_HOME/ORCAdesk or ~/.config/ORCAdesk
    """
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    root = Path(base) / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def config_file() -> Path:
    """Path to the persisted settings file."""
    return user_data_root() / "settings.json"


def default_workspace_root() -> Path:
    """Default location for ORCA calculation outputs."""
    ws = user_data_root() / "workspaces"
    ws.mkdir(parents=True, exist_ok=True)
    return ws
