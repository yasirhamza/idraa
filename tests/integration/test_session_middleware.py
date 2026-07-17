"""Session middleware integration — signed cookie -> request.state.user.

Exercises the full request path: the app (via ``client``) sees the same
per-test SQLite file that ``db_session`` writes to, so a commit here is
visible to the in-app session the middleware opens via ``get_session()``.
See tests/conftest.py for the fixture rationale.
"""

# omicron-1 F12: dashboard now requires auth; use /login for anon middleware probes.

from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import IndustryType, OrganizationSize, UserRole
from idraa.models.organization import Organization
from idraa.models.user import User
from idraa.services.auth import (
    SESSION_COOKIE,
    create_session,
    hash_password,
    sign_session_id,
)


async def _seed_org_and_user(db: AsyncSession) -> User:
    org = Organization(
        name="Acme",
        industry_type=IndustryType.INFORMATION,
        organization_size=OrganizationSize.SMALL,
    )
    db.add(org)
    await db.flush()
    user = User(
        organization_id=org.id,
        email="admin@acme.test",
        password_hash=hash_password("pw"),
        full_name="Admin",
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def test_no_cookie_means_no_current_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Seed a user so Task 1.1.5's setup-guard lets GET / through to the
    # dashboard. The point of this test is the anon-nav branch, not the
    # guard — with no user seeded, the guard would 307 before any nav
    # rendering happened.
    await _seed_org_and_user(db_session)
    await db_session.commit()
    r = await client.get("/login")
    assert r.status_code == 200
    assert "Sign in" in r.text
    assert "Sign out" not in r.text


async def test_valid_cookie_loads_user(client: AsyncClient, db_session: AsyncSession) -> None:
    # NB: this test uses BOTH the app's DB (via client) AND a separate db_session —
    # both point at the SAME per-test SQLite file via the db_url fixture.
    user = await _seed_org_and_user(db_session)
    sess = await create_session(db_session, user.id, ip="127.0.0.1")
    await db_session.commit()
    signed = sign_session_id(sess.id)

    client.cookies.set(SESSION_COOKIE, signed)
    r = await client.get("/login")
    assert r.status_code == 200
    assert "admin@acme.test" in r.text


async def test_tampered_cookie_falls_through_to_anon(
    client: AsyncClient,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A forged/tampered cookie must be rejected and the request continues as anon.

    Observability requirement from 1.1.3 security review: this is the signal
    the SOC wants to see — assert the warning was emitted, and assert the
    request still succeeded with the anon nav (no "Sign out" button).

    Seeds a user so Task 1.1.5's setup-guard does not 307 the GET / before
    SessionMiddleware gets a chance to inspect the cookie.
    """
    await _seed_org_and_user(db_session)
    await db_session.commit()
    client.cookies.set(SESSION_COOKIE, "definitely.not-a-valid-signed-token")
    with caplog.at_level(logging.WARNING, logger="idraa.middleware.session"):
        r = await client.get("/login")
    assert r.status_code == 200
    assert "Sign in" in r.text
    assert "Sign out" not in r.text
    assert any("rejected session cookie" in rec.getMessage() for rec in caplog.records), (
        f"expected rejected-cookie log; got {[r.getMessage() for r in caplog.records]}"
    )
