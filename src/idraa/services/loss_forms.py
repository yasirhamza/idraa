"""Epic D-i (#497): authoring-time composition of FAIR loss-form magnitudes
into a single lognormal. Engine-boundary safe -- this runs at curation
time (D-iii migration / authoring), never in the sampling loop.

Milestone B (#loss-pert-overhaul) scope note: the composed lognormal is the
STORED shape only for the narrow catastrophic class; for capped entries (the
default) it is the intermediate the mechanical PERT conversion derives from
(low/high = exp(mu -/+ Z*sigma), mode == low -- see
docs/reference/loss-representation.md). This module remains the envelope x
share composer for curation either way; it no longer describes the default
library loss shape.

Method (D-i plan-gate decision, methodology-gated): pure Fenton-Wilkinson
moment-matching of independent lognormals. NO dominant-form shortcut (it dropped
up to 10% of the composed mean -- finding M2) and NO sigma-floor (it would
wrongly widen a genuinely-independent sum of comparable forms -- finding M4). FW
is mean-preserving and reduces to identity for one form. Independence understates
the tail under positive correlation; that bias is documented in
docs/reference/loss-magnitude-forms.md and any per-entry correlation widening is
applied upstream by the D-iii author, not here.

A form is a log-space lognormal (mean_log, sigma) -- 'mean_log' is the mean of
ln X (matching the stored {"distribution":"lognormal","mean":...} convention),
NOT the arithmetic mean.
"""

from __future__ import annotations

import math


def compose_forms_to_lognormal(
    forms: list[tuple[float, float]],
) -> tuple[float, float]:
    """Compose active loss-form lognormals into one (mean_log, sigma).

    Args:
        forms: non-empty list of (mean_log, sigma) log-space params, one per
            active form on a single loss side (all primary OR all secondary).

    Returns:
        (mean_log, sigma) for the composed single lognormal node. Mean-preserving:
        exp(mean_log + sigma**2 / 2) == sum(exp(mu_i + sigma_i**2 / 2)).

    Raises:
        ValueError: empty input, or any non-finite / non-positive-sigma param.
    """
    if not forms:
        raise ValueError("compose_forms_to_lognormal needs at least one form")
    for mu, sigma in forms:
        if not (math.isfinite(mu) and math.isfinite(sigma)):
            raise ValueError(f"form params must be finite; got ({mu!r}, {sigma!r})")
        if sigma <= 0:
            raise ValueError(f"form sigma must be > 0; got {sigma!r}")

    if len(forms) == 1:
        return forms[0]

    means = [math.exp(mu + sigma * sigma / 2.0) for mu, sigma in forms]
    variances = [
        (math.exp(sigma * sigma) - 1.0) * math.exp(2.0 * mu + sigma * sigma) for mu, sigma in forms
    ]
    total_mean = math.fsum(means)
    total_var = math.fsum(variances)

    sigma_s_sq = math.log(1.0 + total_var / (total_mean * total_mean))
    mu_s = math.log(total_mean) - sigma_s_sq / 2.0
    return (mu_s, math.sqrt(sigma_s_sq))
