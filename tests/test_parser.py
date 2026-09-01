"""Tests for orcamgr/core/parser.py.

Contracts under test (see PRINCIPLES.md):
  P27 - the parser is tolerant of any file CONTENT (empty/truncated/CRLF
        input yields partial results, never an exception) and the last
        occurrence of a recurring value wins.
  P6  - file-I/O errors (OSError) DO propagate; converting them is the
        caller's job. That is the documented exception to "never raise".
  P3  - subtle marker decisions are pinned: the excited-states marker is
        case-sensitive because freq thermochemistry prints a lowercase
        look-alike ("no thermally accessible electronically excited
        states") in real non-TDDFT outputs.
  P28 - failures are diagnosed via the prioritized signature dictionary:
        the message carries the explanation, the prescription, and the
        raw ORCA line.

Synthetic fixtures are built in this file (no test-data directory); the
real-corpus smoke test auto-skips when the corpus is absent so the suite
stays green on any machine.
"""

import pathlib

import pytest

from orcamgr.core.parser import ParseResult, parse_file


# ---------------------------------------------------------------------------
# synthetic .out fixtures
# ---------------------------------------------------------------------------

OPT_KEYWORDS = "wB97X-D4 def2-TZVP TightSCF RIJCOSX TightOpt def2/J"
SP_KEYWORDS = "wB97X-D4 def2-TZVP TightSCF RIJCOSX SP def2/J"
FREQ_KEYWORDS = "wB97X-D4 def2-TZVP VeryTightSCF RIJCOSX Freq def2/J"


def _header(keywords: str = OPT_KEYWORDS) -> str:
    """ORCA banner + input echo + charge/multiplicity section."""
    return f"""
                                 *****************
                                 * O   R   C   A *
                                 *****************

                         Program Version 6.1.1  -   RELEASE   -

================================================================================
                                       INPUT FILE
================================================================================
NAME = job.inp
|  1> ! {keywords}
|  2> %maxcore 2400
|  3> * xyz 0 1
|  4> O   0.0 0.0 0.0
|  5> *
|  6>
|  7>                          ****END OF INPUT****

 Total Charge           Charge          ....    0
 Multiplicity           Mult            ....    1
 Number of Electrons    NEL             ....   10
"""


def _geometry(z_oxygen: float) -> str:
    return f"""
---------------------------------
CARTESIAN COORDINATES (ANGSTROEM)
---------------------------------
  O      0.000000    0.000000    {z_oxygen:9.6f}
  H      0.000000    0.757200   -0.469200
  H      0.000000   -0.757200   -0.469200

"""


def _energy(eh: float) -> str:
    return f"\nFINAL SINGLE POINT ENERGY       {eh:.12f}\n"


CONVERGED_MARKER = """
                    ***********************HURRAY********************
                    ***        THE OPTIMIZATION HAS CONVERGED     ***
                    *************************************************
"""

FREQ_BLOCK = """
-----------------------
VIBRATIONAL FREQUENCIES
-----------------------

Scaling factor for frequencies =  1.000000000

   0:         0.00 cm**-1
   1:         0.00 cm**-1
   2:         0.00 cm**-1
   3:         0.00 cm**-1
   4:         0.00 cm**-1
   5:         0.00 cm**-1
   6:      -155.32 cm**-1 ***imaginary mode***
   7:      1610.55 cm**-1
   8:      3660.12 cm**-1

------------
NORMAL MODES
------------
"""

TERMINATION = """
                             ****ORCA TERMINATED NORMALLY****
TOTAL RUN TIME: 0 days 0 hours 5 minutes 30 seconds 123 msec
"""

FIRST_ENERGY = -76.100000000000
FINAL_ENERGY = -76.123456789012

# A converged geometry optimization: energy and geometry each appear TWICE
# (an intermediate opt step, then the converged values) so last-wins is
# actually exercised, not just vacuously true.
FULL_OPT_OUT = (
    _header(OPT_KEYWORDS)
    + _geometry(0.100000) + _energy(FIRST_ENERGY)
    + _geometry(0.117300) + CONVERGED_MARKER + _energy(FINAL_ENERGY)
    + TERMINATION
)

FREQ_OUT = (
    _header(FREQ_KEYWORDS)
    + _geometry(0.117300) + _energy(FINAL_ENERGY)
    + FREQ_BLOCK + TERMINATION
)


