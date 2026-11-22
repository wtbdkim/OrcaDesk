"""The Windows build's license notices stay in step with what it bundles.

The installer bundles PyQt6 (GPL-3.0), Qt (LGPL-3.0), Chromium, Python and
others, so it is conveyed under the GPL-3.0 as a whole and has to carry the
license texts and notices (LICENSE-BINARY.txt, THIRD_PARTY_NOTICES.txt,
licenses/; docs/ARCHITECTURE.md "Packaging the Windows build"). These tests
pin the seams that rot silently:

  * a version bump that leaves installer.iss or the corresponding-source tag
    in LICENSE-BINARY.txt behind (installer.iss was untracked once, and stayed
    at 0.5.0 for three releases)
  * a pin in requirements-build.txt changing without the notices saying so
  * a notice pointing at a license file that is not there, or a license file
    no notice explains
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orcamgr.paths import APP_VERSION  # noqa: E402

LICENSES = ROOT / "licenses"


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8-sig")


def _iss_define(name: str) -> str:
    m = re.search(rf'^#define {name} "([^"]*)"', _read("installer.iss"), re.M)
    assert m, f"installer.iss defines no {name}"
    return m.group(1)


def test_installer_version_follows_app_version():
    assert _iss_define("MyAppVersion") == APP_VERSION
    assert _iss_define("MyAppVersionFull").startswith(APP_VERSION)


def test_installer_shows_the_binary_license_and_is_tracked():
    iss = _read("installer.iss")
    assert re.search(r"^LicenseFile=LICENSE-BINARY\.txt\s*$", iss, re.M)
    assert _iss_define("MyAppPublisher") == "Taewoo Kim"
    # it is part of the installer's corresponding source, so never ignored
    ignored = [ln.strip() for ln in _read(".gitignore").splitlines()]
    assert "installer.iss" not in ignored


def test_the_build_carries_the_notices():
    spec = _read("build.spec")
    for name in ("LICENSE", "LICENSE-BINARY.txt", "THIRD_PARTY_NOTICES.txt"):
        assert f'"{name}"' in spec, f"build.spec does not copy {name}"
    assert '"licenses"' in spec


def test_corresponding_source_names_this_release():
    text = _read("LICENSE-BINARY.txt")
    assert f"ORCAdesk {APP_VERSION}" in text
    assert f"/tree/v{APP_VERSION}" in text
    assert _read("THIRD_PARTY_NOTICES.txt").splitlines()[0].startswith(
        f"ORCAdesk {APP_VERSION} ")


def _notice_blocks() -> dict[str, str]:
    """THIRD_PARTY_NOTICES.txt's numbered items: heading -> body."""
    text = _read("THIRD_PARTY_NOTICES.txt")
    parts = re.split(r"^\d+\. (.+)\n-+\n", text, flags=re.M)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def _version_line(block: str) -> str:
    m = re.search(r"^Version:\s+(.+)$", block, re.M)
    assert m, block[:200]
    return m.group(1)


def test_every_bundled_pin_is_in_the_notices():
    pins = dict(re.findall(r"^([A-Za-z0-9_.-]+)==(\S+)", _read("requirements-build.txt"), re.M))
    blocks = _notice_blocks()
    # requirements-build.txt name -> the notice item that names its version
    item = {
        "PyQt6": "PyQt6",
        "PyQt6-WebEngine": "PyQt6-WebEngine",
        "PyQt6-sip": "PyQt6-sip",
        "PyQt6-Qt6": "Qt",
        "PyQt6-WebEngine-Qt6": "Qt WebEngine, with Chromium",
        "psutil": "psutil",
        "numpy": "NumPy",
        "pyinstaller": "PyInstaller bootloader, loader and run-time hooks",
    }
    for pin, heading in item.items():
        assert pin in pins, f"requirements-build.txt no longer pins {pin}"
        assert heading in blocks, f"THIRD_PARTY_NOTICES.txt has no item {heading!r}"
        assert pins[pin] in _version_line(blocks[heading]), (pin, pins[pin])


def test_license_files_and_notices_match():
    referenced = set()
    for name in ("THIRD_PARTY_NOTICES.txt", "LICENSE-BINARY.txt"):
        referenced |= set(re.findall(r"licenses\\([A-Za-z0-9_.+-]+\.txt)", _read(name)))
    present = {p.name for p in LICENSES.glob("*.txt")}
    assert referenced - present == set(), "notices point at missing files"
    # every file in licenses/ is explained in THIRD_PARTY_NOTICES.txt
    notices = _read("THIRD_PARTY_NOTICES.txt")
    unexplained = {n for n in present if n not in notices}
    assert unexplained == set()
