"""HTMX wizard helper services.

Pre-fill scenario step-3 SME-estimate rows from IRIS industry baselines,
and apply overlay multipliers to current ``(low, high)`` rows. Pure
functions over form state. State persistence happens in the calling
route.

T7 reshape: the wizard step-3 form is no longer a single ``(low, mode,
high)`` PERT triple per fieldset. It is now a list of per-SME
``{"sme_id", "low", "high"}`` rows per fieldset (evaluator-style
elicitation, MD-3 default 90% CI). The IRIS baseline returns a single
synthetic ``(low, high)`` pair per fieldset attributed to the IRIS
system-owned SME; overlay multipliers scale ``(low, high)`` of every
row in the affected fieldsets.

Meth-7/8/9 PR2 (per plan §Task 7.1):

- PERT and TRIANGULAR are different distributions — different
  closed-form quantiles per ``distribution_type``.
- Quantile extraction is analytic (scipy.stats.beta.ppf / triangular
  CDF inversion), NOT Monte Carlo sampling — pre-fill values must be
  deterministic, not seed-dependent.
- Unsupported ``distribution_type`` raises ``ValueError``; the caller
  catches per-fieldset so missing-data flows through the spec's "no
  IRIS baseline" path (returns ``None`` for that fieldset) rather than
  silently emitting a degenerate ``(0, 0)`` pair.
"""

from __future__ import annotations

import math
from typing import Any

from fair_cam.parameters.industry_calibration import (
    create_industry_calibrated_parameters,
)
from fair_cam.risk_engine.fair_core import FAIRDistribution

from idraa.services.calibration import CalibrationContext
from idraa.services.industry_mapping import V3_TO_FAIR_CAM_INDUSTRY

# Latest IRIS year supported by fair_cam. Hard-coded — iris_calibration_year
# is no longer a per-scenario decision (PR pi).
IRIS_YEAR = 2025

# Hard ceiling on form numeric values to prevent inf/nan pollution. An
# analyst submitting 1e308 and chaining overlay multiplications will produce
# inf otherwise; the JSON serializer + downstream MC engine then mis-handle.
_MAX_ABS_FORM_VALUE = 1e15


def iris_baseline_for_form_v2(
    ctx: CalibrationContext,
) -> dict[str, dict[str, float] | None] | None:
    """Return ``{tef, vuln, pl, sl} -> {"low", "high"}`` 5th/95th-percentile
    quantile pairs of the IRIS-fitted distribution per fieldset, or ``None``
    when the v3 industry slug has no fair_cam equivalent OR fair_cam's
    IRIS calibration rejects the ``(industry, revenue_tier)`` combo.

    Replaces the deleted ``iris_baseline_for_form`` (which returned PERT
    triples). Same fair_cam API entry point — only the per-distribution
    quantile extraction is new (analytic, per ``_quantile_pair``).

    Per-fieldset ``ValueError`` from ``_quantile_pair`` (raised for an
    unsupported ``distribution_type``) is swallowed to ``None`` for that
    fieldset so the caller's "no IRIS baseline" UI path renders the
    fieldset empty rather than crashing the prefill request — Meth-9 PR2.
    """
    fair_industry = V3_TO_FAIR_CAM_INDUSTRY.get(ctx.industry)
    if fair_industry is None:
        return None
    try:
        params = create_industry_calibrated_parameters(
            fair_industry,
            ctx.revenue_tier,
            iris_year=IRIS_YEAR,
        )
    except (KeyError, ValueError):
        return None

    def _safe(dist: FAIRDistribution | None) -> dict[str, float] | None:
        if dist is None:
            return None
        try:
            return _quantile_pair(dist)
        except ValueError:
            return None

    return {
        "tef": _safe(params.threat_event_frequency),
        "vuln": _safe(params.vulnerability),
        "pl": _safe(params.primary_loss),
        # FAIRParameters.secondary_loss is non-optional; if fair_cam's IRIS
        # builder produces a degenerate zero-PL secondary, surface as None
        # so the form leaves SL blank.
        "sl": _safe(params.secondary_loss) if params.secondary_loss else None,
    }


