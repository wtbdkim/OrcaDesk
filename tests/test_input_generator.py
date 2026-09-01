"""Tests for orcamgr/core/input_generator.py.

Contracts under test (see PRINCIPLES.md):
  P30 - keyword rewriting only through exact verified maps, verbatim otherwise
        (no hyphen-stripping heuristic).
  P31 - dispersion is always an explicit separate D3BJ/D4 keyword, never a
        bare or combined "-D3"; auxiliary bases are added only in the
        verified cases and never when the user already decided.
  P34 - untrusted numerics are coerced and clamped at StepConfig.from_dict
        (they are f-string-interpolated into .inp text).
  P26 - NEB reactant/product mismatches are detected (with the first
        divergent index), never auto-fixed.

These modules are framework-free and write nothing to disk, so no path
isolation is needed here.
"""

import math
import re

import pytest

from orcamgr.core.input_generator import (
    DEFAULT_AUX,
    GEOMETRY_PLACEHOLDER,
    Solvation,
    StepConfig,
    _auto_aux,
    build_input,
    build_input_template,
    check_neb_atom_order,
    is_composite_method,
    method_forbids_ri,
    normalize_functional,
    smd_supports,
    render_raw_input,
)


WATER_XYZ = (
    "O    0.000000    0.000000    0.117300\n"
    "H    0.000000    0.757200   -0.469200\n"
    "H    0.000000   -0.757200   -0.469200"
)


def _keyword_line(text: str) -> str:
    """First line of a generated .inp (the '!' simple-input line)."""
    first = text.splitlines()[0]
    assert first.startswith("!"), f"expected keyword line, got {first!r}"
    return first


# ---------------------------------------------------------------------------
# Composite ("3c") methods bring their own basis
#
# ORCA takes an explicit basis on the ! line as an override, so emitting the
# picker's default alongside r2SCAN-3c runs plain r2SCAN/def2-TZVP instead —
# it terminates normally and validates DONE, so nothing else catches it.
# Measured on ORCA 6.1.1 (water, single point): with def2-TZVP appended, 43
# basis functions and -76.417967 Eh; without, def2-mTZVPP and -76.418907 Eh.
# ---------------------------------------------------------------------------

def test_composite_methods_are_recognized():
    for name in ("HF-3c", "B97-3c", "r2SCAN-3c", "PBEh-3c", "B3LYP-3c", "wB97X-3c"):
        assert is_composite_method(name), name
    for name in ("B3LYP", "wB97X-D4", "PBE0", "B97-D3", "CAM-B3LYP", "M06-2X", ""):
        assert not is_composite_method(name), name


@pytest.mark.parametrize("functional", ["r2SCAN-3c", "HF-3c", "wB97X-3c"])
def test_composite_method_keeps_its_own_basis(functional):
    cfg = StepConfig(kind="sp", functional=functional, basis_set="def2-TZVP")
    line = _keyword_line(build_input(cfg, WATER_XYZ))

    assert functional in line
    assert "def2-TZVP" not in line       # the picker's basis is NOT an override
    assert DEFAULT_AUX not in line       # nor is an aux chosen from that basis


def test_composite_method_says_why_the_basis_is_missing():
    cfg = StepConfig(kind="sp", functional="r2SCAN-3c", basis_set="def2-TZVP")
    text = build_input(cfg, WATER_XYZ)

    note = [ln for ln in text.splitlines() if ln.startswith("#")]
    assert len(note) == 1
    assert "composite" in note[0] and "r2SCAN-3c" in note[0]


def test_non_composite_functional_still_gets_its_basis_and_aux():
    cfg = StepConfig(kind="sp", functional="B3LYP", basis_set="def2-TZVP")
    text = build_input(cfg, WATER_XYZ)

    assert "def2-TZVP" in _keyword_line(text)
    assert DEFAULT_AUX in _keyword_line(text)
    assert not [ln for ln in text.splitlines() if ln.startswith("#")]


# ---------------------------------------------------------------------------
# What ORCA refuses to run — measured, not assumed
#
# Every entry in data/solvents.json was run against ORCA 6.1.1 in both models
# (88 names x 2). Exactly five combinations fail, and all five are here.
# ---------------------------------------------------------------------------

