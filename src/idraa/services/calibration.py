# ruff: noqa: RUF002
"""Calibration context + IRIS-tier / industry-slug helpers.

This module owns the calibration anchor — the org-context tuple that feeds
wizard IRIS pre-fill via :func:`iris_baseline_for_form`. (Library-entry PL/SL
org ratio-scaling via :func:`library_calibrated_pre_fill` was REMOVED
2026-07-07 — the IRIS sector envelope IS the calibration; pre-fill is now
entry-absolute and org context no longer feeds PL/SL.) Issue #88 reframed
``industry`` and ``revenue_tier`` as org attributes (per FAIR Institute
Cyber Risk Scenario Taxonomy Feb 2025, pages 5-6: scenarios are
Threat/Asset/Method/Effect only), so the helpers live here rather than as
incidentals on the routes layer.

Helpers ``revenue_tier_from_annual_revenue`` and ``org_industry_slug`` are
relocated from ``routes/scenario_form_helpers.py`` (the original definitions
are now re-exports from this module to avoid breaking external imports).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from idraa.services.industry_mapping import V3_TO_FAIR_CAM_INDUSTRY

if TYPE_CHECKING:
    from idraa.models.organization import Organization


# IRIS revenue-tier thresholds (USD). Mirrors keys in
# fair_cam.data.iris_2025.ANNUAL_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024.
_REVENUE_TIER_THRESHOLDS: list[tuple[float, str]] = [
    (10_000_000, "less_than_10m"),
    (100_000_000, "10m_to_100m"),
    (1_000_000_000, "100m_to_1b"),
    (10_000_000_000, "1b_to_10b"),
    (100_000_000_000, "10b_to_100b"),
]
_REVENUE_TIER_DEFAULT = "100m_to_1b"

# Revenue-tier display labels for UI rendering. Mirrors the slug enumeration
# in _REVENUE_TIER_THRESHOLDS; kept colocated to prevent drift.
REVENUE_TIER_LABELS: dict[str, str] = {
    "less_than_10m": "< $10M",
    "10m_to_100m": "$10M - $100M",
    "100m_to_1b": "$100M - $1B",
    "1b_to_10b": "$1B - $10B",
    "10b_to_100b": "$10B - $100B",
    "more_than_100b": "> $100B",
}


def revenue_tier_from_annual_revenue(annual_revenue: Any) -> str:
    """Map org.annual_revenue (Decimal | None) to an IRIS tier slug.

    None / unset returns the middle tier as a soft default; caller should
    surface a "set your annual revenue at /organization" prompt so calibration
    is grounded in a real number rather than a placeholder.
    """
    if annual_revenue is None:
        return _REVENUE_TIER_DEFAULT
    rev = float(annual_revenue)
    for threshold, tier in _REVENUE_TIER_THRESHOLDS:
        if rev < threshold:
            return tier
    return "more_than_100b"


def org_industry_slug(org_industry_type: Any) -> str:
    """Map IndustryType enum value to the lowercase slug fair_cam expects.

    Reverse-lookup against V3_TO_FAIR_CAM_INDUSTRY: compares string values
    rather than enum identity (v3 IndustryType vs fair_cam IndustryType are
    distinct StrEnum classes whose ``==`` returns False even when their
    underlying string values match).
    """
    if org_industry_type is None:
        return "other"
    target = getattr(org_industry_type, "value", str(org_industry_type))
    for slug, fc in V3_TO_FAIR_CAM_INDUSTRY.items():
        if fc.value == target:
            return slug
    return "other"


@dataclass(frozen=True)
class CalibrationContext:
    """Org-derived calibration anchor for wizard library + IRIS pre-fill.

    industry + revenue_tier participate in:
      - services/wizard_helpers.iris_baseline_for_form (step-3 IRIS prior button;
        revenue_tier feeds FREQUENCY only — loss uses the per-industry prior,
        NOT tier-scaled)
      (library_calibrated_pre_fill no longer uses this context — org PL/SL
       ratio-scaling was removed 2026-07-07.)

    FAIR-CAM-native scope (PR γ-2 #103): there is no security_maturity or
    industry_sub_sector axis at pre-fill — controls express maturity via
    the FAIR-CAM control-aware reduction layer at MC time.
    """

    industry: str
    revenue_tier: str


def calibration_context_from_org(org: Organization) -> CalibrationContext:
    """Build a CalibrationContext from the current Organization row.

    Always sources from live org state — no snapshot, no staleness.
    """
    return CalibrationContext(
        industry=org_industry_slug(org.industry_type),
        revenue_tier=revenue_tier_from_annual_revenue(org.annual_revenue),
    )
