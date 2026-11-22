"""Per-system run folders (core/rundir.py).

A calculation in a system runs in {workspace}/{system name}/{name}/, a
standalone one in {workspace}/{name}/. What is pinned here:

* the layout, for every backend (ORCA, MLIP, CREST);
* the folder is recorded when a calc first starts (Calculation.run_dir) and
  persisted; from then on it wins: renaming the system or moving the block to
  another row never moves a calc that has run, while one that has not follows
  the system's current name;
* a session from before the change: a calc that ran did so in
  {workspace}/{name}/, and is found (and re-run) there;
* a parent's files (geometry, .hess, IRC end points) are read from the
  parent's own folder, in whatever system it is;
* keep-existing and the overwrite conflicts look where the calc runs;
* a system name that is not a safe folder is refused, never mapped;
* reattach after a restart tails the recorded folder;
* a CANCELLED re-run and a rerun_calc run again where they ran;
* no client can set the recorded folder (calc_from_dict ignores it; the
  session restore checks it);
* a folder the user moved by hand is found again by name and recorded where
  it is now (relocate_moved) -- never a guess between two, never a running job,
  never a move of the app's own.

Workspaces are tmp_path folders; nothing launches ORCA (a fake runner writes
the output a converged run would). A test that starts a run stops it in a
finally.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import orcamgr.core.queue as queue_mod
import orcamgr.state.store as store_mod
from orcamgr.core import rundir
from orcamgr.core.input_generator import StepConfig
from orcamgr.core.queue import (CalcState, Calculation, GeometrySource,
                                QueueCallbacks, QueueEngine, find_parent_hessian)
from orcamgr.core.runner import OrcaRunError
from orcamgr.gui.bridge import Bridge
from orcamgr.state.store import (QueueOpError, QueueStore, calc_from_dict,
                                 calc_from_session_dict, calc_to_session_dict,
                                 make_engine_factory, system_from_dict)

H2 = "H 0.0 0.0 0.0\nH 0.0 0.0 0.74"

# What a converged ORCA 6.1.1 optimization of H2 leaves (the markers the
# parser reads): it validates as a DONE opt and carries a final geometry.
OPT_OUT = """\
                                 * O   R   C   A *

                         Program Version 6.1.1  - RELEASE

================================================================================
                                       INPUT FILE
================================================================================
NAME = job.inp
|  1> ! HF def2-SVP Opt
|  2> * xyz 0 1
|  3> H   0.000000   0.000000   0.000000
|  4> H   0.000000   0.000000   0.740000
|  5> *
|  6> ****END OF INPUT****

                    ***********************HURRAY********************
                    ***        THE OPTIMIZATION HAS CONVERGED     ***
                    *************************************************

---------------------------------
CARTESIAN COORDINATES (ANGSTROEM)
---------------------------------
  H      0.000000    0.000000    0.000000
  H      0.000000    0.000000    0.740000

-------------------------   --------------------
FINAL SINGLE POINT ENERGY        -1.170000000000
-------------------------   --------------------

                             ****ORCA TERMINATED NORMALLY****
"""


# ---- fixtures / helpers --------------------------------------------------------

@pytest.fixture
def data(monkeypatch, tmp_path):
    """The user data folder (session.json) -- the store module's own binding."""
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(store_mod, "user_data_root", lambda: root)
    return root


@pytest.fixture
def store(data):
    return QueueStore()


@pytest.fixture
def ws(tmp_path):
    return tmp_path / "ws"


def add_system(store, sid="s1", name="glycine"):
    store.apply_ops([{"op": "add_system", "system": {"id": sid, "name": name}}])


def opt(name, system="", **kw):
    d = {"name": name, "kind": "opt", "xyz": H2, "system": system,
         "config": {"kind": "opt", "functional": "HF", "basis_set": "def2-SVP",
                    "nprocs": 1}}
    d.update(kw)
    return d


def add(store, *calcs):
    store.apply_ops([{"op": "add_calc", "calc": c} for c in calcs])


class FakeOrca:
    """Stands in for OrcaRunner: launch records the input it was given,
    monitor writes the output a converged run leaves there."""
    launched: "list[Path]" = []
    tailed: "list[Path]" = []
    fail_launch = False

    def launch(self, inp_path, out_path):
        if FakeOrca.fail_launch:
            raise OrcaRunError("could not start ORCA (test)")
        FakeOrca.launched.append(Path(inp_path))
        return 4242, 1.0

    def adopt(self, pid, create_time):
        return None

    def end_position(self, out_path):
        return 0

    def monitor(self, out_path, on_line=None, start_pos=0, on_fatal=None):
        FakeOrca.tailed.append(Path(out_path))
        Path(out_path).write_text(OPT_OUT, encoding="utf-8")

    def cancel(self):
        pass

    def detach(self):
        pass


@pytest.fixture(autouse=True)
def _fresh_fake():
    FakeOrca.launched, FakeOrca.tailed, FakeOrca.fail_launch = [], [], False
    yield
    FakeOrca.fail_launch = False


