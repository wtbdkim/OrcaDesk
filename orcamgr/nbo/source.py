"""
Turn a finished calculation into a :class:`~orcamgr.nbo.wavefunction.Wavefunction`.

Everything else in this package reads a Molden file. This is the step that makes
one: ORCA stores its converged wavefunction in a binary ``.gbw``, and
``orca_2mkl`` — which ships beside ``orca`` itself — writes the Molden form of
it. Nothing is recomputed, no SCF is re-run; the ``.gbw`` a job already left
behind is the whole input, which is what makes the analysis retroactive.

The conversion is **cached beside the run**. It costs a second on a small
molecule and rather longer on a large one, and the ``.gbw`` never changes after
a job finishes, so a Molden file that is newer than its ``.gbw`` is reused. The
file is written where ``orca_2mkl`` puts it, next to the ``.gbw``, so a user who
goes looking for something to open in Avogadro finds it there too (P5).

Every way this can fail is a sentence the user can act on: no ``.gbw`` because
the job never converged, no ``orca_2mkl`` because the ORCA path is wrong,
``orca_2mkl`` refusing the file because it came from a different ORCA version.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..config import orca_tool
from .wavefunction import Wavefunction, WavefunctionError, load_molden

#: ``orca_2mkl`` is a file format conversion, not a calculation -- it reads the
#: .gbw and writes text. Minutes would mean something is wrong, not slow.
CONVERSION_TIMEOUT = 300.0

#: What ``orca_2mkl <base> -molden`` writes, beside the ``.gbw`` it read.
MOLDEN_SUFFIX = ".molden.input"


def molden_path(run_dir: str | Path, base: str) -> Path:
    """Where this calculation's Molden file lives, whether or not it exists yet."""
    return Path(run_dir) / f"{base}{MOLDEN_SUFFIX}"


def _is_current(molden: Path, gbw: Path) -> bool:
    """Is an existing Molden file still the one this ``.gbw`` implies?

    Compared by modification time rather than by content: a ``.gbw`` is rewritten
    when a calculation is re-run, and a stale Molden file would then describe the
    previous run's wavefunction — the same class of mistake as serving a cached
    cube for the wrong grid.
    """
    try:
        return molden.is_file() and molden.stat().st_mtime >= gbw.stat().st_mtime
    except OSError:
        return False


def write_molden(orca_path: str | Path, run_dir: str | Path, base: str,
                 force: bool = False) -> Path:
    """Convert ``{run_dir}/{base}.gbw`` to Molden, and return the file's path.

    Reuses an existing conversion unless ``force``. Raises
    :class:`WavefunctionError` with an actionable message on every failure.
    """
    run_dir = Path(run_dir)
    gbw = run_dir / f"{base}.gbw"
    target = molden_path(run_dir, base)

    if not gbw.is_file():
        raise WavefunctionError(
            f"there is no wavefunction file ({gbw.name}) for this calculation. "
            "ORCA writes one when a job converges, so this analysis needs a run "
            "that finished.")
    if not force and _is_current(target, gbw):
        return target

    exe = orca_tool(orca_path, "orca_2mkl")
    if exe is None:
        raise WavefunctionError(
            "orca_2mkl was not found next to the configured ORCA executable. It "
            "ships with ORCA, so this usually means the ORCA path in Settings "
            "points somewhere else.")
    try:
        proc = subprocess.run(
            [str(exe), base, "-molden"], cwd=str(run_dir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", timeout=CONVERSION_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise WavefunctionError(
            f"orca_2mkl did not finish within {CONVERSION_TIMEOUT:.0f} s while "
            f"converting {gbw.name}.") from e
    except OSError as e:
        raise WavefunctionError(f"could not run orca_2mkl: {e}") from e

    if not target.is_file():
        # orca_2mkl reports a refused file on stdout and still exits 0, so the
        # missing output is the reliable signal -- its last line is the reason.
        detail = _last_meaningful_line(proc.stdout)
        raise WavefunctionError(
            f"orca_2mkl could not read {gbw.name}"
            + (f": {detail}" if detail else
               " (it may have been written by a different ORCA version)."))
    return target


def _last_meaningful_line(output: str) -> str:
    """The last non-empty line of a tool's output, as one short sentence."""
    lines = [ln.strip() for ln in (output or "").splitlines() if ln.strip()]
    return lines[-1][:200] if lines else ""


def wavefunction_for(orca_path: str | Path, run_dir: str | Path,
                     base: str) -> Wavefunction:
    """The converged wavefunction of one finished calculation, converting its
    ``.gbw`` first if there is no current Molden file beside it."""
    return load_molden(write_molden(orca_path, run_dir, base))
