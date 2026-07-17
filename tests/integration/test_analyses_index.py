"""Integration tests for the org-wide historical analyses index (GET /analyses).

The sidebar "Analyses" item points here; it lists every run for the org
(SINGLE + AGGREGATE, all statuses), newest first, paginated, reusing the
parameterized runs/_history_list.html partial in show_run_meta mode.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from tests.integration._reports_fixtures import (
    _make_completed_aggregate_run,
    _make_completed_single_run,
)


async def _org(db: AsyncSession, org_id: uuid.UUID) -> Organization:
    org = await db.get(Organization, org_id)
    assert org is not None
    return org


@pytest.mark.asyncio
async def test_analyses_index_lists_runs_with_name_and_type(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """GET /analyses lists the org's runs with Name + Type columns and a
    residual ALE for both SINGLE and AGGREGATE shapes."""
    client, org_id = authed_analyst
    org = await _org(db_session, org_id)
    await _make_completed_aggregate_run(db_session, org, name="Portfolio Q2")
    await _make_completed_single_run(db_session, org, name="Ransomware drill")
    await db_session.commit()

    r = await client.get("/analyses")
    assert r.status_code == 200
    body = r.text
    assert "Analyses" in body
    # Run names render
    assert "Portfolio Q2" in body
    assert "Ransomware drill" in body
    # Type column (title-cased run_type)
    assert "Aggregate" in body
    assert "Single" in body
    # AGGREGATE residual ALE (aggregate_with_controls ALE) is shown, not "—"
    assert "2,610,000" in body


@pytest.mark.asyncio
async def test_analyses_index_empty_state(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    """No runs → friendly empty state, and no Export CSV affordance."""
    client, _ = authed_analyst
    r = await client.get("/analyses")
    assert r.status_code == 200
    assert "No analysis runs yet" in r.text
    assert "/analyses/export.csv" not in r.text


@pytest.mark.asyncio
async def test_analyses_index_is_org_scoped(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_organization: Organization,
    db_session: AsyncSession,
) -> None:
    """A run belonging to another org must never appear in this org's index."""
    client, org_id = authed_analyst
    my_org = await _org(db_session, org_id)
    await _make_completed_single_run(db_session, my_org, name="Mine visible")
    # seed_organization is a DIFFERENT org.
    await _make_completed_single_run(db_session, seed_organization, name="Other org secret")
    await db_session.commit()

    r = await client.get("/analyses")
    assert r.status_code == 200
    assert "Mine visible" in r.text
    assert "Other org secret" not in r.text


@pytest.mark.asyncio
async def test_analyses_index_analyst_sees_new_and_export(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Analyst sees the 'New analysis' button and (with runs) the CSV export."""
    client, org_id = authed_analyst
    org = await _org(db_session, org_id)
    await _make_completed_single_run(db_session, org, name="A run")
    await db_session.commit()

    r = await client.get("/analyses")
    assert r.status_code == 200
    assert "/analyses/new" in r.text
    assert "New analysis" in r.text
    assert "/analyses/export.csv" in r.text


@pytest.mark.asyncio
async def test_analyses_index_reviewer_sees_list_but_no_new_button(
    authed_reviewer: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Reviewer (read-only) can view the list but the create affordance is
    hidden — creating a run is analyst/admin-only (enforced on /analyses/new)."""
    client, org_id = authed_reviewer
    org = await _org(db_session, org_id)
    await _make_completed_single_run(db_session, org, name="Reviewer visible run")
    await db_session.commit()

    r = await client.get("/analyses")
    assert r.status_code == 200
    assert "Reviewer visible run" in r.text
    assert "/analyses/new" not in r.text


@pytest.mark.asyncio
async def test_analyses_index_paginates(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """With more runs than page_size, page 1 shows a next-page link to /analyses."""
    client, org_id = authed_analyst
    org = await _org(db_session, org_id)
    for i in range(23):
        await _make_completed_single_run(db_session, org, name=f"run-{i:02d}")
    await db_session.commit()

    r = await client.get("/analyses?page=1&page_size=20")
    assert r.status_code == 200
    # Pagination control points back at the org-wide index, not a scenario URL.
    assert "/analyses?page=2" in r.text
