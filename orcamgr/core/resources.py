"""CPU/RAM accounting for a parallel queue run.

The engine may run several calculations at once (see `QueueEngine._run_walk`).
Admission is by *budget*, not by a lane count: every calculation declares how
many cores it will use (ORCA `%pal nprocs`, CREST `-T`, the MLIP worker's thread
cap), and a job starts only while the declared cores and the estimated memory
still fit inside the user's budget. So "two 8-core jobs" and "four 4-core jobs"
are the same setting — the per-calculation `nprocs` decides the shape, and
ORCAdesk never rewrites it (a raw `.inp` owns its own `%pal`, and raw text is
never edited on the user's behalf — P26).

Cores are *declared* and therefore exact; memory is **estimated**, and the
estimate is wrong in both directions:

* ORCA's `%maxcore` is a per-core *guideline it may exceed*, not a cap, so a
  large job can use more than it is charged;
* CREST is charged far more than it takes — a GFN2 search on a small molecule
  peaks around 20 MB across all its xtb workers (measured: ethanol, `-T 2` and
  `-T 8`, `--quick`), because xtb's memory grows with system size and stays
  modest.

So the memory budget alone cannot promise anything, and a mixed queue is exactly
where it is weakest. `free_ram_mb()` is the second line of defence: before a
*second* job is admitted, the machine is asked how much memory it actually has
left. That catches an under-estimate from any backend, and it never blocks the
first job, so the queue can always make progress.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is a hard dependency in practice
    psutil = None

# Fallbacks used when psutil can't answer (it is a hard dependency of the app,
# so these only matter in odd environments).
_FALLBACK_CORES = 4
_FALLBACK_RAM_MB = 8192

# Share of total system RAM the auto budget offers to the queue. The rest is
# left to the OS, the app itself, and whatever else the user is running.
_AUTO_RAM_SHARE = 0.75

# ORCA's own default %maxcore (MB per core) — what a raw input that declares
# none will actually use. The form path always writes an explicit %maxcore.
_ORCA_DEFAULT_MAXCORE_MB = 1024

# Memory estimates for the backends that declare nothing. They exist so a queue
# of MLIP or CREST jobs cannot pile up unbounded, not to predict real usage.
#
# MLIP: torch plus a MACE model, per job. Roughly right for the CPU models and
# on the generous side for the small ones.
_MLIP_RAM_MB = 3000
# CREST: measured at ~20 MB total for a 9-atom search at -T 2 and -T 8, so the
# real figure is tiny; xtb's memory grows with system size rather than thread
# count. This scales gently with the thread count anyway (each -T is another
# concurrent xtb worker) and keeps a floor, so a large host-guest system is not
# wildly under-charged while a small search stops eating 2 GB of budget it never
# touches.
_CREST_RAM_PER_THREAD_MB = 128
_CREST_RAM_FLOOR_MB = 256

# Memory left untouched when deciding whether the machine can take another job:
# the OS, ORCAdesk itself, and whatever else the user is running.
_RAM_RESERVE_MB = 2048

# `%pal nprocs 8 end`, or the `nprocs 8` line inside a multi-line %pal block.
# The "=" is not decoration: ORCA 6.1.1 accepts `nprocs=2` and `nprocs = 2` and
# runs both on 2 MPI ranks (verified), and requiring whitespace read them as
# "declares nothing" — so a parallel raw job was charged one core and the
# dispatcher could admit several of them at once (P57).
_PAL_NPROCS_RE = re.compile(r"^\s*(?:%pal\s+)?nprocs\s*=?\s*(\d+)",
                            re.IGNORECASE | re.MULTILINE)
# The `PAL8` / `! ... PAL8` simple-input keyword (ORCA accepts PAL2..PAL8).
_PAL_KEYWORD_RE = re.compile(r"(?:^|\s)PAL(\d+)(?:\s|$)", re.IGNORECASE | re.MULTILINE)
# `%maxcore 4000` (same "=" spelling as %pal)
_MAXCORE_RE = re.compile(r"^\s*%maxcore\s*=?\s*(\d+)", re.IGNORECASE | re.MULTILINE)


def auto_cores() -> int:
    """Physical cores — the honest default budget. Hyperthreads are excluded
    deliberately: ORCA's compute kernels do not gain from them, and counting
    them would let the queue oversubscribe by 2x on a machine that reports 32
    logical / 16 physical."""
    if psutil is not None:
        n = psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True)
        if n:
            return int(n)
    return _FALLBACK_CORES


def auto_ram_mb() -> int:
    """Three quarters of installed RAM, in MB."""
    if psutil is not None:
        try:
            return max(1024, int(psutil.virtual_memory().total / (1024 * 1024)
                                 * _AUTO_RAM_SHARE))
        except Exception:
            pass
    return _FALLBACK_RAM_MB


def free_ram_mb() -> int:
    """Memory the machine actually has available right now, in MB. 0 when it
    cannot be determined — callers treat that as "don't second-guess the
    estimate"."""
    if psutil is None:
        return 0
    try:
        return int(psutil.virtual_memory().available / (1024 * 1024))
    except Exception:
        return 0


