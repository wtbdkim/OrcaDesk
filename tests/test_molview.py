"""Unit tests for the 3D-viewer frame reader (orcamgr.molview).

Pure file I/O, no Qt — mirrors how the Bridge slots use it: point it at a
multi-structure .xyz file or a folder of them and get viewer frames back.
"""

from orcamgr.molview import (
    iter_xyz_frames, frames_from_file, frames_from_folder, _parse_energy,
)


def _frame(sym: str, comment: str, z: float) -> str:
    return f"1\n{comment}\n{sym} 0.0 0.0 {z}"


def test_iter_yields_each_frame_verbatim():
    text = "\n".join([_frame("H", "-1.5", 0.0), _frame("O", "-2.5", 1.0)])
    frames = list(iter_xyz_frames(text))
    assert len(frames) == 2
    assert frames[0][0] == "-1.5"                   # comment
    assert frames[0][1].splitlines()[0] == "1"      # verbatim header
    assert frames[1][1].endswith("O 0.0 0.0 1.0")


def test_parse_energy_only_for_bare_numbers():
    assert _parse_energy("-154.123456") == -154.123456
    assert _parse_energy("  -154.1  extra tokens ignored") == -154.1
    assert _parse_energy("Energy = -154") is None    # first token not numeric
    assert _parse_energy("") is None


def test_frames_from_single_frame_file_uses_stem_label(tmp_path):
    p = tmp_path / "best.xyz"
    p.write_text(_frame("H", "-1.0", 0.0), encoding="utf-8")
    frames = frames_from_file(p)
    assert len(frames) == 1
    assert frames[0]["label"] == "best"
    assert frames[0]["energy"] == -1.0
    assert frames[0]["xyz"].startswith("1\n")


def test_frames_from_multi_frame_file_number_the_labels(tmp_path):
    p = tmp_path / "ens.xyz"
    p.write_text("\n".join(_frame("C", str(-10 - k), float(k)) for k in range(3)),
                 encoding="utf-8")
    frames = frames_from_file(p, label_prefix="w_c")
    assert [f["label"] for f in frames] == ["w_c#1", "w_c#2", "w_c#3"]
    assert [f["energy"] for f in frames] == [-10.0, -11.0, -12.0]


def test_frames_from_folder_natural_order(tmp_path):
    (tmp_path / "m_c2.xyz").write_text(_frame("O", "-2", 2.0), encoding="utf-8")
    (tmp_path / "m_c10.xyz").write_text(_frame("N", "-10", 10.0), encoding="utf-8")
    (tmp_path / "m_c1.xyz").write_text(_frame("H", "-1", 1.0), encoding="utf-8")
    frames = frames_from_folder(tmp_path)
    # natural (not lexical) order: c1, c2, c10
    assert [f["label"] for f in frames] == ["m_c1", "m_c2", "m_c10"]


def test_frames_from_folder_empty_is_empty_list(tmp_path):
    assert frames_from_folder(tmp_path) == []


def test_energy_none_when_comment_not_numeric(tmp_path):
    p = tmp_path / "x.xyz"
    p.write_text("1\nfrom CREST run\nH 0 0 0", encoding="utf-8")
    assert frames_from_file(p)[0]["energy"] is None
