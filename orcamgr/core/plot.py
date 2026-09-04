"""
Volumetric data (molecular orbitals, electron / spin density) from a finished
calculation's ``.gbw``, by shelling out to ORCA's ``orca_plot``.

This is post-processing, not a queue step: ``orca_plot`` reads the *converged*
wavefunction, so a calculation that is already ``DONE`` can be visualized
without re-running anything. Qt-free and framework-free like the rest of
``core/`` — the Bridge slot drives it on a background thread.

Cost, measured on this machine (ORCA 6.1.1, 52 atoms / 987 basis functions):
one MO at a 60³ grid takes **0.17 s** and writes a 3.1 MB cube; the same grid
costs 0.9 MB at 40³ and 7.3 MB at 80³. An SCF *density* plot over that grid
takes **9.9 s** — ~60× an MO, since it contracts the whole density matrix
rather than one MO vector, but still seconds, not minutes (P3). Both are
trivial next to the SCF that produced the ``.gbw``, which is the point: the
expensive work is already paid for.

The **electrostatic potential** breaks that pattern and is the one kind whose
cost the UI has to say out loud. It is a Coulomb sum over the entire density at
every grid point, and it measured **49.3 s at 40³** on that same system — ~2.8
min at 60³ and ~6.6 min at 80³ by the cubic scaling. It also scales with the
basis: the same plot on water at def2-SVP (24 basis functions) is **0.2 s**. So
ESP defaults to the coarse grid (:data:`DEFAULT_GRIDS`), which costs it little —
the potential is a smooth, long-ranged field, unlike an orbital.

Why the interactive menu and not ``orca_plot gbw-file plot-inputfile``
---------------------------------------------------------------------
``orca_plot`` advertises a non-interactive plot-input-file mode, but its parser
reads ~17 positional fields in a fixed order (PlotType, Format, MO/OP, state
density, infile, outfile, ncont, icont, Skeleton, Atoms, UseCol, dim1/dim2,
min/max per direction, at1..at3, v1..v3) with no documented layout — a format
recovered only by reverse-engineering, and one that would break silently on any
reordering. Driving the interactive menu is the *verified* path: every sequence
in :data:`_SEQUENCES` was run against the real binary and confirmed to exit 0
with the expected cube on disk.

That choice has one sharp edge, and it dictates the safety rails below: when a
fed sequence desynchronizes from the prompts, ``orca_plot`` hits EOF on stdin
and spins forever printing ``Invalid input. Please try again``. Observed
directly — a wrong density sequence burned 3 minutes of CPU and accumulated
2 GB of output in the parent before it was killed. So a bounded read, a hard
timeout, and an explicit desync check are load-bearing here, not hygiene.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .procutil import no_window_flags

# Grid intervals offered to the user. The cube grows with the cube of this
# number (0.9 / 3.1 / 7.3 MB at 40 / 60 / 80 for a 52-atom box), and the whole
# file crosses the QWebChannel into the JS heap, so the ceiling is a payload
# decision as much as a quality one (P48). 60 is the default: visually smooth
# after the viewer's own mesh smoothing, and a fifth of the bytes of 80.
GRID_CHOICES = (40, 60, 80)
DEFAULT_GRID = 60

# Per-kind default grid, for the kinds whose cost is not the others'. An MO is
# 0.17 s and a density 9.9 s at 60³, but the electrostatic potential is a
# Coulomb sum over the whole density at EVERY grid point: measured 49.3 s at
# 40³ on that same 52-atom / 987-basis-function system, which the cubic scaling
# puts at ~2.8 min at 60³ and ~6.6 min at 80³ (P3). The potential is also a
# smooth, long-ranged field, so a coarse grid costs it much less than it costs
# an orbital. 40 is therefore the honest default for ESP — the higher grids stay
# selectable, and the UI says what they cost before the click.
DEFAULT_GRIDS = {"esp": 40}


def default_grid_for(kind: str) -> int:
    """The grid an unconfigured request of this kind should use."""
    return DEFAULT_GRIDS.get(kind, DEFAULT_GRID)

# Refuse to ship anything larger than this to the front-end. Nothing we can
# generate through GRID_CHOICES comes close; this catches a stale hand-made cube
# sitting in the folder, which would otherwise freeze the UI on load.
MAX_CUBE_BYTES = 12_000_000

# orca_plot is silent for the whole of a long density plot, so there is no
# progress to read — only a ceiling. Generous: the user has a Cancel button.
DEFAULT_TIMEOUT_S = 1800

# Cap on captured stdout. Success needs one marker line; the rest is menu echo.
# A desynced run prints without end, so this bound is what keeps a mistake from
# becoming a memory leak.
_OUTPUT_CAP_BYTES = 200_000

_FINISHED = "*** PLOTTING FINISHED ***"
_DESYNC = "Invalid input. Please try again"
# The ESP path's own failure: orca_plot refused the density name we typed. It
# then falls into the desync loop, so this must be checked FIRST — otherwise the
# generic "this ORCA build's menu differs" sentence would blame the wrong thing.
_BAD_DENSITY = "Wrong Density Name selected"
_BAD_MO = "Invalid MO requested for plot"

PLOT_KINDS = ("mo", "eldens", "spindens", "esp")

#: Human labels for the kinds, for log lines and viewer titles.
KIND_LABELS = {"mo": "Molecular orbital",
               "eldens": "Electron density",
               "spindens": "Spin density",
               "esp": "Electrostatic potential"}

#: The SCF density orca_plot's ESP prompt is answered with. It is the density
#: the menu itself lists as available for "(scf) electron density" (``scfp``),
#: so a run that can plot a density can plot its potential.
_SCF_DENSITY_SUFFIX = ".scfp"


@dataclass
class CubeRequest:
    """What to plot. ``index``/``operator`` apply to ``kind == "mo"`` only
    (operator 0 = alpha or closed-shell, 1 = beta)."""
    kind: str = "mo"
    index: int = 0
    operator: int = 0
    grid: int = DEFAULT_GRID

    def normalized(self) -> "CubeRequest":
        """Clamp to what the menu sequences actually accept. This is the trust
        boundary — the request arrives as JSON from the front-end (P34)."""
        kind = self.kind if self.kind in PLOT_KINDS else "mo"
        grid = min(GRID_CHOICES,
                   key=lambda g: abs(g - int(self.grid or default_grid_for(kind))))
        return CubeRequest(kind=kind, index=max(0, int(self.index or 0)),
                           operator=1 if int(self.operator or 0) == 1 else 0,
                           grid=grid)


def plot_output_name(base: str, req: CubeRequest) -> str:
    """The file ``orca_plot`` writes for this request, next to the ``.gbw``.
    Verified against ORCA 6.1.1: ``water.mo4a.cube`` / ``wcat.mo3b.cube`` /
    ``water.eldens.cube`` / ``wcat.spindens.cube``.

    Note what is *absent*: the grid. orca_plot names a plot by what it is, not by
    how finely it was sampled, so two resolutions of one orbital collide here —
    which is why the stored name below adds it.

    ESP is the odd one out: orca_plot names it after the DENSITY it was computed
    from, not after the plot type, so the file is ``water.scfp.esp.cube`` rather
    than ``water.esp.cube``. Verified against ORCA 6.1.1."""
    if req.kind == "mo":
        return f"{base}.mo{req.index}{'b' if req.operator else 'a'}.cube"
    if req.kind == "esp":
        return f"{base}{_SCF_DENSITY_SUFFIX}.esp.cube"
    return f"{base}.{req.kind}.cube"


def cube_filename(base: str, req: CubeRequest) -> str:
    """The name a generated cube is *kept* under in ``cubes/``.

    Grid-qualified (``water.mo4a.g60.cube``) because the cache key has to be the
    whole request. orca_plot's own name is not: ask for the same orbital at 80³
    after viewing it at 60³ and the reuse check would hand back the coarse file
    while the UI labelled it 80³ — a caption that quietly lies about what is on
    screen. Observed before this split existed."""
    stem = plot_output_name(base, req)[:-len(".cube")]
    return f"{stem}.g{req.grid}.cube"


def _menu_sequence(req: CubeRequest, base: str = "") -> str:
    """The keystrokes that drive ``orca_plot``'s interactive menu.

    Common prefix selects the output format and the grid:
      ``5`` → output format menu, ``7`` → 3D Gaussian cube; ``4`` → grid
      intervals, then the number (one value sets all three dimensions).
    Then per kind, via ``1`` (type of plot):
      MO       ``1`` → molecular orbitals, then ``3`` operator, ``2`` MO number.
      density  ``2``/``3`` → (scf) electron / spin density, then ``y`` to accept
               the default density name the program offers (``<base>.scfp`` /
               ``.scfr``) — this prompt is the one that has no analogue in the MO
               path, and omitting it is exactly what desynchronizes the run.
      esp      ``43`` → Electrostatic Potential, then the density name **typed
               in full** (``<base>.scfp``). Note it is neither of the other two
               shapes: the density prompt here is not a y/n confirmation, and an
               empty line answers it with "Wrong Density Name selected" and drops
               straight into the endless ``Invalid input`` loop the module
               docstring describes. Observed while adding this kind.
    Finally ``11`` generates the plot and ``12`` exits.
    """
    head = f"5\n7\n4\n{req.grid}\n"
    if req.kind == "mo":
        body = f"1\n1\n3\n{req.operator}\n2\n{req.index}\n"
    elif req.kind == "esp":
        body = f"1\n43\n{base}{_SCF_DENSITY_SUFFIX}\n"
    else:
        body = f"1\n{2 if req.kind == 'eldens' else 3}\ny\n"
    return head + body + "11\n12\n"


def _failure_sentence(out: str) -> str:
    """Turn orca_plot's output into one actionable sentence (P28).

    Its fatal errors arrive wrapped in banner decoration —
    ``!!!  CANNOT OPEN FILE  !!!`` / ``!!!  Filename: x.densitiesinfo  !!!`` —
    which carries no information and drowns the part that does, so the rules
    below strip the frame and keep the words.

    The density sidecars get their own sentence because that failure is both
    the most likely one and the least self-explanatory: ORCA 6 keeps densities
    in ``.densities`` / ``.densitiesinfo`` beside the ``.gbw``, and orca_plot
    reads them *even for an MO plot*. A run folder with the ``.gbw`` alone —
    hand-copied, or pruned to save space — fails here and nowhere else.
    """
    lines = []
    for raw in out.splitlines():
        ln = raw.strip().strip("!").strip()
        if ln and not set(ln) <= set("!-=* "):
            lines.append(ln)
    tail = lines[-2:]
    if any(".densities" in ln for ln in tail):
        return ("orca_plot could not read this calculation's density files "
                "(.densities / .densitiesinfo). ORCA writes them next to the "
                ".gbw and needs them even to plot an orbital — if the run "
                "folder was copied, copy those files too.")
    joined = " — ".join(tail)
    return "orca_plot produced no cube file" + (f": {joined}" if joined else ".")


def orca_plot_exe(orca_path: str | Path) -> Optional[Path]:
    """``orca_plot`` next to the configured ``orca`` executable, or None."""
    from ..config import orca_tool
    return orca_tool(orca_path, "orca_plot")


def _run_bounded(argv: list[str], stdin_text: str, cwd: Path,
                 timeout: float) -> tuple[str, bool]:
    """Run ``argv``, feed ``stdin_text``, and return ``(captured, timed_out)``.

    Drains the child's stdout continuously but *stores* only the first
    ``_OUTPUT_CAP_BYTES`` — a desynced orca_plot prints an unbounded stream, and
    both halves matter: stop storing so memory stays flat, keep draining so the
    child never blocks on a full pipe and the timeout can do its job.
    """
    proc = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, cwd=str(cwd),
        creationflags=no_window_flags())
    chunks: list[str] = []
    stored = 0

    def _drain() -> None:
        nonlocal stored
        assert proc.stdout is not None
        for line in proc.stdout:
            if stored < _OUTPUT_CAP_BYTES:
                chunks.append(line)
                stored += len(line)
    reader = threading.Thread(target=_drain, name="orca-plot-read", daemon=True)
    reader.start()
    try:
        if proc.stdin is not None:
            proc.stdin.write(stdin_text)
            proc.stdin.flush()
            proc.stdin.close()
    except OSError:
        pass        # the child died early; the wait below reports it
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    reader.join(timeout=5)
    return "".join(chunks), timed_out


def generate_cube(orca_path: str | Path, run_dir: str | Path, base: str,
                  req: CubeRequest, *, dest_dir: str | Path | None = None,
                  timeout: float = DEFAULT_TIMEOUT_S,
                  reuse: bool = True,
                  on_log: Optional[Callable[[str], None]] = None) -> dict:
    """Produce one cube for ``{run_dir}/{base}.gbw`` and move it under
    ``dest_dir`` (default ``{run_dir}/cubes``).

    Returns ``{ok, path, cached, seconds}`` or ``{ok: False, error}``. Errors are
    values, never exceptions (P6) — the caller is a background thread whose only
    channel back to the UI is a status dict.
    """
    req = req.normalized()
    run_dir = Path(run_dir)
    dest = Path(dest_dir) if dest_dir else (run_dir / "cubes")
    produced_name = plot_output_name(base, req)   # what orca_plot writes
    final = dest / cube_filename(base, req)       # what we keep it as

    if reuse and final.exists() and final.stat().st_size > 0:
        # Cubes are deterministic in their inputs, and a regenerated one would be
        # byte-identical; reopening a previously viewed orbital should be instant.
        return {"ok": True, "path": str(final), "cached": True, "seconds": 0.0}

    gbw = run_dir / f"{base}.gbw"
    if not gbw.exists():
        return {"ok": False, "error": f"No wavefunction file ({base}.gbw) in the "
                                      f"run folder — orbitals need a finished ORCA job."}
    exe = orca_plot_exe(orca_path)
    if exe is None:
        return {"ok": False, "error": "orca_plot was not found next to the configured "
                                      "ORCA executable. Check the ORCA path in Settings."}

    label = KIND_LABELS.get(req.kind, req.kind)
    what = f"{label} {req.index}{'β' if req.operator else 'α'}" if req.kind == "mo" else label
    if on_log:
        on_log(f"orca_plot: {what} at grid {req.grid}³ …")

    started = time.time()
    out, timed_out = _run_bounded([str(exe), gbw.name, "-i"],
                                  _menu_sequence(req, base), run_dir, timeout)
    elapsed = time.time() - started

    if timed_out:
        return {"ok": False, "error": f"orca_plot did not finish within "
                                      f"{int(timeout)} s and was stopped."}
    if _BAD_DENSITY in out:
        return {"ok": False,
                "error": f"orca_plot has no density named {base}{_SCF_DENSITY_SUFFIX} "
                         f"for this wavefunction, so the electrostatic potential "
                         f"cannot be computed from it. A finished SCF writes one; "
                         f"a restarted or converted .gbw may not."}
    if _DESYNC in out:
        # The menu numbering is printed with a computed index, so a different
        # ORCA build could renumber it. Say that plainly instead of leaving a
        # half-made cube behind (P2, P28).
        return {"ok": False, "error": "orca_plot rejected the command sequence — this "
                                      "ORCA build's plot menu differs from the one "
                                      "ORCAdesk drives. Please report the ORCA version."}
    if _BAD_MO in out:
        return {"ok": False, "error": f"Orbital {req.index} is out of range for this "
                                      f"wavefunction."}

    produced = run_dir / produced_name
    if not produced.exists():
        return {"ok": False, "error": _failure_sentence(out)}
    if _FINISHED not in out:
        return {"ok": False, "error": "orca_plot exited before finishing the plot."}

    try:
        dest.mkdir(parents=True, exist_ok=True)
        # Keep cubes out of the run folder proper: they are derived artifacts and
        # there can be dozens of them, while the run folder is the user's result.
        shutil.move(str(produced), str(final))
    except OSError as e:
        return {"ok": False, "error": f"Could not store the cube: {e}"}

    if on_log:
        on_log(f"orca_plot: wrote {final.name} "
               f"({final.stat().st_size / 1e6:.1f} MB) in {elapsed:.1f} s")
    return {"ok": True, "path": str(final), "cached": False, "seconds": elapsed}
