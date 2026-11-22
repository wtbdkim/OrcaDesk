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

**One pinned release, checked before it is unpacked.** The script fetches a
named release (never ``/releases/latest/``, which moves under the user) and
compares the archive's SHA-256 with the one recorded here before ``tar`` sees
it. A mismatch deletes the download and stops with a sentence that says so:
nothing is extracted, nothing is run, and a CREST already installed is left as
it was (the old install is only removed after the check passes). Bumping the
release is three constants and a re-hash, see ``docs/ARCHITECTURE.md``
(*CREST*).

What it installs is CREST, LGPL-3.0-or-later (``LICENSE`` + ``LICENSE.LESSER``
inside the archive); its static binary carries the xTB methods through tblite,
also LGPL-3.0-or-later. The separate ``xtb`` program is not installed.
"""

from __future__ import annotations

import shlex
import sys

from .shell import is_missing, missing_message, run_bash
from .env import resolve_crest_bin

# The pinned release. v3.0.2 is what GitHub's /releases/latest/ resolved to when
# this was pinned (2026-11-22) -- the "latest" TAG is a rolling prerelease and is
# deliberately not used. The GNU (gfortran + OpenBLAS) build is the portable pick.
CREST_VERSION = "3.0.2"
CREST_ASSET = "crest-gnu-12-ubuntu-latest.tar.xz"
CREST_RELEASE_URL = (
    f"https://github.com/crest-lab/crest/releases/download/"
    f"v{CREST_VERSION}/{CREST_ASSET}"
)
# SHA-256 of that asset (8,764,012 bytes). The v3.0.2 release publishes no
# checksum of its own (GitHub's API shows no digest for assets that old), so it
# was computed here: downloaded twice -- once by the tag URL, once through the
# /releases/latest/ redirect -- and hashed with sha256sum and certutil, all four
# agreeing.
CREST_SHA256 = "8e5bd18b06f99741ebd7bb71b3a996295f391b2f076aaeab739740f709e9554d"
# Where it comes from and under which terms, for the Settings card and the docs.
CREST_SOURCE = "github.com/crest-lab/crest"
CREST_LICENSE = "LGPL-3.0"

# Uses curl if present, else wget. Fails loudly (nonzero rc + message) if neither
# a downloader nor the extractors are available — which is why there is no
# separate pre-flight tool check: the install script IS the check, and reports a
# missing tool as an ordinary install failure with the same message channel.
# The hash tool is part of that check: sha256sum (coreutils, in every Ubuntu),
# else shasum, else openssl -- without one the download cannot be verified, so
# the install refuses rather than unpacks it unchecked.
_SCRIPT_TEMPLATE = '''
set -u
have() {{ command -v "$1" >/dev/null 2>&1; }}
if ! have tar || ! have xz; then
  echo "ORCAdesk-ERR: 'tar'/'xz' missing in this distro (install: sudo apt install -y xz-utils tar)"; exit 91
fi
if ! have curl && ! have wget; then
  echo "ORCAdesk-ERR: neither 'curl' nor 'wget' is available (install: sudo apt install -y curl)"; exit 92
fi
if ! have sha256sum && ! have shasum && ! have openssl; then
  echo "ORCAdesk-ERR: no SHA-256 tool (sha256sum) to verify the download with (install: sudo apt install -y coreutils)"; exit 96
fi
sha256_of() {{
  if have sha256sum; then sha256sum "$1" | cut -d' ' -f1
  elif have shasum; then shasum -a 256 "$1" | cut -d' ' -f1
  else openssl dgst -sha256 "$1" | sed 's/^.*= *//'
  fi
}}
DEST="$HOME/.local/opt/crest"; BIN="$HOME/.local/bin"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
URL={url}
WANT={sha256}
echo "ORCAdesk: downloading CREST {version} from $URL ..."
if have curl; then
  curl -fL --retry 3 -o "$TMP/crest.tar.xz" "$URL" || {{ echo "ORCAdesk-ERR: download failed"; exit 93; }}
else
  wget -O "$TMP/crest.tar.xz" "$URL" || {{ echo "ORCAdesk-ERR: download failed"; exit 93; }}
fi
echo "ORCAdesk: checking SHA-256 ..."
GOT="$(sha256_of "$TMP/crest.tar.xz" | tr 'A-F' 'a-f')"
if [ "$GOT" != "$WANT" ]; then
  echo "ORCAdesk-ERR: the downloaded file is not the expected CREST {version} release (SHA-256 mismatch: expected $WANT, got ${{GOT:-nothing}}). It was deleted without being unpacked or run; nothing was installed or changed."; exit 97
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


def install_script(url: str = CREST_RELEASE_URL, sha256: str = CREST_SHA256,
                   version: str = CREST_VERSION) -> str:
    """The bash script that downloads ``url``, refuses it unless its SHA-256 is
    ``sha256``, and only then unpacks it. The defaults are the pinned release;
    the parameters exist so a test can point it at a local file. Every value is
    shell-quoted, though none of them comes from the user."""
    return _SCRIPT_TEMPLATE.format(url=shlex.quote(url),
                                   sha256=shlex.quote(sha256.strip().lower()),
                                   version=version)


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
    rc, out, err = run_bash(distro, install_script(), timeout=timeout)
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
