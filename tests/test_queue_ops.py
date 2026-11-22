"""The queue editor's model in the shared store (state/store.py).

* the Calculation graph fields ride calc_from_dict / calc_to_dict /
  session.json, defaulting to "standalone, as today", checked at the trust
  boundary (and degraded, not evicted, on restore);
* systems (the lanes) live in the store, are persisted beside the
  calculations and restored on load; an old session without them loads as it
  always did;
* apply_ops applies a batch atomically — one refused operation and nothing
  changes — through the single mutations' own rules;
* a system rename renames its lane's calcs that have not started and
  re-points references; a started calc keeps its name and its folder;
* xyz_file_entries: a dropped / picked .xyz gives one structure (and the
  charge / multiplicity its comment line states), or why not.

Session I/O is redirected to tmp_path (see test_store.py for why the store
module's own user_data_root is patched).
"""

from __future__ import annotations

import json

import pytest

import orcamgr.state.store as store_mod
from orcamgr.core.queue import CalcState, Calculation
from orcamgr.state.store import (QueueOpError, QueueStore, calc_from_dict,
                                 calc_from_session_dict, calc_to_dict,
                                 calc_to_session_dict, system_from_dict,
                                 xyz_charge_mult, xyz_file_entries)

H2 = "H 0.0 0.0 0.0\nH 0.0 0.0 0.74"
TPL = "! B3LYP def2-SVP SP\n* xyz {{CHARGE}} {{MULT}}\n{{GEOMETRY}}\n*\n"


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(store_mod, "user_data_root", lambda: tmp_path)
    return QueueStore()


def sys_op(sid="s1", name="mol", **kw):
    return {"op": "add_system", "system": {"id": sid, "name": name,
                                           "inputs": [{"label": "start", "xyz": H2}], **kw}}


def calc_op(name, system="s1", **kw):
    d = {"name": name, "kind": "sp", "is_raw": True, "raw_text": TPL,
         "xyz": H2, "system": system, "node": f"n-{name}"}
    d.update(kw)
    return {"op": "add_calc", "calc": d}


# ---- the graph fields at the trust boundary --------------------------------------

def test_graph_fields_default_to_a_standalone_calc():
    c = calc_from_dict({"name": "a", "kind": "sp", "xyz": H2})
    assert (c.system, c.node, c.col, c.row, c.after) == ("", "", 0, 0, "")
    assert (c.gate, c.auto_pass, c.approved, c.ref_point, c.set_name) == (
        "auto", True, False, "", "")


def test_graph_fields_round_trip_through_summary_and_session():
    d = {"name": "b", "kind": "irc", "xyz": H2, "system": "s1", "node": "n1",
         "col": 3, "row": 1, "after": "a", "gate": "approval", "auto_pass": False,
         "approved": True, "ref_point": "irc_b", "set_name": "my set"}
    c = calc_from_dict(d)
    summary = calc_to_dict(c, waiting=True)
    for k, v in d.items():
        if k not in ("xyz",):
            assert summary[k] == v, k
    assert summary["waiting"] is True
    back = calc_from_session_dict(json.loads(json.dumps(calc_to_session_dict(c))))
    for k in ("system", "node", "col", "row", "after", "gate", "auto_pass",
              "approved", "ref_point", "set_name"):
        assert getattr(back, k) == getattr(c, k), k


@pytest.mark.parametrize("bad", [
    {"gate": "maybe"}, {"ref_point": "irc_x"}, {"approved": "yes"},
    {"col": "3"}, {"system": "a/b"}, {"after": 7},
])
def test_a_bad_graph_field_is_refused_for_a_new_calc(bad):
    with pytest.raises(ValueError):
        calc_from_dict({"name": "a", "kind": "sp", "xyz": H2, **bad})


def test_a_bad_graph_field_degrades_on_restore_instead_of_evicting():
    c = calc_from_dict({"name": "a", "kind": "sp", "xyz": H2, "gate": "maybe",
                        "ref_point": 5, "col": "x"}, restoring=True)
    assert (c.gate, c.ref_point, c.col) == ("auto", "", 0)