def test_ethyl_acetate_uses_the_spelling_orca_accepts():
    # "Ethyl Acetate" is "Solvent name not found" in BOTH models; its sibling
    # "Methyl Acetate" was mapped and this one was not
    cfg = StepConfig(kind="sp", solvation=Solvation("CPCM", "Ethyl Acetate"))
    assert 'solvent "ethyl ethanoate"' in build_input(cfg, WATER_XYZ)


@pytest.mark.parametrize("solvent", ["Phenol", "Ammonia", "Liquid Ammonia"])
def test_smd_refuses_solvents_only_cpcm_has(solvent):
    # SMD aborts at startup on these ("SMD solvent not found!"), which leaves
    # the calculation FAILED — and FAILED is locked (P24). Refuse up front.
    assert smd_supports(solvent) is False
    with pytest.raises(ValueError) as e:
        build_input(StepConfig(kind="sp", solvation=Solvation("SMD", solvent)),
                    WATER_XYZ)
    assert "SMD" in str(e.value)


@pytest.mark.parametrize("solvent", ["Phenol", "Ammonia", "Liquid Ammonia"])
def test_cpcm_still_accepts_them(solvent):
    text = build_input(StepConfig(kind="sp", solvation=Solvation("CPCM", solvent)),
                       WATER_XYZ)
    assert "CPCM" in text


@pytest.mark.parametrize("solvent", ["Water", "Ethanol", "Toluene", "DMSO"])
def test_ordinary_solvents_work_in_both_models(solvent):
    for model in ("CPCM", "SMD"):
        build_input(StepConfig(kind="sp", solvation=Solvation(model, solvent)),
                    WATER_XYZ)   # must not raise


def test_a_jk_aux_does_not_suppress_the_coulomb_aux():
    """def2/JK fits Coulomb AND exchange for RIJK; it is not an AuxJ. Under
    RIJCOSX, ORCA aborts with "RIJ is chosen with no AuxJ basis set!" — the
    substring test for "/J" matched "/JK" and dropped the aux that was needed."""
    cfg = StepConfig(kind="sp", functional="HF", basis_set="def2-SVP",
                     ri_approximation="RIJCOSX", options="def2/JK")
    line = _keyword_line(build_input(cfg, WATER_XYZ))

    assert DEFAULT_AUX in line      # def2/J is still added
    assert "def2/JK" in line        # ...and what the user typed is untouched


def test_a_real_coulomb_aux_still_counts_as_user_supplied():
    cfg = StepConfig(kind="sp", functional="HF", basis_set="def2-SVP",
                     ri_approximation="RIJCOSX", options="def2/J")
    assert _keyword_line(build_input(cfg, WATER_XYZ)).count("def2/J") == 1


@pytest.mark.parametrize("functional", ["HF-3c", "Native-GFN2-xTB", "Native-GFN-xTB"])
def test_methods_that_cannot_use_ri_get_no_ri_keyword(functional):
    """ORCA 6.1.1 refuses these outright with the picker's default RIJCOSX —
    "Incompatible choice for integral handling" / "Native xTB not compatible
    with RI", exit 55 — and runs them fine with no RI keyword at all."""
    assert method_forbids_ri(functional) is True
    cfg = StepConfig(kind="sp", functional=functional, ri_approximation="RIJCOSX")
    text = build_input(cfg, WATER_XYZ)
    line = _keyword_line(text)

    assert "RIJCOSX" not in line
    assert DEFAULT_AUX not in line
    assert any("RI approximation" in ln for ln in text.splitlines()
               if ln.startswith("#"))


@pytest.mark.parametrize("functional", ["r2SCAN-3c", "B97-3c", "PBEh-3c", "wB97X-3c",
                                        "B3LYP", "wB97X-D4"])
def test_methods_that_accept_ri_keep_it(functional):
    # HF-3c is the one composite ORCA refuses RI for; the others take RIJCOSX
    # and simply use their own settings (same energy to every digit)
    assert method_forbids_ri(functional) is False
    cfg = StepConfig(kind="sp", functional=functional, ri_approximation="RIJCOSX")
    assert "RIJCOSX" in _keyword_line(build_input(cfg, WATER_XYZ))


# ---------------------------------------------------------------------------
# normalize_functional (P30)
# ---------------------------------------------------------------------------

def test_minnesota_functional_names_map_to_orca_spelling():
    assert normalize_functional("M06-2X") == "M062X"
    assert normalize_functional("M06-L") == "M06L"


def test_scan_maps_to_scanfunc():
    assert normalize_functional("SCAN") == "SCANfunc"


