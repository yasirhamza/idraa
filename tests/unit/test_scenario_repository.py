"""ScenarioRepo: get_for_org IDOR boundary, list pagination, filters."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any as _Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import ControlNotFoundForRunError, ScenarioNotFoundError
from idraa.models.control import Control
from idraa.models.enums import (
    ControlType,
    EntityStatus,
    IndustryType,
    OrganizationSize,
    ScenarioType,
    ThreatCategory,
)
from idraa.models.organization import Organization
from idraa.models.scenario import Scenario
from idraa.models.scenario_control import ScenarioControl
from idraa.repositories.scenario_repo import ScenarioRepo


def _seed_scenario(
    db_session: AsyncSession,
    *,
    org_id: uuid.UUID,
    name: str,
    status: EntityStatus = EntityStatus.ACTIVE,
) -> Scenario:
    s = Scenario(
        organization_id=org_id,
        name=name,
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss={"distribution": "PERT", "low": 50_000, "mode": 250_000, "high": 2_000_000},
        status=status,
    )
    db_session.add(s)
    return s


async def test_get_for_org_returns_row(db_session: AsyncSession) -> None:
    org = Organization(
        name="A",
        industry_type=IndustryType.MANUFACTURING,
        organization_size=OrganizationSize.MEDIUM,
    )
    db_session.add(org)
    await db_session.flush()
    s = _seed_scenario(db_session, org_id=org.id, name="RW")
    await db_session.flush()

    repo = ScenarioRepo(db_session)
    result = await repo.get_for_org(organization_id=org.id, scenario_id=s.id)
    assert result is not None
    assert result.id == s.id
    assert result.name == "RW"


async def test_get_for_org_returns_none_for_other_org(db_session: AsyncSession) -> None:
    """IDOR boundary: a scenario in org A is not visible to org B."""
    org_a = Organization(
        name="A",
        industry_type=IndustryType.MANUFACTURING,
        organization_size=OrganizationSize.MEDIUM,
    )
    org_b = Organization(
        name="B",
        industry_type=IndustryType.INFORMATION,
        organization_size=OrganizationSize.SMALL,
    )
    db_session.add_all([org_a, org_b])
    await db_session.flush()
    s = _seed_scenario(db_session, org_id=org_a.id, name="A's RW")
    await db_session.flush()

    repo = ScenarioRepo(db_session)
    result = await repo.get_for_org(organization_id=org_b.id, scenario_id=s.id)
    assert result is None


async def test_get_for_org_returns_none_for_unknown_id(db_session: AsyncSession) -> None:
    org = Organization(
        name="A",
        industry_type=IndustryType.MANUFACTURING,
        organization_size=OrganizationSize.MEDIUM,
    )
    db_session.add(org)
    await db_session.flush()

    repo = ScenarioRepo(db_session)
    result = await repo.get_for_org(organization_id=org.id, scenario_id=uuid.uuid4())
    assert result is None


async def test_list_for_org_returns_only_org_rows(db_session: AsyncSession) -> None:
    org_a = Organization(
        name="A",
        industry_type=IndustryType.MANUFACTURING,
        organization_size=OrganizationSize.MEDIUM,
    )
    org_b = Organization(
        name="B",
        industry_type=IndustryType.INFORMATION,
        organization_size=OrganizationSize.SMALL,
    )
    db_session.add_all([org_a, org_b])
    await db_session.flush()
    _seed_scenario(db_session, org_id=org_a.id, name="A1")
    _seed_scenario(db_session, org_id=org_a.id, name="A2")
    _seed_scenario(db_session, org_id=org_b.id, name="B1")
    await db_session.flush()

    repo = ScenarioRepo(db_session)
    rows, total = await repo.list_for_org(organization_id=org_a.id, limit=10, offset=0)
    assert total == 2
    assert {r.name for r in rows} == {"A1", "A2"}


async def test_list_for_org_returns_all_scenarios(db_session: AsyncSession) -> None:
    """Per-scenario industry column removed (issue #88); list_for_org returns all
    scenarios for the org without an industry filter."""
    org = Organization(
        name="A",
        industry_type=IndustryType.MANUFACTURING,
        organization_size=OrganizationSize.MEDIUM,
    )
    db_session.add(org)
    await db_session.flush()
    _seed_scenario(db_session, org_id=org.id, name="MFG")
    _seed_scenario(db_session, org_id=org.id, name="TECH")
    await db_session.flush()

    repo = ScenarioRepo(db_session)
    rows, total = await repo.list_for_org(
        organization_id=org.id,
        limit=10,
        offset=0,
    )
    assert total == 2
    names = {r.name for r in rows}
    assert names == {"MFG", "TECH"}


async def test_list_for_org_pagination(db_session: AsyncSession) -> None:
    org = Organization(
        name="A",
        industry_type=IndustryType.MANUFACTURING,
        organization_size=OrganizationSize.MEDIUM,
    )
    db_session.add(org)
    await db_session.flush()
    for i in range(5):
        _seed_scenario(db_session, org_id=org.id, name=f"S{i}")
    await db_session.flush()

    repo = ScenarioRepo(db_session)
    page1, total = await repo.list_for_org(organization_id=org.id, limit=2, offset=0)
    page2, _ = await repo.list_for_org(organization_id=org.id, limit=2, offset=2)
    assert total == 5
    assert len(page1) == 2
    assert len(page2) == 2
    assert {r.id for r in page1}.isdisjoint({r.id for r in page2})


async def test_list_for_org_filter_by_status_excludes_deleted(
    db_session: AsyncSession,
) -> None:
    org = Organization(
        name="A",
        industry_type=IndustryType.MANUFACTURING,
        organization_size=OrganizationSize.MEDIUM,
    )
    db_session.add(org)
    await db_session.flush()
    _seed_scenario(db_session, org_id=org.id, name="A", status=EntityStatus.ACTIVE)
    _seed_scenario(db_session, org_id=org.id, name="D", status=EntityStatus.DELETED)
    await db_session.flush()

    repo = ScenarioRepo(db_session)
    rows, total = await repo.list_for_org(
        organization_id=org.id,
        status=EntityStatus.ACTIVE,
        limit=10,
        offset=0,
    )
    assert total == 1
    assert rows[0].name == "A"


@pytest.mark.asyncio
async def test_set_mitigating_controls_inserts(
    db_session: AsyncSession,
    seed_scenario_with_no_controls: Scenario,
    seed_control_factory: Callable[..., Awaitable[Control]],
) -> None:
    """Empty starting state → new control_ids inserted."""
    scenario = seed_scenario_with_no_controls
    c1 = await seed_control_factory(name="A")
    c2 = await seed_control_factory(name="B")

    repo = ScenarioRepo(db_session)
    await repo.set_mitigating_controls(
        scenario_id=scenario.id,
        organization_id=scenario.organization_id,
        control_ids=[c1.id, c2.id],
    )
    await db_session.commit()
    await db_session.refresh(scenario, attribute_names=["mitigating_controls"])
    names = sorted(c.name for c in scenario.mitigating_controls)
    assert names == ["A", "B"]


@pytest.mark.asyncio
async def test_set_mitigating_controls_diff_apply(
    db_session: AsyncSession,
    seed_scenario_with_no_controls: Scenario,
    seed_control_factory: Callable[..., Awaitable[Control]],
) -> None:
    """Existing controls are diffed: removed are deleted, added are inserted."""
    scenario = seed_scenario_with_no_controls
    c_keep = await seed_control_factory(name="Keep")
    c_drop = await seed_control_factory(name="Drop")
    c_add = await seed_control_factory(name="Add")

    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=c_keep.id))
    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=c_drop.id))
    await db_session.commit()

    repo = ScenarioRepo(db_session)
    await repo.set_mitigating_controls(
        scenario_id=scenario.id,
        organization_id=scenario.organization_id,
        control_ids=[c_keep.id, c_add.id],
    )
    await db_session.commit()
    await db_session.refresh(scenario, attribute_names=["mitigating_controls"])
    names = sorted(c.name for c in scenario.mitigating_controls)
    assert names == ["Add", "Keep"]


@pytest.mark.asyncio
async def test_set_mitigating_controls_rejects_cross_org(
    db_session: AsyncSession,
    seed_scenario_with_no_controls: Scenario,
    seed_control_factory: Callable[..., Awaitable[Control]],
    seed_organization_factory: Callable[..., Awaitable[Organization]],
    seed_user: _Any,
) -> None:
    """A control_id belonging to a different org raises ControlNotFoundForRunError."""
    scenario = seed_scenario_with_no_controls
    from datetime import UTC, datetime

    from idraa.models.control_function_assignment import ControlFunctionAssignment
    from idraa.models.enums import FairCamSubFunction

    other_org = await seed_organization_factory(name="other-org-set-controls")
    foreign_ctrl = Control(
        id=uuid.uuid4(),
        organization_id=other_org.id,
        name="Foreign",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),
        nist_csf_functions=[],
        iso_27001_domains=[],
        compliance_mappings={},
        skill_requirements=[],
        technology_dependencies=[],
        applicable_industries=[],
        applicable_org_sizes=[],
        status=EntityStatus.ACTIVE,
        version="1.0",
    )
    db_session.add(foreign_ctrl)
    await db_session.flush()

    db_session.add(
        ControlFunctionAssignment(
            control_id=foreign_ctrl.id,
            organization_id=other_org.id,
            sub_function=FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
            capability_value=0.5,
            coverage=0.5,
            reliability=0.5,
            confirmed_by_user_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    repo = ScenarioRepo(db_session)
    with pytest.raises(ControlNotFoundForRunError):
        await repo.set_mitigating_controls(
            scenario_id=scenario.id,
            organization_id=scenario.organization_id,
            control_ids=[foreign_ctrl.id],
        )


@pytest.mark.asyncio
async def test_get_for_org_or_raise_raises_on_miss(
    db_session: AsyncSession,
    seed_organization: Organization,
) -> None:
    with pytest.raises(ScenarioNotFoundError):
        await ScenarioRepo(db_session).get_for_org_or_raise(
            seed_organization.id,
            uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_get_for_org_or_raise_returns_scenario(
    db_session: AsyncSession,
    seed_scenario_with_no_controls: Scenario,
    seed_organization: Organization,
) -> None:
    found = await ScenarioRepo(db_session).get_for_org_or_raise(
        seed_organization.id,
        seed_scenario_with_no_controls.id,
    )
    assert found.id == seed_scenario_with_no_controls.id
