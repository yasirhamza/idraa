"""Library entry calibration — pure functions, no DB I/O.

Issue #103 (PR gamma-2) introduced org revenue-tier ratio-scaling of a library
entry's PL/SL at pre-fill time. That scaling was **removed (2026-07-07)**:
after the Epic D envelope x share recalibration, each entry's loss is anchored to
the IRIS Figure A3 **sector envelope** — a sector aggregate with no single
revenue tier — so scaling it by a per-entry ``calibration_anchor.revenue_tier``
(which no longer matched the loss basis; 64/93 entries were off by >3x) produced
incoherent magnitudes. The sector envelope IS the calibration; org size
influences risk via controls at Monte-Carlo time, not a loss pre-multiplier.

``library_calibrated_pre_fill`` now passes TEF/vuln/PL/SL through unchanged
(override fall-through preserved) and returns ``None`` calibration metadata, so
no "calibrated for your org" banner is shown.

``CalibrationAnchor`` is retained as the canonical validator for the
``calibration_anchor`` industry/revenue_tier provenance shape (still stored on
every entry). ``revenue_tier`` is now vestigial for calibration — kept as
provenance metadata; removing it from the schema is a separate data migration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from idraa.services.industry_mapping import V3_TO_FAIR_CAM_INDUSTRY

if TYPE_CHECKING:
    from idraa.models.scenario_library import (
        ScenarioLibraryEntry,
        ScenarioLibraryOverride,
    )

# Allowed revenue tier slugs — mirrors _REVENUE_TIER_THRESHOLDS in
# services/calibration.py and IRIS revenue-tier keys in fair_cam.data.iris_2025.
_REVENUE_TIER_SLUGS = frozenset(
    {
        "less_than_10m",
        "10m_to_100m",
        "100m_to_1b",
        "1b_to_10b",
        "10b_to_100b",
        "more_than_100b",
    }
)

# Allowed industry slugs (frozen at import time via V3_TO_FAIR_CAM_INDUSTRY).
_INDUSTRY_SLUGS: frozenset[str] = frozenset(V3_TO_FAIR_CAM_INDUSTRY.keys())


class CalibrationAnchor(BaseModel):
    """Pydantic-validated shape for ScenarioLibraryEntry.calibration_anchor.

    Validates that ``industry`` is a recognized v3 industry slug and
    ``revenue_tier`` is one of the six IRIS revenue tier slugs. ``loss_anchor``
    and ``vuln_posture`` are optional provenance fields. ``extra="forbid"`` is a
    typo guard; those two explicit optional fields are the ONLY extras permitted
    beyond ``industry`` and ``revenue_tier``.

    NOTE: ``revenue_tier`` no longer drives any loss scaling (removed 2026-07-07);
    it is retained as provenance metadata only.
    """

    model_config = ConfigDict(extra="forbid")

    industry: str = Field(...)
    revenue_tier: str = Field(...)
    # C-iii-a provenance keys (optional — absent on pre-curation entries).
    loss_anchor: str | None = None
    vuln_posture: str | None = None

    @field_validator("industry")
    @classmethod
    def _validate_industry(cls, v: str) -> str:
        if v not in _INDUSTRY_SLUGS:
            raise ValueError(f"industry must be one of {sorted(_INDUSTRY_SLUGS)} (got: {v!r})")
        return v

    @field_validator("revenue_tier")
    @classmethod
    def _validate_revenue_tier(cls, v: str) -> str:
        if v not in _REVENUE_TIER_SLUGS:
            raise ValueError(
                f"revenue_tier must be one of {sorted(_REVENUE_TIER_SLUGS)} (got: {v!r})"
            )
        return v


def library_calibrated_pre_fill(
    entry: ScenarioLibraryEntry,
    override: ScenarioLibraryOverride | None,
) -> tuple[dict[str, dict[str, Any] | None], None]:
    """Return (form_dict, None) — entry-absolute pre-fill, no org scaling.

    form_dict keys: 'tef', 'vuln', 'pl', 'sl' — each a distribution dict (or None
    for sl). Each field is the override value when the override sets it, else the
    entry's own value. **No revenue-tier scaling is applied** (removed
    2026-07-07 — the sector envelope IS the calibration; see module docstring).
    The second tuple element is always ``None`` (no calibration banner).
    """
    if override is not None:
        override_tef = override.threat_event_frequency
        override_vuln = override.vulnerability
        override_pl = override.primary_loss
        override_sl = override.secondary_loss
    else:
        override_tef = None
        override_vuln = None
        override_pl = None
        override_sl = None

    return (
        {
            "tef": override_tef if override_tef is not None else entry.threat_event_frequency,
            "vuln": override_vuln if override_vuln is not None else entry.vulnerability,
            "pl": override_pl if override_pl is not None else entry.primary_loss,
            "sl": override_sl if override_sl is not None else entry.secondary_loss,
        },
        None,
    )
