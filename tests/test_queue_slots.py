"""The queue editor's Bridge slots as JSON envelopes (gui/bridge.py).

The slots are thin adapters over QueueStore / inpsets, so they are called
unbound on a stub ``self`` (the precedent is test_log_replay): no Qt
application, no channel, no MLIP/CREST probes. What is pinned here is the
wire contract the front-end codes against — the keys, op_index, the per-file
refusals — not the rules themselves (test_queue_ops / test_inpsets).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import orcamgr.state.inpsets as inpsets_mod
import orcamgr.state.workflows as workflows_mod
import orcamgr.state.store as store_mod
from orcamgr.gui.bridge import Bridge
from orcamgr.state.store import QueueStore

H2 = "H 0.0 0.0 0.0\nH 0.0 0.0 0.74"


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    monkeypatch.setattr(store_mod, "user_data_root", lambda: tmp_path)
    monkeypatch.setattr(inpsets_mod, "user_data_root", lambda: tmp_path)
    monkeypatch.setattr(workflows_mod, "user_data_root", lambda: tmp_path)
    stub = SimpleNamespace(
        store=QueueStore(),
        settings=SimpleNamespace(default_nprocs=4, default_maxcore_mb=2000),
        window=SimpleNamespace(),
    )
    stub._inpset_defaults = lambda: Bridge._inpset_defaults(stub)
    stub._accepted = lambda calc: Bridge._accepted(stub, calc)
    stub._preview_hess = lambda *a: Bridge._preview_hess(stub, *a)
    stub._holds_output = lambda name: Bridge._holds_output(stub, name)
    stub._HESS_KINDS = Bridge._HESS_KINDS
    return stub


def call(stub, slot, *args):
    return json.loads(getattr(Bridge, slot)(stub, *args))


def test_inp_set_slots(bridge):
    sets = call(bridge, "get_inp_sets")
    assert sets["ok"] and [s["kind"] for s in sets["sets"]] == ["ORCA", "MLIP", "CREST"]
    assert "%pal nprocs 4 end" in next(s for s in sets["sets"] if s["kind"] == "ORCA")["text"]
    assert set(sets["defaults"]) == {"ORCA", "MLIP", "CREST"}
    assert "%pal nprocs 4 end" in sets["defaults"]["ORCA"]
    saved = call(bridge, "save_inp_set", json.dumps(
        {"name": "mine", "kind": "ORCA", "text": "! {{JOB}}\n* xyz 0 1\n{{GEOMETRY}}\n*\n"}))
    assert saved["ok"] and saved["set"]["id"]
    bad = call(bridge, "save_inp_set", json.dumps({"name": "x", "kind": "ORCA", "text": "! {{JOB}}"}))
    assert bad["ok"] is False and "GEOMETRY" in bad["error"]
    assert call(bridge, "delete_inp_set", saved["set"]["id"]) == {"ok": True}
    assert call(bridge, "delete_inp_set", "nope")["ok"] is False


def test_apply_queue_ops_envelope(bridge):
    ops = [{"op": "add_system", "system": {"id": "s1", "name": "mol",
                                           "inputs": [{"label": "a", "xyz": H2}]}},
           {"op": "add_calc", "calc": {"name": "mol-sp", "kind": "sp", "xyz": H2,
                                       "system": "s1"}}]
    ok = call(bridge, "apply_queue_ops", json.dumps(ops))
    assert ok["ok"] and ok["snapshot"]["systems"][0]["name"] == "mol"
    refused = call(bridge, "apply_queue_ops", json.dumps(
        [{"op": "remove_calc", "name": "mol-sp"}, {"op": "remove_calc", "name": "zzz"}]))
    assert refused == {"ok": False, "error": "No calculation named 'zzz'.", "op_index": 1}
    assert bridge.store.names() == ["mol-sp"]       # the first op did not land
    assert call(bridge, "apply_queue_ops", "{not json")["op_index"] == -1
    assert call(bridge, "apply_queue_ops", json.dumps({"op": "order"}))["ok"] is False


def test_get_system_slot(bridge):
    call(bridge, "apply_queue_ops", json.dumps(
        [{"op": "add_system", "system": {"id": "s1", "name": "mol",
                                         "inputs": [{"label": "a", "xyz": H2}]}}]))
    got = call(bridge, "get_system", "s1")
    assert got["ok"] and got["system"]["inputs"][0]["xyz"] == H2
    assert call(bridge, "get_system", "s9")["ok"] is False


def test_workflow_slots_save_list_and_delete(bridge):
    step = {"id": "c0", "node": "n0", "kind": "opt", "raw_text": "", "config": {}}
    assert call(bridge, "get_workflows") == {"ok": True, "workflows": []}
    saved = call(bridge, "save_workflow", json.dumps({"name": "Opt", "steps": [step]}))
    assert saved["ok"] and saved["workflow"]["id"] and saved["workflow"]["steps"] == [step]
    assert [w["name"] for w in call(bridge, "get_workflows")["workflows"]] == ["Opt"]
    clash = call(bridge, "save_workflow", json.dumps({"name": "opt", "steps": [step]}))
    assert clash["ok"] is False and "already a workflow" in clash["error"]
    assert call(bridge, "save_workflow", "not json")["ok"] is False
    assert call(bridge, "delete_workflow", saved["workflow"]["id"]) == {"ok": True}
    gone = call(bridge, "delete_workflow", saved["workflow"]["id"])
    assert gone["ok"] is False and "No workflow" in gone["error"]


def test_irc_structures_lists_an_irc_runs_files_only_once_it_is_done(bridge, tmp_path):
    """get_irc_structures: the ends and paths ORCA wrote in the IRC's run
    folder, in the editor's order -- and none while the IRC is not DONE (a
    re-queued IRC's folder still holds the last run's files)."""
    from orcamgr.core.queue import CalcState
    run = tmp_path / "ws" / "ts-irc"
    run.mkdir(parents=True)
    frame = "2\nE -1.0\nH 0 0 0\nH 0 0 0.74\n"
    for suffix, frames in (("_IRC_F.xyz", 1), ("_IRC_B.xyz", 1), ("_IRC_Full_trj.xyz", 3)):
        (run / f"ts-irc{suffix}").write_text(frame * frames, encoding="utf-8")
    bridge.settings.workspace_root = str(tmp_path / "ws")
    bridge._calc_run_dir = lambda name: Bridge._calc_run_dir(bridge, name)
    bridge._IRC_FILES = Bridge._IRC_FILES
    call(bridge, "apply_queue_ops", json.dumps([
        {"op": "add_calc", "calc": {"name": "ts-irc", "kind": "irc", "xyz": H2}},
        {"op": "add_calc", "calc": {"name": "ts-opt", "kind": "opt", "xyz": H2}}]))
    pending = call(bridge, "get_irc_structures", "ts-irc")
    assert pending == {"ok": True, "done": False, "files": []}
    bridge.store.get("ts-irc").state = CalcState.DONE
    got = call(bridge, "get_irc_structures", "ts-irc")
    assert got["ok"] and got["done"]
    assert [(f["key"], f["label"], f["count"]) for f in got["files"]] == [
        ("F", "Forward end", 1), ("B", "Backward end", 1), ("Full_trj", "Whole path", 3)]
    assert got["files"][2]["path"] == str(run / "ts-irc_IRC_Full_trj.xyz")
    assert call(bridge, "get_irc_structures", "ts-opt")["ok"] is False      # not an IRC
    assert call(bridge, "get_irc_structures", "nope")["ok"] is False


def test_open_queue_window_slot(bridge):
    assert call(bridge, "open_queue_window")["ok"] is False      # no queue window
    shown = []
    bridge.window.show_queue_window = lambda: shown.append(True)
    assert call(bridge, "open_queue_window") == {"ok": True}
    assert shown == [True]


def test_the_guided_update_calc_keeps_a_lane_calcs_graph_fields(bridge):
    call(bridge, "apply_queue_ops", json.dumps([
        {"op": "add_system", "system": {"id": "s1", "name": "mol"}},
        {"op": "add_calc", "calc": {"name": "mol-opt", "kind": "opt", "xyz": H2,
                                    "system": "s1"}},
        {"op": "add_calc", "calc": {"name": "mol-sp", "kind": "sp", "xyz": H2,
                                    "system": "s1", "node": "n2", "col": 1,
                                    "after": "mol-opt", "gate": "approval"}}]))
    # the builder's payload knows nothing about lanes
    out = call(bridge, "update_calc", "mol-sp", json.dumps(
        {"name": "mol-sp", "kind": "sp", "xyz": H2, "charge": 1}))
    assert out["ok"]
    sp = bridge.store.get("mol-sp")
    assert (sp.charge, sp.system, sp.node, sp.col, sp.after, sp.gate) == (
        1, "s1", "n2", 1, "mol-opt", "approval")
    # ...and a payload that names a field still sets it
    call(bridge, "update_calc", "mol-sp", json.dumps(
        {"name": "mol-sp", "kind": "sp", "xyz": H2, "gate": "auto"}))
    assert bridge.store.get("mol-sp").gate == "auto"


def test_inp_sets_carry_the_job_map(bridge):
    sets = call(bridge, "get_inp_sets")
    assert sets["jobs"]["opt"] == "TightOpt" and sets["jobs"]["ts_opt_freq"] == "OptTS Freq"
    # TD-DFT and NEB-TS run from a level too (the block's options carry their
    # %tddft / %neb settings); a General input is the user's alone
    assert sets["jobs"]["neb_ts"] == "NEB-TS" and sets["jobs"]["tddft"] == ""
    assert "general" not in sets["jobs"]
    assert sets["mlip_jobs"] == ["mlip_opt", "mlip_sp", "mlip_freq", "mlip_opt_freq"]
    # a CREST set's block runs the conformer search, and an SP follows it
    assert sets["crest_jobs"] == ["crest_conf"]
    assert sets["next_kind"]["crest_conf"] == "sp"


def test_the_preview_renders_a_level_block_for_its_kind(bridge, tmp_path):
    bridge.settings.workspace_root = str(tmp_path)
    level = next(s for s in call(bridge, "get_inp_sets")["sets"] if s["kind"] == "ORCA")
    calc = {"name": "mol-ts", "kind": "ts_opt", "is_raw": True, "raw_text": level["text"],
            "geometry_source": "direct", "xyz": H2, "charge": -1, "multiplicity": 2}
    pv = call(bridge, "build_inp_preview", json.dumps(calc))
    assert pv["ok"] and pv["text"].startswith("! wB97X-D4 def2-TZVP OptTS\n")
    assert "Calc_Hess true" in pv["text"] and "* xyz -1 2\n" + H2 in pv["text"]
    # an IRC after a frequency that has not run yet reads the .hess it will leave
    call(bridge, "apply_queue_ops", json.dumps([{"op": "add_calc", "calc": {
        "name": "mol-freq", "kind": "ts_freq", "xyz": H2}}]))
    irc = dict(calc, name="mol-irc", kind="irc", after="mol-freq",
               geometry_source="reference", ref_name="mol-freq", xyz="")
    pv = call(bridge, "build_inp_preview", json.dumps(irc))
    assert 'InitHess read\n  Hess_Filename "mol-freq.hess"' in pv["text"]
    assert "{{GEOMETRY}}" in pv["text"]               # a reference: resolved at run time
    # after nothing that leaves a Hessian, it computes its own
    pv = call(bridge, "build_inp_preview", json.dumps(dict(irc, after="", ref_name="")))
    assert "InitHess calc_anfreq" in pv["text"]
    bad = call(bridge, "build_inp_preview", json.dumps(dict(calc, kind="general")))
    assert bad["ok"] is False and "cannot run as" in bad["error"]


def test_read_xyz_paths(bridge, tmp_path):
    a, multi = tmp_path / "water.xyz", tmp_path / "traj.xyz"
    frame = "2\n\n" + H2 + "\n"
    a.write_text(frame, encoding="utf-8")
    multi.write_text(frame + frame, encoding="utf-8")
    out = call(bridge, "read_xyz_paths", json.dumps([str(a), str(multi), str(tmp_path / "gone.xyz")]))
    assert out["ok"] and [f["name"] for f in out["files"]] == ["water", "traj", "gone"]
    assert out["files"][0]["xyz"] == H2 and out["files"][0]["error"] == ""
    assert "2 structures" in out["files"][1]["error"] and out["files"][1]["xyz"] == ""
    assert out["files"][2]["error"]
    assert (out["files"][0]["charge"], out["files"][0]["multiplicity"]) == (None, None)
    assert call(bridge, "read_xyz_paths", "{}")["ok"] is False