def _write_out(tmp_path: pathlib.Path, text: str, name: str = "job.out") -> str:
    """Write fixture bytes verbatim (no newline translation) and return the path."""
    p = tmp_path / name
    p.write_bytes(text.encode("utf-8"))
    return str(p)


# ---------------------------------------------------------------------------
# happy path: termination, energies, geometry, convergence, frequencies
# ---------------------------------------------------------------------------

def test_normal_termination_detected(tmp_path):
    r = parse_file(_write_out(tmp_path, FULL_OPT_OUT))
    assert r.terminated_normally is True
    assert r.error_message == ""
    assert r.orca_version == "6.1.1"
    assert r.run_time_seconds == pytest.approx(330.123)


def test_charge_multiplicity_and_electrons_parsed(tmp_path):
    r = parse_file(_write_out(tmp_path, FULL_OPT_OUT))
    assert r.charge == 0
    assert r.multiplicity == 1
    assert r.n_electrons == 10


def test_final_energy_last_occurrence_wins(tmp_path):
    # the energy appears twice; the LAST one is the converged value (P27)
    r = parse_file(_write_out(tmp_path, FULL_OPT_OUT))
    assert r.final_energy_eh == pytest.approx(FINAL_ENERGY)
    assert r.final_energy_eh != pytest.approx(FIRST_ENERGY)


def test_final_geometry_comes_from_last_coordinate_block(tmp_path):
    r = parse_file(_write_out(tmp_path, FULL_OPT_OUT))
    assert r.n_atoms == 3
    assert [a.symbol for a in r.geometry] == ["O", "H", "H"]
    # the O z-coordinate differs between the two blocks; last wins
    assert r.geometry[0].z == pytest.approx(0.117300)


def test_opt_convergence_marker_detected(tmp_path):
    r = parse_file(_write_out(tmp_path, FULL_OPT_OUT))
    assert r.is_optimization is True
    assert r.opt_converged is True


def test_unconverged_opt_is_not_converged_even_when_terminated_normally(tmp_path):
    # normal termination is not success (P25 is judged downstream on this)
    text = (_header(OPT_KEYWORDS) + _geometry(0.1) + _energy(FIRST_ENERGY)
            + TERMINATION)
    r = parse_file(_write_out(tmp_path, text))
    assert r.terminated_normally is True
    assert r.is_optimization is True
    assert r.opt_converged is False


def test_imaginary_frequency_counted_and_zero_modes_dropped(tmp_path):
    r = parse_file(_write_out(tmp_path, FREQ_OUT))
    assert r.has_frequencies is True
    # the six 0.00 rotation/translation modes are filtered out
    assert r.frequencies == pytest.approx([-155.32, 1610.55, 3660.12])
    assert r.n_imaginary == 1


# ---------------------------------------------------------------------------
# tolerance (P27) and the documented OSError exception (P6)
# ---------------------------------------------------------------------------

def test_empty_file_returns_partial_result_without_exception(tmp_path):
    r = parse_file(_write_out(tmp_path, "", name="empty.out"))
    assert isinstance(r, ParseResult)
    assert r.terminated_normally is False
    assert r.error_message  # honest "ended abnormally" note, not silence
    assert r.final_energy_eh is None
    assert r.geometry == []


def test_truncated_file_returns_partial_result(tmp_path):
    # a crashed/running job: header + one energy, then cut mid-line
    text = (_header(OPT_KEYWORDS) + _geometry(0.100000)
            + _energy(FIRST_ENERGY) + "FINAL SINGLE PO")
    r = parse_file(_write_out(tmp_path, text, name="truncated.out"))
    assert r.terminated_normally is False
    assert r.error_message
    # everything that WAS printed is still recovered
    assert r.final_energy_eh == pytest.approx(FIRST_ENERGY)
    assert r.n_atoms == 3
    assert r.opt_converged is False


def test_crlf_and_mixed_line_endings_parse_identically(tmp_path):
    lf = parse_file(_write_out(tmp_path, FULL_OPT_OUT, name="lf.out"))
    # Windows CRLF throughout
    crlf_text = FULL_OPT_OUT.replace("\n", "\r\n")
    crlf = parse_file(_write_out(tmp_path, crlf_text, name="crlf.out"))
    # mixed: first 20 line breaks CRLF, one lone CR, rest LF
    mixed_text = FULL_OPT_OUT.replace("\n", "\r\n", 20).replace(
        "TOTAL RUN TIME", "\rTOTAL RUN TIME", 1)
    mixed = parse_file(_write_out(tmp_path, mixed_text, name="mixed.out"))
    for r in (crlf, mixed):
        assert r.terminated_normally == lf.terminated_normally is True
        assert r.final_energy_eh == pytest.approx(lf.final_energy_eh)
        assert r.n_atoms == lf.n_atoms == 3
        assert r.opt_converged == lf.opt_converged is True
        assert r.run_time_seconds == pytest.approx(lf.run_time_seconds)


