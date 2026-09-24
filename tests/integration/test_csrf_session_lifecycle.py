"""GHSA-46jj-823j-mjj9 B4: end-to-end CSRF lifecycles through the real stack
(design Test 7). Each flow checks that the token minted BEFORE the session
changed is dead afterwards (403) and that the page GET after the change
re-mints a token that works — for setup, password + TOTP login, passkey login
and logout.

The POST target is ``/logout`` (authenticated, state-changing, reachable by
every signed-in user whatever the enrollment state).
"""

from __future__ import annotations

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models._types import now_utc
from idraa.models.mfa import UserTotp, WebAuthnCredential
from idraa.models.user import User
from tests.conftest import csrf_post


async def _cookie_token(client: AsyncClient, url: str) -> str:
    r = await client.get(url)
    assert r.status_code in (200, 303, 307)
    token = client.cookies.get("csrf_token")
    assert token
    return token


async def _logout(client: AsyncClient, token: str):
    return await client.post("/logout", headers={"X-CSRF-Token": token}, follow_redirects=False)


async def _seed_setup(client: AsyncClient) -> None:
    await csrf_post(
        client,
        "/setup",
        {
            "org_name": "A",
            "industry_type": "information",
            "organization_size": "small",
            "email": "a@b.c",
            "full_name": "A",
            "password": "pw-12345678",
        },
    )


