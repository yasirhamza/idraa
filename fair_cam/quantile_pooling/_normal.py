"""Truncated-normal fitter for vuln. Port of R/fit_distributions.R:124-128 + 233-241."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from scipy.optimize import minimize
from scipy.stats import truncnorm

from ._types import (
    MIXTURE_BISECT_MAX_ITER,
    MIXTURE_BISECT_REL_TOL,
    MIXTURE_BRACKET_WIDEN_MAX_DOUBLINGS,
    MIXTURE_MODE_GRID_POINTS,
    DeadlineCallback,
    ModeClampReason,
    NormalTruncFit,
    NormMixture,
    PertTriple,
    QuantilePoolingError,
    _clamp_mode,
    _golden_section_max,
    _normalize_weights,
    _warn_if_divergent_fits,
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
) -> NormMixture:
    """Pool SME normal (vuln, MD-4a) fits into a linear-opinion-pool
    mixture (issue #27 via #25) -- each ``fits[i]`` survives verbatim as
    its own ``NormMixture`` component, including a ``mean`` outside
    ``[min_support, max_support]`` (unclamped here; ``normal_mixture_to_
    pert_approx`` owns the clamp, same responsibility split as before).
    ``weights`` are normalized to sum to 1 (``weights=None`` == equal
    weights). A single fit collapses to a single-component mixture --
    EXACT identity with every downstream path that only ever pools one
    SME.

    Methodology: the linear opinion pool is the standard combination rule
    for expert probability distributions -- Clemen, R.T. & Winkler, R.L.
    (1999), "Combining Probability Distributions From Experts in Risk
    Analysis", Risk Analysis 19(2), pp. 187-203 (lineage to Stone 1961).

    R-oracle departure (explicit, not silent): this function used to be a
    faithful port of R/fit_distributions.R:124-128 (MD-1) -- a weighted
    arithmetic mean of (mean, sd, min_support, max_support). For
    DIVERGENT fits that average concentrated mass BETWEEN the experts,
    covering neither stated range (issue #343). The mixture replaces
    that averaging: it is an intentional, methodology-justified break
    from the R oracle for multi-component pooling, not a bug -- see
    docs/superpowers/specs/2026-07-19-mixture-pooling-design.md
    "Decision record" (2026-07-19). A divergent pooling call still logs
    (now INFO, not WARNING -- see ``_warn_if_divergent_fits``): divergence
    is represented by the mixture, no longer distorted by averaging.
    """
    normalized = _normalize_weights(fits, weights, NormMixture.__name__)
    _warn_if_divergent_fits(fits, "mean", "sd", "combine_norm")
    return NormMixture(components=tuple(fits), weights=normalized)


def normal_to_pert_approx(
    fit: NormalTruncFit,
    q_low: float = 0.05,
    q_high: float = 0.95,
) -> tuple[PertTriple, ModeClampReason | None]:
    """Same 4-branch precedence as lognormal_to_pert_approx; mode = mean
    for untruncated normal, clamped to support + PERT bounds.

    Issue #27 Task 2: the clamp precedence runs through the shared
    ``_clamp_mode`` helper (also used by ``lognormal_to_pert_approx`` and
    both mixture collapsers) -- same code path, not a parallel
    reimplementation, so ``normal_mixture_to_pert_approx``'s
    single-component branch is byte-identical to this function by
    construction."""
    low = _qnormtrunc(q_low, fit.mean, fit.sd, fit.min_support, fit.max_support)
    high = _qnormtrunc(q_high, fit.mean, fit.sd, fit.min_support, fit.max_support)
    raw_mode = fit.mean
    mode, reason = _clamp_mode(raw_mode, fit.min_support, fit.max_support, low, high)
    return PertTriple(low=low, mode=mode, high=high), reason


def _pnormtrunc(x: float, mean: float, sd: float, min_support: float, max_support: float) -> float:
    """CDF of a truncated normal at value x. Companion to ``_qnormtrunc``
    (issue #27 Task 2). ``x`` outside ``[min_support, max_support]``
    returns the saturated 0.0 / 1.0 rather than raising, since mixture
    bisection probes arbitrary x during bracket search."""
    if sd <= 0:
        raise ValueError(f"sd must be > 0, got {sd}")
    if x <= min_support:
        return 0.0
    if math.isfinite(max_support) and x >= max_support:
        return 1.0
    a = (min_support - mean) / sd if math.isfinite(min_support) else -math.inf
    b = (max_support - mean) / sd if math.isfinite(max_support) else math.inf
    rv = truncnorm(a, b, loc=mean, scale=sd)
    return float(rv.cdf(x))


def _norm_trunc_pdf(
    x: float, mean: float, sd: float, min_support: float, max_support: float
) -> float:
    """PDF of a truncated normal at x. Used only by the mixture mode
    search (issue #27 Task 2), never for quantile inversion."""
    if x < min_support or (math.isfinite(max_support) and x > max_support):
        return 0.0
    a = (min_support - mean) / sd if math.isfinite(min_support) else -math.inf
    b = (max_support - mean) / sd if math.isfinite(max_support) else math.inf
    rv = truncnorm(a, b, loc=mean, scale=sd)
    return float(rv.pdf(x))


def mixture_quantile_norm(mix: NormMixture, p: float) -> float:
    """Quantile of the mixture CDF at probability ``p``, solved by
    bisection (issue #27 Task 2) -- normal counterpart to
    ``mixture_quantile_lognorm``. Bisects in LINEAR space (not log-space):
    normal support can include zero/negative values, so a log transform is
    not generally valid here.

    Single component delegates to ``_qnormtrunc`` EXACTLY -- byte-identity
    for single-SME pooling. Bracket-widening / bisection tuning constants
    and the ``ArithmeticError`` circuit breaker mirror
    ``mixture_quantile_lognorm`` exactly; see that docstring for the
    self-bracketing argument (widening is additive here, doubling the
    current bracket width, rather than multiplicative on the value
    itself)."""
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must be in (0, 1), got {p}")
    if len(mix.components) == 1:
        c = mix.components[0]
        return _qnormtrunc(p, c.mean, c.sd, c.min_support, c.max_support)

    def mix_cdf(x: float) -> float:
        return sum(
            w * _pnormtrunc(x, c.mean, c.sd, c.min_support, c.max_support)
            for c, w in zip(mix.components, mix.weights, strict=True)
        )

    lo = min(
        _qnormtrunc(p * 0.5, c.mean, c.sd, c.min_support, c.max_support) for c in mix.components
    )
    hi = max(
        _qnormtrunc(1.0 - (1.0 - p) * 0.5, c.mean, c.sd, c.min_support, c.max_support)
        for c in mix.components
    )

    doublings = 0
    while mix_cdf(lo) > p:
        if doublings >= MIXTURE_BRACKET_WIDEN_MAX_DOUBLINGS:
            raise ArithmeticError(
                f"mixture_quantile_norm: lower bracket failed to converge "
                f"after {MIXTURE_BRACKET_WIDEN_MAX_DOUBLINGS} doublings "
                f"(p={p}, components={mix.components}, weights={mix.weights})"
            )
        width = max(hi - lo, 1e-9)
        lo -= width
        doublings += 1

    doublings = 0
    while mix_cdf(hi) < p:
        if doublings >= MIXTURE_BRACKET_WIDEN_MAX_DOUBLINGS:
            raise ArithmeticError(
                f"mixture_quantile_norm: upper bracket failed to converge "
                f"after {MIXTURE_BRACKET_WIDEN_MAX_DOUBLINGS} doublings "
                f"(p={p}, components={mix.components}, weights={mix.weights})"
            )
        width = max(hi - lo, 1e-9)
        hi += width
        doublings += 1

    for _ in range(MIXTURE_BISECT_MAX_ITER):
        if (hi - lo) < MIXTURE_BISECT_REL_TOL * max(abs(lo), abs(hi), 1.0):
            break
        mid = (lo + hi) / 2.0
        if mix_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _mixture_density_norm(mix: NormMixture, x: float) -> float:
    return sum(
        w * _norm_trunc_pdf(x, c.mean, c.sd, c.min_support, c.max_support)
        for c, w in zip(mix.components, mix.weights, strict=True)
    )


def _mixture_mode_norm(mix: NormMixture, low: float, high: float) -> float:
    """UNCONSTRAINED global argmax of the mixture density (issue #27 Task
    2, normal counterpart to ``_mixture_mode_lognorm``). Component modes =
    means (a truncated normal's untruncated mode is its mean). Grid is
    LINEAR-spaced (per the binding amendment's "mode grid linear-spaced")
    over ``[min_i meanᵢ, max_i meanᵢ] ∪ [low, high]``, padded slightly so a
    degenerate single-valued bracket (e.g. identical means) still has a
    search window."""
    component_modes = [c.mean for c in mix.components]
    bracket_lo = min(min(component_modes), low)
    bracket_hi = max(max(component_modes), high)
    pad = max((bracket_hi - bracket_lo) * 0.01, 1e-9)
    bracket_lo -= pad
    bracket_hi += pad

    def density(x: float) -> float:
        return _mixture_density_norm(mix, x)

    grid = np.linspace(bracket_lo, bracket_hi, MIXTURE_MODE_GRID_POINTS)
    candidates = sorted(set(grid.tolist()) | set(component_modes))
    densities = [density(x) for x in candidates]
    best_i = max(range(len(candidates)), key=lambda i: densities[i])
    win_lo = candidates[best_i - 1] if best_i > 0 else candidates[0]
    win_hi = candidates[best_i + 1] if best_i < len(candidates) - 1 else candidates[-1]
    if win_lo >= win_hi:
        return candidates[best_i]
    return _golden_section_max(density, win_lo, win_hi)


def normal_mixture_to_pert_approx(
    mix: NormMixture,
    q_low: float = 0.05,
    q_high: float = 0.95,
) -> tuple[PertTriple, ModeClampReason | None]:
    """PERT-collapse a normal mixture (issue #27 Task 2) -- normal
    counterpart to ``lognormal_mixture_to_pert_approx``; same dedicated
    single-component branch / shared ``_clamp_mode`` / multi-component
    unconstrained-argmax structure. See that docstring for the full
    rationale (range-coverage restored, bimodal-collapse limitation
    documented for the lognormal path -- the normal path shares the same
    structural limitation for divergent vuln estimates)."""
    low = mixture_quantile_norm(mix, q_low)
    high = mixture_quantile_norm(mix, q_high)
    min_support = min(c.min_support for c in mix.components)
    max_support = max(c.max_support for c in mix.components)

    if len(mix.components) == 1:
        c = mix.components[0]
        raw_mode = c.mean
    else:
        raw_mode = _mixture_mode_norm(mix, low, high)

    mode, reason = _clamp_mode(raw_mode, min_support, max_support, low, high)
    return PertTriple(low=low, mode=mode, high=high), reason
