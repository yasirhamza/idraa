from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_scenario(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    name: str,
    status: object,
) -> object:
    """Insert a schema-valid scenario into ``org_id`` and commit so the route
    layer's separate engine sees it. Names deliberately avoid CSV-formula
    leading chars (=/+/-/@) so the csv_response sanitizer doesn't alter the
    ``in r.text`` assertions."""
    from idraa.models.enums import ScenarioType, ThreatCategory
    from idraa.models.scenario import Scenario

    s = Scenario(
        organization_id=org_id,
        name=name,
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss={"distribution": "PERT", "low": 50_000, "mode": 250_000, "high": 2_000_000},
        status=status,
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


@pytest_asyncio.fixture
async def seeded_scenario(
    authed_viewer: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> object:
    """An ACTIVE scenario in the viewer client's org."""
    from idraa.models.enums import EntityStatus

    _client, org_id = authed_viewer
    return await _seed_scenario(
        db_session, org_id=org_id, name="Phishing AD Compromise", status=EntityStatus.ACTIVE
    )


@pytest_asyncio.fixture
async def archived_scenario(
    authed_viewer: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> object:
    """A non-ACTIVE (deprecated) scenario in the viewer client's org."""
    from idraa.models.enums import EntityStatus

    _client, org_id = authed_viewer
    return await _seed_scenario(
        db_session, org_id=org_id, name="Retired Legacy VPN", status=EntityStatus.DEPRECATED
    )


@pytest_asyncio.fixture
async def other_org_scenario(db_session: AsyncSession) -> object:
    """An ACTIVE scenario in a DIFFERENT org than any authed client (cross-org IDOR)."""
    from idraa.models.enums import EntityStatus
    from tests.factories import create_org

    other_org = await create_org(db_session, name="Other Export Org")
    return await _seed_scenario(
        db_session, org_id=other_org.id, name="Other Org Scenario", status=EntityStatus.ACTIVE
    )


@pytest.mark.asyncio
async def test_bulk_export_csv_viewer_ok(
    viewer_client: AsyncClient, seeded_scenario: object
) -> None:
    r = await viewer_client.get("/scenarios/export?format=csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert seeded_scenario.name in r.text


@pytest.mark.asyncio
async def test_bulk_export_json_viewer_ok(
    viewer_client: AsyncClient, seeded_scenario: object
) -> None:
    r = await viewer_client.get("/scenarios/export?format=json")
    assert r.status_code == 200
    body = json.loads(r.text)
    assert any(o["name"] == seeded_scenario.name for o in body)


@pytest.mark.asyncio
async def test_single_export_json(viewer_client: AsyncClient, seeded_scenario: object) -> None:
    r = await viewer_client.get(f"/scenarios/{seeded_scenario.id}/export?format=json")
    assert r.status_code == 200
    assert json.loads(r.text)[0]["name"] == seeded_scenario.name


@pytest.mark.asyncio
async def test_single_export_cross_org_404(
    viewer_client: AsyncClient, other_org_scenario: object
) -> None:
    r = await viewer_client.get(f"/scenarios/{other_org_scenario.id}/export?format=json")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_export_unauthenticated_blocked(client: AsyncClient) -> None:
    r = await client.get("/scenarios/export?format=csv")
    # Unauthenticated is blocked: the app's 401→303 handler, the 307 setup-guard
    # redirect (no org seeded), or a bare 401/403 — never a 200 export.
    assert r.status_code in (302, 303, 307, 401, 403)


@pytest.mark.asyncio
async def test_bulk_export_admin_and_analyst_ok(
    admin_client: AsyncClient, analyst_client: AsyncClient, seeded_scenario: object
) -> None:
    # B3/Sec-B1: export is require_user (any authenticated), NOT require_role(VIEWER).
    # A require_role(VIEWER) allowlist would 403 these two — this test pins the fix.
    for c in (admin_client, analyst_client):
        r = await c.get("/scenarios/export?format=csv")
        assert r.status_code == 200, f"export must be reachable by {c}"


@pytest.mark.asyncio
async def test_bulk_export_honors_status_filter(
    viewer_client: AsyncClient, seeded_scenario: object, archived_scenario: object
) -> None:
    # SC-I3: ?status= filter is applied (active-only excludes the archived one).
    r = await viewer_client.get("/scenarios/export?format=json&status=active")
    names = [o["name"] for o in json.loads(r.text)]
    assert seeded_scenario.name in names
    assert archived_scenario.name not in names


@pytest.mark.asyncio
async def test_export_route_registered_before_id_parser(viewer_client: AsyncClient) -> None:
    # B5/Sec-I1: /scenarios/export must NOT be parsed as /scenarios/{uuid}.
    r = await viewer_client.get("/scenarios/export?format=csv")
    assert r.status_code != 422  # 422 == the UUID parser ate "export"
