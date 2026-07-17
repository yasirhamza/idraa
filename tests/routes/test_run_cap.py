"""Task 11: raise the MC iteration cap to 1,000,000 for opt-in high-fidelity
tail runs. Gated on the PR1 hardening (binary codec, streaming encode,
event-loop offload, disk guard, startup VACUUM) already being merged, and on
the Task 8 benchmark confirming the envelope (M=30/1M ~700MB RSS, fits the
4GB VM).

``mc_iterations_default`` stays at 10_000 — high-N is opt-in, never default.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.config import Settings
from idraa.models.enums import EntityStatus, ScenarioType, ThreatCategory
from idraa.models.scenario import Scenario
from tests.conftest import csrf_post


def test_default_max_is_one_million() -> None:
    assert Settings().mc_iterations_max == 1_000_000


def test_default_iterations_stays_cheap() -> None:
    assert Settings().mc_iterations_default == 10_000


def _seed_scenario_for_org(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    created_by: uuid.UUID | None = None,
    name: str = "run-cap-test-scenario",
) -> Scenario:
    s = Scenario(
        organization_id=org_id,
        name=name,
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss={
            "distribution": "PERT",
            "low": 50_000,
            "mode": 250_000,
            "high": 2_000_000,
        },
        status=EntityStatus.ACTIVE,
        created_by=created_by,
    )
    db.add(s)
    return s


@pytest.mark.asyncio
async def test_post_analyses_at_new_cap_is_queued(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    wire_executor_to_test_db: None,
) -> None:
    """mc_iterations=1_000_000 (the new cap) is accepted, not rejected."""
    client, org_id = authed_analyst
    scenario = _seed_scenario_for_org(db_session, org_id=org_id, name="cap-1m")
    await db_session.commit()

    response = await csrf_post(
        client,
        "/analyses",
        {
            "scenario_ids": str(scenario.id),
            "mc_iterations": "1000000",
        },
    )
    assert response.status_code == 204
    assert response.headers.get("HX-Redirect", "").startswith("/runs/")


@pytest.mark.asyncio
async def test_post_analyses_above_new_cap_returns_422(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    wire_executor_to_test_db: None,
) -> None:
    """mc_iterations=1_000_001 (one past the new cap) is rejected with 422."""
    client, org_id = authed_analyst
    scenario = _seed_scenario_for_org(db_session, org_id=org_id, name="cap-over")
    await db_session.commit()

    response = await csrf_post(
        client,
        "/analyses",
        {
            "scenario_ids": str(scenario.id),
            "mc_iterations": "1000001",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_post_analyses_at_old_cap_still_queued(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    wire_executor_to_test_db: None,
) -> None:
    """mc_iterations=500_000 (above the OLD 100k cap, below the new 1M cap)
    is accepted — regression guard that the raise actually took effect."""
    client, org_id = authed_analyst
    scenario = _seed_scenario_for_org(db_session, org_id=org_id, name="cap-500k")
    await db_session.commit()

    response = await csrf_post(
        client,
        "/analyses",
        {
            "scenario_ids": str(scenario.id),
            "mc_iterations": "500000",
        },
    )
    assert response.status_code == 204
    assert response.headers.get("HX-Redirect", "").startswith("/runs/")
