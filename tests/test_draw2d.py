"""The 2D-drawing add-on's Qt-free backend (orcamgr/draw2d/).

RDKit is not needed: the probe is pointed at a fake ``rdkit`` package on
PYTHONPATH, the worker protocol at a stdlib-only stub script, and the MOL
writer is pure. The projection and the stereo rule live in the front-end and
are tested by tests/web/draw2d.test.js.

One opt-in test runs the REAL worker: set ORCADESK_RDKIT_PYTHON to an
interpreter that has RDKit.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from orcamgr.config import Settings
from orcamgr.core.parser import read_charge
from orcamgr.draw2d import env as d2env
from orcamgr.draw2d import installer as d2inst
from orcamgr.draw2d.cdxml import write_cdxml
from orcamgr.draw2d.molfile import MolfileError, write_molblock
from orcamgr.draw2d.worker import WORKER_SCRIPT, clean_atoms, run_worker

PY = sys.executable
ETHANOL = [
    {"el": "C", "x": 1.2750, "y": -0.2515, "z": -0.0158},
    {"el": "C", "x": -0.0146, "y": 0.5611, "z": 0.0328},
    {"el": "O", "x": -1.1686, "y": -0.2415, "z": -0.0563},
    {"el": "H", "x": 1.3033, "y": -0.8526, "z": -0.9214},
    {"el": "H", "x": 2.1344, "y": 0.4130, "z": -0.0069},
    {"el": "H", "x": 1.3404, "y": -0.9160, "z": 0.8449},
    {"el": "H", "x": -0.0400, "y": 1.1639, "z": 0.9532},
    {"el": "H", "x": -0.0684, "y": 1.2380, "z": -0.8229},
    {"el": "H", "x": -1.1389, "y": -0.9024, "z": 0.6447},
]


def _has_real_rdkit(python: str) -> bool:
    try:
        return subprocess.run([python, "-c", "import rdkit"],
                              capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _fake_rdkit(root: Path, with_bonds: bool = True) -> Path:
    pkg = root / "rdkit"
    (pkg / "Chem").mkdir(parents=True)
    (pkg / "__init__.py").write_text("__version__ = '2099.01.1'\n", encoding="utf-8")
    (pkg / "Chem" / "__init__.py").write_text("", encoding="utf-8")
    if with_bonds:
        (pkg / "Chem" / "rdDetermineBonds.py").write_text("", encoding="utf-8")
    return root


# ---------------------------------------------------------------- probe

def test_probe_reports_ready_when_rdkit_and_bond_perception_import(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(_fake_rdkit(tmp_path)))
    p = d2env.probe_env(PY)
    assert p["ready"] is True and p["error"] is None
    assert p["rdkit"] == "2099.01.1"
    st = d2env.status_from_probe(p)
    assert st["state"] == "ready" and "RDKit 2099.01.1" in st["message"]


def test_probe_says_an_rdkit_without_bond_perception_is_too_old(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(_fake_rdkit(tmp_path, with_bonds=False)))
    p = d2env.probe_env(PY)
    assert not p["ready"]
    assert "too old" in p["error"]
    assert d2env.status_from_probe(p)["state"] == "error"


def test_probe_says_rdkit_is_missing(monkeypatch):
    if _has_real_rdkit(PY):
        pytest.skip("this interpreter has RDKit")
    monkeypatch.delenv("PYTHONPATH", raising=False)
    p = d2env.probe_env(PY)
    assert not p["ready"] and "not installed" in p["error"]


def test_probe_refuses_a_missing_path_and_a_folder(tmp_path):
    assert "does not exist" in d2env.probe_env(str(tmp_path / "nope.exe"))["error"]
    assert "folder" in d2env.probe_env(str(tmp_path))["error"]
    assert d2env.status_unset()["state"] == "unset"


def test_probe_reads_its_line_past_trailing_noise():
    out = '{"python": "3.12", "rdkit": "1", "missing": []}\nbye from atexit\n'
    assert d2env.last_json_line(out, "missing")["rdkit"] == "1"
    assert d2env.last_json_line("no json here", "missing") is None


# ---------------------------------------------------------------- worker protocol

_STUB = r'''
import sys, json
req = json.loads(sys.stdin.read())
print("a banner a conda env prints")
print(json.dumps({"ok": True, "rdkit": "stub", "method": "bonds", "note": "",
                  "bonds": [[0, 1, 1], [1, 2, 1]], "charges": [0] * len(req["atoms"]),
                  "radicals": [0] * len(req["atoms"]), "stereocentres": [1, 99],
                  "echo": req}))
'''


def test_worker_protocol_round_trip_with_a_stub():
    r = run_worker(PY, ETHANOL[:3], charge=-1, multiplicity=1, script=_STUB)
    assert r["ok"] is True
    assert r["bonds"] == [[0, 1, 1], [1, 2, 1]]
    assert r["stereocentres"] == [1]          # 99 is not an atom: dropped
    assert r["method"] == "bonds"


def test_worker_result_that_does_not_fit_the_atoms_is_refused():
    bad = _STUB.replace("[[0, 1, 1], [1, 2, 1]]", "[[0, 7, 1]]")
    r = run_worker(PY, ETHANOL[:3], script=bad)
    assert r["ok"] is False and "does not fit" in r["error"]
    bad_order = _STUB.replace("[[0, 1, 1], [1, 2, 1]]", "[[0, 1, 5]]")
    assert run_worker(PY, ETHANOL[:3], script=bad_order)["ok"] is False


def test_worker_error_and_silence_come_back_as_sentences():
    err = 'import json; print(json.dumps({"ok": False, "error": "RDKit could not read these atoms."}))'
    r = run_worker(PY, ETHANOL[:3], script=err)
    assert r == {"ok": False, "error": "RDKit could not read these atoms."}
    r = run_worker(PY, ETHANOL[:3], script="import sys; sys.exit(3)")
    assert r["ok"] is False and "printed no result" in r["error"]


def test_worker_notes_a_multiplicity_its_lewis_structure_disagrees_with():
    r = run_worker(PY, ETHANOL[:3], multiplicity=3, script=_STUB)
    assert r["ok"] and "multiplicity is 3" in r["note"]
    assert "multiplicity" not in run_worker(PY, ETHANOL[:3], multiplicity=1,
                                            script=_STUB)["note"]


def test_worker_refuses_bad_atoms_before_launching_anything(tmp_path):
    for atoms, words in [([], "No atoms"),
                         ([{"el": "C1", "x": 0, "y": 0, "z": 0}], "element"),
                         ([{"el": "C", "x": "nan", "y": 0, "z": 0}], "not a number"),
                         ([{"el": "C"}], "Unreadable")]:
        with pytest.raises(ValueError, match=words):
            clean_atoms(atoms)
    r = run_worker(str(tmp_path / "missing.exe"), ETHANOL)
    assert r["ok"] is False and "Settings" in r["error"]
    assert clean_atoms([{"el": "cl", "x": 0, "y": 0, "z": 0}])[0]["el"] == "Cl"


def test_the_worker_script_is_parameter_free():
    # inputs arrive on stdin; nothing may be formatted into the script itself
    assert "{atoms" not in WORKER_SCRIPT and "%(" not in WORKER_SCRIPT
    compile(WORKER_SCRIPT, "<worker>", "exec")


@pytest.mark.skipif(not os.environ.get("ORCADESK_RDKIT_PYTHON"),
                    reason="set ORCADESK_RDKIT_PYTHON to an interpreter with RDKit")
def test_real_worker_perceives_ethanol_and_falls_back_on_a_bad_charge():
    py = os.environ["ORCADESK_RDKIT_PYTHON"]
    r = run_worker(py, ETHANOL, 0, 1)
    assert r["ok"] and r["method"] == "bonds" and r["note"] == ""
    assert len(r["bonds"]) == 8 and all(o == 1 for *_ij, o in r["bonds"])
    r = run_worker(py, ETHANOL, 1, 1)
    assert r["ok"] and r["method"] == "connectivity" and "single" in r["note"]


def _xyz(text):
    return [{"el": el, "x": float(x), "y": float(y), "z": float(z)}
            for el, x, y, z in (line.split() for line in text.strip().splitlines())]


# methanesulfonamide (MMFF): xyz2mol alone gives S2+ / O- / O-
MESYLAMIDE = _xyz("""
C 1.249181 0.031394 0.475893
S -0.233468 -0.553768 -0.315104
O -0.822119 -1.604875 0.479855
O -0.002012 -0.691402 -1.733126
N -1.253128 0.765492 -0.148410
H 1.025808 0.263519 1.518747
H 1.609484 0.913626 -0.056241
H 1.994417 -0.765164 0.423282
H -2.060779 0.512387 0.424132
H -1.507384 1.128791 -1.069029
""")

# an SN2-like transition state [Cl...CH3...Cl]-: both C...Cl 2.14 A (1.2 x
# covalent, inside RDKit's 1.3 connectivity factor), the partial bonds a TS
# drawing must not turn into charges
SN2_TS = _xyz("""
C 0.000 0.000 0.000
Cl 2.140 0.000 0.000
Cl -2.140 0.000 0.000
H 0.000 1.070 0.000
H 0.000 -0.535 0.927
H 0.000 -0.535 -0.927
""")


@pytest.mark.skipif(not os.environ.get("ORCADESK_RDKIT_PYTHON"),
                    reason="set ORCADESK_RDKIT_PYTHON to an interpreter with RDKit")
def test_real_worker_tidies_charge_separation_and_leaves_partial_bonds_out():
    py = os.environ["ORCADESK_RDKIT_PYTHON"]
    r = run_worker(py, MESYLAMIDE, 0, 1)
    assert r["ok"] and r["method"] == "bonds" and r["note"] == ""
    assert not any(r["charges"])                       # S=O, not S2+ / O-
    assert sorted(o for *_ij, o in r["bonds"]).count(2) == 2
    r = run_worker(py, SN2_TS, -1, 1)
    assert r["ok"] and r["method"] == "bonds"
    assert "Partial bonds" in r["note"] and "C1...Cl2" in r["note"].replace("Cl2...C1", "C1...Cl2")
    assert len(r["bonds"]) == 5                        # the partial bonds still drawn
    assert r["charges"][1] == 0 and r["charges"][2] == 0 and r["charges"][0] == 0


# ---------------------------------------------------------------- MOL V2000

def _drawing():
    return {"title": "test molecule",
            "atoms": [{"el": "C", "x": 0.0, "y": 0.0}, {"el": "C", "x": 1.5, "y": 0.0},
                      {"el": "O", "x": -0.75, "y": 1.3, "charge": -1},
                      {"el": "N", "x": 2.25, "y": -1.3, "charge": 1, "radical": 1}],
            "bonds": [{"a": 0, "b": 1, "order": 1, "stereo": 1},
                      {"a": 0, "b": 2, "order": 1, "stereo": 6},
                      {"a": 1, "b": 3, "order": 2, "stereo": 1}]}


def test_molblock_counts_line_coordinates_and_flags():
    text = write_molblock(_drawing(), now=0)
    lines = text.splitlines()
    assert lines[0] == "test molecule"
    assert lines[1].startswith("  ORCAdesk") and lines[1].endswith("2D")
    assert len(lines[1]) == 22
    assert lines[3] == "  4  3  0  0  0  0  0  0  0  0999 V2000"
    # atom block: fixed columns 10.4 x3, then the symbol
    assert lines[4][:30] == "    0.0000    0.0000    0.0000"
    assert lines[5][:10] == "    1.5000" and lines[5][31:34] == "C  "
    assert lines[6][31:34] == "O  "
    # bond block: narrow end first, wedge 1, hash 6; a wedge on a double is dropped
    assert lines[8] == "  1  2  1  1  0  0  0"
    assert lines[9] == "  1  3  1  6  0  0  0"
    assert lines[10] == "  2  4  2  0  0  0  0"
    assert "M  CHG  2   3  -1   4   1" in lines
    assert "M  RAD  1   4   2" in lines
    assert lines[-1] == "M  END"


def test_molblock_title_is_one_ascii_line():
    d = _drawing()
    d["title"] = "a\nbé" + "x" * 200
    first = write_molblock(d).splitlines()[0]
    assert "\n" not in first and len(first) <= 80 and first.isascii()


@pytest.mark.parametrize("mutate, words", [
    (lambda d: d.update(atoms=[]), "no atoms"),
    (lambda d: d["atoms"][0].update(el="Q1"), "element"),
    (lambda d: d["atoms"][0].update(x=float("inf")), "not a number"),
    (lambda d: d["bonds"].append({"a": 0, "b": 9, "order": 1}), "does not have"),
    (lambda d: d["bonds"].append({"a": 0, "b": 0, "order": 1}), "does not have"),
    (lambda d: d["bonds"][0].update(order=4), "order"),
    (lambda d: d["bonds"][0].update(stereo=4), "stereo"),
    (lambda d: d["atoms"][0].update(charge=40), "charge"),
])
def test_molblock_refuses_what_the_format_cannot_hold(mutate, words):
    d = _drawing()
    mutate(d)
    with pytest.raises(MolfileError, match=words):
        write_molblock(d)


# ---------------------------------------------------------------- CDXML

def _cdxml(d):
    import xml.etree.ElementTree as ET
    return ET.fromstring(write_cdxml(d).split("\n", 2)[2])   # past the prolog + DOCTYPE


def test_cdxml_carries_the_drawings_style_so_chemdraw_draws_it_as_shown():
    root = _cdxml(_drawing())
    # the SVG's sizes at 1 px = 0.75 pt: fixed bond, 13 px labels, 1.5 px lines
    assert float(root.get("BondLength")) == pytest.approx(43.2 * 0.75)
    assert float(root.get("LabelSize")) == pytest.approx(13 * 0.75)
    assert float(root.get("LineWidth")) == pytest.approx(1.5 * 0.75, abs=0.01)
    assert root.find("fonttable/font").get("name") == "Arial"


def test_cdxml_sizes_follow_draw2d_js():
    """The writer restates draw2d.js's px sizes; they must not drift apart."""
    import re
    from orcamgr.draw2d import cdxml
    js = (Path(__file__).resolve().parents[1] / "web" / "next" / "draw2d.js").read_text(encoding="utf-8")
    assert float(re.search(r"const BOND_PX = ([\d.]+);", js).group(1)) == cdxml._BOND_PX
    assert float(re.search(r"const PX = ([\d.]+);", js).group(1)) == cdxml._PX_PER_A
    assert float(re.search(r"const font = ([\d.]+);", js).group(1)) == cdxml._FONT_PX