def test_normalization_matches_case_insensitively_on_whole_token():
    assert normalize_functional("m06-2x") == "M062X"
    assert normalize_functional("Scan") == "SCANfunc"
    # surrounding whitespace is trimmed before lookup
    assert normalize_functional("  M06-2X  ") == "M062X"


def test_valid_hyphenated_keywords_pass_through_untouched():
    # P30: the map is an exact dict, never a hyphen-stripping heuristic --
    # these are all valid ORCA keywords WITH the hyphen and must survive.
    for name in ("CAM-B3LYP", "wB97X-D4", "wB97X-D3", "r2SCAN-3c",
                 "B97-D3", "LC-BLYP"):
        assert normalize_functional(name) == name


def test_unknown_functional_passes_verbatim_trimmed():
    assert normalize_functional("MYFUNC-42X") == "MYFUNC-42X"
    assert normalize_functional("  XTB2  ") == "XTB2"
    assert normalize_functional("") == ""


# ---------------------------------------------------------------------------
# dispersion normalization (P31)
# ---------------------------------------------------------------------------

def test_combined_dispersion_token_becomes_separate_d3bj():
    # ORCA rejects combined FUNC-D3 tokens, and bare "-D3" is ambiguous
    # between damping schemes: both spellings normalize to "FUNC D3BJ".
    assert normalize_functional("B3LYP-D3") == "B3LYP D3BJ"
    assert normalize_functional("B3LYP-D3BJ") == "B3LYP D3BJ"
    assert normalize_functional("BLYP-D3") == "BLYP D3BJ"
    assert normalize_functional("PBE0-D3BJ") == "PBE0 D3BJ"


def test_explicit_d3bj_and_d4_are_preserved():
    # An already-separated dispersion keyword is not a known mismatch and
    # must pass verbatim.
    assert normalize_functional("B3LYP D3BJ") == "B3LYP D3BJ"
    assert normalize_functional("wB97X-D4") == "wB97X-D4"


def test_build_input_never_emits_bare_dash_d3():
    cfg = StepConfig(kind="opt", functional="B3LYP-D3")
    text = build_input(cfg, WATER_XYZ)
    line = _keyword_line(text)
    assert "B3LYP D3BJ" in line
    # neither the combined token nor a bare "-D3" may reach the .inp
    assert "B3LYP-D3" not in text
    assert re.search(r"-D3\b(?!BJ)", text) is None


# ---------------------------------------------------------------------------
# _auto_aux (P31)
# ---------------------------------------------------------------------------

def test_auto_aux_rij_with_def2_basis_gets_def2j():
    cfg = StepConfig(functional="wB97X-D4", basis_set="def2-TZVP",
                     ri_approximation="RIJCOSX")
    assert _auto_aux(cfg) == DEFAULT_AUX == "def2/J"
    # and it lands on the keyword line of the generated input
    assert "def2/J" in _keyword_line(build_input(cfg, WATER_XYZ))


def test_auto_aux_double_hybrid_and_mp2_get_autoaux():
    # double hybrid / MP2 need a correlation-fitting /C aux as well
    dh = StepConfig(functional="B2PLYP", basis_set="def2-TZVP",
                    ri_approximation="RIJCOSX")
    assert _auto_aux(dh) == "AutoAux"
    mp2 = StepConfig(functional="MP2", basis_set="def2-TZVP",
                     ri_approximation="RIJCOSX")
    assert _auto_aux(mp2) == "AutoAux"
    # a combined-dispersion double hybrid still classifies via its base name
    dh_d3 = StepConfig(functional="B2PLYP-D3", basis_set="def2-TZVP",
                       ri_approximation="RIJCOSX")
    assert _auto_aux(dh_d3) == "AutoAux"


def test_auto_aux_rijk_def2_gets_def2jk_and_nondef2_gets_autoaux():
    def2 = StepConfig(functional="B3LYP", basis_set="def2-TZVP",
                      ri_approximation="RIJK")
    assert _auto_aux(def2) == "def2/JK"
    other = StepConfig(functional="B3LYP", basis_set="cc-pVTZ",
                       ri_approximation="RIJK")
    assert _auto_aux(other) == "AutoAux"


