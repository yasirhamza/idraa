from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.mfa import WebAuthnCredential
from idraa.models.session import AuthSession
from idraa.services.auth import SESSION_COOKIE, unsign_session_id


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


async def _client_session(db_session: AsyncSession, client: AsyncClient) -> AuthSession:
    signed = client.cookies.get(SESSION_COOKIE)
    assert signed is not None
    sid = unsign_session_id(signed)
    assert sid is not None
    sess = await db_session.get(AuthSession, sid)
    assert sess is not None
    return sess


async def _csrf_token(client: AsyncClient) -> str:
    await client.get("/auth/step-up")
    token = client.cookies.get("csrf_token")
    assert token is not None
    return token


async def _attach_passkey(
    db_session: AsyncSession, user_id: uuid.UUID, cred_id: bytes = b"test-cred"
) -> WebAuthnCredential:
    cred = WebAuthnCredential(
        user_id=user_id,
        credential_id=cred_id,
        public_key=b"unused-in-these-tests",
        sign_count=0,
        nickname="Test key",
    )
    db_session.add(cred)
    await db_session.commit()
    return cred


async def test_options_scoped_to_own_credentials(
    db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    sess = await _client_session(db_session, admin_client)
    await _attach_passkey(db_session, sess.user_id)
    token = await _csrf_token(admin_client)
    r = await admin_client.post("/auth/step-up/passkey/options", headers={"X-CSRF-Token": token})
    assert r.status_code == 200
    assert len(r.json()["allowCredentials"]) == 1


async def test_options_without_passkeys_is_400(admin_client: AsyncClient) -> None:
    token = await _csrf_token(admin_client)
    r = await admin_client.post("/auth/step-up/passkey/options", headers={"X-CSRF-Token": token})
    assert r.status_code == 400
    assert "no passkeys" in r.json()["error"]


async def test_verify_rejects_other_users_credential(
    db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    # ANOTHER user owns the target passkey; the admin tries to step up with
    # it. Seed the other user directly — the authed_* fixtures all share one
    # underlying AsyncClient, so a second HTTP client is a cookie-clobber trap.
    from tests.factories import create_org, create_user

    other_org = await create_org(db_session, name="Other Org")
    other = await create_user(db_session, other_org, email="other@test.local")
    await _attach_passkey(db_session, other.id, cred_id=b"other-cred")
    admin_sess = await _client_session(db_session, admin_client)
    await _attach_passkey(db_session, admin_sess.user_id, cred_id=b"admin-cred")
    token = await _csrf_token(admin_client)
    r = await admin_client.post(  # prime the challenge cookie
        "/auth/step-up/passkey/options", headers={"X-CSRF-Token": token}
    )
    assert r.status_code == 200
    r = await admin_client.post(
        "/auth/step-up/passkey/verify",
        json={"credential": {"rawId": _b64url(b"other-cred")}, "next": "/"},
        headers={"X-CSRF-Token": token},
    )
    assert r.status_code == 400
    assert "unknown credential" in r.json()["error"]


async def test_verify_success_stamps_session_and_sanitizes_next(
    db_session: AsyncSession, admin_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full success path with the crypto verification monkeypatched (a real
    assertion needs a real authenticator — the e2e covers that; this pins the
    session-stamping + next-sanitizing behavior). Pattern from
    test_mfa_passkey_routes.py::test_passkey_verify_duplicate_credential_id_returns_400."""
    from idraa.services import webauthn_service

    sess = await _client_session(db_session, admin_client)
    await _attach_passkey(db_session, sess.user_id, cred_id=b"admin-cred")
    # Backdate so the stamp is observable.
    sess.reauthenticated_at = datetime.now(UTC) - timedelta(seconds=999)
    await db_session.commit()

    monkeypatch.setattr(webauthn_service, "verify_authentication", lambda *a, **k: 1)
    token = await _csrf_token(admin_client)
    r = await admin_client.post("/auth/step-up/passkey/options", headers={"X-CSRF-Token": token})
    assert r.status_code == 200
    r = await admin_client.post(
        "/auth/step-up/passkey/verify",
        json={
            "credential": {"rawId": _b64url(b"admin-cred")},
            "next": "https://evil.example/phish",  # not a local path -> "/"
        },
        headers={"X-CSRF-Token": token},
    )
    assert r.status_code == 200
    assert r.json()["next"] == "/"
    await db_session.refresh(sess)
    ra = sess.reauthenticated_at
    assert ra is not None
    if ra.tzinfo is None:
        ra = ra.replace(tzinfo=UTC)
    assert datetime.now(UTC) - ra < timedelta(seconds=30)
