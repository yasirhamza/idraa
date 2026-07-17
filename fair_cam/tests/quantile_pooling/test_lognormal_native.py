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
