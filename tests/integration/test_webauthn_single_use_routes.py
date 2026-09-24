"""B5 (advisory GHSA-46jj-823j-mjj9): step-up + registration challenge single-use,
and the step-up challenge's session binding (design Tests 5-7).

Crypto verification is monkeypatched (a real assertion needs a real
authenticator); these pin the single-use claim and the binding, not the
crypto. The challenge cookie is captured from the real options route and
re-set explicitly before every replay — a successful verify deletes the
jar's copy, so relying on the jar would test a missing-cookie 400 instead.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.mfa import WebAuthnCredential
from idraa.models.session import AuthSession
from idraa.models.user import User
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


async def _stepup_token(client: AsyncClient) -> str:
    await client.get("/auth/step-up")
    token = client.cookies.get("csrf_token")
    assert token is not None
    return token


async def _attach_passkey(db_session: AsyncSession, user_id, cred_id: bytes) -> None:
    db_session.add(
        WebAuthnCredential(
            user_id=user_id,
            credential_id=cred_id,
            public_key=b"unused-in-these-tests",
            sign_count=0,
            nickname="Test key",
        )
    )
    await db_session.commit()


async def _mint_stepup_challenge(client: AsyncClient, token: str) -> str:
    r = await client.post("/auth/step-up/passkey/options", headers={"X-CSRF-Token": token})
    assert r.status_code == 200
    raw = r.cookies.get("rf_webauthn_stepup")
    assert raw is not None
    return raw


async def _stepup_verify(client: AsyncClient, token: str, raw: str, cred_id: bytes):
    client.cookies.set("rf_webauthn_stepup", raw)
    return await client.post(
        "/auth/step-up/passkey/verify",
        json={"credential": {"rawId": _b64url(cred_id)}, "next": "/"},
        headers={"X-CSRF-Token": token},
    )


async def test_stepup_replay_rejected_and_does_not_restamp(
    db_session: AsyncSession, admin_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Red on HEAD: the replay returns 200 and re-stamps reauthenticated_at."""
    from idraa.services import webauthn_service

    sess = await _client_session(db_session, admin_client)
    await _attach_passkey(db_session, sess.user_id, b"su-cred")
    monkeypatch.setattr(webauthn_service, "verify_authentication", lambda *a, **k: 0)
    token = await _stepup_token(admin_client)
    raw = await _mint_stepup_challenge(admin_client, token)

    r1 = await _stepup_verify(admin_client, token, raw, b"su-cred")
    assert r1.status_code == 200

    # Backdate after the legitimate stamp so a replay re-stamp is observable.
    stale = datetime.now(UTC) - timedelta(seconds=999)
    sess.reauthenticated_at = stale
    await db_session.commit()

    r2 = await _stepup_verify(admin_client, token, raw, b"su-cred")
    assert r2.status_code == 400
    assert "challenge already used" in r2.json()["error"]

    await db_session.refresh(sess)
    ra = sess.reauthenticated_at
    assert ra is not None
    if ra.tzinfo is None:
        ra = ra.replace(tzinfo=UTC)
    assert abs((ra - stale).total_seconds()) < 1  # NOT re-stamped

    replayed = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "user.webauthn_challenge_replayed")
        )
    ).scalar()
    assert replayed == 1