def test_missing_file_propagates_oserror(tmp_path):
    # P6: parse_file tolerates any CONTENT but propagates file-I/O errors;
    # every calling boundary converts them. This is the documented contract.
    with pytest.raises(OSError):
        parse_file(str(tmp_path / "does_not_exist.out"))


# ---------------------------------------------------------------------------
# TD-DFT marker case-sensitivity (P3: the recorded counterexample)
# ---------------------------------------------------------------------------

THERMO_LOOKALIKE = (
    "\n(2) There are no thermally accessible electronically"
    " excited states\n"
)

TDDFT_BLOCKS = """
------------------------------------
TD-DFT/TDA EXCITED STATES (SINGLETS)
------------------------------------

the weight of the individual excitations are printed if larger than 1.0e-02

STATE  1:  E=   0.148342 au      4.036 eV    32555.4 cm**-1
     4a ->   5a  :     0.987654 (c=  0.99381035)

-----------------------------------------------------------------------------
         ABSORPTION SPECTRUM VIA TRANSITION ELECTRIC DIPOLE MOMENTS
-----------------------------------------------------------------------------
      Transition      Energy     Energy  Wavelength fosc(D2)      D2
                       (eV)      (cm-1)    (nm)                 (au**2)
-----------------------------------------------------------------------------
  0-1A  ->  1-1A    4.036000   32555.4   307.2   0.052000000   0.31000
"""


def test_lowercase_excited_states_lookalike_does_not_activate_tddft(tmp_path):
    # freq thermochemistry prints this sentence in real non-TDDFT outputs;
    # a case-folded marker match would treat it as the section header.
    text = (_header(FREQ_KEYWORDS) + _geometry(0.1173) + _energy(FINAL_ENERGY)
            + FREQ_BLOCK + THERMO_LOOKALIKE + TERMINATION)
    r = parse_file(_write_out(tmp_path, text))
    assert r.has_tddft is False
    assert r.transitions == []
    assert r.tddft_states == []


def test_tddft_states_survive_lowercase_lookalike_after_real_block(tmp_path):
    # a TD-DFT+Freq run: the lowercase look-alike appears AFTER the real
    # uppercase block; with last-wins the case-sensitive marker must still
    # land on the real block, not the thermochemistry sentence.
    text = (_header(SP_KEYWORDS) + _geometry(0.1173) + _energy(FINAL_ENERGY)
            + TDDFT_BLOCKS + THERMO_LOOKALIKE + TERMINATION)
    r = parse_file(_write_out(tmp_path, text))
    assert r.has_tddft is True
    assert len(r.transitions) == 1
    t = r.transitions[0]
    assert t.state == 1
    assert t.energy_ev == pytest.approx(4.036)
    assert t.wavelength_nm == pytest.approx(307.2)
    assert t.fosc == pytest.approx(0.052)
    assert len(r.tddft_states) == 1
    st = r.tddft_states[0]
    assert st["state"] == 1
    assert st["ev"] == pytest.approx(4.036)
    assert st["contributions"] == [("4a", "5a", pytest.approx(0.987654))]


TDDFT_VELOCITY_TABLE = """
-----------------------------------------------------------------------------
         ABSORPTION SPECTRUM VIA TRANSITION VELOCITY DIPOLE MOMENTS
-----------------------------------------------------------------------------
      Transition      Energy     Energy  Wavelength fosc(P2)      P2
                       (eV)      (cm-1)    (nm)                 (au**2)
-----------------------------------------------------------------------------
  0-1A  ->  1-1A    4.036000   32555.4   307.2   0.099000000   0.44000
"""


def test_single_root_tddft_does_not_absorb_velocity_table(tmp_path):
    # nroots 1: the electric-dipole table has exactly ONE row, and the velocity
    # table right below has the same row shape. The blank line after the single
    # row must end the scan — previously it only broke after >1 rows, so state
    # 1 was double-counted with the velocity-gauge fosc.
    text = (_header(SP_KEYWORDS) + _geometry(0.1173) + _energy(FINAL_ENERGY)
            + TDDFT_BLOCKS + TDDFT_VELOCITY_TABLE + TERMINATION)
    r = parse_file(_write_out(tmp_path, text))
    assert len(r.transitions) == 1
    assert r.transitions[0].fosc == pytest.approx(0.052)  # electric, not P2


