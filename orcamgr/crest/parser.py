"""
Parse a finished CREST conformer search into the shared ``ParseResult``.

CREST writes its ensemble to plain multi-structure ``.xyz`` files in the run
folder. The two files this reads:

* ``crest_conformers.xyz`` — the deduplicated, energy-sorted ensemble. Each
  block is a standard XYZ frame whose *comment line* is the conformer's
  **absolute energy in Hartree** (e.g. ``      -11.39433939``). The first frame
  is the lowest-energy conformer (== ``crest_best.xyz``).
* ``crest.energies`` — ``index  relative-energy(kcal/mol)`` rows. Read only as a
  cross-check / fallback; relative energies are otherwise computed from the
  absolute energies in the ``.xyz`` (single source of truth).

Reading this into the SAME ``ParseResult`` the ORCA/MLIP parsers produce lets a
downstream ORCA calc reference a chosen conformer's geometry through the existing
reference path, and lets the Results tab list the ensemble with no special
plumbing. ``geometry`` / ``final_energy_eh`` are set to the lowest-energy
conformer so the best-geometry reference path keeps working unchanged.

Format verified against a real CREST 3.0.2 run (ethanol, GFN2) — see
``tests/crest/fixtures/``.
"""

from __future__ import annotations

import re

from pathlib import Path

from ..core.parser import ParseResult, Atom, Conformer, HARTREE_TO_KCAL


# Exact success line CREST prints on normal completion. It is emitted via a
# Fortran list-directed write, so the physical line has ONE LEADING SPACE
# (" CREST terminated normally.") — match as a substring, never full-line.
CREST_SUCCESS_MARKER = "CREST terminated normally."


