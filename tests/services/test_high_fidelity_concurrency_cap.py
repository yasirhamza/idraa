"""Issue #508 (PR2 final-gate Sec-I): create_and_dispatch caps the number of
concurrent in-flight (RUNNING + QUEUED) high-fidelity runs, so raising the MC
cap to 1M (each such run ~700 MB RSS) can't OOM the VM via unbounded concurrent
dispatch.

Fixtures mirror the disk-guard test (db_session / seed_scenario_with_controls /
seed_user / seed_organization). High-N dispatches QUEUE a background task rather
than executing inline, so seeding "in-flight" runs is just repeated
create_and_dispatch calls (with distinct random_seeds to avoid inputs_hash
collision). The default cap is 2.
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

_HIGH_N = 300_000  # >= default high_fidelity_iterations_threshold (250k)


def _ample_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10**12, 1, 10**12))


async def _dispatch(service, org, scenario, user, *, mc: int, seed: int):
    return await service.create_and_dispatch(
        organization_id=org.id,
        scenario_ids=[scenario.id],
        mc_iterations_override=mc,
        random_seed=seed,
        created_by=user.id,
        background_tasks=BackgroundTasks(),
    )


@pytest.mark.asyncio
async def test_high_n_dispatch_rejected_at_cap(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the cap (2) already met by in-flight high-N runs, a 3rd is rejected."""
    _ample_disk(monkeypatch)
    service = RunService(db_session)
    # Two high-N runs QUEUE (BackgroundTasks is not executed in-test).
    r1 = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=1
    )
    r2 = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=2
    )
    assert r1.status == RunStatus.QUEUED and r2.status == RunStatus.QUEUED
    with pytest.raises(RunValidationError, match="capacity is busy"):
        await _dispatch(
            service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=3
        )


@pytest.mark.asyncio
async def test_low_n_run_never_capped(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
    wire_executor_to_test_db: None,
) -> None:
    """A sub-threshold run is dispatched even when the high-N cap is saturated."""
    _ample_disk(monkeypatch)
    service = RunService(db_session)
    await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=1
    )
    await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=2
    )
    # A low-N run (< 1000 → sync inline) is NOT counted against the high-N cap.
    low = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=200, seed=3
    )
    await db_session.refresh(low)
    assert low.status == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_terminal_high_n_runs_do_not_count(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Completed/failed high-N runs are not in-flight, so they free capacity."""
    _ample_disk(monkeypatch)
    service = RunService(db_session)
    r1 = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=1
    )
    r2 = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=2
    )
    # Flip both to terminal — they no longer occupy high-N capacity.
    r1.status = RunStatus.COMPLETED
    r2.status = RunStatus.FAILED
    await db_session.flush()
    r3 = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=3
    )
    assert r3.status == RunStatus.QUEUED  # capacity freed → accepted
