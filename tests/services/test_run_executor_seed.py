"""Tests: executor reads persisted seed + persists derived_seed_keys.

Validates the seed-reproducibility wiring in run_executor.execute_run:

- SINGLE run -> run_samples.derived_seed_keys == {str(run.scenario_id): 0}
  (single path spawns exactly one child seed -> spawn index 0; keyed off the
  RUN's scenario_id because ControlEnhancedRisk.scenario_id is None on the
  single path).
- AGGREGATE run -> derived_seed_keys has one entry per scenario_id; the
  scenario_id -> spawn-index mapping is STABLE across two executions of the
  same inputs (proves the aggregate scenario order is pinned to
  run.aggregate_scenario_ids rather than the non-deterministic fetch order).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RunStatus
from idraa.models.run_samples import RunSamples
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.services.runs import RunService


async def _load_derived_seed_keys(db_session: AsyncSession, run_id: Any) -> dict[str, int]:
    samples = (
        await db_session.execute(select(RunSamples).where(RunSamples.run_id == run_id))
    ).scalar_one()
    keys = samples.derived_seed_keys
    assert keys is not None
    return keys


@pytest.mark.asyncio
async def test_single_run_persists_derived_seed_key(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
) -> None:
    """SINGLE run -> derived_seed_keys keyed by RUN scenario_id -> spawn index 0."""
    bg = BackgroundTasks()
    service = RunService(db_session)
    run = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=[seed_scenario_with_controls.id],
        mc_iterations_override=200,  # < _SYNC_THRESHOLD -> runs inline
        created_by=seed_user.id,
        background_tasks=bg,
        random_seed=7,
    )
    await db_session.refresh(run)
    assert run.status == RunStatus.COMPLETED

    keys = await _load_derived_seed_keys(db_session, run.id)
    assert keys == {str(run.scenario_id): 0}


@pytest.mark.asyncio
async def test_aggregate_run_derived_seed_keys_stable_across_reruns(
    db_session: AsyncSession,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    seed_user: User,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
) -> None:
    """AGGREGATE -> one entry per scenario_id; mapping STABLE across re-runs.

    Stability proves the executor pins scenario iteration order to
    run.aggregate_scenario_ids (the spawn index is assigned in iteration
    order), not to the non-deterministic fetch_by_ids_for_org result order.
    """
    from idraa.models.scenario_control import ScenarioControl

    # 3 scenarios, each with its own mitigating control.
    scenarios = [await seed_scenario_factory(name=f"seed_agg_{i}") for i in range(3)]
    for i, s in enumerate(scenarios):
        c = await seed_control_factory(name=f"seed_agg_ctrl_{i}")
        db_session.add(ScenarioControl(scenario_id=s.id, control_id=c.id))
    await db_session.commit()
    scenario_ids = {str(s.id) for s in scenarios}

    async def _run_once() -> dict[str, int]:
        bg = BackgroundTasks()
        service = RunService(db_session)
        run = await service.create_and_dispatch(
            organization_id=seed_organization.id,
            scenario_ids=[s.id for s in scenarios],
            mc_iterations_override=200,  # < _SYNC_THRESHOLD -> runs inline
            created_by=seed_user.id,
            background_tasks=bg,
            random_seed=7,
        )
        await db_session.refresh(run)
        assert run.status == RunStatus.COMPLETED
        return await _load_derived_seed_keys(db_session, run.id)

    keys_first = await _run_once()
    keys_second = await _run_once()

    # One entry per scenario_id; keys are exactly the scenario id set.
    assert set(keys_first.keys()) == scenario_ids
    # Spawn indices are the 0..N-1 range, one per scenario.
    assert sorted(keys_first.values()) == list(range(len(scenarios)))
    # The scenario_id -> spawn index mapping is identical across re-runs
    # (order pinned to aggregate_scenario_ids).
    assert keys_first == keys_second
