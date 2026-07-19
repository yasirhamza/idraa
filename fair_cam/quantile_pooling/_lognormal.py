"""Truncated-lognormal fitter, pooler, PERT collapser.
Port of evaluator/collector R/fit_distributions.R:67-79 + 174-209."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from scipy.optimize import minimize
from scipy.stats import truncnorm

from ._types import (
    DeadlineCallback,
    LogNormalTruncFit,
    LognormMixture,
    ModeClampReason,
    PertTriple,
    QuantilePoolingError,
    _normalize_weights,
    _warn_if_divergent_fits,
)

# smallest positive float64 mantissa avoiding log underflow
_LOG_FLOOR = 1e-300


def _qlnormtrunc(
    p: float, meanlog: float, sdlog: float, min_support: float, max_support: float
) -> float:
    """Quantile of a truncated lognormal at probability p. Mirrors
    EnvStats::qlnormTrunc behavior used by R's fit_lognorm_trunc."""
    if sdlog <= 0:
        raise ValueError(f"sdlog must be > 0, got {sdlog}")
    a = (math.log(max(min_support, _LOG_FLOOR)) - meanlog) / sdlog
    b = (math.log(max_support) - meanlog) / sdlog if math.isfinite(max_support) else math.inf
    rv = truncnorm(a, b, loc=meanlog, scale=sdlog)
    return float(math.exp(rv.ppf(p)))