def test_an_mlip_template_becomes_an_mlip_config():
    c = calc_from_dict({"name": "m", "kind": "mlip_opt", "xyz": H2, "is_raw": True,
                        "raw_text": "model MACE-OFF small\ndevice cpu\nnprocs 3\n"})
    assert c.is_raw is False                       # it runs as an MLIP calc
    assert (c.config.mlip_model, c.config.mlip_device, c.config.nprocs) == (
        "MACE-OFF small", "cpu", 3)
    assert c.config.kind == "mlip_opt"
    assert "MACE-OFF small" in c.raw_text           # the template is kept for display
    with pytest.raises(ValueError, match="unknown key"):
        calc_from_dict({"name": "m", "kind": "mlip_opt", "xyz": H2, "is_raw": True,
                        "raw_text": "modle x\n"})


# ---- systems in the store and the session ----------------------------------------

def test_snapshot_carries_systems_without_coordinates(store):
    store.apply_ops([sys_op()])
    snap = store.snapshot()
    assert snap["systems"] == [{"id": "s1", "name": "mol", "charge": 0,
                                "multiplicity": 1, "inputs": [{"label": "start"}]}]
    full = store.get_system("s1")
    assert full["inputs"][0]["xyz"] == H2
    assert store.get_system("nope") is None


def test_a_system_input_keeps_a_coordinate_block_not_the_xyz_header():
    s = system_from_dict({"name": "w", "inputs": [{"label": "", "xyz": "2\ncomment\n" + H2}]})
    assert s["inputs"] == [{"label": "input 1", "xyz": H2}]
    assert s["id"].startswith("sys-")


def test_snapshot_derives_waiting(store):
    store.apply_ops([sys_op(), calc_op("mol-opt"),
                     calc_op("mol-sp", after="mol-opt", gate="approval")])
    store.get("mol-opt").state = CalcState.DONE
    rows = {c["name"]: c for c in store.snapshot()["calculations"]}
    assert rows["mol-sp"]["waiting"] is True
    assert rows["mol-opt"]["waiting"] is False


def test_session_round_trip_restores_systems_and_graph_fields(store, tmp_path):
    store.apply_ops([sys_op(charge=-1, multiplicity=2),
                     calc_op("mol-opt", col=0),
                     calc_op("mol-sp", after="mol-opt", gate="approval", col=1,
                             set_name="B3LYP / def2-SVP")])
    fresh = QueueStore()
    fresh.load_session()
    assert fresh.get_system("s1") == store.get_system("s1")
    sp = fresh.get("mol-sp")
    assert (sp.system, sp.after, sp.gate, sp.col, sp.set_name) == (
        "s1", "mol-opt", "approval", 1, "B3LYP / def2-SVP")


def test_an_old_session_without_systems_loads_unchanged(store, tmp_path):
    (tmp_path / "session.json").write_text(json.dumps({"schema": 1, "calculations": [
        {"name": "old", "kind": "sp", "xyz": H2, "state": "done"}]}), encoding="utf-8")
    store.load_session()
    assert [c.name for c in store.list()] == ["old"]
    assert store.get("old").state == CalcState.DONE
    assert store.snapshot()["systems"] == []


def test_a_calc_whose_lane_did_not_come_back_is_standalone(store, tmp_path):
    (tmp_path / "session.json").write_text(json.dumps({"schema": 1, "calculations": [
        {"name": "a", "kind": "sp", "xyz": H2, "system": "lost"}],
        "systems": [{"id": "s1", "name": "kept"}, "garbage",
                    {"id": "s2", "name": "kept"}]}), encoding="utf-8")
    store.load_session()
    assert store.get("a").system == ""
    assert [s["id"] for s in store.systems()] == ["s1"]   # corrupt / duplicate skipped