def run(store, ws, keep=()):
    """One run of the whole queue through the store, as Run does, with the
    fake ORCA. Stopped in a finally, whatever happens."""
    base = make_engine_factory(store, "orca-not-needed", str(ws), set(keep))

    def factory():
        eng = base()
        eng._orca_runner_factory = FakeOrca
        return eng

    try:
        store.start_run(factory)
        assert store.wait_for_run(15)
    finally:
        store.cancel_run()
        store.wait_for_run(15)


def bridge(store, ws):
    """A Bridge stand-in with the methods the folder slots use."""
    stub = SimpleNamespace(store=store, settings=SimpleNamespace(workspace_root=str(ws)))
    for m in ("_calc_run_dir", "_result_file", "_holds_output", "show_path_in_folder",
              "_preview_hess", "_overwrite_conflicts"):
        setattr(stub, m, (lambda f: lambda *a: f(stub, *a))(getattr(Bridge, m)))
    stub._IRC_FILES = Bridge._IRC_FILES
    stub._HESS_KINDS = Bridge._HESS_KINDS
    return stub


def call(stub, slot, *args):
    return json.loads(getattr(Bridge, slot)(stub, *args))


def write_out(folder: Path, name: str, text: str = OPT_OUT) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{name}.out"
    out.write_text(text, encoding="utf-8")
    return out


def write_session(data: Path, schema: int, calcs: list, systems: list) -> None:
    (data / "session.json").write_text(json.dumps(
        {"schema": schema, "calculations": calcs, "systems": systems}), encoding="utf-8")


def session_calc(name, **kw):
    d = calc_to_session_dict(calc_from_dict(opt(name)))
    d.pop("run_dir", None)          # an older session never wrote one
    d.update(kw)
    return d


# ---- the helper --------------------------------------------------------------

def test_the_planned_folder_is_the_system_folder_or_the_root():
    assert rundir.planned_run_dir("glycine_Opt", "glycine") == "glycine/glycine_Opt"
    assert rundir.planned_run_dir("water", "") == "water"
    c = Calculation(name="x", kind="sp", config=StepConfig(kind="sp"))
    assert rundir.run_folder("W", c, "sys") == Path("W") / "sys" / "x"
    c.run_dir = "old/x"                                   # recorded wins
    assert rundir.run_folder("W", c, "sys") == Path("W") / "old" / "x"
    c.output_path = str(Path("Z") / "x" / "x.out")        # and output_path for reads
    assert rundir.result_folder("W", c, "sys") == Path("Z") / "x"
    assert rundir.run_folder("W", c, "sys") == Path("W") / "old" / "x"


@pytest.mark.parametrize("value, ok", [
    ("x", True), ("sys/x", True), ("my mol/x", True),
    # a folder the user sorted by hand, up to MAX_FOLDER_DEPTH folders deep
    ("a/b/x", True), ("a/b/c/d/x", True), ("a/b/c/d/e/x", False),
    ("../x", False), ("sys/../x", False), ("/x", False), ("C:/x", False),
    ("C:\\sys\\x", False), ("a//x", False), ("sys/y", False), ("sys/", False),
    ("con/x", False), ("sys./x", False), ("", False), (None, False), (7, False),
])
def test_a_restored_folder_is_inside_the_workspace_and_named_after_the_calc(value, ok):
    assert rundir.restored_run_dir(value, "x") == (value if ok else "")


def test_a_client_cannot_set_the_recorded_folder(store, ws):
    c = calc_from_dict(opt("evil", run_dir="../../elsewhere/evil"))
    assert c.run_dir == ""
    add(store, opt("sneaky", run_dir="C:/Windows/sneaky"))
    assert store.get("sneaky").run_dir == ""
    d = calc_to_session_dict(c)
    d["run_dir"] = "../../elsewhere/evil"
    assert calc_from_session_dict(d).run_dir == ""         # restore checks it


# ---- the layout, recorded at first start --------------------------------------

def test_a_system_calc_runs_in_its_system_folder_a_standalone_one_in_the_root(store, ws, data):
    add_system(store, "s1", "glycine")
    add(store, opt("glycine_Opt", system="s1"), opt("water"))
    run(store, ws)

    g, w = store.get("glycine_Opt"), store.get("water")
    assert (g.state, w.state) == (CalcState.DONE, CalcState.DONE)
    assert (ws / "glycine" / "glycine_Opt" / "glycine_Opt.inp").is_file()
    assert Path(g.output_path) == ws / "glycine" / "glycine_Opt" / "glycine_Opt.out"
    assert (ws / "water" / "water.inp").is_file()
    assert not (ws / "glycine_Opt").exists()
    # recorded, and persisted with the rest of the calc
    assert (g.run_dir, w.run_dir) == ("glycine/glycine_Opt", "water")
    saved = json.loads((data / "session.json").read_text(encoding="utf-8"))
    assert saved["schema"] == 3
    assert {c["name"]: c["run_dir"] for c in saved["calculations"]} == {
        "glycine_Opt": "glycine/glycine_Opt", "water": "water"}


