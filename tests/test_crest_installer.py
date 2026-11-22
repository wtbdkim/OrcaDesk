"""
The CREST auto-installer downloads a binary and unpacks it on the user's
machine (a WSL distro on Windows, the machine itself on Linux), so what it
fetches is pinned and checked:

* a NAMED release, never ``/releases/latest/`` (which moves under the user),
* whose archive must match a recorded SHA-256 before ``tar`` ever sees it,
* and a mismatch is a failure with a sentence that says so -- nothing unpacked,
  nothing run, an existing install left as it was.

The script is transport-agnostic (shell.py picks WSL or local bash), so most
checks read the generated script and fake the transport. One test RUNS the
script against a local archive in a POSIX bash with HOME pointed at a tmp dir:
on Linux the system bash, on Windows Git for Windows' bash -- never
``System32\\bash.exe``, which is WSL's launcher and would install into a real
distro's home.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from orcamgr.crest import installer


# ------------------------------------------------------------------ the pin

def test_the_installer_fetches_a_named_release_never_latest():
    """/releases/latest/ resolves to whatever was published last -- the
    checksum would then fail on the next upstream release, or (pinned to
    nothing) check nothing at all."""
    url = installer.CREST_RELEASE_URL
    assert "/latest/" not in url
    assert url == ("https://github.com/crest-lab/crest/releases/download/"
                   f"v{installer.CREST_VERSION}/{installer.CREST_ASSET}")
    assert re.fullmatch(r"\d+\.\d+(\.\d+)?", installer.CREST_VERSION)


def test_the_pinned_checksum_is_a_sha256():
    assert re.fullmatch(r"[0-9a-f]{64}", installer.CREST_SHA256)


def test_the_script_carries_the_pinned_url_and_checksum():
    s = installer.install_script()
    assert f"URL={installer.CREST_RELEASE_URL}" in s
    assert f"WANT={installer.CREST_SHA256}" in s
    assert "releases/latest" not in s


def test_the_checksum_is_compared_before_anything_is_unpacked_or_removed():
    """The order is the whole guarantee: verify, THEN remove the old install,
    THEN extract. Removing first would leave the user with no CREST after a
    refused download."""
    s = installer.install_script()
    check = s.index('if [ "$GOT" != "$WANT" ]')
    assert s.index('curl -fL') < check
    assert check < s.index('rm -rf "$DEST"') < s.index("tar -xf")
    # the mismatch branch ends the script, with its own exit code
    branch = s[check:s.index("\nfi\n", check)]
    assert "ORCAdesk-ERR:" in branch and "SHA-256 mismatch" in branch
    assert "exit 97" in branch


def test_a_missing_hash_tool_refuses_instead_of_installing_unchecked():
    s = installer.install_script()
    assert "have sha256sum" in s and "exit 96" in s
    assert s.index("exit 96") < s.index("curl -fL")


def test_script_values_are_shell_quoted():
    s = installer.install_script(url="file:///tmp/a b.tar.xz", sha256="AB" * 32)
    assert "URL='file:///tmp/a b.tar.xz'" in s
    assert "WANT=" + "ab" * 32 in s           # compared in lower case


# ----------------------------------------------------- through the transport

def _fake_transport(monkeypatch, rc, out, err=""):
    seen = {}

    def run_bash(target, script, timeout=0, login=False):
        seen["target"], seen["script"] = target, script
        return rc, out, err

    def no_probe(*_a, **_kw):
        raise AssertionError("a failed install must not go on to probe for CREST")

    monkeypatch.setattr(installer, "run_bash", run_bash)
    monkeypatch.setattr(installer, "resolve_crest_bin", no_probe)
    return seen


@pytest.mark.parametrize("platform,target", [("win32", "Ubuntu"), ("linux", "local")])
def test_both_transports_are_given_the_pinned_script(monkeypatch, platform, target):
    monkeypatch.setattr(sys, "platform", platform)
    seen = _fake_transport(monkeypatch, 0, "ORCAdesk-OK: /x/crest")
    monkeypatch.setattr(installer, "resolve_crest_bin",
                        lambda *_a, **_kw: ("/x/crest", "Version 3.0.2"))
    res = installer.install_crest(target)
    assert res["ok"] is True
    assert seen["target"] == target
    assert installer.CREST_SHA256 in seen["script"]
    assert installer.CREST_RELEASE_URL in seen["script"]


def test_a_checksum_mismatch_is_a_failure_with_a_clear_message(monkeypatch):
    line = ("ORCAdesk-ERR: the downloaded file is not the expected CREST 3.0.2 "
            "release (SHA-256 mismatch: expected aa, got bb). It was deleted "
            "without being unpacked or run; nothing was installed or changed.")
    _fake_transport(monkeypatch, 97, "ORCAdesk: checking SHA-256 ...\n" + line)
    res = installer.install_crest("Ubuntu")
    assert res["ok"] is False and res["crest_bin"] == ""
    assert "SHA-256 mismatch" in res["error"]
    assert "nothing was installed" in res["error"]


# ------------------------------------------------- the script, actually run

def _posix_bash():
    """A bash that runs HERE, or None. On Windows only Git for Windows' bash
    qualifies: ``bash`` on PATH may be WSL's launcher."""
    if not sys.platform.startswith("win"):
        return shutil.which("bash")
    git = shutil.which("git")
    if not git:
        return None
    for up in Path(git).resolve().parents:
        cand = up / "usr" / "bin" / "bash.exe"
        if cand.is_file():
            return str(cand)
    return None


def _fake_release(tmp_path):
    """A tar.xz shaped like the real asset: crest/crest + the licence files."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tar:
        for name, data in (("crest/crest", b"#!/bin/sh\necho fake crest\n"),
                           ("crest/LICENSE", b"GPL-3.0 text\n"),
                           ("crest/LICENSE.LESSER", b"LGPL-3.0 text\n")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
    path = tmp_path / "release" / "crest.tar.xz"
    path.parent.mkdir()
    path.write_bytes(buf.getvalue())
    return path, hashlib.sha256(buf.getvalue()).hexdigest()


def _run(bash, script, home):
    env = dict(os.environ, HOME=str(home))
    return subprocess.run([bash, "-c", script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=120)


@pytest.mark.skipif(_posix_bash() is None, reason="no local POSIX bash")
def test_the_script_refuses_a_tampered_download_and_keeps_the_old_install(tmp_path):
    bash = _posix_bash()
    probe = _run(bash, "command -v curl && command -v xz && command -v tar", tmp_path)
    if probe.returncode != 0:
        pytest.skip("curl / xz / tar not available in this bash")
    archive, good = _fake_release(tmp_path)
    url = archive.resolve().as_uri()
    home = tmp_path / "home"
    old = home / ".local" / "opt" / "crest" / "crest" / "crest"
    old.parent.mkdir(parents=True)
    old.write_text("the CREST the user already had\n")

    bad = "0" * 64
    p = _run(bash, installer.install_script(url=url, sha256=bad), home)
    assert p.returncode == 97, p.stdout + p.stderr
    assert "SHA-256 mismatch" in p.stdout and "ORCAdesk-OK" not in p.stdout
    assert old.read_text() == "the CREST the user already had\n"
    assert not (home / ".local" / "opt" / "crest" / "crest" / "LICENSE").exists()

    # ...and the same archive with its real hash installs
    p = _run(bash, installer.install_script(url=url, sha256=good), home)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "ORCAdesk-OK:" in p.stdout
    assert (home / ".local" / "opt" / "crest" / "crest" / "LICENSE.LESSER").exists()
