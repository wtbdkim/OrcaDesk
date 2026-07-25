"""Contract tests for orcamgr.core.queue.validate_result.

P25: success is judged per-kind, chemically. validate_result must raise
OrcaRunError for a chemically bad result and return silently for a good one,
with rules depending on the calculation kind:

* opt / ts_opt (and the *_freq combos)  -> geometry must have converged
* freq / opt_freq                       -> zero imaginary frequencies
* ts_freq / ts_opt_freq                 -> exactly ONE imaginary frequency
* neb_ts                                -> exactly one imaginary mode, but only
                                           when frequencies were computed at all
* mlip_opt                              -> opt_converged (no ORCA-style checks)
* sp / general / nmr / tddft / irc      -> normal termination is enough
* terminated_normally == False          -> fails for every kind

These tests construct ParseResult objects directly (no .out parsing), so they
pin the validation contract independent of the parser.
"""

from __future__ import annotations

import pytest

from orcamgr.core.input_generator import StepConfig
from orcamgr.core.parser import ParseResult
from orcamgr.core.queue import Calculation, validate_result
from orcamgr.core.runner import OrcaRunError


ALL_KINDS = [
    "opt", "ts_opt", "freq", "ts_freq", "opt_freq", "ts_opt_freq",
    "irc", "tddft", "sp", "general", "nmr", "neb_ts", "mlip_opt",
    "mlip_freq", "mlip_opt_freq",
]


# ---- helpers -------------------------------------------------------------
def make_calc(kind: str) -> Calculation:
    return Calculation(name=f"calc_{kind}", kind=kind, config=StepConfig(kind=kind))


def make_result(**overrides) -> ParseResult:
    """A ParseResult of a normally terminated run; fields overridable."""
    result = ParseResult(terminated_normally=True)
    for key, value in overrides.items():
        assert hasattr(result, key), f"unknown ParseResult field: {key}"
        setattr(result, key, value)
    return result


def freq_result(freqs, **overrides) -> ParseResult:
    """A normally terminated result with a computed frequency block."""
    return make_result(
        has_frequencies=True,
        frequencies=list(freqs),
        n_imaginary=sum(1 for f in freqs if f < 0),
        **overrides,
    )


# ---- abnormal termination fails every kind --------------------------------
@pytest.mark.parametrize("kind", ALL_KINDS)
def test_abnormal_termination_fails_every_kind(kind):
    calc = make_calc(kind)
    result = make_result(terminated_normally=False)
    with pytest.raises(OrcaRunError):
        validate_result(calc, result)


def test_abnormal_mlip_failure_carries_the_worker_error_message():
    # An MLIP job has no ORCA .out; its failure reason is the parsed
    # error_message itself, not the generic ORCA wording.
    calc = make_calc("mlip_opt")
    result = make_result(terminated_normally=False,
                         error_message="worker crashed: no module named mace")
    with pytest.raises(OrcaRunError, match="worker crashed"):
        validate_result(calc, result)


# ---- optimization kinds must converge -------------------------------------
@pytest.mark.parametrize("kind", ["opt", "ts_opt"])
def test_unconverged_optimization_fails(kind):
    result = make_result(is_optimization=True, opt_converged=False)
    with pytest.raises(OrcaRunError, match="did not converge"):
        validate_result(make_calc(kind), result)


@pytest.mark.parametrize("kind", ["opt", "ts_opt"])
def test_converged_optimization_passes(kind):
    result = make_result(is_optimization=True, opt_converged=True)
    validate_result(make_calc(kind), result)


# ---- freq: a true minimum has zero imaginary modes ------------------------
def test_freq_with_zero_imaginary_modes_passes():
    validate_result(make_calc("freq"), freq_result([100.0, 250.5, 3021.7]))


def test_freq_with_any_imaginary_mode_fails():
    with pytest.raises(OrcaRunError, match="imaginary"):
        validate_result(make_calc("freq"), freq_result([-42.7, 250.5, 3021.7]))


# ---- ts_freq: a transition state has exactly one imaginary mode ------------
def test_ts_freq_with_exactly_one_imaginary_mode_passes():
    validate_result(make_calc("ts_freq"), freq_result([-410.3, 250.5, 3021.7]))


def test_ts_freq_with_zero_imaginary_modes_fails():
    with pytest.raises(OrcaRunError, match="No imaginary frequency"):
        validate_result(make_calc("ts_freq"), freq_result([250.5, 3021.7]))


def test_ts_freq_with_two_imaginary_modes_fails():
    with pytest.raises(OrcaRunError, match="exactly one"):
        validate_result(make_calc("ts_freq"), freq_result([-410.3, -95.2, 3021.7]))


# ---- combined kinds check BOTH convergence and the frequency rule ----------
def test_opt_freq_converged_with_clean_frequencies_passes():
    result = freq_result([100.0, 250.5], is_optimization=True, opt_converged=True)
    validate_result(make_calc("opt_freq"), result)