def test_auto_aux_user_supplied_aux_suppresses_auto():
    # P31: if the user set an aux (or AutoAux) in options, add nothing
    explicit = StepConfig(functional="wB97X-D4", basis_set="def2-TZVP",
                          ri_approximation="RIJCOSX", options="def2/J")
    assert _auto_aux(explicit) == ""
    autoaux = StepConfig(functional="B2PLYP", basis_set="def2-TZVP",
                         ri_approximation="RIJCOSX", options="AutoAux")
    assert _auto_aux(autoaux) == ""


def test_auto_aux_ri_picker_autoaux_not_duplicated():
    # "AutoAux" is a selectable RI-approximation choice
    # (data/ri_approximations.json) and goes on the ! line verbatim; _auto_aux
    # adding a second one would emit a duplicated simple-input keyword, which
    # ORCA aborts on.
    mp2 = StepConfig(functional="MP2", basis_set="def2-TZVP",
                     ri_approximation="AutoAux")
    assert _auto_aux(mp2) == ""
    line = _keyword_line(build_input(mp2, WATER_XYZ))
    assert line.upper().count("AUTOAUX") == 1
    dh = StepConfig(functional="B2PLYP", basis_set="def2-TZVP",
                    ri_approximation="AutoAux")
    assert _auto_aux(dh) == ""


def test_auto_aux_nori_adds_nothing():
    plain = StepConfig(functional="wB97X-D4", basis_set="def2-TZVP",
                       ri_approximation="NoRI")
    assert _auto_aux(plain) == ""
    # even a double hybrid: NoRI means the conventional path, no aux
    dh = StepConfig(functional="B2PLYP", basis_set="def2-TZVP",
                    ri_approximation="NoRI")
    assert _auto_aux(dh) == ""


def test_auto_aux_unknown_basis_with_rij_adds_nothing():
    # P31: don't guess an aux for a non-def2 orbital basis
    cfg = StepConfig(functional="wB97X-D4", basis_set="cc-pVTZ",
                     ri_approximation="RIJCOSX")
    assert _auto_aux(cfg) == ""


# ---------------------------------------------------------------------------
# build_input
# ---------------------------------------------------------------------------

def test_build_input_puts_charge_and_multiplicity_on_coordinate_line():
    cfg = StepConfig(kind="sp", calculation_type="SP")
    text = build_input(cfg, WATER_XYZ, charge=-1, multiplicity=2)
    lines = text.splitlines()
    assert "* xyz -1 2" in lines
    # the coordinate block is terminated by a bare "*"
    star = lines.index("* xyz -1 2")
    assert "*" in lines[star + 1:]
    # coordinates appear between the two markers
    assert lines[star + 1].split()[0] == "O"


def test_render_raw_input_substitutes_geometry_placeholder():
    raw = f"! B3LYP def2-SVP\n* xyz 0 1\n{GEOMETRY_PLACEHOLDER}\n*\n"
    out = render_raw_input(raw, WATER_XYZ)
    assert GEOMETRY_PLACEHOLDER not in out
    assert "O    0.000000    0.000000    0.117300" in out


def test_render_raw_input_without_placeholder_is_verbatim():
    raw = "! B3LYP def2-SVP\n* xyz 0 1\nO 0.0 0.0 0.0\n*\n"
    assert render_raw_input(raw, WATER_XYZ) == raw


def test_build_input_template_embeds_placeholder_for_references():
    cfg = StepConfig(kind="opt")
    text = build_input_template(cfg, charge=0, multiplicity=1,
                                use_placeholder=True)
    assert GEOMETRY_PLACEHOLDER in text
    direct = build_input_template(cfg, charge=0, multiplicity=1,
                                  use_placeholder=False, xyz=WATER_XYZ)
    assert GEOMETRY_PLACEHOLDER not in direct


def test_ts_opt_emits_calc_hess_true_and_plain_opt_does_not():
    ts = build_input(StepConfig(kind="ts_opt", calculation_type="OptTS"),
                     WATER_XYZ)
    assert "%geom" in ts
    assert "Calc_Hess true" in ts
    plain = build_input(StepConfig(kind="opt", calculation_type="TightOpt"),
                        WATER_XYZ)
    assert "%geom" in plain
    assert "Calc_Hess" not in plain


def test_freq_block_omitted_at_orca_defaults():
    cfg = StepConfig(kind="freq", calculation_type="Freq")  # 298.15 K, 1 atm
    assert "%freq" not in build_input(cfg, WATER_XYZ)


