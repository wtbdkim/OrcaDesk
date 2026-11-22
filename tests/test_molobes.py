"""The 2D drawing's orbital overlay: per-atom lobes of one MO
(orcamgr/nbo/molobes.py) and the bridge slot that serves them.

The lobe math is checked three ways: on constructed wavefunctions whose
answer is known by hand (shares, sign flips, the p axis); on real ORCA 6.1.1
Molden files where chemistry fixes the answer (water's HOMO is the
out-of-plane O p; N2's and formamide's π orbitals are p perpendicular to the
molecule, same sign when bonding, alternating when antibonding); and against
the orbital's own value on a grid (orcamgr/nbo/grid.py, itself pinned against
orca_plot's cubes) -- the orbital must be positive on the +p side of an atom.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from orcamgr.core.parser import parse_file
from orcamgr.nbo.grid import orbital_values
from orcamgr.nbo.molobes import P_AXIS, MoLobeSet
from orcamgr.nbo.wavefunction import (BOHR_PER_ANGSTROM, Shell, Wavefunction,
                                      WavefunctionError, _spherical_m, load_molden)

FIXTURES = pathlib.Path(__file__).parent / "nbo" / "fixtures"


def _set(stem: str) -> MoLobeSet:
    return MoLobeSet.from_wavefunction(load_molden(FIXTURES / f"{stem}.molden.input"))


def _toy(c: np.ndarray, shells: list[tuple[int, int]], n_atoms: int,
         occ: list[float] | None = None) -> MoLobeSet:
    """A Wavefunction built by hand: `shells` = [(atom, l)], C as given (its
    S is whatever C^T S C = I implies)."""
    bf_atom, bf_l, bf_m, bf_shell = [], [], [], []
    shell_objs = []
    for k, (atom, l) in enumerate(shells):
        shell_objs.append(Shell(atom=atom, l=l, exponents=(1.0,), coefficients=(1.0,)))
        for m in _spherical_m(l):
            bf_atom.append(atom); bf_l.append(l); bf_m.append(m); bf_shell.append(k)
    n = c.shape[1]
    wf = Wavefunction(
        elements=["C"] * n_atoms, atomic_numbers=np.full(n_atoms, 6),
        nuclear_charges=np.full(n_atoms, 6.0),
        coordinates=np.arange(n_atoms * 3, dtype=float).reshape(n_atoms, 3),
        shells=shell_objs, bf_atom=np.array(bf_atom), bf_l=np.array(bf_l),
        bf_m=np.array(bf_m), bf_shell=np.array(bf_shell),
        coefficients={"alpha": c},
        energies={"alpha": np.linspace(-1.0, 1.0, n)},
        occupations={"alpha": np.array(occ if occ is not None else [2.0] + [0.0] * (n - 1))},
        spherical=True)
    return MoLobeSet.from_wavefunction(wf)


# ---------------------------------------------------------------- constructed


def test_p_order_is_px_py_pz_as_the_grid_draws_it():
    """Molden writes p as px, py, pz; the reader stores m = +1, -1, 0; the
    evaluator pinned against orca_plot maps +1 -> x, -1 -> y, 0 -> z."""
    assert _spherical_m(1) == [1, -1, 0]
    assert P_AXIS == {1: 0, -1: 1, 0: 2}


def test_h2_like_shares_and_signs_with_a_real_overlap():
    """Two s functions overlapping by 0.4: bonding has the same sign on both,
    antibonding opposite, each atom half the orbital, and S comes back."""
    s = 0.4
    c = np.array([[1, 1], [1, -1]], float)
    c[:, 0] /= np.sqrt(2 + 2 * s)
    c[:, 1] /= np.sqrt(2 - 2 * s)
    ls = _toy(c, [(0, 0), (1, 0)], 2)
    assert ls.wavefunction.overlap() == pytest.approx(np.array([[1, s], [s, 1]]))
    bond, anti = ls.lobes("alpha", 0), ls.lobes("alpha", 1)
    for r in (bond, anti):
        assert r["norm"] == pytest.approx(1.0)
        assert [a["share"] for a in r["atoms"]] == pytest.approx([0.5, 0.5])
        assert all(a["p"] == [0, 0, 0] and a["p_share"] == 0 for a in r["atoms"])
    assert np.sign(bond["atoms"][0]["s"]) == np.sign(bond["atoms"][1]["s"])
    assert np.sign(anti["atoms"][0]["s"]) == -np.sign(anti["atoms"][1]["s"])
    assert abs(bond["atoms"][0]["s"]) == pytest.approx(np.sqrt(0.5))


def test_a_p_only_orbital_points_along_its_coefficient_axis_and_flips_with_it():
    """One atom, s + p (px, py, pz) with an orthogonal C (S = I): an MO that
    is pure py has p along +y, all of its share p; negating it flips p."""
    rng = np.random.default_rng(7)
    c = np.eye(4)
    c[:, 1] = [0, 0, 1, 0]          # pure py (Molden order s, px, py, pz)
    c[:, 2] = [0, 1, 0, 0]          # pure px
    c[:, 3] = [0, 0, 0, -1]         # pure -pz
    c[:, 0] = [1, 0, 0, 0]
    ls = _toy(c, [(0, 0), (0, 1)], 1, occ=[2, 2, 2, 0])
    y = ls.lobes("alpha", 1)["atoms"][0]
    assert y["share"] == pytest.approx(1) and y["p_share"] == pytest.approx(1)
    assert y["p"] == pytest.approx([0, 1, 0])
    assert ls.lobes("alpha", 2)["atoms"][0]["p"] == pytest.approx([1, 0, 0])
    assert ls.lobes("alpha", 3)["atoms"][0]["p"] == pytest.approx([0, 0, -1])
    # a general direction, and its negative
    v = rng.normal(size=3); v /= np.linalg.norm(v)
    c2 = np.eye(4); c2[1:, 1] = v
    q, _ = np.linalg.qr(c2)
    q[:, 1] *= np.sign(q[1:, 1] @ v)
    ls2 = _toy(q, [(0, 0), (0, 1)], 1)
    p = np.array(ls2.lobes("alpha", 1)["atoms"][0]["p"])
    assert p == pytest.approx(v, abs=1e-6)
    q[:, 1] *= -1
    assert np.array(_toy(q, [(0, 0), (0, 1)], 1).lobes("alpha", 1)["atoms"][0]["p"]) \
        == pytest.approx(-v, abs=1e-6)


def test_two_p_shells_of_opposite_sign_still_give_the_dominant_direction():
    """Split-valence p: inner and outer shells can carry opposite signs. The
    axis is their principal one, pointed the way the larger shell points --
    a plain sum would let them cancel."""
    c = np.eye(6)
    col = np.zeros(6)
    col[[0, 3]] = [0.8, -0.6]       # px of shell 1, px of shell 2 (opposite)
    c[:, 0] = col
    c[:, 3] = [0.6, 0, 0, 0.8, 0, 0]
    ls = _toy(c, [(0, 1), (0, 1)], 1)
    a = ls.lobes("alpha", 0)["atoms"][0]
    assert a["p"] == pytest.approx([1, 0, 0])
    assert a["p_share"] == pytest.approx(1)


def test_d_share_is_folded_into_the_share_not_the_p_vector():
    c = np.eye(9)                   # s, p(3), d(5) on one atom
    c[:, 0] = 0
    c[[1, 4], 0] = [0.6, 0.8]       # px + one d component
    c[:, 1] = 0
    c[[1, 4], 1] = [0.8, -0.6]
    c[:, 4] = 0
    c[0, 4] = 1.0                   # the s function, so C stays square and whole
    ls = _toy(c, [(0, 0), (0, 1), (0, 2)], 1)
    a = ls.lobes("alpha", 0)["atoms"][0]
    assert a["share"] == pytest.approx(1)
    assert a["d_share"] == pytest.approx(0.64)
    assert a["p_share"] == pytest.approx(0.36)
    assert a["p"] == pytest.approx([0.6, 0, 0])


def test_unknown_orbital_or_spin_is_an_error_sentence():
    ls = _set("h2o")
    with pytest.raises(WavefunctionError, match="no orbital"):
        ls.lobes("alpha", 10_000)
    with pytest.raises(WavefunctionError, match="unknown spin"):
        ls.lobes("gamma", 0)
    # a restricted run's "beta" is its one manifold
    assert ls.lobes("b", 4)["atoms"] == ls.lobes("alpha", 4)["atoms"]


# ---------------------------------------------------------------- real files


def _normal(xyz: np.ndarray) -> np.ndarray:
    """The unit normal of a planar molecule (smallest principal axis)."""
    d = xyz - xyz.mean(axis=0)
    _w, v = np.linalg.eigh(d.T @ d)
    return v[:, 0]


@pytest.mark.parametrize("stem", ["h2o", "h2o_rot_g"])
def test_water_homo_is_the_out_of_plane_oxygen_p(stem):
    ls = _set(stem)
    homo = [o for o in ls.orbital_list() if o["frontier"] == "homo"][0]
    r = ls.lobes("alpha", homo["index"])
    assert r["norm"] == pytest.approx(1, abs=1e-6)
    o = r["atoms"][0]
    assert o["share"] > 0.9 and o["p_share"] > 0.9
    p = np.array(o["p"]) / np.linalg.norm(o["p"])
    n = _normal(ls.wavefunction.coordinates)
    assert abs(p @ n) > 0.99


def test_n2_pi_orbitals_are_perpendicular_and_bonding():
    ls = _set("n2")
    axis = np.array([0, 0, 1.0])
    for idx in (4, 5):              # the degenerate πu pair below the σ HOMO
        r = ls.lobes("alpha", idx)
        a, b = (np.array(x["p"]) for x in r["atoms"])
        assert [x["share"] for x in r["atoms"]] == pytest.approx([0.5, 0.5], abs=1e-3)
        assert abs(a @ axis) < 1e-3 and abs(b @ axis) < 1e-3
        assert a @ b > 0            # bonding: the same phase on both atoms
    lumo = ls.lobes("alpha", 7)     # πg*: opposite phases
    a, b = (np.array(x["p"]) for x in lumo["atoms"])
    assert a @ b < 0


def test_formamide_pi_system_is_the_textbook_allyl_like_three():
    """Formamide's three π orbitals over O=C-N (4 electrons, allyl-anion
    like): π1 (HOMO−3) one phase over C, O and N; π2 (HOMO−1) O against N
    with little on C; π3* (LUMO) C against O and N. Every p perpendicular to
    the molecular plane. (The N inner p shell is written all-negative by ORCA:
    without the phase correction π2 and π3* come out wrong.)"""
    ls = _set("formamide")
    heavy = [0, 1, 2]               # C, O, N
    n = _normal(ls.wavefunction.coordinates)
    orbs = {o["label"]: o["index"] for o in ls.orbital_list()}
    pi = ls.lobes("alpha", orbs["HOMO−3"])
    pi2 = ls.lobes("alpha", orbs["HOMO−1"])
    star = ls.lobes("alpha", orbs["LUMO"])
    for r in (pi, pi2, star):
        for i in heavy:
            a = r["atoms"][i]
            if a["share"] < 0.02:
                continue
            p = np.array(a["p"])
            assert abs(p @ n) / np.linalg.norm(p) > 0.99
    signs = lambda r: [np.sign(np.array(r["atoms"][i]["p"]) @ n) for i in heavy]
    assert len(set(signs(pi))) == 1                     # π1: one phase over C, O, N
    s2 = signs(pi2)
    assert s2[1] != s2[2] and pi2["atoms"][0]["share"] < 0.1   # π2: O against N
    s = signs(star)
    assert s[0] != s[1] and s[0] != s[2] and s[1] == s[2]      # π3*: C against O, N


@pytest.mark.parametrize("stem,label", [("h2o", "HOMO"), ("formamide", "HOMO−3"),
                                        ("formamide", "HOMO−1"), ("formamide", "LUMO"),
                                        ("h2o_rot_g", "HOMO"), ("n2", "HOMO−1")])
def test_the_positive_lobe_is_where_the_orbital_is_positive(stem, label):
    """Evaluate the orbital itself just off each p-dominated atom, along +p
    and along -p: positive on the + side, negative on the other."""
    ls = _set(stem)
    wf = ls.wavefunction
    index = {o["label"]: o["index"] for o in ls.orbital_list()}[label]
    r = ls.lobes("alpha", index)
    checked = 0
    for i, a in enumerate(r["atoms"]):
        if a["share"] < 0.1 or a["p_share"] < 2 * a["s_share"]:
            continue
        u = np.array(a["p"]) / np.linalg.norm(a["p"])
        pts = wf.coordinates[i] + np.outer([0.5, -0.5], u)       # Bohr
        plus, minus = orbital_values(wf, wf.coefficients["alpha"][:, index], pts)
        assert plus > 0 > minus, (i, plus, minus)
        checked += 1
    assert checked


@pytest.mark.parametrize("stem", ["h2o", "h2o_rot_g", "formamide", "n2", "h2o_tzvp"])
def test_the_s_circle_has_the_orbital_s_valence_sign(stem):
    """An s-dominated atom's circle takes the sign of the orbital in its
    valence region: the spherical average 0.6 Bohr out, past a 2s-like node
    (at the nucleus a valence σ can have the opposite, core-like sign -- the
    textbook picture is of the outer lobe). Every occupied orbital."""
    ls = _set(stem)
    wf = ls.wavefunction
    rng = np.random.default_rng(1)
    d = rng.normal(size=(400, 3))
    d /= np.linalg.norm(d, axis=1)[:, None]
    checked = 0
    for o in ls.orbital_list():
        if o["spin"] not in ("", "a") or o["occ"] < 1:
            continue
        r = ls.lobes("alpha", o["index"])
        for i, a in enumerate(r["atoms"]):
            if a["share"] < 0.1 or a["s_share"] <= a["p_share"] + a["d_share"]:
                continue
            v = orbital_values(wf, wf.coefficients["alpha"][:, o["index"]],
                               wf.coordinates[i] + 0.6 * d).mean()
            assert np.sign(v) == np.sign(a["s"]), (o["label"], i, a["s"], v)
            checked += 1
    assert checked


def test_the_orbital_list_names_the_frontier_as_the_parser_does():
    for stem in ("h2o", "h2o_cation"):
        ls = _set(stem)
        rows = ls.orbital_list()
        res = parse_file(str(FIXTURES / f"{stem}.out"))
        homo = [o for o in rows if o["frontier"] == "homo"][0]
        assert homo["index"] == res.homo_index
        assert homo["spin"] == (res.homo_spin or "")
        assert homo["ev"] == pytest.approx(res.homo_ev, abs=2e-3)
        lumo = [o for o in rows if o["frontier"] == "lumo"][0]
        assert lumo["index"] == res.lumo_index
    plain = {o["label"] for o in _set("h2o").orbital_list()}
    assert {"HOMO", "HOMO−1", "LUMO", "LUMO+1"} <= plain
    open_shell = _set("h2o_cation").orbital_list()
    assert {o["spin"] for o in open_shell} == {"a", "b"}
    assert all(o["label"][:2] in ("α ", "β ") for o in open_shell)


def test_coordinates_come_back_in_angstrom():
    ls = _set("n2")
    assert ls.elements() == ["N", "N"]
    assert ls.coordinates_angstrom()[1][2] == pytest.approx(2.0749192950 / BOHR_PER_ANGSTROM,
                                                            abs=1e-5)


# ---------------------------------------------------------------- the slot


def _stub(run_dir: pathlib.Path, base: str):
    from orcamgr.gui.bridge import Bridge
    logs: list = []
    stub = SimpleNamespace(
        settings=SimpleNamespace(orca_path=str(run_dir / "no-orca")),
        store=SimpleNamespace(append_log=lambda m, lvl="info": logs.append((lvl, m))),
        _lobe_lock=threading.Lock(),
        _lobe={"key": None, "mtime": 0.0, "state": "idle", "error": "", "set": None},
        _nbo_lock=threading.Lock(), _nbo_sets={},
        _plot_source=lambda src: (run_dir, base, False, ""))
    call = lambda *a: json.loads(Bridge.get_mo_lobes(stub, *a))
    return stub, call, logs


def _until_done(call, *args):
    deadline = time.time() + 30
    r = call(*args)
    while r.get("state") == "running" and time.time() < deadline:
        time.sleep(0.05)
        r = call(*args)
    return r


def test_slot_reads_the_cached_molden_and_answers_each_orbital(tmp_path):
    """A Molden file newer than the .gbw is reused -- the configured ORCA here
    does not exist, so a conversion attempt would fail the test."""
    pytest.importorskip("PyQt6.QtCore")
    (tmp_path / "w.gbw").write_bytes(b"x")
    molden = tmp_path / "w.molden.input"
    shutil.copy(FIXTURES / "h2o.molden.input", molden)
    later = (tmp_path / "w.gbw").stat().st_mtime + 5
    os.utime(molden, (later, later))
    _stub_obj, call, logs = _stub(tmp_path, "w")
    first = call("calc:w", "alpha", 4)
    assert first["ok"] and first["state"] in ("running", "done")
    r = _until_done(call, "calc:w", "alpha", 4)
    assert r["ok"] and r["state"] == "done", r
    assert r["spin"] == "alpha" and r["index"] == 4
    assert len(r["atoms"]) == 3 and r["elements"] == ["O", "H", "H"]
    assert sum(a["share"] for a in r["atoms"]) == pytest.approx(1, abs=1e-4)
    listed = call("calc:w", "", -1)
    assert listed["state"] == "done" and "atoms" not in listed
    assert any(o["label"] == "HOMO" and o["index"] == 4 for o in listed["orbitals"])
    bad = call("calc:w", "alpha", 999)
    assert bad["ok"] is False and "no orbital" in bad["error"]
    assert not logs


def test_slot_errors_are_data(tmp_path):
    pytest.importorskip("PyQt6.QtCore")
    _stub_obj, call, _logs = _stub(tmp_path, "gone")
    r = call("calc:gone", "alpha", 0)
    assert r == {"ok": False, "state": "error",
                 "error": r["error"]} and ".gbw" in r["error"]
    # a .gbw with no Molden beside it and no orca_2mkl: the conversion's sentence
    (tmp_path / "gone.gbw").write_bytes(b"x")
    r = _until_done(call, "calc:gone", "alpha", 0)
    assert r["ok"] is False and "orca_2mkl" in r["error"]
