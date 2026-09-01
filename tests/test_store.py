"""
Tests for orcamgr/state/store.py — the shared QueueStore and its
serialization layer (calc_from_dict / calc_to_dict / session persistence).

Contracts under test come from PRINCIPLES.md:
  * P24 — DONE is frozen; FAILED is locked; only EDITABLE_STATES may be
    edited/reordered; removal is allowed in every state except RUNNING.
  * P29 — the queue is live while it runs: editable-state rows may be
    added/removed/edited/reordered mid-run; the in-flight calc, non-editable
    states, clear, and reference integrity stay protected.
  * P32 — persistence never crashes the app (corrupt JSON degrades, corrupt
    entries are skipped item-by-item).
  * P33 — calc names are validated at the shared choke point (path-dangerous
    characters, '..', Windows reserved names; Unicode allowed).
  * P34 — untrusted numerics are coerced and clamped at deserialization.

No conftest.py: every fixture/helper lives in this file. All session I/O is
redirected to tmp_path by monkeypatching the store module's user_data_root.
"""

from __future__ import annotations

import json

import pytest

import orcamgr.state.store as store_mod
from orcamgr.core.queue import CalcState, Calculation, GeometrySource
from orcamgr.state.store import (
    EDITABLE_STATES,
    QueueStore,
    calc_from_dict,
    calc_from_session_dict,
    calc_to_dict,
    calc_to_session_dict,
    queue_needs_orca,
)


# ---- helpers / fixtures --------------------------------------------------