def test_freq_block_emitted_when_temp_or_pressure_changed():
    hot = StepConfig(kind="freq", calculation_type="Freq", freq_temp_k=310.15)
    text = build_input(hot, WATER_XYZ)
    assert "%freq" in text
    assert "Temp 310.15" in text
    pressed = StepConfig(kind="freq", calculation_type="Freq",
                         freq_pressure_atm=2.0)
    text = build_input(pressed, WATER_XYZ)
    assert "%freq" in text
    assert "Pressure 2.0" in text


# ---------------------------------------------------------------------------
# StepConfig.from_dict clamping (P34)
# ---------------------------------------------------------------------------

def test_from_dict_clamps_nprocs_and_maxcore_to_range():
    assert StepConfig.from_dict({"nprocs": 999_999}).nprocs == 1024
    assert StepConfig.from_dict({"nprocs": -5}).nprocs == 1
    assert StepConfig.from_dict({"nprocs": 8}).nprocs == 8
    assert StepConfig.from_dict({"maxcore_mb": 1}).maxcore_mb == 64
    assert StepConfig.from_dict({"maxcore_mb": 10**9}).maxcore_mb == 1_000_000


def test_from_dict_coerces_numeric_strings_and_defaults_on_injection():
    # numbers arriving as strings across the wire are coerced ...
    assert StepConfig.from_dict({"nprocs": "12"}).nprocs == 12
    # ... but a non-numeric string (would be line injection into %pal)
    # falls back to the default
    assert StepConfig.from_dict({"nprocs": "6 end %pal"}).nprocs == 6
    assert StepConfig.from_dict({"maxcore_mb": None}).maxcore_mb == 2400


def test_from_dict_maps_removed_mediumscf_to_a_valid_tier():
    # ORCA has no MediumSCF tier (it aborts: "UNRECOGNIZED OR DUPLICATED
    # KEYWORD(S) IN SIMPLE INPUT LINE"). A calc/session that still carries it is
    # mapped to the nearest valid tier at the trust boundary, so the emitted
    # '!' line never contains MediumSCF.
    assert StepConfig.from_dict({"scf_convergence": "MediumSCF"}).scf_convergence == "NormalSCF"
    assert StepConfig.from_dict({"scf_convergence": "mediumscf"}).scf_convergence == "NormalSCF"
    cfg = StepConfig.from_dict({"scf_convergence": "MediumSCF", "functional": "PBE",
                                "basis_set": "def2-SVP"})
    assert "MediumSCF" not in build_input(cfg, WATER_XYZ)
    # a valid tier is left untouched
    assert StepConfig.from_dict({"scf_convergence": "TightSCF"}).scf_convergence == "TightSCF"


def test_from_dict_nan_float_falls_back_to_default():
    cfg = StepConfig.from_dict({"freq_temp_k": float("nan"),
                                "freq_pressure_atm": "nan"})
    assert cfg.freq_temp_k == 298.15
    assert cfg.freq_pressure_atm == 1.0
    assert not math.isnan(cfg.freq_temp_k)


def test_from_dict_filters_unknown_fields_instead_of_rejecting():
    # forward compatibility: unknown keys are dropped, not an error
    cfg = StepConfig.from_dict({"kind": "sp", "some_future_field": 123})
    assert cfg.kind == "sp"
    assert not hasattr(cfg, "some_future_field")


# ---------------------------------------------------------------------------
# check_neb_atom_order (P26: detect, don't auto-fix)
# ---------------------------------------------------------------------------

NEB_REACTANT = (
    "C    0.0  0.0  0.0\n"
    "H    0.0  0.0  1.1\n"
    "O    1.2  0.0  0.0"
)


def test_neb_atom_count_mismatch_is_reported():
    product = "C 0.0 0.0 0.0\nH 0.0 0.0 1.1"
    res = check_neb_atom_order(NEB_REACTANT, product)
    assert res["ok"] is False
    assert "count" in res["error"].lower()
    assert res["mismatch_index"] is None


def test_neb_composition_mismatch_is_reported():
    # same count, different elements (H replaced by F)
    product = "C 0.0 0.0 0.0\nF 0.0 0.0 1.1\nO 1.2 0.0 0.0"
    res = check_neb_atom_order(NEB_REACTANT, product)
    assert res["ok"] is False
    assert "composition" in res["error"].lower()
    assert res["mismatch_index"] is None


