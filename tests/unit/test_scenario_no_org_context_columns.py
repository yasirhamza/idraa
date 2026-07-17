"""Regression guard: Scenario must NOT have industry / revenue_tier columns.

Issue #88: those were denormalized snapshots that went stale. They're
gone permanently — this test ensures they don't get reintroduced.
"""

from __future__ import annotations

from idraa.models.scenario import Scenario


def test_scenario_has_no_industry_column() -> None:
    assert "industry" not in Scenario.__table__.columns


def test_scenario_has_no_revenue_tier_column() -> None:
    assert "revenue_tier" not in Scenario.__table__.columns
