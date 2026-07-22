"""Login state machine: password -> mfa_pending -> TOTP/recovery, + minimal
login throttle (idraa#81 slice, plan-gate B1).

Uses the shared ``csrf_post`` helper (tests/conftest.py) so every POST
carries a valid ``_csrf`` field.
"""

from __future__ import annotations

import pyotp
from httpx import AsyncClient
from sqlalchemy import select

from idraa.models._types import now_utc
from idraa.models.mfa import RecoveryCode, UserTotp
from idraa.models.user import User
from idraa.services.mfa_crypto import hash_recovery_code


async def _seed_setup(client: AsyncClient) -> None:
    from tests.conftest import csrf_post

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
    client.cookies.delete("idraa_session")


async def test_password_login_with_totp_second_factor(client: AsyncClient, db_session) -> None:
    from tests.conftest import csrf_post

    await _seed_setup(client)
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

    # Step 1: password -> mfa challenge (NOT a session yet).
    r = await csrf_post(
        client, "/login", {"email": "a@b.c", "password": "pw-12345678"}, follow_redirects=False
    )
    assert r.status_code == 200
    assert "idraa_session" not in r.cookies
    assert "code" in r.text.lower()

    # Step 2: TOTP code -> session.
    code = pyotp.TOTP(secret).now()
    r2 = await csrf_post(
        client, "/login/mfa", {"code": code}, bootstrap_url="/login", follow_redirects=False
    )
    assert r2.status_code == 303
    assert "idraa_session" in r2.cookies or any(
        "idraa_session" in h for h in r2.headers.get_list("set-cookie")
    )


async def test_recovery_code_second_factor_is_single_use(client: AsyncClient, db_session) -> None:
    from tests.conftest import csrf_post

    await _seed_setup(client)
    user = (await db_session.execute(select(User))).scalars().first()
    db_session.add(RecoveryCode(user_id=user.id, code_hash=hash_recovery_code("aaaaa-bbbbb")))
    from idraa.services.mfa_crypto import encrypt_totp_secret

    db_session.add(
        UserTotp(
            user_id=user.id,
            secret_encrypted=encrypt_totp_secret(pyotp.random_base32()),
            confirmed_at=now_utc(),
        )
    )
    user.mfa_enrolled_at = now_utc()
    await db_session.commit()

    await csrf_post(
        client, "/login", {"email": "a@b.c", "password": "pw-12345678"}, follow_redirects=False
    )
    r = await csrf_post(
        client,
        "/login/mfa",
        {"code": "aaaaa-bbbbb"},
        bootstrap_url="/login",
        follow_redirects=False,
    )
    assert r.status_code == 303
    await db_session.commit()
    used = (await db_session.execute(select(RecoveryCode))).scalars().first()
    assert used.used_at is not None


async def test_unenrolled_user_password_login_gets_session(client: AsyncClient, db_session) -> None:
    # Migration path: no strong factor yet -> straight session (interstitial traps later).
    from tests.conftest import csrf_post

    await _seed_setup(client)
    r = await csrf_post(
        client, "/login", {"email": "a@b.c", "password": "pw-12345678"}, follow_redirects=False
    )
    assert r.status_code == 303
    set_cookie = "".join(r.headers.get_list("set-cookie"))
    assert "idraa_session" in r.cookies or "idraa_session" in set_cookie


async def test_repeated_bad_password_locks_account(client: AsyncClient, db_session) -> None:
    # B1: minimal throttle. AUTH_MAX_FAILED_LOGINS defaults to 5.
    from tests.conftest import csrf_post

    await _seed_setup(client)
    for _ in range(5):
        await csrf_post(
            client,
            "/login",
            {"email": "a@b.c", "password": "wrong-pass"},
            follow_redirects=False,
        )
    await db_session.commit()
    user = (await db_session.execute(select(User))).scalars().first()
    assert user.locked_until is not None
    # Even the CORRECT password is denied while locked.
    r = await csrf_post(
        client, "/login", {"email": "a@b.c", "password": "pw-12345678"}, follow_redirects=False
    )
    assert r.status_code == 400


async def _seed_totp_user(client: AsyncClient, db_session) -> None:
    import pyotp

    from idraa.services.mfa_crypto import encrypt_totp_secret

    await _seed_setup(client)
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


async def test_repeated_bad_totp_locks_account(client: AsyncClient, db_session) -> None:
    # B1: failed second-factor attempts count toward lockout, within one window.
    from tests.conftest import csrf_post

    await _seed_totp_user(client, db_session)
    await csrf_post(
        client, "/login", {"email": "a@b.c", "password": "pw-12345678"}, follow_redirects=False
    )  # ONE password login -> mfa_pending
    for _ in range(5):
        await csrf_post(
            client,
            "/login/mfa",
            {"code": "000000"},
            bootstrap_url="/login",
            follow_redirects=False,
        )
    await db_session.commit()
    locked = (await db_session.execute(select(User))).scalars().first()
    assert locked.locked_until is not None


async def test_relogin_does_not_reset_mfa_throttle(client: AsyncClient, db_session) -> None:
    # Regression (plan-gate round 2): a correct password must NOT reset the
    # 2FA failure counter while a second factor is still pending — otherwise
    # an attacker who has the password bypasses the TOTP rate limit by
    # re-POSTing /login before each guess.
    from tests.conftest import csrf_post

    await _seed_totp_user(client, db_session)
    for _ in range(5):
        await csrf_post(
            client,
            "/login",
            {"email": "a@b.c", "password": "pw-12345678"},
            follow_redirects=False,
        )  # correct password each time
        await csrf_post(
            client,
            "/login/mfa",
            {"code": "000000"},
            bootstrap_url="/login",
            follow_redirects=False,
        )
    await db_session.commit()
    locked = (await db_session.execute(select(User))).scalars().first()
    assert locked.locked_until is not None  # re-login did NOT wipe the counter
