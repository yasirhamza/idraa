import math

import pytest
from fair_cam.quantile_pooling import (
    Z_0_95,
    lognormal_from_quantiles,
    lognormal_mean,
    lognormal_quantiles,
)


def test_z_constant_matches_scipy():
    from scipy.stats import norm

    assert pytest.approx(float(norm.ppf(0.95)), abs=1e-12) == Z_0_95


def test_closed_form_hand_math():
    # low=p5=100, high=p95=10000.
    # mean = (ln100 + ln10000)/2 = (4.60517 + 9.21034)/2 = 6.907755
    # sigma = (ln10000 - ln100)/(2*1.6448536) = 4.60517/3.2897072 = 1.399869
    params = lognormal_from_quantiles(100.0, 10000.0)
    assert params["mean"] == pytest.approx(6.907755, abs=1e-5)
    assert params["sigma"] == pytest.approx(1.399869, abs=1e-5)


def test_roundtrip_identity():
    # quantiles(from_quantiles(lo, hi)) must reproduce (lo, hi) exactly.
    for lo, hi in [(100.0, 10000.0), (0.5, 4.0), (1e3, 5e6), (2.0, 3.0)]:
        p = lognormal_from_quantiles(lo, hi)
        out = lognormal_quantiles(p["mean"], p["sigma"], (0.05, 0.95))
        assert out[0] == pytest.approx(lo, rel=1e-9)
        assert out[1] == pytest.approx(hi, rel=1e-9)


def test_mean_exceeds_median_for_skewed():
    # lognormal mean = exp(mean + sigma^2/2) > median = exp(mean) for sigma>0.
    p = lognormal_from_quantiles(100.0, 10000.0)
    median = math.exp(p["mean"])
    assert lognormal_mean(p["mean"], p["sigma"]) > median


@pytest.mark.parametrize("lo,hi", [(0.0, 10.0), (-1.0, 10.0), (5.0, 1.0)])
def test_guards_reject_bad_input(lo, hi):
    with pytest.raises(ValueError):
        lognormal_from_quantiles(lo, hi)


def test_equal_low_high_gives_zero_sigma_point_mass():
    p = lognormal_from_quantiles(50.0, 50.0)
    assert p["sigma"] == pytest.approx(0.0, abs=1e-12)
    assert p["mean"] == pytest.approx(math.log(50.0), abs=1e-12)


# ---------------------------------------------------------------------------
# lognormal_from_median_mean — TIER-2 σ helper (Epic C-i, #335)
# ---------------------------------------------------------------------------

from fair_cam.quantile_pooling import lognormal_from_median_mean  # noqa: E402


def test_median_mean_hand_math():
    # median=1_000_000, mean=1_648_721 → σ² = 2·ln(1.648721) = 1.0 → σ=1.0
    p = lognormal_from_median_mean(1_000_000.0, 1_648_721.27)
    assert p["mean"] == pytest.approx(math.log(1_000_000.0), abs=1e-9)
    assert p["sigma"] == pytest.approx(1.0, abs=1e-5)


def test_roundtrip_mean_recovers():
    # the derived params must reproduce the input mean via lognormal_mean
    p = lognormal_from_median_mean(250_000.0, 560_000.0)
    assert lognormal_mean(p["mean"], p["sigma"]) == pytest.approx(560_000.0, rel=1e-9)
    assert math.exp(p["mean"]) == pytest.approx(250_000.0, rel=1e-9)  # median


# NOTE: named distinctly from the existing `test_guards_reject_bad_input` in
# this same file (line ~42) — two same-named functions in one module silently
# overwrite each other (plan-gate spec-#1).
@pytest.mark.parametrize(
    "median,mean",
    [
        (0.0, 10.0),  # median must be > 0
        (-1.0, 10.0),
        (100.0, 100.0),  # mean == median ⇒ σ=0 degenerate point mass: the helper
        # RAISES (a σ=0 loss distribution is meaningless for TIER-2).
        # This intentionally DIFFERS from lognormal_from_quantiles,
        # which allows lo==hi → σ=0. Pinned here (plan-gate spec-#5).
        (100.0, 50.0),  # mean < median impossible for lognormal
        # NaN/inf guard — these slip the <=0 and <=median checks because NaN
        # comparisons are always False and inf<=median is False; without the
        # explicit isfinite gate they would return {nan,nan} or {sigma=inf},
        # the documented Meth-B1 non-finite σ failure mode (security IMPORTANT).
        (float("nan"), 1e6),  # NaN median slips both range guards
        (1e6, float("inf")),  # inf mean slips the mean<=median guard
        (float("inf"), 1e6),  # inf median: mean<=median → inf<=inf is True, raises
        # existing guard — but also covered here for completeness
        (1e6, float("nan")),  # NaN mean slips the mean<=median guard
    ],
)
def test_median_mean_guards_reject_bad_input(median, mean):
    with pytest.raises(ValueError):
        lognormal_from_median_mean(median, mean)


# ---------------------------------------------------------------------------
# truncated_lognormal_mean / truncated_lognormal_mixture_mean -- PR2 Task 8b.
#
# The truncated-mean closed form (exp(mean+sigma**2/2) * Phi(b-sigma) / Phi(b))
# is the SAME formula fair_cam/risk_engine/_truncation.py's module docstring
# verifies (method 3) against its own inverse-CDF sampler at n=5,000,000 --
# this kernel is the single source of truth both the web display
# (idraa.app.lognormal_display_rows) and the run-view-model disclosure
# (idraa.services.run_view_model._lognormal_retention) now import, rather
# than each re-deriving it inline.
# ---------------------------------------------------------------------------

