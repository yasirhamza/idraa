"""Closed-form UNTRUNCATED lognormal <-> (low, high) percentile-pair conversion.

Epic B (#326). The native FAIREngine samples an UNTRUNCATED lognormal
(rng.lognormal(mean, sigma)). These helpers convert between the two real-space
percentiles an analyst enters (low=p5, high=p95) and the engine's native
log-space {mean, sigma}, with NO truncation and NO optimizer — the two-quantile
fit of an untruncated lognormal is closed-form and exact:

    ln X ~ N(mean, sigma);  p5  = exp(mean - z*sigma) = low
                            p95 = exp(mean + z*sigma) = high
  => mean  = (ln(low) + ln(high)) / 2
     sigma = (ln(high) - ln(low)) / (2*z)            with z = z_0.95

This is distinct from `fit_lognorm_trunc` (truncated, scipy-fitted) which exists
for the elicit->PERT-approx path. Do NOT route native-lognormal storage through
the truncated fitter — its support bounds would drift the sampled p5/p95 off the
entered low/high under the untruncated sampler.

Distribution assumption: cyber loss severity (and, per the evaluator/collector
port, frequency) is modelled lognormal — heavy right tail. Same anchors as
`fit_lognorm_trunc`: Hubbard, *How to Measure Anything in Cybersecurity Risk*
(2nd ed., 2023, ch. 6); Jones, *Measuring and Managing Information Risk: A FAIR
Approach*. `Z_0_95 = scipy.stats.norm.ppf(0.95)` is the standard-normal 0.95
quantile: a 90% credible interval => p5/p95 bounds => z at probability 0.95.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

# scipy.stats.norm.ppf(0.95); pinned so callers never re-hardcode it.
Z_0_95: float = 1.6448536269514722


def lognormal_from_quantiles(
    low: float, high: float, q_low: float = 0.05, q_high: float = 0.95
) -> dict[str, float]:
    """Return native log-space {mean, sigma} for an untruncated lognormal whose
    q_low/q_high quantiles are (low, high). Defaults are the p5/p95 entry pair.

    Raises ValueError if low<=0, high<=0, or high<low (the same guard contract
    as fit_lognorm_trunc).
    """
    if low <= 0 or high <= 0:
        raise ValueError(f"low and high must be > 0; got low={low}, high={high}")
    if high < low:
        raise ValueError(f"high must be >= low; got low={low}, high={high}")
    if q_low <= 0 or q_high >= 1 or q_high <= q_low:
        raise ValueError(f"require 0<q_low<q_high<1; got {q_low}, {q_high}")
    from scipy.stats import norm  # local import keeps module import cheap

    z_low = float(norm.ppf(q_low))
    z_high = float(norm.ppf(q_high))
    ln_low, ln_high = math.log(low), math.log(high)
    sigma = (ln_high - ln_low) / (z_high - z_low)
    mean = (ln_high + ln_low) / 2.0 - sigma * (z_high + z_low) / 2.0
    return {"mean": mean, "sigma": sigma}


def lognormal_quantiles(mean: float, sigma: float, qs: Sequence[float]) -> tuple[float, ...]:
    """Real-space quantiles of an untruncated lognormal at probabilities qs."""
    from scipy.stats import norm

    return tuple(float(math.exp(mean + sigma * float(norm.ppf(q)))) for q in qs)


def lognormal_mean(mean: float, sigma: float) -> float:
    """Real-space mean (expected value) of an untruncated lognormal:
    exp(mean + sigma**2 / 2). For sigma>0 this exceeds the median exp(mean)."""
    return float(math.exp(mean + (sigma * sigma) / 2.0))


def truncated_lognormal_mean(mean: float, sigma: float, max_value: float) -> float:
    """Real-space mean of a lognormal(mean, sigma) RIGHT-TRUNCATED to support
    ``[0, max_value)`` -- the closed-form conditional mean under the SAME
    truncation ``fair_cam.risk_engine._truncation.truncated_lognormal``
    applies at draw time (PR2 capacity bound). This is fair_cam's single
    source of truth for the truncated mean: ``idraa.app.lognormal_display_rows``
    (web display) and ``idraa.services.run_view_model._lognormal_retention``
    (per-run disclosure) both import this rather than re-deriving it.

    Formula (frozen -- mirrors ``truncated_lognormal``'s own module
    docstring verification #3, which checks this same closed form against a
    5,000,000-draw empirical mean to 3.79e-04 relative agreement):

        b = (ln(max_value) - mean) / sigma
        E[X | X < max_value] = exp(mean + sigma**2/2) * Phi(b - sigma) / Phi(b)

    Derivation: for ``ln X ~ N(mean, sigma)`` right-truncated at
    ``max_value``, the conditional mean is the untruncated mean
    ``exp(mean + sigma**2/2)`` scaled by the ratio of two standard-normal
    CDFs evaluated at ``b`` shifted by ``sigma`` -- the standard
    truncated-lognormal conditional-mean identity (Phi/Phi's shift-by-sigma
    trick applied to ln X's truncated-normal mean).

    A cap comfortably above the distribution's practical support needs no
    special case: as ``b`` grows, ``Phi(b - sigma) / Phi(b)`` -> 1 and this
    naturally converges to ``lognormal_mean(mean, sigma)``.

    Raises ``ValueError`` for non-finite/<=0 ``sigma`` or ``max_value`` --
    mirrors ``truncated_lognormal``'s own guard contract (fair_cam/
    risk_engine/_truncation.py), which raises on the exact same silent-but-
    finite failure modes. Also raises ``ValueError`` if ``Phi(b)`` underflows
    to exactly ``0.0`` (``max_value`` far below the distribution's core --
    see that module's Underflow note): the true conditional mean is not
    ``0.0/0.0`` there, but this closed form cannot resolve it, so it fails
    loud rather than silently return a division artifact. Callers that need
    a fail-soft "cap is non-binding" default for a possibly-malformed
    legacy row (e.g. a pre-D19 snapshot) catch this ``ValueError``
    explicitly -- see ``services.run_view_model._lognormal_retention``.
    """
    if not (math.isfinite(sigma) and sigma > 0):
        raise ValueError(f"truncated_lognormal_mean: sigma must be finite and > 0, got {sigma!r}")
    if not (math.isfinite(max_value) and max_value > 0):
        raise ValueError(
            f"truncated_lognormal_mean: max_value must be finite and > 0, got {max_value!r}"
        )
    from scipy.special import ndtr  # local import keeps module import cheap

    b = (math.log(max_value) - mean) / sigma
    phi_b = float(ndtr(b))
    if phi_b <= 0.0:
        raise ValueError(
            f"truncated_lognormal_mean: ndtr(b) underflowed to 0.0 for b={b!r} "
            f"(max_value={max_value!r} sits far below the distribution's core -- "
            "see fair_cam.risk_engine._truncation's Underflow note)"
        )
    phi_b_minus_sigma = float(ndtr(b - sigma))
    return float(math.exp(mean + (sigma * sigma) / 2.0) * phi_b_minus_sigma / phi_b)


def truncated_lognormal_mixture_mean(
    components: Sequence[tuple[float, float, float]], max_value: float
) -> float:
    """Absolute truncated mean of a lognormal MIXTURE bounded at a SHARED
    ``max_value`` -- the per-component-truncation semantics
    ``fair_cam.risk_engine._truncation`` applies for ``LOGNORMAL_MIXTURE``
    (design's Bound rule: each regime is capped at its own capacity
    independently, NOT the whole mixture conditioned on ``X < max_value``).

    ``components`` is a sequence of ``(weight, mean, sigma)`` triples;
    weights are assumed to already sum to 1 and are NOT re-normalized here
    (mirrors ``lognormal_mixture_mean``-shaped callers elsewhere in this
    package, e.g. ``combine_lognorm_trunc``'s own normalized-weight
    contract). Returns::

        sum(w_i * truncated_lognormal_mean(mean_i, sigma_i, max_value)
            for w_i, mean_i, sigma_i in components)

    -- identical per-component numerator to ``truncated_lognormal_mean``
    above, just weight-summed. This is the ABSOLUTE truncated mean (a
    dollar figure), not a retention ratio: a caller that wants a retained
    FRACTION divides by the untruncated mixture mean itself (mirrors
    ``services.run_view_model._field_mean_and_retention``'s mixture kernel,
    which performs exactly that division).

    A single-component list (weight 1.0) is IDENTICAL to calling
    ``truncated_lognormal_mean`` directly -- not a separate code path.
    """
    return sum(weight * truncated_lognormal_mean(m, s, max_value) for weight, m, s in components)


def lognormal_from_median_mean(median: float, mean: float) -> dict[str, float]:
    """Return native log-space {mean, sigma} for a lognormal with the given
    real-space median and mean (TIER-2 vendor-stat derivation, spec §1).

    mean = median·exp(σ²/2)  ⇒  σ = sqrt(2·ln(mean/median)),  μ = ln(median).
    Requires median > 0 and mean > median STRICTLY: a lognormal's mean strictly
    exceeds its median for σ>0; mean == median ⇒ σ=0 (a degenerate point mass,
    meaningless as a TIER-2 loss distribution) so it RAISES (NOT a silent σ=0,
    unlike lognormal_from_quantiles). mean < median is impossible for a lognormal.

    Distribution assumption: cyber loss severity is lognormal — heavy right tail.
    Derivation: for ln X ~ N(μ, σ), median = exp(μ) and mean = exp(μ + σ²/2),
    so mean/median = exp(σ²/2) ⇒ σ = sqrt(2·ln(mean/median)), μ = ln(median).
    """
    if not (math.isfinite(median) and math.isfinite(mean)):
        raise ValueError(f"median and mean must be finite; got median={median}, mean={mean}")
    if median <= 0:
        raise ValueError(f"median must be > 0; got {median}")
    if mean <= median:
        raise ValueError(
            f"mean must be > median for a lognormal (σ>0); got mean={mean}, median={median}"
        )
    sigma = math.sqrt(2.0 * math.log(mean / median))
    return {"mean": math.log(median), "sigma": sigma}
