"""#315: data exports need analyst or admin; the run PDF stays open to every role.

Behavioural companion to ``tests/unit/test_export_role_gate.py`` (which pins the
gate over the route table). The role fixtures share one client, so each test
uses exactly one role.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import UserRole
from idraa.models.organization import Organization
from idraa.services.auth import SESSION_COOKIE
from tests.factories import create_user, login_client_as
from tests.integration.test_aggregate_matrix_grid import (  # noqa: F401  (fixture)
    aggregate_run_client,
)

_RID = "00000000-0000-4000-8000-000000000315"

# Every role-gated export. Id-bearing routes use an id that names nothing: the
# role gate answers before any lookup, so a read-only role sees 403, not 404.
_GATED = [
    "/scenarios/export?format=csv",
    "/scenarios/export?format=json",
    f"/scenarios/{_RID}/export?format=json",
    "/controls/export.csv",
    "/controls/library/export.csv",
    "/overlays/export.csv",
    "/library/export.csv",
    "/library/export",
    f"/library/entries/{_RID}/export",
    "/analyses/export.csv",
    "/reports/export.csv",
    f"/reports/run/{_RID}/verification.xlsx",
    f"/runs/{_RID}/control-matrix.csv",
    f"/runs/{_RID}/samples.csv.gz",
]

# List pages and the export links each offers to a user who may export.
_PAGES = [
    ("/scenarios", "/scenarios/export?format=csv"),
    ("/controls", "/controls/export.csv"),
    ("/controls/library", "/controls/library/export.csv"),
    ("/overlays", "/overlays/export.csv"),
    ("/library", "/library/export.csv"),
    ("/reports", "/reports/export.csv"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _GATED)
async def test_viewer_cannot_export(viewer_client: AsyncClient, path: str) -> None:
    r = await viewer_client.get(path)
    assert r.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _GATED)
async def test_reviewer_cannot_export(reviewer_client: AsyncClient, path: str) -> None:
    r = await reviewer_client.get(path)
    assert r.status_code == 403


def _expected_for_export_role(path: str) -> int:
    """200 for a list export; 404 where the path carries the id that names nothing."""
    return 404 if _RID in path else 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _GATED)
async def test_analyst_passes_the_role_gate(analyst_client: AsyncClient, path: str) -> None:
    """Non-vacuity control: the same paths get past the gate for the export role."""
    r = await analyst_client.get(path)
    assert r.status_code == _expected_for_export_role(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _GATED)
async def test_admin_passes_the_role_gate(admin_client: AsyncClient, path: str) -> None:
    r = await admin_client.get(path)
    assert r.status_code == _expected_for_export_role(path)


@pytest.mark.asyncio
async def test_run_pdf_stays_open_to_viewer(viewer_client: AsyncClient) -> None:
    """The PDF report is exempt: a viewer passes the gate and reaches the lookup (404)."""
    r = await viewer_client.get(f"/reports/run/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(("page", "link"), _PAGES)
async def test_viewer_is_not_offered_export_buttons(
    viewer_client: AsyncClient, page: str, link: str
) -> None:
    r = await viewer_client.get(page)
    assert r.status_code == 200
    assert link.replace("&", "&amp;") not in r.text


@pytest.mark.asyncio
@pytest.mark.parametrize(("page", "link"), _PAGES)
async def test_analyst_is_offered_export_buttons(
    analyst_client: AsyncClient, page: str, link: str
) -> None:
    r = await analyst_client.get(page)
    assert r.status_code == 200
    assert link.replace("&", "&amp;") in r.text


# ---- pages that need a completed run or a scenario ------------------------


async def _login_as_role(
    client: AsyncClient, db: AsyncSession, org_id: uuid.UUID, role: UserRole
) -> None:
    """Swap the shared client's session for a new user of ``role`` in ``org_id``."""
    org = await db.get(Organization, org_id)
    assert org is not None
    user = await create_user(db, org, email=f"{role.value}-315@test.local", role=role)
    client.cookies.set(SESSION_COOKIE, await login_client_as(db, user))


