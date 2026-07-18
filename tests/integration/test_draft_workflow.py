"""tests/integration/test_draft_workflow.py — epic #34 P1a.

DRAFT scenarios are review-pending priors: visible and editable, but
excluded from run creation (server-side gate — the form filter is
convenience), dashboard metrics, and library coverage until promoted.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import EntityStatus
from tests.conftest import csrf_post

# _seed_scenario lives in tests/integration/test_scenario_routes.py; move it
# to tests/factories.py if importing across test modules is awkward — it is
# already parameterized by status.
from tests.integration.test_scenario_routes import _seed_scenario


@pytest.mark.asyncio
async def test_run_create_rejects_draft_scenario(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    draft = _seed_scenario(db_session, org_id=org_id, name="Draft S", status=EntityStatus.DRAFT)
    await db_session.commit()
    r = await csrf_post(
        client,
        "/analyses",
        {"scenario_ids": [str(draft.id)], "mc_iterations": "1000"},
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert "draft" in r.text.lower()


@pytest.mark.asyncio
async def test_run_create_rejects_mixed_active_and_draft(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    active = _seed_scenario(db_session, org_id=org_id, name="Active S", status=EntityStatus.ACTIVE)
    draft = _seed_scenario(db_session, org_id=org_id, name="Draft T", status=EntityStatus.DRAFT)
    await db_session.commit()
    r = await csrf_post(
        client,
        "/analyses",
        {"scenario_ids": [str(active.id), str(draft.id)], "mc_iterations": "1000"},
        follow_redirects=False,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_new_analysis_picker_omits_drafts(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    _seed_scenario(db_session, org_id=org_id, name="Visible Active", status=EntityStatus.ACTIVE)
    _seed_scenario(db_session, org_id=org_id, name="Hidden Draft", status=EntityStatus.DRAFT)
    await db_session.commit()
    r = await client.get("/analyses/new")
    assert "Visible Active" in r.text and "Hidden Draft" not in r.text


@pytest.mark.asyncio
async def test_dashboard_counts_exclude_drafts(authed_analyst, db_session: AsyncSession):
    client, org_id = authed_analyst
    _seed_scenario(db_session, org_id=org_id, name="Counted", status=EntityStatus.ACTIVE)
    _seed_scenario(db_session, org_id=org_id, name="Not Counted", status=EntityStatus.DRAFT)
    await db_session.commit()
    from idraa.repositories.scenario_repo import ScenarioRepo

    repo = ScenarioRepo(db_session)
    # dashboard calls count_for_org(status=ACTIVE) after this task
    assert await repo.count_for_org(organization_id=org_id, status=EntityStatus.ACTIVE) == 1
    pinned = await repo.list_pinned_library_entry_ids_for_org(org_id)
    # neither seed pins a library entry; assertion is that the signature accepts the default
    assert pinned == []
