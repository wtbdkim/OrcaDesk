"""Opt-in: a REAL CREST search on a remote Linux device, through the remote
CREST runner (the path the queue engine takes for a crest_conf placed on a
device). Proves what the fake transport cannot: the device probe finds CREST
and asks THAT binary whether its --cinp parser takes "force constant=" /
"reference=", the setsid launch of `crest`, the 10 s sync, the whole-folder
fetch + verify + guarded delete, and that the local CREST result path reads
the fetched folder.

Runs in its own remote workdir (~/orcadesk-smoke-crest), never the app's
~/orcadesk-runs. Needs CREST on the device (ORCAdesk's installer layout,
~/.local/bin/crest, or on PATH).

    ORCADESK_SMOKE_REMOTE=aws-orca python -m pytest tests/smoke/test_remote_crest_smoke.py -v

About 1-2 minutes (ethanol, --quick, -T 4).
"""
from __future__ import annotations

import os
import time

import pytest

from orcamgr.core.input_generator import StepConfig
from orcamgr.crest.parser import parse_crest_result
from orcamgr.crest.runner import build_crest_argv, write_crest_run_files
from orcamgr.remote.devices import probe
from orcamgr.remote.runner import RemoteCrestRunner, launch_stamp, remote_dir_for
from orcamgr.remote.ssh import SshTransport

ALIAS = os.environ.get("ORCADESK_SMOKE_REMOTE", "").strip()
pytestmark = pytest.mark.skipif(not ALIAS, reason="set ORCADESK_SMOKE_REMOTE=<ssh alias>")

ETHANOL = ("C 1.275 -0.252 -0.016\nC -0.015 0.561 0.033\nO -1.169 -0.242 -0.056\n"
           "H 1.303 -0.853 -0.921\nH 2.134 0.413 -0.007\nH 1.340 -0.916 0.845\n"
           "H -0.040 1.164 0.953\nH -0.068 1.238 -0.823\nH -1.139 -0.902 0.645")


def test_a_constrained_search_runs_on_the_device_and_reads_here(tmp_path):
    t = SshTransport()
    info = probe(t, ALIAS, workdir="~/orcadesk-smoke-crest")
    assert info.usable, info.message
    if not info.crest_path:
        pytest.skip(f"no CREST on {ALIAS}")

    cons = [{"kind": "C", "atoms": [0], "value": None},
            {"kind": "C", "atoms": [8], "value": None}]
    cfg = StepConfig.from_dict({"kind": "crest_conf", "crest_preset": "quick",
                                "crest_threads": 4, "geom_constraints": cons,
                                "crest_force_constant": 0.5})
    argv = build_crest_argv(cfg, 0, 1)
    d = tmp_path / "smoke" / "eth"
    write_crest_run_files(d, "eth", ETHANOL, info.crest_path, argv, transport="local",
                          constraints=cfg.geom_constraints, extended=info.crest_extended,
                          force_constant=cfg.crest_force_constant)
    r = RemoteCrestRunner(t, ALIAS, info.crest_path, info.workdir,
                          remote_dir_for(info.workdir, "smoke/eth", launch_stamp()),
                          flags=argv)
    t0 = time.monotonic()
    r.launch(d / "eth.xyz", d / "eth.out")
    r.monitor(d / "eth.out")
    took = time.monotonic() - t0

    res = parse_crest_result(str(d / "eth.out"))
    assert res.terminated_normally, res.error_message
    assert len(res.conformers) >= 1 and res.final_energy_eh < 0
    out = (d / "eth.out").read_text(errors="replace")
    if info.crest_extended:
        # the device's own CREST took the lines: they are in its echo of the file
        assert "force constant=0.5" in out and "reference=coord.ref" in out
    gone = t.run(ALIAS, f"[ -e '{r.remote_dir}' ] && echo left || echo gone").out.strip()
    assert gone == "gone", "remote folder left behind"
    print(f"\n{ALIAS}: {len(res.conformers)} conformer(s), {took:.0f} s, "
          f"CREST {info.crest_version!r}, extended={info.crest_extended}")