def _quantile_pair(dist: FAIRDistribution) -> dict[str, float]:
    """Extract 5th/95th quantiles per MD-3 default 90% CI.

    Meth-7/8/9 PR2 fixes:
      - Meth-7: PERT and TRIANGULAR are DIFFERENT distributions; use the
        right closed-form per ``distribution_type``, not the same
        Beta-sampling path for both.
      - Meth-8: use analytic quantile (``scipy.stats.beta.ppf`` /
        triangular CDF inversion) instead of 10k-sample Monte Carlo —
        pre-fill values should be deterministic, not seed-dependent.
      - Meth-9: raise ``ValueError`` for unsupported ``distribution_type``
        so the caller's "no IRIS baseline" path handles missing-data
        correctly, rather than silently returning ``(0, 0)`` which
        ``clean_quantile_pair`` then expands into a degenerate
        ``(0.05, 0.05)`` pre-fill.

    The ``1.645`` constant is the 95th-percentile z-score
    (``scipy.stats.norm.ppf(0.95)``) used for 5th/95th quantile extraction
    from normal/lognormal in real space.
    """
    from scipy.stats import beta as _beta_dist

    p = dist.parameters
    kind = dist.distribution_type.value

    if kind == "pert" and "mode" in p:
        # Analytic PERT quantile via scipy Beta CDF inversion. PERT is a
        # 4-parameter Beta scaled to [low, high]. Meth-1 (#324): the alpha/beta
        # derivation now uses the Vose modified-BetaPERT form (gamma=4,
        # stdev-based alpha/beta) — IDENTICAL to ``FAIRDistribution.sample``'s
        # PERT branch in fair_core.py and to pyfair's ``utility/beta_pert.py``:
        #   mean  = (low + 4*mode + high) / 6
        #   stdev = (high - low) / 6
        #   alpha = ((mean-low)/(high-low)) * ((mean-low)*(high-mean)/stdev**2 - 1)
        #   beta  = alpha * (high - mean) / (mean - low)
        # The prior simpler form fixed alpha+beta=6 (mean-based): it matched the
        # PERT mean but gave a DIFFERENT shape than what the engine samples,
        # so the prefill q05/q95 diverged from the engine's draws (up to
        # ~6% at q05). The Vose form makes the wizard prefill quantiles equal
        # the quantiles of the distribution the native engine actually
        # samples downstream.
        low_p, mode_p, high_p = p["low"], p["mode"], p.get("high", p["low"] * 2)
        # Mirror fair_core's PERT degenerate-case guards exactly so the
        # prefill never diverges from (or crashes where) the engine doesn't.
        if low_p > high_p:
            raise ValueError(f"PERT low ({low_p}) must be <= high ({high_p})")
        if low_p == high_p:
            # fair_core returns a point-mass at ``low`` (np.full(size, low));
            # in quantile terms both q05 and q95 collapse to ``low``.
            return {"low": float(low_p), "high": float(low_p)}
        if mode_p < low_p or mode_p > high_p:
            raise ValueError(f"PERT mode ({mode_p}) must be in [low, high] ([{low_p}, {high_p}])")
        gamma = 4.0
        mean = (low_p + gamma * mode_p + high_p) / (gamma + 2.0)
        stdev = (high_p - low_p) / (gamma + 2.0)
        g1 = (mean - low_p) / (high_p - low_p)
        g2 = ((mean - low_p) * (high_p - mean)) / (stdev**2)
        alpha = g1 * (g2 - 1.0)
        beta_p = alpha * (high_p - mean) / (mean - low_p)
        return {
            "low": float(low_p + _beta_dist.ppf(0.05, alpha, beta_p) * (high_p - low_p)),
            "high": float(low_p + _beta_dist.ppf(0.95, alpha, beta_p) * (high_p - low_p)),
        }

    if kind == "triangular" and "mode" in p:
        # Triangular has different shape than PERT/Beta. Closed-form quantile:
        #   for q < (mode-low)/(high-low):  low + sqrt(q*(high-low)*(mode-low))
        #   else:                            high - sqrt((1-q)*(high-low)*(high-mode))
        low_p, mode_p, high_p = p["low"], p["mode"], p.get("high", p["low"] * 2)
        if high_p <= low_p:
            raise ValueError(f"Triangular high ({high_p}) must be > low ({low_p})")
        cdf_at_mode = (mode_p - low_p) / (high_p - low_p)

        def _tri_q(q: float) -> float:
            if q < cdf_at_mode:
                return float(low_p + math.sqrt(q * (high_p - low_p) * (mode_p - low_p)))
            return float(high_p - math.sqrt((1 - q) * (high_p - low_p) * (high_p - mode_p)))

        return {"low": _tri_q(0.05), "high": _tri_q(0.95)}

    if kind == "lognormal":
        mean = p.get("mean", 0.0)
        sigma = p.get("sigma", 1.0)
        return {
            "low": math.exp(mean - 1.645 * sigma),
            "high": math.exp(mean + 1.645 * sigma),
        }
    if kind == "normal":
        mean = p.get("mean", 0.0)
        std = p.get("std", 0.0)
        return {
            "low": max(0.0, mean - 1.645 * std),
            "high": mean + 1.645 * std,
        }
    if kind == "uniform":
        return {"low": p.get("low", 0.0), "high": p.get("high", 1.0)}

    # Meth-9 PR2: raise rather than silently return zero-width pair. The
    # caller's per-fieldset ``except ValueError: return None`` collapses
    # this fieldset to "no IRIS baseline" which the UI handles cleanly.
    raise ValueError(f"unsupported distribution_type for quantile extraction: {kind!r}")


def apply_overlay_multipliers(
    current_estimates: dict[str, list[dict[str, Any]]],
    overlay_freq_mult: float,
    overlay_mag_mult: float,
) -> dict[str, list[dict[str, Any]]]:
    """Scale ``(low, high)`` of every row in each fieldset by the appropriate
    overlay multiplier.

    - ``tef`` rows scale by ``overlay_freq_mult`` (frequency).
    - ``pl`` / ``sl`` rows scale by ``overlay_mag_mult`` (magnitude).
    - ``vuln`` rows are NOT scaled — vulnerability is a probability per the
      FAIR Standard; overlays do not multiply probabilities.

    Pure function over the ``state.sme_estimates`` shape: a dict keyed by
    fieldset, each value a list of ``{"sme_id", "low", "high"}`` rows.
    Non-row keys (``sme_id``) are preserved untouched. ``_clamp`` bounds
    the scaled values so chained overlay applications cannot produce inf /
    nan that pollutes downstream JSON serialization or the MC engine.
    """
    result: dict[str, list[dict[str, Any]]] = {}
    for fs, rows in current_estimates.items():
        if fs == "tef":
            mult = overlay_freq_mult
        elif fs in ("pl", "sl"):
            mult = overlay_mag_mult
        else:
            mult = 1.0
        result[fs] = [
            {
                **row,
                "low": _clamp(float(row["low"]) * mult),
                "high": _clamp(float(row["high"]) * mult),
            }
            for row in rows
        ]
    return result


def _clamp(value: float) -> float:
    """Bound numeric form values to prevent inf/nan from chained multipliers."""
    if value != value or abs(value) > _MAX_ABS_FORM_VALUE:  # NaN check + magnitude
        return _MAX_ABS_FORM_VALUE if value > 0 else -_MAX_ABS_FORM_VALUE
    return value
