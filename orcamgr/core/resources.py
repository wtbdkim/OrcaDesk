"""CPU/RAM accounting for a parallel queue run.

The engine may run several calculations at once (see `QueueEngine._run_walk`).
Admission is by *budget*, not by a lane count: every calculation declares how
many cores it will use (ORCA `%pal nprocs`, CREST `-T`, the MLIP worker's thread
cap), and a job starts only while the declared cores and the estimated memory
still fit inside the user's budget. So "two 8-core jobs" and "four 4-core jobs"
are the same setting — the per-calculation `nprocs` decides the shape, and
ORCAdesk never rewrites it (a raw `.inp` owns its own `%pal`, and raw text is
never edited on the user's behalf — P26).

Cores are *declared* and therefore exact; memory is *estimated* (ORCA's
`%maxcore` is a per-core ceiling, not a measurement, and MLIP/CREST do not
declare one at all), so the RAM budget is a guard rail against obvious
oversubscription, not an accounting system.
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

# Memory estimates for the backends that declare nothing (MB per job). Both are
# deliberately rough and generous-but-not-absurd: they exist so a queue of MLIP
# or CREST jobs cannot pile up unbounded, not to predict real usage.
_MLIP_RAM_MB = 3000
_CREST_RAM_MB = 2000

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


@dataclass
class ResourceBudget:
    """The admission limits for one run.

    max_jobs: how many calculations may be in flight at once (1 = the classic
        one-at-a-time queue). A hard cap that also covers the non-CPU reasons to
        not run everything at once (disk contention, a single GPU).
    cores / ram_mb: 0 means "auto" — resolved from the machine at run start.
    """
    max_jobs: int = 1
    cores: int = 0
    ram_mb: int = 0

    def resolved(self) -> "ResourceBudget":
        """A copy with the auto (0) fields filled in from this machine."""
        return ResourceBudget(
            max_jobs=max(1, int(self.max_jobs or 1)),
            cores=int(self.cores) if self.cores and self.cores > 0 else auto_cores(),
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


def estimated_ram_mb(calc) -> int:
    """Rough memory footprint of this calculation, in MB. For ORCA this is
    `%maxcore` x cores — ORCA's own per-core ceiling, which is what the run will
    try to use; the other backends get a flat per-job estimate."""
    kind = getattr(calc, "kind", "") or ""
    if kind.startswith("mlip"):
        return _MLIP_RAM_MB
    if kind.startswith("crest"):
        return _CREST_RAM_MB
    cfg = getattr(calc, "config", None)
    cores = declared_cores(calc)
    if getattr(calc, "is_raw", False):
        per_core = raw_maxcore_mb(getattr(calc, "raw_text", "") or "")
    else:
        per_core = int(getattr(cfg, "maxcore_mb", 0) or 0)
    if per_core <= 0:
        per_core = int(getattr(cfg, "maxcore_mb", 0) or 0) or 2400
    return max(1, per_core * cores)
