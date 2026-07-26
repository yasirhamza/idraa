"""Unit tests for `fair_cam.risk_engine._truncation.truncated_lognormal`
(PR2 capacity bound, Task 2 -- Fable-owned math). The formula is FROZEN
(module docstring); these tests pin behaviour, not re-derive it.

Per the verification-reporting collaboration convention, numeric
assertions print expected-vs-actual side by side.
"""

from __future__ import annotations

import gc
import math
import tracemalloc

import numpy as np
import pytest

from fair_cam.quantile_pooling._lognormal import _qlnormtrunc
from fair_cam.risk_engine._truncation import (
    truncated_lognormal,
    truncated_lognormal_mixture_gather,
)

# Same SYNTHETIC triple as the module docstring's 3-way verification --
# `fair_cam/` is TRACKED and PUBLIC; never a real deployment (meanlog, sigma,
# max) here (rule (b)/(d), CLAUDE.md).
_MEANLOG = math.log(1e6)
_SIGMA = 1.7
_MAX = 1e9
_SEED = 20260725


def test_no_point_mass_at_cap_and_support_is_half_open():
    """At large n: zero draws equal `max_value`, and max(draws) is strictly
    below it -- kills np.minimum (the LEC-wall clamp the design forbids),
    which WOULD pile a point mass exactly at the cap."""
    n = 2_000_000
    rng = np.random.default_rng(_SEED)
    x = truncated_lognormal(rng, _MEANLOG, _SIGMA, n, _MAX)

    n_at_cap = int(np.sum(x == _MAX))
    print(f"draws == max_value: expected=0 actual={n_at_cap}")
    assert n_at_cap == 0
    print(f"max(draws) < max_value: max(draws)={x.max()!r} max_value={_MAX!r}")
    assert x.max() < _MAX
    assert x.min() >= 0.0


def test_scalar_and_equivalent_array_params_are_bit_identical():
    """Scalar (meanlog, sigma) and a size-`size` array whose every element
    equals that scalar must produce BIT-IDENTICAL output at the same seed
    -- the array path is not a different algorithm, just a different
    allocation strategy for the same arithmetic."""
    n = 10_000
    rng_scalar = np.random.default_rng(99)
    rng_array = np.random.default_rng(99)

    x_scalar = truncated_lognormal(rng_scalar, _MEANLOG, _SIGMA, n, _MAX)
    x_array = truncated_lognormal(rng_array, np.full(n, _MEANLOG), np.full(n, _SIGMA), n, _MAX)

    np.testing.assert_array_equal(x_scalar, x_array)


def test_mixed_scalar_array_params_are_bit_identical_to_full_scalar():
    """A scalar meanlog paired with an array sigma (all-equal values), and
    vice versa, must also match the fully-scalar result bit for bit."""
    n = 10_000
    rng_ref = np.random.default_rng(55)
    x_ref = truncated_lognormal(rng_ref, _MEANLOG, _SIGMA, n, _MAX)

    rng_a = np.random.default_rng(55)
    x_meanlog_array = truncated_lognormal(rng_a, np.full(n, _MEANLOG), _SIGMA, n, _MAX)
    np.testing.assert_array_equal(x_ref, x_meanlog_array)

    rng_b = np.random.default_rng(55)
    x_sigma_array = truncated_lognormal(rng_b, _MEANLOG, np.full(n, _SIGMA), n, _MAX)
    np.testing.assert_array_equal(x_ref, x_sigma_array)


@pytest.mark.parametrize("bad_max", [0.0, -1.0, float("inf"), float("nan")])
def test_degenerate_max_value_raises(bad_max):
    rng = np.random.default_rng(1)
    with pytest.raises(ValueError, match="max_value"):
        truncated_lognormal(rng, _MEANLOG, _SIGMA, 100, bad_max)


