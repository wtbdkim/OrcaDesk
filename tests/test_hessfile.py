"""Reading an ORCA .hess for its modes, and pushing coordinates along one.

The fixture is a real ORCA 6.1.1 water frequency run's .hess: 3 atoms, 9 modes
(6 rigid + 3 vibrations), whose $normal_modes table spans two column blocks -
so the block parsing is exercised, not just the first five columns.

The contracts pinned here:

* the wavenumbers and vectors read back are ORCA's own, rigid modes zero;
* the push is a distance a reader can picture: the most-moved atom goes
  exactly the amplitude asked for, and + then - comes back to the start;
* a mode is a list of per-atom arrows, so coordinates holding other atoms or
  another order are refused with a sentence rather than pushed (P26);
* only the moved lines of the user's text change (the same rewrite the
  constraint Apply uses).
"""

import math
from pathlib import Path

import pytest

from orcamgr.core.hessfile import HessError, read_hess
from orcamgr.core.structure import displace_along_mode, parse_block

H2O = Path(__file__).parent / "hess" / "fixtures" / "h2o.hess"


def _shift(a, b):
    return max(math.dist((p.x, p.y, p.z), (q.x, q.y, q.z)) for p, q in zip(a, b))


def test_reads_the_geometry_wavenumbers_and_modes():
    h = read_hess(H2O)
    assert h.symbols == ["O", "H", "H"]
    assert len(h.freqs) == 9 and len(h.modes) == 9
    assert h.vibrations() == [6, 7, 8]
    assert [round(h.freqs[k], 2) for k in h.vibrations()] == [1606.44, 3822.87, 3905.68]
    for k in range(6):                       # translations and rotations
        assert all(v == 0.0 for v in h.modes[k])
    for k in h.vibrations():                 # ORCA writes them normalized
        assert math.sqrt(sum(v * v for v in h.modes[k])) == pytest.approx(1.0, abs=1e-6)
    # the second column block (modes 5-8) was read, not left at zero
    assert any(v != 0.0 for v in h.modes[8])


def test_a_file_without_modes_is_a_sentence(tmp_path):
    f = tmp_path / "x.hess"
    f.write_text("$orca_hessian_file\n$hessian\n3\n$end\n", encoding="utf-8")
    with pytest.raises(HessError, match="not the .hess of a finished frequency"):
        read_hess(f)


def test_an_empty_box_gets_the_hessians_geometry_pushed():
    h = read_hess(H2O)
    r = displace_along_mode("", h.symbols, h.coords, h.modes[6], 0.2)
    assert r["ok"], r["error"]
    pushed = parse_block(r["xyz"])
    start = parse_block("\n".join(f"{s} {x} {y} {z}" for s, (x, y, z) in zip(h.symbols, h.coords)))
    assert [a.sym for a in pushed] == ["O", "H", "H"]
    assert _shift(pushed, start) == pytest.approx(0.2, abs=1e-6)
    assert r["max_shift"] == pytest.approx(0.2)
    assert r["atom"].startswith("H")          # in a bend the hydrogens move most


def test_plus_then_minus_comes_back():
    h = read_hess(H2O)
    there = displace_along_mode("", h.symbols, h.coords, h.modes[7], 0.35)["xyz"]
    back = displace_along_mode(there, h.symbols, h.coords, h.modes[7], -0.35)["xyz"]
    start = "\n".join(f"{s} {x} {y} {z}" for s, (x, y, z) in zip(h.symbols, h.coords))
    assert _shift(parse_block(back), parse_block(start)) < 1e-6


def test_only_the_moved_lines_of_the_users_text_change():
    h = read_hess(H2O)
    rows = [f"  {s}   {x:.5f}   {y:.5f}   {z:.5f}   # mine" for s, (x, y, z) in
            zip(h.symbols, h.coords)]
    text = "3\nwater\n" + "\n".join(rows) + "\n"
    r = displace_along_mode(text, h.symbols, h.coords, h.modes[6], 0.1)
    assert r["ok"]
    old, new = text.split("\n"), r["xyz"].split("\n")
    assert old[:2] == new[:2] and old[-1] == new[-1]
    for a, b in zip(old[2:5], new[2:5]):
        assert b.endswith("# mine") and b.split()[0] == a.split()[0]


@pytest.mark.parametrize("text, says", [
    ("O 0 0 0\nH 0 0 1", "2 atoms and the Hessian 3"),
    ("H 0 0 0\nO 0 0 1\nH 0 1 0", "Atom 1 is H in the coordinates and O in the Hessian"),
])
def test_coordinates_for_other_atoms_are_refused(text, says):
    h = read_hess(H2O)
    r = displace_along_mode(text, h.symbols, h.coords, h.modes[6], 0.2)
    assert not r["ok"] and says in r["error"]


@pytest.mark.parametrize("mode, amp, says", [
    (0, 0.2, "translation or a rotation"),
    (6, 5.0, "at most 2"),
    (6, float("nan"), "at most 2"),
])
def test_a_push_it_cannot_make_is_a_sentence(mode, amp, says):
    h = read_hess(H2O)
    r = displace_along_mode("", h.symbols, h.coords, h.modes[mode], amp)
    assert not r["ok"] and says in r["error"]
