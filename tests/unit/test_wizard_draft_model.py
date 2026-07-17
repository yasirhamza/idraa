"""WizardDraft model smoke + composite-PK + JSON round-trip."""

from __future__ import annotations

import uuid
from typing import Any as _Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.wizard_draft import WizardDraft


@pytest.mark.asyncio
async def test_wizard_draft_round_trip(
    db_session: AsyncSession, seed_user: _Any, seed_organization: _Any
) -> None:
    tx_id = uuid.uuid4()
    draft = WizardDraft(
        user_id=seed_user.id,
        tx_id=tx_id,
        organization_id=seed_organization.id,
        state_json={"current_step": 2},
    )
    db_session.add(draft)
    await db_session.commit()

    fetched = (
        await db_session.execute(
            select(WizardDraft).where(
                WizardDraft.user_id == seed_user.id,
                WizardDraft.tx_id == tx_id,
            )
        )
    ).scalar_one()
    assert fetched.state_json == {"current_step": 2}
    assert fetched.organization_id == seed_organization.id


@pytest.mark.asyncio
async def test_wizard_draft_composite_pk_uniqueness(
    db_session: AsyncSession, seed_user: _Any, seed_organization: _Any
) -> None:
    tx_id = uuid.uuid4()
    db_session.add(
        WizardDraft(
            user_id=seed_user.id,
            tx_id=tx_id,
            organization_id=seed_organization.id,
            state_json={},
        )
    )
    await db_session.commit()
    db_session.add(
        WizardDraft(
            user_id=seed_user.id,
            tx_id=tx_id,
            organization_id=seed_organization.id,
            state_json={},
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_anonymous_client_unset_session_cookie(anonymous_client: AsyncClient) -> None:
    """F0 fixture parity smoke — anonymous_client sends no session cookie."""
    from idraa.services.auth import SESSION_COOKIE

    assert anonymous_client.cookies.get(SESSION_COOKIE) is None
