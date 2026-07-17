"""Integration tests for POST /scenarios/{id}/run + GET /run/new.

Fixtures follow the Phase 1.3/1.4 topology:
- ``authed_analyst`` / ``authed_reviewer`` — tuples of (AsyncClient, org_id)
  provided by conftest.py.
- ``seed_scenario_with_controls`` — a committed Scenario row with 2 controls
  belonging to ``seed_organization`` (a DIFFERENT org from authed_* fixtures).
- For tests that need the analyst's own scenario: seed a scenario inline
  against the analyst's org_id.
- ``wire_executor_to_test_db`` — patches execute_run's sessionmaker to the
  per-test SQLite DB so background-dispatched runs can write results.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import EntityStatus, ScenarioType, ThreatCategory
from idraa.models.scenario import Scenario
from tests.conftest import csrf_post


def _seed_scenario_for_org(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    name: str = "run-test-scenario",
) -> Scenario:
    """Seed a minimal valid Scenario belonging to ``org_id``.

    Mirrors ``test_scenario_routes._seed_scenario``: caller must
    ``await db.commit()`` after calling this.
    """
    s = Scenario(
        organization_id=org_id,
        name=name,
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={
            "distribution": "PERT",
            "low": 0.1,
            "mode": 0.5,
            "high": 2.0,
        },
        vulnerability={
            "distribution": "PERT",
            "low": 0.2,
            "mode": 0.4,
            "high": 0.6,
        },
        primary_loss={
            "distribution": "PERT",
            "low": 50_000,
            "mode": 250_000,
            "high": 2_000_000,
        },
        status=EntityStatus.ACTIVE,
    )
    db.add(s)
    return s


# ---- GET /scenarios/{id}/run/new -------------------------------------


@pytest.mark.asyncio
async def test_get_run_new_redirects_to_analyses_new(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """PR xi: GET /scenarios/{id}/run/new returns 303 redirect to /analyses/new.

    The old modal-trigger GET handler was deleted; the legacy URL now
    redirects to the unified /analyses/new?prefill_scenario_id=<id> form.
    RBAC is enforced by the /analyses/new destination, not the redirect.
    """
    client, org_id = authed_analyst
    scenario = _seed_scenario_for_org(db_session, org_id=org_id)
    await db_session.commit()

    response = await client.get(
        f"/scenarios/{scenario.id}/run/new",
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers.get("location", "")
    assert "prefill_scenario_id" in location
    assert str(scenario.id) in location


@pytest.mark.asyncio
async def test_get_run_new_reviewer_redirects_303(
    authed_reviewer: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """PR xi: legacy redirect handler has no RBAC gate; reviewer gets 303.

    RBAC is now enforced on the /analyses/new destination form. The redirect
    itself is open so existing bookmarks and CTAs reach the new form without
    a spurious 403.
    """
    client, org_id = authed_reviewer
    scenario = _seed_scenario_for_org(db_session, org_id=org_id)
    await db_session.commit()

    response = await client.get(
        f"/scenarios/{scenario.id}/run/new",
        follow_redirects=False,
    )
    assert response.status_code == 303


# ---- POST /scenarios/{id}/run ----------------------------------------


@pytest.mark.asyncio
async def test_post_run_returns_hx_redirect(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    wire_executor_to_test_db: None,
) -> None:
    client, org_id = authed_analyst
    scenario = _seed_scenario_for_org(db_session, org_id=org_id)
    await db_session.commit()

    response = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/run",
        {"mc_iterations": "200"},
        follow_redirects=False,
    )
    assert response.status_code == 204
    assert "HX-Redirect" in response.headers
    assert response.headers["HX-Redirect"].startswith("/runs/")


@pytest.mark.asyncio
async def test_post_run_reviewer_403(
    authed_reviewer: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_reviewer
    scenario = _seed_scenario_for_org(db_session, org_id=org_id)
    await db_session.commit()

    response = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/run",
        {"mc_iterations": "200"},
        follow_redirects=False,
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_post_run_iteration_out_of_range_422(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    scenario = _seed_scenario_for_org(db_session, org_id=org_id)
    await db_session.commit()

    response = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/run",
        {"mc_iterations": "50"},
        follow_redirects=False,
    )
    # 422 = canonical validation failure; some routes may re-render with 200
    assert response.status_code in (200, 422)


@pytest.mark.asyncio
async def test_post_run_above_settings_cap_rejected(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Issue #259: legacy POST /scenarios/{id}/run must enforce the same
    Settings.mc_iterations_max OOM cap as POST /analyses.

    Before the fix, this path only hit the hardcoded 1M service ceiling, so an
    authenticated analyst could dispatch a run 10x past the deployment cap
    (default 100_000) and OOM-kill the 2GB Fly worker. mc_iterations above the
    Settings cap must be rejected, not dispatched.
    """
    from idraa.config import get_settings

    client, org_id = authed_analyst
    scenario = _seed_scenario_for_org(db_session, org_id=org_id)
    await db_session.commit()

    over_cap = get_settings().mc_iterations_max + 1
    response = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/run",
        {"mc_iterations": str(over_cap)},
        follow_redirects=False,
    )
    assert response.status_code in (400, 422)


@pytest.mark.asyncio
async def test_post_run_cross_org_scenario_404(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Scenario not in user's org -> 404."""
    client, _ = authed_analyst

    response = await csrf_post(
        client,
        f"/scenarios/{uuid.uuid4()}/run",
        {"mc_iterations": "200"},
        follow_redirects=False,
    )
    assert response.status_code == 404
