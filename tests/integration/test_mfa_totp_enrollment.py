from __future__ import annotations

import re

import pyotp
from httpx import AsyncClient
from sqlalchemy import select

from idraa.models.mfa import RecoveryCode, UserTotp


async def test_totp_enroll_confirm_and_recovery_stamps_enrolled(
    authed_admin: tuple[AsyncClient, object], db_session
) -> None:
    from tests.conftest import csrf_post

    client, _org = authed_admin

    # GET returns the QR + the manual key; the unconfirmed secret rides a signed
    # cookie (NO DB write on GET). Extract the secret from the shown manual key.
    r = await client.get("/account/security/totp/enroll")
    assert r.status_code == 200
    assert "<svg" in r.text
    m = re.search(r"Manual key:\s*([A-Z2-7]+)", r.text)
    assert m, "manual key not rendered"
    secret = m.group(1)
    # No UserTotp row exists yet (GET didn't persist).
    assert (await db_session.execute(select(UserTotp))).scalars().first() is None

    # Confirm with a live code (the rf_totp_pending cookie is carried by the client).
    code = pyotp.TOTP(secret).now()
    r2 = await csrf_post(
        client,
        "/account/security/totp/enroll",
        {"code": code},
        bootstrap_url="/account/security",
        follow_redirects=False,
    )
    assert r2.status_code == 303

    await db_session.commit()
    confirmed = (await db_session.execute(select(UserTotp))).scalars().first()
    assert confirmed is not None and confirmed.confirmed_at is not None

    # Generate recovery codes → completes enrollment.
    r3 = await csrf_post(
        client,
        "/account/security/recovery-codes/generate",
        {},
        bootstrap_url="/account/security",
    )
    assert r3.status_code == 200
    found = re.findall(r"\b[0-9a-f]{5}-[0-9a-f]{5}\b", r3.text)
    assert len(found) >= 10, "recovery codes should be shown once"

    await db_session.commit()
    assert (await db_session.execute(select(RecoveryCode))).scalars().first() is not None

    r4 = await client.get("/account/security")
    assert "Enrolled" in r4.text or "enrolled" in r4.text


async def test_totp_confirm_rejects_bad_code(
    authed_admin: tuple[AsyncClient, object], db_session
) -> None:
    from tests.conftest import csrf_post

    client, _ = authed_admin
    await client.get("/account/security/totp/enroll")
    r = await csrf_post(
        client,
        "/account/security/totp/enroll",
        {"code": "000000"},
        bootstrap_url="/account/security",
    )
    assert r.status_code == 400
