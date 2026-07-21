"""Wizard-draft TTL sweep (drafts-surfaced spec §4)."""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.config import get_settings
from idraa.models._types import now_utc  # DA-10: the import run_reaper.py:66 uses
from idraa.models.wizard_draft import WizardDraft
from idraa.services.wizard_state import WizardStateService

pytestmark = pytest.mark.asyncio


async def _mk_draft(
    db: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID, age_days: int
) -> uuid.UUID:
    tx = uuid.uuid4()
    draft = WizardDraft(
        user_id=user_id,
        tx_id=tx,
        organization_id=org_id,
        state_json={"tx_id": str(tx), "current_step": 3},
    )
    db.add(draft)
    await db.flush()
    # backdate via direct UPDATE (onupdate would restamp)
    from sqlalchemy import update

    await db.execute(
        update(WizardDraft)
        .where(WizardDraft.tx_id == tx)
        .values(updated_at=now_utc() - datetime.timedelta(days=age_days))
    )
    await db.commit()
    return tx


async def test_ttl_setting_default() -> None:
    assert get_settings().wizard_draft_ttl_days == 30


async def test_cleanup_deletes_old_keeps_recent(
    seed_user, seed_organization, db_session: AsyncSession
) -> None:
    user = seed_user
    old_tx = await _mk_draft(db_session, user.id, user.organization_id, age_days=40)
    new_tx = await _mk_draft(db_session, user.id, user.organization_id, age_days=1)
    svc = WizardStateService(db_session)
    await svc.cleanup_expired(max_age_minutes=30 * 24 * 60)
    await db_session.commit()
    # NO count assertion (DQ-5): cleanup_expired's docstring warns SQLite may
    # report -1 — correctness is the row set, not the count.
    from sqlalchemy import select

    remaining = (await db_session.execute(select(WizardDraft.tx_id))).scalars().all()
    assert new_tx in remaining and old_tx not in remaining
