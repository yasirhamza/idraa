"""RunRepo: CRUD + IDOR-guarded reads + paginated history queries."""

from __future__ import annotations

import datetime
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from typing import Any as _Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import RunNotFoundError
from idraa.models.risk_analysis_run import (
    RiskAnalysisRun,
    RunStatus,
    RunType,
)
from idraa.repositories.run_repo import RunRepo


@pytest.fixture
async def seed_run_factory(
    db_session: AsyncSession,
    seed_scenario_with_no_controls: _Any,
    seed_user: _Any,
    seed_organization: _Any,
) -> Callable[..., Awaitable[RiskAnalysisRun]]:
    async def _factory(
        *,
        scenario: _Any = None,
        status: RunStatus = RunStatus.QUEUED,
        mc_iterations: int = 1000,
        organization: _Any = None,
        created_at: datetime.datetime | None = None,
    ) -> RiskAnalysisRun:
        scenario = scenario or seed_scenario_with_no_controls
        organization = organization or seed_organization
        run = RiskAnalysisRun(
            id=uuid.uuid4(),
            organization_id=organization.id,
            scenario_id=scenario.id,
            mc_iterations=mc_iterations,
            inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            controls_snapshot=[],
            control_ids_used=[],
            status=status,
            run_type=RunType.SINGLE,
            created_by=seed_user.id,
        )
        if created_at is not None:
            run.created_at = created_at
        db_session.add(run)
        await db_session.commit()
        await db_session.refresh(run)
        return run

    return _factory


@pytest.mark.asyncio
async def test_create_persists_run(
    db_session: AsyncSession,
    seed_run_factory: Callable[..., Awaitable[RiskAnalysisRun]],
) -> None:
    run = await seed_run_factory()
    fresh = await db_session.get(RiskAnalysisRun, run.id)
    assert fresh is not None
    assert fresh.status == RunStatus.QUEUED


@pytest.mark.asyncio
async def test_get_for_org_returns_run(
    db_session: AsyncSession,
    seed_run_factory: Callable[..., Awaitable[RiskAnalysisRun]],
    seed_organization: _Any,
) -> None:
    run = await seed_run_factory()
    found = await RunRepo(db_session).get_for_org(seed_organization.id, run.id)
    assert found is not None
    assert found.id == run.id


@pytest.mark.asyncio
async def test_get_for_org_idor_guard(
    db_session: AsyncSession,
    seed_run_factory: Callable[..., Awaitable[RiskAnalysisRun]],
    seed_organization_factory: Callable[..., Awaitable[_Any]],
) -> None:
    """Cross-org request returns None (NOT raises)."""
    run = await seed_run_factory()
    other_org = await seed_organization_factory(name="other-runrepo")
    found = await RunRepo(db_session).get_for_org(other_org.id, run.id)
    assert found is None


@pytest.mark.asyncio
async def test_get_for_org_or_raise_raises_on_miss(
    db_session: AsyncSession,
    seed_organization: _Any,
) -> None:
    nonexistent = uuid.uuid4()
    with pytest.raises(RunNotFoundError):
        await RunRepo(db_session).get_for_org_or_raise(
            seed_organization.id,
            nonexistent,
        )


@pytest.mark.asyncio
async def test_list_for_scenario_pagination(
    db_session: AsyncSession,
    seed_run_factory: Callable[..., Awaitable[RiskAnalysisRun]],
    seed_scenario_with_no_controls: _Any,
    seed_organization: _Any,
) -> None:
    """Returns runs ordered by created_at DESC; honours limit + offset."""
    # Seed with explicit created_at values for deterministic DESC ordering
    base = datetime.datetime(2026, 4, 1, tzinfo=datetime.UTC)
    runs = []
    for i in range(5):
        r = await seed_run_factory(created_at=base + datetime.timedelta(hours=i))
        runs.append(r)

    page1 = await RunRepo(db_session).list_for_scenario(
        seed_organization.id,
        seed_scenario_with_no_controls.id,
        limit=3,
        offset=0,
    )
    assert len(page1) == 3

    page2 = await RunRepo(db_session).list_for_scenario(
        seed_organization.id,
        seed_scenario_with_no_controls.id,
        limit=3,
        offset=3,
    )
    assert len(page2) == 2

    # Verify DESC ordering: page1 newest first
    assert page1[0].created_at >= page1[-1].created_at
    # Verify page2 contains the older runs (offset 3 of 5 sorted DESC)
    assert page2[0].created_at <= page1[-1].created_at


@pytest.mark.asyncio
async def test_list_for_scenario_idor_guard(
    db_session: AsyncSession,
    seed_run_factory: Callable[..., Awaitable[RiskAnalysisRun]],
    seed_scenario_with_no_controls: _Any,
    seed_organization_factory: Callable[..., Awaitable[_Any]],
) -> None:
    """Listing under a different org returns empty."""
    await seed_run_factory()
    other_org = await seed_organization_factory(name="other-listrun")
    page = await RunRepo(db_session).list_for_scenario(
        other_org.id,
        seed_scenario_with_no_controls.id,
        limit=10,
        offset=0,
    )
    assert page == []


@pytest.mark.asyncio
async def test_count_for_scenario(
    db_session: AsyncSession,
    seed_run_factory: Callable[..., Awaitable[RiskAnalysisRun]],
    seed_scenario_with_no_controls: _Any,
    seed_organization: _Any,
) -> None:
    for _ in range(3):
        await seed_run_factory()
    count = await RunRepo(db_session).count_for_scenario(
        seed_organization.id,
        seed_scenario_with_no_controls.id,
    )
    assert count == 3