async def test_stepup_challenge_bound_to_minting_session(
    db_session: AsyncSession, admin_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S8, red on HEAD: a step-up ceremony minted under session A is accepted
    when presented with session B of the SAME user (two different users are
    already rejected by the credential-ownership filter — that variant would
    be vacuous)."""
    from idraa.services import webauthn_service
    from idraa.services.auth import create_session, sign_session_id

    sess_a = await _client_session(db_session, admin_client)
    await _attach_passkey(db_session, sess_a.user_id, b"bind-cred")
    monkeypatch.setattr(webauthn_service, "verify_authentication", lambda *a, **k: 0)
    token = await _stepup_token(admin_client)
    raw = await _mint_stepup_challenge(admin_client, token)  # bound to session A

    # A second, independent session of the same user (e.g. a stolen cookie).
    sess_b = await create_session(db_session, sess_a.user_id, ip=None)
    sess_b.reauthenticated_at = datetime.now(UTC) - timedelta(seconds=999)
    await db_session.commit()
    stamp_before = sess_b.reauthenticated_at
    admin_client.cookies.set(SESSION_COOKIE, sign_session_id(sess_b.id))
    token_b = await _stepup_token(admin_client)

    r = await _stepup_verify(admin_client, token_b, raw, b"bind-cred")
    assert r.status_code == 400
    assert "challenge expired" in r.json()["error"]
    await db_session.refresh(sess_b)
    after = sess_b.reauthenticated_at
    assert after is not None
    if after.tzinfo is None:
        after = after.replace(tzinfo=UTC)
    assert abs((after - stamp_before).total_seconds()) < 1  # session B NOT stamped


async def test_stepup_old_format_cookie_is_400_not_500(
    db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    """A step-up cookie minted before the session binding shipped (a plain
    signed string) must load as None -> 400, never a TypeError -> 500."""
    from idraa.services.auth import _webauthn_stepup_serializer

    sess = await _client_session(db_session, admin_client)
    await _attach_passkey(db_session, sess.user_id, b"old-cred")
    token = await _stepup_token(admin_client)
    old = _webauthn_stepup_serializer().dumps("legacy-plain-challenge")
    r = await _stepup_verify(admin_client, token, old, b"old-cred")
    assert r.status_code == 400
    assert "challenge expired" in r.json()["error"]


async def test_register_delete_replay_rejected(
    authed_admin: tuple[AsyncClient, object],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Red on HEAD: register -> delete the passkey -> replay the SAME ceremony
    within the TTL re-registers the deleted credential."""
    from idraa.services import webauthn_service
    from tests.conftest import csrf_post

    client, _ = authed_admin
    cred_id = b"\x0a\x0b\x0c\x0d"

    def fake_verify_registration(credential, challenge_b64url):
        return webauthn_service.RegisteredCredential(
            credential_id=cred_id,
            public_key=b"pk",
            sign_count=0,
            aaguid="00000000-0000-0000-0000-000000000000",
            transports=None,
        )

    monkeypatch.setattr(webauthn_service, "verify_registration", fake_verify_registration)
    await client.get("/account/security")
    token = client.cookies.get("csrf_token")
    opts = await client.post("/account/security/passkey/options", headers={"X-CSRF-Token": token})
    assert opts.status_code == 200
    raw = opts.cookies.get("rf_webauthn_challenge")
    assert raw is not None

    async def _verify():
        client.cookies.set("rf_webauthn_challenge", raw)
        return await client.post(
            "/account/security/passkey/verify",
            json={"credential": {"id": "whatever"}, "nickname": "A"},
            headers={"X-CSRF-Token": token},
        )

    r1 = await _verify()
    assert r1.status_code == 200
    me_id = (
        await db_session.execute(select(User.id).where(User.email == "user@test.local"))
    ).scalar_one()
    cred_pk = (
        await db_session.execute(
            select(WebAuthnCredential.id).where(WebAuthnCredential.user_id == me_id)
        )
    ).scalar_one()
    d = await csrf_post(
        client,
        f"/account/security/passkey/{cred_pk}/delete",
        {},
        bootstrap_url="/account/security",
        follow_redirects=False,
    )
    assert d.status_code == 303

    r2 = await _verify()
    assert r2.status_code == 400
    assert "challenge already used" in r2.json()["error"]
    db_session.expire_all()
    remaining = (
        await db_session.execute(
            select(func.count())
            .select_from(WebAuthnCredential)
            .where(WebAuthnCredential.user_id == me_id)
        )
    ).scalar()
    assert remaining == 0


async def test_register_plain_double_verify_rejected_by_the_claim(
    authed_admin: tuple[AsyncClient, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression pin (already 400 on HEAD via the credential UNIQUE): the
    replay is now rejected by the challenge claim, before any insert."""
    from idraa.services import webauthn_service

    client, _ = authed_admin
    monkeypatch.setattr(
        webauthn_service,
        "verify_registration",
        lambda c, ch: webauthn_service.RegisteredCredential(
            credential_id=b"\x0e\x0f",
            public_key=b"pk",
            sign_count=0,
            aaguid="00000000-0000-0000-0000-000000000000",
            transports=None,
        ),
    )
    await client.get("/account/security")
    token = client.cookies.get("csrf_token")
    opts = await client.post("/account/security/passkey/options", headers={"X-CSRF-Token": token})
    raw = opts.cookies.get("rf_webauthn_challenge")
    for expected in (200, 400):
        client.cookies.set("rf_webauthn_challenge", raw)
        r = await client.post(
            "/account/security/passkey/verify",
            json={"credential": {"id": "x"}, "nickname": "B"},
            headers={"X-CSRF-Token": token},
        )
        assert r.status_code == expected
    assert "challenge already used" in r.json()["error"]
