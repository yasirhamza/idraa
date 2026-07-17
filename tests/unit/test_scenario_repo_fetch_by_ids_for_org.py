"""Unit tests for ScenarioRepo.fetch_by_ids_for_org (PR xi F4)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.repositories.scenario_repo import ScenarioRepo


@pytest.mark.asyncio
async def test_fetch_by_ids_for_org_returns_matching_scenarios(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_scenario_factory: Any,
) -> None:
    """Returns scenarios that match BOTH org AND id filter."""
    s1 = await seed_scenario_factory(name="s1")
    s2 = await seed_scenario_factory(name="s2")
    repo = ScenarioRepo(db_session)
    result = await repo.fetch_by_ids_for_org(seed_organization.id, [s1.id, s2.id])
    assert len(result) == 2
    assert {s.id for s in result} == {s1.id, s2.id}


@pytest.mark.asyncio
async def test_fetch_by_ids_for_org_silently_rejects_cross_org(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_scenario_factory: Any,
    seed_organization_factory: Any,
    seed_user: Any,
) -> None:
    """Cross-org scenario IDs return empty (no error, no leak)."""
    s_a = await seed_scenario_factory(name="s_org_a")  # in seed_organization
    org_b = await seed_organization_factory(name="org-b")
    s_b = await seed_scenario_factory(
        name="s_org_b", organization_id=org_b.id, created_by=seed_user.id
    )
    repo = ScenarioRepo(db_session)
    # Query for s_b's id from org A's perspective: returns empty
    result = await repo.fetch_by_ids_for_org(seed_organization.id, [s_b.id])
    assert result == []
    # Sanity: s_a is found
    result_a = await repo.fetch_by_ids_for_org(seed_organization.id, [s_a.id])
    assert len(result_a) == 1


@pytest.mark.asyncio
async def test_fetch_by_ids_for_org_refreshes_mitigating_controls_after_m2m_insert(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
) -> None:
    """Issue #101 regression: identity-map staleness around M2M rows.

    When a Scenario sits in the session's identity map with an empty
    ``mitigating_controls`` (loaded before any ScenarioControl rows
    existed), a follow-up ``fetch_by_ids_for_org`` must re-fire its
    selectinload and reflect newly-committed M2M rows — not return the
    cached empty list.
    """
    from idraa.models.scenario_control import ScenarioControl

    s = await seed_scenario_factory(name="s-issue-101")
    ctrl = await seed_control_factory(name="ctrl-issue-101")
    repo = ScenarioRepo(db_session)

    first = await repo.fetch_by_ids_for_org(seed_organization.id, [s.id])
    assert len(first) == 1
    assert first[0].mitigating_controls == []

    db_session.add(ScenarioControl(scenario_id=s.id, control_id=ctrl.id))
    await db_session.commit()

    second = await repo.fetch_by_ids_for_org(seed_organization.id, [s.id])
    assert len(second) == 1
    assert len(second[0].mitigating_controls) == 1, (
        "selectinload returned cached empty mitigating_controls; "
        "fetch_by_ids_for_org must refresh on re-query"
    )
    assert second[0].mitigating_controls[0].id == ctrl.id