def test_the_folder_is_recorded_when_the_calc_starts_even_if_it_then_fails(store, ws):
    add_system(store, "s1", "mol")
    add(store, opt("mol_Opt", system="s1"))
    FakeOrca.fail_launch = True
    run(store, ws)
    c = store.get("mol_Opt")
    assert c.state == CalcState.FAILED
    assert c.run_dir == "mol/mol_Opt"
    assert (ws / "mol" / "mol_Opt" / "mol_Opt.inp").is_file()


def test_a_calc_blocked_before_anything_ran_has_no_folder_yet(store, ws):
    """BLOCKED by a pre-launch check (here: the IRC never ran forward, so it
    has no forward end point) -- nothing ran, so nothing is recorded, and a
    rename still takes it along."""
    add_system(store, "s1", "ts")
    store.apply_ops([{"op": "add_calc", "calc": {"name": "ts_IRC", "kind": "irc",
                                                 "xyz": H2, "system": "s1"}}])
    irc = store.get("ts_IRC")
    irc.state, irc.run_dir = CalcState.DONE, "ts/ts_IRC"
    irc.output_path = str(write_out(ws / "ts" / "ts_IRC", "ts_IRC"))
    add(store, opt("ts_F", system="s1", geometry_source="reference", ref_name="ts_IRC",
                   ref_point="irc_f", after="ts_IRC", xyz=""))
    run(store, ws)
    f = store.get("ts_F")
    assert f.state == CalcState.BLOCKED and "forward" in f.message
    assert f.run_dir == ""
    store.apply_ops([{"op": "update_system", "id": "s1", "patch": {"name": "tsx"}}])
    assert store.names() == ["ts_IRC", "tsx_F"]


def test_an_mlip_and_a_crest_calc_run_in_their_system_folder(store, ws, monkeypatch):
    import orcamgr.crest.env as crest_env_mod
    import orcamgr.crest.runner as crest_runner_mod
    import orcamgr.mlip.runner as mlip_runner_mod

    class FakeMlip:
        def __init__(self, python):
            pass

        def run(self, script, args, out_path, cwd=None, on_line=None):
            cfg = json.loads(Path(args[0]).read_text(encoding="utf-8"))
            Path(cfg["result_json"]).write_text(json.dumps({
                "task": "opt", "converged": True, "energy_ev": -27.2, "n_steps": 1,
                "model": "", "geometry": [["H", 0, 0, 0], ["H", 0, 0, 0.74]],
                "error": None}), encoding="utf-8")
            return 0

        def cancel(self):
            pass

        def detach(self):
            pass

    class FakeCrest:
        def __init__(self, distro):
            pass

        def launch(self, script_path, name, out_path):
            out_path = Path(out_path)
            (out_path.parent / "crest_conformers.xyz").write_text(
                "1\n -5.0\n H 0 0 0\n", encoding="utf-8")
            out_path.write_text(" CREST terminated normally.\n", encoding="utf-8")
            (out_path.parent / f"{name}.crest.rc").write_text("0\n", encoding="utf-8")
            return 4321, 9.0

        def monitor(self, out_path, name, on_line=None, start_pos=0):
            return 0

        def cancel(self):
            pass

        def detach(self):
            pass

    monkeypatch.setattr(mlip_runner_mod, "MlipRunner", FakeMlip)
    monkeypatch.setattr(crest_runner_mod, "CrestRunner", FakeCrest)
    monkeypatch.setattr(crest_env_mod, "resolve_run_target",
                        lambda pref="", timeout=30.0: ("FakeDistro", "/fake/crest"))
    add_system(store, "s1", "mol")
    store.apply_ops([{"op": "add_calc", "calc": {
        "name": "mol_ML", "kind": "mlip_opt", "xyz": H2, "system": "s1",
        "config": {"kind": "mlip_opt", "mlip_model": "MACE-OFF small"}}},
        {"op": "add_calc", "calc": {
            "name": "mol_CREST", "kind": "crest_conf", "xyz": H2, "system": "s1",
            "config": {"kind": "crest_conf"}}}])
    base = make_engine_factory(store, "", str(ws),
                               mlip_envs=[{"id": "e", "name": "e", "python": __file__}])
    try:
        store.start_run(base)
        assert store.wait_for_run(15)
    finally:
        store.cancel_run()
        store.wait_for_run(15)
    ml, cr = store.get("mol_ML"), store.get("mol_CREST")
    assert (ml.run_dir, cr.run_dir) == ("mol/mol_ML", "mol/mol_CREST")
    assert Path(ml.output_path) == ws / "mol" / "mol_ML" / "mol_ML.mlip.json"
    assert Path(cr.output_path) == ws / "mol" / "mol_CREST" / "mol_CREST.out"
    assert ml.state == CalcState.DONE, ml.message


# ---- renaming the system, moving a block ----------------------------------------

