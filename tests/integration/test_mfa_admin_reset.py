from __future__ import annotations

import uuid

import pyotp
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models._types import now_utc
from idraa.models.audit_log import AuditLog
from idraa.models.mfa import RecoveryCode, UserTotp, WebAuthnCredential
from idraa.models.organization import Organization
from idraa.models.session import AuthSession
from idraa.models.user import User
from idraa.services.mfa_crypto import encrypt_totp_secret, hash_recovery_code
from tests.conftest import csrf_post


async def _target_user_with_factors(db_session: AsyncSession, org_id: uuid.UUID) -> User:
    from tests.factories import create_user

    org = await db_session.get(Organization, org_id)
    assert org is not None
    user = await create_user(db_session, org, email="target@test.local")
    db_session.add(
        UserTotp(
            user_id=user.id,
            secret_encrypted=encrypt_totp_secret(pyotp.random_base32()),
            confirmed_at=now_utc(),
        )
    )
    db_session.add(
        WebAuthnCredential(
            user_id=user.id,
            credential_id=b"c1",
            public_key=b"pk",
            sign_count=0,
            nickname="k",
        )
    )
    db_session.add(RecoveryCode(user_id=user.id, code_hash=hash_recovery_code("aaaaa-bbbbb")))
    user.mfa_enrolled_at = now_utc()
    await db_session.commit()
    return user


async def test_admin_reset_clears_factors_revokes_sessions_audits(
    db_session: AsyncSession, authed_admin: tuple[AsyncClient, uuid.UUID]
) -> None:
    client, org_id = authed_admin
    target = await _target_user_with_factors(db_session, org_id)
    from idraa.services.auth import create_session

    await create_session(db_session, target.id, ip=None)
    await db_session.commit()

    r = await csrf_post(
        client,
        f"/users/{target.id}/reset-mfa",
        {"confirm": "1"},
        bootstrap_url="/users",
        follow_redirects=False,
    )
    assert r.status_code == 303

    assert (await db_session.scalar(select(UserTotp).where(UserTotp.user_id == target.id))) is None
    assert (
        await db_session.scalar(
            select(WebAuthnCredential).where(WebAuthnCredential.user_id == target.id)
        )
    ) is None
    assert (
        await db_session.scalar(select(RecoveryCode).where(RecoveryCode.user_id == target.id))
    ) is None
    await db_session.refresh(target)
    assert target.mfa_enrolled_at is None
    assert (
        await db_session.scalar(select(AuthSession).where(AuthSession.user_id == target.id))
    ) is None
    actions = {
        row.action
        for row in (
            await db_session.execute(select(AuditLog).where(AuditLog.entity_id == target.id))
        ).scalars()
    }
    assert "user.mfa_admin_reset" in actions
    assert "user.sessions_revoked" in actions


async def test_reset_requires_confirm(
    db_session: AsyncSession, authed_admin: tuple[AsyncClient, uuid.UUID]
) -> None:
    client, org_id = authed_admin
    target = await _target_user_with_factors(db_session, org_id)
    r = await csrf_post(
        client,
        f"/users/{target.id}/reset-mfa",
        {},
        bootstrap_url="/users",
        follow_redirects=False,
    )
    assert r.status_code == 400


async def test_reset_is_admin_only(
    db_session: AsyncSession, authed_analyst: tuple[AsyncClient, uuid.UUID]
) -> None:
    # Single authed fixture only — the authed_* fixtures share one underlying
    # AsyncClient, so mixing admin+analyst clients in a test cookie-clobbers.
    client, org_id = authed_analyst
    target = await _target_user_with_factors(db_session, org_id)
    r = await csrf_post(
        client,
        f"/users/{target.id}/reset-mfa",
        {"confirm": "1"},
        bootstrap_url="/",
        follow_redirects=False,
    )
    assert r.status_code == 403