def test_neb_order_mismatch_reports_first_divergent_index():
    # same composition, H and O swapped -> first divergence at index 1
    product = "C 0.0 0.0 0.0\nO 1.2 0.0 0.0\nH 0.0 0.0 1.1"
    res = check_neb_atom_order(NEB_REACTANT, product)
    assert res["ok"] is False
    assert res["mismatch_index"] == 1
    assert "#2" in res["error"]  # human-facing message is 1-based


def test_neb_matching_structures_pass():
    # identical order, different coordinates (that's the whole point of NEB)
    product = "C 0.5 0.0 0.0\nH 0.5 0.0 1.2\nO 1.9 0.0 0.0"
    res = check_neb_atom_order(NEB_REACTANT, product)
    assert res == {"ok": True, "error": "", "mismatch_index": None}


def test_neb_empty_input_is_rejected():
    res = check_neb_atom_order("", NEB_REACTANT)
    assert res["ok"] is False
    assert res["mismatch_index"] is None


def test_from_dict_survives_non_string_basis_element():
    """Trust boundary (P34): a malformed phone payload with a non-string
    element must not crash .strip() at deserialization. Non-string values are
    str()-coerced (matching the constructor's existing coercion); empty/None
    elements are filtered.
    Regression: the filter clause used to call .strip() on the raw value."""
    cfg = StepConfig.from_dict({"basis_assignments": [
        {"element": 5},                          # non-string -> coerced "5"
        {"element": None},                       # empty -> filtered
        {"element": "  "},                       # blank -> filtered
        {"element": "Ir", "basis": "def2-TZVP"},  # valid -> kept
    ]})
    assert [b.element for b in cfg.basis_assignments] == ["5", "Ir"]


def test_from_dict_coerces_non_string_str_fields_to_defaults():
    """Trust boundary (P34): non-string values for str-typed fields must
    degrade to the field default at deserialization, not crash later inside
    build_input (e.g. .upper() in _auto_aux)."""
    cfg = StepConfig.from_dict({"kind": 123, "options": 42,
                                "solvation": {"model": 7, "solvent": None}})
    assert isinstance(cfg.kind, str)
    assert isinstance(cfg.options, str)
    assert isinstance(cfg.solvation.model, str)
    assert isinstance(cfg.solvation.solvent, str)
    # and the config must survive input generation end to end
    text = build_input(cfg, "H 0.0 0.0 0.0")
    assert "* xyz" in text


# ---- IRC: an explicit "read" Hessian selection is never silently replaced ----
def test_irc_inithess_read_requires_a_filename():
    # Falling back to calc_anfreq would run a different (and possibly far more
    # expensive) method than the one the user chose — the generator refuses.
    cfg = StepConfig(kind="irc", irc_init_hess="read", irc_hess_file="")
    with pytest.raises(ValueError, match="requires a .hess filename"):
        build_input(cfg, WATER_XYZ)


def test_irc_inithess_read_with_filename_emits_read_block():
    cfg = StepConfig(kind="irc", irc_init_hess="read", irc_hess_file="TS2.hess")
    text = build_input(cfg, WATER_XYZ)
    assert "InitHess read" in text
    assert 'Hess_Filename "TS2.hess"' in text


# ---------------------------------------------------------------------------
# Solvation: name normalization + %cpcm block routing for space-named solvents
# ---------------------------------------------------------------------------

def test_solvent_alias_maps_picker_label_to_orca_name():
    # picker labels ORCA's own solvent table spells differently are mapped
    # (exact dict, case-insensitive), unknown names pass through verbatim
    cfg = StepConfig(kind="sp", solvation=Solvation(model="SMD",
                                                    solvent="Ethylene Glycol"))
    line = _keyword_line(build_input(cfg, WATER_XYZ))
    assert "SMD(1,2-ethanediol)" in line
    cfg2 = StepConfig(kind="sp", solvation=Solvation(model="SMD",
                                                     solvent="2-Butanone"))
    assert "SMD(butanone)" in _keyword_line(build_input(cfg2, WATER_XYZ))
    passthru = StepConfig(kind="sp", solvation=Solvation(model="CPCM",
                                                         solvent="Water"))
    assert "CPCM(Water)" in _keyword_line(build_input(passthru, WATER_XYZ))


