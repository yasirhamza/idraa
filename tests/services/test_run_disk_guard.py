"""Sec-I2: create_and_dispatch rejects new runs when the DB volume is low on
free space, so a burst of high-N/high-M runs cannot refill the 3 GB volume
between 14-day retention purges (the 2026-06-29 outage class).

Fixtures mirror tests/unit/test_run_service.py's proven create_and_dispatch
harness (db_session / seed_scenario_with_controls / seed_user /
seed_organization), not the services/conftest.py in-memory `db` fixture — the
disk check is exercised via a monkeypatched shutil.disk_usage, so the actual
DB backing store is irrelevant to the assertion.
"""

from __future__ import annotations

import shutil

import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import RunValidationError
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RunStatus
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.services.runs import RunService


@pytest.mark.asyncio
async def test_dispatch_rejected_when_disk_low(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Free space below Settings.min_free_disk_bytes → RunValidationError."""
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100, 99, 1))
    bg = BackgroundTasks()
    service = RunService(db_session)
    with pytest.raises(RunValidationError, match="disk space"):
        await service.create_and_dispatch(
            organization_id=seed_organization.id,
            scenario_ids=[seed_scenario_with_controls.id],
            mc_iterations_override=200,
            created_by=seed_user.id,
            background_tasks=bg,
        )


@pytest.mark.asyncio
async def test_dispatch_allowed_when_disk_ok(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
    wire_executor_to_test_db: None,  # required for executor session sharing
) -> None:
    """Ample free space → dispatch proceeds (no raise)."""
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10**12, 1, 10**12))
    bg = BackgroundTasks()
    service = RunService(db_session)
    run = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=[seed_scenario_with_controls.id],
        mc_iterations_override=200,
        created_by=seed_user.id,
        background_tasks=bg,
    )
    assert run is not None
    await db_session.refresh(run)
    assert run.status == RunStatus.COMPLETED
