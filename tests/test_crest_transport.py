"""
CREST runs in a POSIX shell that is a WSL distro on Windows and this machine on
Linux. The transport is DETECTED, so the platform decides it and neither shape
can be exercised on the other's machine — which is exactly why these tests pass
``transport=`` explicitly and monkeypatch ``sys.platform``: the Linux script has
to be reviewable from Windows and the WSL script from Linux, or one of the two
is only ever seen by the user it breaks for.

The dangerous half is the generated ``run_crest.sh``. Under WSL it ends with
``rm -rf "$SCRATCH"`` on a directory inside the VHD; locally SCRATCH would BE
the user's calc folder, so that line must not exist in the local shape at all.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

from orcamgr.crest import shell
from orcamgr.crest.runner import write_crest_run_files


ETHANOL = "C 0.0 0.0 0.0\nO 1.4 0.0 0.0\nH -0.4 1.0 0.0"


def _script(tmp_path, transport, name="conf", crest_bin="/home/u/.local/bin/crest"):
    d = pathlib.Path(tmp_path) / name
    path = write_crest_run_files(d, name, ETHANOL, crest_bin,
                                 ["--gfn2", "-T", "4"], transport=transport)
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------- detection

def test_the_transport_is_read_from_the_platform_not_a_setting(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert shell.transport_kind() == "wsl" and not shell.is_local()
    monkeypatch.setattr(sys, "platform", "linux")
    assert shell.transport_kind() == "local" and shell.is_local()
    monkeypatch.setattr(sys, "platform", "darwin")
    assert shell.transport_kind() == "local"


def test_a_local_transport_has_exactly_one_target(monkeypatch):
    """The 'which distro' setting still round-trips locally -- there is just
    nothing to choose. A target of "" would make resolve_run_target's preferred
    ordering silently drop the only place CREST can run."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(shell, "available", lambda: True)
    assert shell.list_targets() == [shell.LOCAL_TARGET]
    monkeypatch.setattr(shell, "available", lambda: False)
    assert shell.list_targets() == []


def test_the_missing_message_names_what_is_missing_here(monkeypatch):
    """"Install WSL" is useless advice on a machine that has no WSL to install."""
    monkeypatch.setattr(sys, "platform", "linux")
    assert "bash" in shell.missing_message() and "WSL" not in shell.missing_message()
    monkeypatch.setattr(sys, "platform", "win32")
    assert "WSL" in shell.missing_message()


def test_a_transport_failure_is_data_not_an_exception(monkeypatch):
    """Same contract as wsl.run_bash: CrestRunner._liveness exists so a single
    transient failure cannot condemn a healthy multi-hour run, and an exception
    walks straight past that guard."""
    import subprocess
    monkeypatch.setattr(sys, "platform", "linux")

    def boom(*_a, **_kw):
        raise OSError(24, "Too many open files")

    monkeypatch.setattr(subprocess, "run", boom)
    rc, out, err = shell.run_bash(shell.LOCAL_TARGET, "true")
    assert rc != 0 and out == "" and "shell-error" in err
    assert not shell.is_missing(err)          # a hiccup is not "bash is gone"


def test_bash_missing_is_recognised_as_the_transport_being_absent(monkeypatch):
    import subprocess
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "run",
                        lambda *_a, **_kw: (_ for _ in ()).throw(FileNotFoundError()))
    _, _, err = shell.run_bash(shell.LOCAL_TARGET, "true")
    assert shell.is_missing(err)


# ------------------------------------------------------------- path naming

def test_a_windows_path_is_translated_only_for_wsl():
    """One expression, shared by the script and by launch(), so the two cannot
    disagree about where the calc folder is."""
    wsl_expr = shell.shell_path_expr(r"D:\work\job", transport="wsl")
    assert "wslpath -u" in wsl_expr and r"D:\work\job" in wsl_expr
    local_expr = shell.shell_path_expr("/home/u/work/job", transport="local")
    assert "wslpath" not in local_expr and "/home/u/work/job" in local_expr


def test_a_path_with_a_space_survives_both_transports():
    """The default workspace lives under the user profile, so an account named
    'John Smith' is the ordinary case, not the exotic one."""
    for kind in ("wsl", "local"):
        expr = shell.shell_path_expr("/home/John Smith/w", transport=kind)
        assert "'/home/John Smith/w'" in expr


# ------------------------------------------------------- the script itself

