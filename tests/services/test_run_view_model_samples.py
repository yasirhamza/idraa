"""Tests for RunService.load_samples — the org-scoped lazy raw-array accessor.

The heavy per-iteration sample arrays were split off ``simulation_results``
into the 1:1 ``run_samples`` table (#294/#297). ``load_samples`` re-reads them
lazily, org-scoped (IDOR-safe via RunRepo.get_for_org_or_raise), returning None
when the row is absent ("samples purged" / never-persisted).
"""

from __future__ import annotations

import pytest

from idraa.errors import RunNotFoundError
from idraa.services.runs import RunService


@pytest.mark.asyncio
async def test_load_samples_none_when_purged(db_session, seed_run_factory, seed_organization):
    # seed_run_factory creates a run with NO run_samples row.
    run = await seed_run_factory()
    result = await RunService(db_session).load_samples(run.id, org_id=seed_organization.id)
    assert result is None


@pytest.mark.asyncio
async def test_load_samples_returns_arrays_when_present(
    db_session, seed_completed_run, seed_organization
):
    # seed_completed_run runs the executor inline, which writes a run_samples row.
    arrays = await RunService(db_session).load_samples(
        seed_completed_run.id, org_id=seed_organization.id
    )
    assert arrays and isinstance(arrays, dict)


@pytest.mark.asyncio
async def test_load_samples_cross_org_raises(
    db_session, seed_run_factory, seed_organization_factory
):
    run = await seed_run_factory()  # belongs to seed_organization (org A)
    other_org = await seed_organization_factory(name="cross-org-B")
    with pytest.raises(RunNotFoundError):
        await RunService(db_session).load_samples(run.id, org_id=other_org.id)
