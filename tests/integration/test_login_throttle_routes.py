"""Per-source (per-IP) 429 gate on the three login POST paths (idraa#81).

Uses the shared ``csrf_post`` helper so every POST carries a valid ``_csrf``
field -- bypassing the CSRF dance would get a flat 403 from CSRFMiddleware
before the throttle gate ever runs.

The throttle is trusted-strategy-gated: with no ``trusted_client_ip_header``
/ ``trusted_proxy_count`` configured, ``resolve_throttle_source`` returns
``None`` and the store no-ops (fail-open by design -- idraa#81). Tests that
want the gate ACTIVE configure ``trusted_client_ip_header`` to a header the
test can set directly, mirroring the ``X-Test-Client-IP`` shape documented
in ``routes/deps.py::resolve_throttle_source``.
"""

from __future__ import annotations

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models._types import now_utc
from idraa.models.mfa import UserTotp
from idraa.models.user import User
from idraa.services.mfa_crypto import encrypt_totp_secret
from tests.conftest import csrf_post


def _trust(monkeypatch: pytest.MonkeyPatch) -> None:
    from idraa.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "trusted_client_ip_header", "X-Test-Client-IP", raising=False)
    monkeypatch.setattr(s, "auth_ip_max_failed_logins", 3, raising=False)


@pytest.mark.asyncio
async def test_login_429_after_ip_threshold(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _trust(monkeypatch)
    hdr = {"X-Test-Client-IP": "203.0.113.7"}
    for _ in range(3):
        r = await csrf_post(
            client, "/login", {"email": "nobody@example.com", "password": "wrong"}, headers=hdr
        )
        assert r.status_code == 400  # dummy-hash path still counts
    r = await csrf_post(
        client, "/login", {"email": "nobody@example.com", "password": "wrong"}, headers=hdr
    )
    assert r.status_code == 429 and r.headers.get("retry-after")


@pytest.mark.asyncio
async def test_default_config_throttle_noops(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from idraa.config import get_settings

    monkeypatch.setattr(get_settings(), "auth_ip_max_failed_logins", 3, raising=False)
    # No trusted strategy configured -> source is None -> no 429 ever.
    for _ in range(6):
        r = await csrf_post(client, "/login", {"email": "nobody@example.com", "password": "wrong"})
        assert r.status_code == 400


async def _seed_setup(client: AsyncClient, *, email: str = "a@b.c") -> None:
    """Create the first (factor-less) admin via /setup, then drop the session.

    Mirrors ``tests/integration/test_login_flow.py::_seed_setup`` -- a
    freshly-bootstrapped admin has no strong 2nd factor enrolled, so a
    correct-password POST to /login is full auth (reaches the
    ``reset_source_throttle`` branch, not the mfa-pending branch).
    """
    await csrf_post(
        client,
        "/setup",
        {
            "org_name": "A",
            "industry_type": "information",
            "organization_size": "small",
            "email": email,
            "full_name": "A",
            "password": "pw-12345678",
        },
    )
    client.cookies.delete("idraa_session")


@pytest.mark.asyncio
async def test_login_success_resets_source_throttle(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _trust(monkeypatch)
    hdr = {"X-Test-Client-IP": "203.0.113.9"}
    await _seed_setup(client)

    # Two failed attempts against the real account (below the threshold of 3).
    for _ in range(2):
        r = await csrf_post(client, "/login", {"email": "a@b.c", "password": "wrong"}, headers=hdr)
        assert r.status_code == 400

    # Correct password -> full auth (no strong factor enrolled) -> resets the
    # per-source throttle alongside the per-account one.
    r = await csrf_post(
        client,
        "/login",
        {"email": "a@b.c", "password": "pw-12345678"},
        headers=hdr,
        follow_redirects=False,
    )
    assert r.status_code == 303
    client.cookies.delete("idraa_session")

    # A fresh burst of failures starts from zero: 3 misses land the 3rd at
    # exactly the threshold (still 400, not yet 429); a 4th is the 429.
    for _ in range(3):
        r = await csrf_post(client, "/login", {"email": "a@b.c", "password": "wrong"}, headers=hdr)
        assert r.status_code == 400
    r = await csrf_post(client, "/login", {"email": "a@b.c", "password": "wrong"}, headers=hdr)
    assert r.status_code == 429


async def _seed_totp_user(
    client: AsyncClient, db_session: AsyncSession, *, email: str = "a@b.c"
) -> None:
    """Create a TOTP-enrolled user (mirrors ``test_login_mfa_flow._seed_totp_user``)."""
    await _seed_setup(client, email=email)
    user = (await db_session.execute(select(User))).scalars().first()
    db_session.add(
        UserTotp(
            user_id=user.id,
            secret_encrypted=encrypt_totp_secret(pyotp.random_base32()),
            confirmed_at=now_utc(),
        )
    )
    user.mfa_enrolled_at = now_utc()
    await db_session.commit()


@pytest.mark.asyncio
async def test_login_mfa_429_after_ip_threshold(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The per-source budget is shared across the whole "login" surface
    # (login_post / login_mfa_post / login_passkey_verify) -- this proves the
    # gate also fires from the 2nd-factor path, not just /login.
    _trust(monkeypatch)
    hdr = {"X-Test-Client-IP": "203.0.113.11"}
    await _seed_totp_user(client, db_session)

    # Correct password establishes mfa_pending. login_post's own comment
    # ("Do NOT reset the throttle here") confirms this step neither consumes
    # nor resets the per-source budget -- only a *failed* attempt counts.
    r = await csrf_post(
        client,
        "/login",
        {"email": "a@b.c", "password": "pw-12345678"},
        headers=hdr,
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "idraa_session" not in r.cookies

    # Three wrong TOTP codes accrue the per-source failure count via
    # register_failed_source (the ``method is None`` branch) -- each is a
    # plain 400 (invalid code), and the rf_mfa_pending cookie set by the
    # /login step above persists across these failures (only success clears
    # it), so the client stays on the same mfa_pending session throughout.
    for _ in range(3):
        r = await csrf_post(
            client,
            "/login/mfa",
            {"code": "000000"},
            bootstrap_url="/login",
            headers=hdr,
            follow_redirects=False,
        )
        assert r.status_code == 400

    # The 4th wrong attempt trips the shared per-source threshold -> 429,
    # raised by login_mfa_post's own is_ip_blocked gate (checked before the
    # mfa_pending cookie is even read).
    r = await csrf_post(
        client,
        "/login/mfa",
        {"code": "000000"},
        bootstrap_url="/login",
        headers=hdr,
        follow_redirects=False,
    )
    assert r.status_code == 429 and r.headers.get("retry-after")