def test_after_a_rename_a_finished_calc_keeps_its_folder_a_pending_one_follows(store, ws):
    add_system(store, "s1", "glycine")
    add(store, opt("glycine_Opt", system="s1"))
    run(store, ws)
    done = store.get("glycine_Opt")
    first_out = done.output_path
    add(store, opt("glycine_Freq", system="s1", geometry_source="reference",
                   ref_name="glycine_Opt", after="glycine_Opt", xyz=""))
    store.apply_ops([{"op": "update_system", "id": "s1", "patch": {"name": "gly"}}])

    # the finished one: same name, same folder, nothing moved on disk
    assert store.get("glycine_Opt") is done
    assert done.run_dir == "glycine/glycine_Opt" and done.output_path == first_out
    assert (ws / "glycine" / "glycine_Opt" / "glycine_Opt.out").is_file()
    assert not (ws / "gly" / "glycine_Opt").exists()
    # ...and still loads (the report's slots resolve through the record)
    b = bridge(store, ws)
    assert call(b, "get_inp", "glycine_Opt")["ok"]
    assert b._calc_run_dir("glycine_Opt") == ws / "glycine" / "glycine_Opt"
    # the pending one follows the new name, and reads its parent across folders
    assert store.names() == ["glycine_Opt", "gly_Freq"]
    assert store.run_folder(store.get("gly_Freq"), ws) == ws / "gly" / "gly_Freq"
    run(store, ws)
    freq = store.get("gly_Freq")
    assert freq.state == CalcState.DONE, freq.message
    assert freq.run_dir == "gly/gly_Freq"
    assert "0.740000" in (ws / "gly" / "gly_Freq" / "gly_Freq.inp").read_text(encoding="utf-8")


def test_a_block_moved_to_another_row_keeps_the_folder_it_ran_in(store, ws):
    add_system(store, "s1", "a")
    add_system(store, "s2", "b")
    add(store, opt("a_Opt", system="s1"), opt("a_SP", system="s1"))
    store.get("a_Opt").state = CalcState.DONE
    store.get("a_Opt").run_dir = "a/a_Opt"
    for name in ("a_Opt", "a_SP"):
        store.apply_ops([{"op": "place_calc", "name": name, "place": {"system": "s2"}}])
    assert store.run_folder(store.get("a_Opt"), ws) == ws / "a" / "a_Opt"   # ran there
    assert store.run_folder(store.get("a_SP"), ws) == ws / "b" / "a_SP"     # not yet run


# ---- a session from before system folders --------------------------------------

def test_an_old_session_finds_what_ran_in_the_root_and_runs_the_rest_in_the_new_layout(
        data, ws):
    old_out = write_out(ws / "mol_Opt", "mol_Opt")
    write_out(ws / "mol_X", "mol_X", "cancelled half way\n")
    write_session(data, 2, [
        session_calc("mol_Opt", system="s1", state="done", output_path=str(old_out)),
        # a very old record: done, but no output path
        session_calc("mol_Old", system="s1", state="done"),
        session_calc("mol_X", system="s1", state="cancelled"),
        session_calc("mol_Freq", system="s1", geometry_source="reference",
                     ref_name="mol_Opt", after="mol_Opt", xyz=""),
    ], [{"id": "s1", "name": "mol"}])
    store = QueueStore()
    store.load_session()

    assert [store.get(n).run_dir for n in ("mol_Opt", "mol_Old", "mol_X", "mol_Freq")] == [
        "mol_Opt", "mol_Old", "mol_X", ""]
    b = bridge(store, ws)
    assert b._calc_run_dir("mol_Opt") == ws / "mol_Opt"
    assert b._calc_run_dir("mol_Old") == ws / "mol_Old"
    assert call(b, "get_output_info", "mol_Opt")["ok"]

    run(store, ws)
    # the cancelled one runs again where it ran; the new one in the system folder,
    # on the geometry of its parent in the root
    assert FakeOrca.launched[0] == ws / "mol_X" / "mol_X.inp"
    assert store.get("mol_Freq").run_dir == "mol/mol_Freq"
    assert "0.740000" in (ws / "mol" / "mol_Freq" / "mol_Freq.inp").read_text(encoding="utf-8")
    assert old_out.read_text(encoding="utf-8") == OPT_OUT          # untouched


def test_an_old_rerun_row_reruns_in_the_root(data, ws):
    write_out(ws / "mol_Opt", "mol_Opt")
    write_session(data, 2, [session_calc("mol_Opt", system="s1", rerun=True)],
                  [{"id": "s1", "name": "mol"}])
    store = QueueStore()
    store.load_session()
    assert store.get("mol_Opt").run_dir == "mol_Opt"


# ---- a parent in another system's folder --------------------------------------

def _lookup(names):
    return lambda sid: names.get(sid, "")