from fair_cam.quantile_pooling import (  # noqa: E402
    truncated_lognormal_mean,
    truncated_lognormal_mixture_mean,
)
from scipy.special import ndtr  # noqa: E402

# SYNTHETIC triple -- same one fair_cam/risk_engine/_truncation.py's module
# docstring and test suite use. fair_cam/ is TRACKED and PUBLIC; a real
# deployment's (mean, sigma, max) is never disclosed here (CLAUDE.md rule
# (b)/(d), mirrors Task 1's explicit synthetic-value instruction).
_TRUNC_MEAN = math.log(1e6)
_TRUNC_SIGMA = 1.7
_TRUNC_MAX = 1e9


def test_truncated_lognormal_mean_hand_math():
    # b = (ln(1e9) - ln(1e6)) / 1.7 = ln(1000) / 1.7 = 6.907755.../1.7
    b = (math.log(_TRUNC_MAX) - _TRUNC_MEAN) / _TRUNC_SIGMA
    expected = (
        math.exp(_TRUNC_MEAN + _TRUNC_SIGMA**2 / 2.0)
        * float(ndtr(b - _TRUNC_SIGMA))
        / float(ndtr(b))
    )
    actual = truncated_lognormal_mean(_TRUNC_MEAN, _TRUNC_SIGMA, _TRUNC_MAX)
    print(f"truncated mean: expected(hand-math)={expected!r} actual(kernel)={actual!r}")
    assert actual == pytest.approx(expected, rel=1e-12)


def test_truncated_lognormal_mean_empirical_agreement():
    """Independent check against fair_cam.risk_engine._truncation's own
    inverse-CDF sampler -- mirrors that module's own docstring verification
    #3 (closed-form conditional mean vs. a 5,000,000-draw empirical mean)."""
    import numpy as np
    from fair_cam.risk_engine._truncation import truncated_lognormal

    rng = np.random.default_rng(20260725)
    draws = truncated_lognormal(rng, _TRUNC_MEAN, _TRUNC_SIGMA, 5_000_000, _TRUNC_MAX)
    empirical = float(np.mean(draws))
    closed_form = truncated_lognormal_mean(_TRUNC_MEAN, _TRUNC_SIGMA, _TRUNC_MAX)
    rel = abs(empirical - closed_form) / closed_form
    print(
        f"truncated mean: expected(closed-form)={closed_form!r} "
        f"actual(empirical n=5e6)={empirical!r} rel={rel:.3e}"
    )
    assert rel < 5e-3  # Monte Carlo noise at n=5e6 -- mirrors _truncation.py's own tolerance


def test_truncated_lognormal_mean_non_binding_cap_matches_untruncated():
    """A cap far above the distribution's practical support (b >> 8.29,
    the Saturation note in fair_cam/risk_engine/_truncation.py) must
    reproduce lognormal_mean to float precision -- the closed form needs no
    special-cased branch for a non-binding cap."""
    huge_max = math.exp(_TRUNC_MEAN + 50.0 * _TRUNC_SIGMA)
    truncated = truncated_lognormal_mean(_TRUNC_MEAN, _TRUNC_SIGMA, huge_max)
    untruncated = lognormal_mean(_TRUNC_MEAN, _TRUNC_SIGMA)
    assert truncated == pytest.approx(untruncated, rel=1e-9)


@pytest.mark.parametrize("bad_sigma", [0.0, -1.0, float("inf"), float("nan")])
def test_truncated_lognormal_mean_rejects_bad_sigma(bad_sigma):
    with pytest.raises(ValueError):
        truncated_lognormal_mean(_TRUNC_MEAN, bad_sigma, _TRUNC_MAX)


@pytest.mark.parametrize("bad_max", [0.0, -1.0, float("inf"), float("nan")])
def test_truncated_lognormal_mean_rejects_bad_max(bad_max):
    with pytest.raises(ValueError):
        truncated_lognormal_mean(_TRUNC_MEAN, _TRUNC_SIGMA, bad_max)


def test_truncated_lognormal_mean_underflow_guard_raises():
    """A cap absurdly below the distribution's core (b ~= -69, the same
    underflow footgun fair_cam/risk_engine/_truncation.py documents) must
    raise rather than silently compute 0.0/0.0."""
    with pytest.raises(ValueError):
        truncated_lognormal_mean(math.log(1e9), 0.3, 1.0)


def test_truncated_lognormal_mixture_mean_hand_math():
    # Two components, UNEQUAL sigma, one shared cap -- same shape as
    # fair_cam/tests/risk_engine/test_mixture_truncation_pin.py's fixtures.
    components = [
        (0.4, math.log(1_000.0), 0.5),
        (0.6, math.log(1_000_000_000.0), 0.8),
    ]
    cap = 5_000_000_000.0
    expected = sum(w * truncated_lognormal_mean(m, s, cap) for w, m, s in components)
    actual = truncated_lognormal_mixture_mean(components, cap)
    print(f"mixture truncated mean: expected(hand-math)={expected!r} actual(kernel)={actual!r}")
    assert actual == pytest.approx(expected, rel=1e-12)


def test_truncated_lognormal_mixture_mean_single_component_matches_scalar_kernel():
    """A 1-component mixture (weight=1.0) must be identical to calling the
    scalar kernel directly -- no separate/divergent code path."""
    actual = truncated_lognormal_mixture_mean([(1.0, _TRUNC_MEAN, _TRUNC_SIGMA)], _TRUNC_MAX)
    expected = truncated_lognormal_mean(_TRUNC_MEAN, _TRUNC_SIGMA, _TRUNC_MAX)
    assert actual == pytest.approx(expected, rel=1e-12)
