"""Integration tests for the AGGREGATE path in run_executor (PR xi F6)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.risk_analysis_run import RunStatus
from idraa.models.run_samples import RunSamples
from idraa.services.run_executor import execute_run
from idraa.services.sample_codec import decode_sample_arrays


@pytest.mark.asyncio
async def test_executor_branches_on_run_type(
    db_session: AsyncSession,
    seed_aggregate_run_factory: Any,
) -> None:
    """An AGGREGATE-typed run is processed via the new path; result has per_scenario shape."""
    run = await seed_aggregate_run_factory(n_scenarios=2, n_simulations=1000)
    await execute_run(run.id)
    await db_session.refresh(run)
    assert run.status == RunStatus.COMPLETED
    assert "per_scenario" in run.simulation_results
    assert "aggregate_with_controls" in run.simulation_results
    assert "aggregate_without_controls" in run.simulation_results
    assert "control_value" in run.simulation_results


@pytest.mark.asyncio
async def test_executor_aggregate_persists_full_sample_arrays(
    db_session: AsyncSession,
    seed_aggregate_run_factory: Any,
) -> None:
    """Sample arrays present in BOTH per_scenario AND aggregate.

    #297/#294: the heavy per-iteration arrays no longer live embedded in
    run.simulation_results (which now carries only the slim summary) — the
    executor splits them out into the run_samples table, keyed by the
    compound paths produced by split_simulation_payload.
    """
    run = await seed_aggregate_run_factory(n_scenarios=2, n_simulations=1000)
    await execute_run(run.id)
    await db_session.refresh(run)
    sr = run.simulation_results
    assert len(sr["per_scenario"]) == 2

    samples = (
        await db_session.execute(select(RunSamples).where(RunSamples.run_id == run.id))
    ).scalar_one()
    # Writer invariant (Task 3): new rows use the binary codec exclusively —
    # arrays_codec populated, legacy arrays column NULL.
    assert samples.arrays is None
    assert samples.arrays_codec is not None
    arrays = decode_sample_arrays(samples.arrays_codec)
    for i in range(2):
        assert len(arrays[f"per_scenario/{i}/base_risk"]) == 1000
        assert len(arrays[f"per_scenario/{i}/residual_risk"]) == 1000
    assert len(arrays["aggregate_with_controls"]) == 1000
    assert len(arrays["aggregate_without_controls"]) == 1000


# PR pi F12 deleted the last_simulated_at AGGREGATE update test —
# the runtime no longer writes those columns, and they're dropped in F14.


@pytest.mark.asyncio
async def test_executor_aggregate_cancellation_mid_calibration(
    db_session: AsyncSession,
    seed_aggregate_run_factory: Any,
) -> None:
    """Cancellation between calibration and engine call is honored."""
    run = await seed_aggregate_run_factory(n_scenarios=2, n_simulations=1000)
    run.status = RunStatus.CANCELLED
    await db_session.commit()
    await execute_run(run.id)
    await db_session.refresh(run)
    assert run.status == RunStatus.CANCELLED
    assert run.simulation_results is None  # engine never ran