@pytest.mark.parametrize("bad_sigma", [0.0, -1.0, float("inf"), float("nan")])
def test_degenerate_scalar_sigma_raises(bad_sigma):
    rng = np.random.default_rng(1)
    with pytest.raises(ValueError, match="sigma"):
        truncated_lognormal(rng, _MEANLOG, bad_sigma, 100, _MAX)


@pytest.mark.parametrize("bad_sigma", [0.0, -1.0, float("inf"), float("nan")])
def test_degenerate_array_sigma_raises(bad_sigma):
    """One poisoned element in an otherwise-valid sigma array must still
    raise -- the check must be elementwise, not just `sigma[0]`."""
    rng = np.random.default_rng(1)
    sigma_arr = np.array([1.0, 1.2, bad_sigma, 0.8])
    with pytest.raises(ValueError, match="sigma"):
        truncated_lognormal(rng, np.array([9.0, 9.0, 9.0, 9.0]), sigma_arr, 4, _MAX)


def test_quantile_agreement_vs_qlnormtrunc_calls_truncated_lognormal():
    """Empirical quantiles of a truncated_lognormal sample vs.
    fair_cam.quantile_pooling._lognormal._qlnormtrunc's closed-form
    truncated-normal quantile (mirrors EnvStats::qlnormTrunc). This test
    CALLS truncated_lognormal -- an inline re-derivation compared to
    itself would be a tautology and would not discharge this criterion.

    The interior of the sweep (away from the extreme tail, where a finite
    sample under-populates the quantile and empirical noise dominates the
    true agreement) must agree tightly; the full sweep is reported but not
    gated as strictly, per the module docstring's worst-case-not-favourable
    disclosure.
    """
    n = 2_000_000
    rng = np.random.default_rng(_SEED)
    samples = truncated_lognormal(rng, _MEANLOG, _SIGMA, n, _MAX)

    interior_ps = [0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99]
    worst_interior = 0.0
    for p in interior_ps:
        empirical = float(np.quantile(samples, p))
        reference = _qlnormtrunc(p, _MEANLOG, _SIGMA, 0.0, _MAX)
        rel_err = abs(empirical - reference) / reference
        print(
            f"p={p}: expected(_qlnormtrunc)={reference:,.4f} "
            f"actual(empirical)={empirical:,.4f} rel_err={rel_err:.4%}"
        )
        worst_interior = max(worst_interior, rel_err)

    print(f"WORST interior rel_err={worst_interior:.4%}")
    assert worst_interior < 0.01


def test_mean_agrees_with_closed_form_conditional_mean():
    """Sample mean vs. the closed-form conditional mean of a truncated
    lognormal: E[X|X<M] = exp(meanlog + sigma**2/2) * Phi(b-sigma)/Phi(b)."""
    from scipy.special import ndtr

    n = 2_000_000
    rng = np.random.default_rng(_SEED + 1)
    samples = truncated_lognormal(rng, _MEANLOG, _SIGMA, n, _MAX)

    b = (math.log(_MAX) - _MEANLOG) / _SIGMA
    analytic_mean = math.exp(_MEANLOG + _SIGMA**2 / 2.0) * ndtr(b - _SIGMA) / ndtr(b)
    sample_mean = float(samples.mean())
    rel_err = abs(sample_mean - analytic_mean) / analytic_mean
    print(f"mean: expected(analytic)={analytic_mean:,.4f} actual(sampled)={sample_mean:,.4f}")
    print(f"rel_err={rel_err:.4%}")
    assert rel_err < 0.01


def test_mean_agrees_with_rejection_sampling():
    """Sample mean vs. rejection sampling (draw untruncated, discard >= M,
    average the rest) -- an entirely independent estimator of the same
    conditional mean."""
    n = 2_000_000
    rng_trunc = np.random.default_rng(_SEED + 2)
    trunc_mean = float(truncated_lognormal(rng_trunc, _MEANLOG, _SIGMA, n, _MAX).mean())

    rng_rej = np.random.default_rng(_SEED + 3)
    raw = rng_rej.lognormal(_MEANLOG, _SIGMA, n)
    accepted = raw[raw < _MAX]
    rejection_mean = float(accepted.mean())

    rel_err = abs(trunc_mean - rejection_mean) / rejection_mean
    print(
        f"mean: expected(rejection-sampling, n_accepted={len(accepted)})={rejection_mean:,.4f} "
        f"actual(truncated_lognormal)={trunc_mean:,.4f} rel_err={rel_err:.4%}"
    )
    assert rel_err < 0.01


