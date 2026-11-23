"""
Detect a usable CREST environment: somewhere with the ``crest`` binary in it.

"Somewhere" is what ``shell.py`` decides — a WSL distro on Windows, this machine
on Linux — and this module is written against that abstraction rather than
against WSL, so the probe, the indicator and the run target are one piece of
logic on both platforms. A *target* is therefore a distro name or
``shell.LOCAL_TARGET``; the payload keeps calling it ``distro`` because that is
the wire field the desktop and the phone already speak.

Backs the top-bar "CREST ready" indicator (polled from the Bridge, like the MLIP
indicator). Unlike ``orca_is_valid()`` (a file-exists check), CREST readiness is
probed by actually running ``crest --version`` on each candidate target. The
probe is intentionally cheap (a single transport call per target) but still runs
off the UI thread on the Bridge.

The installer (``installer.py``) places the binary at
``~/.local/opt/crest/crest/crest`` and symlinks ``~/.local/bin/crest``; the probe
finds it there or anywhere on the login-shell PATH — which on Linux is also
where a distro package or a conda install of CREST already lives, so a user who
set CREST up themselves is detected with nothing to install.
"""

from __future__ import annotations

from .shell import (
    available as shell_available, is_local, list_targets,
    missing_message, run_bash, transport_kind,
)

# Candidate locations, checked in order. ``command -v`` first — run through a
# LOGIN shell (run_bash(login=True): plain ``wsl -e bash -c`` sources no
# profile, so a user-managed install visible only via ~/.profile / conda PATH
# would be missed) — then the paths our installer uses.
_RESOLVE_SCRIPT = (
    'p="$(command -v crest 2>/dev/null)"; '
    '[ -z "$p" ] && [ -x "$HOME/.local/bin/crest" ] && p="$HOME/.local/bin/crest"; '
    '[ -z "$p" ] && [ -x "$HOME/.local/opt/crest/crest/crest" ] && p="$HOME/.local/opt/crest/crest/crest"; '
    'if [ -n "$p" ]; then echo "CREST_BIN=$p"; "$p" --version 2>&1 | tr -d "\\r"; fi'
)


def resolve_crest_bin(distro: str, timeout: float = 30.0) -> tuple[str, str]:
    """Return (crest_bin_path, version_line) for ``distro`` — ("", "") if CREST
    isn't found. version_line is the first line of ``crest --version`` mentioning
    a version, if any."""
    rc, out, _ = run_bash(distro, _RESOLVE_SCRIPT, timeout=timeout, login=True)
    if rc != 0 or "CREST_BIN=" not in out:
        return "", ""
    path = ""
    version = ""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("CREST_BIN="):
            path = line[len("CREST_BIN="):].strip()
        elif "version" in line.lower() and not version:
            version = line
    return path, version


# Does this binary's --cinp parser take ``force constant=`` and ``reference=``?
# The CREST 3.0.2 release segfaults on either (measured, crest/runner.py); the
# continuous build cfdc301, which still says "Version 3.0.2", does not. The
# version line cannot tell them apart, so the binary is asked: a water with a
# constraints file holding both lines (and the coord.ref it names), cut off
# after 15 s. The release dies while parsing, in well under a second.
_CINP_PROBE = r"""
d=$(mktemp -d 2>/dev/null) || exit 3
cd "$d" || exit 3
cat > p.xyz <<'XYZ'
3

O 0.000000 0.000000 0.000000
H 0.000000 0.757000 0.587000
H 0.000000 -0.757000 0.587000
XYZ
cat > coord.ref <<'REF'
__COORD__REF
cat > c.inp <<'CINP'
$constrain
  atoms: 1,2
  force constant=0.5
  reference=coord.ref
$metadyn
  atoms: 3
$end
CINP
timeout 15 __BIN__ p.xyz --gfn2 -T 1 --cinp c.inp > log 2>&1
echo "PROBE_RC=$?"
echo "PROBE_CRASH=$(grep -ciE 'segmentation|sigsegv|error allocating' log)"
cd / && rm -rf "$d"
"""
_cinp_cache: dict = {}


def cinp_probe_verdict(out: str):
    """True / False from the probe's output, or None when it never ran (the
    transport failed) -- an unknown is not cached and reads as "no"."""
    rc = crash = None
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("PROBE_RC="):
            rc = line[len("PROBE_RC="):]
        elif line.startswith("PROBE_CRASH="):
            crash = line[len("PROBE_CRASH="):]
    if rc is None:
        return None
    if rc in ("134", "139") or (crash not in (None, "", "0")):
        return False
    return True