def ram_headroom_mb() -> int:
    """How much of the free memory a new job may take, after leaving the OS and
    everything else their reserve. 0 when free memory is unknown."""
    free = free_ram_mb()
    return max(0, free - _RAM_RESERVE_MB) if free else 0


@dataclass
class ResourceBudget:
    """The admission limits for one run.

    max_jobs: how many calculations may be in flight at once. 1 = the classic
        one-at-a-time queue; **0 = as many as the core/RAM budget allows**, which
        is the usual way to think about it — you cap the machine, not the job
        count. A non-zero value is the extra cap for the non-CPU reasons to not
        run everything at once (disk contention, a single GPU).
    cores / ram_mb: 0 means "auto" — resolved from the machine at run start.
    auto_jobs: max_jobs was left at 0 ("as many as fit"). resolved() fills
        max_jobs in, so this is what still says so afterwards -- it decides
        how {{NPROCS}} is sized (pool_shape).
    """
    max_jobs: int = 1
    cores: int = 0
    ram_mb: int = 0
    auto_jobs: bool = False

    @classmethod
    def from_settings(cls, settings) -> "ResourceBudget":
        """The budget a run gets from the user's settings. Both run entry points
        (the desktop bridge and the phone's /api/run) build it here, so the two
        cannot drift apart (P4)."""
        return cls(max_jobs=getattr(settings, "max_concurrent_jobs", 1),
                   cores=getattr(settings, "max_total_cores", 0),
                   ram_mb=getattr(settings, "max_total_ram_mb", 0))

    def resolved(self) -> "ResourceBudget":
        """A copy with the auto (0) fields filled in from this machine."""
        cores = int(self.cores) if self.cores and self.cores > 0 else auto_cores()
        jobs = int(self.max_jobs or 0)
        auto = bool(self.auto_jobs) or jobs <= 0
        # 0 = "as many as fit": every job takes at least one core, so the core
        # budget is already the ceiling — this just stops the job count from
        # being a second thing to keep in sync with it.
        if jobs <= 0:
            jobs = max(1, cores)
        return ResourceBudget(
            max_jobs=jobs,
            cores=cores,
            ram_mb=int(self.ram_mb) if self.ram_mb and self.ram_mb > 0 else auto_ram_mb(),
            auto_jobs=auto,
        )


def _without_comments(text: str) -> str:
    """``text`` with ORCA's ``#`` comments blanked out (length preserved, so
    match positions still order correctly).

    ORCA ignores everything after a ``#``, and the core accounting has to agree
    with it: "# PAL8" left in a raw input as a note charged the job eight cores
    it never used, and that budget is denied to jobs that would.
    """
    out = []
    for line in (text or "").split("\n"):
        i = line.find("#")
        out.append(line if i < 0 else line[:i] + " " * (len(line) - i))
    return "\n".join(out)


