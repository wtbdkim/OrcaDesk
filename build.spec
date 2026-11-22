# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for ORCAdesk.

Why a .spec (not a one-line command):
  * Our own assets (web/, data/) must be bundled and land at the SAME relative
    paths the code expects (resource_root()/web, resource_root()/data).
  * The Qt modules that ship in the PyQt6 wheels but that the app never uses
    are kept out (see "Qt modules" below), and so is anything the build
    machine happens to have installed.
  * The licence texts and notices ship with the binary (see "Notices" below).

Build:   build.bat   (a clean virtual environment, .venv-build, with the
                      pinned versions from requirements-build.txt, then:)
         .venv-build/Scripts/python -m PyInstaller build.spec --noconfirm
Output:  dist/ORCAdesk/           (onedir — recommended for WebEngine)

Build from the clean environment, never from a global site-packages: what
PyInstaller finds importable is what it bundles, and every bundled component
carries a licence notice (THIRD_PARTY_NOTICES.txt) that has to match.

Distribute the whole dist/ORCAdesk/ folder (zip it). Do NOT ship just the
.exe; the WebEngine runtime lives beside it.
"""

import os
import shutil
from pathlib import Path

import PyQt6

block_cipher = None

# --- QtWebEngine -------------------------------------------------------------
# PyInstaller's own hooks (hook-PyQt6.QtWebEngineCore) collect the Chromium
# runtime: QtWebEngineProcess.exe, the .pak resources, ICU data and the
# locales. (This spec used collect_all() on the two modules for that; they are
# extension modules, not packages, so collect_all() collected nothing and only
# added the hidden imports below.)
hiddenimports = [
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebChannel",
    "PyQt6.QtNetwork",
]

# --- our bundled assets (source ; destination-relative-to-bundle-root) ---
datas = [
    ("web", "web"),
    ("web_mobile", "web_mobile"),
    ("data", "data"),
    ("resources", "resources"),
]

# --- Qt modules ----------------------------------------------------------------
# The PyQt6 modules the app imports, plus QtPrintSupport, which
# PyQt6.QtWebEngineWidgets imports itself. Every other PyQt6 extension module
# is excluded. Without this, QtWebEngineWidgets' link to Qt6QuickWidgets made
# PyInstaller pull in PyQt6.QtQuick and PyQt6.QtQml, whose hook collects the
# WHOLE qml/ tree, and the QML plugins in it dragged in about 60 Qt DLLs the
# app never loads: Quick3D (+Physics, +Xr), QuickTimeline, Multimedia, Pdf,
# Sensors, SerialPort, TextToSpeech, RemoteObjects, Test, ... Some of those
# (Quick3D, Quick3DPhysics, QuickTimeline) Qt offers only under the GPL.
# The Qt DLLs the kept modules really link (Qt6Quick, Qt6Qml, Qt6Positioning,
# ... for Qt6WebEngineCore) are still collected by PyInstaller's binary
# dependency analysis; only the Python bindings and the qml/ tree go.
_PYQT6_USED = {
    "QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtPrintSupport",
    "QtWebChannel", "QtWebEngineCore", "QtWebEngineWidgets",
}
_pyqt6_dir = Path(PyQt6.__file__).resolve().parent
_PYQT6_UNUSED = sorted(
    p.name.split(".")[0] for p in _pyqt6_dir.glob("Qt*.pyd")
    if p.name.split(".")[0] not in _PYQT6_USED
) + ["QAxContainer"]

# Phone-sync is NOT part of the packaged build (CHANGELOG/CLAUDE.md): the
# feature is optional at runtime (ServerController.is_available()), but
# PyInstaller's bytecode scan follows the function-level fastapi/uvicorn/
# qrcode imports, so whether the whole server stack landed in the bundle
# depended on the build machine's site-packages — nondeterministic packaging
# and, when present, an unreviewed LAN-binding feature live in the shipped
# exe. Exclude it explicitly; drop these lines deliberately when phone-sync
# ships for real.
excludes = [
    "fastapi", "uvicorn", "starlette", "pydantic", "pydantic_core",
    "anyio", "qrcode", "PIL",
]
# Packages a polluted build machine leaked into earlier builds (PyYAML,
# charset_normalizer, typing_extensions). The clean .venv-build has none of
# them; the excludes keep them out if someone builds elsewhere.
excludes += ["yaml", "charset_normalizer", "typing_extensions"]
excludes += ["PyQt6." + m for m in _PYQT6_UNUSED]


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)


# --- files the analysis found that the app does not use ------------------------
def _drop(dest):
    name = dest.replace("\\", "/").rsplit("/", 1)[-1].lower()
    # Qt's OpenSSL TLS backend, and the OpenSSL DLLs PyInstaller looks for on
    # the build machine's PATH to go with it (an earlier build took Git for
    # Windows' copies). The app makes no TLS connection through Qt; QtNetwork
    # keeps its Schannel backend. Python's own OpenSSL (libssl-3.dll /
    # libcrypto-3.dll, for _ssl/_hashlib) is a different file and stays.
    if name in ("qopensslbackend.dll", "libssl-3-x64.dll", "libcrypto-3-x64.dll"):
        return True
    # The PDF image-format plugin is the only thing that links Qt6Pdf (PDFium);
    # the app never reads a PDF as an image.
    if name == "qpdf.dll" or name.startswith("qt6pdf"):
        return True
    return False


a.binaries = [e for e in a.binaries if not _drop(e[0])]
a.datas = [e for e in a.datas if not _drop(e[0])]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ORCAdesk",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX can corrupt Qt DLLs; keep off
    console=False,             # windowed app (no console popup)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="resources/orcadesk.ico",   # app icon (orca)
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ORCAdesk",
)

# --- Notices ---------------------------------------------------------------------
# The license texts go next to ORCAdesk.exe (not into _internal), so the
# dist folder that gets zipped and the folder the installer copies both carry
# them where a user looks. LICENSE-BINARY.txt says what the binary is licensed
# under (the source is MIT; the build, which bundles PyQt6, is GPL-3.0 as a
# whole); THIRD_PARTY_NOTICES.txt lists every bundled component; licenses/
# holds the full texts. installer.iss refuses to compile without them.
# A change to what is bundled (a pin in requirements-build.txt, the Python
# version, an exclude here) changes what those files must say.
_root = Path(SPECPATH)
_out = Path(DISTPATH) / "ORCAdesk"
for _name in ("LICENSE", "LICENSE-BINARY.txt", "THIRD_PARTY_NOTICES.txt"):
    shutil.copy2(_root / _name, _out / _name)
shutil.copytree(_root / "licenses", _out / "licenses", dirs_exist_ok=True)
