"""Unit tests for the orca_plot shell-out (orcamgr.core.plot).

The menu sequences are the contract with an external binary that has no stable
machine interface, so they are pinned character for character. Each one was run
against the real ORCA 6.1.1 ``orca_plot`` and confirmed to exit 0 with the
expected cube on disk; if someone "tidies" one, the failure mode is not a wrong
picture but a hang — a desynced sequence makes orca_plot spin on EOF printing
``Invalid input`` without end (P56: tests pin the contracts that matter).

Everything here is ORCA-free: the sequence builders, the trust-boundary clamp
and the pre-flight refusals all run without the binary.
"""

import pytest

from orcamgr.core.plot import (
    DEFAULT_GRID, GRID_CHOICES, MAX_CUBE_BYTES, PLOT_KINDS,
    CubeRequest, _menu_sequence, cube_filename, generate_cube, orca_plot_exe,
    plot_output_name,
)


# --- the verified menu sequences ---------------------------------------------

def test_mo_sequence_is_the_verified_one():
    """5,7 = Gaussian cube; 4,G = grid; 1,1 = MO plot; 3,op; 2,n; 11 plot; 12 exit."""
    seq = _menu_sequence(CubeRequest(kind="mo", index=96, operator=0, grid=60))
    assert seq == "5\n7\n4\n60\n1\n1\n3\n0\n2\n96\n11\n12\n"


def test_beta_operator_rides_the_operator_prompt():
    seq = _menu_sequence(CubeRequest(kind="mo", index=3, operator=1, grid=40))
    assert seq == "5\n7\n4\n40\n1\n1\n3\n1\n2\n3\n11\n12\n"


def test_density_sequences_answer_the_extra_filename_prompt():
    """Selecting a density makes orca_plot offer a default density name and wait
    for y/n. That prompt has no analogue in the MO path, and skipping it is
    exactly what desynchronizes the run."""
    assert _menu_sequence(CubeRequest(kind="eldens", grid=60)) == "5\n7\n4\n60\n1\n2\ny\n11\n12\n"
    assert _menu_sequence(CubeRequest(kind="spindens", grid=60)) == "5\n7\n4\n60\n1\n3\ny\n11\n12\n"


def test_every_sequence_ends_by_exiting():
    """Without the trailing exit, orca_plot reaches EOF and loops forever."""
    for kind in PLOT_KINDS:
        assert _menu_sequence(CubeRequest(kind=kind)).endswith("11\n12\n")


# --- output filenames ---------------------------------------------------------

@pytest.mark.parametrize("req,expected", [
    (CubeRequest(kind="mo", index=4, operator=0), "w.mo4a.cube"),
    (CubeRequest(kind="mo", index=3, operator=1), "w.mo3b.cube"),
    (CubeRequest(kind="eldens"), "w.eldens.cube"),
    (CubeRequest(kind="spindens"), "w.spindens.cube"),
])
def test_plot_output_name_matches_what_orca_plot_writes(req, expected):
    assert plot_output_name("w", req) == expected


def test_stored_name_carries_the_grid():
    """orca_plot names a plot by what it is, not how finely it was sampled, so
    the cache key must add the grid — otherwise asking for 80 after viewing 60
    returns the coarse cube under an 80 label."""
    a = cube_filename("w", CubeRequest(kind="mo", index=4, grid=60))
    b = cube_filename("w", CubeRequest(kind="mo", index=4, grid=80))
    assert a == "w.mo4a.g60.cube" and b == "w.mo4a.g80.cube"
    assert a != b


def test_every_stored_name_is_grid_qualified():
    for kind in PLOT_KINDS:
        for grid in GRID_CHOICES:
            assert cube_filename("w", CubeRequest(kind=kind, grid=grid)).endswith(
                f".g{grid}.cube")


# --- trust boundary -----------------------------------------------------------

def test_unknown_kind_falls_back_to_mo():
    assert CubeRequest(kind="../etc").normalized().kind == "mo"