def test_opt_freq_unconverged_fails_even_with_clean_frequencies():
    result = freq_result([100.0, 250.5], is_optimization=True, opt_converged=False)
    with pytest.raises(OrcaRunError, match="did not converge"):
        validate_result(make_calc("opt_freq"), result)


def test_opt_freq_converged_but_imaginary_mode_fails():
    result = freq_result([-42.7, 250.5], is_optimization=True, opt_converged=True)
    with pytest.raises(OrcaRunError, match="imaginary"):
        validate_result(make_calc("opt_freq"), result)


def test_ts_opt_freq_converged_with_one_imaginary_mode_passes():
    result = freq_result([-410.3, 250.5], is_optimization=True, opt_converged=True)
    validate_result(make_calc("ts_opt_freq"), result)


def test_ts_opt_freq_unconverged_fails_even_with_one_imaginary_mode():
    result = freq_result([-410.3, 250.5], is_optimization=True, opt_converged=False)
    with pytest.raises(OrcaRunError, match="did not converge"):
        validate_result(make_calc("ts_opt_freq"), result)


@pytest.mark.parametrize("freqs", [
    [250.5, 3021.7],            # zero imaginary: not a TS
    [-410.3, -95.2, 3021.7],    # two imaginary: higher-order saddle point
])
def test_ts_opt_freq_requires_exactly_one_imaginary_mode(freqs):
    result = freq_result(freqs, is_optimization=True, opt_converged=True)
    with pytest.raises(OrcaRunError):
        validate_result(make_calc("ts_opt_freq"), result)


# ---- neb_ts: the frequency check applies only when frequencies exist -------
def test_neb_ts_without_frequencies_passes():
    # The user may remove the verifying FREQ step; then no frequency rule
    # applies and normal termination is enough.
    validate_result(make_calc("neb_ts"), make_result(has_neb_path=True))


def test_neb_ts_with_exactly_one_imaginary_mode_passes():
    validate_result(make_calc("neb_ts"),
                    freq_result([-410.3, 100.0], has_neb_path=True))


@pytest.mark.parametrize("freqs", [
    [100.0, 200.0],             # zero imaginary
    [-410.3, -95.2, 100.0],     # two imaginary
])
def test_neb_ts_with_frequencies_requires_exactly_one_imaginary_mode(freqs):
    with pytest.raises(OrcaRunError, match="exactly one"):
        validate_result(make_calc("neb_ts"), freq_result(freqs, has_neb_path=True))


# ---- mlip_opt: convergence is the only check --------------------------------
def test_mlip_opt_unconverged_fails():
    result = make_result(is_optimization=True, opt_converged=False)
    with pytest.raises(OrcaRunError, match="did not converge"):
        validate_result(make_calc("mlip_opt"), result)


def test_mlip_opt_converged_passes():
    result = make_result(is_optimization=True, opt_converged=True)
    validate_result(make_calc("mlip_opt"), result)


def test_mlip_opt_is_not_subject_to_orca_frequency_rules():
    # Even if the result carried imaginary modes, an MLIP optimization is
    # validated on convergence alone (it returns before the ORCA-kind checks).
    result = freq_result([-42.0, 100.0], is_optimization=True, opt_converged=True)
    validate_result(make_calc("mlip_opt"), result)


# ---- mlip_freq / mlip_opt_freq: the imaginary-mode rule (MLIP, no ORCA .out) --
def test_mlip_freq_zero_imaginary_passes():
    result = freq_result([120.0, 1500.0, 3000.0])
    validate_result(make_calc("mlip_freq"), result)


def test_mlip_freq_imaginary_mode_fails():
    result = freq_result([-300.0, 1500.0])
    with pytest.raises(OrcaRunError, match="imaginary"):
        validate_result(make_calc("mlip_freq"), result)


def test_mlip_opt_freq_requires_convergence_and_zero_imaginary():
    # unconverged relaxation fails first
    unconv = freq_result([120.0, 1500.0], opt_converged=False)
    with pytest.raises(OrcaRunError, match="did not converge"):
        validate_result(make_calc("mlip_opt_freq"), unconv)
    # converged but with an imaginary mode still fails (not a true minimum)
    saddle = freq_result([-300.0, 1500.0], opt_converged=True)
    with pytest.raises(OrcaRunError, match="imaginary"):
        validate_result(make_calc("mlip_opt_freq"), saddle)
    # converged minimum passes
    minimum = freq_result([120.0, 1500.0], opt_converged=True)
    validate_result(make_calc("mlip_opt_freq"), minimum)


# ---- plain kinds: normal termination is enough ------------------------------
@pytest.mark.parametrize("kind", ["sp", "general", "nmr", "tddft", "irc"])
def test_plain_kinds_pass_on_normal_termination(kind):
    validate_result(make_calc(kind), make_result())


def test_sp_is_not_subject_to_optimization_convergence():
    # The convergence rule is keyed on the calc kind, not on result flags:
    # a single point never fails for lacking an optimization convergence.
    result = make_result(is_optimization=False, opt_converged=False)
    validate_result(make_calc("sp"), result)