def test_the_wsl_script_stages_through_an_ext4_scratch_dir(tmp_path):
    s = _script(tmp_path, "wsl")
    assert 'SCRATCH="$HOME/.orcadesk/scratch/$NAME"' in s
    assert "wslpath -u" in s
    assert "cp \"$MNT/$NAME.xyz\" \"$SCRATCH/input.xyz\"" in s
    assert "for f in crest_conformers.xyz" in s          # copied back
    assert 'rm -rf "$SCRATCH"' in s                      # and cleaned up


def test_the_local_script_runs_in_the_calc_folder_and_never_deletes_it(tmp_path):
    """The one line that must not survive the port: locally SCRATCH would be the
    user's calc folder, so an `rm -rf` of it takes the results with it."""
    s = _script(tmp_path, "local")
    assert "SCRATCH" not in s
    assert "rm -rf" not in s
    assert "wslpath" not in s
    assert 'cd "$MNT"' in s
    assert 'INPUT="$NAME.xyz"' in s                      # nothing to copy in
    assert "cp " not in s                                # nor back out


def test_both_scripts_write_the_rc_marker_last(tmp_path):
    """monitor() stops on the .rc marker and a finished-while-closed run is
    judged from it, so it must appear only after the ensemble is in place --
    otherwise the parse races the copy-back."""
    for kind in ("wsl", "local"):
        s = _script(tmp_path / kind, kind)
        rc_at = s.index('echo "$rc" > "$MNT/$NAME.crest.rc"')
        assert s.index('"$CRESTBIN"') < rc_at
        if kind == "wsl":
            assert s.index("for f in crest_conformers.xyz") < rc_at


def test_both_scripts_record_pid_and_start_time_for_reattach(tmp_path):
    """(pid, create_time) is what survives a restart -- without the start time
    a recycled PID reads as the job still running."""
    for kind in ("wsl", "local"):
        s = _script(tmp_path / kind, kind)
        assert 'echo "$$" > "$MNT/$NAME.crest.pid"' in s
        assert "/proc/$$/stat" in s


def test_the_input_xyz_is_written_beside_the_script(tmp_path):
    d = tmp_path / "conf"
    write_crest_run_files(d, "conf", ETHANOL, "/usr/bin/crest", ["--gfn2"],
                          transport="local")
    assert (d / "conf.xyz").read_text(encoding="utf-8").startswith("3\n")


def test_the_script_is_written_with_lf_endings(tmp_path):
    """bash chokes on CRLF, and the file is generated on Windows for WSL."""
    d = tmp_path / "conf"
    path = write_crest_run_files(d, "conf", ETHANOL, "/usr/bin/crest", ["--gfn2"],
                                 transport="wsl")
    assert b"\r\n" not in path.read_bytes()


def test_crest_flags_are_quoted_into_the_script(tmp_path):
    d = tmp_path / "conf"
    path = write_crest_run_files(d, "conf", ETHANOL, "/usr/bin/crest",
                                 ["--alpb", "ethyl acetate"], transport="local")
    assert "'ethyl acetate'" in path.read_text(encoding="utf-8")


# ------------------------------------------------------------ env wording

def test_env_messages_follow_the_transport(monkeypatch):
    from orcamgr.crest import env
    monkeypatch.setattr(sys, "platform", "linux")
    assert "machine" in env.not_installed_message()
    monkeypatch.setattr(sys, "platform", "win32")
    assert "WSL" in env.not_installed_message()


def test_aggregate_status_reports_the_transport(monkeypatch):
    from orcamgr.crest import env
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(env, "shell_available", lambda: True)
    st = env.aggregate_status([{"distro": "local", "ready": True}])
    assert st["transport"] == "local" and st["wsl"] is True and st["state"] == "ready"
    st = env.aggregate_status([])
    assert st["state"] == "unset" and st["transport"] == "local"


def test_the_macos_installer_refuses_instead_of_fetching_a_linux_binary(monkeypatch):
    """The published asset is an Ubuntu build; installing it on macOS would
    produce a binary that cannot run, and the failure would land at the first
    launch rather than at the click."""
    from orcamgr.crest import installer
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    res = installer.install_crest("local")
    assert res["ok"] is False and "macOS" in res["error"]


@pytest.mark.skipif(sys.platform.startswith("win"),
                    reason="no local bash transport on Windows")
def test_a_real_local_bash_round_trips():
    assert shell.available()
    rc, out, _ = shell.run_bash(shell.LOCAL_TARGET, "echo hello")
    assert rc == 0 and out.strip() == "hello"
