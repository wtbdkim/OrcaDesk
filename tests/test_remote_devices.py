"""Remote devices: which exist (~/.ssh/config), what the probe says about
each, and the shape of their settings (orcamgr/remote/devices.py, ssh.py).
Nothing here opens a connection -- the transport is faked."""
from __future__ import annotations

import pytest

from orcamgr.remote import devices
from orcamgr.remote.devices import (DeviceMonitor, interpret_probe, normalize_device,
                                    normalize_devices, parse_ssh_config, probe,
                                    probe_script, resolve_device)
from orcamgr.remote.ssh import RC_SSH, RC_TIMEOUT, RunResult, sftp_quote, valid_alias

from fake_transport import FakeTransport

_READY = """os=Linux
home=/home/u
cores=32
mem_total_kb=131072000
mem_avail_kb=120000000
orca=/home/u/orca_6_1_1_linux_x86-64_shared_openmpi418/orca
mpirun=/usr/bin/mpirun
setsid=/usr/bin/setsid
workdir=/home/u/orcadesk-runs
free_kb=500000000
"""


# ---- aliases ------------------------------------------------------------------

@pytest.mark.parametrize("alias,ok", [
    ("gcp-orca", True), ("vm1.example", True), ("a_b", True),
    ("-oProxyCommand=x", False), ("", False), ("two words", False), ("x;rm", False),
])
def test_an_alias_that_ssh_could_read_as_an_option_is_refused(alias, ok):
    assert valid_alias(alias) is ok


def test_the_ssh_config_lists_concrete_hosts_and_follows_includes(tmp_path):
    (tmp_path / "conf.d").mkdir()
    (tmp_path / "conf.d" / "work").write_text("Host cluster\n  HostName c.example\n")
    cfg = tmp_path / "config"
    cfg.write_text(
        "# comment\n"
        "Include conf.d/*\n"
        "Host gcp-orca aws1\n  HostName 1.2.3.4\n"
        "Host *\n  ServerAliveInterval 30\n"
        "Host !bad web?\n"
        "Host=home-pc\n"
        "Host -oProxyCommand=evil\n"
        "Host gcp-orca\n")
    assert parse_ssh_config(cfg) == ["cluster", "gcp-orca", "aws1", "home-pc"]


def test_a_missing_ssh_config_is_no_devices(tmp_path):
    assert parse_ssh_config(tmp_path / "nope") == []


def test_include_cycles_end(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text(f"Include {cfg}\nHost a\n")
    assert parse_ssh_config(cfg) == ["a"]


# ---- the probe ------------------------------------------------------------------

def test_a_ready_linux_device_reads_in_full():
    info = interpret_probe("gcp", RunResult(0, _READY))
    assert info.state == "ready" and info.usable
    assert info.cores == 32 and info.mem_total_mb == 128000
    assert info.orca_version == "6.1.1"           # from the tarball's folder name
    assert info.has_mpi and info.has_setsid
    assert info.workdir == "/home/u/orcadesk-runs"


@pytest.mark.parametrize("err,rc,needle", [
    ("Host key verification failed.", RC_SSH, "has not confirmed the device"),
    ("Permission denied (publickey).", RC_SSH, "refused the key"),
    ("ssh: connect to host 1.2.3.4 port 22: Connection timed out", RC_SSH, "switched on"),
    ("timeout", RC_TIMEOUT, "switched on"),
    ("ssh-not-found", 127, "OpenSSH client"),
])
def test_a_failed_probe_says_what_to_do_in_plain_words(err, rc, needle):
    info = interpret_probe("gcp", RunResult(rc, "", err))
    assert info.state == "error" and needle in info.message


def test_a_device_that_is_not_linux_or_has_no_orca_is_unusable():
    assert "Only Linux" in interpret_probe("m", RunResult(0, _READY.replace("os=Linux", "os=Darwin"))).message
    no_orca = interpret_probe("m", RunResult(0, _READY.replace(
        "orca=/home/u/orca_6_1_1_linux_x86-64_shared_openmpi418/orca", "orca=")))
    assert no_orca.state == "error" and "ORCA was not found" in no_orca.message
    no_setsid = interpret_probe("m", RunResult(0, _READY.replace("setsid=/usr/bin/setsid", "setsid=")))
    assert "setsid" in no_setsid.message


def test_the_probe_script_quotes_what_the_user_typed():
    s = probe_script("~/runs; rm -rf /", "/opt/orca x/orca")
    assert "'~/runs; rm -rf /'" in s
    assert "'/opt/orca x/orca'" in s


def test_probe_goes_through_the_transport():
    fake = FakeTransport()
    fake.probe_out = _READY
    assert probe(fake, "gcp").state == "ready"
    assert probe(fake, "-bad").state == "error"


def test_the_monitor_keeps_the_latest_probe_and_reports_ready_devices():
    fake = FakeTransport()
    fake.probe_out = _READY
    ready = []
    mon = DeviceMonitor(fake, on_ready=lambda a, i: ready.append(a))
    mon.refresh([{"alias": "gcp"}], wait=True)
    assert mon.get("gcp").state == "ready" and ready == ["gcp"]
    fake.reachable = False
    mon.refresh([{"alias": "gcp"}], wait=True)
    assert mon.get("gcp").state == "error" and ready == ["gcp"]


# ---- settings ---------------------------------------------------------------------

def test_a_device_entry_is_coerced_to_its_exact_shape():
    d = normalize_device({"alias": "gcp", "enabled": "true", "max_jobs": -3,
                          "cores": "8", "ram_mb": 10**12, "orca_path": 5, "workdir": ""})
    assert d == {"alias": "gcp", "enabled": False, "max_jobs": 0, "cores": 8,
                 "ram_mb": 4_000_000, "orca_path": "", "workdir": devices.DEFAULT_WORKDIR}
    assert normalize_device({"alias": "-x"}) is None
    assert normalize_devices([{"alias": "a"}, {"alias": "a"}, "junk", None]) == [
        normalize_device({"alias": "a"})]
    assert normalize_devices("not a list") == []


def test_settings_load_degrades_a_bad_remote_devices_value(tmp_path, monkeypatch):
    import json
    from orcamgr import config
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"remote_devices": {"alias": "x"}, "orca_path": "o",
                             "workspace_root": str(tmp_path)}))
    monkeypatch.setattr(config, "config_file", lambda: f)
    assert config.Settings.load().remote_devices == []


def test_an_enabled_device_resolves_to_a_budget_from_its_caps_or_its_probe():
    info = interpret_probe("gcp", RunResult(0, _READY))
    auto = resolve_device(normalize_device({"alias": "gcp", "enabled": True}), info)
    assert auto.online and auto.budget.cores == 32 and auto.budget.max_jobs == 32
    assert auto.budget.ram_mb == int(128000 * 0.75)
    capped = resolve_device(normalize_device({"alias": "gcp", "enabled": True, "cores": 8,
                                              "ram_mb": 16000, "max_jobs": 2}), info)
    assert (capped.budget.cores, capped.budget.ram_mb, capped.budget.max_jobs) == (8, 16000, 2)
    never = resolve_device(normalize_device({"alias": "gcp", "enabled": True}), None)
    assert not never.online


def test_sftp_paths_are_quoted_for_its_batch_parser():
    assert sftp_quote('a "b"\\c') == '"a \\"b\\"\\\\c"'
