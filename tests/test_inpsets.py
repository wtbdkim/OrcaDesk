"""Inp sets (state/inpsets.py): the queue editor's saved input templates.

An ORCA set is a level of theory: a whole input with {{JOB}} on its ! line
(filled from each block's kind at launch) and {{GEOMETRY}}; an MLIP set is
`key value` lines. Seeded once from ORCAdesk's own defaults (StepConfig +
build_input, at the user's %pal / %maxcore), stored in
user_data_root()/inp_sets.json (schema 2); a schema-1 file (one set per job
kind) is backed up once and migrated. Every test passes ``root`` explicitly,
so nothing touches the real user data.
"""

from __future__ import annotations

import json

import pytest

from orcamgr.core.input_generator import StepConfig, build_input
from orcamgr.state import inpsets
from orcamgr.state.inpsets import (SET_KINDS,
                                   convert_legacy_text, default_kind_after,
                                   delete_set, load_sets, migrate_sets,
                                   mlip_config_from_text, save_set,
                                   validate_set)

LEVEL = "! r2SCAN-3c {{JOB}}\n* xyz 0 1\n{{GEOMETRY}}\n*\n"


def test_the_first_load_seeds_one_orca_level_and_the_mlip_set(tmp_path):
    sets = load_sets(tmp_path, nprocs=8, maxcore_mb=3000)
    assert [s["kind"] for s in sets] == list(SET_KINDS) == ["ORCA", "MLIP"]
    assert inpsets.sets_file(tmp_path).exists()
    orca, mlip = sets
    assert orca["name"] == "wB97X-D4 / def2-TZVP"
    assert orca["text"] == ("! wB97X-D4 def2-TZVP {{JOB}}\n%maxcore 3000\n"
                            "%pal nprocs 8 end\n* xyz {{CHARGE}} {{MULT}}\n"
                            "{{GEOMETRY}}\n*\n")
    # a level carries no job and none of the job's blocks
    assert "%geom" not in orca["text"] and "%irc" not in orca["text"]
    assert mlip_config_from_text(mlip["text"])["mlip_model"] == mlip["name"]
    # and the seeds are what a second load reads back
    assert load_sets(tmp_path, nprocs=1, maxcore_mb=100) == sets
    assert json.loads(inpsets.sets_file(tmp_path).read_text(encoding="utf-8"))["schema"] == 2


def test_save_mints_an_id_and_replace_keeps_it(tmp_path):
    s = save_set({"name": "r2SCAN-3c", "kind": "ORCA", "text": LEVEL}, tmp_path)
    assert s["id"].startswith("set-")
    again = save_set({**s, "name": "r2SCAN-3c tight"}, tmp_path)
    assert again["id"] == s["id"]
    names = [x["name"] for x in load_sets(tmp_path) if x["id"] == s["id"]]
    assert names == ["r2SCAN-3c tight"]


def test_a_name_is_unique_within_its_kind_only(tmp_path):
    """A block names its set by (kind, name), so a second ORCA set of one
    name would make that ambiguous; an MLIP set may share it."""
    a = save_set({"name": "mine", "kind": "ORCA", "text": LEVEL}, tmp_path)
    with pytest.raises(ValueError, match="already an ORCA set named 'mine'"):
        save_set({"name": "MINE", "kind": "ORCA", "text": LEVEL}, tmp_path)
    save_set({"name": "mine", "kind": "MLIP", "text": "model x\n"}, tmp_path)
    assert save_set({**a, "text": LEVEL + "\n"}, tmp_path)["id"] == a["id"]   # itself


def test_seed_texts_are_the_seeds_by_kind():
    texts = inpsets.seed_texts(nprocs=3, maxcore_mb=1000)
    assert list(texts) == list(SET_KINDS)
    assert "%pal nprocs 3 end" in texts["ORCA"] and "{{JOB}}" in texts["ORCA"]
    assert texts["MLIP"].startswith("model ")


def test_delete_removes_and_an_emptied_list_stays_empty(tmp_path):
    for s in load_sets(tmp_path):
        assert delete_set(s["id"], tmp_path)
    assert load_sets(tmp_path) == []               # not re-seeded
    assert delete_set("nope", tmp_path) is False


