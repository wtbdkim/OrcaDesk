"""Unit tests for the 3D-viewer frame reader (orcamgr.molview).

Pure file I/O, no Qt — mirrors how the Bridge slots use it: point it at a
multi-structure .xyz file or a folder of them and get viewer frames back.
"""

from orcamgr.molview import (
    iter_xyz_frames, frames_from_file, frames_from_folder, _parse_energy,
    count_xyz_frames, discover_structure_sets,
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


# --- discovery: what a result folder holds that the viewer can open ----------
# The Results tab's Visual mode lists these instead of asking the user to find
# them in a folder dialog, so what is (and is not) discovered is behaviour.

def test_discover_lists_xyz_files_with_frame_counts(tmp_path):
    (tmp_path / "run.xyz").write_text(_frame("H", "-1", 0.0), encoding="utf-8")
    (tmp_path / "run_trj.xyz").write_text(
        "\n".join(_frame("H", str(-1 - k), float(k)) for k in range(4)), encoding="utf-8")
    sets = discover_structure_sets(tmp_path, base="run")
    by_label = {s["label"]: s for s in sets}
    assert by_label["run.xyz"]["count"] == 1
    # the trajectory is named by what it is, not by its filename
    assert by_label["Optimization trajectory"]["count"] == 4
    assert all(s["kind"] == "file" for s in sets)


def test_discover_names_the_crest_ensemble(tmp_path):
    (tmp_path / "crest_conformers.xyz").write_text(
        "\n".join(_frame("H", str(-1 - k), float(k)) for k in range(3)), encoding="utf-8")
    (tmp_path / "crest_best.xyz").write_text(_frame("H", "-1", 0.0), encoding="utf-8")
    labels = [s["label"] for s in discover_structure_sets(tmp_path)]
    assert "Conformer ensemble" in labels and "Best conformer" in labels


def test_discover_finds_the_conformers_subfolder_first(tmp_path):
    (tmp_path / "run.xyz").write_text(_frame("H", "-1", 0.0), encoding="utf-8")
    sub = tmp_path / "conformers"
    sub.mkdir()
    for k in range(3):
        (sub / f"run_c{k + 1}.xyz").write_text(_frame("H", str(-1 - k), float(k)),
                                               encoding="utf-8")
    sets = discover_structure_sets(tmp_path, base="run")
    # the per-conformer export is what someone opening a finished search wants,
    # so it leads — and a folder set counts FILES, not frames
    assert sets[0]["kind"] == "folder" and sets[0]["count"] == 3
    assert sets[0]["label"] == "Conformers (exported)"


def test_discover_skips_empty_subfolders_and_non_xyz(tmp_path):
    (tmp_path / "run.out").write_text("not a structure", encoding="utf-8")
    (tmp_path / "cubes").mkdir()                     # orca_plot's output folder
    (tmp_path / "cubes" / "run.mo1a.g60.cube").write_text("x", encoding="utf-8")
    assert discover_structure_sets(tmp_path, base="run") == []


def test_discover_on_a_missing_folder_is_empty_not_an_error(tmp_path):
    assert discover_structure_sets(tmp_path / "gone") == []


def test_folder_with_no_xyz_of_its_own_reads_its_conformers_subfolder(tmp_path):
    """Pointing the viewer at a finished CREST run's folder means the
    per-conformer export — the caller should not have to know that."""
    sub = tmp_path / "conformers"
    sub.mkdir()
    (sub / "m_c1.xyz").write_text(_frame("H", "-1", 0.0), encoding="utf-8")
    (sub / "m_c2.xyz").write_text(_frame("O", "-2", 1.0), encoding="utf-8")
    assert [f["label"] for f in frames_from_folder(tmp_path)] == ["m_c1", "m_c2"]


def test_a_folders_own_xyz_wins_over_the_subfolder(tmp_path):
    (tmp_path / "top.xyz").write_text(_frame("H", "-1", 0.0), encoding="utf-8")
    sub = tmp_path / "conformers"
    sub.mkdir()
    (sub / "m_c1.xyz").write_text(_frame("O", "-2", 1.0), encoding="utf-8")
    assert [f["label"] for f in frames_from_folder(tmp_path)] == ["top"]


def test_count_frames_of_an_unreadable_file_is_zero(tmp_path):
    assert count_xyz_frames(tmp_path / "nope.xyz") == 0


def test_many_unnamed_xyz_collapse_into_one_whole_folder_set(tmp_path):
    """A conformers/ export holds hundreds of .xyz. One row each would be a list
    nobody reads, built from hundreds of file reads on a tab switch."""
    for k in range(20):
        (tmp_path / f"m_c{k + 1}.xyz").write_text(_frame("H", str(-1 - k), float(k)),
                                                  encoding="utf-8")
    sets = discover_structure_sets(tmp_path)
    assert len(sets) == 1
    assert sets[0]["kind"] == "folder" and sets[0]["count"] == 20
    assert sets[0]["path"] == str(tmp_path)


def test_named_files_keep_their_own_row_past_the_collapse(tmp_path):
    (tmp_path / "crest_conformers.xyz").write_text(_frame("H", "-1", 0.0), encoding="utf-8")
    for k in range(20):
        (tmp_path / f"junk{k}.xyz").write_text(_frame("H", "-1", 0.0), encoding="utf-8")
    labels = [s["label"] for s in discover_structure_sets(tmp_path)]
    assert "Conformer ensemble" in labels
    assert any(l.startswith("All structures in") for l in labels)
    assert not any(l.startswith("junk") for l in labels)
