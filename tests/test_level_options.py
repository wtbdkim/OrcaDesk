"""A level block's options: an inp-set LEVEL ({{JOB}} on its ``!`` line) runs
with the block's own config merged in (input_generator.render_template_text),
the way the Guided builder writes the same settings.

* a block with no options renders byte-identical to the level as it always
  did (the pin), and the blocks it adds are the ones build_input writes;
* options merge into the level, never duplicating a block: a missing block is
  inserted whole, a block the level has takes only the lines it lacks, and a
  key set on both sides (or constraints for a %geom written on one line) is
  REFUSED -- at the store when the block is added or edited, and BLOCKED by
  the engine before launch;
* the store checks a level block's constraint / scan indices against its
  coordinates, an update_calc on a level block keeps its level, a schema-1
  session's level blocks read their config as no options;
* TD-DFT and NEB-TS run from a level; an IRC's initial Hessian is "auto"
  (the parent's) unless the options choose, and a picked .hess is copied in;
* build_inp_preview is the launch renderer's output; existing_outputs.

Session I/O is redirected to tmp_path (see test_store.py for why).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import orcamgr.state.store as store_mod
from orcamgr.core.input_generator import (
    StepConfig, TemplateConflict, build_input, is_level_text, render_template_text,
    template_config, template_hess_need, template_option_conflicts)
from orcamgr.core.queue import (CalcState, Calculation, GeometrySource, OrcaBlocked,
                                _wants_scan, template_hess_problem)
from orcamgr.core.runner import OrcaRunError
from orcamgr.state.store import (QueueOpError, QueueStore, calc_from_dict,
                                 constraint_warning)

from test_queue_engine import _LaunchAttempted, _real_engine, make_calc

H2O = ("O 0.000000 0.000000 0.117300\nH 0.000000 0.757200 -0.469200\n"
       "H 0.000000 -0.757200 -0.469200")
LEVEL = "! B3LYP def2-SVP {{JOB}}\n%pal nprocs 2 end\n* xyz {{CHARGE}} {{MULT}}\n{{GEOMETRY}}\n*\n"
BOND = {"kind": "B", "atoms": [0, 1], "value": None}
SCAN = {"kind": "A", "atoms": [1, 0, 2], "start": 100.0, "end": 110.0, "steps": 3}


def render(kind, opts=None, level=LEVEL, hess="", n_atoms=None):
    cfg = None if opts is None else template_config(opts, kind)
    return render_template_text(level, kind, 0, 1, hess, cfg, n_atoms)


def with_block(block: str) -> str:
    return LEVEL.replace("* xyz", block + "\n* xyz")


# ---- no options: exactly what a level always rendered as ----------------------------

@pytest.mark.parametrize("kind, block", [
    ("opt", "%geom\n  MaxIter 200\nend\n"),
    ("ts_opt", "%geom\n  MaxIter 200\n  Calc_Hess true\nend\n"),
    ("freq", ""), ("ts_freq", ""),
    ("opt_freq", "%geom\n  MaxIter 200\nend\n"),
    ("ts_opt_freq", "%geom\n  MaxIter 200\n  Calc_Hess true\nend\n"),
    ("sp", ""), ("nmr", ""),
    ("irc", "%irc\n  MaxIter 100\n  InitHess calc_anfreq\nend\n"),
])
def test_a_block_with_no_options_renders_byte_identical(kind, block):
    job = {"opt": "TightOpt", "ts_opt": "OptTS", "freq": "Freq", "ts_freq": "Freq",
           "opt_freq": "TightOpt Freq", "ts_opt_freq": "OptTS Freq", "sp": "SP",
           "nmr": "NMR", "irc": "IRC"}[kind]
    expected = (f"! B3LYP def2-SVP {job}\n%pal nprocs 2 end\n{block}"
                "* xyz 0 1\n{{GEOMETRY}}\n*\n")
    assert render(kind) == expected
    assert render(kind, {}) == expected                       # config {} is no options
    # a config the store holds for a level block (all StepConfig defaults)
    assert render(kind, template_config(StepConfig(kind=kind).to_dict()
                                        | {"irc_init_hess": "auto"}, kind).to_dict()) == expected


def test_no_options_with_a_parent_hessian_reads_it():
    assert ('%irc\n  MaxIter 100\n  InitHess read\n  Hess_Filename "ts.hess"\nend\n'
            in render("irc", {}, hess="ts.hess"))


def test_the_options_render_as_the_guided_builder_writes_them():
    """One block, two writers: a level with options and a Guided calc with
    the same settings give the same input."""
    level = build_input(StepConfig(kind="sp", calculation_type="{{JOB}}", nprocs=1),
                        "{{GEOMETRY}}", 0, 1)
    cases = [
        ("opt", "TightOpt", dict(geom_constraints=[BOND], geom_scan=[SCAN], max_iter=60)),
        ("ts_opt", "OptTS", dict(geom_constraints=[BOND])),
        ("opt_freq", "TightOpt Freq", dict(freq_temp_k=350.0, freq_pressure_atm=2.0)),
        ("freq", "Freq", dict(freq_temp_k=200.0)),
        ("irc", "IRC", dict(irc_maxiter=40, irc_direction="forward",
                            irc_init_hess="calc_numfreq")),
        ("tddft", "", dict(tddft_nroots=8, tddft_maxdim=20, tddft_tda=False,
                           tddft_triplets=True)),
        ("neb_ts", "NEB-TS", dict(neb_nimages=12, neb_preopt_ends=True,
                                  neb_ts_guess_xyz="H 0 0 0")),
        ("nmr", "NMR", dict(nmr_jcoupling=True)),
    ]
    for kind, calc_type, opts in cases:
        guided = build_input(StepConfig(kind=kind, calculation_type=calc_type, nprocs=1,
                                        **opts), "{{GEOMETRY}}", 0, 1)
        assert render_template_text(level, kind, 0, 1, "",
                                    template_config(opts, kind)) == guided, kind


# ---- the ! line --------------------------------------------------------------------

def test_extra_keywords_scf_and_solvation_go_on_the_job_line_once():
    text = render("sp", {"options": "D3BJ def2/J d3bj", "scf_convergence": "TightSCF",
                         "solvation": {"model": "CPCM", "solvent": "Water"}},
                  level=LEVEL.replace("{{JOB}}", "{{JOB}} def2/J"))
    assert text.startswith("! B3LYP def2-SVP SP def2/J TightSCF D3BJ CPCM(Water)\n")


def test_a_solvent_with_spaces_gets_its_cpcm_block():
    text = render("sp", {"solvation": {"model": "CPCM", "solvent": "Diethyl Ether"}})
    assert text.startswith("! B3LYP def2-SVP SP CPCM\n")
    assert '%pal nprocs 2 end\n%cpcm\n  solvent "Diethyl Ether"\nend\n* xyz' in text


def test_td_dft_removes_the_job_token():
    text = render("tddft", {})
    assert text.startswith("! B3LYP def2-SVP\n%pal nprocs 2 end\n%tddft\n  nroots 3\nend\n")
    assert render("tddft", {}, level=LEVEL.replace("! B3LYP def2-SVP {{JOB}}",
                                                   "! B3LYP def2-SVP\n! {{JOB}}")) \
        .startswith("! B3LYP def2-SVP\n%pal")                 # an emptied ! line goes


@pytest.mark.parametrize("level, opts, says", [
    (LEVEL.replace("{{JOB}}", "{{JOB}} TightSCF"), {"scf_convergence": "VeryTightSCF"},
     "already sets its SCF convergence (TightSCF)"),
    (LEVEL.replace("{{JOB}}", "{{JOB}} CPCM(Toluene)"),
     {"solvation": {"model": "SMD", "solvent": "Water"}}, "solvent model (CPCM(Toluene))"),
    (with_block('%cpcm\n  epsilon 10\nend'),
     {"solvation": {"model": "CPCM", "solvent": "Water"}}, "solvent model (%cpcm)"),
])
def test_a_job_line_choice_on_both_sides_is_refused(level, opts, says):
    with pytest.raises(TemplateConflict, match=r"Keep it in one place"):
        render("sp", opts, level=level)
    assert says in template_option_conflicts(level, "sp", template_config(opts, "sp"))


def test_the_same_scf_tier_on_both_sides_is_not_a_conflict():
    text = render("sp", {"scf_convergence": "tightscf"},
                  level=LEVEL.replace("{{JOB}}", "{{JOB}} TightSCF"))
    assert text.startswith("! B3LYP def2-SVP SP TightSCF\n")


# ---- %geom -------------------------------------------------------------------------

def test_constraints_and_scan_go_into_one_inserted_geom():
    text = render("opt", {"geom_constraints": [BOND], "geom_scan": [SCAN], "max_iter": 60})
    assert ("%geom\n  MaxIter 60\n  Constraints\n    { B 0 1 C }\n  end\n"
            "  Scan\n    A 1 0 2 = 100.00, 110.00, 3\n  end\nend\n* xyz") in text
    assert "Scan" not in render("opt_freq", {"geom_scan": [SCAN]})   # opt only, like Guided
    assert "%geom" not in render("sp", {"geom_constraints": [BOND]})


def test_options_go_into_the_levels_own_geom_before_its_end():
    level = with_block("%geom\n  Trust 0.2\n  Hybrid_Hess\n    { 0 1 }\n  end\nend")
    text = render("ts_opt", {"geom_constraints": [BOND]}, level=level)
    assert text.count("%geom") == 1
    assert ("%geom\n  Trust 0.2\n  Hybrid_Hess\n    { 0 1 }\n  end\n  MaxIter 200\n"
            "  Calc_Hess true\n  Constraints\n    { B 0 1 C }\n  end\nend\n* xyz") in text


def test_a_levels_initial_hessian_is_not_overridden_by_calc_hess():
    text = render("ts_opt", {}, level=with_block("%geom\n  InHess Read\n  InHessName \"g.hess\"\nend"))
    assert "Calc_Hess" not in text and "MaxIter 200" in text


def test_a_one_line_geom_takes_the_job_lines_as_words():
    text = render("ts_opt", {}, level=with_block("%geom Trust 0.2 end  # mine"))
    assert "%geom Trust 0.2 MaxIter 200 Calc_Hess true end  # mine\n" in text


@pytest.mark.parametrize("level, opts, says", [
    (with_block("%geom\n  MaxIter 30\nend"), {"max_iter": 60},
     "%geom block already sets MaxIter"),
    (with_block("%geom\n  Constraints\n    { A 0 1 2 C }\n  end\nend"),
     {"geom_constraints": [BOND]}, "already has a Constraints block"),
    (with_block("%geom\n  Scan\n    B 0 1 = 1.0, 1.2, 3\n  end\nend"),
     {"geom_scan": [SCAN]}, "already has a Scan block"),
    (with_block("%geom Trust 0.2 end"), {"geom_constraints": [BOND]},
     "written on one line"),
    (with_block("%geom\n  Trust 0.2\n"), {"geom_constraints": [BOND]}, "no closing end"),
])
def test_a_geom_option_the_level_cannot_take_is_refused(level, opts, says):
    with pytest.raises(TemplateConflict, match=says):
        render("opt", opts, level=level)


def test_a_maxiter_of_the_default_beside_the_levels_own_is_no_conflict():
    text = render("opt", {"max_iter": 200}, level=with_block("%geom\n  MaxIter 30\nend"))
    assert "MaxIter 30" in text and "MaxIter 200" not in text


def test_constraint_and_scan_indices_are_checked_against_the_structure():
    with pytest.raises(ValueError, match="names atom 5, but the structure has 3 atoms"):
        render("opt", {"geom_constraints": [{"kind": "B", "atoms": [0, 4], "value": None}]},
               n_atoms=3)
    with pytest.raises(ValueError, match="scan on atoms 1-2-9"):
        render("opt", {"geom_scan": [dict(SCAN, atoms=[0, 1, 8])]}, n_atoms=3)


# ---- %freq / %irc / %tddft / %neb / %eprnmr ---------------------------------------

def test_freq_temperature_and_pressure_merge_or_refuse():
    assert "%freq\n  Temp 350.0\n  Pressure 1.0\nend\n* xyz" in render("freq", {"freq_temp_k": 350})
    own = with_block("%freq\n  Temp 300\nend")
    assert "%freq\n  Temp 300\n  Pressure 2.0\nend" in render("freq", {"freq_pressure_atm": 2},
                                                              level=own)
    with pytest.raises(TemplateConflict, match="%freq block already sets Temp"):
        render("freq", {"freq_temp_k": 350}, level=own)
    assert render("freq", {}, level=own).count("%freq") == 1


def test_irc_options_with_the_initial_hessian_explicit():
    text = render("irc", {"irc_init_hess": "calc_numfreq", "irc_direction": "backward"},
                  hess="parent.hess")
    assert "%irc\n  MaxIter 100\n  Direction backward\n  InitHess calc_numfreq\nend" in text
    assert "parent.hess" not in text                        # explicit: no parent Hessian
    picked = render("irc", {"irc_init_hess": "read",
                            "irc_hess_file": "C:\\my files\\ts run.hess"}, hess="parent.hess")
    assert 'InitHess read\n  Hess_Filename "ts run.hess"' in picked
    with pytest.raises(ValueError, match="requires a .hess filename"):
        render("irc", {"irc_init_hess": "read"})


def test_irc_options_into_the_levels_own_irc():
    own = with_block("%irc\n  MaxIter 30\nend")
    assert render("irc", {}, level=own).count("%irc") == 1
    assert "%irc\n  MaxIter 30\n  Direction forward\nend" in render(
        "irc", {"irc_direction": "forward"}, level=own)
    with pytest.raises(TemplateConflict, match="%irc block already sets MaxIter"):
        render("irc", {"irc_maxiter": 50}, level=own)


def test_template_hess_need_follows_the_initial_hessian_option():
    assert template_hess_need(LEVEL, "irc") == "optional"
    assert template_hess_need(LEVEL, "irc", template_config({}, "irc")) == "optional"
    for init in ("calc_anfreq", "calc_numfreq", "read"):
        assert template_hess_need(LEVEL, "irc", template_config(
            {"irc_init_hess": init, "irc_hess_file": "x.hess"}, "irc")) == ""


def test_tddft_and_neb_merge_into_the_levels_own_block():
    own = with_block("%tddft\n  maxdim 5\nend")
    assert "%tddft\n  maxdim 5\n  nroots 8\nend" in render("tddft", {"tddft_nroots": 8},
                                                           level=own)
    with pytest.raises(TemplateConflict, match="%tddft block already sets maxdim"):
        render("tddft", {"tddft_maxdim": 9}, level=own)
    neb = with_block('%neb\n  NEB_End_XYZFile "mine.xyz"\nend')
    text = render("neb_ts", {"neb_nimages": 10}, level=neb)
    assert text.count("NEB_End_XYZFile") == 1 and '"mine.xyz"' in text
    assert "Nimages 10" in text


def test_nmr_j_couplings_follow_the_coordinates():
    text = render("nmr", {"nmr_jcoupling": True})
    assert text.endswith("*\n%eprnmr\n  Nuclei = all H { shift, ssall }\n"
                         "  Nuclei = all C { shift, ssall }\nend\n")
    with pytest.raises(TemplateConflict, match="own %eprnmr block"):
        render("nmr", {"nmr_jcoupling": True}, level=LEVEL + "%eprnmr\n  Nuclei = all H { shift }\nend\n")


def test_options_never_reach_a_text_that_is_not_a_level():
    old = LEVEL.replace("! B3LYP def2-SVP {{JOB}}", "! B3LYP def2-SVP SP")
    assert not is_level_text(old)
    assert render_template_text(old, "sp", 0, 1, "", template_config(
        {"options": "D3BJ"}, "sp")) == old.replace("{{CHARGE}} {{MULT}}", "0 1")
    assert template_option_conflicts(old, "sp", template_config({}, "sp")) == ""
    assert template_option_conflicts(LEVEL, "general", template_config({}, "general")) == ""


# ---- the store ---------------------------------------------------------------------

@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(store_mod, "user_data_root", lambda: tmp_path)
    return QueueStore()


def block(name, kind="opt", **kw):
    d = {"name": name, "kind": kind, "is_raw": True, "raw_text": LEVEL, "xyz": H2O,
         "set_name": "B3LYP / def2-SVP"}
    d.update(kw)
    return d


def test_a_level_blocks_config_is_its_options():
    c = calc_from_dict(block("w-opt", config={"geom_constraints": [BOND], "options": "D3BJ",
                                              "kind": "sp"}))
    assert c.config.kind == "opt"                         # the block's own kind
    assert c.config.geom_constraints == [{"kind": "B", "atoms": [0, 1], "value": None}]
    assert c.config.irc_init_hess == "auto"              # not named: the level's default
    assert calc_from_dict(block("w-irc", "irc", config={"irc_init_hess": "calc_anfreq"})) \
        .config.irc_init_hess == "calc_anfreq"           # named: the user's choice


def test_the_store_checks_a_level_blocks_indices_against_its_coordinates():
    with pytest.raises(ValueError, match="names atom 4, but the structure has 3 atoms"):
        calc_from_dict(block("w-opt", config={"geom_constraints": [
            {"kind": "B", "atoms": [0, 3], "value": None}]}))
    with pytest.raises(ValueError, match="scan on atoms"):
        calc_from_dict(block("w-opt", config={"geom_scan": [dict(SCAN, atoms=[0, 1, 7])]}))
    # a referenced geometry is checked at run time, never on restore
    calc_from_dict(block("w-opt", geometry_source="reference", ref_name="p", xyz="",
                         config={"geom_constraints": [{"kind": "B", "atoms": [0, 30]}]}))
    calc_from_dict(block("w-opt", config={"geom_constraints": [
        {"kind": "B", "atoms": [0, 3], "value": None}]}), restoring=True)


def test_a_conflict_is_refused_with_the_op_index_and_names_the_block(store):
    level = with_block("%geom\n  MaxIter 30\nend")
    store.apply_ops([{"op": "add_calc", "calc": block("w-opt", raw_text=level)}])
    with pytest.raises(QueueOpError) as e:
        store.apply_ops([
            {"op": "update_calc", "name": "w-opt", "calc": {"approved": False}},
            {"op": "update_calc", "name": "w-opt", "calc": {"config": {"max_iter": 60}}}])
    assert e.value.index == 1
    assert str(e.value).startswith("'w-opt': The level's own %geom block already sets MaxIter")
    assert store.get("w-opt").config.max_iter == 200      # nothing changed
    # restored as it was saved, whatever the rule says now
    calc_from_dict(block("w-opt", raw_text=level, config={"max_iter": 60}), restoring=True)


def test_an_options_edit_keeps_the_block_on_its_level(store):
    store.apply_ops([{"op": "add_calc", "calc": block("w-opt", system="", node="n1")}])
    store.apply_ops([{"op": "update_calc", "name": "w-opt", "calc": {
        "kind": "ts_opt", "geometry_source": "direct", "xyz": H2O, "ref_name": "",
        "ref_point": "", "config": {"geom_constraints": [BOND], "options": "D3BJ"}}}])
    c = store.get("w-opt")
    assert (c.kind, c.is_raw, c.raw_text, c.set_name) == ("ts_opt", True, LEVEL,
                                                          "B3LYP / def2-SVP")
    assert c.config.kind == "ts_opt" and c.config.options == "D3BJ"
    # a Type change alone keeps the options
    store.apply_ops([{"op": "update_calc", "name": "w-opt", "calc": {"kind": "opt"}}])
    assert store.get("w-opt").config.geom_constraints and store.get("w-opt").config.kind == "opt"


def test_a_guided_calc_becoming_a_level_block_carries_no_options(store):
    store.apply_ops([{"op": "add_calc", "calc": {
        "name": "w-opt", "kind": "opt", "xyz": H2O,
        "config": {"options": "D3BJ", "solvation": {"model": "CPCM", "solvent": "Water"},
                   "geom_constraints": [BOND]}}}])
    store.apply_ops([{"op": "update_calc", "name": "w-opt", "calc": {
        "is_raw": True, "raw_text": with_block("%cpcm\n  epsilon 10\nend"),
        "set_name": "mine"}}])
    c = store.get("w-opt")
    assert c.is_raw and c.config.options == "" and not c.config.geom_constraints
    assert c.config.solvation.model == ""                 # else: a conflict with the level


def test_options_ride_the_session_and_a_schema_1_level_block_has_none(store, tmp_path):
    store.apply_ops([{"op": "add_calc", "calc": block(
        "w-opt", config={"geom_constraints": [BOND], "options": "D3BJ"})}])
    store.save_session()
    saved = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    assert saved["schema"] == 3                # 2: options; 3: recorded run folders
    again = QueueStore()
    again.load_session()
    assert again.get("w-opt").config.geom_constraints and again.get("w-opt").config.options == "D3BJ"
    # a schema-2 file (before run folders were recorded) has its options too
    saved["schema"] = 2
    (tmp_path / "session.json").write_text(json.dumps(saved), encoding="utf-8")
    two = QueueStore()
    two.load_session()
    assert two.get("w-opt").config.geom_constraints and two.get("w-opt").config.options == "D3BJ"
    # the same file as a schema-1 build wrote it: a level block's config was
    # never read then, so it is no options now (and an IRC's Hessian "auto")
    saved["schema"] = 1
    saved["calculations"][0]["kind"] = "irc"
    saved["calculations"][0]["config"]["irc_init_hess"] = "calc_anfreq"
    (tmp_path / "session.json").write_text(json.dumps(saved), encoding="utf-8")
    legacy = QueueStore()
    legacy.load_session()
    c = legacy.get("w-opt")
    assert c.config.options == "" and not c.config.geom_constraints
    assert (c.config.kind, c.config.irc_init_hess) == ("irc", "auto")


def test_a_workflow_copy_carries_a_level_steps_options(store):
    """The editor's workflow paste is an add_calc per step with the step's
    config; the options arrive as they were copied."""
    store.apply_ops([{"op": "add_system", "system": {"id": "s2", "name": "w",
                                                     "inputs": [{"label": "a", "xyz": H2O}]}},
                     {"op": "add_calc", "calc": block(
                         "w-opt", system="s2", node="n1",
                         config={"geom_scan": [SCAN], "max_iter": 80})},
                     {"op": "add_calc", "calc": block(
                         "w-freq", "freq", system="s2", node="n2", after="w-opt",
                         geometry_source="reference", ref_name="w-opt", xyz="",
                         config={"freq_temp_k": 350.0})}])
    assert store.get("w-opt").config.geom_scan and store.get("w-opt").config.max_iter == 80
    assert store.get("w-freq").config.freq_temp_k == 350.0
    assert _wants_scan(store.get("w-opt"))


def test_an_mlip_set_block_keeps_its_model_through_an_options_edit(store):
    text = "model MACE-OFF small\ndevice cpu\n"
    store.apply_ops([{"op": "add_calc", "calc": {
        "name": "w-pre", "kind": "mlip_opt", "is_raw": True, "raw_text": text,
        "xyz": H2O, "set_name": "MACE"}}])
    store.apply_ops([{"op": "update_calc", "name": "w-pre", "calc": {
        "kind": "mlip_freq", "config": {"freq_temp_k": 310.0}}}])
    c = store.get("w-pre")
    assert (c.kind, c.config.mlip_model, c.config.mlip_device, c.config.freq_temp_k) == (
        "mlip_freq", "MACE-OFF small", "cpu", 310.0)


def test_constraint_warning_judges_a_level_blocks_constraints():
    angle = {"kind": "A", "atoms": [1, 0, 2], "value": 179.0}
    c = calc_from_dict(block("w-opt", config={"geom_constraints": [angle]}))
    assert "linear bend" in constraint_warning(c)


def test_a_picked_hessian_needs_no_parent_a_named_one_does():
    picked = template_config({"irc_init_hess": "read",
                              "irc_hess_file": "C:\\data\\ts.hess"}, "irc")
    assert template_hess_problem("w-irc", "irc", LEVEL, "s", "", False, picked) == ""
    named = template_config({"irc_init_hess": "read", "irc_hess_file": "ts.hess"}, "irc")
    assert "reads a Hessian" in template_hess_problem("w-irc", "irc", LEVEL, "s", "", False, named)
    assert template_hess_problem("w-irc", "irc", LEVEL, "s", "w-ts", False, named) == ""


# ---- the engine --------------------------------------------------------------------

def level_calc(name, kind, opts=None, text=LEVEL):
    c = Calculation(name=name, kind=kind, config=template_config(opts or {}, kind))
    c.is_raw, c.raw_text, c.xyz = True, text, H2O
    return c


def launched(tmp_path, calc, by_name=None):
    engine = _real_engine(tmp_path)
    engine._by_name = by_name or {calc.name: calc}
    with pytest.raises(_LaunchAttempted):
        engine._run_calc(calc, 0)
    return (tmp_path / calc.name / f"{calc.name}.inp").read_text(encoding="utf-8")


def test_the_engine_runs_a_level_block_with_its_options(tmp_path):
    inp = launched(tmp_path, level_calc("w-opt", "opt", {"geom_constraints": [BOND],
                                                         "options": "D3BJ"}))
    assert inp.startswith("! B3LYP def2-SVP TightOpt D3BJ\n")
    assert "  Constraints\n    { B 0 1 C }\n  end\nend\n* xyz 0 1\nO 0.000000" in inp


def test_the_engine_blocks_a_conflict_before_launch(tmp_path):
    calc = level_calc("w-opt", "opt", {"max_iter": 60},
                      text=with_block("%geom\n  MaxIter 30\nend"))
    with pytest.raises(OrcaBlocked, match="already sets MaxIter"):
        _real_engine(tmp_path)._run_calc(calc, 0)
    assert not (tmp_path / "w-opt" / "w-opt.inp").exists()


def test_the_engine_checks_indices_against_a_referenced_structure(tmp_path):
    calc = level_calc("w-opt", "opt", {"geom_constraints": [
        {"kind": "B", "atoms": [0, 5], "value": None}]})
    calc.geometry_source, calc.ref_name, calc.xyz = GeometrySource.REFERENCE, "p", ""
    engine = _real_engine(tmp_path)
    engine._resolve_geometry = lambda c: H2O
    with pytest.raises(OrcaRunError, match="names atom 6, but the structure has 3 atoms"):
        engine._run_calc(calc, 0)


def test_an_explicit_initial_hessian_stages_no_parent_hessian(tmp_path):
    (tmp_path / "ts-freq").mkdir()
    (tmp_path / "ts-freq" / "ts-freq.hess").write_text("$hessian\n", encoding="utf-8")
    parent = make_calc("ts-freq", kind="ts_freq", state=CalcState.DONE)
    calc = level_calc("w-irc", "irc", {"irc_init_hess": "calc_numfreq"})
    calc.after = "ts-freq"
    inp = launched(tmp_path, calc, {"ts-freq": parent, "w-irc": calc})
    assert "InitHess calc_numfreq" in inp and "Hess_Filename" not in inp
    assert not (tmp_path / "w-irc" / "ts-freq.hess").exists()


def test_a_picked_hessian_is_copied_into_the_run_folder(tmp_path):
    src = tmp_path / "picked" / "my ts.hess"
    src.parent.mkdir()
    src.write_text("$hessian picked\n", encoding="utf-8")
    calc = level_calc("w-irc", "irc", {"irc_init_hess": "read", "irc_hess_file": str(src)})
    inp = launched(tmp_path, calc)
    assert 'InitHess read\n  Hess_Filename "my ts.hess"' in inp
    assert (tmp_path / "w-irc" / "my ts.hess").read_text(encoding="utf-8") == "$hessian picked\n"


def test_a_neb_ts_level_block_writes_its_product(tmp_path):
    calc = level_calc("w-neb", "neb_ts", {"neb_product_xyz": H2O.replace("0.117300", "0.2"),
                                          "options": "FREQ"})
    inp = launched(tmp_path, calc)
    assert inp.startswith("! B3LYP def2-SVP NEB-TS FREQ\n")
    assert '%neb\n  NEB_End_XYZFile "product.xyz"\nend\n* xyz' in inp
    assert (tmp_path / "w-neb" / "product.xyz").read_text(encoding="utf-8").startswith("3\n")
    # without a product nothing launches, and the row stays editable (BLOCKED)
    with pytest.raises(OrcaBlocked, match="needs a product geometry"):
        _real_engine(tmp_path)._run_calc(level_calc("w-neb2", "neb_ts"), 0)


def test_a_td_dft_level_block_runs(tmp_path):
    inp = launched(tmp_path, level_calc("w-td", "tddft", {"tddft_nroots": 5}))
    assert inp.startswith("! B3LYP def2-SVP\n%pal nprocs 2 end\n%tddft\n  nroots 5\nend\n* xyz")


# ---- the bridge: preview == launch, existing_outputs -------------------------------

@pytest.fixture
def bridge(store, tmp_path):
    from types import SimpleNamespace
    from orcamgr.gui.bridge import Bridge
    stub = SimpleNamespace(store=store, settings=SimpleNamespace(workspace_root=str(tmp_path)))
    stub._preview_hess = lambda *a: Bridge._preview_hess(stub, *a)
    stub._holds_output = lambda name: Bridge._holds_output(stub, name)
    stub._HESS_KINDS = Bridge._HESS_KINDS
    stub.call = lambda slot, *a: json.loads(getattr(Bridge, slot)(stub, *a))
    return stub


@pytest.mark.parametrize("kind, opts", [
    ("opt", {"geom_constraints": [BOND], "geom_scan": [SCAN], "options": "D3BJ"}),
    ("irc", {"irc_direction": "forward"}),
    ("nmr", {"nmr_jcoupling": True, "solvation": {"model": "CPCM", "solvent": "Water"}}),
    ("tddft", {"tddft_nroots": 6}),
])
def test_the_preview_is_what_launches(bridge, tmp_path, kind, opts):
    d = block("w-x", kind, config=opts, charge=0, multiplicity=1, geometry_source="direct")
    pv = bridge.call("build_inp_preview", json.dumps(d))
    assert pv["ok"], pv
    inp = launched(tmp_path / "ws", calc_from_dict(d))
    assert pv["text"] == inp


def test_the_preview_says_why_options_cannot_render(bridge):
    d = block("w-x", "opt", raw_text=with_block("%geom Trust 0.2 end"),
              config={"geom_constraints": [BOND]})
    pv = bridge.call("build_inp_preview", json.dumps(d))
    assert pv["ok"] is False and "written on one line" in pv["error"]


def test_existing_outputs_lists_the_names_whose_folder_holds_an_output(bridge, tmp_path):
    (tmp_path / "a-opt").mkdir()
    (tmp_path / "a-opt" / "a-opt.out").write_text("x", encoding="utf-8")
    (tmp_path / "a-pre").mkdir()
    (tmp_path / "a-pre" / "a-pre.mlip.json").write_text("{}", encoding="utf-8")
    (tmp_path / "a-empty").mkdir()                       # a folder alone holds nothing
    out = bridge.call("existing_outputs", json.dumps(
        ["a-pre", "a-opt", "a-empty", "nope", "a-opt", "", "..", "x/../a-opt", 3]))
    assert out == {"ok": True, "existing": ["a-pre", "a-opt"]}
    assert bridge.call("existing_outputs", "{}")["ok"] is False
    assert bridge.call("has_existing_output", "a-opt") == {"ok": True, "exists": True}