def test_a_hessian_and_irc_end_points_are_read_from_the_parents_own_folder(ws):
    names = {"sA": "A", "sB": "B"}
    eng = QueueEngine("orca-not-needed", str(ws), system_names=_lookup(names))
    ts = Calculation(name="A_TS", kind="ts_freq", config=StepConfig(kind="ts_freq"),
                     system="sA", state=CalcState.DONE, run_dir="A/A_TS")
    irc = Calculation(name="A_IRC", kind="irc", config=StepConfig(kind="irc"),
                      system="sA", state=CalcState.DONE, run_dir="A/A_IRC")
    (ws / "A" / "A_TS").mkdir(parents=True)
    (ws / "A" / "A_TS" / "A_TS.hess").write_text("$hessian\n", encoding="utf-8")
    (ws / "A" / "A_IRC").mkdir(parents=True)
    (ws / "A" / "A_IRC" / "A_IRC_IRC_F.xyz").write_text(
        "2\nend\nH 0 0 0\nH 0 0 0.9\n", encoding="utf-8")
    eng._by_name = {"A_TS": ts, "A_IRC": irc}

    # the IRC end point a step in another system starts from
    child = Calculation(name="B_SP", kind="sp", config=StepConfig(kind="sp"), system="sB",
                        geometry_source=GeometrySource.REFERENCE, ref_name="A_IRC",
                        ref_point="irc_f")
    assert eng._irc_end_geometry(child, irc).splitlines() == ["H 0 0 0", "H 0 0 0.9"]

    # the .hess an IRC in system B reads from its parent in system A
    b_irc = Calculation(name="B_IRC", kind="irc", system="sB",
                        config=StepConfig(kind="irc", irc_init_hess="read",
                                          irc_hess_file="A_TS.hess"),
                        geometry_source=GeometrySource.REFERENCE, ref_name="A_TS")
    run_dir = ws / "B" / "B_IRC"
    run_dir.mkdir(parents=True)
    eng._stage_irc_hessian(b_irc, run_dir)
    assert (run_dir / "A_TS.hess").is_file()

    # a template's {{HESS}}: found in the parent's folder, and every place
    # looked is named (a parent that has not run: where it will)
    tpl = SimpleNamespace(name="B_X", after="A_TS", geometry_source=GeometrySource.DIRECT,
                          ref_name="")
    src, looked = find_parent_hessian(tpl, eng._by_name, ws, _lookup(names))
    assert src == ws / "A" / "A_TS" / "A_TS.hess"
    later = Calculation(name="A_F2", kind="freq", config=StepConfig(kind="freq"), system="sA")
    tpl.after = "A_F2"
    src, looked = find_parent_hessian(tpl, {"A_F2": later}, ws, _lookup(names))
    assert src is None and looked == [str(ws / "A" / "A_F2" / "A_F2.hess")]


def test_the_irc_picker_lists_an_irc_in_its_system_folder(store, ws):
    add_system(store, "s1", "ts")
    store.apply_ops([{"op": "add_calc", "calc": {"name": "ts_IRC", "kind": "irc",
                                                 "xyz": H2, "system": "s1"}}])
    c = store.get("ts_IRC")
    c.state, c.run_dir = CalcState.DONE, "ts/ts_IRC"
    folder = ws / "ts" / "ts_IRC"
    folder.mkdir(parents=True)
    (folder / "ts_IRC_IRC_F.xyz").write_text("2\nf\nH 0 0 0\nH 0 0 1\n", encoding="utf-8")
    store.apply_ops([{"op": "update_system", "id": "s1", "patch": {"name": "renamed"}}])
    got = call(bridge(store, ws), "get_irc_structures", "ts_IRC")
    assert got["ok"] and [f["path"] for f in got["files"]] == [str(folder / "ts_IRC_IRC_F.xyz")]


# ---- keep-existing and the overwrite conflicts -----------------------------------

def test_keep_existing_and_conflicts_look_where_the_calc_runs(store, ws):
    add_system(store, "s1", "mol")
    add(store, opt("mol_Opt", system="s1"), opt("mol_SP", system="s1"), opt("solo"))
    write_out(ws / "mol" / "mol_Opt", "mol_Opt")              # its own folder
    old_root = write_out(ws / "mol_SP", "mol_SP")             # from before system folders
    write_out(ws / "solo", "solo")                            # a standalone's folder
    b = bridge(store, ws)
    assert call(b, "check_overwrite_conflicts")["conflicts"] == ["mol_Opt", "solo"]

    run(store, ws, keep={"mol_Opt", "mol_SP", "solo"})
    kept = store.get("mol_Opt")
    assert kept.state == CalcState.DONE and "Kept existing" in kept.message
    assert kept.run_dir == "mol/mol_Opt"
    assert Path(kept.output_path) == ws / "mol" / "mol_Opt" / "mol_Opt.out"
    # the root output is not this calc's: it ran, in its system folder, and the
    # old file was never written to
    assert ws / "mol" / "mol_SP" / "mol_SP.inp" in FakeOrca.launched
    assert old_root.read_text(encoding="utf-8") == OPT_OUT
    assert store.get("solo").message.startswith("Kept existing")


def test_a_new_name_with_output_anywhere_in_the_workspace_is_reported(store, ws):
    write_out(ws / "old_root", "old_root")                    # {workspace}/{name}/
    write_out(ws / "gone_sys" / "gone_Opt", "gone_Opt")        # a removed row's folder
    (ws / "gone_sys" / "empty").mkdir()
    b = bridge(store, ws)
    got = call(b, "existing_outputs", json.dumps(["old_root", "gone_Opt", "empty", "fresh"]))
    assert got == {"ok": True, "existing": ["old_root", "gone_Opt"]}
    assert call(b, "has_existing_output", "gone_Opt")["exists"] is True
    assert call(b, "has_existing_output", "fresh")["exists"] is False


def test_the_workspace_scan_reads_system_folders(store, ws):
    write_out(ws / "solo", "solo")
    write_out(ws / "mol" / "mol_Opt", "mol_Opt")
    got = call(bridge(store, ws), "list_workspace_results")
    assert got["ok"] and sorted(r["name"] for r in got["results"]) == ["mol_Opt", "solo"]


