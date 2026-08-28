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
_PAL_NPROCS_RE = re.compile(r"^\s*(?:%pal\s+)?nprocs\s+(\d+)", re.IGNORECASE | re.MULTILINE)
# The `PAL8` / `! ... PAL8` simple-input keyword (ORCA accepts PAL2..PAL8).
_PAL_KEYWORD_RE = re.compile(r"(?:^|\s)PAL(\d+)(?:\s|$)", re.IGNORECASE | re.MULTILINE)
# `%maxcore 4000`
_MAXCORE_RE = re.compile(r"^\s*%maxcore\s+(\d+)", re.IGNORECASE | re.MULTILINE)


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
    """
    max_jobs: int = 1
    cores: int = 0
    ram_mb: int = 0

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
        # 0 = "as many as fit": every job takes at least one core, so the core
        # budget is already the ceiling — this just stops the job count from
        # being a second thing to keep in sync with it.
        if jobs <= 0:
            jobs = max(1, cores)
        return ResourceBudget(
            max_jobs=jobs,
            cores=cores,
            ram_mb=int(self.ram_mb) if self.ram_mb and self.ram_mb > 0 else auto_ram_mb(),
        )


def _raw_int(pattern: re.Pattern, text: str) -> int:
    m = pattern.search(text or "")
    if not m:
        return 0
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return 0


def raw_nprocs(raw_text: str) -> int:
    """The core count a hand-written `.inp` declares (`%pal nprocs N` or `PALn`),
    or 0 when it declares none (ORCA then runs serial)."""
    n = _raw_int(_PAL_NPROCS_RE, raw_text)
    return n or _raw_int(_PAL_KEYWORD_RE, raw_text)


def raw_maxcore_mb(raw_text: str) -> int:
    """The `%maxcore` a hand-written `.inp` declares, or 0."""
    return _raw_int(_MAXCORE_RE, raw_text)


def declared_cores(calc) -> int:
    """How many cores this calculation will occupy while it runs.

    Read from what will actually execute: a raw `.inp`'s own `%pal`, CREST's
    `-T`, the form's `nprocs`. Never less than 1, so a job always consumes a
    slot's worth of budget.
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
        n = raw_nprocs(getattr(calc, "raw_text", "") or "")
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


def estimated_ram_mb(calc) -> int:
    """Rough memory footprint of this calculation, in MB. For ORCA this is
    `%maxcore` x cores — ORCA's own per-core ceiling, which is what the run will
    try to use; the other backends get a flat per-job estimate."""
    kind = getattr(calc, "kind", "") or ""
    if kind.startswith("mlip"):
        return _MLIP_RAM_MB
    if kind.startswith("crest"):
        threads = declared_cores(calc)
        return max(_CREST_RAM_FLOOR_MB, threads * _CREST_RAM_PER_THREAD_MB)
    cfg = getattr(calc, "config", None)
    cores = declared_cores(calc)
    if getattr(calc, "is_raw", False):
        # Same rule as declared_cores: read what will actually execute. A raw
        # input that declares no %maxcore gets ORCA's default, NOT the hidden
        # form field (which is not what runs).
        per_core = raw_maxcore_mb(getattr(calc, "raw_text", "") or "") or _ORCA_DEFAULT_MAXCORE_MB
    else:
        per_core = int(getattr(cfg, "maxcore_mb", 0) or 0) or _ORCA_DEFAULT_MAXCORE_MB
    return max(1, per_core * cores)
