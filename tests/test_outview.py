"""orcamgr/outview.py: the line index behind the Results window's output pane,
which draws a whole .out a screenful at a time and searches it on the backend.
"""
import os

from orcamgr import outview
from orcamgr.outview import OutputIndex, index_for


def _write(p, data: bytes):
    p.write_bytes(data)
    return p


def test_lines_are_numbered_from_the_first(tmp_path):
    p = _write(tmp_path / "a.out", b"one\r\ntwo\n\nfour\n")
    idx = OutputIndex(p)
    assert idx.line_count == 4                 # a trailing newline ends a line
    assert idx.lines(0, 10) == ["one", "two", "", "four"]
    assert idx.lines(2, 2) == ["", "four"]
    assert idx.lines(9, 5) == []
    assert idx.max_len == 4                    # "one\r" counts its \r


def test_a_last_line_without_newline_is_a_line(tmp_path):
    idx = OutputIndex(_write(tmp_path / "a.out", b"a\nb"))
    assert idx.line_count == 2
    assert idx.lines(1, 1) == ["b"]
    assert OutputIndex(_write(tmp_path / "e.out", b"")).line_count == 0


def test_lines_span_chunk_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr(outview, "_CHUNK", 7)
    body = "".join(f"line {i}\n" for i in range(50)).encode()
    idx = OutputIndex(_write(tmp_path / "a.out", body))
    assert idx.line_count == 50
    assert idx.lines(17, 3) == ["line 17", "line 18", "line 19"]


def test_find_lists_every_match_with_its_line_ignoring_ascii_case(tmp_path, monkeypatch):
    monkeypatch.setattr(outview, "_CHUNK", 8)  # matches straddle chunk edges
    p = _write(tmp_path / "a.out",
               b"SCF CONVERGED\nnothing\nscf converged after scf converged\n")
    hits, total = OutputIndex(p).find("Scf Converged")
    assert hits == [0, 2, 2] and total == 3
    assert OutputIndex(p).find("absent") == ([], 0)
    assert OutputIndex(p).find("") == ([], 0)


def test_find_caps_the_positions_but_counts_them_all(tmp_path):
    p = _write(tmp_path / "a.out", b"x\n" * 30)
    hits, total = OutputIndex(p).find("x", limit=10)
    assert len(hits) == 10 and total == 30


def test_an_appended_file_is_extended_and_a_rewritten_one_rescanned(tmp_path):
    p = _write(tmp_path / "a.out", b"a\nb")
    idx = index_for(p)
    assert idx.line_count == 2
    with open(p, "ab") as f:                   # a running job appends
        f.write(b"c\nd\n")
    st = os.stat(p)
    os.utime(p, (st.st_atime, st.st_mtime + 1))
    assert index_for(p).lines(0, 9) == ["a", "bc", "d"]
    # a re-run rewrites from byte 0, and may well end up LONGER
    _write(p, b"new run\nlonger than before\nthree\n")
    os.utime(p, (st.st_atime, st.st_mtime + 2))
    assert index_for(p).lines(0, 9) == ["new run", "longer than before", "three"]


def test_lines_decode_the_way_the_live_tail_does(tmp_path):
    # ORCA echoes argv paths in the ANSI code page; the rest is UTF-8
    p = _write(tmp_path / "a.out", "물 계산\n".encode("utf-8"))
    assert OutputIndex(p).lines(0, 1) == ["물 계산"]
