"""SessionMiddleware auth-state coverage — the previously untested paths.

middleware/session.py + services/auth.py:load_active_session enforce the
absolute (non-sliding) 14d session TTL and the tampered-cookie / inactive-
user fallthrough-to-anonymous. None of these paths had direct tests; the
authed-client fixtures mint sessions via tests/factories.login_client_as
and never exercise expiry or rejection.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.session import AuthSession
from idraa.services.auth import SESSION_COOKIE, sign_session_id
from tests.factories import create_org, create_user, login_client_as


async def _authed_client_with_session(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[AsyncClient, AuthSession]:
    org = await create_org(db_session)
    user = await create_user(db_session, org)
    cookie = await login_client_as(db_session, user)
    client.cookies.set(SESSION_COOKIE, cookie)
    # Scope to this user's session — deterministic even if a future test
    # seeds additional sessions through this helper (review finding).
    sess = (
        await db_session.execute(
            AuthSession.__table__.select().where(AuthSession.user_id == user.id)
        )
    ).first()
    assert sess is not None
    row = await db_session.get(AuthSession, sess.id)
    assert row is not None
    return client, row


@pytest.mark.asyncio
async def test_valid_session_authenticates(client: AsyncClient, db_session: AsyncSession) -> None:
    """Control case: an unexpired session reaches the dashboard (200)."""
    authed, _sess = await _authed_client_with_session(client, db_session)
    r = await authed.get("/", follow_redirects=False)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_expired_session_is_anonymous(client: AsyncClient, db_session: AsyncSession) -> None:
    """A session past expires_at is rejected by load_active_session — the
    request proceeds anonymous and protected pages bounce to /login.
    The TTL is ABSOLUTE (set at create_session; never slides on use)."""
    authed, sess = await _authed_client_with_session(client, db_session)
    sess.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()  # middleware reads via its own connection

    r = await authed.get("/", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"].startswith("/login")


@pytest.mark.asyncio
async def test_ttl_does_not_slide_on_use(client: AsyncClient, db_session: AsyncSession) -> None:
    """Documented policy (middleware/session.py): requests do NOT extend
    expires_at. Pin it so a future 'helpful' sliding-TTL change is a
    deliberate decision, not an accident."""
    authed, sess = await _authed_client_with_session(client, db_session)
    before = sess.expires_at

    r = await authed.get("/", follow_redirects=False)
    assert r.status_code == 200

    await db_session.refresh(sess)
    assert sess.expires_at == before


@pytest.mark.asyncio
async def test_tampered_cookie_is_anonymous_and_logged(
    client: AsyncClient,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A cookie that fails signature verification falls through to anonymous
    AND emits the SOC-facing WARNING (middleware/session.py observability
    contract). An org/user is seeded so the bounce is the auth redirect
    (/login), not the setup-guard redirect (/setup)."""
    org = await create_org(db_session)
    await create_user(db_session, org)
    await db_session.commit()

    client.cookies.set(SESSION_COOKIE, "forged-garbage-value")
    # caplog scope must match middleware/session.py's module logger
    # (__name__ == "idraa.middleware.session"); if the warning call moves
    # to another module, update the scope here or the assert fails loudly.
    with caplog.at_level("WARNING", logger="idraa.middleware.session"):
        r = await client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"].startswith("/login")
    assert any("invalid signature" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_unknown_session_id_is_anonymous(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A correctly-signed cookie whose session row does not exist (e.g.
    deleted by logout elsewhere) is anonymous — no crash, no 500."""
    org = await create_org(db_session)
    await create_user(db_session, org)
    await db_session.commit()

    client.cookies.set(SESSION_COOKIE, sign_session_id(uuid.uuid4()))
    r = await client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"].startswith("/login")


@pytest.mark.asyncio
async def test_inactive_user_session_is_anonymous(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Deactivating a user invalidates their live sessions at the middleware
    layer (user.is_active gate) even though the session row still exists."""
    authed, sess = await _authed_client_with_session(client, db_session)
    from idraa.models.user import User

    user = await db_session.get(User, sess.user_id)
    assert user is not None
    user.is_active = False
    await db_session.commit()

    r = await authed.get("/", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"].startswith("/login")
