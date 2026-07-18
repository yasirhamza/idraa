"""Integration test for services/dashboard.build_dashboard (omicron-1).

Uses authed_admin: tuple[AsyncClient, uuid.UUID] for the org-id (NOT the
standalone `organization` fixture, which would create a second org and
make require_sole_org nondeterministic). Scenario rows are built via
_make_scenario (NOT seed_scenario_factory which is bound to a different
org).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.enums import (
    AssetClass,
    ControlType,
    EntityStatus,
    IndustrySubSector,
    ThreatActorType,
    ThreatCategory,
)
from idraa.models.framework_crosswalk import FrameworkControl
from idraa.models.organization import Organization
from idraa.models.scenario_library import ScenarioLibraryEntry
from idraa.services.dashboard import DashboardData, build_dashboard
from tests.integration._dashboard_fixtures import (
    _make_completed_aggregate_run,
    _make_completed_single_run,
    _make_scenario,
)


async def test_build_dashboard_cold_start_returns_empty_data(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    _, org_id = authed_admin
    org = (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one()

    data = await build_dashboard(db_session, org)
    assert isinstance(data, DashboardData)
    assert data.latest_aggregate is None
    assert data.latest_aggregate_label is None
    assert data.recent_runs == []
    assert data.top_scenarios == []
    assert data.dual_lec is None
    assert data.control_value is None
    assert data.residual_ale is None
    assert data.total_scenarios_with_runs == 0


async def test_build_dashboard_with_aggregate_run_populates_all_cards(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    _, org_id = authed_admin
    org = (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one()
    org.annual_revenue = Decimal("100000000")
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    run = _make_completed_aggregate_run(
        org_id=org.id,
        name="My Portfolio",
        scenario_ids=[s1, s2],
        ale_with_controls=100_000.0,
        ale_without_controls=500_000.0,
        control_value_dollars=400_000.0,
        control_value_percent=80.0,
    )
    db_session.add(run)
    await db_session.flush()

    data = await build_dashboard(db_session, org)
    assert data.latest_aggregate is not None
    assert data.latest_aggregate.id == run.id
    assert data.latest_aggregate_label == "My Portfolio"  # uses run.name
    assert data.control_value == {"dollars": 400_000.0, "percent": 80.0}
    assert data.residual_ale is not None
    assert data.residual_ale["value"] == 100_000.0
    assert abs(data.residual_ale["pct_revenue"] - 0.10) < 1e-9
    assert data.dual_lec is not None
    assert "with_controls" in data.dual_lec
    assert "without_controls" in data.dual_lec
    assert len(data.recent_runs) == 1
    assert data.recent_runs[0].headline_ale == 100_000.0


async def test_build_dashboard_single_only_uses_fallback_with_real_scenario_names(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Critical: top_scenarios on the fallback path must label by the
    actual Scenario.name, not by the run name (which is a different field
    after Q15).

    Uses _make_scenario (NOT seed_scenario_factory) because the latter is
    bound to seed_organization, which is a DIFFERENT org from the one
    authed_admin authenticates against."""
    _, org_id = authed_admin
    org = (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one()

    s1 = _make_scenario(org_id=org.id, name="Ransomware Q2")
    s2 = _make_scenario(org_id=org.id, name="Insider Threat")
    db_session.add_all([s1, s2])
    await db_session.flush()  # populate scenarios before runs FK
    db_session.add_all(
        [
            _make_completed_single_run(
                org_id=org.id, name="r1", scenario_id=s1.id, residual_ale=300.0
            ),
            _make_completed_single_run(
                org_id=org.id, name="r2", scenario_id=s2.id, residual_ale=700.0
            ),
        ]
    )
    await db_session.flush()

    data = await build_dashboard(db_session, org)
    assert data.latest_aggregate is None
    assert data.dual_lec is None
    assert data.control_value is None
    assert len(data.top_scenarios) == 2
    # Top by residual ALE: 700 → "Insider Threat", 300 → "Ransomware Q2"
    assert data.top_scenarios[0].scenario_name == "Insider Threat"
    assert data.top_scenarios[1].scenario_name == "Ransomware Q2"
    # Critical: scenario_name is the actual scenario, NOT the run name
    assert data.top_scenarios[0].scenario_name != "r2"
    assert data.top_scenarios[1].scenario_name != "r1"


def _make_library_entry(
    *,
    slug: str,
    sub_sectors: list[str] | None,
) -> ScenarioLibraryEntry:
    """Minimal-valid published ScenarioLibraryEntry, mirroring conftest's
    seed_library_entry required-field set (Task 3 DB-wiring integration
    coverage, #478 fix-report gap)."""
    return ScenarioLibraryEntry(
        id=uuid.uuid4(),
        version=1,
        slug=slug,
        name=slug,
        status="published",
        threat_event_type=ThreatCategory.RANSOMWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="d",
        canonical_fair_gap="g",
        source_citations=[],
        applicable_sub_sectors=sub_sectors,
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={
            "distribution": "PERT",
            "low": 100_000.0,
            "mode": 750_000.0,
            "high": 5_000_000.0,
        },
        suggested_control_ids=[],
    )


async def test_build_dashboard_wires_budget_control_and_scenario_coverage(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Task 3 (#478) DB-wiring integration test — pins the JSON-extract
    query in ScenarioRepo.list_pinned_library_entry_ids_for_org, the
    Organization.industry_sub_sector enum/string handling in
    ScenarioLibraryRepo.list_published, and CrosswalkService.codes_for's
    version resolution, all exercised together through build_dashboard.

    Numbers mirror the SWE reviewer's probe: spend 2,670,000 against a
    3,500,000 budget -> ratio ~= 0.763.
    """
    _, org_id = authed_admin
    org = (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one()
    org.annual_security_budget = Decimal("3500000")
    org.industry_sub_sector = IndustrySubSector.WATER_UTILITY

    control = Control(
        organization_id=org.id,
        name="EDR Platform",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("2670000"),
        nist_csf_functions=["PR.AC-7"],
    )
    db_session.add(control)

    framework_control = FrameworkControl(
        framework="nist_csf",
        framework_version="1.1",
        code="PR.AC-7",
        title="Access Permissions Management",
        description=None,
        asset_type=None,
        security_function=None,
        citation={"source": "FAIR Institute"},
    )
    db_session.add(framework_control)

    entry = _make_library_entry(slug="water-scada-ransomware", sub_sectors=["water_utility"])
    db_session.add(entry)
    await db_session.flush()  # entry.id/version stable before use in library_pin

    scenario = _make_scenario(org_id=org.id, name="Water Utility SCADA Ransomware")
    scenario.library_pin = {"entry_id": str(entry.id), "version": entry.version}
    db_session.add(scenario)
    await db_session.flush()

    data = await build_dashboard(db_session, org)

    # --- budget card: Sigma(annual_cost) vs org.annual_security_budget ---
    assert data.budget["spend"] == 2_670_000.0
    assert data.budget["budget"] == 3_500_000.0
    assert data.budget["ratio"] == pytest.approx(2_670_000.0 / 3_500_000.0)
    assert data.budget["headroom"] == pytest.approx(830_000.0)

    # --- control coverage: nist_csf 1/1 via CrosswalkService.codes_for ---
    nist_csf = next(f for f in data.control_coverage["frameworks"] if f["name"] == "nist_csf")
    assert nist_csf["coverage"].covered_count == 1
    assert nist_csf["coverage"].reference_count == 1
    assert nist_csf["coverage"].ratio == pytest.approx(1.0)
    assert nist_csf["coverage"].present == ["PR.AC-7"]

    # --- scenario coverage: 1 pinned entry out of 1 sector-applicable entry ---
    assert data.scenario_coverage.covered_count == 1
    assert data.scenario_coverage.reference_count == 1
    assert data.scenario_coverage.ratio == pytest.approx(1.0)


async def test_build_dashboard_scenario_coverage_counts_unpinned_reference_entries(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Reference set is ALL published sector-applicable library entries, not
    just the ones the org happens to have pinned — a second unpinned entry
    must widen the denominator without affecting the numerator."""
    _, org_id = authed_admin
    org = (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one()
    org.industry_sub_sector = IndustrySubSector.WATER_UTILITY

    pinned_entry = _make_library_entry(slug="water-pinned", sub_sectors=["water_utility"])
    unpinned_entry = _make_library_entry(slug="water-unpinned", sub_sectors=["water_utility"])
    db_session.add_all([pinned_entry, unpinned_entry])
    await db_session.flush()

    scenario = _make_scenario(org_id=org.id, name="Pinned Scenario")
    scenario.library_pin = {"entry_id": str(pinned_entry.id), "version": pinned_entry.version}
    db_session.add(scenario)
    await db_session.flush()

    data = await build_dashboard(db_session, org)

    assert data.scenario_coverage.covered_count == 1
    assert data.scenario_coverage.reference_count == 2
    assert data.scenario_coverage.ratio == pytest.approx(0.5)


async def test_build_dashboard_scenario_count_and_coverage_exclude_drafts(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Epic #34 P1a Task 2: DRAFT scenarios are review-pending priors —
    excluded from both scenario_count (ACTIVE-only) and scenario_coverage's
    covered_count (a draft's library_pin must not count as coverage until
    the scenario is promoted)."""
    _, org_id = authed_admin
    org = (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one()
    org.industry_sub_sector = IndustrySubSector.WATER_UTILITY

    entry = _make_library_entry(slug="water-draft-pin", sub_sectors=["water_utility"])
    db_session.add(entry)
    await db_session.flush()

    draft = _make_scenario(org_id=org.id, name="Draft Pinned Scenario")
    draft.status = EntityStatus.DRAFT
    draft.library_pin = {"entry_id": str(entry.id), "version": entry.version}
    db_session.add(draft)
    await db_session.flush()

    data = await build_dashboard(db_session, org)

    assert data.scenario_count == 0
    assert data.scenario_coverage.covered_count == 0
    assert data.scenario_coverage.reference_count == 1
