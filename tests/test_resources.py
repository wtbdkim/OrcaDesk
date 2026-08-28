"""What a calculation costs, and what the budget resolves to.

`core/resources.py` is what admission control reads: how many cores a
calculation will actually occupy and roughly how much memory. The subtlety is
that the answer must come from what will really execute — a raw `.inp` owns its
own `%pal`/`%maxcore`, and CREST/MLIP declare their threads elsewhere — because
ORCAdesk never rewrites a calculation to fit the budget.
"""
from __future__ import annotations

from orcamgr.core.input_generator import StepConfig
from orcamgr.core.queue import Calculation
from orcamgr.core.resources import (
    ResourceBudget, auto_cores, auto_ram_mb, declared_cores, estimated_ram_mb,
    raw_maxcore_mb, raw_nprocs, worker_threads,
)


def _calc(kind: str = "sp", **cfg) -> Calculation:
    return Calculation(name="c", kind=kind, config=StepConfig(kind=kind, **cfg))


# ---- reading a hand-written .inp --------------------------------------------

def test_raw_nprocs_reads_the_pal_block():
    assert raw_nprocs("%pal nprocs 8 end") == 8
    assert raw_nprocs("%pal\n  nprocs 12\nend\n") == 12


def test_raw_nprocs_reads_the_pal_keyword():
    assert raw_nprocs("! wB97X-D4 def2-TZVP PAL4") == 4


def test_raw_nprocs_is_zero_when_nothing_is_declared():
    assert raw_nprocs("! wB97X-D4 def2-TZVP") == 0
    assert raw_nprocs("") == 0


def test_raw_maxcore_is_read_and_optional():
    assert raw_maxcore_mb("%maxcore 4000\n! B3LYP") == 4000
    assert raw_maxcore_mb("! B3LYP") == 0


# ---- what a calculation costs ------------------------------------------------

def test_form_calc_declares_its_nprocs():
    assert declared_cores(_calc(nprocs=6)) == 6


def test_raw_calc_declares_the_cores_its_own_text_asks_for():
    # The hidden form field is NOT what runs — the pasted text is.
    calc = _calc(nprocs=6)
    calc.is_raw = True
    calc.raw_text = "%pal nprocs 2 end\n! B3LYP\n"
    assert declared_cores(calc) == 2


def test_raw_calc_without_pal_is_serial():
    calc = _calc(nprocs=6)
    calc.is_raw = True
    calc.raw_text = "! B3LYP def2-SVP\n"
    assert declared_cores(calc) == 1


def test_crest_declares_its_thread_count():
    assert declared_cores(_calc("crest_conf", crest_threads=12)) == 12


def test_a_cuda_mlip_job_barely_touches_the_cpu():
    assert declared_cores(_calc("mlip_opt", nprocs=8, mlip_device="cuda")) == 1
    assert declared_cores(_calc("mlip_opt", nprocs=8, mlip_device="")) == 8


def test_cores_are_never_zero():
    assert declared_cores(_calc(nprocs=0)) == 1


def test_orca_memory_is_maxcore_times_cores():
    # %maxcore is PER CORE: this is exactly the trap the RAM budget guards.
    assert estimated_ram_mb(_calc(nprocs=6, maxcore_mb=2400)) == 14_400


def test_raw_memory_comes_from_the_raw_text():
    calc = _calc(nprocs=6, maxcore_mb=2400)
    calc.is_raw = True
    calc.raw_text = "%pal nprocs 2 end\n%maxcore 1000\n! B3LYP\n"
    assert estimated_ram_mb(calc) == 2000


def test_non_orca_backends_get_a_flat_estimate():
    # Neither declares a memory ceiling; the estimate only has to stop them
    # piling up unbounded.
    assert estimated_ram_mb(_calc("mlip_opt")) > 0
    assert estimated_ram_mb(_calc("crest_conf")) > 0


# ---- the budget ---------------------------------------------------------------

def test_auto_budget_resolves_against_this_machine():
    b = ResourceBudget().resolved()
    assert b.max_jobs == 1                      # one at a time unless raised
    assert b.cores == auto_cores() >= 1
    assert b.ram_mb == auto_ram_mb() >= 1024


def test_explicit_budget_is_kept_as_given():
    b = ResourceBudget(max_jobs=3, cores=12, ram_mb=8000).resolved()
    assert (b.max_jobs, b.cores, b.ram_mb) == (3, 12, 8000)


def test_zero_means_auto_per_field():
    b = ResourceBudget(max_jobs=2, cores=0, ram_mb=4096).resolved()
    assert b.cores == auto_cores()
    assert b.ram_mb == 4096


# ---- the thread cap is not the admission cost -------------------------------

def test_worker_threads_is_not_the_cuda_accounting_value():
    # A CUDA job is CHARGED one core (its CPU use is marginal), but it still
    # runs ASE/numpy work between GPU calls -- capping its threads at that one
    # core would serialize a finite-difference frequency run.
    gpu = _calc("mlip_freq", nprocs=8, mlip_device="cuda")
    assert declared_cores(gpu) == 1          # what the budget is charged
    assert worker_threads(gpu) == 8          # what the worker may use


def test_worker_threads_follows_nprocs_on_cpu():
    assert worker_threads(_calc("mlip_opt", nprocs=4, mlip_device="cpu")) == 4
    assert worker_threads(_calc("mlip_opt", nprocs=0)) == 1


# ---- raw memory never falls back to the hidden form -------------------------

def test_raw_without_maxcore_uses_orcas_default_not_the_form():
    # declared_cores already refuses to trust the hidden form for a raw calc;
    # the memory half must agree, or the two disagree about what runs.
    calc = _calc(nprocs=6, maxcore_mb=8000)
    calc.is_raw = True
    calc.raw_text = "! B3LYP def2-SVP\n"        # no %pal, no %maxcore
    assert declared_cores(calc) == 1
    assert estimated_ram_mb(calc) == 1024       # ORCA's own default, x 1 core


def test_budget_from_settings_reads_the_three_knobs():
    class _S:
        max_concurrent_jobs = 3
        max_total_cores = 12
        max_total_ram_mb = 8000

    b = ResourceBudget.from_settings(_S())
    assert (b.max_jobs, b.cores, b.ram_mb) == (3, 12, 8000)


def test_zero_jobs_means_as_many_as_the_budget_allows():
    # The usual way to think about it: cap the machine, not the job count.
    # Every job takes at least one core, so the core budget is already the
    # ceiling -- 0 just stops the job count being a second number to keep in
    # sync with it.
    b = ResourceBudget(max_jobs=0, cores=12, ram_mb=8000).resolved()
    assert b.max_jobs == 12
    # and with auto cores too
    b2 = ResourceBudget(max_jobs=0).resolved()
    assert b2.max_jobs == auto_cores()
    # an explicit cap still wins
    assert ResourceBudget(max_jobs=2, cores=12).resolved().max_jobs == 2
