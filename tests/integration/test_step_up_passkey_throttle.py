"""Sec-N1 -- bound the passkey step-up audit flood via the per-source throttle.

An attacker who has stolen a session cookie (but not the victim's passkey)
can hammer POST /auth/step-up/passkey/verify with garbage credentials
forever -- each miss writes an audit_log row (P2's Sec-I1 forensic-value
design) with no per-call cap. This pins the per-*source* (surface="stepup")
429 gate added in Task 5, DISTINCT from the /login throttle's "login"
surface so a step-up flood cannot burn the attacker's own /login budget
(and, more importantly, so a step-up flood targeting one IP never touches
another account's /login throttle).

The passkey miss path deliberately does NOT call register_failed_login
(P2 precedent, same-file `_audit_failure` docstring) -- that would let a
hostile session-holder lock the VICTIM out of /login, a worse DoS than the
audit flood this task bounds. This module has no assertion for that by
design (it's a negative -- there's nothing to observe); the code-level
guard is the absence of the call in routes/step_up.py, checked at review.

Fixture note: `stepup_client_with_passkey` is built locally rather than
imported from test_step_up_passkey.py because that module's enrollment/CSRF
helpers (`_attach_passkey`, `_csrf_token`) are private (leading underscore),
not exported fixtures -- see that file for the patterns this mirrors.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.mfa import WebAuthnCredential
from idraa.models.session import AuthSession
from idraa.services.auth import SESSION_COOKIE, unsign_session_id


@dataclass
class _StepUpClient:
    client: AsyncClient
    csrf: str


async def _client_session(db_session: AsyncSession, client: AsyncClient) -> AuthSession:
    signed = client.cookies.get(SESSION_COOKIE)
    assert signed is not None
    sid = unsign_session_id(signed)
    assert sid is not None
    sess = await db_session.get(AuthSession, sid)
    assert sess is not None
    return sess


@pytest_asyncio.fixture
async def stepup_client_with_passkey(
    db_session: AsyncSession, admin_client: AsyncClient
) -> _StepUpClient:
    """Authed admin client with one enrolled passkey + a primed CSRF cookie.

    A passkey must be enrolled or POST /auth/step-up/passkey/options 400s
    with "no passkeys enrolled" before the throttle-relevant /verify call
    is ever reached.
    """
    sess = await _client_session(db_session, admin_client)
    cred = WebAuthnCredential(
        user_id=sess.user_id,
        credential_id=b"throttle-test-cred",
        public_key=b"unused-in-these-tests",
        sign_count=0,
        nickname="Throttle test key",
    )
    db_session.add(cred)
    await db_session.commit()

    get = await admin_client.get("/auth/step-up")
    assert get.status_code == 200
    token = admin_client.cookies.get("csrf_token")
    assert token is not None
    return _StepUpClient(client=admin_client, csrf=token)


@pytest.mark.asyncio
async def test_passkey_stepup_429_after_source_threshold(
    stepup_client_with_passkey: _StepUpClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from idraa.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "trusted_client_ip_header", "X-Test-Client-IP", raising=False)
    monkeypatch.setattr(s, "auth_ip_max_failed_logins", 3, raising=False)
    hdr = {"X-Test-Client-IP": "203.0.113.9", "X-CSRF-Token": stepup_client_with_passkey.csrf}
    client = stepup_client_with_passkey.client
    codes = []
    for _ in range(5):
        await client.post("/auth/step-up/passkey/options", headers=hdr)  # mint challenge cookie
        r = await client.post(
            "/auth/step-up/passkey/verify",
            json={
                "credential": {"rawId": "AAAA", "id": "AAAA", "type": "public-key", "response": {}}
            },
            headers=hdr,
        )
        codes.append(r.status_code)
    assert 429 in codes
