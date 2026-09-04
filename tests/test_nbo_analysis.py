"""Unit tests for the analysis pipeline (orcamgr.nbo.source / .analysis).

These are the two layers between "a calculation finished" and "the Results tab
has something to draw": convert the ``.gbw`` ORCA left behind, run the analysis,
and keep the answer. Neither needs ORCA to test — the conversion is a subprocess
call that is replaced here, and every fixture already has the Molden file that
call would have produced.

What the tests are really guarding is staleness. A cache served for the wrong
wavefunction is worse than no cache: the numbers look right and belong to the
previous run. Both the Molden file and the JSON are therefore tied to the
``.gbw``'s modification time, and a re-run has to invalidate them.
"""

import json

import pytest

from orcamgr.nbo import source
from orcamgr.nbo.analysis import (
    CACHE_FORMAT, NboAnalysis, analysis_for, analyze, cache_path, read_cache,
    write_cache,
)
from orcamgr.nbo.source import molden_path, wavefunction_for, write_molden
from orcamgr.nbo.wavefunction import WavefunctionError, load_molden

from test_nbo_wavefunction import FIXTURES


def _run_dir(tmp_path, stem: str = "h2o", base: str = "water"):
    """A run folder holding a `.gbw` and the Molden file a conversion would make."""
    (tmp_path / f"{base}.gbw").write_bytes(b"binary wavefunction")
    (tmp_path / f"{base}.molden.input").write_text(
        (FIXTURES / f"{stem}.molden.input").read_text(encoding="utf-8"),
        encoding="utf-8")
    return tmp_path


@pytest.fixture
def no_conversion(monkeypatch):
    """Fail loudly if anything tries to actually run orca_2mkl."""
    def refuse(*_a, **_k):
        raise AssertionError("orca_2mkl should not have been run")
    monkeypatch.setattr(source.subprocess, "run", refuse)
    return refuse


# --------------------------------------------------------------------------
# converting the .gbw
# --------------------------------------------------------------------------

def test_molden_path_is_what_orca_2mkl_writes(tmp_path):
    assert molden_path(tmp_path, "water").name == "water.molden.input"


def test_a_current_molden_file_is_reused(tmp_path, no_conversion):
    run = _run_dir(tmp_path)
    # The Molden file was written after the .gbw, so it describes it.
    assert write_molden("orca.exe", run, "water") == molden_path(run, "water")


def test_a_molden_older_than_its_gbw_is_regenerated(tmp_path, monkeypatch):
    run = _run_dir(tmp_path)
    import os
    molden = molden_path(run, "water")
    gbw = run / "water.gbw"
    os.utime(molden, (1_000_000, 1_000_000))       # older than the .gbw
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        molden.touch()                             # as the real tool would
        return type("P", (), {"stdout": "", "returncode": 0})()

    monkeypatch.setattr(source, "orca_tool", lambda *_a: tmp_path / "orca_2mkl.exe")
    monkeypatch.setattr(source.subprocess, "run", fake_run)
    write_molden("orca.exe", run, "water")
    assert calls, "a stale Molden file must be regenerated, not served"
    assert gbw.stat().st_mtime <= molden.stat().st_mtime


def test_force_regenerates_even_a_current_file(tmp_path, monkeypatch):
    run = _run_dir(tmp_path)
    calls = []
    monkeypatch.setattr(source, "orca_tool", lambda *_a: tmp_path / "orca_2mkl.exe")
    monkeypatch.setattr(source.subprocess, "run",
                        lambda argv, **k: (calls.append(argv),
                                           type("P", (), {"stdout": ""})())[1])
    write_molden("orca.exe", run, "water", force=True)
    assert calls


def test_a_calculation_with_no_wavefunction_says_so(tmp_path, no_conversion):
    with pytest.raises(WavefunctionError, match="no wavefunction file"):
        write_molden("orca.exe", tmp_path, "water")


def test_a_missing_orca_2mkl_points_at_the_orca_path(tmp_path, monkeypatch):
    run = _run_dir(tmp_path)
    (run / "water.molden.input").unlink()
    monkeypatch.setattr(source, "orca_tool", lambda *_a: None)
    with pytest.raises(WavefunctionError, match="ORCA path in Settings"):
        write_molden("orca.exe", run, "water")


def test_a_conversion_that_produced_nothing_reports_the_tool_s_reason(
        tmp_path, monkeypatch):
    run = _run_dir(tmp_path)
    (run / "water.molden.input").unlink()
    monkeypatch.setattr(source, "orca_tool", lambda *_a: tmp_path / "orca_2mkl.exe")
    monkeypatch.setattr(
        source.subprocess, "run",
        lambda argv, **k: type("P", (), {"stdout": "Error: wrong version\n"})())
    # orca_2mkl exits 0 even when it refuses a file, so the missing output is
    # the signal and its last line is the explanation.
    with pytest.raises(WavefunctionError, match="wrong version"):
        write_molden("orca.exe", run, "water")


