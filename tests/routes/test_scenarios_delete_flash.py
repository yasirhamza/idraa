"""Regression for issue #167: scenario delete redirects without flash.

Same #154 class — POST /scenarios/{id}/delete redirected to /scenarios
without setting a flash banner. Fix: appends ``?deleted=1`` to the
redirect; GET /scenarios reads the flag and renders a "Deleted
scenario." success banner. Mirrors the precedent set by #154 for
control delete + overlay deactivate + maintenance confirm.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import ScenarioSource, ScenarioType
from idraa.models.scenario import Scenario
from tests.conftest import csrf_post


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
    return sc


@pytest.mark.asyncio
async def test_post_scenario_delete_redirects_with_deleted_param(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """POST /scenarios/{id}/delete MUST include ?deleted=1 in redirect (#167)."""
    client, org_id = authed_admin
    sc = await _seed_scenario(db_session, org_id, name="ToDelete-#167")
    await db_session.commit()

    r = await csrf_post(
        client,
        f"/scenarios/{sc.id}/delete",
        {"expected_row_version": str(sc.row_version)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("/scenarios"), f"unexpected location {location!r}"
    assert "deleted=1" in location, (
        f"location {location!r} missing deleted=1 query flag (issue #167)"
    )


@pytest.mark.asyncio
async def test_get_scenarios_with_deleted_flag_renders_flash(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """GET /scenarios?deleted=1 renders the 'Deleted scenario.' banner."""
    client, _ = authed_admin
    r = await client.get("/scenarios?deleted=1")
    assert r.status_code == 200
    assert "Deleted scenario" in r.text


@pytest.mark.asyncio
async def test_get_scenarios_without_deleted_flag_no_stale_flash(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """GET /scenarios (no flag) does NOT render a stale banner."""
    client, _ = authed_admin
    r = await client.get("/scenarios")
    assert r.status_code == 200
    assert "Deleted scenario" not in r.text