TDDFT_TRIPLET_BLOCK = """
------------------------------------
TD-DFT/TDA EXCITED STATES (TRIPLETS)
------------------------------------

the weight of the individual excitations are printed if larger than 1.0e-02

STATE  1:  E=   0.110000 au      2.993 eV    24140.0 cm**-1
     4a ->   6a  :     0.911111 (c=  0.95452672)
"""


def test_triplets_enabled_keeps_singlet_state_compositions(tmp_path):
    # with triplets requested ORCA prints a SINGLETS block then a TRIPLETS
    # block; plain last-wins kept only the triplet compositions while the
    # absorption table pairs with the singlets. The singlet block must win,
    # and its scan must stop at the triplet header (no mixed state list).
    text = (_header(SP_KEYWORDS) + _geometry(0.1173) + _energy(FINAL_ENERGY)
            + TDDFT_BLOCKS + TDDFT_TRIPLET_BLOCK + TERMINATION)
    r = parse_file(_write_out(tmp_path, text))
    assert len(r.tddft_states) == 1
    st = r.tddft_states[0]
    assert st["ev"] == pytest.approx(4.036)          # the singlet state
    assert st["contributions"] == [("4a", "5a", pytest.approx(0.987654))]


# ---------------------------------------------------------------------------
# error diagnosis (P28)
# ---------------------------------------------------------------------------

def test_scf_not_converged_yields_diagnosis_and_prescription(tmp_path):
    text = _header(SP_KEYWORDS) + """
       *****************************************************
       *                     ERROR                         *
       *        SCF NOT CONVERGED AFTER 125 CYCLES         *
       *****************************************************
"""
    r = parse_file(_write_out(tmp_path, text))
    assert r.terminated_normally is False
    # the 'what': the known explanation for the matched signature
    assert "SCF did not converge" in r.error_message
    # the 'what to try next': an actionable prescription for a chemist
    assert "Try more SCF iterations" in r.error_message
    # the raw ORCA line is quoted so nothing is smoothed over (P2)
    assert "SCF NOT CONVERGED AFTER 125 CYCLES" in r.error_message


def test_signature_diagnosis_beats_generic_error_line(tmp_path):
    # the signature dictionary is prioritized: a specific signature wins
    # over the later generic "ABORTING THE RUN" line
    text = _header(SP_KEYWORDS) + """
UNRECOGNIZED OR DUPLICATED KEYWORD(S) IN SIMPLE INPUT LINE
ABORTING THE RUN
"""
    r = parse_file(_write_out(tmp_path, text))
    assert "did not recognize part of the input" in r.error_message


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Which modes ORCA marked imaginary
# ---------------------------------------------------------------------------

IMAG_ZERO_OUT = (
    "VIBRATIONAL FREQUENCIES\n"
    "-----------------------\n"
    "   0:        -0.00 cm**-1 ***imaginary mode***\n"
    "   1:      1500.00 cm**-1\n"
    "NORMAL MODES\n"
    "****ORCA TERMINATED NORMALLY****\n"
)


def test_a_marked_zero_mode_is_recorded_as_imaginary(tmp_path):
    # the marker is the authority, not the sign: -0.0 < 0 is False
    r = parse_file(_write_out(tmp_path, IMAG_ZERO_OUT, "z.out"))

    assert r.n_imaginary == 1
    assert r.imaginary_frequencies == [-0.0]


def test_a_negative_mode_without_the_marker_still_counts(tmp_path):
    text = ("VIBRATIONAL FREQUENCIES\n"
            "-----------------------\n"
            "   0:      -350.00 cm**-1\n"
            "   1:      1500.00 cm**-1\n"
            "NORMAL MODES\n"
            "****ORCA TERMINATED NORMALLY****\n")
    r = parse_file(_write_out(tmp_path, text, "n.out"))

    assert r.n_imaginary == 1
    assert r.imaginary_frequencies == [-350.0]


