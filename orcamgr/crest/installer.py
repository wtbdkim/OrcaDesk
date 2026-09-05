"""
Auto-install CREST onto a target, with no user shell interaction.

CREST publishes a **statically linked** Linux binary, so installation is just:
download the release tarball, extract it, and symlink the binary — no compiler,
no conda, no dependency resolution, and (because it is static) no glibc concerns.
This is the whole reason ORCAdesk can install CREST for the user: a static tar is
fully scriptable, unlike the WSL distro provisioning itself (which needs the user
to create a Linux account once).

The script is plain POSIX and knows nothing about WSL, so it installs into a WSL
distro on Windows and onto the machine itself on Linux with no change at all —
the transport (``shell.py``) is the only difference, and it is one import. What
does NOT carry over is macOS: the published asset is an Ubuntu build, so
installing it there would produce a binary that cannot run, and
:func:`install_crest` refuses up front and says where to get a real one (P2 —
reported, never a mystery failure at the first launch).

Verified end to end on WSL Ubuntu: the binary lands at
``~/.local/opt/crest/crest/crest`` and is symlinked to ``~/.local/bin/crest``.
"""

from __future__ import annotations

import sys

from .shell import is_missing, missing_message, run_bash
from .env import resolve_crest_bin

# GitHub's /latest/download/ redirects to the current stable release's asset.
# The GNU (gfortran+OpenBLAS) build is the portable pick; the asset name is
# stable across the tagged and rolling releases.
CREST_RELEASE_URL = (
    "https://github.com/crest-lab/crest/releases/latest/download/"
    "crest-gnu-12-ubuntu-latest.tar.xz"
)

# Uses curl if present, else wget. Fails loudly (nonzero rc + message) if neither
# a downloader nor the extractors are available — which is why there is no
# separate pre-flight tool check: the install script IS the check, and reports a
# missing tool as an ordinary install failure with the same message channel.
_INSTALL_SCRIPT = f'''
set -u
have() {{ command -v "$1" >/dev/null 2>&1; }}
if ! have tar || ! have xz; then
  echo "ORCAdesk-ERR: 'tar'/'xz' missing in this distro (install: sudo apt install -y xz-utils tar)"; exit 91
fi
if ! have curl && ! have wget; then
  echo "ORCAdesk-ERR: neither 'curl' nor 'wget' is available (install: sudo apt install -y curl)"; exit 92
fi
DEST="$HOME/.local/opt/crest"; BIN="$HOME/.local/bin"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
URL="{CREST_RELEASE_URL}"
echo "ORCAdesk: downloading CREST ..."
if have curl; then
  curl -fL --retry 3 -o "$TMP/crest.tar.xz" "$URL" || {{ echo "ORCAdesk-ERR: download failed"; exit 93; }}
else
  wget -O "$TMP/crest.tar.xz" "$URL" || {{ echo "ORCAdesk-ERR: download failed"; exit 93; }}
fi
echo "ORCAdesk: extracting ..."
rm -rf "$DEST"; mkdir -p "$DEST" "$BIN"
tar -xf "$TMP/crest.tar.xz" -C "$DEST" || {{ echo "ORCAdesk-ERR: extract failed"; exit 94; }}
CRESTBIN="$(find "$DEST" -type f -name crest | head -1)"
if [ -z "$CRESTBIN" ]; then echo "ORCAdesk-ERR: crest binary not found in archive"; exit 95; fi
chmod +x "$CRESTBIN"
ln -sf "$CRESTBIN" "$BIN/crest"
echo "ORCAdesk-OK: $CRESTBIN"
'''


def install_crest(distro: str, timeout: float = 300.0) -> dict:
    """Download + install the static CREST binary onto ``distro`` (a WSL distro,
    or ``shell.LOCAL_TARGET``). Returns {ok, crest_bin, version, error}.
    Blocking (run it off the UI thread)."""
    if sys.platform == "darwin":
        return {"ok": False, "crest_bin": "", "version": "",
                "error": ("the published CREST build is a Linux binary and will "
                          "not run on macOS. Install CREST yourself (conda-forge: "
                          "`conda install -c conda-forge crest`); ORCAdesk will "
                          "find it on PATH.")}
    rc, out, err = run_bash(distro, _INSTALL_SCRIPT, timeout=timeout)
    if is_missing(err):
        return {"ok": False, "crest_bin": "", "version": "",
                "error": missing_message()}
    if rc != 0 or "ORCAdesk-OK:" not in out:
        msg = ""
        for line in out.splitlines():
            if "ORCAdesk-ERR:" in line:
                msg = line.split("ORCAdesk-ERR:", 1)[1].strip()
                break
        if not msg:
            msg = (err.strip() or out.strip() or f"install failed (exit {rc})")
        return {"ok": False, "crest_bin": "", "version": "", "error": msg}

    # Confirm by resolving + version-probing the freshly installed binary.
    path, version = resolve_crest_bin(distro, timeout=30.0)
    if not path:
        return {"ok": False, "crest_bin": "", "version": "",
                "error": "CREST installed but could not be located afterward."}
    return {"ok": True, "crest_bin": path, "version": version, "error": ""}