def test_cdxml_atoms_bonds_and_wedges():
    d = _drawing()
    d["atoms"][2]["label"] = "O"
    d["atoms"][3]["label"] = "NH2"
    root = _cdxml(d)
    nodes = root.findall("page/fragment/n")
    bonds = root.findall("page/fragment/b")
    assert len(nodes) == 4 and len(bonds) == 3
    # carbon is the default element; the others by Z, with charge and radical
    assert [n.get("Element") for n in nodes] == [None, None, "8", "7"]
    assert nodes[2].get("Charge") == "-1" and nodes[3].get("Radical") == "Doublet"
    # the drawn label, its counted hydrogens; a bare carbon has no text
    assert nodes[3].find("t/s").text == "NH2" and nodes[3].get("NumHydrogens") == "2"
    assert nodes[2].get("NumHydrogens") == "0"
    assert nodes[0].find("t") is None
    # 18 pt per Å (24 px x 0.75), y pointing down
    p = [tuple(map(float, n.get("p").split())) for n in nodes]
    assert p[1][0] - p[0][0] == pytest.approx(1.5 * 18)
    assert p[2][1] - p[0][1] == pytest.approx(-1.3 * 18)
    # narrow end at B, as the MOL's first atom; a wedge on a double is dropped
    ids = [n.get("id") for n in nodes]
    assert (bonds[0].get("B"), bonds[0].get("E"), bonds[0].get("Display")) == (ids[0], ids[1], "WedgeBegin")
    assert bonds[1].get("Display") == "WedgedHashBegin"
    assert bonds[2].get("Order") == "2" and bonds[2].get("Display") is None