async def _org_of_run(db: AsyncSession, run_id: uuid.UUID) -> uuid.UUID:
    from idraa.models.risk_analysis_run import RiskAnalysisRun

    run = await db.get(RiskAnalysisRun, run_id)
    assert run is not None
    return run.organization_id


def _run_links(run_id: uuid.UUID) -> dict[str, str]:
    return {
        "workbook": f"/reports/run/{run_id}/verification.xlsx",
        "samples": f"/runs/{run_id}/samples.csv.gz",
        "matrix": f"/runs/{run_id}/control-matrix.csv",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.VIEWER, UserRole.REVIEWER])
async def test_run_pages_offer_read_only_roles_the_pdf_only(
    aggregate_run_client: tuple[AsyncClient, uuid.UUID],  # noqa: F811
    db_session: AsyncSession,
    role: UserRole,
) -> None:
    """Run detail (header actions, overflow menu, the control-ledger macro), the
    reports list row action, the analyses index and the dashboard table."""
    client, run_id = aggregate_run_client
    await _login_as_role(client, db_session, await _org_of_run(db_session, run_id), role)

    detail = await client.get(f"/runs/{run_id}")
    assert detail.status_code == 200
    assert f'href="/reports/run/{run_id}"' in detail.text, "the PDF stays for every role"
    for name, link in _run_links(run_id).items():
        assert link not in detail.text, f"{name} offered to {role.value} on run detail"
    assert "data-chart-data" in detail.text, "the charts themselves still render"
    assert 'data-role="csv"' not in detail.text, "chart Download data offered"
    assert "Export matrix CSV" not in detail.text

    reports = await client.get("/reports")
    assert reports.status_code == 200
    assert f"/reports/run/{run_id}" in reports.text
    assert "verification.xlsx" not in reports.text
    assert "/reports/export.csv" not in reports.text

    for page in ("/analyses", "/"):
        r = await client.get(page)
        assert r.status_code == 200
        assert "/analyses/export.csv" not in r.text, f"{page} offers the analyses export"


@pytest.mark.asyncio
async def test_run_pages_offer_the_export_role_everything(
    aggregate_run_client: tuple[AsyncClient, uuid.UUID],  # noqa: F811
) -> None:
    """Positive control for the test above (the fixture's client is an analyst)."""
    client, run_id = aggregate_run_client
    detail = await client.get(f"/runs/{run_id}")
    assert detail.status_code == 200
    assert f'href="/reports/run/{run_id}"' in detail.text
    links = _run_links(run_id)
    assert links["workbook"] in detail.text
    assert links["samples"] in detail.text
    assert links["matrix"] in detail.text
    # The control-ledger macro is imported without context: the link there
    # exercises can_export()'s request fallback.
    assert detail.text.count(links["matrix"]) >= 2
    assert "Export matrix CSV" in detail.text
    assert 'data-role="csv"' in detail.text, "chart Download data lost for the export role"

    reports = await client.get("/reports")
    assert "verification.xlsx" in reports.text
    for page in ("/analyses", "/"):
        r = await client.get(page)
        assert "/analyses/export.csv" in r.text, f"{page} lost the analyses export"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "offered"),
    [
        (UserRole.VIEWER, False),
        (UserRole.REVIEWER, False),
        (UserRole.ANALYST, True),
        (UserRole.ADMIN, True),
    ],
)
async def test_scenario_view_export_buttons_follow_the_role(
    aggregate_run_client: tuple[AsyncClient, uuid.UUID],  # noqa: F811
    db_session: AsyncSession,
    role: UserRole,
    offered: bool,
) -> None:
    from sqlalchemy import select

    from idraa.models.scenario import Scenario

    client, run_id = aggregate_run_client
    org_id = await _org_of_run(db_session, run_id)
    await _login_as_role(client, db_session, org_id, role)
    scenario_id = (
        (
            await db_session.execute(
                select(Scenario.id)
                .where(Scenario.organization_id == org_id)
                .order_by(Scenario.name)
            )
        )
        .scalars()
        .first()
    )
    r = await client.get(f"/scenarios/{scenario_id}")
    assert r.status_code == 200
    assert (f"/scenarios/{scenario_id}/export" in r.text) is offered
