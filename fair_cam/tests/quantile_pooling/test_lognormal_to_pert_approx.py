"""lognormal_to_pert_approx: mode derivation + 4-branch clamp + precedence
+ PERT-fidelity bounds. Per Meth-1/2 R2 + Meth-9 R3 + Meth-7 R2."""

from __future__ import annotations

import json
import math
import pathlib

import numpy as np
import pytest
from fair_cam.quantile_pooling import (
    LogNormalTruncFit,
    ModeClampReason,
    lognormal_to_pert_approx,
)
from scipy.stats import truncnorm


def _truncated_lognormal_samples(
    meanlog: float,
    sdlog: float,
    min_support: float,
    max_support: float,
    n: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Mirrors scripts/derive_pert_fidelity_bounds.py per Meth-2 T1 fix.
    Reference must be the fitted-truncated-lognormal, not raw lognormal."""
    a = (math.log(max(min_support, 1e-300)) - meanlog) / sdlog
    b = math.inf if math.isinf(max_support) else (math.log(max_support) - meanlog) / sdlog
    trunc_log_samples = truncnorm.rvs(
        a,
        b,
        loc=meanlog,
        scale=sdlog,
        size=n,
        random_state=rng,
    )
    return np.exp(trunc_log_samples)


_BOUNDS = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "pert_fidelity_bounds.json").read_text()
)


def test_mode_uses_true_lognormal_mode_formula() -> None:
    """Meth-1 R2: mode = exp(meanlog - sdlog**2), NOT exp(meanlog)."""
    fit = LogNormalTruncFit(meanlog=2.0, sdlog=1.0, min_support=0.0, max_support=math.inf)
    pert, reason = lognormal_to_pert_approx(fit)
    expected_mode = math.exp(2.0 - 1.0**2)
    assert pert.mode == pytest.approx(expected_mode, abs=1e-9)
    median = math.exp(2.0)
    assert abs(pert.mode - median) > 0.1
    assert reason is None


def test_clamp_above_max_support() -> None:
    fit = LogNormalTruncFit(meanlog=10.0, sdlog=0.1, min_support=0.0, max_support=5.0)
    pert, reason = lognormal_to_pert_approx(fit)
    assert pert.mode <= 5.0
    assert reason == ModeClampReason.UNTRUNCATED_MODE_ABOVE_MAX_SUPPORT


def test_clamp_below_min_support() -> None:
    fit = LogNormalTruncFit(meanlog=-5.0, sdlog=0.5, min_support=1.0, max_support=math.inf)
    pert, reason = lognormal_to_pert_approx(fit)
    assert pert.mode >= 1.0
    assert reason == ModeClampReason.UNTRUNCATED_MODE_BELOW_MIN_SUPPORT


def test_precedence_support_boundary_wins_over_pert_boundary() -> None:
    """Meth-9 R3: when both raw_mode > max_support AND raw_mode > high,
    support-boundary precedence wins."""
    fit = LogNormalTruncFit(meanlog=10.0, sdlog=0.1, min_support=0.0, max_support=5.0)
    pert, reason = lognormal_to_pert_approx(fit)
    assert reason == ModeClampReason.UNTRUNCATED_MODE_ABOVE_MAX_SUPPORT


def test_precedence_support_wins_when_max_support_clips_below_raw_mode() -> None:
    """Meth-3 T1 review: this test was previously misnamed
    'test_clamp_above_pert_high', but for any sdlog > 0 the lognormal mode
    satisfies raw_mode <= median <= q95, so the MODE_ABOVE_PERT_HIGH branch
    is unreachable for lognormal — the only way to push raw_mode above
    high is when max_support clips q_high BELOW raw_mode, which triggers
    UNTRUNCATED_MODE_ABOVE_MAX_SUPPORT first (precedence rule). This test
    now honestly documents that semantics."""
    # raw_mode = exp(5 - 0.01) ≈ 147.4 > max_support=120, so
    # UNTRUNCATED_MODE_ABOVE_MAX_SUPPORT wins over MODE_ABOVE_PERT_HIGH.
    fit = LogNormalTruncFit(meanlog=5.0, sdlog=0.1, min_support=0.0, max_support=120.0)
    pert, reason = lognormal_to_pert_approx(fit)
    assert reason == ModeClampReason.UNTRUNCATED_MODE_ABOVE_MAX_SUPPORT


def test_clamp_below_pert_low() -> None:
    """raw_mode < PERT low but raw_mode >= min_support -> MODE_BELOW_PERT_LOW.

    Requires raw_mode = exp(meanlog - sdlog^2) < q05_truncated.
    For high sdlog (e.g., 3.0) with positive meanlog, raw_mode collapses
    very small. With min_support clipping q05 above raw_mode, we get
    MODE_BELOW_PERT_LOW (raw_mode is >= min_support but < q05)."""
    # meanlog=2, sdlog=3 -> raw_mode = exp(2-9) = exp(-7) ≈ 0.000912.
    # min_support=0.01 -> raw_mode < min_support -> would hit BELOW_MIN_SUPPORT.
    # Need raw_mode >= min_support AND raw_mode < q05.
    # Try meanlog=2, sdlog=2, min_support=0 -> raw_mode = exp(2-4) = exp(-2) ≈ 0.1353.
    # q05 (untruncated) ≈ exp(2 - 1.645*2) ≈ exp(-1.29) ≈ 0.275.
    # raw_mode (0.135) < q05 (0.275) -> MODE_BELOW_PERT_LOW. min_support=0
    # so raw_mode (0.135) > min_support (0) -> reason is MODE_BELOW_PERT_LOW.
    fit = LogNormalTruncFit(meanlog=2.0, sdlog=2.0, min_support=0.0, max_support=math.inf)
    pert, reason = lognormal_to_pert_approx(fit)
    assert reason == ModeClampReason.MODE_BELOW_PERT_LOW
    assert pert.mode == pytest.approx(pert.low, abs=1e-9)


def test_no_clamp_when_mode_inside_bounds() -> None:
    fit = LogNormalTruncFit(meanlog=4.0, sdlog=0.5, min_support=0.0, max_support=math.inf)
    pert, reason = lognormal_to_pert_approx(fit)
    assert reason is None
    assert pert.low <= pert.mode <= pert.high


@pytest.mark.parametrize("sdlog", [0.25, 0.5, 1.0, 1.5, 2.0])
def test_pert_approx_fidelity_within_derived_bound(sdlog: float) -> None:
    bound = _BOUNDS["per_sdlog"][str(sdlog)]["bound_with_1_5x_safety"]
    rng = np.random.default_rng(seed=42)
    fit = LogNormalTruncFit(meanlog=4.0, sdlog=sdlog, min_support=0.0, max_support=math.inf)
    pert, _ = lognormal_to_pert_approx(fit)
    mean = (pert.low + 4 * pert.mode + pert.high) / 6
    alpha = 6 * ((mean - pert.low) / (pert.high - pert.low))
    beta = 6 * ((pert.high - mean) / (pert.high - pert.low))
    beta_samples = rng.beta(alpha, beta, size=100_000)
    pert_samples = pert.low + beta_samples * (pert.high - pert.low)
    # Meth-2 T1 fix: reference samples from fitted TRUNCATED lognormal,
    # not raw untruncated lognormal. Mirrors derive_pert_fidelity_bounds.py.
    ln_samples = _truncated_lognormal_samples(
        meanlog=4.0,
        sdlog=sdlog,
        min_support=fit.min_support,
        max_support=fit.max_support,
        n=100_000,
        rng=rng,
    )
    for p in (0.05, 0.95):
        pert_q = float(np.percentile(pert_samples, p * 100))
        ln_q = float(np.percentile(ln_samples, p * 100))
        rel_dev = abs(pert_q - ln_q) / max(abs(ln_q), 1e-9)
        assert rel_dev <= bound, f"sdlog={sdlog} p={p}: {rel_dev:.4f} > {bound:.4f}"
