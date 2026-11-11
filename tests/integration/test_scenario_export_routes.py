from __future__ import annotations

import json
import uuid
from typing import Any

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
    from idraa.models.threat_community import canonical_threat_community_id

    s = Scenario(
        threat_community_id=canonical_threat_community_id("cybercriminals"),
        threat_community_version=1,
        threat_community_provenance="assigned",
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
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> object:
    """An ACTIVE scenario in the analyst client's org."""
    from idraa.models.enums import EntityStatus

    _client, org_id = authed_analyst
    return await _seed_scenario(
        db_session, org_id=org_id, name="Phishing AD Compromise", status=EntityStatus.ACTIVE
    )


@pytest_asyncio.fixture
async def archived_scenario(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> object:
    """A non-ACTIVE (deprecated) scenario in the analyst client's org."""
    from idraa.models.enums import EntityStatus

    _client, org_id = authed_analyst
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
async def test_bulk_export_csv_analyst_ok(
    analyst_client: AsyncClient, seeded_scenario: object
) -> None:
    r = await analyst_client.get("/scenarios/export?format=csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert seeded_scenario.name in r.text


@pytest.mark.asyncio
async def test_bulk_export_json_analyst_ok(
    analyst_client: AsyncClient, seeded_scenario: object
) -> None:
    r = await analyst_client.get("/scenarios/export?format=json")
    assert r.status_code == 200
    body = json.loads(r.text)
    assert any(o["name"] == seeded_scenario.name for o in body)


@pytest.mark.asyncio
async def test_single_export_json(analyst_client: AsyncClient, seeded_scenario: object) -> None:
    r = await analyst_client.get(f"/scenarios/{seeded_scenario.id}/export?format=json")
    assert r.status_code == 200
    assert json.loads(r.text)[0]["name"] == seeded_scenario.name


@pytest.mark.asyncio
async def test_single_export_cross_org_404(
    analyst_client: AsyncClient, other_org_scenario: object
) -> None:
    r = await analyst_client.get(f"/scenarios/{other_org_scenario.id}/export?format=json")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_export_unauthenticated_blocked(client: AsyncClient) -> None:
    r = await client.get("/scenarios/export?format=csv")
    # Unauthenticated is blocked: the app's 401→303 handler, the 307 setup-guard
    # redirect (no org seeded), or a bare 401/403 — never a 200 export.
    assert r.status_code in (302, 303, 307, 401, 403)


@pytest.mark.asyncio
async def test_bulk_export_admin_ok(admin_client: AsyncClient) -> None:
    # #315: the export role is analyst or admin. (The role fixtures share one
    # client, so each role gets its own test: one cookie per test.)
    r = await admin_client.get("/scenarios/export?format=csv")
    assert r.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/scenarios/export?format=csv", "/scenarios/export?format=json"])
async def test_bulk_export_viewer_forbidden(viewer_client: AsyncClient, path: str) -> None:
    # #315: read-only roles read on screen; they do not take the register away.
    r = await viewer_client.get(path)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_bulk_export_reviewer_forbidden(reviewer_client: AsyncClient) -> None:
    r = await reviewer_client.get("/scenarios/export?format=csv")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_single_export_viewer_forbidden_before_lookup(
    viewer_client: AsyncClient, other_org_scenario: Any
) -> None:
    # The role gate answers before the org-scoped lookup, so a viewer gets the
    # same 403 for an id in another org as for one in their own: no oracle.
    r = await viewer_client.get(f"/scenarios/{other_org_scenario.id}/export?format=json")
    assert r.status_code == 403
    r = await viewer_client.get(f"/scenarios/{uuid.uuid4()}/export?format=json")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_bulk_export_honors_status_filter(
    analyst_client: AsyncClient, seeded_scenario: object, archived_scenario: object
) -> None:
    # SC-I3: ?status= filter is applied (active-only excludes the archived one).
    r = await analyst_client.get("/scenarios/export?format=json&status=active")
    names = [o["name"] for o in json.loads(r.text)]
    assert seeded_scenario.name in names
    assert archived_scenario.name not in names


@pytest.mark.asyncio
async def test_export_route_registered_before_id_parser(analyst_client: AsyncClient) -> None:
    # B5/Sec-I1: /scenarios/export must NOT be parsed as /scenarios/{uuid}.
    r = await analyst_client.get("/scenarios/export?format=csv")
    assert r.status_code != 422  # 422 == the UUID parser ate "export"