def test_show_folder_names_the_calc_and_the_backend_finds_its_folder(store, ws, monkeypatch):
    import orcamgr.openwith as openwith
    shown = []
    monkeypatch.setattr(openwith, "show_in_folder",
                        lambda p: shown.append(Path(p)) or {"ok": True})
    add_system(store, "s1", "mol")
    add(store, opt("mol_Opt", system="s1"))
    b = bridge(store, ws)
    got = call(b, "show_calc_folder", "mol_Opt")
    assert got["ok"] is False and "no folder yet" in got["error"]
    (ws / "mol" / "mol_Opt").mkdir(parents=True)
    assert call(b, "show_calc_folder", "mol_Opt")["ok"] is True
    assert shown == [ws / "mol" / "mol_Opt"]


# ---- unsafe system names ---------------------------------------------------------

@pytest.mark.parametrize("name", [
    "a/b", "a\\b", "a:b", "a*b", 'a"b', "a<b", "a|b", "..", "x..y", "name.",
    "con", "CON.txt", "con .txt", "nul", "com1", "LPT9", "com\u00b9", "lpt\u00b3",
    "tab\tname", "bell\x07", "x" * 121,
])
def test_a_system_name_that_is_not_a_safe_folder_is_refused(store, name):
    with pytest.raises(ValueError):
        system_from_dict({"id": "s1", "name": name})
    with pytest.raises(QueueOpError):
        store.apply_ops([{"op": "add_system", "system": {"id": "s1", "name": name}}])
    add_system(store, "s1", "ok")
    with pytest.raises(QueueOpError):
        store.apply_ops([{"op": "update_system", "id": "s1", "patch": {"name": name}}])
    assert store.get_system("s1")["name"] == "ok"


def test_trailing_spaces_are_trimmed_and_the_folder_is_the_name_verbatim(store, ws):
    store.apply_ops([{"op": "add_system", "system": {"id": "s1", "name": "  분자_1  "}}])
    assert store.get_system("s1")["name"] == "분자_1"
    add(store, opt("분자_1_Opt", system="s1"))
    assert store.run_folder(store.get("분자_1_Opt"), ws) == ws / "분자_1" / "분자_1_Opt"


def test_a_restored_name_with_a_space_is_a_folder_and_an_unsafe_one_is_not_used():
    # restoring relaxes only ORCA's rule: a space is fine in a folder
    assert system_from_dict({"id": "s1", "name": "my mol"}, restoring=True)["name"] == "my mol"
    assert rundir.system_folder("my mol") == "my mol"
    # the helper never builds a path from a name that slipped past validation
    for bad in ("a/b", "..", "con", "x.", ""):
        assert rundir.system_folder(bad) == ""
        assert rundir.planned_run_dir("c", bad) == "c"


# ---- reattach after a restart ------------------------------------------------------

def _reattach(store, ws, monkeypatch):
    monkeypatch.setattr(queue_mod, "process_matches", lambda pid, ct: True)
    run(store, ws)
    return FakeOrca.tailed


def test_reattach_after_a_restart_tails_the_recorded_folder(data, ws, monkeypatch):
    monkeypatch.setattr(store_mod, "process_matches", lambda pid, ct: True)
    # recorded under "mol"; the system has been renamed since
    write_session(data, 3, [session_calc("mol_Opt", system="s1", state="running",
                                         pid=4242, create_time=1.0,
                                         run_dir="mol/mol_Opt")],
                  [{"id": "s1", "name": "renamed"}])
    store = QueueStore()
    store.load_session()
    assert store.has_live_running()
    (ws / "mol" / "mol_Opt").mkdir(parents=True)        # where the job is writing
    tailed = _reattach(store, ws, monkeypatch)
    assert tailed == [ws / "mol" / "mol_Opt" / "mol_Opt.out"]
    c = store.get("mol_Opt")
    assert c.state == CalcState.DONE and c.run_dir == "mol/mol_Opt"


def test_reattach_of_a_job_from_before_system_folders_tails_the_root(data, ws, monkeypatch):
    monkeypatch.setattr(store_mod, "process_matches", lambda pid, ct: True)
    write_session(data, 2, [session_calc("mol_Opt", system="s1", state="running",
                                         pid=4242, create_time=1.0)],
                  [{"id": "s1", "name": "mol"}])
    store = QueueStore()
    store.load_session()
    assert store.get("mol_Opt").run_dir == "mol_Opt"
    (ws / "mol_Opt").mkdir(parents=True)                # where the job is writing
    assert _reattach(store, ws, monkeypatch) == [ws / "mol_Opt" / "mol_Opt.out"]
    assert store.get("mol_Opt").state == CalcState.DONE


# ---- a CANCELLED re-run and a rerun_calc --------------------------------------------