def test_grid_is_snapped_to_an_offered_choice():
    """The grid is interpolated into the keystroke stream, so it must be one of
    ours — an arbitrary number would also be an arbitrary cube size (P34)."""
    for raw, want in ((1000, 80), (-5, 40), (55, 60), (41, 40)):
        assert CubeRequest(grid=raw).normalized().grid == want
    assert CubeRequest(grid=DEFAULT_GRID).normalized().grid in GRID_CHOICES


def test_an_unset_grid_means_the_default_not_the_smallest():
    """0 is "not specified" on the wire, so it resolves to DEFAULT_GRID — snapping
    it to the nearest choice would silently hand every such caller 40³."""
    assert CubeRequest(grid=0).normalized().grid == DEFAULT_GRID


def test_negative_orbital_index_is_clamped():
    assert CubeRequest(index=-5).normalized().index == 0


def test_operator_is_binary():
    assert CubeRequest(operator=9).normalized().operator == 0
    assert CubeRequest(operator=1).normalized().operator == 1


# --- executable resolution ----------------------------------------------------

def test_orca_plot_is_found_beside_orca(tmp_path):
    (tmp_path / "orca.exe").write_text("")
    (tmp_path / "orca_plot.exe").write_text("")
    assert orca_plot_exe(tmp_path / "orca.exe") == tmp_path / "orca_plot.exe"


def test_missing_orca_plot_is_none_not_a_guess(tmp_path):
    (tmp_path / "orca.exe").write_text("")
    assert orca_plot_exe(tmp_path / "orca.exe") is None
    assert orca_plot_exe("") is None


# --- pre-flight refusals (no ORCA needed) -------------------------------------

def test_refuses_without_a_gbw(tmp_path):
    r = generate_cube(tmp_path / "orca.exe", tmp_path, "w", CubeRequest())
    assert r["ok"] is False and "gbw" in r["error"]


def test_refuses_without_orca_plot(tmp_path):
    (tmp_path / "w.gbw").write_text("")
    r = generate_cube(tmp_path / "orca.exe", tmp_path, "w", CubeRequest())
    assert r["ok"] is False and "orca_plot" in r["error"]


def test_an_existing_cube_is_reused_without_running_anything(tmp_path):
    """Reopening an orbital must not re-run orca_plot: the cube is a pure
    function of its inputs, so a regenerated one would be byte-identical."""
    cubes = tmp_path / "cubes"
    cubes.mkdir()
    (cubes / "w.mo4a.g60.cube").write_text("cached")
    r = generate_cube(tmp_path / "no-orca.exe", tmp_path, "w",
                      CubeRequest(kind="mo", index=4, grid=60))
    assert r["ok"] is True and r["cached"] is True
    assert r["path"].endswith("w.mo4a.g60.cube")


def test_reuse_can_be_turned_off(tmp_path):
    cubes = tmp_path / "cubes"
    cubes.mkdir()
    (cubes / "w.mo4a.g60.cube").write_text("cached")
    r = generate_cube(tmp_path / "no-orca.exe", tmp_path, "w",
                      CubeRequest(kind="mo", index=4, grid=60), reuse=False)
    assert r["ok"] is False        # falls through to the missing-.gbw refusal


def test_an_empty_cached_file_is_not_reused(tmp_path):
    """A zero-byte cube is a crashed run, not a result."""
    cubes = tmp_path / "cubes"
    cubes.mkdir()
    (cubes / "w.mo4a.g60.cube").write_text("")
    r = generate_cube(tmp_path / "no-orca.exe", tmp_path, "w",
                      CubeRequest(kind="mo", index=4, grid=60))
    assert r["ok"] is False


def test_payload_cap_leaves_room_for_the_largest_offered_grid():
    """80³ on a typical box is ~7.3 MB measured; the cap must not refuse what
    the UI itself can produce."""
    assert MAX_CUBE_BYTES > 8_000_000
