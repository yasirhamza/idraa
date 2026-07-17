"""Milestone B: the step-4 %-of-revenue hint renders only when the org has
annual_revenue set AND the scenario is on step 4 (capped visibility is
client-side via Alpine; the server gate is revenue-set + step-4). Display-only
— no validation, no scaling."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from idraa.models.user import User
from tests.integration._wizard_step3_test_helpers import _bootstrap_wizard_through_step_2


async def _analyst_id(db: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    row = (
        await db.execute(
            select(User).where(User.organization_id == org_id, User.email == "analyst@test.local")
        )
    ).scalar_one()
    return row.id


@pytest.mark.asyncio
async def test_revenue_hint_renders_when_revenue_set(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await db_session.execute(
        update(Organization)
        .where(Organization.id == org_id)
        .values(annual_revenue=Decimal("4000000000"))
    )
    await db_session.commit()
    user_id = await _analyst_id(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id)
    r4 = await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    assert r4.status_code == 200, r4.text
    assert "data-revenue-hint" in r4.text
    assert 'data-annual-revenue="4000000000' in r4.text
    # Step 3 shares the partial but must NOT carry the hint (no page-level
    # Alpine scope there — architect plan-gate A-N2).
    r3 = await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    assert r3.status_code == 200
    assert "data-revenue-hint" not in r3.text


@pytest.mark.asyncio
async def test_revenue_hint_absent_when_revenue_unset(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    user_id = await _analyst_id(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id)
    r4 = await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    assert r4.status_code == 200, r4.text
    assert "data-revenue-hint" not in r4.text
