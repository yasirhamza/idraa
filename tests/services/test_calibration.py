"""Tests for services.calibration — relocated helpers + CalibrationContext seam."""

from __future__ import annotations

from decimal import Decimal

import pytest

from idraa.models.enums import IndustryType
from idraa.models.organization import Organization
from idraa.services.calibration import (
    CalibrationContext,
    calibration_context_from_org,
    org_industry_slug,
    revenue_tier_from_annual_revenue,
)


class TestRevenueTierFromAnnualRevenue:
    def test_none_returns_default_middle_tier(self) -> None:
        assert revenue_tier_from_annual_revenue(None) == "100m_to_1b"

    @pytest.mark.parametrize(
        ("revenue", "expected_tier"),
        [
            (Decimal("5000000"), "less_than_10m"),
            (Decimal("50000000"), "10m_to_100m"),
            (Decimal("500000000"), "100m_to_1b"),
            (Decimal("4000000000"), "1b_to_10b"),
            (Decimal("50000000000"), "10b_to_100b"),
            (Decimal("500000000000"), "more_than_100b"),
        ],
    )
    def test_tier_thresholds(self, revenue: Decimal, expected_tier: str) -> None:
        assert revenue_tier_from_annual_revenue(revenue) == expected_tier


class TestOrgIndustrySlug:
    def test_healthcare(self) -> None:
        assert org_industry_slug(IndustryType.HEALTHCARE) == "healthcare"

    def test_none_returns_other(self) -> None:
        assert org_industry_slug(None) == "other"


class TestCalibrationContextDefaults:
    def test_frozen(self) -> None:
        ctx = CalibrationContext(industry="healthcare", revenue_tier="1b_to_10b")
        with pytest.raises(AttributeError):
            ctx.industry = "tech"  # type: ignore[misc]


class TestCalibrationContextFromOrg:
    def test_builds_from_org_fields(self) -> None:
        org = Organization(
            name="Acme",
            industry_type=IndustryType.HEALTHCARE,
            organization_size="MEDIUM",  # type: ignore[arg-type]
            annual_revenue=Decimal("4000000000"),
        )
        ctx = calibration_context_from_org(org)
        assert ctx.industry == "healthcare"
        assert ctx.revenue_tier == "1b_to_10b"
