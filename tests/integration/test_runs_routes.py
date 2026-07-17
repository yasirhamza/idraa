"""Integration tests for the omicron-1 F4 ``name`` field on POST /analyses.

F4 added ``name: str | None = None`` to ``RunService.create_and_dispatch`` and
exposed it as an optional ``Form(default=None, max_length=200)`` parameter on
the POST /analyses route + the legacy /scenarios/{id}/run adapter. These three
tests pin the route-layer contract:

- happy path: supplied ``name`` persists on the created run
- regression: omitting the field leaves ``run.name`` NULL (no implicit default)
- security: oversized name (201 chars) fails validation with 422

These tests scope their seeded scenarios to the authed user's own org
(via _make_scenario from _dashboard_fixtures), keeping the route tests
free of cross-org wiring.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.risk_analysis_run import RiskAnalysisRun
from tests.conftest import csrf_post
from tests.integration._dashboard_fixtures import _make_scenario


@pytest.mark.asyncio
async def test_post_analyses_accepts_optional_name_field(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    wire_executor_to_test_db: None,
) -> None:
    """Happy path: supplied ``name`` value persists onto the created run."""
    client, org_id = authed_analyst
    scenario = _make_scenario(org_id=org_id, name="f4-named-run-scenario")
    db_session.add(scenario)
    await db_session.commit()

    response = await csrf_post(
        client,
        "/analyses",
        {
            "scenario_ids": str(scenario.id),
            "mc_iterations": "1000",
            "name": "Q2 ransomware drill",
        },
        bootstrap_url="/analyses/new",
        follow_redirects=False,
    )
    assert response.status_code == 204
    redirect = response.headers.get("HX-Redirect", "")
    assert redirect.startswith("/runs/")

    run_id = uuid.UUID(redirect.removeprefix("/runs/"))
    row = (
        await db_session.execute(select(RiskAnalysisRun).where(RiskAnalysisRun.id == run_id))
    ).scalar_one()
    assert row.name == "Q2 ransomware drill"


@pytest.mark.asyncio
async def test_post_analyses_without_name_persists_null(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    wire_executor_to_test_db: None,
) -> None:
    """Regression: omitting ``name`` leaves ``run.name`` NULL (no implicit default)."""
    client, org_id = authed_analyst
    scenario = _make_scenario(org_id=org_id, name="f4-unnamed-run-scenario")
    db_session.add(scenario)
    await db_session.commit()

    response = await csrf_post(
        client,
        "/analyses",
        {
            "scenario_ids": str(scenario.id),
            "mc_iterations": "1000",
        },
        bootstrap_url="/analyses/new",
        follow_redirects=False,
    )
    assert response.status_code == 204
    redirect = response.headers.get("HX-Redirect", "")
    assert redirect.startswith("/runs/")

    run_id = uuid.UUID(redirect.removeprefix("/runs/"))
    row = (
        await db_session.execute(select(RiskAnalysisRun).where(RiskAnalysisRun.id == run_id))
    ).scalar_one()
    assert row.name is None


@pytest.mark.asyncio
async def test_post_analyses_rejects_oversized_name_field(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Security: name >200 chars rejected at the FastAPI Form() layer (422)."""
    client, org_id = authed_analyst
    scenario = _make_scenario(org_id=org_id, name="f4-oversize-name-scenario")
    db_session.add(scenario)
    await db_session.commit()

    response = await csrf_post(
        client,
        "/analyses",
        {
            "scenario_ids": str(scenario.id),
            "mc_iterations": "1000",
            "name": "x" * 201,
        },
        bootstrap_url="/analyses/new",
        follow_redirects=False,
    )
    assert response.status_code == 422