def test_a_session_with_only_systems_still_restores_them(store, tmp_path):
    (tmp_path / "session.json").write_text(json.dumps(
        {"schema": 1, "calculations": [], "systems": [{"id": "s1", "name": "x"}]}),
        encoding="utf-8")
    store.load_session()
    assert [s["name"] for s in store.systems()] == ["x"]


def test_clear_removes_the_lanes_too(store):
    store.apply_ops([sys_op(), calc_op("mol-sp")])
    store.clear()
    assert store.list() == [] and store.systems() == []


def test_a_calc_naming_an_unknown_system_is_refused(store):
    with pytest.raises(ValueError, match="system that does not exist"):
        store.add(calc_from_dict({"name": "a", "kind": "sp", "xyz": H2, "system": "x"}))


# ---- apply_ops: all or nothing ------------------------------------------------------

def test_one_bad_op_and_nothing_is_applied(store):
    store.apply_ops([sys_op(sid="s0", name="first")])
    before = (store.names(), store.systems(), store.version())
    with pytest.raises(QueueOpError) as e:
        store.apply_ops([sys_op(), calc_op("mol-a"), calc_op("MOL-A")])   # duplicate
    assert e.value.index == 2
    assert "already exists" in str(e.value)
    assert (store.names(), store.systems(), store.version()) == before


def test_ops_may_add_a_child_before_its_parent(store):
    store.apply_ops([sys_op(), calc_op("mol-sp", after="mol-opt"), calc_op("mol-opt")])
    assert store.names() == ["mol-sp", "mol-opt"]


def test_update_calc_is_a_patch(store):
    store.apply_ops([sys_op(), calc_op("mol-opt"),
                     calc_op("mol-sp", after="mol-opt", gate="approval")])
    store.get("mol-opt").state = CalcState.DONE
    store.apply_ops([{"op": "update_calc", "name": "mol-sp", "calc": {"approved": True}}])
    sp = store.get("mol-sp")
    assert sp.approved and sp.after == "mol-opt" and sp.raw_text == TPL
    assert not store.snapshot()["calculations"][1]["waiting"]


def test_update_calc_on_a_finished_calc_is_refused(store):
    store.apply_ops([sys_op(), calc_op("mol-opt")])
    store.get("mol-opt").state = CalcState.DONE
    with pytest.raises(QueueOpError, match="Only pending"):
        store.apply_ops([{"op": "update_calc", "name": "mol-opt", "calc": {"col": 2}}])


# ---- place_calc: a finished calc becomes a lane's first node ------------------------

def _done_standalone(store, name="1TS", kind="ts_opt"):
    store.apply_ops([calc_op(name, system="", kind=kind, node="")])
    c = store.get(name)
    c.state = CalcState.DONE
    c.message = "converged"
    c.output_path = "C:/ws/" + name + "/" + name + ".out"
    return c


def test_a_finished_standalone_calc_is_placed_into_a_new_lane_untouched(store):
    """The editor's way to hang a follow-up off a finished calc: a new system
    and a placement-only patch. The finished row keeps everything it ran and
    produced (P24) and stays the same object (the engine tracks rows by
    identity)."""
    live = _done_standalone(store)
    before = {k: v for k, v in calc_to_session_dict(live).items()
              if k not in ("system", "node", "col", "row")}
    store.apply_ops([sys_op(sid="s9", name="1TS"),
                     {"op": "place_calc", "name": "1TS",
                      "place": {"system": "s9", "node": "n1", "col": 0, "row": 0}},
                     calc_op("1TS-freq", system="s9", kind="ts_freq", after="1TS",
                             geometry_source="reference", ref_name="1TS", xyz="",
                             col=1, row=0)])
    c = store.get("1TS")
    assert c is live
    assert (c.system, c.node, c.col, c.row) == ("s9", "n1", 0, 0)
    assert c.state == CalcState.DONE and c.message == "converged"
    assert {k: v for k, v in calc_to_session_dict(c).items()
            if k not in ("system", "node", "col", "row")} == before
    snap = store.snapshot()["calculations"]
    assert [(x["name"], x["system"], x["state"]) for x in snap] == [
        ("1TS", "s9", "done"), ("1TS-freq", "s9", "pending")]