def test_cdxml_refuses_what_the_mol_writer_refuses():
    d = _drawing()
    d["atoms"][0]["el"] = "Q1"
    with pytest.raises(MolfileError):
        write_cdxml(d)


# ---------------------------------------------------------------- installer

def test_install_plan_is_venv_pip_rdkit(tmp_path):
    plan = d2inst.install_plan("/usr/bin/python3", tmp_path / "env")
    assert [s["label"] for s in plan][0] == "Creating the environment"
    assert plan[0]["argv"][1:3] == ["-m", "venv"]
    assert plan[-1]["argv"][-1] == "rdkit"
    assert "--progress-bar" in plan[-1]["argv"]


def test_supported_python_window():
    assert d2inst.is_supported_python((3, 12))
    assert d2inst.is_supported_python((3, 9)) and d2inst.is_supported_python((3, 15))
    assert not d2inst.is_supported_python((3, 8))
    assert not d2inst.is_supported_python(())


def test_installer_refuses_an_unsupported_base(monkeypatch, tmp_path):
    monkeypatch.setattr(d2inst, "python_version", lambda _p: (3, 7))
    r = d2inst.Draw2dEnvInstaller().run(PY, tmp_path / "env")
    assert not r["ok"] and "3.7" in r["error"]


def test_installer_removes_its_own_leftovers_and_adopts_a_finished_env(tmp_path):
    left = tmp_path / "half"
    (left / "Scripts").mkdir(parents=True)
    (left / "pyvenv.cfg").write_text("", encoding="utf-8")
    assert d2inst._is_half_built(left, registered=False)
    assert not d2inst._is_half_built(left, registered=True)
    done = tmp_path / "done"
    sp = (done / "Lib" / "site-packages") if sys.platform.startswith("win") \
        else (done / "lib" / "python3.12" / "site-packages")
    (sp / "rdkit").mkdir(parents=True)
    py = d2inst.venv_python(done)
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("", encoding="utf-8")
    assert not d2inst._is_half_built(done, registered=False)   # holds RDKit
    r = d2inst.Draw2dEnvInstaller().run(PY, done)
    assert r["ok"] and r["python"] == str(py)
    other = tmp_path / "other"
    other.mkdir()
    (other / "notes.txt").write_text("mine", encoding="utf-8")
    r = d2inst.Draw2dEnvInstaller().run(PY, other)
    assert not r["ok"] and "not an environment" in r["error"]
    assert (other / "notes.txt").exists()


# ---------------------------------------------------------------- settings / charge

def test_settings_draw2d_python_defaults_and_survives_a_wrong_type(tmp_path):
    assert Settings().draw2d_python == ""
    from orcamgr import paths
    cfg = paths.config_file()
    cfg.write_text(json.dumps({"draw2d_python": 12}), encoding="utf-8")
    try:
        assert Settings.load().draw2d_python == ""
    finally:
        cfg.unlink()


def test_read_charge_from_an_output_head(tmp_path):
    out = tmp_path / "x.out"
    out.write_text("junk\n Total Charge           Charge          ....   -2\n"
                   " Multiplicity           Mult            ....    1\n", encoding="utf-8")
    assert read_charge(out) == -2
    assert read_charge(tmp_path / "missing.out") is None
