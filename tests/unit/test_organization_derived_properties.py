"""Tests for Organization.revenue_tier + industry_slug derived properties."""

from __future__ import annotations

from decimal import Decimal

import pytest

from idraa.models.enums import IndustryType, OrganizationSize
from idraa.models.organization import Organization


def _org(
    *,
    annual_revenue: Decimal | None,
    industry_type: IndustryType = IndustryType.OTHER,
) -> Organization:
    return Organization(
        name="Acme",
        industry_type=industry_type,
        organization_size=OrganizationSize.MEDIUM,
        annual_revenue=annual_revenue,
    )


class TestOrgRevenueTier:
    @pytest.mark.parametrize(
        ("revenue", "expected_tier"),
        [
            (None, "100m_to_1b"),
            (Decimal("5000000"), "less_than_10m"),
            (Decimal("50000000"), "10m_to_100m"),
            (Decimal("500000000"), "100m_to_1b"),
            (Decimal("4000000000"), "1b_to_10b"),
            (Decimal("50000000000"), "10b_to_100b"),
            (Decimal("500000000000"), "more_than_100b"),
            # Boundary: exactly 10M lands in 10m_to_100m (strict-less-than threshold)
            (Decimal("10000000"), "10m_to_100m"),
        ],
    )
    def test_tier_property(self, revenue: Decimal | None, expected_tier: str) -> None:
        assert _org(annual_revenue=revenue).revenue_tier == expected_tier


class TestOrgIndustrySlug:
    @pytest.mark.parametrize(
        ("industry_type", "expected_slug"),
        [
            (IndustryType.HEALTHCARE, "healthcare"),
            (IndustryType.OTHER, "other"),
            (IndustryType.FINANCIAL, "financial"),
        ],
    )
    def test_slug_property(self, industry_type: IndustryType, expected_slug: str) -> None:
        org = _org(annual_revenue=None, industry_type=industry_type)
        assert org.industry_slug == expected_slug