def raw_nprocs(raw_text: str) -> int:
    """The core count a hand-written `.inp` declares (`%pal nprocs N` or `PALn`),
    or 0 when it declares none (ORCA then runs serial).

    The LAST declaration wins, whichever form it is in — that is what ORCA does,
    measured on 6.1.1: `! ... PAL2` followed by `%pal nprocs 1 end` runs serial,
    the same two swapped runs on 2 ranks, and `%pal nprocs 2 end` followed by
    `%pal nprocs 4 end` runs on 4. Taking the first match, and preferring the
    block form regardless of order, charged the wrong number in both directions;
    under-charging is the one that matters, because it lets the dispatcher
    admit jobs the machine cannot actually run at once (P57).
    """
    text = _without_comments(raw_text or "")
    last = 0
    pos = -1
    for pattern in (_PAL_NPROCS_RE, _PAL_KEYWORD_RE):
        for m in pattern.finditer(text):
            if m.start() > pos:
                try:
                    last, pos = int(m.group(1)), m.start()
                except (TypeError, ValueError):
                    pass
    return last


def raw_maxcore_mb(raw_text: str) -> int:
    """The `%maxcore` a hand-written `.inp` declares, or 0. Last one wins, as
    with %pal."""
    matches = list(_MAXCORE_RE.finditer(_without_comments(raw_text or "")))
    if not matches:
        return 0
    try:
        return int(matches[-1].group(1))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# {{NPROCS}} / {{MAXCORE}}: a raw input that leaves its size to the device
# ---------------------------------------------------------------------------
# A number in a raw `.inp` is the user's and runs as written (P26). These two
# placeholders are how a text asks for the opposite: "size me to wherever I
# run". They are filled at launch from the pool the job was admitted to --
# this PC or one device -- out of that pool's own three settings (the user's
# decision, 2026-11-24):
#
#   nprocs  = cores cap / calculations at once   (at least 1)
#   maxcore = memory cap / cores cap             (MB per core)
#
# so N jobs at once use exactly the cores cap and add up to exactly the
# memory cap. No share is held back: the memory cap is the user's own number
# and already carries the margin they want (the "0 = auto" cap is 75% of the
# machine).
#
# Calculations at once left at 0 ("as many as fit") has no N to divide by, and
# there nprocs is every core of the cap still FREE when the job starts (the
# user's decision, 2026-11-24): a job alone takes the whole cap, one started
# beside an 8-core job takes the rest. ORCA cannot grow or shrink a run once
# it has started, so a job that finds nothing free waits for one to finish.
# maxcore stays memory cap / cores cap, so the memory follows the cores.
NPROCS_PLACEHOLDER = "{{NPROCS}}"
MAXCORE_PLACEHOLDER = "{{MAXCORE}}"


@dataclass(frozen=True)
class JobShape:
    """What {{NPROCS}} / {{MAXCORE}} become in one pool (pool_shape)."""
    nprocs: int = 1
    maxcore_mb: int = _ORCA_DEFAULT_MAXCORE_MB