# ---------------------------------------------------------------------------
# Unrestricted output has TWO orbital manifolds
#
# UKS/UHF prints "SPIN UP ORBITALS" and "SPIN DOWN ORBITALS" as separate tables
# with a blank line between them, and each numbers from 0. Reading to the first
# blank line stopped at the end of alpha, so an open-shell frontier came from
# alpha alone. The rows below are the real ORCA 6.1.1 output for the OH radical
# (UKS B3LYP/def2-SVP, mult 2), trimmed to the frontier region: the true LUMO is
# beta #4 at -4.0376 eV, while alpha-only reported #5 at +1.5038 eV.
# ---------------------------------------------------------------------------

def _labelled(result):
    """{label: value} over summary_rows(), which yields (label, value, category)."""
    return {label: value for label, value, _cat in result.summary_rows()}


UKS_ORBITALS_OUT = (
    "ORBITAL ENERGIES\n"
    "----------------\n"
    "                 SPIN UP ORBITALS\n"
    "  NO   OCC          E(Eh)            E(eV) \n"
    "   3   1.0000      -0.400192       -10.8898 \n"
    "   4   1.0000      -0.325086        -8.8460 \n"
    "   5   0.0000       0.055265         1.5038 \n"
    "\n"
    "                 SPIN DOWN ORBITALS\n"
    "  NO   OCC          E(Eh)            E(eV) \n"
    "   2   1.0000      -0.432037       -11.7563 \n"
    "   3   1.0000      -0.298066        -8.1108 \n"
    "   4   0.0000      -0.148378        -4.0376 \n"
    "\n"
    "****ORCA TERMINATED NORMALLY****\n"
)

RKS_ORBITALS_OUT = (
    "ORBITAL ENERGIES\n"
    "----------------\n"
    "  NO   OCC          E(Eh)            E(eV) \n"
    "   3   2.0000      -0.400192       -10.8898 \n"
    "   4   2.0000      -0.325086        -8.8460 \n"
    "   5   0.0000       0.055265         1.5038 \n"
    "\n"
    "****ORCA TERMINATED NORMALLY****\n"
)


def test_unrestricted_output_parses_both_spin_manifolds(tmp_path):
    r = parse_file(_write_out(tmp_path, UKS_ORBITALS_OUT, "uks.out"))

    assert [o.spin for o in r.orbitals] == ["a", "a", "a", "b", "b", "b"]
    assert len(r.orbitals) == 6


def test_open_shell_frontier_orbitals_span_both_manifolds(tmp_path):
    r = parse_file(_write_out(tmp_path, UKS_ORBITALS_OUT, "uks.out"))

    # highest occupied of EITHER manifold, lowest virtual of either
    assert (r.homo_index, r.homo_spin) == (3, "b")
    assert (r.lumo_index, r.lumo_spin) == (4, "b")
    assert r.homo_ev == pytest.approx(-8.1108)
    assert r.lumo_ev == pytest.approx(-4.0376)
    assert r.gap_ev == pytest.approx(4.0732)


def test_restricted_output_keeps_one_unlabelled_manifold(tmp_path):
    r = parse_file(_write_out(tmp_path, RKS_ORBITALS_OUT, "rks.out"))

    assert [o.spin for o in r.orbitals] == ["", "", ""]
    assert (r.homo_index, r.lumo_index) == (4, 5)
    assert r.homo_spin == "" and r.lumo_spin == ""


def test_frontier_summary_names_the_manifold_only_when_there_are_two(tmp_path):
    uks = _labelled(parse_file(_write_out(tmp_path, UKS_ORBITALS_OUT, "uks.out")))
    rks = _labelled(parse_file(_write_out(tmp_path, RKS_ORBITALS_OUT, "rks.out")))

    assert "(beta)" in uks["LUMO"]
    assert "(" not in rks["LUMO"]


# ---------------------------------------------------------------------------
# "opt" is a keyword, not a substring
# ---------------------------------------------------------------------------

def _sp_with_keywords(kw):
    return (f"|  1> ! {kw}\n"
            "INPUT FILE\n"
            f"|  1> ! {kw}\n"
            "FINAL SINGLE POINT ENERGY      -76.400000000000\n"
            "****ORCA TERMINATED NORMALLY****\n")


@pytest.mark.parametrize("kw", [
    "B3LYP cc-pVDZ-F12-OptRI SP",     # shipped basis label containing "opt"
    "B3LYP def2-SVP ExtOpt",          # shipped extra option containing "opt"
    "HF def2-SVP NoPropFile",
])
def test_single_point_is_not_reported_as_an_optimization(tmp_path, kw):
    r = parse_file(_write_out(tmp_path, _sp_with_keywords(kw), "sp.out"))
    assert r.is_optimization is False


