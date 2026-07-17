"""Tests for ``iris_baseline_for_form_v2``'s CalibrationContext signature
(issue #88 + T7 reshape)."""

from __future__ import annotations

import pytest
from fair_cam.risk_engine.fair_core import DistributionType, FAIRDistribution

from idraa.services.calibration import CalibrationContext
from idraa.services.wizard_helpers import _quantile_pair, iris_baseline_for_form_v2


def _vose_alpha_beta(low: float, mode: float, high: float) -> tuple[float, float]:
    """Vose modified-BetaPERT α/β — IDENTICAL derivation to
    ``FAIRDistribution.sample``'s PERT branch in fair_core.py. The prefill
    quantiles MUST match these so the wizard shows the user the same shape
    the native engine samples (Meth-1, #324)."""
    gamma = 4.0
    mean = (low + gamma * mode + high) / (gamma + 2.0)
    stdev = (high - low) / (gamma + 2.0)
    g1 = (mean - low) / (high - low)
    g2 = ((mean - low) * (high - mean)) / (stdev**2)
    alpha = g1 * (g2 - 1.0)
    beta = alpha * (high - mean) / (mean - low)
    return alpha, beta


def test_iris_baseline_v2_accepts_calibration_context() -> None:
    ctx = CalibrationContext(industry="healthcare", revenue_tier="1b_to_10b")
    result = iris_baseline_for_form_v2(ctx)
    # IRIS has healthcare + 1b_to_10b — expect a dict back, not None.
    assert result is not None
    # T7 shape: per-fieldset {"low": float, "high": float} (or None).
    for fs in ("tef", "vuln", "pl"):
        assert fs in result, f"missing fieldset {fs}"
        pair = result[fs]
        assert pair is not None, f"{fs} pair unexpectedly None"
        assert "low" in pair and "high" in pair
        assert pair["high"] >= pair["low"]


def test_iris_baseline_v2_returns_none_for_unknown_industry() -> None:
    # A slug absent from V3_TO_FAIR_CAM_INDUSTRY returns None so the caller
    # renders an inline notice rather than crashing.
    ctx = CalibrationContext(industry="unknown_xyz", revenue_tier="1b_to_10b")
    assert iris_baseline_for_form_v2(ctx) is None


# --- Meth-1 (#324): IRIS-prefill PERT quantiles must use the Vose modified-
# BetaPERT α/β so they equal the quantiles of the distribution the native
# engine actually samples (fair_core PERT branch). Asserting against
# scipy.stats.beta.ppf with the Vose α/β proves prefill↔engine consistency.


def test_quantile_pair_pert_matches_vose_betapert() -> None:
    from scipy.stats import beta as beta_dist

    low, mode, high = 1000.0, 10000.0, 50000.0
    dist = FAIRDistribution(DistributionType.PERT, {"low": low, "mode": mode, "high": high})
    alpha, beta = _vose_alpha_beta(low, mode, high)
    expected_q05 = low + beta_dist.ppf(0.05, alpha, beta) * (high - low)
    expected_q95 = low + beta_dist.ppf(0.95, alpha, beta) * (high - low)

    pair = _quantile_pair(dist)
    assert pair["low"] == pytest.approx(expected_q05, rel=1e-9)
    assert pair["high"] == pytest.approx(expected_q95, rel=1e-9)


def test_quantile_pair_pert_pinned_vose_values() -> None:
    # Pinned regression values for the representative (1000, 10000, 50000)
    # triple. These are the NEW Vose-form quantiles. The OLD α+β=6 mean-form
    # gave q05=3630.59 / q95=30841.65 (a 6.10% / -1.58% divergence from the
    # engine's actual draws) — the inconsistency Meth-1 eliminates.
    dist = FAIRDistribution(
        DistributionType.PERT, {"low": 1000.0, "mode": 10000.0, "high": 50000.0}
    )
    pair = _quantile_pair(dist)
    assert pair["low"] == pytest.approx(3852.2285, abs=1e-3)
    assert pair["high"] == pytest.approx(30355.4585, abs=1e-3)


def test_quantile_pair_pert_degenerate_low_equals_high() -> None:
    # Mirror fair_core's point-mass guard (low == high → np.full(size, low));
    # quantiles collapse to ``low`` rather than dividing by zero.
    dist = FAIRDistribution(DistributionType.PERT, {"low": 5000.0, "mode": 5000.0, "high": 5000.0})
    pair = _quantile_pair(dist)
    assert pair["low"] == 5000.0
    assert pair["high"] == 5000.0


def test_quantile_pair_pert_rejects_mode_out_of_range() -> None:
    # Mirror fair_core's mode∈[low,high] validation.
    dist = FAIRDistribution(
        DistributionType.PERT, {"low": 1000.0, "mode": 60000.0, "high": 50000.0}
    )
    with pytest.raises(ValueError, match="mode"):
        _quantile_pair(dist)


def test_quantile_pair_pert_rejects_low_above_high() -> None:
    dist = FAIRDistribution(
        DistributionType.PERT, {"low": 50000.0, "mode": 10000.0, "high": 1000.0}
    )
    with pytest.raises(ValueError, match="low"):
        _quantile_pair(dist)
