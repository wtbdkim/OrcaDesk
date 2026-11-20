"""Opt-in smoke matrix against the REAL installed backends (P3 evidence).

One minimal, answer-known input per calc kind, run through the real
QueueEngine — the same code path the desktop Run button uses — so a pass
proves the whole per-kind pipeline: input generation → the backend accepts
it → detached launch + tail → parse → per-kind validation judges it DONE.
The assert is deliberately just "ended DONE" plus a couple of parsed facts:
`validate_result` already embeds the scientific pass bar per kind
(convergence, imaginary-mode counts, conformer count), so the job of this
matrix is to feed it systems whose correct outcome is known:

* H2O / H2CO   — sp, opt, freq (0 imaginary), opt_freq, tddft, nmr, general
* HCN ⇌ HNC    — the canonical 3-atom isomerization: ts_opt / ts_opt_freq /
                 ts_freq (exactly 1 imaginary), neb_ts (band + TS + FREQ),
                 irc (InitHess read, .hess staged from the TS calc's folder)
* ethanol      — crest_conf (≥1 conformer)

The chains double as reference-handoff coverage: opt → freq,
ts_opt_freq → ts_freq / irc, mlip_opt → sp, crest_conf → sp.

On top of the structural bar, the tests pin QUANTITATIVE chemical sanity with
generous literature windows — water's energy (≈ −76.47 Eh) and O–H length
(≈ 0.96 Å, DFT and MACE-OFF), its stretch frequencies (≈ 3850/3950 cm⁻¹),
the saddle's imaginary mode (≈ 1140i cm⁻¹), variational ordering
(E_opt < E_sp), and a cross-algorithm check that OptTS and NEB-TS land on
the same saddle (measured agreement: sub-μEh) — so a parse regression that
reads a plausible-looking but wrong number cannot pass.

Deliberately NOT covered: option combinatorics (solvation × RI × per-element
basis × raw …) — the unit suite pins those, and new option paths get targeted
real-ORCA validation when they change (P3). This matrix answers one question:
"does every kind still run end-to-end on a real install today".

Run with:

    ORCADESK_SMOKE=1 python -m pytest tests/smoke -v

Wall-clock: roughly 1.5 h for the ORCA section (IRC and NEB dominate; measured
on a 12-core desktop against ORCA 6.1.1), plus ~2 min for MLIP + CREST.
Without the env var the whole module skips, so the default `python -m pytest`
stays fast; each backend section also skips itself when its backend is absent
(no ORCA / no ready MLIP env / no CREST distro), mirroring the suite's
auto-skip convention.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from orcamgr.config import Settings
from orcamgr.core.input_generator import (
    preset_freq,
    preset_general,
    preset_irc,
    preset_neb_ts,
    preset_nmr,
    preset_opt,
    preset_sp,
    preset_tddft,
    preset_ts_freq,
    preset_ts_opt,
)
from orcamgr.core.queue import CalcState, QueueCallbacks, QueueEngine
from orcamgr.state.store import calc_from_dict

pytestmark = pytest.mark.skipif(
    os.environ.get("ORCADESK_SMOKE") != "1",
    reason="real-backend smoke matrix is opt-in: set ORCADESK_SMOKE=1",
)

# ---- minimal answer-known geometries (Angstrom) -----------------------------
H2O = """O 0.000000 0.000000 0.117300
H 0.000000 0.757200 -0.469200
H 0.000000 -0.757200 -0.469200"""

H2CO = """C 0.000000 0.000000 0.000000
O 0.000000 0.000000 1.205000
H 0.000000 0.943000 -0.587000
H 0.000000 -0.943000 -0.587000"""

# HCN ⇌ HNC: atom order (H, C, N) is identical in all three blocks — the
# NEB-TS pre-launch guard requires it, and this matrix is its live test.
HCN = """H 0.000000 0.000000 -1.064000
C 0.000000 0.000000 0.000000
N 0.000000 0.000000 1.156000"""

HNC = """H 0.000000 0.000000 2.161000
C 0.000000 0.000000 0.000000
N 0.000000 0.000000 1.169000"""

# near the known saddle: C–H ≈ 1.19, N–H ≈ 1.40, C–N ≈ 1.19
HCN_TS_GUESS = """H 1.136000 0.000000 0.368000
C 0.000000 0.000000 0.000000
N 0.000000 0.000000 1.187000"""

ETHANOL = """C 1.187900 -0.382900 0.000000
C 0.000000 0.552600 0.000000
O -1.186700 -0.247200 0.000000
H -1.923700 0.385000 0.000000
H 2.098500 0.230600 0.000000
H 1.118400 -1.009300 0.886900
H 1.118400 -1.009300 -0.886900
H -0.022700 1.181200 0.885200
H -0.022700 1.181200 -0.885200"""


# ---- harness ----------------------------------------------------------------
def _calc(name, preset=None, kind="", xyz="", ref="", charge=0, mult=1,
          nprocs=2, **cfg):
    """Build a Calculation through the shared client path (calc_from_dict →
    StepConfig.from_dict), exactly as the desktop or phone would."""
    base = preset().to_dict() if preset else {}
    base.update(cfg)
    if kind:
        base["kind"] = kind
    # tiny systems: keep launches light and MPI-portable (NEB overrides this —
    # it parallelizes over images, so more procs actually help there)
    base["nprocs"] = nprocs
    base["maxcore_mb"] = 2000
    return calc_from_dict({
        "name": name,
        "kind": base.get("kind", "sp"),
        "charge": charge,
        "multiplicity": mult,
        "geometry_source": "reference" if ref else "direct",
        "xyz": "" if ref else xyz,
        "ref_name": ref,
        "config": base,
    })


def _run(calcs, workspace, settings):
    logs: list[str] = []
    engine = QueueEngine(
        orca_path=settings.orca_path,
        workspace_root=str(workspace),
        callbacks=QueueCallbacks(
            log=lambda msg, level, _calc="": logs.append(f"[{level}] {msg}"),
            calc_update=lambda index, calc: None,
        ),
        mlip_envs=settings.mlip_envs,
        crest_distro=settings.crest_distro,
    )
    engine.run_all(calcs)
    return {"calcs": {c.name: c for c in calcs}, "logs": logs}


def _assert_done(matrix, name):
    calc = matrix["calcs"][name]
    tail = "\n".join(matrix["logs"][-30:])
    assert calc.state is CalcState.DONE, (
        f"{name} ended {calc.state.name}: {calc.message}\n"
        f"--- engine log tail ---\n{tail}"
    )
    return calc


def _bond_len(geometry, i, j):
    a, b = geometry[i], geometry[j]
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2) ** 0.5


@pytest.fixture(scope="session")
def settings():
    return Settings.load()


# ---- ORCA matrix ------------------------------------------------------------
@pytest.fixture(scope="session")
def orca_matrix(settings, tmp_path_factory):
    if not settings.orca_is_valid():
        pytest.skip("no valid ORCA executable configured (Settings.orca_path)")
    calcs = [
        _calc("smoke_sp", preset_sp, xyz=H2O),
        _calc("smoke_general", preset_general, xyz=H2O),
        _calc("smoke_opt", preset_opt, xyz=H2O),
        _calc("smoke_freq", preset_freq, ref="smoke_opt"),
        _calc("smoke_opt_freq", preset_opt, kind="opt_freq", xyz=H2O,
              calculation_type="TightOpt Freq", scf_convergence="VeryTightSCF"),
        _calc("smoke_ts_opt", preset_ts_opt, xyz=HCN_TS_GUESS),
        _calc("smoke_ts_opt_freq", preset_ts_opt, kind="ts_opt_freq",
              xyz=HCN_TS_GUESS,
              calculation_type="OptTS Freq", scf_convergence="VeryTightSCF"),
        _calc("smoke_ts_freq", preset_ts_freq, ref="smoke_ts_opt_freq"),
        _calc("smoke_irc", preset_irc, ref="smoke_ts_opt_freq",
              irc_init_hess="read", irc_hess_file="smoke_ts_opt_freq.hess"),
        _calc("smoke_tddft", preset_tddft, xyz=H2CO, tddft_nroots=5),
        _calc("smoke_nmr", preset_nmr, xyz=H2O),
        # the TS guess is REQUIRED here, not decoration: a straight HCN→HNC
        # interpolation sweeps H through the C–N axis (through the nuclei) and
        # the climbing image diverges; the off-axis guess anchors the path.
        # It also covers the ts_guess.xyz side-file write. The preset's
        # trailing FREQ is dropped: ORCA 6.1.1's parallel NUMFREQ after NEB-TS
        # loses the COSX Cholesky factors at 8 procs (a backend file race, not
        # an ORCAdesk path) and this saddle's 1-imaginary-mode verification
        # already runs twice (ts_opt_freq, ts_freq); the test instead asserts
        # the *_NEB-TS_converged.xyz artifact, written only on TS convergence.
        _calc("smoke_neb_ts", preset_neb_ts, xyz=HCN, neb_product_xyz=HNC,
              neb_ts_guess_xyz=HCN_TS_GUESS, options="",
              nprocs=min(8, os.cpu_count() or 2)),
    ]
    return _run(calcs, tmp_path_factory.mktemp("smoke_orca"), settings)


def test_sp(orca_matrix):
    calc = _assert_done(orca_matrix, "smoke_sp")
    # wB97X-D4/def2-TZVP water: ≈ −76.47 Eh. The window is generous (ORCA
    # version drift) but kills wrong-molecule / wrong-number parses outright.
    assert -76.6 < calc.result.final_energy_eh < -76.3


def test_general(orca_matrix):
    calc = _assert_done(orca_matrix, "smoke_general")
    assert -76.6 < calc.result.final_energy_eh < -76.3


def test_opt(orca_matrix):
    calc = _assert_done(orca_matrix, "smoke_opt")
    assert calc.result.opt_converged
    # variational sanity: the relaxed energy must lie below the unrelaxed sp
    sp = orca_matrix["calcs"]["smoke_sp"]
    assert calc.result.final_energy_eh < sp.result.final_energy_eh
    # O–H ≈ 0.96 Å in the optimized geometry (input order O, H, H); a
    # Bohr-vs-Å or geometry-parse regression lands far outside this window
    geom = calc.result.geometry
    assert 0.90 < _bond_len(geom, 0, 1) < 1.05
    assert 0.90 < _bond_len(geom, 0, 2) < 1.05


def test_freq_via_reference(orca_matrix):
    # freq on smoke_opt's optimized geometry: a true minimum → 0 imaginary
    calc = _assert_done(orca_matrix, "smoke_freq")
    assert calc.result.n_imaginary == 0


def test_opt_freq(orca_matrix):
    calc = _assert_done(orca_matrix, "smoke_opt_freq")
    assert calc.result.opt_converged
    assert calc.result.n_imaginary == 0
    # water's O–H stretches sit near 3850/3950 cm⁻¹ at this level
    assert calc.result.frequencies
    assert 3500 < max(calc.result.frequencies) < 4200


def test_ts_opt(orca_matrix):
    calc = _assert_done(orca_matrix, "smoke_ts_opt")
    assert calc.result.opt_converged


def test_ts_opt_freq(orca_matrix):
    # the HCN⇌HNC saddle: exactly one imaginary mode is the pass bar
    calc = _assert_done(orca_matrix, "smoke_ts_opt_freq")
    assert calc.result.opt_converged
    assert calc.result.n_imaginary == 1
    # the H-migration mode is ≈ 1140i cm⁻¹ (literature ~1100–1200i)
    assert calc.result.frequencies
    assert 700 < abs(min(calc.result.frequencies)) < 1600


def test_ts_freq_via_reference(orca_matrix):
    calc = _assert_done(orca_matrix, "smoke_ts_freq")
    assert calc.result.n_imaginary == 1


def test_irc_with_staged_hessian(orca_matrix):
    calc = _assert_done(orca_matrix, "smoke_irc")
    assert calc.result.terminated_normally
    assert calc.result.final_energy_eh is not None
    # the engine must have staged the referenced calc's .hess into the run folder
    run_dir = Path(calc.output_path).parent
    assert (run_dir / "smoke_ts_opt_freq.hess").exists()


def test_tddft(orca_matrix):
    calc = _assert_done(orca_matrix, "smoke_tddft")
    assert calc.result.final_energy_eh is not None


def test_nmr(orca_matrix):
    calc = _assert_done(orca_matrix, "smoke_nmr")
    assert calc.result.final_energy_eh is not None


def test_neb_ts(orca_matrix):
    calc = _assert_done(orca_matrix, "smoke_neb_ts")
    assert calc.result.terminated_normally
    assert calc.result.final_energy_eh is not None
    # ORCA writes this artifact only when the band located a converged TS
    run_dir = Path(calc.output_path).parent
    assert (run_dir / "smoke_neb_ts_NEB-TS_converged.xyz").exists()


def test_ts_energy_cross_check(orca_matrix):
    # two independent algorithms — eigenvector-following (OptTS) and the
    # climbing-image band (NEB-TS) — must land on the SAME saddle; measured
    # agreement is sub-μEh, so 0.1 mEh is a generous same-point criterion
    ts = _assert_done(orca_matrix, "smoke_ts_opt_freq")
    neb = _assert_done(orca_matrix, "smoke_neb_ts")
    assert abs(ts.result.final_energy_eh - neb.result.final_energy_eh) < 1e-4


# ---- MLIP matrix ------------------------------------------------------------
@pytest.fixture(scope="session")
def mlip_matrix(settings, tmp_path_factory):
    envs = settings.mlip_envs or []
    if not envs:
        pytest.skip("no MLIP environment registered in settings")
    from orcamgr.mlip.env import probe_env
    probe = probe_env(envs[0].get("python", ""))
    if not probe.get("ready"):
        pytest.skip(f"MLIP env not ready: "
                    f"{probe.get('error') or probe.get('common_missing')}")
    calcs = [
        _calc("smoke_mlip_opt", kind="mlip_opt", xyz=H2O,
              mlip_model="MACE-OFF small"),
        _calc("smoke_mlip_sp", kind="mlip_sp", xyz=H2O,
              mlip_model="MACE-OFF small"),
        # relax + vibrational analysis + ideal-gas thermochemistry: a true
        # minimum (water) must come back with zero imaginary modes and a Gibbs G
        _calc("smoke_mlip_opt_freq", kind="mlip_opt_freq", xyz=H2O,
              mlip_model="MACE-OFF small"),
    ]
    if settings.orca_is_valid():
        # MLIP → ORCA geometry handoff, live
        calcs.append(_calc("smoke_mlip_ref_sp", preset_sp, ref="smoke_mlip_opt"))
    return _run(calcs, tmp_path_factory.mktemp("smoke_mlip"), settings)


def test_mlip_opt(mlip_matrix):
    calc = _assert_done(mlip_matrix, "smoke_mlip_opt")
    assert calc.result.opt_converged
    assert calc.result.final_energy_eh is not None
    # MACE-OFF must also relax water to O–H ≈ 0.96 Å (input order O, H, H)
    geom = calc.result.geometry
    assert 0.90 < _bond_len(geom, 0, 1) < 1.05
    assert 0.90 < _bond_len(geom, 0, 2) < 1.05


def test_mlip_sp(mlip_matrix):
    calc = _assert_done(mlip_matrix, "smoke_mlip_sp")
    assert calc.result.final_energy_eh is not None


def test_mlip_opt_freq(mlip_matrix):
    # DONE already asserts (via validate_result) that the relaxation converged
    # AND the minimum has zero imaginary modes; check the frequency block +
    # thermochemistry actually landed, and that water's stretches are sane.
    calc = _assert_done(mlip_matrix, "smoke_mlip_opt_freq")
    r = calc.result
    assert r.has_frequencies and r.n_imaginary == 0
    assert len(r.frequencies) == 3            # 3N-6 for a nonlinear triatomic
    assert max(r.frequencies) > 3000          # O–H stretch, cm^-1
    assert r.zpe_eh is not None and r.gibbs_eh is not None


def test_mlip_to_orca_handoff(mlip_matrix):
    if "smoke_mlip_ref_sp" not in mlip_matrix["calcs"]:
        pytest.skip("no valid ORCA executable for the handoff leg")
    calc = _assert_done(mlip_matrix, "smoke_mlip_ref_sp")
    assert calc.result.final_energy_eh is not None


# ---- CREST matrix -----------------------------------------------------------
@pytest.fixture(scope="session")
def crest_matrix(settings, tmp_path_factory):
    from orcamgr.crest.env import aggregate_status, probe_all
    status = aggregate_status(probe_all())
    if status.get("state") != "ready":
        pytest.skip(f"CREST not ready in any WSL distro "
                    f"(state={status.get('state')})")
    calcs = [
        _calc("smoke_crest_conf", kind="crest_conf", xyz=ETHANOL,
              crest_method="gfn2", crest_preset="quick", crest_threads=4),
    ]
    if settings.orca_is_valid():
        # conformer → ORCA handoff: sp on the best conformer
        calcs.append(_calc("smoke_crest_ref_sp", preset_sp,
                           ref="smoke_crest_conf"))
    return _run(calcs, tmp_path_factory.mktemp("smoke_crest"), settings)


def test_crest_conf(crest_matrix):
    calc = _assert_done(crest_matrix, "smoke_crest_conf")
    assert calc.result.is_conformer_search
    assert len(calc.result.conformers) >= 1
    # rank-sorted contract: the first conformer IS the best one
    best = calc.result.conformers[0]
    assert best.rel_kcal == 0.0
    assert calc.result.final_energy_eh == best.energy_eh


def test_crest_to_orca_handoff(crest_matrix):
    if "smoke_crest_ref_sp" not in crest_matrix["calcs"]:
        pytest.skip("no valid ORCA executable for the handoff leg")
    calc = _assert_done(crest_matrix, "smoke_crest_ref_sp")
    assert calc.result.final_energy_eh is not None