@pytest.mark.parametrize("kw", ["B3LYP def2-SVP Opt", "B3LYP def2-SVP TightOpt",
                                "B3LYP def2-SVP OptTS", "wB97X-D4 def2-TZVP VeryTightOpt"])
def test_optimization_keywords_are_still_recognized(tmp_path, kw):
    r = parse_file(_write_out(tmp_path, _sp_with_keywords(kw), "opt.out"))
    assert r.is_optimization is True


def test_optimization_without_a_keyword_is_recognized_from_the_output(tmp_path):
    # a raw .inp can drive the optimization from a %geom block alone
    text = (_sp_with_keywords("B3LYP def2-SVP")
            + "*************GEOMETRY OPTIMIZATION CYCLE   1*************\n")
    assert parse_file(_write_out(tmp_path, text, "raw.out")).is_optimization is True


# ---------------------------------------------------------------------------
# TD-DFT with triplets: one table, two multiplicities
# ---------------------------------------------------------------------------

TRIPLET_SPECTRUM_OUT = (
    "         ABSORPTION SPECTRUM VIA TRANSITION ELECTRIC DIPOLE MOMENTS\n"
    "-----------------------------------------------------------------\n"
    "     Transition      Energy     Energy  Wavelength fosc(D2)      D2\n"
    "-----------------------------------------------------------------\n"
    "  0-1A  ->  1-3A    6.939754   55972.9   178.7   0.000000000   0.00000\n"
    "  0-1A  ->  1-1A    7.612667   61400.3   162.9   0.017915283   0.09606\n"
    "  0-1A  ->  2-3A    8.961563   72279.9   138.4   0.000000000   0.00000\n"
    "  0-1A  ->  3-3A    9.050064   72993.7   137.0   0.000000000   0.00000\n"
    "  0-1A  ->  2-1A    9.513980   76735.4   130.3   0.000000000   0.00000\n"
    "  0-1A  ->  3-1A    9.922963   80034.1   124.9   0.085262842   0.35072\n"
    "\n"
    "****ORCA TERMINATED NORMALLY****\n"
)


def test_triplet_transitions_keep_their_multiplicity(tmp_path):
    r = parse_file(_write_out(tmp_path, TRIPLET_SPECTRUM_OUT, "td.out"))

    # state numbers restart per multiplicity, so the pair identifies the state
    assert [(t.state, t.mult) for t in r.transitions] == [
        (1, 3), (1, 1), (2, 3), (3, 3), (2, 1), (3, 1)]


def test_state_count_says_how_many_of_each_multiplicity(tmp_path):
    r = parse_file(_write_out(tmp_path, TRIPLET_SPECTRUM_OUT, "td.out"))
    rows = _labelled(r)

    # "6" for `nroots 3` is not a count anyone asked for
    assert rows["TD-DFT states"] == "3 singlet + 3 triplet"


# real-corpus smoke test (auto-skips off this machine)
# ---------------------------------------------------------------------------

_CORPUS = pathlib.Path(r"D:\02_KSA\03_Research\06_ComputationalChem\Orca")


@pytest.mark.skipif(not _CORPUS.is_dir(),
                    reason="real ORCA output corpus not present on this machine")
