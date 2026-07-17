"""Task 3.6: Wizard-created scenario is USD-only by construction (P2).

The wizard has no entry-currency selector; the finalize route hard-stamps
entry_currency="USD" and entry_rate=None on the returned row before commit.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.scenario import Scenario
from idraa.models.user import User
from tests.conftest import csrf_post
from tests.integration._wizard_step3_test_helpers import (
    _current_version_token,
    _persist_fair_rows_via_steps_3_and_4,
)

_STEP_2_BASE: dict[str, str] = {
    "threat_category": "ransomware",
    "threat_actor_type": "cybercriminals",
    "asset_class": "systems",
}


async def _resolve_user_id(db: AsyncSession, email: str) -> uuid.UUID:
    row = (await db.execute(select(User).where(User.email == email))).scalar_one()
    return row.id


async def _seed_sme(db: AsyncSession, *, org_id: uuid.UUID, created_by: uuid.UUID) -> uuid.UUID:
    from idraa.models.sme import SubjectMatterExpert

    sme = SubjectMatterExpert(
        organization_id=org_id,
        name="USD-wizard SME",
        email="usd-wizard-sme@example.com",
        created_by=created_by,
        created_via="admin",
    )
    db.add(sme)
    await db.flush()
    await db.commit()
    return sme.id


async def _get_tx(db: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    from idraa.models.wizard_draft import WizardDraft

    draft = (
        await db.execute(
            select(WizardDraft)
            .where(WizardDraft.user_id == user_id)
            .order_by(WizardDraft.updated_at.desc())
            .limit(1)
        )
    ).scalar_one()
    return draft.tx_id


async def _run_wizard_to_completion(
    client: AsyncClient,
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    sme_id: uuid.UUID,
    lib_entry_id: str,
    scenario_name: str,
) -> None:
    """Drive the wizard through steps 1-2 + SME rows + finalize."""
    await csrf_post(
        client,
        "/scenarios/new/wizard/step/1",
        data={"library_entry_id": lib_entry_id},
    )
    await csrf_post(
        client,
        "/scenarios/new/wizard/step/2",
        data={"name": scenario_name, **_STEP_2_BASE},
    )
    tx = await _get_tx(db, user_id)
    await _persist_fair_rows_via_steps_3_and_4(
        client,
        db,
        tx,
        tef=[(str(sme_id), 1.0, 12.0)],
        vuln=[(str(sme_id), 0.05, 0.5)],
        pl=[(str(sme_id), 100_000.0, 5_000_000.0)],
    )
    await db.close()
    vt = await _current_version_token(db, tx)
    resp = await csrf_post(
        client,
        f"/scenarios/new/wizard/finalize?tx={tx}",
        data={"version_token": str(vt)},
    )
    assert resp.status_code == 303, resp.text


@pytest.mark.asyncio
async def test_wizard_created_scenario_is_usd(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_library_entry: Any,
    db_session: AsyncSession,
) -> None:
    """Wizard-created scenario is hard-stamped entry_currency='USD', entry_rate=None.

    This is the pinned contract for Task 3.6 (P2 currency plan): the wizard has
    no native-currency selector, so we must ensure the row is explicitly stamped
    (not just relying on the column default) so a future wizard change cannot
    silently inherit a non-USD value.
    """
    client, org_id = authed_analyst
    user_id = await _resolve_user_id(db_session, "analyst@test.local")
    sme_id = await _seed_sme(db_session, org_id=org_id, created_by=user_id)
    lib_entry_id = str(seed_library_entry.id)
    await db_session.close()

    await _run_wizard_to_completion(
        client,
        db_session,
        user_id=user_id,
        sme_id=sme_id,
        lib_entry_id=lib_entry_id,
        scenario_name="USD wizard contract test",
    )

    row = (
        await db_session.execute(
            select(Scenario).where(Scenario.name == "USD wizard contract test")
        )
    ).scalar_one()
    assert row.entry_currency == "USD"
    assert row.entry_rate is None
