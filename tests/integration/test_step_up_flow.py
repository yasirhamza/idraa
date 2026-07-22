from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pyotp
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models._types import now_utc
from idraa.models.mfa import UserTotp
from idraa.models.session import AuthSession
from idraa.models.user import User
from idraa.services.auth import SESSION_COOKIE, unsign_session_id
from idraa.services.mfa_crypto import encrypt_totp_secret
from tests.conftest import csrf_post

# Fixture note: the root conftest's DB fixture is `db_session` (same db_url as
# the HTTP client fixtures — the authed_admin + db_session pairing is proven by
# tests/integration/test_mfa_passkey_routes.py::test_passkey_delete_removes_row).
# Fixture users are created by tests/factories.py::create_user with default
# password "pw-12345678".


async def _client_session(db_session: AsyncSession, client: AsyncClient) -> AuthSession:
    signed = client.cookies.get(SESSION_COOKIE)
    assert signed is not None
    sid = unsign_session_id(signed)
    assert sid is not None
    sess = await db_session.get(AuthSession, sid)
    assert sess is not None
    return sess


async def _make_stale(db_session: AsyncSession, client: AsyncClient) -> None:
    sess = await _client_session(db_session, client)
    sess.reauthenticated_at = datetime.now(UTC) - timedelta(seconds=999)
    await db_session.commit()


async def _enroll_totp(db_session: AsyncSession, client: AsyncClient) -> str:
    """Attach a confirmed TOTP to the client's user; return the secret."""
    sess = await _client_session(db_session, client)
    secret = pyotp.random_base32()
    db_session.add(
        UserTotp(
            user_id=sess.user_id,
            secret_encrypted=encrypt_totp_secret(secret),
            confirmed_at=now_utc(),
        )
    )
    user = await db_session.get(User, sess.user_id)
    assert user is not None
    user.mfa_enrolled_at = now_utc()
    await db_session.commit()
    return secret


async def test_step_up_page_renders_code_form_for_totp_user(
    db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    await _enroll_totp(db_session, admin_client)
    r = await admin_client.get("/auth/step-up?next=/users", follow_redirects=False)
    assert r.status_code == 200
    assert 'action="/auth/step-up/verify"' in r.text
    assert 'name="code"' in r.text
    assert 'name="password"' not in r.text  # strong-factor users never see password


async def test_step_up_page_renders_password_form_for_factorless_user(
    admin_client: AsyncClient,
) -> None:
    r = await admin_client.get("/auth/step-up", follow_redirects=False)
    assert r.status_code == 200
    assert 'name="password"' in r.text
    assert 'name="code"' not in r.text


async def test_step_up_verify_totp_stamps_session_and_redirects(
    db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    secret = await _enroll_totp(db_session, admin_client)
    await _make_stale(db_session, admin_client)
    r = await csrf_post(
        admin_client,
        "/auth/step-up/verify",
        {"code": pyotp.TOTP(secret).now(), "next": "/users"},
        bootstrap_url="/auth/step-up",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/users"
    sess = await _client_session(db_session, admin_client)
    await db_session.refresh(sess)
    ra = sess.reauthenticated_at
    assert ra is not None
    if ra.tzinfo is None:
        ra = ra.replace(tzinfo=UTC)
    assert datetime.now(UTC) - ra < timedelta(seconds=30)


async def test_step_up_verify_password_for_factorless_user(
    db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    # "pw-12345678" is tests/factories.py::create_user's default password.
    await _make_stale(db_session, admin_client)
    r = await csrf_post(
        admin_client,
        "/auth/step-up/verify",
        {"password": "pw-12345678", "next": "/"},
        bootstrap_url="/auth/step-up",
        follow_redirects=False,
    )
    assert r.status_code == 303


async def test_step_up_verify_password_refused_for_strong_factor_user(
    db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    await _enroll_totp(db_session, admin_client)
    await _make_stale(db_session, admin_client)
    r = await csrf_post(
        admin_client,
        "/auth/step-up/verify",
        {"password": "pw-12345678", "next": "/"},
        bootstrap_url="/auth/step-up",
        follow_redirects=False,
    )
    assert r.status_code == 400  # password never satisfies a strong-factor account


async def test_step_up_wrong_code_400_and_counts_toward_lockout(
    db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    await _enroll_totp(db_session, admin_client)
    await _make_stale(db_session, admin_client)
    r = await csrf_post(
        admin_client,
        "/auth/step-up/verify",
        {"code": "000000", "next": "/"},
        bootstrap_url="/auth/step-up",
        follow_redirects=False,
    )
    assert r.status_code == 400
    sess = await _client_session(db_session, admin_client)
    user = await db_session.get(User, sess.user_id)
    assert user is not None
    await db_session.refresh(user)
    assert user.failed_login_count == 1


async def test_anonymous_step_up_page_bounces_to_login(
    anonymous_client: AsyncClient,
    admin_user: User,
    db_session: AsyncSession,
) -> None:
    # admin_user seeds a user so setup_guard does not 307->/setup; the route
    # then runs require_user -> 401 -> _auth_redirect_handler -> 303 /login.
    # Mirrors tests/integration/test_dashboard.py::
    # test_dashboard_unauthenticated_redirects_to_login. create_user only
    # flushes, so commit explicitly so the client's separate engine can
    # observe the User row.
    await db_session.commit()
    r = await anonymous_client.get("/auth/step-up", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")
