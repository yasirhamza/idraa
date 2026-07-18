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
