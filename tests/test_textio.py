"""
Tests for orcamgr/textio.py and the readers that use it.

Windows does not hand ORCAdesk UTF-8. An ORCA `.out` is mixed (ORCA's own ASCII
strings as UTF-8, the paths it was given on argv in the process ANSI code page);
a CPython child — `pip`, `python -m venv`, the MLIP worker — encodes its pipe
output with the locale ANSI code page. Every one of those was read as UTF-8, so
on a Korean Windows the run path in the Log tab, the interpreter list in the
MLIP installer, and a failed install's only diagnostic all came back as U+FFFD.

Calc names may be Unicode by design (P33 allows Korean) and the default
workspace lives under the user's profile folder, so this is the normal case for
a Korean user, not an exotic one.

No Qt, no ORCA: the fixtures are bytes.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

from orcamgr.core.tailer import FileTailer, decode_orca_line
from orcamgr.core.parser import parse_file
from orcamgr.textio import decode_process_output, repair_ansi_line

# cp949 is the Korean Windows ANSI/OEM code page; skip where it isn't the one
# repair_ansi_line will reach for.
_KOREAN = sys.platform.startswith("win")
_KO_PATH = "C:\\work\\물분자\\물분자.inp"


def _out(tmp_path: pathlib.Path, body: bytes, name: str = "job.out") -> pathlib.Path:
    p = tmp_path / name
    p.write_bytes(body)
    return p


# ---- decode_process_output --------------------------------------------------

def test_utf8_output_is_decoded_as_utf8():
    assert decode_process_output("설치 완료".encode("utf-8")) == "설치 완료"


def test_empty_output_is_empty_not_an_error():
    assert decode_process_output(b"") == ""
    assert decode_process_output(None) == ""


def test_already_decoded_text_passes_through():
    # the helper is total, so no call site has to check the type first
    assert decode_process_output("plain") == "plain"


@pytest.mark.skipif(not _KOREAN, reason="ANSI code page fallback is a Windows rule")
def test_ansi_output_falls_back_to_the_code_page():
    # `where python.exe` printing a path under a Hangul folder: not valid UTF-8,
    # so it used to decode to mojibake, Path.exists() said no, and the
    # interpreter vanished from the MLIP installer's list
    assert decode_process_output(_KO_PATH.encode("cp949")) == _KO_PATH


def test_undecodable_output_never_raises():
    # a diagnostic that cannot be read is still better than an exception thrown
    # while reporting an error
    assert isinstance(decode_process_output(b"\xff\xfe\x00 broken"), str)


# ---- repair_ansi_line / the ORCA .out ---------------------------------------

def test_a_pure_utf8_line_is_returned_untouched():
    line = "ORCA TERMINATED NORMALLY"
    assert repair_ansi_line(line) is line


@pytest.mark.skipif(not _KOREAN, reason="ANSI code page fallback is a Windows rule")
def test_a_mixed_encoding_line_is_repaired(tmp_path):
    raw = b"  NAME = " + _KO_PATH.encode("cp949")
    line = raw.decode("utf-8", "surrogateescape")
    assert decode_orca_line(line) == "  NAME = " + _KO_PATH


@pytest.mark.skipif(not _KOREAN, reason="ANSI code page fallback is a Windows rule")
def test_the_live_tail_shows_the_real_path(tmp_path):
    p = _out(tmp_path, b"****ORCA TERMINATED NORMALLY****\n"
                       + ("  NAME = " + _KO_PATH).encode("cp949") + b"\n")
    seen = []
    FileTailer(p).drain(seen.append)

    assert seen == ["****ORCA TERMINATED NORMALLY****", "  NAME = " + _KO_PATH]


@pytest.mark.skipif(not _KOREAN, reason="ANSI code page fallback is a Windows rule")
def test_the_parser_reads_the_same_file_the_same_way(tmp_path):
    # the Log tab and the Results tab must not disagree about what the file says
    p = _out(tmp_path, b"****ORCA TERMINATED NORMALLY****\n"
                       + ("  NAME = " + _KO_PATH).encode("cp949") + b"\n")
    r = parse_file(str(p))

    assert r.terminated_normally is True


def test_a_utf16_out_still_wins_over_the_repair(tmp_path):
    # PowerShell 5.1 redirection writes UTF-16; the BOM sniff must come first
    p = _out(tmp_path, "****ORCA TERMINATED NORMALLY****\n".encode("utf-16"))
    assert parse_file(str(p)).terminated_normally is True


# ---- drain is linear, not quadratic -----------------------------------------

def test_a_multi_megabyte_burst_drains_in_one_pass(tmp_path):
    """drain() sliced the buffer once per line, copying the remainder every
    time. ORCA emits multi-MB bursts (normal modes, MO coefficients) and the
    monitor only checks cancel/detach BETWEEN drains, so Stop went unanswered:
    measured 11 s for 4 MB and 47 s for 8 MB, with the next chunk larger again.
    """
    import time

    line = "x" * 200 + "\n"
    p = _out(tmp_path, (line * 40_000).encode("utf-8"))   # ~8 MB
    n = 0

    def count(_ln):
        nonlocal n
        n += 1

    t0 = time.perf_counter()
    FileTailer(p).drain(count)
    elapsed = time.perf_counter() - t0

    assert n == 40_000
    # generous by two orders of magnitude against the old 47 s, so the test
    # pins the complexity rather than the machine
    assert elapsed < 3.0, f"{elapsed:.1f}s for 8 MB — drain is quadratic again"


def test_a_partial_final_line_stays_buffered_across_drains(tmp_path):
    p = _out(tmp_path, b"first\nsecond\npart")
    tailer = FileTailer(p)
    seen = []

    tailer.drain(seen.append)
    assert seen == ["first", "second"]

    with open(p, "a", encoding="utf-8") as f:
        f.write("ial\n")
    tailer.drain(seen.append)
    assert seen == ["first", "second", "partial"]


def test_a_character_split_across_two_drains_survives(tmp_path):
    """A multibyte character straddling the poll boundary used to be decoded as
    two U+FFFD — the file is read in fixed chunks and each read finalized the
    decoder, destroying the bytes. Reading with surrogateescape carries them
    into the buffer instead, and the line is repaired when it completes."""
    full = "hello 물분자 world\n".encode("utf-8")
    cut = full.index("물".encode("utf-8")) + 1     # mid-character
    p = _out(tmp_path, full[:cut])
    tailer = FileTailer(p)
    seen = []

    tailer.drain(seen.append)
    assert seen == []                              # nothing complete yet

    with open(p, "ab") as f:
        f.write(full[cut:])
    tailer.drain(seen.append)

    assert seen == ["hello 물분자 world"]


def test_crlf_is_stripped(tmp_path):
    p = _out(tmp_path, b"one\r\ntwo\r\n")
    seen = []
    FileTailer(p).drain(seen.append)
    assert seen == ["one", "two"]