def test_placing_back_and_removing_the_lane_undoes_the_adoption(store):
    _done_standalone(store)
    store.apply_ops([sys_op(sid="s9", name="1TS"),
                     {"op": "place_calc", "name": "1TS", "place": {"system": "s9", "node": "n1"}}])
    store.apply_ops([{"op": "place_calc", "name": "1TS",
                      "place": {"system": "", "node": "", "col": 0, "row": 0}},
                     {"op": "remove_system", "id": "s9"}])
    c = store.get("1TS")
    assert (c.system, c.node, c.state) == ("", "", CalcState.DONE)
    assert store.systems() == []


@pytest.mark.parametrize("bad", [
    {"after": "x"}, {"raw_text": "! HF"}, {"state": "pending"}, {"name": "y"},
    {"kind": "sp"}, {"approved": True},
])
def test_place_calc_changes_placement_and_nothing_else(store, bad):
    _done_standalone(store)
    with pytest.raises(QueueOpError, match="only system, node, col and row"):
        store.apply_ops([{"op": "place_calc", "name": "1TS", "place": bad}])


def test_place_calc_refuses_an_unknown_system_or_a_bad_value(store):
    _done_standalone(store)
    with pytest.raises(QueueOpError, match="does not exist"):
        store.apply_ops([{"op": "place_calc", "name": "1TS", "place": {"system": "nope"}}])
    with pytest.raises(QueueOpError, match="whole number"):
        store.apply_ops([{"op": "place_calc", "name": "1TS", "place": {"col": "2"}}])
    with pytest.raises(QueueOpError, match="No calculation named"):
        store.apply_ops([{"op": "place_calc", "name": "ghost", "place": {"col": 1}}])


def test_a_refused_batch_leaves_a_placed_row_where_it_was(store):
    live = _done_standalone(store)
    with pytest.raises(QueueOpError):
        store.apply_ops([sys_op(sid="s9", name="1TS"),
                         {"op": "place_calc", "name": "1TS", "place": {"system": "s9"}},
                         {"op": "explode"}])
    assert store.get("1TS") is live and live.system == "" and store.systems() == []


def test_a_running_row_is_placed_in_place(store):
    store.apply_ops([sys_op(), calc_op("mol-opt")])
    live = store.get("mol-opt")
    live.state = CalcState.RUNNING
    store.apply_ops([{"op": "place_calc", "name": "mol-opt", "place": {"col": 3, "row": 1}},
                     {"op": "place_calc", "name": "mol-opt", "place": {"row": 2}}])
    assert store.get("mol-opt") is live
    assert (live.col, live.row, live.state) == (3, 2, CalcState.RUNNING)


def test_remove_calc_of_an_unknown_name_is_refused_unless_removed_in_the_batch(store):
    store.apply_ops([sys_op(), calc_op("mol-sp")])
    with pytest.raises(QueueOpError, match="No calculation named 'ghost'"):
        store.apply_ops([{"op": "remove_calc", "name": "ghost"}])
    store.apply_ops([{"op": "remove_system", "id": "s1"},
                     {"op": "remove_calc", "name": "mol-sp"}])
    assert store.names() == [] and store.systems() == []


def test_order_takes_a_full_permutation_only(store):
    store.apply_ops([sys_op(), calc_op("a"), calc_op("b"), calc_op("c")])
    store.get("a").state = CalcState.DONE          # finished rows move with a lane
    store.apply_ops([{"op": "order", "names": ["c", "a", "b"]}])
    assert store.names() == ["c", "a", "b"]
    for bad in (["a", "b"], ["a", "b", "b"], ["a", "b", "z"]):
        with pytest.raises(QueueOpError, match="exactly once"):
            store.apply_ops([{"op": "order", "names": bad}])