def _cost_lognorm_trunc(
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
    meanlog, sdlog = float(x[0]), float(x[1])
    if sdlog <= 0:
        return float("inf")
    try:
        q1 = _qlnormtrunc(q_low, meanlog, sdlog, min_support, max_support)
        q2 = _qlnormtrunc(q_high, meanlog, sdlog, min_support, max_support)
    except (ValueError, ZeroDivisionError):
        return float("inf")
    return (q1 - low) ** 2 + (q2 - high) ** 2


def fit_lognorm_trunc(
    low: float,
    high: float,
    q_low: float = 0.05,
    q_high: float = 0.95,
    min_support: float = 0.0,
    max_support: float = math.inf,
    *,
    maxiter: int = 200,
    wall_clock_ms: int = 500,
) -> LogNormalTruncFit:
    """Port of evaluator/collector fit_lognorm_trunc (R/fit_distributions.R:202-209).

    SCOPE / FOOTGUN: this is the TRUNCATED, scipy-optimized fitter, retained for
    the elicit->PERT-approx path (``lognormal_to_pert_approx``). Do NOT route
    NATIVE lognormal {mean, sigma} storage through it — use the closed-form
    ``lognormal_from_quantiles`` (``_lognormal_native``) instead. The optimizer
    diverges for large/wide anchors: from its fixed ``x0=[0.01, 1.0]`` seed,
    Nelder-Mead cannot reach a true meanlog of ~12 within ``maxiter`` and stalls
    at garbage (``meanlog~=0, sdlog~=10.76`` for a $1k-$50M p5/p95 pair). The
    closed form has no support bounds to drift the sampled p5/p95 off the entered
    low/high under an untruncated sampler. (See _lognormal_native module docstring
    and services/wizard_finalize._fit_lognorm_native.)

    Distribution assumption (MD-1.5a/b per spec §10):
      Lognormal fits for TEF/PL/SL data. Cyber LOSS MAGNITUDE (PL/SL) is
      empirically lognormal per Hubbard, *How to Measure Anything in
      Cybersecurity Risk* (2nd ed., 2023, ch. 6) and Jones, *Measuring
      and Managing Information Risk: A FAIR Approach*. TEF lognormality
      is a faithful port from evaluator/collector's fit_scenarios
      (R/fit_distributions.R:349-372 — uses fit_lognorm_trunc for both
      impact and frequency); per-fieldset Poisson alternative tracked
      via spec MD-8 deferred work.

    Calibration philosophy (MD-3a): LEAST-SQUARES on EXACTLY 2 UNWEIGHTED
    ANCHORS via Nelder-Mead with R-matching defaults x0=[0.01, 1.0],
    xatol=1e-6, fatol=1e-6.

    Bounded by maxiter + wall_clock_ms (via DeadlineCallback)."""
    if low <= 0 or high <= 0:
        raise QuantilePoolingError(f"low and high must be > 0; got low={low}, high={high}")
    if high < low:
        raise QuantilePoolingError(f"high must be >= low; got low={low}, high={high}")
    callback = DeadlineCallback(wall_clock_ms)
    res = minimize(
        _cost_lognorm_trunc,
        x0=np.array([0.01, 1.0]),
        args=(low, high, q_low, q_high, min_support, max_support),
        method="Nelder-Mead",
        options={"maxiter": maxiter, "xatol": 1e-6, "fatol": 1e-6},
        callback=callback,
    )
    meanlog, sdlog = float(res.x[0]), float(res.x[1])
    if not (math.isfinite(meanlog) and math.isfinite(sdlog)) or sdlog <= 0:
        raise QuantilePoolingError(
            f"fit produced non-finite params: meanlog={meanlog}, sdlog={sdlog}"
        )
    return LogNormalTruncFit(
        meanlog=meanlog,
        sdlog=sdlog,
        min_support=min_support,
        max_support=max_support,
    )


def combine_lognorm_trunc(
    fits: Sequence[LogNormalTruncFit],
    weights: Sequence[float] | None = None,
) -> LognormMixture:
    """Pool SME lognormal fits into a linear-opinion-pool mixture (issue
    #27 via #25) -- each ``fits[i]`` survives verbatim as its own
    ``LognormMixture`` component; ``weights`` are normalized to sum to 1
    (``weights=None`` == equal weights). A single fit collapses to a
    single-component mixture -- EXACT identity with every downstream path
    that only ever pools one SME (the dominant production case).

    Methodology: the linear opinion pool is the standard combination rule
    for expert probability distributions -- Clemen, R.T. & Winkler, R.L.
    (1999), "Combining Probability Distributions From Experts in Risk
    Analysis", Risk Analysis 19(2), pp. 187-203 (lineage to Stone 1961).

    R-oracle departure (explicit, not silent): this function used to be a
    faithful port of R/fit_distributions.R:67-79 (MD-1) -- a weighted
    arithmetic mean of (meanlog, sdlog, min_support, max_support). For
    DIVERGENT fits that average concentrated mass BETWEEN the experts,
    covering neither stated range (issue #343's worked example: $1k-$10k
    pooled with $1M-$50M gave a 90% range of ~$31k-$710k). The mixture
    replaces that averaging: it is an intentional, methodology-justified
    break from the R oracle for multi-component pooling, not a bug --
    see docs/superpowers/specs/2026-07-19-mixture-pooling-design.md
    "Decision record" (2026-07-19). A divergent pooling call still logs
    (now INFO, not WARNING -- see ``_warn_if_divergent_fits``): divergence
    is represented by the mixture, no longer distorted by averaging.
    """
    normalized = _normalize_weights(fits, weights, LognormMixture.__name__)
    _warn_if_divergent_fits(fits, "meanlog", "sdlog", "combine_lognorm_trunc")
    return LognormMixture(components=tuple(fits), weights=normalized)


def lognormal_to_pert_approx(
    fit: LogNormalTruncFit,
    q_low: float = 0.05,
    q_high: float = 0.95,
) -> tuple[PertTriple, ModeClampReason | None]:
    """Approximate fitted truncated lognormal as PERT triple.

    Mapping (Meth-1/2 R2 + Meth-9 R3 precedence):
      low  = quantile(fit, q_low)
      raw_mode = exp(meanlog - sdlog**2)   # TRUE LOGNORMAL MODE
      mode = clamp(raw_mode, max(min_support, low), min(max_support, high))
      high = quantile(fit, q_high)

    Precedence (support-boundary wins over PERT-boundary):
      1. raw_mode < min_support  -> UNTRUNCATED_MODE_BELOW_MIN_SUPPORT
      2. raw_mode > max_support  -> UNTRUNCATED_MODE_ABOVE_MAX_SUPPORT
      3. ELSE raw_mode > high    -> MODE_ABOVE_PERT_HIGH
      4. ELSE raw_mode < low     -> MODE_BELOW_PERT_LOW

    Meth-3 T1 review note: the MODE_ABOVE_PERT_HIGH branch (step 3) is
    unreachable in practice for the LOGNORMAL distribution — for any
    sdlog > 0, the lognormal mode satisfies raw_mode <= median <= q95,
    so raw_mode > high implies max_support already clipped high below
    raw_mode, which triggers step 2 first (precedence rule). The branch
    + enum value are kept for symmetry with normal_to_pert_approx (where
    the mode = mean, so right-skewed PERT bounds can leave mode > high)
    and as a stable wire-format enum value."""
    low = _qlnormtrunc(q_low, fit.meanlog, fit.sdlog, fit.min_support, fit.max_support)
    high = _qlnormtrunc(q_high, fit.meanlog, fit.sdlog, fit.min_support, fit.max_support)
    raw_mode = math.exp(fit.meanlog - fit.sdlog**2)
    reason: ModeClampReason | None = None
    lo_bound = max(fit.min_support, low)
    hi_bound = min(fit.max_support, high)
    if raw_mode < fit.min_support:
        mode, reason = lo_bound, ModeClampReason.UNTRUNCATED_MODE_BELOW_MIN_SUPPORT
    elif raw_mode > fit.max_support:
        mode, reason = hi_bound, ModeClampReason.UNTRUNCATED_MODE_ABOVE_MAX_SUPPORT
    elif raw_mode > high:  # unreachable for lognormal -- see ModeClampReason docstring
        # kept for symmetry with normal_to_pert_approx + wire-format stability
        mode, reason = high, ModeClampReason.MODE_ABOVE_PERT_HIGH
    elif raw_mode < low:
        mode, reason = low, ModeClampReason.MODE_BELOW_PERT_LOW
    else:
        mode = raw_mode
    return PertTriple(low=low, mode=mode, high=high), reason
