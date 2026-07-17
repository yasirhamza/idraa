# tests/unit/test_wizard_state.py
"""WizardStateService — DB-backed wizard state per (user_id, tx_id).

Spec §8.5 + paranoid-review Decision A.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from idraa.models.user import User
from idraa.models.wizard_draft import WizardDraft
from idraa.services.wizard_state import WizardStateService


@pytest.mark.asyncio
async def test_new_wizard_state_has_fresh_tx_id(
    db_session: AsyncSession,
    seed_user: User,
    seed_organization: Organization,
) -> None:
    svc = WizardStateService(db_session)
    state = await svc.get_or_create(
        user_id=seed_user.id,
        organization_id=seed_organization.id,
    )
    assert state.tx_id is not None
    assert state.current_step == 1


@pytest.mark.asyncio
async def test_wizard_state_persists_across_requests_same_tx(
    db_session: AsyncSession,
    seed_user: User,
    seed_organization: Organization,
) -> None:
    """Re-fetching with the same tx_id returns the persisted state."""
    svc = WizardStateService(db_session)
    state = await svc.get_or_create(
        user_id=seed_user.id,
        organization_id=seed_organization.id,
    )
    state.library_entry_id = "00000000-0000-0000-0000-000000000001"
    state.current_step = 2
    await svc.advance_step(
        user_id=seed_user.id,
        organization_id=seed_organization.id,
        state=state,
    )
    await db_session.commit()

    state2 = await svc.get_or_create(
        user_id=seed_user.id,
        organization_id=seed_organization.id,
        tx_id=uuid.UUID(state.tx_id),
    )
    assert state2.library_entry_id == "00000000-0000-0000-0000-000000000001"
    assert state2.current_step == 2


@pytest.mark.asyncio
async def test_wizard_state_parallel_tabs_isolated(
    db_session: AsyncSession,
    seed_user: User,
    seed_organization: Organization,
) -> None:
    """Two tabs of the wizard with different tx_ids are independent."""
    svc = WizardStateService(db_session)
    state_a = await svc.get_or_create(
        user_id=seed_user.id,
        organization_id=seed_organization.id,
    )
    state_b = await svc.get_or_create(
        user_id=seed_user.id,
        organization_id=seed_organization.id,
    )  # New tx_id
    assert state_a.tx_id != state_b.tx_id

    state_a.library_entry_id = "aaa"
    state_b.library_entry_id = "bbb"
    await svc.advance_step(
        user_id=seed_user.id,
        organization_id=seed_organization.id,
        state=state_a,
    )
    await svc.advance_step(
        user_id=seed_user.id,
        organization_id=seed_organization.id,
        state=state_b,
    )
    await db_session.commit()

    a_reloaded = await svc.get_or_create(
        user_id=seed_user.id,
        organization_id=seed_organization.id,
        tx_id=uuid.UUID(state_a.tx_id),
    )
    b_reloaded = await svc.get_or_create(
        user_id=seed_user.id,
        organization_id=seed_organization.id,
        tx_id=uuid.UUID(state_b.tx_id),
    )
    assert a_reloaded.library_entry_id == "aaa"
    assert b_reloaded.library_entry_id == "bbb"


@pytest.mark.asyncio
async def test_clear_wizard_state_removes_only_specified_tx(
    db_session: AsyncSession,
    seed_user: User,
    seed_organization: Organization,
) -> None:
    svc = WizardStateService(db_session)
    state_a = await svc.get_or_create(
        user_id=seed_user.id,
        organization_id=seed_organization.id,
    )
    state_b = await svc.get_or_create(
        user_id=seed_user.id,
        organization_id=seed_organization.id,
    )
    await svc.advance_step(
        user_id=seed_user.id,
        organization_id=seed_organization.id,
        state=state_a,
    )
    await svc.advance_step(
        user_id=seed_user.id,
        organization_id=seed_organization.id,
        state=state_b,
    )
    await db_session.commit()

    await svc.clear(user_id=seed_user.id, tx_id=uuid.UUID(state_a.tx_id))
    await db_session.commit()

    # a's row deleted; b still present.
    rows = (await db_session.execute(select(WizardDraft))).scalars().all()
    assert len(rows) == 1
    assert rows[0].tx_id == uuid.UUID(state_b.tx_id)


@pytest.mark.asyncio
async def test_cleanup_expired_removes_idle_drafts(
    db_session: AsyncSession,
    seed_user: User,
    seed_organization: Organization,
) -> None:
    """r2 MAJOR — TTL test. cleanup_expired deletes drafts older than 30min."""
    svc = WizardStateService(db_session)
    state = await svc.get_or_create(
        user_id=seed_user.id,
        organization_id=seed_organization.id,
    )
    await db_session.commit()
    # Force-bump updated_at to >30min ago so the cleanup sweep picks it up.
    draft = (
        await db_session.execute(
            select(WizardDraft).where(WizardDraft.tx_id == uuid.UUID(state.tx_id))
        )
    ).scalar_one()
    draft.updated_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=45)
    await db_session.commit()

    await svc.cleanup_expired(max_age_minutes=30)
    await db_session.commit()

    remaining = (
        await db_session.execute(
            select(WizardDraft).where(WizardDraft.tx_id == uuid.UUID(state.tx_id))
        )
    ).scalar_one_or_none()
    assert remaining is None
