"""Regression tests for routes.scenario_form_helpers.org_industry_slug.

The original implementation compared the V3-side enum member against the
fair_cam-side enum member with ``==``. Both are StrEnum classes whose
underlying string values match, but Python's enum equality is identity-
based across classes — every comparison returned False, so the slug
helper returned "other" for every org. The scenario UI then displayed
"other" as the industry regardless of what the operator had set on the
organization profile.

These tests pin the value-based comparison in place.
"""

from __future__ import annotations

import pytest
from fair_cam.parameters.industry_calibration import IndustryType as FCIndustryType

from idraa.models.enums import IndustryType as V3IndustryType
from idraa.routes.scenario_form_helpers import org_industry_slug


@pytest.mark.parametrize(
    "industry_type, expected_slug",
    [
        (V3IndustryType.MANUFACTURING, "manufacturing"),
        (V3IndustryType.HEALTHCARE, "healthcare"),
        (V3IndustryType.FINANCIAL, "financial"),
        (V3IndustryType.INFORMATION, "information"),
        (V3IndustryType.UTILITIES, "utilities"),
        (V3IndustryType.RETAIL, "retail"),
        (V3IndustryType.PUBLIC, "public"),
        (V3IndustryType.EDUCATION, "education"),
        (V3IndustryType.OTHER, "other"),
    ],
)
def test_v3_industry_member_resolves_to_matching_slug(
    industry_type: V3IndustryType, expected_slug: str
) -> None:
    """Known-mapped V3 enum members slug to their canonical strings."""
    assert org_industry_slug(industry_type) == expected_slug


@pytest.mark.parametrize(
    "unmapped_industry",
    [
        V3IndustryType.AGRICULTURE,
        V3IndustryType.MINING,
        V3IndustryType.CONSTRUCTION,
        V3IndustryType.TRADE,
        V3IndustryType.TRANSPORTATION,
        V3IndustryType.REAL_ESTATE,
        V3IndustryType.PROFESSIONAL,
        V3IndustryType.MANAGEMENT,
        V3IndustryType.ADMINISTRATIVE,
        V3IndustryType.ENTERTAINMENT,
        V3IndustryType.HOSPITALITY,
    ],
)
def test_v3_industry_not_in_v3_to_fc_map_falls_back_to_other(
    unmapped_industry: V3IndustryType,
) -> None:
    """V3 members without a fair_cam counterpart fall through to "other"."""
    assert org_industry_slug(unmapped_industry) == "other"


def test_none_input_returns_other() -> None:
    assert org_industry_slug(None) == "other"


def test_raw_string_input_resolves_correctly() -> None:
    """Defensive: callers occasionally pass the raw stored string."""
    assert org_industry_slug("manufacturing") == "manufacturing"
    assert org_industry_slug("healthcare") == "healthcare"


def test_fc_member_passed_directly_also_resolves() -> None:
    """Symmetry: a fair_cam member should also slug correctly."""
    assert org_industry_slug(FCIndustryType.MANUFACTURING) == "manufacturing"
