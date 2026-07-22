from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.organization import Organization
from idraa.models.session import AuthSession
from idraa.models.user import User
from tests.conftest import csrf_post


async def _target_with_session(db_session: AsyncSession, org_id: uuid.UUID) -> User:
    from idraa.services.auth import create_session
    from tests.factories import create_user

    org = await db_session.get(Organization, org_id)
    assert org is not None
    user = await create_user(db_session, org, email="victim@test.local")
    await create_session(db_session, user.id, ip=None)
    await db_session.commit()
    return user


async def test_set_active_deactivation_revokes_sessions(
    db_session: AsyncSession, authed_admin: tuple[AsyncClient, uuid.UUID]
) -> None:
    client, org_id = authed_admin
    target = await _target_with_session(db_session, org_id)
    r = await csrf_post(
        client,
        f"/users/{target.id}/set-active",
        {"active": "0"},
        bootstrap_url="/users",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert (
        await db_session.scalar(select(AuthSession).where(AuthSession.user_id == target.id))
    ) is None
    actions = {
        row.action
        for row in (
            await db_session.execute(select(AuditLog).where(AuditLog.entity_id == target.id))
        ).scalars()
    }
    assert "user.sessions_revoked" in actions


async def test_edit_post_deactivation_revokes_sessions(
    db_session: AsyncSession, authed_admin: tuple[AsyncClient, uuid.UUID]
) -> None:
    client, org_id = authed_admin
    target = await _target_with_session(db_session, org_id)
    r = await csrf_post(
        client,
        f"/users/{target.id}/edit",
        {"role": target.role.value},
        bootstrap_url="/users",
        follow_redirects=False,
    )  # is_active checkbox omitted == deactivate (checkbox semantics)
    assert r.status_code == 303
    assert (
        await db_session.scalar(select(AuthSession).where(AuthSession.user_id == target.id))
    ) is None


async def test_reactivation_does_not_touch_sessions(
    db_session: AsyncSession, authed_admin: tuple[AsyncClient, uuid.UUID]
) -> None:
    client, org_id = authed_admin
    target = await _target_with_session(db_session, org_id)
    await csrf_post(
        client,
        f"/users/{target.id}/set-active",
        {"active": "0"},
        bootstrap_url="/users",
        follow_redirects=False,
    )
    r = await csrf_post(
        client,
        f"/users/{target.id}/set-active",
        {"active": "1"},
        bootstrap_url="/users",
        follow_redirects=False,
    )
    assert r.status_code == 303  # no error; nothing to revoke on reactivate