def cinp_extended(distro: str, crest_bin: str, version: str = "",
                  timeout: float = 40.0) -> bool:
    """Whether ``crest_bin`` on ``distro`` takes the extended --cinp lines
    (force constant, reference). Asked once per (target, binary, version
    line) and remembered; a binary replaced in place under the same version
    line is asked again only after a restart."""
    if not crest_bin:
        return False
    import shlex
    if not version:
        _, version = resolve_crest_bin(distro, timeout=timeout)
    key = (distro, crest_bin, version)
    if key in _cinp_cache:
        return _cinp_cache[key]
    from .runner import coord_ref_text
    ref = coord_ref_text("3\n\nO 0.000000 0.000000 0.000000\n"
                         "H 0.000000 0.757000 0.587000\nH 0.000000 -0.757000 0.587000\n")
    script = (_CINP_PROBE.replace("__COORD__", ref)
              .replace("__BIN__", shlex.quote(crest_bin)))
    try:
        _rc, out, _err = run_bash(distro, script, timeout=timeout, login=True)
    except Exception:
        return False
    verdict = cinp_probe_verdict(out)
    if verdict is None:
        return False
    _cinp_cache[key] = verdict
    return verdict


def probe_distro(distro: str, timeout: float = 30.0) -> dict:
    """Probe one target for CREST (a WSL distro, or this machine). Returns
    {distro, ready, crest_bin, version, error, cinp_extended}."""
    result = {"distro": distro, "ready": False, "crest_bin": "", "version": "", "error": "",
              "cinp_extended": False}
    try:
        path, version = resolve_crest_bin(distro, timeout=timeout)
    except Exception as e:  # never let a probe crash the indicator
        result["error"] = f"{type(e).__name__}: {e}"
        return result
    if path:
        result["ready"] = True
        result["crest_bin"] = path
        result["version"] = version
        result["cinp_extended"] = cinp_extended(distro, path, version)
    else:
        result["error"] = ("CREST not found on this machine." if is_local()
                           else "CREST not found in this distro.")
    return result


def probe_all(timeout: float = 30.0) -> list[dict]:
    """Probe every candidate target for CREST (WSL distros, or this machine)."""
    return [probe_distro(d, timeout=timeout) for d in list_targets()]


def aggregate_status(target_probes: list[dict]) -> dict:
    """Aggregate per-target probes into the top-bar status payload:
    {state, distros, wsl, transport}. state is 'ready' if any target has CREST,
    else 'unset' if there is no target at all (no WSL / no distro / no bash),
    else 'error'.

    ``wsl`` is kept as the field name — it is what the desktop and the phone
    already read — but it means "the transport is available", so a Linux machine
    with a working shell reports True. ``transport`` says which one it is, so
    the UI can word "no WSL distribution" and "no bash" as the different
    problems they are."""
    kind = transport_kind()
    if not shell_available():
        return {"state": "unset", "distros": [], "wsl": False, "transport": kind}
    if not target_probes:
        return {"state": "unset", "distros": [], "wsl": True, "transport": kind}
    state = "ready" if any(d.get("ready") for d in target_probes) else "error"
    return {"state": state, "distros": target_probes, "wsl": True, "transport": kind}


def no_target_message() -> str:
    """Why there is nowhere to run CREST. On Windows that is a missing distro
    the user must create themselves (D41: the one step ORCAdesk cannot script);
    locally the transport IS the target, so reaching here means bash is gone."""
    if is_local():
        return missing_message()
    return ("No usable WSL distribution found (only infrastructure distros like "
            "docker-desktop). Install a Linux distro, e.g. `wsl --install -d Ubuntu`.")


def not_installed_message() -> str:
    """Why the targets that exist cannot run CREST."""
    where = "on this machine" if is_local() else "in any WSL distribution"
    return (f"CREST is not installed {where}. Install it from "
            "Settings → CREST, or run the auto-installer.")


def resolve_run_target(preferred_distro: str = "", timeout: float = 30.0) -> tuple[str, str]:
    """Pick the (target, crest_bin) to run a CREST calc on: the preferred target
    if it has CREST, else the first one that does. Raises RuntimeError with a
    user-facing message if none is usable."""
    if not shell_available():
        raise RuntimeError(missing_message())
    targets = list_targets()
    if not targets:
        raise RuntimeError(no_target_message())
    order = ([preferred_distro] + [d for d in targets if d != preferred_distro]
             if preferred_distro else targets)
    for d in order:
        if not d:
            continue
        path, _ = resolve_crest_bin(d, timeout=timeout)
        if path:
            return d, path
    raise RuntimeError(not_installed_message())