def test_order_rearranges_the_live_list_in_place(store):
    store.apply_ops([sys_op(), calc_op("a"), calc_op("b")])
    live = store._calcs                             # what a running engine walks
    store.apply_ops([{"op": "order", "names": ["b", "a"]}])
    assert store._calcs is live and [c.name for c in live] == ["b", "a"]


def test_an_unknown_op_is_refused(store):
    with pytest.raises(QueueOpError, match="Unknown operation"):
        store.apply_ops([{"op": "explode"}])


def test_mid_run_a_batch_may_not_leave_a_dependency_dangling(store):
    store.apply_ops([sys_op(), calc_op("mol-opt"), calc_op("mol-sp", after="mol-opt")])
    store._running = True                          # a run in progress, idle engine
    try:
        with pytest.raises(QueueOpError, match="runs after 'mol-opt'") as e:
            store.apply_ops([{"op": "remove_calc", "name": "mol-opt"}])
        assert e.value.index is None
        # the node together with its descendant is consistent as a whole
        store.apply_ops([{"op": "remove_calc", "name": "mol-opt"},
                         {"op": "remove_calc", "name": "mol-sp"}])
    finally:
        store._running = False
    assert store.names() == []


# ---- systems: rename, recharge, remove ------------------------------------------------

def test_a_rename_renames_the_lane_and_repoints_its_references(store):
    store.apply_ops([sys_op(name="mol"), calc_op("mol-opt"),
                     calc_op("mol-freq", after="mol-opt", geometry_source="reference",
                             ref_name="mol-opt"),
                     calc_op("standalone", system="", geometry_source="reference",
                             ref_name="mol-freq")])
    store.apply_ops([{"op": "update_system", "id": "s1", "patch": {"name": "water"}}])
    assert store.names() == ["water-opt", "water-freq", "standalone"]
    freq = store.get("water-freq")
    assert (freq.after, freq.ref_name) == ("water-opt", "water-opt")
    assert store.get("standalone").ref_name == "water-freq"
    assert store.get_system("s1")["name"] == "water"


def test_a_rename_after_the_lane_has_started_moves_only_what_has_not(store):
    """A started calc keeps its name (P24, and other calcs name it) and the
    folder recorded when it started (core/rundir.py); what has not started is
    renamed and runs under the new name. It used to be refused outright, when a
    calc's folder was its name alone."""
    store.apply_ops([sys_op(name="mol"), calc_op("mol-opt"), calc_op("mol-cx"),
                     calc_op("mol-sp", after="mol-opt", geometry_source="reference",
                             ref_name="mol-opt")])
    done = store.get("mol-opt")
    done.state, done.run_dir = CalcState.DONE, "mol/mol-opt"
    cancelled = store.get("mol-cx")
    cancelled.state, cancelled.run_dir = CalcState.CANCELLED, "mol/mol-cx"
    store.apply_ops([{"op": "update_system", "id": "s1", "patch": {"name": "w"}}])
    assert store.names() == ["mol-opt", "mol-cx", "w-sp"]
    assert store.get("mol-opt") is done and done.run_dir == "mol/mol-opt"
    assert store.get("mol-cx").run_dir == "mol/mol-cx"        # it ran: it stays
    sp = store.get("w-sp")
    assert (sp.after, sp.ref_name, sp.run_dir) == ("mol-opt", "mol-opt", "")


def test_a_rename_is_refused_while_a_calc_it_would_rename_is_about_to_run(store):
    store.apply_ops([sys_op(name="mol"), calc_op("mol-opt"), calc_op("mol-sp")])
    store.set_running(True)
    store._engine = type("E", (), {"active_names": {"mol-sp"}})()
    try:
        with pytest.raises(QueueOpError, match="about to run"):
            store.apply_ops([{"op": "update_system", "id": "s1", "patch": {"name": "w"}}])
    finally:
        store._engine = None
        store.set_running(False)
    assert store.names() == ["mol-opt", "mol-sp"]