@pytest.fixture
def session_root(monkeypatch, tmp_path):
    """Redirect all session-file I/O (QueueStore autosave/load) to tmp_path.

    store.py binds user_data_root at import time (`from ..paths import
    user_data_root`), so the store module's own attribute must be patched —
    patching orcamgr.paths would not reach _session_file().
    """
    monkeypatch.setattr(store_mod, "user_data_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def store(session_root):
    return QueueStore()


def make_calc(name: str, kind: str = "sp", **overrides) -> Calculation:
    """Build a Calculation through the shared deserialization choke point,
    exactly as the bridge and the HTTP server do."""
    payload = {
        "name": name,
        "kind": kind,
        "charge": 0,
        "multiplicity": 1,
        "geometry_source": "direct",
        "xyz": "H 0.0 0.0 0.0",
        "config": {"kind": kind},
    }
    payload.update(overrides)
    return calc_from_dict(payload)


def add_with_state(store: QueueStore, name: str, state: CalcState,
                   kind: str = "sp") -> Calculation:
    calc = make_calc(name, kind=kind)
    store.add(calc)
    calc.state = state
    return calc


# ---- 1. name validation (P33) -------------------------------------------

@pytest.mark.parametrize("bad_char", list('\\/:*?"<>|'))
def test_name_with_path_dangerous_character_rejected(bad_char):
    with pytest.raises(ValueError):
        make_calc(f"calc{bad_char}x")


def test_name_containing_dotdot_rejected():
    with pytest.raises(ValueError):
        make_calc("..")
    with pytest.raises(ValueError):
        make_calc("a..b")


def test_name_ending_with_dot_rejected():
    with pytest.raises(ValueError):
        make_calc("trailing.")


@pytest.mark.parametrize("reserved", [
    "con", "CON", "aux", "nul", "prn", "com1", "com9", "lpt1", "lpt9",
    # reserved device names are reserved even with an extension on Windows
    "con.inp", "AUX.out",
])
def test_windows_reserved_device_names_rejected(reserved):
    with pytest.raises(ValueError):
        make_calc(reserved)


def test_empty_name_rejected():
    with pytest.raises(ValueError):
        make_calc("")
    with pytest.raises(ValueError):
        make_calc("   ")  # whitespace-only strips to empty


# A calc name is also the .inp file name ORCA is invoked with, and ORCA 6
# splits its own argument on whitespace before handing it to orca_startup: a
# space or an "&" gives "Cannot open input file <first word>" / "no input
# files" and an error termination in Startup — every run of that calculation,
# serial and parallel alike, and a failed calc is locked (P24). Measured on
# 6.1.1, where "(", ")", "\'", ",", "=" and ";" all run normally, which is why
# this is a two-character rule and not a safe-character list. The FOLDER may
# hold spaces: OrcaRunner.launch names the input relative to it.

@pytest.mark.parametrize("name", ["water opt", "a&b", "two  spaces", "tab\tname"])
def test_name_orca_cannot_open_rejected(name):
    with pytest.raises(ValueError):
        make_calc(name)


@pytest.mark.parametrize("name", ["water_opt", "water-opt", "a(b)", "a,b",
                                  "a=b", "a;b", "a\'b"])
def test_name_orca_can_open_accepted(name):
    assert make_calc(name).name == name


def test_unicode_korean_name_allowed():
    calc = make_calc("물분자_최적화")
    assert calc.name == "물분자_최적화"


def test_ordinary_ascii_name_allowed():
    assert make_calc("benzene_opt-1.step2").name == "benzene_opt-1.step2"


# ---- 2. calc_from_dict clamps and coercion (P34) -------------------------

def test_charge_clamped_to_plus_minus_100():
    assert make_calc("a", charge=1000).charge == 100
    assert make_calc("b", charge=-1000).charge == -100
    assert make_calc("c", charge=-100).charge == -100


def test_multiplicity_clamped_to_1_200():
    assert make_calc("a", multiplicity=0).multiplicity == 1
    assert make_calc("b", multiplicity=-5).multiplicity == 1
    assert make_calc("c", multiplicity=999).multiplicity == 200


def test_numeric_strings_coerced_to_int():
    calc = make_calc("a", charge="-7", multiplicity="3",
                     config={"kind": "sp", "nprocs": "8", "maxcore_mb": "512"})
    assert calc.charge == -7
    assert calc.multiplicity == 3
    assert calc.config.nprocs == 8
    assert calc.config.maxcore_mb == 512


def test_stepconfig_int_fields_clamped_to_documented_ranges():
    cfg = make_calc("a", config={
        "kind": "sp",
        "nprocs": 0,
        "maxcore_mb": 1,
        "max_iter": 0,
        "neb_nimages": 1,
        "tddft_nroots": 0,
    }).config
    assert cfg.nprocs == 1
    assert cfg.maxcore_mb == 64
    assert cfg.max_iter == 1
    assert cfg.neb_nimages == 2
    assert cfg.tddft_nroots == 1

    cfg = make_calc("b", config={
        "kind": "sp",
        "nprocs": 99999,
        "maxcore_mb": 10**9,
        "neb_nimages": 10**6,
    }).config
    assert cfg.nprocs == 1024
    assert cfg.maxcore_mb == 1_000_000
    assert cfg.neb_nimages == 200


def test_stepconfig_float_fields_clamped_and_nan_rejected():
    cfg = make_calc("a", config={
        "kind": "freq",
        "freq_temp_k": -50.0,
        "freq_pressure_atm": -1.0,
    }).config
    assert cfg.freq_temp_k == 0.0
    assert cfg.freq_pressure_atm == 0.0

    cfg = make_calc("b", config={"kind": "freq",
                                 "freq_temp_k": float("nan")}).config
    assert cfg.freq_temp_k == 298.15  # NaN falls back to the default


def test_non_numeric_stepconfig_field_falls_back_to_default():
    cfg = make_calc("a", config={"kind": "sp", "nprocs": "many",
                                 "freq_temp_k": "hot"}).config
    assert cfg.nprocs == 6
    assert cfg.freq_temp_k == 298.15


def test_unknown_keys_ignored_not_rejected():
    # forward compatibility (P34): unknown fields are filtered, not an error
    calc = calc_from_dict({
        "name": "fwd_compat",
        "kind": "sp",
        "xyz": "H 0 0 0",
        "config": {"kind": "sp", "nprocs": 2, "field_from_the_future": 42},
        "top_level_future_field": True,
    })
    assert calc.config.nprocs == 2
    assert not hasattr(calc.config, "field_from_the_future")


def test_chemistry_strings_pass_through_verbatim():
    # P34: defense for numbers, freedom for chemistry — no allowlist here
    cfg = make_calc("a", config={"kind": "sp",
                                 "functional": "MyCustomFunc-2077",
                                 "basis_set": "my-weird-basis"}).config
    assert cfg.functional == "MyCustomFunc-2077"
    assert cfg.basis_set == "my-weird-basis"


# ---- 3. EDITABLE_STATES and per-state locking (P24) ----------------------

def test_editable_states_are_exactly_pending_cancelled_blocked():
    assert EDITABLE_STATES == {
        CalcState.PENDING, CalcState.CANCELLED, CalcState.BLOCKED,
    }


@pytest.mark.parametrize("locked_state", [
    CalcState.DONE, CalcState.FAILED, CalcState.RUNNING,
])
def test_replace_rejects_non_editable_target(store, locked_state):
    add_with_state(store, "locked", locked_state)
    with pytest.raises(ValueError):
        store.replace("locked", make_calc("locked"))


@pytest.mark.parametrize("editable_state", sorted(EDITABLE_STATES))
def test_replace_resets_editable_calc_to_pending(store, editable_state):
    old = add_with_state(store, "job", editable_state)
    old.message = "stale"
    old.output_path = "C:/somewhere/job.out"
    assert store.replace("job", make_calc("job", kind="opt")) is True
    replaced = store.get("job")
    assert replaced.state == CalcState.PENDING
    assert replaced.message == ""
    assert replaced.result is None
    assert replaced.output_path == ""
    assert replaced.kind == "opt"


def test_replace_rejects_rename_colliding_with_other_entry(store):
    store.add(make_calc("a"))
    store.add(make_calc("b"))
    with pytest.raises(ValueError):
        store.replace("a", make_calc("b"))
    # renaming to a fresh name is fine
    assert store.replace("a", make_calc("c")) is True
    assert store.names() == ["c", "b"]


def test_reorder_rejects_non_editable_endpoints(store):
    store.add(make_calc("a"))
    add_with_state(store, "b", CalcState.DONE)
    store.add(make_calc("c"))
    with pytest.raises(ValueError):
        store.reorder(1, 2)  # from a DONE calc
    with pytest.raises(ValueError):
        store.reorder(0, 1)  # onto a DONE calc
    assert store.reorder(0, 2) is True
    assert store.names() == ["b", "c", "a"]


def test_reorder_same_index_is_a_noop_and_bounds_are_checked(store):
    store.add(make_calc("a"))
    store.add(make_calc("b"))
    assert store.reorder(0, 0) is False
    with pytest.raises(ValueError):
        store.reorder(0, 99)
    with pytest.raises(ValueError):
        store.reorder(-1, 0)


def test_remove_rejects_only_running(store):
    # P24: the x control stays available in every state except RUNNING —
    # removing DONE/FAILED deletes only the queue entry, never disk.
    add_with_state(store, "done", CalcState.DONE)
    add_with_state(store, "failed", CalcState.FAILED)
    add_with_state(store, "blocked", CalcState.BLOCKED)
    add_with_state(store, "running", CalcState.RUNNING)
    assert store.remove("done") is True
    assert store.remove("failed") is True
    assert store.remove("blocked") is True
    with pytest.raises(ValueError):
        store.remove("running")
    assert store.names() == ["running"]


def test_remove_unknown_name_returns_false(store):
    assert store.remove("no_such_calc") is False


# ---- 4. live queue while running (P29) -----------------------------------
# The engine walks the store's own list, so editable-state rows may be
# added/removed/edited/reordered mid-run; what stays protected: the in-flight
# calc, non-editable states, clear, and reference integrity (a mid-run
# mutation bypasses the pre-run dangling-reference screen).

def test_editable_mutations_allowed_while_run_flag_is_set(store):
    store.add(make_calc("a"))
    store.add(make_calc("b"))
    store.set_running(True)
    store.add(make_calc("c"))                     # add lands in the live queue
    store.replace("b", make_calc("b"))            # pending rows stay editable
    store.reorder(0, 1)                           # and reorderable
    assert store.remove("c") is True              # and removable
    with pytest.raises(ValueError):
        store.clear()                             # clear stays blocked
    assert store.names() == ["b", "a"]


def test_non_editable_states_immune_while_running(store):
    add_with_state(store, "hot", CalcState.RUNNING)
    add_with_state(store, "done", CalcState.DONE)
    add_with_state(store, "dead", CalcState.FAILED)
    store.set_running(True)
    for name in ("hot", "done", "dead"):
        with pytest.raises(ValueError):
            store.remove(name)
        with pytest.raises(ValueError):
            store.replace(name, make_calc(name))
    store.set_running(False)
    # idle-time semantics unchanged: anything but RUNNING can be removed
    assert store.remove("done") is True
    assert store.remove("dead") is True
    with pytest.raises(ValueError):
        store.remove("hot")


def test_mid_run_add_or_edit_with_dangling_reference_refused(store):
    store.add(make_calc("base"))
    store.set_running(True)
    with pytest.raises(ValueError):
        store.add(make_calc("orphan", geometry_source="reference",
                            ref_name="ghost", xyz=""))
    with pytest.raises(ValueError):   # self-reference is dangling too
        store.add(make_calc("selfy", geometry_source="reference",
                            ref_name="selfy", xyz=""))
    with pytest.raises(ValueError):
        store.replace("base", make_calc("base", geometry_source="reference",
                                        ref_name="ghost", xyz=""))
    store.set_running(False)
    # idle-time laxness unchanged: the pre-run screen re-checks references
    store.add(make_calc("orphan", geometry_source="reference",
                        ref_name="ghost", xyz=""))
    assert "orphan" in store.names()


def test_mid_run_remove_or_rename_of_referenced_calc_refused(store):
    store.add(make_calc("parent"))
    store.add(make_calc("child", geometry_source="reference",
                        ref_name="parent", xyz=""))
    store.set_running(True)
    with pytest.raises(ValueError):
        store.remove("parent")
    with pytest.raises(ValueError):   # renaming would dangle the child too
        store.replace("parent", make_calc("parent2"))
    store.set_running(False)
    assert store.remove("parent") is True   # idle-time semantics unchanged


def test_mid_run_mutation_of_engine_active_calc_refused(store):
    store.add(make_calc("picked"))
    store.add(make_calc("idle"))
    store.set_running(True)

    class _Engine:
        # what QueueEngine.active_names exposes for a picked-but-not-stamped calc
        active_names = {"picked"}
    store._engine = _Engine()
    with pytest.raises(ValueError):
        store.remove("picked")
    with pytest.raises(ValueError):
        store.replace("picked", make_calc("picked"))
    assert store.remove("idle") is True   # other editable rows stay mutable


# ---- 5. duplicate names / version counter --------------------------------

def test_duplicate_name_add_rejected(store):
    store.add(make_calc("dup"))
    with pytest.raises(ValueError):
        store.add(make_calc("dup"))
    assert store.names() == ["dup"]


def test_duplicate_name_add_rejected_case_insensitive(store):
    # calc.name is an on-disk folder name and Windows resolves "water" and
    # "Water" to the SAME folder — both being accepted would silently share
    # one .out between two calculations.
    store.add(make_calc("water"))
    with pytest.raises(ValueError):
        store.add(make_calc("Water"))
    with pytest.raises(ValueError):
        store.add(make_calc("WATER"))
    assert store.names() == ["water"]


def test_replace_rename_collision_rejected_case_insensitive(store):
    store.add(make_calc("water"))
    store.add(make_calc("other"))
    with pytest.raises(ValueError):
        store.replace("other", make_calc("WATER"))
    # renaming only by case is a collision with itself? No — the target IS the
    # entry being replaced, so a case-only rename of the same row is allowed
    store.replace("other", make_calc("Other"))
    assert store.names() == ["water", "Other"]


def test_version_increases_on_every_mutation(store):
    versions = [store.version()]

    store.add(make_calc("a"))
    versions.append(store.version())
    store.add(make_calc("b"))
    versions.append(store.version())
    store.reorder(0, 1)
    versions.append(store.version())
    store.replace("a", make_calc("a2"))
    versions.append(store.version())
    store.remove("b")
    versions.append(store.version())
    store.touch()
    versions.append(store.version())
    store.set_running(True)
    versions.append(store.version())
    store.set_running(False)
    store.clear()
    versions.append(store.version())

    assert versions == sorted(versions)
    assert len(set(versions)) == len(versions), "each mutation must bump"


# ---- 6. session persistence (P32) ----------------------------------------

def test_session_roundtrip_restores_queue_and_runtime_state(session_root):
    store = QueueStore()
    store.add(make_calc("opt1", kind="opt", charge=-1, multiplicity=2,
                        config={"kind": "opt", "functional": "B3LYP",
                                "basis_set": "def2-SVP", "nprocs": 4}))
    store.add(make_calc("freq1", kind="freq", geometry_source="reference",
                        ref_name="opt1", xyz=""))
    done = store.get("opt1")
    done.state = CalcState.DONE
    done.message = "Completed."
    done.output_path = str(session_root / "opt1" / "opt1.out")
    store.touch()  # persist the in-place state change

    assert (session_root / "session.json").exists()

    fresh = QueueStore()
    fresh.load_session()
    assert fresh.names() == ["opt1", "freq1"]
    a = fresh.get("opt1")
    assert a.state == CalcState.DONE
    assert a.message == "Completed."
    assert a.charge == -1
    assert a.multiplicity == 2
    assert a.config.functional == "B3LYP"
    assert a.config.nprocs == 4
    b = fresh.get("freq1")
    assert b.state == CalcState.PENDING
    assert b.geometry_source == GeometrySource.REFERENCE
    assert b.ref_name == "opt1"


def test_corrupt_session_json_degrades_to_empty_queue(session_root):
    (session_root / "session.json").write_text("{not json at all",
                                               encoding="utf-8")
    store = QueueStore()
    store.load_session()  # must not raise
    assert store.names() == []


def test_missing_session_file_is_a_noop(session_root):
    store = QueueStore()
    store.load_session()
    assert store.names() == []


@pytest.mark.parametrize("content", ["null", "[]", "42", '"a string"'])
def test_valid_json_non_dict_session_degrades_to_empty_queue(session_root, content):
    # valid JSON that isn't an object used to raise AttributeError on
    # data.get() — a self-perpetuating startup crash until the user deleted
    # session.json by hand (P32; same guard as Settings.load)
    (session_root / "session.json").write_text(content, encoding="utf-8")
    store = QueueStore()
    store.load_session()  # must not raise
    assert store.names() == []


def test_corrupt_session_entry_skipped_others_restored(session_root):
    good = calc_to_session_dict(make_calc("good", kind="sp"))
    bad_name = dict(good, name="bad|name")     # fails name validation
    not_a_dict = "junk-entry"                  # fails deserialization entirely
    payload = {"schema": 1,
               "calculations": [bad_name, good, not_a_dict]}
    (session_root / "session.json").write_text(
        json.dumps(payload), encoding="utf-8")

    store = QueueStore()
    store.load_session()  # must not raise
    assert store.names() == ["good"]


def test_running_calc_with_dead_process_demoted_to_failed_on_restore(session_root):
    d = calc_to_session_dict(make_calc("orphan", kind="opt"))
    d["state"] = "running"
    d["pid"] = None          # no persisted process identity -> definitely gone
    d["create_time"] = None
    (session_root / "session.json").write_text(
        json.dumps({"schema": 1, "calculations": [d]}), encoding="utf-8")

    store = QueueStore()
    store.load_session()
    c = store.get("orphan")
    assert c.state == CalcState.FAILED
    assert "Interrupted" in c.message
    assert store.has_live_running() is False


def test_running_calc_with_live_process_stays_running_for_reattach(session_root):
    psutil = pytest.importorskip("psutil")
    me = psutil.Process()  # the test process itself is certainly alive
    d = calc_to_session_dict(make_calc("live", kind="opt"))
    d["state"] = "running"
    d["pid"] = me.pid
    d["create_time"] = me.create_time()
    (session_root / "session.json").write_text(
        json.dumps({"schema": 1, "calculations": [d]}), encoding="utf-8")

    store = QueueStore()
    store.load_session()
    c = store.get("live")
    assert c.state == CalcState.RUNNING
    assert c.pid == me.pid
    assert store.has_live_running() is True


def test_unknown_session_state_string_degrades_to_pending(session_root):
    d = calc_to_session_dict(make_calc("odd"))
    d["state"] = "state_from_the_future"
    (session_root / "session.json").write_text(
        json.dumps({"schema": 1, "calculations": [d]}), encoding="utf-8")
    store = QueueStore()
    store.load_session()
    assert store.get("odd").state == CalcState.PENDING


# ---- 7. queue_needs_orca --------------------------------------------------

def test_all_mlip_queue_needs_no_orca():
    calcs = [make_calc("m1", kind="mlip_opt"), make_calc("m2", kind="mlip_opt")]
    assert queue_needs_orca(calcs) is False


def test_pending_orca_calc_requires_orca():
    calcs = [make_calc("m1", kind="mlip_opt"), make_calc("o1", kind="sp")]
    assert queue_needs_orca(calcs) is True


def test_done_and_failed_orca_calcs_never_force_an_orca_path():
    # P24: DONE is never recomputed and FAILED is locked — neither will ever
    # launch ORCA, so a stale ORCA calc must not block an all-MLIP run.
    done = make_calc("d", kind="opt")
    done.state = CalcState.DONE
    failed = make_calc("f", kind="sp")
    failed.state = CalcState.FAILED
    mlip = make_calc("m", kind="mlip_opt")
    assert queue_needs_orca([done, failed, mlip]) is False
    assert queue_needs_orca([done, failed]) is False


def test_cancelled_orca_calc_still_requires_orca():
    # CANCELLED re-runs on the next Run (P24), so it will launch ORCA
    c = make_calc("c", kind="sp")
    c.state = CalcState.CANCELLED
    assert queue_needs_orca([c]) is True


def test_empty_queue_needs_no_orca():
    assert queue_needs_orca([]) is False


# ---- 8. serialization roundtrips ------------------------------------------

def test_session_dict_roundtrip_preserves_every_field():
    calc = calc_from_dict({
        "name": "roundtrip",
        "kind": "tddft",
        "charge": -2,
        "multiplicity": 3,
        "geometry_source": "reference",
        "xyz": "",
        "ref_name": "opt1",
        "is_raw": True,
        "raw_text": "! B3LYP def2-SVP\n* xyz -2 3\n{{GEOMETRY}}\n*",
        "config": {"kind": "tddft", "functional": "CAM-B3LYP",
                   "basis_set": "def2-TZVP", "nprocs": 12,
                   "tddft_nroots": 25, "tddft_tda": True,
                   "solvation": {"enabled": True, "solvent": "water"}},
    })
    calc.state = CalcState.DONE
    calc.message = "ok"
    calc.output_path = "C:/ws/roundtrip/roundtrip.out"
    calc.pid = 4242
    calc.create_time = 1234567.89

    restored = calc_from_session_dict(calc_to_session_dict(calc))
    # comparing the full-fidelity serialized form compares every wire field
    assert calc_to_session_dict(restored) == calc_to_session_dict(calc)
    assert restored.state == CalcState.DONE
    assert restored.geometry_source == GeometrySource.REFERENCE
    assert restored.config.tddft_nroots == 25
    assert restored.config.tddft_tda is True
    assert restored.pid == 4242


def test_calc_to_dict_summary_reflects_calc_fields():
    calc = make_calc("summ", kind="opt", charge=1, multiplicity=2,
                     config={"kind": "opt", "scf_convergence": "VeryTightSCF"})
    d = calc_to_dict(calc)
    assert d["name"] == "summ"
    assert d["kind"] == "opt"
    assert d["charge"] == 1
    assert d["multiplicity"] == 2
    assert d["geometry_source"] == "direct"
    assert d["state"] == "pending"
    assert d["scf_convergence"] == "VeryTightSCF"
    assert "q1" in d["meta"] and "m2" in d["meta"]


def test_calc_to_dict_meta_shows_model_not_charge_for_mlip():
    calc = make_calc("mace", kind="mlip_opt",
                     config={"kind": "mlip_opt",
                             "mlip_model": "MACE-OFF medium"})
    meta = calc_to_dict(calc)["meta"]
    assert "MACE-OFF medium" in meta
    assert "q0" not in meta


def test_calc_to_dict_backend_detail_fields_are_explicit_and_kind_scoped():
    # The desktop renders these escaped — they must arrive as raw fields, not
    # only inside the pre-joined meta line (which embeds user-typed ref names).
    mlip = calc_to_dict(make_calc(
        "mace2", kind="mlip_opt",
        config={"kind": "mlip_opt", "mlip_model": "MACE-OFF small"}))
    assert mlip["mlip_model"] == "MACE-OFF small"
    assert mlip["crest_method"] == ""

    crest = calc_to_dict(make_calc(
        "conf", kind="crest_conf",
        config={"kind": "crest_conf", "crest_method": "gfnff"}))
    assert crest["crest_method"] == "gfnff"
    assert crest["mlip_model"] == ""

    orca = calc_to_dict(make_calc("plain", kind="opt", config={"kind": "opt"}))
    assert orca["mlip_model"] == ""
    assert orca["crest_method"] == ""


def test_log_trim_preferentially_retains_web_lines(store):
    # ORCA stdout floods the ring; console-captured "[web] ..." lines are the
    # diagnostic channel and must survive the 5000 -> 4000 trim.
    store.append_log("[web] level=error TypeError: boom", "err")
    for i in range(5100):
        store.append_log(f"orca stdout line {i}")
    payload = store.log_since(0)
    msgs = [ln["msg"] for ln in payload["lines"]]
    assert any(m.startswith("[web] ") for m in msgs), "web line was evicted"
    assert len(msgs) <= 4501          # still bounded (4000 tail + <=500 web + growth)
    seqs = [ln["seq"] for ln in payload["lines"]]
    assert seqs == sorted(seqs)       # retention must preserve seq order


# ---- reconcile: finished / stopped while ORCAdesk was closed -----------------

def test_reconcile_running_orca_with_complete_out_is_judged_done(tmp_path):
    # The DONE branch requires output_path — persisted at launch since the
    # queue.py fix; without it every finished-while-closed run locked FAILED.
    calc = make_calc("ghost", kind="sp")
    calc.state = CalcState.RUNNING
    calc.pid = None                       # process gone
    out = tmp_path / "ghost" / "ghost.out"
    out.parent.mkdir(parents=True)
    out.write_text(
        "FINAL SINGLE POINT ENERGY       -1.000000000000\n"
        "                             ****ORCA TERMINATED NORMALLY****\n",
        encoding="utf-8")
    calc.output_path = str(out)
    store_mod.reconcile_calcs([calc])
    assert calc.state == CalcState.DONE
    assert "finished while ORCAdesk was closed" in calc.message


def test_reconcile_running_mlip_without_result_is_cancelled_not_failed():
    # An MLIP worker never survives shutdown (detach terminates it), so a
    # RUNNING mlip calc in a restored session was stopped deliberately:
    # CANCELLED (re-runnable), never the locked FAILED.
    calc = make_calc("mace", kind="mlip_opt",
                     config={"kind": "mlip_opt", "mlip_model": "MACE-OFF medium"})
    calc.state = CalcState.RUNNING
    store_mod.reconcile_calcs([calc])
    assert calc.state == CalcState.CANCELLED
    assert calc.message == "Stopped when ORCAdesk closed."


def test_reconcile_running_mlip_with_complete_result_is_done(tmp_path):
    # the worker raced to completion just as the app closed — judge DONE
    calc = make_calc("mace2", kind="mlip_opt",
                     config={"kind": "mlip_opt", "mlip_model": "MACE-OFF medium"})
    calc.state = CalcState.RUNNING
    rj = tmp_path / "mace2" / "mace2.mlip.json"
    rj.parent.mkdir(parents=True)
    rj.write_text(json.dumps({
        "converged": True, "task": "opt", "energy_ev": -100.0,
        "geometry": [["O", 0.0, 0.0, 0.0], ["H", 0.0, 0.0, 0.96]],
    }), encoding="utf-8")
    calc.output_path = str(rj)
    store_mod.reconcile_calcs([calc])
    assert calc.state == CalcState.DONE


# ---- find_dangling_reference (shared pre-run check, P4) ----------------------

def test_find_dangling_reference_shared_helper():
    a = make_calc("a")
    b = make_calc("b", geometry_source="reference", ref_name="a", xyz="")
    assert store_mod.find_dangling_reference([a, b]) is None
    c = make_calc("c", geometry_source="reference", ref_name="ghost", xyz="")
    msg = store_mod.find_dangling_reference([a, c])
    assert msg is not None
    assert "ghost" in msg and "'c'" in msg


# ---- phone auth token --------------------------------------------------------

def test_check_token_matches_only_the_pin(store):
    pin = store.token
    assert store.check_token(pin) is True
    assert store.check_token("000000" if pin != "000000" else "111111") is False
    assert store.check_token("") is False
    assert store.check_token(None) is False


def test_check_token_non_ascii_is_denied_not_a_crash(store):
    # hmac.compare_digest on str raises TypeError for non-ASCII input; the
    # token comes straight from an HTTP header/query param, so a stray
    # non-ASCII char must be an auth failure, not an HTTP 500.
    assert store.check_token("한글토큰") is False
    assert store.check_token("pin·123") is False


# ---- calc_from_dict trust boundary: wrong-typed payload values ---------------

def test_calc_from_dict_non_string_name_is_valueerror():
    # every caller (bridge add_calc / POST /api/queue) catches ValueError and
    # turns it into an {"error"} envelope / HTTP 400 — an AttributeError from
    # .strip() would escape the slot instead
    for bad in (123, None, ["x"], {"n": 1}):
        with pytest.raises(ValueError):
            calc_from_dict({"name": bad, "kind": "sp"})


def test_calc_from_dict_infinite_charge_is_valueerror():
    # JSON 1e999 parses to float("inf"); int(inf) raises OverflowError which
    # no caller catches — it must surface as the standard ValueError instead
    with pytest.raises(ValueError):
        calc_from_dict({"name": "x", "charge": float("inf")})
    with pytest.raises(ValueError):
        calc_from_dict({"name": "x", "multiplicity": float("-inf")})


def test_calc_from_dict_non_string_passthrough_fields_degrade():
    c = calc_from_dict({"name": "x", "kind": 7, "xyz": 1,
                        "ref_name": ["a"], "raw_text": {"t": 1}})
    assert isinstance(c.kind, str)
    assert c.xyz == "" and c.ref_name == "" and c.raw_text == ""


# ---- session restore: wrong-typed runtime fields + name dedup (P32) ----------

def test_session_wrong_typed_pid_restores_and_reconciles(session_root):
    d = calc_to_session_dict(make_calc("weird", kind="opt"))
    d["state"] = "running"
    d["pid"] = [1]              # wrong-typed (hand-edited/corrupted session)
    d["create_time"] = "soon"
    (session_root / "session.json").write_text(
        json.dumps({"schema": 1, "calculations": [d]}), encoding="utf-8")
    store = QueueStore()
    store.load_session()        # must not raise (TypeError used to escape
    #                             process_matches via reconcile_calcs)
    assert store.names() == ["weird"]
    assert store.get("weird").state == CalcState.FAILED


def test_session_device_output_path_does_not_hang_startup(session_root):
    # Path("CON").exists() is True on Windows and open("CON") blocks on the
    # console forever — reconciliation must skip non-regular files
    d = calc_to_session_dict(make_calc("dev", kind="opt"))
    d["state"] = "running"
    d["pid"] = 999999997
    d["create_time"] = 1.0
    d["output_path"] = "CON"
    (session_root / "session.json").write_text(
        json.dumps({"schema": 1, "calculations": [d]}), encoding="utf-8")
    store = QueueStore()
    store.load_session()        # must return, not hang
    assert store.get("dev").state == CalcState.FAILED


def test_session_restore_dedups_case_colliding_names(session_root):
    # a session written by a pre-uniqueness build (or merged externally) may
    # hold "water" and "Water" — one on-disk folder on Windows; the restore
    # path must uphold the same case-insensitive invariant add() enforces
    d1 = calc_to_session_dict(make_calc("water", kind="sp"))
    d2 = calc_to_session_dict(make_calc("Water", kind="opt"))
    d3 = calc_to_session_dict(make_calc("water", kind="freq"))
    (session_root / "session.json").write_text(
        json.dumps({"schema": 1, "calculations": [d1, d2, d3]}), encoding="utf-8")
    store = QueueStore()
    store.load_session()
    assert store.names() == ["water"]          # first occurrence wins
    assert store.get("water").kind == "sp"


def test_find_dangling_reference_skips_locked_states():
    # FAILED calcs are locked (P24) and DONE results are frozen — neither will
    # run again, so a dangling ref on them must not veto the whole run (e.g. a
    # FAILED calc whose referenced parent was later removed)
    dead = make_calc("dead", geometry_source="reference", ref_name="gone", xyz="")
    dead.state = CalcState.FAILED
    ok = make_calc("ok")
    assert store_mod.find_dangling_reference([dead, ok]) is None
    pending = make_calc("pend", geometry_source="reference", ref_name="gone", xyz="")
    assert store_mod.find_dangling_reference([pending, ok]) is not None