@pytest.mark.parametrize("bad, msg", [
    ({"name": "x", "kind": "ORCA", "text": "! {{JOB}}\n* xyz 0 1\nH 0 0 0\n*\n"}, "GEOMETRY"),
    # a level without {{JOB}} would run the one job its text names, whatever
    # the block's Type says
    ({"name": "x", "kind": "ORCA", "text": "! B3LYP SP\n* xyz 0 1\n{{GEOMETRY}}\n*\n"}, "JOB"),
    # ... and it has to be where the job keyword goes, on the ! line
    ({"name": "x", "kind": "ORCA", "text": "! B3LYP\n%base \"{{JOB}}\"\n{{GEOMETRY}}"}, "JOB"),
    ({"name": "x", "kind": "Opt", "text": LEVEL}, "kind"),     # schema 1's kinds are gone
    ({"name": "", "kind": "ORCA", "text": LEVEL}, "name"),
    ({"name": "x", "kind": "MLIP", "text": "modle MACE\n"}, "unknown key"),
    ({"name": "x", "kind": "MLIP", "text": "device tpu\n"}, "device"),
    ({"id": "../x", "name": "x", "kind": "ORCA", "text": LEVEL}, "id"),
    ("not an object", "object"),
])
def test_a_set_that_does_not_validate_is_refused(tmp_path, bad, msg):
    with pytest.raises(ValueError, match=msg):
        save_set(bad, tmp_path)
    with pytest.raises(ValueError):
        validate_set(bad)


def test_a_valid_level_may_name_its_job_anywhere_on_a_bang_line():
    s = validate_set({"name": "x", "kind": "ORCA",
                      "text": "! B3LYP def2-SVP\n! {{JOB}} TightSCF\n* xyz 0 1\n{{GEOMETRY}}\n*\n"})
    assert s["kind"] == "ORCA"


def test_an_unreadable_file_reads_as_the_seeds_without_being_overwritten(tmp_path):
    f = inpsets.sets_file(tmp_path)
    f.write_text("{ not json", encoding="utf-8")
    assert [s["kind"] for s in load_sets(tmp_path)] == list(SET_KINDS)
    assert f.read_text(encoding="utf-8") == "{ not json"
    assert not (tmp_path / inpsets.BACKUP_NAME).exists()


def test_a_bad_entry_in_the_file_is_dropped_alone(tmp_path):
    good = {"id": "g", "name": "ok", "kind": "ORCA", "text": LEVEL}
    inpsets.sets_file(tmp_path).write_text(json.dumps(
        {"schema": 2, "sets": [good, {"id": "b", "kind": "ORCA"}, good]}), encoding="utf-8")
    assert load_sets(tmp_path) == [good]


def test_mlip_text_maps_onto_the_config_fields():
    assert mlip_config_from_text(
        "# a comment\nmodel MACE-OFF large\ndevice CUDA\nnprocs 99999\nenv e1\n") == {
        "mlip_model": "MACE-OFF large", "mlip_device": "cuda", "nprocs": 1024,
        "mlip_env_id": "e1"}


def test_the_natural_next_step_a_level_runs():
    """What a level dropped on the + slot runs before the user picks another
    Type: Opt to start a row, Freq after Opt, TS Freq after OptTS, IRC after a
    TS frequency, SP after an IRC (the -F/-B pair) and after anything else."""
    assert [default_kind_after(k) for k in
            ("", "mlip_opt", "opt", "ts_opt", "ts_freq", "ts_opt_freq", "irc",
             "freq", "sp", "opt_freq", "crest_conf")] == [
        "opt", "opt", "freq", "ts_freq", "irc", "irc", "sp", "sp", "sp", "sp", "sp"]


# ---- schema 1 -> 2 -----------------------------------------------------------

