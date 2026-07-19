"""Truncated-lognormal fitter, pooler, PERT collapser.
Port of evaluator/collector R/fit_distributions.R:67-79 + 174-209."""

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
    LogNormalTruncFit,
    LognormMixture,
    ModeClampReason,
    PertTriple,
    QuantilePoolingError,
    _clamp_mode,
    _golden_section_max,
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
    and as a stable wire-format enum value.

    Issue #27 Task 2: the clamp precedence itself now runs through the
    shared ``_clamp_mode`` helper (also used by ``normal_to_pert_approx``
    and both mixture collapsers) -- same code path, not a parallel
    reimplementation, so ``lognormal_mixture_to_pert_approx``'s
    single-component branch is byte-identical to this function by
    construction."""
    low = _qlnormtrunc(q_low, fit.meanlog, fit.sdlog, fit.min_support, fit.max_support)
    high = _qlnormtrunc(q_high, fit.meanlog, fit.sdlog, fit.min_support, fit.max_support)
    raw_mode = math.exp(fit.meanlog - fit.sdlog**2)
    mode, reason = _clamp_mode(raw_mode, fit.min_support, fit.max_support, low, high)
    return PertTriple(low=low, mode=mode, high=high), reason


def _plnormtrunc(
    x: float, meanlog: float, sdlog: float, min_support: float, max_support: float
) -> float:
    """CDF of a truncated lognormal at value x. Companion to
    ``_qlnormtrunc`` (issue #27 Task 2) -- identical truncnorm plumbing,
    evaluated at a log-transformed value rather than inverted at a
    probability. ``x`` outside ``[min_support, max_support]`` returns the
    saturated 0.0 / 1.0 rather than raising, since mixture bisection probes
    arbitrary x during bracket search."""
    if sdlog <= 0:
        raise ValueError(f"sdlog must be > 0, got {sdlog}")
    if x <= max(min_support, 0.0):
        return 0.0
    if math.isfinite(max_support) and x >= max_support:
        return 1.0
    a = (math.log(max(min_support, _LOG_FLOOR)) - meanlog) / sdlog
    b = (math.log(max_support) - meanlog) / sdlog if math.isfinite(max_support) else math.inf
    rv = truncnorm(a, b, loc=meanlog, scale=sdlog)
    return float(rv.cdf(math.log(x)))


def _lognorm_trunc_pdf(
    x: float, meanlog: float, sdlog: float, min_support: float, max_support: float
) -> float:
    """PDF of a truncated lognormal at x -- density-transform of the
    truncated-normal pdf on ln(x): f_X(x) = f_Y(ln x) / x. Used only by the
    mixture mode search (issue #27 Task 2), never for quantile inversion."""
    if x <= max(min_support, 0.0):
        return 0.0
    if math.isfinite(max_support) and x >= max_support:
        return 0.0
    a = (math.log(max(min_support, _LOG_FLOOR)) - meanlog) / sdlog
    b = (math.log(max_support) - meanlog) / sdlog if math.isfinite(max_support) else math.inf
    rv = truncnorm(a, b, loc=meanlog, scale=sdlog)
    return float(rv.pdf(math.log(x)) / x)


def mixture_quantile_lognorm(mix: LognormMixture, p: float) -> float:
    """Quantile of the mixture CDF ``F(x) = Σ wᵢ Fᵢ(x)`` at probability
    ``p``, solved by bisection (issue #27 Task 2) -- deterministic, no
    sampling.

    Single component delegates to ``_qlnormtrunc`` EXACTLY (the same call,
    not a reimplementation) -- the byte-identity guarantee for the
    dominant single-SME production path.

    Bracket: ``[min_i Qᵢ(p·0.5), max_i Qᵢ(1-(1-p)·0.5)]``, widened
    geometrically (log-space doubling) until it brackets ``p``. This
    bracket is self-satisfying in practice for any finite, well-formed
    component set: at ``x = max_i Qᵢ(1-(1-p)·0.5)``, every OTHER
    component's CDF is already ≈1 there (x sits far into that component's
    own tail once the components diverge, and coincides with its own
    quantile when they don't) -- so ``mix_cdf(hi) >= p`` holds from the
    first candidate, and symmetrically for the lower bound. Widening is
    therefore a defensive circuit breaker for a malformed/mis-seeded
    bracket, not something well-formed inputs are expected to hit (see
    ``test_bracket_widening_hard_cap_raises_arithmetic_error``, which
    exercises the cap via a deliberately mis-seeded bracket). HARD CAP
    ``MIXTURE_BRACKET_WIDEN_MAX_DOUBLINGS`` doublings per side raises
    ``ArithmeticError`` with the component params -- a finite-but-huge
    meanlog must not spin the render path.

    Bisection runs in log-space (natural for a distribution spanning many
    orders of magnitude) to ``MIXTURE_BISECT_REL_TOL`` relative
    tolerance."""
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must be in (0, 1), got {p}")
    if len(mix.components) == 1:
        c = mix.components[0]
        return _qlnormtrunc(p, c.meanlog, c.sdlog, c.min_support, c.max_support)

    def mix_cdf(x: float) -> float:
        return sum(
            w * _plnormtrunc(x, c.meanlog, c.sdlog, c.min_support, c.max_support)
            for c, w in zip(mix.components, mix.weights, strict=True)
        )

    lo = min(
        _qlnormtrunc(p * 0.5, c.meanlog, c.sdlog, c.min_support, c.max_support)
        for c in mix.components
    )
    hi = max(
        _qlnormtrunc(1.0 - (1.0 - p) * 0.5, c.meanlog, c.sdlog, c.min_support, c.max_support)
        for c in mix.components
    )

    doublings = 0
    while mix_cdf(lo) > p:
        if doublings >= MIXTURE_BRACKET_WIDEN_MAX_DOUBLINGS:
            raise ArithmeticError(
                f"mixture_quantile_lognorm: lower bracket failed to converge "
                f"after {MIXTURE_BRACKET_WIDEN_MAX_DOUBLINGS} doublings "
                f"(p={p}, components={mix.components}, weights={mix.weights})"
            )
        lo = lo / 2.0
        doublings += 1

    doublings = 0
    while mix_cdf(hi) < p:
        if doublings >= MIXTURE_BRACKET_WIDEN_MAX_DOUBLINGS:
            raise ArithmeticError(
                f"mixture_quantile_lognorm: upper bracket failed to converge "
                f"after {MIXTURE_BRACKET_WIDEN_MAX_DOUBLINGS} doublings "
                f"(p={p}, components={mix.components}, weights={mix.weights})"
            )
        hi = hi * 2.0
        doublings += 1

    lo_log, hi_log = math.log(lo), math.log(hi)
    for _ in range(MIXTURE_BISECT_MAX_ITER):
        if (hi_log - lo_log) < MIXTURE_BISECT_REL_TOL:
            break
        mid_log = (lo_log + hi_log) / 2.0
        mid = math.exp(mid_log)
        if mix_cdf(mid) < p:
            lo_log = mid_log
        else:
            hi_log = mid_log
    return math.exp((lo_log + hi_log) / 2.0)


def _mixture_density_lognorm(mix: LognormMixture, x: float) -> float:
    return sum(
        w * _lognorm_trunc_pdf(x, c.meanlog, c.sdlog, c.min_support, c.max_support)
        for c, w in zip(mix.components, mix.weights, strict=True)
    )


def _mixture_mode_lognorm(mix: LognormMixture, low: float, high: float) -> float:
    """UNCONSTRAINED global argmax of the mixture density ``Σ wᵢ fᵢ(x)``
    (issue #27 Task 2 binding amendment, multi-component branch).

    Candidates: every component's own closed-form mode
    ``exp(meanlogᵢ - sdlogᵢ²)`` -- INCLUDING modes that fall outside
    ``[low, high]`` or even the component's own support, since bounding is
    the caller's job (``_clamp_mode``), not this function's -- plus a
    ``MIXTURE_MODE_GRID_POINTS``-point log-spaced grid over the widened
    bracket ``[min_i modeᵢ/4, max_i modeᵢ·4] ∪ [low, high]``. The best
    candidate seeds a golden-section refine (±1e-9 relative) over its
    immediate neighbors in the candidate set.

    Returns the RAW (unclamped) mode -- callers apply ``_clamp_mode``."""
    component_modes = [math.exp(c.meanlog - c.sdlog**2) for c in mix.components]
    bracket_lo = max(min(min(component_modes) / 4.0, low), _LOG_FLOOR)
    bracket_hi = max(max(component_modes) * 4.0, high)

    def density(x: float) -> float:
        return _mixture_density_lognorm(mix, x)

    grid = np.geomspace(bracket_lo, bracket_hi, MIXTURE_MODE_GRID_POINTS)
    candidates = sorted(set(grid.tolist()) | set(component_modes))
    densities = [density(x) for x in candidates]
    best_i = max(range(len(candidates)), key=lambda i: densities[i])
    win_lo = candidates[best_i - 1] if best_i > 0 else candidates[0]
    win_hi = candidates[best_i + 1] if best_i < len(candidates) - 1 else candidates[-1]
    if win_lo >= win_hi:
        return candidates[best_i]
    return _golden_section_max(density, win_lo, win_hi)


def lognormal_mixture_to_pert_approx(
    mix: LognormMixture,
    q_low: float = 0.05,
    q_high: float = 0.95,
) -> tuple[PertTriple, ModeClampReason | None]:
    """PERT-collapse a lognormal mixture (issue #27 Task 2).

    ``low = mixture_quantile_lognorm(mix, q_low)``,
    ``high = mixture_quantile_lognorm(mix, q_high)``.

    Mode:
      ``len(components) == 1`` -- DEDICATED branch: ``raw_mode`` is the
        SAME closed-form ``exp(meanlog - sdlog**2)`` as
        ``lognormal_to_pert_approx``, clamped via the SAME ``_clamp_mode``
        helper -- byte-identical result (incl. ``mode_clamp_reason``) by
        construction, not by coincidence.
      ``len(components) > 1`` -- ``raw_mode`` is the UNCONSTRAINED global
        mixture-density argmax (``_mixture_mode_lognorm``), THEN
        ``_clamp_mode`` applies unchanged -- clamp-reason semantics are
        preserved for mixtures too.

    Support bounds for the clamp = min/max over component supports (Task 2
    binding amendment); supports are per-fieldset-uniform by construction
    in production, so this reduces to the single shared support in
    practice.

    Scope limitation (Meth-I1, spec §2 "PERT-collapse paths"): this
    restores RANGE coverage (low/high span the experts' union) -- the
    headline #27 defect -- but a unimodal PERT cannot represent a bimodal
    mixture: for the worked A/B pair (spec §6) the collapse places ~13% of
    mass in the inter-expert valley where the true mixture places <1%, and
    the collapsed median is ~66x the true mixture median (the Beta-PERT
    shape pulls its median toward the higher-loss expert). See
    ``test_divergent_pert_collapse_documented_limitation`` for the pinned
    values. That residual is a documented, TESTED limitation of the
    summary shape -- not a regression -- and is why the native mixture
    storage path (``services/wizard_finalize``, catastrophic losses only)
    exists: it is exact on ranges, moments, AND multimodality
    simultaneously, where the PERT collapse is not."""
    low = mixture_quantile_lognorm(mix, q_low)
    high = mixture_quantile_lognorm(mix, q_high)
    min_support = min(c.min_support for c in mix.components)
    max_support = max(c.max_support for c in mix.components)

    if len(mix.components) == 1:
        c = mix.components[0]
        raw_mode = math.exp(c.meanlog - c.sdlog**2)
    else:
        raw_mode = _mixture_mode_lognorm(mix, low, high)

    mode, reason = _clamp_mode(raw_mode, min_support, max_support, low, high)
    return PertTriple(low=low, mode=mode, high=high), reason