def test_corpus_smoke_real_outputs_parse_without_exception():
    files = sorted(_CORPUS.rglob("*.out"))
    assert files, "corpus directory exists but contains no .out files"
    # deterministic spread of up to 8 files across the sorted corpus
    step = max(1, len(files) // 8)
    sample = files[::step][:8]
    for path in sample:
        r = parse_file(str(path))
        assert isinstance(r, ParseResult), path
        assert isinstance(r.terminated_normally, bool), path
        assert r.filename == path.name


# ---------------------------------------------------------------------------
# PATH SUMMARY table kind (NEB-TS vs IRC) + ungated thermochemistry rows
# ---------------------------------------------------------------------------

NEB_PATH_BLOCK = """
                              PATH SUMMARY FOR NEB-TS
              ---------------------------------------------------------
All forces in Eh/Bohr.

Image     E(Eh)   dE(kcal/mol)  max(|Fp|)  RMS(Fp)
   0   -76.100000     0.00       0.00012   0.00005
   1   -76.090000     6.27       0.00034   0.00011
  TS   -76.089000     6.90       0.00021   0.00008   <= TS
   2   -76.099000     0.63       0.00013   0.00006
"""

IRC_PATH_BLOCK = """
                                 IRC PATH SUMMARY
              ---------------------------------------------------------
Step        E(Eh)     dE(kcal/mol)   max(|F|)    RMS(F)
   1    -76.089000      6.90         0.00121    0.00043
   2    -76.095000      3.14         0.00098    0.00031
   3    -76.100000      0.00         0.00011    0.00004
"""

GIBBS_BLOCK = """
-------------------------
GIBBS FREE ENERGY
-------------------------

Final Gibbs free energy         ...     -76.15000000 Eh
"""


def test_neb_path_kind_recorded_for_neb_ts_table(tmp_path):
    out = (_header(OPT_KEYWORDS) + _geometry(0.1) + _energy(FINAL_ENERGY)
           + NEB_PATH_BLOCK + TERMINATION)
    r = parse_file(_write_out(tmp_path, out))
    assert r.has_neb_path is True
    assert r.neb_path_kind == "neb"
    assert any(p["is_ts"] for p in r.neb_path)


def test_irc_path_summary_recorded_as_irc(tmp_path):
    out = (_header(SP_KEYWORDS) + _geometry(0.1) + _energy(FINAL_ENERGY)
           + IRC_PATH_BLOCK + TERMINATION)
    r = parse_file(_write_out(tmp_path, out))
    assert r.has_neb_path is True
    assert r.neb_path_kind == "irc"
    assert len(r.neb_path) == 3


# The plain "PATH SUMMARY" table (NEB/NEB-CI, and printed during every NEB-TS
# run) has an extra Dist.(Ang.) column before E(Eh); the energy columns must be
# read from the header position, never assumed to sit right after the label.
NEB_DIST_PATH_BLOCK = """
                         PATH SUMMARY
---------------------------------------------------------------
All forces in Eh/Bohr.

Image Dist.(Ang.)    E(Eh)   dE(kcal/mol)  max(|Fp|)  RMS(Fp)
  0     0.000    -76.100000      0.00       0.07189   0.01533
  1     3.731    -76.090000      6.27       0.00439   0.00130
  2     4.174    -76.099000      0.63       0.00376   0.00111
"""


def test_plain_neb_path_summary_dist_column_not_read_as_energy(tmp_path):
    out = (_header(SP_KEYWORDS) + _geometry(0.1) + _energy(FINAL_ENERGY)
           + NEB_DIST_PATH_BLOCK + TERMINATION)
    r = parse_file(_write_out(tmp_path, out))
    assert r.has_neb_path is True
    assert [p["e_eh"] for p in r.neb_path] == [-76.1, -76.09, -76.099]
    assert [p["de_kcal"] for p in r.neb_path] == [0.0, 6.27, 0.63]


def test_utf16_encoded_out_is_decoded(tmp_path):
    # PowerShell 5.1's `orca job.inp > job.out` writes UTF-16LE with a BOM;
    # decoded as utf-8 every marker would miss and the file parsed to nothing.
    text = _header(SP_KEYWORDS) + _geometry(0.1) + _energy(FINAL_ENERGY) + TERMINATION
    path = tmp_path / "utf16.out"
    path.write_bytes(text.encode("utf-16"))  # utf-16 codec prepends the LE BOM
    r = parse_file(str(path))
    assert r.terminated_normally is True
    assert r.final_energy_eh == FINAL_ENERGY


def test_thermochemistry_without_frequencies_table_is_still_summarized(tmp_path):
    # A real ORCA shape: an IRC job reading a Hessian prints the GIBBS FREE
    # ENERGY block with no VIBRATIONAL FREQUENCIES table. The parsed value must
    # reach the summary rows — the freq-count rows alone stay gated.
    out = (_header(SP_KEYWORDS) + _geometry(0.1) + _energy(FINAL_ENERGY)
           + GIBBS_BLOCK + TERMINATION)
    r = parse_file(_write_out(tmp_path, out))
    assert r.has_frequencies is False
    assert r.gibbs_eh == pytest.approx(-76.15)
    labels = [label for (label, _v, _c) in r.summary_rows()]
    assert "Final Gibbs G" in labels
    assert "Frequencies" not in labels


def test_orca_version_row_only_when_parsed():
    # MLIP/CREST results (and non-ORCA files) have no version — no "?" noise row.
    r = ParseResult()
    labels = [label for (label, _v, _c) in r.summary_rows()]
    assert "ORCA version" not in labels
    r.orca_version = "6.1.1"
    assert ("ORCA version", "6.1.1", "") in r.summary_rows()


# ---------------------------------------------------------------------------
# read_multiplicity — the head-scan used by the orbital viewer to decide
# whether a *spin density* is worth offering, for a result that has no queue
# entry to ask. Deliberately not a full parse: one integer, bounded read.
# ---------------------------------------------------------------------------

def _out_with_mult(mult, lead=0, tail=""):
    head = "\n".join(f"filler line {i}" for i in range(lead))
    body = (f"  Total Charge           Charge          ....    0\n"
            f"  Multiplicity           Mult            ....    {mult}\n"
            f"  Number of Electrons    NEL             ....   10\n")
    return (head + "\n" if head else "") + body + tail


def test_read_multiplicity_reads_the_value(tmp_path):
    from orcamgr.core.parser import read_multiplicity
    p = tmp_path / "a.out"
    p.write_text(_out_with_mult(2), encoding="utf-8")
    assert read_multiplicity(p) == 2


def test_read_multiplicity_handles_crlf(tmp_path):
    from orcamgr.core.parser import read_multiplicity
    p = tmp_path / "a.out"
    p.write_bytes(_out_with_mult(3).replace("\n", "\r\n").encode())
    assert read_multiplicity(p) == 3


def test_read_multiplicity_is_none_when_absent(tmp_path):
    """None means "the file never said" — the caller must not read that as a
    closed shell it can assert (P2)."""
    from orcamgr.core.parser import read_multiplicity
    p = tmp_path / "a.out"
    p.write_text("nothing useful here\n" * 10, encoding="utf-8")
    assert read_multiplicity(p) is None


def test_read_multiplicity_is_none_for_a_missing_file(tmp_path):
    from orcamgr.core.parser import read_multiplicity
    assert read_multiplicity(tmp_path / "gone.out") is None


def test_read_multiplicity_stops_after_the_scan_window(tmp_path):
    """The bound is what keeps a file that never contains the line from costing
    a full read — ORCA prints it in the input summary, near the top."""
    from orcamgr.core.parser import read_multiplicity, _MULT_SCAN_LINES
    p = tmp_path / "a.out"
    p.write_text(_out_with_mult(2, lead=_MULT_SCAN_LINES + 50), encoding="utf-8")
    assert read_multiplicity(p) is None          # past the window: not found


def test_read_multiplicity_agrees_with_the_full_parse(tmp_path):
    """One number, two routes — they must not drift."""
    from orcamgr.core.parser import parse_file, read_multiplicity
    p = tmp_path / "a.out"
    p.write_text(_out_with_mult(4), encoding="utf-8")
    assert read_multiplicity(p) == parse_file(str(p)).multiplicity == 4


# ---------------------------------------------------------------------------
# The failure diagnosis quotes the CAUSE, not the banner
# ---------------------------------------------------------------------------

def test_the_error_message_quotes_orcas_reason_not_the_abort_banner(tmp_path):
    """ORCA prints the reason and then ".... aborting the run"; quoting the
    matched line handed the user the second one — literally the words the
    signature matched. Every real failure this app has seen prints its cause a
    line or two above."""
    text = ("[file x]: Error: Cannot open input file C:/Users/John\n"
            "  .... aborting the run\n")
    r = parse_file(_write_out(tmp_path, text, "e.out"))

    assert "Cannot open input file" in r.error_message
    assert "aborting the run\")" not in r.error_message


def test_a_matched_error_line_is_still_quoted_when_it_is_the_cause(tmp_path):
    text = "UNRECOGNIZED OR DUPLICATED KEYWORD(S) IN SIMPLE INPUT LINE\n"
    r = parse_file(_write_out(tmp_path, text, "e.out"))
    assert "UNRECOGNIZED" in r.error_message


def test_the_input_echo_starts_at_the_input(tmp_path):
    """The echo began with ORCA's framing — the banner, an 80-character "="
    rule and "NAME = job.inp" — none of which is input text."""
    text = ("INPUT FILE\n"
            "=" * 80 + "\n"
            "NAME = job.inp\n"
            "|  1> ! B3LYP def2-SVP\n"
            "|  2> * xyz 0 1\n"
            "|  3> *\n"
            "                          ****END OF INPUT****\n")
    r = parse_file(_write_out(tmp_path, text, "i.out"))

    assert r.input_block.splitlines()[0] == "! B3LYP def2-SVP"
    assert "NAME =" not in r.input_block
    assert "====" not in r.input_block
