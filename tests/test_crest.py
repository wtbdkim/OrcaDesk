"""
Tests for the CREST pipeline (orcamgr/crest/): ensemble parsing against a REAL
CREST 3.0.2 output corpus (ethanol, GFN2 — see tests/crest/fixtures/), CLI-flag
building, per-kind validation, and the QueueEngine integration via a fake runner
that needs no WSL and no CREST binary.

Contract references (PRINCIPLES.md):
  P3  — parser evidence comes from real ORCA/CREST output, not invented text.
  P6  — failure is data: a run without a usable conformer is a failure, not a
        silently-empty success.
  P25 — success is judged per-kind: a crest_conf must produce >=1 conformer.

The QueueEngine test fakes CrestRunner + resolve_run_target so no wsl.exe is
spawned; the parser tests read the checked-in real ensemble files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import orcamgr.crest.env as crest_env_mod
import orcamgr.crest.runner as crest_runner_mod
from orcamgr.core.input_generator import StepConfig
from orcamgr.core.queue import (
    Calculation, CalcState, GeometrySource, QueueCallbacks, QueueEngine,
    expand_conformer_tracks, validate_result,
)
from orcamgr.core.parser import ParseResult, Conformer, HARTREE_TO_KCAL
from orcamgr.core.runner import OrcaRunError
from orcamgr.core.parser import Atom
from orcamgr.crest.parser import parse_crest_result, CREST_SUCCESS_MARKER
from orcamgr.crest.runner import build_crest_argv
from orcamgr.state.store import queue_needs_orca

FIXTURES = Path(__file__).parent / "crest" / "fixtures" / "ethanol"


# ---------------------------------------------------------------------------
# 1. parse_crest_result against the REAL ethanol ensemble (P3)
# ---------------------------------------------------------------------------

def test_parse_crest_result_reads_real_ethanol_ensemble():
    r = parse_crest_result(str(FIXTURES / "crest.out"))

    assert r.is_conformer_search is True
    assert r.terminated_normally is True          # marker present in crest.out
    assert r.error_message == ""
    assert len(r.conformers) == 2
    # absolute energies come from the .xyz comment lines (Hartree)
    assert r.conformers[0].energy_eh == pytest.approx(-11.39433939)
    assert r.conformers[1].energy_eh == pytest.approx(-11.39186743)
    # relative energies computed from the min match CREST's own crest.energies
    assert r.conformers[0].rel_kcal == pytest.approx(0.0, abs=1e-6)
    assert r.conformers[1].rel_kcal == pytest.approx(1.551, abs=2e-3)
    # geometry / final energy are set to the lowest-energy conformer so the
    # existing best-geometry reference path keeps working
    assert r.final_energy_eh == pytest.approx(-11.39433939)
    assert r.n_atoms == 9
    assert len(r.geometry) == 9
    assert r.geometry[0].symbol == "C"


def test_crest_relative_energy_matches_hartree_to_kcal_conversion():
    r = parse_crest_result(str(FIXTURES / "crest.out"))
    delta = (r.conformers[1].energy_eh - r.conformers[0].energy_eh) * HARTREE_TO_KCAL
    assert r.conformers[1].rel_kcal == pytest.approx(delta)


def test_shows_electronic_props_false_for_conformer_search():
    # a conformer search is a specialty result — no SCF electronic props to show
    r = parse_crest_result(str(FIXTURES / "crest.out"))
    assert r.shows_electronic_props is False


# ---------------------------------------------------------------------------
# 2. parser edge cases
# ---------------------------------------------------------------------------

def test_missing_ensemble_yields_failure_without_raising(tmp_path):
    r = parse_crest_result(str(tmp_path / "job" / "job.out"))
    assert r.terminated_normally is False
    assert r.error_message
    assert r.conformers == []


def test_marker_absent_is_reported_as_not_terminated(tmp_path):
    # ensemble present but the .out lacks the success marker -> not normal
    (tmp_path / "crest_conformers.xyz").write_text(
        "1\n   -1.0\n H 0.0 0.0 0.0\n", encoding="utf-8")
    (tmp_path / "job.out").write_text("some log without the marker\n", encoding="utf-8")
    r = parse_crest_result(str(tmp_path / "job.out"))
    assert r.conformers                      # it did parse the structure
    assert r.terminated_normally is False
    assert "normal termination" in r.error_message.lower()


def test_ensemble_present_without_out_file_is_inferred_successful(tmp_path):
    # if the .out is unavailable, a non-empty ensemble is taken as success
    (tmp_path / "crest_conformers.xyz").write_text(
        "1\n   -1.0\n H 0.0 0.0 0.0\n", encoding="utf-8")
    r = parse_crest_result(str(tmp_path / "job.out"))   # .out does not exist
    assert r.terminated_normally is True
    assert len(r.conformers) == 1


def test_crash_exit_code_is_surfaced_not_hidden_as_no_ensemble(tmp_path):
    # A CREST segfault (exit 139) must be reported as a crash, not a bland
    # "no conformer ensemble" (the real user-reported case).
    (tmp_path / "job.crest.rc").write_text("139\n", encoding="utf-8")
    (tmp_path / "job.out").write_text(
        "CREST banner...\nProgram received signal SIGSEGV: Segmentation fault.\n",
        encoding="utf-8")
    r = parse_crest_result(str(tmp_path / "job.out"))
    assert r.terminated_normally is False
    assert not r.conformers
    assert "139" in r.error_message
    assert "again" in r.error_message.lower()   # tells the user to retry


def test_clean_exit_without_ensemble_is_reported_distinctly(tmp_path):
    (tmp_path / "job.crest.rc").write_text("0\n", encoding="utf-8")
    (tmp_path / "job.out").write_text("did nothing useful\n", encoding="utf-8")
    r = parse_crest_result(str(tmp_path / "job.out"))
    assert r.terminated_normally is False
    assert "exit 0" in r.error_message.lower() or "no conformer" in r.error_message.lower()


def test_conformers_are_ranked_by_energy(tmp_path):
    # deliberately out-of-order input; parser must sort ascending by energy
    (tmp_path / "crest_conformers.xyz").write_text(
        "1\n   -4.0\n H 0.0 0.0 0.0\n"
        "1\n   -6.0\n H 0.1 0.0 0.0\n"
        "1\n   -5.0\n H 0.2 0.0 0.0\n", encoding="utf-8")
    r = parse_crest_result(str(tmp_path / "job.out"))
    assert [c.energy_eh for c in r.conformers] == [-6.0, -5.0, -4.0]
    assert r.conformers[0].rel_kcal == pytest.approx(0.0)
    assert r.final_energy_eh == pytest.approx(-6.0)


# ---------------------------------------------------------------------------
# 3. build_crest_argv
# ---------------------------------------------------------------------------

def test_build_crest_argv_basic_gfn2():
    cfg = StepConfig(kind="crest_conf", crest_method="gfn2", crest_solvent="",
                     crest_ewin=6.0, crest_threads=4)
    argv = build_crest_argv(cfg, charge=0, multiplicity=1)
    assert argv[0] == "--gfn2"
    assert "--alpb" not in argv                 # no solvent
    assert "--ewin" in argv and "6" in argv
    assert argv[argv.index("-T") + 1] == "4"
    # --uhf is unpaired electrons = multiplicity - 1, NOT the multiplicity
    assert argv[argv.index("--uhf") + 1] == "0"
    assert argv[argv.index("--chrg") + 1] == "0"


def test_build_crest_argv_uhf_is_multiplicity_minus_one():
    cfg = StepConfig(kind="crest_conf")
    argv = build_crest_argv(cfg, charge=-1, multiplicity=3)   # triplet anion
    assert argv[argv.index("--uhf") + 1] == "2"
    assert argv[argv.index("--chrg") + 1] == "-1"


def test_build_crest_argv_solvent_and_method_flags():
    cfg = StepConfig(kind="crest_conf", crest_method="gfnff", crest_solvent="water")
    argv = build_crest_argv(cfg, charge=0, multiplicity=1)
    assert argv[0] == "--gfnff"
    assert argv[argv.index("--alpb") + 1] == "water"


def test_build_crest_argv_unknown_method_is_prefixed():
    cfg = StepConfig(kind="crest_conf", crest_method="gfn2//gfnff")
    argv = build_crest_argv(cfg, 0, 1)
    assert argv[0] == "--gfn2//gfnff"


def test_build_crest_argv_defaults_emit_no_advanced_flags():
    argv = build_crest_argv(StepConfig(kind="crest_conf"), 0, 1)
    for flag in ("--nci", "--quick", "--squick", "--mquick", "--mdlen", "--tstep",
                 "--tnmd", "--mddump", "--vbdump", "--norotmd", "--cbonds",
                 "--subrmsd", "--cluster", "--keepdir", "--gbsa"):
        assert flag not in argv


def test_build_crest_argv_advanced_flags_and_values():
    cfg = StepConfig(
        kind="crest_conf", crest_preset="quick", crest_nci=True,
        crest_solvent="water", crest_solvent_model="gbsa",
        crest_mdlen_mult=2.0, crest_tstep_fs=2.0, crest_tnmd_k=500.0,
        crest_mddump_fs=200, crest_vbdump_ps=0.5,
        crest_norotmd=True, crest_cbonds=True, crest_subrmsd=True,
        crest_cluster=True, crest_keepdir=True)
    argv = build_crest_argv(cfg, 0, 1)
    assert "--quick" in argv and "--nci" in argv
    # gbsa model switches the solvent flag; alpb must NOT also appear
    assert argv[argv.index("--gbsa") + 1] == "water" and "--alpb" not in argv
    assert argv[argv.index("--mdlen") + 1] == "x2"          # multiplier syntax
    assert argv[argv.index("--tstep") + 1] == "2"
    assert argv[argv.index("--tnmd") + 1] == "500"
    assert argv[argv.index("--mddump") + 1] == "200"
    assert argv[argv.index("--vbdump") + 1] == "0.5"
    for flag in ("--norotmd", "--cbonds", "--subrmsd", "--cluster", "--keepdir"):
        assert flag in argv


def test_build_crest_argv_preset_and_model_reject_bad_values():
    # from_dict is the trust boundary: bogus enum values degrade to defaults
    cfg = StepConfig.from_dict({"kind": "crest_conf", "crest_preset": "turbo",
                                "crest_solvent_model": "cosmo", "crest_solvent": "thf",
                                "crest_mdlen_mult": "9e9"})
    assert cfg.crest_preset == "" and cfg.crest_solvent_model == "alpb"
    assert cfg.crest_mdlen_mult == 100.0                    # clamped to the max
    argv = build_crest_argv(cfg, 0, 1)
    assert "--quick" not in argv
    assert argv[argv.index("--alpb") + 1] == "thf"          # bad model -> default alpb


# ---------------------------------------------------------------------------
# 4. validate_result for crest_conf (P25 / P6)
# ---------------------------------------------------------------------------

def _crest_calc(name="conf"):
    return Calculation(name=name, kind="crest_conf",
                       config=StepConfig(kind="crest_conf"), xyz="H 0 0 0")


def test_validate_passes_for_terminated_with_conformers():
    r = ParseResult(is_conformer_search=True, terminated_normally=True,
                    conformers=[Conformer(1, -1.0, 0.0, [])])
    validate_result(_crest_calc(), r)          # must not raise


def test_validate_fails_when_no_conformers():
    r = ParseResult(is_conformer_search=True, terminated_normally=True, conformers=[])
    with pytest.raises(OrcaRunError):
        validate_result(_crest_calc(), r)


def test_validate_fails_when_not_terminated_normally():
    r = ParseResult(is_conformer_search=True, terminated_normally=False,
                    error_message="CREST did not report normal termination.")
    with pytest.raises(OrcaRunError):
        validate_result(_crest_calc(), r)


# ---------------------------------------------------------------------------
# 5. queue_needs_orca excludes crest (all-CREST queue runs with no ORCA path)
# ---------------------------------------------------------------------------

def test_all_crest_queue_does_not_need_orca():
    calcs = [_crest_calc("a"), _crest_calc("b")]
    assert queue_needs_orca(calcs) is False


def test_mixed_queue_with_an_orca_calc_needs_orca():
    orca = Calculation(name="opt", kind="opt", config=StepConfig(kind="opt"), xyz="H 0 0 0")
    assert queue_needs_orca([_crest_calc("a"), orca]) is True


# ---------------------------------------------------------------------------
# 6. QueueEngine integration via a fake runner (no WSL, no crest binary)
# ---------------------------------------------------------------------------

_FAKE_ENSEMBLE = (
    "1\n        -5.00000000\n H 0.000000 0.000000 0.000000\n"
    "1\n        -4.99840000\n H 0.100000 0.000000 0.000000\n"
)


class _FakeCrestRunner:
    """Stand-in for CrestRunner: writes the ensemble + marker .out the real
    detached WSL script would produce, with no wsl.exe involved."""
    written_empty = False   # class flag to force a no-conformer (failed) run

    def __init__(self, distro, crest_bin):
        self.distro = distro

    def cancel(self): pass
    def detach(self): pass
    def adopt(self, pid, ct): pass
    def is_alive(self): return False
    def end_position(self, out_path): return 0

    def launch(self, script_path, name, out_path):
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if not _FakeCrestRunner.written_empty:
            (out_path.parent / "crest_conformers.xyz").write_text(_FAKE_ENSEMBLE, encoding="utf-8")
        out_path.write_text("fake crest log\n " + CREST_SUCCESS_MARKER + "\n", encoding="utf-8")
        (out_path.parent / f"{name}.crest.rc").write_text("0\n", encoding="utf-8")
        return 4321, 999.0

    def monitor(self, out_path, name, on_line=None, start_pos=0):
        if on_line:
            on_line("[crest] fake progress line")
        return 0


def _recording_callbacks():
    logs, updates = [], []
    cb = QueueCallbacks(log=lambda m, l: logs.append((l, m)),
                        calc_update=lambda i, c: updates.append((i, c.state)))
    return cb, logs, updates


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeCrestRunner.written_empty = False
    yield
    _FakeCrestRunner.written_empty = False


def _patch_runner(monkeypatch):
    monkeypatch.setattr(crest_runner_mod, "CrestRunner", _FakeCrestRunner)
    monkeypatch.setattr(crest_env_mod, "resolve_run_target",
                        lambda pref="", timeout=30.0: ("FakeDistro", "/fake/bin/crest"))


def test_queue_engine_runs_crest_conf_to_done(tmp_path, monkeypatch):
    _patch_runner(monkeypatch)
    cb, logs, _ = _recording_callbacks()
    engine = QueueEngine("", str(tmp_path), cb)
    calc = _crest_calc("conf")

    engine.run_all([calc])

    assert calc.state == CalcState.DONE
    assert "2 conformer" in calc.message
    assert calc.result is not None
    assert len(calc.result.conformers) == 2
    assert calc.result.final_energy_eh == pytest.approx(-5.0)
    assert len(calc.result.geometry) == 1
    # the resolved distro is persisted on the config so a reattach can find it
    assert calc.config.crest_env_id == "FakeDistro"
    assert calc.output_path.endswith(".out")
    assert any(level == "ok" for level, _ in logs)


def test_queue_engine_crest_no_conformers_is_failed(tmp_path, monkeypatch):
    _patch_runner(monkeypatch)
    _FakeCrestRunner.written_empty = True     # produce no ensemble
    cb, _logs, _ = _recording_callbacks()
    engine = QueueEngine("", str(tmp_path), cb)
    calc = _crest_calc("conf")

    engine.run_all([calc])

    assert calc.state == CalcState.FAILED
    assert "conformer" in calc.message.lower()


def test_crest_failure_blocks_dependent_not_whole_queue(tmp_path, monkeypatch):
    # P23: a failed crest parent blocks its dependent; an unrelated crest runs on.
    _patch_runner(monkeypatch)
    _FakeCrestRunner.written_empty = True      # every crest run here fails
    cb, _logs, _ = _recording_callbacks()
    engine = QueueEngine("", str(tmp_path), cb)

    parent = _crest_calc("parent")
    child = _crest_calc("child")
    child.geometry_source = GeometrySource.REFERENCE
    child.ref_name = "parent"
    unrelated = _crest_calc("unrelated")

    engine.run_all([parent, child, unrelated])

    assert parent.state == CalcState.FAILED
    assert child.state == CalcState.BLOCKED
    assert unrelated.state == CalcState.FAILED
    assert "dependency" not in unrelated.message.lower()


# ---------------------------------------------------------------------------
# 6b. _failure_detail: subdivided, actionable no-ensemble diagnoses
# ---------------------------------------------------------------------------

def _write_failed_crest(tmp_path, rc, out_text):
    """A run folder with an exit-code marker + .out but no ensemble files."""
    (tmp_path / "t.crest.rc").write_text(f"{rc}\n", encoding="utf-8")
    (tmp_path / "t.out").write_text(out_text, encoding="utf-8")
    return str(tmp_path / "t.out")


def test_failure_detail_names_topology_change(tmp_path):
    # Real CREST failure mode (P3): the initial optimization changed the bonding
    # topology and CREST safety-terminated (exit 1). The message must name the
    # cause + the fix, not a bland "no conformers".
    out = _write_failed_crest(tmp_path, 1,
        " *WARNING* Change in topology detected!\n"
        " ERROR STOP safety termination of CREST\n")
    r = parse_crest_result(out)
    assert not r.terminated_normally and not r.conformers
    msg = r.error_message.lower()
    assert "topology" in msg and "pre-optimize" in msg


def test_failure_detail_segfault_is_flagged_as_intermittent(tmp_path):
    out = _write_failed_crest(tmp_path, 139, " forrtl: severe\n Segmentation fault\n")
    r = parse_crest_result(out)
    assert "segmentation fault" in r.error_message.lower()
    assert "139" in r.error_message


def test_failure_detail_generic_exit_code_still_reported(tmp_path):
    # An unknown non-zero exit falls back to the exit-code + last-output line.
    out = _write_failed_crest(tmp_path, 2, " some progress\n ERROR: mystery abort\n")
    r = parse_crest_result(out)
    assert "exit code 2" in r.error_message


# ---------------------------------------------------------------------------
# 7. synthetic multi-conformer results (shared by the expansion tests below)
# ---------------------------------------------------------------------------

def _result_with_conformers(n=2):
    confs = []
    for k in range(1, n + 1):
        geom = [Atom("O", 0.0, 0.0, 0.1 * k), Atom("H", 0.0, 0.75, -0.4)]
        confs.append(Conformer(index=k, energy_eh=-5.0 + 0.001 * k,
                               rel_kcal=0.627 * k, geometry=geom))
    return ParseResult(is_conformer_search=True, terminated_normally=True,
                       conformers=confs, geometry=confs[0].geometry,
                       final_energy_eh=confs[0].energy_eh)


# ---------------------------------------------------------------------------
# 8. per-conformer track expansion (crest_handoff="all"):
#    a queued reference chain crest <- opt <- freq fans out into one clone
#    chain per conformer when the search finishes.
# ---------------------------------------------------------------------------

def _ref_calc(name, kind, ref, charge=0, mult=1):
    return Calculation(name=name, kind=kind, config=StepConfig(kind=kind),
                       charge=charge, multiplicity=mult,
                       geometry_source=GeometrySource.REFERENCE, ref_name=ref)


def _done_all_crest(name="cr", n=3):
    c = _crest_calc(name)
    c.config.crest_handoff = "all"
    c.state = CalcState.DONE
    c.result = _result_with_conformers(n)
    return c


def test_expand_fans_reference_chain_out_track_major():
    crest = _done_all_crest("cr", 3)
    opt = _ref_calc("opt", "opt", "cr", charge=-1, mult=2)
    freq = _ref_calc("freq", "freq", "opt")
    calcs = [crest, opt, freq]

    removed, added = expand_conformer_tracks(
        crest, crest.result, calcs, taken_names={c.name for c in calcs})

    assert removed == ["opt", "freq"]
    assert [c.name for c in added] == [
        "opt_c1", "freq_c1", "opt_c2", "freq_c2", "opt_c3", "freq_c3"]
    o1, f1 = added[0], added[1]
    # 1-hop clone: the conformer is baked in as DIRECT geometry; the template's
    # own charge/multiplicity are kept (the template IS the user's spec)
    assert o1.geometry_source == GeometrySource.DIRECT and o1.ref_name == ""
    row = o1.xyz.splitlines()[0].split()
    assert row[0] == "O" and len(row) == 4
    assert o1.charge == -1 and o1.multiplicity == 2
    # deeper clone: REFERENCE re-pointed to the SAME-track parent clone
    assert f1.geometry_source == GeometrySource.REFERENCE and f1.ref_name == "opt_c1"
    assert added[3].ref_name == "opt_c2"
    # tracks get independent config copies
    o1.config.functional = "PBE0"
    assert opt.config.functional != "PBE0"
    # different conformers -> different baked geometries
    assert added[0].xyz != added[2].xyz


def test_expand_noop_without_all_handoff_two_conformers_or_templates():
    crest = _done_all_crest("cr", 3)
    opt = _ref_calc("opt", "opt", "cr")
    crest.config.crest_handoff = "lowest"
    assert expand_conformer_tracks(crest, crest.result, [crest, opt], set()) is None
    crest.config.crest_handoff = "all"
    assert expand_conformer_tracks(crest, _result_with_conformers(1),
                                   [crest, opt], set()) is None
    unrelated = Calculation(name="x", kind="opt", config=StepConfig(kind="opt"),
                            xyz="H 0 0 0")
    assert expand_conformer_tracks(crest, crest.result, [crest, unrelated], set()) is None


def test_expand_clones_only_pending_dependents_and_uniquifies_names():
    crest = _done_all_crest("cr", 2)
    done_dep = _ref_calc("old", "opt", "cr")
    done_dep.state = CalcState.DONE           # finished earlier: never cloned
    opt = _ref_calc("opt", "opt", "cr")
    calcs = [crest, done_dep, opt]

    removed, added = expand_conformer_tracks(
        crest, crest.result, calcs,
        taken_names={"cr", "old", "opt", "opt_c1"})

    assert removed == ["opt"]
    assert [c.name for c in added] == ["opt_c1_2", "opt_c2"]


def test_queue_engine_expands_all_handoff_and_runs_the_tracks(tmp_path, monkeypatch):
    # e2e on the fake runner (2-conformer ensemble): [crest(all), child(ref)]
    # becomes [crest, child_c1, child_c2], all DONE, the store notified once.
    _patch_runner(monkeypatch)
    cb, _logs, _ = _recording_callbacks()
    subs = []
    cb.queue_substitute = lambda removed, added: (
        subs.append((list(removed), list(added))) or True)
    engine = QueueEngine("", str(tmp_path), cb)
    parent = _crest_calc("par")
    parent.config.crest_handoff = "all"
    child = _crest_calc("child")
    child.geometry_source = GeometrySource.REFERENCE
    child.ref_name = "par"
    calcs = [parent, child]

    engine.run_all(calcs)

    assert [c.name for c in calcs] == ["par", "child_c1", "child_c2"]
    assert [c.state for c in calcs] == [CalcState.DONE] * 3
    assert len(subs) == 1 and subs[0][0] == ["child"]
    # the clones carry their conformer's coordinates as DIRECT geometry
    assert all(c.geometry_source == GeometrySource.DIRECT for c in calcs[1:])


def test_queue_engine_store_refusal_falls_back_to_single_geometry(tmp_path, monkeypatch):
    # if the store refuses the substitution (name collision), the template stays
    # and runs on the best conformer — never half a substitution.
    _patch_runner(monkeypatch)
    cb, logs, _ = _recording_callbacks()
    cb.queue_substitute = lambda removed, added: False
    engine = QueueEngine("", str(tmp_path), cb)
    parent = _crest_calc("par")
    parent.config.crest_handoff = "all"
    child = _crest_calc("child")
    child.geometry_source = GeometrySource.REFERENCE
    child.ref_name = "par"
    calcs = [parent, child]

    engine.run_all(calcs)

    assert [c.name for c in calcs] == ["par", "child"]
    assert child.state == CalcState.DONE
    assert any("could not expand" in m for _l, m in logs)


def test_run_start_expansion_for_already_done_crest(tmp_path, monkeypatch):
    # the search finished in a previous run; referencing templates added later
    # are expanded by the DONE-skip branch when the next run starts.
    _patch_runner(monkeypatch)
    cb, _logs, _ = _recording_callbacks()
    engine = QueueEngine("", str(tmp_path), cb)
    parent = _done_all_crest("par", 2)
    child = _crest_calc("child")
    child.geometry_source = GeometrySource.REFERENCE
    child.ref_name = "par"
    calcs = [parent, child]

    engine.run_all(calcs)

    assert [c.name for c in calcs] == ["par", "child_c1", "child_c2"]
    assert [c.state for c in calcs] == [CalcState.DONE] * 3
