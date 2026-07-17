"""Truncated-normal fitter for vuln. Port of R/fit_distributions.R:124-128 + 233-241."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from scipy.optimize import minimize
from scipy.stats import truncnorm

from ._types import (
    DeadlineCallback,
    ModeClampReason,
    NormalTruncFit,
    PertTriple,
    QuantilePoolingError,
    _warn_if_divergent_fits,
    _weighted_mean_fields,
)


def _qnormtrunc(p: float, mean: float, sd: float, min_support: float, max_support: float) -> float:
    if sd <= 0:
        raise ValueError(f"sd must be > 0, got {sd}")
    a = (min_support - mean) / sd if math.isfinite(min_support) else -math.inf
    b = (max_support - mean) / sd if math.isfinite(max_support) else math.inf
    rv = truncnorm(a, b, loc=mean, scale=sd)
    return float(rv.ppf(p))


def _cost_norm_trunc(
    x: np.ndarray,
    low: float,
    high: float,
    q_low: float,
    q_high: float,
    min_support: float,
    max_support: float,
) -> float:
    """POSITIONAL args (NOT keyword-only) so scipy.optimize.minimize's
    args= tuple works. Per plan-review Meth-1/Spec-2/Arch-2 fix."""
    mean, sd = float(x[0]), float(x[1])
    if sd <= 0:
        return float("inf")
    try:
        q1 = _qnormtrunc(q_low, mean, sd, min_support, max_support)
        q2 = _qnormtrunc(q_high, mean, sd, min_support, max_support)
    except (ValueError, ZeroDivisionError):
        return float("inf")
    return (q1 - low) ** 2 + (q2 - high) ** 2


def fit_norm_trunc(
    low: float,
    high: float,
    q_low: float = 0.05,
    q_high: float = 0.95,
    min_support: float = 0.0,
    max_support: float = 1.0,
    *,
    maxiter: int = 200,
    wall_clock_ms: int = 500,
) -> NormalTruncFit:
    """Port of evaluator/collector fit_norm_trunc (R/fit_distributions.R:233-241).
    Used for vuln per MD-4a. Same calibration philosophy as fit_lognorm_trunc."""
    if high < low:
        raise QuantilePoolingError(f"high must be >= low; got low={low}, high={high}")
    callback = DeadlineCallback(wall_clock_ms)
    res = minimize(
        _cost_norm_trunc,
        x0=np.array([0.01, 1.0]),
        args=(low, high, q_low, q_high, min_support, max_support),
        method="Nelder-Mead",
        options={"maxiter": maxiter, "xatol": 1e-6, "fatol": 1e-6},
        callback=callback,
    )
    mean, sd = float(res.x[0]), float(res.x[1])
    if not (math.isfinite(mean) and math.isfinite(sd)) or sd <= 0:
        raise QuantilePoolingError(f"fit produced non-finite params: mean={mean}, sd={sd}")
    return NormalTruncFit(
        mean=mean,
        sd=sd,
        min_support=min_support,
        max_support=max_support,
    )


def combine_norm(
    fits: Sequence[NormalTruncFit],
    weights: Sequence[float] | None = None,
) -> NormalTruncFit:
    """Port of R/fit_distributions.R:124-128. Per MD-1 + MD-4a: pooled.mean
    can land outside [min_support, max_support] when individual fits
    diverged; ACCEPTABLE because normal_to_pert_approx clamps the mode.

    #343 caveat: parameter averaging is NOT a mixture — divergent fits
    pool to a distribution concentrating mass between the experts. A
    divergent pooling call logs a WARNING (see
    ``_warn_if_divergent_fits``); true mixture pooling is tracked at #243.
    ``weights=None`` == equal weights.
    """
    _warn_if_divergent_fits(fits, "mean", "sd", "combine_norm")
    return _weighted_mean_fields(
        fits,
        weights,
        ("mean", "sd", "min_support", "max_support"),
        NormalTruncFit,
    )


def normal_to_pert_approx(
    fit: NormalTruncFit,
    q_low: float = 0.05,
    q_high: float = 0.95,
) -> tuple[PertTriple, ModeClampReason | None]:
    """Same 4-branch precedence as lognormal_to_pert_approx; mode = mean
    for untruncated normal, clamped to support + PERT bounds."""
    low = _qnormtrunc(q_low, fit.mean, fit.sd, fit.min_support, fit.max_support)
    high = _qnormtrunc(q_high, fit.mean, fit.sd, fit.min_support, fit.max_support)
    raw_mode = fit.mean
    reason: ModeClampReason | None = None
    lo_bound = max(fit.min_support, low)
    hi_bound = min(fit.max_support, high)
    if raw_mode < fit.min_support:
        mode, reason = lo_bound, ModeClampReason.UNTRUNCATED_MODE_BELOW_MIN_SUPPORT
    elif raw_mode > fit.max_support:
        mode, reason = hi_bound, ModeClampReason.UNTRUNCATED_MODE_ABOVE_MAX_SUPPORT
    elif raw_mode > high:
        mode, reason = high, ModeClampReason.MODE_ABOVE_PERT_HIGH
    elif raw_mode < low:
        mode, reason = low, ModeClampReason.MODE_BELOW_PERT_LOW
    else:
        mode = raw_mode
    return PertTriple(low=low, mode=mode, high=high), reason