def test_a_conversion_timeout_is_an_error_not_a_hang(tmp_path, monkeypatch):
    run = _run_dir(tmp_path)
    (run / "water.molden.input").unlink()
    monkeypatch.setattr(source, "orca_tool", lambda *_a: tmp_path / "orca_2mkl.exe")

    def timeout(*_a, **_k):
        raise source.subprocess.TimeoutExpired("orca_2mkl", 300)
    monkeypatch.setattr(source.subprocess, "run", timeout)
    with pytest.raises(WavefunctionError, match="did not finish"):
        write_molden("orca.exe", run, "water")


def test_wavefunction_for_reads_the_converted_file(tmp_path, no_conversion):
    run = _run_dir(tmp_path)
    wf = wavefunction_for("orca.exe", run, "water")
    assert wf.n_atoms == 3 and wf.n_basis == 24


# --------------------------------------------------------------------------
# the analysis object
# --------------------------------------------------------------------------

def test_analyze_assembles_the_whole_picture():
    wf = load_molden(FIXTURES / "h2o.molden.input")
    a = analyze(wf, base="water")
    assert a.base == "water"
    assert (a.n_atoms, a.n_basis) == (3, 24)
    assert a.n_electrons == pytest.approx(10.0)
    assert a.restricted and not a.has_ecp
    assert len(a.atoms) == 3
    assert len(a.bonds) == 2                       # the two O-H
    # only the minimal basis is listed: O 1s/2s/2p and one 1s per hydrogen
    assert len(a.orbitals) == 7
    assert {row.label for row in a.orbitals} == {"O 1s", "O 2s", "O 2p", "H 1s"}
    assert a.diagnostics["electron_count_error"] < 1e-8
    assert a.diagnostics["minimal_fraction"] > 0.99


def test_the_analysis_survives_a_round_trip_through_json():
    wf = load_molden(FIXTURES / "hi.molden.input")
    original = analyze(wf, base="hi")
    restored = NboAnalysis.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored.to_dict() == original.to_dict()
    assert restored.atoms[0].configuration == original.atoms[0].configuration
    assert restored.bonds == original.bonds


# --------------------------------------------------------------------------
# the cache, and every way it must refuse to be used
# --------------------------------------------------------------------------

def test_a_cache_is_served_only_for_the_wavefunction_it_came_from(tmp_path):
    wf = load_molden(FIXTURES / "h2o.molden.input")
    analysis = analyze(wf, base="water", source_mtime=1234.0)
    assert write_cache(tmp_path, "water", analysis)

    assert read_cache(tmp_path, "water", 1234.0) is not None
    # A re-run rewrites the .gbw with a new mtime; the old analysis describes a
    # wavefunction that no longer exists.
    assert read_cache(tmp_path, "water", 1235.0) is None


def test_a_cache_from_another_format_is_recomputed(tmp_path):
    wf = load_molden(FIXTURES / "h2o.molden.input")
    write_cache(tmp_path, "water", analyze(wf, base="water", source_mtime=1.0))
    data = json.loads(cache_path(tmp_path, "water").read_text(encoding="utf-8"))
    data["format"] = CACHE_FORMAT + 1
    cache_path(tmp_path, "water").write_text(json.dumps(data), encoding="utf-8")
    assert read_cache(tmp_path, "water", 1.0) is None


@pytest.mark.parametrize("content", ["", "{", "null", "[]", '{"format": 1}'])
def test_an_unreadable_cache_is_a_reason_to_recompute_not_to_fail(tmp_path, content):
    cache_path(tmp_path, "water").write_text(content, encoding="utf-8")
    assert read_cache(tmp_path, "water", 1.0) is None


def test_a_missing_cache_is_not_an_error(tmp_path):
    assert read_cache(tmp_path, "nothing_here", 1.0) is None


def test_writing_a_cache_nowhere_is_survivable(tmp_path):
    wf = load_molden(FIXTURES / "h2o.molden.input")
    assert write_cache(tmp_path / "no" / "such" / "dir", "water",
                       analyze(wf, base="water")) is False


def test_analysis_for_computes_once_and_then_serves_the_cache(
        tmp_path, no_conversion):
    run = _run_dir(tmp_path)
    first = analysis_for("orca.exe", run, "water")
    assert cache_path(run, "water").is_file()
    second = analysis_for("orca.exe", run, "water")
    assert second.to_dict() == first.to_dict()
    assert second.source_mtime == (run / "water.gbw").stat().st_mtime


def test_use_cache_false_ignores_a_stored_answer(tmp_path, no_conversion):
    run = _run_dir(tmp_path)
    analysis_for("orca.exe", run, "water")
    cache_path(run, "water").write_text('{"format": 1, "base": "poisoned"}',
                                        encoding="utf-8")
    assert analysis_for("orca.exe", run, "water", use_cache=False).base == "water"