def _v1_seeds(nprocs: int = 6, maxcore_mb: int = 2400) -> "list[dict]":
    """schema 1's seeds, built the way 0.11.0 built them: one per job kind."""
    common = dict(nprocs=nprocs, maxcore_mb=maxcore_mb)
    cfgs = [("Opt", StepConfig(kind="opt", calculation_type="TightOpt", **common)),
            ("OptTS", StepConfig(kind="ts_opt", calculation_type="OptTS", **common)),
            ("Freq", StepConfig(kind="freq", calculation_type="Freq", **common)),
            ("IRC", StepConfig(kind="irc", calculation_type="IRC", irc_init_hess="read",
                               irc_hess_file="{{HESS}}", **common)),
            ("SP", StepConfig(kind="sp", calculation_type="SP", **common))]
    out = [{"id": "seed-mlip", "name": "MACE-OFF medium", "kind": "MLIP",
            "text": f"model MACE-OFF medium\ndevice auto\nnprocs {nprocs}\n"}]
    out += [{"id": f"seed-{k.lower()}", "name": "wB97X-D4 / def2-TZVP", "kind": k,
             "text": build_input(c, "{{GEOMETRY}}", "{{CHARGE}}", "{{MULT}}")}
            for k, c in cfgs]
    return out


def _write_v1(tmp_path, sets) -> bytes:
    raw = json.dumps({"schema": 1, "sets": sets}).encode("utf-8")
    inpsets.sets_file(tmp_path).write_bytes(raw)
    return raw


def test_the_seeded_v1_library_becomes_one_level(tmp_path):
    raw = _write_v1(tmp_path, _v1_seeds(8, 3000))
    sets = load_sets(tmp_path)
    assert [(s["id"], s["kind"]) for s in sets] == [("seed-mlip", "MLIP"), ("seed-opt", "ORCA")]
    level = sets[1]
    assert level["name"] == "wB97X-D4 / def2-TZVP"
    assert level["text"] == ("! wB97X-D4 def2-TZVP {{JOB}}\n%maxcore 3000\n"
                             "%pal nprocs 8 end\n* xyz {{CHARGE}} {{MULT}}\n"
                             "{{GEOMETRY}}\n*\n")
    # backed up byte for byte, and the file is schema 2 now
    assert (tmp_path / inpsets.BACKUP_NAME).read_bytes() == raw
    stored = json.loads(inpsets.sets_file(tmp_path).read_text(encoding="utf-8"))
    assert stored["schema"] == 2 and stored["sets"] == sets
    assert load_sets(tmp_path) == sets               # read back, not migrated twice


def test_the_backup_is_written_once_and_never_overwritten(tmp_path):
    backup = tmp_path / inpsets.BACKUP_NAME
    backup.write_bytes(b"an earlier backup")
    _write_v1(tmp_path, _v1_seeds())
    load_sets(tmp_path)
    assert backup.read_bytes() == b"an earlier backup"


def test_user_text_is_kept_and_only_the_seeded_block_goes():
    # a %geom the user edited stays whole; the job keyword still becomes {{JOB}}
    own = ("! r2SCAN-3c TightOpt TightSCF\n%geom\n  MaxIter 50\nend\n"
           "* xyz {{CHARGE}} {{MULT}}\n{{GEOMETRY}}\n*\n")
    assert convert_legacy_text("Opt", own) == own.replace("TightOpt", "{{JOB}}")
    # the seeded block of ANOTHER kind is not this kind's to remove
    sp = "! B3LYP SP\n%geom\n  MaxIter 200\nend\n* xyz 0 1\n{{GEOMETRY}}\n*\n"
    assert convert_legacy_text("SP", sp) == sp.replace("SP\n", "{{JOB}}\n", 1)
    # the seeded IRC block, spacing and case aside, goes
    irc = ("! B3LYP def2-SVP IRC\n%IRC\n MaxIter   100\n  InitHess read\n"
           '  Hess_Filename "{{HESS}}"\nEND\n* xyz 0 1\n{{GEOMETRY}}\n*\n')
    assert convert_legacy_text("IRC", irc) == "! B3LYP def2-SVP {{JOB}}\n* xyz 0 1\n{{GEOMETRY}}\n*\n"
    # several job keywords make one placeholder; none at all gets one appended
    assert convert_legacy_text("Opt", "! PBE Opt Freq\n{{GEOMETRY}}") == "! PBE {{JOB}}\n{{GEOMETRY}}"
    assert convert_legacy_text("SP", "! PBE def2-SVP\n{{GEOMETRY}}") == "! PBE def2-SVP {{JOB}}\n{{GEOMETRY}}"


