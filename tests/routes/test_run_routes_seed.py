"""Route-layer tests for random_seed wiring on both run-creation routes.

Covers:

GET /analyses/new
- response contains name="random_seed" with value="42"

GET /runs/{id}/status (FAILED single run)
- every /scenarios/{id}/run re-run form carries name="random_seed"

GET /runs/{id}/status (CANCELLED single run)
- every /scenarios/{id}/run re-run form carries name="random_seed"

POST /analyses
- supplied random_seed=7 persists on created run
- omitting random_seed defaults to 42
- random_seed=-1  (out-of-range)  → 422
- random_seed="abc" (non-int)     → 422

POST /scenarios/{id}/run  (legacy adapter)
- supplied random_seed=9 persists on created run
- omitting random_seed defaults to 42
- random_seed="abc" (non-int)     → 422
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import EntityStatus, ScenarioType, ThreatCategory
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus
from idraa.models.scenario import Scenario
from idraa.models.user import User
from tests.conftest import csrf_post


def _seed_scenario(db: AsyncSession, *, org_id: uuid.UUID, name: str = "seed-scenario") -> Scenario:
    """Minimal valid Scenario in org_id.  Caller must await db.commit()."""
    s = Scenario(
        organization_id=org_id,
        name=name,
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss={"distribution": "PERT", "low": 50_000, "mode": 250_000, "high": 2_000_000},
        status=EntityStatus.ACTIVE,
    )
    db.add(s)
    return s


async def _get_run(db: AsyncSession, run_id: uuid.UUID) -> RiskAnalysisRun:
    row = (
        await db.execute(select(RiskAnalysisRun).where(RiskAnalysisRun.id == run_id))
    ).scalar_one()
    return row


# ---------------------------------------------------------------------------
# GET /analyses/new — UI presence of random_seed field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_analysis_form_contains_random_seed_input(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    """GET /analyses/new → HTML contains an input with name="random_seed" and value="42"."""
    client, _ = authed_analyst
    response = await client.get("/analyses/new")
    assert response.status_code == 200
    body = response.text
    assert 'name="random_seed"' in body, (
        "GET /analyses/new did not render a random_seed input field"
    )
    assert 'value="42"' in body, "GET /analyses/new random_seed field did not have default value=42"


# ---------------------------------------------------------------------------
# GET /runs/{id}/status — re-run forms carry random_seed hidden input
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_poll_failed_single_rerun_form_carries_random_seed(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_run_factory: Any,
    seed_user: User,
) -> None:
    """FAILED SINGLE status fragment: the re-run form must carry name="random_seed"."""
    _, analyst_org_id = authed_analyst
    scenario = _seed_scenario(db_session, org_id=analyst_org_id, name="seed-ui-failed")
    await db_session.commit()

    run: RiskAnalysisRun = await seed_run_factory(
        scenario=scenario,
        status=RunStatus.FAILED,
        organization_id=analyst_org_id,
    )
    run.error_message = "synthetic failure for seed UI test"
    await db_session.commit()

    client, _ = authed_analyst
    response = await client.get(f"/runs/{run.id}/status")
    assert response.status_code == 200
    body = response.text

    # The failed-state branch renders exactly one /scenarios/.../run form.
    assert f"/scenarios/{run.scenario_id}/run" in body, (
        "FAILED status fragment missing /scenarios/.../run re-run form"
    )
    assert 'name="random_seed"' in body, (
        "FAILED status fragment re-run form missing name='random_seed' hidden input"
    )


@pytest.mark.asyncio
async def test_status_poll_cancelled_single_rerun_form_carries_random_seed(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_run_factory: Any,
    seed_user: User,
) -> None:
    """CANCELLED SINGLE status fragment: the re-run form must carry name="random_seed"."""
    from datetime import UTC, datetime

    _, analyst_org_id = authed_analyst
    scenario = _seed_scenario(db_session, org_id=analyst_org_id, name="seed-ui-cancelled")
    await db_session.commit()

    run: RiskAnalysisRun = await seed_run_factory(
        scenario=scenario,
        status=RunStatus.CANCELLED,
        organization_id=analyst_org_id,
        completed_at=datetime.now(UTC),
    )
    await db_session.commit()

    client, _ = authed_analyst
    response = await client.get(f"/runs/{run.id}/status")
    assert response.status_code == 200
    body = response.text

    # The cancelled-state branch renders exactly one /scenarios/.../run form.
    assert f"/scenarios/{run.scenario_id}/run" in body, (
        "CANCELLED status fragment missing /scenarios/.../run re-run form"
    )
    assert 'name="random_seed"' in body, (
        "CANCELLED status fragment re-run form missing name='random_seed' hidden input"
    )


# ---------------------------------------------------------------------------
# POST /analyses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_analyses_explicit_seed_persists(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    wire_executor_to_test_db: None,
) -> None:
    """POST /analyses with random_seed=7 → run.random_seed == 7."""
    client, org_id = authed_analyst
    scenario = _seed_scenario(db_session, org_id=org_id, name="seed-explicit-analyses")
    await db_session.commit()

    response = await csrf_post(
        client,
        "/analyses",
        {
            "scenario_ids": str(scenario.id),
            "mc_iterations": "200",
            "random_seed": "7",
        },
        bootstrap_url="/analyses/new",
        follow_redirects=False,
    )
    assert response.status_code == 204
    redirect = response.headers.get("HX-Redirect", "")
    assert redirect.startswith("/runs/")

    run_id = uuid.UUID(redirect.removeprefix("/runs/"))
    run = await _get_run(db_session, run_id)
    assert run.random_seed == 7


@pytest.mark.asyncio
async def test_post_analyses_omitted_seed_defaults_to_42(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    wire_executor_to_test_db: None,
) -> None:
    """POST /analyses without random_seed → run.random_seed == 42."""
    client, org_id = authed_analyst
    scenario = _seed_scenario(db_session, org_id=org_id, name="seed-default-analyses")
    await db_session.commit()

    response = await csrf_post(
        client,
        "/analyses",
        {
            "scenario_ids": str(scenario.id),
            "mc_iterations": "200",
        },
        bootstrap_url="/analyses/new",
        follow_redirects=False,
    )
    assert response.status_code == 204
    redirect = response.headers.get("HX-Redirect", "")
    assert redirect.startswith("/runs/")

    run_id = uuid.UUID(redirect.removeprefix("/runs/"))
    run = await _get_run(db_session, run_id)
    assert run.random_seed == 42


@pytest.mark.asyncio
async def test_post_analyses_negative_seed_returns_422(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """POST /analyses with random_seed=-1 → 422 (service range gate)."""
    client, org_id = authed_analyst
    scenario = _seed_scenario(db_session, org_id=org_id, name="seed-negative-analyses")
    await db_session.commit()

    response = await csrf_post(
        client,
        "/analyses",
        {
            "scenario_ids": str(scenario.id),
            "mc_iterations": "200",
            "random_seed": "-1",
        },
        bootstrap_url="/analyses/new",
        follow_redirects=False,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_post_analyses_non_int_seed_returns_422(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """POST /analyses with random_seed='abc' → 422 (FastAPI Form parse)."""
    client, org_id = authed_analyst
    scenario = _seed_scenario(db_session, org_id=org_id, name="seed-nonint-analyses")
    await db_session.commit()

    response = await csrf_post(
        client,
        "/analyses",
        {
            "scenario_ids": str(scenario.id),
            "mc_iterations": "200",
            "random_seed": "abc",
        },
        bootstrap_url="/analyses/new",
        follow_redirects=False,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /scenarios/{id}/run  (legacy adapter)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_route_explicit_seed_persists(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    wire_executor_to_test_db: None,
) -> None:
    """POST /scenarios/{id}/run with random_seed=9 → run.random_seed == 9."""
    client, org_id = authed_analyst
    scenario = _seed_scenario(db_session, org_id=org_id, name="seed-explicit-legacy")
    await db_session.commit()

    response = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/run",
        {
            "mc_iterations": "200",
            "random_seed": "9",
        },
        follow_redirects=False,
    )
    assert response.status_code == 204
    redirect = response.headers.get("HX-Redirect", "")
    assert redirect.startswith("/runs/")

    run_id = uuid.UUID(redirect.removeprefix("/runs/"))
    run = await _get_run(db_session, run_id)
    assert run.random_seed == 9


@pytest.mark.asyncio
async def test_legacy_route_omitted_seed_defaults_to_42(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    wire_executor_to_test_db: None,
) -> None:
    """POST /scenarios/{id}/run without random_seed → run.random_seed == 42."""
    client, org_id = authed_analyst
    scenario = _seed_scenario(db_session, org_id=org_id, name="seed-default-legacy")
    await db_session.commit()

    response = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/run",
        {
            "mc_iterations": "200",
        },
        follow_redirects=False,
    )
    assert response.status_code == 204
    redirect = response.headers.get("HX-Redirect", "")
    assert redirect.startswith("/runs/")

    run_id = uuid.UUID(redirect.removeprefix("/runs/"))
    run = await _get_run(db_session, run_id)
    assert run.random_seed == 42


@pytest.mark.asyncio
async def test_legacy_route_non_int_seed_returns_422(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """POST /scenarios/{id}/run with random_seed='abc' → 422 (ValueError → except block)."""
    client, org_id = authed_analyst
    scenario = _seed_scenario(db_session, org_id=org_id, name="seed-nonint-legacy")
    await db_session.commit()

    response = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/run",
        {
            "mc_iterations": "200",
            "random_seed": "abc",
        },
        follow_redirects=False,
    )
    assert response.status_code == 422