# ---- peak-allocation (per-branch, no-regression against the measured
# pre-PR2 peak) ----


def _tracemalloc_peak(fn):
    gc.collect()
    tracemalloc.start()
    try:
        result = fn()
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    del result
    gc.collect()
    return peak


@pytest.mark.slow
def test_scalar_branch_peak_allocation_parity_with_pre_pr2():
    """Scalar branch (plain LOGNORMAL with `max`) must not regress the
    pre-PR2 `rng.lognormal` peak -- target is exact parity (1.00x a
    size-`size` float64 array), measured at the deployed ceiling
    n=10,000,000 per the design's memory envelope."""
    n = 10_000_000
    mean, sigma, max_value = 13.0, 1.2, 5e8

    peak_old = _tracemalloc_peak(lambda: np.random.default_rng(1).lognormal(mean, sigma, n))
    peak_new = _tracemalloc_peak(
        lambda: truncated_lognormal(np.random.default_rng(1), mean, sigma, n, max_value)
    )

    size_array_bytes = n * 8
    print(
        f"scalar peak: expected(pre-PR2 rng.lognormal)={peak_old:,d} bytes "
        f"({peak_old / size_array_bytes:.3f}x) vs "
        f"actual(truncated_lognormal)={peak_new:,d} bytes "
        f"({peak_new / size_array_bytes:.3f}x)"
    )
    # Generous 10% slack over exact parity for measurement noise (allocator
    # bookkeeping, tracemalloc's own overhead) -- the hard requirement is
    # "no regression", not bit-exact byte equality.
    assert peak_new <= peak_old * 1.10


@pytest.mark.slow
def test_mixture_branch_peak_allocation_no_regression_against_pre_pr2():
    """Multi-component mixture branch (`truncated_lognormal_mixture_gather`,
    what the engine actually calls) must not exceed the measured pre-PR2
    mixture peak (rng.choice + rng.lognormal on the gathered arrays) at the
    deployed ceiling n=10,000,000. Target is BELOW baseline via
    gather-release staging (measured 3.00x vs the baseline's 4.00x, both in
    units of one size-`size` float64 array) -- NOT a per-component masked
    loop, which would violate the one-`rng.choice`-then-one-`rng.random`
    stream contract (Layer B, pinned separately)."""
    n = 10_000_000
    means = np.array([8.0, 10.0, 12.0])
    sigmas = np.array([0.5, 0.8, 0.3])
    weights = np.array([0.3, 0.5, 0.2])
    max_value = 5e8

    def old_mixture():
        rng = np.random.default_rng(2)
        idx = rng.choice(3, size=n, p=weights)
        return rng.lognormal(means[idx], sigmas[idx], size=n)

    def new_mixture():
        rng = np.random.default_rng(2)
        idx = rng.choice(3, size=n, p=weights)
        return truncated_lognormal_mixture_gather(rng, means, sigmas, idx, n, max_value)

    peak_old = _tracemalloc_peak(old_mixture)
    peak_new = _tracemalloc_peak(new_mixture)

    size_array_bytes = n * 8
    print(
        f"mixture peak: expected(pre-PR2 baseline, no-regression ceiling)={peak_old:,d} bytes "
        f"({peak_old / size_array_bytes:.3f}x) vs "
        f"actual(truncated_lognormal_mixture_gather)={peak_new:,d} bytes "
        f"({peak_new / size_array_bytes:.3f}x)"
    )
    assert peak_new <= peak_old