def _parse_multi_xyz(text: str) -> list[tuple[float | None, list[Atom]]]:
    """Parse a multi-structure XYZ into ``[(energy_eh, [Atom, ...]), ...]``.

    Tolerant of leading/trailing whitespace and ``\\r\\n``. The comment line's
    first float-parseable token is taken as the energy (Hartree); if none parses,
    the frame's energy is ``None``. Frames with the wrong atom count are skipped.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    frames: list[tuple[float | None, list[Atom]]] = []
    i = 0
    n = len(lines)
    while i < n:
        # skip blank lines between frames
        while i < n and not lines[i].strip():
            i += 1
        if i >= n:
            break
        count_tok = lines[i].split()
        if not count_tok or not count_tok[0].lstrip("+-").isdigit():
            i += 1
            continue
        natoms = int(count_tok[0])
        if natoms <= 0:
            i += 1
            continue
        comment = lines[i + 1] if i + 1 < n else ""
        energy = _first_float(comment)
        atoms: list[Atom] = []
        base = i + 2
        for j in range(base, min(base + natoms, n)):
            parts = lines[j].split()
            if len(parts) < 4:
                break
            try:
                atoms.append(Atom(symbol=parts[0],
                                  x=float(parts[1]), y=float(parts[2]), z=float(parts[3])))
            except ValueError:
                break
        if len(atoms) == natoms:
            frames.append((energy, atoms))
            i = base + natoms
        else:
            # short frame: resume at the line that failed the coordinate scan —
            # advancing by the DECLARED count would jump into (or past) the next
            # frame's header and silently swallow valid conformers after it
            i = base + len(atoms)
    return frames


_FORTRAN_EXP = re.compile(r"^[+-]?\d*\.?\d+[Dd][+-]?\d+$")


def _first_float(s: str) -> float | None:
    for tok in s.split():
        try:
            return float(tok)
        except ValueError:
            # Fortran writes exponents with a D ("-0.765D+02"), which float()
            # does not take. Only worth trying when the token really looks like
            # one, so an ordinary word is not mangled into a number.
            if _FORTRAN_EXP.match(tok):
                try:
                    return float(tok.replace("D", "E").replace("d", "e"))
                except ValueError:
                    continue
            continue
    return None


# Specific, actionable CREST failure signatures, checked against the full .out
# BEFORE the generic exit-code message so a known cause is named plainly. Each is
# (substrings-any, message); the message should say what went wrong AND what to do.
# Grounded in real CREST 3.0.2 output (P3): the topology-change / safety-termination
# family was seen on a real planar-cyclohexane run (exit 1). The list is
# intentionally extensible — add a signature when a new failure mode is observed
# in an actual .out, never from guesswork.
_CREST_ERROR_SIGNATURES: "list[tuple[tuple[str, ...], str]]" = [
    (("Change in topology detected", "safety termination"),
     "CREST stopped because the molecule's bonding topology changed during the "
     "initial optimization — usually a strained or unphysical input geometry "
     "(e.g. a flat ring, or bad bond lengths). Pre-optimize the structure first "
     "(an ORCA or MLIP opt, then reference it), or switch the method to GFN-FF, "
     "then retry."),
]


def _match_error_signature(out_text: str) -> str:
    """Return the message for the first matching known-failure signature, else ""."""
    if not out_text:
        return ""
    for keys, message in _CREST_ERROR_SIGNATURES:
        if any(k in out_text for k in keys):
            return message
    return ""


def _tail_signal_line(out_text: str) -> str:
    """The last output line that looks like an error/termination signal (else the
    very last non-blank line), for appending to a generic failure message."""
    lines = [ln.strip() for ln in out_text.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if any(k in ln for k in ("SIGSEGV", "Segmentation", "error", "ERROR",
                                 "abort", "Abort", "terminated")):
            return ln
    return lines[-1] if lines else ""


def _failure_detail(folder: Path, out_p, fallback: str = "") -> str:
    """Explain why a CREST run produced no ensemble, using the ``.crest.rc`` exit
    code the run script wrote and the ``.out``. Diagnosis is layered: a
    segmentation fault (exit 139 — an intermittent CREST 3.0.2 bug) and known
    failure signatures (e.g. a topology-change safety termination) are named with
    an actionable message; otherwise fall back to the exit code + last output
    line. Surfaced honestly rather than as a bland "no conformers"."""
    rc = None
    if out_p is not None:
        try:
            rc_txt = (folder / f"{out_p.stem}.crest.rc").read_text(encoding="utf-8").strip()
            rc = int(rc_txt)
        except (OSError, ValueError):
            rc = None
    out_text = ""
    if out_p is not None and out_p.exists():
        try:
            out_text = out_p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            out_text = ""
    tail = _tail_signal_line(out_text)

    if rc == 139:
        # self-explanatory; the backtrace tail adds only noise
        return ("CREST crashed (segmentation fault, exit 139) — an intermittent "
                "CREST 3.0.2 bug, not your input. Build the calculation again to retry.")
    # A named cause (topology change, etc.) beats the generic exit-code message.
    signature = _match_error_signature(out_text)
    if signature:
        code = f" (CREST exit code {rc})" if rc not in (None, 0) else ""
        return signature + code
    if rc is not None and rc != 0:
        msg = f"CREST exited with an error (exit code {rc}); it produced no conformers."
        return f"{msg} Last output: {tail}" if tail else msg
    if rc == 0:
        return ("CREST finished (exit 0) but wrote no conformer ensemble."
                + (f" Last output: {tail}" if tail else ""))
    # no .rc marker at all → the run was interrupted before it could finish
    return (fallback or "CREST produced no conformer ensemble (crest_conformers.xyz not found)."
            ) + (f" Last output: {tail}" if tail else "")


def parse_crest_result(output_path: str) -> ParseResult:
    """Read a CREST run's output folder into a ParseResult.

    ``output_path`` is the run's ``.out`` file (in the Windows workspace folder);
    ``crest_conformers.xyz`` / ``crest.energies`` are read from the same folder.
    A missing/empty ensemble yields ``terminated_normally = False`` with an
    ``error_message`` so the engine/validation treat it as a failure.
    """
    r = ParseResult(path=str(output_path or ""), is_conformer_search=True)
    if r.path:
        r.filename = Path(r.path).name

    out_p = Path(output_path) if output_path else None
    folder = out_p.parent if out_p else None
    if folder is None:
        r.error_message = "CREST run produced no output folder."
        return r

    conf_p = folder / "crest_conformers.xyz"
    if not conf_p.exists():
        # Fall back to the single best structure if the ensemble file is absent.
        conf_p = folder / "crest_best.xyz"
    if not conf_p.exists():
        # No ensemble — explain WHY from the exit-code marker + output (a CREST
        # crash must not be reported as a bland "no ensemble"; P2/A22).
        r.error_message = _failure_detail(folder, out_p)
        return r

    try:
        frames = _parse_multi_xyz(conf_p.read_text(encoding="utf-8", errors="replace"))
    except OSError as e:
        r.error_message = f"Could not read CREST ensemble: {e}"
        return r

    if not frames:
        r.error_message = _failure_detail(folder, out_p,
                                          fallback="CREST ensemble file contained no structures.")
        return r

    # Rank by absolute energy (frames with a parsed energy first, ascending);
    # CREST already writes them sorted, but don't rely on it.
    def _key(fr):
        return (fr[0] is None, fr[0] if fr[0] is not None else 0.0)
    frames.sort(key=_key)

    e_min = frames[0][0]
    conformers: list[Conformer] = []
    for idx, (energy, atoms) in enumerate(frames, start=1):
        rel = ((energy - e_min) * HARTREE_TO_KCAL
               if (energy is not None and e_min is not None) else 0.0)
        conformers.append(Conformer(index=idx,
                                     energy_eh=energy if energy is not None else 0.0,
                                     rel_kcal=rel,
                                     geometry=atoms))

    r.conformers = conformers
    best = conformers[0]
    r.geometry = best.geometry
    r.n_atoms = len(best.geometry)
    # `is not None`, not truthiness: a conformer energy of exactly 0.0 Eh is a
    # real (if unusual) value, and treating it as "missing" dropped it.
    if best.energy_eh is not None:
        r.final_energy_eh = best.energy_eh

    # Success detection: prefer the explicit marker in the .out; if the .out is
    # unavailable, a non-empty ensemble is taken as success (the ensemble only
    # exists if CREST reached the CREGEN output stage).
    if out_p and out_p.exists():
        try:
            out_text = out_p.read_text(encoding="utf-8", errors="replace")
            r.terminated_normally = CREST_SUCCESS_MARKER in out_text
            if not r.terminated_normally:
                r.error_message = ("CREST did not report normal termination "
                                   "(marker not found in output).")
        except OSError:
            r.terminated_normally = True
    else:
        r.terminated_normally = True

    return r