def test_a_rename_onto_an_existing_name_is_refused(store):
    store.apply_ops([sys_op(name="mol"), calc_op("mol-sp"),
                     calc_op("w-sp", system="")])
    with pytest.raises(QueueOpError, match="'w-sp' already exists"):
        store.apply_ops([{"op": "update_system", "id": "s1", "patch": {"name": "w"}}])


def test_a_rename_to_an_orca_hostile_name_is_refused(store):
    store.apply_ops([sys_op(name="mol"), calc_op("mol-sp")])
    with pytest.raises(QueueOpError, match="space"):
        store.apply_ops([{"op": "update_system", "id": "s1", "patch": {"name": "my mol"}}])


def test_charge_and_multiplicity_propagate_to_the_editable_calcs_of_the_lane(store):
    store.apply_ops([sys_op(), calc_op("a"), calc_op("b"), calc_op("x", system="")])
    store.get("a").state = CalcState.DONE
    store.apply_ops([{"op": "update_system", "id": "s1",
                      "patch": {"charge": 1, "multiplicity": 2}}])
    assert (store.get("b").charge, store.get("b").multiplicity) == (1, 2)
    assert store.get("a").charge == 0              # frozen (P24)
    assert store.get("x").charge == 0              # another lane


def test_update_system_refuses_an_unknown_key(store):
    store.apply_ops([sys_op()])
    with pytest.raises(QueueOpError, match="cannot change: id"):
        store.apply_ops([{"op": "update_system", "id": "s1", "patch": {"id": "s9"}}])


def test_remove_system_takes_its_unstarted_calcs_and_refuses_a_started_one(store):
    store.apply_ops([sys_op(), calc_op("a"), calc_op("b")])
    store.get("a").state = CalcState.DONE
    with pytest.raises(QueueOpError, match="has already run"):
        store.apply_ops([{"op": "remove_system", "id": "s1"}])
    # removing the finished row first (queue-list only) makes it removable
    store.apply_ops([{"op": "remove_calc", "name": "a"},
                     {"op": "remove_system", "id": "s1"}])
    assert store.names() == [] and store.systems() == []


def test_system_names_are_unique(store):
    store.apply_ops([sys_op()])
    with pytest.raises(QueueOpError, match="system named 'MOL'"):
        store.apply_ops([sys_op(sid="s2", name="MOL")])


# ---- xyz_file_entries: how a dropped / picked .xyz is read ----------------------------

XYZ = "2\nhydrogen\n" + H2 + "\n"


def _entries(tmp_path, files):
    paths = []
    for name, text in files:
        f = tmp_path / name
        f.write_text(text, encoding="utf-8")
        paths.append(f)
    return xyz_file_entries(paths)


def test_each_file_gives_its_stem_and_one_structure(tmp_path):
    (water,) = _entries(tmp_path, [("water.xyz", "\ufeff" + XYZ)])   # a BOM is fine
    assert (water["label"], water["name"], water["xyz"], water["error"]) == (
        "water.xyz", "water", H2, "")


def test_a_multi_frame_file_is_refused(tmp_path):
    (traj,) = _entries(tmp_path, [("traj.xyz", XYZ + XYZ)])
    assert "2 structures" in traj["error"] and traj["xyz"] == ""


def test_a_file_without_coordinates_is_refused(tmp_path):
    (empty,) = _entries(tmp_path, [("empty.xyz", "")])
    assert "No coordinates" in empty["error"]


def test_a_truncated_file_is_refused_not_half_read(tmp_path):
    (cut,) = _entries(tmp_path, [("cut.xyz", "3\n\n" + H2 + "\n")])
    assert "says 3 atoms" in cut["error"] and cut["xyz"] == ""


@pytest.mark.parametrize("comment, want", [
    ("charge=1 mult=2", (1, 2)),
    ("Charge: -1  Multiplicity: 2", (-1, 2)),
    ("anion chrg -2", (-2, None)),
    ("xtb opt, uhf=2", (None, 3)),          # unpaired electrons -> 2S+1
    ("mult 3 uhf 0", (None, 3)),            # a stated multiplicity wins over uhf
    ("energy: -76.4 converged", (None, None)),
    ("charge=+1 multiplicity=0", (1, None)),  # no molecule has multiplicity 0
    ("charge=99", (None, None)),            # nor that charge: not read, not clamped
    ("discharged multiple times", (None, None)),
])
def test_the_comment_line_may_state_charge_and_multiplicity(comment, want):
    assert xyz_charge_mult(f"2\n{comment}\n{H2}\n") == want