def test_a_cancelled_calc_reruns_where_it_ran_after_a_rename_a_move_and_an_edit(store, ws):
    add_system(store, "s1", "mol")
    add_system(store, "s2", "other")
    add(store, opt("mol_Opt", system="s1"))
    c = store.get("mol_Opt")
    c.state, c.run_dir = CalcState.CANCELLED, "mol/mol_Opt"
    store.apply_ops([{"op": "update_system", "id": "s1", "patch": {"name": "w"}}])
    store.apply_ops([{"op": "place_calc", "name": "mol_Opt", "place": {"system": "s2"}}])
    store.apply_ops([{"op": "update_calc", "name": "mol_Opt",
                      "calc": {"config": {"kind": "opt", "functional": "PBE0"}}}])
    edited = store.get("mol_Opt")
    assert edited.state == CalcState.PENDING and edited.run_dir == "mol/mol_Opt"
    assert edited.config.functional == "PBE0"
    run(store, ws)
    assert FakeOrca.launched == [ws / "mol" / "mol_Opt" / "mol_Opt.inp"]
    # a rename of the calc itself is a new name: a new folder when it starts
    store.get("mol_Opt").state = CalcState.CANCELLED
    store.apply_ops([{"op": "update_calc", "name": "mol_Opt", "calc": {"name": "x_Opt"}}])
    assert store.get("x_Opt").run_dir == ""


def test_a_rerun_calc_runs_again_in_the_folder_it_ran_in(store, ws):
    add_system(store, "s1", "mol")
    add(store, opt("mol_Opt", system="s1"))
    run(store, ws)
    assert store.get("mol_Opt").run_dir == "mol/mol_Opt"
    store.apply_ops([{"op": "update_system", "id": "s1", "patch": {"name": "w"}}])
    store.apply_ops([{"op": "rerun_calc", "name": "mol_Opt", "calc": {
        "raw_text": "! HF def2-SVP {{JOB}}\n* xyz {{CHARGE}} {{MULT}}\n{{GEOMETRY}}\n*\n",
        "is_raw": True, "set_name": "hf"}}])
    again = store.get("mol_Opt")
    assert again.rerun and again.run_dir == "mol/mol_Opt" and again.output_path == ""
    FakeOrca.launched.clear()
    run(store, ws)
    assert FakeOrca.launched == [ws / "mol" / "mol_Opt" / "mol_Opt.inp"]
    assert store.get("mol_Opt").state == CalcState.DONE


# ---- a folder the user moved by hand ----------------------------------------------

def done_at(store, ws, name, folder, system=""):
    """A finished calc whose output sits in ``folder`` (relative to ws), recorded there."""
    add(store, opt(name, system=system))
    out = write_out(ws.joinpath(*folder.split("/")), name)
    c = store.get(name)
    c.state, c.run_dir, c.output_path = CalcState.DONE, folder, str(out)
    return c


