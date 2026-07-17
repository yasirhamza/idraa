"""Integration test for RunRepo.list_for_scenario AGGREGATE-membership extension (PR xi F10)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.repositories.run_repo import RunRepo


@pytest.mark.asyncio
async def test_aggregate_run_appears_in_each_constituent_scenario_history(
    db_session: AsyncSession,
    seed_aggregate_run_factory: Any,
    seed_organization: Any,
) -> None:
    """A scenario's run history page surfaces both:
    - SINGLE runs scoped to that scenario (existing behavior)
    - AGGREGATE runs that include the scenario in aggregate_scenario_ids (NEW in PR xi)

    This test uses the repository layer directly to verify the load-then-filter
    logic. Each of the N constituent scenarios should see the AGGREGATE run in
    its history when querying via RunRepo.list_for_scenario.
    """
    # Seed an AGGREGATE run with 3 constituent scenarios
    aggregate_run = await seed_aggregate_run_factory(n_scenarios=3)

    # Extract the 3 scenario UUIDs from the AGGREGATE run's aggregate_scenario_ids
    scenario_ids = [uuid.UUID(sid_str) for sid_str in aggregate_run.aggregate_scenario_ids]

    assert len(scenario_ids) == 3, "Fixture should create 3 scenarios"

    # For each constituent scenario, verify the AGGREGATE run appears in its history
    repo = RunRepo(db_session)
    for scenario_id in scenario_ids:
        # Query the history for this scenario (no pagination yet; full list)
        history = await repo.list_for_scenario(
            organization_id=seed_organization.id,
            scenario_id=scenario_id,
            limit=100,
            offset=0,
        )

        # Find the AGGREGATE run in the history
        run_ids = [r.id for r in history]
        assert aggregate_run.id in run_ids, (
            f"AGGREGATE run {aggregate_run.id} missing from history of "
            f"constituent scenario {scenario_id}"
        )

        # Also verify the count is consistent
        count = await repo.count_for_scenario(
            organization_id=seed_organization.id,
            scenario_id=scenario_id,
        )
        assert count >= 1, f"count_for_scenario should be >= 1 for scenario {scenario_id}"
        assert count == len(history), (
            f"count_for_scenario ({count}) must match list length ({len(history)}) "
            f"for scenario {scenario_id}"
        )