def pool_shape(budget: "ResourceBudget", mpi: bool = True,
               cores_in_use: int = 0) -> JobShape:
    """The job shape a pool's budget gives. ``cores_in_use`` is what the jobs
    already running in the pool hold; it matters only when calculations at once
    is auto (every free core). ``mpi`` False (a device without mpirun) keeps
    nprocs at 1: a parallel ORCA cannot start there."""
    b = budget.resolved()
    cores = max(1, int(b.cores))
    if not mpi:
        nprocs = 1
    elif b.auto_jobs:
        nprocs = max(1, cores - max(0, int(cores_in_use)))
    else:
        nprocs = max(1, cores // max(1, int(b.max_jobs)))
    return JobShape(nprocs=nprocs, maxcore_mb=max(1, int(b.ram_mb) // cores))


def sized_by_pool(raw_text: str) -> "tuple[bool, bool]":
    """(nprocs, maxcore): which of the two placeholders a raw text uses
    outside its comments."""
    text = _without_comments(raw_text or "")
    return NPROCS_PLACEHOLDER in text, MAXCORE_PLACEHOLDER in text


def fill_pool_placeholders(text: str, shape: JobShape, n_atoms: int = 0) -> str:
    """``text`` with {{NPROCS}} / {{MAXCORE}} filled from ``shape``. A text
    without them is returned unchanged. The nprocs written is capped for a
    numerical frequency (numfreq_rank_cap): here ORCAdesk picks the number,
    so it picks one that cannot hang, where a typed one is only warned about."""
    if NPROCS_PLACEHOLDER not in text and MAXCORE_PLACEHOLDER not in text:
        return text
    nprocs = shape.nprocs
    cap = numfreq_rank_cap(text, n_atoms, nprocs) if n_atoms else None
    if cap:
        nprocs = cap
    return (text.replace(NPROCS_PLACEHOLDER, str(max(1, nprocs)))
                .replace(MAXCORE_PLACEHOLDER, str(max(1, shape.maxcore_mb))))


# ---------------------------------------------------------------------------
# numerical frequencies: more ranks than displacements is a deadlock
# ---------------------------------------------------------------------------
# ORCA parallelises a NumFreq over its displaced geometries, one gradient per
# rank. Ask for more ranks than there are displacements and the run finishes
# every gradient and then hangs forever in the collect step: measured on ORCA
# 6.1.1 (Windows, MS-MPI), CO2 with `%pal nprocs 15` wrote all 12 displacement
# gradients in 20 seconds and then spun at 100% of one core with no MPI
# children left and no further output. The same input at `nprocs 12` finished
# in 38 seconds. Nothing is lost by capping: the surplus ranks had no
# displacement to work on in the first place, so the wall-clock is identical.

#: `NumFreq` as a simple-input keyword, and the `%irc InitHess calc_numfreq`
#: spelling — both run the displacement machinery. Analytic `Freq` is not one,
#: and the `(?:calc_)?` prefix is what keeps `Freq` from matching here at all.
_NUMFREQ_RE = re.compile(r"(?<![A-Za-z0-9_])(?:calc_)?numfreq(?![A-Za-z0-9_])",
                         re.IGNORECASE)


def declares_numerical_freq(text: str) -> bool:
    """Does this `.inp` text ask for a NUMERICAL frequency run?

    Comments are stripped first, so a `# NumFreq` note left in a raw input does
    not count — the same rule `raw_nprocs` applies to `# PAL8`.
    """
    return bool(_NUMFREQ_RE.search(_without_comments(text or "")))


def numfreq_displacements(n_atoms: int) -> int:
    """How many displaced-geometry gradients a NumFreq of ``n_atoms`` runs.

    Central differences over every Cartesian coordinate (6N), less the 6 ORCA
    takes back from translation invariance — the "Number of displacements
    ... 18 - 6" a 3-atom .out prints. Those are ORCA 6's defaults, which are
    what ORCAdesk writes; an input that turns translation invariance off runs
    6 more, so this count is the conservative one (capping to it can only ever
    leave a rank idle, never deadlock).
    """
    return max(0, 6 * int(n_atoms) - 6)


def numfreq_rank_cap(text: str, n_atoms: int, nprocs: int):
    """The `%pal nprocs` a numerical-frequency run must not exceed, or None
    when this request is already safe (or is not a NumFreq at all).

    A judgment, not an edit: the callers decide what to do with it — the input
    generator caps the number it is about to write and says so in the file, the
    queue engine only warns about a raw `.inp`, because raw text is never
    rewritten on the user's behalf (P26).
    """
    if not declares_numerical_freq(text):
        return None
    try:
        n_atoms, nprocs = int(n_atoms), int(nprocs)
    except (TypeError, ValueError):
        return None
    if n_atoms < 1 or nprocs < 1:
        return None
    displacements = numfreq_displacements(n_atoms)
    if displacements < 1 or nprocs <= displacements:
        return None
    return displacements


def declared_cores(calc, shape: "JobShape | None" = None) -> int:
    """How many cores this calculation will occupy while it runs.

    Read from what will actually execute: a raw `.inp`'s own `%pal`, CREST's
    `-T`, the form's `nprocs`. Never less than 1, so a job always consumes a
    slot's worth of budget. A raw `.inp` with {{NPROCS}} occupies the
    ``shape`` of the pool it would run in (1 when none is given).
    """
    cfg = getattr(calc, "config", None)
    kind = getattr(calc, "kind", "") or ""
    if kind.startswith("crest"):
        return max(1, int(getattr(cfg, "crest_threads", 1) or 1))
    if kind.startswith("mlip"):
        # A CUDA job's CPU use is marginal (the model runs on the GPU); a CPU job
        # is capped to nprocs by the worker (mlip/runner.py sets the torch thread
        # count), so nprocs is the honest number in both cases.
        if (getattr(cfg, "mlip_device", "") or "").lower() == "cuda":
            return 1
        return max(1, int(getattr(cfg, "nprocs", 1) or 1))
    if getattr(calc, "is_raw", False):
        text = getattr(calc, "raw_text", "") or ""
        if sized_by_pool(text)[0]:
            return shape.nprocs if shape is not None else 1
        n = raw_nprocs(text)
        if n:
            return n
        # A raw input with no %pal runs serial, whatever the (hidden) form says.
        return 1
    return max(1, int(getattr(cfg, "nprocs", 1) or 1))


def uses_gpu(calc) -> bool:
    """True when this calculation will run on the GPU.

    Only an explicit ``cuda`` counts. The empty (auto) device is resolved inside
    the MLIP worker, in the user's own environment — the only place that can ask
    torch whether a GPU exists — so ORCAdesk cannot know here, and guessing
    would either serialize CPU jobs for nothing or claim a lane that is not
    used. A user who wants the GPU lane picks GPU explicitly; the build card
    only offers it when a ready env actually reports CUDA.
    """
    kind = getattr(calc, "kind", "") or ""
    if not kind.startswith("mlip"):
        return False
    cfg = getattr(calc, "config", None)
    return (getattr(cfg, "mlip_device", "") or "").strip().lower() == "cuda"


def worker_threads(calc) -> int:
    """CPU threads the MLIP worker may use — deliberately NOT declared_cores.

    A CUDA job is *charged* one core (its CPU use is marginal next to the GPU),
    but it still runs real ASE/numpy work between GPU calls — a finite-difference
    frequency run especially — so capping it at that one core would serialize it.
    The cap is the declared nprocs either way; only the accounting differs by
    device.
    """
    cfg = getattr(calc, "config", None)
    return max(1, int(getattr(cfg, "nprocs", 1) or 1))


def estimated_ram_mb(calc, shape: "JobShape | None" = None) -> int:
    """Rough memory footprint of this calculation, in MB. For ORCA this is
    `%maxcore` x cores — ORCA's own per-core ceiling, which is what the run will
    try to use; the other backends get a flat per-job estimate. {{NPROCS}} /
    {{MAXCORE}} are read from ``shape``, as declared_cores does."""
    kind = getattr(calc, "kind", "") or ""
    if kind.startswith("mlip"):
        return _MLIP_RAM_MB
    if kind.startswith("crest"):
        threads = declared_cores(calc)
        return max(_CREST_RAM_FLOOR_MB, threads * _CREST_RAM_PER_THREAD_MB)
    cfg = getattr(calc, "config", None)
    cores = declared_cores(calc, shape)
    if getattr(calc, "is_raw", False):
        # Same rule as declared_cores: read what will actually execute. A raw
        # input that declares no %maxcore gets ORCA's default, NOT the hidden
        # form field (which is not what runs).
        text = getattr(calc, "raw_text", "") or ""
        if sized_by_pool(text)[1]:
            per_core = shape.maxcore_mb if shape is not None else _ORCA_DEFAULT_MAXCORE_MB
        else:
            per_core = raw_maxcore_mb(text) or _ORCA_DEFAULT_MAXCORE_MB
    else:
        per_core = int(getattr(cfg, "maxcore_mb", 0) or 0) or _ORCA_DEFAULT_MAXCORE_MB
    return max(1, per_core * cores)
