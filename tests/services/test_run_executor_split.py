"""execute_run persists the slim summary on the run and the heavy per-iteration
sample arrays in run_samples (#297 / #294).

Mirrors the SYNC-path executor test setup in tests/unit/test_run_executor.py:
RunService is bypassed and execute_run is called directly with a pre-seeded
QUEUED row (mc_iterations < _SYNC_THRESHOLD so the executor runs inline).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus
from idraa.models.run_samples import RunSamples
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.services.run_executor import execute_run
from idraa.services.sample_codec import decode_sample_arrays
from idraa.services.simulation_payload import SAMPLE_ARRAY_KEY


def _no_array(obj: object) -> bool:
    if isinstance(obj, dict):
        return SAMPLE_ARRAY_KEY not in obj and all(_no_array(v) for v in obj.values())
    if isinstance(obj, list):
        return all(_no_array(v) for v in obj)
    return True


@pytest.fixture
async def queued_run(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
) -> RiskAnalysisRun:
    scenario = seed_scenario_with_controls
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=seed_organization.id,
        scenario_id=scenario.id,
        mc_iterations=200,
        inputs_hash="h" * 64,
        controls_snapshot=[],
        control_ids_used=[str(c.id) for c in scenario.mitigating_controls],
        status=RunStatus.QUEUED,
        created_by=seed_user.id,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return run


@pytest.mark.asyncio
async def test_executed_run_splits_summary_and_samples(
    db_session: AsyncSession,
    queued_run: RiskAnalysisRun,
) -> None:
    await execute_run(queued_run.id)
    await db_session.refresh(queued_run)

    assert queued_run.status == RunStatus.COMPLETED, queued_run.error_message
    # Summary stays on the run, but NO per-iteration arrays remain embedded.
    assert queued_run.simulation_results is not None
    assert _no_array(queued_run.simulation_results)

    # The arrays moved to run_samples, scoped to the same org.
    samples = (
        await db_session.execute(select(RunSamples).where(RunSamples.run_id == queued_run.id))
    ).scalar_one()
    assert samples.organization_id == queued_run.organization_id

    # Writer invariant (Task 3 / T2 carry-forward): a freshly-written row uses
    # the binary codec exclusively — arrays_codec populated, legacy arrays
    # column NULL. Never both populated, never both empty for a non-degenerate
    # run (this scenario's controls guarantee a non-empty sample array).
    assert samples.arrays is None
    assert samples.arrays_codec is not None
    decoded = decode_sample_arrays(samples.arrays_codec)
    assert decoded
    assert all(len(v) > 0 for v in decoded.values())
