"""Decoding text that Windows does not write in UTF-8.

Three of ORCAdesk's four backends hand it bytes that are *not* UTF-8 on Windows,
and each was being read as if they were:

* **ORCA's `.out`** is genuinely mixed. ORCA writes its own (ASCII) strings as
  UTF-8 but echoes the paths it was given on argv in the process ANSI code page.
  Calc names may be Unicode by design — Korean names are explicitly allowed —
  and the default workspace lives under the user's profile folder, so on a
  Korean Windows every line quoting the run path arrived as a run of U+FFFD.
* **A CPython child writing to a pipe** (`python -m venv`, `pip install`, the
  MLIP worker) encodes stdout with `locale.getencoding()`, not UTF-8. That is
  the only diagnostic a failed install or a crashed worker has.
* **Console tools** (`where`, `py -0p`) print in the console code page. A path
  under a Hangul folder decoded as UTF-8 became mojibake, `Path.exists()` said
  no, and the interpreter silently vanished from the list.

WSL is deliberately not in that list: `crest/wsl.py` sets `WSL_UTF8=1`, so its
output really is UTF-8.

Qt-free and dependency-free, so every layer can share the one judgment (P4).
"""
from __future__ import annotations

import locale
import sys

# The process ANSI code page — cp949 on Korean Windows, cp1252 on a Western one.
# Empty off Windows, where the whole question does not arise (everything is
# UTF-8) and guessing would only add a way to be wrong.
try:
    ANSI_ENCODING = (locale.getpreferredencoding(False)
                     if sys.platform.startswith("win") else "")
except Exception:                                   # pragma: no cover
    ANSI_ENCODING = ""


def decode_process_output(raw: bytes) -> str:
    """Decode a child process's output: UTF-8 if it is valid, else the ANSI code
    page, else UTF-8 with replacement.

    UTF-8 first because it is what a correctly-configured child (and every
    non-Windows one) produces, and because a byte string that decodes cleanly as
    UTF-8 is almost never something else. The replacement pass at the end is the
    guarantee that this never raises: a diagnostic that cannot be read is still
    better than an exception thrown while reporting an error.
    """
    if not raw:
        return ""
    if isinstance(raw, str):
        # already decoded (a caller that kept text mode, a test double) — the
        # helper stays total rather than making every call site check
        return raw
    for enc in ("utf-8", ANSI_ENCODING):
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def repair_ansi_line(line: str) -> str:
    """Repair one line read as UTF-8 with ``errors="surrogateescape"``.

    This is the mixed-encoding case: part of the line is UTF-8 and part of it is
    ANSI, so the file cannot simply be decoded in one codec. Reading with
    ``surrogateescape`` keeps the undecodable bytes recoverable instead of
    destroying them as U+FFFD; a line with no surrogates is already valid UTF-8
    and is returned untouched, and one with surrogates is put back to bytes and
    decoded in the ANSI code page. Decoding the whole line that way is safe
    because the UTF-8 half is ASCII, which both codecs agree on.
    """
    if not any("\udc80" <= ch <= "\udcff" for ch in line):
        return line
    try:
        raw = line.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:                      # pragma: no cover
        return line
    return decode_process_output(raw)
