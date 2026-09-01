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
    ResourceBudget, auto_cores, auto_ram_mb, declared_cores, declares_numerical_freq,
    estimated_ram_mb, free_ram_mb, numfreq_displacements, numfreq_rank_cap,
    ram_headroom_mb, raw_maxcore_mb, raw_nprocs, uses_gpu, worker_threads,
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


def test_only_an_explicit_cuda_device_claims_the_gpu_lane():
    # "" is auto, resolved inside the worker (the only place that can ask torch
    # whether a GPU exists), so ORCAdesk must not guess: claiming a lane that is
    # not used would serialize CPU jobs for nothing.
    assert uses_gpu(_calc("mlip_opt", mlip_device="cuda")) is True
    assert uses_gpu(_calc("mlip_freq", mlip_device="CUDA")) is True
    assert uses_gpu(_calc("mlip_opt", mlip_device="cpu")) is False
    assert uses_gpu(_calc("mlip_opt", mlip_device="")) is False
    assert uses_gpu(_calc("sp")) is False
    assert uses_gpu(_calc("crest_conf")) is False


# ---- memory: the estimate, and the machine's own answer ----------------------

def test_crest_memory_scales_with_its_threads():
    # Measured at ~20 MB total for a 9-atom search (-T 2 and -T 8), so the flat
    # 2 GB it used to be charged was budget it never touched. Each -T is another
    # concurrent xtb worker, so the estimate follows the thread count, with a
    # floor for a larger system.
    small = estimated_ram_mb(_calc("crest_conf", crest_threads=1))
    big = estimated_ram_mb(_calc("crest_conf", crest_threads=16))
    assert small == 256                       # the floor
    assert big == 16 * 128
    assert big > small


def test_free_memory_is_reported_or_honestly_zero():
    free = free_ram_mb()
    assert free >= 0
    if free:
        # headroom keeps a reserve for the OS and everything else
        assert ram_headroom_mb() < free


# ---- numerical frequencies: more ranks than displacements deadlocks ----------

def test_numerical_freq_is_told_apart_from_an_analytic_one():
    # Only the numerical run displaces geometries, so only it has a ceiling.
    assert declares_numerical_freq("! wB97M-V def2-QZVPP TightOpt NumFreq") is True
    assert declares_numerical_freq("! B3LYP def2-SVP Opt Freq") is False
    # the %irc spelling runs the same displacement machinery
    assert declares_numerical_freq("%irc InitHess calc_numfreq end") is True
    assert declares_numerical_freq("%irc InitHess calc_anfreq end") is False
    # a comment is not an instruction — the rule raw_nprocs applies to "# PAL8"
    assert declares_numerical_freq("! B3LYP Opt   # NumFreq next time") is False
    assert declares_numerical_freq("") is False


def test_displacement_count_matches_what_orca_prints():
    # ORCA prints "Number of displacements ... 18 - 6" for a 3-atom molecule:
    # central differences over 3N coordinates, less translation invariance.
    assert numfreq_displacements(3) == 12
    assert numfreq_displacements(24) == 138
    assert numfreq_displacements(1) == 0
    assert numfreq_displacements(0) == 0


def test_rank_cap_fires_only_on_a_numfreq_that_overflows():
    numfreq = "! wB97M-V def2-QZVPP TightOpt NumFreq"
    # CO2 on 15 ranks with 12 displacements: the run that hung for 20 minutes.
    assert numfreq_rank_cap(numfreq, 3, 15) == 12
    # exactly one displacement per rank is the fastest safe shape, not a problem
    assert numfreq_rank_cap(numfreq, 3, 12) is None
    assert numfreq_rank_cap(numfreq, 3, 6) is None
    # an analytic Freq has no displacements to run out of
    assert numfreq_rank_cap("! B3LYP def2-SVP Opt Freq", 3, 64) is None
    # a molecule big enough that no plausible core count reaches the ceiling
    assert numfreq_rank_cap(numfreq, 24, 15) is None


def test_rank_cap_answers_nothing_rather_than_guessing():
    # An unknown atom count (an "* xyzfile" geometry we cannot see) is not a
    # licence to invent a ceiling — P2: say nothing rather than guess.
    numfreq = "! wB97M-V def2-QZVPP NumFreq"
    assert numfreq_rank_cap(numfreq, 0, 15) is None
    assert numfreq_rank_cap(numfreq, 3, 0) is None
    assert numfreq_rank_cap(numfreq, None, 15) is None
