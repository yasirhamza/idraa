"""#tef-pert-revert: hand-authored TEF finalizes as bounded, right-skewed PERT
(reverses Epic B's lognormal TEF authoring)."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.scenario import Scenario
from idraa.models.user import User
from tests.conftest import csrf_post
from tests.integration._wizard_step3_test_helpers import (
    _bootstrap_wizard_through_step_2,
    _current_version_token,
)


async def _analyst_id(db: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    row = (
        await db.execute(
            select(User).where(User.organization_id == org_id, User.email == "analyst@test.local")
        )
    ).scalar_one()
    return row.id


@pytest.mark.asyncio
async def test_wizard_authors_tef_as_pert(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    user_id = await _analyst_id(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id)
    # visit steps 3+4 to auto-seed all fieldsets from the IRIS baseline
    assert (await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")).status_code == 200
    assert (await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")).status_code == 200
    db_session.expire_all()
    vt = await _current_version_token(db_session, tx)
    resp = await csrf_post(
        client, f"/scenarios/new/wizard/finalize?tx={tx}", data={"version_token": str(vt)}
    )
    assert resp.status_code in (200, 303), resp.text
    db_session.expire_all()
    scen = (
        (
            await db_session.execute(
                select(Scenario)
                .where(Scenario.organization_id == org_id)
                .order_by(Scenario.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert scen is not None
    tef = scen.threat_event_frequency
    assert tef["distribution"] == "PERT"
    assert {"low", "mode", "high"} <= set(tef)
    assert tef["low"] < tef["mode"] < tef["high"]
    # RIGHT-SKEW: the authored mode must sit in the lower half of (low, high) —
    # the lognormal->PERT collapse produces this; a symmetric normal fit would
    # put the mode at the midpoint (plan-gate methodology BLOCKER guard).
    assert tef["mode"] < (tef["low"] + tef["high"]) / 2.0
