"""B5 (advisory GHSA-46jj-823j-mjj9): login passkey challenge single-use.

A captured (assertion, challenge cookie) pair must not replay within the
challenge TTL — on HEAD, replaying the same verified assertion mints a
SECOND session (two 200s, two auth_sessions rows). Design doc Tests 3-4:
docs/superpowers/specs/2026-09-23-webauthn-challenge-single-use-design.md.

verify_authentication is monkeypatched (pattern from
test_step_up_passkey.py::test_verify_success_stamps_session_and_sanitizes_next)
— a real assertion needs a real authenticator; this pins the single-use
claim, not the crypto.

The challenge cookie value is captured from the /login/passkey/options
response and resent EXPLICITLY on every verify call, independent of the
client's cookie jar — clear_webauthn_challenge_cookie deletes the jar's copy
on the first 200, so relying on the jar for a "replay" would silently test
the WRONG thing (a missing-cookie 400, not the single-use claim).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.mfa import WebAuthnCredential
from idraa.models.session import AuthSession


async def _csrf_token(client: AsyncClient) -> str:
    await client.get("/login")
    token = client.cookies.get("csrf_token")
    assert token is not None
    return token


async def _seed_user_with_passkey(
    db_session: AsyncSession, cred_id: bytes = b"login-cred"
) -> uuid.UUID:
    from tests.factories import create_org, create_user

    org = await create_org(db_session)
    user = await create_user(db_session, org, email="passkey@test.local")
    cred = WebAuthnCredential(
        user_id=user.id,
        credential_id=cred_id,
        public_key=b"unused-in-these-tests",
        sign_count=0,
        nickname="Login key",
    )
    db_session.add(cred)
    await db_session.commit()
    return user.id


async def _mint_challenge_cookie(client: AsyncClient, token: str) -> str:
    r = await client.post("/login/passkey/options", headers={"X-CSRF-Token": token})
    assert r.status_code == 200
    raw = r.cookies.get("rf_webauthn_challenge")
    assert raw is not None
    return raw


async def test_login_passkey_replay_rejected_after_first_success(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Red on HEAD: replaying the same (assertion, challenge) pair mints a
    SECOND session instead of a 400."""
    from idraa.services import webauthn_service

    await _seed_user_with_passkey(db_session)
    monkeypatch.setattr(webauthn_service, "verify_authentication", lambda *a, **k: 0)

    token = await _csrf_token(client)
    raw_challenge = await _mint_challenge_cookie(client, token)

    payload = {"credential": {"rawId": "bG9naW4tY3JlZA"}}  # base64url("login-cred")

    async def _verify() -> object:
        # Re-assert the cookie into the jar before every call: the previous
        # response's clear_webauthn_challenge_cookie (on 200) or a 400 leaves
        # the jar in whatever state that response dictated — we want THIS
        # exact challenge resent regardless (that's what "replay" means).
        client.cookies.set("rf_webauthn_challenge", raw_challenge)
        return await client.post(
            "/login/passkey/verify", json=payload, headers={"X-CSRF-Token": token}
        )

    r1 = await _verify()
    assert r1.status_code == 200

    r2 = await _verify()
    assert r2.status_code == 400
    assert "challenge already used" in r2.json()["error"]

    # A third and fourth replay must still 400 (S1: audit row stays bounded,
    # checked below) — not degrade into some other error class.
    r3 = await _verify()
    assert r3.status_code == 400

    sessions = (await db_session.execute(select(func.count()).select_from(AuthSession))).scalar()
    assert sessions == 1

    replay_rows = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "user.webauthn_challenge_replayed")
        )
    ).scalar()
    assert replay_rows == 1  # S1: guarded — one row no matter how many replays


async def test_login_passkey_concurrent_verify_mints_one_session(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two concurrent verifies of the SAME (assertion, challenge) pair must
    yield exactly one 200 and one auth_sessions row — route-level counterpart
    of the service-layer warm-pool race in
    test_webauthn_challenge_atomicity.py."""
    from idraa.services import webauthn_service

    await _seed_user_with_passkey(db_session, cred_id=b"race-cred")
    monkeypatch.setattr(webauthn_service, "verify_authentication", lambda *a, **k: 0)

    token = await _csrf_token(client)
    raw_challenge = await _mint_challenge_cookie(client, token)

    payload = {"credential": {"rawId": "cmFjZS1jcmVk"}}  # base64url("race-cred")
    client.cookies.set("rf_webauthn_challenge", raw_challenge)  # already the jar's value here

    async def _verify() -> object:
        return await client.post(
            "/login/passkey/verify", json=payload, headers={"X-CSRF-Token": token}
        )

    r1, r2 = await asyncio.gather(_verify(), _verify())
    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [200, 400]

    sessions = (await db_session.execute(select(func.count()).select_from(AuthSession))).scalar()
    assert sessions == 1
