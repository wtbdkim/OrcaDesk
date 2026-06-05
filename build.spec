# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for ORCAdesk.

Why a .spec (not a one-line command):
  * QtWebEngine ships a whole Chromium runtime (QtWebEngineProcess, .pak
    resources, ICU data, locales). PyInstaller's Qt hooks usually collect these,
    but we force it with collect_all() so a missing file doesn't produce the
    classic "blank white window" at runtime.
  * Our own assets (web/, data/) must be bundled and land at the SAME relative
    paths the code expects (resource_root()/web, resource_root()/data).

Build:   pyinstaller build.spec --noconfirm
Output:  dist/ORCAdesk/           (onedir — recommended for WebEngine)

Distribute the whole dist/ORCAdesk/ folder (zip it). Do NOT ship just the
.exe; the WebEngine runtime lives beside it.
"""

from PyInstaller.utils.hooks import collect_all

block_cipher = None

# --- collect QtWebEngine (binaries, data, hidden imports) ---
we_datas, we_binaries, we_hidden = collect_all("PyQt6.QtWebEngineWidgets")
we_datas2, we_binaries2, we_hidden2 = collect_all("PyQt6.QtWebEngineCore")

datas = []
binaries = []
hiddenimports = []

datas += we_datas + we_datas2
binaries += we_binaries + we_binaries2
hiddenimports += we_hidden + we_hidden2

# --- our bundled assets (source ; destination-relative-to-bundle-root) ---
datas += [
    ("web", "web"),
    ("web_mobile", "web_mobile"),
    ("data", "data"),
    ("resources", "resources"),
]

hiddenimports += [
    "PyQt6.QtWebChannel",
    "PyQt6.QtNetwork",
]


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

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