def test_a_bare_block_has_no_comment_line_to_read():
    assert xyz_charge_mult("charge=1 mult=2\n" + H2) == (None, None)
    assert xyz_charge_mult("") == (None, None)


def test_a_file_entry_carries_what_its_comment_line_states(tmp_path):
    ion, plain = _entries(tmp_path, [("ion.xyz", "2\ncharge=1 mult=2\n" + H2 + "\n"),
                                     ("plain.xyz", XYZ)])
    assert (ion["charge"], ion["multiplicity"], ion["xyz"]) == (1, 2, H2)
    assert (plain["charge"], plain["multiplicity"]) == (None, None)


def test_an_mlip_template_patch_is_applied_to_the_config_again(store):
    store.apply_ops([sys_op(), calc_op("m", kind="mlip_opt", set_name="MACE",
                                       raw_text="model MACE-OFF small\n")])
    assert store.get("m").config.mlip_model == "MACE-OFF small"
    store.apply_ops([{"op": "update_calc", "name": "m",
                      "calc": {"raw_text": "model MACE-OFF large\nnprocs 2\n"}}])
    m = store.get("m")
    assert (m.config.mlip_model, m.config.nprocs, m.is_raw) == ("MACE-OFF large", 2, False)


def test_mid_run_the_single_mutations_guard_after_like_a_reference(store):
    store.apply_ops([sys_op(), calc_op("mol-opt"), calc_op("mol-sp", after="mol-opt")])
    store._running = True
    try:
        with pytest.raises(ValueError, match="runs after it"):
            store.remove("mol-opt")
        with pytest.raises(ValueError, match="runs after 'gone'"):
            store.add(calc_from_dict({"name": "x", "kind": "sp", "xyz": H2,
                                      "after": "gone"}))
        with pytest.raises(ValueError, match="runs after it"):
            store.replace("mol-opt", calc_from_dict(
                {"name": "mol-opt2", "kind": "sp", "xyz": H2, "system": "s1"}))
    finally:
        store._running = False


# ---- a level's block: its kind is its Type ----------------------------------------

LEVEL = "! B3LYP def2-SVP {{JOB}}\n* xyz {{CHARGE}} {{MULT}}\n{{GEOMETRY}}\n*\n"


def test_a_level_block_refuses_a_kind_with_no_job_keyword(store):
    store.apply_ops([sys_op()])
    with pytest.raises(QueueOpError, match="cannot run as a 'general'"):
        store.apply_ops([calc_op("mol-x", kind="general", raw_text=LEVEL)])
    store.apply_ops([calc_op("mol-opt", kind="opt", raw_text=LEVEL)])
    with pytest.raises(QueueOpError, match="cannot run as a 'general'"):
        store.apply_ops([{"op": "update_calc", "name": "mol-opt", "calc": {"kind": "general"}}])
    assert store.get("mol-opt").kind == "opt"


def test_a_type_change_is_a_kind_patch_and_the_config_follows(store):
    store.apply_ops([sys_op(), calc_op("mol-opt", kind="opt", raw_text=LEVEL)])
    store.apply_ops([{"op": "update_calc", "name": "mol-opt", "calc": {"kind": "irc"}}])
    c = store.get("mol-opt")
    assert (c.kind, c.config.kind, c.raw_text, c.is_raw) == ("irc", "irc", LEVEL, True)


def test_a_restored_level_block_is_never_evicted_over_its_kind():
    d = {"name": "x", "kind": "tddft", "is_raw": True, "raw_text": LEVEL, "xyz": H2}
    assert calc_from_dict(d, restoring=True).kind == "tddft"


