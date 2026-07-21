"""
Auto-install CREST into a WSL distro, with no user shell interaction.

CREST publishes a **statically linked** Linux binary, so installation is just:
download the release tarball, extract it, and symlink the binary — no compiler,
no conda, no dependency resolution, and (because it is static) no glibc concerns.
This is the whole reason ORCAdesk can install CREST for the user: a static tar is
fully scriptable, unlike the WSL distro provisioning itself (which needs the user
to create a Linux account once).

Verified end to end on WSL Ubuntu: the binary lands at
``~/.local/opt/crest/crest/crest`` and is symlinked to ``~/.local/bin/crest``.
"""

from __future__ import annotations

from .wsl import run_bash
from .env import resolve_crest_bin

# GitHub's /latest/download/ redirects to the current stable release's asset.
# The GNU (gfortran+OpenBLAS) build is the portable pick; the asset name is
# stable across the tagged and rolling releases.
CREST_RELEASE_URL = (
    "https://github.com/crest-lab/crest/releases/latest/download/"
    "crest-gnu-12-ubuntu-latest.tar.xz"
)

_TOOLS_SCRIPT = (
    'for t in curl wget tar xz; do '
    'if command -v "$t" >/dev/null 2>&1; then echo "$t=1"; else echo "$t=0"; fi; done'
)

# Uses curl if present, else wget. Fails loudly (nonzero rc + message) if neither
# a downloader nor the extractors are available.
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


def distro_can_install(distro: str) -> dict:
    """Check the tools an auto-install needs. Returns
    {ok, missing:[...], has_downloader}."""
    rc, out, _ = run_bash(distro, _TOOLS_SCRIPT, timeout=20.0)
    present = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.strip().split("=", 1)
            present[k] = (v == "1")
    has_downloader = present.get("curl", False) or present.get("wget", False)
    missing = []
    if not present.get("tar", False):
        missing.append("tar")
    if not present.get("xz", False):
        missing.append("xz")
    if not has_downloader:
        missing.append("curl-or-wget")
    return {"ok": (not missing), "missing": missing, "has_downloader": has_downloader}


def install_crest(distro: str, timeout: float = 300.0) -> dict:
    """Download + install the static CREST binary into ``distro``. Returns
    {ok, crest_bin, version, error}. Blocking (run it off the UI thread)."""
    rc, out, err = run_bash(distro, _INSTALL_SCRIPT, timeout=timeout)
    if err == "wsl-not-found":
        return {"ok": False, "crest_bin": "", "version": "",
                "error": "wsl.exe not found — WSL is required."}
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