def move(ws, src, dst):
    """What the user does in the file manager."""
    target = ws.joinpath(*dst.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    ws.joinpath(*src.split("/")).rename(target)
    return target


def log_lines(store, text):
    return [ln for ln in store.log_since(0)["lines"] if text in ln["msg"]]


def test_the_search_finds_a_run_folder_by_name_and_output_down_to_the_depth_bound(ws):
    write_out(ws / "proj" / "series" / "a", "a")
    write_out(ws / "b", "b")
    (ws / "proj" / "c").mkdir(parents=True)                         # named c, no output
    write_out(ws / "x1" / "dup", "dup")
    write_out(ws / "x2" / "dup", "dup")
    write_out(ws.joinpath("1", "2", "3", "4", "deep"), "deep")          # 4 folders up
    write_out(ws.joinpath("1", "2", "3", "4", "5", "deeper"), "deeper")  # 5: past the bound
    got = rundir.find_moved(ws, {n: f"{n}.out" for n in
                                 ("a", "b", "c", "dup", "deep", "deeper", "none")})
    assert {n: sorted(v) for n, v in got.items()} == {
        "a": ["proj/series/a"], "b": ["b"], "dup": ["x1/dup", "x2/dup"],
        "deep": ["1/2/3/4/deep"]}
    assert rundir.find_moved(ws, {"a": "a.mlip.json"}) == {}          # the file it wrote
    assert rundir.find_moved(ws / "absent", {"a": "a.out"}) == {}
    assert rundir.find_moved(ws, {"../a": "a.out", "": "x.out"}) == {}


def test_the_search_does_not_follow_a_link_or_a_junction(ws, tmp_path):
    write_out(tmp_path / "outside" / "a", "a")
    ws.mkdir()
    linked = False
    if sys.platform == "win32":
        import _winapi                       # a junction needs no privilege
        _winapi.CreateJunction(str(tmp_path / "outside"), str(ws / "junction"))
        linked = True
    try:
        os.symlink(tmp_path / "outside", ws / "link", target_is_directory=True)
        linked = True
    except (OSError, NotImplementedError):
        pass                                 # Windows without the symlink privilege
    if not linked:
        pytest.skip("no link could be made here")
    assert rundir.find_moved(ws, {"a": "a.out"}) == {}


def test_a_finished_calc_moved_by_hand_is_found_recorded_and_restored(store, ws, data):
    add_system(store, "s1", "mol")
    c = done_at(store, ws, "mol_Opt", "mol/mol_Opt", system="s1")
    new = move(ws, "mol/mol_Opt", "archive/2026 runs/mol_Opt")
    assert store.relocate_moved(ws) == ["mol_Opt"]
    assert c.run_dir == "archive/2026 runs/mol_Opt"
    assert Path(c.output_path) == new / "mol_Opt.out"
    assert store.result_folder(c, ws) == new and store.run_folder(c, ws) == new
    assert len(log_lines(store, "Found mol_Opt in archive/2026 runs/mol_Opt")) == 1
    # persisted, the deeper folder included: the next launch reads it there
    again = QueueStore()
    again.load_session()
    assert again.get("mol_Opt").run_dir == "archive/2026 runs/mol_Opt"
    assert again.get("mol_Opt").output_path == c.output_path
    assert store.relocate_moved(ws) == []                    # nothing left to look for


def test_two_folders_that_fit_are_never_guessed_between(store, ws):
    c = done_at(store, ws, "solo", "solo")
    old = c.output_path
    move(ws, "solo", "a/solo")
    write_out(ws / "b" / "solo", "solo")                     # a copy
    assert store.relocate_moved(ws) == [] and store.relocate_moved(ws) == []
    assert c.output_path == old and c.run_dir == "solo"
    assert len(log_lines(store, "2 folders named solo")) == 1        # said once
    (ws / "b" / "solo" / "solo.out").unlink()                # the user keeps one
    assert store.relocate_moved(ws) == ["solo"] and c.run_dir == "a/solo"


def test_a_running_calc_and_an_output_still_in_place_are_left_alone(store, ws):
    live = done_at(store, ws, "live", "live")
    live.state = CalcState.RUNNING                           # its job writes where it started
    move(ws, "live", "elsewhere/live")
    here = done_at(store, ws, "here", "here")
    write_out(ws / "copy" / "here", "here")                  # the record still holds
    before = sorted(p.relative_to(ws).as_posix() for p in ws.rglob("*"))
    assert store.relocate_moved(ws) == []
    assert (live.run_dir, here.run_dir) == ("live", "here")
    assert sorted(p.relative_to(ws).as_posix() for p in ws.rglob("*")) == before


def test_an_on_demand_lookup_does_not_walk_again_for_a_name_just_missed(store, ws, monkeypatch):
    done_at(store, ws, "gone", "gone")
    shutil.rmtree(ws / "gone")                               # deleted, not moved
    walks = []
    real = rundir.find_moved
    monkeypatch.setattr(rundir, "find_moved",
                        lambda w, wanted: walks.append(set(wanted)) or real(w, wanted))
    assert store.relocate_moved(ws, ["gone"]) == []
    assert store.relocate_moved(ws, ["gone"]) == []          # within the retry window
    assert walks == [{"gone"}]
    write_out(ws / "back" / "gone", "gone")
    assert store.relocate_moved(ws) == ["gone"]              # a full lookup always looks
    assert store.get("gone").run_dir == "back/gone"


def test_the_irc_picker_and_show_folder_follow_a_moved_irc(store, ws, monkeypatch):
    import orcamgr.openwith as openwith
    shown = []
    monkeypatch.setattr(openwith, "show_in_folder",
                        lambda p: shown.append(Path(p)) or {"ok": True})
    add_system(store, "s1", "ts")
    store.apply_ops([{"op": "add_calc", "calc": {"name": "ts_IRC", "kind": "irc",
                                                 "xyz": H2, "system": "s1"}}])
    c = store.get("ts_IRC")
    folder = ws / "ts" / "ts_IRC"
    out = write_out(folder, "ts_IRC")
    (folder / "ts_IRC_IRC_Full_trj.xyz").write_text(
        "2\nb\nH 0 0 0\nH 0 0 1\n2\nf\nH 0 0 0\nH 0 0 1.1\n", encoding="utf-8")
    c.state, c.run_dir, c.output_path = CalcState.DONE, "ts/ts_IRC", str(out)
    b = bridge(store, ws)
    # what the picker hit: the path files moved with the folder
    new = move(ws, "ts/ts_IRC", "sorted/3CNin/ts_IRC")
    got = call(b, "get_irc_structures", "ts_IRC")
    assert got["ok"] and [f["path"] for f in got["files"]] == [
        str(new / "ts_IRC_IRC_Full_trj.xyz")]
    # moved again: Show folder follows it too
    newer = move(ws, "sorted/3CNin/ts_IRC", "ts_IRC")
    assert call(b, "show_calc_folder", "ts_IRC")["ok"] is True
    assert shown == [newer / "ts_IRC.out"] and c.run_dir == "ts_IRC"


def test_a_step_after_a_moved_parent_starts_from_its_geometry_where_it_is_now(store, ws):
    parent = done_at(store, ws, "p_Opt", "p_Opt")
    move(ws, "p_Opt", "done/p_Opt")
    add(store, opt("p_SP", geometry_source="reference", ref_name="p_Opt",
                   after="p_Opt", xyz=""))
    run(store, ws)
    assert parent.run_dir == "done/p_Opt"
    assert store.get("p_SP").state == CalcState.DONE
    assert "0.740000" in (ws / "p_SP" / "p_SP.inp").read_text(encoding="utf-8")