def test_space_named_solvent_routes_through_cpcm_block():
    # ORCA's simple-input parser splits on whitespace: CPCM(Diethyl Ether) is
    # an INPUT ERROR. Space-named solvents carry only the activation keyword
    # on the ! line and select the solvent in a quoted %cpcm block.
    cpcm = StepConfig(kind="sp", solvation=Solvation(model="CPCM",
                                                     solvent="Diethyl Ether"))
    text = build_input(cpcm, WATER_XYZ)
    line = _keyword_line(text)
    assert "CPCM" in line and "(" not in line.split("CPCM")[1][:1]
    assert 'solvent "Diethyl Ether"' in text
    assert "Ether)" not in line

    smd = StepConfig(kind="sp", solvation=Solvation(model="SMD",
                                                    solvent="Carbon Tetrachloride"))
    text2 = build_input(smd, WATER_XYZ)
    line2 = _keyword_line(text2)
    assert "SMD(" not in line2       # activation via CPCM keyword + block
    assert "smd true" in text2
    assert 'smdsolvent "Carbon Tetrachloride"' in text2


def test_gas_phase_emits_no_cpcm_block():
    cfg = StepConfig(kind="sp")
    text = build_input(cfg, WATER_XYZ)
    assert "%cpcm" not in text


# ---------------------------------------------------------------------------
# _auto_aux: correlated wavefunction methods beyond MP2 need /C too
# ---------------------------------------------------------------------------

def test_auto_aux_correlated_wavefunction_methods_get_autoaux():
    # a /J-only aux makes ORCA abort AFTER the full SCF for CC/QCISD/NEVPT2/
    # CASPT2/ADC2 methods — same family as the MP2 case
    for method in ("CCSD", "CCSD(T)", "DLPNO-CCSD(T)", "QCISD(T)",
                   "NEVPT2", "CASPT2", "STEOM-CCSD", "ADC2"):
        cfg = StepConfig(functional=method, basis_set="def2-SVP",
                         ri_approximation="RIJCOSX")
        assert _auto_aux(cfg) == "AutoAux", method
    # NoRI still means the conventional path — nothing added
    nori = StepConfig(functional="CCSD(T)", basis_set="def2-SVP",
                      ri_approximation="NoRI")
    assert _auto_aux(nori) == ""


# ---------------------------------------------------------------------------
# NEB atom-order check: element symbols compare case-insensitively
# ---------------------------------------------------------------------------

def test_neb_atom_order_element_case_insensitive():
    # legacy tools write "CL"; ORCA itself is case-insensitive, so the check
    # must not refuse chemically identical, correctly ordered structures
    res = check_neb_atom_order("Cl 0 0 0\nH 1 0 0", "CL 0 0 1\nH 1 0 1")
    assert res["ok"] is True
    # a REAL order mismatch still reports, with normalized symbols
    bad = check_neb_atom_order("Cl 0 0 0\nH 1 0 0", "H 0 0 1\nCL 1 0 1")
    assert bad["ok"] is False and bad["mismatch_index"] == 0


def test_clamp_int_infinite_value_degrades_to_default():
    # JSON 1e999 parses to float("inf"); int(inf) raises OverflowError which
    # must degrade like any other non-numeric, not escape the trust boundary
    cfg = StepConfig.from_dict({"nprocs": float("inf"),
                                "maxcore_mb": float("-inf")})
    assert cfg.nprocs == StepConfig().nprocs
    assert cfg.maxcore_mb == StepConfig().maxcore_mb


# ---------------------------------------------------------------------------
# DLPNO is RI by construction
#
# Turning RI off does not give a DLPNO method a conventional path, it gives it
# no path at all. Measured on ORCA 6.1.1: "! DLPNO-CCSD(T) def2-SVP NoRI"
# aborts in mdci_check; the same line plus AutoAux runs normally; and plain
# "! MP2 def2-SVP NoRI" runs fine, which is why the NoRI escape stays for
# everything else.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("functional", ["DLPNO-CCSD(T)", "DLPNO-CCSD", "DLPNO-MP2"])
def test_dlpno_gets_an_aux_even_with_ri_switched_off(functional):
    cfg = StepConfig(kind="sp", functional=functional, basis_set="def2-SVP",
                     ri_approximation="NoRI")
    assert "AutoAux" in _keyword_line(build_input(cfg, WATER_XYZ))


def test_a_conventional_correlated_method_still_honours_nori():
    cfg = StepConfig(kind="sp", functional="MP2", basis_set="def2-SVP",
                     ri_approximation="NoRI")
    assert "AutoAux" not in _keyword_line(build_input(cfg, WATER_XYZ))
