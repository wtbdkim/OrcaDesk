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
