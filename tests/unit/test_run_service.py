"""RunService: orchestrates run lifecycle.

create_and_dispatch validates inputs, freezes them, persists QUEUED row
+ audit, then dispatches sync (mc_iterations<1000) or async (>=1000)
via FastAPI BackgroundTasks.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import (
    RunNotFoundError,
    RunValidationError,
    ScenarioNotFoundError,
)
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.services.runs import RunService


@pytest.mark.asyncio
async def test_create_and_dispatch_sync_path_completes(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    wire_executor_to_test_db: None,  # required for executor session sharing
) -> None:
    """mc_iterations<1000 → synchronous execution; returned run is COMPLETED."""
    bg = BackgroundTasks()
    service = RunService(db_session)
    run = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=[seed_scenario_with_controls.id],
        mc_iterations_override=200,
        created_by=seed_user.id,
        background_tasks=bg,
    )
    await db_session.refresh(run)
    assert run.status == RunStatus.COMPLETED
    assert run.simulation_results is not None
    assert len(bg.tasks) == 0  # sync path: no BG tasks queued


@pytest.mark.asyncio
async def test_create_and_dispatch_async_path_queues_bg_task(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
) -> None:
    """mc_iterations>=1000 → BG task queued; returned run is QUEUED."""
    bg = BackgroundTasks()
    service = RunService(db_session)
    run = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=[seed_scenario_with_controls.id],
        mc_iterations_override=1000,
        created_by=seed_user.id,
        background_tasks=bg,
    )
    assert run.status == RunStatus.QUEUED
    assert len(bg.tasks) == 1


@pytest.mark.asyncio
async def test_create_and_dispatch_requires_mc_iterations(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
) -> None:
    """PR π: mc_iterations_override=None is no longer a valid input.

    The Scenario.mc_iterations fallback was dropped in PR π (Scenario.mc_iterations
    is being removed in F14). Callers must pass an explicit iteration count.
    """
    bg = BackgroundTasks()
    service = RunService(db_session)
    with pytest.raises(RunValidationError, match="mc_iterations"):
        await service.create_and_dispatch(
            organization_id=seed_organization.id,
            scenario_ids=[seed_scenario_with_controls.id],
            mc_iterations_override=None,
            created_by=seed_user.id,
            background_tasks=bg,
        )


@pytest.mark.asyncio
async def test_create_and_dispatch_validates_iteration_range(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
) -> None:
    """mc_iterations<100 OR >mc_iterations_max → RunValidationError.

    Issue #259: the upper bound is the Settings.mc_iterations_max OOM cap
    (default 100_000), not a hardcoded 1M ceiling. 2_000_000 is above the cap.
    """
    bg = BackgroundTasks()
    service = RunService(db_session)
    with pytest.raises(RunValidationError):
        await service.create_and_dispatch(
            organization_id=seed_organization.id,
            scenario_ids=[seed_scenario_with_controls.id],
            mc_iterations_override=50,
            created_by=seed_user.id,
            background_tasks=bg,
        )

    with pytest.raises(RunValidationError):
        await service.create_and_dispatch(
            organization_id=seed_organization.id,
            scenario_ids=[seed_scenario_with_controls.id],
            mc_iterations_override=2_000_000,
            created_by=seed_user.id,
            background_tasks=bg,
        )


@pytest.mark.asyncio
async def test_create_and_dispatch_rejects_unknown_scenario(
    db_session: AsyncSession,
    seed_user: User,
    seed_organization: Organization,
) -> None:
    bg = BackgroundTasks()
    service = RunService(db_session)
    with pytest.raises(ScenarioNotFoundError):
        await service.create_and_dispatch(
            organization_id=seed_organization.id,
            scenario_ids=[uuid.uuid4()],
            mc_iterations_override=200,
            created_by=seed_user.id,
            background_tasks=bg,
        )


@pytest.mark.asyncio
async def test_cancel_idempotent_on_terminal(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
) -> None:
    """Cancel on already-COMPLETED run is a no-op (returns the row)."""
    bg = BackgroundTasks()
    service = RunService(db_session)
    run = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=[seed_scenario_with_controls.id],
        mc_iterations_override=200,
        created_by=seed_user.id,
        background_tasks=bg,
    )
    await db_session.refresh(run)
    assert run.status == RunStatus.COMPLETED

    after = await service.cancel(
        organization_id=seed_organization.id,
        run_id=run.id,
        cancelled_by=seed_user.id,
    )
    assert after.status == RunStatus.COMPLETED  # unchanged


@pytest.mark.asyncio
async def test_cancel_flips_queued_to_cancelled(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
) -> None:
    """Cancel on QUEUED run flips status."""
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=seed_organization.id,
        scenario_id=seed_scenario_with_controls.id,
        mc_iterations=10000,
        inputs_hash="m" * 64,
        controls_snapshot=[],
        control_ids_used=[],
        status=RunStatus.QUEUED,
        created_by=seed_user.id,
    )
    db_session.add(run)
    await db_session.commit()

    service = RunService(db_session)
    after = await service.cancel(
        organization_id=seed_organization.id,
        run_id=run.id,
        cancelled_by=seed_user.id,
    )
    assert after.status == RunStatus.CANCELLED
    assert after.completed_at is not None


@pytest.mark.asyncio
async def test_cancel_raises_on_unknown(
    db_session: AsyncSession,
    seed_user: User,
    seed_organization: Organization,
) -> None:
    service = RunService(db_session)
    with pytest.raises(RunNotFoundError):
        await service.cancel(
            organization_id=seed_organization.id,
            run_id=uuid.uuid4(),
            cancelled_by=seed_user.id,
        )


@pytest.mark.asyncio
async def test_list_history_returns_paginated(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
) -> None:
    bg = BackgroundTasks()
    service = RunService(db_session)
    for _ in range(3):
        await service.create_and_dispatch(
            organization_id=seed_organization.id,
            scenario_ids=[seed_scenario_with_controls.id],
            mc_iterations_override=200,
            created_by=seed_user.id,
            background_tasks=bg,
        )

    rows, total = await service.list_history(
        organization_id=seed_organization.id,
        scenario_id=seed_scenario_with_controls.id,
        page=1,
        page_size=10,
    )
    assert total == 3
    assert len(rows) == 3


# D14: mc_iterations exactly at the Settings cap — must be accepted
@pytest.mark.asyncio
async def test_create_and_dispatch_accepts_mc_iterations_at_upper_bound(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
) -> None:
    """mc_iterations == Settings.mc_iterations_max exactly must not raise.

    Issue #259: the upper bound is now the Settings OOM cap, not a hardcoded 1M.
    """
    from idraa.config import get_settings

    iter_max = get_settings().mc_iterations_max
    bg = BackgroundTasks()
    service = RunService(db_session)
    # At the upper bound: should NOT raise RunValidationError.
    # The run will be queued (not executed inline), so we don't need
    # wire_executor_to_test_db.
    run = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=[seed_scenario_with_controls.id],
        mc_iterations_override=iter_max,
        created_by=seed_user.id,
        background_tasks=bg,
    )
    assert run.mc_iterations == iter_max
    # At >= _SYNC_THRESHOLD the run is queued, not executed inline.
    from idraa.models.risk_analysis_run import RunStatus

    assert run.status == RunStatus.QUEUED


# D15: mc_iterations one above the Settings cap — must be rejected
@pytest.mark.asyncio
async def test_create_and_dispatch_rejects_mc_iterations_above_max(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
) -> None:
    """Issue #259: mc_iterations == mc_iterations_max + 1 must raise.

    This is the service-boundary half of the legacy POST /scenarios/{id}/run
    bypass fix: every dispatch path now shares the one Settings cap gate.
    """
    from idraa.config import get_settings

    iter_max = get_settings().mc_iterations_max
    bg = BackgroundTasks()
    service = RunService(db_session)
    with pytest.raises(RunValidationError):
        await service.create_and_dispatch(
            organization_id=seed_organization.id,
            scenario_ids=[seed_scenario_with_controls.id],
            mc_iterations_override=iter_max + 1,
            created_by=seed_user.id,
            background_tasks=bg,
        )


# D16: mc_iterations below lower bound (99 < _MIN_ITERATIONS=100) — must be rejected
@pytest.mark.asyncio
async def test_create_and_dispatch_rejects_mc_iterations_below_min(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
) -> None:
    """mc_iterations == _MIN_ITERATIONS - 1 (99) must raise RunValidationError."""
    from idraa.services.runs import _MIN_ITERATIONS

    bg = BackgroundTasks()
    service = RunService(db_session)
    with pytest.raises(RunValidationError):
        await service.create_and_dispatch(
            organization_id=seed_organization.id,
            scenario_ids=[seed_scenario_with_controls.id],
            mc_iterations_override=_MIN_ITERATIONS - 1,
            created_by=seed_user.id,
            background_tasks=bg,
        )
