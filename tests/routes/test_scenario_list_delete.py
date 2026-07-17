"""Issue #295: per-row Delete affordance on the scenarios LIST page.

Hard-delete already worked via the detail-page "Danger zone" form
(POST /scenarios/{id}/delete, analyst+). The gap was that the list page
(/scenarios) — where operators instinctively look — had no delete
affordance. This adds a per-row Delete action that reuses the EXISTING
endpoint, gated to analyst+ (reviewers must not see it).
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import ScenarioSource, ScenarioType
from idraa.models.scenario import Scenario


async def _seed_scenario(db: AsyncSession, org_id: uuid.UUID, *, name: str) -> Scenario:
    sc = Scenario(
        organization_id=org_id,
        name=name,
        threat_category="malware",
        scenario_type=ScenarioType.CUSTOM,
        source=ScenarioSource.EXPERT_JUDGMENT,
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss={"distribution": "PERT", "low": 1000, "mode": 5000, "high": 20000},
        version="1.0",
    )
    db.add(sc)
    await db.flush()
    await db.commit()
    return sc


@pytest.mark.asyncio
async def test_scenario_list_shows_delete_for_analyst(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    sc = await _seed_scenario(db_session, org_id, name="ListDelete-analyst")

    r = await client.get("/scenarios")
    assert r.status_code == 200
    assert f"/scenarios/{sc.id}/delete".encode() in r.content


@pytest.mark.asyncio
async def test_scenario_list_hides_delete_for_reviewer(
    authed_reviewer: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_reviewer
    sc = await _seed_scenario(db_session, org_id, name="ListDelete-reviewer")

    r = await client.get("/scenarios")
    assert r.status_code == 200
    # Reviewer is read-only: the delete affordance must not render.
    assert f"/scenarios/{sc.id}/delete".encode() not in r.content
