"""Inp sets (state/inpsets.py): the queue editor's saved input templates.

Seeded once from ORCAdesk's own defaults (StepConfig + build_input, at the
user's %pal / %maxcore), stored in user_data_root()/inp_sets.json, validated
on save (a non-MLIP set must carry {{GEOMETRY}}; an MLIP set must parse).
Every test passes ``root`` explicitly, so nothing touches the real user data.
"""

from __future__ import annotations

import json

import pytest

from orcamgr.state import inpsets
from orcamgr.state.inpsets import (SET_KINDS, calc_kind_for_set, delete_set,
                                   load_sets, mlip_config_from_text, save_set,
                                   validate_set)


def test_the_first_load_seeds_one_set_per_kind_and_writes_them(tmp_path):
    sets = load_sets(tmp_path, nprocs=8, maxcore_mb=3000)
    assert [s["kind"] for s in sets] == list(SET_KINDS)
    assert inpsets.sets_file(tmp_path).exists()
    by_kind = {s["kind"]: s for s in sets}
    for kind in ("Opt", "OptTS", "Freq", "IRC", "SP"):
        text = by_kind[kind]["text"]
        assert "{{GEOMETRY}}" in text and "* xyz {{CHARGE}} {{MULT}}" in text
        assert "%pal nprocs 8 end" in text and "%maxcore 3000" in text
        assert by_kind[kind]["name"] == "wB97X-D4 / def2-TZVP"
    assert "TightOpt" in by_kind["Opt"]["text"] and "MaxIter 200" in by_kind["Opt"]["text"]
    assert "Calc_Hess true" in by_kind["OptTS"]["text"]
    assert "InitHess read" in by_kind["IRC"]["text"]
    assert 'Hess_Filename "{{HESS}}"' in by_kind["IRC"]["text"]
    assert mlip_config_from_text(by_kind["MLIP"]["text"])["mlip_model"] == by_kind["MLIP"]["name"]
    # and the seeds are what a second load reads back
    assert load_sets(tmp_path, nprocs=1, maxcore_mb=100) == sets


def test_save_mints_an_id_and_replace_keeps_it(tmp_path):
    s = save_set({"name": "r2SCAN-3c", "kind": "Opt",
                  "text": "! r2SCAN-3c Opt\n* xyz 0 1\n{{GEOMETRY}}\n*\n"}, tmp_path)
    assert s["id"].startswith("set-")
    again = save_set({**s, "name": "r2SCAN-3c tight"}, tmp_path)
    assert again["id"] == s["id"]
    names = [x["name"] for x in load_sets(tmp_path) if x["id"] == s["id"]]
    assert names == ["r2SCAN-3c tight"]


def test_a_name_is_unique_within_its_kind_only(tmp_path):
    """A block names its set by (kind, name), so a second Opt set of one name
    would make that ambiguous; the same name in another kind is fine (the
    seeds all describe one level of theory)."""
    t = "! Opt\n* xyz 0 1\n{{GEOMETRY}}\n*\n"
    a = save_set({"name": "mine", "kind": "Opt", "text": t}, tmp_path)
    with pytest.raises(ValueError, match="already an Opt set named 'mine'"):
        save_set({"name": "MINE", "kind": "Opt", "text": t}, tmp_path)
    save_set({"name": "mine", "kind": "SP", "text": t}, tmp_path)
    assert save_set({**a, "text": t + "\n"}, tmp_path)["id"] == a["id"]   # itself


def test_seed_texts_are_the_seeds_by_kind(tmp_path):
    texts = inpsets.seed_texts(nprocs=3, maxcore_mb=1000)
    assert list(texts) == list(SET_KINDS)
    assert "%pal nprocs 3 end" in texts["Opt"] and "{{GEOMETRY}}" in texts["SP"]
    assert texts["MLIP"].startswith("model ")


def test_delete_removes_and_an_emptied_list_stays_empty(tmp_path):
    for s in load_sets(tmp_path):
        assert delete_set(s["id"], tmp_path)
    assert load_sets(tmp_path) == []               # not re-seeded
    assert delete_set("nope", tmp_path) is False


@pytest.mark.parametrize("bad, msg", [
    ({"name": "x", "kind": "SP", "text": "! SP\n* xyz 0 1\nH 0 0 0\n*\n"}, "GEOMETRY"),
    ({"name": "x", "kind": "Scan", "text": "{{GEOMETRY}}"}, "kind"),
    ({"name": "", "kind": "SP", "text": "{{GEOMETRY}}"}, "name"),
    ({"name": "x", "kind": "MLIP", "text": "modle MACE\n"}, "unknown key"),
    ({"name": "x", "kind": "MLIP", "text": "device tpu\n"}, "device"),
    ({"id": "../x", "name": "x", "kind": "SP", "text": "{{GEOMETRY}}"}, "id"),
    ("not an object", "object"),
])
def test_a_set_that_does_not_validate_is_refused(tmp_path, bad, msg):
    with pytest.raises(ValueError, match=msg):
        save_set(bad, tmp_path)
    with pytest.raises(ValueError):
        validate_set(bad)


def test_an_unreadable_file_reads_as_the_seeds_without_being_overwritten(tmp_path):
    f = inpsets.sets_file(tmp_path)
    f.write_text("{ not json", encoding="utf-8")
    assert [s["kind"] for s in load_sets(tmp_path)] == list(SET_KINDS)
    assert f.read_text(encoding="utf-8") == "{ not json"


def test_a_bad_entry_in_the_file_is_dropped_alone(tmp_path):
    good = {"id": "g", "name": "ok", "kind": "SP", "text": "{{GEOMETRY}}"}
    inpsets.sets_file(tmp_path).write_text(json.dumps(
        {"schema": 1, "sets": [good, {"id": "b", "kind": "SP"}, good]}), encoding="utf-8")
    assert load_sets(tmp_path) == [good]


def test_mlip_text_maps_onto_the_config_fields():
    assert mlip_config_from_text(
        "# a comment\nmodel MACE-OFF large\ndevice CUDA\nnprocs 99999\nenv e1\n") == {
        "mlip_model": "MACE-OFF large", "mlip_device": "cuda", "nprocs": 1024,
        "mlip_env_id": "e1"}


def test_the_calc_kind_of_a_set():
    assert [calc_kind_for_set(k) for k in SET_KINDS] == [
        "mlip_opt", "opt", "ts_opt", "freq", "irc", "sp"]
    assert calc_kind_for_set("Freq", after_kind="ts_opt") == "ts_freq"
    assert calc_kind_for_set("Freq", after_kind="opt") == "freq"
    with pytest.raises(ValueError):
        calc_kind_for_set("Scan")