def test_identical_levels_merge_and_a_name_clash_is_told_apart():
    v1 = _v1_seeds()
    own_freq = {"id": "u2", "name": "wB97X-D4 / def2-TZVP", "kind": "Freq",
                "text": "! wB97X-D4 def2-TZVP Freq NoRI\n* xyz 0 1\n{{GEOMETRY}}\n*\n"}
    own_sp = {"id": "u3", "name": "cheap", "kind": "SP",
              "text": "! PBE def2-SVP SP\n* xyz 0 1\n{{GEOMETRY}}\n*\n"}
    own_opt = {"id": "u4", "name": "cheap opt", "kind": "Opt",
               "text": "! PBE def2-SVP Opt\n* xyz 0 1\n{{GEOMETRY}}\n*\n"}
    sets = migrate_sets(v1 + [own_freq, own_sp, own_opt])
    assert [(s["id"], s["name"]) for s in sets] == [
        ("seed-mlip", "MACE-OFF medium"),
        ("seed-opt", "wB97X-D4 / def2-TZVP"),
        ("u2", "wB97X-D4 / def2-TZVP (Freq)"),        # same name, other level
        ("u3", "cheap"),                               # u4 merged into it
    ]
    assert all(validate_set(s) == s for s in sets)


def test_an_entry_that_was_not_a_valid_v1_set_is_dropped_alone(tmp_path):
    _write_v1(tmp_path, _v1_seeds()[:2] + [{"id": "b", "kind": "SP", "name": "x",
                                            "text": "! SP\n* xyz 0 1\nH 0 0 0\n*\n"}])
    assert [s["id"] for s in load_sets(tmp_path)] == ["seed-mlip", "seed-opt"]


def test_a_save_into_a_v1_file_migrates_and_backs_up_first(tmp_path):
    raw = _write_v1(tmp_path, _v1_seeds())
    save_set({"name": "mine", "kind": "ORCA", "text": LEVEL}, tmp_path)
    assert (tmp_path / inpsets.BACKUP_NAME).read_bytes() == raw
    assert [s["kind"] for s in load_sets(tmp_path)] == ["MLIP", "ORCA", "ORCA"]


# ---- a block made from a schema-1 set reads as its migrated level ----------

def test_a_v1_blocks_text_has_its_migrated_set_among_its_level_forms():
    # the queue editor's "not modified" test (get_calc's levels): a block
    # copied the OLD set's text, and the migration turned the set into a level
    old = _v1_seeds()
    new = migrate_sets(old)
    level = next(s for s in new if s["kind"] == "ORCA")
    for o in old:
        if o["kind"] == "MLIP":
            continue
        assert level["text"] in inpsets.level_forms(o["text"]), o["kind"]
        assert o["text"] not in inpsets.level_forms(o["text"])


def test_a_renamed_v1_set_is_found_by_its_level_form():
    a = {"id": "a", "name": "Mine", "kind": "Opt",
         "text": "! B3LYP def2-SVP TightOpt\n* xyz 0 1\n{{GEOMETRY}}\n*\n"}
    b = {"id": "b", "name": "Mine", "kind": "Freq",
         "text": "! PBE0 def2-SVP Freq\n* xyz 0 1\n{{GEOMETRY}}\n*\n"}
    new = {s["id"]: s for s in migrate_sets([a, b])}
    assert new["b"]["name"] == "Mine (Freq)"
    assert new["b"]["text"] in inpsets.level_forms(b["text"])
    assert new["a"]["text"] not in inpsets.level_forms(b["text"])


def test_a_level_text_has_no_level_forms():
    assert inpsets.level_forms(LEVEL) == []
    assert inpsets.level_forms("") == []