async def test_setup_then_authenticated_post(client: AsyncClient) -> None:
    anon = await _cookie_token(client, "/setup")
    r = await client.post(
        "/setup",
        data={
            "_csrf": anon,
            "org_name": "A",
            "industry_type": "information",
            "organization_size": "small",
            "email": "a@b.c",
            "full_name": "A",
            "password": "pw-12345678",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303 and "idraa_session" in client.cookies
    # The pre-setup (anon-bound) token is dead now that a session exists.
    assert (await _logout(client, anon)).status_code == 403
    fresh = await _cookie_token(client, "/")
    assert fresh != anon
    assert (await _logout(client, fresh)).status_code == 303


async def test_password_totp_login_then_authenticated_post(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_setup(client)
    client.cookies.delete("idraa_session")
    user = (await db_session.execute(select(User))).scalars().first()
    secret = pyotp.random_base32()
    from idraa.services.mfa_crypto import encrypt_totp_secret

    db_session.add(
        UserTotp(
            user_id=user.id, secret_encrypted=encrypt_totp_secret(secret), confirmed_at=now_utc()
        )
    )
    user.mfa_enrolled_at = now_utc()
    await db_session.commit()

    anon = await _cookie_token(client, "/login")
    r = await client.post(
        "/login",
        data={"_csrf": anon, "email": "a@b.c", "password": "pw-12345678"},
        follow_redirects=False,
    )
    assert r.status_code == 200  # MFA challenge page, still anonymous
    r = await client.post(
        "/login/mfa",
        data={"_csrf": anon, "code": pyotp.TOTP(secret).now()},  # anon token still valid
        follow_redirects=False,
    )
    assert r.status_code == 303 and "idraa_session" in client.cookies
    assert (await _logout(client, anon)).status_code == 403
    fresh = await _cookie_token(client, "/")
    assert (await _logout(client, fresh)).status_code == 303


async def test_passkey_login_then_authenticated_post(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from idraa.services import webauthn_service

    await _seed_setup(client)
    client.cookies.delete("idraa_session")
    user = (await db_session.execute(select(User))).scalars().first()
    db_session.add(
        WebAuthnCredential(
            user_id=user.id, credential_id=b"lc-cred", public_key=b"k", sign_count=0, nickname="K"
        )
    )
    user.mfa_enrolled_at = now_utc()
    await db_session.commit()
    monkeypatch.setattr(webauthn_service, "verify_authentication", lambda *a, **k: 0)

    anon = await _cookie_token(client, "/login")
    headers = {"X-CSRF-Token": anon}
    assert (await client.post("/login/passkey/options", headers=headers)).status_code == 200
    r = await client.post(
        "/login/passkey/verify",
        json={"credential": {"rawId": "bGMtY3JlZA"}},  # base64url("lc-cred")
        headers=headers,
    )
    assert r.status_code == 200 and "idraa_session" in client.cookies
    assert (await _logout(client, anon)).status_code == 403
    fresh = await _cookie_token(client, "/")
    assert (await _logout(client, fresh)).status_code == 303


async def test_logout_then_login_post_uses_reminted_anon_token(client: AsyncClient) -> None:
    await _seed_setup(client)  # leaves the setup auto-login session in the jar
    authed = await _cookie_token(client, "/")
    assert (await _logout(client, authed)).status_code == 303
    # The session-bound token is dead once the session cookie is gone.
    r = await client.post(
        "/login",
        data={"_csrf": authed, "email": "a@b.c", "password": "pw-12345678"},
        follow_redirects=False,
    )
    assert r.status_code == 403
    anon = await _cookie_token(client, "/login")
    r = await client.post(
        "/login",
        data={"_csrf": anon, "email": "a@b.c", "password": "pw-12345678"},
        follow_redirects=False,
    )
    assert r.status_code in (200, 303)  # MFA funnel or session — not a CSRF 403


async def test_password_login_then_authenticated_post(client: AsyncClient) -> None:
    """Plain password login (no second factor): the session cookie arrives on a
    redirect — never on a rendered form (the invariant that keeps a page's
    token bound to the right session) — the pre-login token is dead
    afterwards, and the next page GET's token works."""
    await _seed_setup(client)
    client.cookies.delete("idraa_session")
    anon = await _cookie_token(client, "/login")
    r = await client.post(
        "/login",
        data={"_csrf": anon, "email": "a@b.c", "password": "pw-12345678"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "idraa_session" in client.cookies
    assert "<form" not in r.text
    assert (await _logout(client, anon)).status_code == 403
    fresh = await _cookie_token(client, "/")
    assert (await _logout(client, fresh)).status_code == 303


async def test_stale_tab_after_relogin_is_403_with_refresh(client: AsyncClient) -> None:
    """A tab rendered under session 1 posts after the user signed out and back
    in (session 2): 403, and an HTMX request is told to reload."""
    await _seed_setup(client)
    tab_token = await _cookie_token(client, "/")  # the old tab's page token
    assert (await _logout(client, tab_token)).status_code == 303
    anon = await _cookie_token(client, "/login")
    r = await client.post(
        "/login",
        data={"_csrf": anon, "email": "a@b.c", "password": "pw-12345678"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "idraa_session" in client.cookies
    # The old tab's token is the ONLY csrf cookie sent, so the 403 must come
    # from the session binding, not a double-submit mismatch.
    client.cookies.delete("csrf_token")
    client.cookies.set("csrf_token", tab_token)
    r = await client.post(
        "/logout", headers={"X-CSRF-Token": tab_token, "HX-Request": "true"}, follow_redirects=False
    )
    assert r.status_code == 403
    assert r.headers.get("HX-Refresh") == "true"
    # One fixed body for every CSRF failure: tells the user what to do, never
    # which check tripped (no oracle).
    assert "reload the page" in r.text
    assert "binding" not in r.text and "session" not in r.text.split("reload")[0].lower().replace(
        "signed in or out", ""
    )


def test_js_reads_csrf_only_through_the_helper() -> None:
    """Design Test 8: <meta name="csrf-token"> goes stale after a boosted login
    (<body hx-boost> swaps body+title only), so no JS may read it except the
    cookie-first helper's fallback in static/js/csrf.js."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "idraa"
    readers = [
        str(p.relative_to(root))
        for p in [*root.glob("static/js/*.js"), *root.rglob("templates/**/*.html")]
        if re.search(r"""meta\[name=["']?csrf-token""", p.read_text(encoding="utf-8"))
    ]
    assert readers == ["static/js/csrf.js"], readers
    webauthn = (root / "static/js/webauthn.js").read_text(encoding="utf-8")
    assert "idraaCsrfToken" in webauthn
    wizard = (root / "templates/scenarios/wizard/_fair_params_form_inner.html").read_text(
        encoding="utf-8"
    )
    assert "'X-CSRF-Token': csrf" in wizard and "window.idraaCsrfToken" in wizard
    # The helper agrees with Starlette's last-wins cookie parser: exact-name
    # match, keep scanning (no early return inside the loop), no decoding.
    helper = (root / "static/js/csrf.js").read_text(encoding="utf-8")
    loop = helper[helper.index("for (var i = 0") : helper.index("if (found) return found;")]
    assert "=== NAME" in loop and "found = " in loop
    assert "return" not in loop and "decodeURIComponent(" not in helper
    base = (root / "templates/base.html").read_text(encoding="utf-8")
    assert base.index("/static/js/csrf.js") < base.index("/static/js/webauthn.js")
