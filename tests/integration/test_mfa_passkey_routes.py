from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from idraa.models.mfa import WebAuthnCredential
from idraa.models.user import User


async def test_passkey_options_returns_json_and_sets_challenge_cookie(
    authed_admin: tuple[AsyncClient, object],
) -> None:
    client, _ = authed_admin
    # Bootstrap a CSRF token, then call the JSON endpoint with the header.
    await client.get("/account/security")
    token = client.cookies.get("csrf_token")
    r = await client.post("/account/security/passkey/options", headers={"X-CSRF-Token": token})
    assert r.status_code == 200
    body = r.json()
    assert body["authenticatorSelection"]["userVerification"] == "required"
    assert "rf_webauthn_challenge" in r.cookies or any(
        "rf_webauthn_challenge" in h for h in r.headers.get_list("set-cookie")
    )


async def test_passkey_options_without_csrf_header_is_forbidden(
    authed_admin: tuple[AsyncClient, object],
) -> None:
    client, _ = authed_admin
    await client.get("/account/security")
    r = await client.post("/account/security/passkey/options")  # no X-CSRF-Token
    assert r.status_code == 403


async def test_passkey_delete_removes_row(
    authed_admin: tuple[AsyncClient, object], db_session
) -> None:
    from tests.conftest import csrf_post

    client, _ = authed_admin
    me = (await db_session.execute(select(User))).scalars().first()
    cred = WebAuthnCredential(
        user_id=me.id, credential_id=b"\x09\x09", public_key=b"k", nickname="X"
    )
    db_session.add(cred)
    await db_session.commit()
    r = await csrf_post(
        client,
        f"/account/security/passkey/{cred.id}/delete",
        {},
        bootstrap_url="/account/security",
        follow_redirects=False,
    )
    assert r.status_code == 303
    await db_session.commit()
    remaining = (await db_session.execute(select(WebAuthnCredential))).scalars().all()
    assert all(c.id != cred.id for c in remaining)


async def test_passkey_verify_duplicate_credential_id_returns_400(
    authed_admin: tuple[AsyncClient, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """T2-M1: WebAuthnCredential.credential_id is unique=True but was untested.

    verify_registration is monkeypatched to hand back the SAME credential_id
    on two successive registration ceremonies (a real duplicate can't be
    produced without a real authenticator). The endpoint must translate the
    resulting IntegrityError into a 400 JSON error, not a 500.
    """
    from idraa.services import webauthn_service

    client, _ = authed_admin
    fixed_cred_id = b"\x01\x02\x03\x04"

    def fake_verify_registration(
        credential: object, challenge_b64url: str
    ) -> webauthn_service.RegisteredCredential:
        return webauthn_service.RegisteredCredential(
            credential_id=fixed_cred_id,
            public_key=b"pk",
            sign_count=0,
            aaguid="00000000-0000-0000-0000-000000000000",
            transports=None,
        )

    monkeypatch.setattr(webauthn_service, "verify_registration", fake_verify_registration)

    await client.get("/account/security")
    token = client.cookies.get("csrf_token")

    async def _register_once():
        opts = await client.post(
            "/account/security/passkey/options", headers={"X-CSRF-Token": token}
        )
        assert opts.status_code == 200
        return await client.post(
            "/account/security/passkey/verify",
            json={"credential": {"id": "whatever"}, "nickname": "A"},
            headers={"X-CSRF-Token": token},
        )

    r1 = await _register_once()
    assert r1.status_code == 200

    r2 = await _register_once()
    assert r2.status_code == 400
    assert "already registered" in r2.json()["error"]
