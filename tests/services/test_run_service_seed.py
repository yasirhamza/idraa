"""Tests: random_seed threading through RunService.create_and_dispatch.

Validates:
- Out-of-range seed values raise RunValidationError.
- Seed is persisted on the returned run row.
- Omitting random_seed defaults to 42 (back-compat for existing callers).
"""

from __future__ import annotations

import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import RunValidationError
from idraa.models.organization import Organization
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.services.runs import RunService


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [-1, 2**32, 2**40])
async def test_seed_out_of_range_rejected(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    bad: int,
) -> None:
    """Seeds outside [0, 2**32-1] must raise RunValidationError."""
    bg = BackgroundTasks()
    service = RunService(db_session)
    with pytest.raises(RunValidationError, match="random_seed"):
        await service.create_and_dispatch(
            organization_id=seed_organization.id,
            scenario_ids=[seed_scenario_with_controls.id],
            mc_iterations_override=200,
            created_by=seed_user.id,
            background_tasks=bg,
            random_seed=bad,
        )


@pytest.mark.asyncio
async def test_seed_persisted_on_run(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
) -> None:
    """Explicitly supplied seed must be stored on the run row."""
    bg = BackgroundTasks()
    service = RunService(db_session)
    run = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=[seed_scenario_with_controls.id],
        mc_iterations_override=200,
        created_by=seed_user.id,
        background_tasks=bg,
        random_seed=1234,
    )
    assert run.random_seed == 1234


@pytest.mark.asyncio
async def test_seed_defaults_to_42(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
) -> None:
    """Omitting random_seed must default to 42 and persist it on the run."""
    bg = BackgroundTasks()
    service = RunService(db_session)
    run = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=[seed_scenario_with_controls.id],
        mc_iterations_override=1000,  # QUEUED path; no executor needed
        created_by=seed_user.id,
        background_tasks=bg,
    )
    assert run.random_seed == 42
